"""Curated local AWS workflow labs."""

from __future__ import annotations

import json
import zipfile
import base64
from io import BytesIO
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError
from django.core.cache import cache

from ..aws import FlociClientFactory, _clean_response

AWS_ACCOUNT_ID = '000000000000'
AWS_REGION = 'us-east-1'
CLOUDFORMATION_STACK_NAME = 'lab-storage-messaging'
CLOUDFORMATION_BUCKET_NAME = 'lab-cfn-storage'
CLOUDFORMATION_QUEUE_NAME = 'lab-cfn-jobs'
CLOUDFORMATION_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{CLOUDFORMATION_QUEUE_NAME}'
)
CLOUDFORMATION_TEMPLATE = {
    'AWSTemplateFormatVersion': '2010-09-09',
    'Description': 'Provision local S3 storage and an SQS work queue.',
    'Resources': {
        'StorageBucket': {
            'Type': 'AWS::S3::Bucket',
            'Properties': {
                'BucketName': CLOUDFORMATION_BUCKET_NAME,
            },
        },
        'JobsQueue': {
            'Type': 'AWS::SQS::Queue',
            'Properties': {
                'QueueName': CLOUDFORMATION_QUEUE_NAME,
                'VisibilityTimeout': 30,
            },
        },
    },
    'Outputs': {
        'BucketName': {
            'Description': 'Name of the provisioned storage bucket.',
            'Value': {'Ref': 'StorageBucket'},
        },
        'QueueUrl': {
            'Description': 'URL of the provisioned work queue.',
            'Value': {'Ref': 'JobsQueue'},
        },
        'QueueArn': {
            'Description': 'ARN of the provisioned work queue.',
            'Value': {'Fn::GetAtt': ['JobsQueue', 'Arn']},
        },
    },
}
CLOUDFORMATION_TEMPLATE_TEXT = json.dumps(
    CLOUDFORMATION_TEMPLATE,
    indent=2,
)
CLOUDFORMATION_CREATED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:created'
CLOUDFORMATION_INSPECTED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:inspected'
CLOUDFORMATION_DELETED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:deleted'
CLOUDFORMATION_S3_SQS_LAB = {
    'service': 'cloudformation',
    'key': 's3-sqs-stack',
    'title': 'Provision S3 and SQS resources with CloudFormation',
    'description': 'Validate one infrastructure template, create a stack, inspect its outputs, resources, and events, then delete the stack and verify cleanup.',
    'steps': [
        {
            'key': 'validate-template',
            'title': 'Validate the infrastructure template',
            'command': 'aws cloudformation validate-template --template-body file://storage-messaging-stack.json',
            'explanation': 'Checks the template structure before creating any resources.',
            'artifact_label': 'storage-messaging-stack.json',
            'artifact': CLOUDFORMATION_TEMPLATE_TEXT,
        },
        {
            'key': 'create-stack',
            'title': 'Create the stack',
            'command': f'aws cloudformation create-stack --stack-name {CLOUDFORMATION_STACK_NAME} --template-body file://storage-messaging-stack.json',
            'explanation': 'Asks CloudFormation to provision the named S3 bucket and SQS queue as one managed unit.',
        },
        {
            'key': 'describe-stack',
            'title': 'Inspect stack status and outputs',
            'command': f'aws cloudformation describe-stacks --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Requires CREATE_COMPLETE and verifies the bucket name, queue URL, and queue ARN outputs.',
        },
        {
            'key': 'describe-stack-resources',
            'title': 'Map logical resources to physical resources',
            'command': f'aws cloudformation describe-stack-resources --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Shows how StorageBucket and JobsQueue map to their real AWS-shaped identifiers.',
        },
        {
            'key': 'describe-stack-events',
            'title': 'Inspect the provisioning timeline',
            'command': f'aws cloudformation describe-stack-events --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Verifies CREATE_IN_PROGRESS and CREATE_COMPLETE events for the stack and both resources.',
        },
        {
            'key': 'inspect-provisioned-resources',
            'title': 'Verify the provisioned services',
            'command': f'aws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\naws sqs get-queue-attributes --queue-url <queue-url> --attribute-names QueueArn VisibilityTimeout',
            'explanation': 'Confirms that S3 and SQS expose the resources with the expected queue ARN and visibility timeout.',
        },
        {
            'key': 'delete-stack',
            'title': 'Delete the stack',
            'command': f'aws cloudformation delete-stack --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Deletes the CloudFormation ownership unit instead of deleting the bucket and queue manually.',
        },
        {
            'key': 'confirm-resources-deleted',
            'title': 'Confirm stack-owned cleanup',
            'command': f'aws cloudformation describe-stacks --stack-name {CLOUDFORMATION_STACK_NAME}\naws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\naws sqs get-queue-url --queue-name {CLOUDFORMATION_QUEUE_NAME}',
            'explanation': 'Requires the stack, bucket, and queue to be absent after deletion.',
        },
    ],
}

