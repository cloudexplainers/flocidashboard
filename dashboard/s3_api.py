"""S3 console API helpers (boto3 → Floci)."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError

from .aws import FlociClientFactory, _clean_response, _error_code, public_endpoint_url
from .aws.s3 import _s3_bucket_location, _s3_bucket_objects, _s3_bucket_versions, _s3_optional

MULTIPART_THRESHOLD = 5 * 1024 * 1024
MULTIPART_PART_SIZE = 5 * 1024 * 1024


def _bucket_encryption(s3, bucket: str) -> dict[str, Any]:
    configuration = s3.get_bucket_encryption(Bucket=bucket).get('ServerSideEncryptionConfiguration') or {}
    rules = configuration.get('Rules') or []
    algorithm = None
    if rules:
        algorithm = (rules[0].get('ApplyServerSideEncryptionByDefault') or {}).get('SSEAlgorithm')
    return {**configuration, 'IsDefaultSseS3': algorithm == 'AES256'}


def _s3_client():
    return FlociClientFactory().client('s3')


def probe_anonymous_list(name: str) -> dict[str, Any]:
    """Send an unsigned ListObjectsV2 (MaxKeys=0) for the bucket, as an anonymous caller would.

    Reads nothing back but the answer. Floci started with FLOCI_SERVICES_S3_ENFORCE_AUTH=false
    allows every unsigned request, whatever Block Public Access and bucket policies say.
    """
    factory = FlociClientFactory()
    anonymous = boto3.client(
        's3',
        endpoint_url=factory.endpoint_url,
        region_name=factory.region,
        config=Config(
            signature_version=UNSIGNED,
            s3={'addressing_style': 'path'},
            retries={'max_attempts': 1},
            connect_timeout=2,
            read_timeout=5,
        ),
    )
    try:
        anonymous.list_objects_v2(Bucket=name, MaxKeys=0)
    except ClientError as exc:
        return {
            'bucket': name,
            'anonymous_list_allowed': False,
            'status_code': exc.response.get('ResponseMetadata', {}).get('HTTPStatusCode'),
            'error_code': _error_code(exc),
        }
    return {'bucket': name, 'anonymous_list_allowed': True, 'status_code': 200, 'error_code': None}


def s3_website_endpoint(bucket: str, region: str | None) -> str:
    """Website endpoint in the AWS shape http://<bucket>.s3-website-<region>.<host>, served by Floci."""
    parsed = urlparse(public_endpoint_url())
    host = parsed.hostname or 'localhost'
    port = f':{parsed.port}' if parsed.port else ''
    return f'http://{bucket}.s3-website-{region or "us-east-1"}.{host}{port}'


def client_error_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, ClientError):
        error = exc.response.get('Error', {})
        return {
            'error': error.get('Message') or str(exc),
            'code': error.get('Code'),
        }
    return {'error': str(exc)}


def list_s3_buckets() -> list[dict[str, Any]]:
    s3 = _s3_client()
    buckets = s3.list_buckets().get('Buckets', [])
    result = []
    for bucket in buckets:
        name = bucket.get('Name')
        location = _s3_optional(
            lambda n=name: _s3_bucket_location(s3, n),
            {'NoSuchBucket'},
        )
        result.append({
            'name': name,
            'created': bucket.get('CreationDate'),
            'region': location if isinstance(location, str) else None,
        })
    return result


