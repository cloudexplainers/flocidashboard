"""CloudWatch inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _cloudwatch_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def cloudwatch_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    logs = factory.client('logs')
    cloudwatch = factory.client('cloudwatch')

    log_groups = core._paginate(logs, 'describe_log_groups', 'logGroups')

    def log_group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group.get('logGroupName')
        streams = _cloudwatch_optional(
            lambda: core._paginate(logs, 'describe_log_streams', 'logStreams', logGroupName=name),
            {'ResourceNotFoundException'},
        )
        stream_details = []

        if isinstance(streams, list):
            for stream in streams:
                stream_name = stream.get('logStreamName')
                events = _cloudwatch_optional(
                    lambda: logs.get_log_events(
                        logGroupName=name,
                        logStreamName=stream_name,
                        limit=10,
                        startFromHead=False,
                    ).get('events', []),
                    {'ResourceNotFoundException'},
                )
                stream_details.append({
                    **stream,
                    'recent_events': events,
                })

        return {
            'name': name,
            'arn': group.get('arn'),
            'created': group.get('creationTime'),
            'retention_days': group.get('retentionInDays'),
            'stored_bytes': group.get('storedBytes'),
            'metric_filter_count': group.get('metricFilterCount'),
            'tags': _cloudwatch_optional(
                lambda: logs.list_tags_log_group(logGroupName=name).get('tags', {}),
                {'ResourceNotFoundException'},
            ),
            'streams': stream_details,
            'stream_count': len(stream_details),
        }

    detailed_log_groups = [log_group_detail(group) for group in log_groups]
    metrics = core._paginate(cloudwatch, 'list_metrics', 'Metrics')
    alarms = core._paginate(cloudwatch, 'describe_alarms', 'MetricAlarms')

    return {
        'summary': {
            'log_groups': len(detailed_log_groups),
            'log_streams': sum(group.get('stream_count') or 0 for group in detailed_log_groups),
            'recent_log_events': sum(
                len(stream.get('recent_events') or [])
                for group in detailed_log_groups
                for stream in group.get('streams', [])
            ),
            'metrics': len(metrics),
            'alarms': len(alarms),
        },
        'log_groups': detailed_log_groups,
        'metrics': _clean_response(metrics),
        'alarms': _clean_response(alarms),
        'logs_supported': [
            'CreateLogGroup',
            'DeleteLogGroup',
            'DescribeLogGroups',
            'CreateLogStream',
            'DeleteLogStream',
            'DescribeLogStreams',
            'PutLogEvents',
            'GetLogEvents',
            'FilterLogEvents',
            'PutRetentionPolicy',
            'DeleteRetentionPolicy',
            'TagLogGroup',
            'UntagLogGroup',
            'ListTagsLogGroup',
            'StartQuery',
            'GetQueryResults',
        ],
        'metrics_supported': [
            'PutMetricData',
            'ListMetrics',
            'GetMetricStatistics',
            'GetMetricData',
            'PutMetricAlarm',
            'DescribeAlarms',
            'DeleteAlarms',
            'SetAlarmState',
        ],
        'configuration': {
            'logs_max_events_per_query': 10000,
            'logs_protocol': 'JSON 1.1, X-Amz-Target: Logs.*',
            'metrics_protocol': 'Query XML and JSON 1.1',
        },
        'notes': [
            'Logs Insights runs a subset of the query language: fields, filter with =, != or ==, sort, dedup and limit. Other commands (stats, parse) and operators fail the query; a filter joining conditions with and/or compares only the first and returns too few or too many rows without an error.',
        ],
    }


def list_log_groups(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': group.get('logGroupName'),
            'arn': group.get('arn'),
            'retention': group.get('retentionInDays'),
        }
        for group in factory.client('logs').get_paginator('describe_log_groups').paginate().build_full_result().get('logGroups', [])
    ]


def list_cloudwatch_metrics(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    cloudwatch = factory.client('cloudwatch')
    resources = [
        {
            'type': 'metric',
            'namespace': metric.get('Namespace'),
            'name': metric.get('MetricName'),
            'dimensions': metric.get('Dimensions'),
        }
        for metric in _safe_value(lambda: core._paginate(cloudwatch, 'list_metrics', 'Metrics'), [])
    ]
    resources.extend(
        {
            'type': 'alarm',
            'name': alarm.get('AlarmName'),
            'arn': alarm.get('AlarmArn'),
            'state': alarm.get('StateValue'),
        }
        for alarm in _safe_value(lambda: core._paginate(cloudwatch, 'describe_alarms', 'MetricAlarms'), [])
    )
    return resources
