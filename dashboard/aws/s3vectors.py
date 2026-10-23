"""S3 Vectors inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def s3vectors_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    s3vectors = factory.client('s3vectors')
    operations = set(s3vectors.meta.service_model.operation_names)

    vector_buckets = _safe_value(lambda: core._operation_items(s3vectors, 'list_vector_buckets', 'vectorBuckets'), []) if 'ListVectorBuckets' in operations else []
    indexes: list[dict[str, Any]] = []
    core.record_truncation('ListIndexes', 'vector buckets (indexes)', 50, len(vector_buckets))
    for bucket in vector_buckets[:50]:
        bucket_name = bucket.get('vectorBucketName') or bucket.get('name')
        if bucket_name and 'ListIndexes' in operations:
            bucket_indexes = _safe_value(lambda bucket_name=bucket_name: core._operation_items(s3vectors, 'list_indexes', 'indexes', vectorBucketName=bucket_name), [])
            indexes.extend({'vectorBucketName': bucket_name, **index} for index in bucket_indexes)

    return {
        'summary': {
            'vector_buckets': len(vector_buckets),
            'indexes': len(indexes),
            'available_sdk_operations': len(operations),
        },
        'vector_buckets': _clean_response(vector_buckets),
        'indexes': _clean_response(indexes),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateVectorBucket',
                'ListVectorBuckets',
                'GetVectorBucket',
                'DeleteVectorBucket',
                'CreateIndex',
                'ListIndexes',
                'GetIndex',
                'DeleteIndex',
                'PutVectors',
                'QueryVectors',
                'GetVectors',
                'DeleteVectors',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.27 adds AWS S3 Vectors service support for local vector-search workflows.',
            'The inspector lists vector buckets and samples indexes for the first 50 buckets when the SDK model exposes those operations.',
        ],
    }


def list_s3vectors_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    s3vectors = factory.client('s3vectors')
    operations = set(s3vectors.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListVectorBuckets' in operations:
        buckets = _safe_value(lambda: core._operation_items(s3vectors, 'list_vector_buckets', 'vectorBuckets'), [])
        resources.extend(
            {
                'type': 'vector_bucket',
                'name': bucket.get('vectorBucketName') or bucket.get('name'),
                'arn': bucket.get('vectorBucketArn'),
            }
            for bucket in buckets
        )
        if 'ListIndexes' in operations:
            for bucket in buckets[:50]:
                bucket_name = bucket.get('vectorBucketName') or bucket.get('name')
                if not bucket_name:
                    continue
                resources.extend(
                    {
                        'type': 'index',
                        'name': index.get('indexName'),
                        'vector_bucket': bucket_name,
                        'arn': index.get('indexArn'),
                    }
                    for index in _safe_value(
                        lambda bucket_name=bucket_name: core._operation_items(s3vectors, 'list_indexes', 'indexes', vectorBucketName=bucket_name),
                        [],
                    )
                )

    return resources
