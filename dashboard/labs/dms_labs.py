"""Migration toolkit lab: an application portfolio sorted into the 7 Rs with the CAF perspectives that own each
decision, a DataSync task that copies a file share to S3 (and when a Snowball beats the network), a DMS replication
instance, source and target endpoints, a full load plus change data capture task, a cutover, and an Elastic
Beanstalk environment for the app that is replatformed.

Floci 2.3.0 runs the DMS, DataSync and Elastic Beanstalk control planes: replication instances are available at once,
tasks start and stop, an endpoint in use cannot be deleted, DataSync stores agents, locations and tasks, and Beanstalk
environments go Ready and Green at once. No data moves: DataSync task executions, DMS connection tests and table
statistics are unsupported, and any engine name is accepted, so the lab checks the settings and works the numbers out
itself and says so.
"""

from __future__ import annotations

import csv
import io
import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'dms'
LAB_KEY = 'migration-toolkit'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}

PORTFOLIO_BUCKET = 'lab-mig-portfolio'
PORTFOLIO_KEY = 'discovery/portfolio.csv'
LANDING_BUCKET = 'lab-mig-file-landing'

# A discovery inventory: (app, what it is, signals that drive the decision).
PORTFOLIO = [
    ('fax-gateway', 'Fax server nobody has used since 2023', 'unused'),
    ('mainframe-billing', 'COBOL billing on a mainframe under a 2-year contract', 'not-ready'),
    ('vmware-intranet', 'Intranet VMs on vSphere that must move this quarter', 'vmware'),
    ('inventory-api', 'Java API on Linux VMs; no time to change code', 'as-is'),
    ('legacy-crm', 'Self-hosted CRM with a SaaS equivalent', 'saas-available'),
    ('orders-db', 'MySQL on a VM; the team wants managed backups and patching', 'managed-db'),
    ('shop-web', 'Python web app; move to a managed platform without a rewrite', 'managed-platform'),
    ('reporting', 'Monolith that must scale 50x at month end', 'cloud-native'),
]
STRATEGIES = {
    'unused': ('Retire', 'Decommission it; nothing to migrate.'),
    'not-ready': ('Retain', 'Keep it on premises for now and revisit.'),
    'vmware': ('Relocate', 'Move the VMs as they are to VMware Cloud on AWS.'),
    'as-is': ('Rehost', 'Lift and shift to EC2, for example with AWS Application Migration Service.'),
    'saas-available': ('Repurchase', 'Drop and shop: move to the SaaS product.'),
    'managed-db': ('Replatform', 'Lift, tinker and shift: move to Amazon Aurora with AWS DMS.'),
    'managed-platform': ('Replatform', 'Move the code unchanged to Elastic Beanstalk.'),
    'cloud-native': ('Refactor', 'Re-architect with cloud-native services such as Lambda and SQS.'),
}
CAF_PERSPECTIVES = [
    ('Business', 'Ties the migration to business outcomes and the portfolio decisions.'),
    ('People', 'Training, culture and organizational change for the teams.'),
    ('Governance', 'Program management, cost and risk while the portfolio moves.'),
    ('Platform', 'The landing zone and architecture the workloads move onto.'),
    ('Security', 'Identity, data protection and compliance during and after the move.'),
    ('Operations', 'Running, monitoring and supporting the workloads once they are in AWS.'),
]

AGENT_NAME = 'lab-mig-datacenter-agent'
ACTIVATION_KEY = 'LABMG-DTSYN-C0AGT-2026A-KEY01'
NAS_HOST = 'nas01.corp.example'
NAS_EXPORT = '/exports/shared'
DATASYNC_ROLE = f'arn:aws:iam::{ACCOUNT}:role/lab-mig-datasync-s3'
DATASYNC_TASK = 'lab-mig-nas-to-s3'
TASK_OPTIONS = {'VerifyMode': 'ONLY_FILES_TRANSFERRED', 'OverwriteMode': 'ALWAYS', 'PreserveDeletedFiles': 'PRESERVE',
                'BytesPerSecond': 100 * 1024 * 1024}
SHARE_TB = 120
LINK_GBPS = 1.0
LINK_UTILIZATION = 0.8
SNOWBALL_DAYS = 7  # order, ship, copy and ship back, roughly a week

SUBNET_GROUP = 'lab-mig-dms-subnets'
REPLICATION_INSTANCE = 'lab-mig-dms-instance'
INSTANCE_CLASS = 'dms.t3.medium'
SOURCE_ENDPOINT = 'lab-mig-onprem-mysql'
TARGET_ENDPOINT = 'lab-mig-aurora-mysql'
SOURCE = {'EngineName': 'mysql', 'ServerName': 'mysql01.corp.example', 'Port': 3306, 'Username': 'dms_reader',
          'Password': 'LabMigration-2026!'}
TARGET = {'EngineName': 'aurora', 'ServerName': 'orders.cluster-lab.us-east-1.rds.amazonaws.com', 'Port': 3306,
          'Username': 'dms_writer', 'Password': 'LabMigration-2026!'}
TASK_ID = 'lab-mig-orders-full-load-cdc'
TABLE_MAPPINGS = {'rules': [
    {'rule-type': 'selection', 'rule-id': '1', 'rule-name': 'include-shop', 'rule-action': 'include',
     'object-locator': {'schema-name': 'shop', 'table-name': '%'}},
    {'rule-type': 'selection', 'rule-id': '2', 'rule-name': 'skip-audit-log', 'rule-action': 'exclude',
     'object-locator': {'schema-name': 'shop', 'table-name': 'audit_log'}},
    {'rule-type': 'transformation', 'rule-id': '3', 'rule-name': 'rename-schema', 'rule-action': 'rename',
     'rule-target': 'schema', 'object-locator': {'schema-name': 'shop'}, 'value': 'orders'},
]}
SOURCE_TABLES = ['customers', 'orders', 'order_items', 'products', 'audit_log']

