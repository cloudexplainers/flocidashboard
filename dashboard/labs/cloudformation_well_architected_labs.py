"""Well-Architected review lab: deploy a small orders workload with CloudFormation, review it against the six pillars
of the AWS Well-Architected Framework, fix what the review finds with a change set, prove the reliability fix with a
failure drill, review it again, and spin up a full test copy and throw it away to see the cloud's speed and the
design principles at work.

Floci 2.3.0 creates the stack's S3 bucket, SQS queues, SNS topic and CloudWatch alarm for real, plans and executes
change sets, and moves a message that fails too often to the dead-letter queue. The Well-Architected Tool is not
emulated, so the lab runs the pillar review itself from the deployed settings and says so.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudformation'
LAB_KEY = 'well-architected-review'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

STACK = 'lab-wa-orders'
TEST_STACK = 'lab-wa-orders-test'
CHANGE_SET = 'lab-wa-improvements'
MAX_RECEIVES = 3


def _names(env: str) -> dict[str, str]:
    return {'bucket': f'lab-wa-orders-{env}-uploads', 'queue': f'lab-wa-orders-{env}-jobs',
            'dlq': f'lab-wa-orders-{env}-jobs-dlq', 'topic': f'lab-wa-orders-{env}-alerts',
            'alarm': f'lab-wa-orders-{env}-jobs-age'}


PROD = _names('prod')
TEST = _names('test')

PARAMETERS = {'Env': {'Type': 'String', 'Default': 'prod', 'AllowedValues': ['prod', 'test']}}


def _sub(suffix: str) -> dict[str, str]:
    return {'Fn::Sub': 'lab-wa-orders-${Env}' + suffix}


BASELINE_TEMPLATE = {
    'AWSTemplateFormatVersion': '2010-09-09',
    'Description': 'Orders workload, first draft (Well-Architected review lab)',
    'Parameters': PARAMETERS,
    'Resources': {
        'Uploads': {'Type': 'AWS::S3::Bucket', 'Properties': {'BucketName': _sub('-uploads')}},
        'Jobs': {'Type': 'AWS::SQS::Queue', 'Properties': {'QueueName': _sub('-jobs')}},
    },
}

IMPROVED_TEMPLATE = {
    'AWSTemplateFormatVersion': '2010-09-09',
    'Description': 'Orders workload after the Well-Architected review (Well-Architected review lab)',
    'Parameters': PARAMETERS,
    'Resources': {
        'Uploads': {'Type': 'AWS::S3::Bucket', 'Properties': {
            'BucketName': _sub('-uploads'),
            'BucketEncryption': {'ServerSideEncryptionConfiguration': [
                {'ServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]},
            'PublicAccessBlockConfiguration': {'BlockPublicAcls': True, 'BlockPublicPolicy': True,
                                               'IgnorePublicAcls': True, 'RestrictPublicBuckets': True},
            'VersioningConfiguration': {'Status': 'Enabled'},
            'LifecycleConfiguration': {'Rules': [{
                'Id': 'tier-and-trim', 'Status': 'Enabled',
                'Transitions': [{'StorageClass': 'STANDARD_IA', 'TransitionInDays': 30}],
                'NoncurrentVersionExpiration': {'NoncurrentDays': 90}}]},
            'Tags': [{'Key': 'CostCenter', 'Value': 'orders'}, {'Key': 'Environment', 'Value': {'Ref': 'Env'}}],
        }},
        'JobsDlq': {'Type': 'AWS::SQS::Queue', 'Properties': {
            'QueueName': _sub('-jobs-dlq'), 'MessageRetentionPeriod': 1209600, 'SqsManagedSseEnabled': True}},
        'Jobs': {'Type': 'AWS::SQS::Queue', 'Properties': {
            'QueueName': _sub('-jobs'),
            'ReceiveMessageWaitTimeSeconds': 20,
            'SqsManagedSseEnabled': True,
            'RedrivePolicy': {'deadLetterTargetArn': {'Fn::GetAtt': ['JobsDlq', 'Arn']}, 'maxReceiveCount': MAX_RECEIVES},
            'Tags': [{'Key': 'CostCenter', 'Value': 'orders'}, {'Key': 'Environment', 'Value': {'Ref': 'Env'}}],
        }},
        'Alerts': {'Type': 'AWS::SNS::Topic', 'Properties': {'TopicName': _sub('-alerts')}},
        'JobsAgeAlarm': {'Type': 'AWS::CloudWatch::Alarm', 'Properties': {
            'AlarmName': _sub('-jobs-age'),
            'AlarmDescription': 'Jobs are waiting more than 5 minutes',
            'Namespace': 'AWS/SQS', 'MetricName': 'ApproximateAgeOfOldestMessage',
            'Dimensions': [{'Name': 'QueueName', 'Value': {'Fn::GetAtt': ['Jobs', 'QueueName']}}],
            'Statistic': 'Maximum', 'Period': 60, 'EvaluationPeriods': 5, 'Threshold': 300,
            'ComparisonOperator': 'GreaterThanThreshold', 'AlarmActions': [{'Ref': 'Alerts'}],
        }},
    },
}

PILLARS = ['Operational excellence', 'Security', 'Reliability', 'Performance efficiency', 'Cost optimization',
           'Sustainability']

DESIGN_PRINCIPLES = [
    ('Perform operations as code', 'The whole workload is a CloudFormation template.'),
    ('Make frequent, small, reversible changes', 'The fixes went out as one reviewed change set with no replacements.'),
    ('Stop guessing your capacity needs', 'SQS and S3 scale with demand; nothing was sized up front.'),
    ('Test systems at production scale', 'A full test copy was created from the same template and deleted.'),
    ('Automatically recover from failure', 'A message that kept failing moved to the dead-letter queue by itself.'),
    ('Drive architectures using data', 'An alarm on the age of the oldest job notifies the team.'),
    ('Use managed services', 'Queues, storage, alerts and alarms are all managed services.'),
]
CLOUD_BENEFITS = [
    ('Speed and agility', 'A complete environment exists in seconds and is gone in seconds.'),
    ('Stop guessing capacity', 'Nothing was provisioned for peak load.'),
    ('Trade fixed expense for variable expense', 'The test copy costs only for the seconds it existed.'),
    ('Go global in minutes', 'The same template deploys to any Region.'),
    ('Stop spending money running data centers', 'There is no hardware to rack, power or cool.'),
    ('Benefit from massive economies of scale', 'Pay-as-you-go prices reflect AWS buying at scale.'),
]

BASELINE_COMMAND = (
    f'aws cloudformation create-stack --stack-name {STACK} --template-body file://orders-v1.json '
    '--parameters ParameterKey=Env,ParameterValue=prod\n'
    f'aws cloudformation wait stack-create-complete --stack-name {STACK}\n'
    f'aws cloudformation describe-stack-resources --stack-name {STACK}'
)
REVIEW_COMMAND = (
    f'aws s3api get-bucket-encryption --bucket {PROD["bucket"]}\n'
    f'aws s3api get-public-access-block --bucket {PROD["bucket"]}\n'
    f'aws s3api get-bucket-versioning --bucket {PROD["bucket"]}\n'
    f'aws s3api get-bucket-lifecycle-configuration --bucket {PROD["bucket"]}\n'
    f'aws sqs get-queue-attributes --queue-url <{PROD["queue"]}-url> --attribute-names All\n'
    f'aws cloudwatch describe-alarms --alarm-names {PROD["alarm"]}\n'
    '# Score the workload against the six Well-Architected pillars'
)
CHANGE_SET_COMMAND = (
    f'aws cloudformation create-change-set --stack-name {STACK} --change-set-name {CHANGE_SET} '
    '--template-body file://orders-v2.json --parameters ParameterKey=Env,UsePreviousValue=true\n'
    f'aws cloudformation describe-change-set --stack-name {STACK} --change-set-name {CHANGE_SET}'
)
EXECUTE_COMMAND = (
    f'aws cloudformation execute-change-set --stack-name {STACK} --change-set-name {CHANGE_SET}\n'
    f'aws cloudformation wait stack-update-complete --stack-name {STACK}'
)
DRILL_COMMAND = (
    f'aws sqs send-message --queue-url <{PROD["queue"]}-url> --message-body \'{{"order": "poison"}}\'\n'
    f'aws sqs receive-message --queue-url <{PROD["queue"]}-url> --visibility-timeout 0   # fail it {MAX_RECEIVES + 1} times\n'
    f'aws sqs receive-message --queue-url <{PROD["dlq"]}-url>'
)
REREVIEW_COMMAND = '# Run the same six-pillar review against the updated stack'
COPY_COMMAND = (
    f'aws cloudformation create-stack --stack-name {TEST_STACK} --template-body file://orders-v2.json '
    '--parameters ParameterKey=Env,ParameterValue=test\n'
    f'aws cloudformation wait stack-create-complete --stack-name {TEST_STACK}\n'
    f'aws cloudformation delete-stack --stack-name {TEST_STACK}\n'
    f'aws cloudformation wait stack-delete-complete --stack-name {TEST_STACK}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Review a workload against the Well-Architected pillars and fix it with a change set',
    'description': (
        'Deploy a first draft of an orders workload with CloudFormation, score it against the six pillars of the '
        'AWS Well-Architected Framework, fix the findings with one change set, prove the reliability fix with a '
        'failure drill, review it again, then create a full test copy and throw it away to see the design '
        'principles and the benefits of the cloud at work.'
    ),
    'steps': [
        {
            'key': 'deploy-baseline',
            'title': 'Deploy the first draft of the workload',
            'command': BASELINE_COMMAND,
            'explanation': (
                'The first draft works: an S3 bucket for uploads and an SQS queue for jobs, both from a template. '
                'Nothing about it is reviewed yet. How Floci emulates this: the stack and its resources are '
                'created for real.'
            ),
        },
        {
            'key': 'review-baseline',
            'title': 'Review it against the six pillars',
            'command': REVIEW_COMMAND,
            'explanation': (
                'The Well-Architected Framework has six pillars: operational excellence, security, reliability, '
                'performance efficiency, cost optimization and sustainability. A review asks questions per pillar '
                'and records what is missing. The AWS Well-Architected Tool runs reviews in the console. How Floci '
                'emulates this: the Well-Architected Tool is not emulated, so the step reads the deployed settings '
                'and scores each pillar itself.'
            ),
        },
        {
            'key': 'plan-improvements',
            'title': 'Plan the fixes as one change set',
            'command': CHANGE_SET_COMMAND,
            'explanation': (
                'The fixes: versioning on the bucket (reliability), encryption and Block Public Access written into '
                'the template (new buckets and queues already get both by default, so the review passed security, '
                'but code makes it explicit), '
                'a lifecycle rule that tiers to Standard-IA and trims old versions (cost, sustainability), a '
                'dead-letter queue and long polling on the queue (reliability, performance), cost allocation tags, '
                'and an alarm that notifies SNS when jobs wait too long (operational excellence). A change set '
                'shows what will change before anything does. How Floci emulates this: change sets are planned and '
                'described as on AWS.'
            ),
        },
        {
            'key': 'apply-improvements',
            'title': 'Execute the change set',
            'command': EXECUTE_COMMAND,
            'explanation': (
                'Executing applies the reviewed plan in place: no resource is replaced, so the bucket keeps its '
                'data and the queue keeps its URL. How Floci emulates this: the update runs and every new setting is '
                'applied to the real resources.'
            ),
        },
        {
            'key': 'failure-drill',
            'title': 'Prove the reliability fix with a failure drill',
            'command': DRILL_COMMAND,
            'explanation': (
                f'Design for failure: a job that fails every time should not block the queue forever. After '
                f'{MAX_RECEIVES} failed receives SQS moves it to the dead-letter queue, where the team can inspect '
                'it. Regular drills (game days) prove that recovery works. How Floci emulates this: the redrive '
                'to the dead-letter queue is real.'
            ),
        },
        {
            'key': 'review-again',
            'title': 'Review the workload again',
            'command': REREVIEW_COMMAND,
            'explanation': (
                'Reviews are not one-off: run them again after changes and on a regular schedule. How Floci '
                'emulates this: the step scores the updated settings the same way as the first review.'
            ),
        },
        {
            'key': 'disposable-copy',
            'title': 'Create a full test copy and throw it away',
            'command': COPY_COMMAND,
            'explanation': (
                'The same template builds an identical test environment in seconds, and deleting the stack '
                'removes all of it, so you pay only while it exists. That is the cloud\'s speed and agility and '
                'the "test at production scale" principle. How Floci emulates this: the copy is created and '
                'deleted for real; the step times both and matches the design principles and the cloud\'s '
                'benefits to what the lab did.'
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


# ── Pure logic ───────────────────────────────────────────────────────────────

def review(observed: dict[str, Any]) -> list[dict[str, Any]]:
    """Score each pillar from the observed settings. Each check is (question, passed)."""
    bucket = observed.get('bucket') or {}
    queue = observed.get('queue') or {}
    alarm = observed.get('alarm') or {}
    checks = {
        'Operational excellence': [
            ('Is the workload defined as code?', bool(observed.get('managed_by_stack'))),
            ('Does an alarm notify someone when jobs back up?', bool(alarm.get('actions'))),
        ],
        'Security': [
            ('Is the bucket encrypted by default?', bool(bucket.get('encrypted'))),
            ('Does the bucket block public access?', bool(bucket.get('public_access_blocked'))),
            ('Is the queue encrypted?', bool(queue.get('encrypted'))),
        ],
        'Reliability': [
            ('Can an overwritten or deleted upload be recovered (versioning)?', bucket.get('versioning') == 'Enabled'),
            ('Do failing jobs go to a dead-letter queue instead of blocking?', bool(queue.get('dead_letter_queue'))),
        ],
        'Performance efficiency': [
            ('Are managed, elastic services used instead of fixed servers?', bool(observed.get('managed_by_stack'))),
            ('Does the consumer use long polling?', int(queue.get('wait_seconds') or 0) >= 1),
        ],
        'Cost optimization': [
            ('Do resources carry a CostCenter tag?', bool(bucket.get('cost_tag')) and bool(queue.get('cost_tag'))),
            ('Do infrequently read uploads move to a cheaper storage class?', bool(bucket.get('tiering'))),
        ],
        'Sustainability': [
            ('Is data that is no longer needed deleted (old versions expire)?', bool(bucket.get('trims_old_versions'))),
            ('Are idle resources avoided (no always-on servers)?', bool(observed.get('managed_by_stack'))),
        ],
    }
    rows = []
    for pillar in PILLARS:
        items = checks[pillar]
        missing = [question for question, passed in items if not passed]
        rows.append({'Pillar': pillar, 'Passed': len(items) - len(missing), 'Questions': len(items),
                     'Status': 'pass' if not missing else 'needs work', 'Findings': missing})
    return rows


def _seconds(value: float) -> str:
    return 'under a second' if value < 1 else f'{value:g} s'


def summarize(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Return (pillars that pass, total findings)."""
    return sum(1 for row in rows if row['Status'] == 'pass'), sum(len(row['Findings']) for row in rows)


