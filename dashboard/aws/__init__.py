"""Floci client plumbing and the shared resource list.

This package holds the shared pieces: FlociClientFactory, the runtime
endpoint and identity overrides, pagination and response-cleaning helpers,
and list_resources. Each service's inventory and resource-list loaders live
in their own module (dashboard/aws/<service>.py, lambda_.py for Lambda) and
are re-exported at the bottom, so `from dashboard.aws import s3_inventory`
still works.

Service modules call FlociClientFactory and the paginate helpers through
this package (core.FlociClientFactory), so tests can patch them once on
dashboard.aws.
"""

from __future__ import annotations

import copy
import os
import json
import threading
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any, Callable, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound
from botocore.parsers import ResponseParserError
from django.conf import settings


RUNTIME_ENDPOINT_SESSION_KEY = 'floci_runtime_endpoint_url'
RUNTIME_IDENTITY_SESSION_KEY = 'floci_identity'
IAM_BOUNDARY_CACHE_KEY = 'dashboard:iam-permissions-boundaries'
_CLIENT_CACHE: dict[tuple, Any] = {}
_CLIENT_CACHE_LOCK = threading.Lock()
_CLIENT_CACHE_MAX = 512
_runtime_endpoint_override: ContextVar[str | None] = ContextVar(
    'floci_runtime_endpoint_override',
    default=None,
)
_runtime_identity_override: ContextVar[dict[str, Any] | None] = ContextVar(
    'floci_runtime_identity',
    default=None,
)


def normalize_endpoint_url(value: str) -> str:
    return str(value or '').strip().rstrip('/')


def set_runtime_endpoint_override(endpoint_url: str | None):
    return _runtime_endpoint_override.set(normalize_endpoint_url(endpoint_url) if endpoint_url else None)


def reset_runtime_endpoint_override(token) -> None:
    _runtime_endpoint_override.reset(token)


def runtime_endpoint_override() -> str | None:
    return _runtime_endpoint_override.get()


def set_runtime_identity_override(identity: dict[str, Any] | None):
    return _runtime_identity_override.set(identity if isinstance(identity, dict) else None)


def reset_runtime_identity_override(token) -> None:
    _runtime_identity_override.reset(token)


def runtime_identity_override() -> dict[str, Any] | None:
    return _runtime_identity_override.get()


def default_endpoint_url() -> str:
    return normalize_endpoint_url(
        os.getenv(
            'FLOCI_AWS_ENDPOINT_URL',
            os.getenv(
                'FLOCI_ENDPOINT',
                os.getenv(
                    'AWS_ENDPOINT_URL',
                    settings.FLOCI_AWS_ENDPOINT_URL,
                ),
            ),
        ),
    )


@dataclass(frozen=True)
class ResourceResult:
    name: str
    label: str
    count: Optional[int]
    items: list[dict[str, Any]]
    error: Optional[str] = None


