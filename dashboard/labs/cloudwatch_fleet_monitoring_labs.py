"""Fleet monitoring lab: a CloudWatch agent configuration in Parameter Store, memory, disk, and process metrics collected
from two instances in the agent's CWAgent namespace, an alarm per instance that notifies an SNS topic and asks EC2 to
reboot, a system status alarm with the recover action, a composite fleet alarm, a process failure caught and cleared,
an ECS service scaled out by an alarm through Application Auto Scaling, and a dashboard that shows two Regions and a
second account.

Floci 2.4.0 runs the instances as real containers and runs Run Command scripts in them, evaluates metric and composite
alarms, delivers alarm notifications through SNS to SQS, and runs Application Auto Scaling step policies when their
alarm fires, changing the ECS service's desired count. What Floci does not do: it has no CloudWatch agent (the lab
collects the same metrics with Run Command and publishes them as the agent would), it publishes no AWS/EC2 metrics, it
stores EC2 alarm actions (reboot, recover) without applying them, and it sends no alarm state change events to
EventBridge. Dashboards are stored and returned as written, including widgets for other Regions and accounts.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'cloudwatch'
LAB_KEY = 'fleet-monitoring'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
OTHER_REGION = 'us-west-2'
ACCOUNT = '000000000000'
SHARED_ACCOUNT = '111122223333'

IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't3.micro'
APP = 'lab-fleet-monitoring'
INSTANCE_NAMES = ('web-1', 'web-2')
FAILING = 'web-2'
PROCESS = 'lab-web-server'

AGENT_PARAMETER = 'AmazonCloudWatch-lab-fleet'
AGENT_NAMESPACE = 'CWAgent'
MEM_METRIC = 'mem_used_percent'
DISK_METRIC = 'disk_used_percent'
PROC_METRIC = 'procstat_lookup_pid_count'
AGENT_CONFIG = {
    'agent': {'metrics_collection_interval': 60, 'run_as_user': 'cwagent'},
    'metrics': {
        'namespace': AGENT_NAMESPACE,
        'append_dimensions': {'InstanceId': '${aws:InstanceId}'},
        'metrics_collected': {
            'mem': {'measurement': ['mem_used_percent']},
            'disk': {'measurement': ['used_percent'], 'resources': ['/'], 'drop_device': True},
            'procstat': [{'pattern': PROCESS, 'measurement': ['pid_count']}],
        },
    },
    'logs': {'logs_collected': {'files': {'collect_list': [
        {'file_path': '/var/log/lab-web-server.log', 'log_group_name': f'/{APP}/web', 'log_stream_name': '{instance_id}'},
    ]}}},
}

TOPIC = 'lab-fleet-ops-alerts'
QUEUE = 'lab-fleet-ops-inbox'
DOWN_ALARMS = {name: f'lab-fleet-{name}-web-down' for name in INSTANCE_NAMES}
RECOVER_ALARMS = {name: f'lab-fleet-{name}-system-check' for name in INSTANCE_NAMES}
FLEET_ALARM = 'lab-fleet-degraded'
FLEET_RULE = ' OR '.join(f'ALARM("{DOWN_ALARMS[name]}")' for name in INSTANCE_NAMES)
REBOOT_ACTION = f'arn:aws:automate:{REGION}:ec2:reboot'
RECOVER_ACTION = f'arn:aws:automate:{REGION}:ec2:recover'

CLUSTER = 'lab-fleet-ops'
WORKER_SERVICE = 'report-worker'
TASK_FAMILY = 'lab-fleet-report-worker'
WORKER_IMAGE = 'public.ecr.aws/docker/library/ubuntu:22.04'
RESOURCE_ID = f'service/{CLUSTER}/{WORKER_SERVICE}'
DIMENSION = 'ecs:service:DesiredCount'
MIN_WORKERS, MAX_WORKERS = 1, 4
SCALE_POLICY = 'lab-fleet-backlog-scale-out'
BACKLOG_NAMESPACE = 'LabFleet'
BACKLOG_METRIC = 'ReportBacklog'
BACKLOG_ALARM = 'lab-fleet-backlog-high'
BACKLOG_THRESHOLD = 100
BACKLOG_SPIKE = 250
STEP_ADJUSTMENTS = [
    {'MetricIntervalLowerBound': 0, 'MetricIntervalUpperBound': 100, 'ScalingAdjustment': 1},
    {'MetricIntervalLowerBound': 100, 'ScalingAdjustment': 2},
]
COOLDOWN_SECONDS = 300

DASHBOARD = 'lab-fleet-operations'

COMMAND_TIMEOUT = 60
BOOT_TIMEOUT = 150
ALARM_WAIT_SECONDS = 120
SCALE_WAIT_SECONDS = 120

FLOCI = 'How Floci emulates this: '

# The agent would read these every minute. Run Command reads them when a step runs. The pattern is anchored, so neither
# the shell that runs the script (whose command line also names the process) nor a killed process left as a zombie
# (shown as [lab-web-server], since the instance's init does not reap it) is counted.
COLLECT_SCRIPT = (
    "awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{printf \"mem=%.1f\\n\", (t-a)*100/t}' /proc/meminfo\n"
    "df -P / | awk 'NR==2{sub(\"%\",\"\",$5); print \"disk=\" $5}'\n"
    f"echo \"pids=$(ps -eo args= | grep -c '^{PROCESS}')\""
)
# The process runs a copy of sleep under the server's name. Renaming a process from inside (Perl's $0) doesn't show in
# ps when Docker emulates the instance's architecture, as Docker Desktop on Apple silicon does for x86 AMIs.
START_SCRIPT = (
    f"cp /bin/sleep /usr/local/bin/{PROCESS}\n"
    f"nohup {PROCESS} 86400 >/dev/null 2>&1 &\n"
    f"sleep 1; echo \"pids=$(ps -eo args= | grep -c '^{PROCESS}')\""
)
STOP_SCRIPT = f"pkill -f '^{PROCESS}' || true\nsleep 1\necho \"pids=$(ps -eo args= | grep -c '^{PROCESS}')\""


def user_data() -> str:
    """Boot script: start the web server process the alarms watch."""
    script = f'#!/bin/sh\n{START_SCRIPT}\n'
    return base64.b64encode(script.encode()).decode()


def queue_policy(queue_arn: str, topic_arn: str) -> dict[str, Any]:
    return {
        'Version': '2012-10-17',
        'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'sns.amazonaws.com'}, 'Action': 'sqs:SendMessage',
                       'Resource': queue_arn, 'Condition': {'ArnEquals': {'aws:SourceArn': topic_arn}}}],
    }


def dashboard_body(instances: dict[str, str], alarm_arns: list[str]) -> dict[str, Any]:
    """Widgets for the fleet in this Region, a twin fleet in another Region, and the shared services account."""
    memory = [[AGENT_NAMESPACE, MEM_METRIC, 'InstanceId', instance_id, {'label': name}]
              for name, instance_id in instances.items()]
    processes = [[AGENT_NAMESPACE, PROC_METRIC, 'InstanceId', instance_id, 'pattern', PROCESS, 'pid_finder', 'native',
                  {'label': name}] for name, instance_id in instances.items()]
    return {
        'widgets': [
            {'type': 'text', 'x': 0, 'y': 0, 'width': 24, 'height': 2,
             'properties': {'markdown': f'# {APP}\nAgent metrics, process alarms, and the report workers.'}},
            {'type': 'metric', 'x': 0, 'y': 2, 'width': 12, 'height': 6,
             'properties': {'title': 'Memory used (%)', 'region': REGION, 'stat': 'Average', 'period': 60,
                            'view': 'timeSeries', 'metrics': memory}},
            {'type': 'metric', 'x': 12, 'y': 2, 'width': 12, 'height': 6,
             'properties': {'title': f'{PROCESS} processes', 'region': REGION, 'stat': 'Minimum', 'period': 60,
                            'view': 'singleValue', 'metrics': processes}},
            {'type': 'alarm', 'x': 0, 'y': 8, 'width': 12, 'height': 4,
             'properties': {'title': 'Fleet alarms', 'alarms': alarm_arns}},
            {'type': 'metric', 'x': 12, 'y': 8, 'width': 12, 'height': 4,
             'properties': {'title': 'Report backlog', 'region': REGION, 'stat': 'Maximum', 'period': 60,
                            'metrics': [[BACKLOG_NAMESPACE, BACKLOG_METRIC]]}},
            {'type': 'metric', 'x': 0, 'y': 12, 'width': 12, 'height': 6,
             'properties': {'title': f'Standby fleet memory ({OTHER_REGION})', 'region': OTHER_REGION,
                            'stat': 'Average', 'period': 300, 'metrics': [[AGENT_NAMESPACE, MEM_METRIC]]}},
            {'type': 'metric', 'x': 12, 'y': 12, 'width': 12, 'height': 6,
             'properties': {'title': f'Shared services account {SHARED_ACCOUNT}', 'region': REGION,
                            'stat': 'Sum', 'period': 300,
                            'metrics': [['AWS/ApplicationELB', 'HTTPCode_Target_5XX_Count', 'LoadBalancer',
                                         'app/lab-shared-alb/0123456789abcdef', {'accountId': SHARED_ACCOUNT}]]}},
        ],
    }


def parse_collection(stdout: str) -> dict[str, float]:
    """Read mem=, disk=, and pids= lines from the collection script."""
    values: dict[str, float] = {}
    for line in stdout.splitlines():
        name, _, value = line.strip().partition('=')
        try:
            values[name] = float(value)
        except ValueError:
            continue
    return values


def agent_metric_data(instance_id: str, values: dict[str, float]) -> list[dict[str, Any]]:
    """The datapoints the agent would publish for one instance, with the agent's own dimensions."""
    data = []
    if 'mem' in values:
        data.append({'MetricName': MEM_METRIC, 'Unit': 'Percent', 'Value': values['mem'],
                     'Dimensions': [{'Name': 'InstanceId', 'Value': instance_id}]})
    if 'disk' in values:
        data.append({'MetricName': DISK_METRIC, 'Unit': 'Percent', 'Value': values['disk'],
                     'Dimensions': [{'Name': 'InstanceId', 'Value': instance_id}, {'Name': 'path', 'Value': '/'}]})
    if 'pids' in values:
        data.append({'MetricName': PROC_METRIC, 'Unit': 'None', 'Value': values['pids'],
                     'Dimensions': [{'Name': 'InstanceId', 'Value': instance_id}, {'Name': 'pattern', 'Value': PROCESS},
                                    {'Name': 'pid_finder', 'Value': 'native'}]})
    return data


