"""Container blue/green lab: build a release pipeline for a containerized service. Create an ECR repository with
immutable tags, push two releases through the ECR API, preview a lifecycle policy, put an Application Load Balancer
with blue and green target groups in front of an ECS service that CodeDeploy controls, shift production traffic to
release 2 with a canary and a smoke-test hook, and watch a failed hook stop release 3.

Floci 2.4.0 stores images in a real registry and rejects a push that would overwrite an immutable tag. CodeDeploy
creates the green task set, runs the Lambda hook, moves the production listener 10% and then 100% to the green target
group, and deletes the old task set. Floci does not scan images, does not apply lifecycle policies, does not list untagged
images, ignores the test listener and the AfterAllowTestTraffic hook, and does not roll back a failed deployment.
The lab checks those rules itself and says so in each step.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import tarfile
import time
import zipfile
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from django.core.cache import cache

from dashboard.aws import FlociClientFactory, _clean_response

SERVICE = 'ecs'
LAB_KEY = 'container-blue-green'
CACHE_PREFIX = f'floci-lab:{SERVICE}:{LAB_KEY}:'
REGION = 'us-east-1'
ACCOUNT = '000000000000'

REPOSITORY = 'lab-storefront'
RELEASE_1 = 'release-1'
RELEASE_2 = 'release-2'
RELEASE_3 = 'release-3'  # never pushed, so the smoke test fails it
RELEASE_PREFIX = 'release-'

CLUSTER = 'lab-storefront-cluster'
SERVICE_NAME = 'lab-storefront'
FAMILY = 'lab-storefront'
CONTAINER = 'web'
CONTAINER_PORT = 80
DESIRED_COUNT = 2

LOAD_BALANCER = 'lab-storefront-alb'
BLUE_TG = 'lab-storefront-blue'
GREEN_TG = 'lab-storefront-green'
PROD_PORT = 80
TEST_PORT = 8080
HEALTH_PATH = '/health'

APPLICATION = 'lab-storefront'
DEPLOYMENT_GROUP = 'lab-storefront-bluegreen'
CODEDEPLOY_ROLE = 'lab-storefront-codedeploy'
CODEDEPLOY_POLICY_ARN = 'arn:aws:iam::aws:policy/AWSCodeDeployRoleForECS'
ALARM = 'lab-storefront-5xx'
CANARY_CONFIG = 'CodeDeployDefault.ECSCanary10Percent5Minutes'
TERMINATION_WAIT_MINUTES = 5

HOOK_FUNCTION = 'lab-storefront-smoke-test'
HOOK_ROLE = 'lab-storefront-smoke-test-role'
HOOK_ROLE_ARN = f'arn:aws:iam::{ACCOUNT}:role/{HOOK_ROLE}'
HOOK_EVENT = 'BeforeAllowTraffic'

LAB_TAG = {'Key': 'Lab', 'Value': LAB_KEY}
UNTAGGED_KEEP = 1
RELEASES_KEEP = 10

LIFECYCLE_POLICY = {
    'rules': [
        {
            'rulePriority': 1,
            'description': 'Keep only the newest untagged build',
            'selection': {'tagStatus': 'untagged', 'countType': 'imageCountMoreThan', 'countNumber': UNTAGGED_KEEP},
            'action': {'type': 'expire'},
        },
        {
            'rulePriority': 2,
            'description': f'Keep the {RELEASES_KEEP} newest releases',
            'selection': {'tagStatus': 'tagged', 'tagPrefixList': [RELEASE_PREFIX],
                          'countType': 'imageCountMoreThan', 'countNumber': RELEASES_KEEP},
            'action': {'type': 'expire'},
        },
    ],
}

LAMBDA_TRUST = {'Version': '2012-10-17', 'Statement': [{
    'Effect': 'Allow', 'Principal': {'Service': 'lambda.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
CODEDEPLOY_TRUST = {'Version': '2012-10-17', 'Statement': [{
    'Effect': 'Allow', 'Principal': {'Service': 'codedeploy.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}
HOOK_POLICY = {'Version': '2012-10-17', 'Statement': [{
    'Effect': 'Allow',
    'Action': ['codedeploy:GetDeployment', 'codedeploy:PutLifecycleEventHookExecutionStatus',
               'ecs:DescribeTaskDefinition', 'ecr:DescribeImages', 'logs:CreateLogGroup', 'logs:CreateLogStream',
               'logs:PutLogEvents'],
    'Resource': '*'}]}

# The smoke test reads the task definition the deployment is shipping and refuses it unless its image exists in
# ECR. On AWS this hook would usually run on AfterAllowTestTraffic and call the test listener.
HOOK_SOURCE = '''import json

import boto3


def handler(event, context):
    deployment_id = event['DeploymentId']
    execution_id = event['LifecycleEventHookExecutionId']
    codedeploy = boto3.client('codedeploy')
    status, reason = 'Failed', ''
    try:
        revision = codedeploy.get_deployment(deploymentId=deployment_id)['deploymentInfo']['revision']
        spec = json.loads(revision['appSpecContent']['content'])
        task_definition = spec['Resources'][0]['TargetService']['Properties']['TaskDefinition']
        definition = boto3.client('ecs').describe_task_definition(taskDefinition=task_definition)['taskDefinition']
        image = definition['containerDefinitions'][0]['image']
        repository_uri, tag = image.rsplit(':', 1)
        repository = repository_uri.split('/', 1)[1]
        boto3.client('ecr').describe_images(repositoryName=repository, imageIds=[{'imageTag': tag}])
        status, reason = 'Succeeded', image + ' exists in ECR'
    except Exception as exc:
        reason = str(exc)
    print(json.dumps({'deploymentId': deployment_id, 'status': status, 'reason': reason}))
    codedeploy.put_lifecycle_event_hook_execution_status(
        deploymentId=deployment_id, lifecycleEventHookExecutionId=execution_id, status=status)
    return {'status': status, 'reason': reason}
'''


# ── Pure logic ───────────────────────────────────────────────────────────────

def _pushed_at(image: dict[str, Any]) -> float:
    value = image.get('imagePushedAt')
    if hasattr(value, 'timestamp'):
        return value.timestamp()
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _rule_matches(selection: dict[str, Any], image: dict[str, Any]) -> bool:
    tags = image.get('imageTags') or []
    status = selection.get('tagStatus', 'any')
    if status == 'untagged':
        return not tags
    if status == 'tagged':
        prefixes = selection.get('tagPrefixList') or []
        return bool(tags) and (not prefixes or any(tag.startswith(prefix) for tag in tags for prefix in prefixes))
    return True


def lifecycle_preview(policy: dict[str, Any], images: list[dict[str, Any]], now: float | None = None,
                      protected: set[str] | None = None) -> list[dict[str, Any]]:
    """Evaluate an ECR lifecycle policy the way ECR does: rules run in priority order, each image is claimed by the
    first rule that selects it, imageCountMoreThan keeps the newest N, and sinceImagePushed expires images older than
    N days. Images in `protected` (tags or digests a task definition still uses) are reported as blocked."""
    now = time.time() if now is None else now
    protected = protected or set()
    decisions: dict[str, dict[str, Any]] = {}
    for rule in sorted(policy.get('rules', []), key=lambda item: item.get('rulePriority', 0)):
        selection = rule.get('selection', {})
        matched = [image for image in images
                   if image['imageDigest'] not in decisions and _rule_matches(selection, image)]
        matched.sort(key=_pushed_at, reverse=True)
        if selection.get('countType') == 'imageCountMoreThan':
            expired = matched[int(selection.get('countNumber', 0)):]
        else:
            days = int(selection.get('countNumber', 0))
            expired = [image for image in matched if now - _pushed_at(image) > days * 86400]
        for image in matched:
            decisions[image['imageDigest']] = {
                'imageDigest': image['imageDigest'],
                'imageTags': image.get('imageTags') or [],
                'rule': rule.get('rulePriority'),
                'action': 'expire' if image in expired else 'keep',
            }
    for image in images:
        decisions.setdefault(image['imageDigest'], {
            'imageDigest': image['imageDigest'], 'imageTags': image.get('imageTags') or [], 'rule': None,
            'action': 'keep'})
    rows = sorted(decisions.values(), key=lambda row: (row['action'] != 'expire', row['imageTags'], row['imageDigest']))
    for row in rows:
        if row['action'] == 'expire' and ({row['imageDigest'], *row['imageTags']} & protected):
            row['action'] = 'blocked'
    return rows


def forward_weights(actions: list[dict[str, Any]]) -> dict[str, int]:
    """Read a listener rule's forward action as {target group name: weight}."""
    forward = next((action for action in actions or [] if action.get('Type') == 'forward'), None)
    if not forward:
        return {}
    groups = (forward.get('ForwardConfig') or {}).get('TargetGroups') or []
    if groups:
        return {_tg_name(group['TargetGroupArn']): int(group.get('Weight', 1)) for group in groups}
    arn = forward.get('TargetGroupArn')
    return {_tg_name(arn): 100} if arn else {}


