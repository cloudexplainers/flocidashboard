"""Serverless observability lab: structured JSON logs from a Lambda function, metric filters on JSON fields, a Logs
Insights query, a custom metric from code and an embedded metric format line, an alarm that targets SNS, a dashboard,
and active tracing.

Floci 2.3.0 delivers the function's output to its log group, turns JSON metric filters into real metrics (counts and
values with units), accepts PutMetricData from the function, evaluates alarms (a breached alarm reaches ALARM in about
15 seconds), and stores dashboards. It does not extract embedded metric format lines, its Logs Insights supports
fields, filter, sort and limit but not stats, alarm actions never notify SNS (newer Floci releases publish them, so
the alarm step subscribes a queue to the topic and reports whether the notification arrived), Lambda's JSON log format
and tracing settings are stored without effect, and X-Ray is not available. Each step says which of these it meets.
"""

from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from typing import Any
from urllib.parse import unquote

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudwatch'
LAB_KEY = 'serverless-observability'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

FUNCTION = 'lab-obs-orders'
ROLE_NAME = 'lab-obs-orders-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
ROLE_POLICY = 'lab-obs-orders-telemetry'
LOG_GROUP = f'/aws/lambda/{FUNCTION}'
RETENTION_DAYS = 14
RUNTIME = 'python3.12'
HANDLER = 'orders.handler'
STAGE = 'dev'
NAMESPACE = 'LabObs/Orders'
EMF_NAMESPACE = 'LabObs/OrdersEMF'

ERRORS_FILTER = 'lab-obs-order-errors'
LATENCY_FILTER = 'lab-obs-order-latency'
ERRORS_PATTERN = '{ $.level = "ERROR" }'
LATENCY_PATTERN = '{ $.latency_ms > 0 }'
ERRORS_METRIC = 'OrderErrors'
LATENCY_METRIC = 'OrderLatency'
ORDERS_METRIC = 'OrdersPlaced'

TRAFFIC_ORDERS = 12
BAD_EVERY = 4           # every fourth order is invalid: 3 of 12
ALARM_BURST = 3
TOPIC = 'lab-obs-alerts'
TOPIC_ARN = f'arn:aws:sns:{REGION}:{ACCOUNT}:{TOPIC}'
INBOX = 'lab-obs-alerts-inbox'
INBOX_ARN = f'arn:aws:sqs:{REGION}:{ACCOUNT}:{INBOX}'
NOTIFICATION_WAIT_SECONDS = 10
INBOX_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Sid': 'AlarmTopic', 'Effect': 'Allow', 'Principal': {'Service': 'sns.amazonaws.com'},
        'Action': 'sqs:SendMessage', 'Resource': INBOX_ARN,
        'Condition': {'ArnEquals': {'aws:SourceArn': TOPIC_ARN}},
    }],
}
ALARM = 'lab-obs-order-errors'
ALARM_THRESHOLD = 3
ALARM_WAIT_SECONDS = 120
DASHBOARD = 'lab-obs-orders'
INSIGHTS_QUERY = ('fields @timestamp, order_id, reason, latency_ms | filter level = "ERROR" '
                  '| sort @timestamp desc | limit 20')
STATS_QUERY = 'stats count(*) as orders, avg(latency_ms) as avg_latency by level'

LAMBDA_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}


def role_policy(with_tracing: bool) -> dict[str, Any]:
    statements: list[dict[str, Any]] = [
        {
            'Sid': 'Logs',
            'Effect': 'Allow',
            'Action': ['logs:CreateLogStream', 'logs:PutLogEvents'],
            'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{LOG_GROUP}:*',
        },
        {
            'Sid': 'OrderMetrics',
            'Effect': 'Allow',
            'Action': 'cloudwatch:PutMetricData',
            'Resource': '*',
        },
    ]
    if with_tracing:
        statements.append({'Sid': 'Tracing', 'Effect': 'Allow',
                           'Action': ['xray:PutTraceSegments', 'xray:PutTelemetryRecords'], 'Resource': '*'})
    return {'Version': '2012-10-17', 'Statement': statements}


ORDERS_SOURCE = '''"""Accept or reject an order, logging one JSON object per line and publishing an orders metric."""
import json
import os
import time

import boto3

cloudwatch = boto3.client("cloudwatch")


def log(level, message, **fields):
    print(json.dumps({"level": level, "message": message, **fields}))


def handler(event, context):
    order_id = event.get("order_id")
    fields = {
        "order_id": order_id,
        "run_id": event.get("run_id"),
        "request_id": context.aws_request_id,
        "latency_ms": int(event.get("simulated_latency_ms", 50)),
    }
    if not order_id or float(event.get("amount", 0)) <= 0:
        reason = "amount must be positive" if order_id else "order_id is required"
        log("ERROR", "order rejected", reason=reason, **fields)
        return {"status": "REJECTED", "reason": reason}
    stage = os.environ["STAGE"]
    cloudwatch.put_metric_data(Namespace=os.environ["METRIC_NAMESPACE"], MetricData=[{
        "MetricName": "OrdersPlaced", "Value": 1, "Unit": "Count",
        "Dimensions": [{"Name": "Environment", "Value": stage}],
    }])
    # Embedded metric format: CloudWatch Logs turns this line into an OrderValue metric, with no API call.
    print(json.dumps({
        "_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
            "Namespace": os.environ["EMF_NAMESPACE"], "Dimensions": [["Environment"]],
            "Metrics": [{"Name": "OrderValue", "Unit": "None"}],
        }]},
        "Environment": stage, "OrderValue": float(event["amount"]), "order_id": order_id,
        "run_id": event.get("run_id"),
    }))
    log("INFO", "order accepted", amount=float(event["amount"]), **fields)
    return {"status": "ACCEPTED", "order_id": order_id}
'''

