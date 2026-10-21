"""Normalized, evidence-backed resource relationship graphs."""

from __future__ import annotations

from typing import Any, Callable

from botocore.exceptions import ClientError

from .aws import FlociClientFactory
from .labs import eventbridge_application as app

LAYERS = ('entrypoint', 'compute', 'routing', 'target', 'observability')


def _client(service: str):
    return FlociClientFactory().client(service)


def _safe(action: Callable[[], Any]) -> tuple[bool, Any]:
    try:
        return True, action()
    except (ClientError, ValueError):
        return False, None


def _node(node_id: str, name: str, service: str, kind: str, layer: str, exists: bool, href: str, state: str = '') -> dict[str, Any]:
    status = 'healthy' if exists else 'broken'
    if exists and state == 'DISABLED':
        status = 'disabled'
    return {'id': node_id, 'name': name, 'service': service, 'kind': kind, 'layer': layer, 'status': status, 'state': state or ('Available' if exists else 'Missing'), 'href': href}


def _edge(edge_id: str, source: str, target: str, relation: str, evidence_label: str, evidence_value: Any, status: str, detail: str = '') -> dict[str, Any]:
    return {'id': edge_id, 'source': source, 'target': target, 'relation': relation, 'status': status, 'detail': detail, 'evidence': {'label': evidence_label, 'value': evidence_value}}


