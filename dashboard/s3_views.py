"""HTTP views for the S3 console API."""

from __future__ import annotations

import json
import re

from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, json_error, parse_json_body
from .aws import create_s3_bucket
from .s3_api import (
    probe_anonymous_list,
    copy_s3_object,
    create_s3_folder,
    delete_s3_bucket,
    delete_s3_cors,
    delete_s3_lifecycle,
    delete_s3_website,
    delete_s3_objects,
    download_s3_object,
    empty_s3_bucket,
    get_s3_bucket,
    get_s3_bucket_tags,
    get_s3_cors,
    get_s3_encryption,
    get_s3_lifecycle,
    get_s3_notifications,
    get_s3_object_tags,
    get_s3_policy,
    get_s3_website_with_endpoint,
    delete_s3_policy,
    get_s3_public_access_block,
    get_s3_versioning,
    head_s3_object,
    list_s3_buckets,
    list_s3_objects,
    presign_s3_object,
    put_s3_bucket_tags,
    put_s3_cors,
    put_s3_encryption,
    put_s3_lifecycle,
    put_s3_notifications,
    put_s3_object_tags,
    put_s3_policy,
    put_s3_public_access_block,
    put_s3_versioning,
    put_s3_website,
    upload_s3_object,
    delete_s3_intelligent_tiering,
    delete_s3_replication,
    get_s3_object_lock,
    get_s3_replication,
    list_s3_intelligent_tiering,
    put_s3_intelligent_tiering,
    put_s3_object_legal_hold,
    put_s3_object_lock,
    put_s3_object_retention,
    put_s3_replication,
)

S3_BUCKET_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')
S3_KEY_MAX_LEN = 1024


def _json_error(message: str, status: int = 400, code: str | None = None) -> JsonResponse:
    return json_error(message, status=status, code=code, service='s3')


def _handle_s3(exc: Exception) -> JsonResponse:
    return handle_action_error(exc, service='s3')


def _validate_bucket(name: str) -> str | None:
    if not name or len(name) < 3 or len(name) > 63:
        return 'Invalid bucket name'
    if not S3_BUCKET_NAME_RE.match(name):
        return 'Invalid bucket name'
    return None


def _validate_key(key: str) -> str | None:
    if not key or len(key) > S3_KEY_MAX_LEN:
        return 'Invalid object key'
    return None


def _parse_json_body(request) -> dict:
    return parse_json_body(request)


def _parse_s3_create_body(request) -> tuple[str, str | None]:
    body = _parse_json_body(request)
    name = str(body.get('name') or body.get('bucket_name') or '').strip()
    region = body.get('region')
    if region is not None:
        region = str(region).strip() or None

    err = _validate_bucket(name)
    if err:
        raise ValueError(err)

    if region is not None and (len(region) < 1 or len(region) > 32):
        raise ValueError('Invalid region')

    return name, region


@require_http_methods(['GET', 'POST'])
def s3_buckets_list(request):
    if request.method == 'POST':
        try:
            name, region = _parse_s3_create_body(request)
            return JsonResponse(create_s3_bucket(name, region))
        except Exception as exc:
            return _handle_s3(exc)

    try:
        return JsonResponse({'buckets': list_s3_buckets()})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'DELETE'])
def s3_bucket_detail(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)

    if request.method == 'DELETE':
        try:
            return JsonResponse(delete_s3_bucket(bucket_name))
        except Exception as exc:
            return _handle_s3(exc)

    try:
        return JsonResponse(get_s3_bucket(bucket_name))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET'])