def get_s3_bucket(name: str) -> dict[str, Any]:
    factory = FlociClientFactory()
    s3 = factory.client('s3')
    buckets = {b.get('Name'): b for b in s3.list_buckets().get('Buckets', [])}
    if name not in buckets:
        raise ClientError(
            {'Error': {'Code': 'NoSuchBucket', 'Message': 'The specified bucket does not exist'}},
            'HeadBucket',
        )

    bucket = buckets[name]
    objects = _s3_bucket_objects(s3, name)
    total_bytes = sum(item.get('size') or 0 for item in objects)

    return {
        'name': name,
        'arn': f'arn:aws:s3:::{name}',
        'path_style_url': f'{factory.endpoint_url.rstrip("/")}/{name}',
        'created': bucket.get('CreationDate'),
        'location': _s3_optional(
            lambda: _s3_bucket_location(s3, name),
            {'NoSuchBucket'},
        ),
        'region': _s3_optional(
            lambda: _s3_bucket_location(s3, name),
            {'NoSuchBucket'},
        ),
        'versioning': _s3_optional(
            lambda: s3.get_bucket_versioning(Bucket=name),
            {'NoSuchBucket'},
        ),
        'tagging': _s3_optional(
            lambda: s3.get_bucket_tagging(Bucket=name).get('TagSet', []),
            {'NoSuchTagSet', 'NoSuchBucket'},
        ),
        'policy': _s3_optional(
            lambda: json.loads(s3.get_bucket_policy(Bucket=name).get('Policy', '{}')),
            {'NoSuchBucketPolicy', 'NoSuchBucket'},
        ),
        'cors': _s3_optional(
            lambda: s3.get_bucket_cors(Bucket=name).get('CORSRules', []),
            {'NoSuchCORSConfiguration', 'NoSuchBucket'},
        ),
        'lifecycle': _s3_optional(
            lambda: s3.get_bucket_lifecycle_configuration(Bucket=name).get('Rules', []),
            {'NoSuchLifecycleConfiguration', 'NoSuchBucket'},
        ),
        'acl': _s3_optional(
            lambda: s3.get_bucket_acl(Bucket=name),
            {'NoSuchBucket'},
        ),
        'encryption': _s3_optional(
            lambda: _bucket_encryption(s3, name),
            {'ServerSideEncryptionConfigurationNotFoundError', 'NoSuchBucket'},
        ),
        'notification': _s3_optional(
            lambda: s3.get_bucket_notification_configuration(Bucket=name),
            {'NoSuchBucket'},
        ),
        'website': _s3_optional(
            lambda: s3.get_bucket_website(Bucket=name),
            {'NoSuchWebsiteConfiguration', 'NoSuchBucket'},
        ),
        'public_access_block': _s3_optional(
            lambda: s3.get_public_access_block(Bucket=name).get('PublicAccessBlockConfiguration'),
            {'NoSuchPublicAccessBlockConfiguration', 'NoSuchBucket'},
        ),
        'object_lock': _s3_optional(
            lambda: s3.get_object_lock_configuration(Bucket=name).get('ObjectLockConfiguration'),
            {'ObjectLockConfigurationNotFoundError', 'NoSuchBucket'},
        ),
        'objects': objects,
        'object_versions': _s3_bucket_versions(s3, name),
        'object_count': len(objects),
        'total_bytes': total_bytes,
    }


def delete_s3_bucket(name: str) -> dict[str, str]:
    _s3_client().delete_bucket(Bucket=name)
    return {'name': name, 'deleted': True}


def empty_s3_bucket(name: str) -> dict[str, Any]:
    """Delete every object version and delete marker, as the console's Empty does.

    In a versioned bucket, deleting only current objects leaves old versions and
    adds delete markers, so the bucket still can't be deleted. Per-key failures
    come back in a 200 DeleteObjects response; they are returned as an error.
    """
    s3 = _s3_client()
    listing = _paginate_versions(s3, name)
    objects = [
        {'Key': item['Key'], 'VersionId': item['VersionId']} if item.get('VersionId') and item['VersionId'] != 'null'
        else {'Key': item['Key']}
        for item in listing
        if item.get('Key')
    ]
    deleted = 0
    errors: list[dict[str, Any]] = []
    for chunk in _chunks(objects, 1000):
        response = s3.delete_objects(Bucket=name, Delete={'Objects': chunk, 'Quiet': False})
        deleted += len(response.get('Deleted', []))
        errors.extend(response.get('Errors', []))

    result: dict[str, Any] = {'name': name, 'deleted_count': deleted}
    if errors:
        result['errors'] = _clean_response(errors)
        first = errors[0]
        result['error'] = (
            f'{len(errors)} of {len(objects)} objects could not be deleted '
            f'(first: {first.get("Key")}: {first.get("Code")} {first.get("Message") or ""}).'
        ).replace(' )', ')')
    return result


