"""Centralized logging lab: KMS-encrypted log groups with retention for two services, per-service error metrics and a
composite alarm, a subscription filter that streams only errors to Kinesis, a Firehose archive in S3 with a lifecycle
rule, a Logs Insights query across both groups, and a destination and metric stream for a central logging account.

Floci 2.4.0 stores the KMS key and retention on each log group, turns JSON metric filters with dimensions into real
per-service metrics, evaluates metric and composite alarms, delivers subscription filter matches to Kinesis and to
Firehose (gzip-encoded, as on AWS), and Firehose writes its buffer to S3 about a minute later. What Floci does not do:
it does not refuse writes when the log group's key is disabled, it allows more than two subscription filters per group,
its Logs Insights has no stats command (the lab totals the rows itself), it applies S3 lifecycle expiration but not
storage class transitions, and
destinations and metric streams are stored without anything crossing accounts or streaming metrics (and it has no
DescribeDestinations or DeleteDestination).
"""

from __future__ import annotations

import base64
import json
import time
import zlib
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudwatch'
LAB_KEY = 'centralized-logging'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
CENTRAL_ACCOUNT = '111122223333'

SERVICES = ('orders', 'payments')
GROUPS = {name: f'/lab-central/{name}' for name in SERVICES}
STREAM = 'app'
RETENTION_DAYS = 30
KEY_ALIAS = 'alias/lab-central-logs'

NAMESPACE = 'LabCentral'
ERROR_METRIC = 'ServiceErrors'
ERROR_PATTERN = '{ $.level = "ERROR" }'
METRIC_FILTER = 'lab-central-errors'
ALARMS = {name: f'lab-central-{name}-errors' for name in SERVICES}
ALARM_THRESHOLD = 3
INCIDENT_ALARM = 'lab-central-incident'
INCIDENT_RULE = ' OR '.join(f'ALARM("{ALARMS[name]}")' for name in SERVICES)

ERROR_STREAM = 'lab-central-errors'
ERROR_SUBSCRIPTION = 'errors-to-kinesis'
LOGS_ROLE = 'lab-central-logs-role'
LOGS_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{LOGS_ROLE}'
FIREHOSE_ROLE = 'lab-central-firehose-role'
FIREHOSE_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FIREHOSE_ROLE}'
INLINE_POLICY = 'lab-central'

BUCKET = 'lab-central-log-archive'
ARCHIVE_PREFIX = 'logs/'
ARCHIVE_STREAM = 'lab-central-archive'
ARCHIVE_ARN = f'arn:aws:firehose:{REGION}:{ACCOUNT}:deliverystream/{ARCHIVE_STREAM}'
ARCHIVE_SUBSCRIPTION = 'all-to-archive'
ARCHIVE_BUFFER_SECONDS = 60
LIFECYCLE_RULE = {
    'ID': 'archive-then-expire', 'Status': 'Enabled', 'Filter': {'Prefix': ARCHIVE_PREFIX},
    'Transitions': [{'Days': 30, 'StorageClass': 'STANDARD_IA'}, {'Days': 90, 'StorageClass': 'GLACIER'}],
    'Expiration': {'Days': 365},
}

DESTINATION = 'lab-central-security'
METRIC_STREAM = 'lab-central-metrics'

# Each service writes six lines; payments has three errors (its alarm fires), orders two (its alarm stays OK).
TRAFFIC = {
    'orders': ['INFO', 'INFO', 'ERROR', 'WARN', 'INFO', 'ERROR'],
    'payments': ['INFO', 'ERROR', 'ERROR', 'WARN', 'ERROR', 'INFO'],
}
MESSAGES = {'INFO': 'request served', 'WARN': 'slow dependency', 'ERROR': 'request failed'}
EXPECTED_ERRORS = {name: levels.count('ERROR') for name, levels in TRAFFIC.items()}
EXPECTED_EVENTS = sum(len(levels) for levels in TRAFFIC.values())

STREAM_WAIT_SECONDS = 30
ALARM_WAIT_SECONDS = 150
ARCHIVE_WAIT_SECONDS = 150
QUERY_WAIT_SECONDS = 20
INSIGHTS_QUERY = 'fields @timestamp, service, level, message, run | filter level = "ERROR" | sort @timestamp desc'

FLOCI = 'How Floci emulates this: '

LOGS_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'logs.amazonaws.com'}, 'Action': 'sts:AssumeRole',
                   'Condition': {'StringLike': {'aws:SourceArn': f'arn:aws:logs:{REGION}:{ACCOUNT}:*'}}}],
}
LOGS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Effect': 'Allow', 'Action': 'kinesis:PutRecord',
         'Resource': f'arn:aws:kinesis:{REGION}:{ACCOUNT}:stream/{ERROR_STREAM}'},
        {'Effect': 'Allow', 'Action': ['firehose:PutRecord', 'firehose:PutRecordBatch'], 'Resource': ARCHIVE_ARN},
    ],
}
FIREHOSE_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'firehose.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
FIREHOSE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Action': ['s3:PutObject', 's3:GetBucketLocation', 's3:ListBucket'],
                   'Resource': [f'arn:aws:s3:::{BUCKET}', f'arn:aws:s3:::{BUCKET}/*']}],
}


