"""Redshift Serverless warehouse: a namespace and workgroup, a star schema designed with distribution and sort keys,
sales staged in S3 and loaded in one transaction through the Data API, a materialized view refreshed after a late
file, a rolling seven-day average, an analyst who can read only a masked view, an export to S3 with lifecycle rules,
and a usage limit and a snapshot.

Floci 2.4.0 runs a real PostgreSQL behind each workgroup, so the SQL, transactions, materialized views, window
functions and grants are real, and the Data API behaves like AWS's (asynchronous statements, one statement per
ExecuteStatement, a batch as one transaction). What Floci does not do: it is PostgreSQL, not Redshift, so Redshift-only
syntax (DISTSTYLE, DISTKEY, SORTKEY, ENCODE, COPY, UNLOAD) is refused. The lab shows the Redshift DDL, runs the
standard SQL part of it, loads with INSERT instead of COPY, and exports with the Data API instead of UNLOAD.
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import date, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'redshift'
LAB_KEY = 'serverless-warehouse'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'

NAMESPACE = 'lab-dw'
WORKGROUP = 'lab-dw-wg'
DATABASE = 'sales'
ADMIN_USER = 'labadmin'
ADMIN_PASSWORD = 'LabDwAdmin2026x'
ADMIN_SECRET = 'lab-dw/admin'
ANALYST_USER = 'analyst'
ANALYST_PASSWORD = 'LabAnalyst2026x'
ANALYST_SECRET = 'lab-dw/analyst'
BUCKET = 'lab-dw-staging'
STAGE_PREFIX = 'staging/sales/'
EXPORT_PREFIX = 'exports/daily_store_sales/'
MANIFEST_KEY = 'staging/sales.manifest'
MV = 'mv_daily_store_sales'
MASKED_VIEW = 'sales_masked'
SNAPSHOT = 'lab-dw-after-load'
USAGE_LIMIT_RPU_HOURS = 60
BASE_CAPACITY = 8

STORES = [('S1', 'Downtown', 'east'), ('S2', 'Harbor', 'west'), ('S3', 'Airport', 'east')]
PRODUCTS = [('P1', 'Coffee beans', 'grocery', 12.5), ('P2', 'Mug', 'homeware', 8.0),
            ('P3', 'Grinder', 'homeware', 45.0), ('P4', 'Tea', 'grocery', 6.5)]
CUSTOMERS = ['ana', 'ben', 'chen', 'dara', 'eli', 'fay', 'gus']
FIRST_DAY = date(2026, 10, 1)
DAYS = 14
LATE_DAY = FIRST_DAY + timedelta(days=DAYS)
BAD_SALE_ID = 9999

STATEMENT_TIMEOUT = 60
WORKGROUP_WAIT_SECONDS = 180
FLOCI = 'How Floci emulates this: '


# ── Pure helpers ─────────────────────────────────────────────────────────────

def sale_rows(first: date = FIRST_DAY, days: int = DAYS, start_id: int = 1) -> list[dict[str, Any]]:
    """Two sales per store per day, deterministic, so every total can be checked."""
    rows = []
    sale_id = start_id
    for offset in range(days):
        day = first + timedelta(days=offset)
        for store_index, (store_id, _, _) in enumerate(STORES):
            for slot in range(2):
                product = PRODUCTS[(offset + store_index + slot) % len(PRODUCTS)]
                quantity = 1 + (offset * 3 + store_index * 5 + slot * 7) % 4
                customer = CUSTOMERS[(offset + store_index * 2 + slot) % len(CUSTOMERS)]
                rows.append({'sale_id': sale_id, 'sale_date': day.isoformat(), 'store_id': store_id,
                             'product_id': product[0], 'customer_email': f'{customer}@example.com',
                             'quantity': quantity, 'amount': round(quantity * product[3], 2)})
                sale_id += 1
    return rows


def late_rows() -> list[dict[str, Any]]:
    return sale_rows(LATE_DAY, 1, start_id=DAYS * len(STORES) * 2 + 1)


FIELDS = ['sale_id', 'sale_date', 'store_id', 'product_id', 'customer_email', 'quantity', 'amount']


def to_csv(rows: list[dict[str, Any]], extra_lines: list[str] | None = None) -> str:
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS, lineterminator='\n')
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    for line in extra_lines or []:
        out.write(line + '\n')
    return out.getvalue()


BAD_LINE = f'{BAD_SALE_ID},2026-10-07,S2,P3,eli@example.com,two,90.00'


def staged_files() -> dict[str, str]:
    """Two weekly files; the second carries one row whose quantity is not a number."""
    rows = sale_rows()
    half = len(rows) // 2
    return {f'{STAGE_PREFIX}2026-10-week1.csv': to_csv(rows[:half]),
            f'{STAGE_PREFIX}2026-10-week2.csv': to_csv(rows[half:], [BAD_LINE])}


def manifest(files: dict[str, str]) -> dict[str, Any]:
    return {'entries': [{'url': f's3://{BUCKET}/{key}', 'mandatory': True,
                         'meta': {'content_length': len(body.encode())}} for key, body in sorted(files.items())]}


def parse_rows(body: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split a CSV file into loadable rows and load errors, like COPY with MAXERROR and STL_LOAD_ERRORS."""
    good, errors = [], []
    for line_number, row in enumerate(csv.DictReader(io.StringIO(body)), start=2):
        try:
            good.append({'sale_id': int(row['sale_id']), 'sale_date': date.fromisoformat(row['sale_date']).isoformat(),
                         'store_id': row['store_id'], 'product_id': row['product_id'],
                         'customer_email': row['customer_email'], 'quantity': int(row['quantity']),
                         'amount': float(row['amount'])})
        except (TypeError, ValueError, KeyError) as exc:
            errors.append({'line_number': line_number, 'raw_line': ','.join(row.get(f) or '' for f in FIELDS),
                           'err_reason': f'Invalid value: {exc}'})
    return good, errors


def _literal(value: Any) -> str:
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def insert_sql(rows: list[dict[str, Any]], table: str = 'fact_sales') -> str:
    values = ',\n'.join('(' + ', '.join(_literal(row[field]) for field in FIELDS) + ')' for row in rows)
    return f'INSERT INTO {table} ({", ".join(FIELDS)}) VALUES\n{values}'


