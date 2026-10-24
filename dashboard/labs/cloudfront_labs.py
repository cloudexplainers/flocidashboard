"""Edge delivery lab: two private S3 origins behind one CloudFront distribution with origin access control, cache
policies per path, a custom error page, an invalidation, and a Global Accelerator for a UDP game service in two
Regions, with a traffic dial failover drill and a chooser between CloudFront and Global Accelerator.

Floci 2.3.0 serves a distribution for real: a request sent to Floci with the distribution's domain in the Host
header is routed by path to the right origin, gets the default root object and the custom error page, and a
disabled distribution answers 404. Floci never caches, does not redirect HTTP to HTTPS, and does not enforce origin
access control, bucket policies, or S3 Block Public Access for anonymous reads. Global Accelerator is control plane
only: accelerators, listeners, and endpoint groups are stored and validated, but no traffic flows, health checks
never run (every endpoint reports HEALTHY), and endpoint IDs are not checked. The lab checks those rules itself.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudfront'
LAB_KEY = 'edge-delivery'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

SITE_BUCKET = 'lab-edge-site'
ASSETS_BUCKET = 'lab-edge-assets'
INDEX_V1 = '<!doctype html><title>Shop</title><h1>Shop v1</h1><link rel="stylesheet" href="/assets/app.css">'
INDEX_V2 = '<!doctype html><title>Shop</title><h1>Shop v2</h1><link rel="stylesheet" href="/assets/app.css">'
NOT_FOUND_PAGE = '<!doctype html><title>Not found</title><h1>Page not found</h1>'
APP_CSS = 'body { font-family: sans-serif; }'
ASSET_KEY = 'assets/app.css'

OAC_NAME = 'lab-edge-oac'
HTML_POLICY = 'lab-edge-html'
ASSETS_POLICY = 'lab-edge-assets'
HTML_TTL = {'MinTTL': 0, 'DefaultTTL': 300, 'MaxTTL': 3600}
ASSETS_TTL = {'MinTTL': 0, 'DefaultTTL': 86400, 'MaxTTL': 31536000}
DIST_COMMENT = 'lab-edge-delivery'
SITE_ORIGIN, ASSETS_ORIGIN = 'site-s3', 'assets-s3'
ASSETS_PATH = '/assets/*'
PRICE_CLASS = 'PriceClass_100'
DEPLOY_TIMEOUT_SECONDS = 60

GA_REGION = 'us-west-2'
ACCELERATOR = 'lab-edge-accelerator'
GAME_PORT = 7777
GAME_REGIONS = ['us-east-1', 'eu-west-1']
EIP_NAME = 'lab-edge-game'

# Workloads the chooser compares: protocol, whether responses are cacheable, and whether clients need fixed IPs.
SCENARIOS = [
    ('Storefront pages and images', 'HTTPS', True, False),
    ('Video on demand (HLS segments)', 'HTTPS', True, False),
    ('Multiplayer game servers', 'UDP', False, False),
    ('IoT devices on MQTT with a firewall allowlist', 'TCP', False, True),
    ('Payments API that partners allowlist by IP', 'HTTPS', False, True),
]

ORIGINS_COMMAND = (
    f'aws s3api create-bucket --bucket {SITE_BUCKET}\n'
    f'aws s3api create-bucket --bucket {ASSETS_BUCKET}\n'
    f'aws s3api put-public-access-block --bucket {SITE_BUCKET} --public-access-block-configuration '
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true\n'
    f'aws s3 cp index.html s3://{SITE_BUCKET}/ && aws s3 cp 404.html s3://{SITE_BUCKET}/\n'
    f'aws s3 cp app.css s3://{ASSETS_BUCKET}/{ASSET_KEY}'
)
POLICIES_COMMAND = (
    f'aws cloudfront create-origin-access-control --origin-access-control-config Name={OAC_NAME},'
    'SigningProtocol=sigv4,SigningBehavior=always,OriginAccessControlOriginType=s3\n'
    f'aws cloudfront create-cache-policy --cache-policy-config file://{HTML_POLICY}.json     # TTL 0 / 300 / 3600\n'
    f'aws cloudfront create-cache-policy --cache-policy-config file://{ASSETS_POLICY}.json   # TTL 0 / 1 day / 1 year'
)
DISTRIBUTION_COMMAND = (
    'aws cloudfront create-distribution --distribution-config file://distribution.json\n'
    f'# Origins {SITE_ORIGIN} and {ASSETS_ORIGIN} with {OAC_NAME}; default behavior redirect-to-https with '
    f'{HTML_POLICY};\n# {ASSETS_PATH} to {ASSETS_ORIGIN} with {ASSETS_POLICY}; 404 -> /404.html; {PRICE_CLASS}\n'
    'aws cloudfront wait distribution-deployed --id <distribution-id>'
)
BUCKET_POLICY_COMMAND = (
    f'aws s3api put-bucket-policy --bucket {SITE_BUCKET} --policy file://site-oac-policy.json\n'
    f'aws s3api put-bucket-policy --bucket {ASSETS_BUCKET} --policy file://assets-oac-policy.json\n'
    '# Principal cloudfront.amazonaws.com, s3:GetObject, Condition AWS:SourceArn = the distribution ARN'
)
SERVE_COMMAND = (
    'curl -i https://<distribution-domain>/\n'
    f'curl -i https://<distribution-domain>/{ASSET_KEY}\n'
    'curl -i https://<distribution-domain>/no-such-page\n'
    f'curl -i https://{SITE_BUCKET}.s3.amazonaws.com/index.html   # direct, should be refused'
)
INVALIDATE_COMMAND = (
    f'aws s3 cp index.html s3://{SITE_BUCKET}/   # v2\n'
    'aws cloudfront create-invalidation --distribution-id <distribution-id> --paths /index.html\n'
    'aws cloudfront wait invalidation-completed --distribution-id <distribution-id> --id <invalidation-id>'
)
ACCELERATOR_COMMAND = (
    f'aws ec2 allocate-address --region {GAME_REGIONS[0]}   # and {GAME_REGIONS[1]}\n'
    f'aws globalaccelerator create-accelerator --name {ACCELERATOR} --region {GA_REGION}\n'
    f'aws globalaccelerator create-listener --accelerator-arn <arn> --protocol UDP '
    f'--port-ranges FromPort={GAME_PORT},ToPort={GAME_PORT} --region {GA_REGION}\n'
    f'aws globalaccelerator create-endpoint-group --listener-arn <arn> --endpoint-group-region {GAME_REGIONS[0]} '
    f'--endpoint-configurations EndpointId=<eipalloc>,Weight=100 --health-check-protocol TCP '
    f'--health-check-port {GAME_PORT} --region {GA_REGION}   # and {GAME_REGIONS[1]}'
)
FAILOVER_COMMAND = (
    f'aws globalaccelerator update-endpoint-group --endpoint-group-arn <{GAME_REGIONS[0]}-group> '
    f'--traffic-dial-percentage 0 --region {GA_REGION}\n'
    '# North American players now reach eu-west-1 on the same static IPs\n'
    f'aws globalaccelerator update-endpoint-group --endpoint-group-arn <{GAME_REGIONS[0]}-group> '
    f'--traffic-dial-percentage 100 --region {GA_REGION}'
)
CHOOSE_COMMAND = (
    '# HTTP(S) and cacheable -> CloudFront; TCP/UDP, static anycast IPs, or fast Regional failover -> '
    'Global Accelerator\n'
    f'aws globalaccelerator describe-accelerator --accelerator-arn <arn> --region {GA_REGION}   # IpSets'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Deliver content at the edge with CloudFront and Global Accelerator',
    'description': (
        'Put two private S3 origins behind one CloudFront distribution with origin access control, cache policies '
        'per path, HTTPS redirects, and a custom error page; serve and invalidate through the edge; then front a '
        'UDP game service in two Regions with Global Accelerator, drain a Region with the traffic dial, and choose '
        'CloudFront or Global Accelerator for five workloads.'
    ),
    'steps': [
        {
            'key': 'private-origins',
            'title': 'Create private S3 origins for pages and assets',
            'command': ORIGINS_COMMAND,
            'explanation': (
                'The site bucket holds HTML pages and a 404 page; the assets bucket holds versioned CSS and images. '
                'Both block all public access, so only CloudFront can read them. How Floci emulates this: the buckets '
                'and the Block Public Access settings are stored, but Floci still serves anonymous reads, so the step '
                'checks the settings and records an anonymous read.'
            ),
        },
        {
            'key': 'access-and-cache-policies',
            'title': 'Create origin access control and cache policies',
            'command': POLICIES_COMMAND,
            'explanation': (
                'Origin access control (OAC) makes CloudFront sign its origin requests with SigV4; it replaces the '
                'legacy origin access identity (OAI) and supports SSE-KMS. A cache policy sets the TTLs and the cache '
                'key: pages change often (5 minutes by default), while fingerprinted assets can be cached for a day '
                'or up to a year. Leaving cookies, headers, and query strings out of the cache key raises the hit '
                'ratio. How Floci emulates this: both are stored; Floci never caches, so the step checks the TTLs.'
            ),
        },
        {
            'key': 'distribution',
            'title': 'Create the distribution with path-based behaviors',
            'command': DISTRIBUTION_COMMAND,
            'explanation': (
                f'The default behavior sends pages to the site origin and redirects HTTP to HTTPS; {ASSETS_PATH} goes '
                f'to the assets origin with the long TTL. A custom error response turns a 404 into a friendly page, '
                f'and {PRICE_CLASS} serves only from North America and Europe edge locations to save cost. How Floci '
                'emulates this: the distribution deploys at once and serves requests, but Floci accepts a behavior '
                'that targets a missing origin and never redirects to HTTPS, so the step checks the configuration.'
            ),
        },
        {
            'key': 'origin-bucket-policies',
            'title': 'Allow only this distribution to read the buckets',
            'command': BUCKET_POLICY_COMMAND,
            'explanation': (
                'With OAC, each bucket policy allows the cloudfront.amazonaws.com service principal to call '
                's3:GetObject only when AWS:SourceArn is this distribution, so another distribution (or a direct '
                'request) cannot read the objects. How Floci emulates this: the policies are stored but not '
                'evaluated for these reads, so the step checks each statement.'
            ),
        },
        {
            'key': 'serve-through-edge',
            'title': 'Serve pages, assets, and the error page through the edge',
            'command': SERVE_COMMAND,
            'explanation': (
                'Requests reach the nearest edge location; the distribution picks a behavior by path, returns the '
                'default root object for /, and serves the custom error page for a missing key. How Floci emulates '
                "this: the request goes to Floci with the distribution's domain in the Host header, and Floci routes "
                'it by path to the right bucket for real.'
            ),
        },
        {
            'key': 'invalidate',
            'title': 'Publish a new page and invalidate the cached copy',
            'command': INVALIDATE_COMMAND,
            'explanation': (
                'Edge caches keep a page until its TTL expires, so publishing a new index.html needs an invalidation '
                '(the first 1,000 paths a month are free) or a new file name. Fingerprinted asset names such as '
                'app.3f9c.css avoid invalidations entirely. How Floci emulates this: the invalidation completes, but '
                'Floci does not cache, so the new page is visible even before it; the step reports what AWS would '
                'have served.'
            ),
        },
        {
            'key': 'global-accelerator',
            'title': 'Front a UDP game service in two Regions with Global Accelerator',
            'command': ACCELERATOR_COMMAND,
            'explanation': (
                'Global Accelerator gives two static anycast IP addresses that enter the AWS network at the nearest '
                'edge and reach the closest healthy endpoint group over the AWS backbone. It works for TCP and UDP, '
                'which CloudFront does not serve. How Floci emulates this: the accelerator, the UDP listener, and '
                'the endpoint groups are stored and validated, but no traffic flows and Floci does not check that '
                'the endpoints exist, so the step checks the Elastic IPs.'
            ),
        },
        {
            'key': 'regional-failover',
            'title': 'Drain a Region with the traffic dial',
            'command': FAILOVER_COMMAND,
            'explanation': (
                'Setting a Region\'s traffic dial to 0 sends its players to the next closest endpoint group on the '
                'same IP addresses, with no DNS change or TTL to wait for; failing health checks do the same '
                'automatically within seconds. How Floci emulates this: the dial is stored and validated (0 to 100), '
                'health checks never run, and no traffic flows, so the step works out where players go.'
            ),
        },
        {
            'key': 'choose-edge-service',
            'title': 'Choose CloudFront or Global Accelerator',
            'command': CHOOSE_COMMAND,
            'explanation': (
                'CloudFront caches HTTP and HTTPS content at the edge (static sites, video, APIs with Lambda@Edge or '
                'CloudFront Functions). Global Accelerator does not cache; it speeds up and fails over TCP and UDP '
                'applications behind static IPs that clients can allowlist. How Floci emulates this: the step applies '
                'the rules to five workloads and checks the two static IPs on the accelerator.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str, region: str | None = None):
    factory = FlociClientFactory()
    if region:
        factory.region = region
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


def _http(path: str, host: str) -> tuple[int, str]:
    """Send a request to Floci's endpoint with the distribution's domain in the Host header."""
    endpoint = FlociClientFactory().endpoint_url.rstrip('/')
    try:
        with urlopen(Request(endpoint + path, headers={'Host': host}, method='GET'), timeout=15) as response:
            return response.status, response.read().decode(errors='replace')
    except HTTPError as exc:
        return exc.code, exc.read().decode(errors='replace')
    except URLError as exc:
        return 0, str(exc.reason)


# ── Pure rules ───────────────────────────────────────────────────────────────

def ttl_problem(name: str, ttl: dict[str, Any], default_at_least: int = 0, default_at_most: int | None = None) -> str:
    """Why a cache policy's TTLs are invalid or wrong for its content, or '' when they are fine."""
    low, default, high = ttl.get('MinTTL'), ttl.get('DefaultTTL'), ttl.get('MaxTTL')
    if None in (low, default, high):
        return f'{name} must set MinTTL, DefaultTTL, and MaxTTL.'
    if not low <= default <= high:
        return f'{name} must keep MinTTL <= DefaultTTL <= MaxTTL (got {low} / {default} / {high}).'
    if default < default_at_least:
        return f'{name} should cache for at least {default_at_least} seconds by default.'
    if default_at_most is not None and default > default_at_most:
        return f'{name} should cache for at most {default_at_most} seconds by default.'
    return ''


