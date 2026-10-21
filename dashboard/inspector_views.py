"""HTTP endpoints for the local inspection inbox."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error
from .inspector_api import (
    clear_ses_mailbox,
    lambda_log_events,
    lambda_log_groups,
    list_sqs_queues,
    read_ses_mailbox,
    receive_sqs_messages,
)

@require_http_methods(['GET'])
def inspector_sqs_queues(request):
    try:
        return JsonResponse(list_sqs_queues())
    except Exception as exc:
        return handle_action_error(exc, service='inspector', operation='list_sqs_queues')


@require_http_methods(['GET'])
def inspector_sqs_messages(request):
    try:
        return JsonResponse(receive_sqs_messages(
            request.GET.get('queue_url', ''),
            max_number=request.GET.get('max_number') or 10,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='inspector', operation='receive_sqs_messages')


@require_http_methods(['GET'])
def inspector_ses_messages(request):
    try:
        return JsonResponse(read_ses_mailbox())
    except Exception as exc:
        return handle_action_error(exc, service='inspector', operation='read_ses_mailbox')


@require_http_methods(['DELETE'])
def inspector_ses_messages_clear(request):
    try:
        return JsonResponse(clear_ses_mailbox())
    except Exception as exc:
        return handle_action_error(exc, service='inspector', operation='clear_ses_mailbox')


@require_http_methods(['GET'])
def inspector_lambda_log_groups(request):
    try:
        return JsonResponse(lambda_log_groups(
            function_name=request.GET.get('function_name', ''),
            limit=request.GET.get('limit') or 25,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='inspector', operation='lambda_log_groups')


@require_http_methods(['GET'])
def inspector_lambda_log_events(request):
    try:
        return JsonResponse(lambda_log_events(
            request.GET.get('log_group_name', ''),
            limit=request.GET.get('limit') or 50,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='inspector', operation='lambda_log_events')