# ── Lookups ──────────────────────────────────────────────────────────────────

def _stack(name: str = STACK) -> dict[str, Any] | None:
    try:
        stacks = _client('cloudformation').describe_stacks(StackName=name).get('Stacks', [])
    except ClientError as exc:
        if _code(exc) == 'ValidationError':
            return None
        raise
    live = [item for item in stacks if item.get('StackStatus') != 'DELETE_COMPLETE']
    return live[0] if live else None


def _wait_stack(name: str) -> dict[str, Any] | None:
    _wait(lambda: not (( _stack(name) or {}).get('StackStatus') or '').endswith('IN_PROGRESS'), timeout=120)
    return _stack(name)


def _resources(name: str = STACK) -> dict[str, dict[str, Any]]:
    try:
        items = _client('cloudformation').describe_stack_resources(StackName=name).get('StackResources', [])
    except ClientError as exc:
        if _code(exc) == 'ValidationError':
            return {}
        raise
    return {item['LogicalResourceId']: item for item in items}


def _change_set() -> dict[str, Any] | None:
    try:
        return _client('cloudformation').describe_change_set(StackName=STACK, ChangeSetName=CHANGE_SET)
    except ClientError as exc:
        if _code(exc) in ('ChangeSetNotFound', 'ChangeSetNotFoundException', 'ValidationError'):
            return None
        raise


