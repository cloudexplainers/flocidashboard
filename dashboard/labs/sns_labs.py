"""SNS workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError

from dashboard.aws import _clean_response
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _error_code,
    _json_text,
    _sns_client,
    _sqs_client,
    _sqs_queue_url,
)


SNS_FANOUT_TOPIC_NAME = 'lab-order-events'
SNS_FANOUT_TOPIC_ARN = (
    f'arn:aws:sns:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SNS_FANOUT_TOPIC_NAME}'
)
SNS_FANOUT_ORDERS_QUEUE_NAME = 'lab-order-processing'
SNS_FANOUT_AUDIT_QUEUE_NAME = 'lab-order-audit'
SNS_FANOUT_ORDERS_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SNS_FANOUT_ORDERS_QUEUE_NAME}'
)
SNS_FANOUT_AUDIT_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SNS_FANOUT_AUDIT_QUEUE_NAME}'
)
SNS_FANOUT_MESSAGE = {
    'event': 'order.created',
    'order_id': 'FLOCI-FANOUT-1001',
}
SNS_FANOUT_MESSAGE_TEXT = json.dumps(SNS_FANOUT_MESSAGE, separators=(',', ':'))
SNS_FANOUT_MESSAGE_ATTRIBUTES = {
    'EventType': {
        'DataType': 'String',
        'StringValue': 'order.created',
    },
    'Environment': {
        'DataType': 'String',
        'StringValue': 'lab',
    },
}
SNS_FILTER_TOPIC_NAME = 'lab-filtered-events'
SNS_FILTER_TOPIC_ARN = (
    f'arn:aws:sns:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SNS_FILTER_TOPIC_NAME}'
)
SNS_FILTER_CREATED_QUEUE_NAME = 'lab-created-events'
SNS_FILTER_PRIORITY_QUEUE_NAME = 'lab-priority-events'
SNS_FILTER_CREATED_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SNS_FILTER_CREATED_QUEUE_NAME}'
)
SNS_FILTER_PRIORITY_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SNS_FILTER_PRIORITY_QUEUE_NAME}'
)
SNS_FILTER_CREATED_POLICY = {'EventType': ['order.created']}
SNS_FILTER_PRIORITY_POLICY = {'Priority': ['high']}
SNS_FILTER_CREATED_MESSAGE = {
    'event': 'order.created',
    'order_id': 'FLOCI-FILTER-1001',
}
SNS_FILTER_CREATED_MESSAGE_TEXT = json.dumps(
    SNS_FILTER_CREATED_MESSAGE,
    separators=(',', ':'),
)
SNS_FILTER_CREATED_ATTRIBUTES = {
    'EventType': {'DataType': 'String', 'StringValue': 'order.created'},
    'Priority': {'DataType': 'String', 'StringValue': 'normal'},
}
SNS_FILTER_PRIORITY_MESSAGE = {
    'event': 'order.cancelled',
    'order_id': 'FLOCI-FILTER-1002',
}
SNS_FILTER_PRIORITY_MESSAGE_TEXT = json.dumps(
    SNS_FILTER_PRIORITY_MESSAGE,
    separators=(',', ':'),
)
SNS_FILTER_PRIORITY_ATTRIBUTES = {
    'EventType': {'DataType': 'String', 'StringValue': 'order.cancelled'},
    'Priority': {'DataType': 'String', 'StringValue': 'high'},
}
SNS_SQS_FANOUT_LAB = {
    'service': 'sns',
    'key': 'sqs-fanout',
    'title': 'Fan out an SNS message to SQS queues',
    'description': 'Publish one order event to an SNS topic and verify that two independently subscribed SQS queues each receive a copy.',
    'steps': [
        {
            'key': 'create-topic',
            'title': 'Create the SNS topic',
            'command': f'aws sns create-topic --name {SNS_FANOUT_TOPIC_NAME}',
            'explanation': 'Creates the publisher-facing topic for order events.',
        },
        {
            'key': 'create-orders-queue',
            'title': 'Create the processing queue',
            'command': f'aws sqs create-queue --queue-name {SNS_FANOUT_ORDERS_QUEUE_NAME}',
            'explanation': 'Creates the queue used by an order-processing consumer.',
        },
        {
            'key': 'create-audit-queue',
            'title': 'Create the audit queue',
            'command': f'aws sqs create-queue --queue-name {SNS_FANOUT_AUDIT_QUEUE_NAME}',
            'explanation': 'Creates a second queue representing an independent audit consumer.',
        },
        {
            'key': 'set-orders-queue-policy',
            'title': 'Authorize delivery to the processing queue',
            'command': 'aws sqs set-queue-attributes --queue-url <orders-queue-url> --attributes file://orders-queue-policy.json',
            'explanation': 'Allows only the lab SNS topic to call SendMessage on the processing queue.',
            'artifact_label': 'orders-queue-policy.json',
            'artifact': json.dumps({
                'Policy': json.dumps({
                    'Version': '2012-10-17',
                    'Statement': [{
                        'Sid': 'AllowOrderTopicDelivery',
                        'Effect': 'Allow',
                        'Principal': {'Service': 'sns.amazonaws.com'},
                        'Action': 'sqs:SendMessage',
                        'Resource': SNS_FANOUT_ORDERS_QUEUE_ARN,
                        'Condition': {
                            'ArnEquals': {'aws:SourceArn': SNS_FANOUT_TOPIC_ARN},
                        },
                    }],
                }),
            }, indent=2),
        },
        {
            'key': 'set-audit-queue-policy',
            'title': 'Authorize delivery to the audit queue',
            'command': 'aws sqs set-queue-attributes --queue-url <audit-queue-url> --attributes file://audit-queue-policy.json',
            'explanation': 'Allows only the same SNS topic to call SendMessage on the audit queue.',
            'artifact_label': 'audit-queue-policy.json',
            'artifact': json.dumps({
                'Policy': json.dumps({
                    'Version': '2012-10-17',
                    'Statement': [{
                        'Sid': 'AllowOrderTopicDelivery',
                        'Effect': 'Allow',
                        'Principal': {'Service': 'sns.amazonaws.com'},
                        'Action': 'sqs:SendMessage',
                        'Resource': SNS_FANOUT_AUDIT_QUEUE_ARN,
                        'Condition': {
                            'ArnEquals': {'aws:SourceArn': SNS_FANOUT_TOPIC_ARN},
                        },
                    }],
                }),
            }, indent=2),
        },
        {
            'key': 'subscribe-orders-queue',
            'title': 'Subscribe the processing queue',
            'command': f'aws sns subscribe --topic-arn {SNS_FANOUT_TOPIC_ARN} --protocol sqs --notification-endpoint {SNS_FANOUT_ORDERS_QUEUE_ARN} --attributes RawMessageDelivery=true --return-subscription-arn',
            'explanation': 'Creates an immediately confirmed SQS subscription with raw message delivery.',
        },
        {
            'key': 'subscribe-audit-queue',
            'title': 'Subscribe the audit queue',
            'command': f'aws sns subscribe --topic-arn {SNS_FANOUT_TOPIC_ARN} --protocol sqs --notification-endpoint {SNS_FANOUT_AUDIT_QUEUE_ARN} --attributes RawMessageDelivery=true --return-subscription-arn',
            'explanation': 'Adds the second queue as an independent subscriber to the same topic.',
        },
        {
            'key': 'list-subscriptions',
            'title': 'Inspect the fan-out topology',
            'command': f'aws sns list-subscriptions-by-topic --topic-arn {SNS_FANOUT_TOPIC_ARN}',
            'explanation': 'Verifies both confirmed SQS endpoints and their raw-delivery settings.',
        },
        {
            'key': 'publish-message',
            'title': 'Publish one order event',
            'command': f'aws sns publish --topic-arn {SNS_FANOUT_TOPIC_ARN} --message file://order-created.json --subject "Order created" --message-attributes file://message-attributes.json',
            'explanation': 'Publishes once to SNS. The topic performs delivery independently to both queues.',
            'artifact_label': 'order-created.json',
            'artifact': json.dumps(SNS_FANOUT_MESSAGE, indent=2),
            'secondary_artifact_label': 'message-attributes.json',
            'secondary_artifact': json.dumps(SNS_FANOUT_MESSAGE_ATTRIBUTES, indent=2),
        },
        {
            'key': 'receive-orders-copy',
            'title': 'Receive the processing copy',
            'command': 'aws sqs receive-message --queue-url <orders-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All',
            'explanation': 'Verifies the exact raw JSON body and SNS message attributes in the processing queue.',
        },
        {
            'key': 'receive-audit-copy',
            'title': 'Receive the audit copy',
            'command': 'aws sqs receive-message --queue-url <audit-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All',
            'explanation': 'Verifies the same event arrived independently in the audit queue.',
        },
    ],
}

SNS_FILTER_POLICIES_LAB = {
    'service': 'sns',
    'key': 'filter-policies',
    'title': 'Route selected SNS messages with subscription filters',
    'description': 'Attach different message-attribute filters to two SQS subscriptions and prove that each queue receives only its matching event.',
    'steps': [
        {
            'key': 'create-topic',
            'title': 'Create the filtered-events topic',
            'command': f'aws sns create-topic --name {SNS_FILTER_TOPIC_NAME}',
            'explanation': 'Creates the shared publisher endpoint for both event types.',
        },
        {
            'key': 'create-created-queue',
            'title': 'Create the created-events queue',
            'command': f'aws sqs create-queue --queue-name {SNS_FILTER_CREATED_QUEUE_NAME}',
            'explanation': 'Creates the consumer queue that should receive only order.created events.',
        },
        {
            'key': 'create-priority-queue',
            'title': 'Create the priority queue',
            'command': f'aws sqs create-queue --queue-name {SNS_FILTER_PRIORITY_QUEUE_NAME}',
            'explanation': 'Creates the consumer queue that should receive only high-priority events.',
        },
        {
            'key': 'set-created-queue-policy',
            'title': 'Authorize the created-events queue',
            'command': 'aws sqs set-queue-attributes --queue-url <created-queue-url> --attributes file://created-queue-policy.json',
            'explanation': 'Allows only the filtered-events topic to send messages to this queue.',
        },
        {
            'key': 'set-priority-queue-policy',
            'title': 'Authorize the priority queue',
            'command': 'aws sqs set-queue-attributes --queue-url <priority-queue-url> --attributes file://priority-queue-policy.json',
            'explanation': 'Allows only the same topic to send messages to the priority queue.',
        },
        {
            'key': 'subscribe-created-filter',
            'title': 'Filter for order.created',
            'command': f'aws sns subscribe --topic-arn {SNS_FILTER_TOPIC_ARN} --protocol sqs --notification-endpoint {SNS_FILTER_CREATED_QUEUE_ARN} --attributes file://created-filter.json --return-subscription-arn',
            'explanation': 'Creates a raw-delivery subscription with FilterPolicyScope=MessageAttributes.',
            'artifact_label': 'created-filter.json',
            'artifact': json.dumps({
                'RawMessageDelivery': 'true',
                'FilterPolicyScope': 'MessageAttributes',
                'FilterPolicy': json.dumps(SNS_FILTER_CREATED_POLICY),
            }, indent=2),
        },
        {
            'key': 'subscribe-priority-filter',
            'title': 'Filter for high priority',
            'command': f'aws sns subscribe --topic-arn {SNS_FILTER_TOPIC_ARN} --protocol sqs --notification-endpoint {SNS_FILTER_PRIORITY_QUEUE_ARN} --attributes file://priority-filter.json --return-subscription-arn',
            'explanation': 'Creates a second subscription that matches Priority=high regardless of event type.',
            'artifact_label': 'priority-filter.json',
            'artifact': json.dumps({
                'RawMessageDelivery': 'true',
                'FilterPolicyScope': 'MessageAttributes',
                'FilterPolicy': json.dumps(SNS_FILTER_PRIORITY_POLICY),
            }, indent=2),
        },
        {
            'key': 'inspect-filter-policies',
            'title': 'Inspect both filter policies',
            'command': 'aws sns get-subscription-attributes --subscription-arn <subscription-arn>',
            'explanation': 'Verifies both confirmed subscriptions, raw delivery, message-attribute scope, and exact filter documents.',
        },
        {
            'key': 'publish-created-event',
            'title': 'Publish a normal created event',
            'command': f'aws sns publish --topic-arn {SNS_FILTER_TOPIC_ARN} --message file://created-event.json --message-attributes file://created-attributes.json',
            'explanation': 'Matches EventType=order.created but not Priority=high.',
            'artifact_label': 'created-event.json',
            'artifact': json.dumps(SNS_FILTER_CREATED_MESSAGE, indent=2),
            'secondary_artifact_label': 'created-attributes.json',
            'secondary_artifact': json.dumps(SNS_FILTER_CREATED_ATTRIBUTES, indent=2),
        },
        {
            'key': 'publish-priority-event',
            'title': 'Publish a high-priority cancellation',
            'command': f'aws sns publish --topic-arn {SNS_FILTER_TOPIC_ARN} --message file://priority-event.json --message-attributes file://priority-attributes.json',
            'explanation': 'Matches Priority=high but not EventType=order.created.',
            'artifact_label': 'priority-event.json',
            'artifact': json.dumps(SNS_FILTER_PRIORITY_MESSAGE, indent=2),
            'secondary_artifact_label': 'priority-attributes.json',
            'secondary_artifact': json.dumps(SNS_FILTER_PRIORITY_ATTRIBUTES, indent=2),
        },
        {
            'key': 'receive-created-route',
            'title': 'Verify the created-event route',
            'command': 'aws sqs receive-message --queue-url <created-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All',
            'explanation': 'Requires exactly the created event and proves the high-priority cancellation was excluded.',
        },
        {
            'key': 'receive-priority-route',
            'title': 'Verify the priority route',
            'command': 'aws sqs receive-message --queue-url <priority-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All',
            'explanation': 'Requires exactly the cancellation event and proves the normal created event was excluded.',
        },
    ],
}


def _sns_fanout_queue_policy(queue_arn: str) -> dict[str, Any]:
    return {
        'Version': '2012-10-17',
        'Statement': [{
            'Sid': 'AllowOrderTopicDelivery',
            'Effect': 'Allow',
            'Principal': {'Service': 'sns.amazonaws.com'},
            'Action': 'sqs:SendMessage',
            'Resource': queue_arn,
            'Condition': {
                'ArnEquals': {'aws:SourceArn': SNS_FANOUT_TOPIC_ARN},
            },
        }],
    }


def _verify_sns_fanout_topic() -> dict[str, Any]:
    try:
        response = _sns_client().get_topic_attributes(
            TopicArn=SNS_FANOUT_TOPIC_ARN,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Attributes', {}).get('TopicArn') == SNS_FANOUT_TOPIC_ARN:
        return {
            'status': 'passed',
            'message': 'The order-events SNS topic exists.',
            'resource': {'topic_arn': SNS_FANOUT_TOPIC_ARN},
        }
    return {'status': 'failed', 'message': 'The SNS topic does not exist.'}


def _verify_sns_fanout_queue(
    queue_name: str,
    queue_arn: str,
) -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(queue_name)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Attributes', {}).get('QueueArn') == queue_arn:
        return {
            'status': 'passed',
            'message': f'Queue {queue_name} exists with the expected ARN.',
            'resource': {'queue_url': queue_url, 'queue_arn': queue_arn},
        }
    return {'status': 'failed', 'message': f'Queue {queue_name} is not ready.'}


def _verify_sns_fanout_queue_policy(
    queue_name: str,
    queue_arn: str,
) -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(queue_name)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['Policy'],
        )
        policy_text = response.get('Attributes', {}).get('Policy', '')
        policy = json.loads(policy_text) if policy_text else {}
    except (ClientError, json.JSONDecodeError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    if policy == _sns_fanout_queue_policy(queue_arn):
        return {
            'status': 'passed',
            'message': f'Queue {queue_name} authorizes only the lab SNS topic.',
            'resource': {'queue_url': queue_url, 'policy': policy},
        }
    return {
        'status': 'failed',
        'message': f'Queue {queue_name} does not have the expected SNS policy.',
    }


def _sns_fanout_subscriptions() -> list[dict[str, Any]]:
    return _sns_client().list_subscriptions_by_topic(
        TopicArn=SNS_FANOUT_TOPIC_ARN,
    ).get('Subscriptions', [])


def _verify_sns_fanout_subscription(queue_arn: str) -> dict[str, Any]:
    try:
        subscription = next(
            (
                item for item in _sns_fanout_subscriptions()
                if item.get('Protocol') == 'sqs'
                and item.get('Endpoint') == queue_arn
                and item.get('SubscriptionArn') not in {None, 'PendingConfirmation'}
            ),
            None,
        )
        attributes = (
            _sns_client().get_subscription_attributes(
                SubscriptionArn=subscription['SubscriptionArn'],
            ).get('Attributes', {})
            if subscription
            else {}
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if subscription and attributes.get('RawMessageDelivery') == 'true':
        return {
            'status': 'passed',
            'message': 'The SQS endpoint is confirmed with raw message delivery.',
            'resource': {
                'subscription': _clean_response(subscription),
                'attributes': _clean_response(attributes),
            },
        }
    return {
        'status': 'failed',
        'message': 'The expected raw-delivery SQS subscription is not confirmed.',
    }


def _sns_fanout_message(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            message for message in messages
            if message.get('Body') == SNS_FANOUT_MESSAGE_TEXT
            and message.get('MessageAttributes', {}).get('EventType', {}).get(
                'StringValue'
            ) == 'order.created'
            and message.get('MessageAttributes', {}).get('Environment', {}).get(
                'StringValue'
            ) == 'lab'
        ),
        None,
    )


def _verify_sns_fanout_delivery(queue_name: str) -> dict[str, Any]:
    try:
        response = _sqs_client().receive_message(
            QueueUrl=_sqs_queue_url(queue_name),
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
            MessageAttributeNames=['All'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    message = _sns_fanout_message(response.get('Messages', []))
    if message:
        return {
            'status': 'passed',
            'message': f'Queue {queue_name} contains the raw order.created event.',
            'resource': _clean_response(message),
        }
    return {
        'status': 'failed',
        'message': f'Queue {queue_name} does not contain the expected event.',
    }


def _sns_filter_queue_policy(queue_arn: str) -> dict[str, Any]:
    return {
        'Version': '2012-10-17',
        'Statement': [{
            'Sid': 'AllowFilteredTopicDelivery',
            'Effect': 'Allow',
            'Principal': {'Service': 'sns.amazonaws.com'},
            'Action': 'sqs:SendMessage',
            'Resource': queue_arn,
            'Condition': {
                'ArnEquals': {'aws:SourceArn': SNS_FILTER_TOPIC_ARN},
            },
        }],
    }


def _verify_sns_filter_topic() -> dict[str, Any]:
    try:
        response = _sns_client().get_topic_attributes(
            TopicArn=SNS_FILTER_TOPIC_ARN,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Attributes', {}).get('TopicArn') == SNS_FILTER_TOPIC_ARN:
        return {
            'status': 'passed',
            'message': 'The filtered-events SNS topic exists.',
            'resource': {'topic_arn': SNS_FILTER_TOPIC_ARN},
        }
    return {'status': 'failed', 'message': 'The filtered-events topic is missing.'}


def _verify_sns_filter_queue(
    queue_name: str,
    queue_arn: str,
) -> dict[str, Any]:
    return _verify_sns_fanout_queue(queue_name, queue_arn)


def _verify_sns_filter_queue_policy(
    queue_name: str,
    queue_arn: str,
) -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(queue_name)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['Policy'],
        )
        policy_text = response.get('Attributes', {}).get('Policy', '')
        policy = json.loads(policy_text) if policy_text else {}
    except (ClientError, json.JSONDecodeError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    if policy == _sns_filter_queue_policy(queue_arn):
        return {
            'status': 'passed',
            'message': f'Queue {queue_name} authorizes only the filtered-events topic.',
            'resource': {'queue_url': queue_url, 'policy': policy},
        }
    return {'status': 'failed', 'message': f'Queue policy for {queue_name} is incorrect.'}


def _sns_filter_subscriptions() -> list[dict[str, Any]]:
    return _sns_client().list_subscriptions_by_topic(
        TopicArn=SNS_FILTER_TOPIC_ARN,
    ).get('Subscriptions', [])


def _verify_sns_filter_subscription(
    queue_arn: str,
    filter_policy: dict[str, Any],
) -> dict[str, Any]:
    try:
        subscription = next(
            (
                item for item in _sns_filter_subscriptions()
                if item.get('Protocol') == 'sqs'
                and item.get('Endpoint') == queue_arn
                and item.get('SubscriptionArn') not in {None, 'PendingConfirmation'}
            ),
            None,
        )
        attributes = (
            _sns_client().get_subscription_attributes(
                SubscriptionArn=subscription['SubscriptionArn'],
            ).get('Attributes', {})
            if subscription
            else {}
        )
        stored_filter = json.loads(attributes.get('FilterPolicy', '{}'))
    except (ClientError, json.JSONDecodeError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        subscription
        and attributes.get('RawMessageDelivery') == 'true'
        and attributes.get('FilterPolicyScope') == 'MessageAttributes'
        and stored_filter == filter_policy
    ):
        return {
            'status': 'passed',
            'message': 'The confirmed subscription has the expected message-attribute filter.',
            'resource': {
                'subscription': _clean_response(subscription),
                'attributes': _clean_response(attributes),
            },
        }
    return {'status': 'failed', 'message': 'The expected subscription filter is not active.'}


def _verify_sns_filter_route(
    queue_name: str,
    expected_body: str,
    excluded_body: str,
) -> dict[str, Any]:
    try:
        response = _sqs_client().receive_message(
            QueueUrl=_sqs_queue_url(queue_name),
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
            MessageAttributeNames=['All'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    messages = response.get('Messages', [])
    bodies = [message.get('Body') for message in messages]
    if len(messages) == 1 and bodies == [expected_body] and excluded_body not in bodies:
        return {
            'status': 'passed',
            'message': f'Queue {queue_name} contains only its matching event.',
            'resource': {'messages': _clean_response(messages)},
        }
    return {
        'status': 'failed',
        'message': f'Queue {queue_name} does not contain exactly its matching event.',
    }


def _sns_fanout_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'sns',
        'lab': 'sqs-fanout',
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


def _run_sns_fanout_create_topic() -> dict[str, Any]:
    command = f'aws sns create-topic --name {SNS_FANOUT_TOPIC_NAME}'
    started = time.perf_counter()
    response = _sns_client().create_topic(Name=SNS_FANOUT_TOPIC_NAME)
    verification = _verify_sns_fanout_topic()
    return _sns_fanout_step_result(
        'create-topic',
        command,
        response,
        verification,
        started,
    )


def _run_sns_fanout_create_queue(
    queue_name: str,
    queue_arn: str,
    step_key: str,
) -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {queue_name}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=queue_name)
    verification = _verify_sns_fanout_queue(queue_name, queue_arn)
    return _sns_fanout_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sns_fanout_set_queue_policy(
    queue_name: str,
    queue_arn: str,
    step_key: str,
    queue_url_label: str,
    artifact_name: str,
) -> dict[str, Any]:
    command = (
        f'aws sqs set-queue-attributes --queue-url <{queue_url_label}> '
        f'--attributes file://{artifact_name}'
    )
    started = time.perf_counter()
    response = _sqs_client().set_queue_attributes(
        QueueUrl=_sqs_queue_url(queue_name),
        Attributes={'Policy': json.dumps(_sns_fanout_queue_policy(queue_arn))},
    )
    verification = _verify_sns_fanout_queue_policy(queue_name, queue_arn)
    return _sns_fanout_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sns_fanout_subscribe(
    queue_arn: str,
    step_key: str,
) -> dict[str, Any]:
    command = (
        f'aws sns subscribe --topic-arn {SNS_FANOUT_TOPIC_ARN} '
        f'--protocol sqs --notification-endpoint {queue_arn} '
        '--attributes RawMessageDelivery=true --return-subscription-arn'
    )
    started = time.perf_counter()
    response = _sns_client().subscribe(
        TopicArn=SNS_FANOUT_TOPIC_ARN,
        Protocol='sqs',
        Endpoint=queue_arn,
        Attributes={'RawMessageDelivery': 'true'},
        ReturnSubscriptionArn=True,
    )
    verification = _verify_sns_fanout_subscription(queue_arn)
    return _sns_fanout_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sns_fanout_list_subscriptions() -> dict[str, Any]:
    command = (
        'aws sns list-subscriptions-by-topic '
        f'--topic-arn {SNS_FANOUT_TOPIC_ARN}'
    )
    started = time.perf_counter()
    response = _sns_client().list_subscriptions_by_topic(
        TopicArn=SNS_FANOUT_TOPIC_ARN,
    )
    orders = _verify_sns_fanout_subscription(SNS_FANOUT_ORDERS_QUEUE_ARN)
    audit = _verify_sns_fanout_subscription(SNS_FANOUT_AUDIT_QUEUE_ARN)
    verified = (
        orders.get('status') == 'passed'
        and audit.get('status') == 'passed'
    )
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'Both SQS queues are confirmed raw-delivery subscribers.'
            if verified
            else 'Both expected subscriptions are not ready.'
        ),
        'resource': _clean_response(response),
    }
    return _sns_fanout_step_result(
        'list-subscriptions',
        command,
        response,
        verification,
        started,
    )


def _run_sns_fanout_publish() -> dict[str, Any]:
    command = (
        f'aws sns publish --topic-arn {SNS_FANOUT_TOPIC_ARN} '
        '--message file://order-created.json --subject "Order created" '
        '--message-attributes file://message-attributes.json'
    )
    started = time.perf_counter()
    response = _sns_client().publish(
        TopicArn=SNS_FANOUT_TOPIC_ARN,
        Message=SNS_FANOUT_MESSAGE_TEXT,
        Subject='Order created',
        MessageAttributes=SNS_FANOUT_MESSAGE_ATTRIBUTES,
    )
    orders = _verify_sns_fanout_delivery(SNS_FANOUT_ORDERS_QUEUE_NAME)
    audit = _verify_sns_fanout_delivery(SNS_FANOUT_AUDIT_QUEUE_NAME)
    verified = (
        orders.get('status') == 'passed'
        and audit.get('status') == 'passed'
    )
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'One SNS publish delivered a copy to both SQS queues.'
            if verified
            else 'The event has not reached both queues.'
        ),
        'resource': {
            'publish': _clean_response(response),
            'orders_delivery': orders.get('resource'),
            'audit_delivery': audit.get('resource'),
        },
    }
    return _sns_fanout_step_result(
        'publish-message',
        command,
        response,
        verification,
        started,
    )


def _run_sns_fanout_receive(
    queue_name: str,
    step_key: str,
    queue_url_label: str,
) -> dict[str, Any]:
    command = (
        f'aws sqs receive-message --queue-url <{queue_url_label}> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 1 --message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_client().receive_message(
        QueueUrl=_sqs_queue_url(queue_name),
        MaxNumberOfMessages=10,
        VisibilityTimeout=0,
        WaitTimeSeconds=1,
        MessageAttributeNames=['All'],
    )
    message = _sns_fanout_message(response.get('Messages', []))
    verification = {
        'status': 'passed' if message else 'failed',
        'message': (
            f'Queue {queue_name} received the raw order event and attributes.'
            if message
            else f'Queue {queue_name} did not return the expected event.'
        ),
        'resource': _clean_response(message) if message else None,
    }
    return _sns_fanout_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _sns_filter_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'sns',
        'lab': 'filter-policies',
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


def _run_sns_filter_create_topic() -> dict[str, Any]:
    command = f'aws sns create-topic --name {SNS_FILTER_TOPIC_NAME}'
    started = time.perf_counter()
    response = _sns_client().create_topic(Name=SNS_FILTER_TOPIC_NAME)
    verification = _verify_sns_filter_topic()
    return _sns_filter_step_result(
        'create-topic',
        command,
        response,
        verification,
        started,
    )


def _run_sns_filter_create_queue(
    queue_name: str,
    queue_arn: str,
    step_key: str,
) -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {queue_name}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=queue_name)
    verification = _verify_sns_filter_queue(queue_name, queue_arn)
    return _sns_filter_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sns_filter_set_queue_policy(
    queue_name: str,
    queue_arn: str,
    step_key: str,
    queue_url_label: str,
    artifact_name: str,
) -> dict[str, Any]:
    command = (
        f'aws sqs set-queue-attributes --queue-url <{queue_url_label}> '
        f'--attributes file://{artifact_name}'
    )
    started = time.perf_counter()
    response = _sqs_client().set_queue_attributes(
        QueueUrl=_sqs_queue_url(queue_name),
        Attributes={'Policy': json.dumps(_sns_filter_queue_policy(queue_arn))},
    )
    verification = _verify_sns_filter_queue_policy(queue_name, queue_arn)
    return _sns_filter_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sns_filter_subscribe(
    queue_arn: str,
    filter_policy: dict[str, Any],
    step_key: str,
    artifact_name: str,
) -> dict[str, Any]:
    command = (
        f'aws sns subscribe --topic-arn {SNS_FILTER_TOPIC_ARN} '
        f'--protocol sqs --notification-endpoint {queue_arn} '
        f'--attributes file://{artifact_name} --return-subscription-arn'
    )
    attributes = {
        'RawMessageDelivery': 'true',
        'FilterPolicyScope': 'MessageAttributes',
        'FilterPolicy': json.dumps(filter_policy),
    }
    started = time.perf_counter()
    response = _sns_client().subscribe(
        TopicArn=SNS_FILTER_TOPIC_ARN,
        Protocol='sqs',
        Endpoint=queue_arn,
        Attributes=attributes,
        ReturnSubscriptionArn=True,
    )
    verification = _verify_sns_filter_subscription(queue_arn, filter_policy)
    return _sns_filter_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sns_filter_inspect() -> dict[str, Any]:
    command = (
        'aws sns get-subscription-attributes '
        '--subscription-arn <subscription-arn>'
    )
    started = time.perf_counter()
    subscriptions = _sns_filter_subscriptions()
    details = []
    for subscription in subscriptions:
        subscription_arn = subscription.get('SubscriptionArn')
        if subscription_arn and subscription_arn != 'PendingConfirmation':
            details.append(
                _sns_client().get_subscription_attributes(
                    SubscriptionArn=subscription_arn,
                ).get('Attributes', {})
            )
    created = _verify_sns_filter_subscription(
        SNS_FILTER_CREATED_QUEUE_ARN,
        SNS_FILTER_CREATED_POLICY,
    )
    priority = _verify_sns_filter_subscription(
        SNS_FILTER_PRIORITY_QUEUE_ARN,
        SNS_FILTER_PRIORITY_POLICY,
    )
    verified = (
        created.get('status') == 'passed'
        and priority.get('status') == 'passed'
    )
    response = {'Subscriptions': details}
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'Both subscriptions expose the expected message-attribute filters.'
            if verified
            else 'Both subscription filters are not ready.'
        ),
        'resource': _clean_response(response),
    }
    return _sns_filter_step_result(
        'inspect-filter-policies',
        command,
        response,
        verification,
        started,
    )


def _run_sns_filter_publish(
    message_text: str,
    message_attributes: dict[str, Any],
    step_key: str,
    message_artifact: str,
    attributes_artifact: str,
) -> dict[str, Any]:
    command = (
        f'aws sns publish --topic-arn {SNS_FILTER_TOPIC_ARN} '
        f'--message file://{message_artifact} '
        f'--message-attributes file://{attributes_artifact}'
    )
    started = time.perf_counter()
    response = _sns_client().publish(
        TopicArn=SNS_FILTER_TOPIC_ARN,
        Message=message_text,
        MessageAttributes=message_attributes,
    )
    verification = {
        'status': 'passed' if response.get('MessageId') else 'failed',
        'message': (
            'SNS accepted the attributed event for filtered delivery.'
            if response.get('MessageId')
            else 'SNS did not return a message ID.'
        ),
        'resource': _clean_response(response),
    }
    return _sns_filter_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sns_filter_receive(
    queue_name: str,
    expected_body: str,
    excluded_body: str,
    step_key: str,
    queue_url_label: str,
) -> dict[str, Any]:
    command = (
        f'aws sqs receive-message --queue-url <{queue_url_label}> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 1 --message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_client().receive_message(
        QueueUrl=_sqs_queue_url(queue_name),
        MaxNumberOfMessages=10,
        VisibilityTimeout=0,
        WaitTimeSeconds=1,
        MessageAttributeNames=['All'],
    )
    messages = response.get('Messages', [])
    bodies = [message.get('Body') for message in messages]
    verified = (
        len(messages) == 1
        and bodies == [expected_body]
        and excluded_body not in bodies
    )
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            f'Queue {queue_name} contains only the event selected by its filter.'
            if verified
            else f'Queue {queue_name} contains an unexpected routing result.'
        ),
        'resource': {'messages': _clean_response(messages)},
    }
    return _sns_filter_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _reset_sns_sqs_fanout() -> dict[str, Any]:
    command = (
        f'aws sns list-subscriptions-by-topic --topic-arn {SNS_FANOUT_TOPIC_ARN}\n'
        'aws sns unsubscribe --subscription-arn <subscription-arn>\n'
        f'aws sns delete-topic --topic-arn {SNS_FANOUT_TOPIC_ARN}\n'
        'aws sqs delete-queue --queue-url <orders-queue-url>\n'
        'aws sqs delete-queue --queue-url <audit-queue-url>'
    )
    started = time.perf_counter()
    unsubscribed = 0
    deleted_topic = False
    deleted_queues: list[str] = []
    try:
        for subscription in _sns_fanout_subscriptions():
            subscription_arn = subscription.get('SubscriptionArn')
            if subscription_arn and subscription_arn != 'PendingConfirmation':
                _sns_client().unsubscribe(SubscriptionArn=subscription_arn)
                unsubscribed += 1
        _sns_client().delete_topic(TopicArn=SNS_FANOUT_TOPIC_ARN)
        deleted_topic = True
    except ClientError as exc:
        if _error_code(exc) not in {'NotFound', 'NotFoundException'}:
            raise

    for queue_name in (
        SNS_FANOUT_ORDERS_QUEUE_NAME,
        SNS_FANOUT_AUDIT_QUEUE_NAME,
    ):
        try:
            queue_url = _sqs_queue_url(queue_name)
            _sqs_client().delete_queue(QueueUrl=queue_url)
            deleted_queues.append(queue_name)
        except ClientError as exc:
            if _error_code(exc) not in {
                'AWS.SimpleQueueService.NonExistentQueue',
                'QueueDoesNotExist',
            }:
                raise
    output = {
        'unsubscribed': unsubscribed,
        'deleted_topic': deleted_topic,
        'deleted_queues': deleted_queues,
    }
    return {
        'service': 'sns',
        'lab': 'sqs-fanout',
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
            'message': 'SNS fan-out subscriptions, topic, and both SQS queues were removed.',
        },
    }


def _reset_sns_filter_policies() -> dict[str, Any]:
    command = (
        f'aws sns list-subscriptions-by-topic --topic-arn {SNS_FILTER_TOPIC_ARN}\n'
        'aws sns unsubscribe --subscription-arn <subscription-arn>\n'
        f'aws sns delete-topic --topic-arn {SNS_FILTER_TOPIC_ARN}\n'
        'aws sqs delete-queue --queue-url <created-queue-url>\n'
        'aws sqs delete-queue --queue-url <priority-queue-url>'
    )
    started = time.perf_counter()
    unsubscribed = 0
    deleted_topic = False
    deleted_queues: list[str] = []
    try:
        for subscription in _sns_filter_subscriptions():
            subscription_arn = subscription.get('SubscriptionArn')
            if subscription_arn and subscription_arn != 'PendingConfirmation':
                _sns_client().unsubscribe(SubscriptionArn=subscription_arn)
                unsubscribed += 1
        _sns_client().delete_topic(TopicArn=SNS_FILTER_TOPIC_ARN)
        deleted_topic = True
    except ClientError as exc:
        if _error_code(exc) not in {'NotFound', 'NotFoundException'}:
            raise
    for queue_name in (
        SNS_FILTER_CREATED_QUEUE_NAME,
        SNS_FILTER_PRIORITY_QUEUE_NAME,
    ):
        try:
            _sqs_client().delete_queue(QueueUrl=_sqs_queue_url(queue_name))
            deleted_queues.append(queue_name)
        except ClientError as exc:
            if _error_code(exc) not in {
                'AWS.SimpleQueueService.NonExistentQueue',
                'QueueDoesNotExist',
            }:
                raise
    output = {
        'unsubscribed': unsubscribed,
        'deleted_topic': deleted_topic,
        'deleted_queues': deleted_queues,
    }
    return {
        'service': 'sns',
        'lab': 'filter-policies',
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
            'message': 'Filtered subscriptions, topic, and both queues were removed.',
        },
    }


LABS = [SNS_SQS_FANOUT_LAB, SNS_FILTER_POLICIES_LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'sns' and lab_key == 'sqs-fanout':
        runners = {
            'create-topic': _run_sns_fanout_create_topic,
            'create-orders-queue': lambda: _run_sns_fanout_create_queue(
                SNS_FANOUT_ORDERS_QUEUE_NAME,
                SNS_FANOUT_ORDERS_QUEUE_ARN,
                'create-orders-queue',
            ),
            'create-audit-queue': lambda: _run_sns_fanout_create_queue(
                SNS_FANOUT_AUDIT_QUEUE_NAME,
                SNS_FANOUT_AUDIT_QUEUE_ARN,
                'create-audit-queue',
            ),
            'set-orders-queue-policy': lambda: _run_sns_fanout_set_queue_policy(
                SNS_FANOUT_ORDERS_QUEUE_NAME,
                SNS_FANOUT_ORDERS_QUEUE_ARN,
                'set-orders-queue-policy',
                'orders-queue-url',
                'orders-queue-policy.json',
            ),
            'set-audit-queue-policy': lambda: _run_sns_fanout_set_queue_policy(
                SNS_FANOUT_AUDIT_QUEUE_NAME,
                SNS_FANOUT_AUDIT_QUEUE_ARN,
                'set-audit-queue-policy',
                'audit-queue-url',
                'audit-queue-policy.json',
            ),
            'subscribe-orders-queue': lambda: _run_sns_fanout_subscribe(
                SNS_FANOUT_ORDERS_QUEUE_ARN,
                'subscribe-orders-queue',
            ),
            'subscribe-audit-queue': lambda: _run_sns_fanout_subscribe(
                SNS_FANOUT_AUDIT_QUEUE_ARN,
                'subscribe-audit-queue',
            ),
            'list-subscriptions': _run_sns_fanout_list_subscriptions,
            'publish-message': _run_sns_fanout_publish,
            'receive-orders-copy': lambda: _run_sns_fanout_receive(
                SNS_FANOUT_ORDERS_QUEUE_NAME,
                'receive-orders-copy',
                'orders-queue-url',
            ),
            'receive-audit-copy': lambda: _run_sns_fanout_receive(
                SNS_FANOUT_AUDIT_QUEUE_NAME,
                'receive-audit-copy',
                'audit-queue-url',
            ),
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sns' and lab_key == 'filter-policies':
        runners = {
            'create-topic': _run_sns_filter_create_topic,
            'create-created-queue': lambda: _run_sns_filter_create_queue(
                SNS_FILTER_CREATED_QUEUE_NAME,
                SNS_FILTER_CREATED_QUEUE_ARN,
                'create-created-queue',
            ),
            'create-priority-queue': lambda: _run_sns_filter_create_queue(
                SNS_FILTER_PRIORITY_QUEUE_NAME,
                SNS_FILTER_PRIORITY_QUEUE_ARN,
                'create-priority-queue',
            ),
            'set-created-queue-policy': lambda: _run_sns_filter_set_queue_policy(
                SNS_FILTER_CREATED_QUEUE_NAME,
                SNS_FILTER_CREATED_QUEUE_ARN,
                'set-created-queue-policy',
                'created-queue-url',
                'created-queue-policy.json',
            ),
            'set-priority-queue-policy': lambda: _run_sns_filter_set_queue_policy(
                SNS_FILTER_PRIORITY_QUEUE_NAME,
                SNS_FILTER_PRIORITY_QUEUE_ARN,
                'set-priority-queue-policy',
                'priority-queue-url',
                'priority-queue-policy.json',
            ),
            'subscribe-created-filter': lambda: _run_sns_filter_subscribe(
                SNS_FILTER_CREATED_QUEUE_ARN,
                SNS_FILTER_CREATED_POLICY,
                'subscribe-created-filter',
                'created-filter.json',
            ),
            'subscribe-priority-filter': lambda: _run_sns_filter_subscribe(
                SNS_FILTER_PRIORITY_QUEUE_ARN,
                SNS_FILTER_PRIORITY_POLICY,
                'subscribe-priority-filter',
                'priority-filter.json',
            ),
            'inspect-filter-policies': _run_sns_filter_inspect,
            'publish-created-event': lambda: _run_sns_filter_publish(
                SNS_FILTER_CREATED_MESSAGE_TEXT,
                SNS_FILTER_CREATED_ATTRIBUTES,
                'publish-created-event',
                'created-event.json',
                'created-attributes.json',
            ),
            'publish-priority-event': lambda: _run_sns_filter_publish(
                SNS_FILTER_PRIORITY_MESSAGE_TEXT,
                SNS_FILTER_PRIORITY_ATTRIBUTES,
                'publish-priority-event',
                'priority-event.json',
                'priority-attributes.json',
            ),
            'receive-created-route': lambda: _run_sns_filter_receive(
                SNS_FILTER_CREATED_QUEUE_NAME,
                SNS_FILTER_CREATED_MESSAGE_TEXT,
                SNS_FILTER_PRIORITY_MESSAGE_TEXT,
                'receive-created-route',
                'created-queue-url',
            ),
            'receive-priority-route': lambda: _run_sns_filter_receive(
                SNS_FILTER_PRIORITY_QUEUE_NAME,
                SNS_FILTER_PRIORITY_MESSAGE_TEXT,
                SNS_FILTER_CREATED_MESSAGE_TEXT,
                'receive-priority-route',
                'priority-queue-url',
            ),
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'sns' and lab_key == 'sqs-fanout':
        topic_verification = _verify_sns_fanout_topic()
        orders_queue_verification = _verify_sns_fanout_queue(
            SNS_FANOUT_ORDERS_QUEUE_NAME,
            SNS_FANOUT_ORDERS_QUEUE_ARN,
        )
        audit_queue_verification = _verify_sns_fanout_queue(
            SNS_FANOUT_AUDIT_QUEUE_NAME,
            SNS_FANOUT_AUDIT_QUEUE_ARN,
        )
        orders_policy_verification = _verify_sns_fanout_queue_policy(
            SNS_FANOUT_ORDERS_QUEUE_NAME,
            SNS_FANOUT_ORDERS_QUEUE_ARN,
        )
        audit_policy_verification = _verify_sns_fanout_queue_policy(
            SNS_FANOUT_AUDIT_QUEUE_NAME,
            SNS_FANOUT_AUDIT_QUEUE_ARN,
        )
        orders_subscription_verification = _verify_sns_fanout_subscription(
            SNS_FANOUT_ORDERS_QUEUE_ARN,
        )
        audit_subscription_verification = _verify_sns_fanout_subscription(
            SNS_FANOUT_AUDIT_QUEUE_ARN,
        )
        orders_delivery_verification = _verify_sns_fanout_delivery(
            SNS_FANOUT_ORDERS_QUEUE_NAME,
        )
        audit_delivery_verification = _verify_sns_fanout_delivery(
            SNS_FANOUT_AUDIT_QUEUE_NAME,
        )
        topic_verified = topic_verification.get('status') == 'passed'
        orders_queue_verified = (
            orders_queue_verification.get('status') == 'passed'
        )
        audit_queue_verified = audit_queue_verification.get('status') == 'passed'
        orders_policy_verified = (
            orders_policy_verification.get('status') == 'passed'
        )
        audit_policy_verified = audit_policy_verification.get('status') == 'passed'
        orders_subscription_verified = (
            orders_subscription_verification.get('status') == 'passed'
        )
        audit_subscription_verified = (
            audit_subscription_verification.get('status') == 'passed'
        )
        orders_delivery_verified = (
            orders_delivery_verification.get('status') == 'passed'
        )
        audit_delivery_verified = audit_delivery_verification.get('status') == 'passed'
        subscriptions_verified = (
            orders_subscription_verified and audit_subscription_verified
        )
        deliveries_verified = (
            orders_delivery_verified and audit_delivery_verified
        )
        subscriptions_verification = {
            'status': 'passed',
            'message': 'Both SQS endpoints are confirmed raw-delivery subscribers.',
        }
        publish_verification = {
            'status': 'passed',
            'message': 'The published order event is available in both queues.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                topic_verified
                and orders_queue_verified
                and audit_queue_verified
                and orders_policy_verified
                and audit_policy_verified
                and subscriptions_verified
                and deliveries_verified
            ),
            'steps': {
                'create-topic': {
                    'verified': topic_verified,
                    'verification': topic_verification if topic_verified else None,
                },
                'create-orders-queue': {
                    'verified': orders_queue_verified,
                    'verification': (
                        orders_queue_verification
                        if orders_queue_verified
                        else None
                    ),
                },
                'create-audit-queue': {
                    'verified': audit_queue_verified,
                    'verification': (
                        audit_queue_verification if audit_queue_verified else None
                    ),
                },
                'set-orders-queue-policy': {
                    'verified': orders_policy_verified,
                    'verification': (
                        orders_policy_verification
                        if orders_policy_verified
                        else None
                    ),
                },
                'set-audit-queue-policy': {
                    'verified': audit_policy_verified,
                    'verification': (
                        audit_policy_verification
                        if audit_policy_verified
                        else None
                    ),
                },
                'subscribe-orders-queue': {
                    'verified': orders_subscription_verified,
                    'verification': (
                        orders_subscription_verification
                        if orders_subscription_verified
                        else None
                    ),
                },
                'subscribe-audit-queue': {
                    'verified': audit_subscription_verified,
                    'verification': (
                        audit_subscription_verification
                        if audit_subscription_verified
                        else None
                    ),
                },
                'list-subscriptions': {
                    'verified': subscriptions_verified,
                    'verification': (
                        subscriptions_verification
                        if subscriptions_verified
                        else None
                    ),
                },
                'publish-message': {
                    'verified': deliveries_verified,
                    'verification': (
                        publish_verification if deliveries_verified else None
                    ),
                },
                'receive-orders-copy': {
                    'verified': orders_delivery_verified,
                    'verification': (
                        orders_delivery_verification
                        if orders_delivery_verified
                        else None
                    ),
                },
                'receive-audit-copy': {
                    'verified': audit_delivery_verified,
                    'verification': (
                        audit_delivery_verification
                        if audit_delivery_verified
                        else None
                    ),
                },
            },
        }

    if service_key == 'sns' and lab_key == 'filter-policies':
        topic_verification = _verify_sns_filter_topic()
        created_queue_verification = _verify_sns_filter_queue(
            SNS_FILTER_CREATED_QUEUE_NAME,
            SNS_FILTER_CREATED_QUEUE_ARN,
        )
        priority_queue_verification = _verify_sns_filter_queue(
            SNS_FILTER_PRIORITY_QUEUE_NAME,
            SNS_FILTER_PRIORITY_QUEUE_ARN,
        )
        created_policy_verification = _verify_sns_filter_queue_policy(
            SNS_FILTER_CREATED_QUEUE_NAME,
            SNS_FILTER_CREATED_QUEUE_ARN,
        )
        priority_policy_verification = _verify_sns_filter_queue_policy(
            SNS_FILTER_PRIORITY_QUEUE_NAME,
            SNS_FILTER_PRIORITY_QUEUE_ARN,
        )
        created_subscription_verification = _verify_sns_filter_subscription(
            SNS_FILTER_CREATED_QUEUE_ARN,
            SNS_FILTER_CREATED_POLICY,
        )
        priority_subscription_verification = _verify_sns_filter_subscription(
            SNS_FILTER_PRIORITY_QUEUE_ARN,
            SNS_FILTER_PRIORITY_POLICY,
        )
        created_route_verification = _verify_sns_filter_route(
            SNS_FILTER_CREATED_QUEUE_NAME,
            SNS_FILTER_CREATED_MESSAGE_TEXT,
            SNS_FILTER_PRIORITY_MESSAGE_TEXT,
        )
        priority_route_verification = _verify_sns_filter_route(
            SNS_FILTER_PRIORITY_QUEUE_NAME,
            SNS_FILTER_PRIORITY_MESSAGE_TEXT,
            SNS_FILTER_CREATED_MESSAGE_TEXT,
        )
        verifications = {
            'topic': topic_verification,
            'created_queue': created_queue_verification,
            'priority_queue': priority_queue_verification,
            'created_policy': created_policy_verification,
            'priority_policy': priority_policy_verification,
            'created_subscription': created_subscription_verification,
            'priority_subscription': priority_subscription_verification,
            'created_route': created_route_verification,
            'priority_route': priority_route_verification,
        }
        verified = {
            key: value.get('status') == 'passed'
            for key, value in verifications.items()
        }
        filters_verified = (
            verified['created_subscription']
            and verified['priority_subscription']
        )
        routes_verified = (
            verified['created_route']
            and verified['priority_route']
        )
        filter_verification = {
            'status': 'passed',
            'message': 'Both message-attribute subscription filters are active.',
        }
        publish_verification = {
            'status': 'passed',
            'message': 'Both attributed events were routed to their matching queues.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': all(verified.values()),
            'steps': {
                'create-topic': {
                    'verified': verified['topic'],
                    'verification': topic_verification if verified['topic'] else None,
                },
                'create-created-queue': {
                    'verified': verified['created_queue'],
                    'verification': (
                        created_queue_verification
                        if verified['created_queue']
                        else None
                    ),
                },
                'create-priority-queue': {
                    'verified': verified['priority_queue'],
                    'verification': (
                        priority_queue_verification
                        if verified['priority_queue']
                        else None
                    ),
                },
                'set-created-queue-policy': {
                    'verified': verified['created_policy'],
                    'verification': (
                        created_policy_verification
                        if verified['created_policy']
                        else None
                    ),
                },
                'set-priority-queue-policy': {
                    'verified': verified['priority_policy'],
                    'verification': (
                        priority_policy_verification
                        if verified['priority_policy']
                        else None
                    ),
                },
                'subscribe-created-filter': {
                    'verified': verified['created_subscription'],
                    'verification': (
                        created_subscription_verification
                        if verified['created_subscription']
                        else None
                    ),
                },
                'subscribe-priority-filter': {
                    'verified': verified['priority_subscription'],
                    'verification': (
                        priority_subscription_verification
                        if verified['priority_subscription']
                        else None
                    ),
                },
                'inspect-filter-policies': {
                    'verified': filters_verified,
                    'verification': (
                        filter_verification if filters_verified else None
                    ),
                },
                'publish-created-event': {
                    'verified': routes_verified,
                    'verification': (
                        publish_verification if routes_verified else None
                    ),
                },
                'publish-priority-event': {
                    'verified': routes_verified,
                    'verification': (
                        publish_verification if routes_verified else None
                    ),
                },
                'receive-created-route': {
                    'verified': verified['created_route'],
                    'verification': (
                        created_route_verification
                        if verified['created_route']
                        else None
                    ),
                },
                'receive-priority-route': {
                    'verified': verified['priority_route'],
                    'verification': (
                        priority_route_verification
                        if verified['priority_route']
                        else None
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
    if service_key == 'sns' and lab_key == 'sqs-fanout':
        return _reset_sns_sqs_fanout()

    if service_key == 'sns' and lab_key == 'filter-policies':
        return _reset_sns_filter_policies()

    raise ValueError('Lab reset not found')
