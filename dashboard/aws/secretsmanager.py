"""Secrets Manager inventory and resource-list loaders."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code


def _secrets_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def secretsmanager_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    client = factory.client('secretsmanager')
    secrets = core._paginate(client, 'list_secrets', 'SecretList', IncludePlannedDeletion=True)

    def secret_detail(secret: dict[str, Any]) -> dict[str, Any]:
        secret_id = secret.get('ARN') or secret.get('Name')
        description = _secrets_optional(
            lambda: client.describe_secret(SecretId=secret_id),
            {'ResourceNotFoundException'},
        )
        metadata = description if isinstance(description, dict) and not description.get('error') else secret
        versions = _secrets_optional(
            lambda: core._paginate(client, 'list_secret_version_ids', 'Versions', SecretId=secret_id),
            {'ResourceNotFoundException'},
        )
        policy = _secrets_optional(
            lambda: client.get_resource_policy(SecretId=secret_id),
            {'ResourceNotFoundException'},
        )
        return {
            'name': metadata.get('Name'),
            'arn': metadata.get('ARN'),
            'description': metadata.get('Description'),
            'kms_key_id': metadata.get('KmsKeyId'),
            'created': metadata.get('CreatedDate'),
            'last_changed': metadata.get('LastChangedDate'),
            'last_accessed': metadata.get('LastAccessedDate'),
            'last_rotated': metadata.get('LastRotatedDate'),
            'next_rotation': metadata.get('NextRotationDate'),
            'deleted': metadata.get('DeletedDate'),
            'rotation_enabled': metadata.get('RotationEnabled'),
            'rotation_lambda_arn': metadata.get('RotationLambdaARN'),
            'rotation_rules': metadata.get('RotationRules'),
            'version_ids_to_stages': metadata.get('VersionIdsToStages'),
            'tags': metadata.get('Tags', []),
            'versions': versions,
            # ResourcePolicy is absent when the secret has none; a failed read keeps its error.
            'resource_policy': (
                policy if isinstance(policy, dict) and policy.get('error')
                else (policy or {}).get('ResourcePolicy')
            ),
        }

    if len(secrets) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(secrets))) as executor:
            detailed_secrets = core.map_in_context(executor, secret_detail, secrets)
    else:
        detailed_secrets = [secret_detail(secret) for secret in secrets]

    return {
        'summary': {
            'secrets': len(detailed_secrets),
            'scheduled_for_deletion': sum(1 for secret in detailed_secrets if secret.get('deleted')),
            'rotation_enabled': sum(1 for secret in detailed_secrets if secret.get('rotation_enabled')),
            'versions': sum(
                len(secret.get('versions') or [])
                for secret in detailed_secrets
                if isinstance(secret.get('versions'), list)
            ),
        },
        'secrets': detailed_secrets,
        'supported': [
            'CreateSecret',
            'GetSecretValue',
            'PutSecretValue',
            'UpdateSecret',
            'DescribeSecret',
            'ListSecrets',
            'DeleteSecret',
            'RotateSecret',
            'ListSecretVersionIds',
            'RestoreSecret',
            'UpdateSecretVersionStage',
            'GetRandomPassword',
            'TagResource',
            'UntagResource',
            'BatchGetSecretValue',
        ],
        'configuration': {
            'default_recovery_window_days': 30,
            'protocol': 'JSON 1.1, X-Amz-Target: secretsmanager.*',
            'value_display': 'Secret values are retrieved only on explicit request.',
        },
        'notes': [
            'Rotation invokes the rotation Lambda function for real.',
            'Resource policies are stored and returned, but Floci does not evaluate them: a Deny does not block GetSecretValue.',
        ],
    }


def list_secrets(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': secret.get('Name'),
            'arn': secret.get('ARN'),
        }
        for secret in factory.client('secretsmanager').get_paginator('list_secrets').paginate().build_full_result().get('SecretList', [])
    ]
