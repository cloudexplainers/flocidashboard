"""Validated operations for the CloudWatch console."""
import math
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from .actions import handle_action_error, parse_json_body
from .aws import FlociClientFactory


def _required(body, key):
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{key.replace("_", " ").capitalize()} is required')
    return value.strip()


def _number(body, key, *, integer=False, minimum=None):
    value = body.get(key)
    if value is None or isinstance(value, bool) or value == '':
        raise ValueError(f'{key} must be a number')
    number = float(value)
    if not math.isfinite(number) or (integer and not number.is_integer()) or (minimum is not None and number < minimum):
        raise ValueError(f'Invalid {key}')
    return int(number) if integer else number


@require_http_methods(['POST', 'DELETE'])
def alarms(request):
    operation = 'delete_alarms' if request.method == 'DELETE' else 'put_metric_alarm'
    try:
        body = parse_json_body(request)
        name = _required(body, 'alarm_name')
        if request.method == 'DELETE':
            FlociClientFactory().client('cloudwatch').delete_alarms(AlarmNames=[name])
        else:
            operator = body.get('comparison_operator')
            if operator not in ('GreaterThanThreshold', 'GreaterThanOrEqualToThreshold', 'LessThanThreshold', 'LessThanOrEqualToThreshold'):
                raise ValueError('Select a supported comparison operator')
            statistic = body.get('statistic', 'Average')
            if statistic not in ('Average', 'Sum', 'Minimum', 'Maximum', 'SampleCount'):
                raise ValueError('Select a supported statistic')
            period = _number(body, 'period', integer=True, minimum=1)
            if period not in (10, 20, 30) and period % 60:
                raise ValueError('Period must be 10, 20, 30, or a multiple of 60 seconds')
            evaluations = _number(body, 'evaluation_periods', integer=True, minimum=1)
            datapoints = _number(body, 'datapoints_to_alarm', integer=True, minimum=1) if 'datapoints_to_alarm' in body else evaluations
            if datapoints > evaluations:
                raise ValueError('Datapoints to alarm cannot exceed evaluation periods')
            missing = body.get('treat_missing_data', 'missing')
            if missing not in ('missing', 'ignore', 'breaching', 'notBreaching'):
                raise ValueError('Invalid missing data treatment')
            dimensions = body.get('dimensions', [])
            if not isinstance(dimensions, list) or len(dimensions) > 30 or any(not isinstance(d, dict) or set(d) != {'Name', 'Value'} or not all(isinstance(v, str) and v for v in d.values()) for d in dimensions):
                raise ValueError('Dimensions must contain Name and Value strings')
            kwargs = dict(AlarmName=name, Namespace=_required(body, 'namespace'), MetricName=_required(body, 'metric_name'),
                          ComparisonOperator=operator, Threshold=_number(body, 'threshold'), Statistic=statistic,
                          Period=period, EvaluationPeriods=evaluations, DatapointsToAlarm=datapoints,
                          TreatMissingData=missing, Dimensions=dimensions)
            FlociClientFactory().client('cloudwatch').put_metric_alarm(**kwargs)
        return JsonResponse({'alarm_name': name})
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation=operation)


@require_http_methods(['POST'])
def alarm_state(request):
    try:
        body = parse_json_body(request)
        name = _required(body, 'alarm_name')
        state = body.get('state_value')
        if state not in ('OK', 'ALARM', 'INSUFFICIENT_DATA'):
            raise ValueError('Invalid alarm state')
        reason = _required(body, 'state_reason')
        FlociClientFactory().client('cloudwatch').set_alarm_state(AlarmName=name, StateValue=state, StateReason=reason)
        return JsonResponse({'alarm_name': name, 'state_value': state})
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation='set_alarm_state')


@require_http_methods(['POST'])
def metric_data(request):
    try:
        body = parse_json_body(request)
        namespace = _required(body, 'namespace')
        metric = {'MetricName': _required(body, 'metric_name'), 'Value': _number(body, 'value')}
        if body.get('unit'):
            metric['Unit'] = body['unit']
        FlociClientFactory().client('cloudwatch').put_metric_data(Namespace=namespace, MetricData=[metric])
        return JsonResponse({'namespace': namespace, 'metric_name': metric['MetricName']})
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation='put_metric_data')


@require_http_methods(['POST'])
def create_stream(request):
    try:
        body = parse_json_body(request)
        group, stream = _required(body, 'log_group_name'), _required(body, 'log_stream_name')
        FlociClientFactory().client('logs').create_log_stream(logGroupName=group, logStreamName=stream)
        return JsonResponse({'log_group_name': group, 'log_stream_name': stream})
    except Exception as exc:
        return handle_action_error(exc, service='logs', operation='create_log_stream')


@require_http_methods(['PUT', 'DELETE'])
def retention(request):
    operation = 'delete_retention_policy' if request.method == 'DELETE' else 'put_retention_policy'
    try:
        body = parse_json_body(request)
        group = _required(body, 'log_group_name')
        if request.method == 'DELETE':
            FlociClientFactory().client('logs').delete_retention_policy(logGroupName=group)
        else:
            days = _number(body, 'retention_in_days', integer=True, minimum=1)
            if days not in (1, 3, 5, 7, 14, 30, 60, 90, 120, 150, 180, 365, 400, 545, 731, 1096, 1827, 2192, 2557, 2922, 3288, 3653):
                raise ValueError('Invalid retention period')
            FlociClientFactory().client('logs').put_retention_policy(logGroupName=group, retentionInDays=days)
        return JsonResponse({'log_group_name': group})
    except Exception as exc:
        return handle_action_error(exc, service='logs', operation=operation)
