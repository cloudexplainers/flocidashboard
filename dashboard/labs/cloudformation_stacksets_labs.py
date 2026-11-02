"""Multi-Region baseline lab: roll a baseline out to two Regions with a CloudFormation StackSet, push an update to
every stack instance, deploy it to an organizational unit with service-managed permissions, build a network stack
around a Lambda-backed custom resource, protect that stack, find and fix drift, and publish an approved product in
AWS Service Catalog.

Floci 2.4.0 deploys StackSet instances into other Regions for real, propagates StackSet updates, deploys
service-managed StackSets to the accounts in an OU, runs Lambda-backed custom resources, and enforces termination
protection. It does not detect drift, does not enforce stack policies, ignores per-Region parameter overrides, does
not auto-deploy to accounts that join an OU later, and records Service Catalog provisioning without creating a
stack. The lab checks those rules itself and says so in each step.
"""

from __future__ import annotations

import fnmatch
import io
import json
import time
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudformation'
LAB_KEY = 'stacksets-baseline'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
REGIONS = ['us-east-1', 'us-west-2']
DRIFT_REGION = 'us-west-2'

STACK_SET = 'lab-baseline'
ORG_STACK_SET = 'lab-baseline-org'
ADMIN_ROLE = 'AWSCloudFormationStackSetAdministrationRole'
EXECUTION_ROLE = 'AWSCloudFormationStackSetExecutionRole'
CONFIG_PARAMETER = '/lab-baseline/config'
GUARDRAIL_PARAMETER = '/lab-baseline/org-guardrail'
AUDIT_LOG_GROUP = 'lab-baseline-audit'
ALERTS_TOPIC = 'lab-baseline-alerts'
OWNER = 'platform'
RETENTION_DAYS = 90

OU_NAME = 'lab-baseline-workloads'
MEMBER_ACCOUNT = 'lab-baseline-member'
MEMBER_EMAIL = 'lab-baseline-member@example.com'

FUNCTION = 'lab-baseline-cidr-allocator'
FUNCTION_ROLE = 'lab-baseline-cidr-allocator-role'
FUNCTION_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FUNCTION_ROLE}'
FUNCTION_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
NETWORK_STACK = 'lab-baseline-network'
NETWORK_ENVIRONMENT = 'prod'
CIDR_PARAMETER = '/lab-baseline/network/cidr'
RENAMED_CIDR_PARAMETER = '/lab-baseline/network/cidr-v2'
CIDR_EXPORT = 'lab-baseline-network-cidr'
CHANGE_SET = 'lab-baseline-rename-parameter'

CATALOG_BUCKET = 'lab-baseline-catalog'
PRODUCT_TEMPLATE_KEY = 'products/team-queue.json'
PORTFOLIO = 'lab-baseline-portfolio'
PRODUCT = 'lab-baseline-team-queue'
PRODUCT_VERSION = 'v1'
LAUNCH_ROLE = 'lab-baseline-catalog-launch'
DEVELOPER_ROLE = 'lab-baseline-developer'
PROVISIONED_PRODUCT = 'lab-baseline-team-a-queue'
TEAM_QUEUE = 'lab-baseline-team-a-jobs'

LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}

# The CIDR allocator hands every environment its own /16 so networks never overlap.
CIDR_BLOCKS = {'dev': '10.10.0.0/16', 'test': '10.20.0.0/16', 'prod': '10.30.0.0/16'}


def allocate_cidr(environment: str) -> str:
    return CIDR_BLOCKS.get(environment, '10.99.0.0/16')


def config_value(version: int, owner: str = OWNER, retention: int = RETENTION_DAYS) -> str:
    return json.dumps({'owner': owner, 'retentionDays': retention, 'version': version}, separators=(',', ':'))


def _baseline_template(version: int) -> dict[str, Any]:
    resources: dict[str, Any] = {
        'BaselineConfig': {'Type': 'AWS::SSM::Parameter', 'Properties': {
            'Name': CONFIG_PARAMETER, 'Type': 'String',
            'Description': 'Account baseline settings (multi-Region baseline lab)',
            'Value': {'Fn::Sub': '{"owner":"${Owner}","retentionDays":${RetentionDays},"version":' + str(version) + '}'},
        }},
        'AuditLogGroup': {'Type': 'AWS::Logs::LogGroup', 'Properties': {
            'LogGroupName': AUDIT_LOG_GROUP, 'RetentionInDays': {'Ref': 'RetentionDays'},
        }},
    }
    if version >= 2:
        resources['Alerts'] = {'Type': 'AWS::SNS::Topic', 'Properties': {'TopicName': ALERTS_TOPIC}}
    return {
        'AWSTemplateFormatVersion': '2010-09-09',
        'Description': f'Account baseline v{version} (multi-Region baseline lab)',
        'Parameters': {
            'Owner': {'Type': 'String', 'Default': OWNER},
            'RetentionDays': {'Type': 'Number', 'Default': RETENTION_DAYS, 'AllowedValues': [1, 7, 14, 30, 90, 365]},
        },
        'Resources': resources,
    }


BASELINE_V1 = _baseline_template(1)
BASELINE_V2 = _baseline_template(2)

GUARDRAIL_TEMPLATE = {
    'AWSTemplateFormatVersion': '2010-09-09',
    'Description': 'Guardrail settings every workload account gets (multi-Region baseline lab)',
    'Resources': {
        'Guardrail': {'Type': 'AWS::SSM::Parameter', 'Properties': {
            'Name': GUARDRAIL_PARAMETER, 'Type': 'String',
            'Value': json.dumps({'allowedRegions': REGIONS, 'requireEncryption': True}, separators=(',', ':')),
        }},
    },
}


def _network_template(parameter_name: str = CIDR_PARAMETER) -> dict[str, Any]:
    return {
        'AWSTemplateFormatVersion': '2010-09-09',
        'Description': 'Network allocation backed by a custom resource (multi-Region baseline lab)',
        'Resources': {
            'Allocation': {'Type': 'Custom::CidrAllocation', 'Properties': {
                'ServiceToken': FUNCTION_ARN, 'Environment': NETWORK_ENVIRONMENT,
            }},
            'CidrParameter': {'Type': 'AWS::SSM::Parameter', 'Properties': {
                'Name': parameter_name, 'Type': 'String', 'Value': {'Fn::GetAtt': ['Allocation', 'Cidr']},
            }},
        },
        'Outputs': {
            'VpcCidr': {'Value': {'Fn::GetAtt': ['Allocation', 'Cidr']}, 'Export': {'Name': CIDR_EXPORT}},
        },
    }


NETWORK_TEMPLATE = _network_template()
RENAMED_NETWORK_TEMPLATE = _network_template(RENAMED_CIDR_PARAMETER)

STACK_POLICY = {
    'Statement': [
        {'Effect': 'Allow', 'Action': 'Update:*', 'Principal': '*', 'Resource': '*'},
        {'Effect': 'Deny', 'Action': ['Update:Replace', 'Update:Delete'], 'Principal': '*',
         'Resource': 'LogicalResourceId/CidrParameter'},
    ],
}

PRODUCT_TEMPLATE = {
    'AWSTemplateFormatVersion': '2010-09-09',
    'Description': 'An approved, encrypted SQS queue with a dead-letter queue (Service Catalog product)',
    'Parameters': {'QueueName': {'Type': 'String'}},
    'Resources': {
        'DeadLetters': {'Type': 'AWS::SQS::Queue', 'Properties': {
            'QueueName': {'Fn::Sub': '${QueueName}-dlq'}, 'SqsManagedSseEnabled': True}},
        'Queue': {'Type': 'AWS::SQS::Queue', 'Properties': {
            'QueueName': {'Ref': 'QueueName'}, 'SqsManagedSseEnabled': True,
            'RedrivePolicy': {'deadLetterTargetArn': {'Fn::GetAtt': ['DeadLetters', 'Arn']}, 'maxReceiveCount': 5}}},
    },
}

