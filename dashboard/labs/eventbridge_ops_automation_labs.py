"""Event-driven operations lab: a custom event bus with an archive, a rule that never fires debugged with
TestEventPattern, an input transformer that turns an event into a readable alert, the missed event replayed from the
archive, a broken target found through its dead-letter queue, a Systems Manager Automation runbook, the runbook's
steps run by a Lambda function through Run Command, and a scheduled cleanup with EventBridge Scheduler.

Floci 2.4.0 runs rules, input transformers, archives and replays, sends events a target could not take to the target's
dead-letter queue with the error in message attributes, runs the Lambda function (which reaches Floci's Run Command
and SQS), runs Run Command on the instance, and fires schedules. What Floci does not do: it stores Automation
documents but cannot start an Automation execution, it does not support numeric matching in event patterns (only exact
values, prefixes, equals-ignore-case and the like), it does not run Scheduler's universal targets (an SDK call as the
target), and it publishes no AWS/Events metrics such as MatchedEvents or FailedInvocations.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from datetime import datetime, timedelta, timezone
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'eventbridge'
LAB_KEY = 'ops-automation'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

BUS = 'lab-ops'
BUS_ARN = f'arn:aws:events:{REGION}:{ACCOUNT}:event-bus/{BUS}'
ARCHIVE = 'lab-ops-archive'
ARCHIVE_ARN = f'arn:aws:events:{REGION}:{ACCOUNT}:archive/{ARCHIVE}'
REPLAY_PREFIX = 'lab-ops-replay'
INBOX = 'lab-ops-inbox'
DLQ = 'lab-ops-target-dlq'
RULE = 'lab-ops-disk-high'
RULE_ARN = f'arn:aws:events:{REGION}:{ACCOUNT}:rule/{BUS}/{RULE}'
SOURCE = 'lab.fleet'
DETAIL_TYPE = 'Disk Usage High'

IMAGE_ID = 'ami-ubuntu2204'
INSTANCE_TYPE = 't3.micro'
INSTANCE_NAME = 'lab-ops-web'
CACHE_DIR = '/var/lab-cache'
CACHE_FILES = 5

RUNBOOK = 'LabOps-ClearCache'
FUNCTION = 'lab-ops-clear-cache'
FUNCTION_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
TYPO_FUNCTION = 'lab-ops-clear-cach'
TYPO_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{TYPO_FUNCTION}'
FUNCTION_ROLE = 'lab-ops-runbook-role'
FUNCTION_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FUNCTION_ROLE}'
SCHEDULER_ROLE = 'lab-ops-scheduler-role'
SCHEDULER_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{SCHEDULER_ROLE}'
INLINE_POLICY = 'lab-ops'
SCHEDULE = 'lab-ops-nightly-cleanup'
SCHEDULE_EXPRESSION = 'rate(1 minute)'
PRODUCTION_SCHEDULE = 'cron(0 3 * * ? *)'

# The monitoring agent reports CRITICAL; the rule's author wrote critical.
BROKEN_PATTERN = {'source': [SOURCE], 'detail-type': [DETAIL_TYPE], 'detail': {'severity': ['critical']}}
FIXED_PATTERN = {'source': [SOURCE], 'detail-type': [DETAIL_TYPE],
                 'detail': {'severity': [{'equals-ignore-case': 'critical'}]}}
NUMERIC_PATTERN = {'source': [SOURCE], 'detail-type': [DETAIL_TYPE], 'detail': {'percent': [{'numeric': ['>=', 90]}]}}
INPUT_PATHS = {'name': '$.detail.name', 'instance': '$.detail.instance', 'percent': '$.detail.percent',
               'severity': '$.detail.severity', 'ref': '$.detail.ref'}
INPUT_TEMPLATE = ('"[<severity>] Disk on <name> (<instance>) is <percent>% full. Alert <ref>. '
                  f'Runbook: {RUNBOOK}."')
RETRY_POLICY = {'MaximumRetryAttempts': 2, 'MaximumEventAgeInSeconds': 300}

MISSED_REF = 'disk-1'
ENRICHED_REF = 'disk-2'
TARGET_REF = 'disk-3'
RUNBOOK_REF = 'disk-4'

EVENT_WAIT_SECONDS = 15
COMMAND_TIMEOUT = 60
BOOT_TIMEOUT = 120
RUNBOOK_WAIT_SECONDS = 90
SCHEDULE_WAIT_SECONDS = 150

FLOCI = 'How Floci emulates this: '

FILL_SCRIPT = (
    f'mkdir -p {CACHE_DIR}\n'
    f'for i in $(seq 1 {CACHE_FILES}); do head -c 1048576 /dev/zero > {CACHE_DIR}/blob-$i; done\n'
    f'echo "files=$(ls {CACHE_DIR} | wc -l)"'
)
COUNT_SCRIPT = f'echo "files=$(ls {CACHE_DIR} 2>/dev/null | wc -l)"'
CLEAR_SCRIPT = (
    f'count=$(ls {CACHE_DIR} 2>/dev/null | wc -l)\n'
    f'rm -f {CACHE_DIR}/*\n'
    'echo "cleared=$count"'
)

RUNBOOK_CONTENT = {
    'schemaVersion': '0.3',
    'description': 'Clear the application cache on an instance whose disk is filling up, then confirm.',
    'assumeRole': '{{ AutomationAssumeRole }}',
    'parameters': {
        'InstanceId': {'type': 'String', 'description': 'The instance with the full disk.'},
        'AutomationAssumeRole': {'type': 'String', 'default': ''},
    },
    'mainSteps': [
        {'name': 'ClearCache', 'action': 'aws:runCommand', 'maxAttempts': 2, 'onFailure': 'Abort',
         'inputs': {'DocumentName': 'AWS-RunShellScript', 'InstanceIds': ['{{ InstanceId }}'],
                    'Parameters': {'commands': [CLEAR_SCRIPT]}}},
        {'name': 'Confirm', 'action': 'aws:runCommand',
         'inputs': {'DocumentName': 'AWS-RunShellScript', 'InstanceIds': ['{{ InstanceId }}'],
                    'Parameters': {'commands': [COUNT_SCRIPT]}}},
    ],
}

FUNCTION_SOURCE = '''import json
import os
import time

import boto3

CLEAR_SCRIPT = os.environ["CLEAR_SCRIPT"]


def run(ssm, instance_id, script):
    command_id = ssm.send_command(InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
                                  Parameters={"commands": [script]}, Comment=os.environ["RUNBOOK"])["Command"]["CommandId"]
    for _ in range(40):
        time.sleep(1)
        try:
            result = ssm.get_command_invocation(CommandId=command_id, InstanceId=instance_id)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if result["Status"] not in ("Pending", "InProgress", "Delayed"):
            return result["Status"], result.get("StandardOutputContent", "").strip()
    return "TimedOut", ""


def handler(event, context):
    detail = event.get("detail") or event
    instance_id = detail["instance"]
    ssm = boto3.client("ssm")
    status, output = run(ssm, instance_id, CLEAR_SCRIPT)
    cleared = output.partition("cleared=")[2].strip() or "0"
    report = {"runbook": os.environ["RUNBOOK"], "step": "ClearCache", "instance": instance_id,
              "status": status, "cleared": int(cleared) if cleared.isdigit() else 0,
              "trigger": detail.get("task") or detail.get("ref") or "event"}
    sqs = boto3.client("sqs")
    url = sqs.get_queue_url(QueueName=os.environ["INBOX"])["QueueUrl"]
    sqs.send_message(QueueUrl=url, MessageBody=json.dumps(report))
    return report
'''


def _trust(service: str) -> dict[str, Any]:
    return {'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole'}]}


FUNCTION_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'RunTheRunbookSteps', 'Effect': 'Allow', 'Action': ['ssm:SendCommand'],
         'Resource': [f'arn:aws:ec2:{REGION}:{ACCOUNT}:instance/*',
                      f'arn:aws:ssm:{REGION}::document/AWS-RunShellScript',
                      f'arn:aws:ssm:{REGION}:{ACCOUNT}:*']},
        {'Sid': 'ReadTheResult', 'Effect': 'Allow', 'Action': ['ssm:GetCommandInvocation'], 'Resource': '*'},
        {'Sid': 'Report', 'Effect': 'Allow', 'Action': ['sqs:GetQueueUrl', 'sqs:SendMessage'],
         'Resource': f'arn:aws:sqs:{REGION}:{ACCOUNT}:{INBOX}'},
    ],
}
SCHEDULER_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Action': 'lambda:InvokeFunction', 'Resource': FUNCTION_ARN}],
}


def queue_policy(queue_arn: str) -> dict[str, Any]:
    """Only this account's rules on the ops bus may send to the queue."""
    return {
        'Version': '2012-10-17',
        'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'events.amazonaws.com'}, 'Action': 'sqs:SendMessage',
                       'Resource': queue_arn,
                       'Condition': {'ArnLike': {'aws:SourceArn': f'arn:aws:events:{REGION}:{ACCOUNT}:rule/{BUS}/*'}}}],
    }