def daily_store_totals(rows: list[dict[str, Any]]) -> dict[tuple[str, str], float]:
    totals: dict[tuple[str, str], float] = {}
    for row in rows:
        key = (row['sale_date'], row['store_id'])
        totals[key] = round(totals.get(key, 0) + row['amount'], 2)
    return totals


def rolling_average(totals: dict[tuple[str, str], float], store: str, window: int = 7) -> dict[str, float]:
    """Average of the store's daily totals over the current day and the six before it (fewer at the start)."""
    days = sorted(day for day, store_id in totals if store_id == store)
    result = {}
    for index, day in enumerate(days):
        recent = [totals[(item, store)] for item in days[max(0, index - window + 1):index + 1]]
        result[day] = round(sum(recent) / len(recent), 2)
    return result


def mask_email(email: str) -> str:
    local, _, domain = email.partition('@')
    return f'{local[:1]}***@{domain}'


def records_to_rows(result: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for record in result.get('Records') or []:
        row = []
        for field in record:
            if field.get('isNull'):
                row.append(None)
            else:
                row.append(next(iter(value for key, value in field.items() if key != 'isNull'), None))
        rows.append(row)
    return rows


# ── SQL shown and run ────────────────────────────────────────────────────────

REDSHIFT_DDL = [
    'CREATE TABLE dim_store (store_id CHAR(2) PRIMARY KEY, name VARCHAR(40), region VARCHAR(10)) DISTSTYLE ALL',
    'CREATE TABLE dim_product (product_id CHAR(2) PRIMARY KEY, name VARCHAR(40), category VARCHAR(20), '
    'unit_price DECIMAL(8,2)) DISTSTYLE ALL',
    'CREATE TABLE fact_sales (sale_id INT PRIMARY KEY, sale_date DATE NOT NULL, store_id CHAR(2) NOT NULL, '
    'product_id CHAR(2) NOT NULL, customer_email VARCHAR(80), quantity INT ENCODE AZ64, amount DECIMAL(10,2) ENCODE AZ64) '
    'DISTSTYLE KEY DISTKEY (product_id) COMPOUND SORTKEY (sale_date, store_id)',
]
PORTABLE_DDL = [
    'CREATE TABLE IF NOT EXISTS dim_store (store_id CHAR(2) PRIMARY KEY, name VARCHAR(40), region VARCHAR(10))',
    'CREATE TABLE IF NOT EXISTS dim_product (product_id CHAR(2) PRIMARY KEY, name VARCHAR(40), '
    'category VARCHAR(20), unit_price DECIMAL(8,2))',
    'CREATE TABLE IF NOT EXISTS fact_sales (sale_id INT PRIMARY KEY, sale_date DATE NOT NULL, '
    'store_id CHAR(2) NOT NULL REFERENCES dim_store, product_id CHAR(2) NOT NULL REFERENCES dim_product, '
    'customer_email VARCHAR(80), quantity INT, amount DECIMAL(10,2))',
    'CREATE TABLE IF NOT EXISTS load_errors (filename VARCHAR(200), line_number INT, raw_line VARCHAR(400), '
    'err_reason VARCHAR(200), loaded_at TIMESTAMP DEFAULT now())',
]
DIMENSION_SQL = [
    'DELETE FROM dim_store',
    'INSERT INTO dim_store VALUES ' + ', '.join(f"('{s}', '{n}', '{r}')" for s, n, r in STORES),
    'DELETE FROM dim_product',
    'INSERT INTO dim_product VALUES ' + ', '.join(f"('{p}', '{n}', '{c}', {u})" for p, n, c, u in PRODUCTS),
]
MV_SQL = (
    f'CREATE MATERIALIZED VIEW {MV} AS SELECT f.sale_date, f.store_id, s.region, SUM(f.amount) AS total, '
    'COUNT(*) AS sales FROM fact_sales f JOIN dim_store s ON s.store_id = f.store_id GROUP BY f.sale_date, f.store_id, s.region'
)
ROLLING_SQL = (
    'SELECT store_id, sale_date, total, ROUND(AVG(total) OVER (PARTITION BY store_id ORDER BY sale_date '
    f'ROWS BETWEEN 6 PRECEDING AND CURRENT ROW), 2) AS avg_7d FROM {MV} ORDER BY store_id, sale_date'
)
TOP_PRODUCTS_SQL = (
    'SELECT p.category, p.name, SUM(f.amount) AS revenue, RANK() OVER (PARTITION BY p.category ORDER BY SUM(f.amount) DESC) '
    'AS rank_in_category FROM fact_sales f JOIN dim_product p ON p.product_id = f.product_id GROUP BY p.category, p.name '
    'ORDER BY p.category, rank_in_category'
)
MASKED_VIEW_SQL = (
    f'CREATE OR REPLACE VIEW {MASKED_VIEW} AS SELECT sale_id, sale_date, store_id, product_id, quantity, amount, '
    "LEFT(customer_email, 1) || '***@' || SPLIT_PART(customer_email, '@', 2) AS customer_email FROM fact_sales"
)

# ── Commands shown in the console ───────────────────────────────────────────

WORKGROUP_COMMAND = (
    f'aws secretsmanager create-secret --name {ADMIN_SECRET} '
    f'--secret-string \'{{"username":"{ADMIN_USER}","password":"<admin password>"}}\'\n'
    f'aws redshift-serverless create-namespace --namespace-name {NAMESPACE} --db-name {DATABASE} '
    f'--admin-username {ADMIN_USER} --admin-user-password <admin password>\n'
    f'aws redshift-serverless create-workgroup --workgroup-name {WORKGROUP} --namespace-name {NAMESPACE} '
    f'--base-capacity {BASE_CAPACITY}\n'
    f'aws redshift-serverless get-workgroup --workgroup-name {WORKGROUP}'
)
SCHEMA_COMMAND = (
    '# The Redshift DDL (distribution and sort keys):\n'
    + '\n'.join(f'#   {sql}' for sql in REDSHIFT_DDL) + '\n'
    f'aws redshift-data batch-execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    f'--secret-arn <{ADMIN_SECRET} arn> --sqls "CREATE TABLE dim_store (...)" "CREATE TABLE dim_product (...)" '
    '"CREATE TABLE fact_sales (...)" "CREATE TABLE load_errors (...)"\n'
    f'aws redshift-data list-tables --workgroup-name {WORKGROUP} --database {DATABASE} --schema-pattern public'
)
LOAD_COMMAND = (
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3 cp 2026-10-week1.csv s3://{BUCKET}/{STAGE_PREFIX}\n'
    f'aws s3 cp 2026-10-week2.csv s3://{BUCKET}/{STAGE_PREFIX}\n'
    f'aws s3 cp sales.manifest s3://{BUCKET}/{MANIFEST_KEY}\n'
    f'# In Redshift: COPY fact_sales FROM \'s3://{BUCKET}/{MANIFEST_KEY}\' IAM_ROLE default CSV IGNOREHEADER 1 '
    'MANIFEST MAXERROR 5\n'
    f'aws redshift-data batch-execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    '--secret-arn <admin secret arn> --sqls "INSERT INTO fact_sales VALUES (...)" "INSERT INTO load_errors VALUES (...)"\n'
    f'aws redshift-data execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    '--secret-arn <admin secret arn> --sql "SELECT COUNT(*) FROM fact_sales"'
)
MV_COMMAND = (
    f'aws redshift-data execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    f'--secret-arn <admin secret arn> --sql "CREATE MATERIALIZED VIEW {MV} AS SELECT sale_date, store_id, '
    'SUM(amount) ..."\n'
    f'aws s3 cp 2026-10-15-late.csv s3://{BUCKET}/{STAGE_PREFIX}\n'
    f'aws redshift-data execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    f'--secret-arn <admin secret arn> --sql "REFRESH MATERIALIZED VIEW {MV}"'
)
ANALYZE_COMMAND = (
    f'aws redshift-data execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    '--secret-arn <admin secret arn> --sql "SELECT store_id, sale_date, total, AVG(total) OVER (PARTITION BY store_id '
    'ORDER BY sale_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) ..."\n'
    'aws redshift-data describe-statement --id <statement id>\n'
    'aws redshift-data get-statement-result --id <statement id>'
)
ACCESS_COMMAND = (
    f'aws redshift-data batch-execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    f'--secret-arn <admin secret arn> --sqls "CREATE USER {ANALYST_USER} PASSWORD \'<analyst password>\'" '
    f'"CREATE VIEW {MASKED_VIEW} AS SELECT ... LEFT(customer_email, 1) || \'***@\' ..." '
    f'"GRANT SELECT ON {MASKED_VIEW} TO {ANALYST_USER}"\n'
    f'aws secretsmanager create-secret --name {ANALYST_SECRET} --secret-string file://analyst.json\n'
    f'aws redshift-data execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    f'--secret-arn <analyst secret arn> --sql "SELECT * FROM {MASKED_VIEW} LIMIT 3"\n'
    f'aws redshift-data execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    '--secret-arn <analyst secret arn> --sql "SELECT customer_email FROM fact_sales LIMIT 1"   # denied'
)
EXPORT_COMMAND = (
    f'# In Redshift: UNLOAD (\'SELECT * FROM {MV}\') TO \'s3://{BUCKET}/{EXPORT_PREFIX}\' IAM_ROLE default '
    'FORMAT PARQUET PARTITION BY (sale_date)\n'
    f'aws redshift-data execute-statement --workgroup-name {WORKGROUP} --database {DATABASE} '
    f'--secret-arn <admin secret arn> --sql "SELECT * FROM {MV}" --result-format CSV\n'
    'aws redshift-data get-statement-result-v2 --id <statement id>\n'
    f'aws s3 cp daily_store_sales.csv s3://{BUCKET}/{EXPORT_PREFIX}month=2026-10/part-000.csv\n'
    f'aws s3api put-bucket-lifecycle-configuration --bucket {BUCKET} --lifecycle-configuration file://lifecycle.json'
)
COST_COMMAND = (
    f'aws redshift-serverless create-usage-limit --resource-arn <{WORKGROUP} arn> --usage-type serverless-compute '
    f'--amount {USAGE_LIMIT_RPU_HOURS} --period monthly --breach-action emit-metric\n'
    f'aws redshift-serverless create-snapshot --namespace-name {NAMESPACE} --snapshot-name {SNAPSHOT} '
    '--retention-period 7\n'
    f'aws redshift-serverless list-snapshots --namespace-name {NAMESPACE}'
)

ROW_COUNT = DAYS * len(STORES) * 2

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Build a Redshift Serverless warehouse: star schema, loads, materialized views, and masked access',
    'description': (
        'Create a Redshift Serverless namespace and workgroup, design a star schema with distribution and sort keys, '
        'stage sales in S3 and load them in one transaction through the Data API, keep a materialized view fresh '
        'after a late file, compute a rolling seven-day average, give an analyst a masked view and nothing else, '
        'export results to S3 with lifecycle rules, and cap the cost with a usage limit and a snapshot.'
    ),
    'steps': [
        {
            'key': 'create-workgroup',
            'title': f'Create the {NAMESPACE} namespace and the {WORKGROUP} workgroup',
            'command': WORKGROUP_COMMAND,
            'explanation': (
                'Redshift Serverless splits storage from compute. The namespace holds the database, its users, '
                f'snapshots and the IAM roles used by COPY and UNLOAD; the workgroup holds the compute, measured in '
                f'Redshift Processing Units (RPUs, base capacity {BASE_CAPACITY} here), its network settings and its '
                'endpoint. You pay for RPU-seconds while queries run and for storage, so it suits spiky or unknown '
                'load; a provisioned RA3 cluster costs the same whether busy or idle and suits steady load. The admin '
                f'password goes into the {ADMIN_SECRET} secret, so code passes a secret ARN to the Data API and never '
                'holds the password. '
                + FLOCI + 'the workgroup is a real PostgreSQL 15 container behind the endpoint, ready in seconds.'
            ),
        },
        {
            'key': 'design-schema',
            'title': 'Design a star schema with distribution and sort keys',
            'command': SCHEMA_COMMAND,
            'explanation': (
                'A star schema keeps facts (fact_sales, one row per sale) apart from dimensions (dim_store, '
                'dim_product). In Redshift the small dimensions use DISTSTYLE ALL, a copy on every node, so joins '
                'never move them; the fact table is distributed on the column it joins most on its largest dimension '
                '(DISTKEY product_id) and sorted on the columns queries filter by (SORTKEY sale_date, store_id), so '
                'a date filter skips whole blocks. AZ64 compresses numbers. Pick a DISTKEY with many distinct values, '
                'or rows pile up on one slice (skew). Primary and foreign keys are informational in Redshift: they '
                'help the planner but are not enforced. A load_errors table will hold rejected rows. '
                + FLOCI + 'Floci runs PostgreSQL, which refuses DISTSTYLE, DISTKEY, SORTKEY and ENCODE; the lab '
                'tries the Redshift DDL, records the error, and creates the same tables with standard SQL, where the '
                'keys are enforced.'
            ),
        },
        {
            'key': 'stage-and-load',
            'title': f'Stage two files in S3 and load {ROW_COUNT} sales in one transaction',
            'command': LOAD_COMMAND,
            'explanation': (
                f'The sales arrive as CSV files under s3://{BUCKET}/{STAGE_PREFIX}, listed in a manifest so the load '
                'takes exactly those files. In Redshift, COPY loads them in parallel, one file per slice at a time, '
                'so split large loads into files of similar size (a multiple of the slice count) and compress them. '
                'MAXERROR lets the load skip bad rows and records them in STL_LOAD_ERRORS; without it one bad row '
                'fails the whole load. Here week 2 holds a row whose quantity is "two". The load runs as one '
                'BatchExecuteStatement, which is a single transaction: the first attempt includes the bad row and '
                f'fails, leaving fact_sales empty, and the second loads the {ROW_COUNT} good rows and writes the bad '
                'one to load_errors. '
                + FLOCI + 'the transaction and rollback are real. Floci refuses COPY, so the lab reads the files from '
                'S3 and loads them with INSERT; that is fine for a lab, but in Redshift single-row INSERTs are slow '
                'and COPY is the way to load.'
            ),
        },
        {
            'key': 'materialized-view',
            'title': f'Keep {MV} fresh after a late file',
            'command': MV_COMMAND,
            'explanation': (
                f'{MV} precomputes daily sales per store with the region from dim_store, so dashboards read a few '
                'rows instead of joining and aggregating the fact table. A materialized view is a stored result: when '
                f'the {LATE_DAY.isoformat()} file arrives late, the view does not show it until REFRESH MATERIALIZED '
                'VIEW runs. Redshift refreshes incrementally where it can, and AUTO REFRESH YES refreshes it in the '
                'background. A plain view always reads the current data but recomputes it on every query. '
                + FLOCI + 'the materialized view and the refresh are real PostgreSQL; the lab shows the missing day '
                'before the refresh and the right total after.'
            ),
        },
        {
            'key': 'analyze-sales',
            'title': 'Compute a rolling seven-day average and rank products',
            'command': ANALYZE_COMMAND,
            'explanation': (
                'Window functions compute across rows without collapsing them. AVG(total) OVER (PARTITION BY '
                'store_id ORDER BY sale_date ROWS BETWEEN 6 PRECEDING AND CURRENT ROW) is a rolling seven-day average '
                'for each store, and RANK() OVER (PARTITION BY category ORDER BY revenue DESC) ranks products inside '
                'each category. The Data API is asynchronous: ExecuteStatement returns an Id at once, '
                'DescribeStatement reports SUBMITTED, STARTED, FINISHED or FAILED, and GetStatementResult pages '
                'through the rows, so a Lambda function or Step Functions workflow does not hold a connection open. '
                'The lab checks every average against its own arithmetic. '
                + FLOCI + 'the SQL runs in PostgreSQL, which has the same window functions.'
            ),
        },
        {
            'key': 'analyst-access',
            'title': f'Let {ANALYST_USER} read only a masked view',
            'command': ACCESS_COMMAND,
            'explanation': (
                f'{ANALYST_USER} is a database user with its own secret, {ANALYST_SECRET}. It is granted SELECT on '
                f'{MASKED_VIEW} only: the view shows every sale but masks the email (a***@example.com). A view runs '
                'with its owner\'s rights, so the analyst never needs a grant on fact_sales, and a query on the '
                'table is denied. In Redshift you can also attach a dynamic data masking policy to the column, a '
                'row-level security policy to the table, and grant through roles (CREATE ROLE, GRANT ROLE) rather '
                'than to users one by one. '
                + FLOCI + 'the user, grant and denial are real: the Data API connects with the analyst\'s secret, '
                'and PostgreSQL refuses the table with "permission denied".'
            ),
        },
        {
            'key': 'export-and-lifecycle',
            'title': 'Export the daily totals to S3 and expire old staging files',
            'command': EXPORT_COMMAND,
            'explanation': (
                'UNLOAD writes a query result to S3 in parallel, as CSV or Parquet, partitioned by a column, so other '
                'tools (Athena, EMR, a partner) read it without touching the warehouse. Here the lab asks the Data '
                f'API for the result as CSV and writes it to {EXPORT_PREFIX}month=2026-10/. A lifecycle rule then '
                'expires staging files after 30 days, once they are loaded, and moves exports to S3 Glacier Instant '
                'Retrieval after 90 days. '
                + FLOCI + 'Floci refuses UNLOAD, so the lab exports with GetStatementResultV2 in CSV format; the '
                'lifecycle configuration is stored, and Floci applies expiration but not transitions.'
            ),
        },
        {
            'key': 'cost-and-recovery',
            'title': f'Cap compute at {USAGE_LIMIT_RPU_HOURS} RPU-hours a month and take a snapshot',
            'command': COST_COMMAND,
            'explanation': (
                f'A usage limit on {WORKGROUP} sets a monthly budget of RPU-hours; its breach action logs, emits a '
                'CloudWatch metric you can alarm on, or deactivates the workgroup so queries stop. Lower base '
                'capacity and a maximum RPU setting also cap cost. Redshift Serverless keeps recovery points every '
                f'30 minutes for 24 hours; {SNAPSHOT} is a manual snapshot kept for 7 days, which you can restore '
                'into a namespace or share with another account. '
                + FLOCI + 'the usage limit is stored but never measured, and the snapshot dumps the real database, '
                'so RestoreFromSnapshot brings the tables back.'
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

def _namespace() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('redshift-serverless').get_namespace(namespaceName=NAMESPACE)['namespace'])


def _workgroup() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('redshift-serverless').get_workgroup(workgroupName=WORKGROUP)['workgroup'])


def _available() -> bool:
    return (_workgroup() or {}).get('status') == 'AVAILABLE'


def _secret_arn(name: str) -> str | None:
    return _safe_call(lambda: _client('secretsmanager').describe_secret(SecretId=name)['ARN'])


def _put_secret(name: str, user: str, password: str) -> str:
    sm = _client('secretsmanager')
    value = json.dumps({'username': user, 'password': password})
    try:
        return sm.create_secret(Name=name, SecretString=value)['ARN']
    except ClientError as exc:
        if _code(exc) != 'ResourceExistsException':
            raise
        sm.put_secret_value(SecretId=name, SecretString=value)
        return sm.describe_secret(SecretId=name)['ARN']


def _finish(statement_id: str) -> dict[str, Any]:
    data = _client('redshift-data')
    found: dict[str, Any] = {}

    def done() -> bool:
        found.update(data.describe_statement(Id=statement_id))
        return found.get('Status') in ('FINISHED', 'FAILED', 'ABORTED')

    if not _wait(done, STATEMENT_TIMEOUT, delay=0.5):
        found['Status'] = 'TIMED_OUT'
    return found


def _target(secret: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {'WorkgroupName': WORKGROUP, 'Database': DATABASE}
    arn = _secret_arn(secret) if secret else None
    if arn:
        params['SecretArn'] = arn
    return params


def run_sql(sql: str, secret: str | None = ADMIN_SECRET, **extra) -> dict[str, Any]:
    """Run one statement and wait. Returns Status, Error and, for a query, Rows."""
    data = _client('redshift-data')
    statement = data.execute_statement(Sql=sql, **_target(secret), **extra)
    described = _finish(statement['Id'])
    outcome = {'Id': statement['Id'], 'Status': described.get('Status'), 'Error': described.get('Error')}
    if described.get('Status') == 'FINISHED' and described.get('HasResultSet'):
        if extra.get('ResultFormat') == 'CSV':
            result = data.get_statement_result_v2(Id=statement['Id'])
            outcome['Columns'] = [column['name'] for column in result.get('ColumnMetadata', [])]
            outcome['CSV'] = [record.get('CSVRecords', '') for record in result.get('Records', [])]
        else:
            result = data.get_statement_result(Id=statement['Id'])
            outcome['Columns'] = [column['name'] for column in result.get('ColumnMetadata', [])]
            outcome['Rows'] = records_to_rows(result)
    else:
        outcome['ResultRows'] = described.get('ResultRows')
    return outcome


def run_batch(sqls: list[str], secret: str | None = ADMIN_SECRET) -> dict[str, Any]:
    data = _client('redshift-data')
    statement = data.batch_execute_statement(Sqls=sqls, **_target(secret))
    described = _finish(statement['Id'])
    return {'Id': statement['Id'], 'Status': described.get('Status'), 'Error': described.get('Error'),
            'SubStatements': [{'Status': item.get('Status'), 'Error': item.get('Error')}
                              for item in described.get('SubStatements', [])]}


def _scalar(sql: str, secret: str | None = ADMIN_SECRET) -> Any:
    outcome = run_sql(sql, secret)
    rows = outcome.get('Rows') or []
    return rows[0][0] if rows and rows[0] else None


def _tables() -> set[str]:
    tables = _safe_call(lambda: _client('redshift-data').list_tables(
        SchemaPattern='public', **_target(ADMIN_SECRET))['Tables'], [])
    return {item['name'] for item in tables}


def _bucket_exists() -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=BUCKET) is not None, False)


def _object(key: str) -> str | None:
    return _safe_call(lambda: _client('s3').get_object(Bucket=BUCKET, Key=key)['Body'].read().decode())


def _usage_limits() -> list[dict[str, Any]]:
    arn = (_workgroup() or {}).get('workgroupArn')
    if not arn:
        return []
    return _safe_call(lambda: _client('redshift-serverless').list_usage_limits(resourceArn=arn)['usageLimits'], [])


def _snapshot() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('redshift-serverless').get_snapshot(snapshotName=SNAPSHOT)['snapshot'])


