"""Elastic Load Balancing and Auto Scaling workflow lab: a highly available web tier."""

from __future__ import annotations

import base64
import json
import re
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response
from dashboard.ec2_api import instance_command_detail, run_instance_command

SERVICE = 'elasticloadbalancing'
LAB_KEY = 'ha-web-tier'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'

VPC_NAME = 'lab-ha-web-vpc'
VPC_CIDR = '10.62.0.0/16'
SUBNETS = (
    {'name': 'lab-ha-web-public-a', 'cidr': '10.62.1.0/24', 'az': 'us-east-1a'},
    {'name': 'lab-ha-web-public-b', 'cidr': '10.62.2.0/24', 'az': 'us-east-1b'},
)
IGW_NAME = 'lab-ha-web-igw'
ROUTE_TABLE_NAME = 'lab-ha-web-public-rt'
ALB_SG_NAME = 'lab-ha-web-alb-sg'
WEB_SG_NAME = 'lab-ha-web-instance-sg'
WEB_PORT = 8080
LISTENER_PORT = 80
LAUNCH_TEMPLATE_NAME = 'lab-ha-web-lt'
IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't2.micro'
TARGET_GROUP_NAME = 'lab-ha-web-tg'
LOAD_BALANCER_NAME = 'lab-ha-web-alb'
ASG_NAME = 'lab-ha-web-asg'
POLICY_NAME = 'lab-ha-web-cpu50'
MIN_SIZE = 2
MAX_SIZE = 4
SCALED_CAPACITY = 3
GRACE_PERIOD_SECONDS = 60
V1_BODY = 'ha-web-v1'
V2_BODY = 'ha-web-v2'
V2_DESCRIPTION = 'v2: new release'
PROBE_REQUESTS = 8

# Waits for instances to boot, run user data, and pass health checks. On Floci
# 2.3.0 a new instance takes about a minute to turn healthy.
HEALTHY_WAIT_SECONDS = 180
REFRESH_WAIT_SECONDS = 240


def user_data_script(body: str) -> str:
    """A small web server on port 8080 that answers with the release and the instance ID.

    Perl is in the base Ubuntu image, so the server needs no packages.
    """
    return f'''#!/bin/sh
cat > /opt/web.pl <<'PERL'
use IO::Socket::INET;
sub get {{
  my ($host, $path) = @_;
  my $s = IO::Socket::INET->new(PeerAddr => $host, PeerPort => 80, Timeout => 2) or return '';
  print $s "GET $path HTTP/1.0\\r\\nHost: $host\\r\\n\\r\\n";
  local $/; my $r = <$s>; $r =~ s/^.*?\\r\\n\\r\\n//s; return $r;
}}
my $id = get('169.254.169.254', '/latest/meta-data/instance-id') || `hostname`;
chomp $id;
my $server = IO::Socket::INET->new(LocalPort => {WEB_PORT}, Listen => 10, Reuse => 1) or die $!;
while (my $c = $server->accept()) {{
  while (<$c>) {{ last if /^\\r?$/; }}
  my $b = "{body} $id\\n";
  print $c "HTTP/1.0 200 OK\\r\\nContent-Type: text/plain\\r\\nContent-Length: " . length($b) . "\\r\\n\\r\\n$b";
  close $c;
}}
PERL
nohup perl /opt/web.pl >/var/log/web.log 2>&1 &
'''


USER_DATA_V1 = user_data_script(V1_BODY)
USER_DATA_V2 = user_data_script(V2_BODY)

CREATE_NETWORK_COMMAND = (
    f'aws ec2 create-vpc --cidr-block {VPC_CIDR} '
    f"--tag-specifications 'ResourceType=vpc,Tags=[{{Key=Name,Value={VPC_NAME}}}]'\n"
    f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {SUBNETS[0]["cidr"]} --availability-zone {SUBNETS[0]["az"]}\n'
    f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {SUBNETS[1]["cidr"]} --availability-zone {SUBNETS[1]["az"]}\n'
    'aws ec2 create-internet-gateway\n'
    'aws ec2 attach-internet-gateway --internet-gateway-id <igw-id> --vpc-id <vpc-id>\n'
    'aws ec2 create-route-table --vpc-id <vpc-id>\n'
    'aws ec2 create-route --route-table-id <route-table-id> --destination-cidr-block 0.0.0.0/0 --gateway-id <igw-id>\n'
    'aws ec2 associate-route-table --route-table-id <route-table-id> --subnet-id <subnet-a-id>\n'
    'aws ec2 associate-route-table --route-table-id <route-table-id> --subnet-id <subnet-b-id>'
)
CREATE_SECURITY_GROUPS_COMMAND = (
    f'aws ec2 create-security-group --group-name {ALB_SG_NAME} --description "Load balancer" --vpc-id <vpc-id>\n'
    f'aws ec2 authorize-security-group-ingress --group-id <alb-sg-id> --protocol tcp --port {LISTENER_PORT} --cidr 0.0.0.0/0\n'
    f'aws ec2 create-security-group --group-name {WEB_SG_NAME} --description "Web servers" --vpc-id <vpc-id>\n'
    f'aws ec2 authorize-security-group-ingress --group-id <instance-sg-id> --protocol tcp --port {WEB_PORT} --source-group <alb-sg-id>'
)
CREATE_LAUNCH_TEMPLATE_COMMAND = (
    f'aws ec2 create-launch-template --launch-template-name {LAUNCH_TEMPLATE_NAME} '
    f"--launch-template-data '{{\"ImageId\":\"{IMAGE_ID}\",\"InstanceType\":\"{INSTANCE_TYPE}\","
    '"SecurityGroupIds":["<instance-sg-id>"],"UserData":"<base64 of user-data.sh>"}\''
)
CREATE_TARGET_GROUP_COMMAND = (
    f'aws elbv2 create-target-group --name {TARGET_GROUP_NAME} --protocol HTTP --port {WEB_PORT} '
    '--vpc-id <vpc-id> --target-type instance --health-check-path / --health-check-interval-seconds 10 '
    '--healthy-threshold-count 2 --unhealthy-threshold-count 2'
)
CREATE_LOAD_BALANCER_COMMAND = (
    f'aws elbv2 create-load-balancer --name {LOAD_BALANCER_NAME} --type application --scheme internet-facing '
    '--subnets <subnet-a-id> <subnet-b-id> --security-groups <alb-sg-id>\n'
    f'aws elbv2 create-listener --load-balancer-arn <alb-arn> --protocol HTTP --port {LISTENER_PORT} '
    '--default-actions Type=forward,TargetGroupArn=<target-group-arn>'
)
CREATE_ASG_COMMAND = (
    f'aws autoscaling create-auto-scaling-group --auto-scaling-group-name {ASG_NAME} '
    f"--launch-template 'LaunchTemplateName={LAUNCH_TEMPLATE_NAME},Version=$Latest' "
    f'--min-size {MIN_SIZE} --max-size {MAX_SIZE} --desired-capacity {MIN_SIZE} '
    '--vpc-zone-identifier "<subnet-a-id>,<subnet-b-id>" --target-group-arns <target-group-arn> '
    f'--health-check-type ELB --health-check-grace-period {GRACE_PERIOD_SECONDS}'
)
TARGET_HEALTH_COMMAND = 'aws elbv2 describe-target-health --target-group-arn <target-group-arn>'
PROBE_COMMAND = (
    'aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript '
    f'--parameters \'commands=["for i in $(seq {PROBE_REQUESTS}); do curl -s http://<alb-dns-name>/; done"]\''
)
SCALING_POLICY_COMMAND = (
    f'aws autoscaling put-scaling-policy --auto-scaling-group-name {ASG_NAME} --policy-name {POLICY_NAME} '
    '--policy-type TargetTrackingScaling --target-tracking-configuration '
    '\'{"PredefinedMetricSpecification":{"PredefinedMetricType":"ASGAverageCPUUtilization"},"TargetValue":50.0}\''
)
SCALE_OUT_COMMAND = (
    f'aws autoscaling set-desired-capacity --auto-scaling-group-name {ASG_NAME} --desired-capacity {SCALED_CAPACITY}'
)
SELF_HEAL_COMMAND = 'aws ec2 terminate-instances --instance-ids <one-asg-instance-id>'
INSTANCE_REFRESH_COMMAND = (
    f'aws ec2 create-launch-template-version --launch-template-name {LAUNCH_TEMPLATE_NAME} --source-version 1 '
    f'--version-description "{V2_DESCRIPTION}" --launch-template-data \'{{"UserData":"<base64 of user-data-v2.sh>"}}\'\n'
    f'aws autoscaling start-instance-refresh --auto-scaling-group-name {ASG_NAME} '
    "--preferences '{\"MinHealthyPercentage\":50,\"InstanceWarmup\":30}'"
)

