"""Shared helpers and the dispatch facade for the local AWS workflow labs.

Each service's lab definitions, step runners, verifiers, status, and reset
live in ``<service>_labs.py`` (plus a few older modules such as
``stepfunctions_labs.py``). This module keeps the lab registry, the
``run_lab_step``/``lab_status``/``reset_lab`` dispatchers, and the helpers
those modules share.
"""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError

from ..aws import FlociClientFactory, _clean_response

AWS_ACCOUNT_ID = '000000000000'
AWS_REGION = 'us-east-1'


def labs_for_service(service_key: str) -> list[dict[str, Any]]:
    if service_key == 'iam':
        from .iam_labs import LABS
        return LABS
    if service_key == 's3':
        from .s3_labs import LABS
        return LABS
    if service_key == 'sqs':
        from .sqs_labs import LABS
        return LABS
    if service_key == 'sns':
        from .sns_labs import LABS
        return LABS
    if service_key == 'scheduler':
        from .scheduler_labs import LABS
        return LABS
    if service_key == 'cloudformation':
        from .cloudformation_labs import LABS
        return LABS
    if service_key == 'ec2':
        from .ec2_labs import LABS
        return LABS
    if service_key == 'lambda':
        from .lambda_labs import LABS
        return LABS
    if service_key == 'apigateway':
        from .apigateway_labs import LABS
        return LABS
    if service_key == 'eventbridge':
        from .eventbridge_application import LAB
        return [LAB]
    if service_key == 'dynamodb':
        from .dynamodb_labs import LABS
        return LABS
    if service_key == 'kms':
        from .kms_labs import LABS
        return LABS
    if service_key == 'ssm':
        from .ssm_labs import LABS
        return LABS
    if service_key == 'secretsmanager':
        from .secretsmanager_labs import LABS
        return LABS
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import LABS
        return LABS
    if service_key == 'cognito':
        from .cognito_labs import LABS
        return LABS
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import LABS
        return LABS
    if service_key == 'rds':
        from .rds_labs import LABS
        return LABS
    if service_key == 'ecs':
        from .ecs_labs import LABS
        return LABS
    if service_key == 'eks':
        from .eks_labs import LABS
        return LABS
    if service_key == 'elasticloadbalancing':
        from .elasticloadbalancing_labs import LABS
        return LABS
    if service_key == 'route53':
        from .route53_labs import LABS
        return LABS
    if service_key == 'elasticache':
        from .elasticache_labs import LABS
        return LABS
    if service_key == 'kinesis':
        from .kinesis_labs import LABS
        return LABS
    if service_key == 'backup':
        from .backup_labs import LABS
        return LABS
    return []


