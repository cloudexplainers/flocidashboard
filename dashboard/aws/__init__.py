from __future__ import annotations

import copy
import os
import json
import threading
from contextvars import ContextVar
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from datetime import date, timedelta
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import Any, Callable, Optional

import boto3
from botocore import xform_name
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound
from botocore.parsers import ResponseParserError
from django.conf import settings
from django.core.cache import cache


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


def _paginate(client, operation_name: str, result_key: str, **kwargs) -> list[Any]:
    paginator = client.get_paginator(operation_name)
    return paginator.paginate(**kwargs).build_full_result().get(result_key, [])


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


def wafv2_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    wafv2 = factory.client('wafv2')
    operations = set(wafv2.meta.service_model.operation_names)
    scopes = ['REGIONAL', 'CLOUDFRONT']

    def scoped_items(operation_id: str, method_name: str, result_key: str) -> list[dict[str, Any]]:
        if operation_id not in operations:
            return []
        items: list[dict[str, Any]] = []
        for scope in scopes:
            values = _safe_value(
                lambda scope=scope: _operation_items(wafv2, method_name, result_key, Scope=scope),
                [],
            )
            for value in values:
                items.append({**value, 'Scope': scope})
        return items

    web_acls = scoped_items('ListWebACLs', 'list_web_acls', 'WebACLs')
    rule_groups = scoped_items('ListRuleGroups', 'list_rule_groups', 'RuleGroups')
    ip_sets = scoped_items('ListIPSets', 'list_ip_sets', 'IPSets')
    regex_pattern_sets = scoped_items('ListRegexPatternSets', 'list_regex_pattern_sets', 'RegexPatternSets')

    return {
        'summary': {
            'web_acls': len(web_acls),
            'rule_groups': len(rule_groups),
            'ip_sets': len(ip_sets),
            'regex_pattern_sets': len(regex_pattern_sets),
            'available_sdk_operations': len(operations),
        },
        'web_acls': _clean_response(web_acls),
        'rule_groups': _clean_response(rule_groups),
        'ip_sets': _clean_response(ip_sets),
        'regex_pattern_sets': _clean_response(regex_pattern_sets),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateWebACL',
                'ListWebACLs',
                'GetWebACL',
                'UpdateWebACL',
                'DeleteWebACL',
                'CreateRuleGroup',
                'ListRuleGroups',
                'CreateIPSet',
                'ListIPSets',
                'CreateRegexPatternSet',
                'ListRegexPatternSets',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.25 adds AWS WAF v2 management API support as a Phase 1 service.',
            'WAF v2 APIs are scope-aware, so the dashboard checks both REGIONAL and CLOUDFRONT resource scopes.',
        ],
    }


def memorydb_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    memorydb = factory.client('memorydb')
    operations = set(memorydb.meta.service_model.operation_names)

    clusters = _safe_value(lambda: _operation_items(memorydb, 'describe_clusters', 'Clusters'), []) if 'DescribeClusters' in operations else []
    users = _safe_value(lambda: _operation_items(memorydb, 'describe_users', 'Users'), []) if 'DescribeUsers' in operations else []
    acls = _safe_value(lambda: _operation_items(memorydb, 'describe_acls', 'ACLs'), []) if 'DescribeACLs' in operations else []
    subnet_groups = _safe_value(lambda: _operation_items(memorydb, 'describe_subnet_groups', 'SubnetGroups'), []) if 'DescribeSubnetGroups' in operations else []
    parameter_groups = _safe_value(lambda: _operation_items(memorydb, 'describe_parameter_groups', 'ParameterGroups'), []) if 'DescribeParameterGroups' in operations else []
    snapshots = _safe_value(lambda: _operation_items(memorydb, 'describe_snapshots', 'Snapshots'), []) if 'DescribeSnapshots' in operations else []

    return {
        'summary': {
            'clusters': len(clusters),
            'users': len(users),
            'acls': len(acls),
            'subnet_groups': len(subnet_groups),
            'parameter_groups': len(parameter_groups),
            'snapshots': len(snapshots),
            'available_sdk_operations': len(operations),
        },
        'clusters': _clean_response(clusters),
        'users': _clean_response(users),
        'acls': _clean_response(acls),
        'subnet_groups': _clean_response(subnet_groups),
        'parameter_groups': _clean_response(parameter_groups),
        'snapshots': _clean_response(snapshots),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateCluster',
                'DescribeClusters',
                'DeleteCluster',
                'CreateUser',
                'DescribeUsers',
                'CreateACL',
                'DescribeACLs',
                'CreateSubnetGroup',
                'DescribeSubnetGroups',
                'DescribeParameterGroups',
                'DescribeSnapshots',
                'TagResource',
                'UntagResource',
                'ListTags',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.27 adds Amazon MemoryDB service emulation with mock mode.',
            'Floci 1.5.28 models MemoryDB authentication through ACLs and users.',
            'This read-only inspector focuses on cluster, user, ACL, subnet group, parameter group, and snapshot inventory.',
        ],
    }


def s3vectors_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    s3vectors = factory.client('s3vectors')
    operations = set(s3vectors.meta.service_model.operation_names)

    vector_buckets = _safe_value(lambda: _operation_items(s3vectors, 'list_vector_buckets', 'vectorBuckets'), []) if 'ListVectorBuckets' in operations else []
    indexes: list[dict[str, Any]] = []
    for bucket in vector_buckets[:50]:
        bucket_name = bucket.get('vectorBucketName') or bucket.get('name')
        if bucket_name and 'ListIndexes' in operations:
            bucket_indexes = _safe_value(lambda bucket_name=bucket_name: _operation_items(s3vectors, 'list_indexes', 'indexes', vectorBucketName=bucket_name), [])
            indexes.extend({'vectorBucketName': bucket_name, **index} for index in bucket_indexes)

    return {
        'summary': {
            'vector_buckets': len(vector_buckets),
            'indexes': len(indexes),
            'available_sdk_operations': len(operations),
        },
        'vector_buckets': _clean_response(vector_buckets),
        'indexes': _clean_response(indexes),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateVectorBucket',
                'ListVectorBuckets',
                'GetVectorBucket',
                'DeleteVectorBucket',
                'CreateIndex',
                'ListIndexes',
                'GetIndex',
                'DeleteIndex',
                'PutVectors',
                'QueryVectors',
                'GetVectors',
                'DeleteVectors',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.27 adds AWS S3 Vectors service support for local vector-search workflows.',
            'The inspector lists vector buckets and samples indexes for the first 50 buckets when the SDK model exposes those operations.',
        ],
    }


