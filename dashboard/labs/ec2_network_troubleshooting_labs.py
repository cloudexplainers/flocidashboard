"""Network troubleshooting lab: a network ACL that drops return traffic and the flow log records that show it, a
private subnet with no way out, an IPv6 subnet that needs an egress-only gateway, load balancer access logs that tell
a target error from a timeout, private DNS that does not resolve until the VPC and Resolver are set up, and a
CloudFront cache key that makes every request a miss.

Floci 2.4.0 stores the whole network: VPCs with Amazon IPv6 blocks, network ACLs and their entries, route tables with
NAT gateway and egress-only gateway routes, flow logs, load balancer attributes, Resolver endpoints and rules and
their VPC associations, private hosted zones, DNS Firewall domain lists, cache policies and distributions. It moves
no packets between them and writes no flow log or access log records, so the lab evaluates every path the way AWS
does and builds the log records AWS would write. Firewall rule groups and adding domains to a domain list are
unsupported, and CloudFront has no invalidations.
"""

from __future__ import annotations

import ipaddress
import json
import shlex
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ec2'
LAB_KEY = 'network-troubleshooting'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
TAG = {'Key': 'Lab', 'Value': LAB_KEY}

PREFIX = 'lab-nettrouble'
VPC_NAME = PREFIX
VPC_CIDR = '10.62.0.0/16'
WEB_A, WEB_B, APP = f'{PREFIX}-web-a', f'{PREFIX}-web-b', f'{PREFIX}-app'
SUBNETS = {
    WEB_A: {'cidr': '10.62.1.0/24', 'zone': 'us-east-1a', 'ipv6': None},
    WEB_B: {'cidr': '10.62.3.0/24', 'zone': 'us-east-1b', 'ipv6': None},
    APP: {'cidr': '10.62.2.0/24', 'zone': 'us-east-1a', 'ipv6': 2},
}
PUBLIC_TABLE, PRIVATE_TABLE = f'{PREFIX}-public', f'{PREFIX}-private'
WEB_ACL = f'{PREFIX}-web'
WEB_SG, DNS_SG = f'{PREFIX}-web', f'{PREFIX}-dns'
WEB_ENI = f'{PREFIX}-web-server'
IGW, EIGW, NAT = f'{PREFIX}-igw', f'{PREFIX}-eigw', f'{PREFIX}-nat'

CLIENT_IP, CLIENT_PORT = '198.51.100.24', 51544
MIRROR_IP = '151.101.0.10'
MIRROR_IPV6 = '2a04:4e42::10'
INTERNET_IPV6 = '2001:db8:ff::7'
EPHEMERAL = (1024, 65535)

LOG_BUCKET = f'{PREFIX}-logs-{ACCOUNT}'
FLOW_PREFIX = 'flow/'
FLOW_FORMAT = ('${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} '
               '${packets} ${bytes} ${start} ${end} ${action} ${log-status} ${flow-direction}')
ALB = f'{PREFIX}-alb'
ALB_PREFIX = 'alb'
ELB_LOG_PRINCIPAL = 'logdelivery.elasticloadbalancing.amazonaws.com'

PRIVATE_ZONE = 'app.internal'
DB_RECORD, DB_IP = f'db.{PRIVATE_ZONE}', '10.62.2.40'
CORP_DOMAIN = 'corp.example'
CORP_DNS = '10.10.0.2'
RESOLVER_ENDPOINT = f'{PREFIX}-outbound'
RESOLVER_RULE = f'{PREFIX}-corp'
DOMAIN_LIST = f'{PREFIX}-blocked'
BLOCKED_DOMAINS = ['malware.example', '*.bad.example']
DNS_QUERIES = [DB_RECORD, f'hr.{CORP_DOMAIN}', 'malware.example', 'cdn.bad.example', 'aws.amazon.com']

SLOW_POLICY, FAST_POLICY = f'{PREFIX}-everything', f'{PREFIX}-pages'
DIST_COMMENT = f'{PREFIX} storefront'
DEPLOY_TIMEOUT_SECONDS = 60
CDN_REQUESTS = [
    {'path': '/products', 'query': {'lang': 'en', 'utm_source': f'ad{n % 4}'}, 'cookies': {'session': f's{n}'}}
    for n in range(8)
] + [
    {'path': '/products', 'query': {'lang': 'fr', 'utm_source': 'mail'}, 'cookies': {'session': f's{n}'}}
    for n in range(8, 12)
]

FLOCI = 'How Floci emulates this: '