CUSTOM_RESOURCE_SOURCE = '''import json
import urllib.request

BLOCKS = %s


def handler(event, context):
    print(json.dumps({key: event.get(key) for key in ("RequestType", "LogicalResourceId", "ResourceProperties")}))
    environment = event.get("ResourceProperties", {}).get("Environment", "dev")
    cidr = BLOCKS.get(environment, "10.99.0.0/16")
    body = {
        "Status": "SUCCESS",
        "PhysicalResourceId": "cidr-" + environment,
        "StackId": event["StackId"],
        "RequestId": event["RequestId"],
        "LogicalResourceId": event["LogicalResourceId"],
        "Data": {"Cidr": cidr, "Environment": environment},
    }
    request = urllib.request.Request(event["ResponseURL"], data=json.dumps(body).encode(), method="PUT",
                                     headers={"Content-Type": ""})
    urllib.request.urlopen(request, timeout=10)
    return body
''' % json.dumps(CIDR_BLOCKS)

LAMBDA_TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
ADMIN_TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'Service': 'cloudformation.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
EXECUTION_TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'AWS': f'arn:aws:iam::{ACCOUNT}:root'}, 'Action': 'sts:AssumeRole'}]}
ADMIN_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': 'sts:AssumeRole', 'Resource': f'arn:aws:iam::*:role/{EXECUTION_ROLE}'}]}
LAUNCH_TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'Service': 'servicecatalog.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
LAUNCH_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': ['cloudformation:*', 'sqs:*'], 'Resource': '*'},
    {'Effect': 'Allow', 'Action': 's3:GetObject', 'Resource': f'arn:aws:s3:::{CATALOG_BUCKET}/*'}]}
DEVELOPER_TRUST = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Principal': {'AWS': f'arn:aws:iam::{ACCOUNT}:root'}, 'Action': 'sts:AssumeRole'}]}
DEVELOPER_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': ['servicecatalog:SearchProducts', 'servicecatalog:DescribeProduct',
                                   'servicecatalog:ProvisionProduct', 'servicecatalog:DescribeRecord'], 'Resource': '*'}]}

OPERATION_PREFERENCES = {'RegionConcurrencyType': 'SEQUENTIAL', 'RegionOrder': REGIONS,
                         'MaxConcurrentCount': 1, 'FailureToleranceCount': 0}

# ── Commands ─────────────────────────────────────────────────────────────────

CREATE_COMMAND = (
    f'aws iam create-role --role-name {ADMIN_ROLE} --assume-role-policy-document file://cfn-trust.json\n'
    f'aws iam create-role --role-name {EXECUTION_ROLE} --assume-role-policy-document file://admin-account-trust.json\n'
    f'aws cloudformation create-stack-set --stack-set-name {STACK_SET} --template-body file://baseline-v1.json '
    f'--permission-model SELF_MANAGED --administration-role-arn arn:aws:iam::{ACCOUNT}:role/{ADMIN_ROLE} '
    f'--execution-role-name {EXECUTION_ROLE}\n'
    f'aws cloudformation describe-stack-set --stack-set-name {STACK_SET}'
)
DEPLOY_COMMAND = (
    f'aws cloudformation create-stack-instances --stack-set-name {STACK_SET} --accounts {ACCOUNT} '
    f'--regions {" ".join(REGIONS)} --operation-preferences RegionConcurrencyType=SEQUENTIAL,'
    f'RegionOrder={",".join(REGIONS)},MaxConcurrentCount=1,FailureToleranceCount=0\n'
    f'aws cloudformation describe-stack-set-operation --stack-set-name {STACK_SET} --operation-id <id>\n'
    f'aws cloudformation list-stack-instances --stack-set-name {STACK_SET}\n'
    f'aws ssm get-parameter --name {CONFIG_PARAMETER} --region us-west-2'
)
UPDATE_COMMAND = (
    f'aws cloudformation update-stack-set --stack-set-name {STACK_SET} --template-body file://baseline-v2.json '
    '--operation-preferences RegionConcurrencyType=SEQUENTIAL,MaxConcurrentCount=1,FailureToleranceCount=0\n'
    f'aws cloudformation list-stack-set-operations --stack-set-name {STACK_SET}\n'
    'aws sns list-topics --region us-west-2'
)
ORG_COMMAND = (
    'aws organizations create-organization --feature-set ALL   # if the account is not in one yet\n'
    'aws cloudformation activate-organizations-access\n'
    f'aws organizations create-organizational-unit --parent-id <root-id> --name {OU_NAME}\n'
    f'aws organizations create-account --email {MEMBER_EMAIL} --account-name {MEMBER_ACCOUNT}\n'
    'aws organizations move-account --account-id <member-id> --source-parent-id <root-id> '
    '--destination-parent-id <ou-id>\n'
    f'aws cloudformation create-stack-set --stack-set-name {ORG_STACK_SET} --template-body file://guardrail.json '
    '--permission-model SERVICE_MANAGED --auto-deployment Enabled=true,RetainStacksOnAccountRemoval=false\n'
    f'aws cloudformation create-stack-instances --stack-set-name {ORG_STACK_SET} '
    f'--deployment-targets OrganizationalUnitIds=<ou-id> --regions {" ".join(REGIONS)}'
)
CUSTOM_RESOURCE_COMMAND = (
    f'aws iam create-role --role-name {FUNCTION_ROLE} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime python3.12 --handler allocator.handler '
    f'--role {FUNCTION_ROLE_ARN} --zip-file fileb://allocator.zip\n'
    f'aws cloudformation create-stack --stack-name {NETWORK_STACK} --template-body file://network.json\n'
    f'aws cloudformation describe-stacks --stack-name {NETWORK_STACK} --query "Stacks[0].Outputs"\n'
    'aws cloudformation list-exports'
)
PROTECT_COMMAND = (
    f'aws cloudformation update-termination-protection --stack-name {NETWORK_STACK} '
    '--enable-termination-protection\n'
    f'aws cloudformation delete-stack --stack-name {NETWORK_STACK}   # refused\n'
    f'aws cloudformation set-stack-policy --stack-name {NETWORK_STACK} --stack-policy-body file://stack-policy.json\n'
    f'aws cloudformation create-change-set --stack-name {NETWORK_STACK} --change-set-name {CHANGE_SET} '
    '--template-body file://network-renamed.json\n'
    f'aws cloudformation describe-change-set --stack-name {NETWORK_STACK} --change-set-name {CHANGE_SET}'
)
DRIFT_COMMAND = (
    f'aws ssm put-parameter --name {CONFIG_PARAMETER} --overwrite --value \'{{"owner":"someone"}}\' '
    f'--region {DRIFT_REGION}   # an out-of-band change\n'
    f'aws cloudformation detect-stack-drift --stack-name StackSet-{STACK_SET}-<id> --region {DRIFT_REGION}\n'
    f'aws cloudformation detect-stack-set-drift --stack-set-name {STACK_SET}\n'
    f'aws ssm put-parameter --name {CONFIG_PARAMETER} --overwrite --value <template value> --region {DRIFT_REGION}'
)
CATALOG_COMMAND = (
    f'aws s3 cp team-queue.json s3://{CATALOG_BUCKET}/{PRODUCT_TEMPLATE_KEY}\n'
    f'aws servicecatalog create-portfolio --display-name {PORTFOLIO} --provider-name platform\n'
    f'aws servicecatalog create-product --name {PRODUCT} --owner platform --product-type CLOUD_FORMATION_TEMPLATE '
    f'--provisioning-artifact-parameters Name={PRODUCT_VERSION},Type=CLOUD_FORMATION_TEMPLATE,'
    f'Info={{LoadTemplateFromURL=https://{CATALOG_BUCKET}.s3.amazonaws.com/{PRODUCT_TEMPLATE_KEY}}}\n'
    'aws servicecatalog associate-product-with-portfolio --product-id <prod-id> --portfolio-id <port-id>\n'
    'aws servicecatalog create-constraint --type LAUNCH --portfolio-id <port-id> --product-id <prod-id> '
    f'--parameters \'{{"RoleArn": "arn:aws:iam::{ACCOUNT}:role/{LAUNCH_ROLE}"}}\'\n'
    'aws servicecatalog associate-principal-with-portfolio --portfolio-id <port-id> '
    f'--principal-arn arn:aws:iam::{ACCOUNT}:role/{DEVELOPER_ROLE} --principal-type IAM\n'
    f'aws servicecatalog provision-product --product-id <prod-id> --provisioning-artifact-id <pa-id> '
    f'--provisioned-product-name {PROVISIONED_PRODUCT} --provisioning-parameters Key=QueueName,Value={TEAM_QUEUE}'
)