def _mv_totals() -> dict[tuple[str, str], float]:
    rows = run_sql(f'SELECT sale_date, store_id, total FROM {MV}').get('Rows') or []
    return {(str(day), store): round(float(total), 2) for day, store, total in rows}


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_workgroup() -> tuple[bool, str]:
    namespace = _namespace()
    if not namespace:
        return False, f'Create the {NAMESPACE} namespace.'
    if namespace.get('dbName') != DATABASE:
        return False, f'{NAMESPACE} should hold the {DATABASE} database.'
    if not _available():
        return False, f'{WORKGROUP} is not AVAILABLE yet.'
    if not _secret_arn(ADMIN_SECRET):
        return False, f'Store the admin credentials in {ADMIN_SECRET}.'
    return True, f'{WORKGROUP} is AVAILABLE on {NAMESPACE}, and the admin credentials are in {ADMIN_SECRET}.'


def _check_schema() -> tuple[bool, str]:
    missing = {'dim_store', 'dim_product', 'fact_sales', 'load_errors'} - _tables()
    if missing:
        return False, f'Create {", ".join(sorted(missing))}.'
    if _scalar('SELECT COUNT(*) FROM dim_store') != len(STORES):
        return False, f'dim_store should hold {len(STORES)} stores.'
    return True, 'The star schema exists: fact_sales, dim_store, dim_product, and load_errors.'


