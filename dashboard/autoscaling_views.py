"""HTTP endpoints for the Auto Scaling capacity workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .autoscaling_api import (
    attach_instances,
    attach_target_groups,
    create_auto_scaling_group,
    create_launch_configuration,
    delete_auto_scaling_group,
    delete_launch_configuration,
    delete_lifecycle_hook,
    delete_policy,
    detach_instances,
    detach_target_groups,
    put_lifecycle_hook,
    put_scaling_policy,
    set_desired_capacity,
    terminate_instance,
    update_auto_scaling_group,
)


@require_http_methods(['POST'])
def autoscaling_launch_configurations_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_launch_configuration(
            body.get('name', ''),
            body.get('image_id', ''),
            body.get('instance_type', ''),
            key_name=body.get('key_name') or '',
            security_groups=body.get('security_groups'),
            user_data=body.get('user_data') or '',
            iam_instance_profile=body.get('iam_instance_profile') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='create_launch_configuration')


@require_http_methods(['DELETE'])
def autoscaling_launch_configuration_delete(request, name: str):
    try:
        return JsonResponse(delete_launch_configuration(name))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='delete_launch_configuration')


@require_http_methods(['POST'])
def autoscaling_groups_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_auto_scaling_group(
            body.get('name', ''),
            body.get('launch_configuration_name', ''),
            min_size=body.get('min_size', 1),
            max_size=body.get('max_size', 1),
            desired_capacity=body.get('desired_capacity'),
            availability_zones=body.get('availability_zones'),
            target_group_arns=body.get('target_group_arns'),
            tags=body.get('tags'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='create_auto_scaling_group')


@require_http_methods(['PUT', 'DELETE'])
def autoscaling_group_detail(request, name: str):
    if request.method == 'DELETE':
        try:
            body = parse_json_body(request) if request.body else {}
            return JsonResponse(delete_auto_scaling_group(
                name,
                force_delete=body.get('force_delete') is not False,
            ))
        except Exception as exc:
            return handle_action_error(exc, service='autoscaling', operation='delete_auto_scaling_group')

    try:
        body = parse_json_body(request)
        return JsonResponse(update_auto_scaling_group(
            name,
            launch_configuration_name=body.get('launch_configuration_name') or '',
            min_size=body.get('min_size'),
            max_size=body.get('max_size'),
            desired_capacity=body.get('desired_capacity'),
            availability_zones=body.get('availability_zones'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='update_auto_scaling_group')


@require_http_methods(['POST'])
def autoscaling_group_desired_capacity(request, name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(set_desired_capacity(
            name,
            body.get('desired_capacity'),
            honor_cooldown=body.get('honor_cooldown') is True,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='set_desired_capacity')


@require_http_methods(['POST', 'DELETE'])
def autoscaling_group_instances(request, name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(detach_instances(
                name,
                body.get('instance_ids'),
                decrement_desired_capacity=body.get('decrement_desired_capacity') is True,
            ))
        return JsonResponse(attach_instances(name, body.get('instance_ids')))
    except Exception as exc:
        operation = 'detach_instances' if request.method == 'DELETE' else 'attach_instances'
        return handle_action_error(exc, service='autoscaling', operation=operation)


@require_http_methods(['POST'])
def autoscaling_instance_terminate(request, instance_id: str):
    try:
        body = parse_json_body(request) if request.body else {}
        return JsonResponse(terminate_instance(
            instance_id,
            decrement_desired_capacity=body.get('decrement_desired_capacity') is True,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='terminate_instance')


@require_http_methods(['POST', 'DELETE'])
def autoscaling_group_target_groups(request, name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(detach_target_groups(name, body.get('target_group_arns')))
        return JsonResponse(attach_target_groups(name, body.get('target_group_arns')))
    except Exception as exc:
        operation = 'detach_target_groups' if request.method == 'DELETE' else 'attach_target_groups'
        return handle_action_error(exc, service='autoscaling', operation=operation)


@require_http_methods(['POST'])
def autoscaling_lifecycle_hooks_put(request, name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_lifecycle_hook(
            name,
            body.get('hook_name', ''),
            body.get('transition', ''),
            default_result=body.get('default_result') or 'ABANDON',
            heartbeat_timeout=body.get('heartbeat_timeout') or 3600,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='put_lifecycle_hook')


@require_http_methods(['DELETE'])
def autoscaling_lifecycle_hook_delete(request, name: str, hook_name: str):
    try:
        return JsonResponse(delete_lifecycle_hook(name, hook_name))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='delete_lifecycle_hook')


@require_http_methods(['POST'])
def autoscaling_policies_put(request, name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_scaling_policy(
            name,
            body.get('policy_name', ''),
            adjustment_type=body.get('adjustment_type') or 'ChangeInCapacity',
            scaling_adjustment=body.get('scaling_adjustment') or 1,
            cooldown=body.get('cooldown') or 60,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='put_scaling_policy')


@require_http_methods(['DELETE'])
def autoscaling_policy_delete(request, name: str, policy_name: str):
    try:
        return JsonResponse(delete_policy(name, policy_name))
    except Exception as exc:
        return handle_action_error(exc, service='autoscaling', operation='delete_policy')
