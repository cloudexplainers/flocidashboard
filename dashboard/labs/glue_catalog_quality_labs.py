"""Catalog, data quality, and schema evolution: daily order files land in S3, a partitioned Glue table describes them,
missing partitions are found and registered, a crawler is scheduled, DQDL rules are evaluated per partition with Athena,
failing rows are quarantined and the rest written to a curated table, a quality score metric raises an alarm, and the
schema evolves under the Glue Schema Registry and table versions.

Floci 2.4.0 stores the Data Catalog (tables, partitions, partition indexes, table versions), runs Athena SQL in DuckDB
over the catalog, enforces Schema Registry compatibility, and evaluates CloudWatch alarms. What Floci does not do: its
crawlers finish without creating or updating tables, Glue Data Quality rulesets are stored but never evaluated, and
Athena reads every folder under a table's location whether or not the partition is registered. The lab infers the
schema itself, runs each rule as SQL, and checks the partitions against the folders.
"""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'glue'
LAB_KEY = 'catalog-quality'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
ACCOUNT = '000000000000'

BUCKET = 'lab-catalog-lake'
RAW_PREFIX = 'raw/orders/'
CURATED_PREFIX = 'curated/orders/'
QUARANTINE_PREFIX = 'quarantine/orders/'
RESULTS_PREFIX = 'athena-results/'
DQ_RESULTS_PREFIX = 'dq-results/raw_orders/'
DATABASE = 'lab_sales'
RAW_TABLE = 'raw_orders'
CLEAN_TABLE = 'orders_clean'
CRAWLER = 'lab-raw-orders-crawler'
CRAWLER_ROLE = 'lab-glue-crawler'
RULESET = 'lab-raw-orders-rules'
REGISTRY = 'lab-sales-registry'
SCHEMA = 'order-events'
NAMESPACE = 'LabDataQuality'
METRIC = 'DataQualityScore'
ALARM = 'lab-raw-orders-quality'
TOPIC = 'lab-data-quality-alerts'
THRESHOLD = 0.9
PARTITION_INDEX = 'by_dt'
DAYS = ['2026-11-01', '2026-11-02', '2026-11-03']
NEW_DAY = '2026-11-04'
COUNTRIES = ['DE', 'US', 'FR']

COLUMNS = [('order_id', 'int'), ('customer', 'string'), ('amount', 'double'), ('country', 'string')]
NEW_COLUMN = ('channel', 'string')

DQDL = (
    'Rules = [\n'
    '    RowCount > 0,\n'
    '    IsComplete "customer",\n'
    '    IsUnique "order_id",\n'
    '    ColumnValues "amount" between 0 and 10000,\n'
    '    ColumnValues "country" in ["DE", "US", "FR"]\n'
    ']'
)
RULES = ['RowCount > 0', 'IsComplete "customer"', 'IsUnique "order_id"', 'ColumnValues "amount" between 0 and 10000',
         'ColumnValues "country" in ["DE", "US", "FR"]']

ATHENA_TIMEOUT = 90
ALARM_WAIT_SECONDS = 150
FLOCI = 'How Floci emulates this: '


# ── Data ─────────────────────────────────────────────────────────────────────

def day_rows(day: str) -> list[dict[str, Any]]:
    """Six clean orders a day; the third day adds four broken ones."""
    base = (DAYS.index(day) if day in DAYS else len(DAYS)) * 100
    names = ['ana', 'ben', 'chen', 'dara', 'eli', 'fay']
    rows = [{'order_id': base + index + 1, 'customer': names[index], 'amount': round(12.5 * (index + 1), 2),
             'country': COUNTRIES[index % 3]} for index in range(6)]
    if day == DAYS[2]:
        rows += [
            {'order_id': base + 7, 'customer': '', 'amount': 30.0, 'country': 'US'},
            {'order_id': base + 2, 'customer': 'ben', 'amount': 25.0, 'country': 'US'},
            {'order_id': base + 8, 'customer': 'gus', 'amount': 25000.0, 'country': 'DE'},
            {'order_id': base + 9, 'customer': 'hal', 'amount': 40.0, 'country': 'XX'},
        ]
    return rows


def to_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator='\n', extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, '') for column in columns})
    return out.getvalue()


def raw_key(day: str) -> str:
    return f'{RAW_PREFIX}dt={day}/orders-{day}.csv'


def infer_schema(body: str) -> list[tuple[str, str]]:
    """What a crawler's CSV classifier does: header names, and int, double, or string from the values."""
    reader = csv.DictReader(io.StringIO(body))
    rows = list(reader)
    schema = []
    for name in reader.fieldnames or []:
        values = [row[name] for row in rows if row.get(name)]

        def is_type(kind, values=values) -> bool:
            try:
                for value in values:
                    kind(value)
            except ValueError:
                return False
            return bool(values)

        schema.append((name, 'int' if is_type(int) else 'double' if is_type(float) else 'string'))
    return schema


def partition_values(keys: list[str], prefix: str = RAW_PREFIX) -> list[str]:
    """The dt=... folders under a prefix, as MSCK REPAIR TABLE would find them."""
    found = set()
    for key in keys:
        rest = key[len(prefix):] if key.startswith(prefix) else ''
        folder = rest.split('/', 1)[0]
        if folder.startswith('dt=') and '/' in rest:
            found.add(folder[3:])
    return sorted(found)


QUALITY_SQL = (
    'SELECT dt, COUNT(*) AS row_count, '
    "SUM(CASE WHEN customer IS NULL OR customer = '' THEN 1 ELSE 0 END) AS missing_customer, "
    'COUNT(*) - COUNT(DISTINCT order_id) AS duplicate_ids, '
    'SUM(CASE WHEN amount IS NULL OR amount < 0 OR amount > 10000 THEN 1 ELSE 0 END) AS bad_amount, '
    "SUM(CASE WHEN country IS NULL OR country NOT IN ('DE', 'US', 'FR') THEN 1 ELSE 0 END) AS bad_country "
    'FROM {table} GROUP BY dt ORDER BY dt'
)


def score_partition(counts: dict[str, int]) -> dict[str, Any]:
    outcomes = {
        RULES[0]: counts['row_count'] > 0,
        RULES[1]: counts['missing_customer'] == 0,
        RULES[2]: counts['duplicate_ids'] == 0,
        RULES[3]: counts['bad_amount'] == 0,
        RULES[4]: counts['bad_country'] == 0,
    }
    passed = sum(1 for ok in outcomes.values() if ok)
    return {'rules': {rule: 'PASS' if ok else 'FAIL' for rule, ok in outcomes.items()},
            'score': round(passed / len(outcomes), 2)}


