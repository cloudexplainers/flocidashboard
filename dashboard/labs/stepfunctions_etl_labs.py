"""Event-driven batch ETL: CSV files land in S3, a Step Functions Map state runs a Lambda transform on each file,
valid rows go to date-partitioned JSON, bad rows and bad files are set aside, a DynamoDB manifest makes reruns safe,
failures alert through SNS to SQS, new files start the workflow through EventBridge, a nightly EventBridge Scheduler
sweep catches anything missed, and Athena queries the curated output.

Floci 2.4.0 runs all of it for real: the Lambda function runs in a container and calls S3 and DynamoDB, the state
machine runs Map, Retry, Catch, Choice and JSONPath filters, S3 sends Object Created events to EventBridge, and
Floci checks the IAM policy of both the workflow role and the function role. What Floci does not do: its Step
Functions has no Athena or Glue service integrations ("Unsupported resource"), so the workflow calls Lambda, and
the lab runs the Athena check itself. A Glue ETL job would not run its script on Floci, which is why the transform
is a Lambda function.
"""

from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'stepfunctions'
LAB_KEY = 'etl-pipeline'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

BUCKET = 'lab-etl-lake'
INCOMING = 'incoming/'
CURATED = 'curated/orders/'
REJECTED = 'rejected/'
RESULTS = 'athena-results/'
MANIFEST = 'lab-etl-manifest'
FUNCTION = 'lab-etl-transform'
FUNCTION_ROLE = 'lab-etl-transform-role'
WORKFLOW = 'lab-etl-pipeline'
WORKFLOW_ROLE = 'lab-etl-workflow-role'
EVENTS_ROLE = 'lab-etl-events-role'
SCHEDULER_ROLE = 'lab-etl-scheduler-role'
TOPIC = 'lab-etl-alerts'
ALERT_QUEUE = 'lab-etl-alerts-inbox'
SCHEDULE_DLQ = 'lab-etl-schedule-dlq'
RULE = 'lab-etl-new-file'
RULE_TARGET = 'etl-workflow'
SCHEDULE = 'lab-etl-nightly'
SCHEDULE_CRON = 'cron(0 2 * * ? *)'
DATABASE = 'lab_etl'
TABLE = 'orders_curated'
INLINE_POLICY = 'lab-etl'

FUNCTION_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{FUNCTION_ROLE}'
WORKFLOW_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{WORKFLOW_ROLE}'
EVENTS_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{EVENTS_ROLE}'
SCHEDULER_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{SCHEDULER_ROLE}'
FUNCTION_ARN = f'arn:aws:lambda:{REGION}:{ACCOUNT}:function:{FUNCTION}'
WORKFLOW_ARN = f'arn:aws:states:{REGION}:{ACCOUNT}:stateMachine:{WORKFLOW}'
TOPIC_ARN = f'arn:aws:sns:{REGION}:{ACCOUNT}:{TOPIC}'
MANIFEST_ARN = f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{MANIFEST}'

EXECUTION_TIMEOUT = 120
EVENT_TIMEOUT = 90
ATHENA_TIMEOUT = 60
FLOCI = 'How Floci emulates this: '

# ── Sample data ──────────────────────────────────────────────────────────────

HEADER = ['order_id', 'order_date', 'customer', 'amount']
LEGACY_HEADER = 'id;date;cust;total'
DAY_ONE = '2026-11-01'
DAY_TWO = '2026-11-02'
DAY_THREE = '2026-11-03'
DAY_FOUR = '2026-11-04'


def _csv(rows: list[list[Any]], header: list[str] | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(header or HEADER)
    writer.writerows(rows)
    return buffer.getvalue()


FILES = {
    f'orders-{DAY_ONE}.csv': _csv([
        [101, DAY_ONE, 'ana', '12.50'], [102, DAY_ONE, 'ben', '30.00'], [103, DAY_ONE, 'chen', '8.25'],
        [104, DAY_ONE, 'dara', '41.00'], [105, DAY_ONE, 'eli', '19.75'],
    ]),
    # One late order from the day before, and one row with an amount that is not a number.
    f'orders-{DAY_TWO}.csv': _csv([
        [201, DAY_TWO, 'fay', '22.00'], [202, DAY_TWO, 'gus', 'n/a'], [203, DAY_TWO, 'ana', '15.00'],
        [204, DAY_ONE, 'ben', '5.50'], [205, DAY_TWO, 'chen', '60.00'],
    ]),
}
LEGACY_FILE = 'orders-legacy.csv'
LEGACY_ROWS = [[301, DAY_THREE, 'dara', '11.00'], [302, DAY_THREE, 'eli', '27.50'], [303, DAY_THREE, 'fay', '9.00']]
LEGACY_BODY = LEGACY_HEADER + '\n' + ''.join(';'.join(str(value) for value in row) + '\n' for row in LEGACY_ROWS)
FIXED_LEGACY_BODY = _csv(LEGACY_ROWS)
NEW_FILE = f'orders-{DAY_FOUR}.csv'
NEW_BODY = _csv([[401, DAY_FOUR, 'gus', '33.00'], [402, DAY_FOUR, 'ana', '14.00'], [403, DAY_FOUR, 'ben', '7.25'],
                 [404, DAY_FOUR, 'chen', '18.00']])
BATCH = [*FILES, LEGACY_FILE]


def incoming(name: str) -> str:
    return INCOMING + name


EXPECTED_AFTER_BATCH = {DAY_ONE: 6, DAY_TWO: 3}
EXPECTED_FINAL = {DAY_ONE: 6, DAY_TWO: 3, DAY_THREE: 3, DAY_FOUR: 4}
EXPECTED_AMOUNTS = {DAY_ONE: 117.0, DAY_TWO: 97.0, DAY_THREE: 47.5, DAY_FOUR: 72.25}

# ── The transform function ───────────────────────────────────────────────────

TRANSFORM_SOURCE = '''
import csv
import datetime
import io
import json
import os

import boto3

s3 = boto3.client('s3')
dynamodb = boto3.client('dynamodb')
TABLE = os.environ['MANIFEST_TABLE']
CURATED = os.environ['CURATED_PREFIX']
REJECTED = os.environ['REJECTED_PREFIX']
HEADER = ['order_id', 'order_date', 'customer', 'amount']


def record(key, etag, status, **fields):
    item = {'object_key': {'S': key}, 'etag': {'S': etag}, 'status': {'S': status},
            'processed_at': {'S': datetime.datetime.now(datetime.timezone.utc).isoformat()}}
    for name, value in fields.items():
        item[name] = {'N': str(value)} if isinstance(value, int) else {'S': str(value)}
    dynamodb.put_item(TableName=TABLE, Item=item)


def handler(event, context):
    bucket, key, dry_run = event['bucket'], event['key'], bool(event.get('dryRun'))
    etag = s3.head_object(Bucket=bucket, Key=key)['ETag'].strip('"')
    done = dynamodb.get_item(TableName=TABLE, Key={'object_key': {'S': key}}).get('Item')
    if not dry_run and done and done['status']['S'] == 'DONE' and done['etag']['S'] == etag:
        return {'key': key, 'status': 'skipped', 'rows': int(done['rows']['N'])}

    reader = csv.reader(io.StringIO(s3.get_object(Bucket=bucket, Key=key)['Body'].read().decode('utf-8')))
    header = next(reader, [])
    if header != HEADER:
        if not dry_run:
            record(key, etag, 'FAILED', error='BadHeader')
        raise ValueError(f'BadHeader: {key} has columns {header}, expected {HEADER}')

    by_day, rejected = {}, []
    for line_number, line in enumerate(reader, start=2):
        if not line:
            continue
        row = dict(zip(HEADER, line))
        try:
            day = datetime.date.fromisoformat(row['order_date']).isoformat()
            clean = {'order_id': int(row['order_id']), 'customer': row['customer'].strip(),
                     'amount': round(float(row['amount']), 2)}
        except (KeyError, ValueError) as exc:
            rejected.append({'line': line_number, 'row': line, 'error': str(exc)})
            continue
        by_day.setdefault(day, []).append(clean)

    rows = sum(len(items) for items in by_day.values())
    if dry_run:
        return {'key': key, 'status': 'dry-run', 'rows': rows, 'rejected': len(rejected), 'days': sorted(by_day)}

    name = key.rsplit('/', 1)[-1].rsplit('.', 1)[0]
    outputs = []
    for day, items in sorted(by_day.items()):
        out = f'{CURATED}dt={day}/{name}.json'
        body = ''.join(json.dumps(item) + '\\n' for item in items)
        s3.put_object(Bucket=bucket, Key=out, Body=body.encode(), ContentType='application/json')
        outputs.append(out)
    if rejected:
        s3.put_object(Bucket=bucket, Key=f'{REJECTED}{name}.json', Body=json.dumps(rejected).encode(),
                      ContentType='application/json')
    record(key, etag, 'DONE', rows=rows, rejected=len(rejected), outputs=','.join(outputs))
    return {'key': key, 'status': 'processed', 'rows': rows, 'rejected': len(rejected), 'outputs': outputs}
'''


def _transform_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('index.py', TRANSFORM_SOURCE)
    return buffer.getvalue()


# ── Policies and the workflow ────────────────────────────────────────────────

def _trust(service: str) -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': [
        {'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole'}]}