def rdsdata_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    rdsdata = factory.client('rds-data')
    operations = set(rdsdata.meta.service_model.operation_names)

    return {
        'summary': {
            'statements': 0,
            'transactions': 0,
            'available_sdk_operations': len(operations),
        },
        'statement_operations': [
            {'name': operation}
            for operation in [
                'ExecuteStatement',
                'BatchExecuteStatement',
            ]
            if operation in operations
        ],
        'transaction_operations': [
            {'name': operation}
            for operation in [
                'BeginTransaction',
                'CommitTransaction',
                'RollbackTransaction',
            ]
            if operation in operations
        ],
        'request_fields': [
            {'name': 'resourceArn', 'description': 'RDS cluster ARN for the Data API target.'},
            {'name': 'secretArn', 'description': 'Secret ARN used by the Data API request.'},
            {'name': 'database', 'description': 'Optional database name.'},
            {'name': 'sql', 'description': 'SQL statement for ExecuteStatement or BatchExecuteStatement.'},
            {'name': 'transactionId', 'description': 'Transaction token returned by BeginTransaction.'},
        ],
        'supported_from_sdk': [
            operation
            for operation in [
                'ExecuteStatement',
                'BatchExecuteStatement',
                'BeginTransaction',
                'CommitTransaction',
                'RollbackTransaction',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.31 adds PostgreSQL support to the RDS Data API, extending the MySQL-backed flow introduced in 1.5.25.',
            'Floci 1.5.25 adds RDS Data API support under the rds-data service name.',
            'RDS Data API is request-oriented rather than catalog-oriented, so this page surfaces supported calls and required request fields.',
        ],
    }


def transcribe_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    transcribe = factory.client('transcribe')
    operations = set(transcribe.meta.service_model.operation_names)

    transcription_jobs = _safe_value(
        lambda: _paginate(transcribe, 'list_transcription_jobs', 'TranscriptionJobSummaries'),
        [],
    ) if 'ListTranscriptionJobs' in operations else []
    medical_transcription_jobs = _safe_value(
        lambda: _paginate(transcribe, 'list_medical_transcription_jobs', 'MedicalTranscriptionJobSummaries'),
        [],
    ) if 'ListMedicalTranscriptionJobs' in operations else []
    vocabularies = _safe_value(
        lambda: _paginate(transcribe, 'list_vocabularies', 'Vocabularies'),
        [],
    ) if 'ListVocabularies' in operations else []
    medical_vocabularies = _safe_value(
        lambda: _paginate(transcribe, 'list_medical_vocabularies', 'Vocabularies'),
        [],
    ) if 'ListMedicalVocabularies' in operations else []
    vocabulary_filters = _safe_value(
        lambda: _paginate(transcribe, 'list_vocabulary_filters', 'VocabularyFilters'),
        [],
    ) if 'ListVocabularyFilters' in operations else []
    language_models = _safe_value(
        lambda: _paginate(transcribe, 'list_language_models', 'Models'),
        [],
    ) if 'ListLanguageModels' in operations else []
    call_analytics_jobs = _safe_value(
        lambda: _paginate(transcribe, 'list_call_analytics_jobs', 'CallAnalyticsJobSummaries'),
        [],
    ) if 'ListCallAnalyticsJobs' in operations else []
    call_analytics_categories = _safe_value(
        lambda: _paginate(transcribe, 'list_call_analytics_categories', 'Categories'),
        [],
    ) if 'ListCallAnalyticsCategories' in operations else []

    return {
        'summary': {
            'transcription_jobs': len(transcription_jobs),
            'medical_jobs': len(medical_transcription_jobs),
            'vocabularies': len(vocabularies),
            'vocabulary_filters': len(vocabulary_filters),
            'language_models': len(language_models),
            'call_analytics_jobs': len(call_analytics_jobs),
            'available_sdk_operations': len(operations),
        },
        'transcription_jobs': [
            {'name': item.get('TranscriptionJobName'), **_clean_response(item)}
            for item in transcription_jobs
        ],
        'medical_transcription_jobs': [
            {'name': item.get('MedicalTranscriptionJobName'), **_clean_response(item)}
            for item in medical_transcription_jobs
        ],
        'vocabularies': [
            {'name': item.get('VocabularyName'), **_clean_response(item)}
            for item in vocabularies
        ],
        'medical_vocabularies': [
            {'name': item.get('VocabularyName'), **_clean_response(item)}
            for item in medical_vocabularies
        ],
        'vocabulary_filters': [
            {'name': item.get('VocabularyFilterName'), **_clean_response(item)}
            for item in vocabulary_filters
        ],
        'language_models': [
            {'name': item.get('ModelName'), **_clean_response(item)}
            for item in language_models
        ],
        'call_analytics_jobs': [
            {'name': item.get('CallAnalyticsJobName'), **_clean_response(item)}
            for item in call_analytics_jobs
        ],
        'call_analytics_categories': [
            {'name': item.get('CategoryName'), **_clean_response(item)}
            for item in call_analytics_categories
        ],
        'supported_from_sdk': [
            operation
            for operation in [
                'StartTranscriptionJob',
                'GetTranscriptionJob',
                'ListTranscriptionJobs',
                'DeleteTranscriptionJob',
                'CreateVocabulary',
                'GetVocabulary',
                'ListVocabularies',
                'DeleteVocabulary',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci Transcribe is a control-plane stub: jobs transition to COMPLETED immediately and vocabularies transition to READY immediately.',
            'StartTranscriptionJob accepts a media URI but performs no audio parsing or speech-to-text processing.',
            'LanguageCode defaults to en-US and MediaFormat defaults to mp4 when omitted.',
            'Floci 1.5.29 persists vocabularies across restart; transcription job execution remains stubbed.',
            'Streaming, medical transcription, call analytics, and specialized Transcribe APIs are out of scope.',
        ],
    }


def _lambda_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def lambda_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    lambda_client = factory.client('lambda')
    functions = _safe_value(lambda: _paginate(lambda_client, 'list_functions', 'Functions'), [])
    event_source_mappings = _safe_value(
        lambda: _paginate(lambda_client, 'list_event_source_mappings', 'EventSourceMappings'),
        [],
    )

    mappings_by_function: dict[str, list[dict[str, Any]]] = {}
    for mapping in event_source_mappings:
        function_arn = mapping.get('FunctionArn')
        if function_arn:
            function_name = function_arn.rsplit(':', 1)[-1]
            mappings_by_function.setdefault(function_name, []).append(_clean_response(mapping))

    def function_detail(function: dict[str, Any]) -> dict[str, Any]:
        name = function.get('FunctionName')
        arn = function.get('FunctionArn')
        function_response = _lambda_optional(
            lambda: lambda_client.get_function(FunctionName=name),
            {'ResourceNotFoundException'},
        )
        configuration = _lambda_optional(
            lambda: lambda_client.get_function_configuration(FunctionName=name),
            {'ResourceNotFoundException'},
        )
        policy = _lambda_optional(
            lambda: json.loads(lambda_client.get_policy(FunctionName=name).get('Policy', '{}')),
            {'ResourceNotFoundException'},
        )

        return {
            'name': name,
            'arn': arn,
            'runtime': function.get('Runtime'),
            'handler': function.get('Handler'),
            'package_type': function.get('PackageType'),
            'state': function.get('State'),
            'last_modified': function.get('LastModified'),
            'role': function.get('Role'),
            'memory_size': function.get('MemorySize'),
            'timeout': function.get('Timeout'),
            'architectures': function.get('Architectures'),
            'environment': function.get('Environment'),
            'tracing_config': function.get('TracingConfig'),
            'layers': function.get('Layers'),
            'code': function_response.get('Code') if isinstance(function_response, dict) else function_response,
            'configuration': configuration,
            'versions': _lambda_optional(
                lambda: _paginate(lambda_client, 'list_versions_by_function', 'Versions', FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'aliases': _lambda_optional(
                lambda: _paginate(lambda_client, 'list_aliases', 'Aliases', FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'event_source_mappings': mappings_by_function.get(name, []),
            'policy': policy,
            'function_url': _lambda_optional(
                lambda: lambda_client.get_function_url_config(FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'code_signing_config': _lambda_optional(
                lambda: lambda_client.get_function_code_signing_config(FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'concurrency': _lambda_optional(
                lambda: lambda_client.get_function_concurrency(FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'tags': _lambda_optional(
                lambda: lambda_client.list_tags(Resource=arn).get('Tags', {}),
                {'ResourceNotFoundException'},
            ) if arn else None,
        }

    # Detail enrichment is independent per function and otherwise requires many
    # sequential round trips. Preserve list ordering while bounding concurrency.
    if len(functions) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(functions))) as executor:
            detailed_functions = list(executor.map(function_detail, functions))
    else:
        detailed_functions = [function_detail(function) for function in functions]

    def layer_name(layer: dict[str, Any]) -> str | None:
        arn = layer.get('Arn') or layer.get('LayerVersionArn') or layer.get('LayerArn')
        if not arn or ':layer:' not in arn:
            return None
        return arn.split(':layer:', 1)[1].split(':', 1)[0]

    attached_layer_names = sorted({
        name
        for function in detailed_functions
        for name in [
            layer_name(layer)
            for layer in function.get('layers') or []
            if isinstance(layer, dict)
        ]
        if name
    })
    layer_versions = [
        {
            'name': name,
            'versions': _lambda_optional(
                lambda name=name: _paginate(lambda_client, 'list_layer_versions', 'LayerVersions', LayerName=name),
                {'ResourceNotFoundException'},
            ),
        }
        for name in attached_layer_names
    ]

    return {
        'summary': {
            'functions': len(detailed_functions),
            'event_source_mappings': len(event_source_mappings),
            'function_urls': sum(1 for function in detailed_functions if function.get('function_url')),
            'attached_layers': len(attached_layer_names),
            'aliases': sum(
                len(function.get('aliases') or [])
                for function in detailed_functions
                if isinstance(function.get('aliases'), list)
            ),
            'published_versions': sum(
                len(function.get('versions') or [])
                for function in detailed_functions
                if isinstance(function.get('versions'), list)
            ),
        },
        'functions': detailed_functions,
        'event_source_mappings': _clean_response(event_source_mappings),
        'layer_versions': layer_versions,
        'supported': [
            'CreateFunction',
            'GetFunction',
            'GetFunctionConfiguration',
            'ListFunctions',
            'UpdateFunctionCode',
            'UpdateFunctionConfiguration',
            'DeleteFunction',
            'Invoke',
            'CreateEventSourceMapping',
            'GetEventSourceMapping',
            'ListEventSourceMappings',
            'UpdateEventSourceMapping',
            'DeleteEventSourceMapping',
            'PublishVersion',
            'ListVersionsByFunction',
            'CreateAlias',
            'GetAlias',
            'ListAliases',
            'UpdateAlias',
            'DeleteAlias',
            'AddPermission',
            'GetPolicy',
            'RemovePermission',
            'GetFunctionCodeSigningConfig',
            'CreateFunctionUrlConfig',
            'GetFunctionUrlConfig',
            'UpdateFunctionUrlConfig',
            'DeleteFunctionUrlConfig',
            'ListTags',
            'TagResource',
            'UntagResource',
            'PutFunctionConcurrency',
            'GetFunctionConcurrency',
            'DeleteFunctionConcurrency',
            'PublishLayerVersion',
            'GetLayerVersion',
            'ListLayerVersions',
            'DeleteLayerVersion',
        ],
        'not_implemented': [
            'Layer permission operations',
            'Provisioned concurrency operations',
            'Dead-letter and async/event invoke config operations',
            'InvokeWithResponseStream',
            'Code signing management beyond GetFunctionCodeSigningConfig',
            'GetAccountSettings',
        ],
        'notes': [
            'Lambda runs function code inside real Docker containers.',
            'S3-based deployments support reactive hot reload when the source object changes.',
            'Bind-mount hot reload uses S3Bucket=hot-reload and requires explicit configuration.',
            'Reserved concurrency is enforced; provisioned concurrency is not implemented.',
            'Floci 1.5.31 persists Lambda version counters and event-invoke configs, and mounts function code read-only for faster cold starts.',
            'Floci 1.5.28 improves SQS event source mappings by carrying message attributes, FIFO system attributes, and persisted firstReceiveTimestamp, and resolves leading ./ handler paths inside packages.',
            'Floci 1.5.18 uses a checkout/release Lambda port pool with clearer exhaustion errors during concurrent invocations.',
            'Floci 1.5.19 adds Lambda layer publishing, lookup, version listing, and deletion.',
            'Floci 1.5.22 supports path-style ECR image URIs and lets spawned containers resolve public localhost.floci.io hostnames.',
        ],
    }


def _sqs_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def sqs_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    sqs = factory.client('sqs')
    queue_urls = sqs.list_queues().get('QueueUrls', [])

    def queue_name(queue_url: str) -> str:
        return queue_url.rstrip('/').rsplit('/', 1)[-1]

    def queue_detail(queue_url: str) -> dict[str, Any]:
        attributes = _sqs_optional(
            lambda: sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=['All']).get('Attributes', {}),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
        tags = _sqs_optional(
            lambda: sqs.list_queue_tags(QueueUrl=queue_url).get('Tags', {}),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
        dead_letter_sources = _sqs_optional(
            lambda: sqs.list_dead_letter_source_queues(QueueUrl=queue_url).get('queueUrls', []),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
        message_move_tasks = _sqs_optional(
            lambda: sqs.list_message_move_tasks(SourceArn=attributes.get('QueueArn')).get('Results', [])
            if isinstance(attributes, dict) and attributes.get('QueueArn') else [],
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )

        return {
            'name': queue_name(queue_url),
            'url': queue_url,
            'arn': attributes.get('QueueArn') if isinstance(attributes, dict) else None,
            'fifo': queue_url.endswith('.fifo') or (
                isinstance(attributes, dict) and attributes.get('FifoQueue') == 'true'
            ),
            'attributes': attributes,
            'tags': tags,
            'dead_letter_source_queues': dead_letter_sources,
            'message_move_tasks': message_move_tasks,
            'approximate_messages': int(attributes.get('ApproximateNumberOfMessages', 0))
            if isinstance(attributes, dict) else 0,
            'approximate_not_visible': int(attributes.get('ApproximateNumberOfMessagesNotVisible', 0))
            if isinstance(attributes, dict) else 0,
            'approximate_delayed': int(attributes.get('ApproximateNumberOfMessagesDelayed', 0))
            if isinstance(attributes, dict) else 0,
        }

    queues = [queue_detail(url) for url in queue_urls]

    return {
        'summary': {
            'queues': len(queues),
            'fifo_queues': sum(1 for queue in queues if queue.get('fifo')),
            'visible_messages': sum(queue.get('approximate_messages') or 0 for queue in queues),
            'in_flight_messages': sum(queue.get('approximate_not_visible') or 0 for queue in queues),
            'delayed_messages': sum(queue.get('approximate_delayed') or 0 for queue in queues),
        },
        'queues': queues,
        'supported': [
            'CreateQueue',
            'DeleteQueue',
            'ListQueues',
            'GetQueueUrl',
            'GetQueueAttributes',
            'SetQueueAttributes',
            'SendMessage',
            'SendMessageBatch',
            'ReceiveMessage',
            'DeleteMessage',
            'DeleteMessageBatch',
            'ChangeMessageVisibility',
            'ChangeMessageVisibilityBatch',
            'PurgeQueue',
            'TagQueue',
            'UntagQueue',
            'ListQueueTags',
            'ListDeadLetterSourceQueues',
            'StartMessageMoveTask',
            'ListMessageMoveTasks',
            'CancelMessageMoveTask',
        ],
        'configuration': {
            'default_visibility_timeout_seconds': 30,
            'max_message_size_bytes': 1048576,
            'queue_url_format': f'{factory.endpoint_url.rstrip("/")}/000000000000/<queue-name>',
        },
        'notes': [
            'Floci 1.5.25 aligns MaximumMessageSize with AWS at 1 MB and reports ApproximateNumberOfMessagesDelayed in GetQueueAttributes.',
            'Floci 1.5.25 releases in-flight ReceiveMessage long polls when their queue is deleted.',
            'Floci 1.5.19 applies ContentBasedDeduplication from CloudFormation templates and accepts bare queue names in QueueUrl fields.',
            'Floci 1.5.18 supports StartMessageMoveTask, ListMessageMoveTasks, and CancelMessageMoveTask with async drain behavior.',
            'FIFO deduplication is scoped per MessageGroupId when DeduplicationScope=messageGroup.',
            'AWSTraceHeader is persisted and returned as an SQS system attribute.',
        ],
    }


def _secrets_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def secretsmanager_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    client = factory.client('secretsmanager')
    secrets = _paginate(client, 'list_secrets', 'SecretList', IncludePlannedDeletion=True)

    def secret_detail(secret: dict[str, Any]) -> dict[str, Any]:
        secret_id = secret.get('ARN') or secret.get('Name')
        description = _secrets_optional(
            lambda: client.describe_secret(SecretId=secret_id),
            {'ResourceNotFoundException'},
        )
        metadata = description if isinstance(description, dict) and not description.get('error') else secret
        versions = _secrets_optional(
            lambda: _paginate(client, 'list_secret_version_ids', 'Versions', SecretId=secret_id),
            {'ResourceNotFoundException'},
        )
        return {
            'name': metadata.get('Name'),
            'arn': metadata.get('ARN'),
            'description': metadata.get('Description'),
            'kms_key_id': metadata.get('KmsKeyId'),
            'created': metadata.get('CreatedDate'),
            'last_changed': metadata.get('LastChangedDate'),
            'last_accessed': metadata.get('LastAccessedDate'),
            'last_rotated': metadata.get('LastRotatedDate'),
            'next_rotation': metadata.get('NextRotationDate'),
            'deleted': metadata.get('DeletedDate'),
            'rotation_enabled': metadata.get('RotationEnabled'),
            'rotation_lambda_arn': metadata.get('RotationLambdaARN'),
            'rotation_rules': metadata.get('RotationRules'),
            'version_ids_to_stages': metadata.get('VersionIdsToStages'),
            'tags': metadata.get('Tags', []),
            'versions': versions,
        }

    if len(secrets) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(secrets))) as executor:
            detailed_secrets = list(executor.map(secret_detail, secrets))
    else:
        detailed_secrets = [secret_detail(secret) for secret in secrets]

    return {
        'summary': {
            'secrets': len(detailed_secrets),
            'scheduled_for_deletion': sum(1 for secret in detailed_secrets if secret.get('deleted')),
            'rotation_enabled': sum(1 for secret in detailed_secrets if secret.get('rotation_enabled')),
            'versions': sum(
                len(secret.get('versions') or [])
                for secret in detailed_secrets
                if isinstance(secret.get('versions'), list)
            ),
        },
        'secrets': detailed_secrets,
        'supported': [
            'CreateSecret',
            'GetSecretValue',
            'PutSecretValue',
            'UpdateSecret',
            'DescribeSecret',
            'ListSecrets',
            'DeleteSecret',
            'RotateSecret',
            'ListSecretVersionIds',
            'RestoreSecret',
            'UpdateSecretVersionStage',
            'GetRandomPassword',
            'TagResource',
            'UntagResource',
            'BatchGetSecretValue',
        ],
        'configuration': {
            'default_recovery_window_days': 30,
            'protocol': 'JSON 1.1, X-Amz-Target: secretsmanager.*',
            'value_display': 'Secret values are retrieved only on explicit request.',
        },
        'notes': [
            'Floci 1.5.31 supports filters and pagination in BatchGetSecretValue.',
            'Floci 1.5.27 implements automatic secret rotation lifecycle behavior.',
            'Floci 1.5.28 registers AWSPENDING before invoking the rotation Lambda.',
        ],
    }


def opensearch_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    opensearch = factory.client('opensearch')

    operations = set(opensearch.meta.service_model.operation_names)

    def call_if_supported(operation_name: str, result_key: str, **kwargs) -> Any:
        if operation_name not in operations:
            return []

        operation = getattr(opensearch, xform_name(operation_name))
        return _safe_value(lambda: _clean_response(operation(**kwargs)).get(result_key, []), [])

    def paginate_if_supported(operation_name: str, result_key: str, **kwargs) -> list[Any]:
        if operation_name not in operations:
            return []

        return _safe_value(lambda: _paginate(opensearch, xform_name(operation_name), result_key, **kwargs), [])

    domain_names = call_if_supported('ListDomainNames', 'DomainNames')
    names = [domain.get('DomainName') for domain in domain_names if domain.get('DomainName')]
    domain_statuses: list[dict[str, Any]] = []
    for chunk in _chunks(names, 5):
        domain_statuses.extend(
            _safe_value(lambda chunk=chunk: opensearch.describe_domains(DomainNames=chunk).get('DomainStatusList', []), [])
        )

    def domain_detail(status: dict[str, Any]) -> dict[str, Any]:
        name = status.get('DomainName')
        arn = status.get('ARN') or status.get('DomainArn')
        config = _safe_value(lambda name=name: _clean_response(opensearch.describe_domain_config(DomainName=name).get('DomainConfig', {})), {}) if name else {}
        health = _safe_value(lambda name=name: _clean_response(opensearch.describe_domain_health(DomainName=name)), {}) if name else {}
        nodes = _safe_value(lambda name=name: _clean_response(opensearch.describe_domain_nodes(DomainName=name).get('DomainNodesStatusList', [])), []) if name else []
        packages = paginate_if_supported('ListPackagesForDomain', 'DomainPackageDetailsList', DomainName=name) if name else []
        maintenances = paginate_if_supported('ListDomainMaintenances', 'DomainMaintenances', DomainName=name) if name else []
        scheduled_actions = paginate_if_supported('ListScheduledActions', 'ScheduledActions', DomainName=name) if name else []
        endpoint_access = paginate_if_supported('ListVpcEndpointAccess', 'AuthorizedPrincipalList', DomainName=name) if name else []
        data_sources = call_if_supported('ListDataSources', 'DataSources', DomainName=name) if name else []
        tags = _safe_value(lambda arn=arn: opensearch.list_tags(ARN=arn).get('TagList', []), []) if arn else []

        return {
            'name': name,
            'arn': arn,
            'engine_version': status.get('EngineVersion') or status.get('ElasticsearchVersion'),
            'created': status.get('Created'),
            'deleted': status.get('Deleted'),
            'processing': status.get('Processing'),
            'upgrade_processing': status.get('UpgradeProcessing'),
            'endpoint': status.get('Endpoint'),
            'endpoints': status.get('Endpoints'),
            'cluster_config': status.get('ClusterConfig'),
            'ebs_options': status.get('EBSOptions'),
            'vpc_options': status.get('VPCOptions'),
            'endpoint_options': status.get('DomainEndpointOptions'),
            'advanced_security_options': status.get('AdvancedSecurityOptions'),
            'encryption_at_rest_options': status.get('EncryptionAtRestOptions'),
            'node_to_node_encryption_options': status.get('NodeToNodeEncryptionOptions'),
            'service_software_options': status.get('ServiceSoftwareOptions'),
            'health': health,
            'config': config,
            'nodes': nodes,
            'package_count': len(packages),
            'maintenance_count': len(maintenances),
            'scheduled_action_count': len(scheduled_actions),
            'vpc_endpoint_principal_count': len(endpoint_access),
            'data_source_count': len(data_sources),
            'packages': packages,
            'maintenance': maintenances,
            'scheduled_actions': scheduled_actions,
            'vpc_endpoint_access': endpoint_access,
            'data_sources': data_sources,
            'tags': tags,
        }

    domains = [domain_detail(status) for status in domain_statuses]
    packages = paginate_if_supported('DescribePackages', 'PackageDetailsList')
    versions = paginate_if_supported('ListVersions', 'Versions')
    vpc_endpoint_summaries = paginate_if_supported('ListVpcEndpoints', 'VpcEndpointSummaryList')
    vpc_endpoint_ids = [endpoint.get('VpcEndpointId') for endpoint in vpc_endpoint_summaries if endpoint.get('VpcEndpointId')]
    vpc_endpoints: list[dict[str, Any]] = []
    for chunk in _chunks(vpc_endpoint_ids, 25):
        vpc_endpoints.extend(
            _safe_value(lambda chunk=chunk: _clean_response(opensearch.describe_vpc_endpoints(VpcEndpointIds=chunk).get('VpcEndpoints', [])), [])
        )
    if not vpc_endpoints:
        vpc_endpoints = vpc_endpoint_summaries

    inbound_connections = paginate_if_supported('DescribeInboundConnections', 'Connections')
    outbound_connections = paginate_if_supported('DescribeOutboundConnections', 'Connections')
    applications = call_if_supported('ListApplications', 'ApplicationSummaries')
    direct_query_data_sources = paginate_if_supported('ListDirectQueryDataSources', 'DirectQueryDataSources')
    nodes = [
        {
            'name': node.get('NodeId') or node.get('NodeName'),
            'domain_name': domain.get('name'),
            'node_id': node.get('NodeId'),
            'availability_zone': node.get('AvailabilityZone'),
            'instance_type': node.get('InstanceType'),
            'node_status': node.get('NodeStatus'),
            'storage_type': node.get('StorageType'),
            'storage_volume_type': node.get('StorageVolumeType'),
            'node_options': node,
        }
        for domain in domains
        for node in domain.get('nodes', [])
    ]

    return {
        'summary': {
            'domains': len(domains),
            'nodes': len(nodes),
            'packages': len(packages),
            'versions': len(versions),
            'vpc_endpoints': len(vpc_endpoints),
            'inbound_connections': len(inbound_connections),
            'outbound_connections': len(outbound_connections),
            'applications': len(applications),
            'direct_query_data_sources': len(direct_query_data_sources),
        },
        'domains': domains,
        'nodes': nodes,
        'packages': packages,
        'versions': versions,
        'vpc_endpoints': vpc_endpoints,
        'inbound_connections': inbound_connections,
        'outbound_connections': outbound_connections,
        'applications': applications,
        'direct_query_data_sources': direct_query_data_sources,
        'supported_from_sdk': [
            'CreateDomain',
            'DescribeDomain',
            'DescribeDomains',
            'DescribeDomainConfig',
            'DescribeDomainHealth',
            'DescribeDomainNodes',
            'ListDomainNames',
            'DeleteDomain',
            'CreatePackage',
            'DescribePackages',
            'ListPackagesForDomain',
            'AssociatePackage',
            'CreateVpcEndpoint',
            'ListVpcEndpoints',
            'DescribeVpcEndpoints',
            'CreateOutboundConnection',
            'DescribeInboundConnections',
            'DescribeOutboundConnections',
            'ListVersions',
            'ListTags',
        ],
        'notes': [
            'Floci OpenSearch supports mock metadata mode and real container-backed domains exposed on ports 9400-9499.',
            'Supported interactive workflows include domain lifecycle, config updates, upgrades, tags, versions, and instance type limits.',
            'Real mode picks an OpenSearch or Elasticsearch image from the requested EngineVersion and marks the domain ready after cluster health is green or yellow.',
        ],
    }


def pipes_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    pipes = factory.client('pipes')
    summaries = _safe_value(lambda: _paginate(pipes, 'list_pipes', 'Pipes'), [])

    def pipe_detail(summary: dict[str, Any]) -> dict[str, Any]:
        name = summary.get('Name')
        detail = _safe_value(lambda name=name: _clean_response(pipes.describe_pipe(Name=name)), {}) if name else {}
        arn = detail.get('Arn') or summary.get('Arn')
        tags = detail.get('Tags')
        if tags is None and arn:
            tags = _safe_value(lambda arn=arn: pipes.list_tags_for_resource(resourceArn=arn).get('tags', {}), {})

        return {
            'name': name,
            'arn': arn,
            'description': detail.get('Description') or summary.get('Description'),
            'desired_state': detail.get('DesiredState') or summary.get('DesiredState'),
            'current_state': detail.get('CurrentState') or summary.get('CurrentState'),
            'state_reason': detail.get('StateReason') or summary.get('StateReason'),
            'source': detail.get('Source') or summary.get('Source'),
            'source_parameters': detail.get('SourceParameters'),
            'enrichment': detail.get('Enrichment'),
            'enrichment_parameters': detail.get('EnrichmentParameters'),
            'target': detail.get('Target') or summary.get('Target'),
            'target_parameters': detail.get('TargetParameters'),
            'role_arn': detail.get('RoleArn'),
            'log_configuration': detail.get('LogConfiguration'),
            'kms_key_identifier': detail.get('KmsKeyIdentifier'),
            'created': detail.get('CreationTime') or summary.get('CreationTime'),
            'last_modified': detail.get('LastModifiedTime') or summary.get('LastModifiedTime'),
            'tags': tags or {},
        }

    pipe_details = [pipe_detail(summary) for summary in summaries]
    states: dict[str, int] = {}
    for pipe in pipe_details:
        state = pipe.get('current_state') or 'UNKNOWN'
        states[state] = states.get(state, 0) + 1

    return {
        'summary': {
            'pipes': len(pipe_details),
            'running': states.get('RUNNING', 0),
            'stopped': states.get('STOPPED', 0),
            'creating': states.get('CREATING', 0),
            'deleting': states.get('DELETING', 0),
        },
        'pipes': pipe_details,
        'states': [{'name': state, 'state': state, 'count': count} for state, count in sorted(states.items())],
        'supported_from_sdk': [
            'CreatePipe',
            'DescribePipe',
            'ListPipes',
            'StartPipe',
            'StopPipe',
            'UpdatePipe',
            'DeletePipe',
            'ListTagsForResource',
            'TagResource',
            'UntagResource',
        ],
        'supported_sources': [
            'SQS queues',
            'Kinesis streams',
            'DynamoDB streams',
            'Kafka topics (MSK)',
        ],
        'supported_targets': [
            'Lambda functions',
            'SQS queues',
            'SNS topics',
            'Kinesis streams',
            'Step Functions state machines',
        ],
        'pipe_states': [
            'STARTING',
            'RUNNING',
            'STOPPING',
            'STOPPED',
            'DELETED',
        ],
        'notes': [
            'EventBridge Pipes uses a REST-JSON API against the local Floci endpoint.',
            'Pipe summaries are expanded with DescribePipe so source, enrichment, target, logging, role, KMS, and tag data are visible.',
            'Supported sources include SQS, Kinesis, DynamoDB streams, and Kafka topics.',
            'Floci 1.5.28 adds Kafka pipe sources and polling support.',
            'Supported targets include Lambda, SQS, SNS, Kinesis, and Step Functions.',
        ],
    }


def pricing_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    pricing = factory.client('pricing')
    operations = set(pricing.meta.service_model.operation_names)

    services = _safe_value(
        lambda: _paginate(pricing, 'describe_services', 'Services'),
        [],
    ) if 'DescribeServices' in operations else []
    service_details = [
        {
            'name': service.get('ServiceCode'),
            'service_code': service.get('ServiceCode'),
            'attribute_names': service.get('AttributeNames', []),
            'attribute_count': len(service.get('AttributeNames', [])),
        }
        for service in services
    ]

    attribute_values = []
    for service in service_details[:10]:
        service_code = service.get('service_code')
        for attribute_name in service.get('attribute_names', [])[:5]:
            values = _safe_value(
                lambda service_code=service_code, attribute_name=attribute_name: _paginate(
                    pricing,
                    'get_attribute_values',
                    'AttributeValues',
                    ServiceCode=service_code,
                    AttributeName=attribute_name,
                ),
                [],
            ) if service_code and 'GetAttributeValues' in operations else []
            attribute_values.append({
                'name': f'{service_code}.{attribute_name}',
                'service_code': service_code,
                'attribute_name': attribute_name,
                'values': values[:25],
                'value_count': len(values),
            })

    price_lists = _safe_value(
        lambda: _paginate(pricing, 'list_price_lists', 'PriceLists', ServiceCode='AmazonS3'),
        [],
    ) if 'ListPriceLists' in operations else []

    return {
        'summary': {
            'services': len(service_details),
            'attribute_value_samples': len(attribute_values),
            'price_lists': len(price_lists),
            'available_sdk_operations': len(operations),
        },
        'services': service_details,
        'attribute_values': attribute_values,
        'price_lists': _clean_response(price_lists),
        'supported_from_sdk': [
            operation
            for operation in [
                'DescribeServices',
                'GetAttributeValues',
                'GetProducts',
                'ListPriceLists',
                'GetPriceListFileUrl',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.15 adds AWS Price List service support under the pricing service name.',
            'Attribute values are sampled from the first 10 services and first 5 attributes per service to keep page loads bounded.',
        ],
    }


def neptune_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    neptune = factory.client('neptune')
    operations = set(neptune.meta.service_model.operation_names)

    clusters = _safe_value(
        lambda: _operation_items(neptune, 'describe_db_clusters', 'DBClusters'),
        [],
    ) if 'DescribeDBClusters' in operations else []
    instances = _safe_value(
        lambda: _operation_items(neptune, 'describe_db_instances', 'DBInstances'),
        [],
    ) if 'DescribeDBInstances' in operations else []
    subnet_groups = _safe_value(
        lambda: _operation_items(neptune, 'describe_db_subnet_groups', 'DBSubnetGroups'),
        [],
    ) if 'DescribeDBSubnetGroups' in operations else []
    cluster_snapshots = _safe_value(
        lambda: _operation_items(neptune, 'describe_db_cluster_snapshots', 'DBClusterSnapshots'),
        [],
    ) if 'DescribeDBClusterSnapshots' in operations else []

    return {
        'summary': {
            'clusters': len(clusters),
            'instances': len(instances),
            'subnet_groups': len(subnet_groups),
            'cluster_snapshots': len(cluster_snapshots),
            'available_sdk_operations': len(operations),
        },
        'clusters': _clean_response(clusters),
        'instances': _clean_response(instances),
        'subnet_groups': _clean_response(subnet_groups),
        'cluster_snapshots': _clean_response(cluster_snapshots),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateDBCluster',
                'DeleteDBCluster',
                'DescribeDBClusters',
                'ModifyDBCluster',
                'CreateDBInstance',
                'DeleteDBInstance',
                'DescribeDBInstances',
                'ModifyDBInstance',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci manages real Apache TinkerPop Gremlin Server Docker containers and proxies Gremlin connections to them.',
            'Floci 1.5.27 can use a neo4j backend for openCypher queries when NEPTUNE_DB_TYPE selects it.',
            'The management API uses AWS-compatible Query XML through localhost:4566; the data plane is exposed as localhost:<proxy-port> over TCP/WebSocket.',
            'The first cluster claims the configured proxy base port, commonly 8182, and each additional cluster increments within the configured range.',
            'IAM database authentication, Neptune Analytics, serverless auto-pause/resume, snapshots, and restore operations are out of scope.',
        ],
    }


def resourcegroupstagging_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    tagging = factory.client('resourcegroupstaggingapi')
    resources = _safe_value(
        lambda: _paginate(tagging, 'get_resources', 'ResourceTagMappingList', IncludeComplianceDetails=True),
        [],
    )
    tag_keys = _safe_value(lambda: _paginate(tagging, 'get_tag_keys', 'TagKeys'), [])
    tag_values = []
    for key in tag_keys[:25]:
        values = _safe_value(lambda key=key: _paginate(tagging, 'get_tag_values', 'TagValues', Key=key), [])
        tag_values.append({
            'name': key,
            'key': key,
            'values': values,
            'value_count': len(values),
        })

    resource_type_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    resource_details = []
    for mapping in resources:
        arn = mapping.get('ResourceARN')
        arn_parts = arn.split(':') if arn else []
        resource_type = arn_parts[2] if len(arn_parts) > 2 else 'unknown'
        resource_type_counts[resource_type] = resource_type_counts.get(resource_type, 0) + 1
        tags = mapping.get('Tags', [])
        for tag in tags:
            key = tag.get('Key')
            if key:
                tag_counts[key] = tag_counts.get(key, 0) + 1

        resource_details.append({
            'name': arn.rsplit('/', 1)[-1].rsplit(':', 1)[-1] if arn else None,
            'arn': arn,
            'resource_type': resource_type,
            'tags': tags,
            'tag_count': len(tags),
            'compliance_details': mapping.get('ComplianceDetails'),
        })

    resource_types = [
        {'name': resource_type, 'resource_type': resource_type, 'count': count}
        for resource_type, count in sorted(resource_type_counts.items())
    ]
    top_tags = [
        {'name': key, 'key': key, 'resource_count': count}
        for key, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:50]
    ]

    return {
        'summary': {
            'tagged_resources': len(resource_details),
            'tag_keys': len(tag_keys),
            'tag_value_keys_sampled': len(tag_values),
            'resource_types': len(resource_types),
        },
        'resources': resource_details,
        'resource_types': resource_types,
        'tag_keys': [{'name': key, 'key': key} for key in tag_keys],
        'tag_values': tag_values,
        'top_tags': top_tags,
        'supported_operations': [
            'GetResources',
            'GetTagKeys',
            'GetTagValues',
            'TagResources',
            'UntagResources',
        ],
        'notes': [
            'Floci stores tags for arbitrary AWS-shaped ARNs without validating that the referenced resource exists.',
            'GetResources supports ARN, tag, resource type, and pagination filters.',
            'Global ARNs with an empty region segment, such as S3 bucket ARNs, are visible across regions.',
            'Tag values are sampled for the first 25 tag keys to keep page loads bounded.',
            'AWS Organizations tag policy enforcement and persistent storage across restarts are out of scope.',
        ],
    }


def ssm_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    ssm = factory.client('ssm')

    parameters = _safe_value(lambda: _paginate(ssm, 'describe_parameters', 'Parameters'), [])
    documents = _safe_value(lambda: _paginate(ssm, 'list_documents', 'DocumentIdentifiers'), [])
    instances = _safe_value(lambda: _paginate(ssm, 'describe_instance_information', 'InstanceInformationList'), [])
    sessions = _safe_value(lambda: _paginate(ssm, 'describe_sessions', 'Sessions', State='History'), [])
    automation_executions = _safe_value(lambda: _paginate(ssm, 'describe_automation_executions', 'AutomationExecutionMetadataList'), [])
    maintenance_windows = _safe_value(lambda: _paginate(ssm, 'describe_maintenance_windows', 'WindowIdentities'), [])
    patch_baselines = _safe_value(lambda: _paginate(ssm, 'describe_patch_baselines', 'BaselineIdentities'), [])
    default_patch_baselines = [
        {
            'name': operating_system,
            'operating_system': operating_system,
            'baseline_id': response.get('BaselineId'),
        }
        for operating_system in [
            'AMAZON_LINUX',
            'AMAZON_LINUX_2',
            'AMAZON_LINUX_2022',
            'AMAZON_LINUX_2023',
            'CENTOS',
            'DEBIAN',
            'MACOS',
            'ORACLE_LINUX',
            'RASPBIAN',
            'REDHAT_ENTERPRISE_LINUX',
            'ROCKY_LINUX',
            'SUSE',
            'UBUNTU',
            'WINDOWS',
        ]
        for response in [_safe_value(lambda operating_system=operating_system: ssm.get_default_patch_baseline(OperatingSystem=operating_system), {})]
        if response.get('BaselineId')
    ]
    associations = _safe_value(lambda: _paginate(ssm, 'list_associations', 'Associations'), [])
    commands = _safe_value(lambda: _paginate(ssm, 'list_commands', 'Commands'), [])
    command_invocations = _safe_value(lambda: _paginate(ssm, 'list_command_invocations', 'CommandInvocations', Details=True), [])
    compliance_summaries = _safe_value(lambda: _paginate(ssm, 'list_compliance_summaries', 'ComplianceSummaryItems'), [])
    resource_compliance_summaries = _safe_value(lambda: _paginate(ssm, 'list_resource_compliance_summaries', 'ResourceComplianceSummaryItems'), [])
    data_syncs = _safe_value(lambda: _paginate(ssm, 'list_resource_data_sync', 'ResourceDataSyncItems'), [])
    ops_items = _safe_value(lambda: _paginate(ssm, 'describe_ops_items', 'OpsItemSummaries'), [])
    ops_metadata = _safe_value(lambda: _paginate(ssm, 'list_ops_metadata', 'OpsMetadataList'), [])

    def parameter_detail(parameter: dict[str, Any]) -> dict[str, Any]:
        name = parameter.get('Name')
        tags = _safe_value(lambda name=name: ssm.list_tags_for_resource(ResourceType='Parameter', ResourceId=name).get('TagList', []), []) if name else []
        return {
            'name': name,
            'type': parameter.get('Type'),
            'key_id': parameter.get('KeyId'),
            'last_modified': parameter.get('LastModifiedDate'),
            'last_modified_user': parameter.get('LastModifiedUser'),
            'description': parameter.get('Description'),
            'version': parameter.get('Version'),
            'tier': parameter.get('Tier'),
            'policies': parameter.get('Policies'),
            'data_type': parameter.get('DataType'),
            'tags': tags,
        }

    def document_detail(document: dict[str, Any]) -> dict[str, Any]:
        name = document.get('Name')
        detail = _safe_value(lambda name=name: _clean_response(ssm.describe_document(Name=name).get('Document', {})), {}) if name else {}
        return {
            'name': name,
            'owner': detail.get('Owner') or document.get('Owner'),
            'platform_types': detail.get('PlatformTypes') or document.get('PlatformTypes'),
            'document_version': detail.get('DocumentVersion') or document.get('DocumentVersion'),
            'document_type': detail.get('DocumentType') or document.get('DocumentType'),
            'schema_version': detail.get('SchemaVersion') or document.get('SchemaVersion'),
            'document_format': detail.get('DocumentFormat') or document.get('DocumentFormat'),
            'target_type': detail.get('TargetType') or document.get('TargetType'),
            'status': detail.get('Status'),
            'created': detail.get('CreatedDate') or document.get('CreatedDate'),
            'description': detail.get('Description'),
            'parameters': detail.get('Parameters'),
            'tags': detail.get('Tags'),
        }

    parameter_details = [parameter_detail(parameter) for parameter in parameters]
    document_details = [document_detail(document) for document in documents[:50]]
    parameter_types: dict[str, int] = {}
    for parameter in parameter_details:
        parameter_type = parameter.get('type') or 'Unknown'
        parameter_types[parameter_type] = parameter_types.get(parameter_type, 0) + 1

    return {
        'summary': {
            'parameters': len(parameter_details),
            'documents': len(documents),
            'managed_instances': len(instances),
            'sessions': len(sessions),
            'automations': len(automation_executions),
            'maintenance_windows': len(maintenance_windows),
            'patch_baselines': len(patch_baselines),
            'default_patch_baselines': len(default_patch_baselines),
            'associations': len(associations),
            'commands': len(commands),
            'ops_items': len(ops_items),
        },
        'parameters': parameter_details,
        'parameter_types': [{'name': key, 'type': key, 'count': count} for key, count in sorted(parameter_types.items())],
        'documents': document_details,
        'document_identifiers': documents,
        'managed_instances': instances,
        'sessions': sessions,
        'automation_executions': automation_executions,
        'maintenance_windows': maintenance_windows,
        'patch_baselines': patch_baselines,
        'default_patch_baselines': default_patch_baselines,
        'associations': associations,
        'commands': commands,
        'command_invocations': command_invocations,
        'compliance_summaries': compliance_summaries,
        'resource_compliance_summaries': resource_compliance_summaries,
        'resource_data_syncs': data_syncs,
        'ops_items': ops_items,
        'ops_metadata': ops_metadata,
        'supported_from_sdk': [
            'PutParameter',
            'GetParameter',
            'GetParameters',
            'GetParametersByPath',
            'DescribeParameters',
            'DeleteParameter',
            'CreateDocument',
            'DescribeDocument',
            'ListDocuments',
            'SendCommand',
            'ListCommands',
            'ListCommandInvocations',
            'StartSession',
            'DescribeSessions',
            'StartAutomationExecution',
            'DescribeAutomationExecutions',
            'CreateMaintenanceWindow',
            'DescribeMaintenanceWindows',
            'CreatePatchBaseline',
            'DescribePatchBaselines',
            'GetDefaultPatchBaseline',
            'ListAssociations',
            'DescribeInstanceInformation',
            'ListTagsForResource',
        ],
        'notes': [
            'This page is inferred from the SSM AWS SDK API because service docs were not provided for this pass.',
            'Parameter values are intentionally not fetched; the page shows metadata, types, tiers, policies, and tags.',
            'Document details are expanded for the first 50 document identifiers to keep page loads bounded.',
            'AWS-owned patch baselines are shown separately from the per-operating-system defaults returned by GetDefaultPatchBaseline.',
            'Floci 1.5.26 executes SendCommand in EC2 containers and adds patch baseline discovery APIs.',
            'Floci 1.5.27 rejects send-command timeouts below the AWS minimum.',
            'Floci 1.5.29 retains local service diagnostics on command failure.',
        ],
    }


def route53_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    route53 = factory.client('route53')
    hosted_zones = _safe_value(lambda: _paginate(route53, 'list_hosted_zones', 'HostedZones'), [])
    health_checks = _safe_value(lambda: _paginate(route53, 'list_health_checks', 'HealthChecks'), [])
    traffic_policies = _safe_value(lambda: route53.list_traffic_policies().get('TrafficPolicySummaries', []), [])
    traffic_policy_instances = _safe_value(
        lambda: route53.list_traffic_policy_instances().get('TrafficPolicyInstances', []),
        [],
    )
    delegation_sets = _safe_value(
        lambda: route53.list_reusable_delegation_sets().get('DelegationSets', []),
        [],
    )

    def zone_detail(zone: dict[str, Any]) -> dict[str, Any]:
        zone_id = zone.get('Id')
        clean_zone_id = zone_id.rsplit('/', 1)[-1] if zone_id else None
        details = _safe_value(lambda: route53.get_hosted_zone(Id=zone_id), {}) if zone_id else {}
        records = _safe_value(
            lambda: _paginate(route53, 'list_resource_record_sets', 'ResourceRecordSets', HostedZoneId=zone_id),
            [],
        ) if zone_id else []
        query_logging_configs = _safe_value(
            lambda: route53.list_query_logging_configs(HostedZoneId=clean_zone_id).get('QueryLoggingConfigs', []),
            [],
        ) if clean_zone_id else []

        return {
            'name': zone.get('Name'),
            'id': zone_id,
            'clean_id': clean_zone_id,
            'caller_reference': zone.get('CallerReference'),
            'private_zone': zone.get('Config', {}).get('PrivateZone'),
            'comment': zone.get('Config', {}).get('Comment'),
            'resource_record_set_count': zone.get('ResourceRecordSetCount'),
            'delegation_set': details.get('DelegationSet'),
            'vpcs': details.get('VPCs'),
            'query_logging_configs': query_logging_configs,
            'record_count': len(records),
            'records': _clean_response(records),
        }

    detailed_zones = [zone_detail(zone) for zone in hosted_zones]

    return {
        'summary': {
            'hosted_zones': len(detailed_zones),
            'record_sets': sum(zone.get('record_count') or 0 for zone in detailed_zones),
            'health_checks': len(health_checks),
            'traffic_policies': len(traffic_policies),
            'traffic_policy_instances': len(traffic_policy_instances),
            'delegation_sets': len(delegation_sets),
            'private_zones': sum(1 for zone in detailed_zones if zone.get('private_zone')),
        },
        'hosted_zones': detailed_zones,
        'health_checks': _clean_response(health_checks),
        'traffic_policies': _clean_response(traffic_policies),
        'traffic_policy_instances': _clean_response(traffic_policy_instances),
        'delegation_sets': _clean_response(delegation_sets),
        'supported': [
            'CreateHostedZone',
            'GetHostedZone',
            'ListHostedZones',
            'DeleteHostedZone',
            'ChangeResourceRecordSets',
            'ListResourceRecordSets',
            'ListHealthChecks',
            'CreateHealthCheck',
            'DeleteHealthCheck',
            'ListTrafficPolicies',
            'ListTrafficPolicyInstances',
            'ListReusableDelegationSets',
        ],
        'notes': [
            'Route 53 inventory expands hosted zones with record sets and query logging configuration when available.',
            'Route 53 is a global service; this dashboard still uses the configured local endpoint.',
        ],
    }


def transfer_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    transfer = factory.client('transfer')
    servers = _safe_value(lambda: _paginate(transfer, 'list_servers', 'Servers'), [])
    workflows = _safe_value(lambda: _paginate(transfer, 'list_workflows', 'Workflows'), [])
    profiles = _safe_value(lambda: _paginate(transfer, 'list_profiles', 'Profiles'), [])
    certificates = _safe_value(lambda: _paginate(transfer, 'list_certificates', 'Certificates'), [])
    connectors = _safe_value(lambda: _paginate(transfer, 'list_connectors', 'Connectors'), [])
    security_policies = _safe_value(lambda: _paginate(transfer, 'list_security_policies', 'SecurityPolicyNames'), [])
    web_apps = _safe_value(lambda: _paginate(transfer, 'list_web_apps', 'WebApps'), [])

    def server_detail(server: dict[str, Any]) -> dict[str, Any]:
        server_id = server.get('ServerId')
        details = _safe_value(lambda: transfer.describe_server(ServerId=server_id).get('Server', {}), {}) if server_id else {}
        users = _safe_value(lambda: _paginate(transfer, 'list_users', 'Users', ServerId=server_id), []) if server_id else []
        host_keys = _safe_value(lambda: transfer.list_host_keys(ServerId=server_id).get('HostKeys', []), []) if server_id else []
        agreements = _safe_value(lambda: _paginate(transfer, 'list_agreements', 'Agreements', ServerId=server_id), []) if server_id else []

        return {
            'name': server_id,
            'id': server_id,
            'arn': server.get('Arn') or details.get('Arn'),
            'state': server.get('State') or details.get('State'),
            'endpoint_type': server.get('EndpointType') or details.get('EndpointType'),
            'domain': server.get('Domain') or details.get('Domain'),
            'identity_provider_type': server.get('IdentityProviderType') or details.get('IdentityProviderType'),
            'protocols': details.get('Protocols'),
            'endpoint_details': details.get('EndpointDetails'),
            'logging_role': details.get('LoggingRole'),
            'structured_log_destinations': details.get('StructuredLogDestinations'),
            'security_policy_name': details.get('SecurityPolicyName'),
            'workflow_details': details.get('WorkflowDetails'),
            'certificate': details.get('Certificate'),
            'tags': details.get('Tags'),
            'user_count': len(users),
            'users': _clean_response(users),
            'host_key_count': len(host_keys),
            'host_keys': _clean_response(host_keys),
            'agreement_count': len(agreements),
            'agreements': _clean_response(agreements),
        }

    def profile_detail(profile: dict[str, Any]) -> dict[str, Any]:
        profile_id = profile.get('ProfileId')
        details = _safe_value(lambda: transfer.describe_profile(ProfileId=profile_id).get('Profile', {}), {}) if profile_id else {}
        return {
            'name': profile_id,
            'id': profile_id,
            'arn': profile.get('Arn') or details.get('Arn'),
            'as2_id': details.get('As2Id'),
            'profile_type': details.get('ProfileType'),
            'certificate_ids': details.get('CertificateIds'),
            'tags': details.get('Tags'),
        }

    def certificate_detail(certificate: dict[str, Any]) -> dict[str, Any]:
        certificate_id = certificate.get('CertificateId')
        details = _safe_value(
            lambda: transfer.describe_certificate(CertificateId=certificate_id).get('Certificate', {}),
            {},
        ) if certificate_id else {}
        return {
            'name': certificate_id,
            'id': certificate_id,
            'arn': certificate.get('Arn') or details.get('Arn'),
            'status': details.get('Status'),
            'type': details.get('Type'),
            'usage': details.get('Usage'),
            'certificate': details.get('Certificate'),
            'active_date': details.get('ActiveDate'),
            'inactive_date': details.get('InactiveDate'),
            'serial': details.get('Serial'),
            'not_before_date': details.get('NotBeforeDate'),
            'not_after_date': details.get('NotAfterDate'),
            'description': details.get('Description'),
            'tags': details.get('Tags'),
        }

    def connector_detail(connector: dict[str, Any]) -> dict[str, Any]:
        connector_id = connector.get('ConnectorId')
        details = _safe_value(
            lambda: transfer.describe_connector(ConnectorId=connector_id).get('Connector', {}),
            {},
        ) if connector_id else {}
        return {
            'name': connector_id,
            'id': connector_id,
            'arn': connector.get('Arn') or details.get('Arn'),
            'url': details.get('Url'),
            'as2_config': details.get('As2Config'),
            'access_role': details.get('AccessRole'),
            'logging_role': details.get('LoggingRole'),
            'security_policy_name': details.get('SecurityPolicyName'),
            'tags': details.get('Tags'),
        }

    detailed_servers = [server_detail(server) for server in servers]
    detailed_profiles = [profile_detail(profile) for profile in profiles]
    detailed_certificates = [certificate_detail(certificate) for certificate in certificates]
    detailed_connectors = [connector_detail(connector) for connector in connectors]

    return {
        'summary': {
            'servers': len(detailed_servers),
            'users': sum(server.get('user_count') or 0 for server in detailed_servers),
            'host_keys': sum(server.get('host_key_count') or 0 for server in detailed_servers),
            'agreements': sum(server.get('agreement_count') or 0 for server in detailed_servers),
            'workflows': len(workflows),
            'profiles': len(detailed_profiles),
            'certificates': len(detailed_certificates),
            'connectors': len(detailed_connectors),
            'security_policies': len(security_policies),
            'web_apps': len(web_apps),
        },
        'servers': detailed_servers,
        'workflows': _clean_response(workflows),
        'profiles': detailed_profiles,
        'certificates': detailed_certificates,
        'connectors': detailed_connectors,
        'security_policies': _clean_response(security_policies),
        'web_apps': _clean_response(web_apps),
        'supported': [
            'CreateServer',
            'DescribeServer',
            'UpdateServer',
            'ListServers',
            'StartServer',
            'StopServer',
            'DeleteServer',
            'CreateUser',
            'DescribeUser',
            'UpdateUser',
            'ListUsers',
            'DeleteUser',
            'ImportSshPublicKey',
            'DeleteSshPublicKey',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'notes': [
            'Floci Transfer Family covers the management-plane API only; actual SFTP, FTP, FTPS, and AS2 file transfer is not emulated.',
            'Server EndpointType defaults to PUBLIC, and State transitions between ONLINE and OFFLINE through StartServer and StopServer.',
            'SSH key bodies are stored and returned as-is without cryptographic validation.',
            'Server ARNs follow arn:aws:transfer:{region}:{accountId}:server/{serverId}; user ARNs include user/{serverId}/{userName}.',
        ],
    }


def _sns_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def sns_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    sns = factory.client('sns')
    topics = _safe_value(lambda: _paginate(sns, 'list_topics', 'Topics'), [])
    subscriptions = _safe_value(lambda: _paginate(sns, 'list_subscriptions', 'Subscriptions'), [])

    def topic_name(topic_arn: Optional[str]) -> str:
        return topic_arn.rsplit(':', 1)[-1] if topic_arn else 'Unnamed topic'

    def topic_detail(topic: dict[str, Any]) -> dict[str, Any]:
        arn = topic.get('TopicArn')
        topic_subscriptions = _sns_optional(
            lambda: _paginate(sns, 'list_subscriptions_by_topic', 'Subscriptions', TopicArn=arn),
            {'NotFound', 'InvalidParameter'},
        )

        return {
            'name': topic_name(arn),
            'arn': arn,
            'attributes': _sns_optional(
                lambda: sns.get_topic_attributes(TopicArn=arn).get('Attributes', {}),
                {'NotFound', 'InvalidParameter'},
            ),
            'tags': _sns_optional(
                lambda: sns.list_tags_for_resource(ResourceArn=arn).get('Tags', []),
                {'NotFound', 'InvalidParameter'},
            ),
            'subscriptions': topic_subscriptions,
            'subscription_count': len(topic_subscriptions) if isinstance(topic_subscriptions, list) else 0,
        }

    def subscription_detail(subscription: dict[str, Any]) -> dict[str, Any]:
        arn = subscription.get('SubscriptionArn')
        attributes = None

        if arn and arn != 'PendingConfirmation':
            attributes = _sns_optional(
                lambda: sns.get_subscription_attributes(SubscriptionArn=arn).get('Attributes', {}),
                {'NotFound', 'InvalidParameter'},
            )

        return {
            'name': arn or subscription.get('Endpoint') or 'Subscription',
            'arn': arn,
            'topic_arn': subscription.get('TopicArn'),
            'protocol': subscription.get('Protocol'),
            'endpoint': subscription.get('Endpoint'),
            'owner': subscription.get('Owner'),
            'attributes': attributes,
        }

    detailed_topics = [topic_detail(topic) for topic in topics]
    detailed_subscriptions = [subscription_detail(subscription) for subscription in subscriptions]

    return {
        'summary': {
            'topics': len(detailed_topics),
            'subscriptions': len(detailed_subscriptions),
            'sqs_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('protocol') == 'sqs'),
            'lambda_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('protocol') == 'lambda'),
            'http_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('protocol') in {'http', 'https'}),
            'pending_subscriptions': sum(1 for subscription in detailed_subscriptions if subscription.get('arn') == 'PendingConfirmation'),
        },
        'topics': detailed_topics,
        'subscriptions': detailed_subscriptions,
        'supported': [
            'CreateTopic',
            'DeleteTopic',
            'ListTopics',
            'GetTopicAttributes',
            'SetTopicAttributes',
            'Subscribe',
            'Unsubscribe',
            'ListSubscriptions',
            'ListSubscriptionsByTopic',
            'GetSubscriptionAttributes',
            'SetSubscriptionAttributes',
            'ConfirmSubscription',
            'Publish',
            'PublishBatch',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'fanout': [
            'Floci supports SNS to SQS fan-out and delivers published messages immediately.',
            'Floci 1.5.22 adds mocked iOS and Android mobile push platform endpoints.',
            'Floci 1.5.19 creates SNS to SQS subscriptions from CloudFormation templates, preserves binary message attributes, and reports per-entry PublishBatch failures.',
            'Floci 1.5.18 delivers http:// and https:// subscriptions with confirmation handshakes, message POSTs, and signature headers.',
            'Floci 1.5.17 supports FilterPolicyScope=MessageBody for body-based subscription filtering.',
        ],
        'protocols': ['Query XML', 'JSON 1.0'],
    }


def _ses_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def _ses_mailbox(factory: FlociClientFactory) -> Any:
    mailbox_url = f'{factory.endpoint_url.rstrip("/")}/_aws/ses'

    try:
        with urlopen(mailbox_url, timeout=2) as response:
            body = response.read().decode('utf-8')
    except URLError as exc:
        return {'error': str(exc.reason)}
    except TimeoutError as exc:
        return {'error': str(exc)}

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {'raw': body}


def _ses_message_preview(message: dict[str, Any]) -> dict[str, Any]:
    body = message.get('body') or message.get('Body') or message.get('text') or message.get('html')
    preview = None

    if isinstance(body, str):
        preview = body[:160]

    return {
        'id': message.get('id') or message.get('Id') or message.get('messageId') or message.get('MessageId'),
        'from': message.get('from') or message.get('From') or message.get('source') or message.get('Source'),
        'to': message.get('to') or message.get('To') or message.get('destination') or message.get('Destination'),
        'subject': message.get('subject') or message.get('Subject'),
        'timestamp': message.get('timestamp') or message.get('Timestamp'),
        'body_preview': preview,
    }


def _sesv2_items(client, operation_name: str, result_key: str) -> list[Any]:
    # SES v2 list operations expose NextToken without SDK paginator definitions.
    items = []
    tokens = set()
    options = {}
    while True:
        response = getattr(client, operation_name)(**options)
        items.extend(response.get(result_key, []))
        token = response.get('NextToken')
        if not token:
            return items
        if token in tokens:
            raise ValueError(f'{operation_name} returned a repeated NextToken')
        tokens.add(token)
        options = {'NextToken': token}


def ses_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    ses = factory.client('ses')
    sesv2 = factory.client('sesv2')
    sesv2_operations = set(sesv2.meta.service_model.operation_names)
    identities = _safe_value(lambda: _paginate(ses, 'list_identities', 'Identities'), [])
    templates = _safe_value(lambda: _paginate(ses, 'list_templates', 'TemplatesMetadata'), [])
    verified_emails = _safe_value(lambda: ses.list_verified_email_addresses().get('VerifiedEmailAddresses', []), [])
    identity_attributes: dict[str, Any] = {}

    for batch in _chunks(identities, 100):
        response = _ses_optional(
            lambda batch=batch: ses.get_identity_verification_attributes(Identities=batch).get('VerificationAttributes', {}),
            {'InvalidParameterValue'},
        )
        if isinstance(response, dict):
            identity_attributes.update(response)

    notification_attributes: dict[str, Any] = {}
    for batch in _chunks(identities, 100):
        response = _ses_optional(
            lambda batch=batch: ses.get_identity_notification_attributes(Identities=batch).get('NotificationAttributes', {}),
            {'InvalidParameterValue'},
        )
        if isinstance(response, dict):
            notification_attributes.update(response)

    dkim_attributes: dict[str, Any] = {}
    for batch in _chunks(identities, 100):
        response = _ses_optional(
            lambda batch=batch: ses.get_identity_dkim_attributes(Identities=batch).get('DkimAttributes', {}),
            {'InvalidParameterValue'},
        )
        if isinstance(response, dict):
            dkim_attributes.update(response)

    detailed_identities = [
        {
            'name': identity,
            'type': 'domain' if '@' not in identity else 'email',
            'verification': identity_attributes.get(identity),
            'notifications': notification_attributes.get(identity),
            'dkim': dkim_attributes.get(identity),
        }
        for identity in identities
    ]

    detailed_templates = [
        {
            'name': template.get('Name'),
            'created': template.get('CreatedTimestamp'),
            'details': _ses_optional(
                lambda template=template: ses.get_template(TemplateName=template.get('Name')).get('Template', {}),
                {'TemplateDoesNotExist'},
            ) if template.get('Name') else None,
        }
        for template in templates
    ]

    mailbox = _ses_mailbox(factory)
    messages: list[dict[str, Any]] = []
    if isinstance(mailbox, list):
        messages = [_ses_message_preview(message) for message in mailbox if isinstance(message, dict)]
    elif isinstance(mailbox, dict):
        raw_messages = mailbox.get('messages') or mailbox.get('Messages') or mailbox.get('items') or mailbox.get('Items') or []
        if isinstance(raw_messages, list):
            messages = [_ses_message_preview(message) for message in raw_messages if isinstance(message, dict)]

    v2_identities = _ses_optional(
        lambda: _sesv2_items(sesv2, 'list_email_identities', 'EmailIdentities'),
        {'NotFoundException', 'BadRequestException'},
    )
    v2_templates = _ses_optional(
        lambda: _sesv2_items(sesv2, 'list_email_templates', 'TemplatesMetadata'),
        {'NotFoundException', 'BadRequestException'},
    )
    v2_configuration_sets = _ses_optional(
        lambda: sesv2.list_configuration_sets().get('ConfigurationSets', []),
        {'NotFoundException', 'BadRequestException'},
    )
    v2_contact_lists = _ses_optional(
        lambda: _operation_items(sesv2, 'list_contact_lists', 'ContactLists'),
        {'NotFoundException', 'BadRequestException'},
    ) if 'ListContactLists' in sesv2_operations else []

    def configuration_set_name(configuration_set: Any) -> str | None:
        if isinstance(configuration_set, str):
            return configuration_set
        if isinstance(configuration_set, dict):
            return configuration_set.get('Name') or configuration_set.get('ConfigurationSetName')
        return None

    detailed_v2_configuration_sets: list[dict[str, Any]] | Any = v2_configuration_sets
    if isinstance(v2_configuration_sets, list):
        detailed_v2_configuration_sets = []
        for configuration_set in v2_configuration_sets:
            name = configuration_set_name(configuration_set)
            details = _ses_optional(
                lambda name=name: sesv2.get_configuration_set(ConfigurationSetName=name),
                {'NotFoundException', 'BadRequestException'},
            ) if name else None
            event_destinations = _ses_optional(
                lambda name=name: sesv2.get_configuration_set_event_destinations(
                    ConfigurationSetName=name,
                ).get('EventDestinations', []),
                {'NotFoundException', 'BadRequestException'},
            ) if name else []
            sending_options = details.get('SendingOptions') if isinstance(details, dict) else None
            sending_enabled = None
            if isinstance(sending_options, dict):
                sending_enabled = sending_options.get('SendingEnabled')
            elif isinstance(details, dict) and 'SendingEnabled' in details:
                sending_enabled = details.get('SendingEnabled')
            detailed_v2_configuration_sets.append({
                'name': name,
                'details': details,
                'sending_enabled': sending_enabled,
                'sending_options': sending_options,
                'delivery_options': details.get('DeliveryOptions') if isinstance(details, dict) else None,
                'reputation_options': details.get('ReputationOptions') if isinstance(details, dict) else None,
                'suppression_options': details.get('SuppressionOptions') if isinstance(details, dict) else None,
                'tracking_options': details.get('TrackingOptions') if isinstance(details, dict) else None,
                'event_destinations': event_destinations,
                'event_destination_count': len(event_destinations) if isinstance(event_destinations, list) else 0,
            })

    detailed_v2_contact_lists: list[dict[str, Any]] | Any = v2_contact_lists
    if isinstance(v2_contact_lists, list):
        detailed_v2_contact_lists = []
        for contact_list in v2_contact_lists:
            name = contact_list.get('ContactListName')
            details = _ses_optional(
                lambda name=name: sesv2.get_contact_list(ContactListName=name),
                {'NotFoundException', 'BadRequestException'},
            ) if name and 'GetContactList' in sesv2_operations else None
            contacts = _ses_optional(
                lambda name=name: _operation_items(sesv2, 'list_contacts', 'Contacts', ContactListName=name),
                {'NotFoundException', 'BadRequestException'},
            ) if name and 'ListContacts' in sesv2_operations else []
            detailed_v2_contact_lists.append({
                'name': name,
                'description': contact_list.get('Description') or (
                    details.get('Description') if isinstance(details, dict) else None
                ),
                'created': contact_list.get('CreatedTimestamp') or (
                    details.get('CreatedTimestamp') if isinstance(details, dict) else None
                ),
                'updated': contact_list.get('LastUpdatedTimestamp') or (
                    details.get('LastUpdatedTimestamp') if isinstance(details, dict) else None
                ),
                'topics': details.get('Topics') if isinstance(details, dict) else None,
                'contacts': contacts,
                'contact_count': len(contacts) if isinstance(contacts, list) else 0,
                'details': details,
            })

    return {
        'summary': {
            'identities': len(detailed_identities),
            'verified_email_addresses': len(verified_emails),
            'templates': len(detailed_templates),
            'captured_messages': len(messages),
            'v2_identities': len(v2_identities) if isinstance(v2_identities, list) else 0,
            'v2_templates': len(v2_templates) if isinstance(v2_templates, list) else 0,
            'v2_configuration_sets': (
                len(detailed_v2_configuration_sets) if isinstance(detailed_v2_configuration_sets, list) else 0
            ),
            'v2_contact_lists': (
                len(detailed_v2_contact_lists) if isinstance(detailed_v2_contact_lists, list) else 0
            ),
            'v2_contacts': (
                sum(item.get('contact_count') or 0 for item in detailed_v2_contact_lists)
                if isinstance(detailed_v2_contact_lists, list) else 0
            ),
            'v2_event_destinations': (
                sum(item.get('event_destination_count') or 0 for item in detailed_v2_configuration_sets)
                if isinstance(detailed_v2_configuration_sets, list) else 0
            ),
        },
        'identities': detailed_identities,
        'verified_email_addresses': _string_items(verified_emails, 'email'),
        'templates': detailed_templates,
        'send_quota': _ses_optional(lambda: ses.get_send_quota(), set()),
        'send_statistics': _ses_optional(lambda: ses.get_send_statistics().get('SendDataPoints', []), set()),
        'account': _ses_optional(lambda: sesv2.get_account(), set()) if 'GetAccount' in sesv2_operations else None,
        'account_sending_enabled': _ses_optional(lambda: ses.get_account_sending_enabled(), set()),
        'mailbox': {
            'url': f'{factory.endpoint_url.rstrip("/")}/_aws/ses',
            'messages': messages,
            'raw': mailbox if isinstance(mailbox, dict) and mailbox.get('error') else None,
        },
        'v2_identities': v2_identities,
        'v2_templates': v2_templates,
        'v2_configuration_sets': detailed_v2_configuration_sets,
        'v2_contact_lists': detailed_v2_contact_lists,
        'supported_v1': [
            'VerifyEmailIdentity',
            'VerifyEmailAddress',
            'VerifyDomainIdentity',
            'DeleteIdentity',
            'ListIdentities',
            'GetIdentityVerificationAttributes',
            'SendEmail',
            'SendRawEmail',
            'SendTemplatedEmail',
            'CreateTemplate',
            'GetTemplate',
            'UpdateTemplate',
            'DeleteTemplate',
            'ListTemplates',
            'GetSendQuota',
            'GetSendStatistics',
            'GetAccountSendingEnabled',
            'ListVerifiedEmailAddresses',
            'DeleteVerifiedEmailAddress',
            'SetIdentityNotificationTopic',
            'GetIdentityNotificationAttributes',
            'GetIdentityDkimAttributes',
        ],
        'supported_v2': [
            'CreateEmailIdentity',
            'ListEmailIdentities',
            'GetEmailIdentity',
            'DeleteEmailIdentity',
            'PutEmailIdentityDkimAttributes',
            'PutEmailIdentityFeedbackAttributes',
            'SendEmail',
            'GetAccount',
            'PutAccountSendingAttributes',
            'PutAccountSuppressionAttributes',
            'CreateEmailTemplate',
            'ListEmailTemplates',
            'GetEmailTemplate',
            'UpdateEmailTemplate',
            'DeleteEmailTemplate',
            'CreateConfigurationSet',
            'ListConfigurationSets',
            'GetConfigurationSet',
            'DeleteConfigurationSet',
            'CreateConfigurationSetEventDestination',
            'GetConfigurationSetEventDestinations',
            'UpdateConfigurationSetEventDestination',
            'DeleteConfigurationSetEventDestination',
            'CreateContactList',
            'ListContactLists',
            'GetContactList',
            'DeleteContactList',
            'CreateContact',
            'ListContacts',
            'GetContact',
            'UpdateContact',
            'DeleteContact',
        ],
        'inspection_endpoints': [
            'GET /_aws/ses',
            'GET /_aws/ses?id=<message-id>',
            'DELETE /_aws/ses',
        ],
        'configuration': {
            'smtp_host': None,
            'smtp_port': 25,
            'smtp_starttls': 'DISABLED',
        },
        'environment_variables': [
            'FLOCI_SERVICES_SES_ENABLED',
            'FLOCI_SERVICES_SES_SMTP_HOST',
            'FLOCI_SERVICES_SES_SMTP_PORT',
            'FLOCI_SERVICES_SES_SMTP_USER',
            'FLOCI_SERVICES_SES_SMTP_PASS',
            'FLOCI_SERVICES_SES_SMTP_STARTTLS',
        ],
        'notes': [
            'Identity verification succeeds immediately in Floci.',
            'Floci 1.5.30 implements SES v2 ContactList CRUD.',
            'Emails are always stored locally and can be inspected through /_aws/ses.',
            'Floci 1.5.29 makes FromEmailAddress optional for SES v2 SendEmail.',
            'Floci 1.5.28 publishes SNS notifications to identity feedback topics and implements SES v1 PutConfigurationSetDeliveryOptions.',
            'SMTP relay failures are logged but do not affect the SES API response.',
            'SES v1 and SES v2 share identity, template, and sent-message state.',
            'Floci 1.5.27 adds SES v2 dedicated IP pools, configuration-set option groups, v1 configuration-set tracking, and reputation-metrics options.',
            'Floci 1.5.25 persists inline options on SES v2 CreateConfigurationSet, keeps MAIL FROM attributes only when configured, and omits unknown identities from GetIdentityNotificationAttributes.',
            'Floci 1.5.24 publishes SES events to Firehose, EventBridge, and CloudWatch destinations in addition to existing SNS event destination support.',
            'Floci 1.5.22 publishes SES events to SNS configuration set destinations, adds v1 destination CRUD, and enforces suppression lists at send time with per-configuration-set overrides.',
            'Floci 1.5.23 adds per-configuration-set sending toggles for SES v1 and SES v2.',
            'Floci 1.5.21 adds SES v2 configuration set event destination operations.',
            'Floci 1.5.17 adds SES v2 PutAccountSuppressionAttributes.',
        ],
    }


def _rds_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def rds_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    rds = factory.client('rds')
    raw_instances = _rds_optional(lambda: _paginate(rds, 'describe_db_instances', 'DBInstances'), set())
    raw_clusters = _rds_optional(lambda: _paginate(rds, 'describe_db_clusters', 'DBClusters'), set())
    raw_groups = _rds_optional(lambda: _paginate(rds, 'describe_db_parameter_groups', 'DBParameterGroups'), set())
    issues = [f'{label}: {value["error"]}' for label, value in [('Databases', raw_instances), ('Clusters', raw_clusters), ('Parameter groups', raw_groups)] if isinstance(value, dict) and value.get('error')]
    instances = raw_instances if isinstance(raw_instances, list) else []
    clusters = raw_clusters if isinstance(raw_clusters, list) else []
    parameter_groups = raw_groups if isinstance(raw_groups, list) else []

    def instance_detail(instance: dict[str, Any]) -> dict[str, Any]:
        endpoint = instance.get('Endpoint') or {}
        return {
            'name': instance.get('DBInstanceIdentifier'),
            'arn': instance.get('DBInstanceArn'),
            'status': instance.get('DBInstanceStatus'),
            'engine': instance.get('Engine'),
            'engine_version': instance.get('EngineVersion'),
            'class': instance.get('DBInstanceClass'),
            'allocated_storage': instance.get('AllocatedStorage'),
            'storage_type': instance.get('StorageType'),
            'multi_az': instance.get('MultiAZ'),
            'publicly_accessible': instance.get('PubliclyAccessible'),
            'iam_authentication': instance.get('IAMDatabaseAuthenticationEnabled'),
            'master_username': instance.get('MasterUsername'),
            'endpoint': endpoint,
            'connect_host': endpoint.get('Address'),
            'connect_port': endpoint.get('Port'),
            'db_name': instance.get('DBName'),
            'parameter_groups': instance.get('DBParameterGroups'),
            'subnet_group': instance.get('DBSubnetGroup'),
            'security_groups': instance.get('VpcSecurityGroups'),
            'created': instance.get('InstanceCreateTime'),
            'cluster_identifier': instance.get('DBClusterIdentifier'),
            'availability_zone': instance.get('AvailabilityZone'),
            'secondary_availability_zone': instance.get('SecondaryAvailabilityZone'),
            'storage_encrypted': instance.get('StorageEncrypted'),
            'deletion_protection': instance.get('DeletionProtection'),
            'backup_retention_period': instance.get('BackupRetentionPeriod'),
            'pending_modifications': instance.get('PendingModifiedValues'),
            'tags': _rds_optional(
                lambda: rds.list_tags_for_resource(ResourceName=instance.get('DBInstanceArn')).get('TagList', []),
                {'DBInstanceNotFound', 'InvalidParameterValue'},
            ) if instance.get('DBInstanceArn') else None,
        }

    def cluster_detail(cluster: dict[str, Any]) -> dict[str, Any]:
        return {
            'name': cluster.get('DBClusterIdentifier'),
            'arn': cluster.get('DBClusterArn'),
            'status': cluster.get('Status'),
            'engine': cluster.get('Engine'),
            'engine_version': cluster.get('EngineVersion'),
            'database_name': cluster.get('DatabaseName'),
            'master_username': cluster.get('MasterUsername'),
            'endpoint': cluster.get('Endpoint'),
            'reader_endpoint': cluster.get('ReaderEndpoint'),
            'storage_encrypted': cluster.get('StorageEncrypted'),
            'deletion_protection': cluster.get('DeletionProtection'),
            'multi_az': cluster.get('MultiAZ'),
            'availability_zones': cluster.get('AvailabilityZones'),
            'subnet_group': cluster.get('DBSubnetGroup'),
            'security_groups': cluster.get('VpcSecurityGroups'),
            'backup_retention_period': cluster.get('BackupRetentionPeriod'),
            'port': cluster.get('Port'),
            'members': cluster.get('DBClusterMembers'),
            'parameter_group': cluster.get('DBClusterParameterGroup'),
            'created': cluster.get('ClusterCreateTime'),
            'iam_authentication': cluster.get('IAMDatabaseAuthenticationEnabled'),
            'tags': _rds_optional(
                lambda: rds.list_tags_for_resource(ResourceName=cluster.get('DBClusterArn')).get('TagList', []),
                {'DBClusterNotFoundFault', 'InvalidParameterValue'},
            ) if cluster.get('DBClusterArn') else None,
        }

    def parameter_group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group.get('DBParameterGroupName')
        parameters = _rds_optional(
            lambda: _paginate(rds, 'describe_db_parameters', 'Parameters', DBParameterGroupName=name),
            {'DBParameterGroupNotFound'},
        )

        return {
            'name': name,
            'arn': group.get('DBParameterGroupArn'),
            'family': group.get('DBParameterGroupFamily'),
            'description': group.get('Description'),
            'parameters': parameters,
            'parameter_count': len(parameters) if isinstance(parameters, list) else 0,
        }

    detailed_instances = [instance_detail(instance) for instance in instances]
    detailed_clusters = [cluster_detail(cluster) for cluster in clusters]
    detailed_parameter_groups = [parameter_group_detail(group) for group in parameter_groups]

    return {
        'issues': issues,
        'summary': {
            'instances': len(detailed_instances),
            'clusters': len(detailed_clusters),
            'parameter_groups': len(detailed_parameter_groups),
            'available_instances': sum(1 for instance in detailed_instances if instance.get('status') == 'available'),
            'proxy_endpoints': sum(1 for instance in detailed_instances if instance.get('connect_port')),
        },
        'instances': detailed_instances,
        'clusters': detailed_clusters,
        'parameter_groups': detailed_parameter_groups,
        'supported': [
            'CreateDBInstance',
            'DescribeDBInstances',
            'DeleteDBInstance',
            'ModifyDBInstance',
            'RebootDBInstance',
            'CreateDBCluster',
            'DescribeDBClusters',
            'DeleteDBCluster',
            'ModifyDBCluster',
            'CreateDBParameterGroup',
            'DescribeDBParameterGroups',
            'DeleteDBParameterGroup',
            'ModifyDBParameterGroup',
            'DescribeDBParameters',
        ],
        'supported_engines': [
            {'name': 'postgres', 'default_image': 'postgres:16-alpine'},
            {'name': 'mysql', 'default_image': 'mysql:8.0'},
            {'name': 'mariadb', 'default_image': 'mariadb:11'},
        ],
        'configuration': {
            'proxy_base_port': 7001,
            'proxy_max_port': 7099,
            'default_postgres_image': 'postgres:16-alpine',
            'default_mysql_image': 'mysql:8.0',
            'default_mariadb_image': 'mariadb:11',
        },
        'environment_variables': [
            'FLOCI_SERVICES_RDS_ENABLED',
            'FLOCI_SERVICES_RDS_PROXY_BASE_PORT',
            'FLOCI_SERVICES_RDS_PROXY_MAX_PORT',
            'FLOCI_SERVICES_RDS_DEFAULT_POSTGRES_IMAGE',
            'FLOCI_SERVICES_RDS_DEFAULT_MYSQL_IMAGE',
            'FLOCI_SERVICES_RDS_DEFAULT_MARIADB_IMAGE',
            'FLOCI_SERVICES_DOCKER_NETWORK',
            'FLOCI_STORAGE_SERVICES_RDS_MODE',
        ],
        'notes': [
            'RDS management uses Query XML and the data plane uses PostgreSQL or MySQL wire protocol.',
            'Floci manages real PostgreSQL, MySQL, and MariaDB Docker containers.',
            'Floci 1.5.31 adds RDS mock mode so clusters and instances can be created without Docker for CI and fast unit-style tests.',
            'Instances expose local TCP proxy endpoints on localhost:<proxy-port>.',
            'Floci 1.5.28 aligns CreateDBInstance/CreateDBCluster parameter group validation, subnet group placement, and requested database-name proxy handling with AWS behavior.',
            'Floci 1.5.25 adds RDS provisioning lifecycle support for create, describe, modify, and delete flows.',
            'Floci 1.5.25 persists STS session secret keys so RDS IAM database authentication validates across requests.',
            'Floci 1.5.21 restores persisted runtime state on startup for local RDS resources.',
            'IAM database authentication is supported when enabled at instance creation time.',
        ],
    }


def _stepfunctions_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def stepfunctions_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    stepfunctions = factory.client('stepfunctions')
    operations = set(stepfunctions.meta.service_model.operation_names)
    state_machines = _safe_value(lambda: _paginate(stepfunctions, 'list_state_machines', 'stateMachines'), [])

    def execution_detail(execution: dict[str, Any]) -> dict[str, Any]:
        arn = execution.get('executionArn')
        details = _stepfunctions_optional(
            lambda: stepfunctions.describe_execution(executionArn=arn),
            {'ExecutionDoesNotExist'},
        )
        history = _stepfunctions_optional(
            lambda: stepfunctions.get_execution_history(executionArn=arn, maxResults=25).get('events', []),
            {'ExecutionDoesNotExist'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}

        return {
            'name': execution.get('name') or described.get('name') or arn,
            'arn': arn,
            'state_machine_arn': execution.get('stateMachineArn') or described.get('stateMachineArn'),
            'status': execution.get('status') or described.get('status'),
            'started': execution.get('startDate') or described.get('startDate'),
            'stopped': execution.get('stopDate') or described.get('stopDate'),
            'input': described.get('input'),
            'output': described.get('output'),
            'trace_header': described.get('traceHeader'),
            'history': history,
            'history_event_count': len(history) if isinstance(history, list) else 0,
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def state_machine_detail(machine: dict[str, Any]) -> dict[str, Any]:
        arn = machine.get('stateMachineArn')
        details = _stepfunctions_optional(
            lambda: stepfunctions.describe_state_machine(stateMachineArn=arn),
            {'StateMachineDoesNotExist'},
        )
        executions = _stepfunctions_optional(
            lambda: _paginate(stepfunctions, 'list_executions', 'executions', stateMachineArn=arn),
            {'StateMachineDoesNotExist'},
        )
        versions = _stepfunctions_optional(
            lambda: _operation_items(stepfunctions, 'list_state_machine_versions', 'stateMachineVersions', stateMachineArn=arn),
            {'StateMachineDoesNotExist'},
        ) if 'ListStateMachineVersions' in operations else []
        described = details if isinstance(details, dict) and not details.get('error') else {}
        execution_details = [
            execution_detail(execution)
            for execution in executions
        ] if isinstance(executions, list) else []
        version_details = [
            {
                'arn': version.get('stateMachineVersionArn'),
                'revision_id': version.get('revisionId'),
                'creation_date': version.get('creationDate'),
            }
            for version in versions
        ] if isinstance(versions, list) else []

        return {
            'name': machine.get('name') or described.get('name') or arn,
            'arn': arn,
            'type': machine.get('type') or described.get('type'),
            'status': machine.get('status') or described.get('status'),
            'created': machine.get('creationDate') or described.get('creationDate'),
            'role_arn': described.get('roleArn'),
            'definition': described.get('definition'),
            'logging_configuration': described.get('loggingConfiguration'),
            'tracing_configuration': described.get('tracingConfiguration'),
            'revision_id': described.get('revisionId'),
            'label': described.get('label'),
            'versions': version_details,
            'version_count': len(version_details),
            'executions': execution_details,
            'execution_count': len(execution_details),
            'running_executions': sum(1 for execution in execution_details if execution.get('status') == 'RUNNING'),
            'succeeded_executions': sum(1 for execution in execution_details if execution.get('status') == 'SUCCEEDED'),
            'failed_executions': sum(1 for execution in execution_details if execution.get('status') == 'FAILED'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_state_machines = [state_machine_detail(machine) for machine in state_machines]

    return {
        'summary': {
            'state_machines': len(detailed_state_machines),
            'executions': sum(machine.get('execution_count') or 0 for machine in detailed_state_machines),
            'versions': sum(machine.get('version_count') or 0 for machine in detailed_state_machines),
            'running': sum(machine.get('running_executions') or 0 for machine in detailed_state_machines),
            'succeeded': sum(machine.get('succeeded_executions') or 0 for machine in detailed_state_machines),
            'failed': sum(machine.get('failed_executions') or 0 for machine in detailed_state_machines),
        },
        'state_machines': detailed_state_machines,
        'supported': [
            'CreateStateMachine',
            'DescribeStateMachine',
            'ListStateMachines',
            'DeleteStateMachine',
            'StartExecution',
            'DescribeExecution',
            'ListExecutions',
            'StopExecution',
            'GetExecutionHistory',
            'PublishStateMachineVersion',
            'ListStateMachineVersions',
            'DeleteStateMachineVersion',
            'SendTaskSuccess',
            'SendTaskFailure',
            'SendTaskHeartbeat',
        ],
        'protocol': 'JSON 1.1',
        'notes': [
            'Floci exposes Step Functions through the AmazonStatesService JSON 1.1 target.',
            'Floci 1.5.31 supports Map ItemReader for S3-backed JSON datasets and resolves qualified Lambda function ARNs in lambda:invoke service integrations.',
            'Floci 1.5.30 adds state machine version APIs, ECS runTask integration, ResultSelector, ResultPath wildcard projection, ArrayContains, and account-aware execution routing.',
            'Floci 1.5.29 adds aws-sdk integrations for CloudFormation, EC2, and S3 PutObject.',
            'Floci 1.5.28 returns no execution data through the SDK where AWS omits it.',
            'Execution histories are previewed with a small event limit on the dashboard.',
            'Task token callbacks are available for waitForTaskToken workflows.',
        ],
    }


def _scheduler_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def scheduler_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    scheduler = factory.client('scheduler')
    groups = _safe_value(lambda: _paginate(scheduler, 'list_schedule_groups', 'ScheduleGroups'), [])
    schedules = _safe_value(lambda: _paginate(scheduler, 'list_schedules', 'Schedules'), [])

    def group_name(group: dict[str, Any]) -> str:
        return group.get('Name') or group.get('Arn') or 'default'

    def group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group_name(group)
        details = _scheduler_optional(
            lambda: scheduler.get_schedule_group(Name=name),
            {'ResourceNotFoundException'},
        )
        group_schedules = _scheduler_optional(
            lambda: _paginate(scheduler, 'list_schedules', 'Schedules', GroupName=name),
            {'ResourceNotFoundException'},
        )

        return {
            'name': name,
            'arn': group.get('Arn') or (details.get('Arn') if isinstance(details, dict) else None),
            'state': group.get('State') or (details.get('State') if isinstance(details, dict) else None),
            'created': group.get('CreationDate') or (details.get('CreationDate') if isinstance(details, dict) else None),
            'last_modified': group.get('LastModificationDate') or (details.get('LastModificationDate') if isinstance(details, dict) else None),
            'schedule_count': len(group_schedules) if isinstance(group_schedules, list) else 0,
            'schedules': group_schedules,
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def schedule_detail(schedule: dict[str, Any]) -> dict[str, Any]:
        name = schedule.get('Name')
        group = schedule.get('GroupName') or 'default'
        details = _scheduler_optional(
            lambda: scheduler.get_schedule(Name=name, GroupName=group),
            {'ResourceNotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}

        return {
            'name': name,
            'arn': schedule.get('Arn') or described.get('Arn'),
            'group': group,
            'state': schedule.get('State') or described.get('State'),
            'expression': schedule.get('ScheduleExpression') or described.get('ScheduleExpression'),
            'timezone': described.get('ScheduleExpressionTimezone'),
            'start_date': described.get('StartDate'),
            'end_date': described.get('EndDate'),
            'action_after_completion': described.get('ActionAfterCompletion'),
            'flexible_time_window': described.get('FlexibleTimeWindow'),
            'target': described.get('Target') or schedule.get('Target'),
            'created': schedule.get('CreationDate') or described.get('CreationDate'),
            'last_modified': schedule.get('LastModificationDate') or described.get('LastModificationDate'),
            'description': described.get('Description'),
            'kms_key_arn': described.get('KmsKeyArn'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_groups = [group_detail(group) for group in groups]
    detailed_schedules = [schedule_detail(schedule) for schedule in schedules]

    return {
        'summary': {
            'groups': len(detailed_groups),
            'schedules': len(detailed_schedules),
            'enabled': sum(1 for schedule in detailed_schedules if schedule.get('state') == 'ENABLED'),
            'disabled': sum(1 for schedule in detailed_schedules if schedule.get('state') == 'DISABLED'),
        },
        'groups': detailed_groups,
        'schedules': detailed_schedules,
        'supported': [
            'CreateScheduleGroup',
            'GetScheduleGroup',
            'DeleteScheduleGroup',
            'ListScheduleGroups',
            'CreateSchedule',
            'GetSchedule',
            'UpdateSchedule',
            'DeleteSchedule',
            'ListSchedules',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'supported_expressions': [
            'at(YYYY-MM-DDTHH:mm:ss)',
            'rate(N minutes|hours|days|weeks)',
            'cron(minute hour day-of-month month day-of-week year)',
        ],
        'supported_targets': [
            'SQS',
            'Lambda',
            'SNS',
            'ECS RunTask',
            'EventBridge PutEvents',
            'Universal AWS SDK targets for SNS Publish and SQS SendMessage',
        ],
        'not_supported': [
            'RetryPolicy and DeadLetterConfig enforcement',
            'FlexibleTimeWindow jitter',
            'NextToken-based pagination',
        ],
        'configuration': {
            'invocation_enabled': True,
            'tick_interval_seconds': 10,
        },
        'notes': [
            'The default schedule group is created automatically on first access.',
            'Schedules without an explicit group are placed in the default group.',
            'The default group cannot be deleted.',
            'State=DISABLED schedules and schedules outside StartDate/EndDate are skipped.',
            'Floci 1.5.31 dispatches ECS targets to RunTask.',
            'FIFO SQS targets can carry MessageGroupId through Target.SqsParameters.',
        ],
    }


def _textract_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def textract_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    textract = factory.client('textract')
    operations = set(textract.meta.service_model.operation_names)

    def paginate_if_supported(operation_name: str, result_key: str, **kwargs) -> list[Any]:
        if operation_name not in operations:
            return []

        return _safe_value(lambda: _paginate(textract, xform_name(operation_name), result_key, **kwargs), [])

    adapters = paginate_if_supported('ListAdapters', 'Adapters')

    def adapter_detail(adapter: dict[str, Any]) -> dict[str, Any]:
        adapter_id = adapter.get('AdapterId')
        details = _textract_optional(
            lambda: textract.get_adapter(AdapterId=adapter_id),
            {'ResourceNotFoundException', 'InvalidParameterException'},
        ) if adapter_id and 'GetAdapter' in operations else None
        versions = paginate_if_supported('ListAdapterVersions', 'AdapterVersions', AdapterId=adapter_id) if adapter_id else []

        version_details = []
        if adapter_id and 'GetAdapterVersion' in operations:
            for version in versions:
                adapter_version = version.get('AdapterVersion')
                if adapter_version:
                    version_details.append(_textract_optional(
                        lambda adapter_version=adapter_version: textract.get_adapter_version(
                            AdapterId=adapter_id,
                            AdapterVersion=adapter_version,
                        ),
                        {'ResourceNotFoundException', 'InvalidParameterException'},
                    ))

        described = details if isinstance(details, dict) and not details.get('error') else {}
        arn = adapter.get('AdapterArn') or described.get('AdapterArn')

        return {
            'name': adapter.get('AdapterName') or described.get('AdapterName') or adapter_id,
            'adapter_id': adapter_id,
            'arn': arn,
            'description': adapter.get('Description') or described.get('Description'),
            'feature_types': adapter.get('FeatureTypes') or described.get('FeatureTypes'),
            'auto_update': described.get('AutoUpdate'),
            'created': adapter.get('CreationTime') or described.get('CreationTime'),
            'version_count': len(versions),
            'versions': versions,
            'version_details': version_details,
            'tags': described.get('Tags') or (
                _textract_optional(
                    lambda arn=arn: textract.list_tags_for_resource(ResourceARN=arn).get('Tags', {}),
                    {'ResourceNotFoundException', 'InvalidParameterException'},
                ) if arn and 'ListTagsForResource' in operations else None
            ),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_adapters = [adapter_detail(adapter) for adapter in adapters]
    adapter_versions = [
        version
        for adapter in detailed_adapters
        for version in adapter.get('versions', [])
    ]

    supported_operations = [
        'AnalyzeDocument',
        'DetectDocumentText',
        'StartDocumentAnalysis',
        'StartDocumentTextDetection',
        'GetDocumentAnalysis',
        'GetDocumentTextDetection',
    ]

    return {
        'summary': {
            'adapters': len(detailed_adapters),
            'adapter_versions': len(adapter_versions),
            'available_sdk_operations': len(operations),
            'supported_operations': len([operation for operation in supported_operations if operation in operations]),
        },
        'adapters': detailed_adapters,
        'adapter_versions': adapter_versions,
        'supported_from_sdk': supported_operations,
        'available_sdk_operations': sorted(operations),
        'document_operations': [
            'AnalyzeDocument',
            'DetectDocumentText',
        ],
        'async_job_operations': [
            'StartDocumentAnalysis',
            'GetDocumentAnalysis',
            'StartDocumentTextDetection',
            'GetDocumentTextDetection',
        ],
        'stub_blocks': [
            {'BlockType': 'PAGE', 'Text': None, 'Relationships': 'CHILD -> LINE'},
            {'BlockType': 'LINE', 'Text': 'Floci', 'Relationships': 'CHILD -> WORD'},
            {'BlockType': 'WORD', 'Text': 'Floci', 'Relationships': None},
        ],
        'notes': [
            'Floci Textract returns a fixed PAGE, LINE, and WORD block hierarchy with synthetic geometry and 99.9 confidence.',
            'Document and DocumentLocation inputs are accepted but not parsed, so no real OCR or document analysis is performed.',
            'StartDocumentTextDetection and StartDocumentAnalysis return immediately succeeded in-memory jobs.',
            'Job IDs are not persisted across restarts, and text-detection job IDs cannot be fetched through GetDocumentAnalysis or vice versa.',
            'Adapter management, AnalyzeExpense, AnalyzeID, AnalyzeLendingDocument, and async pagination are out of scope.',
        ],
    }


def list_sqs_queues(factory: FlociClientFactory) -> list[dict[str, Any]]:
    urls = factory.client('sqs').list_queues().get('QueueUrls', [])
    return _string_items(urls, 'url')


def list_sns_topics(factory: FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {'arn': topic.get('TopicArn')}
        for topic in factory.client('sns').get_paginator('list_topics').paginate().build_full_result().get('Topics', [])
    ]


def list_ses_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    ses = factory.client('ses')
    resources = [
        {'type': 'identity', 'name': identity}
        for identity in _safe_value(lambda: _paginate(ses, 'list_identities', 'Identities'), [])
    ]
    resources.extend(
        {
            'type': 'template',
            'name': template.get('Name'),
        }
        for template in _safe_value(lambda: _paginate(ses, 'list_templates', 'TemplatesMetadata'), [])
    )
    mailbox = _ses_mailbox(factory)
    if isinstance(mailbox, list):
        resources.extend(
            {
                'type': 'captured_message',
                'id': message.get('id') or message.get('Id') or message.get('messageId') or message.get('MessageId'),
            }
            for message in mailbox
            if isinstance(message, dict)
        )
    elif isinstance(mailbox, dict):
        messages = mailbox.get('messages') or mailbox.get('Messages') or mailbox.get('items') or mailbox.get('Items') or []
        if isinstance(messages, list):
            resources.extend(
                {
                    'type': 'captured_message',
                    'id': message.get('id') or message.get('Id') or message.get('messageId') or message.get('MessageId'),
                }
                for message in messages
                if isinstance(message, dict)
            )
    return resources


def list_rds_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    rds = factory.client('rds')
    resources = [
        {
            'type': 'instance',
            'name': instance.get('DBInstanceIdentifier'),
            'arn': instance.get('DBInstanceArn'),
            'status': instance.get('DBInstanceStatus'),
            'endpoint': instance.get('Endpoint'),
        }
        for instance in _safe_value(lambda: _paginate(rds, 'describe_db_instances', 'DBInstances'), [])
    ]
    resources.extend(
        {
            'type': 'cluster',
            'name': cluster.get('DBClusterIdentifier'),
            'arn': cluster.get('DBClusterArn'),
            'status': cluster.get('Status'),
        }
        for cluster in _safe_value(lambda: _paginate(rds, 'describe_db_clusters', 'DBClusters'), [])
    )
    resources.extend(
        {
            'type': 'parameter_group',
            'name': group.get('DBParameterGroupName'),
            'arn': group.get('DBParameterGroupArn'),
        }
        for group in _safe_value(lambda: _paginate(rds, 'describe_db_parameter_groups', 'DBParameterGroups'), [])
    )
    return resources


def list_stepfunctions_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    stepfunctions = factory.client('stepfunctions')
    resources = [
        {
            'type': 'state_machine',
            'name': machine.get('name'),
            'arn': machine.get('stateMachineArn'),
            'type_name': machine.get('type'),
        }
        for machine in _safe_value(lambda: _paginate(stepfunctions, 'list_state_machines', 'stateMachines'), [])
    ]
    for machine in list(resources):
        arn = machine.get('arn')
        resources.extend(
            {
                'type': 'execution',
                'state_machine': arn,
                'name': execution.get('name'),
                'arn': execution.get('executionArn'),
                'status': execution.get('status'),
            }
            for execution in _safe_value(
                lambda arn=arn: _paginate(stepfunctions, 'list_executions', 'executions', stateMachineArn=arn),
                [],
            )
        )
    return resources


def list_scheduler_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    scheduler = factory.client('scheduler')
    resources = [
        {
            'type': 'schedule_group',
            'name': group.get('Name'),
            'arn': group.get('Arn'),
            'state': group.get('State'),
        }
        for group in _safe_value(lambda: _paginate(scheduler, 'list_schedule_groups', 'ScheduleGroups'), [])
    ]
    resources.extend(
        {
            'type': 'schedule',
            'name': schedule.get('Name'),
            'arn': schedule.get('Arn'),
            'group': schedule.get('GroupName'),
            'state': schedule.get('State'),
            'expression': schedule.get('ScheduleExpression'),
        }
        for schedule in _safe_value(lambda: _paginate(scheduler, 'list_schedules', 'Schedules'), [])
    )
    return resources


def list_lambda_functions(factory: FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': function.get('FunctionName'),
            'runtime': function.get('Runtime'),
            'arn': function.get('FunctionArn'),
        }
        for function in factory.client('lambda').get_paginator('list_functions').paginate().build_full_result().get('Functions', [])
    ]


def list_secrets(factory: FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': secret.get('Name'),
            'arn': secret.get('ARN'),
        }
        for secret in factory.client('secretsmanager').get_paginator('list_secrets').paginate().build_full_result().get('SecretList', [])
    ]


def list_opensearch_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    opensearch = factory.client('opensearch')
    resources: list[dict[str, Any]] = []
    domain_names = _safe_value(lambda: opensearch.list_domain_names().get('DomainNames', []), [])
    names = [domain.get('DomainName') for domain in domain_names if domain.get('DomainName')]
    for chunk in _chunks(names, 5):
        resources.extend(
            {
                'type': 'domain',
                'name': domain.get('DomainName'),
                'arn': domain.get('ARN') or domain.get('DomainArn'),
                'engine_version': domain.get('EngineVersion') or domain.get('ElasticsearchVersion'),
                'processing': domain.get('Processing'),
                'endpoint': domain.get('Endpoint'),
            }
            for domain in _safe_value(lambda chunk=chunk: opensearch.describe_domains(DomainNames=chunk).get('DomainStatusList', []), [])
        )

    resources.extend(
        {
            'type': 'package',
            'name': package.get('PackageName'),
            'id': package.get('PackageID'),
            'status': package.get('PackageStatus'),
            'package_type': package.get('PackageType'),
        }
        for package in _safe_value(lambda: _paginate(opensearch, 'describe_packages', 'PackageDetailsList'), [])
    )
    resources.extend(
        {
            'type': 'vpc_endpoint',
            'name': endpoint.get('VpcEndpointId'),
            'id': endpoint.get('VpcEndpointId'),
            'domain_arn': endpoint.get('DomainArn'),
            'status': endpoint.get('Status'),
        }
        for endpoint in _safe_value(lambda: _paginate(opensearch, 'list_vpc_endpoints', 'VpcEndpointSummaryList'), [])
    )
    return resources


def list_pipes_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    pipes = factory.client('pipes')
    return [
        {
            'type': 'pipe',
            'name': pipe.get('Name'),
            'arn': pipe.get('Arn'),
            'desired_state': pipe.get('DesiredState'),
            'current_state': pipe.get('CurrentState'),
            'source': pipe.get('Source'),
            'target': pipe.get('Target'),
        }
        for pipe in _safe_value(lambda: _paginate(pipes, 'list_pipes', 'Pipes'), [])
    ]


def list_resourcegroupstagging_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    tagging = factory.client('resourcegroupstaggingapi')
    return [
        {
            'type': 'tagged_resource',
            'name': mapping.get('ResourceARN'),
            'arn': mapping.get('ResourceARN'),
            'tag_count': len(mapping.get('Tags', [])),
            'tags': mapping.get('Tags', []),
            'compliance_details': mapping.get('ComplianceDetails'),
        }
        for mapping in _safe_value(
            lambda: _paginate(tagging, 'get_resources', 'ResourceTagMappingList', IncludeComplianceDetails=True),
            [],
        )
    ]


def list_ssm_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    ssm = factory.client('ssm')
    resources = [
        {
            'type': 'parameter',
            'name': parameter.get('Name'),
            'parameter_type': parameter.get('Type'),
            'tier': parameter.get('Tier'),
            'version': parameter.get('Version'),
        }
        for parameter in _safe_value(lambda: _paginate(ssm, 'describe_parameters', 'Parameters'), [])
    ]
    resources.extend(
        {
            'type': 'document',
            'name': document.get('Name'),
            'owner': document.get('Owner'),
            'document_type': document.get('DocumentType'),
            'document_version': document.get('DocumentVersion'),
        }
        for document in _safe_value(lambda: _paginate(ssm, 'list_documents', 'DocumentIdentifiers'), [])
    )
    resources.extend(
        {
            'type': 'managed_instance',
            'id': instance.get('InstanceId'),
            'name': instance.get('Name') or instance.get('InstanceId'),
            'ping_status': instance.get('PingStatus'),
            'platform_type': instance.get('PlatformType'),
        }
        for instance in _safe_value(lambda: _paginate(ssm, 'describe_instance_information', 'InstanceInformationList'), [])
    )
    return resources


def list_route53_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    route53 = factory.client('route53')
    resources = [
        {
            'type': 'hosted_zone',
            'name': zone.get('Name'),
            'id': zone.get('Id'),
            'private_zone': zone.get('Config', {}).get('PrivateZone'),
            'record_sets': zone.get('ResourceRecordSetCount'),
        }
        for zone in _safe_value(lambda: _paginate(route53, 'list_hosted_zones', 'HostedZones'), [])
    ]
    resources.extend(
        {
            'type': 'health_check',
            'name': check.get('Id'),
            'id': check.get('Id'),
            'health_check_version': check.get('HealthCheckVersion'),
            'config': check.get('HealthCheckConfig'),
        }
        for check in _safe_value(lambda: _paginate(route53, 'list_health_checks', 'HealthChecks'), [])
    )
    resources.extend(
        {
            'type': 'traffic_policy',
            'name': policy.get('Name'),
            'id': policy.get('Id'),
            'policy_type': policy.get('Type'),
            'latest_version': policy.get('LatestVersion'),
        }
        for policy in _safe_value(lambda: route53.list_traffic_policies().get('TrafficPolicySummaries', []), [])
    )
    return resources


def list_transfer_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    transfer = factory.client('transfer')
    resources = [
        {
            'type': 'server',
            'name': server.get('ServerId'),
            'arn': server.get('Arn'),
            'state': server.get('State'),
            'endpoint_type': server.get('EndpointType'),
        }
        for server in _safe_value(lambda: _paginate(transfer, 'list_servers', 'Servers'), [])
    ]
    resources.extend(
        {
            'type': 'workflow',
            'name': workflow.get('WorkflowId'),
            'arn': workflow.get('Arn'),
            'description': workflow.get('Description'),
        }
        for workflow in _safe_value(lambda: _paginate(transfer, 'list_workflows', 'Workflows'), [])
    )
    resources.extend(
        {
            'type': 'profile',
            'name': profile.get('ProfileId'),
            'arn': profile.get('Arn'),
            'profile_type': profile.get('ProfileType'),
        }
        for profile in _safe_value(lambda: _paginate(transfer, 'list_profiles', 'Profiles'), [])
    )
    resources.extend(
        {
            'type': 'connector',
            'name': connector.get('ConnectorId'),
            'arn': connector.get('Arn'),
            'url': connector.get('Url'),
        }
        for connector in _safe_value(lambda: _paginate(transfer, 'list_connectors', 'Connectors'), [])
    )
    return resources


def list_pricing_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    pricing = factory.client('pricing')
    operations = set(pricing.meta.service_model.operation_names)

    if 'DescribeServices' not in operations:
        return []

    return [
        {
            'type': 'pricing_service',
            'name': service.get('ServiceCode'),
            'attribute_count': len(service.get('AttributeNames', [])),
        }
        for service in _safe_value(lambda: _paginate(pricing, 'describe_services', 'Services'), [])
    ]


def list_wafv2_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    wafv2 = factory.client('wafv2')
    operations = set(wafv2.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    for scope in ['REGIONAL', 'CLOUDFRONT']:
        if 'ListWebACLs' in operations:
            resources.extend(
                {
                    'type': 'web_acl',
                    'name': acl.get('Name'),
                    'id': acl.get('Id'),
                    'arn': acl.get('ARN'),
                    'scope': scope,
                }
                for acl in _safe_value(
                    lambda scope=scope: _operation_items(wafv2, 'list_web_acls', 'WebACLs', Scope=scope),
                    [],
                )
            )
        if 'ListRuleGroups' in operations:
            resources.extend(
                {
                    'type': 'rule_group',
                    'name': group.get('Name'),
                    'id': group.get('Id'),
                    'arn': group.get('ARN'),
                    'scope': scope,
                }
                for group in _safe_value(
                    lambda scope=scope: _operation_items(wafv2, 'list_rule_groups', 'RuleGroups', Scope=scope),
                    [],
                )
            )

    return resources


def list_rdsdata_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    operations = set(factory.client('rds-data').meta.service_model.operation_names)
    return [
        {
            'type': 'operation',
            'name': operation,
            'status': 'supported',
        }
        for operation in [
            'ExecuteStatement',
            'BatchExecuteStatement',
            'BeginTransaction',
            'CommitTransaction',
            'RollbackTransaction',
        ]
        if operation in operations
    ]


def list_memorydb_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    memorydb = factory.client('memorydb')
    operations = set(memorydb.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'DescribeClusters' in operations:
        resources.extend(
            {
                'type': 'cluster',
                'name': cluster.get('Name'),
                'arn': cluster.get('ARN'),
                'status': cluster.get('Status'),
            }
            for cluster in _safe_value(lambda: _operation_items(memorydb, 'describe_clusters', 'Clusters'), [])
        )
    if 'DescribeUsers' in operations:
        resources.extend(
            {
                'type': 'user',
                'name': user.get('Name'),
                'arn': user.get('ARN'),
                'status': user.get('Status'),
            }
            for user in _safe_value(lambda: _operation_items(memorydb, 'describe_users', 'Users'), [])
        )
    if 'DescribeACLs' in operations:
        resources.extend(
            {
                'type': 'acl',
                'name': acl.get('Name'),
                'arn': acl.get('ARN'),
                'status': acl.get('Status'),
            }
            for acl in _safe_value(lambda: _operation_items(memorydb, 'describe_acls', 'ACLs'), [])
        )

    return resources


def list_s3vectors_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    s3vectors = factory.client('s3vectors')
    operations = set(s3vectors.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListVectorBuckets' in operations:
        buckets = _safe_value(lambda: _operation_items(s3vectors, 'list_vector_buckets', 'vectorBuckets'), [])
        resources.extend(
            {
                'type': 'vector_bucket',
                'name': bucket.get('vectorBucketName') or bucket.get('name'),
                'arn': bucket.get('vectorBucketArn'),
            }
            for bucket in buckets
        )
        if 'ListIndexes' in operations:
            for bucket in buckets[:50]:
                bucket_name = bucket.get('vectorBucketName') or bucket.get('name')
                if not bucket_name:
                    continue
                resources.extend(
                    {
                        'type': 'index',
                        'name': index.get('indexName'),
                        'vector_bucket': bucket_name,
                        'arn': index.get('indexArn'),
                    }
                    for index in _safe_value(
                        lambda bucket_name=bucket_name: _operation_items(s3vectors, 'list_indexes', 'indexes', vectorBucketName=bucket_name),
                        [],
                    )
                )

    return resources


def list_neptune_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    neptune = factory.client('neptune')
    operations = set(neptune.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'DescribeDBClusters' in operations:
        resources.extend(
            {
                'type': 'db_cluster',
                'name': cluster.get('DBClusterIdentifier'),
                'arn': cluster.get('DBClusterArn'),
                'status': cluster.get('Status'),
            }
            for cluster in _safe_value(lambda: _operation_items(neptune, 'describe_db_clusters', 'DBClusters'), [])
        )

    if 'DescribeDBInstances' in operations:
        resources.extend(
            {
                'type': 'db_instance',
                'name': instance.get('DBInstanceIdentifier'),
                'arn': instance.get('DBInstanceArn'),
                'status': instance.get('DBInstanceStatus'),
            }
            for instance in _safe_value(lambda: _operation_items(neptune, 'describe_db_instances', 'DBInstances'), [])
        )

    return resources


def list_transcribe_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    transcribe = factory.client('transcribe')
    operations = set(transcribe.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListTranscriptionJobs' in operations:
        resources.extend(
            {
                'type': 'transcription_job',
                'name': job.get('TranscriptionJobName'),
                'status': job.get('TranscriptionJobStatus'),
                'language_code': job.get('LanguageCode'),
            }
            for job in _safe_value(
                lambda: _paginate(transcribe, 'list_transcription_jobs', 'TranscriptionJobSummaries'),
                [],
            )
        )

    if 'ListVocabularies' in operations:
        resources.extend(
            {
                'type': 'vocabulary',
                'name': vocabulary.get('VocabularyName'),
                'state': vocabulary.get('VocabularyState'),
                'language_code': vocabulary.get('LanguageCode'),
            }
            for vocabulary in _safe_value(lambda: _paginate(transcribe, 'list_vocabularies', 'Vocabularies'), [])
        )

    if 'ListVocabularyFilters' in operations:
        resources.extend(
            {
                'type': 'vocabulary_filter',
                'name': vocabulary_filter.get('VocabularyFilterName'),
                'language_code': vocabulary_filter.get('LanguageCode'),
            }
            for vocabulary_filter in _safe_value(
                lambda: _paginate(transcribe, 'list_vocabulary_filters', 'VocabularyFilters'),
                [],
            )
        )

    return resources


def list_textract_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    textract = factory.client('textract')
    operations = set(textract.meta.service_model.operation_names)

    if 'ListAdapters' not in operations:
        return []

    adapters = [
        {
            'type': 'adapter',
            'name': adapter.get('AdapterName') or adapter.get('AdapterId'),
            'id': adapter.get('AdapterId'),
            'arn': adapter.get('AdapterArn'),
            'feature_types': adapter.get('FeatureTypes'),
        }
        for adapter in _safe_value(lambda: _paginate(textract, 'list_adapters', 'Adapters'), [])
    ]
    resources = list(adapters)

    if 'ListAdapterVersions' in operations:
        for adapter in adapters:
            adapter_id = adapter.get('id')
            if not adapter_id:
                continue

            resources.extend(
                {
                    'type': 'adapter_version',
                    'name': f'{adapter.get("name") or adapter_id}:{version.get("AdapterVersion")}',
                    'id': adapter_id,
                    'adapter_version': version.get('AdapterVersion'),
                    'status': version.get('Status'),
                }
                for version in _safe_value(
                    lambda adapter_id=adapter_id: _paginate(
                        textract,
                        'list_adapter_versions',
                        'AdapterVersions',
                        AdapterId=adapter_id,
                    ),
                    [],
                )
            )

    return resources


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
        ('opensearch-resources', 'OpenSearch resources', list_opensearch_resources),
        ('pipes-resources', 'EventBridge Pipes resources', list_pipes_resources),
        ('pricing-resources', 'AWS Price List resources', list_pricing_resources),
        ('costexplorer-resources', 'Cost Explorer resources', costexplorer.list_costexplorer_resources),
        ('cur-resources', 'Cost and Usage Reports', cur.list_cur_resources),
        ('bcmdataexports-resources', 'BCM Data Exports', bcmdataexports.list_bcmdataexports_resources),
        ('memorydb-resources', 'MemoryDB resources', list_memorydb_resources),
        ('neptune-resources', 'Neptune resources', list_neptune_resources),
        ('resourcegroupstagging-resources', 'Resource Groups Tagging resources', list_resourcegroupstagging_resources),
        ('ssm-resources', 'SSM resources', list_ssm_resources),
        ('cloudformation-resources', 'CloudFormation resources', cloudformation.list_cloudformation_resources),
        ('ecr-resources', 'ECR resources', ecr.list_ecr_resources),
        ('glue-resources', 'Glue resources', glue.list_glue_resources),
        ('rds-resources', 'RDS resources', list_rds_resources),
        ('rdsdata-resources', 'RDS Data API operations', list_rdsdata_resources),
        ('route53-resources', 'Route 53 resources', list_route53_resources),
        ('iam-users', 'IAM users', iam.list_iam_users),
        ('iam-roles', 'IAM roles', iam.list_iam_roles),
        ('iot-resources', 'IoT Core resources', iot.list_iot_resources),
        ('ec2-resources', 'EC2 resources', ec2.list_ec2_resources),
        ('ecs-resources', 'ECS resources', ecs.list_ecs_resources),
        ('s3-buckets', 'S3 buckets', s3.list_s3_buckets),
        ('s3vectors-resources', 'S3 Vectors resources', list_s3vectors_resources),
        ('sqs-queues', 'SQS queues', list_sqs_queues),
        ('dynamodb-tables', 'DynamoDB tables', dynamodb.list_dynamodb_tables),
        ('docdb-resources', 'DocumentDB resources', docdb.list_docdb_resources),
        ('sns-topics', 'SNS topics', list_sns_topics),
        ('ses-resources', 'SES resources', list_ses_resources),
        ('scheduler-resources', 'EventBridge Scheduler resources', list_scheduler_resources),
        ('stepfunctions-resources', 'Step Functions resources', list_stepfunctions_resources),
        ('textract-resources', 'Textract resources', list_textract_resources),
        ('transcribe-resources', 'Transcribe resources', list_transcribe_resources),
        ('transfer-resources', 'Transfer Family resources', list_transfer_resources),
        ('wafv2-resources', 'WAF v2 resources', list_wafv2_resources),
        ('lambda-functions', 'Lambda functions', list_lambda_functions),
        ('kms-keys', 'KMS keys', kms.list_kms_keys),
        ('secrets', 'Secrets Manager secrets', list_secrets),
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
from . import (  # noqa: E402
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
    s3,
)
from .acm import acm_inventory  # noqa: E402
from .amazonmq import amazonmq_inventory  # noqa: E402
from .apigateway import apigateway_inventory  # noqa: E402
from .appconfig import appconfig_inventory  # noqa: E402
from .appsync import appsync_inventory  # noqa: E402
from .athena import athena_inventory  # noqa: E402
from .autoscaling import autoscaling_inventory  # noqa: E402
from .backup import backup_inventory  # noqa: E402
from .batch import batch_inventory  # noqa: E402
from .bcmdataexports import bcmdataexports_inventory  # noqa: E402
from .bedrockruntime import bedrockruntime_inventory  # noqa: E402
from .cloudcontrol import cloudcontrol_inventory  # noqa: E402
from .cloudformation import cloudformation_inventory  # noqa: E402
from .cloudfront import cloudfront_inventory  # noqa: E402
from .cloudmap import cloudmap_inventory  # noqa: E402
from .cloudtrail import cloudtrail_inventory  # noqa: E402
from .cloudwatch import cloudwatch_inventory  # noqa: E402
from .codebuild import codebuild_inventory  # noqa: E402
from .codedeploy import codedeploy_inventory  # noqa: E402
from .codepipeline import codepipeline_inventory  # noqa: E402
from .cognito import cognito_inventory  # noqa: E402
from .config import config_inventory  # noqa: E402
from .costexplorer import costexplorer_inventory  # noqa: E402
from .cur import cur_inventory  # noqa: E402
from .docdb import docdb_inventory  # noqa: E402
from .dynamodb import dynamodb_inventory  # noqa: E402
from .ec2 import ec2_inventory  # noqa: E402
from .ecr import ecr_inventory  # noqa: E402
from .ecs import ecs_inventory  # noqa: E402
from .eks import eks_inventory  # noqa: E402
from .elasticache import elasticache_inventory  # noqa: E402
from .elasticbeanstalk import elasticbeanstalk_inventory  # noqa: E402
from .elasticloadbalancing import elasticloadbalancing_inventory  # noqa: E402
from .emr import emr_inventory  # noqa: E402
from .eventbridge import eventbridge_inventory  # noqa: E402
from .firehose import firehose_inventory  # noqa: E402
from .glue import glue_inventory  # noqa: E402
from .iam import iam_inventory  # noqa: E402
from .iot import iot_inventory  # noqa: E402
from .kafka import kafka_inventory  # noqa: E402
from .kinesis import kinesis_inventory  # noqa: E402
from .kms import kms_inventory  # noqa: E402
from .s3 import create_s3_bucket, s3_inventory  # noqa: E402
