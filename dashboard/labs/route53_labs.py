"""Route 53 routing and failover lab: a public hosted zone, health checks, failover primary and secondary records,
an alias at the zone apex, weighted and latency records, a failover drill, and a split-horizon private hosted zone.

Floci 2.3.0 stores hosted zones and record sets and keeps the fields of the simple, failover, weighted, latency and
alias policies. Its built-in DNS server answers only A records in private hosted zones (public zone names get
NXDOMAIN, and TestDNSAnswer is unsupported), its health checks never probe (every check reports success), and it skips most
of the validation AWS does. So each step checks the stored configuration against the AWS rules itself, and the
failover drill has the lab play the Route 53 resolver over the records Floci stored.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'route53'
LAB_KEY = 'routing-failover'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'

ZONE = 'lab-shop.example.com'
WWW = f'www.{ZONE}'
API = f'api.{ZONE}'
APP = f'app.{ZONE}'
DB = f'db.{ZONE}'
TTL = 60
REFERENCE_PREFIX = 'lab-route53-failover-'
ZONE_TAGS = [{'Key': 'Lab', 'Value': LAB_KEY}]

PRIMARY_IP = '192.0.2.10'      # us-east-1 web tier (a load balancer in a real build)
SECONDARY_IP = '198.51.100.20'  # us-west-2 standby
HEALTH_CHECKS = {
    'primary': {'ip': PRIMARY_IP, 'name': 'lab-r53-primary-us-east-1'},
    'secondary': {'ip': SECONDARY_IP, 'name': 'lab-r53-secondary-us-west-2'},
}
HEALTH_PATH = '/health'
REQUEST_INTERVAL = 30
FAILURE_THRESHOLD = 3

WEIGHTED = [('blue', '192.0.2.30', 90), ('green', '192.0.2.31', 10)]
LATENCY = [('us-east-1', '192.0.2.40'), ('eu-west-1', '203.0.113.40')]

VPC_NAME = 'lab-route53-vpc'
VPC_CIDR = '10.53.0.0/16'
INTERNAL_WWW_IP = '10.53.1.10'
DB_TARGET = 'lab-orders-db.c1abcdefgh.us-east-1.rds.amazonaws.com'

ROUTING_FIELDS = ('Weight', 'Region', 'Failover', 'GeoLocation', 'GeoProximityLocation', 'MultiValueAnswer',
                  'CidrRoutingConfig')
AWS_REGIONS = {
    'us-east-1', 'us-east-2', 'us-west-1', 'us-west-2', 'ca-central-1', 'eu-west-1', 'eu-west-2', 'eu-west-3',
    'eu-central-1', 'eu-north-1', 'eu-south-1', 'ap-south-1', 'ap-northeast-1', 'ap-northeast-2', 'ap-northeast-3',
    'ap-southeast-1', 'ap-southeast-2', 'ap-east-1', 'sa-east-1', 'me-south-1', 'af-south-1',
}

ZONE_COMMAND = (
    f'aws route53 create-hosted-zone --name {ZONE} --caller-reference "$(date +%s)" '
    '--hosted-zone-config Comment="Shop public zone (Route 53 lab)",PrivateZone=false\n'
    'aws route53 change-tags-for-resource --resource-type hostedzone --resource-id <zone-id> '
    f'--add-tags Key=Lab,Value={LAB_KEY}\n'
    f'aws route53 list-resource-record-sets --hosted-zone-id <zone-id>   # the NS and SOA records'
)
HEALTH_CHECK_COMMAND = (
    'aws route53 create-health-check --caller-reference "$(date +%s)-primary" --health-check-config '
    f'IPAddress={PRIMARY_IP},Port=80,Type=HTTP,ResourcePath={HEALTH_PATH},RequestInterval={REQUEST_INTERVAL},'
    f'FailureThreshold={FAILURE_THRESHOLD}\n'
    'aws route53 create-health-check --caller-reference "$(date +%s)-secondary" --health-check-config '
    f'IPAddress={SECONDARY_IP},Port=80,Type=HTTP,ResourcePath={HEALTH_PATH},RequestInterval={REQUEST_INTERVAL},'
    f'FailureThreshold={FAILURE_THRESHOLD}\n'
    'aws route53 change-tags-for-resource --resource-type healthcheck --resource-id <health-check-id> '
    f"--add-tags Key=Name,Value={HEALTH_CHECKS['primary']['name']}\n"
    'aws route53 get-health-check-status --health-check-id <health-check-id>'
)
FAILOVER_COMMAND = (
    'aws route53 change-resource-record-sets --hosted-zone-id <zone-id> --change-batch file://failover.json\n'
    f'aws route53 list-resource-record-sets --hosted-zone-id <zone-id> --query "ResourceRecordSets[?Name==\'{WWW}.\']"'
)
ALIAS_COMMAND = (
    'aws route53 change-resource-record-sets --hosted-zone-id <zone-id> --change-batch file://apex-alias.json\n'
    '# A CNAME is not allowed at the zone apex: the apex already has NS and SOA records.'
)
WEIGHTED_COMMAND = 'aws route53 change-resource-record-sets --hosted-zone-id <zone-id> --change-batch file://weighted.json'
LATENCY_COMMAND = 'aws route53 change-resource-record-sets --hosted-zone-id <zone-id> --change-batch file://latency.json'
DRILL_COMMAND = (
    'aws route53 get-health-check-status --health-check-id <primary-health-check-id>\n'
    f'dig +short {WWW}   # 192.0.2.10 while the primary is healthy\n'
    '# Make the primary fail its health check (stop the web tier, or block the health checkers), wait\n'
    f'# FailureThreshold x RequestInterval ({FAILURE_THRESHOLD} x {REQUEST_INTERVAL}s) plus the {TTL}s TTL, then:\n'
    f'dig +short {WWW}   # 198.51.100.20 from the secondary\n'
    f'dig +short {ZONE}  # the apex alias follows www'
)
PRIVATE_ZONE_COMMAND = (
    f'aws ec2 create-vpc --cidr-block {VPC_CIDR} --tag-specifications '
    f"'ResourceType=vpc,Tags=[{{Key=Name,Value={VPC_NAME}}}]'\n"
    'aws ec2 modify-vpc-attribute --vpc-id <vpc-id> --enable-dns-support \'{"Value":true}\'\n'
    'aws ec2 modify-vpc-attribute --vpc-id <vpc-id> --enable-dns-hostnames \'{"Value":true}\'\n'
    f'aws route53 create-hosted-zone --name {ZONE} --caller-reference "$(date +%s)-private" '
    '--vpc VPCRegion=us-east-1,VPCId=<vpc-id> --hosted-zone-config PrivateZone=true\n'
    'aws route53 change-resource-record-sets --hosted-zone-id <private-zone-id> --change-batch file://private.json'
)


def _plain(name: str, kind: str, value: str, **extra: Any) -> dict[str, Any]:
    return {'Name': name, 'Type': kind, 'TTL': TTL, 'ResourceRecords': [{'Value': value}], **extra}


def _failover_records(primary_check: str = '<primary-health-check-id>',
                      secondary_check: str = '<secondary-health-check-id>') -> list[dict[str, Any]]:
    return [
        _plain(WWW, 'A', PRIMARY_IP, SetIdentifier='primary-us-east-1', Failover='PRIMARY', HealthCheckId=primary_check),
        _plain(WWW, 'A', SECONDARY_IP, SetIdentifier='secondary-us-west-2', Failover='SECONDARY',
               HealthCheckId=secondary_check),
    ]


def _alias_record(zone_id: str = '<zone-id>') -> dict[str, Any]:
    return {'Name': ZONE, 'Type': 'A',
            'AliasTarget': {'HostedZoneId': zone_id, 'DNSName': WWW, 'EvaluateTargetHealth': True}}


def _weighted_records() -> list[dict[str, Any]]:
    return [_plain(API, 'A', ip, SetIdentifier=name, Weight=weight) for name, ip, weight in WEIGHTED]


def _latency_records() -> list[dict[str, Any]]:
    return [_plain(APP, 'A', ip, SetIdentifier=region, Region=region) for region, ip in LATENCY]


def _private_records() -> list[dict[str, Any]]:
    return [_plain(WWW, 'A', INTERNAL_WWW_IP), _plain(DB, 'CNAME', DB_TARGET)]


def _batch(records: list[dict[str, Any]]) -> str:
    return json.dumps({'Changes': [{'Action': 'UPSERT', 'ResourceRecordSet': record} for record in records]}, indent=2)


LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Route traffic and fail over between Regions with Route 53',
    'description': (
        'Create a public hosted zone, health-check a primary endpoint in us-east-1 and a standby in us-west-2, publish '
        'failover primary and secondary records, point the zone apex at them with an alias, split traffic with '
        'weighted records and route by latency, drill a failover, and give a VPC its own answers with a split-horizon '
        'private hosted zone.'
    ),
    'steps': [
        {
            'key': 'create-hosted-zone',
            'title': 'Create a public hosted zone',
            'command': ZONE_COMMAND,
            'explanation': (
                f'A hosted zone holds the records for one domain ({ZONE}) and costs $0.50 a month. Route 53 gives a '
                'public zone four name servers (the NS record) and a start of authority (SOA) record; you publish the '
                'zone by setting those four name servers at the domain\'s registrar, or as an NS record in the parent '
                'zone for a subdomain. Route 53 is also a registrar, and it is the only AWS service with a 100% '
                'availability SLA. How Floci emulates this: Floci creates the zone with four placeholder name servers '
                'and the NS and SOA records, and it refuses to delete a zone that still has other records, as AWS '
                'does. It answers no DNS queries for a public zone: its built-in DNS server returns NXDOMAIN for these '
                'names and answers only private hosted zones.'
            ),
        },
        {
            'key': 'create-health-checks',
            'title': 'Health-check the primary and standby endpoints',
            'command': HEALTH_CHECK_COMMAND,
            'explanation': (
                f'A Route 53 health check probes an endpoint from checkers in several Regions. This one requests '
                f'http://{PRIMARY_IP}{HEALTH_PATH} every {REQUEST_INTERVAL} seconds (10 seconds is the fast option, '
                f'at extra cost) and marks the endpoint unhealthy after {FAILURE_THRESHOLD} failures in a row '
                '(1 to 10). An endpoint is healthy when more than 18% of the checkers see a 2xx or 3xx answer within '
                'the timeout. Two other kinds exist: a calculated check combines child checks (healthy when at least N '
                'are), and a CloudWatch alarm check follows an alarm, which is how you health-check a private resource '
                'the public checkers cannot reach. Health checks also feed CloudWatch metrics, so an alarm can notify '
                'you. How Floci emulates this: Floci stores the checks but never probes. Every check reports "Success: '
                'HTTP Status Code 200" from a placeholder checker, even for these documentation addresses that nothing '
                'answers. It accepts intervals and thresholds AWS refuses, drops the Regions, Inverted, and calculated '
                'or alarm settings, and UpdateHealthCheck replaces the whole configuration with only the fields you '
                'send. The step checks the settings itself.'
            ),
        },
        {
            'key': 'failover-records',
            'title': 'Publish failover primary and secondary records',
            'command': FAILOVER_COMMAND,
            'explanation': (
                f'Failover routing is active-passive: Route 53 answers {WWW} with the PRIMARY record while its health '
                'check passes and with the SECONDARY record when it fails. Each record needs a set identifier, a record '
                f'name can have only one primary and one secondary, and a low TTL ({TTL} seconds) keeps resolvers from '
                'caching the old answer for long. This is the DNS half of the pilot light and warm standby disaster '
                'recovery strategies: the standby Region is ready, and DNS moves users to it. If the secondary had no '
                'health check, Route 53 would treat it as always healthy. How Floci emulates this: Floci stores the '
                'records with their failover role and health check, but it accepts two primaries, a health check ID '
                'that does not exist, and a record name that mixes routing policies. The step checks those rules itself.'
            ),
            'artifact_label': 'failover.json',
            'artifact': _batch(_failover_records()),
        },
        {
            'key': 'alias-apex',
            'title': 'Point the zone apex at www with an alias record',
            'command': ALIAS_COMMAND,
            'explanation': (
                f'Users type {ZONE} as often as {WWW}. A CNAME cannot sit at the zone apex, because the apex already '
                'has the NS and SOA records and a CNAME cannot share a name. An alias record can: it is a Route 53 '
                'extension that answers with the target\'s addresses, works at the apex, costs nothing to query for '
                'AWS targets, and follows the target\'s TTL. Alias targets are load balancers, CloudFront distributions, '
                'API Gateway, S3 website endpoints, Global Accelerator, VPC interface endpoints, and other records in '
                'the same zone, as here. With EvaluateTargetHealth the alias is only healthy while its target is, so '
                'the apex fails over with www. How Floci emulates this: Floci stores the alias, but it also accepts an '
                'alias to a name that does not exist and a CNAME at the apex. The step checks both itself.'
            ),
            'artifact_label': 'apex-alias.json',
            'artifact': _batch([_alias_record()]),
        },
        {
            'key': 'weighted-records',
            'title': 'Split traffic 90/10 with weighted records',
            'command': WEIGHTED_COMMAND,
            'explanation': (
                f'Weighted routing answers each query with one record in proportion to its weight: here the blue '
                f'release gets {WEIGHTED[0][2]}% of {API} and the green canary {WEIGHTED[1][2]}%. Weights run from 0 to '
                '255; weight 0 takes a record out of rotation without deleting it, and if every record has weight 0 '
                'Route 53 answers with all of them equally. Add health checks to weighted records and Route 53 skips '
                'unhealthy ones. Use it for canary and blue/green releases and for A/B tests. Resolvers cache answers '
                'for the TTL, so the split is only approximate over a few clients. How Floci emulates this: Floci '
                'stores the weights and set identifiers, but accepts a weight above 255 and a weighted record without '
                'a set identifier. The step checks the weights and works out the split itself.'
            ),
            'artifact_label': 'weighted.json',
            'artifact': _batch(_weighted_records()),
        },
        {
            'key': 'latency-records',
            'title': 'Route users to the lowest-latency Region',
            'command': LATENCY_COMMAND,
            'explanation': (
                f'Latency routing answers {APP} with the record whose Region has the lowest measured latency from the '
                'user\'s resolver, one record per Region. Know the other policies for the exam. Geolocation routes on '
                'where the user is (continent, country, or US state) for content rights, language, or data residency, '
                'and needs a default record for everyone else. Geoproximity routes on distance, with a bias to grow or '
                'shrink a Region\'s share, and is set up in Traffic Flow. Multivalue answer returns up to eight healthy '
                'records, a simple client-side spread that is not a load balancer. IP-based routing maps client CIDR '
                'blocks to records. Simple routing returns all values of one record with no health checks. How Floci '
                'emulates this: Floci stores latency records. It drops the GeoLocation and MultiValueAnswer fields '
                'from records (they come back as plain records), so this lab does not build those policies.'
            ),
            'artifact_label': 'latency.json',
            'artifact': _batch(_latency_records()),
        },
        {
            'key': 'failover-drill',
            'title': 'Drill a failover: primary down, then both down',
            'command': DRILL_COMMAND,
            'explanation': (
                'A failover is only real once it has been tested. When the primary health check fails, Route 53 '
                f'answers {WWW} with the secondary, and the apex alias follows. Users move after the health check '
                f'notices ({FAILURE_THRESHOLD} failures x {REQUEST_INTERVAL} seconds) plus up to one TTL of caching. '
                'If the secondary is unhealthy too, Route 53 returns the primary: it fails open rather than returning '
                'nothing. Weighted and latency groups do the same when every record is unhealthy. How Floci emulates '
                'this: Floci never fails a health check and answers no DNS queries for public zones, so the lab plays the Route 53 '
                'resolver. It reads the records and health checks Floci stored and the status Floci reports, then '
                'works out the answers with the primary healthy, with the primary failing, and with both failing.'
            ),
        },
        {
            'key': 'private-hosted-zone',
            'title': 'Give a VPC its own answers with a private hosted zone',
            'command': PRIVATE_ZONE_COMMAND,
            'explanation': (
                f'A private hosted zone answers only inside the VPCs associated with it. Giving it the same name as '
                f'the public zone is split-horizon DNS: inside {VPC_NAME}, {WWW} resolves to the internal address '
                f'{INTERNAL_WWW_IP}, while the internet still gets the public failover records, and {DB} exists only '
                'inside. The VPC needs both enableDnsSupport and enableDnsHostnames turned on. Associate more VPCs, '
                'including VPCs in other Regions and accounts, to share the zone, and use Route 53 Resolver endpoints '
                'to answer these names from on premises over VPN or Direct Connect. How Floci emulates this: Floci '
                'stores the zone and its VPC associations, but accepts a VPC that does not exist and a VPC with DNS '
                'hostnames turned off, so the step checks the VPC itself. Its built-in DNS server does answer A '
                f'records in private zones ({WWW} returned {INTERNAL_WWW_IP}), but to any client on its Docker network '
                'rather than only the associated VPC, and with every value whatever the routing policy; the CNAME '
                'for db got an empty answer.'
            ),
            'artifact_label': 'private.json',
            'artifact': _batch(_private_records()),
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


def _marker(step: str, value: Any = True) -> None:
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


# ── Lookups ──────────────────────────────────────────────────────────────────

def _name(value: str | None) -> str:
    return (value or '').rstrip('.').lower()


def _short(zone_id: str) -> str:
    return (zone_id or '').rsplit('/', 1)[-1]


def _zone(private: bool = False) -> dict[str, Any] | None:
    zones = _client('route53').list_hosted_zones_by_name(DNSName=ZONE).get('HostedZones', [])
    return next((zone for zone in zones if _name(zone.get('Name')) == ZONE
                 and bool(zone.get('Config', {}).get('PrivateZone')) == private), None)


def _records(zone_id: str) -> list[dict[str, Any]]:
    route53 = _client('route53')
    records: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {'HostedZoneId': zone_id}
    for _ in range(50):
        page = route53.list_resource_record_sets(**kwargs)
        records.extend(page.get('ResourceRecordSets', []))
        if not page.get('IsTruncated') or not page.get('NextRecordName'):
            break
        kwargs = {'HostedZoneId': zone_id, 'StartRecordName': page['NextRecordName'],
                  'StartRecordType': page.get('NextRecordType')}
        if page.get('NextRecordIdentifier'):
            kwargs['StartRecordIdentifier'] = page['NextRecordIdentifier']
    seen, unique = set(), []
    for record in records:
        key = (_name(record.get('Name')), record.get('Type'), record.get('SetIdentifier'))
        if key not in seen:
            seen.add(key)
            unique.append(record)
    return unique


def _named(records: list[dict[str, Any]], name: str, kind: str | None = None) -> list[dict[str, Any]]:
    return [record for record in records if _name(record.get('Name')) == name
            and (kind is None or record.get('Type') == kind)]


def _health_checks() -> list[dict[str, Any]]:
    route53 = _client('route53')
    checks: list[dict[str, Any]] = []
    kwargs: dict[str, Any] = {}
    for _ in range(50):
        page = route53.list_health_checks(**kwargs)
        checks.extend(page.get('HealthChecks', []))
        if not page.get('IsTruncated') or not page.get('NextMarker'):
            break
        kwargs = {'Marker': page['NextMarker']}
    return checks


def _health_check(role: str) -> dict[str, Any] | None:
    prefix = f'{REFERENCE_PREFIX}{role}-'
    return next((check for check in _health_checks() if (check.get('CallerReference') or '').startswith(prefix)), None)


def _values(record: dict[str, Any]) -> list[str]:
    return [item.get('Value') for item in record.get('ResourceRecords') or []]


def _vpc() -> dict[str, Any] | None:
    vpcs = _client('ec2').describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_NAME]}]).get('Vpcs', [])
    return vpcs[0] if vpcs else None


def _vpc_attribute(vpc_id: str, attribute: str) -> bool:
    response = _client('ec2').describe_vpc_attribute(VpcId=vpc_id, Attribute=attribute)
    key = attribute[0].upper() + attribute[1:]
    return bool(response.get(key, {}).get('Value'))


def _need_zone() -> tuple[dict[str, Any] | None, str]:
    zone = _zone()
    return zone, '' if zone else 'The public hosted zone was not found. Run "Create a public hosted zone" first.'


def _converge(zone_id: str, name: str, kind: str, desired: list[dict[str, Any]]) -> None:
    """Make the records of one name and type exactly the desired set: delete strays, then upsert."""
    wanted = {record.get('SetIdentifier') for record in desired}
    changes = [{'Action': 'DELETE', 'ResourceRecordSet': record}
               for record in _named(_records(zone_id), name, kind) if record.get('SetIdentifier') not in wanted]
    changes += [{'Action': 'UPSERT', 'ResourceRecordSet': record} for record in desired]
    if changes:
        _client('route53').change_resource_record_sets(
            HostedZoneId=zone_id, ChangeBatch={'Comment': f'Route 53 lab: {name} {kind}', 'Changes': changes})


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_zone() -> tuple[bool, str]:
    zone, error = _need_zone()
    if not zone:
        return False, error
    apex = _named(_records(zone['Id']), ZONE)
    name_servers = [value for record in apex if record.get('Type') == 'NS' for value in _values(record)]
    if not name_servers:
        return False, f'{ZONE} has no NS record.'
    if not any(record.get('Type') == 'SOA' for record in apex):
        return False, f'{ZONE} has no SOA record.'
    return True, (f"Public hosted zone {_short(zone['Id'])} for {ZONE} exists with {len(name_servers)} name servers "
                  f"({', '.join(name_servers)}) and an SOA record.")


def _health_check_problems(check: dict[str, Any] | None, ip: str) -> list[str]:
    if not check:
        return ['not found']
    config = check.get('HealthCheckConfig') or {}
    problems = []
    if config.get('Type') not in {'HTTP', 'HTTPS'}:
        problems.append(f"type is {config.get('Type')}, not HTTP")
    if config.get('IPAddress') != ip:
        problems.append(f"checks {config.get('IPAddress')}, not {ip}")
    if config.get('Port', 80) != 80:
        problems.append(f"port is {config.get('Port')}, not 80")
    if config.get('ResourcePath') != HEALTH_PATH:
        problems.append(f"path is {config.get('ResourcePath')}, not {HEALTH_PATH}")
    interval = config.get('RequestInterval', 30)
    if interval not in (10, 30):
        problems.append(f'request interval {interval}s is not 10 or 30 (AWS refuses it)')
    elif interval != REQUEST_INTERVAL:
        problems.append(f'request interval is {interval}s, not {REQUEST_INTERVAL}s')
    threshold = config.get('FailureThreshold', 3)
    if not 1 <= threshold <= 10:
        problems.append(f'failure threshold {threshold} is outside 1 to 10 (AWS refuses it)')
    elif threshold != FAILURE_THRESHOLD:
        problems.append(f'failure threshold is {threshold}, not {FAILURE_THRESHOLD}')
    return problems


def _check_health_checks() -> tuple[bool, str]:
    issues = []
    for role, spec in HEALTH_CHECKS.items():
        problems = _health_check_problems(_health_check(role), spec['ip'])
        if problems:
            issues.append(f"{role} health check: {'; '.join(problems)}")
    if issues:
        return False, ' '.join(issues)
    return True, (f'HTTP health checks probe {PRIMARY_IP}{HEALTH_PATH} and {SECONDARY_IP}{HEALTH_PATH} every '
                  f'{REQUEST_INTERVAL}s and fail after {FAILURE_THRESHOLD} misses.')


def _check_failover() -> tuple[bool, str]:
    zone, error = _need_zone()
    if not zone:
        return False, error
    records = _named(_records(zone['Id']), WWW, 'A')
    if not records:
        return False, f'{WWW} has no A records.'
    mixed = [record.get('SetIdentifier') or '(none)' for record in records if not record.get('Failover')]
    if mixed:
        return False, f"{WWW} mixes routing policies: {', '.join(mixed)} is not a failover record."
    if any(not record.get('SetIdentifier') for record in records):
        return False, 'Every failover record needs a set identifier.'
    by_role: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        by_role.setdefault(record['Failover'], []).append(record)
    if len(by_role.get('PRIMARY', [])) != 1 or len(by_role.get('SECONDARY', [])) != 1:
        counts = {role: len(items) for role, items in by_role.items()}
        return False, f'{WWW} needs exactly one PRIMARY and one SECONDARY record (found {counts}).'
    existing = {check['Id'] for check in _health_checks()}
    for role, ip in (('PRIMARY', PRIMARY_IP), ('SECONDARY', SECONDARY_IP)):
        record = by_role[role][0]
        check = _health_check(role.lower())
        if _values(record) != [ip]:
            return False, f'The {role} record answers {_values(record)}, not {ip}.'
        if not record.get('HealthCheckId'):
            return False, f'The {role} record has no health check, so Route 53 would always treat it as healthy.'
        if record['HealthCheckId'] not in existing:
            return False, f"The {role} record uses health check {record['HealthCheckId']}, which does not exist."
        if not check or record['HealthCheckId'] != check['Id']:
            return False, f'The {role} record is not attached to the {role.lower()} health check.'
        if record.get('TTL', 0) > TTL:
            return False, f"The {role} record's TTL is {record.get('TTL')}s; keep it at {TTL}s or less to fail over fast."
    return True, (f'{WWW} has a PRIMARY record ({PRIMARY_IP}) and a SECONDARY record ({SECONDARY_IP}), each with '
                  f'its health check and a {TTL}s TTL.')


def _check_alias() -> tuple[bool, str]:
    zone, error = _need_zone()
    if not zone:
        return False, error
    records = _records(zone['Id'])
    apex = _named(records, ZONE)
    if any(record.get('Type') == 'CNAME' for record in apex):
        return False, f'{ZONE} has a CNAME record; a CNAME cannot sit at the zone apex (AWS refuses it).'
    alias = next((record for record in apex if record.get('Type') == 'A' and record.get('AliasTarget')), None)
    if not alias:
        return False, f'{ZONE} has no A alias record.'
    target = alias['AliasTarget']
    if _name(target.get('DNSName')) != WWW or _short(target.get('HostedZoneId')) != _short(zone['Id']):
        return False, f"The apex alias points at {target.get('DNSName')}, not {WWW} in this zone."
    if not _named(records, WWW, 'A'):
        return False, f'The apex alias targets {WWW}, which has no A records (AWS refuses an alias to a missing name).'
    if not target.get('EvaluateTargetHealth'):
        return False, 'EvaluateTargetHealth is off, so the apex would not fail over with www.'
    return True, f'{ZONE} is an A alias to {WWW} with EvaluateTargetHealth, and there is no CNAME at the apex.'


def _weighted_shares(records: list[dict[str, Any]]) -> dict[str, float]:
    total = sum(record.get('Weight', 0) for record in records)
    if not total:
        return {record['SetIdentifier']: round(100 / len(records), 1) for record in records}
    return {record['SetIdentifier']: round(100 * record.get('Weight', 0) / total, 1) for record in records}


def _check_weighted() -> tuple[bool, str]:
    zone, error = _need_zone()
    if not zone:
        return False, error
    records = _named(_records(zone['Id']), API, 'A')
    if not records:
        return False, f'{API} has no A records.'
    if any('Weight' not in record for record in records):
        return False, f'{API} mixes routing policies: every record needs a weight.'
    identifiers = [record.get('SetIdentifier') for record in records]
    if not all(identifiers) or len(set(identifiers)) != len(identifiers):
        return False, 'Every weighted record needs its own set identifier.'
    out_of_range = [record['SetIdentifier'] for record in records if not 0 <= record['Weight'] <= 255]
    if out_of_range:
        return False, f"Weights must be 0 to 255 (AWS refuses others): {', '.join(out_of_range)}."
    expected = {name: (ip, weight) for name, ip, weight in WEIGHTED}
    actual = {record['SetIdentifier']: (_values(record)[0] if _values(record) else None, record['Weight'])
              for record in records}
    if actual != expected:
        return False, f'{API} weighted records are {actual}, expected {expected}.'
    shares = _weighted_shares(records)
    return True, f"{API} splits {', '.join(f'{name} {share:g}%' for name, share in shares.items())}."


def _check_latency() -> tuple[bool, str]:
    zone, error = _need_zone()
    if not zone:
        return False, error
    records = _named(_records(zone['Id']), APP, 'A')
    if not records:
        return False, f'{APP} has no A records.'
    if any(not record.get('Region') for record in records):
        return False, f'{APP} mixes routing policies: every record needs a Region.'
    regions = [record['Region'] for record in records]
    if len(set(regions)) != len(regions):
        return False, 'Latency records need one record per Region.'
    unknown = [region for region in regions if region not in AWS_REGIONS]
    if unknown:
        return False, f"Not an AWS Region: {', '.join(unknown)}."
    actual = {record['Region']: _values(record) for record in records}
    expected = {region: [ip] for region, ip in LATENCY}
    if actual != expected:
        return False, f'{APP} latency records are {actual}, expected {expected}.'
    return True, f"{APP} answers by Region: {', '.join(f'{region} {ip}' for region, ip in LATENCY)}."


# ── The resolver the drill plays ─────────────────────────────────────────────

def _healthy(record: dict[str, Any], health: dict[str, bool]) -> bool:
    check = record.get('HealthCheckId')
    return True if not check else health.get(check, True)


def _answer(records: list[dict[str, Any]], name: str, health: dict[str, bool], depth: int = 0) -> dict[str, Any]:
    """Route 53's answer for an A query: values, the record that gave them, and whether that answer is healthy."""
    group = _named(records, name, 'A')
    if not group or depth > 4:
        return {'values': [], 'record': None, 'healthy': False}
    alias = next((record for record in group if record.get('AliasTarget')), None)
    if alias:
        target = _answer(records, _name(alias['AliasTarget'].get('DNSName')), health, depth + 1)
        healthy = target['healthy'] if alias['AliasTarget'].get('EvaluateTargetHealth') else True
        return {'values': target['values'], 'record': f"alias to {target['record']}", 'healthy': healthy}
    if any(record.get('Failover') for record in group):
        primary = next((record for record in group if record.get('Failover') == 'PRIMARY'), None)
        secondary = next((record for record in group if record.get('Failover') == 'SECONDARY'), None)
        if primary and _healthy(primary, health):
            chosen, healthy = primary, True
        elif secondary and _healthy(secondary, health):
            chosen, healthy = secondary, True
        else:
            chosen, healthy = primary or secondary, False  # both unhealthy: Route 53 returns the primary
        return {'values': _values(chosen), 'record': f"{chosen['Failover']} ({chosen.get('SetIdentifier')})",
                'healthy': healthy}
    healthy_records = [record for record in group if _healthy(record, health)]
    pool = healthy_records or group
    return {'values': sorted({value for record in pool for value in _values(record)}),
            'record': ', '.join(record.get('SetIdentifier') or 'simple' for record in pool),
            'healthy': bool(healthy_records)}