def serving(weights: dict[str, int]) -> str | None:
    """The target group that gets all the traffic, or None while traffic is split."""
    live = {name: weight for name, weight in weights.items() if weight}
    return next(iter(live)) if len(live) == 1 else None


def _tg_name(arn: str) -> str:
    parts = (arn or '').split('/')
    return parts[-2] if len(parts) >= 3 else arn


def image_tag(image: str) -> str:
    return image.rsplit(':', 1)[-1] if ':' in image.rsplit('/', 1)[-1] else 'latest'


def appspec(task_definition_arn: str) -> dict[str, Any]:
    return {
        'version': 0.0,
        'Resources': [{'TargetService': {'Type': 'AWS::ECS::Service', 'Properties': {
            'TaskDefinition': task_definition_arn,
            'LoadBalancerInfo': {'ContainerName': CONTAINER, 'ContainerPort': CONTAINER_PORT},
        }}}],
        'Hooks': [{HOOK_EVENT: HOOK_FUNCTION}],
    }


def build_image(label: str) -> tuple[bytes, bytes, bytes]:
    """A tiny OCI image: one layer holding /app/version.txt, a config, and its manifest's raw parts."""
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode='w') as archive:
        body = f'{label}\n'.encode()
        info = tarfile.TarInfo('app/version.txt')
        info.size = len(body)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(body))
    raw = tar_buffer.getvalue()
    layer = gzip.compress(raw, mtime=0)
    config = json.dumps({
        'architecture': 'amd64', 'os': 'linux',
        'config': {'Labels': {'org.opencontainers.image.version': label}, 'ExposedPorts': {'80/tcp': {}}},
        'rootfs': {'type': 'layers', 'diff_ids': [_digest(raw)]},
    }, sort_keys=True).encode()
    return raw, layer, config


def _digest(data: bytes) -> str:
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def manifest(layer: bytes, config: bytes) -> str:
    return json.dumps({
        'schemaVersion': 2,
        'mediaType': 'application/vnd.oci.image.manifest.v1+json',
        'config': {'mediaType': 'application/vnd.oci.image.config.v1+json', 'digest': _digest(config),
                   'size': len(config)},
        'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+gzip', 'digest': _digest(layer),
                    'size': len(layer)}],
    }, sort_keys=True)


# ── Commands ─────────────────────────────────────────────────────────────────

REPOSITORY_COMMAND = (
    f'aws ecr create-repository --repository-name {REPOSITORY} --image-tag-mutability IMMUTABLE '
    '--image-scanning-configuration scanOnPush=true\n'
    f'aws ecr put-lifecycle-policy --repository-name {REPOSITORY} --lifecycle-policy-text file://lifecycle.json'
)
PUSH_COMMAND = (
    f'docker build -t {REPOSITORY}:{RELEASE_1} . && docker push <registry>/{REPOSITORY}:{RELEASE_1}\n'
    f'docker build -t {REPOSITORY}:{RELEASE_2} . && docker push <registry>/{REPOSITORY}:{RELEASE_2}\n'
    f'# the lab pushes through the API: initiate-layer-upload, upload-layer-part, complete-layer-upload, put-image\n'
    f'aws ecr put-image --repository-name {REPOSITORY} --image-tag {RELEASE_1} --image-manifest file://changed.json'
)
LIFECYCLE_COMMAND = (
    f'aws ecr put-image --repository-name {REPOSITORY} --image-digest <scratch-build-digest> '
    '--image-manifest file://scratch.json   # two untagged scratch builds\n'
    f'aws ecr start-lifecycle-policy-preview --repository-name {REPOSITORY}\n'
    f'aws ecr get-lifecycle-policy-preview --repository-name {REPOSITORY}'
)
LOAD_BALANCER_COMMAND = (
    f'aws elbv2 create-load-balancer --name {LOAD_BALANCER} --type application --subnets <subnet-a> <subnet-b>\n'
    f'aws elbv2 create-target-group --name {BLUE_TG} --protocol HTTP --port {CONTAINER_PORT} --target-type ip '
    f'--vpc-id <vpc> --health-check-path {HEALTH_PATH}\n'
    f'aws elbv2 create-target-group --name {GREEN_TG} --protocol HTTP --port {CONTAINER_PORT} --target-type ip '
    f'--vpc-id <vpc> --health-check-path {HEALTH_PATH}\n'
    f'aws elbv2 create-listener --load-balancer-arn <alb> --protocol HTTP --port {PROD_PORT} '
    f'--default-actions Type=forward,TargetGroupArn=<{BLUE_TG}>\n'
    f'aws elbv2 create-listener --load-balancer-arn <alb> --protocol HTTP --port {TEST_PORT} '
    f'--default-actions Type=forward,TargetGroupArn=<{GREEN_TG}>'
)
SERVICE_COMMAND = (
    f'aws ecs create-cluster --cluster-name {CLUSTER}\n'
    f'aws ecs register-task-definition --family {FAMILY} --network-mode awsvpc --requires-compatibilities FARGATE '
    f'--cpu 256 --memory 512 --container-definitions \'[{{"name":"{CONTAINER}","image":"<repo-uri>:{RELEASE_1}",'
    f'"portMappings":[{{"containerPort":{CONTAINER_PORT}}}]}}]\'\n'
    f'aws ecs create-service --cluster {CLUSTER} --service-name {SERVICE_NAME} --task-definition {FAMILY}:1 '
    f'--desired-count {DESIRED_COUNT} --launch-type FARGATE --deployment-controller type=CODE_DEPLOY '
    f'--load-balancers targetGroupArn=<{BLUE_TG}>,containerName={CONTAINER},containerPort={CONTAINER_PORT} '
    '--network-configuration awsvpcConfiguration={subnets=[<subnet-a>,<subnet-b>]}'
)
DEPLOYMENT_GROUP_COMMAND = (
    f'aws cloudwatch put-metric-alarm --alarm-name {ALARM} --namespace AWS/ApplicationELB '
    '--metric-name HTTPCode_Target_5XX_Count --statistic Sum --period 60 --evaluation-periods 1 --threshold 5 '
    '--comparison-operator GreaterThanThreshold\n'
    f'aws deploy create-application --application-name {APPLICATION} --compute-platform ECS\n'
    f'aws deploy create-deployment-group --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    f'--service-role-arn <{CODEDEPLOY_ROLE}> --deployment-config-name {CANARY_CONFIG} '
    '--deployment-style deploymentType=BLUE_GREEN,deploymentOption=WITH_TRAFFIC_CONTROL '
    f'--ecs-services clusterName={CLUSTER},serviceName={SERVICE_NAME} '
    '--load-balancer-info file://target-group-pair.json '
    '--blue-green-deployment-configuration file://blue-green.json '
    '--auto-rollback-configuration enabled=true,events=DEPLOYMENT_FAILURE,DEPLOYMENT_STOP_ON_ALARM '
    f'--alarm-configuration enabled=true,alarms=[{{name={ALARM}}}]'
)
DEPLOY_COMMAND = (
    f'aws lambda create-function --function-name {HOOK_FUNCTION} --runtime python3.12 --handler index.handler '
    f'--role <{HOOK_ROLE}> --zip-file fileb://smoke-test.zip\n'
    f'aws ecs register-task-definition --family {FAMILY} ... "image":"<repo-uri>:{RELEASE_2}"\n'
    f'aws deploy create-deployment --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    '--revision \'{"revisionType":"AppSpecContent","appSpecContent":{"content":"<appspec.json>"}}\'\n'
    'aws elbv2 describe-rules --listener-arn <prod-listener>   # 90/10, then all green'
)
FAILED_COMMAND = (
    f'aws ecs register-task-definition --family {FAMILY} ... "image":"<repo-uri>:{RELEASE_3}"\n'
    f'aws deploy create-deployment --application-name {APPLICATION} --deployment-group-name {DEPLOYMENT_GROUP} '
    '--revision ...\n'
    'aws deploy get-deployment --deployment-id <id>   # Failed: HOOK_EXECUTION_FAILURE\n'
    f'aws ecs delete-task-set --cluster {CLUSTER} --service {SERVICE_NAME} --task-set <failed-green-task-set> --force'
)

