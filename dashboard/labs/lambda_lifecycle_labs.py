"""Lambda event lifecycle lab: test events, synchronous errors, asynchronous retries and maximum event age, OnSuccess
and OnFailure destinations, a dead-letter queue, and an environment variable encrypted with a customer managed key.

Floci 2.3.0 runs the function in a real container, and its asynchronous invocation path is real: event invoke configs
(retries and maximum event age), SQS destinations with AWS-shaped records, and the dead-letter queue all work. The first
retry comes about a minute after the failure, so the failure step waits for it. Once an OnFailure destination is set,
Floci sends failed events only there and no longer to the DLQ, so the lab shows the DLQ first, with retries off. Inside the function, boto3 uses the
execution role's credentials and Floci enforces the role's policy on KMS. Floci checks kms:Decrypt against
arn:aws:kms:<region>:<account>:key/* rather than the key's ARN, so the lab grants Decrypt on key/* and says so. The
encryption context is enforced.
"""

from __future__ import annotations

import base64
import io
import json
import secrets
import time
import uuid
import zipfile
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'lambda'
LAB_KEY = 'async-error-handling'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

FUNCTION = 'lab-order-events'
FUNCTION_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
ROLE_NAME = 'lab-order-events-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
ROLE_POLICY = 'lab-order-events-access'
LOG_GROUP = f'/aws/lambda/{FUNCTION}'
RUNTIME = 'python3.12'
HANDLER = 'orders.handler'
TIMEOUT_SECONDS = 10

SUCCESS_QUEUE = 'lab-order-events-success'
FAILURE_QUEUE = 'lab-order-events-failure'
DLQ = 'lab-order-events-dlq'
QUEUES = (SUCCESS_QUEUE, FAILURE_QUEUE, DLQ)


def queue_arn(name: str) -> str:
    return f'arn:aws:sqs:{REGION}:{ACCOUNT}:{name}'


DLQ_RETRIES = 0
MAX_RETRIES = 1
MAX_EVENT_AGE = 3600
FAILURE_WAIT_SECONDS = 150
KEY_ALIAS = 'alias/lab-order-events'
KEY_RESOURCE = f'arn:aws:kms:{REGION}:{ACCOUNT}:key/*'
TOKEN_VARIABLE = 'PAYMENT_API_TOKEN'
TOKEN_PREFIX = 'pay_'
ENCRYPTION_CONTEXT_KEY = 'LambdaFunctionName'

GOOD_EVENT = {'order_id': 'ord-1001', 'amount': 42.5}
BAD_EVENT = {'order_id': 'ord-1002', 'amount': -5}

LAMBDA_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}


def role_policy(with_kms: bool) -> dict[str, Any]:
    statements: list[dict[str, Any]] = [
        {
            'Sid': 'Logs',
            'Effect': 'Allow',
            'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
            'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{LOG_GROUP}:*',
        },
        {
            'Sid': 'DestinationsAndDlq',
            'Effect': 'Allow',
            'Action': 'sqs:SendMessage',
            'Resource': [queue_arn(name) for name in QUEUES],
        },
    ]
    if with_kms:
        statements.append({'Sid': 'DecryptToken', 'Effect': 'Allow', 'Action': 'kms:Decrypt', 'Resource': KEY_RESOURCE})
    return {'Version': '2012-10-17', 'Statement': statements}


ORDERS_SOURCE = '''"""Accept an order event. Invalid orders raise, so Lambda reports a function error."""
import base64
import json
import os

import boto3

_token = None


def payment_token():
    """Decrypt the payment API token once per execution environment, not on every invocation."""
    global _token
    if _token is None:
        blob = base64.b64decode(os.environ["PAYMENT_API_TOKEN"])
        context = {"LambdaFunctionName": os.environ["AWS_LAMBDA_FUNCTION_NAME"]}
        _token = boto3.client("kms").decrypt(CiphertextBlob=blob, EncryptionContext=context)["Plaintext"].decode()
    return _token


def handler(event, context):
    print(json.dumps({"level": "INFO", "request_id": context.aws_request_id, "order_id": event.get("order_id")}))
    if event.get("action") == "token-check":
        token = payment_token()
        return {"token_loaded": True, "token_prefix": token[:4], "token_length": len(token)}
    if not event.get("order_id"):
        raise ValueError("order_id is required")
    if float(event.get("amount", 0)) <= 0:
        raise ValueError(f"amount must be positive for {event['order_id']}")
    return {"order_id": event["order_id"], "status": "ACCEPTED", "amount": event["amount"]}
'''

