"""Interactive RDS helpers for local database workflows."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('rds')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _positive_int(value: Any, label: str, default: int) -> int:
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
    if value in (None, '', []):
        return []
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Tags must be an object or list')

    tags = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = item.get('Key') or item.get('key')
        if not key:
            raise ValueError('Each tag needs a Key')
        tag_value = item.get('Value')
        if tag_value is None:
            tag_value = item.get('value')
        tags.append({'Key': str(key), 'Value': '' if tag_value is None else str(tag_value)})
    return tags


def create_db_instance(
    identifier: str,
    engine: str,
    username: str,
    password: str,
    *,
    db_instance_class: str = 'db.t3.micro',
    allocated_storage: Any = 20,
    db_name: str = '',
    engine_version: str = '',
    enable_iam_auth: bool = False,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'DBInstanceIdentifier': _required(identifier, 'DB instance identifier'),
        'DBInstanceClass': _required(db_instance_class, 'DB instance class'),
        'Engine': _required(engine, 'Engine'),
        'MasterUsername': _required(username, 'Master username'),
        'MasterUserPassword': _required(password, 'Master user password'),
        'AllocatedStorage': _positive_int(allocated_storage, 'Allocated storage', 20),
    }
    if db_name:
        kwargs['DBName'] = db_name
    if engine_version:
        kwargs['EngineVersion'] = engine_version
    if enable_iam_auth:
        kwargs['EnableIAMDatabaseAuthentication'] = True
    parsed_tags = _tags(tags)
    if parsed_tags:
        kwargs['Tags'] = parsed_tags

    response = _client().create_db_instance(**kwargs)
    instance = response.get('DBInstance', {})
    return {
        'identifier': kwargs['DBInstanceIdentifier'],
        'instance': _clean_response(instance),
        'response': _clean_response(response),
    }


def modify_db_instance(
    identifier: str,
    *,
    db_instance_class: str = '',
    allocated_storage: Any = None,
    master_user_password: str = '',
    apply_immediately: bool = True,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'DBInstanceIdentifier': _required(identifier, 'DB instance identifier'),
        'ApplyImmediately': bool(apply_immediately),
    }
    if db_instance_class:
        kwargs['DBInstanceClass'] = db_instance_class
    if allocated_storage not in (None, ''):
        kwargs['AllocatedStorage'] = _positive_int(allocated_storage, 'Allocated storage', 20)
    if master_user_password:
        kwargs['MasterUserPassword'] = master_user_password
    if len(kwargs) == 2:
        raise ValueError('At least one setting must be changed')

    response = _client().modify_db_instance(**kwargs)
    return {
        'identifier': kwargs['DBInstanceIdentifier'],
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def reboot_db_instance(identifier: str) -> dict[str, Any]:
    clean_identifier = _required(identifier, 'DB instance identifier')
    response = _client().reboot_db_instance(DBInstanceIdentifier=clean_identifier)
    return {
        'identifier': clean_identifier,
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def delete_db_instance(identifier: str, *, skip_final_snapshot: bool = True, final_snapshot_identifier: str = '') -> dict[str, Any]:
    clean_identifier = _required(identifier, 'DB instance identifier')
    kwargs: dict[str, Any] = {
        'DBInstanceIdentifier': clean_identifier,
        'SkipFinalSnapshot': bool(skip_final_snapshot),
    }
    if not kwargs['SkipFinalSnapshot']:
        kwargs['FinalDBSnapshotIdentifier'] = _required(final_snapshot_identifier, 'Final snapshot identifier')
    response = _client().delete_db_instance(**kwargs)
    return {
        'identifier': clean_identifier,
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def create_db_cluster(
    identifier: str,
    engine: str,
    username: str,
    password: str,
    *,
    database_name: str = '',
    engine_version: str = '',
    enable_iam_auth: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'DBClusterIdentifier': _required(identifier, 'DB cluster identifier'),
        'Engine': _required(engine, 'Engine'),
        'MasterUsername': _required(username, 'Master username'),
        'MasterUserPassword': _required(password, 'Master user password'),
    }
    if database_name:
        kwargs['DatabaseName'] = database_name
    if engine_version:
        kwargs['EngineVersion'] = engine_version
    if enable_iam_auth:
        kwargs['EnableIAMDatabaseAuthentication'] = True
    response = _client().create_db_cluster(**kwargs)
    return {
        'identifier': kwargs['DBClusterIdentifier'],
        'cluster': _clean_response(response.get('DBCluster', {})),
        'response': _clean_response(response),
    }


def delete_db_cluster(identifier: str, *, skip_final_snapshot: bool = True, final_snapshot_identifier: str = '') -> dict[str, Any]:
    clean_identifier = _required(identifier, 'DB cluster identifier')
    kwargs: dict[str, Any] = {
        'DBClusterIdentifier': clean_identifier,
        'SkipFinalSnapshot': bool(skip_final_snapshot),
    }
    if not kwargs['SkipFinalSnapshot']:
        kwargs['FinalDBSnapshotIdentifier'] = _required(final_snapshot_identifier, 'Final snapshot identifier')
    response = _client().delete_db_cluster(**kwargs)
    return {
        'identifier': clean_identifier,
        'cluster': _clean_response(response.get('DBCluster', {})),
        'response': _clean_response(response),
    }


def create_db_parameter_group(name: str, family: str, description: str) -> dict[str, Any]:
    response = _client().create_db_parameter_group(
        DBParameterGroupName=_required(name, 'Parameter group name'),
        DBParameterGroupFamily=_required(family, 'Parameter group family'),
        Description=_required(description, 'Description'),
    )
    return {'parameter_group': _clean_response(response.get('DBParameterGroup', {})), 'response': _clean_response(response)}


def delete_db_parameter_group(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Parameter group name')
    response = _client().delete_db_parameter_group(DBParameterGroupName=clean_name)
    return {'name': clean_name, 'response': _clean_response(response)}
