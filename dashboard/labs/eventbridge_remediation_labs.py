"""Event-driven remediation lab: revert unapproved Parameter Store changes and make public uploads private from their
EventBridge events, report compliance to a custom AWS Config rule, gate automatic fixes on a composite alarm that
spots a change storm during a deployment, run the response as a Step Functions workflow, and escalate instead of
fixing while the storm lasts.

Floci 2.4.0 publishes real Parameter Store Change and S3 Object Created events, runs the Lambda functions and the
workflow for real, records Config evaluations sent with PutEvaluations, evaluates metric alarms about once a minute,
and evaluates composite alarms when a child changes state. What Floci does not do: it never invokes a custom Config
rule's function (the lab invokes it the way a periodic trigger would), it has no Config remediation or SSM Automation
(so Lambda and Step Functions do the fixing), and PutEvaluations takes the rule name as its result token.
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

SERVICE = 'eventbridge'
LAB_KEY = 'drift-remediation'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

PARAMETER_PREFIX = '/lab-remediate/app/'
LOG_LEVEL = f'{PARAMETER_PREFIX}log-level'
MAX_CONNECTIONS = f'{PARAMETER_PREFIX}max-connections'
APPROVED = {LOG_LEVEL: 'INFO', MAX_CONNECTIONS: '50'}
BASELINE_PARAMETER = '/lab-remediate/approved'
LOG_TABLE = 'lab-remediate-log'
BUCKET = 'lab-remediate-uploads'
PUBLIC_KEY = 'reports/q3-revenue.csv'
ALL_USERS = 'http://acs.amazonaws.com/groups/global/AllUsers'

FUNCTION = 'lab-remediate-fixer'
FUNCTION_ROLE = 'lab-remediate-fixer-role'
FUNCTION_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FUNCTION_ROLE}'
FUNCTION_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
RULE = 'lab-remediate-drift'
LAMBDA_TARGET = 'fixer'
WORKFLOW_TARGET = 'workflow'

CONFIG_RULE = 'lab-remediate-approved-settings'

NAMESPACE = 'LabRemediate'
DRIFT_ALARM = 'lab-remediate-drift-rate'
DEPLOY_ALARM = 'lab-remediate-deployment'
STORM_ALARM = 'lab-remediate-storm'
STORM_RULE = f'ALARM("{DRIFT_ALARM}") AND ALARM("{DEPLOY_ALARM}")'
STORM_THRESHOLD = 3

WORKFLOW = 'lab-remediate-workflow'
WORKFLOW_ROLE = 'lab-remediate-workflow-role'
WORKFLOW_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{WORKFLOW_ROLE}'
WORKFLOW_ARN = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{WORKFLOW}'
EVENTS_ROLE = 'lab-remediate-events-role'
EVENTS_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{EVENTS_ROLE}'
ESCALATION_QUEUE = 'lab-remediate-escalations'
INLINE_POLICY = 'lab-remediate'

EVENT_WAIT_SECONDS = 45
ALARM_WAIT_SECONDS = 150

FLOCI = 'How Floci emulates this: '

FIXER_SOURCE = '''"""Inspect a change from its event, put it back to the approved state, and record what changed."""
import json
import os
import time

import boto3

ssm, s3, dynamodb = boto3.client('ssm'), boto3.client('s3'), boto3.client('dynamodb')
config, cloudwatch = boto3.client('config'), boto3.client('cloudwatch')
BASELINE, LOG_TABLE, RULE = os.environ['BASELINE_PARAMETER'], os.environ['LOG_TABLE'], os.environ['CONFIG_RULE']
NAMESPACE, STORM_ALARM, BUCKET = os.environ['NAMESPACE'], os.environ['STORM_ALARM'], os.environ['BUCKET']
ALL_USERS = 'http://acs.amazonaws.com/groups/global/AllUsers'


def approved():
    return json.loads(ssm.get_parameter(Name=BASELINE)['Parameter']['Value'])


def storm_state():
    alarms = cloudwatch.describe_alarms(AlarmNames=[STORM_ALARM], AlarmTypes=['CompositeAlarm'])['CompositeAlarms']
    return alarms[0]['StateValue'] if alarms else 'OK'


def record(finding, action):
    dynamodb.put_item(TableName=LOG_TABLE, Item={
        'id': {'S': str(time.time_ns())}, 'resource': {'S': finding['resource']}, 'kind': {'S': finding['kind']},
        'found': {'S': str(finding['current'])}, 'restored': {'S': str(finding['approved'])},
        'action': {'S': action}, 'at': {'N': str(int(time.time()))}})


def report(finding, compliance):
    if finding['kind'] != 'parameter':
        return
    try:
        config.put_evaluations(ResultToken=RULE, Evaluations=[{
            'ComplianceResourceType': 'AWS::SSM::Parameter', 'ComplianceResourceId': finding['resource'],
            'ComplianceType': compliance, 'OrderingTimestamp': time.time(),
            'Annotation': f"value {finding['current']!r}, approved {finding['approved']!r}"[:255]}])
    except config.exceptions.NoSuchConfigRuleException:
        pass  # the Config rule is added later; fixing does not depend on it


def inspect(event):
    detail = event.get('detail') or {}
    if event.get('source') == 'aws.ssm':
        name, wanted = detail.get('name'), approved()
        if name not in wanted or detail.get('operation') == 'Delete':
            return {'drifted': False, 'resource': name, 'kind': 'parameter', 'reason': 'not managed'}
        current = ssm.get_parameter(Name=name)['Parameter']['Value']
        finding = {'kind': 'parameter', 'resource': name, 'current': current, 'approved': wanted[name],
                   'drifted': current != wanted[name]}
    elif event.get('source') == 'aws.s3':
        bucket, key = (detail.get('bucket') or {}).get('name'), (detail.get('object') or {}).get('key')
        if bucket != BUCKET:
            return {'drifted': False, 'resource': f'{bucket}/{key}', 'kind': 'object', 'reason': 'not managed'}
        grants = s3.get_object_acl(Bucket=bucket, Key=key)['Grants']
        public = sorted({grant['Permission'] for grant in grants if grant['Grantee'].get('URI') == ALL_USERS})
        finding = {'kind': 'object', 'resource': f'{bucket}/{key}', 'current': 'public ' + ','.join(public)
                   if public else 'private', 'approved': 'private', 'drifted': bool(public)}
    else:
        return {'drifted': False, 'resource': None, 'kind': None, 'reason': 'unknown source'}
    if finding['drifted']:
        cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=[{'MetricName': 'DriftDetected', 'Value': 1}])
        report(finding, 'NON_COMPLIANT')
    finding['storm'] = storm_state()
    return finding


def fix(finding):
    if finding['kind'] == 'parameter':
        ssm.put_parameter(Name=finding['resource'], Value=finding['approved'], Type='String', Overwrite=True)
        report(finding, 'COMPLIANT')
    else:
        bucket, key = finding['resource'].split('/', 1)
        s3.put_object_acl(Bucket=bucket, Key=key, ACL='private')
    record(finding, 'restored')
    cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=[{'MetricName': 'DriftFixed', 'Value': 1}])
    return {**finding, 'fixed': True}


def evaluate_all():
    results = []
    for name, wanted in approved().items():
        current = ssm.get_parameter(Name=name)['Parameter']['Value']
        finding = {'kind': 'parameter', 'resource': name, 'current': current, 'approved': wanted}
        report(finding, 'COMPLIANT' if current == wanted else 'NON_COMPLIANT')
        results.append({**finding, 'compliant': current == wanted})
    return results


def handler(event, context):
    action = event.get('action')
    if action == 'evaluate':
        return evaluate_all()
    if action == 'inspect':
        return inspect(event['event'])
    if action == 'fix':
        return fix(event['finding'])
    finding = inspect(event)
    result = fix(finding) if finding['drifted'] else finding
    print(json.dumps(result))
    return result
'''


def event_pattern() -> dict[str, Any]:
    return {'$or': [
        {'source': ['aws.ssm'], 'detail-type': ['Parameter Store Change'],
         'detail': {'name': [{'prefix': PARAMETER_PREFIX}], 'operation': ['Create', 'Update']}},
        {'source': ['aws.s3'], 'detail-type': ['Object Created'], 'detail': {'bucket': {'name': [BUCKET]}}},
    ]}


def workflow_definition(queue_url: str) -> dict[str, Any]:
    return {
        'Comment': 'Inspect a change, fix it unless a change storm is on, and escalate while it is.',
        'StartAt': 'Inspect',
        'States': {
            'Inspect': {
                'Type': 'Task', 'Resource': 'arn:aws:states:::lambda:invoke',
                'Parameters': {'FunctionName': FUNCTION_ARN, 'Payload': {'action': 'inspect', 'event.$': '$'}},
                'ResultSelector': {'finding.$': '$.Payload'}, 'ResultPath': '$.check', 'Next': 'Drifted?',
                'Retry': [{'ErrorEquals': ['Lambda.TooManyRequestsException', 'Lambda.ServiceException'],
                           'IntervalSeconds': 1, 'MaxAttempts': 2}],
            },
            'Drifted?': {
                'Type': 'Choice',
                'Choices': [
                    {'Variable': '$.check.finding.drifted', 'BooleanEquals': False, 'Next': 'NoDrift'},
                    {'Variable': '$.check.finding.storm', 'StringEquals': 'ALARM', 'Next': 'Escalate'},
                ],
                'Default': 'Fix',
            },
            'NoDrift': {'Type': 'Succeed'},
            'Fix': {
                'Type': 'Task', 'Resource': 'arn:aws:states:::lambda:invoke',
                'Parameters': {'FunctionName': FUNCTION_ARN,
                               'Payload': {'action': 'fix', 'finding.$': '$.check.finding'}},
                'ResultSelector': {'result.$': '$.Payload'}, 'ResultPath': '$.fix', 'End': True,
            },
            'Escalate': {
                'Type': 'Task', 'Resource': 'arn:aws:states:::sqs:sendMessage',
                'Parameters': {'QueueUrl': queue_url, 'MessageBody.$': '$'}, 'ResultPath': '$.escalation',
                'Next': 'RecordEscalation',
            },
            'RecordEscalation': {
                'Type': 'Task', 'Resource': 'arn:aws:states:::dynamodb:putItem',
                'Parameters': {'TableName': LOG_TABLE, 'Item': {
                    'id': {'S.$': '$$.Execution.Name'}, 'resource': {'S.$': '$.check.finding.resource'},
                    'kind': {'S.$': '$.check.finding.kind'}, 'found': {'S.$': '$.check.finding.current'},
                    'restored': {'S': '-'}, 'action': {'S': 'escalated'}}},
                'ResultPath': None, 'End': True,
            },
        },
    }


def _trust(service: str) -> dict[str, Any]:
    return {'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole'}]}


FIXER_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'ReadAndRestoreSettings', 'Effect': 'Allow', 'Action': ['ssm:GetParameter', 'ssm:PutParameter'],
         'Resource': f'arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/lab-remediate/*'},
        {'Sid': 'MakeUploadsPrivate', 'Effect': 'Allow', 'Action': ['s3:GetObjectAcl', 's3:PutObjectAcl'],
         'Resource': f'arn:aws:s3:::{BUCKET}/*'},
        {'Sid': 'RecordChanges', 'Effect': 'Allow', 'Action': 'dynamodb:PutItem',
         'Resource': f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{LOG_TABLE}'},
        {'Sid': 'ReportCompliance', 'Effect': 'Allow', 'Action': 'config:PutEvaluations', 'Resource': '*'},
        {'Sid': 'Metrics', 'Effect': 'Allow', 'Action': ['cloudwatch:PutMetricData', 'cloudwatch:DescribeAlarms'],
         'Resource': '*'},
        {'Sid': 'Logs', 'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
         'Resource': '*'},
    ],
}
WORKFLOW_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Effect': 'Allow', 'Action': 'lambda:InvokeFunction', 'Resource': FUNCTION_ARN},
        {'Effect': 'Allow', 'Action': 'sqs:SendMessage',
         'Resource': f'arn:aws:sqs:{REGION}:{ACCOUNT}:{ESCALATION_QUEUE}'},
        {'Effect': 'Allow', 'Action': 'dynamodb:PutItem',
         'Resource': f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{LOG_TABLE}'},
    ],
}
EVENTS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Action': 'states:StartExecution', 'Resource': WORKFLOW_ARN}],
}


# ── Commands ─────────────────────────────────────────────────────────────────

BASELINE_COMMAND = (
    f'aws ssm put-parameter --name {LOG_LEVEL} --type String --value INFO\n'
    f'aws ssm put-parameter --name {MAX_CONNECTIONS} --type String --value 50\n'
    f'aws ssm put-parameter --name {BASELINE_PARAMETER} --type String --value \'{json.dumps(APPROVED)}\'\n'
    f'aws dynamodb create-table --table-name {LOG_TABLE} --attribute-definitions AttributeName=id,AttributeType=S '
    '--key-schema AttributeName=id,KeyType=HASH --billing-mode PAY_PER_REQUEST\n'
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3api put-bucket-ownership-controls --bucket {BUCKET} '
    '--ownership-controls Rules=[{ObjectOwnership=ObjectWriter}]   # ACLs on, so an upload can be public\n'
    f'aws s3api put-bucket-notification-configuration --bucket {BUCKET} '
    '--notification-configuration \'{"EventBridgeConfiguration":{}}\''
)
REVERT_COMMAND = (
    f'aws iam create-role --role-name {FUNCTION_ROLE} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime python3.12 --handler index.handler '
    f'--role {FUNCTION_ROLE_ARN} --zip-file fileb://fixer.zip\n'
    f'aws events put-rule --name {RULE} --event-pattern file://drift-pattern.json\n'
    f'aws events put-targets --rule {RULE} --targets Id={LAMBDA_TARGET},Arn={FUNCTION_ARN}\n'
    f'aws lambda add-permission --function-name {FUNCTION} --statement-id events --action lambda:InvokeFunction '
    f'--principal events.amazonaws.com --source-arn <rule-arn>\n'
    f'aws ssm put-parameter --name {LOG_LEVEL} --value DEBUG --overwrite   # an unapproved change\n'
    f'aws ssm get-parameter --name {LOG_LEVEL}   # INFO again'
)
PUBLIC_COMMAND = (
    f'aws s3api put-object --bucket {BUCKET} --key {PUBLIC_KEY} --body q3-revenue.csv --acl public-read\n'
    f'aws s3api get-object-acl --bucket {BUCKET} --key {PUBLIC_KEY}   # the AllUsers grant is gone'
)
CONFIG_COMMAND = (
    f'aws configservice put-config-rule --config-rule \'{{"ConfigRuleName":"{CONFIG_RULE}","Source":{{'
    f'"Owner":"CUSTOM_LAMBDA","SourceIdentifier":"{FUNCTION_ARN}","SourceDetails":[{{"EventSource":"aws.config",'
    '"MessageType":"ScheduledNotification","MaximumExecutionFrequency":"One_Hour"}]},"Scope":{'
    '"ComplianceResourceTypes":["AWS::SSM::Parameter"]}}\'\n'
    f'aws lambda invoke --function-name {FUNCTION} --payload \'{{"action":"evaluate"}}\' out.json   # the periodic run\n'
    f'aws ssm put-parameter --name {MAX_CONNECTIONS} --value 500 --overwrite\n'
    f'aws configservice get-compliance-details-by-config-rule --config-rule-name {CONFIG_RULE}'
)
STORM_COMMAND = (
    f'aws cloudwatch put-metric-alarm --alarm-name {DRIFT_ALARM} --namespace {NAMESPACE} --metric-name DriftDetected '
    f'--statistic Sum --period 60 --evaluation-periods 1 --threshold {STORM_THRESHOLD} '
    '--comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {DEPLOY_ALARM} --namespace {NAMESPACE} '
    '--metric-name DeploymentInProgress --statistic Maximum --period 60 --evaluation-periods 1 --threshold 1 '
    '--comparison-operator GreaterThanOrEqualToThreshold --treat-missing-data notBreaching\n'
    f'aws cloudwatch put-composite-alarm --alarm-name {STORM_ALARM} --alarm-rule \'{STORM_RULE}\''
)
WORKFLOW_COMMAND = (
    f'aws sqs create-queue --queue-name {ESCALATION_QUEUE}\n'
    f'aws stepfunctions create-state-machine --name {WORKFLOW} --role-arn {WORKFLOW_ROLE_ARN} '
    '--definition file://remediation.asl.json\n'
    f'aws events remove-targets --rule {RULE} --ids {LAMBDA_TARGET}\n'
    f'aws events put-targets --rule {RULE} --targets Id={WORKFLOW_TARGET},Arn={WORKFLOW_ARN},RoleArn={EVENTS_ROLE_ARN}\n'
    f'aws ssm put-parameter --name {LOG_LEVEL} --value WARN --overwrite\n'
    f'aws stepfunctions get-execution-history --execution-arn <arn>   # Inspect -> Drifted? -> Fix'
)
ESCALATE_COMMAND = (
    f'aws cloudwatch put-metric-data --namespace {NAMESPACE} --metric-name DeploymentInProgress --value 1   '
    '# the pipeline says a deployment is running\n'
    f'aws ssm put-parameter --name {LOG_LEVEL} --value DEBUG --overwrite   # x{STORM_THRESHOLD}, each one fixed\n'
    f'aws cloudwatch describe-alarms --alarm-names {STORM_ALARM} --alarm-types CompositeAlarm   # ALARM\n'
    f'aws ssm put-parameter --name {LOG_LEVEL} --value TRACE --overwrite\n'
    f'aws sqs receive-message --queue-url <escalations-url>   # escalated, not fixed'
)
RESUME_COMMAND = (
    f'aws cloudwatch describe-alarms --alarm-names {STORM_ALARM} --alarm-types CompositeAlarm   # OK again\n'
    f'aws sqs receive-message --queue-url <escalations-url>\n'
    f'aws stepfunctions start-execution --state-machine-arn {WORKFLOW_ARN} --input <the escalated event>\n'
    f'aws ssm get-parameter --name {LOG_LEVEL}   # INFO'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Remediate configuration drift automatically from events, with a storm gate and escalation',
    'description': (
        'Revert unapproved Parameter Store changes and make public uploads private from their EventBridge events, '
        'report compliance to a custom AWS Config rule, gate automatic fixes on a composite alarm that spots a '
        'change storm during a deployment, run the response as a Step Functions workflow, and escalate instead of '
        'fixing while the storm lasts.'
    ),
    'steps': [
        {
            'key': 'approved-baseline',
            'title': 'Record the approved settings',
            'command': BASELINE_COMMAND,
            'explanation': (
                f'The application reads its log level and connection limit from Parameter Store. The approved values '
                f'live in {BASELINE_PARAMETER}, which only the platform team can change, and every fix is written to '
                f'the {LOG_TABLE} table. Uploads land in {BUCKET}, which sends its object events to EventBridge and '
                'allows ACLs, so a public upload is possible and can be caught. '
                + FLOCI + 'the parameters, table, bucket, ownership setting, and EventBridge notification are real.'
            ),
        },
        {
            'key': 'auto-revert',
            'title': 'Revert an unapproved parameter change from its event',
            'command': REVERT_COMMAND,
            'explanation': (
                'Parameter Store sends a Parameter Store Change event for every create, update, and delete. A rule '
                f'matching parameters under {PARAMETER_PREFIX} invokes {FUNCTION}, which compares the value with the '
                'approved one, puts it back, and records the change. Its own fix sends another event, which it '
                'ignores because the value now matches. '
                + FLOCI + 'the event, the rule, and the function are real, and the value is back to INFO a few seconds '
                'after the change.'
            ),
        },
        {
            'key': 'public-object',
            'title': 'Make a public upload private',
            'command': PUBLIC_COMMAND,
            'explanation': (
                f'Someone uploads {PUBLIC_KEY} with a public-read ACL. The Object Created event reaches the same rule, '
                'and the function removes the AllUsers grant. The preventive control is S3 Block Public Access and '
                'BucketOwnerEnforced ownership, which this bucket turned off to show the detective one. '
                + FLOCI + 'the event, the ACL, and the fix are real.'
            ),
        },
        {
            'key': 'config-compliance',
            'title': 'Report compliance to a custom Config rule',
            'command': CONFIG_COMMAND,
            'explanation': (
                f'{CONFIG_RULE} is a custom rule backed by the same function. AWS Config would invoke it on a '
                'schedule and on configuration changes, with a result token the function passes back to '
                'PutEvaluations. The function reports NON_COMPLIANT when it finds drift and COMPLIANT once it has '
                'fixed it, and the next periodic run confirms every setting. On AWS, Config remediation actions and SSM '
                'Automation runbooks are the built-in way to fix a noncompliant resource. '
                + FLOCI + 'the rule and its evaluations are real, but Floci never invokes a custom rule\'s function, '
                'so the lab invokes it for the periodic run. Floci reads the rule name as the result token. Floci '
                'has no Config remediation or SSM Automation, which is why Lambda does the fixing.'
            ),
        },
        {
            'key': 'storm-alarm',
            'title': 'Build a composite alarm that spots a change storm',
            'command': STORM_COMMAND,
            'explanation': (
                f'Automatic remediation should not fight a deployment. {DRIFT_ALARM} goes into ALARM when '
                f'{STORM_THRESHOLD} or more drifts are found in a minute, and {DEPLOY_ALARM} when the pipeline reports '
                f'a deployment in progress. The composite alarm {STORM_ALARM} is in ALARM only when both are, which '
                'cuts the noise of either signal alone; composite alarms can also suppress their actions while '
                'another alarm is active. '
                + FLOCI + 'the metric alarms are evaluated about once a minute and the composite alarm is evaluated '
                'whenever a child changes state.'
            ),
        },
        {
            'key': 'remediation-workflow',
            'title': 'Move the response into a Step Functions workflow',
            'command': WORKFLOW_COMMAND,
            'explanation': (
                f'{WORKFLOW} inspects the change, checks the storm alarm, and either fixes it or escalates it to the '
                f'{ESCALATION_QUEUE} queue and records that it did. The rule now starts the workflow instead of the '
                'function, so each response has an execution history that shows which path it took. '
                + FLOCI + 'the rule starts the workflow, and the Lambda, SQS, and DynamoDB tasks run for real.'
            ),
        },
        {
            'key': 'storm-escalation',
            'title': 'Escalate instead of fixing during a deployment',
            'command': ESCALATE_COMMAND,
            'explanation': (
                f'The pipeline reports a deployment and the log level changes {STORM_THRESHOLD} times in a minute. The '
                'workflow fixes each one, and both alarms, then the composite alarm, go into ALARM. The next change '
                'is escalated: it goes to the queue with the event that caused it, the log level stays as it is, and the '
                'log table records the escalation. '
                + FLOCI + 'the alarms really change state, about a minute after the data arrives, so the step can '
                'take two minutes.'
            ),
        },
        {
            'key': 'resume-after-storm',
            'title': 'Resume when the storm clears and re-drive the escalation',
            'command': RESUME_COMMAND,
            'explanation': (
                'When no data arrives for a minute, both alarms return to OK and so does the composite alarm. The '
                'on-call engineer re-drives the escalated event through the workflow, which now fixes it. '
                + FLOCI + 'the alarms return to OK on their own and the re-driven execution is real.'
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


# ── Pure helpers ─────────────────────────────────────────────────────────────

def is_public(grants: list[dict[str, Any]]) -> bool:
    return any((grant.get('Grantee') or {}).get('URI') == ALL_USERS for grant in grants)


def execution_path(events: list[dict[str, Any]]) -> list[str]:
    """The states an execution entered, in order, from its history."""
    return [event['stateEnteredEventDetails']['name'] for event in events
            if event.get('type', '').endswith('StateEntered') and event.get('stateEnteredEventDetails')]


# ── Lookups ──────────────────────────────────────────────────────────────────

def _parameter(name: str) -> str | None:
    return _safe_call(lambda: _client('ssm').get_parameter(Name=name)['Parameter']['Value'])


def _log() -> list[dict[str, Any]]:
    items = _safe_call(lambda: _client('dynamodb').scan(TableName=LOG_TABLE).get('Items', []), [])
    rows = [{key: next(iter(value.values())) for key, value in item.items()} for item in items]
    return sorted(rows, key=lambda row: row.get('id', ''))


def _acl(key: str) -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('s3').get_object_acl(Bucket=BUCKET, Key=key)['Grants'], [])


def _function() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('lambda').get_function(FunctionName=FUNCTION)['Configuration'])


def _targets() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('events').list_targets_by_rule(Rule=RULE).get('Targets', []), [])


def _alarm_state(name: str, composite: bool = False) -> str | None:
    kind = 'CompositeAlarms' if composite else 'MetricAlarms'
    alarms = _safe_call(lambda: _client('cloudwatch').describe_alarms(
        AlarmNames=[name], AlarmTypes=['CompositeAlarm' if composite else 'MetricAlarm'])[kind], [])
    return alarms[0]['StateValue'] if alarms else None


def _queue_url() -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=ESCALATION_QUEUE)['QueueUrl'])


def _compliance() -> dict[str, str]:
    results = _safe_call(lambda: _client('config').get_compliance_details_by_config_rule(
        ConfigRuleName=CONFIG_RULE).get('EvaluationResults', []), [])
    return {result['EvaluationResultIdentifier']['EvaluationResultQualifier']['ResourceId']: result['ComplianceType']
            for result in results}


def _executions() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('stepfunctions').list_executions(
        stateMachineArn=WORKFLOW_ARN, maxResults=50).get('executions', []), [])


def _history(execution_arn: str) -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('stepfunctions').get_execution_history(
        executionArn=execution_arn, maxResults=200).get('events', []), [])


def _wait_execution(known: set[str], timeout: float = EVENT_WAIT_SECONDS) -> dict[str, Any] | None:
    """Wait for the executions a change starts to finish, and return the first with its path.

    A fix changes the parameter again, which starts a second execution that finds nothing to do, so the first
    execution is the one that answered the change."""
    finished: list[dict[str, Any]] = []

    def settled() -> bool:
        new = [execution for execution in _executions() if execution['executionArn'] not in known]
        if not new or any(execution.get('status') == 'RUNNING' for execution in new):
            return False
        finished[:] = sorted(new, key=lambda execution: str(execution.get('startDate')))
        return True
    if not _wait(settled, timeout):
        return None
    time.sleep(1.5)
    settled()
    first = dict(finished[0])
    first['path'] = execution_path(_history(first['executionArn']))
    first['executions'] = len(finished)
    return first


def _change(name: str, value: str) -> None:
    _client('ssm').put_parameter(Name=name, Value=value, Type='String', Overwrite=True)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_baseline() -> tuple[bool, str]:
    baseline = _parameter(BASELINE_PARAMETER)
    if not baseline or json.loads(baseline) != APPROVED:
        return False, f'Record the approved settings in {BASELINE_PARAMETER}.'
    if any(_parameter(name) is None for name in APPROVED):
        return False, 'Create the application parameters.'
    if not _safe_call(lambda: _client('dynamodb').describe_table(TableName=LOG_TABLE)):
        return False, f'Create the {LOG_TABLE} table.'
    notification = _safe_call(lambda: _client('s3').get_bucket_notification_configuration(Bucket=BUCKET), {})
    if 'EventBridgeConfiguration' not in (notification or {}):
        return False, f'Turn on EventBridge notifications for {BUCKET}.'
    return True, f'{len(APPROVED)} approved settings are recorded and {BUCKET} sends its events to EventBridge.'


def _check_revert() -> tuple[bool, str]:
    marker = _marked('auto-revert') or {}
    if not _function() or not _targets():
        return False, f'Create {FUNCTION} and the {RULE} rule.'
    if marker.get('after') != APPROVED[LOG_LEVEL]:
        return False, f'Change {LOG_LEVEL} and watch it come back.'
    if not any(row.get('resource') == LOG_LEVEL and row.get('action') == 'restored' for row in _log()):
        return False, f'No fix of {LOG_LEVEL} is recorded in {LOG_TABLE}.'
    return True, (f'{LOG_LEVEL} was set to {marker.get("changedTo")} and was back to {marker["after"]} in '
                  f'{marker.get("seconds")} s, with the fix recorded.')


def _check_public() -> tuple[bool, str]:
    marker = _marked('public-object') or {}
    if not marker.get('publicAtUpload'):
        return False, f'Upload {PUBLIC_KEY} with a public-read ACL.'
    grants = _acl(PUBLIC_KEY)
    if not grants or is_public(grants):
        return False, f'{PUBLIC_KEY} is still public.'
    return True, f'{PUBLIC_KEY} was public at upload and is private now.'


def _check_config() -> tuple[bool, str]:
    marker = _marked('config-compliance') or {}
    compliance = _compliance()
    if set(compliance) != set(APPROVED) or set(compliance.values()) != {'COMPLIANT'}:
        return False, f'Every parameter should be COMPLIANT in {CONFIG_RULE}; it shows {compliance}.'
    if 'NON_COMPLIANT' not in (marker.get('seen') or []):
        return False, f'Change {MAX_CONNECTIONS} and watch the rule report it.'
    return True, (f'{CONFIG_RULE} saw {MAX_CONNECTIONS} go NON_COMPLIANT and back to COMPLIANT; both parameters '
                  'are COMPLIANT.')


def _check_storm() -> tuple[bool, str]:
    rule = _safe_call(lambda: _client('cloudwatch').describe_alarms(
        AlarmNames=[STORM_ALARM], AlarmTypes=['CompositeAlarm'])['CompositeAlarms'][0]['AlarmRule'])
    if rule != STORM_RULE:
        return False, f'Create the {STORM_ALARM} composite alarm.'
    if _alarm_state(DRIFT_ALARM) is None or _alarm_state(DEPLOY_ALARM) is None:
        return False, 'Create both child alarms.'
    return True, f'{STORM_ALARM} is {STORM_RULE}.'


def _check_workflow() -> tuple[bool, str]:
    marker = _marked('remediation-workflow') or {}
    if [target.get('Arn') for target in _targets()] != [WORKFLOW_ARN]:
        return False, f'Point {RULE} at {WORKFLOW} only.'
    if 'Fix' not in (marker.get('path') or []) or marker.get('after') != APPROVED[LOG_LEVEL]:
        return False, 'Run a change through the workflow and watch it fix it.'
    return True, f'The rule started {WORKFLOW}, which took {" -> ".join(marker["path"])} and restored {LOG_LEVEL}.'


def _check_escalation() -> tuple[bool, str]:
    marker = _marked('storm-escalation') or {}
    if marker.get('storm') != 'ALARM':
        return False, f'Make {STORM_ALARM} go into ALARM.'
    if 'Escalate' not in (marker.get('path') or []):
        return False, 'The change during the storm was not escalated.'
    if not marker.get('queued') or marker.get('valueAfter') == APPROVED[LOG_LEVEL]:
        return False, 'The escalated change should wait in the queue, unfixed.'
    return True, (f'During the storm {LOG_LEVEL}={marker["valueAfter"]} was escalated to {ESCALATION_QUEUE} '
                  'instead of being fixed.')


def _check_resume() -> tuple[bool, str]:
    marker = _marked('resume-after-storm') or {}
    if marker.get('storm') != 'OK':
        return False, f'Wait for {STORM_ALARM} to return to OK.'
    if 'Fix' not in (marker.get('path') or []) or _parameter(LOG_LEVEL) != APPROVED[LOG_LEVEL]:
        return False, 'Re-drive the escalated event through the workflow.'
    return True, f'{STORM_ALARM} is OK and the re-driven event restored {LOG_LEVEL} to {APPROVED[LOG_LEVEL]}.'


CHECKS = {
    'approved-baseline': _check_baseline,
    'auto-revert': _check_revert,
    'public-object': _check_public,
    'config-compliance': _check_config,
    'storm-alarm': _check_storm,
    'remediation-workflow': _check_workflow,
    'storm-escalation': _check_escalation,
    'resume-after-storm': _check_resume,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def step_approved_baseline() -> dict[str, Any]:
    started = time.perf_counter()
    for name, value in APPROVED.items():
        _change(name, value)
    _change(BASELINE_PARAMETER, json.dumps(APPROVED))
    dynamodb = _client('dynamodb')
    if not _safe_call(lambda: dynamodb.describe_table(TableName=LOG_TABLE)):
        dynamodb.create_table(TableName=LOG_TABLE, AttributeDefinitions=[{'AttributeName': 'id', 'AttributeType': 'S'}],
                              KeySchema=[{'AttributeName': 'id', 'KeyType': 'HASH'}], BillingMode='PAY_PER_REQUEST',
                              Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        dynamodb.get_waiter('table_exists').wait(TableName=LOG_TABLE, WaiterConfig={'Delay': 1, 'MaxAttempts': 30})
    s3 = _client('s3')
    try:
        s3.head_bucket(Bucket=BUCKET)
    except ClientError:
        s3.create_bucket(Bucket=BUCKET)
    s3.put_bucket_ownership_controls(Bucket=BUCKET,
                                     OwnershipControls={'Rules': [{'ObjectOwnership': 'ObjectWriter'}]})
    _safe_call(lambda: s3.delete_public_access_block(Bucket=BUCKET))
    s3.put_bucket_notification_configuration(Bucket=BUCKET,
                                             NotificationConfiguration={'EventBridgeConfiguration': {}})
    response = {'Approved': APPROVED, 'Parameters': {name: _parameter(name) for name in APPROVED},
                'LogTable': LOG_TABLE, 'Bucket': BUCKET,
                'Notifications': _safe_call(lambda: s3.get_bucket_notification_configuration(Bucket=BUCKET))}
    verified, message = _check_baseline()
    return _result('approved-baseline', BASELINE_COMMAND, response, verified, message, started)


def _fixer_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('index.py', FIXER_SOURCE)
    return buffer.getvalue()


def _ensure_role(name: str, trust: dict[str, Any], policy: dict[str, Any], description: str) -> None:
    iam = _client('iam')
    try:
        iam.get_role(RoleName=name)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description)
    iam.put_role_policy(RoleName=name, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(policy))


def _ensure_function() -> None:
    _ensure_role(FUNCTION_ROLE, _trust('lambda.amazonaws.com'), FIXER_POLICY, 'Drift remediation function')
    lam = _client('lambda')
    environment = {'Variables': {'BASELINE_PARAMETER': BASELINE_PARAMETER, 'LOG_TABLE': LOG_TABLE,
                                 'CONFIG_RULE': CONFIG_RULE, 'NAMESPACE': NAMESPACE, 'STORM_ALARM': STORM_ALARM,
                                 'BUCKET': BUCKET}}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_fixer_zip())
        _wait(lambda: (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=environment)
    else:
        lam.create_function(FunctionName=FUNCTION, Runtime='python3.12', Role=FUNCTION_ROLE_ARN,
                            Handler='index.handler', Code={'ZipFile': _fixer_zip()}, Timeout=30,
                            Environment=environment, Description='Reverts unapproved changes',
                            Tags={'Lab': LAB_KEY})
    _wait(lambda: (_function() or {}).get('State', 'Active') == 'Active'
          and (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)


def _put_rule() -> str:
    return _client('events').put_rule(Name=RULE, EventPattern=json.dumps(event_pattern()), State='ENABLED',
                                      Description='Unapproved settings and public uploads')['RuleArn']


def step_auto_revert() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_baseline()[0]:
        return _result('auto-revert', REVERT_COMMAND, {}, False, 'Record the approved settings first.', started)
    _ensure_function()
    rule_arn = _put_rule()
    events = _client('events')
    stale = [target['Id'] for target in _targets() if target['Id'] != LAMBDA_TARGET]
    if stale:
        events.remove_targets(Rule=RULE, Ids=stale)
    events.put_targets(Rule=RULE, Targets=[{'Id': LAMBDA_TARGET, 'Arn': FUNCTION_ARN}])
    lam = _client('lambda')
    _safe_call(lambda: lam.remove_permission(FunctionName=FUNCTION, StatementId='events'))
    lam.add_permission(FunctionName=FUNCTION, StatementId='events', Action='lambda:InvokeFunction',
                       Principal='events.amazonaws.com', SourceArn=rule_arn)
    _change(LOG_LEVEL, 'DEBUG')
    changed = time.monotonic()
    _wait(lambda: _parameter(LOG_LEVEL) == APPROVED[LOG_LEVEL], EVENT_WAIT_SECONDS)
    marker = {'changedTo': 'DEBUG', 'after': _parameter(LOG_LEVEL), 'seconds': round(time.monotonic() - changed, 1)}
    _marker('auto-revert', marker)
    response = {'EventPattern': event_pattern(), 'Targets': _targets(), **marker, 'Log': _log()[-3:]}
    verified, message = _check_revert()
    return _result('auto-revert', REVERT_COMMAND, response, verified, message, started)


def step_public_object() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_revert()[0]:
        return _result('public-object', PUBLIC_COMMAND, {}, False, 'Revert a parameter change first.', started)
    _client('s3').put_object(Bucket=BUCKET, Key=PUBLIC_KEY, Body=b'region,revenue\nus-east-1,1200000\n',
                             ACL='public-read', ContentType='text/csv')
    at_upload = _acl(PUBLIC_KEY)
    _wait(lambda: not is_public(_acl(PUBLIC_KEY)), EVENT_WAIT_SECONDS)
    marker = {'publicAtUpload': is_public(at_upload) or bool(_marked('public-object') or {}).get('publicAtUpload')}
    # The fix can land before the first read; the log shows it was public then.
    if not marker['publicAtUpload']:
        marker['publicAtUpload'] = any(row.get('resource') == f'{BUCKET}/{PUBLIC_KEY}' for row in _log())
    _marker('public-object', marker)
    response = {'GrantsAtUpload': at_upload, 'GrantsNow': _acl(PUBLIC_KEY), 'Log': _log()[-2:]}
    verified, message = _check_public()
    return _result('public-object', PUBLIC_COMMAND, response, verified, message, started)


def step_config_compliance() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_public()[0]:
        return _result('config-compliance', CONFIG_COMMAND, {}, False, 'Make the public upload private first.', started)
    config = _client('config')
    config.put_config_rule(ConfigRule={
        'ConfigRuleName': CONFIG_RULE,
        'Description': 'Application parameters match the approved baseline',
        'Scope': {'ComplianceResourceTypes': ['AWS::SSM::Parameter']},
        'Source': {'Owner': 'CUSTOM_LAMBDA', 'SourceIdentifier': FUNCTION_ARN, 'SourceDetails': [
            {'EventSource': 'aws.config', 'MessageType': 'ScheduledNotification',
             'MaximumExecutionFrequency': 'One_Hour'},
            {'EventSource': 'aws.config', 'MessageType': 'ConfigurationItemChangeNotification'}]},
    })
    periodic = json.loads(_client('lambda').invoke(FunctionName=FUNCTION,
                                                   Payload=json.dumps({'action': 'evaluate'}))['Payload'].read())
    seen: list[str] = []

    def watch() -> bool:
        state = _compliance().get(MAX_CONNECTIONS)
        if state and (not seen or seen[-1] != state):
            seen.append(state)
        fixed = any(row.get('resource') == MAX_CONNECTIONS and row.get('found') == '500' for row in _log())
        return state == 'COMPLIANT' and ('NON_COMPLIANT' in seen or fixed)
    _change(MAX_CONNECTIONS, '500')
    _wait(watch, EVENT_WAIT_SECONDS, delay=0.3)
    if 'NON_COMPLIANT' not in seen and any(
            row.get('resource') == MAX_CONNECTIONS and row.get('found') == '500' for row in _log()):
        # The fix can land between two reads; the log shows the noncompliant value it found.
        seen.insert(0, 'NON_COMPLIANT')
    _wait(lambda: _parameter(MAX_CONNECTIONS) == APPROVED[MAX_CONNECTIONS], 10, delay=0.5)
    # The next periodic run confirms the fixed state, whatever order the event-driven reports arrived in.
    confirmed = json.loads(_client('lambda').invoke(FunctionName=FUNCTION,
                                                    Payload=json.dumps({'action': 'evaluate'}))['Payload'].read())
    _marker('config-compliance', {'seen': seen})
    response = {'PeriodicEvaluation': periodic, 'ConfirmingEvaluation': confirmed,
                'ComplianceSeenForMaxConnections': seen,
                'Compliance': _compliance(), 'MaxConnections': _parameter(MAX_CONNECTIONS)}
    verified, message = _check_config()
    return _result('config-compliance', CONFIG_COMMAND, response, verified, message, started)


def step_storm_alarm() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_config()[0]:
        return _result('storm-alarm', STORM_COMMAND, {}, False, 'Report compliance first.', started)
    cloudwatch = _client('cloudwatch')
    common = {'Namespace': NAMESPACE, 'Period': 60, 'EvaluationPeriods': 1,
              'ComparisonOperator': 'GreaterThanOrEqualToThreshold', 'TreatMissingData': 'notBreaching'}
    cloudwatch.put_metric_alarm(AlarmName=DRIFT_ALARM, MetricName='DriftDetected', Statistic='Sum',
                                Threshold=STORM_THRESHOLD, AlarmDescription='Many drifts in a minute', **common)
    cloudwatch.put_metric_alarm(AlarmName=DEPLOY_ALARM, MetricName='DeploymentInProgress', Statistic='Maximum',
                                Threshold=1, AlarmDescription='A deployment is in progress', **common)
    cloudwatch.put_composite_alarm(AlarmName=STORM_ALARM, AlarmRule=STORM_RULE,
                                   AlarmDescription='Drift during a deployment: escalate, do not fix')
    response = {'Alarms': {DRIFT_ALARM: _alarm_state(DRIFT_ALARM), DEPLOY_ALARM: _alarm_state(DEPLOY_ALARM),
                           STORM_ALARM: _alarm_state(STORM_ALARM, composite=True)}, 'Rule': STORM_RULE}
    verified, message = _check_storm()
    return _result('storm-alarm', STORM_COMMAND, response, verified, message, started)


def step_remediation_workflow() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_storm()[0]:
        return _result('remediation-workflow', WORKFLOW_COMMAND, {}, False, 'Build the storm alarm first.', started)
    sqs = _client('sqs')
    queue_url = sqs.create_queue(QueueName=ESCALATION_QUEUE, tags={'Lab': LAB_KEY})['QueueUrl']
    _ensure_role(WORKFLOW_ROLE, _trust('states.amazonaws.com'), WORKFLOW_POLICY, 'Drift remediation workflow')
    _ensure_role(EVENTS_ROLE, _trust('events.amazonaws.com'), EVENTS_POLICY, 'Starts the remediation workflow')
    sfn = _client('stepfunctions')
    definition = json.dumps(workflow_definition(queue_url))
    if _safe_call(lambda: sfn.describe_state_machine(stateMachineArn=WORKFLOW_ARN)):
        sfn.update_state_machine(stateMachineArn=WORKFLOW_ARN, definition=definition, roleArn=WORKFLOW_ROLE_ARN)
    else:
        sfn.create_state_machine(name=WORKFLOW, definition=definition, roleArn=WORKFLOW_ROLE_ARN,
                                 tags=[{'key': 'Lab', 'value': LAB_KEY}])
    events = _client('events')
    _put_rule()
    if any(target['Id'] == LAMBDA_TARGET for target in _targets()):
        events.remove_targets(Rule=RULE, Ids=[LAMBDA_TARGET])
    events.put_targets(Rule=RULE, Targets=[{'Id': WORKFLOW_TARGET, 'Arn': WORKFLOW_ARN, 'RoleArn': EVENTS_ROLE_ARN}])
    known = {execution['executionArn'] for execution in _executions()}
    _change(LOG_LEVEL, 'WARN')
    execution = _wait_execution(known) or {}
    _wait(lambda: _parameter(LOG_LEVEL) == APPROVED[LOG_LEVEL], 10)
    marker = {'execution': execution.get('executionArn'), 'status': execution.get('status'),
              'path': execution.get('path'), 'after': _parameter(LOG_LEVEL)}
    _marker('remediation-workflow', marker)
    response = {'Definition': workflow_definition(queue_url), 'Targets': _targets(), **marker}
    verified, message = _check_workflow()
    return _result('remediation-workflow', WORKFLOW_COMMAND, response, verified, message, started)


def step_storm_escalation() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_workflow()[0]:
        return _result('storm-escalation', ESCALATE_COMMAND, {}, False, 'Run the workflow first.', started)
    cloudwatch, sqs = _client('cloudwatch'), _client('sqs')
    queue_url = _queue_url()
    sqs.purge_queue(QueueUrl=queue_url)
    cloudwatch.put_metric_data(Namespace=NAMESPACE, MetricData=[{'MetricName': 'DeploymentInProgress', 'Value': 1}])
    storm_changes = []
    for value in ('DEBUG', 'WARN', 'ERROR')[:STORM_THRESHOLD]:
        known = {execution['executionArn'] for execution in _executions()}
        _change(LOG_LEVEL, value)
        execution = _wait_execution(known) or {}
        storm_changes.append({'value': value, 'path': execution.get('path')})
        _wait(lambda: _parameter(LOG_LEVEL) == APPROVED[LOG_LEVEL], 10)
    in_storm = _wait(lambda: _alarm_state(STORM_ALARM, composite=True) == 'ALARM', ALARM_WAIT_SECONDS, delay=2)
    marker: dict[str, Any] = {'storm': _alarm_state(STORM_ALARM, composite=True), 'stormChanges': storm_changes}
    if in_storm:
        known = {execution['executionArn'] for execution in _executions()}
        _change(LOG_LEVEL, 'TRACE')
        execution = _wait_execution(known) or {}
        time.sleep(2)
        messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10, WaitTimeSeconds=5,
                                       VisibilityTimeout=1).get('Messages', [])
        marker.update({'path': execution.get('path'), 'valueAfter': _parameter(LOG_LEVEL), 'queued': len(messages),
                       'children': {DRIFT_ALARM: _alarm_state(DRIFT_ALARM), DEPLOY_ALARM: _alarm_state(DEPLOY_ALARM)}})
    _marker('storm-escalation', marker)
    response = {**marker, 'Log': _log()[-4:]}
    verified, message = _check_escalation()
    return _result('storm-escalation', ESCALATE_COMMAND, response, verified, message, started)


def step_resume_after_storm() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_escalation()[0]:
        return _result('resume-after-storm', RESUME_COMMAND, {}, False, 'Escalate a change during the storm first.',
                       started)
    _wait(lambda: _alarm_state(STORM_ALARM, composite=True) == 'OK', ALARM_WAIT_SECONDS, delay=2)
    storm = _alarm_state(STORM_ALARM, composite=True)
    sqs, sfn = _client('sqs'), _client('stepfunctions')
    queue_url = _queue_url()
    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, WaitTimeSeconds=5).get('Messages', [])
    marker: dict[str, Any] = {'storm': storm, 'redriven': bool(messages)}
    if messages and storm == 'OK':
        escalated = json.loads(messages[0]['Body'])
        event = {key: value for key, value in escalated.items() if key not in ('check', 'escalation')}
        execution_arn = sfn.start_execution(stateMachineArn=WORKFLOW_ARN, input=json.dumps(event))['executionArn']
        _wait(lambda: (_safe_call(lambda: sfn.describe_execution(executionArn=execution_arn)) or {}).get(
            'status') != 'RUNNING', EVENT_WAIT_SECONDS)
        sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=messages[0]['ReceiptHandle'])
        marker.update({'execution': execution_arn, 'path': execution_path(_history(execution_arn)),
                       'after': _parameter(LOG_LEVEL)})
    _marker('resume-after-storm', marker)
    verified, message = _check_resume()
    return _result('resume-after-storm', RESUME_COMMAND, {**marker, 'Log': _log()[-2:]}, verified, message, started)


RUNNERS = {
    'approved-baseline': step_approved_baseline,
    'auto-revert': step_auto_revert,
    'public-object': step_public_object,
    'config-compliance': step_config_compliance,
    'storm-alarm': step_storm_alarm,
    'remediation-workflow': step_remediation_workflow,
    'storm-escalation': step_storm_escalation,
    'resume-after-storm': step_resume_after_storm,
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
    """Remove the rule, workflow, queue, alarms, Config rule, function, roles, table, bucket, and parameters."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if ('NotFound' in code or 'NoSuch' in code or 'DoesNotExist' in code or 'NonExistent' in code
                    or code in ('ParameterNotFound', 'StateMachineDoesNotExist')):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    events = _client('events')
    targets = [target['Id'] for target in _targets()]
    if targets:
        attempt(f'targets:{RULE}', lambda: events.remove_targets(Rule=RULE, Ids=targets))
    if _safe_call(lambda: events.describe_rule(Name=RULE)):
        attempt(f'rule:{RULE}', lambda: events.delete_rule(Name=RULE))
    if _safe_call(lambda: _client('stepfunctions').describe_state_machine(stateMachineArn=WORKFLOW_ARN)):
        attempt(f'state-machine:{WORKFLOW}', lambda: _client('stepfunctions').delete_state_machine(
            stateMachineArn=WORKFLOW_ARN))
    url = _queue_url()
    if url:
        attempt(f'queue:{ESCALATION_QUEUE}', lambda: _client('sqs').delete_queue(QueueUrl=url))
    cloudwatch = _client('cloudwatch')
    if _alarm_state(STORM_ALARM, composite=True):
        attempt(f'alarm:{STORM_ALARM}', lambda: cloudwatch.delete_alarms(AlarmNames=[STORM_ALARM]))
    existing = [name for name in (DRIFT_ALARM, DEPLOY_ALARM) if _alarm_state(name)]
    if existing:
        attempt('alarms:' + ','.join(existing), lambda: cloudwatch.delete_alarms(AlarmNames=existing))
    if _safe_call(lambda: _client('config').describe_config_rules(ConfigRuleNames=[CONFIG_RULE])):
        attempt(f'config-rule:{CONFIG_RULE}', lambda: _client('config').delete_config_rule(ConfigRuleName=CONFIG_RULE))
    if _function():
        attempt(f'function:{FUNCTION}', lambda: _client('lambda').delete_function(FunctionName=FUNCTION))
    iam = _client('iam')
    for role in (FUNCTION_ROLE, WORKFLOW_ROLE, EVENTS_ROLE):
        if _safe_call(lambda role=role: iam.get_role(RoleName=role)):
            attempt(f'role-policy:{role}', lambda role=role: iam.delete_role_policy(
                RoleName=role, PolicyName=INLINE_POLICY))
            attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))
    if _safe_call(lambda: _client('dynamodb').describe_table(TableName=LOG_TABLE)):
        attempt(f'table:{LOG_TABLE}', lambda: _client('dynamodb').delete_table(TableName=LOG_TABLE))
    s3 = _client('s3')
    if _safe_call(lambda: s3.head_bucket(Bucket=BUCKET)) is not None:
        for item in _safe_call(lambda: s3.list_objects_v2(Bucket=BUCKET).get('Contents', []), []):
            attempt(f'object:{item["Key"]}', lambda key=item['Key']: s3.delete_object(Bucket=BUCKET, Key=key))
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))
    ssm = _client('ssm')
    for name in (*APPROVED, BASELINE_PARAMETER):
        if _parameter(name) is not None:
            attempt(f'parameter:{name}', lambda name=name: ssm.delete_parameter(Name=name))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws events remove-targets --rule {RULE} --ids {WORKFLOW_TARGET} && aws events delete-rule --name {RULE}\n'
            f'aws stepfunctions delete-state-machine --state-machine-arn {WORKFLOW_ARN}\n'
            f'aws cloudwatch delete-alarms --alarm-names {STORM_ALARM} {DRIFT_ALARM} {DEPLOY_ALARM}\n'
            f'aws configservice delete-config-rule --config-rule-name {CONFIG_RULE}\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws dynamodb delete-table --table-name {LOG_TABLE} && aws s3 rb s3://{BUCKET} --force'
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
            else 'The rule, workflow, queue, alarms, Config rule, function, roles, table, bucket, and parameters '
                 'are gone.',
        },
    }