def traffic_split(groups: list[tuple[str, float, bool]]) -> dict[str, float]:
    """Share of one client population per Region, given endpoint groups ordered from closest to farthest.

    Each group is (region, traffic dial percentage, healthy). A group keeps its dial's share of the traffic that
    reaches it and passes the rest to the next closest group; unhealthy groups pass everything on. Traffic left
    after the last group goes back to the closest healthy group with a dial above zero, as Global Accelerator does.
    """
    shares = {region: 0.0 for region, _dial, _healthy in groups}
    remaining = 100.0
    for region, dial, healthy in groups:
        if not healthy:
            continue
        taken = remaining * max(0.0, min(dial, 100.0)) / 100
        shares[region] += taken
        remaining -= taken
    if remaining > 1e-9:
        fallback = next((region for region, dial, healthy in groups if healthy and dial > 0), None)
        if fallback:
            shares[fallback] += remaining
    return {region: round(share, 1) for region, share in shares.items()}


def choose_edge(protocol: str, cacheable: bool, needs_static_ip: bool) -> str:
    """CloudFront for cacheable HTTP(S); Global Accelerator for TCP/UDP or clients that allowlist fixed IPs."""
    if protocol.upper() not in ('HTTP', 'HTTPS') or needs_static_ip:
        return 'Global Accelerator'
    # HTTP(S) without fixed IPs: CloudFront, which also accelerates uncached requests over the AWS network.
    return 'CloudFront'


