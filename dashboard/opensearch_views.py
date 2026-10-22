"""HTTP endpoints for the OpenSearch domain workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .opensearch_api import (
    add_tags,
    create_domain,
    delete_domain,
    describe_instance_type_limits,
    execute_domain_request,
    get_compatible_versions,
    list_instance_type_details,
    list_tags,
    list_versions,
    remove_tags,
    update_domain_config,
    upgrade_domain,
)


@require_http_methods(['POST'])
def opensearch_domains_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_domain(
            domain_name=body.get('domain_name') or '',
            engine_version=body.get('engine_version') or 'OpenSearch_2.19',
            instance_type=body.get('instance_type') or 'm5.large.search',
            instance_count=body.get('instance_count') or 1,
            ebs_enabled=body.get('ebs_enabled', True),
            volume_type=body.get('volume_type') or 'gp3',
            volume_size=body.get('volume_size') or 10,
            tags=body.get('tags') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='create_domain')


@require_http_methods(['POST'])
def opensearch_domains_update(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_domain_config(
            domain_name=body.get('domain_name') or '',
            engine_version=body.get('engine_version') or '',
            cluster_config=body.get('cluster_config') or {},
            ebs_options=body.get('ebs_options') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='update_domain_config')


@require_http_methods(['POST'])
def opensearch_domains_delete(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(delete_domain(body.get('domain_name') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='delete_domain')


@require_http_methods(['POST'])
def opensearch_domains_upgrade(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(upgrade_domain(
            body.get('domain_name') or '',
            body.get('target_version') or '',
            perform_check_only=body.get('perform_check_only') or False,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='upgrade_domain')


@require_http_methods(['POST', 'DELETE'])
def opensearch_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(add_tags(body.get('arn') or '', body.get('tags') or []))
        return JsonResponse(remove_tags(body.get('arn') or '', body.get('tag_keys') or []))
    except Exception as exc:
        operation = 'add_tags' if request.method == 'POST' else 'remove_tags'
        return handle_action_error(exc, service='opensearch', operation=operation)


@require_http_methods(['POST'])
def opensearch_tags_list(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(list_tags(body.get('arn') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='list_tags')


@require_http_methods(['GET'])
def opensearch_versions(request):
    try:
        return JsonResponse(list_versions())
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='list_versions')


@require_http_methods(['POST'])
def opensearch_compatible_versions(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_compatible_versions(body.get('domain_name') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='get_compatible_versions')


@require_http_methods(['POST'])
def opensearch_instance_type_details(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(list_instance_type_details(body.get('engine_version') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='list_instance_type_details')


@require_http_methods(['POST'])
def opensearch_instance_type_limits(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(describe_instance_type_limits(
            body.get('engine_version') or '',
            body.get('instance_type') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='describe_instance_type_limits')


@require_http_methods(['POST'])
def opensearch_devtools_request(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(execute_domain_request(
            domain_name=body.get('domain_name', ''),
            method=body.get('method') or 'GET',
            path=body.get('path') or '/_cluster/health',
            body=body.get('body'),
            headers=body.get('headers'),
            endpoint_url=body.get('endpoint_url'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='opensearch', operation='execute_domain_request')