FLOCI = 'How Floci emulates this: '

LAB = {
    'service': SERVICE,
    'key': LAB_KEY,
    'title': 'Ship a container with an ECS blue/green deployment through CodeDeploy',
    'description': (
        'Build the release path for a containerized storefront: an ECR repository with immutable tags and a lifecycle '
        'policy, two releases pushed to it, an Application Load Balancer with blue and green target groups, an ECS '
        'service that CodeDeploy controls, a canary deployment gated by a smoke-test hook, and a bad release that the '
        'hook stops before it takes traffic.'
    ),
    'steps': [
        {
            'key': 'create-repository',
            'title': 'Create an ECR repository with immutable tags and a lifecycle policy',
            'command': REPOSITORY_COMMAND,
            'explanation': (
                f'Immutable tags mean {RELEASE_1} always names the same image, so a task definition that says '
                f'{RELEASE_1} deploys exactly what was tested. Scan on push checks every new image for known '
                f'vulnerabilities. The lifecycle policy keeps only the newest untagged build and the {RELEASES_KEEP} '
                'newest releases, so old images do not pile up. '
                + FLOCI + 'the repository is backed by a real registry and tag immutability is enforced. Scan on '
                'push and the lifecycle policy are stored only: Floci never scans an image and never expires one.'
            ),
        },
        {
            'key': 'push-images',
            'title': 'Push two releases and try to overwrite one',
            'command': PUSH_COMMAND,
            'explanation': (
                f'The lab pushes {RELEASE_1} and {RELEASE_2} through the ECR layer API, the same calls docker push '
                f'makes. Then it tries to push a different image as {RELEASE_1}. ECR refuses with '
                'ImageTagAlreadyExistsException, which is what stops a pipeline from silently replacing a release. '
                + FLOCI + 'the layers and manifests land in Floci\'s registry, and the overwrite is rejected for '
                'real. There are no scan findings, because Floci does not scan.'
            ),
        },
        {
            'key': 'preview-lifecycle',
            'title': 'Preview which images the lifecycle policy would expire',
            'command': LIFECYCLE_COMMAND,
            'explanation': (
                'CI pushes untagged scratch builds that nobody deploys. The lab pushes two of them and previews the '
                'policy: rules run in priority order, each image is claimed by the first rule that selects it, and '
                'imageCountMoreThan keeps the newest N. The older scratch build would expire and every release '
                'stays. A lifecycle rule should never expire an image a running task definition uses, so the '
                'preview flags that case as blocked. '
                + FLOCI + 'ECR lifecycle previews are not available, so the lab evaluates the stored policy against '
                'the repository\'s images itself. Floci lists tagged images only, so the lab looks the untagged '
                'builds up by digest.'
            ),
        },
        {
            'key': 'create-load-balancer',
            'title': 'Create a load balancer with blue and green target groups',
            'command': LOAD_BALANCER_COMMAND,
            'explanation': (
                f'A blue/green deployment needs two target groups. The production listener on port {PROD_PORT} '
                f'sends traffic to {BLUE_TG}, where the current release runs. The test listener on port {TEST_PORT} '
                f'points at {GREEN_TG}, so a hook can test a new release before customers see it. '
                + FLOCI + 'the load balancer, target groups, and listeners are stored as on AWS. No targets are '
                'registered, because the lab\'s placeholder image never starts a task.'
            ),
        },
        {
            'key': 'create-service',
            'title': 'Create an ECS service that CodeDeploy controls',
            'command': SERVICE_COMMAND,
            'explanation': (
                f'With the CODE_DEPLOY deployment controller, ECS no longer rolls out new task definitions itself. '
                f'Every release goes through a CodeDeploy deployment, which starts a new (green) task set next to the '
                f'current one. The service starts on {RELEASE_1} behind {BLUE_TG}. '
                + FLOCI + 'the service and its deployment controller are stored as on AWS, without a first task set. '
                'Floci tries to start the service\'s tasks in Docker, but the lab\'s image is a tiny placeholder '
                'with no program in it, so they stop with TaskFailedToStart. The lab checks the service settings.'
            ),
        },
        {
            'key': 'create-deployment-group',
            'title': 'Create a CodeDeploy blue/green deployment group',
            'command': DEPLOYMENT_GROUP_COMMAND,
            'explanation': (
                f'The deployment group ties CodeDeploy to the service, names the target group pair and the production '
                f'and test listeners, and uses {CANARY_CONFIG}: 10% of production traffic goes to green first, then '
                f'the rest. Automatic rollback is on for failed deployments and for the {ALARM} alarm, and the old '
                f'task set is kept for {TERMINATION_WAIT_MINUTES} minutes after success so a rollback is instant. '
                + FLOCI + 'the deployment group and its settings are stored as on AWS. Floci never watches the alarm '
                'and never rolls a deployment back.'
            ),
        },
        {
            'key': 'deploy-canary',
            'title': f'Ship {RELEASE_2} with a canary and a smoke-test hook',
            'command': DEPLOY_COMMAND,
            'explanation': (
                f'The lab creates the {HOOK_FUNCTION} function, registers a task definition for {RELEASE_2}, and '
                f'starts a deployment. CodeDeploy starts the green task set, runs the smoke test on {HOOK_EVENT}, '
                'moves 10% of production traffic to green, and then moves the rest and removes the old task set. The '
                'smoke test checks that the image the deployment ships exists in ECR and reports the result with '
                'PutLifecycleEventHookExecutionStatus. '
                + FLOCI + 'this runs for real: the hook Lambda runs, the listener rule goes to 90/10 and then to '
                'green, and the green task set becomes primary. The canary wait is about 5 seconds instead of 5 '
                'minutes. Floci ignores the test listener and the AfterAllowTestTraffic hook, so the smoke test runs '
                f'on {HOOK_EVENT}.'
            ),
        },
        {
            'key': 'failed-hook',
            'title': f'Watch the smoke test stop {RELEASE_3}',
            'command': FAILED_COMMAND,
            'explanation': (
                f'{RELEASE_3} was never pushed, so the smoke test cannot find its image and reports Failed. CodeDeploy '
                f'stops the deployment with HOOK_EXECUTION_FAILURE before any production traffic moves, and '
                f'{RELEASE_2} keeps serving. On AWS, automatic rollback also removes the failed green task set. '
                + FLOCI + 'the hook failure stops the deployment for real and traffic does not move. Floci does not '
                'roll back, so it leaves the failed task set behind, and the lab deletes it the way a rollback would.'
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


def _not_found(exc: ClientError) -> bool:
    code = _code(exc)
    return 'NotFound' in code or 'NoSuch' in code or 'DoesNotExist' in code or code in (
        'ResourceNotFoundException', 'ClusterNotFoundException', 'ServiceNotFoundException',
        'RepositoryNotFoundException', 'ImageNotFoundException', 'ValidationError')


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


# ── Lookups ──────────────────────────────────────────────────────────────────

def _repository() -> dict[str, Any] | None:
    try:
        repositories = _client('ecr').describe_repositories(repositoryNames=[REPOSITORY]).get('repositories', [])
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    return repositories[0] if repositories else None


def _images() -> list[dict[str, Any]]:
    try:
        return _client('ecr').describe_images(repositoryName=REPOSITORY).get('imageDetails', [])
    except ClientError as exc:
        if _not_found(exc):
            return []
        raise


def _image(tag: str) -> dict[str, Any] | None:
    return next((image for image in _images() if tag in (image.get('imageTags') or [])), None)


def _with_untagged(digests: list[str]) -> list[dict[str, Any]]:
    """Tagged images plus the untagged ones the lab pushed. Floci's DescribeImages and ListImages enumerate tags
    only, so untagged images are looked up by digest."""
    images = _images()
    known = {image['imageDigest'] for image in images}
    wanted = [{'imageDigest': digest} for digest in digests if digest not in known]
    if wanted:
        images += _safe_call(lambda: _client('ecr').describe_images(
            repositoryName=REPOSITORY, imageIds=wanted).get('imageDetails', []), []) or []
    return images


def _lifecycle_policy() -> dict[str, Any] | None:
    try:
        text = _client('ecr').get_lifecycle_policy(repositoryName=REPOSITORY).get('lifecyclePolicyText')
    except ClientError as exc:
        if _not_found(exc) or 'LifecyclePolicyNotFound' in _code(exc):
            return None
        raise
    return json.loads(text) if text else None


def _load_balancer() -> dict[str, Any] | None:
    try:
        balancers = _client('elbv2').describe_load_balancers(Names=[LOAD_BALANCER]).get('LoadBalancers', [])
    except ClientError as exc:
        if _not_found(exc) or 'LoadBalancerNotFound' in _code(exc):
            return None
        raise
    return balancers[0] if balancers else None


def _target_group(name: str) -> dict[str, Any] | None:
    try:
        groups = _client('elbv2').describe_target_groups(Names=[name]).get('TargetGroups', [])
    except ClientError as exc:
        if _not_found(exc) or 'TargetGroupNotFound' in _code(exc):
            return None
        raise
    return groups[0] if groups else None


def _listeners() -> dict[int, dict[str, Any]]:
    balancer = _load_balancer()
    if not balancer:
        return {}
    listeners = _client('elbv2').describe_listeners(LoadBalancerArn=balancer['LoadBalancerArn']).get('Listeners', [])
    return {listener['Port']: listener for listener in listeners}


def _prod_weights() -> dict[str, int]:
    """Where the production listener sends traffic. Floci moves traffic by rewriting the listener's default rule, so
    read DescribeRules rather than the listener's DefaultActions."""
    listener = _listeners().get(PROD_PORT)
    if not listener:
        return {}
    rules = _client('elbv2').describe_rules(ListenerArn=listener['ListenerArn']).get('Rules', [])
    default = next((rule for rule in rules if rule.get('IsDefault')), None)
    return forward_weights(default['Actions'] if default else listener.get('DefaultActions', []))


def _service() -> dict[str, Any] | None:
    try:
        services = _client('ecs').describe_services(cluster=CLUSTER, services=[SERVICE_NAME]).get('services', [])
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise
    live = [item for item in services if item.get('status') == 'ACTIVE']
    return live[0] if live else None


def _task_sets() -> list[dict[str, Any]]:
    try:
        return _client('ecs').describe_task_sets(cluster=CLUSTER, service=SERVICE_NAME).get('taskSets', [])
    except ClientError as exc:
        if _not_found(exc):
            return []
        raise


def _primary_task_set() -> dict[str, Any] | None:
    return next((item for item in _task_sets() if item.get('status') == 'PRIMARY'), None)


def _task_definition_image(arn: str) -> str:
    definition = _client('ecs').describe_task_definition(taskDefinition=arn)['taskDefinition']
    return definition['containerDefinitions'][0]['image']


def _deployment_group() -> dict[str, Any] | None:
    try:
        return _client('codedeploy').get_deployment_group(
            applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP).get('deploymentGroupInfo')
    except ClientError as exc:
        if _not_found(exc):
            return None
        raise


def _deployment(deployment_id: str) -> dict[str, Any] | None:
    return _safe_call(lambda: _client('codedeploy').get_deployment(deploymentId=deployment_id)['deploymentInfo'])


def _role_exists(name: str) -> bool:
    try:
        _client('iam').get_role(RoleName=name)
        return True
    except ClientError as exc:
        if _code(exc) == 'NoSuchEntity':
            return False
        raise


def _ensure_role(name: str, trust: dict[str, Any], description: str, policy: dict[str, Any] | None = None,
                 managed: str | None = None) -> None:
    iam = _client('iam')
    if not _role_exists(name):
        iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust), Description=description,
                        Tags=[LAB_TAG])
    if policy:
        iam.put_role_policy(RoleName=name, PolicyName=f'{name}-policy', PolicyDocument=json.dumps(policy))
    if managed:
        iam.attach_role_policy(RoleName=name, PolicyArn=managed)


def _network() -> tuple[str, list[str]]:
    ec2 = _client('ec2')
    vpcs = ec2.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}]).get('Vpcs', [])
    if not vpcs:
        vpcs = ec2.describe_vpcs().get('Vpcs', [])
    vpc_id = vpcs[0]['VpcId']
    subnets = ec2.describe_subnets(Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}]).get('Subnets', [])
    subnets.sort(key=lambda item: item.get('AvailabilityZone', ''))
    return vpc_id, [item['SubnetId'] for item in subnets[:2]]