def notifications(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Alarm name and new state from SNS envelopes delivered to SQS."""
    found = []
    for message in messages:
        try:
            envelope = json.loads(message.get('Body') or '')
            alarm = json.loads(envelope.get('Message') or '')
        except (TypeError, ValueError):
            continue
        if isinstance(alarm, dict) and alarm.get('AlarmName'):
            found.append({'alarm': alarm['AlarmName'], 'state': alarm.get('NewStateValue', ''),
                          'reason': alarm.get('NewStateReason', '')})
    return found


def desired_after_step(current: int, metric: float) -> int:
    """The desired count the step policy sets for a metric value, within the target's bounds."""
    over = metric - BACKLOG_THRESHOLD
    if over < 0:
        return current
    adjustment = next(step['ScalingAdjustment'] for step in STEP_ADJUSTMENTS
                      if over >= step['MetricIntervalLowerBound']
                      and over < step.get('MetricIntervalUpperBound', float('inf')))
    return max(MIN_WORKERS, min(MAX_WORKERS, current + adjustment))


PROC_DIMENSIONS = 'Name=pattern,Value=lab-web-server Name=pid_finder,Value=native'

AGENT_COMMAND = (
    f'aws ssm put-parameter --name {AGENT_PARAMETER} --type String --overwrite --value file://agent-config.json\n'
    f'aws sns create-topic --name {TOPIC}\n'
    f'aws sqs create-queue --queue-name {QUEUE}\n'
    f'aws sqs set-queue-attributes --queue-url <queue-url> --attributes Policy=file://queue-policy.json\n'
    f'aws sns subscribe --topic-arn <topic-arn> --protocol sqs --notification-endpoint <queue-arn>\n'
    '# On AWS: aws ssm send-command --document-name AmazonCloudWatch-ManageAgent --parameters '
    f'action=configure,optionalConfigurationSource=ssm,optionalConfigurationLocation={AGENT_PARAMETER}'
)
FLEET_COMMAND = (
    ''.join(
        f'aws ec2 run-instances --image-id {IMAGE_ID} --instance-type {INSTANCE_TYPE} --user-data file://start-web.sh '
        f'--tag-specifications \'ResourceType=instance,Tags=[{{Key=Name,Value={name}}},{{Key=App,Value={APP}}}]\'\n'
        for name in INSTANCE_NAMES
    )
    + f'aws ssm send-command --document-name AWS-RunShellScript --targets Key=tag:App,Values={APP} '
    f'--parameters \'commands=["pgrep -c -f ^{PROCESS}"]\''
)
COLLECT_COMMAND = (
    f'aws ssm send-command --document-name AWS-RunShellScript --targets Key=tag:App,Values={APP} '
    '--parameters commands=<collect.sh>   # memory, disk, and process count, as the agent reads them\n'
    f'aws cloudwatch put-metric-data --namespace {AGENT_NAMESPACE} --metric-data '
    f'\'[{{"MetricName":"{MEM_METRIC}","Unit":"Percent","Value":<mem>,'
    '"Dimensions":[{"Name":"InstanceId","Value":"<instance-id>"}]}, ...]\'\n'
    f'aws cloudwatch list-metrics --namespace {AGENT_NAMESPACE}'
)
ALARMS_COMMAND = (
    ''.join(
        f'aws cloudwatch put-metric-alarm --alarm-name {DOWN_ALARMS[name]} --namespace {AGENT_NAMESPACE} '
        f'--metric-name {PROC_METRIC} --dimensions Name=InstanceId,Value=<{name}-id> {PROC_DIMENSIONS} '
        '--statistic Minimum --period 60 --evaluation-periods 1 --threshold 1 --comparison-operator LessThanThreshold '
        f'--treat-missing-data ignore --alarm-actions <topic-arn> {REBOOT_ACTION} --ok-actions <topic-arn>\n'
        for name in INSTANCE_NAMES
    )
    + ''.join(
        f'aws cloudwatch put-metric-alarm --alarm-name {RECOVER_ALARMS[name]} --namespace AWS/EC2 '
        f'--metric-name StatusCheckFailed_System --dimensions Name=InstanceId,Value=<{name}-id> --statistic Maximum '
        '--period 60 --evaluation-periods 2 --threshold 1 --comparison-operator GreaterThanOrEqualToThreshold '
        f'--alarm-actions {RECOVER_ACTION} <topic-arn>\n'
        for name in INSTANCE_NAMES
    )
    + f'aws cloudwatch put-composite-alarm --alarm-name {FLEET_ALARM} --alarm-rule \'{FLEET_RULE}\' '
    '--alarm-actions <topic-arn>'
)
FAILURE_COMMAND = (
    f'aws ssm send-command --document-name AWS-RunShellScript --instance-ids <{FAILING}-id> '
    f'--parameters \'commands=["pkill -f ^{PROCESS}"]\'   # the web server crashes\n'
    'aws ssm send-command ... commands=<collect.sh>  &&  aws cloudwatch put-metric-data ...\n'
    f'aws cloudwatch describe-alarms --alarm-names {" ".join(DOWN_ALARMS.values())} {FLEET_ALARM} '
    '--alarm-types MetricAlarm CompositeAlarm\n'
    f'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10\n'
    f'aws ec2 describe-instances --instance-ids <{FAILING}-id>   # did the reboot action run?'
)
RESTORE_COMMAND = (
    f'aws ssm send-command --document-name AWS-RunShellScript --instance-ids <{FAILING}-id> '
    '--parameters commands=<start-web.sh>\n'
    'aws ssm send-command ... commands=<collect.sh>  &&  aws cloudwatch put-metric-data ...\n'
    f'aws cloudwatch describe-alarm-history --alarm-name {DOWN_ALARMS[FAILING]} --history-item-type StateUpdate\n'
    f'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10'
)
SCALE_COMMAND = (
    f'aws ecs create-cluster --cluster-name {CLUSTER}\n'
    f'aws ecs register-task-definition --family {TASK_FAMILY} --requires-compatibilities FARGATE '
    '--network-mode awsvpc --cpu 256 --memory 512 --container-definitions file://worker.json\n'
    f'aws ecs create-service --cluster {CLUSTER} --service-name {WORKER_SERVICE} --task-definition {TASK_FAMILY} '
    f'--desired-count {MIN_WORKERS} --launch-type FARGATE --network-configuration file://network.json\n'
    f'aws application-autoscaling register-scalable-target --service-namespace ecs --resource-id {RESOURCE_ID} '
    f'--scalable-dimension {DIMENSION} --min-capacity {MIN_WORKERS} --max-capacity {MAX_WORKERS}\n'
    f'aws application-autoscaling put-scaling-policy --policy-name {SCALE_POLICY} --service-namespace ecs '
    f'--resource-id {RESOURCE_ID} --scalable-dimension {DIMENSION} --policy-type StepScaling '
    '--step-scaling-policy-configuration file://steps.json\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {BACKLOG_ALARM} --namespace {BACKLOG_NAMESPACE} '
    f'--metric-name {BACKLOG_METRIC} --statistic Maximum --period 60 --evaluation-periods 1 '
    f'--threshold {BACKLOG_THRESHOLD} --comparison-operator GreaterThanThreshold --alarm-actions <policy-arn>\n'
    f'aws cloudwatch put-metric-data --namespace {BACKLOG_NAMESPACE} --metric-name {BACKLOG_METRIC} '
    f'--value {BACKLOG_SPIKE}\n'
    f'aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id {RESOURCE_ID}'
)
DASHBOARD_COMMAND = (
    f'aws cloudwatch put-dashboard --dashboard-name {DASHBOARD} --dashboard-body file://dashboard.json\n'
    f'aws cloudwatch get-dashboard --dashboard-name {DASHBOARD}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Monitor an EC2 fleet with agent metrics, alarm actions, alarm-driven scaling, and a shared dashboard',
    'description': (
        'Store a CloudWatch agent configuration in Parameter Store, collect memory, disk, and process metrics from two '
        'instances, alarm on a crashed web server with SNS and EC2 actions, combine the alarms in a composite fleet '
        'alarm, catch a failure and clear it, scale an ECS service from an alarm with Application Auto Scaling, and '
        'build a dashboard that shows two Regions and a second account.'
    ),
    'steps': [
        {
            'key': 'agent-config',
            'title': 'Store the CloudWatch agent configuration and an alert channel',
            'command': AGENT_COMMAND,
            'explanation': (
                'EC2 reports CPU, network, and disk I/O by itself, but not memory, disk space, or whether a process '
                'is running: those need the CloudWatch agent. The agent reads a JSON configuration, here from the '
                f'Parameter Store parameter {AGENT_PARAMETER}: memory used, disk used on /, and a procstat count of '
                f'{PROCESS} processes, all in the {AGENT_NAMESPACE} namespace with the InstanceId dimension, plus a '
                'log file. Keeping it in Parameter Store lets Systems Manager (the AmazonCloudWatch-ManageAgent '
                'document) push one configuration to the whole fleet. Alarms will notify the '
                f'{TOPIC} SNS topic, and an SQS queue subscribed to it stands in for the on-call inbox. '
                + FLOCI + 'the parameter, topic, queue, and subscription are real. Floci has no agent and no '
                'AmazonCloudWatch-ManageAgent document, so later steps collect the same values with Run Command.'
            ),
        },
        {
            'key': 'launch-fleet',
            'title': 'Launch two instances running the web server',
            'command': FLEET_COMMAND,
            'explanation': (
                f'Two instances, {INSTANCE_NAMES[0]} and {INSTANCE_NAMES[1]}, are tagged App={APP}, and their user '
                f'data starts the {PROCESS} process. The step waits until Run Command can reach each instance and '
                'counts the process. On AWS the instances would need an instance profile with the '
                'CloudWatchAgentServerPolicy and AmazonSSMManagedInstanceCore policies, so the agent can publish and '
                'Systems Manager can manage them. '
                + FLOCI + 'the instances are real containers and Run Command runs the scripts in them without an SSM '
                'agent. User data runs some seconds after the instance reports running, and now and then not at all, '
                'so the step waits for it and starts the server with Run Command if it never appears.'
            ),
        },
        {
            'key': 'collect-metrics',
            'title': 'Collect agent metrics from both instances',
            'command': COLLECT_COMMAND,
            'explanation': (
                'The step reads memory used from /proc/meminfo, disk used from df, and the process count with '
                'ps on each instance, which is what the agent\'s mem, disk, and procstat plugins read, and '
                f'publishes them to {AGENT_NAMESPACE} with the dimensions the agent uses (InstanceId, path, pattern, '
                'pid_finder). ListMetrics then shows three metrics per instance. The agent would do this every '
                'minute on its own. '
                + FLOCI + 'the values come from the instances and the metrics are real; the lab plays the agent, '
                'because Floci cannot run it.'
            ),
        },
        {
            'key': 'process-alarms',
            'title': 'Alarm on a stopped web server, with SNS, reboot, and recover actions',
            'command': ALARMS_COMMAND,
            'explanation': (
                f'Each instance gets an alarm such as {DOWN_ALARMS[INSTANCE_NAMES[0]]}: fewer than one {PROCESS} '
                'process in a minute is ALARM. Its actions notify the topic and reboot the instance (the EC2 reboot action, '
                f'{REBOOT_ACTION}), and its OK action notifies the topic when it recovers. Missing data is ignored '
                'here, because the lab collects only when a step runs; with the agent publishing every minute you '
                'would treat missing data as breaching, so a dead agent pages too. A second alarm per instance '
                'watches StatusCheckFailed_System and uses the recover action, which moves the instance to new '
                f'hardware with the same ID and IP. The composite alarm {FLEET_ALARM} is ALARM when either web '
                'server is down, so one page covers the fleet. '
                + FLOCI + 'the alarms evaluate for real and both go to OK from the collected data. Floci stores the '
                'reboot and recover actions but never applies them, and publishes no AWS/EC2 metrics, so the system '
                'check alarms stay INSUFFICIENT_DATA.'
            ),
        },
        {
            'key': 'process-failure',
            'title': f'Crash the web server on {FAILING} and follow the alarm',
            'command': FAILURE_COMMAND,
            'explanation': (
                f'The step kills {PROCESS} on {FAILING} and collects again. Its alarm goes into ALARM, so does '
                f'{FLEET_ALARM}, and the other instance\'s alarm stays OK. The on-call queue receives the SNS '
                'notifications, which name the alarm, the new state, and the reason. On AWS the reboot action '
                'would restart the instance and its user data would start the web server again. On AWS every state '
                'change is also a CloudWatch Alarm State Change event on the default event bus, which an '
                'EventBridge rule could send to a Lambda function or an Automation runbook instead. '
                + FLOCI + 'the alarms and the notifications are real. Floci does not apply the reboot action (the '
                'instance keeps running, without its web server) and sends no alarm state change events.'
            ),
        },
        {
            'key': 'restore-service',
            'title': f'Restart the web server on {FAILING} and watch the alarms clear',
            'command': RESTORE_COMMAND,
            'explanation': (
                'Because the reboot did not happen, an operator starts the process with Run Command, which is what '
                'the reboot would have achieved through user data. After the next collection the alarm and the '
                'composite alarm return to OK, the OK action sends a notification, and the alarm history shows each '
                'state change with its reason, which is where you look when an alarm flaps. '
                + FLOCI + 'the restart, the OK state, the OK notification, and the alarm history are real.'
            ),
        },
        {
            'key': 'scale-from-alarm',
            'title': 'Scale an ECS service out from a backlog alarm',
            'command': SCALE_COMMAND,
            'explanation': (
                f'Report workers run as the ECS service {WORKER_SERVICE}, registered with Application Auto Scaling '
                f'between {MIN_WORKERS} and {MAX_WORKERS} tasks. A step scaling policy adds one task when the backlog '
                f'is up to 100 over the {BACKLOG_THRESHOLD} threshold and two tasks beyond that, with a '
                f'{COOLDOWN_SECONDS}-second cooldown. The alarm {BACKLOG_ALARM} calls the policy directly: alarm '
                f'actions can invoke scaling policies, not just notify. A backlog of {BACKLOG_SPIKE} is 150 over, so '
                f'the service goes from {MIN_WORKERS} to {desired_after_step(MIN_WORKERS, BACKLOG_SPIKE)} tasks, and '
                'the scaling activity names the alarm as its cause. Target tracking would do the same with less '
                'setup, by keeping a metric (such as backlog per task) at a target. '
                + FLOCI + 'the alarm runs the policy and the desired count really changes, with a scaling activity. '
                'Floci re-runs the policy on every evaluation while the alarm stays in ALARM, with the cooldown '
                'deciding whether it scales again.'
            ),
        },
        {
            'key': 'shared-dashboard',
            'title': 'Build a dashboard across Regions and accounts',
            'command': DASHBOARD_COMMAND,
            'explanation': (
                'One dashboard shows the fleet: memory and process counts per instance, the alarms, and the report '
                f'backlog. One widget reads the standby fleet in {OTHER_REGION} (a widget names its own Region) and '
                f'another reads a load balancer in the shared services account {SHARED_ACCOUNT} (a metric names its '
                'accountId). On AWS, cross-account widgets need CloudWatch cross-account observability: the '
                'monitoring account has a sink and each source account links to it. Share the dashboard with chosen '
                'people, through SSO, or publicly, and keep its JSON in source control to recreate it. '
                + FLOCI + 'the dashboard is stored and returned exactly as written, including the other Region and '
                'account; Floci does not render widgets or check that the metrics exist.'
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


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Floci lookups ────────────────────────────────────────────────────────────

def _parameter() -> dict[str, Any] | None:
    value = _safe_call(lambda: _client('ssm').get_parameter(Name=AGENT_PARAMETER)['Parameter']['Value'])
    try:
        return json.loads(value) if value else None
    except ValueError:
        return None


def _topic_arn() -> str | None:
    topics = _safe_call(lambda: _client('sns').list_topics()['Topics'], [])
    return next((item['TopicArn'] for item in topics if item['TopicArn'].endswith(f':{TOPIC}')), None)


def _queue_url() -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=QUEUE)['QueueUrl'])


def _queue_arn(url: str) -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_attributes(QueueUrl=url, AttributeNames=['QueueArn'])
                      ['Attributes']['QueueArn'])


def _subscribed(topic_arn: str, queue_arn: str) -> bool:
    subscriptions = _safe_call(lambda: _client('sns').list_subscriptions_by_topic(TopicArn=topic_arn)
                               ['Subscriptions'], [])
    return any(item.get('Endpoint') == queue_arn and item.get('Protocol') == 'sqs' for item in subscriptions)


def _instances() -> dict[str, dict[str, Any]]:
    """Live lab instances by Name tag."""
    reservations = _safe_call(lambda: _client('ec2').describe_instances(Filters=[
        {'Name': 'tag:App', 'Values': [APP]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running', 'stopping', 'stopped']},
    ])['Reservations'], [])
    found = {}
    for reservation in reservations:
        for instance in reservation.get('Instances', []):
            name = next((tag['Value'] for tag in instance.get('Tags', []) if tag['Key'] == 'Name'), None)
            if name in INSTANCE_NAMES:
                found[name] = instance
    return found


def _instance_ids() -> dict[str, str]:
    return {name: item['InstanceId'] for name, item in _instances().items()}


def _run_shell(instance_id: str, script: str) -> dict[str, Any]:
    """Run a script through Run Command and return the finished invocation."""
    ssm = _client('ssm')
    command_id = ssm.send_command(InstanceIds=[instance_id], DocumentName='AWS-RunShellScript',
                                  Parameters={'commands': [script]}, Comment=LAB_KEY)['Command']['CommandId']
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
    return {'InstanceId': instance_id, 'Status': outcome.get('Status'),
            'Stdout': (outcome.get('StandardOutputContent') or '').strip(),
            'Stderr': (outcome.get('StandardErrorContent') or '').strip()}


def _alarm_states() -> dict[str, str]:
    names = [*DOWN_ALARMS.values(), *RECOVER_ALARMS.values(), FLEET_ALARM, BACKLOG_ALARM]
    response = _safe_call(lambda: _client('cloudwatch').describe_alarms(
        AlarmNames=names, AlarmTypes=['MetricAlarm', 'CompositeAlarm']), {})
    alarms = (response.get('MetricAlarms') or []) + (response.get('CompositeAlarms') or [])
    return {alarm['AlarmName']: alarm['StateValue'] for alarm in alarms}


def _alarms() -> dict[str, dict[str, Any]]:
    names = [*DOWN_ALARMS.values(), *RECOVER_ALARMS.values(), FLEET_ALARM, BACKLOG_ALARM]
    response = _safe_call(lambda: _client('cloudwatch').describe_alarms(
        AlarmNames=names, AlarmTypes=['MetricAlarm', 'CompositeAlarm']), {})
    alarms = (response.get('MetricAlarms') or []) + (response.get('CompositeAlarms') or [])
    return {alarm['AlarmName']: alarm for alarm in alarms}


def _wait_states(expected: dict[str, str], timeout: float = ALARM_WAIT_SECONDS) -> dict[str, str]:
    states: dict[str, str] = {}

    def reached() -> bool:
        states.clear()
        states.update(_alarm_states())
        return all(states.get(name) == state for name, state in expected.items())

    _wait(reached, timeout, delay=3)
    return states


def _agent_metrics() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('cloudwatch').list_metrics(Namespace=AGENT_NAMESPACE)['Metrics'], [])


def _drain_inbox() -> list[dict[str, str]]:
    url = _queue_url()
    if not url:
        return []
    sqs, found = _client('sqs'), []
    for _ in range(5):
        messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=1).get('Messages', [])
        if not messages:
            break
        found.extend(notifications(messages))
        for message in messages:
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message['ReceiptHandle'])
    return found