LAB_BATCH_ORDER = [
    {
        'service': 'iam',
        'title': 'IAM labs',
        'summary': 'Continue into storage after the identity foundation: users, policies, groups, roles, STS, and instance profiles.',
    },
    {
        'service': 's3',
        'title': 'S3 labs',
        'summary': 'Continue into application encryption and keys after storage: buckets, objects, versioning, policies, encryption, lifecycle, notifications, multipart uploads, Object Lock, storage classes, and replication.',
    },
    {
        'service': 'kms',
        'title': 'KMS labs',
        'summary': 'Continue into application configuration after customer managed keys, aliases, encrypt/decrypt round trips, key policies, rotation, grants, envelope encryption, and SSE-KMS.',
    },
    {
        'service': 'ssm',
        'title': 'SSM Parameter Store labs',
        'summary': 'Continue into secrets management after hierarchical application configuration in Parameter Store.',
    },
    {
        'service': 'secretsmanager',
        'title': 'Secrets Manager labs',
        'summary': 'Continue into user identity directories after secret lifecycle creation, reads, and value updates.',
    },
    {
        'service': 'cognito',
        'title': 'Cognito authentication labs',
        'summary': 'Continue into asynchronous queueing after user pools, app clients, registration, and JWT token authentication.',
    },
    {
        'service': 'sqs',
        'title': 'SQS labs',
        'summary': 'Continue into pub/sub after queueing: message lifecycle, visibility, delays, batches, DLQs, FIFO, purge, and deletion.',
    },
    {
        'service': 'sns',
        'title': 'SNS labs',
        'summary': 'Continue into scheduled delivery after pub/sub fan-out and message filtering.',
    },
    {
        'service': 'scheduler',
        'title': 'EventBridge Scheduler lab',
        'summary': 'Continue into database persistence after scheduled SQS delivery through a scoped IAM role.',
    },
    {
        'service': 'dynamodb',
        'title': 'DynamoDB labs',
        'summary': 'Continue into serverless functions after table CRUD, primary/hash keys, query patterns, and Lambda write foundations.',
    },
    {
        'service': 'lambda',
        'title': 'Lambda labs',
        'summary': 'Continue into API Gateway after Lambda creation, runtime configuration reads, SQS event sources, invocation, and CloudWatch Logs inspection.',
    },
    {
        'service': 'apigateway',
        'title': 'API Gateway labs',
        'summary': 'Continue into event-driven architecture after exposing Lambda through a local HTTP API.',
    },
    {
        'service': 'eventbridge',
        'title': 'EventBridge application lab',
        'summary': 'Continue into state machine workflows after building a complete API-to-event fan-out workflow with transformed queues, Lambda notification logs, and failure experiments.',
    },
    {
        'service': 'stepfunctions',
        'title': 'Step Functions workflow labs',
        'summary': 'Continue into observability and incident response after ASL state machines, Choice branching, and Parallel task execution.',
    },
    {
        'service': 'cloudwatch',
        'title': 'CloudWatch observability labs',
        'summary': 'Continue into VPC networking after custom metric data, threshold alarms, Log Groups, and structured event streams.',
    },
    {
        'service': 'ec2',
        'title': 'EC2 networking labs',
        'summary': 'Continue into declarative infrastructure as code after VPC routing, security controls, NAT gateways and three-tier subnets, gateway endpoints, and interface endpoints.',
    },
    {
        'service': 'rds',
        'title': 'RDS & Aurora labs',
        'summary': 'Master relational database provisioning, custom parameter groups, storage scaling, read replicas, point-in-time restore, and Aurora failover.',
    },
    {
        'service': 'elasticache',
        'title': 'ElastiCache caching lab',
        'summary': 'Speed up reads with cache-aside: a Multi-AZ Redis-compatible replication group in private subnets, an eviction policy, an Aurora source of truth, and a Lambda reader that caches with a TTL and invalidates on writes.',
    },
    {
        'service': 'kinesis',
        'title': 'Streaming data lake lab',
        'summary': 'Stream clickstream events through Kinesis and Firehose into a date-partitioned S3 data lake, catalog them in Glue, query them with Athena, and convert them to Parquet.',
    },
    {
        'service': 'backup',
        'title': 'Backup & disaster recovery lab',
        'summary': 'Protect data with AWS Backup: an encrypted, locked vault, a cross-Region copy vault, daily and monthly rules, a tag-based selection, an on-demand backup, and a DR strategy chosen from RPO and RTO.',
    },
    {
        'service': 'elasticloadbalancing',
        'title': 'Load balancing & Auto Scaling lab',
        'summary': 'Build a highly available web tier: an Application Load Balancer, health-checked targets, and an Auto Scaling group that scales out, replaces lost instances, and rolls out new releases.',
    },
    {
        'service': 'route53',
        'title': 'Route 53 DNS lab',
        'summary': 'Route users with DNS: a public hosted zone, health checks, failover between Regions, an apex alias, weighted and latency records, a failover drill, and a split-horizon private hosted zone.',
    },
    {
        'service': 'ecs',
        'title': 'ECS & Fargate container labs',
        'summary': 'Deploy containerized microservices: cluster creation, task definitions, standalone Fargate tasks, and autoscaling services.',
    },
    {
        'service': 'eks',
        'title': 'EKS Kubernetes labs',
        'summary': 'Deploy managed Kubernetes control planes, EC2 worker node groups, serverless Fargate pod profiles, and local kubeconfig setup.',
    },
    {
        'service': 'cloudformation',
        'title': 'CloudFormation lab',
        'summary': 'Complete the core AWS learning path with infrastructure as code: template validation, provisioning S3 + SQS, outputs, events, and dependency-aware teardown.',
    },
]


NEXT_BUILD_RECOMMENDATIONS = [
    'KMS key, alias, encrypt, and decrypt workflow',
    'SSM Parameter Store app configuration',
    'Secrets Manager secret lifecycle',
    'Cognito User Pool user registration and authentication',
    'SQS dead-letter queue redrive and FIFO deduplication',
    'DynamoDB table CRUD and query patterns',
    'Lambda create, invoke, and inspect CloudWatch logs',
    'Lambda reads configuration and secrets at runtime',
    'API Gateway to Lambda request workflow',
    'EventBridge application spine distributed fan-out',
    'Step Functions order workflow with Choice branching',
    'CloudWatch custom metrics and alarms',
]