FLOCI_REACH_NOTE = (
    'How Floci emulates this: Floci serves the listener on that port inside the Floci container and resolves the '
    "load balancer's DNS name to it for instances, so you test it from an instance; the port is not published to "
    'your computer.'
)

HA_WEB_TIER_LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Build a highly available web tier with an Application Load Balancer and Auto Scaling',
    'description': (
        'Spread a web tier across two Availability Zones: a launch template with a web server in user data, an '
        'Application Load Balancer with a health-checked target group, and an Auto Scaling group that keeps two '
        'instances behind it. Send traffic through the load balancer, scale out, watch the group replace a terminated '
        'instance, and roll out a new release with an instance refresh.'
    ),
    'steps': [
        {
            'key': 'create-network',
            'title': 'Create a VPC with public subnets in two Availability Zones',
            'command': CREATE_NETWORK_COMMAND,
            'explanation': (
                'High availability starts with placement: an Application Load Balancer needs subnets in at least two '
                'Availability Zones, and an Auto Scaling group spread over them keeps serving if one zone fails. An '
                'internet-facing load balancer needs public subnets, so both get a route to an internet gateway. In '
                'production you would usually put the instances in private subnets and only the load balancer in '
                'public ones.'
            ),
        },
        {
            'key': 'create-security-groups',
            'title': 'Let only the load balancer reach the web servers',
            'command': CREATE_SECURITY_GROUPS_COMMAND,
            'explanation': (
                f'The load balancer group accepts HTTP on port {LISTENER_PORT} from anywhere. The instance group '
                f'accepts port {WEB_PORT} only from members of the load balancer group (a security group reference '
                'instead of a CIDR), so clients cannot bypass the load balancer. '
                'How Floci emulates this: Floci stores security groups but enforces them only with '
                'FLOCI_NETWORK_SECURITY_GROUP_ENFORCEMENT_ENABLED=true.'
            ),
        },
        {
            'key': 'create-launch-template',
            'title': 'Create a launch template with a web server in user data',
            'command': CREATE_LAUNCH_TEMPLATE_COMMAND,
            'explanation': (
                'A launch template is the blueprint the Auto Scaling group launches from: AMI, instance type, '
                'security groups, and user data. Templates are versioned, which the instance refresh at the end uses. '
                f'The user data starts a web server on port {WEB_PORT} that answers "{V1_BODY} <instance-id>", so you '
                'can see which instance served each request. Launch configurations are the older, unversioned '
                'alternative; AWS recommends launch templates.'
            ),
            'artifact_label': 'user-data.sh',
            'artifact': USER_DATA_V1,
        },
        {
            'key': 'create-target-group',
            'title': 'Create a target group with an HTTP health check',
            'command': CREATE_TARGET_GROUP_COMMAND,
            'explanation': (
                'A target group lists the instances that receive traffic and probes each one. Here a target turns '
                'healthy after 2 successful checks of "/" ten seconds apart, and unhealthy after 2 failures; the load '
                'balancer only routes to healthy targets.'
            ),
        },
        {
            'key': 'create-load-balancer',
            'title': 'Create an internet-facing Application Load Balancer and listener',
            'command': CREATE_LOAD_BALANCER_COMMAND,
            'explanation': (
                'The Application Load Balancer gets a node in each subnet and one DNS name; clients use the name, '
                'never instance addresses. The listener accepts HTTP on port 80 and forwards to the target group. '
                'An ALB works at layer 7 (paths, hosts, headers); a Network Load Balancer works at layer 4 for TCP, '
                'UDP, static IPs, and very high throughput. '
                + FLOCI_REACH_NOTE
            ),
        },
        {
            'key': 'create-auto-scaling-group',
            'title': 'Create an Auto Scaling group across both subnets',
            'command': CREATE_ASG_COMMAND,
            'explanation': (
                f'The group keeps between {MIN_SIZE} and {MAX_SIZE} instances, spreads them across the two subnets, '
                'and registers each one with the target group. With --health-check-type ELB the group also treats an '
                'instance that fails the load balancer health check as unhealthy and replaces it, not just one whose '
                f'EC2 status checks fail; the {GRACE_PERIOD_SECONDS}-second grace period gives new instances time to '
                'boot first. '
                'How Floci emulates this: Floci replaces instances that stop or are terminated, but not ones that '
                'fail health checks, and it ignores the grace period.'
            ),
        },
        {
            'key': 'wait-healthy-targets',
            'title': 'Watch the target group mark both instances healthy',
            'command': TARGET_HEALTH_COMMAND,
            'explanation': (
                'New targets start as "initial" and stay unhealthy until user data has started the web server and '
                'enough health checks pass. The step waits until every instance in the group is a healthy target, '
                'which takes about a minute.'
            ),
        },
        {
            'key': 'test-load-balancer',
            'title': 'Send requests through the load balancer',
            'command': PROBE_COMMAND,
            'explanation': (
                f'Sends {PROBE_REQUESTS} requests to the load balancer DNS name from inside one of the instances. Each '
                'answer names the instance that served it, and the step checks that every healthy instance answered: '
                'the ALB spreads requests over the targets in both Availability Zones (round robin by default). '
                'The dashboard sends the requests with a short Perl client, because the instance image may not '
                'include curl. '
                + FLOCI_REACH_NOTE
            ),
        },
        {
            'key': 'add-target-tracking-policy',
            'title': 'Add a target tracking scaling policy',
            'command': SCALING_POLICY_COMMAND,
            'explanation': (
                'Target tracking keeps a metric near a target, like a thermostat: here, average CPU at 50 percent. '
                'Auto Scaling creates the CloudWatch alarms itself and adds instances when CPU is above the target and '
                'removes them when it is well below. Step scaling and simple scaling react to alarms you define; '
                'scheduled scaling changes capacity at set times; predictive scaling forecasts from history. '
                'How Floci emulates this: Floci stores the policy but never runs it, so the next step scales out by '
                'hand, as the policy would.'
            ),
        },
        {
            'key': 'scale-out',
            'title': f'Scale out to {SCALED_CAPACITY} instances',
            'command': SCALE_OUT_COMMAND,
            'explanation': (
                'Raising the desired capacity is what a scaling policy does when the metric is above target. The '
                'group launches a third instance from the launch template, registers it with the target group, and '
                'the load balancer starts sending it traffic once it is healthy. The step waits for that.'
            ),
        },
        {
            'key': 'self-heal',
            'title': 'Terminate an instance and watch the group replace it',
            'command': SELF_HEAL_COMMAND,
            'explanation': (
                'The group notices the desired capacity is no longer met, deregisters the lost instance from the '
                'target group, and launches a replacement in the same Availability Zone to keep the group balanced. '
                'The load balancer keeps serving from the remaining healthy instances meanwhile. The step waits until '
                'the replacement is healthy. '
                'How Floci emulates this: Floci replaces terminated or stopped instances. It does not replace an '
                'instance you mark Unhealthy with set-instance-health, as AWS does.'
            ),
        },
        {
            'key': 'instance-refresh',
            'title': 'Roll out a new release with an instance refresh',
            'command': INSTANCE_REFRESH_COMMAND,
            'explanation': (
                f'Version 2 of the launch template answers "{V2_BODY}". Because the group uses $Latest, new instances '
                'get it, and an instance refresh replaces the existing ones. With MinHealthyPercentage 50, AWS '
                'replaces instances in batches and keeps at least half the capacity serving; InstanceWarmup is how '
                'long a new instance gets before it counts as healthy. A failed refresh can be cancelled or rolled '
                'back. The step checks that every instance runs version 2 and that the load balancer serves only the '
                'new release. '
                'How Floci emulates this: Floci replaces every instance at once, whatever MinHealthyPercentage says, '
                'so the site is briefly unavailable, and it cannot cancel or roll back a refresh. It also keeps only '
                'the latest launch template version, so version 1 cannot be launched again.'
            ),
            'artifact_label': 'user-data-v2.sh',
            'artifact': USER_DATA_V2,
        },
    ],
}