def _check_load() -> tuple[bool, str]:
    count = _scalar('SELECT COUNT(*) FROM fact_sales WHERE sale_date < ' + _literal(LATE_DAY.isoformat()))
    if count != ROW_COUNT:
        return False, f'fact_sales has {count} rows from the two staged files; expected {ROW_COUNT}.'
    errors = _scalar('SELECT COUNT(*) FROM load_errors')
    if not errors:
        return False, 'The bad row was not recorded in load_errors.'
    if not (_marked('stage-and-load') or {}).get('rolledBack'):
        return False, 'Run the load to see the failed all-or-nothing attempt first.'
    return True, f'{ROW_COUNT} sales loaded in one transaction, and the bad row is in load_errors.'


def _check_mv() -> tuple[bool, str]:
    marker = _marked('materialized-view') or {}
    if not marker.get('staleBeforeRefresh'):
        return False, f'Show that {MV} misses the late file before it is refreshed.'
    expected = daily_store_totals(sale_rows() + late_rows())
    if _mv_totals() != expected:
        return False, f'{MV} does not match fact_sales; refresh it.'
    return True, f'{MV} missed {LATE_DAY.isoformat()} until it was refreshed, and now matches fact_sales.'


def _check_analysis() -> tuple[bool, str]:
    marker = _marked('analyze-sales') or {}
    if not marker.get('matches'):
        return False, 'The rolling averages have not been checked yet.'
    return True, 'Every rolling seven-day average matches the arithmetic, and products are ranked per category.'


