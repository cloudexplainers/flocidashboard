"""Network scale-out lab: three VPCs, a peering connection with DNS resolution, the limits of peering (no transitive
routing, no overlapping CIDRs), a Transit Gateway with VPC attachments, segmented Transit Gateway route tables with
static and blackhole routes, VPC flow logs to S3 and CloudWatch Logs, and a peering-versus-Transit-Gateway cost model.

Floci 2.3.0 stores the whole control plane: peering connections (with DNS options), Transit Gateways, attachments,
route tables, associations, static and blackhole routes, and flow logs. It moves no packets, accepts peering between
overlapping CIDRs and routes that would need transitive peering, never populates propagated routes, drops the
Transit Gateway target from DescribeRouteTables, and writes no flow log records. The lab evaluates routing itself
and says so.
"""

from __future__ import annotations

import ipaddress
import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ec2'
LAB_KEY = 'network-scale-out'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'
AZ = 'us-east-1a'
TAG = {'Key': 'Lab', 'Value': LAB_KEY}

SHARED, APP, DATA = 'lab-scale-shared', 'lab-scale-app', 'lab-scale-data'
VPCS = {
    SHARED: {'cidr': '10.40.0.0/16', 'subnet': '10.40.1.0/24', 'host': '10.40.1.10'},
    APP: {'cidr': '10.41.0.0/16', 'subnet': '10.41.1.0/24', 'host': '10.41.1.25'},
    DATA: {'cidr': '10.42.0.0/16', 'subnet': '10.42.1.0/24', 'host': '10.42.1.30'},
}
OVERLAP = 'lab-scale-overlap'
OVERLAP_CIDR = '10.41.0.0/16'
PEERING = 'lab-scale-shared-app'
SUMMARY_CIDR = '10.40.0.0/14'
TGW = 'lab-scale-tgw'
SHARED_RT = 'lab-scale-shared-rt'
SPOKES_RT = 'lab-scale-spokes-rt'
# Which attachment each Transit Gateway route table serves, and its static routes (None is a blackhole).
TGW_TABLES = {
    SHARED_RT: {'associations': [SHARED], 'routes': {VPCS[APP]['cidr']: APP, VPCS[DATA]['cidr']: DATA}},
    SPOKES_RT: {'associations': [APP, DATA],
                'routes': {VPCS[SHARED]['cidr']: SHARED, VPCS[APP]['cidr']: None, VPCS[DATA]['cidr']: None}},
}
EXPECTED_REACH = {
    (SHARED, APP): True, (APP, SHARED): True, (SHARED, DATA): True, (DATA, SHARED): True,
    (APP, DATA): False, (DATA, APP): False,
}

FLOW_BUCKET = 'lab-scale-flow-logs'
FLOW_PREFIX = 'flow/'
FLOW_FORMAT = ('${version} ${vpc-id} ${subnet-id} ${srcaddr} ${dstaddr} ${srcport} ${dstport} ${protocol} '
               '${packets} ${bytes} ${action} ${tcp-flags} ${flow-direction}')
FLOW_GROUP = '/lab/network-scale-out/flow-rejects'
FLOW_ROLE = 'lab-scale-flow-logs-role'
FLOW_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FLOW_ROLE}'
FLOW_TRUST = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {
    'Service': 'vpc-flow-logs.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
FLOW_ROLE_POLICY = {'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Action': [
    'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogGroups', 'logs:DescribeLogStreams'],
    'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:{FLOW_GROUP}:*'}]}
FLOW_BUCKET_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'AWSLogDeliveryAclCheck', 'Effect': 'Allow', 'Principal': {'Service': 'delivery.logs.amazonaws.com'},
         'Action': 's3:GetBucketAcl', 'Resource': f'arn:aws:s3:::{FLOW_BUCKET}',
         'Condition': {'StringEquals': {'aws:SourceAccount': ACCOUNT}}},
        {'Sid': 'AWSLogDeliveryWrite', 'Effect': 'Allow', 'Principal': {'Service': 'delivery.logs.amazonaws.com'},
         'Action': 's3:PutObject', 'Resource': f'arn:aws:s3:::{FLOW_BUCKET}/{FLOW_PREFIX}AWSLogs/{ACCOUNT}/*',
         'Condition': {'StringEquals': {'aws:SourceAccount': ACCOUNT, 's3:x-amz-acl': 'bucket-owner-full-control'}}},
    ],
}
# Records in FLOW_FORMAT as the app VPC would log them (Floci writes none).
SAMPLE_RECORDS = [
    '5 vpc-app subnet-app 10.41.1.25 10.40.1.10 49152 443 6 20 4200 ACCEPT 2 egress',
    '5 vpc-app subnet-app 10.40.1.10 10.41.1.25 443 49152 6 18 52000 ACCEPT 18 ingress',
    '5 vpc-app subnet-app 203.0.113.50 10.41.1.25 51515 22 6 1 60 REJECT 2 ingress',
    '5 vpc-app subnet-app 203.0.113.50 10.41.1.25 51516 3389 6 1 60 REJECT 2 ingress',
    '5 vpc-app subnet-app 198.51.100.7 10.41.1.25 40000 22 6 1 60 REJECT 2 ingress',
    '5 vpc-app subnet-app 10.41.1.25 10.42.1.30 50000 5432 6 3 180 ACCEPT 2 egress',
]

HOURS_PER_MONTH = 730
TGW_ATTACHMENT_HOUR = 0.05
TGW_PER_GB = 0.02
PEERING_CROSS_AZ_PER_GB = 0.02   # $0.01 out plus $0.01 in; traffic within one AZ is free
ROUTE_TABLE_DEFAULT_LIMIT = 50
COST_SCENARIOS = [(3, 1000), (10, 1000), (10, 20000)]

