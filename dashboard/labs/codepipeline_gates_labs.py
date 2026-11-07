"""Pipeline quality gates lab: give builds their settings from Parameter Store and Secrets Manager, gate releases on
unit tests and a coverage minimum, read the JUnit report, run an integration test that calls DynamoDB from the build,
chain the gates in a CodePipeline, and route failed stages to an SQS queue through EventBridge.

Floci 2.4.0 runs every build for real in a Docker container: it resolves the buildspec's Parameter Store and Secrets
Manager references into environment variables, fails the phase on a non-zero exit with the output's tail as the
phase context, uploads the artifact files to S3 even when the build fails, and points the AWS SDKs in the build at
Floci. Pipelines run their S3 source and CodeBuild actions and publish real stage state-change events to EventBridge.
What Floci does not do: build logs stay empty, report groups are stored but no reports are created from the
buildspec's reports section (so the lab reads the JUnit file itself), and a pipeline's CodeBuild action reads the
project's own S3 source rather than the source stage's artifact.
"""

from __future__ import annotations

import io
import json
import time
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'codepipeline'
LAB_KEY = 'pipeline-quality-gates'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

SOURCE_BUCKET = 'lab-gates-source'
SOURCE_KEY = 'source/orders-service.zip'
ARTIFACT_BUCKET = 'lab-gates-artifacts'
UNIT_PREFIX = 'unit'
INTEGRATION_PREFIX = 'integration'

TABLE = 'lab-gates-orders'
TABLE_PARAMETER = '/lab-gates/orders-table'
COVERAGE_PARAMETER = '/lab-gates/coverage-minimum'
COVERAGE_MINIMUM = 80
SECRET = 'lab-gates/payments-api-key'
SECRET_VALUE = 'pk_lab_4f9c2e7a1b'

BUILD_ROLE = 'lab-gates-codebuild-role'
PIPELINE_ROLE = 'lab-gates-pipeline-role'
BUILD_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{BUILD_ROLE}'
PIPELINE_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{PIPELINE_ROLE}'
INLINE_POLICY = 'lab-gates'

UNIT_PROJECT = 'lab-gates-unit'
INTEGRATION_PROJECT = 'lab-gates-integration'
# Floci runs builds in this image from Docker Hub; on AWS the curated aws/codebuild/standard:7.0 image is usual.
BUILD_IMAGE = 'python:3.12-slim'
REPORT_GROUP = 'lab-gates-unit-tests'
BUILD_WAIT_SECONDS = 240

PIPELINE = 'lab-gates-pipeline'
SOURCE_STAGE, UNIT_STAGE, INTEGRATION_STAGE = 'Source', 'UnitTests', 'IntegrationTest'
SOURCE_ACTION, UNIT_ACTION, INTEGRATION_ACTION = 'S3Source', 'UnitTestsAndCoverage', 'OrdersTableTest'
PIPELINE_WAIT_SECONDS = 420

RULE = 'lab-gates-failed-stages'
QUEUE = 'lab-gates-pipeline-failures'

FLOCI = 'How Floci emulates this: '

# ── The orders service, release by release ───────────────────────────────────

RELEASE_1 = 'release-1'          # pricing, fully tested
RELEASE_2 = 'release-2'          # adds refunds with no tests: coverage drops below the minimum
RELEASE_2_TESTED = 'release-2.1'  # the same code with refund tests
RELEASE_3 = 'release-3'          # a storage change keyed on order_id: unit tests pass, the table rejects it
RELEASE_3_FIXED = 'release-3.1'  # storage keyed on id again


def app_source(release: str, refunds: bool) -> str:
    source = (
        '"""Orders service: prices orders and works out refunds."""\n'
        f'RELEASE = {release!r}\n'
        'TAX_RATE = 0.2\n\n\n'
        'def validate(order):\n'
        "    if not order.get('items'):\n"
        "        raise ValueError('an order needs at least one item')\n"
        "    for item in order['items']:\n"
        "        if item['quantity'] < 1:\n"
        "            raise ValueError('quantities start at 1')\n\n\n"
        'def total(order):\n'
        '    validate(order)\n'
        "    subtotal = sum(item['price'] * item['quantity'] for item in order['items'])\n"
        '    return round(subtotal * (1 + TAX_RATE), 2)\n'
    )
    if refunds:
        source += (
            '\n\n'
            'def refund(order, days_since_delivery):\n'
            '    if days_since_delivery > 30:\n'
            '        return 0.0\n'
            "    if order.get('status') == 'damaged':\n"
            '        return total(order)\n'
            '    restocking_fee = 0.1\n'
            '    return round(total(order) * (1 - restocking_fee), 2)\n'
        )
    return source


def test_source(refund_tests: bool) -> str:
    source = (
        'import unittest\n\n'
        'import app\n\n'
        "ORDER = {'items': [{'price': 10.0, 'quantity': 2}, {'price': 5.0, 'quantity': 1}]}\n\n\n"
        'class TotalTests(unittest.TestCase):\n'
        '    def test_total_adds_tax(self):\n'
        '        self.assertEqual(app.total(ORDER), 30.0)\n\n'
        '    def test_empty_order_is_rejected(self):\n'
        '        with self.assertRaises(ValueError):\n'
        "            app.total({'items': []})\n\n"
        '    def test_zero_quantity_is_rejected(self):\n'
        '        with self.assertRaises(ValueError):\n'
        "            app.total({'items': [{'price': 1.0, 'quantity': 0}]})\n"
    )
    if refund_tests:
        source += (
            '\n\n'
            'class RefundTests(unittest.TestCase):\n'
            '    def test_late_refund_is_zero(self):\n'
            '        self.assertEqual(app.refund(ORDER, 31), 0.0)\n\n'
            '    def test_damaged_order_is_refunded_in_full(self):\n'
            "        self.assertEqual(app.refund({**ORDER, 'status': 'damaged'}, 3), 30.0)\n\n"
            '    def test_refund_keeps_a_restocking_fee(self):\n'
            '        self.assertEqual(app.refund(ORDER, 3), 27.0)\n'
        )
    return source