def _floci_health(check_id: str) -> dict[str, Any]:
    observations = _client('route53').get_health_check_status(HealthCheckId=check_id).get('HealthCheckObservations', [])
    statuses = [item.get('StatusReport', {}).get('Status') or '' for item in observations]
    checkers = [item.get('IPAddress') for item in observations]
    healthy = bool(statuses) and sum(status.startswith('Success') for status in statuses) / len(statuses) > 0.18
    return {'healthy': healthy, 'statuses': statuses, 'checkers': checkers}


def _fingerprint(records: list[dict[str, Any]]) -> str:
    relevant = [record for record in records if _name(record.get('Name')) in {ZONE, WWW, API}
                and record.get('Type') == 'A']
    return json.dumps(sorted(relevant, key=lambda record: (record['Name'], record.get('SetIdentifier') or '')),
                      sort_keys=True, default=str)


def _drill(records: list[dict[str, Any]], primary_id: str, secondary_id: str,
           reported: dict[str, bool]) -> dict[str, Any]:
    scenarios = {
        'AsFlociReports': reported,
        'PrimaryFailing': {primary_id: False, secondary_id: True},
        'BothFailing': {primary_id: False, secondary_id: False},
    }
    results = {}
    for label, health in scenarios.items():
        www, apex = _answer(records, WWW, health), _answer(records, ZONE, health)
        results[label] = {
            'Health': {'primary': health.get(primary_id), 'secondary': health.get(secondary_id)},
            WWW: {'Answer': www['values'], 'From': www['record']},
            ZONE: {'Answer': apex['values'], 'From': apex['record']},
        }
    return results