class FlociClientFactory:
    def __init__(
        self,
        endpoint_url: str | None = None,
        *,
        identity: dict[str, Any] | None = None,
        use_runtime_identity: bool = True,
    ) -> None:
        override = normalize_endpoint_url(endpoint_url) if endpoint_url else runtime_endpoint_override()
        runtime_identity = runtime_identity_override() if use_runtime_identity else None
        session_identity = identity if identity is not None else runtime_identity
        self.endpoint_url = override or default_endpoint_url()
        self.region = os.getenv('AWS_DEFAULT_REGION') or os.getenv(
            'AWS_REGION',
            os.getenv(
                'FLOCI_AWS_REGION',
                settings.FLOCI_AWS_REGION,
            ),
        )
        self.has_env_credentials = bool(os.getenv('AWS_ACCESS_KEY_ID') and os.getenv('AWS_SECRET_ACCESS_KEY'))
        self.profile = os.getenv('AWS_PROFILE') or os.getenv('FLOCI_AWS_PROFILE')
        self.access_key_id = os.getenv('AWS_ACCESS_KEY_ID')
        self.secret_access_key = os.getenv('AWS_SECRET_ACCESS_KEY')
        self.session_token = os.getenv('AWS_SESSION_TOKEN')
        self.credential_source = 'environment' if self.has_env_credentials else 'default_provider_chain'
        self.profile_source = (
            'AWS_PROFILE' if os.getenv('AWS_PROFILE')
            else 'FLOCI_AWS_PROFILE' if os.getenv('FLOCI_AWS_PROFILE')
            else None
        )
        self.identity_type = None
        self.identity_label = None
        self.identity_expires_at = None

        if session_identity and session_identity.get('access_key_id') and session_identity.get('secret_access_key'):
            self.profile = None
            self.profile_source = None
            self.access_key_id = session_identity.get('access_key_id')
            self.secret_access_key = session_identity.get('secret_access_key')
            self.session_token = session_identity.get('session_token') or None
            self.has_env_credentials = True
            self.credential_source = 'session_identity'
            self.identity_type = session_identity.get('type') or 'session'
            self.identity_label = session_identity.get('label') or self.identity_type
            self.identity_expires_at = session_identity.get('expires_at')
        elif self.profile:
            self.credential_source = 'profile'
        elif not self.has_env_credentials:
            settings_profile = self._available_settings_profile()
            if settings_profile:
                self.profile = settings_profile
                self.credential_source = 'profile'
                self.profile_source = 'settings'
            else:
                self.access_key_id = 'test'
                self.secret_access_key = 'test'
                self.credential_source = 'local_default'
        self.endpoint_source = (
            'runtime_override' if override
            else 'FLOCI_AWS_ENDPOINT_URL' if os.getenv('FLOCI_AWS_ENDPOINT_URL')
            else 'FLOCI_ENDPOINT' if os.getenv('FLOCI_ENDPOINT')
            else 'AWS_ENDPOINT_URL' if os.getenv('AWS_ENDPOINT_URL')
            else 'settings'
        )
        self.region_source = (
            'AWS_DEFAULT_REGION' if os.getenv('AWS_DEFAULT_REGION')
            else 'AWS_REGION' if os.getenv('AWS_REGION')
            else 'FLOCI_AWS_REGION' if os.getenv('FLOCI_AWS_REGION')
            else 'settings'
        )
        self.config = Config(
            retries={'max_attempts': 2, 'mode': 'standard'},
            connect_timeout=2,
            read_timeout=5,
        )
        self._validate_local_endpoint()

    def _available_settings_profile(self) -> Optional[str]:
        profile = settings.FLOCI_AWS_PROFILE
        if not profile:
            return None

        try:
            session = boto3.Session(profile_name=profile, region_name=self.region)
            if session.get_credentials():
                return profile
        except (BotoCoreError, ProfileNotFound):
            return None
        return None

    def _validate_local_endpoint(self) -> None:
        parsed = urlparse(self.endpoint_url)
        hostname = (parsed.hostname or '').rstrip('.').lower()
        allowed_hosts = {'localhost', '127.0.0.1', '::1', 'floci', 'host.docker.internal'}
        allowed_suffixes = ('.localhost.floci.io', '.localhost.localstack.cloud')

        if (
            hostname not in allowed_hosts
            and hostname not in {'localhost.floci.io', 'localhost.localstack.cloud'}
            and not any(hostname.endswith(suffix) for suffix in allowed_suffixes)
        ):
            raise ValueError(
                'Refusing to use a non-local AWS endpoint. '
                f'FLOCI_AWS_ENDPOINT_URL is {self.endpoint_url!r}.'
            )

    def client(self, service_name: str):
        # Building a boto3 session and client costs ~30ms, and lab progress
        # alone needs hundreds. Clients are thread-safe, so reuse one per
        # endpoint/region/credential/config combination. The config is part of
        # the key so long-running callers (EKS, EC2, ECS, RDS) keep their longer
        # timeout and no-retry settings. boto3.Session is part of the key so
        # tests that patch it never receive a stale cached client.
        cache_key = (
            boto3.Session,
            service_name,
            self.endpoint_url,
            self.region,
            self.profile,
            self.access_key_id,
            self.secret_access_key,
            self.session_token,
            self.config.connect_timeout,
            self.config.read_timeout,
            repr(self.config.retries),
        )
        with _CLIENT_CACHE_LOCK:
            cached = _CLIENT_CACHE.get(cache_key)
            if cached is not None:
                return cached
            client = self._build_client(service_name)
            if len(_CLIENT_CACHE) >= _CLIENT_CACHE_MAX:
                _CLIENT_CACHE.clear()
            _CLIENT_CACHE[cache_key] = client
            return client

    def _build_client(self, service_name: str):
        try:
            session = boto3.Session(
                profile_name=self.profile,
                region_name=self.region,
                aws_access_key_id=self.access_key_id if not self.profile else None,
                aws_secret_access_key=self.secret_access_key if not self.profile else None,
                aws_session_token=self.session_token if not self.profile else None,
            )
        except ProfileNotFound:
            session = boto3.Session(
                region_name=self.region,
                aws_access_key_id=self.access_key_id,
                aws_secret_access_key=self.secret_access_key,
                aws_session_token=self.session_token,
            )

        if service_name == 's3':
            config = Config(
                retries={'max_attempts': 2, 'mode': 'standard'},
                connect_timeout=2,
                read_timeout=5,
                s3={'addressing_style': 'auto'},
            )
        else:
            # botocore rewrites a config's retries dict in place; pass a copy so
            # self.config, and the cache key built from it, stay unchanged.
            config = copy.deepcopy(self.config)
        return session.client(
            service_name,
            endpoint_url=self.endpoint_url,
            region_name=self.region,
            config=config,
        )

    def credential_context(self) -> dict[str, Any]:
        return {
            'credential_source': self.credential_source,
            'endpoint_source': self.endpoint_source,
            'has_env_credentials': self.has_env_credentials,
            'identity_expires_at': self.identity_expires_at,
            'identity_label': self.identity_label,
            'identity_type': self.identity_type,
            'profile': self.profile,
            'profile_source': self.profile_source,
            'region_source': self.region_source,
        }

    def local_identity_hint(self) -> Optional[dict[str, Any]]:
        if self.access_key_id == 'test' and self.secret_access_key == 'test':
            return {
                'account': 'local',
                'arn': 'test (local credentials)',
                'user_id': 'test',
            }
        return None

    def identity(self) -> dict[str, Any]:
        response = self.client('sts').get_caller_identity()
        return {
            'account': response.get('Account'),
            'arn': response.get('Arn'),
            'user_id': response.get('UserId'),
        }

    def health(self) -> dict[str, Any]:
        health_url = f'{self.endpoint_url.rstrip("/")}/_floci/health'

        try:
            with urlopen(health_url, timeout=2) as response:
                body = response.read().decode('utf-8')
        except URLError as exc:
            return {
                'ok': False,
                'url': health_url,
                'error': str(exc.reason),
            }
        except TimeoutError as exc:
            return {
                'ok': False,
                'url': health_url,
                'error': str(exc),
            }

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {'raw': body}

        return {
            'ok': True,
            'url': health_url,
            'data': data,
        }

    def init_status(self) -> dict[str, Any]:
        init_url = f'{self.endpoint_url.rstrip("/")}/_floci/init'

        try:
            with urlopen(init_url, timeout=2) as response:
                body = response.read().decode('utf-8')
        except (URLError, TimeoutError) as exc:
            return {
                'ok': False,
                'url': init_url,
                'error': str(getattr(exc, 'reason', exc)),
            }

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {'raw': body}

        return {
            'ok': True,
            'url': init_url,
            'data': data,
        }

    def reset_state(self) -> dict[str, Any]:
        reset_url = f'{self.endpoint_url.rstrip("/")}/_floci/state/reset'
        req = Request(reset_url, data=b'{}', headers={'Content-Type': 'application/json'})

        try:
            with urlopen(req, timeout=5) as response:
                body = response.read().decode('utf-8')
        except (URLError, TimeoutError) as exc:
            return {
                'ok': False,
                'url': reset_url,
                'error': str(getattr(exc, 'reason', exc)),
            }

        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {'status': 'OK'}

        return {
            'ok': True,
            'url': reset_url,
            'data': data,
        }


