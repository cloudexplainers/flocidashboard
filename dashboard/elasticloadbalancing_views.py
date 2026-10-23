"""HTTP endpoints for the ELBv2 control-plane workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .elasticloadbalancing_api import (
    add_tags,
    create_listener,
    create_load_balancer,
    create_rule,
    create_target_group,
    delete_listener,
    delete_load_balancer,
    delete_rule,
    delete_target_group,
    deregister_targets,
    describe_target_group_attributes,
    modify_listener,
    modify_rule,
    modify_target_group_attributes,
    modify_target_group_health_check,
    register_targets,
    remove_tags,
)


@require_http_methods(['POST'])
def elbv2_load_balancers_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_load_balancer(
            body.get('name', ''),
            lb_type=body.get('type') or 'application',
            scheme=body.get('scheme') or 'internet-facing',
            subnets=body.get('subnets'),
            security_groups=body.get('security_groups'),
            ip_address_type=body.get('ip_address_type') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='create_load_balancer')


@require_http_methods(['DELETE'])
def elbv2_load_balancer_delete(request, load_balancer_arn: str):
    try:
        return JsonResponse(delete_load_balancer(load_balancer_arn))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='delete_load_balancer')


@require_http_methods(['POST'])
def elbv2_target_groups_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_target_group(
            body.get('name', ''),
            body.get('protocol', ''),
            body.get('port'),
            target_type=body.get('target_type') or 'instance',
            vpc_id=body.get('vpc_id') or '',
            health_check_path=body.get('health_check_path') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='create_target_group')


@require_http_methods(['DELETE'])
def elbv2_target_group_delete(request, target_group_arn: str):
    try:
        return JsonResponse(delete_target_group(target_group_arn))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='delete_target_group')


@require_http_methods(['POST', 'DELETE'])
def elbv2_target_group_targets(request, target_group_arn: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(deregister_targets(target_group_arn, body.get('targets')))
        return JsonResponse(register_targets(target_group_arn, body.get('targets')))
    except Exception as exc:
        operation = 'deregister_targets' if request.method == 'DELETE' else 'register_targets'
        return handle_action_error(exc, service='elasticloadbalancing', operation=operation)


@require_http_methods(['POST'])
def elbv2_listeners_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_listener(
            body.get('load_balancer_arn', ''),
            body.get('protocol', ''),
            body.get('port'),
            body.get('target_group_arn', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='create_listener')


@require_http_methods(['PUT', 'DELETE'])
def elbv2_listener_delete(request, listener_arn: str):
    if request.method == 'PUT':
        try:
            body = parse_json_body(request)
            return JsonResponse(modify_listener(
                listener_arn,
                port=body.get('port'),
                action_type=body.get('action_type') or '',
                target_group_arn=body.get('target_group_arn') or '',
                status_code=body.get('status_code'),
                message_body=body.get('message_body') or '',
            ))
        except Exception as exc:
            return handle_action_error(exc, service='elasticloadbalancing', operation='modify_listener')
    try:
        return JsonResponse(delete_listener(listener_arn))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='delete_listener')


@require_http_methods(['POST'])
def elbv2_rules_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_rule(
            body.get('listener_arn', ''),
            body.get('priority'),
            body.get('path_pattern', ''),
            body.get('target_group_arn', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='create_rule')


@require_http_methods(['PUT', 'DELETE'])
def elbv2_rule_delete(request, rule_arn: str):
    if request.method == 'PUT':
        try:
            body = parse_json_body(request)
            return JsonResponse(modify_rule(
                rule_arn,
                path_pattern=body.get('path_pattern') or '',
                target_group_arn=body.get('target_group_arn') or '',
                priority=body.get('priority'),
            ))
        except Exception as exc:
            return handle_action_error(exc, service='elasticloadbalancing', operation='modify_rule')
    try:
        return JsonResponse(delete_rule(rule_arn))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='delete_rule')


@require_http_methods(['POST', 'DELETE'])
def elbv2_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(remove_tags(body.get('resource_arns'), body.get('tag_keys')))
        return JsonResponse(add_tags(body.get('resource_arns'), body.get('tags')))
    except Exception as exc:
        operation = 'remove_tags' if request.method == 'DELETE' else 'add_tags'
        return handle_action_error(exc, service='elasticloadbalancing', operation=operation)


@require_http_methods(['GET', 'PUT'])
def elbv2_target_group_attributes(request, target_group_arn: str):
    if request.method == 'GET':
        try:
            return JsonResponse(describe_target_group_attributes(target_group_arn))
        except Exception as exc:
            return handle_action_error(exc, service='elasticloadbalancing', operation='describe_target_group_attributes')
    try:
        body = parse_json_body(request)
        return JsonResponse(modify_target_group_attributes(
            target_group_arn,
            stickiness_enabled=body.get('stickiness_enabled'),
            stickiness_type=body.get('stickiness_type') or '',
            stickiness_duration_seconds=body.get('stickiness_duration_seconds'),
            app_cookie_name=body.get('app_cookie_name') or '',
            deregistration_delay_seconds=body.get('deregistration_delay_seconds'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='modify_target_group_attributes')


@require_http_methods(['PUT'])
def elbv2_target_group_health_check(request, target_group_arn: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(modify_target_group_health_check(
            target_group_arn,
            path=body.get('path') or '',
            interval_seconds=body.get('interval_seconds'),
            timeout_seconds=body.get('timeout_seconds'),
            healthy_threshold=body.get('healthy_threshold'),
            unhealthy_threshold=body.get('unhealthy_threshold'),
            matcher=body.get('matcher') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='elasticloadbalancing', operation='modify_target_group')
