"""CI/CD pipeline lab: ship a Lambda price quote service from a versioned S3 source through CodeBuild (unit tests and
packaging), a failing build traced to its failed phase, a CodePipeline with source, build, and manual approval stages,
a CodeDeploy canary from version 1 to version 2 of a Lambda alias, and a rollback.

Floci 2.3.0 runs builds for real: it pulls the source zip from S3, runs each buildspec phase with sh in a Docker
container of the project's image, fails the build on a non-zero exit, and uploads a zip artifact. Pipelines run their
S3 source, CodeBuild, and manual approval actions. CodeDeploy shifts Lambda alias traffic for real, canary and linear,
on a clock of seconds instead of minutes. What Floci does not do: build logs stay empty (the failed phase's context
holds the output instead), artifacts are always named <project>-<build number>.zip at the bucket root, a project with
source type CODEPIPELINE gets no source files, CloudWatch alarms never stop a deployment, and stopping a deployment
leaves the alias weights where they were, so the lab rolls back by deploying the previous version.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'codepipeline'
LAB_KEY = 'lambda-cicd'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

BUCKET = 'lab-cicd-artifacts'
SOURCE_KEY = 'source/quote-service.zip'
ARTIFACT_PATH = 'builds'
ARTIFACT_NAME = 'quote-service.zip'

FUNCTION = 'lab-cicd-quote'
ALIAS = 'live'
RUNTIME = 'python3.12'
HANDLER = 'app.handler'
LAMBDA_ROLE = 'lab-cicd-lambda-role'
BUILD_ROLE = 'lab-cicd-codebuild-role'
PIPELINE_ROLE = 'lab-cicd-pipeline-role'
DEPLOY_ROLE = 'lab-cicd-codedeploy-role'
LAMBDA_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{LAMBDA_ROLE}'
BUILD_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{BUILD_ROLE}'
PIPELINE_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{PIPELINE_ROLE}'
DEPLOY_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{DEPLOY_ROLE}'
DEPLOY_POLICY_ARN = 'arn:aws:iam::aws:policy/service-role/AWSCodeDeployRoleForLambda'
INLINE_POLICY = 'lab-cicd'

PROJECT = 'lab-cicd-build'
# Floci runs the build in this image from Docker Hub. On AWS the curated aws/codebuild/standard:7.0 image is usual,
# but any image the build can pull works, including this one.
BUILD_IMAGE = 'python:3.12-slim'
BUILD_STAGE = 'ci'
BUILD_TIMEOUT_MINUTES = 10
BUILD_WAIT_SECONDS = 240

PIPELINE = 'lab-cicd-pipeline'
SOURCE_STAGE, BUILD_STAGE_NAME, APPROVAL_STAGE = 'Source', 'Build', 'Approve'
SOURCE_ACTION, BUILD_ACTION, APPROVAL_ACTION = 'S3Source', 'UnitTestAndPackage', 'ReleaseApproval'
PIPELINE_WAIT_SECONDS = 300

APPLICATION = 'lab-cicd-app'
DEPLOYMENT_GROUP = 'lab-cicd-live'
CANARY_CONFIG = 'CodeDeployDefault.LambdaCanary10Percent5Minutes'
ROLLBACK_CONFIG = 'CodeDeployDefault.LambdaAllAtOnce'
CANARY_PERCENT = 10
ALARM = 'lab-cicd-quote-errors'
DEPLOY_WAIT_SECONDS = 120

UNIT_PRICE = 10.0
BULK_QUANTITY = 10
RELEASES = {
    # release -> (discount for orders of BULK_QUANTITY or more, total the unit test expects for BULK_QUANTITY)
    '1': (0.0, 100.0),
    '2': (0.1, 90.0),
}
BROKEN_DISCOUNT = 1.0  # a typo for 0.1: bulk orders become free, and the unit test catches it

BUILDSPEC = """version: 0.2
env:
  variables:
    APP_NAME: quote-service
phases:
  install:
    commands:
      - python --version
  pre_build:
    commands:
      - python -m unittest -v test_app
  build:
    commands:
      - mkdir -p dist
      - cp app.py dist/
      - |
        python -c "import json, os; json.dump({'build': os.environ['CODEBUILD_BUILD_ID'], 'stage': os.environ['STAGE'], 'app': os.environ['APP_NAME']}, open('dist/build-info.json', 'w'))"
artifacts:
  base-directory: dist
  files:
    - '**/*'