def labs_for_service(service_key: str) -> list[dict[str, Any]]:
    if service_key == 'iam':
        from .iam_labs import LABS
        return LABS
    if service_key == 's3':
        from .s3_labs import LABS
        return LABS
    if service_key == 'sqs':
        from .sqs_labs import LABS
        return LABS
    if service_key == 'sns':
        from .sns_labs import LABS
        return LABS
    if service_key == 'scheduler':
        from .scheduler_labs import LABS
        return LABS
    if service_key == 'cloudformation':
        return [CLOUDFORMATION_S3_SQS_LAB]
    if service_key == 'ec2':
        from .ec2_labs import LABS
        return LABS
    if service_key == 'lambda':
        from .lambda_labs import LABS
        return LABS
    if service_key == 'apigateway':
        from .apigateway_labs import LABS
        return LABS
    if service_key == 'eventbridge':
        from .eventbridge_application import LAB
        return [LAB]
    if service_key == 'dynamodb':
        from .dynamodb_labs import LABS
        return LABS
    if service_key == 'kms':
        from .kms_labs import LABS
        return LABS
    if service_key == 'ssm':
        from .ssm_labs import LABS
        return LABS
    if service_key == 'secretsmanager':
        from .secretsmanager_labs import LABS
        return LABS
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import LABS
        return LABS
    if service_key == 'cognito':
        from .cognito_labs import LABS
        return LABS
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import LABS
        return LABS
    if service_key == 'rds':
        from .rds_labs import LABS
        return LABS
    if service_key == 'ecs':
        from .ecs_labs import LABS
        return LABS
    if service_key == 'eks':
        from .eks_labs import LABS
        return LABS
    return []


