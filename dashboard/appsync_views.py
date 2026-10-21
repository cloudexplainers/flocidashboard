"""HTTP endpoints for the AppSync management workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .appsync_api import (
    create_api_key,
    create_data_source,
    create_function,
    create_graphql_api,
    create_resolver,
    create_type,
    delete_api_key,
    delete_data_source,
    delete_function,
    delete_graphql_api,
    delete_resolver,
    delete_type,
    execute_graphql,
    start_schema_creation,
    get_schema,
    tag_resource,
    untag_resource,
)


def _action(request, operation, handler):
    try:
        return JsonResponse(handler(parse_json_body(request)))
    except Exception as exc:
        return handle_action_error(exc, service='appsync', operation=operation)


@require_http_methods(['POST'])
def appsync_apis_create(request):
    return _action(request, 'create_graphql_api', lambda body: create_graphql_api(
        body.get('name') or '',
        authentication_type=body.get('authentication_type') or 'API_KEY',
        tags=body.get('tags') or {},
    ))


@require_http_methods(['DELETE'])
def appsync_api_delete(request, api_id: str):
    try:
        return JsonResponse(delete_graphql_api(api_id))
    except Exception as exc:
        return handle_action_error(exc, service='appsync', operation='delete_graphql_api')


@require_http_methods(['GET', 'POST'])
def appsync_schema_create(request, api_id: str):
    if request.method == 'GET':
        try:
            return JsonResponse(get_schema(api_id))
        except Exception as exc:
            return handle_action_error(exc, service='appsync', operation='get_schema')
    return _action(request, 'start_schema_creation', lambda body: start_schema_creation(api_id, body.get('definition') or ''))


@require_http_methods(['POST', 'DELETE'])
def appsync_api_keys(request, api_id: str):
    if request.method == 'POST':
        return _action(request, 'create_api_key', lambda body: create_api_key(api_id, description=body.get('description') or '', expires=body.get('expires')))
    return _action(request, 'delete_api_key', lambda body: delete_api_key(api_id, body.get('key_id') or ''))


@require_http_methods(['POST', 'DELETE'])
def appsync_data_sources(request, api_id: str):
    if request.method == 'POST':
        return _action(request, 'create_data_source', lambda body: create_data_source(api_id, body.get('name') or '', source_type=body.get('source_type') or 'NONE', description=body.get('description') or '', **{key: body[key] for key in ('service_role_arn', 'config') if key in body}))
    return _action(request, 'delete_data_source', lambda body: delete_data_source(api_id, body.get('name') or ''))


@require_http_methods(['POST', 'DELETE'])
def appsync_resolvers(request, api_id: str):
    if request.method == 'POST':
        return _action(request, 'create_resolver', lambda body: create_resolver(api_id, body.get('type_name') or '', body.get('field_name') or '', data_source_name=body.get('data_source_name') or '', **{key: body[key] for key in ('request_mapping_template', 'response_mapping_template') if key in body}))
    return _action(request, 'delete_resolver', lambda body: delete_resolver(api_id, body.get('type_name') or '', body.get('field_name') or ''))


@require_http_methods(['POST', 'DELETE'])
def appsync_functions(request, api_id: str):
    if request.method == 'POST':
        return _action(request, 'create_function', lambda body: create_function(api_id, body.get('name') or '', body.get('data_source_name') or '', description=body.get('description') or ''))
    return _action(request, 'delete_function', lambda body: delete_function(api_id, body.get('function_id') or ''))


@require_http_methods(['POST', 'DELETE'])
def appsync_types(request, api_id: str):
    if request.method == 'POST':
        return _action(request, 'create_type', lambda body: create_type(api_id, body.get('definition') or '', format_name=body.get('format') or 'SDL'))
    return _action(request, 'delete_type', lambda body: delete_type(api_id, body.get('type_name') or ''))


@require_http_methods(['POST', 'DELETE'])
def appsync_tags(request):
    if request.method == 'POST':
        return _action(request, 'tag_resource', lambda body: tag_resource(body.get('resource_arn') or '', body.get('tags') or {}))
    return _action(request, 'untag_resource', lambda body: untag_resource(body.get('resource_arn') or '', body.get('tag_keys') or []))


@require_http_methods(['POST'])
def appsync_graphql_run(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(execute_graphql(
            api_id=body.get('api_id', ''),
            query=body.get('query', ''),
            variables=body.get('variables'),
            operation_name=body.get('operation_name'),
            api_key=body.get('api_key'),
            endpoint_url=body.get('endpoint_url'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='appsync', operation='execute_graphql')
