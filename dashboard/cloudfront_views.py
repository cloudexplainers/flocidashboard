"""HTTP endpoints for the CloudFront workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .cloudfront_api import (
    create_cache_policy,
    create_distribution,
    create_function,
    create_invalidation,
    list_invalidations,
    get_invalidation,
    create_origin_access_identity,
    delete_distribution,
    delete_function,
    publish_function,
    tag_resource,
    untag_resource,
    update_distribution,
)


@require_http_methods(['POST'])
def cloudfront_distributions_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_distribution(
            body.get('caller_reference', ''),
            body.get('origin_id', ''),
            body.get('origin_domain_name', ''),
            comment=body.get('comment', ''),
            enabled=body.get('enabled') is not False,
            aliases=body.get('aliases'),
            viewer_protocol_policy=body.get('viewer_protocol_policy') or 'redirect-to-https',
            cache_policy_id=body.get('cache_policy_id', ''),
            **{key: body[key] for key in ('origin_type', 'origin_protocol_policy', 'http_port', 'https_port') if key in body},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudfront', operation='create_distribution')


@require_http_methods(['PUT', 'DELETE'])
def cloudfront_distribution_detail(request, distribution_id: str):
    if request.method == 'DELETE':
        try:
            body = parse_json_body(request) if request.body else {}
            return JsonResponse(delete_distribution(distribution_id, if_match=body.get('if_match', '')))
        except Exception as exc:
            return handle_action_error(exc, service='cloudfront', operation='delete_distribution')

    try:
        body = parse_json_body(request)
        return JsonResponse(update_distribution(
            distribution_id,
            body.get('config'),
            if_match=body.get('if_match', ''),
            comment=body.get('comment', ''),
            enabled=body.get('enabled') if 'enabled' in body else None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudfront', operation='update_distribution')


@require_http_methods(['GET', 'POST'])
def cloudfront_invalidations_create(request, distribution_id: str):
    try:
        if request.method == 'GET':
            return JsonResponse(list_invalidations(distribution_id))
        body = parse_json_body(request)
        return JsonResponse(create_invalidation(
            distribution_id,
            body.get('paths'),
            caller_reference=body.get('caller_reference', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudfront', operation='list_invalidations' if request.method == 'GET' else 'create_invalidation')


@require_http_methods(['POST'])
def cloudfront_cache_policies_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_cache_policy(
            body.get('name', ''),
            default_ttl=body.get('default_ttl', 86400),
            max_ttl=body.get('max_ttl', 31536000),
            min_ttl=body.get('min_ttl', 0),
            comment=body.get('comment', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudfront', operation='create_cache_policy')


@require_http_methods(['POST'])
def cloudfront_origin_access_identities_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_origin_access_identity(
            body.get('caller_reference', ''),
            comment=body.get('comment', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudfront', operation='create_origin_access_identity')


@require_http_methods(['POST'])
def cloudfront_functions_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_function(
            body.get('name', ''),
            body.get('code', ''),
            comment=body.get('comment', ''),
            runtime=body.get('runtime') or 'cloudfront-js-1.0',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cloudfront', operation='create_function')


@require_http_methods(['POST', 'DELETE'])
def cloudfront_function_detail(request, function_name: str):
    try:
        body = parse_json_body(request) if request.body else {}
        if request.method == 'DELETE':
            return JsonResponse(delete_function(function_name, if_match=body.get('if_match', '')))
        return JsonResponse(publish_function(function_name, if_match=body.get('if_match', '')))
    except Exception as exc:
        operation = 'delete_function' if request.method == 'DELETE' else 'publish_function'
        return handle_action_error(exc, service='cloudfront', operation=operation)


@require_http_methods(['POST', 'DELETE'])
def cloudfront_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(untag_resource(body.get('resource_arn', ''), body.get('tag_keys')))
        return JsonResponse(tag_resource(body.get('resource_arn', ''), body.get('tags')))
    except Exception as exc:
        operation = 'untag_resource' if request.method == 'DELETE' else 'tag_resource'
        return handle_action_error(exc, service='cloudfront', operation=operation)


@require_http_methods(['GET'])
def cloudfront_invalidation_detail(request, distribution_id: str, invalidation_id: str):
    try:
        return JsonResponse(get_invalidation(distribution_id, invalidation_id))
    except Exception as exc:
        return handle_action_error(exc, service='cloudfront', operation='get_invalidation')
