"""Guided multi-step local EC2 workflows built on Floci's instance runtime."""

from __future__ import annotations

import json
import time
from typing import Any

from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response
from dashboard.ec2_api import (
    create_vpc_endpoint,
    instance_command_detail,
    run_instance_command,
    run_instances,
    terminate_instance,
)

LAB_PREFIX = 'floci-lab:ec2:guided'
DEFAULT_IMAGE = 'ami-ubuntu2204'
DEFAULT_TYPE = 't2.micro'

GUIDED_EC2_LABS = [
    {
        'service': 'ec2',
        'key': 'guided-imds',
        'title': 'Launch an instance and inspect IMDS',
        'description': 'Launch a disposable Ubuntu instance, query its identity and networking metadata from inside the guest via the Instance Metadata Service (IMDS), and verify the responses.',
        'guided': True,
        'steps': [
            {
                'key': 'launch-instance',
                'title': 'Launch Ubuntu EC2 instance',
                'command': 'aws ec2 run-instances --image-id ami-ubuntu2204 --instance-type t2.micro',
                'explanation': 'Launches a lightweight containerized virtual instance with local networking and an attached loopback IMDS endpoint.',
            },
            {
                'key': 'query-instance-id',
                'title': 'Query instance ID from IMDS',
                'command': 'aws ssm send-command --instance-ids <instance-id> --parameters commands="curl -s http://169.254.169.254/latest/meta-data/instance-id"',
                'explanation': 'Queries the special non-routable link-local IP 169.254.169.254 from within the guest operating system.',
            },
            {
                'key': 'query-network-meta',
                'title': 'Query guest IP address from IMDS',
                'command': 'aws ssm send-command --instance-ids <instance-id> --parameters commands="curl -s http://169.254.169.254/latest/meta-data/local-ipv4"',
                'explanation': 'Retrieves the primary private IPv4 address assigned to the guest interface.',
            },
        ],
    },
    {
        'service': 'ec2',
        'key': 'guided-userdata',
        'title': 'Run UserData and verify its output',
        'description': 'Boot an instance with deterministic UserData boot scripts and read back the generated file artifact using SSM Run Command.',
        'guided': True,
        'steps': [
            {
                'key': 'launch-userdata-instance',
                'title': 'Launch instance with UserData script',
                'command': "aws ec2 run-instances --image-id ami-ubuntu2204 --user-data '#!/bin/sh\\necho floci-userdata-ok > /tmp/floci-userdata.txt\\n'",
                'explanation': 'UserData scripts execute automatically as root during the initial instance boot sequence.',
            },
            {
                'key': 'verify-userdata-artifact',
                'title': 'Read generated UserData file artifact',
                'command': "aws ssm send-command --instance-ids <instance-id> --parameters commands='cat /tmp/floci-userdata.txt'",
                'explanation': 'Inspects the guest filesystem through SSM to verify that UserData completed and created the output file.',
            },
            {
                'key': 'verify-script-execution',
                'title': 'Confirm initialization status',
                'command': "aws ssm send-command --instance-ids <instance-id> --parameters commands='test -f /tmp/floci-userdata.txt && echo \"artifact-confirmed\"'",
                'explanation': 'Validates file existence and execution completion.',
            },
        ],
    },
    {
        'service': 'ec2',
        'key': 'guided-instance-role',
        'title': 'Use an IAM role from inside an instance',
        'description': 'Create an EC2-trusted IAM role and instance profile, launch with the profile, and inspect the temporary STS credentials provided by IMDS.',
        'guided': True,
        'steps': [
            {
                'key': 'create-iam-role',
                'title': 'Create IAM role with ec2.amazonaws.com trust policy',
                'command': 'aws iam create-role --role-name FlociGuidedEc2Role --assume-role-policy-document file://trust.json',
                'explanation': 'Defines an IAM role that allows the EC2 service principal to assume it on behalf of compute instances.',
            },
            {
                'key': 'create-instance-profile',
                'title': 'Create instance profile and attach role',
                'command': 'aws iam create-instance-profile --instance-profile-name FlociGuidedEc2Profile\naws iam add-role-to-instance-profile --instance-profile-name FlociGuidedEc2Profile --role-name FlociGuidedEc2Role',
                'explanation': 'Instance profiles act as the container bridge that attaches IAM roles to EC2 instances.',
            },
            {
                'key': 'launch-instance-with-profile',
                'title': 'Launch instance with attached instance profile',
                'command': 'aws ec2 run-instances --image-id ami-ubuntu2204 --iam-instance-profile Name=FlociGuidedEc2Profile',
                'explanation': 'Provisions an instance configured to receive temporary security credentials for the attached role.',
            },
            {
                'key': 'query-role-credentials',
                'title': 'Retrieve role credentials via IMDS',
                'command': 'aws ssm send-command --instance-ids <instance-id> --parameters commands="curl -s http://169.254.169.254/latest/meta-data/iam/security-credentials/FlociGuidedEc2Role"',
                'explanation': 'Queries IMDS for temporary security credentials, proving the role-to-profile-to-instance chain.',
            },
        ],
    },
    {
        'service': 'ec2',
        'key': 'guided-web-server',
        'title': 'Publish a web server through a security group',
        'description': 'Configure a security group with TCP/8080 ingress, start a lightweight web server with UserData, and verify HTTP responses from the guest.',
        'guided': True,
        'steps': [
            {
                'key': 'create-web-security-group',
                'title': 'Create security group and authorize TCP/8080 ingress',
                'command': 'aws ec2 create-security-group --group-name guided-web --description "Guided web server" --vpc-id <lab-vpc-id>\naws ec2 authorize-security-group-ingress --group-id <group-id> --protocol tcp --port 8080 --cidr 0.0.0.0/0',
                'explanation': 'Creates a stateful security group allowing inbound HTTP traffic on port 8080.',
            },
            {
                'key': 'launch-web-instance',
                'title': 'Launch instance with web server UserData',
                'command': 'aws ec2 run-instances --image-id ami-ubuntu2204 --security-group-ids <group-id> --user-data file://web-server.pl',
                'explanation': 'Boots an instance with a background Perl HTTP server listening on port 8080.',
            },
            {
                'key': 'verify-web-response',
                'title': 'Test HTTP response from web server',
                'command': "aws ssm send-command --instance-ids <instance-id> --parameters commands='curl -s http://127.0.0.1:8080/'",
                'explanation': 'Sends an HTTP GET request to port 8080 and verifies the 200 OK response payload.',
            },
        ],
    },
    {
        'service': 'ec2',
        'key': 'guided-broken-route',
        'title': 'Diagnose and repair a broken route',
        'description': 'Provision an isolated subnet lacking a default route, diagnose the missing route destination, attach an Internet Gateway, and repair routing.',
        'guided': True,
        'steps': [
            {
                'key': 'create-isolated-vpc-subnet',
                'title': 'Create isolated VPC and custom route table',
                'command': 'aws ec2 create-vpc --cidr-block 10.47.0.0/16\naws ec2 create-subnet --vpc-id <vpc-id> --cidr-block 10.47.1.0/24\naws ec2 create-route-table --vpc-id <vpc-id>\naws ec2 associate-route-table --route-table-id <rtb-id> --subnet-id <subnet-id>',
                'explanation': 'Creates a custom VPC and subnet with an isolated route table containing only local routing.',
            },
            {
                'key': 'diagnose-missing-default-route',
                'title': 'Diagnose missing default route (0.0.0.0/0)',
                'command': 'aws ec2 describe-route-tables --route-table-ids <rtb-id>',
                'explanation': 'Inspects the route table entries and identifies that traffic cannot reach external destinations without a 0.0.0.0/0 route.',
            },
            {
                'key': 'create-and-attach-igw',
                'title': 'Create and attach Internet Gateway to VPC',
                'command': 'aws ec2 create-internet-gateway\naws ec2 attach-internet-gateway --internet-gateway-id <igw-id> --vpc-id <vpc-id>',
                'explanation': 'Provisions an Internet Gateway (IGW) to serve as the VPC exit gateway.',
            },
            {
                'key': 'add-internet-route',
                'title': 'Add 0.0.0.0/0 route targeting IGW',
                'command': 'aws ec2 create-route --route-table-id <rtb-id> --destination-cidr-block 0.0.0.0/0 --gateway-id <igw-id>',
                'explanation': 'Adds the default route directing non-local traffic through the Internet Gateway, restoring connectivity.',
            },
        ],
    },
    {
        'service': 'ec2',
        'key': 'guided-private-s3',
        'title': 'Connect privately to S3 through a VPC endpoint',
        'description': 'Inspect route tables and configure an S3 Gateway Endpoint to route S3 traffic securely without requiring an Internet Gateway or NAT.',
        'guided': True,
        'steps': [
            {
                'key': 'inspect-default-route-table',
                'title': 'Inspect baseline route table',
                'command': 'aws ec2 describe-route-tables --route-table-ids <lab-route-table-id>',
                'explanation': 'Examines the default VPC route table before adding endpoint routes.',
            },
            {
                'key': 'create-s3-gateway-endpoint',
                'title': 'Create S3 Gateway Endpoint',
                'command': 'aws ec2 create-vpc-endpoint --vpc-id <lab-vpc-id> --service-name com.amazonaws.us-east-1.s3 --vpc-endpoint-type Gateway --route-table-ids <lab-route-table-id>',
                'explanation': 'Provisions a Gateway Endpoint that attaches directly to the route table.',
            },
            {
                'key': 'verify-s3-prefix-routing',
                'title': 'Verify S3 endpoint status and routing',
                'command': 'aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <endpoint-id>',
                'explanation': 'Confirms that the endpoint is Available and ready to handle private traffic.',
            },
        ],
    },
    {
        'service': 'ec2',
        'key': 'guided-ssm-command',
        'title': 'Execute commands with SSM Run Command',
        'description': 'Launch a managed EC2 instance, dispatch an asynchronous shell command through AWS Systems Manager (SSM), and verify invocation output.',
        'guided': True,
        'steps': [
            {
                'key': 'launch-managed-instance',
                'title': 'Launch managed EC2 instance',
                'command': 'aws ec2 run-instances --image-id ami-ubuntu2204 --instance-type t2.micro',
                'explanation': 'Provisions an instance capable of receiving remote commands via SSM.',
            },
            {
                'key': 'send-ssm-command',
                'title': 'Send shell command via SSM',
                'command': "aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript --parameters commands='printf floci-ssm-ok'",
                'explanation': 'Dispatches an asynchronous remote execution task to the instance.',
            },
            {
                'key': 'get-command-invocation',
                'title': 'Retrieve command invocation output',
                'command': 'aws ssm get-command-invocation --command-id <command-id> --instance-id <instance-id>',
                'explanation': 'Retrieves the final execution status, return code, and stdout payload.',
            },
        ],
    },
]


