"""Amazon RDS & Aurora workflow labs."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
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

RESILIENCE_LAB_KEY = 'aurora-resilience'
PRIMARY_ID = 'lab-orders-primary'
REPLICA_ID = 'lab-orders-replica'
PITR_ID = 'lab-orders-pitr'
CLUSTER_ID = 'lab-aurora-orders'
WRITER_ID = 'lab-aurora-orders-1'
READER_ID = 'lab-aurora-orders-2'
CLUSTER_SNAPSHOT_ID = 'lab-aurora-orders-snapshot'
CLUSTER_ARN = f'arn:aws:rds:{REGION}:{ACCOUNT}:cluster:{CLUSTER_ID}'
AURORA_INSTANCE_CLASS = 'db.r6g.large'
ORDER_ID = 1001
ORDER_ITEM = 'keyboard'
WRITE_ORDER_SQL = (
    f"CREATE TABLE IF NOT EXISTS orders AS SELECT {ORDER_ID} AS order_id, '{ORDER_ITEM}'::text AS item"
)
READ_ORDER_SQL = f'SELECT order_id, item FROM orders WHERE order_id = {ORDER_ID}'
SECRET_PLACEHOLDER = '<master-user-secret-arn>'

CREATE_PRIMARY_COMMAND = (
    f'aws rds create-db-instance --db-instance-identifier {PRIMARY_ID} --engine postgres '
    '--db-instance-class db.t3.micro --allocated-storage 20 --master-username dbadmin '
    '--master-user-password LabPassword123! --db-name orders --multi-az --storage-encrypted '
    f'--backup-retention-period {BACKUP_RETENTION_DAYS}'
)
CREATE_REPLICA_COMMAND = (
    f'aws rds create-db-instance-read-replica --db-instance-identifier {REPLICA_ID} '
    f'--source-db-instance-identifier {PRIMARY_ID}'
)
PITR_COMMAND = (
    f'aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier {PRIMARY_ID} '
    f'--target-db-instance-identifier {PITR_ID} --use-latest-restorable-time'
)
PROMOTE_COMMAND = (
    f'aws rds promote-read-replica --db-instance-identifier {REPLICA_ID} '
    f'--backup-retention-period {BACKUP_RETENTION_DAYS}'
)
CREATE_CLUSTER_COMMAND = (
    f'aws rds create-db-cluster --db-cluster-identifier {CLUSTER_ID} --engine aurora-postgresql '
    '--master-username dbadmin --manage-master-user-password --database-name orders --enable-http-endpoint'
)
ADD_WRITER_COMMAND = (
    f'aws rds create-db-instance --db-instance-identifier {WRITER_ID} --db-cluster-identifier {CLUSTER_ID} '
    f'--engine aurora-postgresql --db-instance-class {AURORA_INSTANCE_CLASS}'
)
ADD_READER_COMMAND = (
    f'aws rds create-db-instance --db-instance-identifier {READER_ID} --db-cluster-identifier {CLUSTER_ID} '
    f'--engine aurora-postgresql --db-instance-class {AURORA_INSTANCE_CLASS}'
)
WRITE_ORDER_COMMAND = (
    f'aws rds-data execute-statement --resource-arn {CLUSTER_ARN} --secret-arn {SECRET_PLACEHOLDER} '
    f'--database orders --sql "{WRITE_ORDER_SQL}"'
)
FAILOVER_COMMAND = (
    f'aws rds failover-db-cluster --db-cluster-identifier {CLUSTER_ID} --target-db-instance-identifier {READER_ID}'
)
CLUSTER_SNAPSHOT_COMMAND = (
    f'aws rds create-db-cluster-snapshot --db-cluster-snapshot-identifier {CLUSTER_SNAPSHOT_ID} '
    f'--db-cluster-identifier {CLUSTER_ID}'
)

RDS_RESILIENCE_LAB = {
    'service': 'rds',
    'key': RESILIENCE_LAB_KEY,
    'title': 'Design resilient databases with read replicas, point-in-time restore, and Aurora failover',
    'description': (
        'Compare the resilience tools RDS and Aurora give you: a Multi-AZ PostgreSQL primary with automated backups, '
        'a read replica that you promote, a point-in-time restore to a new instance, and an Aurora cluster that keeps '
        'its data through a failover and a manual cluster snapshot.'
    ),
    'steps': [
        {
            'key': 'create-multi-az-primary',
            'title': 'Create a Multi-AZ PostgreSQL primary with automated backups',
            'command': CREATE_PRIMARY_COMMAND,
            'explanation': (
                'Multi-AZ keeps a synchronous standby in a second Availability Zone and fails over to it automatically, '
                'behind the same endpoint, usually in one to two minutes. The standby serves no reads: Multi-AZ is for '
                'availability, not read scaling. Automated backups (retention 1 to 35 days) are what enable '
                'point-in-time restore and read replicas. '
                'How Floci emulates this: Floci records Multi-AZ but runs no standby.'
            ),
        },
        {
            'key': 'create-read-replica',
            'title': 'Create a read replica to scale reads',
            'command': CREATE_REPLICA_COMMAND,
            'explanation': (
                'A read replica is a separate DB instance with its own endpoint that RDS updates asynchronously from '
                'the source. It serves read-only traffic, so it offloads reporting and read-heavy queries; reads can '
                'lag the primary. It can live in another Region for disaster recovery. Exam trap: read replicas scale '
                'reads, Multi-AZ provides failover. '
                'How Floci emulates this: Floci copies the source into the replica once, when it is created; later '
                'writes are not replicated, and the replica accepts writes.'
            ),
        },
        {
            'key': 'restore-point-in-time',
            'title': 'Restore the primary to a point in time as a new DB instance',
            'command': PITR_COMMAND,
            'explanation': (
                'Point-in-time restore replays automated backups and transaction logs to any second in the retention '
                'window, up to the latest restorable time (usually within the last five minutes). It always creates a '
                'new DB instance with a new endpoint; the source is not changed, so applications must be pointed at '
                'the new instance. '
                'How Floci emulates this: Floci checks the time is inside the window but keeps no transaction log, so '
                'the new instance gets a copy of the source as it is now.'
            ),
        },
        {
            'key': 'promote-read-replica',
            'title': 'Promote the read replica to a standalone DB instance',
            'command': PROMOTE_COMMAND,
            'explanation': (
                'Promotion stops replication and makes the replica a standalone instance that accepts writes, after a '
                'reboot. It is a manual step, for example to recover in another Region, and cannot be undone. A '
                'promoted instance needs its own automated backups, which --backup-retention-period turns on.'
            ),
        },
        {
            'key': 'create-aurora-cluster',
            'title': 'Create an Aurora PostgreSQL cluster with a managed master password',
            'command': CREATE_CLUSTER_COMMAND,
            'explanation': (
                'An Aurora cluster separates storage from compute: one cluster volume keeps six copies of the data '
                'across three Availability Zones, and every instance reads it. The cluster endpoint always points at '
                'the writer; the reader endpoint spreads connections across Aurora Replicas. '
                '--manage-master-user-password stores the password in Secrets Manager instead of in your command, and '
                '--enable-http-endpoint turns on the Data API used later. '
                'How Floci emulates this: one PostgreSQL database serves the whole cluster.'
            ),
        },
        {
            'key': 'add-aurora-writer',
            'title': 'Add the writer instance to the cluster',
            'command': ADD_WRITER_COMMAND,
            'explanation': (
                'A new cluster has storage but no compute. Its first instance becomes the writer, the only instance '
                'that accepts writes.'
            ),
        },
        {
            'key': 'add-aurora-reader',
            'title': 'Add an Aurora Replica as a failover target',
            'command': ADD_READER_COMMAND,
            'explanation': (
                'Later instances are Aurora Replicas: they read the same cluster volume, so replica lag is usually '
                'under 100 ms, and they serve reads through the reader endpoint. Up to 15 replicas can be failover '
                'targets; in AWS you place them in other Availability Zones. '
                'How Floci emulates this: Floci does not place instances in Availability Zones.'
            ),
        },
        {
            'key': 'write-order-row',
            'title': 'Write an order through the cluster with the Data API',
            'command': WRITE_ORDER_COMMAND,
            'explanation': (
                'The RDS Data API runs SQL over HTTPS with credentials from Secrets Manager, so no database driver or '
                'open port is needed. The secret ARN is the cluster\'s MasterUserSecret: '
                f'aws rds describe-db-clusters --db-cluster-identifier {CLUSTER_ID} '
                '--query "DBClusters[0].MasterUserSecret.SecretArn". The step checks the row can be read back.'
            ),
        },
        {
            'key': 'failover-aurora-cluster',
            'title': 'Fail over the cluster to the Aurora Replica and keep the data',
            'command': FAILOVER_COMMAND,
            'explanation': (
                'Failover promotes a replica to writer and turns the old writer into a replica; the cluster endpoint '
                'follows the new writer, typically within about 30 seconds, and no data is lost because the storage '
                'is shared. Without a replica, Aurora has to create a new writer, which takes longer. The step checks '
                'the roles swapped and the order is still there. '
                'How Floci emulates this: Floci moves only the writer role; no connections drop.'
            ),
        },
        {
            'key': 'snapshot-aurora-cluster',
            'title': 'Take a manual snapshot of the cluster',
            'command': CLUSTER_SNAPSHOT_COMMAND,
            'explanation': (
                'Aurora backs up continuously for point-in-time restore within the retention period; a manual cluster '
                'snapshot is kept until you delete it, can be copied to another Region or account, and restores to a '
                'new cluster. '
                'How Floci emulates this: the snapshot holds a dump of the cluster database (Aurora PostgreSQL only).'
            ),
        },
    ],
}

LABS = [RDS_LIFECYCLE_LAB, RDS_RESILIENCE_LAB]


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
    if lab_key == RESILIENCE_LAB_KEY:
        runners = {
            'create-multi-az-primary': step_create_primary,
            'create-read-replica': step_create_replica,
            'restore-point-in-time': step_restore_point_in_time,
            'promote-read-replica': step_promote_replica,
            'create-aurora-cluster': step_create_cluster,
            'add-aurora-writer': step_add_writer,
            'add-aurora-reader': step_add_reader,
            'write-order-row': step_write_order,
            'failover-aurora-cluster': step_failover_cluster,
            'snapshot-aurora-cluster': step_snapshot_cluster,
        }
        runner = runners.get(step_key)
        if not runner:
            raise ValueError(f'Unknown step {step_key} for lab {lab_key}')
        return runner()
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
    if lab_key == RESILIENCE_LAB_KEY:
        return resilience_status()
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
    if lab_key == RESILIENCE_LAB_KEY:
        return resilience_reset()
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


# ── Resilience lab: read replicas, point-in-time restore, Aurora failover ──────

def _error_code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _instance(identifier: str) -> dict[str, Any] | None:
    try:
        instances = client('rds').describe_db_instances(DBInstanceIdentifier=identifier).get('DBInstances', [])
    except ClientError as exc:
        if _error_code(exc) in {'DBInstanceNotFound', 'DBInstanceNotFoundFault'}:
            return None
        raise
    return instances[0] if instances else None


def _cluster() -> dict[str, Any] | None:
    try:
        clusters = client('rds').describe_db_clusters(DBClusterIdentifier=CLUSTER_ID).get('DBClusters', [])
    except ClientError as exc:
        if _error_code(exc) in {'DBClusterNotFoundFault', 'DBClusterNotFound'}:
            return None
        raise
    return clusters[0] if clusters else None


def _writer(cluster: dict[str, Any] | None) -> str | None:
    for member in (cluster or {}).get('DBClusterMembers') or []:
        if member.get('IsClusterWriter') is True:
            return member.get('DBInstanceIdentifier')
    return None


def _members(cluster: dict[str, Any] | None) -> dict[str, bool]:
    return {
        member.get('DBInstanceIdentifier'): member.get('IsClusterWriter') is True
        for member in (cluster or {}).get('DBClusterMembers') or []
    }


def _create_or_existing(create, describe, exists_codes: set[str]) -> Any:
    """Run a create call; when the resource already exists (a re-run), return its description."""
    try:
        return create()
    except ClientError as exc:
        if _error_code(exc) in exists_codes:
            return describe()
        raise


def _resilience_result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    if verified:
        marker(f'{RESILIENCE_LAB_KEY}:{step}', True)
    return result(RESILIENCE_LAB_KEY, step, command, response, verified, message, started)


def _check_primary(instance: dict[str, Any] | None) -> tuple[bool, str]:
    if not instance:
        return False, f'DB instance {PRIMARY_ID} not found.'
    problems = []
    if instance.get('DBInstanceStatus') != 'available':
        problems.append(f"status is {instance.get('DBInstanceStatus')}")
    if instance.get('MultiAZ') is not True:
        problems.append('Multi-AZ is off')
    if instance.get('BackupRetentionPeriod') != BACKUP_RETENTION_DAYS:
        problems.append(f"backup retention is {instance.get('BackupRetentionPeriod')} days, not {BACKUP_RETENTION_DAYS}")
    if not instance.get('LatestRestorableTime'):
        problems.append('no latest restorable time is reported, so point-in-time restore is not available')
    if problems:
        return False, f'{PRIMARY_ID}: ' + '; '.join(problems) + '.'
    return True, (
        f'{PRIMARY_ID} is a Multi-AZ PostgreSQL instance with {BACKUP_RETENTION_DAYS}-day automated backups; '
        f"it can be restored to any time up to {instance['LatestRestorableTime']}."
    )


def step_create_primary() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    response = _create_or_existing(
        lambda: rds.create_db_instance(
            DBInstanceIdentifier=PRIMARY_ID,
            Engine='postgres',
            DBInstanceClass='db.t3.micro',
            AllocatedStorage=20,
            MasterUsername='dbadmin',
            MasterUserPassword='LabPassword123!',
            DBName='orders',
            MultiAZ=True,
            StorageEncrypted=True,
            BackupRetentionPeriod=BACKUP_RETENTION_DAYS,
        ),
        lambda: rds.describe_db_instances(DBInstanceIdentifier=PRIMARY_ID),
        {'DBInstanceAlreadyExists', 'DBInstanceAlreadyExistsFault'},
    )
    verified, message = _check_primary(_instance(PRIMARY_ID))
    return _resilience_result('create-multi-az-primary', CREATE_PRIMARY_COMMAND, response, verified, message, started)


def _check_replica_link() -> tuple[bool, str]:
    replica = _instance(REPLICA_ID)
    primary = _instance(PRIMARY_ID)
    if not replica:
        return False, f'Read replica {REPLICA_ID} not found.'
    if replica.get('ReadReplicaSourceDBInstanceIdentifier') != PRIMARY_ID:
        return False, f'{REPLICA_ID} does not report {PRIMARY_ID} as its source.'
    if REPLICA_ID not in ((primary or {}).get('ReadReplicaDBInstanceIdentifiers') or []):
        return False, f'{PRIMARY_ID} does not list {REPLICA_ID} among its read replicas.'
    replication = next(
        (info.get('Status') for info in replica.get('StatusInfos') or [] if info.get('StatusType') == 'read replication'),
        None,
    )
    return True, (
        f'{REPLICA_ID} replicates from {PRIMARY_ID}'
        + (f' (status {replication})' if replication else '')
        + f", has its own endpoint port {(replica.get('Endpoint') or {}).get('Port')}, and starts with "
        f"backup retention {replica.get('BackupRetentionPeriod')}."
    )


def step_create_replica() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    if (_instance(REPLICA_ID) or {}).get('ReadReplicaSourceDBInstanceIdentifier') is None and marked(f'{RESILIENCE_LAB_KEY}:promote-read-replica'):
        # Already promoted in this run: the replica existed and is now standalone.
        replica = _instance(REPLICA_ID)
        return _resilience_result('create-read-replica', CREATE_REPLICA_COMMAND, {'DBInstance': replica}, bool(replica),
                                  f'{REPLICA_ID} was created as a read replica and has since been promoted.', started)
    response = _create_or_existing(
        lambda: rds.create_db_instance_read_replica(DBInstanceIdentifier=REPLICA_ID, SourceDBInstanceIdentifier=PRIMARY_ID),
        lambda: rds.describe_db_instances(DBInstanceIdentifier=REPLICA_ID),
        {'DBInstanceAlreadyExists', 'DBInstanceAlreadyExistsFault'},
    )
    verified, message = _check_replica_link()
    return _resilience_result('create-read-replica', CREATE_REPLICA_COMMAND, response, verified, message, started)


def _check_pitr(restored: dict[str, Any] | None) -> tuple[bool, str]:
    if not restored:
        return False, f'DB instance {PITR_ID} not found.'
    problems = []
    if restored.get('DBInstanceStatus') != 'available':
        problems.append(f"status is {restored.get('DBInstanceStatus')}")
    if restored.get('Engine') != 'postgres':
        problems.append(f"engine is {restored.get('Engine')}")
    if restored.get('ReadReplicaSourceDBInstanceIdentifier'):
        problems.append('it is linked to a source as a replica')
    if restored.get('StorageEncrypted') is not True:
        problems.append('storage is not encrypted like the source')
    if not _instance(PRIMARY_ID):
        problems.append(f'the source {PRIMARY_ID} is gone')
    if problems:
        return False, f'{PITR_ID}: ' + '; '.join(problems) + '.'
    endpoint = restored.get('Endpoint') or {}
    return True, (
        f'{PITR_ID} is a new, independent PostgreSQL instance at {endpoint.get("Address")}:{endpoint.get("Port")}, '
        f'encrypted like its source; {PRIMARY_ID} is unchanged.'
    )


def step_restore_point_in_time() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    response = _create_or_existing(
        lambda: rds.restore_db_instance_to_point_in_time(
            SourceDBInstanceIdentifier=PRIMARY_ID,
            TargetDBInstanceIdentifier=PITR_ID,
            UseLatestRestorableTime=True,
        ),
        lambda: rds.describe_db_instances(DBInstanceIdentifier=PITR_ID),
        {'DBInstanceAlreadyExists', 'DBInstanceAlreadyExistsFault'},
    )
    verified, message = _check_pitr(_instance(PITR_ID))
    return _resilience_result('restore-point-in-time', PITR_COMMAND, response, verified, message, started)


def _check_promoted() -> tuple[bool, str]:
    replica = _instance(REPLICA_ID)
    if not replica:
        return False, f'DB instance {REPLICA_ID} not found.'
    if replica.get('ReadReplicaSourceDBInstanceIdentifier'):
        return False, f"{REPLICA_ID} is still a replica of {replica['ReadReplicaSourceDBInstanceIdentifier']}."
    if REPLICA_ID in ((_instance(PRIMARY_ID) or {}).get('ReadReplicaDBInstanceIdentifiers') or []):
        return False, f'{PRIMARY_ID} still lists {REPLICA_ID} as a read replica.'
    if replica.get('BackupRetentionPeriod') != BACKUP_RETENTION_DAYS:
        return False, f"{REPLICA_ID} keeps backups for {replica.get('BackupRetentionPeriod')} days, not {BACKUP_RETENTION_DAYS}."
    return True, f'{REPLICA_ID} is now a standalone DB instance with {BACKUP_RETENTION_DAYS}-day automated backups.'


def step_promote_replica() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    replica = _instance(REPLICA_ID)
    if replica and replica.get('ReadReplicaSourceDBInstanceIdentifier'):
        response = rds.promote_read_replica(DBInstanceIdentifier=REPLICA_ID, BackupRetentionPeriod=BACKUP_RETENTION_DAYS)
    else:
        # Not a replica (already promoted, or never created): report what Floci shows.
        response = {'DBInstance': replica}
    verified, message = _check_promoted()
    return _resilience_result('promote-read-replica', PROMOTE_COMMAND, response, verified, message, started)


def _check_cluster(cluster: dict[str, Any] | None) -> tuple[bool, str]:
    if not cluster:
        return False, f'DB cluster {CLUSTER_ID} not found.'
    problems = []
    if cluster.get('Status') != 'available':
        problems.append(f"status is {cluster.get('Status')}")
    if not cluster.get('Endpoint') or not cluster.get('ReaderEndpoint'):
        problems.append('the cluster or reader endpoint is not reported')
    if not (cluster.get('MasterUserSecret') or {}).get('SecretArn'):
        problems.append('no managed master user secret is reported')
    if problems:
        return False, f'{CLUSTER_ID}: ' + '; '.join(problems) + '.'
    return True, (
        f"{CLUSTER_ID} is available with cluster endpoint {cluster['Endpoint']}:{cluster.get('Port')} and reader endpoint "
        f"{cluster['ReaderEndpoint']}; its master password is in {cluster['MasterUserSecret']['SecretArn']}."
    )


def step_create_cluster() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    response = _create_or_existing(
        lambda: rds.create_db_cluster(
            DBClusterIdentifier=CLUSTER_ID,
            Engine='aurora-postgresql',
            MasterUsername='dbadmin',
            ManageMasterUserPassword=True,
            DatabaseName='orders',
            EnableHttpEndpoint=True,
        ),
        lambda: rds.describe_db_clusters(DBClusterIdentifier=CLUSTER_ID),
        {'DBClusterAlreadyExistsFault'},
    )
    verified, message = _check_cluster(_cluster())
    return _resilience_result('create-aurora-cluster', CREATE_CLUSTER_COMMAND, response, verified, message, started)


def _add_cluster_instance(identifier: str):
    rds = client('rds')
    return _create_or_existing(
        lambda: rds.create_db_instance(
            DBInstanceIdentifier=identifier,
            DBClusterIdentifier=CLUSTER_ID,
            Engine='aurora-postgresql',
            DBInstanceClass=AURORA_INSTANCE_CLASS,
        ),
        lambda: rds.describe_db_instances(DBInstanceIdentifier=identifier),
        {'DBInstanceAlreadyExists', 'DBInstanceAlreadyExistsFault'},
    )


def step_add_writer() -> dict[str, Any]:
    started = time.perf_counter()
    response = _add_cluster_instance(WRITER_ID)
    members = _members(_cluster())
    failed_over = bool(marked(f'{RESILIENCE_LAB_KEY}:failover-aurora-cluster'))
    if WRITER_ID not in members:
        verified, message = False, f'{WRITER_ID} is not a member of {CLUSTER_ID}.'
    elif members[WRITER_ID] or failed_over:
        verified, message = True, f'{WRITER_ID} joined {CLUSTER_ID} as its writer.'
    else:
        verified, message = False, f'{WRITER_ID} joined {CLUSTER_ID} but is not the writer.'
    return _resilience_result('add-aurora-writer', ADD_WRITER_COMMAND, response, verified, message, started)


def step_add_reader() -> dict[str, Any]:
    started = time.perf_counter()
    response = _add_cluster_instance(READER_ID)
    cluster = _cluster()
    members = _members(cluster)
    failed_over = bool(marked(f'{RESILIENCE_LAB_KEY}:failover-aurora-cluster'))
    if READER_ID not in members:
        verified, message = False, f'{READER_ID} is not a member of {CLUSTER_ID}.'
    elif not members[READER_ID] or failed_over:
        verified, message = True, (
            f'{READER_ID} joined {CLUSTER_ID} as an Aurora Replica; the writer is {_writer(cluster)}, '
            f"and the reader endpoint is {(cluster or {}).get('ReaderEndpoint')}."
        )
    else:
        verified, message = False, f'{READER_ID} joined {CLUSTER_ID} as the writer, not as a replica. Add the writer first.'
    return _resilience_result('add-aurora-reader', ADD_READER_COMMAND, response, verified, message, started)


def _secret_arn(cluster: dict[str, Any] | None) -> str | None:
    return ((cluster or {}).get('MasterUserSecret') or {}).get('SecretArn')


def _read_order(cluster: dict[str, Any] | None) -> tuple[bool, str, Any]:
    secret = _secret_arn(cluster)
    if not secret:
        return False, f'{CLUSTER_ID} reports no managed master user secret.', None
    try:
        response = client('rds-data').execute_statement(
            resourceArn=cluster.get('DBClusterArn') or CLUSTER_ARN,
            secretArn=secret,
            database='orders',
            sql=READ_ORDER_SQL,
        )
    except ClientError as exc:
        return False, f'Reading order {ORDER_ID} failed: {exc}', None
    records = response.get('records') or []
    row = records[0] if records else []
    values = [next(iter(field.values())) if isinstance(field, dict) and field else None for field in row]
    if values[:2] == [ORDER_ID, ORDER_ITEM]:
        return True, f'Order {ORDER_ID} ({ORDER_ITEM}) is in the cluster database.', response
    return False, f'Order {ORDER_ID} was not found in the cluster database.', response


def step_write_order() -> dict[str, Any]:
    started = time.perf_counter()
    cluster = _cluster()
    secret = _secret_arn(cluster)
    command = WRITE_ORDER_COMMAND.replace(SECRET_PLACEHOLDER, secret or SECRET_PLACEHOLDER)
    if not secret:
        return _resilience_result('write-order-row', command, {}, False, f'{CLUSTER_ID} reports no managed master user secret. Create the cluster first.', started)
    if not _writer(cluster):
        return _resilience_result('write-order-row', command, {}, False, f'{CLUSTER_ID} has no writer instance. Add the writer first.', started)
    response = client('rds-data').execute_statement(
        resourceArn=cluster.get('DBClusterArn') or CLUSTER_ARN,
        secretArn=secret,
        database='orders',
        sql=WRITE_ORDER_SQL,
    )
    verified, message, _ = _read_order(cluster)
    return _resilience_result('write-order-row', command, response, verified, message, started)


def step_failover_cluster() -> dict[str, Any]:
    started = time.perf_counter()
    cluster = _cluster()
    if _writer(cluster) == READER_ID:
        response = {'DBCluster': cluster}  # A re-run: the replica is already the writer.
    else:
        response = client('rds').failover_db_cluster(DBClusterIdentifier=CLUSTER_ID, TargetDBInstanceIdentifier=READER_ID)
    cluster = _cluster()
    members = _members(cluster)
    if members.get(READER_ID) is not True:
        verified, message = False, f'{READER_ID} did not become the writer of {CLUSTER_ID}.'
    elif members.get(WRITER_ID) is not False:
        verified, message = False, f'{WRITER_ID} is not reported as a replica after the failover.'
    else:
        has_order, order_message, _ = _read_order(cluster)
        verified = has_order
        message = (
            f'{READER_ID} is now the writer and {WRITER_ID} a replica. ' + order_message
            if has_order
            else f'The roles swapped, but the data check failed: {order_message}'
        )
    return _resilience_result('failover-aurora-cluster', FAILOVER_COMMAND, response, verified, message, started)


def _cluster_snapshot() -> dict[str, Any] | None:
    try:
        snapshots = client('rds').describe_db_cluster_snapshots(DBClusterSnapshotIdentifier=CLUSTER_SNAPSHOT_ID).get('DBClusterSnapshots', [])
    except ClientError as exc:
        if _error_code(exc) in {'DBClusterSnapshotNotFoundFault', 'DBClusterSnapshotNotFound'}:
            return None
        raise
    return snapshots[0] if snapshots else None


def _check_cluster_snapshot(snapshot: dict[str, Any] | None) -> tuple[bool, str]:
    if not snapshot:
        return False, f'Cluster snapshot {CLUSTER_SNAPSHOT_ID} not found.'
    if snapshot.get('DBClusterIdentifier') != CLUSTER_ID or snapshot.get('Status') != 'available':
        return False, f"{CLUSTER_SNAPSHOT_ID} is {snapshot.get('Status')} for cluster {snapshot.get('DBClusterIdentifier')}."
    return True, f"{CLUSTER_SNAPSHOT_ID} of {CLUSTER_ID} is available ({snapshot.get('SnapshotType')}, {snapshot.get('Engine')})."


def step_snapshot_cluster() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    response = _create_or_existing(
        lambda: rds.create_db_cluster_snapshot(DBClusterSnapshotIdentifier=CLUSTER_SNAPSHOT_ID, DBClusterIdentifier=CLUSTER_ID),
        lambda: rds.describe_db_cluster_snapshots(DBClusterSnapshotIdentifier=CLUSTER_SNAPSHOT_ID),
        {'DBClusterSnapshotAlreadyExistsFault'},
    )
    verified, message = _check_cluster_snapshot(_cluster_snapshot())
    return _resilience_result('snapshot-aurora-cluster', CLUSTER_SNAPSHOT_COMMAND, response, verified, message, started)


def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def resilience_status() -> dict[str, Any]:
    """Live state where it lasts; a step whose state a later step changes also needs its marker."""
    done = lambda step: bool(marked(f'{RESILIENCE_LAB_KEY}:{step}'))  # noqa: E731
    cluster_exists = _safe(_cluster)
    checks = {
        'create-multi-az-primary': lambda: _check_primary(_instance(PRIMARY_ID))[0],
        # Promotion removes the link, so a promoted replica counts once the step passed.
        'create-read-replica': lambda: _check_replica_link()[0] or (done('create-read-replica') and _instance(REPLICA_ID)),
        'restore-point-in-time': lambda: _check_pitr(_instance(PITR_ID))[0],
        'promote-read-replica': lambda: done('promote-read-replica') and _check_promoted()[0],
        'create-aurora-cluster': lambda: _check_cluster(_cluster())[0],
        # Failover swaps the roles, so membership plus the marker stands for these two.
        'add-aurora-writer': lambda: cluster_exists and done('add-aurora-writer') and WRITER_ID in _members(_cluster()),
        'add-aurora-reader': lambda: cluster_exists and done('add-aurora-reader') and READER_ID in _members(_cluster()),
        'write-order-row': lambda: cluster_exists and _read_order(_cluster())[0],
        'failover-aurora-cluster': lambda: cluster_exists and done('failover-aurora-cluster') and _writer(_cluster()) == READER_ID,
        'snapshot-aurora-cluster': lambda: _check_cluster_snapshot(_cluster_snapshot())[0],
    }
    steps = {}
    for step, check in checks.items():
        verified = _safe(check)
        steps[step] = {
            'verified': verified,
            'verification': {'status': 'passed', 'message': 'Verified against Floci.'} if verified else None,
        }
    passed = sum(1 for item in steps.values() if item['verified'])
    complete = passed == len(steps)
    return {
        'service': 'rds',
        'lab': RESILIENCE_LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def _wait_until_gone(describe, attempts: int = 30, delay: float = 1.0) -> bool:
    for _ in range(attempts):
        try:
            if not describe():
                return True
        except (ClientError, BotoCoreError):
            return True
        time.sleep(delay)
    return False


def resilience_reset() -> dict[str, Any]:
    started = time.perf_counter()
    rds = client('rds')
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call, missing_codes: set[str]) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            if _error_code(exc) not in missing_codes:
                errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    instance_missing = {'DBInstanceNotFound', 'DBInstanceNotFoundFault'}
    attempt(f'cluster-snapshot:{CLUSTER_SNAPSHOT_ID}',
            lambda: rds.delete_db_cluster_snapshot(DBClusterSnapshotIdentifier=CLUSTER_SNAPSHOT_ID),
            {'DBClusterSnapshotNotFoundFault', 'DBClusterSnapshotNotFound'})
    # Cluster members go first: a cluster with instances can't be deleted.
    for identifier in (READER_ID, WRITER_ID, REPLICA_ID, PITR_ID, PRIMARY_ID):
        attempt(f'db:{identifier}',
                lambda identifier=identifier: rds.delete_db_instance(DBInstanceIdentifier=identifier, SkipFinalSnapshot=True),
                instance_missing)
    for identifier in (READER_ID, WRITER_ID):
        if not _wait_until_gone(lambda identifier=identifier: _instance(identifier)):
            errors.append(f'db:{identifier}: still reported after delete')
    attempt(f'cluster:{CLUSTER_ID}',
            lambda: rds.delete_db_cluster(DBClusterIdentifier=CLUSTER_ID, SkipFinalSnapshot=True),
            {'DBClusterNotFoundFault', 'DBClusterNotFound'})
    for step in RDS_RESILIENCE_LAB['steps']:
        cache.delete(f"{CACHE_PREFIX}{RESILIENCE_LAB_KEY}:{step['key']}")

    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': 'rds',
        'lab': RESILIENCE_LAB_KEY,
        'command': f'aws rds delete-db-cluster --db-cluster-identifier {CLUSTER_ID} --skip-final-snapshot ... # cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'errors': errors,
        'verification': {
            'status': 'failed' if errors else 'passed',
            'message': 'Some lab resources could not be deleted: ' + '; '.join(errors) if errors else 'RDS resilience lab resources cleaned up.',
        },
    }
