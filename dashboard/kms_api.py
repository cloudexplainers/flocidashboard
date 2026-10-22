"""Interactive KMS helpers for local key workflows."""

from __future__ import annotations

import base64
import json
from typing import Any

from .aws import FlociClientFactory


def _client():
    return FlociClientFactory().client('kms')


def _clean_required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _optional_int(value: Any, label: str, default: int) -> int:
    if value in (None, ''):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a number') from exc
    if number <= 0:
        raise ValueError(f'{label} must be greater than zero')
    return number


def _tags(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    if not isinstance(value, list):
        raise ValueError('Tags must be a list')
    tags = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = _clean_required(item.get('TagKey') or item.get('key'), 'Tag key')
        val = str(item.get('TagValue') if item.get('TagValue') is not None else item.get('value', ''))
        tags.append({'TagKey': key, 'TagValue': val})
    return tags


def _list(value: Any, label: str) -> list[str]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError(f'{label} must be a list')


def _bytes_from_base64(value: Any, label: str) -> bytes:
    text = _clean_required(value, label)
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f'{label} must be base64 encoded') from exc


def _plaintext_bytes(value: Any) -> bytes:
    if value in (None, ''):
        raise ValueError('Plaintext is required')
    if isinstance(value, str):
        return value.encode('utf-8')
    return json.dumps(value).encode('utf-8')


def _encoded_blob(value: Any) -> str:
    raw = bytes(value or b'')
    return base64.b64encode(raw).decode('ascii')


def _json_datetime(value: Any) -> Any:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _decode_plaintext(value: Any) -> dict[str, Any]:
    raw = bytes(value or b'')
    text = raw.decode('utf-8', errors='replace')
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
    return {
        'plaintext': text,
        'plaintext_json': parsed,
        'plaintext_base64': _encoded_blob(raw),
        'size_bytes': len(raw),
    }


def create_key(
    *,
    description: str = '',
    key_usage: str = 'ENCRYPT_DECRYPT',
    key_spec: str = 'SYMMETRIC_DEFAULT',
    override_id: str = '',
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'KeyUsage': (key_usage or 'ENCRYPT_DECRYPT').strip().upper(),
        'KeySpec': (key_spec or 'SYMMETRIC_DEFAULT').strip().upper(),
    }
    if description:
        kwargs['Description'] = description
    clean_tags = _tags(tags)
    if override_id:
        clean_tags.append({'TagKey': 'floci:override-id', 'TagValue': override_id.strip()})
    if clean_tags:
        kwargs['Tags'] = clean_tags

    response = _client().create_key(**kwargs)
    metadata = response.get('KeyMetadata', {})
    return {
        'key_id': metadata.get('KeyId'),
        'key_arn': metadata.get('Arn'),
        'key_state': metadata.get('KeyState'),
        'metadata': metadata,
    }


def create_alias(alias_name: str, target_key_id: str) -> dict[str, Any]:
    alias = _clean_required(alias_name, 'Alias name')
    if not alias.startswith('alias/'):
        alias = f'alias/{alias}'
    target = _clean_required(target_key_id, 'Target key ID')
    _client().create_alias(AliasName=alias, TargetKeyId=target)
    return {'alias_name': alias, 'target_key_id': target}


def delete_alias(alias_name: str) -> dict[str, Any]:
    alias = _clean_required(alias_name, 'Alias name')
    if not alias.startswith('alias/'):
        alias = f'alias/{alias}'
    _client().delete_alias(AliasName=alias)
    return {'alias_name': alias, 'deleted': True}


def encrypt(key_id: str, plaintext: Any) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    response = _client().encrypt(KeyId=clean_key, Plaintext=_plaintext_bytes(plaintext))
    return {
        'key_id': response.get('KeyId') or clean_key,
        'ciphertext_blob': _encoded_blob(response.get('CiphertextBlob')),
        'encryption_algorithm': response.get('EncryptionAlgorithm'),
    }


def decrypt(ciphertext_blob: str) -> dict[str, Any]:
    response = _client().decrypt(CiphertextBlob=_bytes_from_base64(ciphertext_blob, 'Ciphertext blob'))
    return {
        'key_id': response.get('KeyId'),
        'encryption_algorithm': response.get('EncryptionAlgorithm'),
        **_decode_plaintext(response.get('Plaintext')),
    }


