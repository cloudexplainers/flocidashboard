"""Validated operations for the CloudWatch console."""
import json
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


def _iso(value):
    return value.isoformat() if hasattr(value, 'isoformat') else value


@require_http_methods(['GET', 'PUT', 'DELETE'])
def dashboards(request):
    """Lists dashboards with their bodies, saves one (PutDashboard), or deletes one."""
    operation = {'GET': 'list_dashboards', 'PUT': 'put_dashboard', 'DELETE': 'delete_dashboards'}[request.method]
    try:
        cloudwatch = FlociClientFactory().client('cloudwatch')
        if request.method == 'GET':
            items = []
            for entry in cloudwatch.list_dashboards().get('DashboardEntries', []):
                body = cloudwatch.get_dashboard(DashboardName=entry['DashboardName']).get('DashboardBody') or '{}'
                items.append({
                    'name': entry.get('DashboardName'),
                    'arn': entry.get('DashboardArn'),
                    'last_modified': _iso(entry.get('LastModified')),
                    'size': entry.get('Size'),
                    'body': body,
                })
            return JsonResponse({'dashboards': items})
        body = parse_json_body(request)
        name = _required(body, 'dashboard_name')
        if request.method == 'DELETE':
            cloudwatch.delete_dashboards(DashboardNames=[name])
            return JsonResponse({'dashboard_name': name, 'deleted': True})
        dashboard_body = body.get('dashboard_body')
        if isinstance(dashboard_body, (dict, list)):
            dashboard_body = json.dumps(dashboard_body)
        if not isinstance(dashboard_body, str) or not dashboard_body.strip():
            raise ValueError('Dashboard body is required')
        try:
            parsed = json.loads(dashboard_body)
        except json.JSONDecodeError as exc:
            raise ValueError('Dashboard body must be valid JSON') from exc
        if not isinstance(parsed, dict) or not isinstance(parsed.get('widgets'), list):
            raise ValueError('Dashboard body must be an object with a "widgets" list')
        response = cloudwatch.put_dashboard(DashboardName=name, DashboardBody=dashboard_body)
        return JsonResponse({'dashboard_name': name, 'validation_messages': response.get('DashboardValidationMessages', [])})
    except Exception as exc:
        return handle_action_error(exc, service='cloudwatch', operation=operation)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def subscription_filters(request):
    """Lists, creates or replaces, and deletes CloudWatch Logs subscription filters for one log group."""
    operation = {'GET': 'describe_subscription_filters', 'PUT': 'put_subscription_filter', 'DELETE': 'delete_subscription_filter'}[request.method]
    try:
        logs = FlociClientFactory().client('logs')
        if request.method == 'GET':
            group = (request.GET.get('log_group_name') or '').strip()
            if not group:
                raise ValueError('Log group name is required')
            filters = logs.describe_subscription_filters(logGroupName=group).get('subscriptionFilters', [])
            return JsonResponse({'log_group_name': group, 'subscription_filters': [{
                'filter_name': item.get('filterName'),
                'filter_pattern': item.get('filterPattern') or '',
                'destination_arn': item.get('destinationArn'),
                'role_arn': item.get('roleArn') or '',
                'distribution': item.get('distribution') or '',
                'creation_time': item.get('creationTime'),
            } for item in filters]})
        body = parse_json_body(request)
        group, name = _required(body, 'log_group_name'), _required(body, 'filter_name')
        if request.method == 'DELETE':
            logs.delete_subscription_filter(logGroupName=group, filterName=name)
            return JsonResponse({'log_group_name': group, 'filter_name': name, 'deleted': True})
        pattern = body.get('filter_pattern', '')
        if not isinstance(pattern, str):
            raise ValueError('Filter pattern must be text (empty matches every event)')
        kwargs = dict(logGroupName=group, filterName=name, filterPattern=pattern,
                      destinationArn=_required(body, 'destination_arn'))
        if isinstance(body.get('role_arn'), str) and body['role_arn'].strip():
            kwargs['roleArn'] = body['role_arn'].strip()
        distribution = body.get('distribution') or 'ByLogStream'
        if distribution not in ('ByLogStream', 'Random'):
            raise ValueError('Distribution must be ByLogStream or Random')
        kwargs['distribution'] = distribution
        logs.put_subscription_filter(**kwargs)
        return JsonResponse({'log_group_name': group, 'filter_name': name})
    except Exception as exc:
        return handle_action_error(exc, service='logs', operation=operation)
