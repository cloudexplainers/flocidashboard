"""HTTP endpoints for the Cognito auth workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .cognito_api import (
    add_user_to_group,
    change_user_state,
    admin_create_user,
    admin_delete_user,
    admin_set_user_password,
    create_group,
    create_identity_pool,
    create_resource_server,
    create_user_pool,
    create_user_pool_client,
    delete_group,
    delete_resource_server,
    delete_user_pool,
    delete_user_pool_client,
    initiate_auth,
    global_sign_out,
    oauth_client_credentials,
    remove_user_from_group,
    revoke_token,
)


@require_http_methods(['POST'])
def cognito_user_pools_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_user_pool(body.get('name', ''), tags=body.get('tags')))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='create_user_pool')


@require_http_methods(['DELETE'])
def cognito_user_pool_delete(request, user_pool_id: str):
    try:
        return JsonResponse(delete_user_pool(user_pool_id))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='delete_user_pool')


@require_http_methods(['POST'])
def cognito_user_pool_clients_create(request, user_pool_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_user_pool_client(
            user_pool_id,
            body.get('name', ''),
            generate_secret=body.get('generate_secret') is not False,
            allowed_oauth_scopes=body.get('allowed_oauth_scopes'),
            explicit_auth_flows=body.get('explicit_auth_flows'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='create_user_pool_client')


@require_http_methods(['DELETE'])
def cognito_user_pool_client_delete(request, user_pool_id: str, client_id: str):
    try:
        return JsonResponse(delete_user_pool_client(user_pool_id, client_id))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='delete_user_pool_client')


@require_http_methods(['POST'])
def cognito_resource_servers_create(request, user_pool_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_resource_server(
            user_pool_id,
            body.get('identifier', ''),
            body.get('name', ''),
            scopes=body.get('scopes'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='create_resource_server')


@require_http_methods(['DELETE'])
def cognito_resource_server_delete(request, user_pool_id: str, identifier: str):
    try:
        return JsonResponse(delete_resource_server(user_pool_id, identifier))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='delete_resource_server')


@require_http_methods(['POST'])
def cognito_users_create(request, user_pool_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(admin_create_user(
            user_pool_id,
            body.get('username', ''),
            temporary_password=body.get('temporary_password') or '',
            attributes=body.get('attributes'),
            message_action=body.get('message_action') or 'SUPPRESS',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='admin_create_user')


@require_http_methods(['DELETE'])
def cognito_user_delete(request, user_pool_id: str, username: str):
    try:
        return JsonResponse(admin_delete_user(user_pool_id, username))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='admin_delete_user')


@require_http_methods(['POST'])
def cognito_user_password_set(request, user_pool_id: str, username: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(admin_set_user_password(
            user_pool_id,
            username,
            body.get('password', ''),
            permanent=body.get('permanent') is not False,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='admin_set_user_password')


@require_http_methods(['POST'])
def cognito_groups_create(request, user_pool_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_group(
            user_pool_id,
            body.get('group_name', ''),
            description=body.get('description') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='create_group')


@require_http_methods(['DELETE'])
def cognito_group_delete(request, user_pool_id: str, group_name: str):
    try:
        return JsonResponse(delete_group(user_pool_id, group_name))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='delete_group')


@require_http_methods(['POST', 'DELETE'])
def cognito_group_membership(request, user_pool_id: str, group_name: str, username: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(remove_user_from_group(user_pool_id, username, group_name))
        return JsonResponse(add_user_to_group(user_pool_id, username, group_name))
    except Exception as exc:
        operation = 'remove_user_from_group' if request.method == 'DELETE' else 'add_user_to_group'
        return handle_action_error(exc, service='cognito', operation=operation)


@require_http_methods(['POST'])
def cognito_auth_initiate(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(initiate_auth(
            body.get('client_id', ''),
            body.get('username', ''),
            body.get('password', ''),
            auth_flow=body.get('auth_flow') or 'USER_PASSWORD_AUTH',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='initiate_auth')


@require_http_methods(['POST'])
def cognito_oauth_token(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(oauth_client_credentials(
            body.get('client_id', ''),
            body.get('client_secret') or '',
            scope=body.get('scope') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='oauth_client_credentials')


@require_http_methods(['POST'])
def cognito_global_sign_out(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(global_sign_out(body.get('access_token') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='global_sign_out')


@require_http_methods(['POST'])
def cognito_revoke_token(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(revoke_token(
            body.get('client_id') or '', body.get('token') or '', client_secret=body.get('client_secret') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='revoke_token')


@require_http_methods(['POST'])
def cognito_user_state(request, user_pool_id: str, username: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(change_user_state(user_pool_id, username, body.get('action', '')))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='change_user_state')


@require_http_methods(['POST'])
def cognito_identity_pools_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_identity_pool(
            body.get('identity_pool_name', ''),
            allow_unauthenticated=body.get('allow_unauthenticated_identities') is True,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='cognito', operation='create_identity_pool')