def _service() -> dict[str, Any] | None:
    services = _safe_call(lambda: _client('ecs').describe_services(cluster=CLUSTER, services=[WORKER_SERVICE])
                          ['services'], [])
    return next((item for item in services if item.get('status') == 'ACTIVE'), None)


def _scalable_target() -> dict[str, Any] | None:
    targets = _safe_call(lambda: _client('application-autoscaling').describe_scalable_targets(
        ServiceNamespace='ecs', ResourceIds=[RESOURCE_ID])['ScalableTargets'], [])
    return targets[0] if targets else None


def _policy_arn() -> str | None:
    policies = _safe_call(lambda: _client('application-autoscaling').describe_scaling_policies(
        ServiceNamespace='ecs', ResourceId=RESOURCE_ID, PolicyNames=[SCALE_POLICY])['ScalingPolicies'], [])
    return policies[0]['PolicyARN'] if policies else None


def _dashboard() -> dict[str, Any] | None:
    body = _safe_call(lambda: _client('cloudwatch').get_dashboard(DashboardName=DASHBOARD)['DashboardBody'])
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def _collect(instances: dict[str, str]) -> dict[str, dict[str, float]]:
    """Read each instance's values and publish them as the agent would."""
    cloudwatch, collected = _client('cloudwatch'), {}
    for name, instance_id in instances.items():
        run = _run_shell(instance_id, COLLECT_SCRIPT)
        values = parse_collection(run['Stdout']) if run['Status'] == 'Success' else {}
        collected[name] = values
        data = agent_metric_data(instance_id, values)
        if data:
            cloudwatch.put_metric_data(Namespace=AGENT_NAMESPACE, MetricData=data)
    return collected


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_agent_config() -> tuple[bool, str]:
    config = _parameter()
    if not config:
        return False, f'{AGENT_PARAMETER} does not hold an agent configuration yet.'
    collected = (config.get('metrics') or {}).get('metrics_collected') or {}
    if not {'mem', 'disk', 'procstat'} <= set(collected):
        return False, f'{AGENT_PARAMETER} does not collect memory, disk, and procstat.'
    topic, url = _topic_arn(), _queue_url()
    if not topic or not url:
        return False, f'Create the {TOPIC} topic and the {QUEUE} queue.'
    queue_arn = _queue_arn(url)
    if not queue_arn or not _subscribed(topic, queue_arn):
        return False, f'{QUEUE} is not subscribed to {TOPIC} yet.'
    return True, f'The agent configuration is in {AGENT_PARAMETER}, and {QUEUE} receives {TOPIC} alerts.'


