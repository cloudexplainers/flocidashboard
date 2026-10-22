"""HTTP endpoints for the Transcribe workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .transcribe_api import (
    create_vocabulary,
    delete_transcription_job,
    delete_vocabulary,
    get_transcription_job,
    get_vocabulary,
    start_transcription_job,
)


@require_http_methods(['POST'])
def transcribe_jobs_start(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(start_transcription_job(
            body.get('name') or '',
            media_uri=body.get('media_uri') or '',
            language_code=body.get('language_code') or 'en-US',
            media_format=body.get('media_format') or '',
            options=body.get('options') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='transcribe', operation='start_transcription_job')


@require_http_methods(['POST'])
def transcribe_job_get(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_transcription_job(body.get('name') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='transcribe', operation='get_transcription_job')


@require_http_methods(['DELETE'])
def transcribe_job_detail(request, job_name: str):
    try:
        return JsonResponse(delete_transcription_job(job_name))
    except Exception as exc:
        return handle_action_error(exc, service='transcribe', operation='delete_transcription_job')


@require_http_methods(['POST'])
def transcribe_vocabularies_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_vocabulary(
            body.get('name') or '',
            language_code=body.get('language_code') or 'en-US',
            phrases=body.get('phrases') or [],
            vocabulary_file_uri=body.get('vocabulary_file_uri') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='transcribe', operation='create_vocabulary')


@require_http_methods(['POST'])
def transcribe_vocabulary_get(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(get_vocabulary(body.get('name') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='transcribe', operation='get_vocabulary')


@require_http_methods(['DELETE'])
def transcribe_vocabulary_detail(request, vocabulary_name: str):
    try:
        return JsonResponse(delete_vocabulary(vocabulary_name))
    except Exception as exc:
        return handle_action_error(exc, service='transcribe', operation='delete_vocabulary')