QUEUES_COMMAND = '\n'.join(f'aws sqs create-queue --queue-name {name}' for name in QUEUES)
CREATE_COMMAND = (
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} '
    '--policy-document file://access.json   # logs + sqs:SendMessage on the three queues\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime {RUNTIME} --handler {HANDLER} '
    f'--role {ROLE_ARN} --zip-file fileb://orders.zip --timeout {TIMEOUT_SECONDS}'
)
TEST_EVENT_COMMAND = (
    f"echo '{json.dumps(GOOD_EVENT)}' > good-order.json\n"
    f"echo '{json.dumps(BAD_EVENT)}' > bad-order.json\n"
    f'aws lambda invoke --function-name {FUNCTION} --payload file://good-order.json '
    '--cli-binary-format raw-in-base64-out --log-type Tail out.json\n'
    f'aws lambda invoke --function-name {FUNCTION} --payload file://bad-order.json '
    '--cli-binary-format raw-in-base64-out out.json   # 200 with "FunctionError": "Unhandled"'
)
DLQ_COMMAND = (
    f'aws lambda put-function-event-invoke-config --function-name {FUNCTION} '
    f'--maximum-retry-attempts {DLQ_RETRIES} --maximum-event-age-in-seconds {MAX_EVENT_AGE}\n'
    f'aws lambda update-function-configuration --function-name {FUNCTION} '
    f'--dead-letter-config TargetArn={queue_arn(DLQ)}\n'
    f'aws lambda invoke --function-name {FUNCTION} --invocation-type Event --payload file://bad-order.json '
    '--cli-binary-format raw-in-base64-out out.json   # StatusCode 202: the caller never sees the error\n'
    f'aws sqs receive-message --queue-url <{DLQ} url> --message-attribute-names All --wait-time-seconds 10'
)
DESTINATIONS_COMMAND = (
    f'aws lambda update-function-event-invoke-config --function-name {FUNCTION} '
    f'--maximum-retry-attempts {MAX_RETRIES} --destination-config '
    f"'{{\"OnSuccess\":{{\"Destination\":\"{queue_arn(SUCCESS_QUEUE)}\"}},"
    f"\"OnFailure\":{{\"Destination\":\"{queue_arn(FAILURE_QUEUE)}\"}}}}'"
)
ASYNC_SUCCESS_COMMAND = (
    f'aws lambda invoke --function-name {FUNCTION} --invocation-type Event --payload file://good-order.json '
    '--cli-binary-format raw-in-base64-out out.json   # StatusCode 202, empty body\n'
    f'aws sqs receive-message --queue-url <{SUCCESS_QUEUE} url> --wait-time-seconds 10'
)
ASYNC_FAILURE_COMMAND = (
    f'aws lambda invoke --function-name {FUNCTION} --invocation-type Event --payload file://bad-order.json '
    '--cli-binary-format raw-in-base64-out out.json\n'
    f'aws sqs receive-message --queue-url <{FAILURE_QUEUE} url> --wait-time-seconds 20   # after the retry'
)
ENCRYPTED_ENV_COMMAND = (
    f'aws kms create-key --description "Order events secrets"\n'
    f'aws kms create-alias --alias-name {KEY_ALIAS} --target-key-id <key-id>\n'
    f'aws kms encrypt --key-id {KEY_ALIAS} --plaintext fileb://token.txt '
    f'--encryption-context {ENCRYPTION_CONTEXT_KEY}={FUNCTION} --query CiphertextBlob --output text > token.b64\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} '
    '--policy-document file://access.json   # adds kms:Decrypt\n'
    f'aws lambda update-function-configuration --function-name {FUNCTION} --kms-key-arn <key-arn> '
    f'--environment "Variables={{{TOKEN_VARIABLE}=$(cat token.b64)}}"\n'
    f'aws lambda invoke --function-name {FUNCTION} --payload \'{{"action":"token-check"}}\' '
    '--cli-binary-format raw-in-base64-out out.json'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Handle Lambda events and failures with test events, async retries, destinations, a DLQ, and KMS',
    'description': (
        'Test an order function with good and bad events, then invoke it asynchronously: limit retries and event '
        'age, route results to OnSuccess and OnFailure SQS destinations, catch failed events in a dead-letter queue, '
        'and keep a payment API token in an environment variable encrypted with a customer managed KMS key.'
    ),
    'steps': [
        {
            'key': 'create-queues',
            'title': 'Create the destination and dead-letter queues',
            'command': QUEUES_COMMAND,
            'explanation': (
                'Asynchronous invocations can report their outcome to a destination: an SQS queue, SNS topic, '
                'Lambda function, EventBridge bus, or S3 bucket (failures only). This lab uses one queue for '
                'successes, one for failures, and a third as the function\'s dead-letter queue. How Floci emulates '
                'this: the queues are real SQS queues.'
            ),
        },
        {
            'key': 'create-function',
            'title': 'Create the order function and its role',
            'command': CREATE_COMMAND,
            'explanation': (
                'The function accepts an order and raises an error for a missing order ID or an amount that is not '
                'positive. Destinations and DLQs are written by Lambda with the function\'s execution role, so the '
                'role needs sqs:SendMessage on those queues as well as permission to write its logs. How Floci '
                'emulates this: the function runs in a real python3.12 container.'
            ),
            'artifact_label': 'orders.py',
            'artifact': ORDERS_SOURCE,
        },
        {
            'key': 'test-event',
            'title': 'Invoke it synchronously with good and bad test events',
            'command': TEST_EVENT_COMMAND,
            'explanation': (
                'A test event is a saved JSON payload you invoke the function with, from the console\'s Test tab or '
                'from a file with the CLI. A synchronous (RequestResponse) call returns the result to the caller. '
                'When the handler raises, the call still returns HTTP 200, but the response carries FunctionError '
                'and a payload with errorMessage and errorType; the caller decides whether to retry, and Lambda '
                'never retries synchronous calls itself. --log-type Tail returns the last 4 KB of the log. How Floci '
                'emulates this: both invocations really run; the step checks the accepted order and the function '
                'error. Floci does not return the log tail, so read the log group instead.'
            ),
        },
        {
            'key': 'dead-letter-queue',
            'title': 'Send a failed async event to a dead-letter queue',
            'command': DLQ_COMMAND,
            'explanation': (
                'With the Event invocation type, Lambda queues the event, answers 202 at once, and runs the function '
                'later, so the caller never sees an error. When it fails, Lambda retries up to twice by default '
                '(about one minute, then two minutes later). MaximumRetryAttempts sets 0 to 2 retries, and '
                'MaximumEventAgeInSeconds (60 seconds to 6 hours) drops events that wait too long, for example while '
                'the function is throttled. A dead-letter queue (SQS or SNS) on the function keeps the original '
                'event once retries run out, with RequestID, ErrorCode, and ErrorMessage attributes. Retries are off '
                'here so the event fails straight to the DLQ. How Floci emulates this: the event invoke config and '
                'the DLQ are real; the step waits for its own event in the queue.'
            ),
        },
        {
            'key': 'destinations',
            'title': f'Allow {MAX_RETRIES} retry and route results to OnSuccess and OnFailure destinations',
            'command': DESTINATIONS_COMMAND,
            'explanation': (
                f'The update turns on {MAX_RETRIES} retry and adds destinations. Destinations are newer than DLQs '
                'and carry more: the request payload, the response or error, the '
                'condition (Success, RetriesExhausted, EventAgeExceeded), and the number of attempts. OnSuccess makes '
                'a chain of functions observable without code; OnFailure is the recommended replacement for a DLQ, '
                'although both can be set. Destinations apply only to asynchronous invocations (and to stream event '
                'source mappings for failures). How Floci emulates this: the destination config is real.'
            ),
        },
        {
            'key': 'async-success',
            'title': 'Invoke asynchronously and read the success record',
            'command': ASYNC_SUCCESS_COMMAND,
            'explanation': (
                'The caller gets StatusCode 202 and an empty body, so it cannot see the result. Lambda sends a record '
                'to the OnSuccess queue with requestContext.condition Success, the request payload, and the response '
                'payload. How Floci emulates this: Floci delivers an AWS-shaped record to the queue; the step waits '
                'for the record of its own invocation.'
            ),
        },
        {
            'key': 'async-failure',
            'title': 'Watch a failing event retry and land in the OnFailure destination',
            'command': ASYNC_FAILURE_COMMAND,
            'explanation': (
                'The bad order is accepted with 202, fails, is retried once, and fails again. Lambda then sends a '
                'record with condition RetriesExhausted, approximateInvokeCount 2, and the error to the OnFailure '
                'queue. Make the handler idempotent: a retry runs the same event again. How Floci emulates this: the '
                'retry and the failure record are real. The first retry comes about a minute after the failure, as '
                'on AWS, so this step waits up to two and a half minutes. Once an OnFailure destination is set, '
                'Floci no longer copies failed events to the DLQ, so this step reads only the failure queue.'
            ),
        },
        {
            'key': 'encrypted-env',
            'title': 'Encrypt the payment API token with a customer managed key',
            'command': ENCRYPTED_ENV_COMMAND,
            'explanation': (
                'Lambda always encrypts environment variables at rest, with an AWS managed key by default. A customer '
                'managed key (--kms-key-arn) lets you control and audit who can decrypt them. For a secret, go '
                'further: encrypt the value yourself (the console\'s encryption helpers do this), store only the '
                'ciphertext, and decrypt it in the function once per execution environment, outside the handler '
                'path. The encryption context binds the ciphertext to this function, and the role needs kms:Decrypt. '
                'Secrets Manager or Parameter Store is often the better home for secrets that rotate. How Floci '
                'emulates this: KMS encryption and the encryption context are real, and Floci enforces the role\'s '
                'policy on the function\'s Decrypt call. Floci checks Decrypt against key/* rather than the key\'s '
                'ARN, so the policy grants key/* here; on AWS, name the key ARN.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    factory = FlociClientFactory()
    if name in ('lambda', 'sqs'):
        factory.config = factory.config.merge(Config(read_timeout=60, retries={'max_attempts': 0}))
    return factory.client(name)


def _code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


def _marker(step: str, value: Any) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'step': step,
        'command': command,
        'exit_code': 0,
        'stdout': json.dumps(clean, indent=2, default=str),
        'stderr': '',
        'json': clean,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verified,
        'verification': {'status': 'passed' if verified else 'failed', 'message': message},
    }


def _missing(step: str, command: str, message: str, started: float) -> dict[str, Any]:
    return _result(step, command, {'error': message}, False, message, started)


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('orders.py', date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, ORDERS_SOURCE)
    return buffer.getvalue()


# ── Lookups ──────────────────────────────────────────────────────────────────

def _queue_url(name: str) -> str | None:
    try:
        return _client('sqs').get_queue_url(QueueName=name)['QueueUrl']
    except ClientError as exc:
        if 'NonExistentQueue' in _code(exc) or _code(exc) == 'QueueDoesNotExist':
            return None
        raise


def _function() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_configuration(FunctionName=FUNCTION)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _wait_updated() -> None:
    _wait(lambda: (_function() or {}).get('State') in (None, 'Active') and
          (_function() or {}).get('LastUpdateStatus') in (None, 'Successful', 'Failed'), timeout=30)


def _invoke_config() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_event_invoke_config(FunctionName=FUNCTION)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _destinations(config: dict[str, Any] | None) -> tuple[str | None, str | None]:
    destination = (config or {}).get('DestinationConfig') or {}
    return ((destination.get('OnSuccess') or {}).get('Destination'),
            (destination.get('OnFailure') or {}).get('Destination'))


def _role_exists() -> bool:
    try:
        _client('iam').get_role(RoleName=ROLE_NAME)
        return True
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return False
        raise


def _role_policy() -> dict[str, Any] | None:
    try:
        document = _client('iam').get_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY)['PolicyDocument']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise
    if isinstance(document, str):
        from urllib.parse import unquote
        document = json.loads(unquote(document))
    return document


def _key_arn() -> str | None:
    try:
        return _client('kms').describe_key(KeyId=KEY_ALIAS)['KeyMetadata']['Arn']
    except ClientError as exc:
        if _code(exc) in {'NotFoundException', 'NotFound'}:
            return None
        raise


def _invoke_sync(payload: dict[str, Any], tail: bool = False) -> dict[str, Any]:
    kwargs: dict[str, Any] = {'FunctionName': FUNCTION, 'Payload': json.dumps(payload).encode()}
    if tail:
        kwargs['LogType'] = 'Tail'
    response = _client('lambda').invoke(**kwargs)
    body = json.loads(response['Payload'].read() or b'null')
    log_tail = ''
    if response.get('LogResult'):
        try:
            log_tail = base64.b64decode(response['LogResult']).decode(errors='replace')
        except (ValueError, TypeError):
            log_tail = ''
    return {'StatusCode': response.get('StatusCode'), 'FunctionError': response.get('FunctionError'),
            'Payload': body, 'LogTail': log_tail}


def _invoke_async(payload: dict[str, Any]) -> int:
    response = _client('lambda').invoke(FunctionName=FUNCTION, InvocationType='Event',
                                        Payload=json.dumps(payload).encode())
    return int(response.get('StatusCode') or 0)


def _drain(queue_url: str, matches, timeout: float, attributes: bool = False) -> dict[str, Any] | None:
    """Receive until a message matches, deleting every message seen (older runs leave records behind)."""
    sqs = _client('sqs')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        kwargs: dict[str, Any] = {'QueueUrl': queue_url, 'MaxNumberOfMessages': 10,
                                  'WaitTimeSeconds': max(1, min(10, int(deadline - time.monotonic())))}
        if attributes:
            kwargs['MessageAttributeNames'] = ['All']
        for message in sqs.receive_message(**kwargs).get('Messages', []):
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=message['ReceiptHandle'])
            try:
                body = json.loads(message.get('Body') or 'null')
            except ValueError:
                body = message.get('Body')
            if matches(body):
                return {'Body': body, 'MessageAttributes': {
                    name: value.get('StringValue') for name, value in (message.get('MessageAttributes') or {}).items()
                }}
    return None


def _record_for(order_id: str, condition: str):
    def matches(body: Any) -> bool:
        return (isinstance(body, dict) and (body.get('requestPayload') or {}).get('order_id') == order_id and
                (body.get('requestContext') or {}).get('condition') == condition)
    return matches


def _event_for(order_id: str):
    return lambda body: isinstance(body, dict) and body.get('order_id') == order_id


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_queues() -> tuple[bool, str]:
    missing = [name for name in QUEUES if not _queue_url(name)]
    if missing:
        return False, 'Missing queues: ' + ', '.join(missing) + '.'
    return True, 'The success, failure, and dead-letter queues exist.'


def _check_function() -> tuple[bool, str]:
    function = _function()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    if function.get('Role') != ROLE_ARN or function.get('Runtime') != RUNTIME or function.get('Handler') != HANDLER:
        return False, f'{FUNCTION} must run {HANDLER} on {RUNTIME} as {ROLE_NAME}.'
    policy = _role_policy() or {}
    resources = [resource for statement in policy.get('Statement', [])
                 if 'sqs:SendMessage' in (statement.get('Action') if isinstance(statement.get('Action'), list)
                                          else [statement.get('Action')])
                 for resource in (statement.get('Resource') if isinstance(statement.get('Resource'), list)
                                  else [statement.get('Resource')])]
    if not all(queue_arn(name) in resources for name in QUEUES):
        return False, f'{ROLE_NAME} must allow sqs:SendMessage on the three lab queues.'
    return True, f'{FUNCTION} runs {HANDLER} on {RUNTIME} as {ROLE_NAME}, which can send to the three queues.'


def _check_test_event() -> tuple[bool, str]:
    marker = _marked('test-event') or {}
    good, bad = marker.get('good') or {}, marker.get('bad') or {}
    if (good.get('Payload') or {}).get('status') != 'ACCEPTED' or good.get('FunctionError'):
        return False, 'Run the good test event: it should return status ACCEPTED.'
    if bad.get('FunctionError') != 'Unhandled' or (bad.get('Payload') or {}).get('errorType') != 'ValueError':
        return False, 'Run the bad test event: it should return FunctionError Unhandled with a ValueError.'
    if not _function():
        return False, f'Function {FUNCTION} was not found.'
    return True, 'The good order was accepted and the bad order returned FunctionError Unhandled (ValueError).'


def _check_dlq() -> tuple[bool, str]:
    config = _invoke_config()
    if not config or config.get('MaximumEventAgeInSeconds') != MAX_EVENT_AGE:
        return False, f'{FUNCTION} needs an event invoke config with a maximum event age of {MAX_EVENT_AGE} seconds.'
    target = ((_function() or {}).get('DeadLetterConfig') or {}).get('TargetArn')
    if target != queue_arn(DLQ):
        return False, f'{FUNCTION} must use {DLQ} as its dead-letter queue.'
    marker = _marked('dead-letter-queue') or {}
    message = marker.get('message') or {}
    if marker.get('status_code') != 202 or (message.get('Body') or {}).get('order_id') != marker.get('order_id'):
        return False, f'Invoke the bad order asynchronously and wait for it in {DLQ}.'
    return True, f'The failed event reached {DLQ}; events expire after {MAX_EVENT_AGE // 60} minutes.'


def _check_destinations() -> tuple[bool, str]:
    config = _invoke_config()
    on_success, on_failure = _destinations(config)
    if on_success != queue_arn(SUCCESS_QUEUE) or on_failure != queue_arn(FAILURE_QUEUE):
        return False, f'OnSuccess must be {SUCCESS_QUEUE} and OnFailure {FAILURE_QUEUE}.'
    if (config or {}).get('MaximumRetryAttempts') != MAX_RETRIES:
        return False, f'The event invoke config must allow {MAX_RETRIES} retry.'
    return True, f'Async events get {MAX_RETRIES} retry; OnSuccess goes to {SUCCESS_QUEUE} and OnFailure to {FAILURE_QUEUE}.'


def _check_async_success() -> tuple[bool, str]:
    marker = _marked('async-success') or {}
    record = marker.get('record') or {}
    if marker.get('status_code') != 202 or (record.get('requestContext') or {}).get('condition') != 'Success':
        return False, f'Invoke asynchronously and wait for the Success record in {SUCCESS_QUEUE}.'
    if (record.get('responsePayload') or {}).get('status') != 'ACCEPTED':
        return False, 'The success record should carry the function\'s response.'
    ok, message = _check_destinations()
    return (True, f'The 202 invocation produced a Success record in {SUCCESS_QUEUE}.') if ok else (False, message)


def _check_async_failure() -> tuple[bool, str]:
    marker = _marked('async-failure') or {}
    record = marker.get('record') or {}
    context = record.get('requestContext') or {}
    if marker.get('status_code') != 202 or context.get('condition') != 'RetriesExhausted':
        return False, f'Invoke the bad order asynchronously and wait for the RetriesExhausted record in {FAILURE_QUEUE}.'
    if int(context.get('approximateInvokeCount') or 0) != MAX_RETRIES + 1:
        return False, f'The failure record should show {MAX_RETRIES + 1} attempts.'
    ok, message = _check_destinations()
    if not ok:
        return False, message
    return True, f'The bad order ran {MAX_RETRIES + 1} times, then landed in {FAILURE_QUEUE} (RetriesExhausted).'


def _check_encrypted_env() -> tuple[bool, str]:
    key_arn = _key_arn()
    if not key_arn:
        return False, f'Key {KEY_ALIAS} was not found.'
    function = _function() or {}
    if function.get('KMSKeyArn') != key_arn:
        return False, f'{FUNCTION} must use {KEY_ALIAS} for its environment variables.'
    value = ((function.get('Environment') or {}).get('Variables') or {}).get(TOKEN_VARIABLE) or ''
    if not value or value.startswith(TOKEN_PREFIX):
        return False, f'{TOKEN_VARIABLE} must hold the token\'s ciphertext, not the token.'
    marker = _marked('encrypted-env') or {}
    if not (marker.get('result') or {}).get('token_loaded'):
        return False, 'Invoke the token check: the function should decrypt the token.'
    return True, f'{TOKEN_VARIABLE} holds ciphertext under {KEY_ALIAS}, and the function decrypts it with its role.'


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_queues() -> dict[str, Any]:
    started = time.perf_counter()
    sqs = _client('sqs')
    urls = {name: sqs.create_queue(QueueName=name)['QueueUrl'] for name in QUEUES}
    verified, message = _check_queues()
    return _result('create-queues', QUEUES_COMMAND, {'QueueUrls': urls}, verified, message, started)


def step_create_function() -> dict[str, Any]:
    started = time.perf_counter()
    if not all(_queue_url(name) for name in QUEUES):
        return _missing('create-function', CREATE_COMMAND, 'Create the queues first.', started)
    iam, lam = _client('iam'), _client('lambda')
    if not _role_exists():
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
                        Description='Order events function (Lambda event lifecycle lab)')
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY,
                        PolicyDocument=json.dumps(role_policy(with_kms=bool(_key_arn()))))
    settings = {'Role': ROLE_ARN, 'Runtime': RUNTIME, 'Handler': HANDLER, 'Timeout': TIMEOUT_SECONDS}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_zip())
        _wait_updated()
        lam.update_function_configuration(FunctionName=FUNCTION, **settings)
    else:
        lam.create_function(FunctionName=FUNCTION, Code={'ZipFile': _zip()},
                            Description='Order events (Lambda event lifecycle lab)', **settings)
    _wait_updated()
    function = _function() or {}
    response = {'Function': {key: function.get(key) for key in (
        'FunctionName', 'FunctionArn', 'Runtime', 'Handler', 'Role', 'Timeout', 'State')},
        'RolePolicy': role_policy(with_kms=bool(_key_arn()))}
    verified, message = _check_function()
    return _result('create-function', CREATE_COMMAND, response, verified, message, started)


