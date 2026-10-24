"""Serverless environments lab: package a Lambda layer and function, keep feature flags per environment in AWS
AppConfig, review a change set for a SAM template, deploy the same template as a dev stack and a prod stack, ship a
new release to dev only, and turn a feature on in prod with a flag instead of a deploy.

Floci 2.3.0 runs the SAM transform (Globals, AutoPublishAlias, SimpleTable) and AppConfig's data plane for real, and the
function reads its flags and writes to its own table. What it does not do: the transform drops a function's Policies
(so the template declares its role explicitly), Ref on AWS::Serverless::LayerVersion returns no ARN (so the template
uses AWS::Lambda::LayerVersion), the live alias points at $LATEST and an update publishes no new version, AppConfig
deployments finish at once whatever the strategy, and flag constraints are not validated. The lab says so and checks
those settings itself.
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

SERVICE = 'cloudformation'
LAB_KEY = 'sam-environments'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

BUCKET = 'lab-sam-artifacts'
LAYER_KEY = 'layers/pricing-layer.zip'
TEMPLATE_KEY = 'templates/quote-service.json'
STAGES = ('dev', 'prod')
STACKS = {stage: f'lab-sam-{stage}' for stage in STAGES}
FUNCTIONS = {stage: f'lab-sam-quote-{stage}' for stage in STAGES}
TABLES = {stage: f'lab-sam-quotes-{stage}' for stage in STAGES}
ALIAS = 'live'
RUNTIME = 'python3.12'

FLAGS_APP = 'lab-sam-flags'
FLAGS_PROFILE = 'quote-flags'
STRATEGY = 'lab-sam-all-at-once'
FLAG = 'bulk_discount'
DISCOUNT_PERCENT = 15
MAX_PERCENT = 50
QUANTITY = 20
UNIT_PRICE = 10.0


def app_key(release: int) -> str:
    return f'app/quote-service-{release}.zip'


LAYER_SOURCE = '''def price(quantity, flag):
    """Shared pricing rules, packaged as a layer and imported by every function."""
    base = quantity * 10.0
    if flag.get("enabled"):
        return round(base * (1 - flag.get("percent", 0) / 100), 2)
    return base
'''

APP_SOURCE = '''import json
import os

import boto3
from pricing import price

flags = boto3.client("appconfigdata")
table = boto3.resource("dynamodb").Table(os.environ["TABLE"])
RELEASE = "{release}"


def read_flag():
    """Fetch this environment's flags; fall back to "off" so a flag outage never breaks quotes."""
    try:
        token = flags.start_configuration_session(
            ApplicationIdentifier=os.environ["FLAGS_APP"],
            EnvironmentIdentifier=os.environ["STAGE"],
            ConfigurationProfileIdentifier=os.environ["FLAGS_PROFILE"],
        )["InitialConfigurationToken"]
        body = flags.get_latest_configuration(ConfigurationToken=token)["Configuration"].read()
        return (json.loads(body) if body else {{}}).get("{flag}", {{"enabled": False}}), None
    except Exception as exc:  # noqa: BLE001
        return {{"enabled": False}}, type(exc).__name__


def handler(event, context):
    flag, flag_error = read_flag()
    total = price(int(event.get("quantity", 1)), flag)
    table.put_item(Item={{"id": context.aws_request_id, "total": str(total), "release": RELEASE}})
    return {{"release": RELEASE, "stage": os.environ["STAGE"], "total": total, "flag": flag,
            "flagError": flag_error, "table": os.environ["TABLE"]}}