BROKEN_COMMAND = (
    f'aws ec2 create-vpc --cidr-block {VPC_CIDR} --amazon-provided-ipv6-cidr-block\n'
    f'aws ec2 create-subnet --vpc-id <vpc> --cidr-block 10.62.1.0/24 --availability-zone us-east-1a   # {WEB_A}\n'
    'aws ec2 create-internet-gateway; aws ec2 attach-internet-gateway --internet-gateway-id <igw> --vpc-id <vpc>\n'
    'aws ec2 create-route --route-table-id <public> --destination-cidr-block 0.0.0.0/0 --gateway-id <igw>\n'
    'aws ec2 create-network-acl --vpc-id <vpc>\n'
    'aws ec2 create-network-acl-entry --network-acl-id <acl> --ingress --rule-number 100 --protocol tcp '
    '--port-range From=443,To=443 --cidr-block 0.0.0.0/0 --rule-action allow\n'
    'aws ec2 create-network-acl-entry --network-acl-id <acl> --egress --rule-number 100 --protocol tcp '
    '--port-range From=443,To=443 --cidr-block 0.0.0.0/0 --rule-action allow\n'
    'aws ec2 replace-network-acl-association --association-id <web-a association> --network-acl-id <acl>'
)
FLOW_COMMAND = (
    f'aws ec2 create-flow-logs --resource-type Subnet --resource-ids <{WEB_A}> --traffic-type ALL \\\n'
    f'  --log-destination-type s3 --log-destination arn:aws:s3:::{LOG_BUCKET}/{FLOW_PREFIX} \\\n'
    "  --log-format '${version} ${account-id} ${interface-id} ${srcaddr} ${dstaddr} ... ${flow-direction}'\n"
    f'aws s3 cp s3://{LOG_BUCKET}/{FLOW_PREFIX}... - | grep REJECT\n'
    'aws ec2 create-network-acl-entry --network-acl-id <acl> --egress --rule-number 110 --protocol tcp '
    '--port-range From=1024,To=65535 --cidr-block 0.0.0.0/0 --rule-action allow'
)
EGRESS_COMMAND = (
    'aws ec2 describe-route-tables --filters Name=association.subnet-id,Values=<app>   # no 0.0.0.0/0 route\n'
    f'aws ec2 allocate-address --domain vpc\n'
    f'aws ec2 create-nat-gateway --subnet-id <{WEB_A}> --allocation-id <eip>\n'
    'aws ec2 create-route --route-table-id <private> --destination-cidr-block 0.0.0.0/0 --nat-gateway-id <nat>'
)
IPV6_COMMAND = (
    f'aws ec2 describe-subnets --filters Name=tag:Name,Values={APP}   # created with the /64 <vpc /56>:02::/64\n'
    'aws ec2 create-egress-only-internet-gateway --vpc-id <vpc>\n'
    'aws ec2 create-route --route-table-id <private> --destination-ipv6-cidr-block ::/0 '
    '--egress-only-internet-gateway-id <eigw>'
)
ALB_COMMAND = (
    f'aws s3api put-bucket-policy --bucket {LOG_BUCKET} --policy file://elb-log-delivery.json\n'
    f'aws elbv2 create-load-balancer --name {ALB} --subnets <{WEB_A}> <{WEB_B}>\n'
    'aws elbv2 modify-load-balancer-attributes --load-balancer-arn <alb> --attributes \\\n'
    f'  Key=access_logs.s3.enabled,Value=true Key=access_logs.s3.bucket,Value={LOG_BUCKET} '
    f'Key=access_logs.s3.prefix,Value={ALB_PREFIX}\n'
    f'aws s3 cp s3://{LOG_BUCKET}/{ALB_PREFIX}/AWSLogs/{ACCOUNT}/elasticloadbalancing/... - | zcat'
)
DNS_COMMAND = (
    'aws ec2 modify-vpc-attribute --vpc-id <vpc> --enable-dns-hostnames \'{"Value":true}\'\n'
    f'aws route53 create-hosted-zone --name {PRIVATE_ZONE} --vpc VPCRegion={REGION},VPCId=<vpc> '
    '--hosted-zone-config PrivateZone=true --caller-reference <ref>\n'
    f'aws route53resolver create-resolver-endpoint --name {RESOLVER_ENDPOINT} --direction OUTBOUND ...\n'
    f'aws route53resolver create-resolver-rule --name {RESOLVER_RULE} --rule-type FORWARD '
    f'--domain-name {CORP_DOMAIN} --target-ips Ip={CORP_DNS},Port=53 --resolver-endpoint-id <endpoint> --creator-request-id <ref>\n'
    'aws route53resolver associate-resolver-rule --resolver-rule-id <rule> --vpc-id <vpc>\n'
    f'aws route53resolver create-firewall-domain-list --name {DOMAIN_LIST}'
)
CDN_COMMAND = (
    f'aws cloudfront create-cache-policy --cache-policy-config file://{SLOW_POLICY}.json   # all query strings, cookies\n'
    'aws cloudfront create-distribution --distribution-config file://storefront.json\n'
    f'aws cloudfront create-cache-policy --cache-policy-config file://{FAST_POLICY}.json   # only lang\n'
    'aws cloudfront update-distribution --id <dist> --if-match <etag> --distribution-config file://storefront.json'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Troubleshoot networks: ACLs, flow logs, egress, IPv6, load balancer logs, DNS, and caching',
    'description': (
        'Find why HTTPS fails through a network ACL and prove it with flow log records, give a private subnet a way '
        'out with a NAT gateway and an IPv6 subnet one with an egress-only gateway, read load balancer access logs '
        'to tell a target error from a timeout, make private DNS and a forwarding rule resolve, block bad domains, '
        'and fix a CloudFront cache key that makes every request a miss.'
    ),
    'steps': [
        {
            'key': 'broken-web-path',
            'title': 'Build the web subnet and find where HTTPS stops',
            'command': BROKEN_COMMAND,
            'explanation': (
                f'{VPC_NAME} has a public subnet {WEB_A} with an internet gateway route, a web server interface with '
                'a security group that allows 443, and a custom network ACL that allows 443 in and 443 out. Clients '
                'reach the server, but every page load hangs. Network ACLs are stateless: the reply leaves the '
                f'server from port 443 to the client\'s ephemeral port ({CLIENT_PORT} here), and the outbound rules '
                'only allow destination port 443, so the reply hits the final deny. Security groups are stateful '
                'and allow the reply on their own. '
                + FLOCI + 'the VPC, subnets, gateway, routes, ACL entries and the association are real API objects, '
                'but Floci moves no packets through them, so the lab evaluates the ACL rules in order the way AWS '
                'does.'
            ),
        },
        {
            'key': 'flow-log-rejects',
            'title': 'Prove it with flow logs, then fix the ACL',
            'command': FLOW_COMMAND,
            'explanation': (
                f'A flow log on {WEB_A} with the flow-direction field writes to s3://{LOG_BUCKET}/{FLOW_PREFIX}. The '
                'records show the pattern: the ingress record to port 443 is ACCEPT and the egress record back to '
                'the ephemeral port is REJECT. An ACCEPT one way and a REJECT the other is a network ACL, because a '
                'security group never rejects a reply. An outbound rule for TCP 1024-65535 fixes it, and the next '
                'records are ACCEPT both ways. '
                + FLOCI + 'the flow log is stored as ACTIVE, but Floci writes no records, so the lab builds the '
                'records AWS would write from its own evaluation of the ACL, before and after the fix.'
            ),
        },
        {
            'key': 'private-egress',
            'title': 'Give the private subnet a way out',
            'command': EGRESS_COMMAND,
            'explanation': (
                f'Instances in {APP} cannot download packages from {MIRROR_IP}. Its route table has only the local '
                'route, so traffic for the internet has nowhere to go. A NAT gateway in the public subnet, with an '
                'Elastic IP, and a 0.0.0.0/0 route to it in the private table fix it: the NAT gateway forwards '
                'through the public subnet\'s internet gateway route, and nothing on the internet can start a '
                'connection back. Reachability Analyzer finds this kind of break on AWS. '
                + FLOCI + 'the NAT gateway, Elastic IP and route are real API objects; Reachability Analyzer is '
                'unsupported, so the lab traces the routes hop by hop with longest-prefix matching.'
            ),
        },
        {
            'key': 'ipv6-egress-only',
            'title': 'Let IPv6 instances reach out without being reachable',
            'command': IPV6_COMMAND,
            'explanation': (
                f'{APP} has a /64 from the VPC\'s Amazon IPv6 block. IPv6 addresses are public, so there is no NAT: '
                'an egress-only internet gateway and a ::/0 route to it let instances start connections out, and '
                'drop connections started from the internet. The same route to the internet gateway would make the '
                'instances reachable from anywhere their security group allows. '
                + FLOCI + 'the IPv6 block, the egress-only gateway and the ::/0 route are stored; the lab evaluates '
                'which direction is allowed. Floci cannot add an IPv6 block to an existing subnet '
                '(AssociateSubnetCidrBlock), so the first step created the subnet with its /64.'
            ),
        },
        {
            'key': 'alb-access-logs',
            'title': 'Read load balancer access logs',
            'command': ALB_COMMAND,
            'explanation': (
                f'Access logs for {ALB} go to s3://{LOG_BUCKET}/{ALB_PREFIX}/, with a bucket policy that lets the '
                f'{ELB_LOG_PRINCIPAL} service write there. Each entry has the load balancer\'s status and the '
                'target\'s. A 502 with target status "-" means the target closed or refused the connection; a 504 '
                'with target_processing_time -1 means the target did not answer within the idle timeout; a 503 '
                'means no healthy targets; a 500 from both means the app failed. '
                + FLOCI + 'the load balancer and its access_logs.s3.* attributes are stored, but Floci writes no log '
                'files, so the lab reads sample entries in the AWS access log format.'
            ),
        },
        {
            'key': 'dns-resolution',
            'title': 'Make private DNS and forwarding resolve, and block bad domains',
            'command': DNS_COMMAND,
            'explanation': (
                f'{DB_RECORD} lives in the private hosted zone {PRIVATE_ZONE}, which only answers when the VPC has '
                'both enableDnsSupport and enableDnsHostnames on, and a new VPC has hostnames off. Names under '
                f'{CORP_DOMAIN} belong to the on-premises DNS at {CORP_DNS}: an outbound Resolver endpoint and a '
                'forwarding rule send them there, but only once the rule is associated with the VPC. When a rule '
                'and a private zone both match a name, the more specific domain wins. A DNS Firewall domain list '
                'blocks known bad domains, including wildcards. '
                + FLOCI + 'the VPC attribute, the private zone, the endpoint, the rule and its association, and '
                'the domain list are real API objects. Floci does not answer DNS queries, cannot add domains to a '
                'list, and has no firewall rule groups, so the lab resolves each name the way Route 53 Resolver '
                'does.'
            ),
        },
        {
            'key': 'cdn-cache-key',
            'title': 'Fix a CloudFront cache key that misses on every request',
            'command': CDN_COMMAND,
            'explanation': (
                f'The storefront distribution in front of {ALB} uses {SLOW_POLICY}, which puts every query string '
                'and cookie in the cache key. Each visitor has a different session cookie and campaigns add '
                'utm_source, so almost every request is a miss and goes to the origin. '
                f'{FAST_POLICY} keeps only the lang query string in the key and no cookies, so twelve requests '
                'make two cache entries. A value the origin needs but the cache must not split on goes in an origin '
                'request policy instead. To change cached content, use versioned file names or an invalidation. '
                + FLOCI + 'cache policies and the distribution are stored and the distribution reaches Deployed, '
                'but Floci serves no edge cache and has no invalidations, so the lab computes the cache keys and the '
                'hit ratio itself.'
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


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _tag_spec(resource: str, name: str) -> list[dict[str, Any]]:
    return [{'ResourceType': resource, 'Tags': [{'Key': 'Name', 'Value': name}, TAG]}]


def _name(item: dict[str, Any]) -> str | None:
    return next((tag['Value'] for tag in item.get('Tags') or [] if tag['Key'] == 'Name'), None)


# ── Pure helpers: ACLs, routes, flow logs, access logs, DNS, cache keys ──────

def _port_matches(entry: dict[str, Any], port: int) -> bool:
    if str(entry.get('Protocol')) == '-1':
        return True
    if str(entry.get('Protocol')) != '6':
        return False
    ports = entry.get('PortRange') or {}
    return int(ports.get('From', 0)) <= port <= int(ports.get('To', 65535))


def acl_decision(entries: list[dict[str, Any]], egress: bool, port: int, address: str) -> dict[str, Any]:
    """Evaluate network ACL rules for one TCP packet the way AWS does: lowest rule number first, first match wins."""
    ip = ipaddress.ip_address(address)
    for entry in sorted((item for item in entries if bool(item.get('Egress')) == egress),
                        key=lambda item: int(item['RuleNumber'])):
        block = entry.get('CidrBlock') or entry.get('Ipv6CidrBlock')
        if not block or ip.version != ipaddress.ip_network(block).version:
            continue
        if ip in ipaddress.ip_network(block) and _port_matches(entry, port):
            return {'action': entry['RuleAction'], 'rule': int(entry['RuleNumber'])}
    return {'action': 'deny', 'rule': '*'}


def https_session(entries: list[dict[str, Any]], client_ip: str = CLIENT_IP,
                  client_port: int = CLIENT_PORT) -> dict[str, Any]:
    """The request to 443 is checked by the inbound rules, and the reply to the client's port by the outbound rules."""
    request = acl_decision(entries, False, 443, client_ip)
    reply = acl_decision(entries, True, client_port, client_ip)
    return {'request': request, 'reply': reply,
            'works': request['action'] == 'allow' and reply['action'] == 'allow'}


def flow_records(session: dict[str, Any], interface: str, server_ip: str, start: int,
                 client_ip: str = CLIENT_IP, client_port: int = CLIENT_PORT) -> list[str]:
    """The two records AWS writes for one HTTPS attempt, in FLOW_FORMAT."""
    def action(decision: dict[str, Any]) -> str:
        return 'ACCEPT' if decision['action'] == 'allow' else 'REJECT'
    inbound = action(session['request'])
    outbound = action(session['reply']) if inbound == 'ACCEPT' else None
    records = [f'5 {ACCOUNT} {interface} {client_ip} {server_ip} {client_port} 443 6 3 180 {start} {start + 60} '
               f'{inbound} OK ingress']
    if outbound:
        records.append(f'5 {ACCOUNT} {interface} {server_ip} {client_ip} 443 {client_port} 6 3 4380 {start} '
                       f'{start + 60} {outbound} OK egress')
    return records


def parse_flow(records: list[str], fields: str = FLOW_FORMAT) -> list[dict[str, str]]:
    names = [field.strip('${}') for field in fields.split()]
    return [dict(zip(names, record.split())) for record in records]


def diagnose_flows(rows: list[dict[str, str]]) -> str:
    accepted_in = [row for row in rows if row.get('flow-direction') == 'ingress' and row.get('action') == 'ACCEPT']
    rejected_out = [row for row in rows if row.get('flow-direction') == 'egress' and row.get('action') == 'REJECT']
    for row in rejected_out:
        if any(item['srcaddr'] == row['dstaddr'] and item['dstport'] == row['srcport'] for item in accepted_in):
            return 'network-acl'
    if any(row.get('action') == 'REJECT' and row.get('flow-direction') == 'ingress' for row in rows):
        return 'security-group-or-acl-inbound'
    return 'none'


def route_target(routes: list[dict[str, Any]], destination: str) -> str | None:
    """Longest-prefix match over a route table's active routes."""
    ip = ipaddress.ip_address(destination)
    best: tuple[int, str] | None = None
    for route in routes:
        block = route.get('DestinationCidrBlock') or route.get('DestinationIpv6CidrBlock')
        if not block or route.get('State', 'active') != 'active':
            continue
        network = ipaddress.ip_network(block)
        if network.version != ip.version or ip not in network:
            continue
        target = (route.get('GatewayId') or route.get('NatGatewayId') or route.get('EgressOnlyInternetGatewayId')
                  or route.get('TransitGatewayId') or route.get('VpcPeeringConnectionId')
                  or route.get('NetworkInterfaceId'))
        if target and (best is None or network.prefixlen > best[0]):
            best = (network.prefixlen, target)
    return best[1] if best else None


def egress_path(tables: dict[str, list[dict[str, Any]]], subnet: str, destination: str,
                nat_subnets: dict[str, str]) -> dict[str, Any]:
    """Trace an outbound connection from a subnet hop by hop. tables maps subnet ID to its routes."""
    hops = [subnet]
    current = subnet
    for _ in range(4):
        target = route_target(tables.get(current, []), destination)
        if target is None:
            return {'reaches': False, 'hops': hops, 'stoppedAt': current, 'reason': 'no route'}
        hops.append(target)
        if target.startswith(('igw-', 'eigw-')):
            return {'reaches': True, 'hops': hops, 'via': target}
        if target == 'local':
            return {'reaches': False, 'hops': hops, 'stoppedAt': current, 'reason': 'stays in the VPC'}
        if target.startswith('nat-') and target in nat_subnets:
            current = nat_subnets[target]
            hops.append(current)
            continue
        return {'reaches': False, 'hops': hops, 'stoppedAt': target, 'reason': 'unsupported target'}
    return {'reaches': False, 'hops': hops, 'reason': 'loop'}


def ipv6_subnet_block(vpc_block: str, index: int) -> str:
    return str(list(ipaddress.ip_network(vpc_block).subnets(new_prefix=64))[index])


def ipv6_directions(routes: list[dict[str, Any]]) -> dict[str, bool]:
    target = route_target(routes, INTERNET_IPV6) or ''
    return {'outbound': target.startswith(('igw-', 'eigw-')), 'inboundFromInternet': target.startswith('igw-')}


SAMPLE_ALB_LOG = [
    f'https 2026-11-04T18:00:01.101000Z app/{ALB}/50dc6c495c0c9188 198.51.100.24:51544 10.62.1.25:80 0.000 0.012 '
    f'0.000 200 200 312 4120 "GET https://shop.lab.example:443/products HTTP/1.1" "curl/8.5.0" '
    'ECDHE-RSA-AES128-GCM-SHA256 TLSv1.3 - "Root=1-1" "shop.lab.example" "-" 0 2026-11-04T18:00:01.089000Z '
    '"forward" "-" "-" "10.62.1.25:80" "200" "-" "-"',
    f'https 2026-11-04T18:00:02.204000Z app/{ALB}/50dc6c495c0c9188 198.51.100.31:50211 10.62.1.26:80 0.000 -1 -1 '
    f'502 - 298 272 "GET https://shop.lab.example:443/cart HTTP/1.1" "curl/8.5.0" ECDHE-RSA-AES128-GCM-SHA256 '
    'TLSv1.3 - "Root=1-2" "shop.lab.example" "-" 0 2026-11-04T18:00:02.203000Z "forward" "-" "-" '
    '"10.62.1.26:80" "-" "-" "-"',
    f'https 2026-11-04T18:01:03.307000Z app/{ALB}/50dc6c495c0c9188 198.51.100.40:49152 10.62.1.27:80 0.000 -1 -1 '
    f'504 - 301 0 "POST https://shop.lab.example:443/checkout HTTP/1.1" "curl/8.5.0" ECDHE-RSA-AES128-GCM-SHA256 '
    'TLSv1.3 - "Root=1-3" "shop.lab.example" "-" 0 2026-11-04T18:00:03.300000Z "forward" "-" "-" '
    '"10.62.1.27:80" "-" "-" "-"',
    f'https 2026-11-04T18:01:04.410000Z app/{ALB}/50dc6c495c0c9188 198.51.100.52:60001 - -1 -1 -1 503 - 280 '
    f'326 "GET https://shop.lab.example:443/api/stock HTTP/1.1" "curl/8.5.0" ECDHE-RSA-AES128-GCM-SHA256 '
    'TLSv1.3 - "Root=1-4" "shop.lab.example" "-" 0 2026-11-04T18:01:04.409000Z "forward" "-" "-" "-" "-" "-" "-"',
    f'https 2026-11-04T18:01:05.513000Z app/{ALB}/50dc6c495c0c9188 198.51.100.24:51600 10.62.1.25:80 0.000 0.087 '
    f'0.000 500 500 305 190 "GET https://shop.lab.example:443/orders HTTP/1.1" "curl/8.5.0" '
    'ECDHE-RSA-AES128-GCM-SHA256 TLSv1.3 - "Root=1-5" "shop.lab.example" "-" 0 2026-11-04T18:01:05.425000Z '
    '"forward" "-" "-" "10.62.1.25:80" "500" "-" "-"',
]


def parse_alb_entry(line: str) -> dict[str, str]:
    fields = shlex.split(line)
    return {'type': fields[0], 'time': fields[1], 'client': fields[3], 'target': fields[4],
            'request_processing_time': fields[5], 'target_processing_time': fields[6],
            'elb_status_code': fields[8], 'target_status_code': fields[9], 'request': fields[12]}


def diagnose_alb(entry: dict[str, str]) -> str:
    elb, target = entry['elb_status_code'], entry['target_status_code']
    if elb == '503' and entry['target'] == '-':
        return 'no healthy targets'
    if elb == '502' and target == '-':
        return 'target closed or refused the connection'
    if elb == '504' and entry['target_processing_time'] == '-1':
        return 'target did not answer within the idle timeout'
    if target != '-' and target == elb and elb.startswith('5'):
        return 'application error on the target'
    if elb.startswith(('2', '3')):
        return 'ok'
    return 'other'


def _domain_matches(name: str, domain: str) -> bool:
    name, domain = name.rstrip('.').lower(), domain.rstrip('.').lower()
    if domain.startswith('*.'):
        return name.endswith(domain[1:])
    return name == domain or name.endswith('.' + domain)


def resolve(name: str, dns_support: bool, dns_hostnames: bool, zones: dict[str, dict[str, str]],
            rules: list[dict[str, Any]], blocked: list[str]) -> dict[str, Any]:
    """Answer a query from an instance in the VPC the way Route 53 Resolver does."""
    if not dns_support:
        return {'answer': None, 'source': 'none', 'detail': 'enableDnsSupport is off, so nothing answers at .2'}
    if any(_domain_matches(name, domain) for domain in blocked):
        return {'answer': None, 'source': 'dns-firewall', 'detail': 'blocked (NXDOMAIN)'}
    candidates: list[tuple[int, str, Any]] = []
    for rule in rules:
        if rule.get('associated') and _domain_matches(name, rule['domain']):
            candidates.append((len(rule['domain']), 'rule', rule))
    if dns_hostnames:
        for zone, records in zones.items():
            if _domain_matches(name, zone):
                candidates.append((len(zone), 'zone', records))
    if candidates:
        candidates.sort(key=lambda item: (item[0], item[1] == 'rule'), reverse=True)
        _, kind, value = candidates[0]
        if kind == 'rule':
            return {'answer': f'forwarded to {", ".join(value["targets"])}', 'source': 'forwarding-rule',
                    'detail': f'rule for {value["domain"]}'}
        answer = value.get(name.rstrip('.').lower())
        return {'answer': answer, 'source': 'private-zone', 'detail': 'found' if answer else 'NXDOMAIN'}
    if name.endswith(('.internal', '.' + CORP_DOMAIN)):
        return {'answer': None, 'source': 'public-dns', 'detail': 'NXDOMAIN: the name is not on the internet'}
    return {'answer': 'public address', 'source': 'public-dns', 'detail': 'resolved on the internet'}


def cache_key(request: dict[str, Any], parameters: dict[str, Any]) -> str:
    query = parameters['QueryStringsConfig']
    behavior = query['QueryStringBehavior']
    if behavior == 'all':
        kept = request['query']
    elif behavior == 'whitelist':
        kept = {key: value for key, value in request['query'].items() if key in query['QueryStrings']['Items']}
    else:
        kept = {}
    cookies = parameters['CookiesConfig']
    kept_cookies = request['cookies'] if cookies['CookieBehavior'] == 'all' else {}
    return request['path'] + '?' + '&'.join(f'{k}={v}' for k, v in sorted(kept.items())) + \
        '|' + ';'.join(f'{k}={v}' for k, v in sorted(kept_cookies.items()))


def hit_ratio(requests: list[dict[str, Any]], parameters: dict[str, Any]) -> dict[str, Any]:
    keys = [cache_key(request, parameters) for request in requests]
    unique = len(set(keys))
    return {'requests': len(keys), 'cacheEntries': unique, 'hits': len(keys) - unique,
            'hitRatio': round((len(keys) - unique) / len(keys), 2) if keys else 0.0}


def _cache_parameters(query: dict[str, Any], cookie_behavior: str) -> dict[str, Any]:
    return {'EnableAcceptEncodingGzip': True, 'EnableAcceptEncodingBrotli': True,
            'HeadersConfig': {'HeaderBehavior': 'none'},
            'CookiesConfig': {'CookieBehavior': cookie_behavior},
            'QueryStringsConfig': query}


SLOW_PARAMETERS = _cache_parameters({'QueryStringBehavior': 'all'}, 'all')
FAST_PARAMETERS = _cache_parameters(
    {'QueryStringBehavior': 'whitelist', 'QueryStrings': {'Quantity': 1, 'Items': ['lang']}}, 'none')


# ── Floci lookups ────────────────────────────────────────────────────────────

def _lab_filter() -> list[dict[str, Any]]:
    return [{'Name': 'tag:Lab', 'Values': [LAB_KEY]}]


def _vpc() -> dict[str, Any] | None:
    vpcs = _safe_call(lambda: _client('ec2').describe_vpcs(Filters=_lab_filter())['Vpcs'], []) or []
    return next((vpc for vpc in vpcs if _name(vpc) == VPC_NAME), None)


def _named(kind: str, key: str, vpc_id: str | None = None) -> dict[str, dict[str, Any]]:
    filters = _lab_filter() + ([{'Name': 'vpc-id', 'Values': [vpc_id]}] if vpc_id else [])
    method = getattr(_client('ec2'), f'describe_{kind}')
    items = _safe_call(lambda: method(Filters=filters)[key], []) or []
    return {_name(item): item for item in items if _name(item)}


def _subnets() -> dict[str, dict[str, Any]]:
    return _named('subnets', 'Subnets')


def _tables() -> dict[str, dict[str, Any]]:
    return _named('route_tables', 'RouteTables')


def _acl() -> dict[str, Any] | None:
    return _named('network_acls', 'NetworkAcls').get(WEB_ACL)


def _groups() -> dict[str, dict[str, Any]]:
    return _named('security_groups', 'SecurityGroups')


def _eni() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('ec2').describe_network_interfaces(Filters=_lab_filter())[
        'NetworkInterfaces'], []) or []
    return next((item for item in items if _name(item) == WEB_ENI or item.get('Description') == WEB_ENI), None)


def _nat() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('ec2').describe_nat_gateways(Filters=_lab_filter())['NatGateways'], []) or []
    return next((item for item in items if item.get('State') not in ('deleted', 'deleting', 'failed')), None)


def _eigw() -> dict[str, Any] | None:
    vpc = _vpc()
    if not vpc:
        return None
    items = _safe_call(lambda: _client('ec2').describe_egress_only_internet_gateways()[
        'EgressOnlyInternetGateways'], []) or []
    return next((item for item in items if any(
        attachment.get('VpcId') == vpc['VpcId'] for attachment in item.get('Attachments', []))), None)


def _igw() -> dict[str, Any] | None:
    vpc = _vpc()
    if not vpc:
        return None
    items = _safe_call(lambda: _client('ec2').describe_internet_gateways(Filters=[
        {'Name': 'attachment.vpc-id', 'Values': [vpc['VpcId']]}])['InternetGateways'], []) or []
    return items[0] if items else None


def _route_map() -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    """Routes per subnet ID, and the subnet of each NAT gateway."""
    subnets = {item['SubnetId']: name for name, item in _subnets().items()}
    tables: dict[str, list[dict[str, Any]]] = {}
    for table in _tables().values():
        for association in table.get('Associations', []):
            if association.get('SubnetId') in subnets:
                tables[association['SubnetId']] = table.get('Routes', [])
    nat = _nat()
    nats = {nat['NatGatewayId']: nat['SubnetId']} if nat else {}
    return tables, nats


def _flow_log() -> dict[str, Any] | None:
    subnet = _subnets().get(WEB_A)
    if not subnet:
        return None
    logs = _safe_call(lambda: _client('ec2').describe_flow_logs(Filters=[
        {'Name': 'resource-id', 'Values': [subnet['SubnetId']]}])['FlowLogs'], []) or []
    return logs[0] if logs else None


def _bucket_exists() -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=LOG_BUCKET), None) is not None