def step_test_event() -> dict[str, Any]:
    started = time.perf_counter()
    if not _function():
        return _missing('test-event', TEST_EVENT_COMMAND, 'Create the function first.', started)
    good = _invoke_sync(GOOD_EVENT, tail=True)
    bad = _invoke_sync(BAD_EVENT)
    _marker('test-event', {'good': good, 'bad': bad})
    verified, message = _check_test_event()
    if verified:
        message += (f' Both calls returned HTTP {bad.get("StatusCode")}: a function error is in the response, not '
                    'the status code.')
    return _result('test-event', TEST_EVENT_COMMAND, {'GoodOrder': good, 'BadOrder': bad}, verified, message, started)


def step_dead_letter_queue() -> dict[str, Any]:
    started = time.perf_counter()
    dlq_url = _queue_url(DLQ)
    if not _function() or not dlq_url:
        return _missing('dead-letter-queue', DLQ_COMMAND, 'Create the queues and the function first.', started)
    lam = _client('lambda')
    if _invoke_config():
        lam.delete_function_event_invoke_config(FunctionName=FUNCTION)
    lam.put_function_event_invoke_config(FunctionName=FUNCTION, MaximumRetryAttempts=DLQ_RETRIES,
                                         MaximumEventAgeInSeconds=MAX_EVENT_AGE)
    lam.update_function_configuration(FunctionName=FUNCTION, DeadLetterConfig={'TargetArn': queue_arn(DLQ)})
    _wait_updated()
    order_id = f'{BAD_EVENT["order_id"]}-{uuid.uuid4().hex[:6]}'
    status_code = _invoke_async({**BAD_EVENT, 'order_id': order_id})
    found = _drain(dlq_url, _event_for(order_id), timeout=45, attributes=True)
    _marker('dead-letter-queue', {'order_id': order_id, 'status_code': status_code, 'message': found or {}})
    verified, message = _check_dlq()
    if not found:
        message = f'The event for {order_id} did not reach {DLQ} within 45 seconds.'
    elif verified and found.get('MessageAttributes', {}).get('ErrorMessage'):
        message += f' Its ErrorMessage attribute reads "{found["MessageAttributes"]["ErrorMessage"]}".'
    response = {'EventInvokeConfig': _invoke_config(), 'DeadLetterConfig': (_function() or {}).get('DeadLetterConfig'),
                'InvokeStatusCode': status_code, 'DeadLetterMessage': found}
    return _result('dead-letter-queue', DLQ_COMMAND, response, verified, message, started)


