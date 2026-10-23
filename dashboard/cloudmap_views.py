"""HTTP endpoints for the Cloud Map service discovery workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .cloudmap_api import (
    create_namespace,
    create_service,
    delete_namespace,
    delete_service,
    deregister_instance,
    discover_instances,
    register_instance,
    tag_resource,
    untag_resource,
)


def _action(request, operation, handler):
    try:
        return JsonResponse(handler(parse_json_body(request)))
    except Exception as exc:
        return handle_action_error(exc, service='cloudmap', operation=operation)


@require_http_methods(['POST'])
def cloudmap_namespaces_create(request):
    return _action(request, 'create_namespace', lambda body: create_namespace(
        name=body.get('name') or '',
        namespace_type=body.get('namespace_type') or 'HTTP',
        description=body.get('description') or '',
        vpc=body.get('vpc') or '',
        tags=body.get('tags') or {},
    ))


@require_http_methods(['DELETE'])
def cloudmap_namespace_delete(request, namespace_id: str):
    try:
        return JsonResponse(delete_namespace(namespace_id))
    except Exception as exc:
        return handle_action_error(exc, service='cloudmap', operation='delete_namespace')


@require_http_methods(['POST'])
def cloudmap_services_create(request):
    return _action(request, 'create_service', lambda body: create_service(
        name=body.get('name') or '',
        namespace_id=body.get('namespace_id') or '',
        description=body.get('description') or '',
        dns_config=body.get('dns_config') or {},
        health_check_config=body.get('health_check_config') or {},
        health_check_custom_config=body.get('health_check_custom_config') or {},
        tags=body.get('tags') or {},
    ))


@require_http_methods(['DELETE'])
def cloudmap_service_delete(request, service_id: str):
    try:
        return JsonResponse(delete_service(service_id))
    except Exception as exc:
        return handle_action_error(exc, service='cloudmap', operation='delete_service')


@require_http_methods(['POST'])
def cloudmap_instances_register(request, service_id: str):
    return _action(request, 'register_instance', lambda body: register_instance(
        service_id,
        body.get('instance_id') or '',
        body.get('attributes') or {},
    ))


@require_http_methods(['DELETE'])
def cloudmap_instance_deregister(request, service_id: str, instance_id: str):
    try:
        return JsonResponse(deregister_instance(service_id, instance_id))
    except Exception as exc:
        return handle_action_error(exc, service='cloudmap', operation='deregister_instance')


@require_http_methods(['POST'])
def cloudmap_instances_discover(request):
    return _action(request, 'discover_instances', lambda body: discover_instances(
        body.get('namespace_name') or '',
        body.get('service_name') or '',
        query_parameters=body.get('query_parameters') or {},
    ))


@require_http_methods(['POST', 'DELETE'])
def cloudmap_tags(request):
    if request.method == 'POST':
        return _action(request, 'tag_resource', lambda body: tag_resource(body.get('resource_arn') or '', body.get('tags') or {}))
    return _action(request, 'untag_resource', lambda body: untag_resource(body.get('resource_arn') or '', body.get('tag_keys') or []))