'''


def app_source(release: int) -> str:
    return APP_SOURCE.format(release=release, flag=FLAG)


def template(release: int) -> dict[str, Any]:
    """The SAM template both stacks share; only the Stage parameter differs."""
    return {
        'AWSTemplateFormatVersion': '2010-09-09',
        'Transform': 'AWS::Serverless-2016-10-31',
        'Description': 'Quote service (serverless environments lab)',
        'Parameters': {'Stage': {'Type': 'String', 'AllowedValues': list(STAGES)}},
        'Globals': {'Function': {
            'Runtime': RUNTIME, 'Timeout': 15, 'MemorySize': 256,
            'Environment': {'Variables': {'STAGE': {'Ref': 'Stage'}, 'FLAGS_APP': FLAGS_APP,
                                          'FLAGS_PROFILE': FLAGS_PROFILE}},
        }},
        'Resources': {
            'PricingLayer': {'Type': 'AWS::Lambda::LayerVersion', 'Properties': {
                'LayerName': {'Fn::Sub': 'lab-sam-pricing-${Stage}'},
                'Content': {'S3Bucket': BUCKET, 'S3Key': LAYER_KEY}, 'CompatibleRuntimes': [RUNTIME]}},
            'QuotesTable': {'Type': 'AWS::Serverless::SimpleTable', 'Properties': {
                'TableName': {'Fn::Sub': 'lab-sam-quotes-${Stage}'}}},
            'QuoteRole': {'Type': 'AWS::IAM::Role', 'Properties': {
                'AssumeRolePolicyDocument': {'Version': '2012-10-17', 'Statement': [{
                    'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]},
                'ManagedPolicyArns': ['arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole'],
                'Policies': [{'PolicyName': 'quotes-and-flags', 'PolicyDocument': {'Version': '2012-10-17', 'Statement': [
                    {'Effect': 'Allow', 'Action': ['dynamodb:PutItem', 'dynamodb:GetItem'],
                     'Resource': {'Fn::GetAtt': ['QuotesTable', 'Arn']}},
                    {'Effect': 'Allow', 'Action': ['appconfig:StartConfigurationSession', 'appconfig:GetLatestConfiguration'],
                     'Resource': '*'},
                ]}}]}},
            'QuoteFunction': {'Type': 'AWS::Serverless::Function', 'Properties': {
                'FunctionName': {'Fn::Sub': 'lab-sam-quote-${Stage}'}, 'Handler': 'app.handler',
                'CodeUri': {'Bucket': BUCKET, 'Key': app_key(release)}, 'Layers': [{'Ref': 'PricingLayer'}],
                'AutoPublishAlias': ALIAS, 'Role': {'Fn::GetAtt': ['QuoteRole', 'Arn']},
                'Environment': {'Variables': {'TABLE': {'Ref': 'QuotesTable'}}}}},
        },
        'Outputs': {'QuoteFunctionName': {'Value': {'Ref': 'QuoteFunction'}},
                    'QuotesTableName': {'Value': {'Ref': 'QuotesTable'}}},
    }


def flags_document(enabled: bool, percent: int = DISCOUNT_PERCENT) -> dict[str, Any]:
    return {
        'version': '1',
        'flags': {FLAG: {'name': 'Bulk discount', 'attributes': {
            'percent': {'constraints': {'type': 'number', 'minimum': 0, 'maximum': MAX_PERCENT}}}}},
        'values': {FLAG: {'enabled': enabled, 'percent': percent}},
    }


PACKAGE_COMMAND = (
    f'aws s3 mb s3://{BUCKET}\n'
    'sam build && sam package --s3-bucket ' + BUCKET + ' --output-template-file packaged.json\n'
    f'# uploads {LAYER_KEY} (python/pricing.py) and {app_key(1)} (app.py)'
)
FLAGS_COMMAND = (
    f'aws appconfig create-application --name {FLAGS_APP}\n'
    'aws appconfig create-environment --application-id <app-id> --name dev   # and prod\n'
    f'aws appconfig create-configuration-profile --application-id <app-id> --name {FLAGS_PROFILE} '
    '--location-uri hosted --type AWS.AppConfig.FeatureFlags\n'
    'aws appconfig create-hosted-configuration-version ... --content-type application/json --content fileb://flags-on.json\n'
    f'aws appconfig create-deployment-strategy --name {STRATEGY} --deployment-duration-in-minutes 0 '
    '--growth-factor 100 --final-bake-time-in-minutes 0 --replicate-to NONE\n'
    'aws appconfig start-deployment --application-id <app-id> --environment-id <dev-id> ...'
)
CHANGESET_COMMAND = (
    f'aws cloudformation create-change-set --stack-name {STACKS["dev"]} --change-set-name release-1 '
    '--change-set-type CREATE --template-url packaged.json --parameters ParameterKey=Stage,ParameterValue=dev '
    '--capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND\n'
    f'aws cloudformation describe-change-set --stack-name {STACKS["dev"]} --change-set-name release-1\n'
    '# sam deploy --guided does the same and asks before executing'
)
DEPLOY_DEV_COMMAND = (
    f'aws cloudformation execute-change-set --stack-name {STACKS["dev"]} --change-set-name release-1\n'
    f'aws cloudformation wait stack-create-complete --stack-name {STACKS["dev"]}\n'
    f'aws lambda invoke --function-name {FUNCTIONS["dev"]}:{ALIAS} --payload \'{{"quantity": {QUANTITY}}}\' out.json'
)
DEPLOY_PROD_COMMAND = (
    f'sam deploy --stack-name {STACKS["prod"]} --parameter-overrides Stage=prod --capabilities CAPABILITY_IAM '
    'CAPABILITY_AUTO_EXPAND   # or samconfig.toml [prod] section\n'
    f'aws lambda invoke --function-name {FUNCTIONS["prod"]}:{ALIAS} --payload \'{{"quantity": {QUANTITY}}}\' out.json'
)
UPDATE_DEV_COMMAND = (
    f'aws s3 cp quote-service-2.zip s3://{BUCKET}/{app_key(2)}\n'
    f'aws cloudformation create-change-set --stack-name {STACKS["dev"]} --change-set-name release-2 --change-set-type UPDATE ...\n'
    f'aws cloudformation execute-change-set --stack-name {STACKS["dev"]} --change-set-name release-2'
)
FLAG_PROD_COMMAND = (
    'aws appconfig create-hosted-configuration-version ... --content fileb://flags-on.json\n'
    'aws appconfig start-deployment --application-id <app-id> --environment-id <prod-id> '
    '--deployment-strategy-id <strategy-id> --configuration-profile-id <profile-id> --configuration-version <n>\n'
    f'aws lambda invoke --function-name {FUNCTIONS["prod"]}:{ALIAS} --payload \'{{"quantity": {QUANTITY}}}\' out.json'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Deploy one SAM template to dev and prod, with a Lambda layer and AppConfig feature flags',
    'description': (
        'Package a Lambda layer and function code, keep a feature flag per environment in AppConfig, review the change '
        'set a SAM template produces, deploy the same template as a dev stack and a prod stack, ship release 2 to dev '
        'only, and turn the feature on in prod with a flag instead of a deploy.'
    ),
    'steps': [
        {
            'key': 'package-artifacts',
            'title': 'Package the pricing layer and the function code to S3',
            'command': PACKAGE_COMMAND,
            'explanation': (
                'sam build installs dependencies and sam package uploads each CodeUri and ContentUri to S3, rewriting '
                'the template to point at the uploaded zips. The pricing rules go in a layer (files under python/ land '
                'on the import path at /opt/python), so several functions share one copy and the function zip stays '
                'small. How Floci emulates this: S3 is real; the step uploads what sam package would.'
            ),
        },
        {
            'key': 'feature-flags',
            'title': 'Keep a bulk discount flag per environment in AppConfig',
            'command': FLAGS_COMMAND,
            'explanation': (
                'AppConfig separates configuration from code: an application has environments (dev, prod), a feature '
                'flag profile holds versioned flag documents with constraints (percent between 0 and 50), and a '
                'deployment strategy rolls a version out to one environment, with alarms that roll it back. dev gets '
                'the discount on and prod gets it off. How Floci emulates this: versions, environments and the data '
                'API are real, but deployments finish at once whatever the strategy, and constraints are not '
                'validated (a 90% value is accepted; AWS rejects it).'
            ),
        },
        {
            'key': 'review-changeset',
            'title': 'Review the change set a SAM template produces for dev',
            'command': CHANGESET_COMMAND,
            'explanation': (
                'Transform: AWS::Serverless-2016-10-31 turns short SAM resources into CloudFormation: AutoPublishAlias '
                'adds a Lambda version and a live alias, SimpleTable becomes a DynamoDB table, and Globals fill in '
                'every function. A change set shows exactly what will be added, modified or replaced before anything '
                'changes; CAPABILITY_IAM acknowledges the role and CAPABILITY_AUTO_EXPAND the transform. How Floci '
                'emulates this: the transform and change sets are real, but the transform drops a function\'s '
                'Policies and Ref on AWS::Serverless::LayerVersion returns no ARN, so the template declares its role '
                'and uses AWS::Lambda::LayerVersion.'
            ),
        },
        {
            'key': 'deploy-dev',
            'title': 'Execute the change set and invoke dev through its alias',
            'command': DEPLOY_DEV_COMMAND,
            'explanation': (
                'Executing the change set creates the stack. Callers use the live alias, never $LATEST, so a release '
                'only reaches them when the alias moves. The function reads its flag from AppConfig and writes the '
                'quote to the stack\'s own table, which its role allows. How Floci emulates this: the function, '
                'layer, table and role are real, and the role is enforced for DynamoDB, but the alias points at '
                '$LATEST instead of version 1.'
            ),
        },
        {
            'key': 'deploy-prod',
            'title': 'Deploy the same template as the prod stack',
            'command': DEPLOY_PROD_COMMAND,
            'explanation': (
                'One template, two stacks: the Stage parameter names each environment\'s function, table and layer, so '
                'dev and prod never share resources, and samconfig.toml can hold each environment\'s parameters. The '
                'same code gives prod the full price because prod\'s flag is off. How Floci emulates this: both stacks '
                'are real and isolated.'
            ),
        },
        {
            'key': 'update-dev',
            'title': 'Ship release 2 to dev only with an update change set',
            'command': UPDATE_DEV_COMMAND,
            'explanation': (
                'Changing CodeUri makes the update change set modify the function in place (no replacement). On AWS, '
                'AutoPublishAlias also publishes version 2 and moves the live alias to it, which is where a '
                'DeploymentPreference would shift traffic gradually with CodeDeploy. prod keeps release 1. How Floci '
                'emulates this: the update is real, but no new version is published and the alias stays on $LATEST, '
                'which already runs release 2.'
            ),
        },
        {
            'key': 'flag-prod',
            'title': 'Turn the discount on in prod with a flag, not a deploy',
            'command': FLAG_PROD_COMMAND,
            'explanation': (
                'Deploying a new flag version to prod changes behavior without touching the stack or the code: the '
                'next request sees it. In production the AWS AppConfig Lambda extension caches flags and polls in the '
                'background, and a deployment strategy with a bake time and alarms limits the blast radius. How Floci '
                'emulates this: the flag reaches the function at once, because Floci finishes deployments '
                'immediately.'
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


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, source in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, source)
    return buffer.getvalue()


# ── Lookups ──────────────────────────────────────────────────────────────────

def _objects() -> set[str]:
    try:
        return {item['Key'] for item in _client('s3').list_objects_v2(Bucket=BUCKET).get('Contents', [])}
    except ClientError as exc:
        if _code(exc) == 'NoSuchBucket':
            return set()
        raise


def _stack(stage: str) -> dict[str, Any] | None:
    try:
        stacks = _client('cloudformation').describe_stacks(StackName=STACKS[stage]).get('Stacks', [])
    except ClientError as exc:
        if _code(exc) == 'ValidationError':
            return None
        raise
    stack = stacks[0] if stacks else None
    if stack and stack.get('StackStatus') in ('DELETE_COMPLETE', 'REVIEW_IN_PROGRESS'):
        return None
    return stack


def _live(stack: dict[str, Any] | None) -> bool:
    return bool(stack) and stack.get('StackStatus') in ('CREATE_COMPLETE', 'UPDATE_COMPLETE')


def _stack_resources(stage: str) -> dict[str, dict[str, Any]]:
    try:
        resources = _client('cloudformation').describe_stack_resources(StackName=STACKS[stage]).get('StackResources', [])
    except ClientError:
        return {}
    return {item['LogicalResourceId']: item for item in resources}


def _app() -> dict[str, Any] | None:
    apps = [item for item in _client('appconfig').list_applications().get('Items', []) if item.get('Name') == FLAGS_APP]
    return apps[0] if apps else None


def _environments(app_id: str) -> dict[str, str]:
    items = _client('appconfig').list_environments(ApplicationId=app_id).get('Items', [])
    return {item['Name']: item['Id'] for item in items}


def _profile(app_id: str) -> dict[str, Any] | None:
    items = _client('appconfig').list_configuration_profiles(ApplicationId=app_id).get('Items', [])
    found = [item for item in items if item.get('Name') == FLAGS_PROFILE]
    return found[0] if found else None


def _strategy() -> dict[str, Any] | None:
    items = _client('appconfig').list_deployment_strategies().get('Items', [])
    found = [item for item in items if item.get('Name') == STRATEGY]
    return found[0] if found else None


def _flags_ids() -> dict[str, Any] | None:
    app = _app()
    if not app:
        return None
    profile = _profile(app['Id'])
    if not profile:
        return None
    return {'app': app['Id'], 'profile': profile['Id'], 'environments': _environments(app['Id'])}


def _deployed_flag(stage: str) -> dict[str, Any] | None:
    """Read the flag an environment serves, the way the function does."""
    ids = _flags_ids()
    if not ids or stage not in ids['environments']:
        return None
    data = _client('appconfigdata')
    token = data.start_configuration_session(
        ApplicationIdentifier=ids['app'], EnvironmentIdentifier=ids['environments'][stage],
        ConfigurationProfileIdentifier=ids['profile'])['InitialConfigurationToken']
    body = data.get_latest_configuration(ConfigurationToken=token)['Configuration'].read()
    return (json.loads(body) if body else {}).get(FLAG, {'enabled': False})


def _deploy_flags(stage: str, enabled: bool) -> dict[str, Any]:
    ids = _flags_ids() or {}
    appconfig = _client('appconfig')
    version = appconfig.create_hosted_configuration_version(
        ApplicationId=ids['app'], ConfigurationProfileId=ids['profile'], ContentType='application/json',
        Content=json.dumps(flags_document(enabled)).encode(),
        Description=f'{FLAG} {"on" if enabled else "off"} for {stage}')['VersionNumber']
    deployment = appconfig.start_deployment(
        ApplicationId=ids['app'], EnvironmentId=ids['environments'][stage], DeploymentStrategyId=(_strategy() or {})['Id'],
        ConfigurationProfileId=ids['profile'], ConfigurationVersion=str(version),
        Description=f'{FLAG} {"on" if enabled else "off"}')

    def done() -> bool:
        state = appconfig.get_deployment(ApplicationId=ids['app'], EnvironmentId=ids['environments'][stage],
                                         DeploymentNumber=deployment['DeploymentNumber']).get('State')
        return state in ('COMPLETE', 'ROLLED_BACK')
    _wait(done, timeout=60, delay=2)
    final = appconfig.get_deployment(ApplicationId=ids['app'], EnvironmentId=ids['environments'][stage],
                                     DeploymentNumber=deployment['DeploymentNumber'])
    return {'Version': version, 'DeploymentNumber': deployment['DeploymentNumber'], 'State': final.get('State'),
            'PercentageComplete': final.get('PercentageComplete')}


def _invoke(stage: str) -> dict[str, Any]:
    response = _client('lambda').invoke(FunctionName=f'{FUNCTIONS[stage]}:{ALIAS}',
                                        Payload=json.dumps({'quantity': QUANTITY}).encode())
    payload = json.loads(response['Payload'].read() or b'null')
    return {'FunctionError': response.get('FunctionError'), 'Payload': payload}


def _alias_version(stage: str) -> str | None:
    try:
        return _client('lambda').get_alias(FunctionName=FUNCTIONS[stage], Name=ALIAS).get('FunctionVersion')
    except ClientError:
        return None


def _quote_count(stage: str) -> int:
    try:
        return _client('dynamodb').scan(TableName=TABLES[stage], Select='COUNT').get('Count', 0)
    except ClientError:
        return 0


def _price(enabled: bool) -> float:
    base = QUANTITY * UNIT_PRICE
    return round(base * (1 - DISCOUNT_PERCENT / 100), 2) if enabled else base


def _change_set(stage: str, release: int, name: str) -> dict[str, Any]:
    """Create a change set (CREATE for a new stack, UPDATE for a live one) and wait for it."""
    cloudformation = _client('cloudformation')
    kind = 'UPDATE' if _live(_stack(stage)) else 'CREATE'
    if kind == 'CREATE':
        try:
            existing = cloudformation.describe_stacks(StackName=STACKS[stage]).get('Stacks', [{}])[0]
        except ClientError:
            existing = {}
        if existing.get('StackStatus') in ('ROLLBACK_COMPLETE', 'REVIEW_IN_PROGRESS', 'CREATE_FAILED'):
            cloudformation.delete_stack(StackName=STACKS[stage])
            _wait(lambda: _stack(stage) is None, timeout=90, delay=2)
    cloudformation.create_change_set(
        StackName=STACKS[stage], ChangeSetName=name, ChangeSetType=kind, TemplateBody=json.dumps(template(release)),
        Parameters=[{'ParameterKey': 'Stage', 'ParameterValue': stage}],
        Capabilities=['CAPABILITY_IAM', 'CAPABILITY_AUTO_EXPAND'], Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])

    def ready() -> bool:
        status = cloudformation.describe_change_set(StackName=STACKS[stage], ChangeSetName=name).get('Status')
        return status in ('CREATE_COMPLETE', 'FAILED')
    _wait(ready, timeout=60)
    described = cloudformation.describe_change_set(StackName=STACKS[stage], ChangeSetName=name)
    changes = [{'Action': item['ResourceChange'].get('Action'),
                'LogicalResourceId': item['ResourceChange'].get('LogicalResourceId'),
                'ResourceType': item['ResourceChange'].get('ResourceType'),
                'Replacement': item['ResourceChange'].get('Replacement')}
               for item in described.get('Changes', [])]
    reason = described.get('StatusReason') or ''
    no_changes = described.get('Status') == 'FAILED' and ("didn't contain changes" in reason or 'No updates' in reason)
    return {'Name': name, 'Type': kind, 'Status': described.get('Status'), 'StatusReason': reason or None,
            'NoChanges': no_changes, 'Changes': changes}


def _execute(stage: str, change_set: dict[str, Any]) -> dict[str, Any] | None:
    if change_set.get('Status') != 'CREATE_COMPLETE':
        return _stack(stage)
    _client('cloudformation').execute_change_set(StackName=STACKS[stage], ChangeSetName=change_set['Name'])
    done = ('CREATE_COMPLETE', 'UPDATE_COMPLETE', 'ROLLBACK_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE', 'CREATE_FAILED')
    _wait(lambda: (_stack(stage) or {}).get('StackStatus') in done, timeout=120, delay=2)
    return _stack(stage)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_package() -> tuple[bool, str]:
    missing = {LAYER_KEY, app_key(1), TEMPLATE_KEY} - _objects()
    if missing:
        return False, f'Upload {", ".join(sorted(missing))} to {BUCKET}.'
    return True, f'The layer, release 1 of the function, and the packaged template are in {BUCKET}.'


def _check_flags() -> tuple[bool, str]:
    ids = _flags_ids()
    if not ids or set(STAGES) - set(ids['environments']) or not _strategy():
        return False, f'Create {FLAGS_APP} with dev and prod environments, the {FLAGS_PROFILE} profile, and {STRATEGY}.'
    marker = _marked('feature-flags')
    if not marker:
        return False, 'Deploy the flags to dev and prod.'
    return True, marker['message']


def _check_changeset() -> tuple[bool, str]:
    marker = _marked('review-changeset')
    if not marker:
        return False, f'Create a change set for {STACKS["dev"]}.'
    return True, marker['message']


def _check_stage(step: str, stage: str) -> tuple[bool, str]:
    stack = _stack(stage)
    if not _live(stack):
        return False, f'Stack {STACKS[stage]} is not deployed.'
    resources = _stack_resources(stage)
    expected = {'QuoteFunction', 'QuotesTable', 'PricingLayer', 'QuoteRole'}
    if expected - set(resources):
        return False, f'{STACKS[stage]} is missing {", ".join(sorted(expected - set(resources)))}.'
    marker = _marked(step)
    if not marker:
        return False, f'Invoke {FUNCTIONS[stage]} through its alias.'
    return True, marker['message']


def _check_update() -> tuple[bool, str]:
    if not _check_stage('deploy-dev', 'dev')[0]:
        return False, f'Deploy {STACKS["dev"]} first.'
    marker = _marked('update-dev')
    if not marker:
        return False, 'Ship release 2 to dev.'
    return True, marker['message']


def _check_flag_prod() -> tuple[bool, str]:
    if not _check_stage('deploy-prod', 'prod')[0]:
        return False, f'Deploy {STACKS["prod"]} first.'
    flag = _deployed_flag('prod') or {}
    if not flag.get('enabled'):
        return False, f'prod should serve {FLAG} enabled.'
    marker = _marked('flag-prod')
    if not marker:
        return False, 'Turn the flag on in prod and invoke prod.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_package_artifacts() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    try:
        s3.create_bucket(Bucket=BUCKET)
    except ClientError as exc:
        if _code(exc) not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            raise
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    s3.put_object(Bucket=BUCKET, Key=LAYER_KEY, Body=_zip({'python/pricing.py': LAYER_SOURCE}))
    s3.put_object(Bucket=BUCKET, Key=app_key(1), Body=_zip({'app.py': app_source(1)}))
    s3.put_object(Bucket=BUCKET, Key=TEMPLATE_KEY, Body=json.dumps(template(1), indent=2).encode(),
                  ContentType='application/json')
    response = {'Bucket': BUCKET, 'Objects': sorted(_objects()),
                'Layer': {'Key': LAYER_KEY, 'Files': ['python/pricing.py']},
                'Function': {'Key': app_key(1), 'Files': ['app.py']}, 'Template': template(1)}
    verified, message = _check_package()
    return _result('package-artifacts', PACKAGE_COMMAND, response, verified, message, started)


def step_feature_flags() -> dict[str, Any]:
    started = time.perf_counter()
    appconfig = _client('appconfig')
    app = _app() or appconfig.create_application(Name=FLAGS_APP, Description='Quote service flags (lab)')
    environments = _environments(app['Id'])
    for stage in STAGES:
        if stage not in environments:
            appconfig.create_environment(ApplicationId=app['Id'], Name=stage, Description=f'{stage} (lab)')
    if not _profile(app['Id']):
        appconfig.create_configuration_profile(ApplicationId=app['Id'], Name=FLAGS_PROFILE, LocationUri='hosted',
                                               Type='AWS.AppConfig.FeatureFlags')
    if not _strategy():
        appconfig.create_deployment_strategy(Name=STRATEGY, DeploymentDurationInMinutes=0, GrowthFactor=100,
                                             GrowthType='LINEAR', FinalBakeTimeInMinutes=0, ReplicateTo='NONE')
    ids = _flags_ids() or {}
    # A value outside the flag's constraint: AWS rejects this version.
    invalid = flags_document(True, percent=90)
    try:
        bad = appconfig.create_hosted_configuration_version(
            ApplicationId=ids['app'], ConfigurationProfileId=ids['profile'], ContentType='application/json',
            Content=json.dumps(invalid).encode(), Description='90% (should be rejected)')
        appconfig.delete_hosted_configuration_version(ApplicationId=ids['app'], ConfigurationProfileId=ids['profile'],
                                                      VersionNumber=bad['VersionNumber'])
        constraint = {'Rejected': False, 'Version': bad['VersionNumber']}
    except ClientError as exc:
        constraint = {'Rejected': True, 'Error': _code(exc)}
    deployments = {'dev': _deploy_flags('dev', True), 'prod': _deploy_flags('prod', False)}
    served = {stage: _deployed_flag(stage) for stage in STAGES}
    response = {'Application': ids.get('app'), 'Environments': ids.get('environments'), 'Profile': ids.get('profile'),
                'FlagsOn': flags_document(True), 'ConstraintCheck': constraint, 'Deployments': deployments,
                'Served': served}
    if not (served['dev'] or {}).get('enabled') or (served['prod'] or {}).get('enabled'):
        return _result('feature-flags', FLAGS_COMMAND, response, False,
                       f'dev should serve {FLAG} on and prod off; got {served}.', started)
    message = (f'{FLAGS_APP} serves {FLAG} on ({DISCOUNT_PERCENT}%) in dev and off in prod, each from its own '
               f'deployment of {FLAGS_PROFILE}. ')
    if all(item.get('State') == 'COMPLETE' for item in deployments.values()):
        message += 'Both deployments finished at once; Floci ignores the strategy\'s duration and bake time. '
    message += (f'A 90% value was rejected ({constraint["Error"]}).' if constraint['Rejected'] else
                f'A 90% value broke the flag\'s {MAX_PERCENT}% maximum but was accepted: AWS rejects it, Floci does '
                'not validate constraints, so the step deleted that version.')
    _marker('feature-flags', {'message': message})
    verified, check = _check_flags()
    return _result('feature-flags', FLAGS_COMMAND, response, verified, message if verified else check, started)


def step_review_changeset() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_package()[0]:
        return _result('review-changeset', CHANGESET_COMMAND, {}, False, 'Package the artifacts first.', started)
    change_set = _change_set('dev', 1, f'release-1-{int(time.time())}')
    response = {'ChangeSet': change_set}
    generated = {item['LogicalResourceId'] for item in change_set['Changes']}
    if change_set['Type'] == 'UPDATE':
        if change_set['NoChanges']:
            message = (f'{STACKS["dev"]} is already deployed with release 1, so the change set had no changes; '
                       'reset the lab to review the first deployment again.')
        else:
            message = (f'{STACKS["dev"]} is already deployed, so this is an UPDATE change set that would '
                       + ', '.join(f'{item["Action"].lower()} {item["LogicalResourceId"]}' for item in change_set['Changes'])
                       + '.')
        _marker('review-changeset', {'message': message, 'ChangeSet': change_set})
        verified, check = _check_changeset()
        return _result('review-changeset', CHANGESET_COMMAND, response, verified, message if verified else check, started)
    if change_set['Status'] != 'CREATE_COMPLETE' or not {'QuoteFunction', 'QuoteRole'} <= generated:
        return _result('review-changeset', CHANGESET_COMMAND, response, False,
                       f'The change set should be ready and add the function and its role; got {change_set["Status"]} '
                       f'{change_set["StatusReason"] or ""}.', started)
    from_transform = sorted(generated - set(template(1)['Resources']))
    message = (f'Change set {change_set["Name"]} would add {len(change_set["Changes"])} resources: '
               + ', '.join(f'{item["LogicalResourceId"]} ({item["ResourceType"]})' for item in change_set['Changes'])
               + '.')
    if from_transform:
        message += f' The SAM transform generated {", ".join(from_transform)} from AutoPublishAlias.'
    message += ' Nothing changes until it is executed.'
    _marker('review-changeset', {'message': message, 'ChangeSet': change_set})
    verified, check = _check_changeset()
    return _result('review-changeset', CHANGESET_COMMAND, response, verified, message if verified else check, started)


def _deploy_stage(step: str, stage: str, command: str) -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_flags()[0]:
        return _result(step, command, {}, False, 'Deploy the feature flags first.', started)
    if stage == 'dev':
        marker = _marked('review-changeset') or {}
        change_set = marker.get('ChangeSet')
        if not change_set:
            return _result(step, command, {}, False, 'Review the dev change set first.', started)
    else:
        if not _check_stage('deploy-dev', 'dev')[0]:
            return _result(step, command, {}, False, 'Deploy dev first.', started)
        change_set = _change_set(stage, 1, f'release-1-{int(time.time())}')
    stack = _execute(stage, change_set)
    response: dict[str, Any] = {'ChangeSet': change_set['Name'], 'StackStatus': (stack or {}).get('StackStatus'),
                                'StackStatusReason': (stack or {}).get('StackStatusReason')}
    if not _live(stack):
        return _result(step, command, response, False,
                       f'{STACKS[stage]} did not deploy: {(stack or {}).get("StackStatus")} '
                       f'{(stack or {}).get("StackStatusReason") or ""}'.strip(), started)
    before = _quote_count(stage)
    invoked = _invoke(stage)
    payload = invoked['Payload'] if isinstance(invoked['Payload'], dict) else {}
    alias = _alias_version(stage)
    response.update({'Invoke': invoked, 'AliasVersion': alias, 'QuotesWritten': _quote_count(stage) - before,
                     'Resources': sorted(_stack_resources(stage))})
    expected_flag = stage == 'dev'
    expected_total = _price(expected_flag)
    if invoked['FunctionError'] or payload.get('stage') != stage or payload.get('total') != expected_total:
        return _result(step, command, response, False,
                       f'{FUNCTIONS[stage]}:{ALIAS} should answer stage {stage} with total {expected_total:g}; got '
                       f'{json.dumps(invoked["Payload"])[:300]}.', started)
    message = (f'{STACKS[stage]} is deployed. {FUNCTIONS[stage]}:{ALIAS} priced {QUANTITY} items at '
               f'{payload["total"]:g} ({FLAG} {"on" if expected_flag else "off"} in {stage}), using the layer\'s '
               f'pricing code, and wrote the quote to {payload.get("table")}.')
    if alias == '$LATEST':
        message += ' The alias points at $LATEST; on AWS AutoPublishAlias points it at version 1.'
    if payload.get('flagError'):
        message += f' The flag read failed ({payload["flagError"]}), so the function used its default.'
    _marker(step, {'message': message, 'Release': payload.get('release')})
    verified, check = _check_stage(step, stage)
    return _result(step, command, response, verified, message if verified else check, started)


def step_deploy_dev() -> dict[str, Any]:
    return _deploy_stage('deploy-dev', 'dev', DEPLOY_DEV_COMMAND)


def step_deploy_prod() -> dict[str, Any]:
    return _deploy_stage('deploy-prod', 'prod', DEPLOY_PROD_COMMAND)


def step_update_dev() -> dict[str, Any]:
    started = time.perf_counter()
    if not (_check_stage('deploy-dev', 'dev')[0] and _check_stage('deploy-prod', 'prod')[0]):
        return _result('update-dev', UPDATE_DEV_COMMAND, {}, False, 'Deploy dev and prod first.', started)
    _client('s3').put_object(Bucket=BUCKET, Key=app_key(2), Body=_zip({'app.py': app_source(2)}))
    versions_before = _versions('dev')
    change_set = _change_set('dev', 2, f'release-2-{int(time.time())}')
    stack = _execute('dev', change_set) if not change_set['NoChanges'] else _stack('dev')
    dev, prod = _invoke('dev'), _invoke('prod')
    releases = {stage: (call['Payload'] or {}).get('release') if isinstance(call['Payload'], dict) else None
                for stage, call in (('dev', dev), ('prod', prod))}
    versions_after = _versions('dev')
    response = {'ChangeSet': change_set, 'StackStatus': (stack or {}).get('StackStatus'),
                'Releases': releases, 'VersionsBefore': versions_before, 'VersionsAfter': versions_after,
                'AliasVersion': _alias_version('dev')}
    if releases != {'dev': '2', 'prod': '1'}:
        return _result('update-dev', UPDATE_DEV_COMMAND, response, False,
                       f'dev should run release 2 and prod release 1; got {releases}.', started)
    actions = ', '.join(f'{item["Action"].lower()} {item["LogicalResourceId"]}'
                        + (' (no replacement)' if item.get('Replacement') == 'False' else '')
                        for item in change_set['Changes']) or 'no changes (already on release 2)'
    message = f'The update change set would {actions}. dev now answers with release 2 and prod still runs release 1.'
    if versions_after == versions_before:
        message += (' No new version was published and the alias stayed on $LATEST: on AWS AutoPublishAlias '
                    'publishes version 2 and moves the alias to it.')
    _marker('update-dev', {'message': message})
    verified, check = _check_update()
    return _result('update-dev', UPDATE_DEV_COMMAND, response, verified, message if verified else check, started)


def _versions(stage: str) -> list[str]:
    try:
        items = _client('lambda').list_versions_by_function(FunctionName=FUNCTIONS[stage]).get('Versions', [])
    except ClientError:
        return []
    return sorted(item['Version'] for item in items if item['Version'] != '$LATEST')


def step_flag_prod() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_stage('deploy-prod', 'prod')[0]:
        return _result('flag-prod', FLAG_PROD_COMMAND, {}, False, 'Deploy prod first.', started)
    stack_before = (_stack('prod') or {}).get('LastUpdatedTime') or (_stack('prod') or {}).get('CreationTime')
    deployment = _deploy_flags('prod', True)
    invoked = _invoke('prod')
    payload = invoked['Payload'] if isinstance(invoked['Payload'], dict) else {}
    stack_after = (_stack('prod') or {}).get('LastUpdatedTime') or (_stack('prod') or {}).get('CreationTime')
    response = {'FlagDeployment': deployment, 'Invoke': invoked, 'StackUnchanged': stack_before == stack_after}
    if invoked['FunctionError'] or payload.get('total') != _price(True):
        return _result('flag-prod', FLAG_PROD_COMMAND, response, False,
                       f'prod should now price {QUANTITY} items at {_price(True):g}; got '
                       f'{json.dumps(invoked["Payload"])[:300]}.', started)
    message = (f'Deploying flag version {deployment["Version"]} to prod turned the {DISCOUNT_PERCENT}% discount on: the '
               f'next request priced {QUANTITY} items at {payload["total"]:g} instead of {_price(False):g}, with no '
               f'change to {STACKS["prod"]} or its code (still release {payload.get("release")}).')
    if deployment.get('State') == 'COMPLETE':
        message += ' The deployment finished at once; on AWS the strategy would roll it out and bake first.'
    _marker('flag-prod', {'message': message})
    verified, check = _check_flag_prod()
    return _result('flag-prod', FLAG_PROD_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'package-artifacts': step_package_artifacts,
    'feature-flags': step_feature_flags,
    'review-changeset': step_review_changeset,
    'deploy-dev': step_deploy_dev,
    'deploy-prod': step_deploy_prod,
    'update-dev': step_update_dev,
    'flag-prod': step_flag_prod,
}


def run_step(step_key: str) -> dict[str, Any]:
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


def status() -> dict[str, Any]:
    checks = {
        'package-artifacts': lambda: _check_package()[0],
        'feature-flags': lambda: _check_flags()[0],
        'review-changeset': lambda: _check_changeset()[0],
        'deploy-dev': lambda: _check_stage('deploy-dev', 'dev')[0],
        'deploy-prod': lambda: _check_stage('deploy-prod', 'prod')[0],
        'update-dev': lambda: _check_update()[0],
        'flag-prod': lambda: _check_flag_prod()[0],
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


def reset() -> dict[str, Any]:
    """Delete both stacks, the AppConfig application, and the artifact bucket."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('NoSuchBucket', 'ValidationError'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudformation, appconfig, s3 = _client('cloudformation'), _client('appconfig'), _client('s3')
    for stage in STAGES:
        try:
            exists = _stack(stage) is not None or bool(cloudformation.describe_stacks(StackName=STACKS[stage]).get('Stacks'))
        except ClientError:
            exists = False
        if exists:
            attempt(f'stack:{STACKS[stage]}', lambda stage=stage: cloudformation.delete_stack(StackName=STACKS[stage]))
    for stage in STAGES:
        _wait(lambda stage=stage: _stack(stage) is None, timeout=120, delay=2)
    try:
        ids = _flags_ids()
        app = _app()
    except (ClientError, BotoCoreError) as exc:
        ids, app = None, None
        errors.append(f'appconfig lookup: {exc}')
    if app:
        app_id = app['Id']
        if ids:
            versions = appconfig.list_hosted_configuration_versions(
                ApplicationId=app_id, ConfigurationProfileId=ids['profile']).get('Items', [])
            for version in versions:
                attempt(f'flags-version:{version["VersionNumber"]}', lambda version=version: appconfig.delete_hosted_configuration_version(
                    ApplicationId=app_id, ConfigurationProfileId=ids['profile'], VersionNumber=version['VersionNumber']))
            attempt(f'profile:{FLAGS_PROFILE}', lambda: appconfig.delete_configuration_profile(
                ApplicationId=app_id, ConfigurationProfileId=ids['profile']))
        for name, environment_id in _environments(app_id).items():
            attempt(f'environment:{name}', lambda environment_id=environment_id: appconfig.delete_environment(
                ApplicationId=app_id, EnvironmentId=environment_id))
        attempt(f'application:{FLAGS_APP}', lambda: appconfig.delete_application(ApplicationId=app_id))
    strategy = None
    try:
        strategy = _strategy()
    except (ClientError, BotoCoreError):
        pass
    if strategy:
        attempt(f'strategy:{STRATEGY}', lambda: appconfig.delete_deployment_strategy(DeploymentStrategyId=strategy['Id']))
    try:
        versions = s3.list_object_versions(Bucket=BUCKET)
        objects = [{'Key': item['Key'], 'VersionId': item['VersionId']}
                   for item in versions.get('Versions', []) + versions.get('DeleteMarkers', [])]
        if objects:
            s3.delete_objects(Bucket=BUCKET, Delete={'Objects': objects})
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))
    except ClientError as exc:
        if _code(exc) != 'NoSuchBucket':
            errors.append(f'bucket:{BUCKET}: {exc}')
    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws cloudformation delete-stack --stack-name {STACKS["dev"]}\n'
            f'aws cloudformation delete-stack --stack-name {STACKS["prod"]}\n'
            'aws appconfig delete-hosted-configuration-version / delete-configuration-profile / delete-environment / '
            'delete-application / delete-deployment-strategy ...\n'
            f'aws s3 rb s3://{BUCKET} --force'
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
            else 'Both stacks, the flags application, and the artifact bucket are deleted.',
        },
    }