def _check_drill() -> tuple[bool, str]:
    marker = _marked('failover-drill')
    if not marker:
        return False, 'Run the failover drill.'
    zone = _zone()
    if not zone or marker.get('Fingerprint') != _fingerprint(_records(zone['Id'])):
        return False, 'The records changed since the drill; run it again.'
    return True, 'The drill answered the primary, then the secondary, then the primary again with both failing.'


def _check_private() -> tuple[bool, str]:
    vpc = _vpc()
    if not vpc:
        return False, f'VPC {VPC_NAME} not found.'
    for attribute in ('enableDnsSupport', 'enableDnsHostnames'):
        if not _vpc_attribute(vpc['VpcId'], attribute):
            return False, f"{attribute} is off for {vpc['VpcId']}; a private hosted zone needs it on (Floci does not)."
    zone = _zone(private=True)
    if not zone:
        return False, f'No private hosted zone named {ZONE}.'
    vpcs = _client('route53').get_hosted_zone(Id=zone['Id']).get('VPCs', [])
    if not any(item.get('VPCId') == vpc['VpcId'] and item.get('VPCRegion') == 'us-east-1' for item in vpcs):
        return False, f"The private zone is not associated with {vpc['VpcId']} in us-east-1."
    records = _records(zone['Id'])
    www = _named(records, WWW, 'A')
    db = _named(records, DB, 'CNAME')
    if len(www) != 1 or _values(www[0]) != [INTERNAL_WWW_IP] or any(field in www[0] for field in ROUTING_FIELDS):
        return False, f'Inside the VPC {WWW} should be one simple A record for {INTERNAL_WWW_IP}.'
    if len(db) != 1 or [_name(value) for value in _values(db[0])] != [DB_TARGET]:
        return False, f'{DB} should be a CNAME to {DB_TARGET}.'
    return True, (f"Private zone {_short(zone['Id'])} for {ZONE} is associated with {vpc['VpcId']} (DNS support and "
                  f'hostnames on): inside the VPC {WWW} is {INTERNAL_WWW_IP} and {DB} exists.')


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_hosted_zone() -> dict[str, Any]:
    started = time.perf_counter()
    route53 = _client('route53')
    zone = _zone()
    if not zone:
        zone = route53.create_hosted_zone(
            Name=ZONE, CallerReference=f'{REFERENCE_PREFIX}public-{time.time_ns()}',
            HostedZoneConfig={'Comment': 'Shop public zone (Route 53 lab)', 'PrivateZone': False},
        )['HostedZone']
    route53.change_tags_for_resource(ResourceType='hostedzone', ResourceId=_short(zone['Id']), AddTags=ZONE_TAGS)
    details = route53.get_hosted_zone(Id=zone['Id'])
    response = {
        'HostedZone': details.get('HostedZone', zone),
        'NameServers': details.get('DelegationSet', {}).get('NameServers', []),
        'DefaultRecords': [record for record in _named(_records(zone['Id']), ZONE) if record.get('Type') in {'NS', 'SOA'}],
    }
    verified, message = _check_zone()
    return _result('create-hosted-zone', ZONE_COMMAND, response, verified, message, started)


