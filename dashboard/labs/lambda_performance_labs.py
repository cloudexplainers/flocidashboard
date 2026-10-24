"""Lambda performance and cost lab: memory sizing with a cost per million requests, versions, an alias with a weighted
canary, reserved concurrency that throttles, an IAM-authenticated function URL, and promoting the canary.

Floci 2.3.0 runs every invocation in a real container. It reports the configured memory to the function but does not
scale CPU with it, so the sizing sweep shows flat durations; the lab says so and still teaches the math. Versions,
weighted alias routing, reserved concurrency throttling, and function URLs are real. A function URL with AWS_IAM auth
also answers unsigned requests, and provisioned concurrency requests are routed to the S3 handler, so the lab checks
the auth type itself and explains provisioned concurrency without configuring it.
"""

from __future__ import annotations

import io
import json
import math
import statistics
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config
from botocore.credentials import Credentials
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'lambda'
LAB_KEY = 'performance-cost'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

FUNCTION = 'lab-price-quote'
ROLE_NAME = 'lab-price-quote-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
ROLE_POLICY = 'lab-price-quote-logs'
LOG_GROUP = f'/aws/lambda/{FUNCTION}'
RUNTIME = 'python3.12'
HANDLER = 'quote.handler'
ALIAS = 'live'
INITIAL_MEMORY = 128
TIMEOUT_SECONDS = 10

# Sizing sweep: 1,769 MB is where a function gets one full vCPU on AWS.
MEMORY_SIZES = (128, 512, 1024, 1769)
SWEEP_INVOCATIONS = 3
ROUNDS = 100_000
LATENCY_TARGET_MS = 250
PRICE_PER_GB_SECOND = 0.0000166667   # x86_64, us-east-1
ARM_PRICE_PER_GB_SECOND = 0.0000133334
PRICE_PER_MILLION_REQUESTS = 0.20

V1_RULE = 'standard'
V2_RULE = 'volume-discount'
V1_DESCRIPTION = 'Standard pricing'
V2_DESCRIPTION = 'Volume discount for 10 or more'
CANARY_WEIGHT = 0.2
CANARY_INVOCATIONS = 50
PROMOTE_INVOCATIONS = 10

RESERVED_CONCURRENCY = 5
BURST_INVOCATIONS = 8
HOLD_SECONDS = 3

URL_ORIGIN = 'https://shop.example.com'
URL_PATH = '/quote?sku=SKU-1&quantity=12'

LAMBDA_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
LOGS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
        'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{LOG_GROUP}:*',
    }],
}

QUOTE_SOURCE = '''"""Quote a price. The pricing loop is CPU-bound, so on AWS it runs faster with more memory (and CPU)."""
import hashlib
import json
import os
import time


def quote(sku, quantity, rounds):
    digest = sku.encode()
    for _ in range(rounds):
        digest = hashlib.sha256(digest).digest()
    unit = 10 + digest[0] % 40
    if os.environ["PRICING_RULE"] == "volume-discount" and quantity >= 10:
        unit = round(unit * 0.9, 2)
    return round(unit * quantity, 2)


def handler(event, context):
    started = time.perf_counter()
    url_request = "requestContext" in event
    params = (event.get("queryStringParameters") or {}) if url_request else event
    time.sleep(float(params.get("hold_seconds", 0)))
    sku, quantity = params.get("sku", "SKU-1"), int(params.get("quantity", 1))
    body = {
        "sku": sku,
        "quantity": quantity,
        "total": quote(sku, quantity, int(params.get("rounds", 1000))),
        "pricing_rule": os.environ["PRICING_RULE"],
        "version": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", "$LATEST"),
        "memory_mb": int(context.memory_limit_in_mb),
        "handler_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    if url_request:
        return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps(body)}
    return body
'''

