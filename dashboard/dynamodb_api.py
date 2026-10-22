"""Interactive DynamoDB helpers for the table explorer."""

from __future__ import annotations

import base64
import json
import re
from decimal import Decimal
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

from .aws import FlociClientFactory

deserializer = TypeDeserializer()
serializer = TypeSerializer()


def _dynamodb_client():
    return FlociClientFactory().client('dynamodb')


def validate_table_name(name: str) -> str:
    value = (name or '').strip()
    if not value:
        raise ValueError('Table name is required')
    return value


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode('ascii')
    if isinstance(value, set):
        return [_json_safe(item) for item in sorted(value, key=str)]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def _deserialize_item(item: dict[str, Any]) -> dict[str, Any]:
    if not item:
        return {}
    is_wire_format = any(
        isinstance(v, dict) and any(k in {'S', 'N', 'B', 'SS', 'NS', 'BS', 'M', 'L', 'NULL', 'BOOL'} for k in v)
        for v in item.values()
    )
    if is_wire_format:
        return _json_safe({
            key: deserializer.deserialize(value) if isinstance(value, dict) and any(k in {'S', 'N', 'B', 'SS', 'NS', 'BS', 'M', 'L', 'NULL', 'BOOL'} for k in value) else value
            for key, value in item.items()
        })
    return _json_safe(item)