def _alb() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('elbv2').describe_load_balancers(Names=[ALB])['LoadBalancers'][0])


def _alb_attributes() -> dict[str, str]:
    alb = _alb()
    if not alb:
        return {}
    items = _safe_call(lambda: _client('elbv2').describe_load_balancer_attributes(
        LoadBalancerArn=alb['LoadBalancerArn'])['Attributes'], []) or []
    return {item['Key']: item['Value'] for item in items}


def _vpc_dns(vpc_id: str) -> tuple[bool, bool]:
    ec2 = _client('ec2')
    support = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsSupport')['EnableDnsSupport']['Value']
    hostnames = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsHostnames')[
        'EnableDnsHostnames']['Value']
    return bool(support), bool(hostnames)


def _zone() -> dict[str, Any] | None:
    zones = _safe_call(lambda: _client('route53').list_hosted_zones()['HostedZones'], []) or []
    return next((zone for zone in zones if zone['Name'].rstrip('.') == PRIVATE_ZONE), None)


def _zone_records(zone_id: str) -> dict[str, str]:
    records = _safe_call(lambda: _client('route53').list_resource_record_sets(HostedZoneId=zone_id)[
        'ResourceRecordSets'], []) or []
    return {item['Name'].rstrip('.'): item['ResourceRecords'][0]['Value'] for item in records
            if item['Type'] == 'A' and item.get('ResourceRecords')}