ENVIRONMENT = {'STAGE': STAGE, 'METRIC_NAMESPACE': NAMESPACE, 'EMF_NAMESPACE': EMF_NAMESPACE}
LOGGING_CONFIG = {'LogFormat': 'JSON', 'ApplicationLogLevel': 'INFO', 'SystemLogLevel': 'WARN', 'LogGroup': LOG_GROUP}

CREATE_COMMAND = (
    f'aws logs create-log-group --log-group-name {LOG_GROUP}\n'
    f'aws logs put-retention-policy --log-group-name {LOG_GROUP} --retention-in-days {RETENTION_DAYS}\n'
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} '
    '--policy-document file://telemetry.json   # logs + PutMetricData\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime {RUNTIME} --handler {HANDLER} '
    f'--role {ROLE_ARN} --zip-file fileb://orders.zip '
    f'--environment "Variables={{STAGE={STAGE},METRIC_NAMESPACE={NAMESPACE},EMF_NAMESPACE={EMF_NAMESPACE}}}" '
    '--logging-config LogFormat=JSON,ApplicationLogLevel=INFO,SystemLogLevel=WARN'
)
FILTERS_COMMAND = (
    f'aws logs put-metric-filter --log-group-name {LOG_GROUP} --filter-name {ERRORS_FILTER} '
    f"--filter-pattern '{ERRORS_PATTERN}' --metric-transformations "
    f'metricName={ERRORS_METRIC},metricNamespace={NAMESPACE},metricValue=1,defaultValue=0\n'
    f'aws logs put-metric-filter --log-group-name {LOG_GROUP} --filter-name {LATENCY_FILTER} '
    f"--filter-pattern '{LATENCY_PATTERN}' --metric-transformations "
    f"'metricName={LATENCY_METRIC},metricNamespace={NAMESPACE},metricValue=$.latency_ms,unit=Milliseconds'"
)
TRAFFIC_COMMAND = (
    f'for i in $(seq 1 {TRAFFIC_ORDERS}); do\n'
    f'  aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    '--payload "{\\"order_id\\":\\"ord-$i\\",\\"amount\\":25,\\"simulated_latency_ms\\":$((80 + 15 * i))}" out.json\n'
    f'done   # every {BAD_EVERY}th order sends amount 0 and is rejected\n'
    f'aws logs tail {LOG_GROUP} --since 5m'
)
METRICS_COMMAND = (
    f'aws cloudwatch get-metric-statistics --namespace {NAMESPACE} --metric-name {ERRORS_METRIC} '
    '--statistics Sum --period 60 --start-time <15 minutes ago> --end-time <now>\n'
    f'aws cloudwatch get-metric-statistics --namespace {NAMESPACE} --metric-name {LATENCY_METRIC} '
    '--statistics Average Maximum --period 60 --start-time <15 minutes ago> --end-time <now>\n'
    f'aws cloudwatch get-metric-statistics --namespace {NAMESPACE} --metric-name {ORDERS_METRIC} '
    f'--dimensions Name=Environment,Value={STAGE} --statistics Sum --period 60 ...\n'
    f'aws cloudwatch list-metrics --namespace {EMF_NAMESPACE}   # the metric from the EMF line'
)
INSIGHTS_COMMAND = (
    f'aws logs start-query --log-group-name {LOG_GROUP} --start-time <15 minutes ago> --end-time <now> '
    f"--query-string '{INSIGHTS_QUERY}'\n"
    'aws logs get-query-results --query-id <id>\n'
    f"# then: '{STATS_QUERY}'"
)
ALARM_COMMAND = (
    f'aws sns create-topic --name {TOPIC}\n'
    f'aws sqs create-queue --queue-name {INBOX} --attributes file://inbox-policy.json\n'
    f'aws sns subscribe --topic-arn {TOPIC_ARN} --protocol sqs --notification-endpoint {INBOX_ARN}\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {ALARM} --namespace {NAMESPACE} --metric-name {ERRORS_METRIC} '
    f'--statistic Sum --period 60 --evaluation-periods 1 --threshold {ALARM_THRESHOLD} '
    '--comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching '
    f'--alarm-actions {TOPIC_ARN} --ok-actions {TOPIC_ARN}\n'
    f'# send {ALARM_BURST} rejected orders, then:\n'
    f'aws cloudwatch describe-alarms --alarm-names {ALARM} --query "MetricAlarms[0].StateValue"\n'
    f'aws sqs receive-message --queue-url <{INBOX} URL> --wait-time-seconds {NOTIFICATION_WAIT_SECONDS}'
)
DASHBOARD_COMMAND = (
    f'aws cloudwatch put-dashboard --dashboard-name {DASHBOARD} --dashboard-body file://dashboard.json\n'
    f'aws cloudwatch get-dashboard --dashboard-name {DASHBOARD}'
)
TRACING_COMMAND = (
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} '
    '--policy-document file://telemetry.json   # adds xray:PutTraceSegments\n'
    f'aws lambda update-function-configuration --function-name {FUNCTION} --tracing-config Mode=Active\n'
    'aws xray get-trace-summaries --start-time <5 minutes ago> --end-time <now>'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Instrument a serverless app with structured logs, metric filters, custom metrics, alarms, and dashboards',
    'description': (
        'Log JSON from a Lambda function, turn log fields into metrics with metric filters, query errors with Logs '
        'Insights, publish a business metric from code and with the embedded metric format, alarm on errors through '
        'SNS, build a dashboard, and turn on active tracing.'
    ),
    'steps': [
        {
            'key': 'create-function',
            'title': 'Create an order function that logs JSON',
            'command': CREATE_COMMAND,
            'explanation': (
                'Structured logs are one JSON object per line, with the same field names everywhere (level, '
                'request_id, order_id, latency_ms), so metric filters and queries can read fields instead of '
                'parsing text. Creating the log group yourself lets you set retention (logs are kept forever by '
                'default) before the first invocation. Lambda\'s advanced logging controls can also write JSON and '
                'filter by level. The role writes its logs and may call PutMetricData, which has no resource ARN; on AWS, add a '
                'cloudwatch:namespace condition to keep the function in its own namespace. How Floci emulates this: '
                'the function runs in a real container and its output reaches the log group. Floci stores the JSON '
                'log format and levels but still writes plain text, so the handler formats its own JSON. Floci '
                'denies PutMetricData when the policy carries the namespace condition, so the role leaves it out.'
            ),
            'artifact_label': 'orders.py',
            'artifact': ORDERS_SOURCE,
        },
        {
            'key': 'metric-filters',
            'title': 'Turn log fields into metrics with metric filters',
            'command': FILTERS_COMMAND,
            'explanation': (
                'A metric filter matches log events as they arrive and publishes a metric: a count of matching '
                f'lines ({ERRORS_PATTERN}) or a value taken from a field ($.latency_ms, with a unit). defaultValue '
                '0 reports zero when nothing matches, so the metric has no gaps. Filters only see events ingested '
                'after they exist, which is why they come before the traffic. How Floci emulates this: JSON metric '
                'filters are real and publish datapoints.'
            ),
        },
        {
            'key': 'send-traffic',
            'title': f'Send {TRAFFIC_ORDERS} orders and read the structured logs',
            'command': TRAFFIC_COMMAND,
            'explanation': (
                f'{TRAFFIC_ORDERS} orders with rising latency; every {BAD_EVERY}th one is invalid and logged at ERROR '
                'with a reason. Each line carries the request ID, which ties a log line to one invocation. How '
                'Floci emulates this: every invocation really runs; the step reads the lines back from the log '
                'group by this run\'s ID.'
            ),
        },
        {
            'key': 'read-metrics',
            'title': 'Read the filter metrics, the custom metric, and the EMF metric',
            'command': METRICS_COMMAND,
            'explanation': (
                'The two filter metrics come from the logs. OrdersPlaced comes from PutMetricData in the handler, '
                'with an Environment dimension; each call costs an API request and adds latency. The embedded metric '
                'format (EMF) is the cheaper alternative: the function prints a JSON line with an _aws block and '
                'CloudWatch Logs extracts the metric asynchronously. How Floci emulates this: the filter metrics and '
                'PutMetricData are real. Floci does not extract EMF lines, so the EMF metric never appears; the step '
                'checks the line was logged and says so.'
            ),
        },
        {
            'key': 'logs-insights',
            'title': 'Find the rejected orders with Logs Insights',
            'command': INSIGHTS_COMMAND,
            'explanation': (
                'Logs Insights queries discover JSON fields automatically. The query keeps ERROR lines and shows '
                'which orders failed and why, newest first. stats aggregates (count, avg, percentiles) by a field, '
                'which is how you would chart errors by level. How Floci emulates this: fields, filter, sort, and '
                'limit work. Floci rejects stats queries, so the step runs one, reports that it failed, and counts '
                'the levels itself.'
            ),
        },
        {
            'key': 'error-alarm',
            'title': f'Alarm through SNS when {ALARM_THRESHOLD} orders fail in a minute',
            'command': ALARM_COMMAND,
            'explanation': (
                f'The alarm evaluates the {ERRORS_METRIC} sum every minute. TreatMissingData notBreaching keeps it '
                'OK when no logs arrive. AlarmActions and OKActions notify the SNS topic, which can email the team '
                f'or trigger automation; here a queue subscribed to the topic stands in for the team\'s inbox, and '
                'its policy lets only this topic send to it. The step sends '
                f'{ALARM_BURST} rejected orders so the current minute breaches. How Floci emulates this: the alarm '
                'is evaluated for real and moves to ALARM within about 15 seconds. Older Floci releases do not run alarm '
                'actions, so nothing reaches the queue; newer releases publish the notification. The step '
                'checks the state and the configured actions, and reports whether the ALARM message arrived.'
            ),
            'artifact_label': 'inbox-policy.json',
            'artifact': json.dumps(INBOX_POLICY, indent=2),
        },
        {
            'key': 'dashboard',
            'title': 'Build a dashboard for the order service',
            'command': DASHBOARD_COMMAND,
            'explanation': (
                'A dashboard collects the signals someone on call needs on one page: errors, latency (average and '
                'p90), orders placed, the alarm, and a Logs Insights table of recent rejections. Its body is JSON, so '
                'it can live in source control and be deployed with CloudFormation. How Floci emulates this: the '
                'dashboard body is stored and returned as sent.'
            ),
        },
        {
            'key': 'active-tracing',
            'title': 'Turn on active tracing',
            'command': TRACING_COMMAND,
            'explanation': (
                'With active tracing, Lambda sends a trace segment for each invocation to AWS X-Ray, and the X-Ray '
                'SDK (or AWS Distro for OpenTelemetry) adds subsegments for downstream calls such as PutMetricData. '
                'Annotations are indexed key-value pairs you can filter traces by; metadata is not indexed. The '
                'service map shows each component\'s latency and errors. The role needs xray:PutTraceSegments. How '
                'Floci emulates this: the tracing setting is stored, but Floci has no X-Ray service, so the step '
                'checks the setting and reports that the trace API is unavailable.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    factory = FlociClientFactory()
    if name == 'lambda':
        factory.config = factory.config.merge(Config(read_timeout=60, retries={'max_attempts': 0}))
    return factory.client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _not_found(exc: ClientError) -> bool:
    code = _code(exc)
    return code in {'ResourceNotFoundException', 'NoSuchEntity', 'NotFound', 'ResourceNotFound'} or 'NotFound' in code


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


def _missing(step: str, command: str, message: str, started: float) -> dict[str, Any]:
    return _result(step, command, {'error': message}, False, message, started)


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('orders.py', date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, ORDERS_SOURCE)
    return buffer.getvalue()


# ── Lookups ──────────────────────────────────────────────────────────────────

def _function() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_configuration(FunctionName=FUNCTION)
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise


def _wait_updated() -> None:
    _wait(lambda: (_function() or {}).get('State') in (None, 'Active') and
          (_function() or {}).get('LastUpdateStatus') in (None, 'Successful', 'Failed'), timeout=30)


def _log_group() -> dict[str, Any] | None:
    groups = _client('logs').describe_log_groups(logGroupNamePrefix=LOG_GROUP).get('logGroups', [])
    return next((group for group in groups if group.get('logGroupName') == LOG_GROUP), None)


def _filters() -> dict[str, dict[str, Any]]:
    try:
        found = _client('logs').describe_metric_filters(logGroupName=LOG_GROUP).get('metricFilters', [])
    except ClientError as exc:
        if _not_found(exc):
            return {}
        raise
    return {item['filterName']: item for item in found}


def _role_exists() -> bool:
    try:
        _client('iam').get_role(RoleName=ROLE_NAME)
        return True
    except ClientError as exc:
        if _not_found(exc):
            return False
        raise


def _role_policy() -> dict[str, Any] | None:
    try:
        document = _client('iam').get_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY)['PolicyDocument']
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    if isinstance(document, str):
        document = json.loads(unquote(document))
    return document


def _alarm() -> dict[str, Any] | None:
    alarms = _client('cloudwatch').describe_alarms(AlarmNames=[ALARM]).get('MetricAlarms', [])
    return alarms[0] if alarms else None


def _topic_exists() -> bool:
    topics = _client('sns').list_topics().get('Topics', [])
    return any(item.get('TopicArn') == TOPIC_ARN for item in topics)


def _inbox_url() -> str | None:
    try:
        return _client('sqs').get_queue_url(QueueName=INBOX)['QueueUrl']
    except ClientError as exc:
        if _not_found(exc) or 'NonExistentQueue' in _code(exc):
            return None
        raise


def _alarm_notification(sqs, queue_url: str) -> dict[str, Any] | None:
    """The ALARM notification the topic delivered to the inbox queue, or None if none arrived in time."""
    found: dict[str, Any] = {}

    def arrived() -> bool:
        response = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=1)
        for item in response.get('Messages', []):
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=item['ReceiptHandle'])
            try:
                envelope = json.loads(item.get('Body') or '{}')
                body = json.loads(envelope.get('Message') or '{}')
            except (TypeError, ValueError, AttributeError):
                continue
            if isinstance(body, dict) and body.get('AlarmName') == ALARM and body.get('NewStateValue') == 'ALARM':
                found.update(Subject=envelope.get('Subject'), NewStateValue=body['NewStateValue'],
                             NewStateReason=body.get('NewStateReason'))
        return bool(found)

    _wait(arrived, timeout=NOTIFICATION_WAIT_SECONDS, delay=0)
    return found or None


