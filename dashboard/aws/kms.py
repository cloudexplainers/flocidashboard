"""KMS inventory and resource-list loaders."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _kms_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def kms_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    kms = factory.client('kms')
    keys = _safe_value(lambda: core._paginate(kms, 'list_keys', 'Keys'), [])
    aliases = _safe_value(lambda: core._paginate(kms, 'list_aliases', 'Aliases'), [])

    aliases_by_key: dict[str, list[dict[str, Any]]] = {}
    for alias in aliases:
        target_key_id = alias.get('TargetKeyId')
        if target_key_id:
            aliases_by_key.setdefault(target_key_id, []).append(_clean_response(alias))

    def key_detail(key: dict[str, Any]) -> dict[str, Any]:
        key_id = key.get('KeyId')
        metadata = _kms_optional(
            lambda: kms.describe_key(KeyId=key_id).get('KeyMetadata', {}),
            {'NotFoundException', 'KMSInvalidStateException'},
        )

        return {
            'name': key_id,
            'key_id': key_id,
            'key_arn': key.get('KeyArn'),
            'metadata': metadata,
            'aliases': _kms_optional(
                lambda: core._paginate(kms, 'list_aliases', 'Aliases', KeyId=key_id),
                {'NotFoundException'},
            ),
            'tags': _kms_optional(
                lambda: core._paginate(kms, 'list_resource_tags', 'Tags', KeyId=key_id),
                {'NotFoundException'},
            ),
            'policy': _kms_optional(
                lambda: json.loads(kms.get_key_policy(KeyId=key_id, PolicyName='default').get('Policy', '{}')),
                {'NotFoundException'},
            ),
            'rotation_enabled': _kms_optional(
                lambda: kms.get_key_rotation_status(KeyId=key_id).get('KeyRotationEnabled'),
                {'NotFoundException', 'UnsupportedOperationException'},
            ),
            'grants': _kms_optional(
                lambda: core._paginate(kms, 'list_grants', 'Grants', KeyId=key_id),
                {'NotFoundException'},
            ),
        }

    if len(keys) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(keys))) as executor:
            detailed_keys = list(executor.map(key_detail, keys))
    else:
        detailed_keys = [key_detail(key) for key in keys]

    return {
        'summary': {
            'keys': len(detailed_keys),
            'aliases': len(aliases),
            'enabled_keys': sum(
                1
                for key in detailed_keys
                if isinstance(key.get('metadata'), dict)
                and key['metadata'].get('KeyState') == 'Enabled'
            ),
            'pending_deletion': sum(
                1
                for key in detailed_keys
                if isinstance(key.get('metadata'), dict)
                and key['metadata'].get('KeyState') == 'PendingDeletion'
            ),
            'rotation_enabled': sum(1 for key in detailed_keys if key.get('rotation_enabled') is True),
            'grants': sum(len(key.get('grants') or []) for key in detailed_keys if isinstance(key.get('grants'), list)),
        },
        'keys': detailed_keys,
        'aliases': _clean_response(aliases),
        'supported': [
            'CreateKey',
            'DescribeKey',
            'ListKeys',
            'Encrypt',
            'Decrypt',
            'ReEncrypt',
            'GenerateDataKey',
            'GenerateDataKeyWithoutPlaintext',
            'GenerateRandom',
            'Sign',
            'Verify',
            'GenerateMac',
            'VerifyMac',
            'CreateAlias',
            'DeleteAlias',
            'ListAliases',
            'ScheduleKeyDeletion',
            'CancelKeyDeletion',
            'TagResource',
            'UntagResource',
            'ListResourceTags',
            'GetKeyPolicy',
            'PutKeyPolicy',
            'GetKeyRotationStatus',
            'EnableKeyRotation',
            'DisableKeyRotation',
            'RotateKeyOnDemand',
            'CreateGrant',
            'ListGrants',
            'ListRetirableGrants',
            'RevokeGrant',
            'RetireGrant',
        ],
        'notes': [
            'KMS uses JSON 1.1 with X-Amz-Target: TrentService.* against the local Floci endpoint.',
            'CreateKey accepts creation-time tag floci:override-id for deterministic local test key IDs.',
            'Floci strips reserved floci:* tags from stored resource tags and rejects adding them later.',
            'Floci 1.5.18 adds KMS GenerateMac and VerifyMac support.',
            'Floci 1.5.22 adds KMS GenerateRandom support.',
            'Floci 1.5.24 expands key state and key description operations for closer AWS parity.',
            'Floci 1.5.26 adds EnableKey support and improves RSA DIGEST signature wrapping.',
            'Floci 1.5.21 enforces the RotateKeyOnDemand rotation limit.',
            'Grant lifecycle APIs are stored and queryable but are not evaluated during cryptographic operations.',
        ],
    }


def list_kms_keys(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'id': key.get('KeyId'),
            'arn': key.get('KeyArn'),
        }
        for key in factory.client('kms').get_paginator('list_keys').paginate().build_full_result().get('Keys', [])
    ]