def _string_items(values: list[str], key: str) -> list[dict[str, Any]]:
    return [{key: value} for value in values]


def _resource(
    name: str,
    label: str,
    loader: Callable[[], list[dict[str, Any]]],
) -> ResourceResult:
    try:
        items = loader()
        return ResourceResult(name=name, label=label, count=len(items), items=items)
    except (BotoCoreError, ClientError, ResponseParserError, ValueError) as exc:
        return ResourceResult(
            name=name,
            label=label,
            count=None,
            items=[],
            error=str(exc),
        )


def _resource_service_key(name: str) -> str:
    keys = {
        'acm-certificates': 'acm',
        'apigateway-apis': 'apigateway',
        'appconfig-resources': 'appconfig',
        'appsync-resources': 'appsync',
        'amazonmq-resources': 'amazonmq',
        'athena-resources': 'athena',
        'autoscaling-resources': 'autoscaling',
        'backup-resources': 'backup',
        'batch-resources': 'batch',
        'bcmdataexports-resources': 'bcmdataexports',
        'bedrockruntime-resources': 'bedrockruntime',
        'cloudfront-resources': 'cloudfront',
        'cloudformation-resources': 'cloudformation',
        'cloudmap-resources': 'cloudmap',
        'cloudtrail-resources': 'cloudtrail',
        'cloudwatch-metrics': 'cloudwatch',
        'config-resources': 'config',
        'cognito-resources': 'cognito',
        'costexplorer-resources': 'costexplorer',
        'cur-resources': 'cur',
        'dynamodb-tables': 'dynamodb',
        'docdb-resources': 'docdb',
        'ec2-resources': 'ec2',
        'ecr-resources': 'ecr',
        'ecs-resources': 'ecs',
        'eks-resources': 'eks',
        'elasticache-resources': 'elasticache',
        'elasticloadbalancing-resources': 'elasticloadbalancing',
        'emr-resources': 'emr',
        'eventbridge-resources': 'eventbridge',
        'firehose-resources': 'firehose',
        'glue-resources': 'glue',
        'iam-roles': 'iam',
        'iam-users': 'iam',
        'kafka-resources': 'kafka',
        'kinesis-resources': 'kinesis',
        'kms-keys': 'kms',
        'lambda-functions': 'lambda',
        'log-groups': 'cloudwatch',
        'neptune-resources': 'neptune',
        'opensearch-resources': 'opensearch',
        'pipes-resources': 'pipes',
        'pricing-resources': 'pricing',
        'rds-resources': 'rds',
        'rdsdata-resources': 'rdsdata',
        'resourcegroupstagging-resources': 'resourcegroupstagging',
        'route53-resources': 'route53',
        's3-buckets': 's3',
        'scheduler-resources': 'scheduler',
        'secrets': 'secretsmanager',
        'ses-resources': 'ses',
        'sns-topics': 'sns',
        'sqs-queues': 'sqs',
        'ssm-resources': 'ssm',
        'stepfunctions-resources': 'stepfunctions',
        'textract-resources': 'textract',
        'transcribe-resources': 'transcribe',
        'transfer-resources': 'transfer',
        'wafv2-resources': 'wafv2',
        'codepipeline-resources': 'codepipeline',
        'memorydb-resources': 'memorydb',
        's3vectors-resources': 's3vectors',
        'iot-resources': 'iot',
        'elasticbeanstalk-resources': 'elasticbeanstalk',
    }
    return keys.get(name, name.replace('-resources', ''))


