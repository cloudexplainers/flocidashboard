"""Bedrock control-plane (guardrails) inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _safe_value


def _policy_summary(detail: dict[str, Any]) -> dict[str, Any]:
    return {
        'content_filters': [
            f"{item.get('type')} (input {item.get('inputStrength')}, output {item.get('outputStrength')})"
            for item in (detail.get('contentPolicy') or {}).get('filters', [])
        ],
        'denied_topics': [item.get('name') for item in (detail.get('topicPolicy') or {}).get('topics', [])],
        'blocked_words': [item.get('text') for item in (detail.get('wordPolicy') or {}).get('words', [])],
        'pii_entities': [
            f"{item.get('type')}: {item.get('action')}"
            for item in (detail.get('sensitiveInformationPolicy') or {}).get('piiEntities', [])
        ],
    }


def bedrock_inventory() -> dict[str, Any]:
    bedrock = core.FlociClientFactory().client('bedrock')
    guardrails = []
    for summary in _safe_value(lambda: core._paginate(bedrock, 'list_guardrails', 'guardrails'), []):
        guardrail_id = summary.get('id')
        detail = _safe_value(lambda guardrail_id=guardrail_id: bedrock.get_guardrail(guardrailIdentifier=guardrail_id), {}) or {}
        versions = _safe_value(
            lambda guardrail_id=guardrail_id: core._paginate(bedrock, 'list_guardrails', 'guardrails', guardrailIdentifier=guardrail_id),
            [],
        )
        guardrails.append({
            'name': summary.get('name'),
            'id': guardrail_id,
            'status': summary.get('status'),
            'versions': ', '.join(str(version.get('version')) for version in versions),
            **_policy_summary(detail),
            'blocked_input_message': detail.get('blockedInputMessaging'),
            'blocked_output_message': detail.get('blockedOutputsMessaging'),
            'kms_key_arn': detail.get('kmsKeyArn'),
            'arn': summary.get('arn'),
            'updated': summary.get('updatedAt'),
        })
    return {
        'summary': {
            'guardrails': len(guardrails),
            'guardrail_versions': sum(len([v for v in guardrail['versions'].split(', ') if v and v != 'DRAFT']) for guardrail in guardrails),
        },
        'guardrails': guardrails,
        'notes': [
            'Floci stores and versions guardrails (content filters, denied topics, word lists, and PII actions), but never applies them: Converse ignores guardrailConfig and ApplyGuardrail is not emulated.',
            'The rest of the Bedrock control plane (ListFoundationModels, customization, model evaluation, Knowledge Bases, and Agents) is not emulated. Model calls are on the Bedrock Runtime page.',
        ],
    }


def list_bedrock_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    bedrock = factory.client('bedrock')
    return [
        {'type': 'guardrail', 'name': item.get('name'), 'id': item.get('id'), 'arn': item.get('arn'), 'status': item.get('status')}
        for item in _safe_value(lambda: core._paginate(bedrock, 'list_guardrails', 'guardrails'), [])
    ]
