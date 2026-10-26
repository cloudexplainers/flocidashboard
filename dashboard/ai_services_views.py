"""HTTP endpoints for the Comprehend, Rekognition, and Translate workbenches."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from . import ai_services_api as api
from .actions import handle_action_error, parse_json_body


@require_http_methods(['POST'])
def comprehend_detect(request, operation: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(api.comprehend_detect(operation.replace('-', '_'), body.get('text') or '', body.get('language_code') or 'en'))
    except Exception as exc:
        return handle_action_error(exc, service='comprehend', operation=operation.replace('-', '_'))


@require_http_methods(['POST'])
def rekognition_detect(request, operation: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(api.rekognition_detect(
            operation.replace('-', '_'),
            bucket=body.get('bucket') or '',
            key=body.get('key') or '',
            image_base64=body.get('image_base64') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='rekognition', operation=operation.replace('-', '_'))


@require_http_methods(['POST'])
def rekognition_compare_faces(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(api.rekognition_compare_faces(
            source_bucket=body.get('source_bucket') or '',
            source_key=body.get('source_key') or '',
            target_bucket=body.get('target_bucket') or '',
            target_key=body.get('target_key') or '',
            similarity_threshold=body.get('similarity_threshold') or 80,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='rekognition', operation='compare_faces')


@require_http_methods(['POST'])
def translate_text(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(api.translate_text(
            body.get('text') or '',
            source_language=body.get('source_language') or 'auto',
            target_language=body.get('target_language') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='translate', operation='translate_text')