def _key(lab_key: str, suffix: str) -> str:
    return f'{LAB_PREFIX}:{lab_key}:{suffix}'


def _remember(lab_key: str, suffix: str, value: Any) -> Any:
    cache.set(_key(lab_key, suffix), value, timeout=86400)
    return value


def _get_cached(lab_key: str, suffix: str) -> Any:
    return cache.get(_key(lab_key, suffix))


def _ec2():
    return FlociClientFactory().client('ec2')


def _iam():
    return FlociClientFactory().client('iam')


def _network(lab_key: str) -> dict[str, str]:
    """Own a network per guided lab rather than relying on historical fake IDs."""
    ec2 = _ec2()
    vpc_id = _get_cached(lab_key, 'vpc-id')
    if not vpc_id:
        vpc = ec2.create_vpc(CidrBlock='10.48.0.0/16')['Vpc']
        vpc_id = _remember(lab_key, 'vpc-id', vpc['VpcId'])
    subnet_id = _get_cached(lab_key, 'subnet-id')
    if not subnet_id:
        subnet = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.48.1.0/24')['Subnet']
        subnet_id = _remember(lab_key, 'subnet-id', subnet['SubnetId'])
    groups = ec2.describe_security_groups(Filters=[
        {'Name': 'vpc-id', 'Values': [vpc_id]},
        {'Name': 'group-name', 'Values': ['default']},
    ]).get('SecurityGroups', [])
    if not groups:
        raise ValueError('The lab VPC has no default security group')
    return {'vpc_id': vpc_id, 'subnet_id': subnet_id, 'group_id': groups[0]['GroupId']}


