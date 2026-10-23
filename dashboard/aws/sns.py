"""SNS inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _sns_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def sns_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    sns = factory.client('sns')
    topics = _safe_value(lambda: core._paginate(sns, 'list_topics', 'Topics'), [])
    subscriptions = _safe_value(lambda: core._paginate(sns, 'list_subscriptions', 'Subscriptions'), [])

    def topic_name(topic_arn: Optional[str]) -> str:
        return topic_arn.rsplit(':', 1)[-1] if topic_arn else 'Unnamed topic'

    def topic_detail(topic: dict[str, Any]) -> dict[str, Any]:
        arn = topic.get('TopicArn')
        topic_subscriptions = _sns_optional(
            lambda: core._paginate(sns, 'list_subscriptions_by_topic', 'Subscriptions', TopicArn=arn),
            {'NotFound', 'InvalidParameter'},
        )

        return {
            'name': topic_name(arn),
            'arn': arn,
            'attributes': _sns_optional(
                lambda: sns.get_topic_attributes(TopicArn=arn).get('Attributes', {}),
                {'NotFound', 'InvalidParameter'},
            ),
            'tags': _sns_optional(
                lambda: sns.list_tags_for_resource(ResourceArn=arn).get('Tags', []),
                {'NotFound', 'InvalidParameter'},
            ),
            'subscriptions': topic_subscriptions,
            'subscription_count': len(topic_subscriptions) if isinstance(topic_subscriptions, list) else 0,
        }

    def subscription_detail(subscription: dict[str, Any]) -> dict[str, Any]:
        arn = subscription.get('SubscriptionArn')
        attributes = None

        if arn and arn != 'PendingConfirmation':
            attributes = _sns_optional(
                lambda: sns.get_subscription_attributes(SubscriptionArn=arn).get('Attributes', {}),
                {'NotFound', 'InvalidParameter'},
            )

        return {
            'name': arn or subscription.get('Endpoint') or 'Subscription',
            'arn': arn,
            'topic_arn': subscription.get('TopicArn'),
            'protocol': subscription.get('Protocol'),
            'endpoint': subscription.get('Endpoint'),
            'owner': subscription.get('Owner'),
            'attributes': attributes,
        }

    detailed_topics = [topic_detail(topic) for topic in topics]
    detailed_subscriptions = [subscription_detail(subscription) for subscription in subscriptions]

    return {
        'summary': {
            'topics': len(detailed_topics),
            'subscriptions': len(detailed_subscriptions),
            'sqs_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('protocol') == 'sqs'),
            'lambda_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('protocol') == 'lambda'),
            'http_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('protocol') in {'http', 'https'}),
            'pending_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('arn') == 'PendingConfirmation'),
        },
        'topics': detailed_topics,
        'subscriptions': detailed_subscriptions,
        'supported': [
            'CreateTopic',
            'DeleteTopic',
            'ListTopics',
            'GetTopicAttributes',
            'SetTopicAttributes',
            'Subscribe',
            'Unsubscribe',
            'ListSubscriptions',
            'ListSubscriptionsByTopic',
            'GetSubscriptionAttributes',
            'SetSubscriptionAttributes',
            'ConfirmSubscription',
            'Publish',
            'PublishBatch',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'fanout': [
            'Floci supports SNS to SQS fan-out and delivers published messages immediately.',
        ],
        'protocols': ['Query XML', 'JSON 1.0'],
    }


def list_sns_topics(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {'arn': topic.get('TopicArn')}
        for topic in factory.client('sns').get_paginator('list_topics').paginate().build_full_result().get('Topics', [])
    ]