def next_lab_batch(service_key: str, lab_key: str) -> dict[str, Any] | None:
    labs = labs_for_service(service_key)
    if not labs or labs[-1]['key'] != lab_key:
        return None

    batch_services = [batch['service'] for batch in LAB_BATCH_ORDER]
    if service_key not in batch_services:
        return None

    batch_index = batch_services.index(service_key)
    if batch_index + 1 >= len(LAB_BATCH_ORDER):
        return {
            'title': 'All learning batches complete',
            'summary': f'You have completed all {len(LAB_BATCH_ORDER)} AWS service lab batches in Floci.',
            'service': None,
            'lab': None,
            'lab_title': None,
        }

    next_batch = LAB_BATCH_ORDER[batch_index + 1]
    next_labs = labs_for_service(next_batch['service'])
    if not next_labs:
        return None
    first_lab = next_labs[0]
    return {
        **next_batch,
        'lab': first_lab['key'],
        'lab_title': first_lab['title'],
    }


def get_lab(service_key: str, lab_key: str) -> dict[str, Any] | None:
    for lab in labs_for_service(service_key):
        if lab['key'] == lab_key:
            return lab
    return None


def _iam_client():
    return FlociClientFactory().client('iam')


def _sts_client():
    return FlociClientFactory().client('sts')


def _s3_client():
    return FlociClientFactory().client('s3')


def _sqs_client():
    return FlociClientFactory().client('sqs')


def _sns_client():
    return FlociClientFactory().client('sns')


def _scheduler_client():
    return FlociClientFactory().client('scheduler')


def _cloudformation_client():
    return FlociClientFactory().client('cloudformation')


def _ec2_client():
    return FlociClientFactory().client('ec2')


def _lambda_client():
    return FlociClientFactory().client('lambda')


def _logs_client():
    return FlociClientFactory().client('logs')


def _apigatewayv2_client():
    return FlociClientFactory().client('apigatewayv2')


def _dynamodb_client():
    return FlociClientFactory().client('dynamodb')


def _kms_client():
    return FlociClientFactory().client('kms')


def _ssm_client():
    return FlociClientFactory().client('ssm')


def _secretsmanager_client():
    return FlociClientFactory().client('secretsmanager')


def _floci_client_factory(**kwargs):
    return FlociClientFactory(**kwargs)


def _json_text(value: Any) -> str:
    return json.dumps(_clean_response(value), indent=2, default=str)


def _error_code(exc: ClientError) -> str | None:
    return exc.response.get('Error', {}).get('Code')


def _ignore_missing(action) -> bool:
    try:
        action()
        return True
    except ClientError as exc:
        if _error_code(exc) == 'NoSuchEntity':
            return False
        raise


