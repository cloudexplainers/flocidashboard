"""SQS workflow labs: definitions, step runners, verifiers, status, and reset."""

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
    _error_code,
    _json_text,
    _sqs_client,
    _sqs_queue_url,
)


SQS_BASICS_QUEUE_NAME = 'lab-basics'
SQS_BASICS_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SQS_BASICS_QUEUE_NAME}'
)
SQS_MESSAGE_BODY = {
    'event': 'order.created',
    'order_id': 'FLOCI-1001',
}
SQS_MESSAGE_BODY_TEXT = json.dumps(SQS_MESSAGE_BODY, separators=(',', ':'))
SQS_MESSAGE_ATTRIBUTES = {
    'Lab': {
        'DataType': 'String',
        'StringValue': 'message-lifecycle',
    },
}
SQS_MESSAGE_DELETE_CACHE_KEY = 'floci-lab:sqs:message-lifecycle:deleted'
SQS_VISIBILITY_BODY = {
    'event': 'job.ready',
    'job_id': 'FLOCI-JOB-1001',
}
SQS_VISIBILITY_BODY_TEXT = json.dumps(SQS_VISIBILITY_BODY, separators=(',', ':'))
SQS_VISIBILITY_ATTRIBUTES = {
    'Lab': {
        'DataType': 'String',
        'StringValue': 'visibility-timeout',
    },
}
SQS_VISIBILITY_RECEIPT_CACHE_KEY = 'floci-lab:sqs:visibility-timeout:receipt'
SQS_VISIBILITY_EXTENDED_CACHE_KEY = 'floci-lab:sqs:visibility-timeout:extended'
SQS_VISIBILITY_HIDDEN_CACHE_KEY = 'floci-lab:sqs:visibility-timeout:hidden'
SQS_VISIBILITY_SHORTENED_CACHE_KEY = 'floci-lab:sqs:visibility-timeout:shortened'
SQS_VISIBILITY_RETURNED_CACHE_KEY = 'floci-lab:sqs:visibility-timeout:returned'
SQS_VISIBILITY_EXTENDED_SECONDS = 60
SQS_VISIBILITY_SHORT_SECONDS = 2
SQS_DELAYED_BODY = {
    'event': 'report.generate',
    'report_id': 'FLOCI-REPORT-1001',
}
SQS_DELAYED_BODY_TEXT = json.dumps(SQS_DELAYED_BODY, separators=(',', ':'))
SQS_DELAYED_ATTRIBUTES = {
    'Lab': {
        'DataType': 'String',
        'StringValue': 'delayed-message',
    },
}
SQS_DELAY_SECONDS = 10
SQS_DELAYED_OBSERVED_CACHE_KEY = 'floci-lab:sqs:delayed-message:observed'
SQS_DELAYED_RETURNED_CACHE_KEY = 'floci-lab:sqs:delayed-message:returned'
SQS_BATCH_MESSAGES = [
    {'event': 'task.created', 'task_id': f'FLOCI-TASK-{number}'}
    for number in range(1, 4)
]
SQS_BATCH_MESSAGE_TEXTS = [
    json.dumps(message, separators=(',', ':'))
    for message in SQS_BATCH_MESSAGES
]
SQS_BATCH_ATTRIBUTE_VALUE = 'batch-messages'
SQS_BATCH_DELETE_CACHE_KEY = 'floci-lab:sqs:batch-messages:deleted'
SQS_CONFIGURATION_ATTRIBUTES = {
    'VisibilityTimeout': '45',
    'MessageRetentionPeriod': '86400',
    'ReceiveMessageWaitTimeSeconds': '10',
}
SQS_DEFAULT_ATTRIBUTES = {
    'VisibilityTimeout': '30',
    'MessageRetentionPeriod': '345600',
    'ReceiveMessageWaitTimeSeconds': '0',
}
SQS_CONFIGURATION_TAGS = {
    'Environment': 'lab',
    'Purpose': 'training',
}
SQS_REDRIVE_SOURCE_QUEUE_NAME = 'lab-redrive-source'
SQS_REDRIVE_DLQ_NAME = 'lab-redrive-dlq'
SQS_REDRIVE_SOURCE_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SQS_REDRIVE_SOURCE_QUEUE_NAME}'
)
SQS_REDRIVE_DLQ_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SQS_REDRIVE_DLQ_NAME}'
)
SQS_REDRIVE_MAX_RECEIVE_COUNT = 2
SQS_REDRIVE_POLICY = {
    'deadLetterTargetArn': SQS_REDRIVE_DLQ_ARN,
    'maxReceiveCount': str(SQS_REDRIVE_MAX_RECEIVE_COUNT),
}
SQS_REDRIVE_BODY = {
    'event': 'payment.process',
    'payment_id': 'FLOCI-PAYMENT-1001',
    'simulate_failure': True,
}
SQS_REDRIVE_BODY_TEXT = json.dumps(SQS_REDRIVE_BODY, separators=(',', ':'))
SQS_REDRIVE_ATTRIBUTES = {
    'Lab': {
        'DataType': 'String',
        'StringValue': 'dead-letter-redrive',
    },
}
SQS_REDRIVE_FAILURE_ONE_CACHE_KEY = 'floci-lab:sqs:dead-letter-redrive:failure-one'
SQS_REDRIVE_FAILURE_TWO_CACHE_KEY = 'floci-lab:sqs:dead-letter-redrive:failure-two'
SQS_REDRIVE_TRIGGERED_CACHE_KEY = 'floci-lab:sqs:dead-letter-redrive:triggered'
SQS_REDRIVE_DLQ_OBSERVED_CACHE_KEY = 'floci-lab:sqs:dead-letter-redrive:dlq-observed'
SQS_REDRIVE_TASK_CACHE_KEY = 'floci-lab:sqs:dead-letter-redrive:task'
SQS_REDRIVE_RETURNED_CACHE_KEY = 'floci-lab:sqs:dead-letter-redrive:returned'
SQS_FIFO_QUEUE_NAME = 'lab-orders.fifo'
SQS_FIFO_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SQS_FIFO_QUEUE_NAME}'
)
SQS_FIFO_GROUP_ID = 'customer-FLOCI-1001'
SQS_FIFO_MESSAGES = [
    {
        'event': 'order.status',
        'order_id': 'FLOCI-ORDER-1001',
        'status': status,
        'step': step,
    }
    for step, status in enumerate(
        ('created', 'paid', 'fulfilled'),
        start=1,
    )
]
SQS_FIFO_MESSAGE_TEXTS = [
    json.dumps(message, separators=(',', ':'))
    for message in SQS_FIFO_MESSAGES
]
SQS_FIFO_DUPLICATE_BODY = {
    **SQS_FIFO_MESSAGES[0],
    'duplicate_payload': True,
}
SQS_FIFO_DUPLICATE_BODY_TEXT = json.dumps(
    SQS_FIFO_DUPLICATE_BODY,
    separators=(',', ':'),
)
SQS_FIFO_DEDUPLICATION_IDS = [
    f'FLOCI-ORDER-1001-{step}'
    for step in range(1, 4)
]
SQS_FIFO_FIRST_SEND_CACHE_KEY = 'floci-lab:sqs:fifo-ordering:first-send'
SQS_FIFO_DEDUPLICATED_CACHE_KEY = 'floci-lab:sqs:fifo-ordering:deduplicated'
SQS_FIFO_ORDERED_CACHE_KEY = 'floci-lab:sqs:fifo-ordering:ordered'
SQS_CLEANUP_QUEUE_NAME = 'lab-cleanup'
SQS_CLEANUP_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SQS_CLEANUP_QUEUE_NAME}'
)
SQS_CLEANUP_VISIBILITY_TIMEOUT = '45'
SQS_CLEANUP_TAGS = {'Purpose': 'cleanup-training'}
SQS_CLEANUP_MESSAGES = [
    {'event': 'cleanup.test', 'message_id': f'FLOCI-CLEANUP-{number}'}
    for number in range(1, 4)
]
SQS_CLEANUP_MESSAGE_TEXTS = [
    json.dumps(message, separators=(',', ':'))
    for message in SQS_CLEANUP_MESSAGES
]
SQS_CLEANUP_ATTRIBUTE_VALUE = 'purge-delete'
SQS_CLEANUP_POPULATED_CACHE_KEY = 'floci-lab:sqs:purge-delete:populated'
SQS_CLEANUP_PURGED_CACHE_KEY = 'floci-lab:sqs:purge-delete:purged'
SQS_CLEANUP_DELETED_CACHE_KEY = 'floci-lab:sqs:purge-delete:deleted'
SQS_CREATE_QUEUE_LAB = {
    'service': 'sqs',
    'key': 'create-queue',
    'title': 'Create and inspect an SQS queue',
    'description': 'Create a standard queue, resolve its queue URL, inspect its attributes, and find it in the account queue list.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create the queue',
            'command': f'aws sqs create-queue --queue-name {SQS_BASICS_QUEUE_NAME}',
            'explanation': 'Creates a standard SQS queue and returns the queue URL used by later message operations.',
        },
        {
            'key': 'get-queue-url',
            'title': 'Resolve the queue URL',
            'command': f'aws sqs get-queue-url --queue-name {SQS_BASICS_QUEUE_NAME}',
            'explanation': 'Looks up the stable queue URL from its human-readable queue name.',
        },
        {
            'key': 'get-queue-attributes',
            'title': 'Inspect queue attributes',
            'command': 'aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names All',
            'explanation': 'Reads the queue ARN, visibility timeout, retention settings, message counts, and other current attributes.',
        },
        {
            'key': 'list-queues',
            'title': 'List account queues',
            'command': 'aws sqs list-queues',
            'explanation': f'Lists SQS queue URLs and verifies that {SQS_BASICS_QUEUE_NAME} appears in the result.',
        },
    ],
}

SQS_MESSAGE_LIFECYCLE_LAB = {
    'service': 'sqs',
    'key': 'message-lifecycle',
    'title': 'Send, receive, and delete an SQS message',
    'description': 'Ensure the lab queue exists, send a known JSON event, receive and inspect it, then delete it with the live receipt handle.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Ensure the queue exists',
            'command': f'aws sqs create-queue --queue-name {SQS_BASICS_QUEUE_NAME}',
            'explanation': 'Creates the shared training queue if it does not already exist.',
        },
        {
            'key': 'send-message',
            'title': 'Send the order event',
            'command': 'aws sqs send-message --queue-url <queue-url> --message-body file://message.json --message-attributes file://message-attributes.json',
            'explanation': 'Sends a known JSON event with a Lab message attribute so the workflow can identify its own message.',
            'artifact_label': 'message.json',
            'artifact': json.dumps(SQS_MESSAGE_BODY, indent=2),
            'secondary_artifact_label': 'message-attributes.json',
            'secondary_artifact': json.dumps(SQS_MESSAGE_ATTRIBUTES, indent=2),
        },
        {
            'key': 'receive-message',
            'title': 'Receive and inspect the event',
            'command': 'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All',
            'explanation': 'Long-polls the queue and verifies the exact JSON body and Lab attribute without hiding the message from status checks.',
        },
        {
            'key': 'delete-message',
            'title': 'Delete the processed event',
            'command': 'aws sqs delete-message --queue-url <queue-url> --receipt-handle <receipt-handle>',
            'explanation': 'Receives the matching message to discover its current receipt handle, deletes it, and verifies that it is no longer available.',
        },
    ],
}

SQS_VISIBILITY_TIMEOUT_LAB = {
    'service': 'sqs',
    'key': 'visibility-timeout',
    'title': 'Understand SQS visibility timeout',
    'description': 'Receive a job without deleting it, adjust its visibility timeout, prove it is temporarily hidden, and receive it again when the timeout expires.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Ensure the queue exists',
            'command': f'aws sqs create-queue --queue-name {SQS_BASICS_QUEUE_NAME}',
            'explanation': 'Creates the shared training queue if it does not already exist.',
        },
        {
            'key': 'send-message',
            'title': 'Send the job',
            'command': 'aws sqs send-message --queue-url <queue-url> --message-body file://job.json --message-attributes file://message-attributes.json',
            'explanation': 'Sends a known job.ready event identified by a visibility-timeout Lab attribute.',
            'artifact_label': 'job.json',
            'artifact': json.dumps(SQS_VISIBILITY_BODY, indent=2),
            'secondary_artifact_label': 'message-attributes.json',
            'secondary_artifact': json.dumps(SQS_VISIBILITY_ATTRIBUTES, indent=2),
        },
        {
            'key': 'receive-message',
            'title': 'Receive without deleting',
            'command': 'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 30 --wait-time-seconds 1 --message-attribute-names All',
            'explanation': 'Receives the job and stores its current receipt handle while leaving the message in the queue.',
        },
        {
            'key': 'extend-message-visibility',
            'title': 'Extend the visibility timeout',
            'command': f'aws sqs change-message-visibility --queue-url <queue-url> --receipt-handle <receipt-handle> --visibility-timeout {SQS_VISIBILITY_EXTENDED_SECONDS}',
            'explanation': 'Extends the in-flight timeout to one minute, leaving enough time to confirm that other receives cannot see the job.',
        },
        {
            'key': 'verify-hidden',
            'title': 'Confirm the job is temporarily hidden',
            'command': 'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 0 --message-attribute-names All',
            'explanation': 'Polls immediately and verifies that the in-flight job is not currently available.',
        },
        {
            'key': 'shorten-message-visibility',
            'title': 'Shorten the visibility timeout',
            'command': f'aws sqs change-message-visibility --queue-url <queue-url> --receipt-handle <receipt-handle> --visibility-timeout {SQS_VISIBILITY_SHORT_SECONDS}',
            'explanation': 'Shortens the remaining invisibility to two seconds so the lab can demonstrate reappearance quickly.',
        },
        {
            'key': 'receive-after-timeout',
            'title': 'Receive the job after timeout',
            'command': 'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 3 --message-attribute-names All',
            'explanation': 'Waits for the short visibility timeout to expire, then verifies that the same undeleted job becomes available again.',
        },
    ],
}

