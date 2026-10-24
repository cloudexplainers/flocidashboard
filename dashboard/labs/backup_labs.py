"""Backup and DR plan lab: a KMS-encrypted vault with an access policy and a vault lock, a vault in a second Region,
a backup plan with daily and monthly rules and a cross-Region copy, a tag-based selection, an on-demand backup of a
DynamoDB table, and a DR strategy chooser that maps RPO and RTO to backup and restore, pilot light, warm standby, or
multi-site active-active.

Floci 2.3.0 stores vaults, plans, and selections, runs on-demand backup jobs to COMPLETED, and enforces the vault
lock's minimum retention when a recovery point is deleted. A recovery point holds no data, restore and copy jobs are
unsupported, plan schedules never run, the vault access policy is not evaluated, plan lifecycle rules are not
validated, and plan tags are dropped, so the lab checks those rules itself.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'backup'
LAB_KEY = 'backup-dr-plan'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
DR_REGION = 'us-west-2'
ACCOUNT = '000000000000'

KEY_ALIAS = 'alias/lab-backup-vault'
VAULT = 'lab-orders-vault'
DR_VAULT = 'lab-orders-vault-dr'
VAULT_ARN = f'arn:aws:backup:{REGION}:{ACCOUNT}:backup-vault:{VAULT}'
DR_VAULT_ARN = f'arn:aws:backup:{DR_REGION}:{ACCOUNT}:backup-vault:{DR_VAULT}'
LOCK_MIN_DAYS, LOCK_MAX_DAYS, LOCK_GRACE_DAYS = 7, 2555, 3

PLAN = 'lab-orders-plan'
DAILY_RULE, MONTHLY_RULE = 'daily-35-days', 'monthly-7-years'
DAILY_CRON, MONTHLY_CRON = 'cron(0 5 * * ? *)', 'cron(0 5 1 * ? *)'
DAILY_RETENTION = 35
MONTHLY_COLD_AFTER, MONTHLY_RETENTION = 90, 2555
COLD_MINIMUM_DAYS = 90

ROLE_NAME = 'lab-backup-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
BACKUP_POLICY_ARN = 'arn:aws:iam::aws:policy/service-role/AWSBackupServiceRolePolicyForBackup'
SELECTION = 'tagged-daily'
TAG_KEY, TAG_VALUE = 'backup', 'daily'
TABLE = 'lab-orders-backup'
TABLE_ARN = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{TABLE}'
JOB_TIMEOUT_SECONDS = 60

# Requirements for the orders table, and the other workloads the chooser compares.
ORDERS_RPO_MINUTES, ORDERS_RTO_MINUTES = 24 * 60, 12 * 60
SCENARIOS = [
    ('Orders table (this lab)', ORDERS_RPO_MINUTES, ORDERS_RTO_MINUTES),
    ('Checkout API', 15, 60),
    ('Payments service', 1, 10),
    ('Global storefront', 0, 0),
]
STRATEGIES = {
    'backup-and-restore': {
        'Name': 'Backup and restore', 'TypicalRpo': 'hours', 'TypicalRto': 'hours (often under 24)', 'Cost': '$',
        'Pieces': 'AWS Backup with cross-Region copies; infrastructure as code to rebuild',
    },
    'pilot-light': {
        'Name': 'Pilot light', 'TypicalRpo': 'minutes', 'TypicalRto': 'tens of minutes', 'Cost': '$$',
        'Pieces': 'Data replicated live (Aurora Global Database, DynamoDB global tables, S3 replication); servers off '
                  'until failover',
    },
    'warm-standby': {
        'Name': 'Warm standby', 'TypicalRpo': 'seconds', 'TypicalRto': 'minutes', 'Cost': '$$$',
        'Pieces': 'A scaled-down full copy always running; scale out and fail over with Route 53 health checks',
    },
    'multi-site': {
        'Name': 'Multi-site active-active', 'TypicalRpo': 'near zero', 'TypicalRto': 'near zero', 'Cost': '$$$$',
        'Pieces': 'Full capacity in each Region serving traffic; Route 53 latency or weighted routing, global tables',
    },
}

BACKUP_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'backup.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
VAULT_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Sid': 'DenyRecoveryPointDeletion', 'Effect': 'Deny', 'Principal': '*',
        'Action': ['backup:DeleteRecoveryPoint', 'backup:UpdateRecoveryPointLifecycle'], 'Resource': '*',
    }],
}

VAULT_COMMAND = (
    f'aws kms create-key --description "Backup vault key"\n'
    f'aws kms create-alias --alias-name {KEY_ALIAS} --target-key-id <key-id>\n'
    f'aws backup create-backup-vault --backup-vault-name {VAULT} --encryption-key-arn <key-arn>\n'
    f'aws backup put-backup-vault-access-policy --backup-vault-name {VAULT} --policy file://deny-delete.json'
)
LOCK_COMMAND = (
    f'aws backup put-backup-vault-lock-configuration --backup-vault-name {VAULT} --min-retention-days {LOCK_MIN_DAYS} '
    f'--max-retention-days {LOCK_MAX_DAYS} --changeable-for-days {LOCK_GRACE_DAYS}'
)
DR_VAULT_COMMAND = f'aws backup create-backup-vault --backup-vault-name {DR_VAULT} --region {DR_REGION}'
PLAN_COMMAND = (
    'aws backup create-backup-plan --backup-plan file://plan.json\n'
    f'# {DAILY_RULE}: {DAILY_CRON}, keep {DAILY_RETENTION} days, copy to {DR_VAULT} in {DR_REGION}\n'
    f'# {MONTHLY_RULE}: {MONTHLY_CRON}, cold storage after {MONTHLY_COLD_AFTER} days, keep {MONTHLY_RETENTION} days'
)
SELECTION_COMMAND = (
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://backup-trust.json\n'
    f'aws iam attach-role-policy --role-name {ROLE_NAME} --policy-arn {BACKUP_POLICY_ARN}\n'
    f'aws dynamodb create-table --table-name {TABLE} ... --tags Key={TAG_KEY},Value={TAG_VALUE}\n'
    f'aws backup create-backup-selection --backup-plan-id <plan-id> --backup-selection '
    f"'{{\"SelectionName\":\"{SELECTION}\",\"IamRoleArn\":\"{ROLE_ARN}\",\"ListOfTags\":[{{\"ConditionType\":"
    f"\"STRINGEQUALS\",\"ConditionKey\":\"{TAG_KEY}\",\"ConditionValue\":\"{TAG_VALUE}\"}}]}}'"
)
BACKUP_COMMAND = (
    f'aws backup start-backup-job --backup-vault-name {VAULT} --resource-arn {TABLE_ARN} --iam-role-arn {ROLE_ARN} '
    f'--lifecycle DeleteAfterDays={DAILY_RETENTION}\n'
    'aws backup describe-backup-job --backup-job-id <job-id>\n'
    f'aws backup delete-recovery-point --backup-vault-name {VAULT} --recovery-point-arn <arn>   # refused by the lock\n'
    'aws backup start-restore-job --recovery-point-arn <arn> --iam-role-arn ... --metadata targetTableName=...'
)
STRATEGY_COMMAND = (
    f'# Orders table: RPO {ORDERS_RPO_MINUTES // 60} h, RTO {ORDERS_RTO_MINUTES // 60} h, lowest cost\n'
    f'aws backup get-backup-plan --backup-plan-id <plan-id>   # daily rule and a copy to {DR_REGION}?'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Plan backups and choose a disaster recovery strategy with AWS Backup',
    'description': (
        'Create a KMS-encrypted backup vault with a deny-delete policy and a vault lock, a vault in a second Region, '
        'a plan with daily and monthly rules and a cross-Region copy, and a tag-based selection; back up a DynamoDB '
        'table on demand, then match RPO and RTO targets to backup and restore, pilot light, warm standby, or '
        'multi-site active-active.'
    ),
    'steps': [
        {
            'key': 'backup-vault',
            'title': 'Create an encrypted vault that denies deletions',
            'command': VAULT_COMMAND,
            'explanation': (
                'A backup vault holds recovery points, encrypted with its KMS key. A vault access policy that denies '
                'DeleteRecoveryPoint protects backups even from administrators of the account, which matters for '
                'ransomware. How Floci emulates this: the vault, its key, and the policy are stored, but Floci does '
                'not evaluate vault policies, so the step checks the policy itself.'
            ),
        },
        {
            'key': 'vault-lock',
            'title': 'Lock the vault for compliance',
            'command': LOCK_COMMAND,
            'explanation': (
                f'Vault Lock enforces a retention window: recovery points cannot be deleted before {LOCK_MIN_DAYS} days '
                f'or kept beyond {LOCK_MAX_DAYS} days (7 years). For {LOCK_GRACE_DAYS} days the lock can still be '
                'removed (governance); after that it is in compliance mode and nobody, including AWS, can change it. '
                'How Floci emulates this: the lock is enforced; the backup step shows a deletion being refused.'
            ),
        },
        {
            'key': 'dr-vault',
            'title': f'Create a vault in {DR_REGION} for copies',
            'command': DR_VAULT_COMMAND,
            'explanation': (
                'Copies in another Region (or another account, with AWS Organizations) survive the loss of a Region '
                'or of the source account. They are the basis of the backup and restore DR strategy. How Floci '
                f'emulates this: the vault is created in {DR_REGION}.'
            ),
        },
        {
            'key': 'backup-plan',
            'title': 'Plan daily and monthly backups with a cross-Region copy',
            'command': PLAN_COMMAND,
            'explanation': (
                f'A plan has rules: a schedule, a start and completion window, a lifecycle, and copy actions. Daily '
                f'backups kept {DAILY_RETENTION} days set the RPO at 24 hours; monthly backups move to cold storage '
                f'after {MONTHLY_COLD_AFTER} days and are kept {MONTHLY_RETENTION // 365} years for compliance. Cold '
                f'storage must keep a recovery point at least {COLD_MINIMUM_DAYS} days. How Floci emulates this: the '
                'plan is stored but its schedules never run, and Floci does not validate the lifecycle or the vault '
                'names, so the step checks the rules.'
            ),
        },
        {
            'key': 'tag-selection',
            'title': f'Select resources tagged {TAG_KEY}={TAG_VALUE}',
            'command': SELECTION_COMMAND,
            'explanation': (
                'A selection assigns resources to a plan by ARN or by tag, so a new table tagged backup=daily is '
                'protected without editing the plan. AWS Backup assumes the selection\'s role to read and back up '
                'each resource. How Floci emulates this: the selection and role are stored; nothing evaluates the tag.'
            ),
        },
        {
            'key': 'on-demand-backup',
            'title': f'Back up {TABLE} on demand',
            'command': BACKUP_COMMAND,
            'explanation': (
                'An on-demand job backs up one resource now into a vault with its own lifecycle; restores create a '
                'new resource (a new table) from a recovery point, and should be tested regularly. How Floci emulates '
                'this: the job runs to COMPLETED and creates a recovery point, and the vault lock refuses deleting '
                'it. The recovery point holds no data and restore and copy jobs are unsupported, so the step records '
                'those attempts.'
            ),
        },
        {
            'key': 'dr-strategy',
            'title': 'Choose a disaster recovery strategy from RPO and RTO',
            'command': STRATEGY_COMMAND,
            'explanation': (
                'RPO is how much data you can lose; RTO is how long you can be down. Backup and restore (hours, '
                'lowest cost) rebuilds from copies; pilot light keeps data replicated and servers off (tens of '
                'minutes); warm standby runs a scaled-down copy (minutes); multi-site active-active serves from '
                'every Region (near zero, highest cost). Pick the cheapest strategy that meets both targets. How '
                'Floci emulates this: the step evaluates the targets and checks that the plan meets the orders '
                'table\'s RPO with a copy in another Region.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str, region: str | None = None):
    factory = FlociClientFactory()
    if region:
        factory.region = region
    return factory.client(name)


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


def choose_strategy(rpo_minutes: int, rto_minutes: int) -> str:
    """The cheapest strategy whose typical recovery meets both targets."""
    if rpo_minutes < 1 or rto_minutes < 1:
        return 'multi-site'
    if rto_minutes <= 15 or rpo_minutes <= 5:
        return 'warm-standby'
    if rto_minutes <= 120 or rpo_minutes < 60:
        return 'pilot-light'
    return 'backup-and-restore'


def schedule_interval_minutes(expression: str) -> int | None:
    """Minutes between runs for the simple cron forms a plan uses: hourly, daily, or monthly."""
    if not expression.startswith('cron(') or not expression.endswith(')'):
        return None
    fields = expression[5:-1].split()
    if len(fields) != 6:
        return None
    minute, hour, day, month, weekday, _year = fields
    if hour in ('*', '*/1') and month == '*':
        return 60
    if day in ('*', '?') and month == '*' and weekday in ('*', '?') and hour.isdigit():
        return 24 * 60
    if day.isdigit() and month == '*':
        return 31 * 24 * 60
    return None


# ── Lookups ──────────────────────────────────────────────────────────────────

def _key_arn() -> str | None:
    kms = _client('kms')
    try:
        metadata = kms.describe_key(KeyId=KEY_ALIAS)['KeyMetadata']
    except ClientError as exc:
        if _code(exc) == 'NotFoundException':
            return None
        raise
    return None if metadata.get('KeyState') == 'PendingDeletion' else metadata.get('Arn')


def _vault(name: str = VAULT, region: str | None = None) -> dict[str, Any] | None:
    try:
        return _client('backup', region).describe_backup_vault(BackupVaultName=name)
    except ClientError as exc:
        if _code(exc) in ('ResourceNotFoundException', 'AccessDeniedException'):
            return None
        raise


def _vault_policy() -> dict[str, Any] | None:
    try:
        policy = _client('backup').get_backup_vault_access_policy(BackupVaultName=VAULT).get('Policy')
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise
    return json.loads(policy) if isinstance(policy, str) else policy


def _plan() -> dict[str, Any] | None:
    plans = [item for item in _client('backup').list_backup_plans().get('BackupPlansList', [])
             if item.get('BackupPlanName') == PLAN and not item.get('DeletionDate')]
    if not plans:
        return None
    return _client('backup').get_backup_plan(BackupPlanId=plans[0]['BackupPlanId'])


def _selection(plan_id: str) -> dict[str, Any] | None:
    backup = _client('backup')
    items = [item for item in backup.list_backup_selections(BackupPlanId=plan_id).get('BackupSelectionsList', [])
             if item.get('SelectionName') == SELECTION]
    if not items:
        return None
    return backup.get_backup_selection(BackupPlanId=plan_id, SelectionId=items[0]['SelectionId'])


def _role() -> dict[str, Any] | None:
    try:
        return _client('iam').get_role(RoleName=ROLE_NAME)['Role']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise


def _table() -> dict[str, Any] | None:
    try:
        return _client('dynamodb').describe_table(TableName=TABLE)['Table']
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _recovery_points() -> list[dict[str, Any]]:
    try:
        points = _client('backup').list_recovery_points_by_backup_vault(BackupVaultName=VAULT).get('RecoveryPoints', [])
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return []
        raise
    return [point for point in points if point.get('ResourceArn') == TABLE_ARN]


def _rules(plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {rule.get('RuleName'): rule for rule in ((plan or {}).get('BackupPlan') or {}).get('Rules', [])}


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_vault() -> tuple[bool, str]:
    vault, key = _vault(), _key_arn()
    if not vault:
        return False, f'Vault {VAULT} was not found.'
    if not key or vault.get('EncryptionKeyArn') != key:
        return False, f'{VAULT} must be encrypted with the {KEY_ALIAS} key.'
    policy = _vault_policy() or {}
    denies = [statement for statement in policy.get('Statement', []) if statement.get('Effect') == 'Deny'
              and 'backup:DeleteRecoveryPoint' in (statement.get('Action') if isinstance(statement.get('Action'), list)
                                                   else [statement.get('Action')])]
    if not denies:
        return False, f'The {VAULT} access policy must deny backup:DeleteRecoveryPoint.'
    return True, f'{VAULT} is encrypted with {KEY_ALIAS} and its policy denies deleting recovery points.'


def _check_lock() -> tuple[bool, str]:
    vault = _vault()
    if not vault:
        return False, f'Vault {VAULT} was not found.'
    if not vault.get('Locked'):
        return False, f'{VAULT} is not locked.'
    if vault.get('MinRetentionDays') != LOCK_MIN_DAYS or vault.get('MaxRetentionDays') != LOCK_MAX_DAYS:
        return False, f'The lock should keep recovery points {LOCK_MIN_DAYS} to {LOCK_MAX_DAYS} days.'
    return True, f'{VAULT} is locked to keep recovery points {LOCK_MIN_DAYS} to {LOCK_MAX_DAYS} days.'


def _check_dr_vault() -> tuple[bool, str]:
    vault = _vault(DR_VAULT, DR_REGION)
    if not vault:
        return False, f'Vault {DR_VAULT} was not found in {DR_REGION}.'
    return True, f'{DR_VAULT} exists in {DR_REGION} for cross-Region copies.'


def _check_plan() -> tuple[bool, str]:
    plan = _plan()
    if not plan:
        return False, f'Backup plan {PLAN} was not found.'
    rules = _rules(plan)
    daily, monthly = rules.get(DAILY_RULE), rules.get(MONTHLY_RULE)
    if not daily or not monthly:
        return False, f'{PLAN} needs rules {DAILY_RULE} and {MONTHLY_RULE}.'
    for rule in (daily, monthly):
        if rule.get('TargetBackupVaultName') != VAULT:
            return False, f'{rule["RuleName"]} must back up to {VAULT}.'
        lifecycle = rule.get('Lifecycle') or {}
        cold, delete = lifecycle.get('MoveToColdStorageAfterDays'), lifecycle.get('DeleteAfterDays')
        if cold and (not delete or delete < cold + COLD_MINIMUM_DAYS):
            return False, (f'{rule["RuleName"]} moves to cold storage after {cold} days, so it must keep backups at '
                           f'least {cold + COLD_MINIMUM_DAYS} days.')
        if delete and not LOCK_MIN_DAYS <= delete <= LOCK_MAX_DAYS:
            return False, f'{rule["RuleName"]} keeps backups {delete} days, outside the vault lock window.'
    if schedule_interval_minutes(daily.get('ScheduleExpression') or '') != 24 * 60:
        return False, f'{DAILY_RULE} must run once a day.'
    if (daily.get('Lifecycle') or {}).get('DeleteAfterDays') != DAILY_RETENTION:
        return False, f'{DAILY_RULE} should keep backups {DAILY_RETENTION} days.'
    if (monthly.get('Lifecycle') or {}) != {'MoveToColdStorageAfterDays': MONTHLY_COLD_AFTER,
                                            'DeleteAfterDays': MONTHLY_RETENTION}:
        return False, (f'{MONTHLY_RULE} should move to cold storage after {MONTHLY_COLD_AFTER} days and keep backups '
                       f'{MONTHLY_RETENTION} days.')
    copies = [action.get('DestinationBackupVaultArn') for action in daily.get('CopyActions') or []]
    if DR_VAULT_ARN not in copies:
        return False, f'{DAILY_RULE} must copy to {DR_VAULT} in {DR_REGION}.'
    return True, (f'{PLAN} backs up daily (kept {DAILY_RETENTION} days, copied to {DR_REGION}) and monthly (cold after '
                  f'{MONTHLY_COLD_AFTER} days, kept {MONTHLY_RETENTION // 365} years).')


def _check_selection() -> tuple[bool, str]:
    plan = _plan()
    if not plan:
        return False, f'Backup plan {PLAN} was not found.'
    selection = (_selection(plan['BackupPlanId']) or {}).get('BackupSelection')
    if not selection:
        return False, f'{PLAN} has no {SELECTION} selection.'
    if selection.get('IamRoleArn') != ROLE_ARN or not _role():
        return False, f'The selection must use {ROLE_NAME}.'
    stored = selection.get('ListOfTags') or []
    if not stored and not selection.get('Resources'):
        # Floci drops ListOfTags from the stored selection, so fall back to the selection the step sent.
        stored = (_marked('tag-selection') or {}).get('ListOfTags') or []
    tags = [(item.get('ConditionKey'), item.get('ConditionValue')) for item in stored]
    if (TAG_KEY, TAG_VALUE) not in tags:
        return False, f'The selection must pick resources tagged {TAG_KEY}={TAG_VALUE}.'
    table = _table()
    if not table:
        return False, f'Table {TABLE} was not found.'
    table_tags = {item['Key']: item['Value'] for item in _client('dynamodb').list_tags_of_resource(
        ResourceArn=table['TableArn']).get('Tags', [])}
    if table_tags.get(TAG_KEY) != TAG_VALUE:
        return False, f'{TABLE} must be tagged {TAG_KEY}={TAG_VALUE}.'
    return True, f'{SELECTION} picks resources tagged {TAG_KEY}={TAG_VALUE}, including {TABLE}, as {ROLE_NAME}.'


def _check_backup() -> tuple[bool, str]:
    points = [point for point in _recovery_points() if point.get('Status') == 'COMPLETED']
    if not points:
        return False, f'{VAULT} has no completed recovery point for {TABLE}.'
    marker = _marked('on-demand-backup')
    if not marker:
        return False, f'Back up {TABLE} on demand.'
    return True, marker['message']


def _check_strategy() -> tuple[bool, str]:
    ok, problem = _check_plan()
    if not ok:
        return False, problem
    marker = _marked('dr-strategy')
    if not marker:
        return False, 'Run the chooser against the RPO and RTO targets.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_backup_vault() -> dict[str, Any]:
    started = time.perf_counter()
    kms, backup = _client('kms'), _client('backup')
    key = _key_arn()
    if not key:
        created = kms.create_key(Description='Backup vault key (AWS Backup lab)', Tags=[
            {'TagKey': 'Lab', 'TagValue': LAB_KEY}])['KeyMetadata']
        try:
            kms.create_alias(AliasName=KEY_ALIAS, TargetKeyId=created['KeyId'])
        except ClientError as exc:
            if _code(exc) != 'AlreadyExistsException':
                raise
            kms.update_alias(AliasName=KEY_ALIAS, TargetKeyId=created['KeyId'])
        key = created['Arn']
    vault = _vault()
    if vault and vault.get('EncryptionKeyArn') != key and not vault.get('NumberOfRecoveryPoints'):
        backup.delete_backup_vault(BackupVaultName=VAULT)
        vault = None
    if not vault:
        backup.create_backup_vault(BackupVaultName=VAULT, EncryptionKeyArn=key, BackupVaultTags={'Lab': LAB_KEY})
    backup.put_backup_vault_access_policy(BackupVaultName=VAULT, Policy=json.dumps(VAULT_POLICY))
    response = {'Vault': _vault(), 'AccessPolicy': _vault_policy()}
    verified, message = _check_vault()
    if verified:
        message += ' Floci stores the policy without evaluating it.'
    return _result('backup-vault', VAULT_COMMAND, response, verified, message, started)


def step_vault_lock() -> dict[str, Any]:
    started = time.perf_counter()
    if not _vault():
        return _result('vault-lock', LOCK_COMMAND, {}, False, f'Create {VAULT} first.', started)
    _client('backup').put_backup_vault_lock_configuration(
        BackupVaultName=VAULT, MinRetentionDays=LOCK_MIN_DAYS, MaxRetentionDays=LOCK_MAX_DAYS,
        ChangeableForDays=LOCK_GRACE_DAYS)
    vault = _vault() or {}
    response = {key: vault.get(key) for key in ('BackupVaultName', 'Locked', 'MinRetentionDays', 'MaxRetentionDays',
                                                 'LockDate')}
    verified, message = _check_lock()
    return _result('vault-lock', LOCK_COMMAND, response, verified, message, started)


def step_dr_vault() -> dict[str, Any]:
    started = time.perf_counter()
    if not _vault(DR_VAULT, DR_REGION):
        _client('backup', DR_REGION).create_backup_vault(BackupVaultName=DR_VAULT, BackupVaultTags={'Lab': LAB_KEY})
    verified, message = _check_dr_vault()
    return _result('dr-vault', DR_VAULT_COMMAND, {'Vault': _vault(DR_VAULT, DR_REGION)}, verified, message, started)


def _plan_document() -> dict[str, Any]:
    return {
        'BackupPlanName': PLAN,
        'Rules': [
            {'RuleName': DAILY_RULE, 'TargetBackupVaultName': VAULT, 'ScheduleExpression': DAILY_CRON,
             'StartWindowMinutes': 60, 'CompletionWindowMinutes': 180,
             'Lifecycle': {'DeleteAfterDays': DAILY_RETENTION},
             'CopyActions': [{'DestinationBackupVaultArn': DR_VAULT_ARN, 'Lifecycle': {'DeleteAfterDays': DAILY_RETENTION}}],
             'RecoveryPointTags': {'Rule': DAILY_RULE}},
            {'RuleName': MONTHLY_RULE, 'TargetBackupVaultName': VAULT, 'ScheduleExpression': MONTHLY_CRON,
             'StartWindowMinutes': 60, 'CompletionWindowMinutes': 360,
             'Lifecycle': {'MoveToColdStorageAfterDays': MONTHLY_COLD_AFTER, 'DeleteAfterDays': MONTHLY_RETENTION},
             'RecoveryPointTags': {'Rule': MONTHLY_RULE}},
        ],
    }


def step_backup_plan() -> dict[str, Any]:
    started = time.perf_counter()
    if not _vault() or not _vault(DR_VAULT, DR_REGION):
        return _result('backup-plan', PLAN_COMMAND, {}, False, f'Create {VAULT} and {DR_VAULT} first.', started)
    backup = _client('backup')
    plan = _plan()
    if plan:
        backup.update_backup_plan(BackupPlanId=plan['BackupPlanId'], BackupPlan=_plan_document())
    else:
        backup.create_backup_plan(BackupPlan=_plan_document(), BackupPlanTags={'Lab': LAB_KEY})
    plan = _plan() or {}
    response = {'BackupPlanId': plan.get('BackupPlanId'), 'BackupPlan': plan.get('BackupPlan')}
    verified, message = _check_plan()
    if verified:
        message += ' Floci never runs the schedules and does not validate the lifecycle, so the step checked it.'
    return _result('backup-plan', PLAN_COMMAND, response, verified, message, started)


def step_tag_selection() -> dict[str, Any]:
    started = time.perf_counter()
    plan = _plan()
    if not plan:
        return _result('tag-selection', SELECTION_COMMAND, {}, False, f'Create {PLAN} first.', started)
    iam, dynamodb, backup = _client('iam'), _client('dynamodb'), _client('backup')
    if not _role():
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(BACKUP_TRUST),
                        Description='AWS Backup service role (Backup lab)')
    iam.attach_role_policy(RoleName=ROLE_NAME, PolicyArn=BACKUP_POLICY_ARN)
    if not _table():
        dynamodb.create_table(
            TableName=TABLE, AttributeDefinitions=[{'AttributeName': 'order_id', 'AttributeType': 'S'}],
            KeySchema=[{'AttributeName': 'order_id', 'KeyType': 'HASH'}], BillingMode='PAY_PER_REQUEST',
            Tags=[{'Key': TAG_KEY, 'Value': TAG_VALUE}, {'Key': 'Lab', 'Value': LAB_KEY}])
        _wait(lambda: (_table() or {}).get('TableStatus') == 'ACTIVE', timeout=30)
    else:
        dynamodb.tag_resource(ResourceArn=TABLE_ARN, Tags=[{'Key': TAG_KEY, 'Value': TAG_VALUE}])
    tags = [{'ConditionType': 'STRINGEQUALS', 'ConditionKey': TAG_KEY, 'ConditionValue': TAG_VALUE}]
    existing = _selection(plan['BackupPlanId'])
    if existing and not (existing.get('BackupSelection') or {}).get('ListOfTags') and not _marked('tag-selection'):
        # Recreate a selection made outside this step so the step knows what it holds.
        backup.delete_backup_selection(BackupPlanId=plan['BackupPlanId'], SelectionId=existing['SelectionId'])
        existing = None
    if not existing:
        backup.create_backup_selection(BackupPlanId=plan['BackupPlanId'], BackupSelection={
            'SelectionName': SELECTION, 'IamRoleArn': ROLE_ARN, 'ListOfTags': tags})
    _marker('tag-selection', {'ListOfTags': tags})
    stored = (_selection(plan['BackupPlanId']) or {}).get('BackupSelection') or {}
    response = {'Selection': stored, 'SentListOfTags': tags,
                'Table': {key: (_table() or {}).get(key) for key in ('TableName', 'TableArn', 'TableStatus')}}
    verified, message = _check_selection()
    if verified and not stored.get('ListOfTags'):
        message += ' Floci dropped the tag condition from the stored selection, so the step checked the one it sent.'
    return _result('tag-selection', SELECTION_COMMAND, response, verified, message, started)


def step_on_demand_backup() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_selection()
    if not ok or not _check_lock()[0]:
        return _result('on-demand-backup', BACKUP_COMMAND, {}, False,
                       f'Lock the vault and create the selection first: {problem if not ok else _check_lock()[1]}', started)
    dynamodb, backup = _client('dynamodb'), _client('backup')
    dynamodb.put_item(TableName=TABLE, Item={'order_id': {'S': 'order-1001'}, 'total': {'N': '59.90'}})
    job_id = backup.start_backup_job(BackupVaultName=VAULT, ResourceArn=TABLE_ARN, IamRoleArn=ROLE_ARN,
                                     Lifecycle={'DeleteAfterDays': DAILY_RETENTION})['BackupJobId']
    job: dict[str, Any] = {}

    def done() -> bool:
        job.update(backup.describe_backup_job(BackupJobId=job_id))
        return job.get('State') not in ('CREATED', 'PENDING', 'RUNNING')
    _wait(done, timeout=JOB_TIMEOUT_SECONDS, delay=2)
    point_arn = job.get('RecoveryPointArn')
    attempts: dict[str, Any] = {}
    for label, call in (
            ('DeleteRecoveryPoint', lambda: backup.delete_recovery_point(BackupVaultName=VAULT, RecoveryPointArn=point_arn)),
            ('StartRestoreJob', lambda: backup.start_restore_job(
                RecoveryPointArn=point_arn, IamRoleArn=ROLE_ARN, ResourceType='DynamoDB',
                Metadata={'targetTableName': f'{TABLE}-restored'}))):
        try:
            call()
            attempts[label] = {'Status': 'accepted'}
        except ClientError as exc:
            attempts[label] = {'Status': 'refused', 'Code': _code(exc),
                               'Message': (exc.response.get('Error', {}).get('Message') or '')[:200]}
    response = {'BackupJob': {key: job.get(key) for key in (
        'BackupJobId', 'State', 'ResourceArn', 'RecoveryPointArn', 'BackupVaultName', 'CompletionDate')},
        'RecoveryPoints': [{key: point.get(key) for key in ('RecoveryPointArn', 'Status', 'Lifecycle', 'CreationDate')}
                           for point in _recovery_points()],
        'Attempts': attempts}
    if job.get('State') != 'COMPLETED' or not point_arn:
        return _result('on-demand-backup', BACKUP_COMMAND, response, False,
                       f'The backup job ended {job.get("State")} without a recovery point.', started)
    if attempts['DeleteRecoveryPoint']['Status'] != 'refused':
        return _result('on-demand-backup', BACKUP_COMMAND, response, False,
                       'The vault lock should have refused deleting the new recovery point.', started)
    message = (f'The backup job completed with a recovery point in {VAULT}, and deleting it was refused by the '
               f'vault lock ({LOCK_MIN_DAYS}-day minimum).')
    if attempts['StartRestoreJob']['Status'] == 'refused':
        message += ' Floci keeps no data in recovery points and does not support restore jobs.'
    _marker('on-demand-backup', {'message': message})
    verified, check = _check_backup()
    return _result('on-demand-backup', BACKUP_COMMAND, response, verified, message if verified else check, started)


def step_dr_strategy() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_plan()
    if not ok:
        return _result('dr-strategy', STRATEGY_COMMAND, {}, False, f'Fix the plan first: {problem}', started)
    plan = _plan() or {}
    daily = _rules(plan)[DAILY_RULE]
    interval = schedule_interval_minutes(daily['ScheduleExpression']) or 0
    choices = []
    for name, rpo, rto in SCENARIOS:
        strategy = choose_strategy(rpo, rto)
        choices.append({'Workload': name, 'RpoMinutes': rpo, 'RtoMinutes': rto, 'Strategy': STRATEGIES[strategy]['Name'],
                        **{key: STRATEGIES[strategy][key] for key in ('TypicalRpo', 'TypicalRto', 'Cost', 'Pieces')}})
    orders = choose_strategy(ORDERS_RPO_MINUTES, ORDERS_RTO_MINUTES)
    meets_rpo = 0 < interval <= ORDERS_RPO_MINUTES
    cross_region = any(action.get('DestinationBackupVaultArn', '').split(':')[3] != REGION
                       for action in daily.get('CopyActions') or [])
    response = {'Scenarios': choices, 'Strategies': STRATEGIES,
                'OrdersTable': {'Strategy': STRATEGIES[orders]['Name'], 'BackupIntervalMinutes': interval,
                                'MeetsRpo': meets_rpo, 'CopyInAnotherRegion': cross_region}}
    if orders != 'backup-and-restore' or not meets_rpo or not cross_region:
        return _result('dr-strategy', STRATEGY_COMMAND, response, False,
                       'The orders table needs backups at least every 24 hours with a copy in another Region.', started)
    message = (f'Backup and restore fits the orders table (RPO {ORDERS_RPO_MINUTES // 60} h, RTO '
               f'{ORDERS_RTO_MINUTES // 60} h): {PLAN} backs up daily and copies to {DR_REGION}. The checkout API '
               'needs pilot light, payments warm standby, and the global storefront multi-site active-active.')
    _marker('dr-strategy', {'message': message})
    verified, check = _check_strategy()
    return _result('dr-strategy', STRATEGY_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'backup-vault': step_backup_vault,
    'vault-lock': step_vault_lock,
    'dr-vault': step_dr_vault,
    'backup-plan': step_backup_plan,
    'tag-selection': step_tag_selection,
    'on-demand-backup': step_on_demand_backup,
    'dr-strategy': step_dr_strategy,
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
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    checks = {
        'backup-vault': lambda: _check_vault()[0],
        'vault-lock': lambda: _check_lock()[0],
        'dr-vault': lambda: _check_dr_vault()[0],
        'backup-plan': lambda: _check_plan()[0],
        'tag-selection': lambda: _check_selection()[0],
        'on-demand-backup': lambda: _check_backup()[0],
        'dr-strategy': lambda: _check_strategy()[0],
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
        'service': SERVICE,
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Remove the lock (still in its grace period), recovery points, selection, plan, vaults, table, role, and key."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code == 'NoSuchEntity':
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    backup, iam, dynamodb, kms = _client('backup'), _client('iam'), _client('dynamodb'), _client('kms')
    try:
        plan, vault, dr_vault = _plan(), _vault(), _vault(DR_VAULT, DR_REGION)
    except (ClientError, BotoCoreError) as exc:
        plan = vault = dr_vault = None
        errors.append(f'lookup: {exc}')
    if plan:
        selection = _selection(plan['BackupPlanId'])
        if selection:
            attempt(f'selection:{SELECTION}', lambda: backup.delete_backup_selection(
                BackupPlanId=plan['BackupPlanId'], SelectionId=selection['SelectionId']))
        attempt(f'plan:{PLAN}', lambda: backup.delete_backup_plan(BackupPlanId=plan['BackupPlanId']))
    if vault:
        if vault.get('Locked'):
            attempt(f'vault-lock:{VAULT}', lambda: backup.delete_backup_vault_lock_configuration(BackupVaultName=VAULT))
        points = backup.list_recovery_points_by_backup_vault(BackupVaultName=VAULT).get('RecoveryPoints', [])
        for point in points:
            attempt(f'recovery-point:{point["RecoveryPointArn"].rsplit(":", 1)[-1]}',
                    lambda point=point: backup.delete_recovery_point(BackupVaultName=VAULT,
                                                                     RecoveryPointArn=point['RecoveryPointArn']))
        attempt(f'vault-policy:{VAULT}', lambda: backup.delete_backup_vault_access_policy(BackupVaultName=VAULT))
        attempt(f'vault:{VAULT}', lambda: backup.delete_backup_vault(BackupVaultName=VAULT))
    if dr_vault:
        attempt(f'vault:{DR_VAULT}', lambda: _client('backup', DR_REGION).delete_backup_vault(BackupVaultName=DR_VAULT))
    attempt(f'table:{TABLE}', lambda: dynamodb.delete_table(TableName=TABLE))
    attempt(f'role-policy:{ROLE_NAME}', lambda: iam.detach_role_policy(RoleName=ROLE_NAME, PolicyArn=BACKUP_POLICY_ARN))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))
    try:
        key = _key_arn()
    except (ClientError, BotoCoreError) as exc:
        key = None
        errors.append(f'key: {exc}')
    if key:
        attempt(f'alias:{KEY_ALIAS}', lambda: kms.delete_alias(AliasName=KEY_ALIAS))
        attempt('key:pending-deletion', lambda: kms.schedule_key_deletion(KeyId=key, PendingWindowInDays=7))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws backup delete-backup-selection --backup-plan-id <plan-id> --selection-id <selection-id>\n'
            'aws backup delete-backup-plan --backup-plan-id <plan-id>\n'
            f'aws backup delete-backup-vault-lock-configuration --backup-vault-name {VAULT}   # only in the grace period\n'
            f'aws backup delete-recovery-point --backup-vault-name {VAULT} --recovery-point-arn <arn>\n'
            f'aws backup delete-backup-vault --backup-vault-name {VAULT}\n'
            f'aws backup delete-backup-vault --backup-vault-name {DR_VAULT} --region {DR_REGION}\n'
            f'aws dynamodb delete-table --table-name {TABLE}\n'
            f'aws iam detach-role-policy --role-name {ROLE_NAME} --policy-arn {BACKUP_POLICY_ARN}\n'
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            f'aws kms schedule-key-deletion --key-id {KEY_ALIAS} --pending-window-in-days 7'
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
            else 'The plan, vaults, recovery points, table, and role are deleted, and the key is pending deletion.',
        },
    }