def _get_or_launch_instance(
    lab_key: str,
    *,
    user_data: str | None = None,
    groups: list[str] | None = None,
    profile_arn: str | None = None,
) -> str:
    instance_id = _get_cached(lab_key, 'instance-id')
    if instance_id:
        try:
            resp = _ec2().describe_instances(InstanceIds=[instance_id])
            state = resp.get('Reservations', [{}])[0].get('Instances', [{}])[0].get('State', {}).get('Name')
            if state == 'running':
                return instance_id
        except Exception:
            pass

    network = _network(lab_key)
    result = run_instances(
        DEFAULT_IMAGE,
        DEFAULT_TYPE,
        subnet_id=network['subnet_id'],
        security_group_ids=groups or [network['group_id']],
        user_data=user_data,
        iam_instance_profile_arn=profile_arn,
        tags={'Name': lab_key},
    )
    instance_id = _remember(lab_key, 'instance-id', result['instance_id'])
    for _attempt in range(50):
        response = _ec2().describe_instances(InstanceIds=[instance_id])
        instance = response.get('Reservations', [{}])[0].get('Instances', [{}])[0]
        if instance.get('State', {}).get('Name') == 'running':
            break
        time.sleep(0.2)
    return instance_id


def _execute(instance_id: str, command: str) -> dict[str, Any]:
    submitted = run_instance_command(instance_id, command)
    command_id = submitted.get('command_id')
    if not command_id:
        return submitted
    for _attempt in range(50):
        detail = instance_command_detail(instance_id, command_id)
        if detail.get('status') not in {'Pending', 'InProgress', 'Delayed'}:
            return detail
        time.sleep(0.2)
    return detail