FUNCTION_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject'], 'Resource': f'arn:aws:s3:::{BUCKET}/*'},
    {'Effect': 'Allow', 'Action': ['dynamodb:GetItem', 'dynamodb:PutItem'], 'Resource': MANIFEST_ARN},
    {'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
     'Resource': '*'},
]}
WORKFLOW_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': 'lambda:InvokeFunction', 'Resource': [FUNCTION_ARN, f'{FUNCTION_ARN}:*']},
    {'Effect': 'Allow', 'Action': 'sns:Publish', 'Resource': TOPIC_ARN},
]}
START_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': 'states:StartExecution', 'Resource': WORKFLOW_ARN},
]}


def scheduler_policy(dlq_arn: str) -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': [
        *START_POLICY['Statement'],
        {'Effect': 'Allow', 'Action': 'sqs:SendMessage', 'Resource': dlq_arn},
    ]}


def workflow_definition() -> dict[str, Any]:
    return {
        'Comment': 'Transform each landed file; set bad files aside; alert when any file failed.',
        'StartAt': 'TransformFiles',
        'States': {
            'TransformFiles': {
                'Type': 'Map',
                'ItemsPath': '$.files',
                'MaxConcurrency': 4,
                'ItemSelector': {'bucket.$': '$.bucket', 'key.$': '$$.Map.Item.Value'},
                'ItemProcessor': {
                    'ProcessorConfig': {'Mode': 'INLINE'},
                    'StartAt': 'Transform',
                    'States': {
                        'Transform': {
                            'Type': 'Task',
                            'Resource': 'arn:aws:states:::lambda:invoke',
                            'Parameters': {'FunctionName': FUNCTION_ARN, 'Payload.$': '$'},
                            'OutputPath': '$.Payload',
                            'Retry': [{
                                'ErrorEquals': ['Lambda.ServiceException', 'Lambda.TooManyRequestsException',
                                                'Lambda.SdkClientException'],
                                'IntervalSeconds': 1, 'MaxAttempts': 3, 'BackoffRate': 2.0,
                            }],
                            'Catch': [{'ErrorEquals': ['States.ALL'], 'ResultPath': '$.error', 'Next': 'SetAside'}],
                            'End': True,
                        },
                        'SetAside': {
                            'Type': 'Pass',
                            'Parameters': {'key.$': '$.key', 'status': 'failed', 'failed': True,
                                           'cause.$': '$.error.Cause'},
                            'End': True,
                        },
                    },
                },
                'ResultPath': '$.results',
                'Next': 'Summarize',
            },
            'Summarize': {
                'Type': 'Pass',
                'Parameters': {
                    'failedFiles.$': '$.results[?(@.failed)].key',
                    'failedCount.$': 'States.ArrayLength($.results[?(@.failed)])',
                    'fileCount.$': 'States.ArrayLength($.results)',
                },
                'ResultPath': '$.summary',
                'Next': 'AnyFailed',
            },
            'AnyFailed': {
                'Type': 'Choice',
                'Choices': [{'Variable': '$.summary.failedCount', 'NumericGreaterThan': 0, 'Next': 'Alert'}],
                'Default': 'Done',
            },
            'Alert': {
                'Type': 'Task',
                'Resource': 'arn:aws:states:::sns:publish',
                'Parameters': {
                    'TopicArn': TOPIC_ARN,
                    'Subject': 'ETL files failed',
                    'Message.$': 'States.JsonToString($.summary)',
                },
                'ResultPath': None,
                'Next': 'Done',
            },
            'Done': {'Type': 'Succeed'},
        },
    }


def event_pattern() -> dict[str, Any]:
    return {'source': ['aws.s3'], 'detail-type': ['Object Created'],
            'detail': {'bucket': {'name': [BUCKET]}, 'object': {'key': [{'prefix': INCOMING}]}}}


INPUT_TRANSFORMER = {
    'InputPathsMap': {'bucket': '$.detail.bucket.name', 'key': '$.detail.object.key'},
    'InputTemplate': '{"bucket": <bucket>, "files": [<key>]}',
}


def sweep_input() -> dict[str, Any]:
    return {'bucket': BUCKET, 'files': [incoming(name) for name in [*BATCH, NEW_FILE]]}


# ── Commands ─────────────────────────────────────────────────────────────────

LAND_COMMAND = (
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    + ''.join(f'aws s3 cp {name} s3://{BUCKET}/{incoming(name)}\n' for name in BATCH)
    + f'aws dynamodb create-table --table-name {MANIFEST} --attribute-definitions AttributeName=object_key,'
    'AttributeType=S --key-schema AttributeName=object_key,KeyType=HASH --billing-mode PAY_PER_REQUEST'
)
FUNCTION_COMMAND = (
    f'aws iam create-role --role-name {FUNCTION_ROLE} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws iam put-role-policy --role-name {FUNCTION_ROLE} --policy-name {INLINE_POLICY} '
    '--policy-document file://transform-policy.json   # S3 objects and the manifest only\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime python3.12 --handler index.handler '
    f'--role {FUNCTION_ROLE_ARN} --zip-file fileb://transform.zip --timeout 60 '
    f'--environment "Variables={{MANIFEST_TABLE={MANIFEST},CURATED_PREFIX={CURATED},REJECTED_PREFIX={REJECTED}}}"\n'
    f'aws lambda invoke --function-name {FUNCTION} --cli-binary-format raw-in-base64-out '
    f'--payload \'{{"bucket":"{BUCKET}","key":"{incoming(BATCH[1])}","dryRun":true}}\' out.json'
)
WORKFLOW_COMMAND = (
    f'aws sns create-topic --name {TOPIC}\n'
    f'aws sqs create-queue --queue-name {ALERT_QUEUE}\n'
    f'aws sns subscribe --topic-arn {TOPIC_ARN} --protocol sqs '
    f'--notification-endpoint arn:aws:sqs:{REGION}:{ACCOUNT}:{ALERT_QUEUE}\n'
    f'aws iam create-role --role-name {WORKFLOW_ROLE} --assume-role-policy-document file://states-trust.json\n'
    f'aws iam put-role-policy --role-name {WORKFLOW_ROLE} --policy-name {INLINE_POLICY} '
    '--policy-document file://workflow-policy.json   # invoke the transform, publish alerts\n'
    f'aws stepfunctions create-state-machine --name {WORKFLOW} --role-arn {WORKFLOW_ROLE_ARN} '
    '--definition file://etl-pipeline.asl.json'
)
RUN_COMMAND = (
    f'aws stepfunctions start-execution --state-machine-arn {WORKFLOW_ARN} '
    f'--input \'{{"bucket":"{BUCKET}","files":["{incoming(BATCH[0])}","{incoming(BATCH[1])}",'
    f'"{incoming(BATCH[2])}"]}}\'\n'
    'aws stepfunctions describe-execution --execution-arn <execution-arn>\n'
    f'aws s3 ls s3://{BUCKET}/{CURATED} --recursive\n'
    f'aws sqs receive-message --queue-url http://localhost:4566/{ACCOUNT}/{ALERT_QUEUE}'
)
EVENT_COMMAND = (
    f'aws s3api put-bucket-notification-configuration --bucket {BUCKET} '
    '--notification-configuration \'{"EventBridgeConfiguration":{}}\'\n'
    f'aws events put-rule --name {RULE} --event-pattern file://new-file-pattern.json\n'
    f'aws iam create-role --role-name {EVENTS_ROLE} --assume-role-policy-document file://events-trust.json\n'
    f'aws events put-targets --rule {RULE} --targets file://workflow-target.json   # InputTransformer builds the input\n'
    f'aws s3 cp {NEW_FILE} s3://{BUCKET}/{incoming(NEW_FILE)}'
)
SWEEP_COMMAND = (
    f'aws sqs create-queue --queue-name {SCHEDULE_DLQ}\n'
    f'aws iam create-role --role-name {SCHEDULER_ROLE} --assume-role-policy-document file://scheduler-trust.json\n'
    f'aws scheduler create-schedule --name {SCHEDULE} --schedule-expression "{SCHEDULE_CRON}" '
    '--schedule-expression-timezone UTC --flexible-time-window Mode=FLEXIBLE,MaximumWindowInMinutes=15 '
    '--target file://sweep-target.json   # retry policy and dead-letter queue inside\n'
    f'aws stepfunctions start-execution --state-machine-arn {WORKFLOW_ARN} --input file://sweep-input.json   '
    '# run the nightly input once now'
)
FIX_COMMAND = (
    f'aws dynamodb get-item --table-name {MANIFEST} --key \'{{"object_key":{{"S":"{incoming(LEGACY_FILE)}"}}}}\'\n'
    f'aws s3 cp {LEGACY_FILE} s3://{BUCKET}/{incoming(LEGACY_FILE)}   # header fixed; the new ETag starts a run\n'
    f'aws stepfunctions list-executions --state-machine-arn {WORKFLOW_ARN} --max-results 1'
)
QUERY_COMMAND = (
    f'aws glue create-database --database-input Name={DATABASE}\n'
    f'aws glue create-table --database-name {DATABASE} --table-input file://orders_curated.json   '
    '# JSON, partitioned by dt\n'
    f'aws glue batch-create-partition --database-name {DATABASE} --table-name {TABLE} '
    '--partition-input-list file://curated-partitions.json\n'
    f'aws athena start-query-execution --query-string "SELECT dt, COUNT(*) AS orders, SUM(amount) AS revenue '
    f'FROM {DATABASE}.{TABLE} GROUP BY dt ORDER BY dt" --result-configuration OutputLocation=s3://{BUCKET}/{RESULTS}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Build an event-driven batch ETL pipeline with Step Functions, Lambda, and S3',
    'description': (
        'Land CSV files in S3, transform each file with a Lambda function inside a Step Functions Map state, write '
        'valid rows as date-partitioned JSON and set bad rows and bad files aside, keep a DynamoDB manifest so '
        'reruns never duplicate data, alert on failures through SNS and SQS, start the workflow from S3 events and '
        'a nightly schedule, replay a fixed file, and check the result with Athena.'
    ),
    'steps': [
        {
            'key': 'land-files',
            'title': f'Land three order files in s3://{BUCKET}/{INCOMING} and create the {MANIFEST} table',
            'command': LAND_COMMAND,
            'explanation': (
                'Batch pipelines start where files arrive. Each source drops a file in a landing prefix, and the '
                'pipeline never edits it: the raw file stays as the record of what was received, and every output is '
                f'rebuilt from it. Two files follow the agreed layout ({", ".join(HEADER)}); {DAY_TWO} also holds a '
                f'late order from {DAY_ONE} and one amount that is not a number. {LEGACY_FILE} comes from an old '
                'system that still sends semicolons and other column names. The DynamoDB manifest will record each '
                'file the pipeline finishes, keyed by object key and ETag, so a rerun knows what it already did. '
                + FLOCI + 'S3 and DynamoDB behave as on AWS for these calls.'
            ),
        },
        {
            'key': 'transform-function',
            'title': f'Write the {FUNCTION} function and test it with a dry run',
            'command': FUNCTION_COMMAND,
            'explanation': (
                'The transform reads one file, checks the header, and checks every row: order_id must be a number, '
                'order_date a date, amount a number. Good rows are grouped by order date, not by file date, so the '
                f'late order lands in the {DAY_ONE} partition. Each output is named after its source file '
                f'(curated/orders/dt=.../<file>.json), so running the same file again overwrites its own output '
                'instead of adding a copy: the write is idempotent. Bad rows go to rejected/ with the reason, so one '
                'bad row does not stop a file; a bad header fails the whole file. The role allows only GetObject and '
                'PutObject on the bucket and GetItem and PutItem on the manifest (least privilege). A dry run '
                'parses without writing. For big files the same code runs as a Glue job (PySpark) or on EMR. '
                + FLOCI + 'the function runs in a real Lambda container and calls Floci S3 and DynamoDB, and Floci '
                'checks the role policy on each call. Glue jobs exist in Floci but do not run their scripts, so this '
                'lab transforms in Lambda.'
            ),
        },
        {
            'key': 'state-machine',
            'title': f'Create the {WORKFLOW} state machine with Map, Retry, Catch, and an alert',
            'command': WORKFLOW_COMMAND,
            'explanation': (
                'Step Functions orchestrates the files. A Map state runs the transform for every file in the input, '
                'four at a time (MaxConcurrency). Retry covers transient errors (Lambda.ServiceException, throttling) '
                'with exponential backoff; it does not retry BadHeader, because the same file would fail again. '
                'Catch turns a failed file into a result marked failed, so the other files still finish. Summarize '
                'counts the failures with a JSONPath filter and States.ArrayLength, and a Choice state publishes to '
                f'the {TOPIC} topic only when something failed. The topic delivers to the {ALERT_QUEUE} queue (in '
                'production, also to email or a pager). The workflow role may invoke only this function and publish '
                'only to this topic. Amazon MWAA (Airflow) and Glue workflows are the other orchestrators on the exam. '
                + FLOCI + 'Map, Retry, Catch, Choice, intrinsic functions and JSONPath filters run as on AWS, and '
                'Floci refuses lambda:InvokeFunction if the workflow role lacks it. Its Step Functions has no Athena '
                'or Glue integrations, so a workflow that needs them calls a Lambda function.'
            ),
        },
        {
            'key': 'run-batch',
            'title': 'Run the batch and see one file set aside and alerted',
            'command': RUN_COMMAND,
            'explanation': (
                f'The execution transforms the three files. {BATCH[0]} and {BATCH[1]} succeed: the curated prefix '
                f'now has {EXPECTED_AFTER_BATCH[DAY_ONE]} orders for {DAY_ONE} (five plus the late one) and '
                f'{EXPECTED_AFTER_BATCH[DAY_TWO]} for {DAY_TWO}, and rejected/ holds the row with amount n/a. '
                f'{LEGACY_FILE} fails with BadHeader; Catch marks it failed and the execution still succeeds, then '
                'the alert lands in the queue with the file name. The manifest records the two finished files as '
                'DONE and the legacy file as FAILED. Reading the execution history shows which state each file took. '
                + FLOCI + 'the execution and its history are real; the alert reaches SQS through SNS.'
            ),
        },
        {
            'key': 'event-trigger',
            'title': f'Start the workflow from S3 Object Created events with the {RULE} rule',
            'command': EVENT_COMMAND,
            'explanation': (
                'Instead of waiting for a batch, process each file as it lands. The bucket sends its events to '
                f'EventBridge; the {RULE} rule matches Object Created events in {INCOMING} only (so the pipeline\'s '
                'own outputs never trigger it), and an input transformer turns the event into the workflow input '
                '{"bucket": ..., "files": [key]}. The events role may only start this state machine. Uploading '
                f'{NEW_FILE} starts an execution within seconds. S3 Event Notifications straight to Lambda or SQS are '
                'the older way; EventBridge adds filtering, several targets, and replay. '
                + FLOCI + 'S3 sends Object Created events to EventBridge, and the rule starts the state machine '
                'with the transformed input.'
            ),
        },
        {
            'key': 'nightly-sweep',
            'title': f'Schedule a nightly sweep with {SCHEDULE} and prove a rerun adds nothing',
            'command': SWEEP_COMMAND,
            'explanation': (
                'Events can be missed (a rule was disabled, a target failed), so pipelines often add a scheduled '
                f'sweep. {SCHEDULE} runs at 02:00 UTC ({SCHEDULE_CRON}) with a 15-minute flexible window, retries '
                f'twice, and sends events it cannot deliver to the {SCHEDULE_DLQ} dead-letter queue. Its input lists '
                'every landed file. Running that input now shows why the manifest matters: the finished files '
                'return skipped because their key and ETag match a DONE item, the curated row count does not change, '
                'and only the still-broken legacy file fails again. Idempotency is what makes retries, replays and '
                'sweeps safe. '
                + FLOCI + 'the schedule is stored with its retry policy and dead-letter queue; the lab runs its input '
                'once instead of waiting for 02:00.'
            ),
        },
        {
            'key': 'fix-and-replay',
            'title': f'Fix {LEGACY_FILE} and let the upload replay it',
            'command': FIX_COMMAND,
            'explanation': (
                f'The manifest says {LEGACY_FILE} FAILED with BadHeader. The source team resends it with the agreed '
                'header. The upload changes the object\'s ETag and starts the workflow through the event rule; the '
                'transform sees no DONE item for that ETag and processes it, so the three orders reach the '
                f'{DAY_THREE} partition and the manifest flips to DONE. Nothing else is reprocessed. Replaying from the '
                'raw file is only possible because the landing prefix was never changed. '
                + FLOCI + 'the second upload sends a new event and the run is real.'
            ),
        },
        {
            'key': 'query-curated',
            'title': f'Catalog the curated output as {DATABASE}.{TABLE} and check it with Athena',
            'command': QUERY_COMMAND,
            'explanation': (
                f'A Glue table over {CURATED} with dt as the partition key makes the output queryable. Athena counts '
                'orders and sums revenue per day; the counts must match what the manifest says was written '
                f'({", ".join(f"{day}: {count}" for day, count in EXPECTED_FINAL.items())}). A check like this is a '
                'cheap data quality gate at the end of a pipeline: in production a final workflow state would run it '
                'and fail the execution if the numbers drift. '
                + FLOCI + 'Athena runs the SQL on DuckDB over the S3 files. Step Functions in Floci cannot call '
                'Athena directly, so the lab runs the query itself.'
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


# ── Floci lookups ────────────────────────────────────────────────────────────

def _bucket_exists() -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=BUCKET), None) is not None


def _keys(prefix: str) -> list[str]:
    keys: list[str] = []
    s3 = _client('s3')
    token = None
    while True:
        kwargs = {'Bucket': BUCKET, 'Prefix': prefix}
        if token:
            kwargs['ContinuationToken'] = token
        page = _safe_call(lambda: s3.list_objects_v2(**kwargs), {}) or {}
        keys.extend(item['Key'] for item in page.get('Contents', []))
        token = page.get('NextContinuationToken')
        if not token:
            return keys


def _object(key: str) -> str | None:
    return _safe_call(lambda: _client('s3').get_object(Bucket=BUCKET, Key=key)['Body'].read().decode())


def curated_counts() -> dict[str, int]:
    """Rows per dt partition, read straight from the curated JSON files."""
    counts: dict[str, int] = {}
    for key in _keys(CURATED):
        day = key[len(CURATED):].split('/', 1)[0].removeprefix('dt=')
        body = _object(key) or ''
        counts[day] = counts.get(day, 0) + sum(1 for line in body.splitlines() if line.strip())
    return dict(sorted(counts.items()))


def _manifest_table() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('dynamodb').describe_table(TableName=MANIFEST)['Table'])


def manifest_item(key: str) -> dict[str, Any] | None:
    item = _safe_call(lambda: _client('dynamodb').get_item(
        TableName=MANIFEST, Key={'object_key': {'S': key}}).get('Item'))
    if not item:
        return None
    return {name: value.get('S', value.get('N')) for name, value in item.items()}


def _function() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('lambda').get_function(FunctionName=FUNCTION)['Configuration'])


def _state_machine() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('stepfunctions').describe_state_machine(stateMachineArn=WORKFLOW_ARN))


def _executions() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('stepfunctions').list_executions(
        stateMachineArn=WORKFLOW_ARN, maxResults=100).get('executions', []), []) or []


def _describe(execution_arn: str | None) -> dict[str, Any] | None:
    if not execution_arn:
        return None
    return _safe_call(lambda: _client('stepfunctions').describe_execution(executionArn=execution_arn))


def execution_output(execution: dict[str, Any] | None) -> dict[str, Any]:
    try:
        return json.loads((execution or {}).get('output') or '{}')
    except ValueError:
        return {}


def results_by_file(output: dict[str, Any]) -> dict[str, str]:
    return {item.get('key', ''): item.get('status', '') for item in output.get('results', [])}


def _wait_execution(execution_arn: str, timeout: float = EXECUTION_TIMEOUT) -> dict[str, Any] | None:
    found: dict[str, Any] = {}

    def done() -> bool:
        found.update(_describe(execution_arn) or {})
        return found.get('status') not in (None, 'RUNNING')
    _wait(done, timeout)
    return found or None


def _wait_new_execution(known: set[str], timeout: float = EVENT_TIMEOUT) -> dict[str, Any] | None:
    """Wait for an execution that was not in `known` to start and finish."""
    new: list[str] = []

    def started() -> bool:
        new[:] = [item['executionArn'] for item in _executions() if item['executionArn'] not in known]
        return bool(new)
    if not _wait(started, timeout):
        return None
    return _wait_execution(new[0])


def _start(payload: dict[str, Any]) -> dict[str, Any] | None:
    execution_arn = _client('stepfunctions').start_execution(
        stateMachineArn=WORKFLOW_ARN, input=json.dumps(payload))['executionArn']
    return _wait_execution(execution_arn)


def _queue_url(name: str) -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=name)['QueueUrl'])


def _queue_arn(url: str) -> str:
    return _client('sqs').get_queue_attributes(QueueUrl=url, AttributeNames=['QueueArn'])['Attributes']['QueueArn']


def _drain_alerts() -> list[dict[str, Any]]:
    url = _queue_url(ALERT_QUEUE)
    if not url:
        return []
    sqs = _client('sqs')
    alerts: list[dict[str, Any]] = []
    for _ in range(3):
        messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=1).get('Messages', [])
        for message in messages:
            try:
                envelope = json.loads(message['Body'])
                alerts.append(json.loads(envelope.get('Message', '{}')))
            except ValueError:
                alerts.append({'raw': message['Body']})
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message['ReceiptHandle'])
        if alerts:
            break
    return alerts


def _rule_target() -> dict[str, Any] | None:
    targets = _safe_call(lambda: _client('events').list_targets_by_rule(Rule=RULE).get('Targets', []), []) or []
    return next((target for target in targets if target.get('Id') == RULE_TARGET), None)


def _schedule() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('scheduler').get_schedule(Name=SCHEDULE))


def _table() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('glue').get_table(DatabaseName=DATABASE, Name=TABLE)['Table'])


def _ensure_role(name: str, service: str, policy: dict[str, Any], description: str) -> None:
    iam = _client('iam')
    try:
        iam.get_role(RoleName=name)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(_trust(service)), Description=description)
    iam.put_role_policy(RoleName=name, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(policy))


def athena(sql: str) -> dict[str, Any]:
    client = _client('athena')
    query_id = client.start_query_execution(
        QueryString=sql, QueryExecutionContext={'Database': DATABASE},
        ResultConfiguration={'OutputLocation': f's3://{BUCKET}/{RESULTS}'})['QueryExecutionId']
    status: dict[str, Any] = {}

    def done() -> bool:
        status.update(client.get_query_execution(QueryExecutionId=query_id)['QueryExecution']['Status'])
        return status.get('State') in ('SUCCEEDED', 'FAILED', 'CANCELLED')
    _wait(done, ATHENA_TIMEOUT, delay=0.5)
    outcome = {'QueryExecutionId': query_id, 'State': status.get('State'), 'Reason': status.get('StateChangeReason'),
               'Rows': []}
    if status.get('State') == 'SUCCEEDED':
        rows = client.get_query_results(QueryExecutionId=query_id)['ResultSet']['Rows']
        header = [cell.get('VarCharValue') for cell in rows[0]['Data']] if rows else []
        outcome['Rows'] = [dict(zip(header, [cell.get('VarCharValue') for cell in row['Data']])) for row in rows[1:]]
    return outcome


def _table_input() -> dict[str, Any]:
    return {'Name': TABLE, 'TableType': 'EXTERNAL_TABLE', 'Parameters': {'classification': 'json'},
            'PartitionKeys': [{'Name': 'dt', 'Type': 'string'}],
            'StorageDescriptor': {
                'Columns': [{'Name': 'order_id', 'Type': 'bigint'}, {'Name': 'customer', 'Type': 'string'},
                            {'Name': 'amount', 'Type': 'double'}],
                'Location': f's3://{BUCKET}/{CURATED}',
                'InputFormat': 'org.apache.hadoop.mapred.TextInputFormat',
                'OutputFormat': 'org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat',
                'SerdeInfo': {'SerializationLibrary': 'org.openx.data.jsonserde.JsonSerDe'}}}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_land() -> tuple[bool, str]:
    if not _bucket_exists():
        return False, f'Create {BUCKET}.'
    missing = [name for name in BATCH if incoming(name) not in _keys(INCOMING)]
    if missing:
        return False, f'Land {", ".join(missing)}.'
    if not _manifest_table():
        return False, f'Create the {MANIFEST} table.'
    return True, f'Three files are in s3://{BUCKET}/{INCOMING}, and {MANIFEST} is ready.'


def _check_function() -> tuple[bool, str]:
    function = _function()
    if not function:
        return False, f'Create {FUNCTION}.'
    dry_run = _marked('transform-function') or {}
    if dry_run.get('status') != 'dry-run' or dry_run.get('rows') != 4 or dry_run.get('rejected') != 1:
        return False, f'Dry-run {BATCH[1]}: it should parse 4 good rows and 1 rejected row.'
    return True, f'{FUNCTION} parses {BATCH[1]}: 4 good rows across {", ".join(dry_run.get("days", []))}, 1 rejected.'


def _check_workflow() -> tuple[bool, str]:
    machine = _state_machine()
    if not machine:
        return False, f'Create {WORKFLOW}.'
    definition = json.loads(machine.get('definition') or '{}')
    mapped = definition.get('States', {}).get('TransformFiles', {})
    task = mapped.get('ItemProcessor', {}).get('States', {}).get('Transform', {})
    if mapped.get('Type') != 'Map' or not task.get('Retry') or not task.get('Catch'):
        return False, 'The workflow needs a Map state whose Transform task has Retry and Catch.'
    if 'Alert' not in definition.get('States', {}):
        return False, 'Add the Alert state.'
    if not _queue_url(ALERT_QUEUE):
        return False, f'Subscribe {ALERT_QUEUE} to {TOPIC}.'
    return True, f'{WORKFLOW} maps over files with Retry and Catch, and alerts {TOPIC} → {ALERT_QUEUE}.'


def _check_run() -> tuple[bool, str]:
    marker = _marked('run-batch') or {}
    execution = _describe(marker.get('execution'))
    if not execution:
        return False, 'Run the batch.'
    if execution.get('status') != 'SUCCEEDED':
        return False, f'The batch execution is {execution.get("status")}.'
    statuses = results_by_file(execution_output(execution))
    expected = {incoming(BATCH[0]): 'processed', incoming(BATCH[1]): 'processed', incoming(LEGACY_FILE): 'failed'}
    if statuses != expected:
        return False, f'Unexpected file results: {statuses}.'
    if not all(f'{REJECTED}{name.rsplit(".", 1)[0]}.json' in _keys(REJECTED) for name in [BATCH[1]]):
        return False, f'The bad row of {BATCH[1]} should be in {REJECTED}.'
    if not any(incoming(LEGACY_FILE) in (alert.get('failedFiles') or []) for alert in marker.get('alerts', [])):
        return False, f'An alert naming {LEGACY_FILE} should reach {ALERT_QUEUE}.'
    return True, (f'Two files processed and {LEGACY_FILE} set aside; {ALERT_QUEUE} got the alert; curated rows '
                  f'{marker.get("counts")}.')


def _check_event() -> tuple[bool, str]:
    target = _rule_target()
    if not target or target.get('Arn') != WORKFLOW_ARN or not target.get('InputTransformer'):
        return False, f'Point {RULE} at {WORKFLOW} with an input transformer.'
    marker = _marked('event-trigger') or {}
    execution = _describe(marker.get('execution'))
    if not execution or execution.get('status') != 'SUCCEEDED':
        return False, f'Upload {NEW_FILE} and wait for the execution it starts.'
    if results_by_file(execution_output(execution)) != {incoming(NEW_FILE): 'processed'}:
        return False, f'The event run should process {NEW_FILE} only.'
    if f'dt={DAY_FOUR}' not in ' '.join(_keys(CURATED)):
        return False, f'The {DAY_FOUR} partition is missing.'
    return True, f'Uploading {NEW_FILE} started {WORKFLOW} through {RULE}, and dt={DAY_FOUR} exists.'


def _check_sweep() -> tuple[bool, str]:
    schedule = _schedule()
    if not schedule or schedule.get('ScheduleExpression') != SCHEDULE_CRON:
        return False, f'Create {SCHEDULE} with {SCHEDULE_CRON}.'
    target = schedule.get('Target', {})
    if target.get('Arn') != WORKFLOW_ARN or not target.get('DeadLetterConfig', {}).get('Arn'):
        return False, f'Target {WORKFLOW} and give the schedule a dead-letter queue.'
    marker = _marked('nightly-sweep') or {}
    statuses = marker.get('statuses') or {}
    expected = {incoming(name): 'skipped' for name in [*FILES, NEW_FILE]}
    expected[incoming(LEGACY_FILE)] = 'failed'
    if statuses != expected:
        return False, 'Run the sweep input: finished files should be skipped, and only the legacy file fail.'
    if marker.get('before') != marker.get('after'):
        return False, f'The rerun changed the curated rows: {marker.get("before")} → {marker.get("after")}.'
    return True, f'{SCHEDULE} runs nightly; the sweep skipped 3 finished files and left the rows at {marker["after"]}.'


def _check_fix() -> tuple[bool, str]:
    item = manifest_item(incoming(LEGACY_FILE)) or {}
    if item.get('status') != 'DONE':
        return False, f'{LEGACY_FILE} is {item.get("status") or "not in the manifest"}; upload the fixed file.'
    if curated_counts().get(DAY_THREE) != EXPECTED_FINAL[DAY_THREE]:
        return False, f'The {DAY_THREE} partition should hold {EXPECTED_FINAL[DAY_THREE]} orders.'
    return True, f'The fixed {LEGACY_FILE} was replayed: manifest DONE, {EXPECTED_FINAL[DAY_THREE]} orders on {DAY_THREE}.'


def _check_query() -> tuple[bool, str]:
    if not _table():
        return False, f'Create {DATABASE}.{TABLE}.'
    marker = _marked('query-curated') or {}
    counts = marker.get('counts') or {}
    if counts != EXPECTED_FINAL:
        return False, f'Athena counts {counts} should be {EXPECTED_FINAL}.'
    if marker.get('revenue') != EXPECTED_AMOUNTS:
        return False, f'Athena revenue {marker.get("revenue")} should be {EXPECTED_AMOUNTS}.'
    return True, f'Athena agrees with the manifest: {counts}.'


CHECKS = {
    'land-files': _check_land,
    'transform-function': _check_function,
    'state-machine': _check_workflow,
    'run-batch': _check_run,
    'event-trigger': _check_event,
    'nightly-sweep': _check_sweep,
    'fix-and-replay': _check_fix,
    'query-curated': _check_query,
}


def _blocked(step: str, command: str, started: float, needs: str) -> dict[str, Any] | None:
    ready = {
        'bucket': _bucket_exists,
        'function': lambda: _function() is not None,
        'workflow': lambda: _state_machine() is not None,
    }[needs]()
    if ready:
        return None
    return _result(step, command, {}, False, 'Run the earlier steps first.', started)


# ── Steps ────────────────────────────────────────────────────────────────────

def step_land_files() -> dict[str, Any]:
    started = time.perf_counter()
    s3, dynamodb = _client('s3'), _client('dynamodb')
    if not _bucket_exists():
        s3.create_bucket(Bucket=BUCKET)
    bodies = {**FILES, LEGACY_FILE: LEGACY_BODY}
    for name, body in bodies.items():
        s3.put_object(Bucket=BUCKET, Key=incoming(name), Body=body.encode(), ContentType='text/csv')
    if not _manifest_table():
        dynamodb.create_table(
            TableName=MANIFEST, BillingMode='PAY_PER_REQUEST',
            AttributeDefinitions=[{'AttributeName': 'object_key', 'AttributeType': 'S'}],
            KeySchema=[{'AttributeName': 'object_key', 'KeyType': 'HASH'}],
            Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        dynamodb.get_waiter('table_exists').wait(TableName=MANIFEST, WaiterConfig={'Delay': 1, 'MaxAttempts': 30})
    verified, message = _check_land()
    return _result('land-files', LAND_COMMAND, {
        'Landed': {name: body.splitlines()[0] for name, body in bodies.items()}, 'Manifest': MANIFEST,
    }, verified, message, started)


def step_transform_function() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('transform-function', FUNCTION_COMMAND, started, 'bucket')
    if blocked:
        return blocked
    _ensure_role(FUNCTION_ROLE, 'lambda.amazonaws.com', FUNCTION_POLICY, 'ETL transform function')
    lam = _client('lambda')
    environment = {'Variables': {'MANIFEST_TABLE': MANIFEST, 'CURATED_PREFIX': CURATED, 'REJECTED_PREFIX': REJECTED}}
    if _function():
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=_transform_zip())
        _wait(lambda: (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=environment, Timeout=60)
    else:
        lam.create_function(FunctionName=FUNCTION, Runtime='python3.12', Role=FUNCTION_ROLE_ARN,
                            Handler='index.handler', Code={'ZipFile': _transform_zip()}, Timeout=60,
                            Environment=environment, Description='Validates and partitions order files',
                            Tags={'Lab': LAB_KEY})
    _wait(lambda: (_function() or {}).get('State', 'Active') == 'Active'
          and (_function() or {}).get('LastUpdateStatus', 'Successful') != 'InProgress', 30)
    response = lam.invoke(FunctionName=FUNCTION, Payload=json.dumps(
        {'bucket': BUCKET, 'key': incoming(BATCH[1]), 'dryRun': True}).encode())
    payload = json.loads(response['Payload'].read() or b'{}')
    _marker('transform-function', payload)
    verified, message = _check_function()
    return _result('transform-function', FUNCTION_COMMAND, {
        'Function': FUNCTION, 'DryRun': payload, 'FunctionError': response.get('FunctionError'),
    }, verified, message, started)


def step_state_machine() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('state-machine', WORKFLOW_COMMAND, started, 'function')
    if blocked:
        return blocked
    sns, sqs = _client('sns'), _client('sqs')
    sns.create_topic(Name=TOPIC, Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    queue_url = sqs.create_queue(QueueName=ALERT_QUEUE, tags={'Lab': LAB_KEY})['QueueUrl']
    queue_arn = _queue_arn(queue_url)
    sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={'Policy': json.dumps({
        'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': 'sns.amazonaws.com'}, 'Action': 'sqs:SendMessage',
            'Resource': queue_arn, 'Condition': {'ArnEquals': {'aws:SourceArn': TOPIC_ARN}}}]})})
    subscriptions = sns.list_subscriptions_by_topic(TopicArn=TOPIC_ARN).get('Subscriptions', [])
    if not any(item.get('Endpoint') == queue_arn for item in subscriptions):
        sns.subscribe(TopicArn=TOPIC_ARN, Protocol='sqs', Endpoint=queue_arn,
                      Attributes={'RawMessageDelivery': 'false'})
    _ensure_role(WORKFLOW_ROLE, 'states.amazonaws.com', WORKFLOW_POLICY, 'ETL workflow')
    sfn = _client('stepfunctions')
    definition = json.dumps(workflow_definition())
    if _state_machine():
        sfn.update_state_machine(stateMachineArn=WORKFLOW_ARN, definition=definition, roleArn=WORKFLOW_ROLE_ARN)
    else:
        sfn.create_state_machine(name=WORKFLOW, definition=definition, roleArn=WORKFLOW_ROLE_ARN,
                                 tags=[{'key': 'Lab', 'value': LAB_KEY}])
    verified, message = _check_workflow()
    return _result('state-machine', WORKFLOW_COMMAND, {
        'StateMachineArn': WORKFLOW_ARN, 'Definition': workflow_definition(), 'AlertQueue': queue_url,
    }, verified, message, started)


def step_run_batch() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('run-batch', RUN_COMMAND, started, 'workflow')
    if blocked:
        return blocked
    _drain_alerts()
    execution = _start({'bucket': BUCKET, 'files': [incoming(name) for name in BATCH]}) or {}
    alerts = _drain_alerts()
    counts = curated_counts()
    _marker('run-batch', {'execution': execution.get('executionArn'), 'alerts': alerts, 'counts': counts})
    verified, message = _check_run()
    output = execution_output(execution)
    return _result('run-batch', RUN_COMMAND, {
        'Status': execution.get('status'), 'Results': output.get('results'), 'Summary': output.get('summary'),
        'CuratedRows': counts, 'Rejected': _keys(REJECTED), 'Alerts': alerts,
        'Manifest': {name: (manifest_item(incoming(name)) or {}).get('status') for name in BATCH},
    }, verified, message, started)


def step_event_trigger() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('event-trigger', EVENT_COMMAND, started, 'workflow')
    if blocked:
        return blocked
    s3, events = _client('s3'), _client('events')
    s3.put_bucket_notification_configuration(Bucket=BUCKET, NotificationConfiguration={'EventBridgeConfiguration': {}})
    _ensure_role(EVENTS_ROLE, 'events.amazonaws.com', START_POLICY, 'Starts the ETL workflow')
    events.put_rule(Name=RULE, EventPattern=json.dumps(event_pattern()), State='ENABLED',
                    Description='New files in the landing prefix')
    events.put_targets(Rule=RULE, Targets=[{'Id': RULE_TARGET, 'Arn': WORKFLOW_ARN, 'RoleArn': EVENTS_ROLE_ARN,
                                            'InputTransformer': INPUT_TRANSFORMER}])
    known = {item['executionArn'] for item in _executions()}
    s3.put_object(Bucket=BUCKET, Key=incoming(NEW_FILE), Body=NEW_BODY.encode(), ContentType='text/csv')
    execution = _wait_new_execution(known) or {}
    _marker('event-trigger', {'execution': execution.get('executionArn')})
    verified, message = _check_event()
    return _result('event-trigger', EVENT_COMMAND, {
        'Rule': RULE, 'EventPattern': event_pattern(), 'InputTransformer': INPUT_TRANSFORMER,
        'Execution': {'status': execution.get('status'), 'input': execution.get('input'),
                      'results': execution_output(execution).get('results')},
        'CuratedRows': curated_counts(),
    }, verified, message, started)


def step_nightly_sweep() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('nightly-sweep', SWEEP_COMMAND, started, 'workflow')
    if blocked:
        return blocked
    sqs, scheduler = _client('sqs'), _client('scheduler')
    dlq_arn = _queue_arn(sqs.create_queue(QueueName=SCHEDULE_DLQ, tags={'Lab': LAB_KEY})['QueueUrl'])
    _ensure_role(SCHEDULER_ROLE, 'scheduler.amazonaws.com', scheduler_policy(dlq_arn), 'Nightly ETL sweep')
    settings = {
        'ScheduleExpression': SCHEDULE_CRON, 'ScheduleExpressionTimezone': 'UTC',
        'FlexibleTimeWindow': {'Mode': 'FLEXIBLE', 'MaximumWindowInMinutes': 15},
        'Description': 'Nightly sweep of the landing prefix',
        'Target': {'Arn': WORKFLOW_ARN, 'RoleArn': SCHEDULER_ROLE_ARN, 'Input': json.dumps(sweep_input()),
                   'RetryPolicy': {'MaximumRetryAttempts': 2, 'MaximumEventAgeInSeconds': 3600},
                   'DeadLetterConfig': {'Arn': dlq_arn}},
    }
    if _schedule():
        scheduler.update_schedule(Name=SCHEDULE, **settings)
    else:
        scheduler.create_schedule(Name=SCHEDULE, **settings)
    before = curated_counts()
    _drain_alerts()
    execution = _start(sweep_input()) or {}
    _drain_alerts()
    after = curated_counts()
    statuses = results_by_file(execution_output(execution))
    _marker('nightly-sweep', {'execution': execution.get('executionArn'), 'statuses': statuses,
                              'before': before, 'after': after})
    verified, message = _check_sweep()
    return _result('nightly-sweep', SWEEP_COMMAND, {
        'Schedule': {'Name': SCHEDULE, **settings, 'Target': {**settings['Target'], 'Input': sweep_input()}},
        'SweepResults': statuses, 'CuratedRowsBefore': before, 'CuratedRowsAfter': after,
    }, verified, message, started)


def step_fix_and_replay() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('fix-and-replay', FIX_COMMAND, started, 'workflow')
    if blocked:
        return blocked
    before = manifest_item(incoming(LEGACY_FILE))
    known = {item['executionArn'] for item in _executions()}
    _client('s3').put_object(Bucket=BUCKET, Key=incoming(LEGACY_FILE), Body=FIXED_LEGACY_BODY.encode(),
                             ContentType='text/csv')
    execution = _wait_new_execution(known)
    if execution is None:
        # No event rule (the event step was skipped): replay the file directly.
        execution = _start({'bucket': BUCKET, 'files': [incoming(LEGACY_FILE)]}) or {}
    _drain_alerts()
    verified, message = _check_fix()
    return _result('fix-and-replay', FIX_COMMAND, {
        'ManifestBefore': before, 'ManifestAfter': manifest_item(incoming(LEGACY_FILE)),
        'Execution': {'status': execution.get('status'), 'results': execution_output(execution).get('results')},
        'CuratedRows': curated_counts(),
    }, verified, message, started)


def step_query_curated() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _blocked('query-curated', QUERY_COMMAND, started, 'bucket')
    if blocked:
        return blocked
    glue = _client('glue')
    if not _safe_call(lambda: glue.get_database(Name=DATABASE)):
        glue.create_database(DatabaseInput={'Name': DATABASE, 'Description': 'Curated ETL output'})
    if _table():
        glue.delete_table(DatabaseName=DATABASE, Name=TABLE)
    glue.create_table(DatabaseName=DATABASE, TableInput=_table_input())
    days = sorted(curated_counts())
    if days:
        glue.batch_create_partition(DatabaseName=DATABASE, TableName=TABLE, PartitionInputList=[
            {'Values': [day], 'StorageDescriptor': {**_table_input()['StorageDescriptor'],
                                                    'Location': f's3://{BUCKET}/{CURATED}dt={day}/'}}
            for day in days])
    outcome = athena(f'SELECT dt, COUNT(*) AS orders, SUM(amount) AS revenue FROM {TABLE} GROUP BY dt ORDER BY dt')
    counts = {row['dt']: int(_number(row.get('orders'))) for row in outcome['Rows']}
    revenue = {row['dt']: round(_number(row.get('revenue')), 2) for row in outcome['Rows']}
    _marker('query-curated', {'counts': counts, 'revenue': revenue})
    verified, message = _check_query()
    return _result('query-curated', QUERY_COMMAND, {
        'Partitions': days, 'Athena': {'State': outcome['State'], 'Reason': outcome['Reason'], 'Rows': outcome['Rows']},
        'Expected': EXPECTED_FINAL,
    }, verified, message, started)


RUNNERS = {
    'land-files': step_land_files,
    'transform-function': step_transform_function,
    'state-machine': step_state_machine,
    'run-batch': step_run_batch,
    'event-trigger': step_event_trigger,
    'nightly-sweep': step_nightly_sweep,
    'fix-and-replay': step_fix_and_replay,
    'query-curated': step_query_curated,
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
    """Remove the schedule, rule, workflow, function, roles, topic, queues, manifest, catalog, and bucket."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or 'NonExistent' in code or code in (
                    'ResourceNotFoundException', 'EntityNotFoundException', 'StateMachineDoesNotExist'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    if _schedule():
        attempt(f'schedule:{SCHEDULE}', lambda: _client('scheduler').delete_schedule(Name=SCHEDULE))
    events = _client('events')
    if _rule_target():
        attempt(f'target:{RULE_TARGET}', lambda: events.remove_targets(Rule=RULE, Ids=[RULE_TARGET]))
    if _safe_call(lambda: events.describe_rule(Name=RULE)):
        attempt(f'rule:{RULE}', lambda: events.delete_rule(Name=RULE))
    if _state_machine():
        attempt(f'state-machine:{WORKFLOW}', lambda: _client('stepfunctions').delete_state_machine(
            stateMachineArn=WORKFLOW_ARN))
    if _function():
        attempt(f'function:{FUNCTION}', lambda: _client('lambda').delete_function(FunctionName=FUNCTION))
    iam = _client('iam')
    for role in (FUNCTION_ROLE, WORKFLOW_ROLE, EVENTS_ROLE, SCHEDULER_ROLE):
        if _safe_call(lambda role=role: iam.get_role(RoleName=role)):
            attempt(f'policy:{role}', lambda role=role: iam.delete_role_policy(RoleName=role,
                                                                                PolicyName=INLINE_POLICY))
            attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))
    sns = _client('sns')
    for item in _safe_call(lambda: sns.list_subscriptions_by_topic(TopicArn=TOPIC_ARN).get('Subscriptions', []),
                           []) or []:
        attempt('subscription', lambda item=item: sns.unsubscribe(SubscriptionArn=item['SubscriptionArn']))
    if _safe_call(lambda: sns.get_topic_attributes(TopicArn=TOPIC_ARN)):
        attempt(f'topic:{TOPIC}', lambda: sns.delete_topic(TopicArn=TOPIC_ARN))
    for queue in (ALERT_QUEUE, SCHEDULE_DLQ):
        url = _queue_url(queue)
        if url:
            attempt(f'queue:{queue}', lambda url=url: _client('sqs').delete_queue(QueueUrl=url))
    if _manifest_table():
        attempt(f'table:{MANIFEST}', lambda: _client('dynamodb').delete_table(TableName=MANIFEST))
    glue = _client('glue')
    if _table():
        attempt(f'glue-table:{TABLE}', lambda: glue.delete_table(DatabaseName=DATABASE, Name=TABLE))
    if _safe_call(lambda: glue.get_database(Name=DATABASE)):
        attempt(f'glue-database:{DATABASE}', lambda: glue.delete_database(Name=DATABASE))
    if _bucket_exists():
        s3 = _client('s3')
        keys = _keys('')
        for start in range(0, len(keys), 1000):
            chunk = keys[start:start + 1000]
            attempt('objects', lambda chunk=chunk: s3.delete_objects(
                Bucket=BUCKET, Delete={'Objects': [{'Key': key} for key in chunk], 'Quiet': True}))
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))
    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': 'aws stepfunctions delete-state-machine ... # cleanup',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verified': not errors,
        'verification': {
            'status': 'passed' if not errors else 'failed',
            'message': 'ETL pipeline lab resources removed.' if not errors else '; '.join(errors)[:500],
        },
    }
