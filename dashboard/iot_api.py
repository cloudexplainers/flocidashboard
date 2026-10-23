"""Interactive IoT Core and IoT Data Plane helpers for local MQTT testing."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _iot_client():
    return FlociClientFactory().client('iot')


def _iot_data_client():
    return FlociClientFactory().client('iot-data')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def publish_mqtt_message(
    topic: str,
    payload: Any,
    *,
    qos: int = 0,
) -> dict[str, Any]:
    clean_topic = _required(topic, 'MQTT topic')
    if isinstance(payload, (dict, list)):
        payload_bytes = json.dumps(payload).encode('utf-8')
    elif isinstance(payload, str):
        payload_bytes = payload.encode('utf-8')
    elif isinstance(payload, bytes):
        payload_bytes = payload
    else:
        payload_bytes = str(payload or '').encode('utf-8')

    _iot_data_client().publish(
        topic=clean_topic,
        qos=int(qos) if qos in (0, 1) else 0,
        payload=payload_bytes,
    )
    return {
        'topic': clean_topic,
        'qos': qos,
        'published': True,
        'payload_size_bytes': len(payload_bytes),
    }


def get_thing_shadow(
    thing_name: str,
    *,
    shadow_name: str | None = None,
) -> dict[str, Any]:
    clean_name = _required(thing_name, 'Thing name')
    kwargs: dict[str, Any] = {'thingName': clean_name}
    if shadow_name:
        kwargs['shadowName'] = shadow_name.strip()

    response = _iot_data_client().get_thing_shadow(**kwargs)
    raw_payload = response.get('payload')
    payload_data = None
    if raw_payload:
        raw_text = raw_payload.read().decode('utf-8') if hasattr(raw_payload, 'read') else str(raw_payload)
        try:
            payload_data = json.loads(raw_text)
        except Exception:
            payload_data = {'raw': raw_text}

    return {
        'thing_name': clean_name,
        'shadow_name': shadow_name,
        'payload': payload_data,
    }


def update_thing_shadow(
    thing_name: str,
    payload: Any,
    *,
    shadow_name: str | None = None,
) -> dict[str, Any]:
    clean_name = _required(thing_name, 'Thing name')
    if isinstance(payload, (dict, list)):
        payload_bytes = json.dumps(payload).encode('utf-8')
    elif isinstance(payload, str):
        payload_bytes = payload.encode('utf-8')
    elif isinstance(payload, bytes):
        payload_bytes = payload
    else:
        payload_bytes = str(payload or '{}').encode('utf-8')

    kwargs: dict[str, Any] = {
        'thingName': clean_name,
        'payload': payload_bytes,
    }
    if shadow_name:
        kwargs['shadowName'] = shadow_name.strip()

    response = _iot_data_client().update_thing_shadow(**kwargs)
    raw_payload = response.get('payload')
    payload_data = None
    if raw_payload:
        raw_text = raw_payload.read().decode('utf-8') if hasattr(raw_payload, 'read') else str(raw_payload)
        try:
            payload_data = json.loads(raw_text)
        except Exception:
            payload_data = {'raw': raw_text}

    return {
        'thing_name': clean_name,
        'shadow_name': shadow_name,
        'payload': payload_data,
    }


def delete_thing_shadow(
    thing_name: str,
    *,
    shadow_name: str | None = None,
) -> dict[str, Any]:
    clean_name = _required(thing_name, 'Thing name')
    kwargs: dict[str, Any] = {'thingName': clean_name}
    if shadow_name:
        kwargs['shadowName'] = shadow_name.strip()

    response = _iot_data_client().delete_thing_shadow(**kwargs)
    raw_payload = response.get('payload')
    payload_data = None
    if raw_payload:
        raw_text = raw_payload.read().decode('utf-8') if hasattr(raw_payload, 'read') else str(raw_payload)
        try:
            payload_data = json.loads(raw_text)
        except Exception:
            payload_data = {'raw': raw_text}

    return {
        'thing_name': clean_name,
        'shadow_name': shadow_name,
        'deleted': True,
        'payload': payload_data,
    }


def create_thing(
    thing_name: str,
    *,
    thing_type_name: str | None = None,
    attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    clean_name = _required(thing_name, 'Thing name')
    kwargs: dict[str, Any] = {'thingName': clean_name}
    if thing_type_name:
        kwargs['thingTypeName'] = thing_type_name.strip()
    if attributes and isinstance(attributes, dict):
        kwargs['attributePayload'] = {'attributes': {str(k): str(v) for k, v in attributes.items()}}

    response = _iot_client().create_thing(**kwargs)
    return {
        'thing_name': response.get('thingName'),
        'thing_arn': response.get('thingArn'),
        'thing_id': response.get('thingId'),
        'response': _clean_response(response),
    }


def delete_thing(thing_name: str) -> dict[str, Any]:
    clean_name = _required(thing_name, 'Thing name')
    response = _iot_client().delete_thing(thingName=clean_name)
    return {
        'thing_name': clean_name,
        'deleted': True,
        'response': _clean_response(response),
    }


def create_topic_rule(
    rule_name: str,
    sql: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    description: str | None = None,
    rule_disabled: bool = False,
) -> dict[str, Any]:
    clean_name = _required(rule_name, 'Rule name')
    clean_sql = _required(sql, 'SQL statement')
    payload: dict[str, Any] = {
        'sql': clean_sql,
        'ruleDisabled': rule_disabled,
        # A rule with no actions is valid; don't add one the user didn't ask for.
        'actions': actions or [],
    }
    if description:
        payload['description'] = description.strip()

    response = _iot_client().create_topic_rule(
        ruleName=clean_name,
        topicRulePayload=payload,
    )
    return {
        'rule_name': clean_name,
        'sql': clean_sql,
        'response': _clean_response(response),
    }


def delete_topic_rule(rule_name: str) -> dict[str, Any]:
    clean_name = _required(rule_name, 'Rule name')
    response = _iot_client().delete_topic_rule(ruleName=clean_name)
    return {
        'rule_name': clean_name,
        'deleted': True,
        'response': _clean_response(response),
    }
