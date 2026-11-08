"""Machine identity lab: a permissions boundary that lets a team create its own roles without exceeding a ceiling,
role chaining from a CI role with a session policy, attribute-based access control decided from tags, and a database
credential rotated by a Secrets Manager rotation function through all four steps, including a rotation that fails its
test and is cleared.

Floci 2.4.0 enforces IAM for these calls: permissions boundaries, the iam:PermissionsBoundary condition key, explicit
denies, and session policies all decide real requests. It runs the rotation function for every step and moves the
version labels. What Floci does not do: it does not resolve tag condition keys (aws:ResourceTag, aws:PrincipalTag), so
an ABAC policy denies every request and the lab evaluates the tag decisions itself; it does not cap a chained role
session at one hour; and it never retries a failed rotation, so the lab clears the stuck version and rotates again.
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

SERVICE = 'iam'
LAB_KEY = 'machine-identity'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
ROOT = f'arn:aws:iam::{ACCOUNT}:root'

BOUNDARY = 'lab-identity-team-boundary'
BOUNDARY_ARN = f'arn:aws:iam::{ACCOUNT}:policy/{BOUNDARY}'
TEAM_ADMIN = 'lab-identity-team-admin'
TEAM_ADMIN_ARN = f'arn:aws:iam::{ACCOUNT}:role/{TEAM_ADMIN}'
TEAM_PREFIX = 'team-orders-'
WORKER = f'{TEAM_PREFIX}worker'
WORKER_ARN = f'arn:aws:iam::{ACCOUNT}:role/{WORKER}'
UNBOUNDED = f'{TEAM_PREFIX}unbounded'
CI_ROLE = 'lab-identity-ci'
CI_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{CI_ROLE}'
DEPLOYER = f'{TEAM_PREFIX}deployer'
DEPLOYER_ARN = f'arn:aws:iam::{ACCOUNT}:role/{DEPLOYER}'
ABAC_ROLE = 'lab-identity-abac'
ABAC_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ABAC_ROLE}'
INLINE_POLICY = 'lab-identity'

JOBS_QUEUE = 'team-orders-jobs'
AUDIT_QUEUE = 'team-orders-audit'
PAYMENTS_QUEUE = 'team-payments-jobs'
QUEUE_TAGS = {JOBS_QUEUE: 'orders', AUDIT_QUEUE: 'orders', PAYMENTS_QUEUE: 'payments'}
CHAINED_SECONDS = 7200

SECRET = 'lab-identity/orders-db'
DB_TABLE = 'lab-identity-db'
DB_USER = 'orders_app'
FUNCTION = 'lab-identity-rotator'
FUNCTION_ROLE = 'lab-identity-rotator-role'
FUNCTION_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FUNCTION_ROLE}'
FUNCTION_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
INITIAL_PASSWORD = 'orders-initial-1'
ROTATION_DAYS = 30
ROTATION_WAIT_SECONDS = 60
ROTATION_STEPS = ['createSecret', 'setSecret', 'testSecret', 'finishSecret']

FLOCI = 'How Floci emulates this: '


def _trust(principal: dict[str, Any], actions: list[str] | None = None) -> dict[str, Any]:
    return {'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Principal': principal, 'Action': actions or ['sts:AssumeRole']}]}


BOUNDARY_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'TeamServices', 'Effect': 'Allow',
         'Action': ['sqs:*', 'dynamodb:*', 'logs:*', 'secretsmanager:GetSecretValue'], 'Resource': '*'},
        {'Sid': 'TeamRoles', 'Effect': 'Allow', 'Action': 'sts:AssumeRole',
         'Resource': f'arn:aws:iam::{ACCOUNT}:role/{TEAM_PREFIX}*'},
    ],
}
TEAM_ADMIN_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'CreateOnlyWithTheBoundary', 'Effect': 'Allow',
         'Action': ['iam:CreateRole', 'iam:PutRolePermissionsBoundary'],
         'Resource': f'arn:aws:iam::{ACCOUNT}:role/{TEAM_PREFIX}*',
         'Condition': {'StringEquals': {'iam:PermissionsBoundary': BOUNDARY_ARN}}},
        {'Sid': 'ManageTeamRoles', 'Effect': 'Allow',
         'Action': ['iam:GetRole', 'iam:PutRolePolicy', 'iam:GetRolePolicy', 'iam:DeleteRolePolicy',
                    'iam:DeleteRole', 'iam:TagRole'],
         'Resource': f'arn:aws:iam::{ACCOUNT}:role/{TEAM_PREFIX}*'},
        {'Sid': 'KeepTheCeiling', 'Effect': 'Deny',
         'Action': ['iam:DeleteRolePermissionsBoundary', 'iam:CreatePolicyVersion', 'iam:DeletePolicy',
                    'iam:SetDefaultPolicyVersion'],
         'Resource': '*'},
    ],
}
ADMIN_EVERYTHING = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': '*', 'Resource': '*'}]}
CI_POLICY = {'Version': '2012-10-17',
             'Statement': [{'Effect': 'Allow', 'Action': 'sts:AssumeRole', 'Resource': DEPLOYER_ARN}]}
DEPLOYER_POLICY = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': 'sqs:*', 'Resource': '*'}]}
SESSION_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Action': ['sqs:SendMessage', 'sqs:GetQueueUrl'],
                   'Resource': f'arn:aws:sqs:{REGION}:{ACCOUNT}:{JOBS_QUEUE}'}],
}
ABAC_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Sid': 'SameTeamOnly', 'Effect': 'Allow', 'Action': ['sqs:SendMessage'], 'Resource': '*',
                   'Condition': {'StringEquals': {'aws:ResourceTag/team': '${aws:PrincipalTag/team}'}}}],
}
FUNCTION_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Effect': 'Allow', 'Action': ['secretsmanager:GetSecretValue', 'secretsmanager:PutSecretValue',
                                       'secretsmanager:DescribeSecret', 'secretsmanager:UpdateSecretVersionStage'],
         'Resource': f'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:{SECRET}*'},
        {'Effect': 'Allow', 'Action': ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
         'Resource': f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{DB_TABLE}'},
        {'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
         'Resource': '*'},
    ],
}

ROTATOR_SOURCE = '''"""Rotate the orders database password in four steps. The table stands in for the database's user store."""
import json
import os
import secrets

import boto3
from botocore.exceptions import ClientError