def _check_fleet() -> tuple[bool, str]:
    marker = _marked('launch-fleet') or {}
    counts = marker.get('processes') or {}
    if set(counts) != set(INSTANCE_NAMES):
        return False, 'Launch both instances first.'
    if any(count < 1 for count in counts.values()):
        return False, f'{PROCESS} is not running on every instance: {counts}.'
    live = _instance_ids()
    if any(live.get(name) != instance_id for name, instance_id in (marker.get('instances') or {}).items()):
        return False, 'The lab instances have changed; launch the fleet again.'
    return True, f'Both instances run {PROCESS}, and Run Command reaches them.'


def _check_collected() -> tuple[bool, str]:
    marker = _marked('collect-metrics') or {}
    values = marker.get('values') or {}
    if set(values) != set(INSTANCE_NAMES) or any({'mem', 'disk', 'pids'} - set(item) for item in values.values()):
        return False, 'Collect memory, disk, and process counts from both instances first.'
    names = {metric['MetricName'] for metric in _agent_metrics()}
    missing = {MEM_METRIC, DISK_METRIC, PROC_METRIC} - names
    if missing:
        return False, f'{AGENT_NAMESPACE} has no {", ".join(sorted(missing))} metrics yet.'
    return True, f'{AGENT_NAMESPACE} holds memory, disk, and process metrics for both instances.'