# ── Lookups ──────────────────────────────────────────────────────────────────

def _bucket_exists(bucket: str) -> bool:
    try:
        _client('s3').head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        if _code(exc) in ('404', 'NoSuchBucket', 'NotFound'):
            return False
        raise


def _public_access_block(bucket: str) -> dict[str, Any]:
    try:
        return _client('s3').get_public_access_block(Bucket=bucket).get('PublicAccessBlockConfiguration') or {}
    except ClientError as exc:
        if 'NoSuch' in _code(exc) or _code(exc) == '404':
            return {}
        raise


def _object(bucket: str, key: str) -> str | None:
    try:
        return _client('s3').get_object(Bucket=bucket, Key=key)['Body'].read().decode()
    except ClientError as exc:
        if _code(exc) in ('NoSuchKey', 'NoSuchBucket', '404'):
            return None
        raise


def _bucket_policy(bucket: str) -> dict[str, Any] | None:
    try:
        policy = _client('s3').get_bucket_policy(Bucket=bucket).get('Policy')
    except ClientError as exc:
        if 'NoSuch' in _code(exc) or _code(exc) == '404':
            return None
        raise
    return json.loads(policy) if isinstance(policy, str) else policy


def _oac() -> dict[str, Any] | None:
    items = (_client('cloudfront').list_origin_access_controls().get('OriginAccessControlList') or {}).get('Items') or []
    for item in items:
        if item.get('Name') == OAC_NAME:
            return item
    return None


def _cache_policy(name: str) -> dict[str, Any] | None:
    items = (_client('cloudfront').list_cache_policies(Type='custom').get('CachePolicyList') or {}).get('Items') or []
    for item in items:
        policy = item.get('CachePolicy') or {}
        if (policy.get('CachePolicyConfig') or {}).get('Name') == name:
            return policy
    return None


def _distribution_summary() -> dict[str, Any] | None:
    items = (_client('cloudfront').list_distributions().get('DistributionList') or {}).get('Items') or []
    for item in items:
        if item.get('Comment') == DIST_COMMENT:
            return item
    return None


def _distribution() -> dict[str, Any] | None:
    summary = _distribution_summary()
    if not summary:
        return None
    return _client('cloudfront').get_distribution(Id=summary['Id'])['Distribution']


def _accelerator() -> dict[str, Any] | None:
    for item in _client('globalaccelerator', GA_REGION).list_accelerators().get('Accelerators', []):
        if item.get('Name') == ACCELERATOR:
            return item
    return None


def _listener(accelerator_arn: str) -> dict[str, Any] | None:
    listeners = _client('globalaccelerator', GA_REGION).list_listeners(AcceleratorArn=accelerator_arn).get('Listeners', [])
    return listeners[0] if listeners else None


def _endpoint_groups(listener_arn: str) -> dict[str, dict[str, Any]]:
    groups = _client('globalaccelerator', GA_REGION).list_endpoint_groups(ListenerArn=listener_arn).get('EndpointGroups', [])
    return {group.get('EndpointGroupRegion'): group for group in groups}


def _elastic_ip(region: str) -> dict[str, Any] | None:
    addresses = _client('ec2', region).describe_addresses(
        Filters=[{'Name': 'tag:Name', 'Values': [EIP_NAME]}]).get('Addresses', [])
    return addresses[0] if addresses else None


def _game_setup() -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, dict[str, Any]]]:
    accelerator = _accelerator()
    listener = _listener(accelerator['AcceleratorArn']) if accelerator else None
    groups = _endpoint_groups(listener['ListenerArn']) if listener else {}
    return accelerator, listener, groups


# ── Checks ───────────────────────────────────────────────────────────────────

BLOCK_ALL = ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')


def _check_origins() -> tuple[bool, str]:
    for bucket in (SITE_BUCKET, ASSETS_BUCKET):
        if not _bucket_exists(bucket):
            return False, f'Bucket {bucket} was not found.'
        block = _public_access_block(bucket)
        if not all(block.get(flag) for flag in BLOCK_ALL):
            return False, f'{bucket} must block all public access.'
    if _object(SITE_BUCKET, 'index.html') is None or _object(SITE_BUCKET, '404.html') is None:
        return False, f'{SITE_BUCKET} needs index.html and 404.html.'
    if _object(ASSETS_BUCKET, ASSET_KEY) is None:
        return False, f'{ASSETS_BUCKET} needs {ASSET_KEY}.'
    return True, f'{SITE_BUCKET} and {ASSETS_BUCKET} block all public access and hold the pages and assets.'