def _endpoint() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('route53resolver').list_resolver_endpoints()['ResolverEndpoints'], []) or []
    return next((item for item in items if item.get('Name') == RESOLVER_ENDPOINT), None)


def _rule() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('route53resolver').list_resolver_rules()['ResolverRules'], []) or []
    return next((item for item in items if item.get('Name') == RESOLVER_RULE), None)


def _rule_associated(rule_id: str, vpc_id: str) -> bool:
    items = _safe_call(lambda: _client('route53resolver').list_resolver_rule_associations()[
        'ResolverRuleAssociations'], []) or []
    return any(item.get('ResolverRuleId') == rule_id and item.get('VPCId') == vpc_id
               and item.get('Status') != 'DELETING' for item in items)


def _domain_list() -> dict[str, Any] | None:
    items = _safe_call(lambda: _client('route53resolver').list_firewall_domain_lists()['FirewallDomainLists'], [])
    return next((item for item in items or [] if item.get('Name') == DOMAIN_LIST), None)


def _cache_policy(name: str) -> dict[str, Any] | None:
    items = _safe_call(lambda: (_client('cloudfront').list_cache_policies(Type='custom').get('CachePolicyList')
                                or {}).get('Items') or [], []) or []
    for item in items:
        policy = item.get('CachePolicy') or {}
        if (policy.get('CachePolicyConfig') or {}).get('Name') == name:
            return policy
    return None


def _distribution() -> dict[str, Any] | None:
    cloudfront = _client('cloudfront')
    items = _safe_call(lambda: (cloudfront.list_distributions().get('DistributionList') or {}).get('Items') or [],
                       []) or []
    summary = next((item for item in items if item.get('Comment') == DIST_COMMENT), None)
    if not summary:
        return None
    return _safe_call(lambda: cloudfront.get_distribution(Id=summary['Id'])['Distribution'])


# ── Checks ───────────────────────────────────────────────────────────────────

def _acl_entries() -> list[dict[str, Any]]:
    return (_acl() or {}).get('Entries', [])


