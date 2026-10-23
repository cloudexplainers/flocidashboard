"""HTTP endpoints for the SQS queue workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .sqs_api import (
    cancel_message_move_task,
    create_queue,
    delete_message,
    delete_queue,
    get_queue_attributes,
    list_message_move_tasks,
    purge_queue,
    receive_messages,
    send_message,
    set_queue_attributes,
    start_message_move_task,
)


@require_http_methods(['POST'])
def sqs_queues_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_queue(
            body.get('name') or body.get('queue_name') or '',
            fifo=bool(body.get('fifo')),
            visibility_timeout=body.get('visibility_timeout'),
            content_based_deduplication=body.get('content_based_deduplication') is True,
            message_retention_period=body.get('message_retention_period'),
            delay_seconds=body.get('delay_seconds'),
            receive_wait_time=body.get('receive_wait_time'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='create_queue')


@require_http_methods(['DELETE'])
def sqs_queue_delete(request, queue_name: str):
    try:
        return JsonResponse(delete_queue(queue_name))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='delete_queue')


@require_http_methods(['POST'])
def sqs_queue_purge(request, queue_name: str):
    try:
        return JsonResponse(purge_queue(queue_name))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='purge_queue')


@require_http_methods(['POST'])
def sqs_messages_send(request, queue_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(send_message(
            queue_name,
            body.get('body') or body.get('message_body') or body.get('message') or '',
            delay_seconds=body.get('delay_seconds') or None,
            message_group_id=body.get('message_group_id') or None,
            message_deduplication_id=body.get('message_deduplication_id') or None,
            message_attributes=body.get('message_attributes') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='send_message')


@require_http_methods(['GET', 'POST'])
def sqs_messages_receive(request, queue_name: str):
    try:
        body = parse_json_body(request) if request.method == 'POST' and request.body else {}
        max_number = body.get('max_number') or request.GET.get('max_number') or body.get('max_number_of_messages') or 5
        vis_timeout = body.get('visibility_timeout') or request.GET.get('visibility_timeout') or None
        wait_time = body.get('wait_time_seconds') or request.GET.get('wait_time_seconds') or 0
        return JsonResponse(receive_messages(
            queue_name,
            max_number=int(max_number),
            visibility_timeout=int(vis_timeout) if vis_timeout is not None else None,
            wait_time_seconds=int(wait_time),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='receive_messages')


@require_http_methods(['DELETE'])
def sqs_message_delete(request, queue_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(delete_message(queue_name, body.get('receipt_handle', '')))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='delete_message')


@require_http_methods(['GET', 'POST', 'PUT'])
def sqs_queue_attributes(request, queue_name: str):
    try:
        if request.method == 'GET':
            return JsonResponse(get_queue_attributes(queue_name))
        body = parse_json_body(request)
        attributes = body.get('attributes') or body
        return JsonResponse(set_queue_attributes(queue_name, attributes))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='set_queue_attributes')


def _queue_arn(queue_name: str) -> str:
    # The queue's real ARN, not one built from a guessed account and Region.
    return get_queue_attributes(queue_name, ['QueueArn'])['attributes'].get('QueueArn') or ''


@require_http_methods(['GET', 'POST'])
def sqs_queue_redrive(request, queue_name: str):
    try:
        if request.method == 'GET':
            source_arn = request.GET.get('source_arn') or _queue_arn(queue_name)
            return JsonResponse(list_message_move_tasks(source_arn))
        body = parse_json_body(request)
        source_arn = body.get('source_arn') or _queue_arn(queue_name)
        return JsonResponse(start_message_move_task(
            source_arn=source_arn,
            destination_arn=body.get('destination_arn') or None,
            max_number_of_messages_per_second=body.get('max_number_of_messages_per_second') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='start_message_move_task')


@require_http_methods(['DELETE'])
def sqs_task_cancel(request, task_handle: str):
    try:
        return JsonResponse(cancel_message_move_task(task_handle))
    except Exception as exc:
        return handle_action_error(exc, service='sqs', operation='cancel_message_move_task')