def _verify_role(role_name: str, trusted_service: str) -> dict[str, Any]:
    try:
        role = _iam_client().get_role(RoleName=role_name).get('Role', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    trust_policy = role.get('AssumeRolePolicyDocument') or {}
    statements = trust_policy.get('Statement', [])
    trusted = any(
        statement.get('Effect') == 'Allow'
        and statement.get('Action') == 'sts:AssumeRole'
        and (
            statement.get('Principal', {}).get('Service') == trusted_service
            or statement.get('Principal', {}).get('AWS') == trusted_service
        )
        for statement in statements
    )
    if role.get('RoleName') == role_name and trusted:
        return {
            'status': 'passed',
            'message': f'Role {role_name} exists and trusts {trusted_service}.',
            'resource': _clean_response(role),
        }

    return {
        'status': 'failed',
        'message': f'Role {role_name} does not have the expected {trusted_service} trust policy.',
    }


LAMBDA_LOG_WAIT_SECONDS = 20


def _wait_for_verification(verify, timeout_seconds: float = LAMBDA_LOG_WAIT_SECONDS) -> dict[str, Any]:
    """Re-run a verification until it passes or time runs out.

    Asynchronous Lambda invocations write logs a moment later (longer on a
    cold start), so log checks that run right after the trigger need to wait.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        verification = verify()
        if verification.get('status') == 'passed' or time.monotonic() >= deadline:
            return verification
        time.sleep(0.5)


def _describe_log_streams_if_present(log_group_name: str) -> dict[str, Any]:
    try:
        return _logs_client().describe_log_streams(
            logGroupName=log_group_name,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        )
    except ClientError as exc:
        if _error_code(exc) == 'ResourceNotFoundException':
            return {'logStreams': []}
        raise


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _secret_string_json(response: dict[str, Any]) -> Any:
    return _parse_json_text(response.get('SecretString') or '')


def _sqs_queue_url(queue_name: str) -> str:
    return _sqs_client().get_queue_url(QueueName=queue_name)['QueueUrl']


def _config_lab_step_result(
    service: str,
    lab: str,
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': service,
        'lab': lab,
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _ignore_missing_codes(action, missing_codes: set[str]) -> bool:
    try:
        action()
        return True
    except ClientError as exc:
        if _error_code(exc) in missing_codes:
            return False
        raise


def run_lab_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import run_step
        return run_step(step_key)
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'cognito':
        from .cognito_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'rds':
        from .rds_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'ecs':
        from .ecs_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'eks':
        from .eks_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'elasticloadbalancing':
        from .elasticloadbalancing_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'route53':
        from .route53_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'elasticache':
        from .elasticache_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'kinesis':
        from .kinesis_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'backup':
        from .backup_labs import run_step
        return run_step(service_key, lab_key, step_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2':
        from .ec2_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'iam':
        from .iam_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'lambda':
        from .lambda_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'apigateway':
        from .apigateway_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'kms':
        from .kms_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'ssm':
        from .ssm_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 's3':
        from .s3_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'sqs':
        from .sqs_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'sns':
        from .sns_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'scheduler':
        from .scheduler_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'cloudformation':
        from .cloudformation_labs import run_step
        return run_step(service_key, lab_key, step_key)

    raise ValueError('Lab step not found')


def lab_status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import status
        return status()
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import status
        return status(service_key, lab_key)
    if service_key == 'cognito':
        from .cognito_labs import status
        return status(service_key, lab_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import status
        return status(service_key, lab_key)
    if service_key == 'rds':
        from .rds_labs import status
        return status(service_key, lab_key)
    if service_key == 'ecs':
        from .ecs_labs import status
        return status(service_key, lab_key)
    if service_key == 'eks':
        from .eks_labs import status
        return status(service_key, lab_key)
    if service_key == 'elasticloadbalancing':
        from .elasticloadbalancing_labs import status
        return status(service_key, lab_key)
    if service_key == 'route53':
        from .route53_labs import status
        return status(service_key, lab_key)
    if service_key == 'elasticache':
        from .elasticache_labs import status
        return status(service_key, lab_key)
    if service_key == 'kinesis':
        from .kinesis_labs import status
        return status(service_key, lab_key)
    if service_key == 'backup':
        from .backup_labs import status
        return status(service_key, lab_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2':
        from .ec2_labs import status
        return status(service_key, lab_key)

    if service_key == 'iam':
        from .iam_labs import status
        return status(service_key, lab_key)

    if service_key == 'lambda':
        from .lambda_labs import status
        return status(service_key, lab_key)

    if service_key == 'apigateway':
        from .apigateway_labs import status
        return status(service_key, lab_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import status
        return status(service_key, lab_key)

    if service_key == 'kms':
        from .kms_labs import status
        return status(service_key, lab_key)

    if service_key == 'ssm':
        from .ssm_labs import status
        return status(service_key, lab_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import status
        return status(service_key, lab_key)

    if service_key == 's3':
        from .s3_labs import status
        return status(service_key, lab_key)

    if service_key == 'sqs':
        from .sqs_labs import status
        return status(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import status
        return status(service_key, lab_key)

    if service_key == 'scheduler':
        from .scheduler_labs import status
        return status(service_key, lab_key)

    if service_key == 'cloudformation':
        from .cloudformation_labs import status
        return status(service_key, lab_key)

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset_lab(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import reset
        return reset()
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'cognito':
        from .cognito_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'rds':
        from .rds_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'ecs':
        from .ecs_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'eks':
        from .eks_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'elasticloadbalancing':
        from .elasticloadbalancing_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'route53':
        from .route53_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'elasticache':
        from .elasticache_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'kinesis':
        from .kinesis_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'backup':
        from .backup_labs import reset
        return reset(service_key, lab_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2':
        from .ec2_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'iam':
        from .iam_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'lambda':
        from .lambda_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'apigateway':
        from .apigateway_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'kms':
        from .kms_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'ssm':
        from .ssm_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import reset
        return reset(service_key, lab_key)

    if service_key == 's3':
        from .s3_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'sqs':
        from .sqs_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'scheduler':
        from .scheduler_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'cloudformation':
        from .cloudformation_labs import reset
        return reset(service_key, lab_key)

    raise ValueError('Lab reset not found')


# Service lab modules. Their names stay importable from ``dashboard.labs`` so
# existing callers and tests keep working.
from . import (  # noqa: E402
    sqs_labs,
    sns_labs,
    scheduler_labs,
    kms_labs,
    ssm_labs,
    secretsmanager_labs,
    dynamodb_labs,
    apigateway_labs,
    lambda_labs,
    s3_labs,
    iam_labs,
    ec2_labs,
    cloudformation_labs,
)

_SERVICE_LAB_MODULES = (
    sqs_labs,
    sns_labs,
    scheduler_labs,
    kms_labs,
    ssm_labs,
    secretsmanager_labs,
    dynamodb_labs,
    apigateway_labs,
    lambda_labs,
    s3_labs,
    iam_labs,
    ec2_labs,
    cloudformation_labs,
)

for _module in _SERVICE_LAB_MODULES:
    for _name, _value in vars(_module).items():
        if not _name.startswith('__') and _name not in ('LABS', 'run_step', 'status', 'reset'):
            globals().setdefault(_name, _value)
del _module, _name, _value