FLOCI = 'How Floci emulates this: '

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Roll out a multi-Region baseline with StackSets, custom resources, and Service Catalog',
    'description': (
        'Deploy one baseline template to two Regions with a CloudFormation StackSet, push an update to every stack '
        'instance, roll a guardrail out to an organizational unit with service-managed permissions, build a network '
        'stack around a Lambda-backed custom resource, protect it with termination protection and a stack policy, '
        'find and fix drift, and publish an approved product in AWS Service Catalog.'
    ),
    'steps': [
        {
            'key': 'create-stack-set',
            'title': 'Create a self-managed StackSet for the account baseline',
            'command': CREATE_COMMAND,
            'explanation': (
                'A StackSet holds one template and deploys it as stack instances, one per account and Region. With '
                'self-managed permissions, the administrator account uses the '
                f'{ADMIN_ROLE} role to assume {EXECUTION_ROLE} in each target account, so the lab creates both. The '
                f'baseline is a configuration parameter ({CONFIG_PARAMETER}) and an audit log group with '
                f'{RETENTION_DAYS}-day retention. '
                + FLOCI + 'the StackSet and both roles are stored as on AWS.'
            ),
        },
        {
            'key': 'deploy-regions',
            'title': 'Deploy stack instances to two Regions',
            'command': DEPLOY_COMMAND,
            'explanation': (
                'CreateStackInstances starts one operation that deploys to every account and Region you list. '
                'Operation preferences control the blast radius: Regions one after another (SEQUENTIAL), one account '
                'at a time, and stop at the first failure (failure tolerance 0). ParameterOverrides can give one '
                'Region different values. '
                + FLOCI + 'each Region gets a real stack, so the parameter and log group exist in us-east-1 and '
                'us-west-2. Floci ignores parameter overrides, so the lab uses the same values everywhere.'
            ),
        },
        {
            'key': 'update-stack-set',
            'title': 'Push a baseline update to every stack instance',
            'command': UPDATE_COMMAND,
            'explanation': (
                'Change the template once and UpdateStackSet rolls it to every stack instance with the same '
                f'operation preferences. Version 2 adds the {ALERTS_TOPIC} SNS topic and bumps the config version. '
                + FLOCI + 'the update operation runs and the new topic appears in both Regions.'
            ),
        },
        {
            'key': 'org-rollout',
            'title': 'Roll a guardrail out to an organizational unit',
            'command': ORG_COMMAND,
            'explanation': (
                'With service-managed permissions, CloudFormation uses AWS Organizations instead of roles you create: '
                'activate trusted access, then target an OU instead of account IDs. Automatic deployment adds the '
                'stack to accounts that join the OU later and removes it from accounts that leave. '
                + FLOCI + 'trusted access is required as on AWS, and the guardrail stack instances are created for '
                f'every account in {OU_NAME} in both Regions. Floci does not deploy to accounts that join the OU '
                'later, and it cannot sign in as the member account, so the lab reads the stack instances.'
            ),
        },
        {
            'key': 'custom-resource',
            'title': 'Allocate a network CIDR with a Lambda-backed custom resource',
            'command': CUSTOM_RESOURCE_COMMAND,
            'explanation': (
                'A custom resource lets a template call your own code. CloudFormation invokes the function in '
                'ServiceToken with Create, Update or Delete and a presigned ResponseURL; the function does its work '
                'and PUTs SUCCESS or FAILED with data that Fn::GetAtt can read. Here it hands each environment a '
                'non-overlapping /16. The stack exports the CIDR for other stacks to import. '
                + FLOCI + 'the function runs in a real container and its response completes the stack.'
            ),
        },
        {
            'key': 'protect-stack',
            'title': 'Protect the network stack',
            'command': PROTECT_COMMAND,
            'explanation': (
                'Termination protection blocks DeleteStack. A stack policy decides which updates are allowed per '
                'resource: this one allows everything except replacing or deleting the CIDR parameter, so a rename '
                '(which replaces it) fails. Review replacements in a change set before executing. '
                + FLOCI + 'termination protection is enforced. Stack policies are stored but not enforced, so the lab '
                'evaluates the policy against the change set itself and does not execute it.'
            ),
        },
        {
            'key': 'detect-drift',
            'title': 'Find and fix drift in one Region',
            'command': DRIFT_COMMAND,
            'explanation': (
                'Drift is a difference between a stack\'s template and the live resource, usually from a manual '
                'change. Drift detection (per stack, or per StackSet across every instance) reports IN_SYNC or '
                'DRIFTED with the property differences. Fix it by restoring the setting or by updating the template '
                f'to match. The step changes {CONFIG_PARAMETER} and the log group retention in {DRIFT_REGION} by hand. '
                + FLOCI + 'drift detection is not supported, so the lab compares each Region\'s live settings with '
                'the template, then restores them and checks again.'
            ),
        },
        {
            'key': 'service-catalog',
            'title': 'Publish an approved product in Service Catalog',
            'command': CATALOG_COMMAND,
            'explanation': (
                'Service Catalog lets a platform team publish approved templates as products in a portfolio. A '
                'launch constraint names the role that provisions the product, so a developer who may not create '
                'queues directly can still launch the approved one. '
                + FLOCI + 'the portfolio, product, constraint and principal are stored and the provision request '
                'returns a SUCCEEDED record, but no stack or queue is created, so the lab says what AWS would create.'
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


def _not_found(exc: ClientError) -> bool:
    code = _code(exc)
    return 'NotFound' in code or 'NoSuch' in code or code in (
        'ValidationError', 'StackSetNotFoundException', 'ParameterNotFound', 'ResourceNotFoundException')


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


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError):  # KeyError: Floci answers GetStackPolicy without a result element
        return default


# ── Pure logic ───────────────────────────────────────────────────────────────

def _as_list(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value or [])


def stack_policy_decision(policy: dict[str, Any], action: str, logical_id: str) -> str:
    """Evaluate a stack policy like CloudFormation: an explicit Deny wins, then an Allow, else deny by default."""
    allowed = False
    for statement in policy.get('Statement', []):
        actions = _as_list(statement.get('Action')) or ['Update:*']
        resources = _as_list(statement.get('Resource')) or ['*']
        if not any(fnmatch.fnmatchcase(action, pattern) for pattern in actions):
            continue
        if not any(pattern == '*' or fnmatch.fnmatchcase(f'LogicalResourceId/{logical_id}', pattern)
                   for pattern in resources):
            continue
        if statement.get('Effect') == 'Deny':
            return 'Deny'
        if statement.get('Effect') == 'Allow':
            allowed = True
    return 'Allow' if allowed else 'Deny'


def update_action(change: dict[str, Any]) -> str | None:
    """Map a change set resource change to the stack policy action it needs (None when no policy applies)."""
    action = change.get('Action')
    if action == 'Modify':
        return 'Update:Replace' if str(change.get('Replacement')) in ('True', 'Conditional') else 'Update:Modify'
    if action == 'Remove':
        return 'Update:Delete'
    return None


def evaluate_change_set(policy: dict[str, Any], changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for change in changes:
        needed = update_action(change)
        rows.append({'LogicalId': change.get('LogicalResourceId'), 'Action': change.get('Action'),
                     'Replacement': change.get('Replacement'), 'PolicyAction': needed,
                     'Decision': stack_policy_decision(policy, needed, change.get('LogicalResourceId', ''))
                     if needed else 'Allow'})
    return rows


def compare_baseline(expected: dict[str, Any], live: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one drift row per property, as drift detection reports them."""
    rows = []
    for name in sorted(expected):
        status = 'IN_SYNC' if live.get(name) == expected[name] else 'MODIFIED'
        if live.get(name) is None:
            status = 'DELETED'
        rows.append({'Property': name, 'Expected': expected[name], 'Actual': live.get(name), 'Status': status})
    return rows


# ── Lookups ──────────────────────────────────────────────────────────────────

def _stack_set(name: str = STACK_SET) -> dict[str, Any] | None:
    try:
        stack_set = _client('cloudformation').describe_stack_set(StackSetName=name)['StackSet']
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    return stack_set if stack_set.get('Status') != 'DELETED' else None


def _instances(name: str = STACK_SET) -> list[dict[str, Any]]:
    try:
        return _client('cloudformation').list_stack_instances(StackSetName=name).get('Summaries', [])
    except ClientError as exc:
        if _not_found(exc):
            return []
        raise


def _operations(name: str = STACK_SET) -> list[dict[str, Any]]:
    try:
        return _client('cloudformation').list_stack_set_operations(StackSetName=name).get('Summaries', [])
    except ClientError as exc:
        if _not_found(exc):
            return []
        raise


def _wait_operation(name: str, operation_id: str) -> dict[str, Any]:
    cf = _client('cloudformation')
    result: dict[str, Any] = {}

    def done() -> bool:
        result.update(cf.describe_stack_set_operation(StackSetName=name, OperationId=operation_id)['StackSetOperation'])
        return result.get('Status') in ('SUCCEEDED', 'FAILED', 'STOPPED')
    _wait(done, timeout=60)
    return result


def _parameter(name: str, region: str) -> str | None:
    try:
        return _client('ssm', region).get_parameter(Name=name)['Parameter']['Value']
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise


def _retention(region: str) -> int | None:
    groups = _client('logs', region).describe_log_groups(logGroupNamePrefix=AUDIT_LOG_GROUP).get('logGroups', [])
    group = next((item for item in groups if item.get('logGroupName') == AUDIT_LOG_GROUP), None)
    return group.get('retentionInDays') if group else None


def _topic_exists(region: str) -> bool:
    topics = _client('sns', region).list_topics().get('Topics', [])
    return any(topic['TopicArn'].endswith(f':{ALERTS_TOPIC}') for topic in topics)


def _live_baseline(region: str) -> dict[str, Any]:
    return {'ConfigValue': _parameter(CONFIG_PARAMETER, region), 'AuditLogRetentionDays': _retention(region)}


def _expected_baseline() -> dict[str, Any]:
    version = 2 if _template_version() >= 2 else 1
    return {'ConfigValue': config_value(version), 'AuditLogRetentionDays': RETENTION_DAYS}


def _template_version() -> int:
    stack_set = _stack_set() or {}
    try:
        body = json.loads(stack_set.get('TemplateBody') or '{}')
    except ValueError:
        return 0
    return 2 if 'Alerts' in body.get('Resources', {}) else (1 if body else 0)


def _role_exists(name: str) -> bool:
    try:
        _client('iam').get_role(RoleName=name)
        return True
    except ClientError as exc:
        if _not_found(exc):
            return False
        raise


def _ensure_role(name: str, trust: dict[str, Any], description: str, policy: dict[str, Any] | None = None,
                 managed: str | None = None) -> None:
    iam = _client('iam')
    if not _role_exists(name):
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description,
                        Tags=[LAB_TAG])
    if policy:
        iam.put_role_policy(RoleName=name, PolicyName=f'{name}-policy', PolicyDocument=json.dumps(policy))
    if managed:
        iam.attach_role_policy(RoleName=name, PolicyArn=managed)


def _stack(name: str = NETWORK_STACK) -> dict[str, Any] | None:
    try:
        stacks = _client('cloudformation').describe_stacks(StackName=name).get('Stacks', [])
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    live = [item for item in stacks if item.get('StackStatus') != 'DELETE_COMPLETE']
    return live[0] if live else None


def _wait_stack(name: str = NETWORK_STACK) -> dict[str, Any] | None:
    _wait(lambda: not ((_stack(name) or {}).get('StackStatus') or '').endswith('IN_PROGRESS'), timeout=120)
    return _stack(name)


def _outputs(stack: dict[str, Any] | None) -> dict[str, str]:
    return {item['OutputKey']: item.get('OutputValue') for item in (stack or {}).get('Outputs', []) or []}


def _change_set() -> dict[str, Any] | None:
    try:
        return _client('cloudformation').describe_change_set(StackName=NETWORK_STACK, ChangeSetName=CHANGE_SET)
    except ClientError as exc:
        if _not_found(exc) or 'ChangeSetNotFound' in _code(exc):
            return None
        raise


def _function() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_configuration(FunctionName=FUNCTION)
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('allocator.py', date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, CUSTOM_RESOURCE_SOURCE)
    return buffer.getvalue()


def _organization() -> dict[str, Any] | None:
    try:
        return _client('organizations').describe_organization()['Organization']
    except ClientError as exc:
        if _code(exc) == 'AWSOrganizationsNotInUseException':
            return None
        raise


def _root() -> dict[str, Any] | None:
    roots = _safe_call(lambda: _client('organizations').list_roots().get('Roots', []), []) or []
    return roots[0] if roots else None


def _ou() -> dict[str, Any] | None:
    root = _root()
    if not root:
        return None
    units = _client('organizations').list_organizational_units_for_parent(ParentId=root['Id']).get(
        'OrganizationalUnits', [])
    return next((unit for unit in units if unit['Name'] == OU_NAME), None)


def _member() -> dict[str, Any] | None:
    accounts = _safe_call(lambda: _client('organizations').list_accounts().get('Accounts', []), []) or []
    return next((item for item in accounts if item.get('Name') == MEMBER_ACCOUNT and item.get('Status') == 'ACTIVE'),
                None)


def _portfolio() -> dict[str, Any] | None:
    portfolios = _client('servicecatalog').list_portfolios().get('PortfolioDetails', [])
    return next((item for item in portfolios if item.get('DisplayName') == PORTFOLIO), None)


def _product() -> dict[str, Any] | None:
    products = _client('servicecatalog').search_products_as_admin().get('ProductViewDetails', [])
    return next((item['ProductViewSummary'] for item in products
                 if item['ProductViewSummary'].get('Name') == PRODUCT), None)


def _provisioned() -> dict[str, Any] | None:
    # Search rather than DescribeProvisionedProduct by name: Floci keeps terminated products and answers with the
    # first one of that name.
    products = _client('servicecatalog').search_provisioned_products(
        Filters={'SearchQuery': [f'name:{PROVISIONED_PRODUCT}']}).get('ProvisionedProducts', [])
    live = [item for item in products if item.get('Name') == PROVISIONED_PRODUCT and item.get('Status') != 'TERMINATED']
    return live[-1] if live else None


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_create() -> tuple[bool, str]:
    stack_set = _stack_set()
    if not stack_set:
        return False, f'Create the {STACK_SET} StackSet.'
    if not (_role_exists(ADMIN_ROLE) and _role_exists(EXECUTION_ROLE)):
        return False, f'Create the {ADMIN_ROLE} and {EXECUTION_ROLE} roles.'
    return True, (f'{STACK_SET} is {stack_set.get("Status")} with the self-managed permission model and the baseline '
                  f'template v{_template_version()}.')


def _deployed_regions() -> list[str]:
    return sorted(item['Region'] for item in _instances()
                  if item.get('Account') == ACCOUNT and item.get('Status') == 'CURRENT')


def _check_deploy() -> tuple[bool, str]:
    if not _stack_set():
        return False, f'Create the {STACK_SET} StackSet first.'
    regions = _deployed_regions()
    missing = [region for region in REGIONS if region not in regions]
    if missing:
        return False, f'Deploy stack instances to {", ".join(missing)}.'
    absent = [region for region in REGIONS if _parameter(CONFIG_PARAMETER, region) is None]
    if absent:
        return False, f'{CONFIG_PARAMETER} is missing in {", ".join(absent)}.'
    return True, f'Stack instances are CURRENT in {" and ".join(REGIONS)}, and {CONFIG_PARAMETER} exists in both.'


def _check_update() -> tuple[bool, str]:
    if not _check_deploy()[0]:
        return False, 'Deploy the stack instances first.'
    if _template_version() < 2:
        return False, f'Update {STACK_SET} to the v2 template.'
    missing = [region for region in REGIONS if not _topic_exists(region)]
    if missing:
        return False, f'{ALERTS_TOPIC} is missing in {", ".join(missing)}.'
    return True, f'The v2 baseline reached every stack instance: {ALERTS_TOPIC} exists in both Regions.'


def _check_org() -> tuple[bool, str]:
    if not _stack_set(ORG_STACK_SET):
        return False, f'Create the service-managed {ORG_STACK_SET} StackSet.'
    member = _member()
    if not member:
        return False, f'Create the {MEMBER_ACCOUNT} account in {OU_NAME}.'
    regions = sorted(item['Region'] for item in _instances(ORG_STACK_SET)
                     if item.get('Account') == member['Id'] and item.get('Status') == 'CURRENT')
    if regions != sorted(REGIONS):
        return False, f'Deploy {ORG_STACK_SET} to {OU_NAME} in {" and ".join(REGIONS)}.'
    return True, f'{ORG_STACK_SET} has CURRENT stack instances for {MEMBER_ACCOUNT} ({member["Id"]}) in both Regions.'


def _check_custom() -> tuple[bool, str]:
    stack = _stack()
    if not stack or stack.get('StackStatus') not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
        return False, f'Create the {NETWORK_STACK} stack.'
    expected = allocate_cidr(NETWORK_ENVIRONMENT)
    cidr = _outputs(stack).get('VpcCidr')
    if cidr != expected:
        return False, f'{NETWORK_STACK} should output VpcCidr {expected}, not {cidr}.'
    return True, f'The custom resource allocated {cidr} to {NETWORK_ENVIRONMENT}, exported as {CIDR_EXPORT}.'


def _check_protect() -> tuple[bool, str]:
    if not _check_custom()[0]:
        return False, f'Create the {NETWORK_STACK} stack first.'
    if not (_stack() or {}).get('EnableTerminationProtection'):
        return False, f'Turn on termination protection for {NETWORK_STACK}.'
    marker = _marked('protect-stack')
    if not marker:
        return False, 'Run this step to set the stack policy and evaluate the change set.'
    return True, marker['message']


def _check_drift() -> tuple[bool, str]:
    if not _check_deploy()[0]:
        return False, 'Deploy the stack instances first.'
    expected = _expected_baseline()
    drifted = [region for region in REGIONS
               if any(row['Status'] != 'IN_SYNC' for row in compare_baseline(expected, _live_baseline(region)))]
    if drifted:
        return False, f'{", ".join(drifted)} still drifted from the template.'
    marker = _marked('detect-drift')
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_catalog() -> tuple[bool, str]:
    portfolio, product = _portfolio(), _product()
    if not portfolio or not product:
        return False, f'Create the {PORTFOLIO} portfolio and the {PRODUCT} product.'
    sc = _client('servicecatalog')
    constraints = sc.list_constraints_for_portfolio(PortfolioId=portfolio['Id']).get('ConstraintDetails', [])
    if not any(item.get('Type') == 'LAUNCH' for item in constraints):
        return False, f'Add a launch constraint with the {LAUNCH_ROLE} role.'
    principals = sc.list_principals_for_portfolio(PortfolioId=portfolio['Id']).get('Principals', [])
    if not any(item.get('PrincipalARN', '').endswith(f':role/{DEVELOPER_ROLE}') for item in principals):
        return False, f'Give {DEVELOPER_ROLE} access to {PORTFOLIO}.'
    marker = _marked('service-catalog')
    if not marker:
        return False, f'Provision {PROVISIONED_PRODUCT}.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_stack_set() -> dict[str, Any]:
    started = time.perf_counter()
    _ensure_role(ADMIN_ROLE, ADMIN_TRUST, 'Lets CloudFormation StackSets assume the execution role',
                 policy=ADMIN_POLICY)
    _ensure_role(EXECUTION_ROLE, EXECUTION_TRUST, 'Deploys StackSet instances in this account',
                 managed='arn:aws:iam::aws:policy/AdministratorAccess')
    cf = _client('cloudformation')
    if not _stack_set():
        cf.create_stack_set(StackSetName=STACK_SET, TemplateBody=json.dumps(BASELINE_V1),
                            Description='Account baseline (multi-Region baseline lab)',
                            PermissionModel='SELF_MANAGED',
                            AdministrationRoleARN=f'arn:aws:iam::{ACCOUNT}:role/{ADMIN_ROLE}',
                            ExecutionRoleName=EXECUTION_ROLE, Tags=[LAB_TAG])
    stack_set = _stack_set() or {}
    response = {'StackSet': {key: stack_set.get(key) for key in (
        'StackSetName', 'Status', 'PermissionModel', 'AdministrationRoleARN', 'ExecutionRoleName')},
        'Template': BASELINE_V1 if _template_version() < 2 else 'already updated to v2'}
    verified, message = _check_create()
    return _result('create-stack-set', CREATE_COMMAND, response, verified, message, started)


def step_deploy_regions() -> dict[str, Any]:
    started = time.perf_counter()
    if not _stack_set():
        return _result('deploy-regions', DEPLOY_COMMAND, {}, False, f'Create the {STACK_SET} StackSet first.', started)
    missing = [region for region in REGIONS if region not in _deployed_regions()]
    operation: dict[str, Any] = {}
    if missing:
        operation_id = _client('cloudformation').create_stack_instances(
            StackSetName=STACK_SET, Accounts=[ACCOUNT], Regions=missing,
            OperationPreferences=OPERATION_PREFERENCES)['OperationId']
        operation = _wait_operation(STACK_SET, operation_id)
    response = {
        'Operation': {key: operation.get(key) for key in ('OperationId', 'Action', 'Status')} if operation
        else 'Both Regions were already deployed.',
        'OperationPreferences': OPERATION_PREFERENCES,
        'StackInstances': [{key: item.get(key) for key in ('Account', 'Region', 'Status', 'StackId')}
                           for item in _instances()],
        'LiveBaseline': {region: _live_baseline(region) for region in REGIONS},
    }
    verified, message = _check_deploy()
    return _result('deploy-regions', DEPLOY_COMMAND, response, verified, message, started)


def step_update_stack_set() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_deploy()[0]:
        return _result('update-stack-set', UPDATE_COMMAND, {}, False, 'Deploy the stack instances first.', started)
    operation: dict[str, Any] = {}
    if _template_version() < 2:
        operation_id = _client('cloudformation').update_stack_set(
            StackSetName=STACK_SET, TemplateBody=json.dumps(BASELINE_V2),
            Description='Account baseline v2 (multi-Region baseline lab)',
            OperationPreferences=OPERATION_PREFERENCES)['OperationId']
        operation = _wait_operation(STACK_SET, operation_id)
        _wait(lambda: all(_topic_exists(region) for region in REGIONS), timeout=30)
    response = {
        'Operation': {key: operation.get(key) for key in ('OperationId', 'Action', 'Status')} if operation
        else 'The StackSet already runs v2.',
        'Operations': [{key: item.get(key) for key in ('Action', 'Status')} for item in _operations()],
        'AlertsTopic': {region: _topic_exists(region) for region in REGIONS},
        'ConfigValue': {region: _parameter(CONFIG_PARAMETER, region) for region in REGIONS},
    }
    verified, message = _check_update()
    return _result('update-stack-set', UPDATE_COMMAND, response, verified, message, started)


def step_org_rollout() -> dict[str, Any]:
    started = time.perf_counter()
    organizations, cf = _client('organizations'), _client('cloudformation')
    created_org = False
    if not _organization():
        organizations.create_organization(FeatureSet='ALL')
        created_org = True
    root = _root()
    if not root:
        return _result('org-rollout', ORG_COMMAND, {}, False, 'The organization has no root.', started)
    if created_org:
        _safe_call(lambda: organizations.tag_resource(ResourceId=root['Id'], Tags=[LAB_TAG]))
    cf.activate_organizations_access()
    ou = _ou()
    if not ou:
        ou = organizations.create_organizational_unit(ParentId=root['Id'], Name=OU_NAME,
                                                      Tags=[LAB_TAG])['OrganizationalUnit']
    member = _member()
    if not member:
        status = organizations.create_account(Email=MEMBER_EMAIL, AccountName=MEMBER_ACCOUNT,
                                              Tags=[LAB_TAG])['CreateAccountStatus']
        _wait(lambda: organizations.describe_create_account_status(
            CreateAccountRequestId=status['Id'])['CreateAccountStatus']['State'] != 'IN_PROGRESS', timeout=30)
        member = _member()
    if not member:
        return _result('org-rollout', ORG_COMMAND, {}, False, f'{MEMBER_ACCOUNT} could not be created.', started)
    parents = organizations.list_parents(ChildId=member['Id']).get('Parents', [])
    if parents and parents[0]['Id'] != ou['Id']:
        organizations.move_account(AccountId=member['Id'], SourceParentId=parents[0]['Id'], DestinationParentId=ou['Id'])
    if not _stack_set(ORG_STACK_SET):
        cf.create_stack_set(StackSetName=ORG_STACK_SET, TemplateBody=json.dumps(GUARDRAIL_TEMPLATE),
                            Description='Workload guardrails (multi-Region baseline lab)',
                            PermissionModel='SERVICE_MANAGED',
                            AutoDeployment={'Enabled': True, 'RetainStacksOnAccountRemoval': False}, Tags=[LAB_TAG])
    operation: dict[str, Any] = {}
    deployed = {item['Region'] for item in _instances(ORG_STACK_SET) if item.get('Account') == member['Id']}
    if not set(REGIONS) <= deployed:
        operation_id = cf.create_stack_instances(
            StackSetName=ORG_STACK_SET, DeploymentTargets={'OrganizationalUnitIds': [ou['Id']]}, Regions=REGIONS,
            OperationPreferences={'RegionConcurrencyType': 'PARALLEL', 'FailureTolerancePercentage': 0,
                                  'MaxConcurrentPercentage': 100})['OperationId']
        operation = _wait_operation(ORG_STACK_SET, operation_id)
    response = {
        'TrustedAccess': _safe_call(lambda: cf.describe_organizations_access().get('Status')),
        'OrganizationalUnit': {'Id': ou['Id'], 'Name': ou['Name']},
        'MemberAccount': {'Id': member['Id'], 'Name': member['Name']},
        'Operation': {key: operation.get(key) for key in ('OperationId', 'Action', 'Status')} if operation
        else 'The OU was already deployed.',
        'StackInstances': [{key: item.get(key) for key in ('Account', 'Region', 'Status', 'OrganizationalUnitId')}
                           for item in _instances(ORG_STACK_SET)],
        'AutoDeploymentTargets': _safe_call(lambda: cf.list_stack_set_auto_deployment_targets(
            StackSetName=ORG_STACK_SET).get('Summaries', []), []),
        'Note': ('Floci does not deploy to accounts that join the OU later; on AWS, automatic deployment would. '
                 'Floci cannot sign in as the member account, so the stack instances are the evidence.'),
    }
    verified, message = _check_org()
    return _result('org-rollout', ORG_COMMAND, response, verified, message, started)


def step_custom_resource() -> dict[str, Any]:
    started = time.perf_counter()
    _ensure_role(FUNCTION_ROLE, LAMBDA_TRUST, 'CIDR allocator custom resource (multi-Region baseline lab)',
                 managed='arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole')
    lam, cf = _client('lambda'), _client('cloudformation')
    if not _function():
        lam.create_function(FunctionName=FUNCTION, Runtime='python3.12', Role=FUNCTION_ROLE_ARN,
                            Handler='allocator.handler', Code={'ZipFile': _zip()}, Timeout=30,
                            Description='Allocates a VPC CIDR per environment (multi-Region baseline lab)',
                            Tags={'Lab': LAB_KEY})
    _wait(lambda: (_function() or {}).get('State') in (None, 'Active', 'Failed'), timeout=60)
    stack = _stack()
    if stack and stack.get('StackStatus') in ('ROLLBACK_COMPLETE', 'CREATE_FAILED', 'ROLLBACK_FAILED'):
        cf.update_termination_protection(StackName=NETWORK_STACK, EnableTerminationProtection=False)
        cf.delete_stack(StackName=NETWORK_STACK)
        _wait(lambda: not _stack(), timeout=60)
        stack = None
    if not stack:
        cf.create_stack(StackName=NETWORK_STACK, TemplateBody=json.dumps(NETWORK_TEMPLATE), Tags=[LAB_TAG])
    stack = _wait_stack() or {}
    exports = _safe_call(lambda: cf.list_exports().get('Exports', []), []) or []
    response = {
        'Stack': {key: stack.get(key) for key in ('StackName', 'StackStatus', 'StackStatusReason')},
        'Outputs': _outputs(stack),
        'Export': next((item for item in exports if item.get('Name') == CIDR_EXPORT), None),
        'CidrParameter': _parameter(CIDR_PARAMETER, REGION),
        'Template': NETWORK_TEMPLATE,
    }
    verified, message = _check_custom()
    return _result('custom-resource', CUSTOM_RESOURCE_COMMAND, response, verified, message, started)


def step_protect_stack() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_custom()[0]:
        return _result('protect-stack', PROTECT_COMMAND, {}, False, f'Create the {NETWORK_STACK} stack first.', started)
    cf = _client('cloudformation')
    cf.update_termination_protection(StackName=NETWORK_STACK, EnableTerminationProtection=True)
    try:
        cf.delete_stack(StackName=NETWORK_STACK)
        delete_attempt = 'DeleteStack was accepted (termination protection did not block it).'
    except ClientError as exc:
        delete_attempt = f'Refused: {exc.response.get("Error", {}).get("Message")}'
    cf.set_stack_policy(StackName=NETWORK_STACK, StackPolicyBody=json.dumps(STACK_POLICY))
    stored = _safe_call(lambda: cf.get_stack_policy(StackName=NETWORK_STACK).get('StackPolicyBody'))
    if _change_set():
        cf.delete_change_set(StackName=NETWORK_STACK, ChangeSetName=CHANGE_SET)
    cf.create_change_set(StackName=NETWORK_STACK, ChangeSetName=CHANGE_SET,
                         TemplateBody=json.dumps(RENAMED_NETWORK_TEMPLATE),
                         Description='Rename the CIDR parameter (replaces it)')
    _wait(lambda: (_change_set() or {}).get('Status') in ('CREATE_COMPLETE', 'FAILED'), timeout=60)
    change_set = _change_set() or {}
    changes = [item['ResourceChange'] for item in change_set.get('Changes', [])]
    rows = evaluate_change_set(STACK_POLICY, changes)
    denied = [row['LogicalId'] for row in rows if row['Decision'] == 'Deny']
    cf.delete_change_set(StackName=NETWORK_STACK, ChangeSetName=CHANGE_SET)
    protected = bool((_stack() or {}).get('EnableTerminationProtection')) and _stack() is not None
    if protected and denied:
        _marker('protect-stack', {'message': (
            f'Termination protection blocked DeleteStack, and the stack policy would stop the change set at '
            f'{", ".join(denied)} (Update:Replace is denied); the lab deleted the change set instead of executing it.')})
    response = {
        'TerminationProtection': (_stack() or {}).get('EnableTerminationProtection'),
        'DeleteAttempt': delete_attempt,
        'StackPolicy': STACK_POLICY,
        'StackPolicyReadBack': stored or 'Floci stores the policy but returns nothing from GetStackPolicy.',
        'ChangeSetEvaluation': rows,
        'Note': ('Floci does not enforce stack policies: executing this change set would succeed there. '
                 'On AWS the update fails and rolls back at CidrParameter.'),
    }
    verified, message = _check_protect()
    return _result('protect-stack', PROTECT_COMMAND, response, verified, message, started)


def step_detect_drift() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_deploy()[0]:
        return _result('detect-drift', DRIFT_COMMAND, {}, False, 'Deploy the stack instances first.', started)
    ssm, logs = _client('ssm', DRIFT_REGION), _client('logs', DRIFT_REGION)
    expected = _expected_baseline()
    ssm.put_parameter(Name=CONFIG_PARAMETER, Value=config_value(max(_template_version(), 1), owner='someone', retention=1),
                      Type='String', Overwrite=True)
    logs.put_retention_policy(logGroupName=AUDIT_LOG_GROUP, retentionInDays=1)
    stack_name = next((item.get('StackId', '').split('/')[1] for item in _instances()
                       if item.get('Region') == DRIFT_REGION and item.get('StackId')), f'StackSet-{STACK_SET}')
    try:
        detection = _client('cloudformation', DRIFT_REGION).detect_stack_drift(StackName=stack_name)
        native = f'DetectStackDrift started: {detection.get("StackDriftDetectionId")}'
    except ClientError as exc:
        native = f'DetectStackDrift is not available in Floci ({_code(exc)}); the lab compares the settings itself.'
    before = {region: compare_baseline(expected, _live_baseline(region)) for region in REGIONS}
    drifted = sorted(region for region, rows in before.items() if any(row['Status'] != 'IN_SYNC' for row in rows))
    ssm.put_parameter(Name=CONFIG_PARAMETER, Value=expected['ConfigValue'], Type='String', Overwrite=True)
    logs.put_retention_policy(logGroupName=AUDIT_LOG_GROUP, retentionInDays=expected['AuditLogRetentionDays'])
    after = {region: compare_baseline(expected, _live_baseline(region)) for region in REGIONS}
    in_sync = all(row['Status'] == 'IN_SYNC' for rows in after.values() for row in rows)
    if drifted == [DRIFT_REGION] and in_sync:
        _marker('detect-drift', {'message': (
            f'{DRIFT_REGION} drifted (config value and log retention MODIFIED) while us-east-1 stayed IN_SYNC; '
            'restoring the template values brought every stack instance back IN_SYNC.')})
    response = {'NativeDriftDetection': native, 'Before': before, 'DriftedRegions': drifted, 'After': after}
    verified, message = _check_drift()
    return _result('detect-drift', DRIFT_COMMAND, response, verified, message, started)


def step_service_catalog() -> dict[str, Any]:
    started = time.perf_counter()
    s3, sc = _client('s3'), _client('servicecatalog')
    if _safe_call(lambda: s3.head_bucket(Bucket=CATALOG_BUCKET)) is None:
        _safe_call(lambda: s3.create_bucket(Bucket=CATALOG_BUCKET))
    s3.put_object(Bucket=CATALOG_BUCKET, Key=PRODUCT_TEMPLATE_KEY, Body=json.dumps(PRODUCT_TEMPLATE).encode())
    _ensure_role(LAUNCH_ROLE, LAUNCH_TRUST, 'Service Catalog launches the team queue product with this role',
                 policy=LAUNCH_POLICY)
    _ensure_role(DEVELOPER_ROLE, DEVELOPER_TRUST, 'Developers may launch approved products only',
                 policy=DEVELOPER_POLICY)
    portfolio = _portfolio()
    if not portfolio:
        portfolio = sc.create_portfolio(DisplayName=PORTFOLIO, ProviderName='platform',
                                        Description='Approved building blocks', IdempotencyToken=f'{PORTFOLIO}-1',
                                        Tags=[LAB_TAG])['PortfolioDetail']
    product = _product()
    if not product:
        template_url = f'{FlociClientFactory().endpoint_url.rstrip("/")}/{CATALOG_BUCKET}/{PRODUCT_TEMPLATE_KEY}'
        sc.create_product(Name=PRODUCT, Owner='platform', ProductType='CLOUD_FORMATION_TEMPLATE',
                          Description='Encrypted SQS queue with a dead-letter queue',
                          ProvisioningArtifactParameters={'Name': PRODUCT_VERSION, 'Type': 'CLOUD_FORMATION_TEMPLATE',
                                                          'Info': {'LoadTemplateFromURL': template_url}},
                          IdempotencyToken=f'{PRODUCT}-1', Tags=[LAB_TAG])
        product = _product()
    if not product:
        return _result('service-catalog', CATALOG_COMMAND, {}, False, f'{PRODUCT} could not be created.', started)
    product_id = product['ProductId']
    artifacts = sc.list_provisioning_artifacts(ProductId=product_id).get('ProvisioningArtifactDetails', [])
    artifact_id = next((item['Id'] for item in artifacts if item.get('Name') == PRODUCT_VERSION), artifacts[0]['Id'])
    portfolios = sc.list_portfolios_for_product(ProductId=product_id).get('PortfolioDetails', [])
    if not any(item['Id'] == portfolio['Id'] for item in portfolios):
        sc.associate_product_with_portfolio(ProductId=product_id, PortfolioId=portfolio['Id'])
    constraints = sc.list_constraints_for_portfolio(PortfolioId=portfolio['Id']).get('ConstraintDetails', [])
    if not any(item.get('Type') == 'LAUNCH' for item in constraints):
        sc.create_constraint(PortfolioId=portfolio['Id'], ProductId=product_id, Type='LAUNCH',
                             Parameters=json.dumps({'RoleArn': f'arn:aws:iam::{ACCOUNT}:role/{LAUNCH_ROLE}'}),
                             Description='Launch with the catalog role', IdempotencyToken=f'{PRODUCT}-launch')
    sc.associate_principal_with_portfolio(PortfolioId=portfolio['Id'], PrincipalType='IAM',
                                          PrincipalARN=f'arn:aws:iam::{ACCOUNT}:role/{DEVELOPER_ROLE}')
    # Floci answers a repeated ProvisionProduct for the same name with the first record, even after that product
    # was terminated, so the step provisions once and judges the record it gets back.
    record = sc.provision_product(ProductId=product_id, ProvisioningArtifactId=artifact_id,
                                  ProvisionedProductName=PROVISIONED_PRODUCT,
                                  ProvisioningParameters=[{'Key': 'QueueName', 'Value': TEAM_QUEUE}],
                                  ProvisionToken=f'{PROVISIONED_PRODUCT}-{int(time.time())}', Tags=[LAB_TAG])['RecordDetail']
    _wait(lambda: sc.describe_record(Id=record['RecordId'])['RecordDetail'].get('Status') not in (
        'CREATED', 'IN_PROGRESS', 'IN_PROGRESS_IN_ERROR'), timeout=60)
    record = sc.describe_record(Id=record['RecordId'])['RecordDetail']
    if record.get('Status') == 'SUCCEEDED' and record.get('ProvisionedProductName') == PROVISIONED_PRODUCT:
        _marker('service-catalog', {'message': (
            f'{PROVISIONED_PRODUCT} was provisioned from {PRODUCT} {PRODUCT_VERSION} through {PORTFOLIO} '
            f'(record {record["RecordId"]} SUCCEEDED), launched with the {LAUNCH_ROLE} role.')})
    queue_created = _safe_call(lambda: bool(_client('sqs').get_queue_url(QueueName=TEAM_QUEUE)), False)
    response = {
        'Portfolio': {key: portfolio.get(key) for key in ('Id', 'DisplayName', 'ProviderName')},
        'Product': {'Id': product_id, 'Name': PRODUCT, 'Version': PRODUCT_VERSION, 'ArtifactId': artifact_id},
        'LaunchConstraintRole': LAUNCH_ROLE,
        'Principal': DEVELOPER_ROLE,
        'Record': {key: record.get(key) for key in ('RecordId', 'Status', 'RecordType', 'ProvisionedProductName')},
        'QueueCreated': queue_created,
        'OnAws': (f'Service Catalog would run the template as a stack named SC-{ACCOUNT}-pp-<id> with the '
                  f'{LAUNCH_ROLE} role, creating {TEAM_QUEUE} and {TEAM_QUEUE}-dlq.'),
    }
    if not queue_created:
        response['Note'] = 'Floci records the provisioned product but does not create the stack or the queue.'
    verified, message = _check_catalog()
    return _result('service-catalog', CATALOG_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-stack-set': step_create_stack_set,
    'deploy-regions': step_deploy_regions,
    'update-stack-set': step_update_stack_set,
    'org-rollout': step_org_rollout,
    'custom-resource': step_custom_resource,
    'protect-stack': step_protect_stack,
    'detect-drift': step_detect_drift,
    'service-catalog': step_service_catalog,
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
    except (ClientError, BotoCoreError, KeyError, TypeError, IndexError):
        return False


CHECKS = {
    'create-stack-set': _check_create,
    'deploy-regions': _check_deploy,
    'update-stack-set': _check_update,
    'org-rollout': _check_org,
    'custom-resource': _check_custom,
    'protect-stack': _check_protect,
    'detect-drift': _check_drift,
    'service-catalog': _check_catalog,
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
    """Remove the Service Catalog product, the network stack and its function, both StackSets with their instances,
    the member account and OU, the lab's roles, and the organization if the lab created it and nothing else is in it."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if _not_found(exc) or code in ('AWSOrganizationsNotInUseException', 'InvalidParametersException',
                                           'NoSuchEntity', 'ResourceInUseException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')
        except KeyError:
            deleted.append(label)  # Floci answers some deletes (DeleteStackSet) without a result element.

    sc = _client('servicecatalog')
    provisioned = _safe_call(_provisioned)
    if provisioned:
        attempt(f'provisioned-product:{PROVISIONED_PRODUCT}', lambda: sc.terminate_provisioned_product(
            ProvisionedProductName=PROVISIONED_PRODUCT, TerminateToken=f'{PROVISIONED_PRODUCT}-{int(time.time())}'))
    portfolio, product = _safe_call(_portfolio), _safe_call(_product)
    if portfolio:
        principals = _safe_call(lambda: sc.list_principals_for_portfolio(PortfolioId=portfolio['Id']).get(
            'Principals', []), []) or []
        for principal in principals:
            attempt('principal', lambda principal=principal: sc.disassociate_principal_from_portfolio(
                PortfolioId=portfolio['Id'], PrincipalARN=principal['PrincipalARN']))
        constraints = _safe_call(lambda: sc.list_constraints_for_portfolio(PortfolioId=portfolio['Id']).get(
            'ConstraintDetails', []), []) or []
        for constraint in constraints:
            attempt('constraint', lambda constraint=constraint: sc.delete_constraint(Id=constraint['ConstraintId']))
        if product:
            attempt('product-association', lambda: sc.disassociate_product_from_portfolio(
                ProductId=product['ProductId'], PortfolioId=portfolio['Id']))
    if product:
        attempt(f'product:{PRODUCT}', lambda: sc.delete_product(Id=product['ProductId']))
    if portfolio:
        attempt(f'portfolio:{PORTFOLIO}', lambda: sc.delete_portfolio(Id=portfolio['Id']))
    s3 = _client('s3')
    objects = _safe_call(lambda: s3.list_objects_v2(Bucket=CATALOG_BUCKET).get('Contents', []), []) or []
    for item in objects:
        attempt(f'object:{item["Key"]}', lambda item=item: s3.delete_object(Bucket=CATALOG_BUCKET, Key=item['Key']))
    attempt(f'bucket:{CATALOG_BUCKET}', lambda: s3.delete_bucket(Bucket=CATALOG_BUCKET))

    cf = _client('cloudformation')
    if _safe_call(_stack):
        attempt('termination-protection', lambda: cf.update_termination_protection(
            StackName=NETWORK_STACK, EnableTerminationProtection=False))
        attempt(f'stack:{NETWORK_STACK}', lambda: cf.delete_stack(StackName=NETWORK_STACK))
        if not _wait(lambda: not _safe_call(_stack, True), timeout=120):
            errors.append(f'stack:{NETWORK_STACK}: still present after delete-stack')
    attempt(f'function:{FUNCTION}', lambda: _client('lambda').delete_function(FunctionName=FUNCTION))

    for name in (ORG_STACK_SET, STACK_SET):
        if not _safe_call(lambda name=name: _stack_set(name)):
            continue
        instances = _safe_call(lambda name=name: _instances(name), []) or []
        if instances:
            regions = sorted({item['Region'] for item in instances})
            if name == ORG_STACK_SET:
                units = sorted({item['OrganizationalUnitId'] for item in instances if item.get('OrganizationalUnitId')})
                kwargs: dict[str, Any] = {'DeploymentTargets': {'OrganizationalUnitIds': units}} if units else {
                    'DeploymentTargets': {'Accounts': sorted({item['Account'] for item in instances})}}
            else:
                kwargs = {'Accounts': sorted({item['Account'] for item in instances})}
            try:
                operation_id = cf.delete_stack_instances(StackSetName=name, Regions=regions, RetainStacks=False,
                                                         **kwargs)['OperationId']
                _wait_operation(name, operation_id)
                deleted.append(f'stack-instances:{name}')
            except (ClientError, BotoCoreError) as exc:
                errors.append(f'stack-instances:{name}: {exc}')
        attempt(f'stack-set:{name}', lambda name=name: cf.delete_stack_set(StackSetName=name))
    for region in REGIONS:
        # Stack instances remove these; delete leftovers so a rerun starts clean.
        attempt(f'parameter:{region}', lambda region=region: _client('ssm', region).delete_parameter(
            Name=CONFIG_PARAMETER))
        attempt(f'log-group:{region}', lambda region=region: _client('logs', region).delete_log_group(
            logGroupName=AUDIT_LOG_GROUP))

    organizations = _client('organizations')
    organization = _safe_call(_organization)
    if organization:
        member = _safe_call(_member)
        if member:
            try:
                organizations.remove_account_from_organization(AccountId=member['Id'])
                deleted.append(f'account:{MEMBER_ACCOUNT}')
            except ClientError:
                attempt(f'account:{MEMBER_ACCOUNT}', lambda: organizations.close_account(AccountId=member['Id']))
        ou = _safe_call(_ou)
        if ou:
            attempt(f'ou:{OU_NAME}', lambda: organizations.delete_organizational_unit(OrganizationalUnitId=ou['Id']))
        root = _safe_call(_root)
        if root:
            tags = _safe_call(lambda: {tag['Key']: tag['Value'] for tag in organizations.list_tags_for_resource(
                ResourceId=root['Id']).get('Tags', [])}, {}) or {}
            if tags.get('Lab') == LAB_KEY:
                others = _safe_call(lambda: [item for item in organizations.list_accounts().get('Accounts', [])
                                             if item['Id'] != organization.get('MasterAccountId')
                                             and item.get('Status') == 'ACTIVE'], ['unknown'])
                units = _safe_call(lambda: organizations.list_organizational_units_for_parent(
                    ParentId=root['Id']).get('OrganizationalUnits', []), ['unknown'])
                if not others and not units:
                    attempt(f'organization:{organization["Id"]}', lambda: organizations.delete_organization())

    iam = _client('iam')
    for role in (FUNCTION_ROLE, LAUNCH_ROLE, DEVELOPER_ROLE, ADMIN_ROLE, EXECUTION_ROLE):
        if role in (ADMIN_ROLE, EXECUTION_ROLE):
            tags = _safe_call(lambda role=role: {tag['Key']: tag['Value'] for tag in iam.list_role_tags(
                RoleName=role).get('Tags', [])}, {}) or {}
            if tags.get('Lab') != LAB_KEY:
                continue  # Shared, well-known role names: remove them only if this lab created them.
        for policy in _safe_call(lambda role=role: iam.list_role_policies(RoleName=role).get('PolicyNames', []),
                                 []) or []:
            attempt(f'role-policy:{policy}', lambda role=role, policy=policy: iam.delete_role_policy(
                RoleName=role, PolicyName=policy))
        for attached in _safe_call(lambda role=role: iam.list_attached_role_policies(RoleName=role).get(
                'AttachedPolicies', []), []) or []:
            attempt(f'detach:{role}', lambda role=role, attached=attached: iam.detach_role_policy(
                RoleName=role, PolicyArn=attached['PolicyArn']))
        attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws servicecatalog terminate-provisioned-product --provisioned-product-name {PROVISIONED_PRODUCT}\n'
            f'aws cloudformation update-termination-protection --stack-name {NETWORK_STACK} '
            '--no-enable-termination-protection\n'
            f'aws cloudformation delete-stack --stack-name {NETWORK_STACK}\n'
            f'aws cloudformation delete-stack-instances --stack-set-name {STACK_SET} --accounts {ACCOUNT} '
            f'--regions {" ".join(REGIONS)} --no-retain-stacks\n'
            f'aws cloudformation delete-stack-set --stack-set-name {STACK_SET}\n'
            f'aws organizations remove-account-from-organization --account-id <{MEMBER_ACCOUNT}-id>'
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
            else 'The StackSets, stacks, catalog product, member account and roles are gone.',
        },
    }