def _dashboard() -> dict[str, Any] | None:
    try:
        response = _client('cloudwatch').get_dashboard(DashboardName=DASHBOARD)
    except ClientError as exc:
        if _not_found(exc) or _code(exc) == 'ResourceNotFound':
            return None
        raise
    try:
        return json.loads(response.get('DashboardBody') or '{}')
    except ValueError:
        return {}


def _invoke(payload: dict[str, Any]) -> dict[str, Any]:
    response = _client('lambda').invoke(FunctionName=FUNCTION, Payload=json.dumps(payload).encode())
    body = json.loads(response['Payload'].read() or b'null')
    if response.get('FunctionError'):
        raise RuntimeError(f'{FUNCTION} failed: {body}')
    return body


def _order(run_id: str, index: int, bad: bool) -> dict[str, Any]:
    return {'order_id': f'ord-{run_id}-{index:02d}', 'run_id': run_id, 'amount': 0 if bad else 25,
            'simulated_latency_ms': 80 + 15 * index}


def _log_lines(run_id: str, since_ms: int) -> list[dict[str, Any]]:
    """JSON log lines that carry this run's ID. A plain-term pattern is used because Floci ignores JSON patterns
    in FilterLogEvents."""
    lines: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {'logGroupName': LOG_GROUP, 'startTime': since_ms, 'filterPattern': f'"{run_id}"'}
    for _ in range(20):
        page = _client('logs').filter_log_events(**kwargs)
        for event in page.get('events', []):
            try:
                line = json.loads(event.get('message') or '')
            except ValueError:
                continue
            if isinstance(line, dict) and line.get('run_id') == run_id:
                lines.append(line)
        if not page.get('nextToken') or page.get('nextToken') == kwargs.get('nextToken'):
            break
        kwargs['nextToken'] = page['nextToken']
    return lines