SQS_DELAYED_MESSAGE_LAB = {
    'service': 'sqs',
    'key': 'delayed-message',
    'title': 'Work with delayed SQS messages',
    'description': 'Send a message that is initially unavailable, verify its delayed state, then receive it after the delivery delay expires.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Ensure the queue exists',
            'command': f'aws sqs create-queue --queue-name {SQS_BASICS_QUEUE_NAME}',
            'explanation': 'Creates the shared training queue if it does not already exist.',
        },
        {
            'key': 'send-delayed-message',
            'title': 'Send a delayed report request',
            'command': f'aws sqs send-message --queue-url <queue-url> --message-body file://report.json --message-attributes file://message-attributes.json --delay-seconds {SQS_DELAY_SECONDS}',
            'explanation': 'Sends a known report.generate event with a ten-second delivery delay. The lab immediately verifies the delayed count and confirms the message cannot yet be received.',
            'artifact_label': 'report.json',
            'artifact': json.dumps(SQS_DELAYED_BODY, indent=2),
            'secondary_artifact_label': 'message-attributes.json',
            'secondary_artifact': json.dumps(SQS_DELAYED_ATTRIBUTES, indent=2),
        },
        {
            'key': 'get-queue-attributes',
            'title': 'Inspect the delayed message count',
            'command': 'aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names ApproximateNumberOfMessagesDelayed',
            'explanation': 'Reads the queue delayed-message count and confirms the lab observed the message before delivery.',
        },
        {
            'key': 'receive-after-delay',
            'title': 'Receive the report request after its delay',
            'command': 'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 12 --message-attribute-names All',
            'explanation': 'Waits for the delivery delay to expire and verifies that the exact report.generate message becomes available.',
        },
    ],
}

SQS_BATCH_MESSAGES_LAB = {
    'service': 'sqs',
    'key': 'batch-messages',
    'title': 'Send and delete SQS messages in batches',
    'description': 'Send three task events in one API call, receive the batch, then delete all three with one batch acknowledgement.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Ensure the queue exists',
            'command': f'aws sqs create-queue --queue-name {SQS_BASICS_QUEUE_NAME}',
            'explanation': 'Creates the shared training queue if it does not already exist.',
        },
        {
            'key': 'send-message-batch',
            'title': 'Send three task events',
            'command': 'aws sqs send-message-batch --queue-url <queue-url> --entries file://send-batch.json',
            'explanation': 'Sends three independent task.created events in one request and verifies every entry succeeded.',
            'artifact_label': 'send-batch.json',
            'artifact': json.dumps({
                'Entries': [
                    {
                        'Id': f'task-{index}',
                        'MessageBody': text,
                        'MessageAttributes': {
                            'Lab': {
                                'DataType': 'String',
                                'StringValue': SQS_BATCH_ATTRIBUTE_VALUE,
                            },
                        },
                    }
                    for index, text in enumerate(SQS_BATCH_MESSAGE_TEXTS, start=1)
                ],
            }, indent=2),
        },
        {
            'key': 'receive-message',
            'title': 'Receive the task batch',
            'command': 'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --message-attribute-names All',
            'explanation': 'Receives up to ten messages and verifies that all three attributed task events are present.',
        },
        {
            'key': 'delete-message-batch',
            'title': 'Delete the processed batch',
            'command': 'aws sqs delete-message-batch --queue-url <queue-url> --entries file://delete-batch.json',
            'explanation': 'Discovers current receipt handles for the three task events and acknowledges them in one batch request.',
            'artifact_label': 'delete-batch.json',
            'artifact': json.dumps({
                'Entries': [
                    {'Id': f'task-{index}', 'ReceiptHandle': '<receipt-handle>'}
                    for index in range(1, 4)
                ],
            }, indent=2),
        },
    ],
}

SQS_QUEUE_CONFIGURATION_LAB = {
    'service': 'sqs',
    'key': 'queue-configuration',
    'title': 'Configure SQS queue attributes and tags',
    'description': 'Tune message processing defaults, apply operational tags, and inspect both configurations on the shared training queue.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Ensure the queue exists',
            'command': f'aws sqs create-queue --queue-name {SQS_BASICS_QUEUE_NAME}',
            'explanation': 'Creates the shared training queue if it does not already exist.',
        },
        {
            'key': 'set-queue-attributes',
            'title': 'Configure processing defaults',
            'command': 'aws sqs set-queue-attributes --queue-url <queue-url> --attributes file://queue-attributes.json',
            'explanation': 'Sets a 45-second visibility timeout, one-day message retention, and 10-second default long polling.',
            'artifact_label': 'queue-attributes.json',
            'artifact': json.dumps(SQS_CONFIGURATION_ATTRIBUTES, indent=2),
        },
        {
            'key': 'get-queue-attributes',
            'title': 'Inspect queue attributes',
            'command': 'aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names VisibilityTimeout MessageRetentionPeriod ReceiveMessageWaitTimeSeconds',
            'explanation': 'Reads the configured processing values and verifies all three exact settings.',
        },
        {
            'key': 'tag-queue',
            'title': 'Tag the queue',
            'command': 'aws sqs tag-queue --queue-url <queue-url> --tags Environment=lab,Purpose=training',
            'explanation': 'Adds operational tags that identify the queue as a training resource.',
        },
        {
            'key': 'list-queue-tags',
            'title': 'Inspect queue tags',
            'command': 'aws sqs list-queue-tags --queue-url <queue-url>',
            'explanation': 'Reads the queue tags and verifies the Environment and Purpose values.',
        },
    ],
}

SQS_DEAD_LETTER_REDRIVE_LAB = {
    'service': 'sqs',
    'key': 'dead-letter-redrive',
    'title': 'Route failed messages to a dead-letter queue',
    'description': 'Configure a source queue and dead-letter queue, fail the same message twice, inspect it in the DLQ, then redrive it to the source queue.',
    'steps': [
        {
            'key': 'create-dlq',
            'title': 'Create the dead-letter queue',
            'command': f'aws sqs create-queue --queue-name {SQS_REDRIVE_DLQ_NAME}',
            'explanation': 'Creates the queue that isolates messages a consumer could not process successfully.',
        },
        {
            'key': 'create-source-queue',
            'title': 'Create the source queue',
            'command': f'aws sqs create-queue --queue-name {SQS_REDRIVE_SOURCE_QUEUE_NAME}',
            'explanation': 'Creates the application queue that initially receives the payment-processing message.',
        },
        {
            'key': 'set-redrive-policy',
            'title': 'Attach the dead-letter queue',
            'command': 'aws sqs set-queue-attributes --queue-url <source-queue-url> --attributes file://redrive-policy.json',
            'explanation': 'Configures the DLQ target and moves a message after two receives without successful deletion.',
            'artifact_label': 'redrive-policy.json',
            'artifact': json.dumps({'RedrivePolicy': json.dumps(SQS_REDRIVE_POLICY)}, indent=2),
        },
        {
            'key': 'get-redrive-policy',
            'title': 'Inspect the redrive policy',
            'command': 'aws sqs get-queue-attributes --queue-url <source-queue-url> --attribute-names RedrivePolicy',
            'explanation': 'Reads the source queue configuration and verifies the exact DLQ ARN and maximum receive count.',
        },
        {
            'key': 'send-message',
            'title': 'Send a message that will fail',
            'command': 'aws sqs send-message --queue-url <source-queue-url> --message-body file://payment.json --message-attributes file://message-attributes.json',
            'explanation': 'Sends a known payment.process event with a flag that represents a consumer processing failure.',
            'artifact_label': 'payment.json',
            'artifact': json.dumps(SQS_REDRIVE_BODY, indent=2),
            'secondary_artifact_label': 'message-attributes.json',
            'secondary_artifact': json.dumps(SQS_REDRIVE_ATTRIBUTES, indent=2),
        },
        {
            'key': 'fail-message-once',
            'title': 'Fail the first processing attempt',
            'command': 'aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 0 --attribute-names All --message-attribute-names All',
            'explanation': 'Receives the message but deliberately does not delete it, simulating a failed consumer attempt.',
        },
        {
            'key': 'fail-message-twice',
            'title': 'Fail the second processing attempt',
            'command': 'aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 0 --attribute-names All --message-attribute-names All',
            'explanation': 'Receives the same message a second time without acknowledgement, reaching maxReceiveCount.',
        },
        {
            'key': 'trigger-dead-lettering',
            'title': 'Exceed the receive limit',
            'command': 'aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 0 --attribute-names All --message-attribute-names All',
            'explanation': 'Polls once more after maxReceiveCount is reached. SQS withholds the message from the source response and moves it to the DLQ.',
        },
        {
            'key': 'inspect-dlq',
            'title': 'Inspect the dead-lettered message',
            'command': 'aws sqs receive-message --queue-url <dlq-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 1 --attribute-names All --message-attribute-names All',
            'explanation': 'Verifies that SQS removed the failed message from the source queue and isolated it in the DLQ.',
        },
        {
            'key': 'start-message-move-task',
            'title': 'Redrive the message',
            'command': f'aws sqs start-message-move-task --source-arn {SQS_REDRIVE_DLQ_ARN} --destination-arn {SQS_REDRIVE_SOURCE_QUEUE_ARN} --max-number-of-messages-per-second 10',
            'explanation': 'Starts an SQS managed redrive task that returns messages from the DLQ to the source queue.',
        },
        {
            'key': 'list-message-move-tasks',
            'title': 'Inspect the redrive task',
            'command': f'aws sqs list-message-move-tasks --source-arn {SQS_REDRIVE_DLQ_ARN} --max-results 10',
            'explanation': 'Reads the task status and moved-message counts for the DLQ.',
        },
        {
            'key': 'receive-redriven-message',
            'title': 'Receive the recovered message',
            'command': 'aws sqs receive-message --queue-url <source-queue-url> --max-number-of-messages 1 --visibility-timeout 0 --wait-time-seconds 1 --attribute-names All --message-attribute-names All',
            'explanation': 'Confirms that the exact payment event returned to the source queue with a fresh receive count.',
        },
    ],
}

SQS_FIFO_ORDERING_LAB = {
    'service': 'sqs',
    'key': 'fifo-ordering',
    'title': 'Preserve ordering and deduplicate messages with SQS FIFO',
    'description': 'Create a FIFO queue, send an ordered workflow in one message group, suppress a duplicate send, and verify delivery order.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create the FIFO queue',
            'command': f'aws sqs create-queue --queue-name {SQS_FIFO_QUEUE_NAME} --attributes FifoQueue=true,ContentBasedDeduplication=false',
            'explanation': 'Creates a FIFO queue with explicit deduplication IDs. FIFO queue names must end in .fifo.',
        },
        {
            'key': 'get-queue-attributes',
            'title': 'Inspect FIFO settings',
            'command': 'aws sqs get-queue-attributes --queue-url <fifo-queue-url> --attribute-names QueueArn FifoQueue ContentBasedDeduplication',
            'explanation': 'Verifies the FIFO queue ARN and confirms that content-based deduplication is disabled.',
        },
        {
            'key': 'send-created',
            'title': 'Send order step 1',
            'command': f'aws sqs send-message --queue-url <fifo-queue-url> --message-body file://order-created.json --message-group-id {SQS_FIFO_GROUP_ID} --message-deduplication-id {SQS_FIFO_DEDUPLICATION_IDS[0]}',
            'explanation': 'Starts the ordered workflow. Messages in the same message group are delivered in sequence.',
            'artifact_label': 'order-created.json',
            'artifact': json.dumps(SQS_FIFO_MESSAGES[0], indent=2),
        },
        {
            'key': 'send-duplicate',
            'title': 'Retry with the same deduplication ID',
            'command': f'aws sqs send-message --queue-url <fifo-queue-url> --message-body file://duplicate-created.json --message-group-id {SQS_FIFO_GROUP_ID} --message-deduplication-id {SQS_FIFO_DEDUPLICATION_IDS[0]}',
            'explanation': 'Uses the same deduplication ID with a different body. SQS accepts the request but suppresses the duplicate message.',
            'artifact_label': 'duplicate-created.json',
            'artifact': json.dumps(SQS_FIFO_DUPLICATE_BODY, indent=2),
        },
        {
            'key': 'send-paid',
            'title': 'Send order step 2',
            'command': f'aws sqs send-message --queue-url <fifo-queue-url> --message-body file://order-paid.json --message-group-id {SQS_FIFO_GROUP_ID} --message-deduplication-id {SQS_FIFO_DEDUPLICATION_IDS[1]}',
            'explanation': 'Adds the paid event to the same ordered message group.',
            'artifact_label': 'order-paid.json',
            'artifact': json.dumps(SQS_FIFO_MESSAGES[1], indent=2),
        },
        {
            'key': 'send-fulfilled',
            'title': 'Send order step 3',
            'command': f'aws sqs send-message --queue-url <fifo-queue-url> --message-body file://order-fulfilled.json --message-group-id {SQS_FIFO_GROUP_ID} --message-deduplication-id {SQS_FIFO_DEDUPLICATION_IDS[2]}',
            'explanation': 'Adds the fulfilled event as the final event in the same message group.',
            'artifact_label': 'order-fulfilled.json',
            'artifact': json.dumps(SQS_FIFO_MESSAGES[2], indent=2),
        },
        {
            'key': 'inspect-message-count',
            'title': 'Confirm the duplicate was suppressed',
            'command': 'aws sqs get-queue-attributes --queue-url <fifo-queue-url> --attribute-names ApproximateNumberOfMessages',
            'explanation': 'Verifies that four successful send requests produced only three available messages.',
        },
        {
            'key': 'receive-ordered-messages',
            'title': 'Receive the ordered workflow',
            'command': 'aws sqs receive-message --queue-url <fifo-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1 --attribute-names All --message-attribute-names All',
            'explanation': 'Receives all three messages and verifies created, paid, and fulfilled order plus increasing FIFO sequence numbers.',
        },
    ],
}

