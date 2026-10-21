"""Interactive API Gateway helpers for local request testing."""

from __future__ import annotations

import json
from time import monotonic
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .aws import FlociClientFactory

ALLOWED_METHODS = {'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'}


def _clean_required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _path(value: str) -> str:
    cleaned = (value or '').strip() or '/'
    if '://' in cleaned or cleaned.startswith('//'):
        raise ValueError('Path must be relative')
    return cleaned if cleaned.startswith('/') else f'/{cleaned}'


def _headers(value: Any) -> dict[str, str]:
    if value in (None, ''):
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError('Headers must be valid JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('Headers must be a JSON object')
    return {
        str(key): str(item)
        for key, item in value.items()
        if str(key).strip() and item is not None
    }


def _body(value: Any) -> bytes | None:
    if value in (None, ''):
        return None
    if isinstance(value, str):
        return value.encode('utf-8')
    return json.dumps(value).encode('utf-8')


def _is_allowed_local_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').rstrip('.').lower()
    allowed_hosts = {'localhost', '127.0.0.1', '::1', 'localhost.floci.io', 'localhost.localstack.cloud'}
    allowed_suffixes = ('.localhost.floci.io', '.localhost.localstack.cloud')
    return (
        parsed.scheme in {'http', 'https'}
        and (
            hostname in allowed_hosts
            or any(hostname.endswith(suffix) for suffix in allowed_suffixes)
        )
    )


def _is_execute_api_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').rstrip('.').lower()
    return '.execute-api.' in hostname or hostname.endswith('.execute-api')


def _local_execute_url(factory: FlociClientFactory, api_id: str, stage: str, path: str) -> str:
    clean_stage = (stage or '$default').strip() or '$default'
    return (
        f'{factory.endpoint_url.rstrip("/")}/restapis/{api_id}/{clean_stage}'
        f'/_user_request_{path}'
    )


def build_request_url(
    api_type: str,
    api_id: str,
    path: str,
    *,
    stage: str = '',
    endpoint: str = '',
) -> str:
    factory = FlociClientFactory()
    clean_type = (api_type or '').strip().lower()
    clean_id = _clean_required(api_id, 'API ID')
    clean_path = _path(path)

    if clean_type == 'rest':
        clean_stage = _clean_required(stage, 'Stage')
        return _local_execute_url(factory, clean_id, clean_stage, clean_path)

    if clean_type == 'http':
        if not endpoint:
            return _local_execute_url(factory, clean_id, stage, clean_path)

        base = endpoint.rstrip('/')
        url = f'{base}{clean_path}'
        if not _is_allowed_local_url(url):
            if _is_execute_api_url(url):
                return _local_execute_url(factory, clean_id, stage, clean_path)
            raise ValueError('HTTP API endpoint must be a local Floci URL')
        return url

    raise ValueError('API type must be rest or http')


def test_api_request(
    api_type: str,
    api_id: str,
    method: str,
    path: str,
    *,
    stage: str = '',
    endpoint: str = '',
    query: dict[str, Any] | None = None,
    headers: Any = None,
    body: Any = None,
) -> dict[str, Any]:
    clean_method = (method or 'GET').strip().upper()
    if clean_method not in ALLOWED_METHODS:
        raise ValueError('HTTP method is not supported')

    url = build_request_url(api_type, api_id, path, stage=stage, endpoint=endpoint)
    query_values = {
        str(key): str(value)
        for key, value in (query or {}).items()
        if str(key).strip() and value is not None and value != ''
    }
    if query_values:
        url = f'{url}?{urlencode(query_values)}'

    request_headers = _headers(headers)
    request_body = _body(body)
    if request_body is not None and not any(key.lower() == 'content-type' for key in request_headers):
        request_headers['Content-Type'] = 'application/json'

    request = Request(url, data=request_body, headers=request_headers, method=clean_method)
    started = monotonic()
    try:
        with urlopen(request, timeout=8) as response:
            response_body = response.read()
            status = response.getcode()
            response_headers = dict(response.headers.items())
    except HTTPError as exc:
        response_body = exc.read()
        status = exc.code
        response_headers = dict(exc.headers.items())

    text = response_body.decode('utf-8', errors='replace')
    parsed_body: Any = None
    try:
        parsed_body = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed_body = None

    return {
        'api_type': (api_type or '').strip().lower(),
        'api_id': api_id,
        'method': clean_method,
        'url': url,
        'status_code': status,
        'duration_ms': round((monotonic() - started) * 1000, 2),
        'headers': response_headers,
        'body': text,
        'json': parsed_body,
    }


def create_api(api_type: str, name: str, *, description: str = '') -> dict[str, Any]:
    clean_type = (api_type or '').strip().lower()
    clean_name = _clean_required(name, 'API name')
    factory = FlociClientFactory()
    if clean_type == 'rest':
        kwargs: dict[str, Any] = {'name': clean_name}
        if description.strip():
            kwargs['description'] = description.strip()
        response = factory.client('apigateway').create_rest_api(**kwargs)
        return {'api_type': 'rest', 'api_id': response.get('id'), 'api': response}
    if clean_type == 'http':
        kwargs = {'Name': clean_name, 'ProtocolType': 'HTTP'}
        if description.strip():
            kwargs['Description'] = description.strip()
        response = factory.client('apigatewayv2').create_api(**kwargs)
        return {'api_type': 'http', 'api_id': response.get('ApiId'), 'api': response}
    raise ValueError('API type must be rest or http')


def delete_api(api_type: str, api_id: str) -> dict[str, Any]:
    clean_type = (api_type or '').strip().lower()
    clean_id = _clean_required(api_id, 'API ID')
    factory = FlociClientFactory()
    if clean_type == 'rest':
        response = factory.client('apigateway').delete_rest_api(restApiId=clean_id)
    elif clean_type == 'http':
        response = factory.client('apigatewayv2').delete_api(ApiId=clean_id)
    else:
        raise ValueError('API type must be rest or http')
    return {'api_type': clean_type, 'api_id': clean_id, 'deleted': True, 'response': response}