def _queue_url(name: str) -> str | None:
    try:
        return _client('sqs').get_queue_url(QueueName=name)['QueueUrl']
    except ClientError as exc:
        if 'NonExistentQueue' in _code(exc) or _code(exc) == 'QueueDoesNotExist':
            return None
        raise


def _observe(names: dict[str, str] = PROD, stack: str = STACK) -> dict[str, Any]:
    s3, sqs, cw = _client('s3'), _client('sqs'), _client('cloudwatch')
    bucket_name, observed = names['bucket'], {'managed_by_stack': bool(_stack(stack))}

    def safe(call, default=None):
        try:
            return call()
        except ClientError:
            return default

    rules = safe(lambda: s3.get_bucket_lifecycle_configuration(Bucket=bucket_name)['Rules'], []) or []
    public = safe(lambda: s3.get_public_access_block(Bucket=bucket_name)['PublicAccessBlockConfiguration'], {}) or {}
    tags = safe(lambda: s3.get_bucket_tagging(Bucket=bucket_name)['TagSet'], []) or []
    observed['bucket'] = {
        'encrypted': bool(safe(lambda: s3.get_bucket_encryption(Bucket=bucket_name)['ServerSideEncryptionConfiguration']['Rules'])),
        'public_access_blocked': bool(public) and all(public.get(key) for key in (
            'BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')),
        'versioning': safe(lambda: s3.get_bucket_versioning(Bucket=bucket_name).get('Status')),
        'tiering': any(rule.get('Status') == 'Enabled' and rule.get('Transitions') for rule in rules),
        'trims_old_versions': any(rule.get('Status') == 'Enabled' and rule.get('NoncurrentVersionExpiration') for rule in rules),
        'cost_tag': any(tag.get('Key') == 'CostCenter' for tag in tags),
    }
    url = _queue_url(names['queue'])
    attributes = safe(lambda: sqs.get_queue_attributes(QueueUrl=url, AttributeNames=['All'])['Attributes'], {}) if url else {}
    queue_tags = safe(lambda: sqs.list_queue_tags(QueueUrl=url).get('Tags', {}), {}) if url else {}
    observed['queue'] = {
        'exists': bool(url),
        'encrypted': str(attributes.get('SqsManagedSseEnabled', '')).lower() == 'true' or bool(attributes.get('KmsMasterKeyId')),
        'dead_letter_queue': bool(attributes.get('RedrivePolicy')),
        'wait_seconds': attributes.get('ReceiveMessageWaitTimeSeconds', '0'),
        'cost_tag': 'CostCenter' in (queue_tags or {}),
    }
    alarms = safe(lambda: cw.describe_alarms(AlarmNames=[names['alarm']]).get('MetricAlarms', []), []) or []
    observed['alarm'] = {'exists': bool(alarms), 'actions': alarms[0].get('AlarmActions', []) if alarms else []}
    return observed


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_baseline() -> tuple[bool, str]:
    stack = _stack()
    if not stack or stack.get('StackStatus') not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
        return False, f'Create the {STACK} stack.'
    resources = _resources()
    if not {'Uploads', 'Jobs'} <= set(resources):
        return False, f'{STACK} should contain the Uploads bucket and the Jobs queue.'
    return True, f'{STACK} is {stack["StackStatus"]} with the {PROD["bucket"]} bucket and the {PROD["queue"]} queue.'


