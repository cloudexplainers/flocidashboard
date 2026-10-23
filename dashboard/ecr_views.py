"""HTTP endpoints for the ECR repository workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .ecr_api import (
    batch_delete_image,
    batch_get_image,
    create_repository,
    delete_lifecycle_policy,
    delete_repository,
    delete_repository_policy,
    get_authorization_token,
    get_push_commands,
    put_image_tag_mutability,
    put_lifecycle_policy,
    put_mock_image,
    run_garbage_collection,
    set_repository_policy,
    tag_resource,
    untag_resource,
)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on', 'force'}
    return bool(value)


@require_http_methods(['POST'])
def ecr_repositories_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_repository(
            body.get('name') or '',
            image_tag_mutability=body.get('image_tag_mutability') or 'MUTABLE',
            tags=body.get('tags') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='create_repository')


@require_http_methods(['POST'])
def ecr_repositories_delete(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(delete_repository(
            body.get('repository_name') or '',
            force=_truthy(body.get('force', True)),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='delete_repository')


@require_http_methods(['POST'])
def ecr_authorization_token(request):
    try:
        return JsonResponse(get_authorization_token())
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='get_authorization_token')


@require_http_methods(['POST'])
def ecr_images_delete(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(batch_delete_image(
            body.get('repository_name') or '',
            body.get('image_ids') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='batch_delete_image')


@require_http_methods(['POST'])
def ecr_images_get(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(batch_get_image(
            body.get('repository_name') or '',
            body.get('image_ids') or [],
            body.get('accepted_media_types') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='batch_get_image')


@require_http_methods(['POST'])
def ecr_tag_mutability_put(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_image_tag_mutability(
            body.get('repository_name') or '',
            body.get('image_tag_mutability') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='put_image_tag_mutability')


@require_http_methods(['POST', 'DELETE'])
def ecr_lifecycle_policy(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(put_lifecycle_policy(
                body.get('repository_name') or '',
                body.get('lifecycle_policy_text'),
            ))
        return JsonResponse(delete_lifecycle_policy(body.get('repository_name') or ''))
    except Exception as exc:
        operation = 'put_lifecycle_policy' if request.method == 'POST' else 'delete_lifecycle_policy'
        return handle_action_error(exc, service='ecr', operation=operation)


@require_http_methods(['POST', 'DELETE'])
def ecr_repository_policy(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(set_repository_policy(
                body.get('repository_name') or '',
                body.get('policy_text'),
                force=_truthy(body.get('force', True)),
            ))
        return JsonResponse(delete_repository_policy(body.get('repository_name') or ''))
    except Exception as exc:
        operation = 'set_repository_policy' if request.method == 'POST' else 'delete_repository_policy'
        return handle_action_error(exc, service='ecr', operation=operation)


@require_http_methods(['POST', 'DELETE'])
def ecr_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(tag_resource(body.get('resource_arn') or '', body.get('tags') or []))
        return JsonResponse(untag_resource(body.get('resource_arn') or '', body.get('tag_keys') or []))
    except Exception as exc:
        operation = 'tag_resource' if request.method == 'POST' else 'untag_resource'
        return handle_action_error(exc, service='ecr', operation=operation)


@require_http_methods(['POST'])
def ecr_garbage_collection(request):
    try:
        return JsonResponse(run_garbage_collection())
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='garbage_collection')


@require_http_methods(['GET', 'POST'])
def ecr_push_commands(request, name: str = ''):
    try:
        if not name:
            body = parse_json_body(request) if request.method == 'POST' else {}
            name = body.get('repository_name') or request.GET.get('repository_name') or ''
        region = request.GET.get('region') or None
        return JsonResponse(get_push_commands(name, region=region))
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='get_push_commands')


@require_http_methods(['POST'])
def ecr_mock_image_put(request, name: str = ''):
    try:
        body = parse_json_body(request)
        repo_name = name or body.get('repository_name') or ''
        return JsonResponse(put_mock_image(
            repo_name,
            image_tag=body.get('image_tag') or 'latest',
            image_digest=body.get('image_digest'),
            image_manifest=body.get('image_manifest'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ecr', operation='put_mock_image')
