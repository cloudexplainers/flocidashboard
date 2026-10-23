"""Three-tier VPC lab: public, application, and data subnets in two Availability Zones, with one NAT
gateway per zone, an isolated data tier, and an S3 gateway endpoint that keeps S3 traffic off the NAT.

Floci 2.3.0 stores Elastic IPs, NAT gateways, route tables, DB subnet groups, and gateway endpoints and
reports them as AWS does, but it does not route instance traffic: every instance is a Docker container
that reaches the internet through Docker's bridge, whatever its subnet's route table says. Each step
checks the configuration, including rules AWS enforces and Floci does not, and says what Floci skips.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ec2'
LAB_KEY = 'nat-three-tier'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'

VPC_NAME = 'lab-three-tier-vpc'
VPC_CIDR = '10.64.0.0/16'
ZONES = ('us-east-1a', 'us-east-1b')
TIERS = ('public', 'app', 'data')
SUBNETS = (
    {'name': 'lab-three-tier-public-a', 'tier': 'public', 'cidr': '10.64.0.0/24', 'az': 'us-east-1a'},
    {'name': 'lab-three-tier-public-b', 'tier': 'public', 'cidr': '10.64.1.0/24', 'az': 'us-east-1b'},
    {'name': 'lab-three-tier-app-a', 'tier': 'app', 'cidr': '10.64.10.0/24', 'az': 'us-east-1a'},
    {'name': 'lab-three-tier-app-b', 'tier': 'app', 'cidr': '10.64.11.0/24', 'az': 'us-east-1b'},
    {'name': 'lab-three-tier-data-a', 'tier': 'data', 'cidr': '10.64.20.0/24', 'az': 'us-east-1a'},
    {'name': 'lab-three-tier-data-b', 'tier': 'data', 'cidr': '10.64.21.0/24', 'az': 'us-east-1b'},
)
IGW_NAME = 'lab-three-tier-igw'
PUBLIC_ROUTE_TABLE = 'lab-three-tier-public-rt'
APP_ROUTE_TABLES = {'us-east-1a': 'lab-three-tier-app-rt-a', 'us-east-1b': 'lab-three-tier-app-rt-b'}
DATA_ROUTE_TABLE = 'lab-three-tier-data-rt'
EIP_NAMES = {'us-east-1a': 'lab-three-tier-nat-eip-a', 'us-east-1b': 'lab-three-tier-nat-eip-b'}
NAT_NAMES = {'us-east-1a': 'lab-three-tier-nat-a', 'us-east-1b': 'lab-three-tier-nat-b'}
DB_SUBNET_GROUP = 'lab-three-tier-data'
ENDPOINT_NAME = 'lab-three-tier-s3'
LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}
DEFAULT_ROUTE = '0.0.0.0/0'

NAT_WAIT_SECONDS = 120


def _s3_service_name() -> str:
    return f'com.amazonaws.{FlociClientFactory().region}.s3'


def _subnet_spec(tier: str, az: str) -> dict[str, str]:
    return next(spec for spec in SUBNETS if spec['tier'] == tier and spec['az'] == az)


CREATE_VPC_COMMAND = (
    f'aws ec2 create-vpc --cidr-block {VPC_CIDR} '
    f"--tag-specifications 'ResourceType=vpc,Tags=[{{Key=Name,Value={VPC_NAME}}},{{Key=Lab,Value={LAB_KEY}}}]'\n"
    'aws ec2 modify-vpc-attribute --vpc-id <vpc-id> --enable-dns-support \'{"Value":true}\'\n'
    'aws ec2 modify-vpc-attribute --vpc-id <vpc-id> --enable-dns-hostnames \'{"Value":true}\''
)
CREATE_SUBNETS_COMMAND = '\n'.join(
    f"aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {spec['cidr']} --availability-zone {spec['az']} "
    f"--tag-specifications 'ResourceType=subnet,Tags=[{{Key=Name,Value={spec['name']}}},{{Key=Tier,Value={spec['tier']}}}]'"
    for spec in SUBNETS
)
ATTACH_IGW_COMMAND = (
    f"aws ec2 create-internet-gateway --tag-specifications 'ResourceType=internet-gateway,Tags=[{{Key=Name,Value={IGW_NAME}}}]'\n"
    'aws ec2 attach-internet-gateway --internet-gateway-id <igw-id> --vpc-id <vpc-id>'
)
ROUTE_PUBLIC_COMMAND = (
    f"aws ec2 create-route-table --vpc-id <vpc-id> --tag-specifications 'ResourceType=route-table,Tags=[{{Key=Name,Value={PUBLIC_ROUTE_TABLE}}}]'\n"
    f'aws ec2 create-route --route-table-id <public-rt-id> --destination-cidr-block {DEFAULT_ROUTE} --gateway-id <igw-id>\n'
    'aws ec2 associate-route-table --route-table-id <public-rt-id> --subnet-id <public-a-subnet-id>\n'
    'aws ec2 associate-route-table --route-table-id <public-rt-id> --subnet-id <public-b-subnet-id>\n'
    'aws ec2 modify-subnet-attribute --subnet-id <public-a-subnet-id> --map-public-ip-on-launch\n'
    'aws ec2 modify-subnet-attribute --subnet-id <public-b-subnet-id> --map-public-ip-on-launch'
)
ALLOCATE_EIPS_COMMAND = '\n'.join(
    f"aws ec2 allocate-address --domain vpc --tag-specifications 'ResourceType=elastic-ip,Tags=[{{Key=Name,Value={name}}}]'"
    for name in EIP_NAMES.values()
)
CREATE_NATS_COMMAND = (
    '\n'.join(
        f'aws ec2 create-nat-gateway --subnet-id <public-{az[-1]}-subnet-id> --allocation-id <eip-{az[-1]}-allocation-id> '
        f"--tag-specifications 'ResourceType=natgateway,Tags=[{{Key=Name,Value={name}}}]'"
        for az, name in NAT_NAMES.items()
    )
    + '\naws ec2 wait nat-gateway-available --nat-gateway-ids <nat-a-id> <nat-b-id>'
)
ROUTE_APP_COMMAND = '\n'.join(
    f"aws ec2 create-route-table --vpc-id <vpc-id> --tag-specifications 'ResourceType=route-table,Tags=[{{Key=Name,Value={name}}}]'\n"
    f'aws ec2 create-route --route-table-id <app-rt-{az[-1]}-id> --destination-cidr-block {DEFAULT_ROUTE} --nat-gateway-id <nat-{az[-1]}-id>\n'
    f'aws ec2 associate-route-table --route-table-id <app-rt-{az[-1]}-id> --subnet-id <app-{az[-1]}-subnet-id>'
    for az, name in APP_ROUTE_TABLES.items()
)
ISOLATE_DATA_COMMAND = (
    f"aws ec2 create-route-table --vpc-id <vpc-id> --tag-specifications 'ResourceType=route-table,Tags=[{{Key=Name,Value={DATA_ROUTE_TABLE}}}]'\n"
    'aws ec2 associate-route-table --route-table-id <data-rt-id> --subnet-id <data-a-subnet-id>\n'
    'aws ec2 associate-route-table --route-table-id <data-rt-id> --subnet-id <data-b-subnet-id>\n'
    f'aws rds create-db-subnet-group --db-subnet-group-name {DB_SUBNET_GROUP} '
    '--db-subnet-group-description "Data tier, no internet route" --subnet-ids <data-a-subnet-id> <data-b-subnet-id>'
)
ENDPOINT_COMMAND = (
    'aws ec2 create-vpc-endpoint --vpc-endpoint-type Gateway --vpc-id <vpc-id> '
    '--service-name com.amazonaws.us-east-1.s3 --route-table-ids <app-rt-a-id> <app-rt-b-id> '
    f"--tag-specifications 'ResourceType=vpc-endpoint,Tags=[{{Key=Name,Value={ENDPOINT_NAME}}}]'"
)
TRACE_COMMAND = (
    'aws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc-id> '
    "--query 'RouteTables[].{Table:Tags[?Key==`Name`]|[0].Value,Subnets:Associations[].SubnetId,Routes:Routes}'\n"
    'aws ec2 describe-nat-gateways --filter Name=vpc-id,Values=<vpc-id> '
    "--query 'NatGateways[].{Id:NatGatewayId,Subnet:SubnetId,State:State,PublicIp:NatGatewayAddresses[0].PublicIp}'"
)

FLOCI_ROUTING_NOTE = (
    'How Floci emulates this: Floci stores the routes and reports them as AWS does, but it does not route '
    "instance traffic. Every instance is a Docker container that reaches the internet through Docker's bridge, "
    'whatever its subnet routes to, so the lab checks the configuration.'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Build a three-tier VPC with a NAT gateway in each Availability Zone',
    'description': (
        'Lay out public, application, and data subnets across two Availability Zones. The public tier routes to an '
        'internet gateway, each application subnet reaches out through the NAT gateway in its own zone, the data tier '
        'has no internet route at all, and an S3 gateway endpoint keeps S3 traffic off the NAT gateways.'
    ),
    'steps': [
        {
            'key': 'create-vpc',
            'title': 'Create the VPC with DNS support',
            'command': CREATE_VPC_COMMAND,
            'explanation': (
                f'The VPC gets a /16 ({VPC_CIDR}, 65,536 addresses) to divide between three tiers in two Availability '
                'Zones. DNS support and DNS hostnames must both be on for private hosted zones, interface endpoints, and '
                'RDS endpoint names to resolve inside the VPC.'
            ),
        },
        {
            'key': 'create-tier-subnets',
            'title': 'Create public, application, and data subnets in two zones',
            'command': CREATE_SUBNETS_COMMAND,
            'explanation': (
                'A subnet lives in exactly one Availability Zone, so every tier gets one subnet per zone, and losing a '
                'zone takes out only half of each tier. What makes a subnet public or private is only '
                'its route table, which the next steps set. AWS reserves five addresses in every subnet (the first four '
                'and the last), so each /24 has 251 usable addresses.'
            ),
        },
        {
            'key': 'attach-internet-gateway',
            'title': 'Create and attach an internet gateway',
            'command': ATTACH_IGW_COMMAND,
            'explanation': (
                'An internet gateway is a horizontally scaled, highly available VPC component with no bandwidth limit '
                'and no charge. It does one-to-one NAT for instances that have a public IPv4 address. A VPC can have '
                'only one, and attaching it does not make any subnet public yet.'
            ),
        },
        {
            'key': 'route-public-subnets',
            'title': 'Route the public subnets to the internet gateway',
            'command': ROUTE_PUBLIC_COMMAND,
            'explanation': (
                'A subnet is public when its route table sends 0.0.0.0/0 to an internet gateway. Load balancers, '
                'bastion hosts, and the NAT gateways go here. Auto-assigning public IPv4 addresses lets instances '
                'launched here be reached; since February 2024 AWS charges for every public IPv4 address, so keep '
                'application servers out of this tier. ' + FLOCI_ROUTING_NOTE
            ),
        },
        {
            'key': 'allocate-elastic-ips',
            'title': 'Allocate an Elastic IP for each NAT gateway',
            'command': ALLOCATE_EIPS_COMMAND,
            'explanation': (
                'A public NAT gateway needs an Elastic IP: a static public IPv4 address that stays with your account '
                'until you release it. Partners that allow-list your outbound traffic see this address, and it does '
                'not change when the NAT gateway is replaced if you reuse the allocation. '
                'How Floci emulates this: Floci hands out addresses from a made-up pool, and DescribeAddresses does '
                'not show which NAT gateway uses an address.'
            ),
        },
        {
            'key': 'create-nat-gateways',
            'title': 'Create a NAT gateway in each public subnet',
            'command': CREATE_NATS_COMMAND,
            'explanation': (
                'A NAT gateway lets instances in private subnets start outbound IPv4 connections (patches, external '
                'APIs) while nothing on the internet can connect in. It must sit in a public subnet. It is a zonal, '
                'managed service: AWS makes it redundant inside its zone and scales it to 100 Gbps, but if the zone '
                'fails, subnets that route to it lose egress. One NAT gateway per zone is the highly available design; '
                'one shared gateway is cheaper but is a single point of failure and adds cross-zone data charges. You '
                'pay per hour and per GB processed. A NAT instance (an EC2 instance with source/destination check '
                'disabled) is the legacy, self-managed alternative; unlike a NAT gateway it can use security groups '
                'and act as a bastion. NAT gateways are IPv4 only; IPv6 egress uses an egress-only internet gateway. '
                'How Floci emulates this: Floci reports each NAT gateway available at once and translates no '
                'traffic. It also accepts an Elastic IP that another NAT gateway already uses, and a public NAT '
                'gateway with no Elastic IP, which AWS refuses, so the step checks both rules itself.'
            ),
        },
        {
            'key': 'route-app-subnets',
            'title': 'Route each application subnet through the NAT gateway in its zone',
            'command': ROUTE_APP_COMMAND,
            'explanation': (
                'Each application subnet gets its own route table whose default route targets the NAT gateway in the '
                'same Availability Zone. If zone A fails, subnets in zone B still reach the internet, and no traffic '
                'crosses zones to reach a NAT gateway. The local route (the VPC CIDR) is always present and cannot be '
                'removed, so the tiers can still talk to each other; security groups and network ACLs control that. '
                + FLOCI_ROUTING_NOTE + ' Floci also keeps a route to a deleted NAT gateway "active"; AWS marks it '
                'blackhole.'
            ),
        },
        {
            'key': 'isolate-data-subnets',
            'title': 'Keep the data tier off the internet and group it for RDS',
            'command': ISOLATE_DATA_COMMAND,
            'explanation': (
                'The data subnets share a route table with only the local route: databases neither accept nor start '
                'internet connections. A DB subnet group tells RDS which subnets it may place instances in; AWS '
                'requires subnets in at least two Availability Zones so Multi-AZ can put the standby in another zone. '
                'How Floci emulates this: Floci accepts a DB subnet group in a single zone, so the step checks the '
                'zones itself.'
            ),
        },
        {
            'key': 'add-s3-gateway-endpoint',
            'title': 'Send S3 traffic through a gateway endpoint instead of the NAT gateways',
            'command': ENDPOINT_COMMAND,
            'explanation': (
                'Without an endpoint, every byte the application tier sends to or reads from S3 goes through a NAT '
                'gateway and is billed per GB processed. A gateway endpoint for S3 (or DynamoDB) is free: AWS adds a '
                'route for the service prefix list to each chosen route table, and that more specific route wins over '
                '0.0.0.0/0. Interface endpoints (PrivateLink) cover other services, cost per hour and per GB, and work '
                'from on-premises networks too. '
                'How Floci emulates this: Floci records the endpoint and its route tables, but does not add the '
                'prefix-list route to the tables.'
            ),
        },
        {
            'key': 'trace-egress-paths',
            'title': 'Trace where each subnet sends internet traffic',
            'command': TRACE_COMMAND,
            'explanation': (
                'Reads every route table and NAT gateway and follows the default route of each subnet: public subnets '
                'to the internet gateway, each application subnet to the NAT gateway in its own zone (which sits in a '
                'public subnet), and the data subnets nowhere. On AWS, VPC Reachability Analyzer proves a path like '
                'this end to end; Floci does not support it (CreateNetworkInsightsPath is unsupported), and it does '
                'not route traffic, so the trace reads the configuration.'
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


def _marker(step: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _result(step: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    if verified:
        _marker(step)
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


def _tags(resource_type: str, name: str) -> list[dict[str, Any]]:
    return [{'ResourceType': resource_type, 'Tags': [{'Key': 'Name', 'Value': name}, LAB_TAG]}]


def _name(item: dict[str, Any]) -> str | None:
    return next((tag.get('Value') for tag in item.get('Tags') or [] if tag.get('Key') == 'Name'), None)


def _by_name(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in items if _name(item) == name), None)


# ── Lookups by the lab's fixed names ─────────────────────────────────────────

def _vpc() -> dict[str, Any] | None:
    vpcs = _client('ec2').describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_NAME]}]).get('Vpcs', [])
    return _by_name(vpcs, VPC_NAME)


def _subnets(vpc_id: str) -> dict[str, dict[str, Any]]:
    """Lab subnets by name."""
    items = _client('ec2').describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', [])
    found = {}
    for spec in SUBNETS:
        subnet = _by_name(items, spec['name'])
        if subnet:
            found[spec['name']] = subnet
    return found


def _internet_gateway(vpc_id: str) -> dict[str, Any] | None:
    gateways = _client('ec2').describe_internet_gateways(
        Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}],
    ).get('InternetGateways', [])
    return gateways[0] if gateways else None


def _route_tables(vpc_id: str) -> list[dict[str, Any]]:
    return _client('ec2').describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('RouteTables', [])


def _addresses() -> dict[str, dict[str, Any]]:
    """Lab Elastic IPs by zone."""
    items = _client('ec2').describe_addresses(
        Filters=[{'Name': 'tag:Name', 'Values': list(EIP_NAMES.values())}],
    ).get('Addresses', [])
    return {az: address for az, name in EIP_NAMES.items() if (address := _by_name(items, name))}


def _nat_gateways(vpc_id: str) -> dict[str, dict[str, Any]]:
    """Lab NAT gateways by zone, ignoring deleted ones."""
    items = _client('ec2').describe_nat_gateways(
        Filter=[{'Name': 'vpc-id', 'Values': [vpc_id]}],
    ).get('NatGateways', [])
    live = [item for item in items if item.get('State') not in ('deleting', 'deleted', 'failed')]
    return {az: nat for az, name in NAT_NAMES.items() if (nat := _by_name(live, name))}


def _db_subnet_group() -> dict[str, Any] | None:
    try:
        groups = _client('rds').describe_db_subnet_groups(DBSubnetGroupName=DB_SUBNET_GROUP).get('DBSubnetGroups', [])
    except ClientError as exc:
        if _code(exc) in {'DBSubnetGroupNotFoundFault', 'DBSubnetGroupNotFound'}:
            return None
        raise
    return groups[0] if groups else None


def _endpoint(vpc_id: str) -> dict[str, Any] | None:
    items = _client('ec2').describe_vpc_endpoints(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}, {'Name': 'service-name', 'Values': [_s3_service_name()]}],
    ).get('VpcEndpoints', [])
    live = [item for item in items if (item.get('State') or '').lower() not in ('deleted', 'deleting', 'failed')]
    return _by_name(live, ENDPOINT_NAME) or (live[0] if live else None)


def _default_route(table: dict[str, Any] | None) -> dict[str, Any] | None:
    return next((route for route in (table or {}).get('Routes') or [] if route.get('DestinationCidrBlock') == DEFAULT_ROUTE), None)


def _table_for_subnet(tables: list[dict[str, Any]], subnet_id: str) -> dict[str, Any] | None:
    """The route table a subnet uses: its explicit association, or the VPC's main table."""
    for table in tables:
        if any(item.get('SubnetId') == subnet_id for item in table.get('Associations') or []):
            return table
    return next((table for table in tables if any(item.get('Main') for item in table.get('Associations') or [])), None)