def _check_marker(step: str, prerequisite=None) -> tuple[bool, str]:
    if prerequisite:
        ok, message = prerequisite()
        if not ok:
            return False, message
    marker = _marked(step)
    if not marker:
        return False, 'Run this step.'
    return True, marker['message']


def _check_review() -> tuple[bool, str]:
    return _check_marker('review-baseline', _check_baseline)


def _check_plan() -> tuple[bool, str]:
    if not _check_baseline()[0]:
        return False, f'Create the {STACK} stack first.'
    if _improved():
        return True, f'{STACK} already runs the improved template (the change set was executed).'
    change_set = _change_set()
    if not change_set:
        return False, f'Create the {CHANGE_SET} change set.'
    changes = {(item['ResourceChange']['Action'], item['ResourceChange']['LogicalResourceId'])
               for item in change_set.get('Changes', [])}
    expected = {('Modify', 'Uploads'), ('Modify', 'Jobs'), ('Add', 'JobsDlq'), ('Add', 'Alerts'), ('Add', 'JobsAgeAlarm')}
    if not expected <= changes:
        return False, f'{CHANGE_SET} should modify Uploads and Jobs and add JobsDlq, Alerts and JobsAgeAlarm.'
    replaced = [item['ResourceChange']['LogicalResourceId'] for item in change_set.get('Changes', [])
                if str(item['ResourceChange'].get('Replacement')) == 'True']
    if replaced:
        return False, f'{CHANGE_SET} would replace {", ".join(replaced)}; fix the template so nothing is replaced.'
    return True, f'{CHANGE_SET} modifies 2 resources in place and adds 3; nothing is replaced.'