def _emf_logged(run_id: str, since_ms: int) -> bool:
    kwargs: dict[str, Any] = {'logGroupName': LOG_GROUP, 'startTime': since_ms, 'filterPattern': f'"{run_id}"'}
    for event in _client('logs').filter_log_events(**kwargs).get('events', []):
        try:
            line = json.loads(event.get('message') or '')
        except ValueError:
            continue
        if isinstance(line, dict) and '_aws' in line:
            return True
    return False


def _statistic(metric: str, statistic: str, since: float, dimensions: list | None = None) -> float | None:
    response = _client('cloudwatch').get_metric_statistics(
        Namespace=NAMESPACE, MetricName=metric, Dimensions=dimensions or [], StartTime=since,
        EndTime=time.time() + 120, Period=60, Statistics=[statistic])
    points = [point.get(statistic) for point in response.get('Datapoints', []) if point.get(statistic) is not None]
    if not points:
        return None
    return sum(points) if statistic == 'Sum' else max(points)


def _query(query: str, since: float) -> tuple[str, list[dict[str, str]]]:
    logs = _client('logs')
    try:
        query_id = logs.start_query(logGroupName=LOG_GROUP, startTime=int(since), endTime=int(time.time()) + 60,
                                    queryString=query)['queryId']
    except ClientError as exc:
        return f'Rejected: {_code(exc)}', []
    response: dict[str, Any] = {}
    for _ in range(30):
        response = logs.get_query_results(queryId=query_id)
        if response.get('status') in ('Complete', 'Failed', 'Cancelled', 'Timeout'):
            break
        time.sleep(0.5)
    rows = [{field['field']: field['value'] for field in row if not field['field'].startswith('@ptr')}
            for row in response.get('results', [])]
    return response.get('status') or 'Unknown', rows


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_function() -> tuple[bool, str]:
    group = _log_group()
    if not group:
        return False, f'Log group {LOG_GROUP} was not found.'
    if group.get('retentionInDays') != RETENTION_DAYS:
        return False, f'{LOG_GROUP} should keep logs for {RETENTION_DAYS} days.'
    function = _function()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    if function.get('Role') != ROLE_ARN or function.get('Handler') != HANDLER or function.get('Runtime') != RUNTIME:
        return False, f'{FUNCTION} must run {HANDLER} on {RUNTIME} as {ROLE_NAME}.'
    variables = (function.get('Environment') or {}).get('Variables') or {}
    if any(variables.get(key) != value for key, value in ENVIRONMENT.items()):
        return False, f'{FUNCTION} needs STAGE, METRIC_NAMESPACE, and EMF_NAMESPACE environment variables.'
    policy = _role_policy() or {}
    if not any(item.get('Sid') == 'OrderMetrics' and item.get('Action') == 'cloudwatch:PutMetricData'
               for item in policy.get('Statement', [])):
        return False, f'{ROLE_NAME} should allow cloudwatch:PutMetricData.'
    return True, (f'{FUNCTION} logs to {LOG_GROUP} (kept {RETENTION_DAYS} days) and may publish metrics with '
                  'PutMetricData.')