def _check_policies() -> tuple[bool, str]:
    oac = _oac()
    if not oac:
        return False, f'Origin access control {OAC_NAME} was not found.'
    if (oac.get('SigningProtocol'), oac.get('SigningBehavior'), oac.get('OriginAccessControlOriginType')) != (
            'sigv4', 'always', 's3'):
        return False, f'{OAC_NAME} must always sign S3 requests with SigV4.'
    for name, ttl_bounds in ((HTML_POLICY, (0, 300)), (ASSETS_POLICY, (86400, None))):
        policy = _cache_policy(name)
        if not policy:
            return False, f'Cache policy {name} was not found.'
        problem = ttl_problem(name, policy.get('CachePolicyConfig') or {}, *ttl_bounds)
        if problem:
            return False, problem
    return True, (f'{OAC_NAME} signs requests with SigV4; {HTML_POLICY} caches pages 5 minutes and {ASSETS_POLICY} '
                  'caches assets a day by default (up to a year).')


def _behavior_problems(config: dict[str, Any]) -> list[str]:
    origins = {item.get('Id'): item for item in (config.get('Origins') or {}).get('Items') or []}
    behaviors = [('default', config.get('DefaultCacheBehavior') or {})] + [
        (item.get('PathPattern'), item) for item in (config.get('CacheBehaviors') or {}).get('Items') or []]
    problems = []
    for path, behavior in behaviors:
        if behavior.get('TargetOriginId') not in origins:
            problems.append(f'Behavior {path} targets missing origin {behavior.get("TargetOriginId")}.')
        if behavior.get('ViewerProtocolPolicy') not in ('redirect-to-https', 'https-only'):
            problems.append(f'Behavior {path} must redirect HTTP to HTTPS.')
    return problems


def _check_distribution() -> tuple[bool, str]:
    distribution = _distribution()
    if not distribution:
        return False, f'The distribution ({DIST_COMMENT}) was not found.'
    config = distribution.get('DistributionConfig') or {}
    oac, html, assets = _oac(), _cache_policy(HTML_POLICY), _cache_policy(ASSETS_POLICY)
    if not oac or not html or not assets:
        return False, 'Create the origin access control and cache policies first.'
    origins = {item.get('Id'): item for item in (config.get('Origins') or {}).get('Items') or []}
    expected = {SITE_ORIGIN: SITE_BUCKET, ASSETS_ORIGIN: ASSETS_BUCKET}
    for origin_id, bucket in expected.items():
        origin = origins.get(origin_id)
        if not origin or not (origin.get('DomainName') or '').startswith(f'{bucket}.s3.'):
            return False, f'Origin {origin_id} must point at {bucket}.'
        if origin.get('OriginAccessControlId') != oac.get('Id'):
            return False, f'Origin {origin_id} must use {OAC_NAME}.'
    problems = _behavior_problems(config)
    if problems:
        return False, problems[0]
    default = config.get('DefaultCacheBehavior') or {}
    paths = {item.get('PathPattern'): item for item in (config.get('CacheBehaviors') or {}).get('Items') or []}
    asset_behavior = paths.get(ASSETS_PATH) or {}
    if default.get('TargetOriginId') != SITE_ORIGIN or default.get('CachePolicyId') != html.get('Id'):
        return False, f'The default behavior must send pages to {SITE_ORIGIN} with {HTML_POLICY}.'
    if asset_behavior.get('TargetOriginId') != ASSETS_ORIGIN or asset_behavior.get('CachePolicyId') != assets.get('Id'):
        return False, f'{ASSETS_PATH} must go to {ASSETS_ORIGIN} with {ASSETS_POLICY}.'
    errors = {item.get('ErrorCode'): item for item in (config.get('CustomErrorResponses') or {}).get('Items') or []}
    if (errors.get(404) or {}).get('ResponsePagePath') != '/404.html':
        return False, 'A 404 must return /404.html.'
    if config.get('DefaultRootObject') != 'index.html' or config.get('PriceClass') != PRICE_CLASS:
        return False, f'The distribution must serve index.html for / and use {PRICE_CLASS}.'
    if not config.get('Enabled') or distribution.get('Status') != 'Deployed':
        return False, 'The distribution must be enabled and deployed.'
    return True, (f'{distribution["DomainName"]} sends {ASSETS_PATH} to {ASSETS_BUCKET} and everything else to '
                  f'{SITE_BUCKET}, both through {OAC_NAME}, with HTTPS redirects and a custom 404 page.')


def _oac_statement_problem(bucket: str, distribution_arn: str) -> str:
    policy = _bucket_policy(bucket) or {}
    for statement in policy.get('Statement', []):
        principal = statement.get('Principal') or {}
        actions = statement.get('Action')
        actions = actions if isinstance(actions, list) else [actions]
        condition = (statement.get('Condition') or {}).get('StringEquals') or {}
        if (statement.get('Effect') == 'Allow' and principal.get('Service') == 'cloudfront.amazonaws.com'
                and 's3:GetObject' in actions and statement.get('Resource') == f'arn:aws:s3:::{bucket}/*'
                and condition.get('AWS:SourceArn') == distribution_arn):
            return ''
    return (f'{bucket} needs a policy that allows cloudfront.amazonaws.com to get objects only for this '
            'distribution (AWS:SourceArn).')


def _check_bucket_policies() -> tuple[bool, str]:
    distribution = _distribution()
    if not distribution:
        return False, 'Create the distribution first.'
    for bucket in (SITE_BUCKET, ASSETS_BUCKET):
        problem = _oac_statement_problem(bucket, distribution['ARN'])
        if problem:
            return False, problem
    return True, f'{SITE_BUCKET} and {ASSETS_BUCKET} allow reads only from distribution {distribution["Id"]}.'


def _check_marked(step: str, prerequisite) -> tuple[bool, str]:
    ok, problem = prerequisite()
    if not ok:
        return False, problem
    marker = _marked(step)
    if not marker:
        return False, f'Run the {step} step.'
    return True, marker['message']


def _check_serve() -> tuple[bool, str]:
    return _check_marked('serve-through-edge', _check_bucket_policies)


def _check_invalidate() -> tuple[bool, str]:
    ok, problem = _check_marked('invalidate', _check_distribution)
    if ok and _object(SITE_BUCKET, 'index.html') != INDEX_V2:
        return False, f'{SITE_BUCKET}/index.html should be the new page.'
    return ok, problem