_MANUAL_PAGE_TOKENS = ('NextToken', 'nextToken', 'NextPageToken')
_MANUAL_PAGE_LIMIT = 100


def _paginate(client, operation_name: str, result_key: str, **kwargs) -> list[Any]:
    if client.can_paginate(operation_name):
        paginator = client.get_paginator(operation_name)
        return paginator.paginate(**kwargs).build_full_result().get(result_key, [])

    # botocore has no paginator for some list operations (Firehose, Athena work
    # groups, Transcribe, ...). get_paginator would raise OperationNotPageableError,
    # which callers swallow, so those lists always looked empty. Follow the
    # operation's own continuation token instead.
    operation = getattr(client, operation_name)
    items: list[Any] = []
    request = dict(kwargs)
    seen: set[str] = set()
    for _ in range(_MANUAL_PAGE_LIMIT):
        response = operation(**request)
        items.extend(response.get(result_key, []) or [])
        token_key = next((key for key in _MANUAL_PAGE_TOKENS if response.get(key)), None)
        if operation_name == 'list_delivery_streams' and response.get('HasMoreDeliveryStreams') and items:
            token_key, token = 'ExclusiveStartDeliveryStreamName', items[-1]
        elif operation_name == 'list_streams' and response.get('LastEvaluatedStreamArn'):
            token_key, token = 'ExclusiveStartStreamArn', response['LastEvaluatedStreamArn']
        elif token_key:
            token = response[token_key]
        else:
            break
        if not isinstance(token, str) or token in seen:
            break
        seen.add(token)
        request[token_key] = token
    return items


