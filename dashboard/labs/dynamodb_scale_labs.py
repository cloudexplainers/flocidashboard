"""DynamoDB at scale lab: provisioned capacity sized from a workload, Application Auto Scaling, an on-demand table,
TTL expiry, a filtered stream processor in Lambda, point-in-time recovery, and a global table replica.

Floci 2.3.0 stores capacity settings but never throttles, so capacity is a calculation the lab checks. It stores
scaling targets and policies and creates their CloudWatch alarms, but nothing ever changes the capacity. TTL really
deletes expired items, and streams really feed Lambda through a filtered event source mapping. PITR is a flag (restore
is unsupported, as are on-demand backups), and a replica is listed as ACTIVE without a table in the other Region, so
those steps check the settings and say what Floci skips.
"""

from __future__ import annotations

import io
import json
import math
import time
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'dynamodb'
LAB_KEY = 'capacity-at-scale'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
REPLICA_REGION = 'us-west-2'
ACCOUNT = '000000000000'

ORDERS = 'lab-orders-at-scale'
SESSIONS = 'lab-sessions'
AUDIT = 'lab-order-audit'
ORDERS_ARN = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{ORDERS}'
AUDIT_ARN = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{AUDIT}'
INITIAL_CAPACITY = 5

# Workload the provisioned table is sized for.
WRITES_PER_SECOND = 40
WRITE_ITEM_KB = 2.5
READS_PER_SECOND = 120
READ_ITEM_KB = 6
STRONGLY_CONSISTENT = True
REQUIRED_WCU = WRITES_PER_SECOND * math.ceil(WRITE_ITEM_KB / 1)
REQUIRED_RCU = READS_PER_SECOND * math.ceil(READ_ITEM_KB / 4) // (1 if STRONGLY_CONSISTENT else 2)
SCALING_TARGET_UTILIZATION = 70.0
SCALING_MAX_FACTOR = 4

TTL_ATTRIBUTE = 'expires_at'
TTL_TIMEOUT_SECONDS = 150
STREAM_TIMEOUT_SECONDS = 60

ROLE_NAME = 'lab-order-audit-role'
ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}'
ROLE_POLICY = 'lab-order-audit-access'
FUNCTION = 'lab-order-shipped-audit'
RUNTIME = 'python3.12'
HANDLER = 'audit.handler'
PITR_DAYS = 7
SHIPPED_FILTER = {'eventName': ['MODIFY'], 'dynamodb': {'NewImage': {'status': {'S': ['SHIPPED']}}}}

LAMBDA_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
AUDIT_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'ReadOrderStream', 'Effect': 'Allow',
         'Action': ['dynamodb:DescribeStream', 'dynamodb:GetRecords', 'dynamodb:GetShardIterator', 'dynamodb:ListStreams'],
         'Resource': f'{ORDERS_ARN}/stream/*'},
        {'Sid': 'WriteAudit', 'Effect': 'Allow', 'Action': 'dynamodb:PutItem', 'Resource': AUDIT_ARN},
    ],
}

AUDIT_SOURCE = '''"""Write an audit item for every order that changes to SHIPPED (the event source mapping filters the rest)."""
import os

import boto3

dynamodb = boto3.client("dynamodb")


def handler(event, context):
    for record in event["Records"]:
        new = record["dynamodb"]["NewImage"]
        old = record["dynamodb"].get("OldImage", {})
        dynamodb.put_item(TableName=os.environ["AUDIT_TABLE"], Item={
            "order_id": new["order_id"],
            "event_id": {"S": record["eventID"]},
            "customer_id": new["customer_id"],
            "from_status": old.get("status", {"S": "NONE"}),
            "to_status": new["status"],
        })
    return {"audited": len(event["Records"])}
'''