def s3_bucket_anonymous_access(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        return JsonResponse(probe_anonymous_list(bucket_name))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['POST'])
def s3_bucket_empty(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        result = empty_s3_bucket(bucket_name)
        # Some objects were not deleted: not a success.
        return JsonResponse(result, status=409 if result.get('error') else 200)
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'POST', 'DELETE'])
def s3_bucket_objects(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)

    if request.method == 'GET':
        prefix = request.GET.get('prefix', '')
        delimiter = request.GET.get('delimiter', '/')
        token = request.GET.get('continuation_token') or None
        try:
            max_keys = int(request.GET.get('max_keys', '100'))
        except ValueError:
            return _json_error('Invalid max_keys')
        max_keys = max(1, min(max_keys, 1000))
        versions = request.GET.get('versions', '').lower() in ('1', 'true', 'yes')
        try:
            return JsonResponse(list_s3_objects(
                bucket_name,
                prefix=prefix,
                delimiter=delimiter,
                continuation_token=token,
                max_keys=max_keys,
                versions=versions,
                version_id_marker=request.GET.get('version_id_marker') or None,
            ))
        except Exception as exc:
            return _handle_s3(exc)

    if request.method == 'POST':
        if request.content_type and 'application/json' in request.content_type:
            try:
                body_json = _parse_json_body(request)
                key = str(body_json.get('key', '')).strip()
                if not key:
                    return _json_error('key is required')
                key_err = _validate_key(key)
                if key_err:
                    return _json_error(key_err)
                content = body_json.get('body', '')
                if isinstance(content, str):
                    body_bytes = content.encode('utf-8')
                elif isinstance(content, bytes):
                    body_bytes = content
                else:
                    body_bytes = json.dumps(content).encode('utf-8')
                content_type = body_json.get('content_type') or 'text/plain; charset=utf-8'
                return JsonResponse(upload_s3_object(bucket_name, key, body_bytes, content_type))
            except Exception as exc:
                return _handle_s3(exc)

        key = request.POST.get('key', '').strip()
        if not key:
            return _json_error('key is required')
        key_err = _validate_key(key)
        if key_err:
            return _json_error(key_err)
        upload_file = request.FILES.get('file')
        if not upload_file:
            return _json_error('file is required')
        try:
            body = upload_file.read()
            content_type = upload_file.content_type or None
            return JsonResponse(upload_s3_object(bucket_name, key, body, content_type))
        except Exception as exc:
            return _handle_s3(exc)

    try:
        body = _parse_json_body(request)
        keys = body.get('keys', [])
        if not isinstance(keys, list) or not keys:
            return _json_error('keys array is required')
        for item in keys:
            if not isinstance(item, dict) or not item.get('key'):
                return _json_error('Each key entry must include key')
        return JsonResponse(delete_s3_objects(bucket_name, keys))
    except Exception as exc:
        return _handle_s3(exc)


def s3_object_download(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    key = request.GET.get('key', '')
    key_err = _validate_key(key)
    if key_err:
        return _json_error(key_err)
    version_id = request.GET.get('versionId') or request.GET.get('version_id')
    try:
        body, meta = download_s3_object(bucket_name, key, version_id)
        response = HttpResponse(body, content_type=meta.get('content_type') or 'application/octet-stream')
        filename = key.split('/')[-1] or 'download'
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        if meta.get('content_length') is not None:
            response['Content-Length'] = str(meta['content_length'])
        return response
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['POST'])
def s3_object_copy(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        body = _parse_json_body(request)
        source_key = str(body.get('source_key', '')).strip()
        dest_key = str(body.get('dest_key', '')).strip()
        dest_bucket = body.get('dest_bucket')
        if dest_bucket:
            dest_bucket = str(dest_bucket).strip()
            if _validate_bucket(dest_bucket):
                return _json_error('Invalid dest_bucket')
        for k in (source_key, dest_key):
            if _validate_key(k):
                return _json_error('Invalid key')
        return JsonResponse(copy_s3_object(
            bucket_name,
            source_key,
            dest_key,
            dest_bucket,
            body.get('source_version_id'),
        ))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['PUT'])
