"""Interactive Kinesis helpers for stream testing."""

from __future__ import annotations

import base64
import json
from datetime import datetime
from typing import Any

from .aws import FlociClientFactory

SHARD_ITERATOR_TYPES = {
    'TRIM_HORIZON',
    'LATEST',
    'AT_SEQUENCE_NUMBER',
    'AFTER_SEQUENCE_NUMBER',
    'AT_TIMESTAMP',
}


def _client():
    return FlociClientFactory().client('kinesis')


def _clean_required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _positive_int(value: Any, label: str, default: int) -> int:
    if value in (None, ''):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a number') from exc
    if number <= 0:
        raise ValueError(f'{label} must be greater than zero')
    return number


def _record_data(value: Any) -> bytes:
    if value in (None, ''):
        raise ValueError('Record data is required')
    if isinstance(value, str):
        return value.encode('utf-8')
    return json.dumps(value).encode('utf-8')


def _decode_record_data(value: Any) -> dict[str, Any]:
    raw_bytes: bytes
    if isinstance(value, bytes):
        raw_bytes = value
    elif hasattr(value, 'read'):
        raw_bytes = value.read()
    elif isinstance(value, str):
        try:
            raw_bytes = base64.b64decode(value, validate=True)
        except (ValueError, TypeError):
            raw_bytes = value.encode('utf-8')
    else:
        raw_bytes = bytes(value or b'')

    text = raw_bytes.decode('utf-8', errors='replace')
    parsed: Any = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    return {
        'text': text,
        'json': parsed,
        'base64': base64.b64encode(raw_bytes).decode('ascii'),
        'size_bytes': len(raw_bytes),
    }