sm, ddb = boto3.client('secretsmanager'), boto3.client('dynamodb')
TABLE, USER = os.environ['DB_TABLE'], os.environ['DB_USER']
KEY = {'user': {'S': USER}}


def record(token, step, result):
    ddb.update_item(TableName=TABLE, Key=KEY,
                    UpdateExpression='SET steps = list_append(if_not_exists(steps, :none), :step)',
                    ExpressionAttributeValues={':none': {'L': []}, ':step': {'L': [{'M': {
                        'token': {'S': token}, 'step': {'S': step}, 'result': {'S': result}}}]}})


def secret(arn, **version):
    return json.loads(sm.get_secret_value(SecretId=arn, **version)['SecretString'])


def pending(arn, token):
    try:
        value = sm.get_secret_value(SecretId=arn, VersionId=token, VersionStage='AWSPENDING').get('SecretString')
    except ClientError as exc:
        if exc.response['Error']['Code'] in ('ResourceNotFoundException', 'InvalidRequestException'):
            return None
        raise
    return json.loads(value) if value else None


def database():
    return ddb.get_item(TableName=TABLE, Key=KEY, ConsistentRead=True)['Item']


def create_secret(arn, token):
    if pending(arn, token):
        return 'pending value already exists'
    value = {**secret(arn, VersionStage='AWSCURRENT'), 'password': 'orders-' + secrets.token_hex(8)}
    sm.put_secret_value(SecretId=arn, ClientRequestToken=token, SecretString=json.dumps(value),
                        VersionStages=['AWSPENDING'])
    return 'new password generated'


def set_secret(arn, token):
    ddb.update_item(TableName=TABLE, Key=KEY, UpdateExpression='ADD passwords :new',
                    ExpressionAttributeValues={':new': {'SS': [pending(arn, token)['password']]}})
    return 'password added to the database user (the old one still works)'


def test_secret(arn, token):
    item = database()
    if item.get('locked', {}).get('BOOL'):
        raise RuntimeError('login refused: the database is locked for maintenance')
    if pending(arn, token)['password'] not in item.get('passwords', {}).get('SS', []):
        raise RuntimeError('login refused: the new password is not set on the database')
    return 'logged in with the new password'


def finish_secret(arn, token):
    versions = sm.describe_secret(SecretId=arn)['VersionIdsToStages']
    current = next((version for version, stages in versions.items() if 'AWSCURRENT' in stages), None)
    if current == token:
        return 'already current'
    sm.update_secret_version_stage(SecretId=arn, VersionStage='AWSCURRENT', MoveToVersionId=token,
                                   RemoveFromVersionId=current)
    return 'AWSCURRENT moved to the new version'


STEPS = {'createSecret': create_secret, 'setSecret': set_secret, 'testSecret': test_secret,
         'finishSecret': finish_secret}


def handler(event, context):
    step, arn, token = event['Step'], event['SecretId'], event['ClientRequestToken']
    try:
        result = STEPS[step](arn, token)
    except Exception as exc:
        record(token, step, f'failed: {exc}')
        raise
    record(token, step, result)
    print(json.dumps({'step': step, 'result': result}))
    return {'step': step, 'result': result}