# ── Image pushes ─────────────────────────────────────────────────────────────

def _upload_blob(data: bytes) -> None:
    ecr = _client('ecr')
    digest = _digest(data)
    available = ecr.batch_check_layer_availability(repositoryName=REPOSITORY, layerDigests=[digest]).get('layers', [])
    if any(layer.get('layerAvailability') == 'AVAILABLE' for layer in available):
        return
    upload = ecr.initiate_layer_upload(repositoryName=REPOSITORY)
    ecr.upload_layer_part(repositoryName=REPOSITORY, uploadId=upload['uploadId'], partFirstByte=0,
                          partLastByte=len(data) - 1, layerPartBlob=data)
    try:
        ecr.complete_layer_upload(repositoryName=REPOSITORY, uploadId=upload['uploadId'], layerDigests=[digest])
    except ClientError as exc:
        if _code(exc) != 'LayerAlreadyExistsException':
            raise


def _push(label: str, tag: str | None) -> dict[str, Any]:
    """Push a tiny image under `tag`, or untagged when tag is None. Returns the image id or the error code."""
    _raw, layer, config = build_image(label)
    _upload_blob(layer)
    _upload_blob(config)
    body = manifest(layer, config)
    kwargs: dict[str, Any] = {'repositoryName': REPOSITORY, 'imageManifest': body,
                              'imageManifestMediaType': 'application/vnd.oci.image.manifest.v1+json'}
    if tag:
        kwargs['imageTag'] = tag
    else:
        kwargs['imageDigest'] = _digest(body.encode())
    try:
        return {'imageId': _client('ecr').put_image(**kwargs)['image']['imageId']}
    except ClientError as exc:
        return {'error': _code(exc), 'message': exc.response.get('Error', {}).get('Message', '')}


# ── Checks ───────────────────────────────────────────────────────────────────

def _check_repository() -> tuple[bool, str]:
    repository = _repository()
    if not repository:
        return False, f'Create the {REPOSITORY} repository.'
    problems = []
    if repository.get('imageTagMutability') != 'IMMUTABLE':
        problems.append('tags are mutable')
    if not (repository.get('imageScanningConfiguration') or {}).get('scanOnPush'):
        problems.append('scan on push is off')
    if _lifecycle_policy() != LIFECYCLE_POLICY:
        problems.append('the lifecycle policy is missing or different')
    if problems:
        return False, f'{REPOSITORY}: ' + ', '.join(problems) + '.'
    return True, f'{REPOSITORY} has immutable tags, scan on push, and the lifecycle policy.'