def _is_public(tables: list[dict[str, Any]], subnet_id: str) -> bool:
    route = _default_route(_table_for_subnet(tables, subnet_id))
    return bool(route and (route.get('GatewayId') or '').startswith('igw-'))


# ── Checks ───────────────────────────────────────────────────────────────────

def _need_vpc() -> tuple[dict[str, Any] | None, str]:
    vpc = _vpc()
    return vpc, '' if vpc else f'VPC {VPC_NAME} not found. Run "Create the VPC with DNS support" first.'


def _check_vpc() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    ec2 = _client('ec2')
    vpc_id = vpc['VpcId']
    support = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsSupport').get('EnableDnsSupport', {}).get('Value')
    hostnames = ec2.describe_vpc_attribute(VpcId=vpc_id, Attribute='enableDnsHostnames').get('EnableDnsHostnames', {}).get('Value')
    if vpc.get('CidrBlock') != VPC_CIDR:
        return False, f"{VPC_NAME} has CIDR {vpc.get('CidrBlock')}, expected {VPC_CIDR}."
    if not (support and hostnames):
        return False, f'{VPC_NAME} needs both DNS support and DNS hostnames turned on.'
    return True, f'{VPC_NAME} ({vpc_id}, {VPC_CIDR}) exists with DNS support and DNS hostnames on.'


