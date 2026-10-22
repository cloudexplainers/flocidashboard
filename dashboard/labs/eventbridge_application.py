"""API Gateway -> Lambda -> EventBridge application-spine capstone lab."""

from __future__ import annotations

import json
import time
import zipfile
from io import BytesIO
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

REGION = 'us-east-1'
ACCOUNT = '000000000000'
BUS = 'lab-application-events'
PROCESSING_QUEUE = 'lab-order-processing'
AUDIT_QUEUE = 'lab-order-audit'
DLQ = 'lab-eventbridge-dlq'
PRODUCER_ROLE = 'FlociEventProducerRole'
PRODUCER_POLICY = 'PutApplicationEvents'
NOTIFIER_ROLE = 'FlociEventNotifierRole'
NOTIFIER_POLICY = 'WriteNotificationLogs'
PRODUCER_FUNCTION = 'lab-event-producer'
NOTIFIER_FUNCTION = 'lab-event-notifier'
API_NAME = 'lab-event-api'
ROUTE_KEY = 'POST /orders'
STAGE = '$default'
API_PERMISSION = 'AllowFlociEventApi'
EVENT_PERMISSION = 'AllowFlociEventBridge'
PROCESSING_RULE = 'lab-process-orders'
AUDIT_RULE = 'lab-audit-orders'
NOTIFICATION_RULE = 'lab-priority-notifications'
LEGACY_BUS = 'floci-lab-application-events'
LEGACY_PROCESSING_QUEUE = 'floci-lab-order-processing'
LEGACY_AUDIT_QUEUE = 'floci-lab-order-audit'
LEGACY_DLQ = 'floci-lab-eventbridge-dlq'
LEGACY_PRODUCER_FUNCTION = 'floci-lab-event-producer'
LEGACY_NOTIFIER_FUNCTION = 'floci-lab-event-notifier'
LEGACY_API_NAME = 'floci-lab-event-api'
LEGACY_PROCESSING_RULE = 'floci-lab-process-orders'
LEGACY_AUDIT_RULE = 'floci-lab-audit-orders'
LEGACY_NOTIFICATION_RULE = 'floci-lab-priority-notifications'
CORRELATION = 'FLOCI-EVENT-5001'
DISABLED_CORRELATION = 'FLOCI-EVENT-DISABLED-5002'
CACHE_PREFIX = 'floci-lab:eventbridge:application-spine:'


def arn(service: str, resource: str) -> str:
    return f'arn:aws:{service}:{REGION}:{ACCOUNT}:{resource}'


QUEUE_ARNS = {
    PROCESSING_QUEUE: arn('sqs', PROCESSING_QUEUE),
    AUDIT_QUEUE: arn('sqs', AUDIT_QUEUE),
    DLQ: arn('sqs', DLQ),
}
PRODUCER_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{PRODUCER_ROLE}'
NOTIFIER_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{NOTIFIER_ROLE}'
PRODUCER_FUNCTION_ARN = arn('lambda', f'function:{PRODUCER_FUNCTION}')
NOTIFIER_FUNCTION_ARN = arn('lambda', f'function:{NOTIFIER_FUNCTION}')

TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
PRODUCER_POLICY_DOC = {
    'Version': '2012-10-17',
    'Statement': [
        # Floci 2.3 evaluates PutEvents at resource "*" before inspecting Entries.
        # Keep the action restricted; the producer code always uses the lab bus.
        {'Effect': 'Allow', 'Action': 'events:PutEvents', 'Resource': '*'},
        {
            'Effect': 'Allow',
            'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
            'Resource': arn('logs', f'log-group:/aws/lambda/{PRODUCER_FUNCTION}:*'),
        },
    ],
}
NOTIFIER_POLICY_DOC = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
            'Resource': arn('logs', f'log-group:/aws/lambda/{NOTIFIER_FUNCTION}:*'),
        }
    ],
}
ORDER_PATTERN = {'source': ['com.floci.orders'], 'detail-type': ['OrderCreated']}
PRIORITY_PATTERN = {'source': ['com.floci.orders'], 'detail': {'priority': ['high']}}
PROCESSING_TRANSFORMER = {
    'InputPathsMap': {'order': '$.detail.order_id', 'correlation': '$.detail.correlation_id'},
    'InputTemplate': '{"order_id":"<order>","correlation_id":"<correlation>","destination":"processing"}',
}
RETRY_POLICY = {'MaximumRetryAttempts': 2, 'MaximumEventAgeInSeconds': 60}
DEAD_LETTER_CONFIG = {'Arn': QUEUE_ARNS[DLQ]}