def _improved() -> bool:
    stack = _stack()
    return bool(stack) and stack.get('StackStatus') == 'UPDATE_COMPLETE' and \
        {'JobsDlq', 'Alerts', 'JobsAgeAlarm'} <= set(_resources())


def _check_apply() -> tuple[bool, str]:
    if not _improved():
        return False, f'Execute {CHANGE_SET} and wait for UPDATE_COMPLETE.'
    return True, f'{STACK} is UPDATE_COMPLETE with the dead-letter queue, alert topic and alarm.'


def _check_drill() -> tuple[bool, str]:
    if not _improved():
        return False, f'Execute {CHANGE_SET} first.'
    return _check_marker('failure-drill')


def _check_rereview() -> tuple[bool, str]:
    if not _improved():
        return False, f'Execute {CHANGE_SET} first.'
    passed, findings = summarize(review(_observe()))
    if findings:
        return False, f'{passed} of 6 pillars pass; {findings} findings remain.'
    marker = _marked('review-again')
    if not marker:
        return False, 'Run the second review.'
    return True, marker['message']


def _check_copy() -> tuple[bool, str]:
    if _stack(TEST_STACK):
        return False, f'Delete the {TEST_STACK} stack when you are done with it.'
    return _check_marker('disposable-copy')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_deploy_baseline() -> dict[str, Any]:
    started = time.perf_counter()
    cf = _client('cloudformation')
    stack = _stack()
    if stack and stack.get('StackStatus') in ('ROLLBACK_COMPLETE', 'CREATE_FAILED'):
        cf.delete_stack(StackName=STACK)
        _wait(lambda: not _stack(), timeout=60)
        stack = None
    if not stack:
        cf.create_stack(StackName=STACK, TemplateBody=json.dumps(BASELINE_TEMPLATE),
                        Parameters=[{'ParameterKey': 'Env', 'ParameterValue': 'prod'}],
                        Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    stack = _wait_stack(STACK) or {}
    response = {'Stack': {key: stack.get(key) for key in ('StackName', 'StackStatus', 'Description')},
                'Resources': {key: {'Type': item['ResourceType'], 'PhysicalId': item.get('PhysicalResourceId')}
                              for key, item in _resources().items()},
                'Template': BASELINE_TEMPLATE if not _improved() else 'already improved'}
    verified, message = _check_baseline()
    return _result('deploy-baseline', BASELINE_COMMAND, response, verified, message, started)


def step_review_baseline() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_baseline()[0]:
        return _result('review-baseline', REVIEW_COMMAND, {}, False, f'Create the {STACK} stack first.', started)
    observed = _observe()
    rows = review(observed)
    passed, findings = summarize(rows)
    message = (f'{passed} of 6 pillars pass and the review found {findings} things to fix'
               + (' (the stack is already improved; reset the lab to see the first draft).' if not findings else '.'))
    _marker('review-baseline', {'message': message, 'findings': findings})
    response = {'Observed': observed, 'Review': rows}
    verified, check = _check_review()
    return _result('review-baseline', REVIEW_COMMAND, response, verified, check, started)


def step_plan_improvements() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_baseline()[0]:
        return _result('plan-improvements', CHANGE_SET_COMMAND, {}, False, f'Create the {STACK} stack first.', started)
    cf = _client('cloudformation')
    response: dict[str, Any] = {}
    if _improved():
        response['Note'] = f'{STACK} already runs the improved template; there is nothing left to plan.'
    else:
        if _change_set():
            cf.delete_change_set(StackName=STACK, ChangeSetName=CHANGE_SET)
        cf.create_change_set(StackName=STACK, ChangeSetName=CHANGE_SET, TemplateBody=json.dumps(IMPROVED_TEMPLATE),
                             Parameters=[{'ParameterKey': 'Env', 'UsePreviousValue': True}],
                             Description='Fixes from the Well-Architected review')
        _wait(lambda: (_change_set() or {}).get('Status') in ('CREATE_COMPLETE', 'FAILED'), timeout=60)
        change_set = _change_set() or {}
        response = {'Status': change_set.get('Status'), 'Changes': [
            {'Action': item['ResourceChange']['Action'], 'LogicalId': item['ResourceChange']['LogicalResourceId'],
             'Type': item['ResourceChange']['ResourceType'], 'Replacement': item['ResourceChange'].get('Replacement')}
            for item in change_set.get('Changes', [])]}
    response['FixByPillar'] = {
        'Security': 'Encryption and Block Public Access stated in the template (already the default for new buckets and queues)',
        'Reliability': 'Bucket versioning; a dead-letter queue after 3 failed receives',
        'Operational excellence': 'An alarm on the age of the oldest job that notifies SNS',
        'Performance efficiency': '20-second long polling',
        'Cost optimization': 'CostCenter tags; uploads move to Standard-IA after 30 days',
        'Sustainability': 'Old versions expire after 90 days',
    }
    verified, message = _check_plan()
    return _result('plan-improvements', CHANGE_SET_COMMAND, response, verified, message, started)


def step_apply_improvements() -> dict[str, Any]:
    started = time.perf_counter()
    cf = _client('cloudformation')
    if not _improved():
        if not _check_plan()[0] or not _change_set():
            return _result('apply-improvements', EXECUTE_COMMAND, {}, False, f'Create the {CHANGE_SET} change set first.',
                           started)
        cf.execute_change_set(StackName=STACK, ChangeSetName=CHANGE_SET)
        _wait_stack(STACK)
    stack = _stack() or {}
    response = {'Stack': {key: stack.get(key) for key in ('StackName', 'StackStatus')},
                'Resources': {key: item['ResourceType'] for key, item in _resources().items()}}
    verified, message = _check_apply()
    return _result('apply-improvements', EXECUTE_COMMAND, response, verified, message, started)


def step_failure_drill() -> dict[str, Any]:
    started = time.perf_counter()
    if not _improved():
        return _result('failure-drill', DRILL_COMMAND, {}, False, f'Execute {CHANGE_SET} first.', started)
    sqs = _client('sqs')
    queue_url, dlq_url = _queue_url(PROD['queue']), _queue_url(PROD['dlq'])
    if not queue_url or not dlq_url:
        return _result('failure-drill', DRILL_COMMAND, {}, False, 'The jobs queue or its dead-letter queue is missing.',
                       started)
    sqs.purge_queue(QueueUrl=dlq_url)
    drill_id = f'poison-{int(time.time())}'
    sqs.send_message(QueueUrl=queue_url, MessageBody=json.dumps({'order': 'poison', 'drill': drill_id}))
    attempts = []
    for attempt in range(1, MAX_RECEIVES + 3):
        messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1, VisibilityTimeout=0,
                                       WaitTimeSeconds=1, AttributeNames=['ApproximateReceiveCount']).get('Messages', [])
        attempts.append({'Attempt': attempt, 'Received': bool(messages),
                         'ReceiveCount': messages[0]['Attributes'].get('ApproximateReceiveCount') if messages else None})
        if not messages and attempt > MAX_RECEIVES:
            break
    moved: list[dict[str, Any]] = []

    def in_dlq() -> bool:
        found = sqs.receive_message(QueueUrl=dlq_url, MaxNumberOfMessages=10, WaitTimeSeconds=1,
                                    VisibilityTimeout=0).get('Messages', [])
        moved[:] = [item for item in found if drill_id in item.get('Body', '')]
        return bool(moved)
    _wait(in_dlq, timeout=15)
    response = {'Attempts': attempts, 'DeadLetterQueue': [item.get('Body') for item in moved]}
    if moved:
        _marker('failure-drill', {'message': (
            f'The poison job failed {MAX_RECEIVES} times and SQS moved it to {PROD["dlq"]} on its own; the jobs queue '
            'keeps flowing.')})
    verified, message = _check_drill()
    if not moved:
        verified, message = False, f'The poison job did not reach {PROD["dlq"]}.'
    return _result('failure-drill', DRILL_COMMAND, response, verified, message, started)