ORDERS_COMMAND = (
    f'aws dynamodb create-table --table-name {ORDERS} '
    '--attribute-definitions AttributeName=customer_id,AttributeType=S AttributeName=order_id,AttributeType=S '
    '--key-schema AttributeName=customer_id,KeyType=HASH AttributeName=order_id,KeyType=RANGE '
    f'--billing-mode PROVISIONED --provisioned-throughput ReadCapacityUnits={INITIAL_CAPACITY},'
    f'WriteCapacityUnits={INITIAL_CAPACITY} --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES'
)
CAPACITY_COMMAND = (
    f'# {WRITES_PER_SECOND} writes/s x ceil({WRITE_ITEM_KB} KB / 1 KB) = {REQUIRED_WCU} WCU\n'
    f'# {READS_PER_SECOND} strongly consistent reads/s x ceil({READ_ITEM_KB} KB / 4 KB) = {REQUIRED_RCU} RCU\n'
    f'aws dynamodb update-table --table-name {ORDERS} --provisioned-throughput '
    f'ReadCapacityUnits={REQUIRED_RCU},WriteCapacityUnits={REQUIRED_WCU}'
)
SCALING_COMMAND = (
    'aws application-autoscaling register-scalable-target --service-namespace dynamodb '
    f'--resource-id table/{ORDERS} --scalable-dimension dynamodb:table:WriteCapacityUnits '
    f'--min-capacity {REQUIRED_WCU} --max-capacity {REQUIRED_WCU * SCALING_MAX_FACTOR}\n'
    'aws application-autoscaling put-scaling-policy --service-namespace dynamodb '
    f'--resource-id table/{ORDERS} --scalable-dimension dynamodb:table:WriteCapacityUnits '
    f'--policy-name {ORDERS}-wcu --policy-type TargetTrackingScaling --target-tracking-scaling-policy-configuration '
    f"'TargetValue={SCALING_TARGET_UTILIZATION},PredefinedMetricSpecification={{PredefinedMetricType=DynamoDBWriteCapacityUtilization}}'\n"
    '# the same for dynamodb:table:ReadCapacityUnits with DynamoDBReadCapacityUtilization'
)
SESSIONS_COMMAND = (
    f'aws dynamodb create-table --table-name {SESSIONS} --attribute-definitions AttributeName=session_id,AttributeType=S '
    '--key-schema AttributeName=session_id,KeyType=HASH --billing-mode PAY_PER_REQUEST'
)
TTL_COMMAND = (
    f'aws dynamodb update-time-to-live --table-name {SESSIONS} '
    f'--time-to-live-specification Enabled=true,AttributeName={TTL_ATTRIBUTE}\n'
    f'aws dynamodb put-item --table-name {SESSIONS} --item \'{{"session_id":{{"S":"s-expired-1"}},'
    f'"{TTL_ATTRIBUTE}":{{"N":"<epoch seconds in the past>"}}}}\'\n'
    f'aws dynamodb put-item --table-name {SESSIONS} --item \'{{"session_id":{{"S":"s-live-1"}},'
    f'"{TTL_ATTRIBUTE}":{{"N":"<epoch seconds an hour ahead>"}}}}\'\n'
    f'aws dynamodb scan --table-name {SESSIONS} --select COUNT   # expired sessions disappear'
)
STREAM_COMMAND = (
    f'aws dynamodb create-table --table-name {AUDIT} --attribute-definitions AttributeName=order_id,AttributeType=S '
    '--key-schema AttributeName=order_id,KeyType=HASH --billing-mode PAY_PER_REQUEST\n'
    f'aws iam create-role --role-name {ROLE_NAME} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam put-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY} --policy-document file://audit.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime {RUNTIME} --handler {HANDLER} --role {ROLE_ARN} '
    f'--zip-file fileb://audit.zip --environment Variables={{AUDIT_TABLE={AUDIT}}}\n'
    f'aws lambda create-event-source-mapping --function-name {FUNCTION} --event-source-arn <orders-stream-arn> '
    '--starting-position TRIM_HORIZON --batch-size 10 --maximum-retry-attempts 3 --bisect-batch-on-function-error '
    f"--filter-criteria '{{\"Filters\":[{{\"Pattern\":\"{json.dumps(SHIPPED_FILTER).replace(chr(34), chr(92) + chr(34))}\"}}]}}'\n"
    f'aws dynamodb put-item --table-name {ORDERS} --item file://order.json   # status PENDING\n'
    f'aws dynamodb update-item --table-name {ORDERS} --key file://key.json --update-expression "SET #s = :s" '
    '--expression-attribute-names \'{"#s":"status"}\' --expression-attribute-values \'{":s":{"S":"SHIPPED"}}\''
)
PITR_COMMAND = (
    f'aws dynamodb update-continuous-backups --table-name {ORDERS} --point-in-time-recovery-specification '
    f'PointInTimeRecoveryEnabled=true,RecoveryPeriodInDays={PITR_DAYS}\n'
    f'aws dynamodb describe-continuous-backups --table-name {ORDERS}\n'
    f'aws dynamodb restore-table-to-point-in-time --source-table-name {ORDERS} --target-table-name {ORDERS}-restored '
    '--use-latest-restorable-time'
)
REPLICA_COMMAND = (
    f'aws dynamodb update-table --table-name {ORDERS} --replica-updates '
    f"'[{{\"Create\":{{\"RegionName\":\"{REPLICA_REGION}\"}}}}]'\n"
    f'aws dynamodb describe-table --table-name {ORDERS} --query "Table.Replicas"\n'
    f'aws dynamodb describe-table --table-name {ORDERS} --region {REPLICA_REGION}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Run DynamoDB at scale with capacity planning, auto scaling, TTL, streams, and global tables',
    'description': (
        'Size a provisioned table from a workload, let Application Auto Scaling track utilization, put spiky sessions '
        'in an on-demand table that expires them with TTL, audit shipped orders with a filtered stream processor in '
        'Lambda, turn on point-in-time recovery, and add a replica in a second Region for a global table.'
    ),
    'steps': [
        {
            'key': 'provisioned-table',
            'title': 'Create a provisioned table with a stream',
            'command': ORDERS_COMMAND,
            'explanation': (
                'Provisioned capacity is the cheaper mode for steady, predictable traffic: you pay per read and write '
                'capacity unit per hour whether or not you use it, and requests over it are throttled '
                '(ProvisionedThroughputExceededException) once burst capacity runs out. A good partition key, here '
                'the customer ID, spreads load across partitions; a hot key is throttled even when the table has spare '
                'capacity. The stream records every change with old and new images for the later steps. How Floci '
                'emulates this: the table, its capacity, and its stream are real, but Floci never throttles, so '
                'capacity is a setting the lab checks rather than a limit you can hit.'
            ),
        },
        {
            'key': 'size-capacity',
            'title': 'Size read and write capacity for the workload',
            'command': CAPACITY_COMMAND,
            'explanation': (
                'One WCU is one write per second of an item up to 1 KB; one RCU is one strongly consistent read per '
                'second of up to 4 KB, or two eventually consistent reads. Sizes round up per item, so '
                f'{WRITES_PER_SECOND} writes/s of {WRITE_ITEM_KB} KB items need {REQUIRED_WCU} WCU, and '
                f'{READS_PER_SECOND} strongly consistent reads/s of {READ_ITEM_KB} KB items need {REQUIRED_RCU} RCU '
                f'({REQUIRED_RCU // 2} if eventually consistent reads were fine). Transactional reads and writes cost '
                'double. DAX, an in-memory cache in front of DynamoDB, cuts read capacity and latency for read-heavy '
                'tables. How Floci emulates this: the new capacity is stored; the step checks it matches the math.'
            ),
        },
        {
            'key': 'auto-scaling',
            'title': 'Track 70% utilization with Application Auto Scaling',
            'command': SCALING_COMMAND,
            'explanation': (
                'Auto scaling registers each capacity dimension as a scalable target with a minimum and maximum and '
                f'adds a target tracking policy: keep consumed capacity near {SCALING_TARGET_UTILIZATION:.0f}% of '
                'provisioned. The policy creates CloudWatch alarms that scale out after a couple of minutes of high '
                'use and scale in more slowly (only a limited number of decreases a day). Scaling takes minutes, so a '
                'sudden spike can still throttle; on-demand mode or a higher minimum covers that. How Floci emulates '
                'this: Floci stores the targets and policies and creates the alarms, but DynamoDB publishes no '
                'consumed-capacity metrics, so nothing ever changes the capacity. The step checks the settings and '
                'the alarms.'
            ),
        },
        {
            'key': 'on-demand-table',
            'title': 'Use on-demand capacity for spiky session traffic',
            'command': SESSIONS_COMMAND,
            'explanation': (
                'On-demand mode bills per request with no capacity to plan, and serves up to double the previous peak '
                'at once. It suits new tables, unpredictable or spiky traffic, and tables that are idle most of the '
                'time; steady traffic is cheaper provisioned, with reserved capacity cheaper still. You can switch a '
                'table between modes, with limits on how often. How Floci emulates this: the billing mode is stored '
                'and the table works the same either way.'
            ),
        },
        {
            'key': 'ttl-expiry',
            'title': 'Expire old sessions with TTL',
            'command': TTL_COMMAND,
            'explanation': (
                f'Time to live deletes an item once the epoch-seconds value in its {TTL_ATTRIBUTE} attribute is in the '
                'past, at no cost in write capacity. AWS deletes expired items within a few days, not at the second, '
                'so reads should still filter on the attribute; the deletes appear in the stream as REMOVE records '
                'made by the DynamoDB service, which lets you archive expired items to S3. How Floci emulates this: '
                f'Floci really deletes expired items, usually within a minute; the step waits up to '
                f'{TTL_TIMEOUT_SECONDS} seconds.'
            ),
        },
        {
            'key': 'stream-processor',
            'title': 'Audit shipped orders with a filtered stream processor',
            'command': STREAM_COMMAND,
            'explanation': (
                'DynamoDB Streams keeps each change for 24 hours in order per item. A Lambda event source mapping polls '
                'the stream and invokes the function with batches; the filter passes only updates whose new status '
                'is SHIPPED, so the function is not even invoked for the rest. On a function error the mapping '
                'retries the batch (here up to 3 times), and bisecting splits a failing batch to isolate a bad '
                'record; an on-failure destination keeps what still fails. The role reads the stream and writes only '
                'to the audit table. How Floci emulates this: the mapping, filter, retries, and the function\'s write '
                'are real; Floci reads the stream without checking the role, so the step checks the policy.'
            ),
            'artifact_label': 'audit.py',
            'artifact': AUDIT_SOURCE,
        },
        {
            'key': 'point-in-time-recovery',
            'title': 'Turn on point-in-time recovery',
            'command': PITR_COMMAND,
            'explanation': (
                f'PITR keeps continuous backups for {PITR_DAYS} days here (1 to 35), and restores the table to any '
                'second in that window as a new table; on-demand backups (CreateBackup or AWS Backup) are kept until '
                'deleted. Neither affects table performance. For disaster recovery across Regions, restore elsewhere '
                'from AWS Backup copies or use a global table. How Floci emulates this: the PITR setting is stored and '
                'reported, but RestoreTableToPointInTime and CreateBackup are unsupported, so the step checks the '
                'setting and records the restore attempt.'
            ),
        },
        {
            'key': 'global-table-replica',
            'title': 'Add a replica in a second Region',
            'command': REPLICA_COMMAND,
            'explanation': (
                f'A global table replicates a table to other Regions ({REPLICA_REGION} here), usually within a second, '
                'with every replica writable (multi-active, last writer wins). It needs the stream with new and old '
                'images, and a provisioned table needs write auto scaling (or on-demand mode) so replicas keep up. It '
                'is the DynamoDB answer for low-latency global reads and writes and for Region-level disaster '
                'recovery with near-zero RPO. How Floci emulates this: Floci lists the replica as ACTIVE, but creates '
                f'no table in {REPLICA_REGION} and copies nothing. The step checks the prerequisites and says so.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str, region: str | None = None):
    factory = FlociClientFactory()
    if region:
        factory.region = region
    return factory.client(name)


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


def _wait(predicate, timeout: float, delay: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


# ── Lookups ──────────────────────────────────────────────────────────────────

def _table(name: str, region: str | None = None) -> dict[str, Any] | None:
    try:
        return _client('dynamodb', region).describe_table(TableName=name)['Table']
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _capacity(table: dict[str, Any]) -> tuple[int, int]:
    throughput = table.get('ProvisionedThroughput') or {}
    return int(throughput.get('ReadCapacityUnits') or 0), int(throughput.get('WriteCapacityUnits') or 0)


def _billing_mode(table: dict[str, Any]) -> str:
    return (table.get('BillingModeSummary') or {}).get('BillingMode') or 'PROVISIONED'


def _wait_active(name: str) -> None:
    _wait(lambda: (_table(name) or {}).get('TableStatus') == 'ACTIVE', timeout=30, delay=1)


def _scalable_targets() -> dict[str, dict[str, Any]]:
    targets = _client('application-autoscaling').describe_scalable_targets(
        ServiceNamespace='dynamodb', ResourceIds=[f'table/{ORDERS}']).get('ScalableTargets', [])
    return {target['ScalableDimension']: target for target in targets}


def _scaling_policies() -> dict[str, dict[str, Any]]:
    policies = _client('application-autoscaling').describe_scaling_policies(
        ServiceNamespace='dynamodb', ResourceId=f'table/{ORDERS}').get('ScalingPolicies', [])
    return {policy['ScalableDimension']: policy for policy in policies}


def _alarm_names() -> set[str]:
    names: set[str] = set()
    kwargs: dict[str, Any] = {'AlarmNamePrefix': f'TargetTracking-table/{ORDERS}-'}
    for _ in range(20):
        page = _client('cloudwatch').describe_alarms(**kwargs)
        names.update(alarm['AlarmName'] for alarm in page.get('MetricAlarms', []))
        if not page.get('NextToken'):
            break
        kwargs['NextToken'] = page['NextToken']
    return names


def _ttl(name: str) -> dict[str, Any]:
    return _client('dynamodb').describe_time_to_live(TableName=name).get('TimeToLiveDescription') or {}


def _session_ids() -> set[str]:
    items = _client('dynamodb').scan(TableName=SESSIONS, ProjectionExpression='session_id').get('Items', [])
    return {item['session_id']['S'] for item in items}


def _function() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function_configuration(FunctionName=FUNCTION)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _mapping(stream_arn: str | None) -> dict[str, Any] | None:
    if not stream_arn:
        return None
    mappings = _client('lambda').list_event_source_mappings(
        FunctionName=FUNCTION, EventSourceArn=stream_arn).get('EventSourceMappings', [])
    return mappings[0] if mappings else None


def _role_policy() -> dict[str, Any] | None:
    try:
        document = _client('iam').get_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY)['PolicyDocument']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise
    return json.loads(document) if isinstance(document, str) else document


def _audit_items() -> list[dict[str, Any]]:
    return _client('dynamodb').scan(TableName=AUDIT).get('Items', [])


def _continuous_backups() -> dict[str, Any]:
    return _client('dynamodb').describe_continuous_backups(TableName=ORDERS).get('ContinuousBackupsDescription') or {}


def _zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo('audit.py', date_time=(2026, 1, 1, 0, 0, 0))
        info.external_attr = 0o644 << 16
        archive.writestr(info, AUDIT_SOURCE)
    return buffer.getvalue()


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_orders() -> tuple[bool, str]:
    table = _table(ORDERS)
    if not table:
        return False, f'Table {ORDERS} was not found.'
    keys = {key['AttributeName']: key['KeyType'] for key in table.get('KeySchema') or []}
    if keys != {'customer_id': 'HASH', 'order_id': 'RANGE'}:
        return False, f'{ORDERS} needs customer_id as partition key and order_id as sort key.'
    if _billing_mode(table) != 'PROVISIONED':
        return False, f'{ORDERS} must use provisioned capacity.'
    stream = table.get('StreamSpecification') or {}
    if not stream.get('StreamEnabled') or stream.get('StreamViewType') != 'NEW_AND_OLD_IMAGES':
        return False, f'{ORDERS} needs a stream with new and old images.'
    return True, f'{ORDERS} is provisioned with a NEW_AND_OLD_IMAGES stream.'


def _check_capacity() -> tuple[bool, str]:
    table = _table(ORDERS)
    if not table:
        return False, f'Table {ORDERS} was not found.'
    rcu, wcu = _capacity(table)
    if rcu < REQUIRED_RCU or wcu < REQUIRED_WCU:
        return False, f'{ORDERS} has {rcu} RCU and {wcu} WCU, below the {REQUIRED_RCU} RCU and {REQUIRED_WCU} WCU needed.'
    return True, f'{ORDERS} has {rcu} RCU and {wcu} WCU for the workload.'


def _check_scaling() -> tuple[bool, str]:
    targets, policies = _scalable_targets(), _scaling_policies()
    expected = {'dynamodb:table:ReadCapacityUnits': (REQUIRED_RCU, 'DynamoDBReadCapacityUtilization'),
                'dynamodb:table:WriteCapacityUnits': (REQUIRED_WCU, 'DynamoDBWriteCapacityUtilization')}
    for dimension, (minimum, metric) in expected.items():
        label = dimension.rsplit(':', 1)[-1]
        target = targets.get(dimension)
        if not target:
            return False, f'{label} is not registered as a scalable target.'
        if target.get('MinCapacity') != minimum or target.get('MaxCapacity') != minimum * SCALING_MAX_FACTOR:
            return False, (f'{label} should scale between {minimum} and {minimum * SCALING_MAX_FACTOR}, not '
                           f'{target.get("MinCapacity")} and {target.get("MaxCapacity")}.')
        config = (policies.get(dimension) or {}).get('TargetTrackingScalingPolicyConfiguration') or {}
        if config.get('TargetValue') != SCALING_TARGET_UTILIZATION or (
                config.get('PredefinedMetricSpecification') or {}).get('PredefinedMetricType') != metric:
            return False, f'{label} needs a target tracking policy on {metric} at {SCALING_TARGET_UTILIZATION:.0f}%.'
    return True, (f'Reads and writes track {SCALING_TARGET_UTILIZATION:.0f}% utilization between the sized capacity and '
                  f'{SCALING_MAX_FACTOR}x it.')


def _check_sessions() -> tuple[bool, str]:
    table = _table(SESSIONS)
    if not table:
        return False, f'Table {SESSIONS} was not found.'
    if _billing_mode(table) != 'PAY_PER_REQUEST':
        return False, f'{SESSIONS} must use on-demand capacity.'
    return True, f'{SESSIONS} bills per request.'


def _check_ttl() -> tuple[bool, str]:
    if not _table(SESSIONS):
        return False, f'Table {SESSIONS} was not found.'
    ttl = _ttl(SESSIONS)
    if ttl.get('TimeToLiveStatus') not in {'ENABLED', 'ENABLING'} or ttl.get('AttributeName') != TTL_ATTRIBUTE:
        return False, f'TTL on {SESSIONS} must use {TTL_ATTRIBUTE}.'
    marker = _marked('ttl-expiry')
    if not marker:
        return False, 'Write sessions and watch the expired ones go.'
    return True, marker['message']


def _check_stream_setup() -> tuple[bool, str]:
    table = _table(ORDERS)
    stream_arn = (table or {}).get('LatestStreamArn')
    if not stream_arn:
        return False, f'{ORDERS} has no stream.'
    if not _table(AUDIT):
        return False, f'Table {AUDIT} was not found.'
    function = _function()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    if function.get('Role') != ROLE_ARN:
        return False, f'{FUNCTION} must run as {ROLE_NAME}.'
    policy = _role_policy() or {}
    writes = [statement for statement in policy.get('Statement', []) if statement.get('Effect') == 'Allow'
              and 'dynamodb:PutItem' in (statement.get('Action') if isinstance(statement.get('Action'), list)
                                         else [statement.get('Action')])]
    if not writes or any(statement.get('Resource') != AUDIT_ARN for statement in writes):
        return False, f'{ROLE_NAME} may write only to {AUDIT}.'
    mapping = _mapping(stream_arn)
    if not mapping:
        return False, f'{FUNCTION} has no event source mapping on the {ORDERS} stream.'
    if mapping.get('State') not in {'Enabled', 'Enabling', 'Creating', 'Updating'}:
        return False, f'The event source mapping is {mapping.get("State")}.'
    patterns = [json.loads(item.get('Pattern') or '{}') for item in (mapping.get('FilterCriteria') or {}).get('Filters', [])]
    if patterns != [SHIPPED_FILTER]:
        return False, 'The mapping must pass only updates whose new status is SHIPPED.'
    if mapping.get('MaximumRetryAttempts') != 3 or not mapping.get('BisectBatchOnFunctionError'):
        return False, 'The mapping should retry 3 times and bisect failing batches.'
    return True, f'{FUNCTION} receives only SHIPPED updates from the {ORDERS} stream.'


def _check_stream() -> tuple[bool, str]:
    ok, problem = _check_stream_setup()
    if not ok:
        return ok, problem
    marker = _marked('stream-processor')
    if not marker:
        return False, 'Ship an order and watch the audit table.'
    return True, marker['message']


def _check_pitr() -> tuple[bool, str]:
    if not _table(ORDERS):
        return False, f'Table {ORDERS} was not found.'
    description = (_continuous_backups().get('PointInTimeRecoveryDescription') or {})
    if description.get('PointInTimeRecoveryStatus') != 'ENABLED':
        return False, f'Point-in-time recovery is off on {ORDERS}.'
    days = description.get('RecoveryPeriodInDays')
    if days not in (None, PITR_DAYS):
        return False, f'The recovery window is {days} days, not {PITR_DAYS}.'
    return True, f'Point-in-time recovery keeps {PITR_DAYS} days for {ORDERS}.'


def _check_replica() -> tuple[bool, str]:
    table = _table(ORDERS)
    if not table:
        return False, f'Table {ORDERS} was not found.'
    replicas = {replica.get('RegionName'): replica.get('ReplicaStatus') for replica in table.get('Replicas') or []}
    if REPLICA_REGION not in replicas:
        return False, f'{ORDERS} has no replica in {REPLICA_REGION}.'
    stream = table.get('StreamSpecification') or {}
    if stream.get('StreamViewType') != 'NEW_AND_OLD_IMAGES':
        return False, 'A global table needs a stream with new and old images.'
    if _billing_mode(table) == 'PROVISIONED' and 'dynamodb:table:WriteCapacityUnits' not in _scalable_targets():
        return False, 'A provisioned global table needs write auto scaling (AWS refuses the replica without it).'
    return True, f'{ORDERS} has a {REPLICA_REGION} replica ({replicas[REPLICA_REGION]}).'


# ── Steps ────────────────────────────────────────────────────────────────────

def step_provisioned_table() -> dict[str, Any]:
    started = time.perf_counter()
    dynamodb = _client('dynamodb')
    table = _table(ORDERS)
    if not table:
        dynamodb.create_table(
            TableName=ORDERS,
            AttributeDefinitions=[{'AttributeName': 'customer_id', 'AttributeType': 'S'},
                                  {'AttributeName': 'order_id', 'AttributeType': 'S'}],
            KeySchema=[{'AttributeName': 'customer_id', 'KeyType': 'HASH'},
                       {'AttributeName': 'order_id', 'KeyType': 'RANGE'}],
            BillingMode='PROVISIONED',
            ProvisionedThroughput={'ReadCapacityUnits': INITIAL_CAPACITY, 'WriteCapacityUnits': INITIAL_CAPACITY},
            StreamSpecification={'StreamEnabled': True, 'StreamViewType': 'NEW_AND_OLD_IMAGES'},
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        _wait_active(ORDERS)
    else:
        stream = table.get('StreamSpecification') or {}
        if not stream.get('StreamEnabled'):
            dynamodb.update_table(TableName=ORDERS, StreamSpecification={
                'StreamEnabled': True, 'StreamViewType': 'NEW_AND_OLD_IMAGES'})
            _wait_active(ORDERS)
    table = _table(ORDERS) or {}
    response = {key: table.get(key) for key in (
        'TableName', 'TableStatus', 'KeySchema', 'BillingModeSummary', 'ProvisionedThroughput', 'StreamSpecification',
        'LatestStreamArn')}
    verified, message = _check_orders()
    return _result('provisioned-table', ORDERS_COMMAND, response, verified, message, started)


def step_size_capacity() -> dict[str, Any]:
    started = time.perf_counter()
    table = _table(ORDERS)
    if not table:
        return _result('size-capacity', CAPACITY_COMMAND, {}, False, f'Create {ORDERS} first.', started)
    if _capacity(table) != (REQUIRED_RCU, REQUIRED_WCU) and _billing_mode(table) == 'PROVISIONED':
        _client('dynamodb').update_table(TableName=ORDERS, ProvisionedThroughput={
            'ReadCapacityUnits': REQUIRED_RCU, 'WriteCapacityUnits': REQUIRED_WCU})
        _wait_active(ORDERS)
    rcu, wcu = _capacity(_table(ORDERS) or {})
    response = {
        'Workload': {'WritesPerSecond': WRITES_PER_SECOND, 'WriteItemKB': WRITE_ITEM_KB,
                     'StronglyConsistentReadsPerSecond': READS_PER_SECOND, 'ReadItemKB': READ_ITEM_KB},
        'Math': {'WCU': f'{WRITES_PER_SECOND} x ceil({WRITE_ITEM_KB} / 1) = {REQUIRED_WCU}',
                 'RCU': f'{READS_PER_SECOND} x ceil({READ_ITEM_KB} / 4) = {REQUIRED_RCU}',
                 'EventuallyConsistentRCU': REQUIRED_RCU // 2},
        'ProvisionedThroughput': {'ReadCapacityUnits': rcu, 'WriteCapacityUnits': wcu},
        'MonthlyHours': 730,
    }
    verified, message = _check_capacity()
    if verified:
        message += ' Floci never throttles, so the capacity is not enforced.'
    return _result('size-capacity', CAPACITY_COMMAND, response, verified, message, started)


def step_auto_scaling() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_capacity()
    if not ok:
        return _result('auto-scaling', SCALING_COMMAND, {}, False, f'Size the capacity first: {problem}', started)
    scaling = _client('application-autoscaling')
    for dimension, minimum, metric in (
            ('dynamodb:table:ReadCapacityUnits', REQUIRED_RCU, 'DynamoDBReadCapacityUtilization'),
            ('dynamodb:table:WriteCapacityUnits', REQUIRED_WCU, 'DynamoDBWriteCapacityUtilization')):
        scaling.register_scalable_target(
            ServiceNamespace='dynamodb', ResourceId=f'table/{ORDERS}', ScalableDimension=dimension,
            MinCapacity=minimum, MaxCapacity=minimum * SCALING_MAX_FACTOR)
        scaling.put_scaling_policy(
            PolicyName=f'{ORDERS}-{dimension.rsplit(":", 1)[-1]}', ServiceNamespace='dynamodb',
            ResourceId=f'table/{ORDERS}', ScalableDimension=dimension, PolicyType='TargetTrackingScaling',
            TargetTrackingScalingPolicyConfiguration={
                'TargetValue': SCALING_TARGET_UTILIZATION,
                'PredefinedMetricSpecification': {'PredefinedMetricType': metric},
                'ScaleOutCooldown': 60, 'ScaleInCooldown': 300})
    alarms = sorted(_alarm_names())
    response = {'ScalableTargets': list(_scalable_targets().values()), 'ScalingPolicies': list(_scaling_policies().values()),
                'Alarms': alarms, 'ProvisionedThroughput': (_table(ORDERS) or {}).get('ProvisionedThroughput')}
    verified, message = _check_scaling()
    if verified:
        message += (f' The policies created {len(alarms)} CloudWatch alarm(s); Floci publishes no consumed-capacity '
                    'metrics, so they never scale the table.')
    return _result('auto-scaling', SCALING_COMMAND, response, verified, message, started)


def step_on_demand_table() -> dict[str, Any]:
    started = time.perf_counter()
    dynamodb = _client('dynamodb')
    table = _table(SESSIONS)
    if not table:
        dynamodb.create_table(
            TableName=SESSIONS, AttributeDefinitions=[{'AttributeName': 'session_id', 'AttributeType': 'S'}],
            KeySchema=[{'AttributeName': 'session_id', 'KeyType': 'HASH'}], BillingMode='PAY_PER_REQUEST',
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        _wait_active(SESSIONS)
    elif _billing_mode(table) != 'PAY_PER_REQUEST':
        dynamodb.update_table(TableName=SESSIONS, BillingMode='PAY_PER_REQUEST')
        _wait_active(SESSIONS)
    table = _table(SESSIONS) or {}
    verified, message = _check_sessions()
    return _result('on-demand-table', SESSIONS_COMMAND, {key: table.get(key) for key in (
        'TableName', 'TableStatus', 'BillingModeSummary', 'ProvisionedThroughput')}, verified, message, started)


def step_ttl_expiry() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_sessions()
    if not ok:
        return _result('ttl-expiry', TTL_COMMAND, {}, False, f'Create the sessions table first: {problem}', started)
    dynamodb = _client('dynamodb')
    ttl = _ttl(SESSIONS)
    if ttl.get('TimeToLiveStatus') not in {'ENABLED', 'ENABLING'} or ttl.get('AttributeName') != TTL_ATTRIBUTE:
        dynamodb.update_time_to_live(TableName=SESSIONS, TimeToLiveSpecification={
            'Enabled': True, 'AttributeName': TTL_ATTRIBUTE})
    now = int(time.time())
    run = f'{time.time_ns() // 1_000_000:x}'
    expired = [f's-expired-{run}-{index}' for index in range(1, 4)]
    live = [f's-live-{run}-{index}' for index in range(1, 3)]
    for session_id in expired:
        dynamodb.put_item(TableName=SESSIONS, Item={'session_id': {'S': session_id}, 'user_id': {'S': 'user-1'},
                                                    TTL_ATTRIBUTE: {'N': str(now - 3600)}})
    for session_id in live:
        dynamodb.put_item(TableName=SESSIONS, Item={'session_id': {'S': session_id}, 'user_id': {'S': 'user-2'},
                                                    TTL_ATTRIBUTE: {'N': str(now + 3600)}})
    waited = time.monotonic()
    gone = _wait(lambda: not (set(expired) & _session_ids()), timeout=TTL_TIMEOUT_SECONDS, delay=3)
    seconds = round(time.monotonic() - waited)
    remaining = _session_ids()
    response = {'TimeToLive': _ttl(SESSIONS), 'Expired': expired, 'Live': live,
                'StillPresent': sorted((set(expired) | set(live)) & remaining), 'WaitedSeconds': seconds}
    if not gone:
        return _result('ttl-expiry', TTL_COMMAND, response, False,
                       f'The expired sessions were still there after {TTL_TIMEOUT_SECONDS} seconds.', started)
    if not set(live) <= remaining:
        return _result('ttl-expiry', TTL_COMMAND, response, False, 'TTL deleted a session that had not expired.', started)
    message = (f'TTL deleted the {len(expired)} expired sessions after about {seconds} seconds and kept the '
               f'{len(live)} live ones (AWS can take days, so reads should filter on {TTL_ATTRIBUTE}).')
    _marker('ttl-expiry', {'message': message})
    return _result('ttl-expiry', TTL_COMMAND, response, True, message, started)


def step_stream_processor() -> dict[str, Any]:
    started = time.perf_counter()
    table = _table(ORDERS)
    stream_arn = (table or {}).get('LatestStreamArn')
    if not stream_arn:
        return _result('stream-processor', STREAM_COMMAND, {}, False, f'Create {ORDERS} with its stream first.', started)
    dynamodb, iam, lam = _client('dynamodb'), _client('iam'), _client('lambda')
    if not _table(AUDIT):
        dynamodb.create_table(
            TableName=AUDIT, AttributeDefinitions=[{'AttributeName': 'order_id', 'AttributeType': 'S'}],
            KeySchema=[{'AttributeName': 'order_id', 'KeyType': 'HASH'}], BillingMode='PAY_PER_REQUEST',
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        _wait_active(AUDIT)
    try:
        iam.get_role(RoleName=ROLE_NAME)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=ROLE_NAME, AssumeRolePolicyDocument=json.dumps(LAMBDA_TRUST),
                        Description='Order audit stream processor (DynamoDB lab)')
    iam.put_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY, PolicyDocument=json.dumps(AUDIT_POLICY))
    settings = {'Role': ROLE_ARN, 'Runtime': RUNTIME, 'Handler': HANDLER, 'Timeout': 30,
                'Environment': {'Variables': {'AUDIT_TABLE': AUDIT}}}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_zip())
        _wait(lambda: (_function() or {}).get('LastUpdateStatus') in (None, 'Successful', 'Failed'), timeout=30, delay=1)
        lam.update_function_configuration(FunctionName=FUNCTION, **settings)
    else:
        lam.create_function(FunctionName=FUNCTION, Code={'ZipFile': _zip()},
                            Description='Audit shipped orders (DynamoDB lab)', **settings)
    _wait(lambda: (_function() or {}).get('State') in (None, 'Active') and
          (_function() or {}).get('LastUpdateStatus') in (None, 'Successful'), timeout=30, delay=1)
    filters = {'Filters': [{'Pattern': json.dumps(SHIPPED_FILTER)}]}
    mapping = _mapping(stream_arn)
    if mapping:
        lam.update_event_source_mapping(UUID=mapping['UUID'], BatchSize=10, MaximumRetryAttempts=3,
                                        BisectBatchOnFunctionError=True, FilterCriteria=filters, Enabled=True)
    else:
        lam.create_event_source_mapping(
            FunctionName=FUNCTION, EventSourceArn=stream_arn, StartingPosition='TRIM_HORIZON', BatchSize=10,
            MaximumRetryAttempts=3, BisectBatchOnFunctionError=True, FilterCriteria=filters)
    ok, problem = _check_stream_setup()
    if not ok:
        return _result('stream-processor', STREAM_COMMAND, {}, False, problem, started)
    run = f'{time.time_ns() // 1_000_000:x}'
    shipped, pending = f'order-{run}-1', f'order-{run}-2'
    for order_id in (shipped, pending):
        dynamodb.put_item(TableName=ORDERS, Item={'customer_id': {'S': 'customer-42'}, 'order_id': {'S': order_id},
                                                  'status': {'S': 'PENDING'}, 'total': {'N': '59.90'}})
    dynamodb.update_item(TableName=ORDERS, Key={'customer_id': {'S': 'customer-42'}, 'order_id': {'S': shipped}},
                         UpdateExpression='SET #s = :s', ExpressionAttributeNames={'#s': 'status'},
                         ExpressionAttributeValues={':s': {'S': 'SHIPPED'}})
    dynamodb.update_item(TableName=ORDERS, Key={'customer_id': {'S': 'customer-42'}, 'order_id': {'S': pending}},
                         UpdateExpression='SET #t = :t', ExpressionAttributeNames={'#t': 'total'},
                         ExpressionAttributeValues={':t': {'N': '64.90'}})

    def audited() -> list[dict[str, Any]]:
        return [item for item in _audit_items() if item.get('order_id', {}).get('S') in {shipped, pending}]
    _wait(lambda: bool(audited()), timeout=STREAM_TIMEOUT_SECONDS, delay=2)
    time.sleep(2)  # give a wrongly passed record time to arrive too
    entries = audited()
    response = {'ShippedOrder': shipped, 'UpdatedButNotShipped': pending, 'AuditItems': entries,
                'EventSourceMapping': {key: (_mapping(stream_arn) or {}).get(key) for key in (
                    'UUID', 'State', 'BatchSize', 'MaximumRetryAttempts', 'BisectBatchOnFunctionError',
                    'FilterCriteria', 'StartingPosition')}}
    ids = [entry['order_id']['S'] for entry in entries]
    if ids != [shipped]:
        detail = 'nothing' if not ids else ', '.join(ids)
        return _result('stream-processor', STREAM_COMMAND, response, False,
                       f'Expected one audit item for {shipped}; the audit table has {detail}.', started)
    entry = entries[0]
    message = (f'Only the shipped order reached the function: {shipped} went '
               f'{entry.get("from_status", {}).get("S")} to {entry.get("to_status", {}).get("S")}, and the other '
               'update was filtered out.')
    _marker('stream-processor', {'message': message})
    return _result('stream-processor', STREAM_COMMAND, response, True, message, started)


def step_point_in_time_recovery() -> dict[str, Any]:
    started = time.perf_counter()
    if not _table(ORDERS):
        return _result('point-in-time-recovery', PITR_COMMAND, {}, False, f'Create {ORDERS} first.', started)
    dynamodb = _client('dynamodb')
    dynamodb.update_continuous_backups(TableName=ORDERS, PointInTimeRecoverySpecification={
        'PointInTimeRecoveryEnabled': True, 'RecoveryPeriodInDays': PITR_DAYS})
    restore: dict[str, Any]
    try:
        dynamodb.restore_table_to_point_in_time(SourceTableName=ORDERS, TargetTableName=f'{ORDERS}-restored',
                                                UseLatestRestorableTime=True)
        restore = {'Status': 'started', 'TargetTableName': f'{ORDERS}-restored'}
    except ClientError as exc:
        restore = {'Status': 'refused', 'Code': _code(exc), 'Message': exc.response.get('Error', {}).get('Message')}
    response = {'ContinuousBackups': _continuous_backups(), 'RestoreAttempt': restore}
    verified, message = _check_pitr()
    if verified and restore['Status'] == 'refused':
        message += f' The restore was refused ({restore["Code"]}): Floci stores PITR but cannot restore.'
    return _result('point-in-time-recovery', PITR_COMMAND, response, verified, message, started)


def step_global_table_replica() -> dict[str, Any]:
    started = time.perf_counter()
    table = _table(ORDERS)
    if not table:
        return _result('global-table-replica', REPLICA_COMMAND, {}, False, f'Create {ORDERS} first.', started)
    if _billing_mode(table) == 'PROVISIONED' and 'dynamodb:table:WriteCapacityUnits' not in _scalable_targets():
        return _result('global-table-replica', REPLICA_COMMAND, {}, False,
                       'Set up auto scaling first: AWS refuses a replica for a provisioned table without write '
                       'auto scaling.', started)
    replicas = {replica.get('RegionName') for replica in table.get('Replicas') or []}
    if REPLICA_REGION not in replicas:
        _client('dynamodb').update_table(TableName=ORDERS, ReplicaUpdates=[{'Create': {'RegionName': REPLICA_REGION}}])
        _wait(lambda: any(replica.get('RegionName') == REPLICA_REGION and replica.get('ReplicaStatus') == 'ACTIVE'
                          for replica in (_table(ORDERS) or {}).get('Replicas') or []), timeout=60, delay=2)
    table = _table(ORDERS) or {}
    remote = _table(ORDERS, REPLICA_REGION)
    response = {'Replicas': table.get('Replicas'), 'TableInReplicaRegion': bool(remote)}
    verified, message = _check_replica()
    if verified and not remote:
        message += f' Floci created no table in {REPLICA_REGION}, so nothing is replicated.'
    return _result('global-table-replica', REPLICA_COMMAND, response, verified, message, started)


RUNNERS = {
    'provisioned-table': step_provisioned_table,
    'size-capacity': step_size_capacity,
    'auto-scaling': step_auto_scaling,
    'on-demand-table': step_on_demand_table,
    'ttl-expiry': step_ttl_expiry,
    'stream-processor': step_stream_processor,
    'point-in-time-recovery': step_point_in_time_recovery,
    'global-table-replica': step_global_table_replica,
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
    checks = {
        'provisioned-table': lambda: _check_orders()[0],
        'size-capacity': lambda: _check_capacity()[0],
        'auto-scaling': lambda: _check_scaling()[0],
        'on-demand-table': lambda: _check_sessions()[0],
        'ttl-expiry': lambda: _check_ttl()[0],
        'stream-processor': lambda: _check_stream()[0],
        'point-in-time-recovery': lambda: _check_pitr()[0],
        'global-table-replica': lambda: _check_replica()[0],
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


def reset() -> dict[str, Any]:
    """Delete the mapping, function and role, the scaling policies and targets, and the three tables."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> bool:
        try:
            call()
            deleted.append(label)
            return True
        except ClientError as exc:
            code = _code(exc)
            if code in {'ResourceNotFoundException', 'NoSuchEntity', 'ObjectNotFoundException'} or 'NotFound' in code:
                return False
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')
        return False

    lam, iam, scaling, dynamodb = (_client(name) for name in ('lambda', 'iam', 'application-autoscaling', 'dynamodb'))
    try:
        stream_arn = (_table(ORDERS) or {}).get('LatestStreamArn')
        mapping = _mapping(stream_arn) if _function() else None
    except (ClientError, BotoCoreError) as exc:
        mapping = None
        errors.append(f'event source mapping: {exc}')
    if mapping:
        attempt(f"event-source-mapping:{mapping['UUID']}", lambda: lam.delete_event_source_mapping(UUID=mapping['UUID']))
    attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    attempt(f'role-policy:{ROLE_POLICY}', lambda: iam.delete_role_policy(RoleName=ROLE_NAME, PolicyName=ROLE_POLICY))
    attempt(f'role:{ROLE_NAME}', lambda: iam.delete_role(RoleName=ROLE_NAME))
    for dimension in ('dynamodb:table:ReadCapacityUnits', 'dynamodb:table:WriteCapacityUnits'):
        name = f'{ORDERS}-{dimension.rsplit(":", 1)[-1]}'
        attempt(f'scaling-policy:{name}', lambda name=name, dimension=dimension: scaling.delete_scaling_policy(
            PolicyName=name, ServiceNamespace='dynamodb', ResourceId=f'table/{ORDERS}', ScalableDimension=dimension))
        attempt(f'scalable-target:{dimension}', lambda dimension=dimension: scaling.deregister_scalable_target(
            ServiceNamespace='dynamodb', ResourceId=f'table/{ORDERS}', ScalableDimension=dimension))
    for alarm in sorted(_safe_alarms(errors)):
        attempt(f'alarm:{alarm}', lambda alarm=alarm: _client('cloudwatch').delete_alarms(AlarmNames=[alarm]))
    for table in (ORDERS, SESSIONS, AUDIT):
        attempt(f'table:{table}', lambda table=table: dynamodb.delete_table(TableName=table))

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            'aws lambda delete-event-source-mapping --uuid <mapping-uuid>\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws iam delete-role-policy --role-name {ROLE_NAME} --policy-name {ROLE_POLICY}\n'
            f'aws iam delete-role --role-name {ROLE_NAME}\n'
            'aws application-autoscaling deregister-scalable-target --service-namespace dynamodb '
            f'--resource-id table/{ORDERS} --scalable-dimension dynamodb:table:WriteCapacityUnits   # and reads\n'
            f'aws dynamodb delete-table --table-name {ORDERS}\n'
            f'aws dynamodb delete-table --table-name {SESSIONS}\n'
            f'aws dynamodb delete-table --table-name {AUDIT}'
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
            else 'The stream processor, its role, the scaling setup, and the three tables are deleted.',
        },
    }


def _safe_alarms(errors: list[str]) -> set[str]:
    try:
        return _alarm_names()
    except (ClientError, BotoCoreError) as exc:
        errors.append(f'alarms: {exc}')
        return set()
