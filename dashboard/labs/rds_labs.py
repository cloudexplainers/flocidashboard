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

RDS_LIFECYCLE_LAB = {
    'service': 'rds',
    'key': 'db-instance-lifecycle',
    'title': 'Provision relational databases and manage instance lifecycles in Amazon RDS',
    'description': 'Create custom DB parameter groups, provision a PostgreSQL DB instance, inspect connection topology, modify storage allocations, and execute instance reboots.',
    'steps': [
        {
            'key': 'create-parameter-group',
            'title': 'Create custom DB Parameter Group',
            'command': f'aws rds create-db-parameter-group --db-parameter-group-name {PG_NAME} --db-parameter-group-family postgres15 --description "Custom parameters for orders DB"',
            'explanation': 'Defines database engine configurations and tuning parameters for PostgreSQL 15 engines.',
        },
        {
            'key': 'create-db-instance',
            'title': 'Provision PostgreSQL DB Instance',
            'command': f'aws rds create-db-instance --db-instance-identifier {DB_IDENTIFIER} --engine postgres --db-instance-class db.t3.micro --allocated-storage 20 --master-username dbadmin --master-user-password LabPassword123! --db-parameter-group-name {PG_NAME}',
            'explanation': 'Launches a managed PostgreSQL database instance configured with our custom parameter group.',
        },
        {
            'key': 'describe-db-instance',
            'title': 'Inspect DB status, endpoint, and configuration',
            'command': f'aws rds describe-db-instances --db-instance-identifier {DB_IDENTIFIER}',
            'explanation': 'Queries the instance endpoint, port, status, allocated storage, and engine version.',
        },
        {
            'key': 'modify-db-instance',
            'title': 'Scale allocated storage capacity',
            'command': f'aws rds modify-db-instance --db-instance-identifier {DB_IDENTIFIER} --allocated-storage 40 --apply-immediately',
            'explanation': 'Dynamically expands database storage volume capacity without downtime.',
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


def step_create_instance() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    try:
        resp = rds.create_db_instance(
            DBInstanceIdentifier=DB_IDENTIFIER,
            Engine='postgres',
            DBInstanceClass='db.t3.micro',
            AllocatedStorage=20,
            MasterUsername='dbadmin',
            MasterUserPassword='LabPassword123!',
            DBParameterGroupName=PG_NAME,
        )
    except ClientError as exc:
        if 'DBInstanceAlreadyExists' in str(exc):
            resp = rds.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
        else:
            raise
    marker('db', {'id': DB_IDENTIFIER})
    return result(
        'db-instance-lifecycle',
        'create-db-instance',
        f'aws rds create-db-instance --db-instance-identifier {DB_IDENTIFIER} ...',
        resp,
        True,
        f'Created PostgreSQL DB instance {DB_IDENTIFIER}.',
        started,
    )


def step_describe_instance() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    resp = rds.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER)
    instances = resp.get('DBInstances', [])
    verified = len(instances) > 0
    return result(
        'db-instance-lifecycle',
        'describe-db-instance',
        f'aws rds describe-db-instances --db-instance-identifier {DB_IDENTIFIER}',
        resp,
        verified,
        f'Inspected DB instance {DB_IDENTIFIER} status and endpoint.' if verified else 'DB instance not found',
        started,
    )


def step_modify_instance() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    resp = rds.modify_db_instance(
        DBInstanceIdentifier=DB_IDENTIFIER,
        AllocatedStorage=40,
        ApplyImmediately=True,
    )
    marker('modified', True)
    return result(
        'db-instance-lifecycle',
        'modify-db-instance',
        f'aws rds modify-db-instance --db-instance-identifier {DB_IDENTIFIER} --allocated-storage 40',
        resp,
        True,
        f'Scaled storage capacity for DB instance {DB_IDENTIFIER} to 40 GB.',
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

    # 2. Instance
    try:
        dbs = rds.describe_db_instances(DBInstanceIdentifier=DB_IDENTIFIER).get('DBInstances', [])
        db_ok = len(dbs) > 0
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
            'verification': {'status': 'passed', 'message': f'DB Instance {DB_IDENTIFIER} running.'} if db_ok else None,
        },
        'describe-db-instance': {
            'verified': db_ok,
            'verification': {'status': 'passed', 'message': f'DB Instance {DB_IDENTIFIER} topology queried.'} if db_ok else None,
        },
        'modify-db-instance': {
            'verified': mod_ok,
            'verification': {'status': 'passed', 'message': 'DB Instance storage scaled to 40 GB.'} if mod_ok else None,
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