def _check_filters() -> tuple[bool, str]:
    filters = _filters()
    errors, latency = filters.get(ERRORS_FILTER), filters.get(LATENCY_FILTER)
    if not errors or not latency:
        return False, f'Create both metric filters ({ERRORS_FILTER} and {LATENCY_FILTER}) on {LOG_GROUP}.'
    error_transform = (errors.get('metricTransformations') or [{}])[0]
    latency_transform = (latency.get('metricTransformations') or [{}])[0]
    if error_transform.get('metricName') != ERRORS_METRIC or error_transform.get('metricNamespace') != NAMESPACE:
        return False, f'{ERRORS_FILTER} must publish {NAMESPACE}/{ERRORS_METRIC}.'
    if latency_transform.get('metricName') != LATENCY_METRIC or latency_transform.get('metricValue') != '$.latency_ms':
        return False, f'{LATENCY_FILTER} must publish {LATENCY_METRIC} from $.latency_ms.'
    return True, f'{ERRORS_FILTER} counts ERROR lines and {LATENCY_FILTER} publishes $.latency_ms in milliseconds.'


def _check_traffic() -> tuple[bool, str]:
    marker = _marked('send-traffic') or {}
    errors, infos = marker.get('errors', 0), marker.get('infos', 0)
    expected_errors = TRAFFIC_ORDERS // BAD_EVERY
    if errors != expected_errors or infos != TRAFFIC_ORDERS - expected_errors:
        return False, (f'Send the {TRAFFIC_ORDERS} orders: the log group should hold {expected_errors} ERROR and '
                       f'{TRAFFIC_ORDERS - expected_errors} INFO lines for the run.')
    if not marker.get('fields_ok'):
        return False, 'Every log line should carry level, order_id, request_id, and latency_ms.'
    return True, (f'The log group holds {infos} INFO and {errors} ERROR JSON lines for run {marker.get("run_id")}, '
                  'each with a request ID.')


def _check_metrics() -> tuple[bool, str]:
    marker = _marked('read-metrics') or {}
    expected_errors = TRAFFIC_ORDERS // BAD_EVERY
    if (marker.get('errors') or 0) < expected_errors:
        return False, f'{ERRORS_METRIC} should sum to at least {expected_errors} after the traffic.'
    if (marker.get('latency_max') or 0) < 80 + 15 * (TRAFFIC_ORDERS - 1):
        return False, f'{LATENCY_METRIC} should reach the slowest order\'s latency.'
    if (marker.get('orders') or 0) < TRAFFIC_ORDERS - expected_errors:
        return False, f'{ORDERS_METRIC} should count the accepted orders.'
    if not marker.get('emf_logged'):
        return False, 'The function should log an EMF line for each accepted order.'
    return True, (f'Over the last few minutes (earlier runs included), {ERRORS_METRIC} sums to {marker["errors"]:g}, '
                  f'{LATENCY_METRIC} peaks at {marker["latency_max"]:g} ms, and {ORDERS_METRIC} counts '
                  f'{marker["orders"]:g} orders.')


def _check_insights() -> tuple[bool, str]:
    marker = _marked('logs-insights') or {}
    if marker.get('status') != 'Complete' or not marker.get('rows'):
        return False, 'Run the Logs Insights query: it should return the rejected orders.'
    if any(row.get('reason') is None for row in marker['rows']):
        return False, 'Each row should show the rejection reason.'
    return True, f'The query found {len(marker["rows"])} rejected orders with their reasons.'


