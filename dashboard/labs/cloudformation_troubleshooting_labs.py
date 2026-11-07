"""Provisioning troubleshooting lab: a deployer denied by IAM and fixed, a stack that fails on an invalid property and
has to be deleted from ROLLBACK_COMPLETE, an app stack that fails on a misspelled export, an Auto Scaling group stuck
below its desired capacity in a full subnet and fixed with a second subnet, an AMI copied to another Region, a subnet
shared with another account through AWS RAM, and termination protection on the network stack.

Floci 2.4.0 enforces IAM on the CloudFormation API, fails a stack when a resource's API call fails (and records the
reason in its events), refuses to update a stack in ROLLBACK_COMPLETE, resolves exports and fails on a missing one,
counts the free addresses in a subnet (network interfaces and instances use them, and Auto Scaling stops when they run
out), copies AMIs between Regions, stores RAM shares and their associations, and enforces termination protection.
What Floci does not do: it does not check a template's property names or most values before calling the service, it
creates the stack's resources without checking the caller's or the service role's permissions for them, it lets a
stack whose export is imported be deleted or its export changed, it has no ListImports, ModifyImageAttribute or
validation for AWS::EC2::Subnet::Id parameters, and RAM shares do not make the subnet visible to another account.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudformation'
LAB_KEY = 'deployment-troubleshooting'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
COPY_REGION = 'us-west-2'
ACCOUNT = '000000000000'
SHARED_ACCOUNT = '111122223333'

DEPLOYER = 'lab-deploy-ci'
DEPLOYER_POLICY = 'lab-deploy'
CHECK_STACK = 'lab-deploy-access-check'
OPS_STACK = 'lab-deploy-ops'
NETWORK_STACK = 'lab-deploy-network'
APP_STACK = 'lab-deploy-app'
OPS_QUEUE = 'lab-deploy-ops-events'
BAD_RETENTION = 30
GOOD_RETENTION = 345600
VPC_EXPORT = 'lab-deploy-vpc-id'
TYPO_EXPORT = 'lab-deploy-vpcid'
VPC_CIDR = '10.61.0.0/16'
SUBNET_A_CIDR = '10.61.1.0/28'
SUBNET_B_CIDR = '10.61.2.0/26'
AZ_A, AZ_B = 'us-east-1a', 'us-east-1b'
OTHER_WORKLOAD_ENIS = 9
GROUP = 'lab-deploy-web'
TEMPLATE = 'lab-deploy-web'
DESIRED = 4
IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't3.nano'
AMI_NAME = 'lab-deploy-web-golden'
SHARE = 'lab-deploy-shared-subnets'
APP_TAG = 'lab-deploy'
STACK_WAIT_SECONDS = 120
GROUP_WAIT_SECONDS = 120
FLOCI = 'How Floci emulates this: '

QUEUE_RESOURCE = {'Type': 'AWS::SQS::Queue', 'Properties': {'QueueName': OPS_QUEUE}}
CHECK_TEMPLATE = {'Resources': {'Marker': {'Type': 'AWS::SSM::Parameter', 'Properties': {
    'Name': '/lab-deploy/access-check', 'Type': 'String', 'Value': 'deployer can deploy'}}}}


def ops_template(retention: int) -> dict[str, Any]:
    queue = json.loads(json.dumps(QUEUE_RESOURCE))
    queue['Properties']['MessageRetentionPeriod'] = retention
    return {'Description': 'Operations queue', 'Resources': {'Events': queue},
            'Outputs': {'EventsQueueArn': {'Value': {'Fn::GetAtt': ['Events', 'Arn']}}}}


def network_template(second_subnet: bool) -> dict[str, Any]:
    resources: dict[str, Any] = {
        'Vpc': {'Type': 'AWS::EC2::VPC', 'Properties': {'CidrBlock': VPC_CIDR}},
        'SubnetA': {'Type': 'AWS::EC2::Subnet', 'Properties': {
            'VpcId': {'Ref': 'Vpc'}, 'CidrBlock': SUBNET_A_CIDR, 'AvailabilityZone': AZ_A}},
    }
    outputs: dict[str, Any] = {
        'VpcId': {'Value': {'Ref': 'Vpc'}, 'Export': {'Name': VPC_EXPORT}},
        'SubnetA': {'Value': {'Ref': 'SubnetA'}},
    }
    if second_subnet:
        resources['SubnetB'] = {'Type': 'AWS::EC2::Subnet', 'Properties': {
            'VpcId': {'Ref': 'Vpc'}, 'CidrBlock': SUBNET_B_CIDR, 'AvailabilityZone': AZ_B}}
        outputs['SubnetB'] = {'Value': {'Ref': 'SubnetB'}}
    return {'Description': 'App network', 'Resources': resources, 'Outputs': outputs}


def app_template(export_name: str) -> dict[str, Any]:
    return {
        'Description': 'Web tier',
        'Parameters': {'Subnets': {'Type': 'CommaDelimitedList'}},
        'Resources': {
            'WebSecurityGroup': {'Type': 'AWS::EC2::SecurityGroup', 'Properties': {
                'GroupDescription': 'web tier', 'VpcId': {'Fn::ImportValue': export_name}}},
            'WebTemplate': {'Type': 'AWS::EC2::LaunchTemplate', 'Properties': {
                'LaunchTemplateName': TEMPLATE,
                'LaunchTemplateData': {'ImageId': IMAGE_ID, 'InstanceType': INSTANCE_TYPE}}},
            'WebGroup': {'Type': 'AWS::AutoScaling::AutoScalingGroup', 'Properties': {
                'AutoScalingGroupName': GROUP, 'MinSize': '0', 'MaxSize': str(DESIRED + 2),
                'DesiredCapacity': str(DESIRED),
                'LaunchTemplate': {'LaunchTemplateId': {'Ref': 'WebTemplate'},
                                   'Version': {'Fn::GetAtt': ['WebTemplate', 'LatestVersionNumber']}},
                'VPCZoneIdentifier': {'Ref': 'Subnets'},
                'Tags': [{'Key': 'App', 'Value': APP_TAG, 'PropagateAtLaunch': True}]}},
        },
    }


def deployer_policy(can_create: bool) -> dict[str, Any]:
    actions = ['cloudformation:DescribeStacks', 'cloudformation:DescribeStackEvents', 'cloudformation:DeleteStack']
    if can_create:
        actions += ['cloudformation:CreateStack', 'cloudformation:UpdateStack', 'cloudformation:TagResource']
    return {'Version': '2012-10-17', 'Statement': [
        # In AWS, scope this to arn:aws:cloudformation:<region>:<account>:stack/lab-deploy-*/*. Floci matches
        # CloudFormation calls only against "*".
        {'Effect': 'Allow', 'Action': actions, 'Resource': '*'},
        {'Effect': 'Allow', 'Action': ['ssm:PutParameter', 'ssm:DeleteParameter'],
         'Resource': f'arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/lab-deploy/*'}]}


# ── Pure helpers ─────────────────────────────────────────────────────────────

def first_failure(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first resource that failed, reading events oldest first; later failures are usually the rollback."""
    ordered = sorted(events, key=lambda item: str(item.get('Timestamp')))
    for event in ordered:
        if str(event.get('ResourceStatus', '')).endswith('_FAILED') and event.get('ResourceStatusReason') \
                and event.get('LogicalResourceId') != event.get('StackName'):
            return {'resource': event['LogicalResourceId'], 'status': event['ResourceStatus'],
                    'reason': event['ResourceStatusReason']}
    return None