def _check_broken() -> tuple[bool, str]:
    subnet, acl = _subnets().get(WEB_A), _acl()
    if not _vpc() or not subnet or not acl or not _igw() or not _eni():
        return False, f'{VPC_NAME}, {WEB_A}, its gateway, the web server interface, or {WEB_ACL} is missing.'
    if not any(item.get('SubnetId') == subnet['SubnetId'] for item in acl.get('Associations', [])):
        return False, f'{WEB_ACL} is not associated with {WEB_A}.'
    record = _marked('broken-web-path') or {}
    if record.get('request', {}).get('action') != 'allow' or record.get('reply', {}).get('action') != 'deny':
        return False, 'The broken path (request allowed, reply denied) has not been found.'
    return True, f'The request to 443 is allowed and the reply to port {CLIENT_PORT} hits the ACL\'s final deny.'


def _check_flow() -> tuple[bool, str]:
    log = _flow_log()
    if not log or log.get('LogDestinationType') != 's3' or 'flow-direction' not in (log.get('LogFormat') or ''):
        return False, f'{WEB_A} needs a flow log to S3 with the flow-direction field.'
    if (_marked('flow-log-rejects') or {}).get('diagnosis') != 'network-acl':
        return False, 'The flow records have not been read.'
    session = https_session(_acl_entries())
    if not session['works']:
        return False, f'The reply is still denied by rule {session["reply"]["rule"]}.'
    return True, 'The records pointed at the ACL, and HTTPS now passes both ways.'


def _check_egress() -> tuple[bool, str]:
    subnet = _subnets().get(APP)
    if not subnet or not _nat():
        return False, f'{APP} or the NAT gateway is missing.'
    if (_marked('private-egress') or {}).get('before', {}).get('reaches') is not False:
        return False, 'The missing route has not been traced.'
    tables, nats = _route_map()
    path = egress_path(tables, subnet['SubnetId'], MIRROR_IP, nats)
    if not path['reaches']:
        return False, f'{APP} still cannot reach {MIRROR_IP}: {path.get("reason")}.'
    return True, f'{APP} reaches the internet through {" → ".join(path["hops"][1:])}.'


def _check_ipv6() -> tuple[bool, str]:
    subnet = _subnets().get(APP)
    if not subnet or not subnet.get('Ipv6CidrBlockAssociationSet') or not _eigw():
        return False, f'{APP} needs an IPv6 block and the VPC an egress-only gateway.'
    tables, _ = _route_map()
    directions = ipv6_directions(tables.get(subnet['SubnetId'], []))
    if not directions['outbound'] or directions['inboundFromInternet']:
        return False, f'IPv6 should go out and not come in: {directions}.'
    return True, 'IPv6 connections go out through the egress-only gateway, and the internet cannot start one in.'


def _check_alb() -> tuple[bool, str]:
    attributes = _alb_attributes()
    if attributes.get('access_logs.s3.enabled') != 'true' or attributes.get('access_logs.s3.bucket') != LOG_BUCKET:
        return False, f'{ALB} does not send access logs to {LOG_BUCKET}.'
    policy = json.loads(_safe_call(lambda: _client('s3').get_bucket_policy(Bucket=LOG_BUCKET)['Policy'], '{}'))
    if not any((item.get('Principal') or {}).get('Service') == ELB_LOG_PRINCIPAL
               for item in policy.get('Statement', [])):
        return False, f'The bucket policy does not let {ELB_LOG_PRINCIPAL} write logs.'
    causes = (_marked('alb-access-logs') or {}).get('causes') or {}
    if len(causes) < 4:
        return False, 'The access log entries have not been read.'
    return True, f'Access logs are on, and the sample entries show {len(causes)} different causes.'


def _dns_inputs() -> dict[str, Any] | None:
    vpc, zone, rule = _vpc(), _zone(), _rule()
    if not vpc or not zone or not rule or not _endpoint():
        return None
    support, hostnames = _vpc_dns(vpc['VpcId'])
    return {
        'dns_support': support, 'dns_hostnames': hostnames,
        'zones': {PRIVATE_ZONE: _zone_records(zone['Id'])},
        'rules': [{'domain': rule['DomainName'].rstrip('.'), 'targets': [item['Ip'] for item in rule['TargetIps']],
                   'associated': _rule_associated(rule['Id'], vpc['VpcId'])}],
        'blocked': BLOCKED_DOMAINS if _domain_list() else [],
    }


def _answers(inputs: dict[str, Any]) -> dict[str, Any]:
    return {name: resolve(name, **inputs) for name in DNS_QUERIES}


def _check_dns() -> tuple[bool, str]:
    inputs = _dns_inputs()
    if not inputs:
        return False, 'The private zone, the Resolver endpoint, or the forwarding rule is missing.'
    before = (_marked('dns-resolution') or {}).get('before') or {}
    if before.get(DB_RECORD, {}).get('answer') or before.get(f'hr.{CORP_DOMAIN}', {}).get('source') != 'public-dns':
        return False, 'The failing lookups have not been recorded.'
    answers = _answers(inputs)
    expected = {DB_RECORD: 'private-zone', f'hr.{CORP_DOMAIN}': 'forwarding-rule', 'malware.example': 'dns-firewall',
                'cdn.bad.example': 'dns-firewall', 'aws.amazon.com': 'public-dns'}
    wrong = [name for name, source in expected.items() if answers[name]['source'] != source]
    if wrong or answers[DB_RECORD]['answer'] != DB_IP:
        return False, f'These names still resolve the wrong way: {", ".join(wrong) or DB_RECORD}.'
    return True, 'Private names, forwarded names, blocked names, and public names all resolve as intended.'


def _check_cdn() -> tuple[bool, str]:
    fast, distribution = _cache_policy(FAST_POLICY), _distribution()
    if not fast or not distribution:
        return False, f'{FAST_POLICY} or the storefront distribution is missing.'
    behavior = distribution.get('DistributionConfig', {}).get('DefaultCacheBehavior', {})
    if behavior.get('CachePolicyId') != fast['Id']:
        return False, f'The distribution does not use {FAST_POLICY}.'
    record = _marked('cdn-cache-key') or {}
    before, after = record.get('before', {}).get('hitRatio'), record.get('after', {}).get('hitRatio')
    if before is None or after is None or after <= before:
        return False, 'The hit ratios have not been compared.'
    return True, f'The cache hit ratio for the sample went from {before:.0%} to {after:.0%}.'


