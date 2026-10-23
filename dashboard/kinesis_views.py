"""HTTP endpoints for the Kinesis stream workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .kinesis_api import create_stream, get_records, merge_shards, put_record, split_shard, update_shard_count


@require_http_methods(['POST'])
def kinesis_streams_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_stream(
            body.get('name', ''),
            shard_count=body.get('shard_count') or 1,
            mode=body.get('mode') or 'PROVISIONED',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='kinesis', operation='create_stream')


@require_http_methods(['POST'])
def kinesis_records_put(request, stream_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_record(
            stream_name,
            body.get('partition_key', ''),
            body.get('data'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='kinesis', operation='put_record')


@require_http_methods(['POST'])
def kinesis_records_get(request, stream_name: str, shard_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_records(
            stream_name,
            shard_id,
            iterator_type=body.get('iterator_type') or 'TRIM_HORIZON',
            limit=body.get('limit') or 25,
            sequence_number=body.get('sequence_number') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='kinesis', operation='get_records')


@require_http_methods(['POST'])
def kinesis_shard_count(request, stream_name: str):
    try:
        return JsonResponse(update_shard_count(stream_name, parse_json_body(request).get('target_shard_count')))
    except Exception as exc:
        return handle_action_error(exc, service='kinesis', operation='update_shard_count')


@require_http_methods(['POST'])
def kinesis_shard_split(request, stream_name: str, shard_id: str):
    try:
        return JsonResponse(split_shard(stream_name, shard_id, parse_json_body(request).get('new_starting_hash_key')))
    except Exception as exc:
        return handle_action_error(exc, service='kinesis', operation='split_shard')


@require_http_methods(['POST'])
def kinesis_shard_merge(request, stream_name: str, shard_id: str):
    try:
        return JsonResponse(merge_shards(stream_name, shard_id, parse_json_body(request).get('adjacent_shard_id', '')))
    except Exception as exc:
        return handle_action_error(exc, service='kinesis', operation='merge_shards')
