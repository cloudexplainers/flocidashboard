"""HTTP endpoints for the Step Functions execution workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .stepfunctions_api import (
    create_state_machine, delete_state_machine, delete_state_machine_version,
    describe_execution, describe_state_machine, get_execution_history,
    publish_state_machine_version, start_execution, stop_execution, update_state_machine,
    create_state_machine_alias, delete_state_machine_alias, list_versions_and_aliases,
    update_state_machine_alias,
)


@require_http_methods(['POST'])
def stepfunctions_state_machines_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_state_machine(
            name=body.get('name', ''),
            definition=body.get('definition'),
            role_arn=body.get('role_arn', ''),
            state_machine_type=body.get('type') or 'STANDARD',
            logging_configuration=body.get('logging_configuration'),
            tracing_configuration=body.get('tracing_configuration'),
            tags=body.get('tags'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='create_state_machine')


@require_http_methods(['GET', 'PATCH', 'DELETE'])
def stepfunctions_state_machine_detail(request, state_machine_arn: str):
    try:
        if request.method == 'PATCH':
            body = parse_json_body(request)
            return JsonResponse(update_state_machine(state_machine_arn, body.get('definition'), body.get('role_arn') or ''))
        if request.method == 'DELETE':
            return JsonResponse(delete_state_machine(state_machine_arn))
        return JsonResponse(describe_state_machine(state_machine_arn))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='delete_state_machine' if request.method == 'DELETE' else 'describe_state_machine')


@require_http_methods(['POST'])
def stepfunctions_executions_start(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(start_execution(
            body.get('state_machine_arn', ''),
            body.get('input'),
            name=body.get('name') or None,
            trace_header=body.get('trace_header') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='start_execution')


@require_http_methods(['GET'])
def stepfunctions_execution_detail(request, execution_arn: str):
    try:
        return JsonResponse(describe_execution(execution_arn))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='describe_execution')


@require_http_methods(['GET'])
def stepfunctions_execution_history(request, execution_arn: str):
    try:
        return JsonResponse(get_execution_history(execution_arn, **({'next_token': request.GET['next_token']} if request.GET.get('next_token') else {})))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='get_execution_history')


@require_http_methods(['POST'])
def stepfunctions_state_machine_versions_publish(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(publish_state_machine_version(
            body.get('state_machine_arn', ''),
            revision_id=body.get('revision_id') or None,
            description=body.get('description') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='publish_state_machine_version')


@require_http_methods(['DELETE'])
def stepfunctions_state_machine_version_delete(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(delete_state_machine_version(body.get('state_machine_version_arn', '')))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='delete_state_machine_version')


@require_http_methods(['POST'])
def stepfunctions_executions_stop(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(stop_execution(
            body.get('execution_arn', ''),
            error=body.get('error') or None,
            cause=body.get('cause') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='stop_execution')


@require_http_methods(['GET', 'POST', 'DELETE'])
def stepfunctions_activities(request):
    from .aws import FlociClientFactory, _clean_response
    try:
        client = FlociClientFactory().client('stepfunctions')
        if request.method == 'GET':
            activities = client.get_paginator('list_activities').paginate().build_full_result().get('activities', [])
            return JsonResponse({'activities': _clean_response(activities)})
        body = parse_json_body(request)
        if request.method == 'POST':
            name = (body.get('name') or '').strip()
            if not name:
                raise ValueError('Activity name is required')
            return JsonResponse(_clean_response(client.create_activity(name=name)))
        arn = (body.get('activity_arn') or '').strip()
        if ':states:' not in arn or ':activity:' not in arn:
            raise ValueError('A valid activity ARN is required')
        client.delete_activity(activityArn=arn)
        return JsonResponse({'activity_arn': arn, 'deleted': True})
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='activities')


@require_http_methods(['POST'])
def stepfunctions_task_callback(request):
    import json
    from .aws import FlociClientFactory
    try:
        body = parse_json_body(request)
        token = (body.get('task_token') or '').strip()
        if not token:
            raise ValueError('Task token is required')
        success = body.get('success')
        if not isinstance(success, bool):
            raise ValueError('Success must be a boolean')
        if success:
            output = body.get('output')
            if isinstance(output, str):
                json.loads(output)
            else:
                output = json.dumps(output)
            FlociClientFactory().client('stepfunctions').send_task_success(taskToken=token, output=output)
        else:
            FlociClientFactory().client('stepfunctions').send_task_failure(taskToken=token, error=body.get('error') or 'TaskFailed', cause=body.get('cause') or '')
        return JsonResponse({'success': success})
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='task_callback')


@require_http_methods(['GET'])
def stepfunctions_versions_aliases(request):
    try:
        return JsonResponse(list_versions_and_aliases(request.GET.get('state_machine_arn', '')))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation='list_state_machine_aliases')


@require_http_methods(['POST', 'PUT', 'DELETE'])
def stepfunctions_aliases(request):
    operation = {'POST': 'create_state_machine_alias', 'PUT': 'update_state_machine_alias', 'DELETE': 'delete_state_machine_alias'}[request.method]
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(delete_state_machine_alias(body.get('alias_arn', '')))
        if request.method == 'PUT':
            return JsonResponse(update_state_machine_alias(body.get('alias_arn', ''), body.get('routing'), body.get('description')))
        return JsonResponse(create_state_machine_alias(body.get('name', ''), body.get('routing'), body.get('description') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='stepfunctions', operation=operation)
