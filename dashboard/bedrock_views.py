"""HTTP endpoints for the Bedrock guardrails workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from . import bedrock_api as api
from .actions import handle_action_error, parse_json_body


@require_http_methods(['POST'])
def guardrails(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(api.create_guardrail(
            body.get('name') or '',
            blocked_input_message=body.get('blocked_input_message') or '',
            blocked_output_message=body.get('blocked_output_message') or '',
            description=body.get('description') or '',
            content_filters=body.get('content_filters'),
            denied_topics=body.get('denied_topics'),
            blocked_words=body.get('blocked_words'),
            pii_entities=body.get('pii_entities'),
            kms_key_id=body.get('kms_key_id') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='bedrock', operation='create_guardrail')


@require_http_methods(['POST'])
def guardrail_get(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(api.get_guardrail(body.get('guardrail_id') or '', body.get('version') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='bedrock', operation='get_guardrail')


@require_http_methods(['POST'])
def guardrail_versions(request, guardrail_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(api.create_guardrail_version(guardrail_id, body.get('description') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='bedrock', operation='create_guardrail_version')


@require_http_methods(['DELETE'])
def guardrail_detail(request, guardrail_id: str):
    try:
        return JsonResponse(api.delete_guardrail(guardrail_id))
    except Exception as exc:
        return handle_action_error(exc, service='bedrock', operation='delete_guardrail')