def _paginate_versions(s3, bucket: str) -> list[dict[str, Any]]:
    result = s3.get_paginator('list_object_versions').paginate(Bucket=bucket).build_full_result()
    return [*(result.get('Versions') or []), *(result.get('DeleteMarkers') or [])]


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def list_s3_objects(
    bucket: str,
    *,
    prefix: str = '',
    delimiter: str = '/',
    continuation_token: str | None = None,
    max_keys: int = 100,
    versions: bool = False,
    version_id_marker: str | None = None,
) -> dict[str, Any]:
    s3 = _s3_client()

    if versions:
        params: dict[str, Any] = {
            'Bucket': bucket,
            'MaxKeys': max_keys,
        }
        if delimiter:
            params['Delimiter'] = delimiter
        if prefix:
            params['Prefix'] = prefix
        if continuation_token:
            params['KeyMarker'] = continuation_token
            # A page can end part way through one key's versions; both markers are needed.
            if version_id_marker:
                params['VersionIdMarker'] = version_id_marker

        response = s3.list_object_versions(**params)
        items = []
        for item in response.get('Versions', []):
            items.append({
                'key': item.get('Key'),
                'name': _object_display_name(item.get('Key'), prefix),
                'type': 'file',
                'size': item.get('Size'),
                'etag': item.get('ETag'),
                'last_modified': item.get('LastModified'),
                'storage_class': item.get('StorageClass'),
                'version_id': item.get('VersionId'),
                'is_latest': item.get('IsLatest'),
                'is_delete_marker': False,
            })
        for item in response.get('DeleteMarkers', []):
            items.append({
                'key': item.get('Key'),
                'name': _object_display_name(item.get('Key'), prefix),
                'type': 'delete_marker',
                'size': None,
                'etag': None,
                'last_modified': item.get('LastModified'),
                'storage_class': None,
                'version_id': item.get('VersionId'),
                'is_latest': item.get('IsLatest'),
                'is_delete_marker': True,
            })

        # Group by key with the current version first, as the console's "Show versions"
        # view does. ListObjectVersions already returns versions newest first.
        items.sort(key=lambda item: (item.get('key') or '', not item.get('is_latest')))
        return {
            'bucket': bucket,
            'prefix': prefix,
            'folders': [
                {'prefix': entry.get('Prefix'), 'name': _object_display_name(entry.get('Prefix'), prefix).rstrip('/')}
                for entry in response.get('CommonPrefixes', [])
                if entry.get('Prefix')
            ],
            'objects': items,
            'is_truncated': response.get('IsTruncated', False),
            'next_continuation_token': response.get('NextKeyMarker'),
            'next_version_id_marker': response.get('NextVersionIdMarker'),
            'key_count': len(items),
        }

    params = {
        'Bucket': bucket,
        'MaxKeys': max_keys,
        'Delimiter': delimiter,
    }
    if prefix:
        params['Prefix'] = prefix
    if continuation_token:
        params['ContinuationToken'] = continuation_token

    response = s3.list_objects_v2(**params)
    folders = [
        {
            'prefix': item.get('Prefix'),
            'name': _folder_display_name(item.get('Prefix'), prefix),
            'type': 'folder',
        }
        for item in response.get('CommonPrefixes', [])
    ]
    objects = []
    for item in response.get('Contents', []):
        key = item.get('Key') or ''
        if key == prefix or key.endswith('/'):
            continue
        objects.append({
            'key': key,
            'name': _object_display_name(key, prefix),
            'type': 'file',
            'size': item.get('Size'),
            'etag': item.get('ETag'),
            'last_modified': item.get('LastModified'),
            'storage_class': item.get('StorageClass'),
            'version_id': None,
            'is_latest': True,
            'is_delete_marker': False,
        })

    return {
        'bucket': bucket,
        'prefix': prefix,
        'folders': folders,
        'objects': objects,
        'is_truncated': response.get('IsTruncated', False),
        'next_continuation_token': response.get('NextContinuationToken'),
        'key_count': response.get('KeyCount', len(folders) + len(objects)),
    }


def _folder_display_name(full_prefix: str | None, parent_prefix: str) -> str:
    if not full_prefix:
        return ''
    relative = full_prefix[len(parent_prefix):] if parent_prefix else full_prefix
    return relative.rstrip('/').split('/')[0] if relative else full_prefix.rstrip('/')


def _object_display_name(key: str | None, parent_prefix: str) -> str:
    if not key:
        return ''
    relative = key[len(parent_prefix):] if parent_prefix and key.startswith(parent_prefix) else key
    return relative.split('/')[0] if '/' in relative else relative


def upload_s3_object(bucket: str, key: str, body: bytes, content_type: str | None = None) -> dict[str, Any]:
    if len(body) >= MULTIPART_THRESHOLD:
        return _multipart_upload(bucket, key, body, content_type)

    s3 = _s3_client()
    params: dict[str, Any] = {'Bucket': bucket, 'Key': key, 'Body': body}
    if content_type:
        params['ContentType'] = content_type
    response = s3.put_object(**params)
    return {
        'bucket': bucket,
        'key': key,
        'etag': response.get('ETag'),
        'size': len(body),
    }


