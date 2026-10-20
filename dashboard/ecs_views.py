"""HTTP endpoints for the ECS container workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .ecs_api import (
    create_cluster,
    create_service,
    delete_cluster,
    delete_service,
    delete_task_definitions,
    deregister_task_definition,
    diff_task_definitions,
    put_account_setting,
    register_task_definition,
    run_task,
    scale_service,
    stop_task,
    tag_resource,
    untag_resource,
    update_container_instances_state,
    update_service,
    update_task_protection,
    validate_fargate_configuration,
)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on', 'force'}
    return bool(value)


@require_http_methods(['POST'])
def ecs_clusters_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_cluster(
            body.get('name') or '',
            tags=body.get('tags') or [],
            capacity_providers=body.get('capacity_providers') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='create_cluster')


@require_http_methods(['POST'])
def ecs_clusters_delete(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(delete_cluster(body.get('cluster') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='delete_cluster')


@require_http_methods(['POST'])
def ecs_task_definitions_register(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(register_task_definition(
            family=body.get('family') or '',
            container_definitions=body.get('container_definitions') or [],
            requires_compatibilities=body.get('requires_compatibilities') or [],
            network_mode=body.get('network_mode') or 'awsvpc',
            cpu=body.get('cpu') or '256',
            memory=body.get('memory') or '512',
            task_role_arn=body.get('task_role_arn') or '',
            execution_role_arn=body.get('execution_role_arn') or '',
            volumes=body.get('volumes') or [],
            tags=body.get('tags') or [],
            options=body.get('options') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='register_task_definition')


@require_http_methods(['POST', 'DELETE'])
def ecs_task_definition_detail(request):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(delete_task_definitions(body.get('task_definitions') or []))
        return JsonResponse(deregister_task_definition(body.get('task_definition') or ''))
    except Exception as exc:
        operation = 'delete_task_definitions' if request.method == 'DELETE' else 'deregister_task_definition'
        return handle_action_error(exc, service='ecs', operation=operation)


@require_http_methods(['POST'])
def ecs_tasks_run(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(run_task(
            cluster=body.get('cluster') or '',
            task_definition=body.get('task_definition') or '',
            launch_type=body.get('launch_type') or 'FARGATE',
            count=body.get('count') or 1,
            network_configuration=body.get('network_configuration') or {},
            overrides=body.get('overrides') or {},
            started_by=body.get('started_by') or '',
            tags=body.get('tags') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='run_task')


@require_http_methods(['POST'])
def ecs_tasks_stop(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(stop_task(
            body.get('cluster') or '',
            body.get('task') or '',
            reason=body.get('reason') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='stop_task')


@require_http_methods(['POST'])
def ecs_tasks_protection(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_task_protection(
            body.get('cluster') or '',
            body.get('tasks') or [],
            protection_enabled=_truthy(body.get('protection_enabled')),
            expires_in_minutes=body.get('expires_in_minutes'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='update_task_protection')


@require_http_methods(['POST'])
def ecs_container_instances_state(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_container_instances_state(
            body.get('cluster') or '',
            body.get('container_instances') or [],
            body.get('status') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='update_container_instances_state')


@require_http_methods(['POST'])
def ecs_services_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_service(
            cluster=body.get('cluster') or '',
            service_name=body.get('service_name') or '',
            task_definition=body.get('task_definition') or '',
            desired_count=body.get('desired_count', 1),
            launch_type=body.get('launch_type') or 'FARGATE',
            network_configuration=body.get('network_configuration') or {},
            tags=body.get('tags') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='create_service')


@require_http_methods(['POST'])
def ecs_services_update(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_service(
            cluster=body.get('cluster') or '',
            service=body.get('service') or '',
            desired_count=body.get('desired_count'),
            task_definition=body.get('task_definition') or '',
            network_configuration=body.get('network_configuration') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='update_service')


@require_http_methods(['POST'])
def ecs_services_delete(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(delete_service(
            body.get('cluster') or '',
            body.get('service') or '',
            force=_truthy(body.get('force', True)),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='delete_service')


@require_http_methods(['POST', 'DELETE'])
def ecs_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(tag_resource(body.get('resource_arn') or '', body.get('tags') or []))
        return JsonResponse(untag_resource(body.get('resource_arn') or '', body.get('tag_keys') or []))
    except Exception as exc:
        operation = 'tag_resource' if request.method == 'POST' else 'untag_resource'
        return handle_action_error(exc, service='ecs', operation=operation)


@require_http_methods(['POST'])
def ecs_account_settings_put(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_account_setting(
            body.get('name') or '',
            body.get('value') or '',
            principal_arn=body.get('principal_arn') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='put_account_setting')


@require_http_methods(['POST'])
def ecs_services_scale(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(scale_service(
            body.get('cluster') or '',
            body.get('service') or '',
            body.get('desired_count', 1),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='scale_service')


@require_http_methods(['POST'])
def ecs_fargate_validate(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(validate_fargate_configuration(
            cpu=body.get('cpu') or '256',
            memory=body.get('memory') or '512',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='validate_fargate_configuration')


@require_http_methods(['POST'])
def ecs_task_definitions_diff(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(diff_task_definitions(
            body.get('task_definition_a') or '',
            body.get('task_definition_b') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecs', operation='diff_task_definitions')