def _check_access() -> tuple[bool, str]:
    if not _secret_arn(ANALYST_SECRET):
        return False, f'Store the analyst credentials in {ANALYST_SECRET}.'
    view = run_sql(f'SELECT customer_email FROM {MASKED_VIEW} ORDER BY sale_id LIMIT 1', ANALYST_SECRET)
    if view.get('Status') != 'FINISHED':
        return False, f'{ANALYST_USER} cannot read {MASKED_VIEW}: {view.get("Error")}'
    email = (view.get('Rows') or [[None]])[0][0]
    if not email or '***@' not in str(email):
        return False, f'{MASKED_VIEW} does not mask the email.'
    table = run_sql('SELECT customer_email FROM fact_sales LIMIT 1', ANALYST_SECRET)
    if table.get('Status') == 'FINISHED':
        return False, f'{ANALYST_USER} can still read fact_sales.'
    return True, f'{ANALYST_USER} reads {MASKED_VIEW} ({email}) and is denied fact_sales.'


def _check_export() -> tuple[bool, str]:
    body = _object(f'{EXPORT_PREFIX}month=2026-10/part-000.csv')
    if not body:
        return False, 'Export the daily totals to S3.'
    rules = _safe_call(lambda: _client('s3').get_bucket_lifecycle_configuration(Bucket=BUCKET)['Rules'], [])
    prefixes = {(rule.get('Filter') or {}).get('Prefix') for rule in rules}
    if not {STAGE_PREFIX, EXPORT_PREFIX} <= prefixes:
        return False, 'Add lifecycle rules for the staging and export prefixes.'
    lines = [line for line in body.splitlines() if line.strip()]
    return True, f'{len(lines) - 1} daily totals exported, with lifecycle rules for staging and exports.'