def usable_addresses(cidr: str) -> int:
    """AWS keeps five addresses in every subnet: network, router, DNS, future use, and broadcast."""
    prefix = int(cidr.rsplit('/', 1)[1])
    return 2 ** (32 - prefix) - 5


def capacity_diagnosis(desired: int, running: int, free: dict[str, int]) -> str:
    if running >= desired:
        return f'{running} of {desired} instances are running.'
    full = [subnet for subnet, count in free.items() if count == 0]
    if full and len(full) == len(free):
        return (f'{running} of {desired} instances: every subnet in the group is out of addresses '
                f'({", ".join(full)}). Add a subnet or use a larger one.')
    return f'{running} of {desired} instances; the subnets still have {sum(free.values())} free addresses.'


# ── Commands shown in the console ───────────────────────────────────────────

ACCESS_COMMAND = (
    f'aws iam create-user --user-name {DEPLOYER}\n'
    f'aws iam put-user-policy --user-name {DEPLOYER} --policy-name {DEPLOYER_POLICY} '
    '--policy-document file://describe-only.json\n'
    f'AWS_PROFILE={DEPLOYER} aws cloudformation create-stack --stack-name {CHECK_STACK} '
    '--template-body file://check.json   # AccessDenied\n'
    f'aws iam simulate-principal-policy --policy-source-arn arn:aws:iam::{ACCOUNT}:user/{DEPLOYER} '
    '--action-names cloudformation:CreateStack\n'
    f'aws iam put-user-policy --user-name {DEPLOYER} --policy-name {DEPLOYER_POLICY} '
    '--policy-document file://deployer.json\n'
    f'AWS_PROFILE={DEPLOYER} aws cloudformation create-stack --stack-name {CHECK_STACK} ...'
)
ROLLBACK_COMMAND = (
    f'aws cloudformation create-stack --stack-name {OPS_STACK} --template-body file://ops.json '
    f'# MessageRetentionPeriod: {BAD_RETENTION}\n'
    f'aws cloudformation describe-stack-events --stack-name {OPS_STACK} '
    '--query "StackEvents[?ends_with(ResourceStatus, \'_FAILED\')]"\n'
    f'aws cloudformation update-stack --stack-name {OPS_STACK} ...   # refused in ROLLBACK_COMPLETE\n'
    f'aws cloudformation delete-stack --stack-name {OPS_STACK}\n'
    f'aws cloudformation create-stack --stack-name {OPS_STACK} ... # MessageRetentionPeriod: {GOOD_RETENTION}'
)
EXPORT_COMMAND = (
    f'aws cloudformation create-stack --stack-name {NETWORK_STACK} --template-body file://network.json\n'
    f'aws cloudformation create-stack --stack-name {APP_STACK} --template-body file://app.json '
    '--parameters ParameterKey=Subnets,ParameterValue=<subnet-a>\n'
    f'aws cloudformation describe-stack-events --stack-name {APP_STACK}   # No export named {TYPO_EXPORT}\n'
    'aws cloudformation list-exports\n'
    f'aws cloudformation delete-stack --stack-name {APP_STACK}\n'
    f'aws cloudformation create-stack --stack-name {APP_STACK} ... # Fn::ImportValue {VPC_EXPORT}'
)
CAPACITY_COMMAND = (
    f'aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names {GROUP}\n'
    f'aws autoscaling describe-scaling-activities --auto-scaling-group-name {GROUP}\n'
    'aws ec2 describe-subnets --subnet-ids <subnet-a> --query "Subnets[0].AvailableIpAddressCount"\n'
    'aws ec2 describe-network-interfaces --filters Name=subnet-id,Values=<subnet-a>\n'
    f'aws cloudformation update-stack --stack-name {NETWORK_STACK} ...   # adds SubnetB {SUBNET_B_CIDR}\n'
    f'aws cloudformation update-stack --stack-name {APP_STACK} --use-previous-template '
    '--parameters ParameterKey=Subnets,ParameterValue=\\"<subnet-a>,<subnet-b>\\"'
)
AMI_COMMAND = (
    f'aws ec2 create-image --instance-id <web-instance> --name {AMI_NAME}-<timestamp> --no-reboot\n'
    f'aws ec2 copy-image --source-image-id <ami> --source-region {REGION} --region {COPY_REGION} --name <same name>\n'
    f'aws ec2 describe-images --image-ids <copy> --region {COPY_REGION}\n'
    f'aws ec2 modify-image-attribute --image-id <ami> --launch-permission "Add=[{{UserId={SHARED_ACCOUNT}}}]"'
)
SHARE_COMMAND = (
    f'aws ram create-resource-share --name {SHARE} --resource-arns arn:aws:ec2:{REGION}:{ACCOUNT}:subnet/<subnet-b> '
    f'--principals {SHARED_ACCOUNT} --no-allow-external-principals\n'
    'aws ram get-resource-share-associations --association-type PRINCIPAL\n'
    'aws ram get-resource-share-associations --association-type RESOURCE'
)
PROTECT_COMMAND = (
    f'aws cloudformation update-termination-protection --stack-name {NETWORK_STACK} '
    '--enable-termination-protection\n'
    f'aws cloudformation delete-stack --stack-name {NETWORK_STACK}   # refused\n'
    'aws cloudformation list-exports'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Troubleshoot deployments: denied deploys, failed stacks, full subnets, AMI copies, and shared subnets',
    'description': (
        'Fix a deployer that IAM denies, read a failed stack\'s events and recover it from ROLLBACK_COMPLETE, find a '
        'misspelled export, get an Auto Scaling group out of a full subnet, copy an AMI to another Region, share a '
        'subnet with another account through AWS RAM, and protect the network stack from deletion.'
    ),
    'steps': [
        {
            'key': 'deployer-access',
            'title': f'Fix {DEPLOYER}, which IAM denies cloudformation:CreateStack',
            'command': ACCESS_COMMAND,
            'explanation': (
                f'The CI user {DEPLOYER} may describe stacks but not create them, so its first deploy fails with '
                'AccessDenied. The policy simulator confirms which action is missing, and a policy that adds '
                'CreateStack (and TagResource, since the stack is created with tags) fixes it; in AWS you would scope it to arn:aws:cloudformation:<region>:<account>:'
                'stack/lab-deploy-*/*. Without a service role, CloudFormation also needs the caller\'s '
                'permission for every resource in the template; with one (--role-arn), the caller needs only '
                'iam:PassRole for that role, which is how you give CI narrow rights. '
                + FLOCI + 'IAM enforcement denies and then allows the CreateStack call for real. Floci matches '
                'CloudFormation calls only against Resource "*", so the lab\'s policy uses that. Floci creates the '
                'stack\'s resources without checking the caller\'s or the role\'s permissions for them.'
            ),
        },
        {
            'key': 'failed-stack',
            'title': 'Read the failed stack\'s events and recover from ROLLBACK_COMPLETE',
            'command': ROLLBACK_COMMAND,
            'explanation': (
                f'{OPS_STACK} sets the queue\'s MessageRetentionPeriod to {BAD_RETENTION} seconds, below the minimum of '
                '60. SQS refuses it, the queue is CREATE_FAILED, and the stack rolls back to ROLLBACK_COMPLETE. The '
                'root cause is the first failed event, oldest first. A stack that failed its first create cannot be '
                'updated, only deleted and created again, so the fix is delete, correct, recreate. Turning off '
                'rollback (--disable-rollback) keeps the failed resources for debugging. '
                + FLOCI + 'the failure, its reason, the rollback, and the refused update are real. Floci does not '
                'check property names or most values itself, so only values the service refuses fail.'
            ),
        },
        {
            'key': 'missing-export',
            'title': f'Find the misspelled export {TYPO_EXPORT}',
            'command': EXPORT_COMMAND,
            'explanation': (
                f'{NETWORK_STACK} creates the VPC and a /28 subnet and exports the VPC ID as {VPC_EXPORT}. The first '
                f'{APP_STACK} imports {TYPO_EXPORT}, so its security group fails with "No export named". ListExports '
                'shows the real name; the app stack is deleted and created with it. Exports are per Region and '
                'account, and a stack cannot delete or change an export another stack imports. '
                + FLOCI + 'the import, the failure, and ListExports are real. Floci lets an imported export be '
                'changed or its stack deleted, which AWS refuses.'
            ),
        },
        {
            'key': 'subnet-capacity',
            'title': f'Get {GROUP} out of a full subnet',
            'command': CAPACITY_COMMAND,
            'explanation': (
                f'{GROUP} wants {DESIRED} instances but has fewer. A /28 has {usable_addresses(SUBNET_A_CIDR)} usable '
                f'addresses (AWS keeps five), and {OTHER_WORKLOAD_ENIS} are taken by other workloads\' network '
                'interfaces, so the subnet runs out. AvailableIpAddressCount and the network interfaces show it. A '
                f'second subnet ({SUBNET_B_CIDR}, {usable_addresses(SUBNET_B_CIDR)} addresses) in another '
                'Availability Zone is added to the network stack and to the group, which then reaches its desired '
                'capacity across two zones. '
                + FLOCI + 'the address count, the interfaces, the group stopping when addresses run out, and the '
                'failed scaling activity with its reason are real.'
            ),
        },
        {
            'key': 'copy-ami',
            'title': f'Copy the web AMI to {COPY_REGION}',
            'command': AMI_COMMAND,
            'explanation': (
                f'An AMI belongs to one Region. {AMI_NAME} is made from a web instance and copied to {COPY_REGION} for '
                'disaster recovery or a second deployment; the copy has a new ID, so templates look it up per Region '
                '(a mapping or an SSM parameter). Sharing an AMI with another account adds a launch permission; an '
                'encrypted AMI also needs the KMS key shared. '
                + FLOCI + 'CreateImage and CopyImage are real. ModifyImageAttribute is not supported, so the lab '
                'explains the launch permission.'
            ),
        },
        {
            'key': 'share-subnet',
            'title': f'Share the new subnet with account {SHARED_ACCOUNT} through AWS RAM',
            'command': SHARE_COMMAND,
            'explanation': (
                f'AWS RAM shares subnets so another account ({SHARED_ACCOUNT}) can launch into this VPC while the '
                'network team keeps owning it. Inside an organization, turn on sharing with AWS Organizations and '
                'share with an OU; external accounts must accept an invitation. The participant sees the subnet but '
                'cannot change it or the VPC. '
                + FLOCI + 'the share and its principal and resource associations are stored. Floci has one account, '
                'so the subnet does not appear anywhere else.'
            ),
        },
        {
            'key': 'protect-network',
            'title': f'Turn on termination protection for {NETWORK_STACK}',
            'command': PROTECT_COMMAND,
            'explanation': (
                f'The app depends on {NETWORK_STACK}, so termination protection makes DeleteStack fail until someone '
                'turns it off on purpose. Stack policies protect individual resources from updates, and '
                'DeletionPolicy: Retain keeps a resource when its stack goes. '
                + FLOCI + 'termination protection is enforced for real.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str, region: str | None = None, identity: dict[str, Any] | None = None):
    factory = FlociClientFactory(identity=identity) if identity else FlociClientFactory()
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


def _safe_call(call, default=None):
    try:
        return call()
    except (ClientError, BotoCoreError, KeyError, IndexError):
        return default


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Floci lookups ────────────────────────────────────────────────────────────

def _stack(name: str) -> dict[str, Any] | None:
    stacks = _safe_call(lambda: _client('cloudformation').describe_stacks(StackName=name)['Stacks'], [])
    live = [item for item in stacks if item.get('StackStatus') != 'DELETE_COMPLETE']
    return live[0] if live else None


def _settled(name: str) -> dict[str, Any] | None:
    _wait(lambda: not str((_stack(name) or {}).get('StackStatus', '')).endswith('_IN_PROGRESS'),
          STACK_WAIT_SECONDS, delay=2)
    return _stack(name)


def _events(name: str) -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('cloudformation').describe_stack_events(StackName=name)['StackEvents'], [])


def _outputs(name: str) -> dict[str, str]:
    return {item['OutputKey']: item['OutputValue'] for item in (_stack(name) or {}).get('Outputs') or []}


def _exports() -> dict[str, str]:
    exports = _safe_call(lambda: _client('cloudformation').list_exports()['Exports'], [])
    return {item['Name']: item['Value'] for item in exports}


def _delete_stack(name: str) -> None:
    """Delete a stack; one stuck in DELETE_FAILED is deleted again, keeping the resources that would not go."""
    cloudformation = _client('cloudformation')
    for _ in range(2):
        stack = _stack(name)
        if not stack:
            return
        if stack.get('StackStatus') == 'DELETE_FAILED':
            failed = sorted({event['LogicalResourceId'] for event in _events(name)
                             if event.get('ResourceStatus') == 'DELETE_FAILED' and event['LogicalResourceId'] != name})
            if name == APP_STACK and 'WebGroup' in failed and not _group():
                _placeholder_group()
            cloudformation.delete_stack(StackName=name, RetainResources=failed)
        else:
            cloudformation.delete_stack(StackName=name)
        _wait(lambda: (_stack(name) or {}).get('StackStatus') in (None, 'DELETE_FAILED'), STACK_WAIT_SECONDS)


def _placeholder_group() -> None:
    """Floci fails to delete a stack whose Auto Scaling group is already gone (AWS treats it as deleted) and ignores
    RetainResources, so an empty group with the same name lets the stack delete finish."""
    ec2 = _client('ec2')
    name = f'{TEMPLATE}-placeholder'
    if not _safe_call(lambda: ec2.describe_launch_templates(LaunchTemplateNames=[name])['LaunchTemplates']):
        ec2.create_launch_template(LaunchTemplateName=name,
                                   LaunchTemplateData={'ImageId': IMAGE_ID, 'InstanceType': INSTANCE_TYPE})
    _client('autoscaling').create_auto_scaling_group(
        AutoScalingGroupName=GROUP, MinSize=0, MaxSize=0, DesiredCapacity=0,
        LaunchTemplate={'LaunchTemplateName': name}, AvailabilityZones=[AZ_A])


def _group() -> dict[str, Any] | None:
    groups = _safe_call(lambda: _client('autoscaling').describe_auto_scaling_groups(
        AutoScalingGroupNames=[GROUP])['AutoScalingGroups'], [])
    return groups[0] if groups else None


def _running(group: dict[str, Any] | None) -> int:
    return sum(1 for item in (group or {}).get('Instances') or []
               if not str(item.get('LifecycleState', '')).startswith('Terminat'))


def _free_addresses(subnet_ids: list[str]) -> dict[str, int]:
    subnets = _safe_call(lambda: _client('ec2').describe_subnets(SubnetIds=subnet_ids)['Subnets'], [])
    return {item['SubnetId']: item.get('AvailableIpAddressCount', 0) for item in subnets}


def _enis(subnet_id: str) -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('ec2').describe_network_interfaces(Filters=[
        {'Name': 'subnet-id', 'Values': [subnet_id]}])['NetworkInterfaces'], [])


def _live_instances(subnet_ids: list[str]) -> list[dict[str, Any]]:
    if not subnet_ids:
        return []
    reservations = _safe_call(lambda: _client('ec2').describe_instances(Filters=[
        {'Name': 'subnet-id', 'Values': subnet_ids},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'shutting-down', 'stopping', 'stopped']},
    ])['Reservations'], [])
    return [item for reservation in reservations for item in reservation.get('Instances', [])]


