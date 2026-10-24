"""CloudFormation workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _cloudformation_client,
    _error_code,
    _json_text,
    _s3_client,
    _sqs_client,
    _sqs_queue_url,
)
from .s3_labs import (
    _reset_s3_bucket,
)


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
CLOUDFORMATION_DLQ_NAME = 'lab-cfn-jobs-dlq'
CLOUDFORMATION_DLQ_ARN = f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{CLOUDFORMATION_DLQ_NAME}'
CLOUDFORMATION_CHANGE_SET_NAME = 'lab-add-dead-letter-queue'
CLOUDFORMATION_UPDATED_VISIBILITY_TIMEOUT = 60
CLOUDFORMATION_MAX_RECEIVE_COUNT = 3
CLOUDFORMATION_UPDATED_TEMPLATE = json.loads(json.dumps(CLOUDFORMATION_TEMPLATE))
CLOUDFORMATION_UPDATED_TEMPLATE['Description'] = (
    'Provision local S3 storage and an SQS work queue with a dead-letter queue.'
)
CLOUDFORMATION_UPDATED_TEMPLATE['Resources']['JobsDeadLetterQueue'] = {
    'Type': 'AWS::SQS::Queue',
    'Properties': {
        'QueueName': CLOUDFORMATION_DLQ_NAME,
        'MessageRetentionPeriod': 1209600,
    },
}
CLOUDFORMATION_UPDATED_TEMPLATE['Resources']['JobsQueue']['Properties'].update({
    'VisibilityTimeout': CLOUDFORMATION_UPDATED_VISIBILITY_TIMEOUT,
    'RedrivePolicy': {
        'deadLetterTargetArn': {'Fn::GetAtt': ['JobsDeadLetterQueue', 'Arn']},
        'maxReceiveCount': CLOUDFORMATION_MAX_RECEIVE_COUNT,
    },
})
CLOUDFORMATION_UPDATED_TEMPLATE['Outputs']['DeadLetterQueueArn'] = {
    'Description': 'ARN of the dead-letter queue.',
    'Value': {'Fn::GetAtt': ['JobsDeadLetterQueue', 'Arn']},
}
CLOUDFORMATION_UPDATED_TEMPLATE_TEXT = json.dumps(CLOUDFORMATION_UPDATED_TEMPLATE, indent=2)
CLOUDFORMATION_CREATE_CHANGE_SET_COMMAND = (
    f'aws cloudformation create-change-set --stack-name {CLOUDFORMATION_STACK_NAME} '
    f'--change-set-name {CLOUDFORMATION_CHANGE_SET_NAME} --change-set-type UPDATE '
    '--template-body file://storage-messaging-stack-v2.json'
)
CLOUDFORMATION_DESCRIBE_CHANGE_SET_COMMAND = (
    f'aws cloudformation describe-change-set --stack-name {CLOUDFORMATION_STACK_NAME} '
    f'--change-set-name {CLOUDFORMATION_CHANGE_SET_NAME}'
)
CLOUDFORMATION_EXECUTE_CHANGE_SET_COMMAND = (
    f'aws cloudformation execute-change-set --stack-name {CLOUDFORMATION_STACK_NAME} '
    f'--change-set-name {CLOUDFORMATION_CHANGE_SET_NAME}'
)
CLOUDFORMATION_CHANGE_SET_EXECUTED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:change-set-executed'
CLOUDFORMATION_CREATED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:created'
CLOUDFORMATION_INSPECTED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:inspected'
CLOUDFORMATION_DELETED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:deleted'
CLOUDFORMATION_S3_SQS_LAB = {
    'service': 'cloudformation',
    'key': 's3-sqs-stack',
    'title': 'Provision S3 and SQS resources with CloudFormation',
    'description': (
        'Validate one infrastructure template, create a stack, and inspect its outputs, resources, and events. '
        'Then preview an update with a change set before applying it, and finally delete the stack and verify cleanup.'
    ),
    'steps': [
        {
            'key': 'validate-template',
            'title': 'Validate the infrastructure template',
            'command': 'aws cloudformation validate-template --template-body file://storage-messaging-stack.json',
            'explanation': (
                'On AWS this checks the template structure before creating any resources. '
                'Floci accepts any template here and returns no parameters, so a mistake shows up only when the stack is created.'
            ),
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
            'key': 'create-change-set',
            'title': 'Propose an update with a change set',
            'command': CLOUDFORMATION_CREATE_CHANGE_SET_COMMAND,
            'explanation': (
                'Submits version 2 of the template as a change set: it adds a dead-letter queue and changes the jobs '
                f'queue to {CLOUDFORMATION_UPDATED_VISIBILITY_TIMEOUT}s visibility with a redrive policy. '
                'Nothing changes yet; CloudFormation only works out what would happen.'
            ),
            'artifact_label': 'storage-messaging-stack-v2.json',
            'artifact': CLOUDFORMATION_UPDATED_TEMPLATE_TEXT,
        },
        {
            'key': 'describe-change-set',
            'title': 'Review the proposed changes',
            'command': CLOUDFORMATION_DESCRIBE_CHANGE_SET_COMMAND,
            'explanation': (
                'Lists each resource change: JobsDeadLetterQueue is an Add, JobsQueue is a Modify with '
                'Replacement False. Checking Replacement before you apply is the point of a change set: a replaced '
                'database or bucket would lose its data.'
            ),
        },
        {
            'key': 'execute-change-set',
            'title': 'Apply the reviewed change set',
            'command': CLOUDFORMATION_EXECUTE_CHANGE_SET_COMMAND,
            'explanation': (
                'Applies exactly the reviewed changes. The stack moves to UPDATE_COMPLETE, the dead-letter queue '
                'exists, and the jobs queue now carries the new visibility timeout and redrive policy.'
            ),
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
            'command': f'aws cloudformation describe-stacks --stack-name {CLOUDFORMATION_STACK_NAME}\naws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\naws sqs get-queue-url --queue-name {CLOUDFORMATION_QUEUE_NAME}\naws sqs get-queue-url --queue-name {CLOUDFORMATION_DLQ_NAME}',
            'explanation': 'Requires the stack, bucket, and both queues to be absent after deletion, including the queue the change set added.',
        },
    ],
}


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
        stack.get('StackStatus') in {'CREATE_COMPLETE', 'UPDATE_COMPLETE'}
        and outputs.get('BucketName') == CLOUDFORMATION_BUCKET_NAME
        and queue_url.rstrip('/').endswith(f'/{CLOUDFORMATION_QUEUE_NAME}')
        and outputs.get('QueueArn') == CLOUDFORMATION_QUEUE_ARN
    ):
        return {
            'status': 'passed',
            'message': f"The stack is {stack.get('StackStatus')} with all expected outputs.",
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
        and bucket.get('ResourceStatus') in {'CREATE_COMPLETE', 'UPDATE_COMPLETE'}
        and queue.get('PhysicalResourceId', '').rstrip('/').endswith(
            f'/{CLOUDFORMATION_QUEUE_NAME}'
        )
        and queue.get('ResourceType') == 'AWS::SQS::Queue'
        and queue.get('ResourceStatus') in {'CREATE_COMPLETE', 'UPDATE_COMPLETE'}
    ):
        return {
            'status': 'passed',
            'message': 'Both logical resources map to completed physical resources.',
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
    dlq_absent = False
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
    try:
        _sqs_queue_url(CLOUDFORMATION_DLQ_NAME)
    except ClientError as exc:
        dlq_absent = _error_code(exc) in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }
    if stack_absent and bucket_absent and queue_absent and dlq_absent:
        return {
            'status': 'passed',
            'message': 'The stack, bucket, jobs queue, and dead-letter queue are all absent.',
        }
    return {'status': 'failed', 'message': 'One or more stack-owned resources still exist.'}


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
            CLOUDFORMATION_CHANGE_SET_EXECUTED_CACHE_KEY,
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


def _describe_lab_change_set() -> dict[str, Any]:
    return _cloudformation_client().describe_change_set(
        StackName=CLOUDFORMATION_STACK_NAME,
        ChangeSetName=CLOUDFORMATION_CHANGE_SET_NAME,
    )


def _change_set_actions(change_set: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        change.get('ResourceChange', {}).get('LogicalResourceId'): change.get('ResourceChange', {})
        for change in change_set.get('Changes', [])
    }


def _verify_cloudformation_change_set_proposed() -> dict[str, Any]:
    try:
        change_set = _describe_lab_change_set()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    actions = _change_set_actions(change_set)
    dlq = actions.get('JobsDeadLetterQueue', {})
    queue = actions.get('JobsQueue', {})
    if (
        change_set.get('Status') == 'CREATE_COMPLETE'
        and dlq.get('Action') == 'Add'
        and queue.get('Action') == 'Modify'
        and str(queue.get('Replacement')) in {'False', 'false'}
    ):
        return {
            'status': 'passed',
            'message': (
                'The change set adds JobsDeadLetterQueue and modifies JobsQueue in place (Replacement False); '
                f"execution status {change_set.get('ExecutionStatus')}."
            ),
            'resource': {'changes': _clean_response(list(actions.values()))},
        }
    return {
        'status': 'failed',
        'message': f"Change set {CLOUDFORMATION_CHANGE_SET_NAME} does not show the expected Add and in-place Modify.",
    }


def _verify_cloudformation_change_set_executed() -> dict[str, Any]:
    try:
        stack = _cloudformation_stack()
        queue_url = _sqs_queue_url(CLOUDFORMATION_QUEUE_NAME)
        attributes = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['VisibilityTimeout', 'RedrivePolicy'],
        ).get('Attributes', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    outputs = {item.get('OutputKey'): item.get('OutputValue') for item in stack.get('Outputs', [])}
    redrive = json.loads(attributes.get('RedrivePolicy') or '{}')
    if (
        stack.get('StackStatus') == 'UPDATE_COMPLETE'
        and outputs.get('DeadLetterQueueArn') == CLOUDFORMATION_DLQ_ARN
        and attributes.get('VisibilityTimeout') == str(CLOUDFORMATION_UPDATED_VISIBILITY_TIMEOUT)
        and redrive.get('deadLetterTargetArn') == CLOUDFORMATION_DLQ_ARN
        and int(redrive.get('maxReceiveCount', 0)) == CLOUDFORMATION_MAX_RECEIVE_COUNT
    ):
        return {
            'status': 'passed',
            'message': (
                f'The stack is UPDATE_COMPLETE; {CLOUDFORMATION_QUEUE_NAME} now has '
                f'{CLOUDFORMATION_UPDATED_VISIBILITY_TIMEOUT}s visibility and redrives to {CLOUDFORMATION_DLQ_NAME} '
                f'after {CLOUDFORMATION_MAX_RECEIVE_COUNT} receives.'
            ),
            'resource': {'outputs': outputs, 'queue_attributes': attributes},
        }
    return {'status': 'failed', 'message': 'The stack does not reflect the executed change set yet.'}


def _run_cloudformation_create_change_set() -> dict[str, Any]:
    started = time.perf_counter()
    client = _cloudformation_client()
    try:
        response = client.create_change_set(
            StackName=CLOUDFORMATION_STACK_NAME,
            ChangeSetName=CLOUDFORMATION_CHANGE_SET_NAME,
            ChangeSetType='UPDATE',
            TemplateBody=CLOUDFORMATION_UPDATED_TEMPLATE_TEXT,
            Description='Add a dead-letter queue to the jobs queue.',
        )
    except ClientError as exc:
        if _error_code(exc) != 'AlreadyExistsException':
            raise
        response = _describe_lab_change_set()
    deadline = time.monotonic() + 10
    verification = _verify_cloudformation_change_set_proposed()
    while verification.get('status') != 'passed' and time.monotonic() < deadline:
        time.sleep(0.2)
        verification = _verify_cloudformation_change_set_proposed()
    return _cloudformation_step_result(
        'create-change-set',
        CLOUDFORMATION_CREATE_CHANGE_SET_COMMAND,
        response,
        verification,
        started,
    )


def _run_cloudformation_describe_change_set() -> dict[str, Any]:
    started = time.perf_counter()
    response = _describe_lab_change_set()
    return _cloudformation_step_result(
        'describe-change-set',
        CLOUDFORMATION_DESCRIBE_CHANGE_SET_COMMAND,
        response,
        _verify_cloudformation_change_set_proposed(),
        started,
    )


def _run_cloudformation_execute_change_set() -> dict[str, Any]:
    started = time.perf_counter()
    response = _cloudformation_client().execute_change_set(
        StackName=CLOUDFORMATION_STACK_NAME,
        ChangeSetName=CLOUDFORMATION_CHANGE_SET_NAME,
    )
    _wait_for_cloudformation_status({'UPDATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE', 'UPDATE_FAILED'})
    verification = _verify_cloudformation_change_set_executed()
    if verification.get('status') == 'passed':
        cache.set(CLOUDFORMATION_CHANGE_SET_EXECUTED_CACHE_KEY, True, timeout=86400)
    return _cloudformation_step_result(
        'execute-change-set',
        CLOUDFORMATION_EXECUTE_CHANGE_SET_COMMAND,
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
    if not cache.get(CLOUDFORMATION_CHANGE_SET_EXECUTED_CACHE_KEY):
        raise ValueError('Apply the change set before deleting the stack.')
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
        f'aws sqs get-queue-url --queue-name {CLOUDFORMATION_QUEUE_NAME}\n'
        f'aws sqs get-queue-url --queue-name {CLOUDFORMATION_DLQ_NAME}'
    )
    started = time.perf_counter()
    verification = _verify_cloudformation_deleted()
    response = {
        'stack_absent': verification.get('status') == 'passed',
        'bucket_absent': verification.get('status') == 'passed',
        'queue_absent': verification.get('status') == 'passed',
        'dead_letter_queue_absent': verification.get('status') == 'passed',
    }
    return _cloudformation_step_result(
        'confirm-resources-deleted',
        command,
        response,
        verification,
        started,
    )


def _delete_lab_queue(queue_name: str) -> bool:
    try:
        _sqs_client().delete_queue(QueueUrl=_sqs_queue_url(queue_name))
        return True
    except ClientError as exc:
        if _error_code(exc) in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            return True
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

    deleted_queue = _delete_lab_queue(CLOUDFORMATION_QUEUE_NAME)
    deleted_dlq = _delete_lab_queue(CLOUDFORMATION_DLQ_NAME)

    cache.delete_many([
        CLOUDFORMATION_CREATED_CACHE_KEY,
        CLOUDFORMATION_INSPECTED_CACHE_KEY,
        CLOUDFORMATION_DELETED_CACHE_KEY,
        CLOUDFORMATION_CHANGE_SET_EXECUTED_CACHE_KEY,
    ])
    output = {
        'deleted_stack': deleted_stack,
        'deleted_bucket': deleted_bucket,
        'deleted_queue': deleted_queue,
        'deleted_dead_letter_queue': deleted_dlq,
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
            'message': 'The CloudFormation stack and its S3 bucket and SQS queues were removed.',
        },
    }


LABS = [CLOUDFORMATION_S3_SQS_LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        runners = {
            'validate-template': _run_cloudformation_validate_template,
            'create-stack': _run_cloudformation_create_stack,
            'describe-stack': _run_cloudformation_describe_stack,
            'describe-stack-resources': _run_cloudformation_describe_resources,
            'describe-stack-events': _run_cloudformation_describe_events,
            'inspect-provisioned-resources': _run_cloudformation_inspect_services,
            'create-change-set': _run_cloudformation_create_change_set,
            'describe-change-set': _run_cloudformation_describe_change_set,
            'execute-change-set': _run_cloudformation_execute_change_set,
            'delete-stack': _run_cloudformation_delete_stack,
            'confirm-resources-deleted': _run_cloudformation_confirm_deleted,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
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
        executed = bool(cache.get(CLOUDFORMATION_CHANGE_SET_EXECUTED_CACHE_KEY))
        executed_verification = {
            'status': 'passed',
            'message': 'The change set was reviewed and applied; the stack reached UPDATE_COMPLETE.',
        }
        if executed:
            change_set_proposed = True
            change_set_executed = (
                deleted_verified
                or _verify_cloudformation_change_set_executed().get('status') == 'passed'
            )
        else:
            change_set_proposed = (
                not deleted_verified
                and _verify_cloudformation_change_set_proposed().get('status') == 'passed'
            )
            change_set_executed = False
        if deleted_verified or executed:
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
                and executed
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
                'create-change-set': {
                    'verified': change_set_proposed,
                    'verification': executed_verification if executed else (
                        {'status': 'passed', 'message': 'The change set is ready for review.'}
                        if change_set_proposed else None
                    ),
                },
                'describe-change-set': {
                    'verified': change_set_proposed,
                    'verification': executed_verification if executed else (
                        {'status': 'passed', 'message': 'The change set shows an Add and an in-place Modify.'}
                        if change_set_proposed else None
                    ),
                },
                'execute-change-set': {
                    'verified': change_set_executed,
                    'verification': executed_verification if change_set_executed else None,
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


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        return _reset_cloudformation_s3_sqs()

    raise ValueError('Lab reset not found')
