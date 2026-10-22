"""DynamoDB inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value, _string_items


def _dynamodb_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def dynamodb_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    dynamodb = factory.client('dynamodb')
    streams = factory.client('dynamodbstreams')
    table_names = _safe_value(lambda: core._paginate(dynamodb, 'list_tables', 'TableNames'), [])

    def table_detail(table_name: str) -> dict[str, Any]:
        description = _dynamodb_optional(
            lambda: dynamodb.describe_table(TableName=table_name).get('Table', {}),
            {'ResourceNotFoundException'},
        )
        table_arn = description.get('TableArn') if isinstance(description, dict) else None
        scan_preview = _dynamodb_optional(
            lambda: dynamodb.scan(TableName=table_name, Limit=5),
            {'ResourceNotFoundException'},
        )

        return {
            'name': table_name,
            'arn': table_arn,
            'status': description.get('TableStatus') if isinstance(description, dict) else None,
            'created': description.get('CreationDateTime') if isinstance(description, dict) else None,
            'deletion_protection': description.get('DeletionProtectionEnabled') if isinstance(description, dict) else None,
            'table_class': (description.get('TableClassSummary') or {}).get('TableClass') if isinstance(description, dict) else None,
            'continuous_backups': _dynamodb_optional(
                lambda: dynamodb.describe_continuous_backups(TableName=table_name).get('ContinuousBackupsDescription', {}),
                {'ResourceNotFoundException', 'TableNotFoundException'},
            ),
            'item_count': description.get('ItemCount') if isinstance(description, dict) else None,
            'size_bytes': description.get('TableSizeBytes') if isinstance(description, dict) else None,
            'billing_mode': description.get('BillingModeSummary') if isinstance(description, dict) else None,
            'key_schema': description.get('KeySchema') if isinstance(description, dict) else None,
            'attribute_definitions': description.get('AttributeDefinitions') if isinstance(description, dict) else None,
            'provisioned_throughput': description.get('ProvisionedThroughput') if isinstance(description, dict) else None,
            'global_secondary_indexes': description.get('GlobalSecondaryIndexes') if isinstance(description, dict) else None,
            'local_secondary_indexes': description.get('LocalSecondaryIndexes') if isinstance(description, dict) else None,
            'stream_specification': description.get('StreamSpecification') if isinstance(description, dict) else None,
            'latest_stream_arn': description.get('LatestStreamArn') if isinstance(description, dict) else None,
            'ttl': _dynamodb_optional(
                lambda: dynamodb.describe_time_to_live(TableName=table_name).get('TimeToLiveDescription', {}),
                {'ResourceNotFoundException'},
            ),
            'tags': _dynamodb_optional(
                lambda: core._paginate(dynamodb, 'list_tags_of_resource', 'Tags', ResourceArn=table_arn)
                if table_arn else [],
                {'ResourceNotFoundException'},
            ),
            'scan_preview': {
                'count': scan_preview.get('Count'),
                'scanned_count': scan_preview.get('ScannedCount'),
                'items': scan_preview.get('Items', []),
            } if isinstance(scan_preview, dict) and 'error' not in scan_preview else scan_preview,
        }

    tables = [table_detail(name) for name in table_names]
    stream_summaries = _safe_value(lambda: core._paginate(streams, 'list_streams', 'Streams'), [])

    def stream_detail(stream: dict[str, Any]) -> dict[str, Any]:
        stream_arn = stream.get('StreamArn')
        description = _dynamodb_optional(
            lambda: streams.describe_stream(StreamArn=stream_arn).get('StreamDescription', {}),
            {'ResourceNotFoundException'},
        )

        return {
            'name': stream.get('TableName') or stream_arn,
            'table_name': stream.get('TableName'),
            'stream_arn': stream_arn,
            'label': stream.get('StreamLabel'),
            'description': description,
        }

    stream_details = [stream_detail(stream) for stream in stream_summaries]

    return {
        'summary': {
            'tables': len(tables),
            'items': sum(table.get('item_count') or 0 for table in tables),
            'global_secondary_indexes': sum(
                len(table.get('global_secondary_indexes') or [])
                for table in tables
                if isinstance(table.get('global_secondary_indexes'), list)
            ),
            'streams': len(stream_details),
            'ttl_enabled': sum(
                1
                for table in tables
                if isinstance(table.get('ttl'), dict)
                and table['ttl'].get('TimeToLiveStatus') == 'ENABLED'
            ),
        },
        'tables': tables,
        'streams': stream_details,
        'supported': [
            'CreateTable',
            'DeleteTable',
            'DescribeTable',
            'ListTables',
            'UpdateTable',
            'PutItem',
            'GetItem',
            'DeleteItem',
            'UpdateItem',
            'Query',
            'Scan',
            'BatchWriteItem',
            'BatchGetItem',
            'TransactWriteItems',
            'TransactGetItems',
            'ExecuteStatement',
            'ExecuteTransaction',
            'BatchExecuteStatement',
            'DescribeTimeToLive',
            'UpdateTimeToLive',
            'TagResource',
            'UntagResource',
            'ListTagsOfResource',
        ],
        'notes': [
            'Floci 1.5.28 returns exact 400 error messages for invalid expressions.',
            'Floci 1.5.27 adds TableId, TableClass, OnDemandThroughput, deletion-protection fixes, stricter expression validation, ExclusiveStartKey validation, and scan cursor improvements.',
            'Floci 1.5.17 adds DynamoDB PartiQL support for ExecuteStatement, ExecuteTransaction, and BatchExecuteStatement.',
            'Floci 1.5.21 merges nested map paths correctly in ProjectionExpression.',
            'UpdateItem now honors the legacy Expected condition shape for compatibility with older clients.',
        ],
        'streams_supported': [
            'ListStreams',
            'DescribeStream',
            'GetShardIterator',
            'GetRecords',
        ],
    }


def list_dynamodb_tables(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    names = factory.client('dynamodb').get_paginator('list_tables').paginate().build_full_result().get('TableNames', [])
    return _string_items(names, 'name')