LABS = [HA_WEB_TIER_LAB]


# ── Clients, markers, results ──────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _marker(step: str, value: Any = True) -> None:
    cache.set(CACHE_PREFIX + step, _clean_response(value), timeout=86400)


def _marked(step: str) -> Any:
    return cache.get(CACHE_PREFIX + step)


def _error_code(exc: ClientError) -> str:
    return exc.response.get('Error', {}).get('Code') or ''


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


def _wait(check, timeout_seconds: float, interval: float = 3.0) -> tuple[bool, str]:
    """Re-run ``check`` (returning (ok, message)) until it passes or time runs out."""
    deadline = time.monotonic() + timeout_seconds
    while True:
        ok, message = check()
        if ok or time.monotonic() >= deadline:
            return ok, message
        time.sleep(interval)


# ── Lookups by the lab's fixed names ───────────────────────────────────────────

def _by_name_tag(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for item in items:
        if any(tag.get('Key') == 'Name' and tag.get('Value') == name for tag in item.get('Tags') or []):
            return item
    return None


def _vpc() -> dict[str, Any] | None:
    vpcs = _client('ec2').describe_vpcs(Filters=[{'Name': 'tag:Name', 'Values': [VPC_NAME]}]).get('Vpcs', [])
    return _by_name_tag(vpcs, VPC_NAME)


def _subnets(vpc_id: str) -> list[dict[str, Any]]:
    subnets = _client('ec2').describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', [])
    found = []
    for spec in SUBNETS:
        subnet = _by_name_tag(subnets, spec['name'])
        if subnet:
            found.append(subnet)
    return found


def _security_group(vpc_id: str, name: str) -> dict[str, Any] | None:
    groups = _client('ec2').describe_security_groups(Filters=[
        {'Name': 'vpc-id', 'Values': [vpc_id]},
        {'Name': 'group-name', 'Values': [name]},
    ]).get('SecurityGroups', [])
    return next((group for group in groups if group.get('GroupName') == name), None)


def _launch_template() -> dict[str, Any] | None:
    try:
        templates = _client('ec2').describe_launch_templates(LaunchTemplateNames=[LAUNCH_TEMPLATE_NAME]).get('LaunchTemplates', [])
    except ClientError as exc:
        if 'NotFound' in _error_code(exc):
            return None
        raise
    return templates[0] if templates else None


def _launch_template_versions() -> list[dict[str, Any]]:
    try:
        return _client('ec2').describe_launch_template_versions(LaunchTemplateName=LAUNCH_TEMPLATE_NAME).get('LaunchTemplateVersions', [])
    except ClientError as exc:
        if 'NotFound' in _error_code(exc):
            return []
        raise


def _decoded_user_data(version: dict[str, Any]) -> str:
    raw = (version.get('LaunchTemplateData') or {}).get('UserData') or ''
    try:
        return base64.b64decode(raw).decode()
    except (ValueError, UnicodeDecodeError):
        return ''


def _target_group() -> dict[str, Any] | None:
    try:
        groups = _client('elbv2').describe_target_groups(Names=[TARGET_GROUP_NAME]).get('TargetGroups', [])
    except ClientError as exc:
        if _error_code(exc) == 'TargetGroupNotFound':
            return None
        raise
    return groups[0] if groups else None


def _load_balancer() -> dict[str, Any] | None:
    try:
        balancers = _client('elbv2').describe_load_balancers(Names=[LOAD_BALANCER_NAME]).get('LoadBalancers', [])
    except ClientError as exc:
        if _error_code(exc) == 'LoadBalancerNotFound':
            return None
        raise
    return balancers[0] if balancers else None


def _listeners(lb_arn: str) -> list[dict[str, Any]]:
    return _client('elbv2').describe_listeners(LoadBalancerArn=lb_arn).get('Listeners', [])


def _asg() -> dict[str, Any] | None:
    groups = _client('autoscaling').describe_auto_scaling_groups(AutoScalingGroupNames=[ASG_NAME]).get('AutoScalingGroups', [])
    return groups[0] if groups else None


def _in_service(group: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [item for item in (group or {}).get('Instances') or [] if item.get('LifecycleState') == 'InService']


def _target_health(tg_arn: str) -> dict[str, str]:
    descriptions = _client('elbv2').describe_target_health(TargetGroupArn=tg_arn).get('TargetHealthDescriptions', [])
    return {item['Target']['Id']: (item.get('TargetHealth') or {}).get('State') for item in descriptions}


# ── Checks ─────────────────────────────────────────────────────────────────────

def _check_network() -> tuple[bool, str]:
    vpc = _vpc()
    if not vpc:
        return False, f'VPC {VPC_NAME} not found.'
    subnets = _subnets(vpc['VpcId'])
    zones = sorted({subnet.get('AvailabilityZone') for subnet in subnets})
    if len(subnets) != len(SUBNETS) or len(zones) < 2:
        return False, f'{VPC_NAME} needs one subnet in each of {SUBNETS[0]["az"]} and {SUBNETS[1]["az"]}; found zones {zones}.'
    ec2 = _client('ec2')
    gateways = ec2.describe_internet_gateways(Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc['VpcId']]}]).get('InternetGateways', [])
    if not gateways:
        return False, f'{VPC_NAME} has no internet gateway attached.'
    tables = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc['VpcId']]}]).get('RouteTables', [])
    table = _by_name_tag(tables, ROUTE_TABLE_NAME)
    if not table:
        return False, f'Route table {ROUTE_TABLE_NAME} not found.'
    has_default = any(
        route.get('DestinationCidrBlock') == '0.0.0.0/0' and (route.get('GatewayId') or '').startswith('igw-')
        for route in table.get('Routes') or []
    )
    associated = {item.get('SubnetId') for item in table.get('Associations') or []}
    if not has_default:
        return False, f'{ROUTE_TABLE_NAME} has no default route to the internet gateway.'
    missing = [subnet['SubnetId'] for subnet in subnets if subnet['SubnetId'] not in associated]
    if missing:
        return False, f'Subnets {missing} are not associated with {ROUTE_TABLE_NAME}.'
    return True, (
        f"{VPC_NAME} ({vpc['VpcId']}) has public subnets in {', '.join(zones)} routed to "
        f"{gateways[0]['InternetGatewayId']}."
    )