def store_source(key_attribute: str) -> str:
    """DynamoDB access with only the standard library: the slim build image has no boto3 and no internet."""
    return (
        '"""Orders table access over the DynamoDB API, signed with SigV4 by hand (no boto3 in the image)."""\n'
        'import datetime\nimport hashlib\nimport hmac\nimport json\nimport os\nimport urllib.error\n'
        'import urllib.request\n\n'
        f'KEY_ATTRIBUTE = {key_attribute!r}\n\n\n'
        'def call(action, body):\n'
        "    endpoint = os.environ.get('AWS_ENDPOINT_URL', 'https://dynamodb.us-east-1.amazonaws.com').rstrip('/')\n"
        "    region = os.environ.get('AWS_REGION', 'us-east-1')\n"
        "    key, secret = os.environ['AWS_ACCESS_KEY_ID'], os.environ['AWS_SECRET_ACCESS_KEY']\n"
        '    payload = json.dumps(body).encode()\n'
        '    now = datetime.datetime.now(datetime.timezone.utc)\n'
        "    amz_date, day = now.strftime('%Y%m%dT%H%M%SZ'), now.strftime('%Y%m%d')\n"
        "    headers = {'content-type': 'application/x-amz-json-1.0', 'host': endpoint.split('://', 1)[1],\n"
        "               'x-amz-date': amz_date, 'x-amz-target': 'DynamoDB_20120810.' + action}\n"
        "    if os.environ.get('AWS_SESSION_TOKEN'):\n"
        "        headers['x-amz-security-token'] = os.environ['AWS_SESSION_TOKEN']\n"
        "    signed = ';'.join(sorted(headers))\n"
        "    canonical = '\\n'.join(['POST', '/', '', ''.join(f'{k}:{headers[k]}\\n' for k in sorted(headers)),\n"
        '                           signed, hashlib.sha256(payload).hexdigest()])\n'
        "    scope = f'{day}/{region}/dynamodb/aws4_request'\n"
        "    to_sign = '\\n'.join(['AWS4-HMAC-SHA256', amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])\n"
        "    signing_key = ('AWS4' + secret).encode()\n"
        "    for part in (day, region, 'dynamodb', 'aws4_request'):\n"
        '        signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()\n'
        '    signature = hmac.new(signing_key, to_sign.encode(), hashlib.sha256).hexdigest()\n'
        "    headers['authorization'] = (f'AWS4-HMAC-SHA256 Credential={key}/{scope}, SignedHeaders={signed}, '\n"
        "                                f'Signature={signature}')\n"
        "    request = urllib.request.Request(endpoint + '/', data=payload, headers=headers, method='POST')\n"
        '    try:\n'
        '        with urllib.request.urlopen(request, timeout=10) as response:\n'
        "            return json.loads(response.read() or b'{}')\n"
        '    except urllib.error.HTTPError as exc:\n'
        "        raise RuntimeError(f'{action} failed: {exc.read().decode(errors=\"replace\")}') from None\n\n\n"
        'def save(table, order_id, total, release):\n'
        "    call('PutItem', {'TableName': table, 'Item': {KEY_ATTRIBUTE: {'S': order_id},\n"
        "                                               'total': {'N': str(total)}, 'release': {'S': release}}})\n\n\n"
        'def load(table, order_id):\n'
        "    return call('GetItem', {'TableName': table, 'Key': {KEY_ATTRIBUTE: {'S': order_id}},\n"
        "                            'ConsistentRead': True}).get('Item')\n"
    )


RUN_TESTS = '''"""Unit tests with line coverage of app.py, a JUnit report, and the coverage gate (standard library only)."""
import json
import os
import sys
import time
import unittest
import xml.etree.ElementTree as ET

SOURCE = os.path.abspath('app.py')
MINIMUM = int(os.environ.get('COVERAGE_MINIMUM', '80'))


def executable_lines(path):
    lines, pending = set(), [compile(open(path).read(), path, 'exec')]
    while pending:
        code = pending.pop()
        lines.update(line for _start, _end, line in code.co_lines() if line)
        pending.extend(const for const in code.co_consts if hasattr(const, 'co_lines'))
    return lines


hits = set()


def tracer(frame, event, arg):
    if frame.f_code.co_filename != SOURCE:
        return None

    def local(frame, event, arg):
        if event == 'line':
            hits.add(frame.f_lineno)
        return local
    hits.add(frame.f_lineno)
    return local


class Recorder(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []

    def startTest(self, test):
        self._started = time.perf_counter()
        super().startTest(test)

    def _record(self, test, outcome, detail=''):
        self.cases.append({'class': type(test).__name__, 'name': test._testMethodName, 'outcome': outcome,
                           'detail': detail, 'time': round(time.perf_counter() - self._started, 4)})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._record(test, 'passed')

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._record(test, 'failed', str(err[1]))

    def addError(self, test, err):
        super().addError(test, err)
        self._record(test, 'error', str(err[1]))


sys.settrace(tracer)
import app  # noqa: E402  (imported under the tracer so module-level lines count)
suite = unittest.defaultTestLoader.loadTestsFromName('test_app')
result = unittest.TextTestRunner(resultclass=Recorder, verbosity=2).run(suite)
sys.settrace(None)

executable = executable_lines(SOURCE)
covered = sorted(executable & hits)
missed = sorted(executable - hits)
percent = round(100 * len(covered) / len(executable)) if executable else 100

os.makedirs('reports', exist_ok=True)
failures = sum(1 for case in result.cases if case['outcome'] == 'failed')
errors = sum(1 for case in result.cases if case['outcome'] == 'error')
suite_node = ET.Element('testsuite', name='orders-service-unit', tests=str(len(result.cases)),
                        failures=str(failures), errors=str(errors),
                        time=str(round(sum(case['time'] for case in result.cases), 4)))
for case in result.cases:
    node = ET.SubElement(suite_node, 'testcase', classname=case['class'], name=case['name'], time=str(case['time']))
    if case['outcome'] != 'passed':
        ET.SubElement(node, 'failure' if case['outcome'] == 'failed' else 'error', message=case['detail'][:200])
ET.ElementTree(suite_node).write('reports/junit.xml', encoding='utf-8', xml_declaration=True)
json.dump({'release': app.RELEASE, 'percent': percent, 'minimum': MINIMUM, 'covered': len(covered),
           'executable': len(executable), 'missedLines': missed}, open('reports/coverage.json', 'w'))
key = os.environ.get('PAYMENTS_API_KEY', '')
json.dump({'ordersTable': os.environ.get('ORDERS_TABLE'), 'coverageMinimum': os.environ.get('COVERAGE_MINIMUM'),
           'paymentsKeyPresent': bool(key), 'paymentsKeyLength': len(key)}, open('reports/settings.json', 'w'))

if not result.wasSuccessful():
    print(f'unit tests failed: {failures} failures, {errors} errors')
    sys.exit(1)
if percent < MINIMUM:
    print(f'coverage {percent}% is below the {MINIMUM}% minimum (missed lines {missed})')
    sys.exit(2)
print(f'coverage {percent}% meets the {MINIMUM}% minimum')
'''

INTEGRATION_TEST = '''"""Integration test: save an order to the real orders table and read it back."""
import json
import os
import sys

import app
import store

table = os.environ['ORDERS_TABLE']
order_id = 'it-' + os.environ.get('CODEBUILD_BUILD_NUMBER', '0')
order = {'items': [{'price': 12.5, 'quantity': 2}]}
result = {'release': app.RELEASE, 'table': table, 'orderId': order_id, 'keyAttribute': store.KEY_ATTRIBUTE}
try:
    expected = app.total(order)
    store.save(table, order_id, expected, app.RELEASE)
    item = store.load(table, order_id)
    if not item or float(item['total']['N']) != expected:
        raise AssertionError(f'read back {item!r}, expected total {expected}')
    result.update(passed=True, item=item)
except Exception as exc:  # report every failure the same way
    result.update(passed=False, error=str(exc)[:300])
os.makedirs('reports', exist_ok=True)
json.dump(result, open('reports/integration.json', 'w'))
if not result['passed']:
    print('integration test failed: ' + result['error'])
    sys.exit(1)
print(f'integration test passed: {order_id} round-tripped through {table}')
'''

UNIT_BUILDSPEC = f"""version: 0.2
env:
  parameter-store:
    ORDERS_TABLE: {TABLE_PARAMETER}
    COVERAGE_MINIMUM: {COVERAGE_PARAMETER}
  secrets-manager:
    PAYMENTS_API_KEY: {SECRET}
phases:
  build:
    commands:
      - python run_tests.py
reports:
  {REPORT_GROUP}:
    base-directory: reports
    files:
      - junit.xml
    file-format: JUNITXML
artifacts:
  base-directory: reports
  files:
    - '**/*'
"""

INTEGRATION_BUILDSPEC = f"""version: 0.2
env:
  parameter-store:
    ORDERS_TABLE: {TABLE_PARAMETER}
phases:
  build:
    commands:
      - python integration_test.py
artifacts:
  base-directory: reports
  files:
    - '**/*'
"""

RELEASES = {
    # release -> (refunds in app.py, refund tests, DynamoDB key attribute in store.py)
    RELEASE_1: (False, False, 'id'),
    RELEASE_2: (True, False, 'id'),
    RELEASE_2_TESTED: (True, True, 'id'),
    RELEASE_3: (True, True, 'order_id'),
    RELEASE_3_FIXED: (True, True, 'id'),
}