def _check_alarms() -> tuple[bool, str]:
    alarms = _alarms()
    topic = _topic_arn()
    for name in INSTANCE_NAMES:
        down = alarms.get(DOWN_ALARMS[name]) or {}
        if topic not in (down.get('AlarmActions') or []) or REBOOT_ACTION not in (down.get('AlarmActions') or []):
            return False, f'{DOWN_ALARMS[name]} does not notify {TOPIC} and reboot the instance.'
        recover = alarms.get(RECOVER_ALARMS[name]) or {}
        if RECOVER_ACTION not in (recover.get('AlarmActions') or []):
            return False, f'{RECOVER_ALARMS[name]} does not use the recover action.'
    if FLEET_ALARM not in alarms:
        return False, f'The {FLEET_ALARM} composite alarm does not exist yet.'
    states = (_marked('process-alarms') or {}).get('states') or {}
    healthy = [DOWN_ALARMS[name] for name in INSTANCE_NAMES] + [FLEET_ALARM]
    if any(states.get(name) != 'OK' for name in healthy):
        return False, f'The web server alarms did not settle at OK: {states or "not checked"}.'
    return True, f'Both web server alarms and {FLEET_ALARM} are OK, with SNS, reboot, and recover actions in place.'


def _check_failure() -> tuple[bool, str]:
    marker = _marked('process-failure') or {}
    states = marker.get('states') or {}
    if states.get(DOWN_ALARMS[FAILING]) != 'ALARM' or states.get(FLEET_ALARM) != 'ALARM':
        return False, f'{DOWN_ALARMS[FAILING]} and {FLEET_ALARM} are not in ALARM: {states or "not checked"}.'
    healthy = next(name for name in INSTANCE_NAMES if name != FAILING)
    if states.get(DOWN_ALARMS[healthy]) != 'OK':
        return False, f'{DOWN_ALARMS[healthy]} fired too, but {healthy} is healthy.'
    paged = {(item['alarm'], item['state']) for item in marker.get('notifications') or []}
    if (DOWN_ALARMS[FAILING], 'ALARM') not in paged:
        return False, f'No ALARM notification for {DOWN_ALARMS[FAILING]} reached {QUEUE}.'
    return True, f'{FAILING}\'s alarm and {FLEET_ALARM} fired, and the on-call queue was notified.'