CREATE_COMMAND = (
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} --policy-document file://logs.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime {RUNTIME} --handler {HANDLER} '
    f'--role {ROLE_ARN} --zip-file fileb://quote.zip --memory-size {INITIAL_MEMORY} --timeout {TIMEOUT_SECONDS} '
    f'--architectures x86_64 --environment Variables={{PRICING_RULE={V1_RULE}}}'
)
SIZING_COMMAND = (
    f'for mb in {" ".join(str(size) for size in MEMORY_SIZES)}; do\n'
    f'  aws lambda update-function-configuration --function-name {FUNCTION} --memory-size $mb\n'
    f'  aws lambda wait function-updated --function-name {FUNCTION}\n'
    f'  aws lambda invoke --function-name {FUNCTION} --payload \'{{"rounds":{ROUNDS}}}\' '
    '--cli-binary-format raw-in-base64-out --log-type Tail out.json   # REPORT line: Billed Duration\n'
    'done\n'
    '# cost per 1M requests = $0.20 + memory GB x billed seconds x $0.0000166667 x 1,000,000\n'
    f'aws lambda update-function-configuration --function-name {FUNCTION} --memory-size <cheapest size under '
    f'{LATENCY_TARGET_MS} ms>'
)
VERSION_COMMAND = f'aws lambda publish-version --function-name {FUNCTION} --description "{V1_DESCRIPTION}"'
ALIAS_COMMAND = (
    f'aws lambda create-alias --function-name {FUNCTION} --name {ALIAS} --function-version 1\n'
    f'aws lambda invoke --function-name {FUNCTION}:{ALIAS} --payload \'{{"quantity":12}}\' '
    '--cli-binary-format raw-in-base64-out out.json'
)
CANARY_COMMAND = (
    f'aws lambda update-function-configuration --function-name {FUNCTION} '
    f'--environment Variables={{PRICING_RULE={V2_RULE}}}\n'
    f'aws lambda publish-version --function-name {FUNCTION} --description "{V2_DESCRIPTION}"\n'
    f'aws lambda update-alias --function-name {FUNCTION} --name {ALIAS} --function-version 1 '
    f"--routing-config 'AdditionalVersionWeights={{\"2\"={CANARY_WEIGHT}}}'\n"
    f'# invoke {FUNCTION}:{ALIAS} {CANARY_INVOCATIONS} times and count ExecutedVersion'
)
CONCURRENCY_COMMAND = (
    f'aws lambda put-function-concurrency --function-name {FUNCTION} '
    f'--reserved-concurrent-executions {RESERVED_CONCURRENCY}\n'
    f'# {BURST_INVOCATIONS} parallel invocations that each hold for {HOLD_SECONDS} s\n'
    f'seq {BURST_INVOCATIONS} | xargs -P {BURST_INVOCATIONS} -I{{}} aws lambda invoke --function-name '
    f'{FUNCTION}:{ALIAS} --payload \'{{"hold_seconds":{HOLD_SECONDS}}}\' --cli-binary-format raw-in-base64-out '
    'out-{}.json   # some fail with TooManyRequestsException\n'
    'aws lambda get-account-settings --query AccountLimit'
)
URL_COMMAND = (
    f'aws lambda create-function-url-config --function-name {FUNCTION} --qualifier {ALIAS} --auth-type AWS_IAM '
    f"--cors 'AllowOrigins={URL_ORIGIN},AllowMethods=GET'\n"
    f'curl --aws-sigv4 "aws:amz:{REGION}:lambda" --user "$AWS_ACCESS_KEY_ID:$AWS_SECRET_ACCESS_KEY" '
    f'"<function-url>{URL_PATH.lstrip("/")}"'
)
PROMOTE_COMMAND = (
    f'aws lambda update-alias --function-name {FUNCTION} --name {ALIAS} --function-version 2 '
    "--routing-config 'AdditionalVersionWeights={}'\n"
    f'# invoke {FUNCTION}:{ALIAS} {PROMOTE_INVOCATIONS} times: every call runs version 2'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Tune Lambda performance and cost with memory sizing, aliases, canaries, and concurrency',
    'description': (
        'Size a CPU-bound function from a memory sweep and a cost per million requests, publish versions, send 20% of '
        'an alias to a new version as a canary, cap the function with reserved concurrency and watch it throttle, '
        'serve it from an IAM-authenticated function URL, and promote the canary.'
    ),
    'steps': [
        {
            'key': 'create-function',
            'title': 'Create the price quote function',
            'command': CREATE_COMMAND,
            'explanation': (
                'The function prices an order with a CPU-bound loop, so its speed depends on CPU. Lambda bills each '
                'request plus duration in GB-seconds (memory times billed time, rounded up to the millisecond). The '
                'execution role only writes the function\'s logs. arm64 (Graviton) functions cost about 20% less per '
                'GB-second and are often faster; this lab stays on x86_64. How Floci emulates this: Floci runs the '
                'function in a real python3.12 container.'
            ),
            'artifact_label': 'quote.py',
            'artifact': QUOTE_SOURCE,
        },
        {
            'key': 'memory-sizing',
            'title': 'Size memory from a sweep and a cost per million requests',
            'command': SIZING_COMMAND,
            'explanation': (
                'Lambda gives CPU in proportion to memory: one full vCPU at 1,769 MB, up to six at 10,240 MB. A '
                'CPU-bound function often costs the same or less at a larger size because it finishes sooner, so the '
                'right size comes from measuring: run the same work at each size, compute the cost per million '
                f'requests, and pick the cheapest size that meets the latency target ({LATENCY_TARGET_MS} ms here). '
                'AWS Lambda Power Tuning and Compute Optimizer automate this. How Floci emulates this: Floci passes '
                'the memory size to the function but does not scale CPU with it, so durations stay about the same '
                'and the smallest size wins here. The step measures each size and sets the cheapest one that meets '
                'the target.'
            ),
        },
        {
            'key': 'publish-version',
            'title': 'Publish version 1',
            'command': VERSION_COMMAND,
            'explanation': (
                'A published version is an immutable snapshot of the code and configuration, including memory and '
                'environment variables, with its own ARN. $LATEST keeps changing; versions do not, so production '
                'should invoke a version or an alias, never $LATEST. How Floci emulates this: versions are real and '
                'keep their configuration.'
            ),
        },
        {
            'key': 'alias-live',
            'title': 'Point the live alias at version 1',
            'command': ALIAS_COMMAND,
            'explanation': (
                f'An alias is a named pointer to a version. Callers, event source mappings, and API integrations use '
                f'the {ALIAS} alias ARN, so a release only moves the alias and a rollback moves it back. How Floci '
                'emulates this: aliases are real and invocations through them report the executed version.'
            ),
        },
        {
            'key': 'canary-release',
            'title': f'Send {CANARY_WEIGHT:.0%} of live traffic to version 2',
            'command': CANARY_COMMAND,
            'explanation': (
                f'Version 2 applies a volume discount. A weighted alias keeps version 1 as the primary and sends '
                f'{CANARY_WEIGHT:.0%} of invocations to version 2, so a bad release reaches only a fraction of '
                'users. CodeDeploy automates this as canary or linear deployments and rolls back when a CloudWatch '
                'alarm fires. How Floci emulates this: weighted routing is real; the step counts which version '
                'served each call and checks each price matches that version\'s rule.'
            ),
        },
        {
            'key': 'reserved-concurrency',
            'title': f'Reserve and cap concurrency at {RESERVED_CONCURRENCY}',
            'command': CONCURRENCY_COMMAND,
            'explanation': (
                'Accounts start with 1,000 concurrent executions per Region shared by all functions. Reserved '
                'concurrency sets aside capacity for one function and is also its maximum, which protects a '
                'downstream database from a burst; calls over it are throttled (synchronous callers get '
                'TooManyRequestsException, asynchronous events are retried). Provisioned concurrency is different: '
                'it keeps initialized environments warm on an alias to remove cold starts, and it is billed while '
                'configured. How Floci emulates this: reserved concurrency is enforced, and the step fires '
                f'{BURST_INVOCATIONS} calls at once to show the throttles. Floci routes provisioned concurrency '
                'requests to its S3 handler, so the lab explains it without configuring it.'
            ),
        },
        {
            'key': 'function-url',
            'title': 'Serve the alias from an IAM-authenticated function URL',
            'command': URL_COMMAND,
            'explanation': (
                'A function URL is a dedicated HTTPS endpoint for one function or alias, without API Gateway. AWS_IAM '
                'auth requires SigV4-signed requests from principals allowed lambda:InvokeFunctionUrl; NONE makes it '
                'public behind a resource-based policy. Use API Gateway when you need usage plans, request '
                'validation, or WAF. How Floci emulates this: the URL really invokes the alias with an HTTP event, '
                'but Floci also answers unsigned requests, so the step checks the auth type itself.'
            ),
        },
        {
            'key': 'promote-v2',
            'title': 'Promote version 2',
            'command': PROMOTE_COMMAND,
            'explanation': (
                'When the canary looks healthy, point the alias at version 2 and clear the weights. Callers notice '
                'nothing because they keep using the alias. Rolling back is the same command with version 1. How '
                'Floci emulates this: the alias update is real; the step checks every call now runs version 2.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    factory = FlociClientFactory()
    if name == 'lambda':
        # Throttles must reach the lab rather than be retried away, and held calls outlast the 5 s default.
        factory.config = factory.config.merge(Config(read_timeout=60, retries={'max_attempts': 0},
                                                     max_pool_connections=BURST_INVOCATIONS + 2))
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
        info = zipfile.ZipInfo('quote.py', date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, QUOTE_SOURCE)
    return buffer.getvalue()


def _cost_per_million(memory_mb: int, billed_ms: int, price: float = PRICE_PER_GB_SECOND) -> float:
    return round(PRICE_PER_MILLION_REQUESTS + memory_mb / 1024 * billed_ms / 1000 * price * 1_000_000, 2)


# ── Lookups ──────────────────────────────────────────────────────────────────

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


def _versions() -> dict[str, dict[str, Any]]:
    versions: dict[str, dict[str, Any]] = {}
    kwargs: dict[str, Any] = {'FunctionName': FUNCTION}
    for _ in range(20):
        try:
            page = _client('lambda').list_versions_by_function(**kwargs)
        except ClientError as exc:
            if _code(exc) == 'ResourceNotFoundException':
                return {}
            raise
        versions.update({item['Version']: item for item in page.get('Versions', []) if item['Version'] != '$LATEST'})
        if not page.get('NextMarker'):
            break
        kwargs['Marker'] = page['NextMarker']
    return versions


def _version_with(description: str) -> dict[str, Any] | None:
    matches = [item for item in _versions().values() if item.get('Description') == description]
    return max(matches, key=lambda item: int(item['Version'])) if matches else None


def _rule(config: dict[str, Any]) -> str | None:
    return ((config.get('Environment') or {}).get('Variables') or {}).get('PRICING_RULE')


def _alias() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_alias(FunctionName=FUNCTION, Name=ALIAS)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _weights(alias: dict[str, Any]) -> dict[str, float]:
    """Weights that send traffic away from the primary version. Floci never clears weights on UpdateAlias, so a
    leftover entry for the primary version itself routes nothing elsewhere and is ignored."""
    weights = (alias.get('RoutingConfig') or {}).get('AdditionalVersionWeights') or {}
    return {version: weight for version, weight in weights.items() if version != alias.get('FunctionVersion')}


def _stale_weights(alias: dict[str, Any] | None) -> bool:
    weights = ((alias or {}).get('RoutingConfig') or {}).get('AdditionalVersionWeights') or {}
    return bool(weights) and not _weights(alias or {})


def _reserved() -> int | None:
    return _client('lambda').get_function_concurrency(FunctionName=FUNCTION).get('ReservedConcurrentExecutions')


def _url_config() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_url_config(FunctionName=FUNCTION, Qualifier=ALIAS)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _role_exists() -> bool:
    try:
        _client('iam').get_role(RoleName=ROLE_NAME)
        return True
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return False
        raise


def _invoke(payload: dict[str, Any], qualifier: str | None = None) -> tuple[dict[str, Any], str]:
    kwargs: dict[str, Any] = {'FunctionName': FUNCTION, 'Payload': json.dumps(payload).encode()}
    if qualifier:
        kwargs['Qualifier'] = qualifier
    response = _client('lambda').invoke(**kwargs)
    body = json.loads(response['Payload'].read() or b'null')
    if response.get('FunctionError'):
        raise RuntimeError(f'{FUNCTION} failed: {body}')
    return body, response.get('ExecutedVersion') or '$LATEST'


def _credentials() -> Credentials:
    factory = FlociClientFactory()
    if factory.profile:
        found = boto3.Session(profile_name=factory.profile).get_credentials()
        if found:
            return found.get_frozen_credentials()
    return Credentials(factory.access_key_id or 'test', factory.secret_access_key or 'test', factory.session_token)


def _http(url: str, headers: dict[str, str]) -> tuple[int, str]:
    try:
        with urlopen(Request(url, headers=headers, method='GET'), timeout=30) as response:
            return response.status, response.read().decode()
    except HTTPError as exc:
        return exc.code, exc.read().decode(errors='replace')


def _call_url(function_url: str, signed: bool) -> tuple[int, str]:
    """Call the function URL through Floci's endpoint, sending the URL's host in the Host header."""
    host = urlparse(function_url).netloc
    target = function_url.rstrip('/') + URL_PATH
    headers = {'Host': host, 'Origin': URL_ORIGIN}
    if signed:
        request = AWSRequest(method='GET', url=target, headers=dict(headers))
        SigV4Auth(_credentials(), 'lambda', REGION).add_auth(request)
        headers = {key: value for key, value in request.headers.items()}
    endpoint = FlociClientFactory().endpoint_url.rstrip('/')
    return _http(endpoint + URL_PATH, headers)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_function() -> tuple[bool, str]:
    function = _function()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    if function.get('Role') != ROLE_ARN or function.get('Runtime') != RUNTIME or function.get('Handler') != HANDLER:
        return False, f'{FUNCTION} must run {HANDLER} on {RUNTIME} as {ROLE_NAME}.'
    return True, f'{FUNCTION} runs {HANDLER} on {RUNTIME} as {ROLE_NAME}.'


def _check_sizing() -> tuple[bool, str]:
    function = _function()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    marker = _marked('memory-sizing')
    if not marker:
        return False, 'Run the memory sweep.'
    if function.get('MemorySize') != marker['recommended_mb']:
        return False, f'{FUNCTION} has {function.get("MemorySize")} MB; the sweep picked {marker["recommended_mb"]} MB.'
    return True, marker['message']


def _check_version() -> tuple[bool, str]:
    version = _version_with(V1_DESCRIPTION)
    if not version:
        return False, f'No version of {FUNCTION} is described "{V1_DESCRIPTION}".'
    if _rule(version) != V1_RULE:
        return False, f'Version {version["Version"]} should use the {V1_RULE} pricing rule.'
    marker = _marked('memory-sizing')
    if marker and version.get('MemorySize') != marker['recommended_mb']:
        return False, f'Version {version["Version"]} has {version.get("MemorySize")} MB, not the sized {marker["recommended_mb"]} MB.'
    return True, f'Version {version["Version"]} snapshots {version.get("MemorySize")} MB and the {V1_RULE} rule.'


def _check_alias() -> tuple[bool, str]:
    alias = _alias()
    if not alias:
        return False, f'Alias {ALIAS} was not found.'
    if alias.get('FunctionVersion') in (None, '$LATEST'):
        return False, f'{ALIAS} must point at a published version, not $LATEST.'
    return True, f'{ALIAS} points at version {alias["FunctionVersion"]}.'


def _check_canary() -> tuple[bool, str]:
    v1, v2 = _version_with(V1_DESCRIPTION), _version_with(V2_DESCRIPTION)
    if not v1 or not v2:
        return False, 'Publish both versions first.'
    if _rule(v2) != V2_RULE:
        return False, f'Version {v2["Version"]} should use the {V2_RULE} rule.'
    marker = _marked('canary-release')
    if not marker:
        return False, f'Route {CANARY_WEIGHT:.0%} of {ALIAS} to version {v2["Version"]} and count the calls.'
    return True, marker['message']


def _check_concurrency() -> tuple[bool, str]:
    if not _function():
        return False, f'Function {FUNCTION} was not found.'
    reserved = _reserved()
    if reserved != RESERVED_CONCURRENCY:
        return False, f'{FUNCTION} reserves {reserved if reserved is not None else "no"} concurrency, not {RESERVED_CONCURRENCY}.'
    marker = _marked('reserved-concurrency')
    if not marker:
        return False, f'Fire {BURST_INVOCATIONS} calls at once to see the cap.'
    return True, marker['message']


def _check_url() -> tuple[bool, str]:
    config = _url_config()
    if not config:
        return False, f'{ALIAS} has no function URL.'
    if config.get('AuthType') != 'AWS_IAM':
        return False, f'The function URL must use AWS_IAM auth, not {config.get("AuthType")}.'
    origins = (config.get('Cors') or {}).get('AllowOrigins') or []
    if origins != [URL_ORIGIN]:
        return False, f'CORS should allow only {URL_ORIGIN}.'
    marker = _marked('function-url')
    if not marker:
        return False, 'Call the function URL with a signed request.'
    return True, marker['message']


def _check_promote() -> tuple[bool, str]:
    alias, v2 = _alias(), _version_with(V2_DESCRIPTION)
    if not alias or not v2:
        return False, f'Create {ALIAS} and version 2 first.'
    if alias.get('FunctionVersion') != v2['Version'] or _weights(alias):
        return False, f'{ALIAS} should point only at version {v2["Version"]}.'
    marker = _marked('promote-v2')
    if not marker:
        return False, f'Invoke {ALIAS} to confirm the promotion.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_function() -> dict[str, Any]:
    started = time.perf_counter()
    iam, lam = _client('iam'), _client('lambda')
    if not _role_exists():
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
                        Description='Price quote function (Lambda performance lab)')
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY, PolicyDocument=json.dumps(LOGS_POLICY))
    settings = {'Role': ROLE_ARN, 'Runtime': RUNTIME, 'Handler': HANDLER, 'Timeout': TIMEOUT_SECONDS,
                'Environment': {'Variables': {'PRICING_RULE': V1_RULE}}}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_zip())
        _wait_updated()
        lam.update_function_configuration(FunctionName=FUNCTION, **settings)
    else:
        lam.create_function(FunctionName=FUNCTION, Code={'ZipFile': _zip()}, MemorySize=INITIAL_MEMORY,
                            Architectures=['x86_64'], Description='Price quotes (Lambda performance lab)', **settings)
    _wait_updated()
    body, _ = _invoke({'sku': 'SKU-1', 'quantity': 2})
    function = _function() or {}
    response = {'Function': {key: function.get(key) for key in (
        'FunctionName', 'Runtime', 'Handler', 'Role', 'MemorySize', 'Timeout', 'Architectures', 'Environment')},
        'TestInvoke': body}
    verified, message = _check_function()
    if verified:
        message += f' A test quote for 2 x SKU-1 came back at {body.get("total")}.'
    return _result('create-function', CREATE_COMMAND, response, verified, message, started)


def step_memory_sizing() -> dict[str, Any]:
    started = time.perf_counter()
    if not _function():
        return _result('memory-sizing', SIZING_COMMAND, {}, False, f'Create {FUNCTION} first.', started)
    lam = _client('lambda')
    sweep = []
    for memory in MEMORY_SIZES:
        lam.update_function_configuration(FunctionName=FUNCTION, MemorySize=memory)
        _wait_updated()
        timings = []
        reported = None
        for _ in range(SWEEP_INVOCATIONS):
            body, _ = _invoke({'sku': 'SKU-1', 'quantity': 1, 'rounds': ROUNDS})
            timings.append(float(body['handler_ms']))
            reported = body.get('memory_mb')
        warm_ms = statistics.median(timings[1:] or timings)
        billed = math.ceil(warm_ms)
        sweep.append({'MemoryMB': memory, 'ReportedMemoryMB': reported, 'WarmMs': round(warm_ms, 1),
                      'BilledMs': billed, 'CostPerMillion': _cost_per_million(memory, billed),
                      'Arm64CostPerMillion': _cost_per_million(memory, billed, ARM_PRICE_PER_GB_SECOND)})
    fits = [row for row in sweep if row['WarmMs'] <= LATENCY_TARGET_MS] or sweep
    best = min(fits, key=lambda row: (row['CostPerMillion'], row['WarmMs']))
    lam.update_function_configuration(FunctionName=FUNCTION, MemorySize=best['MemoryMB'])
    _wait_updated()
    fastest, slowest = min(row['WarmMs'] for row in sweep), max(row['WarmMs'] for row in sweep)
    flat = slowest <= fastest * 1.5
    message = (f'{best["MemoryMB"]} MB is the cheapest size under {LATENCY_TARGET_MS} ms at about '
               f'{best["WarmMs"]:.0f} ms and ${best["CostPerMillion"]:.2f} per million requests.')
    if flat:
        message += (f' Durations stayed between {fastest:.0f} and {slowest:.0f} ms because Floci does not scale CPU '
                    'with memory; on AWS the larger sizes run this loop much faster.')
    _marker('memory-sizing', {'recommended_mb': best['MemoryMB'], 'message': message})
    response = {'Sweep': sweep, 'LatencyTargetMs': LATENCY_TARGET_MS, 'Chosen': best,
                'MemorySize': (_function() or {}).get('MemorySize')}
    verified, check = _check_sizing()
    return _result('memory-sizing', SIZING_COMMAND, response, verified, message if verified else check, started)


def step_publish_version() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_sizing()
    if not ok:
        return _result('publish-version', VERSION_COMMAND, {}, False, f'Size the function first: {problem}', started)
    version = _version_with(V1_DESCRIPTION)
    if not version or version.get('MemorySize') != _marked('memory-sizing')['recommended_mb'] or _rule(version) != V1_RULE:
        function = _function() or {}
        if _rule(function) != V1_RULE:
            _client('lambda').update_function_configuration(
                FunctionName=FUNCTION, Environment={'Variables': {'PRICING_RULE': V1_RULE}})
            _wait_updated()
        _client('lambda').publish_version(FunctionName=FUNCTION, Description=V1_DESCRIPTION)
        version = _version_with(V1_DESCRIPTION)
    response = {key: (version or {}).get(key) for key in (
        'FunctionArn', 'Version', 'Description', 'MemorySize', 'Environment', 'CodeSha256')}
    verified, message = _check_version()
    return _result('publish-version', VERSION_COMMAND, response, verified, message, started)


def step_alias_live() -> dict[str, Any]:
    started = time.perf_counter()
    version = _version_with(V1_DESCRIPTION)
    if not version:
        return _result('alias-live', ALIAS_COMMAND, {}, False, 'Publish version 1 first.', started)
    lam = _client('lambda')
    if _alias():
        lam.update_alias(FunctionName=FUNCTION, Name=ALIAS, FunctionVersion=version['Version'],
                         RoutingConfig={'AdditionalVersionWeights': {}})
        if _weights(_alias() or {}):
            # Floci keeps old canary weights on UpdateAlias, so start the alias over.
            lam.delete_alias(FunctionName=FUNCTION, Name=ALIAS)
    if not _alias():
        lam.create_alias(FunctionName=FUNCTION, Name=ALIAS, FunctionVersion=version['Version'],
                         Description='Production traffic')
    body, executed = _invoke({'sku': 'SKU-1', 'quantity': 12}, ALIAS)
    response = {'Alias': _alias(), 'Invoke': {'ExecutedVersion': executed, 'Body': body}}
    verified, message = _check_alias()
    if verified and executed != version['Version']:
        verified, message = False, f'{ALIAS} ran version {executed}, not {version["Version"]}.'
    elif verified:
        message += f' A call through {ALIAS} ran version {executed} with the {body.get("pricing_rule")} rule.'
    return _result('alias-live', ALIAS_COMMAND, response, verified, message, started)


def step_canary_release() -> dict[str, Any]:
    started = time.perf_counter()
    v1 = _version_with(V1_DESCRIPTION)
    ok, problem = _check_alias()
    if not v1 or not ok:
        return _result('canary-release', CANARY_COMMAND, {}, False, f'Create the {ALIAS} alias first: {problem}', started)
    lam = _client('lambda')
    v2 = _version_with(V2_DESCRIPTION)
    if not v2 or _rule(v2) != V2_RULE:
        lam.update_function_configuration(FunctionName=FUNCTION, Environment={'Variables': {'PRICING_RULE': V2_RULE}})
        _wait_updated()
        lam.publish_version(FunctionName=FUNCTION, Description=V2_DESCRIPTION)
        v2 = _version_with(V2_DESCRIPTION) or {}
    lam.update_alias(FunctionName=FUNCTION, Name=ALIAS, FunctionVersion=v1['Version'],
                     RoutingConfig={'AdditionalVersionWeights': {v2['Version']: CANARY_WEIGHT}})
    counts = {v1['Version']: 0, v2['Version']: 0}
    totals: dict[str, set[float]] = {v1['Version']: set(), v2['Version']: set()}
    mismatched = []
    for _ in range(CANARY_INVOCATIONS):
        body, executed = _invoke({'sku': 'SKU-1', 'quantity': 12}, ALIAS)
        counts[executed] = counts.get(executed, 0) + 1
        expected_rule = V1_RULE if executed == v1['Version'] else V2_RULE if executed == v2['Version'] else None
        if body.get('pricing_rule') != expected_rule or body.get('version') != executed:
            mismatched.append(executed)
        totals.setdefault(executed, set()).add(body.get('total'))
    response = {'Alias': _alias(), 'Invocations': CANARY_INVOCATIONS, 'ExecutedVersions': counts,
                'TotalsByVersion': {key: sorted(value) for key, value in totals.items()}}
    if mismatched:
        return _result('canary-release', CANARY_COMMAND, response, False,
                       'Some responses did not match the pricing rule of the version that served them.', started)
    share = counts.get(v2['Version'], 0) / CANARY_INVOCATIONS
    if not counts.get(v1['Version']) or not counts.get(v2['Version']) or not 0.05 <= share <= 0.45:
        return _result('canary-release', CANARY_COMMAND, response, False,
                       f'Expected about {CANARY_WEIGHT:.0%} of calls on version {v2["Version"]}; got {share:.0%}.', started)
    v1_total, v2_total = min(totals[v1['Version']]), min(totals[v2['Version']])
    message = (f'Version {v2["Version"]} served {counts[v2["Version"]]} of {CANARY_INVOCATIONS} calls ({share:.0%}) at '
               f'{v2_total} for 12 units, while version {v1["Version"]} kept quoting {v1_total}.')
    _marker('canary-release', {'message': message})
    verified, check = _check_canary()
    return _result('canary-release', CANARY_COMMAND, response, verified, message if verified else check, started)


def step_reserved_concurrency() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_alias()
    if not ok:
        return _result('reserved-concurrency', CONCURRENCY_COMMAND, {}, False, f'Create the alias first: {problem}', started)
    lam = _client('lambda')
    lam.put_function_concurrency(FunctionName=FUNCTION, ReservedConcurrentExecutions=RESERVED_CONCURRENCY)
    _invoke({'quantity': 1}, ALIAS)  # warm one environment so the burst measures the cap, not cold starts

    def call(_index: int) -> str:
        try:
            _invoke({'quantity': 1, 'hold_seconds': HOLD_SECONDS}, ALIAS)
            return 'succeeded'
        except ClientError as exc:
            return 'throttled' if _code(exc) == 'TooManyRequestsException' else _code(exc) or 'error'
        except (BotoCoreError, RuntimeError):
            return 'error'
    with ThreadPoolExecutor(max_workers=BURST_INVOCATIONS) as pool:
        outcomes = list(pool.map(call, range(BURST_INVOCATIONS)))
    succeeded, throttled = outcomes.count('succeeded'), outcomes.count('throttled')
    account = lam.get_account_settings().get('AccountLimit') or {}
    response = {'ReservedConcurrentExecutions': _reserved(), 'Burst': BURST_INVOCATIONS, 'Outcomes': outcomes,
                'Succeeded': succeeded, 'Throttled': throttled,
                'AccountLimit': {key: account.get(key) for key in ('ConcurrentExecutions', 'UnreservedConcurrentExecutions')}}
    if succeeded + throttled != BURST_INVOCATIONS or not succeeded or not throttled or succeeded > RESERVED_CONCURRENCY:
        return _result('reserved-concurrency', CONCURRENCY_COMMAND, response, False,
                       f'Expected at most {RESERVED_CONCURRENCY} of {BURST_INVOCATIONS} calls to run and the rest to '
                       f'be throttled; {succeeded} ran and {throttled} were throttled.', started)
    message = (f'{succeeded} of {BURST_INVOCATIONS} simultaneous calls ran and {throttled} got TooManyRequestsException: '
               f'the reservation of {RESERVED_CONCURRENCY} is also the cap. The account has '
               f'{account.get("UnreservedConcurrentExecutions")} unreserved executions left.')
    _marker('reserved-concurrency', {'message': message})
    verified, check = _check_concurrency()
    return _result('reserved-concurrency', CONCURRENCY_COMMAND, response, verified, message if verified else check, started)


def step_function_url() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_alias()
    if not ok:
        return _result('function-url', URL_COMMAND, {}, False, f'Create the alias first: {problem}', started)
    lam = _client('lambda')
    settings = {'AuthType': 'AWS_IAM', 'Cors': {'AllowOrigins': [URL_ORIGIN], 'AllowMethods': ['GET']}}
    if _url_config():
        lam.update_function_url_config(FunctionName=FUNCTION, Qualifier=ALIAS, **settings)
    else:
        lam.create_function_url_config(FunctionName=FUNCTION, Qualifier=ALIAS, **settings)
    config = _url_config() or {}
    function_url = config.get('FunctionUrl') or ''
    try:
        signed_status, signed_body = _call_url(function_url, signed=True)
        unsigned_status, _ = _call_url(function_url, signed=False)
    except (URLError, OSError) as exc:
        return _result('function-url', URL_COMMAND, {'FunctionUrl': function_url}, False,
                       f'Could not reach the function URL: {exc}', started)
    try:
        body = json.loads(signed_body)
    except ValueError:
        body = {'raw': signed_body[:500]}
    response = {'FunctionUrl': function_url, 'AuthType': config.get('AuthType'), 'Cors': config.get('Cors'),
                'SignedRequest': {'Status': signed_status, 'Body': body}, 'UnsignedRequestStatus': unsigned_status}
    if signed_status != 200 or not isinstance(body, dict) or 'total' not in body:
        return _result('function-url', URL_COMMAND, response, False,
                       f'The signed request returned HTTP {signed_status} without a quote.', started)
    message = (f'A SigV4-signed GET {URL_PATH} returned a quote of {body["total"]} from version {body.get("version")}.')
    if unsigned_status == 200:
        message += ' Floci also answered an unsigned request, which AWS refuses with 403 for AWS_IAM auth.'
    _marker('function-url', {'message': message})
    verified, check = _check_url()
    return _result('function-url', URL_COMMAND, response, verified, message if verified else check, started)


def step_promote_v2() -> dict[str, Any]:
    started = time.perf_counter()
    v2 = _version_with(V2_DESCRIPTION)
    if not v2 or not _alias():
        return _result('promote-v2', PROMOTE_COMMAND, {}, False, 'Run the canary first.', started)
    _client('lambda').update_alias(FunctionName=FUNCTION, Name=ALIAS, FunctionVersion=v2['Version'],
                                   RoutingConfig={'AdditionalVersionWeights': {}})
    executed = [_invoke({'sku': 'SKU-1', 'quantity': 12}, ALIAS)[1] for _ in range(PROMOTE_INVOCATIONS)]
    response = {'Alias': _alias(), 'ExecutedVersions': executed}
    if set(executed) != {v2['Version']}:
        return _result('promote-v2', PROMOTE_COMMAND, response, False,
                       f'Calls still ran versions {", ".join(sorted(set(executed)))}.', started)
    message = (f'All {PROMOTE_INVOCATIONS} calls through {ALIAS} ran version {v2["Version"]}; rolling back is the same '
               'update-alias with version 1.')
    if _stale_weights(response['Alias']):
        message += (f' Floci kept the old weight entry for version {v2["Version"]} (AWS clears it), which now points '
                    'at the primary version and routes nothing elsewhere.')
    _marker('promote-v2', {'message': message})
    verified, check = _check_promote()
    return _result('promote-v2', PROMOTE_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'create-function': step_create_function,
    'memory-sizing': step_memory_sizing,
    'publish-version': step_publish_version,
    'alias-live': step_alias_live,
    'canary-release': step_canary_release,
    'reserved-concurrency': step_reserved_concurrency,
    'function-url': step_function_url,
    'promote-v2': step_promote_v2,
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
        'create-function': lambda: _check_function()[0],
        'memory-sizing': lambda: _check_sizing()[0],
        'publish-version': lambda: _check_version()[0],
        'alias-live': lambda: _check_alias()[0],
        'canary-release': lambda: _check_canary()[0],
        'reserved-concurrency': lambda: _check_concurrency()[0],
        'function-url': lambda: _check_url()[0],
        'promote-v2': lambda: _check_promote()[0],
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
    """Delete the function URL, concurrency, alias, function (with its versions), role, and log group."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if code in {'ResourceNotFoundException', 'NoSuchEntity'} or 'NotFound' in code:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    lam, iam, logs = _client('lambda'), _client('iam'), _client('logs')
    try:
        exists = bool(_function())
    except (ClientError, BotoCoreError) as exc:
        exists = False
        errors.append(f'function: {exc}')
    if exists:
        attempt(f'function-url:{ALIAS}', lambda: lam.delete_function_url_config(FunctionName=FUNCTION, Qualifier=ALIAS))
        attempt('reserved-concurrency', lambda: lam.delete_function_concurrency(FunctionName=FUNCTION))
        attempt(f'alias:{ALIAS}', lambda: lam.delete_alias(FunctionName=FUNCTION, Name=ALIAS))
        attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    attempt(f'role-policy:{ROLE_POLICY}', lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))
    attempt(f'log-group:{LOG_GROUP}', lambda: logs.delete_log_group(logGroupName=LOG_GROUP))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws lambda delete-function-url-config --function-name {FUNCTION} --qualifier {ALIAS}\n'
            f'aws lambda delete-function-concurrency --function-name {FUNCTION}\n'
            f'aws lambda delete-alias --function-name {FUNCTION} --name {ALIAS}\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws iam delete-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY}\n'
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            f'aws logs delete-log-group --log-group-name {LOG_GROUP}'
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
            else 'The function URL, alias, versions, function, role, and log group are deleted.',
        },
    }