def _check_alarm() -> tuple[bool, str]:
    alarm = _alarm()
    if not alarm:
        return False, f'Alarm {ALARM} was not found.'
    if alarm.get('MetricName') != ERRORS_METRIC or alarm.get('Threshold') != ALARM_THRESHOLD \
            or alarm.get('TreatMissingData') != 'notBreaching':
        return False, f'{ALARM} must watch {ERRORS_METRIC} at {ALARM_THRESHOLD} and treat missing data as not breaching.'
    if TOPIC_ARN not in (alarm.get('AlarmActions') or []) or TOPIC_ARN not in (alarm.get('OKActions') or []):
        return False, f'{ALARM} must notify {TOPIC} on ALARM and OK.'
    if not _topic_exists():
        return False, f'Topic {TOPIC} was not found.'
    if (_marked('error-alarm') or {}).get('state') != 'ALARM':
        return False, f'Send the burst of rejected orders and wait for {ALARM} to reach ALARM.'
    return True, f'{ALARM} reached ALARM and targets {TOPIC} for ALARM and OK.'


def _check_dashboard() -> tuple[bool, str]:
    body = _dashboard()
    if body is None:
        return False, f'Dashboard {DASHBOARD} was not found.'
    widgets = body.get('widgets') or []
    kinds = {widget.get('type') for widget in widgets}
    text = json.dumps(body)
    if not {'metric', 'log', 'alarm'} <= kinds or ERRORS_METRIC not in text or LATENCY_METRIC not in text:
        return False, f'{DASHBOARD} needs metric, alarm, and log widgets covering errors and latency.'
    return True, f'{DASHBOARD} has {len(widgets)} widgets: errors, latency, orders, the alarm, and recent rejections.'


def _check_tracing() -> tuple[bool, str]:
    function = _function()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    if (function.get('TracingConfig') or {}).get('Mode') != 'Active':
        return False, f'{FUNCTION} should use active tracing.'
    policy = _role_policy() or {}
    if not any(item.get('Sid') == 'Tracing' for item in policy.get('Statement', [])):
        return False, f'{ROLE_NAME} needs xray:PutTraceSegments.'
    return True, f'{FUNCTION} uses active tracing and its role may send trace segments.'


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_function() -> dict[str, Any]:
    started = time.perf_counter()
    logs, iam, lam = _client('logs'), _client('iam'), _client('lambda')
    if not _log_group():
        logs.create_log_group(logGroupName=LOG_GROUP)
    logs.put_retention_policy(logGroupName=LOG_GROUP, retentionInDays=RETENTION_DAYS)
    if not _role_exists():
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
                        Description='Order function (serverless observability lab)')
    with_tracing = ((_function() or {}).get('TracingConfig') or {}).get('Mode') == 'Active'
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY,
                        PolicyDocument=json.dumps(role_policy(with_tracing)))
    settings = {'Role': ROLE_ARN, 'Runtime': RUNTIME, 'Handler': HANDLER, 'Timeout': 10,
                'Environment': {'Variables': ENVIRONMENT}, 'LoggingConfig': LOGGING_CONFIG}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_zip())
        _wait_updated()
        lam.update_function_configuration(FunctionName=FUNCTION, **settings)
    else:
        lam.create_function(FunctionName=FUNCTION, Code={'ZipFile': _zip()},
                            Description='Orders (serverless observability lab)', **settings)
    _wait_updated()
    function = _function() or {}
    response = {'LogGroup': _log_group(), 'Function': {key: function.get(key) for key in (
        'FunctionName', 'Runtime', 'Handler', 'Role', 'Environment', 'LoggingConfig')},
        'RolePolicy': role_policy(with_tracing)}
    verified, message = _check_function()
    return _result('create-function', CREATE_COMMAND, response, verified, message, started)


def step_metric_filters() -> dict[str, Any]:
    started = time.perf_counter()
    if not _log_group():
        return _missing('metric-filters', FILTERS_COMMAND, 'Create the log group and function first.', started)
    logs = _client('logs')
    logs.put_metric_filter(logGroupName=LOG_GROUP, filterName=ERRORS_FILTER, filterPattern=ERRORS_PATTERN,
                           metricTransformations=[{'metricName': ERRORS_METRIC, 'metricNamespace': NAMESPACE,
                                                   'metricValue': '1', 'defaultValue': 0.0}])
    logs.put_metric_filter(logGroupName=LOG_GROUP, filterName=LATENCY_FILTER, filterPattern=LATENCY_PATTERN,
                           metricTransformations=[{'metricName': LATENCY_METRIC, 'metricNamespace': NAMESPACE,
                                                   'metricValue': '$.latency_ms', 'unit': 'Milliseconds'}])
    verified, message = _check_filters()
    return _result('metric-filters', FILTERS_COMMAND, {'MetricFilters': list(_filters().values())}, verified,
                   message, started)


def step_send_traffic() -> dict[str, Any]:
    started = time.perf_counter()
    if not _function() or not _filters():
        return _missing('send-traffic', TRAFFIC_COMMAND, 'Create the function and metric filters first.', started)
    run_id = uuid.uuid4().hex[:8]
    since_ms = int(time.time() * 1000) - 5000
    results = [_invoke(_order(run_id, index, index % BAD_EVERY == 0)) for index in range(1, TRAFFIC_ORDERS + 1)]
    expected = TRAFFIC_ORDERS * 1
    lines: list[dict[str, Any]] = []

    def logged() -> bool:
        nonlocal lines
        lines = [line for line in _log_lines(run_id, since_ms) if line.get('level') in ('INFO', 'ERROR')]
        return len(lines) >= expected

    _wait(logged, timeout=30, delay=2)
    errors = sum(1 for line in lines if line.get('level') == 'ERROR')
    fields_ok = bool(lines) and all(all(line.get(key) is not None for key in
                                        ('level', 'order_id', 'request_id', 'latency_ms')) for line in lines)
    _marker('send-traffic', {'run_id': run_id, 'since_ms': since_ms, 'errors': errors,
                             'infos': len(lines) - errors, 'fields_ok': fields_ok})
    verified, message = _check_traffic()
    response = {'Results': results, 'SampleLines': sorted(lines, key=lambda line: line.get('order_id') or '')[:4]}
    return _result('send-traffic', TRAFFIC_COMMAND, response, verified, message, started)