def _check_restored() -> tuple[bool, str]:
    marker = _marked('restore-service') or {}
    states = marker.get('states') or {}
    if states.get(DOWN_ALARMS[FAILING]) != 'OK' or states.get(FLEET_ALARM) != 'OK':
        return False, f'{DOWN_ALARMS[FAILING]} and {FLEET_ALARM} are not back to OK: {states or "not checked"}.'
    paged = {(item['alarm'], item['state']) for item in marker.get('notifications') or []}
    if (DOWN_ALARMS[FAILING], 'OK') not in paged:
        return False, f'No OK notification for {DOWN_ALARMS[FAILING]} reached {QUEUE}.'
    return True, f'{PROCESS} runs on {FAILING} again, the alarms are OK, and the OK notification arrived.'


def _check_scaled() -> tuple[bool, str]:
    service = _service()
    if not service:
        return False, f'The {WORKER_SERVICE} service does not exist yet.'
    target = _scalable_target()
    if not target or (target.get('MinCapacity'), target.get('MaxCapacity')) != (MIN_WORKERS, MAX_WORKERS):
        return False, f'{WORKER_SERVICE} is not registered with Application Auto Scaling ({MIN_WORKERS}-{MAX_WORKERS}).'
    policy = _policy_arn()
    alarm = _alarms().get(BACKLOG_ALARM) or {}
    if not policy or policy not in (alarm.get('AlarmActions') or []):
        return False, f'{BACKLOG_ALARM} does not invoke {SCALE_POLICY}.'
    expected = desired_after_step(MIN_WORKERS, BACKLOG_SPIKE)
    if service.get('desiredCount', 0) < expected:
        return False, f'{WORKER_SERVICE} wants {service.get("desiredCount")} tasks, expected {expected}.'
    return True, f'{BACKLOG_ALARM} ran {SCALE_POLICY}, and {WORKER_SERVICE} scaled to {service["desiredCount"]} tasks.'


def _check_dashboard() -> tuple[bool, str]:
    body = _dashboard()
    if not body:
        return False, f'The {DASHBOARD} dashboard does not exist yet.'
    widgets = body.get('widgets') or []
    regions = {(widget.get('properties') or {}).get('region') for widget in widgets}
    accounts = {options.get('accountId')
                for widget in widgets for metric in (widget.get('properties') or {}).get('metrics') or []
                for options in metric if isinstance(options, dict)}
    if OTHER_REGION not in regions:
        return False, f'No widget reads {OTHER_REGION}.'
    if SHARED_ACCOUNT not in accounts:
        return False, f'No widget reads account {SHARED_ACCOUNT}.'
    if not any(widget.get('type') == 'alarm' for widget in widgets):
        return False, 'The dashboard has no alarm widget.'
    return True, f'{DASHBOARD} shows the fleet, its alarms, {OTHER_REGION}, and account {SHARED_ACCOUNT}.'


CHECKS = {
    'agent-config': _check_agent_config,
    'launch-fleet': _check_fleet,
    'collect-metrics': _check_collected,
    'process-alarms': _check_alarms,
    'process-failure': _check_failure,
    'restore-service': _check_restored,
    'scale-from-alarm': _check_scaled,
    'shared-dashboard': _check_dashboard,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def step_agent_config() -> dict[str, Any]:
    started = time.perf_counter()
    _client('ssm').put_parameter(Name=AGENT_PARAMETER, Type='String', Overwrite=True,
                                 Value=json.dumps(AGENT_CONFIG), Description='CloudWatch agent configuration')
    topic = _client('sns').create_topic(Name=TOPIC)['TopicArn']
    sqs = _client('sqs')
    url = sqs.create_queue(QueueName=QUEUE)['QueueUrl']
    queue_arn = _queue_arn(url)
    sqs.set_queue_attributes(QueueUrl=url, Attributes={'Policy': json.dumps(queue_policy(queue_arn, topic))})
    if not _subscribed(topic, queue_arn):
        _client('sns').subscribe(TopicArn=topic, Protocol='sqs', Endpoint=queue_arn)
    response = {'Parameter': AGENT_PARAMETER, 'AgentConfig': AGENT_CONFIG, 'TopicArn': topic, 'QueueArn': queue_arn}
    verified, message = _check_agent_config()
    return _result('agent-config', AGENT_COMMAND, response, verified, message, started)


def step_launch_fleet() -> dict[str, Any]:
    started = time.perf_counter()
    ec2 = _client('ec2')
    instances = _instance_ids()
    for name in INSTANCE_NAMES:
        if name in instances:
            continue
        launched = ec2.run_instances(
            ImageId=IMAGE_ID, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1, UserData=user_data(),
            TagSpecifications=[{'ResourceType': 'instance', 'Tags': [
                {'Key': 'Name', 'Value': name}, {'Key': 'App', 'Value': APP}, {'Key': 'Lab', 'Value': LAB_KEY}]}])
        instances[name] = launched['Instances'][0]['InstanceId']
    counts: dict[str, float] = {}

    def serving() -> bool:
        for name, instance_id in instances.items():
            if counts.get(name, 0) >= 1:
                continue
            run = _run_shell(instance_id, COLLECT_SCRIPT)
            counts[name] = parse_collection(run['Stdout']).get('pids', 0) if run['Status'] == 'Success' else 0
        return all(counts.get(name, 0) >= 1 for name in INSTANCE_NAMES)

    _wait(serving, BOOT_TIMEOUT, delay=3)
    # Floci sometimes runs an instance's user data late or not at all; start the server the same way user data would.
    started_by_command = []
    for name, instance_id in instances.items():
        if counts.get(name, 0) < 1:
            run = _run_shell(instance_id, START_SCRIPT)
            if run['Status'] == 'Success':
                counts[name] = parse_collection(run['Stdout']).get('pids', 0)
                started_by_command.append(name)
    _marker('launch-fleet', {'instances': instances, 'processes': counts})
    states = {name: item.get('State', {}).get('Name') for name, item in _instances().items()}
    response = {'Instances': instances, 'States': states, 'WebServerProcesses': counts,
                'StartedByRunCommand': started_by_command}
    verified, message = _check_fleet()
    return _result('launch-fleet', FLEET_COMMAND, response, verified, message, started)


def _fleet_or_error(step: str, command: str, started: float) -> tuple[dict[str, str] | None, dict[str, Any] | None]:
    instances = _instance_ids()
    if set(instances) != set(INSTANCE_NAMES):
        return None, _result(step, command, {}, False, 'Launch the fleet first.', started)
    return instances, None


def step_collect_metrics() -> dict[str, Any]:
    started = time.perf_counter()
    instances, error = _fleet_or_error('collect-metrics', COLLECT_COMMAND, started)
    if error:
        return error
    values = _collect(instances)
    _marker('collect-metrics', {'values': values})
    metrics = sorted({(item['MetricName'], next((d['Value'] for d in item.get('Dimensions', [])
                                                  if d['Name'] == 'InstanceId'), ''))
                      for item in _agent_metrics()})
    response = {'Collected': values, 'Metrics': [{'MetricName': name, 'InstanceId': iid} for name, iid in metrics]}
    verified, message = _check_collected()
    return _result('collect-metrics', COLLECT_COMMAND, response, verified, message, started)


def step_process_alarms() -> dict[str, Any]:
    started = time.perf_counter()
    instances, error = _fleet_or_error('process-alarms', ALARMS_COMMAND, started)
    if error:
        return error
    topic = _topic_arn()
    if not topic:
        return _result('process-alarms', ALARMS_COMMAND, {}, False, f'Create the {TOPIC} topic first.', started)
    cloudwatch = _client('cloudwatch')
    for name, instance_id in instances.items():
        cloudwatch.put_metric_alarm(
            AlarmName=DOWN_ALARMS[name], AlarmDescription=f'{PROCESS} is not running on {name}',
            Namespace=AGENT_NAMESPACE, MetricName=PROC_METRIC, Statistic='Minimum', Period=60, EvaluationPeriods=1,
            Threshold=1, ComparisonOperator='LessThanThreshold', TreatMissingData='ignore',
            Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}, {'Name': 'pattern', 'Value': PROCESS},
                        {'Name': 'pid_finder', 'Value': 'native'}],
            AlarmActions=[topic, REBOOT_ACTION], OKActions=[topic])
        cloudwatch.put_metric_alarm(
            AlarmName=RECOVER_ALARMS[name], AlarmDescription=f'Recover {name} when the host fails',
            Namespace='AWS/EC2', MetricName='StatusCheckFailed_System', Statistic='Maximum', Period=60,
            EvaluationPeriods=2, Threshold=1, ComparisonOperator='GreaterThanOrEqualToThreshold',
            Dimensions=[{'Name': 'InstanceId', 'Value': instance_id}], AlarmActions=[RECOVER_ACTION, topic])
    cloudwatch.put_composite_alarm(AlarmName=FLEET_ALARM, AlarmRule=FLEET_RULE, AlarmActions=[topic],
                                   AlarmDescription='At least one web server is down')
    _collect(instances)
    expected = {DOWN_ALARMS[name]: 'OK' for name in INSTANCE_NAMES}
    states = _wait_states({**expected, FLEET_ALARM: 'OK'})
    _drain_inbox()   # OK notifications from the first evaluation
    _marker('process-alarms', {'states': states})
    alarms = _alarms()
    response = {'States': states, 'Actions': {name: alarms.get(name, {}).get('AlarmActions')
                                              for name in [*DOWN_ALARMS.values(), *RECOVER_ALARMS.values()]},
                'FleetRule': FLEET_RULE}
    verified, message = _check_alarms()
    return _result('process-alarms', ALARMS_COMMAND, response, verified, message, started)


