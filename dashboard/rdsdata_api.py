"""Interactive RDS Data API helpers for the SQL query workbench."""

from __future__ import annotations

import base64
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _rdsdata_client():
    return FlociClientFactory().client('rds-data')


def _format_field_value(field: dict[str, Any]) -> Any:
    if not isinstance(field, dict):
        return field
    if field.get('isNull') or 'isNull' in field and field['isNull']:
        return None
    if 'stringValue' in field:
        return field['stringValue']
    if 'longValue' in field:
        return field['longValue']
    if 'doubleValue' in field:
        return field['doubleValue']
    if 'booleanValue' in field:
        return field['booleanValue']
    if 'blobValue' in field:
        val = field['blobValue']
        if isinstance(val, bytes):
            return base64.b64encode(val).decode('ascii')
        return str(val)
    if 'arrayValue' in field:
        arr = field['arrayValue']
        if 'stringValues' in arr:
            return arr['stringValues']
        if 'longValues' in arr:
            return arr['longValues']
        if 'doubleValues' in arr:
            return arr['doubleValues']
        if 'booleanValues' in arr:
            return arr['booleanValues']
        if 'arrayValues' in arr:
            return [_format_field_value(sub) for sub in arr['arrayValues']]
    return field


def _format_records(records: list[Any], column_metadata: list[dict[str, Any]]) -> list[dict[str, Any]]:
    col_names = [col.get('name') or f'col_{i+1}' for i, col in enumerate(column_metadata)]
    rows = []
    for row in records:
        row_dict = {}
        for i, field in enumerate(row):
            col_name = col_names[i] if i < len(col_names) else f'col_{i+1}'
            row_dict[col_name] = _format_field_value(field)
        rows.append(row_dict)
    return rows


def execute_statement(
    resource_arn: str,
    secret_arn: str,
    sql: str,
    *,
    database: str | None = None,
    schema: str | None = None,
    parameters: list[dict[str, Any]] | None = None,
    transaction_id: str | None = None,
    include_result_metadata: bool = True,
) -> dict[str, Any]:
    clean_sql = (sql or '').strip()
    if not clean_sql:
        raise ValueError('SQL statement is required')

    clean_resource = (resource_arn or '').strip()
    if not clean_resource:
        raise ValueError('Resource ARN (Database/Cluster ARN) is required')

    clean_secret = (secret_arn or '').strip()
    if not clean_secret:
        raise ValueError('Secret ARN (Credentials ARN) is required')

    payload: dict[str, Any] = {
        'resourceArn': clean_resource,
        'secretArn': clean_secret,
        'sql': clean_sql,
        'includeResultMetadata': bool(include_result_metadata),
    }
    if database:
        payload['database'] = str(database).strip()
    if schema:
        payload['schema'] = str(schema).strip()
    if parameters:
        payload['parameters'] = parameters
    if transaction_id:
        payload['transactionId'] = str(transaction_id).strip()

    client = _rdsdata_client()
    response = client.execute_statement(**payload)

    column_metadata = response.get('columnMetadata', [])
    records_raw = response.get('records', [])
    columns = [col.get('name') or f'col_{i+1}' for i, col in enumerate(column_metadata)]
    formatted_rows = _format_records(records_raw, column_metadata)

    return {
        'sql': clean_sql,
        'resource_arn': clean_resource,
        'database': database,
        'columns': columns,
        'column_metadata': column_metadata,
        'records': formatted_rows,
        'row_count': len(formatted_rows),
        'number_of_records_updated': response.get('numberOfRecordsUpdated', 0),
        'generated_fields': [_format_field_value(f) for f in response.get('generatedFields', [])],
        'response': _clean_response(response),
    }


def batch_execute_statement(
    resource_arn: str,
    secret_arn: str,
    sql: str,
    parameter_sets: list[list[dict[str, Any]]],
    *,
    database: str | None = None,
    schema: str | None = None,
    transaction_id: str | None = None,
) -> dict[str, Any]:
    clean_sql = (sql or '').strip()
    if not clean_sql:
        raise ValueError('SQL statement is required')
    clean_resource = (resource_arn or '').strip()
    if not clean_resource:
        raise ValueError('Resource ARN is required')
    clean_secret = (secret_arn or '').strip()
    if not clean_secret:
        raise ValueError('Secret ARN is required')

    payload: dict[str, Any] = {
        'resourceArn': clean_resource,
        'secretArn': clean_secret,
        'sql': clean_sql,
        'parameterSets': parameter_sets or [],
    }
    if database:
        payload['database'] = str(database).strip()
    if schema:
        payload['schema'] = str(schema).strip()
    if transaction_id:
        payload['transactionId'] = str(transaction_id).strip()

    client = _rdsdata_client()
    response = client.batch_execute_statement(**payload)
    return {
        'sql': clean_sql,
        'update_results': response.get('updateResults', []),
        'response': _clean_response(response),
    }


def begin_transaction(
    resource_arn: str,
    secret_arn: str,
    *,
    database: str | None = None,
    schema: str | None = None,
) -> dict[str, Any]:
    clean_resource = (resource_arn or '').strip()
    clean_secret = (secret_arn or '').strip()
    if not clean_resource or not clean_secret:
        raise ValueError('Resource ARN and Secret ARN are required')

    payload: dict[str, Any] = {
        'resourceArn': clean_resource,
        'secretArn': clean_secret,
    }
    if database:
        payload['database'] = str(database).strip()
    if schema:
        payload['schema'] = str(schema).strip()

    response = _rdsdata_client().begin_transaction(**payload)
    return {
        'transaction_id': response.get('transactionId'),
        'resource_arn': clean_resource,
        'database': database,
        'response': _clean_response(response),
    }


def commit_transaction(
    resource_arn: str,
    secret_arn: str,
    transaction_id: str,
) -> dict[str, Any]:
    clean_tx = (transaction_id or '').strip()
    if not clean_tx:
        raise ValueError('Transaction ID is required')
    response = _rdsdata_client().commit_transaction(
        resourceArn=resource_arn.strip(),
        secretArn=secret_arn.strip(),
        transactionId=clean_tx,
    )
    return {
        'transaction_id': clean_tx,
        'transaction_status': response.get('transactionStatus'),
        'response': _clean_response(response),
    }


def rollback_transaction(
    resource_arn: str,
    secret_arn: str,
    transaction_id: str,
) -> dict[str, Any]:
    clean_tx = (transaction_id or '').strip()
    if not clean_tx:
        raise ValueError('Transaction ID is required')
    response = _rdsdata_client().rollback_transaction(
        resourceArn=resource_arn.strip(),
        secretArn=secret_arn.strip(),
        transactionId=clean_tx,
    )
    return {
        'transaction_id': clean_tx,
        'transaction_status': response.get('transactionStatus'),
        'response': _clean_response(response),
    }