def generate_data_key(key_id: str, *, key_spec: str = 'AES_256', number_of_bytes: Any = None) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    kwargs: dict[str, Any] = {'KeyId': clean_key}
    if number_of_bytes not in (None, ''):
        kwargs['NumberOfBytes'] = _optional_int(number_of_bytes, 'Number of bytes', 32)
    else:
        kwargs['KeySpec'] = (key_spec or 'AES_256').strip().upper()
    response = _client().generate_data_key(**kwargs)
    plaintext = bytes(response.get('Plaintext') or b'')
    return {
        'key_id': response.get('KeyId') or clean_key,
        'plaintext_base64': _encoded_blob(plaintext),
        'ciphertext_blob': _encoded_blob(response.get('CiphertextBlob')),
        'size_bytes': len(plaintext),
    }


def generate_random(number_of_bytes: Any = 32) -> dict[str, Any]:
    size = _optional_int(number_of_bytes, 'Number of bytes', 32)
    if size > 1024:
        raise ValueError('Number of bytes must be 1024 or less')
    response = _client().generate_random(NumberOfBytes=size)
    plaintext = bytes(response.get('Plaintext') or b'')
    return {
        'plaintext_base64': _encoded_blob(plaintext),
        'size_bytes': len(plaintext),
    }


def set_key_rotation(key_id: str, enabled: bool) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    client = _client()
    if enabled:
        client.enable_key_rotation(KeyId=clean_key)
    else:
        client.disable_key_rotation(KeyId=clean_key)
    return {'key_id': clean_key, 'rotation_enabled': enabled}


def set_key_enabled(key_id: str, enabled: bool) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    client = _client()
    if enabled:
        client.enable_key(KeyId=clean_key)
    else:
        client.disable_key(KeyId=clean_key)
    return {'key_id': clean_key, 'enabled': enabled, 'key_state': 'Enabled' if enabled else 'Disabled'}


def _pending_window(value: Any) -> int:
    # AWS default and console default: 30 days. AWS rejects values outside 7-30 rather
    # than clamping them, so the console does too.
    days = _optional_int(value, 'Waiting period', 30)
    if not 7 <= days <= 30:
        raise ValueError('Waiting period must be between 7 and 30 days')
    return days


def schedule_key_deletion(key_id: str, pending_window_in_days: Any = 30) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    response = _client().schedule_key_deletion(
        KeyId=clean_key,
        PendingWindowInDays=_pending_window(pending_window_in_days),
    )
    return {
        'key_id': response.get('KeyId') or clean_key,
        'deletion_date': _json_datetime(response.get('DeletionDate')),
        'key_state': response.get('KeyState'),
    }