def _operation_items(client, operation_name: str, result_key: str, **kwargs) -> list[Any]:
    if client.can_paginate(operation_name):
        return _paginate(client, operation_name, result_key, **kwargs)

    operation = getattr(client, operation_name)
    return operation(**kwargs).get(result_key, [])


def _nested_operation_items(
    client,
    operation_name: str,
    container_key: str,
    item_key: str = 'Items',
    **kwargs,
) -> list[Any]:
    if client.can_paginate(operation_name):
        response = client.get_paginator(operation_name).paginate(**kwargs).build_full_result()
    else:
        operation = getattr(client, operation_name)
        response = operation(**kwargs)

    container = response.get(container_key, {})
    if isinstance(container, dict):
        return container.get(item_key, []) or []
    return []


def _safe_value(loader: Callable[[], Any], fallback: Any = None) -> Any:
    try:
        return loader()
    except (BotoCoreError, ClientError, ResponseParserError, ValueError):
        return fallback


def _clean_response(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _clean_response(item)
            for key, item in value.items()
            if key != 'ResponseMetadata'
        }

    if isinstance(value, list):
        return [_clean_response(item) for item in value]

    return value


def _chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index:index + size] for index in range(0, len(values), size)]


def _error_code(exc: Exception) -> Optional[str]:
    if isinstance(exc, ClientError):
        return exc.response.get('Error', {}).get('Code')

    return None