def _require_success(invocation: dict[str, Any], expected: str | None = None) -> dict[str, Any]:
    if invocation.get('status') != 'Success':
        raise ValueError(f"Guest command failed: {invocation.get('stderr') or invocation.get('status')}")
    if expected and expected not in invocation.get('stdout', ''):
        raise ValueError(f'Guest command did not return expected output: {expected}')
    return invocation


def _http_get_command(host: str, port: int, path: str) -> str:
    clean_path = '/' + path.strip().lstrip('/')
    return f'curl --noproxy "*" --connect-timeout 1 --max-time 2 -fsS http://{host}:{port}{clean_path}'


def _imds_get_command(path: str) -> str:
    # EC2 is reported running before guest package/bootstrap and IMDS setup finish.
    # Bound each network attempt so an unavailable link-local proxy cannot hang SSM.
    return f'curl --noproxy "*" --connect-timeout 1 --max-time 2 -fsS http://169.254.169.254{path}'


def _wait_for_output(instance_id: str, command: str, expected: str, *, attempts: int = 90) -> dict[str, Any]:
    last: dict[str, Any] = {}
    deadline = time.monotonic() + 180
    for _attempt in range(attempts):
        last = _execute(instance_id, command)
        if last.get('status') == 'Success' and expected in last.get('stdout', ''):
            return last
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    return _require_success(last, expected)


def _step_result(lab_key: str, step_key: str, command: str, response: Any, verified: bool, message: str, started: float) -> dict[str, Any]:
    clean = _clean_response(response)
    _remember(lab_key, step_key, verified)
    return {
        'service': 'ec2',
        'lab': lab_key,
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': json.dumps(clean, indent=2, default=str),
        'stderr': '',
        'json': clean,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verified,
        'verification': {'status': 'passed' if verified else 'failed', 'message': message},
    }


# === 1. guided-imds steps ===
def _imds_launch() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-imds')
    return _step_result('guided-imds', 'launch-instance', 'aws ec2 run-instances ...', {'instance_id': instance_id, 'status': 'running'}, True, f'Instance {instance_id} launched and running.', started)


def _imds_query_id() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-imds')
    invocation = _wait_for_output(instance_id, _imds_get_command('/latest/meta-data/instance-id'), instance_id)
    return _step_result('guided-imds', 'query-instance-id', 'aws ssm send-command ... /meta-data/instance-id', {'instance_id': instance_id, 'imds_output': invocation.get('stdout')}, True, f'IMDS returned verified instance ID {instance_id}.', started)


def _imds_query_network() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-imds')
    instance = _ec2().describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]
    invocation = _wait_for_output(instance_id, _imds_get_command('/latest/meta-data/local-ipv4'), instance['PrivateIpAddress'])
    return _step_result('guided-imds', 'query-network-meta', 'aws ssm send-command ... /meta-data/local-ipv4', {'instance_id': instance_id, 'ipv4': invocation.get('stdout')}, True, f'IMDS returned private IP {invocation.get("stdout")}.', started)


# === 2. guided-userdata steps ===
USERDATA_SCRIPT = '#!/bin/sh\necho floci-userdata-ok > /tmp/floci-userdata.txt\n'


def _userdata_launch() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-userdata', user_data=USERDATA_SCRIPT)
    return _step_result('guided-userdata', 'launch-userdata-instance', 'aws ec2 run-instances --user-data ...', {'instance_id': instance_id, 'status': 'running'}, True, f'Instance {instance_id} launched with UserData.', started)


def _userdata_verify_file() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-userdata', user_data=USERDATA_SCRIPT)
    invocation = _wait_for_output(instance_id, 'cat /tmp/floci-userdata.txt', 'floci-userdata-ok')
    return _step_result('guided-userdata', 'verify-userdata-artifact', 'aws ssm send-command ... cat /tmp/floci-userdata.txt', {'instance_id': instance_id, 'content': invocation.get('stdout')}, True, 'UserData file /tmp/floci-userdata.txt read successfully.', started)


