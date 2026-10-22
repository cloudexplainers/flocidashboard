"""HTTP endpoints for the KMS key workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .kms_api import (
    cancel_key_deletion,
    create_alias,
    create_grant,
    create_key,
    decrypt,
    delete_alias,
    encrypt,
    generate_data_key,
    generate_random,
    get_public_key,
    put_key_policy,
    revoke_grant,
    rotate_key_on_demand,
    schedule_key_deletion,
    sign,
    set_key_enabled,
    set_key_rotation,
    tag_key,
    untag_key,
    update_key_description,
    verify,
    generate_mac,
    verify_mac,
)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on', 'enabled'}
    return bool(value)


@require_http_methods(['POST'])
def kms_keys_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_key(
            description=body.get('description') or '',
            key_usage=body.get('key_usage') or 'ENCRYPT_DECRYPT',
            key_spec=body.get('key_spec') or 'SYMMETRIC_DEFAULT',
            override_id=body.get('override_id') or '',
            tags=body.get('tags') or [],
        ))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='create_key')


@require_http_methods(['POST', 'DELETE'])
def kms_aliases(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(create_alias(body.get('alias_name') or '', body.get('target_key_id') or ''))
        return JsonResponse(delete_alias(body.get('alias_name') or ''))
    except Exception as exc:
        operation = 'create_alias' if request.method == 'POST' else 'delete_alias'
        return handle_action_error(exc, service='kms', operation=operation)


@require_http_methods(['POST'])
def kms_encrypt(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(encrypt(body.get('key_id') or '', body.get('plaintext')))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='encrypt')


@require_http_methods(['POST'])
def kms_decrypt(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(decrypt(body.get('ciphertext_blob') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='decrypt')


@require_http_methods(['POST'])
def kms_data_keys(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(generate_data_key(
            body.get('key_id') or '',
            key_spec=body.get('key_spec') or 'AES_256',
            number_of_bytes=body.get('number_of_bytes'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='generate_data_key')


@require_http_methods(['POST'])
def kms_random(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(generate_random(body.get('number_of_bytes', 32)))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='generate_random')


@require_http_methods(['POST'])
def kms_rotation(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(set_key_rotation(body.get('key_id') or '', _truthy(body.get('enabled'))))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='set_key_rotation')


@require_http_methods(['POST'])
def kms_key_state(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(set_key_enabled(body.get('key_id') or '', _truthy(body.get('enabled'))))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='set_key_enabled')


@require_http_methods(['POST'])
def kms_deletion_schedule(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(schedule_key_deletion(
            body.get('key_id') or '',
            pending_window_in_days=body.get('pending_window_in_days') or 30,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='schedule_key_deletion')


@require_http_methods(['POST'])
def kms_deletion_cancel(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(cancel_key_deletion(body.get('key_id') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='cancel_key_deletion')


@require_http_methods(['POST', 'DELETE'])
def kms_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(tag_key(body.get('key_id') or '', body.get('tags') or []))
        return JsonResponse(untag_key(body.get('key_id') or '', body.get('tag_keys') or []))
    except Exception as exc:
        operation = 'tag_resource' if request.method == 'POST' else 'untag_resource'
        return handle_action_error(exc, service='kms', operation=operation)


@require_http_methods(['PATCH'])
def kms_key_metadata(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_key_description(body.get('key_id') or '', body.get('description') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='update_key_description')


@require_http_methods(['PUT'])
def kms_key_policy(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_key_policy(body.get('key_id') or '', body.get('policy')))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='put_key_policy')


@require_http_methods(['POST'])
def kms_rotation_on_demand(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(rotate_key_on_demand(body.get('key_id') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='rotate_key_on_demand')


@require_http_methods(['POST'])
def kms_public_key(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_public_key(body.get('key_id') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='get_public_key')


@require_http_methods(['POST', 'DELETE'])
def kms_grants(request):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(revoke_grant(body.get('key_id') or '', body.get('grant_id') or ''))
        return JsonResponse(create_grant(
            body.get('key_id') or '', body.get('grantee_principal') or '', body.get('operations') or [],
            name=body.get('name') or '', retiring_principal=body.get('retiring_principal') or '',
        ))
    except Exception as exc:
        operation = 'revoke_grant' if request.method == 'DELETE' else 'create_grant'
        return handle_action_error(exc, service='kms', operation=operation)


@require_http_methods(['POST'])
def kms_sign(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(sign(body.get('key_id') or '', body.get('message'), body.get('signing_algorithm') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='sign')


@require_http_methods(['POST'])
def kms_verify(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(verify(body.get('key_id') or '', body.get('message'), body.get('signature') or '', body.get('signing_algorithm') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='verify')


@require_http_methods(['POST'])
def kms_generate_mac(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(generate_mac(body.get('key_id') or '', body.get('message'), body.get('mac_algorithm') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='generate_mac')


@require_http_methods(['POST'])
def kms_verify_mac(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(verify_mac(body.get('key_id') or '', body.get('message'), body.get('mac') or '', body.get('mac_algorithm') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='kms', operation='verify_mac')
