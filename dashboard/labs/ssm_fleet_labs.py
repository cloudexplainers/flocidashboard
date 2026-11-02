"""In-place fleet release lab: release a new version to an Auto Scaling fleet in place, one instance at a time,
watch a bad release fail validation and roll it back, see in-place changes drift when the group scales out, diagnose a
CodeDeploy deployment that reaches no instances, replace the fleet with an instance refresh, and find the root cause
of a CloudFormation update that rolled back.

Floci 2.4.0 runs the instances as real containers, runs Run Command scripts in them, replaces instances with an
instance refresh, and rolls back a failed stack update with events that name the failing resource. It runs Run Command
without an SSM agent, does not honor MaxConcurrency or a refresh's minimum healthy percentage, and cannot resolve a
CodeDeploy deployment group's EC2 tag filters, so the lab rolls out one instance at a time itself and says so in each
step.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ssm'
LAB_KEY = 'fleet-release-troubleshooting'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't3.micro'
LAUNCH_TEMPLATE = 'lab-fleet-web'
GROUP = 'lab-fleet-web'
APP_TAG = {'Key': 'App', 'Value': GROUP}
LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}
DESIRED = 2
SCALED = 3
APP_DIR = '/opt/storefront'

RELEASE_1 = 'release-1'
RELEASE_2 = 'release-2'
RELEASE_3 = 'release-3'

APPLICATION = 'lab-fleet-web'
DEPLOYMENT_GROUP = 'lab-fleet-web-inplace'
CODEDEPLOY_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/lab-fleet-codedeploy'

STACK = 'lab-fleet-config'
CONFIG_PARAMETER = '/lab-fleet/config-version'
EVENTS_QUEUE = 'lab-fleet-deploy-events'
BAD_RETENTION = 30   # SQS allows 60 to 1,209,600 seconds
GOOD_RETENTION = 345600

COMMAND_TIMEOUT = 60
GROUP_TIMEOUT = 150


def user_data(release: str) -> str:
    """Boot script baked into the launch template: install the release and its config."""
    script = (
        '#!/bin/sh\n'
        f'mkdir -p {APP_DIR}\n'
        f'echo {release} > {APP_DIR}/release\n'
        f'printf \'{{"release":"{release}","database":"orders"}}\\n\' > {APP_DIR}/config.json\n'
    )
    return base64.b64encode(script.encode()).decode()


def release_script(release: str, require_secrets: bool = False) -> str:
    """The in-place release: stage, switch, then validate. Release 3 also needs a secrets file nobody shipped."""
    validate = f'grep -qx {release} {APP_DIR}/release && test -s {APP_DIR}/config.json'
    if require_secrets:
        validate += (f' && {{ test -f {APP_DIR}/secrets.env || '
                     f'{{ echo "ValidateService: {APP_DIR}/secrets.env is missing" >&2; exit 1; }}; }}')
    return (
        f'set -e\nmkdir -p {APP_DIR}\n'
        f'cp {APP_DIR}/release {APP_DIR}/release.previous 2>/dev/null || true\n'
        f'echo {release} > {APP_DIR}/release\n'
        f'printf \'{{"release":"{release}","database":"orders"}}\\n\' > {APP_DIR}/config.json\n'
        f'{validate}\n'
        f'echo "{release} is live on $(hostname)"'
    )


def rollback_script() -> str:
    return (
        f'set -e\ntest -f {APP_DIR}/release.previous\n'
        f'cp {APP_DIR}/release.previous {APP_DIR}/release\n'
        f'printf \'{{"release":"%s","database":"orders"}}\\n\' "$(cat {APP_DIR}/release)" > {APP_DIR}/config.json\n'
        f'echo "rolled back to $(cat {APP_DIR}/release) on $(hostname)"'
    )


READ_SCRIPT = f'cat {APP_DIR}/release'


def plan_rolling(instance_ids: list[str], max_concurrency: int = 1) -> list[list[str]]:
    """Split a fleet into the batches a rolling release runs, max_concurrency instances at a time."""
    size = max(1, max_concurrency)
    ordered = sorted(instance_ids)
    return [ordered[index:index + size] for index in range(0, len(ordered), size)]


def drifted(releases: dict[str, str]) -> dict[str, list[str]]:
    """Group instances by the release they run; more than one key means the fleet has drifted."""
    groups: dict[str, list[str]] = {}
    for instance, release in sorted(releases.items()):
        groups.setdefault(release, []).append(instance)
    return groups


def root_cause(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first resource that failed, reading stack events oldest first. Later failures are usually the rollback
    reacting to it."""
    for event in sorted(events, key=lambda item: str(item.get('Timestamp'))):
        status = event.get('ResourceStatus') or ''
        if status.endswith('_FAILED') and event.get('ResourceType') != 'AWS::CloudFormation::Stack':
            return {'LogicalResourceId': event.get('LogicalResourceId'), 'ResourceStatus': status,
                    'ResourceStatusReason': event.get('ResourceStatusReason')}
    return None