PRODUCER_SOURCE = '''import json, os, boto3
events = boto3.client("events", endpoint_url=os.environ.get("AWS_ENDPOINT_URL"))
def lambda_handler(event, context):
    try:
        body = event.get("body", event)
        if isinstance(body, str): body = json.loads(body)
        order_id = body["order_id"]
        correlation_id = body["correlation_id"]
        priority = body.get("priority", "normal")
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"outcome":"rejected","reason":str(exc)}))
        return {"statusCode":400,"headers":{"content-type":"application/json"},"body":json.dumps({"error":"Malformed order event"})}
    detail = {"order_id":order_id,"correlation_id":correlation_id,"priority":priority}
    result = events.put_events(Entries=[{"EventBusName":os.environ["EVENT_BUS_NAME"],"Source":"com.floci.orders","DetailType":"OrderCreated","Detail":json.dumps(detail)}])
    print(json.dumps({"outcome":"published","correlation_id":correlation_id,"event_id":result["Entries"][0].get("EventId")}))
    return {"statusCode":202,"headers":{"content-type":"application/json"},"body":json.dumps({"accepted":True,"correlation_id":correlation_id})}
'''
NOTIFIER_SOURCE = '''import json
def lambda_handler(event, context):
    correlation = event.get("correlation_id") or event.get("detail", {}).get("correlation_id")
    print(json.dumps({"outcome":"notification","correlation_id":correlation,"event":event}))
    return {"notified":True,"correlation_id":correlation}
'''