def _check_subnets() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    subnets = _subnets(vpc['VpcId'])
    for spec in SUBNETS:
        subnet = subnets.get(spec['name'])
        if not subnet:
            return False, f"Subnet {spec['name']} ({spec['cidr']} in {spec['az']}) not found."
        if subnet.get('CidrBlock') != spec['cidr'] or subnet.get('AvailabilityZone') != spec['az']:
            return False, (f"{spec['name']} is {subnet.get('CidrBlock')} in {subnet.get('AvailabilityZone')}; "
                           f"expected {spec['cidr']} in {spec['az']}.")
    return True, f'Six subnets exist: public, app, and data tiers, each in {ZONES[0]} and {ZONES[1]}.'


def _check_igw() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    gateway = _internet_gateway(vpc['VpcId'])
    if not gateway:
        return False, f'No internet gateway is attached to {VPC_NAME}.'
    return True, f"Internet gateway {gateway['InternetGatewayId']} is attached to {VPC_NAME}."


def _check_public_routes() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    vpc_id = vpc['VpcId']
    gateway = _internet_gateway(vpc_id)
    if not gateway:
        return False, 'Attach the internet gateway first.'
    tables = _route_tables(vpc_id)
    table = _by_name(tables, PUBLIC_ROUTE_TABLE)
    if not table:
        return False, f'Route table {PUBLIC_ROUTE_TABLE} not found.'
    route = _default_route(table)
    if not route or route.get('GatewayId') != gateway['InternetGatewayId']:
        return False, f"{PUBLIC_ROUTE_TABLE} has no 0.0.0.0/0 route to {gateway['InternetGatewayId']}."
    subnets = _subnets(vpc_id)
    for az in ZONES:
        spec = _subnet_spec('public', az)
        subnet = subnets.get(spec['name'])
        if not subnet:
            return False, f"Subnet {spec['name']} not found."
        if (_table_for_subnet(tables, subnet['SubnetId']) or {}).get('RouteTableId') != table['RouteTableId']:
            return False, f"{spec['name']} is not associated with {PUBLIC_ROUTE_TABLE}."
        if not subnet.get('MapPublicIpOnLaunch'):
            return False, f"{spec['name']} does not auto-assign public IPv4 addresses."
    return True, (f"Both public subnets use {PUBLIC_ROUTE_TABLE}, which sends 0.0.0.0/0 to "
                  f"{gateway['InternetGatewayId']}, and auto-assign public IPv4 addresses.")


