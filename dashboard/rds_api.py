"""Interactive RDS helpers for local database workflows."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .aws import FlociClientFactory, _clean_response


def _client(*, long_running: bool = False):
    factory = FlociClientFactory()
    if long_running:
        # These calls can wait for Floci to start or stop a local database container.
        # Do not retry a mutation that may already have succeeded after a read timeout.
        factory.config = factory.config.merge(Config(read_timeout=120, retries={'max_attempts': 0}))
    return factory.client('rds')


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

    response = _client(long_running=True).create_db_instance(**kwargs)
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
    response = _client(long_running=True).reboot_db_instance(DBInstanceIdentifier=clean_identifier)
    return {
        'identifier': clean_identifier,
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def _final_snapshot_status(operation: str, result_key: str, **kwargs) -> dict[str, Any]:
    """Check that the requested final snapshot exists.

    AWS starts the snapshot as part of the delete. Floci 2.3 accepts the request but
    creates no snapshot, so the console reports what actually happened.
    """
    identifier = next(iter(kwargs.values()))
    try:
        snapshots = getattr(_client(), operation)(**kwargs).get(result_key, [])
    except ClientError as exc:
        code = exc.response.get('Error', {}).get('Code') or ''
        if code not in {'DBSnapshotNotFound', 'DBSnapshotNotFoundFault', 'DBClusterSnapshotNotFoundFault'}:
            # The check failed; don't report that the snapshot is missing.
            return {'identifier': identifier, 'found': None, 'status': None, 'error': exc.response.get('Error', {}).get('Message') or str(exc)}
        snapshots = []
    except BotoCoreError as exc:
        return {'identifier': identifier, 'found': None, 'status': None, 'error': str(exc)}
    snapshots = [item for item in snapshots if isinstance(item, dict)] if isinstance(snapshots, list) else []
    status = snapshots[0].get('Status') if snapshots else None
    return {'identifier': identifier, 'found': bool(snapshots), 'status': status if isinstance(status, str) else None}


def delete_db_instance(identifier: str, *, skip_final_snapshot: bool = True, final_snapshot_identifier: str = '') -> dict[str, Any]:
    clean_identifier = _required(identifier, 'DB instance identifier')
    kwargs: dict[str, Any] = {
        'DBInstanceIdentifier': clean_identifier,
        'SkipFinalSnapshot': bool(skip_final_snapshot),
    }
    if not kwargs['SkipFinalSnapshot']:
        kwargs['FinalDBSnapshotIdentifier'] = _required(final_snapshot_identifier, 'Final snapshot identifier')
    response = _client(long_running=True).delete_db_instance(**kwargs)
    result = {
        'identifier': clean_identifier,
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }
    if not kwargs['SkipFinalSnapshot']:
        result['final_snapshot'] = _final_snapshot_status(
            'describe_db_snapshots', 'DBSnapshots', DBSnapshotIdentifier=kwargs['FinalDBSnapshotIdentifier'],
        )
    return result


def create_db_snapshot(instance_identifier: str, snapshot_identifier: str, *, tags: Any = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'DBInstanceIdentifier': _required(instance_identifier, 'DB instance identifier'),
        'DBSnapshotIdentifier': _required(snapshot_identifier, 'Snapshot identifier'),
    }
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['Tags'] = clean_tags
    # Floci dumps the database inside its container before answering.
    response = _client(long_running=True).create_db_snapshot(**kwargs)
    return {
        'identifier': kwargs['DBSnapshotIdentifier'],
        'snapshot': _clean_response(response.get('DBSnapshot', {})),
        'response': _clean_response(response),
    }


def delete_db_snapshot(snapshot_identifier: str) -> dict[str, Any]:
    clean_identifier = _required(snapshot_identifier, 'Snapshot identifier')
    response = _client().delete_db_snapshot(DBSnapshotIdentifier=clean_identifier)
    return {
        'identifier': clean_identifier,
        'snapshot': _clean_response(response.get('DBSnapshot', {})),
        'response': _clean_response(response),
    }


def restore_db_instance_from_snapshot(
    snapshot_identifier: str,
    instance_identifier: str,
    *,
    db_instance_class: str = '',
    publicly_accessible: bool | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'DBSnapshotIdentifier': _required(snapshot_identifier, 'Snapshot identifier'),
        'DBInstanceIdentifier': _required(instance_identifier, 'New DB instance identifier'),
    }
    if (db_instance_class or '').strip():
        kwargs['DBInstanceClass'] = db_instance_class.strip()
    if publicly_accessible is not None:
        kwargs['PubliclyAccessible'] = bool(publicly_accessible)
    # Restoring starts a new database container and loads the snapshot into it.
    response = _client(long_running=True).restore_db_instance_from_db_snapshot(**kwargs)
    return {
        'identifier': kwargs['DBInstanceIdentifier'],
        'snapshot_identifier': kwargs['DBSnapshotIdentifier'],
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def _restore_time(value: Any):
    """Parse an ISO 8601 restore time; a time without a zone is taken as UTC."""
    text = str(value).strip()
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError('Restore time must be an ISO 8601 date and time, for example 2026-10-23T10:30:00Z') from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def restore_db_instance_to_point_in_time(
    source_identifier: str,
    target_identifier: str,
    *,
    restore_time: Any = '',
    use_latest_restorable_time: bool = False,
    db_instance_class: str = '',
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'SourceDBInstanceIdentifier': _required(source_identifier, 'Source DB instance identifier'),
        'TargetDBInstanceIdentifier': _required(target_identifier, 'New DB instance identifier'),
    }
    has_time = restore_time not in (None, '')
    if has_time == bool(use_latest_restorable_time):
        raise ValueError('Choose either the latest restorable time or a custom restore time')
    if has_time:
        kwargs['RestoreTime'] = _restore_time(restore_time)
    else:
        kwargs['UseLatestRestorableTime'] = True
    if (db_instance_class or '').strip():
        kwargs['DBInstanceClass'] = db_instance_class.strip()
    # Floci starts a new database container and copies the source into it before answering.
    response = _client(long_running=True).restore_db_instance_to_point_in_time(**kwargs)
    return {
        'identifier': kwargs['TargetDBInstanceIdentifier'],
        'source_identifier': kwargs['SourceDBInstanceIdentifier'],
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def create_db_instance_read_replica(
    source_identifier: str,
    replica_identifier: str,
    *,
    db_instance_class: str = '',
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'SourceDBInstanceIdentifier': _required(source_identifier, 'Source DB instance identifier'),
        'DBInstanceIdentifier': _required(replica_identifier, 'Read replica identifier'),
    }
    if (db_instance_class or '').strip():
        kwargs['DBInstanceClass'] = db_instance_class.strip()
    response = _client(long_running=True).create_db_instance_read_replica(**kwargs)
    return {
        'identifier': kwargs['DBInstanceIdentifier'],
        'source_identifier': kwargs['SourceDBInstanceIdentifier'],
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def promote_read_replica(identifier: str, *, backup_retention_period: Any = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {'DBInstanceIdentifier': _required(identifier, 'DB instance identifier')}
    if backup_retention_period not in (None, ''):
        try:
            retention = int(backup_retention_period)
        except (TypeError, ValueError) as exc:
            raise ValueError('Backup retention period must be a number of days') from exc
        if not 0 <= retention <= 35:
            raise ValueError('Backup retention period must be between 0 and 35 days')
        kwargs['BackupRetentionPeriod'] = retention
    # Promotion reboots the instance, as in AWS.
    response = _client(long_running=True).promote_read_replica(**kwargs)
    return {
        'identifier': kwargs['DBInstanceIdentifier'],
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
    response = _client(long_running=True).create_db_cluster(**kwargs)
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
    response = _client(long_running=True).delete_db_cluster(**kwargs)
    result = {
        'identifier': clean_identifier,
        'cluster': _clean_response(response.get('DBCluster', {})),
        'response': _clean_response(response),
    }
    if not kwargs['SkipFinalSnapshot']:
        result['final_snapshot'] = _final_snapshot_status(
            'describe_db_cluster_snapshots', 'DBClusterSnapshots', DBClusterSnapshotIdentifier=kwargs['FinalDBSnapshotIdentifier'],
        )
    return result


def add_db_cluster_instance(cluster_identifier: str, instance_identifier: str, *, db_instance_class: str = 'db.r6g.large') -> dict[str, Any]:
    """Add an instance to a cluster. The first one becomes the writer, later ones readers."""
    clean_cluster = _required(cluster_identifier, 'DB cluster identifier')
    client = _client(long_running=True)
    clusters = client.describe_db_clusters(DBClusterIdentifier=clean_cluster).get('DBClusters', [])
    engine = clusters[0].get('Engine') if clusters else None
    if not engine:
        raise ValueError(f'DB cluster {clean_cluster} reports no engine')
    kwargs = {
        'DBInstanceIdentifier': _required(instance_identifier, 'DB instance identifier'),
        'DBClusterIdentifier': clean_cluster,
        'Engine': engine,
        'DBInstanceClass': _required(db_instance_class, 'DB instance class'),
    }
    response = client.create_db_instance(**kwargs)
    return {
        'identifier': kwargs['DBInstanceIdentifier'],
        'cluster_identifier': clean_cluster,
        'instance': _clean_response(response.get('DBInstance', {})),
        'response': _clean_response(response),
    }


def failover_db_cluster(cluster_identifier: str, *, target_instance_identifier: str = '') -> dict[str, Any]:
    kwargs: dict[str, Any] = {'DBClusterIdentifier': _required(cluster_identifier, 'DB cluster identifier')}
    if (target_instance_identifier or '').strip():
        kwargs['TargetDBInstanceIdentifier'] = target_instance_identifier.strip()
    response = _client().failover_db_cluster(**kwargs)
    cluster = response.get('DBCluster', {})
    writer = next(
        (member.get('DBInstanceIdentifier') for member in cluster.get('DBClusterMembers') or [] if member.get('IsClusterWriter')),
        None,
    )
    return {
        'identifier': kwargs['DBClusterIdentifier'],
        'writer': writer,
        'cluster': _clean_response(cluster),
        'response': _clean_response(response),
    }


def create_db_cluster_snapshot(cluster_identifier: str, snapshot_identifier: str, *, tags: Any = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'DBClusterIdentifier': _required(cluster_identifier, 'DB cluster identifier'),
        'DBClusterSnapshotIdentifier': _required(snapshot_identifier, 'Snapshot identifier'),
    }
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['Tags'] = clean_tags
    response = _client(long_running=True).create_db_cluster_snapshot(**kwargs)
    return {
        'identifier': kwargs['DBClusterSnapshotIdentifier'],
        'snapshot': _clean_response(response.get('DBClusterSnapshot', {})),
        'response': _clean_response(response),
    }


def delete_db_cluster_snapshot(snapshot_identifier: str) -> dict[str, Any]:
    clean_identifier = _required(snapshot_identifier, 'Snapshot identifier')
    response = _client().delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=clean_identifier)
    return {
        'identifier': clean_identifier,
        'snapshot': _clean_response(response.get('DBClusterSnapshot', {})),
        'response': _clean_response(response),
    }


def restore_db_cluster_from_snapshot(snapshot_identifier: str, cluster_identifier: str, *, engine: str = '') -> dict[str, Any]:
    clean_snapshot = _required(snapshot_identifier, 'Snapshot identifier')
    client = _client(long_running=True)
    clean_engine = (engine or '').strip()
    if not clean_engine:
        # The API requires the engine; it must match the snapshot's.
        snapshots = client.describe_db_cluster_snapshots(DBClusterSnapshotIdentifier=clean_snapshot).get('DBClusterSnapshots', [])
        clean_engine = (snapshots[0].get('Engine') if snapshots else '') or ''
        if not clean_engine:
            raise ValueError(f'DB cluster snapshot {clean_snapshot} reports no engine')
    kwargs = {
        'SnapshotIdentifier': clean_snapshot,
        'DBClusterIdentifier': _required(cluster_identifier, 'New DB cluster identifier'),
        'Engine': clean_engine,
    }
    response = client.restore_db_cluster_from_snapshot(**kwargs)
    return {
        'identifier': kwargs['DBClusterIdentifier'],
        'snapshot_identifier': clean_snapshot,
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