def step_create_health_checks() -> dict[str, Any]:
    started = time.perf_counter()
    route53 = _client('route53')
    created = {}
    for role, spec in HEALTH_CHECKS.items():
        check = _health_check(role)
        if check and _health_check_problems(check, spec['ip']):
            route53.delete_health_check(HealthCheckId=check['Id'])
            check = None
        if not check:
            check = route53.create_health_check(
                CallerReference=f'{REFERENCE_PREFIX}{role}-{time.time_ns()}',
                HealthCheckConfig={'IPAddress': spec['ip'], 'Port': 80, 'Type': 'HTTP', 'ResourcePath': HEALTH_PATH,
                                   'RequestInterval': REQUEST_INTERVAL, 'FailureThreshold': FAILURE_THRESHOLD},
            )['HealthCheck']
        route53.change_tags_for_resource(ResourceType='healthcheck', ResourceId=check['Id'],
                                         AddTags=[{'Key': 'Name', 'Value': spec['name']},
                                                  {'Key': 'Lab', 'Value': LAB_KEY}])
        created[role] = {'Id': check['Id'], 'HealthCheckConfig': check.get('HealthCheckConfig'),
                         'Status': _floci_health(check['Id'])}
    verified, message = _check_health_checks()
    if verified and all(item['Status']['healthy'] for item in created.values()):
        message += (' Floci reports both healthy without probing: nothing answers at these documentation addresses, '
                    'so on AWS both checks would fail.')
    return _result('create-health-checks', HEALTH_CHECK_COMMAND, {'HealthChecks': created}, verified, message, started)