LAB = {
    'service': 'eventbridge',
    'key': 'application-spine',
    'title': 'Build an event-driven order application',
    'description': 'Build API Gateway → Lambda → EventBridge fan-out to two SQS queues and a notification Lambda, then inspect correlation and deliberately exercise failure paths.',
    'steps': [
        {
            'key': 'create-queues',
            'title': 'Create processing, audit, and dead-letter queues',
            'command': f'aws sqs create-queue --queue-name {PROCESSING_QUEUE}\naws sqs create-queue --queue-name {AUDIT_QUEUE}\naws sqs create-queue --queue-name {DLQ}',
            'explanation': 'Creates isolated destinations for transformed work, the full audit envelope, and failed-target intent.',
        },
        {
            'key': 'create-roles',
            'title': 'Create producer and notification Lambda roles',
            'command': f'aws iam create-role --role-name {PRODUCER_ROLE} --assume-role-policy-document file://lambda-trust.json\naws iam put-role-policy --role-name {PRODUCER_ROLE} --policy-name {PRODUCER_POLICY} --policy-document file://producer-policy.json',
            'explanation': 'Separates producer PutEvents permission from notifier logging permission. Floci 2.3 evaluates PutEvents at resource "*"; on AWS, replace that wildcard with the lab event bus ARN to restrict publishing to one bus.',
            'artifact_label': 'producer-policy.json',
            'artifact': json.dumps(PRODUCER_POLICY_DOC, indent=2),
        },
        {
            'key': 'create-functions',
            'title': 'Create producer and notification functions',
            'command': f'aws lambda create-function --function-name {PRODUCER_FUNCTION} --runtime python3.11 --role {PRODUCER_ROLE_ARN} --handler handler.lambda_handler --zip-file fileb://producer.zip\naws lambda create-function --function-name {NOTIFIER_FUNCTION} --runtime python3.11 --role {NOTIFIER_ROLE_ARN} --handler handler.lambda_handler --zip-file fileb://notifier.zip',
            'explanation': 'The API function validates requests and publishes a domain event; the target function logs a correlated notification.',
            'artifact_label': 'producer-handler.py',
            'artifact': PRODUCER_SOURCE,
            'secondary_artifact_label': 'notifier-handler.py',
            'secondary_artifact': NOTIFIER_SOURCE,
        },
        {
            'key': 'create-bus-rules',
            'title': 'Create the custom bus and three matching rules',
            'command': f'aws events create-event-bus --name {BUS}\naws events put-rule --name {PROCESSING_RULE} --event-bus-name {BUS} --event-pattern file://order-pattern.json\naws events put-rule --name {AUDIT_RULE} --event-bus-name {BUS} --event-pattern file://order-pattern.json\naws events put-rule --name {NOTIFICATION_RULE} --event-bus-name {BUS} --event-pattern file://priority-pattern.json',
            'explanation': 'Two rules match every OrderCreated event while the notification rule matches only high-priority orders.',
            'artifact_label': 'order-pattern.json',
            'artifact': json.dumps(ORDER_PATTERN, indent=2),
            'secondary_artifact_label': 'priority-pattern.json',
            'secondary_artifact': json.dumps(PRIORITY_PATTERN, indent=2),
        },
        {
            'key': 'put-targets',
            'title': 'Attach transformed, audit, and Lambda targets',
            'command': f'aws events put-targets --event-bus-name {BUS} --rule {PROCESSING_RULE} --targets file://processing-target.json',
            'explanation': 'Transforms the processing payload, preserves the full audit envelope, and invokes the notification Lambda. Retry/DLQ fields show the AWS shape; current Floci executes transformation and delivery but does not enforce target retry/DLQ behavior.',
            'artifact_label': 'processing-target.json',
            'artifact': json.dumps(
                {
                    'Id': 'processing',
                    'Arn': QUEUE_ARNS[PROCESSING_QUEUE],
                    'InputTransformer': PROCESSING_TRANSFORMER,
                    'RetryPolicy': RETRY_POLICY,
                    'DeadLetterConfig': DEAD_LETTER_CONFIG,
                },
                indent=2,
            ),
        },
        {
            'key': 'create-api',
            'title': 'Expose the producer through API Gateway',
            'command': f'aws apigatewayv2 create-api --name {API_NAME} --protocol-type HTTP\naws apigatewayv2 create-integration --api-id <api-id> --integration-type AWS_PROXY --integration-uri {PRODUCER_FUNCTION_ARN} --payload-format-version 2.0\naws apigatewayv2 create-route --api-id <api-id> --route-key "{ROUTE_KEY}" --target integrations/<integration-id>\naws apigatewayv2 create-stage --api-id <api-id> --stage-name $default --auto-deploy',
            'explanation': 'Builds the HTTP entry point and Lambda proxy route.',
        },
        {
            'key': 'add-permissions',
            'title': 'Add Lambda resource permissions',
            'command': f'aws lambda add-permission --function-name {PRODUCER_FUNCTION} --statement-id {API_PERMISSION} --action lambda:InvokeFunction --principal apigateway.amazonaws.com\naws lambda add-permission --function-name {NOTIFIER_FUNCTION} --statement-id {EVENT_PERMISSION} --action lambda:InvokeFunction --principal events.amazonaws.com',
            'explanation': 'Shows the resource-based permission each invoking service needs in AWS.',
        },
        {
            'key': 'happy-path',
            'title': 'Send a high-priority order and verify fan-out',
            'command': 'curl -X POST <api-endpoint>/orders -H "Content-Type: application/json" --data @order.json',
            'explanation': 'Verifies the transformed processing message, full EventBridge audit envelope, and notification Lambda correlation.',
            'artifact_label': 'order.json',
            'artifact': json.dumps(
                {'order_id': 'ORDER#5001', 'correlation_id': CORRELATION, 'priority': 'high'}, indent=2
            ),
        },
        {
            'key': 'malformed-event',
            'title': 'Inject a malformed request',
            'command': 'curl -X POST <api-endpoint>/orders -H "Content-Type: application/json" --data \'{"priority":"high"}\'',
            'explanation': 'Proves validation fails at the producer boundary with HTTP 400 before an event enters the bus.',
        },
        {
            'key': 'missing-permission',
            'title': 'Test the missing-permission boundary',
            'command': f'aws iam delete-role-policy --role-name {PRODUCER_ROLE} --policy-name {PRODUCER_POLICY}\ncurl -X POST <api-endpoint>/orders --data @order.json\naws iam put-role-policy --role-name {PRODUCER_ROLE} --policy-name {PRODUCER_POLICY} --policy-document file://producer-policy.json',
            'explanation': 'Temporarily removes events:PutEvents, records the observed local outcome, and restores the policy. The result explicitly distinguishes enforced denial from an emulator boundary.',
        },
        {
            'key': 'disabled-rule',
            'title': 'Disable one rule and observe selective fan-out',
            'command': f'aws events disable-rule --name {PROCESSING_RULE} --event-bus-name {BUS}\ncurl -X POST <api-endpoint>/orders --data @disabled-rule-order.json\naws events enable-rule --name {PROCESSING_RULE} --event-bus-name {BUS}',
            'explanation': 'Proves the audit path continues while the disabled processing rule receives no matching delivery, then restores the rule.',
            'artifact_label': 'disabled-rule-order.json',
            'artifact': json.dumps(
                {'order_id': 'ORDER#5002', 'correlation_id': DISABLED_CORRELATION, 'priority': 'normal'}, indent=2
            ),
        },
    ],
}


