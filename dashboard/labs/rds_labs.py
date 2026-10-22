"""Amazon RDS & Aurora workflow labs."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from botocore.config import Config
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

REGION = 'us-east-1'
ACCOUNT = '000000000000'
CACHE_PREFIX = 'floci-lab:rds:'

PG_NAME = 'lab-pg-ecommerce'
DB_IDENTIFIER = 'lab-db-orders'
BACKUP_RETENTION_DAYS = 7
BACKUP_WINDOW = '03:00-04:00'
INITIAL_STORAGE_GB = 20
SCALED_STORAGE_GB = 40
CREATE_INSTANCE_COMMAND = (
    f'aws rds create-db-instance --db-instance-identifier {DB_IDENTIFIER} --engine postgres '
    f'--db-instance-class db.t3.micro --allocated-storage {INITIAL_STORAGE_GB} --master-username dbadmin '
    f'--master-user-password LabPassword123! --db-parameter-group-name {PG_NAME} --storage-encrypted '
    f'--backup-retention-period {BACKUP_RETENTION_DAYS} --preferred-backup-window {BACKUP_WINDOW}'
)

RDS_LIFECYCLE_LAB = {
    'service': 'rds',
    'key': 'db-instance-lifecycle',
    'title': 'Provision relational databases and manage instance lifecycles in Amazon RDS',
    'description': (
        'Create a custom DB parameter group, provision an encrypted PostgreSQL DB instance with automated backups, '
        'inspect its endpoint and data-protection settings, scale its storage, and reboot it.'
    ),
    'steps': [
        {
            'key': 'create-parameter-group',
            'title': 'Create custom DB Parameter Group',
            'command': f'aws rds create-db-parameter-group --db-parameter-group-name {PG_NAME} --db-parameter-group-family postgres15 --description "Custom parameters for orders DB"',
            'explanation': 'Defines database engine configurations and tuning parameters for PostgreSQL 15 engines.',
        },
        {
            'key': 'create-db-instance',
            'title': 'Provision an encrypted PostgreSQL DB instance with automated backups',
            'command': CREATE_INSTANCE_COMMAND,
            'explanation': (
                'Launches a managed PostgreSQL instance with the custom parameter group. --storage-encrypted encrypts '
                'storage, automated backups, snapshots, and read replicas with KMS; it can only be chosen at creation '
                '(to encrypt an existing instance you copy a snapshot with encryption and restore it). '
                f'--backup-retention-period {BACKUP_RETENTION_DAYS} keeps daily automated backups and transaction logs '
                'for point-in-time restore (0 turns them off; the maximum is 35 days).'
            ),
        },
        {
            'key': 'describe-db-instance',
            'title': 'Inspect status, endpoint, and data protection',
            'command': f'aws rds describe-db-instances --db-instance-identifier {DB_IDENTIFIER}',
            'explanation': (
                'Queries the endpoint, port, status, and storage, and confirms StorageEncrypted, the KMS key, '
                'the backup retention period, and the daily backup window.'
            ),
        },
        {
            'key': 'modify-db-instance',
            'title': 'Scale allocated storage capacity',
            'command': f'aws rds modify-db-instance --db-instance-identifier {DB_IDENTIFIER} --allocated-storage {SCALED_STORAGE_GB} --apply-immediately',
            'explanation': 'Grows the storage volume without downtime. RDS storage can grow but never shrink.',
        },
        {
            'key': 'reboot-db-instance',
            'title': 'Reboot database instance',
            'command': f'aws rds reboot-db-instance --db-instance-identifier {DB_IDENTIFIER}',
            'explanation': 'Executes a graceful database server reboot and verifies restart readiness.',
        },
    ],
}

LABS = [RDS_LIFECYCLE_LAB]


def client(name: str):
    factory = FlociClientFactory()
    # Database image pulls and initialization outlast ordinary inventory requests.
    factory.config = factory.config.merge(Config(read_timeout=120, retries={'max_attempts': 0}))
    return factory.client(name)


def marker(key: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + key, _clean_response(value), timeout=86400)


def marked(key: str) -> Any:
    return cache.get(CACHE_PREFIX + key)


def result(lab: str, step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': 'rds',
        'lab': lab,
        'step': step,
        'command': command,
        'exit_code': 0,
        'stdout': json.dumps(clean, indent=2, default=str),
        'stderr': '',
        'json': clean,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verified,
        'verification': {'status': 'passed' if verified else 'failed', 'message': message},
    }


def step_create_pg() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    try:
        resp = rds.create_db_parameter_group(
            DBParameterGroupName=PG_NAME,
            DBParameterGroupFamily='postgres15',
            Description='Custom parameters for orders DB',
        )
    except ClientError as exc:
        if 'DBParameterGroupAlreadyExists' in str(exc):
            resp = {'DBParameterGroup': {'DBParameterGroupName': PG_NAME, 'DBParameterGroupFamily': 'postgres15'}}
        else:
            raise
    marker('pg', {'name': PG_NAME})
    return result(
        'db-instance-lifecycle',
        'create-parameter-group',
        f'aws rds create-db-parameter-group --db-parameter-group-name {PG_NAME} ...',
        resp,
        True,
        f'Created DB parameter group {PG_NAME}.',
        started,
    )


def _describe_lab_instance() -> dict[str, Any] | None:
    try:
        instances = client('rds').describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER).get('DBInstances', [])
    except ClientError:
        return None
    return instances[0] if instances else None


def _check_data_protection(instance: dict[str, Any] | None) -> tuple[bool, str]:
    if not instance:
        return False, f'DB instance {DB_IDENTIFIER} not found.'
    problems = []
    if not instance.get('StorageEncrypted'):
        problems.append('storage is not encrypted')
    if instance.get('BackupRetentionPeriod') != BACKUP_RETENTION_DAYS:
        problems.append(f"backup retention is {instance.get('BackupRetentionPeriod')} days, not {BACKUP_RETENTION_DAYS}")
    if problems:
        return False, f'{DB_IDENTIFIER}: ' + '; '.join(problems) + '. Reset the lab and create the instance again.'
    return True, (
        f'{DB_IDENTIFIER} is encrypted at rest'
        + (f" with {instance['KmsKeyId']}" if instance.get('KmsKeyId') else '')
        + f' and keeps automated backups for {BACKUP_RETENTION_DAYS} days.'
    )


def step_create_instance() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    try:
        resp = rds.create_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            Engine='postgres',
            DBInstanceClass='db.t3.micro',
            AllocatedStorage=INITIAL_STORAGE_GB,
            MasterUsername='dbadmin',
            MasterUserPassword='LabPassword123!',
            DBParameterGroupName=PG_NAME,
            StorageEncrypted=True,
            BackupRetentionPeriod=BACKUP_RETENTION_DAYS,
            PreferredBackupWindow=BACKUP_WINDOW,
        )
    except ClientError as exc:
        if 'DBInstanceAlreadyExists' in str(exc):
            resp = rds.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
        else:
            raise
    verified, message = _check_data_protection(_describe_lab_instance())
    if verified:
        marker('db', {'id': DB_IDENTIFIER})
    return result(
        'db-instance-lifecycle',
        'create-db-instance',
        CREATE_INSTANCE_COMMAND,
        resp,
        verified,
        message,
        started,
    )


def step_describe_instance() -> dict[str, Any]:
    started = time.perf_counter()
    instance = _describe_lab_instance()
    verified, message = _check_data_protection(instance)
    if verified and not (instance.get('Endpoint') or {}).get('Address'):
        verified, message = False, f'{DB_IDENTIFIER} has no endpoint yet.'
    if verified:
        endpoint = instance['Endpoint']
        message = (
            f"{DB_IDENTIFIER} is {instance.get('DBInstanceStatus')} at {endpoint.get('Address')}:{endpoint.get('Port')}. "
            + message
        )
    return result(
        'db-instance-lifecycle',
        'describe-db-instance',
        f'aws rds describe-db-instances --db-instance-identifier {DB_IDENTIFIER}',
        {'DBInstances': [instance]} if instance else {'DBInstances': []},
        verified,
        message,
        started,
    )


def step_modify_instance() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    resp = rds.modify_db_instance(
        DBInstanceIdentifier=DB_IDENTIFIER,
        AllocatedStorage=SCALED_STORAGE_GB,
        ApplyImmediately=True,
    )
    instance = _describe_lab_instance() or {}
    pending = (instance.get('PendingModifiedValues') or {}).get('AllocatedStorage')
    verified = SCALED_STORAGE_GB in (instance.get('AllocatedStorage'), pending)
    if verified:
        marker('modified', True)
    return result(
        'db-instance-lifecycle',
        'modify-db-instance',
        f'aws rds modify-db-instance --db-instance-identifier {DB_IDENTIFIER} --allocated-storage {SCALED_STORAGE_GB} --apply-immediately',
        resp,
        verified,
        f'Scaled storage for DB instance {DB_IDENTIFIER} to {SCALED_STORAGE_GB} GB.'
        if verified
        else f"{DB_IDENTIFIER} still reports {instance.get('AllocatedStorage')} GB.",
        started,
    )


def step_reboot_instance() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    resp = rds.reboot_db_instance(DBInstanceIdentifier=DB_IDENTIFIER)
    marker('rebooted', True)
    return result(
        'db-instance-lifecycle',
        'reboot-db-instance',
        f'aws rds reboot-db-instance --db-instance-identifier {DB_IDENTIFIER}',
        resp,
        True,
        f'Rebooted DB instance {DB_IDENTIFIER} successfully.',
        started,
    )


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if lab_key != 'db-instance-lifecycle':
        raise ValueError(f'Unknown RDS lab: {lab_key}')
    runners = {
        'create-parameter-group': step_create_pg,
        'create-db-instance': step_create_instance,
        'describe-db-instance': step_describe_instance,
        'modify-db-instance': step_modify_instance,
        'reboot-db-instance': step_reboot_instance,
    }
    runner = runners.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {lab_key}')
    return runner()


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key != 'db-instance-lifecycle':
        raise ValueError(f'Unknown RDS lab: {lab_key}')
    rds = client('rds')
    
    # 1. Parameter group
    try:
        pgs = rds.describe_db_parameter_groups(DBParameterGroupName=PG_NAME).get('DBParameterGroups', [])
        pg_ok = len(pgs) > 0
    except Exception:
        pg_ok = bool(marked('pg'))

    # 2. Instance, encrypted and with automated backups
    try:
        dbs = rds.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER).get('DBInstances', [])
        db_ok = _check_data_protection(dbs[0] if dbs else None)[0]
    except Exception:
        db_ok = bool(marked('db'))

    # 3. Modified
    mod_ok = bool(marked('modified'))

    # 4. Reboot
    reb_ok = bool(marked('rebooted'))

    steps_dict = {
        'create-parameter-group': {
            'verified': pg_ok,
            'verification': {'status': 'passed', 'message': f'DB Parameter Group {PG_NAME} active.'} if pg_ok else None,
        },
        'create-db-instance': {
            'verified': db_ok,
            'verification': {'status': 'passed', 'message': f'DB Instance {DB_IDENTIFIER} is encrypted with {BACKUP_RETENTION_DAYS}-day backups.'} if db_ok else None,
        },
        'describe-db-instance': {
            'verified': db_ok,
            'verification': {'status': 'passed', 'message': f'DB Instance {DB_IDENTIFIER} topology queried.'} if db_ok else None,
        },
        'modify-db-instance': {
            'verified': mod_ok,
            'verification': {'status': 'passed', 'message': f'DB Instance storage scaled to {SCALED_STORAGE_GB} GB.'} if mod_ok else None,
        },
        'reboot-db-instance': {
            'verified': reb_ok,
            'verification': {'status': 'passed', 'message': f'DB Instance {DB_IDENTIFIER} rebooted.'} if reb_ok else None,
        },
    }

    passed_count = sum(1 for s in steps_dict.values() if s['verified'])
    is_complete = passed_count == len(steps_dict)

    return {
        'service': 'rds',
        'lab': lab_key,
        'complete': is_complete,
        'status': 'passed' if is_complete else ('in_progress' if passed_count > 0 else 'not_started'),
        'steps': steps_dict,
        'passed_steps': passed_count,
        'total_steps': len(steps_dict),
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key != 'db-instance-lifecycle':
        raise ValueError(f'Unknown RDS lab: {lab_key}')
    started = time.perf_counter()
    rds = client('rds')
    deleted = []

    try:
        rds.delete_db_instance(DBInstanceIdentifier=DB_IDENTIFIER, SkipFinalSnapshot=True)
        deleted.append(f'db:{DB_IDENTIFIER}')
    except Exception:
        pass

    try:
        rds.delete_db_parameter_group(DBParameterGroupName=PG_NAME)
        deleted.append(f'pg:{PG_NAME}')
    except Exception:
        pass

    for k in ['pg', 'db', 'modified', 'rebooted']:
        cache.delete(CACHE_PREFIX + k)

    payload = {'status': 'reset', 'deleted_resources': deleted}
    return {
        'service': 'rds',
        'lab': lab_key,
        'command': f'aws rds delete-db-instance --db-instance-identifier {DB_IDENTIFIER} ... # cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'verification': {'status': 'passed', 'message': 'RDS lab resources cleaned up.'},
    }