def _check_addresses() -> tuple[bool, str]:
    addresses = _addresses()
    missing = [EIP_NAMES[az] for az in ZONES if az not in addresses]
    if missing:
        return False, f"Elastic IP {', '.join(missing)} not found."
    if any(address.get('Domain') != 'vpc' for address in addresses.values()):
        return False, 'Allocate the Elastic IPs with --domain vpc.'
    ips = ', '.join(f"{addresses[az].get('PublicIp')} ({EIP_NAMES[az]})" for az in ZONES)
    return True, f'Two Elastic IPs are allocated: {ips}.'


def _check_nats() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    vpc_id = vpc['VpcId']
    nats = _nat_gateways(vpc_id)
    subnets = _subnets(vpc_id)
    tables = _route_tables(vpc_id)
    addresses = _addresses()
    used: dict[str, str] = {}
    lines = []
    for az in ZONES:
        name = NAT_NAMES[az]
        nat = nats.get(az)
        if not nat:
            return False, f'NAT gateway {name} not found.'
        if nat.get('State') != 'available':
            return False, f"{name} is {nat.get('State')}, not available yet."
        if nat.get('ConnectivityType', 'public') != 'public':
            return False, f'{name} is a private NAT gateway; this design needs public ones.'
        public_spec = _subnet_spec('public', az)
        subnet = subnets.get(public_spec['name'])
        if not subnet or nat.get('SubnetId') != subnet['SubnetId']:
            return False, f"{name} must be in {public_spec['name']} (the public subnet in {az})."
        if not _is_public(tables, nat['SubnetId']):
            return False, f"{name}'s subnet has no route to the internet gateway, so it could not reach the internet."
        allocation = next((item.get('AllocationId') for item in nat.get('NatGatewayAddresses') or [] if item.get('AllocationId')), None)
        if not allocation:
            return False, f'{name} has no Elastic IP. AWS requires one for a public NAT gateway; Floci accepted it without one.'
        if allocation in used:
            return False, (f'{name} and {used[allocation]} share Elastic IP {allocation}. AWS refuses that; give each '
                           'NAT gateway its own address.')
        if (addresses.get(az) or {}).get('AllocationId') != allocation:
            return False, f'{name} should use {EIP_NAMES[az]}.'
        used[allocation] = name
        public_ip = next((item.get('PublicIp') for item in nat.get('NatGatewayAddresses') or [] if item.get('PublicIp')), '?')
        lines.append(f"{name} ({nat['NatGatewayId']}) in {az}, public IP {public_ip}")
    return True, 'Both NAT gateways are available, each in its zone\'s public subnet with its own Elastic IP: ' + '; '.join(lines) + '.'


