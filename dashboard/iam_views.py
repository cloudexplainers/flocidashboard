"""HTTP endpoints for the IAM identity workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .iam_api import (
    add_user_to_group,
    assume_role,
    attach_managed_policy,
    create_access_key,
    create_group,
    create_instance_profile,
    create_managed_policy,
    create_policy_version,
    create_role,
    create_user,
    cleanup_group,
    cleanup_role,
    cleanup_user,
    delete_access_key,
    delete_inline_policy,
    delete_policy_version,
    detach_managed_policy,
    delete_permissions_boundary,
    get_inline_policy,
    get_managed_policy,
    put_inline_policy,
    put_permissions_boundary,
    remove_user_from_group,
    set_default_policy_version,
    simulate_principal_policy,
    add_role_to_instance_profile,
    update_role_trust_policy,
    update_access_key,
    update_user,
    update_role,
    save_login_profile,
    delete_login_profile,
    get_access_key_last_used,
    remove_role_from_instance_profile,
    delete_instance_profile,
    delete_managed_policy,
    update_tags,
    get_password_policy,
    update_password_policy,
    delete_password_policy,
    generate_credential_report,
    get_credential_report,
)


@require_http_methods(['POST'])
def iam_users_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_user(
            body.get('user_name', ''),
            add_baseline_policy=body.get('add_baseline_policy') is not False,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='create_user')


@require_http_methods(['PATCH', 'DELETE'])
def iam_user_detail(request, user_name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'PATCH':
            return JsonResponse(update_user(user_name, new_name=body.get('new_name') or '', new_path=body.get('new_path') or ''))
        return JsonResponse(cleanup_user(user_name, force=body.get('force') is True))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='update_user' if request.method == 'PATCH' else 'delete_user')


@require_http_methods(['POST'])
def iam_groups_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_group(body.get('group_name', '')))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='create_group')


@require_http_methods(['DELETE'])
def iam_group_detail(request, group_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(cleanup_group(group_name, force=body.get('force') is True))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='delete_group')


@require_http_methods(['POST'])
def iam_roles_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_role(
            body.get('role_name', ''),
            trust_template=body.get('trust_template') or 'lambda',
            trust_policy=body.get('trust_policy') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='create_role')


@require_http_methods(['PATCH', 'DELETE'])
def iam_role_detail(request, role_name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'PATCH':
            return JsonResponse(update_role(role_name, description=body.get('description'), max_session_duration=body.get('max_session_duration')))
        return JsonResponse(cleanup_role(role_name, force=body.get('force') is True))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='update_role' if request.method == 'PATCH' else 'delete_role')


@require_http_methods(['POST'])
def iam_instance_profiles_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_instance_profile(body.get('instance_profile_name', '')))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='create_instance_profile')


@require_http_methods(['POST', 'DELETE'])
def iam_instance_profile_roles(request, instance_profile_name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(remove_role_from_instance_profile(instance_profile_name, body.get('role_name', '')))
        return JsonResponse(add_role_to_instance_profile(instance_profile_name, body.get('role_name', '')))
    except Exception as exc:
        operation = 'remove_role_from_instance_profile' if request.method == 'DELETE' else 'add_role_to_instance_profile'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['DELETE'])
def iam_instance_profile_detail(request, instance_profile_name: str):
    try:
        return JsonResponse(delete_instance_profile(instance_profile_name))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='delete_instance_profile')


@require_http_methods(['POST', 'PUT', 'DELETE'])
def iam_user_login_profile(request, user_name: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_login_profile(user_name))
        body = parse_json_body(request)
        return JsonResponse(save_login_profile(user_name, body.get('password', ''), reset_required=body.get('password_reset_required') is True, update=request.method == 'PUT'))
    except Exception as exc:
        operation = {'POST': 'create_login_profile', 'PUT': 'update_login_profile', 'DELETE': 'delete_login_profile'}[request.method]
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['GET'])
def iam_access_key_last_used(request, access_key_id: str):
    try:
        return JsonResponse(get_access_key_last_used(access_key_id))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='get_access_key_last_used')


@require_http_methods(['POST'])
def iam_policy_simulation(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(simulate_principal_policy(
            body.get('principal_arn', ''),
            body.get('action_names') or body.get('actions') or '',
            body.get('resource_arns') or body.get('resources') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='simulate_principal_policy')


@require_http_methods(['POST'])
def iam_user_access_keys_create(request, user_name: str):
    try:
        return JsonResponse(create_access_key(user_name))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='create_access_key')


@require_http_methods(['PUT', 'DELETE'])
def iam_user_access_key_detail(request, user_name: str, access_key_id: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_access_key(user_name, access_key_id))
        body = parse_json_body(request)
        return JsonResponse(update_access_key(user_name, access_key_id, body.get('status', '')))
    except Exception as exc:
        operation = 'delete_access_key' if request.method == 'DELETE' else 'update_access_key'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['POST'])
def iam_role_assume(request, role_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(assume_role(
            body.get('role_arn', ''),
            body.get('session_name') or f'{role_name}-dashboard',
            duration_seconds=body.get('duration_seconds') or None,
            session_policy=body.get('session_policy') or None,
            session_policy_arns=body.get('session_policy_arns') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='assume_role')


@require_http_methods(['PUT'])
def iam_role_trust_policy(request, role_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_role_trust_policy(role_name, body.get('document')))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='update_role_trust_policy')


@require_http_methods(['POST', 'DELETE'])
def iam_attached_policies(request, principal_type: str, principal_name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(detach_managed_policy(principal_type, principal_name, body.get('policy_arn', '')))
        return JsonResponse(attach_managed_policy(principal_type, principal_name, body.get('policy_arn', '')))
    except Exception as exc:
        operation = 'detach_managed_policy' if request.method == 'DELETE' else 'attach_managed_policy'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['PUT', 'DELETE'])
def iam_permissions_boundary(request, principal_type: str, principal_name: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_permissions_boundary(principal_type, principal_name))
        body = parse_json_body(request)
        return JsonResponse(put_permissions_boundary(principal_type, principal_name, body.get('policy_arn', '')))
    except Exception as exc:
        operation = 'delete_permissions_boundary' if request.method == 'DELETE' else 'put_permissions_boundary'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['PUT', 'DELETE'])
def iam_inline_policy_detail(request, principal_type: str, principal_name: str, policy_name: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_inline_policy(principal_type, principal_name, policy_name))
        body = parse_json_body(request)
        return JsonResponse(put_inline_policy(principal_type, principal_name, policy_name, body.get('document')))
    except Exception as exc:
        operation = 'delete_inline_policy' if request.method == 'DELETE' else 'put_inline_policy'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['GET'])
def iam_inline_policy_document(request, principal_type: str, principal_name: str, policy_name: str):
    try:
        return JsonResponse(get_inline_policy(principal_type, principal_name, policy_name))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='get_inline_policy')


@require_http_methods(['POST', 'DELETE'])
def iam_managed_policies_create(request):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(delete_managed_policy(body.get('policy_arn', '')))
        return JsonResponse(create_managed_policy(
            body.get('name', ''),
            body.get('document'),
            description=body.get('description') or None,
            path=body.get('path') or None,
        ))
    except Exception as exc:
        operation = 'delete_managed_policy' if request.method == 'DELETE' else 'create_managed_policy'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['POST', 'DELETE'])
def iam_tags(request, resource_type: str, resource_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_tags(resource_type, resource_name, body.get('tags'), remove=body.get('tag_keys') if request.method == 'DELETE' else None))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='untag_resource' if request.method == 'DELETE' else 'tag_resource')


@require_http_methods(['POST'])
def iam_managed_policy_document(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_managed_policy(body.get('policy_arn', ''), version_id=body.get('version_id') or None))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='get_managed_policy')


@require_http_methods(['POST'])
def iam_managed_policy_versions_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_policy_version(
            body.get('policy_arn', ''),
            body.get('document'),
            set_as_default=body.get('set_as_default') is not False,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation='create_policy_version')


@require_http_methods(['PUT', 'DELETE'])
def iam_managed_policy_version_detail(request):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(delete_policy_version(body.get('policy_arn', ''), body.get('version_id', '')))
        return JsonResponse(set_default_policy_version(body.get('policy_arn', ''), body.get('version_id', '')))
    except Exception as exc:
        operation = 'delete_policy_version' if request.method == 'DELETE' else 'set_default_policy_version'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['POST', 'DELETE'])
def iam_group_membership(request, group_name: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(remove_user_from_group(body.get('user_name', ''), group_name))
        return JsonResponse(add_user_to_group(body.get('user_name', ''), group_name))
    except Exception as exc:
        operation = 'remove_user_from_group' if request.method == 'DELETE' else 'add_user_to_group'
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def iam_password_policy(request):
    operation = {'GET': 'get_account_password_policy', 'PUT': 'update_account_password_policy', 'DELETE': 'delete_account_password_policy'}[request.method]
    try:
        if request.method == 'GET':
            return JsonResponse(get_password_policy())
        if request.method == 'DELETE':
            return JsonResponse(delete_password_policy())
        return JsonResponse(update_password_policy(parse_json_body(request)))
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation=operation)


@require_http_methods(['GET', 'POST'])
def iam_credential_report(request):
    operation = 'generate_credential_report' if request.method == 'POST' else 'get_credential_report'
    try:
        return JsonResponse(generate_credential_report() if request.method == 'POST' else get_credential_report())
    except Exception as exc:
        return handle_action_error(exc, service='iam', operation=operation)
