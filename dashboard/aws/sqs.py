"""SQS inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _string_items


def _sqs_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def sqs_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    sqs = factory.client('sqs')
    queue_urls = sqs.list_queues().get('QueueUrls', [])

    def queue_name(queue_url: str) -> str:
        return queue_url.rstrip('/').rsplit('/', 1)[-1]

    def queue_detail(queue_url: str) -> dict[str, Any]:
        attributes = _sqs_optional(
            lambda: sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['All']).get('Attributes', {}),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
        tags = _sqs_optional(
            lambda: sqs.list_queue_tags(QueueUrl=queue_url).get('Tags', {}),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
        dead_letter_sources = _sqs_optional(
            lambda: sqs.list_dead_letter_source_queues(QueueUrl=queue_url).get('queueUrls', []),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
        message_move_tasks = _sqs_optional(
            lambda: sqs.list_message_move_tasks(SourceArn=attributes.get('QueueArn')).get('Results', [])
            if isinstance(attributes, dict) and attributes.get('QueueArn') else [],
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )

        return {
            'name': queue_name(queue_url),
            'url': queue_url,
            'arn': attributes.get('QueueArn') if isinstance(attributes, dict) else None,
            'fifo': queue_url.endswith('.fifo') or (
                isinstance(attributes, dict) and attributes.get('FifoQueue') == 'true'
            ),
            'attributes': attributes,
            'tags': tags,
            'dead_letter_source_queues': dead_letter_sources,
            'message_move_tasks': message_move_tasks,
            'approximate_messages': int(attributes.get('ApproximateNumberOfMessages', 0))
            if isinstance(attributes, dict) else 0,
            'approximate_not_visible': int(attributes.get('ApproximateNumberOfMessagesNotVisible', 0))
            if isinstance(attributes, dict) else 0,
            'approximate_delayed': int(attributes.get('ApproximateNumberOfMessagesDelayed', 0))
            if isinstance(attributes, dict) else 0,
        }

    queues = [queue_detail(url) for url in queue_urls]

    return {
        'summary': {
            'queues': len(queues),
            'fifo_queues': sum(1 for queue in queues if queue.get('fifo')),
            'visible_messages': sum(queue.get('approximate_messages') or 0 for queue in queues),
            'in_flight_messages': sum(queue.get('approximate_not_visible') or 0 for queue in queues),
            'delayed_messages': sum(queue.get('approximate_delayed') or 0 for queue in queues),
        },
        'queues': queues,
        'supported': [
            'CreateQueue',
            'DeleteQueue',
            'ListQueues',
            'GetQueueUrl',
            'GetQueueAttributes',
            'SetQueueAttributes',
            'SendMessage',
            'SendMessageBatch',
            'ReceiveMessage',
            'DeleteMessage',
            'DeleteMessageBatch',
            'ChangeMessageVisibility',
            'ChangeMessageVisibilityBatch',
            'PurgeQueue',
            'TagQueue',
            'UntagQueue',
            'ListQueueTags',
            'ListDeadLetterSourceQueues',
            'StartMessageMoveTask',
            'ListMessageMoveTasks',
            'CancelMessageMoveTask',
        ],
        'configuration': {
            'default_visibility_timeout_seconds': 30,
            'max_message_size_bytes': 1048576,
            'queue_url_format': f'{factory.endpoint_url.rstrip("/")}/000000000000/<queue-name>',
        },
        'notes': [
            'FIFO deduplication is scoped per MessageGroupId when DeduplicationScope=messageGroup.',
            'AWSTraceHeader is persisted and returned as an SQS system attribute.',
        ],
    }


def list_sqs_queues(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    urls = factory.client('sqs').list_queues().get('QueueUrls', [])
    return _string_items(urls, 'url')