VPCS_COMMAND = (
    f'aws ec2 create-vpc --cidr-block {VPCS[SHARED]["cidr"]} --tag-specifications '
    f"'ResourceType=vpc,Tags=[{{Key=Name,Value={SHARED}}}]'   # and {APP} {VPCS[APP]['cidr']}, "
    f'{DATA} {VPCS[DATA]["cidr"]}\n'
    f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.40.1.0/24 --availability-zone {AZ}\n'
    'aws ec2 create-route-table --vpc-id <vpc-id>\n'
    'aws ec2 associate-route-table --route-table-id <rtb-id> --subnet-id <subnet-id>'
)
PEERING_COMMAND = (
    f'aws ec2 create-vpc-peering-connection --vpc-id <{SHARED}> --peer-vpc-id <{APP}>\n'
    'aws ec2 accept-vpc-peering-connection --vpc-peering-connection-id <pcx-id>\n'
    'aws ec2 modify-vpc-peering-connection-options --vpc-peering-connection-id <pcx-id> '
    '--requester-peering-connection-options AllowDnsResolutionFromRemoteVpc=true '
    '--accepter-peering-connection-options AllowDnsResolutionFromRemoteVpc=true\n'
    f'aws ec2 create-route --route-table-id <{SHARED}-rt> --destination-cidr-block {VPCS[APP]["cidr"]} '
    '--vpc-peering-connection-id <pcx-id>\n'
    f'aws ec2 create-route --route-table-id <{APP}-rt> --destination-cidr-block {VPCS[SHARED]["cidr"]} '
    '--vpc-peering-connection-id <pcx-id>'
)
LIMITS_COMMAND = (
    f'# Transitive: send {DATA} traffic from {APP} through the {SHARED} peering\n'
    f'aws ec2 create-route --route-table-id <{APP}-rt> --destination-cidr-block {VPCS[DATA]["cidr"]} '
    '--vpc-peering-connection-id <pcx-id>\n'
    f'# Overlap: peer {APP} with another {OVERLAP_CIDR} VPC\n'
    f'aws ec2 create-vpc --cidr-block {OVERLAP_CIDR}\n'
    f'aws ec2 create-vpc-peering-connection --vpc-id <{APP}> --peer-vpc-id <{OVERLAP}>'
)
TGW_COMMAND = (
    f'aws ec2 create-transit-gateway --description {TGW} --options '
    'DefaultRouteTableAssociation=disable,DefaultRouteTablePropagation=disable,DnsSupport=enable\n'
    'aws ec2 describe-transit-gateways --transit-gateway-ids <tgw-id>'
)
ATTACH_COMMAND = (
    'aws ec2 create-transit-gateway-vpc-attachment --transit-gateway-id <tgw-id> --vpc-id <vpc-id> '
    '--subnet-ids <subnet-id>   # each VPC\n'
    f'aws ec2 create-route --route-table-id <rtb-id> --destination-cidr-block {SUMMARY_CIDR} '
    '--transit-gateway-id <tgw-id>   # each VPC'
)
SEGMENT_COMMAND = (
    f'aws ec2 create-transit-gateway-route-table --transit-gateway-id <tgw-id>   # {SHARED_RT} and {SPOKES_RT}\n'
    f'aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id <{SHARED_RT}> '
    f'--transit-gateway-attachment-id <{SHARED}-attach>\n'
    f'aws ec2 associate-transit-gateway-route-table --transit-gateway-route-table-id <{SPOKES_RT}> '
    f'--transit-gateway-attachment-id <{APP}-attach>   # and {DATA}\n'
    f'aws ec2 create-transit-gateway-route --transit-gateway-route-table-id <{SPOKES_RT}> '
    f'--destination-cidr-block {VPCS[SHARED]["cidr"]} --transit-gateway-attachment-id <{SHARED}-attach>\n'
    f'aws ec2 create-transit-gateway-route --transit-gateway-route-table-id <{SPOKES_RT}> '
    f'--destination-cidr-block {VPCS[DATA]["cidr"]} --blackhole'
)
FLOW_COMMAND = (
    f'aws s3api create-bucket --bucket {FLOW_BUCKET}\n'
    f'aws s3api put-bucket-policy --bucket {FLOW_BUCKET} --policy file://flow-logs-bucket.json\n'
    f'aws ec2 create-flow-logs --resource-type VPC --resource-ids <{APP}> --traffic-type ALL '
    f'--log-destination-type s3 --log-destination arn:aws:s3:::{FLOW_BUCKET}/{FLOW_PREFIX} '
    "--max-aggregation-interval 60 --log-format '${version} ${vpc-id} ... ${flow-direction}'\n"
    f'aws logs create-log-group --log-group-name {FLOW_GROUP}\n'
    f'aws ec2 create-flow-logs --resource-type VPC --resource-ids <{APP}> --traffic-type REJECT '
    f'--log-group-name {FLOW_GROUP} --deliver-logs-permission-arn {FLOW_ROLE_ARN}'
)
CHOOSER_COMMAND = '# Price peering and Transit Gateway for 3 and 10 VPCs, then pick one for each scenario'

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Scale out a network with VPC peering, Transit Gateway, and flow logs',
    'description': (
        'Peer two of three VPCs with DNS resolution, see why peering is not transitive and refuses overlapping '
        'CIDRs, connect all three through a Transit Gateway, segment it so spoke VPCs reach only the shared VPC, '
        'capture traffic with VPC flow logs to S3 and CloudWatch Logs, and price peering against Transit Gateway.'
    ),
    'steps': [
        {
            'key': 'three-vpcs',
            'title': 'Create shared, app, and data VPCs with non-overlapping CIDRs',
            'command': VPCS_COMMAND,
            'explanation': (
                'Plan CIDRs before you connect anything: peering and Transit Gateway both need address ranges that '
                'do not overlap, and a summary range (here 10.40.0.0/14 covers all three /16s) keeps route tables '
                'short. Each VPC gets a subnet and its own route table. How Floci emulates this: the VPCs, subnets '
                'and route tables are real API objects; the step checks the ranges do not overlap.'
            ),
        },
        {
            'key': 'vpc-peering',
            'title': 'Peer the shared and app VPCs and route between them',
            'command': PEERING_COMMAND,
            'explanation': (
                'A peering connection is a one-to-one link: one side requests, the other accepts, and each side '
                'adds a route to the other CIDR through the pcx. DNS resolution across the peering lets private '
                'hostnames resolve to private IPs. Peering has no hourly charge and no bandwidth bottleneck; '
                'cross-AZ data costs $0.01/GB each way. How Floci emulates this: the request, acceptance, DNS '
                'options and routes are stored and read back; no packets flow.'
            ),
        },
        {
            'key': 'peering-limits',
            'title': 'Find the limits of peering: no transitive routing, no overlapping CIDRs',
            'command': LIMITS_COMMAND,
            'explanation': (
                'Peering is not transitive: app cannot reach data through shared, even with a route, because a '
                'peering connection only delivers to its own peer VPC (no edge-to-edge routing through gateways '
                'either). AWS also refuses to peer VPCs whose CIDRs overlap. N VPCs in a full mesh need '
                'N(N-1)/2 connections. How Floci emulates this: Floci accepts the transitive route and the '
                'overlapping peering, so the step evaluates both itself, then removes them.'
            ),
        },
        {
            'key': 'transit-gateway',
            'title': 'Create a Transit Gateway without default route tables',
            'command': TGW_COMMAND,
            'explanation': (
                'A Transit Gateway is a regional router that VPCs, VPNs and Direct Connect gateways attach to, so '
                'N VPCs need N attachments instead of a mesh, and routing is transitive. Turning off the default '
                'route table association and propagation lets you segment traffic yourself. How Floci emulates '
                'this: the gateway is created and reports available at once.'
            ),
        },
        {
            'key': 'tgw-attachments',
            'title': 'Attach all three VPCs and route the summary range to the Transit Gateway',
            'command': ATTACH_COMMAND,
            'explanation': (
                'Each VPC attaches through a subnet (one per AZ in production) and sends the 10.40.0.0/14 summary '
                'to the Transit Gateway. Longest prefix wins, so shared and app keep using their /16 peering route '
                'while everything else goes through the gateway. Attachments cost $0.05 an hour each plus $0.02/GB '
                'processed. How Floci emulates this: attachments are available at once; DescribeRouteTables drops '
                'the Transit Gateway target, so the step accepts the summary route without one and works out next '
                'hops itself.'
            ),
        },
        {
            'key': 'tgw-segmentation',
            'title': 'Segment the Transit Gateway so spokes reach only the shared VPC',
            'command': SEGMENT_COMMAND,
            'explanation': (
                'Transit Gateway route tables decide where traffic from each attachment can go. The shared table '
                'routes to both spokes; the spokes table routes only to shared and blackholes the spoke ranges, so '
                'app and data cannot talk to each other (a common shared-services or inspection design). In AWS, '
                'propagation would fill routes automatically. How Floci emulates this: associations, static routes '
                'and blackholes are stored, but propagated routes never appear, so the step uses static routes and '
                'evaluates reachability itself.'
            ),
        },
        {
            'key': 'flow-logs',
            'title': 'Capture traffic with VPC flow logs to S3 and CloudWatch Logs',
            'command': FLOW_COMMAND,
            'explanation': (
                'Flow logs record accepted and rejected IP traffic metadata (not packet contents) for a VPC, subnet '
                'or ENI. S3 is cheap for long retention and Athena queries; CloudWatch Logs suits alarms on '
                'rejected traffic and needs a role that VPC Flow Logs can assume. A custom format adds fields such '
                'as flow-direction and tcp-flags. How Floci emulates this: both flow logs are stored as ACTIVE but '
                'no records are written, so the step analyzes sample records itself.'
            ),
        },
        {
            'key': 'peering-vs-tgw',
            'title': 'Price peering against Transit Gateway and choose for each scenario',
            'command': CHOOSER_COMMAND,
            'explanation': (
                'Peering is cheapest (no hourly fee) and fastest for a few VPCs, but connections grow as N(N-1)/2 '
                'and routes per table are limited. Transit Gateway costs per attachment-hour and per GB but scales '
                'to thousands of VPCs, routes transitively, centralizes inspection, and shares VPN and Direct '
                'Connect. PrivateLink fits exposing one service to many VPCs, even with overlapping CIDRs. How '
                'Floci emulates this: the step prices both designs with us-east-1 list prices.'
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


def _tag_spec(resource: str, name: str) -> list[dict[str, Any]]:
    return [{'ResourceType': resource, 'Tags': [{'Key': 'Name', 'Value': name}, TAG]}]


def _name(item: dict[str, Any]) -> str | None:
    return next((tag['Value'] for tag in item.get('Tags') or [] if tag['Key'] == 'Name'), None)


# ── Pure logic: CIDRs, routing, flow logs, cost ─────────────────────────────

def overlapping(cidrs: dict[str, str]) -> list[tuple[str, str]]:
    names = list(cidrs)
    return [(a, b) for index, a in enumerate(names) for b in names[index + 1:]
            if ipaddress.ip_network(cidrs[a]).overlaps(ipaddress.ip_network(cidrs[b]))]


def next_hop(routes: list[dict[str, Any]], address: str) -> dict[str, Any] | None:
    """Longest-prefix match over a VPC route table."""
    ip = ipaddress.ip_address(address)
    matches = [route for route in routes if route.get('DestinationCidrBlock')
               and ip in ipaddress.ip_network(route['DestinationCidrBlock'])]
    return max(matches, key=lambda route: ipaddress.ip_network(route['DestinationCidrBlock']).prefixlen, default=None)


def peering_reachable(source: str, target: str, routes: dict[str, list[dict[str, Any]]],
                      peerings: dict[str, tuple[str, str]]) -> bool:
    """Peering delivers only to the peer VPC itself: both sides need a route through a pcx joining exactly them."""
    def via_direct_peering(src: str, dst: str) -> bool:
        hop = next_hop(routes.get(src, []), VPCS[dst]['host'])
        pcx = (hop or {}).get('VpcPeeringConnectionId')
        return bool(pcx) and set(peerings.get(pcx, ())) == {src, dst}
    return via_direct_peering(source, target) and via_direct_peering(target, source)


def tgw_lookup(table: dict[str, Any], address: str) -> str | None:
    """The attachment (VPC name) a Transit Gateway route table sends an address to; None if blackholed or no route."""
    ip = ipaddress.ip_address(address)
    matches = [(ipaddress.ip_network(cidr), target) for cidr, target in table['routes'].items()
               if ip in ipaddress.ip_network(cidr)]
    if not matches:
        return None
    return max(matches, key=lambda match: match[0].prefixlen)[1]


def tgw_reachability(tables: dict[str, dict[str, Any]]) -> dict[tuple[str, str], bool]:
    """Whether each VPC reaches each other through the Transit Gateway, both ways (return traffic needs a route too)."""
    table_for = {vpc: table for table in tables.values() for vpc in table['associations']}

    def one_way(src: str, dst: str) -> bool:
        table = table_for.get(src)
        return bool(table) and tgw_lookup(table, VPCS[dst]['host']) == dst

    return {(src, dst): one_way(src, dst) and one_way(dst, src)
            for src in VPCS for dst in VPCS if src != dst}


def analyze_flow_records(records: list[str], fields: str = FLOW_FORMAT) -> dict[str, Any]:
    names = [field.strip('${}') for field in fields.split()]
    rows = [dict(zip(names, record.split())) for record in records]
    rejected = [row for row in rows if row.get('action') == 'REJECT']
    by_port: dict[str, int] = {}
    for row in rejected:
        by_port[row['dstport']] = by_port.get(row['dstport'], 0) + 1
    return {
        'Records': len(rows),
        'Accepted': len(rows) - len(rejected),
        'Rejected': len(rejected),
        'RejectedSources': sorted({row['srcaddr'] for row in rejected}),
        'RejectedPorts': dict(sorted(by_port.items())),
        'BytesAccepted': sum(int(row['bytes']) for row in rows if row.get('action') == 'ACCEPT'),
    }


def mesh_connections(vpcs: int) -> int:
    return vpcs * (vpcs - 1) // 2


def network_costs(vpcs: int, gb_per_month: int) -> dict[str, Any]:
    peering = round(gb_per_month * PEERING_CROSS_AZ_PER_GB, 2)
    tgw_hours = round(vpcs * HOURS_PER_MONTH * TGW_ATTACHMENT_HOUR, 2)
    tgw = round(tgw_hours + gb_per_month * TGW_PER_GB, 2)
    connections = mesh_connections(vpcs)
    return {
        'VPCs': vpcs,
        'GBPerMonth': gb_per_month,
        'PeeringConnections': connections,
        'PeeringRoutesPerTable': vpcs - 1,
        'PeeringMonthlyUSD': peering,
        'TgwAttachments': vpcs,
        'TgwMonthlyUSD': tgw,
        'TgwAttachmentHoursUSD': tgw_hours,
        'Choice': choose_network(vpcs),
    }


def choose_network(vpcs: int, transitive: bool = False, shared_vpn: bool = False) -> str:
    if transitive or shared_vpn or vpcs > 5:
        return 'Transit Gateway'
    return 'VPC peering'


# ── Lookups ──────────────────────────────────────────────────────────────────

def _vpcs() -> dict[str, dict[str, Any]]:
    found = _client('ec2').describe_vpcs(Filters=[{'Name': 'tag:Lab', 'Values': [LAB_KEY]}]).get('Vpcs', [])
    return {_name(vpc): vpc for vpc in found if _name(vpc)}


def _subnet(vpc_id: str) -> dict[str, Any] | None:
    subnets = _client('ec2').describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', [])
    return subnets[0] if subnets else None


def _route_table(vpc_id: str, name: str) -> dict[str, Any] | None:
    tables = _client('ec2').describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get(
        'RouteTables', [])
    return next((table for table in tables if _name(table) == f'{name}-rt'), None)


def _route_tables() -> dict[str, dict[str, Any]]:
    tables = {}
    for name, vpc in _vpcs().items():
        if name in VPCS:
            table = _route_table(vpc['VpcId'], name)
            if table:
                tables[name] = table
    return tables


def _peerings() -> list[dict[str, Any]]:
    vpc_ids = [vpc['VpcId'] for vpc in _vpcs().values()]
    if not vpc_ids:
        return []
    ec2 = _client('ec2')
    found: dict[str, dict[str, Any]] = {}
    for key in ('requester-vpc-info.vpc-id', 'accepter-vpc-info.vpc-id'):
        for pcx in ec2.describe_vpc_peering_connections(Filters=[{'Name': key, 'Values': vpc_ids}]).get(
                'VpcPeeringConnections', []):
            if (pcx.get('Status') or {}).get('Code') not in ('deleted', 'deleting', 'rejected', 'failed'):
                found[pcx['VpcPeeringConnectionId']] = pcx
    return list(found.values())


def _peering_pairs() -> dict[str, tuple[str, str]]:
    names = {vpc['VpcId']: name for name, vpc in _vpcs().items()}
    return {pcx['VpcPeeringConnectionId']: (names.get(pcx['RequesterVpcInfo']['VpcId']),
                                            names.get(pcx['AccepterVpcInfo']['VpcId']))
            for pcx in _peerings()}


def _lab_peering() -> dict[str, Any] | None:
    pairs = _peering_pairs()
    return next((pcx for pcx in _peerings() if set(pairs[pcx['VpcPeeringConnectionId']]) == {SHARED, APP}), None)


def _tgw() -> dict[str, Any] | None:
    gateways = _client('ec2').describe_transit_gateways().get('TransitGateways', [])
    return next((tgw for tgw in gateways if _name(tgw) == TGW and tgw.get('State') not in ('deleted', 'deleting')),
                None)


def _attachments(tgw_id: str) -> dict[str, dict[str, Any]]:
    names = {vpc['VpcId']: name for name, vpc in _vpcs().items()}
    found = _client('ec2').describe_transit_gateway_vpc_attachments(Filters=[
        {'Name': 'transit-gateway-id', 'Values': [tgw_id]}]).get('TransitGatewayVpcAttachments', [])
    return {names[item['VpcId']]: item for item in found
            if item['VpcId'] in names and item.get('State') not in ('deleted', 'deleting')}


def _tgw_tables(tgw_id: str) -> dict[str, dict[str, Any]]:
    found = _client('ec2').describe_transit_gateway_route_tables(Filters=[
        {'Name': 'transit-gateway-id', 'Values': [tgw_id]}]).get('TransitGatewayRouteTables', [])
    return {_name(table): table for table in found
            if _name(table) and table.get('State') not in ('deleted', 'deleting')}


def _tgw_table_model(tgw_id: str) -> dict[str, dict[str, Any]]:
    """Read the Transit Gateway route tables back as {name: {'associations': [vpc], 'routes': {cidr: vpc|None}}}."""
    ec2 = _client('ec2')
    by_attachment = {item['TransitGatewayAttachmentId']: name for name, item in _attachments(tgw_id).items()}
    model = {}
    for name, table in _tgw_tables(tgw_id).items():
        table_id = table['TransitGatewayRouteTableId']
        associations = ec2.get_transit_gateway_route_table_associations(
            TransitGatewayRouteTableId=table_id).get('Associations', [])
        routes = ec2.search_transit_gateway_routes(TransitGatewayRouteTableId=table_id, Filters=[
            {'Name': 'state', 'Values': ['active', 'blackhole']}]).get('Routes', [])
        model[name] = {
            'associations': sorted(by_attachment.get(item['TransitGatewayAttachmentId'], '?')
                                   for item in associations if item.get('State') in ('associated', 'associating')),
            'routes': {route['DestinationCidrBlock']: (
                None if route.get('State') == 'blackhole' else
                by_attachment.get(((route.get('TransitGatewayAttachments') or [{}])[0]).get(
                    'TransitGatewayAttachmentId'), '?'))
                for route in routes},
            'propagated': [route['DestinationCidrBlock'] for route in routes if route.get('Type') == 'propagated'],
        }
    return model


def _flow_logs(vpc_id: str) -> list[dict[str, Any]]:
    return _client('ec2').describe_flow_logs(Filters=[{'Name': 'resource-id', 'Values': [vpc_id]}]).get(
        'FlowLogs', [])


def _bucket_policy() -> dict[str, Any] | None:
    try:
        return json.loads(_client('s3').get_bucket_policy(Bucket=FLOW_BUCKET)['Policy'])
    except ClientError as exc:
        if _code(exc) in ('NoSuchBucket', 'NoSuchBucketPolicy'):
            return None
        raise


def _role_exists() -> bool:
    try:
        _client('iam').get_role(RoleName=FLOW_ROLE)
        return True
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return False
        raise


def _summary_route(table: dict[str, Any] | None, tgw_id: str) -> dict[str, Any] | None:
    for route in (table or {}).get('Routes', []):
        if route.get('DestinationCidrBlock') != SUMMARY_CIDR:
            continue
        # Floci drops TransitGatewayId from DescribeRouteTables, so a summary route with no other target counts.
        others = [key for key in ('GatewayId', 'NatGatewayId', 'VpcPeeringConnectionId', 'NetworkInterfaceId',
                                  'InstanceId') if route.get(key)]
        if route.get('TransitGatewayId') == tgw_id or (not route.get('TransitGatewayId') and not others):
            return route
    return None


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_vpcs() -> tuple[bool, str]:
    vpcs = _vpcs()
    for name, spec in VPCS.items():
        vpc = vpcs.get(name)
        if not vpc or vpc.get('CidrBlock') != spec['cidr']:
            return False, f'VPC {name} ({spec["cidr"]}) was not found.'
        subnet = _subnet(vpc['VpcId'])
        if not subnet or subnet.get('CidrBlock') != spec['subnet']:
            return False, f'{name} needs subnet {spec["subnet"]}.'
        table = _route_table(vpc['VpcId'], name)
        if not table or not any(item.get('SubnetId') == subnet['SubnetId'] for item in table.get('Associations', [])):
            return False, f'{name}-rt must be associated with the {name} subnet.'
    clashes = overlapping({name: vpcs[name]['CidrBlock'] for name in VPCS})
    if clashes:
        return False, f'CIDRs overlap: {clashes}.'
    return True, (f'{SHARED} {VPCS[SHARED]["cidr"]}, {APP} {VPCS[APP]["cidr"]} and {DATA} {VPCS[DATA]["cidr"]} each '
                  f'have a subnet and route table, and no ranges overlap (all fit in {SUMMARY_CIDR}).')


def _check_peering() -> tuple[bool, str]:
    pcx = _lab_peering()
    if not pcx or (pcx.get('Status') or {}).get('Code') != 'active':
        return False, f'No active peering connection between {SHARED} and {APP}.'
    for side in ('RequesterVpcInfo', 'AccepterVpcInfo'):
        if not ((pcx.get(side) or {}).get('PeeringOptions') or {}).get('AllowDnsResolutionFromRemoteVpc'):
            return False, 'Turn on DNS resolution from the remote VPC on both sides.'
    tables = _route_tables()
    routes = {name: table.get('Routes', []) for name, table in tables.items()}
    if not peering_reachable(SHARED, APP, routes, _peering_pairs()):
        return False, f'Both route tables need a route to the other VPC through {pcx["VpcPeeringConnectionId"]}.'
    return True, (f'{pcx["VpcPeeringConnectionId"]} is active with DNS resolution on both sides, and {SHARED} and '
                  f'{APP} route to each other through it.')


def _check_limits() -> tuple[bool, str]:
    marker = _marked('peering-limits')
    if not marker:
        return False, 'Run the peering limits step.'
    if OVERLAP in _vpcs():
        return False, f'Delete {OVERLAP}; it overlaps {APP}.'
    app_table = _route_tables().get(APP)
    if any(route.get('DestinationCidrBlock') == VPCS[DATA]['cidr'] and route.get('VpcPeeringConnectionId')
           for route in (app_table or {}).get('Routes', [])):
        return False, f'Remove the transitive route to {DATA} from {APP}-rt.'
    ok, problem = _check_peering()
    if not ok:
        return False, problem
    return True, marker['message']


def _check_tgw() -> tuple[bool, str]:
    tgw = _tgw()
    if not tgw or tgw.get('State') != 'available':
        return False, f'Transit Gateway {TGW} is not available.'
    options = tgw.get('Options') or {}
    if options.get('DefaultRouteTableAssociation') != 'disable' or options.get('DefaultRouteTablePropagation') != 'disable':
        return False, f'{TGW} must have default route table association and propagation turned off.'
    return True, f'{TGW} ({tgw["TransitGatewayId"]}, ASN {options.get("AmazonSideAsn")}) is available with no default route table.'


def _check_attachments() -> tuple[bool, str]:
    tgw = _tgw()
    if not tgw:
        return False, f'Create {TGW} first.'
    attachments = _attachments(tgw['TransitGatewayId'])
    missing = [name for name in VPCS if (attachments.get(name) or {}).get('State') != 'available']
    if missing:
        return False, f'Attach {", ".join(missing)} to {TGW}.'
    tables = _route_tables()
    without = [name for name in VPCS if not _summary_route(tables.get(name), tgw['TransitGatewayId'])]
    if without:
        return False, f'{", ".join(f"{name}-rt" for name in without)} must send {SUMMARY_CIDR} to {TGW}.'
    return True, (f'All three VPCs are attached to {TGW} and send {SUMMARY_CIDR} to it.')


def _check_segmentation() -> tuple[bool, str]:
    tgw = _tgw()
    if not tgw:
        return False, f'Create {TGW} first.'
    model = _tgw_table_model(tgw['TransitGatewayId'])
    for name, expected in TGW_TABLES.items():
        table = model.get(name)
        if not table:
            return False, f'Transit Gateway route table {name} was not found.'
        if table['associations'] != sorted(expected['associations']):
            return False, f'{name} must be associated with {", ".join(expected["associations"])}.'
        for cidr, target in expected['routes'].items():
            if cidr not in table['routes'] or table['routes'][cidr] != target:
                want = 'a blackhole' if target is None else f'the {target} attachment'
                return False, f'{name} needs {cidr} to {want}.'
    reach = tgw_reachability(model)
    if reach != EXPECTED_REACH:
        return False, f'Reachability {reach} does not match the design.'
    return True, (f'{SHARED_RT} reaches both spokes, {SPOKES_RT} reaches only {SHARED} and blackholes the spoke '
                  f'ranges: shared↔app and shared↔data work, app↔data is dropped.')


def _check_flow_logs() -> tuple[bool, str]:
    vpc = _vpcs().get(APP)
    if not vpc:
        return False, f'Create {APP} first.'
    logs = _flow_logs(vpc['VpcId'])
    s3 = next((item for item in logs if item.get('LogDestinationType') == 's3'
               and item.get('LogDestination') == f'arn:aws:s3:::{FLOW_BUCKET}/{FLOW_PREFIX}'), None)
    if not s3 or s3.get('TrafficType') != 'ALL' or s3.get('LogFormat') != FLOW_FORMAT \
            or s3.get('MaxAggregationInterval') != 60:
        return False, f'{APP} needs an ALL-traffic flow log to {FLOW_BUCKET}/{FLOW_PREFIX} with the custom format and 60 s aggregation.'
    # Floci reads a CloudWatch Logs flow log back without its log group, so a missing name counts.
    cw = next((item for item in logs if item.get('LogDestinationType') == 'cloud-watch-logs'
               and item.get('LogGroupName', FLOW_GROUP) == FLOW_GROUP), None)
    if not cw or cw.get('TrafficType') != 'REJECT' or cw.get('DeliverLogsPermissionArn') != FLOW_ROLE_ARN:
        return False, f'{APP} needs a REJECT flow log to {FLOW_GROUP} delivered with {FLOW_ROLE}.'
    policy = _bucket_policy()
    principals = [((statement.get('Principal') or {}).get('Service')) for statement in (policy or {}).get('Statement', [])]
    if principals.count('delivery.logs.amazonaws.com') < 2:
        return False, f'The {FLOW_BUCKET} policy must let delivery.logs.amazonaws.com check the ACL and write.'
    if not _role_exists():
        return False, f'Role {FLOW_ROLE} was not found.'
    return True, (f'{APP} sends all traffic to s3://{FLOW_BUCKET}/{FLOW_PREFIX} in the custom format and rejected '
                  f'traffic to {FLOW_GROUP}.')


def _check_chooser() -> tuple[bool, str]:
    for check in (_check_peering, _check_segmentation):
        ok, problem = check()
        if not ok:
            return False, problem
    marker = _marked('peering-vs-tgw')
    if not marker:
        return False, 'Run the chooser.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def _ensure_vpc(name: str, cidr: str, subnet_cidr: str | None) -> dict[str, Any]:
    ec2 = _client('ec2')
    vpc = _vpcs().get(name)
    if not vpc:
        vpc = ec2.create_vpc(CidrBlock=cidr, TagSpecifications=_tag_spec('vpc', name))['Vpc']
    vpc_id = vpc['VpcId']
    if subnet_cidr is None:
        return vpc
    subnet = _subnet(vpc_id)
    if not subnet:
        subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock=subnet_cidr, AvailabilityZone=AZ,
                                   TagSpecifications=_tag_spec('subnet', f'{name}-a'))['Subnet']
    table = _route_table(vpc_id, name)
    if not table:
        table = ec2.create_route_table(VpcId=vpc_id, TagSpecifications=_tag_spec('route-table', f'{name}-rt'))[
            'RouteTable']
    if not any(item.get('SubnetId') == subnet['SubnetId'] for item in table.get('Associations', [])):
        ec2.associate_route_table(RouteTableId=table['RouteTableId'], SubnetId=subnet['SubnetId'])
    return vpc


def _put_route(table_id: str, cidr: str, **target: str) -> None:
    ec2 = _client('ec2')
    try:
        ec2.create_route(RouteTableId=table_id, DestinationCidrBlock=cidr, **target)
    except ClientError as exc:
        if _code(exc) != 'RouteAlreadyExists':
            raise
        # Floci's ReplaceRoute refuses Transit Gateway targets, so replace by delete and create.
        ec2.delete_route(RouteTableId=table_id, DestinationCidrBlock=cidr)
        ec2.create_route(RouteTableId=table_id, DestinationCidrBlock=cidr, **target)


def step_three_vpcs() -> dict[str, Any]:
    started = time.perf_counter()
    for name, spec in VPCS.items():
        _ensure_vpc(name, spec['cidr'], spec['subnet'])
    vpcs = _vpcs()
    response = {'Vpcs': [{'Name': name, 'VpcId': vpcs[name]['VpcId'], 'CidrBlock': vpcs[name]['CidrBlock']}
                         for name in VPCS if name in vpcs],
                'Overlaps': overlapping({name: spec['cidr'] for name, spec in VPCS.items()})}
    verified, message = _check_vpcs()
    return _result('three-vpcs', VPCS_COMMAND, response, verified, message, started)


def step_vpc_peering() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    vpcs = _vpcs()
    if not all(name in vpcs for name in VPCS):
        return _result('vpc-peering', PEERING_COMMAND, {}, False, 'Create the three VPCs first.', started)
    pcx = _lab_peering()
    if not pcx:
        pcx = ec2.create_vpc_peering_connection(VpcId=vpcs[SHARED]['VpcId'], PeerVpcId=vpcs[APP]['VpcId'],
                                                TagSpecifications=_tag_spec('vpc-peering-connection', PEERING))[
            'VpcPeeringConnection']
    pcx_id = pcx['VpcPeeringConnectionId']
    requested = (pcx.get('Status') or {}).get('Code')
    if requested == 'pending-acceptance':
        ec2.accept_vpc_peering_connection(VpcPeeringConnectionId=pcx_id)
    ec2.modify_vpc_peering_connection_options(
        VpcPeeringConnectionId=pcx_id,
        RequesterPeeringConnectionOptions={'AllowDnsResolutionFromRemoteVpc': True},
        AccepterPeeringConnectionOptions={'AllowDnsResolutionFromRemoteVpc': True})
    tables = _route_tables()
    _put_route(tables[SHARED]['RouteTableId'], VPCS[APP]['cidr'], VpcPeeringConnectionId=pcx_id)
    _put_route(tables[APP]['RouteTableId'], VPCS[SHARED]['cidr'], VpcPeeringConnectionId=pcx_id)
    current = _lab_peering() or {}
    response = {'VpcPeeringConnectionId': pcx_id, 'StatusWhenRequested': requested,
                'Status': (current.get('Status') or {}).get('Code'),
                'DnsResolution': {side: ((current.get(side) or {}).get('PeeringOptions') or {}).get(
                    'AllowDnsResolutionFromRemoteVpc') for side in ('RequesterVpcInfo', 'AccepterVpcInfo')}}
    verified, message = _check_peering()
    return _result('vpc-peering', PEERING_COMMAND, response, verified, message, started)


def step_peering_limits() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    pcx = _lab_peering()
    vpcs = _vpcs()
    if not pcx or APP not in vpcs:
        return _result('peering-limits', LIMITS_COMMAND, {}, False, 'Peer the shared and app VPCs first.', started)
    pcx_id = pcx['VpcPeeringConnectionId']
    app_table = _route_tables()[APP]['RouteTableId']

    # 1. A route that would need transitive peering.
    transitive: dict[str, Any] = {}
    try:
        _put_route(app_table, VPCS[DATA]['cidr'], VpcPeeringConnectionId=pcx_id)
        transitive['FlociAcceptedRoute'] = True
    except ClientError as exc:
        transitive['FlociAcceptedRoute'] = False
        transitive['Code'] = _code(exc)
    routes = {name: table.get('Routes', []) for name, table in _route_tables().items()}
    hop = next_hop(routes.get(APP, []), VPCS[DATA]['host'])
    transitive['NextHopFromApp'] = (hop or {}).get('VpcPeeringConnectionId')
    transitive['AppReachesData'] = peering_reachable(APP, DATA, routes, _peering_pairs())
    transitive['AppReachesShared'] = peering_reachable(APP, SHARED, routes, _peering_pairs())
    try:
        ec2.delete_route(RouteTableId=app_table, DestinationCidrBlock=VPCS[DATA]['cidr'])
        transitive['RouteRemoved'] = True
    except ClientError as exc:
        transitive['RouteRemoved'] = _code(exc) in ('InvalidRoute.NotFound',)

    # 2. Peering two VPCs whose CIDRs overlap.
    overlap: dict[str, Any] = {'Overlaps': overlapping({APP: VPCS[APP]['cidr'], OVERLAP: OVERLAP_CIDR})}
    other = _ensure_vpc(OVERLAP, OVERLAP_CIDR, None)
    try:
        request = ec2.create_vpc_peering_connection(VpcId=vpcs[APP]['VpcId'], PeerVpcId=other['VpcId'])[
            'VpcPeeringConnection']
        overlap['FlociStatus'] = (request.get('Status') or {}).get('Code')
        ec2.delete_vpc_peering_connection(VpcPeeringConnectionId=request['VpcPeeringConnectionId'])
    except ClientError as exc:
        overlap['FlociStatus'] = _code(exc)
    ec2.delete_vpc(VpcId=other['VpcId'])
    overlap['AwsStatus'] = 'failed' if overlap['Overlaps'] else 'pending-acceptance'

    response = {'Transitive': transitive, 'OverlappingPeering': overlap,
                'FullMeshConnections': {vpcs_count: mesh_connections(vpcs_count) for vpcs_count in (3, 10, 50)}}
    if transitive['AppReachesData'] or not transitive['AppReachesShared'] or not overlap['Overlaps']:
        return _result('peering-limits', LIMITS_COMMAND, response, False,
                       'Expected app to reach shared but not data, and the overlap to be detected.', started)
    message = (f'{APP} reaches {SHARED} but not {DATA}: a route to {VPCS[DATA]["cidr"]} through {pcx_id} goes '
               f'nowhere, because peering only delivers to its peer VPC. Peering {APP} with another '
               f'{OVERLAP_CIDR} VPC fails in AWS. A full mesh of 10 VPCs would need 45 connections.')
    floci_gaps = []
    if transitive['FlociAcceptedRoute']:
        floci_gaps.append('accepted the transitive route')
    if overlap['FlociStatus'] not in ('failed', 'InvalidVpcPeeringConnection.Overlap'):
        floci_gaps.append(f'left the overlapping peering {overlap["FlociStatus"]}')
    if floci_gaps:
        message += f' Floci {" and ".join(floci_gaps)}, so the step evaluated both itself and removed them.'
    _marker('peering-limits', {'message': message})
    verified, check = _check_limits()
    return _result('peering-limits', LIMITS_COMMAND, response, verified, check if not verified else message, started)


def step_transit_gateway() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    tgw = _tgw()
    if not tgw:
        tgw = ec2.create_transit_gateway(Description=TGW, Options={
            'DefaultRouteTableAssociation': 'disable', 'DefaultRouteTablePropagation': 'disable',
            'DnsSupport': 'enable', 'VpnEcmpSupport': 'enable'},
            TagSpecifications=_tag_spec('transit-gateway', TGW))['TransitGateway']
    deadline = time.monotonic() + 60
    while (_tgw() or {}).get('State') == 'pending' and time.monotonic() < deadline:
        time.sleep(2)
    current = _tgw() or {}
    response = {'TransitGatewayId': current.get('TransitGatewayId'), 'State': current.get('State'),
                'Options': current.get('Options')}
    verified, message = _check_tgw()
    if verified:
        message += ' In AWS it takes a few minutes to become available; Floci makes it available at once.'
    return _result('transit-gateway', TGW_COMMAND, response, verified, message, started)


def step_tgw_attachments() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    tgw = _tgw()
    vpcs = _vpcs()
    if not tgw or not all(name in vpcs for name in VPCS):
        return _result('tgw-attachments', ATTACH_COMMAND, {}, False,
                       f'Create the three VPCs and {TGW} first.', started)
    tgw_id = tgw['TransitGatewayId']
    existing = _attachments(tgw_id)
    for name in VPCS:
        if name not in existing:
            subnet = _subnet(vpcs[name]['VpcId'])
            ec2.create_transit_gateway_vpc_attachment(
                TransitGatewayId=tgw_id, VpcId=vpcs[name]['VpcId'], SubnetIds=[subnet['SubnetId']],
                TagSpecifications=_tag_spec('transit-gateway-attachment', f'{name}-attach'))
    deadline = time.monotonic() + 60
    while any(item.get('State') == 'pending' for item in _attachments(tgw_id).values()) and time.monotonic() < deadline:
        time.sleep(2)
    tables = _route_tables()
    for name in VPCS:
        _put_route(tables[name]['RouteTableId'], SUMMARY_CIDR, TransitGatewayId=tgw_id)
    tables = _route_tables()
    hops = {}
    for src in VPCS:
        for dst in VPCS:
            if src == dst:
                continue
            hop = next_hop(tables[src].get('Routes', []), VPCS[dst]['host']) or {}
            hops[f'{src} -> {dst}'] = ('peering ' + hop['VpcPeeringConnectionId'] if hop.get('VpcPeeringConnectionId')
                                       else f'{TGW} ({hop.get("DestinationCidrBlock")})')
    response = {'Attachments': {name: item.get('State') for name, item in _attachments(tgw_id).items()},
                'NextHops': hops,
                'SummaryRouteAsReadBack': _summary_route(tables[APP], tgw_id)}
    verified, message = _check_attachments()
    if verified:
        message += (f' Longest prefix wins: {SHARED} and {APP} still use their /16 peering route, and everything '
                    f'else goes through {TGW}.')
        if not (response['SummaryRouteAsReadBack'] or {}).get('TransitGatewayId'):
            message += ' Floci reads the summary route back without its Transit Gateway target.'
    return _result('tgw-attachments', ATTACH_COMMAND, response, verified, message, started)


def step_tgw_segmentation() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    tgw = _tgw()
    if not tgw:
        return _result('tgw-segmentation', SEGMENT_COMMAND, {}, False, f'Create {TGW} first.', started)
    tgw_id = tgw['TransitGatewayId']
    attachments = _attachments(tgw_id)
    if not all(name in attachments for name in VPCS):
        return _result('tgw-segmentation', SEGMENT_COMMAND, {}, False, 'Attach the three VPCs first.', started)
    tables = _tgw_tables(tgw_id)
    for name in TGW_TABLES:
        if name not in tables:
            ec2.create_transit_gateway_route_table(
                TransitGatewayId=tgw_id, TagSpecifications=_tag_spec('transit-gateway-route-table', name))
    tables = _tgw_tables(tgw_id)
    model = _tgw_table_model(tgw_id)
    for name, spec in TGW_TABLES.items():
        table_id = tables[name]['TransitGatewayRouteTableId']
        for vpc in spec['associations']:
            if vpc in model[name]['associations']:
                continue
            attachment_id = attachments[vpc]['TransitGatewayAttachmentId']
            # An attachment has one association: move it here if it sits on the other table.
            for other_name, other in model.items():
                if other_name != name and vpc in other['associations']:
                    ec2.disassociate_transit_gateway_route_table(
                        TransitGatewayRouteTableId=tables[other_name]['TransitGatewayRouteTableId'],
                        TransitGatewayAttachmentId=attachment_id)
            ec2.associate_transit_gateway_route_table(TransitGatewayRouteTableId=table_id,
                                                      TransitGatewayAttachmentId=attachment_id)
        for cidr, target in spec['routes'].items():
            if model[name]['routes'].get(cidr, '?') == target:
                continue
            if cidr in model[name]['routes']:
                ec2.delete_transit_gateway_route(TransitGatewayRouteTableId=table_id, DestinationCidrBlock=cidr)
            if target is None:
                ec2.create_transit_gateway_route(TransitGatewayRouteTableId=table_id, DestinationCidrBlock=cidr,
                                                 Blackhole=True)
            else:
                ec2.create_transit_gateway_route(TransitGatewayRouteTableId=table_id, DestinationCidrBlock=cidr,
                                                 TransitGatewayAttachmentId=attachments[target][
                                                     'TransitGatewayAttachmentId'])
    model = _tgw_table_model(tgw_id)
    reach = tgw_reachability(model)
    response = {'RouteTables': model,
                'Reachability': {f'{src} -> {dst}': ok for (src, dst), ok in reach.items()}}
    verified, message = _check_segmentation()
    if verified and not any(table['propagated'] for table in model.values()):
        message += ' Floci never fills propagated routes, so the tables use static routes, and the step works out reachability itself.'
    return _result('tgw-segmentation', SEGMENT_COMMAND, response, verified, message, started)


def step_flow_logs() -> dict[str, Any]:
    started = time.perf_counter()
    ec2, s3, iam, logs = _client('ec2'), _client('s3'), _client('iam'), _client('logs')
    vpc = _vpcs().get(APP)
    if not vpc:
        return _result('flow-logs', FLOW_COMMAND, {}, False, f'Create {APP} first.', started)
    try:
        s3.create_bucket(Bucket=FLOW_BUCKET)
    except ClientError as exc:
        if _code(exc) not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
            raise
    s3.put_public_access_block(Bucket=FLOW_BUCKET, PublicAccessBlockConfiguration={
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    s3.put_bucket_policy(Bucket=FLOW_BUCKET, Policy=json.dumps(FLOW_BUCKET_POLICY))
    try:
        iam.create_role(RoleName=FLOW_ROLE, AssumeRolePolicyDocument=json.dumps(FLOW_TRUST),
                        Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    except ClientError as exc:
        if _code(exc) != 'EntityAlreadyExists':
            raise
    iam.put_role_policy(RoleName=FLOW_ROLE, PolicyName='deliver-flow-logs', PolicyDocument=json.dumps(FLOW_ROLE_POLICY))
    try:
        logs.create_log_group(logGroupName=FLOW_GROUP, tags={'Lab': LAB_KEY})
    except ClientError as exc:
        if _code(exc) != 'ResourceAlreadyExistsException':
            raise
    logs.put_retention_policy(logGroupName=FLOW_GROUP, retentionInDays=30)
    existing = _flow_logs(vpc['VpcId'])
    if not any(item.get('LogDestinationType') == 's3' for item in existing):
        ec2.create_flow_logs(ResourceIds=[vpc['VpcId']], ResourceType='VPC', TrafficType='ALL',
                             LogDestinationType='s3', LogDestination=f'arn:aws:s3:::{FLOW_BUCKET}/{FLOW_PREFIX}',
                             LogFormat=FLOW_FORMAT, MaxAggregationInterval=60,
                             TagSpecifications=_tag_spec('vpc-flow-log', f'{APP}-all-to-s3'))
    if not any(item.get('LogDestinationType') == 'cloud-watch-logs' for item in existing):
        ec2.create_flow_logs(ResourceIds=[vpc['VpcId']], ResourceType='VPC', TrafficType='REJECT',
                             LogDestinationType='cloud-watch-logs', LogGroupName=FLOW_GROUP,
                             DeliverLogsPermissionArn=FLOW_ROLE_ARN,
                             TagSpecifications=_tag_spec('vpc-flow-log', f'{APP}-rejects-to-logs'))
    objects = s3.list_objects_v2(Bucket=FLOW_BUCKET, Prefix=FLOW_PREFIX).get('KeyCount', 0)
    streams = logs.describe_log_streams(logGroupName=FLOW_GROUP).get('logStreams', [])
    analysis = analyze_flow_records(SAMPLE_RECORDS)
    response = {'FlowLogs': [{key: item.get(key) for key in ('FlowLogId', 'TrafficType', 'LogDestinationType',
                                                              'LogDestination', 'LogGroupName', 'FlowLogStatus')}
                             for item in _flow_logs(vpc['VpcId'])],
                'ObjectsDelivered': objects, 'LogStreams': len(streams), 'SampleAnalysis': analysis}
    verified, message = _check_flow_logs()
    if verified:
        message += (f' In the sample records, {analysis["Rejected"]} of {analysis["Records"]} flows were rejected, all '
                    f'inbound SSH or RDP from {len(analysis["RejectedSources"])} internet addresses.')
        if not objects and not streams:
            message += ' Floci writes no flow log records, so the step analyzed sample records itself.'
        if not any(item.get('LogGroupName') for item in response['FlowLogs']):
            message += ' Floci also reads the CloudWatch Logs flow log back without its log group name.'
    return _result('flow-logs', FLOW_COMMAND, response, verified, message, started)


def step_peering_vs_tgw() -> dict[str, Any]:
    started = time.perf_counter()
    rows = [network_costs(vpcs, gb) for vpcs, gb in COST_SCENARIOS]
    scenarios = [
        ('3 VPCs, low traffic, no transitive needs', choose_network(3)),
        ('10 VPCs that all talk to each other', choose_network(10)),
        ('4 VPCs sharing one Site-to-Site VPN back to the data center', choose_network(4, shared_vpn=True)),
        ('2 VPCs, lowest latency and cost for heavy traffic', choose_network(2)),
    ]
    response = {'Costs': rows, 'Scenarios': [{'Scenario': text, 'Choice': choice} for text, choice in scenarios],
                'Prices': {'TgwAttachmentHourUSD': TGW_ATTACHMENT_HOUR, 'TgwPerGBUSD': TGW_PER_GB,
                           'PeeringCrossAzPerGBUSD': PEERING_CROSS_AZ_PER_GB, 'PeeringHourlyUSD': 0}}
    small, large = rows[0], rows[1]
    message = (f'With 1 TB a month, 3 VPCs cost ${small["PeeringMonthlyUSD"]:.2f} peered versus '
               f'${small["TgwMonthlyUSD"]:.2f} through a Transit Gateway. At 10 VPCs peering is still '
               f'${large["PeeringMonthlyUSD"]:.2f} but needs {large["PeeringConnections"]} connections, while the '
               f'Transit Gateway costs ${large["TgwMonthlyUSD"]:.2f} with {large["TgwAttachments"]} attachments and '
               'transitive routing. Pick peering for a few VPCs, Transit Gateway for many, or for shared VPN and '
               'Direct Connect.')
    _marker('peering-vs-tgw', {'message': message})
    verified, check = _check_chooser()
    return _result('peering-vs-tgw', CHOOSER_COMMAND, response, verified, check, started)


RUNNERS = {
    'three-vpcs': step_three_vpcs,
    'vpc-peering': step_vpc_peering,
    'peering-limits': step_peering_limits,
    'transit-gateway': step_transit_gateway,
    'tgw-attachments': step_tgw_attachments,
    'tgw-segmentation': step_tgw_segmentation,
    'flow-logs': step_flow_logs,
    'peering-vs-tgw': step_peering_vs_tgw,
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
    except (ClientError, BotoCoreError, KeyError):
        return False


CHECKS = {
    'three-vpcs': _check_vpcs,
    'vpc-peering': _check_peering,
    'peering-limits': _check_limits,
    'transit-gateway': _check_tgw,
    'tgw-attachments': _check_attachments,
    'tgw-segmentation': _check_segmentation,
    'flow-logs': _check_flow_logs,
    'peering-vs-tgw': _check_chooser,
}


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
    """Delete the flow logs and their bucket, log group and role, the Transit Gateway with its route tables and
    attachments, the peering connections, and the VPCs."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or code.startswith('NoSuch') or code == 'NoSuchEntity':
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    ec2, s3, iam, logs = _client('ec2'), _client('s3'), _client('iam'), _client('logs')
    try:
        vpcs = _vpcs()
    except (ClientError, BotoCoreError) as exc:
        vpcs = {}
        errors.append(f'vpc lookup: {exc}')

    for vpc in vpcs.values():
        flow_ids = [item['FlowLogId'] for item in _flow_logs(vpc['VpcId'])]
        if flow_ids:
            attempt(f'flow-logs:{",".join(flow_ids)}', lambda flow_ids=flow_ids: ec2.delete_flow_logs(FlowLogIds=flow_ids))
    attempt(f'log-group:{FLOW_GROUP}', lambda: logs.delete_log_group(logGroupName=FLOW_GROUP))
    attempt(f'role-policy:{FLOW_ROLE}', lambda: iam.delete_role_policy(RoleName=FLOW_ROLE, PolicyName='deliver-flow-logs'))
    attempt(f'role:{FLOW_ROLE}', lambda: iam.delete_role(RoleName=FLOW_ROLE))
    try:
        keys = [item['Key'] for item in s3.list_objects_v2(Bucket=FLOW_BUCKET).get('Contents', [])]
        for key in keys:
            attempt(f'object:{key}', lambda key=key: s3.delete_object(Bucket=FLOW_BUCKET, Key=key))
        attempt(f'bucket:{FLOW_BUCKET}', lambda: s3.delete_bucket(Bucket=FLOW_BUCKET))
    except ClientError as exc:
        if _code(exc) != 'NoSuchBucket':
            errors.append(f'bucket:{FLOW_BUCKET}: {exc}')

    try:
        tgw = _tgw()
    except (ClientError, BotoCoreError) as exc:
        tgw = None
        errors.append(f'tgw lookup: {exc}')
    if tgw:
        tgw_id = tgw['TransitGatewayId']
        all_attachments = ec2.describe_transit_gateway_vpc_attachments(Filters=[
            {'Name': 'transit-gateway-id', 'Values': [tgw_id]}]).get('TransitGatewayVpcAttachments', [])
        for table in _tgw_tables(tgw_id).values():
            table_id = table['TransitGatewayRouteTableId']
            for item in ec2.get_transit_gateway_route_table_associations(
                    TransitGatewayRouteTableId=table_id).get('Associations', []):
                attempt(f'tgw-association:{item["TransitGatewayAttachmentId"]}',
                        lambda item=item, table_id=table_id: ec2.disassociate_transit_gateway_route_table(
                            TransitGatewayRouteTableId=table_id,
                            TransitGatewayAttachmentId=item['TransitGatewayAttachmentId']))
        for item in all_attachments:
            if item.get('State') in ('deleted', 'deleting'):
                continue
            attempt(f'tgw-attachment:{item["TransitGatewayAttachmentId"]}',
                    lambda item=item: ec2.delete_transit_gateway_vpc_attachment(
                        TransitGatewayAttachmentId=item['TransitGatewayAttachmentId']))
        for table in _tgw_tables(tgw_id).values():
            attempt(f'tgw-route-table:{table["TransitGatewayRouteTableId"]}',
                    lambda table=table: ec2.delete_transit_gateway_route_table(
                        TransitGatewayRouteTableId=table['TransitGatewayRouteTableId']))
        deadline = time.monotonic() + 60
        while _attachments(tgw_id) and time.monotonic() < deadline:
            time.sleep(2)
        attempt(f'tgw:{tgw_id}', lambda: ec2.delete_transit_gateway(TransitGatewayId=tgw_id))

    try:
        peerings = _peerings()
    except (ClientError, BotoCoreError) as exc:
        peerings = []
        errors.append(f'peering lookup: {exc}')
    for pcx in peerings:
        attempt(f'peering:{pcx["VpcPeeringConnectionId"]}',
                lambda pcx=pcx: ec2.delete_vpc_peering_connection(VpcPeeringConnectionId=pcx['VpcPeeringConnectionId']))

    for name, vpc in vpcs.items():
        vpc_id = vpc['VpcId']
        tables = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('RouteTables', [])
        for table in tables:
            if any(item.get('Main') for item in table.get('Associations', [])):
                continue
            for item in table.get('Associations', []):
                attempt(f'rt-association:{item["RouteTableAssociationId"]}',
                        lambda item=item: ec2.disassociate_route_table(AssociationId=item['RouteTableAssociationId']))
            attempt(f'route-table:{table["RouteTableId"]}',
                    lambda table=table: ec2.delete_route_table(RouteTableId=table['RouteTableId']))
        for subnet in ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', []):
            attempt(f'subnet:{subnet["SubnetId"]}', lambda subnet=subnet: ec2.delete_subnet(SubnetId=subnet['SubnetId']))
        attempt(f'vpc:{name}', lambda vpc_id=vpc_id: ec2.delete_vpc(VpcId=vpc_id))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws ec2 delete-flow-logs --flow-log-ids <fl-ids>\n'
            f'aws logs delete-log-group --log-group-name {FLOW_GROUP}\n'
            f'aws iam delete-role-policy --role-name {FLOW_ROLE} --policy-name deliver-flow-logs && '
            f'aws iam delete-role --role-name {FLOW_ROLE}\n'
            f'aws s3 rb s3://{FLOW_BUCKET} --force\n'
            'aws ec2 disassociate-transit-gateway-route-table ... && aws ec2 delete-transit-gateway-vpc-attachment ...\n'
            'aws ec2 delete-transit-gateway-route-table ... && aws ec2 delete-transit-gateway --transit-gateway-id <tgw-id>\n'
            'aws ec2 delete-vpc-peering-connection --vpc-peering-connection-id <pcx-id>\n'
            'aws ec2 delete-route-table / delete-subnet / delete-vpc   # each lab VPC'
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
            else 'The flow logs, Transit Gateway, peering connection and VPCs are deleted.',
        },
    }
