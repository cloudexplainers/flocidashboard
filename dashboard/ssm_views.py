"""HTTP endpoints for the SSM Parameter Store workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .ssm_api import delete_parameter, get_parameter, put_parameter


@require_http_methods(['POST'])
def ssm_parameters_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_parameter(
            body.get('name', ''),
            body.get('value'),
            parameter_type=body.get('type') or 'String',
            description=body.get('description') or '',
            overwrite=bool(body.get('overwrite')),
            **{key: body[key] for key in ('key_id', 'tier', 'data_type') if body.get(key)},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ssm', operation='put_parameter')


@require_http_methods(['GET', 'PUT', 'DELETE'])
def ssm_parameter_value(request, parameter_name: str):
    try:
        if request.method == 'GET':
            return JsonResponse(get_parameter(parameter_name, with_decryption=True))
        if request.method == 'PUT':
            body = parse_json_body(request)
            return JsonResponse(put_parameter(
                parameter_name,
                body.get('value'),
                parameter_type=body.get('type') or 'String',
                description=body.get('description') or '',
                overwrite=True,
                **{key: body[key] for key in ('key_id', 'tier', 'data_type') if body.get(key)},
            ))
        return JsonResponse(delete_parameter(parameter_name))
    except Exception as exc:
        operation = {
            'GET': 'get_parameter',
            'PUT': 'put_parameter',
            'DELETE': 'delete_parameter',
        }.get(request.method, 'parameter_value')
        return handle_action_error(exc, service='ssm', operation=operation)


@require_http_methods(['GET'])
def ssm_history(request):
    from .ssm_api import parameter_history
    try:
        return JsonResponse(parameter_history(request.GET.get('name', '')))
    except Exception as exc:
        return handle_action_error(exc, service='ssm', operation='get_parameter_history')


@require_http_methods(['GET', 'POST', 'DELETE'])
def ssm_tags(request):
    from .ssm_api import parameter_tags
    try:
        body = parse_json_body(request) if request.method != 'GET' else {}
        name = body.get('name') or request.GET.get('name', '')
        kwargs = {'tags': body.get('tags')} if request.method == 'POST' else {'tag_keys': body.get('tag_keys')} if request.method == 'DELETE' else {}
        if request.method == 'POST' and kwargs['tags'] is None:
            raise ValueError('Tags are required')
        if request.method == 'DELETE' and kwargs['tag_keys'] is None:
            raise ValueError('Tag keys are required')
        return JsonResponse(parameter_tags(name, **kwargs))
    except Exception as exc:
        return handle_action_error(exc, service='ssm', operation='parameter_tags')


@require_http_methods(['POST', 'DELETE'])
def ssm_documents(request):
    from .ssm_api import create_document, delete_document
    try:
        body = parse_json_body(request)
        return JsonResponse(create_document(body.get('name', ''), body.get('content', ''), body.get('document_type', 'Command')) if request.method == 'POST' else delete_document(body.get('name', '')))
    except Exception as exc:
        return handle_action_error(exc, service='ssm', operation='document')
