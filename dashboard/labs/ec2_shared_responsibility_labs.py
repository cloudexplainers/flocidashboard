"""Shared responsibility lab: the same small app's pieces on EC2, RDS, Lambda and S3, with who patches, encrypts,
backs up and controls access on each, then a responsibility matrix built from what was deployed and a sort of common
tasks into AWS, customer and shared.

Floci 2.3.0 runs the EC2 instance as a real container whose guest OS SSM Run Command can read, runs the Lambda function
in a real AWS-provided runtime image, starts a real PostgreSQL container for RDS and stores the maintenance window,
backup and encryption settings, and enforces S3 Block Public Access. It has no RDS maintenance actions, SSM patch
baselines cannot be created, and an AWS-RunPatchBaseline command never leaves Pending, so the lab reads the guest OS
itself and says so.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ec2'
LAB_KEY = 'shared-responsibility'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}

WEB_SG = 'lab-srm-web-sg'
WEB_INSTANCE = 'lab-srm-web'
IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't3.micro'
SSM_TIMEOUT_SECONDS = 60

DB_ID = 'lab-srm-orders-db'
DB_SETTINGS = {
    'Engine': 'postgres',
    'DBInstanceClass': 'db.t3.micro',
    'AllocatedStorage': 20,
    'MasterUsername': 'orders_admin',
    'PreferredMaintenanceWindow': 'sun:03:00-sun:04:00',
    'PreferredBackupWindow': '01:00-02:00',
    'AutoMinorVersionUpgrade': True,
    'BackupRetentionPeriod': 7,
    'StorageEncrypted': True,
    'PubliclyAccessible': False,
    'MultiAZ': True,
}
DB_PASSWORD = 'LabSharedResp-2026!'

FUNCTION = 'lab-srm-receipts'
ROLE_NAME = 'lab-srm-receipts-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
ROLE_POLICY = 'write-own-logs'
RUNTIME = 'python3.12'
HANDLER = 'receipts.handler'
LAMBDA_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
LOGS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
        'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/lambda/{FUNCTION}:*',
    }],
}
RECEIPTS_SOURCE = '''import os
import platform
import sys


def handler(event, context):
    # Everything below the handler (OS, Python, the runtime client) belongs to AWS.
    return {
        "receipt": f"order {event.get('order_id')} total {event.get('total')}",
        "execution_env": os.environ.get("AWS_EXECUTION_ENV"),
        "python": sys.version.split()[0],
        "os": platform.platform(),
    }
'''

DATA_BUCKET = 'lab-srm-customer-data'
DATA_KEY = 'orders/2026-10-25.csv'
DATA_BODY = b'order_id,customer,total\n1001,ana,42.50\n1002,ben,17.25\n'
TLS_ONLY_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Sid': 'DenyInsecureTransport',
        'Effect': 'Deny',
        'Principal': '*',
        'Action': 's3:*',
        'Resource': [f'arn:aws:s3:::{DATA_BUCKET}', f'arn:aws:s3:::{DATA_BUCKET}/*'],
        'Condition': {'Bool': {'aws:SecureTransport': 'false'}},
    }],
}

# Who owns each layer, by service model. "AWS" = security of the cloud; "Customer" = security in the cloud.
LAYERS = [
    'Physical data centers, hardware, and the global network',
    'Virtualization layer (hypervisor)',
    'Guest operating system patches',
    'Database engine or language runtime patches',
    'Network traffic rules (security groups, Block Public Access)',
    'Application code',
    'Identity and access management (IAM)',
    'Data classification and encryption choices',
    'Backups: when they run and how long they are kept',
]

TASKS = [
    ('Replace a failed disk in an AWS data center', 'AWS', 'Physical infrastructure is security of the cloud.'),
    ('Patch the Ubuntu packages on an EC2 web server', 'Customer', 'On EC2 (IaaS) the guest OS is yours.'),
    ('Apply a PostgreSQL minor version to an RDS instance', 'AWS',
     'AWS patches the RDS engine and OS, in the maintenance window you choose (or you apply it sooner).'),
    ('Pick the RDS maintenance window and backup retention', 'Customer',
     'You configure the managed service; AWS carries the work out.'),
    ('Update the Python runtime under a Lambda function', 'AWS',
     'Lambda runtimes are AWS-managed; you only move to a newer runtime version when one is deprecated.'),
    ('Fix a bug in the Lambda function code', 'Customer', 'Your code is always yours.'),
    ('Decide which S3 objects hold personal data and encrypt them', 'Customer',
     'Data classification and encryption choices are always yours.'),
    ('Grant an IAM role only the permissions it needs', 'Customer', 'IAM is always the customer\'s.'),
    ('Train staff on secure practices', 'Shared',
     'Awareness and training is a shared control: AWS trains its employees, customers train theirs.'),
    ('Patch management across the stack', 'Shared',
     'A shared control: AWS patches its infrastructure and managed services, customers patch guest OSes and apps.'),
    ('Configuration management', 'Shared',
     'A shared control: AWS configures its infrastructure, customers configure their OSes, databases and apps.'),
    ('Isolate one customer\'s instances from another\'s on the same host', 'AWS', 'Hypervisor isolation is AWS\'s.'),
]

SG_COMMAND = (
    f'aws ec2 create-security-group --group-name {WEB_SG} --description "HTTPS only" --vpc-id <default-vpc>\n'
    f'aws ec2 authorize-security-group-ingress --group-id <sg-id> --protocol tcp --port 443 --cidr 0.0.0.0/0\n'
    'aws ec2 describe-security-groups --group-ids <sg-id>'
)
EC2_COMMAND = (
    f'aws ec2 run-instances --image-id {IMAGE_ID} --instance-type {INSTANCE_TYPE} --security-group-ids <sg-id> '
    f'--tag-specifications "ResourceType=instance,Tags=[{{Key=Name,Value={WEB_INSTANCE}}}]"\n'
    'aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript '
    '--parameters \'commands=[". /etc/os-release; echo os=$PRETTY_NAME; echo packages=$(dpkg -l | grep -c ^ii)"]\'\n'
    'aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunPatchBaseline '
    '--parameters Operation=Scan'
)
RDS_COMMAND = (
    f'aws rds create-db-instance --db-instance-identifier {DB_ID} --engine postgres --db-instance-class db.t3.micro '
    '--allocated-storage 20 --master-username orders_admin --master-user-password <password> '
    '--preferred-maintenance-window sun:03:00-sun:04:00 --preferred-backup-window 01:00-02:00 '
    '--auto-minor-version-upgrade --backup-retention-period 7 --storage-encrypted --no-publicly-accessible --multi-az\n'
    'aws rds describe-pending-maintenance-actions'
)
LAMBDA_COMMAND = (
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} --policy-document file://logs.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime {RUNTIME} --handler {HANDLER} '
    f'--role {ROLE_ARN} --zip-file fileb://receipts.zip\n'
    f'aws lambda invoke --function-name {FUNCTION} --payload \'{{"order_id": 1001, "total": 42.5}}\' out.json'
)
S3_COMMAND = (
    f'aws s3api create-bucket --bucket {DATA_BUCKET}\n'
    f'aws s3api put-public-access-block --bucket {DATA_BUCKET} --public-access-block-configuration '
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true\n'
    f'aws s3api put-bucket-encryption --bucket {DATA_BUCKET} --server-side-encryption-configuration '
    '\'{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"},"BucketKeyEnabled":true}]}\'\n'
    f'aws s3api put-bucket-policy --bucket {DATA_BUCKET} --policy file://tls-only.json\n'
    f'aws s3api put-object --bucket {DATA_BUCKET} --key {DATA_KEY} --body orders.csv'
)
MATRIX_COMMAND = (
    'aws ec2 describe-instances --filters Name=tag:Lab,Values=shared-responsibility\n'
    f'aws rds describe-db-instances --db-instance-identifier {DB_ID}\n'
    f'aws lambda get-function-configuration --function-name {FUNCTION}\n'
    f'aws s3api get-public-access-block --bucket {DATA_BUCKET}\n'
    '# Build a who-owns-what matrix for EC2, RDS, Lambda and S3 from these settings'
)
TASKS_COMMAND = '# Sort twelve common tasks into AWS, customer, or shared responsibility'

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'See the shared responsibility model on EC2, RDS, Lambda, and S3',
    'description': (
        'Run the pieces of one small app on EC2, RDS, Lambda and S3, and see where AWS\'s job ends and yours begins '
        'on each: a firewall and a guest OS you patch on EC2, an engine AWS patches in the window you pick on RDS, a '
        'runtime AWS runs on Lambda, and data you protect on S3. Then build the responsibility matrix from what you '
        'deployed and sort common tasks into AWS, customer, and shared.'
    ),
    'steps': [
        {
            'key': 'ec2-security-group',
            'title': 'Open only HTTPS with a security group you control',
            'command': SG_COMMAND,
            'explanation': (
                'Security groups are stateful, instance-level firewalls, and on every service that uses them the '
                'rules are yours: AWS enforces them, but it never decides what to open. The web server allows only '
                'TCP 443 and no SSH, so it is managed through SSM instead. How Floci emulates this: security '
                'groups and their rules are stored as on AWS.'
            ),
        },
        {
            'key': 'ec2-guest-os',
            'title': 'Launch an EC2 instance and inspect the guest OS you must patch',
            'command': EC2_COMMAND,
            'explanation': (
                'EC2 is infrastructure as a service: AWS runs the hardware and hypervisor, and everything from the '
                'guest operating system up is yours to patch, harden and monitor. SSM Run Command reads the OS '
                'version and package count without opening SSH; AWS-RunPatchBaseline with Patch Manager is how a '
                'fleet gets patched. How Floci emulates this: the instance is a real Ubuntu container and Run '
                'Command works, but patch baselines cannot be created and AWS-RunPatchBaseline stays Pending, so the '
                'step reports the scan as not run.'
            ),
        },
        {
            'key': 'rds-managed-engine',
            'title': 'Create an RDS database where AWS patches the engine in your window',
            'command': RDS_COMMAND,
            'explanation': (
                'RDS is a managed service: AWS patches the OS and the PostgreSQL engine, takes the backups, and '
                'fails over to the standby in another AZ, and you get no shell on the host. You still choose when: '
                'the weekly maintenance window, automatic minor version upgrades, the backup window and retention, '
                'encryption at rest, and whether the database is reachable from the internet. How Floci emulates '
                'this: a real PostgreSQL container runs and the settings are stored, but there is no standby and '
                'pending maintenance actions are unsupported.'
            ),
        },
        {
            'key': 'lambda-managed-runtime',
            'title': 'Run a Lambda function whose OS and runtime AWS manages',
            'command': LAMBDA_COMMAND,
            'explanation': (
                'With Lambda (serverless), AWS runs and patches the servers, the OS and the language runtime; the '
                'function reports the runtime it found, which you never installed. You still own the code, its '
                'dependencies, the execution role (here only permission to write its own logs) and the data it '
                'handles. How Floci emulates this: the function runs in the AWS-provided python3.12 runtime image.'
            ),
        },
        {
            'key': 's3-customer-data',
            'title': 'Protect your data in S3 with Block Public Access, encryption, and TLS only',
            'command': S3_COMMAND,
            'explanation': (
                'AWS keeps S3 durable and available, but what is in the bucket and who can read it is always the '
                'customer\'s: turn on Block Public Access, encrypt by default (SSE-S3 or SSE-KMS), and deny requests '
                'that are not over TLS. How Floci emulates this: Block Public Access, default encryption and the '
                'bucket policy are stored, and Block Public Access is enforced.'
            ),
        },
        {
            'key': 'responsibility-matrix',
            'title': 'Build the responsibility matrix from what you deployed',
            'command': MATRIX_COMMAND,
            'explanation': (
                'The line between AWS and customer moves with the service model: the more managed the service, the '
                'more AWS takes on. Data, IAM and encryption choices stay with the customer everywhere. How Floci '
                'emulates this: the step reads the four resources back from Floci and fills in who owns each layer '
                'for each of them.'
            ),
        },
        {
            'key': 'who-does-what',
            'title': 'Sort common tasks into AWS, customer, and shared',
            'command': TASKS_COMMAND,
            'explanation': (
                'Exam questions describe a task and ask who is responsible. Patch management, configuration '
                'management, and awareness and training are shared controls: each side does them for its own '
                'layers. Inherited controls (physical and environmental) come entirely from AWS. How Floci emulates '
                'this: nothing to emulate; the step sorts the tasks and explains each one.'
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


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _tags(name: str, resource_type: str) -> list[dict[str, Any]]:
    return [{'ResourceType': resource_type, 'Tags': [{'Key': 'Name', 'Value': name}, LAB_TAG]}]


# ── Pure logic ───────────────────────────────────────────────────────────────

def parse_os_report(stdout: str) -> dict[str, str]:
    report = {}
    for line in stdout.splitlines():
        if '=' in line:
            key, value = line.split('=', 1)
            report[key.strip()] = value.strip()
    return report


def rds_customer_choices(db: dict[str, Any]) -> list[str]:
    """Return which of the customer's RDS choices differ from the lab's settings (empty when all are in place)."""
    problems = []
    for key in ('PreferredMaintenanceWindow', 'PreferredBackupWindow', 'AutoMinorVersionUpgrade',
                'BackupRetentionPeriod', 'StorageEncrypted', 'PubliclyAccessible', 'MultiAZ'):
        if db.get(key) != DB_SETTINGS[key]:
            problems.append(f'{key} is {db.get(key)!r}, expected {DB_SETTINGS[key]!r}')
    return problems


def bucket_problems(public_access: dict[str, Any] | None, encryption: list[dict[str, Any]] | None,
                    policy: dict[str, Any] | None) -> list[str]:
    problems = []
    if not public_access or not all(public_access.get(key) for key in (
            'BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')):
        problems.append('Block Public Access is not fully on')
    algorithms = [((rule.get('ApplyServerSideEncryptionByDefault') or {}).get('SSEAlgorithm')) for rule in encryption or []]
    if not any(algorithm in ('AES256', 'aws:kms', 'aws:kms:dsse') for algorithm in algorithms):
        problems.append('default encryption is off')
    statements = (policy or {}).get('Statement', [])
    if not any(item.get('Effect') == 'Deny' and
               str(((item.get('Condition') or {}).get('Bool') or {}).get('aws:SecureTransport')).lower() == 'false'
               for item in statements):
        problems.append('the bucket policy does not deny non-TLS requests')
    return problems


def responsibility_matrix(deployed: dict[str, bool]) -> list[dict[str, str]]:
    """Who owns each layer on EC2, RDS, Lambda and S3. Services not deployed show as not checked."""
    owners = {
        'EC2': ['AWS', 'AWS', 'Customer', 'Customer', 'Customer', 'Customer', 'Customer', 'Customer', 'Customer'],
        'RDS': ['AWS', 'AWS', 'AWS', 'AWS', 'Customer', 'Customer', 'Customer', 'Customer', 'Shared'],
        'Lambda': ['AWS', 'AWS', 'AWS', 'AWS', 'Customer', 'Customer', 'Customer', 'Customer', 'n/a'],
        'S3': ['AWS', 'AWS', 'AWS', 'AWS', 'Customer', 'n/a', 'Customer', 'Customer', 'Customer'],
    }
    rows = []
    for index, layer in enumerate(LAYERS):
        row = {'Layer': layer}
        for service, column in owners.items():
            row[service] = column[index] if deployed.get(service) else 'not deployed'
        rows.append(row)
    return rows


def classify_tasks(tasks: list[tuple[str, str, str]] = TASKS) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {'AWS': [], 'Customer': [], 'Shared': []}
    for task, owner, why in tasks:
        grouped[owner].append({'Task': task, 'Why': why})
    return grouped


# ── Lookups ──────────────────────────────────────────────────────────────────

def _default_vpc() -> str | None:
    vpcs = _client('ec2').describe_vpcs(Filters=[{'Name': 'is-default', 'Values': ['true']}]).get('Vpcs', [])
    return vpcs[0]['VpcId'] if vpcs else None


def _group() -> dict[str, Any] | None:
    groups = _client('ec2').describe_security_groups(Filters=[{'Name': 'group-name', 'Values': [WEB_SG]}]).get(
        'SecurityGroups', [])
    return groups[0] if groups else None


def _instance() -> dict[str, Any] | None:
    reservations = _client('ec2').describe_instances(Filters=[
        {'Name': 'tag:Name', 'Values': [WEB_INSTANCE]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
    ]).get('Reservations', [])
    instances = [item for reservation in reservations for item in reservation.get('Instances', [])]
    return instances[0] if instances else None


def _db() -> dict[str, Any] | None:
    try:
        return _client('rds').describe_db_instances(DBInstanceIdentifier=DB_ID)['DBInstances'][0]
    except ClientError as exc:
        if _code(exc) in ('DBInstanceNotFound', 'DBInstanceNotFoundFault'):
            return None
        raise


def _function() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_configuration(FunctionName=FUNCTION)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _role_exists() -> bool:
    try:
        _client('iam').get_role(RoleName=ROLE_NAME)
        return True
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return False
        raise


def _bucket_exists() -> bool:
    try:
        _client('s3').head_bucket(Bucket=DATA_BUCKET)
        return True
    except ClientError:
        return False


def _bucket_settings() -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None, dict[str, Any] | None]:
    s3 = _client('s3')
    try:
        public_access = s3.get_public_access_block(Bucket=DATA_BUCKET)['PublicAccessBlockConfiguration']
    except ClientError:
        public_access = None
    try:
        encryption = s3.get_bucket_encryption(Bucket=DATA_BUCKET)['ServerSideEncryptionConfiguration']['Rules']
    except ClientError:
        encryption = None
    try:
        policy = json.loads(s3.get_bucket_policy(Bucket=DATA_BUCKET)['Policy'])
    except ClientError:
        policy = None
    return public_access, encryption, policy


def _run_shell(instance_id: str, script: str) -> tuple[str, str, str]:
    """Run a script through SSM Run Command and return (status, stdout, stderr)."""
    ssm = _client('ssm')
    command_id = ssm.send_command(InstanceIds=[instance_id], DocumentName='AWS-RunShellScript',
                                  Parameters={'commands': [script]})['Command']['CommandId']
    outcome: dict[str, Any] = {}

    def finished() -> bool:
        try:
            outcome.update(ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id))
        except ClientError as exc:
            if _code(exc) == 'InvocationDoesNotExist':
                return False
            raise
        return outcome.get('Status') not in ('Pending', 'InProgress', 'Delayed')
    if not _wait(finished, timeout=SSM_TIMEOUT_SECONDS, delay=1):
        return 'TimedOut', outcome.get('StandardOutputContent') or '', 'The command did not finish.'
    return outcome.get('Status') or '', outcome.get('StandardOutputContent') or '', outcome.get('StandardErrorContent') or ''


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('receipts.py', date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, RECEIPTS_SOURCE)
    return buffer.getvalue()


def _invoke(payload: dict[str, Any]) -> dict[str, Any]:
    response = _client('lambda').invoke(FunctionName=FUNCTION, Payload=json.dumps(payload).encode())
    raw = response['Payload'].read()
    try:
        body = json.loads(raw or b'{}')
    except json.JSONDecodeError:
        body = {'raw': raw.decode(errors='replace')}
    if response.get('FunctionError'):
        body = {'FunctionError': response['FunctionError'], **(body if isinstance(body, dict) else {'body': body})}
    return body if isinstance(body, dict) else {'body': body}


# ── Checks ───────────────────────────────────────────────────────────────────

def _https_only(group: dict[str, Any]) -> tuple[bool, str]:
    permissions = group.get('IpPermissions', [])
    ports = sorted({(item.get('IpProtocol'), item.get('FromPort'), item.get('ToPort')) for item in permissions},
                   key=str)
    if ports != [('tcp', 443, 443)]:
        return False, f'{WEB_SG} should allow only TCP 443; it allows {ports or "nothing"}.'
    return True, f'{WEB_SG} allows only HTTPS (TCP 443) from anywhere, and no SSH.'


def _check_group() -> tuple[bool, str]:
    group = _group()
    if not group:
        return False, f'Create the {WEB_SG} security group.'
    return _https_only(group)


def _check_instance() -> tuple[bool, str]:
    instance = _instance()
    if not instance or instance.get('State', {}).get('Name') != 'running':
        return False, f'Launch {WEB_INSTANCE} and wait until it is running.'
    group = _group()
    if not group or group['GroupId'] not in [item.get('GroupId') for item in instance.get('SecurityGroups', [])]:
        return False, f'{WEB_INSTANCE} should use the {WEB_SG} security group.'
    marker = _marked('ec2-guest-os')
    if not marker or not marker.get('os'):
        return False, 'Read the guest OS through SSM Run Command.'
    return True, (f'{WEB_INSTANCE} runs {marker["os"]} with {marker.get("packages", "?")} packages that you must keep '
                  'patched; AWS runs the host underneath.')


def _check_db() -> tuple[bool, str]:
    db = _db()
    if not db:
        return False, f'Create the {DB_ID} RDS instance.'
    problems = rds_customer_choices(db)
    if problems:
        return False, f'{DB_ID}: ' + '; '.join(problems) + '.'
    return True, (f'{DB_ID} is PostgreSQL {db.get("EngineVersion")}: AWS patches it in {db["PreferredMaintenanceWindow"]} '
                  f'and keeps {db["BackupRetentionPeriod"]} days of backups, encrypted and private, as you chose.')


def _check_function() -> tuple[bool, str]:
    function = _function()
    if not function:
        return False, f'Create the {FUNCTION} function.'
    if function.get('Runtime') != RUNTIME or function.get('Role') != ROLE_ARN:
        return False, f'{FUNCTION} should run {RUNTIME} with the {ROLE_NAME} role.'
    marker = _marked('lambda-managed-runtime')
    if not marker or not marker.get('execution_env'):
        return False, f'Invoke {FUNCTION} to see the runtime AWS provides.'
    return True, (f'{FUNCTION} ran on {marker["execution_env"]} (Python {marker.get("python")}), a runtime AWS '
                  'manages; the code and the role are yours.')


def _check_bucket() -> tuple[bool, str]:
    if not _bucket_exists():
        return False, f'Create the {DATA_BUCKET} bucket.'
    problems = bucket_problems(*_bucket_settings())
    if problems:
        return False, f'{DATA_BUCKET}: ' + '; '.join(problems) + '.'
    try:
        _client('s3').head_object(Bucket=DATA_BUCKET, Key=DATA_KEY)
    except ClientError:
        return False, f'Upload {DATA_KEY} to {DATA_BUCKET}.'
    return True, f'{DATA_BUCKET} blocks public access, encrypts by default, and refuses non-TLS requests.'


def _check_marker(step: str) -> tuple[bool, str]:
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_matrix() -> tuple[bool, str]:
    return _check_marker('responsibility-matrix')


def _check_tasks() -> tuple[bool, str]:
    return _check_marker('who-does-what')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_ec2_security_group() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    group = _group()
    if not group:
        vpc = _default_vpc()
        kwargs = {'VpcId': vpc} if vpc else {}
        group_id = ec2.create_security_group(GroupName=WEB_SG, Description='Web tier: HTTPS only (shared responsibility lab)',
                                             TagSpecifications=_tags(WEB_SG, 'security-group'), **kwargs)['GroupId']
    else:
        group_id = group['GroupId']
        extra = [item for item in group.get('IpPermissions', [])
                 if (item.get('IpProtocol'), item.get('FromPort'), item.get('ToPort')) != ('tcp', 443, 443)]
        if extra:
            ec2.revoke_security_group_ingress(GroupId=group_id, IpPermissions=extra)
    try:
        ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=[{
            'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443,
            'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'HTTPS from anywhere'}]}])
    except ClientError as exc:
        if _code(exc) != 'InvalidPermission.Duplicate':
            raise
    group = _group() or {}
    response = {'GroupId': group.get('GroupId'), 'IpPermissions': group.get('IpPermissions'),
                'YouDecide': 'Which ports are open and to whom', 'AwsDoes': 'Enforces the rules on every packet'}
    verified, message = _check_group()
    return _result('ec2-security-group', SG_COMMAND, response, verified, message, started)


def step_ec2_guest_os() -> dict[str, Any]:
    started = time.perf_counter()
    group = _group()
    if not group:
        return _result('ec2-guest-os', EC2_COMMAND, {}, False, f'Create {WEB_SG} first.', started)
    ec2 = _client('ec2')
    if not _instance():
        ec2.run_instances(ImageId=IMAGE_ID, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
                          SecurityGroupIds=[group['GroupId']], TagSpecifications=_tags(WEB_INSTANCE, 'instance'))
    _wait(lambda: (_instance() or {}).get('State', {}).get('Name') == 'running', timeout=120, delay=3)
    instance = _instance() or {}
    response: dict[str, Any] = {'InstanceId': instance.get('InstanceId'), 'State': instance.get('State')}
    if instance.get('State', {}).get('Name') == 'running':
        status, stdout, stderr = _run_shell(instance['InstanceId'], (
            '. /etc/os-release; echo "os=$PRETTY_NAME"; echo "kernel=$(uname -r)"; '
            'echo "packages=$(dpkg -l 2>/dev/null | grep -c ^ii)"'))
        report = parse_os_report(stdout)
        response['GuestOs'] = {'Status': status, **report, 'stderr': stderr.strip() or None}
        ssm = _client('ssm')
        try:
            scan_id = ssm.send_command(InstanceIds=[instance['InstanceId']], DocumentName='AWS-RunPatchBaseline',
                                       Parameters={'Operation': ['Scan']})['Command']['CommandId']
            time.sleep(2)
            scan = ssm.get_command_invocation(CommandId=scan_id, InstanceId=instance['InstanceId'])
            response['PatchScan'] = {'Status': scan.get('Status'), 'Note': (
                'Floci never runs AWS-RunPatchBaseline, so the scan stays Pending; on AWS, Patch Manager would '
                'report missing patches against your patch baseline.') if scan.get('Status') in ('Pending', 'InProgress') else None}
        except ClientError as exc:
            response['PatchScan'] = {'Outcome': 'unsupported', 'Code': _code(exc)}
        if status == 'Success' and report.get('os'):
            _marker('ec2-guest-os', {'os': report['os'], 'packages': report.get('packages'),
                                     'kernel': report.get('kernel')})
    response['YouOwn'] = ['Guest OS patches', 'Installed software', 'Security group rules', 'Instance role', 'Data on disk']
    verified, message = _check_instance()
    return _result('ec2-guest-os', EC2_COMMAND, response, verified, message, started)


def step_rds_managed_engine() -> dict[str, Any]:
    started = time.perf_counter()
    rds = _client('rds')
    db = _db()
    settings = {key: value for key, value in DB_SETTINGS.items() if key not in ('Engine', 'MasterUsername', 'AllocatedStorage')}
    if not db:
        rds.create_db_instance(DBInstanceIdentifier=DB_ID, MasterUserPassword=DB_PASSWORD,
                               Tags=[LAB_TAG, {'Key': 'Name', 'Value': DB_ID}], **DB_SETTINGS)
    elif rds_customer_choices(db):
        modifiable = {key: value for key, value in settings.items() if key not in ('StorageEncrypted', 'DBInstanceClass')}
        rds.modify_db_instance(DBInstanceIdentifier=DB_ID, ApplyImmediately=True, **modifiable)
    _wait(lambda: (_db() or {}).get('DBInstanceStatus') == 'available', timeout=180, delay=3)
    db = _db() or {}
    response: dict[str, Any] = {
        'DBInstance': {key: db.get(key) for key in (
            'DBInstanceIdentifier', 'DBInstanceStatus', 'Engine', 'EngineVersion', 'PreferredMaintenanceWindow',
            'AutoMinorVersionUpgrade', 'PreferredBackupWindow', 'BackupRetentionPeriod', 'StorageEncrypted',
            'PubliclyAccessible', 'MultiAZ')},
        'AwsDoes': ['Patches the OS and database engine in the maintenance window', 'Takes automated backups',
                    'Replaces failed hosts and fails over to the standby'],
        'YouChoose': ['The maintenance and backup windows', 'Backup retention', 'Encryption at rest',
                      'Network access (security groups, public or not)', 'Database users and their grants'],
    }
    try:
        response['PendingMaintenance'] = rds.describe_pending_maintenance_actions().get('PendingMaintenanceActions', [])
    except ClientError as exc:
        response['PendingMaintenance'] = {'Outcome': 'unsupported', 'Code': _code(exc)}
    verified, message = _check_db()
    return _result('rds-managed-engine', RDS_COMMAND, response, verified, message, started)


def step_lambda_managed_runtime() -> dict[str, Any]:
    started = time.perf_counter()
    iam, lam = _client('iam'), _client('lambda')
    if not _role_exists():
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
                        Description='Receipts function (shared responsibility lab)', Tags=[LAB_TAG])
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY, PolicyDocument=json.dumps(LOGS_POLICY))
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_zip())
    else:
        lam.create_function(FunctionName=FUNCTION, Runtime=RUNTIME, Handler=HANDLER, Role=ROLE_ARN,
                            Code={'ZipFile': _zip()}, Timeout=15, Tags={LAB_TAG['Key']: LAB_TAG['Value']},
                            Description='Receipts (shared responsibility lab)')
    _wait(lambda: (_function() or {}).get('State') in (None, 'Active') and
          (_function() or {}).get('LastUpdateStatus') in (None, 'Successful', 'Failed'), timeout=60)
    body = _invoke({'order_id': 1001, 'total': 42.5})
    if body.get('execution_env'):
        _marker('lambda-managed-runtime', {'execution_env': body['execution_env'], 'python': body.get('python'),
                                           'os': body.get('os')})
    function = _function() or {}
    response = {
        'Function': {key: function.get(key) for key in ('FunctionName', 'Runtime', 'Handler', 'Role')},
        'Invoke': body,
        'AwsDoes': ['Servers, OS and runtime patches', 'Scaling and availability across AZs'],
        'YouOwn': ['Function code and its dependencies', 'The execution role', 'Data the function handles',
                   'Moving to a newer runtime before the old one is deprecated'],
    }
    verified, message = _check_function()
    return _result('lambda-managed-runtime', LAMBDA_COMMAND, response, verified, message, started)


def step_s3_customer_data() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    if not _bucket_exists():
        s3.create_bucket(Bucket=DATA_BUCKET)
    s3.put_bucket_tagging(Bucket=DATA_BUCKET, Tagging={'TagSet': [LAB_TAG]})
    s3.put_public_access_block(Bucket=DATA_BUCKET, PublicAccessBlockConfiguration={
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    s3.put_bucket_encryption(Bucket=DATA_BUCKET, ServerSideEncryptionConfiguration={'Rules': [{
        'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}, 'BucketKeyEnabled': True}]})
    s3.put_bucket_policy(Bucket=DATA_BUCKET, Policy=json.dumps(TLS_ONLY_POLICY))
    s3.put_object(Bucket=DATA_BUCKET, Key=DATA_KEY, Body=DATA_BODY, ContentType='text/csv')
    public_access, encryption, policy = _bucket_settings()
    response = {'PublicAccessBlock': public_access, 'Encryption': encryption, 'Policy': policy,
                'AwsDoes': ['Durability and availability of the objects', 'The storage hardware and its disposal'],
                'YouOwn': ['What the data is and who may read it', 'Encryption choice and keys', 'Bucket policies']}
    verified, message = _check_bucket()
    return _result('s3-customer-data', S3_COMMAND, response, verified, message, started)


def step_responsibility_matrix() -> dict[str, Any]:
    started = time.perf_counter()
    deployed = {
        'EC2': _check_instance()[0],
        'RDS': _check_db()[0],
        'Lambda': _check_function()[0],
        'S3': _check_bucket()[0],
    }
    rows = responsibility_matrix(deployed)
    missing = [service for service, ok in deployed.items() if not ok]
    response = {'Deployed': deployed, 'Matrix': rows,
                'AlwaysCustomer': ['Data', 'IAM', 'Encryption choices'],
                'AlwaysAws': ['Facilities', 'Hardware', 'Global network', 'Hypervisor']}
    if missing:
        return _result('responsibility-matrix', MATRIX_COMMAND, response, False,
                       'Finish the earlier steps first: ' + ', '.join(missing) + ' not deployed yet.', started)
    customer_os = [service for service in ('EC2', 'RDS', 'Lambda') if rows[2][service] == 'Customer']
    message = (f'Guest OS patching is the customer\'s only on {", ".join(customer_os)}; AWS patches it on RDS and '
               'Lambda. Data, IAM and encryption choices are the customer\'s on all four.')
    _marker('responsibility-matrix', {'message': message})
    verified, check = _check_matrix()
    return _result('responsibility-matrix', MATRIX_COMMAND, response, verified, check, started)


def step_who_does_what() -> dict[str, Any]:
    started = time.perf_counter()
    grouped = classify_tasks()
    counts = {owner: len(items) for owner, items in grouped.items()}
    message = (f'{counts["AWS"]} tasks are AWS\'s, {counts["Customer"]} are the customer\'s, and {counts["Shared"]} '
               'are shared controls (patch management, configuration management, awareness and training).')
    _marker('who-does-what', {'message': message})
    verified, check = _check_tasks()
    return _result('who-does-what', TASKS_COMMAND, grouped, verified, check, started)


RUNNERS = {
    'ec2-security-group': step_ec2_security_group,
    'ec2-guest-os': step_ec2_guest_os,
    'rds-managed-engine': step_rds_managed_engine,
    'lambda-managed-runtime': step_lambda_managed_runtime,
    's3-customer-data': step_s3_customer_data,
    'responsibility-matrix': step_responsibility_matrix,
    'who-does-what': step_who_does_what,
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
    except (ClientError, BotoCoreError, KeyError, TypeError):
        return False


CHECKS = {
    'ec2-security-group': _check_group,
    'ec2-guest-os': _check_instance,
    'rds-managed-engine': _check_db,
    'lambda-managed-runtime': _check_function,
    's3-customer-data': _check_bucket,
    'responsibility-matrix': _check_matrix,
    'who-does-what': _check_tasks,
}


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
    """Terminate the instance and delete its security group, the database, the function and its role, and the bucket."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code.startswith('NoSuch') or code in ('ResourceNotFoundException',):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    ec2, rds, lam, iam, s3 = (_client(name) for name in ('ec2', 'rds', 'lambda', 'iam', 's3'))
    try:
        instance = _instance()
    except (ClientError, BotoCoreError) as exc:
        instance = None
        errors.append(f'instance lookup: {exc}')
    if instance:
        label = f'instance:{instance["InstanceId"]}'
        try:
            ec2.terminate_instances(InstanceIds=[instance['InstanceId']])
        except (ClientError, BotoCoreError):
            pass  # Floci can take longer than the client's read timeout to stop the container; check below.
        if _wait(lambda: not _instance(), timeout=120, delay=2):
            deleted.append(label)
        else:
            errors.append(f'{label}: still running after terminate-instances')
    try:
        group = _group()
    except (ClientError, BotoCoreError):
        group = None
    if group:
        attempt(f'security-group:{WEB_SG}', lambda: ec2.delete_security_group(GroupId=group['GroupId']))
    try:
        db = _db()
    except (ClientError, BotoCoreError):
        db = None
    if db:
        attempt(f'db:{DB_ID}', lambda: rds.delete_db_instance(DBInstanceIdentifier=DB_ID, SkipFinalSnapshot=True,
                                                               DeleteAutomatedBackups=True))
    attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    attempt(f'role-policy:{ROLE_POLICY}', lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))
    if _bucket_exists():
        try:
            for item in s3.list_objects_v2(Bucket=DATA_BUCKET).get('Contents', []):
                s3.delete_object(Bucket=DATA_BUCKET, Key=item['Key'])
        except ClientError as exc:
            errors.append(f'objects:{DATA_BUCKET}: {exc}')
        attempt(f'bucket:{DATA_BUCKET}', lambda: s3.delete_bucket(Bucket=DATA_BUCKET))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws ec2 terminate-instances --instance-ids <instance-id> && aws ec2 delete-security-group --group-id <sg-id>\n'
            f'aws rds delete-db-instance --db-instance-identifier {DB_ID} --skip-final-snapshot\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws iam delete-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} && '
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            f'aws s3 rb s3://{DATA_BUCKET} --force'
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
            else 'The instance, security group, database, function, role and bucket are gone.',
        },
    }
