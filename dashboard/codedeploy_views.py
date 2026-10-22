"""HTTP endpoints for the CodeDeploy workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .codedeploy_api import (
    continue_deployment,
    create_application,
    create_deployment,
    create_deployment_config,
    create_deployment_group,
    delete_application,
    delete_deployment_config,
    delete_deployment_group,
    get_deployment,
    put_lifecycle_event_hook_execution_status,
    simulate_lifecycle_event,
    stop_deployment,
    tag_resource,
    untag_resource,
)


@require_http_methods(['POST'])
def codedeploy_applications_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_application(body.get('name') or '', body.get('compute_platform') or 'Server'))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='create_application')


@require_http_methods(['DELETE'])
def codedeploy_application_detail(request, application_name: str):
    try:
        return JsonResponse(delete_application(application_name))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='delete_application')


@require_http_methods(['POST'])
def codedeploy_deployment_groups_create(request, application_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_deployment_group(application_name, body.get('deployment_group_name') or '', body.get('options') or {}))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='create_deployment_group')


@require_http_methods(['DELETE'])
def codedeploy_deployment_group_detail(request, application_name: str, group_name: str):
    try:
        return JsonResponse(delete_deployment_group(application_name, group_name))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='delete_deployment_group')


@require_http_methods(['POST'])
def codedeploy_deployment_configs_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_deployment_config(body.get('name') or '', body.get('options') or {}))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='create_deployment_config')


@require_http_methods(['DELETE'])
def codedeploy_deployment_config_detail(request, config_name: str):
    try:
        return JsonResponse(delete_deployment_config(config_name))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='delete_deployment_config')


@require_http_methods(['POST'])
def codedeploy_deployments_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_deployment(
            body.get('application_name') or '',
            body.get('deployment_group_name') or '',
            body.get('revision') or {},
            body.get('options') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='create_deployment')


@require_http_methods(['POST'])
def codedeploy_deployment_get(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_deployment(body.get('deployment_id') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='get_deployment')


@require_http_methods(['POST'])
def codedeploy_deployment_stop(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(stop_deployment(body.get('deployment_id') or '', body.get('auto_rollback_enabled') or False))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='stop_deployment')


@require_http_methods(['POST'])
def codedeploy_deployment_continue(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(continue_deployment(body.get('deployment_id') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='continue_deployment')


@require_http_methods(['POST'])
def codedeploy_lifecycle_hook_status(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_lifecycle_event_hook_execution_status(
            body.get('deployment_id') or '',
            body.get('hook_execution_id') or '',
            body.get('status') or 'Succeeded',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='put_lifecycle_event_hook_execution_status')


@require_http_methods(['POST', 'DELETE'])
def codedeploy_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(untag_resource(body.get('resource_arn') or '', body.get('tag_keys') or []))
        return JsonResponse(tag_resource(body.get('resource_arn') or '', body.get('tags') or []))
    except Exception as exc:
        operation = 'untag_resource' if request.method == 'DELETE' else 'tag_resource'
        return handle_action_error(exc, service='codedeploy', operation=operation)


@require_http_methods(['POST'])
def codedeploy_lifecycle_simulate(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(simulate_lifecycle_event(
            deployment_id=body.get('deployment_id') or '',
            lifecycle_event_name=body.get('lifecycle_event_name') or 'BeforeInstall',
            status=body.get('status') or 'Succeeded',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='codedeploy', operation='simulate_lifecycle_event')