def eventbridge_application_graph() -> dict[str, Any]:
    gateway, lam, iam = _client('apigatewayv2'), _client('lambda'), _client('iam')
    events, sqs, logs = _client('events'), _client('sqs'), _client('logs')
    _, api_page = _safe(lambda: gateway.get_apis())
    api_resource = next((item for item in (api_page or {}).get('Items', []) if item.get('Name') == app.API_NAME), None)
    api_id = (api_resource or {}).get('ApiId')
    _, integrations = _safe(lambda: gateway.get_integrations(ApiId=api_id)) if api_id else (False, None)
    integration = next((item for item in (integrations or {}).get('Items', []) if item.get('IntegrationUri') == app.PRODUCER_FUNCTION_ARN), None)
    producer_exists, producer = _safe(lambda: lam.get_function(FunctionName=app.PRODUCER_FUNCTION))
    notifier_exists, notifier = _safe(lambda: lam.get_function(FunctionName=app.NOTIFIER_FUNCTION))
    producer_role_exists, _ = _safe(lambda: iam.get_role(RoleName=app.PRODUCER_ROLE))
    notifier_role_exists, _ = _safe(lambda: iam.get_role(RoleName=app.NOTIFIER_ROLE))
    bus_exists, _ = _safe(lambda: events.describe_event_bus(Name=app.BUS))
    rules, targets = {}, {}
    for name in (app.PROCESSING_RULE, app.AUDIT_RULE, app.NOTIFICATION_RULE):
        exists, rule = _safe(lambda name=name: events.describe_rule(Name=name, EventBusName=app.BUS))
        rules[name] = rule if exists else None
        _, target_page = _safe(lambda name=name: events.list_targets_by_rule(Rule=name, EventBusName=app.BUS))
        targets[name] = (target_page or {}).get('Targets', [])
    queue_exists = {name: _safe(lambda name=name: sqs.get_queue_url(QueueName=name))[0] for name in (app.PROCESSING_QUEUE, app.AUDIT_QUEUE, app.DLQ)}
    log_exists = {}
    for function in (app.PRODUCER_FUNCTION, app.NOTIFIER_FUNCTION):
        expected_group = f'/aws/lambda/{function}'
        _, page = _safe(lambda function=function: logs.describe_log_groups(logGroupNamePrefix=f'/aws/lambda/{function}', limit=10))
        log_exists[function] = any(
            group.get('logGroupName') == expected_group
            for group in (page or {}).get('logGroups', [])
        )

    nodes = [
        _node('api', app.API_NAME, 'apigateway', 'HTTP API', 'entrypoint', bool(api_resource), f'/apigateway?api=http:{api_id or ""}'),
        _node('producer', app.PRODUCER_FUNCTION, 'lambda', 'Function', 'compute', producer_exists, f'/lambda?function={app.PRODUCER_FUNCTION}'),
        _node('producer-role', app.PRODUCER_ROLE, 'iam', 'Role', 'compute', producer_role_exists, f'/iam?type=role&name={app.PRODUCER_ROLE}'),
        _node('bus', app.BUS, 'eventbridge', 'Event bus', 'routing', bus_exists, f'/eventbridge?bus={app.BUS}'),
    ]
    nodes.extend(_node(f'rule:{name}', name, 'eventbridge', 'Rule', 'routing', bool(rules[name]), f'/eventbridge?bus={app.BUS}&rule={name}', (rules[name] or {}).get('State', '')) for name in (app.PROCESSING_RULE, app.AUDIT_RULE, app.NOTIFICATION_RULE))
    nodes.extend([
        _node('processing-queue', app.PROCESSING_QUEUE, 'sqs', 'Queue', 'target', queue_exists[app.PROCESSING_QUEUE], f'/sqs?queue={app.PROCESSING_QUEUE}'),
        _node('audit-queue', app.AUDIT_QUEUE, 'sqs', 'Queue', 'target', queue_exists[app.AUDIT_QUEUE], f'/sqs?queue={app.AUDIT_QUEUE}'),
        _node('notifier', app.NOTIFIER_FUNCTION, 'lambda', 'Function', 'target', notifier_exists, f'/lambda?function={app.NOTIFIER_FUNCTION}'),
        _node('notifier-role', app.NOTIFIER_ROLE, 'iam', 'Role', 'target', notifier_role_exists, f'/iam?type=role&name={app.NOTIFIER_ROLE}'),
        _node('dlq', app.DLQ, 'sqs', 'Dead-letter queue', 'target', queue_exists[app.DLQ], f'/sqs?queue={app.DLQ}'),
        _node('producer-logs', f'/aws/lambda/{app.PRODUCER_FUNCTION}', 'cloudwatch', 'Log group', 'observability', log_exists[app.PRODUCER_FUNCTION], f'/cloudwatch?log_group=/aws/lambda/{app.PRODUCER_FUNCTION}'),
        _node('notifier-logs', f'/aws/lambda/{app.NOTIFIER_FUNCTION}', 'cloudwatch', 'Log group', 'observability', log_exists[app.NOTIFIER_FUNCTION], f'/cloudwatch?log_group=/aws/lambda/{app.NOTIFIER_FUNCTION}'),
    ])
    states = {node['id']: node['status'] for node in nodes}
    def health(*ids: str) -> str:
        return 'healthy' if all(states.get(item) == 'healthy' for item in ids) else 'broken'
    processing = next((item for item in targets[app.PROCESSING_RULE] if item.get('Id') == 'processing'), {})
    audit = next((item for item in targets[app.AUDIT_RULE] if item.get('Id') == 'audit'), {})
    notification = next((item for item in targets[app.NOTIFICATION_RULE] if item.get('Id') == 'notification'), {})
    producer_role_matches = ((producer or {}).get('Configuration') or {}).get('Role') == app.PRODUCER_ROLE_ARN
    notifier_role_matches = ((notifier or {}).get('Configuration') or {}).get('Role') == app.NOTIFIER_ROLE_ARN
    processing_target_matches = (
        processing.get('Arn') == app.QUEUE_ARNS[app.PROCESSING_QUEUE]
        and processing.get('InputTransformer') == app.PROCESSING_TRANSFORMER
    )
    audit_target_matches = audit.get('Arn') == app.QUEUE_ARNS[app.AUDIT_QUEUE]
    notification_target_matches = notification.get('Arn') == app.NOTIFIER_FUNCTION_ARN
    edges = [
        _edge('api-producer', 'api', 'producer', 'invokes', 'IntegrationUri', (integration or {}).get('IntegrationUri'), health('api', 'producer') if integration else 'broken'),
        _edge('producer-role-edge', 'producer-role', 'producer', 'assumed by', 'Function.Role', ((producer or {}).get('Configuration') or {}).get('Role'), health('producer-role', 'producer') if producer_role_matches else 'broken'),
        _edge('producer-bus', 'producer', 'bus', 'PutEvents', 'EVENT_BUS_NAME', app.BUS, health('producer', 'bus')),
    ]
    edges.extend(_edge(f'bus-rule:{name}', 'bus', f'rule:{name}', 'matches through', 'EventPattern', (rules[name] or {}).get('EventPattern'), 'disabled' if (rules[name] or {}).get('State') == 'DISABLED' else health('bus', f'rule:{name}')) for name in (app.PROCESSING_RULE, app.AUDIT_RULE, app.NOTIFICATION_RULE))
    edges.extend([
        _edge('processing-target', f'rule:{app.PROCESSING_RULE}', 'processing-queue', 'delivers transformed event', 'InputTransformer', processing.get('InputTransformer'), health(f'rule:{app.PROCESSING_RULE}', 'processing-queue') if processing_target_matches else 'broken'),
        _edge('audit-target', f'rule:{app.AUDIT_RULE}', 'audit-queue', 'delivers full envelope', 'Target.Arn', audit.get('Arn'), health(f'rule:{app.AUDIT_RULE}', 'audit-queue') if audit_target_matches else 'broken'),
        _edge('notification-target', f'rule:{app.NOTIFICATION_RULE}', 'notifier', 'invokes', 'Target.Arn', notification.get('Arn'), health(f'rule:{app.NOTIFICATION_RULE}', 'notifier') if notification_target_matches else 'broken'),
        _edge('notifier-role-edge', 'notifier-role', 'notifier', 'assumed by', 'Function.Role', ((notifier or {}).get('Configuration') or {}).get('Role'), health('notifier-role', 'notifier') if notifier_role_matches else 'broken'),
        _edge('producer-log-edge', 'producer', 'producer-logs', 'writes logs to', 'Convention', f'/aws/lambda/{app.PRODUCER_FUNCTION}', health('producer', 'producer-logs')),
        _edge('notifier-log-edge', 'notifier', 'notifier-logs', 'writes logs to', 'Convention', f'/aws/lambda/{app.NOTIFIER_FUNCTION}', health('notifier', 'notifier-logs')),
        _edge('processing-dlq', f'rule:{app.PROCESSING_RULE}', 'dlq', 'dead-letters to', 'DeadLetterConfig.Arn', app.DEAD_LETTER_CONFIG['Arn'], 'unsupported', 'This Floci version does not persist or enforce target RetryPolicy and DeadLetterConfig.'),
    ])
    summary = {status: sum(item['status'] == status for item in [*nodes, *edges]) for status in ('healthy', 'disabled', 'broken', 'unverified', 'unsupported')}
    return {'scenario': 'eventbridge-application-spine', 'title': 'Event-driven order application', 'description': 'Live relationships discovered from API Gateway, Lambda, IAM, EventBridge, SQS, and CloudWatch Logs.', 'layers': list(LAYERS), 'nodes': nodes, 'edges': edges, 'summary': summary}


def resource_graph(scenario: str) -> dict[str, Any]:
    if scenario == 'eventbridge-application-spine':
        return eventbridge_application_graph()
    raise ValueError('Unknown resource graph scenario')