def source_files(release: str) -> dict[str, str]:
    refunds, refund_tests, key_attribute = RELEASES[release]
    return {
        'app.py': app_source(release, refunds),
        'test_app.py': test_source(refund_tests),
        'store.py': store_source(key_attribute),
        'run_tests.py': RUN_TESTS,
        'integration_test.py': INTEGRATION_TEST,
    }


def source_zip(release: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, body in source_files(release).items():
            archive.writestr(name, body)
    return buffer.getvalue()


def junit_summary(xml_text: str) -> dict[str, Any]:
    """What a CodeBuild test report shows: totals by status and each case."""
    root = ET.fromstring(xml_text)
    suites = [root] if root.tag == 'testsuite' else root.findall('testsuite')
    cases = []
    for suite in suites:
        for case in suite.findall('testcase'):
            status = 'SUCCEEDED'
            if case.find('failure') is not None:
                status = 'FAILED'
            elif case.find('error') is not None:
                status = 'ERROR'
            elif case.find('skipped') is not None:
                status = 'SKIPPED'
            cases.append({'name': f"{case.get('classname')}.{case.get('name')}", 'status': status,
                          'durationMs': round(float(case.get('time') or 0) * 1000, 1)})
    counts: dict[str, int] = {}
    for case in cases:
        counts[case['status']] = counts.get(case['status'], 0) + 1
    passed = counts.get('SUCCEEDED', 0)
    return {'total': len(cases), 'statusCounts': counts,
            'passRate': round(100 * passed / len(cases), 1) if cases else 0.0, 'cases': cases}


def event_pattern() -> dict[str, Any]:
    return {
        'source': ['aws.codepipeline'],
        'detail-type': ['CodePipeline Stage Execution State Change'],
        'detail': {'pipeline': [PIPELINE], 'state': ['FAILED']},
    }


def pipeline_document() -> dict[str, Any]:
    def build_action(name: str, project: str) -> dict[str, Any]:
        return {'name': name, 'runOrder': 1,
                'actionTypeId': {'category': 'Test', 'owner': 'AWS', 'provider': 'CodeBuild', 'version': '1'},
                'configuration': {'ProjectName': project}, 'inputArtifacts': [{'name': 'SourceZip'}]}
    return {
        'name': PIPELINE,
        'roleArn': PIPELINE_ROLE_ARN,
        'artifactStore': {'type': 'S3', 'location': ARTIFACT_BUCKET},
        'stages': [
            {'name': SOURCE_STAGE, 'actions': [{
                'name': SOURCE_ACTION, 'runOrder': 1,
                'actionTypeId': {'category': 'Source', 'owner': 'AWS', 'provider': 'S3', 'version': '1'},
                'configuration': {'S3Bucket': SOURCE_BUCKET, 'S3ObjectKey': SOURCE_KEY, 'PollForSourceChanges': 'false'},
                'outputArtifacts': [{'name': 'SourceZip'}]}]},
            {'name': UNIT_STAGE, 'actions': [build_action(UNIT_ACTION, UNIT_PROJECT)]},
            {'name': INTEGRATION_STAGE, 'actions': [build_action(INTEGRATION_ACTION, INTEGRATION_PROJECT)]},
        ],
    }


def _trust(service: str) -> dict[str, Any]:
    return {'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole'}]}


BUILD_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'Source', 'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:GetObjectVersion'],
         'Resource': f'arn:aws:s3:::{SOURCE_BUCKET}/*'},
        {'Sid': 'Artifacts', 'Effect': 'Allow', 'Action': ['s3:PutObject'],
         'Resource': f'arn:aws:s3:::{ARTIFACT_BUCKET}/*'},
        {'Sid': 'Settings', 'Effect': 'Allow', 'Action': ['ssm:GetParameters'],
         'Resource': f'arn:aws:ssm:{REGION}:{ACCOUNT}:parameter/lab-gates/*'},
        {'Sid': 'Secret', 'Effect': 'Allow', 'Action': ['secretsmanager:GetSecretValue'],
         'Resource': f'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:{SECRET}-*'},
        {'Sid': 'OrdersTable', 'Effect': 'Allow', 'Action': ['dynamodb:PutItem', 'dynamodb:GetItem'],
         'Resource': f'arn:aws:dynamodb:{REGION}:{ACCOUNT}:table/{TABLE}'},
        {'Sid': 'Reports', 'Effect': 'Allow',
         'Action': ['codebuild:CreateReport', 'codebuild:UpdateReport', 'codebuild:BatchPutTestCases'],
         'Resource': f'arn:aws:codebuild:{REGION}:{ACCOUNT}:report-group/{REPORT_GROUP}'},
        {'Sid': 'Logs', 'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
         'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/codebuild/lab-gates-*'},
    ],
}
PIPELINE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'Source', 'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:GetObjectVersion', 's3:GetBucketVersioning'],
         'Resource': [f'arn:aws:s3:::{SOURCE_BUCKET}', f'arn:aws:s3:::{SOURCE_BUCKET}/*']},
        {'Sid': 'ArtifactStore', 'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject'],
         'Resource': f'arn:aws:s3:::{ARTIFACT_BUCKET}/*'},
        {'Sid': 'RunBuilds', 'Effect': 'Allow', 'Action': ['codebuild:StartBuild', 'codebuild:BatchGetBuilds'],
         'Resource': [f'arn:aws:codebuild:{REGION}:{ACCOUNT}:project/{UNIT_PROJECT}',
                      f'arn:aws:codebuild:{REGION}:{ACCOUNT}:project/{INTEGRATION_PROJECT}']},
    ],
}


def queue_policy(queue_arn: str, rule_arn: str) -> dict[str, Any]:
    return {'Version': '2012-10-17', 'Statement': [{
        'Sid': 'EventBridgeFailedStages', 'Effect': 'Allow', 'Principal': {'Service': 'events.amazonaws.com'},
        'Action': 'sqs:SendMessage', 'Resource': queue_arn, 'Condition': {'ArnEquals': {'aws:SourceArn': rule_arn}}}]}


# ── Commands ─────────────────────────────────────────────────────────────────