def step_read_metrics() -> dict[str, Any]:
    started = time.perf_counter()
    traffic = _marked('send-traffic')
    if not traffic:
        return _missing('read-metrics', METRICS_COMMAND, 'Send the traffic first.', started)
    since = traffic['since_ms'] / 1000 - 120
    values: dict[str, Any] = {}

    def published() -> bool:
        values.update({
            'errors': _statistic(ERRORS_METRIC, 'Sum', since),
            'latency_max': _statistic(LATENCY_METRIC, 'Maximum', since),
            'orders': _statistic(ORDERS_METRIC, 'Sum', since, [{'Name': 'Environment', 'Value': STAGE}]),
        })
        return all(value is not None for value in values.values())

    _wait(published, timeout=20, delay=2)
    emf_metrics = _client('cloudwatch').list_metrics(Namespace=EMF_NAMESPACE).get('Metrics', [])
    values['emf_logged'] = _emf_logged(traffic['run_id'], traffic['since_ms'])
    values['emf_metrics'] = len(emf_metrics)
    _marker('read-metrics', values)
    verified, message = _check_metrics()
    if verified:
        message += (f' The EMF line was logged and CloudWatch extracted {len(emf_metrics)} metrics from it.'
                    if emf_metrics else ' The EMF line was logged, but Floci does not extract EMF, so OrderValue '
                    'does not appear (on AWS it would).')
    return _result('read-metrics', METRICS_COMMAND, values, verified, message, started)


def step_logs_insights() -> dict[str, Any]:
    started = time.perf_counter()
    traffic = _marked('send-traffic')
    if not traffic:
        return _missing('logs-insights', INSIGHTS_COMMAND, 'Send the traffic first.', started)
    since = traffic['since_ms'] / 1000 - 60
    status, rows = _query(INSIGHTS_QUERY, since)
    stats_status, stats_rows = _query(STATS_QUERY, since)
    _marker('logs-insights', {'status': status, 'rows': rows})
    verified, message = _check_insights()
    if verified:
        if stats_status == 'Complete' and stats_rows:
            message += f' The stats query returned {len(stats_rows)} rows.'
        else:
            lines = _log_lines(traffic['run_id'], traffic['since_ms'])
            counts = {level: sum(1 for line in lines if line.get('level') == level) for level in ('INFO', 'ERROR')}
            message += (f' Floci did not run the stats query ({stats_status}); counted from the logs instead: '
                        f'{counts["INFO"]} INFO, {counts["ERROR"]} ERROR.')
    response = {'Query': INSIGHTS_QUERY, 'Status': status, 'Rows': rows, 'StatsQuery': STATS_QUERY,
                'StatsStatus': stats_status, 'StatsRows': stats_rows}
    return _result('logs-insights', INSIGHTS_COMMAND, response, verified, message, started)


def step_error_alarm() -> dict[str, Any]:
    started = time.perf_counter()
    if not _function() or not _filters():
        return _missing('error-alarm', ALARM_COMMAND, 'Create the function and metric filters first.', started)
    sns, cloudwatch, sqs = _client('sns'), _client('cloudwatch'), _client('sqs')
    sns.create_topic(Name=TOPIC)
    inbox_url = sqs.create_queue(QueueName=INBOX, Attributes={'Policy': json.dumps(INBOX_POLICY)})['QueueUrl']
    if not any(item.get('Endpoint') == INBOX_ARN
               for item in sns.list_subscriptions_by_topic(TopicArn=TOPIC_ARN).get('Subscriptions', [])):
        sns.subscribe(TopicArn=TOPIC_ARN, Protocol='sqs', Endpoint=INBOX_ARN)
    if _alarm():
        # A rerun starts from a fresh alarm, so it transitions into ALARM (and notifies) again.
        cloudwatch.delete_alarms(AlarmNames=[ALARM])
    cloudwatch.put_metric_alarm(
        AlarmName=ALARM, AlarmDescription='Three or more rejected orders in a minute',
        Namespace=NAMESPACE, MetricName=ERRORS_METRIC, Statistic='Sum', Period=60, EvaluationPeriods=1,
        Threshold=ALARM_THRESHOLD, ComparisonOperator='GreaterThanOrEqualToThreshold',
        TreatMissingData='notBreaching', AlarmActions=[TOPIC_ARN], OKActions=[TOPIC_ARN])
    run_id = uuid.uuid4().hex[:8]
    for index in range(1, ALARM_BURST + 1):
        _invoke(_order(run_id, index, True))
    reached = _wait(lambda: (_alarm() or {}).get('StateValue') == 'ALARM', timeout=ALARM_WAIT_SECONDS, delay=3)
    alarm = _alarm() or {}
    notification = _alarm_notification(sqs, inbox_url) if reached else None
    _marker('error-alarm', {'state': alarm.get('StateValue')})
    verified, message = _check_alarm()
    if verified and notification:
        message += f' The ALARM notification reached {INBOX} through the topic, as it would on AWS.'
    elif verified:
        message += (f' No notification reached {INBOX}: this Floci version does not run alarm actions. On AWS, '
                    'subscribers would get the ALARM message now.')
    elif not reached:
        message = (f'{ALARM} stayed {alarm.get("StateValue")} for {ALARM_WAIT_SECONDS} seconds after '
                   f'{ALARM_BURST} rejected orders.')
    response = {'Alarm': {key: alarm.get(key) for key in (
        'AlarmName', 'StateValue', 'StateReason', 'MetricName', 'Threshold', 'TreatMissingData', 'AlarmActions',
        'OKActions')}, 'Notification': notification}
    return _result('error-alarm', ALARM_COMMAND, response, verified, message, started)