def client(name: str):
    return FlociClientFactory().client(name)


def zip_bytes(source: str) -> bytes:
    output = BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('handler.py', source)
    return output.getvalue()


def marker(key: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + key, _clean_response(value), timeout=86400)


def marked(key: str) -> Any:
    return cache.get(CACHE_PREFIX + key)


def result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    return {
        'service': 'eventbridge',
        'lab': 'application-spine',
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


def ignore_exists(action: Callable[[], Any], get_action: Callable[[], Any]) -> Any:
    try:
        return action()
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') not in {
            'ResourceConflictException',
            'EntityAlreadyExists',
            'ResourceAlreadyExistsException',
        }:
            raise
        return get_action()


def queue_url(name: str) -> str:
    return client('sqs').get_queue_url(QueueName=name)['QueueUrl']


def api(name: str = API_NAME) -> dict[str, Any] | None:
    for item in client('apigatewayv2').get_apis().get('Items', []):
        if item.get('Name') == name:
            return item
    return None


def create_queues() -> dict[str, Any]:
    started = time.perf_counter()
    sqs = client('sqs')
    created = []
    for name in QUEUE_ARNS:
        created.append(sqs.create_queue(QueueName=name))
    marker('queues')
    return result(
        'create-queues',
        'aws sqs create-queue ...',
        created,
        True,
        'Processing, audit, and dead-letter queues exist.',
        started,
    )


def create_roles() -> dict[str, Any]:
    started = time.perf_counter()
    iam = client('iam')
    for role, policy_name, policy in [
        (PRODUCER_ROLE, PRODUCER_POLICY, PRODUCER_POLICY_DOC),
        (NOTIFIER_ROLE, NOTIFIER_POLICY, NOTIFIER_POLICY_DOC),
    ]:
        try:
            iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(TRUST_POLICY))
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') != 'EntityAlreadyExists':
                raise
        iam.put_role_policy(RoleName=role, PolicyName=policy_name, PolicyDocument=json.dumps(policy))
    marker('roles')
    return result(
        'create-roles',
        'aws iam create-role ...',
        {'roles': [PRODUCER_ROLE, NOTIFIER_ROLE]},
        True,
        'Producer publishing and notifier logging policies exist. Floci evaluates PutEvents at resource "*"; on AWS, scope that statement to the event bus ARN.',
        started,
    )


def create_functions() -> dict[str, Any]:
    started = time.perf_counter()
    lam = client('lambda')
    responses = []
    for name, role, source in [
        (PRODUCER_FUNCTION, PRODUCER_ROLE_ARN, PRODUCER_SOURCE),
        (NOTIFIER_FUNCTION, NOTIFIER_ROLE_ARN, NOTIFIER_SOURCE),
    ]:
        kwargs = {
            'FunctionName': name,
            'Runtime': 'python3.11',
            'Role': role,
            'Handler': 'handler.lambda_handler',
            'Code': {'ZipFile': zip_bytes(source)},
            'Timeout': 10,
            'MemorySize': 128,
            'Publish': True,
        }
        if name == PRODUCER_FUNCTION:
            kwargs['Environment'] = {'Variables': {'EVENT_BUS_NAME': BUS}}
        responses.append(
            ignore_exists(
                lambda kwargs=kwargs: lam.create_function(**kwargs),
                lambda name=name: lam.get_function(FunctionName=name),
            )
        )
    marker('functions')
    return result(
        'create-functions',
        'aws lambda create-function ...',
        responses,
        True,
        'Producer and notification functions exist.',
        started,
    )


