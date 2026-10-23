from __future__ import annotations

import json
import os
from urllib.parse import urlparse

from botocore.exceptions import BotoCoreError, ClientError
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .aws import (
    FlociClientFactory,
    RUNTIME_ENDPOINT_SESSION_KEY,
    default_endpoint_url,
    normalize_endpoint_url,
    set_runtime_endpoint_override,
)


def _read_json(request) -> dict:
    try:
        body = json.loads(request.body.decode('utf-8') or '{}')
    except json.JSONDecodeError as exc:
        raise ValueError('Request body must be valid JSON.') from exc
    if not isinstance(body, dict):
        raise ValueError('Request body must be a JSON object.')
    return body


def _validate_endpoint_url(value: str) -> str:
    endpoint_url = normalize_endpoint_url(value)
    if not endpoint_url:
        raise ValueError('Endpoint URL is required.')

    parsed = urlparse(endpoint_url)
    if parsed.scheme not in {'http', 'https'} or not parsed.netloc:
        raise ValueError('Endpoint must be a valid http or https URL.')
    return endpoint_url


def _settings_payload(request) -> dict:
    factory = FlociClientFactory()
    override = request.session.get(RUNTIME_ENDPOINT_SESSION_KEY) or ''
    return {
        'endpoint_url': factory.endpoint_url,
        'endpoint_source': factory.endpoint_source,
        'runtime_endpoint_url': override,
        'default_endpoint_url': default_endpoint_url(),
        'region': factory.region,
        'service_auth': {
            's3_enforce_auth': os.getenv('FLOCI_SERVICES_S3_ENFORCE_AUTH', 'false').lower() == 'true',
            'iam_enforcement': os.getenv('FLOCI_SERVICES_IAM_ENFORCEMENT_ENABLED', 'false').lower() == 'true',
            'validate_signatures': os.getenv('FLOCI_AUTH_VALIDATE_SIGNATURES', 'false').lower() == 'true',
        },
        **factory.credential_context(),
    }


@require_GET
def settings_detail(request):
    try:
        return JsonResponse(_settings_payload(request))
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@require_POST
def settings_endpoint_save(request):
    try:
        body = _read_json(request)
        endpoint_url = _validate_endpoint_url(body.get('endpoint_url') or '')
        probe = FlociClientFactory(endpoint_url=endpoint_url).health()
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)

    request.session[RUNTIME_ENDPOINT_SESSION_KEY] = endpoint_url
    request.session.modified = True
    set_runtime_endpoint_override(endpoint_url)
    return JsonResponse({
        **_settings_payload(request),
        'saved': True,
        'probe': probe,
    })


@require_http_methods(['DELETE', 'POST'])
def settings_endpoint_reset(request):
    request.session.pop(RUNTIME_ENDPOINT_SESSION_KEY, None)
    request.session.modified = True
    set_runtime_endpoint_override(None)
    return JsonResponse({
        **_settings_payload(request),
        'reset': True,
    })


@require_POST
def settings_test_connection(request):
    try:
        body = _read_json(request)
        endpoint_url = body.get('endpoint_url')
        factory = (
            FlociClientFactory(endpoint_url=_validate_endpoint_url(endpoint_url))
            if endpoint_url
            else FlociClientFactory()
        )
        payload = {
            'endpoint_url': factory.endpoint_url,
            'endpoint_source': 'submitted' if endpoint_url else factory.endpoint_source,
            'region': factory.region,
            **factory.credential_context(),
            'health': factory.health(),
        }
        try:
            payload['identity'] = factory.identity()
            payload['identity_resolved'] = True
        except (BotoCoreError, ClientError, ValueError) as exc:
            hint = factory.local_identity_hint()
            if hint:
                payload['identity'] = hint
                payload['identity_resolved'] = False
            payload['identity_error'] = str(exc)
        return JsonResponse(payload)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)


@require_POST
def settings_floci_reset(request):
    try:
        factory = FlociClientFactory()
        result = factory.reset_state()
        if not result.get('ok'):
            # Floci did not take the reset (unreachable or an HTTP error): not a success.
            return JsonResponse({
                'error': f"Floci did not reset its state: {result.get('error') or 'no response'}",
                'reset_result': result,
            }, status=502)
        return JsonResponse({
            **_settings_payload(request),
            'reset_result': result,
        })
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