def _check_security_groups() -> tuple[bool, str]:
    vpc = _vpc()
    if not vpc:
        return False, f'VPC {VPC_NAME} not found. Run the first step.'
    alb_group = _security_group(vpc['VpcId'], ALB_SG_NAME)
    web_group = _security_group(vpc['VpcId'], WEB_SG_NAME)
    if not alb_group or not web_group:
        return False, f'Security groups {ALB_SG_NAME} and {WEB_SG_NAME} must both exist in {VPC_NAME}.'
    alb_open = any(
        rule.get('IpProtocol') == 'tcp' and rule.get('FromPort') == LISTENER_PORT
        and any(item.get('CidrIp') == '0.0.0.0/0' for item in rule.get('IpRanges') or [])
        for rule in alb_group.get('IpPermissions') or []
    )
    web_rules = [rule for rule in web_group.get('IpPermissions') or [] if rule.get('FromPort') == WEB_PORT]
    web_from_alb = any(
        any(pair.get('GroupId') == alb_group['GroupId'] for pair in rule.get('UserIdGroupPairs') or [])
        for rule in web_rules
    )
    web_open = any(rule.get('IpRanges') for rule in web_group.get('IpPermissions') or [])
    if not alb_open:
        return False, f'{ALB_SG_NAME} does not allow TCP {LISTENER_PORT} from 0.0.0.0/0.'
    if not web_from_alb:
        return False, f'{WEB_SG_NAME} does not allow TCP {WEB_PORT} from {ALB_SG_NAME}.'
    if web_open:
        return False, f'{WEB_SG_NAME} also allows traffic from CIDR ranges; only {ALB_SG_NAME} should reach it.'
    return True, (
        f"{ALB_SG_NAME} ({alb_group['GroupId']}) accepts HTTP from anywhere; {WEB_SG_NAME} ({web_group['GroupId']}) "
        f'accepts port {WEB_PORT} only from the load balancer group.'
    )


def _check_launch_template() -> tuple[bool, str]:
    template = _launch_template()
    if not template:
        return False, f'Launch template {LAUNCH_TEMPLATE_NAME} not found.'
    # Floci 2.3.0 keeps only the latest version, so after the refresh step version 1 is gone;
    # either release's web server counts.
    versions = _launch_template_versions()
    version = next((v for v in versions if v.get('VersionNumber') == 1), None) or (versions[-1] if versions else None)
    if not version:
        return False, f'{LAUNCH_TEMPLATE_NAME} has no versions.'
    data = version.get('LaunchTemplateData') or {}
    user_data = _decoded_user_data(version)
    if data.get('ImageId') != IMAGE_ID or not (V1_BODY in user_data or V2_BODY in user_data):
        return False, f'{LAUNCH_TEMPLATE_NAME} does not launch {IMAGE_ID} with the web server in user data.'
    return True, (
        f"{LAUNCH_TEMPLATE_NAME} ({template['LaunchTemplateId']}) version {version.get('VersionNumber')} launches "
        f'{IMAGE_ID} {data.get("InstanceType")} with the web server in user data.'
    )


def _check_target_group() -> tuple[bool, str]:
    group = _target_group()
    if not group:
        return False, f'Target group {TARGET_GROUP_NAME} not found.'
    vpc = _vpc()
    problems = []
    if group.get('Port') != WEB_PORT or group.get('Protocol') != 'HTTP':
        problems.append(f"it forwards {group.get('Protocol')}:{group.get('Port')}, not HTTP:{WEB_PORT}")
    if group.get('TargetType') != 'instance':
        problems.append(f"target type is {group.get('TargetType')}")
    if vpc and group.get('VpcId') != vpc['VpcId']:
        problems.append(f'it is not in {VPC_NAME}')
    if group.get('HealthCheckPath') != '/':
        problems.append(f"the health check path is {group.get('HealthCheckPath')}")
    if problems:
        return False, f'{TARGET_GROUP_NAME}: ' + '; '.join(problems) + '.'
    return True, (
        f'{TARGET_GROUP_NAME} sends HTTP to port {WEB_PORT} and checks "/" every '
        f"{group.get('HealthCheckIntervalSeconds')} s (healthy after {group.get('HealthyThresholdCount')}, "
        f"unhealthy after {group.get('UnhealthyThresholdCount')})."
    )


def _check_load_balancer() -> tuple[bool, str]:
    balancer = _load_balancer()
    if not balancer:
        return False, f'Load balancer {LOAD_BALANCER_NAME} not found.'
    group = _target_group()
    zones = sorted({zone.get('ZoneName') for zone in balancer.get('AvailabilityZones') or []})
    state = (balancer.get('State') or {}).get('Code')
    problems = []
    if balancer.get('Type') != 'application' or balancer.get('Scheme') != 'internet-facing':
        problems.append(f"it is {balancer.get('Scheme')} {balancer.get('Type')}")
    if len(zones) < 2:
        problems.append(f'it spans {zones}, not two Availability Zones')
    if state != 'active':
        problems.append(f'its state is {state}')
    listener = next((item for item in _listeners(balancer['LoadBalancerArn']) if item.get('Port') == LISTENER_PORT), None)
    forwards = listener and any(
        action.get('Type') == 'forward' and action.get('TargetGroupArn') == (group or {}).get('TargetGroupArn')
        for action in listener.get('DefaultActions') or []
    )
    if not forwards:
        problems.append(f'no HTTP:{LISTENER_PORT} listener forwards to {TARGET_GROUP_NAME}')
    if problems:
        return False, f'{LOAD_BALANCER_NAME}: ' + '; '.join(problems) + '.'
    return True, (
        f"{LOAD_BALANCER_NAME} is active in {', '.join(zones)} at {balancer.get('DNSName')}; port {LISTENER_PORT} "
        f'forwards to {TARGET_GROUP_NAME}.'
    )


def _check_asg_config() -> tuple[bool, str]:
    group = _asg()
    if not group:
        return False, f'Auto Scaling group {ASG_NAME} not found.'
    target_group = _target_group()
    problems = []
    if (group.get('LaunchTemplate') or {}).get('LaunchTemplateName') != LAUNCH_TEMPLATE_NAME:
        problems.append(f'it does not launch from {LAUNCH_TEMPLATE_NAME}')
    if group.get('MinSize') != MIN_SIZE or group.get('MaxSize') != MAX_SIZE:
        problems.append(f"its size range is {group.get('MinSize')}-{group.get('MaxSize')}, not {MIN_SIZE}-{MAX_SIZE}")
    if (target_group or {}).get('TargetGroupArn') not in (group.get('TargetGroupARNs') or []):
        problems.append(f'it is not attached to {TARGET_GROUP_NAME}')
    if group.get('HealthCheckType') != 'ELB':
        problems.append(f"its health check type is {group.get('HealthCheckType')}, not ELB")
    if len([subnet for subnet in (group.get('VPCZoneIdentifier') or '').split(',') if subnet]) < 2:
        problems.append('it does not use both subnets')
    if problems:
        return False, f'{ASG_NAME}: ' + '; '.join(problems) + '.'
    return True, ''