def _check_app_routes() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    vpc_id = vpc['VpcId']
    nats = _nat_gateways(vpc_id)
    subnets = _subnets(vpc_id)
    tables = _route_tables(vpc_id)
    lines = []
    for az in ZONES:
        spec = _subnet_spec('app', az)
        subnet = subnets.get(spec['name'])
        if not subnet:
            return False, f"Subnet {spec['name']} not found."
        nat = nats.get(az)
        if not nat:
            return False, f'NAT gateway {NAT_NAMES[az]} not found. Create the NAT gateways first.'
        table = _table_for_subnet(tables, subnet['SubnetId'])
        if _name(table or {}) != APP_ROUTE_TABLES[az]:
            return False, f"{spec['name']} is not associated with {APP_ROUTE_TABLES[az]}."
        route = _default_route(table)
        target = (route or {}).get('NatGatewayId')
        if not target:
            return False, f'{APP_ROUTE_TABLES[az]} has no 0.0.0.0/0 route to a NAT gateway.'
        if target != nat['NatGatewayId']:
            other = next((NAT_NAMES[zone] for zone, item in nats.items() if item['NatGatewayId'] == target), target)
            return False, (f"{spec['name']} in {az} routes to {other}, not to {NAT_NAMES[az]} in its own zone. If the "
                           "other zone failed, this subnet would lose egress, and cross-zone traffic is billed.")
        lines.append(f"{spec['name']} → {NAT_NAMES[az]}")
    return True, 'Each application subnet routes 0.0.0.0/0 to the NAT gateway in its own zone: ' + ', '.join(lines) + '.'


def _check_data_tier() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    vpc_id = vpc['VpcId']
    subnets = _subnets(vpc_id)
    tables = _route_tables(vpc_id)
    data_ids = []
    for az in ZONES:
        spec = _subnet_spec('data', az)
        subnet = subnets.get(spec['name'])
        if not subnet:
            return False, f"Subnet {spec['name']} not found."
        table = _table_for_subnet(tables, subnet['SubnetId'])
        if _name(table or {}) != DATA_ROUTE_TABLE:
            return False, f"{spec['name']} is not associated with {DATA_ROUTE_TABLE}."
        extra = [route for route in table.get('Routes') or [] if route.get('GatewayId') != 'local']
        if extra:
            target = next((value for key, value in extra[0].items() if key.endswith('Id') and value), '?')
            return False, f"{DATA_ROUTE_TABLE} has a route beyond the local one ({extra[0].get('DestinationCidrBlock') or extra[0].get('DestinationPrefixListId')} → {target}). The data tier must not reach the internet."
        data_ids.append(subnet['SubnetId'])
    group = _db_subnet_group()
    if not group:
        return False, f'DB subnet group {DB_SUBNET_GROUP} not found.'
    members = {item.get('SubnetIdentifier'): (item.get('SubnetAvailabilityZone') or {}).get('Name') for item in group.get('Subnets') or []}
    if set(members) != set(data_ids):
        return False, f'{DB_SUBNET_GROUP} should contain exactly the two data subnets {data_ids}; it has {sorted(members)}.'
    zones = sorted({zone for zone in members.values() if zone})
    if len(zones) < 2:
        return False, f'{DB_SUBNET_GROUP} covers only {zones}. AWS requires two Availability Zones; Floci accepted one.'
    return True, (f'Both data subnets use {DATA_ROUTE_TABLE}, which has only the local route, and DB subnet group '
                  f"{DB_SUBNET_GROUP} spans {', '.join(zones)}.")


def _check_endpoint() -> tuple[bool, str]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error
    vpc_id = vpc['VpcId']
    endpoint = _endpoint(vpc_id)
    if not endpoint:
        return False, f'No S3 gateway endpoint in {VPC_NAME}.'
    if (endpoint.get('VpcEndpointType') or '').lower() != 'gateway':
        return False, f"{endpoint['VpcEndpointId']} is an {endpoint.get('VpcEndpointType')} endpoint; use a Gateway endpoint (free)."
    tables = _route_tables(vpc_id)
    wanted = {name: (_by_name(tables, name) or {}).get('RouteTableId') for name in APP_ROUTE_TABLES.values()}
    attached = set(endpoint.get('RouteTableIds') or [])
    missing = [name for name, table_id in wanted.items() if not table_id or table_id not in attached]
    if missing:
        return False, f"{endpoint['VpcEndpointId']} is not attached to {', '.join(missing)}."
    return True, f"S3 gateway endpoint {endpoint['VpcEndpointId']} is attached to both application route tables."


def _trace() -> tuple[bool, str, list[dict[str, Any]]]:
    vpc, error = _need_vpc()
    if not vpc:
        return False, error, []
    vpc_id = vpc['VpcId']
    subnets = _subnets(vpc_id)
    tables = _route_tables(vpc_id)
    nats = {nat['NatGatewayId']: (az, nat) for az, nat in _nat_gateways(vpc_id).items()}
    by_id = {subnet['SubnetId']: spec for spec in SUBNETS if (subnet := subnets.get(spec['name']))}
    endpoint = _endpoint(vpc_id)
    endpoint_tables = set((endpoint or {}).get('RouteTableIds') or [])
    paths, problems = [], []
    for spec in SUBNETS:
        subnet = subnets.get(spec['name'])
        if not subnet:
            problems.append(f"{spec['name']} is missing")
            continue
        table = _table_for_subnet(tables, subnet['SubnetId'])
        route = _default_route(table) or {}
        target = route.get('GatewayId') or route.get('NatGatewayId') or None
        path = {
            'Subnet': spec['name'], 'Tier': spec['tier'], 'AvailabilityZone': spec['az'],
            'RouteTable': _name(table or {}) or (table or {}).get('RouteTableId'), 'DefaultRoute': target or 'none (local only)',
            'S3': 'gateway endpoint' if (table or {}).get('RouteTableId') in endpoint_tables else 'default route',
        }
        if spec['tier'] == 'public' and not (target or '').startswith('igw-'):
            problems.append(f"{spec['name']} does not route to the internet gateway")
        if spec['tier'] == 'app':
            nat_az, nat = nats.get(target, (None, None))
            if not nat:
                problems.append(f"{spec['name']} does not route to a lab NAT gateway")
            else:
                path['Via'] = f"{_name(nat)} in {nat_az}, then the internet gateway"
                if nat_az != spec['az']:
                    problems.append(f"{spec['name']} crosses zones to {_name(nat)}")
                if not _is_public(tables, nat.get('SubnetId', '')) or by_id.get(nat.get('SubnetId'), {}).get('tier') != 'public':
                    problems.append(f'{_name(nat)} is not in a public subnet')
            if path['S3'] != 'gateway endpoint':
                problems.append(f"{spec['name']} sends S3 traffic through the NAT gateway")
        if spec['tier'] == 'data' and target:
            problems.append(f"{spec['name']} has a default route to {target}")
        paths.append(path)
    if problems:
        return False, 'The egress paths are not as designed: ' + '; '.join(problems) + '.', paths
    return True, (
        'Public subnets → internet gateway; app-a → NAT gateway A and app-b → NAT gateway B, each in its own zone, with '
        'S3 through the gateway endpoint; data subnets → no internet route. Floci does not route traffic, so this is the '
        'configured path, not a measured one.'
    ), paths


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_vpc() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    vpc = _vpc() or ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=_tags('vpc', VPC_NAME))['Vpc']
    vpc_id = vpc['VpcId']
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsSupport={'Value': True})
    ec2.modify_vpc_attribute(VpcId=vpc_id, EnableDnsHostnames={'Value': True})
    verified, message = _check_vpc()
    return _result('create-vpc', CREATE_VPC_COMMAND, {'Vpc': vpc}, verified, message, started)


