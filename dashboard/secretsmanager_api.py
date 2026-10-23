"""Interactive Secrets Manager helpers for local secret workflows."""

from __future__ import annotations

import base64
import json
from typing import Any

from .aws import FlociClientFactory


def _client():
    return FlociClientFactory().client('secretsmanager')


def _clean_required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _secret_string(value: Any) -> str:
    if value in (None, ''):
        raise ValueError('Secret value is required')
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _decode_secret_value(response: dict[str, Any]) -> dict[str, Any]:
    if 'SecretString' in response:
        text = response.get('SecretString') or ''
        parsed: Any = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        return {
            'type': 'string',
            'value': text,
            'json': parsed,
            'size_bytes': len(text.encode('utf-8')),
        }

    binary = response.get('SecretBinary') or b''
    if isinstance(binary, str):
        raw = base64.b64decode(binary)
    else:
        raw = bytes(binary)
    return {
        'type': 'binary',
        'value': base64.b64encode(raw).decode('ascii'),
        'json': None,
        'size_bytes': len(raw),
    }


def _json_datetime(value: Any) -> Any:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def create_secret(
    name: str,
    value: Any,
    *,
    description: str = '',
    kms_key_id: str = '',
) -> dict[str, Any]:
    secret_name = _clean_required(name, 'Secret name')
    kwargs: dict[str, Any] = {
        'Name': secret_name,
        'SecretString': _secret_string(value),
    }
    if description:
        kwargs['Description'] = description
    if kms_key_id:
        kwargs['KmsKeyId'] = kms_key_id

    response = _client().create_secret(**kwargs)
    return {
        'name': response.get('Name') or secret_name,
        'arn': response.get('ARN'),
        'version_id': response.get('VersionId'),
    }


def put_secret_value(secret_id: str, value: Any) -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    response = _client().put_secret_value(
        SecretId=clean_id,
        SecretString=_secret_string(value),
    )
    return {
        'name': response.get('Name') or clean_id,
        'arn': response.get('ARN'),
        'version_id': response.get('VersionId'),
        'version_stages': response.get('VersionStages'),
    }


def get_secret_value(secret_id: str, *, version_id: str = '', version_stage: str = '') -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    kwargs = {'SecretId': clean_id}
    if version_id:
        kwargs['VersionId'] = version_id
    if version_stage:
        kwargs['VersionStage'] = version_stage
    response = _client().get_secret_value(**kwargs)
    decoded = _decode_secret_value(response)
    return {
        'name': response.get('Name') or clean_id,
        'arn': response.get('ARN'),
        'version_id': response.get('VersionId'),
        'version_stages': response.get('VersionStages'),
        'created_date': _json_datetime(response.get('CreatedDate')),
        **decoded,
    }


def delete_secret(
    secret_id: str,
    *,
    recovery_window_days: Any = 30,
    force_delete_without_recovery: bool = False,
) -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    kwargs: dict[str, Any] = {'SecretId': clean_id}
    if force_delete_without_recovery:
        kwargs['ForceDeleteWithoutRecovery'] = True
    else:
        try:
            window = int(recovery_window_days or 30)
        except (TypeError, ValueError) as exc:
            raise ValueError('Recovery window days must be a number') from exc
        kwargs['RecoveryWindowInDays'] = max(7, min(window, 30))

    response = _client().delete_secret(**kwargs)
    return {
        'name': response.get('Name') or clean_id,
        'arn': response.get('ARN'),
        'deletion_date': _json_datetime(response.get('DeletionDate')),
    }


def update_secret(secret_id: str, *, description: str = '', kms_key_id: str = '') -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    kwargs = {'SecretId': clean_id, 'Description': str(description or '')}
    if kms_key_id:
        kwargs['KmsKeyId'] = str(kms_key_id)
    response = _client().update_secret(**kwargs)
    return {'name': response.get('Name') or clean_id, 'arn': response.get('ARN'), 'version_id': response.get('VersionId')}


def restore_secret(secret_id: str) -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    response = _client().restore_secret(SecretId=clean_id)
    return {'name': response.get('Name') or clean_id, 'arn': response.get('ARN'), 'restored': True}


def rotate_secret(secret_id: str, *, rotation_lambda_arn: str = '', rotation_rules: Any = None, rotate_immediately: bool = True) -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    kwargs: dict[str, Any] = {'SecretId': clean_id, 'RotateImmediately': bool(rotate_immediately)}
    if rotation_lambda_arn:
        kwargs['RotationLambdaARN'] = rotation_lambda_arn
    if rotation_rules:
        if not isinstance(rotation_rules, dict):
            raise ValueError('Rotation rules must be an object')
        kwargs['RotationRules'] = rotation_rules
    response = _client().rotate_secret(**kwargs)
    return {'name': response.get('Name') or clean_id, 'arn': response.get('ARN'), 'version_id': response.get('VersionId')}


def tag_secret(secret_id: str, tags: Any) -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    if not isinstance(tags, list) or not tags:
        raise ValueError('At least one tag is required')
    clean_tags = [{'Key': _clean_required(item.get('Key') or item.get('key'), 'Tag key'), 'Value': str(item.get('Value') if item.get('Value') is not None else item.get('value', ''))} for item in tags if isinstance(item, dict)]
    _client().tag_resource(SecretId=clean_id, Tags=clean_tags)
    return {'name': clean_id, 'tags': clean_tags}


