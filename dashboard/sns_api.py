"""Interactive SNS helpers for the publish workbench."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory


def _sns_client():
    return FlociClientFactory().client('sns')


def validate_topic_arn(topic_arn: str) -> str:
    value = (topic_arn or '').strip()
    if not value or ':sns:' not in value:
        raise ValueError('A valid SNS topic ARN is required')
    return value


def _message_attribute(name: str, value: Any) -> dict[str, str]:
    if isinstance(value, dict):
        data_type = value.get('DataType') or value.get('data_type') or value.get('type') or 'String'
        string_value = value.get('StringValue')
        if string_value is None:
            string_value = value.get('string_value')
        if string_value is None:
            string_value = value.get('value')
        if string_value is None and 'BinaryValue' not in value and 'binary_value' not in value:
            raise ValueError(f'Message attribute {name} requires a value')
        attribute = {'DataType': str(data_type)}
        if 'BinaryValue' in value:
            attribute['BinaryValue'] = value['BinaryValue']
        elif 'binary_value' in value:
            attribute['BinaryValue'] = value['binary_value']
        else:
            attribute['StringValue'] = str(string_value)
        return attribute

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {'DataType': 'Number', 'StringValue': str(value)}

    return {'DataType': 'String', 'StringValue': str(value)}


def normalize_message_attributes(attributes: Any) -> dict[str, dict[str, str]]:
    if attributes in (None, ''):
        return {}
    if not isinstance(attributes, dict):
        raise ValueError('Message attributes must be a JSON object')

    normalized = {}
    for name, value in attributes.items():
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError('Message attribute names cannot be empty')
        normalized[clean_name] = _message_attribute(clean_name, value)
    return normalized


def create_topic(
    name: str,
    *,
    fifo: bool = False,
    display_name: str | None = None,
    kms_master_key_id: str | None = None,
    content_based_deduplication: bool = False,
) -> dict[str, Any]:
    clean_name = (name or '').strip()
    if not clean_name:
        raise ValueError('Topic name is required')
    if fifo and not clean_name.endswith('.fifo'):
        raise ValueError('FIFO topic names must end with .fifo')

    attributes: dict[str, str] = {}
    if fifo or clean_name.endswith('.fifo'):
        attributes['FifoTopic'] = 'true'
        attributes['ContentBasedDeduplication'] = 'true' if content_based_deduplication else 'false'
    if display_name:
        attributes['DisplayName'] = display_name.strip()
    if kms_master_key_id:
        attributes['KmsMasterKeyId'] = kms_master_key_id.strip()

    payload: dict[str, Any] = {'Name': clean_name}
    if attributes:
        payload['Attributes'] = attributes

    response = _sns_client().create_topic(**payload)
    return {
        'name': clean_name,
        'topic_arn': response.get('TopicArn'),
    }


def delete_topic(topic_arn: str) -> dict[str, Any]:
    arn = validate_topic_arn(topic_arn)
    _sns_client().delete_topic(TopicArn=arn)
    return {'topic_arn': arn, 'deleted': True}


def get_topic_attributes(topic_arn: str) -> dict[str, Any]:
    arn = validate_topic_arn(topic_arn)
    response = _sns_client().get_topic_attributes(TopicArn=arn)
    return {'topic_arn': arn, 'attributes': response.get('Attributes', {})}


def subscribe(
    topic_arn: str,
    protocol: str,
    endpoint: str,
    *,
    filter_policy: Any = None,
    raw_message_delivery: bool = False,
    filter_policy_scope: str = 'MessageAttributes',
) -> dict[str, Any]:
    arn = validate_topic_arn(topic_arn)
    clean_proto = (protocol or '').strip().lower()
    clean_endpoint = (endpoint or '').strip()
    if not clean_proto:
        raise ValueError('Protocol is required (e.g. sqs, lambda, http, https, email)')
    if not clean_endpoint:
        raise ValueError('Endpoint target ARN/URL/email is required')

    attributes: dict[str, str] = {}
    if raw_message_delivery:
        attributes['RawMessageDelivery'] = 'true'
    if filter_policy is not None:
        policy = json.loads(filter_policy) if isinstance(filter_policy, str) else filter_policy
        if not isinstance(policy, dict):
            raise ValueError('Filter policy must be a JSON object')
        if filter_policy_scope not in ('MessageAttributes', 'MessageBody'):
            raise ValueError('Filter policy scope must be MessageAttributes or MessageBody')
        # An empty policy means "no filtering"; the console omits the attribute in that case.
        if policy:
            attributes['FilterPolicy'] = json.dumps(policy)
            attributes['FilterPolicyScope'] = filter_policy_scope
    if raw_message_delivery and clean_proto not in ('sqs', 'http', 'https', 'firehose'):
        raise ValueError('Raw message delivery is not available for this protocol')

    payload: dict[str, Any] = {
        'TopicArn': arn,
        'Protocol': clean_proto,
        'Endpoint': clean_endpoint,
        'ReturnSubscriptionArn': True,
    }
    if attributes:
        payload['Attributes'] = attributes

    response = _sns_client().subscribe(**payload)
    return {
        'topic_arn': arn,
        'protocol': clean_proto,
        'endpoint': clean_endpoint,
        'subscription_arn': response.get('SubscriptionArn'),
    }


def unsubscribe(subscription_arn: str) -> dict[str, Any]:
    clean_arn = (subscription_arn or '').strip()
    if not clean_arn or clean_arn == 'PendingConfirmation':
        raise ValueError('A valid subscription ARN is required to unsubscribe')
    _sns_client().unsubscribe(SubscriptionArn=clean_arn)
    return {'subscription_arn': clean_arn, 'unsubscribed': True}


def set_subscription_attributes(
    subscription_arn: str,
    attribute_name: str,
    attribute_value: Any,
) -> dict[str, Any]:
    clean_arn = (subscription_arn or '').strip()
    clean_attr = (attribute_name or '').strip()
    if not clean_arn or not clean_attr:
        raise ValueError('Subscription ARN and attribute name are required')

    if clean_attr == 'FilterPolicy':
        policy = json.loads(attribute_value) if isinstance(attribute_value, str) else attribute_value
        if not isinstance(policy, dict):
            raise ValueError('Filter policy must be a JSON object')
        attribute_value = policy
    if clean_attr == 'FilterPolicyScope' and attribute_value not in ('MessageAttributes', 'MessageBody'):
        raise ValueError('Invalid filter policy scope')
    if clean_attr == 'RawMessageDelivery' and not (isinstance(attribute_value, bool) or isinstance(attribute_value, str) and attribute_value in ('true', 'false')):
        raise ValueError('Raw message delivery must be true or false')
    val = json.dumps(attribute_value) if isinstance(attribute_value, (dict, list, bool)) else str(attribute_value)
    _sns_client().set_subscription_attributes(
        SubscriptionArn=clean_arn,
        AttributeName=clean_attr,
        AttributeValue=val,
    )
    return {
        'subscription_arn': clean_arn,
        'attribute_name': clean_attr,
        'attribute_value': val,
        'updated': True,
    }


def publish_message(
    topic_arn: str,
    message: str,
    *,
    subject: str | None = None,
    message_attributes: Any = None,
    message_structure: str | None = None,
    message_group_id: str | None = None,
    message_deduplication_id: str | None = None,
) -> dict[str, Any]:
    if not message:
        raise ValueError('Message body is required')

    arn = validate_topic_arn(topic_arn)
    payload: dict[str, Any] = {
        'TopicArn': arn,
        'Message': message,
    }
    if subject:
        payload['Subject'] = subject
    if message_structure:
        payload['MessageStructure'] = message_structure

    attributes = normalize_message_attributes(message_attributes)
    if attributes:
        payload['MessageAttributes'] = attributes

    if arn.endswith('.fifo'):
        if not message_group_id:
            raise ValueError('Message group ID is required for FIFO topics')
        payload['MessageGroupId'] = message_group_id
        if message_deduplication_id:
            payload['MessageDeduplicationId'] = message_deduplication_id

    response = _sns_client().publish(**payload)
    return {
        'topic_arn': arn,
        'message_id': response.get('MessageId'),
        'sequence_number': response.get('SequenceNumber'),
    }