SQS_PURGE_DELETE_LAB = {
    'service': 'sqs',
    'key': 'purge-delete',
    'title': 'Purge messages and delete an SQS queue',
    'description': 'Populate a dedicated queue, purge all messages while preserving its configuration, then delete the queue itself.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create a configured cleanup queue',
            'command': f'aws sqs create-queue --queue-name {SQS_CLEANUP_QUEUE_NAME} --attributes VisibilityTimeout={SQS_CLEANUP_VISIBILITY_TIMEOUT} --tags Purpose=cleanup-training',
            'explanation': 'Creates a dedicated queue with a non-default visibility timeout and tag so the lab can prove purge preserves queue configuration.',
        },
        {
            'key': 'send-message-batch',
            'title': 'Populate the queue',
            'command': 'aws sqs send-message-batch --queue-url <cleanup-queue-url> --entries file://cleanup-messages.json',
            'explanation': 'Adds three attributed messages that belong only to this cleanup lab.',
            'artifact_label': 'cleanup-messages.json',
            'artifact': json.dumps({
                'Entries': [
                    {
                        'Id': f'cleanup-{index}',
                        'MessageBody': text,
                        'MessageAttributes': {
                            'Lab': {
                                'DataType': 'String',
                                'StringValue': SQS_CLEANUP_ATTRIBUTE_VALUE,
                            },
                        },
                    }
                    for index, text in enumerate(
                        SQS_CLEANUP_MESSAGE_TEXTS,
                        start=1,
                    )
                ],
            }, indent=2),
        },
        {
            'key': 'inspect-populated-queue',
            'title': 'Inspect the populated queue',
            'command': 'aws sqs get-queue-attributes --queue-url <cleanup-queue-url> --attribute-names QueueArn VisibilityTimeout ApproximateNumberOfMessages',
            'explanation': 'Verifies the queue ARN, 45-second visibility timeout, and three available messages.',
        },
        {
            'key': 'purge-queue',
            'title': 'Purge every message',
            'command': 'aws sqs purge-queue --queue-url <cleanup-queue-url>',
            'explanation': 'Permanently removes all available and in-flight messages without deleting the queue.',
        },
        {
            'key': 'inspect-purged-queue',
            'title': 'Confirm the queue still exists',
            'command': 'aws sqs get-queue-attributes --queue-url <cleanup-queue-url> --attribute-names QueueArn VisibilityTimeout ApproximateNumberOfMessages',
            'explanation': 'Verifies zero messages while the queue ARN, visibility timeout, and Purpose tag remain intact.',
        },
        {
            'key': 'delete-queue',
            'title': 'Delete the queue',
            'command': 'aws sqs delete-queue --queue-url <cleanup-queue-url>',
            'explanation': 'Deletes the queue resource itself and verifies that its name no longer resolves to a queue URL.',
        },
    ],
}


def _sqs_basics_queue_url() -> str:
    return _sqs_client().get_queue_url(
        QueueName=SQS_BASICS_QUEUE_NAME,
    )['QueueUrl']


def _verify_sqs_basics_queue_url() -> dict[str, Any]:
    try:
        queue_url = _sqs_basics_queue_url()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if queue_url.rstrip('/').endswith(f'/{SQS_BASICS_QUEUE_NAME}'):
        return {
            'status': 'passed',
            'message': f'Queue URL for {SQS_BASICS_QUEUE_NAME} resolves successfully.',
            'resource': {
                'name': SQS_BASICS_QUEUE_NAME,
                'url': queue_url,
            },
        }

    return {
        'status': 'failed',
        'message': 'The resolved queue URL does not match the expected queue name.',
    }


def _verify_sqs_basics_queue_attributes() -> dict[str, Any]:
    try:
        queue_url = _sqs_basics_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['All'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    attributes = response.get('Attributes', {})
    if attributes.get('QueueArn') == SQS_BASICS_QUEUE_ARN:
        return {
            'status': 'passed',
            'message': 'The queue attributes contain the expected standard-queue ARN.',
            'resource': {
                'url': queue_url,
                'attributes': _clean_response(attributes),
            },
        }

    return {
        'status': 'failed',
        'message': 'The queue attributes do not contain the expected queue ARN.',
    }


def _verify_sqs_basics_listed() -> dict[str, Any]:
    try:
        response = _sqs_client().list_queues()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    queue_urls = response.get('QueueUrls', [])
    queue_url = next(
        (
            url for url in queue_urls
            if url.rstrip('/').endswith(f'/{SQS_BASICS_QUEUE_NAME}')
        ),
        None,
    )
    if queue_url:
        return {
            'status': 'passed',
            'message': f'{SQS_BASICS_QUEUE_NAME} appears in the account queue list.',
            'resource': {
                'url': queue_url,
                'queue_urls': queue_urls,
            },
        }

    return {
        'status': 'failed',
        'message': f'{SQS_BASICS_QUEUE_NAME} does not appear in the queue list.',
    }


def _sqs_receive_lab_messages(
    visibility_timeout: int = 0,
    wait_time_seconds: int = 1,
) -> dict[str, Any]:
    queue_url = _sqs_basics_queue_url()
    return _sqs_client().receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        VisibilityTimeout=visibility_timeout,
        WaitTimeSeconds=wait_time_seconds,
        AttributeNames=['All'],
        MessageAttributeNames=['All'],
    )


def _sqs_lifecycle_message(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            message for message in messages
            if message.get('Body') == SQS_MESSAGE_BODY_TEXT
            and message.get('MessageAttributes', {}).get('Lab', {}).get('StringValue')
            == 'message-lifecycle'
        ),
        None,
    )


def _verify_sqs_lifecycle_message() -> dict[str, Any]:
    try:
        response = _sqs_receive_lab_messages(visibility_timeout=0)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    message = _sqs_lifecycle_message(response.get('Messages', []))
    if message:
        return {
            'status': 'passed',
            'message': 'The queue contains the expected order.created event and Lab attribute.',
            'resource': _clean_response(message),
        }

    return {
        'status': 'failed',
        'message': 'The queue does not contain the expected lifecycle message.',
    }


def _verify_sqs_lifecycle_message_deleted() -> dict[str, Any]:
    if not cache.get(SQS_MESSAGE_DELETE_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The lifecycle message has not been deleted by this lab run.',
        }

    verification = _verify_sqs_lifecycle_message()
    if verification.get('status') == 'failed':
        return {
            'status': 'passed',
            'message': 'The processed lifecycle message was deleted from the queue.',
        }

    return {
        'status': 'failed',
        'message': 'The lifecycle message is still available in the queue.',
    }


def _sqs_visibility_message(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            message for message in messages
            if message.get('Body') == SQS_VISIBILITY_BODY_TEXT
            and message.get('MessageAttributes', {}).get('Lab', {}).get('StringValue')
            == 'visibility-timeout'
        ),
        None,
    )


def _verify_sqs_visibility_message_available() -> dict[str, Any]:
    try:
        response = _sqs_receive_lab_messages(visibility_timeout=0)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    message = _sqs_visibility_message(response.get('Messages', []))
    if message:
        return {
            'status': 'passed',
            'message': 'The visibility-timeout job is available in the queue.',
            'resource': _clean_response(message),
        }

    return {
        'status': 'failed',
        'message': 'The visibility-timeout job is not currently available.',
    }


def _verify_sqs_visibility_hidden() -> dict[str, Any]:
    if not cache.get(SQS_VISIBILITY_HIDDEN_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The lab has not yet verified the message while it was hidden.',
        }
    return {
        'status': 'passed',
        'message': 'The job was unavailable while its visibility timeout was active.',
    }


def _verify_sqs_visibility_returned() -> dict[str, Any]:
    if not cache.get(SQS_VISIBILITY_RETURNED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The lab has not yet received the job after its timeout.',
        }
    verification = _verify_sqs_visibility_message_available()
    if verification.get('status') == 'passed':
        verification['message'] = 'The undeleted job became available again after the visibility timeout.'
    return verification


def _sqs_delayed_message(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            message for message in messages
            if message.get('Body') == SQS_DELAYED_BODY_TEXT
            and message.get('MessageAttributes', {}).get('Lab', {}).get('StringValue')
            == 'delayed-message'
        ),
        None,
    )


def _sqs_delayed_count() -> tuple[str, int]:
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=['ApproximateNumberOfMessagesDelayed'],
    )
    return (
        queue_url,
        int(response.get('Attributes', {}).get('ApproximateNumberOfMessagesDelayed', 0)),
    )


