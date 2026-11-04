"""Backup and restore drill: a web server, an orders table, an assets bucket and a database with RTO and RPO targets,
recovery points taken for each (a golden AMI, point-in-time recovery, object versions, an RDS snapshot), an incident
that loses the server and corrupts the data, each resource restored and timed against the targets, and the app pointed
at the restored copies.

Floci 2.4.0 runs all of it: CreateImage captures the instance's files and a new instance from the AMI has them,
DynamoDB restores the items as they were at a chosen second, S3 keeps every version, and RDS takes snapshots and
restores a real PostgreSQL from a snapshot or to a point in time. What Floci does not do: it has no EBS snapshots of
data volumes (CreateSnapshot), no AWS Backup restore jobs or restore testing plans, and RDS restores finish in seconds
where AWS takes many minutes, so the lab's RTO numbers are much shorter than a real drill's.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'backup'
LAB_KEY = 'restore-drill'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'

APP = 'lab-drill'
IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't3.micro'
WEB_NAME = 'lab-drill-web'
RESTORED_WEB_NAME = 'lab-drill-web-restored'
AMI_NAME = 'lab-drill-golden'
RELEASE = 'release-7'
APP_DIR = '/opt/lab-drill'
TABLE = 'lab-drill-orders'
RESTORED_TABLE = 'lab-drill-orders-restored'
BUCKET = 'lab-drill-assets'
ASSET_KEY = 'site/banner.txt'
GOOD_ASSET = 'Welcome to the store. Free shipping this week.'
BAD_ASSET = 'CORRUPTED BY DEPLOY 812'
DB_ID = 'lab-drill-db'
RESTORED_DB_ID = 'lab-drill-db-restored'
DB_SNAPSHOT = 'lab-drill-db-before-drill'
DB_USER = 'labadmin'
DB_PASSWORD = 'LabDrillPassw0rd!'
CONFIG_PARAMETER = '/lab-drill/app/config'
TARGETS_PARAMETER = '/lab-drill/recovery-targets'
RTO_MINUTES = 15
RPO_MINUTES = 5
ORDERS = [
    {'order_id': f'ord-{index:03d}', 'customer': customer, 'total': total}
    for index, (customer, total) in enumerate(
        [('ana', 42), ('ben', 18), ('chen', 75), ('dara', 9), ('eli', 120)], start=1)
]
LOST_ORDERS = ['ord-002', 'ord-004']
CHANGED_ORDER = 'ord-005'

COMMAND_TIMEOUT = 60
BOOT_TIMEOUT = 150
DB_WAIT_SECONDS = 240
FLOCI = 'How Floci emulates this: '

SETUP_SCRIPT = (
    f'mkdir -p {APP_DIR}\n'
    f'echo {RELEASE} > {APP_DIR}/VERSION\n'
    f'printf \'listen=8080\\nworkers=4\\n\' > {APP_DIR}/app.conf\n'
    f'echo "version=$(cat {APP_DIR}/VERSION) files=$(ls {APP_DIR} | wc -l)"'
)
CHECK_SCRIPT = f'echo "version=$(cat {APP_DIR}/VERSION 2>/dev/null) files=$(ls {APP_DIR} 2>/dev/null | wc -l)"'


# ── Pure helpers ─────────────────────────────────────────────────────────────

def parse_iso(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def minutes_between(start: Any, end: Any) -> float | None:
    first, second = parse_iso(start), parse_iso(end)
    if not first or not second:
        return None
    return round((second - first).total_seconds() / 60, 2)


def drill_report(incident: str, results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """RTO is incident to restored; RPO is recovery point to incident. Both must be inside the targets."""
    rows = {}
    for name, item in results.items():
        rto = minutes_between(incident, item.get('restoredAt'))
        rpo = minutes_between(item.get('recoveryPoint'), incident)
        rows[name] = {'rtoMinutes': rto, 'rpoMinutes': rpo,
                      'meetsRto': rto is not None and 0 <= rto <= RTO_MINUTES,
                      'meetsRpo': rpo is not None and 0 <= rpo <= RPO_MINUTES}
    return {'incident': incident, 'rtoTargetMinutes': RTO_MINUTES, 'rpoTargetMinutes': RPO_MINUTES,
            'resources': rows, 'passed': bool(rows) and all(row['meetsRto'] and row['meetsRpo']
                                                              for row in rows.values())}


def orders_match(items: list[dict[str, Any]]) -> bool:
    """The restored table holds exactly the orders from before the incident."""
    found = {item.get('order_id'): (item.get('customer'), item.get('total')) for item in items}
    return found == {order['order_id']: (order['customer'], order['total']) for order in ORDERS}


def ddb_item(order: dict[str, Any]) -> dict[str, Any]:
    return {'order_id': {'S': order['order_id']}, 'customer': {'S': order['customer']},
            'total': {'N': str(order['total'])}}


def plain_item(item: dict[str, Any]) -> dict[str, Any]:
    return {'order_id': item['order_id']['S'], 'customer': item.get('customer', {}).get('S'),
            'total': int(item['total']['N']) if 'total' in item else None}


def previous_good_version(versions: list[dict[str, Any]], bodies: dict[str, str]) -> dict[str, Any] | None:
    """The newest version older than the latest whose body is the good asset."""
    ordered = sorted(versions, key=lambda item: str(item.get('LastModified')), reverse=True)
    return next((item for item in ordered if not item.get('IsLatest') and bodies.get(item['VersionId']) == GOOD_ASSET),
                None)


def app_config(web: str, table: str, bucket: str, db: str) -> dict[str, str]:
    return {'webInstance': web, 'ordersTable': table, 'assetsBucket': bucket, 'database': db}


# ── Commands shown in the console ───────────────────────────────────────────

PREPARE_COMMAND = (
    f'aws ssm put-parameter --name {TARGETS_PARAMETER} --type String '
    f'--value \'{{"rtoMinutes":{RTO_MINUTES},"rpoMinutes":{RPO_MINUTES}}}\'\n'
    f'aws ec2 run-instances --image-id {IMAGE_ID} --instance-type {INSTANCE_TYPE} '
    f'--tag-specifications \'ResourceType=instance,Tags=[{{Key=Name,Value={WEB_NAME}}},{{Key=App,Value={APP}}}]\'\n'
    f'aws ssm send-command --document-name AWS-RunShellScript --parameters commands=["<install {RELEASE}>"] ...\n'
    f'aws dynamodb create-table --table-name {TABLE} --attribute-definitions AttributeName=order_id,AttributeType=S '
    '--key-schema AttributeName=order_id,KeyType=HASH --billing-mode PAY_PER_REQUEST\n'
    f'aws dynamodb batch-write-item --request-items file://orders.json   # {len(ORDERS)} orders\n'
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3 cp banner.txt s3://{BUCKET}/{ASSET_KEY}\n'
    f'aws rds create-db-instance --db-instance-identifier {DB_ID} --engine postgres --db-instance-class db.t3.micro '
    '--allocated-storage 20 --backup-retention-period 7 ...\n'
    f'aws ssm put-parameter --name {CONFIG_PARAMETER} --type String --value file://app-config.json'
)
PROTECT_COMMAND = (
    f'aws ec2 create-image --instance-id <web-id> --name {AMI_NAME} --no-reboot\n'
    f'aws dynamodb update-continuous-backups --table-name {TABLE} '
    '--point-in-time-recovery-specification PointInTimeRecoveryEnabled=true\n'
    f'aws s3api put-bucket-versioning --bucket {BUCKET} --versioning-configuration Status=Enabled\n'
    f'aws s3 cp banner.txt s3://{BUCKET}/{ASSET_KEY}   # first versioned copy\n'
    f'aws rds create-db-snapshot --db-instance-identifier {DB_ID} --db-snapshot-identifier {DB_SNAPSHOT}'
)
INCIDENT_COMMAND = (
    '# deploy 812 goes wrong\n'
    'aws ec2 terminate-instances --instance-ids <web-id>\n'
    f'aws dynamodb delete-item --table-name {TABLE} --key \'{{"order_id":{{"S":"{LOST_ORDERS[0]}"}}}}\'\n'
    f'aws dynamodb delete-item --table-name {TABLE} --key \'{{"order_id":{{"S":"{LOST_ORDERS[1]}"}}}}\'\n'
    f'aws dynamodb update-item --table-name {TABLE} --key \'{{"order_id":{{"S":"{CHANGED_ORDER}"}}}}\' '
    '--update-expression "SET #t = :zero" ...\n'
    f'aws s3 cp corrupted.txt s3://{BUCKET}/{ASSET_KEY}'
)
SERVER_COMMAND = (
    f'aws ec2 describe-images --owners self --filters Name=name,Values={AMI_NAME}\n'
    'aws ec2 run-instances --image-id <golden-ami> --instance-type t3.micro '
    f'--tag-specifications \'ResourceType=instance,Tags=[{{Key=Name,Value={RESTORED_WEB_NAME}}}]\'\n'
    f'aws ssm send-command ... commands=["cat {APP_DIR}/VERSION"]'
)
TABLE_COMMAND = (
    f'aws dynamodb describe-continuous-backups --table-name {TABLE}\n'
    f'aws dynamodb restore-table-to-point-in-time --source-table-name {TABLE} '
    f'--target-table-name {RESTORED_TABLE} --restore-date-time <one second before the incident>\n'
    f'aws dynamodb scan --table-name {RESTORED_TABLE}'
)
OBJECT_COMMAND = (
    f'aws s3api list-object-versions --bucket {BUCKET} --prefix {ASSET_KEY}\n'
    f'aws s3api copy-object --bucket {BUCKET} --key {ASSET_KEY} '
    f'--copy-source {BUCKET}/{ASSET_KEY}?versionId=<good-version>\n'
    f'aws s3 cp s3://{BUCKET}/{ASSET_KEY} -'
)
DATABASE_COMMAND = (
    f'aws rds describe-db-instances --db-instance-identifier {DB_ID} --query "DBInstances[0].LatestRestorableTime"\n'
    f'aws rds restore-db-instance-to-point-in-time --source-db-instance-identifier {DB_ID} '
    f'--target-db-instance-identifier {RESTORED_DB_ID} --restore-time <one second before the incident>\n'
    f'aws rds wait db-instance-available --db-instance-identifier {RESTORED_DB_ID}\n'
    f'# or from the snapshot: aws rds restore-db-instance-from-db-snapshot --db-snapshot-identifier {DB_SNAPSHOT} ...'
)
REPORT_COMMAND = (
    f'aws ssm put-parameter --name {CONFIG_PARAMETER} --overwrite '
    f'--value \'{{"webInstance":"<restored-id>","ordersTable":"{RESTORED_TABLE}","assetsBucket":"{BUCKET}",'
    f'"database":"{RESTORED_DB_ID}"}}\'\n'
    f'aws s3 cp drill-report.json s3://{BUCKET}/drills/<date>.json\n'
    '# with AWS Backup: aws backup start-restore-job ... and create-restore-testing-plan for scheduled drills'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Run a timed backup and restore drill across EC2, DynamoDB, S3, and RDS',
    'description': (
        'Set RTO and RPO targets for a web server, an orders table, an assets bucket and a database, take a '
        'recovery point of each (a golden AMI, point-in-time recovery, object versions, an RDS snapshot), lose the '
        'server and corrupt the data in an incident, restore each one and time it against the targets, and point the '
        'app at the restored copies.'
    ),
    'steps': [
        {
            'key': 'prepare-workload',
            'title': f'Set RTO {RTO_MINUTES} min and RPO {RPO_MINUTES} min for a four-part app',
            'command': PREPARE_COMMAND,
            'explanation': (
                f'The recovery time objective (RTO) is how long the app may be down: {RTO_MINUTES} minutes. The '
                f'recovery point objective (RPO) is how much data it may lose: {RPO_MINUTES} minutes. They are stored '
                f'in {TARGETS_PARAMETER}. The app is {WEB_NAME} running {RELEASE}, the {TABLE} table with '
                f'{len(ORDERS)} orders, the {BUCKET} bucket with a site banner, and the PostgreSQL database {DB_ID} '
                f'with 7 days of automated backups. {CONFIG_PARAMETER} tells the app which ones to use. '
                + FLOCI + 'all four resources are real: the instance runs the install script, and the database is a '
                'real PostgreSQL.'
            ),
        },
        {
            'key': 'protect',
            'title': 'Take a recovery point of each resource',
            'command': PROTECT_COMMAND,
            'explanation': (
                f'Each service has its own recovery point. {AMI_NAME} is an AMI of the configured server (no reboot, '
                'so the file system may not be consistent; stop the app first in production). Point-in-time recovery '
                f'on {TABLE} keeps 35 days of changes to the second. Versioning on {BUCKET} keeps every version of '
                f'every object. {DB_SNAPSHOT} is a manual snapshot, which stays until you delete it, while automated '
                'backups give point-in-time restore inside the retention period. AWS Backup can take all of these from '
                'one plan. '
                + FLOCI + 'the AMI captures the instance\'s files, and the PITR, versions and snapshot are real. '
                'Floci has no EBS snapshots of data volumes, so the app keeps its files on the root volume.'
            ),
        },
        {
            'key': 'incident',
            'title': 'Deploy 812 loses the server and corrupts the data',
            'command': INCIDENT_COMMAND,
            'explanation': (
                f'A bad deploy terminates {WEB_NAME}, deletes orders {" and ".join(LOST_ORDERS)}, zeroes the total of '
                f'{CHANGED_ORDER}, and overwrites the banner. The incident time is recorded: every RTO is measured '
                'from it, and every RPO back to the recovery point used. '
                + FLOCI + 'the termination, deletes and overwrite are real.'
            ),
        },
        {
            'key': 'restore-server',
            'title': f'Launch {RESTORED_WEB_NAME} from the golden AMI',
            'command': SERVER_COMMAND,
            'explanation': (
                f'A new instance from {AMI_NAME} comes up with {RELEASE} already installed, so recovery takes a boot, '
                'not a rebuild. Its RPO is the age of the AMI: changes made on the server after the image are lost, '
                'which is why images are rebuilt on every release (EC2 Image Builder or a pipeline) and data lives '
                'off the instance. '
                + FLOCI + f'the AMI holds the instance\'s files, and {RESTORED_WEB_NAME} has {APP_DIR}/VERSION.'
            ),
        },
        {
            'key': 'restore-table',
            'title': f'Restore {TABLE} to one second before the incident',
            'command': TABLE_COMMAND,
            'explanation': (
                f'Point-in-time recovery restores into a new table, {RESTORED_TABLE}, as it was one second before the '
                f'incident: all {len(ORDERS)} orders with their totals. A restore never overwrites the source, so you '
                'can compare the two before switching. The new table does not inherit PITR, alarms, auto scaling or '
                'tags; set them again. '
                + FLOCI + 'the restore returns the items as they were at that second.'
            ),
        },
        {
            'key': 'restore-object',
            'title': 'Recover the banner from its previous version',
            'command': OBJECT_COMMAND,
            'explanation': (
                'The overwrite became a new latest version; the good banner is still there as an older version. '
                'Copying that version onto the key makes it the latest again and keeps the bad one for the '
                'investigation. With versioning, a delete only adds a delete marker, which you remove the same way. '
                + FLOCI + 'versions and the copy are real.'
            ),
        },
        {
            'key': 'restore-database',
            'title': f'Restore {DB_ID} to one second before the incident',
            'command': DATABASE_COMMAND,
            'explanation': (
                'Automated backups and transaction logs let RDS restore to any second up to LatestRestorableTime, '
                f'usually within five minutes of now. The restore creates {RESTORED_DB_ID}, a new instance with a new '
                'endpoint; parameter and security groups need checking. A snapshot restore is the other choice, with '
                'the snapshot\'s age as its RPO. '
                + FLOCI + 'the restore starts a real PostgreSQL from the source\'s data in seconds; in AWS it takes '
                'minutes to tens of minutes, which is often the largest part of the RTO.'
            ),
        },
        {
            'key': 'repoint-and-report',
            'title': 'Point the app at the restored copies and check the RTO and RPO',
            'command': REPORT_COMMAND,
            'explanation': (
                f'{CONFIG_PARAMETER} now names the restored server, table and database. The drill report gives each '
                f'resource\'s RTO (incident to restored) and RPO (recovery point to incident) against {RTO_MINUTES} '
                f'and {RPO_MINUTES} minutes, and is saved in {BUCKET}. A miss is a finding, not a failure: an AMI '
                'or object version older than the RPO means taking them more often, and a slow restore means '
                'automating it. AWS Backup restore testing runs this kind of drill on a schedule and records the '
                'restore times. '
                + FLOCI + 'the parameter and report are real. Floci has no AWS Backup restore jobs or restore testing '
                'plans, so the lab restores through each service and keeps the timings itself.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _marker(step: str, value: Any) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
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


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Floci lookups ────────────────────────────────────────────────────────────

def _instances() -> dict[str, dict[str, Any]]:
    reservations = _safe_call(lambda: _client('ec2').describe_instances(Filters=[
        {'Name': 'tag:App', 'Values': [APP]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
    ])['Reservations'], [])
    found = {}
    for reservation in reservations:
        for instance in reservation.get('Instances', []):
            name = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Name'), None)
            if name in (WEB_NAME, RESTORED_WEB_NAME):
                found[name] = instance
    return found


def _image() -> dict[str, Any] | None:
    images = _safe_call(lambda: _client('ec2').describe_images(
        Owners=['self'], Filters=[{'Name': 'name', 'Values': [AMI_NAME]}])['Images'], [])
    live = [item for item in images if item.get('State') != 'deregistered']
    return live[0] if live else None


def _table(name: str) -> dict[str, Any] | None:
    return _safe_call(lambda: _client('dynamodb').describe_table(TableName=name)['Table'])


def _scan(name: str) -> list[dict[str, Any]]:
    items = _safe_call(lambda: _client('dynamodb').scan(TableName=name)['Items'], [])
    return [plain_item(item) for item in items]


def _pitr_on() -> bool:
    description = _safe_call(lambda: _client('dynamodb').describe_continuous_backups(TableName=TABLE)
                             ['ContinuousBackupsDescription'], {})
    return (description.get('PointInTimeRecoveryDescription') or {}).get('PointInTimeRecoveryStatus') == 'ENABLED'


def _bucket_exists() -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=BUCKET) is not None, False)


def _asset() -> str | None:
    return _safe_call(lambda: _client('s3').get_object(Bucket=BUCKET, Key=ASSET_KEY)['Body'].read().decode())


def _versions() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('s3').list_object_versions(Bucket=BUCKET, Prefix=ASSET_KEY)
                      .get('Versions', []), [])


def _db(identifier: str) -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('rds').describe_db_instances(DBInstanceIdentifier=identifier)
                       ['DBInstances'], [])
    return items[0] if items else None


def _snapshot() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('rds').describe_db_snapshots(DBSnapshotIdentifier=DB_SNAPSHOT)
                       ['DBSnapshots'], [])
    return items[0] if items else None


def _parameter(name: str) -> dict[str, Any] | None:
    value = _safe_call(lambda: _client('ssm').get_parameter(Name=name)['Parameter']['Value'])
    try:
        return json.loads(value) if value else None
    except ValueError:
        return None


def _run_shell(instance_id: str, script: str) -> dict[str, Any]:
    ssm = _client('ssm')
    command_id = ssm.send_command(InstanceIds=[instance_id], DocumentName='AWS-RunShellScript',
                                  Parameters={'commands': [script]}, Comment=LAB_KEY)['Command']['CommandId']
    outcome: dict[str, Any] = {}

    def finished() -> bool:
        try:
            outcome.update(ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id))
        except ClientError as exc:
            if _code(exc) == 'InvocationDoesNotExist':
                return False
            raise
        return outcome.get('Status') not in ('Pending', 'InProgress', 'Delayed')

    if not _wait(finished, timeout=COMMAND_TIMEOUT, delay=1):
        outcome['Status'] = 'TimedOut'
    return {'Status': outcome.get('Status'), 'Stdout': (outcome.get('StandardOutputContent') or '').strip()}


def _app_state(instance_id: str) -> dict[str, str]:
    run = _run_shell(instance_id, CHECK_SCRIPT)
    values = {}
    for part in run['Stdout'].split():
        name, _, value = part.partition('=')
        values[name] = value
    return values


def _ready(instance_id: str) -> bool:
    def running() -> bool:
        reservations = _safe_call(lambda: _client('ec2').describe_instances(InstanceIds=[instance_id])
                                  ['Reservations'], [])
        return [item['State']['Name'] for r in reservations for item in r['Instances']] == ['running']

    if not _wait(running, BOOT_TIMEOUT, delay=3):
        return False
    return _wait(lambda: _run_shell(instance_id, 'echo ready')['Stdout'] == 'ready', BOOT_TIMEOUT, delay=5)


def _db_available(identifier: str) -> bool:
    return _wait(lambda: (_db(identifier) or {}).get('DBInstanceStatus') == 'available', DB_WAIT_SECONDS, delay=5)


def _incident_time() -> str | None:
    return (_marked('incident') or {}).get('at')


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_prepare() -> tuple[bool, str]:
    targets = _parameter(TARGETS_PARAMETER) or {}
    if targets.get('rtoMinutes') != RTO_MINUTES or targets.get('rpoMinutes') != RPO_MINUTES:
        return False, f'Store the RTO and RPO targets in {TARGETS_PARAMETER}.'
    if not (_marked('prepare-workload') or {}).get('installed'):
        return False, f'{WEB_NAME} does not run {RELEASE} yet.'
    if not _table(TABLE):
        return False, f'Create {TABLE}.'
    if not _bucket_exists():
        return False, f'Create {BUCKET}.'
    if not _db(DB_ID):
        return False, f'Create {DB_ID}.'
    if not _parameter(CONFIG_PARAMETER):
        return False, f'Write {CONFIG_PARAMETER}.'
    return True, f'Targets RTO {RTO_MINUTES} and RPO {RPO_MINUTES} minutes; four resources and the app config exist.'


def _check_protect() -> tuple[bool, str]:
    image = _image()
    if not image or image.get('State') != 'available':
        return False, f'{AMI_NAME} is not available yet.'
    if not _pitr_on():
        return False, f'Turn on point-in-time recovery for {TABLE}.'
    versioning = _safe_call(lambda: _client('s3').get_bucket_versioning(Bucket=BUCKET).get('Status'))
    if versioning != 'Enabled':
        return False, f'Turn on versioning for {BUCKET}.'
    if (_snapshot() or {}).get('Status') != 'available':
        return False, f'{DB_SNAPSHOT} is not available yet.'
    return True, f'{AMI_NAME}, PITR on {TABLE}, versioning on {BUCKET}, and {DB_SNAPSHOT} are ready.'


def _check_incident() -> tuple[bool, str]:
    marker = _marked('incident') or {}
    if not marker.get('at'):
        return False, 'The incident has not happened yet.'
    if WEB_NAME in _instances():
        return False, f'{WEB_NAME} is still running.'
    if not marker.get('ordersAfter') or marker['ordersAfter'] != len(ORDERS) - len(LOST_ORDERS):
        return False, f'The incident should leave {len(ORDERS) - len(LOST_ORDERS)} orders.'
    if marker.get('assetAfter') != BAD_ASSET:
        return False, 'The banner was not overwritten.'
    return True, f'At {marker["at"]} the server was lost, two orders deleted, one changed, and the banner overwritten.'


def _check_server() -> tuple[bool, str]:
    marker = _marked('restore-server') or {}
    restored = _instances().get(RESTORED_WEB_NAME)
    image = _image() or {}
    if not restored or restored.get('ImageId') != image.get('ImageId'):
        return False, f'{RESTORED_WEB_NAME} is not running from {AMI_NAME}.'
    if (marker.get('state') or {}).get('version') != RELEASE:
        return False, f'{RESTORED_WEB_NAME} does not have {RELEASE}: {marker.get("state") or "not checked"}.'
    return True, f'{RESTORED_WEB_NAME} came up from {AMI_NAME} with {RELEASE} installed.'


def _check_table() -> tuple[bool, str]:
    if not _table(RESTORED_TABLE):
        return False, f'{RESTORED_TABLE} does not exist yet.'
    items = _scan(RESTORED_TABLE)
    if not orders_match(items):
        return False, f'{RESTORED_TABLE} has {len(items)} orders that do not match the ones before the incident.'
    return True, f'{RESTORED_TABLE} holds all {len(ORDERS)} orders as they were before the incident.'


def _check_object() -> tuple[bool, str]:
    if _asset() != GOOD_ASSET:
        return False, 'The banner is not the good version yet.'
    bodies = (_marked('restore-object') or {}).get('bodies') or {}
    if BAD_ASSET not in bodies.values():
        return False, 'Keep the corrupted version for the investigation.'
    return True, 'The banner is the good version again, and the corrupted one is kept as an older version.'


def _check_database() -> tuple[bool, str]:
    restored = _db(RESTORED_DB_ID)
    if not restored or restored.get('DBInstanceStatus') != 'available':
        return False, f'{RESTORED_DB_ID} is not available yet.'
    marker = _marked('restore-database') or {}
    incident = parse_iso(_incident_time())
    point = parse_iso(marker.get('restoreTime'))
    if not incident or not point or point >= incident:
        return False, f'{RESTORED_DB_ID} should be restored to a time before the incident.'
    return True, f'{RESTORED_DB_ID} is available, restored to {marker["restoreTime"]}.'


def _check_report() -> tuple[bool, str]:
    config = _parameter(CONFIG_PARAMETER) or {}
    restored = _instances().get(RESTORED_WEB_NAME) or {}
    expected = app_config(restored.get('InstanceId', ''), RESTORED_TABLE, BUCKET, RESTORED_DB_ID)
    if config != expected:
        return False, f'{CONFIG_PARAMETER} does not name the restored copies yet.'
    report = (_marked('repoint-and-report') or {}).get('report') or {}
    rows = report.get('resources') or {}
    if set(rows) != {'server', 'table', 'object', 'database'} or any(
            row['rtoMinutes'] is None or row['rpoMinutes'] is None for row in rows.values()):
        return False, 'The drill report does not time all four restores yet.'
    if report.get('passed'):
        return True, (f'The app uses the restored copies, and all four met RTO {RTO_MINUTES} and RPO {RPO_MINUTES} '
                      'minutes.')
    missed = ', '.join(f'{name} ({row["rtoMinutes"]} min down, {row["rpoMinutes"]} min lost)'
                       for name, row in rows.items() if not (row['meetsRto'] and row['meetsRpo']))
    return True, f'The app uses the restored copies. Missed the targets: {missed}; the report says where to speed up.'


CHECKS = {
    'prepare-workload': _check_prepare,
    'protect': _check_protect,
    'incident': _check_incident,
    'restore-server': _check_server,
    'restore-table': _check_table,
    'restore-object': _check_object,
    'restore-database': _check_database,
    'repoint-and-report': _check_report,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _tags(name: str) -> list[dict[str, Any]]:
    return [{'ResourceType': 'instance', 'Tags': [{'Key': 'Name', 'Value': name}, {'Key': 'App', 'Value': APP}]}]


def step_prepare_workload() -> dict[str, Any]:
    started = time.perf_counter()
    ssm, ec2, dynamodb, s3, rds = (_client(name) for name in ('ssm', 'ec2', 'dynamodb', 's3', 'rds'))
    ssm.put_parameter(Name=TARGETS_PARAMETER, Type='String', Overwrite=True,
                      Value=json.dumps({'rtoMinutes': RTO_MINUTES, 'rpoMinutes': RPO_MINUTES}))
    if not _db(DB_ID):
        rds.create_db_instance(
            DBInstanceIdentifier=DB_ID, Engine='postgres', DBInstanceClass='db.t3.micro', AllocatedStorage=20,
            MasterUsername=DB_USER, MasterUserPassword=DB_PASSWORD, BackupRetentionPeriod=7,
            Tags=[{'Key': 'App', 'Value': APP}])
    web = _instances().get(WEB_NAME)
    if not web and not _incident_time():
        web = ec2.run_instances(ImageId=IMAGE_ID, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
                                TagSpecifications=_tags(WEB_NAME))['Instances'][0]
    if not _table(TABLE):
        dynamodb.create_table(TableName=TABLE, BillingMode='PAY_PER_REQUEST',
                              AttributeDefinitions=[{'AttributeName': 'order_id', 'AttributeType': 'S'}],
                              KeySchema=[{'AttributeName': 'order_id', 'KeyType': 'HASH'}])
        _wait(lambda: (_table(TABLE) or {}).get('TableStatus') == 'ACTIVE', 30)
    if not _incident_time():
        dynamodb.batch_write_item(RequestItems={TABLE: [{'PutRequest': {'Item': ddb_item(order)}} for order in ORDERS]})
    if not _bucket_exists():
        s3.create_bucket(Bucket=BUCKET)
    if not _incident_time():
        s3.put_object(Bucket=BUCKET, Key=ASSET_KEY, Body=GOOD_ASSET.encode())
    state = {}
    if web and _ready(web['InstanceId']):
        run = _run_shell(web['InstanceId'], SETUP_SCRIPT)
        state = dict(part.partition('=')[::2] for part in run['Stdout'].split())
    _db_available(DB_ID)
    ssm.put_parameter(Name=CONFIG_PARAMETER, Type='String', Overwrite=True, Value=json.dumps(
        app_config((web or {}).get('InstanceId', ''), TABLE, BUCKET, DB_ID)))
    _marker('prepare-workload', {'installed': state.get('version') == RELEASE, 'state': state})
    verified, message = _check_prepare()
    return _result('prepare-workload', PREPARE_COMMAND, {
        'Targets': {'rtoMinutes': RTO_MINUTES, 'rpoMinutes': RPO_MINUTES}, 'Web': (web or {}).get('InstanceId'),
        'App': state, 'Orders': len(_scan(TABLE)), 'Database': (_db(DB_ID) or {}).get('DBInstanceStatus')},
        verified, message, started)


def step_protect() -> dict[str, Any]:
    started = time.perf_counter()
    ec2, dynamodb, s3, rds = (_client(name) for name in ('ec2', 'dynamodb', 's3', 'rds'))
    web = _instances().get(WEB_NAME)
    if not _image():
        if not web:
            return _result('protect', PROTECT_COMMAND, {}, False, f'Run prepare-workload first: {WEB_NAME} is gone.',
                           started)
        ec2.create_image(InstanceId=web['InstanceId'], Name=AMI_NAME, NoReboot=True,
                         Description=f'{WEB_NAME} with {RELEASE}')
    _wait(lambda: (_image() or {}).get('State') == 'available', 120, delay=3)
    dynamodb.update_continuous_backups(TableName=TABLE,
                                       PointInTimeRecoverySpecification={'PointInTimeRecoveryEnabled': True})
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    if not any(item.get('VersionId') not in (None, 'null') for item in _versions()):
        s3.put_object(Bucket=BUCKET, Key=ASSET_KEY, Body=GOOD_ASSET.encode())
    if not _snapshot():
        _db_available(DB_ID)
        rds.create_db_snapshot(DBInstanceIdentifier=DB_ID, DBSnapshotIdentifier=DB_SNAPSHOT)
    _wait(lambda: (_snapshot() or {}).get('Status') == 'available', 120, delay=3)
    image = _image() or {}
    _marker('protect', {'imageId': image.get('ImageId'), 'imageAt': image.get('CreationDate') or _now(),
                        'at': _now()})
    verified, message = _check_protect()
    return _result('protect', PROTECT_COMMAND, {
        'Image': {key: image.get(key) for key in ('ImageId', 'Name', 'State', 'CreationDate')},
        'PointInTimeRecovery': _pitr_on(), 'Versions': len(_versions()),
        'Snapshot': {key: (_snapshot() or {}).get(key) for key in ('DBSnapshotIdentifier', 'Status',
                                                                   'SnapshotCreateTime')}},
        verified, message, started)


def step_incident() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_protect()[0]:
        return _result('incident', INCIDENT_COMMAND, {}, False, 'Take the recovery points first (protect).', started)
    ec2, dynamodb, s3 = (_client(name) for name in ('ec2', 'dynamodb', 's3'))
    time.sleep(2)
    at = _now()
    time.sleep(2)
    web = _instances().get(WEB_NAME)
    if web:
        ec2.terminate_instances(InstanceIds=[web['InstanceId']])
    for order_id in LOST_ORDERS:
        dynamodb.delete_item(TableName=TABLE, Key={'order_id': {'S': order_id}})
    dynamodb.update_item(TableName=TABLE, Key={'order_id': {'S': CHANGED_ORDER}}, UpdateExpression='SET #t = :zero',
                         ExpressionAttributeNames={'#t': 'total'}, ExpressionAttributeValues={':zero': {'N': '0'}})
    s3.put_object(Bucket=BUCKET, Key=ASSET_KEY, Body=BAD_ASSET.encode())
    _wait(lambda: WEB_NAME not in _instances(), 60, delay=3)
    marker = {'at': at, 'ordersAfter': len(_scan(TABLE)), 'assetAfter': _asset()}
    _marker('incident', marker)
    verified, message = _check_incident()
    return _result('incident', INCIDENT_COMMAND, marker, verified, message, started)


def _restored(step: str, recovery_point: Any) -> dict[str, Any]:
    return {'restoredAt': _now(), 'recoveryPoint': str(recovery_point) if recovery_point else None}


def step_restore_server() -> dict[str, Any]:
    started = time.perf_counter()
    image = _image()
    if not image or not _incident_time():
        return _result('restore-server', SERVER_COMMAND, {}, False, 'Run protect and incident first.', started)
    restored = _instances().get(RESTORED_WEB_NAME)
    if not restored:
        restored = _client('ec2').run_instances(ImageId=image['ImageId'], InstanceType=INSTANCE_TYPE, MinCount=1,
                                                MaxCount=1, TagSpecifications=_tags(RESTORED_WEB_NAME))['Instances'][0]
    state = _app_state(restored['InstanceId']) if _ready(restored['InstanceId']) else {}
    timing = _restored('restore-server', (_marked('protect') or {}).get('imageAt'))
    _marker('restore-server', {'state': state, **timing})
    verified, message = _check_server()
    return _result('restore-server', SERVER_COMMAND, {'InstanceId': restored['InstanceId'],
                                                      'ImageId': image['ImageId'], 'App': state, **timing},
                   verified, message, started)


def step_restore_table() -> dict[str, Any]:
    started = time.perf_counter()
    incident = parse_iso(_incident_time())
    if not incident:
        return _result('restore-table', TABLE_COMMAND, {}, False, 'Run incident first.', started)
    point = incident - timedelta(seconds=1)
    if not _table(RESTORED_TABLE):
        _client('dynamodb').restore_table_to_point_in_time(SourceTableName=TABLE, TargetTableName=RESTORED_TABLE,
                                                           RestoreDateTime=point)
    _wait(lambda: (_table(RESTORED_TABLE) or {}).get('TableStatus') == 'ACTIVE', 60)
    items = _scan(RESTORED_TABLE)
    timing = _restored('restore-table', point.isoformat())
    _marker('restore-table', timing)
    verified, message = _check_table()
    return _result('restore-table', TABLE_COMMAND, {'RestoreDateTime': point.isoformat(), 'Items': items,
                                                    'SourceItems': _scan(TABLE), **timing},
                   verified, message, started)


def step_restore_object() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    versions = _versions()
    bodies = {item['VersionId']: _safe_call(lambda item=item: s3.get_object(
        Bucket=BUCKET, Key=ASSET_KEY, VersionId=item['VersionId'])['Body'].read().decode())
        for item in versions}
    good = previous_good_version(versions, bodies)
    if _asset() != GOOD_ASSET:
        if not good:
            return _result('restore-object', OBJECT_COMMAND, {'Versions': versions}, False,
                           'No earlier good version of the banner.', started)
        s3.copy_object(Bucket=BUCKET, Key=ASSET_KEY,
                       CopySource={'Bucket': BUCKET, 'Key': ASSET_KEY, 'VersionId': good['VersionId']})
    timing = _restored('restore-object', (good or {}).get('LastModified'))
    _marker('restore-object', {'bodies': bodies, **timing})
    verified, message = _check_object()
    return _result('restore-object', OBJECT_COMMAND, {
        'Versions': [{'VersionId': item['VersionId'], 'IsLatest': item.get('IsLatest'),
                      'LastModified': item.get('LastModified'), 'Body': bodies.get(item['VersionId'])}
                     for item in versions], 'Restored': _asset(), **timing}, verified, message, started)


def step_restore_database() -> dict[str, Any]:
    started = time.perf_counter()
    incident = parse_iso(_incident_time())
    if not incident:
        return _result('restore-database', DATABASE_COMMAND, {}, False, 'Run incident first.', started)
    source = _db(DB_ID) or {}
    latest = parse_iso(source.get('LatestRestorableTime'))
    point = incident - timedelta(seconds=1)
    if latest and latest < point:
        point = latest
    if not _db(RESTORED_DB_ID):
        _client('rds').restore_db_instance_to_point_in_time(
            SourceDBInstanceIdentifier=DB_ID, TargetDBInstanceIdentifier=RESTORED_DB_ID, RestoreTime=point,
            DBInstanceClass='db.t3.micro', Tags=[{'Key': 'App', 'Value': APP}])
    _db_available(RESTORED_DB_ID)
    timing = _restored('restore-database', point.isoformat())
    _marker('restore-database', {'restoreTime': point.isoformat(), **timing})
    restored = _db(RESTORED_DB_ID) or {}
    verified, message = _check_database()
    return _result('restore-database', DATABASE_COMMAND, {
        'LatestRestorableTime': source.get('LatestRestorableTime'), 'RestoreTime': point.isoformat(),
        'Restored': {key: restored.get(key) for key in ('DBInstanceIdentifier', 'DBInstanceStatus', 'Endpoint')},
        **timing}, verified, message, started)


def step_repoint_and_report() -> dict[str, Any]:
    started = time.perf_counter()
    incident = _incident_time()
    restored = _instances().get(RESTORED_WEB_NAME)
    if not incident or not restored:
        return _result('repoint-and-report', REPORT_COMMAND, {}, False, 'Run the restore steps first.', started)
    _client('ssm').put_parameter(Name=CONFIG_PARAMETER, Type='String', Overwrite=True, Value=json.dumps(
        app_config(restored['InstanceId'], RESTORED_TABLE, BUCKET, RESTORED_DB_ID)))
    results = {name: _marked(step) or {} for name, step in (
        ('server', 'restore-server'), ('table', 'restore-table'), ('object', 'restore-object'),
        ('database', 'restore-database'))}
    report = drill_report(incident, {name: {'restoredAt': item.get('restoredAt'),
                                            'recoveryPoint': item.get('recoveryPoint')}
                                     for name, item in results.items()})
    key = f'drills/{incident[:10]}.json'
    _client('s3').put_object(Bucket=BUCKET, Key=key, Body=json.dumps(report, indent=2).encode(),
                             ContentType='application/json')
    _marker('repoint-and-report', {'report': report, 'key': key})
    verified, message = _check_report()
    return _result('repoint-and-report', REPORT_COMMAND, {'Config': _parameter(CONFIG_PARAMETER), 'Report': report,
                                                          'SavedTo': f's3://{BUCKET}/{key}'},
                   verified, message, started)


RUNNERS = {
    'prepare-workload': step_prepare_workload,
    'protect': step_protect,
    'incident': step_incident,
    'restore-server': step_restore_server,
    'restore-table': step_restore_table,
    'restore-object': step_restore_object,
    'restore-database': step_restore_database,
    'repoint-and-report': step_repoint_and_report,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError, StopIteration):
        return False


def status() -> dict[str, Any]:
    steps = {}
    for step in STEP_KEYS:
        verified = _safe(CHECKS[step])
        steps[step] = {
            'verified': verified,
            'verification': {'status': 'passed', 'message': 'Verified against Floci.'} if verified else None,
        }
    passed = sum(1 for item in steps.values() if item['verified'])
    complete = passed == len(steps)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def reset() -> dict[str, Any]:
    """Remove the instances, AMI, tables, bucket and its versions, databases, snapshot, and parameters."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code in ('ResourceNotFoundException', 'ParameterNotFound'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    ec2 = _client('ec2')
    instances = [item['InstanceId'] for item in _instances().values()]
    if instances:
        attempt('instances:' + ','.join(instances), lambda: ec2.terminate_instances(InstanceIds=instances))
    image = _image()
    if image:
        attempt(f'image:{AMI_NAME}', lambda: ec2.deregister_image(ImageId=image['ImageId']))
    dynamodb = _client('dynamodb')
    for name in (TABLE, RESTORED_TABLE):
        if _table(name):
            attempt(f'table:{name}', lambda name=name: dynamodb.delete_table(TableName=name))
    s3 = _client('s3')
    if _bucket_exists():
        def empty_and_delete() -> None:
            listing = s3.list_object_versions(Bucket=BUCKET)
            for item in listing.get('Versions', []) + listing.get('DeleteMarkers', []):
                s3.delete_object(Bucket=BUCKET, Key=item['Key'], VersionId=item['VersionId'])
            for item in s3.list_objects_v2(Bucket=BUCKET).get('Contents', []):
                s3.delete_object(Bucket=BUCKET, Key=item['Key'])
            s3.delete_bucket(Bucket=BUCKET)
        attempt(f'bucket:{BUCKET}', empty_and_delete)
    rds = _client('rds')
    for identifier in (RESTORED_DB_ID, DB_ID):
        if _db(identifier):
            attempt(f'db:{identifier}', lambda identifier=identifier: rds.delete_db_instance(
                DBInstanceIdentifier=identifier, SkipFinalSnapshot=True, DeleteAutomatedBackups=True))
    if _snapshot():
        attempt(f'db-snapshot:{DB_SNAPSHOT}', lambda: rds.delete_db_snapshot(DBSnapshotIdentifier=DB_SNAPSHOT))
    ssm = _client('ssm')
    for name in (CONFIG_PARAMETER, TARGETS_PARAMETER):
        if _parameter(name) is not None:
            attempt(f'parameter:{name}', lambda name=name: ssm.delete_parameter(Name=name))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws ec2 terminate-instances --instance-ids <web-ids>\n'
            'aws ec2 deregister-image --image-id <golden-ami>\n'
            f'aws dynamodb delete-table --table-name {TABLE}\n'
            f'aws dynamodb delete-table --table-name {RESTORED_TABLE}\n'
            f'aws s3api delete-object --bucket {BUCKET} --key <key> --version-id <each version>\n'
            f'aws s3api delete-bucket --bucket {BUCKET}\n'
            f'aws rds delete-db-instance --db-instance-identifier {RESTORED_DB_ID} --skip-final-snapshot\n'
            f'aws rds delete-db-instance --db-instance-identifier {DB_ID} --skip-final-snapshot\n'
            f'aws rds delete-db-snapshot --db-snapshot-identifier {DB_SNAPSHOT}\n'
            f'aws ssm delete-parameter --name {CONFIG_PARAMETER}\n'
            f'aws ssm delete-parameter --name {TARGETS_PARAMETER}'
        ),
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
            'message': 'Some lab resources could not be removed: ' + '; '.join(errors) if errors
            else 'The instances, AMI, tables, bucket, databases, snapshot, and parameters are gone.',
        },
    }