def s3_folder_create(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        body = _parse_json_body(request)
        folder = str(body.get('folder', '')).strip()
        if not folder:
            return _json_error('folder is required')
        return JsonResponse(create_s3_folder(bucket_name, folder))
    except Exception as exc:
        return _handle_s3(exc)


def s3_object_head(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    key = request.GET.get('key', '')
    if _validate_key(key):
        return _json_error('Invalid key')
    version_id = request.GET.get('versionId') or request.GET.get('version_id')
    try:
        return JsonResponse(head_s3_object(bucket_name, key, version_id))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT'])
def s3_object_tags(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    key = request.GET.get('key', '') if request.method == 'GET' else ''
    if request.method == 'PUT':
        try:
            body = _parse_json_body(request)
            key = str(body.get('key', '')).strip()
            tags = body.get('tags', [])
            version_id = body.get('version_id')
        except Exception as exc:
            return _handle_s3(exc)
        if _validate_key(key):
            return _json_error('Invalid key')
        if not isinstance(tags, list):
            return _json_error('tags must be an array')
        try:
            return JsonResponse({'tags': put_s3_object_tags(bucket_name, key, tags, version_id)})
        except Exception as exc:
            return _handle_s3(exc)

    if _validate_key(key):
        return _json_error('Invalid key')
    version_id = request.GET.get('versionId') or request.GET.get('version_id')
    try:
        return JsonResponse({'tags': get_s3_object_tags(bucket_name, key, version_id)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['POST'])
def s3_object_presign(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        body = _parse_json_body(request)
        key = str(body.get('key', '')).strip()
        if _validate_key(key):
            return _json_error('Invalid key')
        expires = int(body.get('expires_in', 3600))
        expires = max(60, min(expires, 604800))
        version_id = body.get('version_id')
        url = presign_s3_object(bucket_name, key, version_id, expires)
        return JsonResponse({'url': url, 'expires_in': expires})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT'])
def s3_bucket_versioning(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse(get_s3_versioning(bucket_name) or {})
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        status = str(body.get('status', '')).strip()
        return JsonResponse(put_s3_versioning(bucket_name, status))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def s3_bucket_policy(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            policy = get_s3_policy(bucket_name)
            return JsonResponse({'policy': policy})
        except Exception as exc:
            return _handle_s3(exc)
    if request.method == 'DELETE':
        try:
            return JsonResponse(delete_s3_policy(bucket_name))
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        policy = body.get('policy')
        if not isinstance(policy, dict):
            return _json_error('policy object is required')
        return JsonResponse({'policy': put_s3_policy(bucket_name, policy)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def s3_bucket_cors(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse({'rules': get_s3_cors(bucket_name)})
        except Exception as exc:
            return _handle_s3(exc)
    if request.method == 'DELETE':
        try:
            return JsonResponse(delete_s3_cors(bucket_name))
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        rules = body.get('rules', [])
        if not isinstance(rules, list):
            return _json_error('rules must be an array')
        return JsonResponse({'rules': put_s3_cors(bucket_name, rules)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def s3_bucket_lifecycle(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse({'rules': get_s3_lifecycle(bucket_name)})
        except Exception as exc:
            return _handle_s3(exc)
    if request.method == 'DELETE':
        try:
            return JsonResponse(delete_s3_lifecycle(bucket_name))
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        rules = body.get('rules', [])
        if not isinstance(rules, list):
            return _json_error('rules must be an array')
        return JsonResponse({'rules': put_s3_lifecycle(bucket_name, rules)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT'])
def s3_bucket_encryption(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse({'configuration': get_s3_encryption(bucket_name)})
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        config = body.get('configuration')
        if not isinstance(config, dict):
            return _json_error('configuration object is required')
        return JsonResponse({'configuration': put_s3_encryption(bucket_name, config)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT'])
def s3_bucket_public_access_block(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse({'configuration': get_s3_public_access_block(bucket_name)})
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        config = body.get('configuration')
        if not isinstance(config, dict):
            return _json_error('configuration object is required')
        return JsonResponse({'configuration': put_s3_public_access_block(bucket_name, config)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT'])
def s3_bucket_tags(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse({'tags': get_s3_bucket_tags(bucket_name)})
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        tags = body.get('tags', [])
        if not isinstance(tags, list):
            return _json_error('tags must be an array')
        return JsonResponse({'tags': put_s3_bucket_tags(bucket_name, tags)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT'])
def s3_bucket_notifications(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse(get_s3_notifications(bucket_name))
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        return JsonResponse(put_s3_notifications(bucket_name, body))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def s3_bucket_website(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    if request.method == 'GET':
        try:
            return JsonResponse(get_s3_website_with_endpoint(bucket_name))
        except Exception as exc:
            return _handle_s3(exc)
    if request.method == 'DELETE':
        try:
            return JsonResponse(delete_s3_website(bucket_name))
        except Exception as exc:
            return _handle_s3(exc)
    try:
        body = _parse_json_body(request)
        config = body.get('configuration')
        if not isinstance(config, dict):
            return _json_error('configuration object is required')
        return JsonResponse({'configuration': put_s3_website(bucket_name, config)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT'])
def s3_bucket_object_lock(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        if request.method == 'GET':
            return JsonResponse({'configuration': get_s3_object_lock(bucket_name)})
        body = _parse_json_body(request)
        default_retention = body.get('default_retention')
        if default_retention is not None and not isinstance(default_retention, dict):
            return _json_error('default_retention must be an object or null')
        return JsonResponse({'configuration': put_s3_object_lock(bucket_name, default_retention)})
    except Exception as exc:
        return _handle_s3(exc)


def _object_target(body: dict) -> tuple[str, str | None]:
    key = str(body.get('key', '')).strip()
    if _validate_key(key):
        raise ValueError('Invalid key')
    return key, (str(body.get('version_id')).strip() or None) if body.get('version_id') else None


@require_http_methods(['PUT'])
def s3_object_retention(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        body = _parse_json_body(request)
        key, version_id = _object_target(body)
        return JsonResponse(put_s3_object_retention(
            bucket_name, key, version_id, body.get('mode') or None, body.get('retain_until_date') or None,
            bool(body.get('bypass_governance_retention')),
        ))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['PUT'])
def s3_object_legal_hold(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        body = _parse_json_body(request)
        key, version_id = _object_target(body)
        return JsonResponse(put_s3_object_legal_hold(bucket_name, key, version_id, str(body.get('status', ''))))
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def s3_bucket_replication(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        if request.method == 'GET':
            return JsonResponse({'configuration': get_s3_replication(bucket_name)})
        if request.method == 'DELETE':
            return JsonResponse(delete_s3_replication(bucket_name))
        config = _parse_json_body(request).get('configuration')
        if not isinstance(config, dict):
            return _json_error('configuration object is required')
        return JsonResponse({'configuration': put_s3_replication(bucket_name, config)})
    except Exception as exc:
        return _handle_s3(exc)


@require_http_methods(['GET', 'PUT', 'DELETE'])
def s3_bucket_intelligent_tiering(request, bucket_name: str):
    err = _validate_bucket(bucket_name)
    if err:
        return _json_error(err)
    try:
        if request.method == 'GET':
            return JsonResponse({'configurations': list_s3_intelligent_tiering(bucket_name)})
        if request.method == 'DELETE':
            config_id = request.GET.get('id', '').strip()
            if not config_id:
                return _json_error('id is required')
            return JsonResponse(delete_s3_intelligent_tiering(bucket_name, config_id))
        config = _parse_json_body(request).get('configuration')
        if not isinstance(config, dict):
            return _json_error('configuration object is required')
        return JsonResponse({'configuration': put_s3_intelligent_tiering(bucket_name, config)})
    except Exception as exc:
        return _handle_s3(exc)