def untag_secret(secret_id: str, tag_keys: Any) -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    keys = [str(item).strip() for item in (tag_keys if isinstance(tag_keys, list) else str(tag_keys or '').split(',')) if str(item).strip()]
    if not keys:
        raise ValueError('At least one tag key is required')
    _client().untag_resource(SecretId=clean_id, TagKeys=keys)
    return {'name': clean_id, 'tag_keys': keys}


def update_version_stage(secret_id: str, version_stage: str, move_to_version_id: str, *, remove_from_version_id: str = '') -> dict[str, Any]:
    clean_id = _clean_required(secret_id, 'Secret ID')
    kwargs = {'SecretId': clean_id, 'VersionStage': _clean_required(version_stage, 'Version stage'), 'MoveToVersionId': _clean_required(move_to_version_id, 'Move-to version ID')}
    if remove_from_version_id:
        kwargs['RemoveFromVersionId'] = remove_from_version_id
    response = _client().update_secret_version_stage(**kwargs)
    return {'name': response.get('Name') or clean_id, 'arn': response.get('ARN'), 'version_stage': version_stage, 'version_id': move_to_version_id}


def get_random_password(options: Any = None) -> dict[str, Any]:
    if options in (None, ''):
        options = {}
    if not isinstance(options, dict):
        raise ValueError('Password options must be an object')

    clean_kwargs: dict[str, Any] = {}
    mapping = {
        'password_length': 'PasswordLength',
        'passwordlength': 'PasswordLength',
        'exclude_characters': 'ExcludeCharacters',
        'excludecharacters': 'ExcludeCharacters',
        'exclude_numbers': 'ExcludeNumbers',
        'excludenumbers': 'ExcludeNumbers',
        'exclude_punctuation': 'ExcludePunctuation',
        'excludepunctuation': 'ExcludePunctuation',
        'exclude_uppercase': 'ExcludeUppercase',
        'excludeuppercase': 'ExcludeUppercase',
        'exclude_lowercase': 'ExcludeLowercase',
        'excludelowercase': 'ExcludeLowercase',
        'include_space': 'IncludeSpace',
        'includespace': 'IncludeSpace',
        'require_each_included_type': 'RequireEachIncludedType',
        'requireeachincludedtype': 'RequireEachIncludedType',
    }
    for k, v in options.items():
        k_norm = k.lower()
        if k_norm in mapping:
            target_key = mapping[k_norm]
            clean_kwargs[target_key] = int(v) if 'Length' in target_key else bool(v)
        else:
            clean_kwargs[k] = v

    response = _client().get_random_password(**clean_kwargs)
    return {'random_password': response.get('RandomPassword')}


def _region_list(regions: Any) -> list[str]:
    import re
    if isinstance(regions, str):
        regions = [regions]
    if not isinstance(regions, list) or not regions:
        raise ValueError('Choose at least one Region')
    cleaned = []
    for region in regions:
        value = str(region or '').strip()
        if not re.fullmatch(r'[a-z]{2}(-[a-z]+)+-\d', value):
            raise ValueError(f'"{value}" is not a Region name such as us-west-2')
        cleaned.append(value)
    return cleaned


def get_replication(secret_id: str) -> dict[str, Any]:
    secret = _clean_required(secret_id, 'Secret')
    detail = _client().describe_secret(SecretId=secret)
    return {
        'secret_id': secret,
        'primary_region': detail.get('PrimaryRegion') or FlociClientFactory().region,
        'replicas': [{
            'region': item.get('Region'),
            'status': item.get('Status'),
            'status_message': item.get('StatusMessage') or '',
            'kms_key_id': item.get('KmsKeyId') or '',
            'last_accessed': item['LastAccessedDate'].isoformat() if hasattr(item.get('LastAccessedDate'), 'isoformat') else item.get('LastAccessedDate'),
        } for item in detail.get('ReplicationStatus', [])],
    }


def replicate_secret(secret_id: str, regions: Any, *, force_overwrite: bool = False) -> dict[str, Any]:
    secret = _clean_required(secret_id, 'Secret')
    targets = _region_list(regions)
    if FlociClientFactory().region in targets:
        raise ValueError('A secret cannot be replicated to its own Region')
    _client().replicate_secret_to_regions(
        SecretId=secret, AddReplicaRegions=[{'Region': region} for region in targets],
        ForceOverwriteReplicaSecret=force_overwrite is True)
    return get_replication(secret)


def remove_replica_regions(secret_id: str, regions: Any) -> dict[str, Any]:
    secret = _clean_required(secret_id, 'Secret')
    _client().remove_regions_from_replication(SecretId=secret, RemoveReplicaRegions=_region_list(regions))
    return get_replication(secret)


def promote_replica(secret_id: str, region: str) -> dict[str, Any]:
    """StopReplicationToReplica, called in the replica's Region: the replica becomes a standalone secret."""
    secret = _clean_required(secret_id, 'Secret')
    target = _region_list([region])[0]
    factory = FlociClientFactory()
    factory.region = target
    # A full ARN names the primary Region; the replica's ARN differs only in the Region field.
    replica_id = secret
    if secret.startswith('arn:'):
        parts = secret.split(':')
        parts[3] = target
        replica_id = ':'.join(parts)
    factory.client('secretsmanager').stop_replication_to_replica(SecretId=replica_id)
    return {'secret_id': secret, 'promoted_region': target}