def dashboard_body() -> dict[str, Any]:
    def metric(title, metrics, x, y, stat='Sum'):
        return {'type': 'metric', 'x': x, 'y': y, 'width': 12, 'height': 6,
                'properties': {'title': title, 'metrics': metrics, 'region': REGION, 'stat': stat, 'period': 60,
                               'view': 'timeSeries'}}
    return {'widgets': [
        metric('Rejected orders', [[NAMESPACE, ERRORS_METRIC]], 0, 0),
        metric('Latency (ms)', [[NAMESPACE, LATENCY_METRIC, {'stat': 'Average'}],
                                [NAMESPACE, LATENCY_METRIC, {'stat': 'p90'}]], 12, 0, stat='Average'),
        metric('Orders placed', [[NAMESPACE, ORDERS_METRIC, 'Environment', STAGE]], 0, 6),
        {'type': 'alarm', 'x': 12, 'y': 6, 'width': 12, 'height': 6,
         'properties': {'title': 'Alarms', 'alarms': [f'arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{ALARM}']}},
        {'type': 'log', 'x': 0, 'y': 12, 'width': 24, 'height': 6,
         'properties': {'title': 'Recent rejections', 'region': REGION, 'view': 'table',
                        'query': f"SOURCE '{LOG_GROUP}' | {INSIGHTS_QUERY}"}},
    ]}


def step_dashboard() -> dict[str, Any]:
    started = time.perf_counter()
    response = _client('cloudwatch').put_dashboard(DashboardName=DASHBOARD,
                                                   DashboardBody=json.dumps(dashboard_body()))
    verified, message = _check_dashboard()
    if response.get('DashboardValidationMessages'):
        message += f' Validation messages: {response["DashboardValidationMessages"]}'
    return _result('dashboard', DASHBOARD_COMMAND, {'DashboardBody': dashboard_body(), 'PutDashboard': response},
                   verified, message, started)


def step_active_tracing() -> dict[str, Any]:
    started = time.perf_counter()
    if not _function():
        return _missing('active-tracing', TRACING_COMMAND, 'Create the function first.', started)
    _client('iam').put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY,
                                   PolicyDocument=json.dumps(role_policy(True)))
    _client('lambda').update_function_configuration(FunctionName=FUNCTION, TracingConfig={'Mode': 'Active'})
    _wait_updated()
    run_id = uuid.uuid4().hex[:8]
    _invoke(_order(run_id, 1, False))
    try:
        traces = _client('xray').get_trace_summaries(StartTime=time.time() - 300, EndTime=time.time())
        trace_note = f'X-Ray returned {len(traces.get("TraceSummaries", []))} trace summaries.'
    except (ClientError, BotoCoreError) as exc:
        code = _code(exc) if isinstance(exc, ClientError) else type(exc).__name__
        trace_note = f'Floci has no X-Ray service ({code}), so there are no traces to read.'
    verified, message = _check_tracing()
    if verified:
        message += ' ' + trace_note
    response = {'TracingConfig': (_function() or {}).get('TracingConfig'), 'XRay': trace_note}
    return _result('active-tracing', TRACING_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-function': step_create_function,
    'metric-filters': step_metric_filters,
    'send-traffic': step_send_traffic,
    'read-metrics': step_read_metrics,
    'logs-insights': step_logs_insights,
    'error-alarm': step_error_alarm,
    'dashboard': step_dashboard,
    'active-tracing': step_active_tracing,
}

CHECKS = {
    'create-function': _check_function,
    'metric-filters': _check_filters,
    'send-traffic': _check_traffic,
    'read-metrics': _check_metrics,
    'logs-insights': _check_insights,
    'error-alarm': _check_alarm,
    'dashboard': _check_dashboard,
    'active-tracing': _check_tracing,
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
    except (ClientError, BotoCoreError):
        return False


def status() -> dict[str, Any]:
    steps = {}
    for step, check in CHECKS.items():
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


def reset() -> dict[str, Any]:
    """Delete the dashboard, alarm, topic, inbox queue, function, role, and log group (with its metric filters)."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            if _not_found(exc):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudwatch, sns, sqs, lam, iam, logs = (_client(name) for name in ('cloudwatch', 'sns', 'sqs', 'lambda', 'iam', 'logs'))
    try:
        dashboard = _dashboard()
        alarm = _alarm()
        topic = _topic_exists()
        inbox_url = _inbox_url()
        function = _function()
        group = _log_group()
    except (ClientError, BotoCoreError) as exc:
        dashboard = alarm = function = group = inbox_url = None
        topic = False
        errors.append(f'lookup: {exc}')
    if dashboard is not None:
        attempt(f'dashboard:{DASHBOARD}', lambda: cloudwatch.delete_dashboards(DashboardNames=[DASHBOARD]))
    if alarm:
        attempt(f'alarm:{ALARM}', lambda: cloudwatch.delete_alarms(AlarmNames=[ALARM]))
    if topic:
        attempt(f'topic:{TOPIC}', lambda: sns.delete_topic(TopicArn=TOPIC_ARN))
    if inbox_url:
        attempt(f'queue:{INBOX}', lambda: sqs.delete_queue(QueueUrl=inbox_url))
    if function:
        attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    attempt(f'role-policy:{ROLE_POLICY}', lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))
    if group:
        attempt(f'log-group:{LOG_GROUP}', lambda: logs.delete_log_group(logGroupName=LOG_GROUP))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws cloudwatch delete-dashboards --dashboard-names {DASHBOARD}\n'
            f'aws cloudwatch delete-alarms --alarm-names {ALARM}\n'
            f'aws sns delete-topic --topic-arn {TOPIC_ARN}   # also removes its subscriptions\n'
            f'aws sqs delete-queue --queue-url <{INBOX} URL>\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws iam delete-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY}\n'
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            f'aws logs delete-log-group --log-group-name {LOG_GROUP}   # also removes its metric filters'
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
            else 'The dashboard, alarm, topic, function, role, and log group are deleted. Metrics already '
                 'published stay in CloudWatch until they age out.',
        },
    }