def disk_event(instance_id: str, ref: str, percent: int) -> dict[str, Any]:
    """The event the monitoring agent sends when a disk passes 90 percent."""
    return {'name': INSTANCE_NAME, 'instance': instance_id, 'mount': '/', 'percent': percent, 'severity': 'CRITICAL',
            'ref': ref}


def sample_event(detail: dict[str, Any]) -> dict[str, Any]:
    """A full event envelope for TestEventPattern."""
    return {'version': '0', 'id': 'sample', 'source': SOURCE, 'detail-type': DETAIL_TYPE, 'account': ACCOUNT,
            'time': '2026-11-04T12:00:00Z', 'region': REGION, 'resources': [], 'detail': detail}


def render_alert(detail: dict[str, Any]) -> str:
    """What the input transformer should deliver for an event."""
    return (f'[{detail["severity"]}] Disk on {detail["name"]} ({detail["instance"]}) is {detail["percent"]}% full. '
            f'Alert {detail["ref"]}. Runbook: {RUNBOOK}.')


def parse_body(body: str) -> Any:
    try:
        return json.loads(body)
    except (TypeError, ValueError):
        return body


def alert_refs(bodies: list[Any]) -> list[str]:
    """Alert refs named in the transformed messages."""
    refs = []
    for body in bodies:
        if isinstance(body, str) and ' Alert ' in body:
            refs.append(body.split(' Alert ', 1)[1].split('.', 1)[0])
    return refs