def _workload_enis(subnet_id: str) -> list[dict[str, Any]]:
    return [item for item in _enis(subnet_id) if item.get('Description') == 'other workload']


def _image(region: str = REGION) -> dict[str, Any] | None:
    """The lab's AMI; its name carries a timestamp because AMI names must be unique."""
    images = _safe_call(lambda: _client('ec2', region).describe_images(Owners=['self'])['Images'], [])
    live = [item for item in images if item.get('State') != 'deregistered'
            and str(item.get('Name', '')).startswith(AMI_NAME)]
    return live[0] if live else None


def _share() -> dict[str, Any] | None:
    shares = _safe_call(lambda: _client('ram').get_resource_shares(resourceOwner='SELF', name=SHARE)
                        ['resourceShares'], [])
    live = [item for item in shares if item.get('status') not in ('DELETED', 'DELETING')]
    return live[0] if live else None


def _share_associations(kind: str) -> list[dict[str, Any]]:
    share = _share()
    if not share:
        return []
    return _safe_call(lambda: _client('ram').get_resource_share_associations(
        associationType=kind, resourceShareArns=[share['resourceShareArn']])['resourceShareAssociations'], [])


def _deployer_identity() -> dict[str, Any] | None:
    return (_marked('deployer-access') or {}).get('identity')


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_access() -> tuple[bool, str]:
    marker = _marked('deployer-access') or {}
    if marker.get('deniedCode') not in ('AccessDenied', 'AccessDeniedException'):
        return False, f'{DEPLOYER} should first be denied cloudformation:CreateStack.'
    if not marker.get('created'):
        return False, f'{DEPLOYER} has not deployed {CHECK_STACK} with the fixed policy.'
    policy = _safe_call(lambda: _client('iam').get_user_policy(UserName=DEPLOYER, PolicyName=DEPLOYER_POLICY)
                        ['PolicyDocument'])
    if isinstance(policy, str):
        policy = json.loads(policy)
    actions = [action for statement in (policy or {}).get('Statement', []) for action in statement['Action']]
    if 'cloudformation:CreateStack' not in actions:
        return False, f'{DEPLOYER}\'s policy does not allow cloudformation:CreateStack.'
    return True, f'{DEPLOYER} was denied, then deployed {CHECK_STACK} with a policy that allows CreateStack.'


