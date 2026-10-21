"""EventBridge Scheduler workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _error_code,
    _iam_client,
    _json_text,
    _scheduler_client,
    _sqs_client,
    _sqs_queue_url,
)


SCHEDULER_SQS_QUEUE_NAME = 'lab-scheduled-reports'
SCHEDULER_SQS_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SCHEDULER_SQS_QUEUE_NAME}'
)
SCHEDULER_ROLE_NAME = 'FlociSchedulerSqsRole'
SCHEDULER_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{SCHEDULER_ROLE_NAME}'
SCHEDULER_ROLE_POLICY_NAME = 'SendScheduledReportToSqs'
SCHEDULER_GROUP_NAME = 'lab-scheduler'
SCHEDULER_GROUP_ARN = (
    f'arn:aws:scheduler:{AWS_REGION}:{AWS_ACCOUNT_ID}:'
    f'schedule-group/{SCHEDULER_GROUP_NAME}'
)
SCHEDULER_SCHEDULE_NAME = 'send-report-ready'
SCHEDULER_SCHEDULE_ARN = (
    f'arn:aws:scheduler:{AWS_REGION}:{AWS_ACCOUNT_ID}:schedule/'
    f'{SCHEDULER_GROUP_NAME}/{SCHEDULER_SCHEDULE_NAME}'
)
SCHEDULER_MESSAGE = {
    'event': 'report.ready',
    'report_id': 'FLOCI-SCHEDULE-1001',
}
SCHEDULER_MESSAGE_TEXT = json.dumps(SCHEDULER_MESSAGE, separators=(',', ':'))
SCHEDULER_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Principal': {'Service': 'scheduler.amazonaws.com'},
        'Action': 'sts:AssumeRole',
    }],
}
SCHEDULER_ROLE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Action': 'sqs:SendMessage',
        'Resource': SCHEDULER_SQS_QUEUE_ARN,
    }],
}
SCHEDULER_EXPRESSION_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:expression'
SCHEDULER_CREATED_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:created'
SCHEDULER_DELIVERED_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:delivered'
SCHEDULER_DELETED_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:deleted'
SCHEDULER_SQS_DELIVERY_LAB = {
    'service': 'scheduler',
    'key': 'sqs-delivery',
    'title': 'Schedule an EventBridge Scheduler message to SQS',
    'description': 'Create the execution role and target queue, schedule one report event, verify delivery, and confirm automatic schedule cleanup.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create the target queue',
            'command': f'aws sqs create-queue --queue-name {SCHEDULER_SQS_QUEUE_NAME}',
            'explanation': 'Creates the SQS queue that will receive the scheduled report event.',
        },
        {
            'key': 'create-role',
            'title': 'Create the Scheduler execution role',
            'command': f'aws iam create-role --role-name {SCHEDULER_ROLE_NAME} --assume-role-policy-document file://scheduler-trust-policy.json',
            'explanation': 'Creates a role trusted only by the EventBridge Scheduler service.',
            'artifact_label': 'scheduler-trust-policy.json',
            'artifact': json.dumps(SCHEDULER_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Authorize SQS delivery',
            'command': f'aws iam put-role-policy --role-name {SCHEDULER_ROLE_NAME} --policy-name {SCHEDULER_ROLE_POLICY_NAME} --policy-document file://send-message-policy.json',
            'explanation': 'Grants the execution role only sqs:SendMessage on the dedicated target queue.',
            'artifact_label': 'send-message-policy.json',
            'artifact': json.dumps(SCHEDULER_ROLE_POLICY, indent=2),
        },
        {
            'key': 'create-schedule-group',
            'title': 'Create a schedule group',
            'command': f'aws scheduler create-schedule-group --name {SCHEDULER_GROUP_NAME}',
            'explanation': 'Creates a dedicated container for the lab schedule.',
        },
        {
            'key': 'create-schedule',
            'title': 'Schedule the report event',
            'command': f'aws scheduler create-schedule --name {SCHEDULER_SCHEDULE_NAME} --group-name {SCHEDULER_GROUP_NAME} --schedule-expression "at(<utc-time-seconds-from-now>)" --flexible-time-window Mode=OFF --target file://target.json --action-after-completion DELETE',
            'explanation': 'Creates a one-time schedule a few seconds in the future. The runner fills in the live UTC timestamp.',
            'artifact_label': 'target.json',
            'artifact': json.dumps({
                'Arn': SCHEDULER_SQS_QUEUE_ARN,
                'RoleArn': SCHEDULER_ROLE_ARN,
                'Input': SCHEDULER_MESSAGE_TEXT,
                'RetryPolicy': {
                    'MaximumEventAgeInSeconds': 60,
                    'MaximumRetryAttempts': 1,
                },
            }, indent=2),
            'secondary_artifact_label': 'scheduled-message.json',
            'secondary_artifact': json.dumps(SCHEDULER_MESSAGE, indent=2),
        },
        {
            'key': 'get-schedule',
            'title': 'Inspect the pending schedule',
            'command': f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} --group-name {SCHEDULER_GROUP_NAME}',
            'explanation': 'Verifies the one-time expression, disabled flexible window, execution role, target queue, retry policy, and automatic deletion setting.',
        },
        {
            'key': 'receive-scheduled-message',
            'title': 'Wait for SQS delivery',
            'command': 'aws sqs receive-message --queue-url <scheduled-reports-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 12',
            'explanation': 'Polls through Scheduler invocation latency and verifies the exact report.ready JSON body.',
        },
        {
            'key': 'confirm-schedule-deleted',
            'title': 'Confirm automatic schedule cleanup',
            'command': f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} --group-name {SCHEDULER_GROUP_NAME}',
            'explanation': 'Requires ResourceNotFoundException, proving ActionAfterCompletion=DELETE removed the one-time schedule.',
        },
    ],
}


def _verify_scheduler_queue() -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Attributes', {}).get('QueueArn') == SCHEDULER_SQS_QUEUE_ARN:
        return {
            'status': 'passed',
            'message': 'The scheduled-reports target queue exists.',
            'resource': {
                'queue_url': queue_url,
                'queue_arn': SCHEDULER_SQS_QUEUE_ARN,
            },
        }
    return {'status': 'failed', 'message': 'The target queue is not ready.'}


def _verify_scheduler_role() -> dict[str, Any]:
    try:
        response = _iam_client().get_role(RoleName=SCHEDULER_ROLE_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    role = response.get('Role', {})
    if (
        role.get('Arn') == SCHEDULER_ROLE_ARN
        and role.get('AssumeRolePolicyDocument') == SCHEDULER_TRUST_POLICY
    ):
        return {
            'status': 'passed',
            'message': 'The execution role trusts only EventBridge Scheduler.',
            'resource': _clean_response(role),
        }
    return {'status': 'failed', 'message': 'The Scheduler role trust policy is incorrect.'}


def _verify_scheduler_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=SCHEDULER_ROLE_NAME,
            PolicyName=SCHEDULER_ROLE_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyDocument') == SCHEDULER_ROLE_POLICY:
        return {
            'status': 'passed',
            'message': 'The execution role can send messages only to the lab queue.',
            'resource': _clean_response(response),
        }
    return {'status': 'failed', 'message': 'The execution role policy is incorrect.'}


def _verify_scheduler_group() -> dict[str, Any]:
    try:
        response = _scheduler_client().get_schedule_group(
            Name=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Arn') == SCHEDULER_GROUP_ARN:
        return {
            'status': 'passed',
            'message': 'The dedicated schedule group exists.',
            'resource': _clean_response(response),
        }
    return {'status': 'failed', 'message': 'The schedule group does not exist.'}


def _verify_scheduler_schedule() -> dict[str, Any]:
    expression = cache.get(SCHEDULER_EXPRESSION_CACHE_KEY)
    if not expression:
        return {
            'status': 'failed',
            'message': 'The lab has not created its one-time schedule.',
        }
    try:
        response = _scheduler_client().get_schedule(
            Name=SCHEDULER_SCHEDULE_NAME,
            GroupName=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    target = response.get('Target', {})
    if (
        response.get('Arn') == SCHEDULER_SCHEDULE_ARN
        and response.get('ScheduleExpression') == expression
        and response.get('FlexibleTimeWindow') == {'Mode': 'OFF'}
        and response.get('ActionAfterCompletion') == 'DELETE'
        and response.get('State') == 'ENABLED'
        and target.get('Arn') == SCHEDULER_SQS_QUEUE_ARN
        and target.get('RoleArn') == SCHEDULER_ROLE_ARN
        and target.get('Input') == SCHEDULER_MESSAGE_TEXT
        and target.get('RetryPolicy') == {
            'MaximumEventAgeInSeconds': 60,
            'MaximumRetryAttempts': 1,
        }
    ):
        return {
            'status': 'passed',
            'message': 'The pending one-time schedule has the expected target and execution settings.',
            'resource': _clean_response(response),
        }
    return {'status': 'failed', 'message': 'The schedule configuration is incomplete.'}


def _scheduler_message(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            message for message in messages
            if message.get('Body') == SCHEDULER_MESSAGE_TEXT
        ),
        None,
    )


def _verify_scheduler_delivery() -> dict[str, Any]:
    try:
        response = _sqs_client().receive_message(
            QueueUrl=_sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME),
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    message = _scheduler_message(response.get('Messages', []))
    if message:
        return {
            'status': 'passed',
            'message': 'The target queue contains the scheduled report.ready event.',
            'resource': _clean_response(message),
        }
    return {'status': 'failed', 'message': 'The scheduled event is not available.'}


def _verify_scheduler_deleted() -> dict[str, Any]:
    if not cache.get(SCHEDULER_DELETED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'Automatic schedule deletion has not been verified.',
        }
    try:
        _scheduler_client().get_schedule(
            Name=SCHEDULER_SCHEDULE_NAME,
            GroupName=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        if _error_code(exc) == 'ResourceNotFoundException':
            return {
                'status': 'passed',
                'message': 'The completed one-time schedule was deleted automatically.',
            }
        return {'status': 'failed', 'message': str(exc)}
    return {'status': 'failed', 'message': 'The completed schedule still exists.'}


def _scheduler_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'scheduler',
        'lab': 'sqs-delivery',
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


def _run_scheduler_create_queue() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {SCHEDULER_SQS_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=SCHEDULER_SQS_QUEUE_NAME)
    verification = _verify_scheduler_queue()
    return _scheduler_step_result(
        'create-queue',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_create_role() -> dict[str, Any]:
    command = (
        f'aws iam create-role --role-name {SCHEDULER_ROLE_NAME} '
        '--assume-role-policy-document file://scheduler-trust-policy.json'
    )
    started = time.perf_counter()
    try:
        response = _iam_client().create_role(
            RoleName=SCHEDULER_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(SCHEDULER_TRUST_POLICY),
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_role(RoleName=SCHEDULER_ROLE_NAME)
    verification = _verify_scheduler_role()
    return _scheduler_step_result(
        'create-role',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_put_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {SCHEDULER_ROLE_NAME} '
        f'--policy-name {SCHEDULER_ROLE_POLICY_NAME} '
        '--policy-document file://send-message-policy.json'
    )
    started = time.perf_counter()
    response = _iam_client().put_role_policy(
        RoleName=SCHEDULER_ROLE_NAME,
        PolicyName=SCHEDULER_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(SCHEDULER_ROLE_POLICY),
    )
    verification = _verify_scheduler_role_policy()
    return _scheduler_step_result(
        'put-role-policy',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_create_group() -> dict[str, Any]:
    command = (
        'aws scheduler create-schedule-group '
        f'--name {SCHEDULER_GROUP_NAME}'
    )
    started = time.perf_counter()
    try:
        response = _scheduler_client().create_schedule_group(
            Name=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ConflictException':
            raise
        response = _scheduler_client().get_schedule_group(
            Name=SCHEDULER_GROUP_NAME,
        )
    verification = _verify_scheduler_group()
    return _scheduler_step_result(
        'create-schedule-group',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_create_schedule() -> dict[str, Any]:
    scheduled_time = datetime.now(timezone.utc) + timedelta(seconds=8)
    timestamp = scheduled_time.isoformat(timespec='seconds').replace('+00:00', '')
    expression = f'at({timestamp})'
    command = (
        f'aws scheduler create-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME} '
        f'--schedule-expression "{expression}" '
        '--flexible-time-window Mode=OFF --target file://target.json '
        '--action-after-completion DELETE'
    )
    target = {
        'Arn': SCHEDULER_SQS_QUEUE_ARN,
        'RoleArn': SCHEDULER_ROLE_ARN,
        'Input': SCHEDULER_MESSAGE_TEXT,
        'RetryPolicy': {
            'MaximumEventAgeInSeconds': 60,
            'MaximumRetryAttempts': 1,
        },
    }
    started = time.perf_counter()
    response = _scheduler_client().create_schedule(
        Name=SCHEDULER_SCHEDULE_NAME,
        GroupName=SCHEDULER_GROUP_NAME,
        ScheduleExpression=expression,
        FlexibleTimeWindow={'Mode': 'OFF'},
        Target=target,
        State='ENABLED',
        ActionAfterCompletion='DELETE',
        Description='Send a report.ready event to the local SQS lab queue.',
    )
    cache.set(SCHEDULER_EXPRESSION_CACHE_KEY, expression, timeout=86400)
    cache.set(SCHEDULER_CREATED_CACHE_KEY, True, timeout=86400)
    cache.delete_many([
        SCHEDULER_DELIVERED_CACHE_KEY,
        SCHEDULER_DELETED_CACHE_KEY,
    ])
    verification = _verify_scheduler_schedule()
    return _scheduler_step_result(
        'create-schedule',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_get_schedule() -> dict[str, Any]:
    command = (
        f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME}'
    )
    started = time.perf_counter()
    response = _scheduler_client().get_schedule(
        Name=SCHEDULER_SCHEDULE_NAME,
        GroupName=SCHEDULER_GROUP_NAME,
    )
    verification = _verify_scheduler_schedule()
    return _scheduler_step_result(
        'get-schedule',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_receive_message() -> dict[str, Any]:
    command = (
        'aws sqs receive-message '
        '--queue-url <scheduled-reports-queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 12'
    )
    started = time.perf_counter()
    response: dict[str, Any] = {}
    message = None
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        response = _sqs_client().receive_message(
            QueueUrl=_sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME),
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
        )
        message = _scheduler_message(response.get('Messages', []))
        if message:
            break
        time.sleep(0.1)
    if message:
        cache.set(SCHEDULER_DELIVERED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'EventBridge Scheduler delivered the report.ready event to SQS.',
            'resource': _clean_response(message),
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The scheduled report event did not arrive within the delivery window.',
        }
    return _scheduler_step_result(
        'receive-scheduled-message',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_confirm_deleted() -> dict[str, Any]:
    command = (
        f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME}'
    )
    started = time.perf_counter()
    response: dict[str, Any] = {}
    deleted = False
    for _ in range(10):
        try:
            response = _scheduler_client().get_schedule(
                Name=SCHEDULER_SCHEDULE_NAME,
                GroupName=SCHEDULER_GROUP_NAME,
            )
        except ClientError as exc:
            if _error_code(exc) == 'ResourceNotFoundException':
                deleted = True
                response = {
                    'Error': {
                        'Code': _error_code(exc),
                        'Message': exc.response.get('Error', {}).get('Message'),
                    },
                }
                break
            raise
        time.sleep(0.1)
    if deleted:
        cache.set(SCHEDULER_DELETED_CACHE_KEY, True, timeout=86400)
    verification = _verify_scheduler_deleted()
    return _scheduler_step_result(
        'confirm-schedule-deleted',
        command,
        response,
        verification,
        started,
    )


def _reset_scheduler_sqs_delivery() -> dict[str, Any]:
    command = (
        f'aws scheduler delete-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME}\n'
        f'aws scheduler delete-schedule-group --name {SCHEDULER_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {SCHEDULER_ROLE_NAME} '
        f'--policy-name {SCHEDULER_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {SCHEDULER_ROLE_NAME}\n'
        'aws sqs delete-queue --queue-url <scheduled-reports-queue-url>'
    )
    started = time.perf_counter()
    deleted_schedule = False
    deleted_group = False
    deleted_policy = False
    deleted_role = False
    deleted_queue = False
    try:
        _scheduler_client().delete_schedule(
            Name=SCHEDULER_SCHEDULE_NAME,
            GroupName=SCHEDULER_GROUP_NAME,
        )
        deleted_schedule = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        _scheduler_client().delete_schedule_group(Name=SCHEDULER_GROUP_NAME)
        deleted_group = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        _iam_client().delete_role_policy(
            RoleName=SCHEDULER_ROLE_NAME,
            PolicyName=SCHEDULER_ROLE_POLICY_NAME,
        )
        deleted_policy = True
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
    try:
        _iam_client().delete_role(RoleName=SCHEDULER_ROLE_NAME)
        deleted_role = True
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
    try:
        _sqs_client().delete_queue(QueueUrl=_sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME))
        deleted_queue = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete_many([
        SCHEDULER_EXPRESSION_CACHE_KEY,
        SCHEDULER_CREATED_CACHE_KEY,
        SCHEDULER_DELIVERED_CACHE_KEY,
        SCHEDULER_DELETED_CACHE_KEY,
    ])
    output = {
        'deleted_schedule': deleted_schedule,
        'deleted_group': deleted_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_queue': deleted_queue,
    }
    return {
        'service': 'scheduler',
        'lab': 'sqs-delivery',
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
            'message': 'Schedule resources, execution role, and target queue were removed.',
        },
    }


LABS = [SCHEDULER_SQS_DELIVERY_LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'scheduler' and lab_key == 'sqs-delivery':
        runners = {
            'create-queue': _run_scheduler_create_queue,
            'create-role': _run_scheduler_create_role,
            'put-role-policy': _run_scheduler_put_role_policy,
            'create-schedule-group': _run_scheduler_create_group,
            'create-schedule': _run_scheduler_create_schedule,
            'get-schedule': _run_scheduler_get_schedule,
            'receive-scheduled-message': _run_scheduler_receive_message,
            'confirm-schedule-deleted': _run_scheduler_confirm_deleted,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'scheduler' and lab_key == 'sqs-delivery':
        queue_verification = _verify_scheduler_queue()
        role_verification = _verify_scheduler_role()
        policy_verification = _verify_scheduler_role_policy()
        group_verification = _verify_scheduler_group()
        delivery_verification = _verify_scheduler_delivery()
        deleted_verification = _verify_scheduler_deleted()
        queue_verified = queue_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        group_verified = group_verification.get('status') == 'passed'
        delivery_verified = delivery_verification.get('status') == 'passed'
        deleted_verified = deleted_verification.get('status') == 'passed'
        created_verified = bool(cache.get(SCHEDULER_CREATED_CACHE_KEY))
        created_verification = {
            'status': 'passed',
            'message': 'The one-time schedule was created with the verified target settings.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                queue_verified
                and role_verified
                and policy_verified
                and group_verified
                and created_verified
                and delivery_verified
                and deleted_verified
            ),
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'create-role': {
                    'verified': role_verified,
                    'verification': role_verification if role_verified else None,
                },
                'put-role-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'create-schedule-group': {
                    'verified': group_verified,
                    'verification': group_verification if group_verified else None,
                },
                'create-schedule': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'get-schedule': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'receive-scheduled-message': {
                    'verified': delivery_verified,
                    'verification': (
                        delivery_verification if delivery_verified else None
                    ),
                },
                'confirm-schedule-deleted': {
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
    if service_key == 'scheduler' and lab_key == 'sqs-delivery':
        return _reset_scheduler_sqs_delivery()

    raise ValueError('Lab reset not found')