def _check_accelerator() -> tuple[bool, str]:
    accelerator, listener, groups = _game_setup()
    if not accelerator:
        return False, f'Accelerator {ACCELERATOR} was not found.'
    if not accelerator.get('Enabled') or len((accelerator.get('IpSets') or [{}])[0].get('IpAddresses') or []) != 2:
        return False, f'{ACCELERATOR} must be enabled with two static IP addresses.'
    if not listener or listener.get('Protocol') != 'UDP' or listener.get('PortRanges') != [
            {'FromPort': GAME_PORT, 'ToPort': GAME_PORT}]:
        return False, f'{ACCELERATOR} needs a UDP listener on port {GAME_PORT}.'
    for region in GAME_REGIONS:
        group = groups.get(region)
        if not group:
            return False, f'{ACCELERATOR} needs an endpoint group in {region}.'
        address = _elastic_ip(region)
        endpoints = [item.get('EndpointId') for item in group.get('EndpointDescriptions') or []]
        if not address or endpoints != [address['AllocationId']]:
            return False, f'The {region} endpoint group must point at the {EIP_NAME} Elastic IP in {region}.'
        if group.get('HealthCheckProtocol') != 'TCP' or group.get('HealthCheckPort') != GAME_PORT:
            return False, f'The {region} endpoint group must health check TCP port {GAME_PORT}.'
    return True, (f'{ACCELERATOR} listens on UDP {GAME_PORT} at '
                  f'{", ".join(accelerator["IpSets"][0]["IpAddresses"])} and routes to {" and ".join(GAME_REGIONS)}.')


def _check_failover() -> tuple[bool, str]:
    ok, problem = _check_marked('regional-failover', _check_accelerator)
    if not ok:
        return False, problem
    _accelerator_, _listener_, groups = _game_setup()
    if any(groups[region].get('TrafficDialPercentage') != 100 for region in GAME_REGIONS):
        return False, 'Set both traffic dials back to 100 after the drill.'
    return True, problem


def _check_choice() -> tuple[bool, str]:
    return _check_marked('choose-edge-service', _check_accelerator)


# ── Steps ────────────────────────────────────────────────────────────────────

def _put(bucket: str, key: str, body: str, content_type: str) -> None:
    _client('s3').put_object(Bucket=bucket, Key=key, Body=body.encode(), ContentType=content_type)


def step_private_origins() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    for bucket in (SITE_BUCKET, ASSETS_BUCKET):
        if not _bucket_exists(bucket):
            s3.create_bucket(Bucket=bucket)
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={flag: True for flag in BLOCK_ALL})
    _put(SITE_BUCKET, 'index.html', INDEX_V1, 'text/html')
    _put(SITE_BUCKET, '404.html', NOT_FOUND_PAGE, 'text/html')
    _put(ASSETS_BUCKET, ASSET_KEY, APP_CSS, 'text/css')
    try:
        unsigned = boto3.client('s3', endpoint_url=FlociClientFactory().endpoint_url, region_name=REGION,
                                config=Config(signature_version=UNSIGNED, s3={'addressing_style': 'path'}))
        unsigned.get_object(Bucket=SITE_BUCKET, Key='index.html')
        anonymous_read = 'served'
    except ClientError as exc:
        anonymous_read = f'refused ({_code(exc)})'
    except BotoCoreError:
        anonymous_read = 'not tried'
    response = {bucket: {'PublicAccessBlock': _public_access_block(bucket),
                         'Objects': [item['Key'] for item in s3.list_objects_v2(Bucket=bucket).get('Contents', [])]}
                for bucket in (SITE_BUCKET, ASSETS_BUCKET)}
    response['AnonymousRead'] = anonymous_read
    verified, message = _check_origins()
    if verified and anonymous_read == 'served':
        message += ' Floci still served an anonymous read; on AWS Block Public Access refuses it.'
    return _result('private-origins', ORIGINS_COMMAND, response, verified, message, started)


def _cache_policy_config(name: str, ttl: dict[str, int], comment: str) -> dict[str, Any]:
    return {
        'Name': name, 'Comment': comment, **ttl,
        'ParametersInCacheKeyAndForwardedToOrigin': {
            'EnableAcceptEncodingGzip': True, 'EnableAcceptEncodingBrotli': True,
            'HeadersConfig': {'HeaderBehavior': 'none'},
            'CookiesConfig': {'CookieBehavior': 'none'},
            'QueryStringsConfig': {'QueryStringBehavior': 'none'},
        },
    }


def _upsert_cache_policy(name: str, ttl: dict[str, int], comment: str) -> None:
    cloudfront = _client('cloudfront')
    config = _cache_policy_config(name, ttl, comment)
    existing = _cache_policy(name)
    if existing:
        etag = cloudfront.get_cache_policy(Id=existing['Id'])['ETag']
        cloudfront.update_cache_policy(Id=existing['Id'], IfMatch=etag, CachePolicyConfig=config)
    else:
        cloudfront.create_cache_policy(CachePolicyConfig=config)


def step_access_and_cache_policies() -> dict[str, Any]:
    started = time.perf_counter()
    cloudfront = _client('cloudfront')
    if not _oac():
        cloudfront.create_origin_access_control(OriginAccessControlConfig={
            'Name': OAC_NAME, 'Description': 'Edge delivery lab', 'SigningProtocol': 'sigv4',
            'SigningBehavior': 'always', 'OriginAccessControlOriginType': 's3'})
    _upsert_cache_policy(HTML_POLICY, HTML_TTL, 'Pages: 5 minutes by default')
    _upsert_cache_policy(ASSETS_POLICY, ASSETS_TTL, 'Fingerprinted assets: 1 day by default, up to 1 year')
    response = {'OriginAccessControl': _oac(),
                'CachePolicies': {name: (_cache_policy(name) or {}).get('CachePolicyConfig')
                                  for name in (HTML_POLICY, ASSETS_POLICY)}}
    verified, message = _check_policies()
    if verified:
        message += ' Floci stores the TTLs but never caches, so the step checked them.'
    return _result('access-and-cache-policies', POLICIES_COMMAND, response, verified, message, started)


def _distribution_config(caller_reference: str) -> dict[str, Any]:
    oac_id = (_oac() or {}).get('Id', '')
    html_id = (_cache_policy(HTML_POLICY) or {}).get('Id', '')
    assets_id = (_cache_policy(ASSETS_POLICY) or {}).get('Id', '')

    def origin(origin_id: str, bucket: str) -> dict[str, Any]:
        return {'Id': origin_id, 'DomainName': f'{bucket}.s3.{REGION}.amazonaws.com',
                'S3OriginConfig': {'OriginAccessIdentity': ''}, 'OriginAccessControlId': oac_id}

    return {
        'CallerReference': caller_reference,
        'Comment': DIST_COMMENT,
        'Enabled': True,
        'DefaultRootObject': 'index.html',
        'PriceClass': PRICE_CLASS,
        'HttpVersion': 'http2and3',
        'Origins': {'Quantity': 2, 'Items': [origin(SITE_ORIGIN, SITE_BUCKET), origin(ASSETS_ORIGIN, ASSETS_BUCKET)]},
        'DefaultCacheBehavior': {'TargetOriginId': SITE_ORIGIN, 'ViewerProtocolPolicy': 'redirect-to-https',
                                 'CachePolicyId': html_id, 'Compress': True},
        'CacheBehaviors': {'Quantity': 1, 'Items': [
            {'PathPattern': ASSETS_PATH, 'TargetOriginId': ASSETS_ORIGIN, 'ViewerProtocolPolicy': 'redirect-to-https',
             'CachePolicyId': assets_id, 'Compress': True}]},
        'CustomErrorResponses': {'Quantity': 1, 'Items': [
            {'ErrorCode': 404, 'ResponsePagePath': '/404.html', 'ResponseCode': '404', 'ErrorCachingMinTTL': 60}]},
    }


