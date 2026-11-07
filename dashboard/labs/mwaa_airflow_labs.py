"""Airflow pipeline on Amazon MWAA: a versioned DAG bucket, an environment, a DAG that extracts, transforms, loads,
and checks a day of orders, a run triggered through the Airflow CLI, a failed run traced from its task states and
its alert, a rerun with tasks clear, worker scaling, and a broken DAG rolled back from an S3 object version.

Floci 2.4.0 runs a real Apache Airflow 2.10.5 (with its own PostgreSQL) for each environment, syncs the DAG folder
from S3 every 30 seconds, answers the MWAA CLI endpoint, and lets task code call Floci S3 and SNS. What Floci does
not do: it sends no Airflow logs to CloudWatch, so the DAG reports failures itself; it refuses changes to Airflow
configuration options after creation; and the CLI endpoint (WebServerHostname) is reachable only from containers on
Floci's Docker network, which is where the dashboard runs under Docker Compose.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import time
import urllib.error
import urllib.request
from typing import Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'mwaa'
LAB_KEY = 'airflow-pipeline'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

ENVIRONMENT = 'lab-airflow'
AIRFLOW_VERSION = '2.10.5'
DAG_BUCKET = 'lab-airflow-dags'
DATA_BUCKET = 'lab-airflow-data'
DAG_ID = 'orders_daily'
DAG_KEY = f'dags/{DAG_ID}.py'
REQUIREMENTS_KEY = 'requirements.txt'
ROLE = 'lab-airflow-execution'
INLINE_POLICY = 'lab-airflow'
TOPIC = 'lab-airflow-alerts'
ALERT_QUEUE = 'lab-airflow-alerts-inbox'
VARIABLE = 'min_customers'
GOOD_DAY = '2026-11-05'
LATE_DAY = '2026-11-06'
GOOD_RUN = f'lab-{GOOD_DAY}'
LATE_RUN = f'lab-{LATE_DAY}'

ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{ROLE}'
TOPIC_ARN = f'arn:aws:sns:{REGION}:{ACCOUNT}:{TOPIC}'

ENVIRONMENT_WAIT_SECONDS = 240
RUN_WAIT_SECONDS = 150
CLI_TIMEOUT = 90
FLOCI = 'How Floci emulates this: '

# ── Data and the DAG ─────────────────────────────────────────────────────────

ORDERS = {
    GOOD_DAY: [(1, 'ana', '10.00'), (2, 'ben', '5.00'), (3, 'ana', '2.50')],
    LATE_DAY: [(11, 'cara', '7.00'), (12, 'dan', '3.00'), (13, 'cara', '1.00')],
}
EXPECTED_TOTALS = {GOOD_DAY: {'ana': 12.5, 'ben': 5.0}, LATE_DAY: {'cara': 8.0, 'dan': 3.0}}


def orders_csv(day: str) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(['order_id', 'customer', 'amount'])
    writer.writerows(ORDERS[day])
    return out.getvalue()


def raw_key(day: str) -> str:
    return f'raw/dt={day}/orders.csv'


def curated_key(day: str) -> str:
    return f'curated/dt={day}/customer_totals.json'


def alert_key(day: str, task: str) -> str:
    return f'alerts/{day}/{task}.json'


DAG_SOURCE = f'''"""Daily orders: extract the day's raw file, total it per customer, publish, and check quality."""
import csv
import io
import json
from datetime import datetime, timedelta

import boto3
from airflow import DAG
from airflow.models import Variable
from airflow.operators.python import PythonOperator

DATA_BUCKET = '{DATA_BUCKET}'
TOPIC_ARN = '{TOPIC_ARN}'


def alert(context):
    """MWAA sends task logs to CloudWatch; the DAG also reports each failure where on-call looks."""
    ti = context['task_instance']
    body = {{'dag': ti.dag_id, 'run': context['run_id'], 'task': ti.task_id, 'try': ti.try_number,
            'ds': context['ds'], 'error': str(context.get('exception'))[:300]}}
    boto3.client('s3').put_object(Bucket=DATA_BUCKET, Key=f"alerts/{{context['ds']}}/{{ti.task_id}}.json",
                                  Body=json.dumps(body).encode())
    boto3.client('sns').publish(TopicArn=TOPIC_ARN, Subject='Airflow task failed', Message=json.dumps(body))


def extract(ds, **_):
    body = boto3.client('s3').get_object(Bucket=DATA_BUCKET, Key=f'raw/dt={{ds}}/orders.csv')['Body'].read()
    return list(csv.DictReader(io.StringIO(body.decode())))


def transform(ti, **_):
    totals = {{}}
    for row in ti.xcom_pull(task_ids='extract'):
        totals[row['customer']] = round(totals.get(row['customer'], 0) + float(row['amount']), 2)
    return totals


def load(ds, ti, **_):
    totals = ti.xcom_pull(task_ids='transform')
    boto3.client('s3').put_object(Bucket=DATA_BUCKET, Key=f'curated/dt={{ds}}/customer_totals.json',
                                  Body=json.dumps(totals, sort_keys=True).encode())
    return len(totals)


def check(ds, ti, **_):
    minimum = int(Variable.get('{VARIABLE}', default_var=1))
    found = ti.xcom_pull(task_ids='load')
    if found < minimum:
        raise ValueError(f'{{ds}}: {{found}} customers, expected at least {{minimum}}')


with DAG(
    '{DAG_ID}',
    start_date=datetime(2026, 11, 1),
    schedule=None,  # the lab triggers each day; in production: schedule='@daily', catchup=True for backfills
    catchup=False,
    max_active_runs=1,
    default_args={{'retries': 1, 'retry_delay': timedelta(seconds=5), 'on_failure_callback': alert}},
    tags=['lab', 'orders'],
) as dag:
    extract_task = PythonOperator(task_id='extract', python_callable=extract)
    transform_task = PythonOperator(task_id='transform', python_callable=transform)
    load_task = PythonOperator(task_id='load', python_callable=load)
    check_task = PythonOperator(task_id='check', python_callable=check)
    extract_task >> transform_task >> load_task >> check_task