def _check_push() -> tuple[bool, str]:
    ok, message = _check_repository()
    if not ok:
        return False, message
    missing = [tag for tag in (RELEASE_1, RELEASE_2) if not _image(tag)]
    if missing:
        return False, 'Push ' + ' and '.join(missing) + '.'
    attempt = _marked('push-images') or {}
    if attempt.get('overwrite', {}).get('error') != 'ImageTagAlreadyExistsException':
        return False, f'Try to overwrite {RELEASE_1} to see the immutable tag refuse it.'
    if attempt.get('release1') and attempt['release1'] != _image(RELEASE_1)['imageDigest']:
        return False, f'{RELEASE_1} now points at a different image.'
    return True, f'{RELEASE_1} and {RELEASE_2} are in ECR, and overwriting {RELEASE_1} was refused.'


def _protected_images() -> set[str]:
    """Image tags that a registered task definition for the service still uses."""
    protected = set()
    arns = _safe_call(lambda: _client('ecs').list_task_definitions(familyPrefix=FAMILY, status='ACTIVE').get(
        'taskDefinitionArns', []), []) or []
    for arn in arns:
        image = _safe_call(lambda arn=arn: _task_definition_image(arn))
        if image and image.split('/', 1)[-1].startswith(f'{REPOSITORY}:'):
            protected.add(image_tag(image))
    return protected


def _check_lifecycle() -> tuple[bool, str]:
    ok, message = _check_push()
    if not ok:
        return False, message
    preview = _marked('preview-lifecycle')
    if not preview:
        return False, 'Push the scratch builds and preview the lifecycle policy.'
    scratch = preview.get('scratch') or []
    images = _with_untagged(scratch)
    if len(scratch) != 2 or not set(scratch) <= {image['imageDigest'] for image in images}:
        return False, 'The scratch builds are missing; preview again.'
    rows = lifecycle_preview(LIFECYCLE_POLICY, images, protected=_protected_images())
    expiring = [row for row in rows if row['action'] == 'expire']
    if [row['imageDigest'] for row in expiring] != preview['scratch'][:1]:
        return False, 'Only the older scratch build should expire.'
    return True, 'The policy would expire the older scratch build and keep every release.'


def _check_load_balancer() -> tuple[bool, str]:
    if not _load_balancer():
        return False, f'Create the {LOAD_BALANCER} load balancer.'
    missing = [name for name in (BLUE_TG, GREEN_TG) if not _target_group(name)]
    if missing:
        return False, 'Create the ' + ' and '.join(missing) + ' target groups.'
    listeners = _listeners()
    missing_ports = [str(port) for port in (PROD_PORT, TEST_PORT) if port not in listeners]
    if missing_ports:
        return False, 'Add listeners on port ' + ' and '.join(missing_ports) + '.'
    return True, f'{LOAD_BALANCER} has {BLUE_TG}, {GREEN_TG}, and listeners on {PROD_PORT} and {TEST_PORT}.'


def _check_service() -> tuple[bool, str]:
    service = _service()
    if not service:
        return False, f'Create the {SERVICE_NAME} service in {CLUSTER}.'
    if (service.get('deploymentController') or {}).get('type') != 'CODE_DEPLOY':
        return False, 'The service must use the CODE_DEPLOY deployment controller.'
    blue = _target_group(BLUE_TG) or {}
    if not any(item.get('targetGroupArn') == blue.get('TargetGroupArn') for item in service.get('loadBalancers', [])):
        return False, f'Attach the service to {BLUE_TG}.'
    return True, f'{SERVICE_NAME} uses the CODE_DEPLOY controller behind {BLUE_TG}.'


def _check_deployment_group() -> tuple[bool, str]:
    group = _deployment_group()
    if not group:
        return False, f'Create the {DEPLOYMENT_GROUP} deployment group.'
    problems = []
    style = group.get('deploymentStyle') or {}
    if style.get('deploymentType') != 'BLUE_GREEN' or style.get('deploymentOption') != 'WITH_TRAFFIC_CONTROL':
        problems.append('it is not blue/green with traffic control')
    if group.get('deploymentConfigName') != CANARY_CONFIG:
        problems.append(f'it does not use {CANARY_CONFIG}')
    services = group.get('ecsServices') or []
    if {'clusterName': CLUSTER, 'serviceName': SERVICE_NAME} not in [
            {'clusterName': item.get('clusterName'), 'serviceName': item.get('serviceName')} for item in services]:
        problems.append(f'it does not target {SERVICE_NAME}')
    pairs = (group.get('loadBalancerInfo') or {}).get('targetGroupPairInfoList') or []
    pair = pairs[0] if pairs else {}
    if [item.get('name') for item in pair.get('targetGroups', [])] != [BLUE_TG, GREEN_TG]:
        problems.append('the target group pair is wrong')
    listeners = _listeners()
    if (pair.get('prodTrafficRoute') or {}).get('listenerArns') != [listeners.get(PROD_PORT, {}).get('ListenerArn')]:
        problems.append('the production route is wrong')
    if (pair.get('testTrafficRoute') or {}).get('listenerArns') != [listeners.get(TEST_PORT, {}).get('ListenerArn')]:
        problems.append('the test route is wrong')
    rollback = group.get('autoRollbackConfiguration') or {}
    if not rollback.get('enabled') or not {'DEPLOYMENT_FAILURE', 'DEPLOYMENT_STOP_ON_ALARM'} <= set(
            rollback.get('events') or []):
        problems.append('automatic rollback is not on')
    alarms = group.get('alarmConfiguration') or {}
    if not alarms.get('enabled') or ALARM not in [item.get('name') for item in alarms.get('alarms') or []]:
        problems.append(f'the {ALARM} alarm is not attached')
    if problems:
        return False, f'{DEPLOYMENT_GROUP}: ' + '; '.join(problems) + '.'
    return True, f'{DEPLOYMENT_GROUP} is a canary blue/green group with automatic rollback.'


def _check_canary() -> tuple[bool, str]:
    primary = _primary_task_set()
    if not primary:
        return False, f'Deploy {RELEASE_2}.'
    deployment = _deployment(primary.get('externalId') or '') or {}
    if deployment.get('status') != 'Succeeded':
        return False, f'The latest deployment is {deployment.get("status") or "missing"}.'
    image = _safe_call(lambda: _task_definition_image(primary['taskDefinition'])) or ''
    if image_tag(image) != RELEASE_2:
        return False, f'The primary task set runs {image_tag(image) or "an unknown image"}, not {RELEASE_2}.'
    if serving(_prod_weights()) != GREEN_TG:
        return False, f'Production traffic is not all on {GREEN_TG}.'
    return True, f'{RELEASE_2} is primary and gets all production traffic through {GREEN_TG}.'


def _failed_deployment() -> dict[str, Any] | None:
    marker = _marked('failed-hook') or {}
    return _deployment(marker.get('deploymentId', '')) if marker.get('deploymentId') else None


def _check_failed() -> tuple[bool, str]:
    deployment = _failed_deployment()
    if not deployment:
        return False, f'Deploy {RELEASE_3}.'
    error = (deployment.get('errorInformation') or {}).get('code')
    if deployment.get('status') != 'Failed' or error != 'HOOK_EXECUTION_FAILURE':
        return False, f'The {RELEASE_3} deployment is {deployment.get("status")}, not stopped by the hook.'
    ok, message = _check_canary()
    if not ok:
        return False, f'{RELEASE_2} should still be serving: {message}'
    if any(item.get('externalId') == deployment['deploymentId'] for item in _task_sets()):
        return False, f'Delete the task set the failed {RELEASE_3} deployment left behind.'
    return True, f'The smoke test stopped {RELEASE_3}; {RELEASE_2} still serves all traffic.'