def list_resources(service_keys: set[str] | None = None) -> list[ResourceResult]:
    factory = FlociClientFactory()

    resource_loaders: list[tuple[str, str, Callable[[FlociClientFactory], list[dict[str, Any]]]]] = [
        ('acm-certificates', 'ACM certificates', acm.list_acm_resources),
        ('apigateway-apis', 'API Gateway APIs', apigateway.list_apigateway_apis),
        ('appconfig-resources', 'AppConfig resources', appconfig.list_appconfig_resources),
        ('appsync-resources', 'AppSync resources', appsync.list_appsync_resources),
        ('athena-resources', 'Athena resources', athena.list_athena_resources),
        ('autoscaling-resources', 'Auto Scaling resources', autoscaling.list_autoscaling_resources),
        ('backup-resources', 'Backup resources', backup.list_backup_resources),
        ('batch-resources', 'AWS Batch resources', batch.list_batch_resources),
        ('bedrockruntime-resources', 'Bedrock Runtime operations', bedrockruntime.list_bedrockruntime_resources),
        ('cloudfront-resources', 'CloudFront resources', cloudfront.list_cloudfront_resources),
        ('cloudmap-resources', 'Cloud Map resources', cloudmap.list_cloudmap_resources),
        ('cloudtrail-resources', 'CloudTrail trails', cloudtrail.list_cloudtrail_resources),
        ('codebuild-resources', 'CodeBuild resources', codebuild.list_codebuild_resources),
        ('codepipeline-resources', 'CodePipeline resources', codepipeline.list_codepipeline_resources),
        ('codedeploy-resources', 'CodeDeploy resources', codedeploy.list_codedeploy_resources),
        ('config-resources', 'AWS Config resources', config.list_config_resources),
        ('eks-resources', 'EKS resources', eks.list_eks_resources),
        ('elasticache-resources', 'ElastiCache resources', elasticache.list_elasticache_resources),
        ('elasticbeanstalk-resources', 'Elastic Beanstalk resources', elasticbeanstalk.list_elasticbeanstalk_resources),
        ('elasticloadbalancing-resources', 'Elastic Load Balancing resources', elasticloadbalancing.list_elasticloadbalancing_resources),
        ('emr-resources', 'EMR resources', emr.list_emr_resources),
        ('firehose-resources', 'Data Firehose resources', firehose.list_firehose_resources),
        ('kinesis-resources', 'Kinesis resources', kinesis.list_kinesis_resources),
        ('kafka-resources', 'MSK / Kafka resources', kafka.list_kafka_resources),
        ('opensearch-resources', 'OpenSearch resources', opensearch.list_opensearch_resources),
        ('pipes-resources', 'EventBridge Pipes resources', pipes.list_pipes_resources),
        ('pricing-resources', 'AWS Price List resources', pricing.list_pricing_resources),
        ('costexplorer-resources', 'Cost Explorer resources', costexplorer.list_costexplorer_resources),
        ('cur-resources', 'Cost and Usage Reports', cur.list_cur_resources),
        ('bcmdataexports-resources', 'BCM Data Exports', bcmdataexports.list_bcmdataexports_resources),
        ('memorydb-resources', 'MemoryDB resources', memorydb.list_memorydb_resources),
        ('neptune-resources', 'Neptune resources', neptune.list_neptune_resources),
        ('resourcegroupstagging-resources', 'Resource Groups Tagging resources', resourcegroupstagging.list_resourcegroupstagging_resources),
        ('ssm-resources', 'SSM resources', ssm.list_ssm_resources),
        ('cloudformation-resources', 'CloudFormation resources', cloudformation.list_cloudformation_resources),
        ('ecr-resources', 'ECR resources', ecr.list_ecr_resources),
        ('glue-resources', 'Glue resources', glue.list_glue_resources),
        ('rds-resources', 'RDS resources', rds.list_rds_resources),
        ('rdsdata-resources', 'RDS Data API operations', rdsdata.list_rdsdata_resources),
        ('route53-resources', 'Route 53 resources', route53.list_route53_resources),
        ('iam-users', 'IAM users', iam.list_iam_users),
        ('iam-roles', 'IAM roles', iam.list_iam_roles),
        ('iot-resources', 'IoT Core resources', iot.list_iot_resources),
        ('ec2-resources', 'EC2 resources', ec2.list_ec2_resources),
        ('ecs-resources', 'ECS resources', ecs.list_ecs_resources),
        ('s3-buckets', 'S3 buckets', s3.list_s3_buckets),
        ('s3vectors-resources', 'S3 Vectors resources', s3vectors.list_s3vectors_resources),
        ('sqs-queues', 'SQS queues', sqs.list_sqs_queues),
        ('dynamodb-tables', 'DynamoDB tables', dynamodb.list_dynamodb_tables),
        ('docdb-resources', 'DocumentDB resources', docdb.list_docdb_resources),
        ('sns-topics', 'SNS topics', sns.list_sns_topics),
        ('ses-resources', 'SES resources', ses.list_ses_resources),
        ('scheduler-resources', 'EventBridge Scheduler resources', scheduler.list_scheduler_resources),
        ('stepfunctions-resources', 'Step Functions resources', stepfunctions.list_stepfunctions_resources),
        ('textract-resources', 'Textract resources', textract.list_textract_resources),
        ('transcribe-resources', 'Transcribe resources', transcribe.list_transcribe_resources),
        ('transfer-resources', 'Transfer Family resources', transfer.list_transfer_resources),
        ('wafv2-resources', 'WAF v2 resources', wafv2.list_wafv2_resources),
        ('lambda-functions', 'Lambda functions', lambda_.list_lambda_functions),
        ('kms-keys', 'KMS keys', kms.list_kms_keys),
        ('secrets', 'Secrets Manager secrets', secretsmanager.list_secrets),
        ('log-groups', 'CloudWatch log groups', cloudwatch.list_log_groups),
        ('cloudwatch-metrics', 'CloudWatch metrics and alarms', cloudwatch.list_cloudwatch_metrics),
        ('cognito-resources', 'Cognito resources', cognito.list_cognito_resources),
        ('eventbridge-resources', 'EventBridge resources', eventbridge.list_eventbridge_resources),
    ]

    if service_keys is not None:
        resource_loaders = [
            loader
            for loader in resource_loaders
            if _resource_service_key(loader[0]) in service_keys
        ]

    if not resource_loaders:
        return []

    with ThreadPoolExecutor(max_workers=min(12, len(resource_loaders))) as executor:
        return list(executor.map(
            lambda args: _resource(args[0], args[1], partial(args[2], factory)),
            resource_loaders,
        ))


