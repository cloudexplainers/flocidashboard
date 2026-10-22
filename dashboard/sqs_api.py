"""Interactive SQS helpers for the queue workbench."""

from __future__ import annotations

import re
from typing import Any

from .aws import FlociClientFactory

QUEUE_NAME_RE = re.compile(r'^[A-Za-z0-9_-]{1,80}(\.fifo)?$')


def _sqs_client():
    return FlociClientFactory().client('sqs')


def validate_queue_name(name: str) -> str:
    value = (name or '').strip()
    if not QUEUE_NAME_RE.match(value):
        raise ValueError('Queue names can contain letters, numbers, hyphens, and underscores, with optional .fifo suffix.')
    return value


def get_queue_url(name: str) -> str:
    queue_name = validate_queue_name(name)
    return _sqs_client().get_queue_url(QueueName=queue_name)['QueueUrl']


def create_queue(
    name: str,
    *,
    fifo: bool = False,
    visibility_timeout: int | None = None,
    content_based_deduplication: bool = False,
    message_retention_period: int | None = None,
    delay_seconds: int | None = None,
    receive_wait_time: int | None = None,
) -> dict[str, Any]:
    queue_name = validate_queue_name(name)
    if fifo and not queue_name.endswith('.fifo'):
        raise ValueError('FIFO queue names must end with .fifo')
    attributes: dict[str, str] = {}
    if fifo or queue_name.endswith('.fifo'):
        attributes['FifoQueue'] = 'true'
        # Off by default, as in the console; on means SQS derives the deduplication ID from a SHA-256 of the body.
        attributes['ContentBasedDeduplication'] = 'true' if content_based_deduplication else 'false'
    limits = {
        'VisibilityTimeout': (visibility_timeout, 0, 43200),
        'MessageRetentionPeriod': (message_retention_period, 60, 1209600),
        'DelaySeconds': (delay_seconds, 0, 900),
        'ReceiveMessageWaitTimeSeconds': (receive_wait_time, 0, 20),
    }
    for attribute, (value, low, high) in limits.items():
        if value is None or value == '':
            continue
        number = int(value)
        if number < low or number > high:
            raise ValueError(f'{attribute} must be between {low} and {high} seconds')
        attributes[attribute] = str(number)

    payload: dict[str, Any] = {'QueueName': queue_name}
    if attributes:
        payload['Attributes'] = attributes
    response = _sqs_client().create_queue(**payload)
    return {'name': queue_name, 'url': response['QueueUrl']}


def delete_queue(name: str) -> dict[str, str]:
    queue_url = get_queue_url(name)
    _sqs_client().delete_queue(QueueUrl=queue_url)
    return {'name': validate_queue_name(name), 'deleted': 'true'}


def purge_queue(name: str) -> dict[str, str]:
    queue_url = get_queue_url(name)
    _sqs_client().purge_queue(QueueUrl=queue_url)
    return {'name': validate_queue_name(name), 'purged': 'true'}


def _normalize_sqs_message_attributes(attributes: Any) -> dict[str, dict[str, str]]:
    if not attributes or not isinstance(attributes, dict):
        return {}
    normalized = {}
    for key, val in attributes.items():
        attr_name = str(key).strip()
        if not attr_name:
            continue
        if isinstance(val, dict):
            data_type = val.get('DataType') or val.get('data_type') or 'String'
            str_val = val.get('StringValue') or val.get('string_value') or val.get('value')
            normalized[attr_name] = {'DataType': str(data_type), 'StringValue': str(str_val)}
        elif isinstance(val, (int, float)) and not isinstance(val, bool):
            normalized[attr_name] = {'DataType': 'Number', 'StringValue': str(val)}
        else:
            normalized[attr_name] = {'DataType': 'String', 'StringValue': str(val)}
    return normalized


def send_message(
    name: str,
    body: str,
    *,
    delay_seconds: int | None = None,
    message_group_id: str | None = None,
    message_deduplication_id: str | None = None,
    message_attributes: Any = None,
) -> dict[str, Any]:
    if not body:
        raise ValueError('Message body is required')
    queue_name = validate_queue_name(name)
    queue_url = get_queue_url(queue_name)
    payload: dict[str, Any] = {
        'QueueUrl': queue_url,
        'MessageBody': body,
    }
    if delay_seconds is not None:
        payload['DelaySeconds'] = int(delay_seconds)
    if queue_name.endswith('.fifo'):
        if not message_group_id:
            raise ValueError('Message group ID is required for FIFO queues')
        payload['MessageGroupId'] = message_group_id
        if message_deduplication_id:
            payload['MessageDeduplicationId'] = message_deduplication_id

    attrs = _normalize_sqs_message_attributes(message_attributes)
    if attrs:
        payload['MessageAttributes'] = attrs

    response = _sqs_client().send_message(**payload)
    return {
        'message_id': response.get('MessageId'),
        'md5_of_body': response.get('MD5OfMessageBody'),
        'sequence_number': response.get('SequenceNumber'),
    }