CHECKS = {
    'broken-web-path': _check_broken,
    'flow-log-rejects': _check_flow,
    'private-egress': _check_egress,
    'ipv6-egress-only': _check_ipv6,
    'alb-access-logs': _check_alb,
    'dns-resolution': _check_dns,
    'cdn-cache-key': _check_cdn,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _ensure_network() -> dict[str, Any]:
    ec2 = _client('ec2')
    vpc = _vpc() or ec2.create_vpc(CidrBlock=VPC_CIDR, AmazonProvidedIpv6CidrBlock=True,
                                   TagSpecifications=_tag_spec('vpc', VPC_NAME))['Vpc']
    vpc_id = vpc['VpcId']
    subnets = _subnets()
    vpc_block = (vpc.get('Ipv6CidrBlockAssociationSet') or [{}])[0].get('Ipv6CidrBlock')
    for name, spec in SUBNETS.items():
        if name not in subnets:
            ipv6 = {'Ipv6CidrBlock': ipv6_subnet_block(vpc_block, spec['ipv6'])} if spec['ipv6'] and vpc_block else {}
            ec2.create_subnet(VpcId=vpc_id, CidrBlock=spec['cidr'], AvailabilityZone=spec['zone'],
                              TagSpecifications=_tag_spec('subnet', name), **ipv6)
    subnets = _subnets()
    if not _igw():
        igw = ec2.create_internet_gateway(TagSpecifications=_tag_spec('internet-gateway', IGW))['InternetGateway']
        ec2.attach_internet_gateway(InternetGatewayId=igw['InternetGatewayId'], VpcId=vpc_id)
    igw_id = _igw()['InternetGatewayId']
    tables = _tables()
    for name, members in ((PUBLIC_TABLE, (WEB_A, WEB_B)), (PRIVATE_TABLE, (APP,))):
        if name not in tables:
            table = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=_tag_spec('route-table', name))[
                'RouteTable']
            for member in members:
                ec2.associate_route_table(RouteTableId=table['RouteTableId'], SubnetId=subnets[member]['SubnetId'])
            if name == PUBLIC_TABLE:
                ec2.create_route(RouteTableId=table['RouteTableId'], DestinationCidrBlock='0.0.0.0/0',
                                 GatewayId=igw_id)
    groups = _groups()
    if WEB_SG not in groups:
        group = ec2.create_security_group(GroupName=WEB_SG, Description='HTTPS to the web server', VpcId=vpc_id,
                                          TagSpecifications=_tag_spec('security-group', WEB_SG))['GroupId']
        ec2.authorize_security_group_ingress(GroupId=group, IpPermissions=[{
            'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}])
    if not _eni():
        ec2.create_network_interface(SubnetId=subnets[WEB_A]['SubnetId'], Description=WEB_ENI,
                                     Groups=[_groups()[WEB_SG]['GroupId']],
                                     TagSpecifications=_tag_spec('network-interface', WEB_ENI))
    return _vpc()


def _set_acl(fixed: bool) -> dict[str, Any]:
    """Create the web ACL with its two 443 rules, associate it with web-a, and add or remove the ephemeral rule."""
    ec2 = _client('ec2')
    vpc_id = _vpc()['VpcId']
    acl = _acl()
    if not acl:
        acl_id = ec2.create_network_acl(VpcId=vpc_id, TagSpecifications=_tag_spec('network-acl', WEB_ACL))[
            'NetworkAcl']['NetworkAclId']
        for egress in (False, True):
            ec2.create_network_acl_entry(NetworkAclId=acl_id, RuleNumber=100, Protocol='6', RuleAction='allow',
                                         Egress=egress, CidrBlock='0.0.0.0/0', PortRange={'From': 443, 'To': 443})
        acl = _acl()
    acl_id = acl['NetworkAclId']
    subnet_id = _subnets()[WEB_A]['SubnetId']
    if not any(item.get('SubnetId') == subnet_id for item in acl.get('Associations', [])):
        current = ec2.describe_network_acls(Filters=[{'Name': 'association.subnet-id', 'Values': [subnet_id]}])[
            'NetworkAcls']
        association = next(item['NetworkAclAssociationId'] for item in current[0]['Associations']
                           if item['SubnetId'] == subnet_id)
        ec2.replace_network_acl_association(AssociationId=association, NetworkAclId=acl_id)
    has_rule = any(item.get('Egress') and int(item['RuleNumber']) == 110 for item in acl.get('Entries', []))
    if fixed and not has_rule:
        ec2.create_network_acl_entry(NetworkAclId=acl_id, RuleNumber=110, Protocol='6', RuleAction='allow',
                                     Egress=True, CidrBlock='0.0.0.0/0',
                                     PortRange={'From': EPHEMERAL[0], 'To': EPHEMERAL[1]})
    if not fixed and has_rule:
        ec2.delete_network_acl_entry(NetworkAclId=acl_id, RuleNumber=110, Egress=True)
    return _acl()


def step_broken_web_path() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _ensure_network()
    acl = _set_acl(fixed=False)
    session = https_session(acl['Entries'])
    _marker('broken-web-path', session)
    response = {
        'VpcId': vpc['VpcId'], 'Ipv6CidrBlock': (vpc.get('Ipv6CidrBlockAssociationSet') or [{}])[0].get(
            'Ipv6CidrBlock'),
        'WebServer': {key: (_eni() or {}).get(key) for key in ('NetworkInterfaceId', 'PrivateIpAddress')},
        'NetworkAcl': {'Id': acl['NetworkAclId'], 'Entries': sorted(
            acl['Entries'], key=lambda item: (item.get('Egress'), int(item['RuleNumber'])))},
        'HttpsSession': session,
        'SecurityGroup': 'allows 443 in; stateful, so the reply is allowed without an outbound rule',
    }
    verified, message = _check_broken()
    return _result('broken-web-path', BROKEN_COMMAND, response, verified, message, started)


def _ensure_bucket() -> None:
    if not _bucket_exists():
        _client('s3').create_bucket(Bucket=LOG_BUCKET)


def step_flow_log_rejects() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_broken()[0]:
        return _result('flow-log-rejects', FLOW_COMMAND, {}, False, 'Build the web subnet first.', started)
    ec2 = _client('ec2')
    _ensure_bucket()
    if not _flow_log():
        ec2.create_flow_logs(ResourceType='Subnet', ResourceIds=[_subnets()[WEB_A]['SubnetId']], TrafficType='ALL',
                             LogDestinationType='s3', LogDestination=f'arn:aws:s3:::{LOG_BUCKET}/{FLOW_PREFIX}',
                             LogFormat=FLOW_FORMAT, MaxAggregationInterval=60,
                             TagSpecifications=_tag_spec('vpc-flow-log', f'{PREFIX}-web-a'))
    eni = _eni()
    now = int(time.time())
    before = flow_records(https_session(_set_acl(fixed=False)['Entries']), eni['NetworkInterfaceId'],
                          eni['PrivateIpAddress'], now)
    diagnosis = diagnose_flows(parse_flow(before))
    acl = _set_acl(fixed=True)
    after = flow_records(https_session(acl['Entries']), eni['NetworkInterfaceId'], eni['PrivateIpAddress'], now + 60)
    _marker('flow-log-rejects', {'diagnosis': diagnosis})
    log = _flow_log() or {}
    response = {
        'FlowLog': {key: log.get(key) for key in ('FlowLogId', 'FlowLogStatus', 'LogDestination', 'LogFormat',
                                                  'TrafficType', 'MaxAggregationInterval')},
        'RecordsBefore': before, 'Diagnosis': diagnosis,
        'Fix': {'RuleNumber': 110, 'Egress': True, 'Protocol': 'tcp', 'PortRange': '1024-65535'},
        'RecordsAfter': after,
        'Note': 'Floci writes no flow log records; these are the records AWS writes for this ACL.',
    }
    verified, message = _check_flow()
    return _result('flow-log-rejects', FLOW_COMMAND, response, verified, message, started)


def step_private_egress() -> dict[str, Any]:
    started = time.perf_counter()
    if not _vpc():
        return _result('private-egress', EGRESS_COMMAND, {}, False, 'Build the network first.', started)
    ec2 = _client('ec2')
    subnet_id = _subnets()[APP]['SubnetId']
    private = _tables()[PRIVATE_TABLE]
    if route_target(private.get('Routes', []), MIRROR_IP):
        ec2.delete_route(RouteTableId=private['RouteTableId'], DestinationCidrBlock='0.0.0.0/0')
    tables, nats = _route_map()
    before = egress_path(tables, subnet_id, MIRROR_IP, nats)
    nat = _nat()
    if not nat:
        allocation = ec2.allocate_address(Domain='vpc', TagSpecifications=_tag_spec('elastic-ip', NAT))[
            'AllocationId']
        nat = ec2.create_nat_gateway(SubnetId=_subnets()[WEB_A]['SubnetId'], AllocationId=allocation,
                                     TagSpecifications=_tag_spec('natgateway', NAT))['NatGateway']
        _wait(lambda: (_nat() or {}).get('State') == 'available', 30)
    ec2.create_route(RouteTableId=private['RouteTableId'], DestinationCidrBlock='0.0.0.0/0',
                     NatGatewayId=nat['NatGatewayId'])
    tables, nats = _route_map()
    after = egress_path(tables, subnet_id, MIRROR_IP, nats)
    _marker('private-egress', {'before': before})
    response = {'Before': before, 'NatGateway': {key: (_nat() or {}).get(key) for key in (
        'NatGatewayId', 'State', 'SubnetId', 'NatGatewayAddresses')}, 'After': after,
        'Note': 'Reachability Analyzer is unsupported in Floci; the lab traced the route tables.'}
    verified, message = _check_egress()
    return _result('private-egress', EGRESS_COMMAND, response, verified, message, started)


def step_ipv6_egress_only() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _vpc()
    if not vpc:
        return _result('ipv6-egress-only', IPV6_COMMAND, {}, False, 'Build the network first.', started)
    ec2 = _client('ec2')
    subnet = _subnets()[APP]
    eigw = _eigw() or ec2.create_egress_only_internet_gateway(
        VpcId=vpc['VpcId'], TagSpecifications=_tag_spec('egress-only-internet-gateway', EIGW))[
        'EgressOnlyInternetGateway']
    private = _tables()[PRIVATE_TABLE]
    if not any(route.get('DestinationIpv6CidrBlock') == '::/0' for route in private.get('Routes', [])):
        ec2.create_route(RouteTableId=private['RouteTableId'], DestinationIpv6CidrBlock='::/0',
                         EgressOnlyInternetGatewayId=eigw['EgressOnlyInternetGatewayId'])
    tables, _ = _route_map()
    routes = tables.get(subnet['SubnetId'], [])
    with_igw = [route for route in routes if route.get('DestinationIpv6CidrBlock') != '::/0'] + [
        {'DestinationIpv6CidrBlock': '::/0', 'GatewayId': (_igw() or {}).get('InternetGatewayId', 'igw-'),
         'State': 'active'}]
    response = {
        'SubnetIpv6': (_subnets()[APP].get('Ipv6CidrBlockAssociationSet') or [{}])[0].get('Ipv6CidrBlock'),
        'EgressOnlyInternetGatewayId': eigw['EgressOnlyInternetGatewayId'],
        'Routes': routes,
        'WithEgressOnlyGateway': ipv6_directions(routes),
        'IfRoutedToInternetGateway': ipv6_directions(with_igw),
    }
    verified, message = _check_ipv6()
    return _result('ipv6-egress-only', IPV6_COMMAND, response, verified, message, started)


def _log_delivery_policy() -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': [{
        'Sid': 'AllowLoadBalancerLogDelivery', 'Effect': 'Allow', 'Principal': {'Service': ELB_LOG_PRINCIPAL},
        'Action': 's3:PutObject', 'Resource': f'arn:aws:s3:::{LOG_BUCKET}/{ALB_PREFIX}/AWSLogs/{ACCOUNT}/*'}]}


