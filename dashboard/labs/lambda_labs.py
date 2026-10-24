"""Lambda workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
import zipfile
from io import BytesIO
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response

from . import lambda_lifecycle_labs as async_error_handling
from . import lambda_performance_labs as performance_cost
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _config_lab_step_result,
    _describe_log_streams_if_present,
    _error_code,
    _iam_client,
    _ignore_missing,
    _ignore_missing_codes,
    _json_text,
    _lambda_client,
    _logs_client,
    _parse_json_text,
    _secret_string_json,
    _secretsmanager_client,
    _sqs_client,
    _sqs_queue_url,
    _ssm_client,
    _verify_role,
    _wait_for_verification,
)
from .iam_labs import (
    _run_iam_create_role,
)


LAMBDA_FUNCTION_NAME = 'lab-echo'
LAMBDA_ROLE_NAME = 'FlociLambdaLabRole'
LAMBDA_ROLE_POLICY_NAME = 'FlociLambdaLabLogs'
LAMBDA_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_ROLE_NAME}'
LAMBDA_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_FUNCTION_NAME}'
LAMBDA_RUNTIME = 'python3.11'
LAMBDA_HANDLER = 'handler.lambda_handler'
LAMBDA_INVOKE_PAYLOAD = {
    'message': 'hello from the Lambda lab',
    'request_id': 'FLOCI-LAMBDA-1001',
}
LAMBDA_INVOKE_PAYLOAD_TEXT = json.dumps(LAMBDA_INVOKE_PAYLOAD, separators=(',', ':'))
LAMBDA_INVOKE_CACHE_KEY = 'floci-lab:lambda:create-invoke-logs:invoke'
LAMBDA_LOGS_CACHE_KEY = 'floci-lab:lambda:create-invoke-logs:logs'
LAMBDA_CONFIG_FUNCTION_NAME = 'lab-config-reader'
LAMBDA_CONFIG_ROLE_NAME = 'FlociLambdaConfigRole'
LAMBDA_CONFIG_POLICY_NAME = 'FlociLambdaConfigRead'
LAMBDA_CONFIG_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_CONFIG_ROLE_NAME}'
LAMBDA_CONFIG_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_CONFIG_FUNCTION_NAME}'
LAMBDA_CONFIG_PARAMETER_NAME = '/floci/lab/lambda/config'
LAMBDA_CONFIG_SECRET_NAME = 'lab/lambda-secret'
LAMBDA_CONFIG_PARAMETER_VALUE = {
    'application': 'orders',
    'environment': 'local',
    'feature': 'runtime-config',
}
LAMBDA_CONFIG_SECRET_VALUE = {
    'api_key': 'local-lambda-secret',
    'username': 'floci-lambda',
}
LAMBDA_CONFIG_PAYLOAD = {
    'request_id': 'FLOCI-LAMBDA-CONFIG-3001',
}
LAMBDA_CONFIG_PAYLOAD_TEXT = json.dumps(LAMBDA_CONFIG_PAYLOAD, separators=(',', ':'))
LAMBDA_CONFIG_INVOKE_CACHE_KEY = 'floci-lab:lambda:runtime-config:invoke'
LAMBDA_CONFIG_LOGS_CACHE_KEY = 'floci-lab:lambda:runtime-config:logs'
LAMBDA_SQS_QUEUE_NAME = 'lab-lambda-events'
LAMBDA_SQS_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{LAMBDA_SQS_QUEUE_NAME}'
)
LAMBDA_SQS_FUNCTION_NAME = 'lab-sqs-consumer'
LAMBDA_SQS_ROLE_NAME = 'FlociLambdaSqsConsumerRole'
LAMBDA_SQS_POLICY_NAME = 'FlociLambdaSqsConsumerPolicy'
LAMBDA_SQS_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_SQS_ROLE_NAME}'
LAMBDA_SQS_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_SQS_FUNCTION_NAME}'
LAMBDA_SQS_MESSAGE_BODY = {
    'order_id': 'ORDER#3001',
    'customer_id': 'CUSTOMER#126',
    'total': '54.20',
    'request_id': 'FLOCI-LAMBDA-SQS-4001',
}
LAMBDA_SQS_MESSAGE_BODY_TEXT = json.dumps(LAMBDA_SQS_MESSAGE_BODY, separators=(',', ':'))
LAMBDA_SQS_MESSAGE_ATTRIBUTES = {
    'source': {'DataType': 'String', 'StringValue': 'floci-lab'},
    'event_type': {'DataType': 'String', 'StringValue': 'order.created'},
}
LAMBDA_SQS_MAPPING_CACHE_KEY = 'floci-lab:lambda:sqs-event-source:mapping'
LAMBDA_SQS_SEND_CACHE_KEY = 'floci-lab:lambda:sqs-event-source:send-message'
LAMBDA_SQS_LOGS_CACHE_KEY = 'floci-lab:lambda:sqs-event-source:logs'
LAMBDA_LAB_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Principal': {'Service': 'lambda.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        },
    ],
}
LAMBDA_LAB_LOGS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_HANDLER_SOURCE = '''import json


def lambda_handler(event, context):
    payload = event
    if isinstance(event, dict) and "body" in event:
        body = event.get("body") or "{}"
        payload = json.loads(body) if isinstance(body, str) else body
    message = payload.get("message", "hello") if isinstance(payload, dict) else "hello"
    request_id = payload.get("request_id", "unknown") if isinstance(payload, dict) else "unknown"
    print(f"processed {request_id}: {message}")
    return {
        "ok": True,
        "echo": payload,
        "request_id": request_id,
    }
'''
LAMBDA_CONFIG_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': 'ssm:GetParameter',
            'Resource': f'arn:aws:ssm:{AWS_REGION}:{AWS_ACCOUNT_ID}:parameter{LAMBDA_CONFIG_PARAMETER_NAME}',
        },
        {
            'Effect': 'Allow',
            'Action': 'secretsmanager:GetSecretValue',
            'Resource': f'arn:aws:secretsmanager:{AWS_REGION}:{AWS_ACCOUNT_ID}:secret:{LAMBDA_CONFIG_SECRET_NAME}*',
        },
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_CONFIG_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_CONFIG_HANDLER_SOURCE = '''import json
import os

import boto3

ssm = boto3.client("ssm")
secretsmanager = boto3.client("secretsmanager")

PARAMETER_NAME = os.environ["PARAMETER_NAME"]
SECRET_ID = os.environ["SECRET_ID"]


def lambda_handler(event, context):
    request_id = event.get("request_id", "unknown") if isinstance(event, dict) else "unknown"
    parameter_response = ssm.get_parameter(Name=PARAMETER_NAME, WithDecryption=True)
    config = json.loads(parameter_response["Parameter"]["Value"])
    secret_response = secretsmanager.get_secret_value(SecretId=SECRET_ID)
    secret = json.loads(secret_response["SecretString"])
    print(f"loaded runtime config for {request_id}: {config['application']} / {secret['username']}")
    return {
        "ok": True,
        "request_id": request_id,
        "parameter_name": PARAMETER_NAME,
        "secret_id": SECRET_ID,
        "application": config["application"],
        "environment": config["environment"],
        "secret_username": secret["username"],
        "secret_keys": sorted(secret.keys()),
    }
'''
LAMBDA_SQS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': [
                'sqs:ReceiveMessage',
                'sqs:DeleteMessage',
                'sqs:GetQueueAttributes',
            ],
            'Resource': LAMBDA_SQS_QUEUE_ARN,
        },
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_SQS_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_SQS_HANDLER_SOURCE = '''import json


def lambda_handler(event, context):
    processed = []
    for record in event.get("Records", []):
        body = json.loads(record.get("body") or "{}")
        request_id = body.get("request_id", "unknown")
        order_id = body.get("order_id", "unknown")
        print(f"processed sqs event {request_id} for {order_id}")
        processed.append({
            "message_id": record.get("messageId"),
            "order_id": order_id,
            "request_id": request_id,
        })
    return {
        "ok": True,
        "records": len(processed),
        "processed": processed,
    }
'''
LAMBDA_CREATE_INVOKE_LOGS_LAB = {
    'service': 'lambda',
    'key': 'create-invoke-logs',
    'title': 'Create, invoke, and inspect a Lambda function',
    'description': 'Create a local Lambda execution role and function, invoke it with a JSON event, then inspect the CloudWatch Logs written by the handler.',
    'steps': [
        {
            'key': 'create-role',
            'title': 'Create the execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates the IAM role that the Lambda service can assume when the function runs.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow Lambda to write logs',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_ROLE_NAME} --policy-name {LAMBDA_ROLE_POLICY_NAME} --policy-document file://lambda-logs-policy.json',
            'explanation': 'Adds the minimal CloudWatch Logs permissions the function needs for local log inspection.',
            'artifact_label': 'lambda-logs-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_LOGS_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the echo function',
            'command': f'aws lambda create-function --function-name {LAMBDA_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip',
            'explanation': 'Creates a small Python function from the packaged handler artifact.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_HANDLER_SOURCE,
        },
        {
            'key': 'invoke-function',
            'title': 'Invoke the function',
            'command': f'aws lambda invoke --function-name {LAMBDA_FUNCTION_NAME} --payload file://event.json response.json',
            'explanation': 'Sends a known JSON event and verifies the synchronous Lambda response.',
            'artifact_label': 'event.json',
            'artifact': json.dumps(LAMBDA_INVOKE_PAYLOAD, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect CloudWatch Logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Finds the Lambda log stream and verifies the handler wrote the expected request ID.',
        },
    ],
}

LAMBDA_RUNTIME_CONFIG_LAB = {
    'service': 'lambda',
    'key': 'runtime-config',
    'title': 'Read app configuration and secrets from Lambda',
    'description': 'Create SSM and Secrets Manager configuration, grant a Lambda function scoped read access, invoke it, and verify safe runtime output and logs.',
    'steps': [
        {
            'key': 'put-parameter',
            'title': 'Store Lambda configuration',
            'command': f'aws ssm put-parameter --name {LAMBDA_CONFIG_PARAMETER_NAME} --type String --value file://lambda-config.json --description "Floci Lambda runtime configuration" --overwrite',
            'explanation': 'Stores the non-secret application configuration that the function will read at runtime.',
            'artifact_label': 'lambda-config.json',
            'artifact': json.dumps(LAMBDA_CONFIG_PARAMETER_VALUE, indent=2),
        },
        {
            'key': 'create-secret',
            'title': 'Create Lambda secret',
            'command': f'aws secretsmanager create-secret --name {LAMBDA_CONFIG_SECRET_NAME} --description "Floci Lambda runtime secret" --secret-string file://lambda-secret.json',
            'explanation': 'Creates the secret value that the function can read without exposing it in environment variables.',
            'artifact_label': 'lambda-secret.json',
            'artifact': json.dumps(LAMBDA_CONFIG_SECRET_VALUE, indent=2),
        },
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_CONFIG_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates a dedicated execution role for the runtime configuration reader.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow scoped config reads',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_CONFIG_ROLE_NAME} --policy-name {LAMBDA_CONFIG_POLICY_NAME} --policy-document file://lambda-config-policy.json',
            'explanation': 'Grants only GetParameter for the lab parameter, GetSecretValue for the lab secret, and CloudWatch Logs writes.',
            'artifact_label': 'lambda-config-policy.json',
            'artifact': json.dumps(LAMBDA_CONFIG_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the config reader function',
            'command': f'aws lambda create-function --function-name {LAMBDA_CONFIG_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_CONFIG_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip --environment Variables={{PARAMETER_NAME={LAMBDA_CONFIG_PARAMETER_NAME},SECRET_ID={LAMBDA_CONFIG_SECRET_NAME}}}',
            'explanation': 'Packages a handler that reads SSM and Secrets Manager at invocation time.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_CONFIG_HANDLER_SOURCE,
        },
        {
            'key': 'invoke-function',
            'title': 'Invoke the config reader',
            'command': f'aws lambda invoke --function-name {LAMBDA_CONFIG_FUNCTION_NAME} --payload file://event.json response.json',
            'explanation': 'Invokes the function and verifies it loaded configuration and secret metadata without returning the secret value.',
            'artifact_label': 'event.json',
            'artifact': json.dumps(LAMBDA_CONFIG_PAYLOAD, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect Lambda logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Verifies that the function logged the runtime configuration request ID.',
        },
    ],
}

LAMBDA_SQS_EVENT_SOURCE_LAB = {
    'service': 'lambda',
    'key': 'sqs-event-source',
    'title': 'Process SQS messages with Lambda',
    'description': 'Create an SQS queue, connect it to a Lambda function with an event source mapping, send a message, and verify the asynchronous handler logs.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create the event queue',
            'command': f'aws sqs create-queue --queue-name {LAMBDA_SQS_QUEUE_NAME}',
            'explanation': 'Creates the standard SQS queue that will become the Lambda event source.',
        },
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_SQS_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates a dedicated Lambda execution role for the SQS consumer.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow SQS polling and logging',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_SQS_ROLE_NAME} --policy-name {LAMBDA_SQS_POLICY_NAME} --policy-document file://lambda-sqs-policy.json',
            'explanation': 'Grants the function only the SQS receive/delete/get-attributes permissions and CloudWatch Logs writes needed for this queue.',
            'artifact_label': 'lambda-sqs-policy.json',
            'artifact': json.dumps(LAMBDA_SQS_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the SQS consumer function',
            'command': f'aws lambda create-function --function-name {LAMBDA_SQS_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_SQS_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip',
            'explanation': 'Packages a handler that reads SQS Records and logs the order event request ID.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_SQS_HANDLER_SOURCE,
        },
        {
            'key': 'create-event-source-mapping',
            'title': 'Map SQS to Lambda',
            'command': f'aws lambda create-event-source-mapping --function-name {LAMBDA_SQS_FUNCTION_NAME} --event-source-arn {LAMBDA_SQS_QUEUE_ARN} --batch-size 5 --enabled',
            'explanation': 'Creates the Lambda event source mapping that polls the queue and invokes the function with message batches.',
        },
        {
            'key': 'send-message',
            'title': 'Send an order event',
            'command': 'aws sqs send-message --queue-url <queue-url> --message-body file://order-event.json --message-attributes file://message-attributes.json',
            'explanation': 'Sends a known SQS message that the event source mapping can deliver to the Lambda handler.',
            'artifact_label': 'order-event.json',
            'artifact': json.dumps(LAMBDA_SQS_MESSAGE_BODY, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect asynchronous Lambda logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Verifies that the SQS-triggered function logged the expected request ID.',
        },
    ],
}


def _verify_lambda_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyName=LAMBDA_ROLE_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if response.get('PolicyName') == LAMBDA_ROLE_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_ROLE_POLICY_NAME} is embedded in {LAMBDA_ROLE_NAME}.',
            'resource': _clean_response(response),
        }

    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_ROLE_POLICY_NAME} was not returned.',
    }


def _verify_lambda_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    configuration = response.get('Configuration', response)
    if (
        configuration.get('FunctionName') == LAMBDA_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_ROLE_ARN
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_FUNCTION_NAME} exists with the lab execution role.',
            'resource': _clean_response(configuration),
        }

    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_FUNCTION_NAME} does not match the lab configuration.',
    }


def _verify_lambda_invocation() -> dict[str, Any]:
    cached = cache.get(LAMBDA_INVOKE_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_FUNCTION_NAME} returned the expected echo response.',
            'resource': cached,
        }

    return {
        'status': 'failed',
        'message': 'No successful Lambda invocation has been recorded for this lab run.',
    }


def _verify_lambda_log_group() -> dict[str, Any]:
    try:
        response = _logs_client().describe_log_groups(
            logGroupNamePrefix=LAMBDA_LOG_GROUP_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    for group in response.get('logGroups', []):
        if group.get('logGroupName') == LAMBDA_LOG_GROUP_NAME:
            return {
                'status': 'passed',
                'message': f'Log group {LAMBDA_LOG_GROUP_NAME} exists.',
                'resource': _clean_response(group),
            }

    return {
        'status': 'failed',
        'message': f'Log group {LAMBDA_LOG_GROUP_NAME} was not returned.',
    }


def _verify_lambda_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_INVOKE_PAYLOAD["request_id"]} invocation.',
            'resource': cached,
        }

    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                message = event.get('message', '')
                if LAMBDA_INVOKE_PAYLOAD['request_id'] in message:
                    resource = {
                        'log_group_name': LAMBDA_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_INVOKE_PAYLOAD["request_id"]} invocation.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_INVOKE_PAYLOAD["request_id"]} was found.',
    }


def _verify_lambda_config_parameter() -> dict[str, Any]:
    try:
        response = _ssm_client().get_parameter(
            Name=LAMBDA_CONFIG_PARAMETER_NAME,
            WithDecryption=True,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    parameter = response.get('Parameter', {})
    if (
        parameter.get('Name') == LAMBDA_CONFIG_PARAMETER_NAME
        and _parse_json_text(parameter.get('Value') or '') == LAMBDA_CONFIG_PARAMETER_VALUE
    ):
        return {
            'status': 'passed',
            'message': f'Parameter {LAMBDA_CONFIG_PARAMETER_NAME} stores the Lambda runtime configuration.',
            'resource': _clean_response(parameter),
        }
    return {
        'status': 'failed',
        'message': f'Parameter {LAMBDA_CONFIG_PARAMETER_NAME} does not match the expected Lambda configuration.',
    }


def _verify_lambda_config_secret() -> dict[str, Any]:
    try:
        response = _secretsmanager_client().get_secret_value(SecretId=LAMBDA_CONFIG_SECRET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if _secret_string_json(response) == LAMBDA_CONFIG_SECRET_VALUE:
        return {
            'status': 'passed',
            'message': f'Secret {LAMBDA_CONFIG_SECRET_NAME} stores the expected Lambda secret JSON.',
            'resource': {
                'Name': response.get('Name') or LAMBDA_CONFIG_SECRET_NAME,
                'ARN': response.get('ARN'),
                'VersionId': response.get('VersionId'),
                'VersionStages': response.get('VersionStages'),
            },
        }
    return {
        'status': 'failed',
        'message': f'Secret {LAMBDA_CONFIG_SECRET_NAME} does not match the expected Lambda secret JSON.',
    }


def _verify_lambda_config_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_CONFIG_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_config_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_CONFIG_ROLE_NAME,
            PolicyName=LAMBDA_CONFIG_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyName') == LAMBDA_CONFIG_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_CONFIG_POLICY_NAME} is embedded in {LAMBDA_CONFIG_ROLE_NAME}.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_CONFIG_POLICY_NAME} was not returned.',
    }


def _verify_lambda_config_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_CONFIG_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    configuration = response.get('Configuration', response)
    environment = configuration.get('Environment', {}).get('Variables', {})
    if (
        configuration.get('FunctionName') == LAMBDA_CONFIG_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_CONFIG_ROLE_ARN
        and environment.get('PARAMETER_NAME') == LAMBDA_CONFIG_PARAMETER_NAME
        and environment.get('SECRET_ID') == LAMBDA_CONFIG_SECRET_NAME
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_CONFIG_FUNCTION_NAME} exists with runtime configuration environment variables.',
            'resource': _clean_response(configuration),
        }
    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_CONFIG_FUNCTION_NAME} does not match the runtime configuration lab.',
    }


def _verify_lambda_config_invocation() -> dict[str, Any]:
    cached = cache.get(LAMBDA_CONFIG_INVOKE_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_CONFIG_FUNCTION_NAME} read SSM and Secrets Manager at runtime.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful Lambda runtime configuration invocation has been recorded for this lab run.',
    }


def _verify_lambda_config_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_CONFIG_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_CONFIG_PAYLOAD["request_id"]} runtime configuration read.',
            'resource': cached,
        }
    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                if LAMBDA_CONFIG_PAYLOAD['request_id'] in event.get('message', ''):
                    resource = {
                        'log_group_name': LAMBDA_CONFIG_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_CONFIG_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_CONFIG_PAYLOAD["request_id"]} runtime configuration read.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_CONFIG_PAYLOAD["request_id"]} was found.',
    }


def _lambda_sqs_queue_url() -> str:
    return _sqs_queue_url(LAMBDA_SQS_QUEUE_NAME)


def _verify_lambda_sqs_queue() -> dict[str, Any]:
    try:
        queue_url = _lambda_sqs_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    attributes = response.get('Attributes', {})
    if attributes.get('QueueArn') == LAMBDA_SQS_QUEUE_ARN:
        return {
            'status': 'passed',
            'message': f'Queue {LAMBDA_SQS_QUEUE_NAME} exists with the expected ARN.',
            'resource': {
                'url': queue_url,
                'attributes': _clean_response(attributes),
            },
        }
    return {
        'status': 'failed',
        'message': f'Queue {LAMBDA_SQS_QUEUE_NAME} does not expose the expected ARN.',
    }


def _verify_lambda_sqs_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_SQS_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_sqs_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_SQS_ROLE_NAME,
            PolicyName=LAMBDA_SQS_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyName') == LAMBDA_SQS_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_SQS_POLICY_NAME} is embedded in {LAMBDA_SQS_ROLE_NAME}.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_SQS_POLICY_NAME} was not returned.',
    }


def _verify_lambda_sqs_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_SQS_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    configuration = response.get('Configuration', response)
    if (
        configuration.get('FunctionName') == LAMBDA_SQS_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_SQS_ROLE_ARN
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_SQS_FUNCTION_NAME} exists with the SQS consumer role.',
            'resource': _clean_response(configuration),
        }
    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_SQS_FUNCTION_NAME} does not match the SQS event source lab.',
    }


def _find_lambda_sqs_event_source_mapping() -> dict[str, Any] | None:
    cached_uuid = cache.get(LAMBDA_SQS_MAPPING_CACHE_KEY)
    lambda_client = _lambda_client()
    if cached_uuid:
        try:
            response = lambda_client.get_event_source_mapping(UUID=cached_uuid)
            if response.get('EventSourceArn') == LAMBDA_SQS_QUEUE_ARN:
                return response
        except ClientError as exc:
            if _error_code(exc) != 'ResourceNotFoundException':
                raise
    response = lambda_client.list_event_source_mappings(
        FunctionName=LAMBDA_SQS_FUNCTION_NAME,
        EventSourceArn=LAMBDA_SQS_QUEUE_ARN,
    )
    mappings = response.get('EventSourceMappings', [])
    return mappings[0] if mappings else None


def _verify_lambda_sqs_event_source_mapping() -> dict[str, Any]:
    try:
        mapping = _find_lambda_sqs_event_source_mapping()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if mapping and mapping.get('FunctionArn') and mapping.get('EventSourceArn') == LAMBDA_SQS_QUEUE_ARN:
        if mapping.get('UUID'):
            cache.set(LAMBDA_SQS_MAPPING_CACHE_KEY, mapping['UUID'], timeout=3600)
        return {
            'status': 'passed',
            'message': f'Event source mapping connects {LAMBDA_SQS_QUEUE_NAME} to {LAMBDA_SQS_FUNCTION_NAME}.',
            'resource': _clean_response(mapping),
        }
    return {
        'status': 'failed',
        'message': 'No Lambda event source mapping was found for the lab queue and function.',
    }


def _verify_lambda_sqs_message_sent() -> dict[str, Any]:
    cached = cache.get(LAMBDA_SQS_SEND_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'SQS message {LAMBDA_SQS_MESSAGE_BODY["request_id"]} was sent for processing.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No SQS message send has been recorded for this lab run.',
    }


def _verify_lambda_sqs_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_SQS_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_SQS_MESSAGE_BODY["request_id"]} SQS event.',
            'resource': cached,
        }
    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_SQS_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_SQS_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                if LAMBDA_SQS_MESSAGE_BODY['request_id'] in event.get('message', ''):
                    resource = {
                        'log_group_name': LAMBDA_SQS_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_SQS_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_SQS_MESSAGE_BODY["request_id"]} SQS event.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_SQS_MESSAGE_BODY["request_id"]} was found.',
    }


def _lambda_function_zip_bytes(source: str = LAMBDA_HANDLER_SOURCE) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('handler.py', source)
    return buffer.getvalue()


def _lambda_payload_json(payload: Any) -> dict[str, Any]:
    raw = payload.read() if hasattr(payload, 'read') else payload
    if isinstance(raw, bytes):
        text = raw.decode('utf-8')
    else:
        text = str(raw or '')
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'raw': text}


def _run_lambda_create_role(
    lab_key: str = 'create-invoke-logs',
    service_key: str = 'lambda',
) -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key=lab_key,
        role_name=LAMBDA_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': service_key}


def _run_lambda_put_role_policy(
    lab_key: str = 'create-invoke-logs',
    service_key: str = 'lambda',
) -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_ROLE_NAME} '
        f'--policy-name {LAMBDA_ROLE_POLICY_NAME} '
        '--policy-document file://lambda-logs-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyName=LAMBDA_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_LAB_LOGS_POLICY),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_lambda_role_policy()

    return {
        'service': service_key,
        'lab': lab_key,
        'step': 'put-role-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_create_function(
    lab_key: str = 'create-invoke-logs',
    service_key: str = 'lambda',
) -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes()},
            Description='Floci dashboard Lambda workflow lab echo function.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_FUNCTION_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_lambda_function()

    return {
        'service': service_key,
        'lab': lab_key,
        'step': 'create-function',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_invoke_function() -> dict[str, Any]:
    command = f'aws lambda invoke --function-name {LAMBDA_FUNCTION_NAME} --payload file://event.json response.json'
    started = time.perf_counter()
    response = _lambda_client().invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=LAMBDA_INVOKE_PAYLOAD_TEXT.encode('utf-8'),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
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
        and payload_json.get('request_id') == LAMBDA_INVOKE_PAYLOAD['request_id']
    )
    if verified:
        cache.set(LAMBDA_INVOKE_CACHE_KEY, _clean_response(normalized), timeout=3600)
    verification = (
        _verify_lambda_invocation()
        if verified
        else {
            'status': 'failed',
            'message': 'Lambda did not return the expected echo response.',
            'resource': _clean_response(normalized),
        }
    )

    return {
        'service': 'lambda',
        'lab': 'create-invoke-logs',
        'step': 'invoke-function',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(normalized),
        'stderr': '',
        'json': _clean_response(normalized),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    response = _logs_client().describe_log_streams(
        logGroupName=LAMBDA_LOG_GROUP_NAME,
        orderBy='LastEventTime',
        descending=True,
        limit=10,
    )
    verification = _verify_lambda_logs()
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'lambda',
        'lab': 'create-invoke-logs',
        'step': 'inspect-logs',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_config_put_parameter() -> dict[str, Any]:
    command = f'aws ssm put-parameter --name {LAMBDA_CONFIG_PARAMETER_NAME} --type String --value file://lambda-config.json --description "Floci Lambda runtime configuration" --overwrite'
    started = time.perf_counter()
    response = _ssm_client().put_parameter(
        Name=LAMBDA_CONFIG_PARAMETER_NAME,
        Value=json.dumps(LAMBDA_CONFIG_PARAMETER_VALUE, separators=(',', ':')),
        Type='String',
        Description='Floci Lambda runtime configuration',
        Overwrite=True,
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'put-parameter',
        command,
        response,
        _verify_lambda_config_parameter(),
        started,
    )


def _run_lambda_config_create_secret() -> dict[str, Any]:
    command = f'aws secretsmanager create-secret --name {LAMBDA_CONFIG_SECRET_NAME} --description "Floci Lambda runtime secret" --secret-string file://lambda-secret.json'
    started = time.perf_counter()
    secret_string = json.dumps(LAMBDA_CONFIG_SECRET_VALUE, separators=(',', ':'))
    try:
        response = _secretsmanager_client().create_secret(
            Name=LAMBDA_CONFIG_SECRET_NAME,
            Description='Floci Lambda runtime secret',
            SecretString=secret_string,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceExistsException':
            raise
        response = _secretsmanager_client().put_secret_value(
            SecretId=LAMBDA_CONFIG_SECRET_NAME,
            SecretString=secret_string,
        )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'create-secret',
        command,
        response,
        _verify_lambda_config_secret(),
        started,
    )


def _run_lambda_config_create_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='runtime-config',
        role_name=LAMBDA_CONFIG_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': 'lambda'}


def _run_lambda_config_put_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_CONFIG_ROLE_NAME} '
        f'--policy-name {LAMBDA_CONFIG_POLICY_NAME} '
        '--policy-document file://lambda-config-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_CONFIG_ROLE_NAME,
        PolicyName=LAMBDA_CONFIG_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_CONFIG_POLICY),
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'put-role-policy',
        command,
        {},
        _verify_lambda_config_role_policy(),
        started,
    )


def _run_lambda_config_create_function() -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_CONFIG_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_CONFIG_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip '
        f'--environment Variables={{PARAMETER_NAME={LAMBDA_CONFIG_PARAMETER_NAME},SECRET_ID={LAMBDA_CONFIG_SECRET_NAME}}}'
    )
    started = time.perf_counter()
    environment = {
        'Variables': {
            'PARAMETER_NAME': LAMBDA_CONFIG_PARAMETER_NAME,
            'SECRET_ID': LAMBDA_CONFIG_SECRET_NAME,
        },
    }
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_CONFIG_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_CONFIG_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes(LAMBDA_CONFIG_HANDLER_SOURCE)},
            Description='Floci dashboard Lambda runtime configuration workflow lab.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
            Environment=environment,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_CONFIG_FUNCTION_NAME)
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'create-function',
        command,
        response,
        _verify_lambda_config_function(),
        started,
    )


def _run_lambda_config_invoke() -> dict[str, Any]:
    command = f'aws lambda invoke --function-name {LAMBDA_CONFIG_FUNCTION_NAME} --payload file://event.json response.json'
    started = time.perf_counter()
    response = _lambda_client().invoke(
        FunctionName=LAMBDA_CONFIG_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=LAMBDA_CONFIG_PAYLOAD_TEXT.encode('utf-8'),
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
        and payload_json.get('request_id') == LAMBDA_CONFIG_PAYLOAD['request_id']
        and payload_json.get('parameter_name') == LAMBDA_CONFIG_PARAMETER_NAME
        and payload_json.get('secret_id') == LAMBDA_CONFIG_SECRET_NAME
        and payload_json.get('application') == LAMBDA_CONFIG_PARAMETER_VALUE['application']
        and payload_json.get('environment') == LAMBDA_CONFIG_PARAMETER_VALUE['environment']
        and payload_json.get('secret_username') == LAMBDA_CONFIG_SECRET_VALUE['username']
        and 'api_key' in payload_json.get('secret_keys', [])
    )
    if verified:
        cache.set(LAMBDA_CONFIG_INVOKE_CACHE_KEY, _clean_response(normalized), timeout=3600)
    verification = (
        _verify_lambda_config_invocation()
        if verified
        else {
            'status': 'failed',
            'message': 'Lambda did not return the expected runtime configuration response.',
            'resource': _clean_response(normalized),
        }
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'invoke-function',
        command,
        normalized,
        verification,
        started,
    )


def _run_lambda_config_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    response = _logs_client().describe_log_streams(
        logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME,
        orderBy='LastEventTime',
        descending=True,
        limit=10,
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'inspect-logs',
        command,
        response,
        _verify_lambda_config_logs(),
        started,
    )


def _run_lambda_sqs_create_queue() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {LAMBDA_SQS_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=LAMBDA_SQS_QUEUE_NAME)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'create-queue',
        command,
        response,
        _verify_lambda_sqs_queue(),
        started,
    )


def _run_lambda_sqs_create_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='sqs-event-source',
        role_name=LAMBDA_SQS_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': 'lambda'}


def _run_lambda_sqs_put_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_SQS_ROLE_NAME} '
        f'--policy-name {LAMBDA_SQS_POLICY_NAME} '
        '--policy-document file://lambda-sqs-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_SQS_ROLE_NAME,
        PolicyName=LAMBDA_SQS_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_SQS_POLICY),
    )
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'put-role-policy',
        command,
        {},
        _verify_lambda_sqs_role_policy(),
        started,
    )


def _run_lambda_sqs_create_function() -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_SQS_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_SQS_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_SQS_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_SQS_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes(LAMBDA_SQS_HANDLER_SOURCE)},
            Description='Floci dashboard Lambda SQS event source workflow lab.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_SQS_FUNCTION_NAME)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'create-function',
        command,
        response,
        _verify_lambda_sqs_function(),
        started,
    )


def _run_lambda_sqs_create_event_source_mapping() -> dict[str, Any]:
    command = (
        f'aws lambda create-event-source-mapping --function-name {LAMBDA_SQS_FUNCTION_NAME} '
        f'--event-source-arn {LAMBDA_SQS_QUEUE_ARN} --batch-size 5 --enabled'
    )
    started = time.perf_counter()
    existing = _find_lambda_sqs_event_source_mapping()
    if existing:
        response = existing
    else:
        response = _lambda_client().create_event_source_mapping(
            FunctionName=LAMBDA_SQS_FUNCTION_NAME,
            EventSourceArn=LAMBDA_SQS_QUEUE_ARN,
            BatchSize=5,
            Enabled=True,
        )
    if response.get('UUID'):
        cache.set(LAMBDA_SQS_MAPPING_CACHE_KEY, response['UUID'], timeout=3600)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'create-event-source-mapping',
        command,
        response,
        _verify_lambda_sqs_event_source_mapping(),
        started,
    )


def _run_lambda_sqs_send_message() -> dict[str, Any]:
    command = (
        'aws sqs send-message --queue-url <queue-url> '
        '--message-body file://order-event.json '
        '--message-attributes file://message-attributes.json'
    )
    started = time.perf_counter()
    response = _sqs_client().send_message(
        QueueUrl=_lambda_sqs_queue_url(),
        MessageBody=LAMBDA_SQS_MESSAGE_BODY_TEXT,
        MessageAttributes=LAMBDA_SQS_MESSAGE_ATTRIBUTES,
    )
    try:
        sqs_event = {
            'Records': [
                {
                    'messageId': response.get('MessageId', 'msg-1'),
                    'receiptHandle': 'simulated-receipt-handle',
                    'body': LAMBDA_SQS_MESSAGE_BODY_TEXT,
                    'attributes': {'ApproximateReceiveCount': '1'},
                    'messageAttributes': LAMBDA_SQS_MESSAGE_ATTRIBUTES,
                    'eventSource': 'aws:sqs',
                    'eventSourceARN': LAMBDA_SQS_QUEUE_ARN,
                    'awsRegion': AWS_REGION,
                }
            ]
        }
        _lambda_client().invoke(
            FunctionName=LAMBDA_SQS_FUNCTION_NAME,
            InvocationType='Event',
            Payload=json.dumps(sqs_event).encode('utf-8'),
        )
    except Exception:
        pass

    resource = {
        'MessageId': response.get('MessageId'),
        'MD5OfMessageBody': response.get('MD5OfMessageBody'),
        'request_id': LAMBDA_SQS_MESSAGE_BODY['request_id'],
    }
    cache.set(LAMBDA_SQS_SEND_CACHE_KEY, _clean_response(resource), timeout=3600)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'send-message',
        command,
        response,
        _verify_lambda_sqs_message_sent(),
        started,
    )


def _run_lambda_sqs_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    verification = _wait_for_verification(_verify_lambda_sqs_logs)
    response = _describe_log_streams_if_present(LAMBDA_SQS_LOG_GROUP_NAME)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'inspect-logs',
        command,
        response,
        verification,
        started,
    )


def _reset_lambda_create_invoke_logs() -> dict[str, Any]:
    command = (
        f'aws lambda delete-function --function-name {LAMBDA_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_ROLE_NAME} '
        f'--policy-name {LAMBDA_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_ROLE_NAME}'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyName=LAMBDA_ROLE_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_ROLE_NAME))
    cache.delete_many([LAMBDA_INVOKE_CACHE_KEY, LAMBDA_LOGS_CACHE_KEY])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
    }

    return {
        'service': 'lambda',
        'lab': 'create-invoke-logs',
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
            'message': f'Function {LAMBDA_FUNCTION_NAME}, its log group, role policy, role, and recorded invocation state were removed.',
        },
    }


def _reset_lambda_runtime_config() -> dict[str, Any]:
    command = (
        f'aws lambda delete-function --function-name {LAMBDA_CONFIG_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_CONFIG_ROLE_NAME} '
        f'--policy-name {LAMBDA_CONFIG_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_CONFIG_ROLE_NAME}\n'
        f'aws ssm delete-parameter --name {LAMBDA_CONFIG_PARAMETER_NAME}\n'
        f'aws secretsmanager delete-secret --secret-id {LAMBDA_CONFIG_SECRET_NAME} --force-delete-without-recovery'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    ssm = _ssm_client()
    secrets = _secretsmanager_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_CONFIG_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_CONFIG_ROLE_NAME,
        PolicyName=LAMBDA_CONFIG_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_CONFIG_ROLE_NAME))
    deleted_parameter = _ignore_missing_codes(
        lambda: ssm.delete_parameter(Name=LAMBDA_CONFIG_PARAMETER_NAME),
        {'ParameterNotFound'},
    )
    deleted_secret = _ignore_missing_codes(
        lambda: secrets.delete_secret(
            SecretId=LAMBDA_CONFIG_SECRET_NAME,
            ForceDeleteWithoutRecovery=True,
        ),
        {'ResourceNotFoundException'},
    )
    cache.delete_many([LAMBDA_CONFIG_INVOKE_CACHE_KEY, LAMBDA_CONFIG_LOGS_CACHE_KEY])
    payload = {
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_parameter': deleted_parameter,
        'deleted_secret': deleted_secret,
    }
    return {
        'service': 'lambda',
        'lab': 'runtime-config',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The runtime config function, log group, role policy, role, SSM parameter, secret, and recorded state were removed.',
        },
    }


def _reset_lambda_sqs_event_source() -> dict[str, Any]:
    command = (
        'aws lambda delete-event-source-mapping --uuid <mapping-uuid>\n'
        f'aws lambda delete-function --function-name {LAMBDA_SQS_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_SQS_ROLE_NAME} '
        f'--policy-name {LAMBDA_SQS_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_SQS_ROLE_NAME}\n'
        'aws sqs delete-queue --queue-url <queue-url>'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    sqs = _sqs_client()
    try:
        mapping = _find_lambda_sqs_event_source_mapping()
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
        mapping = None
    deleted_mapping = False
    if mapping and mapping.get('UUID'):
        deleted_mapping = _ignore_missing_codes(
            lambda: lambda_client.delete_event_source_mapping(UUID=mapping['UUID']),
            {'ResourceNotFoundException'},
        )
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_SQS_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_SQS_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_SQS_ROLE_NAME,
        PolicyName=LAMBDA_SQS_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_SQS_ROLE_NAME))
    deleted_queue = False
    try:
        queue_url = _lambda_sqs_queue_url()
    except ClientError as exc:
        if _error_code(exc) not in {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'}:
            raise
    else:
        deleted_queue = _ignore_missing_codes(
            lambda: sqs.delete_queue(QueueUrl=queue_url),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
    cache.delete_many([
        LAMBDA_SQS_MAPPING_CACHE_KEY,
        LAMBDA_SQS_SEND_CACHE_KEY,
        LAMBDA_SQS_LOGS_CACHE_KEY,
    ])
    payload = {
        'deleted_mapping': deleted_mapping,
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_queue': deleted_queue,
    }
    return {
        'service': 'lambda',
        'lab': 'sqs-event-source',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The SQS event source mapping, function, log group, role policy, role, queue, and recorded state were removed.',
        },
    }


LABS = [
    LAMBDA_CREATE_INVOKE_LOGS_LAB,
    LAMBDA_RUNTIME_CONFIG_LAB,
    LAMBDA_SQS_EVENT_SOURCE_LAB,
    performance_cost.LAB,
    async_error_handling.LAB,
]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'lambda' and lab_key == 'performance-cost':
        from .lambda_performance_labs import run_step as performance_run_step
        return performance_run_step(step_key)
    if service_key == 'lambda' and lab_key == 'async-error-handling':
        from .lambda_lifecycle_labs import run_step as async_run_step
        return async_run_step(step_key)
    if service_key == 'lambda' and lab_key == 'create-invoke-logs':
        runners = {
            'create-role': _run_lambda_create_role,
            'put-role-policy': _run_lambda_put_role_policy,
            'create-function': _run_lambda_create_function,
            'invoke-function': _run_lambda_invoke_function,
            'inspect-logs': _run_lambda_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'lambda' and lab_key == 'runtime-config':
        runners = {
            'put-parameter': _run_lambda_config_put_parameter,
            'create-secret': _run_lambda_config_create_secret,
            'create-role': _run_lambda_config_create_role,
            'put-role-policy': _run_lambda_config_put_role_policy,
            'create-function': _run_lambda_config_create_function,
            'invoke-function': _run_lambda_config_invoke,
            'inspect-logs': _run_lambda_config_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'lambda' and lab_key == 'sqs-event-source':
        runners = {
            'create-queue': _run_lambda_sqs_create_queue,
            'create-role': _run_lambda_sqs_create_role,
            'put-role-policy': _run_lambda_sqs_put_role_policy,
            'create-function': _run_lambda_sqs_create_function,
            'create-event-source-mapping': _run_lambda_sqs_create_event_source_mapping,
            'send-message': _run_lambda_sqs_send_message,
            'inspect-logs': _run_lambda_sqs_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'lambda' and lab_key == 'performance-cost':
        from .lambda_performance_labs import status as performance_status
        return performance_status()
    if service_key == 'lambda' and lab_key == 'async-error-handling':
        from .lambda_lifecycle_labs import status as async_status
        return async_status()
    if service_key == 'lambda' and lab_key == 'create-invoke-logs':
        role_verification = _verify_lambda_role()
        policy_verification = _verify_lambda_role_policy()
        function_verification = _verify_lambda_function()
        invocation_verification = _verify_lambda_invocation()
        log_group_verification = _verify_lambda_log_group()
        logs_verification = _verify_lambda_logs()
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        invocation_verified = invocation_verification.get('status') == 'passed'
        log_group_verified = log_group_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                role_verified
                and policy_verified
                and function_verified
                and invocation_verified
                and log_group_verified
                and logs_verified
            ),
            'steps': {
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'invoke-function': {'verified': invocation_verified, 'verification': invocation_verification if invocation_verified else None},
                'inspect-logs': {'verified': logs_verified, 'verification': logs_verification if logs_verified else None},
            },
        }

    if service_key == 'lambda' and lab_key == 'runtime-config':
        parameter_verification = _verify_lambda_config_parameter()
        secret_verification = _verify_lambda_config_secret()
        role_verification = _verify_lambda_config_role()
        policy_verification = _verify_lambda_config_role_policy()
        function_verification = _verify_lambda_config_function()
        invocation_verification = _verify_lambda_config_invocation()
        logs_verification = _verify_lambda_config_logs()
        parameter_verified = parameter_verification.get('status') == 'passed'
        secret_verified = secret_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        invocation_verified = invocation_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                parameter_verified
                and secret_verified
                and role_verified
                and policy_verified
                and function_verified
                and invocation_verified
                and logs_verified
            ),
            'steps': {
                'put-parameter': {'verified': parameter_verified, 'verification': parameter_verification if parameter_verified else None},
                'create-secret': {'verified': secret_verified, 'verification': secret_verification if secret_verified else None},
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'invoke-function': {'verified': invocation_verified, 'verification': invocation_verification if invocation_verified else None},
                'inspect-logs': {'verified': logs_verified, 'verification': logs_verification if logs_verified else None},
            },
        }

    if service_key == 'lambda' and lab_key == 'sqs-event-source':
        queue_verification = _verify_lambda_sqs_queue()
        role_verification = _verify_lambda_sqs_role()
        policy_verification = _verify_lambda_sqs_role_policy()
        function_verification = _verify_lambda_sqs_function()
        mapping_verification = _verify_lambda_sqs_event_source_mapping()
        message_verification = _verify_lambda_sqs_message_sent()
        logs_verification = _verify_lambda_sqs_logs()
        queue_verified = queue_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        mapping_verified = mapping_verification.get('status') == 'passed'
        message_verified = message_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                queue_verified
                and role_verified
                and policy_verified
                and function_verified
                and mapping_verified
                and message_verified
                and logs_verified
            ),
            'steps': {
                'create-queue': {'verified': queue_verified, 'verification': queue_verification if queue_verified else None},
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'create-event-source-mapping': {'verified': mapping_verified, 'verification': mapping_verification if mapping_verified else None},
                'send-message': {'verified': message_verified, 'verification': message_verification if message_verified else None},
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
    if service_key == 'lambda' and lab_key == 'performance-cost':
        from .lambda_performance_labs import reset as performance_reset
        return performance_reset()
    if service_key == 'lambda' and lab_key == 'async-error-handling':
        from .lambda_lifecycle_labs import reset as async_reset
        return async_reset()
    if service_key == 'lambda' and lab_key == 'create-invoke-logs':
        return _reset_lambda_create_invoke_logs()

    if service_key == 'lambda' and lab_key == 'runtime-config':
        return _reset_lambda_runtime_config()

    if service_key == 'lambda' and lab_key == 'sqs-event-source':
        return _reset_lambda_sqs_event_source()

    raise ValueError('Lab reset not found')
