"""HTTP endpoints for the CloudWatch Logs viewer."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .cloudwatch_logs_api import get_log_events, get_logs_insights_query_results, list_log_streams, start_logs_insights_query


@require_http_methods(['POST'])
def cloudwatch_log_streams(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(list_log_streams(
            body.get('log_group_name', ''),
            limit=body.get('limit') or 50,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation='list_log_streams')


@require_http_methods(['POST'])
def cloudwatch_log_events(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_log_events(
            body.get('log_group_name', ''),
            body.get('log_stream_name', ''),
            limit=body.get('limit') or 50,
            start_time=body.get('start_time') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation='get_log_events')


@require_http_methods(['POST'])
def cloudwatch_logs_insights_query(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(start_logs_insights_query(
            body.get('log_group_name', ''),
            body.get('query_string', ''),
            start_time=body.get('start_time') or None,
            end_time=body.get('end_time') or None,
            limit=body.get('limit') or 100,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation='start_logs_insights_query')


@require_http_methods(['POST'])
def cloudwatch_logs_insights_results(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_logs_insights_query_results(body.get('query_id', '')))
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation='get_logs_insights_query_results')


@require_http_methods(['GET'])
def cloudwatch_metric_statistics(request):
    """Read real metric samples for the selected time window and dimensions."""
    from datetime import datetime, timedelta, timezone
    import json
    from .aws import FlociClientFactory

    try:
        namespace = (request.GET.get('namespace') or '').strip()
        metric_name = (request.GET.get('metric_name') or '').strip()
        if not namespace or not metric_name:
            raise ValueError('Namespace and metric name are required')
        hours = int(request.GET.get('hours', 1))
        if hours not in (1, 3, 12, 24):
            raise ValueError('Time window must be 1, 3, 12, or 24 hours')
        dimensions = json.loads(request.GET.get('dimensions', '[]'))
        if not isinstance(dimensions, list) or len(dimensions) > 30 or any(
            not isinstance(dimension, dict)
            or set(dimension) != {'Name', 'Value'}
            or not all(isinstance(value, str) for value in dimension.values())
            for dimension in dimensions
        ):
            raise ValueError('Dimensions must contain Name and Value strings')
        statistic = request.GET.get('statistic', 'Average')
        if statistic not in ('Average', 'Sum', 'Minimum', 'Maximum', 'SampleCount'):
            raise ValueError('Unsupported metric statistic')
        end = datetime.now(timezone.utc)
        response = FlociClientFactory().client('cloudwatch').get_metric_statistics(
            Namespace=namespace, MetricName=metric_name, Dimensions=dimensions,
            StartTime=end - timedelta(hours=hours), EndTime=end,
            Period=60 if hours <= 3 else 300, Statistics=[statistic],
        )
        points = sorted(response.get('Datapoints', []), key=lambda point: point['Timestamp'])
        return JsonResponse({'label': response.get('Label', metric_name), 'datapoints': points})
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation='get_metric_statistics')