CHECKS = {
    'create-repository': _check_repository,
    'push-images': _check_push,
    'preview-lifecycle': _check_lifecycle,
    'create-load-balancer': _check_load_balancer,
    'create-service': _check_service,
    'create-deployment-group': _check_deployment_group,
    'deploy-canary': _check_canary,
    'failed-hook': _check_failed,
}


# ── Steps ────────────────────────────────────────────────────────────────────

def step_create_repository() -> dict[str, Any]:
    started = time.perf_counter()
    ecr = _client('ecr')
    if not _repository():
        try:
            ecr.create_repository(repositoryName=REPOSITORY, imageTagMutability='IMMUTABLE',
                                  imageScanningConfiguration={'scanOnPush': True}, tags=[LAB_TAG])
        except ClientError as exc:
            # Floci starts its registry on the first ECR call; a slow first create can be retried by the client
            # and then report that the repository it just made already exists.
            if _code(exc) != 'RepositoryAlreadyExistsException':
                raise
    ecr.put_image_tag_mutability(repositoryName=REPOSITORY, imageTagMutability='IMMUTABLE')
    ecr.put_image_scanning_configuration(repositoryName=REPOSITORY, imageScanningConfiguration={'scanOnPush': True})
    ecr.put_lifecycle_policy(repositoryName=REPOSITORY, lifecyclePolicyText=json.dumps(LIFECYCLE_POLICY))
    repository = _repository() or {}
    response = {'Repository': {key: repository.get(key) for key in (
        'repositoryName', 'repositoryUri', 'imageTagMutability', 'imageScanningConfiguration')},
        'LifecyclePolicy': LIFECYCLE_POLICY}
    verified, message = _check_repository()
    return _result('create-repository', REPOSITORY_COMMAND, response, verified, message, started)


def step_push_images() -> dict[str, Any]:
    started = time.perf_counter()
    if not _repository():
        verified, message = _check_push()
        return _result('push-images', PUSH_COMMAND, {}, verified, message, started)
    pushes = {}
    for tag in (RELEASE_1, RELEASE_2):
        existing = _image(tag)
        pushes[tag] = {'imageId': {'imageDigest': existing['imageDigest'], 'imageTag': tag}} if existing \
            else _push(f'storefront {tag}', tag)
    overwrite = _push(f'storefront {RELEASE_1} rebuilt', RELEASE_1)
    release1 = (_image(RELEASE_1) or {}).get('imageDigest')
    _marker('push-images', {'overwrite': overwrite, 'release1': release1})
    response = {'Pushed': pushes, f'Overwrite {RELEASE_1}': overwrite,
                'Images': [{key: image.get(key) for key in ('imageTags', 'imageDigest', 'imageScanStatus')}
                           for image in _images()]}
    verified, message = _check_push()
    return _result('push-images', PUSH_COMMAND, response, verified, message, started)


def step_preview_lifecycle() -> dict[str, Any]:
    started = time.perf_counter()
    if not _image(RELEASE_2):
        verified, message = _check_lifecycle()
        return _result('preview-lifecycle', LIFECYCLE_COMMAND, {}, verified, message, started)
    scratch = []
    for index in (1, 2):
        pushed = _push(f'scratch build {index} {time.time()}', None)
        if 'imageId' in pushed:
            scratch.append(pushed['imageId']['imageDigest'])
        time.sleep(1.1)  # distinct push times, so "newest" is well defined
    images = _with_untagged(scratch)
    pushed_order = sorted((image for image in images if image['imageDigest'] in scratch), key=_pushed_at)
    scratch = [image['imageDigest'] for image in pushed_order]
    rows = lifecycle_preview(LIFECYCLE_POLICY, images, protected=_protected_images())
    _marker('preview-lifecycle', {'scratch': scratch})
    response = {'Preview': rows, 'Floci': 'StartLifecyclePolicyPreview is not available; the lab evaluated the policy.'}
    verified, message = _check_lifecycle()
    return _result('preview-lifecycle', LIFECYCLE_COMMAND, response, verified, message, started)


def step_create_load_balancer() -> dict[str, Any]:
    started = time.perf_counter()
    elbv2 = _client('elbv2')
    vpc_id, subnets = _network()
    balancer = _load_balancer()
    if not balancer:
        balancer = elbv2.create_load_balancer(Name=LOAD_BALANCER, Subnets=subnets, Type='application',
                                              Scheme='internet-facing', Tags=[LAB_TAG])['LoadBalancers'][0]
    arns = {}
    for name in (BLUE_TG, GREEN_TG):
        group = _target_group(name) or elbv2.create_target_group(
            Name=name, Protocol='HTTP', Port=CONTAINER_PORT, VpcId=vpc_id, TargetType='ip',
            HealthCheckPath=HEALTH_PATH, Tags=[LAB_TAG])['TargetGroups'][0]
        arns[name] = group['TargetGroupArn']
    listeners = _listeners()
    for port, group in ((PROD_PORT, BLUE_TG), (TEST_PORT, GREEN_TG)):
        if port not in listeners:
            elbv2.create_listener(LoadBalancerArn=balancer['LoadBalancerArn'], Protocol='HTTP', Port=port,
                                  DefaultActions=[{'Type': 'forward', 'TargetGroupArn': arns[group]}])
    response = {'LoadBalancer': {'Name': LOAD_BALANCER, 'DNSName': balancer.get('DNSName')},
                'TargetGroups': list(arns), 'Listeners': {
                    str(port): forward_weights(listener.get('DefaultActions', []))
                    for port, listener in _listeners().items()}}
    verified, message = _check_load_balancer()
    return _result('create-load-balancer', LOAD_BALANCER_COMMAND, response, verified, message, started)


def _register(tag: str) -> str:
    repository = _repository() or {}
    uri = repository.get('repositoryUri') or f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/{REPOSITORY}'
    return _client('ecs').register_task_definition(
        family=FAMILY, networkMode='awsvpc', requiresCompatibilities=['FARGATE'], cpu='256', memory='512',
        containerDefinitions=[{'name': CONTAINER, 'image': f'{uri}:{tag}', 'essential': True,
                               'portMappings': [{'containerPort': CONTAINER_PORT, 'protocol': 'tcp'}],
                               'environment': [{'name': 'RELEASE', 'value': tag}]}],
        tags=[{'key': 'Lab', 'value': LAB_KEY}])['taskDefinition']['taskDefinitionArn']


def step_create_service() -> dict[str, Any]:
    started = time.perf_counter()
    blue = _target_group(BLUE_TG)
    if not blue or not _repository():
        verified, message = _check_service()
        return _result('create-service', SERVICE_COMMAND, {}, verified, message, started)
    ecs = _client('ecs')
    ecs.create_cluster(clusterName=CLUSTER, tags=[{'key': 'Lab', 'value': LAB_KEY}])
    if not _service():
        _vpc, subnets = _network()
        ecs.create_service(
            cluster=CLUSTER, serviceName=SERVICE_NAME, taskDefinition=_register(RELEASE_1),
            desiredCount=DESIRED_COUNT, launchType='FARGATE', deploymentController={'type': 'CODE_DEPLOY'},
            loadBalancers=[{'targetGroupArn': blue['TargetGroupArn'], 'containerName': CONTAINER,
                            'containerPort': CONTAINER_PORT}],
            networkConfiguration={'awsvpcConfiguration': {'subnets': subnets, 'assignPublicIp': 'DISABLED'}})
    service = _service() or {}
    response = {'Service': {key: service.get(key) for key in (
        'serviceName', 'status', 'taskDefinition', 'desiredCount', 'runningCount', 'deploymentController',
        'loadBalancers', 'taskSets')}}
    verified, message = _check_service()
    return _result('create-service', SERVICE_COMMAND, response, verified, message, started)


