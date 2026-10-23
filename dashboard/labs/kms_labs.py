"""KMS workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from . import kms_key_management_labs as key_management
from .monolith import (
    _ignore_missing_codes,
    _json_text,
    _kms_client,
)


KMS_LAB_ALIAS_NAME = 'alias/lab-data-key'
KMS_LAB_DESCRIPTION = 'Floci dashboard local workflow lab key'
KMS_LAB_TAGS = [
    {'TagKey': 'Lab', 'TagValue': 'kms-crypto'},
    {'TagKey': 'Owner', 'TagValue': 'floci-dashboard'},
]
KMS_LAB_PLAINTEXT = {
    'application': 'orders',
    'environment': 'local',
    'secret': 'sample-local-token',
}
KMS_LAB_PLAINTEXT_TEXT = json.dumps(KMS_LAB_PLAINTEXT, separators=(',', ':'))
KMS_LAB_KEY_ID_CACHE_KEY = 'floci-lab:kms:crypto:key-id'
KMS_LAB_CIPHERTEXT_CACHE_KEY = 'floci-lab:kms:crypto:ciphertext'
KMS_LAB_DECRYPT_CACHE_KEY = 'floci-lab:kms:crypto:decrypt'
KMS_CRYPTO_LAB = {
    'service': 'kms',
    'key': 'key-alias-encrypt-decrypt',
    'title': 'Protect local app data with KMS',
    'description': 'Create a symmetric KMS key, attach a stable alias, encrypt app configuration, decrypt it, and inspect the key metadata.',
    'steps': [
        {
            'key': 'create-key',
            'title': 'Create a symmetric key',
            'command': 'aws kms create-key --description "Floci dashboard local workflow lab key" --key-usage ENCRYPT_DECRYPT --key-spec SYMMETRIC_DEFAULT --tags file://key-tags.json',
            'explanation': 'Creates a lab-owned symmetric key that can encrypt and decrypt local application data.',
            'artifact_label': 'key-tags.json',
            'artifact': json.dumps(KMS_LAB_TAGS, indent=2),
        },
        {
            'key': 'create-alias',
            'title': 'Create a stable alias',
            'command': f'aws kms create-alias --alias-name {KMS_LAB_ALIAS_NAME} --target-key-id <key-id>',
            'explanation': 'Adds a human-readable alias so application configuration can reference the key without hard-coding the generated key ID.',
        },
        {
            'key': 'describe-key',
            'title': 'Inspect key metadata',
            'command': f'aws kms describe-key --key-id {KMS_LAB_ALIAS_NAME}',
            'explanation': 'Reads the key metadata through the alias and verifies the key is enabled for encrypt/decrypt use.',
        },
        {
            'key': 'encrypt',
            'title': 'Encrypt app configuration',
            'command': f'aws kms encrypt --key-id {KMS_LAB_ALIAS_NAME} --plaintext fileb://app-config.json',
            'explanation': 'Encrypts a small JSON configuration payload with the alias-backed KMS key.',
            'artifact_label': 'app-config.json',
            'artifact': json.dumps(KMS_LAB_PLAINTEXT, indent=2),
        },
        {
            'key': 'decrypt',
            'title': 'Decrypt the ciphertext',
            'command': 'aws kms decrypt --ciphertext-blob fileb://ciphertext.bin',
            'explanation': 'Decrypts the ciphertext produced by the previous step and verifies it matches the original JSON payload.',
        },
    ],
}


def _kms_items(operation: str, result_key: str, **kwargs) -> list[dict[str, Any]]:
    client = _kms_client()
    if hasattr(client, 'get_paginator'):
        try:
            paginator = client.get_paginator(operation)
            return paginator.paginate(**kwargs).build_full_result().get(result_key, [])
        except (ClientError, KeyError, AttributeError, ValueError):
            pass
    return getattr(client, operation)(**kwargs).get(result_key, [])


def _find_kms_lab_key_id() -> str | None:
    cached = cache.get(KMS_LAB_KEY_ID_CACHE_KEY)
    if cached:
        try:
            metadata = _kms_client().describe_key(KeyId=cached).get('KeyMetadata', {})
            if metadata.get('KeyId') and metadata.get('KeyState') != 'PendingDeletion':
                return metadata['KeyId']
            else:
                cache.delete(KMS_LAB_KEY_ID_CACHE_KEY)
        except ClientError:
            cache.delete(KMS_LAB_KEY_ID_CACHE_KEY)

    for alias in _kms_items('list_aliases', 'Aliases'):
        if alias.get('AliasName') == KMS_LAB_ALIAS_NAME and alias.get('TargetKeyId'):
            try:
                metadata = _kms_client().describe_key(KeyId=alias['TargetKeyId']).get('KeyMetadata', {})
                if metadata.get('KeyState') != 'PendingDeletion':
                    cache.set(KMS_LAB_KEY_ID_CACHE_KEY, alias['TargetKeyId'], timeout=86400)
                    return alias['TargetKeyId']
            except ClientError:
                pass

    expected_tags = {(tag['TagKey'], tag['TagValue']) for tag in KMS_LAB_TAGS}
    for key in _kms_items('list_keys', 'Keys'):
        key_id = key.get('KeyId')
        if not key_id:
            continue
        try:
            metadata = _kms_client().describe_key(KeyId=key_id).get('KeyMetadata', {})
            if metadata.get('KeyState') == 'PendingDeletion':
                continue
            tags = _kms_items('list_resource_tags', 'Tags', KeyId=key_id)
        except ClientError:
            continue
        actual_tags = {(tag.get('TagKey'), tag.get('TagValue')) for tag in tags}
        if expected_tags.issubset(actual_tags):
            cache.set(KMS_LAB_KEY_ID_CACHE_KEY, key_id, timeout=86400)
            return key_id
    return None


def _kms_lab_key_id() -> str:
    key_id = _find_kms_lab_key_id()
    if not key_id:
        raise ValueError('KMS lab key has not been created yet.')
    return key_id


def _verify_kms_lab_key() -> dict[str, Any]:
    try:
        key_id = _find_kms_lab_key_id()
        if not key_id:
            return {
                'status': 'failed',
                'message': 'KMS lab key was not found.',
            }
        metadata = _kms_client().describe_key(KeyId=key_id).get('KeyMetadata', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        metadata.get('KeyUsage') == 'ENCRYPT_DECRYPT'
        and metadata.get('KeySpec') == 'SYMMETRIC_DEFAULT'
        and metadata.get('KeyState') in {'Enabled', 'Creating'}
    ):
        return {
            'status': 'passed',
            'message': 'KMS lab key exists and is configured for symmetric encryption.',
            'resource': _clean_response(metadata),
        }
    return {
        'status': 'failed',
        'message': 'KMS lab key does not match the expected symmetric encrypt/decrypt configuration.',
    }


def _verify_kms_lab_alias() -> dict[str, Any]:
    try:
        key_id = _kms_lab_key_id()
        aliases = _kms_items('list_aliases', 'Aliases', KeyId=key_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    for alias in aliases:
        if alias.get('AliasName') == KMS_LAB_ALIAS_NAME and alias.get('TargetKeyId') == key_id:
            return {
                'status': 'passed',
                'message': f'Alias {KMS_LAB_ALIAS_NAME} targets the KMS lab key.',
                'resource': _clean_response(alias),
            }
    return {
        'status': 'failed',
        'message': f'Alias {KMS_LAB_ALIAS_NAME} was not returned for the KMS lab key.',
    }


def _verify_kms_lab_ciphertext() -> dict[str, Any]:
    cached = cache.get(KMS_LAB_CIPHERTEXT_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': 'KMS returned ciphertext for the app configuration payload.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No ciphertext has been recorded for this KMS lab run.',
    }


def _verify_kms_lab_decrypt() -> dict[str, Any]:
    cached = cache.get(KMS_LAB_DECRYPT_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': 'KMS decrypted the ciphertext back to the original app configuration.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful KMS decrypt has been recorded for this lab run.',
    }


def _kms_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'kms',
        'lab': 'key-alias-encrypt-decrypt',
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _bytes_base64(value: Any) -> str:
    return base64.b64encode(bytes(value or b'')).decode('ascii')


def _run_kms_create_key() -> dict[str, Any]:
    command = 'aws kms create-key --description "Floci dashboard local workflow lab key" --key-usage ENCRYPT_DECRYPT --key-spec SYMMETRIC_DEFAULT --tags file://key-tags.json'
    started = time.perf_counter()
    existing_key_id = _find_kms_lab_key_id()
    if existing_key_id:
        response = {'KeyMetadata': _kms_client().describe_key(KeyId=existing_key_id).get('KeyMetadata', {})}
    else:
        response = _kms_client().create_key(
            Description=KMS_LAB_DESCRIPTION,
            KeyUsage='ENCRYPT_DECRYPT',
            KeySpec='SYMMETRIC_DEFAULT',
            Tags=KMS_LAB_TAGS,
        )
    key_id = response.get('KeyMetadata', {}).get('KeyId')
    if key_id:
        cache.set(KMS_LAB_KEY_ID_CACHE_KEY, key_id, timeout=86400)
    return _kms_step_result(
        'create-key',
        command,
        response,
        _verify_kms_lab_key(),
        started,
    )


def _run_kms_create_alias() -> dict[str, Any]:
    command = f'aws kms create-alias --alias-name {KMS_LAB_ALIAS_NAME} --target-key-id <key-id>'
    started = time.perf_counter()
    key_id = _kms_lab_key_id()
    alias_verification = _verify_kms_lab_alias()
    if alias_verification.get('status') == 'passed':
        response = alias_verification.get('resource', {})
    else:
        _kms_client().create_alias(AliasName=KMS_LAB_ALIAS_NAME, TargetKeyId=key_id)
        response = {'AliasName': KMS_LAB_ALIAS_NAME, 'TargetKeyId': key_id}
    return _kms_step_result(
        'create-alias',
        command,
        response,
        _verify_kms_lab_alias(),
        started,
    )


def _run_kms_describe_key() -> dict[str, Any]:
    command = f'aws kms describe-key --key-id {KMS_LAB_ALIAS_NAME}'
    started = time.perf_counter()
    response = _kms_client().describe_key(KeyId=KMS_LAB_ALIAS_NAME)
    return _kms_step_result(
        'describe-key',
        command,
        response,
        _verify_kms_lab_key(),
        started,
    )


def _run_kms_encrypt() -> dict[str, Any]:
    command = f'aws kms encrypt --key-id {KMS_LAB_ALIAS_NAME} --plaintext fileb://app-config.json'
    started = time.perf_counter()
    response = _kms_client().encrypt(
        KeyId=KMS_LAB_ALIAS_NAME,
        Plaintext=KMS_LAB_PLAINTEXT_TEXT.encode('utf-8'),
    )
    normalized = {
        'KeyId': response.get('KeyId'),
        'CiphertextBlob': _bytes_base64(response.get('CiphertextBlob')),
        'EncryptionAlgorithm': response.get('EncryptionAlgorithm'),
    }
    if normalized['CiphertextBlob']:
        cache.set(KMS_LAB_CIPHERTEXT_CACHE_KEY, normalized, timeout=3600)
    return _kms_step_result(
        'encrypt',
        command,
        normalized,
        _verify_kms_lab_ciphertext(),
        started,
    )


def _run_kms_decrypt() -> dict[str, Any]:
    command = 'aws kms decrypt --ciphertext-blob fileb://ciphertext.bin'
    started = time.perf_counter()
    ciphertext = cache.get(KMS_LAB_CIPHERTEXT_CACHE_KEY)
    if not ciphertext or not ciphertext.get('CiphertextBlob'):
        raise ValueError('Run the encrypt step before decrypting the KMS lab ciphertext.')
    response = _kms_client().decrypt(
        CiphertextBlob=base64.b64decode(ciphertext['CiphertextBlob']),
    )
    plaintext = bytes(response.get('Plaintext') or b'').decode('utf-8')
    try:
        plaintext_json = json.loads(plaintext)
    except json.JSONDecodeError:
        plaintext_json = None
    normalized = {
        'KeyId': response.get('KeyId'),
        'EncryptionAlgorithm': response.get('EncryptionAlgorithm'),
        'Plaintext': plaintext,
        'PlaintextJson': plaintext_json,
    }
    if plaintext_json == KMS_LAB_PLAINTEXT:
        cache.set(KMS_LAB_DECRYPT_CACHE_KEY, normalized, timeout=3600)
    return _kms_step_result(
        'decrypt',
        command,
        normalized,
        _verify_kms_lab_decrypt(),
        started,
    )


def _reset_kms_crypto() -> dict[str, Any]:
    command = (
        f'aws kms delete-alias --alias-name {KMS_LAB_ALIAS_NAME}\n'
        'aws kms schedule-key-deletion --key-id <key-id> --pending-window-in-days 7'
    )
    started = time.perf_counter()
    key_id = _find_kms_lab_key_id()
    deleted_alias = False
    scheduled_deletion = False
    kms = _kms_client()
    if key_id:
        deleted_alias = _ignore_missing_codes(
            lambda: kms.delete_alias(AliasName=KMS_LAB_ALIAS_NAME),
            {'NotFoundException'},
        )
        scheduled_deletion = _ignore_missing_codes(
            lambda: kms.schedule_key_deletion(
                KeyId=key_id,
                PendingWindowInDays=7,
            ),
            {'NotFoundException', 'KMSInvalidStateException'},
        )
    cache.delete_many([
        KMS_LAB_KEY_ID_CACHE_KEY,
        KMS_LAB_CIPHERTEXT_CACHE_KEY,
        KMS_LAB_DECRYPT_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_alias': deleted_alias,
        'scheduled_deletion': scheduled_deletion,
        'key_id': key_id,
    }

    return {
        'service': 'kms',
        'lab': 'key-alias-encrypt-decrypt',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The KMS alias was removed, the lab key was scheduled for deletion when present, and recorded ciphertext state was cleared.',
        },
    }


LABS = [KMS_CRYPTO_LAB, key_management.LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'kms' and lab_key == 'key-management':
        from .kms_key_management_labs import run_step as key_management_run_step
        return key_management_run_step(step_key)

    if service_key == 'kms' and lab_key == 'key-alias-encrypt-decrypt':
        runners = {
            'create-key': _run_kms_create_key,
            'create-alias': _run_kms_create_alias,
            'describe-key': _run_kms_describe_key,
            'encrypt': _run_kms_encrypt,
            'decrypt': _run_kms_decrypt,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'kms' and lab_key == 'key-management':
        from .kms_key_management_labs import status as key_management_status
        return key_management_status()

    if service_key == 'kms' and lab_key == 'key-alias-encrypt-decrypt':
        key_verification = _verify_kms_lab_key()
        alias_verification = _verify_kms_lab_alias()
        ciphertext_verification = _verify_kms_lab_ciphertext()
        decrypt_verification = _verify_kms_lab_decrypt()
        key_verified = key_verification.get('status') == 'passed'
        alias_verified = alias_verification.get('status') == 'passed'
        ciphertext_verified = ciphertext_verification.get('status') == 'passed'
        decrypt_verified = decrypt_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': key_verified and alias_verified and ciphertext_verified and decrypt_verified,
            'steps': {
                'create-key': {'verified': key_verified, 'verification': key_verification if key_verified else None},
                'create-alias': {'verified': alias_verified, 'verification': alias_verification if alias_verified else None},
                'describe-key': {'verified': key_verified and alias_verified, 'verification': key_verification if key_verified and alias_verified else None},
                'encrypt': {'verified': ciphertext_verified, 'verification': ciphertext_verification if ciphertext_verified else None},
                'decrypt': {'verified': decrypt_verified, 'verification': decrypt_verification if decrypt_verified else None},
            },
        }

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'kms' and lab_key == 'key-management':
        from .kms_key_management_labs import reset as key_management_reset
        return key_management_reset()

    if service_key == 'kms' and lab_key == 'key-alias-encrypt-decrypt':
        return _reset_kms_crypto()

    raise ValueError('Lab reset not found')