def _multipart_upload(bucket: str, key: str, body: bytes, content_type: str | None) -> dict[str, Any]:
    s3 = _s3_client()
    create_params: dict[str, Any] = {'Bucket': bucket, 'Key': key}
    if content_type:
        create_params['ContentType'] = content_type
    upload = s3.create_multipart_upload(**create_params)
    upload_id = upload['UploadId']
    parts = []
    try:
        part_number = 1
        offset = 0
        while offset < len(body):
            chunk = body[offset:offset + MULTIPART_PART_SIZE]
            part = s3.upload_part(
                Bucket=bucket,
                Key=key,
                PartNumber=part_number,
                UploadId=upload_id,
                Body=chunk,
            )
            parts.append({'ETag': part['ETag'], 'PartNumber': part_number})
            part_number += 1
            offset += MULTIPART_PART_SIZE

        response = s3.complete_multipart_upload(
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts},
        )
        return {
            'bucket': bucket,
            'key': key,
            'etag': response.get('ETag'),
            'size': len(body),
            'multipart': True,
        }
    except Exception:
        s3.abort_multipart_upload(Bucket=bucket, Key=key, UploadId=upload_id)
        raise


def download_s3_object(bucket: str, key: str, version_id: str | None = None) -> tuple[bytes, dict[str, Any]]:
    s3 = _s3_client()
    params: dict[str, Any] = {'Bucket': bucket, 'Key': key}
    if version_id:
        params['VersionId'] = version_id
    response = s3.get_object(**params)
    body = response['Body'].read()
    meta = {
        'content_type': response.get('ContentType'),
        'content_length': response.get('ContentLength'),
        'etag': response.get('ETag'),
        'last_modified': response.get('LastModified'),
        'metadata': response.get('Metadata', {}),
    }
    return body, meta


def delete_s3_objects(bucket: str, keys: list[dict[str, Any]]) -> dict[str, Any]:
    s3 = _s3_client()
    objects = []
    for item in keys:
        entry: dict[str, Any] = {'Key': item['key']}
        if item.get('version_id'):
            entry['VersionId'] = item['version_id']
        objects.append(entry)

    if not objects:
        return {'deleted': [], 'errors': []}

    if len(objects) == 1 and not objects[0].get('VersionId'):
        s3.delete_object(Bucket=bucket, Key=objects[0]['Key'])
        return {'deleted': [{'key': objects[0]['Key']}], 'errors': []}

    response = s3.delete_objects(
        Bucket=bucket,
        Delete={'Objects': objects, 'Quiet': False},
    )
    return _clean_response({
        'deleted': response.get('Deleted', []),
        'errors': response.get('Errors', []),
    })


def copy_s3_object(
    bucket: str,
    source_key: str,
    dest_key: str,
    dest_bucket: str | None = None,
    source_version_id: str | None = None,
) -> dict[str, Any]:
    s3 = _s3_client()
    target_bucket = dest_bucket or bucket
    copy_source: dict[str, str] = {'Bucket': bucket, 'Key': source_key}
    if source_version_id:
        copy_source['VersionId'] = source_version_id

    response = s3.copy_object(
        CopySource=copy_source,
        Bucket=target_bucket,
        Key=dest_key,
    )
    return {
        'source_bucket': bucket,
        'source_key': source_key,
        'dest_bucket': target_bucket,
        'dest_key': dest_key,
        'etag': response.get('CopyObjectResult', {}).get('ETag'),
    }


def create_s3_folder(bucket: str, folder_key: str) -> dict[str, Any]:
    key = folder_key if folder_key.endswith('/') else f'{folder_key}/'
    return upload_s3_object(bucket, key, b'')


def head_s3_object(bucket: str, key: str, version_id: str | None = None) -> dict[str, Any]:
    s3 = _s3_client()
    params: dict[str, Any] = {'Bucket': bucket, 'Key': key}
    if version_id:
        params['VersionId'] = version_id
    response = s3.head_object(**params)
    return _clean_response({
        'key': key,
        'bucket': bucket,
        'version_id': version_id,
        'content_type': response.get('ContentType'),
        'content_length': response.get('ContentLength'),
        'etag': response.get('ETag'),
        'last_modified': response.get('LastModified'),
        'storage_class': response.get('StorageClass'),
        'metadata': response.get('Metadata', {}),
        'versioning': response.get('VersionId'),
    })