LAB_BATCH_ORDER = [
    {
        'service': 'iam',
        'title': 'IAM labs',
        'summary': 'Continue into storage after the identity foundation: users, policies, groups, roles, STS, and instance profiles.',
    },
    {
        'service': 's3',
        'title': 'S3 labs',
        'summary': 'Continue into application encryption and keys after storage: buckets, objects, versioning, policies, encryption, lifecycle, notifications, and multipart uploads.',
    },
    {
        'service': 'kms',
        'title': 'KMS labs',
        'summary': 'Continue into application configuration after customer managed keys, aliases, and encrypt/decrypt round trips.',
    },
    {
        'service': 'ssm',
        'title': 'SSM Parameter Store labs',
        'summary': 'Continue into secrets management after hierarchical application configuration in Parameter Store.',
    },
    {
        'service': 'secretsmanager',
        'title': 'Secrets Manager labs',
        'summary': 'Continue into user identity directories after secret lifecycle creation, reads, and value updates.',
    },
    {
        'service': 'cognito',
        'title': 'Cognito authentication labs',
        'summary': 'Continue into asynchronous queueing after user pools, app clients, registration, and JWT token authentication.',
    },
    {
        'service': 'sqs',
        'title': 'SQS labs',
        'summary': 'Continue into pub/sub after queueing: message lifecycle, visibility, delays, batches, DLQs, FIFO, purge, and deletion.',
    },
    {
        'service': 'sns',
        'title': 'SNS labs',
        'summary': 'Continue into scheduled delivery after pub/sub fan-out and message filtering.',
    },
    {
        'service': 'scheduler',
        'title': 'EventBridge Scheduler lab',
        'summary': 'Continue into database persistence after scheduled SQS delivery through a scoped IAM role.',
    },
    {
        'service': 'dynamodb',
        'title': 'DynamoDB labs',
        'summary': 'Continue into serverless functions after table CRUD, primary/hash keys, query patterns, and Lambda write foundations.',
    },
    {
        'service': 'lambda',
        'title': 'Lambda labs',
        'summary': 'Continue into API Gateway after Lambda creation, runtime configuration reads, SQS event sources, invocation, and CloudWatch Logs inspection.',
    },
    {
        'service': 'apigateway',
        'title': 'API Gateway labs',
        'summary': 'Continue into event-driven architecture after exposing Lambda through a local HTTP API.',
    },
    {
        'service': 'eventbridge',
        'title': 'EventBridge application lab',
        'summary': 'Continue into state machine workflows after building a complete API-to-event fan-out workflow with transformed queues, Lambda notification logs, and failure experiments.',
    },
    {
        'service': 'stepfunctions',
        'title': 'Step Functions workflow labs',
        'summary': 'Continue into observability and incident response after ASL state machines, Choice branching, and Parallel task execution.',
    },
    {
        'service': 'cloudwatch',
        'title': 'CloudWatch observability labs',
        'summary': 'Continue into VPC networking after custom metric data, threshold alarms, Log Groups, and structured event streams.',
    },
    {
        'service': 'ec2',
        'title': 'EC2 networking labs',
        'summary': 'Continue into declarative infrastructure as code after VPC routing, security controls, gateway endpoints, and interface endpoints.',
    },
    {
        'service': 'rds',
        'title': 'RDS & Aurora labs',
        'summary': 'Master relational database provisioning, custom parameter groups, endpoint configuration, storage scaling, and reboot recovery.',
    },
    {
        'service': 'ecs',
        'title': 'ECS & Fargate container labs',
        'summary': 'Deploy containerized microservices: cluster creation, task definitions, standalone Fargate tasks, and autoscaling services.',
    },
    {
        'service': 'eks',
        'title': 'EKS Kubernetes labs',
        'summary': 'Deploy managed Kubernetes control planes, EC2 worker node groups, serverless Fargate pod profiles, and local kubeconfig setup.',
    },
    {
        'service': 'cloudformation',
        'title': 'CloudFormation lab',
        'summary': 'Complete the core AWS learning path with infrastructure as code: template validation, provisioning S3 + SQS, outputs, events, and dependency-aware teardown.',
    },
]


NEXT_BUILD_RECOMMENDATIONS = [
    'KMS key, alias, encrypt, and decrypt workflow',
    'SSM Parameter Store app configuration',
    'Secrets Manager secret lifecycle',
    'Cognito User Pool user registration and authentication',
    'SQS dead-letter queue redrive and FIFO deduplication',
    'DynamoDB table CRUD and query patterns',
    'Lambda create, invoke, and inspect CloudWatch logs',
    'Lambda reads configuration and secrets at runtime',
    'API Gateway to Lambda request workflow',
    'EventBridge application spine distributed fan-out',
    'Step Functions order workflow with Choice branching',
    'CloudWatch custom metrics and alarms',
]


def next_lab_batch(service_key: str, lab_key: str) -> dict[str, Any] | None:
    labs = labs_for_service(service_key)
    if not labs or labs[-1]['key'] != lab_key:
        return None

    batch_services = [batch['service'] for batch in LAB_BATCH_ORDER]
    if service_key not in batch_services:
        return None

    batch_index = batch_services.index(service_key)
    if batch_index + 1 >= len(LAB_BATCH_ORDER):
        return {
            'title': 'All learning batches complete',
            'summary': 'You have completed all 17 AWS service lab batches in Floci.',
            'service': None,
            'lab': None,
            'lab_title': None,
        }

    next_batch = LAB_BATCH_ORDER[batch_index + 1]
    next_labs = labs_for_service(next_batch['service'])
    if not next_labs:
        return None
    first_lab = next_labs[0]
    return {
        **next_batch,
        'lab': first_lab['key'],
        'lab_title': first_lab['title'],
    }


def get_lab(service_key: str, lab_key: str) -> dict[str, Any] | None:
    for lab in labs_for_service(service_key):
        if lab['key'] == lab_key:
            return lab
    return None


def _iam_client():
    return FlociClientFactory().client('iam')


def _sts_client():
    return FlociClientFactory().client('sts')


def _s3_client():
    return FlociClientFactory().client('s3')


def _sqs_client():
    return FlociClientFactory().client('sqs')


