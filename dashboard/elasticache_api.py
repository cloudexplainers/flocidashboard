"""Interactive ElastiCache helpers for local Redis/Valkey workflows."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from .aws import FlociClientFactory, _clean_response


def _factory() -> FlociClientFactory:
    return FlociClientFactory()


def _client():
    return _factory().client('elasticache')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _int_value(value: Any, label: str, default: int | None = None) -> int | None:
    if value in (None, ''):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a number') from exc
    if number < 0:
        raise ValueError(f'{label} must be zero or greater')
    return number


def _list(value: Any, label: str = 'Value') -> list[Any]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return value
    raise ValueError(f'{label} must be a list')


def _dict(value: Any, label: str) -> dict[str, Any]:
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    return value


def _tags(value: Any) -> list[dict[str, str]]:
    if value in (None, '', [], {}):
        return []
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Tags must be an object or list')

    tags = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = _required(item.get('Key') or item.get('key'), 'Tag key')
        tag_value = item.get('Value')
        if tag_value is None:
            tag_value = item.get('value')
        tags.append({'Key': key, 'Value': '' if tag_value is None else str(tag_value)})
    return tags


def _auth_mode(value: Any, auth_type: str = '') -> dict[str, Any]:
    mode = _dict(value, 'Authentication mode')
    if mode:
        return mode
    clean_type = str(auth_type or '').strip().lower()
    if clean_type == 'no-password':
        clean_type = 'no-password-required'  # The AWS enum value.
    if clean_type in {'iam', 'password', 'no-password-required'}:
        return {'Type': clean_type}
    return {}


def create_replication_group(
    *,
    replication_group_id: str,
    description: str,
    engine: str = 'redis',
    cache_node_type: str = '',
    num_cache_clusters: Any = 1,
    port: Any = None,
    user_group_ids: Any = None,
    transit_encryption_enabled: Any = False,
    at_rest_encryption_enabled: Any = False,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'ReplicationGroupId': _required(replication_group_id, 'Replication group ID'),
        'ReplicationGroupDescription': _required(description, 'Description'),
    }
    if engine:
        kwargs['Engine'] = str(engine).strip()
    if cache_node_type:
        kwargs['CacheNodeType'] = cache_node_type
    count = _int_value(num_cache_clusters, 'Cache cluster count', 1)
    if count:
        kwargs['NumCacheClusters'] = count
    clean_port = _int_value(port, 'Port')
    if clean_port:
        kwargs['Port'] = clean_port
    groups = _list(user_group_ids, 'User group IDs')
    if groups:
        kwargs['UserGroupIds'] = groups
    if _bool(transit_encryption_enabled):
        kwargs['TransitEncryptionEnabled'] = True
    if _bool(at_rest_encryption_enabled):
        kwargs['AtRestEncryptionEnabled'] = True
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['Tags'] = clean_tags

    response = _client().create_replication_group(**kwargs)
    group = response.get('ReplicationGroup', {})
    return {
        'replication_group_id': group.get('ReplicationGroupId') or kwargs['ReplicationGroupId'],
        'status': group.get('Status'),
        'node_groups': group.get('NodeGroups'),
        'response': _clean_response(response),
    }


def delete_replication_group(
    replication_group_id: str,
    *,
    retain_primary_cluster: Any = False,
    final_snapshot_identifier: str = '',
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'ReplicationGroupId': _required(replication_group_id, 'Replication group ID'),
        'RetainPrimaryCluster': _bool(retain_primary_cluster),
    }
    if final_snapshot_identifier:
        kwargs['FinalSnapshotIdentifier'] = final_snapshot_identifier
    response = _client().delete_replication_group(**kwargs)
    group = response.get('ReplicationGroup', {})
    return {
        'replication_group_id': group.get('ReplicationGroupId') or kwargs['ReplicationGroupId'],
        'status': group.get('Status'),
        'response': _clean_response(response),
    }


def create_user(
    *,
    user_id: str,
    user_name: str,
    engine: str = 'redis',
    access_string: str = 'on ~* +@all',
    auth_type: str = 'iam',
    authentication_mode: Any = None,
    passwords: Any = None,
    no_password_required: Any = False,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'UserId': _required(user_id, 'User ID'),
        'UserName': _required(user_name, 'User name'),
        'Engine': engine or 'redis',
        'AccessString': _required(access_string, 'Access string'),
    }
    mode = _auth_mode(authentication_mode, auth_type)
    clean_passwords = _list(passwords, 'Passwords')
    if mode:
        kwargs['AuthenticationMode'] = mode
    elif clean_passwords:
        kwargs['Passwords'] = clean_passwords
    else:
        kwargs['NoPasswordRequired'] = _bool(no_password_required)
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['Tags'] = clean_tags

    response = _client().create_user(**kwargs)
    user = response.get('User', {})
    return {
        'user_id': user.get('UserId') or kwargs['UserId'],
        'user_name': user.get('UserName') or kwargs['UserName'],
        'status': user.get('Status'),
        'response': _clean_response(response),
    }


def modify_user(
    *,
    user_id: str,
    access_string: str = '',
    append_access_string: str = '',
    auth_type: str = '',
    authentication_mode: Any = None,
    passwords: Any = None,
    no_password_required: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {'UserId': _required(user_id, 'User ID')}
    if access_string:
        kwargs['AccessString'] = access_string
    if append_access_string:
        kwargs['AppendAccessString'] = append_access_string
    mode = _auth_mode(authentication_mode, auth_type)
    if mode:
        kwargs['AuthenticationMode'] = mode
    clean_passwords = _list(passwords, 'Passwords')
    if clean_passwords:
        kwargs['Passwords'] = clean_passwords
    if no_password_required is not None:
        kwargs['NoPasswordRequired'] = _bool(no_password_required)
    if len(kwargs) == 1:
        raise ValueError('At least one user change is required')

    response = _client().modify_user(**kwargs)
    user = response.get('User', {})
    return {
        'user_id': user.get('UserId') or kwargs['UserId'],
        'status': user.get('Status'),
        'response': _clean_response(response),
    }


def delete_user(user_id: str) -> dict[str, Any]:
    clean_id = _required(user_id, 'User ID')
    response = _client().delete_user(UserId=clean_id)
    user = response.get('User', {})
    return {
        'user_id': user.get('UserId') or clean_id,
        'status': user.get('Status'),
        'response': _clean_response(response),
    }


def validate_iam_auth_token(auth_token: str, *, user_id: str = '', user_name: str = '') -> dict[str, Any]:
    factory = _factory()
    params = {
        'Action': 'ValidateIamAuthToken',
        'Version': '2015-02-02',
        'AuthToken': _required(auth_token, 'Auth token'),
    }
    if user_id:
        params['UserId'] = user_id
    if user_name:
        params['UserName'] = user_name

    body = urlencode(params).encode('utf-8')
    request = Request(
        factory.endpoint_url,
        data=body,
        headers={
            'Content-Type': 'application/x-www-form-urlencoded; charset=utf-8',
            'Accept': 'application/xml',
        },
        method='POST',
    )
    with urlopen(request, timeout=5) as response:
        raw = response.read().decode('utf-8')

    parsed: dict[str, Any] = {}
    try:
        root = ET.fromstring(raw)
        parsed = {
            node.tag.rsplit('}', 1)[-1]: node.text
            for node in root.iter()
            if node is not root and node.text and node.text.strip()
        }
    except ET.ParseError:
        parsed = {}

    return {
        'valid': parsed.get('Valid') or parsed.get('IsValid') or parsed.get('Result'),
        'user_id': user_id,
        'user_name': user_name,
        'response': parsed or raw,
    }