'''

BOUNDARY_COMMAND = (
    f'aws iam create-policy --policy-name {BOUNDARY} --policy-document file://team-boundary.json\n'
    f'aws iam create-role --role-name {TEAM_ADMIN} --assume-role-policy-document file://trust-account.json\n'
    f'aws iam put-role-policy --role-name {TEAM_ADMIN} --policy-name {INLINE_POLICY} '
    '--policy-document file://team-admin.json   # CreateRole only with iam:PermissionsBoundary = the boundary'
)
DELEGATED_COMMAND = (
    f'aws sts assume-role --role-arn {TEAM_ADMIN_ARN} --role-session-name team-admin   # then, as the team admin:\n'
    f'aws iam create-role --role-name {UNBOUNDED} --assume-role-policy-document file://trust.json   # denied\n'
    f'aws iam create-role --role-name {WORKER} --assume-role-policy-document file://trust.json '
    f'--permissions-boundary {BOUNDARY_ARN}\n'
    f'aws iam put-role-policy --role-name {WORKER} --policy-name {INLINE_POLICY} '
    '--policy-document \'{"Statement":[{"Effect":"Allow","Action":"*","Resource":"*"}]}\'\n'
    f'aws iam delete-role-permissions-boundary --role-name {WORKER}   # denied\n'
    f'aws iam create-policy-version --policy-arn {BOUNDARY_ARN} --policy-document file://wider.json '
    '--set-as-default   # denied'
)
CEILING_COMMAND = (
    f'aws sts assume-role --role-arn {WORKER_ARN} --role-session-name worker   # then, as the worker:\n'
    f'aws sqs create-queue --queue-name {JOBS_QUEUE}   # inside the boundary\n'
    'aws iam list-roles                     # its own policy allows *, the boundary does not\n'
    'aws ssm describe-parameters            # outside the boundary too'
)
CHAIN_COMMAND = (
    f'aws sts assume-role --role-arn {CI_ROLE_ARN} --role-session-name pipeline\n'
    f'aws sts assume-role --role-arn {DEPLOYER_ARN} --role-session-name pipeline-deploy '
    f'--duration-seconds {CHAINED_SECONDS} --policy file://only-jobs-queue.json   # with the CI role\'s credentials\n'
    'aws sts get-caller-identity\n'
    f'aws sqs send-message --queue-url <{JOBS_QUEUE}> --message-body deploy-1      # allowed\n'
    f'aws sqs send-message --queue-url <{AUDIT_QUEUE}> --message-body deploy-1     # the session policy denies it'
)
ABAC_COMMAND = (
    ''.join(f'aws sqs tag-queue --queue-url <{queue}> --tags team={team}\n' for queue, team in QUEUE_TAGS.items())
    + f'aws iam create-role --role-name {ABAC_ROLE} --assume-role-policy-document file://abac-trust.json   '
    '# sts:AssumeRole and sts:TagSession\n'
    f'aws iam put-role-policy --role-name {ABAC_ROLE} --policy-name {INLINE_POLICY} '
    '--policy-document file://same-team.json   # aws:ResourceTag/team = ${aws:PrincipalTag/team}\n'
    f'aws sts assume-role --role-arn {ABAC_ROLE_ARN} --role-session-name orders-dev --tags Key=team,Value=orders\n'
    f'aws sqs send-message --queue-url <{JOBS_QUEUE}> --message-body hello       # same team\n'
    f'aws sqs send-message --queue-url <{PAYMENTS_QUEUE}> --message-body hello   # another team'
)
ROTATE_COMMAND = (
    f'aws dynamodb create-table --table-name {DB_TABLE} ...   # stands in for the database user store\n'
    f'aws secretsmanager create-secret --name {SECRET} '
    f'--secret-string \'{{"username":"{DB_USER}","password":"{INITIAL_PASSWORD}"}}\'\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime python3.12 --handler index.handler ...\n'
    f'aws lambda add-permission --function-name {FUNCTION} --statement-id secretsmanager '
    '--action lambda:InvokeFunction --principal secretsmanager.amazonaws.com\n'
    f'aws secretsmanager rotate-secret --secret-id {SECRET} --rotation-lambda-arn {FUNCTION_ARN} '
    f'--rotation-rules AutomaticallyAfterDays={ROTATION_DAYS}\n'
    f'aws secretsmanager describe-secret --secret-id {SECRET}   # VersionIdsToStages'
)
FAILED_COMMAND = (
    f'aws dynamodb update-item --table-name {DB_TABLE} --key \'{{"user":{{"S":"{DB_USER}"}}}}\' '
    '--update-expression "SET locked = :t" --expression-attribute-values \'{":t":{"BOOL":true}}\'\n'
    f'aws secretsmanager rotate-secret --secret-id {SECRET}   # testSecret fails\n'
    f'aws secretsmanager describe-secret --secret-id {SECRET}   # AWSPENDING left on the new version\n'
    f'aws secretsmanager rotate-secret --secret-id {SECRET}   # "A previous rotation isn\'t complete"'
)
REDRIVE_COMMAND = (
    f'aws dynamodb update-item --table-name {DB_TABLE} ... --update-expression "REMOVE locked"\n'
    f'aws secretsmanager cancel-rotate-secret --secret-id {SECRET}   # reports the half-built version\n'
    f'aws secretsmanager update-secret-version-stage --secret-id {SECRET} --version-stage AWSPENDING '
    '--remove-from-version-id <half-built version>\n'
    f'aws secretsmanager rotate-secret --secret-id {SECRET} --rotation-lambda-arn {FUNCTION_ARN} '
    f'--rotation-rules AutomaticallyAfterDays={ROTATION_DAYS}\n'
    f'aws secretsmanager get-secret-value --secret-id {SECRET} --version-stage AWSPREVIOUS\n'
    f'aws dynamodb update-item --table-name {DB_TABLE} ... --update-expression "DELETE passwords :old"   '
    '# retire passwords older than AWSPREVIOUS'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Manage machine identities at scale: boundaries, chained roles, ABAC, and credential rotation',
    'description': (
        'Let a team create its own roles under a permissions boundary they cannot remove, chain from a CI role to a '
        'deploy role with a session policy, decide access from tags with ABAC, and rotate a database password with a '
        'Secrets Manager rotation function through all four steps, including a rotation that fails its test and is '
        'cleared.'
    ),
    'steps': [
        {
            'key': 'team-boundary',
            'title': 'Define the boundary and the team admin role',
            'command': BOUNDARY_COMMAND,
            'explanation': (
                f'The platform team writes {BOUNDARY}, the most any team role may ever do: SQS, DynamoDB, logs, '
                f'reading secrets, and assuming other {TEAM_PREFIX}* roles. The team gets {TEAM_ADMIN}, which may '
                f'create {TEAM_PREFIX}* roles only when the request sets that boundary (the iam:PermissionsBoundary '
                'condition key), and an explicit deny stops it from removing the boundary or editing the boundary '
                'policy. The team now moves fast without a ticket for every role. '
                + FLOCI + 'the policies and roles are real, and the next steps show they are enforced.'
            ),
        },
        {
            'key': 'delegated-roles',
            'title': 'Create a team role as the team admin',
            'command': DELEGATED_COMMAND,
            'explanation': (
                f'As {TEAM_ADMIN}, creating {UNBOUNDED} without a boundary is denied, and creating {WORKER} with the '
                'boundary works. The team gives the worker a policy that allows everything, which is safe because '
                'the boundary still caps it. Removing the boundary or widening the boundary policy is denied. '
                + FLOCI + 'every one of these decisions is made by Floci\'s IAM evaluator, including the '
                'iam:PermissionsBoundary condition and the explicit deny.'
            ),
        },
        {
            'key': 'boundary-ceiling',
            'title': 'Check that the boundary caps the team role',
            'command': CEILING_COMMAND,
            'explanation': (
                f'{WORKER} has a policy that allows every action, but its effective permissions are the overlap with '
                f'the boundary. It creates the {JOBS_QUEUE} and {AUDIT_QUEUE} queues, but listing IAM roles and '
                'reading Parameter Store are denied. '
                + FLOCI + 'the boundary is enforced on every request made with the role\'s credentials.'
            ),
        },
        {
            'key': 'chained-session',
            'title': 'Chain from the CI role to a deploy role with a session policy',
            'command': CHAIN_COMMAND,
            'explanation': (
                f'A pipeline runs as {CI_ROLE}, which may only assume {DEPLOYER}. It passes a session policy that '
                f'narrows the deploy session to sending messages to {JOBS_QUEUE}, so this deployment cannot touch '
                f'{AUDIT_QUEUE} even though the deploy role could. The caller identity names the deploy role and the '
                'session. On AWS a chained session lasts at most one hour, so asking for two hours fails. '
                + FLOCI + 'the chain and the session policy are enforced, but Floci issues the two-hour chained '
                'session; the step reports the expiration it returned.'
            ),
        },
        {
            'key': 'abac-decisions',
            'title': 'Decide access from tags with ABAC',
            'command': ABAC_COMMAND,
            'explanation': (
                'With attribute-based access control, one policy serves every team: a principal may send to a queue '
                'only when the queue\'s team tag matches the team tag on the session. A new team needs tags, not a '
                f'new policy. The session is tagged team=orders, so {JOBS_QUEUE} should be allowed and '
                f'{PAYMENTS_QUEUE} denied. '
                + FLOCI + 'Floci\'s IAM evaluator does not resolve aws:ResourceTag or aws:PrincipalTag, so it denies '
                'both requests. The step records Floci\'s answers and evaluates the policy against the tags itself, '
                'which is the decision AWS would make.'
            ),
        },
        {
            'key': 'rotate-db-credentials',
            'title': 'Rotate the database password through all four steps',
            'command': ROTATE_COMMAND,
            'explanation': (
                f'The orders service reads its database password from {SECRET}. The rotation function {FUNCTION} '
                'generates a new password (createSecret), adds it to the database user without removing the old one '
                '(setSecret), logs in with it (testSecret), and moves the AWSCURRENT label to it (finishSecret). The '
                f'table {DB_TABLE} stands in for the database\'s user store. Because the old password still works, '
                'instances that cached it keep running until they fetch the new one from AWSCURRENT. '
                + FLOCI + 'Floci invokes the function for all four steps in the background and moves AWSPREVIOUS '
                'and AWSCURRENT; rotation rules are stored and Floci also rotates on schedule.'
            ),
        },
        {
            'key': 'failed-rotation',
            'title': 'Watch a rotation fail its test',
            'command': FAILED_COMMAND,
            'explanation': (
                'The database is locked for maintenance. The next rotation creates and sets a new password, but '
                'testSecret cannot log in, so the function fails and AWSCURRENT never moves: the service keeps '
                'working on the current password. The new version keeps the AWSPENDING label, and another '
                'RotateSecret is refused until that rotation completes. '
                + FLOCI + 'the failed step, the unchanged AWSCURRENT, the leftover AWSPENDING, and the refusal are '
                'real. Floci also leaves AWSPENDING on the current version (AWS moves the label), so the lab looks '
                'for the pending version that is not current.'
            ),
        },
        {
            'key': 'redrive-rotation',
            'title': 'Clear the stuck rotation, rotate again, and retire old passwords',
            'command': REDRIVE_COMMAND,
            'explanation': (
                'After the lock is lifted, the on-call engineer cancels the rotation, which reports the half-built '
                'version, and takes the AWSPENDING label off it, rolling back to the current password. A new rotation '
                'then runs all four steps. Finally the lab removes every password except the current and previous '
                'ones from the database, including the one the failed rotation had set, so a leaked old password '
                'stops working. '
                + FLOCI + 'CancelRotateSecret, UpdateSecretVersionStage, and the new rotation are real. On AWS, '
                'Secrets Manager also retries a failed rotation on its own; Floci refuses every RotateSecret, even '
                'with the pending version\'s token, until the label is cleared.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _session_client(name: str, credentials: dict[str, Any]):
    return FlociClientFactory(identity={
        'access_key_id': credentials['AccessKeyId'], 'secret_access_key': credentials['SecretAccessKey'],
        'session_token': credentials['SessionToken'], 'type': 'lab'}).client(name)


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


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _attempt(call) -> dict[str, Any]:
    """Run one request and report whether IAM allowed it."""
    try:
        call()
        return {'allowed': True}
    except ClientError as exc:
        return {'allowed': False, 'error': _code(exc)}


# ── Pure helpers ─────────────────────────────────────────────────────────────

def abac_allows(policy: dict[str, Any], action: str, principal_tags: dict[str, str],
                resource_tags: dict[str, str]) -> bool:
    """Evaluate Allow statements whose only conditions are StringEquals on tags, the way IAM does for ABAC."""
    for statement in policy.get('Statement', []):
        actions = statement.get('Action')
        actions = actions if isinstance(actions, list) else [actions]
        if statement.get('Effect') != 'Allow' or not any(item in (action, '*') for item in actions):
            continue
        matched = True
        for key, wanted in (statement.get('Condition', {}).get('StringEquals') or {}).items():
            if wanted.startswith('${aws:PrincipalTag/'):
                wanted = principal_tags.get(wanted[len('${aws:PrincipalTag/'):-1])
            if key.startswith('aws:ResourceTag/'):
                actual = resource_tags.get(key[len('aws:ResourceTag/'):])
            elif key.startswith('aws:PrincipalTag/'):
                actual = principal_tags.get(key[len('aws:PrincipalTag/'):])
            else:
                actual = None
            if actual is None or wanted is None or actual != wanted:
                matched = False
        if matched:
            return True
    return False


def stage_map(versions: dict[str, list[str]]) -> dict[str, str]:
    """Which version holds each label. Floci can leave AWSPENDING on the current version as well as on a new pending
    one, so a pending version that is not current wins."""
    labels = {stage: version for version, stages in versions.items() for stage in stages}
    unfinished = [version for version, stages in versions.items()
                  if 'AWSPENDING' in stages and 'AWSCURRENT' not in stages]
    if unfinished:
        labels['AWSPENDING'] = unfinished[0]
    return labels


def steps_for(log: list[dict[str, Any]], token: str) -> list[str]:
    return [entry['step'] for entry in log if entry.get('token') == token]


def failures_for(log: list[dict[str, Any]], token: str) -> list[str]:
    return [f'{entry["step"]}: {entry["result"]}' for entry in log
            if entry.get('token') == token and str(entry.get('result', '')).startswith('failed')]


# ── Floci lookups ────────────────────────────────────────────────────────────

def _role(name: str) -> dict[str, Any] | None:
    return _safe_call(lambda: _client('iam').get_role(RoleName=name)['Role'])


def _ensure_role(name: str, trust: dict[str, Any], policy: dict[str, Any], boundary: str | None = None) -> None:
    iam = _client('iam')
    if not _role(name):
        kwargs = {'PermissionsBoundary': boundary} if boundary else {}
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                        Tags=[{'Key': 'Lab', 'Value': LAB_KEY}], **kwargs)
    else:
        iam.update_assume_role_policy(RoleName=name, PolicyDocument=json.dumps(trust))
    iam.put_role_policy(RoleName=name, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(policy))


def _assume(role_arn: str, session: str, client=None, **kwargs) -> dict[str, Any]:
    sts = client or _client('sts')
    last: Exception | None = None
    for _ in range(10):  # a role that was just created can take a moment to be assumable
        try:
            return sts.assume_role(RoleArn=role_arn, RoleSessionName=session, **kwargs)
        except ClientError as exc:
            last = exc
            time.sleep(1)
    raise last  # type: ignore[misc]


def _queue_url(name: str) -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=name)['QueueUrl'])


def _secret_versions() -> dict[str, list[str]]:
    return _safe_call(lambda: _client('secretsmanager').describe_secret(SecretId=SECRET)['VersionIdsToStages'], {})


def _secret_value(stage: str) -> dict[str, Any] | None:
    value = _safe_call(lambda: _client('secretsmanager').get_secret_value(SecretId=SECRET, VersionStage=stage))
    return json.loads(value['SecretString']) if value and value.get('SecretString') else None


def _database() -> dict[str, Any]:
    item = _safe_call(lambda: _client('dynamodb').get_item(TableName=DB_TABLE, Key={'user': {'S': DB_USER}},
                                                           ConsistentRead=True).get('Item'), None) or {}
    return {
        'passwords': sorted(item.get('passwords', {}).get('SS', [])),
        'locked': item.get('locked', {}).get('BOOL', False),
        'steps': [{key: value['S'] for key, value in entry['M'].items()} for entry in item.get('steps', {}).get('L', [])],
    }


def _logs_in(password: str | None) -> bool:
    database = _database()
    return bool(password) and not database['locked'] and password in database['passwords']


def _function() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('lambda').get_function(FunctionName=FUNCTION)['Configuration'])


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_boundary() -> tuple[bool, str]:
    if not _safe_call(lambda: _client('iam').get_policy(PolicyArn=BOUNDARY_ARN)):
        return False, f'The {BOUNDARY} policy does not exist yet.'
    policy = _safe_call(lambda: _client('iam').get_role_policy(RoleName=TEAM_ADMIN, PolicyName=INLINE_POLICY)
                        ['PolicyDocument'])
    if not policy:
        return False, f'{TEAM_ADMIN} has no policy yet.'
    if isinstance(policy, str):
        policy = json.loads(policy)
    conditions = [statement.get('Condition', {}).get('StringEquals', {}).get('iam:PermissionsBoundary')
                  for statement in policy.get('Statement', [])]
    if BOUNDARY_ARN not in conditions:
        return False, f'{TEAM_ADMIN} can create roles without requiring {BOUNDARY}.'
    return True, f'{TEAM_ADMIN} may create {TEAM_PREFIX}* roles only with {BOUNDARY}.'


def _check_delegated() -> tuple[bool, str]:
    marker = _marked('delegated-roles') or {}
    expected = {'createWithoutBoundary': False, 'createWithBoundary': True, 'removeBoundary': False,
                'widenBoundary': False}
    wrong = [name for name, allowed in expected.items() if (marker.get(name) or {}).get('allowed') is not allowed]
    if wrong:
        return False, 'Unexpected decisions: ' + ', '.join(wrong) + '.'
    role = _role(WORKER) or {}
    if (role.get('PermissionsBoundary') or {}).get('PermissionsBoundaryArn') != BOUNDARY_ARN:
        return False, f'{WORKER} does not carry {BOUNDARY}.'
    return True, f'Only a role with {BOUNDARY} could be created, and the boundary could not be removed or widened.'


def _check_ceiling() -> tuple[bool, str]:
    marker = _marked('boundary-ceiling') or {}
    if not (marker.get('sqs') or {}).get('allowed'):
        return False, f'{WORKER} could not use SQS, which the boundary allows.'
    leaked = [name for name in ('iam', 'ssm') if (marker.get(name) or {}).get('allowed') is not False]
    if leaked:
        return False, f'{WORKER} reached ' + ', '.join(leaked) + ', outside the boundary.'
    return True, f'{WORKER} allows everything, but only SQS inside the boundary worked.'


def _check_chain() -> tuple[bool, str]:
    marker = _marked('chained-session') or {}
    if DEPLOYER not in str(marker.get('caller', '')):
        return False, 'The chained session is not running as the deploy role.'
    if not (marker.get('jobs') or {}).get('allowed') or (marker.get('audit') or {}).get('allowed') is not False:
        return False, f'The session policy should allow {JOBS_QUEUE} and deny {AUDIT_QUEUE}: {marker or "not run"}.'
    return True, f'The chained session reached {JOBS_QUEUE} only, as its session policy says.'


def _check_abac() -> tuple[bool, str]:
    marker = _marked('abac-decisions') or {}
    expected = marker.get('expected') or {}
    if expected != {JOBS_QUEUE: True, PAYMENTS_QUEUE: False}:
        return False, f'The tag decisions are not the expected ones: {expected or "not run"}.'
    return True, f'Same team allowed ({JOBS_QUEUE}), another team denied ({PAYMENTS_QUEUE}).'


def _check_rotated() -> tuple[bool, str]:
    marker = _marked('rotate-db-credentials') or {}
    if marker.get('steps') != ROTATION_STEPS:
        return False, f'The rotation ran {marker.get("steps") or "no steps"}, not all four.'
    if not (marker.get('currentLogsIn') and marker.get('previousLogsIn')):
        return False, 'The new password or the previous one does not log in.'
    return True, 'All four steps ran, the new password is AWSCURRENT, and the previous one still logs in.'


def _check_failed() -> tuple[bool, str]:
    marker = _marked('failed-rotation') or {}
    if not marker.get('failures') or not str(marker['failures'][0]).startswith('testSecret'):
        return False, f'The rotation did not fail at testSecret: {marker.get("failures") or "not run"}.'
    if not marker.get('currentUnchanged') or not marker.get('pendingLeft'):
        return False, 'AWSCURRENT moved, or no AWSPENDING version was left.'
    if (marker.get('retry') or {}).get('allowed') is not False:
        return False, 'Another rotation was not refused.'
    return True, 'testSecret failed, AWSCURRENT stayed put, and another rotation was refused until this one completes.'


def _check_redrive() -> tuple[bool, str]:
    marker = _marked('redrive-rotation') or {}
    if marker.get('steps') != ROTATION_STEPS:
        return False, f'The new rotation ran {marker.get("steps") or "nothing"}, not all four steps.'
    if not marker.get('pendingIsCurrent') or not marker.get('currentLogsIn'):
        return False, 'The new version is not AWSCURRENT or does not log in.'
    if marker.get('passwords') != 2:
        return False, f'The database still has {marker.get("passwords")} passwords, not two.'
    return True, 'The stuck rotation was cleared, a new one completed, and only the current and previous passwords log in.'


CHECKS = {
    'team-boundary': _check_boundary,
    'delegated-roles': _check_delegated,
    'boundary-ceiling': _check_ceiling,
    'chained-session': _check_chain,
    'abac-decisions': _check_abac,
    'rotate-db-credentials': _check_rotated,
    'failed-rotation': _check_failed,
    'redrive-rotation': _check_redrive,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def step_team_boundary() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    if not _safe_call(lambda: iam.get_policy(PolicyArn=BOUNDARY_ARN)):
        iam.create_policy(PolicyName=BOUNDARY, PolicyDocument=json.dumps(BOUNDARY_POLICY),
                          Description='The most any team role may do')
    _ensure_role(TEAM_ADMIN, _trust({'AWS': ROOT}), TEAM_ADMIN_POLICY)
    response = {'Boundary': {'Arn': BOUNDARY_ARN, 'Document': BOUNDARY_POLICY},
                'TeamAdmin': {'Arn': TEAM_ADMIN_ARN, 'Policy': TEAM_ADMIN_POLICY}}
    verified, message = _check_boundary()
    return _result('team-boundary', BOUNDARY_COMMAND, response, verified, message, started)


def step_delegated_roles() -> dict[str, Any]:
    started = time.perf_counter()
    if not _role(TEAM_ADMIN):
        return _result('delegated-roles', DELEGATED_COMMAND, {}, False, 'Create the team admin role first.', started)
    iam = _client('iam')
    for name in (WORKER, UNBOUNDED):  # start clean so the create calls are the real test
        if _role(name):
            _safe_call(lambda name=name: iam.delete_role_policy(RoleName=name, PolicyName=INLINE_POLICY))
            _safe_call(lambda name=name: iam.delete_role(RoleName=name))
    admin = _session_client('iam', _assume(TEAM_ADMIN_ARN, 'team-admin')['Credentials'])
    trust = json.dumps(_trust({'AWS': ROOT}))
    marker = {
        'createWithoutBoundary': _attempt(lambda: admin.create_role(RoleName=UNBOUNDED, AssumeRolePolicyDocument=trust)),
        'createWithBoundary': _attempt(lambda: admin.create_role(
            RoleName=WORKER, AssumeRolePolicyDocument=trust, PermissionsBoundary=BOUNDARY_ARN)),
        'putAdminPolicy': _attempt(lambda: admin.put_role_policy(
            RoleName=WORKER, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(ADMIN_EVERYTHING))),
        'removeBoundary': _attempt(lambda: admin.delete_role_permissions_boundary(RoleName=WORKER)),
        'widenBoundary': _attempt(lambda: admin.create_policy_version(
            PolicyArn=BOUNDARY_ARN, PolicyDocument=json.dumps(ADMIN_EVERYTHING), SetAsDefault=True)),
    }
    _marker('delegated-roles', marker)
    response = {**marker, 'Worker': _role(WORKER)}
    verified, message = _check_delegated()
    return _result('delegated-roles', DELEGATED_COMMAND, response, verified, message, started)


def step_boundary_ceiling() -> dict[str, Any]:
    started = time.perf_counter()
    if not _role(WORKER):
        return _result('boundary-ceiling', CEILING_COMMAND, {}, False, f'Create {WORKER} first.', started)
    credentials = _assume(WORKER_ARN, 'worker')['Credentials']
    sqs = _session_client('sqs', credentials)
    marker = {
        'sqs': _attempt(lambda: [sqs.create_queue(QueueName=name) for name in (JOBS_QUEUE, AUDIT_QUEUE)]),
        'iam': _attempt(lambda: _session_client('iam', credentials).list_roles(MaxItems=1)),
        'ssm': _attempt(lambda: _session_client('ssm', credentials).describe_parameters(MaxResults=1)),
    }
    _marker('boundary-ceiling', marker)
    verified, message = _check_ceiling()
    return _result('boundary-ceiling', CEILING_COMMAND, marker, verified, message, started)


def step_chained_session() -> dict[str, Any]:
    started = time.perf_counter()
    for name in (JOBS_QUEUE, AUDIT_QUEUE):
        if not _queue_url(name):
            _client('sqs').create_queue(QueueName=name)
    _ensure_role(DEPLOYER, _trust({'AWS': CI_ROLE_ARN}), DEPLOYER_POLICY, boundary=BOUNDARY_ARN)
    _ensure_role(CI_ROLE, _trust({'AWS': ROOT}), CI_POLICY)
    ci = _assume(CI_ROLE_ARN, 'pipeline')['Credentials']
    chained = _assume(DEPLOYER_ARN, 'pipeline-deploy', client=_session_client('sts', ci),
                      DurationSeconds=CHAINED_SECONDS, Policy=json.dumps(SESSION_POLICY))
    credentials = chained['Credentials']
    sqs = _session_client('sqs', credentials)
    lifetime = round((credentials['Expiration'].timestamp() - time.time()) / 60)
    marker = {
        'caller': _session_client('sts', credentials).get_caller_identity()['Arn'],
        'sessionMinutes': lifetime,
        'jobs': _attempt(lambda: sqs.send_message(QueueUrl=_queue_url(JOBS_QUEUE), MessageBody='deploy-1')),
        'audit': _attempt(lambda: sqs.send_message(QueueUrl=_queue_url(AUDIT_QUEUE), MessageBody='deploy-1')),
    }
    _marker('chained-session', marker)
    verified, message = _check_chain()
    if verified:
        message += (f' Floci issued a {lifetime}-minute chained session; AWS caps role chaining at 60 minutes.'
                    if lifetime > 60 else ' The chained session lasts at most an hour, as on AWS.')
    return _result('chained-session', CHAIN_COMMAND, {**marker, 'SessionPolicy': SESSION_POLICY}, verified, message,
                   started)


def step_abac_decisions() -> dict[str, Any]:
    started = time.perf_counter()
    sqs = _client('sqs')
    for name, team in QUEUE_TAGS.items():
        url = _queue_url(name) or sqs.create_queue(QueueName=name)['QueueUrl']
        sqs.tag_queue(QueueUrl=url, Tags={'team': team})
    _ensure_role(ABAC_ROLE, _trust({'AWS': ROOT}, ['sts:AssumeRole', 'sts:TagSession']), ABAC_POLICY)
    credentials = _assume(ABAC_ROLE_ARN, 'orders-dev', Tags=[{'Key': 'team', 'Value': 'orders'}])['Credentials']
    session = _session_client('sqs', credentials)
    principal_tags = {'team': 'orders'}
    floci, expected = {}, {}
    for name in (JOBS_QUEUE, PAYMENTS_QUEUE):
        floci[name] = _attempt(lambda name=name: session.send_message(QueueUrl=_queue_url(name), MessageBody='hello'))
        expected[name] = abac_allows(ABAC_POLICY, 'sqs:SendMessage', principal_tags, {'team': QUEUE_TAGS[name]})
    marker = {'principalTags': principal_tags, 'queueTags': QUEUE_TAGS, 'floci': floci, 'expected': expected}
    _marker('abac-decisions', marker)
    verified, message = _check_abac()
    if verified:
        agrees = all(floci[name]['allowed'] == expected[name] for name in expected)
        message += (' Floci made the same decisions.' if agrees
                    else ' Floci denied ' + ', '.join(name for name in floci if not floci[name]['allowed'])
                    + ' because it does not resolve tag condition keys.')
    return _result('abac-decisions', ABAC_COMMAND, {**marker, 'Policy': ABAC_POLICY}, verified, message, started)


def _rotator_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('index.py', ROTATOR_SOURCE)
    return buffer.getvalue()


def _ensure_database() -> None:
    dynamodb = _client('dynamodb')
    if not _safe_call(lambda: dynamodb.describe_table(TableName=DB_TABLE)):
        dynamodb.create_table(TableName=DB_TABLE, AttributeDefinitions=[{'AttributeName': 'user', 'AttributeType': 'S'}],
                              KeySchema=[{'AttributeName': 'user', 'KeyType': 'HASH'}], BillingMode='PAY_PER_REQUEST',
                              Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        dynamodb.get_waiter('table_exists').wait(TableName=DB_TABLE, WaiterConfig={'Delay': 1, 'MaxAttempts': 30})
    dynamodb.put_item(TableName=DB_TABLE, Item={'user': {'S': DB_USER}, 'passwords': {'SS': [INITIAL_PASSWORD]},
                                                'steps': {'L': []}})


def _ensure_secret() -> None:
    sm = _client('secretsmanager')
    value = json.dumps({'username': DB_USER, 'password': INITIAL_PASSWORD})
    if _safe_call(lambda: sm.describe_secret(SecretId=SECRET)):
        sm.delete_secret(SecretId=SECRET, ForceDeleteWithoutRecovery=True)
        _wait(lambda: not _safe_call(lambda: sm.describe_secret(SecretId=SECRET)), 10)
    sm.create_secret(Name=SECRET, SecretString=value, Description='Orders service database login',
                     Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])


def _ensure_function() -> None:
    _ensure_role(FUNCTION_ROLE, _trust({'Service': 'lambda.amazonaws.com'}), FUNCTION_POLICY)
    lam = _client('lambda')
    environment = {'Variables': {'DB_TABLE': DB_TABLE, 'DB_USER': DB_USER}}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_rotator_zip())
        _wait(lambda: (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=environment)
    else:
        lam.create_function(FunctionName=FUNCTION, Runtime='python3.12', Role=FUNCTION_ROLE_ARN,
                            Handler='index.handler', Code={'ZipFile': _rotator_zip()}, Timeout=30,
                            Environment=environment, Description='Rotates the orders database password',
                            Tags={'Lab': LAB_KEY})
        lam.add_permission(FunctionName=FUNCTION, StatementId='secretsmanager', Action='lambda:InvokeFunction',
                           Principal='secretsmanager.amazonaws.com')
    _wait(lambda: (_function() or {}).get('State', 'Active') == 'Active'
          and (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)


def _rotate(token: str | None = None) -> dict[str, Any]:
    kwargs = {'ClientRequestToken': token} if token else {}
    return _client('secretsmanager').rotate_secret(
        SecretId=SECRET, RotationLambdaARN=FUNCTION_ARN, RotationRules={'AutomaticallyAfterDays': ROTATION_DAYS},
        **kwargs)


def _wait_rotation(token: str, last_step: str) -> list[str]:
    """Wait until the function has recorded last_step (or a failure) for this token."""
    log: list[dict[str, Any]] = []

    def done() -> bool:
        log[:] = _database()['steps']
        return last_step in steps_for(log, token) or bool(failures_for(log, token))

    _wait(done, ROTATION_WAIT_SECONDS)
    _wait(lambda: False, 1)  # let Floci finish moving the labels after the last step
    return steps_for(log, token)


def step_rotate_db_credentials() -> dict[str, Any]:
    started = time.perf_counter()
    _ensure_database()
    _ensure_secret()
    _ensure_function()
    before = stage_map(_secret_versions())
    response = _rotate()
    token = response.get('VersionId') or ''
    if not token:  # Floci starts the rotation in the background; find the new version's ID
        _wait(lambda: stage_map(_secret_versions()).get('AWSPENDING') not in (None, before.get('AWSCURRENT')), 10)
        token = stage_map(_secret_versions()).get('AWSPENDING') or ''
    steps = _wait_rotation(token, 'finishSecret')
    _wait(lambda: stage_map(_secret_versions()).get('AWSCURRENT') == token, 10)
    current, previous = _secret_value('AWSCURRENT') or {}, _secret_value('AWSPREVIOUS') or {}
    marker = {
        'token': token,
        'steps': steps,
        'labels': stage_map(_secret_versions()),
        'currentLogsIn': current.get('password') != INITIAL_PASSWORD and _logs_in(current.get('password')),
        'previousLogsIn': previous.get('password') == INITIAL_PASSWORD and _logs_in(previous.get('password')),
    }
    _marker('rotate-db-credentials', marker)
    verified, message = _check_rotated()
    payload = {**marker, 'FunctionLog': [entry for entry in _database()['steps'] if entry['token'] == token]}
    return _result('rotate-db-credentials', ROTATE_COMMAND, payload, verified, message, started)


def _set_lock(locked: bool) -> None:
    expression = 'SET locked = :t' if locked else 'REMOVE locked'
    values = {'ExpressionAttributeValues': {':t': {'BOOL': True}}} if locked else {}
    _client('dynamodb').update_item(TableName=DB_TABLE, Key={'user': {'S': DB_USER}}, UpdateExpression=expression,
                                    **values)


def step_failed_rotation() -> dict[str, Any]:
    started = time.perf_counter()
    if not _function() or not _secret_versions():
        return _result('failed-rotation', FAILED_COMMAND, {}, False, 'Rotate the credentials first.', started)
    before = stage_map(_secret_versions())
    if before.get('AWSPENDING') and before['AWSPENDING'] != before.get('AWSCURRENT'):
        return _result('failed-rotation', FAILED_COMMAND, {'labels': before}, False,
                       'A rotation is already pending; re-drive it first.', started)
    _set_lock(True)
    _rotate()
    _wait(lambda: stage_map(_secret_versions()).get('AWSPENDING') not in (None, before.get('AWSCURRENT')), 10)
    token = stage_map(_secret_versions()).get('AWSPENDING') or ''
    steps = _wait_rotation(token, 'finishSecret')
    log = _database()['steps']
    after = stage_map(_secret_versions())
    marker = {
        'token': token,
        'steps': steps,
        'failures': failures_for(log, token),
        'labels': after,
        'currentUnchanged': after.get('AWSCURRENT') == before.get('AWSCURRENT'),
        'pendingLeft': bool(token) and after.get('AWSPENDING') == token,
        'retry': _attempt(_rotate),
    }
    _marker('failed-rotation', marker)
    verified, message = _check_failed()
    return _result('failed-rotation', FAILED_COMMAND, marker, verified, message, started)


def step_redrive_rotation() -> dict[str, Any]:
    started = time.perf_counter()
    labels = stage_map(_secret_versions())
    abandoned = labels.get('AWSPENDING')
    if not abandoned or abandoned == labels.get('AWSCURRENT'):
        return _result('redrive-rotation', REDRIVE_COMMAND, {'labels': labels}, False,
                       'No failed rotation is pending; run the failed rotation step first.', started)
    _set_lock(False)
    sm = _client('secretsmanager')
    cancelled = sm.cancel_rotate_secret(SecretId=SECRET)
    sm.update_secret_version_stage(SecretId=SECRET, VersionStage='AWSPENDING', RemoveFromVersionId=abandoned)
    before = stage_map(_secret_versions())
    _rotate()
    _wait(lambda: stage_map(_secret_versions()).get('AWSPENDING') not in (None, before.get('AWSCURRENT')), 10)
    token = stage_map(_secret_versions()).get('AWSPENDING') or ''
    steps = _wait_rotation(token, 'finishSecret')
    _wait(lambda: stage_map(_secret_versions()).get('AWSCURRENT') == token, 10)
    current, previous = _secret_value('AWSCURRENT') or {}, _secret_value('AWSPREVIOUS') or {}
    keep = {current.get('password'), previous.get('password')}
    retired = [password for password in _database()['passwords'] if password not in keep]
    if retired:
        _client('dynamodb').update_item(TableName=DB_TABLE, Key={'user': {'S': DB_USER}},
                                        UpdateExpression='DELETE passwords :old',
                                        ExpressionAttributeValues={':old': {'SS': retired}})
    marker = {
        'abandonedVersion': abandoned,
        'cancelReportedVersion': cancelled.get('VersionId'),
        'token': token,
        'steps': steps,
        'labels': stage_map(_secret_versions()),
        'pendingIsCurrent': bool(token) and stage_map(_secret_versions()).get('AWSCURRENT') == token,
        'currentLogsIn': _logs_in(current.get('password')),
        'retired': len(retired),
        'passwords': len(_database()['passwords']),
    }
    _marker('redrive-rotation', marker)
    verified, message = _check_redrive()
    return _result('redrive-rotation', REDRIVE_COMMAND, marker, verified, message, started)


RUNNERS = {
    'team-boundary': step_team_boundary,
    'delegated-roles': step_delegated_roles,
    'boundary-ceiling': step_boundary_ceiling,
    'chained-session': step_chained_session,
    'abac-decisions': step_abac_decisions,
    'rotate-db-credentials': step_rotate_db_credentials,
    'failed-rotation': step_failed_rotation,
    'redrive-rotation': step_redrive_rotation,
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
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError):
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
    """Remove the secret, function, table, queues, roles, and boundary policy."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or 'NonExistent' in code:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    sm = _client('secretsmanager')
    if _safe_call(lambda: sm.describe_secret(SecretId=SECRET)):
        attempt(f'secret:{SECRET}', lambda: sm.delete_secret(SecretId=SECRET, ForceDeleteWithoutRecovery=True))
    if _function():
        attempt(f'function:{FUNCTION}', lambda: _client('lambda').delete_function(FunctionName=FUNCTION))
    if _safe_call(lambda: _client('dynamodb').describe_table(TableName=DB_TABLE)):
        attempt(f'table:{DB_TABLE}', lambda: _client('dynamodb').delete_table(TableName=DB_TABLE))
    sqs = _client('sqs')
    for name in QUEUE_TAGS:
        url = _queue_url(name)
        if url:
            attempt(f'queue:{name}', lambda url=url: sqs.delete_queue(QueueUrl=url))
    iam = _client('iam')
    for role in (WORKER, UNBOUNDED, DEPLOYER, CI_ROLE, ABAC_ROLE, TEAM_ADMIN, FUNCTION_ROLE):
        if _role(role):
            if (_role(role) or {}).get('PermissionsBoundary'):
                attempt(f'boundary:{role}', lambda role=role: iam.delete_role_permissions_boundary(RoleName=role))
            for policy in _safe_call(lambda role=role: iam.list_role_policies(RoleName=role)['PolicyNames'], []):
                attempt(f'role-policy:{role}/{policy}', lambda role=role, policy=policy: iam.delete_role_policy(
                    RoleName=role, PolicyName=policy))
            attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))
    if _safe_call(lambda: iam.get_policy(PolicyArn=BOUNDARY_ARN)):
        for version in _safe_call(lambda: iam.list_policy_versions(PolicyArn=BOUNDARY_ARN)['Versions'], []):
            if not version['IsDefaultVersion']:
                attempt(f'policy-version:{version["VersionId"]}', lambda version=version: iam.delete_policy_version(
                    PolicyArn=BOUNDARY_ARN, VersionId=version['VersionId']))
        attempt(f'policy:{BOUNDARY}', lambda: iam.delete_policy(PolicyArn=BOUNDARY_ARN))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws secretsmanager delete-secret --secret-id {SECRET} --force-delete-without-recovery\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws dynamodb delete-table --table-name {DB_TABLE}\n'
            + ''.join(f'aws sqs delete-queue --queue-url <{name}>\n' for name in QUEUE_TAGS)
            + f'aws iam delete-role --role-name <each lab role>   # after removing its policies and boundary\n'
            f'aws iam delete-policy --policy-arn {BOUNDARY_ARN}'
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
            else 'The secret, function, table, queues, roles, and boundary policy are gone.',
        },
    }