'''

BROKEN_DAG_SOURCE = DAG_SOURCE.replace("    check_task = PythonOperator(task_id='check', python_callable=check)\n",
                                       "    check_task = PythonOperator(task_id='check', python_callable=check\n")
REQUIREMENTS = '# Extra packages for the workers, pinned. This DAG needs only what MWAA ships.\n'

TASKS = ['extract', 'transform', 'load', 'check']

# ── Policies ─────────────────────────────────────────────────────────────────

TRUST = {'Version': '2012-10-17', 'Statement': [{
    'Effect': 'Allow', 'Principal': {'Service': ['airflow.amazonaws.com', 'airflow-env.amazonaws.com']},
    'Action': 'sts:AssumeRole'}]}
EXECUTION_POLICY = {'Version': '2012-10-17', 'Statement': [
    {'Effect': 'Allow', 'Action': ['s3:GetObject*', 's3:GetBucket*', 's3:List*'],
     'Resource': [f'arn:aws:s3:::{DAG_BUCKET}', f'arn:aws:s3:::{DAG_BUCKET}/*']},
    {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject', 's3:ListBucket'],
     'Resource': [f'arn:aws:s3:::{DATA_BUCKET}', f'arn:aws:s3:::{DATA_BUCKET}/*']},
    {'Effect': 'Allow', 'Action': 'sns:Publish', 'Resource': TOPIC_ARN},
    {'Effect': 'Allow', 'Action': ['logs:CreateLogStream', 'logs:CreateLogGroup', 'logs:PutLogEvents',
                                   'logs:GetLogEvents', 'logs:GetLogRecord', 'logs:DescribeLogGroups'],
     'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:airflow-{ENVIRONMENT}-*'},
    {'Effect': 'Allow', 'Action': 'cloudwatch:PutMetricData', 'Resource': '*'},
    {'Effect': 'Allow', 'Action': ['sqs:ChangeMessageVisibility', 'sqs:DeleteMessage', 'sqs:GetQueueAttributes',
                                   'sqs:GetQueueUrl', 'sqs:ReceiveMessage', 'sqs:SendMessage'],
     'Resource': f'arn:aws:sqs:{REGION}:*:airflow-celery-*'},
]}
LOGGING = {name: {'Enabled': True, 'LogLevel': level} for name, level in (
    ('DagProcessingLogs', 'WARNING'), ('SchedulerLogs', 'WARNING'), ('TaskLogs', 'INFO'),
    ('WebserverLogs', 'WARNING'), ('WorkerLogs', 'WARNING'))}

# ── Commands ─────────────────────────────────────────────────────────────────

BUCKET_COMMAND = (
    f'aws s3api create-bucket --bucket {DAG_BUCKET}\n'
    f'aws s3api put-bucket-versioning --bucket {DAG_BUCKET} --versioning-configuration Status=Enabled\n'
    f'aws s3api put-public-access-block --bucket {DAG_BUCKET} --public-access-block-configuration '
    'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true\n'
    f'aws s3 cp requirements.txt s3://{DAG_BUCKET}/{REQUIREMENTS_KEY}\n'
    f'aws s3api create-bucket --bucket {DATA_BUCKET}\n'
    f'aws s3 cp orders-{GOOD_DAY}.csv s3://{DATA_BUCKET}/{raw_key(GOOD_DAY)}\n'
    f'aws sns create-topic --name {TOPIC}\n'
    f'aws sqs create-queue --queue-name {ALERT_QUEUE}\n'
    f'aws sns subscribe --topic-arn {TOPIC_ARN} --protocol sqs '
    f'--notification-endpoint arn:aws:sqs:{REGION}:{ACCOUNT}:{ALERT_QUEUE}'
)
ENVIRONMENT_COMMAND = (
    f'aws iam create-role --role-name {ROLE} --assume-role-policy-document file://airflow-trust.json\n'
    f'aws iam put-role-policy --role-name {ROLE} --policy-name {INLINE_POLICY} '
    '--policy-document file://execution-policy.json\n'
    f'aws mwaa create-environment --name {ENVIRONMENT} --airflow-version {AIRFLOW_VERSION} '
    f'--execution-role-arn {ROLE_ARN} --source-bucket-arn arn:aws:s3:::{DAG_BUCKET} --dag-s3-path dags '
    f'--requirements-s3-path {REQUIREMENTS_KEY} --environment-class mw1.small --min-workers 1 --max-workers 2 '
    '--network-configuration SubnetIds=subnet-private-a,subnet-private-b,SecurityGroupIds=sg-airflow '
    '--webserver-access-mode PRIVATE_ONLY --logging-configuration file://logging.json\n'
    f'aws mwaa get-environment --name {ENVIRONMENT}'
)
DEPLOY_COMMAND = (
    f'aws s3 cp {DAG_ID}.py s3://{DAG_BUCKET}/{DAG_KEY}\n'
    f'aws mwaa create-cli-token --name {ENVIRONMENT}   # then POST "<command>" to https://<host>/aws_mwaa/cli\n'
    '# airflow dags reserialize\n'
    '# airflow dags list-import-errors\n'
    f'# airflow variables set {VARIABLE} 2\n'
    f'# airflow dags unpause {DAG_ID}'
)
TRIGGER_COMMAND = (
    f'aws mwaa create-cli-token --name {ENVIRONMENT}\n'
    f'# airflow dags trigger {DAG_ID} -e {GOOD_DAY} -r {GOOD_RUN}\n'
    f'# airflow tasks states-for-dag-run {DAG_ID} {GOOD_RUN}\n'
    f'aws s3 cp s3://{DATA_BUCKET}/{curated_key(GOOD_DAY)} -'
)
FAILURE_COMMAND = (
    f'# airflow dags trigger {DAG_ID} -e {LATE_DAY} -r {LATE_RUN}   # the {LATE_DAY} file has not arrived\n'
    f'# airflow tasks states-for-dag-run {DAG_ID} {LATE_RUN}\n'
    f'aws s3 cp s3://{DATA_BUCKET}/{alert_key(LATE_DAY, "extract")} -\n'
    f'aws sqs receive-message --queue-url http://localhost:4566/{ACCOUNT}/{ALERT_QUEUE}'
)
RERUN_COMMAND = (
    f'aws s3 cp orders-{LATE_DAY}.csv s3://{DATA_BUCKET}/{raw_key(LATE_DAY)}\n'
    f'# airflow tasks clear {DAG_ID} -t extract --downstream -s {LATE_DAY} -e {LATE_DAY} --yes\n'
    f'# airflow dags list-runs -d {DAG_ID}'
)
TUNE_COMMAND = (
    f'aws mwaa update-environment --name {ENVIRONMENT} --min-workers 1 --max-workers 5\n'
    f'aws mwaa update-environment --name {ENVIRONMENT} --airflow-configuration-options core.parallelism=32\n'
    f'aws mwaa get-environment --name {ENVIRONMENT}'
)
ROLLBACK_COMMAND = (
    f'aws s3 cp {DAG_ID}.py s3://{DAG_BUCKET}/{DAG_KEY}   # a typo: a missing parenthesis\n'
    '# airflow dags list-import-errors\n'
    f'aws s3api list-object-versions --bucket {DAG_BUCKET} --prefix {DAG_KEY}\n'
    f'aws s3api copy-object --bucket {DAG_BUCKET} --key {DAG_KEY} '
    f'--copy-source "{DAG_BUCKET}/{DAG_KEY}?versionId=<previous-version>"\n'
    '# airflow dags reserialize'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Orchestrate a daily pipeline with Airflow on MWAA: DAGs, retries, failures, reruns, and rollbacks',
    'description': (
        'Create a versioned DAG bucket and an MWAA environment, deploy a DAG that extracts, transforms, loads, and '
        'checks a day of orders, trigger it through the Airflow CLI, trace a failed run from its task states and '
        'alert, rerun it with tasks clear, scale the workers, and roll a broken DAG back to the previous S3 version.'
    ),
    'steps': [
        {
            'key': 'dag-bucket',
            'title': f'Create the versioned {DAG_BUCKET} bucket, the data bucket, and the alert topic',
            'command': BUCKET_COMMAND,
            'explanation': (
                'Amazon MWAA reads everything from one S3 bucket: the DAG folder, requirements.txt (extra Python '
                'packages for the workers, pinned), and plugins.zip. The bucket must have versioning on and public '
                'access blocked; MWAA points at a specific version of requirements and plugins, and versioning is also '
                f'how a bad DAG is rolled back later. Data lives in a separate bucket, {DATA_BUCKET}, with one raw '
                f'file for {GOOD_DAY}. Failures will go to the {TOPIC} topic and its {ALERT_QUEUE} queue. '
                + FLOCI + 'S3 versioning and the public access block behave as on AWS.'
            ),
        },
        {
            'key': 'create-environment',
            'title': f'Create the {ENVIRONMENT} environment',
            'command': ENVIRONMENT_COMMAND,
            'explanation': (
                'An environment is a managed Airflow: a scheduler, workers, a web server, and a metadata database. '
                f'The execution role is what DAG code runs as: read the DAG bucket, read and write {DATA_BUCKET}, '
                'publish alerts, write its logs, and use the Celery queues. The environment class (mw1.small) sizes '
                'the scheduler and web server, and the workers scale between the minimum and the maximum. On AWS it '
                'runs in two private subnets with a security group, takes 20 to 30 minutes to create, and sends each '
                'log type to CloudWatch at the level set here. PRIVATE_ONLY keeps the Airflow UI inside the VPC. '
                + FLOCI + f'Floci starts a real Airflow {AIRFLOW_VERSION} container and a PostgreSQL container, ready '
                'in about a minute. The first time, Docker pulls the Airflow image (about 2 GB): if the step reports '
                'that the environment is still creating, run it again.'
            ),
        },
        {
            'key': 'deploy-dag',
            'title': f'Deploy the {DAG_ID} DAG and its variable',
            'command': DEPLOY_COMMAND,
            'explanation': (
                f'{DAG_ID} has four tasks: extract reads the day\'s raw file, transform totals it per customer, load '
                'writes the result to curated/, and check fails the run if fewer customers arrived than the '
                f'{VARIABLE} variable allows. Tasks pass small results through XCom; large data stays in S3. '
                'default_args gives every task one retry and an on_failure_callback that writes the error to S3 and '
                'publishes to SNS. MWAA copies the DAG folder to Airflow; the CLI token lets code run a subset of '
                'Airflow CLI commands through the MWAA API (pools, users and connections export are not in it; '
                'use the Airflow UI or REST API for those). '
                'list-import-errors is the first place to look when a DAG does not appear. '
                + FLOCI + 'Floci syncs the folder every 30 seconds; the lab runs dags reserialize so the scheduler '
                'sees the file now instead of after its next scan.'
            ),
        },
        {
            'key': 'trigger-run',
            'title': f'Trigger a run for {GOOD_DAY} and check the curated output',
            'command': TRIGGER_COMMAND,
            'explanation': (
                f'Triggering with a logical date (-e {GOOD_DAY}) gives the run that day as ds, so every task reads '
                'and writes that day\'s partition: the run is idempotent and can be repeated or backfilled. The four '
                f'tasks run in order and the curated file holds {EXPECTED_TOTALS[GOOD_DAY]}. In production the DAG '
                'would have schedule=\'@daily\' and catchup to fill missed days; the lab triggers each day so it does '
                'not depend on today\'s date. '
                + FLOCI + 'the Airflow scheduler and worker run the tasks for real, and the task code calls Floci S3.'
            ),
        },
        {
            'key': 'inspect-failure',
            'title': f'Run {LATE_DAY} before its file arrives and trace the failure',
            'command': FAILURE_COMMAND,
            'explanation': (
                f'The {LATE_DAY} file is late. extract fails with NoSuchKey, retries once after five seconds, and '
                'fails again; transform, load and check become upstream_failed, and the run fails. The task states '
                'show where it broke, and the failure callback left an alert in S3 and in the queue with the task, '
                'the try number, and the error. On AWS the task log in CloudWatch (airflow-<env>-Task) has the stack '
                'trace. Sensors (an S3KeySensor in reschedule mode) are the usual way to wait for a late file '
                'without holding a worker slot. '
                + FLOCI + 'the retry and the callback are real; Floci keeps task logs inside the Airflow container '
                'and sends none to CloudWatch, which is why the DAG reports failures itself.'
            ),
        },
        {
            'key': 'clear-and-rerun',
            'title': f'Land the {LATE_DAY} file and rerun the failed tasks with tasks clear',
            'command': RERUN_COMMAND,
            'explanation': (
                'Once the file arrives there is no need for a new run. tasks clear resets extract and everything '
                'downstream of it for that logical date, and the scheduler runs them again in the same run, which '
                f'then succeeds with {EXPECTED_TOTALS[LATE_DAY]}. Because each task writes only its own day, the '
                'rerun replaces output instead of duplicating it. Clearing is also how a fixed task is replayed '
                'after a code change. '
                + FLOCI + 'the clear and the rerun happen in the real Airflow scheduler.'
            ),
        },
        {
            'key': 'tune-environment',
            'title': 'Scale the workers, and see which settings need a new environment',
            'command': TUNE_COMMAND,
            'explanation': (
                'MWAA adds Celery workers up to the maximum when tasks queue up and removes them when idle; raise the '
                'maximum for a busy backfill, and raise the minimum when start-up latency matters. Airflow '
                'configuration options (core.parallelism, celery.worker_autoscale and so on) are also set on the '
                'environment; on AWS changing them updates the environment, which takes several minutes and '
                'restarts the components. '
                + FLOCI + 'the worker settings update; Floci refuses a change to the Airflow configuration options '
                'because it would have to recreate the Airflow container, so the lab records that refusal.'
            ),
        },
        {
            'key': 'roll-back-dag',
            'title': f'Ship a broken {DAG_ID}, find it in import errors, and roll back to the previous version',
            'command': ROLLBACK_COMMAND,
            'explanation': (
                'A DAG file with a syntax error does not break the running version in one place only: the scheduler '
                'cannot parse it, the DAG stops being updated, and list-import-errors names the file and the line. '
                'With versioning on, the fix is to copy the previous object version back over the key; the next sync '
                'parses it and the import error clears. In a pipeline, a CI step that imports every DAG (a DagBag '
                'test) catches this before upload. '
                + FLOCI + 'the import error comes from the real Airflow parser, and the rollback is a real S3 '
                'version copy.'
            ),
        },
    ],
}

LABS = [LAB]
STEP_KEYS = [step['key'] for step in LAB['steps']]


# ── Clients, markers, results ────────────────────────────────────────────────

def _client(name: str):
    return FlociClientFactory().client(name)


def _slow_client(name: str):
    # Floci starts the Airflow and Postgres containers inside CreateEnvironment, so a cold
    # image pull outlasts the inventory timeout; a retry would then fail with "already exists".
    factory = FlociClientFactory()
    factory.config = factory.config.merge(Config(read_timeout=300, retries={'max_attempts': 0}))
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

def _environment() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('mwaa').get_environment(Name=ENVIRONMENT)['Environment'])


def _available() -> bool:
    return (_environment() or {}).get('Status') == 'AVAILABLE'


def _bucket_exists(bucket: str) -> bool:
    return _safe_call(lambda: _client('s3').head_bucket(Bucket=bucket), None) is not None


def _object(bucket: str, key: str) -> str | None:
    return _safe_call(lambda: _client('s3').get_object(Bucket=bucket, Key=key)['Body'].read().decode())


def _versions() -> list[dict[str, Any]]:
    response = _safe_call(lambda: _client('s3').list_object_versions(Bucket=DAG_BUCKET, Prefix=DAG_KEY), {}) or {}
    return [item for item in response.get('Versions', []) if item.get('Key') == DAG_KEY]


def _queue_url() -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=ALERT_QUEUE)['QueueUrl'])


def _drain_alerts() -> list[dict[str, Any]]:
    url = _queue_url()
    if not url:
        return []
    sqs = _client('sqs')
    alerts: list[dict[str, Any]] = []
    for _ in range(3):
        for message in sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10,
                                           WaitTimeSeconds=1).get('Messages', []):
            try:
                alerts.append(json.loads(json.loads(message['Body']).get('Message', '{}')))
            except ValueError:
                alerts.append({'raw': message['Body']})
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message['ReceiptHandle'])
        if alerts:
            break
    return alerts


# ── The MWAA CLI ─────────────────────────────────────────────────────────────

class CliUnavailable(RuntimeError):
    pass


def cli(command: str) -> dict[str, Any]:
    """Run an Airflow CLI command through the MWAA CLI token, as `aws mwaa create-cli-token` users do."""
    token = _client('mwaa').create_cli_token(Name=ENVIRONMENT)
    host = token['WebServerHostname']
    scheme = 'https' if host.endswith('amazonaws.com') else 'http'
    request = urllib.request.Request(f'{scheme}://{host}/aws_mwaa/cli', data=command.encode(), method='POST',
                                     headers={'Authorization': f'Bearer {token["CliToken"]}',
                                              'Content-Type': 'text/plain'})
    try:
        with urllib.request.urlopen(request, timeout=CLI_TIMEOUT) as response:  # noqa: S310 - local Floci endpoint
            payload = json.loads(response.read() or b'{}')
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CliUnavailable(
            f'The Airflow CLI endpoint {host} is not reachable from the dashboard ({exc}). Floci serves it on its '
            'Docker network, so run the dashboard with Docker Compose next to Floci.') from exc
    return {'stdout': base64.b64decode(payload.get('stdout') or '').decode(errors='replace'),
            'stderr': base64.b64decode(payload.get('stderr') or '').decode(errors='replace')}


def cli_json(command: str) -> Any:
    out = cli(command)['stdout']
    start = min([index for index in (out.find('['), out.find('{')) if index != -1] or [-1])
    if start == -1:
        return None
    try:
        return json.loads(out[start:])
    except ValueError:
        return None


def task_states(run_id: str) -> dict[str, str]:
    rows = cli_json(f'tasks states-for-dag-run {DAG_ID} {run_id} -o json') or []
    return {row.get('task_id'): row.get('state') for row in rows if isinstance(row, dict)}


def runs() -> dict[str, str]:
    rows = cli_json(f'dags list-runs -d {DAG_ID} -o json') or []
    return {row.get('run_id'): row.get('state') for row in rows if isinstance(row, dict)}


def import_errors() -> list[dict[str, Any]]:
    rows = cli_json('dags list-import-errors -o json') or []
    return [row for row in rows if isinstance(row, dict)]


def dag_ids() -> list[str]:
    """DAGs the scheduler has stored. A parsed file that is not in the metadata database yet has is_paused None."""
    rows = cli_json('dags list -o json') or []
    return [row.get('dag_id') for row in rows if isinstance(row, dict) and row.get('is_paused') is not None]


def _wait_run(run_id: str, timeout: float = RUN_WAIT_SECONDS) -> str | None:
    state: dict[str, str | None] = {'value': None}

    def finished() -> bool:
        state['value'] = runs().get(run_id)
        return state['value'] in ('success', 'failed')
    _wait(finished, timeout, delay=3)
    return state['value']


def _cli_failure(step: str, command: str, started: float, exc: Exception) -> dict[str, Any]:
    return _result(step, command, {'Error': str(exc)}, False, str(exc)[:300], started)


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_buckets() -> tuple[bool, str]:
    s3 = _client('s3')
    if not _bucket_exists(DAG_BUCKET):
        return False, f'Create {DAG_BUCKET}.'
    if s3.get_bucket_versioning(Bucket=DAG_BUCKET).get('Status') != 'Enabled':
        return False, f'Turn on versioning for {DAG_BUCKET}.'
    if _object(DATA_BUCKET, raw_key(GOOD_DAY)) is None:
        return False, f'Land the {GOOD_DAY} file in {DATA_BUCKET}.'
    if not _queue_url():
        return False, f'Subscribe {ALERT_QUEUE} to {TOPIC}.'
    return True, f'{DAG_BUCKET} is versioned and private; {GOOD_DAY} has landed; alerts go to {ALERT_QUEUE}.'


def _check_environment() -> tuple[bool, str]:
    environment = _environment()
    if not environment:
        return False, f'Create {ENVIRONMENT}.'
    if environment.get('Status') != 'AVAILABLE':
        return False, f'{ENVIRONMENT} is {environment.get("Status")}; run the step again in a minute.'
    if environment.get('ExecutionRoleArn') != ROLE_ARN:
        return False, f'Use {ROLE} as the execution role.'
    return True, f'{ENVIRONMENT} runs Airflow {environment.get("AirflowVersion")} and is AVAILABLE.'


def _check_deploy() -> tuple[bool, str]:
    marker = _marked('deploy-dag') or {}
    if DAG_ID not in (marker.get('dags') or []):
        return False, f'Deploy {DAG_ID}.'
    if marker.get('importErrors'):
        return False, f'{DAG_ID} has import errors.'
    if str(marker.get('variable')) != '2':
        return False, f'Set {VARIABLE} to 2.'
    return True, f'{DAG_ID} is deployed with no import errors and {VARIABLE}=2.'


def _check_trigger() -> tuple[bool, str]:
    marker = _marked('trigger-run') or {}
    if marker.get('state') != 'success':
        return False, f'The {GOOD_DAY} run is {marker.get("state") or "not started"}.'
    body = _object(DATA_BUCKET, curated_key(GOOD_DAY))
    if not body or json.loads(body) != EXPECTED_TOTALS[GOOD_DAY]:
        return False, f'The curated {GOOD_DAY} totals should be {EXPECTED_TOTALS[GOOD_DAY]}.'
    return True, f'The {GOOD_DAY} run succeeded: {EXPECTED_TOTALS[GOOD_DAY]}.'


def _check_failure() -> tuple[bool, str]:
    marker = _marked('inspect-failure') or {}
    if marker.get('state') != 'failed':
        return False, f'Run {LATE_DAY} before its file arrives; the run should fail.'
    states = marker.get('tasks') or {}
    if states.get('extract') != 'failed' or any(states.get(task) != 'upstream_failed' for task in TASKS[1:]):
        return False, f'Unexpected task states: {states}.'
    alert = _object(DATA_BUCKET, alert_key(LATE_DAY, 'extract'))
    if not alert or json.loads(alert).get('try', 0) < 2:
        return False, 'The failure callback should report extract after its retry.'
    if not any(item.get('task') == 'extract' for item in marker.get('alerts') or []):
        return False, f'The alert should reach {ALERT_QUEUE}.'
    return True, f'extract failed after a retry, the rest are upstream_failed, and the alert reached {ALERT_QUEUE}.'


def _check_rerun() -> tuple[bool, str]:
    marker = _marked('clear-and-rerun') or {}
    if marker.get('state') != 'success':
        return False, f'Clear and rerun {LATE_RUN}; it is {marker.get("state") or "not rerun"}.'
    body = _object(DATA_BUCKET, curated_key(LATE_DAY))
    if not body or json.loads(body) != EXPECTED_TOTALS[LATE_DAY]:
        return False, f'The curated {LATE_DAY} totals should be {EXPECTED_TOTALS[LATE_DAY]}.'
    return True, f'{LATE_RUN} succeeded after tasks clear: {EXPECTED_TOTALS[LATE_DAY]}.'


def _check_tune() -> tuple[bool, str]:
    environment = _environment() or {}
    if environment.get('MaxWorkers') != 5 or environment.get('MinWorkers') != 1:
        return False, 'Set the workers to a minimum of 1 and a maximum of 5.'
    return True, f'{ENVIRONMENT} scales between 1 and 5 workers.'


def _check_rollback() -> tuple[bool, str]:
    marker = _marked('roll-back-dag') or {}
    if not marker.get('brokenSeen'):
        return False, 'Upload the broken DAG and find it in the import errors.'
    if marker.get('errorsAfter'):
        return False, 'Import errors remain after the rollback.'
    if _object(DAG_BUCKET, DAG_KEY) != DAG_SOURCE:
        return False, f'{DAG_KEY} should be the previous, working version.'
    return True, f'The broken {DAG_ID} showed up in import errors, and the previous version is live again.'


CHECKS = {
    'dag-bucket': _check_buckets,
    'create-environment': _check_environment,
    'deploy-dag': _check_deploy,
    'trigger-run': _check_trigger,
    'inspect-failure': _check_failure,
    'clear-and-rerun': _check_rerun,
    'tune-environment': _check_tune,
    'roll-back-dag': _check_rollback,
}


def _needs_environment(step: str, command: str, started: float) -> dict[str, Any] | None:
    if _available():
        return None
    return _result(step, command, {}, False, f'Create {ENVIRONMENT} first and wait until it is AVAILABLE.', started)


# ── Steps ────────────────────────────────────────────────────────────────────

def step_dag_bucket() -> dict[str, Any]:
    started = time.perf_counter()
    s3, sns, sqs = _client('s3'), _client('sns'), _client('sqs')
    for bucket in (DAG_BUCKET, DATA_BUCKET):
        if not _bucket_exists(bucket):
            s3.create_bucket(Bucket=bucket)
    s3.put_bucket_versioning(Bucket=DAG_BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    s3.put_public_access_block(Bucket=DAG_BUCKET, PublicAccessBlockConfiguration={
        'BlockPublicAcls': True, 'IgnorePublicAcls': True, 'BlockPublicPolicy': True, 'RestrictPublicBuckets': True})
    s3.put_object(Bucket=DAG_BUCKET, Key=REQUIREMENTS_KEY, Body=REQUIREMENTS.encode())
    s3.put_object(Bucket=DATA_BUCKET, Key=raw_key(GOOD_DAY), Body=orders_csv(GOOD_DAY).encode(), ContentType='text/csv')
    sns.create_topic(Name=TOPIC, Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    queue_url = sqs.create_queue(QueueName=ALERT_QUEUE, tags={'Lab': LAB_KEY})['QueueUrl']
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['QueueArn'])['Attributes']['QueueArn']
    sqs.set_queue_attributes(QueueUrl=queue_url, Attributes={'Policy': json.dumps({
        'Version': '2012-10-17', 'Statement': [{
            'Effect': 'Allow', 'Principal': {'Service': 'sns.amazonaws.com'}, 'Action': 'sqs:SendMessage',
            'Resource': queue_arn, 'Condition': {'ArnEquals': {'aws:SourceArn': TOPIC_ARN}}}]})})
    subscriptions = sns.list_subscriptions_by_topic(TopicArn=TOPIC_ARN).get('Subscriptions', [])
    if not any(item.get('Endpoint') == queue_arn for item in subscriptions):
        sns.subscribe(TopicArn=TOPIC_ARN, Protocol='sqs', Endpoint=queue_arn)
    verified, message = _check_buckets()
    return _result('dag-bucket', BUCKET_COMMAND, {
        'DagBucket': DAG_BUCKET, 'Versioning': s3.get_bucket_versioning(Bucket=DAG_BUCKET).get('Status'),
        'DataBucket': DATA_BUCKET, 'Landed': raw_key(GOOD_DAY), 'AlertQueue': queue_url,
    }, verified, message, started)


def step_create_environment() -> dict[str, Any]:
    started = time.perf_counter()
    if not _bucket_exists(DAG_BUCKET):
        return _result('create-environment', ENVIRONMENT_COMMAND, {}, False, 'Create the buckets first.', started)
    iam = _client('iam')
    try:
        iam.get_role(RoleName=ROLE)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=ROLE, AssumeRolePolicyDocument=json.dumps(TRUST), Description='MWAA execution role')
    iam.put_role_policy(RoleName=ROLE, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(EXECUTION_POLICY))
    mwaa = _slow_client('mwaa')
    if not _environment():
        mwaa.create_environment(
            Name=ENVIRONMENT, AirflowVersion=AIRFLOW_VERSION, ExecutionRoleArn=ROLE_ARN,
            SourceBucketArn=f'arn:aws:s3:::{DAG_BUCKET}', DagS3Path='dags', RequirementsS3Path=REQUIREMENTS_KEY,
            EnvironmentClass='mw1.small', MinWorkers=1, MaxWorkers=2, WebserverAccessMode='PRIVATE_ONLY',
            NetworkConfiguration={'SubnetIds': ['subnet-private-a', 'subnet-private-b'],
                                  'SecurityGroupIds': ['sg-airflow']},
            LoggingConfiguration=LOGGING, AirflowConfigurationOptions={'core.default_timezone': 'utc'},
            Tags={'Lab': LAB_KEY})
    _wait(lambda: (_environment() or {}).get('Status') in ('AVAILABLE', 'CREATE_FAILED'),
          ENVIRONMENT_WAIT_SECONDS, delay=4)
    environment = _environment() or {}
    verified, message = _check_environment()
    return _result('create-environment', ENVIRONMENT_COMMAND, {
        key: environment.get(key) for key in ('Name', 'Status', 'AirflowVersion', 'EnvironmentClass', 'MinWorkers',
                                              'MaxWorkers', 'WebserverUrl', 'ExecutionRoleArn', 'DagS3Path')
    }, verified, message, started)


def step_deploy_dag() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_environment('deploy-dag', DEPLOY_COMMAND, started)
    if blocked:
        return blocked
    _client('s3').put_object(Bucket=DAG_BUCKET, Key=DAG_KEY, Body=DAG_SOURCE.encode(), ContentType='text/x-python')
    try:
        found: list[str] = []

        def deployed() -> bool:
            cli('dags reserialize')
            found[:] = dag_ids()
            return DAG_ID in found
        _wait(deployed, 75, delay=5)
        errors = import_errors()
        cli(f'variables set {VARIABLE} 2')
        cli(f'dags unpause {DAG_ID}')
        variable = cli(f'variables get {VARIABLE}')['stdout'].strip().splitlines()[-1:] or ['']
    except CliUnavailable as exc:
        return _cli_failure('deploy-dag', DEPLOY_COMMAND, started, exc)
    _marker('deploy-dag', {'dags': found, 'importErrors': errors, 'variable': variable[0].strip()})
    verified, message = _check_deploy()
    return _result('deploy-dag', DEPLOY_COMMAND, {
        'Dags': found, 'ImportErrors': errors, 'Variable': {VARIABLE: variable[0].strip()},
    }, verified, message, started)


def _trigger(run_id: str, day: str) -> None:
    if run_id in runs():
        return
    out = cli(f'dags trigger {DAG_ID} -e {day}T00:00:00+00:00 -r {run_id}')
    if run_id not in out['stdout']:
        raise CliUnavailable(f'Airflow did not create {run_id}: {out["stderr"][-300:]}')


def step_trigger_run() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_environment('trigger-run', TRIGGER_COMMAND, started)
    if blocked:
        return blocked
    try:
        _trigger(GOOD_RUN, GOOD_DAY)
        state = _wait_run(GOOD_RUN)
        states = task_states(GOOD_RUN)
    except CliUnavailable as exc:
        return _cli_failure('trigger-run', TRIGGER_COMMAND, started, exc)
    _marker('trigger-run', {'state': state, 'tasks': states})
    verified, message = _check_trigger()
    return _result('trigger-run', TRIGGER_COMMAND, {
        'Run': GOOD_RUN, 'State': state, 'Tasks': states,
        'Curated': json.loads(_object(DATA_BUCKET, curated_key(GOOD_DAY)) or 'null'),
    }, verified, message, started)


def step_inspect_failure() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_environment('inspect-failure', FAILURE_COMMAND, started)
    if blocked:
        return blocked
    _client('s3').delete_object(Bucket=DATA_BUCKET, Key=raw_key(LATE_DAY))
    _drain_alerts()
    try:
        if LATE_RUN in runs():  # a repeat of this step: run the day again without its file
            cli(f'tasks clear {DAG_ID} -t extract --downstream -s {LATE_DAY} -e {LATE_DAY} --yes')
            _wait(lambda: runs().get(LATE_RUN) not in ('success', 'failed'), 30, delay=2)
        else:
            _trigger(LATE_RUN, LATE_DAY)
        state = _wait_run(LATE_RUN)
        states = task_states(LATE_RUN)
    except CliUnavailable as exc:
        return _cli_failure('inspect-failure', FAILURE_COMMAND, started, exc)
    alerts: list[dict[str, Any]] = []
    _wait(lambda: bool(alerts.extend(_drain_alerts()) or alerts), 15, delay=1)
    _marker('inspect-failure', {'state': state, 'tasks': states, 'alerts': alerts})
    verified, message = _check_failure()
    return _result('inspect-failure', FAILURE_COMMAND, {
        'Run': LATE_RUN, 'State': state, 'Tasks': states,
        'Alert': json.loads(_object(DATA_BUCKET, alert_key(LATE_DAY, 'extract')) or 'null'), 'QueueAlerts': alerts,
    }, verified, message, started)


def step_clear_and_rerun() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_environment('clear-and-rerun', RERUN_COMMAND, started)
    if blocked:
        return blocked
    _client('s3').put_object(Bucket=DATA_BUCKET, Key=raw_key(LATE_DAY), Body=orders_csv(LATE_DAY).encode(),
                             ContentType='text/csv')
    try:
        if LATE_RUN not in runs():
            _trigger(LATE_RUN, LATE_DAY)
        else:
            cli(f'tasks clear {DAG_ID} -t extract --downstream -s {LATE_DAY} -e {LATE_DAY} --yes')
        _wait(lambda: runs().get(LATE_RUN) not in ('failed',), 30, delay=2)
        state = _wait_run(LATE_RUN)
        states = task_states(LATE_RUN)
    except CliUnavailable as exc:
        return _cli_failure('clear-and-rerun', RERUN_COMMAND, started, exc)
    _drain_alerts()
    _marker('clear-and-rerun', {'state': state, 'tasks': states})
    verified, message = _check_rerun()
    return _result('clear-and-rerun', RERUN_COMMAND, {
        'Run': LATE_RUN, 'State': state, 'Tasks': states,
        'Curated': json.loads(_object(DATA_BUCKET, curated_key(LATE_DAY)) or 'null'),
    }, verified, message, started)


def step_tune_environment() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_environment('tune-environment', TUNE_COMMAND, started)
    if blocked:
        return blocked
    mwaa = _client('mwaa')
    mwaa.update_environment(Name=ENVIRONMENT, MinWorkers=1, MaxWorkers=5)
    _wait(_available, 60, delay=2)
    try:
        mwaa.update_environment(Name=ENVIRONMENT, AirflowConfigurationOptions={
            'core.default_timezone': 'utc', 'core.parallelism': '32'})
        options = {'Accepted': True}
    except ClientError as exc:
        options = {'Accepted': False, 'Error': _code(exc), 'Message': str(exc)[:240]}
    _wait(_available, 60, delay=2)
    environment = _environment() or {}
    verified, message = _check_tune()
    return _result('tune-environment', TUNE_COMMAND, {
        'MinWorkers': environment.get('MinWorkers'), 'MaxWorkers': environment.get('MaxWorkers'),
        'Status': environment.get('Status'), 'ConfigurationOptionsUpdate': options,
    }, verified, message, started)


def step_roll_back_dag() -> dict[str, Any]:
    started = time.perf_counter()
    blocked = _needs_environment('roll-back-dag', ROLLBACK_COMMAND, started)
    if blocked:
        return blocked
    s3 = _client('s3')
    if _object(DAG_BUCKET, DAG_KEY) != DAG_SOURCE:
        s3.put_object(Bucket=DAG_BUCKET, Key=DAG_KEY, Body=DAG_SOURCE.encode(), ContentType='text/x-python')
    good_version = next((item['VersionId'] for item in _versions() if item.get('IsLatest')), None)
    s3.put_object(Bucket=DAG_BUCKET, Key=DAG_KEY, Body=BROKEN_DAG_SOURCE.encode(), ContentType='text/x-python')
    try:
        broken: list[dict[str, Any]] = []

        def error_seen() -> bool:
            cli('dags reserialize')
            broken[:] = [item for item in import_errors() if DAG_ID in str(item.get('filepath', ''))]
            return bool(broken)
        _wait(error_seen, 75, delay=5)
        s3.copy_object(Bucket=DAG_BUCKET, Key=DAG_KEY, CopySource={'Bucket': DAG_BUCKET, 'Key': DAG_KEY,
                                                                   'VersionId': good_version})
        after: list[dict[str, Any]] = []

        def cleared() -> bool:
            cli('dags reserialize')
            after[:] = [item for item in import_errors() if DAG_ID in str(item.get('filepath', ''))]
            return not after
        _wait(cleared, 75, delay=5)
    except CliUnavailable as exc:
        return _cli_failure('roll-back-dag', ROLLBACK_COMMAND, started, exc)
    _marker('roll-back-dag', {'brokenSeen': bool(broken), 'errorsAfter': after, 'restored': good_version})
    verified, message = _check_rollback()
    return _result('roll-back-dag', ROLLBACK_COMMAND, {
        'ImportErrors': broken, 'RestoredVersion': good_version, 'ImportErrorsAfter': after,
        'Versions': [{'VersionId': item.get('VersionId'), 'IsLatest': item.get('IsLatest')} for item in _versions()],
    }, verified, message, started)


RUNNERS = {
    'dag-bucket': step_dag_bucket,
    'create-environment': step_create_environment,
    'deploy-dag': step_deploy_dag,
    'trigger-run': step_trigger_run,
    'inspect-failure': step_inspect_failure,
    'clear-and-rerun': step_clear_and_rerun,
    'tune-environment': step_tune_environment,
    'roll-back-dag': step_roll_back_dag,
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
    """Delete the environment, the role, the topic and queue, and both buckets with every object version."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'NoSuch' in code or code == 'ResourceNotFoundException':
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    if _environment():
        attempt(f'environment:{ENVIRONMENT}', lambda: _client('mwaa').delete_environment(Name=ENVIRONMENT))
        _wait(lambda: _environment() is None, 90, delay=3)
    iam = _client('iam')
    if _safe_call(lambda: iam.get_role(RoleName=ROLE)):
        attempt(f'policy:{ROLE}', lambda: iam.delete_role_policy(RoleName=ROLE, PolicyName=INLINE_POLICY))
        attempt(f'role:{ROLE}', lambda: iam.delete_role(RoleName=ROLE))
    sns = _client('sns')
    for item in _safe_call(lambda: sns.list_subscriptions_by_topic(TopicArn=TOPIC_ARN).get('Subscriptions', []),
                           []) or []:
        attempt('subscription', lambda item=item: sns.unsubscribe(SubscriptionArn=item['SubscriptionArn']))
    if _safe_call(lambda: sns.get_topic_attributes(TopicArn=TOPIC_ARN)):
        attempt(f'topic:{TOPIC}', lambda: sns.delete_topic(TopicArn=TOPIC_ARN))
    url = _queue_url()
    if url:
        attempt(f'queue:{ALERT_QUEUE}', lambda: _client('sqs').delete_queue(QueueUrl=url))
    s3 = _client('s3')
    for bucket in (DAG_BUCKET, DATA_BUCKET):
        if not _bucket_exists(bucket):
            continue
        listing = _safe_call(lambda bucket=bucket: s3.list_object_versions(Bucket=bucket), {}) or {}
        objects = [{'Key': item['Key'], 'VersionId': item['VersionId']}
                   for item in listing.get('Versions', []) + listing.get('DeleteMarkers', [])]
        if not objects:
            page = _safe_call(lambda bucket=bucket: s3.list_objects_v2(Bucket=bucket), {}) or {}
            objects = [{'Key': item['Key']} for item in page.get('Contents', [])]
        for start in range(0, len(objects), 1000):
            chunk = objects[start:start + 1000]
            attempt(f'objects:{bucket}', lambda bucket=bucket, chunk=chunk: s3.delete_objects(
                Bucket=bucket, Delete={'Objects': chunk, 'Quiet': True}))
        attempt(f'bucket:{bucket}', lambda bucket=bucket: s3.delete_bucket(Bucket=bucket))
    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': f'aws mwaa delete-environment --name {ENVIRONMENT}  # and the buckets, role, topic, queue',
        'exit_code': 0,
        'stdout': json.dumps(payload, indent=2),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verified': not errors,
        'verification': {
            'status': 'passed' if not errors else 'failed',
            'message': 'Airflow lab resources removed.' if not errors else '; '.join(errors)[:500],
        },
    }