def create_bus_rules() -> dict[str, Any]:
    started = time.perf_counter()
    events = client('events')
    try:
        events.create_event_bus(Name=BUS, Description='Floci event-driven application lab')
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') not in {
            'ResourceAlreadyExistsException',
            'ResourceConflictException',
        }:
            raise
    responses = [
        events.put_rule(
            Name=PROCESSING_RULE, EventBusName=BUS, EventPattern=json.dumps(ORDER_PATTERN), State='ENABLED'
        ),
        events.put_rule(Name=AUDIT_RULE, EventBusName=BUS, EventPattern=json.dumps(ORDER_PATTERN), State='ENABLED'),
        events.put_rule(
            Name=NOTIFICATION_RULE, EventBusName=BUS, EventPattern=json.dumps(PRIORITY_PATTERN), State='ENABLED'
        ),
    ]
    marker('rules')
    return result(
        'create-bus-rules',
        'aws events create-event-bus ...',
        responses,
        True,
        'Custom bus and three enabled rules exist.',
        started,
    )


def put_targets() -> dict[str, Any]:
    started = time.perf_counter()
    events = client('events')
    processing = {
        'Id': 'processing',
        'Arn': QUEUE_ARNS[PROCESSING_QUEUE],
        'InputTransformer': PROCESSING_TRANSFORMER,
        'RetryPolicy': RETRY_POLICY,
        'DeadLetterConfig': DEAD_LETTER_CONFIG,
    }
    responses = [
        events.put_targets(Rule=PROCESSING_RULE, EventBusName=BUS, Targets=[processing]),
        events.put_targets(
            Rule=AUDIT_RULE,
            EventBusName=BUS,
            Targets=[
                {
                    'Id': 'audit',
                    'Arn': QUEUE_ARNS[AUDIT_QUEUE],
                    'RetryPolicy': RETRY_POLICY,
                    'DeadLetterConfig': DEAD_LETTER_CONFIG,
                }
            ],
        ),
        events.put_targets(
            Rule=NOTIFICATION_RULE,
            EventBusName=BUS,
            Targets=[
                {
                    'Id': 'notification',
                    'Arn': NOTIFIER_FUNCTION_ARN,
                    'InputTransformer': {
                        'InputPathsMap': {'correlation': '$.detail.correlation_id', 'order': '$.detail.order_id'},
                        'InputTemplate': '{"correlation_id":"<correlation>","order_id":"<order>"}',
                    },
                    'RetryPolicy': RETRY_POLICY,
                    'DeadLetterConfig': DEAD_LETTER_CONFIG,
                }
            ],
        ),
    ]
    marker('targets')
    marker('retry-boundary', {'configured_request': True, 'floci_enforced': False})
    return result(
        'put-targets',
        'aws events put-targets ...',
        responses,
        all(r.get('FailedEntryCount', 0) == 0 for r in responses),
        'Three targets are attached. Input transformers execute locally; retry and DLQ fields are teaching artifacts because this Floci version does not persist or enforce them.',
        started,
    )


def create_api() -> dict[str, Any]:
    started = time.perf_counter()
    gateway = client('apigatewayv2')
    current = api()
    if not current:
        current = gateway.create_api(Name=API_NAME, ProtocolType='HTTP', Description='EventBridge application lab API')
    api_id = current['ApiId']
    integrations = gateway.get_integrations(ApiId=api_id).get('Items', [])
    integration = next(
        (x for x in integrations if x.get('IntegrationUri') == PRODUCER_FUNCTION_ARN), None
    ) or gateway.create_integration(
        ApiId=api_id, IntegrationType='AWS_PROXY', IntegrationUri=PRODUCER_FUNCTION_ARN, PayloadFormatVersion='2.0'
    )
    routes = gateway.get_routes(ApiId=api_id).get('Items', [])
    if not any(x.get('RouteKey') == ROUTE_KEY for x in routes):
        gateway.create_route(ApiId=api_id, RouteKey=ROUTE_KEY, Target=f'integrations/{integration["IntegrationId"]}')
    stages = gateway.get_stages(ApiId=api_id).get('Items', [])
    if not any(x.get('StageName') == STAGE for x in stages):
        gateway.create_stage(ApiId=api_id, StageName=STAGE, AutoDeploy=True)
    marker('api-id', api_id)
    return result(
        'create-api',
        'aws apigatewayv2 create-api ...',
        current,
        True,
        'HTTP API, Lambda proxy integration, POST route, and default stage exist.',
        started,
    )