def get_s3_object_tags(bucket: str, key: str, version_id: str | None = None) -> list[dict[str, str]]:
    s3 = _s3_client()
    params: dict[str, Any] = {'Bucket': bucket, 'Key': key}
    if version_id:
        params['VersionId'] = version_id
    response = s3.get_object_tagging(**params)
    return response.get('TagSet', [])


def put_s3_object_tags(
    bucket: str,
    key: str,
    tags: list[dict[str, str]],
    version_id: str | None = None,
) -> list[dict[str, str]]:
    s3 = _s3_client()
    params: dict[str, Any] = {
        'Bucket': bucket,
        'Key': key,
        'Tagging': {'TagSet': tags},
    }
    if version_id:
        params['VersionId'] = version_id
    s3.put_object_tagging(**params)
    return tags


def presign_s3_object(bucket: str, key: str, version_id: str | None = None, expires_in: int = 3600) -> str:
    # Sign against the browser-reachable host: the Host header is part of the signature.
    s3 = FlociClientFactory(endpoint_url=public_endpoint_url()).client('s3')
    params: dict[str, Any] = {'Bucket': bucket, 'Key': key}
    if version_id:
        params['VersionId'] = version_id
    return s3.generate_presigned_url(
        'get_object',
        Params=params,
        ExpiresIn=expires_in,
    )


def get_s3_versioning(bucket: str) -> dict[str, Any]:
    return _s3_optional(
        lambda: _s3_client().get_bucket_versioning(Bucket=bucket),
        {'NoSuchBucket'},
    ) or {}


def put_s3_versioning(bucket: str, status: str) -> dict[str, Any]:
    s3 = _s3_client()
    if status not in ('Enabled', 'Suspended'):
        raise ValueError('Status must be Enabled or Suspended')
    s3.put_bucket_versioning(
        Bucket=bucket,
        VersioningConfiguration={'Status': status},
    )
    return get_s3_versioning(bucket)


def get_s3_policy(bucket: str) -> dict[str, Any] | None:
    result = _s3_optional(
        lambda: json.loads(_s3_client().get_bucket_policy(Bucket=bucket).get('Policy', '{}')),
        {'NoSuchBucketPolicy', 'NoSuchBucket'},
    )
    return result if result is not None else None


def put_s3_policy(bucket: str, policy: dict[str, Any]) -> dict[str, Any]:
    _s3_client().put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
    return policy


def delete_s3_policy(bucket: str) -> dict[str, bool]:
    _s3_client().delete_bucket_policy(Bucket=bucket)
    return {'deleted': True}


def get_s3_cors(bucket: str) -> list[dict[str, Any]]:
    result = _s3_optional(
        lambda: _s3_client().get_bucket_cors(Bucket=bucket).get('CORSRules', []),
        {'NoSuchCORSConfiguration', 'NoSuchBucket'},
    )
    return result if isinstance(result, list) else []


def put_s3_cors(bucket: str, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _s3_client().put_bucket_cors(Bucket=bucket, CORSConfiguration={'CORSRules': rules})
    return rules


def delete_s3_cors(bucket: str) -> dict[str, bool]:
    _s3_client().delete_bucket_cors(Bucket=bucket)
    return {'deleted': True}


def get_s3_lifecycle(bucket: str) -> list[dict[str, Any]]:
    result = _s3_optional(
        lambda: _s3_client().get_bucket_lifecycle_configuration(Bucket=bucket).get('Rules', []),
        {'NoSuchLifecycleConfiguration', 'NoSuchBucket'},
    )
    return result if isinstance(result, list) else []


def put_s3_lifecycle(bucket: str, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    _s3_client().put_bucket_lifecycle_configuration(
        Bucket=bucket,
        LifecycleConfiguration={'Rules': rules},
    )
    return rules


def delete_s3_lifecycle(bucket: str) -> dict[str, bool]:
    _s3_client().delete_bucket_lifecycle(Bucket=bucket)
    return {'deleted': True}


def get_s3_encryption(bucket: str) -> dict[str, Any] | None:
    return _s3_optional(
        lambda: _bucket_encryption(_s3_client(), bucket),
        {'ServerSideEncryptionConfigurationNotFoundError', 'NoSuchBucket'},
    )


def put_s3_encryption(bucket: str, config: dict[str, Any]) -> dict[str, Any]:
    _s3_client().put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration=config,
    )
    return config


def get_s3_public_access_block(bucket: str) -> dict[str, Any] | None:
    return _s3_optional(
        lambda: _s3_client().get_public_access_block(Bucket=bucket).get('PublicAccessBlockConfiguration'),
        {'NoSuchPublicAccessBlockConfiguration', 'NoSuchBucket'},
    )


def put_s3_public_access_block(bucket: str, config: dict[str, Any]) -> dict[str, Any]:
    _s3_client().put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration=config,
    )
    return config