def _check_failed_stack() -> tuple[bool, str]:
    marker = _marked('failed-stack') or {}
    failure = marker.get('failure') or {}
    if 'MessageRetentionPeriod' not in failure.get('reason', ''):
        return False, 'Read the first failed event of the broken stack.'
    if not marker.get('updateRefused'):
        return False, 'Try to update the stack in ROLLBACK_COMPLETE.'
    stack = _stack(OPS_STACK) or {}
    if stack.get('StackStatus') != 'CREATE_COMPLETE':
        return False, f'{OPS_STACK} is {stack.get("StackStatus") or "gone"}, not CREATE_COMPLETE.'
    return True, f'{failure["resource"]} failed ({failure["reason"]}); {OPS_STACK} was recreated and is complete.'


def _check_export() -> tuple[bool, str]:
    marker = _marked('missing-export') or {}
    if TYPO_EXPORT not in (marker.get('failure') or {}).get('reason', ''):
        return False, f'The first {APP_STACK} should fail on the missing export {TYPO_EXPORT}.'
    if VPC_EXPORT not in _exports():
        return False, f'{NETWORK_STACK} does not export {VPC_EXPORT}.'
    if (_stack(APP_STACK) or {}).get('StackStatus') not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
        return False, f'{APP_STACK} is not complete yet.'
    return True, f'{APP_STACK} failed on {TYPO_EXPORT}, and the fixed stack imports {VPC_EXPORT}.'


