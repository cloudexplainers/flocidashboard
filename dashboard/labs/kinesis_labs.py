"""Streaming data lake lab: a Kinesis data stream, a Firehose delivery stream into partitioned S3 prefixes, a Glue Data
Catalog table, an Athena workgroup and query, and a Parquet copy of the data.

Floci 2.3.0 runs the pipeline for real: Kinesis stores and replays records, Firehose reads the stream and writes
batches to S3 under the evaluated `!{timestamp:...}` prefix, and Athena runs SQL through a DuckDB sidecar over the Glue
tables (it reads Hive-style `dt=` folders itself). What it skips is checked by the lab: Firehose accepts a prefix with
expressions and no error prefix, the Glue crawler finishes without creating tables, Athena ignores the workgroup's
result location and scan limit, and Athena DDL (CTAS, MSCK REPAIR) is unsupported, so the Parquet step runs the
DuckDB equivalent of the CTAS it teaches.
"""

from __future__ import annotations

import json
import secrets
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'kinesis'
LAB_KEY = 'streaming-data-lake'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

LAKE_BUCKET = 'lab-clickstream-lake'
RESULTS_BUCKET = 'lab-clickstream-athena-results'
RESULTS_EXPIRY_DAYS = 7
STREAM = 'lab-clickstream'
STREAM_ARN = f'arn:aws:kinesis:{REGION}:{ACCOUNT}:stream/{STREAM}'
RETENTION_HOURS = 48
DELIVERY_STREAM = 'lab-clickstream-to-s3'
ROLE_NAME = 'lab-clickstream-firehose-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
ROLE_POLICY = 'lab-clickstream-delivery'
RAW_PREFIX = 'raw/clicks/'
FIREHOSE_PREFIX = RAW_PREFIX + 'dt=!{timestamp:yyyy-MM-dd}/'
ERROR_PREFIX = 'errors/!{firehose:error-output-type}/dt=!{timestamp:yyyy-MM-dd}/'
BUFFER_MB = 1
BUFFER_SECONDS = 0
CURATED_PREFIX = 'curated/clicks/'
DATABASE = 'lab_clickstream'
RAW_TABLE = 'raw_clicks'
CURATED_TABLE = 'curated_clicks'
WORKGROUP = 'lab-clickstream'
SCAN_CUTOFF_BYTES = 100 * 1024 * 1024
LANDING_TIMEOUT_SECONDS = 90

PAGES = [('/home', 8), ('/product/trail-shoes', 7), ('/cart', 5), ('/checkout', 4)]
EVENT_COUNT = sum(count for _, count in PAGES)
COLUMNS = [
    ('event_id', 'string'), ('batch_id', 'string'), ('user_id', 'string'), ('page', 'string'),
    ('action', 'string'), ('duration_ms', 'int'), ('ts', 'string'),
]

FIREHOSE_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'firehose.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
FIREHOSE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'ReadTheStream', 'Effect': 'Allow',
         'Action': ['kinesis:DescribeStream', 'kinesis:DescribeStreamSummary', 'kinesis:GetShardIterator',
                    'kinesis:GetRecords', 'kinesis:ListShards'],
         'Resource': STREAM_ARN},
        {'Sid': 'WriteTheLake', 'Effect': 'Allow',
         'Action': ['s3:AbortMultipartUpload', 's3:GetBucketLocation', 's3:GetObject', 's3:ListBucket',
                    's3:ListBucketMultipartUploads', 's3:PutObject'],
         'Resource': [f'arn:aws:s3:::{LAKE_BUCKET}', f'arn:aws:s3:::{LAKE_BUCKET}/*']},
    ],
}


def _projection_parameters() -> dict[str, str]:
    return {
        'classification': 'json',
        'projection.enabled': 'true',
        'projection.dt.type': 'date',
        'projection.dt.format': 'yyyy-MM-dd',
        'projection.dt.range': '2026-01-01,NOW',
        'projection.dt.interval': '1',
        'projection.dt.interval.unit': 'DAYS',
        'storage.location.template': f's3://{LAKE_BUCKET}/{RAW_PREFIX}dt=${{dt}}/',
    }


def _table_input(name: str, prefix: str, parquet: bool) -> dict[str, Any]:
    if parquet:
        formats = {
            'InputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat',
            'OutputFormat': 'org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat',
            'SerdeInfo': {'SerializationLibrary': 'org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe'},
        }
        parameters = {'classification': 'parquet', 'parquet.compression': 'SNAPPY'}
    else:
        formats = {
            'InputFormat': 'org.apache.hadoop.mapred.TextInputFormat',
            'OutputFormat': 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
            'SerdeInfo': {'SerializationLibrary': 'org.openx.data.jsonserde.JsonSerDe'},
        }
        parameters = _projection_parameters()
    return {
        'Name': name,
        'TableType': 'EXTERNAL_TABLE',
        'Parameters': parameters,
        'PartitionKeys': [{'Name': 'dt', 'Type': 'string'}],
        'StorageDescriptor': {
            'Columns': [{'Name': column, 'Type': kind} for column, kind in COLUMNS],
            'Location': f's3://{LAKE_BUCKET}/{prefix}',
            **formats,
        },
    }


def _ddl(name: str, prefix: str) -> str:
    columns = ',\n  '.join(f'{column} {kind}' for column, kind in COLUMNS)
    return (
        f'CREATE EXTERNAL TABLE {name} (\n  {columns}\n)\nPARTITIONED BY (dt string)\n'
        "ROW FORMAT SERDE 'org.openx.data.jsonserde.JsonSerDe'\n"
        f"LOCATION 's3://{LAKE_BUCKET}/{prefix}'\n"
        "TBLPROPERTIES ('projection.enabled'='true', 'projection.dt.type'='date', 'projection.dt.format'='yyyy-MM-dd',\n"
        "  'projection.dt.range'='2026-01-01,NOW', 'projection.dt.interval'='1', 'projection.dt.interval.unit'='DAYS',\n"
        f"  'storage.location.template'='s3://{LAKE_BUCKET}/{prefix}dt=${{dt}}/')"
    )


CTAS_SQL = (
    f"CREATE TABLE {CURATED_TABLE}\nWITH (format = 'PARQUET', write_compression = 'SNAPPY',\n"
    f"      external_location = 's3://{LAKE_BUCKET}/{CURATED_PREFIX}', partitioned_by = ARRAY['dt'])\n"
    f'AS SELECT {", ".join(column for column, _ in COLUMNS)}, dt FROM {RAW_TABLE}'
)
FLOCI_PARQUET_SQL = (
    f'COPY (SELECT {", ".join(column for column, _ in COLUMNS)}, dt FROM {RAW_TABLE}) '
    f"TO 's3://{LAKE_BUCKET}/{CURATED_PREFIX}' (FORMAT PARQUET, COMPRESSION SNAPPY, PARTITION_BY (dt), "
    'OVERWRITE_OR_IGNORE)'
)