def step_review_again() -> dict[str, Any]:
    started = time.perf_counter()
    if not _improved():
        return _result('review-again', REREVIEW_COMMAND, {}, False, f'Execute {CHANGE_SET} first.', started)
    rows = review(_observe())
    passed, findings = summarize(rows)
    before = (_marked('review-baseline') or {}).get('findings')
    if not findings:
        _marker('review-again', {'message': (
            'All 6 pillars pass now' + (f'; the first review found {before} things to fix.' if before else '.'))})
    response = {'Review': rows}
    verified, message = _check_rereview()
    return _result('review-again', REREVIEW_COMMAND, response, verified, message, started)


def step_disposable_copy() -> dict[str, Any]:
    started = time.perf_counter()
    cf = _client('cloudformation')
    if _stack(TEST_STACK):
        cf.delete_stack(StackName=TEST_STACK)
        _wait(lambda: not _stack(TEST_STACK), timeout=60)
    create_started = time.perf_counter()
    cf.create_stack(StackName=TEST_STACK, TemplateBody=json.dumps(IMPROVED_TEMPLATE),
                    Parameters=[{'ParameterKey': 'Env', 'ParameterValue': 'test'}],
                    Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    stack = _wait_stack(TEST_STACK) or {}
    created_seconds = round(time.perf_counter() - create_started, 1)
    copy_review = review(_observe(TEST, TEST_STACK))
    copy_passed, _ = summarize(copy_review)
    resources = sorted(_resources(TEST_STACK))
    delete_started = time.perf_counter()
    cf.delete_stack(StackName=TEST_STACK)
    gone = _wait(lambda: not _stack(TEST_STACK), timeout=120)
    deleted_seconds = round(time.perf_counter() - delete_started, 1)
    response = {'TestStack': {'Status': stack.get('StackStatus'), 'Resources': resources,
                              'CreatedSeconds': created_seconds, 'DeletedSeconds': deleted_seconds,
                              'PillarsPassing': copy_passed, 'Gone': gone},
                'DesignPrinciples': [{'Principle': name, 'Evidence': evidence} for name, evidence in DESIGN_PRINCIPLES],
                'CloudBenefits': [{'Benefit': name, 'Evidence': evidence} for name, evidence in CLOUD_BENEFITS]}
    if stack.get('StackStatus') == 'CREATE_COMPLETE' and gone:
        _marker('disposable-copy', {'message': (
            f'A full copy ({len(resources)} resources, {copy_passed} of 6 pillars passing) took {_seconds(created_seconds)} '
            f'to create and {_seconds(deleted_seconds)} to delete: test at production scale, then stop paying for it.')})
    verified, message = _check_copy()
    return _result('disposable-copy', COPY_COMMAND, response, verified, message, started)


RUNNERS = {
    'deploy-baseline': step_deploy_baseline,
    'review-baseline': step_review_baseline,
    'plan-improvements': step_plan_improvements,
    'apply-improvements': step_apply_improvements,
    'failure-drill': step_failure_drill,
    'review-again': step_review_again,
    'disposable-copy': step_disposable_copy,
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
    'deploy-baseline': _check_baseline,
    'review-baseline': _check_review,
    'plan-improvements': _check_plan,
    'apply-improvements': _check_apply,
    'failure-drill': _check_drill,
    'review-again': _check_rereview,
    'disposable-copy': _check_copy,
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
    """Empty the buckets, then delete both stacks (which removes their buckets, queues, topic and alarm)."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []
    cf, s3 = _client('cloudformation'), _client('s3')

    for stack_name, names in ((TEST_STACK, TEST), (STACK, PROD)):
        try:
            if not _stack(stack_name):
                continue
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'stack lookup {stack_name}: {exc}')
            continue
        try:
            versions = s3.list_object_versions(Bucket=names['bucket'])
            for item in versions.get('Versions', []) + versions.get('DeleteMarkers', []):
                s3.delete_object(Bucket=names['bucket'], Key=item['Key'], VersionId=item['VersionId'])
        except ClientError as exc:
            if _code(exc) not in ('NoSuchBucket', '404'):
                errors.append(f'objects:{names["bucket"]}: {exc}')
        try:
            cf.delete_stack(StackName=stack_name)
            if _wait(lambda stack_name=stack_name: not _stack(stack_name), timeout=120):
                deleted.append(f'stack:{stack_name}')
            else:
                errors.append(f'stack:{stack_name}: still present after delete-stack')
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'stack:{stack_name}: {exc}')

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws s3api delete-objects --bucket {PROD["bucket"]} ...   # every version\n'
            f'aws cloudformation delete-stack --stack-name {TEST_STACK}\n'
            f'aws cloudformation delete-stack --stack-name {STACK}'
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
            else 'Both stacks and everything they created are gone.',
        },
    }