def split_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Row-level rules: keep the first copy of an order, quarantine the rest with a reason."""
    good, bad, seen = [], [], set()
    for row in rows:
        reasons = []
        if not row.get('customer'):
            reasons.append('IsComplete "customer"')
        amount = row.get('amount')
        if amount is None or not 0 <= float(amount) <= 10000:
            reasons.append('ColumnValues "amount" between 0 and 10000')
        if row.get('country') not in COUNTRIES:
            reasons.append('ColumnValues "country" in ["DE", "US", "FR"]')
        if row.get('order_id') in seen:
            reasons.append('IsUnique "order_id"')
        if reasons:
            bad.append({**row, 'dq_reasons': reasons})
        else:
            good.append(row)
            seen.add(row.get('order_id'))
    return good, bad


def json_lines(rows: list[dict[str, Any]]) -> str:
    return ''.join(json.dumps(row) + '\n' for row in rows)


def avro(fields: list[dict[str, Any]]) -> str:
    return json.dumps({'type': 'record', 'name': 'OrderEvent', 'namespace': 'lab.sales', 'fields': fields})


AVRO_V1 = avro([{'name': 'order_id', 'type': 'int'}, {'name': 'customer', 'type': 'string'},
                {'name': 'amount', 'type': 'double'}, {'name': 'country', 'type': 'string'}])
AVRO_V2 = avro([{'name': 'order_id', 'type': 'int'}, {'name': 'customer', 'type': 'string'},
                {'name': 'amount', 'type': 'double'}, {'name': 'country', 'type': 'string'},
                {'name': 'channel', 'type': 'string', 'default': 'web'}])
AVRO_BREAKING = avro([{'name': 'order_id', 'type': 'string'}, {'name': 'customer', 'type': 'string'},
                      {'name': 'amount', 'type': 'double'}, {'name': 'country', 'type': 'string'},
                      {'name': 'channel', 'type': 'string', 'default': 'web'}])


# ── Commands shown in the console ───────────────────────────────────────────

LAND_COMMAND = (
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    + ''.join(f'aws s3 cp orders-{day}.csv s3://{BUCKET}/{raw_key(day)}\n' for day in DAYS)
    + f'aws glue create-database --database-input Name={DATABASE},Description="Sales data lake"'
)
TABLE_COMMAND = (
    f'aws glue create-table --database-name {DATABASE} --table-input file://raw_orders.json   '
    '# CSV, header skipped, partitioned by dt\n'
    f'aws glue create-partition --database-name {DATABASE} --table-name {RAW_TABLE} '
    f'--partition-input file://dt={DAYS[0]}.json\n'
    f'aws athena start-query-execution --query-string "SELECT dt, COUNT(*) FROM {DATABASE}.{RAW_TABLE} GROUP BY dt" '
    f'--result-configuration OutputLocation=s3://{BUCKET}/{RESULTS_PREFIX}'
)
SYNC_COMMAND = (
    f'aws s3api list-objects-v2 --bucket {BUCKET} --prefix {RAW_PREFIX}\n'
    f'aws glue get-partitions --database-name {DATABASE} --table-name {RAW_TABLE}\n'
    f'aws glue batch-create-partition --database-name {DATABASE} --table-name {RAW_TABLE} '
    '--partition-input-list file://missing-partitions.json   # what MSCK REPAIR TABLE does\n'
    f'aws glue create-partition-index --database-name {DATABASE} --table-name {RAW_TABLE} '
    f'--partition-index Keys=dt,IndexName={PARTITION_INDEX}\n'
    f'aws glue get-partitions --database-name {DATABASE} --table-name {RAW_TABLE} --expression "dt >= \'{DAYS[1]}\'"'
)
CRAWLER_COMMAND = (
    f'aws iam create-role --role-name {CRAWLER_ROLE} --assume-role-policy-document file://glue-trust.json\n'
    f'aws glue create-crawler --name {CRAWLER} --role {CRAWLER_ROLE} --database-name {DATABASE} '
    f'--targets \'{{"S3Targets":[{{"Path":"s3://{BUCKET}/{RAW_PREFIX}"}}]}}\' --schedule "cron(0 2 * * ? *)" '
    '--schema-change-policy UpdateBehavior=LOG,DeleteBehavior=LOG '
    '--recrawl-policy RecrawlBehavior=CRAWL_NEW_FOLDERS_ONLY\n'
    f'aws glue start-crawler --name {CRAWLER}\n'
    f'aws glue get-crawler --name {CRAWLER}'
)
QUALITY_COMMAND = (
    f'aws glue create-data-quality-ruleset --name {RULESET} --ruleset file://rules.dqdl '
    f'--target-table TableName={RAW_TABLE},DatabaseName={DATABASE}\n'
    f'aws glue start-data-quality-ruleset-evaluation-run --data-source \'{{"GlueTable":{{"DatabaseName":"{DATABASE}",'
    f'"TableName":"{RAW_TABLE}"}}}}\' --role {CRAWLER_ROLE} --ruleset-names {RULESET}\n'
    'aws athena start-query-execution --query-string "SELECT dt, COUNT(*), SUM(CASE WHEN customer IS NULL ...) '
    f'FROM {DATABASE}.{RAW_TABLE} GROUP BY dt" --result-configuration OutputLocation=s3://{BUCKET}/{RESULTS_PREFIX}\n'
    f'aws s3 cp dq-results.json s3://{BUCKET}/{DQ_RESULTS_PREFIX}'
)
QUARANTINE_COMMAND = (
    f'aws athena start-query-execution --query-string "SELECT * FROM {DATABASE}.{RAW_TABLE}" '
    f'--result-configuration OutputLocation=s3://{BUCKET}/{RESULTS_PREFIX}\n'
    f'aws s3 cp clean.json s3://{BUCKET}/{CURATED_PREFIX}dt=<day>/part-000.json\n'
    f'aws s3 cp rejected.json s3://{BUCKET}/{QUARANTINE_PREFIX}dt={DAYS[2]}/rejected.json\n'
    f'aws glue create-table --database-name {DATABASE} --table-input file://orders_clean.json   # JSON, by dt'
)
ALARM_COMMAND = (
    f'aws sns create-topic --name {TOPIC}\n'
    f'aws cloudwatch put-metric-data --namespace {NAMESPACE} --metric-name {METRIC} '
    f'--dimensions Database={DATABASE},Table={RAW_TABLE} --value <score per partition>\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {ALARM} --namespace {NAMESPACE} --metric-name {METRIC} '
    f'--dimensions Name=Database,Value={DATABASE} Name=Table,Value={RAW_TABLE} --statistic Minimum --period 60 '
    f'--evaluation-periods 1 --threshold {THRESHOLD} --comparison-operator LessThanThreshold '
    f'--treat-missing-data notBreaching --alarm-actions arn:aws:sns:us-east-1:{ACCOUNT}:{TOPIC}'
)
EVOLVE_COMMAND = (
    f'aws glue create-registry --registry-name {REGISTRY}\n'
    f'aws glue create-schema --registry-id RegistryName={REGISTRY} --schema-name {SCHEMA} --data-format AVRO '
    '--compatibility BACKWARD --schema-definition file://order-event-v1.avsc\n'
    f'aws glue register-schema-version --schema-id RegistryName={REGISTRY},SchemaName={SCHEMA} '
    '--schema-definition file://order-event-v2.avsc   # adds channel with a default\n'
    f'aws glue register-schema-version --schema-id RegistryName={REGISTRY},SchemaName={SCHEMA} '
    '--schema-definition file://order-event-breaking.avsc   # refused\n'
    f'aws glue update-table --database-name {DATABASE} --table-input file://raw_orders-v2.json   # adds channel\n'
    f'aws glue get-table-versions --database-name {DATABASE} --table-name {RAW_TABLE}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Catalog a data lake, check data quality, and evolve the schema with Glue and Athena',
    'description': (
        'Land daily order files in S3, describe them with a partitioned Glue table, find and register missing '
        'partitions, schedule a crawler, evaluate data quality rules per partition with Athena, quarantine the '
        'failing rows and publish the rest to a curated table, alarm on the quality score, and evolve the schema '
        'with the Glue Schema Registry and table versions.'
    ),
    'steps': [
        {
            'key': 'land-raw-data',
            'title': f'Land three days of orders under {RAW_PREFIX}',
            'command': LAND_COMMAND,
            'explanation': (
                f'Each day\'s orders arrive as a CSV file in its own folder, s3://{BUCKET}/{RAW_PREFIX}dt=<day>/. '
                'Folders named key=value are Hive-style partitions: a query that filters on dt reads only the '
                'matching folders. Partition by the column queries filter on most, with few enough values '
                '(a day, not a second) to avoid millions of tiny partitions. '
                f'The Glue database {DATABASE} will hold the tables that describe these files. '
                + FLOCI + 'the bucket, files and database are real.'
            ),
        },
        {
            'key': 'create-table',
            'title': f'Describe the files with the partitioned {RAW_TABLE} table',
            'command': TABLE_COMMAND,
            'explanation': (
                f'{RAW_TABLE} is a Data Catalog table: its columns, the CSV SerDe (LazySimpleSerDe with a comma '
                'delimiter and the header skipped), the S3 location, and the partition key dt. Athena, Redshift '
                'Spectrum, EMR and Glue jobs all read the same definition. Only the first day is registered as a '
                'partition, as if yesterday\'s pipeline had done it. In AWS, Athena sees only registered partitions, '
                'so the other two days are invisible until they are added. '
                + FLOCI + 'the table and partition are stored, and Athena queries them in DuckDB. Floci reads every '
                'dt= folder under the location whether or not it is registered, so the lab checks the catalog '
                'itself in the next step.'
            ),
        },
        {
            'key': 'sync-partitions',
            'title': 'Find the missing partitions, register them, and index dt',
            'command': SYNC_COMMAND,
            'explanation': (
                'The lab lists the dt= folders in S3, compares them with GetPartitions, and registers the missing '
                'days with BatchCreatePartition, which is what MSCK REPAIR TABLE or a crawler does. Three other '
                'ways keep partitions current: the job that writes a folder also adds its partition, partition '
                'projection lets Athena compute partitions from a pattern with no catalog calls, and a crawler '
                'runs on a schedule. A partition index on dt makes GetPartitions with an expression (dt >= '
                f'\'{DAYS[1]}\') fast on tables with many thousands of partitions. '
                + FLOCI + 'the partitions, the expression filter and the index are real catalog operations.'
            ),
        },
        {
            'key': 'schedule-crawler',
            'title': f'Schedule {CRAWLER} to catalog new folders nightly',
            'command': CRAWLER_COMMAND,
            'explanation': (
                f'A crawler connects to s3://{BUCKET}/{RAW_PREFIX} with its IAM role, samples the files, infers the '
                'columns with classifiers (CSV, JSON, Parquet, or custom grok patterns), and creates or updates '
                'tables and partitions. CRAWL_NEW_FOLDERS_ONLY reads only folders added since the last run, which '
                'keeps nightly crawls short; the LOG schema change policy reports changes instead of rewriting the '
                'table, so a bad file cannot change the schema queries depend on. The schedule is a cron expression '
                'in UTC. '
                + FLOCI + 'the crawler runs and finishes without reading the files or touching the catalog. The lab '
                'infers the schema from the newest file the way a CSV classifier does and compares it with the '
                'table.'
            ),
        },
        {
            'key': 'evaluate-quality',
            'title': 'Evaluate five data quality rules on every partition',
            'command': QUALITY_COMMAND,
            'explanation': (
                f'{RULESET} is a Glue Data Quality ruleset in DQDL: RowCount, IsComplete, IsUnique, and two '
                'ColumnValues rules. Glue evaluates it on a catalog table or inside an ETL job, scores the share of '
                'rules that pass, and can stop the job or route failing rows. Here each rule becomes SQL in one '
                f'Athena query grouped by dt, and the results are saved in s3://{BUCKET}/{DQ_RESULTS_PREFIX}. The '
                f'first two days pass; {DAYS[2]} has an order with no customer, a duplicate order ID, an amount of '
                '25,000, and an unknown country. '
                + FLOCI + 'the ruleset is stored, but StartDataQualityRulesetEvaluationRun is not supported, so the '
                'lab evaluates the rules with Athena.'
            ),
        },
        {
            'key': 'quarantine-bad-rows',
            'title': f'Quarantine the failing rows and publish {CLEAN_TABLE}',
            'command': QUARANTINE_COMMAND,
            'explanation': (
                'Applying the row-level rules splits the data: rows that pass go to '
                f'{CURATED_PREFIX} as JSON, partitioned by day; rows that fail go to {QUARANTINE_PREFIX} with the '
                'rules they broke, for someone to fix or replay. The first copy of a duplicated order is kept. '
                f'{CLEAN_TABLE} describes the curated data, and the same quality query on it passes every rule. '
                'Downstream jobs read only the curated table. '
                + FLOCI + 'the files, table and Athena queries are real; the lab applies the rules itself because '
                'Glue Data Quality does not run.'
            ),
        },
        {
            'key': 'alarm-on-quality',
            'title': f'Alarm when the quality score drops below {THRESHOLD}',
            'command': ALARM_COMMAND,
            'explanation': (
                f'Each partition\'s score is published as {NAMESPACE}/{METRIC} (Glue Data Quality publishes the same '
                f'kind of metric). {ALARM} watches the minimum over one minute and goes to ALARM below {THRESHOLD}, '
                f'notifying {TOPIC}. A pipeline can also stop when a score is too low, before bad data reaches the '
                'curated table. EventBridge receives Glue Data Quality results too, for routing without an alarm. '
                + FLOCI + 'the metric data, alarm evaluation and SNS action are real; the alarm changes state within '
                'about a minute.'
            ),
        },
        {
            'key': 'evolve-schema',
            'title': 'Add a channel field under the Schema Registry and a new table version',
            'command': EVOLVE_COMMAND,
            'explanation': (
                f'The order events are registered as Avro in {REGISTRY} with BACKWARD compatibility: a new version '
                'must let consumers read data written with the previous one. Adding channel with a default passes; '
                'changing order_id from int to string is refused, so a producer cannot break consumers. The '
                f'catalog keeps a version of {RAW_TABLE} for every UpdateTable: version 2 adds channel at the end, '
                f'and the {NEW_DAY} file has it. In AWS, older CSV files simply return NULL for a column added at the '
                'end; renaming or reordering CSV columns breaks queries, which is one reason to prefer Parquet, Avro '
                'or Iceberg, which track columns by name or ID. '
                + FLOCI + 'the registry checks compatibility for real and the table versions are stored. Floci\'s '
                'Athena fails when an older file lacks a column, so the lab rewrites the older days with an empty '
                'channel (a backfill) and then queries all four days.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _marker(step: str, value: Any) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'step': step,
        'command': command,
        'exit_code': 0,
        'stdout': json.dumps(clean, indent=2, default=str),
        'stderr': '',
        'json': clean,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verified,
        'verification': {'status': 'passed' if verified else 'failed', 'message': message},
    }


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Floci lookups ────────────────────────────────────────────────────────────

def _bucket_exists() -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=BUCKET) is not None, False)


def _keys(prefix: str) -> list[str]:
    pages = _safe_call(lambda: list(_client('s3').get_paginator('list_objects_v2').paginate(
        Bucket=BUCKET, Prefix=prefix)), [])
    return [item['Key'] for page in pages for item in page.get('Contents', [])]


def _object(key: str) -> str | None:
    return _safe_call(lambda: _client('s3').get_object(Bucket=BUCKET, Key=key)['Body'].read().decode())


def _database() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('glue').get_database(Name=DATABASE)['Database'])


def _table(name: str) -> dict[str, Any] | None:
    return _safe_call(lambda: _client('glue').get_table(DatabaseName=DATABASE, Name=name)['Table'])


def _partitions(expression: str | None = None) -> list[str]:
    params: dict[str, Any] = {'DatabaseName': DATABASE, 'TableName': RAW_TABLE}
    if expression:
        params['Expression'] = expression
    items = _safe_call(lambda: _client('glue').get_partitions(**params)['Partitions'], [])
    return sorted(item['Values'][0] for item in items)


def _table_versions() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('glue').get_table_versions(DatabaseName=DATABASE, TableName=RAW_TABLE)
                      ['TableVersions'], [])


def _crawler() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('glue').get_crawler(Name=CRAWLER)['Crawler'])


def _ruleset() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('glue').get_data_quality_ruleset(Name=RULESET))


def _schema_versions() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('glue').list_schema_versions(
        SchemaId={'RegistryName': REGISTRY, 'SchemaName': SCHEMA})['Schemas'], [])


def _alarm() -> dict[str, Any] | None:
    alarms = _safe_call(lambda: _client('cloudwatch').describe_alarms(AlarmNames=[ALARM])['MetricAlarms'], [])
    return alarms[0] if alarms else None


def athena(sql: str) -> dict[str, Any]:
    """Run a query and wait. Returns State, Reason, and Rows as dicts keyed by column name."""
    client = _client('athena')
    query_id = client.start_query_execution(
        QueryString=sql, QueryExecutionContext={'Database': DATABASE},
        ResultConfiguration={'OutputLocation': f's3://{BUCKET}/{RESULTS_PREFIX}'})['QueryExecutionId']
    status: dict[str, Any] = {}

    def done() -> bool:
        status.update(client.get_query_execution(QueryExecutionId=query_id)['QueryExecution']['Status'])
        return status.get('State') in ('SUCCEEDED', 'FAILED', 'CANCELLED')

    if not _wait(done, ATHENA_TIMEOUT, delay=0.5):
        return {'QueryExecutionId': query_id, 'State': 'TIMED_OUT', 'Rows': []}
    outcome = {'QueryExecutionId': query_id, 'State': status.get('State'), 'Reason': status.get('StateChangeReason'),
               'Rows': []}
    if status.get('State') == 'SUCCEEDED':
        rows = client.get_query_results(QueryExecutionId=query_id)['ResultSet']['Rows']
        header = [cell.get('VarCharValue') for cell in rows[0]['Data']] if rows else []
        outcome['Rows'] = [dict(zip(header, [cell.get('VarCharValue') for cell in row['Data']])) for row in rows[1:]]
    return outcome


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _typed(row: dict[str, Any]) -> dict[str, Any]:
    order_id = _number(row.get('order_id'))
    return {'order_id': int(order_id) if order_id is not None else None, 'customer': row.get('customer') or '',
            'amount': _number(row.get('amount')), 'country': row.get('country') or ''}


def _quality(table: str) -> dict[str, dict[str, Any]]:
    outcome = athena(QUALITY_SQL.format(table=table))
    results = {}
    for row in outcome.get('Rows', []):
        counts = {key: int(float(row.get(key) or 0)) for key in
                  ('row_count', 'missing_customer', 'duplicate_ids', 'bad_amount', 'bad_country')}
        results[row['dt']] = {'counts': counts, **score_partition(counts)}
    return results


# ── Table definitions ────────────────────────────────────────────────────────

def _csv_descriptor(location: str, columns: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        'Columns': [{'Name': name, 'Type': kind} for name, kind in columns],
        'Location': location,
        'InputFormat': 'org.apache.hadoop.mapred.TextInputFormat',
        'OutputFormat': 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
        'SerdeInfo': {'SerializationLibrary': 'org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe',
                      'Parameters': {'field.delim': ','}},
    }


def _raw_table_input(columns: list[tuple[str, str]]) -> dict[str, Any]:
    return {'Name': RAW_TABLE, 'TableType': 'EXTERNAL_TABLE',
            'Parameters': {'classification': 'csv', 'skip.header.line.count': '1'},
            'PartitionKeys': [{'Name': 'dt', 'Type': 'string'}],
            'StorageDescriptor': _csv_descriptor(f's3://{BUCKET}/{RAW_PREFIX}', columns)}


def _partition_input(day: str) -> dict[str, Any]:
    return {'Values': [day], 'StorageDescriptor': _csv_descriptor(f's3://{BUCKET}/{RAW_PREFIX}dt={day}/', COLUMNS)}


def _clean_table_input() -> dict[str, Any]:
    return {'Name': CLEAN_TABLE, 'TableType': 'EXTERNAL_TABLE', 'Parameters': {'classification': 'json'},
            'PartitionKeys': [{'Name': 'dt', 'Type': 'string'}],
            'StorageDescriptor': {
                'Columns': [{'Name': name, 'Type': kind} for name, kind in COLUMNS],
                'Location': f's3://{BUCKET}/{CURATED_PREFIX}',
                'InputFormat': 'org.apache.hadoop.mapred.TextInputFormat',
                'OutputFormat': 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
                'SerdeInfo': {'SerializationLibrary': 'org.openx.data.jsonserde.JsonSerDe'}}}


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_land() -> tuple[bool, str]:
    if not _bucket_exists():
        return False, f'Create {BUCKET}.'
    missing = [day for day in DAYS if raw_key(day) not in _keys(RAW_PREFIX)]
    if missing:
        return False, f'Land the files for {", ".join(missing)}.'
    if not _database():
        return False, f'Create the {DATABASE} database.'
    return True, f'Three daily files are under s3://{BUCKET}/{RAW_PREFIX}, and {DATABASE} exists.'


def _check_table() -> tuple[bool, str]:
    table = _table(RAW_TABLE)
    if not table:
        return False, f'Create {RAW_TABLE}.'
    if [key['Name'] for key in table.get('PartitionKeys', [])] != ['dt']:
        return False, f'{RAW_TABLE} should be partitioned by dt.'
    if DAYS[0] not in _partitions():
        return False, f'Register the {DAYS[0]} partition.'
    return True, f'{RAW_TABLE} describes the CSV files, partitioned by dt.'


def _check_sync() -> tuple[bool, str]:
    folders = partition_values(_keys(RAW_PREFIX))
    registered = _partitions()
    missing = sorted(set(folders) - set(registered))
    if missing:
        return False, f'Register the missing partitions: {", ".join(missing)}.'
    indexes = _safe_call(lambda: _client('glue').get_partition_indexes(
        DatabaseName=DATABASE, TableName=RAW_TABLE)['PartitionIndexDescriptorList'], [])
    if not any(item.get('IndexName') == PARTITION_INDEX for item in indexes):
        return False, f'Create the {PARTITION_INDEX} partition index.'
    return True, f'Every folder is a partition ({", ".join(registered)}), and dt is indexed.'


def _check_crawler() -> tuple[bool, str]:
    crawler = _crawler()
    if not crawler:
        return False, f'Create {CRAWLER}.'
    if (crawler.get('Schedule') or {}).get('ScheduleExpression') != 'cron(0 2 * * ? *)':
        return False, f'Schedule {CRAWLER} nightly.'
    if (crawler.get('RecrawlPolicy') or {}).get('RecrawlBehavior') != 'CRAWL_NEW_FOLDERS_ONLY':
        return False, f'{CRAWLER} should crawl only new folders.'
    if not (_marked('schedule-crawler') or {}).get('schemaMatches'):
        return False, 'Compare the inferred schema with the table.'
    return True, f'{CRAWLER} runs nightly on new folders, and the inferred schema matches {RAW_TABLE}.'


def _check_quality() -> tuple[bool, str]:
    if not _ruleset():
        return False, f'Store the {RULESET} ruleset.'
    body = _object(f'{DQ_RESULTS_PREFIX}results.json')
    if not body:
        return False, 'Save the quality results to S3.'
    results = json.loads(body)
    scores = {day: item['score'] for day, item in results.items()}
    if scores.get(DAYS[0]) != 1.0 or scores.get(DAYS[2], 1.0) >= 1.0:
        return False, f'Unexpected quality scores: {scores}.'
    return True, 'Scores by partition: ' + ', '.join(f'{day} {score}' for day, score in sorted(scores.items())) + '.'


def _check_quarantine() -> tuple[bool, str]:
    rejected = _object(f'{QUARANTINE_PREFIX}dt={DAYS[2]}/rejected.json')
    if not rejected:
        return False, f'Quarantine the failing rows of {DAYS[2]}.'
    if not _table(CLEAN_TABLE):
        return False, f'Create {CLEAN_TABLE}.'
    if not (_marked('quarantine-bad-rows') or {}).get('cleanPasses'):
        return False, f'{CLEAN_TABLE} has not passed every rule yet.'
    count = len([line for line in rejected.splitlines() if line.strip()])
    return True, f'{count} rows quarantined with their reasons; {CLEAN_TABLE} passes every rule.'


def _check_alarm() -> tuple[bool, str]:
    alarm = _alarm()
    if not alarm:
        return False, f'Create {ALARM}.'
    if alarm.get('StateValue') != 'ALARM':
        return False, f'{ALARM} is {alarm.get("StateValue")}; it should be in ALARM for the {DAYS[2]} score.'
    return True, f'{ALARM} is in ALARM: the {DAYS[2]} partition scored below {THRESHOLD}.'


def _check_evolve() -> tuple[bool, str]:
    versions = _schema_versions()
    if len([item for item in versions if item.get('Status') == 'AVAILABLE']) != 2:
        return False, f'{SCHEMA} should have two available versions.'
    marker = _marked('evolve-schema') or {}
    if not marker.get('breakingRefused'):
        return False, 'Show that the breaking version is refused.'
    table = _table(RAW_TABLE) or {}
    if NEW_COLUMN[0] not in [column['Name'] for column in table.get('StorageDescriptor', {}).get('Columns', [])]:
        return False, f'Add {NEW_COLUMN[0]} to {RAW_TABLE}.'
    if len(_table_versions()) < 2:
        return False, f'{RAW_TABLE} should have a second table version.'
    if not marker.get('queriedAllDays'):
        return False, 'Query all four days after the change.'
    return True, (f'{SCHEMA} accepted the compatible version and refused the breaking one; {RAW_TABLE} is at '
                  f'version {len(_table_versions())} with {NEW_COLUMN[0]}.')


CHECKS = {
    'land-raw-data': _check_land,
    'create-table': _check_table,
    'sync-partitions': _check_sync,
    'schedule-crawler': _check_crawler,
    'evaluate-quality': _check_quality,
    'quarantine-bad-rows': _check_quarantine,
    'alarm-on-quality': _check_alarm,
    'evolve-schema': _check_evolve,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _blocked(step: str, command: str, started: float, needs: str) -> dict[str, Any] | None:
    ready = {'bucket': _bucket_exists, 'table': lambda: _table(RAW_TABLE) is not None}[needs]()
    if ready:
        return None
    return _result(step, command, {}, False, 'Run the earlier steps first.', started)


def step_land_raw_data() -> dict[str, Any]:
    started = time.perf_counter()
    s3, glue = _client('s3'), _client('glue')
    if not _bucket_exists():
        s3.create_bucket(Bucket=BUCKET)
    names = [name for name, _ in COLUMNS]
    for day in DAYS:
        s3.put_object(Bucket=BUCKET, Key=raw_key(day), Body=to_csv(day_rows(day), names).encode(),
                      ContentType='text/csv')
    if not _database():
        glue.create_database(DatabaseInput={'Name': DATABASE, 'Description': 'Sales data lake'})
    verified, message = _check_land()
    return _result('land-raw-data', LAND_COMMAND, {'Files': _keys(RAW_PREFIX), 'Database': DATABASE},
                   verified, message, started)


def step_create_table() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('create-table', TABLE_COMMAND, started, 'bucket')
    if blocked:
        return blocked
    glue = _client('glue')
    if _table(RAW_TABLE):
        glue.delete_table(DatabaseName=DATABASE, Name=RAW_TABLE)
    glue.create_table(DatabaseName=DATABASE, TableInput=_raw_table_input(COLUMNS))
    glue.create_partition(DatabaseName=DATABASE, TableName=RAW_TABLE, PartitionInput=_partition_input(DAYS[0]))
    counts = athena(f'SELECT dt, COUNT(*) AS orders FROM {RAW_TABLE} GROUP BY dt ORDER BY dt')
    verified, message = _check_table()
    return _result('create-table', TABLE_COMMAND, {
        'Table': {'Name': RAW_TABLE, 'Columns': COLUMNS, 'PartitionKeys': ['dt']},
        'RegisteredPartitions': _partitions(),
        'AthenaOrdersByDay': {'State': counts.get('State'), 'Rows': counts.get('Rows')},
        'Note': 'In AWS only the registered day would be counted; Floci reads every dt= folder.',
    }, verified, message, started)


def step_sync_partitions() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('sync-partitions', SYNC_COMMAND, started, 'table')
    if blocked:
        return blocked
    glue = _client('glue')
    folders = partition_values(_keys(RAW_PREFIX))
    before = _partitions()
    missing = [day for day in folders if day not in before]
    added = {}
    if missing:
        added = glue.batch_create_partition(DatabaseName=DATABASE, TableName=RAW_TABLE,
                                            PartitionInputList=[_partition_input(day) for day in missing])
    indexes = _safe_call(lambda: glue.get_partition_indexes(DatabaseName=DATABASE, TableName=RAW_TABLE)
                         ['PartitionIndexDescriptorList'], [])
    if not any(item.get('IndexName') == PARTITION_INDEX for item in indexes):
        glue.create_partition_index(DatabaseName=DATABASE, TableName=RAW_TABLE,
                                    PartitionIndex={'Keys': ['dt'], 'IndexName': PARTITION_INDEX})
    recent = _partitions(f"dt >= '{DAYS[1]}'")
    indexes = _safe_call(lambda: glue.get_partition_indexes(DatabaseName=DATABASE, TableName=RAW_TABLE)
                         ['PartitionIndexDescriptorList'], [])
    verified, message = _check_sync()
    return _result('sync-partitions', SYNC_COMMAND, {
        'FoldersInS3': folders, 'PartitionsBefore': before, 'Added': missing,
        'Errors': (added or {}).get('Errors', []), 'PartitionsAfter': _partitions(),
        f"Expression dt >= '{DAYS[1]}'": recent,
        'PartitionIndexes': [{key: item.get(key) for key in ('IndexName', 'IndexStatus')} for item in indexes],
    }, verified, message, started)


TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'Service': 'glue.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
CRAWLER_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:ListBucket'],
     'Resource': [f'arn:aws:s3:::{BUCKET}', f'arn:aws:s3:::{BUCKET}/{RAW_PREFIX}*']},
    {'Effect': 'Allow', 'Action': ['glue:GetDatabase', 'glue:GetTable', 'glue:CreateTable', 'glue:UpdateTable',
                                   'glue:BatchCreatePartition', 'glue:GetPartitions'], 'Resource': '*'},
    {'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
     'Resource': f'arn:aws:logs:us-east-1:{ACCOUNT}:log-group:/aws-glue/*'}]}


def step_schedule_crawler() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('schedule-crawler', CRAWLER_COMMAND, started, 'table')
    if blocked:
        return blocked
    iam, glue = _client('iam'), _client('glue')
    role_arn = _safe_call(lambda: iam.get_role(RoleName=CRAWLER_ROLE)['Role']['Arn'])
    if not role_arn:
        role_arn = iam.create_role(RoleName=CRAWLER_ROLE, AssumeRolePolicyDocument=json.dumps(TRUST))['Role']['Arn']
    iam.put_role_policy(RoleName=CRAWLER_ROLE, PolicyName='crawl-raw-orders', PolicyDocument=json.dumps(CRAWLER_POLICY))
    if not _crawler():
        glue.create_crawler(
            Name=CRAWLER, Role=role_arn, DatabaseName=DATABASE,
            Targets={'S3Targets': [{'Path': f's3://{BUCKET}/{RAW_PREFIX}'}]}, Schedule='cron(0 2 * * ? *)',
            SchemaChangePolicy={'UpdateBehavior': 'LOG', 'DeleteBehavior': 'LOG'},
            RecrawlPolicy={'RecrawlBehavior': 'CRAWL_NEW_FOLDERS_ONLY'})
    tables_before = sorted(item['Name'] for item in glue.get_tables(DatabaseName=DATABASE)['TableList'])
    _safe_call(lambda: glue.start_crawler(Name=CRAWLER))
    _wait(lambda: (_crawler() or {}).get('State') == 'READY', 60, delay=2)
    crawler = _crawler() or {}
    tables_after = sorted(item['Name'] for item in glue.get_tables(DatabaseName=DATABASE)['TableList'])
    newest = _object(raw_key(DAYS[-1])) or ''
    inferred = infer_schema(newest)
    catalog = [(column['Name'], column['Type'])
               for column in (_table(RAW_TABLE) or {}).get('StorageDescriptor', {}).get('Columns', [])]
    matches = inferred == catalog
    _marker('schedule-crawler', {'schemaMatches': matches})
    verified, message = _check_crawler()
    return _result('schedule-crawler', CRAWLER_COMMAND, {
        'Crawler': {'State': crawler.get('State'), 'Schedule': (crawler.get('Schedule') or {}).get('ScheduleExpression'),
                    'LastCrawl': (crawler.get('LastCrawl') or {}).get('Status')},
        'TablesBeforeCrawl': tables_before, 'TablesAfterCrawl': tables_after,
        'InferredFromNewestFile': inferred, 'CatalogColumns': catalog, 'SchemaMatches': matches,
    }, verified, message, started)


def step_evaluate_quality() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('evaluate-quality', QUALITY_COMMAND, started, 'table')
    if blocked:
        return blocked
    glue = _client('glue')
    if _ruleset():
        glue.update_data_quality_ruleset(Name=RULESET, Ruleset=DQDL)
    else:
        glue.create_data_quality_ruleset(Name=RULESET, Ruleset=DQDL,
                                         TargetTable={'TableName': RAW_TABLE, 'DatabaseName': DATABASE})
    try:
        role = f'arn:aws:iam::{ACCOUNT}:role/{CRAWLER_ROLE}'
        run = glue.start_data_quality_ruleset_evaluation_run(
            DataSource={'GlueTable': {'DatabaseName': DATABASE, 'TableName': RAW_TABLE}}, Role=role,
            RulesetNames=[RULESET])
        evaluation = {'RunId': run.get('RunId')}
    except ClientError as exc:
        evaluation = {'Error': _code(exc), 'Message': str(exc)[:200]}
    results = _quality(RAW_TABLE)
    _client('s3').put_object(Bucket=BUCKET, Key=f'{DQ_RESULTS_PREFIX}results.json',
                             Body=json.dumps(results, indent=2).encode(), ContentType='application/json')
    verified, message = _check_quality()
    return _result('evaluate-quality', QUALITY_COMMAND, {
        'Ruleset': DQDL, 'GlueEvaluationRunOnFloci': evaluation, 'ResultsByPartition': results,
    }, verified, message, started)


def step_quarantine_bad_rows() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('quarantine-bad-rows', QUARANTINE_COMMAND, started, 'table')
    if blocked:
        return blocked
    s3, glue = _client('s3'), _client('glue')
    summary = {}
    for day in DAYS:
        outcome = athena(f"SELECT order_id, customer, amount, country FROM {RAW_TABLE} WHERE dt = '{day}' "
                         'ORDER BY order_id')
        rows = [_typed(row) for row in outcome.get('Rows', [])]
        good, bad = split_rows(rows)
        s3.put_object(Bucket=BUCKET, Key=f'{CURATED_PREFIX}dt={day}/part-000.json', Body=json_lines(good).encode(),
                      ContentType='application/json')
        if bad:
            s3.put_object(Bucket=BUCKET, Key=f'{QUARANTINE_PREFIX}dt={day}/rejected.json',
                          Body=json_lines(bad).encode(), ContentType='application/json')
        summary[day] = {'read': len(rows), 'clean': len(good), 'quarantined': bad}
    if _table(CLEAN_TABLE):
        glue.delete_table(DatabaseName=DATABASE, Name=CLEAN_TABLE)
    glue.create_table(DatabaseName=DATABASE, TableInput=_clean_table_input())
    glue.batch_create_partition(DatabaseName=DATABASE, TableName=CLEAN_TABLE, PartitionInputList=[
        {'Values': [day], 'StorageDescriptor': {**_clean_table_input()['StorageDescriptor'],
                                                'Location': f's3://{BUCKET}/{CURATED_PREFIX}dt={day}/'}}
        for day in DAYS])
    clean_quality = _quality(CLEAN_TABLE)
    passes = bool(clean_quality) and all(item['score'] == 1.0 for item in clean_quality.values())
    _marker('quarantine-bad-rows', {'cleanPasses': passes})
    verified, message = _check_quarantine()
    return _result('quarantine-bad-rows', QUARANTINE_COMMAND, {
        'ByDay': summary, 'CleanTableQuality': {day: item['score'] for day, item in clean_quality.items()},
    }, verified, message, started)


def step_alarm_on_quality() -> dict[str, Any]:
    started = time.perf_counter()
    body = _object(f'{DQ_RESULTS_PREFIX}results.json')
    if not body:
        return _result('alarm-on-quality', ALARM_COMMAND, {}, False, 'Evaluate the quality rules first.', started)
    results = json.loads(body)
    sns, cloudwatch = _client('sns'), _client('cloudwatch')
    topic_arn = sns.create_topic(Name=TOPIC)['TopicArn']
    dimensions = [{'Name': 'Database', 'Value': DATABASE}, {'Name': 'Table', 'Value': RAW_TABLE}]
    cloudwatch.put_metric_alarm(
        AlarmName=ALARM, Namespace=NAMESPACE, MetricName=METRIC, Dimensions=dimensions, Statistic='Minimum',
        Period=60, EvaluationPeriods=1, Threshold=THRESHOLD, ComparisonOperator='LessThanThreshold',
        TreatMissingData='notBreaching', AlarmActions=[topic_arn],
        AlarmDescription='A raw_orders partition failed data quality rules.')

    def publish() -> None:
        cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=[
            {'MetricName': METRIC, 'Dimensions': dimensions, 'Value': item['score'], 'Unit': 'None'}
            for item in results.values()])

    publish()

    def in_alarm() -> bool:
        if (_alarm() or {}).get('StateValue') == 'ALARM':
            return True
        publish()
        return False

    _wait(in_alarm, ALARM_WAIT_SECONDS, delay=10)
    alarm = _alarm() or {}
    verified, message = _check_alarm()
    return _result('alarm-on-quality', ALARM_COMMAND, {
        'Published': {day: item['score'] for day, item in results.items()},
        'Alarm': {key: alarm.get(key) for key in ('AlarmName', 'StateValue', 'StateReason', 'AlarmActions')},
    }, verified, message, started)


def step_evolve_schema() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('evolve-schema', EVOLVE_COMMAND, started, 'table')
    if blocked:
        return blocked
    glue, s3 = _client('glue'), _client('s3')
    if not _safe_call(lambda: glue.get_registry(RegistryId={'RegistryName': REGISTRY})):
        glue.create_registry(RegistryName=REGISTRY, Description='Sales event schemas')
    if not _safe_call(lambda: glue.get_schema(SchemaId={'RegistryName': REGISTRY, 'SchemaName': SCHEMA})):
        glue.create_schema(RegistryId={'RegistryName': REGISTRY}, SchemaName=SCHEMA, DataFormat='AVRO',
                           Compatibility='BACKWARD', SchemaDefinition=AVRO_V1)
    schema_id = {'RegistryName': REGISTRY, 'SchemaName': SCHEMA}
    compatible = glue.register_schema_version(SchemaId=schema_id, SchemaDefinition=AVRO_V2)
    _wait(lambda: all(item.get('Status') != 'PENDING' for item in _schema_versions()), 20, delay=1)
    try:
        breaking = glue.register_schema_version(SchemaId=schema_id, SchemaDefinition=AVRO_BREAKING)
        refused = None
        _wait(lambda: all(item.get('Status') != 'PENDING' for item in _schema_versions()), 20, delay=1)
        status = next((item.get('Status') for item in _schema_versions()
                       if item.get('SchemaVersionId') == breaking.get('SchemaVersionId')), None)
        breaking_refused = status == 'FAILURE'
    except ClientError as exc:
        refused = str(exc)[:240]
        breaking_refused = True

    evolved = COLUMNS + [NEW_COLUMN]
    columns = [column['Name'] for column in (_table(RAW_TABLE) or {}).get('StorageDescriptor', {}).get('Columns', [])]
    if NEW_COLUMN[0] not in columns:
        glue.update_table(DatabaseName=DATABASE, TableInput=_raw_table_input(evolved))
    names = [name for name, _ in evolved]
    new_rows = [{**row, 'channel': 'app' if index % 2 else 'web'} for index, row in enumerate(day_rows(NEW_DAY))]
    s3.put_object(Bucket=BUCKET, Key=raw_key(NEW_DAY), Body=to_csv(new_rows, names).encode(), ContentType='text/csv')
    if NEW_DAY not in _partitions():
        glue.create_partition(DatabaseName=DATABASE, TableName=RAW_TABLE, PartitionInput={
            'Values': [NEW_DAY], 'StorageDescriptor': _csv_descriptor(f's3://{BUCKET}/{RAW_PREFIX}dt={NEW_DAY}/', evolved)})
    query = f'SELECT dt, channel, COUNT(*) AS orders FROM {RAW_TABLE} GROUP BY dt, channel ORDER BY dt, channel'
    before_backfill = athena(query)
    backfilled = []
    if before_backfill.get('State') != 'SUCCEEDED':
        for day in DAYS:
            body = _object(raw_key(day)) or ''
            rows = list(csv.DictReader(io.StringIO(body)))
            s3.put_object(Bucket=BUCKET, Key=raw_key(day), Body=to_csv(rows, names).encode(), ContentType='text/csv')
            backfilled.append(day)
    after = athena(query)
    days_seen = {row.get('dt') for row in after.get('Rows', [])}
    _marker('evolve-schema', {'breakingRefused': breaking_refused,
                              'queriedAllDays': after.get('State') == 'SUCCEEDED' and days_seen >= set(DAYS + [NEW_DAY])})
    verified, message = _check_evolve()
    return _result('evolve-schema', EVOLVE_COMMAND, {
        'CompatibleVersion': {'VersionNumber': compatible.get('VersionNumber'), 'Status': compatible.get('Status')},
        'BreakingVersionRefused': refused or breaking_refused,
        'SchemaVersions': [{key: item.get(key) for key in ('VersionNumber', 'Status')} for item in _schema_versions()],
        'TableVersions': len(_table_versions()),
        'AthenaBeforeBackfill': {'State': before_backfill.get('State'), 'Reason': (before_backfill.get('Reason') or '')[:200]},
        'BackfilledDays': backfilled,
        'AthenaAfter': {'State': after.get('State'), 'Rows': after.get('Rows')},
    }, verified, message, started)


RUNNERS = {
    'land-raw-data': step_land_raw_data,
    'create-table': step_create_table,
    'sync-partitions': step_sync_partitions,
    'schedule-crawler': step_schedule_crawler,
    'evaluate-quality': step_evaluate_quality,
    'quarantine-bad-rows': step_quarantine_bad_rows,
    'alarm-on-quality': step_alarm_on_quality,
    'evolve-schema': step_evolve_schema,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError, StopIteration):
        return False


def status() -> dict[str, Any]:
    steps = {}
    for step in STEP_KEYS:
        verified = _safe(CHECKS[step])
        steps[step] = {
            'verified': verified,
            'verification': {'status': 'passed', 'message': 'Verified against Floci.'} if verified else None,
        }
    passed = sum(1 for item in steps.values() if item['verified'])
    complete = passed == len(steps)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def reset() -> dict[str, Any]:
    """Remove the alarm, topic, schema, registry, ruleset, crawler, role, tables, database, and bucket."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code in ('EntityNotFoundException',
                                                                    'ResourceNotFoundException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudwatch, sns, glue, iam, s3 = (_client(name) for name in ('cloudwatch', 'sns', 'glue', 'iam', 's3'))
    if _alarm():
        attempt(f'alarm:{ALARM}', lambda: cloudwatch.delete_alarms(AlarmNames=[ALARM]))
    topic_arn = f'arn:aws:sns:us-east-1:{ACCOUNT}:{TOPIC}'
    if _safe_call(lambda: sns.get_topic_attributes(TopicArn=topic_arn)):
        attempt(f'topic:{TOPIC}', lambda: sns.delete_topic(TopicArn=topic_arn))
    if _safe_call(lambda: glue.get_schema(SchemaId={'RegistryName': REGISTRY, 'SchemaName': SCHEMA})):
        attempt(f'schema:{SCHEMA}', lambda: glue.delete_schema(SchemaId={'RegistryName': REGISTRY, 'SchemaName': SCHEMA}))
    if _safe_call(lambda: glue.get_registry(RegistryId={'RegistryName': REGISTRY})):
        attempt(f'registry:{REGISTRY}', lambda: glue.delete_registry(RegistryId={'RegistryName': REGISTRY}))
    if _ruleset():
        attempt(f'ruleset:{RULESET}', lambda: glue.delete_data_quality_ruleset(Name=RULESET))
    if _crawler():
        attempt(f'crawler:{CRAWLER}', lambda: glue.delete_crawler(Name=CRAWLER))
    if _safe_call(lambda: iam.get_role(RoleName=CRAWLER_ROLE)):
        def delete_role() -> None:
            for name in iam.list_role_policies(RoleName=CRAWLER_ROLE)['PolicyNames']:
                iam.delete_role_policy(RoleName=CRAWLER_ROLE, PolicyName=name)
            iam.delete_role(RoleName=CRAWLER_ROLE)
        attempt(f'role:{CRAWLER_ROLE}', delete_role)
    if _database():
        for name in (RAW_TABLE, CLEAN_TABLE):
            if _table(name):
                attempt(f'table:{name}', lambda name=name: glue.delete_table(DatabaseName=DATABASE, Name=name))
        attempt(f'database:{DATABASE}', lambda: glue.delete_database(Name=DATABASE))
    if _bucket_exists():
        def empty_and_delete() -> None:
            for key in _keys(''):
                s3.delete_object(Bucket=BUCKET, Key=key)
            s3.delete_bucket(Bucket=BUCKET)
        attempt(f'bucket:{BUCKET}', empty_and_delete)

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws cloudwatch delete-alarms --alarm-names {ALARM}\n'
            f'aws sns delete-topic --topic-arn arn:aws:sns:us-east-1:{ACCOUNT}:{TOPIC}\n'
            f'aws glue delete-schema --schema-id RegistryName={REGISTRY},SchemaName={SCHEMA}\n'
            f'aws glue delete-registry --registry-id RegistryName={REGISTRY}\n'
            f'aws glue delete-data-quality-ruleset --name {RULESET}\n'
            f'aws glue delete-crawler --name {CRAWLER}\n'
            f'aws iam delete-role --role-name {CRAWLER_ROLE}\n'
            f'aws glue delete-table --database-name {DATABASE} --name {RAW_TABLE}\n'
            f'aws glue delete-table --database-name {DATABASE} --name {CLEAN_TABLE}\n'
            f'aws glue delete-database --name {DATABASE}\n'
            f'aws s3 rb s3://{BUCKET} --force'
        ),
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'errors': errors,
        'verification': {
            'status': 'failed' if errors else 'passed',
            'message': 'Some lab resources could not be removed: ' + '; '.join(errors) if errors
            else 'The alarm, topic, schema registry, ruleset, crawler, role, tables, database, and bucket are gone.',
        },
    }
