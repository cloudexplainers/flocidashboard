"""HTTP endpoints for the SNS publish workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .sns_api import (
    create_topic,
    delete_topic,
    get_topic_attributes,
    publish_message,
    set_subscription_attributes,
    subscribe,
    unsubscribe,
)


@require_http_methods(['POST'])
def sns_topics_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_topic(
            body.get('name', ''),
            **({'content_based_deduplication': body['content_based_deduplication']} if isinstance(body.get('content_based_deduplication'), bool) else {}),
            fifo=bool(body.get('fifo')),
            display_name=body.get('display_name') or None,
            kms_master_key_id=body.get('kms_master_key_id') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sns', operation='create_topic')


@require_http_methods(['GET', 'DELETE'])
def sns_topic_detail(request, topic_arn: str):
    try:
        if request.method == 'GET':
            return JsonResponse(get_topic_attributes(topic_arn))
        return JsonResponse(delete_topic(topic_arn))
    except Exception as exc:
        return handle_action_error(exc, service='sns', operation='delete_topic')


@require_http_methods(['POST'])
def sns_subscriptions_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(subscribe(
            body.get('topic_arn', ''),
            body.get('protocol', ''),
            body.get('endpoint', ''),
            **({'filter_policy_scope': body['filter_policy_scope']} if body.get('filter_policy_scope') else {}),
            filter_policy=body.get('filter_policy'),
            raw_message_delivery=bool(body.get('raw_message_delivery')),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sns', operation='subscribe')


@require_http_methods(['DELETE'])
def sns_subscription_detail(request, subscription_arn: str):
    try:
        return JsonResponse(unsubscribe(subscription_arn))
    except Exception as exc:
        return handle_action_error(exc, service='sns', operation='unsubscribe')


@require_http_methods(['PUT'])
def sns_subscription_attributes(request, subscription_arn: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(set_subscription_attributes(
            subscription_arn,
            body.get('attribute_name', ''),
            body.get('attribute_value'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sns', operation='set_subscription_attributes')


@require_http_methods(['POST'])
def sns_messages_publish(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(publish_message(
            body.get('topic_arn', ''),
            body.get('message', ''),
            subject=body.get('subject') or None,
            message_attributes=body.get('message_attributes') or None,
            message_structure=body.get('message_structure') or None,
            message_group_id=body.get('message_group_id') or None,
            message_deduplication_id=body.get('message_deduplication_id') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='sns', operation='publish_message')