def step_process_failure() -> dict[str, Any]:
    started = time.perf_counter()
    instances, error = _fleet_or_error('process-failure', FAILURE_COMMAND, started)
    if error:
        return error
    if DOWN_ALARMS[FAILING] not in _alarm_states():
        return _result('process-failure', FAILURE_COMMAND, {}, False, 'Create the alarms first.', started)
    stopped = _run_shell(instances[FAILING], STOP_SCRIPT)
    collected = _collect(instances)
    healthy = next(name for name in INSTANCE_NAMES if name != FAILING)
    states = _wait_states({DOWN_ALARMS[FAILING]: 'ALARM', FLEET_ALARM: 'ALARM', DOWN_ALARMS[healthy]: 'OK'})
    paged: list[dict[str, str]] = []

    def notified() -> bool:
        paged.extend(_drain_inbox())
        return any(item['alarm'] == DOWN_ALARMS[FAILING] and item['state'] == 'ALARM' for item in paged)

    _wait(notified, 30, delay=2)
    instance_state = (_instances().get(FAILING) or {}).get('State', {}).get('Name')
    _marker('process-failure', {'states': states, 'notifications': paged})
    response = {'Stopped': stopped, 'Collected': collected, 'States': states, 'Notifications': paged,
                f'{FAILING}State': instance_state}
    verified, message = _check_failure()
    if verified and instance_state == 'running':
        message += f' Floci did not apply the reboot action: {FAILING} is still running without its web server.'
    return _result('process-failure', FAILURE_COMMAND, response, verified, message, started)


def step_restore_service() -> dict[str, Any]:
    started = time.perf_counter()
    instances, error = _fleet_or_error('restore-service', RESTORE_COMMAND, started)
    if error:
        return error
    restarted = _run_shell(instances[FAILING], START_SCRIPT)
    collected = _collect(instances)
    states = _wait_states({DOWN_ALARMS[FAILING]: 'OK', FLEET_ALARM: 'OK'})
    paged: list[dict[str, str]] = []

    def notified() -> bool:
        paged.extend(_drain_inbox())
        return any(item['alarm'] == DOWN_ALARMS[FAILING] and item['state'] == 'OK' for item in paged)

    _wait(notified, 30, delay=2)
    history = _safe_call(lambda: _client('cloudwatch').describe_alarm_history(
        AlarmName=DOWN_ALARMS[FAILING], HistoryItemType='StateUpdate', MaxRecords=10)['AlarmHistoryItems'], [])
    _marker('restore-service', {'states': states, 'notifications': paged})
    response = {'Restarted': restarted, 'Collected': collected, 'States': states, 'Notifications': paged,
                'History': [{key: item.get(key) for key in ('Timestamp', 'HistorySummary')} for item in history]}
    verified, message = _check_restored()
    return _result('restore-service', RESTORE_COMMAND, response, verified, message, started)


def _ensure_worker_service() -> None:
    ecs = _client('ecs')
    ecs.create_cluster(clusterName=CLUSTER)
    if _service():
        return
    task = ecs.register_task_definition(
        family=TASK_FAMILY, networkMode='awsvpc', requiresCompatibilities=['FARGATE'], cpu='256', memory='512',
        containerDefinitions=[{'name': 'worker', 'image': WORKER_IMAGE, 'command': ['sleep', '86400'],
                               'essential': True}])['taskDefinition']['taskDefinitionArn']
    subnets = [item['SubnetId'] for item in _client('ec2').describe_subnets(
        Filters=[{'Name': 'default-for-az', 'Values': ['true']}]).get('Subnets', [])][:2]
    if not subnets:
        subnets = [item['SubnetId'] for item in _client('ec2').describe_subnets().get('Subnets', [])][:1]
    ecs.create_service(cluster=CLUSTER, serviceName=WORKER_SERVICE, taskDefinition=task, desiredCount=MIN_WORKERS,
                       launchType='FARGATE',
                       networkConfiguration={'awsvpcConfiguration': {'subnets': subnets, 'assignPublicIp': 'ENABLED'}})