def _check_cost() -> tuple[bool, str]:
    limits = _usage_limits()
    if not any(item.get('amount') == USAGE_LIMIT_RPU_HOURS and item.get('usageType') == 'serverless-compute'
               for item in limits):
        return False, f'Set a {USAGE_LIMIT_RPU_HOURS} RPU-hour usage limit on {WORKGROUP}.'
    if (_snapshot() or {}).get('status') != 'AVAILABLE':
        return False, f'Take the {SNAPSHOT} snapshot.'
    return True, f'{WORKGROUP} has a {USAGE_LIMIT_RPU_HOURS} RPU-hour monthly limit, and {SNAPSHOT} is AVAILABLE.'


CHECKS = {
    'create-workgroup': _check_workgroup,
    'design-schema': _check_schema,
    'stage-and-load': _check_load,
    'materialized-view': _check_mv,
    'analyze-sales': _check_analysis,
    'analyst-access': _check_access,
    'export-and-lifecycle': _check_export,
    'cost-and-recovery': _check_cost,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _needs_workgroup(step: str, command: str, started: float) -> dict[str, Any] | None:
    if _available():
        return None
    return _result(step, command, {}, False, f'Run the first step: {WORKGROUP} is not AVAILABLE.', started)


def step_create_workgroup() -> dict[str, Any]:
    started = time.perf_counter()
    rss = _client('redshift-serverless')
    secret_arn = _put_secret(ADMIN_SECRET, ADMIN_USER, ADMIN_PASSWORD)
    if not _namespace():
        rss.create_namespace(namespaceName=NAMESPACE, dbName=DATABASE, adminUsername=ADMIN_USER,
                             adminUserPassword=ADMIN_PASSWORD, tags=[{'key': 'Lab', 'value': LAB_KEY}])
    if not _workgroup():
        rss.create_workgroup(workgroupName=WORKGROUP, namespaceName=NAMESPACE, baseCapacity=BASE_CAPACITY,
                             tags=[{'key': 'Lab', 'value': LAB_KEY}])
    _wait(_available, WORKGROUP_WAIT_SECONDS, delay=3)
    workgroup = _workgroup() or {}
    verified, message = _check_workgroup()
    return _result('create-workgroup', WORKGROUP_COMMAND, {
        'Namespace': {key: (_namespace() or {}).get(key) for key in ('namespaceName', 'dbName', 'adminUsername', 'status')},
        'Workgroup': {key: workgroup.get(key) for key in ('workgroupName', 'status', 'baseCapacity', 'endpoint')},
        'AdminSecretArn': secret_arn}, verified, message, started)


def step_design_schema() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_workgroup('design-schema', SCHEMA_COMMAND, started)
    if blocked:
        return blocked
    redshift_attempt = run_sql(REDSHIFT_DDL[2])
    created = run_batch(PORTABLE_DDL + DIMENSION_SQL)
    verified, message = _check_schema()
    return _result('design-schema', SCHEMA_COMMAND, {
        'RedshiftDdlOnFloci': {'Status': redshift_attempt.get('Status'), 'Error': redshift_attempt.get('Error')},
        'PortableDdl': created, 'Tables': sorted(_tables())}, verified, message, started)


def step_stage_and_load() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_workgroup('stage-and-load', LOAD_COMMAND, started)
    if blocked:
        return blocked
    s3 = _client('s3')
    if not _bucket_exists():
        s3.create_bucket(Bucket=BUCKET)
    files = staged_files()
    for key, body in files.items():
        s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode(), ContentType='text/csv')
    s3.put_object(Bucket=BUCKET, Key=MANIFEST_KEY, Body=json.dumps(manifest(files), indent=2).encode())

    listed = json.loads(_object(MANIFEST_KEY) or '{}')
    good: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for entry in listed.get('entries', []):
        key = entry['url'].split(f's3://{BUCKET}/', 1)[1]
        rows, bad = parse_rows(_object(key) or '')
        good.extend(rows)
        errors.extend({**item, 'filename': entry['url']} for item in bad)

    run_sql('DELETE FROM fact_sales')
    run_sql('DELETE FROM load_errors')
    # All or nothing: the bad row goes in with the good ones, and the whole batch rolls back.
    raw_bad = errors[0]['raw_line'].split(',') if errors else []
    bad_insert = ('INSERT INTO fact_sales VALUES (' + ', '.join(
        [raw_bad[0], _literal(raw_bad[1]), _literal(raw_bad[2]), _literal(raw_bad[3]), _literal(raw_bad[4]),
         _literal(raw_bad[5]), raw_bad[6]]) + ')') if raw_bad else 'SELECT 1'
    first = run_batch([insert_sql(good), bad_insert])
    after_first = _scalar('SELECT COUNT(*) FROM fact_sales')
    error_sql = [
        'INSERT INTO load_errors (filename, line_number, raw_line, err_reason) VALUES '
        + ', '.join(f'({_literal(item["filename"])}, {item["line_number"]}, {_literal(item["raw_line"])}, '
                    f'{_literal(item["err_reason"][:200])})' for item in errors)
    ] if errors else []
    second = run_batch([insert_sql(good)] + error_sql)
    loaded = _scalar('SELECT COUNT(*) FROM fact_sales')
    _marker('stage-and-load', {'rolledBack': first.get('Status') == 'FAILED' and after_first == 0,
                               'loaded': loaded, 'errors': len(errors)})
    verified, message = _check_load()
    return _result('stage-and-load', LOAD_COMMAND, {
        'Manifest': listed,
        'FirstAttempt': {**first, 'RowsAfterRollback': after_first},
        'SecondAttempt': second,
        'Loaded': loaded,
        'LoadErrors': run_sql('SELECT filename, line_number, raw_line, err_reason FROM load_errors').get('Rows'),
    }, verified, message, started)


