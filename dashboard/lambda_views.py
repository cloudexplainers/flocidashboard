"""HTTP endpoints for the Lambda management workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .lambda_api import (
    add_permission, create_function, delete_alias, delete_event_source_mapping,
    delete_function, delete_function_url, get_event_templates, get_function_url,
    invoke_function, invoke_function_url, list_aliases, list_event_source_mappings,
    list_versions, publish_version, remove_permission, save_alias,
    save_event_source_mapping, save_function_url, set_concurrency,
    deploy_function_source, get_function_source,
    update_function_code, update_function_configuration, update_tags,
)


def _error(exc: Exception, operation: str):
    return handle_action_error(exc, service='lambda', operation=operation)


@require_http_methods(['POST'])
def lambda_functions_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_function(body.get('name'), body.get('role'), body.get('code'), configuration=body.get('configuration'), tags=body.get('tags')))
    except Exception as exc:
        return _error(exc, 'create_function')


@require_http_methods(['PATCH', 'DELETE'])
def lambda_function_detail(request, function_name: str):
    try:
        if request.method == 'DELETE':
            body = parse_json_body(request) if request.body else {}
            return JsonResponse(delete_function(function_name, qualifier=body.get('qualifier') or ''))
        return JsonResponse(update_function_configuration(function_name, parse_json_body(request).get('configuration')))
    except Exception as exc:
        return _error(exc, 'delete_function' if request.method == 'DELETE' else 'update_function_configuration')


@require_http_methods(['GET', 'PUT'])
def lambda_function_source(request, function_name: str):
    try:
        if request.method == 'GET':
            return JsonResponse(get_function_source(function_name))
        body = parse_json_body(request)
        return JsonResponse(deploy_function_source(function_name, body.get('files')))
    except Exception as exc:
        return _error(exc, 'get_function' if request.method == 'GET' else 'update_function_code')


@require_http_methods(['PUT'])
def lambda_function_code(request, function_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_function_code(function_name, body.get('code'), publish=body.get('publish', False)))
    except Exception as exc:
        return _error(exc, 'update_function_code')


@require_http_methods(['POST'])
def lambda_functions_invoke(request, function_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(invoke_function(
            function_name,
            body.get('payload'),
            qualifier=body.get('qualifier') or None,
            invocation_type=body.get('invocation_type') or 'RequestResponse',
        ))
    except Exception as exc:
        return _error(exc, 'invoke_function')


@require_http_methods(['GET', 'POST'])
def lambda_function_versions(request, function_name: str):
    if request.method == 'GET':
        try:
            return JsonResponse(list_versions(function_name))
        except Exception as exc:
            return _error(exc, 'list_versions_by_function')
    try:
        return JsonResponse(publish_version(function_name, description=parse_json_body(request).get('description') or ''))
    except Exception as exc:
        return _error(exc, 'publish_version')


@require_http_methods(['GET', 'POST'])
def lambda_function_aliases(request, function_name: str):
    if request.method == 'GET':
        try:
            return JsonResponse(list_aliases(function_name))
        except Exception as exc:
            return _error(exc, 'list_aliases')
    try:
        body = parse_json_body(request)
        return JsonResponse(save_alias(function_name, body.get('name'), body.get('function_version'), description=body.get('description') or '', update=False))
    except Exception as exc:
        return _error(exc, 'create_alias')


@require_http_methods(['PUT', 'DELETE'])
def lambda_function_alias_detail(request, function_name: str, alias_name: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_alias(function_name, alias_name))
        body = parse_json_body(request)
        return JsonResponse(save_alias(function_name, alias_name, body.get('function_version'), description=body.get('description') or '', update=True))
    except Exception as exc:
        return _error(exc, 'delete_alias' if request.method == 'DELETE' else 'update_alias')


@require_http_methods(['GET', 'POST'])
def lambda_function_mappings(request, function_name: str):
    if request.method == 'GET':
        try:
            return JsonResponse(list_event_source_mappings(function_name))
        except Exception as exc:
            return _error(exc, 'list_event_source_mappings')
    try:
        return JsonResponse(save_event_source_mapping(function_name, parse_json_body(request).get('options')))
    except Exception as exc:
        return _error(exc, 'create_event_source_mapping')


@require_http_methods(['PUT', 'DELETE'])
def lambda_mapping_detail(request, uuid: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_event_source_mapping(uuid))
        body = parse_json_body(request)
        return JsonResponse(save_event_source_mapping('', body.get('options'), uuid=uuid))
    except Exception as exc:
        return _error(exc, 'delete_event_source_mapping' if request.method == 'DELETE' else 'update_event_source_mapping')


@require_http_methods(['GET', 'POST', 'PUT', 'DELETE'])
def lambda_function_url(request, function_name: str):
    if request.method == 'GET':
        try:
            # JSON null means "no function URL configured".
            return JsonResponse(get_function_url(function_name), safe=False)
        except Exception as exc:
            return _error(exc, 'get_function_url_config')
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_function_url(function_name))
        return JsonResponse(save_function_url(function_name, parse_json_body(request).get('options'), update=request.method == 'PUT'))
    except Exception as exc:
        operation = {'POST': 'create_function_url_config', 'PUT': 'update_function_url_config', 'DELETE': 'delete_function_url_config'}[request.method]
        return _error(exc, operation)


@require_http_methods(['PUT', 'DELETE'])
def lambda_function_concurrency(request, function_name: str):
    try:
        value = None if request.method == 'DELETE' else parse_json_body(request).get('reserved_concurrency')
        return JsonResponse(set_concurrency(function_name, value))
    except Exception as exc:
        return _error(exc, 'delete_function_concurrency' if request.method == 'DELETE' else 'put_function_concurrency')


@require_http_methods(['POST', 'DELETE'])
def lambda_function_permissions(request, function_name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(remove_permission(function_name, body.get('statement_id')))
        return JsonResponse(add_permission(function_name, body.get('statement')))
    except Exception as exc:
        return _error(exc, 'remove_permission' if request.method == 'DELETE' else 'add_permission')


@require_http_methods(['POST', 'DELETE'])
def lambda_function_tags(request, function_name: str):
    try:
        body = parse_json_body(request)
        arn = body.get('resource_arn') or function_name
        return JsonResponse(update_tags(arn, body.get('tags'), remove=body.get('tag_keys') if request.method == 'DELETE' else None))
    except Exception as exc:
        return _error(exc, 'untag_resource' if request.method == 'DELETE' else 'tag_resource')


@require_http_methods(['GET'])
def lambda_test_event_templates(request):
    try:
        return JsonResponse(get_event_templates())
    except Exception as exc:
        return _error(exc, 'get_event_templates')


@require_http_methods(['POST'])
def lambda_function_url_test(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(invoke_function_url(
            url=body.get('url', ''),
            method=body.get('method') or 'POST',
            headers=body.get('headers') or None,
            body=body.get('body') or None,
            query_params=body.get('query_params') or None,
        ))
    except Exception as exc:
        return _error(exc, 'invoke_function_url')