def step_distribution() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_origins()[0] or not _check_policies()[0]:
        return _result('distribution', DISTRIBUTION_COMMAND, {}, False,
                       'Create the origins, origin access control, and cache policies first.', started)
    cloudfront = _client('cloudfront')
    summary = _distribution_summary()
    if summary:
        current = cloudfront.get_distribution_config(Id=summary['Id'])
        config = _distribution_config(current['DistributionConfig']['CallerReference'])
        cloudfront.update_distribution(Id=summary['Id'], IfMatch=current['ETag'], DistributionConfig=config)
    else:
        cloudfront.create_distribution(DistributionConfig=_distribution_config(f'{LAB_KEY}-{time.time_ns()}'))
    _wait(lambda: (_distribution() or {}).get('Status') == 'Deployed', timeout=DEPLOY_TIMEOUT_SECONDS, delay=2)
    distribution = _distribution() or {}
    response = {key: distribution.get(key) for key in ('Id', 'ARN', 'DomainName', 'Status')}
    response['DistributionConfig'] = distribution.get('DistributionConfig')
    verified, message = _check_distribution()
    if verified:
        message += (' Floci accepts a behavior that targets a missing origin and never redirects to HTTPS, so the step '
                    'checked the behaviors.')
    return _result('distribution', DISTRIBUTION_COMMAND, response, verified, message, started)


def _oac_policy(bucket: str, distribution_arn: str) -> dict[str, Any]:
    return {
        'Version': '2012-10-17',
        'Statement': [{
            'Sid': 'AllowCloudFrontServicePrincipalReadOnly', 'Effect': 'Allow',
            'Principal': {'Service': 'cloudfront.amazonaws.com'}, 'Action': 's3:GetObject',
            'Resource': f'arn:aws:s3:::{bucket}/*',
            'Condition': {'StringEquals': {'AWS:SourceArn': distribution_arn}},
        }],
    }


def step_origin_bucket_policies() -> dict[str, Any]:
    started = time.perf_counter()
    distribution = _distribution()
    if not distribution:
        return _result('origin-bucket-policies', BUCKET_POLICY_COMMAND, {}, False, 'Create the distribution first.',
                       started)
    s3 = _client('s3')
    for bucket in (SITE_BUCKET, ASSETS_BUCKET):
        s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(_oac_policy(bucket, distribution['ARN'])))
    response = {bucket: _bucket_policy(bucket) for bucket in (SITE_BUCKET, ASSETS_BUCKET)}
    verified, message = _check_bucket_policies()
    if verified:
        message += ' Floci stores the policies without evaluating them for these reads, so the step checked them.'
    return _result('origin-bucket-policies', BUCKET_POLICY_COMMAND, response, verified, message, started)


def step_serve_through_edge() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_bucket_policies()
    if not ok:
        return _result('serve-through-edge', SERVE_COMMAND, {}, False, problem, started)
    distribution = _distribution() or {}
    host = distribution['DomainName']
    requests = {'/': _http('/', host), f'/{ASSET_KEY}': _http(f'/{ASSET_KEY}', host),
                '/no-such-page': _http('/no-such-page', host)}
    response = {'Host': host, 'Responses': {path: {'Status': status, 'Body': body[:200]}
                                             for path, (status, body) in requests.items()}}
    page, asset, missing = requests['/'], requests[f'/{ASSET_KEY}'], requests['/no-such-page']
    if page[0] != 200 or '<h1>Shop' not in page[1]:
        return _result('serve-through-edge', SERVE_COMMAND, response, False,
                       f'/ should return index.html from {SITE_BUCKET} (got {page[0]}).', started)
    if asset[0] != 200 or asset[1] != APP_CSS:
        return _result('serve-through-edge', SERVE_COMMAND, response, False,
                       f'/{ASSET_KEY} should come from {ASSETS_BUCKET} (got {asset[0]}).', started)
    if missing[0] != 404 or 'Page not found' not in missing[1]:
        return _result('serve-through-edge', SERVE_COMMAND, response, False,
                       f'A missing page should return /404.html with status 404 (got {missing[0]}).', started)
    message = (f'{host} returned index.html for /, {ASSET_KEY} from {ASSETS_BUCKET} through the {ASSETS_PATH} '
               'behavior, and the custom 404 page for a missing page.')
    _marker('serve-through-edge', {'message': message})
    verified, check = _check_serve()
    return _result('serve-through-edge', SERVE_COMMAND, response, verified, message if verified else check, started)


def step_invalidate() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_serve()
    if not ok:
        return _result('invalidate', INVALIDATE_COMMAND, {}, False, f'Serve through the edge first: {problem}', started)
    distribution = _distribution() or {}
    host, cloudfront = distribution['DomainName'], _client('cloudfront')
    _put(SITE_BUCKET, 'index.html', INDEX_V2, 'text/html')
    before = _http('/', host)
    invalidation = cloudfront.create_invalidation(DistributionId=distribution['Id'], InvalidationBatch={
        'Paths': {'Quantity': 1, 'Items': ['/index.html']}, 'CallerReference': f'{LAB_KEY}-{time.time_ns()}'})
    invalidation_id = invalidation['Invalidation']['Id']
    state: dict[str, Any] = {}

    def completed() -> bool:
        state.update(cloudfront.get_invalidation(DistributionId=distribution['Id'], Id=invalidation_id)['Invalidation'])
        return state.get('Status') == 'Completed'
    _wait(completed, timeout=DEPLOY_TIMEOUT_SECONDS, delay=2)
    after = _http('/', host)
    response = {'Invalidation': {key: state.get(key) for key in ('Id', 'Status', 'InvalidationBatch')},
                'BeforeInvalidation': {'Status': before[0], 'Body': before[1][:120]},
                'AfterInvalidation': {'Status': after[0], 'Body': after[1][:120]},
                'OnAws': f'Without the invalidation, edges would keep v1 for up to {HTML_TTL["DefaultTTL"]} seconds.'}
    if state.get('Status') != 'Completed' or after[0] != 200 or 'Shop v2' not in after[1]:
        return _result('invalidate', INVALIDATE_COMMAND, response, False,
                       'The invalidation should complete and the edge should serve the new page.', started)
    message = f'Invalidation {invalidation_id} completed and the edge serves the new page.'
    if 'Shop v2' in before[1]:
        message += (f' Floci does not cache, so v2 showed even before the invalidation; on AWS the edges would keep v1 '
                    f'for up to {HTML_TTL["DefaultTTL"]} seconds.')
    _marker('invalidate', {'message': message})
    verified, check = _check_invalidate()
    return _result('invalidate', INVALIDATE_COMMAND, response, verified, message if verified else check, started)


