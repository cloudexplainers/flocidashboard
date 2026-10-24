"""DynamoDB workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _describe_log_streams_if_present,
    _dynamodb_client,
    _error_code,
    _iam_client,
    _ignore_missing,
    _ignore_missing_codes,
    _json_text,
    _lambda_client,
    _logs_client,
    _verify_role,
    _wait_for_verification,
)
from . import dynamodb_scale_labs as capacity_at_scale
from .iam_labs import (
    _run_iam_create_role,
)
from .lambda_labs import (
    LAMBDA_HANDLER,
    LAMBDA_LAB_TRUST_POLICY,
    LAMBDA_RUNTIME,
    _lambda_function_zip_bytes,
    _lambda_payload_json,
)


DYNAMODB_TABLE_NAME = 'lab-orders'
DYNAMODB_CUSTOMER_INDEX_NAME = 'CustomerIdIndex'
DYNAMODB_ORDER_ID = 'ORDER#1001'
DYNAMODB_CUSTOMER_ID = 'CUSTOMER#42'
DYNAMODB_INITIAL_TOTAL = '42.50'
DYNAMODB_UPDATED_TOTAL = '84.00'
DYNAMODB_ORDER_ITEM = {
    'OrderId': {'S': DYNAMODB_ORDER_ID},
    'CustomerId': {'S': DYNAMODB_CUSTOMER_ID},
    'Status': {'S': 'CREATED'},
    'Total': {'N': DYNAMODB_INITIAL_TOTAL},
}
DYNAMODB_UPDATED_ATTRIBUTES = {
    ':status': {'S': 'PAID'},
    ':total': {'N': DYNAMODB_UPDATED_TOTAL},
}
DYNAMODB_UPDATE_ATTRIBUTE_NAMES = {
    '#status': 'Status',
    '#total': 'Total',
}
DYNAMODB_PUT_CACHE_KEY = 'floci-lab:dynamodb:crud-query:put-item'
DYNAMODB_UPDATE_CACHE_KEY = 'floci-lab:dynamodb:crud-query:update-item'
DYNAMODB_QUERY_CACHE_KEY = 'floci-lab:dynamodb:crud-query:query'
DYNAMODB_DELETE_ITEM_CACHE_KEY = 'floci-lab:dynamodb:crud-query:delete-item'
DYNAMODB_DELETE_TABLE_CACHE_KEY = 'floci-lab:dynamodb:crud-query:delete-table'
LAMBDA_DYNAMODB_TABLE_NAME = 'lab-lambda-orders'
LAMBDA_DYNAMODB_FUNCTION_NAME = 'lab-order-writer'
LAMBDA_DYNAMODB_ROLE_NAME = 'FlociLambdaDynamoDbRole'
LAMBDA_DYNAMODB_POLICY_NAME = 'FlociLambdaDynamoDbWrite'
LAMBDA_DYNAMODB_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_DYNAMODB_ROLE_NAME}'
LAMBDA_DYNAMODB_FUNCTION_ARN = f'arn:aws:lambda:{AWS_REGION}:{AWS_ACCOUNT_ID}:function:{LAMBDA_DYNAMODB_FUNCTION_NAME}'
LAMBDA_DYNAMODB_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_DYNAMODB_FUNCTION_NAME}'
LAMBDA_DYNAMODB_ORDER_ID = 'ORDER#2001'
LAMBDA_DYNAMODB_CUSTOMER_ID = 'CUSTOMER#84'
LAMBDA_DYNAMODB_PAYLOAD = {
    'order_id': LAMBDA_DYNAMODB_ORDER_ID,
    'customer_id': LAMBDA_DYNAMODB_CUSTOMER_ID,
    'total': '128.25',
    'request_id': 'FLOCI-LAMBDA-DDB-2001',
}
LAMBDA_DYNAMODB_PAYLOAD_TEXT = json.dumps(LAMBDA_DYNAMODB_PAYLOAD, separators=(',', ':'))
LAMBDA_DYNAMODB_INVOKE_CACHE_KEY = 'floci-lab:dynamodb:lambda-writes:invoke'
LAMBDA_DYNAMODB_LOGS_CACHE_KEY = 'floci-lab:dynamodb:lambda-writes:logs'
LAMBDA_DYNAMODB_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': 'dynamodb:PutItem',
            'Resource': f'arn:aws:dynamodb:{AWS_REGION}:{AWS_ACCOUNT_ID}:table/{LAMBDA_DYNAMODB_TABLE_NAME}',
        },
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_DYNAMODB_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_DYNAMODB_HANDLER_SOURCE = '''import json
import os

import boto3

dynamodb = boto3.client("dynamodb")
TABLE_NAME = os.environ["TABLE_NAME"]


def lambda_handler(event, context):
    order_id = event["order_id"]
    customer_id = event["customer_id"]
    request_id = event["request_id"]
    total = event["total"]
    item = {
        "OrderId": {"S": order_id},
        "CustomerId": {"S": customer_id},
        "Status": {"S": "RECEIVED"},
        "Total": {"N": total},
        "RequestId": {"S": request_id},
        "WrittenBy": {"S": "lambda"},
    }
    dynamodb.put_item(TableName=TABLE_NAME, Item=item)
    print(f"wrote {order_id} for {customer_id} with {request_id}")
    return {
        "ok": True,
        "table": TABLE_NAME,
        "order_id": order_id,
        "request_id": request_id,
    }
'''

DYNAMODB_CRUD_QUERY_LAB = {
    'service': 'dynamodb',
    'key': 'crud-query',
    'title': 'Create a DynamoDB table and query items',
    'description': 'Create a local orders table, write an item, read it by key, update it, query by customer, delete it, and remove the table.',
    'steps': [
        {
            'key': 'create-table',
            'title': 'Create the orders table',
            'command': f'aws dynamodb create-table --table-name {DYNAMODB_TABLE_NAME} --attribute-definitions file://attribute-definitions.json --key-schema file://key-schema.json --global-secondary-indexes file://customer-index.json --billing-mode PAY_PER_REQUEST',
            'explanation': 'Creates a table keyed by OrderId with a customer lookup global secondary index.',
            'artifact_label': 'attribute-definitions.json',
            'artifact': json.dumps([
                {'AttributeName': 'OrderId', 'AttributeType': 'S'},
                {'AttributeName': 'CustomerId', 'AttributeType': 'S'},
            ], indent=2),
        },
        {
            'key': 'put-item',
            'title': 'Write an order item',
            'command': f'aws dynamodb put-item --table-name {DYNAMODB_TABLE_NAME} --item file://order-item.json',
            'explanation': 'Stores a single order using DynamoDB attribute value JSON.',
            'artifact_label': 'order-item.json',
            'artifact': json.dumps(DYNAMODB_ORDER_ITEM, indent=2),
        },
        {
            'key': 'get-item',
            'title': 'Read the order by key',
            'command': f'aws dynamodb get-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json',
            'explanation': 'Reads the item by its partition key and verifies the original values.',
            'artifact_label': 'order-key.json',
            'artifact': json.dumps({'OrderId': {'S': DYNAMODB_ORDER_ID}}, indent=2),
        },
        {
            'key': 'update-item',
            'title': 'Update order status and total',
            'command': f'aws dynamodb update-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json --update-expression "SET #status = :status, #total = :total" --expression-attribute-names file://attribute-names.json --expression-attribute-values file://updated-values.json --return-values ALL_NEW',
            'explanation': 'Updates the mutable attributes and returns the new item image.',
            'artifact_label': 'updated-values.json',
            'artifact': json.dumps(DYNAMODB_UPDATED_ATTRIBUTES, indent=2),
            'secondary_artifact_label': 'attribute-names.json',
            'secondary_artifact': json.dumps(DYNAMODB_UPDATE_ATTRIBUTE_NAMES, indent=2),
        },
        {
            'key': 'query-customer-index',
            'title': 'Query orders by customer',
            'command': f'aws dynamodb query --table-name {DYNAMODB_TABLE_NAME} --index-name {DYNAMODB_CUSTOMER_INDEX_NAME} --key-condition-expression "CustomerId = :customer" --expression-attribute-values file://query-values.json',
            'explanation': 'Uses the global secondary index to find orders for one customer.',
            'artifact_label': 'query-values.json',
            'artifact': json.dumps({':customer': {'S': DYNAMODB_CUSTOMER_ID}}, indent=2),
        },
        {
            'key': 'delete-item',
            'title': 'Delete the order item',
            'command': f'aws dynamodb delete-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json',
            'explanation': 'Removes the order while leaving the table available for more writes.',
        },
        {
            'key': 'delete-table',
            'title': 'Delete the table',
            'command': f'aws dynamodb delete-table --table-name {DYNAMODB_TABLE_NAME}',
            'explanation': 'Deletes the lab-owned table after the item lifecycle is complete.',
        },
    ],
}

DYNAMODB_LAMBDA_WRITES_LAB = {
    'service': 'dynamodb',
    'key': 'lambda-writes',
    'title': 'Write DynamoDB items from Lambda',
    'description': 'Create a DynamoDB table, grant a Lambda function least-privilege write access, invoke it with an order event, and verify the item and logs.',
    'steps': [
        {
            'key': 'create-table',
            'title': 'Create the Lambda orders table',
            'command': f'aws dynamodb create-table --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --attribute-definitions AttributeName=OrderId,AttributeType=S --key-schema AttributeName=OrderId,KeyType=HASH --billing-mode PAY_PER_REQUEST',
            'explanation': 'Creates the table that the Lambda function will write to.',
        },
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_DYNAMODB_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates the IAM role that the Lambda service can assume.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow table writes and logs',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_DYNAMODB_ROLE_NAME} --policy-name {LAMBDA_DYNAMODB_POLICY_NAME} --policy-document file://lambda-dynamodb-policy.json',
            'explanation': 'Grants only DynamoDB PutItem on the lab table plus CloudWatch Logs write permissions.',
            'artifact_label': 'lambda-dynamodb-policy.json',
            'artifact': json.dumps(LAMBDA_DYNAMODB_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the order writer function',
            'command': f'aws lambda create-function --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_DYNAMODB_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip --environment Variables={{TABLE_NAME={LAMBDA_DYNAMODB_TABLE_NAME}}}',
            'explanation': 'Packages a small Python handler that writes the incoming order event to DynamoDB.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_DYNAMODB_HANDLER_SOURCE,
        },
        {
            'key': 'invoke-function',
            'title': 'Invoke the writer',
            'command': f'aws lambda invoke --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} --payload file://order-event.json response.json',
            'explanation': 'Sends an order event to Lambda and verifies the synchronous response.',
            'artifact_label': 'order-event.json',
            'artifact': json.dumps(LAMBDA_DYNAMODB_PAYLOAD, indent=2),
        },
        {
            'key': 'get-written-item',
            'title': 'Read the written item',
            'command': f'aws dynamodb get-item --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --key file://order-key.json',
            'explanation': 'Reads DynamoDB directly to prove the Lambda invocation wrote the order item.',
            'artifact_label': 'order-key.json',
            'artifact': json.dumps({'OrderId': {'S': LAMBDA_DYNAMODB_ORDER_ID}}, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect Lambda logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Verifies that the function logged the order write request ID.',
        },
    ],
}


def _verify_dynamodb_table_exists() -> dict[str, Any]:
    try:
        table = _dynamodb_client().describe_table(TableName=DYNAMODB_TABLE_NAME).get('Table', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    indexes = table.get('GlobalSecondaryIndexes') or []
    has_index = any(index.get('IndexName') == DYNAMODB_CUSTOMER_INDEX_NAME for index in indexes)
    if table.get('TableName') == DYNAMODB_TABLE_NAME and has_index:
        return {
            'status': 'passed',
            'message': f'Table {DYNAMODB_TABLE_NAME} exists with the customer lookup index.',
            'resource': _clean_response(table),
        }
    return {
        'status': 'failed',
        'message': f'Table {DYNAMODB_TABLE_NAME} does not match the expected schema.',
    }


def _dynamodb_order_key() -> dict[str, Any]:
    return {'OrderId': {'S': DYNAMODB_ORDER_ID}}


def _get_dynamodb_order_item() -> dict[str, Any] | None:
    return _dynamodb_client().get_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
    ).get('Item')


def _verify_dynamodb_order_created() -> dict[str, Any]:
    try:
        item = _get_dynamodb_order_item()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        item
        and item.get('OrderId', {}).get('S') == DYNAMODB_ORDER_ID
        and item.get('CustomerId', {}).get('S') == DYNAMODB_CUSTOMER_ID
        and item.get('Status', {}).get('S') == 'CREATED'
    ):
        return {
            'status': 'passed',
            'message': f'Order {DYNAMODB_ORDER_ID} exists with the initial attributes.',
            'resource': _clean_response(item),
        }
    return {
        'status': 'failed',
        'message': f'Order {DYNAMODB_ORDER_ID} was not returned with the initial values.',
    }


def _verify_dynamodb_order_updated() -> dict[str, Any]:
    try:
        item = _get_dynamodb_order_item()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        item
        and item.get('Status', {}).get('S') == 'PAID'
        and item.get('Total', {}).get('N') == DYNAMODB_UPDATED_TOTAL
    ):
        return {
            'status': 'passed',
            'message': f'Order {DYNAMODB_ORDER_ID} is updated to PAID with the new total.',
            'resource': _clean_response(item),
        }
    return {
        'status': 'failed',
        'message': f'Order {DYNAMODB_ORDER_ID} was not returned with the updated values.',
    }


def _verify_dynamodb_customer_query() -> dict[str, Any]:
    try:
        response = _dynamodb_client().query(
            TableName=DYNAMODB_TABLE_NAME,
            IndexName=DYNAMODB_CUSTOMER_INDEX_NAME,
            KeyConditionExpression='CustomerId = :customer',
            ExpressionAttributeValues={':customer': {'S': DYNAMODB_CUSTOMER_ID}},
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    for item in response.get('Items', []):
        if (
            item.get('OrderId', {}).get('S') == DYNAMODB_ORDER_ID
            and item.get('Status', {}).get('S') == 'PAID'
        ):
            return {
                'status': 'passed',
                'message': f'The customer index returns order {DYNAMODB_ORDER_ID}.',
                'resource': _clean_response(response),
            }
    return {
        'status': 'failed',
        'message': f'The customer index did not return order {DYNAMODB_ORDER_ID}.',
    }


def _verify_dynamodb_order_deleted() -> dict[str, Any]:
    try:
        item = _get_dynamodb_order_item()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if not item:
        return {
            'status': 'passed',
            'message': f'Order {DYNAMODB_ORDER_ID} is absent from {DYNAMODB_TABLE_NAME}.',
        }
    return {
        'status': 'failed',
        'message': f'Order {DYNAMODB_ORDER_ID} still exists.',
        'resource': _clean_response(item),
    }


def _verify_dynamodb_table_deleted() -> dict[str, Any]:
    try:
        _dynamodb_client().describe_table(TableName=DYNAMODB_TABLE_NAME)
    except ClientError as exc:
        if _error_code(exc) == 'ResourceNotFoundException':
            return {
                'status': 'passed',
                'message': f'Table {DYNAMODB_TABLE_NAME} is deleted.',
            }
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'Table {DYNAMODB_TABLE_NAME} still exists.',
    }


def _verify_lambda_dynamodb_table() -> dict[str, Any]:
    try:
        table = _dynamodb_client().describe_table(TableName=LAMBDA_DYNAMODB_TABLE_NAME).get('Table', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if table.get('TableName') == LAMBDA_DYNAMODB_TABLE_NAME:
        return {
            'status': 'passed',
            'message': f'Table {LAMBDA_DYNAMODB_TABLE_NAME} exists for Lambda writes.',
            'resource': _clean_response(table),
        }
    return {
        'status': 'failed',
        'message': f'Table {LAMBDA_DYNAMODB_TABLE_NAME} was not returned.',
    }


def _verify_lambda_dynamodb_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_DYNAMODB_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_dynamodb_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_DYNAMODB_ROLE_NAME,
            PolicyName=LAMBDA_DYNAMODB_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyName') == LAMBDA_DYNAMODB_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_DYNAMODB_POLICY_NAME} is embedded in {LAMBDA_DYNAMODB_ROLE_NAME}.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_DYNAMODB_POLICY_NAME} was not returned.',
    }


def _verify_lambda_dynamodb_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    configuration = response.get('Configuration', response)
    environment = configuration.get('Environment', {}).get('Variables', {})
    if (
        configuration.get('FunctionName') == LAMBDA_DYNAMODB_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_DYNAMODB_ROLE_ARN
        and environment.get('TABLE_NAME') == LAMBDA_DYNAMODB_TABLE_NAME
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_DYNAMODB_FUNCTION_NAME} exists with the table environment variable.',
            'resource': _clean_response(configuration),
        }
    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_DYNAMODB_FUNCTION_NAME} does not match the lab configuration.',
    }


def _verify_lambda_dynamodb_invocation() -> dict[str, Any]:
    cached = cache.get(LAMBDA_DYNAMODB_INVOKE_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_DYNAMODB_FUNCTION_NAME} returned a successful order-write response.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful Lambda-to-DynamoDB invocation has been recorded for this lab run.',
    }


def _lambda_dynamodb_order_key() -> dict[str, Any]:
    return {'OrderId': {'S': LAMBDA_DYNAMODB_ORDER_ID}}


def _verify_lambda_dynamodb_item() -> dict[str, Any]:
    try:
        item = _dynamodb_client().get_item(
            TableName=LAMBDA_DYNAMODB_TABLE_NAME,
            Key=_lambda_dynamodb_order_key(),
        ).get('Item')
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        item
        and item.get('OrderId', {}).get('S') == LAMBDA_DYNAMODB_ORDER_ID
        and item.get('RequestId', {}).get('S') == LAMBDA_DYNAMODB_PAYLOAD['request_id']
        and item.get('WrittenBy', {}).get('S') == 'lambda'
    ):
        return {
            'status': 'passed',
            'message': f'Lambda wrote order {LAMBDA_DYNAMODB_ORDER_ID} to DynamoDB.',
            'resource': _clean_response(item),
        }
    return {
        'status': 'failed',
        'message': f'Order {LAMBDA_DYNAMODB_ORDER_ID} was not written by Lambda.',
    }


def _verify_lambda_dynamodb_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_DYNAMODB_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_DYNAMODB_PAYLOAD["request_id"]} write.',
            'resource': cached,
        }
    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_DYNAMODB_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_DYNAMODB_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                if LAMBDA_DYNAMODB_PAYLOAD['request_id'] in event.get('message', ''):
                    resource = {
                        'log_group_name': LAMBDA_DYNAMODB_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_DYNAMODB_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_DYNAMODB_PAYLOAD["request_id"]} write.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_DYNAMODB_PAYLOAD["request_id"]} was found.',
    }


def _dynamodb_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'dynamodb',
        'lab': 'crud-query',
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_dynamodb_create_table() -> dict[str, Any]:
    command = (
        f'aws dynamodb create-table --table-name {DYNAMODB_TABLE_NAME} '
        '--attribute-definitions file://attribute-definitions.json '
        '--key-schema file://key-schema.json '
        '--global-secondary-indexes file://customer-index.json '
        '--billing-mode PAY_PER_REQUEST'
    )
    started = time.perf_counter()
    try:
        response = _dynamodb_client().create_table(
            TableName=DYNAMODB_TABLE_NAME,
            AttributeDefinitions=[
                {'AttributeName': 'OrderId', 'AttributeType': 'S'},
                {'AttributeName': 'CustomerId', 'AttributeType': 'S'},
            ],
            KeySchema=[{'AttributeName': 'OrderId', 'KeyType': 'HASH'}],
            GlobalSecondaryIndexes=[{
                'IndexName': DYNAMODB_CUSTOMER_INDEX_NAME,
                'KeySchema': [{'AttributeName': 'CustomerId', 'KeyType': 'HASH'}],
                'Projection': {'ProjectionType': 'ALL'},
            }],
            BillingMode='PAY_PER_REQUEST',
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceInUseException':
            raise
        response = _dynamodb_client().describe_table(TableName=DYNAMODB_TABLE_NAME)
    return _dynamodb_step_result(
        'create-table',
        command,
        response,
        _verify_dynamodb_table_exists(),
        started,
    )


def _run_dynamodb_put_item() -> dict[str, Any]:
    command = f'aws dynamodb put-item --table-name {DYNAMODB_TABLE_NAME} --item file://order-item.json'
    started = time.perf_counter()
    response = _dynamodb_client().put_item(
        TableName=DYNAMODB_TABLE_NAME,
        Item=DYNAMODB_ORDER_ITEM,
    )
    verification = _verify_dynamodb_order_created()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_PUT_CACHE_KEY, verification.get('resource', {}), timeout=3600)
    return _dynamodb_step_result(
        'put-item',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_get_item() -> dict[str, Any]:
    command = f'aws dynamodb get-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json'
    started = time.perf_counter()
    response = _dynamodb_client().get_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
    )
    return _dynamodb_step_result(
        'get-item',
        command,
        response,
        _verify_dynamodb_order_created(),
        started,
    )


def _run_dynamodb_update_item() -> dict[str, Any]:
    command = (
        f'aws dynamodb update-item --table-name {DYNAMODB_TABLE_NAME} '
        '--key file://order-key.json '
        '--update-expression "SET #status = :status, #total = :total" '
        '--expression-attribute-names file://attribute-names.json '
        '--expression-attribute-values file://updated-values.json '
        '--return-values ALL_NEW'
    )
    started = time.perf_counter()
    response = _dynamodb_client().update_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
        UpdateExpression='SET #status = :status, #total = :total',
        ExpressionAttributeNames=DYNAMODB_UPDATE_ATTRIBUTE_NAMES,
        ExpressionAttributeValues=DYNAMODB_UPDATED_ATTRIBUTES,
        ReturnValues='ALL_NEW',
    )
    verification = _verify_dynamodb_order_updated()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_UPDATE_CACHE_KEY, verification.get('resource', {}), timeout=3600)
    return _dynamodb_step_result(
        'update-item',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_query_customer_index() -> dict[str, Any]:
    command = (
        f'aws dynamodb query --table-name {DYNAMODB_TABLE_NAME} '
        f'--index-name {DYNAMODB_CUSTOMER_INDEX_NAME} '
        '--key-condition-expression "CustomerId = :customer" '
        '--expression-attribute-values file://query-values.json'
    )
    started = time.perf_counter()
    response = _dynamodb_client().query(
        TableName=DYNAMODB_TABLE_NAME,
        IndexName=DYNAMODB_CUSTOMER_INDEX_NAME,
        KeyConditionExpression='CustomerId = :customer',
        ExpressionAttributeValues={':customer': {'S': DYNAMODB_CUSTOMER_ID}},
    )
    verification = _verify_dynamodb_customer_query()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_QUERY_CACHE_KEY, verification.get('resource', {}), timeout=3600)
    return _dynamodb_step_result(
        'query-customer-index',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_delete_item() -> dict[str, Any]:
    command = f'aws dynamodb delete-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json'
    started = time.perf_counter()
    response = _dynamodb_client().delete_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
    )
    verification = _verify_dynamodb_order_deleted()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_DELETE_ITEM_CACHE_KEY, True, timeout=3600)
    return _dynamodb_step_result(
        'delete-item',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_delete_table() -> dict[str, Any]:
    command = f'aws dynamodb delete-table --table-name {DYNAMODB_TABLE_NAME}'
    started = time.perf_counter()
    try:
        response = _dynamodb_client().delete_table(TableName=DYNAMODB_TABLE_NAME)
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
        response = {}
    verification = _verify_dynamodb_table_deleted()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_DELETE_TABLE_CACHE_KEY, True, timeout=3600)
    return _dynamodb_step_result(
        'delete-table',
        command,
        response,
        verification,
        started,
    )


def _lambda_dynamodb_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'dynamodb',
        'lab': 'lambda-writes',
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_dynamodb_create_table() -> dict[str, Any]:
    command = (
        f'aws dynamodb create-table --table-name {LAMBDA_DYNAMODB_TABLE_NAME} '
        '--attribute-definitions AttributeName=OrderId,AttributeType=S '
        '--key-schema AttributeName=OrderId,KeyType=HASH --billing-mode PAY_PER_REQUEST'
    )
    started = time.perf_counter()
    try:
        response = _dynamodb_client().create_table(
            TableName=LAMBDA_DYNAMODB_TABLE_NAME,
            AttributeDefinitions=[{'AttributeName': 'OrderId', 'AttributeType': 'S'}],
            KeySchema=[{'AttributeName': 'OrderId', 'KeyType': 'HASH'}],
            BillingMode='PAY_PER_REQUEST',
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceInUseException':
            raise
        response = _dynamodb_client().describe_table(TableName=LAMBDA_DYNAMODB_TABLE_NAME)
    return _lambda_dynamodb_step_result(
        'create-table',
        command,
        response,
        _verify_lambda_dynamodb_table(),
        started,
    )


def _run_lambda_dynamodb_create_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='lambda-writes',
        role_name=LAMBDA_DYNAMODB_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': 'dynamodb'}


def _run_lambda_dynamodb_put_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_DYNAMODB_ROLE_NAME} '
        f'--policy-name {LAMBDA_DYNAMODB_POLICY_NAME} '
        '--policy-document file://lambda-dynamodb-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_DYNAMODB_ROLE_NAME,
        PolicyName=LAMBDA_DYNAMODB_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_DYNAMODB_POLICY),
    )
    return _lambda_dynamodb_step_result(
        'put-role-policy',
        command,
        {},
        _verify_lambda_dynamodb_policy(),
        started,
    )


def _run_lambda_dynamodb_create_function() -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_DYNAMODB_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip '
        f'--environment Variables={{TABLE_NAME={LAMBDA_DYNAMODB_TABLE_NAME}}}'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_DYNAMODB_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes(LAMBDA_DYNAMODB_HANDLER_SOURCE)},
            Description='Floci dashboard Lambda to DynamoDB workflow lab.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
            Environment={'Variables': {'TABLE_NAME': LAMBDA_DYNAMODB_TABLE_NAME}},
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME)
    return _lambda_dynamodb_step_result(
        'create-function',
        command,
        response,
        _verify_lambda_dynamodb_function(),
        started,
    )


def _run_lambda_dynamodb_invoke() -> dict[str, Any]:
    command = f'aws lambda invoke --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} --payload file://order-event.json response.json'
    started = time.perf_counter()
    response = _lambda_client().invoke(
        FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=LAMBDA_DYNAMODB_PAYLOAD_TEXT.encode('utf-8'),
    )
    payload_json = _lambda_payload_json(response.get('Payload'))
    normalized = {
        'StatusCode': response.get('StatusCode'),
        'ExecutedVersion': response.get('ExecutedVersion'),
        'FunctionError': response.get('FunctionError'),
        'Payload': payload_json,
    }
    verified = (
        response.get('StatusCode') in {200, 202}
        and not response.get('FunctionError')
        and payload_json.get('ok') is True
        and payload_json.get('order_id') == LAMBDA_DYNAMODB_ORDER_ID
        and payload_json.get('request_id') == LAMBDA_DYNAMODB_PAYLOAD['request_id']
    )
    if verified:
        cache.set(LAMBDA_DYNAMODB_INVOKE_CACHE_KEY, _clean_response(normalized), timeout=3600)
    verification = (
        _verify_lambda_dynamodb_invocation()
        if verified
        else {
            'status': 'failed',
            'message': 'Lambda did not return the expected DynamoDB write response.',
            'resource': _clean_response(normalized),
        }
    )
    return _lambda_dynamodb_step_result(
        'invoke-function',
        command,
        normalized,
        verification,
        started,
    )


def _run_lambda_dynamodb_get_item() -> dict[str, Any]:
    command = f'aws dynamodb get-item --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --key file://order-key.json'
    started = time.perf_counter()
    response = _dynamodb_client().get_item(
        TableName=LAMBDA_DYNAMODB_TABLE_NAME,
        Key=_lambda_dynamodb_order_key(),
    )
    return _lambda_dynamodb_step_result(
        'get-written-item',
        command,
        response,
        _verify_lambda_dynamodb_item(),
        started,
    )


def _run_lambda_dynamodb_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    verification = _wait_for_verification(_verify_lambda_dynamodb_logs)
    response = _describe_log_streams_if_present(LAMBDA_DYNAMODB_LOG_GROUP_NAME)
    return _lambda_dynamodb_step_result(
        'inspect-logs',
        command,
        response,
        verification,
        started,
    )


def _reset_dynamodb_crud_query() -> dict[str, Any]:
    command = (
        f'aws dynamodb delete-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json\n'
        f'aws dynamodb delete-table --table-name {DYNAMODB_TABLE_NAME}'
    )
    started = time.perf_counter()
    dynamodb = _dynamodb_client()
    deleted_item = False
    deleted_table = False
    try:
        dynamodb.delete_item(TableName=DYNAMODB_TABLE_NAME, Key=_dynamodb_order_key())
        deleted_item = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        dynamodb.delete_table(TableName=DYNAMODB_TABLE_NAME)
        deleted_table = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    cache.delete_many([
        DYNAMODB_PUT_CACHE_KEY,
        DYNAMODB_UPDATE_CACHE_KEY,
        DYNAMODB_QUERY_CACHE_KEY,
        DYNAMODB_DELETE_ITEM_CACHE_KEY,
        DYNAMODB_DELETE_TABLE_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {'deleted_item': deleted_item, 'deleted_table': deleted_table}

    return {
        'service': 'dynamodb',
        'lab': 'crud-query',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': f'Table {DYNAMODB_TABLE_NAME}, its lab item, and recorded DynamoDB lab state were removed.',
        },
    }


def _reset_dynamodb_lambda_writes() -> dict[str, Any]:
    command = (
        f'aws lambda delete-function --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_DYNAMODB_ROLE_NAME} '
        f'--policy-name {LAMBDA_DYNAMODB_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_DYNAMODB_ROLE_NAME}\n'
        f'aws dynamodb delete-item --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --key file://order-key.json\n'
        f'aws dynamodb delete-table --table-name {LAMBDA_DYNAMODB_TABLE_NAME}'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    dynamodb = _dynamodb_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_DYNAMODB_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_DYNAMODB_ROLE_NAME,
        PolicyName=LAMBDA_DYNAMODB_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_DYNAMODB_ROLE_NAME))
    deleted_item = False
    deleted_table = False
    try:
        dynamodb.delete_item(
            TableName=LAMBDA_DYNAMODB_TABLE_NAME,
            Key=_lambda_dynamodb_order_key(),
        )
        deleted_item = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        dynamodb.delete_table(TableName=LAMBDA_DYNAMODB_TABLE_NAME)
        deleted_table = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    cache.delete_many([LAMBDA_DYNAMODB_INVOKE_CACHE_KEY, LAMBDA_DYNAMODB_LOGS_CACHE_KEY])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_item': deleted_item,
        'deleted_table': deleted_table,
    }

    return {
        'service': 'dynamodb',
        'lab': 'lambda-writes',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The Lambda writer, log group, IAM role, DynamoDB item, table, and recorded lab state were removed.',
        },
    }


LABS = [DYNAMODB_CRUD_QUERY_LAB, DYNAMODB_LAMBDA_WRITES_LAB, capacity_at_scale.LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'dynamodb' and lab_key == 'capacity-at-scale':
        from .dynamodb_scale_labs import run_step as scale_run_step
        return scale_run_step(step_key)
    if service_key == 'dynamodb' and lab_key == 'crud-query':
        runners = {
            'create-table': _run_dynamodb_create_table,
            'put-item': _run_dynamodb_put_item,
            'get-item': _run_dynamodb_get_item,
            'update-item': _run_dynamodb_update_item,
            'query-customer-index': _run_dynamodb_query_customer_index,
            'delete-item': _run_dynamodb_delete_item,
            'delete-table': _run_dynamodb_delete_table,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'dynamodb' and lab_key == 'lambda-writes':
        runners = {
            'create-table': _run_lambda_dynamodb_create_table,
            'create-role': _run_lambda_dynamodb_create_role,
            'put-role-policy': _run_lambda_dynamodb_put_policy,
            'create-function': _run_lambda_dynamodb_create_function,
            'invoke-function': _run_lambda_dynamodb_invoke,
            'get-written-item': _run_lambda_dynamodb_get_item,
            'inspect-logs': _run_lambda_dynamodb_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'dynamodb' and lab_key == 'capacity-at-scale':
        from .dynamodb_scale_labs import status as scale_status
        return scale_status()
    if service_key == 'dynamodb' and lab_key == 'crud-query':
        table_deleted_verification = _verify_dynamodb_table_deleted()
        table_deleted = (
            table_deleted_verification.get('status') == 'passed'
            and bool(cache.get(DYNAMODB_DELETE_TABLE_CACHE_KEY))
        )
        if table_deleted:
            return {
                'service': service_key,
                'lab': lab_key,
                'complete': True,
                'steps': {
                    'delete-table': {
                        'verified': True,
                        'verification': table_deleted_verification,
                    },
                },
            }

        table_verification = _verify_dynamodb_table_exists()
        created_verification = _verify_dynamodb_order_created()
        updated_verification = _verify_dynamodb_order_updated()
        query_verification = _verify_dynamodb_customer_query()
        deleted_verification = _verify_dynamodb_order_deleted()
        table_verified = table_verification.get('status') == 'passed'
        created_verified = created_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_PUT_CACHE_KEY))
        updated_verified = updated_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_UPDATE_CACHE_KEY))
        query_verified = query_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_QUERY_CACHE_KEY))
        deleted_verified = deleted_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_DELETE_ITEM_CACHE_KEY))
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': False,
            'steps': {
                'create-table': {'verified': table_verified, 'verification': table_verification if table_verified else None},
                'put-item': {'verified': created_verified or updated_verified, 'verification': (updated_verification if updated_verified else created_verification) if (created_verified or updated_verified) else None},
                'get-item': {'verified': created_verified or updated_verified, 'verification': (updated_verification if updated_verified else created_verification) if (created_verified or updated_verified) else None},
                'update-item': {'verified': updated_verified, 'verification': updated_verification if updated_verified else None},
                'query-customer-index': {'verified': query_verified, 'verification': query_verification if query_verified else None},
                'delete-item': {'verified': deleted_verified, 'verification': deleted_verification if deleted_verified else None},
                'delete-table': {'verified': False, 'verification': None},
            },
        }

    if service_key == 'dynamodb' and lab_key == 'lambda-writes':
        table_verification = _verify_lambda_dynamodb_table()
        role_verification = _verify_lambda_dynamodb_role()
        policy_verification = _verify_lambda_dynamodb_policy()
        function_verification = _verify_lambda_dynamodb_function()
        invocation_verification = _verify_lambda_dynamodb_invocation()
        item_verification = _verify_lambda_dynamodb_item()
        logs_verification = _verify_lambda_dynamodb_logs()
        table_verified = table_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        invocation_verified = invocation_verification.get('status') == 'passed'
        item_verified = item_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                table_verified
                and role_verified
                and policy_verified
                and function_verified
                and invocation_verified
                and item_verified
                and logs_verified
            ),
            'steps': {
                'create-table': {'verified': table_verified, 'verification': table_verification if table_verified else None},
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'invoke-function': {'verified': invocation_verified, 'verification': invocation_verification if invocation_verified else None},
                'get-written-item': {'verified': item_verified, 'verification': item_verification if item_verified else None},
                'inspect-logs': {'verified': logs_verified, 'verification': logs_verification if logs_verified else None},
            },
        }

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'dynamodb' and lab_key == 'capacity-at-scale':
        from .dynamodb_scale_labs import reset as scale_reset
        return scale_reset()
    if service_key == 'dynamodb' and lab_key == 'crud-query':
        return _reset_dynamodb_crud_query()

    if service_key == 'dynamodb' and lab_key == 'lambda-writes':
        return _reset_dynamodb_lambda_writes()

    raise ValueError('Lab reset not found')