def no_instances_checklist(tagged_running: int, managed: int, role_ok: bool) -> list[dict[str, Any]]:
    """The checks for a CodeDeploy NoInstancesReachable failure, in the order to try them."""
    return [
        {'check': 'The tag filter matches running instances', 'ok': tagged_running > 0,
         'detail': f'{tagged_running} running instances carry {APP_TAG["Key"]}={APP_TAG["Value"]}'},
        {'check': 'An agent on each instance is reporting', 'ok': managed >= tagged_running > 0,
         'detail': f'{managed} of {tagged_running} instances report through Systems Manager'},
        {'check': 'The deployment group has a service role', 'ok': role_ok,
         'detail': 'CodeDeploy needs a service role to read tags and Auto Scaling groups'},
    ]


def _template(version: int, retention: int = GOOD_RETENTION) -> dict[str, Any]:
    resources: dict[str, Any] = {
        'ConfigVersion': {'Type': 'AWS::SSM::Parameter', 'Properties': {
            'Name': CONFIG_PARAMETER, 'Type': 'String', 'Value': str(version),
            'Description': 'Fleet config version (fleet release lab)'}},
    }
    if version >= 2:
        resources['DeployEvents'] = {'Type': 'AWS::SQS::Queue', 'Properties': {
            'QueueName': EVENTS_QUEUE, 'MessageRetentionPeriod': retention}}
    return {'AWSTemplateFormatVersion': '2010-09-09',
            'Description': f'Fleet config v{version} (fleet release lab)', 'Resources': resources}


STACK_V1 = _template(1)
STACK_BROKEN = _template(2, BAD_RETENTION)
STACK_FIXED = _template(2)


# ── Commands ─────────────────────────────────────────────────────────────────

FLEET_COMMAND = (
    f'aws ec2 create-launch-template --launch-template-name {LAUNCH_TEMPLATE} --launch-template-data '
    f'\'{{"ImageId":"{IMAGE_ID}","InstanceType":"{INSTANCE_TYPE}","UserData":"<base64 install {RELEASE_1}>"}}\'\n'
    f'aws autoscaling create-auto-scaling-group --auto-scaling-group-name {GROUP} '
    f'--launch-template LaunchTemplateName={LAUNCH_TEMPLATE},Version=\'$Latest\' --min-size {DESIRED} '
    f'--max-size {SCALED} --desired-capacity {DESIRED} --tags Key=App,Value={GROUP},PropagateAtLaunch=true'
)
ROLLING_COMMAND = (
    f'aws ssm send-command --document-name AWS-RunShellScript --targets Key=tag:App,Values={GROUP} '
    f'--max-concurrency 1 --max-errors 0 --parameters commands=["<install {RELEASE_2} and validate>"]\n'
    'aws ssm get-command-invocation --command-id <id> --instance-id <instance>'
)
FAILED_COMMAND = (
    f'aws ssm send-command --document-name AWS-RunShellScript --targets Key=tag:App,Values={GROUP} '
    f'--max-concurrency 1 --max-errors 0 --parameters commands=["<install {RELEASE_3} and validate>"]\n'
    'aws ssm get-command-invocation --command-id <id> --instance-id <instance>   # Failed, ResponseCode 1\n'
    'aws ssm send-command ... --parameters commands=["<restore release.previous>"]'
)
DRIFT_COMMAND = (
    f'aws autoscaling set-desired-capacity --auto-scaling-group-name {GROUP} --desired-capacity {SCALED}\n'
    f'aws ssm send-command --document-name AWS-RunShellScript --targets Key=tag:App,Values={GROUP} '
    f'--parameters commands=["cat {APP_DIR}/release"]'
)
CODEDEPLOY_COMMAND = (
    f'aws deploy create-application --application-name {APPLICATION} --compute-platform Server\n'
    f'aws deploy create-deployment-group --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    f'--service-role-arn {CODEDEPLOY_ROLE_ARN} --deployment-config-name CodeDeployDefault.OneAtATime '
    f'--ec2-tag-filters Key=App,Value={GROUP},Type=KEY_AND_VALUE '
    '--auto-rollback-configuration enabled=true,events=DEPLOYMENT_FAILURE\n'
    f'aws deploy create-deployment --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    '--revision revisionType=AppSpecContent,appSpecContent={content=<appspec.yml>}\n'
    'aws deploy get-deployment --deployment-id <id>   # errorInformation.code\n'
    f'aws ec2 describe-instances --filters Name=tag:App,Values={GROUP} Name=instance-state-name,Values=running\n'
    'aws ssm describe-instance-information'
)
REFRESH_COMMAND = (
    f'aws ec2 create-launch-template-version --launch-template-name {LAUNCH_TEMPLATE} --source-version 1 '
    f'--launch-template-data \'{{"UserData":"<base64 install {RELEASE_2}>"}}\'\n'
    f'aws autoscaling start-instance-refresh --auto-scaling-group-name {GROUP} '
    '--preferences MinHealthyPercentage=50,InstanceWarmup=10\n'
    f'aws autoscaling describe-instance-refreshes --auto-scaling-group-name {GROUP}'
)
ROLLBACK_COMMAND = (
    f'aws cloudformation create-stack --stack-name {STACK} --template-body file://fleet-config-v1.json\n'
    f'aws cloudformation update-stack --stack-name {STACK} --template-body file://fleet-config-v2.json\n'
    f'aws cloudformation describe-stack-events --stack-name {STACK}   # the first *_FAILED event'
)
FIX_COMMAND = (
    f'aws cloudformation update-stack --stack-name {STACK} --template-body file://fleet-config-v2-fixed.json\n'
    f'aws cloudformation wait stack-update-complete --stack-name {STACK}'
)

