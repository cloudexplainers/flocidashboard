"""Interactive Bedrock guardrail helpers."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('bedrock')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _list_value(value: Any, label: str) -> list[Any]:
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a JSON array')
    return value


def create_guardrail(
    name: str,
    *,
    blocked_input_message: str,
    blocked_output_message: str,
    description: str = '',
    content_filters: Any = None,
    denied_topics: Any = None,
    blocked_words: Any = None,
    pii_entities: Any = None,
    kms_key_id: str = '',
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'name': _required(name, 'Guardrail name'),
        'blockedInputMessaging': _required(blocked_input_message, 'Blocked input message'),
        'blockedOutputsMessaging': _required(blocked_output_message, 'Blocked output message'),
    }
    if description:
        payload['description'] = description
    filters = _list_value(content_filters, 'Content filters')
    if filters:
        payload['contentPolicyConfig'] = {'filtersConfig': filters}
    topics = _list_value(denied_topics, 'Denied topics')
    if topics:
        payload['topicPolicyConfig'] = {'topicsConfig': [{'type': 'DENY', **topic} for topic in topics]}
    words = [str(word) for word in _list_value(blocked_words, 'Blocked words')]
    if words:
        payload['wordPolicyConfig'] = {'wordsConfig': [{'text': word} for word in words]}
    pii = _list_value(pii_entities, 'PII entities')
    if pii:
        payload['sensitiveInformationPolicyConfig'] = {'piiEntitiesConfig': pii}
    if (kms_key_id or '').strip():
        payload['kmsKeyId'] = kms_key_id.strip()
    response = _client().create_guardrail(**payload)
    return {'guardrail_id': response.get('guardrailId'), 'response': _clean_response(response)}


def get_guardrail(guardrail_id: str, version: str = '') -> dict[str, Any]:
    kwargs: dict[str, Any] = {'guardrailIdentifier': _required(guardrail_id, 'Guardrail ID')}
    if (version or '').strip():
        kwargs['guardrailVersion'] = version.strip()
    return {'guardrail': _clean_response(_client().get_guardrail(**kwargs))}


def create_guardrail_version(guardrail_id: str, description: str = '') -> dict[str, Any]:
    kwargs: dict[str, Any] = {'guardrailIdentifier': _required(guardrail_id, 'Guardrail ID')}
    if description:
        kwargs['description'] = description
    response = _client().create_guardrail_version(**kwargs)
    return {'guardrail_id': response.get('guardrailId'), 'version': response.get('version'), 'response': _clean_response(response)}


def delete_guardrail(guardrail_id: str) -> dict[str, Any]:
    clean = _required(guardrail_id, 'Guardrail ID')
    return {'guardrail_id': clean, 'response': _clean_response(_client().delete_guardrail(guardrailIdentifier=clean))}
