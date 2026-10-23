"""HTTP endpoints for the Secrets Manager workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .secretsmanager_api import (
    create_secret, delete_secret, get_random_password, get_secret_value, put_secret_value,
    restore_secret, rotate_secret, tag_secret, untag_secret, update_secret, update_version_stage,
    get_replication, promote_replica, remove_replica_regions, replicate_secret,
)


@require_http_methods(['POST'])
def secretsmanager_secrets_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_secret(
            body.get('name') or body.get('secret_name') or '',
            body.get('value') if body.get('value') is not None else (body.get('secret_string') if body.get('secret_string') is not None else body.get('secret_value')),
            description=body.get('description') or '',
            kms_key_id=body.get('kms_key_id') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation='create_secret')


@require_http_methods(['GET', 'PUT', 'DELETE'])
def secretsmanager_secret_value(request, secret_id: str):
    try:
        if request.method == 'GET':
            return JsonResponse(get_secret_value(
                secret_id,
                version_id=request.GET.get('version_id') or '',
                version_stage=request.GET.get('version_stage') or '',
            ))
        body = parse_json_body(request) if request.body else {}
        if request.method == 'PUT':
            return JsonResponse(put_secret_value(secret_id, body.get('value')))
        return JsonResponse(delete_secret(
            secret_id,
            recovery_window_days=body.get('recovery_window_days') or 30,
            force_delete_without_recovery=bool(body.get('force_delete_without_recovery')),
        ))
    except Exception as exc:
        operation = {
            'GET': 'get_secret_value',
            'PUT': 'put_secret_value',
            'DELETE': 'delete_secret',
        }.get(request.method, 'secret_value')
        return handle_action_error(exc, service='secretsmanager', operation=operation)


@require_http_methods(['PATCH'])
def secretsmanager_secret_metadata(request, secret_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_secret(secret_id, description=body.get('description') or '', kms_key_id=body.get('kms_key_id') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation='update_secret')


@require_http_methods(['POST'])
def secretsmanager_secret_restore(request, secret_id: str):
    try:
        return JsonResponse(restore_secret(secret_id))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation='restore_secret')


@require_http_methods(['POST'])
def secretsmanager_secret_rotate(request, secret_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(rotate_secret(secret_id, rotation_lambda_arn=body.get('rotation_lambda_arn') or '', rotation_rules=body.get('rotation_rules') or {}, rotate_immediately=body.get('rotate_immediately', True)))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation='rotate_secret')


@require_http_methods(['POST', 'DELETE'])
def secretsmanager_secret_tags(request, secret_id: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(untag_secret(secret_id, body.get('tag_keys') or []))
        return JsonResponse(tag_secret(secret_id, body.get('tags') or []))
    except Exception as exc:
        operation = 'untag_resource' if request.method == 'DELETE' else 'tag_resource'
        return handle_action_error(exc, service='secretsmanager', operation=operation)


@require_http_methods(['POST'])
def secretsmanager_secret_version_stage(request, secret_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_version_stage(secret_id, body.get('version_stage') or '', body.get('move_to_version_id') or '', remove_from_version_id=body.get('remove_from_version_id') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation='update_secret_version_stage')


@require_http_methods(['POST'])
def secretsmanager_random_password(request):
    try:
        return JsonResponse(get_random_password(parse_json_body(request)))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation='get_random_password')


@require_http_methods(['GET', 'POST', 'DELETE'])
def secretsmanager_secret_replication(request, secret_id: str):
    operation = {'GET': 'describe_secret', 'POST': 'replicate_secret_to_regions', 'DELETE': 'remove_regions_from_replication'}[request.method]
    try:
        if request.method == 'GET':
            return JsonResponse(get_replication(secret_id))
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(remove_replica_regions(secret_id, body.get('regions')))
        return JsonResponse(replicate_secret(secret_id, body.get('regions'), force_overwrite=body.get('force_overwrite') is True))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation=operation)


@require_http_methods(['POST'])
def secretsmanager_secret_promote(request, secret_id: str):
    try:
        return JsonResponse(promote_replica(secret_id, parse_json_body(request).get('region') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='secretsmanager', operation='stop_replication_to_replica')