def _check_capacity() -> tuple[bool, str]:
    marker = _marked('subnet-capacity') or {}
    if marker.get('stuckAt') is None or marker['stuckAt'] >= DESIRED:
        return False, f'{GROUP} should first be stuck below {DESIRED} instances in the full subnet.'
    group = _group() or {}
    subnets = [item for item in str(group.get('VPCZoneIdentifier', '')).split(',') if item]
    if len(subnets) < 2:
        return False, f'{GROUP} still uses one subnet.'
    if _running(group) < DESIRED:
        return False, f'{GROUP} has {_running(group)} of {DESIRED} instances.'
    return True, f'{GROUP} was stuck at {marker["stuckAt"]}; with a second subnet it runs {DESIRED} across two zones.'


def _check_ami() -> tuple[bool, str]:
    source, copy = _image(), _image(COPY_REGION)
    if not source or source.get('State') != 'available':
        return False, f'{AMI_NAME} is not available in {REGION}.'
    if not copy or copy.get('State') != 'available':
        return False, f'The copy is not available in {COPY_REGION}.'
    if copy['ImageId'] == source['ImageId']:
        return False, 'The copy should have its own image ID.'
    return True, f'{AMI_NAME} is {source["ImageId"]} in {REGION} and {copy["ImageId"]} in {COPY_REGION}.'


def _check_share() -> tuple[bool, str]:
    share = _share()
    if not share or share.get('status') != 'ACTIVE':
        return False, f'The {SHARE} resource share does not exist yet.'
    principals = [item['associatedEntity'] for item in _share_associations('PRINCIPAL')
                  if item.get('status') == 'ASSOCIATED']
    subnet_b = _outputs(NETWORK_STACK).get('SubnetB', '')
    resources = [item['associatedEntity'] for item in _share_associations('RESOURCE')]
    if SHARED_ACCOUNT not in principals:
        return False, f'{SHARE} is not shared with {SHARED_ACCOUNT}.'
    if not subnet_b or not any(subnet_b in item for item in resources):
        return False, f'{SHARE} does not hold the new subnet.'
    return True, f'{SHARE} shares {subnet_b} with {SHARED_ACCOUNT}.'


def _check_protect() -> tuple[bool, str]:
    stack = _stack(NETWORK_STACK) or {}
    if not stack.get('EnableTerminationProtection'):
        return False, f'Termination protection is off for {NETWORK_STACK}.'
    if not (_marked('protect-network') or {}).get('deleteRefused'):
        return False, f'Try to delete {NETWORK_STACK}.'
    return True, f'{NETWORK_STACK} is protected; DeleteStack was refused.'