def step_scale_from_alarm() -> dict[str, Any]:
    started = time.perf_counter()
    _ensure_worker_service()
    scaling = _client('application-autoscaling')
    scaling.register_scalable_target(ServiceNamespace='ecs', ResourceId=RESOURCE_ID, ScalableDimension=DIMENSION,
                                     MinCapacity=MIN_WORKERS, MaxCapacity=MAX_WORKERS)
    policy = scaling.put_scaling_policy(
        PolicyName=SCALE_POLICY, ServiceNamespace='ecs', ResourceId=RESOURCE_ID, ScalableDimension=DIMENSION,
        PolicyType='StepScaling', StepScalingPolicyConfiguration={
            'AdjustmentType': 'ChangeInCapacity', 'Cooldown': COOLDOWN_SECONDS, 'MetricAggregationType': 'Maximum',
            'StepAdjustments': STEP_ADJUSTMENTS})['PolicyARN']
    cloudwatch = _client('cloudwatch')
    cloudwatch.put_metric_alarm(
        AlarmName=BACKLOG_ALARM, AlarmDescription='Report backlog is too high for the workers',
        Namespace=BACKLOG_NAMESPACE, MetricName=BACKLOG_METRIC, Statistic='Maximum', Period=60, EvaluationPeriods=1,
        Threshold=BACKLOG_THRESHOLD, ComparisonOperator='GreaterThanThreshold', TreatMissingData='notBreaching',
        AlarmActions=[policy])
    cloudwatch.put_metric_data(Namespace=BACKLOG_NAMESPACE,
                               MetricData=[{'MetricName': BACKLOG_METRIC, 'Value': BACKLOG_SPIKE, 'Unit': 'Count'}])
    expected = desired_after_step(MIN_WORKERS, BACKLOG_SPIKE)
    _wait(lambda: (_service() or {}).get('desiredCount', 0) >= expected, SCALE_WAIT_SECONDS, delay=3)
    service = _service() or {}
    activities = _safe_call(lambda: scaling.describe_scaling_activities(
        ServiceNamespace='ecs', ResourceId=RESOURCE_ID, MaxResults=5)['ScalingActivities'], [])
    response = {
        'Service': {key: service.get(key) for key in ('serviceName', 'desiredCount', 'runningCount', 'pendingCount')},
        'PolicyARN': policy,
        'AlarmState': _alarm_states().get(BACKLOG_ALARM),
        'Activities': [{key: item.get(key) for key in ('Description', 'Cause', 'StatusCode')} for item in activities],
    }
    verified, message = _check_scaled()
    return _result('scale-from-alarm', SCALE_COMMAND, response, verified, message, started)


def step_shared_dashboard() -> dict[str, Any]:
    started = time.perf_counter()
    instances, error = _fleet_or_error('shared-dashboard', DASHBOARD_COMMAND, started)
    if error:
        return error
    alarms = _alarms()
    arns = [alarms[name]['AlarmArn'] for name in [*DOWN_ALARMS.values(), FLEET_ALARM, BACKLOG_ALARM]
            if name in alarms and alarms[name].get('AlarmArn')]
    body = dashboard_body(instances, arns)
    validation = _client('cloudwatch').put_dashboard(DashboardName=DASHBOARD, DashboardBody=json.dumps(body))
    response = {'Validation': validation.get('DashboardValidationMessages', []), 'Stored': _dashboard()}
    verified, message = _check_dashboard()
    return _result('shared-dashboard', DASHBOARD_COMMAND, response, verified, message, started)


RUNNERS = {
    'agent-config': step_agent_config,
    'launch-fleet': step_launch_fleet,
    'collect-metrics': step_collect_metrics,
    'process-alarms': step_process_alarms,
    'process-failure': step_process_failure,
    'restore-service': step_restore_service,
    'scale-from-alarm': step_scale_from_alarm,
    'shared-dashboard': step_shared_dashboard,
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
    """Remove the dashboard, alarms, scaling setup, ECS service and cluster, instances, topic, queue, and parameter."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or 'DoesNotExist' in code or code in (
                    'UnsupportedOperation', 'ParameterNotFound', 'ObjectNotFoundException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    cloudwatch = _client('cloudwatch')
    if _dashboard() is not None:
        attempt(f'dashboard:{DASHBOARD}', lambda: cloudwatch.delete_dashboards(DashboardNames=[DASHBOARD]))
    states = _alarm_states()
    if FLEET_ALARM in states:
        attempt(f'alarm:{FLEET_ALARM}', lambda: cloudwatch.delete_alarms(AlarmNames=[FLEET_ALARM]))
    existing = [name for name in states if name != FLEET_ALARM]
    if existing:
        attempt('alarms:' + ','.join(existing), lambda: cloudwatch.delete_alarms(AlarmNames=existing))
    scaling = _client('application-autoscaling')
    if _policy_arn():
        attempt(f'scaling-policy:{SCALE_POLICY}', lambda: scaling.delete_scaling_policy(
            PolicyName=SCALE_POLICY, ServiceNamespace='ecs', ResourceId=RESOURCE_ID, ScalableDimension=DIMENSION))
    if _scalable_target():
        attempt(f'scalable-target:{RESOURCE_ID}', lambda: scaling.deregister_scalable_target(
            ServiceNamespace='ecs', ResourceId=RESOURCE_ID, ScalableDimension=DIMENSION))
    ecs = _client('ecs')
    if _service():
        attempt(f'ecs-service:{WORKER_SERVICE}', lambda: ecs.delete_service(
            cluster=CLUSTER, service=WORKER_SERVICE, force=True))
    clusters = _safe_call(lambda: ecs.describe_clusters(clusters=[CLUSTER])['clusters'], [])
    if any(item.get('status') == 'ACTIVE' for item in clusters):
        def delete_cluster() -> None:
            for _ in range(10):
                try:
                    ecs.delete_cluster(cluster=CLUSTER)
                    return
                except ClientError as exc:
                    if 'ClusterContains' not in _code(exc):
                        raise
                    time.sleep(2)
            ecs.delete_cluster(cluster=CLUSTER)
        attempt(f'ecs-cluster:{CLUSTER}', delete_cluster)
    definitions = _safe_call(lambda: ecs.list_task_definitions(familyPrefix=TASK_FAMILY)['taskDefinitionArns'], [])
    for arn in definitions:
        attempt(f'task-definition:{arn.rsplit("/", 1)[-1]}',
                lambda arn=arn: ecs.deregister_task_definition(taskDefinition=arn))
    instances = _instance_ids()
    if instances:
        attempt('instances:' + ','.join(instances.values()),
                lambda: _client('ec2').terminate_instances(InstanceIds=list(instances.values())))
    topic = _topic_arn()
    if topic:
        attempt(f'topic:{TOPIC}', lambda: _client('sns').delete_topic(TopicArn=topic))
    url = _queue_url()
    if url:
        attempt(f'queue:{QUEUE}', lambda: _client('sqs').delete_queue(QueueUrl=url))
    if _parameter() is not None:
        attempt(f'parameter:{AGENT_PARAMETER}', lambda: _client('ssm').delete_parameter(Name=AGENT_PARAMETER))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws cloudwatch delete-dashboards --dashboard-names {DASHBOARD}\n'
            f'aws cloudwatch delete-alarms --alarm-names {FLEET_ALARM}\n'
            f'aws cloudwatch delete-alarms --alarm-names {" ".join(DOWN_ALARMS.values())} '
            f'{" ".join(RECOVER_ALARMS.values())} {BACKLOG_ALARM}\n'
            f'aws application-autoscaling delete-scaling-policy --policy-name {SCALE_POLICY} --service-namespace ecs '
            f'--resource-id {RESOURCE_ID} --scalable-dimension {DIMENSION}\n'
            f'aws application-autoscaling deregister-scalable-target --service-namespace ecs --resource-id '
            f'{RESOURCE_ID} --scalable-dimension {DIMENSION}\n'
            f'aws ecs delete-service --cluster {CLUSTER} --service {WORKER_SERVICE} --force\n'
            f'aws ecs delete-cluster --cluster {CLUSTER}\n'
            f'aws ecs deregister-task-definition --task-definition {TASK_FAMILY}:<revision>\n'
            'aws ec2 terminate-instances --instance-ids <web-1-id> <web-2-id>\n'
            'aws sns delete-topic --topic-arn <topic-arn>\n'
            'aws sqs delete-queue --queue-url <queue-url>\n'
            f'aws ssm delete-parameter --name {AGENT_PARAMETER}'
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
            else 'The dashboard, alarms, scaling setup, ECS service, instances, topic, queue, and parameter are gone.',
        },
    }