def cancel_key_deletion(key_id: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    response = _client().cancel_key_deletion(KeyId=clean_key)
    return {
        'key_id': response.get('KeyId') or clean_key,
        'key_state': response.get('KeyState'),
    }


def tag_key(key_id: str, tags: Any) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    clean_tags = _tags(tags)
    if not clean_tags:
        raise ValueError('At least one tag is required')
    _client().tag_resource(KeyId=clean_key, Tags=clean_tags)
    return {'key_id': clean_key, 'tags': clean_tags}


def untag_key(key_id: str, tag_keys: Any) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    if isinstance(tag_keys, str):
        keys = [key.strip() for key in tag_keys.split(',') if key.strip()]
    elif isinstance(tag_keys, list):
        keys = [str(key).strip() for key in tag_keys if str(key).strip()]
    else:
        keys = []
    if not keys:
        raise ValueError('At least one tag key is required')
    _client().untag_resource(KeyId=clean_key, TagKeys=keys)
    return {'key_id': clean_key, 'tag_keys': keys}


def update_key_description(key_id: str, description: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    clean_description = str(description or '')
    _client().update_key_description(KeyId=clean_key, Description=clean_description)
    return {'key_id': clean_key, 'description': clean_description}


def put_key_policy(key_id: str, policy: Any) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    if not isinstance(policy, (dict, list, str)) or policy in ('', None):
        raise ValueError('Policy is required')
    text = policy if isinstance(policy, str) else json.dumps(policy)
    _client().put_key_policy(KeyId=clean_key, PolicyName='default', Policy=text)
    return {'key_id': clean_key, 'policy': policy}


def rotate_key_on_demand(key_id: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    response = _client().rotate_key_on_demand(KeyId=clean_key)
    return {'key_id': response.get('KeyId') or clean_key, 'response': response}


def get_public_key(key_id: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    response = _client().get_public_key(KeyId=clean_key)
    return {
        'key_id': response.get('KeyId') or clean_key,
        'public_key_base64': _encoded_blob(response.get('PublicKey')),
        'key_spec': response.get('KeySpec') or response.get('CustomerMasterKeySpec'),
        'key_usage': response.get('KeyUsage'),
        'encryption_algorithms': response.get('EncryptionAlgorithms', []),
        'signing_algorithms': response.get('SigningAlgorithms', []),
    }


def create_grant(key_id: str, grantee_principal: str, operations: Any, *, name: str = '', retiring_principal: str = '') -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    clean_operations = _list(operations, 'Operations')
    if not clean_operations:
        raise ValueError('At least one grant operation is required')
    kwargs: dict[str, Any] = {'KeyId': clean_key, 'GranteePrincipal': _clean_required(grantee_principal, 'Grantee principal'), 'Operations': clean_operations}
    if name:
        kwargs['Name'] = str(name).strip()
    if retiring_principal:
        kwargs['RetiringPrincipal'] = str(retiring_principal).strip()
    response = _client().create_grant(**kwargs)
    return {'key_id': clean_key, 'grant_id': response.get('GrantId'), 'grant_token': response.get('GrantToken')}


def revoke_grant(key_id: str, grant_id: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    clean_grant = _clean_required(grant_id, 'Grant ID')
    _client().revoke_grant(KeyId=clean_key, GrantId=clean_grant)
    return {'key_id': clean_key, 'grant_id': clean_grant, 'revoked': True}


def sign(key_id: str, message: Any, signing_algorithm: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    algorithm = _clean_required(signing_algorithm, 'Signing algorithm')
    response = _client().sign(KeyId=clean_key, Message=_plaintext_bytes(message), MessageType='RAW', SigningAlgorithm=algorithm)
    return {'key_id': response.get('KeyId') or clean_key, 'signature': _encoded_blob(response.get('Signature')), 'signing_algorithm': response.get('SigningAlgorithm') or algorithm}


def verify(key_id: str, message: Any, signature: str, signing_algorithm: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    algorithm = _clean_required(signing_algorithm, 'Signing algorithm')
    response = _client().verify(KeyId=clean_key, Message=_plaintext_bytes(message), MessageType='RAW', Signature=_bytes_from_base64(signature, 'Signature'), SigningAlgorithm=algorithm)
    return {'key_id': response.get('KeyId') or clean_key, 'signature_valid': bool(response.get('SignatureValid')), 'signing_algorithm': response.get('SigningAlgorithm') or algorithm}


def generate_mac(key_id: str, message: Any, mac_algorithm: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    algorithm = _clean_required(mac_algorithm, 'MAC algorithm')
    response = _client().generate_mac(KeyId=clean_key, Message=_plaintext_bytes(message), MacAlgorithm=algorithm)
    return {'key_id': response.get('KeyId') or clean_key, 'mac': _encoded_blob(response.get('Mac')), 'mac_algorithm': response.get('MacAlgorithm') or algorithm}


def verify_mac(key_id: str, message: Any, mac: str, mac_algorithm: str) -> dict[str, Any]:
    clean_key = _clean_required(key_id, 'Key ID')
    algorithm = _clean_required(mac_algorithm, 'MAC algorithm')
    response = _client().verify_mac(KeyId=clean_key, Message=_plaintext_bytes(message), Mac=_bytes_from_base64(mac, 'MAC'), MacAlgorithm=algorithm)
    return {'key_id': response.get('KeyId') or clean_key, 'mac_valid': bool(response.get('MacValid')), 'mac_algorithm': response.get('MacAlgorithm') or algorithm}