def _check_asg() -> tuple[bool, str]:
    ok, message = _check_asg_config()
    if not ok:
        return ok, message
    group = _asg()
    instances = _in_service(group)
    zones = sorted({item.get('AvailabilityZone') for item in instances})
    if len(instances) < MIN_SIZE:
        return False, f'{ASG_NAME} has {len(instances)} InService instances; waiting for {MIN_SIZE}.'
    if len(zones) < 2:
        return False, f'{ASG_NAME} instances are all in {zones}; they should span two Availability Zones.'
    return True, (
        f"{ASG_NAME} runs {len(instances)} instances in {', '.join(zones)} "
        f"({', '.join(item['InstanceId'] for item in instances)}), registered with {TARGET_GROUP_NAME}."
    )


def _check_healthy_targets(expected: int) -> tuple[bool, str]:
    group = _asg()
    target_group = _target_group()
    if not group or not target_group:
        return False, f'{ASG_NAME} or {TARGET_GROUP_NAME} not found.'
    instances = {item['InstanceId'] for item in _in_service(group)}
    health = _target_health(target_group['TargetGroupArn'])
    healthy = {target for target, state in health.items() if state == 'healthy'}
    summary = ', '.join(f'{target} {state}' for target, state in sorted(health.items())) or 'no targets'
    if len(instances) < expected or not instances <= healthy:
        return False, f'Waiting for {expected} healthy instances of {ASG_NAME} in {TARGET_GROUP_NAME}: {summary}.'
    stale = set(health) - instances
    if stale:
        return False, f'{TARGET_GROUP_NAME} still lists instances outside the group: {sorted(stale)}.'
    return True, f'{TARGET_GROUP_NAME}: {summary}.'


# ── Requests through the load balancer, from inside an instance ────────────────

PROBE_SCRIPT = (
    "perl -MIO::Socket::INET -e 'for (1..$ARGV[1]) { "
    'my $s = IO::Socket::INET->new(PeerAddr => $ARGV[0], PeerPort => ' + str(LISTENER_PORT) + ', Timeout => 3) '
    'or do { print "connect failed: $!\\n"; next }; '
    'print $s "GET / HTTP/1.0\\r\\nHost: $ARGV[0]\\r\\n\\r\\n"; '
    'local $/; my $r = <$s>; $r =~ s/^.*?\\r\\n\\r\\n//s; print $r; }\''
)
RESPONSE_PATTERN = re.compile(r'(ha-web-v\d) (i-[0-9a-f]+)')


def _run_in_instance(instance_id: str, command: str) -> dict[str, Any]:
    submitted = run_instance_command(instance_id, command, timeout_seconds=60)
    command_id = submitted.get('command_id')
    detail: dict[str, Any] = submitted
    for _attempt in range(60):
        if not command_id:
            break
        detail = instance_command_detail(instance_id, command_id)
        if detail.get('status') not in {'Pending', 'InProgress', 'Delayed'}:
            break
        time.sleep(0.5)
    return detail