def step_destinations() -> dict[str, Any]:
    started = time.perf_counter()
    if not _invoke_config():
        return _missing('destinations', DESTINATIONS_COMMAND, 'Set up the dead-letter queue first.', started)
    response = _client('lambda').update_function_event_invoke_config(
        FunctionName=FUNCTION,
        MaximumRetryAttempts=MAX_RETRIES,
        MaximumEventAgeInSeconds=MAX_EVENT_AGE,
        DestinationConfig={'OnSuccess': {'Destination': queue_arn(SUCCESS_QUEUE)},
                           'OnFailure': {'Destination': queue_arn(FAILURE_QUEUE)}},
    )
    verified, message = _check_destinations()
    return _result('destinations', DESTINATIONS_COMMAND, response, verified, message, started)


def step_async_success() -> dict[str, Any]:
    started = time.perf_counter()
    on_success, _ = _destinations(_invoke_config())
    url = _queue_url(SUCCESS_QUEUE)
    if not on_success or not url:
        return _missing('async-success', ASYNC_SUCCESS_COMMAND, 'Configure the destinations first.', started)
    order_id = f'{GOOD_EVENT["order_id"]}-{uuid.uuid4().hex[:6]}'
    status_code = _invoke_async({**GOOD_EVENT, 'order_id': order_id})
    found = _drain(url, _record_for(order_id, 'Success'), timeout=30)
    record = (found or {}).get('Body') or {}
    _marker('async-success', {'order_id': order_id, 'status_code': status_code, 'record': record})
    verified, message = _check_async_success()
    if not found:
        message = f'No Success record for {order_id} reached {SUCCESS_QUEUE} within 30 seconds.'
    response = {'InvokeStatusCode': status_code, 'SuccessRecord': record}
    return _result('async-success', ASYNC_SUCCESS_COMMAND, response, verified, message, started)