def step_alb_access_logs() -> dict[str, Any]:
    started = time.perf_counter()
    if not _vpc():
        return _result('alb-access-logs', ALB_COMMAND, {}, False, 'Build the network first.', started)
    elb = _client('elbv2')
    _ensure_bucket()
    _client('s3').put_bucket_policy(Bucket=LOG_BUCKET, Policy=json.dumps(_log_delivery_policy()))
    subnets = _subnets()
    alb = _alb() or elb.create_load_balancer(
        Name=ALB, Subnets=[subnets[WEB_A]['SubnetId'], subnets[WEB_B]['SubnetId']], Scheme='internet-facing',
        Type='application', Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])['LoadBalancers'][0]
    elb.modify_load_balancer_attributes(LoadBalancerArn=alb['LoadBalancerArn'], Attributes=[
        {'Key': 'access_logs.s3.enabled', 'Value': 'true'}, {'Key': 'access_logs.s3.bucket', 'Value': LOG_BUCKET},
        {'Key': 'access_logs.s3.prefix', 'Value': ALB_PREFIX}])
    entries = [parse_alb_entry(line) for line in SAMPLE_ALB_LOG]
    readings = [{**{key: entry[key] for key in ('request', 'elb_status_code', 'target_status_code',
                                                  'target_processing_time', 'target')},
                 'cause': diagnose_alb(entry)} for entry in entries]
    causes = sorted({item['cause'] for item in readings if item['cause'] != 'ok'})
    _marker('alb-access-logs', {'causes': {cause: True for cause in causes}})
    response = {'LoadBalancer': alb.get('DNSName'), 'Attributes': {
        key: value for key, value in _alb_attributes().items() if key.startswith('access_logs')},
        'BucketPolicy': _log_delivery_policy(), 'Entries': readings,
        'Note': 'Floci writes no access log files; these are sample entries in the AWS format.'}
    verified, message = _check_alb()
    return _result('alb-access-logs', ALB_COMMAND, response, verified, message, started)


def step_dns_resolution() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _vpc()
    if not vpc:
        return _result('dns-resolution', DNS_COMMAND, {}, False, 'Build the network first.', started)
    ec2, route53, resolver = _client('ec2'), _client('route53'), _client('route53resolver')
    vpc_id = vpc['VpcId']
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': False})
    zone = _zone()
    if not zone:
        zone = route53.create_hosted_zone(Name=PRIVATE_ZONE, CallerReference=f'{LAB_KEY}-{time.time_ns()}',
                                          VPC={'VPCRegion': REGION, 'VPCId': vpc_id},
                                          HostedZoneConfig={'PrivateZone': True, 'Comment': PREFIX})['HostedZone']
    route53.change_resource_record_sets(HostedZoneId=zone['Id'], ChangeBatch={'Changes': [{
        'Action': 'UPSERT', 'ResourceRecordSet': {'Name': DB_RECORD, 'Type': 'A', 'TTL': 60,
                                                  'ResourceRecords': [{'Value': DB_IP}]}}]})
    groups = _groups()
    if DNS_SG not in groups:
        group = ec2.create_security_group(GroupName=DNS_SG, Description='DNS to on-premises', VpcId=vpc_id,
                                          TagSpecifications=_tag_spec('security-group', DNS_SG))['GroupId']
        ec2.authorize_security_group_egress(GroupId=group, IpPermissions=[
            {'IpProtocol': protocol, 'FromPort': 53, 'ToPort': 53, 'IpRanges': [{'CidrIp': f'{CORP_DNS}/32'}]}
            for protocol in ('tcp', 'udp')])
    endpoint = _endpoint()
    if not endpoint:
        subnets = _subnets()
        endpoint = resolver.create_resolver_endpoint(
            CreatorRequestId=f'{LAB_KEY}-{time.time_ns()}', Name=RESOLVER_ENDPOINT, Direction='OUTBOUND',
            SecurityGroupIds=[_groups()[DNS_SG]['GroupId']],
            IpAddresses=[{'SubnetId': subnets[APP]['SubnetId']}, {'SubnetId': subnets[WEB_B]['SubnetId']}])[
            'ResolverEndpoint']
    rule = _rule()
    if not rule:
        rule = resolver.create_resolver_rule(
            CreatorRequestId=f'{LAB_KEY}-{time.time_ns()}', Name=RESOLVER_RULE, RuleType='FORWARD',
            DomainName=CORP_DOMAIN, TargetIps=[{'Ip': CORP_DNS, 'Port': 53}],
            ResolverEndpointId=endpoint['Id'])['ResolverRule']
    if _rule_associated(rule['Id'], vpc_id):
        resolver.disassociate_resolver_rule(ResolverRuleId=rule['Id'], VPCId=vpc_id)
    if _domain_list():
        resolver.delete_firewall_domain_list(FirewallDomainListId=_domain_list()['Id'])
    before = _answers(_dns_inputs())
    _marker('dns-resolution', {'before': before})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
    resolver.associate_resolver_rule(ResolverRuleId=rule['Id'], VPCId=vpc_id, Name=f'{RESOLVER_RULE}-{PREFIX}')
    domain_list = resolver.create_firewall_domain_list(CreatorRequestId=f'{LAB_KEY}-{time.time_ns()}',
                                                       Name=DOMAIN_LIST)['FirewallDomainList']
    after = _answers(_dns_inputs())
    response = {'Before': before,
                'Fixes': {'enableDnsHostnames': True, 'RuleAssociation': {'Rule': rule['Id'], 'VPC': vpc_id},
                          'FirewallDomainList': {'Id': domain_list['Id'], 'Domains': BLOCKED_DOMAINS}},
                'After': after,
                'Note': 'Floci answers no DNS queries and cannot add domains to the list; the lab resolves each name.'}
    verified, message = _check_dns()
    return _result('dns-resolution', DNS_COMMAND, response, verified, message, started)


def _upsert_policy(name: str, parameters: dict[str, Any], comment: str) -> dict[str, Any]:
    cloudfront = _client('cloudfront')
    config = {'Name': name, 'Comment': comment, 'MinTTL': 0, 'DefaultTTL': 300, 'MaxTTL': 86400,
              'ParametersInCacheKeyAndForwardedToOrigin': parameters}
    existing = _cache_policy(name)
    if existing:
        etag = cloudfront.get_cache_policy(Id=existing['Id'])['ETag']
        return cloudfront.update_cache_policy(Id=existing['Id'], IfMatch=etag, CachePolicyConfig=config)[
            'CachePolicy']
    return cloudfront.create_cache_policy(CachePolicyConfig=config)['CachePolicy']


def _distribution_config(caller_reference: str, policy_id: str, origin: str) -> dict[str, Any]:
    return {
        'CallerReference': caller_reference, 'Comment': DIST_COMMENT, 'Enabled': True,
        'Origins': {'Quantity': 1, 'Items': [{
            'Id': 'storefront-alb', 'DomainName': origin,
            'CustomOriginConfig': {'HTTPPort': 80, 'HTTPSPort': 443, 'OriginProtocolPolicy': 'https-only',
                                   'OriginSslProtocols': {'Quantity': 1, 'Items': ['TLSv1.2']}}}]},
        'DefaultCacheBehavior': {'TargetOriginId': 'storefront-alb', 'ViewerProtocolPolicy': 'redirect-to-https',
                                 'CachePolicyId': policy_id, 'Compress': True},
    }


def step_cdn_cache_key() -> dict[str, Any]:
    started = time.perf_counter()
    alb = _alb()
    if not alb:
        return _result('cdn-cache-key', CDN_COMMAND, {}, False, 'Create the load balancer first.', started)
    cloudfront = _client('cloudfront')
    slow = _upsert_policy(SLOW_POLICY, SLOW_PARAMETERS, 'Every query string and cookie in the cache key')
    distribution = _distribution()
    if distribution:
        current = cloudfront.get_distribution_config(Id=distribution['Id'])
        config = _distribution_config(current['DistributionConfig']['CallerReference'], slow['Id'], alb['DNSName'])
        cloudfront.update_distribution(Id=distribution['Id'], IfMatch=current['ETag'], DistributionConfig=config)
    else:
        cloudfront.create_distribution(DistributionConfig=_distribution_config(
            f'{LAB_KEY}-{time.time_ns()}', slow['Id'], alb['DNSName']))
    before = hit_ratio(CDN_REQUESTS, SLOW_PARAMETERS)
    fast = _upsert_policy(FAST_POLICY, FAST_PARAMETERS, 'Only lang in the cache key')
    distribution = _distribution()
    current = cloudfront.get_distribution_config(Id=distribution['Id'])
    config = _distribution_config(current['DistributionConfig']['CallerReference'], fast['Id'], alb['DNSName'])
    cloudfront.update_distribution(Id=distribution['Id'], IfMatch=current['ETag'], DistributionConfig=config)
    _wait(lambda: (_distribution() or {}).get('Status') == 'Deployed', DEPLOY_TIMEOUT_SECONDS, 2)
    after = hit_ratio(CDN_REQUESTS, FAST_PARAMETERS)
    _marker('cdn-cache-key', {'before': before, 'after': after})
    distribution = _distribution() or {}
    response = {
        'Distribution': {key: distribution.get(key) for key in ('Id', 'DomainName', 'Status')},
        'Before': {'Policy': SLOW_POLICY, **before,
                   'SampleKeys': [cache_key(item, SLOW_PARAMETERS) for item in CDN_REQUESTS[:3]]},
        'After': {'Policy': FAST_POLICY, **after,
                  'Keys': sorted({cache_key(item, FAST_PARAMETERS) for item in CDN_REQUESTS})},
    }
    verified, message = _check_cdn()
    return _result('cdn-cache-key', CDN_COMMAND, response, verified, message, started)


