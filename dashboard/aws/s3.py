"""S3 inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _s3_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def _s3_bucket_location(s3, bucket: str) -> str:
    response = s3.get_bucket_location(Bucket=bucket)
    return response.get('LocationConstraint') or 'us-east-1'


def _s3_bucket_objects(s3, bucket: str) -> list[dict[str, Any]]:
    objects = _safe_value(
        lambda: core._paginate(s3, 'list_objects_v2', 'Contents', Bucket=bucket),
        [],
    )

    return [
        {
            'key': item.get('Key'),
            'size': item.get('Size'),
            'etag': item.get('ETag'),
            'last_modified': item.get('LastModified'),
            'storage_class': item.get('StorageClass'),
        }
        for item in objects
    ]


def _s3_bucket_versions(s3, bucket: str) -> dict[str, Any]:
    response = _s3_optional(
        lambda: s3.list_object_versions(Bucket=bucket),
        {'NoSuchBucket'},
    )

    if not response or 'error' in response:
        return response or {'versions': [], 'delete_markers': []}

    return {
        'versions': [
            {
                'key': item.get('Key'),
                'version_id': item.get('VersionId'),
                'latest': item.get('IsLatest'),
                'size': item.get('Size'),
                'last_modified': item.get('LastModified'),
            }
            for item in response.get('Versions', [])
        ],
        'delete_markers': [
            {
                'key': item.get('Key'),
                'version_id': item.get('VersionId'),
                'latest': item.get('IsLatest'),
                'last_modified': item.get('LastModified'),
            }
            for item in response.get('DeleteMarkers', [])
        ],
    }


def create_s3_bucket(name: str, region: str | None = None) -> dict[str, Any]:
    factory = core.FlociClientFactory()
    s3 = factory.client('s3')
    resolved_region = (region or factory.region or 'us-east-1').strip()

    if resolved_region == 'us-east-1':
        s3.create_bucket(Bucket=name)
    else:
        s3.create_bucket(
            Bucket=name,
            CreateBucketConfiguration={'LocationConstraint': resolved_region},
        )

    return {'name': name, 'region': resolved_region}


def s3_inventory() -> dict[str, Any]:
    from ..s3_api import s3_inventory_summary

    return s3_inventory_summary()


def list_s3_buckets(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': bucket.get('Name'),
            'created': bucket.get('CreationDate'),
        }
        for bucket in factory.client('s3').list_buckets().get('Buckets', [])
    ]