def step_async_failure() -> dict[str, Any]:
    started = time.perf_counter()
    _, on_failure = _destinations(_invoke_config())
    failure_url = _queue_url(FAILURE_QUEUE)
    if not on_failure or not failure_url:
        return _missing('async-failure', ASYNC_FAILURE_COMMAND, 'Configure the destinations first.', started)
    order_id = f'{BAD_EVENT["order_id"]}-{uuid.uuid4().hex[:6]}'
    status_code = _invoke_async({**BAD_EVENT, 'order_id': order_id})
    found = _drain(failure_url, _record_for(order_id, 'RetriesExhausted'), timeout=FAILURE_WAIT_SECONDS)
    record = (found or {}).get('Body') or {}
    _marker('async-failure', {'order_id': order_id, 'status_code': status_code, 'record': record})
    verified, message = _check_async_failure()
    if not found:
        message = (f'No RetriesExhausted record for {order_id} reached {FAILURE_QUEUE} within '
                   f'{FAILURE_WAIT_SECONDS} seconds.')
    elif verified:
        error = (record.get('responsePayload') or {}).get('errorMessage')
        message += f' The record carries the error "{error}".' if error else ''
    response = {'InvokeStatusCode': status_code, 'FailureRecord': record}
    return _result('async-failure', ASYNC_FAILURE_COMMAND, response, verified, message, started)


