"""Interactive Firehose helpers for local delivery stream testing."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('firehose')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _record_bytes(value: Any) -> bytes:
    if value in (None, ''):
        raise ValueError('Record data is required')
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode('utf-8')
    return json.dumps(value, separators=(',', ':')).encode('utf-8')


def _records(value: Any) -> list[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError('Records must be a non-empty JSON array')
    return value


def _object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object')
    return value


def create_delivery_stream(
    name: str,
    *,
    tags: Any = None,
    s3_bucket_arn: str = '',
    role_arn: str = '',
    prefix: str = '',
    buffering_size_mb: Any = None,
    buffering_interval_seconds: Any = None,
) -> dict[str, Any]:
    """Create a Direct PUT stream with an Amazon S3 destination, as the console does.

    AWS requires a destination. Floci accepts a stream without one and delivers its
    records to its own floci-firehose-results bucket, which has no AWS equivalent, so a
    destination is required here.
    """
    clean_name = _required(name, 'Delivery stream name')
    bucket_arn = _required(s3_bucket_arn, 'S3 bucket ARN')
    if not bucket_arn.startswith('arn:aws:s3:::'):
        bucket_arn = f'arn:aws:s3:::{bucket_arn}'
    destination: dict[str, Any] = {
        'RoleARN': _required(role_arn, 'IAM role ARN'),
        'BucketARN': bucket_arn,
        # Console defaults: 5 MiB buffer size, 300 second buffer interval.
        'BufferingHints': {
            'SizeInMBs': _bounded_int(buffering_size_mb, 'Buffer size', 5, 1, 128),
            'IntervalInSeconds': _bounded_int(buffering_interval_seconds, 'Buffer interval', 300, 0, 900),
        },
        'CompressionFormat': 'UNCOMPRESSED',
    }
    if (prefix or '').strip():
        destination['Prefix'] = prefix.strip()
    kwargs: dict[str, Any] = {
        'DeliveryStreamName': clean_name,
        'DeliveryStreamType': 'DirectPut',
        'ExtendedS3DestinationConfiguration': destination,
    }
    if tags:
        if not isinstance(tags, list):
            raise ValueError('Tags must be a JSON array')
        kwargs['Tags'] = tags

    response = _client().create_delivery_stream(**kwargs)
    return {
        'name': clean_name,
        'arn': response.get('DeliveryStreamARN'),
        'response': _clean_response(response),
    }


def _bounded_int(value: Any, label: str, default: int, minimum: int, maximum: int) -> int:
    if value in (None, ''):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f'{label} must be a whole number') from None
    if not minimum <= number <= maximum:
        raise ValueError(f'{label} must be between {minimum} and {maximum}')
    return number


def delete_delivery_stream(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Delivery stream name')
    response = _client().delete_delivery_stream(DeliveryStreamName=clean_name)
    return {'name': clean_name, 'response': _clean_response(response)}


def update_destination(
    stream_name: str,
    current_version_id: str,
    destination_id: str,
    destination_update: Any,
) -> dict[str, Any]:
    clean_name = _required(stream_name, 'Delivery stream name')
    clean_version = _required(current_version_id, 'Current version ID')
    clean_destination = _required(destination_id, 'Destination ID')
    update = _object(destination_update, 'Destination update')

    response = _client().update_destination(
        DeliveryStreamName=clean_name,
        CurrentDeliveryStreamVersionId=clean_version,
        DestinationId=clean_destination,
        **update,
    )
    return {
        'stream_name': clean_name,
        'destination_id': clean_destination,
        'response': _clean_response(response),
    }


def put_record(stream_name: str, data: Any) -> dict[str, Any]:
    clean_name = _required(stream_name, 'Delivery stream name')
    response = _client().put_record(
        DeliveryStreamName=clean_name,
        Record={'Data': _record_bytes(data)},
    )
    return {
        'stream_name': clean_name,
        'record_id': response.get('RecordId'),
        'encrypted': response.get('Encrypted'),
        'response': _clean_response(response),
    }


def put_record_batch(stream_name: str, records: Any) -> dict[str, Any]:
    clean_name = _required(stream_name, 'Delivery stream name')
    clean_records = [{'Data': _record_bytes(record)} for record in _records(records)]
    response = _client().put_record_batch(
        DeliveryStreamName=clean_name,
        Records=clean_records,
    )
    request_responses = response.get('RequestResponses') or []
    return {
        'stream_name': clean_name,
        'record_count': len(clean_records),
        'failed_put_count': response.get('FailedPutCount', 0),
        'request_responses': _clean_response(request_responses),
        'response': _clean_response(response),
    }