def step_failover_records() -> dict[str, Any]:
    started = time.perf_counter()
    zone, error = _need_zone()
    primary, secondary = _health_check('primary'), _health_check('secondary')
    if not zone or not primary or not secondary:
        return _result('failover-records', FAILOVER_COMMAND, {}, False,
                       error or 'Create the health checks first: the failover records use them.', started)
    _converge(zone['Id'], WWW, 'A', _failover_records(primary['Id'], secondary['Id']))
    verified, message = _check_failover()
    response = {'ResourceRecordSets': _named(_records(zone['Id']), WWW, 'A')}
    return _result('failover-records', FAILOVER_COMMAND, response, verified, message, started)


def step_alias_apex() -> dict[str, Any]:
    started = time.perf_counter()
    zone, error = _need_zone()
    if not zone:
        return _result('alias-apex', ALIAS_COMMAND, {}, False, error, started)
    records = _records(zone['Id'])
    if not _named(records, WWW, 'A'):
        return _result('alias-apex', ALIAS_COMMAND, {}, False,
                       f'Publish the failover records first: the alias targets {WWW}.', started)
    _converge(zone['Id'], ZONE, 'CNAME', [])
    _converge(zone['Id'], ZONE, 'A', [_alias_record(_short(zone['Id']))])
    verified, message = _check_alias()
    response = {'ResourceRecordSets': [record for record in _named(_records(zone['Id']), ZONE)
                                       if record.get('Type') not in {'NS', 'SOA'}]}
    return _result('alias-apex', ALIAS_COMMAND, response, verified, message, started)