def _sns_client():
    return FlociClientFactory().client('sns')


def _scheduler_client():
    return FlociClientFactory().client('scheduler')


def _cloudformation_client():
    return FlociClientFactory().client('cloudformation')


def _ec2_client():
    return FlociClientFactory().client('ec2')


def _lambda_client():
    return FlociClientFactory().client('lambda')


def _logs_client():
    return FlociClientFactory().client('logs')


def _apigatewayv2_client():
    return FlociClientFactory().client('apigatewayv2')


def _dynamodb_client():
    return FlociClientFactory().client('dynamodb')


def _kms_client():
    return FlociClientFactory().client('kms')


def _ssm_client():
    return FlociClientFactory().client('ssm')


def _secretsmanager_client():
    return FlociClientFactory().client('secretsmanager')


def _floci_client_factory(**kwargs):
    return FlociClientFactory(**kwargs)


def _json_text(value: Any) -> str:
    return json.dumps(_clean_response(value), indent=2, default=str)


def _error_code(exc: ClientError) -> str | None:
    return exc.response.get('Error', {}).get('Code')


def _ignore_missing(action) -> bool:
    try:
        action()
        return True
    except ClientError as exc:
        if _error_code(exc) == 'NoSuchEntity':
            return False
        raise


def _verify_role(role_name: str, trusted_service: str) -> dict[str, Any]:
    try:
        role = _iam_client().get_role(RoleName=role_name).get('Role', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    trust_policy = role.get('AssumeRolePolicyDocument') or {}
    statements = trust_policy.get('Statement', [])
    trusted = any(
        statement.get('Effect') == 'Allow'
        and statement.get('Action') == 'sts:AssumeRole'
        and (
            statement.get('Principal', {}).get('Service') == trusted_service
            or statement.get('Principal', {}).get('AWS') == trusted_service
        )
        for statement in statements
    )
    if role.get('RoleName') == role_name and trusted:
        return {
            'status': 'passed',
            'message': f'Role {role_name} exists and trusts {trusted_service}.',
            'resource': _clean_response(role),
        }

    return {
        'status': 'failed',
        'message': f'Role {role_name} does not have the expected {trusted_service} trust policy.',
    }


LAMBDA_LOG_WAIT_SECONDS = 20


def _wait_for_verification(verify, timeout_seconds: float = LAMBDA_LOG_WAIT_SECONDS) -> dict[str, Any]:
    """Re-run a verification until it passes or time runs out.

    Asynchronous Lambda invocations write logs a moment later (longer on a
    cold start), so log checks that run right after the trigger need to wait.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        verification = verify()
        if verification.get('status') == 'passed' or time.monotonic() >= deadline:
            return verification
        time.sleep(0.5)


def _describe_log_streams_if_present(log_group_name: str) -> dict[str, Any]:
    try:
        return _logs_client().describe_log_streams(
            logGroupName=log_group_name,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        )
    except ClientError as exc:
        if _error_code(exc) == 'ResourceNotFoundException':
            return {'logStreams': []}
        raise


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _secret_string_json(response: dict[str, Any]) -> Any:
    return _parse_json_text(response.get('SecretString') or '')


def _sqs_queue_url(queue_name: str) -> str:
    return _sqs_client().get_queue_url(QueueName=queue_name)['QueueUrl']


def _cloudformation_stack() -> dict[str, Any]:
    return _cloudformation_client().describe_stacks(
        StackName=CLOUDFORMATION_STACK_NAME,
    )['Stacks'][0]


def _wait_for_cloudformation_status(
    terminal_statuses: set[str],
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        stack = _cloudformation_stack()
        if stack.get('StackStatus') in terminal_statuses:
            return stack
        time.sleep(0.1)
    raise ValueError(
        f'Stack {CLOUDFORMATION_STACK_NAME} did not reach '
        f'{", ".join(sorted(terminal_statuses))}.'
    )


def _verify_cloudformation_stack() -> dict[str, Any]:
    try:
        stack = _cloudformation_stack()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    outputs = {
        item.get('OutputKey'): item.get('OutputValue')
        for item in stack.get('Outputs', [])
    }
    queue_url = outputs.get('QueueUrl', '')
    if (
        stack.get('StackStatus') == 'CREATE_COMPLETE'
        and outputs.get('BucketName') == CLOUDFORMATION_BUCKET_NAME
        and queue_url.rstrip('/').endswith(f'/{CLOUDFORMATION_QUEUE_NAME}')
        and outputs.get('QueueArn') == CLOUDFORMATION_QUEUE_ARN
    ):
        return {
            'status': 'passed',
            'message': 'The stack is CREATE_COMPLETE with all expected outputs.',
            'resource': {
                'stack': _clean_response(stack),
                'outputs': outputs,
            },
        }
    return {'status': 'failed', 'message': 'The stack outputs are incomplete.'}


def _verify_cloudformation_resources() -> dict[str, Any]:
    try:
        response = _cloudformation_client().describe_stack_resources(
            StackName=CLOUDFORMATION_STACK_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    resources = {
        item.get('LogicalResourceId'): item
        for item in response.get('StackResources', [])
    }
    bucket = resources.get('StorageBucket', {})
    queue = resources.get('JobsQueue', {})
    if (
        bucket.get('PhysicalResourceId') == CLOUDFORMATION_BUCKET_NAME
        and bucket.get('ResourceType') == 'AWS::S3::Bucket'
        and bucket.get('ResourceStatus') == 'CREATE_COMPLETE'
        and queue.get('PhysicalResourceId', '').rstrip('/').endswith(
            f'/{CLOUDFORMATION_QUEUE_NAME}'
        )
        and queue.get('ResourceType') == 'AWS::SQS::Queue'
        and queue.get('ResourceStatus') == 'CREATE_COMPLETE'
    ):
        return {
            'status': 'passed',
            'message': 'Both logical resources map to CREATE_COMPLETE physical resources.',
            'resource': {'resources': _clean_response(list(resources.values()))},
        }
    return {'status': 'failed', 'message': 'The stack resource mapping is incomplete.'}


def _verify_cloudformation_events() -> dict[str, Any]:
    try:
        response = _cloudformation_client().describe_stack_events(
            StackName=CLOUDFORMATION_STACK_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    events = response.get('StackEvents', [])
    completed = {
        (item.get('LogicalResourceId'), item.get('ResourceStatus'))
        for item in events
    }
    required = {
        (CLOUDFORMATION_STACK_NAME, 'CREATE_COMPLETE'),
        ('StorageBucket', 'CREATE_COMPLETE'),
        ('JobsQueue', 'CREATE_COMPLETE'),
    }
    if required.issubset(completed):
        return {
            'status': 'passed',
            'message': 'The event timeline records successful creation of the stack and both resources.',
            'resource': {'events': _clean_response(events)},
        }
    return {'status': 'failed', 'message': 'The expected creation events are missing.'}


def _verify_cloudformation_service_resources() -> dict[str, Any]:
    try:
        _s3_client().head_bucket(Bucket=CLOUDFORMATION_BUCKET_NAME)
        queue_url = _sqs_queue_url(CLOUDFORMATION_QUEUE_NAME)
        queue_attributes = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn', 'VisibilityTimeout'],
        ).get('Attributes', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        queue_attributes.get('QueueArn') == CLOUDFORMATION_QUEUE_ARN
        and queue_attributes.get('VisibilityTimeout') == '30'
    ):
        return {
            'status': 'passed',
            'message': 'S3 and SQS expose the stack-managed resources and expected configuration.',
            'resource': {
                'bucket': CLOUDFORMATION_BUCKET_NAME,
                'queue_url': queue_url,
                'queue_attributes': queue_attributes,
            },
        }
    return {'status': 'failed', 'message': 'The provisioned resource configuration is incorrect.'}


def _verify_cloudformation_deleted() -> dict[str, Any]:
    if not cache.get(CLOUDFORMATION_DELETED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'Stack deletion has not been verified by this lab run.',
        }
    stack_absent = False
    bucket_absent = False
    queue_absent = False
    try:
        _cloudformation_stack()
    except ClientError:
        stack_absent = True
    try:
        _s3_client().head_bucket(Bucket=CLOUDFORMATION_BUCKET_NAME)
    except ClientError:
        bucket_absent = True
    try:
        _sqs_queue_url(CLOUDFORMATION_QUEUE_NAME)
    except ClientError as exc:
        queue_absent = _error_code(exc) in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }
    if stack_absent and bucket_absent and queue_absent:
        return {
            'status': 'passed',
            'message': 'The stack, bucket, and queue are all absent.',
        }
    return {'status': 'failed', 'message': 'One or more stack-owned resources still exist.'}


def run_lab_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import run_step
        return run_step(step_key)
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'cognito':
        from .cognito_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'rds':
        from .rds_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'ecs':
        from .ecs_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'eks':
        from .eks_labs import run_step
        return run_step(service_key, lab_key, step_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2':
        from .ec2_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'iam':
        from .iam_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'lambda':
        from .lambda_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'apigateway':
        from .apigateway_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'kms':
        from .kms_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'ssm':
        from .ssm_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 's3':
        from .s3_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'sqs':
        from .sqs_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'sns':
        from .sns_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'scheduler':
        from .scheduler_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        runners = {
            'validate-template': _run_cloudformation_validate_template,
            'create-stack': _run_cloudformation_create_stack,
            'describe-stack': _run_cloudformation_describe_stack,
            'describe-stack-resources': _run_cloudformation_describe_resources,
            'describe-stack-events': _run_cloudformation_describe_events,
            'inspect-provisioned-resources': _run_cloudformation_inspect_services,
            'delete-stack': _run_cloudformation_delete_stack,
            'confirm-resources-deleted': _run_cloudformation_confirm_deleted,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def lab_status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import status
        return status()
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import status
        return status(service_key, lab_key)
    if service_key == 'cognito':
        from .cognito_labs import status
        return status(service_key, lab_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import status
        return status(service_key, lab_key)
    if service_key == 'rds':
        from .rds_labs import status
        return status(service_key, lab_key)
    if service_key == 'ecs':
        from .ecs_labs import status
        return status(service_key, lab_key)
    if service_key == 'eks':
        from .eks_labs import status
        return status(service_key, lab_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2':
        from .ec2_labs import status
        return status(service_key, lab_key)

    if service_key == 'iam':
        from .iam_labs import status
        return status(service_key, lab_key)

    if service_key == 'lambda':
        from .lambda_labs import status
        return status(service_key, lab_key)

    if service_key == 'apigateway':
        from .apigateway_labs import status
        return status(service_key, lab_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import status
        return status(service_key, lab_key)

    if service_key == 'kms':
        from .kms_labs import status
        return status(service_key, lab_key)

    if service_key == 'ssm':
        from .ssm_labs import status
        return status(service_key, lab_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import status
        return status(service_key, lab_key)

    if service_key == 's3':
        from .s3_labs import status
        return status(service_key, lab_key)

    if service_key == 'sqs':
        from .sqs_labs import status
        return status(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import status
        return status(service_key, lab_key)

    if service_key == 'scheduler':
        from .scheduler_labs import status
        return status(service_key, lab_key)

    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        deleted_verification = _verify_cloudformation_deleted()
        deleted_verified = deleted_verification.get('status') == 'passed'
        created_verified = bool(cache.get(CLOUDFORMATION_CREATED_CACHE_KEY))
        inspected_verified = bool(cache.get(CLOUDFORMATION_INSPECTED_CACHE_KEY))
        created_verification = {
            'status': 'passed',
            'message': 'The stack reached CREATE_COMPLETE with verified outputs.',
        }
        inspected_verification = {
            'status': 'passed',
            'message': 'Stack resources, events, and underlying services were inspected.',
        }
        if deleted_verified:
            stack_verified = created_verified
            resources_verified = inspected_verified
            events_verified = inspected_verified
            services_verified = inspected_verified
        else:
            stack_verified = _verify_cloudformation_stack().get('status') == 'passed'
            resources_verified = (
                _verify_cloudformation_resources().get('status') == 'passed'
            )
            events_verified = (
                _verify_cloudformation_events().get('status') == 'passed'
            )
            services_verified = (
                _verify_cloudformation_service_resources().get('status')
                == 'passed'
            )
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                created_verified
                and inspected_verified
                and deleted_verified
            ),
            'steps': {
                'validate-template': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'create-stack': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'describe-stack': {
                    'verified': stack_verified,
                    'verification': (
                        created_verification if stack_verified else None
                    ),
                },
                'describe-stack-resources': {
                    'verified': resources_verified,
                    'verification': (
                        inspected_verification if resources_verified else None
                    ),
                },
                'describe-stack-events': {
                    'verified': events_verified,
                    'verification': (
                        inspected_verification if events_verified else None
                    ),
                },
                'inspect-provisioned-resources': {
                    'verified': services_verified,
                    'verification': (
                        inspected_verification if services_verified else None
                    ),
                },
                'delete-stack': {
                    'verified': deleted_verified,
                    'verification': (
                        deleted_verification if deleted_verified else None
                    ),
                },
                'confirm-resources-deleted': {
                    'verified': deleted_verified,
                    'verification': (
                        deleted_verification if deleted_verified else None
                    ),
                },
            },
        }

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset_lab(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import reset
        return reset()
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'cognito':
        from .cognito_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'rds':
        from .rds_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'ecs':
        from .ecs_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'eks':
        from .eks_labs import reset
        return reset(service_key, lab_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2':
        from .ec2_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'iam':
        from .iam_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'lambda':
        from .lambda_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'apigateway':
        from .apigateway_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'kms':
        from .kms_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'ssm':
        from .ssm_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import reset
        return reset(service_key, lab_key)

    if service_key == 's3':
        from .s3_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'sqs':
        from .sqs_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'scheduler':
        from .scheduler_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        return _reset_cloudformation_s3_sqs()

    raise ValueError('Lab reset not found')


def _config_lab_step_result(
    service: str,
    lab: str,
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': service,
        'lab': lab,
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _cloudformation_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'cloudformation',
        'lab': 's3-sqs-stack',
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_cloudformation_validate_template() -> dict[str, Any]:
    command = (
        'aws cloudformation validate-template '
        '--template-body file://storage-messaging-stack.json'
    )
    started = time.perf_counter()
    response = _cloudformation_client().validate_template(
        TemplateBody=CLOUDFORMATION_TEMPLATE_TEXT,
    )
    verified = (
        response.get('Parameters') == []
        and response.get('Capabilities') == []
    )
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'CloudFormation accepted the template with no parameters or capabilities.'
            if verified
            else 'CloudFormation did not validate the expected template shape.'
        ),
        'resource': _clean_response(response),
    }
    return _cloudformation_step_result(
        'validate-template',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_create_stack() -> dict[str, Any]:
    command = (
        f'aws cloudformation create-stack --stack-name '
        f'{CLOUDFORMATION_STACK_NAME} '
        '--template-body file://storage-messaging-stack.json'
    )
    started = time.perf_counter()
    response = _cloudformation_client().create_stack(
        StackName=CLOUDFORMATION_STACK_NAME,
        TemplateBody=CLOUDFORMATION_TEMPLATE_TEXT,
    )
    stack = _wait_for_cloudformation_status(
        {'CREATE_COMPLETE', 'CREATE_FAILED', 'ROLLBACK_COMPLETE'},
    )
    if stack.get('StackStatus') == 'CREATE_COMPLETE':
        cache.set(CLOUDFORMATION_CREATED_CACHE_KEY, True, timeout=86400)
        cache.delete_many([
            CLOUDFORMATION_INSPECTED_CACHE_KEY,
            CLOUDFORMATION_DELETED_CACHE_KEY,
        ])
    verification = _verify_cloudformation_stack()
    output = {**_clean_response(response), 'Stack': _clean_response(stack)}
    return _cloudformation_step_result(
        'create-stack',
        command,
        output,
        verification,
        started,
    )


def _run_cloudformation_describe_stack() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stacks '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    response = _cloudformation_client().describe_stacks(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    verification = _verify_cloudformation_stack()
    return _cloudformation_step_result(
        'describe-stack',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_describe_resources() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stack-resources '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    response = _cloudformation_client().describe_stack_resources(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    verification = _verify_cloudformation_resources()
    return _cloudformation_step_result(
        'describe-stack-resources',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_describe_events() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stack-events '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    response = _cloudformation_client().describe_stack_events(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    verification = _verify_cloudformation_events()
    return _cloudformation_step_result(
        'describe-stack-events',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_inspect_services() -> dict[str, Any]:
    command = (
        f'aws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\n'
        'aws sqs get-queue-attributes --queue-url <queue-url> '
        '--attribute-names QueueArn VisibilityTimeout'
    )
    started = time.perf_counter()
    verification = _verify_cloudformation_service_resources()
    if verification.get('status') == 'passed':
        cache.set(CLOUDFORMATION_INSPECTED_CACHE_KEY, True, timeout=86400)
    response = verification.get('resource', {})
    return _cloudformation_step_result(
        'inspect-provisioned-resources',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_delete_stack() -> dict[str, Any]:
    command = (
        f'aws cloudformation delete-stack '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    if not cache.get(CLOUDFORMATION_INSPECTED_CACHE_KEY):
        raise ValueError('Inspect the provisioned resources before deleting the stack.')
    started = time.perf_counter()
    response = _cloudformation_client().delete_stack(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            _cloudformation_stack()
        except ClientError:
            cache.set(CLOUDFORMATION_DELETED_CACHE_KEY, True, timeout=86400)
            break
        time.sleep(0.1)
    verification = _verify_cloudformation_deleted()
    return _cloudformation_step_result(
        'delete-stack',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_confirm_deleted() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stacks '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}\n'
        f'aws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\n'
        f'aws sqs get-queue-url --queue-name {CLOUDFORMATION_QUEUE_NAME}'
    )
    started = time.perf_counter()
    verification = _verify_cloudformation_deleted()
    response = {
        'stack_absent': verification.get('status') == 'passed',
        'bucket_absent': verification.get('status') == 'passed',
        'queue_absent': verification.get('status') == 'passed',
    }
    return _cloudformation_step_result(
        'confirm-resources-deleted',
        command,
        response,
        verification,
        started,
    )


def _ignore_missing_codes(action, missing_codes: set[str]) -> bool:
    try:
        action()
        return True
    except ClientError as exc:
        if _error_code(exc) in missing_codes:
            return False
        raise


def _reset_cloudformation_s3_sqs() -> dict[str, Any]:
    command = (
        f'aws cloudformation delete-stack '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    deleted_stack = False
    deleted_bucket = False
    deleted_queue = False
    try:
        _cloudformation_client().delete_stack(
            StackName=CLOUDFORMATION_STACK_NAME,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                _cloudformation_stack()
            except ClientError:
                deleted_stack = True
                break
            time.sleep(0.1)
    except ClientError:
        deleted_stack = True

    try:
        _s3_client().head_bucket(Bucket=CLOUDFORMATION_BUCKET_NAME)
    except ClientError:
        deleted_bucket = True
    if not deleted_bucket:
        bucket_result = _reset_s3_bucket(
            's3-sqs-stack',
            CLOUDFORMATION_BUCKET_NAME,
        )
        deleted_bucket = bucket_result.get('deleted_bucket', False)

    try:
        queue_url = _sqs_queue_url(CLOUDFORMATION_QUEUE_NAME)
        _sqs_client().delete_queue(QueueUrl=queue_url)
        deleted_queue = True
    except ClientError as exc:
        if _error_code(exc) in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            deleted_queue = True
        else:
            raise

    cache.delete_many([
        CLOUDFORMATION_CREATED_CACHE_KEY,
        CLOUDFORMATION_INSPECTED_CACHE_KEY,
        CLOUDFORMATION_DELETED_CACHE_KEY,
    ])
    output = {
        'deleted_stack': deleted_stack,
        'deleted_bucket': deleted_bucket,
        'deleted_queue': deleted_queue,
    }
    return {
        'service': 'cloudformation',
        'lab': 's3-sqs-stack',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': output,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **output,
        'verification': {
            'status': 'passed',
            'message': 'The CloudFormation stack and its S3 and SQS resources were removed.',
        },
    }


# Service lab modules. Their names stay importable from ``dashboard.labs`` so existing
# callers and tests keep working.
from . import sqs_labs, sns_labs, scheduler_labs, kms_labs, ssm_labs, secretsmanager_labs, dynamodb_labs, apigateway_labs, lambda_labs, s3_labs, iam_labs, ec2_labs  # noqa: E402

for _module in (sqs_labs, sns_labs, scheduler_labs, kms_labs, ssm_labs, secretsmanager_labs, dynamodb_labs, apigateway_labs, lambda_labs, s3_labs, iam_labs, ec2_labs,):
    for _name, _value in vars(_module).items():
        if not _name.startswith('__') and _name not in ('LABS', 'run_step', 'status', 'reset'):
            globals().setdefault(_name, _value)
del _module, _name, _value