def _json_datetime(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def create_stream(name: str, *, shard_count: Any = 1, mode: str = 'PROVISIONED') -> dict[str, Any]:
    stream_name = _clean_required(name, 'Stream name')
    clean_mode = (mode or 'PROVISIONED').strip().upper()
    kwargs: dict[str, Any] = {'StreamName': stream_name}

    if clean_mode == 'ON_DEMAND':
        kwargs['StreamModeDetails'] = {'StreamMode': 'ON_DEMAND'}
    elif clean_mode == 'PROVISIONED':
        kwargs['ShardCount'] = _positive_int(shard_count, 'Shard count', 1)
    else:
        raise ValueError('Stream mode must be PROVISIONED or ON_DEMAND')

    response = _client().create_stream(**kwargs)
    return {
        'name': stream_name,
        'mode': clean_mode,
        'shard_count': kwargs.get('ShardCount'),
        'response': response,
    }


def put_record(stream_name: str, partition_key: str, data: Any) -> dict[str, Any]:
    clean_stream = _clean_required(stream_name, 'Stream name')
    clean_partition_key = _clean_required(partition_key, 'Partition key')
    response = _client().put_record(
        StreamName=clean_stream,
        PartitionKey=clean_partition_key,
        Data=_record_data(data),
    )
    return {
        'stream_name': clean_stream,
        'partition_key': clean_partition_key,
        'shard_id': response.get('ShardId'),
        'sequence_number': response.get('SequenceNumber'),
        'encryption_type': response.get('EncryptionType'),
    }


def get_records(
    stream_name: str,
    shard_id: str,
    *,
    iterator_type: str = 'TRIM_HORIZON',
    limit: Any = 25,
    sequence_number: str = '',
) -> dict[str, Any]:
    clean_stream = _clean_required(stream_name, 'Stream name')
    clean_shard = _clean_required(shard_id, 'Shard ID')
    clean_iterator_type = (iterator_type or 'TRIM_HORIZON').strip().upper()
    if clean_iterator_type not in SHARD_ITERATOR_TYPES:
        raise ValueError('Shard iterator type is not supported')

    iterator_kwargs: dict[str, Any] = {
        'StreamName': clean_stream,
        'ShardId': clean_shard,
        'ShardIteratorType': clean_iterator_type,
    }
    if clean_iterator_type in {'AT_SEQUENCE_NUMBER', 'AFTER_SEQUENCE_NUMBER'}:
        iterator_kwargs['StartingSequenceNumber'] = _clean_required(sequence_number, 'Sequence number')

    client = _client()
    shard_iterator = client.get_shard_iterator(**iterator_kwargs).get('ShardIterator')
    if not shard_iterator:
        raise ValueError('Shard iterator was not returned')

    response = client.get_records(
        ShardIterator=shard_iterator,
        Limit=min(_positive_int(limit, 'Limit', 25), 100),
    )
    records = []
    for record in response.get('Records', []):
        data = _decode_record_data(record.get('Data'))
        records.append({
            'sequence_number': record.get('SequenceNumber'),
            'partition_key': record.get('PartitionKey'),
            'approximate_arrival_timestamp': _json_datetime(record.get('ApproximateArrivalTimestamp')),
            'data_text': data['text'],
            'data_json': data['json'],
            'data_base64': data['base64'],
            'size_bytes': data['size_bytes'],
            'encryption_type': record.get('EncryptionType'),
        })

    return {
        'stream_name': clean_stream,
        'shard_id': clean_shard,
        'iterator_type': clean_iterator_type,
        'records': records,
        'millis_behind_latest': response.get('MillisBehindLatest'),
        'next_shard_iterator': response.get('NextShardIterator'),
    }


def _open_shards(stream: str) -> list[dict[str, Any]]:
    shards = []
    kwargs: dict[str, Any] = {'StreamName': stream}
    while True:
        response = _client().list_shards(**kwargs)
        shards.extend(response.get('Shards', []))
        if not response.get('NextToken'):
            break
        kwargs = {'NextToken': response['NextToken']}
    return [shard for shard in shards if not shard.get('SequenceNumberRange', {}).get('EndingSequenceNumber')]


def update_shard_count(stream_name: str, target_shard_count: Any) -> dict[str, Any]:
    stream = _clean_required(stream_name, 'Stream name')
    target = _positive_int(target_shard_count, 'Target shard count', 0)
    response = _client().update_shard_count(StreamName=stream, TargetShardCount=target, ScalingType='UNIFORM_SCALING')
    return {
        'stream_name': stream,
        'current_shard_count': response.get('CurrentShardCount'),
        'target_shard_count': response.get('TargetShardCount'),
    }


def split_shard(stream_name: str, shard_id: str, new_starting_hash_key: Any = None) -> dict[str, Any]:
    """Splits an open shard; without a hash key, at the middle of its range (an even split)."""
    stream = _clean_required(stream_name, 'Stream name')
    shard = _clean_required(shard_id, 'Shard ID')
    match = next((item for item in _open_shards(stream) if item['ShardId'] == shard), None)
    if not match:
        raise ValueError(f'{shard} is not an open shard of {stream}')
    low = int(match['HashKeyRange']['StartingHashKey'])
    high = int(match['HashKeyRange']['EndingHashKey'])
    if new_starting_hash_key in (None, ''):
        key = (low + high + 1) // 2
    else:
        try:
            key = int(str(new_starting_hash_key).strip())
        except ValueError as exc:
            raise ValueError('New starting hash key must be a whole number') from exc
    # AWS requires the new key to fall strictly inside the shard's range; Floci doesn't check.
    if not low < key <= high:
        raise ValueError(f'New starting hash key must be greater than {low} and at most {high}')
    _client().split_shard(StreamName=stream, ShardToSplit=shard, NewStartingHashKey=str(key))
    return {'stream_name': stream, 'shard_id': shard, 'new_starting_hash_key': str(key)}


def merge_shards(stream_name: str, shard_id: str, adjacent_shard_id: str) -> dict[str, Any]:
    stream = _clean_required(stream_name, 'Stream name')
    first = _clean_required(shard_id, 'Shard ID')
    second = _clean_required(adjacent_shard_id, 'Adjacent shard ID')
    shards = {item['ShardId']: item for item in _open_shards(stream)}
    if first not in shards or second not in shards:
        raise ValueError('Both shards must be open shards of this stream')
    a, b = shards[first]['HashKeyRange'], shards[second]['HashKeyRange']
    if int(a['EndingHashKey']) + 1 != int(b['StartingHashKey']) and int(b['EndingHashKey']) + 1 != int(a['StartingHashKey']):
        raise ValueError('Shards can only be merged with an adjacent shard (their hash key ranges must touch)')
    _client().merge_shards(StreamName=stream, ShardToMerge=first, AdjacentShardToMerge=second)
    return {'stream_name': stream, 'shard_id': first, 'adjacent_shard_id': second}