def step_weighted_records() -> dict[str, Any]:
    started = time.perf_counter()
    zone, error = _need_zone()
    if not zone:
        return _result('weighted-records', WEIGHTED_COMMAND, {}, False, error, started)
    _converge(zone['Id'], API, 'A', _weighted_records())
    records = _named(_records(zone['Id']), API, 'A')
    verified, message = _check_weighted()
    response = {'ResourceRecordSets': records, 'TrafficShare': _weighted_shares(records) if records else {}}
    return _result('weighted-records', WEIGHTED_COMMAND, response, verified, message, started)


def step_latency_records() -> dict[str, Any]:
    started = time.perf_counter()
    zone, error = _need_zone()
    if not zone:
        return _result('latency-records', LATENCY_COMMAND, {}, False, error, started)
    _converge(zone['Id'], APP, 'A', _latency_records())
    verified, message = _check_latency()
    response = {'ResourceRecordSets': _named(_records(zone['Id']), APP, 'A')}
    return _result('latency-records', LATENCY_COMMAND, response, verified, message, started)


def step_failover_drill() -> dict[str, Any]:
    started = time.perf_counter()
    for check in (_check_failover, _check_alias):
        ok, problem = check()
        if not ok:
            return _result('failover-drill', DRILL_COMMAND, {}, False,
                           f'Fix the records before the drill: {problem}', started)
    zone = _zone()
    records = _records(zone['Id'])
    primary, secondary = _health_check('primary'), _health_check('secondary')
    floci = {'primary': _floci_health(primary['Id']), 'secondary': _floci_health(secondary['Id'])}
    reported = {primary['Id']: floci['primary']['healthy'], secondary['Id']: floci['secondary']['healthy']}
    drill = _drill(records, primary['Id'], secondary['Id'], reported)
    expected = {
        'PrimaryFailing': [SECONDARY_IP],
        'BothFailing': [PRIMARY_IP],
    }
    if reported[primary['Id']]:
        expected['AsFlociReports'] = [PRIMARY_IP]
    wrong = [label for label, answer in expected.items()
             if drill[label][WWW]['Answer'] != answer or drill[label][ZONE]['Answer'] != answer]
    response = {
        'FlociHealthCheckStatus': floci,
        'Scenarios': drill,
        'WeightedSplit': _weighted_shares(_named(records, API, 'A')) if _named(records, API, 'A') else {},
        'TimeToFailOver': (f'up to {FAILURE_THRESHOLD} x {REQUEST_INTERVAL}s for the health check plus {TTL}s of '
                           'resolver caching'),
    }
    if wrong:
        return _result('failover-drill', DRILL_COMMAND, response, False,
                       f"Unexpected answers in: {', '.join(wrong)}.", started)
    _marker('failover-drill', {'Fingerprint': _fingerprint(records)})
    verified, message = _check_drill()
    checkers = ', '.join(sorted({ip for item in floci.values() for ip in item['checkers'] if ip}))
    message += (f' Floci reported the primary {"healthy" if reported[primary["Id"]] else "unhealthy"} from checker '
                f'{checkers or "(none)"} without probing {PRIMARY_IP}, and its DNS server does not answer the public zone, so '
                'the lab worked out the answers from the stored records.')
    return _result('failover-drill', DRILL_COMMAND, response, verified, message, started)