def _probe() -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Send requests to the load balancer from an InService instance; return (invocation, [(release, instance)])."""
    balancer = _load_balancer()
    instances = _in_service(_asg())
    if not balancer or not instances:
        return {'status': 'Failed', 'stderr': f'{LOAD_BALANCER_NAME} or InService instances of {ASG_NAME} not found.'}, []
    source = instances[0]['InstanceId']
    invocation = _run_in_instance(source, f"{PROBE_SCRIPT} {balancer['DNSName']} {PROBE_REQUESTS}")
    invocation = {**invocation, 'source_instance': source, 'load_balancer_dns': balancer['DNSName']}
    return invocation, RESPONSE_PATTERN.findall(invocation.get('stdout') or '')


def _check_traffic(release: str) -> tuple[bool, str, dict[str, Any]]:
    invocation, answers = _probe()
    if invocation.get('status') != 'Success':
        return False, f"The request command failed: {invocation.get('stderr') or invocation.get('status')}", invocation
    if len(answers) < PROBE_REQUESTS:
        return False, f'Only {len(answers)} of {PROBE_REQUESTS} requests through {LOAD_BALANCER_NAME} were answered.', invocation
    releases = {item[0] for item in answers}
    served_by = {item[1] for item in answers}
    healthy = {item['InstanceId'] for item in _in_service(_asg())}
    if releases != {release}:
        return False, f'Expected every answer from {release}; got {sorted(releases)}.', invocation
    if not healthy <= served_by:
        return False, (
            f'Requests reached {sorted(served_by)}, but not every instance of {ASG_NAME} ({sorted(healthy)}) answered.'
        ), invocation
    counts = {instance: sum(1 for item in answers if item[1] == instance) for instance in sorted(served_by)}
    spread = ', '.join(f'{instance} {count}' for instance, count in counts.items())
    return True, (
        f'{PROBE_REQUESTS} requests to {invocation["load_balancer_dns"]} were all answered by {release}, '
        f'spread across the instances: {spread}.'
    ), invocation


# ── Steps ──────────────────────────────────────────────────────────────────────

def _tag(resource_type: str, name: str) -> list[dict[str, Any]]:
    return [{'ResourceType': resource_type, 'Tags': [{'Key': 'Name', 'Value': name}]}]


def step_create_network() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    vpc = _vpc() or ec2.create_vpc(CidrBlock=VPC_CIDR, TagSpecifications=_tag('vpc', VPC_NAME))['Vpc']
    vpc_id = vpc['VpcId']
    existing = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', [])
    subnet_ids = []
    for spec in SUBNETS:
        subnet = _by_name_tag(existing, spec['name']) or ec2.create_subnet(
            VpcId=vpc_id, CidrBlock=spec['cidr'], AvailabilityZone=spec['az'], TagSpecifications=_tag('subnet', spec['name']),
        )['Subnet']
        subnet_ids.append(subnet['SubnetId'])
    gateways = ec2.describe_internet_gateways(Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]).get('InternetGateways', [])
    if gateways:
        igw_id = gateways[0]['InternetGatewayId']
    else:
        igw_id = ec2.create_internet_gateway(TagSpecifications=_tag('internet-gateway', IGW_NAME))['InternetGateway']['InternetGatewayId']
        ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    tables = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('RouteTables', [])
    table = _by_name_tag(tables, ROUTE_TABLE_NAME) or ec2.create_route_table(
        VpcId=vpc_id, TagSpecifications=_tag('route-table', ROUTE_TABLE_NAME),
    )['RouteTable']
    table_id = table['RouteTableId']
    if not any(route.get('DestinationCidrBlock') == '0.0.0.0/0' for route in table.get('Routes') or []):
        ec2.create_route(RouteTableId=table_id, DestinationCidrBlock='0.0.0.0/0', GatewayId=igw_id)
    associated = {item.get('SubnetId') for item in table.get('Associations') or []}
    for subnet_id in subnet_ids:
        if subnet_id not in associated:
            ec2.associate_route_table(RouteTableId=table_id, SubnetId=subnet_id)
    response = {'VpcId': vpc_id, 'SubnetIds': subnet_ids, 'InternetGatewayId': igw_id, 'RouteTableId': table_id}
    verified, message = _check_network()
    return _result('create-network', CREATE_NETWORK_COMMAND, response, verified, message, started)


def step_create_security_groups() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _vpc()
    if not vpc:
        return _result('create-security-groups', CREATE_SECURITY_GROUPS_COMMAND, {}, False,
                       f'VPC {VPC_NAME} not found. Run the first step.', started)
    ec2 = _client('ec2')
    vpc_id = vpc['VpcId']

    def ensure(name: str, description: str) -> dict[str, Any]:
        group = _security_group(vpc_id, name)
        if group:
            return group
        group_id = ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc_id)['GroupId']
        return {'GroupId': group_id, 'GroupName': name, 'IpPermissions': []}

    def authorize(group_id: str, permission: dict[str, Any]) -> None:
        try:
            ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=[permission])
        except ClientError as exc:
            if _error_code(exc) != 'InvalidPermission.Duplicate':
                raise

    alb_group = ensure(ALB_SG_NAME, 'Load balancer')
    web_group = ensure(WEB_SG_NAME, 'Web servers')
    authorize(alb_group['GroupId'], {
        'IpProtocol': 'tcp', 'FromPort': LISTENER_PORT, 'ToPort': LISTENER_PORT,
        'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'HTTP from anywhere'}],
    })
    authorize(web_group['GroupId'], {
        'IpProtocol': 'tcp', 'FromPort': WEB_PORT, 'ToPort': WEB_PORT,
        'UserIdGroupPairs': [{'GroupId': alb_group['GroupId'], 'Description': 'From the load balancer'}],
    })
    response = {'LoadBalancerGroupId': alb_group['GroupId'], 'InstanceGroupId': web_group['GroupId']}
    verified, message = _check_security_groups()
    return _result('create-security-groups', CREATE_SECURITY_GROUPS_COMMAND, response, verified, message, started)


def step_create_launch_template() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _vpc()
    web_group = _security_group(vpc['VpcId'], WEB_SG_NAME) if vpc else None
    if not web_group:
        return _result('create-launch-template', CREATE_LAUNCH_TEMPLATE_COMMAND, {}, False,
                       f'Security group {WEB_SG_NAME} not found. Run the earlier steps.', started)
    template = _launch_template()
    if template:
        response = {'LaunchTemplate': template}
    else:
        response = _client('ec2').create_launch_template(
            LaunchTemplateName=LAUNCH_TEMPLATE_NAME,
            VersionDescription='v1',
            LaunchTemplateData={
                'ImageId': IMAGE_ID,
                'InstanceType': INSTANCE_TYPE,
                'SecurityGroupIds': [web_group['GroupId']],
                'UserData': base64.b64encode(USER_DATA_V1.encode()).decode(),
                'TagSpecifications': _tag('instance', 'lab-ha-web'),
            },
        )
    verified, message = _check_launch_template()
    command = CREATE_LAUNCH_TEMPLATE_COMMAND.replace('<instance-sg-id>', web_group['GroupId'])
    return _result('create-launch-template', command, response, verified, message, started)


def step_create_target_group() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _vpc()
    if not vpc:
        return _result('create-target-group', CREATE_TARGET_GROUP_COMMAND, {}, False,
                       f'VPC {VPC_NAME} not found. Run the first step.', started)
    group = _target_group()
    if group:
        response = {'TargetGroups': [group]}
    else:
        response = _client('elbv2').create_target_group(
            Name=TARGET_GROUP_NAME,
            Protocol='HTTP',
            Port=WEB_PORT,
            VpcId=vpc['VpcId'],
            TargetType='instance',
            HealthCheckProtocol='HTTP',
            HealthCheckPath='/',
            HealthCheckIntervalSeconds=10,
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=2,
        )
    verified, message = _check_target_group()
    command = CREATE_TARGET_GROUP_COMMAND.replace('<vpc-id>', vpc['VpcId'])
    return _result('create-target-group', command, response, verified, message, started)


def step_create_load_balancer() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _vpc()
    subnets = _subnets(vpc['VpcId']) if vpc else []
    alb_group = _security_group(vpc['VpcId'], ALB_SG_NAME) if vpc else None
    target_group = _target_group()
    if len(subnets) < 2 or not alb_group or not target_group:
        return _result('create-load-balancer', CREATE_LOAD_BALANCER_COMMAND, {}, False,
                       'The subnets, the load balancer security group, and the target group must exist first.', started)
    elbv2 = _client('elbv2')
    balancer = _load_balancer() or elbv2.create_load_balancer(
        Name=LOAD_BALANCER_NAME,
        Type='application',
        Scheme='internet-facing',
        Subnets=[subnet['SubnetId'] for subnet in subnets],
        SecurityGroups=[alb_group['GroupId']],
    )['LoadBalancers'][0]
    listener = next((item for item in _listeners(balancer['LoadBalancerArn']) if item.get('Port') == LISTENER_PORT), None)
    if not listener:
        listener = elbv2.create_listener(
            LoadBalancerArn=balancer['LoadBalancerArn'],
            Protocol='HTTP',
            Port=LISTENER_PORT,
            DefaultActions=[{'Type': 'forward', 'TargetGroupArn': target_group['TargetGroupArn']}],
        )['Listeners'][0]
    verified, message = _wait(_check_load_balancer, 30, interval=1.0)
    response = {'LoadBalancer': balancer, 'Listener': listener}
    return _result('create-load-balancer', CREATE_LOAD_BALANCER_COMMAND, response, verified, message, started)


def step_create_auto_scaling_group() -> dict[str, Any]:
    started = time.perf_counter()
    vpc = _vpc()
    subnets = _subnets(vpc['VpcId']) if vpc else []
    target_group = _target_group()
    if len(subnets) < 2 or not target_group or not _launch_template():
        return _result('create-auto-scaling-group', CREATE_ASG_COMMAND, {}, False,
                       'The subnets, the launch template, and the target group must exist first.', started)
    group = _asg()
    if group:
        response = {'AutoScalingGroups': [group]}
    else:
        response = _client('autoscaling').create_auto_scaling_group(
            AutoScalingGroupName=ASG_NAME,
            LaunchTemplate={'LaunchTemplateName': LAUNCH_TEMPLATE_NAME, 'Version': '$Latest'},
            MinSize=MIN_SIZE,
            MaxSize=MAX_SIZE,
            DesiredCapacity=MIN_SIZE,
            VPCZoneIdentifier=','.join(subnet['SubnetId'] for subnet in subnets),
            TargetGroupARNs=[target_group['TargetGroupArn']],
            HealthCheckType='ELB',
            HealthCheckGracePeriod=GRACE_PERIOD_SECONDS,
        )
    verified, message = _wait(_check_asg, 90)
    return _result('create-auto-scaling-group', CREATE_ASG_COMMAND, response, verified, message, started)


def step_wait_healthy_targets() -> dict[str, Any]:
    started = time.perf_counter()
    target_group = _target_group()
    if not target_group or not _asg():
        return _result('wait-healthy-targets', TARGET_HEALTH_COMMAND, {}, False,
                       f'{TARGET_GROUP_NAME} or {ASG_NAME} not found. Run the earlier steps.', started)
    expected = max(MIN_SIZE, (_asg() or {}).get('DesiredCapacity') or MIN_SIZE)
    verified, message = _wait(lambda: _check_healthy_targets(expected), HEALTHY_WAIT_SECONDS)
    response = _client('elbv2').describe_target_health(TargetGroupArn=target_group['TargetGroupArn'])
    command = TARGET_HEALTH_COMMAND.replace('<target-group-arn>', target_group['TargetGroupArn'])
    return _result('wait-healthy-targets', command, response, verified, message, started)


def step_test_load_balancer() -> dict[str, Any]:
    started = time.perf_counter()
    verified, message, invocation = _check_traffic(_current_release())
    command = PROBE_COMMAND
    if invocation.get('source_instance'):
        command = command.replace('<instance-id>', invocation['source_instance'])
    if invocation.get('load_balancer_dns'):
        command = command.replace('<alb-dns-name>', invocation['load_balancer_dns'])
    return _result('test-load-balancer', command, invocation, verified, message, started)


def _current_release() -> str:
    return V2_BODY if _marked('instance-refresh') else V1_BODY


def _policy() -> dict[str, Any] | None:
    policies = _client('autoscaling').describe_policies(AutoScalingGroupName=ASG_NAME, PolicyNames=[POLICY_NAME]).get('ScalingPolicies', [])
    return policies[0] if policies else None


def _check_policy() -> tuple[bool, str]:
    policy = _policy()
    if not policy:
        return False, f'Scaling policy {POLICY_NAME} not found on {ASG_NAME}.'
    config = policy.get('TargetTrackingConfiguration') or {}
    metric = (config.get('PredefinedMetricSpecification') or {}).get('PredefinedMetricType')
    if policy.get('PolicyType') != 'TargetTrackingScaling' or metric != 'ASGAverageCPUUtilization' or config.get('TargetValue') != 50.0:
        return False, f'{POLICY_NAME} is not a target tracking policy for average CPU at 50 percent.'
    return True, f'{POLICY_NAME} tracks ASGAverageCPUUtilization at 50 percent on {ASG_NAME}.'


def step_add_target_tracking_policy() -> dict[str, Any]:
    started = time.perf_counter()
    if not _asg():
        return _result('add-target-tracking-policy', SCALING_POLICY_COMMAND, {}, False,
                       f'{ASG_NAME} not found. Run the earlier steps.', started)
    response = _client('autoscaling').put_scaling_policy(
        AutoScalingGroupName=ASG_NAME,
        PolicyName=POLICY_NAME,
        PolicyType='TargetTrackingScaling',
        TargetTrackingConfiguration={
            'PredefinedMetricSpecification': {'PredefinedMetricType': 'ASGAverageCPUUtilization'},
            'TargetValue': 50.0,
        },
    )
    verified, message = _check_policy()
    return _result('add-target-tracking-policy', SCALING_POLICY_COMMAND, response, verified, message, started)


def step_scale_out() -> dict[str, Any]:
    started = time.perf_counter()
    if not _asg():
        return _result('scale-out', SCALE_OUT_COMMAND, {}, False, f'{ASG_NAME} not found. Run the earlier steps.', started)
    response = _client('autoscaling').set_desired_capacity(AutoScalingGroupName=ASG_NAME, DesiredCapacity=SCALED_CAPACITY)

    def check() -> tuple[bool, str]:
        group = _asg() or {}
        if group.get('DesiredCapacity') != SCALED_CAPACITY:
            return False, f"{ASG_NAME} desired capacity is {group.get('DesiredCapacity')}, not {SCALED_CAPACITY}."
        ok, message = _check_healthy_targets(SCALED_CAPACITY)
        if ok:
            zones = sorted({item.get('AvailabilityZone') for item in _in_service(group)})
            message = f"{ASG_NAME} scaled out to {SCALED_CAPACITY} healthy targets in {', '.join(zones)}. " + message
        return ok, message

    verified, message = _wait(check, HEALTHY_WAIT_SECONDS)
    return _result('scale-out', SCALE_OUT_COMMAND, response, verified, message, started)


def step_self_heal() -> dict[str, Any]:
    started = time.perf_counter()
    group = _asg()
    instances = _in_service(group)
    if not group or not instances:
        return _result('self-heal', SELF_HEAL_COMMAND, {}, False, f'{ASG_NAME} has no InService instances.', started)
    desired = group.get('DesiredCapacity') or MIN_SIZE
    victim = instances[-1]['InstanceId']
    response = _client('ec2').terminate_instances(InstanceIds=[victim])
    command = SELF_HEAL_COMMAND.replace('<one-asg-instance-id>', victim)

    def check() -> tuple[bool, str]:
        current = _asg() or {}
        ids = {item['InstanceId'] for item in current.get('Instances') or []}
        if victim in ids:
            return False, f'{ASG_NAME} still lists {victim}.'
        ok, message = _check_healthy_targets(desired)
        if ok:
            replacements = sorted({item['InstanceId'] for item in _in_service(current)} - {item['InstanceId'] for item in instances})
            message = (
                f'{victim} was terminated; {ASG_NAME} launched {", ".join(replacements) or "a replacement"} and is back '
                f'to {desired} healthy targets. ' + message
            )
        return ok, message

    verified, message = _wait(check, HEALTHY_WAIT_SECONDS)
    return _result('self-heal', command, response, verified, message, started)


def _v2_version() -> dict[str, Any] | None:
    return next((v for v in _launch_template_versions() if V2_BODY in _decoded_user_data(v)), None)


def _latest_refresh() -> dict[str, Any] | None:
    refreshes = _client('autoscaling').describe_instance_refreshes(AutoScalingGroupName=ASG_NAME).get('InstanceRefreshes', [])
    return max(refreshes, key=lambda item: str(item.get('StartTime') or ''), default=None)


def _check_refreshed() -> tuple[bool, str]:
    version = _v2_version()
    if not version:
        return False, f'{LAUNCH_TEMPLATE_NAME} has no version with the {V2_BODY} user data.'
    number = str(version.get('VersionNumber'))
    refresh = _latest_refresh()
    if not refresh or refresh.get('Status') != 'Successful':
        return False, f"The instance refresh is {(refresh or {}).get('Status') or 'not started'}."
    group = _asg() or {}
    instances = _in_service(group)
    old = [item['InstanceId'] for item in instances if str((item.get('LaunchTemplate') or {}).get('Version')) != number]
    if old:
        return False, f'Instances {old} still run an older launch template version.'
    ok, message = _check_healthy_targets(group.get('DesiredCapacity') or MIN_SIZE)
    if not ok:
        return ok, message
    return True, f'Every instance of {ASG_NAME} runs launch template version {number} and is healthy.'


def step_instance_refresh() -> dict[str, Any]:
    started = time.perf_counter()
    if not _asg() or not _launch_template():
        return _result('instance-refresh', INSTANCE_REFRESH_COMMAND, {}, False,
                       f'{ASG_NAME} or {LAUNCH_TEMPLATE_NAME} not found. Run the earlier steps.', started)
    ec2 = _client('ec2')
    autoscaling = _client('autoscaling')
    version = _v2_version()
    if not version:
        version = ec2.create_launch_template_version(
            LaunchTemplateName=LAUNCH_TEMPLATE_NAME,
            SourceVersion='1',
            VersionDescription=V2_DESCRIPTION,
            LaunchTemplateData={'UserData': base64.b64encode(USER_DATA_V2.encode()).decode()},
        )['LaunchTemplateVersion']
    try:
        refresh = autoscaling.start_instance_refresh(
            AutoScalingGroupName=ASG_NAME,
            Preferences={'MinHealthyPercentage': 50, 'InstanceWarmup': 30},
        )
    except ClientError as exc:
        if _error_code(exc) != 'InstanceRefreshInProgress':
            raise
        refresh = {'InstanceRefreshInProgress': True}
    verified, message = _wait(_check_refreshed, REFRESH_WAIT_SECONDS)
    invocation: dict[str, Any] = {}
    if verified:
        _marker('instance-refresh')
        verified, traffic_message, invocation = _check_traffic(V2_BODY)
        message = f'{message} {traffic_message}'
    response = {
        'LaunchTemplateVersion': {key: version.get(key) for key in ('VersionNumber', 'VersionDescription')},
        'InstanceRefresh': refresh,
        'Refresh': _latest_refresh(),
        'Requests': invocation,
    }
    return _result('instance-refresh', INSTANCE_REFRESH_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-network': step_create_network,
    'create-security-groups': step_create_security_groups,
    'create-launch-template': step_create_launch_template,
    'create-target-group': step_create_target_group,
    'create-load-balancer': step_create_load_balancer,
    'create-auto-scaling-group': step_create_auto_scaling_group,
    'wait-healthy-targets': step_wait_healthy_targets,
    'test-load-balancer': step_test_load_balancer,
    'add-target-tracking-policy': step_add_target_tracking_policy,
    'scale-out': step_scale_out,
    'self-heal': step_self_heal,
    'instance-refresh': step_instance_refresh,
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if lab_key != LAB_KEY:
        raise ValueError(f'Unknown Elastic Load Balancing lab: {lab_key}')
    runner = RUNNERS.get(step_key)
    if not runner:
        raise ValueError(f'Unknown step {step_key} for lab {lab_key}')
    return runner()


# ── Status and reset ───────────────────────────────────────────────────────────

def _safe(check) -> bool:
    try:
        return bool(check())
    except (ClientError, BotoCoreError):
        return False


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    """Live state where it lasts; steps whose effect later steps change also need their marker."""
    if lab_key != LAB_KEY:
        raise ValueError(f'Unknown Elastic Load Balancing lab: {lab_key}')
    done = lambda step: bool(_marked(step))  # noqa: E731
    asg_ok = lambda: _check_asg_config()[0]  # noqa: E731
    checks = {
        'create-network': lambda: _check_network()[0],
        'create-security-groups': lambda: _check_security_groups()[0],
        'create-launch-template': lambda: _check_launch_template()[0],
        'create-target-group': lambda: _check_target_group()[0],
        'create-load-balancer': lambda: _check_load_balancer()[0],
        'create-auto-scaling-group': asg_ok,
        # Scaling, replacement, and refresh change the instance set, so these count once passed while the group lasts.
        'wait-healthy-targets': lambda: done('wait-healthy-targets') and asg_ok(),
        'test-load-balancer': lambda: done('test-load-balancer') and asg_ok() and _load_balancer(),
        'add-target-tracking-policy': lambda: _check_policy()[0],
        'scale-out': lambda: done('scale-out') and (_asg() or {}).get('DesiredCapacity') == SCALED_CAPACITY,
        'self-heal': lambda: done('self-heal') and asg_ok(),
        'instance-refresh': lambda: done('instance-refresh') and _check_refreshed()[0],
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


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if lab_key != LAB_KEY:
        raise ValueError(f'Unknown Elastic Load Balancing lab: {lab_key}')
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call, missing_codes: set[str] = frozenset(), retry_codes: set[str] = frozenset()) -> None:
        for _try in range(15):
            try:
                call()
                deleted.append(label)
                return
            except ClientError as exc:
                code = _error_code(exc)
                if code in missing_codes or 'NotFound' in code:
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
    elbv2 = _client('elbv2')
    autoscaling = _client('autoscaling')

    if _safe(_asg):
        attempt(f'auto-scaling-group:{ASG_NAME}',
                lambda: autoscaling.delete_auto_scaling_group(AutoScalingGroupName=ASG_NAME, ForceDelete=True),
                {'ValidationError'})
        if not _wait_until(lambda: _asg() is None, 30):
            errors.append(f'auto-scaling-group:{ASG_NAME}: still reported after delete')

    balancer = _load_balancer() if _safe(_load_balancer) else None
    if balancer:
        attempt(f'load-balancer:{LOAD_BALANCER_NAME}',
                lambda: elbv2.delete_load_balancer(LoadBalancerArn=balancer['LoadBalancerArn']))
        _wait_until(lambda: _load_balancer() is None, 15)
    target_group = _target_group() if _safe(_target_group) else None
    if target_group:
        attempt(f'target-group:{TARGET_GROUP_NAME}',
                lambda: elbv2.delete_target_group(TargetGroupArn=target_group['TargetGroupArn']),
                retry_codes={'ResourceInUse'})
    if _safe(_launch_template):
        attempt(f'launch-template:{LAUNCH_TEMPLATE_NAME}',
                lambda: ec2.delete_launch_template(LaunchTemplateName=LAUNCH_TEMPLATE_NAME))

    vpc = _vpc() if _safe(_vpc) else None
    if vpc:
        vpc_id = vpc['VpcId']
        # Instances the group launched must be gone before their security groups and subnets can go.
        def lab_instances_gone() -> bool:
            reservations = ec2.describe_instances(Filters=[
                {'Name': 'vpc-id', 'Values': [vpc_id]},
                {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped', 'shutting-down']},
            ]).get('Reservations', [])
            leftovers = [item['InstanceId'] for reservation in reservations for item in reservation.get('Instances', [])]
            if leftovers:
                ec2.terminate_instances(InstanceIds=leftovers)
            return not leftovers
        if not _wait_until(lab_instances_gone, 30):
            errors.append(f'{VPC_NAME}: instances still running')
        web_group = _security_group(vpc_id, WEB_SG_NAME)
        alb_group = _security_group(vpc_id, ALB_SG_NAME)
        for group in (web_group, alb_group):
            if group:
                attempt(f"security-group:{group['GroupName']}",
                        lambda group=group: ec2.delete_security_group(GroupId=group['GroupId']),
                        retry_codes={'DependencyViolation', 'InvalidGroup.InUse'})
        tables = ec2.describe_route_tables(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('RouteTables', [])
        table = _by_name_tag(tables, ROUTE_TABLE_NAME)
        if table:
            for association in table.get('Associations') or []:
                if association.get('RouteTableAssociationId') and not association.get('Main'):
                    attempt(f"route-table-association:{association['RouteTableAssociationId']}",
                            lambda association=association: ec2.disassociate_route_table(
                                AssociationId=association['RouteTableAssociationId']))
            attempt(f'route-table:{ROUTE_TABLE_NAME}', lambda: ec2.delete_route_table(RouteTableId=table['RouteTableId']),
                    retry_codes={'DependencyViolation'})
        gateways = ec2.describe_internet_gateways(Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}]).get('InternetGateways', [])
        for gateway in gateways:
            gateway_id = gateway['InternetGatewayId']
            attempt(f'internet-gateway-attachment:{gateway_id}',
                    lambda gateway_id=gateway_id: ec2.detach_internet_gateway(InternetGatewayId=gateway_id, VpcId=vpc_id))
            attempt(f'internet-gateway:{gateway_id}',
                    lambda gateway_id=gateway_id: ec2.delete_internet_gateway(InternetGatewayId=gateway_id))
        for subnet in _subnets(vpc_id):
            attempt(f"subnet:{subnet['SubnetId']}", lambda subnet=subnet: ec2.delete_subnet(SubnetId=subnet['SubnetId']),
                    retry_codes={'DependencyViolation'})
        attempt(f'vpc:{VPC_NAME}', lambda: ec2.delete_vpc(VpcId=vpc_id), retry_codes={'DependencyViolation'})

    cache.delete_many([CACHE_PREFIX + step['key'] for step in HA_WEB_TIER_LAB['steps']])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': f'aws autoscaling delete-auto-scaling-group --auto-scaling-group-name {ASG_NAME} --force-delete ... # cleanup',
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
            'message': 'Some lab resources could not be deleted: ' + '; '.join(errors) if errors else 'HA web tier lab resources cleaned up.',
        },
    }