def _decimals(value: Any) -> Any:
    """JSON numbers arrive as floats; DynamoDB numbers must be Decimal (DynamoDB stores them exactly)."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [_decimals(item) for item in value]
    if isinstance(value, dict):
        return {key: _decimals(item) for key, item in value.items()}
    return value


def _serialize_value(val: Any) -> Any:
    if isinstance(val, dict) and len(val) == 1 and list(val.keys())[0] in {'S', 'N', 'B', 'SS', 'NS', 'BS', 'M', 'L', 'NULL', 'BOOL'}:
        return val
    return serializer.serialize(_decimals(val))


def _serialize_item(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError('Item must be a dictionary')
    return {
        str(name): _serialize_value(value)
        for name, value in item.items()
    }


def _serialize_key(key: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(key, dict) or not key:
        raise ValueError('Key must be a non-empty dictionary')
    return {
        str(name): _serialize_value(value)
        for name, value in key.items()
    }


def scan_table(
    table_name: str,
    *,
    limit: int = 25,
    exclusive_start_key: dict[str, Any] | None = None,
    filter_expression: str | None = None,
    expression_attribute_names: dict[str, str] | None = None,
    expression_attribute_values: dict[str, Any] | None = None,
    index_name: str | None = None,
) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    bounded_limit = max(1, min(int(limit), 100))
    payload: dict[str, Any] = {
        'TableName': clean_name,
        'Limit': bounded_limit,
    }
    if exclusive_start_key:
        payload['ExclusiveStartKey'] = _serialize_key(exclusive_start_key)
    if filter_expression:
        payload['FilterExpression'] = filter_expression
    if expression_attribute_names:
        payload['ExpressionAttributeNames'] = expression_attribute_names
    if expression_attribute_values:
        payload['ExpressionAttributeValues'] = _serialize_item(expression_attribute_values)
    if index_name:
        payload['IndexName'] = index_name

    response = _dynamodb_client().scan(**payload)
    return {
        'table': clean_name,
        'count': response.get('Count', 0),
        'scanned_count': response.get('ScannedCount', 0),
        'items': [_deserialize_item(item) for item in response.get('Items', [])],
        'last_evaluated_key': _deserialize_item(response['LastEvaluatedKey'])
        if response.get('LastEvaluatedKey') else None,
    }


def query_table(
    table_name: str,
    key_condition_expression: str,
    expression_attribute_values: dict[str, Any],
    *,
    expression_attribute_names: dict[str, str] | None = None,
    filter_expression: str | None = None,
    index_name: str | None = None,
    limit: int = 25,
    scan_index_forward: bool = True,
    exclusive_start_key: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    if not key_condition_expression:
        raise ValueError('KeyConditionExpression is required for query')
    if not expression_attribute_values:
        raise ValueError('ExpressionAttributeValues is required for query')

    bounded_limit = max(1, min(int(limit), 100))
    payload: dict[str, Any] = {
        'TableName': clean_name,
        'KeyConditionExpression': key_condition_expression,
        'ExpressionAttributeValues': _serialize_item(expression_attribute_values),
        'Limit': bounded_limit,
        'ScanIndexForward': bool(scan_index_forward),
    }
    if expression_attribute_names:
        payload['ExpressionAttributeNames'] = expression_attribute_names
    if filter_expression:
        payload['FilterExpression'] = filter_expression
    if index_name:
        payload['IndexName'] = index_name
    if exclusive_start_key:
        payload['ExclusiveStartKey'] = _serialize_key(exclusive_start_key)

    response = _dynamodb_client().query(**payload)
    return {
        'table': clean_name,
        'count': response.get('Count', 0),
        'scanned_count': response.get('ScannedCount', 0),
        'items': [_deserialize_item(item) for item in response.get('Items', [])],
        'last_evaluated_key': _deserialize_item(response['LastEvaluatedKey'])
        if response.get('LastEvaluatedKey') else None,
    }


def put_item(
    table_name: str,
    item: dict[str, Any],
    *,
    return_values: str = 'NONE',
) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    if not isinstance(item, dict) or not item:
        raise ValueError('Item dictionary is required')
    payload: dict[str, Any] = {
        'TableName': clean_name,
        'Item': _serialize_item(item),
    }
    if return_values and return_values != 'NONE':
        payload['ReturnValues'] = return_values

    response = _dynamodb_client().put_item(**payload)
    return {
        'table': clean_name,
        'item': item,
        'attributes': _deserialize_item(response.get('Attributes', {})) if response.get('Attributes') else None,
        'consumed_capacity': response.get('ConsumedCapacity'),
    }


def get_item(
    table_name: str,
    key: dict[str, Any],
    *,
    consistent_read: bool = False,
    projection_expression: str | None = None,
) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    payload: dict[str, Any] = {
        'TableName': clean_name,
        'Key': _serialize_key(key),
        'ConsistentRead': bool(consistent_read),
    }
    if projection_expression:
        payload['ProjectionExpression'] = projection_expression

    response = _dynamodb_client().get_item(**payload)
    item = response.get('Item')
    return {
        'table': clean_name,
        'item': _deserialize_item(item) if item else None,
        'found': bool(item),
    }


def delete_item(
    table_name: str,
    key: dict[str, Any],
    *,
    return_values: str = 'NONE',
) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    payload: dict[str, Any] = {
        'TableName': clean_name,
        'Key': _serialize_key(key),
    }
    if return_values and return_values != 'NONE':
        payload['ReturnValues'] = return_values

    response = _dynamodb_client().delete_item(**payload)
    return {
        'table': clean_name,
        'key': key,
        'deleted': True,
        'attributes': _deserialize_item(response.get('Attributes', {})) if response.get('Attributes') else None,
    }


def update_time_to_live(
    table_name: str,
    attribute_name: str,
    *,
    enabled: bool = True,
) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    clean_attr = (attribute_name or '').strip()
    if not clean_attr:
        raise ValueError('TTL attribute name is required')
    payload = {
        'TableName': clean_name,
        'TimeToLiveSpecification': {
            'AttributeName': clean_attr,
            'Enabled': bool(enabled),
        },
    }
    response = _dynamodb_client().update_time_to_live(**payload)
    return {
        'table': clean_name,
        'time_to_live_specification': response.get('TimeToLiveSpecification', {}),
    }


def describe_time_to_live(table_name: str) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    response = _dynamodb_client().describe_time_to_live(TableName=clean_name)
    return {
        'table': clean_name,
        'time_to_live_description': response.get('TimeToLiveDescription', {}),
    }


def execute_select_statement(statement: str, *, limit: int = 25) -> dict[str, Any]:
    clean_statement = (statement or '').strip()
    if not clean_statement:
        raise ValueError('Statement is required')
    if not clean_statement.lower().startswith('select'):
        raise ValueError('Only read-only SELECT statements are allowed')

    bounded_limit = max(1, min(int(limit), 100))
    client = _dynamodb_client()
    if 'ExecuteStatement' not in client.meta.service_model.operation_names:
        raise ValueError('ExecuteStatement is not available in this boto3/Floci environment')

    response = client.execute_statement(Statement=clean_statement, Limit=bounded_limit)
    return {
        'statement': clean_statement,
        'count': len(response.get('Items', [])),
        'items': [_deserialize_item(item) for item in response.get('Items', [])],
        'next_token': response.get('NextToken'),
    }


def parse_start_key(value: Any) -> dict[str, Any] | None:
    if value in (None, ''):
        return None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError('Exclusive start key must be a JSON object')
        return parsed
    raise ValueError('Exclusive start key must be a JSON object')


TABLE_NAME_PATTERN = re.compile(r'^[A-Za-z0-9_.-]{3,255}$')
KEY_TYPES = {'S', 'N', 'B'}


def _key_attribute(name: Any, attribute_type: Any, label: str) -> tuple[str, str]:
    clean = str(name or '').strip()
    if not clean:
        raise ValueError(f'{label} name is required')
    clean_type = str(attribute_type or 'S').upper()
    if clean_type not in KEY_TYPES:
        raise ValueError(f'{label} type must be S, N or B')
    return clean, clean_type


def create_table(
    table_name: str,
    *,
    partition_key: dict[str, Any],
    sort_key: dict[str, Any] | None = None,
    billing_mode: str = 'PAY_PER_REQUEST',
    read_capacity_units: int | None = None,
    write_capacity_units: int | None = None,
    deletion_protection: bool = False,
) -> dict[str, Any]:
    """CreateTable with a partition key, optional sort key and capacity mode, as the console's Create table form."""
    name = str(table_name or '').strip()
    if not TABLE_NAME_PATTERN.match(name):
        raise ValueError('Table name must be 3 to 255 characters: letters, numbers, underscores (_), hyphens (-) and periods (.)')
    pk_name, pk_type = _key_attribute(partition_key.get('name'), partition_key.get('type'), 'Partition key')
    key_schema = [{'AttributeName': pk_name, 'KeyType': 'HASH'}]
    attributes = [{'AttributeName': pk_name, 'AttributeType': pk_type}]
    if sort_key and str(sort_key.get('name') or '').strip():
        sk_name, sk_type = _key_attribute(sort_key.get('name'), sort_key.get('type'), 'Sort key')
        if sk_name == pk_name:
            raise ValueError('Sort key must differ from the partition key')
        key_schema.append({'AttributeName': sk_name, 'KeyType': 'RANGE'})
        attributes.append({'AttributeName': sk_name, 'AttributeType': sk_type})
    payload: dict[str, Any] = {
        'TableName': name,
        'KeySchema': key_schema,
        'AttributeDefinitions': attributes,
        'BillingMode': _billing_mode(billing_mode),
        'DeletionProtectionEnabled': bool(deletion_protection),
    }
    if payload['BillingMode'] == 'PROVISIONED':
        payload['ProvisionedThroughput'] = _throughput(read_capacity_units, write_capacity_units)
    table = _dynamodb_client().create_table(**payload).get('TableDescription', {})
    return {'table': name, 'arn': table.get('TableArn'), 'status': table.get('TableStatus')}