def add_permissions() -> dict[str, Any]:
    started = time.perf_counter()
    lam = client('lambda')
    api_id = (api() or {}).get('ApiId')
    calls = [
        (PRODUCER_FUNCTION, API_PERMISSION, 'apigateway.amazonaws.com', arn('execute-api', f'{api_id}/*/*/orders')),
        (NOTIFIER_FUNCTION, EVENT_PERMISSION, 'events.amazonaws.com', arn('events', f'rule/{BUS}/{NOTIFICATION_RULE}')),
    ]
    for function, sid, principal, source in calls:
        try:
            lam.add_permission(
                FunctionName=function,
                StatementId=sid,
                Action='lambda:InvokeFunction',
                Principal=principal,
                SourceArn=source,
            )
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') != 'ResourceConflictException':
                raise
    marker('permissions')
    return result(
        'add-permissions',
        'aws lambda add-permission ...',
        {'statements': [API_PERMISSION, EVENT_PERMISSION]},
        True,
        'API Gateway and EventBridge Lambda resource permissions exist.',
        started,
    )


def request_url(path: str = '/orders') -> str:
    api_id = (api() or {}).get('ApiId')
    return f'{FlociClientFactory().endpoint_url.rstrip("/")}/restapis/{api_id}/$default/_user_request_{path}'


def http_post(payload: Any) -> tuple[int, Any]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    request = Request(request_url(), data=raw.encode(), headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=12) as response:
            status_code, body = response.getcode(), response.read().decode()
    except HTTPError as exc:
        status_code, body = exc.code, exc.read().decode()
    try:
        return status_code, json.loads(body)
    except json.JSONDecodeError:
        return status_code, body


def receive_named(name: str, correlation: str, timeout: float = 8) -> dict[str, Any] | None:
    sqs = client('sqs')
    deadline = time.time() + timeout
    while time.time() < deadline:
        response = sqs.receive_message(
            QueueUrl=queue_url(name), MaxNumberOfMessages=10, VisibilityTimeout=0, WaitTimeSeconds=1
        )
        for message in response.get('Messages', []):
            if correlation not in message.get('Body', ''):
                continue
            if name == PROCESSING_QUEUE:
                try:
                    parsed = json.loads(message.get('Body', ''))
                except json.JSONDecodeError:
                    # Remove only a malformed lab-owned transformed message so a corrected rerun can recover.
                    sqs.delete_message(QueueUrl=queue_url(name), ReceiptHandle=message['ReceiptHandle'])
                    continue
                if parsed.get('destination') != 'processing':
                    continue
            return message
    return None


def logs_contain(function: str, correlation: str, timeout: float = 8) -> bool:
    logs = client('logs')
    group = f'/aws/lambda/{function}'
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            streams = logs.describe_log_streams(
                logGroupName=group, orderBy='LastEventTime', descending=True, limit=10
            ).get('logStreams', [])
            for stream in streams:
                events = logs.get_log_events(logGroupName=group, logStreamName=stream['logStreamName']).get(
                    'events', []
                )
                if any(correlation in event.get('message', '') for event in events):
                    return True
        except ClientError:
            pass
        time.sleep(0.4)
    return False


def happy_path() -> dict[str, Any]:
    started = time.perf_counter()
    status_code, body = http_post({'order_id': 'ORDER#5001', 'correlation_id': CORRELATION, 'priority': 'high'})
    processing = receive_named(PROCESSING_QUEUE, CORRELATION)
    audit = receive_named(AUDIT_QUEUE, CORRELATION)
    notification = logs_contain(NOTIFIER_FUNCTION, CORRELATION)
    payload = {
        'http_status': status_code,
        'response': body,
        'processing_message': processing,
        'audit_message': audit,
        'notification_log': notification,
    }
    ok = status_code == 202 and bool(processing and audit and notification)
    marker('happy-path', payload if ok else False)
    return result(
        'happy-path',
        'curl -X POST ...',
        payload,
        ok,
        'The request returned 202 and the correlation ID reached transformed processing, full audit, and notification-log paths.'
        if ok
        else 'One or more fan-out paths did not verify before the polling window expired.',
        started,
    )


