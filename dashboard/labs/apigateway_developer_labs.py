"""API Gateway for developers lab: a REST API with a Lambda proxy backend tested with a sample event, dev and prod
stages with stage variables, a Lambda TOKEN authorizer that passes a tenant to the backend, a request validator with a
JSON schema model in front of a MOCK integration with mapping templates, a Cognito user pool authorizer, and an API key
with a usage plan.

Floci 2.3.0 serves REST API requests for real: Lambda proxy integrations, both authorizers (401 and 403 included),
request validation, MOCK integrations with mapping templates, stage variables in the Lambda event, and required API
keys. What it does not do: every stage serves the latest configuration rather than the deployment it points at,
`${stageVariables.name}` in an integration URI is not substituted, usage plans enforce which stages a key works on
but drop throttling and quotas (and read back with only their name), and canary settings are dropped, so the lab
checks those itself.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'apigateway'
LAB_KEY = 'rest-api-developer'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

API_NAME = 'lab-apidev-api'
RUNTIME = 'python3.12'
ROLE = 'lab-apidev-lambda-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE}'
ROLE_POLICY = 'lab-apidev-logs'
BACKEND = 'lab-apidev-orders'
AUTHORIZER_FUNCTION = 'lab-apidev-authorizer'
BACKEND_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{BACKEND}'
AUTHORIZER_FUNCTION_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{AUTHORIZER_FUNCTION}'
PERMISSION_SID = 'lab-apidev-invoke'

ORDERS_PATH, PROFILE_PATH = 'orders', 'profile'
DEV, PROD = 'dev', 'prod'
STAGE_VARIABLES = {
    DEV: {'env': 'dev', 'ordersTable': 'orders-dev'},
    PROD: {'env': 'prod', 'ordersTable': 'orders-prod'},
}
TOKEN_AUTHORIZER = 'lab-apidev-token'
TOKEN_TTL = 300
ALLOW_TOKEN, DENY_TOKEN, BAD_TOKEN = 'Bearer allow-acme', 'Bearer deny', 'not-a-bearer-token'
TENANT = 'acme'

MODEL = 'NewOrder'
VALIDATOR = 'lab-apidev-body'
ORDER_SCHEMA = {
    '$schema': 'http://json-schema.org/draft-04/schema#',
    'title': MODEL,
    'type': 'object',
    'required': ['sku', 'quantity'],
    'properties': {'sku': {'type': 'string', 'pattern': '^SKU-[0-9]+$'},
                   'quantity': {'type': 'integer', 'minimum': 1, 'maximum': 100}},
}
VALID_ORDER = {'sku': 'SKU-1001', 'quantity': 3}
INVALID_ORDER = {'sku': 'SKU-1001'}
MOCK_RESPONSE = ('{"orderId": "$context.requestId", "sku": "$input.path(\'$.sku\')", '
                 '"quantity": $input.path(\'$.quantity\'), "status": "ACCEPTED"}')

USER_POOL = 'lab-apidev-users'
APP_CLIENT = 'lab-apidev-web'
USERNAME = 'dev-tester'
USER_EMAIL = 'dev-tester@example.com'
PASSWORD = 'Lab-Passw0rd!'
COGNITO_AUTHORIZER = 'lab-apidev-cognito'
PROFILE_RESPONSE = '{"sub": "$context.authorizer.claims.sub", "email": "$context.authorizer.claims.email"}'

API_KEY = 'lab-apidev-partner'
USAGE_PLAN = 'lab-apidev-partners'
THROTTLE = {'rateLimit': 10.0, 'burstLimit': 20}
QUOTA = {'limit': 5, 'period': 'DAY'}

LAMBDA_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
LOGS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
                   'Resource': '*'}],
}

BACKEND_SOURCE = '''import json


def handler(event, context):
    stage_variables = event.get("stageVariables") or {}
    authorizer = (event.get("requestContext") or {}).get("authorizer") or {}
    body = {
        "env": stage_variables.get("env"),
        "table": stage_variables.get("ordersTable"),
        "tenant": authorizer.get("tenant"),
        "principal": authorizer.get("principalId"),
        "method": event.get("httpMethod"),
        "path": event.get("path"),
    }
    # A proxy integration must return statusCode, headers, and a string body, or API Gateway answers 502.
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}
'''

AUTHORIZER_SOURCE = '''def handler(event, context):
    token = (event.get("authorizationToken") or "").strip()
    if not token.startswith("Bearer "):
        raise Exception("Unauthorized")  # API Gateway answers 401
    value = token[len("Bearer "):]
    if value.startswith("allow-"):
        effect, tenant = "Allow", value[len("allow-"):]
    elif value == "deny":
        effect, tenant = "Deny", ""
    else:
        raise Exception("Unauthorized")
    # Allow every method of the API, so a cached policy also covers the other routes.
    arn = event["methodArn"].split("/")
    resource = arn[0] + "/" + arn[1] + "/*"
    return {
        "principalId": "user-" + (tenant or "denied"),
        "policyDocument": {"Version": "2012-10-17", "Statement": [
            {"Action": "execute-api:Invoke", "Effect": effect, "Resource": resource}]},
        "context": {"tenant": tenant},
    }
'''

TEST_EVENT = {
    'resource': '/orders', 'path': '/orders', 'httpMethod': 'GET',
    'headers': {'Authorization': ALLOW_TOKEN}, 'queryStringParameters': None,
    'stageVariables': STAGE_VARIABLES[DEV],
    'requestContext': {'stage': DEV, 'authorizer': {'principalId': f'user-{TENANT}', 'tenant': TENANT}},
    'body': None, 'isBase64Encoded': False,
}

BACKEND_COMMAND = (
    f'aws iam create-role --role-name {ROLE} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws lambda create-function --function-name {BACKEND} --runtime {RUNTIME} --handler orders.handler '
    f'--role {ROLE_ARN} --zip-file fileb://orders.zip\n'
    f'aws lambda invoke --function-name {BACKEND} --payload file://events/get-orders.json out.json   # test event'
)
DEV_STAGE_COMMAND = (
    f'aws apigateway create-rest-api --name {API_NAME} --endpoint-configuration types=REGIONAL\n'
    f'aws apigateway create-resource --rest-api-id <api-id> --parent-id <root-id> --path-part {ORDERS_PATH}\n'
    'aws apigateway put-method --rest-api-id <api-id> --resource-id <orders-id> --http-method GET --authorization-type NONE\n'
    'aws apigateway put-integration --rest-api-id <api-id> --resource-id <orders-id> --http-method GET '
    f'--type AWS_PROXY --integration-http-method POST --uri arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{BACKEND_ARN}/invocations\n'
    f'aws lambda add-permission --function-name {BACKEND} --statement-id {PERMISSION_SID} --action lambda:InvokeFunction '
    f'--principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:{REGION}:{ACCOUNT}:<api-id>/*/*/*"\n'
    f'aws apigateway create-deployment --rest-api-id <api-id> --stage-name {DEV} --variables env=dev,ordersTable=orders-dev\n'
    f'curl https://<api-id>.execute-api.{REGION}.amazonaws.com/{DEV}/{ORDERS_PATH}'
)
AUTHORIZER_COMMAND = (
    f'aws lambda create-function --function-name {AUTHORIZER_FUNCTION} --runtime {RUNTIME} --handler authorizer.handler ...\n'
    f'aws apigateway create-authorizer --rest-api-id <api-id> --name {TOKEN_AUTHORIZER} --type TOKEN '
    f'--authorizer-uri <authorizer-function-invocation-uri> --identity-source method.request.header.Authorization '
    f'--authorizer-result-ttl-in-seconds {TOKEN_TTL}\n'
    'aws apigateway update-method --rest-api-id <api-id> --resource-id <orders-id> --http-method GET '
    '--patch-operations op=replace,path=/authorizationType,value=CUSTOM op=replace,path=/authorizerId,value=<authorizer-id>\n'
    f'aws apigateway create-deployment --rest-api-id <api-id> --stage-name {DEV}\n'
    f'curl -H "Authorization: {ALLOW_TOKEN}" https://<api-id>.execute-api.{REGION}.amazonaws.com/{DEV}/{ORDERS_PATH}'
)
VALIDATION_COMMAND = (
    f'aws apigateway create-model --rest-api-id <api-id> --name {MODEL} --content-type application/json --schema file://new-order.json\n'
    f'aws apigateway create-request-validator --rest-api-id <api-id> --name {VALIDATOR} --validate-request-body\n'
    'aws apigateway put-method --rest-api-id <api-id> --resource-id <orders-id> --http-method POST --authorization-type NONE '
    f'--request-validator-id <validator-id> --request-models application/json={MODEL}\n'
    'aws apigateway put-integration ... --http-method POST --type MOCK --request-templates \'{"application/json": "{\\"statusCode\\": 201}"}\'\n'
    'aws apigateway put-integration-response ... --status-code 201 --response-templates file://accepted.vtl\n'
    f'aws apigateway create-deployment --rest-api-id <api-id> --stage-name {DEV}'
)
COGNITO_COMMAND = (
    f'aws cognito-idp create-user-pool --pool-name {USER_POOL}\n'
    f'aws cognito-idp create-user-pool-client --user-pool-id <pool-id> --client-name {APP_CLIENT} '
    '--explicit-auth-flows ALLOW_USER_PASSWORD_AUTH ALLOW_REFRESH_TOKEN_AUTH\n'
    f'aws apigateway create-authorizer --rest-api-id <api-id> --name {COGNITO_AUTHORIZER} --type COGNITO_USER_POOLS '
    '--provider-arns <pool-arn> --identity-source method.request.header.Authorization\n'
    f'aws cognito-idp initiate-auth --client-id <client-id> --auth-flow USER_PASSWORD_AUTH '
    f'--auth-parameters USERNAME={USERNAME},PASSWORD=...\n'
    f'curl -H "Authorization: <id-token>" https://<api-id>.execute-api.{REGION}.amazonaws.com/{DEV}/{PROFILE_PATH}'
)
PROD_STAGE_COMMAND = (
    f'aws apigateway create-deployment --rest-api-id <api-id> --description "Release 1"\n'
    f'aws apigateway create-stage --rest-api-id <api-id> --stage-name {PROD} --deployment-id <deployment-id> '
    '--variables env=prod,ordersTable=orders-prod\n'
    f'curl -H "Authorization: {ALLOW_TOKEN}" https://<api-id>.execute-api.{REGION}.amazonaws.com/{PROD}/{ORDERS_PATH}'
)
API_KEY_COMMAND = (
    'aws apigateway update-method --rest-api-id <api-id> --resource-id <orders-id> --http-method POST '
    '--patch-operations op=replace,path=/apiKeyRequired,value=true\n'
    f'aws apigateway create-api-key --name {API_KEY} --enabled\n'
    f'aws apigateway create-usage-plan --name {USAGE_PLAN} --api-stages apiId=<api-id>,stage={PROD} '
    f'--throttle burstLimit={THROTTLE["burstLimit"]},rateLimit={THROTTLE["rateLimit"]:g} '
    f'--quota limit={QUOTA["limit"]},period={QUOTA["period"]}\n'
    'aws apigateway create-usage-plan-key --usage-plan-id <plan-id> --key-id <key-id> --key-type API_KEY\n'
    f'aws apigateway create-deployment --rest-api-id <api-id> --stage-name {PROD}\n'
    f'curl -X POST -H "x-api-key: <key>" -d \'{json.dumps(VALID_ORDER)}\' https://<api-id>.execute-api.{REGION}.amazonaws.com/{PROD}/{ORDERS_PATH}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Build a REST API with stages, Lambda and Cognito authorizers, request validation, and API keys',
    'description': (
        'Test a Lambda proxy backend with a sample event, publish a REST API to dev and prod stages with stage '
        'variables, protect it with a Lambda TOKEN authorizer and a Cognito user pool authorizer, reject bad requests '
        'with a JSON schema validator in front of a MOCK integration, and require an API key with a usage plan.'
    ),
    'steps': [
        {
            'key': 'backend-function',
            'title': 'Create the orders backend and test it with a sample event',
            'command': BACKEND_COMMAND,
            'explanation': (
                'With a Lambda proxy integration, API Gateway passes the whole request (headers, path, stage '
                'variables, authorizer context) as the event, and the function must return statusCode, headers, and '
                'a string body, or the caller gets a 502. Testing with a saved sample event, the way sam local invoke '
                '-e does, catches mistakes before the API exists. How Floci emulates this: the function runs for real '
                f'in a {RUNTIME} container.'
            ),
        },
        {
            'key': 'dev-stage',
            'title': f'Create the REST API and deploy it to a {DEV} stage',
            'command': DEV_STAGE_COMMAND,
            'explanation': (
                'A REST API is a tree of resources and methods; each method has an integration. Changes are not live '
                'until you create a deployment (a snapshot) and point a stage at it. Stage variables are per-stage '
                'settings such as a table name; the backend reads them from event.stageVariables, and an integration '
                'URI can reference them, for example a Lambda alias ${stageVariables.alias}. The resource-based '
                'permission lets API Gateway invoke the function. How Floci emulates this: requests are served for '
                'real and stage variables reach the event; Floci serves the latest configuration on every stage '
                'instead of the deployed snapshot, and does not substitute stage variables in integration URIs.'
            ),
        },
        {
            'key': 'lambda-authorizer',
            'title': 'Protect GET /orders with a Lambda TOKEN authorizer',
            'command': AUTHORIZER_COMMAND,
            'explanation': (
                'A Lambda authorizer receives the bearer token (TOKEN type) or the whole request (REQUEST type) and '
                'returns an IAM policy plus a context map that reaches the backend, here the tenant. Raising '
                '"Unauthorized" returns 401; a Deny policy returns 403. Results are cached by token for the TTL '
                f'({TOKEN_TTL} seconds here), so the policy allows every method of the API rather than only the '
                'method that was called. How Floci emulates this: the authorizer runs for real and its decision and '
                'context are enforced.'
            ),
        },
        {
            'key': 'request-validation',
            'title': 'Validate POST /orders with a model before a MOCK integration',
            'command': VALIDATION_COMMAND,
            'explanation': (
                'A request validator checks the body against a JSON schema model (and required parameters) before '
                'any integration runs, so bad requests get a 400 without costing a Lambda call. A MOCK integration '
                'answers from API Gateway itself, useful for stubbing an API before the backend exists; mapping '
                'templates (VTL) transform requests and responses, and integration responses can override status '
                'codes. How Floci emulates this: validation, the mock, and the templates run for real.'
            ),
        },
        {
            'key': 'cognito-authorizer',
            'title': 'Protect GET /profile with a Cognito user pool authorizer',
            'command': COGNITO_COMMAND,
            'explanation': (
                'A Cognito user pool authorizer validates a JWT from the user pool with no code: send the ID token in '
                'the Authorization header (an access token works when the method lists OAuth scopes). The token\'s '
                'claims are available to mapping templates and the backend as $context.authorizer.claims. Identity '
                'pools are different: they exchange tokens for temporary AWS credentials. How Floci emulates this: '
                'sign-in and the authorizer are real.'
            ),
        },
        {
            'key': 'prod-stage',
            'title': f'Promote the deployment to a {PROD} stage with its own variables',
            'command': PROD_STAGE_COMMAND,
            'explanation': (
                f'A {PROD} stage points at a tested deployment and has its own variables, throttling, logging, and '
                'caching. Promoting means pointing the stage at a new deployment; a canary setting can send a share of '
                'the stage\'s traffic to the new deployment first. Custom domain names map paths to stages, for '
                'example api.example.com/v1 to prod. How Floci emulates this: both stages answer with their own '
                'variables, but Floci serves the latest configuration everywhere and drops canary settings, so the '
                'step checks which deployment each stage points at.'
            ),
        },
        {
            'key': 'api-key-usage-plan',
            'title': 'Require an API key on POST /orders and meter it with a usage plan',
            'command': API_KEY_COMMAND,
            'explanation': (
                'API keys identify callers such as partners; they are not a security control on their own. A usage '
                'plan attaches keys to stages with throttling (steady rate and burst, answered with 429) and a quota '
                'per day, week, or month. How Floci emulates this: a method that requires a key refuses calls without '
                'one (403), and a key only works on stages its usage plan covers, so the same key gets 403 on dev. '
                'Throttling and quotas are dropped and the plan reads back with only its name, so the step checks the '
                'plan it sent and shows that calls past the quota still succeed.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


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


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _zip(name: str, source: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, source)
    return buffer.getvalue()


def _invoke_uri(function_arn: str) -> str:
    return f'arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/{function_arn}/invocations'


def _url(api_id: str, stage: str, path: str) -> str:
    endpoint = FlociClientFactory().endpoint_url.rstrip('/')
    return f'{endpoint}/restapis/{api_id}/{stage}/_user_request_/{path}'


def _call(api_id: str, stage: str, path: str, method: str = 'GET', headers: dict[str, str] | None = None,
          body: Any = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request_headers = {'Content-Type': 'application/json', **(headers or {})}
    request = Request(_url(api_id, stage, path), data=data, headers=request_headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            status, text = response.status, response.read().decode(errors='replace')
    except HTTPError as exc:
        status, text = exc.code, exc.read().decode(errors='replace')
    except URLError as exc:
        return {'Status': None, 'Error': str(exc.reason)}
    try:
        parsed: Any = json.loads(text)
    except ValueError:
        parsed = text[:300]
    return {'Status': status, 'Body': parsed}


# ── Lookups ──────────────────────────────────────────────────────────────────

def _role() -> dict[str, Any] | None:
    try:
        return _client('iam').get_role(RoleName=ROLE)['Role']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise


def _function(name: str) -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function(FunctionName=name)['Configuration']
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _ensure_function(name: str, filename: str, source: str, description: str) -> None:
    lam = _client('lambda')
    handler = f'{filename.removesuffix(".py")}.handler'
    if _function(name):
        lam.update_function_code(FunctionName=name, ZipFile=_zip(filename, source))
    else:
        lam.create_function(FunctionName=name, Runtime=RUNTIME, Handler=handler, Role=ROLE_ARN, Timeout=10,
                            Code={'ZipFile': _zip(filename, source)}, Description=description,
                            Tags={'Lab': LAB_KEY})

    def ready() -> bool:
        function = _function(name) or {}
        return function.get('State', 'Active') == 'Active' and function.get('LastUpdateStatus', 'Successful') != 'InProgress'
    _wait(ready, timeout=60)


def _allow_api_gateway(name: str, api_id: str) -> None:
    lam = _client('lambda')
    try:
        lam.remove_permission(FunctionName=name, StatementId=PERMISSION_SID)
    except ClientError as exc:
        if _code(exc) != 'ResourceNotFoundException':
            raise
    lam.add_permission(FunctionName=name, StatementId=PERMISSION_SID, Action='lambda:InvokeFunction',
                       Principal='apigateway.amazonaws.com',
                       SourceArn=f'arn:aws:execute-api:{REGION}:{ACCOUNT}:{api_id}/*')


def _api() -> dict[str, Any] | None:
    apis = [item for item in _client('apigateway').get_rest_apis(limit=500).get('items', []) if item.get('name') == API_NAME]
    return apis[0] if apis else None


def _api_id() -> str | None:
    return (_api() or {}).get('id')


def _resources(api_id: str) -> dict[str, dict[str, Any]]:
    items = _client('apigateway').get_resources(restApiId=api_id, limit=500, embed=['methods']).get('items', [])
    return {item.get('path'): item for item in items}


def _resource_id(api_id: str, path: str) -> str | None:
    return (_resources(api_id).get(path) or {}).get('id')


def _ensure_resource(api_id: str, path_part: str) -> str:
    existing = _resource_id(api_id, f'/{path_part}')
    if existing:
        return existing
    root = _resource_id(api_id, '/')
    return _client('apigateway').create_resource(restApiId=api_id, parentId=root, pathPart=path_part)['id']


def _method(api_id: str, resource_id: str | None, http_method: str) -> dict[str, Any] | None:
    if not resource_id:
        return None
    try:
        return _client('apigateway').get_method(restApiId=api_id, resourceId=resource_id, httpMethod=http_method)
    except ClientError as exc:
        if _code(exc) == 'NotFoundException':
            return None
        raise


def _reset_method(api_id: str, resource_id: str, http_method: str, **kwargs: Any) -> None:
    apigateway = _client('apigateway')
    if _method(api_id, resource_id, http_method):
        apigateway.delete_method(restApiId=api_id, resourceId=resource_id, httpMethod=http_method)
    apigateway.put_method(restApiId=api_id, resourceId=resource_id, httpMethod=http_method, **kwargs)


def _authorizer(api_id: str, name: str) -> dict[str, Any] | None:
    items = _client('apigateway').get_authorizers(restApiId=api_id, limit=500).get('items', [])
    found = [item for item in items if item.get('name') == name]
    return found[0] if found else None


def _stage(api_id: str, stage: str) -> dict[str, Any] | None:
    try:
        return _client('apigateway').get_stage(restApiId=api_id, stageName=stage)
    except ClientError as exc:
        if _code(exc) == 'NotFoundException':
            return None
        raise


def _deploy(api_id: str, stage: str, description: str) -> str:
    variables = STAGE_VARIABLES[stage]
    return _client('apigateway').create_deployment(restApiId=api_id, stageName=stage, description=description,
                                                   variables=variables)['id']


def _user_pool() -> dict[str, Any] | None:
    pools = [item for item in _client('cognito-idp').list_user_pools(MaxResults=60).get('UserPools', [])
             if item.get('Name') == USER_POOL]
    if not pools:
        return None
    return _client('cognito-idp').describe_user_pool(UserPoolId=pools[0]['Id'])['UserPool']


def _app_client(pool_id: str) -> dict[str, Any] | None:
    clients = [item for item in _client('cognito-idp').list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)
               .get('UserPoolClients', []) if item.get('ClientName') == APP_CLIENT]
    return clients[0] if clients else None


def _id_token(pool_id: str, client_id: str) -> str:
    result = _client('cognito-idp').initiate_auth(
        ClientId=client_id, AuthFlow='USER_PASSWORD_AUTH',
        AuthParameters={'USERNAME': USERNAME, 'PASSWORD': PASSWORD})['AuthenticationResult']
    return result['IdToken']


def _api_key() -> dict[str, Any] | None:
    keys = [item for item in _client('apigateway').get_api_keys(nameQuery=API_KEY, includeValues=True, limit=500)
            .get('items', []) if item.get('name') == API_KEY]
    return keys[0] if keys else None


def _usage_plan() -> dict[str, Any] | None:
    plans = [item for item in _client('apigateway').get_usage_plans(limit=500).get('items', [])
             if item.get('name') == USAGE_PLAN]
    return plans[0] if plans else None


def _plan_keys(plan_id: str) -> list[str]:
    return [item.get('id') for item in _client('apigateway').get_usage_plan_keys(usagePlanId=plan_id, limit=500)
            .get('items', [])]


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_backend() -> tuple[bool, str]:
    function = _function(BACKEND)
    if not function or not _role():
        return False, f'Function {BACKEND} and role {ROLE} must exist.'
    if function.get('Role') != ROLE_ARN or function.get('Runtime') != RUNTIME:
        return False, f'{BACKEND} must run on {RUNTIME} as {ROLE}.'
    marker = _marked('backend-function')
    if not marker:
        return False, f'Invoke {BACKEND} with the sample event.'
    return True, marker['message']


def _check_dev_stage() -> tuple[bool, str]:
    api_id = _api_id()
    if not api_id:
        return False, f'REST API {API_NAME} was not found.'
    orders = _resource_id(api_id, f'/{ORDERS_PATH}')
    integration = (_method(api_id, orders, 'GET') or {}).get('methodIntegration') or {}
    if integration.get('type') != 'AWS_PROXY' or BACKEND_ARN not in (integration.get('uri') or ''):
        return False, f'GET /{ORDERS_PATH} must be a Lambda proxy integration with {BACKEND}.'
    stage = _stage(api_id, DEV)
    if not stage or (stage.get('variables') or {}) != STAGE_VARIABLES[DEV]:
        return False, f'Stage {DEV} must exist with the variables {STAGE_VARIABLES[DEV]}.'
    marker = _marked('dev-stage')
    if not marker:
        return False, f'Call GET /{ORDERS_PATH} on {DEV}.'
    return True, marker['message']


def _check_lambda_authorizer() -> tuple[bool, str]:
    api_id = _api_id()
    if not api_id:
        return False, f'REST API {API_NAME} was not found.'
    authorizer = _authorizer(api_id, TOKEN_AUTHORIZER)
    if not authorizer or authorizer.get('type') != 'TOKEN' or AUTHORIZER_FUNCTION_ARN not in (authorizer.get('authorizerUri') or ''):
        return False, f'{TOKEN_AUTHORIZER} must be a TOKEN authorizer that calls {AUTHORIZER_FUNCTION}.'
    if authorizer.get('identitySource') != 'method.request.header.Authorization':
        return False, f'{TOKEN_AUTHORIZER} must read the Authorization header.'
    method = _method(api_id, _resource_id(api_id, f'/{ORDERS_PATH}'), 'GET') or {}
    if method.get('authorizationType') != 'CUSTOM' or method.get('authorizerId') != authorizer.get('id'):
        return False, f'GET /{ORDERS_PATH} must use {TOKEN_AUTHORIZER}.'
    marker = _marked('lambda-authorizer')
    if not marker:
        return False, 'Call the API with no token, a denied token, and an allowed token.'
    return True, marker['message']


def _check_validation() -> tuple[bool, str]:
    api_id = _api_id()
    if not api_id:
        return False, f'REST API {API_NAME} was not found.'
    validators = [item for item in _client('apigateway').get_request_validators(restApiId=api_id).get('items', [])
                  if item.get('name') == VALIDATOR]
    if not validators or not validators[0].get('validateRequestBody'):
        return False, f'Request validator {VALIDATOR} must validate the body.'
    method = _method(api_id, _resource_id(api_id, f'/{ORDERS_PATH}'), 'POST') or {}
    if method.get('requestValidatorId') != validators[0].get('id') or (method.get('requestModels') or {}).get('application/json') != MODEL:
        return False, f'POST /{ORDERS_PATH} must validate application/json bodies against {MODEL}.'
    if ((method.get('methodIntegration') or {}).get('type')) != 'MOCK':
        return False, f'POST /{ORDERS_PATH} must use a MOCK integration.'
    marker = _marked('request-validation')
    if not marker:
        return False, 'Send an invalid and a valid order.'
    return True, marker['message']


def _check_cognito() -> tuple[bool, str]:
    api_id, pool = _api_id(), _user_pool()
    if not api_id or not pool:
        return False, f'REST API {API_NAME} and user pool {USER_POOL} must exist.'
    authorizer = _authorizer(api_id, COGNITO_AUTHORIZER)
    if not authorizer or authorizer.get('type') != 'COGNITO_USER_POOLS' or not any(
            pool['Id'] in arn for arn in authorizer.get('providerARNs') or []):
        return False, f'{COGNITO_AUTHORIZER} must be a Cognito authorizer for {USER_POOL}.'
    method = _method(api_id, _resource_id(api_id, f'/{PROFILE_PATH}'), 'GET') or {}
    if method.get('authorizationType') != 'COGNITO_USER_POOLS' or method.get('authorizerId') != authorizer.get('id'):
        return False, f'GET /{PROFILE_PATH} must use {COGNITO_AUTHORIZER}.'
    marker = _marked('cognito-authorizer')
    if not marker:
        return False, f'Call GET /{PROFILE_PATH} with and without an ID token.'
    return True, marker['message']


def _check_prod_stage() -> tuple[bool, str]:
    api_id = _api_id()
    if not api_id:
        return False, f'REST API {API_NAME} was not found.'
    stage = _stage(api_id, PROD)
    if not stage or (stage.get('variables') or {}) != STAGE_VARIABLES[PROD]:
        return False, f'Stage {PROD} must exist with the variables {STAGE_VARIABLES[PROD]}.'
    deployments = {item.get('id') for item in _client('apigateway').get_deployments(restApiId=api_id, limit=500).get('items', [])}
    if stage.get('deploymentId') not in deployments:
        return False, f'Stage {PROD} must point at a deployment of {API_NAME}.'
    marker = _marked('prod-stage')
    if not marker:
        return False, f'Call GET /{ORDERS_PATH} on {PROD}.'
    return True, marker['message']


def _sent_plan() -> dict[str, Any]:
    return (_marked('api-key-usage-plan') or {}).get('SentPlan') or {}


def _check_api_key() -> tuple[bool, str]:
    api_id = _api_id()
    if not api_id:
        return False, f'REST API {API_NAME} was not found.'
    method = _method(api_id, _resource_id(api_id, f'/{ORDERS_PATH}'), 'POST') or {}
    if not method.get('apiKeyRequired'):
        return False, f'POST /{ORDERS_PATH} must require an API key.'
    key, plan = _api_key(), _usage_plan()
    if not key or not key.get('enabled'):
        return False, f'API key {API_KEY} must exist and be enabled.'
    if not plan or key['id'] not in _plan_keys(plan['id']):
        return False, f'Usage plan {USAGE_PLAN} must include {API_KEY}.'
    settings = {name: plan.get(name) for name in ('apiStages', 'throttle', 'quota')}
    if not any(settings.values()):
        settings = _sent_plan()  # Floci reads plans back with only their name, so fall back to what the step sent.
    stages = [(item.get('apiId'), item.get('stage')) for item in settings.get('apiStages') or []]
    if (api_id, PROD) not in stages:
        return False, f'{USAGE_PLAN} must apply to the {PROD} stage.'
    throttle, quota = settings.get('throttle') or {}, settings.get('quota') or {}
    if float(throttle.get('rateLimit') or 0) != THROTTLE['rateLimit'] or int(throttle.get('burstLimit') or 0) != THROTTLE['burstLimit']:
        return False, f'{USAGE_PLAN} should throttle at {THROTTLE["rateLimit"]:g} requests per second, bursts of {THROTTLE["burstLimit"]}.'
    if int(quota.get('limit') or 0) != QUOTA['limit'] or quota.get('period') != QUOTA['period']:
        return False, f'{USAGE_PLAN} should allow {QUOTA["limit"]} requests per {QUOTA["period"].lower()}.'
    marker = _marked('api-key-usage-plan')
    if not marker or not marker.get('message'):
        return False, 'Call POST /orders with and without the key.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_backend_function() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    if not _role():
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
                        Description='API backend and authorizer role (API Gateway developer lab)')
    iam.put_role_policy(RoleName=ROLE, PolicyName=ROLE_POLICY, PolicyDocument=json.dumps(LOGS_POLICY))
    _ensure_function(BACKEND, 'orders.py', BACKEND_SOURCE, 'Orders API backend (API Gateway developer lab)')
    response = _client('lambda').invoke(FunctionName=BACKEND, Payload=json.dumps(TEST_EVENT).encode())
    payload = json.loads(response['Payload'].read() or b'null')
    result = {'TestEvent': TEST_EVENT, 'Response': payload, 'FunctionError': response.get('FunctionError')}
    body = {}
    if isinstance(payload, dict) and isinstance(payload.get('body'), str):
        try:
            body = json.loads(payload['body'])
        except ValueError:
            body = {}
    if response.get('FunctionError') or payload.get('statusCode') != 200 or body.get('env') != 'dev' or body.get('tenant') != TENANT:
        return _result('backend-function', BACKEND_COMMAND, result, False,
                       f'{BACKEND} should answer the sample event with statusCode 200, env dev, and tenant {TENANT}.',
                       started)
    message = (f'{BACKEND} answered the sample GET /orders event with a proxy response: statusCode 200, a JSON string '
               f'body with env dev, table orders-dev, and tenant {TENANT}.')
    _marker('backend-function', {'message': message})
    verified, check = _check_backend()
    return _result('backend-function', BACKEND_COMMAND, result, verified, message if verified else check, started)


def step_dev_stage() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_backend()[0]:
        return _result('dev-stage', DEV_STAGE_COMMAND, {}, False, f'Create and test {BACKEND} first.', started)
    apigateway = _client('apigateway')
    api_id = _api_id() or apigateway.create_rest_api(
        name=API_NAME, description='Orders API (API Gateway developer lab)',
        endpointConfiguration={'types': ['REGIONAL']}, tags={'Lab': LAB_KEY})['id']
    orders = _ensure_resource(api_id, ORDERS_PATH)
    _reset_method(api_id, orders, 'GET', authorizationType='NONE')
    apigateway.put_integration(restApiId=api_id, resourceId=orders, httpMethod='GET', type='AWS_PROXY',
                               integrationHttpMethod='POST', uri=_invoke_uri(BACKEND_ARN))
    _allow_api_gateway(BACKEND, api_id)
    deployment = _deploy(api_id, DEV, 'GET /orders')
    call = _call(api_id, DEV, ORDERS_PATH)
    response = {'ApiId': api_id, 'DeploymentId': deployment, 'Stage': _stage(api_id, DEV),
                'InvokeUrl': f'https://{api_id}.execute-api.{REGION}.amazonaws.com/{DEV}/{ORDERS_PATH}',
                'FlociUrl': _url(api_id, DEV, ORDERS_PATH), 'Call': call}
    body = call.get('Body') if isinstance(call.get('Body'), dict) else {}
    if call.get('Status') != 200 or body.get('env') != 'dev' or body.get('table') != 'orders-dev':
        return _result('dev-stage', DEV_STAGE_COMMAND, response, False,
                       f'GET /{ORDERS_PATH} on {DEV} should return 200 with env dev and table orders-dev.', started)
    message = (f'{API_NAME} is deployed to {DEV}; GET /{ORDERS_PATH} reached {BACKEND}, which read env=dev and '
               'ordersTable=orders-dev from the stage variables.')
    _marker('dev-stage', {'message': message})
    verified, check = _check_dev_stage()
    return _result('dev-stage', DEV_STAGE_COMMAND, response, verified, message if verified else check, started)


def step_lambda_authorizer() -> dict[str, Any]:
    started = time.perf_counter()
    api_id = _api_id()
    if not api_id or not _check_dev_stage()[0]:
        return _result('lambda-authorizer', AUTHORIZER_COMMAND, {}, False, f'Deploy the API to {DEV} first.', started)
    apigateway = _client('apigateway')
    _ensure_function(AUTHORIZER_FUNCTION, 'authorizer.py', AUTHORIZER_SOURCE, 'Bearer token authorizer (API Gateway developer lab)')
    _allow_api_gateway(AUTHORIZER_FUNCTION, api_id)
    authorizer = _authorizer(api_id, TOKEN_AUTHORIZER)
    settings = {'authorizerUri': _invoke_uri(AUTHORIZER_FUNCTION_ARN),
                'identitySource': 'method.request.header.Authorization', 'authorizerResultTtlInSeconds': TOKEN_TTL}
    if authorizer:
        authorizer_id = authorizer['id']
        apigateway.update_authorizer(restApiId=api_id, authorizerId=authorizer_id, patchOperations=[
            {'op': 'replace', 'path': f'/{name}', 'value': str(value)} for name, value in settings.items()])
    else:
        authorizer_id = apigateway.create_authorizer(restApiId=api_id, name=TOKEN_AUTHORIZER, type='TOKEN',
                                                     **settings)['id']
    orders = _resource_id(api_id, f'/{ORDERS_PATH}')
    apigateway.update_method(restApiId=api_id, resourceId=orders, httpMethod='GET', patchOperations=[
        {'op': 'replace', 'path': '/authorizationType', 'value': 'CUSTOM'},
        {'op': 'replace', 'path': '/authorizerId', 'value': authorizer_id}])
    _deploy(api_id, DEV, 'Lambda authorizer on GET /orders')
    calls = {
        'NoToken': _call(api_id, DEV, ORDERS_PATH),
        'NotBearer': _call(api_id, DEV, ORDERS_PATH, headers={'Authorization': BAD_TOKEN}),
        'Denied': _call(api_id, DEV, ORDERS_PATH, headers={'Authorization': DENY_TOKEN}),
        'Allowed': _call(api_id, DEV, ORDERS_PATH, headers={'Authorization': ALLOW_TOKEN}),
    }
    response = {'Authorizer': _authorizer(api_id, TOKEN_AUTHORIZER), 'Calls': calls}
    statuses = {name: call.get('Status') for name, call in calls.items()}
    allowed = calls['Allowed'].get('Body') if isinstance(calls['Allowed'].get('Body'), dict) else {}
    if statuses != {'NoToken': 401, 'NotBearer': 401, 'Denied': 403, 'Allowed': 200}:
        return _result('lambda-authorizer', AUTHORIZER_COMMAND, response, False,
                       f'Expected 401 without a token, 401 for a malformed token, 403 when denied, and 200 when '
                       f'allowed; got {statuses}.', started)
    if allowed.get('tenant') != TENANT:
        return _result('lambda-authorizer', AUTHORIZER_COMMAND, response, False,
                       f'The authorizer context should pass tenant {TENANT} to the backend.', started)
    message = (f'{TOKEN_AUTHORIZER} answered 401 with no token or a malformed one, 403 for a denied token, and 200 for '
               f'"{ALLOW_TOKEN}", passing tenant {TENANT} to {BACKEND} in the authorizer context.')
    _marker('lambda-authorizer', {'message': message})
    verified, check = _check_lambda_authorizer()
    return _result('lambda-authorizer', AUTHORIZER_COMMAND, response, verified, message if verified else check, started)


def step_request_validation() -> dict[str, Any]:
    started = time.perf_counter()
    api_id = _api_id()
    if not api_id or not _check_dev_stage()[0]:
        return _result('request-validation', VALIDATION_COMMAND, {}, False, f'Deploy the API to {DEV} first.', started)
    apigateway = _client('apigateway')
    models = {item.get('name') for item in apigateway.get_models(restApiId=api_id, limit=500).get('items', [])}
    if MODEL in models:
        apigateway.update_model(restApiId=api_id, modelName=MODEL, patchOperations=[
            {'op': 'replace', 'path': '/schema', 'value': json.dumps(ORDER_SCHEMA)}])
    else:
        apigateway.create_model(restApiId=api_id, name=MODEL, contentType='application/json',
                                schema=json.dumps(ORDER_SCHEMA), description='A new order')
    validators = [item for item in apigateway.get_request_validators(restApiId=api_id).get('items', [])
                  if item.get('name') == VALIDATOR]
    validator_id = validators[0]['id'] if validators else apigateway.create_request_validator(
        restApiId=api_id, name=VALIDATOR, validateRequestBody=True, validateRequestParameters=False)['id']
    orders = _resource_id(api_id, f'/{ORDERS_PATH}')
    # Rerunning this step puts POST /orders back without the API key requirement that the last step adds.
    _reset_method(api_id, orders, 'POST', authorizationType='NONE', requestValidatorId=validator_id,
                  requestModels={'application/json': MODEL}, apiKeyRequired=False)
    apigateway.put_integration(restApiId=api_id, resourceId=orders, httpMethod='POST', type='MOCK',
                               requestTemplates={'application/json': '{"statusCode": 201}'})
    apigateway.put_method_response(restApiId=api_id, resourceId=orders, httpMethod='POST', statusCode='201',
                                   responseModels={'application/json': 'Empty'})
    apigateway.put_integration_response(restApiId=api_id, resourceId=orders, httpMethod='POST', statusCode='201',
                                        responseTemplates={'application/json': MOCK_RESPONSE})
    _deploy(api_id, DEV, 'Validated POST /orders')
    calls = {'Invalid': _call(api_id, DEV, ORDERS_PATH, 'POST', body=INVALID_ORDER),
             'Valid': _call(api_id, DEV, ORDERS_PATH, 'POST', body=VALID_ORDER)}
    response = {'Model': ORDER_SCHEMA, 'ValidatorId': validator_id, 'ResponseTemplate': MOCK_RESPONSE, 'Calls': calls}
    valid = calls['Valid'].get('Body') if isinstance(calls['Valid'].get('Body'), dict) else {}
    invalid_body = json.dumps(calls['Invalid'].get('Body'))
    if calls['Invalid'].get('Status') != 400:
        return _result('request-validation', VALIDATION_COMMAND, response, False,
                       f'An order without a quantity should get 400, got {calls["Invalid"].get("Status")}.', started)
    if calls['Valid'].get('Status') != 201 or valid.get('sku') != VALID_ORDER['sku'] or valid.get('quantity') != VALID_ORDER['quantity']:
        return _result('request-validation', VALIDATION_COMMAND, response, False,
                       'A valid order should get 201 with its sku and quantity from the mapping template.', started)
    message = (f'The validator rejected an order without a quantity with 400 ({invalid_body[:120]}), and the MOCK '
               f'integration answered a valid order with 201, built by the mapping template from the request '
               f'(sku {valid["sku"]}, quantity {valid["quantity"]}, status {valid.get("status")}).')
    _marker('request-validation', {'message': message})
    verified, check = _check_validation()
    return _result('request-validation', VALIDATION_COMMAND, response, verified, message if verified else check, started)


def step_cognito_authorizer() -> dict[str, Any]:
    started = time.perf_counter()
    api_id = _api_id()
    if not api_id or not _check_dev_stage()[0]:
        return _result('cognito-authorizer', COGNITO_COMMAND, {}, False, f'Deploy the API to {DEV} first.', started)
    cognito, apigateway = _client('cognito-idp'), _client('apigateway')
    pool = _user_pool()
    if not pool:
        cognito.create_user_pool(PoolName=USER_POOL, AutoVerifiedAttributes=['email'],
                                 UsernameAttributes=[], UserPoolTags={'Lab': LAB_KEY})
        pool = _user_pool() or {}
    client = _app_client(pool['Id']) or cognito.create_user_pool_client(
        UserPoolId=pool['Id'], ClientName=APP_CLIENT, GenerateSecret=False,
        ExplicitAuthFlows=['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'])['UserPoolClient']
    try:
        cognito.admin_create_user(UserPoolId=pool['Id'], Username=USERNAME, MessageAction='SUPPRESS',
                                  UserAttributes=[{'Name': 'email', 'Value': USER_EMAIL},
                                                  {'Name': 'email_verified', 'Value': 'true'}])
    except ClientError as exc:
        if _code(exc) != 'UsernameExistsException':
            raise
    cognito.admin_set_user_password(UserPoolId=pool['Id'], Username=USERNAME, Password=PASSWORD, Permanent=True)
    pool_arn = pool.get('Arn') or f'arn:aws:cognito-idp:{REGION}:{ACCOUNT}:userpool/{pool["Id"]}'
    authorizer = _authorizer(api_id, COGNITO_AUTHORIZER)
    authorizer_id = authorizer['id'] if authorizer else apigateway.create_authorizer(
        restApiId=api_id, name=COGNITO_AUTHORIZER, type='COGNITO_USER_POOLS', providerARNs=[pool_arn],
        identitySource='method.request.header.Authorization')['id']
    profile = _ensure_resource(api_id, PROFILE_PATH)
    _reset_method(api_id, profile, 'GET', authorizationType='COGNITO_USER_POOLS', authorizerId=authorizer_id)
    apigateway.put_integration(restApiId=api_id, resourceId=profile, httpMethod='GET', type='MOCK',
                               requestTemplates={'application/json': '{"statusCode": 200}'})
    apigateway.put_method_response(restApiId=api_id, resourceId=profile, httpMethod='GET', statusCode='200')
    apigateway.put_integration_response(restApiId=api_id, resourceId=profile, httpMethod='GET', statusCode='200',
                                        responseTemplates={'application/json': PROFILE_RESPONSE})
    _deploy(api_id, DEV, 'Cognito authorizer on GET /profile')
    token = _id_token(pool['Id'], client['ClientId'])
    calls = {'NoToken': _call(api_id, DEV, PROFILE_PATH),
             'ForgedToken': _call(api_id, DEV, PROFILE_PATH, headers={'Authorization': 'eyJhbGciOiJub25lIn0.e30.'}),
             'IdToken': _call(api_id, DEV, PROFILE_PATH, headers={'Authorization': token})}
    users = cognito.admin_get_user(UserPoolId=pool['Id'], Username=USERNAME)
    sub = next((item['Value'] for item in users.get('UserAttributes', []) if item['Name'] == 'sub'), None)
    response = {'UserPool': {'Id': pool['Id'], 'Arn': pool_arn}, 'AppClient': client.get('ClientId'),
                'IdTokenPreview': token[:24] + '...', 'Calls': calls}
    statuses = {name: call.get('Status') for name, call in calls.items()}
    claims = calls['IdToken'].get('Body') if isinstance(calls['IdToken'].get('Body'), dict) else {}
    if statuses != {'NoToken': 401, 'ForgedToken': 401, 'IdToken': 200}:
        return _result('cognito-authorizer', COGNITO_COMMAND, response, False,
                       f'Expected 401 without a token or with a forged one and 200 with the ID token; got {statuses}.',
                       started)
    if not claims.get('sub') or (sub and claims.get('sub') != sub):
        return _result('cognito-authorizer', COGNITO_COMMAND, response, False,
                       f'GET /{PROFILE_PATH} should return the signed-in user\'s sub claim.', started)
    message = (f'{COGNITO_AUTHORIZER} refused calls with no token or a forged one (401) and accepted {USERNAME}\'s ID '
               f'token; the mapping template returned the token\'s sub and email claims.')
    _marker('cognito-authorizer', {'message': message})
    verified, check = _check_cognito()
    return _result('cognito-authorizer', COGNITO_COMMAND, response, verified, message if verified else check, started)


def step_prod_stage() -> dict[str, Any]:
    started = time.perf_counter()
    api_id = _api_id()
    if not api_id or not (_check_lambda_authorizer()[0] and _check_validation()[0] and _check_cognito()[0]):
        return _result('prod-stage', PROD_STAGE_COMMAND, {}, False,
                       'Add both authorizers and the validator on dev first.', started)
    apigateway = _client('apigateway')
    deployment = apigateway.create_deployment(restApiId=api_id, description='Release 1')['id']
    if _stage(api_id, PROD):
        apigateway.update_stage(restApiId=api_id, stageName=PROD, patchOperations=[
            {'op': 'replace', 'path': '/deploymentId', 'value': deployment}] + [
            {'op': 'replace', 'path': f'/variables/{name}', 'value': value} for name, value in STAGE_VARIABLES[PROD].items()])
    else:
        apigateway.create_stage(restApiId=api_id, stageName=PROD, deploymentId=deployment,
                                variables=STAGE_VARIABLES[PROD], description='Production',
                                tags={'Lab': LAB_KEY})
    calls = {stage: _call(api_id, stage, ORDERS_PATH, headers={'Authorization': ALLOW_TOKEN}) for stage in (DEV, PROD)}
    dev, prod = _stage(api_id, DEV) or {}, _stage(api_id, PROD) or {}
    response = {'Release1DeploymentId': deployment,
                'Stages': {DEV: {'deploymentId': dev.get('deploymentId'), 'variables': dev.get('variables')},
                           PROD: {'deploymentId': prod.get('deploymentId'), 'variables': prod.get('variables'),
                                  'canarySettings': prod.get('canarySettings')}},
                'Calls': calls}
    bodies = {stage: call.get('Body') if isinstance(call.get('Body'), dict) else {} for stage, call in calls.items()}
    if any(call.get('Status') != 200 for call in calls.values()) or bodies[DEV].get('env') != 'dev' or bodies[PROD].get('env') != 'prod':
        return _result('prod-stage', PROD_STAGE_COMMAND, response, False,
                       f'GET /{ORDERS_PATH} should answer env dev on {DEV} and env prod on {PROD}.', started)
    if prod.get('deploymentId') != deployment:
        return _result('prod-stage', PROD_STAGE_COMMAND, response, False,
                       f'{PROD} should point at the release 1 deployment {deployment}.', started)
    message = (f'{PROD} points at deployment {deployment} with its own variables: the same request answered env dev '
               f'and table orders-dev on {DEV}, and env prod and table orders-prod on {PROD}. Floci serves the latest '
               'configuration on every stage, so the step checked the deployment each stage points at.')
    _marker('prod-stage', {'message': message, 'DeploymentId': deployment})
    verified, check = _check_prod_stage()
    return _result('prod-stage', PROD_STAGE_COMMAND, response, verified, message if verified else check, started)


def step_api_key_usage_plan() -> dict[str, Any]:
    started = time.perf_counter()
    api_id = _api_id()
    if not api_id or not _check_prod_stage()[0] or not _check_validation()[0]:
        return _result('api-key-usage-plan', API_KEY_COMMAND, {}, False,
                       f'Create the validated POST method and the {PROD} stage first.', started)
    apigateway = _client('apigateway')
    orders = _resource_id(api_id, f'/{ORDERS_PATH}')
    apigateway.update_method(restApiId=api_id, resourceId=orders, httpMethod='POST', patchOperations=[
        {'op': 'replace', 'path': '/apiKeyRequired', 'value': 'true'}])
    key = _api_key() or apigateway.create_api_key(name=API_KEY, enabled=True, description='Partner key (lab)',
                                                  tags={'Lab': LAB_KEY})
    if not key.get('value'):
        key = _api_key() or key
    sent_plan = {'apiStages': [{'apiId': api_id, 'stage': PROD}], 'throttle': THROTTLE, 'quota': QUOTA}
    plan = _usage_plan()
    if plan:
        try:
            apigateway.update_usage_plan(usagePlanId=plan['id'], patchOperations=[
                {'op': 'replace', 'path': '/throttle/rateLimit', 'value': str(THROTTLE['rateLimit'])},
                {'op': 'replace', 'path': '/throttle/burstLimit', 'value': str(THROTTLE['burstLimit'])},
                {'op': 'replace', 'path': '/quota/limit', 'value': str(QUOTA['limit'])},
                {'op': 'replace', 'path': '/quota/period', 'value': QUOTA['period']}])
        except ClientError as exc:
            if _code(exc) != 'BadRequestException':
                raise
            # Floci rejects these patch paths, so replace the plan instead.
            for key_id in _plan_keys(plan['id']):
                apigateway.delete_usage_plan_key(usagePlanId=plan['id'], keyId=key_id)
            apigateway.delete_usage_plan(usagePlanId=plan['id'])
            plan = None
    if not plan:
        plan = apigateway.create_usage_plan(name=USAGE_PLAN, description='Partner tier (lab)', **sent_plan)
    if key['id'] not in _plan_keys(plan['id']):
        apigateway.create_usage_plan_key(usagePlanId=plan['id'], keyId=key['id'], keyType='API_KEY')
    _marker('api-key-usage-plan', {'SentPlan': sent_plan})
    apigateway.create_deployment(restApiId=api_id, stageName=PROD, description='API key on POST /orders',
                                 variables=STAGE_VARIABLES[PROD])
    no_key = _call(api_id, PROD, ORDERS_PATH, 'POST', body=VALID_ORDER)
    other_stage = _call(api_id, DEV, ORDERS_PATH, 'POST', headers={'x-api-key': key['value']}, body=VALID_ORDER)
    with_key = [_call(api_id, PROD, ORDERS_PATH, 'POST', headers={'x-api-key': key['value']}, body=VALID_ORDER)
                for _ in range(QUOTA['limit'] + 2)]
    stored = _usage_plan() or {}
    response = {'ApiKey': {'id': key['id'], 'name': key.get('name'), 'enabled': key.get('enabled')},
                'StoredPlan': stored, 'SentPlan': sent_plan,
                'Calls': {'NoKey': no_key, 'KeyOnDev': other_stage,
                          'WithKey': [call.get('Status') for call in with_key]}}
    if no_key.get('Status') != 403 or with_key[0].get('Status') != 201:
        return _result('api-key-usage-plan', API_KEY_COMMAND, response, False,
                       f'POST /{ORDERS_PATH} should refuse a call without the key (403) and accept it with the key (201); '
                       f'got {no_key.get("Status")} and {with_key[0].get("Status")}.', started)
    over_quota = [call.get('Status') for call in with_key[QUOTA['limit']:]]
    message = (f'POST /{ORDERS_PATH} on {PROD} refused a call without an API key (403) and accepted {API_KEY} (201). '
               f'{USAGE_PLAN} allows {QUOTA["limit"]} requests a day at {THROTTLE["rateLimit"]:g} per second '
               f'(bursts of {THROTTLE["burstLimit"]}).')
    if other_stage.get('Status') == 403:
        message += f' The same key got 403 on {DEV}, because the plan covers only {PROD}.'
    if all(status == 201 for status in over_quota):
        message += (f' Calls {QUOTA["limit"] + 1} and {QUOTA["limit"] + 2} still succeeded: AWS would answer 429, but '
                    'Floci does not enforce throttling or quotas')
        message += (', and read the plan back with only its name, so the step checked the plan it sent.'
                    if not any(stored.get(name) for name in ('apiStages', 'throttle', 'quota')) else '.')
    elif all(status == 429 for status in over_quota):
        message += f' Calls past the quota got 429, as on AWS.'
    _marker('api-key-usage-plan', {'SentPlan': sent_plan, 'message': message})
    verified, check = _check_api_key()
    return _result('api-key-usage-plan', API_KEY_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'backend-function': step_backend_function,
    'dev-stage': step_dev_stage,
    'lambda-authorizer': step_lambda_authorizer,
    'request-validation': step_request_validation,
    'cognito-authorizer': step_cognito_authorizer,
    'prod-stage': step_prod_stage,
    'api-key-usage-plan': step_api_key_usage_plan,
}


def run_step(step_key: str) -> dict[str, Any]:
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {LAB_KEY}')
    return runner()


# ── Status and reset ─────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status() -> dict[str, Any]:
    checks = {
        'backend-function': lambda: _check_backend()[0],
        'dev-stage': lambda: _check_dev_stage()[0],
        'lambda-authorizer': lambda: _check_lambda_authorizer()[0],
        'request-validation': lambda: _check_validation()[0],
        'cognito-authorizer': lambda: _check_cognito()[0],
        'prod-stage': lambda: _check_prod_stage()[0],
        'api-key-usage-plan': lambda: _check_api_key()[0],
    }
    steps = {}
    for step, check in checks.items():
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
    """Remove the usage plan, API key, REST API, user pool, functions, and role."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code in ('NoSuchEntity', 'ResourceNotFoundException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    apigateway, cognito, lam, iam = _client('apigateway'), _client('cognito-idp'), _client('lambda'), _client('iam')
    try:
        plan, key, api_id, pool = _usage_plan(), _api_key(), _api_id(), _user_pool()
    except (ClientError, BotoCoreError) as exc:
        plan = key = api_id = pool = None
        errors.append(f'lookup: {exc}')
    if plan:
        if key and key['id'] in _plan_keys(plan['id']):
            attempt(f'usage-plan-key:{API_KEY}', lambda: apigateway.delete_usage_plan_key(
                usagePlanId=plan['id'], keyId=key['id']))
        attempt(f'usage-plan:{USAGE_PLAN}', lambda: apigateway.delete_usage_plan(usagePlanId=plan['id']))
    if key:
        attempt(f'api-key:{API_KEY}', lambda: apigateway.delete_api_key(apiKey=key['id']))
    if api_id:
        attempt(f'rest-api:{API_NAME}', lambda: apigateway.delete_rest_api(restApiId=api_id))
    if pool:
        attempt(f'user-pool:{USER_POOL}', lambda: cognito.delete_user_pool(UserPoolId=pool['Id']))
    for name in (BACKEND, AUTHORIZER_FUNCTION):
        attempt(f'function:{name}', lambda name=name: lam.delete_function(FunctionName=name))
    attempt(f'role-policy:{ROLE}', lambda: iam.delete_role_policy(RoleName=ROLE, PolicyName=ROLE_POLICY))
    attempt(f'role:{ROLE}', lambda: iam.delete_role(RoleName=ROLE))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws apigateway delete-usage-plan-key --usage-plan-id <plan-id> --key-id <key-id>\n'
            'aws apigateway delete-usage-plan --usage-plan-id <plan-id>\n'
            'aws apigateway delete-api-key --api-key <key-id>\n'
            'aws apigateway delete-rest-api --rest-api-id <api-id>\n'
            'aws cognito-idp delete-user-pool --user-pool-id <pool-id>\n'
            f'aws lambda delete-function --function-name {BACKEND}\n'
            f'aws lambda delete-function --function-name {AUTHORIZER_FUNCTION}\n'
            f'aws iam delete-role-policy --role-name {ROLE} --policy-name {ROLE_POLICY}\n'
            f'aws iam delete-role --role-name {ROLE}'
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
            else 'The usage plan, API key, REST API, user pool, functions, and role are deleted.',
        },
    }