def step_create_subnets() -> dict[str, Any]:
    started = time.perf_counter()
    vpc, error = _need_vpc()
    if not vpc:
        return _result('create-tier-subnets', CREATE_SUBNETS_COMMAND, {}, False, error, started)
    ec2 = _client('ec2')
    existing = _subnets(vpc['VpcId'])
    created = []
    for spec in SUBNETS:
        subnet = existing.get(spec['name'])
        if not subnet:
            tags = _tags('subnet', spec['name'])
            tags[0]['Tags'].append({'Key': 'Tier', 'Value': spec['tier']})
            subnet = ec2.create_subnet(VpcId=vpc['VpcId'], CidrBlock=spec['cidr'], AvailabilityZone=spec['az'],
                                       TagSpecifications=tags)['Subnet']
        created.append({'Name': spec['name'], 'SubnetId': subnet['SubnetId'], 'CidrBlock': subnet.get('CidrBlock'),
                        'AvailabilityZone': subnet.get('AvailabilityZone')})
    verified, message = _check_subnets()
    return _result('create-tier-subnets', CREATE_SUBNETS_COMMAND, {'Subnets': created}, verified, message, started)


def step_attach_igw() -> dict[str, Any]:
    started = time.perf_counter()
    vpc, error = _need_vpc()
    if not vpc:
        return _result('attach-internet-gateway', ATTACH_IGW_COMMAND, {}, False, error, started)
    ec2 = _client('ec2')
    gateway = _internet_gateway(vpc['VpcId'])
    if not gateway:
        gateway = ec2.create_internet_gateway(TagSpecifications=_tags('internet-gateway', IGW_NAME))['InternetGateway']
        ec2.attach_internet_gateway(InternetGatewayId=gateway['InternetGatewayId'], VpcId=vpc['VpcId'])
    verified, message = _check_igw()
    response = {'InternetGatewayId': gateway['InternetGatewayId'], 'VpcId': vpc['VpcId']}
    return _result('attach-internet-gateway', ATTACH_IGW_COMMAND, response, verified, message, started)


def _ensure_table(vpc_id: str, name: str, tables: list[dict[str, Any]]) -> dict[str, Any]:
    return _by_name(tables, name) or _client('ec2').create_route_table(
        VpcId=vpc_id, TagSpecifications=_tags('route-table', name),
    )['RouteTable']


def _associate(table: dict[str, Any], subnet_id: str, tables: list[dict[str, Any]]) -> None:
    """Point a subnet at ``table``, replacing any other explicit association it has."""
    ec2 = _client('ec2')
    for other in tables:
        for association in other.get('Associations') or []:
            if association.get('SubnetId') != subnet_id:
                continue
            if other['RouteTableId'] == table['RouteTableId']:
                return
            ec2.replace_route_table_association(AssociationId=association['RouteTableAssociationId'],
                                                RouteTableId=table['RouteTableId'])
            return
    ec2.associate_route_table(RouteTableId=table['RouteTableId'], SubnetId=subnet_id)


def _set_default_route(table: dict[str, Any], **target: str) -> None:
    ec2 = _client('ec2')
    route = _default_route(table)
    if route and all(route.get(key) == value for key, value in target.items()):
        return
    if route:
        ec2.replace_route(RouteTableId=table['RouteTableId'], DestinationCidrBlock=DEFAULT_ROUTE, **target)
    else:
        ec2.create_route(RouteTableId=table['RouteTableId'], DestinationCidrBlock=DEFAULT_ROUTE, **target)


def step_route_public() -> dict[str, Any]:
    started = time.perf_counter()
    vpc, error = _need_vpc()
    if not vpc:
        return _result('route-public-subnets', ROUTE_PUBLIC_COMMAND, {}, False, error, started)
    vpc_id = vpc['VpcId']
    gateway = _internet_gateway(vpc_id)
    subnets = _subnets(vpc_id)
    missing = [_subnet_spec('public', az)['name'] for az in ZONES if _subnet_spec('public', az)['name'] not in subnets]
    if not gateway or missing:
        message = 'Attach the internet gateway first.' if not gateway else f"Create subnet {', '.join(missing)} first."
        return _result('route-public-subnets', ROUTE_PUBLIC_COMMAND, {}, False, message, started)
    ec2 = _client('ec2')
    tables = _route_tables(vpc_id)
    table = _ensure_table(vpc_id, PUBLIC_ROUTE_TABLE, tables)
    _set_default_route(table, GatewayId=gateway['InternetGatewayId'])
    for az in ZONES:
        subnet = subnets[_subnet_spec('public', az)['name']]
        _associate(table, subnet['SubnetId'], tables)
        ec2.modify_subnet_attribute(SubnetId=subnet['SubnetId'], MapPublicIpOnLaunch={'Value': True})
    verified, message = _check_public_routes()
    response = {'RouteTable': _by_name(_route_tables(vpc_id), PUBLIC_ROUTE_TABLE)}
    return _result('route-public-subnets', ROUTE_PUBLIC_COMMAND, response, verified, message, started)


def step_allocate_eips() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    existing = _addresses()
    for az, name in EIP_NAMES.items():
        if az not in existing:
            ec2.allocate_address(Domain='vpc', TagSpecifications=_tags('elastic-ip', name))
    verified, message = _check_addresses()
    response = {'Addresses': list(_addresses().values())}
    return _result('allocate-elastic-ips', ALLOCATE_EIPS_COMMAND, response, verified, message, started)


def _wait(check, timeout_seconds: float, interval: float = 3.0) -> tuple[bool, str]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        ok, message = check()
        if ok or time.monotonic() >= deadline:
            return ok, message
        time.sleep(interval)