RUNNERS = {
    'broken-web-path': step_broken_web_path,
    'flow-log-rejects': step_flow_log_rejects,
    'private-egress': step_private_egress,
    'ipv6-egress-only': step_ipv6_egress_only,
    'alb-access-logs': step_alb_access_logs,
    'dns-resolution': step_dns_resolution,
    'cdn-cache-key': step_cdn_cache_key,
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
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError):
        return False


def status() -> dict[str, Any]:
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


def reset() -> dict[str, Any]:
    """Remove the distribution, cache policies, DNS setup, load balancer, NAT gateway, network, and log bucket."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or 'NonExistent' in code:
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudfront = _client('cloudfront')
    distribution = _distribution()
    if distribution:
        distribution_id = distribution['Id']

        def delete_distribution() -> None:
            current = cloudfront.get_distribution_config(Id=distribution_id)
            config = current['DistributionConfig']
            if config.get('Enabled'):
                config['Enabled'] = False
                cloudfront.update_distribution(Id=distribution_id, IfMatch=current['ETag'], DistributionConfig=config)
                _wait(lambda: cloudfront.get_distribution(Id=distribution_id)['Distribution']['Status'] == 'Deployed',
                      DEPLOY_TIMEOUT_SECONDS, 2)
            cloudfront.delete_distribution(Id=distribution_id, IfMatch=cloudfront.get_distribution(
                Id=distribution_id)['ETag'])
        attempt(f'distribution:{distribution_id}', delete_distribution)
    for name in (SLOW_POLICY, FAST_POLICY):
        policy = _cache_policy(name)
        if policy:
            attempt(f'cache-policy:{name}', lambda policy=policy: cloudfront.delete_cache_policy(
                Id=policy['Id'], IfMatch=cloudfront.get_cache_policy(Id=policy['Id'])['ETag']))

    resolver = _client('route53resolver')
    vpc = _vpc()
    rule = _rule()
    if rule:
        if vpc and _rule_associated(rule['Id'], vpc['VpcId']):
            attempt(f'rule-association:{RESOLVER_RULE}', lambda: resolver.disassociate_resolver_rule(
                ResolverRuleId=rule['Id'], VPCId=vpc['VpcId']))
        attempt(f'resolver-rule:{RESOLVER_RULE}', lambda: resolver.delete_resolver_rule(ResolverRuleId=rule['Id']))
    endpoint = _endpoint()
    if endpoint:
        attempt(f'resolver-endpoint:{RESOLVER_ENDPOINT}', lambda: resolver.delete_resolver_endpoint(
            ResolverEndpointId=endpoint['Id']))
    domain_list = _domain_list()
    if domain_list:
        attempt(f'domain-list:{DOMAIN_LIST}', lambda: resolver.delete_firewall_domain_list(
            FirewallDomainListId=domain_list['Id']))
    route53 = _client('route53')
    zone = _zone()
    if zone:
        for record in _safe_call(lambda: route53.list_resource_record_sets(HostedZoneId=zone['Id'])[
                'ResourceRecordSets'], []) or []:
            if record['Type'] not in ('SOA', 'NS'):
                attempt(f'record:{record["Name"]}', lambda record=record: route53.change_resource_record_sets(
                    HostedZoneId=zone['Id'], ChangeBatch={'Changes': [{'Action': 'DELETE',
                                                                       'ResourceRecordSet': record}]}))
        attempt(f'hosted-zone:{PRIVATE_ZONE}', lambda: route53.delete_hosted_zone(Id=zone['Id']))

    alb = _alb()
    if alb:
        attempt(f'load-balancer:{ALB}', lambda: _client('elbv2').delete_load_balancer(
            LoadBalancerArn=alb['LoadBalancerArn']))

    ec2 = _client('ec2')
    nat = _nat()
    if nat:
        attempt(f'nat-gateway:{NAT}', lambda: ec2.delete_nat_gateway(NatGatewayId=nat['NatGatewayId']))
        _wait(lambda: _nat() is None, 30)
    for address in _safe_call(lambda: ec2.describe_addresses(Filters=_lab_filter())['Addresses'], []) or []:
        attempt(f'elastic-ip:{address.get("PublicIp")}', lambda address=address: ec2.release_address(
            AllocationId=address['AllocationId']))

    if vpc:
        vpc_id = vpc['VpcId']
        subnets = {item['SubnetId']: name for name, item in _subnets().items()}
        for subnet_id in subnets:
            for log in _safe_call(lambda subnet_id=subnet_id: ec2.describe_flow_logs(Filters=[
                    {'Name': 'resource-id', 'Values': [subnet_id]}])['FlowLogs'], []) or []:
                attempt(f'flow-log:{log["FlowLogId"]}', lambda log=log: ec2.delete_flow_logs(
                    FlowLogIds=[log['FlowLogId']]))
        for eni in _safe_call(lambda: ec2.describe_network_interfaces(Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]}])['NetworkInterfaces'], []) or []:
            attempt(f'network-interface:{eni["NetworkInterfaceId"]}', lambda eni=eni: ec2.delete_network_interface(
                NetworkInterfaceId=eni['NetworkInterfaceId']))
        eigw = _eigw()
        if eigw:
            attempt(f'egress-only-gateway:{EIGW}', lambda: ec2.delete_egress_only_internet_gateway(
                EgressOnlyInternetGatewayId=eigw['EgressOnlyInternetGatewayId']))
        igw = _igw()
        if igw:
            attempt(f'internet-gateway-detach:{IGW}', lambda: ec2.detach_internet_gateway(
                InternetGatewayId=igw['InternetGatewayId'], VpcId=vpc_id))
            attempt(f'internet-gateway:{IGW}', lambda: ec2.delete_internet_gateway(
                InternetGatewayId=igw['InternetGatewayId']))
        acls = _safe_call(lambda: ec2.describe_network_acls(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}])[
            'NetworkAcls'], []) or []
        default_acl = next((item['NetworkAclId'] for item in acls if item.get('IsDefault')), None)
        for acl in acls:
            if acl.get('IsDefault'):
                continue
            for association in acl.get('Associations', []):
                if default_acl:
                    attempt(f'acl-association:{association["SubnetId"]}', lambda association=association:
                            ec2.replace_network_acl_association(
                                AssociationId=association['NetworkAclAssociationId'], NetworkAclId=default_acl))
        for subnet_id, name in subnets.items():
            attempt(f'subnet:{name}', lambda subnet_id=subnet_id: ec2.delete_subnet(SubnetId=subnet_id))
        for acl in acls:
            if not acl.get('IsDefault'):
                attempt(f'network-acl:{acl["NetworkAclId"]}', lambda acl=acl: ec2.delete_network_acl(
                    NetworkAclId=acl['NetworkAclId']))
        for name, table in _tables().items():
            attempt(f'route-table:{name}', lambda table=table: ec2.delete_route_table(
                RouteTableId=table['RouteTableId']))
        for name, group in _groups().items():
            attempt(f'security-group:{name}', lambda group=group: ec2.delete_security_group(GroupId=group['GroupId']))
        attempt(f'vpc:{VPC_NAME}', lambda: ec2.delete_vpc(VpcId=vpc_id))

    s3 = _client('s3')
    if _bucket_exists():
        for item in _safe_call(lambda: s3.list_objects_v2(Bucket=LOG_BUCKET).get('Contents', []), []) or []:
            attempt(f'object:{item["Key"]}', lambda item=item: s3.delete_object(Bucket=LOG_BUCKET, Key=item['Key']))
        _safe_call(lambda: s3.delete_bucket_policy(Bucket=LOG_BUCKET))
        attempt(f'bucket:{LOG_BUCKET}', lambda: s3.delete_bucket(Bucket=LOG_BUCKET))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws cloudfront delete-distribution --id <dist> --if-match <etag>   # after disabling it\n'
            f'aws cloudfront delete-cache-policy --id <{SLOW_POLICY}> ; <{FAST_POLICY}>\n'
            f'aws route53resolver disassociate-resolver-rule ... ; delete-resolver-rule ; delete-resolver-endpoint\n'
            f'aws route53 delete-hosted-zone --id <{PRIVATE_ZONE}>   # after its records\n'
            f'aws elbv2 delete-load-balancer --load-balancer-arn <{ALB}>\n'
            'aws ec2 delete-nat-gateway --nat-gateway-id <nat>; aws ec2 release-address --allocation-id <eip>\n'
            f'aws ec2 delete-vpc --vpc-id <{VPC_NAME}>   # after its flow logs, interfaces, gateways, subnets, ACL, '
            'route tables and security groups\n'
            f'aws s3 rb s3://{LOG_BUCKET} --force'
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
            else 'The distribution, cache policies, DNS setup, load balancer, NAT gateway, network, and bucket are gone.',
        },
    }
