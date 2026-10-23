from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .actions import parse_json_body
from .aws import FlociClientFactory, RUNTIME_IDENTITY_SESSION_KEY
from .iam_api import BASELINE_IDENTITY_POLICY_DOCUMENT, BASELINE_IDENTITY_POLICY_NAME, validate_name


def _json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _mask(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return '****'
    return f'{value[:4]}...{value[-4:]}'


def _session_identity_payload(identity: dict[str, Any] | None) -> dict[str, Any] | None:
    if not identity:
        return None
    return {
        'type': identity.get('type'),
        'label': identity.get('label'),
        'access_key_id': _mask(identity.get('access_key_id')),
        'has_session_token': bool(identity.get('session_token')),
        'expires_at': identity.get('expires_at'),
    }


def _store_identity(request, identity: dict[str, Any]) -> None:
    request.session[RUNTIME_IDENTITY_SESSION_KEY] = {
        key: _json_value(value)
        for key, value in identity.items()
        if value is not None and value != ''
    }
    request.session.modified = True


def _identity_payload(request, *, changed: bool = False) -> dict[str, Any]:
    # Read the updated session explicitly: request middleware may still hold the
    # previous identity while a switch/clear response is being constructed.
    factory = FlociClientFactory(identity=request.session.get(RUNTIME_IDENTITY_SESSION_KEY), use_runtime_identity=False)
    payload = {
        'changed': changed,
        'endpoint_url': factory.endpoint_url,
        'region': factory.region,
        'session_identity': _session_identity_payload(request.session.get(RUNTIME_IDENTITY_SESSION_KEY)),
        **factory.credential_context(),
    }
    try:
        payload['caller_identity'] = factory.identity()
        payload['identity_resolved'] = True
    except (BotoCoreError, ClientError, ValueError) as exc:
        payload['identity_resolved'] = False
        payload['identity_error'] = str(exc)
        hint = factory.local_identity_hint()
        if hint:
            payload['caller_identity'] = hint
    return payload


def _admin_factory() -> FlociClientFactory:
    return FlociClientFactory(use_runtime_identity=False)


def _create_session_access_key(iam, user_name: str, replace_access_key_id: str | None = None) -> dict[str, Any]:
    if replace_access_key_id:
        iam.delete_access_key(UserName=user_name, AccessKeyId=replace_access_key_id)
    return iam.create_access_key(UserName=user_name).get('AccessKey', {})


def _rotate_user_access_key(iam, user_name: str) -> dict[str, Any]:
    try:
        keys = iam.list_access_keys(UserName=user_name).get('AccessKeyMetadata', [])
    except ClientError:
        keys = []
    for key in keys:
        key_id = key.get('AccessKeyId')
        if key_id:
            iam.delete_access_key(UserName=user_name, AccessKeyId=key_id)
    return iam.create_access_key(UserName=user_name).get('AccessKey', {})


@require_GET
def identity_detail(request):
    return JsonResponse(_identity_payload(request))


@require_POST
def identity_clear(request):
    request.session.pop(RUNTIME_IDENTITY_SESSION_KEY, None)
    request.session.modified = True
    return JsonResponse(_identity_payload(request, changed=True))


@require_POST
def identity_use_admin(request):
    request.session.pop(RUNTIME_IDENTITY_SESSION_KEY, None)
    request.session.modified = True
    return JsonResponse(_identity_payload(request, changed=True))


@require_POST
def identity_use_user(request):
    try:
        body = parse_json_body(request)
        user_name = validate_name(body.get('user_name') or '', 'User name')
        replace_access_key_id = (body.get('replace_access_key_id') or '').strip() or None
        rotate_all = body.get('rotate_access_keys') is True
        factory = _admin_factory()
        iam = factory.client('iam')
        iam.put_user_policy(
            UserName=user_name,
            PolicyName=BASELINE_IDENTITY_POLICY_NAME,
            PolicyDocument=json.dumps(BASELINE_IDENTITY_POLICY_DOCUMENT),
        )
        key = _rotate_user_access_key(iam, user_name) if rotate_all else _create_session_access_key(
            iam,
            user_name,
            replace_access_key_id=replace_access_key_id,
        )
        access_key_id = key.get('AccessKeyId')
        secret_access_key = key.get('SecretAccessKey')
        if not access_key_id or not secret_access_key:
            raise ValueError(f'Could not create an access key for {user_name}.')
        _store_identity(request, {
            'type': 'user',
            'label': user_name,
            'access_key_id': access_key_id,
            'secret_access_key': secret_access_key,
        })
        return JsonResponse(_identity_payload(request, changed=True))
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@require_POST
def identity_assume_role(request):
    try:
        body = parse_json_body(request)
        session_name = validate_name(body.get('session_name') or 'floci-session', 'Session name')
        if not re.fullmatch(r'[\w+=,.@-]{2,64}', session_name, flags=re.ASCII):
            raise ValueError('Session name must use 2–64 letters, numbers, or +=,.@- characters.')
        role_arn = (body.get('role_arn') or '').strip()
        if role_arn:
            match = re.fullmatch(r'arn:([a-z0-9-]+):iam::(\d{12}):role/(.+)', role_arn)
            if not match or not re.fullmatch(r'[\w+=,.@-]{1,64}', match[3].split('/')[-1], flags=re.ASCII):
                raise ValueError('Enter a valid IAM role ARN, including its account ID and role path.')
            role_name = match[3].split('/')[-1]
        else:
            role_name = validate_name(body.get('role_name') or '', 'Role name')
            # Default to the caller's own account rather than assuming Floci's default one.
            account_id = (body.get('account_id') or '').strip() or (
                FlociClientFactory(identity=request.session.get(RUNTIME_IDENTITY_SESSION_KEY)).client('sts').get_caller_identity().get('Account') or ''
            )
            if not re.fullmatch(r'\d{12}', account_id):
                raise ValueError('Account ID must contain 12 digits.')
            role_arn = f'arn:aws:iam::{account_id}:role/{role_name}'
        sts = FlociClientFactory(identity=request.session.get(RUNTIME_IDENTITY_SESSION_KEY)).client('sts')
        response = sts.assume_role(RoleArn=role_arn, RoleSessionName=session_name)
        credentials = response.get('Credentials', {})
        access_key_id = credentials.get('AccessKeyId')
        secret_access_key = credentials.get('SecretAccessKey')
        session_token = credentials.get('SessionToken')
        if not access_key_id or not secret_access_key or not session_token:
            raise ValueError(f'Could not assume role {role_name}.')
        _store_identity(request, {
            'type': 'assumed_role',
            'label': f'{role_name}/{session_name}',
            'access_key_id': access_key_id,
            'secret_access_key': secret_access_key,
            'session_token': session_token,
            'expires_at': _json_value(credentials.get('Expiration')),
        })
        payload = _identity_payload(request, changed=True)
        payload['assumed_role_user'] = response.get('AssumedRoleUser')
        return JsonResponse(payload)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@require_GET
def identity_options(request):
    """List minimal switch targets using the caller's current permissions."""
    from .aws import _paginate
    payload = {'users': [], 'roles': [], 'errors': {}}
    try:
        iam = FlociClientFactory(identity=request.session.get(RUNTIME_IDENTITY_SESSION_KEY)).client('iam')
        for collection, operation, result_key, name_key in (
            ('users', 'list_users', 'Users', 'UserName'),
            ('roles', 'list_roles', 'Roles', 'RoleName'),
        ):
            try:
                payload[collection] = [
                    {'name': item.get(name_key), 'arn': item.get('Arn'), 'path': item.get('Path')}
                    for item in _paginate(iam, operation, result_key)
                ]
            except (BotoCoreError, ClientError, ValueError) as exc:
                payload['errors'][collection] = str(exc)
        return JsonResponse(payload)
    except (BotoCoreError, ClientError, ValueError) as exc:
        return JsonResponse({'error': str(exc)}, status=400)