def _sqs_wait_for_delayed_message(
    wait_seconds: float,
    visibility_timeout: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    deadline = time.monotonic() + wait_seconds
    last_response: dict[str, Any] = {}
    while True:
        remaining = max(0, deadline - time.monotonic())
        last_response = _sqs_receive_lab_messages(
            visibility_timeout=visibility_timeout,
            wait_time_seconds=min(1, max(0, int(remaining))),
        )
        message = _sqs_delayed_message(last_response.get('Messages', []))
        if message or time.monotonic() >= deadline:
            return last_response, message
        time.sleep(min(0.1, remaining))


def _verify_sqs_delayed_observed() -> dict[str, Any]:
    if not cache.get(SQS_DELAYED_OBSERVED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The lab has not yet observed the message while it was delayed.',
        }
    return {
        'status': 'passed',
        'message': 'The queue reported a delayed message that was not yet receivable.',
    }


def _verify_sqs_delayed_returned() -> dict[str, Any]:
    if not cache.get(SQS_DELAYED_RETURNED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The delayed report request has not yet been received.',
        }
    try:
        response, message = _sqs_wait_for_delayed_message(
            wait_seconds=0.5,
            visibility_timeout=0,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if message:
        return {
            'status': 'passed',
            'message': 'The delayed report request became available after its delivery delay.',
            'resource': _clean_response(message),
        }
    return {
        'status': 'failed',
        'message': 'The delayed report request is no longer available in the queue.',
    }


def _sqs_batch_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        message for message in messages
        if message.get('Body') in SQS_BATCH_MESSAGE_TEXTS
        and message.get('MessageAttributes', {}).get('Lab', {}).get('StringValue')
        == SQS_BATCH_ATTRIBUTE_VALUE
    ]


def _sqs_collect_batch_messages(
    wait_seconds: float,
    visibility_timeout: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    deadline = time.monotonic() + wait_seconds
    collected: dict[str, dict[str, Any]] = {}
    responses: list[dict[str, Any]] = []
    while time.monotonic() < deadline and len(collected) < len(SQS_BATCH_MESSAGE_TEXTS):
        response = _sqs_receive_lab_messages(
            visibility_timeout=visibility_timeout,
            wait_time_seconds=1,
        )
        responses.append(response)
        for message in _sqs_batch_messages(response.get('Messages', [])):
            collected[message['Body']] = message
        if len(collected) < len(SQS_BATCH_MESSAGE_TEXTS):
            time.sleep(0.05)
    return list(collected.values()), responses


def _verify_sqs_batch_available() -> dict[str, Any]:
    try:
        messages, _ = _sqs_collect_batch_messages(
            wait_seconds=2,
            visibility_timeout=0,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    bodies = {message.get('Body') for message in messages}
    if bodies == set(SQS_BATCH_MESSAGE_TEXTS):
        return {
            'status': 'passed',
            'message': 'All three task events are available in the queue.',
            'resource': {'messages': _clean_response(messages)},
        }
    return {
        'status': 'failed',
        'message': f'Expected three task events but found {len(bodies)}.',
    }


def _verify_sqs_batch_deleted() -> dict[str, Any]:
    if not cache.get(SQS_BATCH_DELETE_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The task batch has not been deleted by this lab run.',
        }
    verification = _verify_sqs_batch_available()
    if verification.get('status') == 'failed':
        return {
            'status': 'passed',
            'message': 'All three processed task events were deleted from the queue.',
        }
    return {
        'status': 'failed',
        'message': 'One or more task events remain available in the queue.',
    }


def _verify_sqs_configuration_attributes() -> dict[str, Any]:
    try:
        queue_url = _sqs_basics_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=list(SQS_CONFIGURATION_ATTRIBUTES),
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    attributes = response.get('Attributes', {})
    if all(
        attributes.get(name) == value
        for name, value in SQS_CONFIGURATION_ATTRIBUTES.items()
    ):
        return {
            'status': 'passed',
            'message': 'The queue has the expected visibility, retention, and long-poll settings.',
            'resource': {
                'queue_url': queue_url,
                'attributes': _clean_response(attributes),
            },
        }
    return {
        'status': 'failed',
        'message': 'The queue does not have all expected processing attributes.',
    }


def _verify_sqs_configuration_tags() -> dict[str, Any]:
    try:
        queue_url = _sqs_basics_queue_url()
        response = _sqs_client().list_queue_tags(QueueUrl=queue_url)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    tags = response.get('Tags', {})
    if all(tags.get(name) == value for name, value in SQS_CONFIGURATION_TAGS.items()):
        return {
            'status': 'passed',
            'message': 'The queue is tagged Environment=lab and Purpose=training.',
            'resource': {
                'queue_url': queue_url,
                'tags': tags,
            },
        }
    return {
        'status': 'failed',
        'message': 'The queue does not have the expected training tags.',
    }


def _verify_sqs_redrive_queue(
    queue_name: str,
    expected_arn: str,
) -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(queue_name)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    queue_arn = response.get('Attributes', {}).get('QueueArn')
    if queue_arn == expected_arn:
        return {
            'status': 'passed',
            'message': f'Queue {queue_name} exists with the expected ARN.',
            'resource': {
                'name': queue_name,
                'url': queue_url,
                'arn': queue_arn,
            },
        }
    return {
        'status': 'failed',
        'message': f'Queue {queue_name} does not have the expected ARN.',
    }


def _verify_sqs_redrive_policy() -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(SQS_REDRIVE_SOURCE_QUEUE_NAME)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['RedrivePolicy'],
        )
        policy_text = response.get('Attributes', {}).get('RedrivePolicy', '')
        policy = json.loads(policy_text) if policy_text else {}
    except (ClientError, json.JSONDecodeError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    if policy == SQS_REDRIVE_POLICY:
        return {
            'status': 'passed',
            'message': 'The source queue routes messages to the DLQ after two failed receives.',
            'resource': {
                'queue_url': queue_url,
                'redrive_policy': policy,
            },
        }
    return {
        'status': 'failed',
        'message': 'The source queue does not have the expected redrive policy.',
    }


def _sqs_redrive_message(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            message for message in messages
            if message.get('Body') == SQS_REDRIVE_BODY_TEXT
            and message.get('MessageAttributes', {}).get('Lab', {}).get('StringValue')
            == 'dead-letter-redrive'
        ),
        None,
    )


def _sqs_receive_redrive_message(
    queue_name: str,
    wait_time_seconds: int = 0,
) -> dict[str, Any]:
    return _sqs_client().receive_message(
        QueueUrl=_sqs_queue_url(queue_name),
        MaxNumberOfMessages=1,
        VisibilityTimeout=0,
        WaitTimeSeconds=wait_time_seconds,
        AttributeNames=['All'],
        MessageAttributeNames=['All'],
    )


def _verify_sqs_redrive_marker(
    cache_key: str,
    passed_message: str,
    failed_message: str,
) -> dict[str, Any]:
    if cache.get(cache_key):
        return {'status': 'passed', 'message': passed_message}
    return {'status': 'failed', 'message': failed_message}


def _verify_sqs_redrive_task() -> dict[str, Any]:
    task_handle = cache.get(SQS_REDRIVE_TASK_CACHE_KEY)
    if not task_handle:
        return {
            'status': 'failed',
            'message': 'No managed message move task has been started by this lab.',
        }
    try:
        response = _sqs_client().list_message_move_tasks(
            SourceArn=SQS_REDRIVE_DLQ_ARN,
            MaxResults=10,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    task = next(
        (
            item for item in response.get('Results', [])
            if item.get('TaskHandle') == task_handle
        ),
        None,
    )
    if (
        task
        and task.get('DestinationArn') == SQS_REDRIVE_SOURCE_QUEUE_ARN
        and task.get('Status') in {'RUNNING', 'COMPLETED'}
        and int(task.get('ApproximateNumberOfMessagesMoved', 0)) >= 1
    ):
        return {
            'status': 'passed',
            'message': 'The managed redrive task moved the failed message back to the source queue.',
            'resource': _clean_response(task),
        }
    return {
        'status': 'failed',
        'message': 'The message move task has not yet moved the expected message.',
    }


def _sqs_fifo_queue_url() -> str:
    return _sqs_queue_url(SQS_FIFO_QUEUE_NAME)


def _verify_sqs_fifo_queue() -> dict[str, Any]:
    try:
        queue_url = _sqs_fifo_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn', 'FifoQueue', 'ContentBasedDeduplication'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    attributes = response.get('Attributes', {})
    if (
        attributes.get('QueueArn') == SQS_FIFO_QUEUE_ARN
        and attributes.get('FifoQueue') == 'true'
        and attributes.get('ContentBasedDeduplication') == 'false'
    ):
        return {
            'status': 'passed',
            'message': 'The .fifo queue uses explicit message deduplication IDs.',
            'resource': {
                'queue_url': queue_url,
                'attributes': _clean_response(attributes),
            },
        }
    return {
        'status': 'failed',
        'message': 'The queue does not have the expected FIFO configuration.',
    }


def _sqs_receive_fifo_messages() -> dict[str, Any]:
    return _sqs_client().receive_message(
        QueueUrl=_sqs_fifo_queue_url(),
        MaxNumberOfMessages=10,
        VisibilityTimeout=0,
        WaitTimeSeconds=1,
        AttributeNames=['All'],
        MessageAttributeNames=['All'],
    )


def _verify_sqs_fifo_messages() -> dict[str, Any]:
    try:
        response = _sqs_receive_fifo_messages()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    messages = [
        message for message in response.get('Messages', [])
        if message.get('Attributes', {}).get('MessageGroupId') == SQS_FIFO_GROUP_ID
    ]
    bodies = [message.get('Body') for message in messages]
    sequence_numbers = [
        int(message.get('Attributes', {}).get('SequenceNumber', 0))
        for message in messages
    ]
    deduplication_ids = [
        message.get('Attributes', {}).get('MessageDeduplicationId')
        for message in messages
    ]
    if (
        bodies == SQS_FIFO_MESSAGE_TEXTS
        and SQS_FIFO_DUPLICATE_BODY_TEXT not in bodies
        and deduplication_ids == SQS_FIFO_DEDUPLICATION_IDS
        and sequence_numbers == sorted(sequence_numbers)
        and len(set(sequence_numbers)) == len(sequence_numbers)
    ):
        return {
            'status': 'passed',
            'message': 'The FIFO queue returned created, paid, and fulfilled in order with no duplicate.',
            'resource': {
                'messages': _clean_response(messages),
                'sequence_numbers': sequence_numbers,
            },
        }
    return {
        'status': 'failed',
        'message': 'The queue does not contain the expected three-message ordered workflow.',
    }


def _verify_sqs_fifo_message_count() -> dict[str, Any]:
    if not cache.get(SQS_FIFO_DEDUPLICATED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The duplicate send has not yet been verified.',
        }
    try:
        queue_url = _sqs_fifo_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['ApproximateNumberOfMessages'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    count = int(response.get('Attributes', {}).get('ApproximateNumberOfMessages', 0))
    if count == len(SQS_FIFO_MESSAGES):
        return {
            'status': 'passed',
            'message': 'Four successful send requests produced three available messages.',
            'resource': {
                'queue_url': queue_url,
                'approximate_messages': count,
            },
        }
    return {
        'status': 'failed',
        'message': f'Expected three available messages after deduplication but found {count}.',
    }


def _sqs_cleanup_queue_url() -> str:
    return _sqs_queue_url(SQS_CLEANUP_QUEUE_NAME)


def _sqs_cleanup_state() -> tuple[str, dict[str, Any], dict[str, str]]:
    queue_url = _sqs_cleanup_queue_url()
    attributes = _sqs_client().get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=[
            'QueueArn',
            'VisibilityTimeout',
            'ApproximateNumberOfMessages',
        ],
    ).get('Attributes', {})
    tags = _sqs_client().list_queue_tags(QueueUrl=queue_url).get('Tags', {})
    return queue_url, attributes, tags


def _verify_sqs_cleanup_queue(expected_count: int) -> dict[str, Any]:
    try:
        queue_url, attributes, tags = _sqs_cleanup_state()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    count = int(attributes.get('ApproximateNumberOfMessages', 0))
    if (
        attributes.get('QueueArn') == SQS_CLEANUP_QUEUE_ARN
        and attributes.get('VisibilityTimeout') == SQS_CLEANUP_VISIBILITY_TIMEOUT
        and tags.get('Purpose') == SQS_CLEANUP_TAGS['Purpose']
        and count == expected_count
    ):
        return {
            'status': 'passed',
            'message': (
                f'The cleanup queue has {expected_count} available messages; '
                'its visibility timeout and tag are intact.'
            ),
            'resource': {
                'queue_url': queue_url,
                'attributes': _clean_response(attributes),
                'tags': tags,
            },
        }
    return {
        'status': 'failed',
        'message': (
            f'The cleanup queue does not have the expected configuration and '
            f'{expected_count} available messages.'
        ),
    }


def _verify_sqs_cleanup_deleted() -> dict[str, Any]:
    if not cache.get(SQS_CLEANUP_DELETED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'The cleanup queue has not been deleted by this lab run.',
        }
    try:
        _sqs_cleanup_queue_url()
    except ClientError as exc:
        if _error_code(exc) in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            return {
                'status': 'passed',
                'message': f'Queue {SQS_CLEANUP_QUEUE_NAME} no longer resolves.',
            }
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'Queue {SQS_CLEANUP_QUEUE_NAME} still exists.',
    }


def _run_sqs_create_basics_queue(lab_key: str = 'create-queue') -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {SQS_BASICS_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=SQS_BASICS_QUEUE_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_basics_queue_url()

    return {
        'service': 'sqs',
        'lab': lab_key,
        'step': 'create-queue',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_send_lifecycle_message() -> dict[str, Any]:
    command = (
        'aws sqs send-message --queue-url <queue-url> '
        '--message-body file://message.json '
        '--message-attributes file://message-attributes.json'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().send_message(
        QueueUrl=queue_url,
        MessageBody=SQS_MESSAGE_BODY_TEXT,
        MessageAttributes=SQS_MESSAGE_ATTRIBUTES,
    )
    cache.delete(SQS_MESSAGE_DELETE_CACHE_KEY)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_lifecycle_message()

    return {
        'service': 'sqs',
        'lab': 'message-lifecycle',
        'step': 'send-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_receive_lifecycle_message() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 1 --message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_receive_lab_messages(
        visibility_timeout=0,
        wait_time_seconds=1,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    message = _sqs_lifecycle_message(response.get('Messages', []))
    verification = (
        {
            'status': 'passed',
            'message': 'The received message has the expected JSON body and Lab attribute.',
            'resource': _clean_response(message),
        }
        if message
        else {
            'status': 'failed',
            'message': 'The expected lifecycle message was not returned.',
        }
    )

    return {
        'service': 'sqs',
        'lab': 'message-lifecycle',
        'step': 'receive-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_delete_lifecycle_message() -> dict[str, Any]:
    command = (
        'aws sqs delete-message --queue-url <queue-url> '
        '--receipt-handle <receipt-handle>'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_receive_lab_messages(visibility_timeout=30)
    message = _sqs_lifecycle_message(response.get('Messages', []))
    if not message or not message.get('ReceiptHandle'):
        raise ValueError('Receive the lifecycle message before deleting it.')
    delete_response = _sqs_client().delete_message(
        QueueUrl=queue_url,
        ReceiptHandle=message['ReceiptHandle'],
    )
    cache.set(SQS_MESSAGE_DELETE_CACHE_KEY, True, timeout=86400)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_lifecycle_message_deleted()

    output = {
        'MessageId': message.get('MessageId'),
        **_clean_response(delete_response),
    }
    return {
        'service': 'sqs',
        'lab': 'message-lifecycle',
        'step': 'delete-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': output,
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_send_visibility_message() -> dict[str, Any]:
    command = (
        'aws sqs send-message --queue-url <queue-url> '
        '--message-body file://job.json '
        '--message-attributes file://message-attributes.json'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().send_message(
        QueueUrl=queue_url,
        MessageBody=SQS_VISIBILITY_BODY_TEXT,
        MessageAttributes=SQS_VISIBILITY_ATTRIBUTES,
    )
    cache.delete_many([
        SQS_VISIBILITY_RECEIPT_CACHE_KEY,
        SQS_VISIBILITY_EXTENDED_CACHE_KEY,
        SQS_VISIBILITY_HIDDEN_CACHE_KEY,
        SQS_VISIBILITY_SHORTENED_CACHE_KEY,
        SQS_VISIBILITY_RETURNED_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_visibility_message_available()

    return {
        'service': 'sqs',
        'lab': 'visibility-timeout',
        'step': 'send-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_receive_visibility_message() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 30 '
        '--wait-time-seconds 1 --message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_receive_lab_messages(visibility_timeout=30)
    message = _sqs_visibility_message(response.get('Messages', []))
    if not message or not message.get('ReceiptHandle'):
        raise ValueError('Send the visibility-timeout job before receiving it.')
    cache.set(
        SQS_VISIBILITY_RECEIPT_CACHE_KEY,
        message['ReceiptHandle'],
        timeout=300,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = {
        'status': 'passed',
        'message': 'The job was received without deletion and is now in flight.',
        'resource': _clean_response(message),
    }

    return {
        'service': 'sqs',
        'lab': 'visibility-timeout',
        'step': 'receive-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': True,
        'verification': verification,
    }


def _run_sqs_set_visibility(
    seconds: int,
    step_key: str,
    cache_key: str,
) -> dict[str, Any]:
    command = (
        'aws sqs change-message-visibility --queue-url <queue-url> '
        f'--receipt-handle <receipt-handle> --visibility-timeout {seconds}'
    )
    receipt_handle = cache.get(SQS_VISIBILITY_RECEIPT_CACHE_KEY)
    if not receipt_handle:
        raise ValueError('Receive the visibility-timeout job before changing its visibility.')
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().change_message_visibility(
        QueueUrl=queue_url,
        ReceiptHandle=receipt_handle,
        VisibilityTimeout=seconds,
    )
    cache.set(cache_key, True, timeout=86400)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = {
        'status': 'passed',
        'message': f'The job visibility timeout was changed to {seconds} seconds.',
    }

    return {
        'service': 'sqs',
        'lab': 'visibility-timeout',
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': True,
        'verification': verification,
    }


def _run_sqs_extend_visibility() -> dict[str, Any]:
    return _run_sqs_set_visibility(
        SQS_VISIBILITY_EXTENDED_SECONDS,
        'extend-message-visibility',
        SQS_VISIBILITY_EXTENDED_CACHE_KEY,
    )


def _run_sqs_shorten_visibility() -> dict[str, Any]:
    return _run_sqs_set_visibility(
        SQS_VISIBILITY_SHORT_SECONDS,
        'shorten-message-visibility',
        SQS_VISIBILITY_SHORTENED_CACHE_KEY,
    )


def _run_sqs_verify_visibility_hidden() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 0 --message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_receive_lab_messages(
        visibility_timeout=0,
        wait_time_seconds=0,
    )
    message = _sqs_visibility_message(response.get('Messages', []))
    duration_ms = round((time.perf_counter() - started) * 1000)
    if message:
        verification = {
            'status': 'failed',
            'message': 'The job was returned before its visibility timeout expired.',
        }
    else:
        cache.set(SQS_VISIBILITY_HIDDEN_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'The in-flight job is temporarily hidden from other receives.',
        }

    return {
        'service': 'sqs',
        'lab': 'visibility-timeout',
        'step': 'verify-hidden',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_receive_after_visibility_timeout() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 3 --message-attribute-names All'
    )
    started = time.perf_counter()
    time.sleep(SQS_VISIBILITY_SHORT_SECONDS + 0.25)
    response = _sqs_receive_lab_messages(
        visibility_timeout=0,
        wait_time_seconds=3,
    )
    message = _sqs_visibility_message(response.get('Messages', []))
    duration_ms = round((time.perf_counter() - started) * 1000)
    if message:
        cache.set(SQS_VISIBILITY_RETURNED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'The same undeleted job became available after the timeout expired.',
            'resource': _clean_response(message),
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The visibility-timeout job did not reappear in time.',
        }

    return {
        'service': 'sqs',
        'lab': 'visibility-timeout',
        'step': 'receive-after-timeout',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_send_delayed_message() -> dict[str, Any]:
    command = (
        'aws sqs send-message --queue-url <queue-url> '
        '--message-body file://report.json '
        '--message-attributes file://message-attributes.json '
        f'--delay-seconds {SQS_DELAY_SECONDS}'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().send_message(
        QueueUrl=queue_url,
        MessageBody=SQS_DELAYED_BODY_TEXT,
        MessageAttributes=SQS_DELAYED_ATTRIBUTES,
        DelaySeconds=SQS_DELAY_SECONDS,
    )
    cache.delete_many([
        SQS_DELAYED_OBSERVED_CACHE_KEY,
        SQS_DELAYED_RETURNED_CACHE_KEY,
    ])

    delayed_count = 0
    for _ in range(5):
        _, delayed_count = _sqs_delayed_count()
        if delayed_count > 0:
            break
        time.sleep(0.05)
    immediate = _sqs_receive_lab_messages(
        visibility_timeout=0,
        wait_time_seconds=0,
    )
    unavailable = _sqs_delayed_message(immediate.get('Messages', [])) is None
    if delayed_count > 0 and unavailable:
        cache.set(SQS_DELAYED_OBSERVED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'The queue reports the message as delayed and an immediate receive cannot return it.',
            'resource': {
                'approximate_delayed_messages': delayed_count,
            },
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The message was not observed in the expected delayed state.',
        }
    duration_ms = round((time.perf_counter() - started) * 1000)

    output = {
        **_clean_response(response),
        'ApproximateNumberOfMessagesDelayed': delayed_count,
        'ImmediateReceive': _clean_response(immediate),
    }
    return {
        'service': 'sqs',
        'lab': 'delayed-message',
        'step': 'send-delayed-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': output,
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_get_delayed_attributes() -> dict[str, Any]:
    command = (
        'aws sqs get-queue-attributes --queue-url <queue-url> '
        '--attribute-names ApproximateNumberOfMessagesDelayed'
    )
    started = time.perf_counter()
    queue_url, delayed_count = _sqs_delayed_count()
    response = {
        'Attributes': {
            'ApproximateNumberOfMessagesDelayed': str(delayed_count),
        },
    }
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_delayed_observed()
    if verification.get('status') == 'passed':
        verification = {
            **verification,
            'resource': {
                'queue_url': queue_url,
                'approximate_delayed_messages': delayed_count,
            },
        }

    return {
        'service': 'sqs',
        'lab': 'delayed-message',
        'step': 'get-queue-attributes',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': response,
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_receive_after_delay() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 12 --message-attribute-names All'
    )
    started = time.perf_counter()
    response, message = _sqs_wait_for_delayed_message(
        wait_seconds=12.5,
        visibility_timeout=0,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    if message:
        cache.set(SQS_DELAYED_RETURNED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'The delayed report request became available after ten seconds.',
            'resource': _clean_response(message),
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The delayed report request did not become available in time.',
        }

    return {
        'service': 'sqs',
        'lab': 'delayed-message',
        'step': 'receive-after-delay',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_send_message_batch() -> dict[str, Any]:
    command = (
        'aws sqs send-message-batch --queue-url <queue-url> '
        '--entries file://send-batch.json'
    )
    queue_url = _sqs_basics_queue_url()
    entries = [
        {
            'Id': f'task-{index}',
            'MessageBody': text,
            'MessageAttributes': {
                'Lab': {
                    'DataType': 'String',
                    'StringValue': SQS_BATCH_ATTRIBUTE_VALUE,
                },
            },
        }
        for index, text in enumerate(SQS_BATCH_MESSAGE_TEXTS, start=1)
    ]
    started = time.perf_counter()
    response = _sqs_client().send_message_batch(
        QueueUrl=queue_url,
        Entries=entries,
    )
    cache.delete(SQS_BATCH_DELETE_CACHE_KEY)
    duration_ms = round((time.perf_counter() - started) * 1000)
    successful_ids = {
        item.get('Id') for item in response.get('Successful', [])
    }
    expected_ids = {entry['Id'] for entry in entries}
    if successful_ids == expected_ids and not response.get('Failed'):
        verification = _verify_sqs_batch_available()
    else:
        verification = {
            'status': 'failed',
            'message': 'One or more batch send entries failed.',
        }

    return {
        'service': 'sqs',
        'lab': 'batch-messages',
        'step': 'send-message-batch',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_receive_message_batch() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 1 --message-attribute-names All'
    )
    started = time.perf_counter()
    messages, responses = _sqs_collect_batch_messages(
        wait_seconds=2,
        visibility_timeout=0,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    bodies = {message.get('Body') for message in messages}
    verification = (
        {
            'status': 'passed',
            'message': 'ReceiveMessage returned all three task events.',
            'resource': {'messages': _clean_response(messages)},
        }
        if bodies == set(SQS_BATCH_MESSAGE_TEXTS)
        else {
            'status': 'failed',
            'message': f'Expected three task events but found {len(bodies)}.',
        }
    )
    output = {
        'Messages': messages,
        'ReceiveCalls': len(responses),
    }

    return {
        'service': 'sqs',
        'lab': 'batch-messages',
        'step': 'receive-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': _clean_response(output),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_delete_message_batch() -> dict[str, Any]:
    command = (
        'aws sqs delete-message-batch --queue-url <queue-url> '
        '--entries file://delete-batch.json'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    messages, _ = _sqs_collect_batch_messages(
        wait_seconds=2,
        visibility_timeout=30,
    )
    by_body = {message.get('Body'): message for message in messages}
    if set(by_body) != set(SQS_BATCH_MESSAGE_TEXTS):
        raise ValueError('Receive all three batch messages before deleting them.')
    entries = [
        {
            'Id': f'task-{index}',
            'ReceiptHandle': by_body[text]['ReceiptHandle'],
        }
        for index, text in enumerate(SQS_BATCH_MESSAGE_TEXTS, start=1)
    ]
    response = _sqs_client().delete_message_batch(
        QueueUrl=queue_url,
        Entries=entries,
    )
    successful_ids = {
        item.get('Id') for item in response.get('Successful', [])
    }
    expected_ids = {entry['Id'] for entry in entries}
    if successful_ids == expected_ids and not response.get('Failed'):
        cache.set(SQS_BATCH_DELETE_CACHE_KEY, True, timeout=86400)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_batch_deleted()

    return {
        'service': 'sqs',
        'lab': 'batch-messages',
        'step': 'delete-message-batch',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_set_configuration_attributes() -> dict[str, Any]:
    command = (
        'aws sqs set-queue-attributes --queue-url <queue-url> '
        '--attributes file://queue-attributes.json'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().set_queue_attributes(
        QueueUrl=queue_url,
        Attributes=SQS_CONFIGURATION_ATTRIBUTES,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_configuration_attributes()

    return {
        'service': 'sqs',
        'lab': 'queue-configuration',
        'step': 'set-queue-attributes',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_get_configuration_attributes() -> dict[str, Any]:
    command = (
        'aws sqs get-queue-attributes --queue-url <queue-url> '
        '--attribute-names VisibilityTimeout MessageRetentionPeriod '
        'ReceiveMessageWaitTimeSeconds'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=list(SQS_CONFIGURATION_ATTRIBUTES),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_configuration_attributes()

    return {
        'service': 'sqs',
        'lab': 'queue-configuration',
        'step': 'get-queue-attributes',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_tag_configuration_queue() -> dict[str, Any]:
    command = (
        'aws sqs tag-queue --queue-url <queue-url> '
        '--tags Environment=lab,Purpose=training'
    )
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().tag_queue(
        QueueUrl=queue_url,
        Tags=SQS_CONFIGURATION_TAGS,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_configuration_tags()

    return {
        'service': 'sqs',
        'lab': 'queue-configuration',
        'step': 'tag-queue',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_list_configuration_tags() -> dict[str, Any]:
    command = 'aws sqs list-queue-tags --queue-url <queue-url>'
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().list_queue_tags(QueueUrl=queue_url)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_configuration_tags()

    return {
        'service': 'sqs',
        'lab': 'queue-configuration',
        'step': 'list-queue-tags',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _sqs_redrive_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'sqs',
        'lab': 'dead-letter-redrive',
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


def _run_sqs_create_redrive_dlq() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {SQS_REDRIVE_DLQ_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=SQS_REDRIVE_DLQ_NAME)
    verification = _verify_sqs_redrive_queue(
        SQS_REDRIVE_DLQ_NAME,
        SQS_REDRIVE_DLQ_ARN,
    )
    return _sqs_redrive_step_result(
        'create-dlq',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_create_redrive_source() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {SQS_REDRIVE_SOURCE_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=SQS_REDRIVE_SOURCE_QUEUE_NAME)
    verification = _verify_sqs_redrive_queue(
        SQS_REDRIVE_SOURCE_QUEUE_NAME,
        SQS_REDRIVE_SOURCE_QUEUE_ARN,
    )
    return _sqs_redrive_step_result(
        'create-source-queue',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_set_redrive_policy() -> dict[str, Any]:
    command = (
        'aws sqs set-queue-attributes --queue-url <source-queue-url> '
        '--attributes file://redrive-policy.json'
    )
    started = time.perf_counter()
    queue_url = _sqs_queue_url(SQS_REDRIVE_SOURCE_QUEUE_NAME)
    response = _sqs_client().set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={'RedrivePolicy': json.dumps(SQS_REDRIVE_POLICY)},
    )
    verification = _verify_sqs_redrive_policy()
    return _sqs_redrive_step_result(
        'set-redrive-policy',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_get_redrive_policy() -> dict[str, Any]:
    command = (
        'aws sqs get-queue-attributes --queue-url <source-queue-url> '
        '--attribute-names RedrivePolicy'
    )
    started = time.perf_counter()
    queue_url = _sqs_queue_url(SQS_REDRIVE_SOURCE_QUEUE_NAME)
    response = _sqs_client().get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=['RedrivePolicy'],
    )
    verification = _verify_sqs_redrive_policy()
    return _sqs_redrive_step_result(
        'get-redrive-policy',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_send_redrive_message() -> dict[str, Any]:
    command = (
        'aws sqs send-message --queue-url <source-queue-url> '
        '--message-body file://payment.json '
        '--message-attributes file://message-attributes.json'
    )
    started = time.perf_counter()
    response = _sqs_client().send_message(
        QueueUrl=_sqs_queue_url(SQS_REDRIVE_SOURCE_QUEUE_NAME),
        MessageBody=SQS_REDRIVE_BODY_TEXT,
        MessageAttributes=SQS_REDRIVE_ATTRIBUTES,
    )
    cache.delete_many([
        SQS_REDRIVE_FAILURE_ONE_CACHE_KEY,
        SQS_REDRIVE_FAILURE_TWO_CACHE_KEY,
        SQS_REDRIVE_TRIGGERED_CACHE_KEY,
        SQS_REDRIVE_DLQ_OBSERVED_CACHE_KEY,
        SQS_REDRIVE_TASK_CACHE_KEY,
        SQS_REDRIVE_RETURNED_CACHE_KEY,
    ])
    verification = {
        'status': 'passed',
        'message': 'The payment event was accepted by the source queue.',
        'resource': _clean_response(response),
    }
    return _sqs_redrive_step_result(
        'send-message',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_fail_redrive_message(
    step_key: str,
    expected_receive_count: int,
    cache_key: str,
) -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <source-queue-url> '
        '--max-number-of-messages 1 --visibility-timeout 0 '
        '--wait-time-seconds 0 --attribute-names All '
        '--message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_receive_redrive_message(SQS_REDRIVE_SOURCE_QUEUE_NAME)
    message = _sqs_redrive_message(response.get('Messages', []))
    receive_count = int(
        (message or {}).get('Attributes', {}).get('ApproximateReceiveCount', 0)
    )
    if message and receive_count >= expected_receive_count:
        cache.set(cache_key, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': (
                f'The payment event completed failed receive attempt '
                f'{expected_receive_count} without being deleted.'
            ),
            'resource': _clean_response(message),
        }
    else:
        verification = {
            'status': 'failed',
            'message': (
                f'The source queue did not return the expected failed attempt '
                f'{expected_receive_count}.'
            ),
        }
    return _sqs_redrive_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sqs_trigger_dead_lettering() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <source-queue-url> '
        '--max-number-of-messages 1 --visibility-timeout 0 '
        '--wait-time-seconds 0 --attribute-names All '
        '--message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_receive_redrive_message(SQS_REDRIVE_SOURCE_QUEUE_NAME)
    message = _sqs_redrive_message(response.get('Messages', []))
    if not message and cache.get(SQS_REDRIVE_FAILURE_TWO_CACHE_KEY):
        cache.set(SQS_REDRIVE_TRIGGERED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'The source queue withheld the over-received message and routed it to the DLQ.',
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The message is still available from the source queue.',
        }
    return _sqs_redrive_step_result(
        'trigger-dead-lettering',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_inspect_redrive_dlq() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <dlq-url> '
        '--max-number-of-messages 1 --visibility-timeout 0 '
        '--wait-time-seconds 1 --attribute-names All '
        '--message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_receive_redrive_message(
        SQS_REDRIVE_DLQ_NAME,
        wait_time_seconds=1,
    )
    message = _sqs_redrive_message(response.get('Messages', []))
    if message:
        cache.set(SQS_REDRIVE_DLQ_OBSERVED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'The failed payment event is isolated in the dead-letter queue.',
            'resource': _clean_response(message),
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The expected payment event was not found in the dead-letter queue.',
        }
    return _sqs_redrive_step_result(
        'inspect-dlq',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_start_message_move_task() -> dict[str, Any]:
    command = (
        f'aws sqs start-message-move-task --source-arn {SQS_REDRIVE_DLQ_ARN} '
        f'--destination-arn {SQS_REDRIVE_SOURCE_QUEUE_ARN} '
        '--max-number-of-messages-per-second 10'
    )
    started = time.perf_counter()
    response = _sqs_client().start_message_move_task(
        SourceArn=SQS_REDRIVE_DLQ_ARN,
        DestinationArn=SQS_REDRIVE_SOURCE_QUEUE_ARN,
        MaxNumberOfMessagesPerSecond=10,
    )
    task_handle = response.get('TaskHandle')
    if task_handle:
        cache.set(SQS_REDRIVE_TASK_CACHE_KEY, task_handle, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'SQS started a managed message move task for the DLQ.',
            'resource': {'task_handle': task_handle},
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'SQS did not return a message move task handle.',
        }
    return _sqs_redrive_step_result(
        'start-message-move-task',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_list_message_move_tasks() -> dict[str, Any]:
    command = (
        f'aws sqs list-message-move-tasks --source-arn {SQS_REDRIVE_DLQ_ARN} '
        '--max-results 10'
    )
    started = time.perf_counter()
    response = _sqs_client().list_message_move_tasks(
        SourceArn=SQS_REDRIVE_DLQ_ARN,
        MaxResults=10,
    )
    verification = _verify_sqs_redrive_task()
    return _sqs_redrive_step_result(
        'list-message-move-tasks',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_receive_redriven_message() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <source-queue-url> '
        '--max-number-of-messages 1 --visibility-timeout 0 '
        '--wait-time-seconds 1 --attribute-names All '
        '--message-attribute-names All'
    )
    started = time.perf_counter()
    response: dict[str, Any] = {}
    message = None
    for _ in range(10):
        response = _sqs_receive_redrive_message(
            SQS_REDRIVE_SOURCE_QUEUE_NAME,
            wait_time_seconds=1,
        )
        message = _sqs_redrive_message(response.get('Messages', []))
        if message:
            break
        time.sleep(0.05)
    receive_count = int(
        (message or {}).get('Attributes', {}).get('ApproximateReceiveCount', 0)
    )
    if message and receive_count == 1:
        cache.set(SQS_REDRIVE_RETURNED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'The exact payment event returned to the source queue with a fresh receive count.',
            'resource': _clean_response(message),
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The redriven payment event was not available with a fresh receive count.',
        }
    return _sqs_redrive_step_result(
        'receive-redriven-message',
        command,
        response,
        verification,
        started,
    )


def _sqs_fifo_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'sqs',
        'lab': 'fifo-ordering',
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


def _run_sqs_create_fifo_queue() -> dict[str, Any]:
    command = (
        f'aws sqs create-queue --queue-name {SQS_FIFO_QUEUE_NAME} '
        '--attributes FifoQueue=true,ContentBasedDeduplication=false'
    )
    started = time.perf_counter()
    response = _sqs_client().create_queue(
        QueueName=SQS_FIFO_QUEUE_NAME,
        Attributes={
            'FifoQueue': 'true',
            'ContentBasedDeduplication': 'false',
        },
    )
    verification = _verify_sqs_fifo_queue()
    return _sqs_fifo_step_result(
        'create-queue',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_get_fifo_attributes() -> dict[str, Any]:
    command = (
        'aws sqs get-queue-attributes --queue-url <fifo-queue-url> '
        '--attribute-names QueueArn FifoQueue ContentBasedDeduplication'
    )
    started = time.perf_counter()
    response = _sqs_client().get_queue_attributes(
        QueueUrl=_sqs_fifo_queue_url(),
        AttributeNames=['QueueArn', 'FifoQueue', 'ContentBasedDeduplication'],
    )
    verification = _verify_sqs_fifo_queue()
    return _sqs_fifo_step_result(
        'get-queue-attributes',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_send_fifo_message(
    message_index: int,
    step_key: str,
) -> dict[str, Any]:
    artifact_names = ('order-created.json', 'order-paid.json', 'order-fulfilled.json')
    command = (
        'aws sqs send-message --queue-url <fifo-queue-url> '
        f'--message-body file://{artifact_names[message_index]} '
        f'--message-group-id {SQS_FIFO_GROUP_ID} '
        '--message-deduplication-id '
        f'{SQS_FIFO_DEDUPLICATION_IDS[message_index]}'
    )
    started = time.perf_counter()
    response = _sqs_client().send_message(
        QueueUrl=_sqs_fifo_queue_url(),
        MessageBody=SQS_FIFO_MESSAGE_TEXTS[message_index],
        MessageGroupId=SQS_FIFO_GROUP_ID,
        MessageDeduplicationId=SQS_FIFO_DEDUPLICATION_IDS[message_index],
    )
    if message_index == 0:
        cache.delete_many([
            SQS_FIFO_DEDUPLICATED_CACHE_KEY,
            SQS_FIFO_ORDERED_CACHE_KEY,
        ])
        cache.set(
            SQS_FIFO_FIRST_SEND_CACHE_KEY,
            {
                'MessageId': response.get('MessageId'),
                'SequenceNumber': response.get('SequenceNumber'),
            },
            timeout=86400,
        )
    verified = bool(response.get('MessageId') and response.get('SequenceNumber'))
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            f'SQS accepted order workflow step {message_index + 1} '
            f'in message group {SQS_FIFO_GROUP_ID}.'
            if verified
            else 'SQS did not return FIFO message metadata.'
        ),
        'resource': _clean_response(response),
    }
    return _sqs_fifo_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )


def _run_sqs_send_fifo_duplicate() -> dict[str, Any]:
    command = (
        'aws sqs send-message --queue-url <fifo-queue-url> '
        '--message-body file://duplicate-created.json '
        f'--message-group-id {SQS_FIFO_GROUP_ID} '
        f'--message-deduplication-id {SQS_FIFO_DEDUPLICATION_IDS[0]}'
    )
    first_send = cache.get(SQS_FIFO_FIRST_SEND_CACHE_KEY)
    if not first_send:
        raise ValueError('Send the created event before retrying its deduplication ID.')
    started = time.perf_counter()
    response = _sqs_client().send_message(
        QueueUrl=_sqs_fifo_queue_url(),
        MessageBody=SQS_FIFO_DUPLICATE_BODY_TEXT,
        MessageGroupId=SQS_FIFO_GROUP_ID,
        MessageDeduplicationId=SQS_FIFO_DEDUPLICATION_IDS[0],
    )
    deduplicated = (
        response.get('MessageId') == first_send.get('MessageId')
        and response.get('SequenceNumber') == first_send.get('SequenceNumber')
    )
    if deduplicated:
        cache.set(SQS_FIFO_DEDUPLICATED_CACHE_KEY, True, timeout=86400)
    verification = {
        'status': 'passed' if deduplicated else 'failed',
        'message': (
            'The retry returned the original message ID and sequence number, so no duplicate was enqueued.'
            if deduplicated
            else 'The retry did not reuse the original FIFO message metadata.'
        ),
        'resource': {
            'original': first_send,
            'duplicate_send': _clean_response(response),
        },
    }
    return _sqs_fifo_step_result(
        'send-duplicate',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_get_fifo_message_count() -> dict[str, Any]:
    command = (
        'aws sqs get-queue-attributes --queue-url <fifo-queue-url> '
        '--attribute-names ApproximateNumberOfMessages'
    )
    started = time.perf_counter()
    response = _sqs_client().get_queue_attributes(
        QueueUrl=_sqs_fifo_queue_url(),
        AttributeNames=['ApproximateNumberOfMessages'],
    )
    verification = _verify_sqs_fifo_message_count()
    return _sqs_fifo_step_result(
        'inspect-message-count',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_receive_ordered_messages() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <fifo-queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 1 --attribute-names All '
        '--message-attribute-names All'
    )
    started = time.perf_counter()
    response = _sqs_receive_fifo_messages()
    messages = [
        message for message in response.get('Messages', [])
        if message.get('Attributes', {}).get('MessageGroupId') == SQS_FIFO_GROUP_ID
    ]
    bodies = [message.get('Body') for message in messages]
    sequence_numbers = [
        int(message.get('Attributes', {}).get('SequenceNumber', 0))
        for message in messages
    ]
    ordered = (
        bodies == SQS_FIFO_MESSAGE_TEXTS
        and sequence_numbers == sorted(sequence_numbers)
        and len(set(sequence_numbers)) == len(sequence_numbers)
    )
    if ordered:
        cache.set(SQS_FIFO_ORDERED_CACHE_KEY, True, timeout=86400)
    verification = {
        'status': 'passed' if ordered else 'failed',
        'message': (
            'ReceiveMessage returned created, paid, and fulfilled in FIFO sequence.'
            if ordered
            else 'ReceiveMessage did not return the expected ordered workflow.'
        ),
        'resource': {
            'messages': _clean_response(messages),
            'sequence_numbers': sequence_numbers,
        },
    }
    return _sqs_fifo_step_result(
        'receive-ordered-messages',
        command,
        response,
        verification,
        started,
    )


def _sqs_cleanup_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'sqs',
        'lab': 'purge-delete',
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


def _run_sqs_create_cleanup_queue() -> dict[str, Any]:
    command = (
        f'aws sqs create-queue --queue-name {SQS_CLEANUP_QUEUE_NAME} '
        f'--attributes VisibilityTimeout={SQS_CLEANUP_VISIBILITY_TIMEOUT} '
        '--tags Purpose=cleanup-training'
    )
    started = time.perf_counter()
    response = _sqs_client().create_queue(
        QueueName=SQS_CLEANUP_QUEUE_NAME,
        Attributes={'VisibilityTimeout': SQS_CLEANUP_VISIBILITY_TIMEOUT},
        tags=SQS_CLEANUP_TAGS,
    )
    cache.delete_many([
        SQS_CLEANUP_POPULATED_CACHE_KEY,
        SQS_CLEANUP_PURGED_CACHE_KEY,
        SQS_CLEANUP_DELETED_CACHE_KEY,
    ])
    verification = _verify_sqs_cleanup_queue(0)
    return _sqs_cleanup_step_result(
        'create-queue',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_send_cleanup_batch() -> dict[str, Any]:
    command = (
        'aws sqs send-message-batch --queue-url <cleanup-queue-url> '
        '--entries file://cleanup-messages.json'
    )
    entries = [
        {
            'Id': f'cleanup-{index}',
            'MessageBody': text,
            'MessageAttributes': {
                'Lab': {
                    'DataType': 'String',
                    'StringValue': SQS_CLEANUP_ATTRIBUTE_VALUE,
                },
            },
        }
        for index, text in enumerate(SQS_CLEANUP_MESSAGE_TEXTS, start=1)
    ]
    started = time.perf_counter()
    response = _sqs_client().send_message_batch(
        QueueUrl=_sqs_cleanup_queue_url(),
        Entries=entries,
    )
    successful_ids = {item.get('Id') for item in response.get('Successful', [])}
    expected_ids = {entry['Id'] for entry in entries}
    if successful_ids == expected_ids and not response.get('Failed'):
        verification = _verify_sqs_cleanup_queue(len(SQS_CLEANUP_MESSAGES))
    else:
        verification = {
            'status': 'failed',
            'message': 'One or more cleanup messages failed to send.',
        }
    if verification.get('status') == 'passed':
        cache.set(SQS_CLEANUP_POPULATED_CACHE_KEY, True, timeout=86400)
    return _sqs_cleanup_step_result(
        'send-message-batch',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_inspect_populated_cleanup_queue() -> dict[str, Any]:
    command = (
        'aws sqs get-queue-attributes --queue-url <cleanup-queue-url> '
        '--attribute-names QueueArn VisibilityTimeout '
        'ApproximateNumberOfMessages'
    )
    started = time.perf_counter()
    queue_url, attributes, tags = _sqs_cleanup_state()
    response = {'Attributes': attributes, 'Tags': tags}
    verification = _verify_sqs_cleanup_queue(len(SQS_CLEANUP_MESSAGES))
    if verification.get('status') == 'passed':
        cache.set(SQS_CLEANUP_POPULATED_CACHE_KEY, True, timeout=86400)
    return _sqs_cleanup_step_result(
        'inspect-populated-queue',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_purge_cleanup_queue() -> dict[str, Any]:
    command = 'aws sqs purge-queue --queue-url <cleanup-queue-url>'
    if not cache.get(SQS_CLEANUP_POPULATED_CACHE_KEY):
        raise ValueError('Populate and inspect the cleanup queue before purging it.')
    started = time.perf_counter()
    response = _sqs_client().purge_queue(QueueUrl=_sqs_cleanup_queue_url())
    verification = _verify_sqs_cleanup_queue(0)
    if verification.get('status') == 'passed':
        cache.set(SQS_CLEANUP_PURGED_CACHE_KEY, True, timeout=86400)
    return _sqs_cleanup_step_result(
        'purge-queue',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_inspect_purged_cleanup_queue() -> dict[str, Any]:
    command = (
        'aws sqs get-queue-attributes --queue-url <cleanup-queue-url> '
        '--attribute-names QueueArn VisibilityTimeout '
        'ApproximateNumberOfMessages'
    )
    started = time.perf_counter()
    queue_url, attributes, tags = _sqs_cleanup_state()
    response = {'Attributes': attributes, 'Tags': tags}
    verification = _verify_sqs_cleanup_queue(0)
    if verification.get('status') == 'passed':
        cache.set(SQS_CLEANUP_PURGED_CACHE_KEY, True, timeout=86400)
    return _sqs_cleanup_step_result(
        'inspect-purged-queue',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_delete_cleanup_queue() -> dict[str, Any]:
    command = 'aws sqs delete-queue --queue-url <cleanup-queue-url>'
    if not cache.get(SQS_CLEANUP_PURGED_CACHE_KEY):
        raise ValueError('Purge and inspect the cleanup queue before deleting it.')
    started = time.perf_counter()
    response = _sqs_client().delete_queue(QueueUrl=_sqs_cleanup_queue_url())
    cache.set(SQS_CLEANUP_DELETED_CACHE_KEY, True, timeout=86400)
    verification = _verify_sqs_cleanup_deleted()
    return _sqs_cleanup_step_result(
        'delete-queue',
        command,
        response,
        verification,
        started,
    )


def _run_sqs_get_basics_queue_url() -> dict[str, Any]:
    command = f'aws sqs get-queue-url --queue-name {SQS_BASICS_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().get_queue_url(QueueName=SQS_BASICS_QUEUE_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_basics_queue_url()

    return {
        'service': 'sqs',
        'lab': 'create-queue',
        'step': 'get-queue-url',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_get_basics_queue_attributes() -> dict[str, Any]:
    command = 'aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names All'
    started = time.perf_counter()
    queue_url = _sqs_basics_queue_url()
    response = _sqs_client().get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=['All'],
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_basics_queue_attributes()

    return {
        'service': 'sqs',
        'lab': 'create-queue',
        'step': 'get-queue-attributes',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sqs_list_basics_queues() -> dict[str, Any]:
    command = 'aws sqs list-queues'
    started = time.perf_counter()
    response = _sqs_client().list_queues()
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sqs_basics_listed()

    return {
        'service': 'sqs',
        'lab': 'create-queue',
        'step': 'list-queues',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _reset_sqs_create_queue() -> dict[str, Any]:
    command = 'aws sqs delete-queue --queue-url <queue-url>'
    started = time.perf_counter()
    deleted = False
    try:
        queue_url = _sqs_basics_queue_url()
        _sqs_client().delete_queue(QueueUrl=queue_url)
        deleted = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'sqs',
        'lab': 'create-queue',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted': deleted}),
        'stderr': '',
        'json': {'deleted': deleted},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted': deleted,
        'verification': {
            'status': 'passed',
            'message': (
                f'Queue {SQS_BASICS_QUEUE_NAME} was removed.'
                if deleted
                else f'Queue {SQS_BASICS_QUEUE_NAME} was already absent.'
            ),
        },
    }


def _reset_sqs_message_lifecycle() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 30\n'
        'aws sqs delete-message --queue-url <queue-url> '
        '--receipt-handle <receipt-handle>'
    )
    started = time.perf_counter()
    deleted_messages = 0
    try:
        queue_url = _sqs_basics_queue_url()
        response = _sqs_receive_lab_messages(visibility_timeout=30)
        for message in response.get('Messages', []):
            if (
                _sqs_lifecycle_message([message])
                and message.get('ReceiptHandle')
            ):
                _sqs_client().delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=message['ReceiptHandle'],
                )
                deleted_messages += 1
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete(SQS_MESSAGE_DELETE_CACHE_KEY)
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'sqs',
        'lab': 'message-lifecycle',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted_messages': deleted_messages}),
        'stderr': '',
        'json': {'deleted_messages': deleted_messages},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted_messages': deleted_messages,
        'verification': {
            'status': 'passed',
            'message': (
                'Lifecycle messages and stored completion state were cleared; '
                f'queue {SQS_BASICS_QUEUE_NAME} was left in place.'
            ),
        },
    }


def _reset_sqs_visibility_timeout() -> dict[str, Any]:
    command = (
        'aws sqs change-message-visibility --queue-url <queue-url> '
        '--receipt-handle <receipt-handle> --visibility-timeout 0\n'
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 30\n'
        'aws sqs delete-message --queue-url <queue-url> '
        '--receipt-handle <receipt-handle>'
    )
    started = time.perf_counter()
    deleted_messages = 0
    try:
        queue_url = _sqs_basics_queue_url()
        receipt_handle = cache.get(SQS_VISIBILITY_RECEIPT_CACHE_KEY)
        if receipt_handle:
            try:
                _sqs_client().change_message_visibility(
                    QueueUrl=queue_url,
                    ReceiptHandle=receipt_handle,
                    VisibilityTimeout=0,
                )
            except ClientError as exc:
                if _error_code(exc) not in {'ReceiptHandleIsInvalid', 'InvalidParameterValue'}:
                    raise
        response = _sqs_receive_lab_messages(
            visibility_timeout=30,
            wait_time_seconds=1,
        )
        for message in response.get('Messages', []):
            if (
                _sqs_visibility_message([message])
                and message.get('ReceiptHandle')
            ):
                _sqs_client().delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=message['ReceiptHandle'],
                )
                deleted_messages += 1
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete_many([
        SQS_VISIBILITY_RECEIPT_CACHE_KEY,
        SQS_VISIBILITY_EXTENDED_CACHE_KEY,
        SQS_VISIBILITY_HIDDEN_CACHE_KEY,
        SQS_VISIBILITY_SHORTENED_CACHE_KEY,
        SQS_VISIBILITY_RETURNED_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'sqs',
        'lab': 'visibility-timeout',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted_messages': deleted_messages}),
        'stderr': '',
        'json': {'deleted_messages': deleted_messages},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted_messages': deleted_messages,
        'verification': {
            'status': 'passed',
            'message': (
                'Visibility-lab messages and timing state were cleared; '
                f'queue {SQS_BASICS_QUEUE_NAME} was left in place.'
            ),
        },
    }


def _reset_sqs_delayed_message() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 30 --wait-time-seconds 12\n'
        'aws sqs delete-message --queue-url <queue-url> '
        '--receipt-handle <receipt-handle>'
    )
    started = time.perf_counter()
    deleted_messages = 0
    try:
        queue_url = _sqs_basics_queue_url()
        response, _ = _sqs_wait_for_delayed_message(
            wait_seconds=12.5,
            visibility_timeout=30,
        )
        for message in response.get('Messages', []):
            if (
                _sqs_delayed_message([message])
                and message.get('ReceiptHandle')
            ):
                _sqs_client().delete_message(
                    QueueUrl=queue_url,
                    ReceiptHandle=message['ReceiptHandle'],
                )
                deleted_messages += 1
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete_many([
        SQS_DELAYED_OBSERVED_CACHE_KEY,
        SQS_DELAYED_RETURNED_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'sqs',
        'lab': 'delayed-message',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted_messages': deleted_messages}),
        'stderr': '',
        'json': {'deleted_messages': deleted_messages},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted_messages': deleted_messages,
        'verification': {
            'status': 'passed',
            'message': (
                'Delayed-lab messages and timing state were cleared; '
                f'queue {SQS_BASICS_QUEUE_NAME} was left in place.'
            ),
        },
    }


def _reset_sqs_batch_messages() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 30\n'
        'aws sqs delete-message-batch --queue-url <queue-url> '
        '--entries file://delete-batch.json'
    )
    started = time.perf_counter()
    deleted_messages = 0
    try:
        queue_url = _sqs_basics_queue_url()
        messages, _ = _sqs_collect_batch_messages(
            wait_seconds=2,
            visibility_timeout=30,
        )
        entries = [
            {
                'Id': f'task-{index}',
                'ReceiptHandle': message['ReceiptHandle'],
            }
            for index, message in enumerate(messages, start=1)
            if message.get('ReceiptHandle')
        ]
        if entries:
            response = _sqs_client().delete_message_batch(
                QueueUrl=queue_url,
                Entries=entries,
            )
            deleted_messages = len(response.get('Successful', []))
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete(SQS_BATCH_DELETE_CACHE_KEY)
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'sqs',
        'lab': 'batch-messages',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted_messages': deleted_messages}),
        'stderr': '',
        'json': {'deleted_messages': deleted_messages},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted_messages': deleted_messages,
        'verification': {
            'status': 'passed',
            'message': (
                'Batch-lab messages and completion state were cleared; '
                f'queue {SQS_BASICS_QUEUE_NAME} was left in place.'
            ),
        },
    }


def _reset_sqs_queue_configuration() -> dict[str, Any]:
    command = (
        'aws sqs set-queue-attributes --queue-url <queue-url> '
        '--attributes file://default-queue-attributes.json\n'
        'aws sqs untag-queue --queue-url <queue-url> '
        '--tag-keys Environment Purpose'
    )
    started = time.perf_counter()
    reset = False
    try:
        queue_url = _sqs_basics_queue_url()
        _sqs_client().set_queue_attributes(
            QueueUrl=queue_url,
            Attributes=SQS_DEFAULT_ATTRIBUTES,
        )
        _sqs_client().untag_queue(
            QueueUrl=queue_url,
            TagKeys=list(SQS_CONFIGURATION_TAGS),
        )
        reset = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'sqs',
        'lab': 'queue-configuration',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({
            'reset_attributes': reset,
            'removed_tags': reset,
        }),
        'stderr': '',
        'json': {
            'reset_attributes': reset,
            'removed_tags': reset,
        },
        'duration_ms': duration_ms,
        'reset': True,
        'reset_attributes': reset,
        'removed_tags': reset,
        'verification': {
            'status': 'passed',
            'message': (
                'Queue processing attributes were restored to defaults and '
                'the lab tags were removed; the shared queue was left in place.'
                if reset
                else f'Queue {SQS_BASICS_QUEUE_NAME} was already absent.'
            ),
        },
    }


def _reset_sqs_dead_letter_redrive() -> dict[str, Any]:
    command = (
        f'aws sqs list-message-move-tasks --source-arn {SQS_REDRIVE_DLQ_ARN} '
        '--max-results 10\n'
        'aws sqs cancel-message-move-task --task-handle <task-handle>\n'
        'aws sqs delete-queue --queue-url <source-queue-url>\n'
        'aws sqs delete-queue --queue-url <dlq-url>'
    )
    started = time.perf_counter()
    cancelled_tasks = 0
    deleted_queues: list[str] = []
    try:
        tasks = _sqs_client().list_message_move_tasks(
            SourceArn=SQS_REDRIVE_DLQ_ARN,
            MaxResults=10,
        )
        for task in tasks.get('Results', []):
            if task.get('Status') == 'RUNNING' and task.get('TaskHandle'):
                _sqs_client().cancel_message_move_task(
                    TaskHandle=task['TaskHandle'],
                )
                cancelled_tasks += 1
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
            'ResourceNotFoundException',
        }:
            raise

    for queue_name in (
        SQS_REDRIVE_SOURCE_QUEUE_NAME,
        SQS_REDRIVE_DLQ_NAME,
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

    cache.delete_many([
        SQS_REDRIVE_FAILURE_ONE_CACHE_KEY,
        SQS_REDRIVE_FAILURE_TWO_CACHE_KEY,
        SQS_REDRIVE_TRIGGERED_CACHE_KEY,
        SQS_REDRIVE_DLQ_OBSERVED_CACHE_KEY,
        SQS_REDRIVE_TASK_CACHE_KEY,
        SQS_REDRIVE_RETURNED_CACHE_KEY,
    ])
    output = {
        'cancelled_tasks': cancelled_tasks,
        'deleted_queues': deleted_queues,
    }
    return {
        'service': 'sqs',
        'lab': 'dead-letter-redrive',
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
            'message': 'The redrive task state, source queue, and dead-letter queue were cleared.',
        },
    }


def _reset_sqs_fifo_ordering() -> dict[str, Any]:
    command = 'aws sqs delete-queue --queue-url <fifo-queue-url>'
    started = time.perf_counter()
    deleted = False
    try:
        queue_url = _sqs_fifo_queue_url()
        _sqs_client().delete_queue(QueueUrl=queue_url)
        deleted = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete_many([
        SQS_FIFO_FIRST_SEND_CACHE_KEY,
        SQS_FIFO_DEDUPLICATED_CACHE_KEY,
        SQS_FIFO_ORDERED_CACHE_KEY,
    ])
    output = {
        'deleted_queue': deleted,
        'queue_name': SQS_FIFO_QUEUE_NAME,
    }
    return {
        'service': 'sqs',
        'lab': 'fifo-ordering',
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
            'message': (
                f'FIFO queue {SQS_FIFO_QUEUE_NAME} and its lab state were removed.'
                if deleted
                else f'FIFO queue {SQS_FIFO_QUEUE_NAME} was already absent.'
            ),
        },
    }


def _reset_sqs_purge_delete() -> dict[str, Any]:
    command = 'aws sqs delete-queue --queue-url <cleanup-queue-url>'
    started = time.perf_counter()
    deleted = False
    try:
        queue_url = _sqs_cleanup_queue_url()
        _sqs_client().delete_queue(QueueUrl=queue_url)
        deleted = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete_many([
        SQS_CLEANUP_POPULATED_CACHE_KEY,
        SQS_CLEANUP_PURGED_CACHE_KEY,
        SQS_CLEANUP_DELETED_CACHE_KEY,
    ])
    output = {
        'deleted_queue': deleted,
        'queue_name': SQS_CLEANUP_QUEUE_NAME,
    }
    return {
        'service': 'sqs',
        'lab': 'purge-delete',
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
            'message': (
                f'Cleanup queue {SQS_CLEANUP_QUEUE_NAME} and lab state were removed.'
                if deleted
                else f'Cleanup queue {SQS_CLEANUP_QUEUE_NAME} was already absent.'
            ),
        },
    }


LABS = [
    SQS_CREATE_QUEUE_LAB,
    SQS_MESSAGE_LIFECYCLE_LAB,
    SQS_VISIBILITY_TIMEOUT_LAB,
    SQS_DELAYED_MESSAGE_LAB,
    SQS_BATCH_MESSAGES_LAB,
    SQS_QUEUE_CONFIGURATION_LAB,
    SQS_DEAD_LETTER_REDRIVE_LAB,
    SQS_FIFO_ORDERING_LAB,
    SQS_PURGE_DELETE_LAB,
]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'sqs' and lab_key == 'create-queue':
        runners = {
            'create-queue': _run_sqs_create_basics_queue,
            'get-queue-url': _run_sqs_get_basics_queue_url,
            'get-queue-attributes': _run_sqs_get_basics_queue_attributes,
            'list-queues': _run_sqs_list_basics_queues,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'message-lifecycle':
        runners = {
            'create-queue': lambda: _run_sqs_create_basics_queue('message-lifecycle'),
            'send-message': _run_sqs_send_lifecycle_message,
            'receive-message': _run_sqs_receive_lifecycle_message,
            'delete-message': _run_sqs_delete_lifecycle_message,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'visibility-timeout':
        runners = {
            'create-queue': lambda: _run_sqs_create_basics_queue('visibility-timeout'),
            'send-message': _run_sqs_send_visibility_message,
            'receive-message': _run_sqs_receive_visibility_message,
            'extend-message-visibility': _run_sqs_extend_visibility,
            'verify-hidden': _run_sqs_verify_visibility_hidden,
            'shorten-message-visibility': _run_sqs_shorten_visibility,
            'receive-after-timeout': _run_sqs_receive_after_visibility_timeout,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'delayed-message':
        runners = {
            'create-queue': lambda: _run_sqs_create_basics_queue('delayed-message'),
            'send-delayed-message': _run_sqs_send_delayed_message,
            'get-queue-attributes': _run_sqs_get_delayed_attributes,
            'receive-after-delay': _run_sqs_receive_after_delay,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'batch-messages':
        runners = {
            'create-queue': lambda: _run_sqs_create_basics_queue('batch-messages'),
            'send-message-batch': _run_sqs_send_message_batch,
            'receive-message': _run_sqs_receive_message_batch,
            'delete-message-batch': _run_sqs_delete_message_batch,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'queue-configuration':
        runners = {
            'create-queue': lambda: _run_sqs_create_basics_queue('queue-configuration'),
            'set-queue-attributes': _run_sqs_set_configuration_attributes,
            'get-queue-attributes': _run_sqs_get_configuration_attributes,
            'tag-queue': _run_sqs_tag_configuration_queue,
            'list-queue-tags': _run_sqs_list_configuration_tags,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'dead-letter-redrive':
        runners = {
            'create-dlq': _run_sqs_create_redrive_dlq,
            'create-source-queue': _run_sqs_create_redrive_source,
            'set-redrive-policy': _run_sqs_set_redrive_policy,
            'get-redrive-policy': _run_sqs_get_redrive_policy,
            'send-message': _run_sqs_send_redrive_message,
            'fail-message-once': lambda: _run_sqs_fail_redrive_message(
                'fail-message-once',
                1,
                SQS_REDRIVE_FAILURE_ONE_CACHE_KEY,
            ),
            'fail-message-twice': lambda: _run_sqs_fail_redrive_message(
                'fail-message-twice',
                2,
                SQS_REDRIVE_FAILURE_TWO_CACHE_KEY,
            ),
            'trigger-dead-lettering': _run_sqs_trigger_dead_lettering,
            'inspect-dlq': _run_sqs_inspect_redrive_dlq,
            'start-message-move-task': _run_sqs_start_message_move_task,
            'list-message-move-tasks': _run_sqs_list_message_move_tasks,
            'receive-redriven-message': _run_sqs_receive_redriven_message,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'fifo-ordering':
        runners = {
            'create-queue': _run_sqs_create_fifo_queue,
            'get-queue-attributes': _run_sqs_get_fifo_attributes,
            'send-created': lambda: _run_sqs_send_fifo_message(0, 'send-created'),
            'send-duplicate': _run_sqs_send_fifo_duplicate,
            'send-paid': lambda: _run_sqs_send_fifo_message(1, 'send-paid'),
            'send-fulfilled': lambda: _run_sqs_send_fifo_message(
                2,
                'send-fulfilled',
            ),
            'inspect-message-count': _run_sqs_get_fifo_message_count,
            'receive-ordered-messages': _run_sqs_receive_ordered_messages,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs' and lab_key == 'purge-delete':
        runners = {
            'create-queue': _run_sqs_create_cleanup_queue,
            'send-message-batch': _run_sqs_send_cleanup_batch,
            'inspect-populated-queue': _run_sqs_inspect_populated_cleanup_queue,
            'purge-queue': _run_sqs_purge_cleanup_queue,
            'inspect-purged-queue': _run_sqs_inspect_purged_cleanup_queue,
            'delete-queue': _run_sqs_delete_cleanup_queue,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'sqs' and lab_key == 'create-queue':
        url_verification = _verify_sqs_basics_queue_url()
        attributes_verification = _verify_sqs_basics_queue_attributes()
        list_verification = _verify_sqs_basics_listed()
        url_verified = url_verification.get('status') == 'passed'
        attributes_verified = attributes_verification.get('status') == 'passed'
        list_verified = list_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': url_verified and attributes_verified and list_verified,
            'steps': {
                'create-queue': {
                    'verified': url_verified,
                    'verification': url_verification if url_verified else None,
                },
                'get-queue-url': {
                    'verified': url_verified,
                    'verification': url_verification if url_verified else None,
                },
                'get-queue-attributes': {
                    'verified': attributes_verified,
                    'verification': attributes_verification if attributes_verified else None,
                },
                'list-queues': {
                    'verified': list_verified,
                    'verification': list_verification if list_verified else None,
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'message-lifecycle':
        queue_verification = _verify_sqs_basics_queue_url()
        deletion_verification = _verify_sqs_lifecycle_message_deleted()
        queue_verified = queue_verification.get('status') == 'passed'
        deletion_verified = deletion_verification.get('status') == 'passed'
        message_verification = (
            deletion_verification
            if deletion_verified
            else _verify_sqs_lifecycle_message()
        )
        message_verified = message_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': queue_verified and deletion_verified,
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'send-message': {
                    'verified': message_verified,
                    'verification': message_verification if message_verified else None,
                },
                'receive-message': {
                    'verified': message_verified,
                    'verification': message_verification if message_verified else None,
                },
                'delete-message': {
                    'verified': deletion_verified,
                    'verification': deletion_verification if deletion_verified else None,
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'visibility-timeout':
        queue_verification = _verify_sqs_basics_queue_url()
        queue_verified = queue_verification.get('status') == 'passed'
        returned_verification = _verify_sqs_visibility_returned()
        returned_verified = returned_verification.get('status') == 'passed'
        hidden_verification = _verify_sqs_visibility_hidden()
        hidden_verified = hidden_verification.get('status') == 'passed'
        available_verification = (
            returned_verification
            if returned_verified
            else _verify_sqs_visibility_message_available()
        )
        available_verified = available_verification.get('status') == 'passed'
        receipt_verified = bool(cache.get(SQS_VISIBILITY_RECEIPT_CACHE_KEY))
        extended_verified = bool(cache.get(SQS_VISIBILITY_EXTENDED_CACHE_KEY))
        shortened_verified = bool(cache.get(SQS_VISIBILITY_SHORTENED_CACHE_KEY))
        receipt_verification = {
            'status': 'passed',
            'message': 'The job was received and its live receipt handle was captured.',
        }
        extended_verification = {
            'status': 'passed',
            'message': 'The job visibility timeout was extended to 60 seconds.',
        }
        shortened_verification = {
            'status': 'passed',
            'message': 'The job visibility timeout was shortened to 2 seconds.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': queue_verified and hidden_verified and returned_verified,
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'send-message': {
                    'verified': available_verified or receipt_verified,
                    'verification': (
                        available_verification
                        if available_verified
                        else receipt_verification if receipt_verified else None
                    ),
                },
                'receive-message': {
                    'verified': receipt_verified or returned_verified,
                    'verification': (
                        receipt_verification
                        if receipt_verified
                        else returned_verification if returned_verified else None
                    ),
                },
                'extend-message-visibility': {
                    'verified': extended_verified or hidden_verified,
                    'verification': (
                        extended_verification
                        if extended_verified
                        else hidden_verification if hidden_verified else None
                    ),
                },
                'verify-hidden': {
                    'verified': hidden_verified,
                    'verification': hidden_verification if hidden_verified else None,
                },
                'shorten-message-visibility': {
                    'verified': shortened_verified or returned_verified,
                    'verification': (
                        shortened_verification
                        if shortened_verified
                        else returned_verification if returned_verified else None
                    ),
                },
                'receive-after-timeout': {
                    'verified': returned_verified,
                    'verification': returned_verification if returned_verified else None,
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'delayed-message':
        queue_verification = _verify_sqs_basics_queue_url()
        observed_verification = _verify_sqs_delayed_observed()
        returned_verification = _verify_sqs_delayed_returned()
        queue_verified = queue_verification.get('status') == 'passed'
        observed_verified = observed_verification.get('status') == 'passed'
        returned_verified = returned_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': queue_verified and observed_verified and returned_verified,
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'send-delayed-message': {
                    'verified': observed_verified,
                    'verification': observed_verification if observed_verified else None,
                },
                'get-queue-attributes': {
                    'verified': observed_verified,
                    'verification': observed_verification if observed_verified else None,
                },
                'receive-after-delay': {
                    'verified': returned_verified,
                    'verification': returned_verification if returned_verified else None,
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'batch-messages':
        queue_verification = _verify_sqs_basics_queue_url()
        deletion_verification = _verify_sqs_batch_deleted()
        queue_verified = queue_verification.get('status') == 'passed'
        deletion_verified = deletion_verification.get('status') == 'passed'
        batch_verification = (
            deletion_verification
            if deletion_verified
            else _verify_sqs_batch_available()
        )
        batch_verified = batch_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': queue_verified and deletion_verified,
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'send-message-batch': {
                    'verified': batch_verified,
                    'verification': batch_verification if batch_verified else None,
                },
                'receive-message': {
                    'verified': batch_verified,
                    'verification': batch_verification if batch_verified else None,
                },
                'delete-message-batch': {
                    'verified': deletion_verified,
                    'verification': deletion_verification if deletion_verified else None,
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'queue-configuration':
        queue_verification = _verify_sqs_basics_queue_url()
        attributes_verification = _verify_sqs_configuration_attributes()
        tags_verification = _verify_sqs_configuration_tags()
        queue_verified = queue_verification.get('status') == 'passed'
        attributes_verified = attributes_verification.get('status') == 'passed'
        tags_verified = tags_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': queue_verified and attributes_verified and tags_verified,
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'set-queue-attributes': {
                    'verified': attributes_verified,
                    'verification': attributes_verification if attributes_verified else None,
                },
                'get-queue-attributes': {
                    'verified': attributes_verified,
                    'verification': attributes_verification if attributes_verified else None,
                },
                'tag-queue': {
                    'verified': tags_verified,
                    'verification': tags_verification if tags_verified else None,
                },
                'list-queue-tags': {
                    'verified': tags_verified,
                    'verification': tags_verification if tags_verified else None,
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'dead-letter-redrive':
        dlq_verification = _verify_sqs_redrive_queue(
            SQS_REDRIVE_DLQ_NAME,
            SQS_REDRIVE_DLQ_ARN,
        )
        source_verification = _verify_sqs_redrive_queue(
            SQS_REDRIVE_SOURCE_QUEUE_NAME,
            SQS_REDRIVE_SOURCE_QUEUE_ARN,
        )
        policy_verification = _verify_sqs_redrive_policy()
        failure_one_verification = _verify_sqs_redrive_marker(
            SQS_REDRIVE_FAILURE_ONE_CACHE_KEY,
            'The first failed receive was verified.',
            'The message has not completed its first failed receive.',
        )
        failure_two_verification = _verify_sqs_redrive_marker(
            SQS_REDRIVE_FAILURE_TWO_CACHE_KEY,
            'The second failed receive reached maxReceiveCount.',
            'The message has not completed its second failed receive.',
        )
        triggered_verification = _verify_sqs_redrive_marker(
            SQS_REDRIVE_TRIGGERED_CACHE_KEY,
            'SQS removed the over-received message from the source queue.',
            'The source queue has not yet evaluated the message beyond maxReceiveCount.',
        )
        dlq_message_verification = _verify_sqs_redrive_marker(
            SQS_REDRIVE_DLQ_OBSERVED_CACHE_KEY,
            'The failed payment event was observed in the dead-letter queue.',
            'The failed payment event has not been observed in the dead-letter queue.',
        )
        task_verification = _verify_sqs_redrive_task()
        returned_verification = _verify_sqs_redrive_marker(
            SQS_REDRIVE_RETURNED_CACHE_KEY,
            'The redriven payment event was received from the source queue.',
            'The redriven payment event has not been received from the source queue.',
        )
        dlq_verified = dlq_verification.get('status') == 'passed'
        source_verified = source_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        failure_one_verified = failure_one_verification.get('status') == 'passed'
        failure_two_verified = failure_two_verification.get('status') == 'passed'
        triggered_verified = triggered_verification.get('status') == 'passed'
        dlq_message_verified = dlq_message_verification.get('status') == 'passed'
        task_verified = task_verification.get('status') == 'passed'
        returned_verified = returned_verification.get('status') == 'passed'
        sent_verified = (
            failure_one_verified
            or failure_two_verified
            or dlq_message_verified
            or task_verified
            or returned_verified
        )
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                dlq_verified
                and source_verified
                and policy_verified
                and failure_one_verified
                and failure_two_verified
                and triggered_verified
                and dlq_message_verified
                and task_verified
                and returned_verified
            ),
            'steps': {
                'create-dlq': {
                    'verified': dlq_verified,
                    'verification': dlq_verification if dlq_verified else None,
                },
                'create-source-queue': {
                    'verified': source_verified,
                    'verification': source_verification if source_verified else None,
                },
                'set-redrive-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'get-redrive-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'send-message': {
                    'verified': sent_verified,
                    'verification': (
                        failure_one_verification if sent_verified else None
                    ),
                },
                'fail-message-once': {
                    'verified': failure_one_verified,
                    'verification': (
                        failure_one_verification if failure_one_verified else None
                    ),
                },
                'fail-message-twice': {
                    'verified': failure_two_verified,
                    'verification': (
                        failure_two_verification if failure_two_verified else None
                    ),
                },
                'trigger-dead-lettering': {
                    'verified': triggered_verified,
                    'verification': (
                        triggered_verification if triggered_verified else None
                    ),
                },
                'inspect-dlq': {
                    'verified': dlq_message_verified,
                    'verification': (
                        dlq_message_verification if dlq_message_verified else None
                    ),
                },
                'start-message-move-task': {
                    'verified': task_verified,
                    'verification': task_verification if task_verified else None,
                },
                'list-message-move-tasks': {
                    'verified': task_verified,
                    'verification': task_verification if task_verified else None,
                },
                'receive-redriven-message': {
                    'verified': returned_verified,
                    'verification': returned_verification if returned_verified else None,
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'fifo-ordering':
        queue_verification = _verify_sqs_fifo_queue()
        count_verification = _verify_sqs_fifo_message_count()
        ordering_verification = _verify_sqs_fifo_messages()
        queue_verified = queue_verification.get('status') == 'passed'
        count_verified = count_verification.get('status') == 'passed'
        ordering_verified = ordering_verification.get('status') == 'passed'
        deduplicated_verified = bool(cache.get(SQS_FIFO_DEDUPLICATED_CACHE_KEY))
        sent_verified = count_verified or ordering_verified
        sent_verification = (
            ordering_verification
            if ordering_verified
            else count_verification if count_verified else None
        )
        deduplication_verification = {
            'status': 'passed',
            'message': 'The duplicate send reused the original message ID and sequence number.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                queue_verified
                and deduplicated_verified
                and count_verified
                and ordering_verified
            ),
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'get-queue-attributes': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'send-created': {
                    'verified': sent_verified,
                    'verification': sent_verification,
                },
                'send-duplicate': {
                    'verified': deduplicated_verified,
                    'verification': (
                        deduplication_verification
                        if deduplicated_verified
                        else None
                    ),
                },
                'send-paid': {
                    'verified': sent_verified,
                    'verification': sent_verification,
                },
                'send-fulfilled': {
                    'verified': sent_verified,
                    'verification': sent_verification,
                },
                'inspect-message-count': {
                    'verified': count_verified,
                    'verification': count_verification if count_verified else None,
                },
                'receive-ordered-messages': {
                    'verified': ordering_verified,
                    'verification': (
                        ordering_verification if ordering_verified else None
                    ),
                },
            },
        }

    if service_key == 'sqs' and lab_key == 'purge-delete':
        deleted_verification = _verify_sqs_cleanup_deleted()
        deleted_verified = deleted_verification.get('status') == 'passed'
        populated_verified = bool(cache.get(SQS_CLEANUP_POPULATED_CACHE_KEY))
        purged_verified = bool(cache.get(SQS_CLEANUP_PURGED_CACHE_KEY))
        populated_verification = {
            'status': 'passed',
            'message': 'The cleanup queue was populated with three lab messages.',
        }
        purged_verification = {
            'status': 'passed',
            'message': 'The queue was emptied while its configuration remained intact.',
        }
        if deleted_verified:
            queue_verified = True
            queue_verification = {
                'status': 'passed',
                'message': 'The dedicated cleanup queue was created during this lab run.',
            }
        else:
            queue_verification = _verify_sqs_cleanup_queue(
                (
                    len(SQS_CLEANUP_MESSAGES)
                    if populated_verified and not purged_verified
                    else 0
                )
            )
            queue_verified = queue_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                populated_verified
                and purged_verified
                and deleted_verified
            ),
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'send-message-batch': {
                    'verified': populated_verified,
                    'verification': (
                        populated_verification if populated_verified else None
                    ),
                },
                'inspect-populated-queue': {
                    'verified': populated_verified,
                    'verification': (
                        populated_verification if populated_verified else None
                    ),
                },
                'purge-queue': {
                    'verified': purged_verified,
                    'verification': purged_verification if purged_verified else None,
                },
                'inspect-purged-queue': {
                    'verified': purged_verified,
                    'verification': purged_verification if purged_verified else None,
                },
                'delete-queue': {
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
    if service_key == 'sqs' and lab_key == 'create-queue':
        return _reset_sqs_create_queue()

    if service_key == 'sqs' and lab_key == 'message-lifecycle':
        return _reset_sqs_message_lifecycle()

    if service_key == 'sqs' and lab_key == 'visibility-timeout':
        return _reset_sqs_visibility_timeout()

    if service_key == 'sqs' and lab_key == 'delayed-message':
        return _reset_sqs_delayed_message()

    if service_key == 'sqs' and lab_key == 'batch-messages':
        return _reset_sqs_batch_messages()

    if service_key == 'sqs' and lab_key == 'queue-configuration':
        return _reset_sqs_queue_configuration()

    if service_key == 'sqs' and lab_key == 'dead-letter-redrive':
        return _reset_sqs_dead_letter_redrive()

    if service_key == 'sqs' and lab_key == 'fifo-ordering':
        return _reset_sqs_fifo_ordering()

    if service_key == 'sqs' and lab_key == 'purge-delete':
        return _reset_sqs_purge_delete()

    raise ValueError('Lab reset not found')