def step_materialized_view() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_workgroup('materialized-view', MV_COMMAND, started)
    if blocked:
        return blocked
    run_sql(f'DROP MATERIALIZED VIEW IF EXISTS {MV}')
    run_sql(f'DELETE FROM fact_sales WHERE sale_date >= {_literal(LATE_DAY.isoformat())}')
    created = run_sql(MV_SQL)
    late = late_rows()
    late_key = f'{STAGE_PREFIX}{LATE_DAY.isoformat()}-late.csv'
    _client('s3').put_object(Bucket=BUCKET, Key=late_key, Body=to_csv(late).encode(), ContentType='text/csv')
    rows, _ = parse_rows(_object(late_key) or '')
    loaded = run_batch([insert_sql(rows)])
    before = _mv_totals()
    late_day = LATE_DAY.isoformat()
    stale = not any(day == late_day for day, _ in before)
    refreshed = run_sql(f'REFRESH MATERIALIZED VIEW {MV}')
    after = _mv_totals()
    _marker('materialized-view', {'staleBeforeRefresh': stale})
    verified, message = _check_mv()
    return _result('materialized-view', MV_COMMAND, {
        'Create': {'Status': created.get('Status'), 'Error': created.get('Error')},
        'LateFile': {'key': late_key, 'rows': len(rows), 'load': loaded.get('Status')},
        'LateDayInViewBeforeRefresh': not stale,
        'Refresh': refreshed.get('Status'),
        'LateDayAfterRefresh': {store: total for (day, store), total in sorted(after.items()) if day == late_day},
    }, verified, message, started)


def step_analyze_sales() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_workgroup('analyze-sales', ANALYZE_COMMAND, started)
    if blocked:
        return blocked
    rolling = run_sql(ROLLING_SQL)
    ranked = run_sql(TOP_PRODUCTS_SQL)
    totals = daily_store_totals(sale_rows() + late_rows())
    expected = {store: rolling_average(totals, store) for store, _, _ in STORES}
    mismatches = []
    for store, day, _total, average in rolling.get('Rows') or []:
        want = expected.get(store, {}).get(str(day))
        if want is None or abs(float(average) - want) > 0.01:
            mismatches.append({'store': store, 'day': str(day), 'got': average, 'expected': want})
    matches = rolling.get('Status') == 'FINISHED' and bool(rolling.get('Rows')) and not mismatches
    _marker('analyze-sales', {'matches': matches})
    verified, message = _check_analysis()
    sample = [row for row in rolling.get('Rows') or [] if row[0] == 'S1'][-3:]
    return _result('analyze-sales', ANALYZE_COMMAND, {
        'StatementId': rolling.get('Id'), 'Status': rolling.get('Status'),
        'RollingAverageS1LastDays': [dict(zip(rolling.get('Columns', []), row)) for row in sample],
        'Mismatches': mismatches,
        'ProductRanks': [dict(zip(ranked.get('Columns', []), row)) for row in ranked.get('Rows') or []],
    }, verified, message, started)


def step_analyst_access() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_workgroup('analyst-access', ACCESS_COMMAND, started)
    if blocked:
        return blocked
    exists = _scalar(f"SELECT COUNT(*) FROM pg_user WHERE usename = '{ANALYST_USER}'")
    statements = [] if exists else [f"CREATE USER {ANALYST_USER} PASSWORD '{ANALYST_PASSWORD}'"]
    statements += [MASKED_VIEW_SQL, f'REVOKE ALL ON fact_sales FROM {ANALYST_USER}',
                   f'GRANT SELECT ON {MASKED_VIEW} TO {ANALYST_USER}']
    setup = run_batch(statements)
    _put_secret(ANALYST_SECRET, ANALYST_USER, ANALYST_PASSWORD)
    view = run_sql(f'SELECT sale_id, store_id, amount, customer_email FROM {MASKED_VIEW} ORDER BY sale_id LIMIT 3',
                   ANALYST_SECRET)
    table = run_sql('SELECT customer_email FROM fact_sales LIMIT 1', ANALYST_SECRET)
    verified, message = _check_access()
    return _result('analyst-access', ACCESS_COMMAND, {
        'Setup': setup,
        'AnalystReadsView': {'Status': view.get('Status'),
                             'Rows': [dict(zip(view.get('Columns', []), row)) for row in view.get('Rows') or []]},
        'AnalystReadsTable': {'Status': table.get('Status'), 'Error': table.get('Error')},
    }, verified, message, started)