def malformed_event() -> dict[str, Any]:
    started = time.perf_counter()
    status_code, body = http_post({'priority': 'high'})
    ok = status_code == 400
    marker('malformed', {'status': status_code, 'body': body} if ok else False)
    return result(
        'malformed-event',
        'curl ... malformed',
        {'http_status': status_code, 'response': body},
        ok,
        'Malformed input was rejected with HTTP 400 before PutEvents.'
        if ok
        else 'The malformed request was not rejected as expected.',
        started,
    )


def missing_permission() -> dict[str, Any]:
    started = time.perf_counter()
    iam = client('iam')
    iam.delete_role_policy(RoleName=PRODUCER_ROLE, PolicyName=PRODUCER_POLICY)
    try:
        status_code, body = http_post(
            {'order_id': 'ORDER#DENIED', 'correlation_id': 'FLOCI-EVENT-DENIED', 'priority': 'normal'}
        )
    finally:
        iam.put_role_policy(
            RoleName=PRODUCER_ROLE, PolicyName=PRODUCER_POLICY, PolicyDocument=json.dumps(PRODUCER_POLICY_DOC)
        )
    enforced = status_code >= 500
    observed = {'http_status': status_code, 'response': body, 'permission_enforced': enforced, 'policy_restored': True}
    marker('missing-permission', observed)
    return result(
        'missing-permission',
        'aws iam delete-role-policy ...',
        observed,
        True,
        'The permission was removed, the observed outcome was recorded, and the policy was restored. Floci enforced the denial.'
        if enforced
        else 'The permission was removed and restored safely. This Floci runtime did not enforce the Lambda execution-role denial on PutEvents, so the lab records an emulator boundary rather than a false failure.',
        started,
    )


def disabled_rule() -> dict[str, Any]:
    started = time.perf_counter()
    events = client('events')
    events.disable_rule(Name=PROCESSING_RULE, EventBusName=BUS)
    try:
        status_code, body = http_post(
            {'order_id': 'ORDER#5002', 'correlation_id': DISABLED_CORRELATION, 'priority': 'normal'}
        )
        audit = receive_named(AUDIT_QUEUE, DISABLED_CORRELATION)
        processing = receive_named(PROCESSING_QUEUE, DISABLED_CORRELATION, timeout=2)
    finally:
        events.enable_rule(Name=PROCESSING_RULE, EventBusName=BUS)
    ok = status_code == 202 and bool(audit) and not processing
    payload = {
        'http_status': status_code,
        'response': body,
        'audit_message': audit,
        'processing_message': processing,
        'rule_restored': True,
    }
    marker('disabled-rule', payload if ok else False)
    return result(
        'disabled-rule',
        'aws events disable-rule ...',
        payload,
        ok,
        'The audit rule still delivered, the disabled processing rule did not, and the processing rule was re-enabled.'
        if ok
        else 'Selective fan-out did not match the expected disabled-rule behavior.',
        started,
    )


RUNNERS = {
    'create-queues': create_queues,
    'create-roles': create_roles,
    'create-functions': create_functions,
    'create-bus-rules': create_bus_rules,
    'put-targets': put_targets,
    'create-api': create_api,
    'add-permissions': add_permissions,
    'happy-path': happy_path,
    'malformed-event': malformed_event,
    'missing-permission': missing_permission,
    'disabled-rule': disabled_rule,
}


def run_step(step_key: str) -> dict[str, Any]:
    if step_key not in RUNNERS:
        raise ValueError(f'Unknown EventBridge application lab step: {step_key}')
    return RUNNERS[step_key]()


def status() -> dict[str, Any]:
    checks = {
        key: bool(marked(key))
        for key in [
            'queues',
            'roles',
            'functions',
            'rules',
            'targets',
            'api-id',
            'permissions',
            'happy-path',
            'malformed',
            'missing-permission',
            'disabled-rule',
        ]
    }
    mapping = {
        'create-queues': 'queues',
        'create-roles': 'roles',
        'create-functions': 'functions',
        'create-bus-rules': 'rules',
        'put-targets': 'targets',
        'create-api': 'api-id',
        'add-permissions': 'permissions',
        'happy-path': 'happy-path',
        'malformed-event': 'malformed',
        'missing-permission': 'missing-permission',
        'disabled-rule': 'disabled-rule',
    }
    return {
        'service': 'eventbridge',
        'lab': 'application-spine',
        'complete': all(checks.values()),
        'steps': {
            step: {
                'verified': checks[key],
                'verification': {
                    'status': 'passed',
                    'message': 'Verified by the lab runner and retained for this local workflow.',
                }
                if checks[key]
                else None,
            }
            for step, key in mapping.items()
        },
    }