def step_encrypted_env() -> dict[str, Any]:
    started = time.perf_counter()
    if not _function():
        return _missing('encrypted-env', ENCRYPTED_ENV_COMMAND, 'Create the function first.', started)
    kms, iam, lam = _client('kms'), _client('iam'), _client('lambda')
    key_arn = _key_arn()
    if not key_arn:
        key_arn = kms.create_key(Description='Order events secrets (Lambda event lifecycle lab)')['KeyMetadata']['Arn']
        kms.create_alias(AliasName=KEY_ALIAS, TargetKeyId=key_arn)
    token = TOKEN_PREFIX + secrets.token_hex(12)
    blob = kms.encrypt(KeyId=KEY_ALIAS, Plaintext=token.encode(),
                       EncryptionContext={ENCRYPTION_CONTEXT_KEY: FUNCTION})['CiphertextBlob']
    ciphertext = base64.b64encode(blob).decode()
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY, PolicyDocument=json.dumps(role_policy(True)))
    lam.update_function_configuration(FunctionName=FUNCTION, KMSKeyArn=key_arn,
                                      Environment={'Variables': {TOKEN_VARIABLE: ciphertext}})
    _wait_updated()
    result = _invoke_sync({'action': 'token-check'})
    payload = result.get('Payload') or {}
    loaded = bool(payload.get('token_loaded')) and payload.get('token_prefix') == TOKEN_PREFIX
    _marker('encrypted-env', {'result': payload if loaded else {}})
    function = _function() or {}
    response = {'KMSKeyArn': function.get('KMSKeyArn'),
                'Environment': {TOKEN_VARIABLE: ciphertext[:24] + '...'},
                'TokenCheck': payload, 'FunctionError': result.get('FunctionError')}
    verified, message = _check_encrypted_env()
    if result.get('FunctionError'):
        message = f'The function could not decrypt the token: {payload.get("errorMessage") or payload}'
        verified = False
    return _result('encrypted-env', ENCRYPTED_ENV_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-queues': step_create_queues,
    'create-function': step_create_function,
    'test-event': step_test_event,
    'dead-letter-queue': step_dead_letter_queue,
    'destinations': step_destinations,
    'async-success': step_async_success,
    'async-failure': step_async_failure,
    'encrypted-env': step_encrypted_env,
}