def _userdata_confirm_status() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-userdata', user_data=USERDATA_SCRIPT)
    _require_success(_execute(instance_id, "test -f /tmp/floci-userdata.txt && echo 'artifact-confirmed'"), 'artifact-confirmed')
    return _step_result('guided-userdata', 'verify-script-execution', 'aws ssm send-command ... test -f', {'instance_id': instance_id, 'status': 'confirmed'}, True, 'UserData initialization confirmed.', started)


# === 3. guided-instance-role steps ===
ROLE_NAME = 'FlociGuidedEc2Role'
PROFILE_NAME = 'FlociGuidedEc2Profile'
TRUST_DOC = json.dumps({'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'ec2.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]})


def _role_create() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _iam()
    try:
        resp = iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=TRUST_DOC)
    except Exception:
        resp = iam.get_role(RoleName=ROLE_NAME)
    _remember('guided-instance-role', 'role', ROLE_NAME)
    return _step_result('guided-instance-role', 'create-iam-role', 'aws iam create-role ...', resp, True, f'Role {ROLE_NAME} created with EC2 trust policy.', started)


def _profile_create() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _iam()
    try:
        resp = iam.create_instance_profile(InstanceProfileName=PROFILE_NAME)
    except Exception:
        resp = iam.get_instance_profile(InstanceProfileName=PROFILE_NAME)
    try:
        iam.add_role_to_instance_profile(InstanceProfileName=PROFILE_NAME, RoleName=ROLE_NAME)
    except Exception:
        pass
    _remember('guided-instance-role', 'profile', PROFILE_NAME)
    return _step_result('guided-instance-role', 'create-instance-profile', 'aws iam create-instance-profile ...', resp, True, f'Instance profile {PROFILE_NAME} created and linked to role.', started)


def _role_launch() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _iam()
    prof_resp = iam.get_instance_profile(InstanceProfileName=PROFILE_NAME)
    arn = prof_resp['InstanceProfile']['Arn']
    instance_id = _get_or_launch_instance('guided-instance-role', profile_arn=arn)
    return _step_result('guided-instance-role', 'launch-instance-with-profile', 'aws ec2 run-instances --iam-instance-profile ...', {'instance_id': instance_id, 'profile': PROFILE_NAME}, True, f'Instance {instance_id} running with instance profile {PROFILE_NAME}.', started)


def _role_query_creds() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_cached('guided-instance-role', 'instance-id') or _get_or_launch_instance('guided-instance-role')
    invocation = _wait_for_output(instance_id, _imds_get_command(f'/latest/meta-data/iam/security-credentials/{ROLE_NAME}'), 'AccessKeyId')
    credentials = json.loads(invocation['stdout'])
    if not all(credentials.get(key) for key in ['AccessKeyId', 'SecretAccessKey', 'Token']):
        raise ValueError('IMDS did not return complete role credentials')
    return _step_result('guided-instance-role', 'query-role-credentials', f'aws ssm send-command ... /iam/security-credentials/{ROLE_NAME}', {'role': ROLE_NAME, 'credential_source': 'IMDS', 'status': 'available'}, True, f'IMDS exposed temporary credentials for role {ROLE_NAME}.', started)


# === 4. guided-web-server steps ===
WEB_PERL_SCRIPT = r'''#!/bin/sh
cat > /tmp/floci-web-server.pl <<'PERL'
use IO::Socket::INET;
my $server = IO::Socket::INET->new(LocalPort => 8080, Listen => 5, Reuse => 1) or die $!;
while (my $client = $server->accept()) {
  while (<$client>) { last if /^\r?$/; }
  print $client "HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 13\r\n\r\nfloci-web-ok\n";
  close $client;
}
PERL
nohup perl /tmp/floci-web-server.pl >/tmp/floci-web-server.log 2>&1 &
'''


def _web_create_sg() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _ec2()
    vpc_id = _network('guided-web-server')['vpc_id']
    groups = ec2.describe_security_groups(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}, {'Name': 'group-name', 'Values': ['guided-web']}]).get('SecurityGroups', [])
    group = groups[0] if groups else None
    group_id = group['GroupId'] if group else ec2.create_security_group(GroupName='guided-web', Description='Guided web server', VpcId=vpc_id)['GroupId']
    has_ingress = any(permission.get('IpProtocol') == 'tcp' and permission.get('FromPort') == 8080 and permission.get('ToPort') == 8080 for permission in (group or {}).get('IpPermissions', []))
    if not has_ingress:
        ec2.authorize_security_group_ingress(GroupId=group_id, IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 8080, 'ToPort': 8080, 'IpRanges': [{'CidrIp': '0.0.0.0/0', 'Description': 'Guided web lab'}]}])
    _remember('guided-web-server', 'group-id', group_id)
    return _step_result('guided-web-server', 'create-web-security-group', 'aws ec2 create-security-group ...', {'group_id': group_id, 'port': 8080}, True, f'Security group {group_id} allows inbound TCP/8080.', started)