def delete_ignoring(action: Callable[[], Any], codes: set[str]) -> bool:
    try:
        action()
        return True
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') in codes:
            return False
        raise


def reset() -> dict[str, Any]:
    started = time.perf_counter()
    events = client('events')
    lam = client('lambda')
    gateway = client('apigatewayv2')
    logs = client('logs')
    iam = client('iam')
    sqs = client('sqs')
    resource_sets = [
        {
            'bus': BUS,
            'api': API_NAME,
            'rules': [
                (PROCESSING_RULE, ['processing']),
                (AUDIT_RULE, ['audit']),
                (NOTIFICATION_RULE, ['notification']),
            ],
            'functions': [PRODUCER_FUNCTION, NOTIFIER_FUNCTION],
            'queues': [PROCESSING_QUEUE, AUDIT_QUEUE, DLQ],
        },
        {
            'bus': LEGACY_BUS,
            'api': LEGACY_API_NAME,
            'rules': [
                (LEGACY_PROCESSING_RULE, ['processing']),
                (LEGACY_AUDIT_RULE, ['audit']),
                (LEGACY_NOTIFICATION_RULE, ['notification']),
            ],
            'functions': [LEGACY_PRODUCER_FUNCTION, LEGACY_NOTIFIER_FUNCTION],
            'queues': [LEGACY_PROCESSING_QUEUE, LEGACY_AUDIT_QUEUE, LEGACY_DLQ],
        },
    ]
    for resources in resource_sets:
        bus_name = resources['bus']
        for rule, ids in resources['rules']:
            try:
                events.remove_targets(Rule=rule, EventBusName=bus_name, Ids=ids, Force=True)
            except ClientError:
                pass
            delete_ignoring(
                lambda rule=rule, bus_name=bus_name: events.delete_rule(Name=rule, EventBusName=bus_name, Force=True),
                {'ResourceNotFoundException'},
            )
        delete_ignoring(lambda bus_name=bus_name: events.delete_event_bus(Name=bus_name), {'ResourceNotFoundException'})
        current = api(resources['api'])
        if current:
            gateway.delete_api(ApiId=current['ApiId'])
        for function, sid in zip(resources['functions'], [API_PERMISSION, EVENT_PERMISSION]):
            delete_ignoring(
                lambda function=function, sid=sid: lam.remove_permission(FunctionName=function, StatementId=sid),
                {'ResourceNotFoundException'},
            )
            delete_ignoring(
                lambda function=function: lam.delete_function(FunctionName=function), {'ResourceNotFoundException'}
            )
            delete_ignoring(
                lambda function=function: logs.delete_log_group(logGroupName=f'/aws/lambda/{function}'),
                {'ResourceNotFoundException'},
            )
        for name in resources['queues']:
            try:
                sqs.delete_queue(QueueUrl=queue_url(name))
            except ClientError:
                pass
    for role, policy in [(PRODUCER_ROLE, PRODUCER_POLICY), (NOTIFIER_ROLE, NOTIFIER_POLICY)]:
        delete_ignoring(
            lambda role=role, policy=policy: iam.delete_role_policy(RoleName=role, PolicyName=policy), {'NoSuchEntity'}
        )
        delete_ignoring(lambda role=role: iam.delete_role(RoleName=role), {'NoSuchEntity'})
    cache.delete_many(
        [
            CACHE_PREFIX + key
            for key in [
                'queues',
                'roles',
                'functions',
                'rules',
                'targets',
                'retry-boundary',
                'api-id',
                'permissions',
                'happy-path',
                'malformed',
                'missing-permission',
                'disabled-rule',
            ]
        ]
    )
    payload = {
        'removed': True,
        'legacy_names_removed': True,
        'dependency_order': [
            'targets',
            'rules',
            'bus',
            'api',
            'permissions',
            'functions',
            'logs',
            'role policies',
            'roles',
            'queues',
        ],
    }
    return {
        'service': 'eventbridge',
        'lab': 'application-spine',
        'command': 'aws events remove-targets ... # dependency-safe cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verification': {
            'status': 'passed',
            'message': 'Lab resources and recorded milestones were removed in dependency order.',
        },
    }