FLOCI = 'How Floci emulates this: '

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Release in place across an Auto Scaling fleet and troubleshoot the failures',
    'description': (
        'Run a storefront on an Auto Scaling group, release a new version to it in place one instance at a time, '
        'stop and roll back a release that fails validation, watch in-place changes drift when the group scales out, '
        'diagnose a CodeDeploy deployment that reaches no instances, replace the fleet with an instance refresh, and '
        'find the root cause of a CloudFormation update that rolled back.'
    ),
    'steps': [
        {
            'key': 'create-fleet',
            'title': f'Launch a fleet of {DESIRED} instances from a launch template',
            'command': FLEET_COMMAND,
            'explanation': (
                f'The launch template\'s user data installs {RELEASE_1} into {APP_DIR} at boot, and the Auto Scaling '
                f'group keeps {DESIRED} instances running from it. The App tag is propagated to every instance, so '
                'Run Command and CodeDeploy can target the fleet by tag. '
                + FLOCI + 'each instance is a real Docker container and the user data really runs, about 20 seconds '
                'after the instance reports InService, so the lab waits for the install before going on.'
            ),
        },
        {
            'key': 'rolling-release',
            'title': f'Release {RELEASE_2} in place, one instance at a time',
            'command': ROLLING_COMMAND,
            'explanation': (
                'An in-place release changes the software on running instances. With MaxConcurrency 1 and MaxErrors 0, '
                'Run Command updates one instance, waits for its validation to pass, and only then moves on, so a bad '
                'release stops after one instance. Each instance keeps the previous release for a rollback. '
                + FLOCI + 'the script really runs in each container. Floci runs Run Command without an SSM agent and '
                'does not apply MaxConcurrency or MaxErrors, so the lab sends one instance at a time and stops on the '
                'first failure itself.'
            ),
        },
        {
            'key': 'failed-release',
            'title': f'Stop {RELEASE_3} when validation fails and roll it back',
            'command': FAILED_COMMAND,
            'explanation': (
                f'{RELEASE_3} expects a secrets file that nobody shipped, so its validation exits 1 on the first '
                'instance. The rollout stops there, the other instances never see it, and the lab reads the failure '
                'from the invocation: the status, the response code, and the standard error. Then it restores the '
                'previous release on that instance. '
                + FLOCI + 'the failure, its exit code and stderr, and the rollback all really happen in the container.'
            ),
        },
        {
            'key': 'scale-out-drift',
            'title': 'Scale out and find the instance that came up on the old release',
            'command': DRIFT_COMMAND,
            'explanation': (
                f'The launch template still installs {RELEASE_1}. When the group scales to {SCALED}, the new instance '
                f'boots from it and runs {RELEASE_1} while the others run {RELEASE_2}. That drift is the weakness of '
                'in-place releases: anything launched later, by scaling or by replacing an unhealthy instance, gets '
                'the old version. '
                + FLOCI + 'the group really launches a new container from the launch template, and the lab reads every '
                'instance\'s release with Run Command.'
            ),
        },
        {
            'key': 'diagnose-codedeploy',
            'title': 'Diagnose a CodeDeploy deployment that reaches no instances',
            'command': CODEDEPLOY_COMMAND,
            'explanation': (
                'CodeDeploy\'s in-place deployments stop and start the application on each instance through AppSpec '
                'hooks, run by the CodeDeploy agent. The lab creates a deployment group that targets the fleet by tag '
                'and starts a deployment. It fails with NoInstancesReachable, so the lab works through the usual '
                'checks: does the tag filter match running instances, is an agent reporting on each instance, and is '
                'there a service role. '
                + FLOCI + 'there is no CodeDeploy agent, and Floci cannot resolve EC2 tag filters for deployments, so '
                'every in-place deployment fails this way even with correct tags. The lab shows which checks pass and '
                'which one fails.'
            ),
        },
        {
            'key': 'instance-refresh',
            'title': f'Bake {RELEASE_2} into the launch template and refresh the fleet',
            'command': REFRESH_COMMAND,
            'explanation': (
                f'The fix for drift is to make the launch template install {RELEASE_2} and replace every instance. An '
                'instance refresh does that in batches, keeping a minimum healthy percentage in service and waiting '
                'a warm-up time before counting a new instance as healthy. '
                + FLOCI + 'the refresh really terminates the old containers and launches new ones from version 2. It '
                'replaces them all at once rather than keeping 50% in service.'
            ),
        },
        {
            'key': 'stack-rollback',
            'title': 'Find the root cause of a stack update that rolled back',
            'command': ROLLBACK_COMMAND,
            'explanation': (
                f'The fleet\'s configuration lives in the {STACK} stack. An update adds the {EVENTS_QUEUE} queue with a '
                f'retention of {BAD_RETENTION} seconds, below SQS\'s minimum of 60. CloudFormation fails the queue, '
                'rolls the whole update back, and ends in UPDATE_ROLLBACK_COMPLETE. The root cause is the first '
                'resource that failed, reading the events oldest first; the later events are the rollback. '
                + FLOCI + 'the update fails, rolls back, and records the failing resource and its reason for real.'
            ),
        },
        {
            'key': 'fix-stack',
            'title': 'Fix the template and update the stack again',
            'command': FIX_COMMAND,
            'explanation': (
                f'With the retention set to {GOOD_RETENTION} seconds (four days), the same update completes, the '
                f'queue exists, and {CONFIG_PARAMETER} moves to version 2. '
                + FLOCI + 'the update and the queue are real.'
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
    except (ClientError, BotoCoreError, KeyError):
        return default


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Lookups ──────────────────────────────────────────────────────────────────

def _group() -> dict[str, Any] | None:
    groups = _client('autoscaling').describe_auto_scaling_groups(AutoScalingGroupNames=[GROUP]).get(
        'AutoScalingGroups', [])
    return groups[0] if groups else None


def _in_service() -> list[dict[str, Any]]:
    group = _group() or {}
    return [item for item in group.get('Instances', []) if item.get('LifecycleState') == 'InService']


def _launch_template() -> dict[str, Any] | None:
    try:
        templates = _client('ec2').describe_launch_templates(LaunchTemplateNames=[LAUNCH_TEMPLATE]).get(
            'LaunchTemplates', [])
    except ClientError as exc:
        if 'NotFound' in _code(exc) or 'Malformed' in _code(exc):
            return None
        raise
    return templates[0] if templates else None


def _wait_in_service(count: int, timeout: float = GROUP_TIMEOUT) -> list[dict[str, Any]]:
    _wait(lambda: len(_in_service()) >= count and all(
        item.get('LifecycleState') == 'InService' for item in (_group() or {}).get('Instances', [])), timeout)
    return _in_service()


def _run_shell(instance_id: str, script: str) -> dict[str, Any]:
    """Run a script through Run Command and return the finished invocation."""
    ssm = _client('ssm')
    command_id = ssm.send_command(
        InstanceIds=[instance_id], DocumentName='AWS-RunShellScript', Parameters={'commands': [script]},
        MaxConcurrency='1', MaxErrors='0', Comment=f'{LAB_KEY}')['Command']['CommandId']
    outcome: dict[str, Any] = {}

    def finished() -> bool:
        try:
            outcome.update(ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id))
        except ClientError as exc:
            if _code(exc) == 'InvocationDoesNotExist':
                return False
            raise
        return outcome.get('Status') not in ('Pending', 'InProgress', 'Delayed')

    if not _wait(finished, timeout=COMMAND_TIMEOUT, delay=1):
        outcome['Status'] = 'TimedOut'
    return {'InstanceId': instance_id, 'CommandId': command_id, 'Status': outcome.get('Status'),
            'ResponseCode': outcome.get('ResponseCode'),
            'Stdout': (outcome.get('StandardOutputContent') or '').strip(),
            'Stderr': (outcome.get('StandardErrorContent') or '').strip()}


def _wait_installed(instance_ids: list[str], timeout: float = 120) -> None:
    """Wait until each instance's boot script has installed a release. Floci runs user data about 20 seconds after
    the instance reports InService, so a release sent earlier would be overwritten."""
    pending = list(instance_ids)

    def installed() -> bool:
        pending[:] = [instance_id for instance_id in pending
                      if _run_shell(instance_id, f'test -s {APP_DIR}/config.json')['Status'] != 'Success']
        return not pending
    _wait(installed, timeout=timeout, delay=3)


def _releases(instance_ids: list[str]) -> dict[str, str]:
    releases = {}
    for instance_id in instance_ids:
        run = _run_shell(instance_id, READ_SCRIPT)
        releases[instance_id] = run['Stdout'] if run['Status'] == 'Success' else f'unknown ({run["Status"]})'
    return releases


def _stack() -> dict[str, Any] | None:
    try:
        stacks = _client('cloudformation').describe_stacks(StackName=STACK).get('Stacks', [])
    except ClientError as exc:
        if _code(exc) == 'ValidationError':
            return None
        raise
    live = [item for item in stacks if item.get('StackStatus') != 'DELETE_COMPLETE']
    return live[0] if live else None


def _wait_stack() -> dict[str, Any] | None:
    _wait(lambda: not ((_stack() or {}).get('StackStatus') or '').endswith('IN_PROGRESS'), timeout=90)
    return _stack()


def _parameter() -> str | None:
    return _safe_call(lambda: _client('ssm').get_parameter(Name=CONFIG_PARAMETER)['Parameter']['Value'])


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_fleet() -> tuple[bool, str]:
    group = _group()
    if not group:
        return False, f'Create the {GROUP} Auto Scaling group.'
    if not _launch_template():
        return False, f'Create the {LAUNCH_TEMPLATE} launch template.'
    if len(_in_service()) < DESIRED:
        return False, f'Wait for {DESIRED} instances to be InService.'
    tags = {(item.get('Key'), item.get('Value'), item.get('PropagateAtLaunch')) for item in group.get('Tags', [])}
    if (APP_TAG['Key'], APP_TAG['Value'], True) not in tags:
        return False, 'Propagate the App tag to the instances.'
    return True, f'{GROUP} runs {len(_in_service())} instances from {LAUNCH_TEMPLATE}.'


def _check_rolling() -> tuple[bool, str]:
    marker = _marked('rolling-release') or {}
    runs = marker.get('runs') or []
    if len(runs) < DESIRED or not all(run.get('Status') == 'Success' for run in runs):
        return False, f'Release {RELEASE_2} to every instance.'
    return True, f'{RELEASE_2} passed validation on {len(runs)} instances, one at a time.'


def _check_failed() -> tuple[bool, str]:
    marker = _marked('failed-release') or {}
    failed = marker.get('failed') or {}
    if failed.get('Status') != 'Failed' or failed.get('ResponseCode') != 1 or 'secrets.env' not in failed.get(
            'Stderr', ''):
        return False, f'Send {RELEASE_3} and watch its validation fail.'
    if marker.get('skipped', 0) < 1:
        return False, 'The rollout should stop after the first failure.'
    if (marker.get('rollback') or {}).get('Status') != 'Success' or RELEASE_2 not in marker['rollback'].get(
            'Stdout', ''):
        return False, f'Roll the failed instance back to {RELEASE_2}.'
    return True, f'{RELEASE_3} failed on one instance, the rollout stopped, and that instance is back on {RELEASE_2}.'


def _check_drift() -> tuple[bool, str]:
    marker = _marked('scale-out-drift') or {}
    groups = marker.get('releases_by_version') or {}
    if len(groups.get(RELEASE_1, [])) < 1 or len(groups.get(RELEASE_2, [])) < DESIRED:
        return False, f'Scale to {SCALED} and read every instance\'s release.'
    return True, f'The new instance runs {RELEASE_1}; the others run {RELEASE_2}.'


def _check_codedeploy() -> tuple[bool, str]:
    marker = _marked('diagnose-codedeploy') or {}
    deployment_id = marker.get('deploymentId')
    if not deployment_id:
        return False, 'Create the deployment group and start a deployment.'
    deployment = _safe_call(lambda: _client('codedeploy').get_deployment(deploymentId=deployment_id)[
        'deploymentInfo']) or {}
    if deployment.get('status') != 'Failed':
        return False, f'The deployment is {deployment.get("status") or "missing"}.'
    checklist = marker.get('checklist') or []
    if not checklist or not checklist[0].get('ok'):
        return False, 'The tag filter should match the running instances.'
    return True, ('The deployment failed with ' + ((deployment.get('errorInformation') or {}).get('code') or '?')
                  + ' and the checklist shows which check fails.')


def _check_refresh() -> tuple[bool, str]:
    refreshes = _safe_call(lambda: _client('autoscaling').describe_instance_refreshes(
        AutoScalingGroupName=GROUP).get('InstanceRefreshes', []), []) or []
    if not refreshes or refreshes[0].get('Status') != 'Successful':
        return False, 'Start an instance refresh and let it finish.'
    template = _launch_template() or {}
    if int(template.get('LatestVersionNumber') or 0) < 2:
        return False, f'Create launch template version 2 with {RELEASE_2}.'
    marker = _marked('instance-refresh') or {}
    releases = marker.get('releases') or {}
    if not releases or set(releases.values()) != {RELEASE_2}:
        return False, f'Every refreshed instance should run {RELEASE_2}.'
    return True, f'The fleet was replaced from launch template version 2 and every instance runs {RELEASE_2}.'


def _check_rollback() -> tuple[bool, str]:
    marker = _marked('stack-rollback') or {}
    cause = marker.get('rootCause') or {}
    if marker.get('status') != 'UPDATE_ROLLBACK_COMPLETE':
        return False, 'Update the stack with the broken template and let it roll back.'
    if cause.get('LogicalResourceId') != 'DeployEvents':
        return False, 'Find the first resource that failed.'
    if marker.get('parameterAfterRollback') != '1':
        return False, f'{CONFIG_PARAMETER} should be back on version 1 after the rollback.'
    return True, f'The rollback restored version 1; the root cause is DeployEvents: {cause.get("ResourceStatusReason")}'


def _check_fix() -> tuple[bool, str]:
    stack = _stack()
    if not stack or stack.get('StackStatus') != 'UPDATE_COMPLETE':
        return False, f'Update {STACK} with the fixed template.'
    if _parameter() != '2':
        return False, f'{CONFIG_PARAMETER} should be version 2.'
    if not _safe_call(lambda: _client('sqs').get_queue_url(QueueName=EVENTS_QUEUE)):
        return False, f'The {EVENTS_QUEUE} queue is missing.'
    return True, f'{STACK} is UPDATE_COMPLETE with the {EVENTS_QUEUE} queue and config version 2.'


CHECKS = {
    'create-fleet': _check_fleet,
    'rolling-release': _check_rolling,
    'failed-release': _check_failed,
    'scale-out-drift': _check_drift,
    'diagnose-codedeploy': _check_codedeploy,
    'instance-refresh': _check_refresh,
    'stack-rollback': _check_rollback,
    'fix-stack': _check_fix,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_fleet() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    if not _launch_template():
        ec2.create_launch_template(
            LaunchTemplateName=LAUNCH_TEMPLATE, VersionDescription=RELEASE_1,
            LaunchTemplateData={'ImageId': IMAGE_ID, 'InstanceType': INSTANCE_TYPE, 'UserData': user_data(RELEASE_1)},
            TagSpecifications=[{'ResourceType': 'launch-template', 'Tags': [LAB_TAG]}])
    autoscaling = _client('autoscaling')
    if not _group():
        zones = [zone['ZoneName'] for zone in ec2.describe_availability_zones().get('AvailabilityZones', [])][:2]
        autoscaling.create_auto_scaling_group(
            AutoScalingGroupName=GROUP, LaunchTemplate={'LaunchTemplateName': LAUNCH_TEMPLATE, 'Version': '$Latest'},
            MinSize=DESIRED, MaxSize=SCALED, DesiredCapacity=DESIRED, AvailabilityZones=zones or ['us-east-1a'],
            HealthCheckGracePeriod=30,
            Tags=[{**APP_TAG, 'PropagateAtLaunch': True}, {**LAB_TAG, 'PropagateAtLaunch': True}])
    instances = _wait_in_service(DESIRED)
    _wait_installed([item['InstanceId'] for item in instances])
    response = {'AutoScalingGroup': GROUP, 'Instances': [
        {key: item.get(key) for key in ('InstanceId', 'LifecycleState', 'AvailabilityZone', 'LaunchTemplate')}
        for item in instances]}
    verified, message = _check_fleet()
    return _result('create-fleet', FLEET_COMMAND, response, verified, message, started)


def _rollout(release: str, require_secrets: bool = False) -> tuple[list[dict[str, Any]], int]:
    """Release one instance at a time and stop at the first failure (MaxConcurrency 1, MaxErrors 0)."""
    instance_ids = [item['InstanceId'] for item in _in_service()]
    runs: list[dict[str, Any]] = []
    batches = plan_rolling(instance_ids)
    for index, batch in enumerate(batches):
        for instance_id in batch:
            runs.append(_run_shell(instance_id, release_script(release, require_secrets)))
        if any(run['Status'] != 'Success' for run in runs):
            skipped = sum(len(rest) for rest in batches[index + 1:])
            return runs, skipped
    return runs, 0


def step_rolling_release() -> dict[str, Any]:
    started = time.perf_counter()
    if len(_in_service()) < DESIRED:
        verified, message = _check_fleet()
        return _result('rolling-release', ROLLING_COMMAND, {}, False, message, started)
    runs, skipped = _rollout(RELEASE_2)
    _marker('rolling-release', {'runs': runs, 'skipped': skipped})
    verified, message = _check_rolling()
    return _result('rolling-release', ROLLING_COMMAND, {'Runs': runs, 'SkippedInstances': skipped}, verified,
                   message, started)


def step_failed_release() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_rolling()
    if not ok:
        return _result('failed-release', FAILED_COMMAND, {}, False, message, started)
    runs, skipped = _rollout(RELEASE_3, require_secrets=True)
    failed = next((run for run in runs if run['Status'] != 'Success'), None)
    rollback = _run_shell(failed['InstanceId'], rollback_script()) if failed else None
    _marker('failed-release', {'failed': failed, 'skipped': skipped, 'rollback': rollback})
    response = {'Runs': runs, 'SkippedInstances': skipped, 'Rollback': rollback}
    verified, message = _check_failed()
    return _result('failed-release', FAILED_COMMAND, response, verified, message, started)


def step_scale_out_drift() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_rolling()
    if not ok:
        return _result('scale-out-drift', DRIFT_COMMAND, {}, False, message, started)
    _client('autoscaling').set_desired_capacity(AutoScalingGroupName=GROUP, DesiredCapacity=SCALED,
                                               HonorCooldown=False)
    instances = _wait_in_service(SCALED)
    _wait_installed([item['InstanceId'] for item in instances])
    releases = _releases([item['InstanceId'] for item in instances])
    groups = drifted(releases)
    _marker('scale-out-drift', {'releases': releases, 'releases_by_version': groups})
    response = {'DesiredCapacity': SCALED, 'Releases': releases, 'ByRelease': groups}
    verified, message = _check_drift()
    return _result('scale-out-drift', DRIFT_COMMAND, response, verified, message, started)


APPSPEC = (
    'version: 0.0\nos: linux\nfiles:\n  - source: /\n    destination: ' + APP_DIR + '\n'
    'hooks:\n  ApplicationStop:\n    - location: scripts/stop.sh\n      timeout: 60\n'
    '  ApplicationStart:\n    - location: scripts/start.sh\n      timeout: 60\n'
    '  ValidateService:\n    - location: scripts/validate.sh\n      timeout: 60\n'
)


def step_diagnose_codedeploy() -> dict[str, Any]:
    started = time.perf_counter()
    if not _group():
        verified, message = _check_fleet()
        return _result('diagnose-codedeploy', CODEDEPLOY_COMMAND, {}, False, message, started)
    codedeploy = _client('codedeploy')
    try:
        codedeploy.create_application(applicationName=APPLICATION, computePlatform='Server')
    except ClientError as exc:
        if 'AlreadyExists' not in _code(exc):
            raise
    settings = dict(
        serviceRoleArn=CODEDEPLOY_ROLE_ARN, deploymentConfigName='CodeDeployDefault.OneAtATime',
        ec2TagFilters=[{'Key': APP_TAG['Key'], 'Value': APP_TAG['Value'], 'Type': 'KEY_AND_VALUE'}],
        autoRollbackConfiguration={'enabled': True, 'events': ['DEPLOYMENT_FAILURE']},
        deploymentStyle={'deploymentType': 'IN_PLACE', 'deploymentOption': 'WITHOUT_TRAFFIC_CONTROL'})
    try:
        codedeploy.create_deployment_group(applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP,
                                           **settings)
    except ClientError as exc:
        if 'AlreadyExists' not in _code(exc):
            raise
        codedeploy.update_deployment_group(applicationName=APPLICATION, currentDeploymentGroupName=DEPLOYMENT_GROUP,
                                           **settings)
    deployment_id = codedeploy.create_deployment(
        applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP,
        description=f'In-place {RELEASE_2} (fleet release lab)',
        revision={'revisionType': 'AppSpecContent', 'appSpecContent': {'content': APPSPEC}})['deploymentId']
    deployment: dict[str, Any] = {}

    def done() -> bool:
        deployment.update(_safe_call(lambda: codedeploy.get_deployment(deploymentId=deployment_id)[
            'deploymentInfo']) or {})
        return deployment.get('status') in ('Succeeded', 'Failed', 'Stopped')
    _wait(done, timeout=60, delay=1)

    running = [instance for reservation in _client('ec2').describe_instances(Filters=[
        {'Name': f'tag:{APP_TAG["Key"]}', 'Values': [APP_TAG['Value']]},
        {'Name': 'instance-state-name', 'Values': ['running']}]).get('Reservations', [])
        for instance in reservation.get('Instances', [])]
    running_ids = {instance['InstanceId'] for instance in running}
    managed = _safe_call(lambda: _client('ssm').describe_instance_information().get(
        'InstanceInformationList', []), []) or []
    managed_ids = {item.get('InstanceId') for item in managed if item.get('PingStatus') == 'Online'}
    group = _safe_call(lambda: codedeploy.get_deployment_group(
        applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP)['deploymentGroupInfo']) or {}
    checklist = no_instances_checklist(len(running_ids), len(running_ids & managed_ids),
                                       bool(group.get('serviceRoleArn')))
    _marker('diagnose-codedeploy', {'deploymentId': deployment_id, 'checklist': checklist})
    response = {'DeploymentId': deployment_id, 'Status': deployment.get('status'),
                'ErrorInformation': deployment.get('errorInformation'), 'Checklist': checklist}
    verified, message = _check_codedeploy()
    return _result('diagnose-codedeploy', CODEDEPLOY_COMMAND, response, verified, message, started)


def step_instance_refresh() -> dict[str, Any]:
    started = time.perf_counter()
    template = _launch_template()
    if not template or not _group():
        verified, message = _check_fleet()
        return _result('instance-refresh', REFRESH_COMMAND, {}, False, message, started)
    ec2 = _client('ec2')
    if int(template.get('LatestVersionNumber') or 1) < 2:
        ec2.create_launch_template_version(LaunchTemplateName=LAUNCH_TEMPLATE, SourceVersion='1',
                                           VersionDescription=RELEASE_2,
                                           LaunchTemplateData={'UserData': user_data(RELEASE_2)})
    autoscaling = _client('autoscaling')
    before = {item['InstanceId'] for item in _in_service()}
    try:
        refresh_id = autoscaling.start_instance_refresh(
            AutoScalingGroupName=GROUP, Strategy='Rolling',
            Preferences={'MinHealthyPercentage': 50, 'InstanceWarmup': 10})['InstanceRefreshId']
    except ClientError as exc:
        if _code(exc) != 'InstanceRefreshInProgress':
            raise
        refresh_id = autoscaling.describe_instance_refreshes(AutoScalingGroupName=GROUP)['InstanceRefreshes'][0][
            'InstanceRefreshId']
    refresh: dict[str, Any] = {}

    def done() -> bool:
        items = autoscaling.describe_instance_refreshes(AutoScalingGroupName=GROUP,
                                                        InstanceRefreshIds=[refresh_id]).get('InstanceRefreshes', [])
        refresh.update(items[0] if items else {})
        return refresh.get('Status') in ('Successful', 'Failed', 'Cancelled')
    _wait(done, timeout=GROUP_TIMEOUT)
    desired = int((_group() or {}).get('DesiredCapacity') or DESIRED)
    instances = _wait_in_service(desired, timeout=60)
    _wait_installed([item['InstanceId'] for item in instances])
    releases = _releases([item['InstanceId'] for item in instances])
    _marker('instance-refresh', {'releases': releases})
    response = {
        'InstanceRefresh': {key: refresh.get(key) for key in ('InstanceRefreshId', 'Status', 'PercentageComplete')},
        'Replaced': sorted(before - {item['InstanceId'] for item in instances}),
        'Instances': [{'InstanceId': item['InstanceId'],
                       'LaunchTemplateVersion': (item.get('LaunchTemplate') or {}).get('Version')}
                      for item in instances],
        'Releases': releases,
    }
    verified, message = _check_refresh()
    return _result('instance-refresh', REFRESH_COMMAND, response, verified, message, started)


def step_stack_rollback() -> dict[str, Any]:
    started = time.perf_counter()
    cf = _client('cloudformation')
    stack = _stack()
    if stack and stack.get('StackStatus') not in ('CREATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE'):
        cf.delete_stack(StackName=STACK)
        _wait(lambda: _stack() is None, timeout=60)
        stack = None
    if not stack:
        cf.create_stack(StackName=STACK, TemplateBody=json.dumps(STACK_V1), Tags=[LAB_TAG])
        _wait_stack()
    cf.update_stack(StackName=STACK, TemplateBody=json.dumps(STACK_BROKEN))
    stack = _wait_stack() or {}
    events = cf.describe_stack_events(StackName=STACK).get('StackEvents', [])
    cause = root_cause(events)
    parameter = _parameter()
    _marker('stack-rollback', {'status': stack.get('StackStatus'), 'rootCause': cause,
                               'parameterAfterRollback': parameter})
    response = {
        'StackStatus': stack.get('StackStatus'),
        'RootCause': cause,
        'Events': [{key: event.get(key) for key in ('LogicalResourceId', 'ResourceStatus', 'ResourceStatusReason')}
                   for event in events[:8]],
        'ConfigVersionAfterRollback': parameter,
    }
    verified, message = _check_rollback()
    return _result('stack-rollback', ROLLBACK_COMMAND, response, verified, message, started)


def step_fix_stack() -> dict[str, Any]:
    started = time.perf_counter()
    stack = _stack()
    if not stack:
        verified, message = _check_fix()
        return _result('fix-stack', FIX_COMMAND, {}, False, f'Run the rollback step first. {message}', started)
    if stack.get('StackStatus') != 'UPDATE_COMPLETE':
        _client('cloudformation').update_stack(StackName=STACK, TemplateBody=json.dumps(STACK_FIXED))
    stack = _wait_stack() or {}
    response = {'StackStatus': stack.get('StackStatus'), 'ConfigVersion': _parameter(),
                'Queue': _safe_call(lambda: _client('sqs').get_queue_url(QueueName=EVENTS_QUEUE)['QueueUrl'])}
    verified, message = _check_fix()
    return _result('fix-stack', FIX_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-fleet': step_create_fleet,
    'rolling-release': step_rolling_release,
    'failed-release': step_failed_release,
    'scale-out-drift': step_scale_out_drift,
    'diagnose-codedeploy': step_diagnose_codedeploy,
    'instance-refresh': step_instance_refresh,
    'stack-rollback': step_stack_rollback,
    'fix-stack': step_fix_stack,
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
    except (ClientError, BotoCoreError, KeyError, IndexError, ValueError):
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
    """Remove the stack, the CodeDeploy application, the Auto Scaling group with its instances, and the launch
    template."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'DoesNotExist' in code or code in ('ValidationError', 'NoSuchEntity'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    if _safe_call(_stack):
        attempt(f'stack:{STACK}', lambda: _client('cloudformation').delete_stack(StackName=STACK))
        _wait(lambda: _safe_call(_stack) is None, timeout=60)

    codedeploy = _client('codedeploy')
    if _safe_call(lambda: codedeploy.get_application(applicationName=APPLICATION)):
        attempt(f'deployment-group:{DEPLOYMENT_GROUP}', lambda: codedeploy.delete_deployment_group(
            applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP))
        attempt(f'application:{APPLICATION}', lambda: codedeploy.delete_application(applicationName=APPLICATION))

    autoscaling = _client('autoscaling')
    group = _safe_call(_group)
    if group:
        instance_ids = [item['InstanceId'] for item in group.get('Instances', [])]
        attempt(f'auto-scaling-group:{GROUP}', lambda: autoscaling.delete_auto_scaling_group(
            AutoScalingGroupName=GROUP, ForceDelete=True))
        _wait(lambda: _safe_call(_group) is None, timeout=GROUP_TIMEOUT)
        if instance_ids:
            _safe_call(lambda: _client('ec2').terminate_instances(InstanceIds=instance_ids))
    if _safe_call(_launch_template):
        attempt(f'launch-template:{LAUNCH_TEMPLATE}', lambda: _client('ec2').delete_launch_template(
            LaunchTemplateName=LAUNCH_TEMPLATE))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws cloudformation delete-stack --stack-name {STACK}\n'
            f'aws deploy delete-application --application-name {APPLICATION}\n'
            f'aws autoscaling delete-auto-scaling-group --auto-scaling-group-name {GROUP} --force-delete\n'
            f'aws ec2 delete-launch-template --launch-template-name {LAUNCH_TEMPLATE}'
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
            else 'The stack, CodeDeploy application, Auto Scaling group, instances, and launch template are gone.',
        },
    }