LIFECYCLE = {'Rules': [
    {'ID': 'expire-loaded-staging', 'Status': 'Enabled', 'Filter': {'Prefix': STAGE_PREFIX},
     'Expiration': {'Days': 30}},
    {'ID': 'archive-exports', 'Status': 'Enabled', 'Filter': {'Prefix': EXPORT_PREFIX},
     'Transitions': [{'Days': 90, 'StorageClass': 'GLACIER_IR'}]},
]}


def step_export_and_lifecycle() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_workgroup('export-and-lifecycle', EXPORT_COMMAND, started)
    if blocked:
        return blocked
    unload = run_sql(f"UNLOAD ('SELECT * FROM {MV}') TO 's3://{BUCKET}/{EXPORT_PREFIX}' IAM_ROLE default FORMAT PARQUET")
    exported = run_sql(f'SELECT sale_date, store_id, region, total, sales FROM {MV} ORDER BY sale_date, store_id',
                       ResultFormat='CSV')
    body = '\n'.join([','.join(exported.get('Columns') or [])] + (exported.get('CSV') or [])) + '\n'
    key = f'{EXPORT_PREFIX}month=2026-10/part-000.csv'
    s3 = _client('s3')
    s3.put_object(Bucket=BUCKET, Key=key, Body=body.encode(), ContentType='text/csv')
    s3.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration=LIFECYCLE)
    verified, message = _check_export()
    return _result('export-and-lifecycle', EXPORT_COMMAND, {
        'UnloadOnFloci': {'Status': unload.get('Status'), 'Error': unload.get('Error')},
        'Exported': f's3://{BUCKET}/{key}', 'FirstLines': body.splitlines()[:4],
        'Lifecycle': LIFECYCLE}, verified, message, started)


def step_cost_and_recovery() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_workgroup('cost-and-recovery', COST_COMMAND, started)
    if blocked:
        return blocked
    rss = _client('redshift-serverless')
    arn = (_workgroup() or {})['workgroupArn']
    if not any(item.get('amount') == USAGE_LIMIT_RPU_HOURS for item in _usage_limits()):
        rss.create_usage_limit(resourceArn=arn, usageType='serverless-compute', amount=USAGE_LIMIT_RPU_HOURS,
                               period='monthly', breachAction='emit-metric')
    if not _snapshot():
        rss.create_snapshot(namespaceName=NAMESPACE, snapshotName=SNAPSHOT, retentionPeriod=7)
    _wait(lambda: (_snapshot() or {}).get('status') == 'AVAILABLE', 60, delay=2)
    snapshot = _snapshot() or {}
    verified, message = _check_cost()
    return _result('cost-and-recovery', COST_COMMAND, {
        'UsageLimits': [{key: item.get(key) for key in ('usageType', 'amount', 'period', 'breachAction')}
                        for item in _usage_limits()],
        'Snapshot': {key: snapshot.get(key) for key in ('snapshotName', 'status', 'namespaceName', 'snapshotRetentionPeriod')},
    }, verified, message, started)


RUNNERS = {
    'create-workgroup': step_create_workgroup,
    'design-schema': step_design_schema,
    'stage-and-load': step_stage_and_load,
    'materialized-view': step_materialized_view,
    'analyze-sales': step_analyze_sales,
    'analyst-access': step_analyst_access,
    'export-and-lifecycle': step_export_and_lifecycle,
    'cost-and-recovery': step_cost_and_recovery,
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
    ready = _safe_call(_available, False)
    for step in STEP_KEYS:
        verified = _safe(CHECKS[step]) if ready or step == 'create-workgroup' else False
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
    """Remove the usage limits, snapshot, workgroup, namespace, secrets, and bucket."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code == 'ResourceNotFoundException':
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    rss = _client('redshift-serverless')
    for item in _usage_limits():
        attempt(f'usage-limit:{item["usageLimitId"]}',
                lambda item=item: rss.delete_usage_limit(usageLimitId=item['usageLimitId']))
    if _snapshot():
        attempt(f'snapshot:{SNAPSHOT}', lambda: rss.delete_snapshot(snapshotName=SNAPSHOT))
    if _workgroup():
        attempt(f'workgroup:{WORKGROUP}', lambda: rss.delete_workgroup(workgroupName=WORKGROUP))
        _wait(lambda: _workgroup() is None, 60, delay=2)
    if _namespace():
        attempt(f'namespace:{NAMESPACE}', lambda: rss.delete_namespace(namespaceName=NAMESPACE))
    sm = _client('secretsmanager')
    for name in (ADMIN_SECRET, ANALYST_SECRET):
        if _secret_arn(name):
            attempt(f'secret:{name}', lambda name=name: sm.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True))
    s3 = _client('s3')
    if _bucket_exists():
        def empty_and_delete() -> None:
            for item in s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
                s3.delete_object(Bucket=BUCKET, Key=item['Key'])
            s3.delete_bucket(Bucket=BUCKET)
        attempt(f'bucket:{BUCKET}', empty_and_delete)

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws redshift-serverless delete-usage-limit --usage-limit-id <id>\n'
            f'aws redshift-serverless delete-snapshot --snapshot-name {SNAPSHOT}\n'
            f'aws redshift-serverless delete-workgroup --workgroup-name {WORKGROUP}\n'
            f'aws redshift-serverless delete-namespace --namespace-name {NAMESPACE}\n'
            f'aws secretsmanager delete-secret --secret-id {ADMIN_SECRET} --force-delete-without-recovery\n'
            f'aws secretsmanager delete-secret --secret-id {ANALYST_SECRET} --force-delete-without-recovery\n'
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
            else 'The usage limit, snapshot, workgroup, namespace, secrets, and bucket are gone.',
        },
    }