CHECKS = {
    'create-queues': _check_queues,
    'create-function': _check_function,
    'test-event': _check_test_event,
    'dead-letter-queue': _check_dlq,
    'destinations': _check_destinations,
    'async-success': _check_async_success,
    'async-failure': _check_async_failure,
    'encrypted-env': _check_encrypted_env,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check()[0])
    except (ClientError, BotoCoreError):
        return False


def status() -> dict[str, Any]:
    steps = {}
    for step, check in CHECKS.items():
        verified = _safe(check)
        steps[step] = {
            'verified': verified,
            'verification': {'status': 'passed', 'message': 'Verified against Floci.'} if verified else None,
        }
    passed = sum(1 for item in steps.values() if item['verified'])
    complete = passed == len(steps)
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'complete': complete,
        'status': 'passed' if complete else ('in_progress' if passed else 'not_started'),
        'steps': steps,
        'passed_steps': passed,
        'total_steps': len(steps),
    }


def reset() -> dict[str, Any]:
    """Delete the event invoke config, function, role, queues, log group, and key alias; schedule the key's deletion."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if code in {'ResourceNotFoundException', 'NoSuchEntity', 'QueueDoesNotExist'} or 'NotFound' in code \
                    or 'NonExistent' in code:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    lam, iam, sqs, logs, kms = (_client(name) for name in ('lambda', 'iam', 'sqs', 'logs', 'kms'))
    try:
        exists = bool(_function())
    except (ClientError, BotoCoreError) as exc:
        exists = False
        errors.append(f'function: {exc}')
    if exists:
        attempt('event-invoke-config', lambda: lam.delete_function_event_invoke_config(FunctionName=FUNCTION))
        attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    attempt(f'role-policy:{ROLE_POLICY}', lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))
    for name in QUEUES:
        try:
            url = _queue_url(name)
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'queue:{name}: {exc}')
            continue
        if url:
            attempt(f'queue:{name}', lambda url=url: sqs.delete_queue(QueueUrl=url))
    attempt(f'log-group:{LOG_GROUP}', lambda: logs.delete_log_group(logGroupName=LOG_GROUP))
    try:
        key_arn = _key_arn()
    except (ClientError, BotoCoreError) as exc:
        key_arn = None
        errors.append(f'key: {exc}')
    if key_arn:
        attempt(f'alias:{KEY_ALIAS}', lambda: kms.delete_alias(AliasName=KEY_ALIAS))
        attempt('key:pending-deletion', lambda: kms.schedule_key_deletion(KeyId=key_arn, PendingWindowInDays=7))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws lambda delete-function-event-invoke-config --function-name {FUNCTION}\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws iam delete-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY}\n'
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            + ''.join(f'aws sqs delete-queue --queue-url <{name} url>\n' for name in QUEUES) +
            f'aws logs delete-log-group --log-group-name {LOG_GROUP}\n'
            f'aws kms delete-alias --alias-name {KEY_ALIAS}\n'
            'aws kms schedule-key-deletion --key-id <key-arn> --pending-window-in-days 7'
        ),
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'status': 'reset',
        'reset': True,
        'deleted_resources': deleted,
        'errors': errors,
        'verification': {
            'status': 'failed' if errors else 'passed',
            'message': 'Some lab resources could not be removed: ' + '; '.join(errors) if errors
            else 'The function, its async settings, the role, the queues, and the log group are deleted, and the '
                 'key is scheduled for deletion.',
        },
    }