def step_create_deployment_group() -> dict[str, Any]:
    started = time.perf_counter()
    listeners = _listeners()
    if not _service() or PROD_PORT not in listeners or TEST_PORT not in listeners:
        verified, message = _check_deployment_group()
        return _result('create-deployment-group', DEPLOYMENT_GROUP_COMMAND, {}, verified, message, started)
    _ensure_role(CODEDEPLOY_ROLE, CODEDEPLOY_TRUST, 'Lets CodeDeploy run ECS blue/green deployments',
                 managed=CODEDEPLOY_POLICY_ARN)
    _client('cloudwatch').put_metric_alarm(
        AlarmName=ALARM, Namespace='AWS/ApplicationELB', MetricName='HTTPCode_Target_5XX_Count', Statistic='Sum',
        Period=60, EvaluationPeriods=1, Threshold=5, ComparisonOperator='GreaterThanThreshold',
        TreatMissingData='notBreaching', AlarmDescription='Storefront green target group returns errors')
    codedeploy = _client('codedeploy')
    try:
        codedeploy.create_application(applicationName=APPLICATION, computePlatform='ECS')
    except ClientError as exc:
        if 'AlreadyExists' not in _code(exc):
            raise
    settings = dict(
        deploymentConfigName=CANARY_CONFIG,
        serviceRoleArn=f'arn:aws:iam::{ACCOUNT}:role/{CODEDEPLOY_ROLE}',
        deploymentStyle={'deploymentType': 'BLUE_GREEN', 'deploymentOption': 'WITH_TRAFFIC_CONTROL'},
        blueGreenDeploymentConfiguration={
            'terminateBlueInstancesOnDeploymentSuccess': {
                'action': 'TERMINATE', 'terminationWaitTimeInMinutes': TERMINATION_WAIT_MINUTES},
            'deploymentReadyOption': {'actionOnTimeout': 'CONTINUE_DEPLOYMENT'}},
        autoRollbackConfiguration={'enabled': True, 'events': ['DEPLOYMENT_FAILURE', 'DEPLOYMENT_STOP_ON_ALARM']},
        alarmConfiguration={'enabled': True, 'alarms': [{'name': ALARM}]},
        ecsServices=[{'clusterName': CLUSTER, 'serviceName': SERVICE_NAME}],
        loadBalancerInfo={'targetGroupPairInfoList': [{
            'targetGroups': [{'name': BLUE_TG}, {'name': GREEN_TG}],
            'prodTrafficRoute': {'listenerArns': [listeners[PROD_PORT]['ListenerArn']]},
            'testTrafficRoute': {'listenerArns': [listeners[TEST_PORT]['ListenerArn']]},
        }]},
    )
    if _deployment_group():
        codedeploy.update_deployment_group(applicationName=APPLICATION, currentDeploymentGroupName=DEPLOYMENT_GROUP,
                                           **settings)
    else:
        codedeploy.create_deployment_group(applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP,
                                           **settings)
    group = _deployment_group() or {}
    response = {'DeploymentGroup': {key: group.get(key) for key in (
        'deploymentGroupName', 'deploymentConfigName', 'deploymentStyle', 'ecsServices', 'loadBalancerInfo',
        'blueGreenDeploymentConfiguration', 'autoRollbackConfiguration', 'alarmConfiguration')}}
    verified, message = _check_deployment_group()
    return _result('create-deployment-group', DEPLOYMENT_GROUP_COMMAND, response, verified, message, started)


def _hook_zip() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('index.py', HOOK_SOURCE)
    return buffer.getvalue()


def _ensure_hook() -> None:
    _ensure_role(HOOK_ROLE, LAMBDA_TRUST, 'Runs the storefront smoke test', policy=HOOK_POLICY)
    lam = _client('lambda')
    try:
        lam.get_function(FunctionName=HOOK_FUNCTION)
        lam.update_function_code(FunctionName=HOOK_FUNCTION, ZipFile=_hook_zip())
    except ClientError as exc:
        if not _not_found(exc):
            raise
        lam.create_function(FunctionName=HOOK_FUNCTION, Runtime='python3.12', Role=HOOK_ROLE_ARN,
                            Handler='index.handler', Code={'ZipFile': _hook_zip()}, Timeout=30,
                            Description='CodeDeploy smoke test for the storefront', Tags={'Lab': LAB_KEY})
    _wait_for(lambda: (_safe_call(lambda: lam.get_function(FunctionName=HOOK_FUNCTION)['Configuration']) or {}).get(
        'State', 'Active') == 'Active' and (_safe_call(lambda: lam.get_function(
            FunctionName=HOOK_FUNCTION)['Configuration']) or {}).get('LastUpdateStatus', 'Successful') != 'InProgress',
        timeout=30)