def _ensure_elastic_ip(region: str) -> dict[str, Any]:
    address = _elastic_ip(region)
    if not address:
        _client('ec2', region).allocate_address(Domain='vpc', TagSpecifications=[{
            'ResourceType': 'elastic-ip', 'Tags': [{'Key': 'Name', 'Value': EIP_NAME}, {'Key': 'Lab', 'Value': LAB_KEY}]}])
        address = _elastic_ip(region)
    return address or {}


def step_global_accelerator() -> dict[str, Any]:
    started = time.perf_counter()
    ga = _client('globalaccelerator', GA_REGION)
    addresses = {region: _ensure_elastic_ip(region) for region in GAME_REGIONS}
    accelerator = _accelerator()
    if not accelerator:
        accelerator = ga.create_accelerator(Name=ACCELERATOR, IpAddressType='IPV4', Enabled=True,
                                            IdempotencyToken=f'{LAB_KEY}-accelerator',
                                            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])['Accelerator']
    elif not accelerator.get('Enabled'):
        ga.update_accelerator(AcceleratorArn=accelerator['AcceleratorArn'], Enabled=True)
    listener = _listener(accelerator['AcceleratorArn'])
    ports = [{'FromPort': GAME_PORT, 'ToPort': GAME_PORT}]
    if not listener:
        listener = ga.create_listener(AcceleratorArn=accelerator['AcceleratorArn'], PortRanges=ports, Protocol='UDP',
                                      ClientAffinity='SOURCE_IP', IdempotencyToken=f'{LAB_KEY}-listener')['Listener']
    elif listener.get('Protocol') != 'UDP' or listener.get('PortRanges') != ports:
        listener = ga.update_listener(ListenerArn=listener['ListenerArn'], PortRanges=ports, Protocol='UDP',
                                      ClientAffinity='SOURCE_IP')['Listener']
    groups = _endpoint_groups(listener['ListenerArn'])
    for region in GAME_REGIONS:
        endpoints = [{'EndpointId': addresses[region].get('AllocationId', ''), 'Weight': 100}]
        settings = {'TrafficDialPercentage': 100, 'HealthCheckProtocol': 'TCP', 'HealthCheckPort': GAME_PORT,
                    'HealthCheckIntervalSeconds': 10, 'ThresholdCount': 3, 'EndpointConfigurations': endpoints}
        if region in groups:
            ga.update_endpoint_group(EndpointGroupArn=groups[region]['EndpointGroupArn'], **settings)
        else:
            ga.create_endpoint_group(ListenerArn=listener['ListenerArn'], EndpointGroupRegion=region,
                                     IdempotencyToken=f'{LAB_KEY}-{region}', **settings)
    accelerator, listener, groups = _game_setup()
    response = {'Accelerator': {key: (accelerator or {}).get(key) for key in ('Name', 'Status', 'IpSets', 'DnsName')},
                'Listener': listener,
                'EndpointGroups': {region: {key: group.get(key) for key in (
                    'EndpointGroupRegion', 'TrafficDialPercentage', 'HealthCheckProtocol', 'HealthCheckPort',
                    'EndpointDescriptions')} for region, group in groups.items()},
                'ElasticIps': {region: {key: address.get(key) for key in ('AllocationId', 'PublicIp')}
                               for region, address in addresses.items()}}
    verified, message = _check_accelerator()
    if verified:
        message += (' Floci passes no traffic, never runs health checks (every endpoint reports HEALTHY), and does not '
                    'check endpoint IDs, so the step checked the Elastic IPs.')
    return _result('global-accelerator', ACCELERATOR_COMMAND, response, verified, message, started)


def step_regional_failover() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_accelerator()
    if not ok:
        return _result('regional-failover', FAILOVER_COMMAND, {}, False, problem, started)
    ga = _client('globalaccelerator', GA_REGION)
    primary, secondary = GAME_REGIONS
    _accelerator_, _listener_, groups = _game_setup()
    primary_arn = groups[primary]['EndpointGroupArn']

    def split() -> dict[str, float]:
        current = _game_setup()[2]
        return traffic_split([(region, float(current[region].get('TrafficDialPercentage') or 0),
                               all(item.get('HealthState') != 'UNHEALTHY'
                                   for item in current[region].get('EndpointDescriptions') or []))
                              for region in (primary, secondary)])
    normal = split()
    ga.update_endpoint_group(EndpointGroupArn=primary_arn, TrafficDialPercentage=0)
    drained = split()
    try:
        ga.update_endpoint_group(EndpointGroupArn=primary_arn, TrafficDialPercentage=150)
        over_limit = 'accepted'
    except ClientError as exc:
        over_limit = f'refused ({_code(exc)})'
    ga.update_endpoint_group(EndpointGroupArn=primary_arn, TrafficDialPercentage=100)
    restored = split()
    response = {'NorthAmericanPlayers': {'Normal': normal, f'{primary}Dial0': drained, 'Restored': restored},
                'Dial150': over_limit}
    if drained.get(secondary) != 100 or normal.get(primary) != 100 or restored.get(primary) != 100:
        return _result('regional-failover', FAILOVER_COMMAND, response, False,
                       f'With the {primary} dial at 0, players should all reach {secondary}.', started)
    message = (f'With the {primary} dial at 0, North American players reached {secondary} on the same static IPs; '
               f'the dial is back at 100. A dial of 150 was {over_limit}.')
    _marker('regional-failover', {'message': message})
    verified, check = _check_failover()
    return _result('regional-failover', FAILOVER_COMMAND, response, verified, message if verified else check, started)


