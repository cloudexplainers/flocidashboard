"""API Gateway workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response

from . import apigateway_developer_labs as rest_api_developer
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _apigatewayv2_client,
    _error_code,
    _floci_client_factory,
    _iam_client,
    _ignore_missing,
    _ignore_missing_codes,
    _json_text,
    _lambda_client,
    _logs_client,
)
from .lambda_labs import (
    LAMBDA_FUNCTION_NAME,
    LAMBDA_HANDLER,
    LAMBDA_HANDLER_SOURCE,
    LAMBDA_INVOKE_CACHE_KEY,
    LAMBDA_INVOKE_PAYLOAD,
    LAMBDA_INVOKE_PAYLOAD_TEXT,
    LAMBDA_LAB_LOGS_POLICY,
    LAMBDA_LAB_TRUST_POLICY,
    LAMBDA_LOGS_CACHE_KEY,
    LAMBDA_LOG_GROUP_NAME,
    LAMBDA_ROLE_ARN,
    LAMBDA_ROLE_NAME,
    LAMBDA_ROLE_POLICY_NAME,
    LAMBDA_RUNTIME,
    _run_lambda_create_function,
    _run_lambda_create_role,
    _run_lambda_put_role_policy,
    _verify_lambda_function,
    _verify_lambda_role,
    _verify_lambda_role_policy,
)


LAMBDA_FUNCTION_ARN = f'arn:aws:lambda:{AWS_REGION}:{AWS_ACCOUNT_ID}:function:{LAMBDA_FUNCTION_NAME}'
APIGW_LAMBDA_API_NAME = 'lab-lambda-api'
APIGW_LAMBDA_ROUTE_KEY = 'POST /echo'
APIGW_LAMBDA_PATH = '/echo'
APIGW_LAMBDA_STAGE_NAME = '$default'
APIGW_LAMBDA_PERMISSION_SID = 'AllowFlociApiGatewayInvoke'
APIGW_LAMBDA_REQUEST_CACHE_KEY = 'floci-lab:apigateway:lambda-request:request'
APIGW_LAMBDA_API_ID_CACHE_KEY = 'floci-lab:apigateway:lambda-request:api-id'
APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY = 'floci-lab:apigateway:lambda-request:integration-id'
APIGW_LAMBDA_ROUTE_ID_CACHE_KEY = 'floci-lab:apigateway:lambda-request:route-id'
APIGW_LAMBDA_REQUEST_LAB = {
    'service': 'apigateway',
    'key': 'lambda-request',
    'title': 'Send an API Gateway request to Lambda',
    'description': 'Create a local HTTP API, connect a POST route to the Lambda echo function, grant invoke permission, and verify an HTTP request reaches the handler.',
    'steps': [
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Ensures the Lambda execution role exists before the API is wired to the function.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow Lambda to write logs',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_ROLE_NAME} --policy-name {LAMBDA_ROLE_POLICY_NAME} --policy-document file://lambda-logs-policy.json',
            'explanation': 'Keeps the function observable when the API Gateway route invokes it.',
            'artifact_label': 'lambda-logs-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_LOGS_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the API handler function',
            'command': f'aws lambda create-function --function-name {LAMBDA_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip',
            'explanation': 'Creates the function that will receive the API Gateway proxy event.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_HANDLER_SOURCE,
        },
        {
            'key': 'create-api',
            'title': 'Create an HTTP API',
            'command': f'aws apigatewayv2 create-api --name {APIGW_LAMBDA_API_NAME} --protocol-type HTTP',
            'explanation': 'Creates the API Gateway v2 HTTP API that will expose the Lambda function locally.',
        },
        {
            'key': 'create-integration',
            'title': 'Create the Lambda proxy integration',
            'command': f'aws apigatewayv2 create-integration --api-id <api-id> --integration-type AWS_PROXY --integration-uri {LAMBDA_FUNCTION_ARN} --payload-format-version 2.0',
            'explanation': 'Connects API Gateway to the Lambda Invoke API using the AWS_PROXY integration type.',
        },
        {
            'key': 'create-route',
            'title': 'Create the POST /echo route',
            'command': 'aws apigatewayv2 create-route --api-id <api-id> --route-key "POST /echo" --target integrations/<integration-id>',
            'explanation': 'Routes POST requests for /echo to the Lambda proxy integration.',
        },
        {
            'key': 'create-stage',
            'title': 'Create the default stage',
            'command': 'aws apigatewayv2 create-stage --api-id <api-id> --stage-name $default --auto-deploy',
            'explanation': 'Publishes route changes automatically through the default HTTP API stage.',
        },
        {
            'key': 'add-lambda-permission',
            'title': 'Allow API Gateway to invoke Lambda',
            'command': f'aws lambda add-permission --function-name {LAMBDA_FUNCTION_NAME} --statement-id {APIGW_LAMBDA_PERMISSION_SID} --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn arn:aws:execute-api:{AWS_REGION}:{AWS_ACCOUNT_ID}:<api-id>/*/*/echo',
            'explanation': 'Adds the resource policy statement API Gateway needs before it can invoke the function.',
        },
        {
            'key': 'send-request',
            'title': 'Send a local API request',
            'command': 'curl -X POST <api-endpoint>/echo -H "Content-Type: application/json" --data @event.json',
            'explanation': 'Sends the lab event through the local API Gateway data plane and verifies the Lambda echo response.',
            'artifact_label': 'event.json',
            'artifact': json.dumps(LAMBDA_INVOKE_PAYLOAD, indent=2),
        },
    ],
}


def _apigw_items(operation: str, result_key: str, **kwargs) -> list[dict[str, Any]]:
    client = _apigatewayv2_client()
    if hasattr(client, 'get_paginator'):
        try:
            paginator = client.get_paginator(operation)
            return paginator.paginate(**kwargs).build_full_result().get(result_key, [])
        except (ClientError, KeyError, AttributeError, ValueError):
            pass
    return getattr(client, operation)(**kwargs).get(result_key, [])


def _find_apigw_lambda_api() -> dict[str, Any] | None:
    cached_api_id = cache.get(APIGW_LAMBDA_API_ID_CACHE_KEY)
    if cached_api_id:
        try:
            api = _apigatewayv2_client().get_api(ApiId=cached_api_id)
            if api.get('Name') == APIGW_LAMBDA_API_NAME:
                return api
        except ClientError:
            cache.delete(APIGW_LAMBDA_API_ID_CACHE_KEY)

    for api in _apigw_items('get_apis', 'Items'):
        if api.get('Name') == APIGW_LAMBDA_API_NAME:
            api_id = api.get('ApiId')
            if api_id:
                cache.set(APIGW_LAMBDA_API_ID_CACHE_KEY, api_id, timeout=86400)
            return api
    return None


def _apigw_lambda_api_id() -> str:
    api = _find_apigw_lambda_api()
    api_id = (api or {}).get('ApiId')
    if not api_id:
        raise ValueError('API Gateway HTTP API has not been created yet.')
    return api_id


def _verify_apigw_lambda_api() -> dict[str, Any]:
    try:
        api = _find_apigw_lambda_api()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if api and api.get('ProtocolType') == 'HTTP':
        return {
            'status': 'passed',
            'message': f'HTTP API {APIGW_LAMBDA_API_NAME} exists.',
            'resource': _clean_response(api),
        }

    return {
        'status': 'failed',
        'message': f'HTTP API {APIGW_LAMBDA_API_NAME} was not returned.',
    }


def _find_apigw_lambda_integration(api_id: str) -> dict[str, Any] | None:
    cached_id = cache.get(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY)
    integrations = _apigw_items('get_integrations', 'Items', ApiId=api_id)
    for integration in integrations:
        if cached_id and integration.get('IntegrationId') == cached_id:
            return integration
        if (
            integration.get('IntegrationType') == 'AWS_PROXY'
            and integration.get('IntegrationUri') == LAMBDA_FUNCTION_ARN
        ):
            integration_id = integration.get('IntegrationId')
            if integration_id:
                cache.set(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY, integration_id, timeout=86400)
            return integration
    return None


def _apigw_lambda_integration_id() -> str:
    integration = _find_apigw_lambda_integration(_apigw_lambda_api_id())
    integration_id = (integration or {}).get('IntegrationId')
    if not integration_id:
        raise ValueError('API Gateway Lambda integration has not been created yet.')
    return integration_id


def _verify_apigw_lambda_integration() -> dict[str, Any]:
    try:
        api_id = _apigw_lambda_api_id()
        integration = _find_apigw_lambda_integration(api_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}

    if integration:
        return {
            'status': 'passed',
            'message': f'API {APIGW_LAMBDA_API_NAME} has a Lambda proxy integration.',
            'resource': _clean_response(integration),
        }
    return {
        'status': 'failed',
        'message': 'The Lambda proxy integration was not returned.',
    }


def _find_apigw_lambda_route(api_id: str) -> dict[str, Any] | None:
    cached_id = cache.get(APIGW_LAMBDA_ROUTE_ID_CACHE_KEY)
    routes = _apigw_items('get_routes', 'Items', ApiId=api_id)
    integration_id = cache.get(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY)
    expected_target = f'integrations/{integration_id}' if integration_id else None
    for route in routes:
        if cached_id and route.get('RouteId') == cached_id:
            return route
        if route.get('RouteKey') == APIGW_LAMBDA_ROUTE_KEY and (
            not expected_target or route.get('Target') == expected_target
        ):
            route_id = route.get('RouteId')
            if route_id:
                cache.set(APIGW_LAMBDA_ROUTE_ID_CACHE_KEY, route_id, timeout=86400)
            return route
    return None


def _verify_apigw_lambda_route() -> dict[str, Any]:
    try:
        api_id = _apigw_lambda_api_id()
        route = _find_apigw_lambda_route(api_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    if route:
        return {
            'status': 'passed',
            'message': f'Route {APIGW_LAMBDA_ROUTE_KEY} targets the Lambda integration.',
            'resource': _clean_response(route),
        }
    return {
        'status': 'failed',
        'message': f'Route {APIGW_LAMBDA_ROUTE_KEY} was not returned.',
    }


def _verify_apigw_lambda_stage() -> dict[str, Any]:
    try:
        api_id = _apigw_lambda_api_id()
        stages = _apigw_items('get_stages', 'Items', ApiId=api_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    for stage in stages:
        if stage.get('StageName') == APIGW_LAMBDA_STAGE_NAME:
            return {
                'status': 'passed',
                'message': f'Stage {APIGW_LAMBDA_STAGE_NAME} is available for the HTTP API.',
                'resource': _clean_response(stage),
            }
    return {
        'status': 'failed',
        'message': f'Stage {APIGW_LAMBDA_STAGE_NAME} was not returned.',
    }


def _verify_apigw_lambda_permission() -> dict[str, Any]:
    try:
        policy = json.loads(_lambda_client().get_policy(FunctionName=LAMBDA_FUNCTION_NAME).get('Policy', '{}'))
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    statements = policy.get('Statement', [])
    for statement in statements:
        if (
            statement.get('Sid') == APIGW_LAMBDA_PERMISSION_SID
            and statement.get('Principal', {}).get('Service') == 'apigateway.amazonaws.com'
            and statement.get('Action') == 'lambda:InvokeFunction'
        ):
            return {
                'status': 'passed',
                'message': 'Lambda resource policy allows API Gateway to invoke the function.',
                'resource': _clean_response(statement),
            }
    return {
        'status': 'failed',
        'message': 'The API Gateway invoke permission was not returned in the Lambda policy.',
    }


def _verify_apigw_lambda_request() -> dict[str, Any]:
    cached = cache.get(APIGW_LAMBDA_REQUEST_CACHE_KEY)
    if cached:
        message = (
            'The local API Gateway request reached the Lambda handler and produced the expected log marker.'
            if cached.get('verification_basis') == 'lambda_log_marker'
            else 'The local API Gateway request returned the expected Lambda response.'
        )
        return {
            'status': 'passed',
            'message': message,
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful API Gateway request has been recorded for this lab run.',
    }


def _apigw_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'apigateway',
        'lab': 'lambda-request',
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


def _run_apigw_create_api() -> dict[str, Any]:
    command = f'aws apigatewayv2 create-api --name {APIGW_LAMBDA_API_NAME} --protocol-type HTTP'
    started = time.perf_counter()
    existing = _find_apigw_lambda_api()
    if existing:
        response = existing
    else:
        response = _apigatewayv2_client().create_api(
            Name=APIGW_LAMBDA_API_NAME,
            ProtocolType='HTTP',
            Description='Floci dashboard API Gateway to Lambda workflow lab.',
            RouteSelectionExpression='$request.method $request.path',
        )
    api_id = response.get('ApiId')
    if api_id:
        cache.set(APIGW_LAMBDA_API_ID_CACHE_KEY, api_id, timeout=86400)
    return _apigw_step_result(
        'create-api',
        command,
        response,
        _verify_apigw_lambda_api(),
        started,
    )


def _run_apigw_create_integration() -> dict[str, Any]:
    command = (
        'aws apigatewayv2 create-integration --api-id <api-id> '
        f'--integration-type AWS_PROXY --integration-uri {LAMBDA_FUNCTION_ARN} '
        '--payload-format-version 2.0'
    )
    started = time.perf_counter()
    api_id = _apigw_lambda_api_id()
    existing = _find_apigw_lambda_integration(api_id)
    if existing:
        response = existing
    else:
        response = _apigatewayv2_client().create_integration(
            ApiId=api_id,
            IntegrationType='AWS_PROXY',
            IntegrationUri=LAMBDA_FUNCTION_ARN,
            PayloadFormatVersion='2.0',
        )
    integration_id = response.get('IntegrationId')
    if integration_id:
        cache.set(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY, integration_id, timeout=86400)
    return _apigw_step_result(
        'create-integration',
        command,
        response,
        _verify_apigw_lambda_integration(),
        started,
    )


def _run_apigw_create_route() -> dict[str, Any]:
    command = 'aws apigatewayv2 create-route --api-id <api-id> --route-key "POST /echo" --target integrations/<integration-id>'
    started = time.perf_counter()
    api_id = _apigw_lambda_api_id()
    integration_id = _apigw_lambda_integration_id()
    existing = _find_apigw_lambda_route(api_id)
    if existing:
        response = existing
    else:
        response = _apigatewayv2_client().create_route(
            ApiId=api_id,
            RouteKey=APIGW_LAMBDA_ROUTE_KEY,
            Target=f'integrations/{integration_id}',
        )
    route_id = response.get('RouteId')
    if route_id:
        cache.set(APIGW_LAMBDA_ROUTE_ID_CACHE_KEY, route_id, timeout=86400)
    return _apigw_step_result(
        'create-route',
        command,
        response,
        _verify_apigw_lambda_route(),
        started,
    )


def _run_apigw_create_stage() -> dict[str, Any]:
    command = 'aws apigatewayv2 create-stage --api-id <api-id> --stage-name $default --auto-deploy'
    started = time.perf_counter()
    api_id = _apigw_lambda_api_id()
    stage_verification = _verify_apigw_lambda_stage()
    if stage_verification.get('status') == 'passed':
        response = stage_verification.get('resource', {})
    else:
        response = _apigatewayv2_client().create_stage(
            ApiId=api_id,
            StageName=APIGW_LAMBDA_STAGE_NAME,
            AutoDeploy=True,
        )
    return _apigw_step_result(
        'create-stage',
        command,
        response,
        _verify_apigw_lambda_stage(),
        started,
    )


def _run_apigw_add_lambda_permission() -> dict[str, Any]:
    source_arn = f'arn:aws:execute-api:{AWS_REGION}:{AWS_ACCOUNT_ID}:{_apigw_lambda_api_id()}/*/*/echo'
    command = (
        f'aws lambda add-permission --function-name {LAMBDA_FUNCTION_NAME} '
        f'--statement-id {APIGW_LAMBDA_PERMISSION_SID} '
        '--action lambda:InvokeFunction --principal apigateway.amazonaws.com '
        f'--source-arn {source_arn}'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().add_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId=APIGW_LAMBDA_PERMISSION_SID,
            Action='lambda:InvokeFunction',
            Principal='apigateway.amazonaws.com',
            SourceArn=source_arn,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_policy(FunctionName=LAMBDA_FUNCTION_NAME)
    return _apigw_step_result(
        'add-lambda-permission',
        command,
        response,
        _verify_apigw_lambda_permission(),
        started,
    )


def _apigw_lambda_request_url() -> str:
    api_id = _apigw_lambda_api_id()
    stage_name = APIGW_LAMBDA_STAGE_NAME
    return (
        f'{_floci_client_factory().endpoint_url.rstrip("/")}/restapis/{api_id}/'
        f'{stage_name}/_user_request_{APIGW_LAMBDA_PATH}'
    )


def _apigw_lambda_request_log_verification() -> dict[str, Any]:
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
                if LAMBDA_INVOKE_PAYLOAD['request_id'] in event.get('message', ''):
                    return {
                        'status': 'passed',
                        'message': 'The API Gateway request reached the Lambda handler and produced the expected log marker.',
                        'resource': {
                            'log_group_name': LAMBDA_LOG_GROUP_NAME,
                            'log_stream_name': stream_name,
                            'event': _clean_response(event),
                        },
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No Lambda log event for {LAMBDA_INVOKE_PAYLOAD["request_id"]} was found after the API request.',
    }


def _run_apigw_send_request() -> dict[str, Any]:
    command = 'curl -X POST <api-endpoint>/echo -H "Content-Type: application/json" --data @event.json'
    started = time.perf_counter()
    url = _apigw_lambda_request_url()
    request = Request(
        url,
        data=LAMBDA_INVOKE_PAYLOAD_TEXT.encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read().decode('utf-8', errors='replace')
            status_code = response.getcode()
            headers = dict(response.headers.items())
    except HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        status_code = exc.code
        headers = dict(exc.headers.items())
    except (OSError, URLError) as exc:
        raise ValueError(f'API Gateway request could not be completed: {exc}') from exc
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    response_payload = {
        'url': url,
        'status_code': status_code,
        'headers': headers,
        'body': body,
        'json': parsed,
    }
    body_verified = (
        status_code == 200
        and isinstance(parsed, dict)
        and parsed.get('ok') is True
        and parsed.get('request_id') == LAMBDA_INVOKE_PAYLOAD['request_id']
    )
    log_verification = (
        {'status': 'failed', 'message': 'Lambda logs were not checked.'}
        if body_verified or status_code != 200
        else _apigw_lambda_request_log_verification()
    )
    verified = body_verified or (
        status_code == 200
        and log_verification.get('status') == 'passed'
    )
    if verified:
        response_payload['verification_basis'] = (
            'response_body' if body_verified else 'lambda_log_marker'
        )
    if verified:
        cache.set(APIGW_LAMBDA_REQUEST_CACHE_KEY, _clean_response(response_payload), timeout=3600)
    verification = (
        _verify_apigw_lambda_request()
        if verified
        else {
            'status': 'failed',
            'message': (
                'The API Gateway request did not return the expected Lambda echo response '
                'or Lambda log marker.'
            ),
            'resource': _clean_response({
                **response_payload,
                'log_verification': log_verification,
            }),
        }
    )
    return _apigw_step_result(
        'send-request',
        command,
        response_payload,
        verification,
        started,
    )


def _reset_apigw_lambda_request() -> dict[str, Any]:
    command = (
        'aws apigatewayv2 delete-api --api-id <api-id>\n'
        f'aws lambda remove-permission --function-name {LAMBDA_FUNCTION_NAME} '
        f'--statement-id {APIGW_LAMBDA_PERMISSION_SID}\n'
        f'aws lambda delete-function --function-name {LAMBDA_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_ROLE_NAME} '
        f'--policy-name {LAMBDA_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_ROLE_NAME}'
    )
    started = time.perf_counter()
    api = _find_apigw_lambda_api()
    api_id = (api or {}).get('ApiId')
    deleted_api = False
    if api_id:
        deleted_api = _ignore_missing_codes(
            lambda: _apigatewayv2_client().delete_api(ApiId=api_id),
            {'NotFoundException'},
        )
    removed_permission = _ignore_missing_codes(
        lambda: _lambda_client().remove_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId=APIGW_LAMBDA_PERMISSION_SID,
        ),
        {'ResourceNotFoundException'},
    )
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
    cache.delete_many([
        APIGW_LAMBDA_REQUEST_CACHE_KEY,
        APIGW_LAMBDA_API_ID_CACHE_KEY,
        APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY,
        APIGW_LAMBDA_ROUTE_ID_CACHE_KEY,
        LAMBDA_INVOKE_CACHE_KEY,
        LAMBDA_LOGS_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_api': deleted_api,
        'removed_permission': removed_permission,
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
    }

    return {
        'service': 'apigateway',
        'lab': 'lambda-request',
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
            'message': 'The HTTP API, Lambda permission, function, log group, role policy, role, and recorded request state were removed.',
        },
    }


LABS = [APIGW_LAMBDA_REQUEST_LAB, rest_api_developer.LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'apigateway' and lab_key == 'rest-api-developer':
        from .apigateway_developer_labs import run_step as developer_run_step
        return developer_run_step(step_key)
    if service_key == 'apigateway' and lab_key == 'lambda-request':
        runners = {
            'create-role': lambda: _run_lambda_create_role('lambda-request', 'apigateway'),
            'put-role-policy': lambda: _run_lambda_put_role_policy('lambda-request', 'apigateway'),
            'create-function': lambda: _run_lambda_create_function('lambda-request', 'apigateway'),
            'create-api': _run_apigw_create_api,
            'create-integration': _run_apigw_create_integration,
            'create-route': _run_apigw_create_route,
            'create-stage': _run_apigw_create_stage,
            'add-lambda-permission': _run_apigw_add_lambda_permission,
            'send-request': _run_apigw_send_request,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'apigateway' and lab_key == 'rest-api-developer':
        from .apigateway_developer_labs import status as developer_status
        return developer_status()
    if service_key == 'apigateway' and lab_key == 'lambda-request':
        role_verification = _verify_lambda_role()
        policy_verification = _verify_lambda_role_policy()
        function_verification = _verify_lambda_function()
        api_verification = _verify_apigw_lambda_api()
        integration_verification = _verify_apigw_lambda_integration()
        route_verification = _verify_apigw_lambda_route()
        stage_verification = _verify_apigw_lambda_stage()
        permission_verification = _verify_apigw_lambda_permission()
        request_verification = _verify_apigw_lambda_request()
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        api_verified = api_verification.get('status') == 'passed'
        integration_verified = integration_verification.get('status') == 'passed'
        route_verified = route_verification.get('status') == 'passed'
        stage_verified = stage_verification.get('status') == 'passed'
        permission_verified = permission_verification.get('status') == 'passed'
        request_verified = request_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                role_verified
                and policy_verified
                and function_verified
                and api_verified
                and integration_verified
                and route_verified
                and stage_verified
                and permission_verified
                and request_verified
            ),
            'steps': {
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'create-api': {'verified': api_verified, 'verification': api_verification if api_verified else None},
                'create-integration': {'verified': integration_verified, 'verification': integration_verification if integration_verified else None},
                'create-route': {'verified': route_verified, 'verification': route_verification if route_verified else None},
                'create-stage': {'verified': stage_verified, 'verification': stage_verification if stage_verified else None},
                'add-lambda-permission': {'verified': permission_verified, 'verification': permission_verification if permission_verified else None},
                'send-request': {'verified': request_verified, 'verification': request_verification if request_verified else None},
            },
        }

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'apigateway' and lab_key == 'rest-api-developer':
        from .apigateway_developer_labs import reset as developer_reset
        return developer_reset()
    if service_key == 'apigateway' and lab_key == 'lambda-request':
        return _reset_apigw_lambda_request()

    raise ValueError('Lab reset not found')