def _wait_for(predicate, timeout: float, delay: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(delay)


def _deploy(tag: str, timeout: float = 120) -> tuple[str, dict[str, Any], list[dict[str, int]]]:
    """Start a deployment of `tag` and wait for it, recording each production traffic split seen on the way."""
    task_definition = _register(tag)
    deployment_id = _client('codedeploy').create_deployment(
        applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP,
        description=f'Ship {tag} (container blue/green lab)',
        revision={'revisionType': 'AppSpecContent',
                  'appSpecContent': {'content': json.dumps(appspec(task_definition))}})['deploymentId']
    splits: list[dict[str, int]] = []
    deadline = time.monotonic() + timeout
    deployment: dict[str, Any] = {}
    while time.monotonic() < deadline:
        weights = _safe_call(_prod_weights, {}) or {}
        if weights and (not splits or splits[-1] != weights):
            splits.append(weights)
        deployment = _deployment(deployment_id) or {}
        if deployment.get('status') in ('Succeeded', 'Failed', 'Stopped'):
            break
        time.sleep(0.5)
    weights = _safe_call(_prod_weights, {}) or {}
    if weights and (not splits or splits[-1] != weights):
        splits.append(weights)
    return deployment_id, deployment, splits


def _lifecycle_events(deployment_id: str) -> list[dict[str, Any]]:
    codedeploy = _client('codedeploy')
    targets = _safe_call(lambda: codedeploy.list_deployment_targets(deploymentId=deployment_id)['targetIds'], [])
    if not targets:
        return []
    details = _safe_call(lambda: codedeploy.batch_get_deployment_targets(
        deploymentId=deployment_id, targetIds=targets)['deploymentTargets'], []) or []
    events = (details[0].get('ecsTarget') or {}).get('lifecycleEvents', []) if details else []
    return [{'event': item.get('lifecycleEventName'), 'status': item.get('status')} for item in events]


def step_deploy_canary() -> dict[str, Any]:
    started = time.perf_counter()
    if not _deployment_group() or not _image(RELEASE_2):
        verified, message = _check_canary()
        return _result('deploy-canary', DEPLOY_COMMAND, {}, verified, message, started)
    _ensure_hook()
    deployment_id, deployment, splits = _deploy(RELEASE_2)
    _marker('deploy-canary', {'deploymentId': deployment_id, 'splits': splits})
    primary = _primary_task_set() or {}
    response = {
        'DeploymentId': deployment_id,
        'Status': deployment.get('status'),
        'ErrorInformation': deployment.get('errorInformation'),
        'LifecycleEvents': _lifecycle_events(deployment_id),
        'ProductionTrafficSplits': splits,
        'PrimaryTaskSet': {key: primary.get(key) for key in ('id', 'taskDefinition', 'externalId', 'status')},
    }
    verified, message = _check_canary()
    return _result('deploy-canary', DEPLOY_COMMAND, response, verified, message, started)


def step_failed_hook() -> dict[str, Any]:
    started = time.perf_counter()
    ok, message = _check_canary()
    if not ok:
        return _result('failed-hook', FAILED_COMMAND, {}, False, f'Ship {RELEASE_2} first. {message}', started)
    _ensure_hook()
    deployment_id, deployment, splits = _deploy(RELEASE_3)
    _marker('failed-hook', {'deploymentId': deployment_id})
    cleaned = []
    for task_set in _task_sets():
        if task_set.get('externalId') == deployment_id and task_set.get('status') != 'PRIMARY':
            _client('ecs').delete_task_set(cluster=CLUSTER, service=SERVICE_NAME, taskSet=task_set['taskSetArn'],
                                           force=True)
            cleaned.append(task_set.get('id'))
    response = {
        'DeploymentId': deployment_id,
        'Status': deployment.get('status'),
        'ErrorInformation': deployment.get('errorInformation'),
        'LifecycleEvents': _lifecycle_events(deployment_id),
        'ProductionTrafficSplits': splits,
        'DeletedFailedTaskSets': cleaned,
    }
    verified, message = _check_failed()
    return _result('failed-hook', FAILED_COMMAND, response, verified, message, started)


RUNNERS = {
    'create-repository': step_create_repository,
    'push-images': step_push_images,
    'preview-lifecycle': step_preview_lifecycle,
    'create-load-balancer': step_create_load_balancer,
    'create-service': step_create_service,
    'create-deployment-group': step_create_deployment_group,
    'deploy-canary': step_deploy_canary,
    'failed-hook': step_failed_hook,
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


def _role_is_lab(name: str) -> bool:
    tags = _safe_call(lambda: _client('iam').list_role_tags(RoleName=name).get('Tags', []), []) or []
    return LAB_TAG in tags


def reset() -> dict[str, Any]:
    """Remove the deployment group and application, the ECS service and cluster with every task definition revision,
    the load balancer and target groups, the hook function, the alarm, the lab's roles, and the ECR repository."""
    started = time.perf_counter()
    deleted: list[str] = []
    errors: list[str] = []

    def attempt(label: str, call) -> None:
        try:
            call()
            deleted.append(label)
        except ClientError as exc:
            if _not_found(exc) or _code(exc) in ('NoSuchEntity', 'ClusterContainsServicesException'):
                return
            errors.append(f'{label}: {exc}')
        except BotoCoreError as exc:
            errors.append(f'{label}: {exc}')
        except KeyError:
            deleted.append(label)

    codedeploy = _client('codedeploy')
    running = _safe_call(lambda: codedeploy.list_deployments(
        applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP,
        includeOnlyStatuses=['Created', 'Queued', 'InProgress', 'Ready']).get('deployments', []), []) or []
    for deployment_id in running:
        attempt(f'deployment:{deployment_id}', lambda deployment_id=deployment_id: codedeploy.stop_deployment(
            deploymentId=deployment_id))
    if _safe_call(_deployment_group):
        attempt(f'deployment-group:{DEPLOYMENT_GROUP}', lambda: codedeploy.delete_deployment_group(
            applicationName=APPLICATION, deploymentGroupName=DEPLOYMENT_GROUP))
    attempt(f'application:{APPLICATION}', lambda: codedeploy.delete_application(applicationName=APPLICATION))

    ecs = _client('ecs')
    if _safe_call(_service):
        for task_set in _safe_call(_task_sets, []) or []:
            attempt(f'task-set:{task_set.get("id")}', lambda task_set=task_set: ecs.delete_task_set(
                cluster=CLUSTER, service=SERVICE_NAME, taskSet=task_set['taskSetArn'], force=True))
        attempt(f'service:{SERVICE_NAME}', lambda: ecs.delete_service(cluster=CLUSTER, service=SERVICE_NAME,
                                                                      force=True))
    clusters = _safe_call(lambda: ecs.describe_clusters(clusters=[CLUSTER]).get('clusters', []), []) or []
    if any(item.get('status') == 'ACTIVE' for item in clusters):
        # Floci keeps retrying the service's tasks until the service is gone, so wait for the last one to stop.
        _wait_for(lambda: not any(_safe_call(lambda status=status: ecs.list_tasks(
            cluster=CLUSTER, desiredStatus=status).get('taskArns'), []) for status in ('PENDING', 'RUNNING')),
            timeout=30)
        for task_arn in _safe_call(lambda: ecs.list_tasks(cluster=CLUSTER).get('taskArns', []), []) or []:
            _safe_call(lambda task_arn=task_arn: ecs.stop_task(cluster=CLUSTER, task=task_arn, reason='Lab reset'))
        if not _wait_for(lambda: _safe_call(lambda: ecs.delete_cluster(cluster=CLUSTER)) is not None, timeout=30,
                         delay=2):
            attempt(f'cluster:{CLUSTER}', lambda: ecs.delete_cluster(cluster=CLUSTER))
        else:
            deleted.append(f'cluster:{CLUSTER}')
    revisions = _safe_call(lambda: ecs.list_task_definitions(familyPrefix=FAMILY, status='ACTIVE').get(
        'taskDefinitionArns', []), []) or []
    for arn in revisions:
        if arn.rsplit('/', 1)[-1].split(':')[0] == FAMILY:
            attempt(f'task-definition:{arn.rsplit("/", 1)[-1]}', lambda arn=arn: ecs.deregister_task_definition(
                taskDefinition=arn))

    elbv2 = _client('elbv2')
    balancer = _safe_call(_load_balancer)
    if balancer:
        for listener in _safe_call(lambda: list(_listeners().values()), []) or []:
            attempt(f'listener:{listener["Port"]}', lambda listener=listener: elbv2.delete_listener(
                ListenerArn=listener['ListenerArn']))
        attempt(f'load-balancer:{LOAD_BALANCER}', lambda: elbv2.delete_load_balancer(
            LoadBalancerArn=balancer['LoadBalancerArn']))
    for name in (BLUE_TG, GREEN_TG):
        group = _safe_call(lambda name=name: _target_group(name))
        if group:
            attempt(f'target-group:{name}', lambda group=group: elbv2.delete_target_group(
                TargetGroupArn=group['TargetGroupArn']))

    attempt(f'function:{HOOK_FUNCTION}', lambda: _client('lambda').delete_function(FunctionName=HOOK_FUNCTION))
    if _safe_call(lambda: _client('cloudwatch').describe_alarms(AlarmNames=[ALARM]).get('MetricAlarms'), []):
        attempt(f'alarm:{ALARM}', lambda: _client('cloudwatch').delete_alarms(AlarmNames=[ALARM]))

    iam = _client('iam')
    for role, inline, managed in ((HOOK_ROLE, f'{HOOK_ROLE}-policy', None),
                                  (CODEDEPLOY_ROLE, None, CODEDEPLOY_POLICY_ARN)):
        if not _safe_call(lambda role=role: _role_exists(role)) or not _role_is_lab(role):
            continue
        if inline:
            attempt(f'role-policy:{inline}', lambda role=role, inline=inline: iam.delete_role_policy(
                RoleName=role, PolicyName=inline))
        if managed:
            attempt(f'role-attachment:{role}', lambda role=role, managed=managed: iam.detach_role_policy(
                RoleName=role, PolicyArn=managed))
        attempt(f'role:{role}', lambda role=role: iam.delete_role(RoleName=role))

    if _safe_call(_repository):
        attempt(f'repository:{REPOSITORY}', lambda: _client('ecr').delete_repository(
            repositoryName=REPOSITORY, force=True))

    for step in STEP_KEYS:
        cache.delete(CACHE_PREFIX + step)

    payload = {'deleted': deleted, 'errors': errors}
    return {
        'service': SERVICE,
        'lab': LAB_KEY,
        'command': (
            f'aws deploy delete-deployment-group --application-name {APPLICATION} '
            f'--deployment-group-name {DEPLOYMENT_GROUP}\n'
            f'aws deploy delete-application --application-name {APPLICATION}\n'
            f'aws ecs delete-service --cluster {CLUSTER} --service {SERVICE_NAME} --force\n'
            f'aws ecs delete-cluster --cluster {CLUSTER}\n'
            f'aws elbv2 delete-load-balancer --load-balancer-arn <{LOAD_BALANCER}>\n'
            f'aws elbv2 delete-target-group --target-group-arn <{BLUE_TG}>   # and {GREEN_TG}\n'
            f'aws lambda delete-function --function-name {HOOK_FUNCTION}\n'
            f'aws ecr delete-repository --repository-name {REPOSITORY} --force'
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
            else 'The deployment group, service, load balancer, function, roles, and repository are gone.',
        },
    }