"""

LAMBDA_TRUST = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}],
}
LOGS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{'Effect': 'Allow', 'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
                   'Resource': '*'}],
}


def _trust(service: str) -> dict[str, Any]:
    return {'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Principal': {'Service': service}, 'Action': 'sts:AssumeRole'}]}


BUILD_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'ReadSourceWriteArtifacts', 'Effect': 'Allow',
         'Action': ['s3:GetObject', 's3:GetObjectVersion', 's3:PutObject'], 'Resource': f'arn:aws:s3:::{BUCKET}/*'},
        {'Sid': 'BuildLogs', 'Effect': 'Allow',
         'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
         'Resource': f'arn:aws:logs:{REGION}:{ACCOUNT}:log-group:/aws/codebuild/{PROJECT}*'},
    ],
}
PIPELINE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {'Sid': 'ArtifactStore', 'Effect': 'Allow',
         'Action': ['s3:GetObject', 's3:GetObjectVersion', 's3:GetBucketVersioning', 's3:PutObject'],
         'Resource': [f'arn:aws:s3:::{BUCKET}', f'arn:aws:s3:::{BUCKET}/*']},
        {'Sid': 'RunBuilds', 'Effect': 'Allow', 'Action': ['codebuild:StartBuild', 'codebuild:BatchGetBuilds'],
         'Resource': f'arn:aws:codebuild:{REGION}:{ACCOUNT}:project/{PROJECT}'},
    ],
}

BUCKET_COMMAND = (
    f'aws s3api create-bucket --bucket {BUCKET}\n'
    f'aws s3api put-bucket-versioning --bucket {BUCKET} --versioning-configuration Status=Enabled\n'
    'zip quote-service.zip app.py test_app.py buildspec.yml   # release 1\n'
    f'aws s3 cp quote-service.zip s3://{BUCKET}/{SOURCE_KEY}'
)
PROJECT_COMMAND = (
    f'aws iam create-role --role-name {BUILD_ROLE} --assume-role-policy-document file://codebuild-trust.json\n'
    f'aws iam put-role-policy --role-name {BUILD_ROLE} --policy-name {INLINE_POLICY} --policy-document file://build-policy.json\n'
    f'aws codebuild create-project --name {PROJECT} --service-role {BUILD_ROLE} \\\n'
    f'  --source type=S3,location={BUCKET}/{SOURCE_KEY} \\\n'
    f'  --artifacts type=S3,location={BUCKET},path={ARTIFACT_PATH},namespaceType=BUILD_ID,name={ARTIFACT_NAME},packaging=ZIP \\\n'
    f'  --environment type=LINUX_CONTAINER,computeType=BUILD_GENERAL1_SMALL,image={BUILD_IMAGE},'
    f'environmentVariables=[{{name=STAGE,value={BUILD_STAGE}}}] \\\n'
    f'  --timeout-in-minutes {BUILD_TIMEOUT_MINUTES}'
)
FIRST_BUILD_COMMAND = (
    f'aws codebuild start-build --project-name {PROJECT}\n'
    'aws codebuild batch-get-builds --ids <build-id> --query "builds[0].[buildStatus,phases[].[phaseType,phaseStatus]]"\n'
    f'aws s3 cp s3://{BUCKET}/<artifact-key> . && unzip -l <artifact>'
)
DEPLOY_V1_COMMAND = (
    f'aws iam create-role --role-name {LAMBDA_ROLE} --assume-role-policy-document file://lambda-trust.json\n'
    f'aws lambda create-function --function-name {FUNCTION} --runtime {RUNTIME} --handler {HANDLER} '
    f'--role {LAMBDA_ROLE_ARN} --code S3Bucket={BUCKET},S3Key=<artifact-key> --publish\n'
    f'aws lambda create-alias --function-name {FUNCTION} --name {ALIAS} --function-version 1\n'
    f'aws lambda invoke --function-name {FUNCTION}:{ALIAS} --payload \'{{"quantity": {BULK_QUANTITY}}}\' out.json'
)
FAILING_BUILD_COMMAND = (
    '# release 2 with a typo: DISCOUNT = 1.0 instead of 0.1\n'
    f'aws s3 cp quote-service.zip s3://{BUCKET}/{SOURCE_KEY}\n'
    f'aws codebuild start-build --project-name {PROJECT}\n'
    'aws codebuild batch-get-builds --ids <build-id> --query "builds[0].phases[?phaseStatus==\'FAILED\']"\n'
    f'aws logs get-log-events --log-group-name /aws/codebuild/{PROJECT} --log-stream-name <stream>\n'
    f'aws s3api list-object-versions --bucket {BUCKET} --prefix {SOURCE_KEY}'
)
PIPELINE_COMMAND = (
    '# release 2, fixed: DISCOUNT = 0.1\n'
    f'aws s3 cp quote-service.zip s3://{BUCKET}/{SOURCE_KEY}\n'
    f'aws iam create-role --role-name {PIPELINE_ROLE} --assume-role-policy-document file://codepipeline-trust.json\n'
    f'aws codepipeline create-pipeline --pipeline file://pipeline.json\n'
    f'# {SOURCE_STAGE}: S3 {BUCKET}/{SOURCE_KEY} -> {BUILD_STAGE_NAME}: CodeBuild {PROJECT} -> {APPROVAL_STAGE}: Manual'
)
RELEASE_COMMAND = (
    f'aws codepipeline start-pipeline-execution --name {PIPELINE}\n'
    f'aws codepipeline get-pipeline-state --name {PIPELINE}   # approval token\n'
    f'aws codepipeline put-approval-result --pipeline-name {PIPELINE} --stage-name {APPROVAL_STAGE} '
    f'--action-name {APPROVAL_ACTION} --token <token> --result summary="Release 2 reviewed",status=Approved\n'
    f'aws codepipeline get-pipeline-execution --pipeline-name {PIPELINE} --pipeline-execution-id <id>'
)
CANARY_COMMAND = (
    f'aws lambda update-function-code --function-name {FUNCTION} --s3-bucket {BUCKET} --s3-key <pipeline-artifact> --publish\n'
    f'aws cloudwatch put-metric-alarm --alarm-name {ALARM} --namespace AWS/Lambda --metric-name Errors '
    f'--dimensions Name=FunctionName,Value={FUNCTION} Name=Resource,Value={FUNCTION}:{ALIAS} ...\n'
    f'aws deploy create-application --application-name {APPLICATION} --compute-platform Lambda\n'
    f'aws deploy create-deployment-group --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    f'--service-role-arn {DEPLOY_ROLE_ARN} --deployment-config-name {CANARY_CONFIG} '
    '--deployment-style deploymentType=BLUE_GREEN,deploymentOption=WITH_TRAFFIC_CONTROL '
    f'--alarm-configuration enabled=true,alarms=[{{name={ALARM}}}] '
    '--auto-rollback-configuration enabled=true,events=DEPLOYMENT_FAILURE,DEPLOYMENT_STOP_ON_ALARM\n'
    f'aws deploy create-deployment --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    '--revision file://appspec-revision.json   # live: version 1 -> 2'
)
ROLLBACK_COMMAND = (
    f'aws deploy stop-deployment --deployment-id <id> --auto-rollback-enabled   # while a deployment is in progress\n'
    f'aws deploy create-deployment --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    f'--deployment-config-name {ROLLBACK_CONFIG} --revision file://appspec-rollback.json   # live: version 2 -> 1\n'
    f'aws lambda get-alias --function-name {FUNCTION} --name {ALIAS}'
)

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Ship a Lambda service through a CI/CD pipeline with CodeBuild, CodePipeline, and CodeDeploy',
    'description': (
        'Build and unit test a Lambda price quote service from a versioned S3 source with CodeBuild, trace a failing '
        'build to its failed phase, release through a CodePipeline with source, build, and manual approval stages, '
        'shift the live alias from version 1 to version 2 with a CodeDeploy canary, and roll back.'
    ),
    'steps': [
        {
            'key': 'source-bucket',
            'title': 'Put release 1 in a versioned source bucket',
            'command': BUCKET_COMMAND,
            'explanation': (
                'The source is a zip of the code, its unit tests, and buildspec.yml. A CodePipeline S3 source needs '
                'a versioned bucket, so every upload is a new revision the pipeline can pick up and roll back to. '
                'Teams usually keep source in Git (GitHub or GitLab through AWS CodeConnections, or CodeCommit). How '
                'Floci emulates this: the bucket and versions are real; CodeCommit is not supported, which is why the '
                'lab uses an S3 source.'
            ),
        },
        {
            'key': 'build-project',
            'title': 'Create a CodeBuild project that tests and packages the code',
            'command': PROJECT_COMMAND,
            'explanation': (
                'A build project names the source, the build environment (image, compute size, environment '
                'variables), the artifact destination, and a service role CodeBuild assumes. buildspec.yml runs '
                'phases in order: install, pre_build (here the unit tests), build (packaging), and post_build; the '
                'artifacts section picks the files to zip. Keep secrets out of plain environment variables: use '
                'Parameter Store or Secrets Manager references in the buildspec env section. How Floci emulates this: '
                f'the project is stored and builds run in a real container of {BUILD_IMAGE}; Floci does not check the '
                'service role, so the step checks its policy.'
            ),
        },
        {
            'key': 'first-build',
            'title': 'Run the first build and inspect its artifact',
            'command': FIRST_BUILD_COMMAND,
            'explanation': (
                'Each build reports a status per phase, so you can see exactly where a build stopped. The artifact '
                'is the deployable package: app.py plus a build-info.json that records the build ID and STAGE. With '
                f'namespaceType BUILD_ID, AWS writes it to {ARTIFACT_PATH}/<build-id>/{ARTIFACT_NAME}, so every build '
                'keeps its own package. How Floci emulates this: the build runs for real, but Floci names the '
                'artifact <project>-<build number>.zip at the bucket root, so the step looks in both places.'
            ),
        },
        {
            'key': 'deploy-v1',
            'title': f'Create the function from the artifact and point {ALIAS} at version 1',
            'command': DEPLOY_V1_COMMAND,
            'explanation': (
                'Lambda deployment packages are .zip files (up to 50 MB uploaded directly, 250 MB unzipped, or larger '
                'through S3) or container images in ECR up to 10 GB. Publishing creates an immutable numbered version; '
                f'an alias such as {ALIAS} is a pointer clients call, so a release only moves the pointer. How Floci '
                'emulates this: the function, version, and alias are real and the invoke runs the built code.'
            ),
        },
        {
            'key': 'failing-build',
            'title': 'Find why a release 2 build failed',
            'command': FAILING_BUILD_COMMAND,
            'explanation': (
                'Release 2 adds a 10% bulk discount, but a typo makes bulk orders free. The unit test in pre_build '
                'fails, the build fails, and nothing is packaged for deployment: that is the point of testing in the '
                'pipeline. To find the root cause, find the failed phase, then read the build log in CloudWatch Logs '
                f'(/aws/codebuild/{PROJECT}). How Floci emulates this: the test really fails in the container, but the '
                'log stream stays empty; the failed phase\'s context holds the test output instead, and the step reads '
                'it from there.'
            ),
        },
        {
            'key': 'pipeline',
            'title': 'Create a pipeline with source, build, and approval stages',
            'command': PIPELINE_COMMAND,
            'explanation': (
                'With the typo fixed and uploaded, CodePipeline chains the stages: the S3 source action outputs the '
                'zip as an artifact, the CodeBuild action tests and packages it, and a manual approval gates the '
                'release (it can notify an SNS topic). Pipelines store artifacts in an S3 artifact store and assume '
                'a service role. How Floci emulates this: the pipeline runs for real. On AWS the build project for a '
                'pipeline normally has source type CODEPIPELINE so it reads the source stage\'s artifact; Floci starts '
                'such builds without the source files, so this project keeps its S3 source, the same object the '
                'source stage reads.'
            ),
        },
        {
            'key': 'approve-release',
            'title': 'Run the pipeline and approve release 2',
            'command': RELEASE_COMMAND,
            'explanation': (
                'Starting an execution runs the source and build stages, then the approval waits with a token. '
                'Approving with PutApprovalResult (or in the console) lets the execution finish; rejecting fails it. '
                'Approvals time out after 7 days. How Floci emulates this: the stages run, the build runs again with '
                'the fixed code, and the approval waits for the step to approve it.'
            ),
        },
        {
            'key': 'canary-deploy',
            'title': f'Shift {ALIAS} to version 2 with a CodeDeploy canary',
            'command': CANARY_COMMAND,
            'explanation': (
                f'CodeDeploy for Lambda publishes nothing itself: the AppSpec names the function, alias, current '
                f'version, and target version. {CANARY_CONFIG} sends {CANARY_PERCENT}% of traffic to version 2, waits '
                '5 minutes, then shifts the rest; linear configs shift in equal steps, and AllAtOnce shifts '
                'immediately. An alarm on the alias\'s Errors metric stops the deployment and rolls back, and '
                'BeforeAllowTraffic and AfterAllowTraffic hooks can run test functions. How Floci emulates this: the '
                'traffic shift is real but takes seconds instead of minutes, and the step watches the alias weight '
                'during the canary. Floci stores the alarm and rollback settings but never evaluates the alarm.'
            ),
        },
        {
            'key': 'rollback',
            'title': 'Roll back to version 1',
            'command': ROLLBACK_COMMAND,
            'explanation': (
                'A customer reports a pricing problem, so you roll back. During a deployment, stopping it with '
                'automatic rollback (or an alarm) returns traffic to the original version. After a deployment, roll '
                'back by deploying the previous version: the alias and its versions make that fast because nothing '
                f'is rebuilt. Here {ROLLBACK_CONFIG} moves {ALIAS} back at once. How Floci emulates this: the '
                'redeployment is real; stopping a deployment in Floci leaves the alias weights where they were, '
                'which is why the lab redeploys.'
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


def _wait(predicate, timeout: float, delay: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def bulk_total(discount: float, quantity: int = BULK_QUANTITY, unit: float = UNIT_PRICE) -> float:
    total = quantity * unit
    if quantity >= BULK_QUANTITY:
        total *= 1 - discount
    return round(total, 2)


def app_source(release: str, discount: float) -> str:
    return (
        'import os\n\n'
        f'RELEASE = {release!r}\n'
        f'DISCOUNT = {discount!r}\n'
        f'UNIT_PRICE = {UNIT_PRICE!r}\n'
        f'BULK_QUANTITY = {BULK_QUANTITY}\n\n\n'
        'def price(quantity):\n'
        '    total = quantity * UNIT_PRICE\n'
        '    if quantity >= BULK_QUANTITY:\n'
        '        total *= 1 - DISCOUNT\n'
        '    return round(total, 2)\n\n\n'
        'def handler(event, context):\n'
        '    quantity = int(event.get("quantity", 1))\n'
        '    return {"release": RELEASE, "quantity": quantity, "total": price(quantity)}\n'
    )


def test_source(expected_bulk_total: float) -> str:
    return (
        'import unittest\n\n'
        'import app\n\n\n'
        'class PriceTests(unittest.TestCase):\n'
        '    def test_single_item(self):\n'
        f'        self.assertEqual(app.price(1), {UNIT_PRICE!r})\n\n'
        '    def test_bulk_order(self):\n'
        f'        self.assertEqual(app.price({BULK_QUANTITY}), {expected_bulk_total!r})\n\n\n'
        'if __name__ == "__main__":\n'
        '    unittest.main()\n'
    )


def source_zip(release: str, broken: bool = False) -> bytes:
    discount, expected = RELEASES[release]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, body in (('app.py', app_source(release, BROKEN_DISCOUNT if broken else discount)),
                           ('test_app.py', test_source(expected)), ('buildspec.yml', BUILDSPEC)):
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, body)
    return buffer.getvalue()


# ── Lookups ──────────────────────────────────────────────────────────────────

def _bucket_versioning() -> str | None:
    try:
        return _client('s3').get_bucket_versioning(Bucket=BUCKET).get('Status')
    except ClientError as exc:
        if _code(exc) in ('NoSuchBucket', '404'):
            return None
        raise


def _object_exists(key: str) -> bool:
    try:
        _client('s3').head_object(Bucket=BUCKET, Key=key)
        return True
    except ClientError as exc:
        if _code(exc) in ('404', 'NoSuchKey', 'NotFound', 'NoSuchBucket'):
            return False
        raise


def _source_versions() -> list[dict[str, Any]]:
    try:
        versions = _client('s3').list_object_versions(Bucket=BUCKET, Prefix=SOURCE_KEY).get('Versions', [])
    except ClientError as exc:
        if _code(exc) == 'NoSuchBucket':
            return []
        raise
    return [version for version in versions if version.get('Key') == SOURCE_KEY]


def _source_release() -> str | None:
    """The RELEASE in the current source zip, with '-broken' when it carries the discount typo."""
    try:
        body = _client('s3').get_object(Bucket=BUCKET, Key=SOURCE_KEY)['Body'].read()
    except ClientError as exc:
        if _code(exc) in ('NoSuchKey', 'NoSuchBucket', '404'):
            return None
        raise
    return _release_in_zip(body)


def _release_in_zip(body: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            app = archive.read('app.py').decode()
    except (zipfile.BadZipFile, KeyError):
        return None
    values = {}
    for line in app.splitlines():
        for name in ('RELEASE', 'DISCOUNT'):
            if line.startswith(f'{name} = '):
                values[name] = line.split('=', 1)[1].strip().strip('\'"')
    release = values.get('RELEASE')
    if release and values.get('DISCOUNT') == repr(BROKEN_DISCOUNT):
        return f'{release}-broken'
    return release


def _role(name: str) -> dict[str, Any] | None:
    try:
        return _client('iam').get_role(RoleName=name)['Role']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise


def _role_policy(name: str) -> dict[str, Any] | None:
    try:
        document = _client('iam').get_role_policy(RoleName=name, PolicyName=INLINE_POLICY)['PolicyDocument']
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return None
        raise
    return json.loads(document) if isinstance(document, str) else document


def _ensure_role(name: str, trust: dict[str, Any], description: str, policy: dict[str, Any] | None = None) -> None:
    iam = _client('iam')
    if not _role(name):
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description)
    if policy:
        iam.put_role_policy(RoleName=name, PolicyName=INLINE_POLICY, PolicyDocument=json.dumps(policy))


def _project() -> dict[str, Any] | None:
    projects = _client('codebuild').batch_get_projects(names=[PROJECT]).get('projects', [])
    return projects[0] if projects else None


def _build(build_id: str | None) -> dict[str, Any] | None:
    if not build_id:
        return None
    builds = _client('codebuild').batch_get_builds(ids=[build_id]).get('builds', [])
    return builds[0] if builds else None


def _latest_build_ids() -> list[str]:
    try:
        return _client('codebuild').list_builds_for_project(projectName=PROJECT).get('ids', [])
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return []
        raise


def _run_build() -> dict[str, Any]:
    codebuild = _client('codebuild')
    build_id = codebuild.start_build(projectName=PROJECT)['build']['id']
    build: dict[str, Any] = {'id': build_id}

    def finished() -> bool:
        build.update(_build(build_id) or {})
        return build.get('buildStatus') not in (None, 'IN_PROGRESS')
    _wait(finished, timeout=BUILD_WAIT_SECONDS, delay=2)
    return build


def _phases(build: dict[str, Any]) -> list[dict[str, Any]]:
    return [{'phaseType': phase.get('phaseType'), 'phaseStatus': phase.get('phaseStatus'),
             'contexts': phase.get('contexts')} for phase in build.get('phases') or []]


def _failed_phase(build: dict[str, Any]) -> dict[str, Any] | None:
    for phase in build.get('phases') or []:
        if phase.get('phaseStatus') in ('FAILED', 'FAULT', 'TIMED_OUT') and phase.get('phaseType') != 'COMPLETED':
            return phase
    return None


def _artifact_key(build: dict[str, Any]) -> str | None:
    """Where the build's zip landed: the AWS BUILD_ID path, or Floci's <project>-<build number>.zip."""
    build_id = build.get('id') or ''
    candidates = [f'{ARTIFACT_PATH}/{build_id.split(":", 1)[-1]}/{ARTIFACT_NAME}']
    if build.get('buildNumber'):
        candidates.append(f'{PROJECT}-{build["buildNumber"]}.zip')
    elif ':' in build_id and build_id.rsplit(':', 1)[-1].isdigit():
        candidates.append(f'{PROJECT}-{build_id.rsplit(":", 1)[-1]}.zip')
    for key in candidates:
        if _object_exists(key):
            return key
    return None


def _artifact_files(key: str) -> dict[str, str]:
    body = _client('s3').get_object(Bucket=BUCKET, Key=key)['Body'].read()
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        return {name: archive.read(name).decode(errors='replace') for name in archive.namelist()}


def _function() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_function(FunctionName=FUNCTION)['Configuration']
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _alias() -> dict[str, Any] | None:
    try:
        return _client('lambda').get_alias(FunctionName=FUNCTION, Name=ALIAS)
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return None
        raise


def _versions() -> list[str]:
    try:
        versions = _client('lambda').list_versions_by_function(FunctionName=FUNCTION).get('Versions', [])
    except ClientError as exc:
        if _code(exc) == 'ResourceNotFoundException':
            return []
        raise
    return [item['Version'] for item in versions if item.get('Version') != '$LATEST']


def _wait_function_ready() -> None:
    def ready() -> bool:
        function = _function() or {}
        return function.get('State', 'Active') == 'Active' and function.get('LastUpdateStatus', 'Successful') != 'InProgress'
    _wait(ready, timeout=60)


def _invoke(qualifier: str = ALIAS, quantity: int = BULK_QUANTITY) -> tuple[dict[str, Any], str]:
    response = _client('lambda').invoke(FunctionName=FUNCTION, Qualifier=qualifier,
                                        Payload=json.dumps({'quantity': quantity}).encode())
    body = json.loads(response['Payload'].read() or b'null')
    if response.get('FunctionError'):
        raise RuntimeError(f'{FUNCTION} failed: {body}')
    return body, response.get('ExecutedVersion') or ''


def _pipeline() -> dict[str, Any] | None:
    try:
        return _client('codepipeline').get_pipeline(name=PIPELINE)['pipeline']
    except ClientError as exc:
        if _code(exc) == 'PipelineNotFoundException':
            return None
        raise


def _execution(execution_id: str | None) -> dict[str, Any] | None:
    if not execution_id:
        return None
    try:
        return _client('codepipeline').get_pipeline_execution(
            pipelineName=PIPELINE, pipelineExecutionId=execution_id)['pipelineExecution']
    except ClientError as exc:
        if _code(exc) in ('PipelineNotFoundException', 'PipelineExecutionNotFoundException'):
            return None
        raise


def _approval_token() -> str | None:
    state = _client('codepipeline').get_pipeline_state(name=PIPELINE)
    for stage in state.get('stageStates', []):
        if stage.get('stageName') != APPROVAL_STAGE:
            continue
        for action in stage.get('actionStates', []):
            latest = action.get('latestExecution') or {}
            if action.get('actionName') == APPROVAL_ACTION and latest.get('status') == 'InProgress' and latest.get('token'):
                return latest['token']
    return None


def _deployment_group() -> dict[str, Any] | None:
    try:
        return _client('codedeploy').get_deployment_group(
            applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP)['deploymentGroupInfo']
    except ClientError as exc:
        if _code(exc) in ('ApplicationDoesNotExistException', 'DeploymentGroupDoesNotExistException'):
            return None
        raise


def _deployment(deployment_id: str | None) -> dict[str, Any] | None:
    if not deployment_id:
        return None
    try:
        return _client('codedeploy').get_deployment(deploymentId=deployment_id)['deploymentInfo']
    except ClientError as exc:
        if _code(exc) == 'DeploymentDoesNotExistException':
            return None
        raise


def _appspec(current: str, target: str) -> str:
    return json.dumps({
        'version': 0.0,
        'Resources': [{'QuoteFunction': {'Type': 'AWS::Lambda::Function', 'Properties': {
            'Name': FUNCTION, 'Alias': ALIAS, 'CurrentVersion': current, 'TargetVersion': target}}}],
    })


def _deploy(current: str, target: str, config: str | None = None, description: str = '') -> dict[str, Any]:
    """Create a deployment and watch the alias until it finishes, recording the traffic weights it passed through."""
    kwargs: dict[str, Any] = {
        'applicationName': APPLICATION, 'deploymentGroupName': DEPLOYMENT_GROUP, 'description': description,
        'revision': {'revisionType': 'AppSpecContent', 'appSpecContent': {'content': _appspec(current, target)}},
    }
    if config:
        kwargs['deploymentConfigName'] = config
    deployment_id = _client('codedeploy').create_deployment(**kwargs)['deploymentId']
    observed: list[dict[str, Any]] = []
    info: dict[str, Any] = {}

    def finished() -> bool:
        info.update(_deployment(deployment_id) or {})
        alias = _alias() or {}
        weights = ((alias.get('RoutingConfig') or {}).get('AdditionalVersionWeights')) or {}
        sample = {'Status': info.get('status'), 'AliasVersion': alias.get('FunctionVersion'), 'Weights': weights}
        if not observed or observed[-1] != sample:
            observed.append(sample)
        return info.get('status') in ('Succeeded', 'Failed', 'Stopped')
    _wait(finished, timeout=DEPLOY_WAIT_SECONDS, delay=0.5)
    return {'DeploymentId': deployment_id, 'Status': info.get('status'), 'ErrorInformation': info.get('errorInformation'),
            'Observed': observed}


def _canary_seen(observed: list[dict[str, Any]], target: str) -> bool:
    return any(abs(float(sample['Weights'].get(target, 0)) - CANARY_PERCENT / 100) < 0.001 for sample in observed)


def _alarm() -> dict[str, Any] | None:
    alarms = _client('cloudwatch').describe_alarms(AlarmNames=[ALARM]).get('MetricAlarms', [])
    return alarms[0] if alarms else None


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_bucket() -> tuple[bool, str]:
    if _bucket_versioning() != 'Enabled':
        return False, f'Bucket {BUCKET} must exist with versioning enabled.'
    if not _object_exists(SOURCE_KEY):
        return False, f'Upload the source zip to s3://{BUCKET}/{SOURCE_KEY}.'
    return True, f'{BUCKET} is versioned and holds the source at {SOURCE_KEY}.'


def _check_project() -> tuple[bool, str]:
    project = _project()
    if not project:
        return False, f'Build project {PROJECT} was not found.'
    source, artifacts, environment = project.get('source') or {}, project.get('artifacts') or {}, project.get('environment') or {}
    if source.get('type') != 'S3' or source.get('location') != f'{BUCKET}/{SOURCE_KEY}':
        return False, f'{PROJECT} must build from S3 {BUCKET}/{SOURCE_KEY}.'
    if artifacts.get('type') != 'S3' or artifacts.get('location') != BUCKET or artifacts.get('packaging') != 'ZIP':
        return False, f'{PROJECT} must write zipped artifacts to {BUCKET}.'
    if environment.get('image') != BUILD_IMAGE:
        return False, f'{PROJECT} must build in {BUILD_IMAGE}.'
    variables = {item.get('name'): item.get('value') for item in environment.get('environmentVariables') or []}
    if variables.get('STAGE') != BUILD_STAGE:
        return False, f'{PROJECT} must set STAGE={BUILD_STAGE}.'
    if project.get('serviceRole') not in (BUILD_ROLE_ARN, BUILD_ROLE) or not _role(BUILD_ROLE):
        return False, f'{PROJECT} must run as {BUILD_ROLE}.'
    policy = _role_policy(BUILD_ROLE) or {}
    actions = {action for statement in policy.get('Statement', []) for action in (
        statement.get('Action') if isinstance(statement.get('Action'), list) else [statement.get('Action')])}
    if not {'s3:GetObject', 's3:PutObject', 'logs:PutLogEvents'} <= actions:
        return False, f'{BUILD_ROLE} needs to read the source, write artifacts, and write build logs.'
    return True, (f'{PROJECT} builds {SOURCE_KEY} in {BUILD_IMAGE} with STAGE={BUILD_STAGE} as {BUILD_ROLE} and '
                  f'zips its artifacts to {BUCKET}.')


def _check_first_build() -> tuple[bool, str]:
    marker = _marked('first-build')
    if not marker:
        return False, 'Run the first build.'
    build = _build(marker['BuildId'])
    if not build or build.get('buildStatus') != 'SUCCEEDED':
        return False, f'Build {marker["BuildId"]} did not succeed.'
    if not _object_exists(marker['ArtifactKey']):
        return False, f'The artifact {marker["ArtifactKey"]} is missing.'
    return True, marker['message']


def _check_v1() -> tuple[bool, str]:
    function, alias = _function(), _alias()
    if not function:
        return False, f'Function {FUNCTION} was not found.'
    if function.get('Role') != LAMBDA_ROLE_ARN or function.get('Handler') != HANDLER:
        return False, f'{FUNCTION} must run {HANDLER} as {LAMBDA_ROLE}.'
    if not alias or '1' not in _versions():
        return False, f'Publish version 1 and create the {ALIAS} alias.'
    marker = _marked('deploy-v1')
    if not marker:
        return False, f'Invoke {ALIAS} to confirm release 1.'
    return True, marker['message']


def _check_failing_build() -> tuple[bool, str]:
    marker = _marked('failing-build')
    if not marker:
        return False, 'Build the broken release 2 and find the failed phase.'
    build = _build(marker['BuildId'])
    if not build or build.get('buildStatus') != 'FAILED':
        return False, f'Build {marker["BuildId"]} should have failed.'
    return True, marker['message']


def _check_pipeline() -> tuple[bool, str]:
    pipeline = _pipeline()
    if not pipeline:
        return False, f'Pipeline {PIPELINE} was not found.'
    stages = {stage.get('name'): stage for stage in pipeline.get('stages', [])}
    if list(stages) != [SOURCE_STAGE, BUILD_STAGE_NAME, APPROVAL_STAGE]:
        return False, f'{PIPELINE} needs the stages {SOURCE_STAGE}, {BUILD_STAGE_NAME}, and {APPROVAL_STAGE}, in order.'

    def action(stage: str) -> dict[str, Any]:
        actions = stages[stage].get('actions') or [{}]
        return actions[0]
    source, build, approval = action(SOURCE_STAGE), action(BUILD_STAGE_NAME), action(APPROVAL_STAGE)
    if (source.get('actionTypeId') or {}).get('provider') != 'S3' or (source.get('configuration') or {}).get('S3ObjectKey') != SOURCE_KEY:
        return False, f'The {SOURCE_STAGE} stage must read s3://{BUCKET}/{SOURCE_KEY}.'
    if (build.get('actionTypeId') or {}).get('provider') != 'CodeBuild' or (build.get('configuration') or {}).get('ProjectName') != PROJECT:
        return False, f'The {BUILD_STAGE_NAME} stage must run {PROJECT}.'
    source_out = [item.get('name') for item in source.get('outputArtifacts') or []]
    build_in = [item.get('name') for item in build.get('inputArtifacts') or []]
    if not source_out or source_out != build_in:
        return False, f'The {BUILD_STAGE_NAME} stage must take the {SOURCE_STAGE} stage\'s output artifact.'
    if (approval.get('actionTypeId') or {}).get('category') != 'Approval':
        return False, f'The {APPROVAL_STAGE} stage must be a manual approval.'
    store = pipeline.get('artifactStore') or {}
    if store.get('location') != BUCKET or pipeline.get('roleArn') != PIPELINE_ROLE_ARN:
        return False, f'{PIPELINE} must store artifacts in {BUCKET} and run as {PIPELINE_ROLE}.'
    if _source_release() != '2':
        return False, f'Upload the fixed release 2 source to {SOURCE_KEY}.'
    return True, (f'{PIPELINE} reads {SOURCE_KEY}, builds it with {PROJECT}, and waits for a manual approval; the '
                  'source holds the fixed release 2.')


def _check_release() -> tuple[bool, str]:
    marker = _marked('approve-release')
    if not marker:
        return False, 'Run the pipeline and approve the release.'
    execution = _execution(marker['ExecutionId'])
    if not execution or execution.get('status') != 'Succeeded':
        return False, f'Pipeline execution {marker["ExecutionId"]} has not succeeded.'
    return True, marker['message']


def _check_canary() -> tuple[bool, str]:
    marker = _marked('canary-deploy')
    if not marker:
        return False, f'Deploy version 2 to {ALIAS} with a canary.'
    group = _deployment_group()
    if not group or group.get('deploymentConfigName') != CANARY_CONFIG:
        return False, f'Deployment group {DEPLOYMENT_GROUP} must use {CANARY_CONFIG}.'
    deployment = _deployment(marker['DeploymentId'])
    if not deployment or deployment.get('status') != 'Succeeded':
        return False, f'Deployment {marker["DeploymentId"]} has not succeeded.'
    return True, marker['message']


def _check_rollback() -> tuple[bool, str]:
    marker = _marked('rollback')
    if not marker:
        return False, f'Roll {ALIAS} back to version 1.'
    alias = _alias() or {}
    weights = (alias.get('RoutingConfig') or {}).get('AdditionalVersionWeights') or {}
    if alias.get('FunctionVersion') != '1' or weights:
        return False, f'{ALIAS} should send all traffic to version 1.'
    return True, marker['message']


# ── Steps ────────────────────────────────────────────────────────────────────

def step_source_bucket() -> dict[str, Any]:
    started = time.perf_counter()
    s3 = _client('s3')
    if _bucket_versioning() is None:
        try:
            s3.create_bucket(Bucket=BUCKET)
        except ClientError as exc:
            if _code(exc) not in ('BucketAlreadyOwnedByYou', 'BucketAlreadyExists'):
                raise
    s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={'Status': 'Enabled'})
    put = s3.put_object(Bucket=BUCKET, Key=SOURCE_KEY, Body=source_zip('1'), ContentType='application/zip')
    response = {'Versioning': _bucket_versioning(), 'SourceKey': SOURCE_KEY, 'VersionId': put.get('VersionId'),
                'Files': ['app.py', 'test_app.py', 'buildspec.yml'], 'Release': _source_release()}
    verified, message = _check_bucket()
    if verified:
        message += ' Release 1 has no bulk discount.'
    return _result('source-bucket', BUCKET_COMMAND, response, verified, message, started)


def step_build_project() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_bucket()[0]:
        return _result('build-project', PROJECT_COMMAND, {}, False, f'Create {BUCKET} with the source first.', started)
    _ensure_role(BUILD_ROLE, _trust('codebuild.amazonaws.com'), 'CodeBuild service role (CI/CD lab)', BUILD_POLICY)
    settings = {
        'source': {'type': 'S3', 'location': f'{BUCKET}/{SOURCE_KEY}'},
        'artifacts': {'type': 'S3', 'location': BUCKET, 'path': ARTIFACT_PATH, 'namespaceType': 'BUILD_ID',
                      'name': ARTIFACT_NAME, 'packaging': 'ZIP'},
        'environment': {'type': 'LINUX_CONTAINER', 'computeType': 'BUILD_GENERAL1_SMALL', 'image': BUILD_IMAGE,
                        'environmentVariables': [{'name': 'STAGE', 'value': BUILD_STAGE, 'type': 'PLAINTEXT'}]},
        'serviceRole': BUILD_ROLE_ARN,
        'timeoutInMinutes': BUILD_TIMEOUT_MINUTES,
    }
    codebuild = _client('codebuild')
    if _project():
        codebuild.update_project(name=PROJECT, **settings)
    else:
        codebuild.create_project(name=PROJECT, description='Test and package the quote service (CI/CD lab)',
                                 tags=[{'key': 'Lab', 'value': LAB_KEY}], **settings)
    project = _project() or {}
    response = {'Project': {key: project.get(key) for key in (
        'name', 'source', 'artifacts', 'environment', 'serviceRole', 'timeoutInMinutes')},
        'Buildspec': BUILDSPEC, 'RolePolicy': _role_policy(BUILD_ROLE)}
    verified, message = _check_project()
    if verified:
        message += ' Floci does not check the role, so the step checked its policy.'
    return _result('build-project', PROJECT_COMMAND, response, verified, message, started)


def step_first_build() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_project()
    if not ok:
        return _result('first-build', FIRST_BUILD_COMMAND, {}, False, f'Create the project first: {problem}', started)
    if _source_release() != '1':
        _client('s3').put_object(Bucket=BUCKET, Key=SOURCE_KEY, Body=source_zip('1'), ContentType='application/zip')
    build = _run_build()
    response: dict[str, Any] = {'BuildId': build.get('id'), 'BuildStatus': build.get('buildStatus'),
                                'Phases': _phases(build)}
    if build.get('buildStatus') != 'SUCCEEDED':
        failed = _failed_phase(build) or {}
        detail = ' '.join(context.get('message', '') for context in failed.get('contexts') or [])[:300]
        message = f'Build {build.get("id")} ended {build.get("buildStatus")}'
        if failed:
            message += f' in {failed.get("phaseType")}: {detail}'
        if 'pull access denied' in detail or 'repository does not exist' in detail:
            message += f' Floci could not pull {BUILD_IMAGE}; check that Docker can reach Docker Hub.'
        return _result('first-build', FIRST_BUILD_COMMAND, response, False, message, started)
    key = _artifact_key(build)
    if not key:
        return _result('first-build', FIRST_BUILD_COMMAND, response, False,
                       f'Build {build.get("id")} succeeded but no artifact was found in {BUCKET}.', started)
    files = _artifact_files(key)
    try:
        build_info = json.loads(files.get('build-info.json') or '{}')
    except ValueError:
        build_info = {'raw': files.get('build-info.json')}
    response.update({'ArtifactKey': key, 'ArtifactFiles': sorted(files), 'BuildInfo': build_info})
    if 'app.py' not in files or 'build-info.json' not in files:
        return _result('first-build', FIRST_BUILD_COMMAND, response, False,
                       f'The artifact {key} should hold app.py and build-info.json.', started)
    message = (f'Build {build["id"]} passed every phase, including the unit tests, and packaged app.py and '
               f'build-info.json into s3://{BUCKET}/{key}.')
    if key.startswith(f'{PROJECT}-'):
        message += (f' Floci named the artifact {key} at the bucket root; AWS would write '
                    f'{ARTIFACT_PATH}/<build-id>/{ARTIFACT_NAME}.')
    _marker('first-build', {'BuildId': build['id'], 'ArtifactKey': key, 'message': message})
    verified, check = _check_first_build()
    return _result('first-build', FIRST_BUILD_COMMAND, response, verified, message if verified else check, started)


def step_deploy_v1() -> dict[str, Any]:
    started = time.perf_counter()
    marker = _marked('first-build')
    if not marker or not _check_first_build()[0]:
        return _result('deploy-v1', DEPLOY_V1_COMMAND, {}, False, 'Run the first build first.', started)
    _ensure_role(LAMBDA_ROLE, LAMBDA_TRUST, 'Quote service function role (CI/CD lab)', LOGS_POLICY)
    lam = _client('lambda')
    if _function() and '1' in _versions():
        pass
    elif _function():
        lam.update_function_code(FunctionName=FUNCTION, S3Bucket=BUCKET, S3Key=marker['ArtifactKey'])
        _wait_function_ready()
        lam.publish_version(FunctionName=FUNCTION, Description='Release 1')
    else:
        lam.create_function(FunctionName=FUNCTION, Runtime=RUNTIME, Handler=HANDLER, Role=LAMBDA_ROLE_ARN,
                            Code={'S3Bucket': BUCKET, 'S3Key': marker['ArtifactKey']}, Timeout=10,
                            Description='Quote service (CI/CD lab)', Publish=True, Tags={'Lab': LAB_KEY})
    _wait_function_ready()
    if _alias():
        lam.delete_alias(FunctionName=FUNCTION, Name=ALIAS)  # recreate: Floci never clears routing weights
    lam.create_alias(FunctionName=FUNCTION, Name=ALIAS, FunctionVersion='1', Description='Production traffic')
    body, executed = _invoke()
    alias = _alias() or {}
    response = {'Function': {key: (_function() or {}).get(key) for key in ('FunctionName', 'Runtime', 'Handler', 'Role')},
                'Versions': _versions(), 'Alias': {key: alias.get(key) for key in ('Name', 'FunctionVersion', 'RoutingConfig')},
                'Invoke': {'Payload': {'quantity': BULK_QUANTITY}, 'Response': body, 'ExecutedVersion': executed}}
    expected = bulk_total(RELEASES['1'][0])
    if body.get('release') != '1' or body.get('total') != expected:
        return _result('deploy-v1', DEPLOY_V1_COMMAND, response, False,
                       f'{FUNCTION}:{ALIAS} should answer release 1 with a total of {expected}.', started)
    message = (f'{FUNCTION} runs the built package; {ALIAS} points at version 1 and quotes {BULK_QUANTITY} items at '
               f'{expected}.')
    _marker('deploy-v1', {'message': message})
    verified, check = _check_v1()
    return _result('deploy-v1', DEPLOY_V1_COMMAND, response, verified, message if verified else check, started)


def step_failing_build() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_project()[0] or not _check_first_build()[0]:
        return _result('failing-build', FAILING_BUILD_COMMAND, {}, False, 'Run the first build first.', started)
    _client('s3').put_object(Bucket=BUCKET, Key=SOURCE_KEY, Body=source_zip('2', broken=True),
                             ContentType='application/zip')
    build = _run_build()
    failed = _failed_phase(build) or {}
    output = '\n'.join(context.get('message', '') for context in failed.get('contexts') or [])
    log_events: list[str] = []
    logs = build.get('logs') or {}
    if logs.get('groupName') and logs.get('streamName'):
        try:
            log_events = [event.get('message', '') for event in _client('logs').get_log_events(
                logGroupName=logs['groupName'], logStreamName=logs['streamName'], limit=50).get('events', [])]
        except ClientError:
            log_events = []
    response = {'BuildId': build.get('id'), 'BuildStatus': build.get('buildStatus'), 'Phases': _phases(build),
                'FailedPhase': failed.get('phaseType'), 'FailedPhaseOutput': output[-1500:],
                'LogEvents': log_events[-20:],
                'SourceVersions': [{key: version.get(key) for key in ('VersionId', 'IsLatest', 'LastModified')}
                                   for version in _source_versions()]}
    if build.get('buildStatus') != 'FAILED':
        return _result('failing-build', FAILING_BUILD_COMMAND, response, False,
                       f'Build {build.get("id")} ended {build.get("buildStatus")}; the broken release should fail '
                       'its unit tests.', started)
    if failed.get('phaseType') != 'PRE_BUILD' or 'test_bulk_order' not in (output + '\n'.join(log_events)):
        return _result('failing-build', FAILING_BUILD_COMMAND, response, False,
                       f'The build failed in {failed.get("phaseType")}, but it should fail the test_bulk_order unit '
                       'test in PRE_BUILD.', started)
    message = (f'Build {build["id"]} failed in PRE_BUILD: test_bulk_order expected {RELEASES["2"][1]} for '
               f'{BULK_QUANTITY} items but got {bulk_total(BROKEN_DISCOUNT)}, so DISCOUNT is wrong and nothing was '
               'packaged for deployment.')
    if not log_events:
        message += ' Floci left the build log empty, so the step read the output from the failed phase\'s context.'
    _marker('failing-build', {'BuildId': build['id'], 'message': message})
    verified, check = _check_failing_build()
    return _result('failing-build', FAILING_BUILD_COMMAND, response, verified, message if verified else check, started)


def _pipeline_document() -> dict[str, Any]:
    return {
        'name': PIPELINE,
        'roleArn': PIPELINE_ROLE_ARN,
        'artifactStore': {'type': 'S3', 'location': BUCKET},
        'stages': [
            {'name': SOURCE_STAGE, 'actions': [{
                'name': SOURCE_ACTION, 'runOrder': 1,
                'actionTypeId': {'category': 'Source', 'owner': 'AWS', 'provider': 'S3', 'version': '1'},
                'configuration': {'S3Bucket': BUCKET, 'S3ObjectKey': SOURCE_KEY, 'PollForSourceChanges': 'false'},
                'outputArtifacts': [{'name': 'SourceZip'}]}]},
            {'name': BUILD_STAGE_NAME, 'actions': [{
                'name': BUILD_ACTION, 'runOrder': 1,
                'actionTypeId': {'category': 'Build', 'owner': 'AWS', 'provider': 'CodeBuild', 'version': '1'},
                'configuration': {'ProjectName': PROJECT},
                'inputArtifacts': [{'name': 'SourceZip'}], 'outputArtifacts': [{'name': 'Package'}]}]},
            {'name': APPROVAL_STAGE, 'actions': [{
                'name': APPROVAL_ACTION, 'runOrder': 1,
                'actionTypeId': {'category': 'Approval', 'owner': 'AWS', 'provider': 'Manual', 'version': '1'},
                'configuration': {'CustomData': 'Check the unit test results and the release notes for release 2.'}}]},
        ],
    }


def step_pipeline() -> dict[str, Any]:
    started = time.perf_counter()
    if not _check_failing_build()[0]:
        return _result('pipeline', PIPELINE_COMMAND, {}, False, 'Find why the release 2 build failed first.', started)
    _client('s3').put_object(Bucket=BUCKET, Key=SOURCE_KEY, Body=source_zip('2'), ContentType='application/zip')
    _ensure_role(PIPELINE_ROLE, _trust('codepipeline.amazonaws.com'), 'CodePipeline service role (CI/CD lab)',
                 PIPELINE_POLICY)
    codepipeline = _client('codepipeline')
    if _pipeline():
        codepipeline.update_pipeline(pipeline=_pipeline_document())
    else:
        codepipeline.create_pipeline(pipeline=_pipeline_document(), tags=[{'key': 'Lab', 'value': LAB_KEY}])
    pipeline = _pipeline() or {}
    response = {'Pipeline': {key: pipeline.get(key) for key in ('name', 'roleArn', 'artifactStore', 'stages')},
                'SourceRelease': _source_release(), 'SourceVersions': len(_source_versions())}
    verified, message = _check_pipeline()
    return _result('pipeline', PIPELINE_COMMAND, response, verified, message, started)


def step_approve_release() -> dict[str, Any]:
    started = time.perf_counter()
    ok, problem = _check_pipeline()
    if not ok:
        return _result('approve-release', RELEASE_COMMAND, {}, False, f'Create the pipeline first: {problem}', started)
    codepipeline = _client('codepipeline')
    builds_before = set(_latest_build_ids())
    execution_id = codepipeline.start_pipeline_execution(name=PIPELINE)['pipelineExecutionId']
    token: dict[str, str] = {}

    def waiting_for_approval() -> bool:
        found = _approval_token()
        if found:
            token['value'] = found
            return True
        return (_execution(execution_id) or {}).get('status') in ('Failed', 'Stopped', 'Superseded')
    _wait(waiting_for_approval, timeout=PIPELINE_WAIT_SECONDS, delay=2)
    new_builds = [build_id for build_id in _latest_build_ids() if build_id not in builds_before]
    build = _build(new_builds[0]) if new_builds else None
    response: dict[str, Any] = {'ExecutionId': execution_id,
                                'PipelineBuild': {'Id': (build or {}).get('id'), 'Status': (build or {}).get('buildStatus')}}
    if not token.get('value'):
        execution = _execution(execution_id) or {}
        response['Execution'] = {'Status': execution.get('status')}
        return _result('approve-release', RELEASE_COMMAND, response, False,
                       f'The execution ended {execution.get("status")} before reaching the approval.', started)
    codepipeline.put_approval_result(pipelineName=PIPELINE, stageName=APPROVAL_STAGE, actionName=APPROVAL_ACTION,
                                     token=token['value'],
                                     result={'summary': 'Release 2 reviewed: unit tests pass.', 'status': 'Approved'})
    execution: dict[str, Any] = {}

    def finished() -> bool:
        execution.update(_execution(execution_id) or {})
        return execution.get('status') not in (None, 'InProgress')
    _wait(finished, timeout=60, delay=1)
    artifact = _artifact_key(build) if build else None
    response.update({'Execution': {'Status': execution.get('status'),
                                   'ArtifactRevisions': execution.get('artifactRevisions')},
                     'Approval': 'Approved', 'PackageKey': artifact})
    if execution.get('status') != 'Succeeded' or not build or build.get('buildStatus') != 'SUCCEEDED' or not artifact:
        return _result('approve-release', RELEASE_COMMAND, response, False,
                       f'The execution ended {execution.get("status")}; it needs a successful build with a package '
                       'and an approval.', started)
    if _release_in_zip(_client('s3').get_object(Bucket=BUCKET, Key=SOURCE_KEY)['Body'].read()) != '2':
        return _result('approve-release', RELEASE_COMMAND, response, False,
                       'The pipeline should have built the fixed release 2.', started)
    message = (f'Execution {execution_id} built release 2 (build {build["id"]} passed its tests), waited for the '
               f'approval, and succeeded once approved. The package is s3://{BUCKET}/{artifact}.')
    _marker('approve-release', {'ExecutionId': execution_id, 'PackageKey': artifact, 'message': message})
    verified, check = _check_release()
    return _result('approve-release', RELEASE_COMMAND, response, verified, message if verified else check, started)


def step_canary_deploy() -> dict[str, Any]:
    started = time.perf_counter()
    release = _marked('approve-release')
    if not release or not _check_release()[0] or not _check_v1()[0]:
        return _result('canary-deploy', CANARY_COMMAND, {}, False,
                       'Deploy release 1 and approve release 2 in the pipeline first.', started)
    lam, codedeploy, cloudwatch = _client('lambda'), _client('codedeploy'), _client('cloudwatch')
    alias = _alias() or {}
    if alias.get('FunctionVersion') != '1' or (alias.get('RoutingConfig') or {}).get('AdditionalVersionWeights'):
        lam.delete_alias(FunctionName=FUNCTION, Name=ALIAS)
        lam.create_alias(FunctionName=FUNCTION, Name=ALIAS, FunctionVersion='1', Description='Production traffic')
    lam.update_function_code(FunctionName=FUNCTION, S3Bucket=BUCKET, S3Key=release['PackageKey'])
    _wait_function_ready()
    target = lam.publish_version(FunctionName=FUNCTION, Description='Release 2')['Version']
    body_target, _ = _invoke(qualifier=target)
    cloudwatch.put_metric_alarm(
        AlarmName=ALARM, AlarmDescription='Quote service errors on the live alias (CI/CD lab)',
        Namespace='AWS/Lambda', MetricName='Errors', Statistic='Sum', Period=60, EvaluationPeriods=1,
        Threshold=1, ComparisonOperator='GreaterThanOrEqualToThreshold', TreatMissingData='notBreaching',
        Dimensions=[{'Name': 'FunctionName', 'Value': FUNCTION}, {'Name': 'Resource', 'Value': f'{FUNCTION}:{ALIAS}'}])
    _ensure_role(DEPLOY_ROLE, _trust('codedeploy.amazonaws.com'), 'CodeDeploy service role (CI/CD lab)')
    _client('iam').attach_role_policy(RoleName=DEPLOY_ROLE, PolicyArn=DEPLOY_POLICY_ARN)
    try:
        codedeploy.create_application(applicationName=APPLICATION, computePlatform='Lambda',
                                      tags=[{'Key': 'Lab', 'Value': LAB_KEY}])
    except ClientError as exc:
        if _code(exc) != 'ApplicationAlreadyExistsException':
            raise
    group_settings = {
        'serviceRoleArn': DEPLOY_ROLE_ARN,
        'deploymentConfigName': CANARY_CONFIG,
        'deploymentStyle': {'deploymentType': 'BLUE_GREEN', 'deploymentOption': 'WITH_TRAFFIC_CONTROL'},
        'alarmConfiguration': {'enabled': True, 'alarms': [{'name': ALARM}]},
        'autoRollbackConfiguration': {'enabled': True, 'events': ['DEPLOYMENT_FAILURE', 'DEPLOYMENT_STOP_ON_ALARM']},
    }
    if _deployment_group():
        codedeploy.update_deployment_group(applicationName=APPLICATION, currentDeploymentGroupName=DEPLOYMENT_GROUP,
                                           **group_settings)
    else:
        codedeploy.create_deployment_group(applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP,
                                           **group_settings)
    deployment = _deploy('1', target, description='Release 2: bulk discount')
    body, executed = _invoke()
    group = _deployment_group() or {}
    response = {'TargetVersion': target, 'TargetVersionTest': body_target,
                'DeploymentGroup': {key: group.get(key) for key in (
                    'deploymentConfigName', 'deploymentStyle', 'alarmConfiguration', 'autoRollbackConfiguration')},
                'Alarm': {key: (_alarm() or {}).get(key) for key in ('AlarmName', 'MetricName', 'Dimensions', 'StateValue')},
                'Deployment': deployment, 'LiveInvoke': {'Response': body, 'ExecutedVersion': executed}}
    expected = bulk_total(RELEASES['2'][0])
    if deployment['Status'] != 'Succeeded':
        return _result('canary-deploy', CANARY_COMMAND, response, False,
                       f'Deployment {deployment["DeploymentId"]} ended {deployment["Status"]}.', started)
    if body.get('release') != '2' or body.get('total') != expected:
        return _result('canary-deploy', CANARY_COMMAND, response, False,
                       f'{ALIAS} should now answer release 2 with a total of {expected}.', started)
    canary = _canary_seen(deployment['Observed'], target)
    message = (f'Deployment {deployment["DeploymentId"]} shifted {ALIAS} from version 1 to version {target}'
               + (f', holding {CANARY_PERCENT}% on version {target} during the canary' if canary else '')
               + f'; {ALIAS} now quotes {BULK_QUANTITY} items at {expected}.')
    message += (' Floci runs the canary in seconds instead of 5 minutes and stores the alarm without evaluating it.'
                if canary else ' The canary finished before the step could sample the alias weight.')
    _marker('canary-deploy', {'DeploymentId': deployment['DeploymentId'], 'TargetVersion': target, 'message': message})
    verified, check = _check_canary()
    return _result('canary-deploy', CANARY_COMMAND, response, verified, message if verified else check, started)


def step_rollback() -> dict[str, Any]:
    started = time.perf_counter()
    canary = _marked('canary-deploy')
    if not canary or not _check_canary()[0]:
        return _result('rollback', ROLLBACK_COMMAND, {}, False, 'Deploy version 2 with the canary first.', started)
    alias = _alias() or {}
    current = alias.get('FunctionVersion') or canary['TargetVersion']
    if current == '1' and not (alias.get('RoutingConfig') or {}).get('AdditionalVersionWeights'):
        deployment = {'Status': 'Succeeded', 'Note': f'{ALIAS} already points at version 1.'}
    else:
        deployment = _deploy(current, '1', config=ROLLBACK_CONFIG, description='Roll back to release 1')
    body, executed = _invoke()
    alias = _alias() or {}
    response = {'Deployment': deployment,
                'Alias': {key: alias.get(key) for key in ('Name', 'FunctionVersion', 'RoutingConfig')},
                'LiveInvoke': {'Response': body, 'ExecutedVersion': executed}}
    expected = bulk_total(RELEASES['1'][0])
    if deployment.get('Status') != 'Succeeded' or body.get('release') != '1' or body.get('total') != expected:
        return _result('rollback', ROLLBACK_COMMAND, response, False,
                       f'{ALIAS} should answer release 1 again (total {expected}).', started)
    message = (f'{ROLLBACK_CONFIG} moved {ALIAS} back to version 1 at once; it quotes {BULK_QUANTITY} items at '
               f'{expected} again, and version {canary["TargetVersion"]} stays published for a fixed release.')
    _marker('rollback', {'message': message})
    verified, check = _check_rollback()
    return _result('rollback', ROLLBACK_COMMAND, response, verified, message if verified else check, started)


RUNNERS = {
    'source-bucket': step_source_bucket,
    'build-project': step_build_project,
    'first-build': step_first_build,
    'deploy-v1': step_deploy_v1,
    'failing-build': step_failing_build,
    'pipeline': step_pipeline,
    'approve-release': step_approve_release,
    'canary-deploy': step_canary_deploy,
    'rollback': step_rollback,
}


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key != SERVICE or lab_key != LAB_KEY:
        raise ValueError('Lab not found')
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


def status(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    checks = {
        'source-bucket': lambda: _check_bucket()[0],
        'build-project': lambda: _check_project()[0],
        'first-build': lambda: _check_first_build()[0],
        'deploy-v1': lambda: _check_v1()[0],
        'failing-build': lambda: _check_failing_build()[0],
        'pipeline': lambda: _check_pipeline()[0],
        'approve-release': lambda: _check_release()[0],
        'canary-deploy': lambda: _check_canary()[0],
        'rollback': lambda: _check_rollback()[0],
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


def reset(service_key: str = SERVICE, lab_key: str = LAB_KEY) -> dict[str, Any]:
    """Remove the deployment group and application, pipeline, project, function, alarm, roles, and bucket."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            code = _code(exc)
            if 'NotFound' in code or 'DoesNotExist' in code or code in ('NoSuchEntity', 'NoSuchBucket'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')

    codedeploy, codepipeline, codebuild = _client('codedeploy'), _client('codepipeline'), _client('codebuild')
    lam, iam, s3, cloudwatch = _client('lambda'), _client('iam'), _client('s3'), _client('cloudwatch')
    attempt(f'deployment-group:{DEPLOYMENT_GROUP}', lambda: codedeploy.delete_deployment_group(
        applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP))
    attempt(f'application:{APPLICATION}', lambda: codedeploy.delete_application(applicationName=APPLICATION))
    attempt(f'pipeline:{PIPELINE}', lambda: codepipeline.delete_pipeline(name=PIPELINE))
    attempt(f'project:{PROJECT}', lambda: codebuild.delete_project(name=PROJECT))
    attempt(f'function:{FUNCTION}', lambda: lam.delete_function(FunctionName=FUNCTION))
    try:
        if _alarm():
            attempt(f'alarm:{ALARM}', lambda: cloudwatch.delete_alarms(AlarmNames=[ALARM]))
    except (ClientError, BotoCoreError) as exc:
        errors.append(f'alarm: {exc}')
    for role in (BUILD_ROLE, PIPELINE_ROLE, LAMBDA_ROLE):
        attempt(f'role-policy:{role}', lambda role=role: iam.delete_role_policy(RoleName=role, PolicyName=INLINE_POLICY))
    attempt(f'role-policy:{DEPLOY_ROLE}', lambda: iam.detach_role_policy(RoleName=DEPLOY_ROLE, PolicyArn=DEPLOY_POLICY_ARN))
    for role in (BUILD_ROLE, PIPELINE_ROLE, LAMBDA_ROLE, DEPLOY_ROLE):
        attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))
    try:
        listing = s3.list_object_versions(Bucket=BUCKET)
        objects = [{'Key': item['Key'], 'VersionId': item['VersionId']}
                   for item in listing.get('Versions', []) + listing.get('DeleteMarkers', [])]
        if objects:
            attempt(f'objects:{len(objects)}', lambda: s3.delete_objects(Bucket=BUCKET, Delete={'Objects': objects}))
        attempt(f'bucket:{BUCKET}', lambda: s3.delete_bucket(Bucket=BUCKET))
    except ClientError as exc:
        if _code(exc) != 'NoSuchBucket':
            errors.append(f'bucket: {exc}')
    except BotoCoreError as exc:
        errors.append(f'bucket: {exc}')

    cache.delete_many([CACHE_PREFIX + step for step in STEP_KEYS])
    payload = {'status': 'reset', 'deleted_resources': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws deploy delete-deployment-group --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP}\n'
            f'aws deploy delete-application --application-name {APPLICATION}\n'
            f'aws codepipeline delete-pipeline --name {PIPELINE}\n'
            f'aws codebuild delete-project --name {PROJECT}\n'
            f'aws lambda delete-function --function-name {FUNCTION}\n'
            f'aws cloudwatch delete-alarms --alarm-names {ALARM}\n'
            f'aws iam delete-role --role-name {BUILD_ROLE}   # and the pipeline, Lambda, and CodeDeploy roles\n'
            f'aws s3 rb s3://{BUCKET} --force   # after deleting every object version'
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
            else 'The deployment group, application, pipeline, project, function, alarm, roles, and bucket are deleted.',
        },
    }