def step_create_nats() -> dict[str, Any]:
    started = time.perf_counter()
    vpc, error = _need_vpc()
    if not vpc:
        return _result('create-nat-gateways', CREATE_NATS_COMMAND, {}, False, error, started)
    vpc_id = vpc['VpcId']
    subnets = _subnets(vpc_id)
    addresses = _addresses()
    problems = []
    for az in ZONES:
        if _subnet_spec('public', az)['name'] not in subnets:
            problems.append(f"subnet {_subnet_spec('public', az)['name']}")
        if az not in addresses:
            problems.append(f'Elastic IP {EIP_NAMES[az]}')
    if problems:
        return _result('create-nat-gateways', CREATE_NATS_COMMAND, {}, False, f"Create {', '.join(problems)} first.", started)
    if not _is_public(_route_tables(vpc_id), subnets[_subnet_spec('public', ZONES[0])['name']]['SubnetId']):
        return _result('create-nat-gateways', CREATE_NATS_COMMAND, {}, False,
                       'Route the public subnets to the internet gateway first: a NAT gateway in a subnet without that '
                       'route cannot reach the internet.', started)
    ec2 = _client('ec2')
    existing = _nat_gateways(vpc_id)
    for az, name in NAT_NAMES.items():
        if az not in existing:
            ec2.create_nat_gateway(
                SubnetId=subnets[_subnet_spec('public', az)['name']]['SubnetId'],
                AllocationId=addresses[az]['AllocationId'],
                TagSpecifications=_tags('natgateway', name),
            )
    verified, message = _wait(_check_nats, NAT_WAIT_SECONDS)
    response = {'NatGateways': list(_nat_gateways(vpc_id).values())}
    return _result('create-nat-gateways', CREATE_NATS_COMMAND, response, verified, message, started)


def step_route_app() -> dict[str, Any]:
    started = time.perf_counter()
    vpc, error = _need_vpc()
    if not vpc:
        return _result('route-app-subnets', ROUTE_APP_COMMAND, {}, False, error, started)
    vpc_id = vpc['VpcId']
    subnets = _subnets(vpc_id)
    nats = _nat_gateways(vpc_id)
    missing = [NAT_NAMES[az] for az in ZONES if az not in nats]
    missing += [_subnet_spec('app', az)['name'] for az in ZONES if _subnet_spec('app', az)['name'] not in subnets]
    if missing:
        return _result('route-app-subnets', ROUTE_APP_COMMAND, {}, False, f"Create {', '.join(missing)} first.", started)
    tables = _route_tables(vpc_id)
    for az in ZONES:
        table = _ensure_table(vpc_id, APP_ROUTE_TABLES[az], tables)
        _set_default_route(table, NatGatewayId=nats[az]['NatGatewayId'])
        _associate(table, subnets[_subnet_spec('app', az)['name']]['SubnetId'], tables)
    verified, message = _check_app_routes()
    current = _route_tables(vpc_id)
    response = {'RouteTables': [_by_name(current, name) for name in APP_ROUTE_TABLES.values()]}
    return _result('route-app-subnets', ROUTE_APP_COMMAND, response, verified, message, started)


def step_isolate_data() -> dict[str, Any]:
    started = time.perf_counter()
    vpc, error = _need_vpc()
    if not vpc:
        return _result('isolate-data-subnets', ISOLATE_DATA_COMMAND, {}, False, error, started)
    vpc_id = vpc['VpcId']
    subnets = _subnets(vpc_id)
    names = [_subnet_spec('data', az)['name'] for az in ZONES]
    missing = [name for name in names if name not in subnets]
    if missing:
        return _result('isolate-data-subnets', ISOLATE_DATA_COMMAND, {}, False, f"Create subnet {', '.join(missing)} first.", started)
    tables = _route_tables(vpc_id)
    table = _ensure_table(vpc_id, DATA_ROUTE_TABLE, tables)
    subnet_ids = [subnets[name]['SubnetId'] for name in names]
    for subnet_id in subnet_ids:
        _associate(table, subnet_id, tables)
    rds = _client('rds')
    group = _db_subnet_group()
    if not group:
        group = rds.create_db_subnet_group(
            DBSubnetGroupName=DB_SUBNET_GROUP,
            DBSubnetGroupDescription='Data tier, no internet route',
            SubnetIds=subnet_ids,
            Tags=[LAB_TAG],
        )['DBSubnetGroup']
    elif {item.get('SubnetIdentifier') for item in group.get('Subnets') or []} != set(subnet_ids):
        group = rds.modify_db_subnet_group(DBSubnetGroupName=DB_SUBNET_GROUP, SubnetIds=subnet_ids)['DBSubnetGroup']
    verified, message = _check_data_tier()
    response = {'RouteTable': _by_name(_route_tables(vpc_id), DATA_ROUTE_TABLE), 'DBSubnetGroup': group}
    return _result('isolate-data-subnets', ISOLATE_DATA_COMMAND, response, verified, message, started)


def step_add_endpoint() -> dict[str, Any]:
    started = time.perf_counter()
    vpc, error = _need_vpc()
    if not vpc:
        return _result('add-s3-gateway-endpoint', ENDPOINT_COMMAND, {}, False, error, started)
    vpc_id = vpc['VpcId']
    tables = _route_tables(vpc_id)
    table_ids = [(_by_name(tables, name) or {}).get('RouteTableId') for name in APP_ROUTE_TABLES.values()]
    if not all(table_ids):
        return _result('add-s3-gateway-endpoint', ENDPOINT_COMMAND, {}, False,
                       'Create the application route tables first.', started)
    ec2 = _client('ec2')
    endpoint = _endpoint(vpc_id)
    if not endpoint:
        endpoint = ec2.create_vpc_endpoint(
            VpcEndpointType='Gateway', VpcId=vpc_id, ServiceName=_s3_service_name(), RouteTableIds=table_ids,
            TagSpecifications=_tags('vpc-endpoint', ENDPOINT_NAME),
        )['VpcEndpoint']
    else:
        add = [table_id for table_id in table_ids if table_id not in (endpoint.get('RouteTableIds') or [])]
        if add:
            ec2.modify_vpc_endpoint(VpcEndpointId=endpoint['VpcEndpointId'], AddRouteTableIds=add)
            endpoint = _endpoint(vpc_id) or endpoint
    verified, message = _check_endpoint()
    if verified:
        current = _route_tables(vpc_id)
        shown = any(route.get('DestinationPrefixListId') for name in APP_ROUTE_TABLES.values()
                    for route in (_by_name(current, name) or {}).get('Routes') or [])
        if not shown:
            message += ' Floci does not add the prefix-list route to the tables; AWS would show one per table.'
    return _result('add-s3-gateway-endpoint', ENDPOINT_COMMAND, {'VpcEndpoint': endpoint}, verified, message, started)