def delete_table(table_name: str) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    table = _dynamodb_client().delete_table(TableName=clean_name).get('TableDescription', {})
    return {'table': clean_name, 'status': table.get('TableStatus'), 'deleted': True}


def _billing_mode(value: Any) -> str:
    mode = str(value or 'PAY_PER_REQUEST').upper()
    if mode not in {'PAY_PER_REQUEST', 'PROVISIONED'}:
        raise ValueError('Capacity mode must be PAY_PER_REQUEST (on-demand) or PROVISIONED')
    return mode


def _throughput(read_units: Any, write_units: Any) -> dict[str, int]:
    try:
        reads, writes = int(read_units), int(write_units)
    except (TypeError, ValueError) as exc:
        raise ValueError('Provisioned capacity needs whole-number read and write capacity units') from exc
    if reads < 1 or writes < 1:
        raise ValueError('Read and write capacity units must be at least 1')
    return {'ReadCapacityUnits': reads, 'WriteCapacityUnits': writes}


def update_table_capacity(table_name: str, *, billing_mode: str, read_capacity_units: Any = None, write_capacity_units: Any = None) -> dict[str, Any]:
    """UpdateTable to switch between on-demand and provisioned capacity."""
    clean_name = validate_table_name(table_name)
    payload: dict[str, Any] = {'TableName': clean_name, 'BillingMode': _billing_mode(billing_mode)}
    if payload['BillingMode'] == 'PROVISIONED':
        payload['ProvisionedThroughput'] = _throughput(read_capacity_units, write_capacity_units)
    table = _dynamodb_client().update_table(**payload).get('TableDescription', {})
    return {'table': clean_name, 'status': table.get('TableStatus'), 'billing_mode': payload['BillingMode']}


def describe_continuous_backups(table_name: str) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    description = _dynamodb_client().describe_continuous_backups(TableName=clean_name).get('ContinuousBackupsDescription', {})
    return {'table': clean_name, 'continuous_backups': _json_safe(description)}


def update_point_in_time_recovery(table_name: str, *, enabled: bool) -> dict[str, Any]:
    clean_name = validate_table_name(table_name)
    description = _dynamodb_client().update_continuous_backups(
        TableName=clean_name,
        PointInTimeRecoverySpecification={'PointInTimeRecoveryEnabled': bool(enabled)},
    ).get('ContinuousBackupsDescription', {})
    return {'table': clean_name, 'continuous_backups': _json_safe(description)}