def step_choose_edge_service() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_accelerator()
    if not ok or not _check_distribution()[0]:
        return _result('choose-edge-service', CHOOSE_COMMAND, {}, False,
                       f'Create the distribution and the accelerator first. {problem if not ok else ""}'.strip(),
                       started)
    choices = [{'Workload': name, 'Protocol': protocol, 'Cacheable': cacheable, 'NeedsStaticIp': static,
                'Choice': choose_edge(protocol, cacheable, static)} for name, protocol, cacheable, static in SCENARIOS]
    accelerator = _accelerator() or {}
    ips = (accelerator.get('IpSets') or [{}])[0].get('IpAddresses') or []
    distribution = _distribution() or {}
    response = {'Choices': choices, 'Accelerator': {'StaticIps': ips, 'DnsName': accelerator.get('DnsName')},
                'Distribution': {'DomainName': distribution.get('DomainName')}}
    picked = {item['Workload']: item['Choice'] for item in choices}
    expected = {SCENARIOS[0][0]: 'CloudFront', SCENARIOS[1][0]: 'CloudFront', SCENARIOS[2][0]: 'Global Accelerator',
                SCENARIOS[3][0]: 'Global Accelerator', SCENARIOS[4][0]: 'Global Accelerator'}
    if picked != expected or len(ips) != 2:
        return _result('choose-edge-service', CHOOSE_COMMAND, response, False,
                       'The chooser should pick CloudFront for cacheable HTTPS and Global Accelerator for UDP, TCP, '
                       'and allowlisted IPs.', started)
    message = ('CloudFront serves the storefront and video; Global Accelerator fronts the game (UDP), the IoT fleet '
               f'(TCP) and the partner API (allowlisted IPs {", ".join(ips)}).')
    _marker('choose-edge-service', {'message': message})
    verified, check = _check_choice()
    return _result('choose-edge-service', CHOOSE_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'private-origins': step_private_origins,
    'access-and-cache-policies': step_access_and_cache_policies,
    'distribution': step_distribution,
    'origin-bucket-policies': step_origin_bucket_policies,
    'serve-through-edge': step_serve_through_edge,
    'invalidate': step_invalidate,
    'global-accelerator': step_global_accelerator,
    'regional-failover': step_regional_failover,
    'choose-edge-service': step_choose_edge_service,
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
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


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    checks = {
        'private-origins': lambda: _check_origins()[0],
        'access-and-cache-policies': lambda: _check_policies()[0],
        'distribution': lambda: _check_distribution()[0],
        'origin-bucket-policies': lambda: _check_bucket_policies()[0],
        'serve-through-edge': lambda: _check_serve()[0],
        'invalidate': lambda: _check_invalidate()[0],
        'global-accelerator': lambda: _check_accelerator()[0],
        'regional-failover': lambda: _check_failover()[0],
        'choose-edge-service': lambda: _check_choice()[0],
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


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Remove the accelerator, Elastic IPs, distribution, policies, origin access control, and buckets."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code == '404':
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    ga, cloudfront, s3 = _client('globalaccelerator', GA_REGION), _client('cloudfront'), _client('s3')
    try:
        accelerator, listener, groups = _game_setup()
    except (ClientError, BotoCoreError) as exc:
        accelerator, listener, groups = None, None, {}
        errors.append(f'accelerator lookup: {exc}')
    if accelerator:
        arn = accelerator['AcceleratorArn']
        attempt(f'accelerator-disable:{ACCELERATOR}', lambda: ga.update_accelerator(AcceleratorArn=arn, Enabled=False))
        for region, group in groups.items():
            attempt(f'endpoint-group:{region}', lambda group=group: ga.delete_endpoint_group(
                EndpointGroupArn=group['EndpointGroupArn']))
        if listener:
            attempt(f'listener:UDP {GAME_PORT}', lambda: ga.delete_listener(ListenerArn=listener['ListenerArn']))
        attempt(f'accelerator:{ACCELERATOR}', lambda: ga.delete_accelerator(AcceleratorArn=arn))
    for region in GAME_REGIONS:
        try:
            address = _elastic_ip(region)
        except (ClientError, BotoCoreError) as exc:
            address = None
            errors.append(f'elastic-ip lookup {region}: {exc}')
        if address:
            attempt(f'elastic-ip:{region}', lambda region=region, address=address: _client('ec2', region).release_address(
                AllocationId=address['AllocationId']))

    try:
        summary = _distribution_summary()
    except (ClientError, BotoCoreError) as exc:
        summary = None
        errors.append(f'distribution lookup: {exc}')
    if summary:
        distribution_id = summary['Id']

        def delete_distribution() -> None:
            current = cloudfront.get_distribution_config(Id=distribution_id)
            config = current['DistributionConfig']
            if config.get('Enabled'):
                config['Enabled'] = False
                cloudfront.update_distribution(Id=distribution_id, IfMatch=current['ETag'], DistributionConfig=config)
                _wait(lambda: cloudfront.get_distribution(Id=distribution_id)['Distribution']['Status'] == 'Deployed',
                      timeout=DEPLOY_TIMEOUT_SECONDS, delay=2)
            cloudfront.delete_distribution(Id=distribution_id, IfMatch=cloudfront.get_distribution(
                Id=distribution_id)['ETag'])
        attempt(f'distribution:{distribution_id}', delete_distribution)
    for name in (HTML_POLICY, ASSETS_POLICY):
        try:
            policy = _cache_policy(name)
        except (ClientError, BotoCoreError):
            policy = None
        if policy:
            attempt(f'cache-policy:{name}', lambda policy=policy: cloudfront.delete_cache_policy(
                Id=policy['Id'], IfMatch=cloudfront.get_cache_policy(Id=policy['Id'])['ETag']))
    try:
        oac = _oac()
    except (ClientError, BotoCoreError):
        oac = None
    if oac:
        attempt(f'origin-access-control:{OAC_NAME}', lambda: cloudfront.delete_origin_access_control(
            Id=oac['Id'], IfMatch=cloudfront.get_origin_access_control(Id=oac['Id'])['ETag']))
    for bucket in (SITE_BUCKET, ASSETS_BUCKET):
        try:
            exists = _bucket_exists(bucket)
        except (ClientError, BotoCoreError):
            exists = False
        if not exists:
            continue
        for item in s3.list_objects_v2(Bucket=bucket).get('Contents', []):
            attempt(f'object:{bucket}/{item["Key"]}', lambda bucket=bucket, item=item: s3.delete_object(
                Bucket=bucket, Key=item['Key']))
        attempt(f'bucket:{bucket}', lambda bucket=bucket: s3.delete_bucket(Bucket=bucket))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws globalaccelerator update-accelerator --accelerator-arn <arn> --no-enabled --region {GA_REGION}\n'
            f'aws globalaccelerator delete-endpoint-group --endpoint-group-arn <arn> --region {GA_REGION}   # each\n'
            f'aws globalaccelerator delete-listener --listener-arn <arn> --region {GA_REGION}\n'
            f'aws globalaccelerator delete-accelerator --accelerator-arn <arn> --region {GA_REGION}\n'
            'aws ec2 release-address --allocation-id <eipalloc>   # in each Region\n'
            'aws cloudfront update-distribution --id <id> --if-match <etag> ...   # Enabled false, then wait\n'
            'aws cloudfront delete-distribution --id <id> --if-match <etag>\n'
            f'aws cloudfront delete-cache-policy --id <id> --if-match <etag>   # {HTML_POLICY}, {ASSETS_POLICY}\n'
            'aws cloudfront delete-origin-access-control --id <id> --if-match <etag>\n'
            f'aws s3 rb s3://{SITE_BUCKET} --force && aws s3 rb s3://{ASSETS_BUCKET} --force'
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
    }