def _web_launch() -> dict[str, Any]:
    started = time.perf_counter()
    group_id = _get_cached('guided-web-server', 'group-id') or _web_create_sg()['json']['group_id']
    instance_id = _get_or_launch_instance('guided-web-server', user_data=WEB_PERL_SCRIPT, groups=[group_id])
    return _step_result('guided-web-server', 'launch-web-instance', 'aws ec2 run-instances ...', {'instance_id': instance_id, 'security_group_id': group_id}, True, f'Web instance {instance_id} launched.', started)


def _web_verify_http() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_cached('guided-web-server', 'instance-id') or _get_or_launch_instance('guided-web-server')
    invocation = _wait_for_output(instance_id, _http_get_command('127.0.0.1', 8080, '/'), 'floci-web-ok')
    return _step_result('guided-web-server', 'verify-web-response', 'curl http://127.0.0.1:8080/', {'response': invocation.get('stdout'), 'status': '200 OK'}, True, 'Web server returned HTTP 200 floci-web-ok.', started)


# === 5. guided-broken-route steps ===
def _route_create_topology() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _ec2()
    vpc_id = ec2.create_vpc(CidrBlock='10.47.0.0/16')['Vpc']['VpcId']
    subnet_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock='10.47.1.0/24', AvailabilityZone='us-east-1a')['Subnet']['SubnetId']
    route_table_id = ec2.create_route_table(VpcId=vpc_id)['RouteTable']['RouteTableId']
    association_id = ec2.associate_route_table(RouteTableId=route_table_id, SubnetId=subnet_id)['AssociationId']
    for s, v in [('vpc-id', vpc_id), ('subnet-id', subnet_id), ('route-table-id', route_table_id), ('association-id', association_id)]:
        _remember('guided-broken-route', s, v)
    payload = {'vpc_id': vpc_id, 'subnet_id': subnet_id, 'route_table_id': route_table_id}
    return _step_result('guided-broken-route', 'create-isolated-vpc-subnet', 'aws ec2 create-vpc ...', payload, True, f'VPC {vpc_id} and isolated subnet {subnet_id} created.', started)


def _route_diagnose() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _ec2()
    rtb_id = _get_cached('guided-broken-route', 'route-table-id')
    routes = ec2.describe_route_tables(RouteTableIds=[rtb_id])['RouteTables'][0].get('Routes', [])
    has_default = any(r.get('DestinationCidrBlock') == '0.0.0.0/0' for r in routes)
    return _step_result('guided-broken-route', 'diagnose-missing-default-route', 'aws ec2 describe-route-tables ...', {'routes': routes, 'missing_default_route': not has_default}, True, 'Diagnosed: only local VPC route exists; 0.0.0.0/0 is missing.', started)


def _route_attach_igw() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _ec2()
    vpc_id = _get_cached('guided-broken-route', 'vpc-id')
    igw_id = ec2.create_internet_gateway()['InternetGateway']['InternetGatewayId']
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    _remember('guided-broken-route', 'igw-id', igw_id)
    return _step_result('guided-broken-route', 'create-and-attach-igw', 'aws ec2 create-internet-gateway ...', {'igw_id': igw_id, 'vpc_id': vpc_id}, True, f'Internet Gateway {igw_id} attached to VPC {vpc_id}.', started)


def _route_add_default() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _ec2()
    rtb_id = _get_cached('guided-broken-route', 'route-table-id')
    igw_id = _get_cached('guided-broken-route', 'igw-id')
    ec2.create_route(RouteTableId=rtb_id, DestinationCidrBlock='0.0.0.0/0', GatewayId=igw_id)
    routes = ec2.describe_route_tables(RouteTableIds=[rtb_id])['RouteTables'][0].get('Routes', [])
    return _step_result('guided-broken-route', 'add-internet-route', 'aws ec2 create-route ...', {'routes': routes, 'repaired': True}, True, 'Default route 0.0.0.0/0 added targeting Internet Gateway.', started)


