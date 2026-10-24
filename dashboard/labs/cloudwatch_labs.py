"""CloudWatch Metrics & Logs workflow labs."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

from . import cloudwatch_observability_labs as serverless_observability

REGION = 'us-east-1'
ACCOUNT = '000000000000'
CACHE_PREFIX = 'floci-lab:cloudwatch:'

NAMESPACE = 'Floci/Ecommerce'
METRIC_NAME = 'OrderProcessingErrors'
ALARM_NAME = 'lab-order-error-alarm'
ALARM_THRESHOLD = 10
HEALTHY_ERROR_COUNT = 2
SPIKE_ERROR_COUNT = 25
# Floci evaluates alarms periodically (observed up to about a minute); allow headroom.
ALARM_WAIT_SECONDS = 90

LOG_GROUP_NAME = '/aws/floci/app-logs'
LOG_STREAM_NAME = 'web-server-1'
LOG_RETENTION_DAYS = 30
METRIC_FILTER_NAME = 'lab-http-500-errors'
METRIC_FILTER_PATTERN = '"HTTP 500"'
LOG_METRIC_NAME = 'ServerErrors'
LOG_EVENTS = [
    'HTTP 500 Internal Server Error in order-service (ORDER#9021)',
    'HTTP 200 OK GET /orders/9022',
    'HTTP 500 Internal Server Error in order-service (ORDER#9023)',
]
MATCHING_LOG_EVENTS = sum(1 for message in LOG_EVENTS if 'HTTP 500' in message)
LOG_METRIC_WAIT_SECONDS = 20

ALARM_COMMAND = (
    f'aws cloudwatch put-metric-alarm --alarm-name {ALARM_NAME} --metric-name {METRIC_NAME} '
    f'--namespace {NAMESPACE} --statistic Sum --period 60 --evaluation-periods 1 '
    f'--threshold {ALARM_THRESHOLD} --comparison-operator GreaterThanThreshold --treat-missing-data notBreaching'
)
HEALTHY_COMMAND = (
    f'aws cloudwatch put-metric-data --namespace {NAMESPACE} '
    f'--metric-data MetricName={METRIC_NAME},Value={HEALTHY_ERROR_COUNT},Unit=Count'
)
SPIKE_COMMAND = (
    f'aws cloudwatch put-metric-data --namespace {NAMESPACE} '
    f'--metric-data MetricName={METRIC_NAME},Value={SPIKE_ERROR_COUNT},Unit=Count'
)
DESCRIBE_COMMAND = f'aws cloudwatch describe-alarms --alarm-names {ALARM_NAME}'
METRIC_FILTER_COMMAND = (
    f"aws logs put-metric-filter --log-group-name {LOG_GROUP_NAME} --filter-name {METRIC_FILTER_NAME} "
    f"--filter-pattern '{METRIC_FILTER_PATTERN}' "
    f'--metric-transformations metricName={LOG_METRIC_NAME},metricNamespace={NAMESPACE},metricValue=1,defaultValue=0'
)
METRIC_STATS_COMMAND = (
    f'aws cloudwatch get-metric-statistics --namespace {NAMESPACE} --metric-name {LOG_METRIC_NAME} '
    '--statistics Sum --period 60 --start-time <five-minutes-ago> --end-time <now>'
)

METRIC_ALARMS_LAB = {
    'service': 'cloudwatch',
    'key': 'metric-alarms',
    'title': 'Publish custom CloudWatch metrics and trigger alarms',
    'description': (
        'Create a threshold alarm on a custom application metric, publish healthy data and watch the alarm '
        'settle in OK, then publish an error spike and watch CloudWatch evaluate the breach and move the alarm to ALARM.'
    ),
    'steps': [
        {
            'key': 'create-metric-alarm',
            'title': 'Create a threshold metric alarm',
            'command': ALARM_COMMAND,
            'explanation': (
                f'Watches the Sum of {METRIC_NAME} in 60-second periods and alarms when it exceeds {ALARM_THRESHOLD}. '
                'With no data yet the alarm starts in INSUFFICIENT_DATA; treat-missing-data notBreaching keeps quiet '
                'periods from counting as failures.'
            ),
        },
        {
            'key': 'publish-healthy-metrics',
            'title': 'Publish healthy metric data',
            'command': HEALTHY_COMMAND,
            'explanation': (
                f'Publishes {HEALTHY_ERROR_COUNT} errors, which is under the threshold. CloudWatch evaluates the period '
                'and moves the alarm to OK. The runner waits for that evaluation.'
            ),
        },
        {
            'key': 'publish-error-spike',
            'title': 'Publish an error spike',
            'command': SPIKE_COMMAND,
            'explanation': (
                f'Publishes {SPIKE_ERROR_COUNT} errors into the same period, pushing the Sum past {ALARM_THRESHOLD}. '
                'Nothing forces the alarm state: the next evaluation decides it.'
            ),
        },
        {
            'key': 'describe-alarms',
            'title': 'Watch the alarm move to ALARM',
            'command': DESCRIBE_COMMAND,
            'explanation': (
                'Polls the alarm until CloudWatch evaluates the breaching datapoint and records ALARM with a '
                '"Threshold Crossed" reason. Evaluation runs on a schedule, so this can take up to a minute. In AWS an alarm action (for example an SNS topic) would fire here; '
                'Floci evaluates alarms but does not run alarm actions.'
            ),
        },
    ],
}

LOGS_FILTERS_LAB = {
    'service': 'cloudwatch',
    'key': 'log-groups-metric-filters',
    'title': 'Turn application logs into metrics with CloudWatch Logs metric filters',
    'description': (
        'Create a log group with a retention policy, add a metric filter that counts HTTP 500 errors, ingest mixed '
        'application log events, and confirm CloudWatch turned only the matching lines into metric data points.'
    ),
    'steps': [
        {
            'key': 'create-log-group',
            'title': 'Create a CloudWatch log group',
            'command': f'aws logs create-log-group --log-group-name {LOG_GROUP_NAME}',
            'explanation': 'Creates the container for log streams emitted by an application, container, or Lambda function.',
        },
        {
            'key': 'put-retention-policy',
            'title': 'Set a 30-day retention policy',
            'command': f'aws logs put-retention-policy --log-group-name {LOG_GROUP_NAME} --retention-in-days {LOG_RETENTION_DAYS}',
            'explanation': (
                'Log groups keep events forever by default. A retention policy expires old events automatically, '
                'which controls storage cost and supports data-retention requirements.'
            ),
        },
        {
            'key': 'create-log-stream',
            'title': 'Create a log stream',
            'command': f'aws logs create-log-stream --log-group-name {LOG_GROUP_NAME} --log-stream-name {LOG_STREAM_NAME}',
            'explanation': 'Creates a sequence of log events from one source, here a single web server.',
        },
        {
            'key': 'put-metric-filter',
            'title': 'Count HTTP 500 errors with a metric filter',
            'command': METRIC_FILTER_COMMAND,
            'explanation': (
                f'Every new log event that matches {METRIC_FILTER_PATTERN} adds 1 to the {NAMESPACE}/{LOG_METRIC_NAME} metric. '
                'Metric filters only apply to events ingested after the filter exists, so it is created before the logs arrive.'
            ),
        },
        {
            'key': 'put-log-events',
            'title': 'Ingest mixed application log events',
            'command': (
                f'aws logs put-log-events --log-group-name {LOG_GROUP_NAME} --log-stream-name {LOG_STREAM_NAME} '
                '--log-events file://log-events.json'
            ),
            'explanation': f'Uploads {len(LOG_EVENTS)} events: {MATCHING_LOG_EVENTS} server errors and one successful request.',
            'artifact_label': 'log-events.json',
            'artifact': json.dumps(
                [{'timestamp': '<current-time-ms>', 'message': message} for message in LOG_EVENTS],
                indent=2,
            ),
        },
        {
            'key': 'get-metric-statistics',
            'title': 'Confirm the filter produced metric data',
            'command': METRIC_STATS_COMMAND,
            'explanation': (
                f'Reads the {LOG_METRIC_NAME} metric. The Sum should be {MATCHING_LOG_EVENTS}: only the HTTP 500 lines matched. '
                'This metric could now drive an alarm like the one in the metric alarms lab.'
            ),
        },
    ],
}

LABS = [METRIC_ALARMS_LAB, LOGS_FILTERS_LAB, serverless_observability.LAB]


def client(name: str):
    return FlociClientFactory().client(name)


def marker(key: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + key, _clean_response(value), timeout=86400)


def marked(key: str) -> Any:
    return cache.get(CACHE_PREFIX + key)


def result(lab: str, step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': 'cloudwatch',
        'lab': lab,
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


def _describe_alarm() -> dict[str, Any] | None:
    alarms = client('cloudwatch').describe_alarms(AlarmNames=[ALARM_NAME]).get('MetricAlarms', [])
    return next((alarm for alarm in alarms if alarm.get('AlarmName') == ALARM_NAME), None)


def _wait_for_alarm_state(expected: str, timeout_seconds: float = ALARM_WAIT_SECONDS) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        alarm = _describe_alarm()
        if (alarm and alarm.get('StateValue') == expected) or time.monotonic() >= deadline:
            return alarm
        time.sleep(1)


def step_create_alarm() -> dict[str, Any]:
    started = time.perf_counter()
    resp = client('cloudwatch').put_metric_alarm(
        AlarmName=ALARM_NAME,
        MetricName=METRIC_NAME,
        Namespace=NAMESPACE,
        Statistic='Sum',
        Period=60,
        EvaluationPeriods=1,
        Threshold=ALARM_THRESHOLD,
        ComparisonOperator='GreaterThanThreshold',
        TreatMissingData='notBreaching',
    )
    alarm = _describe_alarm()
    verified = bool(alarm) and alarm.get('Threshold') == ALARM_THRESHOLD
    if verified:
        marker('alarm', alarm)
    return result(
        'metric-alarms',
        'create-metric-alarm',
        ALARM_COMMAND,
        alarm or resp,
        verified,
        f'Alarm {ALARM_NAME} watches {NAMESPACE}/{METRIC_NAME} Sum > {ALARM_THRESHOLD} (state {alarm.get("StateValue") if alarm else "missing"}).',
        started,
    )


def step_publish_healthy() -> dict[str, Any]:
    started = time.perf_counter()
    client('cloudwatch').put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[{'MetricName': METRIC_NAME, 'Value': HEALTHY_ERROR_COUNT, 'Unit': 'Count'}],
    )
    alarm = _wait_for_alarm_state('OK')
    state = alarm.get('StateValue') if alarm else 'missing'
    verified = state == 'OK'
    if verified:
        marker('healthy', alarm)
    message = (
        f'CloudWatch evaluated {HEALTHY_ERROR_COUNT} errors as under the threshold; {ALARM_NAME} is OK.'
        if verified
        else f'Expected {ALARM_NAME} to settle in OK after healthy data, but it is {state}. '
        'If you re-ran the lab within a minute, earlier error data may still be in the period; wait a minute and retry.'
    )
    return result('metric-alarms', 'publish-healthy-metrics', HEALTHY_COMMAND, alarm or {}, verified, message, started)


def step_publish_spike() -> dict[str, Any]:
    started = time.perf_counter()
    resp = client('cloudwatch').put_metric_data(
        Namespace=NAMESPACE,
        MetricData=[{'MetricName': METRIC_NAME, 'Value': SPIKE_ERROR_COUNT, 'Unit': 'Count'}],
    )
    marker('spike', {'namespace': NAMESPACE, 'metric': METRIC_NAME, 'value': SPIKE_ERROR_COUNT})
    return result(
        'metric-alarms',
        'publish-error-spike',
        SPIKE_COMMAND,
        resp,
        True,
        f'Published {SPIKE_ERROR_COUNT} errors to {NAMESPACE}/{METRIC_NAME}; the next evaluation will see the breach.',
        started,
    )


def step_describe_alarms() -> dict[str, Any]:
    started = time.perf_counter()
    alarm = _wait_for_alarm_state('ALARM')
    state = alarm.get('StateValue') if alarm else 'missing'
    verified = state == 'ALARM'
    if verified:
        marker('alarm-state', alarm)
    message = (
        f'CloudWatch moved {ALARM_NAME} to ALARM on its own: {alarm.get("StateReason")}'
        if verified
        else f'{ALARM_NAME} is {state}; publish the error spike first, then check again.'
    )
    return result('metric-alarms', 'describe-alarms', DESCRIBE_COMMAND, alarm or {}, verified, message, started)


def _log_group() -> dict[str, Any] | None:
    groups = client('logs').describe_log_groups(logGroupNamePrefix=LOG_GROUP_NAME).get('logGroups', [])
    return next((group for group in groups if group.get('logGroupName') == LOG_GROUP_NAME), None)


def step_create_log_group() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        client('logs').create_log_group(logGroupName=LOG_GROUP_NAME)
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'ResourceAlreadyExistsException':
            raise
    group = _log_group()
    if group:
        marker('log-group', group)
    return result(
        'log-groups-metric-filters',
        'create-log-group',
        f'aws logs create-log-group --log-group-name {LOG_GROUP_NAME}',
        group or {},
        bool(group),
        f'Log group {LOG_GROUP_NAME} exists.',
        started,
    )


def step_put_retention_policy() -> dict[str, Any]:
    started = time.perf_counter()
    client('logs').put_retention_policy(logGroupName=LOG_GROUP_NAME, retentionInDays=LOG_RETENTION_DAYS)
    group = _log_group() or {}
    verified = group.get('retentionInDays') == LOG_RETENTION_DAYS
    if verified:
        marker('retention', group)
    return result(
        'log-groups-metric-filters',
        'put-retention-policy',
        f'aws logs put-retention-policy --log-group-name {LOG_GROUP_NAME} --retention-in-days {LOG_RETENTION_DAYS}',
        group,
        verified,
        f'{LOG_GROUP_NAME} now expires events after {group.get("retentionInDays")} days.',
        started,
    )


def step_create_log_stream() -> dict[str, Any]:
    started = time.perf_counter()
    logs = client('logs')
    try:
        logs.create_log_stream(logGroupName=LOG_GROUP_NAME, logStreamName=LOG_STREAM_NAME)
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'ResourceAlreadyExistsException':
            raise
    streams = logs.describe_log_streams(logGroupName=LOG_GROUP_NAME, logStreamNamePrefix=LOG_STREAM_NAME).get('logStreams', [])
    stream = next((item for item in streams if item.get('logStreamName') == LOG_STREAM_NAME), None)
    if stream:
        marker('log-stream', stream)
    return result(
        'log-groups-metric-filters',
        'create-log-stream',
        f'aws logs create-log-stream --log-group-name {LOG_GROUP_NAME} --log-stream-name {LOG_STREAM_NAME}',
        stream or {},
        bool(stream),
        f'Log stream {LOG_STREAM_NAME} exists in {LOG_GROUP_NAME}.',
        started,
    )


def step_put_metric_filter() -> dict[str, Any]:
    started = time.perf_counter()
    logs = client('logs')
    logs.put_metric_filter(
        logGroupName=LOG_GROUP_NAME,
        filterName=METRIC_FILTER_NAME,
        filterPattern=METRIC_FILTER_PATTERN,
        metricTransformations=[{
            'metricName': LOG_METRIC_NAME,
            'metricNamespace': NAMESPACE,
            'metricValue': '1',
            'defaultValue': 0,
        }],
    )
    filters = logs.describe_metric_filters(logGroupName=LOG_GROUP_NAME, filterNamePrefix=METRIC_FILTER_NAME).get('metricFilters', [])
    metric_filter = next((item for item in filters if item.get('filterName') == METRIC_FILTER_NAME), None)
    verified = bool(metric_filter) and metric_filter.get('filterPattern') == METRIC_FILTER_PATTERN
    if verified:
        marker('metric-filter', metric_filter)
    return result(
        'log-groups-metric-filters',
        'put-metric-filter',
        METRIC_FILTER_COMMAND,
        metric_filter or {},
        verified,
        f'Metric filter {METRIC_FILTER_NAME} maps {METRIC_FILTER_PATTERN} to {NAMESPACE}/{LOG_METRIC_NAME}.',
        started,
    )


def step_put_log_events() -> dict[str, Any]:
    started = time.perf_counter()
    now_ms = int(time.time() * 1000)
    resp = client('logs').put_log_events(
        logGroupName=LOG_GROUP_NAME,
        logStreamName=LOG_STREAM_NAME,
        logEvents=[{'timestamp': now_ms + offset, 'message': message} for offset, message in enumerate(LOG_EVENTS)],
    )
    marker('log-events', {'sent': len(LOG_EVENTS), 'matching': MATCHING_LOG_EVENTS, 'started_ms': now_ms})
    return result(
        'log-groups-metric-filters',
        'put-log-events',
        f'aws logs put-log-events --log-group-name {LOG_GROUP_NAME} --log-stream-name {LOG_STREAM_NAME} --log-events file://log-events.json',
        resp,
        True,
        f'Ingested {len(LOG_EVENTS)} events ({MATCHING_LOG_EVENTS} HTTP 500 errors) into {LOG_STREAM_NAME}.',
        started,
    )


def _filtered_error_sum() -> float:
    now = datetime.now(timezone.utc)
    datapoints = client('cloudwatch').get_metric_statistics(
        Namespace=NAMESPACE,
        MetricName=LOG_METRIC_NAME,
        StartTime=now - timedelta(minutes=5),
        EndTime=now + timedelta(minutes=1),
        Period=60,
        Statistics=['Sum'],
    ).get('Datapoints', [])
    return sum(point.get('Sum', 0) for point in datapoints)


def step_get_metric_statistics() -> dict[str, Any]:
    started = time.perf_counter()
    deadline = time.monotonic() + LOG_METRIC_WAIT_SECONDS
    total = _filtered_error_sum()
    while total < MATCHING_LOG_EVENTS and time.monotonic() < deadline:
        time.sleep(1)
        total = _filtered_error_sum()
    verified = total >= MATCHING_LOG_EVENTS
    payload = {'namespace': NAMESPACE, 'metric': LOG_METRIC_NAME, 'sum_last_5_minutes': total}
    if verified:
        marker('filtered-metric', payload)
    message = (
        f'The metric filter turned the HTTP 500 lines into {NAMESPACE}/{LOG_METRIC_NAME} data (Sum {total:g}).'
        if verified
        else f'No {LOG_METRIC_NAME} data yet (Sum {total:g}). Create the metric filter before ingesting the log events.'
    )
    return result('log-groups-metric-filters', 'get-metric-statistics', METRIC_STATS_COMMAND, payload, verified, message, started)


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'cloudwatch' and lab_key == 'serverless-observability':
        from .cloudwatch_observability_labs import run_step as observability_run_step
        return observability_run_step(step_key)
    if service_key == 'cloudwatch' and lab_key == 'metric-alarms':
        runners = {
            'create-metric-alarm': step_create_alarm,
            'publish-healthy-metrics': step_publish_healthy,
            'publish-error-spike': step_publish_spike,
            'describe-alarms': step_describe_alarms,
        }
        if step_key in runners:
            return runners[step_key]()
    if service_key == 'cloudwatch' and lab_key == 'log-groups-metric-filters':
        runners = {
            'create-log-group': step_create_log_group,
            'put-retention-policy': step_put_retention_policy,
            'create-log-stream': step_create_log_stream,
            'put-metric-filter': step_put_metric_filter,
            'put-log-events': step_put_log_events,
            'get-metric-statistics': step_get_metric_statistics,
        }
        if step_key in runners:
            return runners[step_key]()
    raise ValueError(f'Unknown CloudWatch lab step: {lab_key}/{step_key}')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'cloudwatch' and lab_key == 'serverless-observability':
        from .cloudwatch_observability_labs import status as observability_status
        return observability_status()
    if lab_key == 'metric-alarms':
        keys = {
            'create-metric-alarm': 'alarm',
            'publish-healthy-metrics': 'healthy',
            'publish-error-spike': 'spike',
            'describe-alarms': 'alarm-state',
        }
    elif lab_key == 'log-groups-metric-filters':
        keys = {
            'create-log-group': 'log-group',
            'put-retention-policy': 'retention',
            'create-log-stream': 'log-stream',
            'put-metric-filter': 'metric-filter',
            'put-log-events': 'log-events',
            'get-metric-statistics': 'filtered-metric',
        }
    else:
        raise ValueError(f'Unknown CloudWatch lab: {lab_key}')

    checks = {step: marked(k) is not None for step, k in keys.items()}
    return {
        'service': 'cloudwatch',
        'lab': lab_key,
        'complete': all(checks.values()),
        'steps': {
            step: {
                'verified': checks[step],
                'verification': {
                    'status': 'passed',
                    'message': 'Verified by CloudWatch runner.',
                } if checks[step] else None,
            }
            for step in keys
        },
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'cloudwatch' and lab_key == 'serverless-observability':
        from .cloudwatch_observability_labs import reset as observability_reset
        return observability_reset()
    started = time.perf_counter()
    cw = client('cloudwatch')
    logs = client('logs')

    try:
        cw.delete_alarms(AlarmNames=[ALARM_NAME])
    except ClientError:
        pass

    try:
        logs.delete_log_group(logGroupName=LOG_GROUP_NAME)
    except ClientError:
        pass

    cache.delete_many([
        CACHE_PREFIX + k
        for k in [
            'alarm', 'healthy', 'spike', 'alarm-state',
            'log-group', 'retention', 'log-stream', 'metric-filter', 'log-events', 'filtered-metric',
        ]
    ])

    payload = {'removed': True, 'alarm': ALARM_NAME, 'log_group': LOG_GROUP_NAME}
    return {
        'service': 'cloudwatch',
        'lab': lab_key,
        'command': 'aws cloudwatch delete-alarms ...\naws logs delete-log-group ... # cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verification': {'status': 'passed', 'message': 'CloudWatch lab resources cleaned up.'},
    }