def step_trace() -> dict[str, Any]:
    started = time.perf_counter()
    verified, message, paths = _trace()
    vpc = _vpc() if verified or paths else None
    response: dict[str, Any] = {'EgressPaths': paths}
    if vpc:
        response['NatGateways'] = [
            {'Name': _name(nat), 'NatGatewayId': nat['NatGatewayId'], 'SubnetId': nat.get('SubnetId'), 'State': nat.get('State'),
             'PublicIp': next((item.get('PublicIp') for item in nat.get('NatGatewayAddresses') or []), None)}
            for nat in _nat_gateways(vpc['VpcId']).values()
        ]
    return _result('trace-egress-paths', TRACE_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-vpc': step_create_vpc,
    'create-tier-subnets': step_create_subnets,
    'attach-internet-gateway': step_attach_igw,
    'route-public-subnets': step_route_public,
    'allocate-elastic-ips': step_allocate_eips,
    'create-nat-gateways': step_create_nats,
    'route-app-subnets': step_route_app,
    'isolate-data-subnets': step_isolate_data,
    'add-s3-gateway-endpoint': step_add_endpoint,
    'trace-egress-paths': step_trace,
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
    """Every step is checked against live state; the trace also needs its marker."""
    checks = {
        'create-vpc': lambda: _check_vpc()[0],
        'create-tier-subnets': lambda: _check_subnets()[0],
        'attach-internet-gateway': lambda: _check_igw()[0],
        'route-public-subnets': lambda: _check_public_routes()[0],
        'allocate-elastic-ips': lambda: _check_addresses()[0],
        'create-nat-gateways': lambda: _check_nats()[0],
        'route-app-subnets': lambda: _check_app_routes()[0],
        'isolate-data-subnets': lambda: _check_data_tier()[0],
        'add-s3-gateway-endpoint': lambda: _check_endpoint()[0],
        'trace-egress-paths': lambda: bool(_marked('trace-egress-paths')) and _trace()[0],
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


def _wait_until(check, attempts: int, delay: float = 2.0) -> bool:
    for _ in range(attempts):
        try:
            if check():
                return True
        except (ClientError, BotoCoreError):
            pass
        time.sleep(delay)
    return False


def reset() -> dict[str, Any]:
    """Delete in dependency order: endpoint, NAT gateways, Elastic IPs, DB subnet group, routes, gateway, subnets, VPC."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call, retry_codes: frozenset[str] = frozenset()) -> None:
        for _try in range(15):
            try:
                call()
                deleted.append(label)
                return
            except ClientError as exc:
                code = _code(exc)
                if 'NotFound' in code or code.startswith('InvalidAllocationID') or code in {'InvalidVpcEndpointId.NotFound'}:
                    return
                if code in retry_codes:
                    time.sleep(2)
                    continue
                errors.append(f'{label}: {exc}')
                return
            except BotoCoreError as exc:
                errors.append(f'{label}: {exc}')
                return
        errors.append(f'{label}: still in use after retries')

    ec2 = _client('ec2')
    in_use = frozenset({'DependencyViolation', 'InvalidIPAddress.InUse', 'InvalidDBSubnetGroupStateFault'})
    vpc = _vpc() if _safe(_vpc) else None
    if vpc:
        vpc_id = vpc['VpcId']
        endpoints = ec2.describe_vpc_endpoints(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('VpcEndpoints', [])
        for endpoint in endpoints:
            attempt(f"vpc-endpoint:{endpoint['VpcEndpointId']}",
                    lambda endpoint=endpoint: ec2.delete_vpc_endpoints(VpcEndpointIds=[endpoint['VpcEndpointId']]))
        nats = _nat_gateways(vpc_id) if _safe(lambda: _nat_gateways(vpc_id)) else {}
        for nat in nats.values():
            attempt(f"nat-gateway:{_name(nat)}", lambda nat=nat: ec2.delete_nat_gateway(NatGatewayId=nat['NatGatewayId']))
        if nats and not _wait_until(lambda: not _nat_gateways(vpc_id), 60, 3.0):
            errors.append('NAT gateways still deleting after 3 minutes')

    for address in (_addresses() if _safe(_addresses) else {}).values():
        attempt(f"elastic-ip:{_name(address)}",
                lambda address=address: ec2.release_address(AllocationId=address['AllocationId']), in_use)

    if _safe(_db_subnet_group) and _db_subnet_group():
        attempt(f'db-subnet-group:{DB_SUBNET_GROUP}',
                lambda: _client('rds').delete_db_subnet_group(DBSubnetGroupName=DB_SUBNET_GROUP), in_use)

    if vpc:
        vpc_id = vpc['VpcId']
        lab_tables = {PUBLIC_ROUTE_TABLE, DATA_ROUTE_TABLE, *APP_ROUTE_TABLES.values()}
        for table in _route_tables(vpc_id):
            if _name(table) not in lab_tables:
                continue
            for association in table.get('Associations') or []:
                if association.get('RouteTableAssociationId') and not association.get('Main'):
                    attempt(f"route-table-association:{association['RouteTableAssociationId']}",
                            lambda association=association: ec2.disassociate_route_table(
                                AssociationId=association['RouteTableAssociationId']))
            attempt(f'route-table:{_name(table)}', lambda table=table: ec2.delete_route_table(RouteTableId=table['RouteTableId']),
                    in_use)
        gateway = _internet_gateway(vpc_id)
        if gateway:
            gateway_id = gateway['InternetGatewayId']
            attempt(f'internet-gateway-attachment:{gateway_id}',
                    lambda: ec2.detach_internet_gateway(InternetGatewayId=gateway_id, VpcId=vpc_id), in_use)
            attempt(f'internet-gateway:{gateway_id}', lambda: ec2.delete_internet_gateway(InternetGatewayId=gateway_id), in_use)
        for subnet in _subnets(vpc_id).values():
            attempt(f"subnet:{_name(subnet)}", lambda subnet=subnet: ec2.delete_subnet(SubnetId=subnet['SubnetId']), in_use)
        attempt(f'vpc:{VPC_NAME}', lambda: ec2.delete_vpc(VpcId=vpc_id), in_use)

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws ec2 delete-vpc-endpoints --vpc-endpoint-ids <vpce-id>\n'
            'aws ec2 delete-nat-gateway --nat-gateway-id <nat-a-id>   # and <nat-b-id>; wait until deleted\n'
            'aws ec2 release-address --allocation-id <eip-a-allocation-id>   # and B\n'
            f'aws rds delete-db-subnet-group --db-subnet-group-name {DB_SUBNET_GROUP}\n'
            'aws ec2 delete-route-table ... ; detach-internet-gateway ... ; delete-subnet ... ; delete-vpc ...'
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
            'message': 'Some lab resources could not be deleted: ' + '; '.join(errors) if errors
            else 'Three-tier VPC lab resources deleted in dependency order.',
        },
    }