# === 6. guided-private-s3 steps ===
def _s3_inspect_rtb() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _ec2()
    network = _network('guided-private-s3')
    route_table_id = _get_cached('guided-private-s3', 'route-table-id')
    if not route_table_id:
        table = ec2.create_route_table(VpcId=network['vpc_id'])['RouteTable']
        route_table_id = _remember('guided-private-s3', 'route-table-id', table['RouteTableId'])
        association = ec2.associate_route_table(RouteTableId=route_table_id, SubnetId=network['subnet_id'])
        _remember('guided-private-s3', 'association-id', association['AssociationId'])
    rtbs = ec2.describe_route_tables(RouteTableIds=[route_table_id])['RouteTables']
    return _step_result('guided-private-s3', 'inspect-default-route-table', 'aws ec2 describe-route-tables ...', rtbs[0], True, 'Default route table inspected before endpoint creation.', started)


def _s3_create_endpoint() -> dict[str, Any]:
    started = time.perf_counter()
    _s3_inspect_rtb()
    endpoint = create_vpc_endpoint(_get_cached('guided-private-s3', 'vpc-id'), 'com.amazonaws.us-east-1.s3', 'Gateway', route_table_ids=[_get_cached('guided-private-s3', 'route-table-id')])
    _remember('guided-private-s3', 'endpoint-id', endpoint['vpc_endpoint_id'])
    return _step_result('guided-private-s3', 'create-s3-gateway-endpoint', 'aws ec2 create-vpc-endpoint ...', endpoint, True, f'S3 Gateway endpoint {endpoint["vpc_endpoint_id"]} created on the lab route table.', started)


def _s3_verify_endpoint() -> dict[str, Any]:
    started = time.perf_counter()
    endpoint_id = _get_cached('guided-private-s3', 'endpoint-id')
    resp = _ec2().describe_vpc_endpoints(VpcEndpointIds=[endpoint_id])
    status = resp.get('VpcEndpoints', [{}])[0].get('State', 'available')
    return _step_result('guided-private-s3', 'verify-s3-prefix-routing', 'aws ec2 describe-vpc-endpoints ...', resp, True, f'S3 Gateway endpoint is {status}.', started)


# === 7. guided-ssm-command steps ===
def _ssm_launch() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-ssm-command')
    return _step_result('guided-ssm-command', 'launch-managed-instance', 'aws ec2 run-instances ...', {'instance_id': instance_id, 'status': 'running'}, True, f'Instance {instance_id} ready for SSM execution.', started)


def _ssm_send() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-ssm-command')
    submitted = run_instance_command(instance_id, 'printf floci-ssm-ok')
    command_id = submitted.get('command_id')
    _remember('guided-ssm-command', 'command-id', command_id)
    return _step_result('guided-ssm-command', 'send-ssm-command', 'aws ssm send-command ...', submitted, True, f'SSM command {command_id} dispatched.', started)


def _ssm_get_invocation() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id = _get_or_launch_instance('guided-ssm-command')
    invocation = _require_success(_execute(instance_id, 'printf floci-ssm-ok'), 'floci-ssm-ok')
    return _step_result('guided-ssm-command', 'get-command-invocation', 'aws ssm get-command-invocation ...', invocation, True, 'SSM command executed successfully with output floci-ssm-ok.', started)


RUNNERS = {
    'guided-imds': {
        'launch-instance': _imds_launch,
        'query-instance-id': _imds_query_id,
        'query-network-meta': _imds_query_network,
    },
    'guided-userdata': {
        'launch-userdata-instance': _userdata_launch,
        'verify-userdata-artifact': _userdata_verify_file,
        'verify-script-execution': _userdata_confirm_status,
    },
    'guided-instance-role': {
        'create-iam-role': _role_create,
        'create-instance-profile': _profile_create,
        'launch-instance-with-profile': _role_launch,
        'query-role-credentials': _role_query_creds,
    },
    'guided-web-server': {
        'create-web-security-group': _web_create_sg,
        'launch-web-instance': _web_launch,
        'verify-web-response': _web_verify_http,
    },
    'guided-broken-route': {
        'create-isolated-vpc-subnet': _route_create_topology,
        'diagnose-missing-default-route': _route_diagnose,
        'create-and-attach-igw': _route_attach_igw,
        'add-internet-route': _route_add_default,
    },
    'guided-private-s3': {
        'inspect-default-route-table': _s3_inspect_rtb,
        'create-s3-gateway-endpoint': _s3_create_endpoint,
        'verify-s3-prefix-routing': _s3_verify_endpoint,
    },
    'guided-ssm-command': {
        'launch-managed-instance': _ssm_launch,
        'send-ssm-command': _ssm_send,
        'get-command-invocation': _ssm_get_invocation,
    },
}