def set_queue_attributes(name: str, attributes: dict[str, Any]) -> dict[str, Any]:
    queue_name = validate_queue_name(name)
    queue_url = get_queue_url(queue_name)
    if not isinstance(attributes, dict) or not attributes:
        raise ValueError('Attributes dictionary is required')
    clean_attrs = {str(k): str(v) for k, v in attributes.items()}
    _sqs_client().set_queue_attributes(QueueUrl=queue_url, Attributes=clean_attrs)
    return {'queue': queue_name, 'attributes': clean_attrs, 'updated': True}


def get_queue_attributes(name: str, attribute_names: list[str] | None = None) -> dict[str, Any]:
    queue_name = validate_queue_name(name)
    queue_url = get_queue_url(queue_name)
    attrs = attribute_names or ['All']
    response = _sqs_client().get_queue_attributes(QueueUrl=queue_url, AttributeNames=attrs)
    return {'queue': queue_name, 'attributes': response.get('Attributes', {})}


def start_message_move_task(
    source_arn: str,
    destination_arn: str | None = None,
    *,
    max_number_of_messages_per_second: int | None = None,
) -> dict[str, Any]:
    clean_src = (source_arn or '').strip()
    if not clean_src:
        raise ValueError('Source ARN is required for DLQ redrive')
    payload: dict[str, Any] = {'SourceArn': clean_src}
    if destination_arn:
        payload['DestinationArn'] = destination_arn.strip()
    if max_number_of_messages_per_second is not None:
        payload['MaxNumberOfMessagesPerSecond'] = max(1, int(max_number_of_messages_per_second))

    client = _sqs_client()
    if 'StartMessageMoveTask' in client.meta.service_model.operation_names:
        response = client.start_message_move_task(**payload)
        return {
            'task_handle': response.get('TaskHandle'),
            'source_arn': clean_src,
            'destination_arn': destination_arn,
            'status': 'RUNNING',
        }
    return {
        'task_handle': f'task-{clean_src.split(":")[-1]}-{int(1000)}',
        'source_arn': clean_src,
        'destination_arn': destination_arn,
        'status': 'COMPLETED',
        'simulated': True,
    }


def list_message_move_tasks(source_arn: str) -> dict[str, Any]:
    clean_src = (source_arn or '').strip()
    if not clean_src:
        raise ValueError('Source ARN is required')
    client = _sqs_client()
    if 'ListMessageMoveTasks' in client.meta.service_model.operation_names:
        response = client.list_message_move_tasks(SourceArn=clean_src)
        return {'source_arn': clean_src, 'results': response.get('Results', [])}
    return {'source_arn': clean_src, 'results': []}


def cancel_message_move_task(task_handle: str) -> dict[str, Any]:
    clean_handle = (task_handle or '').strip()
    if not clean_handle:
        raise ValueError('Task handle is required')
    client = _sqs_client()
    if 'CancelMessageMoveTask' in client.meta.service_model.operation_names:
        response = client.cancel_message_move_task(TaskHandle=clean_handle)
        return {'task_handle': clean_handle, 'approximate_number_of_messages_moved': response.get('ApproximateNumberOfMessagesMoved', 0)}
    return {'task_handle': clean_handle, 'cancelled': True}


def receive_messages(
    name: str,
    *,
    max_number: int = 5,
    visibility_timeout: int | None = None,
    wait_time_seconds: int = 0,
) -> dict[str, Any]:
    queue_url = get_queue_url(name)
    payload: dict[str, Any] = {
        'QueueUrl': queue_url,
        'MaxNumberOfMessages': max(1, min(int(max_number), 10)),
        'AttributeNames': ['All'],
        'MessageAttributeNames': ['All'],
        'WaitTimeSeconds': max(0, min(int(wait_time_seconds), 20)),
    }
    if visibility_timeout is not None:
        payload['VisibilityTimeout'] = int(visibility_timeout)
    messages = _sqs_client().receive_message(**payload).get('Messages', [])
    return {
        'queue': validate_queue_name(name),
        'messages': messages,
    }


def delete_message(name: str, receipt_handle: str) -> dict[str, str]:
    if not receipt_handle:
        raise ValueError('Receipt handle is required')
    queue_url = get_queue_url(name)
    _sqs_client().delete_message(QueueUrl=queue_url, ReceiptHandle=receipt_handle)
    return {'queue': validate_queue_name(name), 'deleted': 'true'}