SETTINGS_COMMAND = (
    f'aws ssm put-parameter --name {TABLE_PARAMETER} --type String --value {TABLE}\n'
    f'aws ssm put-parameter --name {COVERAGE_PARAMETER} --type String --value {COVERAGE_MINIMUM}\n'
    f'aws secretsmanager create-secret --name {SECRET} --secret-string <payments API key>\n'
    f'aws s3api create-bucket --bucket {SOURCE_BUCKET} && aws s3api put-bucket-versioning --bucket {SOURCE_BUCKET} '
    '--versioning-configuration Status=Enabled\n'
    f'aws s3 cp orders-service.zip s3://{SOURCE_BUCKET}/{SOURCE_KEY}   # {RELEASE_1}\n'
    f'aws iam create-role --role-name {BUILD_ROLE} --assume-role-policy-document file://codebuild-trust.json\n'
    f'aws codebuild create-project --name {UNIT_PROJECT} --source type=S3,location={SOURCE_BUCKET}/{SOURCE_KEY},'
    'buildspec=file://buildspec-unit.yml '
    f'--artifacts type=S3,location={ARTIFACT_BUCKET},path={UNIT_PREFIX},packaging=NONE '
    f'--environment type=LINUX_CONTAINER,computeType=BUILD_GENERAL1_SMALL,image={BUILD_IMAGE} '
    f'--service-role {BUILD_ROLE_ARN}'
)
UNIT_COMMAND = (
    f'aws codebuild start-build --project-name {UNIT_PROJECT}\n'
    'aws codebuild batch-get-builds --ids <build-id> --query "builds[0].[buildStatus,phases[].[phaseType,phaseStatus]]"\n'
    f'aws s3 cp s3://{ARTIFACT_BUCKET}/{UNIT_PREFIX}/coverage.json -\n'
    f'aws s3 cp s3://{ARTIFACT_BUCKET}/{UNIT_PREFIX}/settings.json -'
)
COVERAGE_COMMAND = (
    f'# {RELEASE_2}: adds refund() with no tests\n'
    f'aws s3 cp orders-service.zip s3://{SOURCE_BUCKET}/{SOURCE_KEY}\n'
    f'aws codebuild start-build --project-name {UNIT_PROJECT}\n'
    'aws codebuild batch-get-builds --ids <build-id> --query "builds[0].phases[?phaseStatus==\'FAILED\'].contexts"\n'
    f'# {RELEASE_2_TESTED}: the same code with refund tests\n'
    f'aws s3 cp orders-service.zip s3://{SOURCE_BUCKET}/{SOURCE_KEY} && '
    f'aws codebuild start-build --project-name {UNIT_PROJECT}'
)
REPORT_COMMAND = (
    f'aws codebuild create-report-group --name {REPORT_GROUP} --type TEST '
    f'--export-config "exportConfigType=S3,s3Destination={{bucket={ARTIFACT_BUCKET},path=reports,packaging=NONE}}"\n'
    f'aws codebuild list-reports-for-report-group --report-group-arn <arn>\n'
    'aws codebuild describe-test-cases --report-arn <report-arn>\n'
    f'aws s3 cp s3://{ARTIFACT_BUCKET}/{UNIT_PREFIX}/junit.xml -'
)
INTEGRATION_COMMAND = (
    f'aws dynamodb create-table --table-name {TABLE} --attribute-definitions AttributeName=id,AttributeType=S '
    '--key-schema AttributeName=id,KeyType=HASH --billing-mode PAY_PER_REQUEST\n'
    f'aws codebuild create-project --name {INTEGRATION_PROJECT} --source type=S3,location={SOURCE_BUCKET}/{SOURCE_KEY},'
    'buildspec=file://buildspec-integration.yml '
    f'--artifacts type=S3,location={ARTIFACT_BUCKET},path={INTEGRATION_PREFIX},packaging=NONE '
    f'--environment type=LINUX_CONTAINER,computeType=BUILD_GENERAL1_SMALL,image={BUILD_IMAGE} '
    f'--service-role {BUILD_ROLE_ARN}\n'
    f'aws codebuild start-build --project-name {INTEGRATION_PROJECT}\n'
    f'aws dynamodb get-item --table-name {TABLE} --key \'{{"id":{{"S":"it-<build number>"}}}}\''
)
PIPELINE_COMMAND = (
    f'aws iam create-role --role-name {PIPELINE_ROLE} --assume-role-policy-document file://codepipeline-trust.json\n'
    'aws codepipeline create-pipeline --pipeline file://pipeline.json\n'
    f'# {SOURCE_STAGE}: S3 -> {UNIT_STAGE}: CodeBuild {UNIT_PROJECT} -> {INTEGRATION_STAGE}: '
    f'CodeBuild {INTEGRATION_PROJECT}\n'
    f'aws codepipeline start-pipeline-execution --name {PIPELINE}\n'
    f'aws codepipeline get-pipeline-execution --pipeline-name {PIPELINE} --pipeline-execution-id <id>'
)
EVENTS_COMMAND = (
    f'aws sqs create-queue --queue-name {QUEUE}\n'
    f'aws events put-rule --name {RULE} --event-pattern file://failed-stages.json\n'
    f'aws events put-targets --rule {RULE} --targets Id=queue,Arn=<queue-arn>\n'
    f'aws sqs set-queue-attributes --queue-url <queue-url> --attributes Policy=<allow events.amazonaws.com>\n'
    f'# {RELEASE_3}: the store keys orders on order_id\n'
    f'aws s3 cp orders-service.zip s3://{SOURCE_BUCKET}/{SOURCE_KEY}\n'
    f'aws codepipeline start-pipeline-execution --name {PIPELINE}\n'
    f'aws sqs receive-message --queue-url <queue-url> --wait-time-seconds 10'
)
RELEASE_COMMAND = (
    f'# {RELEASE_3_FIXED}: the store keys orders on id again\n'
    f'aws s3 cp orders-service.zip s3://{SOURCE_BUCKET}/{SOURCE_KEY}\n'
    f'aws codepipeline start-pipeline-execution --name {PIPELINE}\n'
    f'aws codepipeline list-pipeline-executions --pipeline-name {PIPELINE}\n'
    'aws sqs receive-message --queue-url <queue-url>   # nothing new'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Gate releases with tests, coverage, and an integration stage, and alert on failed stages',
    'description': (
        'Give CodeBuild its settings from Parameter Store and Secrets Manager, run unit tests with a coverage '
        'minimum that blocks an untested release, read the test report, run an integration test against a real '
        'DynamoDB table from the build, chain the gates in a CodePipeline, and route failed stages to an SQS queue '
        'with EventBridge.'
    ),
    'steps': [
        {
            'key': 'build-settings',
            'title': 'Give the build its settings from Parameter Store and Secrets Manager',
            'command': SETTINGS_COMMAND,
            'explanation': (
                f'The build needs the orders table name, the coverage minimum, and a payments API key. The buildspec\'s '
                'env section names them: parameter-store maps a variable to a parameter, and secrets-manager maps one '
                'to a secret (optionally a JSON key, stage, or version in it). CodeBuild reads them with its service '
                'role when the build starts, so nothing secret sits in the project or the source, and CodeBuild masks '
                f'secret values in its logs. The source is {RELEASE_1} of an orders service in a versioned S3 bucket. '
                + FLOCI + 'the parameters, secret, bucket, and project are real; Floci does not check the service role, '
                'so the lab writes the least-privilege policy you would need on AWS.'
            ),
        },
        {
            'key': 'unit-tests',
            'title': 'Run the unit tests and measure coverage',
            'command': UNIT_COMMAND,
            'explanation': (
                'The build runs the unit tests and measures which lines of app.py they execute. It writes a JUnit '
                'report, coverage.json, and settings.json (which records only the secret\'s length, never its value), '
                f'and exits non-zero if a test fails or coverage is below the {COVERAGE_MINIMUM}% minimum from '
                'Parameter Store. A non-zero exit fails the phase and the build: that exit code is the quality gate. '
                + FLOCI + f'the build really runs in a {BUILD_IMAGE} container with the parameter and secret values '
                'in its environment, and Floci uploads the reports folder to the artifact bucket. The image has no '
                'internet, so coverage is measured with the standard library rather than coverage.py.'
            ),
        },
        {
            'key': 'coverage-gate',
            'title': 'Block an untested release with the coverage gate',
            'command': COVERAGE_COMMAND,
            'explanation': (
                f'{RELEASE_2} adds refunds but no tests for them. Every existing test still passes, yet coverage falls '
                f'below {COVERAGE_MINIMUM}%, so the build fails in the BUILD phase and the failed phase names the '
                f'missed lines. {RELEASE_2_TESTED} adds the refund tests and passes. A coverage gate stops code from '
                'growing faster than its tests. '
                + FLOCI + 'the gate fails the build for real. Build logs stay empty in Floci, so the lab reads the '
                'failure from the failed phase\'s context, which holds the tail of the output, and Floci still uploads '
                'the reports of a failed build.'
            ),
        },
        {
            'key': 'test-report',
            'title': 'Create a report group and read the test report',
            'command': REPORT_COMMAND,
            'explanation': (
                'The buildspec\'s reports section sends junit.xml to a report group. On AWS each build then creates a '
                'report with pass rates, durations, and each failed case, and the report group can export raw files '
                'to S3 and keep trends across builds. '
                + FLOCI + 'the report group is stored, but Floci creates no reports from the reports section and does '
                'not support ListReports, so the lab parses the JUnit file from the artifact bucket and shows what the '
                'report would contain.'
            ),
        },
        {
            'key': 'integration-test',
            'title': 'Run an integration test against the orders table',
            'command': INTEGRATION_COMMAND,
            'explanation': (
                'Unit tests replace the database; an integration test uses a real one. A second project runs '
                'integration_test.py, which saves an order to the DynamoDB table named in Parameter Store and reads '
                'it back. The build calls DynamoDB with the credentials CodeBuild gives it, which come from the '
                'service role. '
                + FLOCI + 'the call really reaches Floci\'s DynamoDB: Floci sets AWS_ENDPOINT_URL and test '
                'credentials in the build so SDKs talk to Floci, and the item the build wrote is in the table '
                'afterwards. The image has no boto3, so the service signs its requests with SigV4 itself.'
            ),
        },
        {
            'key': 'pipeline',
            'title': 'Chain the gates in a pipeline',
            'command': PIPELINE_COMMAND,
            'explanation': (
                f'The pipeline runs {SOURCE_STAGE}, then {UNIT_STAGE}, then {INTEGRATION_STAGE}. A stage starts only '
                'when the one before it succeeds, so code that fails the unit or coverage gate never reaches the '
                'integration test, and nothing after a failed gate deploys. '
                + FLOCI + 'the pipeline really runs both builds. On AWS the projects would use source type '
                'CODEPIPELINE to read the source stage\'s artifact; Floci\'s CodeBuild action ignores the input '
                'artifact, so each project reads the same S3 object as the source stage.'
            ),
        },
        {
            'key': 'failure-events',
            'title': 'Send failed stages to a queue and catch a broken release',
            'command': EVENTS_COMMAND,
            'explanation': (
                'CodePipeline publishes a state-change event for each pipeline, stage, and action to EventBridge. A '
                'rule that matches failed stages of this pipeline sends them to an SQS queue, where an on-call tool or '
                f'a Lambda function can pick them up. {RELEASE_3} keys orders on order_id, which the unit tests cannot '
                f'catch; the table\'s key is id, so DynamoDB rejects the write and {INTEGRATION_STAGE} fails. '
                + FLOCI + 'the stage fails for real, and Floci publishes the real aws.codepipeline events to the '
                'default bus, where the rule delivers them to the queue.'
            ),
        },
        {
            'key': 'fix-release',
            'title': 'Fix the release and pass every gate',
            'command': RELEASE_COMMAND,
            'explanation': (
                f'{RELEASE_3_FIXED} keys orders on id again. A new execution passes the unit tests, the coverage gate, '
                'and the integration test, and the execution history shows the failed run followed by the good one. '
                'No new failure reaches the queue. '
                + FLOCI + 'the execution and both builds are real.'
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

def _ensure_bucket(name: str, versioned: bool = False) -> None:
    s3 = _client('s3')
    try:
        s3.head_bucket(Bucket=name)
    except ClientError:
        s3.create_bucket(Bucket=name)
    if versioned:
        s3.put_bucket_versioning(Bucket=name, VersioningConfiguration={'Status': 'Enabled'})


def _ensure_role(name: str, trust: dict[str, Any], policy: dict[str, Any], description: str) -> None:
    iam = _client('iam')
    try:
        iam.get_role(RoleName=name)
    except ClientError as exc:
        if _code(exc) != 'NoSuchEntity':
            raise
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description)
    iam.put_role_policy(RoleName=name, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(policy))


def _upload(release: str) -> None:
    _client('s3').put_object(Bucket=SOURCE_BUCKET, Key=SOURCE_KEY, Body=source_zip(release),
                             ContentType='application/zip', Metadata={'release': release})


def _source_release() -> str | None:
    try:
        head = _client('s3').head_object(Bucket=SOURCE_BUCKET, Key=SOURCE_KEY)
    except ClientError:
        return None
    return (head.get('Metadata') or {}).get('release')


def _parameter(name: str) -> str | None:
    return _safe_call(lambda: _client('ssm').get_parameter(Name=name)['Parameter']['Value'])


def _project(name: str) -> dict[str, Any] | None:
    projects = _safe_call(lambda: _client('codebuild').batch_get_projects(names=[name]).get('projects', []), [])
    return projects[0] if projects else None


def _ensure_project(name: str, buildspec: str, prefix: str) -> None:
    codebuild = _client('codebuild')
    settings = {
        'source': {'type': 'S3', 'location': f'{SOURCE_BUCKET}/{SOURCE_KEY}', 'buildspec': buildspec},
        'artifacts': {'type': 'S3', 'location': ARTIFACT_BUCKET, 'path': prefix, 'packaging': 'NONE',
                      'name': prefix},
        'environment': {'type': 'LINUX_CONTAINER', 'computeType': 'BUILD_GENERAL1_SMALL', 'image': BUILD_IMAGE},
        'serviceRole': BUILD_ROLE_ARN,
        'timeoutInMinutes': 10,
    }
    if _project(name):
        codebuild.update_project(name=name, **settings)
    else:
        codebuild.create_project(name=name, tags=[{'key': 'Lab', 'value': LAB_KEY}], **settings)


def _build(build_id: str | None) -> dict[str, Any] | None:
    if not build_id:
        return None
    builds = _safe_call(lambda: _client('codebuild').batch_get_builds(ids=[build_id]).get('builds', []), [])
    return builds[0] if builds else None


def _build_ids(project: str) -> list[str]:
    return _safe_call(lambda: _client('codebuild').list_builds_for_project(projectName=project).get('ids', []), [])


def _wait_build(build_id: str) -> dict[str, Any]:
    build: dict[str, Any] = {'id': build_id}

    def finished() -> bool:
        build.update(_build(build_id) or {})
        return build.get('buildStatus') not in (None, 'IN_PROGRESS')
    _wait(finished, timeout=BUILD_WAIT_SECONDS, delay=2)
    return build


def _run_build(project: str) -> dict[str, Any]:
    return _wait_build(_client('codebuild').start_build(projectName=project)['build']['id'])


def _failed_phase(build: dict[str, Any]) -> dict[str, Any] | None:
    for phase in build.get('phases') or []:
        if phase.get('phaseStatus') in ('FAILED', 'FAULT', 'TIMED_OUT') and phase.get('phaseType') != 'COMPLETED':
            contexts = phase.get('contexts') or [{}]
            return {'phaseType': phase.get('phaseType'), 'phaseStatus': phase.get('phaseStatus'),
                    'message': contexts[0].get('message', '')}
    return None


def _build_summary(build: dict[str, Any]) -> dict[str, Any]:
    return {'id': build.get('id'), 'status': build.get('buildStatus'), 'failedPhase': _failed_phase(build)}


def _artifact_keys(prefix: str, name: str) -> list[str]:
    """AWS writes unzipped artifacts under <path>/<artifact name>/; Floci ignores both and writes to the bucket root."""
    return [f'{prefix}/{prefix}/{name}', name]


def _artifact(prefix: str, name: str) -> str | None:
    s3 = _client('s3')
    for key in _artifact_keys(prefix, name):
        try:
            return s3.get_object(Bucket=ARTIFACT_BUCKET, Key=key)['Body'].read().decode()
        except ClientError:
            continue
    return None


def _artifact_json(prefix: str, name: str) -> dict[str, Any] | None:
    body = _artifact(prefix, name)
    try:
        return json.loads(body) if body else None
    except ValueError:
        return None


def _clear_artifacts(prefix: str) -> None:
    s3 = _client('s3')
    names = ('junit.xml', 'coverage.json', 'settings.json') if prefix == UNIT_PREFIX else ('integration.json',)
    for name in names:
        for key in _artifact_keys(prefix, name):
            _safe_call(lambda key=key: s3.delete_object(Bucket=ARTIFACT_BUCKET, Key=key))


def _table_item(order_id: str) -> dict[str, Any] | None:
    return _safe_call(lambda: _client('dynamodb').get_item(TableName=TABLE, Key={'id': {'S': order_id}},
                                                           ConsistentRead=True).get('Item'))


def _pipeline() -> dict[str, Any] | None:
    return _safe_call(lambda: _client('codepipeline').get_pipeline(name=PIPELINE)['pipeline'])


def _execution(execution_id: str | None) -> dict[str, Any] | None:
    if not execution_id:
        return None
    return _safe_call(lambda: _client('codepipeline').get_pipeline_execution(
        pipelineName=PIPELINE, pipelineExecutionId=execution_id)['pipelineExecution'])


def _run_pipeline() -> dict[str, Any]:
    execution_id = _client('codepipeline').start_pipeline_execution(name=PIPELINE)['pipelineExecutionId']
    execution: dict[str, Any] = {'pipelineExecutionId': execution_id}

    def finished() -> bool:
        execution.update(_execution(execution_id) or {})
        return execution.get('status') not in (None, 'InProgress', 'Stopping')
    _wait(finished, timeout=PIPELINE_WAIT_SECONDS, delay=3)
    return execution


def _stage_results(execution_id: str) -> dict[str, str]:
    actions = _safe_call(lambda: _client('codepipeline').list_action_executions(
        pipelineName=PIPELINE, filter={'pipelineExecutionId': execution_id}).get('actionExecutionDetails', []), [])
    return {item.get('stageName'): item.get('status') for item in actions}


def _executions() -> list[dict[str, Any]]:
    return _safe_call(lambda: _client('codepipeline').list_pipeline_executions(
        pipelineName=PIPELINE).get('pipelineExecutionSummaries', []), [])


def _queue_url() -> str | None:
    return _safe_call(lambda: _client('sqs').get_queue_url(QueueName=QUEUE)['QueueUrl'])


def _receive(url: str, wait_seconds: int = 20) -> list[dict[str, Any]]:
    """Read failure events off the queue and delete them, keeping the event details."""
    sqs = _client('sqs')
    found: list[dict[str, Any]] = []
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        messages = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=2).get('Messages', [])
        for message in messages:
            sqs.delete_message(QueueUrl=url, ReceiptHandle=message['ReceiptHandle'])
            try:
                event = json.loads(message['Body'])
            except ValueError:
                continue
            detail = event.get('detail') or {}
            found.append({'detailType': event.get('detail-type'), 'pipeline': detail.get('pipeline'),
                          'stage': detail.get('stage'), 'state': detail.get('state'),
                          'executionId': detail.get('execution-id')})
        if found and not messages:
            break
    return found


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_settings() -> tuple[bool, str]:
    if _parameter(TABLE_PARAMETER) != TABLE or _parameter(COVERAGE_PARAMETER) != str(COVERAGE_MINIMUM):
        return False, f'Put {TABLE_PARAMETER} and {COVERAGE_PARAMETER} in Parameter Store.'
    if not _safe_call(lambda: _client('secretsmanager').describe_secret(SecretId=SECRET)):
        return False, f'Create the {SECRET} secret.'
    project = _project(UNIT_PROJECT)
    if not project:
        return False, f'Create the {UNIT_PROJECT} build project.'
    buildspec = (project.get('source') or {}).get('buildspec') or ''
    if TABLE_PARAMETER not in buildspec or SECRET not in buildspec:
        return False, f'The {UNIT_PROJECT} buildspec does not reference the parameter and the secret.'
    if not _source_release():
        return False, f'Upload the source to s3://{SOURCE_BUCKET}/{SOURCE_KEY}.'
    return True, (f'{UNIT_PROJECT} reads {TABLE_PARAMETER}, {COVERAGE_PARAMETER}, and {SECRET} when each build '
                  'starts.')


def _check_unit() -> tuple[bool, str]:
    marker = _marked('unit-tests') or {}
    coverage, settings = marker.get('coverage') or {}, marker.get('settings') or {}
    if marker.get('build', {}).get('status') != 'SUCCEEDED':
        return False, 'Run a successful unit test build.'
    if (coverage.get('percent') or 0) < COVERAGE_MINIMUM:
        return False, 'The build did not record coverage at or above the minimum.'
    if settings.get('ordersTable') != TABLE or not settings.get('paymentsKeyPresent'):
        return False, 'The build did not receive the table name and the payments key.'
    return True, (f'{marker.get("release")} passed with {coverage["percent"]}% coverage; the build got {TABLE} and a '
                  f'{settings.get("paymentsKeyLength")}-character key without the key appearing in its reports.')


def _check_coverage() -> tuple[bool, str]:
    marker = _marked('coverage-gate') or {}
    blocked, passed = marker.get('blocked') or {}, marker.get('passed') or {}
    failed = blocked.get('failedPhase') or {}
    if blocked.get('status') != 'FAILED' or 'below' not in (failed.get('message') or ''):
        return False, f'Build {RELEASE_2} and watch the coverage gate fail it.'
    if passed.get('status') != 'SUCCEEDED':
        return False, f'Build {RELEASE_2_TESTED}, which adds the refund tests.'
    return True, (f'{RELEASE_2} failed in {failed.get("phaseType")} at {blocked.get("coverage")}% coverage; '
                  f'{RELEASE_2_TESTED} passed at {passed.get("coverage")}%.')


def _check_report() -> tuple[bool, str]:
    group = _safe_call(lambda: _client('codebuild').batch_get_report_groups(
        reportGroupArns=[f'arn:aws:codebuild:{REGION}:{ACCOUNT}:report-group/{REPORT_GROUP}']).get('reportGroups', []), [])
    if not group:
        return False, f'Create the {REPORT_GROUP} report group.'
    report = (_marked('test-report') or {}).get('report') or {}
    if not report.get('total'):
        return False, 'Read the JUnit report from the latest unit test build.'
    return True, (f'{REPORT_GROUP} exists; the latest report has {report["total"]} test cases and a '
                  f'{report.get("passRate")}% pass rate.')


def _check_integration() -> tuple[bool, str]:
    marker = _marked('integration-test') or {}
    if marker.get('build', {}).get('status') != 'SUCCEEDED':
        return False, 'Run a successful integration test build.'
    order_id = marker.get('orderId')
    if not order_id or not _table_item(order_id):
        return False, f'The order the build wrote is not in {TABLE}.'
    return True, f'The build saved {order_id} to {TABLE} and read it back; the item is in the table.'


def _check_pipeline() -> tuple[bool, str]:
    pipeline = _pipeline()
    if not pipeline:
        return False, f'Create the {PIPELINE} pipeline.'
    stages = [stage['name'] for stage in pipeline.get('stages', [])]
    if stages != [SOURCE_STAGE, UNIT_STAGE, INTEGRATION_STAGE]:
        return False, f'The pipeline stages are {stages}.'
    execution = _execution((_marked('pipeline') or {}).get('executionId'))
    if (execution or {}).get('status') != 'Succeeded':
        return False, 'Run the pipeline to a successful execution.'
    return True, f'{PIPELINE} ran {", ".join(stages)} and succeeded.'


def _check_events() -> tuple[bool, str]:
    marker = _marked('failure-events') or {}
    if not _safe_call(lambda: _client('events').describe_rule(Name=RULE)):
        return False, f'Create the {RULE} rule.'
    if marker.get('status') != 'Failed':
        return False, f'Run {RELEASE_3} through the pipeline.'
    caught = [event for event in marker.get('events') or []
              if event.get('stage') == INTEGRATION_STAGE and event.get('state') == 'FAILED']
    if not caught:
        return False, f'No failed {INTEGRATION_STAGE} event reached {QUEUE}.'
    return True, (f'{RELEASE_3} failed {INTEGRATION_STAGE} ({marker.get("reason")}), and the failed stage event '
                  f'reached {QUEUE}.')


def _check_fix() -> tuple[bool, str]:
    marker = _marked('fix-release') or {}
    if not _check_events()[0]:
        return False, 'Catch the broken release first.'
    execution = _execution(marker.get('executionId'))
    if (execution or {}).get('status') != 'Succeeded':
        return False, f'Run {RELEASE_3_FIXED} through the pipeline.'
    if marker.get('newFailures'):
        return False, 'A failure event arrived for the fixed release.'
    return True, f'{RELEASE_3_FIXED} passed every gate, and no new failure reached {QUEUE}.'


CHECKS = {
    'build-settings': _check_settings,
    'unit-tests': _check_unit,
    'coverage-gate': _check_coverage,
    'test-report': _check_report,
    'integration-test': _check_integration,
    'pipeline': _check_pipeline,
    'failure-events': _check_events,
    'fix-release': _check_fix,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def step_build_settings() -> dict[str, Any]:
    started = time.perf_counter()
    ssm, secrets = _client('ssm'), _client('secretsmanager')
    ssm.put_parameter(Name=TABLE_PARAMETER, Value=TABLE, Type='String', Overwrite=True)
    ssm.put_parameter(Name=COVERAGE_PARAMETER, Value=str(COVERAGE_MINIMUM), Type='String', Overwrite=True)
    try:
        secrets.create_secret(Name=SECRET, SecretString=SECRET_VALUE, Description='Payments API key (pipeline lab)')
    except ClientError as exc:
        if _code(exc) != 'ResourceExistsException':
            raise
        secrets.put_secret_value(SecretId=SECRET, SecretString=SECRET_VALUE)
    _ensure_bucket(SOURCE_BUCKET, versioned=True)
    _ensure_bucket(ARTIFACT_BUCKET)
    _upload(RELEASE_1)
    _ensure_role(BUILD_ROLE, _trust('codebuild.amazonaws.com'), BUILD_POLICY, 'CodeBuild role (pipeline gates lab)')
    _ensure_project(UNIT_PROJECT, UNIT_BUILDSPEC, UNIT_PREFIX)
    response = {'Parameters': {TABLE_PARAMETER: TABLE, COVERAGE_PARAMETER: str(COVERAGE_MINIMUM)},
                'Secret': SECRET, 'Source': f's3://{SOURCE_BUCKET}/{SOURCE_KEY} ({RELEASE_1})',
                'Project': UNIT_PROJECT, 'Buildspec': UNIT_BUILDSPEC, 'RolePolicy': BUILD_POLICY}
    verified, message = _check_settings()
    return _result('build-settings', SETTINGS_COMMAND, response, verified, message, started)


def _unit_build(release: str) -> dict[str, Any]:
    _upload(release)
    _clear_artifacts(UNIT_PREFIX)
    build = _run_build(UNIT_PROJECT)
    coverage = _artifact_json(UNIT_PREFIX, 'coverage.json') or {}
    return {'release': release, 'build': _build_summary(build), 'coverage': coverage,
            'settings': _artifact_json(UNIT_PREFIX, 'settings.json') or {}}


def step_unit_tests() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_settings()[0]:
        return _result('unit-tests', UNIT_COMMAND, {}, False, 'Set up the build settings first.', started)
    run = _unit_build(RELEASE_1)
    _marker('unit-tests', run)
    verified, message = _check_unit()
    return _result('unit-tests', UNIT_COMMAND, run, verified, message, started)


def step_coverage_gate() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_unit()[0]:
        return _result('coverage-gate', COVERAGE_COMMAND, {}, False, 'Run the unit tests first.', started)
    blocked = _unit_build(RELEASE_2)
    passed = _unit_build(RELEASE_2_TESTED)
    marker = {
        'blocked': {**blocked['build'], 'coverage': blocked['coverage'].get('percent'),
                    'missedLines': blocked['coverage'].get('missedLines')},
        'passed': {**passed['build'], 'coverage': passed['coverage'].get('percent')},
    }
    _marker('coverage-gate', marker)
    verified, message = _check_coverage()
    return _result('coverage-gate', COVERAGE_COMMAND, marker, verified, message, started)


def step_test_report() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_coverage()[0]:
        return _result('test-report', REPORT_COMMAND, {}, False, 'Pass the coverage gate first.', started)
    codebuild = _client('codebuild')
    arn = f'arn:aws:codebuild:{REGION}:{ACCOUNT}:report-group/{REPORT_GROUP}'
    if not _safe_call(lambda: codebuild.batch_get_report_groups(reportGroupArns=[arn]).get('reportGroups'), []):
        codebuild.create_report_group(
            name=REPORT_GROUP, type='TEST',
            exportConfig={'exportConfigType': 'S3', 's3Destination': {
                'bucket': ARTIFACT_BUCKET, 'path': 'reports', 'packaging': 'NONE'}},
            tags=[{'key': 'Lab', 'value': LAB_KEY}])
    try:
        reports = codebuild.list_reports_for_report_group(reportGroupArn=arn).get('reports', [])
        listing: Any = reports
    except ClientError as exc:
        listing = {'error': _code(exc), 'message': str(exc)}
    junit = _artifact(UNIT_PREFIX, 'junit.xml')
    report = junit_summary(junit) if junit else {}
    _marker('test-report', {'report': report})
    response = {'ReportGroup': arn, 'ListReportsForReportGroup': listing,
                'ReportFromJUnit': report, 'Coverage': _artifact_json(UNIT_PREFIX, 'coverage.json')}
    verified, message = _check_report()
    return _result('test-report', REPORT_COMMAND, response, verified, message, started)


def _ensure_table() -> None:
    dynamodb = _client('dynamodb')
    try:
        dynamodb.describe_table(TableName=TABLE)
    except ClientError as exc:
        if _code(exc) != 'ResourceNotFoundException':
            raise
        dynamodb.create_table(TableName=TABLE, AttributeDefinitions=[{'AttributeName': 'id', 'AttributeType': 'S'}],
                              KeySchema=[{'AttributeName': 'id', 'KeyType': 'HASH'}], BillingMode='PAY_PER_REQUEST',
                              Tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
        dynamodb.get_waiter('table_exists').wait(TableName=TABLE, WaiterConfig={'Delay': 1, 'MaxAttempts': 30})


def step_integration_test() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_report()[0]:
        return _result('integration-test', INTEGRATION_COMMAND, {}, False, 'Read the test report first.', started)
    _ensure_table()
    _ensure_project(INTEGRATION_PROJECT, INTEGRATION_BUILDSPEC, INTEGRATION_PREFIX)
    _clear_artifacts(INTEGRATION_PREFIX)
    build = _run_build(INTEGRATION_PROJECT)
    outcome = _artifact_json(INTEGRATION_PREFIX, 'integration.json') or {}
    marker = {'release': _source_release(), 'build': _build_summary(build), 'orderId': outcome.get('orderId'),
              'outcome': outcome}
    _marker('integration-test', marker)
    response = {**marker, 'ItemInTable': _table_item(outcome.get('orderId') or '')}
    verified, message = _check_integration()
    return _result('integration-test', INTEGRATION_COMMAND, response, verified, message, started)


def step_pipeline() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_integration()[0]:
        return _result('pipeline', PIPELINE_COMMAND, {}, False, 'Run the integration test first.', started)
    _ensure_role(PIPELINE_ROLE, _trust('codepipeline.amazonaws.com'), PIPELINE_POLICY,
                 'CodePipeline role (pipeline gates lab)')
    codepipeline = _client('codepipeline')
    if _pipeline():
        codepipeline.update_pipeline(pipeline=pipeline_document())
    else:
        codepipeline.create_pipeline(pipeline=pipeline_document(), tags=[{'key': 'Lab', 'value': LAB_KEY}])
    execution = _run_pipeline()
    execution_id = execution.get('pipelineExecutionId')
    _marker('pipeline', {'executionId': execution_id})
    response = {'Stages': [stage['name'] for stage in (_pipeline() or {}).get('stages', [])],
                'Execution': {'Id': execution_id, 'Status': execution.get('status')},
                'Actions': _stage_results(execution_id), 'Release': _source_release()}
    verified, message = _check_pipeline()
    return _result('pipeline', PIPELINE_COMMAND, response, verified, message, started)


def _ensure_failure_route() -> str:
    sqs, events = _client('sqs'), _client('events')
    url = sqs.create_queue(QueueName=QUEUE, tags={'Lab': LAB_KEY})['QueueUrl']
    queue_arn = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=['QueueArn'])['Attributes']['QueueArn']
    rule_arn = events.put_rule(Name=RULE, EventPattern=json.dumps(event_pattern()), State='ENABLED',
                               Description='Failed stages of the pipeline gates lab')['RuleArn']
    events.put_targets(Rule=RULE, Targets=[{'Id': 'failures-queue', 'Arn': queue_arn}])
    sqs.set_queue_attributes(QueueUrl=url, Attributes={'Policy': json.dumps(queue_policy(queue_arn, rule_arn))})
    sqs.purge_queue(QueueUrl=url)
    return url


def step_failure_events() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_pipeline()[0]:
        return _result('failure-events', EVENTS_COMMAND, {}, False, 'Run the pipeline first.', started)
    url = _ensure_failure_route()
    _upload(RELEASE_3)
    _clear_artifacts(INTEGRATION_PREFIX)
    execution = _run_pipeline()
    execution_id = execution.get('pipelineExecutionId')
    outcome = _artifact_json(INTEGRATION_PREFIX, 'integration.json') or {}
    received = _receive(url)
    marker = {'executionId': execution_id, 'status': execution.get('status'),
              'reason': (outcome.get('error') or '')[:160], 'events': received}
    _marker('failure-events', marker)
    response = {'EventPattern': event_pattern(), 'Execution': {'Id': execution_id, 'Status': execution.get('status')},
                'Actions': _stage_results(execution_id), 'IntegrationResult': outcome, 'QueueMessages': received}
    verified, message = _check_events()
    return _result('failure-events', EVENTS_COMMAND, response, verified, message, started)


def step_fix_release() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_events()[0]:
        return _result('fix-release', RELEASE_COMMAND, {}, False, 'Catch the broken release first.', started)
    url = _queue_url()
    _upload(RELEASE_3_FIXED)
    execution = _run_pipeline()
    execution_id = execution.get('pipelineExecutionId')
    new_failures = _receive(url, wait_seconds=6) if url else []
    _marker('fix-release', {'executionId': execution_id, 'newFailures': new_failures})
    history = [{'id': item.get('pipelineExecutionId'), 'status': item.get('status')} for item in _executions()[:3]]
    response = {'Execution': {'Id': execution_id, 'Status': execution.get('status')},
                'Actions': _stage_results(execution_id), 'History': history, 'NewFailureEvents': new_failures}
    verified, message = _check_fix()
    return _result('fix-release', RELEASE_COMMAND, response, verified, message, started)


RUNNERS = {
    'build-settings': step_build_settings,
    'unit-tests': step_unit_tests,
    'coverage-gate': step_coverage_gate,
    'test-report': step_test_report,
    'integration-test': step_integration_test,
    'pipeline': step_pipeline,
    'failure-events': step_failure_events,
    'fix-release': step_fix_release,
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


def _empty_bucket(name: str) -> None:
    s3 = _client('s3')
    listing = s3.list_object_versions(Bucket=name)
    objects = [{'Key': item['Key'], 'VersionId': item['VersionId']}
               for item in listing.get('Versions', []) + listing.get('DeleteMarkers', [])]
    if objects:
        s3.delete_objects(Bucket=name, Delete={'Objects': objects})
    for item in s3.list_objects_v2(Bucket=name).get('Contents', []):
        s3.delete_object(Bucket=name, Key=item['Key'])


def reset() -> dict[str, Any]:
    """Remove the pipeline, rule, queue, projects, report group, table, settings, roles, and buckets."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if ('NotFound' in code or 'NonExistent' in code or 'DoesNotExist' in code
                    or code in ('NoSuchEntity', 'NoSuchBucket', 'ParameterNotFound', 'InvalidInputException')):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    codepipeline, codebuild, events = _client('codepipeline'), _client('codebuild'), _client('events')
    if _pipeline():
        attempt(f'pipeline:{PIPELINE}', lambda: codepipeline.delete_pipeline(name=PIPELINE))
    if _safe_call(lambda: events.describe_rule(Name=RULE)):
        attempt(f'targets:{RULE}', lambda: events.remove_targets(Rule=RULE, Ids=['failures-queue']))
        attempt(f'rule:{RULE}', lambda: events.delete_rule(Name=RULE))
    url = _queue_url()
    if url:
        attempt(f'queue:{QUEUE}', lambda: _client('sqs').delete_queue(QueueUrl=url))
    for project in (UNIT_PROJECT, INTEGRATION_PROJECT):
        if _project(project):
            attempt(f'project:{project}', lambda project=project: codebuild.delete_project(name=project))
    arn = f'arn:aws:codebuild:{REGION}:{ACCOUNT}:report-group/{REPORT_GROUP}'
    if _safe_call(lambda: codebuild.batch_get_report_groups(reportGroupArns=[arn]).get('reportGroups'), []):
        attempt(f'report-group:{REPORT_GROUP}', lambda: codebuild.delete_report_group(arn=arn, deleteReports=True))
    attempt(f'table:{TABLE}', lambda: _client('dynamodb').delete_table(TableName=TABLE))
    ssm = _client('ssm')
    for name in (TABLE_PARAMETER, COVERAGE_PARAMETER):
        attempt(f'parameter:{name}', lambda name=name: ssm.delete_parameter(Name=name))
    attempt(f'secret:{SECRET}', lambda: _client('secretsmanager').delete_secret(
        SecretId=SECRET, ForceDeleteWithoutRecovery=True))
    iam = _client('iam')
    for role in (BUILD_ROLE, PIPELINE_ROLE):
        attempt(f'role-policy:{role}', lambda role=role: iam.delete_role_policy(RoleName=role, PolicyName=INLINE_POLICY))
        attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))
    for bucket in (SOURCE_BUCKET, ARTIFACT_BUCKET):
        attempt(f'objects:{bucket}', lambda bucket=bucket: _empty_bucket(bucket))
        attempt(f'bucket:{bucket}', lambda bucket=bucket: _client('s3').delete_bucket(Bucket=bucket))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws codepipeline delete-pipeline --name {PIPELINE}\n'
            f'aws events remove-targets --rule {RULE} --ids failures-queue && aws events delete-rule --name {RULE}\n'
            f'aws codebuild delete-project --name {UNIT_PROJECT} && aws codebuild delete-project --name {INTEGRATION_PROJECT}\n'
            f'aws dynamodb delete-table --table-name {TABLE}\n'
            f'aws s3 rb s3://{SOURCE_BUCKET} --force && aws s3 rb s3://{ARTIFACT_BUCKET} --force'
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
            else 'The pipeline, rule, queue, projects, report group, table, settings, roles, and buckets are gone.',
        },
    }