def step_private_hosted_zone() -> dict[str, Any]:
    started = time.perf_counter()
    ec2, route53 = _client('ec2'), _client('route53')
    vpc = _vpc()
    if not vpc:
        vpc = ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=[
            {'ResourceType': 'vpc', 'Tags': [{'Key': 'Name', 'Value': VPC_NAME}, {'Key': 'Lab', 'Value': LAB_KEY}]},
        ])['Vpc']
    ec2.modify_vpc_attribute(VpcId=vpc['VpcId'], EnableDnsSupport={'Value': True})
    ec2.modify_vpc_attribute(VpcId=vpc['VpcId'], EnableDnsHostnames={'Value': True})
    association = {'VPCRegion': 'us-east-1', 'VPCId': vpc['VpcId']}
    zone = _zone(private=True)
    if not zone:
        zone = route53.create_hosted_zone(
            Name=ZONE, CallerReference=f'{REFERENCE_PREFIX}private-{time.time_ns()}', VPC=association,
            HostedZoneConfig={'Comment': 'Shop internal answers (Route 53 lab)', 'PrivateZone': True},
        )['HostedZone']
    else:
        vpcs = route53.get_hosted_zone(Id=zone['Id']).get('VPCs', [])
        if not any(item.get('VPCId') == vpc['VpcId'] for item in vpcs):
            route53.associate_vpc_with_hosted_zone(HostedZoneId=zone['Id'], VPC=association)
    route53.change_tags_for_resource(ResourceType='hostedzone', ResourceId=_short(zone['Id']), AddTags=ZONE_TAGS)
    for record in _private_records():
        _converge(zone['Id'], _name(record['Name']), record['Type'], [record])
    details = route53.get_hosted_zone(Id=zone['Id'])
    response = {
        'HostedZone': details.get('HostedZone', zone),
        'VPCs': details.get('VPCs', []),
        'ResourceRecordSets': [record for record in _records(zone['Id']) if record.get('Type') not in {'NS', 'SOA'}],
    }
    verified, message = _check_private()
    return _result('private-hosted-zone', PRIVATE_ZONE_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-hosted-zone': step_create_hosted_zone,
    'create-health-checks': step_create_health_checks,
    'failover-records': step_failover_records,
    'alias-apex': step_alias_apex,
    'weighted-records': step_weighted_records,
    'latency-records': step_latency_records,
    'failover-drill': step_failover_drill,
    'private-hosted-zone': step_private_hosted_zone,
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
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    checks = {
        'create-hosted-zone': lambda: _check_zone()[0],
        'create-health-checks': lambda: _check_health_checks()[0],
        'failover-records': lambda: _check_failover()[0],
        'alias-apex': lambda: _check_alias()[0],
        'weighted-records': lambda: _check_weighted()[0],
        'latency-records': lambda: _check_latency()[0],
        'failover-drill': lambda: _check_drill()[0],
        'private-hosted-zone': lambda: _check_private()[0],
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
    """Delete both zones' records and the zones, the health checks, and the VPC."""
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if code.startswith('NoSuch') or 'NotFound' in code:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    route53 = _client('route53')
    for private in (False, True):
        try:
            zone = _zone(private=private)
        except (ClientError, BotoCoreError) as exc:
            errors.append(f'hosted zones: {exc}')
            break
        if not zone:
            continue
        zone_id = zone['Id']
        for record in _records(zone_id):
            if record.get('Type') in {'NS', 'SOA'} and _name(record.get('Name')) == ZONE:
                continue
            label = f"record:{_name(record.get('Name'))} {record.get('Type')} {record.get('SetIdentifier') or ''}".strip()
            attempt(label, lambda record=record: route53.change_resource_record_sets(
                HostedZoneId=zone_id, ChangeBatch={'Changes': [{'Action': 'DELETE', 'ResourceRecordSet': record}]}))
        attempt(f"{'private' if private else 'public'}-zone:{_short(zone_id)}",
                lambda zone_id=zone_id: route53.delete_hosted_zone(Id=zone_id))

    try:
        checks = [check for check in _health_checks()
                  if (check.get('CallerReference') or '').startswith(REFERENCE_PREFIX)]
    except (ClientError, BotoCoreError) as exc:
        checks = []
        errors.append(f'health checks: {exc}')
    for check in checks:
        attempt(f"health-check:{check['Id']}", lambda check=check: route53.delete_health_check(HealthCheckId=check['Id']))

    try:
        vpc = _vpc()
    except (ClientError, BotoCoreError) as exc:
        vpc = None
        errors.append(f'vpc: {exc}')
    if vpc:
        attempt(f"vpc:{vpc['VpcId']}", lambda: _client('ec2').delete_vpc(VpcId=vpc['VpcId']))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws route53 change-resource-record-sets --hosted-zone-id <zone-id> --change-batch file://delete.json\n'
            'aws route53 delete-hosted-zone --id <zone-id>   # public, then private\n'
            'aws route53 delete-health-check --health-check-id <health-check-id>\n'
            'aws ec2 delete-vpc --vpc-id <vpc-id>'
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
            else 'Records, both hosted zones, the health checks, and the VPC are deleted.',
        },
    }