CHECKS = {
    'deployer-access': _check_access,
    'failed-stack': _check_failed_stack,
    'missing-export': _check_export,
    'subnet-capacity': _check_capacity,
    'copy-ami': _check_ami,
    'share-subnet': _check_share,
    'protect-network': _check_protect,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _create_stack(name: str, template: dict[str, Any], parameters: dict[str, str] | None = None,
                  identity: dict[str, Any] | None = None) -> dict[str, Any] | None:
    _client('cloudformation', identity=identity).create_stack(
        StackName=name, TemplateBody=json.dumps(template),
        Parameters=[{'ParameterKey': key, 'ParameterValue': value} for key, value in (parameters or {}).items()],
        Tags=[{'Key': 'App', 'Value': APP_TAG}])
    return _settled(name)


def step_deployer_access() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _client('iam')
    marker = _marked('deployer-access') or {}
    if not _safe_call(lambda: iam.get_user(UserName=DEPLOYER)):
        iam.create_user(UserName=DEPLOYER, Tags=[{'Key': 'App', 'Value': APP_TAG}])
    identity = marker.get('identity')
    if not identity:
        key = iam.create_access_key(UserName=DEPLOYER)['AccessKey']
        identity = {'access_key_id': key['AccessKeyId'], 'secret_access_key': key['SecretAccessKey'], 'type': 'lab'}
    iam.put_user_policy(UserName=DEPLOYER, PolicyName=DEPLOYER_POLICY,
                        PolicyDocument=json.dumps(deployer_policy(False)))
    time.sleep(1)
    denied_code, denied_message = None, None
    try:
        _client('cloudformation', identity=identity).create_stack(StackName=CHECK_STACK,
                                                                  TemplateBody=json.dumps(CHECK_TEMPLATE))
        _settled(CHECK_STACK)
    except ClientError as exc:
        denied_code, denied_message = _code(exc), str(exc)
    simulation = _safe_call(lambda: iam.simulate_principal_policy(
        PolicySourceArn=f'arn:aws:iam::{ACCOUNT}:user/{DEPLOYER}', ActionNames=['cloudformation:CreateStack'],
        ResourceArns=[f'arn:aws:cloudformation:{REGION}:{ACCOUNT}:stack/{CHECK_STACK}/x'])
        ['EvaluationResults'][0]['EvalDecision'])
    iam.put_user_policy(UserName=DEPLOYER, PolicyName=DEPLOYER_POLICY,
                        PolicyDocument=json.dumps(deployer_policy(True)))
    time.sleep(1)
    created = False
    if not _stack(CHECK_STACK):
        try:
            created = (_create_stack(CHECK_STACK, CHECK_TEMPLATE, identity=identity) or {}).get(
                'StackStatus') == 'CREATE_COMPLETE'
        except ClientError as exc:
            denied_message = f'{denied_message}; after the fix: {exc}'
    else:
        created = denied_code is not None
    if created:
        _safe_call(lambda: _client('cloudformation', identity=identity).delete_stack(StackName=CHECK_STACK))
    _marker('deployer-access', {'identity': identity, 'deniedCode': denied_code or marker.get('deniedCode'),
                                'created': created or marker.get('created'), 'simulation': simulation})
    verified, message = _check_access()
    return _result('deployer-access', ACCESS_COMMAND, {
        'Denied': {'Code': denied_code, 'Message': denied_message}, 'SimulatedDecision': simulation,
        'CreatedWithFix': created, 'Policy': deployer_policy(True)}, verified, message, started)


def step_failed_stack() -> dict[str, Any]:
    started = time.perf_counter()
    cloudformation = _client('cloudformation')
    marker = _marked('failed-stack') or {}
    failure = marker.get('failure')
    update_refused = marker.get('updateRefused', False)
    refusal = None
    if not failure:
        _delete_stack(OPS_STACK)
        _create_stack(OPS_STACK, ops_template(BAD_RETENTION))
        failure = first_failure(_events(OPS_STACK))
        try:
            cloudformation.update_stack(StackName=OPS_STACK, TemplateBody=json.dumps(ops_template(GOOD_RETENTION)))
        except ClientError as exc:
            update_refused, refusal = _code(exc) == 'ValidationError', str(exc)
    failed_status = (_stack(OPS_STACK) or {}).get('StackStatus')
    if failed_status != 'CREATE_COMPLETE':
        _delete_stack(OPS_STACK)
        _create_stack(OPS_STACK, ops_template(GOOD_RETENTION))
    _marker('failed-stack', {'failure': failure, 'updateRefused': update_refused})
    verified, message = _check_failed_stack()
    return _result('failed-stack', ROLLBACK_COMMAND, {
        'FirstFailure': failure, 'StatusAfterFailure': failed_status, 'UpdateRefused': refusal or update_refused,
        'Recreated': (_stack(OPS_STACK) or {}).get('StackStatus'), 'Outputs': _outputs(OPS_STACK)},
        verified, message, started)


def _ensure_network(second_subnet: bool) -> dict[str, str]:
    stack = _stack(NETWORK_STACK)
    if not stack:
        _create_stack(NETWORK_STACK, network_template(second_subnet))
    elif second_subnet and 'SubnetB' not in _outputs(NETWORK_STACK):
        _client('cloudformation').update_stack(StackName=NETWORK_STACK,
                                               TemplateBody=json.dumps(network_template(True)))
        _settled(NETWORK_STACK)
    outputs = _outputs(NETWORK_STACK)
    subnet_a = outputs.get('SubnetA')
    if subnet_a and len(_workload_enis(subnet_a)) < OTHER_WORKLOAD_ENIS:
        ec2 = _client('ec2')
        for _ in range(OTHER_WORKLOAD_ENIS - len(_workload_enis(subnet_a))):
            ec2.create_network_interface(SubnetId=subnet_a, Description='other workload')
    return outputs


def step_missing_export() -> dict[str, Any]:
    started = time.perf_counter()
    marker = _marked('missing-export') or {}
    outputs = _ensure_network(False)
    subnet_a = outputs.get('SubnetA')
    if not subnet_a:
        return _result('missing-export', EXPORT_COMMAND, {'Network': _stack(NETWORK_STACK)}, False,
                       f'{NETWORK_STACK} did not create its subnet.', started)
    failure = marker.get('failure')
    if not failure:
        _delete_stack(APP_STACK)
        _create_stack(APP_STACK, app_template(TYPO_EXPORT), {'Subnets': subnet_a})
        failure = first_failure(_events(APP_STACK))
    exports = _exports()
    if (_stack(APP_STACK) or {}).get('StackStatus') not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
        _delete_stack(APP_STACK)
        _create_stack(APP_STACK, app_template(VPC_EXPORT), {'Subnets': subnet_a})
    _marker('missing-export', {'failure': failure})
    verified, message = _check_export()
    return _result('missing-export', EXPORT_COMMAND, {
        'FirstFailure': failure, 'Exports': exports, 'App': (_stack(APP_STACK) or {}).get('StackStatus')},
        verified, message, started)


def step_subnet_capacity() -> dict[str, Any]:
    started = time.perf_counter()
    if not _group():
        return _result('subnet-capacity', CAPACITY_COMMAND, {}, False, 'Run missing-export first.', started)
    marker = _marked('subnet-capacity') or {}
    outputs = _outputs(NETWORK_STACK)
    subnet_a = outputs.get('SubnetA', '')
    stuck_at = marker.get('stuckAt')
    diagnosis = marker.get('diagnosis')
    free_before = marker.get('freeBefore')
    if stuck_at is None:
        # Auto Scaling launches in the background; wait until the subnet is full and the count stops changing.
        counts: list[int] = []

        def settled() -> bool:
            counts.append(_running(_group()))
            full = _free_addresses([subnet_a]).get(subnet_a) == 0
            return full and len(counts) >= 3 and counts[-1] == counts[-2] == counts[-3] and counts[-1] > 0

        _wait(settled, 90, delay=4)
        group = _group()
        stuck_at = _running(group)
        free_before = _free_addresses([subnet_a])
        diagnosis = capacity_diagnosis(DESIRED, stuck_at, free_before)
    activities = _safe_call(lambda: [
        {'Status': item['StatusCode'], 'Description': item['Description']}
        for item in _client('autoscaling').describe_scaling_activities(AutoScalingGroupName=GROUP)['Activities']],
        [])
    outputs = _ensure_network(True)
    subnets = ','.join(item for item in (outputs.get('SubnetA'), outputs.get('SubnetB')) if item)
    group = _group() or {}
    if group.get('VPCZoneIdentifier') != subnets:
        _client('cloudformation').update_stack(
            StackName=APP_STACK, UsePreviousTemplate=True,
            Parameters=[{'ParameterKey': 'Subnets', 'ParameterValue': subnets}])
        _settled(APP_STACK)
    _wait(lambda: _running(_group()) >= DESIRED, GROUP_WAIT_SECONDS, delay=3)
    _marker('subnet-capacity', {'stuckAt': stuck_at, 'diagnosis': diagnosis, 'freeBefore': free_before})
    verified, message = _check_capacity()
    return _result('subnet-capacity', CAPACITY_COMMAND, {
        'StuckAt': stuck_at, 'Desired': DESIRED, 'Diagnosis': diagnosis, 'FreeBefore': free_before,
        'UsableInSlash28': usable_addresses(SUBNET_A_CIDR), 'OtherWorkloadInterfaces': len(_workload_enis(subnet_a)),
        'Activities': activities[:6], 'SubnetsNow': subnets, 'Running': _running(_group())},
        verified, message, started)


def step_copy_ami() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    source = _image()
    if not source:
        instances = [item['InstanceId'] for item in (_group() or {}).get('Instances') or []]
        if not instances:
            return _result('copy-ami', AMI_COMMAND, {}, False, f'{GROUP} has no instance to image.', started)
        try:
            ec2.create_image(InstanceId=instances[0], Name=f'{AMI_NAME}-{int(time.time())}', NoReboot=True,
                             Description='web tier golden image')
        except ClientError as exc:
            # A slow CreateImage can be retried by the SDK after Floci already made the image.
            if _code(exc) != 'InvalidAMIName.Duplicate':
                raise
    _wait(lambda: (_image() or {}).get('State') == 'available', 120, delay=3)
    source = _image() or {}
    if not _image(COPY_REGION) and source:
        _client('ec2', COPY_REGION).copy_image(SourceImageId=source['ImageId'], SourceRegion=REGION,
                                               Name=source['Name'])
    _wait(lambda: (_image(COPY_REGION) or {}).get('State') == 'available', 120, delay=3)
    share_outcome = None
    try:
        ec2.modify_image_attribute(ImageId=source.get('ImageId', ''),
                                   LaunchPermission={'Add': [{'UserId': SHARED_ACCOUNT}]})
        share_outcome = 'shared'
    except ClientError as exc:
        share_outcome = _code(exc)
    copy = _image(COPY_REGION) or {}
    verified, message = _check_ami()
    return _result('copy-ami', AMI_COMMAND, {
        'Source': {key: source.get(key) for key in ('ImageId', 'State', 'Name')},
        'Copy': {key: copy.get(key) for key in ('ImageId', 'State', 'Name')}, 'Region': COPY_REGION,
        'LaunchPermission': share_outcome}, verified, message, started)


def step_share_subnet() -> dict[str, Any]:
    started = time.perf_counter()
    subnet_b = _outputs(NETWORK_STACK).get('SubnetB')
    if not subnet_b:
        return _result('share-subnet', SHARE_COMMAND, {}, False, 'Run subnet-capacity first.', started)
    if not _share():
        _client('ram').create_resource_share(
            name=SHARE, resourceArns=[f'arn:aws:ec2:{REGION}:{ACCOUNT}:subnet/{subnet_b}'],
            principals=[SHARED_ACCOUNT], allowExternalPrincipals=False, tags=[{'key': 'App', 'value': APP_TAG}])
    verified, message = _check_share()
    return _result('share-subnet', SHARE_COMMAND, {
        'Share': _share(), 'Principals': _share_associations('PRINCIPAL'), 'Resources': _share_associations('RESOURCE')},
        verified, message, started)


def step_protect_network() -> dict[str, Any]:
    started = time.perf_counter()
    cloudformation = _client('cloudformation')
    if not _stack(NETWORK_STACK):
        return _result('protect-network', PROTECT_COMMAND, {}, False, f'{NETWORK_STACK} does not exist.', started)
    cloudformation.update_termination_protection(StackName=NETWORK_STACK, EnableTerminationProtection=True)
    refusal = None
    try:
        cloudformation.delete_stack(StackName=NETWORK_STACK)
    except ClientError as exc:
        refusal = str(exc)
    _marker('protect-network', {'deleteRefused': bool(refusal)})
    verified, message = _check_protect()
    return _result('protect-network', PROTECT_COMMAND, {
        'TerminationProtection': (_stack(NETWORK_STACK) or {}).get('EnableTerminationProtection'),
        'DeleteRefused': refusal, 'Exports': _exports()}, verified, message, started)


RUNNERS = {
    'deployer-access': step_deployer_access,
    'failed-stack': step_failed_stack,
    'missing-export': step_missing_export,
    'subnet-capacity': step_subnet_capacity,
    'copy-ami': step_copy_ami,
    'share-subnet': step_share_subnet,
    'protect-network': step_protect_network,
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
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError, TypeError, StopIteration):
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
    """Remove the share, AMIs, stacks, network interfaces, and the deployer user."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code in ('UnknownResourceException', 'ValidationError'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    share = _share()
    if share:
        attempt(f'resource-share:{SHARE}', lambda: _client('ram').delete_resource_share(
            resourceShareArn=share['resourceShareArn']))
    for region in (COPY_REGION, REGION):
        image = _image(region)
        if image:
            attempt(f'image:{region}:{image["ImageId"]}',
                    lambda region=region, image=image: _client('ec2', region).deregister_image(
                        ImageId=image['ImageId']))
    cloudformation = _client('cloudformation')
    if _stack(NETWORK_STACK):
        attempt(f'termination-protection:{NETWORK_STACK}', lambda: cloudformation.update_termination_protection(
            StackName=NETWORK_STACK, EnableTerminationProtection=False))
    for name in (APP_STACK, CHECK_STACK, OPS_STACK):
        if _stack(name):
            attempt(f'stack:{name}', lambda name=name: _delete_stack(name))
    if _group():
        attempt(f'autoscaling-group:{GROUP}', lambda: _client('autoscaling').delete_auto_scaling_group(
            AutoScalingGroupName=GROUP, ForceDelete=True))
    outputs = _outputs(NETWORK_STACK)
    # A force-deleted group's instances shut down in the background, and the subnets can't go until they have.
    _wait(lambda: not _live_instances([outputs[key] for key in ('SubnetA', 'SubnetB') if outputs.get(key)]),
          GROUP_WAIT_SECONDS, delay=3)
    subnet_a = outputs.get('SubnetA')
    if subnet_a:
        ec2 = _client('ec2')
        for eni in _enis(subnet_a):
            attempt(f'network-interface:{eni["NetworkInterfaceId"]}',
                    lambda eni=eni: ec2.delete_network_interface(NetworkInterfaceId=eni['NetworkInterfaceId']))
    if _stack(NETWORK_STACK):
        attempt(f'stack:{NETWORK_STACK}', lambda: _delete_stack(NETWORK_STACK))
        left = _stack(NETWORK_STACK)
        if left:
            errors.append(f'stack:{NETWORK_STACK}: still {left.get("StackStatus")} ({left.get("StackStatusReason") or "no reason given"})')
    placeholder = f'{TEMPLATE}-placeholder'
    if _safe_call(lambda: _client('ec2').describe_launch_templates(LaunchTemplateNames=[placeholder])
                  ['LaunchTemplates']):
        attempt(f'launch-template:{placeholder}', lambda: _client('ec2').delete_launch_template(
            LaunchTemplateName=placeholder))
    iam = _client('iam')
    if _safe_call(lambda: iam.get_user(UserName=DEPLOYER)):
        def delete_user() -> None:
            for key in iam.list_access_keys(UserName=DEPLOYER).get('AccessKeyMetadata', []):
                iam.delete_access_key(UserName=DEPLOYER, AccessKeyId=key['AccessKeyId'])
            for name in iam.list_user_policies(UserName=DEPLOYER).get('PolicyNames', []):
                iam.delete_user_policy(UserName=DEPLOYER, PolicyName=name)
            iam.delete_user(UserName=DEPLOYER)
        attempt(f'user:{DEPLOYER}', delete_user)

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws ram delete-resource-share --resource-share-arn <share>\n'
            f'aws ec2 deregister-image --image-id <copy> --region {COPY_REGION}\n'
            'aws ec2 deregister-image --image-id <ami>\n'
            f'aws cloudformation update-termination-protection --stack-name {NETWORK_STACK} '
            '--no-enable-termination-protection\n'
            f'aws cloudformation delete-stack --stack-name {APP_STACK}\n'
            f'aws cloudformation delete-stack --stack-name {OPS_STACK}\n'
            'aws ec2 delete-network-interface --network-interface-id <each>\n'
            f'aws cloudformation delete-stack --stack-name {NETWORK_STACK}\n'
            f'aws iam delete-access-key --user-name {DEPLOYER} --access-key-id <key>\n'
            f'aws iam delete-user-policy --user-name {DEPLOYER} --policy-name {DEPLOYER_POLICY}\n'
            f'aws iam delete-user --user-name {DEPLOYER}'
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
            else 'The share, AMIs, stacks, network interfaces, and deployer user are gone.',
        },
    }