def get_s3_bucket_tags(bucket: str) -> list[dict[str, str]]:
    result = _s3_optional(
        lambda: _s3_client().get_bucket_tagging(Bucket=bucket).get('TagSet', []),
        {'NoSuchTagSet', 'NoSuchBucket'},
    )
    return result if isinstance(result, list) else []


def put_s3_bucket_tags(bucket: str, tags: list[dict[str, str]]) -> list[dict[str, str]]:
    s3 = _s3_client()
    if tags:
        s3.put_bucket_tagging(Bucket=bucket, Tagging={'TagSet': tags})
    else:
        try:
            s3.delete_bucket_tagging(Bucket=bucket)
        except ClientError as exc:
            if _error_code(exc) != 'NoSuchTagSet':
                raise
    return tags


def get_s3_notifications(bucket: str) -> dict[str, Any]:
    result = _s3_optional(
        lambda: _s3_client().get_bucket_notification_configuration(Bucket=bucket),
        {'NoSuchBucket'},
    )
    return result if isinstance(result, dict) else {}


def put_s3_notifications(bucket: str, config: dict[str, Any]) -> dict[str, Any]:
    _s3_client().put_bucket_notification_configuration(Bucket=bucket, NotificationConfiguration=config)
    return config


def get_s3_website(bucket: str) -> dict[str, Any] | None:
    return _s3_optional(
        lambda: _s3_client().get_bucket_website(Bucket=bucket),
        {'NoSuchWebsiteConfiguration', 'NoSuchBucket'},
    )


def get_s3_website_with_endpoint(bucket: str) -> dict[str, Any]:
    s3 = _s3_client()
    configuration = get_s3_website(bucket)
    if isinstance(configuration, dict):
        configuration = {k: v for k, v in configuration.items() if k != 'ResponseMetadata'}
    region = _s3_optional(lambda: _s3_bucket_location(s3, bucket), {'NoSuchBucket'})
    return {
        'configuration': configuration,
        'endpoint': s3_website_endpoint(bucket, region if isinstance(region, str) else None) if configuration else None,
    }


def put_s3_website(bucket: str, config: dict[str, Any]) -> dict[str, Any]:
    _s3_client().put_bucket_website(Bucket=bucket, WebsiteConfiguration=config)
    return config


def delete_s3_website(bucket: str) -> dict[str, bool]:
    _s3_client().delete_bucket_website(Bucket=bucket)
    return {'deleted': True}


def s3_inventory_summary() -> dict[str, Any]:
    buckets = list_s3_buckets()
    detailed_buckets = [
        get_s3_bucket(bucket['name'])
        for bucket in buckets
        if bucket.get('name')
    ]

    return {
        'summary': {
            'buckets': len(detailed_buckets),
            'objects': sum(bucket['object_count'] for bucket in detailed_buckets),
            'total_bytes': sum(bucket['total_bytes'] for bucket in detailed_buckets),
            'versioned_buckets': sum(
                1
                for bucket in detailed_buckets
                if isinstance(bucket.get('versioning'), dict)
                and bucket['versioning'].get('Status') == 'Enabled'
            ),
        },
        'buckets': detailed_buckets,
        'supported': {
            'bucket_configuration': [
                'Location', 'Versioning', 'Tagging', 'Policy', 'CORS', 'Lifecycle',
                'ACL', 'Encryption', 'Notifications', 'Object Lock', 'Public Access Block',
                'Request payment', 'Website hosting',
            ],
            'objects': [
                'ListObjectsV2',
                'ListObjectVersions',
                'HeadObject',
                'GetObjectAttributes',
                'SelectObjectContent',
                'Object tagging',
                'Object ACL',
                'Object retention',
                'Object legal hold',
            ],
            'select_object_content': [
                'CSV, JSON, and Parquet inputs',
                'SQL evaluation through floci-duck',
                'ScanRange filtering',
                'Records, Stats, and End event stream frames',
            ],
            'not_implemented': [
                'Replication',
                'Access logging',
                'Intelligent-Tiering configurations',
                'Inventory configurations',
                'Metrics and Analytics configurations',
            ],
        },
        'notes': [
            'Floci supports S3 bucket website hosting configuration and local website-style object serving.',
        ],
    }