# Service modules. Imported last because they use the helpers above.
from . import (  # noqa: E402, F401
    acm,
    amazonmq,
    apigateway,
    appconfig,
    appsync,
    athena,
    autoscaling,
    backup,
    batch,
    bcmdataexports,
    bedrockruntime,
    cloudcontrol,
    cloudformation,
    cloudfront,
    cloudmap,
    cloudtrail,
    cloudwatch,
    codebuild,
    codedeploy,
    codepipeline,
    cognito,
    config,
    costexplorer,
    cur,
    docdb,
    dynamodb,
    ec2,
    ecr,
    ecs,
    eks,
    elasticache,
    elasticbeanstalk,
    elasticloadbalancing,
    emr,
    eventbridge,
    firehose,
    glue,
    iam,
    iot,
    kafka,
    kinesis,
    kms,
    lambda_,
    memorydb,
    neptune,
    opensearch,
    pipes,
    pricing,
    rds,
    rdsdata,
    resourcegroupstagging,
    route53,
    s3,
    s3vectors,
    scheduler,
    secretsmanager,
    ses,
    sns,
    sqs,
    ssm,
    stepfunctions,
    textract,
    transcribe,
    transfer,
    wafv2,
)
from .acm import acm_inventory  # noqa: E402, F401
from .amazonmq import amazonmq_inventory  # noqa: E402, F401
from .apigateway import apigateway_inventory  # noqa: E402, F401
from .appconfig import appconfig_inventory  # noqa: E402, F401
from .appsync import appsync_inventory  # noqa: E402, F401
from .athena import athena_inventory  # noqa: E402, F401
from .autoscaling import autoscaling_inventory  # noqa: E402, F401
from .backup import backup_inventory  # noqa: E402, F401
from .batch import batch_inventory  # noqa: E402, F401
from .bcmdataexports import bcmdataexports_inventory  # noqa: E402, F401
from .bedrockruntime import bedrockruntime_inventory  # noqa: E402, F401
from .cloudcontrol import cloudcontrol_inventory  # noqa: E402, F401
from .cloudformation import cloudformation_inventory  # noqa: E402, F401
from .cloudfront import cloudfront_inventory  # noqa: E402, F401
from .cloudmap import cloudmap_inventory  # noqa: E402, F401
from .cloudtrail import cloudtrail_inventory  # noqa: E402, F401
from .cloudwatch import cloudwatch_inventory  # noqa: E402, F401
from .codebuild import codebuild_inventory  # noqa: E402, F401
from .codedeploy import codedeploy_inventory  # noqa: E402, F401
from .codepipeline import codepipeline_inventory  # noqa: E402, F401
from .cognito import cognito_inventory  # noqa: E402, F401
from .config import config_inventory  # noqa: E402, F401
from .costexplorer import costexplorer_inventory  # noqa: E402, F401
from .cur import cur_inventory  # noqa: E402, F401
from .docdb import docdb_inventory  # noqa: E402, F401
from .dynamodb import dynamodb_inventory  # noqa: E402, F401
from .ec2 import ec2_inventory  # noqa: E402, F401
from .ecr import ecr_inventory  # noqa: E402, F401
from .ecs import ecs_inventory  # noqa: E402, F401
from .eks import eks_inventory  # noqa: E402, F401
from .elasticache import elasticache_inventory  # noqa: E402, F401
from .elasticbeanstalk import elasticbeanstalk_inventory  # noqa: E402, F401
from .elasticloadbalancing import elasticloadbalancing_inventory  # noqa: E402, F401
from .emr import emr_inventory  # noqa: E402, F401
from .eventbridge import eventbridge_inventory  # noqa: E402, F401
from .firehose import firehose_inventory  # noqa: E402, F401
from .glue import glue_inventory  # noqa: E402, F401
from .iam import iam_inventory  # noqa: E402, F401
from .iot import iot_inventory  # noqa: E402, F401
from .kafka import kafka_inventory  # noqa: E402, F401
from .kinesis import kinesis_inventory  # noqa: E402, F401
from .kms import kms_inventory  # noqa: E402, F401
from .lambda_ import lambda_inventory  # noqa: E402, F401
from .memorydb import memorydb_inventory  # noqa: E402, F401
from .neptune import neptune_inventory  # noqa: E402, F401
from .opensearch import opensearch_inventory  # noqa: E402, F401
from .pipes import pipes_inventory  # noqa: E402, F401
from .pricing import pricing_inventory  # noqa: E402, F401
from .rds import rds_inventory  # noqa: E402, F401
from .rdsdata import rdsdata_inventory  # noqa: E402, F401
from .resourcegroupstagging import resourcegroupstagging_inventory  # noqa: E402, F401
from .route53 import route53_inventory  # noqa: E402, F401
from .s3 import create_s3_bucket, s3_inventory  # noqa: E402, F401
from .s3vectors import s3vectors_inventory  # noqa: E402, F401
from .scheduler import scheduler_inventory  # noqa: E402, F401
from .secretsmanager import secretsmanager_inventory  # noqa: E402, F401
from .ses import ses_inventory  # noqa: E402, F401
from .sns import sns_inventory  # noqa: E402, F401
from .sqs import sqs_inventory  # noqa: E402, F401
from .ssm import ssm_inventory  # noqa: E402, F401
from .stepfunctions import stepfunctions_inventory  # noqa: E402, F401
from .textract import textract_inventory  # noqa: E402, F401
from .transcribe import transcribe_inventory  # noqa: E402, F401
from .transfer import transfer_inventory  # noqa: E402, F401
from .wafv2 import wafv2_inventory  # noqa: E402, F401


def public_endpoint_url() -> str:
    """The Floci URL a browser can reach.

    Inside Docker the dashboard talks to http://floci:4566, which the host's browser
    cannot resolve. FLOCI_PUBLIC_ENDPOINT_URL names the host-side URL for links the
    console hands to the browser (presigned URLs, website endpoints, function URLs).
    """
    return (os.getenv('FLOCI_PUBLIC_ENDPOINT_URL') or FlociClientFactory().endpoint_url).rstrip('/')


def browser_reachable_url(url: str | None) -> str | None:
    """Rewrite a Floci-generated URL (host suffix "floci" or the internal endpoint host)
    so it resolves from the browser, keeping the subdomain, path and query."""
    if not url:
        return url
    parsed = urlparse(url)
    public = urlparse(public_endpoint_url())
    internal = urlparse(FlociClientFactory().endpoint_url)
    host = parsed.hostname or ''
    for suffix in {'floci', internal.hostname or ''} - {''}:
        if host == suffix or host.endswith(f'.{suffix}'):
            new_host = host[: len(host) - len(suffix)] + (public.hostname or 'localhost')
            port = f':{public.port}' if public.port else ''
            return parsed._replace(netloc=f'{new_host}{port}').geturl()
    return url