def dead_letters(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Target, error code and message EventBridge attaches to a dead-lettered event."""
    found = []
    for message in messages:
        attributes = message.get('MessageAttributes') or {}
        found.append({name: (attributes.get(name) or {}).get('StringValue', '')
                      for name in ('TARGET_ARN', 'RULE_ARN', 'ERROR_CODE', 'ERROR_MESSAGE', 'RETRY_ATTEMPTS')})
    return found


SETUP_COMMAND = (
    f'aws events create-event-bus --name {BUS}\n'
    f'aws events create-archive --archive-name {ARCHIVE} --event-source-arn {BUS_ARN} --retention-days 7\n'
    f'aws sqs create-queue --queue-name {INBOX}   # with a policy for events.amazonaws.com\n'
    f'aws sqs create-queue --queue-name {DLQ}\n'
    f'aws ec2 run-instances --image-id {IMAGE_ID} --instance-type {INSTANCE_TYPE} '
    f'--tag-specifications \'ResourceType=instance,Tags=[{{Key=Name,Value={INSTANCE_NAME}}}]\'\n'
    'aws ssm send-command --document-name AWS-RunShellScript --instance-ids <instance-id> '
    f'--parameters commands=<fill {CACHE_DIR}>'
)
SILENT_COMMAND = (
    f'aws events put-rule --name {RULE} --event-bus-name {BUS} --event-pattern '
    f'\'{json.dumps(BROKEN_PATTERN)}\'\n'
    f'aws events put-targets --rule {RULE} --event-bus-name {BUS} --targets file://inbox-target.json\n'
    f'aws events put-events --entries \'[{{"EventBusName":"{BUS}","Source":"{SOURCE}","DetailType":"{DETAIL_TYPE}",'
    f'"Detail":"{{\\"severity\\":\\"CRITICAL\\",\\"percent\\":93,\\"ref\\":\\"{MISSED_REF}\\",...}}"}}]\'\n'
    f'aws sqs receive-message --queue-url <{INBOX}-url> --wait-time-seconds 5   # nothing\n'
    f'aws events test-event-pattern --event-pattern \'{json.dumps(BROKEN_PATTERN)}\' --event file://event.json'
)
FIX_COMMAND = (
    f'aws events test-event-pattern --event-pattern \'{json.dumps(FIXED_PATTERN)}\' --event file://event.json\n'
    f'aws events test-event-pattern --event-pattern \'{json.dumps(NUMERIC_PATTERN)}\' --event file://event.json\n'
    f'aws events put-rule --name {RULE} --event-bus-name {BUS} --event-pattern \'{json.dumps(FIXED_PATTERN)}\'\n'
    f'aws events put-events --entries ... ref={ENRICHED_REF}\n'
    f'aws sqs receive-message --queue-url <{INBOX}-url>'
)
REPLAY_COMMAND = (
    f'aws events describe-archive --archive-name {ARCHIVE}\n'
    f'aws events start-replay --replay-name {REPLAY_PREFIX}-<time> --event-source-arn {ARCHIVE_ARN} '
    f'--event-start-time <before {MISSED_REF}> --event-end-time <now> '
    f'--destination Arn={BUS_ARN},FilterArns={RULE_ARN}\n'
    f'aws events describe-replay --replay-name {REPLAY_PREFIX}-<time>\n'
    f'aws sqs receive-message --queue-url <{INBOX}-url>'
)
TARGET_COMMAND = (
    f'aws events put-targets --rule {RULE} --event-bus-name {BUS} --targets \'[{{"Id":"runbook","Arn":"{TYPO_ARN}",'
    f'"RetryPolicy":{json.dumps(RETRY_POLICY)},"DeadLetterConfig":{{"Arn":"<{DLQ}-arn>"}}}}]\'\n'
    f'aws events put-events --entries ... ref={TARGET_REF}\n'
    f'aws sqs receive-message --queue-url <{DLQ}-url> --message-attribute-names All\n'
    f'aws events remove-targets --rule {RULE} --event-bus-name {BUS} --ids runbook'
)
RUNBOOK_COMMAND = (
    f'aws ssm create-document --name {RUNBOOK} --document-type Automation --document-format JSON '
    '--content file://runbook.json\n'
    f'aws ssm describe-document --name {RUNBOOK}\n'
    f'aws ssm start-automation-execution --document-name {RUNBOOK} --parameters InstanceId=<instance-id>'
)
FUNCTION_COMMAND = (
    f'aws iam create-role --role-name {FUNCTION_ROLE} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime python3.12 --handler index.handler '
    f'--role {FUNCTION_ROLE_ARN} --zip-file fileb://runbook.zip\n'
    f'aws lambda add-permission --function-name {FUNCTION} --statement-id events --action lambda:InvokeFunction '
    f'--principal events.amazonaws.com --source-arn {RULE_ARN}\n'
    f'aws events put-targets --rule {RULE} --event-bus-name {BUS} --targets Id=runbook,Arn={FUNCTION_ARN}\n'
    f'aws events put-events --entries ... ref={RUNBOOK_REF}\n'
    f'aws ssm send-command ... commands=["ls {CACHE_DIR} | wc -l"]'
)
SCHEDULE_COMMAND = (
    f'aws ssm send-command ... commands=<fill {CACHE_DIR}>   # the cache grows again\n'
    f'aws iam create-role --role-name {SCHEDULER_ROLE} --assume-role-policy-document file://scheduler-trust.json\n'
    f'aws scheduler create-schedule --name {SCHEDULE} --schedule-expression "{SCHEDULE_EXPRESSION}" '
    f'--flexible-time-window Mode=OFF --target \'{{"Arn":"{FUNCTION_ARN}","RoleArn":"{SCHEDULER_ROLE_ARN}",'
    '"Input":"{\\"instance\\":\\"<instance-id>\\",\\"task\\":\\"nightly-cleanup\\"}"}\'\n'
    f'# in production: --schedule-expression "{PRODUCTION_SCHEDULE}" --schedule-expression-timezone Europe/London\n'
    f'aws sqs receive-message --queue-url <{INBOX}-url>'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Automate operations with EventBridge: debug a silent rule, enrich alerts, replay, and run a runbook',
    'description': (
        'Give an ops event bus an archive, find out why a rule never fires with TestEventPattern, turn the event into '
        'a readable alert with an input transformer, replay the event the broken rule missed, find a broken target '
        'through its dead-letter queue, write a Systems Manager Automation runbook, run its steps from the rule with '
        'a Lambda function and Run Command, and schedule the same cleanup with EventBridge Scheduler.'
    ),
    'steps': [
        {
            'key': 'ops-setup',
            'title': 'Create an ops event bus with an archive, an inbox, and a dead-letter queue',
            'command': SETUP_COMMAND,
            'explanation': (
                f'Operational events from the fleet go to their own event bus, {BUS}, so their rules and permissions '
                f'stay apart from application events. The archive {ARCHIVE} keeps every event on the bus for seven '
                f'days, so missed events can be replayed later. The SQS queue {INBOX} stands in for the on-call '
                f'channel, and {DLQ} will catch events a target cannot take. The instance {INSTANCE_NAME} gets '
                f'{CACHE_FILES} cache files in {CACHE_DIR}, the cause of the disk alert the lab follows. '
                + FLOCI + 'the bus, archive, queues and instance are real, and Run Command writes the files.'
            ),
        },
        {
            'key': 'silent-rule',
            'title': 'Find out why a rule never fires',
            'command': SILENT_COMMAND,
            'explanation': (
                f'The rule {RULE} should send disk alerts to the inbox, but its pattern asks for severity "critical" '
                'and the agent sends "CRITICAL". Pattern matching is exact and case-sensitive, so the alert '
                f'{MISSED_REF} is dropped without an error: the inbox stays empty. TestEventPattern with a sample '
                'event shows the rule does not match. On AWS you would also see the rule\'s MatchedEvents metric '
                'stay at zero while the bus\'s events arrive. '
                + FLOCI + 'the rule, the drop, and TestEventPattern are real. Floci publishes no AWS/Events '
                'metrics, so the step checks the inbox and the pattern instead.'
            ),
        },
        {
            'key': 'fix-and-enrich',
            'title': 'Fix the pattern and deliver a readable alert with an input transformer',
            'command': FIX_COMMAND,
            'explanation': (
                'The fixed pattern uses equals-ignore-case, and TestEventPattern now matches. A numeric pattern '
                '(percent >= 90) would also catch the alert on AWS. The target\'s input transformer picks the '
                'instance name, ID, percentage, severity and alert ref out of the event and builds one sentence '
                f'that names the runbook, so the on-call engineer does not read raw JSON. Alert {ENRICHED_REF} '
                'arrives as that sentence. '
                + FLOCI + 'equals-ignore-case and the input transformer are real. Floci 2.4.0 does not support '
                'numeric matching: TestEventPattern says the numeric pattern does not match, so do not use it here.'
            ),
        },
        {
            'key': 'replay-missed',
            'title': 'Replay the alert the broken rule missed',
            'command': REPLAY_COMMAND,
            'explanation': (
                f'Alert {MISSED_REF} reached the bus while the rule was broken, so the archive kept it. A replay '
                'sends the archived events from a time window back to the bus, and FilterArns limits it to the '
                f'fixed rule, so other rules do not act twice. The inbox now has {MISSED_REF} too. Replayed events '
                'carry a replay-name field, which a target can use to skip work it already did. '
                + FLOCI + 'the archive, the replay and the redelivery are real.'
            ),
        },
        {
            'key': 'failing-target',
            'title': 'Find a broken target through its dead-letter queue',
            'command': TARGET_COMMAND,
            'explanation': (
                f'A second target was added for the runbook function, with a typo in its name ({TYPO_FUNCTION}). '
                f'EventBridge cannot deliver alert {TARGET_REF} to it. With a retry policy '
                f'({RETRY_POLICY["MaximumRetryAttempts"]} retries, {RETRY_POLICY["MaximumEventAgeInSeconds"]} '
                f'seconds) and a dead-letter queue, the event lands in {DLQ} with attributes that say which rule and '
                'target failed and why (ERROR_CODE, ERROR_MESSAGE). Without a dead-letter queue the event would be '
                'lost, and only the FailedInvocations metric would show it. The broken target is then removed. '
                + FLOCI + 'the dead-letter delivery and its attributes are real, and the inbox target still gets '
                'the alert. Floci does not retry a target that does not exist (RETRY_ATTEMPTS is 0), which AWS '
                'also skips for this error.'
            ),
        },
        {
            'key': 'runbook-document',
            'title': 'Write the cleanup as a Systems Manager Automation runbook',
            'command': RUNBOOK_COMMAND,
            'explanation': (
                f'The runbook {RUNBOOK} (an Automation document, schema 0.3) takes an InstanceId, clears the cache '
                'with an aws:runCommand step that retries once, then confirms the cache is empty. An EventBridge '
                'rule, a CloudWatch alarm, a Config remediation, or a maintenance window can start it, and AWS '
                'provides ready-made runbooks such as AWS-RestartEC2Instance. '
                + FLOCI + 'the document is stored and described, but StartAutomationExecution is not supported, so '
                'the next step runs the same steps with Lambda and Run Command.'
            ),
        },
        {
            'key': 'runbook-function',
            'title': 'Run the runbook\'s steps from the rule with Lambda and Run Command',
            'command': FUNCTION_COMMAND,
            'explanation': (
                f'The function {FUNCTION} does what the runbook\'s ClearCache step does: it runs the cleanup on the '
                'instance named in the event with Run Command, waits for the result, and reports to the inbox. Its '
                'role may only send commands to instances with AWS-RunShellScript, read the result, and write to '
                f'the inbox. With the function as the rule\'s second target, alert {RUNBOOK_REF} clears the '
                f'{CACHE_FILES} cache files by itself. '
                + FLOCI + 'the function, the Run Command, the cleanup and the report are real, and Floci checks the '
                'role\'s policy on each call. It matches SendCommand against an SSM resource in the account, so '
                'the policy also lists the account\'s SSM resources next to the instance and document ARNs.'
            ),
        },
        {
            'key': 'scheduled-cleanup',
            'title': 'Schedule the cleanup with EventBridge Scheduler',
            'command': SCHEDULE_COMMAND,
            'explanation': (
                'Waiting for an alert is reactive; a schedule keeps the cache small. EventBridge Scheduler invokes '
                f'the same function with a fixed input. The lab uses {SCHEDULE_EXPRESSION} so the run happens now; in '
                f'production it would be {PRODUCTION_SCHEDULE} in the team\'s time zone. Systems Manager maintenance '
                'windows do the same for Run Command and Automation tasks, with targets, concurrency and error '
                'limits. Scheduler can also call an API directly (a universal target such as ssm:sendCommand). '
                + FLOCI + 'the schedule fires and the function clears the cache. Floci does not run universal '
                'targets or maintenance windows, so the schedule invokes the function.'
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

def _bus() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('events').describe_event_bus(Name=BUS))


def _archive() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('events').describe_archive(ArchiveName=ARCHIVE))


def _queue_url(name: str) -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=name)['QueueUrl'])


def _queue_arn(name: str) -> str:
    return f'arn:aws:sqs:{REGION}:{ACCOUNT}:{name}'


def _instance_id() -> str | None:
    reservations = _safe_call(lambda: _client('ec2').describe_instances(Filters=[
        {'Name': 'tag:Name', 'Values': [INSTANCE_NAME]},
        {'Name': 'instance-state-name', 'Values': ['pending', 'running']},
    ])['Reservations'], [])
    for reservation in reservations:
        for instance in reservation.get('Instances', []):
            return instance['InstanceId']
    return None


def _rule() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('events').describe_rule(Name=RULE, EventBusName=BUS))


def _targets() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('events').list_targets_by_rule(Rule=RULE, EventBusName=BUS)['Targets'], [])


def _function() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('lambda').get_function(FunctionName=FUNCTION)['Configuration'])


def _runbook() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('ssm').describe_document(Name=RUNBOOK)['Document'])


def _schedule() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('scheduler').get_schedule(Name=SCHEDULE))


def _run_shell(instance_id: str, script: str) -> dict[str, Any]:
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
    return {'Status': outcome.get('Status'), 'Stdout': (outcome.get('StandardOutputContent') or '').strip(),
            'Stderr': (outcome.get('StandardErrorContent') or '').strip()}


def _value(stdout: str, name: str) -> int | None:
    for line in stdout.splitlines():
        key, _, value = line.strip().partition('=')
        if key == name and value.strip().isdigit():
            return int(value)
    return None


def _cache_files(instance_id: str) -> int | None:
    run = _run_shell(instance_id, COUNT_SCRIPT)
    return _value(run['Stdout'], 'files') if run['Status'] == 'Success' else None


def _receive(name: str, wait: int = 1, attributes: bool = False) -> list[dict[str, Any]]:
    url = _queue_url(name)
    if not url:
        return []
    sqs, found = _client('sqs'), []
    for _ in range(5):
        kwargs: dict[str, Any] = {'QueueUrl': url, 'MaxNumberOfMessages': 10, 'WaitTimeSeconds': wait}
        if attributes:
            kwargs['MessageAttributeNames'] = ['All']
        messages = sqs.receive_message(**kwargs).get('Messages', [])
        if not messages:
            break
        found.extend(messages)
        for message in messages:
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message['ReceiptHandle'])
    return found


def _inbox_until(predicate, timeout: float) -> list[Any]:
    bodies: list[Any] = []

    def done() -> bool:
        bodies.extend(parse_body(message.get('Body')) for message in _receive(INBOX))
        return predicate(bodies)

    _wait(done, timeout, delay=1)
    return bodies


def _put_disk_event(instance_id: str, ref: str, percent: int) -> dict[str, Any]:
    response = _client('events').put_events(Entries=[{
        'EventBusName': BUS, 'Source': SOURCE, 'DetailType': DETAIL_TYPE,
        'Detail': json.dumps(disk_event(instance_id, ref, percent))}])
    return {'FailedEntryCount': response.get('FailedEntryCount'),
            'EventId': (response.get('Entries') or [{}])[0].get('EventId')}


def _test_pattern(pattern: dict[str, Any], instance_id: str) -> bool | None:
    event = sample_event(disk_event(instance_id or 'i-sample', 'sample', 93))
    return _safe_call(lambda: _client('events').test_event_pattern(
        EventPattern=json.dumps(pattern), Event=json.dumps(event))['Result'])


def _inbox_target() -> dict[str, Any]:
    return {'Id': 'inbox', 'Arn': _queue_arn(INBOX),
            'InputTransformer': {'InputPathsMap': INPUT_PATHS, 'InputTemplate': INPUT_TEMPLATE}}


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_setup() -> tuple[bool, str]:
    if not _bus():
        return False, f'The {BUS} event bus does not exist yet.'
    archive = _archive() or {}
    if archive.get('EventSourceArn') != BUS_ARN:
        return False, f'The {ARCHIVE} archive does not record {BUS} yet.'
    if not _queue_url(INBOX) or not _queue_url(DLQ):
        return False, f'Create the {INBOX} and {DLQ} queues.'
    if (_marked('ops-setup') or {}).get('cacheFiles') != CACHE_FILES:
        return False, f'{INSTANCE_NAME} does not have its {CACHE_FILES} cache files yet.'
    return True, f'{BUS} records to {ARCHIVE}; {INSTANCE_NAME} has {CACHE_FILES} cache files.'


def _check_silent() -> tuple[bool, str]:
    marker = _marked('silent-rule') or {}
    if not marker:
        return False, 'Create the rule and send the first alert.'
    if marker.get('delivered'):
        return False, f'The inbox received {marker["delivered"]} messages; the broken rule should drop the alert.'
    if marker.get('brokenMatches') is not False:
        return False, 'TestEventPattern should show that the broken pattern does not match.'
    return True, f'{MISSED_REF} reached the bus but not the inbox, and TestEventPattern shows why.'


def _check_fixed() -> tuple[bool, str]:
    marker = _marked('fix-and-enrich') or {}
    if marker.get('fixedMatches') is not True:
        return False, 'The fixed pattern does not match the sample event.'
    if json.loads((_rule() or {}).get('EventPattern') or '{}') != FIXED_PATTERN:
        return False, f'{RULE} still has the broken pattern.'
    if marker.get('alert') != marker.get('expected') or not marker.get('alert'):
        return False, f'The inbox did not receive the transformed alert for {ENRICHED_REF}.'
    return True, f'{RULE} matches again and delivered {ENRICHED_REF} as a readable alert.'


def _check_replayed() -> tuple[bool, str]:
    marker = _marked('replay-missed') or {}
    if marker.get('state') != 'COMPLETED':
        return False, f'The replay is {marker.get("state") or "not started"}.'
    if MISSED_REF not in (marker.get('refs') or []):
        return False, f'The replay did not deliver {MISSED_REF} to the inbox.'
    return True, f'The replay delivered the missed alert {MISSED_REF} through the fixed rule.'


def _check_target() -> tuple[bool, str]:
    marker = _marked('failing-target') or {}
    letters = marker.get('deadLetters') or []
    if not any(item.get('TARGET_ARN') == TYPO_ARN and item.get('ERROR_CODE') for item in letters):
        return False, f'No dead letter for {TYPO_ARN} reached {DLQ}.'
    if any(target['Arn'] == TYPO_ARN for target in _targets()):
        return False, f'Remove the broken {TYPO_FUNCTION} target.'
    return True, f'{DLQ} named the broken target and its error, and the target is gone.'


def _check_runbook() -> tuple[bool, str]:
    document = _runbook() or {}
    if document.get('DocumentType') != 'Automation':
        return False, f'{RUNBOOK} is not an Automation document yet.'
    if not (_marked('runbook-document') or {}).get('startOutcome'):
        return False, 'Try to start the runbook.'
    return True, f'{RUNBOOK} is stored as an Automation runbook.'


def _check_function() -> tuple[bool, str]:
    if not _function():
        return False, f'The {FUNCTION} function does not exist yet.'
    if not any(target['Arn'] == FUNCTION_ARN for target in _targets()):
        return False, f'{FUNCTION} is not a target of {RULE}.'
    marker = _marked('runbook-function') or {}
    report = marker.get('report') or {}
    if report.get('trigger') != RUNBOOK_REF or report.get('cleared') != CACHE_FILES:
        return False, f'The runbook did not report clearing {CACHE_FILES} files for {RUNBOOK_REF}: {report or "none"}.'
    if marker.get('filesAfter') != 0:
        return False, f'{CACHE_DIR} still has {marker.get("filesAfter")} files.'
    return True, f'Alert {RUNBOOK_REF} ran the runbook steps and cleared {CACHE_FILES} cache files.'


def _check_schedule() -> tuple[bool, str]:
    schedule = _schedule() or {}
    if (schedule.get('Target') or {}).get('Arn') != FUNCTION_ARN:
        return False, f'{SCHEDULE} does not invoke {FUNCTION}.'
    marker = _marked('scheduled-cleanup') or {}
    report = marker.get('report') or {}
    if report.get('trigger') != 'nightly-cleanup' or not report.get('cleared'):
        return False, f'The schedule has not cleared the cache yet: {report or "no report"}.'
    if marker.get('filesAfter') != 0:
        return False, f'{CACHE_DIR} still has {marker.get("filesAfter")} files.'
    return True, f'{SCHEDULE} ran the cleanup on schedule and cleared {report["cleared"]} files.'


CHECKS = {
    'ops-setup': _check_setup,
    'silent-rule': _check_silent,
    'fix-and-enrich': _check_fixed,
    'replay-missed': _check_replayed,
    'failing-target': _check_target,
    'runbook-document': _check_runbook,
    'runbook-function': _check_function,
    'scheduled-cleanup': _check_schedule,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def _ensure_role(name: str, trust: dict[str, Any], policy: dict[str, Any], description: str) -> None:
    iam = _client('iam')
    try:
        iam.get_role(RoleName=name)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description)
    iam.put_role_policy(RoleName=name, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(policy))


def _ensure_queue(name: str, policy: dict[str, Any] | None = None) -> str:
    sqs = _client('sqs')
    url = sqs.create_queue(QueueName=name)['QueueUrl']
    if policy:
        sqs.set_queue_attributes(QueueUrl=url, Attributes={'Policy': json.dumps(policy)})
    return url


def _ensure_instance() -> str:
    instance_id = _instance_id()
    if not instance_id:
        instance_id = _client('ec2').run_instances(
            ImageId=IMAGE_ID, InstanceType=INSTANCE_TYPE, MinCount=1, MaxCount=1,
            TagSpecifications=[{'ResourceType': 'instance', 'Tags': [
                {'Key': 'Name', 'Value': INSTANCE_NAME}, {'Key': 'Lab', 'Value': LAB_KEY}]}],
        )['Instances'][0]['InstanceId']
    _wait(lambda: _run_shell(instance_id, 'true')['Status'] == 'Success', BOOT_TIMEOUT, delay=3)
    return instance_id


def _need_instance(step: str, command: str, started: float) -> tuple[str | None, dict[str, Any] | None]:
    instance_id = _instance_id()
    if not instance_id or not _bus():
        return None, _result(step, command, {}, False, 'Set up the ops bus and the instance first.', started)
    return instance_id, None


def step_ops_setup() -> dict[str, Any]:
    started = time.perf_counter()
    events = _client('events')
    if not _bus():
        events.create_event_bus(Name=BUS)
    if not _archive():
        events.create_archive(ArchiveName=ARCHIVE, EventSourceArn=BUS_ARN, RetentionDays=7,
                              Description='Every ops event, for replay')
    _ensure_queue(INBOX, queue_policy(_queue_arn(INBOX)))
    _ensure_queue(DLQ, queue_policy(_queue_arn(DLQ)))
    _receive(INBOX)
    _receive(DLQ)
    instance_id = _ensure_instance()
    fill = _run_shell(instance_id, FILL_SCRIPT)
    files = _value(fill['Stdout'], 'files')
    _marker('ops-setup', {'instance': instance_id, 'cacheFiles': files})
    response = {'EventBus': BUS_ARN, 'Archive': _archive(), 'Queues': [INBOX, DLQ], 'Instance': instance_id,
                'CacheFiles': files}
    verified, message = _check_setup()
    return _result('ops-setup', SETUP_COMMAND, response, verified, message, started)


def step_silent_rule() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id, error = _need_instance('silent-rule', SILENT_COMMAND, started)
    if error:
        return error
    events = _client('events')
    events.put_rule(Name=RULE, EventBusName=BUS, EventPattern=json.dumps(BROKEN_PATTERN), State='ENABLED',
                    Description='Disk alerts to the on-call inbox')
    stale = [target['Id'] for target in _targets() if target['Id'] != 'inbox']
    if stale:
        events.remove_targets(Rule=RULE, EventBusName=BUS, Ids=stale)
    events.put_targets(Rule=RULE, EventBusName=BUS, Targets=[_inbox_target()])
    _receive(INBOX)
    sent_at = time.time()
    sent = _put_disk_event(instance_id, MISSED_REF, 93)
    bodies = _inbox_until(lambda found: bool(found), EVENT_WAIT_SECONDS)
    broken = _test_pattern(BROKEN_PATTERN, instance_id)
    _marker('silent-rule', {'sentAt': sent_at, 'event': sent, 'delivered': len(bodies), 'brokenMatches': broken})
    response = {'Rule': RULE, 'Pattern': BROKEN_PATTERN, 'Sent': sent, 'InboxMessages': bodies,
                'TestEventPattern': {'pattern': BROKEN_PATTERN, 'severityInEvent': 'CRITICAL', 'Result': broken}}
    verified, message = _check_silent()
    return _result('silent-rule', SILENT_COMMAND, response, verified, message, started)


def step_fix_and_enrich() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id, error = _need_instance('fix-and-enrich', FIX_COMMAND, started)
    if error:
        return error
    if not _rule():
        return _result('fix-and-enrich', FIX_COMMAND, {}, False, 'Create the rule first.', started)
    fixed = _test_pattern(FIXED_PATTERN, instance_id)
    numeric = _test_pattern(NUMERIC_PATTERN, instance_id)
    events = _client('events')
    events.put_rule(Name=RULE, EventBusName=BUS, EventPattern=json.dumps(FIXED_PATTERN), State='ENABLED',
                    Description='Disk alerts to the on-call inbox')
    events.put_targets(Rule=RULE, EventBusName=BUS, Targets=[_inbox_target()])
    sent = _put_disk_event(instance_id, ENRICHED_REF, 91)
    expected = render_alert(disk_event(instance_id, ENRICHED_REF, 91))
    bodies = _inbox_until(lambda found: expected in found, EVENT_WAIT_SECONDS)
    alert = next((body for body in bodies if body == expected), bodies[0] if bodies else None)
    _marker('fix-and-enrich', {'fixedMatches': fixed, 'numericMatches': numeric, 'alert': alert,
                               'expected': expected})
    response = {'TestEventPattern': {'equals-ignore-case': fixed, 'numeric >= 90': numeric}, 'Pattern': FIXED_PATTERN,
                'InputTransformer': {'InputPathsMap': INPUT_PATHS, 'InputTemplate': INPUT_TEMPLATE},
                'Sent': sent, 'Delivered': bodies}
    verified, message = _check_fixed()
    if verified and numeric is False:
        message += ' Floci does not support numeric matching, so the numeric pattern did not match.'
    return _result('fix-and-enrich', FIX_COMMAND, response, verified, message, started)


def step_replay_missed() -> dict[str, Any]:
    started = time.perf_counter()
    sent_at = (_marked('silent-rule') or {}).get('sentAt')
    if not sent_at or not _archive():
        return _result('replay-missed', REPLAY_COMMAND, {}, False, 'Send the first alert first.', started)
    events = _client('events')
    name = f'{REPLAY_PREFIX}-{int(time.time())}'
    start = datetime.fromtimestamp(sent_at - 5, tz=timezone.utc)
    end = datetime.now(timezone.utc) + timedelta(seconds=1)
    destination: dict[str, Any] = {'Arn': BUS_ARN, 'FilterArns': [RULE_ARN]}
    try:
        events.start_replay(ReplayName=name, EventSourceArn=ARCHIVE_ARN, EventStartTime=start, EventEndTime=end,
                            Destination=destination, Description='Re-drive alerts the broken rule dropped')
    except ClientError as exc:
        if _code(exc) not in ('ValidationException', 'InvalidParameterValue'):
            raise
        destination = {'Arn': BUS_ARN}
        events.start_replay(ReplayName=name, EventSourceArn=ARCHIVE_ARN, EventStartTime=start, EventEndTime=end,
                            Destination=destination)
    replay: dict[str, Any] = {}

    def finished() -> bool:
        replay.update(_safe_call(lambda: events.describe_replay(ReplayName=name), {}))
        return replay.get('State') in ('COMPLETED', 'FAILED', 'CANCELLED')

    _wait(finished, 60, delay=2)
    bodies = _inbox_until(lambda found: MISSED_REF in alert_refs(found), EVENT_WAIT_SECONDS)
    refs = alert_refs(bodies)
    _marker('replay-missed', {'name': name, 'state': replay.get('State'), 'refs': refs})
    response = {'Archive': _archive(), 'Replay': {key: replay.get(key) for key in (
        'ReplayName', 'State', 'StateReason', 'EventStartTime', 'EventEndTime', 'Destination')},
        'Delivered': bodies}
    verified, message = _check_replayed()
    return _result('replay-missed', REPLAY_COMMAND, response, verified, message, started)


def step_failing_target() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id, error = _need_instance('failing-target', TARGET_COMMAND, started)
    if error:
        return error
    if not _rule():
        return _result('failing-target', TARGET_COMMAND, {}, False, 'Create the rule first.', started)
    events = _client('events')
    events.put_targets(Rule=RULE, EventBusName=BUS, Targets=[
        {'Id': 'runbook', 'Arn': TYPO_ARN, 'RetryPolicy': RETRY_POLICY,
         'DeadLetterConfig': {'Arn': _queue_arn(DLQ)}}])
    _receive(DLQ)
    sent = _put_disk_event(instance_id, TARGET_REF, 92)
    letters: list[dict[str, str]] = []

    def dead_lettered() -> bool:
        letters.extend(dead_letters(_receive(DLQ, attributes=True)))
        return any(item.get('TARGET_ARN') == TYPO_ARN for item in letters)

    _wait(dead_lettered, EVENT_WAIT_SECONDS * 2, delay=1)
    inbox = _inbox_until(lambda found: TARGET_REF in alert_refs(found), EVENT_WAIT_SECONDS)
    events.remove_targets(Rule=RULE, EventBusName=BUS, Ids=['runbook'])
    _marker('failing-target', {'deadLetters': letters, 'inboxRefs': alert_refs(inbox)})
    response = {'Sent': sent, 'DeadLetters': letters, 'InboxStillReceived': alert_refs(inbox),
                'TargetsNow': [target['Id'] for target in _targets()]}
    verified, message = _check_target()
    return _result('failing-target', TARGET_COMMAND, response, verified, message, started)


def step_runbook_document() -> dict[str, Any]:
    started = time.perf_counter()
    ssm = _client('ssm')
    content = json.dumps(RUNBOOK_CONTENT)
    if _runbook():
        try:
            ssm.update_document(Name=RUNBOOK, Content=content, DocumentVersion='$LATEST', DocumentFormat='JSON')
        except ClientError as exc:
            if _code(exc) != 'DuplicateDocumentContent':
                raise
    else:
        ssm.create_document(Name=RUNBOOK, DocumentType='Automation', DocumentFormat='JSON', Content=content,
                            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    instance_id = _instance_id() or 'i-0123456789abcdef0'
    try:
        execution = ssm.start_automation_execution(DocumentName=RUNBOOK, Parameters={'InstanceId': [instance_id]})
        outcome = {'started': True, 'AutomationExecutionId': execution.get('AutomationExecutionId')}
    except ClientError as exc:
        outcome = {'started': False, 'error': f'{_code(exc)}: {exc.response.get("Error", {}).get("Message", "")}'}
    _marker('runbook-document', {'startOutcome': outcome})
    document = _runbook() or {}
    response = {'Document': {key: document.get(key) for key in (
        'Name', 'DocumentType', 'SchemaVersion', 'DocumentVersion', 'Status')},
        'Steps': [step['name'] for step in RUNBOOK_CONTENT['mainSteps']], 'StartAutomationExecution': outcome}
    verified, message = _check_runbook()
    if verified and not outcome.get('started'):
        message += f' Floci cannot start it: {outcome.get("error")}.'
    return _result('runbook-document', RUNBOOK_COMMAND, response, verified, message, started)


def _function_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('index.py', FUNCTION_SOURCE)
    return buffer.getvalue()


def _ensure_function() -> None:
    _ensure_role(FUNCTION_ROLE, _trust('lambda.amazonaws.com'), FUNCTION_POLICY, 'Runs the cache runbook steps')
    lam = _client('lambda')
    environment = {'Variables': {'CLEAR_SCRIPT': CLEAR_SCRIPT, 'RUNBOOK': RUNBOOK, 'INBOX': INBOX}}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_function_zip())
        _wait(lambda: (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=environment, Timeout=90)
    else:
        lam.create_function(FunctionName=FUNCTION, Runtime='python3.12', Role=FUNCTION_ROLE_ARN,
                            Handler='index.handler', Code={'ZipFile': _function_zip()}, Timeout=90,
                            Environment=environment, Description=f'Runs the {RUNBOOK} steps',
                            Tags={'Lab': LAB_KEY})
    _wait(lambda: (_function() or {}).get('State', 'Active') == 'Active'
          and (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)


def _report_for(trigger: str):
    def found(bodies: list[Any]) -> bool:
        return any(isinstance(body, dict) and body.get('trigger') == trigger for body in bodies)
    return found


def step_runbook_function() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id, error = _need_instance('runbook-function', FUNCTION_COMMAND, started)
    if error:
        return error
    if not _rule():
        return _result('runbook-function', FUNCTION_COMMAND, {}, False, 'Create the rule first.', started)
    if (_cache_files(instance_id) or 0) < CACHE_FILES:
        _run_shell(instance_id, FILL_SCRIPT)
    _ensure_function()
    lam = _client('lambda')
    _safe_call(lambda: lam.remove_permission(FunctionName=FUNCTION, StatementId='events'))
    lam.add_permission(FunctionName=FUNCTION, StatementId='events', Action='lambda:InvokeFunction',
                       Principal='events.amazonaws.com', SourceArn=RULE_ARN)
    _client('events').put_targets(Rule=RULE, EventBusName=BUS, Targets=[
        _inbox_target(), {'Id': 'runbook', 'Arn': FUNCTION_ARN, 'RetryPolicy': RETRY_POLICY,
                          'DeadLetterConfig': {'Arn': _queue_arn(DLQ)}}])
    _receive(INBOX)
    sent = _put_disk_event(instance_id, RUNBOOK_REF, 95)
    bodies = _inbox_until(_report_for(RUNBOOK_REF), RUNBOOK_WAIT_SECONDS)
    report = next((body for body in bodies if isinstance(body, dict) and body.get('trigger') == RUNBOOK_REF), None)
    files_after = _cache_files(instance_id)
    _marker('runbook-function', {'report': report, 'filesAfter': files_after})
    response = {'Sent': sent, 'Targets': [target['Id'] for target in _targets()], 'Report': report,
                'Alerts': [body for body in bodies if isinstance(body, str)], 'CacheFilesAfter': files_after}
    verified, message = _check_function()
    return _result('runbook-function', FUNCTION_COMMAND, response, verified, message, started)


def step_scheduled_cleanup() -> dict[str, Any]:
    started = time.perf_counter()
    instance_id, error = _need_instance('scheduled-cleanup', SCHEDULE_COMMAND, started)
    if error:
        return error
    if not _function():
        return _result('scheduled-cleanup', SCHEDULE_COMMAND, {}, False, 'Create the runbook function first.',
                       started)
    filled = _value(_run_shell(instance_id, FILL_SCRIPT)['Stdout'], 'files')
    _ensure_role(SCHEDULER_ROLE, _trust('scheduler.amazonaws.com'), SCHEDULER_POLICY, 'Scheduler runs the cleanup')
    scheduler = _client('scheduler')
    target = {'Arn': FUNCTION_ARN, 'RoleArn': SCHEDULER_ROLE_ARN,
              'Input': json.dumps({'instance': instance_id, 'task': 'nightly-cleanup'}),
              'RetryPolicy': {'MaximumRetryAttempts': 1, 'MaximumEventAgeInSeconds': 600}}
    _receive(INBOX)
    if _schedule():
        scheduler.update_schedule(Name=SCHEDULE, ScheduleExpression=SCHEDULE_EXPRESSION,
                                  FlexibleTimeWindow={'Mode': 'OFF'}, Target=target, State='ENABLED')
    else:
        scheduler.create_schedule(Name=SCHEDULE, ScheduleExpression=SCHEDULE_EXPRESSION,
                                  FlexibleTimeWindow={'Mode': 'OFF'}, Target=target, State='ENABLED',
                                  Description='Clear the cache before it fills the disk')
    bodies = _inbox_until(_report_for('nightly-cleanup'), SCHEDULE_WAIT_SECONDS)
    report = next((body for body in bodies if isinstance(body, dict) and body.get('trigger') == 'nightly-cleanup'),
                  None)
    files_after = _cache_files(instance_id)
    _marker('scheduled-cleanup', {'filled': filled, 'report': report, 'filesAfter': files_after})
    schedule = _schedule() or {}
    response = {'Schedule': {key: schedule.get(key) for key in ('Name', 'ScheduleExpression', 'State')},
                'CacheFilesBefore': filled, 'Report': report, 'CacheFilesAfter': files_after}
    verified, message = _check_schedule()
    return _result('scheduled-cleanup', SCHEDULE_COMMAND, response, verified, message, started)


RUNNERS = {
    'ops-setup': step_ops_setup,
    'silent-rule': step_silent_rule,
    'fix-and-enrich': step_fix_and_enrich,
    'replay-missed': step_replay_missed,
    'failing-target': step_failing_target,
    'runbook-document': step_runbook_document,
    'runbook-function': step_runbook_function,
    'scheduled-cleanup': step_scheduled_cleanup,
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
    """Remove the schedule, function, runbook, rule, archive, bus, queues, roles, and instance."""
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
                    'UnsupportedOperation', 'InvalidDocument'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    if _schedule():
        attempt(f'schedule:{SCHEDULE}', lambda: _client('scheduler').delete_schedule(Name=SCHEDULE))
    events = _client('events')
    if _rule():
        ids = [target['Id'] for target in _targets()]
        if ids:
            attempt(f'targets:{RULE}', lambda: events.remove_targets(Rule=RULE, EventBusName=BUS, Ids=ids))
        attempt(f'rule:{RULE}', lambda: events.delete_rule(Name=RULE, EventBusName=BUS))
    if _function():
        attempt(f'function:{FUNCTION}', lambda: _client('lambda').delete_function(FunctionName=FUNCTION))
    if _runbook():
        attempt(f'document:{RUNBOOK}', lambda: _client('ssm').delete_document(Name=RUNBOOK))
    if _archive():
        attempt(f'archive:{ARCHIVE}', lambda: events.delete_archive(ArchiveName=ARCHIVE))
    if _bus():
        attempt(f'event-bus:{BUS}', lambda: events.delete_event_bus(Name=BUS))
    for name in (INBOX, DLQ):
        url = _queue_url(name)
        if url:
            attempt(f'queue:{name}', lambda url=url: _client('sqs').delete_queue(QueueUrl=url))
    iam = _client('iam')
    for role in (FUNCTION_ROLE, SCHEDULER_ROLE):
        if _safe_call(lambda role=role: iam.get_role(RoleName=role)):
            attempt(f'role-policy:{role}', lambda role=role: iam.delete_role_policy(
                RoleName=role, PolicyName=INLINE_POLICY))
            attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))
    instance_id = _instance_id()
    if instance_id:
        attempt(f'instance:{instance_id}', lambda: _client('ec2').terminate_instances(InstanceIds=[instance_id]))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws scheduler delete-schedule --name {SCHEDULE}\n'
            f'aws events remove-targets --rule {RULE} --event-bus-name {BUS} --ids inbox runbook\n'
            f'aws events delete-rule --name {RULE} --event-bus-name {BUS}\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws ssm delete-document --name {RUNBOOK}\n'
            f'aws events delete-archive --archive-name {ARCHIVE}\n'
            f'aws events delete-event-bus --name {BUS}\n'
            f'aws sqs delete-queue --queue-url <{INBOX}-url>  &&  aws sqs delete-queue --queue-url <{DLQ}-url>\n'
            f'aws iam delete-role --role-name {FUNCTION_ROLE}  &&  aws iam delete-role --role-name {SCHEDULER_ROLE}\n'
            'aws ec2 terminate-instances --instance-ids <instance-id>'
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
            else 'The schedule, function, runbook, rule, archive, bus, queues, roles, and instance are gone.',
        },
    }