def _page_query(table: str, batch_id: str, dt: str | None) -> str:
    where = f"batch_id = '{batch_id}'" + (f" AND dt = '{dt}'" if dt else '')
    return f'SELECT page, count(*) AS views FROM {table} WHERE {where} GROUP BY page ORDER BY page'


BUCKETS_COMMAND = (
    f'aws s3api create-bucket --bucket {LAKE_BUCKET}\n'
    f'aws s3api put-public-access-block --bucket {LAKE_BUCKET} --public-access-block-configuration '
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true\n'
    f'aws s3api put-bucket-encryption --bucket {LAKE_BUCKET} --server-side-encryption-configuration '
    '\'{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}\'\n'
    f'aws s3api create-bucket --bucket {RESULTS_BUCKET}\n'
    f'aws s3api put-bucket-lifecycle-configuration --bucket {RESULTS_BUCKET} --lifecycle-configuration '
    f'\'{{"Rules":[{{"ID":"expire-query-results","Status":"Enabled","Filter":{{}},"Expiration":{{"Days":{RESULTS_EXPIRY_DAYS}}}}}]}}\''
)
STREAM_COMMAND = (
    f'aws kinesis create-stream --stream-name {STREAM} --stream-mode-details StreamMode=ON_DEMAND\n'
    f'aws kinesis increase-stream-retention-period --stream-name {STREAM} --retention-period-hours {RETENTION_HOURS}\n'
    f'aws kinesis describe-stream-summary --stream-name {STREAM}'
)
DELIVERY_COMMAND = (
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://firehose-trust.json\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} --policy-document file://delivery.json\n'
    f'aws firehose create-delivery-stream --delivery-stream-name {DELIVERY_STREAM} '
    '--delivery-stream-type KinesisStreamAsSource '
    f'--kinesis-stream-source-configuration KinesisStreamARN={STREAM_ARN},RoleARN={ROLE_ARN} '
    f"--extended-s3-destination-configuration 'RoleARN={ROLE_ARN},BucketARN=arn:aws:s3:::{LAKE_BUCKET},"
    f'Prefix={FIREHOSE_PREFIX},ErrorOutputPrefix={ERROR_PREFIX},'
    f"BufferingHints={{SizeInMBs={BUFFER_MB},IntervalInSeconds={BUFFER_SECONDS}}},CompressionFormat=UNCOMPRESSED'"
)
PRODUCE_COMMAND = (
    f'aws kinesis put-records --stream-name {STREAM} --records file://clicks.json   '
    f'# {EVENT_COUNT} events, PartitionKey=user_id\n'
    f'aws kinesis get-shard-iterator --stream-name {STREAM} --shard-id shardId-000000000000 '
    '--shard-iterator-type TRIM_HORIZON\n'
    'aws kinesis get-records --shard-iterator <iterator>   # replay from the start of the retention window'
)
LANDING_COMMAND = (
    f'aws s3 ls s3://{LAKE_BUCKET}/{RAW_PREFIX} --recursive\n'
    f'aws s3 cp s3://{LAKE_BUCKET}/{RAW_PREFIX}dt=<today>/<object> - | head'
)
CATALOG_COMMAND = (
    f'aws glue create-database --database-input Name={DATABASE}\n'
    f'aws athena start-query-execution --work-group primary --query-string file://raw_clicks.sql\n'
    f'# raw_clicks.sql:\n# ' + _ddl(RAW_TABLE, RAW_PREFIX).replace('\n', '\n# ')
)
WORKGROUP_COMMAND = (
    f'aws athena create-work-group --name {WORKGROUP} --configuration '
    f"'ResultConfiguration={{OutputLocation=s3://{RESULTS_BUCKET}/results/}},EnforceWorkGroupConfiguration=true,"
    f"PublishCloudWatchMetricsEnabled=true,BytesScannedCutoffPerQuery={SCAN_CUTOFF_BYTES}'"
)
QUERY_COMMAND = (
    f'aws athena start-query-execution --work-group {WORKGROUP} --query-execution-context Database={DATABASE} '
    f'--query-string "{_page_query(RAW_TABLE, "<batch-id>", "<today>")}"\n'
    'aws athena get-query-execution --query-execution-id <id>   # Statistics.DataScannedInBytes\n'
    'aws athena get-query-results --query-execution-id <id>'
)
PARQUET_COMMAND = (
    f'aws athena start-query-execution --work-group {WORKGROUP} --query-execution-context Database={DATABASE} '
    '--query-string file://ctas.sql\n'
    '# ctas.sql:\n# ' + CTAS_SQL.replace('\n', '\n# ') + '\n'
    f'aws athena start-query-execution --work-group {WORKGROUP} --query-execution-context Database={DATABASE} '
    f'--query-string "{_page_query(CURATED_TABLE, "<batch-id>", "<today>")}"'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Stream clickstream events into an S3 data lake and query them with Athena',
    'description': (
        'Build a serverless analytics pipeline: lake and query-result buckets, an on-demand Kinesis data stream, a '
        'Firehose delivery stream that lands events in date-partitioned S3 prefixes, a Glue Data Catalog table with '
        'partition projection, an Athena workgroup with a scan limit, a query that counts page views, and a Parquet '
        'copy of the data that queries scan far less of.'
    ),
    'steps': [
        {
            'key': 'lake-buckets',
            'title': 'Create the data lake and query-result buckets',
            'command': BUCKETS_COMMAND,
            'explanation': (
                'A data lake keeps raw data in S3 in open formats, separate from the engines that read it: Athena, '
                'EMR, Redshift Spectrum, and SageMaker can all query the same objects. Block Public Access and default '
                'encryption are the baseline for any lake bucket. Athena writes every query\'s results to S3, so a '
                f'second bucket holds them, with a lifecycle rule that deletes results after {RESULTS_EXPIRY_DAYS} days. '
                'How Floci emulates this: the buckets, public access block, and encryption setting are stored; '
                'lifecycle rules are stored but never run, so old results would stay.'
            ),
        },
        {
            'key': 'data-stream',
            'title': 'Create an on-demand Kinesis data stream',
            'command': STREAM_COMMAND,
            'explanation': (
                'Kinesis Data Streams is a durable, ordered log: producers put records with a partition key, the key '
                'picks a shard, and records in a shard stay in order. Consumers (Lambda, the KCL, Firehose, Managed '
                'Flink) read at their own pace and can replay anything in the retention window, 24 hours by default '
                'and up to 365 days. Provisioned mode bills per shard (1 MB/s or 1,000 records/s in and 2 MB/s out '
                'each); on-demand mode scales shards for you, which suits unpredictable traffic. How Floci emulates '
                'this: the stream, its mode and its retention are real enough for producers and consumers, but an '
                'on-demand stream gets one shard (AWS starts with four) and no throughput limits apply.'
            ),
        },
        {
            'key': 'delivery-stream',
            'title': 'Deliver the stream to date-partitioned S3 prefixes with Firehose',
            'command': DELIVERY_COMMAND,
            'explanation': (
                'Amazon Data Firehose is the no-code way to load streaming data: it reads the stream (or takes direct '
                'puts), buffers by size or time, optionally transforms with Lambda or converts JSON to Parquet or ORC, '
                f'and writes to S3, Redshift, OpenSearch, or HTTP endpoints. The prefix {FIREHOSE_PREFIX} puts each '
                'day in its own dt= folder, the Hive partition layout Athena and Glue understand, and AWS requires an '
                'error prefix whenever the prefix uses expressions. Zero-second buffering delivers within seconds; '
                'the default is 300 seconds or 5 MB, and bigger buffers mean fewer, larger objects. On the exam: Data '
                'Streams when you need custom consumers, ordering, or replay; Firehose when you only need to land data '
                'in a destination, near real time and fully managed. How Floci emulates this: Floci really reads the '
                'stream and writes batches to S3 under the evaluated prefix. It accepts an expression prefix without '
                'an error prefix and does not use the role, so the step checks both itself.'
            ),
        },
        {
            'key': 'produce-events',
            'title': 'Put clickstream events on the stream and replay them',
            'command': PRODUCE_COMMAND,
            'explanation': (
                f'The producer sends {EVENT_COUNT} click events in one PutRecords call with the user ID as partition '
                'key, so each user\'s clicks stay in order on one shard. PutRecords can partly fail (throttling on a '
                'hot shard): check FailedRecordCount and retry the failed entries. Reading from TRIM_HORIZON replays '
                'the stream from the oldest retained record, which is what lets a new consumer catch up or a broken one '
                'reprocess. Each run tags its events with a batch ID so later steps can find them. How Floci emulates '
                'this: the records are stored and replayed for real.'
            ),
        },
        {
            'key': 'land-in-s3',
            'title': 'Watch Firehose land the events in the lake',
            'command': LANDING_COMMAND,
            'explanation': (
                'Firehose writes newline-delimited JSON objects named after the delivery stream and the time, under '
                'the dt= folder for the day they arrived. Records that fail delivery or transformation go to the error '
                'prefix with the reason, so nothing is silently dropped. How Floci emulates this: the objects are '
                f'real; the step waits up to {LANDING_TIMEOUT_SECONDS} seconds and counts this run\'s events in them.'
            ),
        },
        {
            'key': 'glue-catalog',
            'title': 'Catalog the raw data in Glue with partition projection',
            'command': CATALOG_COMMAND,
            'explanation': (
                'Athena reads tables from the Glue Data Catalog: a table is only a schema and an S3 location, the data '
                'stays where it is. A Glue crawler can infer the schema and partitions for you. New dt= folders appear '
                'every day, so either add partitions (crawler, MSCK REPAIR TABLE, ALTER TABLE ADD PARTITION) or use '
                'partition projection, as here, which computes them from the table properties with no catalog '
                'updates. Lake Formation adds fine-grained permissions on top of the catalog. How Floci emulates this: '
                'the database and table are stored, and Athena reads them. Floci\'s crawler finishes without creating '
                'tables and its Athena does not run this DDL, so the step creates the table through the Glue API. '
                'Floci lists the dt= folders itself and ignores the projection properties; the step checks them.'
            ),
        },
        {
            'key': 'athena-workgroup',
            'title': 'Create an Athena workgroup with a scan limit',
            'command': WORKGROUP_COMMAND,
            'explanation': (
                'Athena is serverless SQL over S3, billed per terabyte scanned ($5 per TB, 10 MB minimum per query). '
                'A workgroup sets where results go, can force that setting on every query, publishes CloudWatch '
                f'metrics, and cancels any query that would scan more than a limit ({SCAN_CUTOFF_BYTES // 1048576} MB '
                'here), which caps the cost of a mistake. How Floci emulates this: the workgroup is stored and queries '
                'run in it, but Floci ignores its result location (results go to Floci\'s own bucket unless a query names one), reports 0 bytes scanned, and never applies '
                'the limit. The step checks the configuration.'
            ),
        },
        {
            'key': 'query-clicks',
            'title': 'Count page views with Athena',
            'command': QUERY_COMMAND,
            'explanation': (
                'The query counts this run\'s page views. The dt filter is partition pruning: Athena reads only that '
                'day\'s folder, so a year of data costs the same to query for one day as a single day does. How Floci '
                'emulates this: Floci runs the SQL with DuckDB over the real objects, so the counts are real; '
                'DataScannedInBytes is always 0, so the step compares the counts with what it produced.'
            ),
        },
        {
            'key': 'convert-parquet',
            'title': 'Convert the data to Parquet and query the curated table',
            'command': PARQUET_COMMAND,
            'explanation': (
                'Columnar formats (Parquet, ORC) store each column together and compress it, so a query that reads '
                'two columns scans a fraction of the data, and Athena costs fall with it; the exam\'s answer to '
                '"reduce Athena cost" is Parquet plus partitioning plus compression. A CTAS query writes the curated '
                'copy in one step; Firehose can also convert JSON to Parquet on the way in using a Glue table as the '
                'schema, and Glue ETL jobs do it in batches. How Floci emulates this: Floci\'s Athena speaks DuckDB '
                'SQL and does not support CTAS, so the step runs the DuckDB COPY that writes the same partitioned, '
                'Snappy-compressed Parquet, then catalogs it through the Glue API and queries it through Athena. '
                'Firehose\'s Parquet conversion is stored but fails on Floci when signature validation is on.'
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


def _missing(exc: ClientError) -> bool:
    code = _code(exc)
    return 'NotFound' in code or code in {'NoSuchBucket', 'NoSuchEntity', 'EntityNotFoundException',
                                          'NoSuchLifecycleConfiguration', '404'}


def _marker(step: str, value: Any = True) -> None:
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


# ── Lookups ──────────────────────────────────────────────────────────────────

def _bucket_exists(bucket: str) -> bool:
    try:
        _client('s3').head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        if _missing(exc) or _code(exc) in {'404', 'NotFound'}:
            return False
        raise


def _optional(call, default=None):
    try:
        return call()
    except ClientError as exc:
        if _missing(exc) or 'NotFound' in _code(exc) or _code(exc).startswith('NoSuch'):
            return default
        raise


def _stream() -> dict[str, Any] | None:
    return _optional(lambda: _client('kinesis').describe_stream_summary(StreamName=STREAM)['StreamDescriptionSummary'])


def _delivery_stream() -> dict[str, Any] | None:
    return _optional(lambda: _client('firehose').describe_delivery_stream(
        DeliveryStreamName=DELIVERY_STREAM)['DeliveryStreamDescription'])


def _s3_destination(description: dict[str, Any] | None) -> dict[str, Any]:
    destinations = (description or {}).get('Destinations') or [{}]
    return destinations[0].get('ExtendedS3DestinationDescription') or {}


def _role_policy() -> dict[str, Any] | None:
    document = _optional(lambda: _client('iam').get_role_policy(
        RoleName=ROLE_NAME, PolicyName=ROLE_POLICY)['PolicyDocument'])
    if isinstance(document, str):
        document = json.loads(document)
    return document


def _table(name: str) -> dict[str, Any] | None:
    return _optional(lambda: _client('glue').get_table(DatabaseName=DATABASE, Name=name)['Table'])


def _workgroup() -> dict[str, Any] | None:
    try:
        return _client('athena').get_work_group(WorkGroup=WORKGROUP)['WorkGroup']
    except ClientError as exc:
        if _code(exc) == 'InvalidRequestException' or _missing(exc):
            return None
        raise


def _objects(prefix: str) -> list[dict[str, Any]]:
    s3 = _client('s3')
    objects: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {'Bucket': LAKE_BUCKET, 'Prefix': prefix}
    for _ in range(50):
        page = s3.list_objects_v2(**kwargs)
        objects.extend(page.get('Contents', []))
        if not page.get('IsTruncated') or not page.get('NextContinuationToken'):
            break
        kwargs['ContinuationToken'] = page['NextContinuationToken']
    return objects


def _landed(batch_id: str) -> dict[str, Any]:
    """Count this batch's events in the raw objects, by dt folder."""
    s3 = _client('s3')
    by_dt: Counter = Counter()
    keys = []
    for item in _objects(RAW_PREFIX):
        body = s3.get_object(Bucket=LAKE_BUCKET, Key=item['Key'])['Body'].read().decode('utf-8', 'replace')
        found = 0
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict) and event.get('batch_id') == batch_id:
                found += 1
        if found:
            folder = item['Key'][len(RAW_PREFIX):].split('/', 1)[0]
            by_dt[folder.removeprefix('dt=')] += found
            keys.append(item['Key'])
    return {'count': sum(by_dt.values()), 'by_dt': dict(by_dt), 'keys': keys}


def _athena(sql: str, workgroup: str | None = None) -> dict[str, Any]:
    athena = _client('athena')
    kwargs: dict[str, Any] = {'QueryString': sql, 'QueryExecutionContext': {'Database': DATABASE}}
    if workgroup:
        kwargs['WorkGroup'] = workgroup
    else:
        kwargs['ResultConfiguration'] = {'OutputLocation': f's3://{RESULTS_BUCKET}/results/'}
    query_id = athena.start_query_execution(**kwargs)['QueryExecutionId']
    execution: dict[str, Any] = {}
    for _ in range(60):
        execution = athena.get_query_execution(QueryExecutionId=query_id)['QueryExecution']
        if execution.get('Status', {}).get('State') in {'SUCCEEDED', 'FAILED', 'CANCELLED'}:
            break
        time.sleep(1)
    state = execution.get('Status', {}).get('State')
    rows: list[list[str]] = []
    if state == 'SUCCEEDED':
        result = athena.get_query_results(QueryExecutionId=query_id)['ResultSet']['Rows']
        rows = [[cell.get('VarCharValue', '') for cell in row.get('Data', [])] for row in result]
    return {
        'QueryExecutionId': query_id,
        'State': state,
        'StateChangeReason': execution.get('Status', {}).get('StateChangeReason'),
        'OutputLocation': (execution.get('ResultConfiguration') or {}).get('OutputLocation'),
        'DataScannedInBytes': (execution.get('Statistics') or {}).get('DataScannedInBytes'),
        'Rows': rows,
    }


def _page_counts(rows: list[list[str]]) -> dict[str, int]:
    counts = {}
    for row in rows[1:] if rows and rows[0][:1] == ['page'] else rows:
        if len(row) >= 2 and row[0]:
            try:
                counts[row[0]] = int(float(row[1]))
            except ValueError:
                continue
    return counts


def _expected_counts() -> dict[str, int]:
    return dict(PAGES)


def _events(batch_id: str) -> list[dict[str, Any]]:
    events, number = [], 0
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    for page, count in PAGES:
        for index in range(count):
            number += 1
            events.append({
                'event_id': f'{batch_id}-{number:03d}', 'batch_id': batch_id, 'user_id': f'user-{number % 6 + 1}',
                'page': page, 'action': 'view', 'duration_ms': 120 + 15 * index, 'ts': stamp,
            })
    return events


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_buckets() -> tuple[bool, str]:
    s3 = _client('s3')
    for bucket in (LAKE_BUCKET, RESULTS_BUCKET):
        if not _bucket_exists(bucket):
            return False, f'Bucket {bucket} was not found.'
    block = _optional(lambda: s3.get_public_access_block(Bucket=LAKE_BUCKET)['PublicAccessBlockConfiguration'], {})
    if not all(block.get(key) for key in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')):
        return False, f'{LAKE_BUCKET} must block all public access.'
    encryption = _optional(lambda: s3.get_bucket_encryption(Bucket=LAKE_BUCKET)['ServerSideEncryptionConfiguration'], {})
    if not (encryption or {}).get('Rules'):
        return False, f'{LAKE_BUCKET} has no default encryption.'
    rules = _optional(lambda: s3.get_bucket_lifecycle_configuration(Bucket=RESULTS_BUCKET)['Rules'], []) or []
    if not any(rule.get('Status') == 'Enabled' and (rule.get('Expiration') or {}).get('Days') == RESULTS_EXPIRY_DAYS
               for rule in rules):
        return False, f'{RESULTS_BUCKET} needs a rule that expires results after {RESULTS_EXPIRY_DAYS} days.'
    return True, (f'{LAKE_BUCKET} blocks public access and encrypts by default; {RESULTS_BUCKET} expires results after '
                  f'{RESULTS_EXPIRY_DAYS} days (Floci stores lifecycle rules but never runs them).')


def _check_stream() -> tuple[bool, str]:
    stream = _stream()
    if not stream:
        return False, f'Stream {STREAM} was not found.'
    if stream.get('StreamStatus') != 'ACTIVE':
        return False, f'{STREAM} is {stream.get("StreamStatus")}.'
    mode = (stream.get('StreamModeDetails') or {}).get('StreamMode')
    if mode != 'ON_DEMAND':
        return False, f'{STREAM} is {mode}; use on-demand mode.'
    if stream.get('RetentionPeriodHours') != RETENTION_HOURS:
        return False, f'{STREAM} keeps records {stream.get("RetentionPeriodHours")} hours, not {RETENTION_HOURS}.'
    return True, (f'{STREAM} is on-demand with {stream.get("OpenShardCount")} open shard(s) and {RETENTION_HOURS}-hour '
                  'retention.')


def _check_delivery() -> tuple[bool, str]:
    description = _delivery_stream()
    if not description:
        return False, f'Delivery stream {DELIVERY_STREAM} was not found.'
    if description.get('DeliveryStreamStatus') != 'ACTIVE':
        return False, f'{DELIVERY_STREAM} is {description.get("DeliveryStreamStatus")}.'
    source = ((description.get('Source') or {}).get('KinesisStreamSourceDescription') or {})
    if description.get('DeliveryStreamType') != 'KinesisStreamAsSource' or source.get('KinesisStreamARN') != STREAM_ARN:
        return False, f'{DELIVERY_STREAM} must read from {STREAM}.'
    destination = _s3_destination(description)
    if destination.get('BucketARN') != f'arn:aws:s3:::{LAKE_BUCKET}':
        return False, f'{DELIVERY_STREAM} must write to {LAKE_BUCKET}.'
    prefix = destination.get('Prefix') or ''
    if not prefix.startswith(RAW_PREFIX + 'dt=') or '!{timestamp:yyyy-MM-dd}' not in prefix:
        return False, f'The prefix should be {FIREHOSE_PREFIX} so each day lands in its own partition.'
    if '!{' in prefix and not destination.get('ErrorOutputPrefix'):
        return False, 'A prefix with expressions needs an error output prefix (AWS refuses it without one).'
    if destination.get('RoleARN') != ROLE_ARN or source.get('RoleARN') != ROLE_ARN:
        return False, f'{DELIVERY_STREAM} must use {ROLE_NAME} for the stream and the bucket.'
    policy = _role_policy()
    actions = {action for statement in (policy or {}).get('Statement', []) if statement.get('Effect') == 'Allow'
               for action in ([statement['Action']] if isinstance(statement.get('Action'), str) else statement.get('Action', []))}
    needed = {'kinesis:GetRecords', 'kinesis:GetShardIterator', 's3:PutObject'}
    if not needed <= actions:
        return False, f'{ROLE_NAME} must allow {", ".join(sorted(needed - actions))}.'
    return True, (f'{DELIVERY_STREAM} reads {STREAM} and writes {prefix} with an error prefix, as {ROLE_NAME}. Floci '
                  'does not use the role.')


def _check_produced() -> tuple[bool, str]:
    marker = _marked('produce-events')
    if not marker:
        return False, 'Put events on the stream.'
    if not _stream():
        return False, f'Stream {STREAM} was not found.'
    return True, f'Batch {marker["batch_id"]}: {marker["count"]} events on {STREAM}.'


def _check_landed() -> tuple[bool, str]:
    marker = _marked('land-in-s3')
    produced = _marked('produce-events')
    if not marker or not produced or marker.get('batch_id') != produced.get('batch_id'):
        return False, 'Wait for this batch to land in the lake.'
    if not _objects(RAW_PREFIX):
        return False, f'No objects under s3://{LAKE_BUCKET}/{RAW_PREFIX}.'
    return True, f'Batch {marker["batch_id"]} landed: {marker["count"]} events.'


def _check_catalog() -> tuple[bool, str]:
    database = _optional(lambda: _client('glue').get_database(Name=DATABASE)['Database'])
    if not database:
        return False, f'Glue database {DATABASE} was not found.'
    table = _table(RAW_TABLE)
    if not table:
        return False, f'Table {DATABASE}.{RAW_TABLE} was not found.'
    descriptor = table.get('StorageDescriptor') or {}
    if descriptor.get('Location', '').rstrip('/') != f's3://{LAKE_BUCKET}/{RAW_PREFIX}'.rstrip('/'):
        return False, f'{RAW_TABLE} must point at s3://{LAKE_BUCKET}/{RAW_PREFIX}.'
    columns = {column.get('Name') for column in descriptor.get('Columns') or []}
    missing = [column for column, _ in COLUMNS if column not in columns]
    if missing:
        return False, f'{RAW_TABLE} is missing columns {", ".join(missing)}.'
    if [key.get('Name') for key in table.get('PartitionKeys') or []] != ['dt']:
        return False, f'{RAW_TABLE} must be partitioned by dt.'
    parameters = table.get('Parameters') or {}
    wrong = [key for key, value in _projection_parameters().items() if parameters.get(key) != value]
    if wrong:
        return False, f'Partition projection is not set up: {", ".join(wrong)}.'
    return True, f'{DATABASE}.{RAW_TABLE} maps the raw JSON with dt partitions projected by date.'


def _check_workgroup() -> tuple[bool, str]:
    workgroup = _workgroup()
    if not workgroup:
        return False, f'Workgroup {WORKGROUP} was not found.'
    config = workgroup.get('Configuration') or {}
    location = (config.get('ResultConfiguration') or {}).get('OutputLocation') or ''
    if not location.startswith(f's3://{RESULTS_BUCKET}/'):
        return False, f'Results should go to s3://{RESULTS_BUCKET}/.'
    if not config.get('EnforceWorkGroupConfiguration'):
        return False, f'{WORKGROUP} must enforce its configuration.'
    cutoff = config.get('BytesScannedCutoffPerQuery')
    if not cutoff or cutoff > SCAN_CUTOFF_BYTES:
        return False, f'{WORKGROUP} needs a scan limit of at most {SCAN_CUTOFF_BYTES // 1048576} MB.'
    if not config.get('PublishCloudWatchMetricsEnabled'):
        return False, f'{WORKGROUP} should publish CloudWatch metrics.'
    return True, f'{WORKGROUP} sends results to {location} and cancels queries over {cutoff // 1048576} MB.'


def _check_query(step: str) -> tuple[bool, str]:
    marker = _marked(step)
    produced = _marked('produce-events')
    if not marker or not produced or marker.get('batch_id') != produced.get('batch_id'):
        return False, 'Run the query for this batch.'
    table = RAW_TABLE if step == 'query-clicks' else CURATED_TABLE
    if not _table(table):
        return False, f'Table {DATABASE}.{table} was not found.'
    return True, marker.get('message', 'Verified.')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_lake_buckets() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    for bucket in (LAKE_BUCKET, RESULTS_BUCKET):
        if not _bucket_exists(bucket):
            s3.create_bucket(Bucket=bucket)
    s3.put_public_access_block(Bucket=LAKE_BUCKET, PublicAccessBlockConfiguration={
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    s3.put_bucket_encryption(Bucket=LAKE_BUCKET, ServerSideEncryptionConfiguration={
        'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]})
    s3.put_bucket_lifecycle_configuration(Bucket=RESULTS_BUCKET, LifecycleConfiguration={'Rules': [
        {'ID': 'expire-query-results', 'Status': 'Enabled', 'Filter': {}, 'Expiration': {'Days': RESULTS_EXPIRY_DAYS}}]})
    response = {
        'LakeBucket': LAKE_BUCKET,
        'PublicAccessBlock': _optional(lambda: s3.get_public_access_block(Bucket=LAKE_BUCKET)['PublicAccessBlockConfiguration']),
        'Encryption': _optional(lambda: s3.get_bucket_encryption(Bucket=LAKE_BUCKET)['ServerSideEncryptionConfiguration']),
        'ResultsBucket': RESULTS_BUCKET,
        'ResultsLifecycle': _optional(lambda: s3.get_bucket_lifecycle_configuration(Bucket=RESULTS_BUCKET)['Rules']),
    }
    verified, message = _check_buckets()
    return _result('lake-buckets', BUCKETS_COMMAND, response, verified, message, started)


def _wait(predicate, attempts: int = 30, delay: float = 1.0) -> bool:
    for attempt in range(attempts):
        if predicate():
            return True
        if attempt < attempts - 1:
            time.sleep(delay)
    return False


def step_data_stream() -> dict[str, Any]:
    started = time.perf_counter()
    kinesis = _client('kinesis')
    stream = _stream()
    if stream and (stream.get('StreamModeDetails') or {}).get('StreamMode') != 'ON_DEMAND':
        kinesis.update_stream_mode(StreamARN=stream.get('StreamARN') or STREAM_ARN,
                                   StreamModeDetails={'StreamMode': 'ON_DEMAND'})
    if not stream:
        kinesis.create_stream(StreamName=STREAM, StreamModeDetails={'StreamMode': 'ON_DEMAND'})
    _wait(lambda: (_stream() or {}).get('StreamStatus') == 'ACTIVE')
    current = (_stream() or {}).get('RetentionPeriodHours') or 24
    if current < RETENTION_HOURS:
        kinesis.increase_stream_retention_period(StreamName=STREAM, RetentionPeriodHours=RETENTION_HOURS)
    elif current > RETENTION_HOURS:
        kinesis.decrease_stream_retention_period(StreamName=STREAM, RetentionPeriodHours=RETENTION_HOURS)
    verified, message = _check_stream()
    return _result('data-stream', STREAM_COMMAND, {'StreamDescriptionSummary': _stream()}, verified, message, started)


def step_delivery_stream() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_buckets()[0] or not _check_stream()[0]:
        return _result('delivery-stream', DELIVERY_COMMAND, {}, False,
                       'Create the lake bucket and the data stream first.', started)
    iam = _client('iam')
    if not _optional(lambda: iam.get_role(RoleName=ROLE_NAME)):
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(FIREHOSE_TRUST),
                        Description='Firehose delivery for the streaming data lake lab')
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY, PolicyDocument=json.dumps(FIREHOSE_POLICY))
    firehose = _client('firehose')
    existing = _delivery_stream()
    if existing and not _check_delivery()[0]:
        firehose.delete_delivery_stream(DeliveryStreamName=DELIVERY_STREAM)
        _wait(lambda: _delivery_stream() is None)
        existing = None
    if not existing:
        firehose.create_delivery_stream(
            DeliveryStreamName=DELIVERY_STREAM, DeliveryStreamType='KinesisStreamAsSource',
            KinesisStreamSourceConfiguration={'KinesisStreamARN': STREAM_ARN, 'RoleARN': ROLE_ARN},
            ExtendedS3DestinationConfiguration={
                'RoleARN': ROLE_ARN, 'BucketARN': f'arn:aws:s3:::{LAKE_BUCKET}', 'Prefix': FIREHOSE_PREFIX,
                'ErrorOutputPrefix': ERROR_PREFIX, 'CompressionFormat': 'UNCOMPRESSED',
                'BufferingHints': {'SizeInMBs': BUFFER_MB, 'IntervalInSeconds': BUFFER_SECONDS},
            },
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    _wait(lambda: (_delivery_stream() or {}).get('DeliveryStreamStatus') == 'ACTIVE')
    description = _delivery_stream() or {}
    response = {
        'DeliveryStreamName': DELIVERY_STREAM,
        'DeliveryStreamStatus': description.get('DeliveryStreamStatus'),
        'Source': description.get('Source'),
        'S3Destination': {key: _s3_destination(description).get(key) for key in (
            'BucketARN', 'Prefix', 'ErrorOutputPrefix', 'BufferingHints', 'CompressionFormat', 'RoleARN')},
        'RolePolicy': FIREHOSE_POLICY,
    }
    verified, message = _check_delivery()
    return _result('delivery-stream', DELIVERY_COMMAND, response, verified, message, started)


def step_produce_events() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_stream()[0]:
        return _result('produce-events', PRODUCE_COMMAND, {}, False, 'Create the data stream first.', started)
    # The random suffix keeps two batches sent in the same millisecond apart.
    batch_id = f'b{time.time_ns() // 1_000_000:x}{secrets.token_hex(2)}'
    events = _events(batch_id)
    kinesis = _client('kinesis')
    pending = [{'Data': (json.dumps(event) + '\n').encode(), 'PartitionKey': event['user_id']} for event in events]
    shards: Counter = Counter()
    retries = 0
    for _ in range(5):
        response = kinesis.put_records(StreamName=STREAM, Records=pending)
        failed = []
        for entry, record in zip(response.get('Records', []), pending):
            if entry.get('ErrorCode'):
                failed.append(record)
            else:
                shards[entry.get('ShardId')] += 1
        if not failed:
            break
        retries += 1
        pending = failed
        time.sleep(0.5)
    else:
        return _result('produce-events', PRODUCE_COMMAND, {'Unsent': len(pending)}, False,
                       f'{len(pending)} records still failed after retries.', started)
    replayed = 0
    for shard in kinesis.list_shards(StreamName=STREAM).get('Shards', []):
        iterator = kinesis.get_shard_iterator(StreamName=STREAM, ShardId=shard['ShardId'],
                                              ShardIteratorType='TRIM_HORIZON').get('ShardIterator')
        for _ in range(20):
            if not iterator:
                break
            page = kinesis.get_records(ShardIterator=iterator, Limit=1000)
            for record in page.get('Records', []):
                data = record.get('Data')
                text = data.decode() if isinstance(data, (bytes, bytearray)) else str(data)
                if f'"batch_id": "{batch_id}"' in text:
                    replayed += 1
            if not page.get('Records') or not page.get('MillisBehindLatest'):
                break
            iterator = page.get('NextShardIterator')
    response = {'BatchId': batch_id, 'Sent': len(events), 'RecordsPerShard': dict(shards), 'Retries': retries,
                'ReplayedFromTrimHorizon': replayed, 'SampleEvent': events[0]}
    if replayed < len(events):
        return _result('produce-events', PRODUCE_COMMAND, response, False,
                       f'Only {replayed} of {len(events)} events could be replayed from the stream.', started)
    _marker('produce-events', {'batch_id': batch_id, 'count': len(events)})
    verified, message = _check_produced()
    message += f' A TRIM_HORIZON read replayed all {replayed}.'
    return _result('produce-events', PRODUCE_COMMAND, response, verified, message, started)


def step_land_in_s3() -> dict[str, Any]:
    started = time.perf_counter()
    produced = _marked('produce-events')
    if not produced:
        return _result('land-in-s3', LANDING_COMMAND, {}, False, 'Put events on the stream first.', started)
    if not _check_delivery()[0]:
        return _result('land-in-s3', LANDING_COMMAND, {}, False, 'Create the delivery stream first.', started)
    batch_id = produced['batch_id']
    landed: dict[str, Any] = {'count': 0}
    deadline = time.monotonic() + LANDING_TIMEOUT_SECONDS
    while True:
        landed = _landed(batch_id)
        if landed['count'] >= produced['count'] or time.monotonic() >= deadline:
            break
        time.sleep(3)
    errors = [item['Key'] for item in _objects('errors/')]
    response = {'BatchId': batch_id, 'Landed': landed['count'], 'ByPartition': landed['by_dt'],
                'Objects': landed['keys'], 'ErrorObjects': errors}
    if landed['count'] < produced['count']:
        return _result('land-in-s3', LANDING_COMMAND, response, False,
                       f'{landed["count"]} of {produced["count"]} events reached the lake after '
                       f'{LANDING_TIMEOUT_SECONDS} seconds.', started)
    folders = ', '.join(f'dt={dt}' for dt in sorted(landed['by_dt']))
    message = f'All {landed["count"]} events of batch {batch_id} landed under {RAW_PREFIX}{folders}/.'
    _marker('land-in-s3', {'batch_id': batch_id, 'count': landed['count'], 'dt': sorted(landed['by_dt'])})
    return _result('land-in-s3', LANDING_COMMAND, response, True, message, started)


def step_glue_catalog() -> dict[str, Any]:
    started = time.perf_counter()
    glue = _client('glue')
    if not _optional(lambda: glue.get_database(Name=DATABASE)):
        glue.create_database(DatabaseInput={'Name': DATABASE, 'Description': 'Clickstream data lake (Kinesis lab)'})
    table_input = _table_input(RAW_TABLE, RAW_PREFIX, parquet=False)
    if _table(RAW_TABLE):
        glue.update_table(DatabaseName=DATABASE, TableInput=table_input)
    else:
        glue.create_table(DatabaseName=DATABASE, TableInput=table_input)
    table = _table(RAW_TABLE) or {}
    response = {'Database': DATABASE, 'Table': {key: table.get(key) for key in (
        'Name', 'TableType', 'Parameters', 'PartitionKeys', 'StorageDescriptor')}, 'DDL': _ddl(RAW_TABLE, RAW_PREFIX)}
    verified, message = _check_catalog()
    return _result('glue-catalog', CATALOG_COMMAND, response, verified, message, started)


def step_athena_workgroup() -> dict[str, Any]:
    started = time.perf_counter()
    athena = _client('athena')
    configuration = {
        'ResultConfiguration': {'OutputLocation': f's3://{RESULTS_BUCKET}/results/'},
        'EnforceWorkGroupConfiguration': True,
        'PublishCloudWatchMetricsEnabled': True,
        'BytesScannedCutoffPerQuery': SCAN_CUTOFF_BYTES,
    }
    if _workgroup():
        athena.update_work_group(WorkGroup=WORKGROUP, ConfigurationUpdates={
            'ResultConfigurationUpdates': configuration['ResultConfiguration'],
            'EnforceWorkGroupConfiguration': True, 'PublishCloudWatchMetricsEnabled': True,
            'BytesScannedCutoffPerQuery': SCAN_CUTOFF_BYTES})
    else:
        athena.create_work_group(Name=WORKGROUP, Configuration=configuration,
                                 Description='Clickstream analysis (Kinesis lab)',
                                 Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    verified, message = _check_workgroup()
    return _result('athena-workgroup', WORKGROUP_COMMAND, {'WorkGroup': _workgroup()}, verified, message, started)


def _query_step(step: str, command: str, table: str, started: float, prepare=None) -> dict[str, Any]:
    landed = _marked('land-in-s3')
    produced = _marked('produce-events')
    if not landed or not produced or landed.get('batch_id') != produced.get('batch_id'):
        return _result(step, command, {}, False, 'Land this batch in the lake first.', started)
    for check, name in ((_check_catalog, 'the Glue table'), (_check_workgroup, 'the workgroup')):
        ok, problem = check()
        if not ok:
            return _result(step, command, {}, False, f'Set up {name} first: {problem}', started)
    response: dict[str, Any] = {}
    if prepare:
        response['Prepare'] = prepare()
        if response['Prepare'].get('State') not in (None, 'SUCCEEDED'):
            return _result(step, command, response, False,
                           f'Preparing {table} failed: {response["Prepare"].get("StateChangeReason")}', started)
    batch_id = landed['batch_id']
    dt = landed['dt'][0] if len(landed.get('dt') or []) == 1 else None
    query = _athena(_page_query(table, batch_id, dt), WORKGROUP)
    response['Query'] = query
    if query['State'] != 'SUCCEEDED':
        return _result(step, command, response, False,
                       f'The query {query["State"]}: {query.get("StateChangeReason")}', started)
    counts, expected = _page_counts(query['Rows']), _expected_counts()
    response['PageViews'], response['Expected'] = counts, expected
    if counts != expected:
        return _result(step, command, response, False, f'Athena counted {counts}, expected {expected}.', started)
    location = query.get('OutputLocation') or ''
    message = (f'Athena counted {sum(counts.values())} page views across {len(counts)} pages in {table}'
               + (f' (dt={dt} only)' if dt else '') + ', matching the batch.')
    if location and not location.startswith(f's3://{RESULTS_BUCKET}/'):
        message += f' Floci wrote the results to {location.split("/results/")[0]}, not the workgroup\'s bucket.'
    if not query.get('DataScannedInBytes'):
        message += ' Floci reports 0 bytes scanned.'
    _marker(step, {'batch_id': batch_id, 'message': message})
    return _result(step, command, response, True, message, started)


def step_query_clicks() -> dict[str, Any]:
    return _query_step('query-clicks', QUERY_COMMAND, RAW_TABLE, time.perf_counter())


def _write_parquet() -> dict[str, Any]:
    result = _athena(FLOCI_PARQUET_SQL, WORKGROUP)
    if result['State'] == 'SUCCEEDED':
        glue = _client('glue')
        table_input = _table_input(CURATED_TABLE, CURATED_PREFIX, parquet=True)
        if _table(CURATED_TABLE):
            glue.update_table(DatabaseName=DATABASE, TableInput=table_input)
        else:
            glue.create_table(DatabaseName=DATABASE, TableInput=table_input)
        result['ParquetObjects'] = [{'Key': item['Key'], 'Size': item.get('Size')} for item in _objects(CURATED_PREFIX)]
        result['RawObjectBytes'] = sum(item.get('Size') or 0 for item in _objects(RAW_PREFIX))
    result['TaughtOnAws'] = CTAS_SQL
    result['RanOnFloci'] = FLOCI_PARQUET_SQL
    return result


def step_convert_parquet() -> dict[str, Any]:
    started = time.perf_counter()
    result = _query_step('convert-parquet', PARQUET_COMMAND, CURATED_TABLE, started, prepare=_write_parquet)
    if result['verified']:
        objects = (result['json'].get('Prepare') or {}).get('ParquetObjects') or []
        parquet_bytes = sum(item.get('Size') or 0 for item in objects)
        raw_bytes = (result['json'].get('Prepare') or {}).get('RawObjectBytes') or 0
        extra = f' {len(objects)} Parquet object(s), {parquet_bytes} bytes, against {raw_bytes} bytes of raw JSON.'
        result['verification']['message'] += extra + ' Floci ran the DuckDB COPY that matches the CTAS.'
        _marker('convert-parquet', {'batch_id': _marked('land-in-s3')['batch_id'],
                                    'message': result['verification']['message']})
    return result


RUNNERS = {
    'lake-buckets': step_lake_buckets,
    'data-stream': step_data_stream,
    'delivery-stream': step_delivery_stream,
    'produce-events': step_produce_events,
    'land-in-s3': step_land_in_s3,
    'glue-catalog': step_glue_catalog,
    'athena-workgroup': step_athena_workgroup,
    'query-clicks': step_query_clicks,
    'convert-parquet': step_convert_parquet,
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    checks = {
        'lake-buckets': lambda: _check_buckets()[0],
        'data-stream': lambda: _check_stream()[0],
        'delivery-stream': lambda: _check_delivery()[0],
        'produce-events': lambda: _check_produced()[0],
        'land-in-s3': lambda: _check_landed()[0],
        'glue-catalog': lambda: _check_catalog()[0],
        'athena-workgroup': lambda: _check_workgroup()[0],
        'query-clicks': lambda: _check_query('query-clicks')[0],
        'convert-parquet': lambda: _check_query('convert-parquet')[0],
    }
    steps = {}
    for step, check in checks.items():
        verified = _safe(check)
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


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Delete the delivery stream, data stream, role, Glue tables and database, workgroup, and both buckets."""
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> bool:
        try:
            call()
            deleted.append(label)
            return True
        except ClientError as exc:
            code = _code(exc)
            if _missing(exc) or code in {'ResourceNotFoundException', 'InvalidRequestException'}:
                return False
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')
        return False

    firehose, kinesis, iam, glue, athena, s3 = (
        _client(name) for name in ('firehose', 'kinesis', 'iam', 'glue', 'athena', 's3'))
    attempt(f'delivery-stream:{DELIVERY_STREAM}', lambda: firehose.delete_delivery_stream(
        DeliveryStreamName=DELIVERY_STREAM))
    attempt(f'stream:{STREAM}', lambda: kinesis.delete_stream(StreamName=STREAM, EnforceConsumerDeletion=True))
    attempt(f'role-policy:{ROLE_POLICY}', lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))
    for table in (CURATED_TABLE, RAW_TABLE):
        attempt(f'table:{DATABASE}.{table}', lambda table=table: glue.delete_table(DatabaseName=DATABASE, Name=table))
    attempt(f'database:{DATABASE}', lambda: glue.delete_database(Name=DATABASE))
    attempt(f'workgroup:{WORKGROUP}', lambda: athena.delete_work_group(WorkGroup=WORKGROUP, RecursiveDeleteOption=True))
    for bucket in (LAKE_BUCKET, RESULTS_BUCKET):
        try:
            exists = _bucket_exists(bucket)
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'bucket {bucket}: {exc}')
            continue
        if not exists:
            continue
        try:
            keys = []
            kwargs: dict[str, Any] = {'Bucket': bucket}
            for _ in range(100):
                page = s3.list_objects_v2(**kwargs)
                keys.extend(item['Key'] for item in page.get('Contents', []))
                if not page.get('IsTruncated') or not page.get('NextContinuationToken'):
                    break
                kwargs['ContinuationToken'] = page['NextContinuationToken']
            for start in range(0, len(keys), 1000):
                s3.delete_objects(Bucket=bucket, Delete={'Objects': [{'Key': key} for key in keys[start:start + 1000]]})
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'objects in {bucket}: {exc}')
        attempt(f'bucket:{bucket}', lambda bucket=bucket: s3.delete_bucket(Bucket=bucket))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws firehose delete-delivery-stream --delivery-stream-name {DELIVERY_STREAM}\n'
            f'aws kinesis delete-stream --stream-name {STREAM} --enforce-consumer-deletion\n'
            f'aws iam delete-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY}\n'
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            f'aws glue delete-table --database-name {DATABASE} --name {CURATED_TABLE}\n'
            f'aws glue delete-table --database-name {DATABASE} --name {RAW_TABLE}\n'
            f'aws glue delete-database --name {DATABASE}\n'
            f'aws athena delete-work-group --work-group {WORKGROUP} --recursive-delete-option\n'
            f'aws s3 rb s3://{LAKE_BUCKET} --force\n'
            f'aws s3 rb s3://{RESULTS_BUCKET} --force'
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
            else 'The delivery stream, data stream, role, Glue catalog, workgroup, and both buckets are deleted.',
        },
    }