# Heterogeneous moves need the schema converted first (AWS SCT or DMS Schema Conversion); homogeneous ones do not.
ENGINE_FAMILIES = {'mysql': 'mysql', 'aurora': 'mysql', 'mariadb': 'mysql', 'postgres': 'postgres',
                   'aurora-postgresql': 'postgres', 'oracle': 'oracle', 'sqlserver': 'sqlserver'}

EB_APP = 'lab-mig-shop-web'
EB_ENV = 'lab-mig-shop-web-prod'
EB_PLATFORM_HINT = 'Python'

PORTFOLIO_COMMAND = (
    f'aws s3api create-bucket --bucket {PORTFOLIO_BUCKET}\n'
    f'aws s3 cp portfolio.csv s3://{PORTFOLIO_BUCKET}/{PORTFOLIO_KEY}\n'
    '# Assign each application one of the 7 Rs, and name the CAF perspective that owns each decision'
)
DATASYNC_COMMAND = (
    f'aws datasync create-agent --activation-key {ACTIVATION_KEY} --agent-name {AGENT_NAME}\n'
    f'aws datasync create-location-nfs --server-hostname {NAS_HOST} --subdirectory {NAS_EXPORT} '
    '--on-prem-config AgentArns=<agent-arn>\n'
    f'aws s3api create-bucket --bucket {LANDING_BUCKET}\n'
    f'aws datasync create-location-s3 --s3-bucket-arn arn:aws:s3:::{LANDING_BUCKET} '
    f'--s3-config BucketAccessRoleArn={DATASYNC_ROLE} --s3-storage-class STANDARD\n'
    f'aws datasync create-task --name {DATASYNC_TASK} --source-location-arn <nfs-arn> --destination-location-arn <s3-arn> '
    '--options VerifyMode=ONLY_FILES_TRANSFERRED,OverwriteMode=ALWAYS,BytesPerSecond=104857600\n'
    'aws datasync start-task-execution --task-arn <task-arn>'
)
INSTANCE_COMMAND = (
    f'aws dms create-replication-subnet-group --replication-subnet-group-identifier {SUBNET_GROUP} '
    '--replication-subnet-group-description "DMS in two AZs" --subnet-ids <subnet-a> <subnet-b>\n'
    f'aws dms create-replication-instance --replication-instance-identifier {REPLICATION_INSTANCE} '
    f'--replication-instance-class {INSTANCE_CLASS} --allocated-storage 50 --replication-subnet-group-identifier '
    f'{SUBNET_GROUP} --no-publicly-accessible'
)
ENDPOINTS_COMMAND = (
    f'aws dms create-endpoint --endpoint-identifier {SOURCE_ENDPOINT} --endpoint-type source --engine-name mysql '
    f'--server-name {SOURCE["ServerName"]} --port 3306 --username dms_reader --password <password>\n'
    f'aws dms create-endpoint --endpoint-identifier {TARGET_ENDPOINT} --endpoint-type target --engine-name aurora '
    f'--server-name {TARGET["ServerName"]} --port 3306 --username dms_writer --password <password>\n'
    'aws dms test-connection --replication-instance-arn <instance-arn> --endpoint-arn <endpoint-arn>'
)
TASK_COMMAND = (
    f'aws dms create-replication-task --replication-task-identifier {TASK_ID} --migration-type full-load-and-cdc '
    '--source-endpoint-arn <source-arn> --target-endpoint-arn <target-arn> --replication-instance-arn <instance-arn> '
    '--table-mappings file://table-mappings.json\n'
    'aws dms start-replication-task --replication-task-arn <task-arn> --start-replication-task-type start-replication\n'
    'aws dms describe-table-statistics --replication-task-arn <task-arn>'
)
CUTOVER_COMMAND = (
    '# After CDC latency reaches zero: stop writes on the source, let the last changes apply, then switch the app\n'
    'aws dms stop-replication-task --replication-task-arn <task-arn>\n'
    'aws dms delete-endpoint --endpoint-arn <source-arn>   # refused while a task still uses it'
)
BEANSTALK_COMMAND = (
    f'aws elasticbeanstalk create-application --application-name {EB_APP}\n'
    'aws elasticbeanstalk list-available-solution-stacks\n'
    f'aws elasticbeanstalk create-environment --application-name {EB_APP} --environment-name {EB_ENV} '
    '--solution-stack-name "<Amazon Linux 2023 ... running Python ...>"'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Plan and run a migration with the 7 Rs, DataSync, AWS DMS, and Elastic Beanstalk',
    'description': (
        'Sort an application portfolio into the 7 migration strategies and name the Cloud Adoption Framework '
        'perspective behind each decision, copy a file share to S3 with DataSync (and see when a Snowball is '
        'faster), migrate a MySQL database to Aurora with a DMS full load and change data capture, cut over, and '
        'replatform a web app onto Elastic Beanstalk.'
    ),
    'steps': [
        {
            'key': 'portfolio-7rs',
            'title': 'Sort the application portfolio into the 7 Rs',
            'command': PORTFOLIO_COMMAND,
            'explanation': (
                'Migrations start with discovery: list every application and decide its strategy. The 7 Rs are '
                'retire, retain, relocate, rehost (lift and shift), repurchase (move to SaaS), replatform (lift, '
                'tinker and shift) and refactor (re-architect). The AWS Cloud Adoption Framework (CAF) groups the '
                'work into six perspectives: business, people and governance on the business side, platform, '
                'security and operations on the technical side. How Floci emulates this: the inventory is a real S3 '
                'object; the step sorts it.'
            ),
        },
        {
            'key': 'datasync-file-share',
            'title': 'Copy an on-premises file share to S3 with DataSync',
            'command': DATASYNC_COMMAND,
            'explanation': (
                'AWS DataSync uses an agent next to the file server to copy NFS or SMB shares to S3, EFS or FSx over '
                'the network, with integrity checks and a bandwidth limit. For data sets that would take too long '
                'on the wire, AWS Snowball Edge devices are shipped, filled and shipped back. How Floci emulates '
                'this: the agent, locations and task are stored, but task executions are unsupported and no files '
                'move, so the step works out network time against a Snowball itself.'
            ),
        },
        {
            'key': 'dms-replication-instance',
            'title': 'Create a DMS replication instance in two AZs',
            'command': INSTANCE_COMMAND,
            'explanation': (
                'AWS Database Migration Service runs migrations on a replication instance placed in your VPC '
                'through a subnet group. The source stays fully operational during the migration, which keeps '
                'downtime to the cutover. How Floci emulates this: the subnet group and instance are stored and the '
                'instance is available at once.'
            ),
        },
        {
            'key': 'dms-endpoints',
            'title': 'Define the on-premises source and the Aurora target',
            'command': ENDPOINTS_COMMAND,
            'explanation': (
                'Endpoints hold connection details for the source and the target. MySQL to Aurora MySQL is a '
                'homogeneous migration, so DMS can copy the schema and data as they are; a heterogeneous one, such '
                'as Oracle to Aurora PostgreSQL, needs the schema converted first with AWS SCT or DMS Schema '
                'Conversion. How Floci emulates this: endpoints are stored, any engine name is accepted, and '
                'connection tests are unsupported, so the step checks the engines itself.'
            ),
        },
        {
            'key': 'dms-full-load-cdc',
            'title': 'Start a full load with change data capture',
            'command': TASK_COMMAND,
            'explanation': (
                'A full-load-and-cdc task copies the existing rows, then keeps applying changes from the source\'s '
                'binary log until you cut over. Table mappings choose what moves: here every table in shop except '
                'audit_log, renamed to the orders schema on the target. How Floci emulates this: the task starts '
                'and reports running, but no rows move and table statistics are unsupported, so the step applies '
                'the mappings to the source tables itself.'
            ),
        },
        {
            'key': 'cutover',
            'title': 'Cut over and stop replication',
            'command': CUTOVER_COMMAND,
            'explanation': (
                'Cut over when CDC latency is near zero: stop writes on the source, let the last changes apply, '
                'point the application at Aurora, then stop the task. DMS will not delete an endpoint a task still '
                'uses. How Floci emulates this: the task stops and the in-use endpoint is protected, as on AWS.'
            ),
        },
        {
            'key': 'replatform-beanstalk',
            'title': 'Replatform the web app onto Elastic Beanstalk',
            'command': BEANSTALK_COMMAND,
            'explanation': (
                'Replatforming keeps the code but moves it onto a managed platform. Elastic Beanstalk provisions '
                'the EC2 instances, load balancer, Auto Scaling and health monitoring for a supported platform such '
                'as Python, and patches the platform for you with managed updates. How Floci emulates this: the '
                'application and environment are stored and the environment reports Ready and Green at once; no '
                'instances run, and a terminated environment\'s name is never freed, so after a reset the lab adds '
                'a number to the name.'
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


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _not_found(exc: ClientError) -> bool:
    code = _code(exc)
    return code in ('ResourceNotFoundFault', 'InvalidRequestException', 'NoSuchBucket', '404', 'NotFound') or \
        'NotFound' in code


# ── Pure logic ───────────────────────────────────────────────────────────────

def portfolio_csv(rows: list[tuple[str, str, str]] = PORTFOLIO) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(['app', 'description', 'signal'])
    writer.writerows(rows)
    return buffer.getvalue()


def assign_strategies(text: str) -> list[dict[str, str]]:
    plan = []
    for row in csv.DictReader(io.StringIO(text)):
        strategy, why = STRATEGIES.get(row['signal'], ('Unknown', 'Collect more discovery data.'))
        plan.append({'App': row['app'], 'Strategy': strategy, 'Why': why})
    return plan


def transfer_days(terabytes: float, gbps: float = LINK_GBPS, utilization: float = LINK_UTILIZATION) -> float:
    seconds = terabytes * 1e12 * 8 / (gbps * 1e9 * utilization)
    return round(seconds / 86400, 1)


def transfer_choice(terabytes: float, gbps: float = LINK_GBPS) -> dict[str, Any]:
    days = transfer_days(terabytes, gbps)
    return {'DataTB': terabytes, 'LinkGbps': gbps, 'NetworkDays': days, 'SnowballDays': SNOWBALL_DAYS,
            'Choice': 'AWS Snowball Edge' if days > SNOWBALL_DAYS else 'AWS DataSync over the network'}


def migration_kind(source_engine: str, target_engine: str) -> str:
    source, target = ENGINE_FAMILIES.get(source_engine), ENGINE_FAMILIES.get(target_engine)
    if not source or not target:
        return 'unknown'
    return 'homogeneous' if source == target else 'heterogeneous'


def mapped_tables(tables: list[str], mappings: dict[str, Any] = TABLE_MAPPINGS, schema: str = 'shop') -> list[dict[str, str]]:
    """Apply DMS selection and schema rename rules to a list of tables in one schema."""

    def matches(locator: dict[str, str], table: str) -> bool:
        if locator.get('schema-name') not in (schema, '%'):
            return False
        pattern = locator.get('table-name', '%')
        if pattern == '%':
            return True
        if pattern.endswith('%'):
            return table.startswith(pattern[:-1])
        return table == pattern

    rules = mappings.get('rules', [])
    included = [table for table in tables
                if any(rule['rule-action'] == 'include' and matches(rule['object-locator'], table)
                       for rule in rules if rule['rule-type'] == 'selection')
                and not any(rule['rule-action'] == 'exclude' and matches(rule['object-locator'], table)
                            for rule in rules if rule['rule-type'] == 'selection')]
    target_schema = schema
    for rule in rules:
        if rule['rule-type'] == 'transformation' and rule.get('rule-target') == 'schema' and \
                rule.get('rule-action') == 'rename' and rule['object-locator'].get('schema-name') == schema:
            target_schema = rule['value']
    return [{'Source': f'{schema}.{table}', 'Target': f'{target_schema}.{table}'} for table in included]


# ── Lookups ──────────────────────────────────────────────────────────────────

def _object_exists(bucket: str, key: str) -> bool:
    try:
        _client('s3').head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def _bucket_exists(bucket: str) -> bool:
    try:
        _client('s3').head_bucket(Bucket=bucket)
        return True
    except ClientError:
        return False


def _agent() -> dict[str, Any] | None:
    agents = _client('datasync').list_agents().get('Agents', [])
    return next((item for item in agents if item.get('Name') == AGENT_NAME), None)


def _locations() -> list[dict[str, Any]]:
    return _client('datasync').list_locations().get('Locations', [])


def _nfs_location() -> str | None:
    for item in _locations():
        uri = item.get('LocationUri', '')
        if uri.startswith('nfs://') and NAS_HOST in uri:
            return item['LocationArn']
    return None


def _s3_location() -> str | None:
    for item in _locations():
        uri = item.get('LocationUri', '')
        if uri.startswith('s3://') and LANDING_BUCKET in uri:
            return item['LocationArn']
    return None


def _datasync_task() -> dict[str, Any] | None:
    datasync = _client('datasync')
    for item in datasync.list_tasks().get('Tasks', []):
        if item.get('Name') == DATASYNC_TASK:
            return datasync.describe_task(TaskArn=item['TaskArn'])
    return None


def _subnet_group() -> dict[str, Any] | None:
    try:
        groups = _client('dms').describe_replication_subnet_groups(Filters=[
            {'Name': 'replication-subnet-group-id', 'Values': [SUBNET_GROUP]}]).get('ReplicationSubnetGroups', [])
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    return next((item for item in groups if item.get('ReplicationSubnetGroupIdentifier') == SUBNET_GROUP), None)


def _instance() -> dict[str, Any] | None:
    try:
        items = _client('dms').describe_replication_instances(Filters=[
            {'Name': 'replication-instance-id', 'Values': [REPLICATION_INSTANCE]}]).get('ReplicationInstances', [])
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    return next((item for item in items if item.get('ReplicationInstanceIdentifier') == REPLICATION_INSTANCE), None)


def _endpoint(identifier: str) -> dict[str, Any] | None:
    try:
        items = _client('dms').describe_endpoints(Filters=[
            {'Name': 'endpoint-id', 'Values': [identifier]}]).get('Endpoints', [])
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    return next((item for item in items if item.get('EndpointIdentifier') == identifier), None)


def _task() -> dict[str, Any] | None:
    try:
        items = _client('dms').describe_replication_tasks(Filters=[
            {'Name': 'replication-task-id', 'Values': [TASK_ID]}]).get('ReplicationTasks', [])
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    return next((item for item in items if item.get('ReplicationTaskIdentifier') == TASK_ID), None)


def _eb_environment() -> dict[str, Any] | None:
    environments = _client('elasticbeanstalk').describe_environments(ApplicationName=EB_APP).get('Environments', [])
    live = [item for item in environments if item.get('Status') not in ('Terminated', 'Terminating')
            and (item.get('EnvironmentName') or '').startswith(EB_ENV)]
    return live[0] if live else None


def _create_environment(stack: str) -> str:
    """Create the environment. Floci 2.3.0 never frees the name of a terminated environment, so after a reset the
    lab adds a numeric suffix (AWS lets you reuse the name once termination finishes)."""
    eb = _client('elasticbeanstalk')
    for attempt in range(1, 21):
        name = EB_ENV if attempt == 1 else f'{EB_ENV}-{attempt}'
        try:
            eb.create_environment(ApplicationName=EB_APP, EnvironmentName=name, SolutionStackName=stack, Tags=[LAB_TAG])
            return name
        except ClientError as exc:
            if 'already exists' not in (exc.response.get('Error', {}).get('Message') or ''):
                raise
    raise RuntimeError(f'No free environment name for {EB_ENV}; restart Floci to clear terminated environments.')


def _eb_application() -> bool:
    apps = _client('elasticbeanstalk').describe_applications(ApplicationNames=[EB_APP]).get('Applications', [])
    return bool(apps)


def _python_stack() -> str | None:
    stacks = _client('elasticbeanstalk').list_available_solution_stacks().get('SolutionStacks', [])
    return next((stack for stack in stacks if f'running {EB_PLATFORM_HINT}' in stack), None)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_portfolio() -> tuple[bool, str]:
    if not _object_exists(PORTFOLIO_BUCKET, PORTFOLIO_KEY):
        return False, f'Upload the portfolio to s3://{PORTFOLIO_BUCKET}/{PORTFOLIO_KEY}.'
    marker = _marked('portfolio-7rs')
    if not marker:
        return False, 'Sort the portfolio into the 7 Rs.'
    return True, marker['message']


def _check_datasync() -> tuple[bool, str]:
    agent = _agent()
    if not agent:
        return False, f'Create the DataSync agent {AGENT_NAME}.'
    nfs, s3 = _nfs_location(), _s3_location()
    if not nfs or not s3:
        return False, f'Create the NFS location for {NAS_HOST} and the S3 location for {LANDING_BUCKET}.'
    task = _datasync_task()
    if not task or task.get('SourceLocationArn') != nfs or task.get('DestinationLocationArn') != s3:
        return False, f'Create the {DATASYNC_TASK} task from the NFS share to the S3 bucket.'
    verify = (task.get('Options') or {}).get('VerifyMode')
    if verify not in (None, 'ONLY_FILES_TRANSFERRED', 'POINT_IN_TIME_CONSISTENT'):
        return False, f'{DATASYNC_TASK} should verify the data it transfers.'
    marker = _marked('datasync-file-share')
    if not marker:
        return False, 'Run the step to compare the network with a Snowball.'
    return True, marker['message']


def _check_instance() -> tuple[bool, str]:
    if not _subnet_group():
        return False, f'Create the {SUBNET_GROUP} replication subnet group.'
    instance = _instance()
    if not instance:
        return False, f'Create the {REPLICATION_INSTANCE} replication instance.'
    if instance.get('ReplicationInstanceStatus') != 'available':
        return False, f'{REPLICATION_INSTANCE} is {instance.get("ReplicationInstanceStatus")}; wait until it is available.'
    if instance.get('ReplicationInstanceClass') != INSTANCE_CLASS:
        return False, f'{REPLICATION_INSTANCE} should be a {INSTANCE_CLASS}.'
    group = (instance.get('ReplicationSubnetGroup') or {}).get('ReplicationSubnetGroupIdentifier')
    if group != SUBNET_GROUP:
        return False, f'{REPLICATION_INSTANCE} should use the {SUBNET_GROUP} subnet group (it uses {group}).'
    if instance.get('PubliclyAccessible'):
        return False, f'{REPLICATION_INSTANCE} should not be publicly accessible.'
    return True, f'{REPLICATION_INSTANCE} ({INSTANCE_CLASS}) is available in {SUBNET_GROUP}, private to the VPC.'


def _check_endpoints() -> tuple[bool, str]:
    source, target = _endpoint(SOURCE_ENDPOINT), _endpoint(TARGET_ENDPOINT)
    if not source or not target:
        return False, f'Create the {SOURCE_ENDPOINT} source and {TARGET_ENDPOINT} target endpoints.'
    if (source.get('EndpointType') or '').upper() != 'SOURCE' or (target.get('EndpointType') or '').upper() != 'TARGET':
        return False, f'{SOURCE_ENDPOINT} must be a source and {TARGET_ENDPOINT} a target.'
    kind = migration_kind(source.get('EngineName', ''), target.get('EngineName', ''))
    if kind != 'homogeneous':
        return False, (f'{source.get("EngineName")} to {target.get("EngineName")} is {kind}; this lab moves MySQL to '
                       'Aurora MySQL.')
    return True, (f'{SOURCE_ENDPOINT} (mysql) to {TARGET_ENDPOINT} (aurora) is a homogeneous migration, so no schema '
                  'conversion is needed.')


def _check_task_settings(task: dict[str, Any]) -> str | None:
    instance, source, target = _instance(), _endpoint(SOURCE_ENDPOINT), _endpoint(TARGET_ENDPOINT)
    if not instance or not source or not target:
        return 'Create the replication instance and endpoints first.'
    if task.get('MigrationType') != 'full-load-and-cdc':
        return f'{TASK_ID} should be full-load-and-cdc (it is {task.get("MigrationType")}).'
    if task.get('SourceEndpointArn') != source['EndpointArn'] or task.get('TargetEndpointArn') != target['EndpointArn'] \
            or task.get('ReplicationInstanceArn') != instance['ReplicationInstanceArn']:
        return f'{TASK_ID} should use {SOURCE_ENDPOINT}, {TARGET_ENDPOINT} and {REPLICATION_INSTANCE}.'
    try:
        mappings = json.loads(task.get('TableMappings') or '{}')
    except json.JSONDecodeError:
        return f'{TASK_ID} has unreadable table mappings.'
    if mapped_tables(SOURCE_TABLES, mappings) != mapped_tables(SOURCE_TABLES):
        return f'{TASK_ID} should include shop.* except audit_log and rename the schema to orders.'
    return None


def _check_task() -> tuple[bool, str]:
    task = _task()
    if not task:
        return False, f'Create the {TASK_ID} replication task.'
    problem = _check_task_settings(task)
    if problem:
        return False, problem
    marker = _marked('dms-full-load-cdc')
    if not marker:
        return False, f'Start {TASK_ID}.'
    return True, marker['message']


def _check_cutover() -> tuple[bool, str]:
    task = _task()
    if not task:
        return False, f'Create and start {TASK_ID} first.'
    if task.get('Status') != 'stopped':
        return False, f'{TASK_ID} is {task.get("Status")}; stop it at cutover.'
    marker = _marked('cutover')
    if not marker:
        return False, 'Run the cutover step.'
    return True, marker['message']


def _check_beanstalk() -> tuple[bool, str]:
    if not _eb_application():
        return False, f'Create the {EB_APP} application.'
    environment = _eb_environment()
    if not environment:
        return False, f'Create the {EB_ENV} environment.'
    if EB_PLATFORM_HINT not in (environment.get('SolutionStackName') or ''):
        return False, f'{EB_ENV} should run a {EB_PLATFORM_HINT} platform.'
    if environment.get('Status') != 'Ready' or environment.get('Health') != 'Green':
        return False, f'{EB_ENV} is {environment.get("Status")} and {environment.get("Health")}; wait for Ready and Green.'
    return True, (f'{EB_ENV} runs {environment["SolutionStackName"]} at {environment.get("CNAME")}: same code, '
                  'managed platform.')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_portfolio() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    if not _bucket_exists(PORTFOLIO_BUCKET):
        s3.create_bucket(Bucket=PORTFOLIO_BUCKET)
    s3.put_bucket_tagging(Bucket=PORTFOLIO_BUCKET, Tagging={'TagSet': [LAB_TAG]})
    s3.put_object(Bucket=PORTFOLIO_BUCKET, Key=PORTFOLIO_KEY, Body=portfolio_csv().encode(), ContentType='text/csv')
    text = s3.get_object(Bucket=PORTFOLIO_BUCKET, Key=PORTFOLIO_KEY)['Body'].read().decode()
    plan = assign_strategies(text)
    used = sorted({row['Strategy'] for row in plan})
    message = (f'{len(plan)} applications sorted into {len(used)} of the 7 Rs ({", ".join(used)}); '
               'nothing is left without a strategy.')
    if any(row['Strategy'] == 'Unknown' for row in plan):
        message = 'Some applications have no strategy yet.'
    else:
        _marker('portfolio-7rs', {'message': message})
    response = {'Plan': plan, 'CafPerspectives': [{'Perspective': name, 'Focus': focus} for name, focus in CAF_PERSPECTIVES]}
    verified, check = _check_portfolio()
    return _result('portfolio-7rs', PORTFOLIO_COMMAND, response, verified, check, started)


def step_datasync() -> dict[str, Any]:
    started = time.perf_counter()
    datasync, s3 = _client('datasync'), _client('s3')
    agent = _agent()
    if not agent:
        agent_arn = datasync.create_agent(ActivationKey=ACTIVATION_KEY, AgentName=AGENT_NAME,
                                          Tags=[LAB_TAG])['AgentArn']
    else:
        agent_arn = agent['AgentArn']
    nfs = _nfs_location() or datasync.create_location_nfs(
        ServerHostname=NAS_HOST, Subdirectory=NAS_EXPORT, OnPremConfig={'AgentArns': [agent_arn]},
        Tags=[LAB_TAG])['LocationArn']
    if not _bucket_exists(LANDING_BUCKET):
        s3.create_bucket(Bucket=LANDING_BUCKET)
    destination = _s3_location() or datasync.create_location_s3(
        S3BucketArn=f'arn:aws:s3:::{LANDING_BUCKET}', S3StorageClass='STANDARD',
        S3Config={'BucketAccessRoleArn': DATASYNC_ROLE}, Tags=[LAB_TAG])['LocationArn']
    task = _datasync_task()
    if not task:
        datasync.create_task(SourceLocationArn=nfs, DestinationLocationArn=destination, Name=DATASYNC_TASK,
                             Options=TASK_OPTIONS, Tags=[LAB_TAG])
        task = _datasync_task() or {}
    try:
        execution: Any = datasync.start_task_execution(TaskArn=task['TaskArn'])
        execution.pop('ResponseMetadata', None)
    except ClientError as exc:
        execution = {'Outcome': 'unsupported', 'Code': _code(exc),
                     'Note': 'Floci does not run DataSync task executions, so no files are copied.'}
    choices = [transfer_choice(1), transfer_choice(SHARE_TB)]
    big = choices[-1]
    message = (f'{DATASYNC_TASK} copies {NAS_HOST}:{NAS_EXPORT} to {LANDING_BUCKET} with verification. A 1 TB share '
               f'takes {choices[0]["NetworkDays"]} days on a {LINK_GBPS:g} Gbps link, so DataSync fits; the full '
               f'{SHARE_TB} TB would take {big["NetworkDays"]} days, so ship it on Snowball Edge (about '
               f'{SNOWBALL_DAYS} days) and sync the changes with DataSync afterwards.')
    _marker('datasync-file-share', {'message': message})
    response = {'Task': {key: task.get(key) for key in ('TaskArn', 'Name', 'SourceLocationArn',
                                                         'DestinationLocationArn', 'Options', 'Status')},
                'Execution': execution, 'TransferChoices': choices}
    verified, check = _check_datasync()
    return _result('datasync-file-share', DATASYNC_COMMAND, response, verified, check, started)


def step_replication_instance() -> dict[str, Any]:
    started = time.perf_counter()
    dms, ec2 = _client('dms'), _client('ec2')
    if not _subnet_group():
        subnets = ec2.describe_subnets(Filters=[{'Name': 'default-for-az', 'Values': ['true']}]).get('Subnets', [])
        subnet_ids = [item['SubnetId'] for item in sorted(subnets, key=lambda item: item['AvailabilityZone'])][:2]
        dms.create_replication_subnet_group(
            ReplicationSubnetGroupIdentifier=SUBNET_GROUP, SubnetIds=subnet_ids, Tags=[LAB_TAG],
            ReplicationSubnetGroupDescription='DMS replication in two AZs (migration lab)')
    if not _instance():
        dms.create_replication_instance(
            ReplicationInstanceIdentifier=REPLICATION_INSTANCE, ReplicationInstanceClass=INSTANCE_CLASS,
            AllocatedStorage=50, ReplicationSubnetGroupIdentifier=SUBNET_GROUP, PubliclyAccessible=False,
            Tags=[LAB_TAG])
    _wait(lambda: (_instance() or {}).get('ReplicationInstanceStatus') == 'available', timeout=60)
    instance = _instance() or {}
    group = _subnet_group() or {}
    response = {
        'ReplicationInstance': {key: instance.get(key) for key in (
            'ReplicationInstanceIdentifier', 'ReplicationInstanceClass', 'ReplicationInstanceStatus',
            'AllocatedStorage', 'AvailabilityZone', 'PubliclyAccessible', 'MultiAZ')},
        'SubnetGroup': {'Id': group.get('ReplicationSubnetGroupIdentifier'), 'VpcId': group.get('VpcId'),
                        'AZs': sorted({(item.get('SubnetAvailabilityZone') or {}).get('Name')
                                       for item in group.get('Subnets', [])})},
    }
    verified, message = _check_instance()
    return _result('dms-replication-instance', INSTANCE_COMMAND, response, verified, message, started)


def step_endpoints() -> dict[str, Any]:
    started = time.perf_counter()
    dms = _client('dms')
    if not _endpoint(SOURCE_ENDPOINT):
        dms.create_endpoint(EndpointIdentifier=SOURCE_ENDPOINT, EndpointType='source', Tags=[LAB_TAG], **SOURCE)
    if not _endpoint(TARGET_ENDPOINT):
        dms.create_endpoint(EndpointIdentifier=TARGET_ENDPOINT, EndpointType='target', Tags=[LAB_TAG], **TARGET)
    source, target, instance = _endpoint(SOURCE_ENDPOINT) or {}, _endpoint(TARGET_ENDPOINT) or {}, _instance()
    test: Any
    if instance and source:
        try:
            test = dms.test_connection(ReplicationInstanceArn=instance['ReplicationInstanceArn'],
                                       EndpointArn=source['EndpointArn'])
            test.pop('ResponseMetadata', None)
        except ClientError as exc:
            test = {'Outcome': 'unsupported', 'Code': _code(exc),
                    'Note': 'Floci cannot test endpoint connections; on AWS the test must succeed before a task runs.'}
    else:
        test = {'Outcome': 'skipped', 'Note': 'Create the replication instance first.'}
    examples = [
        {'Move': 'MySQL to Aurora MySQL', 'Kind': migration_kind('mysql', 'aurora'), 'SchemaConversion': 'Not needed'},
        {'Move': 'Oracle to Aurora PostgreSQL', 'Kind': migration_kind('oracle', 'aurora-postgresql'),
         'SchemaConversion': 'AWS SCT or DMS Schema Conversion first'},
        {'Move': 'SQL Server to Aurora MySQL', 'Kind': migration_kind('sqlserver', 'aurora'),
         'SchemaConversion': 'AWS SCT or DMS Schema Conversion first'},
    ]
    response = {'Source': {key: source.get(key) for key in ('EndpointIdentifier', 'EndpointType', 'EngineName', 'ServerName', 'Port')},
                'Target': {key: target.get(key) for key in ('EndpointIdentifier', 'EndpointType', 'EngineName', 'ServerName', 'Port')},
                'ConnectionTest': test, 'HomogeneousOrNot': examples}
    verified, message = _check_endpoints()
    return _result('dms-endpoints', ENDPOINTS_COMMAND, response, verified, message, started)


def step_full_load_cdc() -> dict[str, Any]:
    started = time.perf_counter()
    dms = _client('dms')
    instance, source, target = _instance(), _endpoint(SOURCE_ENDPOINT), _endpoint(TARGET_ENDPOINT)
    if not instance or not source or not target:
        return _result('dms-full-load-cdc', TASK_COMMAND, {}, False,
                       'Create the replication instance and both endpoints first.', started)
    task = _task()
    if task and _check_task_settings(task):
        if task.get('Status') == 'running':
            dms.stop_replication_task(ReplicationTaskArn=task['ReplicationTaskArn'])
        dms.delete_replication_task(ReplicationTaskArn=task['ReplicationTaskArn'])
        _wait(lambda: not _task(), timeout=30)
        task = None
    if not task:
        dms.create_replication_task(
            ReplicationTaskIdentifier=TASK_ID, MigrationType='full-load-and-cdc',
            SourceEndpointArn=source['EndpointArn'], TargetEndpointArn=target['EndpointArn'],
            ReplicationInstanceArn=instance['ReplicationInstanceArn'], TableMappings=json.dumps(TABLE_MAPPINGS),
            Tags=[LAB_TAG])
        _wait(lambda: (_task() or {}).get('Status') in ('ready', 'stopped', 'running'), timeout=30)
        task = _task() or {}
    if task.get('Status') != 'running':
        start_type = 'start-replication' if task.get('Status') == 'ready' else 'resume-processing'
        dms.start_replication_task(ReplicationTaskArn=task['ReplicationTaskArn'], StartReplicationTaskType=start_type)
        _wait(lambda: (_task() or {}).get('Status') == 'running', timeout=30)
    task = _task() or {}
    try:
        stats: Any = dms.describe_table_statistics(ReplicationTaskArn=task['ReplicationTaskArn']).get('TableStatistics', [])
    except ClientError as exc:
        stats = {'Outcome': 'unsupported', 'Code': _code(exc),
                 'Note': 'Floci keeps no table statistics; the step applies the table mappings itself.'}
    tables = mapped_tables(SOURCE_TABLES)
    response = {'Task': {key: task.get(key) for key in ('ReplicationTaskIdentifier', 'MigrationType', 'Status')},
                'TableMappings': TABLE_MAPPINGS, 'TablesThatMove': tables,
                'Skipped': ['shop.audit_log'], 'TableStatistics': stats}
    if task.get('Status') == 'running' and not _check_task_settings(task):
        _marker('dms-full-load-cdc', {'message': (
            f'{TASK_ID} is running: {len(tables)} tables move from shop to orders (audit_log is skipped), first as a '
            'full load, then as ongoing changes until cutover.')})
    verified, message = _check_task()
    return _result('dms-full-load-cdc', TASK_COMMAND, response, verified, message, started)


def step_cutover() -> dict[str, Any]:
    started = time.perf_counter()
    dms = _client('dms')
    task, source = _task(), _endpoint(SOURCE_ENDPOINT)
    if not task or not source or not _marked('dms-full-load-cdc'):
        return _result('cutover', CUTOVER_COMMAND, {}, False, f'Start {TASK_ID} first.', started)
    if task.get('Status') == 'running':
        dms.stop_replication_task(ReplicationTaskArn=task['ReplicationTaskArn'])
        _wait(lambda: (_task() or {}).get('Status') == 'stopped', timeout=30)
    try:
        dms.delete_endpoint(EndpointArn=source['EndpointArn'])
        protection: dict[str, Any] = {'Outcome': 'deleted', 'Note': 'The source endpoint was deleted; recreate it.'}
    except ClientError as exc:
        protection = {'Outcome': 'refused', 'Code': _code(exc), 'Message': exc.response.get('Error', {}).get('Message')}
    runbook = [
        'Watch CDC latency until it is near zero',
        'Stop writes on the source database (maintenance page or read-only mode)',
        'Let the last changes apply, then validate row counts',
        'Point the application at the Aurora endpoint',
        'Stop the replication task; keep the source read-only until you are sure',
    ]
    response = {'Task': {key: (_task() or {}).get(key) for key in ('ReplicationTaskIdentifier', 'Status')},
                'DeleteSourceEndpoint': protection, 'Runbook': runbook}
    if (_task() or {}).get('Status') == 'stopped' and protection['Outcome'] == 'refused':
        _marker('cutover', {'message': (f'{TASK_ID} is stopped after cutover, and DMS refused to delete '
                                        f'{SOURCE_ENDPOINT} while the task still uses it.')})
    verified, message = _check_cutover()
    return _result('cutover', CUTOVER_COMMAND, response, verified, message, started)


def step_beanstalk() -> dict[str, Any]:
    started = time.perf_counter()
    eb = _client('elasticbeanstalk')
    if not _eb_application():
        eb.create_application(ApplicationName=EB_APP, Description='Shop web app, replatformed (migration lab)',
                              Tags=[LAB_TAG])
    stack = _python_stack()
    if not stack:
        return _result('replatform-beanstalk', BEANSTALK_COMMAND, {}, False,
                       f'No {EB_PLATFORM_HINT} solution stack is available.', started)
    if not _eb_environment():
        _create_environment(stack)
    _wait(lambda: (_eb_environment() or {}).get('Status') == 'Ready', timeout=60, delay=2)
    environment = _eb_environment() or {}
    response = {'Environment': {key: environment.get(key) for key in (
        'EnvironmentName', 'SolutionStackName', 'Status', 'Health', 'CNAME', 'Tier')},
        'BeanstalkManages': ['EC2 instances', 'Load balancer', 'Auto Scaling', 'Health monitoring',
                             'Managed platform updates'],
        'YouManage': ['Application code', 'Environment configuration', 'Data']}
    verified, message = _check_beanstalk()
    return _result('replatform-beanstalk', BEANSTALK_COMMAND, response, verified, message, started)


RUNNERS = {
    'portfolio-7rs': step_portfolio,
    'datasync-file-share': step_datasync,
    'dms-replication-instance': step_replication_instance,
    'dms-endpoints': step_endpoints,
    'dms-full-load-cdc': step_full_load_cdc,
    'cutover': step_cutover,
    'replatform-beanstalk': step_beanstalk,
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError, TypeError):
        return False


CHECKS = {
    'portfolio-7rs': _check_portfolio,
    'datasync-file-share': _check_datasync,
    'dms-replication-instance': _check_instance,
    'dms-endpoints': _check_endpoints,
    'dms-full-load-cdc': _check_task,
    'cutover': _check_cutover,
    'replatform-beanstalk': _check_beanstalk,
}


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
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


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Terminate the Beanstalk environment and app, delete the DMS task, endpoints, instance and subnet group, the
    DataSync task, locations and agent, and both buckets."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            message = exc.response.get('Error', {}).get('Message') or ''
            if _not_found(exc) or code.startswith('NoSuch') or 'No Environment' in message or 'No Application' in message:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    def lookup(call, default=None):
        try:
            return call()
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'lookup: {exc}')
            return default

    eb, dms, datasync, s3 = (_client(name) for name in ('elasticbeanstalk', 'dms', 'datasync', 's3'))
    environment = lookup(_eb_environment)
    if environment:
        attempt(f'environment:{environment["EnvironmentName"]}', lambda: eb.terminate_environment(
            EnvironmentName=environment['EnvironmentName']))
    if lookup(_eb_application, False):
        attempt(f'application:{EB_APP}', lambda: eb.delete_application(ApplicationName=EB_APP, TerminateEnvByForce=True))

    task = lookup(_task)
    if task:
        if task.get('Status') == 'running':
            attempt(f'stop-task:{TASK_ID}', lambda: dms.stop_replication_task(ReplicationTaskArn=task['ReplicationTaskArn']))
        attempt(f'task:{TASK_ID}', lambda: dms.delete_replication_task(ReplicationTaskArn=task['ReplicationTaskArn']))
        _wait(lambda: not _task(), timeout=30)
    for identifier in (SOURCE_ENDPOINT, TARGET_ENDPOINT):
        endpoint = lookup(lambda identifier=identifier: _endpoint(identifier))
        if endpoint:
            attempt(f'endpoint:{identifier}', lambda endpoint=endpoint: dms.delete_endpoint(EndpointArn=endpoint['EndpointArn']))
    instance = lookup(_instance)
    if instance:
        attempt(f'replication-instance:{REPLICATION_INSTANCE}', lambda: dms.delete_replication_instance(
            ReplicationInstanceArn=instance['ReplicationInstanceArn']))
        _wait(lambda: not _instance(), timeout=60)
    if lookup(_subnet_group):
        attempt(f'subnet-group:{SUBNET_GROUP}', lambda: dms.delete_replication_subnet_group(
            ReplicationSubnetGroupIdentifier=SUBNET_GROUP))

    datasync_task = lookup(_datasync_task)
    if datasync_task:
        attempt(f'datasync-task:{DATASYNC_TASK}', lambda: datasync.delete_task(TaskArn=datasync_task['TaskArn']))
    for arn in (lookup(_nfs_location), lookup(_s3_location)):
        if arn:
            attempt(f'location:{arn.rsplit("/", 1)[-1]}', lambda arn=arn: datasync.delete_location(LocationArn=arn))
    agent = lookup(_agent)
    if agent:
        attempt(f'agent:{AGENT_NAME}', lambda: datasync.delete_agent(AgentArn=agent['AgentArn']))

    for bucket in (PORTFOLIO_BUCKET, LANDING_BUCKET):
        if _bucket_exists(bucket):
            try:
                for item in s3.list_objects_v2(Bucket=bucket).get('Contents', []):
                    s3.delete_object(Bucket=bucket, Key=item['Key'])
            except ClientError as exc:
                errors.append(f'objects:{bucket}: {exc}')
            attempt(f'bucket:{bucket}', lambda bucket=bucket: s3.delete_bucket(Bucket=bucket))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws elasticbeanstalk terminate-environment --environment-name {EB_ENV} && '
            f'aws elasticbeanstalk delete-application --application-name {EB_APP}\n'
            'aws dms delete-replication-task --replication-task-arn <task-arn>\n'
            'aws dms delete-endpoint --endpoint-arn <source-arn> && aws dms delete-endpoint --endpoint-arn <target-arn>\n'
            'aws dms delete-replication-instance --replication-instance-arn <instance-arn>\n'
            f'aws dms delete-replication-subnet-group --replication-subnet-group-identifier {SUBNET_GROUP}\n'
            'aws datasync delete-task --task-arn <task-arn> && aws datasync delete-location --location-arn <arn> '
            '&& aws datasync delete-agent --agent-arn <agent-arn>\n'
            f'aws s3 rb s3://{PORTFOLIO_BUCKET} --force && aws s3 rb s3://{LANDING_BUCKET} --force'
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
            else 'The Beanstalk environment, DMS task, endpoints and instance, DataSync task and agent, and buckets are gone.',
        },
    }