def key_policy() -> dict[str, Any]:
    """The account keeps control of the key, and CloudWatch Logs may use it only for this account's log groups."""
    return {
        'Version': '2012-10-17',
        'Statement': [
            {'Sid': 'AccountAdmin', 'Effect': 'Allow', 'Principal': {'AWS': f'arn:aws:iam::{ACCOUNT}:root'},
             'Action': 'kms:*', 'Resource': '*'},
            {'Sid': 'CloudWatchLogs', 'Effect': 'Allow', 'Principal': {'Service': f'logs.{REGION}.amazonaws.com'},
             'Action': ['kms:Encrypt*', 'kms:Decrypt*', 'kms:ReEncrypt*', 'kms:GenerateDataKey*', 'kms:Describe*'],
             'Resource': '*',
             'Condition': {'ArnLike': {'kms:EncryptionContext:aws:logs:arn':
                                       f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/lab-central/*'}}},
        ],
    }


def destination_policy(destination_arn: str) -> dict[str, Any]:
    """Let the workload account subscribe its log groups to the central account's destination."""
    return {
        'Version': '2012-10-17',
        'Statement': [{'Sid': 'WorkloadAccounts', 'Effect': 'Allow', 'Principal': {'AWS': ACCOUNT},
                       'Action': 'logs:PutSubscriptionFilter', 'Resource': destination_arn}],
    }


ENCRYPT_COMMAND = (
    'aws kms create-key --description "CloudWatch Logs for the lab services" --policy file://key-policy.json\n'
    f'aws kms create-alias --alias-name {KEY_ALIAS} --target-key-id <key-id>\n'
    + ''.join(
        f'aws logs create-log-group --log-group-name {group} --kms-key-id <key-arn>\n'
        f'aws logs put-retention-policy --log-group-name {group} --retention-in-days {RETENTION_DAYS}\n'
        f'aws logs create-log-stream --log-group-name {group} --log-stream-name {STREAM}\n'
        for group in GROUPS.values()
    )
    + 'aws kms disable-key --key-id <key-id> && aws logs put-log-events ...   # does Floci refuse the write?\n'
    'aws kms enable-key --key-id <key-id>'
)
METRICS_COMMAND = (
    ''.join(
        f'aws logs put-metric-filter --log-group-name {group} --filter-name {METRIC_FILTER} '
        f'--filter-pattern \'{ERROR_PATTERN}\' --metric-transformations '
        f'metricName={ERROR_METRIC},metricNamespace={NAMESPACE},metricValue=1,unit=Count,'
        'dimensions={Service=$.service}\n'
        for group in GROUPS.values()
    )
    + ''.join(
        f'aws cloudwatch put-metric-alarm --alarm-name {ALARMS[name]} --namespace {NAMESPACE} '
        f'--metric-name {ERROR_METRIC} --dimensions Name=Service,Value={name} --statistic Sum --period 60 '
        f'--evaluation-periods 1 --threshold {ALARM_THRESHOLD} '
        '--comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching\n'
        for name in SERVICES
    )
    + f'aws cloudwatch put-composite-alarm --alarm-name {INCIDENT_ALARM} --alarm-rule \'{INCIDENT_RULE}\''
)
STREAM_COMMAND = (
    f'aws kinesis create-stream --stream-name {ERROR_STREAM} --shard-count 1\n'
    f'aws iam create-role --role-name {LOGS_ROLE} --assume-role-policy-document file://logs-trust.json\n'
    f'aws iam put-role-policy --role-name {LOGS_ROLE} --policy-name {INLINE_POLICY} '
    '--policy-document file://logs-policy.json\n'
    + ''.join(
        f'aws logs put-subscription-filter --log-group-name {group} --filter-name {ERROR_SUBSCRIPTION} '
        f'--filter-pattern \'{ERROR_PATTERN}\' --destination-arn <stream-arn> --role-arn {LOGS_ROLE_ARN}\n'
        for group in GROUPS.values()
    ).rstrip('\n')
)
ARCHIVE_COMMAND = (
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3api put-bucket-lifecycle-configuration --bucket {BUCKET} --lifecycle-configuration file://lifecycle.json\n'
    f'aws iam create-role --role-name {FIREHOSE_ROLE} --assume-role-policy-document file://firehose-trust.json\n'
    f'aws firehose create-delivery-stream --delivery-stream-name {ARCHIVE_STREAM} '
    f'--extended-s3-destination-configuration "RoleARN={FIREHOSE_ROLE_ARN},BucketARN=arn:aws:s3:::{BUCKET},'
    f'Prefix={ARCHIVE_PREFIX},BufferingHints={{IntervalInSeconds={ARCHIVE_BUFFER_SECONDS},SizeInMBs=1}}"\n'
    + ''.join(
        f'aws logs put-subscription-filter --log-group-name {group} --filter-name {ARCHIVE_SUBSCRIPTION} '
        f'--filter-pattern "" --destination-arn {ARCHIVE_ARN} --role-arn {LOGS_ROLE_ARN}\n'
        for group in GROUPS.values()
    ).rstrip('\n')
)
SHIP_COMMAND = (
    ''.join(
        f'aws logs put-log-events --log-group-name {group} --log-stream-name {STREAM} --log-events '
        f'\'[{{"timestamp":<now>,"message":"{{\\"service\\":\\"{name}\\",\\"level\\":\\"ERROR\\",...}}"}}, ...]\'\n'
        for name, group in GROUPS.items()
    )
    + f'aws kinesis get-shard-iterator --stream-name {ERROR_STREAM} --shard-id shardId-000000000000 '
    '--shard-iterator-type TRIM_HORIZON\n'
    'aws kinesis get-records --shard-iterator <iterator>   # each Data is gzip: base64 -d | gunzip'
)
ALARM_COMMAND = (
    ''.join(
        f'aws cloudwatch get-metric-statistics --namespace {NAMESPACE} --metric-name {ERROR_METRIC} '
        f'--dimensions Name=Service,Value={name} --statistics Sum --period 300 --start-time <10 minutes ago> '
        '--end-time <now>\n'
        for name in SERVICES
    )
    + f'aws cloudwatch describe-alarms --alarm-names {" ".join(ALARMS.values())} {INCIDENT_ALARM} '
    '--alarm-types MetricAlarm CompositeAlarm'
)
DELIVERED_COMMAND = (
    f'aws s3api list-objects-v2 --bucket {BUCKET} --prefix {ARCHIVE_PREFIX}\n'
    f'aws s3 cp s3://{BUCKET}/<key> - | gunzip   # one DATA_MESSAGE per batch\n'
    f'aws s3api get-bucket-lifecycle-configuration --bucket {BUCKET}'
)
INSIGHTS_COMMAND = (
    f'aws logs start-query --log-group-names {" ".join(GROUPS.values())} --start-time <30 minutes ago> '
    f'--end-time <now> --query-string \'{INSIGHTS_QUERY}\'\n'
    'aws logs get-query-results --query-id <query-id>\n'
    'aws logs start-query ... --query-string \'filter level = "ERROR" | stats count(*) by service\'   '
    '# the aggregation you would run on AWS'
)
CENTRAL_COMMAND = (
    f'aws logs put-destination --destination-name {DESTINATION} --target-arn <stream-arn> '
    f'--role-arn {LOGS_ROLE_ARN}\n'
    f'aws logs put-destination-policy --destination-name {DESTINATION} '
    '--access-policy file://destination-policy.json\n'
    f'aws cloudwatch put-metric-stream --name {METRIC_STREAM} --firehose-arn {ARCHIVE_ARN} '
    f'--role-arn {FIREHOSE_ROLE_ARN} --output-format json --include-filters Namespace={NAMESPACE}\n'
    f'aws logs describe-destinations --destination-name-prefix {DESTINATION}   # not in Floci: put-destination again\n'
    f'aws cloudwatch get-metric-stream --name {METRIC_STREAM}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Centralize logs from two services: encrypted groups, error streaming, an S3 archive, and alarms',
    'description': (
        'Encrypt two services\' log groups with a KMS key and set retention, turn their errors into per-service '
        'metrics with alarms and a composite incident alarm, stream only errors to Kinesis, archive everything to S3 '
        'through Firehose with a lifecycle rule, query both groups with Logs Insights, and share the error stream '
        'and metrics with a central logging account.'
    ),
    'steps': [
        {
            'key': 'encrypted-log-groups',
            'title': 'Create encrypted log groups with retention',
            'command': ENCRYPT_COMMAND,
            'explanation': (
                f'Each service writes to its own log group, {GROUPS["orders"]} and {GROUPS["payments"]}. A customer '
                f'managed KMS key ({KEY_ALIAS}) encrypts both: its key policy lets the CloudWatch Logs service use it, '
                'but only for log groups in this account under /lab-central/. Retention of '
                f'{RETENTION_DAYS} days deletes old events, so logs are not kept (and paid for) forever. On AWS, '
                'disabling the key stops new writes and reads until it is enabled again. '
                + FLOCI + 'the key, its policy, and the key and retention on each log group are stored and shown by '
                'DescribeLogGroups, but the events are not encrypted with the key: the step disables the key, '
                'writes an event, and reports that Floci accepted it, then enables the key again.'
            ),
        },
        {
            'key': 'service-metrics',
            'title': 'Count errors per service and alarm on them',
            'command': METRICS_COMMAND,
            'explanation': (
                f'A metric filter on each group matches JSON lines where level is ERROR and adds 1 to '
                f'{NAMESPACE}/{ERROR_METRIC}, with a Service dimension taken from the line\'s service field, so one '
                f'metric name gives a separate series per service. Each service gets an alarm at {ALARM_THRESHOLD} '
                f'errors a minute, and the composite alarm {INCIDENT_ALARM} goes into ALARM when either does, so '
                'on-call gets one page per incident instead of one per service. Metric filters only count events '
                'written after they exist, so they come before any traffic. '
                + FLOCI + 'the filters create real per-service metrics, and the alarms and the composite alarm are '
                'evaluated (metric alarms about once a minute).'
            ),
        },
        {
            'key': 'error-stream',
            'title': 'Stream only errors to Kinesis',
            'command': STREAM_COMMAND,
            'explanation': (
                f'A subscription filter on each group sends events matching {ERROR_PATTERN} to the {ERROR_STREAM} '
                'Kinesis stream, where an alerting or security tool reads them within seconds. CloudWatch Logs '
                f'writes to the stream as {LOGS_ROLE}, which trusts logs.amazonaws.com for this account only and may '
                'put records only on that stream. '
                + FLOCI + 'the stream and filters are real and deliver matching events. Floci does not check the '
                'role, and it allows more than the two subscription filters per log group that AWS allows.'
            ),
        },
        {
            'key': 'log-archive',
            'title': 'Archive every event to S3 through Firehose',
            'command': ARCHIVE_COMMAND,
            'explanation': (
                f'A second subscription filter on each group, with an empty pattern, sends every event to the '
                f'{ARCHIVE_STREAM} Firehose stream, which buffers for up to {ARCHIVE_BUFFER_SECONDS} seconds and writes '
                f'batches under s3://{BUCKET}/{ARCHIVE_PREFIX}. A lifecycle rule moves the archive to Standard-IA '
                'after 30 days and Glacier after 90, and deletes it after a year, so the log group keeps a month and '
                'the bucket keeps the cheap long-term copy. '
                + FLOCI + 'the bucket, Firehose stream, and filters are real, and Firehose writes the batches to S3. '
                'Floci applies the rule\'s expiration (the archive would be deleted after a year) but not the '
                'storage class transitions.'
            ),
        },
        {
            'key': 'ship-logs',
            'title': 'Ship application logs and read the errors from Kinesis',
            'command': SHIP_COMMAND,
            'explanation': (
                f'Each service writes {len(TRAFFIC["orders"])} JSON log lines: orders has '
                f'{EXPECTED_ERRORS["orders"]} errors and payments {EXPECTED_ERRORS["payments"]}. The step reads the '
                'stream from the start, base64-decodes and gunzips each record, and keeps the DATA_MESSAGE ones. Every '
                'event it finds should be an error, from both log groups, and none of the INFO or WARN lines. '
                + FLOCI + 'the delivery is real: each record is a gzip CloudWatch Logs envelope with the log group, '
                'stream, filter name, and matching events, as on AWS.'
            ),
        },
        {
            'key': 'error-alarm',
            'title': 'Watch the payments alarm and the incident alarm fire',
            'command': ALARM_COMMAND,
            'explanation': (
                f'The metric filters counted {EXPECTED_ERRORS["orders"]} orders errors and '
                f'{EXPECTED_ERRORS["payments"]} payments errors. Payments reaches the threshold of '
                f'{ALARM_THRESHOLD}, so its alarm goes into ALARM, and so does {INCIDENT_ALARM}; the orders alarm '
                'stays OK. The step waits for the alarms to be evaluated. '
                + FLOCI + 'the per-service sums and the alarm states are real; the first evaluation can take up to '
                'about a minute.'
            ),
        },
        {
            'key': 'archive-delivered',
            'title': 'Read the archive Firehose wrote to S3',
            'command': DELIVERED_COMMAND,
            'explanation': (
                'Firehose writes a batch when its buffer interval ends. The step lists the archive, unpacks each '
                f'object (gzip CloudWatch Logs envelopes, one after another), and counts this run\'s events: all '
                f'{EXPECTED_EVENTS} should be there, every level from both services. On AWS you would add a Firehose '
                'transformation Lambda to unpack the envelopes into one JSON line per event, partition by date, and '
                'query the archive with Athena through a Glue table. '
                + FLOCI + 'the objects are real and arrive about a minute after the events. Floci\'s Athena needs a '
                'DuckDB extension it downloads from the internet, so the lab reads the archive itself.'
            ),
        },
        {
            'key': 'insights-query',
            'title': 'Query both log groups with Logs Insights',
            'command': INSIGHTS_COMMAND,
            'explanation': (
                'One Logs Insights query runs across both log groups and lists the error lines with their service, '
                'which is how you investigate an incident without knowing which service failed. On AWS you would '
                'finish with stats count(*) by service. '
                + FLOCI + 'fields, filter, sort, and limit work across several log groups, but stats, parse, and '
                'like are not supported and @log is empty, so the query returns the rows and the lab counts them by '
                'the service field itself.'
            ),
        },
        {
            'key': 'central-account',
            'title': 'Share errors and metrics with a central logging account',
            'command': CENTRAL_COMMAND,
            'explanation': (
                f'In a multi-account organization, the security account owns a CloudWatch Logs destination '
                f'({DESTINATION}) in front of its Kinesis stream, and its access policy lets workload account '
                f'{ACCOUNT} subscribe log groups to it. A metric stream sends the {NAMESPACE} metrics continuously to '
                'Firehose, for the central account or a monitoring partner. Here both are created in one account, '
                f'and the policy names the workload account as it would in central account {CENTRAL_ACCOUNT}. '
                + FLOCI + 'the destination, its policy, and the metric stream are stored and returned, but nothing '
                'crosses accounts and the metric stream sends no metrics. Floci has no DescribeDestinations or '
                'DeleteDestination (putting the destination again returns it with its policy), and a subscription '
                'filter that points at the destination is accepted but delivers nothing.'
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


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Pure helpers ─────────────────────────────────────────────────────────────

def log_lines(run: str) -> dict[str, list[str]]:
    """The JSON log lines each service writes for one run."""
    return {
        name: [json.dumps({'service': name, 'level': level, 'message': MESSAGES[level], 'request': f'{name}-{index}',
                           'run': run})
               for index, level in enumerate(levels, start=1)]
        for name, levels in TRAFFIC.items()
    }


def unpack_envelopes(data: bytes) -> list[dict[str, Any]]:
    """Split gzip CloudWatch Logs envelopes written back to back (as Firehose and Kinesis carry them)."""
    envelopes = []
    while data:
        if data[:2] != b'\x1f\x8b':
            data = data[1:]
            continue
        inflater = zlib.decompressobj(31)
        envelopes.append(json.loads(inflater.decompress(data)))
        data = inflater.unused_data
    return envelopes


def events_from(envelopes: list[dict[str, Any]], run: str) -> list[dict[str, Any]]:
    """This run's log events from DATA_MESSAGE envelopes, each with its log group and parsed message."""
    found = []
    for envelope in envelopes:
        if envelope.get('messageType') != 'DATA_MESSAGE':
            continue
        for event in envelope.get('logEvents') or []:
            try:
                message = json.loads(event.get('message') or '')
            except ValueError:
                continue
            if isinstance(message, dict) and message.get('run') == run:
                found.append({'logGroup': envelope.get('logGroup'), **message})
    return found


def count_by(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get(field)] = counts.get(row.get(field), 0) + 1
    return dict(sorted(counts.items(), key=lambda item: str(item[0])))


def insights_rows(results: list[list[dict[str, str]]]) -> list[dict[str, str]]:
    return [{cell['field']: cell['value'] for cell in row} for row in results]


# ── Floci lookups ────────────────────────────────────────────────────────────

def _key_arn() -> str | None:
    aliases = _safe_call(lambda: _client('kms').list_aliases()['Aliases'], [])
    target = next((alias.get('TargetKeyId') for alias in aliases if alias['AliasName'] == KEY_ALIAS), None)
    if not target:
        return None
    return _safe_call(lambda: _client('kms').describe_key(KeyId=target)['KeyMetadata']['Arn'])


def _group(name: str) -> dict[str, Any] | None:
    groups = _safe_call(lambda: _client('logs').describe_log_groups(logGroupNamePrefix=name)['logGroups'], [])
    return next((group for group in groups if group['logGroupName'] == name), None)


def _subscriptions(group: str) -> dict[str, dict[str, Any]]:
    filters = _safe_call(lambda: _client('logs').describe_subscription_filters(logGroupName=group)
                         ['subscriptionFilters'], [])
    return {item['filterName']: item for item in filters}


def _metric_filters(group: str) -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('logs').describe_metric_filters(logGroupName=group)['metricFilters'], [])


def _stream_arn() -> str | None:
    return _safe_call(lambda: _client('kinesis').describe_stream_summary(StreamName=ERROR_STREAM)
                      ['StreamDescriptionSummary']['StreamARN'])


def _archive_status() -> str | None:
    return _safe_call(lambda: _client('firehose').describe_delivery_stream(DeliveryStreamName=ARCHIVE_STREAM)
                      ['DeliveryStreamDescription']['DeliveryStreamStatus'])


def _alarm_states() -> dict[str, str]:
    response = _safe_call(lambda: _client('cloudwatch').describe_alarms(
        AlarmNames=[*ALARMS.values(), INCIDENT_ALARM], AlarmTypes=['MetricAlarm', 'CompositeAlarm']), {})
    alarms = (response.get('MetricAlarms') or []) + (response.get('CompositeAlarms') or [])
    return {alarm['AlarmName']: alarm['StateValue'] for alarm in alarms}


def _error_sums() -> dict[str, float]:
    cloudwatch, now = _client('cloudwatch'), time.time()
    sums = {}
    for name in SERVICES:
        points = _safe_call(lambda name=name: cloudwatch.get_metric_statistics(
            Namespace=NAMESPACE, MetricName=ERROR_METRIC, Dimensions=[{'Name': 'Service', 'Value': name}],
            StartTime=now - 900, EndTime=now + 60, Period=900, Statistics=['Sum'])['Datapoints'], [])
        sums[name] = sum(point['Sum'] for point in points)
    return sums


def _lifecycle() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('s3').get_bucket_lifecycle_configuration(Bucket=BUCKET)['Rules'], [])


def _stream_events(run: str) -> list[dict[str, Any]]:
    kinesis = _client('kinesis')
    shards = kinesis.describe_stream(StreamName=ERROR_STREAM)['StreamDescription']['Shards']
    envelopes = []
    for shard in shards:
        iterator = kinesis.get_shard_iterator(StreamName=ERROR_STREAM, ShardId=shard['ShardId'],
                                              ShardIteratorType='TRIM_HORIZON')['ShardIterator']
        for _ in range(20):
            page = kinesis.get_records(ShardIterator=iterator, Limit=1000)
            for record in page['Records']:
                data = record['Data']
                envelopes.extend(unpack_envelopes(data if isinstance(data, bytes) else base64.b64decode(data)))
            iterator = page.get('NextShardIterator')
            if not iterator or not page['Records']:
                break
    return events_from(envelopes, run)


def _archive_events(run: str) -> tuple[list[dict[str, Any]], list[str]]:
    s3 = _client('s3')
    keys = [item['Key'] for item in _safe_call(lambda: s3.list_objects_v2(Bucket=BUCKET, Prefix=ARCHIVE_PREFIX)
                                                .get('Contents', []), [])]
    envelopes = []
    for key in keys:
        envelopes.extend(unpack_envelopes(s3.get_object(Bucket=BUCKET, Key=key)['Body'].read()))
    return events_from(envelopes, run), keys


def _run() -> str | None:
    return (_marked('ship-logs') or {}).get('run')


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_encrypted() -> tuple[bool, str]:
    key = _key_arn()
    if not key:
        return False, f'No key with the alias {KEY_ALIAS} yet.'
    for group in GROUPS.values():
        found = _group(group)
        if not found:
            return False, f'{group} does not exist yet.'
        if found.get('kmsKeyId') != key:
            return False, f'{group} is not encrypted with {KEY_ALIAS}.'
        if found.get('retentionInDays') != RETENTION_DAYS:
            return False, f'{group} keeps events for {found.get("retentionInDays") or "ever"}, not {RETENTION_DAYS} days.'
    return True, f'Both log groups use {KEY_ALIAS} and keep events for {RETENTION_DAYS} days.'


def _check_metrics() -> tuple[bool, str]:
    for group in GROUPS.values():
        filters = {item['filterName']: item for item in _metric_filters(group)}
        transform = ((filters.get(METRIC_FILTER) or {}).get('metricTransformations') or [{}])[0]
        if transform.get('metricName') != ERROR_METRIC or 'Service' not in (transform.get('dimensions') or {}):
            return False, f'{group} has no {METRIC_FILTER} metric filter with a Service dimension.'
    states = _alarm_states()
    missing = [name for name in (*ALARMS.values(), INCIDENT_ALARM) if name not in states]
    if missing:
        return False, 'Missing alarms: ' + ', '.join(missing) + '.'
    return True, f'Both groups count errors per service, with an alarm each and the {INCIDENT_ALARM} composite alarm.'


def _check_stream() -> tuple[bool, str]:
    arn = _stream_arn()
    if not arn:
        return False, f'The {ERROR_STREAM} stream does not exist yet.'
    for group in GROUPS.values():
        found = _subscriptions(group).get(ERROR_SUBSCRIPTION) or {}
        if found.get('destinationArn') != arn or found.get('filterPattern') != ERROR_PATTERN:
            return False, f'{group} does not send its errors to {ERROR_STREAM} yet.'
    return True, f'Both groups send events matching {ERROR_PATTERN} to {ERROR_STREAM}.'


def _check_archive() -> tuple[bool, str]:
    if _archive_status() != 'ACTIVE':
        return False, f'The {ARCHIVE_STREAM} Firehose stream is not active yet.'
    if not any(rule.get('ID') == LIFECYCLE_RULE['ID'] for rule in _lifecycle()):
        return False, f'{BUCKET} has no {LIFECYCLE_RULE["ID"]} lifecycle rule.'
    for group in GROUPS.values():
        found = _subscriptions(group).get(ARCHIVE_SUBSCRIPTION) or {}
        if found.get('destinationArn') != ARCHIVE_ARN:
            return False, f'{group} does not send its events to {ARCHIVE_STREAM} yet.'
    return True, f'Both groups send every event to {ARCHIVE_STREAM}, which writes to s3://{BUCKET}/{ARCHIVE_PREFIX}.'


def _check_shipped() -> tuple[bool, str]:
    marker = _marked('ship-logs') or {}
    if not marker:
        return False, 'Ship the log lines first.'
    if marker.get('streamedByService') != EXPECTED_ERRORS:
        return False, f'Kinesis had {marker.get("streamedByService")} errors, expected {EXPECTED_ERRORS}.'
    if marker.get('streamedOtherLevels'):
        return False, f'Kinesis also carried {marker["streamedOtherLevels"]} non-error lines.'
    return True, 'Kinesis carried only the error lines, from both log groups.'


def _check_alarm() -> tuple[bool, str]:
    marker = _marked('error-alarm') or {}
    states = marker.get('states') or {}
    if states.get(ALARMS['payments']) != 'ALARM' or states.get(INCIDENT_ALARM) != 'ALARM':
        return False, f'The payments alarm and {INCIDENT_ALARM} are not in ALARM yet: {states or "not checked"}.'
    if states.get(ALARMS['orders']) == 'ALARM':
        return False, 'The orders alarm fired too, with only two errors.'
    return True, f'Payments errors fired its alarm and {INCIDENT_ALARM}; orders stayed below the threshold.'


def _check_delivered() -> tuple[bool, str]:
    marker = _marked('archive-delivered') or {}
    if marker.get('archived') != EXPECTED_EVENTS:
        return False, f'The archive holds {marker.get("archived", 0)} of this run\'s {EXPECTED_EVENTS} events.'
    return True, f'All {EXPECTED_EVENTS} events, every level from both services, are in the S3 archive.'


def _check_insights() -> tuple[bool, str]:
    marker = _marked('insights-query') or {}
    if marker.get('errorsByService') != EXPECTED_ERRORS:
        return False, f'The query found {marker.get("errorsByService") or "no"} errors, expected {EXPECTED_ERRORS}.'
    return True, 'One query across both log groups found every error, counted by service.'


def _check_central() -> tuple[bool, str]:
    destination = (_marked('central-account') or {}).get('destination') or {}
    if not destination.get('accessPolicy'):
        return False, f'The {DESTINATION} destination has no access policy yet.'
    principals = [statement.get('Principal', {}).get('AWS')
                  for statement in json.loads(destination['accessPolicy']).get('Statement', [])]
    if ACCOUNT not in principals:
        return False, f'The {DESTINATION} policy does not let account {ACCOUNT} subscribe.'
    stream = _safe_call(lambda: _client('cloudwatch').get_metric_stream(Name=METRIC_STREAM))
    if not stream:
        return False, f'The {METRIC_STREAM} metric stream does not exist yet.'
    return True, f'{DESTINATION} accepts subscriptions from {ACCOUNT}, and {METRIC_STREAM} is stored.'


CHECKS = {
    'encrypted-log-groups': _check_encrypted,
    'service-metrics': _check_metrics,
    'error-stream': _check_stream,
    'log-archive': _check_archive,
    'ship-logs': _check_shipped,
    'error-alarm': _check_alarm,
    'archive-delivered': _check_delivered,
    'insights-query': _check_insights,
    'central-account': _check_central,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _ensure_role(name: str, trust: dict[str, Any], policy: dict[str, Any], description: str) -> None:
    iam = _client('iam')
    try:
        iam.get_role(RoleName=name)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description)
    iam.put_role_policy(RoleName=name, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(policy))


def _ensure_key() -> str:
    arn = _key_arn()
    kms = _client('kms')
    if not arn:
        metadata = kms.create_key(Description='CloudWatch Logs for the lab services', Policy=json.dumps(key_policy()),
                                  Tags=[{'TagKey': 'Lab', 'TagValue': LAB_KEY}])['KeyMetadata']
        kms.create_alias(AliasName=KEY_ALIAS, TargetKeyId=metadata['KeyId'])
        arn = metadata['Arn']
    kms.enable_key(KeyId=arn)
    return arn


def _write_with_disabled_key(arn: str) -> str:
    """Disable the key, try one write, and enable the key again. Returns what happened."""
    kms, logs = _client('kms'), _client('logs')
    kms.disable_key(KeyId=arn)
    try:
        logs.put_log_events(logGroupName=GROUPS['orders'], logStreamName=STREAM, logEvents=[{
            'timestamp': int(time.time() * 1000),
            'message': json.dumps({'service': 'orders', 'level': 'INFO', 'message': 'key check'})}])
        outcome = 'accepted (Floci does not encrypt events with the key)'
    except ClientError as exc:
        outcome = f'refused: {_code(exc)}'
    finally:
        kms.enable_key(KeyId=arn)
    return outcome


def step_encrypted_log_groups() -> dict[str, Any]:
    started = time.perf_counter()
    arn = _ensure_key()
    logs = _client('logs')
    for group in GROUPS.values():
        found = _group(group)
        if not found:
            logs.create_log_group(logGroupName=group, kmsKeyId=arn, tags={'Lab': LAB_KEY})
        elif found.get('kmsKeyId') != arn:
            logs.associate_kms_key(logGroupName=group, kmsKeyId=arn)
        logs.put_retention_policy(logGroupName=group, retentionInDays=RETENTION_DAYS)
        try:
            logs.create_log_stream(logGroupName=group, logStreamName=STREAM)
        except ClientError as exc:
            if _code(exc) != 'ResourceAlreadyExistsException':
                raise
    disabled_write = _write_with_disabled_key(arn)
    response = {
        'Key': {'Alias': KEY_ALIAS, 'Arn': arn, 'Policy': key_policy()},
        'LogGroups': [{field: (_group(group) or {}).get(field)
                       for field in ('logGroupName', 'kmsKeyId', 'retentionInDays')} for group in GROUPS.values()],
        'WriteWhileKeyDisabled': disabled_write,
    }
    verified, message = _check_encrypted()
    if verified:
        message += f' A write while the key was disabled was {disabled_write}.'
    return _result('encrypted-log-groups', ENCRYPT_COMMAND, response, verified, message, started)


def step_service_metrics() -> dict[str, Any]:
    started = time.perf_counter()
    logs, cloudwatch = _client('logs'), _client('cloudwatch')
    for group in GROUPS.values():
        logs.put_metric_filter(logGroupName=group, filterName=METRIC_FILTER, filterPattern=ERROR_PATTERN,
                               metricTransformations=[{
                                   'metricName': ERROR_METRIC, 'metricNamespace': NAMESPACE, 'metricValue': '1',
                                   'unit': 'Count', 'dimensions': {'Service': '$.service'}}])
    for name, alarm in ALARMS.items():
        cloudwatch.put_metric_alarm(
            AlarmName=alarm, AlarmDescription=f'{name} logged {ALARM_THRESHOLD} or more errors in a minute',
            Namespace=NAMESPACE, MetricName=ERROR_METRIC, Dimensions=[{'Name': 'Service', 'Value': name}],
            Statistic='Sum', Period=60, EvaluationPeriods=1, Threshold=ALARM_THRESHOLD,
            ComparisonOperator='GreaterThanOrEqualToThreshold', TreatMissingData='notBreaching')
    cloudwatch.put_composite_alarm(AlarmName=INCIDENT_ALARM, AlarmRule=INCIDENT_RULE,
                                   AlarmDescription='Any service is failing: one page for on-call')
    response = {
        'MetricFilters': {group: _metric_filters(group) for group in GROUPS.values()},
        'Alarms': _alarm_states(),
        'CompositeRule': INCIDENT_RULE,
    }
    verified, message = _check_metrics()
    return _result('service-metrics', METRICS_COMMAND, response, verified, message, started)


def step_error_stream() -> dict[str, Any]:
    started = time.perf_counter()
    kinesis = _client('kinesis')
    if not _stream_arn():
        kinesis.create_stream(StreamName=ERROR_STREAM, ShardCount=1)
    _wait(lambda: _safe_call(lambda: kinesis.describe_stream_summary(StreamName=ERROR_STREAM)
                             ['StreamDescriptionSummary']['StreamStatus']) == 'ACTIVE', 30)
    _ensure_role(LOGS_ROLE, LOGS_TRUST, LOGS_POLICY, 'CloudWatch Logs delivers to Kinesis and Firehose')
    arn = _stream_arn()
    logs = _client('logs')
    for group in GROUPS.values():
        logs.put_subscription_filter(logGroupName=group, filterName=ERROR_SUBSCRIPTION, filterPattern=ERROR_PATTERN,
                                     destinationArn=arn, roleArn=LOGS_ROLE_ARN)
    response = {'StreamArn': arn, 'Role': LOGS_ROLE_ARN,
                'SubscriptionFilters': {group: list(_subscriptions(group)) for group in GROUPS.values()}}
    verified, message = _check_stream()
    return _result('error-stream', STREAM_COMMAND, response, verified, message, started)


def step_log_archive() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_lifecycle_configuration(Bucket=BUCKET, LifecycleConfiguration={'Rules': [LIFECYCLE_RULE]})
    _ensure_role(FIREHOSE_ROLE, FIREHOSE_TRUST, FIREHOSE_POLICY, 'Firehose writes the log archive to S3')
    _ensure_role(LOGS_ROLE, LOGS_TRUST, LOGS_POLICY, 'CloudWatch Logs delivers to Kinesis and Firehose')
    firehose = _client('firehose')
    if not _archive_status():
        firehose.create_delivery_stream(
            DeliveryStreamName=ARCHIVE_STREAM, DeliveryStreamType='DirectPut',
            ExtendedS3DestinationConfiguration={
                'RoleARN': FIREHOSE_ROLE_ARN, 'BucketARN': f'arn:aws:s3:::{BUCKET}', 'Prefix': ARCHIVE_PREFIX,
                'BufferingHints': {'IntervalInSeconds': ARCHIVE_BUFFER_SECONDS, 'SizeInMBs': 1},
                'CompressionFormat': 'UNCOMPRESSED'})
    _wait(lambda: _archive_status() == 'ACTIVE', 30)
    logs = _client('logs')
    for group in GROUPS.values():
        logs.put_subscription_filter(logGroupName=group, filterName=ARCHIVE_SUBSCRIPTION, filterPattern='',
                                     destinationArn=ARCHIVE_ARN, roleArn=LOGS_ROLE_ARN)
    response = {'DeliveryStream': {'Name': ARCHIVE_STREAM, 'Status': _archive_status()},
                'Lifecycle': _lifecycle(),
                'SubscriptionFilters': {group: list(_subscriptions(group)) for group in GROUPS.values()}}
    verified, message = _check_archive()
    if verified:
        message += ' Floci applies the lifecycle expiration but not the storage class transitions.'
    return _result('log-archive', ARCHIVE_COMMAND, response, verified, message, started)


def step_ship_logs() -> dict[str, Any]:
    started = time.perf_counter()
    run = str(time.time_ns())
    logs = _client('logs')
    now = int(time.time() * 1000)
    for name, lines in log_lines(run).items():
        logs.put_log_events(logGroupName=GROUPS[name], logStreamName=STREAM, logEvents=[
            {'timestamp': now + index, 'message': line} for index, line in enumerate(lines)])
    streamed: list[dict[str, Any]] = []

    def all_errors_arrived() -> bool:
        streamed[:] = _stream_events(run)
        return len(streamed) >= sum(EXPECTED_ERRORS.values())

    _wait(all_errors_arrived, STREAM_WAIT_SECONDS)
    errors = [event for event in streamed if event.get('level') == 'ERROR']
    marker = {
        'run': run,
        'shippedAt': now,
        'written': {name: len(levels) for name, levels in TRAFFIC.items()},
        'streamedByService': count_by(errors, 'service'),
        'streamedOtherLevels': len(streamed) - len(errors),
        'streamedFromGroups': sorted({event['logGroup'] for event in streamed}),
    }
    _marker('ship-logs', marker)
    verified, message = _check_shipped()
    return _result('ship-logs', SHIP_COMMAND, {**marker, 'SampleEvents': streamed[:3]}, verified, message, started)


def step_error_alarm() -> dict[str, Any]:
    started = time.perf_counter()
    if not _run():
        return _result('error-alarm', ALARM_COMMAND, {}, False, 'Ship the log lines first.', started)
    states: dict[str, str] = {}

    def fired() -> bool:
        states.update(_alarm_states())
        return states.get(ALARMS['payments']) == 'ALARM' and states.get(INCIDENT_ALARM) == 'ALARM'

    _wait(fired, ALARM_WAIT_SECONDS, delay=5)
    marker = {'states': dict(states), 'errorSums': _error_sums()}
    _marker('error-alarm', marker)
    verified, message = _check_alarm()
    return _result('error-alarm', ALARM_COMMAND, marker, verified, message, started)


def step_archive_delivered() -> dict[str, Any]:
    started = time.perf_counter()
    run = _run()
    if not run:
        return _result('archive-delivered', DELIVERED_COMMAND, {}, False, 'Ship the log lines first.', started)
    archived: list[dict[str, Any]] = []
    keys: list[str] = []

    def delivered() -> bool:
        found, found_keys = _archive_events(run)
        archived[:], keys[:] = found, found_keys
        return len(archived) >= EXPECTED_EVENTS

    _wait(delivered, ARCHIVE_WAIT_SECONDS, delay=5)
    marker = {
        'archived': len(archived),
        'byService': count_by(archived, 'service'),
        'byLevel': count_by(archived, 'level'),
        'objects': keys[-5:],
    }
    _marker('archive-delivered', marker)
    verified, message = _check_delivered()
    return _result('archive-delivered', DELIVERED_COMMAND, {**marker, 'Lifecycle': _lifecycle()}, verified, message,
                   started)


def _run_query(query: str, start: int) -> tuple[str, list[list[dict[str, str]]], str | None]:
    logs = _client('logs')
    try:
        query_id = logs.start_query(logGroupNames=list(GROUPS.values()), startTime=start,
                                    endTime=int(time.time()) + 60, queryString=query)['queryId']
    except ClientError as exc:
        return 'Refused', [], f'{_code(exc)}: {exc.response.get("Error", {}).get("Message", "")}'
    response: dict[str, Any] = {}

    def finished() -> bool:
        response.update(logs.get_query_results(queryId=query_id))
        return response.get('status') not in ('Scheduled', 'Running')

    _wait(finished, QUERY_WAIT_SECONDS)
    return response.get('status', 'Unknown'), response.get('results') or [], None


def step_insights_query() -> dict[str, Any]:
    started = time.perf_counter()
    run = _run()
    if not run:
        return _result('insights-query', INSIGHTS_COMMAND, {}, False, 'Ship the log lines first.', started)
    start = int((_marked('ship-logs') or {}).get('shippedAt', time.time() * 1000) / 1000) - 60
    status, results, error = _run_query(INSIGHTS_QUERY, start)
    rows = [row for row in insights_rows(results) if row.get('run') == run]
    stats_status, stats_results, stats_error = _run_query('filter level = "ERROR" | stats count(*) by service', start)
    marker = {
        'status': status,
        'errorRows': len(rows),
        'errorsByService': count_by(rows, 'service'),
        'statsQuery': {'status': stats_status, 'rows': len(stats_results), 'error': stats_error},
    }
    _marker('insights-query', marker)
    verified, message = _check_insights()
    if error:
        message = f'The query was refused: {error}'
    return _result('insights-query', INSIGHTS_COMMAND, {**marker, 'SampleRows': rows[:3]}, verified, message, started)


def step_central_account() -> dict[str, Any]:
    started = time.perf_counter()
    logs = _client('logs')
    _ensure_role(LOGS_ROLE, LOGS_TRUST, LOGS_POLICY, 'CloudWatch Logs delivers to Kinesis and Firehose')
    target = _stream_arn()
    if not target:
        return _result('central-account', CENTRAL_COMMAND, {}, False,
                       f'Create the {ERROR_STREAM} stream first.', started)
    destination = logs.put_destination(destinationName=DESTINATION, targetArn=target,
                                       roleArn=LOGS_ROLE_ARN)['destination']
    logs.put_destination_policy(destinationName=DESTINATION,
                                accessPolicy=json.dumps(destination_policy(destination['arn'])))
    # Floci has no DescribeDestinations, and PutDestination is idempotent, so putting it again reads the policy back.
    stored = logs.put_destination(destinationName=DESTINATION, targetArn=target, roleArn=LOGS_ROLE_ARN)['destination']
    _marker('central-account', {'destination': stored})
    cloudwatch = _client('cloudwatch')
    cloudwatch.put_metric_stream(Name=METRIC_STREAM, FirehoseArn=ARCHIVE_ARN, RoleArn=FIREHOSE_ROLE_ARN,
                                 OutputFormat='json', IncludeFilters=[{'Namespace': NAMESPACE}])
    response = {
        'Destination': stored,
        'MetricStream': _safe_call(lambda: cloudwatch.get_metric_stream(Name=METRIC_STREAM)),
    }
    verified, message = _check_central()
    return _result('central-account', CENTRAL_COMMAND, response, verified, message, started)


RUNNERS = {
    'encrypted-log-groups': step_encrypted_log_groups,
    'service-metrics': step_service_metrics,
    'error-stream': step_error_stream,
    'log-archive': step_log_archive,
    'ship-logs': step_ship_logs,
    'error-alarm': step_error_alarm,
    'archive-delivered': step_archive_delivered,
    'insights-query': step_insights_query,
    'central-account': step_central_account,
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
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError):
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
    """Remove the metric stream, destination, alarms, log groups, Firehose and Kinesis streams, bucket, roles, and key."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or 'DoesNotExist' in code or code == 'UnsupportedOperation':
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudwatch, logs = _client('cloudwatch'), _client('logs')
    if _safe_call(lambda: cloudwatch.get_metric_stream(Name=METRIC_STREAM)):
        attempt(f'metric-stream:{METRIC_STREAM}', lambda: cloudwatch.delete_metric_stream(Name=METRIC_STREAM))
    if _marked('central-account'):
        # Floci 2.4.0 has no DeleteDestination; the stored destination is replaced the next time the lab runs.
        attempt(f'destination:{DESTINATION}', lambda: logs.delete_destination(destinationName=DESTINATION))
    states = _alarm_states()
    if INCIDENT_ALARM in states:
        attempt(f'alarm:{INCIDENT_ALARM}', lambda: cloudwatch.delete_alarms(AlarmNames=[INCIDENT_ALARM]))
    existing = [name for name in ALARMS.values() if name in states]
    if existing:
        attempt('alarms:' + ','.join(existing), lambda: cloudwatch.delete_alarms(AlarmNames=existing))
    for group in GROUPS.values():
        if _group(group):
            attempt(f'log-group:{group}', lambda group=group: logs.delete_log_group(logGroupName=group))
    if _archive_status():
        attempt(f'firehose:{ARCHIVE_STREAM}', lambda: _client('firehose').delete_delivery_stream(
            DeliveryStreamName=ARCHIVE_STREAM))
    if _stream_arn():
        attempt(f'kinesis:{ERROR_STREAM}', lambda: _client('kinesis').delete_stream(
            StreamName=ERROR_STREAM, EnforceConsumerDeletion=True))
    s3 = _client('s3')
    if _safe_call(lambda: s3.head_bucket(Bucket=BUCKET)) is not None:
        for item in _safe_call(lambda: s3.list_objects_v2(Bucket=BUCKET).get('Contents', []), []):
            attempt(f'object:{item["Key"]}', lambda key=item['Key']: s3.delete_object(Bucket=BUCKET, Key=key))
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))
    iam = _client('iam')
    for role in (LOGS_ROLE, FIREHOSE_ROLE):
        if _safe_call(lambda role=role: iam.get_role(RoleName=role)):
            attempt(f'role-policy:{role}', lambda role=role: iam.delete_role_policy(
                RoleName=role, PolicyName=INLINE_POLICY))
            attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))
    key = _key_arn()
    if key:
        kms = _client('kms')
        attempt(f'alias:{KEY_ALIAS}', lambda: kms.delete_alias(AliasName=KEY_ALIAS))
        attempt(f'key:{key}', lambda: kms.schedule_key_deletion(KeyId=key, PendingWindowInDays=7))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws cloudwatch delete-metric-stream --name {METRIC_STREAM}\n'
            f'aws logs delete-destination --destination-name {DESTINATION}\n'
            f'aws cloudwatch delete-alarms --alarm-names {INCIDENT_ALARM} {" ".join(ALARMS.values())}\n'
            + ''.join(f'aws logs delete-log-group --log-group-name {group}\n' for group in GROUPS.values())
            + f'aws firehose delete-delivery-stream --delivery-stream-name {ARCHIVE_STREAM}\n'
            f'aws kinesis delete-stream --stream-name {ERROR_STREAM}\n'
            f'aws s3 rb s3://{BUCKET} --force\n'
            f'aws kms delete-alias --alias-name {KEY_ALIAS} && aws kms schedule-key-deletion --key-id <key-id> '
            '--pending-window-in-days 7'
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
            else 'The metric stream, destination, alarms, log groups, streams, bucket, roles, and key are gone.',
        },
    }