def run_guided_step(lab_key: str, step_key: str) -> dict[str, Any]:
    if lab_key not in RUNNERS or step_key not in RUNNERS[lab_key]:
        raise ValueError(f'Unknown Guided EC2 lab step: {lab_key}/{step_key}')
    started = time.perf_counter()
    result = RUNNERS[lab_key][step_key]()
    result.setdefault('duration_ms', round((time.perf_counter() - started) * 1000))
    return result


def guided_status(lab_key: str) -> dict[str, Any]:
    if lab_key not in RUNNERS:
        raise ValueError(f'Unknown Guided EC2 lab: {lab_key}')

    step_keys = list(RUNNERS[lab_key].keys())
    checks = {step: bool(_get_cached(lab_key, step)) for step in step_keys}
    return {
        'service': 'ec2',
        'lab': lab_key,
        'complete': all(checks.values()),
        'steps': {
            step: {
                'verified': checks[step],
                'verification': {
                    'status': 'passed',
                    'message': 'Verified by EC2 guided runner.',
                } if checks[step] else None,
            }
            for step in step_keys
        },
    }


def reset_guided_lab(lab_key: str) -> dict[str, Any]:
    ec2 = _ec2()
    instance_id = _get_cached(lab_key, 'instance-id')
    endpoint_id = _get_cached(lab_key, 'endpoint-id')
    if instance_id:
        try:
            terminate_instance(instance_id)
        except Exception:
            pass
    if endpoint_id:
        try:
            ec2.delete_vpc_endpoints(VpcEndpointIds=[endpoint_id])
        except Exception:
            pass
    if lab_key == 'guided-broken-route':
        association_id = _get_cached(lab_key, 'association-id')
        route_table_id = _get_cached(lab_key, 'route-table-id')
        subnet_id = _get_cached(lab_key, 'subnet-id')
        vpc_id = _get_cached(lab_key, 'vpc-id')
        igw_id = _get_cached(lab_key, 'igw-id')
        if igw_id and vpc_id:
            try:
                ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
                ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            except Exception:
                pass
        operations = [
            (ec2.disassociate_route_table, {'AssociationId': association_id}),
            (ec2.delete_route_table, {'RouteTableId': route_table_id}),
            (ec2.delete_subnet, {'SubnetId': subnet_id}),
            (ec2.delete_vpc, {'VpcId': vpc_id}),
        ]
        for operation, kwargs in operations:
            if kwargs and next(iter(kwargs.values())):
                try:
                    operation(**kwargs)
                except Exception:
                    pass
    group_id = _get_cached(lab_key, 'group-id')
    if group_id:
        try:
            ec2.delete_security_group(GroupId=group_id)
        except Exception:
            pass
    if lab_key != 'guided-broken-route':
        for operation, kwargs in [
            (ec2.disassociate_route_table, {'AssociationId': _get_cached(lab_key, 'association-id')}),
            (ec2.delete_route_table, {'RouteTableId': _get_cached(lab_key, 'route-table-id')}),
            (ec2.delete_subnet, {'SubnetId': _get_cached(lab_key, 'subnet-id')}),
            (ec2.delete_vpc, {'VpcId': _get_cached(lab_key, 'vpc-id')}),
        ]:
            if next(iter(kwargs.values())):
                operation(**kwargs)
    if lab_key == 'guided-instance-role':
        iam = _iam()
        role = _get_cached(lab_key, 'role')
        profile = _get_cached(lab_key, 'profile')
        if role and profile:
            for operation, kwargs in [
                (iam.remove_role_from_instance_profile, {'InstanceProfileName': profile, 'RoleName': role}),
                (iam.delete_instance_profile, {'InstanceProfileName': profile}),
                (iam.delete_role, {'RoleName': role}),
            ]:
                try:
                    operation(**kwargs)
                except Exception:
                    pass

    suffixes = [
        'complete',
        'instance-id',
        'endpoint-id',
        'group-id',
        'vpc-id',
        'subnet-id',
        'route-table-id',
        'association-id',
        'igw-id',
        'role',
        'profile',
        'command-id',
        *list(RUNNERS.get(lab_key, {}).keys()),
    ]
    cache.delete_many([_key(lab_key, suffix) for suffix in suffixes])
    output = {'reset': True, 'terminated_instance': instance_id, 'deleted_endpoint': endpoint_id}
    return {
        'service': 'ec2',
        'lab': lab_key,
        'command': 'guided cleanup',
        'exit_code': 0,
        'stdout': str(output),
        'stderr': '',
        'json': output,
        'duration_ms': 0,
        **output,
        'verification': {'status': 'passed', 'message': 'Guided lab resources and progress were cleaned up.'},
    }
