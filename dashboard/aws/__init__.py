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


def iam_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    iam = factory.client('iam')

    boundary_cache = cache.get(IAM_BOUNDARY_CACHE_KEY, {})
    boundary_cache_scope = '|'.join([
        str(factory.endpoint_url),
        str(factory.region),
        str(factory.credential_source),
        str(factory.profile or ''),
        str(factory.access_key_id or ''),
    ])
    scoped_boundaries = boundary_cache.get(boundary_cache_scope, {})

    def cached_boundary(principal_type: str, principal_name: str) -> dict[str, Any] | None:
        value = scoped_boundaries.get(f'{principal_type}:{principal_name}')
        return value if isinstance(value, dict) else None

    def user_detail(user: dict[str, Any]) -> dict[str, Any]:
        name = user.get('UserName')
        full_user = _safe_value(lambda: iam.get_user(UserName=name).get('User', {}), {})

        return {
            'name': name,
            'arn': user.get('Arn'),
            'created': user.get('CreateDate'),
            'path': full_user.get('Path') or user.get('Path'),
            'login_profile': _safe_value(lambda: iam.get_login_profile(UserName=name).get('LoginProfile'), None),
            'permissions_boundary': full_user.get('PermissionsBoundary') or cached_boundary('user', name),
            'groups': [
                group.get('GroupName')
                for group in _safe_value(
                    lambda: _paginate(iam, 'list_groups_for_user', 'Groups', UserName=name),
                    [],
                )
            ],
            'attached_policies': [
                {
                    'name': policy.get('PolicyName'),
                    'arn': policy.get('PolicyArn'),
                }
                for policy in _safe_value(
                    lambda: _paginate(iam, 'list_attached_user_policies', 'AttachedPolicies', UserName=name),
                    [],
                )
            ],
            'inline_policies': _safe_value(
                lambda: _paginate(iam, 'list_user_policies', 'PolicyNames', UserName=name),
                [],
            ),
            'access_keys': [
                {
                    'id': key.get('AccessKeyId'),
                    'status': key.get('Status'),
                    'created': key.get('CreateDate'),
                    'last_used': _safe_value(
                        lambda key_id=key.get('AccessKeyId'): iam.get_access_key_last_used(AccessKeyId=key_id).get('AccessKeyLastUsed', {}),
                        {},
                    ),
                }
                for key in _safe_value(
                    lambda: _paginate(iam, 'list_access_keys', 'AccessKeyMetadata', UserName=name),
                    [],
                )
            ],
            'tags': _safe_value(lambda: iam.list_user_tags(UserName=name).get('Tags', []), []),
        }

    def group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group.get('GroupName')
        group_response = _safe_value(lambda: iam.get_group(GroupName=name), {})

        return {
            'name': name,
            'arn': group.get('Arn'),
            'created': group.get('CreateDate'),
            'users': [
                user.get('UserName')
                for user in group_response.get('Users', [])
            ],
            'attached_policies': [
                {
                    'name': policy.get('PolicyName'),
                    'arn': policy.get('PolicyArn'),
                }
                for policy in _safe_value(
                    lambda: _paginate(iam, 'list_attached_group_policies', 'AttachedPolicies', GroupName=name),
                    [],
                )
            ],
            'inline_policies': _safe_value(
                lambda: _paginate(iam, 'list_group_policies', 'PolicyNames', GroupName=name),
                [],
            ),
        }

    def role_detail(role: dict[str, Any]) -> dict[str, Any]:
        name = role.get('RoleName')
        full_role = _safe_value(lambda: iam.get_role(RoleName=name).get('Role', {}), {})

        return {
            'name': name,
            'arn': role.get('Arn'),
            'created': role.get('CreateDate'),
            'path': full_role.get('Path') or role.get('Path'),
            'description': full_role.get('Description') or role.get('Description'),
            'max_session_duration': full_role.get('MaxSessionDuration') or role.get('MaxSessionDuration'),
            'permissions_boundary': full_role.get('PermissionsBoundary') or cached_boundary('role', name),
            'trust_policy': full_role.get('AssumeRolePolicyDocument') or role.get('AssumeRolePolicyDocument'),
            'attached_policies': [
                {
                    'name': policy.get('PolicyName'),
                    'arn': policy.get('PolicyArn'),
                }
                for policy in _safe_value(
                    lambda: _paginate(iam, 'list_attached_role_policies', 'AttachedPolicies', RoleName=name),
                    [],
                )
            ],
            'inline_policies': _safe_value(
                lambda: _paginate(iam, 'list_role_policies', 'PolicyNames', RoleName=name),
                [],
            ),
            'instance_profiles': [
                profile.get('InstanceProfileName')
                for profile in _safe_value(
                    lambda: _paginate(iam, 'list_instance_profiles_for_role', 'InstanceProfiles', RoleName=name),
                    [],
                )
            ],
            'tags': _safe_value(lambda: iam.list_role_tags(RoleName=name).get('Tags', []), []),
        }

    def policy_detail(policy: dict[str, Any]) -> dict[str, Any]:
        arn = policy.get('Arn')

        return {
            'name': policy.get('PolicyName'),
            'arn': arn,
            'default_version': policy.get('DefaultVersionId'),
            'attachment_count': policy.get('AttachmentCount'),
            'permissions_boundary_usage_count': policy.get('PermissionsBoundaryUsageCount'),
            'created': policy.get('CreateDate'),
            'updated': policy.get('UpdateDate'),
            'path': policy.get('Path'),
            'description': policy.get('Description'),
            'versions': [
                {
                    'id': version.get('VersionId'),
                    'default': version.get('IsDefaultVersion'),
                    'created': version.get('CreateDate'),
                }
                for version in _safe_value(
                    lambda: _paginate(iam, 'list_policy_versions', 'Versions', PolicyArn=arn),
                    [],
                )
            ],
            'tags': _safe_value(lambda: iam.list_policy_tags(PolicyArn=arn).get('Tags', []), []),
        }

    users = _paginate(iam, 'list_users', 'Users')
    groups = _paginate(iam, 'list_groups', 'Groups')
    roles = _paginate(iam, 'list_roles', 'Roles')
    policies = _safe_value(
        lambda: _paginate(iam, 'list_policies', 'Policies', Scope='Local'),
        _paginate(iam, 'list_policies', 'Policies'),
    )
    instance_profiles = _paginate(iam, 'list_instance_profiles', 'InstanceProfiles')

    def enrich(items: list[dict[str, Any]], loader: Callable[[dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
        if len(items) < 2:
            return [loader(item) for item in items]
        with ThreadPoolExecutor(max_workers=min(8, len(items))) as executor:
            return list(executor.map(loader, items))

    return {
        'summary': {
            'users': len(users),
            'groups': len(groups),
            'roles': len(roles),
            'customer_policies': len(policies),
            'instance_profiles': len(instance_profiles),
        },
        'users': enrich(users, user_detail),
        'groups': enrich(groups, group_detail),
        'roles': enrich(roles, role_detail),
        'policies': enrich(policies, policy_detail),
        'instance_profiles': [
            {
                'name': profile.get('InstanceProfileName'),
                'arn': profile.get('Arn'),
                'created': profile.get('CreateDate'),
                'roles': [
                    role.get('RoleName')
                    for role in profile.get('Roles', [])
                ],
            }
            for profile in instance_profiles
        ],
        'notes': [
            'Floci 1.5.30 enforces AssumeRole trust policies when enforcement is enabled and evaluates STS session policies.',
            'Floci 1.5.29 routes assumed-role credentials to the target account and adds the AmazonRDSEnhancedMonitoringRole managed policy.',
            'Floci 1.5.28 makes IAM entity stores thread-safe under concurrent mutation and resolves AWS-managed policies from any account context.',
        ],
    }


def _error_code(exc: Exception) -> Optional[str]:
    if isinstance(exc, ClientError):
        return exc.response.get('Error', {}).get('Code')

    return None


def costexplorer_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    ce = factory.client('ce')
    operations = set(ce.meta.service_model.operation_names)
    today = date.today()
    time_period = {
        'Start': today.replace(day=1).isoformat(),
        'End': (today + timedelta(days=1)).isoformat(),
    }

    dimension_values = []
    for dimension in ['SERVICE', 'REGION', 'LINKED_ACCOUNT', 'USAGE_TYPE']:
        values = _safe_value(
            lambda dimension=dimension: _paginate(
                ce,
                'get_dimension_values',
                'DimensionValues',
                TimePeriod=time_period,
                Dimension=dimension,
            ),
            [],
        ) if 'GetDimensionValues' in operations else []
        dimension_values.append({
            'name': dimension,
            'dimension': dimension,
            'value_count': len(values),
            'values': values[:50],
        })

    tags = _safe_value(
        lambda: _paginate(ce, 'get_tags', 'Tags', TimePeriod=time_period),
        [],
    ) if 'GetTags' in operations else []

    cost_categories = _safe_value(
        lambda: _paginate(ce, 'get_cost_categories', 'CostCategoryNames', TimePeriod=time_period),
        [],
    ) if 'GetCostCategories' in operations else []

    cost_and_usage = _safe_value(
        lambda: ce.get_cost_and_usage(
            TimePeriod=time_period,
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
        ).get('ResultsByTime', []),
        [],
    ) if 'GetCostAndUsage' in operations else []

    return {
        'summary': {
            'dimension_groups': len(dimension_values),
            'dimension_values': sum(item.get('value_count') or 0 for item in dimension_values),
            'tags': len(tags),
            'cost_categories': len(cost_categories),
            'cost_periods': len(cost_and_usage),
            'available_sdk_operations': len(operations),
        },
        'time_period': time_period,
        'dimension_values': dimension_values,
        'tags': [{'name': tag, 'tag': tag} for tag in tags],
        'cost_categories': [{'name': category, 'category': category} for category in cost_categories],
        'cost_and_usage': _clean_response(cost_and_usage),
        'supported_from_sdk': [
            operation
            for operation in [
                'GetCostAndUsage',
                'GetDimensionValues',
                'GetTags',
                'GetCostCategories',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.16 adds Cost Explorer support under the ce service name.',
            'The dashboard samples the current month to keep Cost Explorer reads bounded.',
        ],
    }


def cur_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    cur = factory.client('cur')
    operations = set(cur.meta.service_model.operation_names)

    report_definitions = _safe_value(
        lambda: _operation_items(cur, 'describe_report_definitions', 'ReportDefinitions'),
        [],
    ) if 'DescribeReportDefinitions' in operations else []

    return {
        'summary': {
            'report_definitions': len(report_definitions),
            'available_sdk_operations': len(operations),
        },
        'report_definitions': _clean_response(report_definitions),
        'supported_from_sdk': [
            operation
            for operation in [
                'DescribeReportDefinitions',
                'PutReportDefinition',
                'ModifyReportDefinition',
                'DeleteReportDefinition',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.17 adds Cost and Usage Reports support under the cur service name.',
            'Report definitions connect billing export metadata to local S3-style delivery settings.',
        ],
    }


def emr_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    emr = factory.client('emr')
    operations = set(emr.meta.service_model.operation_names)

    clusters = _safe_value(
        lambda: _operation_items(emr, 'list_clusters', 'Clusters'),
        [],
    ) if 'ListClusters' in operations else []

    def cluster_detail(cluster: dict[str, Any]) -> dict[str, Any]:
        cluster_id = cluster.get('Id')
        details = _safe_value(
            lambda: emr.describe_cluster(ClusterId=cluster_id).get('Cluster', {}),
            {},
        ) if cluster_id and 'DescribeCluster' in operations else {}
        instance_groups = _safe_value(
            lambda: _operation_items(emr, 'list_instance_groups', 'InstanceGroups', ClusterId=cluster_id),
            [],
        ) if cluster_id and 'ListInstanceGroups' in operations else []
        steps = _safe_value(
            lambda: _operation_items(emr, 'list_steps', 'Steps', ClusterId=cluster_id),
            [],
        ) if cluster_id and 'ListSteps' in operations else []

        return {
            'name': cluster.get('Name') or details.get('Name') or cluster_id,
            'id': cluster_id,
            'state': (cluster.get('Status') or details.get('Status') or {}).get('State'),
            'normalized_instance_hours': details.get('NormalizedInstanceHours'),
            'release_label': details.get('ReleaseLabel'),
            'log_uri': details.get('LogUri'),
            'instance_group_count': len(instance_groups),
            'step_count': len(steps),
            'instance_groups': _clean_response(instance_groups),
            'steps': _clean_response(steps),
            'details': _clean_response(details),
        }

    security_configurations = _safe_value(
        lambda: _operation_items(emr, 'list_security_configurations', 'SecurityConfigurations'),
        [],
    ) if 'ListSecurityConfigurations' in operations else []

    detailed_clusters = [cluster_detail(cluster) for cluster in clusters]

    return {
        'summary': {
            'clusters': len(detailed_clusters),
            'instance_groups': sum(cluster.get('instance_group_count') or 0 for cluster in detailed_clusters),
            'steps': sum(cluster.get('step_count') or 0 for cluster in detailed_clusters),
            'security_configurations': len(security_configurations),
            'available_sdk_operations': len(operations),
        },
        'clusters': detailed_clusters,
        'security_configurations': _clean_response(security_configurations),
        'supported_from_sdk': [
            operation
            for operation in [
                'RunJobFlow',
                'ListClusters',
                'DescribeCluster',
                'TerminateJobFlows',
                'ListInstanceGroups',
                'AddInstanceGroups',
                'ListSteps',
                'AddJobFlowSteps',
                'CancelSteps',
                'ListSecurityConfigurations',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.25 adds Amazon EMR management API support as a Phase 1 service.',
            'The dashboard inspects clusters, nested instance groups, and steps when those management APIs are available.',
        ],
    }


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


def docdb_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    docdb = factory.client('docdb')
    operations = set(docdb.meta.service_model.operation_names)

    clusters = _safe_value(
        lambda: _operation_items(docdb, 'describe_db_clusters', 'DBClusters'),
        [],
    ) if 'DescribeDBClusters' in operations else []
    instances = _safe_value(
        lambda: _operation_items(docdb, 'describe_db_instances', 'DBInstances'),
        [],
    ) if 'DescribeDBInstances' in operations else []

    return {
        'summary': {
            'clusters': len(clusters),
            'instances': len(instances),
            'available_sdk_operations': len(operations),
        },
        'clusters': _clean_response(clusters),
        'instances': _clean_response(instances),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateDBCluster',
                'DescribeDBClusters',
                'DeleteDBCluster',
                'CreateDBInstance',
                'DescribeDBInstances',
                'DeleteDBInstance',
                'ListTagsForResource',
                'AddTagsToResource',
                'RemoveTagsFromResource',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.26 adds Amazon DocumentDB service emulation with cluster and instance lifecycle APIs.',
            'DocumentDB can run in mock mode or start a MongoDB-compatible data container for created clusters.',
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


def iot_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    iot = factory.client('iot')
    operations = set(iot.meta.service_model.operation_names)

    things = _safe_value(lambda: _operation_items(iot, 'list_things', 'things'), []) if 'ListThings' in operations else []
    thing_types = _safe_value(lambda: _operation_items(iot, 'list_thing_types', 'thingTypes'), []) if 'ListThingTypes' in operations else []
    policies = _safe_value(lambda: _operation_items(iot, 'list_policies', 'policies'), []) if 'ListPolicies' in operations else []
    certificates = _safe_value(lambda: _operation_items(iot, 'list_certificates', 'certificates'), []) if 'ListCertificates' in operations else []
    topic_rules = _safe_value(lambda: _operation_items(iot, 'list_topic_rules', 'rules'), []) if 'ListTopicRules' in operations else []
    jobs = _safe_value(lambda: _operation_items(iot, 'list_jobs', 'jobs'), []) if 'ListJobs' in operations else []
    role_aliases = _safe_value(lambda: _operation_items(iot, 'list_role_aliases', 'roleAliases'), []) if 'ListRoleAliases' in operations else []

    return {
        'summary': {
            'things': len(things),
            'thing_types': len(thing_types),
            'policies': len(policies),
            'certificates': len(certificates),
            'topic_rules': len(topic_rules),
            'jobs': len(jobs),
            'role_aliases': len(role_aliases),
            'available_sdk_operations': len(operations),
        },
        'things': _clean_response(things),
        'thing_types': _clean_response(thing_types),
        'policies': _clean_response(policies),
        'certificates': _clean_response(certificates),
        'topic_rules': _clean_response(topic_rules),
        'jobs': _clean_response(jobs),
        'role_aliases': _clean_response(role_aliases),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateThing',
                'ListThings',
                'DescribeThing',
                'DeleteThing',
                'CreatePolicy',
                'ListPolicies',
                'CreateKeysAndCertificate',
                'ListCertificates',
                'CreateTopicRule',
                'ListTopicRules',
                'CreateJob',
                'ListJobs',
                'CreateRoleAlias',
                'ListRoleAliases',
                'AttachPolicy',
                'AttachThingPrincipal',
                'ListTagsForResource',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.28 adds AWS IoT Core service emulation for local MQTT-oriented IoT workflows.',
            'Floci 1.5.29 aligns the IoT compatibility runner and MQTT lazy-startup coverage with the 1.5.28 service baseline.',
            'This read-only inspector focuses on registry, policy, certificate, topic rule, job, and role-alias inventory.',
        ],
    }


def elasticbeanstalk_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    beanstalk = factory.client('elasticbeanstalk')
    operations = set(beanstalk.meta.service_model.operation_names)

    applications = _safe_value(lambda: _operation_items(beanstalk, 'describe_applications', 'Applications'), []) if 'DescribeApplications' in operations else []
    environments = _safe_value(lambda: _operation_items(beanstalk, 'describe_environments', 'Environments'), []) if 'DescribeEnvironments' in operations else []
    application_versions = _safe_value(lambda: _operation_items(beanstalk, 'describe_application_versions', 'ApplicationVersions'), []) if 'DescribeApplicationVersions' in operations else []
    solution_stacks = _safe_value(lambda: beanstalk.list_available_solution_stacks().get('SolutionStacks', []), []) if 'ListAvailableSolutionStacks' in operations else []
    cleaned_environments = _clean_response(environments)
    for environment in cleaned_environments:
        if environment.get('Status') == 'Terminated':
            environment['OptionSettings'] = []
            continue
        settings = _safe_value(lambda env=environment: beanstalk.describe_configuration_settings(
            ApplicationName=env.get('ApplicationName'), EnvironmentName=env.get('EnvironmentName'),
        ).get('ConfigurationSettings', []), [])
        environment['OptionSettings'] = (settings[0].get('OptionSettings', []) if settings else [])

    return {
        'summary': {
            'applications': len(applications),
            'environments': len(environments),
            'application_versions': len(application_versions),
            'solution_stacks': len(solution_stacks),
        },
        'applications': _clean_response(applications),
        'environments': cleaned_environments,
        'application_versions': _clean_response(application_versions),
        'solution_stacks': [{'name': stack} for stack in solution_stacks],
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateApplication',
                'DescribeApplications',
                'UpdateApplication',
                'DeleteApplication',
                'CreateApplicationVersion',
                'DescribeApplicationVersions',
                'DeleteApplicationVersion',
                'CreateEnvironment',
                'DescribeEnvironments',
                'UpdateEnvironment',
                'TerminateEnvironment',
                'DescribeConfigurationSettings',
                'CheckDNSAvailability',
                'ListAvailableSolutionStacks',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.28 adds initial Elastic Beanstalk Query API support.',
            'Floci 1.5.31 persists Elastic Beanstalk applications, versions, and environments across restart.',
            'Environments become Ready and Green immediately; Floci stores management-plane state but does not provision application infrastructure.',
            'Tags supplied during creation are stored internally but cannot be retrieved or updated through Floci’s supported Elastic Beanstalk operations.',
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


def ec2_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    ec2 = factory.client('ec2')
    operations = set(ec2.meta.service_model.operation_names)

    reservations = _clean_response(_safe_value(lambda: ec2.describe_instances().get('Reservations', []), []))
    instances = [
        {
            'id': instance.get('InstanceId'),
            'image_id': instance.get('ImageId'),
            'instance_type': instance.get('InstanceType'),
            'state': instance.get('State', {}).get('Name'),
            'state_code': instance.get('State', {}).get('Code'),
            'key_name': instance.get('KeyName'),
            'architecture': instance.get('Architecture'),
            'vpc_id': instance.get('VpcId'),
            'subnet_id': instance.get('SubnetId'),
            'private_ip': instance.get('PrivateIpAddress'),
            'public_ip': instance.get('PublicIpAddress'),
            'private_dns': instance.get('PrivateDnsName'),
            'public_dns': instance.get('PublicDnsName'),
            'security_groups': instance.get('SecurityGroups', []),
            'network_interfaces': instance.get('NetworkInterfaces', []),
            'block_device_mappings': instance.get('BlockDeviceMappings', []),
            'iam_instance_profile': instance.get('IamInstanceProfile'),
            'placement': instance.get('Placement'),
            'root_device_name': instance.get('RootDeviceName'),
            'root_device_type': instance.get('RootDeviceType'),
            'monitoring': instance.get('Monitoring'),
            'launch_time': instance.get('LaunchTime'),
            'tags': instance.get('Tags', []),
        }
        for reservation in reservations
        for instance in reservation.get('Instances', [])
    ]

    vpcs = _clean_response(_safe_value(lambda: ec2.describe_vpcs().get('Vpcs', []), []))
    subnets = _clean_response(_safe_value(lambda: ec2.describe_subnets().get('Subnets', []), []))
    security_groups = _clean_response(_safe_value(lambda: ec2.describe_security_groups().get('SecurityGroups', []), []))
    security_group_rules = _clean_response(_safe_value(lambda: ec2.describe_security_group_rules().get('SecurityGroupRules', []), []))
    key_pairs = _clean_response(_safe_value(lambda: ec2.describe_key_pairs().get('KeyPairs', []), []))
    images = _clean_response(_safe_value(lambda: ec2.describe_images().get('Images', []), []))
    tags = _clean_response(_safe_value(lambda: ec2.describe_tags().get('Tags', []), []))
    internet_gateways = _clean_response(_safe_value(lambda: ec2.describe_internet_gateways().get('InternetGateways', []), []))
    route_tables = _clean_response(_safe_value(lambda: ec2.describe_route_tables().get('RouteTables', []), []))
    network_acls = _clean_response(_safe_value(lambda: ec2.describe_network_acls().get('NetworkAcls', []), []))
    nat_gateways = _clean_response(_safe_value(
        lambda: _paginate(ec2, 'describe_nat_gateways', 'NatGateways'),
        [],
    ))
    network_interfaces = _clean_response(_safe_value(
        lambda: _paginate(ec2, 'describe_network_interfaces', 'NetworkInterfaces'),
        [],
    ))
    prefix_lists = _clean_response(_safe_value(lambda: ec2.describe_prefix_lists().get('PrefixLists', []), []))
    vpc_endpoints = _clean_response(_safe_value(lambda: _paginate(ec2, 'describe_vpc_endpoints', 'VpcEndpoints'), []))
    addresses = _clean_response(_safe_value(lambda: ec2.describe_addresses().get('Addresses', []), []))
    iam_instance_profile_associations = _clean_response(_safe_value(
        lambda: _paginate(ec2, 'describe_iam_instance_profile_associations', 'IamInstanceProfileAssociations'),
        [],
    ))
    availability_zones = _clean_response(_safe_value(lambda: ec2.describe_availability_zones().get('AvailabilityZones', []), []))
    regions = _clean_response(_safe_value(lambda: ec2.describe_regions().get('Regions', []), []))
    account_attributes = _clean_response(_safe_value(lambda: ec2.describe_account_attributes().get('AccountAttributes', []), []))
    instance_types = _clean_response(_safe_value(lambda: ec2.describe_instance_types().get('InstanceTypes', []), []))
    flow_logs = _clean_response(_safe_value(
        lambda: _paginate(ec2, 'describe_flow_logs', 'FlowLogs'),
        [],
    )) if 'DescribeFlowLogs' in operations else []
    snapshots = _clean_response(_safe_value(
        lambda: _paginate(ec2, 'describe_snapshots', 'Snapshots'),
        [],
    )) if 'DescribeSnapshots' in operations else []
    volumes = _clean_response(_safe_value(lambda: _paginate(ec2, 'describe_volumes', 'Volumes'), []))
    launch_templates = _clean_response(_safe_value(
        lambda: _paginate(ec2, 'describe_launch_templates', 'LaunchTemplates'),
        [],
    ))
    launch_template_versions = []
    for template in launch_templates:
        template_id = template.get('LaunchTemplateId')
        if not template_id:
            continue
        versions = _safe_value(
            lambda template_id=template_id: _paginate(
                ec2,
                'describe_launch_template_versions',
                'LaunchTemplateVersions',
                LaunchTemplateId=template_id,
                Versions=[
                    str(number)
                    for number in range(1, int(template.get('LatestVersionNumber') or 1) + 1)
                ],
            ),
            [],
        )
        launch_template_versions.extend(_clean_response(versions))
    spot_instance_requests = _clean_response(_safe_value(
        lambda: _paginate(ec2, 'describe_spot_instance_requests', 'SpotInstanceRequests'),
        [],
    ))

    diagnostics = []
    vpc_ids = {vpc.get('VpcId') for vpc in vpcs if vpc.get('VpcId')}
    attached_igws = {
        attachment.get('VpcId')
        for gateway in internet_gateways
        for attachment in gateway.get('Attachments', [])
        if attachment.get('VpcId')
    }
    routed_subnets = {
        association.get('SubnetId')
        for table in route_tables
        for association in table.get('Associations', [])
        if association.get('SubnetId')
    }
    vpcs_with_main_routes = {
        table.get('VpcId')
        for table in route_tables
        if any(association.get('Main') for association in table.get('Associations', []))
    }
    acl_subnets = {
        association.get('SubnetId')
        for acl in network_acls
        for association in acl.get('Associations', [])
        if association.get('SubnetId')
    }
    for vpc in vpcs:
        if vpc.get('VpcId') not in attached_igws:
            diagnostics.append({
                'severity': 'warning',
                'resource_type': 'vpc',
                'resource_id': vpc.get('VpcId'),
                'title': 'No internet gateway attached',
                'detail': 'Instances in this VPC do not have an internet-gateway path.',
            })
    for subnet_item in subnets:
        subnet_id = subnet_item.get('SubnetId')
        vpc_id = subnet_item.get('VpcId')
        if subnet_id not in routed_subnets and vpc_id not in vpcs_with_main_routes:
            diagnostics.append({
                'severity': 'error',
                'resource_type': 'subnet',
                'resource_id': subnet_id,
                'title': 'No route table association',
                'detail': 'Associate this subnet with a route table or create a main route table for its VPC.',
            })
        if subnet_id not in acl_subnets:
            diagnostics.append({
                'severity': 'warning',
                'resource_type': 'subnet',
                'resource_id': subnet_id,
                'title': 'No network ACL association',
                'detail': 'The subnet is not represented in any current network ACL association.',
            })
    for group in security_groups:
        if group.get('GroupName') != 'default' and not group.get('IpPermissions'):
            diagnostics.append({
                'severity': 'info',
                'resource_type': 'security_group',
                'resource_id': group.get('GroupId'),
                'title': 'No inbound rules',
                'detail': 'Instances using this security group cannot publish CIDR-sourced TCP application ports.',
            })
    for address in addresses:
        if not address.get('AssociationId') and not address.get('InstanceId'):
            diagnostics.append({
                'severity': 'info',
                'resource_type': 'elastic_ip',
                'resource_id': address.get('AllocationId') or address.get('PublicIp'),
                'title': 'Elastic IP is unassociated',
                'detail': 'Associate it with an instance or release it when it is no longer needed.',
            })
    for resource_type, resource_id, vpc_id in [
        *[('security_group', group.get('GroupId'), group.get('VpcId')) for group in security_groups],
        *[('route_table', table.get('RouteTableId'), table.get('VpcId')) for table in route_tables],
        *[('network_acl', acl.get('NetworkAclId'), acl.get('VpcId')) for acl in network_acls],
    ]:
        if vpc_id and vpc_id not in vpc_ids:
            diagnostics.append({
                'severity': 'warning',
                'resource_type': resource_type,
                'resource_id': resource_id,
                'title': 'Resource references a missing VPC',
                'detail': f'{resource_id} still references deleted VPC {vpc_id}. This can indicate an emulator cleanup gap.',
            })

    return {
        'summary': {
            'instances': len(instances),
            'vpcs': len(vpcs),
            'subnets': len(subnets),
            'security_groups': len(security_groups),
            'internet_gateways': len(internet_gateways),
            'route_tables': len(route_tables),
            'network_acls': len(network_acls),
            'nat_gateways': len(nat_gateways),
            'network_interfaces': len(network_interfaces),
            'vpc_endpoints': len(vpc_endpoints),
            'elastic_ips': len(addresses),
            'key_pairs': len(key_pairs),
            'iam_instance_profile_associations': len(iam_instance_profile_associations),
            'flow_logs': len(flow_logs),
            'snapshots': len(snapshots),
            'volumes': len(volumes),
            'launch_templates': len(launch_templates),
            'spot_requests': len(spot_instance_requests),
        },
        'instances': instances,
        'vpcs': vpcs,
        'subnets': subnets,
        'security_groups': security_groups,
        'security_group_rules': security_group_rules,
        'key_pairs': key_pairs,
        'images': images,
        'tags': tags,
        'internet_gateways': internet_gateways,
        'route_tables': route_tables,
        'network_acls': network_acls,
        'nat_gateways': nat_gateways,
        'network_interfaces': network_interfaces,
        'prefix_lists': prefix_lists,
        'vpc_endpoints': vpc_endpoints,
        'addresses': addresses,
        'iam_instance_profile_associations': iam_instance_profile_associations,
        'availability_zones': availability_zones,
        'regions': regions,
        'account_attributes': account_attributes,
        'instance_types': instance_types,
        'flow_logs': flow_logs,
        'snapshots': snapshots,
        'volumes': volumes,
        'launch_templates': launch_templates,
        'launch_template_versions': launch_template_versions,
        'spot_instance_requests': spot_instance_requests,
        'diagnostics': diagnostics,
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateFlowLogs',
                'DescribeFlowLogs',
                'DeleteFlowLogs',
                'DescribeImages',
                'DescribeInstanceTypes',
                'DescribeInstanceAttribute',
                'DescribeSecurityGroupRules',
                'DescribeSnapshots',
            ]
            if operation in operations
        ],
        'default_resources': [
            {'type': 'Default VPC', 'id': 'vpc-default', 'details': 'CIDR 172.31.0.0/16'},
            {'type': 'Default Subnet', 'id': 'subnet-default-a', 'details': 'CIDR 172.31.0.0/20'},
            {'type': 'Default Subnet', 'id': 'subnet-default-b', 'details': 'CIDR 172.31.16.0/20'},
            {'type': 'Default Subnet', 'id': 'subnet-default-c', 'details': 'CIDR 172.31.32.0/20'},
            {'type': 'Default Security Group', 'id': 'sg-default', 'details': 'groupName=default, all-traffic egress'},
            {'type': 'Default Internet Gateway', 'id': 'igw-default', 'details': 'Attached to default VPC'},
            {'type': 'Main Route Table', 'id': 'rtb-default', 'details': 'Associated with default VPC'},
        ],
        'notes': [
            'RunInstances launches a Docker-backed local instance unless EC2 mock mode is enabled.',
            'DescribeImages returns a static list of common AMIs and Floci-native AMI IDs.',
            'CreateKeyPair returns dummy RSA PEM material that is not usable for real SSH.',
            'Use ImportKeyPair with a real public key for working SSH access.',
            'Floci serves IMDS-compatible metadata on the configured host IMDS port, default 9169.',
            'Security group rules are stored and returned but local Docker networking handles enforcement.',
            'Floci 1.5.30 adds VPC Flow Logs and publishes security-group TCP ingress ports on the host.',
            'Floci 1.5.31 adds instance metadata catalog architecture parity plus registered images and snapshots support.',
            'Floci 1.5.27 adds EC2 Network ACL support for VPC subnet traffic-control workflows.',
            'Floci 1.5.28 adds a Java-built Ubuntu AMI guest image, instance type metadata catalog, VPC endpoint compatibility improvements, and persisted default egress security group rules.',
            'Floci 1.5.26 adds Spot Instance request actions and embedded DNS support for instances.',
            'Floci 1.5.25 embeds the EC2 image catalog in the native image and loads it lazily.',
            'Floci 1.5.21 improves EC2 parity for VPC, subnet, and instance methods, subnet attributes, block-device attachTime, volume throughput, DescribeAddressesAttribute, and IAM instance profiles.',
            'Floci 1.5.24 auto-creates the default security group and main route table when CreateVpc is used.',
        ],
    }


def _kms_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def kms_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    kms = factory.client('kms')
    keys = _safe_value(lambda: _paginate(kms, 'list_keys', 'Keys'), [])
    aliases = _safe_value(lambda: _paginate(kms, 'list_aliases', 'Aliases'), [])

    aliases_by_key: dict[str, list[dict[str, Any]]] = {}
    for alias in aliases:
        target_key_id = alias.get('TargetKeyId')
        if target_key_id:
            aliases_by_key.setdefault(target_key_id, []).append(_clean_response(alias))

    def key_detail(key: dict[str, Any]) -> dict[str, Any]:
        key_id = key.get('KeyId')
        metadata = _kms_optional(
            lambda: kms.describe_key(KeyId=key_id).get('KeyMetadata', {}),
            {'NotFoundException', 'KMSInvalidStateException'},
        )

        return {
            'name': key_id,
            'key_id': key_id,
            'key_arn': key.get('KeyArn'),
            'metadata': metadata,
            'aliases': _kms_optional(
                lambda: _paginate(kms, 'list_aliases', 'Aliases', KeyId=key_id),
                {'NotFoundException'},
            ),
            'tags': _kms_optional(
                lambda: _paginate(kms, 'list_resource_tags', 'Tags', KeyId=key_id),
                {'NotFoundException'},
            ),
            'policy': _kms_optional(
                lambda: json.loads(kms.get_key_policy(KeyId=key_id, PolicyName='default').get('Policy', '{}')),
                {'NotFoundException'},
            ),
            'rotation_enabled': _kms_optional(
                lambda: kms.get_key_rotation_status(KeyId=key_id).get('KeyRotationEnabled'),
                {'NotFoundException', 'UnsupportedOperationException'},
            ),
            'grants': _kms_optional(
                lambda: _paginate(kms, 'list_grants', 'Grants', KeyId=key_id),
                {'NotFoundException'},
            ),
        }

    if len(keys) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(keys))) as executor:
            detailed_keys = list(executor.map(key_detail, keys))
    else:
        detailed_keys = [key_detail(key) for key in keys]

    return {
        'summary': {
            'keys': len(detailed_keys),
            'aliases': len(aliases),
            'enabled_keys': sum(
                1
                for key in detailed_keys
                if isinstance(key.get('metadata'), dict)
                and key['metadata'].get('KeyState') == 'Enabled'
            ),
            'pending_deletion': sum(
                1
                for key in detailed_keys
                if isinstance(key.get('metadata'), dict)
                and key['metadata'].get('KeyState') == 'PendingDeletion'
            ),
            'rotation_enabled': sum(1 for key in detailed_keys if key.get('rotation_enabled') is True),
            'grants': sum(len(key.get('grants') or []) for key in detailed_keys if isinstance(key.get('grants'), list)),
        },
        'keys': detailed_keys,
        'aliases': _clean_response(aliases),
        'supported': [
            'CreateKey',
            'DescribeKey',
            'ListKeys',
            'Encrypt',
            'Decrypt',
            'ReEncrypt',
            'GenerateDataKey',
            'GenerateDataKeyWithoutPlaintext',
            'GenerateRandom',
            'Sign',
            'Verify',
            'GenerateMac',
            'VerifyMac',
            'CreateAlias',
            'DeleteAlias',
            'ListAliases',
            'ScheduleKeyDeletion',
            'CancelKeyDeletion',
            'TagResource',
            'UntagResource',
            'ListResourceTags',
            'GetKeyPolicy',
            'PutKeyPolicy',
            'GetKeyRotationStatus',
            'EnableKeyRotation',
            'DisableKeyRotation',
            'RotateKeyOnDemand',
            'CreateGrant',
            'ListGrants',
            'ListRetirableGrants',
            'RevokeGrant',
            'RetireGrant',
        ],
        'notes': [
            'KMS uses JSON 1.1 with X-Amz-Target: TrentService.* against the local Floci endpoint.',
            'CreateKey accepts creation-time tag floci:override-id for deterministic local test key IDs.',
            'Floci strips reserved floci:* tags from stored resource tags and rejects adding them later.',
            'Floci 1.5.18 adds KMS GenerateMac and VerifyMac support.',
            'Floci 1.5.22 adds KMS GenerateRandom support.',
            'Floci 1.5.24 expands key state and key description operations for closer AWS parity.',
            'Floci 1.5.26 adds EnableKey support and improves RSA DIGEST signature wrapping.',
            'Floci 1.5.21 enforces the RotateKeyOnDemand rotation limit.',
            'Grant lifecycle APIs are stored and queryable but are not evaluated during cryptographic operations.',
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


def _dynamodb_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def dynamodb_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    dynamodb = factory.client('dynamodb')
    streams = factory.client('dynamodbstreams')
    table_names = _safe_value(lambda: _paginate(dynamodb, 'list_tables', 'TableNames'), [])

    def table_detail(table_name: str) -> dict[str, Any]:
        description = _dynamodb_optional(
            lambda: dynamodb.describe_table(TableName=table_name).get('Table', {}),
            {'ResourceNotFoundException'},
        )
        table_arn = description.get('TableArn') if isinstance(description, dict) else None
        scan_preview = _dynamodb_optional(
            lambda: dynamodb.scan(TableName=table_name, Limit=5),
            {'ResourceNotFoundException'},
        )

        return {
            'name': table_name,
            'arn': table_arn,
            'status': description.get('TableStatus') if isinstance(description, dict) else None,
            'item_count': description.get('ItemCount') if isinstance(description, dict) else None,
            'size_bytes': description.get('TableSizeBytes') if isinstance(description, dict) else None,
            'billing_mode': description.get('BillingModeSummary') if isinstance(description, dict) else None,
            'key_schema': description.get('KeySchema') if isinstance(description, dict) else None,
            'attribute_definitions': description.get('AttributeDefinitions') if isinstance(description, dict) else None,
            'provisioned_throughput': description.get('ProvisionedThroughput') if isinstance(description, dict) else None,
            'global_secondary_indexes': description.get('GlobalSecondaryIndexes') if isinstance(description, dict) else None,
            'local_secondary_indexes': description.get('LocalSecondaryIndexes') if isinstance(description, dict) else None,
            'stream_specification': description.get('StreamSpecification') if isinstance(description, dict) else None,
            'latest_stream_arn': description.get('LatestStreamArn') if isinstance(description, dict) else None,
            'ttl': _dynamodb_optional(
                lambda: dynamodb.describe_time_to_live(TableName=table_name).get('TimeToLiveDescription', {}),
                {'ResourceNotFoundException'},
            ),
            'tags': _dynamodb_optional(
                lambda: _paginate(dynamodb, 'list_tags_of_resource', 'Tags', ResourceArn=table_arn)
                if table_arn else [],
                {'ResourceNotFoundException'},
            ),
            'scan_preview': {
                'count': scan_preview.get('Count'),
                'scanned_count': scan_preview.get('ScannedCount'),
                'items': scan_preview.get('Items', []),
            } if isinstance(scan_preview, dict) and 'error' not in scan_preview else scan_preview,
        }

    tables = [table_detail(name) for name in table_names]
    stream_summaries = _safe_value(lambda: _paginate(streams, 'list_streams', 'Streams'), [])

    def stream_detail(stream: dict[str, Any]) -> dict[str, Any]:
        stream_arn = stream.get('StreamArn')
        description = _dynamodb_optional(
            lambda: streams.describe_stream(StreamArn=stream_arn).get('StreamDescription', {}),
            {'ResourceNotFoundException'},
        )

        return {
            'name': stream.get('TableName') or stream_arn,
            'table_name': stream.get('TableName'),
            'stream_arn': stream_arn,
            'label': stream.get('StreamLabel'),
            'description': description,
        }

    stream_details = [stream_detail(stream) for stream in stream_summaries]

    return {
        'summary': {
            'tables': len(tables),
            'items': sum(table.get('item_count') or 0 for table in tables),
            'global_secondary_indexes': sum(
                len(table.get('global_secondary_indexes') or [])
                for table in tables
                if isinstance(table.get('global_secondary_indexes'), list)
            ),
            'streams': len(stream_details),
            'ttl_enabled': sum(
                1
                for table in tables
                if isinstance(table.get('ttl'), dict)
                and table['ttl'].get('TimeToLiveStatus') == 'ENABLED'
            ),
        },
        'tables': tables,
        'streams': stream_details,
        'supported': [
            'CreateTable',
            'DeleteTable',
            'DescribeTable',
            'ListTables',
            'UpdateTable',
            'PutItem',
            'GetItem',
            'DeleteItem',
            'UpdateItem',
            'Query',
            'Scan',
            'BatchWriteItem',
            'BatchGetItem',
            'TransactWriteItems',
            'TransactGetItems',
            'ExecuteStatement',
            'ExecuteTransaction',
            'BatchExecuteStatement',
            'DescribeTimeToLive',
            'UpdateTimeToLive',
            'TagResource',
            'UntagResource',
            'ListTagsOfResource',
        ],
        'notes': [
            'Floci 1.5.28 returns exact 400 error messages for invalid expressions.',
            'Floci 1.5.27 adds TableId, TableClass, OnDemandThroughput, deletion-protection fixes, stricter expression validation, ExclusiveStartKey validation, and scan cursor improvements.',
            'Floci 1.5.17 adds DynamoDB PartiQL support for ExecuteStatement, ExecuteTransaction, and BatchExecuteStatement.',
            'Floci 1.5.21 merges nested map paths correctly in ProjectionExpression.',
            'UpdateItem now honors the legacy Expected condition shape for compatibility with older clients.',
        ],
        'streams_supported': [
            'ListStreams',
            'DescribeStream',
            'GetShardIterator',
            'GetRecords',
        ],
    }


def _eventbridge_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def eventbridge_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    events = factory.client('events')
    buses = _safe_value(lambda: _operation_items(events, 'list_event_buses', 'EventBuses'), [])

    if not buses:
        buses = [{'Name': 'default'}]

    def bus_name(bus: dict[str, Any]) -> str:
        return bus.get('Name') or bus.get('Arn') or 'default'

    def bus_detail(bus: dict[str, Any]) -> dict[str, Any]:
        name = bus_name(bus)
        details = _eventbridge_optional(
            lambda: events.describe_event_bus(Name=name),
            {'ResourceNotFoundException'},
        )
        rules = _eventbridge_optional(
            lambda: _operation_items(events, 'list_rules', 'Rules', EventBusName=name),
            {'ResourceNotFoundException'},
        )
        rule_details = []

        if isinstance(rules, list):
            for rule in rules:
                rule_name = rule.get('Name')
                targets = _eventbridge_optional(
                    lambda: _operation_items(
                        events,
                        'list_targets_by_rule',
                        'Targets',
                        Rule=rule_name,
                        EventBusName=name,
                    ),
                    {'ResourceNotFoundException'},
                )
                described_rule = _eventbridge_optional(
                    lambda: events.describe_rule(Name=rule_name, EventBusName=name),
                    {'ResourceNotFoundException'},
                )
                rule_details.append({
                    'name': rule_name,
                    'arn': rule.get('Arn'),
                    'state': rule.get('State'),
                    'description': rule.get('Description'),
                    'schedule_expression': rule.get('ScheduleExpression'),
                    'event_pattern': rule.get('EventPattern'),
                    'role_arn': rule.get('RoleArn'),
                    'managed_by': rule.get('ManagedBy'),
                    'details': described_rule,
                    'targets': targets,
                    'target_count': len(targets) if isinstance(targets, list) else 0,
                })

        return {
            'name': name,
            'arn': bus.get('Arn') or (details.get('Arn') if isinstance(details, dict) else None),
            'description': bus.get('Description') or (details.get('Description') if isinstance(details, dict) else None),
            'policy': bus.get('Policy') or (details.get('Policy') if isinstance(details, dict) else None),
            'details': details,
            'rules': rule_details,
            'rule_count': len(rule_details),
            'target_count': sum(rule.get('target_count') or 0 for rule in rule_details),
        }

    detailed_buses = [bus_detail(bus) for bus in buses]

    return {
        'summary': {
            'event_buses': len(detailed_buses),
            'rules': sum(bus.get('rule_count') or 0 for bus in detailed_buses),
            'targets': sum(bus.get('target_count') or 0 for bus in detailed_buses),
            'enabled_rules': sum(
                1
                for bus in detailed_buses
                for rule in bus.get('rules', [])
                if rule.get('state') == 'ENABLED'
            ),
        },
        'event_buses': detailed_buses,
        'supported': [
            'CreateEventBus',
            'DeleteEventBus',
            'DescribeEventBus',
            'ListEventBuses',
            'PutRule',
            'DeleteRule',
            'DescribeRule',
            'ListRules',
            'EnableRule',
            'DisableRule',
            'PutTargets',
            'RemoveTargets',
            'ListTargetsByRule',
            'PutEvents',
        ],
        'notes': [
            'The default event bus is named default and accepts AWS service events.',
            'Custom event buses are for application events.',
            'Floci 1.5.31 can deliver EventBridge events to Data Firehose delivery stream targets.',
            'Floci 1.5.28 syncs EventBridge tags to Resource Groups Tagging discovery.',
            'Floci 1.5.22 preserves the PutEvents call region and account through pattern matching and delivered event envelopes.',
            'Floci 1.5.21 fixes rule tagging on custom buses whose name contains event-bus.',
        ],
    }


def _eks_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def eks_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    eks = factory.client('eks')
    cluster_names = _safe_value(lambda: _paginate(eks, 'list_clusters', 'clusters'), [])

    def access_entries(cluster_name: str) -> list[dict[str, Any]]:
        principals = _eks_optional(
            lambda: _paginate(eks, 'list_access_entries', 'accessEntries', clusterName=cluster_name),
            {'ResourceNotFoundException', 'InvalidParameterException'},
        )
        if not isinstance(principals, list):
            return []

        entries = []
        for principal in principals:
            detail = _eks_optional(
                lambda principal=principal: eks.describe_access_entry(
                    clusterName=cluster_name,
                    principalArn=principal,
                ).get('accessEntry', {}),
                {'ResourceNotFoundException', 'InvalidParameterException'},
            )
            entry = detail if isinstance(detail, dict) and not detail.get('error') else {'principalArn': principal}
            policies = _eks_optional(
                lambda principal=principal: _paginate(
                    eks,
                    'list_associated_access_policies',
                    'associatedAccessPolicies',
                    clusterName=cluster_name,
                    principalArn=principal,
                ),
                {'ResourceNotFoundException', 'InvalidParameterException'},
            )
            entries.append({
                'name': entry.get('principalArn') or principal,
                'cluster_name': cluster_name,
                'principal_arn': entry.get('principalArn') or principal,
                'kubernetes_groups': entry.get('kubernetesGroups'),
                'access_entry_arn': entry.get('accessEntryArn'),
                'created_at': entry.get('createdAt'),
                'modified_at': entry.get('modifiedAt'),
                'tags': entry.get('tags'),
                'username': entry.get('username'),
                'type': entry.get('type'),
                'associated_policies': policies if isinstance(policies, list) else [],
                'details': detail if isinstance(detail, dict) and detail.get('error') else None,
            })
        return entries

    def cluster_detail(name: str) -> dict[str, Any]:
        response = _eks_optional(lambda: eks.describe_cluster(name=name), {'ResourceNotFoundException'})
        cluster = response.get('cluster', {}) if isinstance(response, dict) and not response.get('error') else {}
        nodegroups = _eks_optional(lambda: _paginate(eks, 'list_nodegroups', 'nodegroups', clusterName=name), {'ResourceNotFoundException'})
        fargate_profiles = _eks_optional(lambda: _paginate(eks, 'list_fargate_profiles', 'fargateProfileNames', clusterName=name), {'ResourceNotFoundException'})

        return {
            'name': name,
            'arn': cluster.get('arn'),
            'created_at': cluster.get('createdAt'),
            'version': cluster.get('version'),
            'endpoint': cluster.get('endpoint'),
            'role_arn': cluster.get('roleArn'),
            'resources_vpc_config': cluster.get('resourcesVpcConfig'),
            'kubernetes_network_config': cluster.get('kubernetesNetworkConfig'),
            'logging': cluster.get('logging'),
            'identity': cluster.get('identity'),
            'status': cluster.get('status'),
            'health': cluster.get('health'),
            'certificate_authority': cluster.get('certificateAuthority'),
            'platform_version': cluster.get('platformVersion'),
            'tags': cluster.get('tags'),
            'encryption_config': cluster.get('encryptionConfig'),
            'connector_config': cluster.get('connectorConfig'),
            'access_config': cluster.get('accessConfig'),
            'upgrade_policy': cluster.get('upgradePolicy'),
            'nodegroup_count': len(nodegroups) if isinstance(nodegroups, list) else 0,
            'fargate_profile_count': len(fargate_profiles) if isinstance(fargate_profiles, list) else 0,
            'nodegroups': nodegroups if isinstance(nodegroups, list) else [],
            'fargate_profiles': fargate_profiles if isinstance(fargate_profiles, list) else [],
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def nodegroup_detail(cluster_name: str, nodegroup_name: str) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name),
            {'ResourceNotFoundException'},
        )
        nodegroup = response.get('nodegroup', {}) if isinstance(response, dict) and not response.get('error') else {}
        return {
            'name': nodegroup_name,
            'cluster_name': cluster_name,
            'arn': nodegroup.get('nodegroupArn'),
            'created_at': nodegroup.get('createdAt'),
            'modified_at': nodegroup.get('modifiedAt'),
            'status': nodegroup.get('status'),
            'capacity_type': nodegroup.get('capacityType'),
            'scaling_config': nodegroup.get('scalingConfig'),
            'instance_types': nodegroup.get('instanceTypes'),
            'subnets': nodegroup.get('subnets'),
            'remote_access': nodegroup.get('remoteAccess'),
            'ami_type': nodegroup.get('amiType'),
            'node_role': nodegroup.get('nodeRole'),
            'labels': nodegroup.get('labels'),
            'taints': nodegroup.get('taints'),
            'resources': nodegroup.get('resources'),
            'disk_size': nodegroup.get('diskSize'),
            'health': nodegroup.get('health'),
            'update_config': nodegroup.get('updateConfig'),
            'launch_template': nodegroup.get('launchTemplate'),
            'version': nodegroup.get('version'),
            'release_version': nodegroup.get('releaseVersion'),
            'tags': nodegroup.get('tags'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def fargate_profile_detail(cluster_name: str, profile_name: str) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_fargate_profile(clusterName=cluster_name, fargateProfileName=profile_name),
            {'ResourceNotFoundException'},
        )
        profile = response.get('fargateProfile', {}) if isinstance(response, dict) and not response.get('error') else {}
        return {
            'name': profile_name,
            'cluster_name': cluster_name,
            'arn': profile.get('fargateProfileArn'),
            'created_at': profile.get('createdAt'),
            'pod_execution_role_arn': profile.get('podExecutionRoleArn'),
            'subnets': profile.get('subnets'),
            'selectors': profile.get('selectors'),
            'status': profile.get('status'),
            'tags': profile.get('tags'),
            'health': profile.get('health'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def addon_detail(cluster_name: str, addon_name: str) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_addon(clusterName=cluster_name, addonName=addon_name),
            {'ResourceNotFoundException'},
        )
        addon = response.get('addon', {}) if isinstance(response, dict) and not response.get('error') else {}
        return {
            'name': addon_name,
            'cluster_name': cluster_name,
            'arn': addon.get('addonArn'),
            'version': addon.get('addonVersion'),
            'status': addon.get('status'),
            'service_account_role_arn': addon.get('serviceAccountRoleArn'),
            'configuration_values': addon.get('configurationValues'),
            'health': addon.get('health'),
            'created_at': addon.get('createdAt'),
            'modified_at': addon.get('modifiedAt'),
            'tags': addon.get('tags'),
            'publisher': addon.get('publisher'),
            'owner': addon.get('owner'),
            'marketplace_information': addon.get('marketplaceInformation'),
            'pod_identity_associations': addon.get('podIdentityAssociations'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def identity_provider_detail(cluster_name: str, config: dict[str, Any]) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_identity_provider_config(clusterName=cluster_name, identityProviderConfig=config),
            {'ResourceNotFoundException', 'InvalidParameterException'},
        )
        provider = response.get('identityProviderConfig', {}) if isinstance(response, dict) and not response.get('error') else {}
        oidc = provider.get('oidc') or {}
        name = config.get('name') or oidc.get('identityProviderConfigName')
        return {
            'name': name,
            'cluster_name': cluster_name,
            'type': config.get('type') or provider.get('type'),
            'status': provider.get('status'),
            'issuer_url': oidc.get('issuerUrl'),
            'client_id': oidc.get('clientId'),
            'username_claim': oidc.get('usernameClaim'),
            'username_prefix': oidc.get('usernamePrefix'),
            'groups_claim': oidc.get('groupsClaim'),
            'groups_prefix': oidc.get('groupsPrefix'),
            'required_claims': oidc.get('requiredClaims'),
            'tags': provider.get('tags'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def enrich(items, loader):
        if len(items) < 2:
            return [loader(item) for item in items]
        with ThreadPoolExecutor(max_workers=min(8, len(items))) as executor:
            return list(executor.map(loader, items))

    clusters = enrich(cluster_names, cluster_detail)
    nodegroup_refs = [(cluster.get('name'), name) for cluster in clusters for name in cluster.get('nodegroups', [])]
    profile_refs = [(cluster.get('name'), name) for cluster in clusters for name in cluster.get('fargate_profiles', [])]
    nodegroups = enrich(nodegroup_refs, lambda ref: nodegroup_detail(*ref))
    fargate_profiles = enrich(profile_refs, lambda ref: fargate_profile_detail(*ref))

    return {
        'summary': {
            'clusters': len(clusters),
            'nodegroups': len(nodegroups),
            'fargate_profiles': len(fargate_profiles),
        },
        'clusters': clusters,
        'nodegroups': nodegroups,
        'fargate_profiles': fargate_profiles,
        'supported_from_sdk': [
            'CreateCluster',
            'DescribeCluster',
            'ListClusters',
            'DeleteCluster',
            'CreateNodegroup',
            'DescribeNodegroup',
            'ListNodegroups',
            'DeleteNodegroup',
            'CreateFargateProfile',
            'DescribeFargateProfile',
            'ListFargateProfiles',
            'DeleteFargateProfile',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'notes': [
            'Floci EKS Phase 1 supports cluster create, describe, list, delete, tag, untag, and list-tags operations through the REST JSON API.',
            'Floci 1.5.22 makes real-mode cluster endpoints reachable and authenticable from the host.',
            'Floci 1.5.23 seeds standard EKS cluster and node group managed IAM policies so common EKS provisioning modules can attach them without explicit local creation.',
            'Mock mode stores cluster metadata in-process and marks clusters ACTIVE immediately; real mode starts a k3s container per cluster.',
            'Floci 1.5.24 adds managed node group create, describe, list, and delete support; the dashboard exposes node group create/delete flows and keeps the full inventory visible.',
            'Floci 1.5.33 adds Fargate profile create, describe, list, and delete support; the dashboard exposes profile create/delete flows and keeps selector, subnet, role, health, and tag details visible.',
        ],
    }

def _elasticache_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def elasticache_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    elasticache = factory.client('elasticache')

    cache_clusters = _safe_value(
        lambda: _paginate(
            elasticache,
            'describe_cache_clusters',
            'CacheClusters',
            ShowCacheNodeInfo=True,
            ShowCacheClustersNotInReplicationGroups=True,
        ),
        [],
    )
    replication_groups = _safe_value(lambda: _paginate(elasticache, 'describe_replication_groups', 'ReplicationGroups'), [])
    subnet_groups = _safe_value(lambda: _paginate(elasticache, 'describe_cache_subnet_groups', 'CacheSubnetGroups'), [])
    parameter_groups = _safe_value(lambda: _paginate(elasticache, 'describe_cache_parameter_groups', 'CacheParameterGroups'), [])
    security_groups = _safe_value(lambda: _paginate(elasticache, 'describe_cache_security_groups', 'CacheSecurityGroups'), [])
    snapshots = _safe_value(lambda: _paginate(elasticache, 'describe_snapshots', 'Snapshots'), [])
    users = _safe_value(lambda: _paginate(elasticache, 'describe_users', 'Users'), [])
    user_groups = _safe_value(lambda: _paginate(elasticache, 'describe_user_groups', 'UserGroups'), [])
    serverless_caches = _safe_value(lambda: _paginate(elasticache, 'describe_serverless_caches', 'ServerlessCaches'), [])
    global_replication_groups = _safe_value(lambda: _paginate(elasticache, 'describe_global_replication_groups', 'GlobalReplicationGroups'), [])
    events = _safe_value(lambda: _paginate(elasticache, 'describe_events', 'Events', MaxRecords=50), [])

    return {
        'summary': {
            'cache_clusters': len(cache_clusters),
            'replication_groups': len(replication_groups),
            'serverless_caches': len(serverless_caches),
            'subnet_groups': len(subnet_groups),
            'parameter_groups': len(parameter_groups),
            'snapshots': len(snapshots),
            'users': len(users),
            'user_groups': len(user_groups),
            'global_replication_groups': len(global_replication_groups),
            'events': len(events),
        },
        'cache_clusters': [
            {
                'name': cluster.get('CacheClusterId'),
                'arn': cluster.get('ARN'),
                'engine': cluster.get('Engine'),
                'engine_version': cluster.get('EngineVersion'),
                'status': cluster.get('CacheClusterStatus'),
                'node_type': cluster.get('CacheNodeType'),
                'num_cache_nodes': cluster.get('NumCacheNodes'),
                'preferred_availability_zone': cluster.get('PreferredAvailabilityZone'),
                'cache_subnet_group_name': cluster.get('CacheSubnetGroupName'),
                'replication_group_id': cluster.get('ReplicationGroupId'),
                'security_groups': cluster.get('SecurityGroups'),
                'cache_security_groups': cluster.get('CacheSecurityGroups'),
                'cache_parameter_group': cluster.get('CacheParameterGroup'),
                'cache_nodes': cluster.get('CacheNodes'),
                'configuration_endpoint': cluster.get('ConfigurationEndpoint'),
                'notification_configuration': cluster.get('NotificationConfiguration'),
                'pending_modified_values': cluster.get('PendingModifiedValues'),
                'created': cluster.get('CacheClusterCreateTime'),
                'preferred_maintenance_window': cluster.get('PreferredMaintenanceWindow'),
                'auto_minor_version_upgrade': cluster.get('AutoMinorVersionUpgrade'),
                'snapshot_retention_limit': cluster.get('SnapshotRetentionLimit'),
                'snapshot_window': cluster.get('SnapshotWindow'),
                'auth_token_enabled': cluster.get('AuthTokenEnabled'),
                'transit_encryption_enabled': cluster.get('TransitEncryptionEnabled'),
                'at_rest_encryption_enabled': cluster.get('AtRestEncryptionEnabled'),
                'log_delivery_configurations': cluster.get('LogDeliveryConfigurations'),
            }
            for cluster in cache_clusters
        ],
        'replication_groups': [
            {
                'name': group.get('ReplicationGroupId'),
                'arn': group.get('ARN'),
                'description': group.get('Description'),
                'status': group.get('Status'),
                'pending_modified_values': group.get('PendingModifiedValues'),
                'member_clusters': group.get('MemberClusters'),
                'node_groups': group.get('NodeGroups'),
                'snapshotting_cluster_id': group.get('SnapshottingClusterId'),
                'automatic_failover': group.get('AutomaticFailover'),
                'multi_az': group.get('MultiAZ'),
                'configuration_endpoint': group.get('ConfigurationEndpoint'),
                'snapshot_retention_limit': group.get('SnapshotRetentionLimit'),
                'snapshot_window': group.get('SnapshotWindow'),
                'cluster_enabled': group.get('ClusterEnabled'),
                'cache_node_type': group.get('CacheNodeType'),
                'auth_token_enabled': group.get('AuthTokenEnabled'),
                'transit_encryption_enabled': group.get('TransitEncryptionEnabled'),
                'at_rest_encryption_enabled': group.get('AtRestEncryptionEnabled'),
                'kms_key_id': group.get('KmsKeyId'),
                'user_group_ids': group.get('UserGroupIds'),
                'log_delivery_configurations': group.get('LogDeliveryConfigurations'),
                'data_tiering': group.get('DataTiering'),
            }
            for group in replication_groups
        ],
        'serverless_caches': [
            {
                'name': cache.get('ServerlessCacheName'),
                'arn': cache.get('ARN'),
                'engine': cache.get('Engine'),
                'major_engine_version': cache.get('MajorEngineVersion'),
                'full_engine_version': cache.get('FullEngineVersion'),
                'status': cache.get('Status'),
                'endpoint': cache.get('Endpoint'),
                'reader_endpoint': cache.get('ReaderEndpoint'),
                'description': cache.get('Description'),
                'cache_usage_limits': cache.get('CacheUsageLimits'),
                'kms_key_id': cache.get('KmsKeyId'),
                'security_group_ids': cache.get('SecurityGroupIds'),
                'subnet_ids': cache.get('SubnetIds'),
                'user_group_id': cache.get('UserGroupId'),
                'created': cache.get('CreateTime'),
                'snapshot_retention_limit': cache.get('SnapshotRetentionLimit'),
                'daily_snapshot_time': cache.get('DailySnapshotTime'),
            }
            for cache in serverless_caches
        ],
        'subnet_groups': subnet_groups,
        'parameter_groups': parameter_groups,
        'security_groups': security_groups,
        'snapshots': snapshots,
        'users': users,
        'user_groups': user_groups,
        'global_replication_groups': global_replication_groups,
        'events': events,
        'supported_from_sdk': [
            'CreateCacheCluster',
            'DescribeCacheClusters',
            'ModifyCacheCluster',
            'DeleteCacheCluster',
            'CreateReplicationGroup',
            'DescribeReplicationGroups',
            'ModifyReplicationGroup',
            'DeleteReplicationGroup',
            'CreateServerlessCache',
            'DescribeServerlessCaches',
            'ModifyServerlessCache',
            'DeleteServerlessCache',
            'CreateCacheSubnetGroup',
            'DescribeCacheSubnetGroups',
            'CreateCacheParameterGroup',
            'DescribeCacheParameterGroups',
            'CreateUser',
            'DescribeUsers',
            'CreateUserGroup',
            'DescribeUserGroups',
            'CreateSnapshot',
            'DescribeSnapshots',
            'DescribeEvents',
        ],
        'notes': [
            'Floci ElastiCache uses the AWS-compatible Query management API and Redis RESP for the data plane.',
            'CreateReplicationGroup starts a real Valkey or Redis Docker container and exposes it through a local TCP proxy port.',
            'Floci 1.5.24 returns reachable Memcached configuration endpoints for local cache clients.',
            'Floci 1.5.25 persists STS session secret keys so IAM-authenticated ElastiCache tokens validate across requests.',
            'Supported interactive workflows include replication-group lifecycle, ElastiCache users, user access strings, and IAM auth token validation.',
        ],
    }


def _ecs_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}

def _elasticloadbalancing_tags(elbv2, arns: list[str]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    for chunk in _chunks([arn for arn in arns if arn], 20):
        response = _safe_value(lambda chunk=chunk: elbv2.describe_tags(ResourceArns=chunk).get('TagDescriptions', []), [])
        for description in response:
            tags[description.get('ResourceArn')] = description.get('Tags', [])
    return tags


def _classic_elb_tags(elb, names: list[str]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    for chunk in _chunks([name for name in names if name], 20):
        response = _safe_value(lambda chunk=chunk: elb.describe_tags(LoadBalancerNames=chunk).get('TagDescriptions', []), [])
        for description in response:
            tags[description.get('LoadBalancerName')] = description.get('Tags', [])
    return tags


def elasticloadbalancing_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    elbv2 = factory.client('elbv2')
    elb = factory.client('elb')

    v2_load_balancers = _safe_value(lambda: _paginate(elbv2, 'describe_load_balancers', 'LoadBalancers'), [])
    v2_target_groups = _safe_value(lambda: _paginate(elbv2, 'describe_target_groups', 'TargetGroups'), [])
    classic_load_balancers = _safe_value(lambda: _paginate(elb, 'describe_load_balancers', 'LoadBalancerDescriptions'), [])
    classic_policy_types = _safe_value(lambda: elb.describe_load_balancer_policy_types().get('PolicyTypeDescriptions', []), [])

    v2_tags = _elasticloadbalancing_tags(elbv2, [lb.get('LoadBalancerArn') for lb in v2_load_balancers])
    target_group_tags = _elasticloadbalancing_tags(elbv2, [group.get('TargetGroupArn') for group in v2_target_groups])
    classic_tags = _classic_elb_tags(elb, [lb.get('LoadBalancerName') for lb in classic_load_balancers])

    listeners: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for load_balancer in v2_load_balancers:
        lb_arn = load_balancer.get('LoadBalancerArn')
        lb_listeners = _safe_value(lambda lb_arn=lb_arn: _paginate(elbv2, 'describe_listeners', 'Listeners', LoadBalancerArn=lb_arn), [])
        for listener in lb_listeners:
            listener['LoadBalancerName'] = load_balancer.get('LoadBalancerName')
            listeners.append(listener)
            listener_arn = listener.get('ListenerArn')
            listener_rules = _safe_value(lambda listener_arn=listener_arn: _paginate(elbv2, 'describe_rules', 'Rules', ListenerArn=listener_arn), [])
            for rule in listener_rules:
                rule['LoadBalancerName'] = load_balancer.get('LoadBalancerName')
                rule['ListenerArn'] = listener_arn
                rules.append(rule)

    target_health = []
    for target_group in v2_target_groups:
        arn = target_group.get('TargetGroupArn')
        response = _safe_value(lambda arn=arn: elbv2.describe_target_health(TargetGroupArn=arn).get('TargetHealthDescriptions', []), [])
        target_health.append({
            'name': target_group.get('TargetGroupName'),
            'target_group_arn': arn,
            'target_health_descriptions': response,
        })

    classic_instance_health = []
    classic_policies = []
    for load_balancer in classic_load_balancers:
        name = load_balancer.get('LoadBalancerName')
        classic_instance_health.append({
            'name': name,
            'instance_states': _safe_value(lambda name=name: elb.describe_instance_health(LoadBalancerName=name).get('InstanceStates', []), []),
        })
        classic_policies.append({
            'name': name,
            'policies': _safe_value(lambda name=name: elb.describe_load_balancer_policies(LoadBalancerName=name).get('PolicyDescriptions', []), []),
        })

    return {
        'summary': {
            'v2_load_balancers': len(v2_load_balancers),
            'classic_load_balancers': len(classic_load_balancers),
            'listeners': len(listeners),
            'rules': len(rules),
            'target_groups': len(v2_target_groups),
            'target_health_sets': len(target_health),
            'classic_policy_types': len(classic_policy_types),
        },
        'v2_load_balancers': [
            {
                'name': lb.get('LoadBalancerName'),
                'arn': lb.get('LoadBalancerArn'),
                'dns_name': lb.get('DNSName'),
                'canonical_hosted_zone_id': lb.get('CanonicalHostedZoneId'),
                'created': lb.get('CreatedTime'),
                'scheme': lb.get('Scheme'),
                'vpc_id': lb.get('VpcId'),
                'state': lb.get('State'),
                'type': lb.get('Type'),
                'availability_zones': lb.get('AvailabilityZones'),
                'security_groups': lb.get('SecurityGroups'),
                'ip_address_type': lb.get('IpAddressType'),
                'customer_owned_ipv4_pool': lb.get('CustomerOwnedIpv4Pool'),
                'enforce_security_group_inbound_rules_on_private_link_traffic': lb.get('EnforceSecurityGroupInboundRulesOnPrivateLinkTraffic'),
                'attributes': _safe_value(lambda arn=lb.get('LoadBalancerArn'): elbv2.describe_load_balancer_attributes(LoadBalancerArn=arn).get('Attributes', []), []),
                'tags': v2_tags.get(lb.get('LoadBalancerArn'), []),
            }
            for lb in v2_load_balancers
        ],
        'listeners': [
            {
                'name': listener.get('ListenerArn'),
                'load_balancer_name': listener.get('LoadBalancerName'),
                'arn': listener.get('ListenerArn'),
                'port': listener.get('Port'),
                'protocol': listener.get('Protocol'),
                'certificates': listener.get('Certificates'),
                'ssl_policy': listener.get('SslPolicy'),
                'default_actions': listener.get('DefaultActions'),
                'alpn_policy': listener.get('AlpnPolicy'),
                'mutual_authentication': listener.get('MutualAuthentication'),
            }
            for listener in listeners
        ],
        'rules': [
            {
                'name': rule.get('RuleArn'),
                'load_balancer_name': rule.get('LoadBalancerName'),
                'listener_arn': rule.get('ListenerArn'),
                'arn': rule.get('RuleArn'),
                'priority': rule.get('Priority'),
                'conditions': rule.get('Conditions'),
                'actions': rule.get('Actions'),
                'is_default': rule.get('IsDefault'),
            }
            for rule in rules
        ],
        'target_groups': [
            {
                'name': group.get('TargetGroupName'),
                'arn': group.get('TargetGroupArn'),
                'protocol': group.get('Protocol'),
                'port': group.get('Port'),
                'vpc_id': group.get('VpcId'),
                'health_check_protocol': group.get('HealthCheckProtocol'),
                'health_check_port': group.get('HealthCheckPort'),
                'health_check_enabled': group.get('HealthCheckEnabled'),
                'health_check_interval_seconds': group.get('HealthCheckIntervalSeconds'),
                'health_check_timeout_seconds': group.get('HealthCheckTimeoutSeconds'),
                'healthy_threshold_count': group.get('HealthyThresholdCount'),
                'unhealthy_threshold_count': group.get('UnhealthyThresholdCount'),
                'health_check_path': group.get('HealthCheckPath'),
                'matcher': group.get('Matcher'),
                'load_balancer_arns': group.get('LoadBalancerArns'),
                'target_type': group.get('TargetType'),
                'protocol_version': group.get('ProtocolVersion'),
                'ip_address_type': group.get('IpAddressType'),
                'tags': target_group_tags.get(group.get('TargetGroupArn'), []),
            }
            for group in v2_target_groups
        ],
        'target_health': target_health,
        'classic_load_balancers': [
            {
                'name': lb.get('LoadBalancerName'),
                'dns_name': lb.get('DNSName'),
                'canonical_hosted_zone_name': lb.get('CanonicalHostedZoneName'),
                'canonical_hosted_zone_name_id': lb.get('CanonicalHostedZoneNameID'),
                'listener_descriptions': lb.get('ListenerDescriptions'),
                'policies': lb.get('Policies'),
                'backend_server_descriptions': lb.get('BackendServerDescriptions'),
                'availability_zones': lb.get('AvailabilityZones'),
                'subnets': lb.get('Subnets'),
                'vpc_id': lb.get('VPCId'),
                'instances': lb.get('Instances'),
                'health_check': lb.get('HealthCheck'),
                'source_security_group': lb.get('SourceSecurityGroup'),
                'security_groups': lb.get('SecurityGroups'),
                'created': lb.get('CreatedTime'),
                'scheme': lb.get('Scheme'),
                'attributes': _safe_value(lambda name=lb.get('LoadBalancerName'): elb.describe_load_balancer_attributes(LoadBalancerName=name).get('LoadBalancerAttributes', {}), {}),
                'tags': classic_tags.get(lb.get('LoadBalancerName'), []),
            }
            for lb in classic_load_balancers
        ],
        'classic_instance_health': classic_instance_health,
        'classic_policies': classic_policies,
        'classic_policy_types': classic_policy_types,
        'supported_from_sdk': [
            'CreateLoadBalancer',
            'DescribeLoadBalancers',
            'DeleteLoadBalancer',
            'CreateListener',
            'DescribeListeners',
            'DescribeListenerAttributes',
            'ModifyListenerAttributes',
            'ModifyCapacityReservation',
            'DescribeCapacityReservation',
            'CreateRule',
            'DescribeRules',
            'CreateTargetGroup',
            'DescribeTargetGroups',
            'RegisterTargets',
            'DescribeTargetHealth',
            'DeregisterTargets',
            'AddTags',
            'DescribeTags',
            'RemoveTags',
            'CreateLoadBalancerListeners',
            'DescribeInstanceHealth',
            'CreateLoadBalancerPolicy',
            'DescribeLoadBalancerPolicies',
        ],
        'notes': [
            'This page combines the AWS elbv2 and classic elb SDK APIs under the Elastic Load Balancing service name.',
            'Floci 1.5.19 adds ELBv2 listener attribute and capacity reservation action families.',
            'Floci 1.5.24 validates subnet VPC consistency when creating load balancers, matching AWS behavior more closely.',
            'Floci 1.5.25 enforces ALB subnet availability-zone rules and improves ELBv2 target resolution and persistence.',
            'Floci 1.5.28 omits the terminal SSL policy marker for closer AWS compatibility.',
            'Floci 1.5.29 publishes ALB DNS names that resolve locally.',
            'Target health is fetched per target group; listener rules are fetched per listener.',
        ],
    }

def firehose_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    firehose = factory.client('firehose')
    operations = set(firehose.meta.service_model.operation_names)
    stream_names = _safe_value(lambda: _paginate(firehose, 'list_delivery_streams', 'DeliveryStreamNames'), [])

    def stream_tags(name: str) -> list[dict[str, Any]]:
        tags: list[dict[str, Any]] = []
        exclusive_start_key = None

        while True:
            kwargs = {'DeliveryStreamName': name, 'Limit': 50}
            if exclusive_start_key:
                kwargs['ExclusiveStartTagKey'] = exclusive_start_key
            response = _safe_value(lambda kwargs=kwargs: firehose.list_tags_for_delivery_stream(**kwargs), {})
            tags.extend(response.get('Tags', []) if isinstance(response, dict) else [])
            if not isinstance(response, dict) or not response.get('HasMoreTags') or not response.get('Tags'):
                break
            exclusive_start_key = response.get('Tags', [])[-1].get('Key')

        return tags

    def stream_detail(name: str) -> dict[str, Any]:
        response = _safe_value(
            lambda: firehose.describe_delivery_stream(DeliveryStreamName=name).get('DeliveryStreamDescription', {}),
            {},
        )
        destinations = response.get('Destinations', []) if isinstance(response, dict) else []

        return {
            'name': name,
            'arn': response.get('DeliveryStreamARN'),
            'status': response.get('DeliveryStreamStatus'),
            'type': response.get('DeliveryStreamType'),
            'version_id': response.get('VersionId'),
            'created': response.get('CreateTimestamp'),
            'updated': response.get('LastUpdateTimestamp'),
            'source': response.get('Source'),
            'delivery_stream_encryption_configuration': response.get('DeliveryStreamEncryptionConfiguration'),
            'failure_description': response.get('FailureDescription'),
            'has_more_destinations': response.get('HasMoreDestinations'),
            'destination_count': len(destinations),
            'destinations': destinations,
            'tags': stream_tags(name),
        }

    streams = [stream_detail(name) for name in stream_names]
    destinations = [
        {
            'name': destination.get('DestinationId'),
            'delivery_stream_name': stream.get('name'),
            'destination_id': destination.get('DestinationId'),
            's3_destination_description': destination.get('S3DestinationDescription'),
            'extended_s3_destination_description': destination.get('ExtendedS3DestinationDescription'),
            'redshift_destination_description': destination.get('RedshiftDestinationDescription'),
            'elasticsearch_destination_description': destination.get('ElasticsearchDestinationDescription'),
            'amazonopensearchservice_destination_description': destination.get('AmazonopensearchserviceDestinationDescription'),
            'splunk_destination_description': destination.get('SplunkDestinationDescription'),
            'http_endpoint_destination_description': destination.get('HttpEndpointDestinationDescription'),
            'snowflake_destination_description': destination.get('SnowflakeDestinationDescription'),
            'iceberg_destination_description': destination.get('IcebergDestinationDescription'),
        }
        for stream in streams
        for destination in stream.get('destinations', [])
    ]

    return {
        'summary': {
            'delivery_streams': len(streams),
            'active_streams': sum(1 for stream in streams if stream.get('status') == 'ACTIVE'),
            'destinations': len(destinations),
            'tagged_streams': sum(1 for stream in streams if stream.get('tags')),
            'extended_s3_destinations': sum(
                1 for destination in destinations
                if destination.get('extended_s3_destination_description')
            ),
            'available_sdk_operations': len(operations),
        },
        'delivery_streams': streams,
        'destinations': destinations,
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateDeliveryStream',
                'DescribeDeliveryStream',
                'ListDeliveryStreams',
                'DeleteDeliveryStream',
                'PutRecord',
                'PutRecordBatch',
                'UpdateDestination',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Incoming records are buffered in memory and flushed to S3 after every 5 records for immediate local feedback.',
            'Floci writes flushed records as raw NDJSON to the floci-firehose-results bucket.',
            'Destinations are flattened from each delivery stream so delivery configuration can be scanned together.',
            'Floci 1.5.31 lets EventBridge rules deliver events to Firehose delivery stream targets.',
            'Floci 1.5.30 returns ExtendedS3DestinationDescription and supports UpdateDestination.',
        ],
    }

def kinesis_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    kinesis = factory.client('kinesis')
    stream_names = _safe_value(lambda: _paginate(kinesis, 'list_streams', 'StreamNames'), [])
    account_settings = _safe_value(lambda: kinesis.describe_account_settings(), {})
    limits = _safe_value(lambda: kinesis.describe_limits(), {})

    def stream_tags(name: str) -> list[dict[str, Any]]:
        tags: list[dict[str, Any]] = []
        exclusive_start_key = None

        while True:
            kwargs = {'StreamName': name, 'Limit': 50}
            if exclusive_start_key:
                kwargs['ExclusiveStartTagKey'] = exclusive_start_key
            response = _safe_value(lambda kwargs=kwargs: kinesis.list_tags_for_stream(**kwargs), {})
            tags.extend(response.get('Tags', []) if isinstance(response, dict) else [])
            if not isinstance(response, dict) or not response.get('HasMoreTags') or not response.get('Tags'):
                break
            exclusive_start_key = response.get('Tags', [])[-1].get('Key')

        return tags

    def consumer_detail(stream_arn: str, consumer: dict[str, Any]) -> dict[str, Any]:
        consumer_arn = consumer.get('ConsumerARN')
        detail = _safe_value(
            lambda: kinesis.describe_stream_consumer(StreamARN=stream_arn, ConsumerARN=consumer_arn).get('ConsumerDescription', {}),
            {},
        )
        return {
            'name': consumer.get('ConsumerName') or detail.get('ConsumerName'),
            'stream_arn': stream_arn,
            'consumer_arn': consumer_arn or detail.get('ConsumerARN'),
            'status': consumer.get('ConsumerStatus') or detail.get('ConsumerStatus'),
            'creation_timestamp': consumer.get('ConsumerCreationTimestamp') or detail.get('ConsumerCreationTimestamp'),
        }

    def stream_detail(name: str) -> dict[str, Any]:
        summary = _safe_value(
            lambda: kinesis.describe_stream_summary(StreamName=name).get('StreamDescriptionSummary', {}),
            {},
        )
        description = _safe_value(
            lambda: kinesis.describe_stream(StreamName=name).get('StreamDescription', {}),
            {},
        )
        stream_arn = summary.get('StreamARN') or description.get('StreamARN')
        shards = _safe_value(lambda: _paginate(kinesis, 'list_shards', 'Shards', StreamName=name), description.get('Shards', []))
        consumers = _safe_value(lambda: _paginate(kinesis, 'list_stream_consumers', 'Consumers', StreamARN=stream_arn), []) if stream_arn else []
        consumer_details = [consumer_detail(stream_arn, consumer) for consumer in consumers] if stream_arn else []

        return {
            'name': name,
            'arn': stream_arn,
            'status': summary.get('StreamStatus') or description.get('StreamStatus'),
            'mode': summary.get('StreamModeDetails') or description.get('StreamModeDetails'),
            'retention_period_hours': summary.get('RetentionPeriodHours') or description.get('RetentionPeriodHours'),
            'created': summary.get('StreamCreationTimestamp') or description.get('StreamCreationTimestamp'),
            'enhanced_monitoring': summary.get('EnhancedMonitoring') or description.get('EnhancedMonitoring'),
            'encryption_type': summary.get('EncryptionType') or description.get('EncryptionType'),
            'key_id': summary.get('KeyId') or description.get('KeyId'),
            'open_shard_count': summary.get('OpenShardCount'),
            'consumer_count': summary.get('ConsumerCount') or len(consumer_details),
            'shard_count': len(shards),
            'shards': shards,
            'consumers': consumer_details,
            'tags': stream_tags(name),
        }

    streams = [stream_detail(name) for name in stream_names]
    shards = [
        {
            'name': shard.get('ShardId'),
            'stream_name': stream.get('name'),
            'shard_id': shard.get('ShardId'),
            'parent_shard_id': shard.get('ParentShardId'),
            'adjacent_parent_shard_id': shard.get('AdjacentParentShardId'),
            'hash_key_range': shard.get('HashKeyRange'),
            'sequence_number_range': shard.get('SequenceNumberRange'),
        }
        for stream in streams
        for shard in stream.get('shards', [])
    ]
    consumers = [
        consumer
        for stream in streams
        for consumer in stream.get('consumers', [])
    ]

    return {
        'summary': {
            'streams': len(streams),
            'active_streams': sum(1 for stream in streams if stream.get('status') == 'ACTIVE'),
            'shards': len(shards),
            'consumers': len(consumers),
            'tagged_streams': sum(1 for stream in streams if stream.get('tags')),
        },
        'streams': streams,
        'shards': shards,
        'consumers': consumers,
        'account_settings': account_settings,
        'limits': limits,
        'supported_from_sdk': [
            'CreateStream',
            'DescribeStream',
            'DescribeStreamSummary',
            'ListStreams',
            'DeleteStream',
            'ListShards',
            'PutRecord',
            'PutRecords',
            'GetShardIterator',
            'GetRecords',
            'RegisterStreamConsumer',
            'ListStreamConsumers',
            'DescribeStreamConsumer',
            'DeregisterStreamConsumer',
            'AddTagsToStream',
            'ListTagsForStream',
            'RemoveTagsFromStream',
            'EnableEnhancedMonitoring',
            'DisableEnhancedMonitoring',
            'StartStreamEncryption',
            'StopStreamEncryption',
            'UpdateShardCount',
            'UpdateStreamMode',
        ],
        'notes': [
            'This page is inferred from the Kinesis AWS SDK API because service docs were not provided for this pass.',
            'Shard and consumer details are flattened from each stream for scanning.',
        ],
    }

def kafka_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    kafka = factory.client('kafka')
    cluster_v1_summaries = _safe_value(lambda: _paginate(kafka, 'list_clusters', 'ClusterInfoList'), [])
    cluster_v2_summaries = _safe_value(lambda: _paginate(kafka, 'list_clusters_v2', 'ClusterInfoList'), [])
    configurations = _safe_value(lambda: _paginate(kafka, 'list_configurations', 'Configurations'), [])
    kafka_versions = _safe_value(lambda: _paginate(kafka, 'list_kafka_versions', 'KafkaVersions'), [])

    def cluster_arn(summary: dict[str, Any]) -> Optional[str]:
        return summary.get('ClusterArn') or summary.get('Provisioned', {}).get('ClusterArn') or summary.get('Serverless', {}).get('ClusterArn')

    def cluster_name(summary: dict[str, Any]) -> Optional[str]:
        return summary.get('ClusterName') or summary.get('Provisioned', {}).get('ClusterName') or summary.get('Serverless', {}).get('ClusterName')

    def cluster_detail(summary: dict[str, Any]) -> dict[str, Any]:
        arn = cluster_arn(summary)
        detail = _safe_value(lambda arn=arn: kafka.describe_cluster_v2(ClusterArn=arn).get('ClusterInfo', {}), {}) if arn else {}
        if not detail and arn:
            detail = _safe_value(lambda arn=arn: kafka.describe_cluster(ClusterArn=arn).get('ClusterInfo', {}), {})
        provisioned = detail.get('Provisioned') or summary.get('Provisioned') or {}
        serverless = detail.get('Serverless') or summary.get('Serverless') or {}
        effective = provisioned or serverless or detail or summary
        nodes = _safe_value(lambda arn=arn: _paginate(kafka, 'list_nodes', 'NodeInfoList', ClusterArn=arn), []) if arn else []
        operations = _safe_value(lambda arn=arn: _paginate(kafka, 'list_cluster_operations', 'ClusterOperationInfoList', ClusterArn=arn), []) if arn else []
        scram_secrets = _safe_value(lambda arn=arn: _paginate(kafka, 'list_scram_secrets', 'SecretArnList', ClusterArn=arn), []) if arn else []
        vpc_connections = _safe_value(lambda arn=arn: _paginate(kafka, 'list_client_vpc_connections', 'ClientVpcConnections', ClusterArn=arn), []) if arn else []
        bootstrap_brokers = _safe_value(lambda arn=arn: kafka.get_bootstrap_brokers(ClusterArn=arn), {}) if arn else {}
        tags = _safe_value(lambda arn=arn: kafka.list_tags_for_resource(ResourceArn=arn).get('Tags', {}), {}) if arn else {}

        return {
            'name': cluster_name(summary) or effective.get('ClusterName') or arn,
            'arn': arn,
            'type': detail.get('ClusterType') or summary.get('ClusterType') or ('SERVERLESS' if serverless else 'PROVISIONED'),
            'state': detail.get('State') or summary.get('State') or effective.get('State'),
            'created': detail.get('CreationTime') or summary.get('CreationTime') or effective.get('CreationTime'),
            'current_version': detail.get('CurrentVersion') or effective.get('CurrentVersion'),
            'kafka_version': effective.get('CurrentBrokerSoftwareInfo', {}).get('KafkaVersion') or effective.get('KafkaVersion'),
            'number_of_broker_nodes': effective.get('NumberOfBrokerNodes'),
            'broker_node_group_info': effective.get('BrokerNodeGroupInfo'),
            'serverless': serverless,
            'provisioned': provisioned,
            'encryption_info': effective.get('EncryptionInfo'),
            'client_authentication': effective.get('ClientAuthentication'),
            'logging_info': effective.get('LoggingInfo'),
            'open_monitoring': effective.get('OpenMonitoring'),
            'storage_mode': effective.get('StorageMode'),
            'connectivity_info': effective.get('ConnectivityInfo'),
            'bootstrap_brokers': bootstrap_brokers,
            'node_count': len(nodes),
            'operation_count': len(operations),
            'scram_secret_count': len(scram_secrets),
            'vpc_connection_count': len(vpc_connections),
            'nodes': nodes,
            'operations': operations,
            'scram_secrets': scram_secrets,
            'client_vpc_connections': vpc_connections,
            'tags': tags,
        }

    clusters_by_arn: dict[str, dict[str, Any]] = {}
    for summary in [*cluster_v1_summaries, *cluster_v2_summaries]:
        arn = cluster_arn(summary) or cluster_name(summary) or str(len(clusters_by_arn))
        clusters_by_arn.setdefault(arn, summary)

    clusters = [cluster_detail(summary) for summary in clusters_by_arn.values()]
    nodes = [
        {
            'name': node.get('BrokerNodeInfo', {}).get('BrokerId') or node.get('NodeARN'),
            'cluster_name': cluster.get('name'),
            'node_arn': node.get('NodeARN'),
            'node_type': node.get('NodeType'),
            'broker_node_info': node.get('BrokerNodeInfo'),
            'zookeeper_node_info': node.get('ZookeeperNodeInfo'),
        }
        for cluster in clusters
        for node in cluster.get('nodes', [])
    ]
    operations = [
        {
            'name': operation.get('ClusterOperationArn'),
            'cluster_name': cluster.get('name'),
            'operation_arn': operation.get('ClusterOperationArn'),
            'operation_type': operation.get('OperationType'),
            'operation_state': operation.get('OperationState'),
            'creation_time': operation.get('CreationTime'),
            'end_time': operation.get('EndTime'),
            'source_cluster_info': operation.get('SourceClusterInfo'),
            'target_cluster_info': operation.get('TargetClusterInfo'),
        }
        for cluster in clusters
        for operation in cluster.get('operations', [])
    ]
    configuration_details = [
        {
            'name': configuration.get('Name'),
            'arn': configuration.get('Arn'),
            'description': configuration.get('Description'),
            'kafka_versions': configuration.get('KafkaVersions'),
            'latest_revision': configuration.get('LatestRevision'),
            'state': configuration.get('State'),
            'created': configuration.get('CreationTime'),
        }
        for configuration in configurations
    ]

    return {
        'summary': {
            'clusters': len(clusters),
            'nodes': len(nodes),
            'operations': len(operations),
            'configurations': len(configuration_details),
            'kafka_versions': len(kafka_versions),
            'scram_secrets': sum(cluster.get('scram_secret_count') or 0 for cluster in clusters),
            'client_vpc_connections': sum(cluster.get('vpc_connection_count') or 0 for cluster in clusters),
        },
        'clusters': clusters,
        'nodes': nodes,
        'operations': operations,
        'configurations': configuration_details,
        'kafka_versions': kafka_versions,
        'supported_from_sdk': [
            'CreateCluster',
            'CreateClusterV2',
            'DescribeCluster',
            'DescribeClusterV2',
            'ListClusters',
            'ListClustersV2',
            'DeleteCluster',
            'GetBootstrapBrokers',
        ],
        'notes': [
            'Floci emulates Amazon MSK by orchestrating one Redpanda container per cluster unless mock mode is enabled.',
            'CreateClusterV2 is mapped to provisioned cluster creation for local compatibility.',
            'Kafka API port 9092 is mapped to a dynamic host port, and GetBootstrapBrokers returns the local connection strings.',
            'Floci 1.5.28 advertises externally reachable broker addresses for local clients.',
            'Floci 1.5.25 populates currentBrokerSoftwareInfo in cluster describe responses.',
            'Each cluster gets a named Docker volume; in persistent modes the volume is retained on delete unless volume pruning is enabled.',
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


def _ecs_operation_supported(client, operation_name: str) -> bool:
    return operation_name in client.meta.service_model.operation_names


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


def ecs_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    ecs = factory.client('ecs')
    cluster_arns = _safe_value(lambda: _paginate(ecs, 'list_clusters', 'clusterArns'), [])
    active_task_definition_arns = _safe_value(
        lambda: _paginate(ecs, 'list_task_definitions', 'taskDefinitionArns', status='ACTIVE'),
        [],
    )
    inactive_task_definition_arns = _safe_value(
        lambda: _paginate(ecs, 'list_task_definitions', 'taskDefinitionArns', status='INACTIVE'),
        [],
    )
    task_definition_arns = list(dict.fromkeys(active_task_definition_arns + inactive_task_definition_arns))
    task_definition_families = _safe_value(lambda: _paginate(ecs, 'list_task_definition_families', 'families'), [])

    def list_tags(arn: Optional[str]) -> Any:
        if not arn:
            return None

        return _ecs_optional(
            lambda: ecs.list_tags_for_resource(resourceArn=arn).get('tags', []),
            {'ClusterNotFoundException', 'ClientException', 'InvalidParameterException'},
        )

    def describe_task_definition(arn: str) -> dict[str, Any]:
        response = _ecs_optional(
            lambda: ecs.describe_task_definition(taskDefinition=arn, include=['TAGS']),
            {'ClientException', 'InvalidParameterException'},
        )

        if not isinstance(response, dict) or response.get('error'):
            return {'name': arn, 'arn': arn, 'details': response}

        task_definition = response.get('taskDefinition', {})
        return {
            'name': f"{task_definition.get('family')}:{task_definition.get('revision')}" if task_definition.get('family') else arn,
            'arn': task_definition.get('taskDefinitionArn') or arn,
            'family': task_definition.get('family'),
            'revision': task_definition.get('revision'),
            'status': task_definition.get('status'),
            'network_mode': task_definition.get('networkMode'),
            'requires_compatibilities': task_definition.get('requiresCompatibilities'),
            'cpu': task_definition.get('cpu'),
            'memory': task_definition.get('memory'),
            'task_role_arn': task_definition.get('taskRoleArn'),
            'execution_role_arn': task_definition.get('executionRoleArn'),
            'registered_at': task_definition.get('registeredAt'),
            'deregistered_at': task_definition.get('deregisteredAt'),
            'containers': [
                {
                    'name': container.get('name'),
                    'image': container.get('image'),
                    'cpu': container.get('cpu'),
                    'memory': container.get('memory'),
                    'essential': container.get('essential'),
                    'port_mappings': container.get('portMappings'),
                    'command': container.get('command'),
                    'entry_point': container.get('entryPoint'),
                    'environment': container.get('environment'),
                    'secrets': container.get('secrets'),
                    'mount_points': container.get('mountPoints'),
                    'health_check': container.get('healthCheck'),
                    'log_configuration': container.get('logConfiguration'),
                    'depends_on': container.get('dependsOn'),
                }
                for container in task_definition.get('containerDefinitions', [])
            ],
            'container_definitions': task_definition.get('containerDefinitions', []),
            'volumes': task_definition.get('volumes'),
            'placement_constraints': task_definition.get('placementConstraints'),
            'runtime_platform': task_definition.get('runtimePlatform'),
            'ephemeral_storage': task_definition.get('ephemeralStorage'),
            'proxy_configuration': task_definition.get('proxyConfiguration'),
            'ipc_mode': task_definition.get('ipcMode'),
            'pid_mode': task_definition.get('pidMode'),
            'inference_accelerators': task_definition.get('inferenceAccelerators'),
            'tags': response.get('tags') or list_tags(task_definition.get('taskDefinitionArn') or arn),
        }

    def cluster_detail(cluster_arn: str) -> dict[str, Any]:
        response = _ecs_optional(
            lambda: ecs.describe_clusters(
                clusters=[cluster_arn],
                include=['ATTACHMENTS', 'CONFIGURATIONS', 'SETTINGS', 'STATISTICS', 'TAGS'],
            ),
            {'ClusterNotFoundException'},
        )

        if not isinstance(response, dict) or response.get('error'):
            return {'name': cluster_arn, 'arn': cluster_arn, 'details': response}

        cluster = (response.get('clusters') or [{}])[0]
        name = cluster.get('clusterName') or cluster_arn
        task_arns = _ecs_optional(
            lambda: _paginate(ecs, 'list_tasks', 'taskArns', cluster=cluster_arn),
            {'ClusterNotFoundException'},
        )
        stopped_task_arns = _ecs_optional(
            lambda: _paginate(ecs, 'list_tasks', 'taskArns', cluster=cluster_arn, desiredStatus='STOPPED'),
            {'ClusterNotFoundException'},
        )
        if isinstance(task_arns, list) and isinstance(stopped_task_arns, list):
            task_arns = list(dict.fromkeys(task_arns + stopped_task_arns))
        service_arns = _ecs_optional(
            lambda: _paginate(ecs, 'list_services', 'serviceArns', cluster=cluster_arn),
            {'ClusterNotFoundException'},
        )
        container_instance_arns = _ecs_optional(
            lambda: _paginate(ecs, 'list_container_instances', 'containerInstanceArns', cluster=cluster_arn),
            {'ClusterNotFoundException'},
        )

        tasks: list[dict[str, Any]] = []
        if isinstance(task_arns, list):
            for batch in _chunks(task_arns, 100):
                task_response = _ecs_optional(
                    lambda batch=batch: ecs.describe_tasks(cluster=cluster_arn, tasks=batch, include=['TAGS']).get('tasks', []),
                    {'ClusterNotFoundException', 'InvalidParameterException'},
                )
                if isinstance(task_response, list):
                    tasks.extend(_clean_response(task_response))
            for batch in _chunks(task_arns, 10):
                protection_response = _ecs_optional(
                    lambda batch=batch: ecs.get_task_protection(cluster=cluster_arn, tasks=batch),
                    {'ClusterNotFoundException', 'InvalidParameterException'},
                )
                protected = protection_response.get('protectedTasks', []) if isinstance(protection_response, dict) else []
                protection_by_task = {item.get('taskArn'): item for item in protected if item.get('taskArn')}
                for task in tasks:
                    if task.get('taskArn') in protection_by_task:
                        task['protection'] = protection_by_task[task['taskArn']]

        services: list[dict[str, Any]] = []
        task_sets: list[dict[str, Any]] = []
        service_deployments: list[Any] = []
        service_revisions: list[Any] = []
        if isinstance(service_arns, list):
            for batch in _chunks(service_arns, 10):
                service_response = _ecs_optional(
                    lambda batch=batch: ecs.describe_services(cluster=cluster_arn, services=batch, include=['TAGS']).get('services', []),
                    {'ClusterNotFoundException', 'ServiceNotFoundException', 'InvalidParameterException'},
                )
                if isinstance(service_response, list):
                    services.extend(_clean_response(service_response))

            for service_arn in service_arns:
                sets = _ecs_optional(
                    lambda service_arn=service_arn: ecs.describe_task_sets(cluster=cluster_arn, service=service_arn).get('taskSets', []),
                    {'ClusterNotFoundException', 'ServiceNotFoundException'},
                )
                if isinstance(sets, list):
                    task_sets.extend(_clean_response(sets))

                if _ecs_operation_supported(ecs, 'ListServiceDeployments'):
                    deployments = _ecs_optional(
                        lambda service_arn=service_arn: _paginate(
                            ecs,
                            'list_service_deployments',
                            'serviceDeployments',
                            cluster=cluster_arn,
                            service=service_arn,
                        ),
                        {'ClusterNotFoundException', 'ServiceNotFoundException'},
                    )
                    if isinstance(deployments, list):
                        service_deployments.extend(deployments)

                if _ecs_operation_supported(ecs, 'DescribeServiceRevisions') and service_deployments:
                    revision_arns = [
                        item.get('serviceRevisionArn')
                        for item in service_deployments
                        if isinstance(item, dict) and item.get('serviceRevisionArn')
                    ]
                    if revision_arns:
                        revisions = _ecs_optional(
                            lambda revision_arns=revision_arns[:10]: ecs.describe_service_revisions(
                                serviceRevisionArns=revision_arns[:10],
                            ).get('serviceRevisions', []),
                            {'InvalidParameterException'},
                        )
                        if isinstance(revisions, list):
                            service_revisions.extend(_clean_response(revisions))

        container_instances: list[dict[str, Any]] = []
        if isinstance(container_instance_arns, list):
            for batch in _chunks(container_instance_arns, 100):
                container_response = _ecs_optional(
                    lambda batch=batch: ecs.describe_container_instances(
                        cluster=cluster_arn,
                        containerInstances=batch,
                    ).get('containerInstances', []),
                    {'ClusterNotFoundException', 'InvalidParameterException'},
                )
                if isinstance(container_response, list):
                    container_instances.extend(_clean_response(container_response))

        return {
            'name': name,
            'arn': cluster.get('clusterArn') or cluster_arn,
            'status': cluster.get('status'),
            'running_tasks': cluster.get('runningTasksCount'),
            'pending_tasks': cluster.get('pendingTasksCount'),
            'active_services': cluster.get('activeServicesCount'),
            'registered_container_instances': cluster.get('registeredContainerInstancesCount'),
            'capacity_providers': cluster.get('capacityProviders'),
            'default_capacity_provider_strategy': cluster.get('defaultCapacityProviderStrategy'),
            'settings': cluster.get('settings'),
            'configuration': cluster.get('configuration'),
            'statistics': cluster.get('statistics'),
            'attachments': cluster.get('attachments'),
            'tags': cluster.get('tags') or list_tags(cluster.get('clusterArn') or cluster_arn),
            'task_count': len(tasks),
            'service_count': len(services),
            'task_set_count': len(task_sets),
            'container_instance_count': len(container_instances),
            'service_deployment_count': len(service_deployments),
            'tasks': tasks,
            'services': services,
            'task_sets': task_sets,
            'container_instances': container_instances,
            'service_deployments': service_deployments,
            'service_revisions': service_revisions,
        }

    def parallel_map(values: list[str], loader: Callable[[str], dict[str, Any]]) -> list[dict[str, Any]]:
        if len(values) < 2:
            return [loader(value) for value in values]
        with ThreadPoolExecutor(max_workers=min(8, len(values))) as executor:
            return list(executor.map(loader, values))

    detailed_clusters = parallel_map(cluster_arns, cluster_detail)
    task_definitions = parallel_map(task_definition_arns, describe_task_definition)
    task_definition_groups: dict[str, list[dict[str, Any]]] = {}
    for definition in task_definitions:
        task_definition_groups.setdefault(definition.get('family') or 'Unknown', []).append(definition)
    for revisions in task_definition_groups.values():
        revisions.sort(key=lambda item: item.get('revision') or 0, reverse=True)
    capacity_providers = _ecs_optional(
        lambda: ecs.describe_capacity_providers().get('capacityProviders', []),
        {'ClientException'},
    )
    account_settings = _ecs_optional(
        lambda: _paginate(ecs, 'list_account_settings', 'settings'),
        {'ClientException'},
    )
    attributes = _ecs_optional(
        lambda: ecs.list_attributes(targetType='container-instance').get('attributes', []),
        {'ClientException'},
    )
    agent_poll_endpoint = _ecs_optional(
        lambda: ecs.discover_poll_endpoint(),
        {'ClientException'},
    )

    return {
        'summary': {
            'clusters': len(detailed_clusters),
            'task_definitions': len(task_definitions),
            'task_definition_families': len(task_definition_families),
            'tasks': sum(cluster.get('task_count') or 0 for cluster in detailed_clusters),
            'services': sum(cluster.get('service_count') or 0 for cluster in detailed_clusters),
            'task_sets': sum(cluster.get('task_set_count') or 0 for cluster in detailed_clusters),
            'container_instances': sum(cluster.get('container_instance_count') or 0 for cluster in detailed_clusters),
            'capacity_providers': len(capacity_providers) if isinstance(capacity_providers, list) else 0,
        },
        'clusters': detailed_clusters,
        'task_definitions': task_definitions,
        'task_definition_groups': [
            {'family': family, 'revisions': revisions}
            for family, revisions in sorted(task_definition_groups.items())
        ],
        'task_definition_families': task_definition_families,
        'capacity_providers': capacity_providers,
        'account_settings': account_settings,
        'attributes': attributes,
        'agent_poll_endpoint': agent_poll_endpoint,
        'supported': {
            'Clusters': ['CreateCluster', 'DescribeClusters', 'ListClusters', 'UpdateCluster', 'UpdateClusterSettings', 'PutClusterCapacityProviders', 'DeleteCluster'],
            'Task definitions': ['RegisterTaskDefinition', 'DescribeTaskDefinition', 'ListTaskDefinitions', 'ListTaskDefinitionFamilies', 'DeregisterTaskDefinition', 'DeleteTaskDefinitions'],
            'Tasks': ['RunTask', 'StartTask', 'StopTask', 'DescribeTasks', 'ListTasks', 'UpdateTaskProtection', 'GetTaskProtection'],
            'Services': ['CreateService', 'UpdateService', 'DeleteService', 'DescribeServices', 'ListServices', 'ListServicesByNamespace'],
            'Task sets': ['CreateTaskSet', 'UpdateTaskSet', 'DeleteTaskSet', 'DescribeTaskSets', 'UpdateServicePrimaryTaskSet'],
            'Container instances': ['RegisterContainerInstance', 'DeregisterContainerInstance', 'DescribeContainerInstances', 'ListContainerInstances', 'UpdateContainerAgent', 'UpdateContainerInstancesState'],
            'Capacity providers': ['CreateCapacityProvider', 'UpdateCapacityProvider', 'DeleteCapacityProvider', 'DescribeCapacityProviders'],
            'Service deployments and revisions': ['DescribeServiceDeployments', 'ListServiceDeployments', 'DescribeServiceRevisions'],
            'Tags': ['TagResource', 'UntagResource', 'ListTagsForResource'],
            'Account settings and attributes': ['PutAccountSetting', 'PutAccountSettingDefault', 'DeleteAccountSetting', 'ListAccountSettings', 'PutAttributes', 'DeleteAttributes', 'ListAttributes'],
            'Agent stubs': ['SubmitTaskStateChange', 'SubmitContainerStateChange', 'SubmitAttachmentStateChanges', 'DiscoverPollEndpoint'],
        },
        'configuration': {
            'mock': False,
            'docker_network': '',
            'default_memory_mb': 512,
            'default_cpu_units': 256,
        },
        'environment_variables': [
            'FLOCI_SERVICES_ECS_ENABLED',
            'FLOCI_SERVICES_ECS_MOCK',
            'FLOCI_SERVICES_ECS_DOCKER_NETWORK',
            'FLOCI_SERVICES_ECS_DEFAULT_MEMORY_MB',
            'FLOCI_SERVICES_ECS_DEFAULT_CPU_UNITS',
        ],
        'notes': [
            'By default Floci ECS launches real Docker containers for tasks.',
            'Floci 1.5.29 honors task portMappings hostPort values, registers persisted model records for native-image reflection, and supports idempotent CloudFormation stack-delete cleanup.',
            'Floci 1.5.28 mounts efsVolumeConfiguration task volumes as shared local volumes.',
            'Floci 1.5.23 honors task-definition volumes and container mountPoints for stateful local task workflows.',
            'Floci 1.5.22 honors RunTask command and environment container overrides and preserves create-time resource tags.',
            'Floci 1.5.25 validates Fargate task network mode and resource requirements more closely.',
            'Floci 1.5.27 persists ECS durable resources across restart.',
            'Floci 1.5.23 retains inactive services for Terraform draining and recreation idempotency.',
            'Floci 1.5.18 registers ECS service containers as ELBv2 targets.',
            'Set FLOCI_SERVICES_ECS_MOCK=true to run tasks as in-process stubs for CI or tests.',
            'When mock mode is false, Floci needs access to the Docker socket.',
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


def _ecr_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def ecr_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    ecr = factory.client('ecr')
    repositories = _safe_value(lambda: _paginate(ecr, 'describe_repositories', 'repositories'), [])

    def repository_detail(repository: dict[str, Any]) -> dict[str, Any]:
        name = repository.get('repositoryName')
        arn = repository.get('repositoryArn')
        image_ids = _ecr_optional(
            lambda: _paginate(ecr, 'list_images', 'imageIds', repositoryName=name),
            {'RepositoryNotFoundException'},
        )
        image_details = _ecr_optional(
            lambda: _paginate(ecr, 'describe_images', 'imageDetails', repositoryName=name),
            {'RepositoryNotFoundException', 'ImageNotFoundException'},
        )

        return {
            'name': name,
            'arn': arn,
            'registry_id': repository.get('registryId'),
            'uri': repository.get('repositoryUri'),
            'created': repository.get('createdAt'),
            'tag_mutability': repository.get('imageTagMutability'),
            'encryption_configuration': repository.get('encryptionConfiguration'),
            'scanning_configuration': repository.get('imageScanningConfiguration'),
            'image_count': len(image_ids) if isinstance(image_ids, list) else 0,
            'images': image_ids,
            'image_details': image_details,
            'tags': _ecr_optional(
                lambda: ecr.list_tags_for_resource(resourceArn=arn).get('tags', []),
                {'RepositoryNotFoundException', 'InvalidParameterException'},
            ) if arn else None,
            'lifecycle_policy': _ecr_optional(
                lambda: ecr.get_lifecycle_policy(repositoryName=name),
                {'LifecyclePolicyNotFoundException', 'RepositoryNotFoundException'},
            ),
            'repository_policy': _ecr_optional(
                lambda: ecr.get_repository_policy(repositoryName=name),
                {'RepositoryPolicyNotFoundException', 'RepositoryNotFoundException'},
            ),
        }

    auth_data = _ecr_optional(
        lambda: ecr.get_authorization_token().get('authorizationData', []),
        {'InvalidParameterException'},
    )
    auth_endpoints = []
    if isinstance(auth_data, list):
        auth_endpoints = [
            {
                'name': item.get('proxyEndpoint') or 'ECR proxy endpoint',
                'proxy_endpoint': item.get('proxyEndpoint'),
                'expires_at': item.get('expiresAt'),
            }
            for item in auth_data
        ]

    if len(repositories) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(repositories))) as executor:
            detailed_repositories = list(executor.map(repository_detail, repositories))
    else:
        detailed_repositories = [repository_detail(repository) for repository in repositories]

    return {
        'summary': {
            'repositories': len(detailed_repositories),
            'images': sum(repository.get('image_count') or 0 for repository in detailed_repositories),
            'tagged_images': sum(
                1
                for repository in detailed_repositories
                for image in repository.get('images', [])
                if isinstance(image, dict) and image.get('imageTag')
            ),
            'auth_endpoints': len(auth_endpoints),
            'storage_bytes': sum(
                image.get('imageSizeInBytes') or 0
                for repository in detailed_repositories
                for image in (repository.get('image_details') if isinstance(repository.get('image_details'), list) else [])
            ),
        },
        'repositories': detailed_repositories,
        'auth_endpoints': auth_endpoints,
        'supported': [
            'CreateRepository',
            'DescribeRepositories',
            'DeleteRepository',
            'GetAuthorizationToken',
            'ListImages',
            'DescribeImages',
            'BatchGetImage',
            'BatchDeleteImage',
            'PutImageTagMutability',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
            'PutLifecyclePolicy',
            'GetLifecyclePolicy',
            'DeleteLifecyclePolicy',
            'SetRepositoryPolicy',
            'GetRepositoryPolicy',
            'DeleteRepositoryPolicy',
        ],
        'admin_endpoints': ['POST /_floci/ecr/gc'],
        'configuration': {
            'registry_image': 'registry:2',
            'registry_container_name': 'floci-ecr-registry',
            'registry_base_port': 5100,
            'registry_max_port': 5199,
            'data_path': './data/ecr',
            'tls_enabled': False,
            'keep_running_on_shutdown': True,
            'uri_style': 'hostname',
        },
        'environment_variables': [
            'FLOCI_SERVICES_ECR_ENABLED',
            'FLOCI_SERVICES_ECR_REGISTRY_IMAGE',
            'FLOCI_SERVICES_ECR_REGISTRY_CONTAINER_NAME',
            'FLOCI_SERVICES_ECR_REGISTRY_BASE_PORT',
            'FLOCI_SERVICES_ECR_REGISTRY_MAX_PORT',
            'FLOCI_SERVICES_ECR_DATA_PATH',
            'FLOCI_SERVICES_ECR_KEEP_RUNNING_ON_SHUTDOWN',
            'FLOCI_SERVICES_ECR_URI_STYLE',
            'FLOCI_SERVICES_ECR_TLS_ENABLED',
        ],
        'not_implemented': [
            'Replication and pull-through cache',
            'Image scanning',
            'Image signing and notary attachments',
            'Lifecycle policy enforcement',
            'Repository policy enforcement',
            'TLS via emulated ACM',
        ],
        'notes': [
            'The control plane uses JSON 1.1 and the data plane is a real OCI Distribution v2 registry.',
            'Repository URIs use loopback hostnames like <account>.dkr.ecr.<region>.localhost:<port>/<repo> by default.',
            'GetAuthorizationToken returns a docker login token, but the dashboard only displays proxy endpoints.',
            'Deleted image blobs are reclaimed by POST /_floci/ecr/gc.',
            'Floci 1.5.19 publishes official Floci images to the AWS ECR Public Gallery as well as Docker Hub.',
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


def _glue_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def _glue_reader_for_table(table: dict[str, Any]) -> str:
    storage = table.get('StorageDescriptor') or {}
    input_format = (storage.get('InputFormat') or '').lower()
    serde = ((storage.get('SerdeInfo') or {}).get('SerializationLibrary') or '').lower()
    haystack = f'{input_format} {serde}'

    if 'parquet' in haystack:
        return 'read_parquet'

    if 'json' in haystack or 'hive' in input_format:
        return 'read_json_auto'

    return 'read_csv_auto'


def _glue_table_column_names(table: dict[str, Any]) -> list[str]:
    storage = table.get('StorageDescriptor') or {}
    columns = storage.get('Columns') or []
    partition_keys = table.get('PartitionKeys') or []
    names: list[str] = []
    for column in [*columns, *partition_keys]:
        if not isinstance(column, dict):
            continue
        name = column.get('Name')
        if name and name not in names:
            names.append(name)
    return names


def glue_inventory() -> dict[str, Any]:
    factory = FlociClientFactory()
    glue = factory.client('glue')
    operations = set(glue.meta.service_model.operation_names)
    databases = _safe_value(lambda: _paginate(glue, 'get_databases', 'DatabaseList'), [])
    registries = _glue_optional(lambda: _paginate(glue, 'list_registries', 'Registries'), {'EntityNotFoundException'})

    def table_detail(database_name: str, table: dict[str, Any]) -> dict[str, Any]:
        table_name = table.get('Name')
        storage = table.get('StorageDescriptor') or {}
        column_names = _glue_table_column_names(table)
        partitions = _glue_optional(
            lambda: _paginate(glue, 'get_partitions', 'Partitions', DatabaseName=database_name, TableName=table_name),
            {'EntityNotFoundException'},
        )
        table_statistics = _glue_optional(
            lambda: glue.get_column_statistics_for_table(
                DatabaseName=database_name,
                TableName=table_name,
                ColumnNames=column_names,
            ).get('ColumnStatisticsList', []),
            {'EntityNotFoundException', 'OperationNotFoundException'},
        ) if column_names and 'GetColumnStatisticsForTable' in operations else []
        if not isinstance(table_statistics, list):
            table_statistics = []
        versions = _glue_optional(
            lambda: _paginate(glue, 'get_table_versions', 'TableVersions', DatabaseName=database_name, TableName=table_name),
            {'EntityNotFoundException', 'OperationNotFoundException'},
        ) if 'GetTableVersions' in operations else []
        if not isinstance(versions, list):
            versions = []

        return {
            'name': table_name,
            'database': database_name,
            'type': table.get('TableType'),
            'created': table.get('CreateTime'),
            'updated': table.get('UpdateTime'),
            'owner': table.get('Owner'),
            'retention': table.get('Retention'),
            'location': storage.get('Location'),
            'input_format': storage.get('InputFormat'),
            'output_format': storage.get('OutputFormat'),
            'serde': storage.get('SerdeInfo'),
            'columns': storage.get('Columns'),
            'partition_keys': table.get('PartitionKeys'),
            'parameters': table.get('Parameters'),
            'duckdb_reader': _glue_reader_for_table(table),
            'partition_count': len(partitions) if isinstance(partitions, list) else 0,
            'partitions': partitions,
            'column_statistics_count': len(table_statistics),
            'column_statistics': table_statistics,
            'version_count': len(versions),
            'versions': versions,
        }

    def database_detail(database: dict[str, Any]) -> dict[str, Any]:
        name = database.get('Name')
        details = _glue_optional(
            lambda: glue.get_database(Name=name).get('Database', {}),
            {'EntityNotFoundException'},
        )
        tables = _glue_optional(
            lambda: _paginate(glue, 'get_tables', 'TableList', DatabaseName=name),
            {'EntityNotFoundException'},
        )
        functions = _glue_optional(
            lambda: _paginate(glue, 'get_user_defined_functions', 'UserDefinedFunctions', DatabaseName=name, Pattern='*'),
            {'EntityNotFoundException'},
        )
        arn = database.get('DatabaseArn') or database.get('Arn') or (details.get('DatabaseArn') if isinstance(details, dict) else None)
        tags = _glue_optional(
            lambda: glue.get_tags(ResourceArn=arn).get('Tags', {}),
            {'EntityNotFoundException', 'OperationNotFoundException'},
        ) if arn and 'GetTags' in operations else None
        table_details = [
            table_detail(name, table)
            for table in tables
        ] if isinstance(tables, list) else []
        function_details = [
            {
                'name': function.get('FunctionName'),
                'database': name,
                'class_name': function.get('ClassName'),
                'owner_name': function.get('OwnerName'),
                'owner_type': function.get('OwnerType'),
                'created': function.get('CreateTime'),
                'resource_uris': function.get('ResourceUris'),
            }
            for function in functions
        ] if isinstance(functions, list) else []

        return {
            'name': name,
            'description': database.get('Description') or (details.get('Description') if isinstance(details, dict) else None),
            'location_uri': database.get('LocationUri') or (details.get('LocationUri') if isinstance(details, dict) else None),
            'parameters': database.get('Parameters') or (details.get('Parameters') if isinstance(details, dict) else None),
            'created': database.get('CreateTime') or (details.get('CreateTime') if isinstance(details, dict) else None),
            'arn': arn,
            'tags': tags,
            'tables': table_details,
            'functions': function_details,
            'table_count': len(table_details),
            'function_count': len(function_details),
            'partition_count': sum(table.get('partition_count') or 0 for table in table_details),
            'table_version_count': sum(table.get('version_count') or 0 for table in table_details),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_databases = [database_detail(database) for database in databases]
    functions = [function for database in detailed_databases for function in database.get('functions', [])]

    def schema_detail(registry_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        schema_name = schema.get('SchemaName') or schema.get('SchemaArn') or schema.get('name')
        versions = _glue_optional(
            lambda: _paginate(
                glue,
                'list_schema_versions',
                'Schemas',
                SchemaId={'RegistryName': registry_name, 'SchemaName': schema_name},
            ),
            {'EntityNotFoundException'},
        )
        if not isinstance(versions, list):
            versions = _glue_optional(
                lambda: _paginate(
                    glue,
                    'list_schema_versions',
                    'SchemaVersions',
                    SchemaId={'RegistryName': registry_name, 'SchemaName': schema_name},
                ),
                {'EntityNotFoundException'},
            )

        return {
            'name': schema_name,
            'registry': registry_name,
            'arn': schema.get('SchemaArn'),
            'status': schema.get('SchemaStatus') or schema.get('Status'),
            'data_format': schema.get('DataFormat'),
            'compatibility': schema.get('Compatibility'),
            'created': schema.get('CreatedTime'),
            'updated': schema.get('UpdatedTime'),
            'version_count': len(versions) if isinstance(versions, list) else 0,
            'versions': versions,
        }

    def registry_detail(registry: dict[str, Any]) -> dict[str, Any]:
        name = registry.get('RegistryName') or registry.get('Name')
        schemas = _glue_optional(
            lambda: _paginate(glue, 'list_schemas', 'Schemas', RegistryId={'RegistryName': name}),
            {'EntityNotFoundException'},
        )
        schema_details = [
            schema_detail(name, schema)
            for schema in schemas
        ] if isinstance(schemas, list) else []

        return {
            'name': name,
            'arn': registry.get('RegistryArn'),
            'description': registry.get('Description'),
            'status': registry.get('Status') or registry.get('RegistryStatus'),
            'created': registry.get('CreatedTime'),
            'updated': registry.get('UpdatedTime'),
            'schema_count': len(schema_details),
            'version_count': sum(schema.get('version_count') or 0 for schema in schema_details),
            'schemas': schema_details,
            'details': schemas if isinstance(schemas, dict) and schemas.get('error') else None,
        }

    detailed_registries = [
        registry_detail(registry)
        for registry in registries
    ] if isinstance(registries, list) else []

    return {
        'summary': {
            'databases': len(detailed_databases),
            'tables': sum(database.get('table_count') or 0 for database in detailed_databases),
            'partitions': sum(database.get('partition_count') or 0 for database in detailed_databases),
            'column_statistics': sum(
                table.get('column_statistics_count') or 0
                for database in detailed_databases
                for table in database.get('tables', [])
            ),
            'table_versions': sum(database.get('table_version_count') or 0 for database in detailed_databases),
            'functions': len(functions),
            'registries': len(detailed_registries),
            'schemas': sum(registry.get('schema_count') or 0 for registry in detailed_registries),
            'schema_versions': sum(registry.get('version_count') or 0 for registry in detailed_registries),
        },
        'databases': detailed_databases,
        'functions': functions,
        'registries': detailed_registries,
        'supported': {
            'Databases': ['CreateDatabase', 'GetDatabase', 'GetDatabases', 'DeleteDatabase', 'UpdateDatabase'],
            'Tables': ['CreateTable', 'GetTable', 'GetTables', 'DeleteTable', 'BatchDeleteTable', 'UpdateTable', 'GetTableVersions'],
            'Partitions': ['CreatePartition', 'BatchCreatePartition', 'GetPartition', 'GetPartitions', 'DeletePartition'],
            'Statistics': ['GetColumnStatisticsForTable', 'DeleteColumnStatisticsForTable'],
            'User-defined functions': ['CreateUserDefinedFunction', 'GetUserDefinedFunction', 'GetUserDefinedFunctions', 'UpdateUserDefinedFunction', 'DeleteUserDefinedFunction'],
            'Registries': ['CreateRegistry', 'GetRegistry', 'ListRegistries', 'UpdateRegistry', 'DeleteRegistry'],
            'Schemas': ['CreateSchema', 'GetSchema', 'ListSchemas', 'UpdateSchema', 'DeleteSchema'],
            'Versions': ['RegisterSchemaVersion', 'GetSchemaVersion', 'ListSchemaVersions', 'DeleteSchemaVersions'],
        },
        'athena_integration': [
            'Athena reads Glue tables for the target database and creates DuckDB views over S3 objects.',
            'The reader function is inferred from StorageDescriptor.InputFormat and SerDe serialization library.',
        ],
        'format_inference': {
            'parquet': 'read_parquet',
            'json_or_hive': 'read_json_auto',
            'default': 'read_csv_auto',
        },
        'notes': [
            'Floci 1.5.25 adds Glue partition APIs, column statistics, table statistics deletion, missing-table delete rejection, and deterministic GetPartitions ordering.',
            'Floci 1.5.23 adds Glue user-defined functions and DeleteDatabase support.',
            'Floci 1.5.23 enforces Glue table version checks and preserves Glue view table fields.',
            'Floci 1.5.24 normalizes catalog names, preserves column parameters, supports UpdateDatabase and BatchDeleteTable, archives table versions, and exposes database tags.',
        ],
    }


def list_iam_users(factory: FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': user.get('UserName'),
            'arn': user.get('Arn'),
            'created': user.get('CreateDate'),
        }
        for user in factory.client('iam').get_paginator('list_users').paginate().build_full_result().get('Users', [])
    ]


def list_iam_roles(factory: FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': role.get('RoleName'),
            'arn': role.get('Arn'),
            'created': role.get('CreateDate'),
        }
        for role in factory.client('iam').get_paginator('list_roles').paginate().build_full_result().get('Roles', [])
    ]


def list_sqs_queues(factory: FlociClientFactory) -> list[dict[str, Any]]:
    urls = factory.client('sqs').list_queues().get('QueueUrls', [])
    return _string_items(urls, 'url')


def list_dynamodb_tables(factory: FlociClientFactory) -> list[dict[str, Any]]:
    names = factory.client('dynamodb').get_paginator('list_tables').paginate().build_full_result().get('TableNames', [])
    return _string_items(names, 'name')


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


def list_ecr_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    ecr = factory.client('ecr')
    resources = [
        {
            'type': 'repository',
            'name': repository.get('repositoryName'),
            'arn': repository.get('repositoryArn'),
            'uri': repository.get('repositoryUri'),
        }
        for repository in _safe_value(lambda: _paginate(ecr, 'describe_repositories', 'repositories'), [])
    ]

    for repository in list(resources):
        name = repository.get('name')
        resources.extend(
            {
                'type': 'image',
                'repository': name,
                'tag': image.get('imageTag'),
                'digest': image.get('imageDigest'),
            }
            for image in _safe_value(lambda name=name: _paginate(ecr, 'list_images', 'imageIds', repositoryName=name), [])
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


def list_glue_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    glue = factory.client('glue')
    resources = [
        {
            'type': 'database',
            'name': database.get('Name'),
        }
        for database in _safe_value(lambda: _paginate(glue, 'get_databases', 'DatabaseList'), [])
    ]
    for database in list(resources):
        database_name = database.get('name')
        tables = _safe_value(lambda database_name=database_name: _paginate(glue, 'get_tables', 'TableList', DatabaseName=database_name), [])
        resources.extend(
            {
                'type': 'table',
                'database': database_name,
                'name': table.get('Name'),
                'location': (table.get('StorageDescriptor') or {}).get('Location'),
            }
            for table in tables
        )
        for table in tables:
            table_name = table.get('Name')
            resources.extend(
                {
                    'type': 'partition',
                    'database': database_name,
                    'table': table_name,
                    'values': partition.get('Values'),
                }
                for partition in _safe_value(
                    lambda database_name=database_name, table_name=table_name: _paginate(
                        glue,
                        'get_partitions',
                        'Partitions',
                        DatabaseName=database_name,
                        TableName=table_name,
                    ),
                    [],
                )
            )
        resources.extend(
            {
                'type': 'user_defined_function',
                'database': database_name,
                'name': function.get('FunctionName'),
                'class_name': function.get('ClassName'),
            }
            for function in _safe_value(
                lambda database_name=database_name: _paginate(
                    glue,
                    'get_user_defined_functions',
                    'UserDefinedFunctions',
                    DatabaseName=database_name,
                    Pattern='*',
                ),
                [],
            )
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


def list_kms_keys(factory: FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'id': key.get('KeyId'),
            'arn': key.get('KeyArn'),
        }
        for key in factory.client('kms').get_paginator('list_keys').paginate().build_full_result().get('Keys', [])
    ]


def list_secrets(factory: FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': secret.get('Name'),
            'arn': secret.get('ARN'),
        }
        for secret in factory.client('secretsmanager').get_paginator('list_secrets').paginate().build_full_result().get('SecretList', [])
    ]


def list_eventbridge_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    events = factory.client('events')
    resources: list[dict[str, Any]] = []
    buses = _safe_value(lambda: _paginate(events, 'list_event_buses', 'EventBuses'), [])

    if not buses:
        buses = [{'Name': 'default'}]

    for bus in buses:
        name = bus.get('Name') or 'default'
        resources.append({
            'type': 'event_bus',
            'name': name,
            'arn': bus.get('Arn'),
        })
        rules = _safe_value(lambda name=name: _paginate(events, 'list_rules', 'Rules', EventBusName=name), [])
        resources.extend(
            {
                'type': 'rule',
                'event_bus': name,
                'name': rule.get('Name'),
                'arn': rule.get('Arn'),
                'state': rule.get('State'),
            }
            for rule in rules
        )
        for rule in rules:
            rule_name = rule.get('Name')
            resources.extend(
                {
                    'type': 'target',
                    'event_bus': name,
                    'rule': rule_name,
                    'id': target.get('Id'),
                    'arn': target.get('Arn'),
                }
                for target in _safe_value(
                    lambda name=name, rule_name=rule_name: _paginate(
                        events,
                        'list_targets_by_rule',
                        'Targets',
                        Rule=rule_name,
                        EventBusName=name,
                    ),
                    [],
                )
            )

    return resources


def list_ecs_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    ecs = factory.client('ecs')
    resources: list[dict[str, Any]] = []
    clusters = _safe_value(lambda: _paginate(ecs, 'list_clusters', 'clusterArns'), [])
    task_definitions = _safe_value(lambda: _paginate(ecs, 'list_task_definitions', 'taskDefinitionArns'), [])
    resources.extend({'type': 'cluster', 'arn': arn} for arn in clusters)
    resources.extend({'type': 'task_definition', 'arn': arn} for arn in task_definitions)

    for cluster in clusters:
        tasks = _safe_value(lambda cluster=cluster: _paginate(ecs, 'list_tasks', 'taskArns', cluster=cluster), [])
        services = _safe_value(lambda cluster=cluster: _paginate(ecs, 'list_services', 'serviceArns', cluster=cluster), [])
        container_instances = _safe_value(lambda cluster=cluster: _paginate(ecs, 'list_container_instances', 'containerInstanceArns', cluster=cluster), [])
        resources.extend({'type': 'task', 'cluster': cluster, 'arn': arn} for arn in tasks)
        resources.extend({'type': 'service', 'cluster': cluster, 'arn': arn} for arn in services)
        resources.extend({'type': 'container_instance', 'cluster': cluster, 'arn': arn} for arn in container_instances)

    return resources


def list_eks_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    eks = factory.client('eks')
    resources: list[dict[str, Any]] = []
    cluster_names = _safe_value(lambda: _paginate(eks, 'list_clusters', 'clusters'), [])

    for cluster_name in cluster_names:
        resources.append({
            'type': 'cluster',
            'name': cluster_name,
        })
        resources.extend(
            {
                'type': 'nodegroup',
                'cluster': cluster_name,
                'name': nodegroup,
            }
            for nodegroup in _safe_value(
                lambda cluster_name=cluster_name: _paginate(eks, 'list_nodegroups', 'nodegroups', clusterName=cluster_name),
                [],
            )
        )
        resources.extend(
            {
                'type': 'fargate_profile',
                'cluster': cluster_name,
                'name': profile,
            }
            for profile in _safe_value(
                lambda cluster_name=cluster_name: _paginate(eks, 'list_fargate_profiles', 'fargateProfileNames', clusterName=cluster_name),
                [],
            )
        )
        resources.extend(
            {
                'type': 'addon',
                'cluster': cluster_name,
                'name': addon,
            }
            for addon in _safe_value(
                lambda cluster_name=cluster_name: _paginate(eks, 'list_addons', 'addons', clusterName=cluster_name),
                [],
            )
        )
        resources.extend(
            {
                'type': 'identity_provider_config',
                'cluster': cluster_name,
                'name': config.get('name'),
                'provider_type': config.get('type'),
            }
            for config in _safe_value(
                lambda cluster_name=cluster_name: _paginate(
                    eks,
                    'list_identity_provider_configs',
                    'identityProviderConfigs',
                    clusterName=cluster_name,
                ),
                [],
            )
            if isinstance(config, dict)
        )
        resources.extend(
            {
                'type': 'access_entry',
                'cluster': cluster_name,
                'principal_arn': principal,
            }
            for principal in _safe_value(
                lambda cluster_name=cluster_name: _paginate(eks, 'list_access_entries', 'accessEntries', clusterName=cluster_name),
                [],
            )
        )

    return resources


def list_elasticache_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    elasticache = factory.client('elasticache')
    resources: list[dict[str, Any]] = []
    resources.extend(
        {
            'type': 'cache_cluster',
            'name': cluster.get('CacheClusterId'),
            'engine': cluster.get('Engine'),
            'status': cluster.get('CacheClusterStatus'),
        }
        for cluster in _safe_value(lambda: _paginate(elasticache, 'describe_cache_clusters', 'CacheClusters'), [])
    )
    resources.extend(
        {
            'type': 'replication_group',
            'name': group.get('ReplicationGroupId'),
            'status': group.get('Status'),
        }
        for group in _safe_value(lambda: _paginate(elasticache, 'describe_replication_groups', 'ReplicationGroups'), [])
    )
    resources.extend(
        {
            'type': 'serverless_cache',
            'name': cache.get('ServerlessCacheName'),
            'engine': cache.get('Engine'),
            'status': cache.get('Status'),
        }
        for cache in _safe_value(lambda: _paginate(elasticache, 'describe_serverless_caches', 'ServerlessCaches'), [])
    )
    resources.extend(
        {
            'type': 'subnet_group',
            'name': group.get('CacheSubnetGroupName'),
        }
        for group in _safe_value(lambda: _paginate(elasticache, 'describe_cache_subnet_groups', 'CacheSubnetGroups'), [])
    )
    resources.extend(
        {
            'type': 'parameter_group',
            'name': group.get('CacheParameterGroupName'),
        }
        for group in _safe_value(lambda: _paginate(elasticache, 'describe_cache_parameter_groups', 'CacheParameterGroups'), [])
    )
    resources.extend(
        {
            'type': 'snapshot',
            'name': snapshot.get('SnapshotName'),
            'status': snapshot.get('SnapshotStatus'),
        }
        for snapshot in _safe_value(lambda: _paginate(elasticache, 'describe_snapshots', 'Snapshots'), [])
    )
    resources.extend(
        {
            'type': 'user',
            'name': user.get('UserName') or user.get('UserId'),
            'id': user.get('UserId'),
        }
        for user in _safe_value(lambda: _paginate(elasticache, 'describe_users', 'Users'), [])
    )
    resources.extend(
        {
            'type': 'user_group',
            'name': group.get('UserGroupId'),
            'status': group.get('Status'),
        }
        for group in _safe_value(lambda: _paginate(elasticache, 'describe_user_groups', 'UserGroups'), [])
    )
    return resources


def list_elasticloadbalancing_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    elbv2 = factory.client('elbv2')
    elb = factory.client('elb')
    resources: list[dict[str, Any]] = []
    v2_load_balancers = _safe_value(lambda: _paginate(elbv2, 'describe_load_balancers', 'LoadBalancers'), [])
    target_groups = _safe_value(lambda: _paginate(elbv2, 'describe_target_groups', 'TargetGroups'), [])
    classic_load_balancers = _safe_value(lambda: _paginate(elb, 'describe_load_balancers', 'LoadBalancerDescriptions'), [])

    resources.extend(
        {
            'type': 'v2_load_balancer',
            'name': load_balancer.get('LoadBalancerName'),
            'arn': load_balancer.get('LoadBalancerArn'),
            'dns_name': load_balancer.get('DNSName'),
            'state': load_balancer.get('State'),
        }
        for load_balancer in v2_load_balancers
    )
    for load_balancer in v2_load_balancers:
        arn = load_balancer.get('LoadBalancerArn')
        resources.extend(
            {
                'type': 'listener',
                'load_balancer': load_balancer.get('LoadBalancerName'),
                'arn': listener.get('ListenerArn'),
                'protocol': listener.get('Protocol'),
                'port': listener.get('Port'),
            }
            for listener in _safe_value(
                lambda arn=arn: _paginate(elbv2, 'describe_listeners', 'Listeners', LoadBalancerArn=arn),
                [],
            )
        )
    resources.extend(
        {
            'type': 'target_group',
            'name': group.get('TargetGroupName'),
            'arn': group.get('TargetGroupArn'),
            'protocol': group.get('Protocol'),
            'port': group.get('Port'),
        }
        for group in target_groups
    )
    resources.extend(
        {
            'type': 'classic_load_balancer',
            'name': load_balancer.get('LoadBalancerName'),
            'dns_name': load_balancer.get('DNSName'),
            'scheme': load_balancer.get('Scheme'),
        }
        for load_balancer in classic_load_balancers
    )
    return resources


def list_firehose_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    firehose = factory.client('firehose')
    stream_names = _safe_value(lambda: _paginate(firehose, 'list_delivery_streams', 'DeliveryStreamNames'), [])
    resources: list[dict[str, Any]] = []

    for name in stream_names:
        description = _safe_value(
            lambda name=name: firehose.describe_delivery_stream(DeliveryStreamName=name).get('DeliveryStreamDescription', {}),
            {},
        )
        resources.append({
            'type': 'delivery_stream',
            'name': name,
            'arn': description.get('DeliveryStreamARN'),
            'status': description.get('DeliveryStreamStatus'),
            'stream_type': description.get('DeliveryStreamType'),
        })
        resources.extend(
            {
                'type': 'destination',
                'delivery_stream': name,
                'id': destination.get('DestinationId'),
            }
            for destination in description.get('Destinations', [])
        )

    return resources


def list_kinesis_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    kinesis = factory.client('kinesis')
    stream_names = _safe_value(lambda: _paginate(kinesis, 'list_streams', 'StreamNames'), [])
    resources: list[dict[str, Any]] = []

    for name in stream_names:
        summary = _safe_value(
            lambda name=name: kinesis.describe_stream_summary(StreamName=name).get('StreamDescriptionSummary', {}),
            {},
        )
        stream_arn = summary.get('StreamARN')
        resources.append({
            'type': 'stream',
            'name': name,
            'arn': stream_arn,
            'status': summary.get('StreamStatus'),
            'open_shard_count': summary.get('OpenShardCount'),
        })
        resources.extend(
            {
                'type': 'shard',
                'stream': name,
                'id': shard.get('ShardId'),
            }
            for shard in _safe_value(
                lambda name=name: _paginate(kinesis, 'list_shards', 'Shards', StreamName=name),
                [],
            )
        )
        if stream_arn:
            resources.extend(
                {
                    'type': 'consumer',
                    'stream': name,
                    'name': consumer.get('ConsumerName'),
                    'arn': consumer.get('ConsumerARN'),
                    'status': consumer.get('ConsumerStatus'),
                }
                for consumer in _safe_value(
                    lambda stream_arn=stream_arn: _paginate(kinesis, 'list_stream_consumers', 'Consumers', StreamARN=stream_arn),
                    [],
                )
            )

    return resources


def list_kafka_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    kafka = factory.client('kafka')
    resources: list[dict[str, Any]] = []
    clusters = _safe_value(lambda: _paginate(kafka, 'list_clusters_v2', 'ClusterInfoList'), [])
    if not clusters:
        clusters = _safe_value(lambda: _paginate(kafka, 'list_clusters', 'ClusterInfoList'), [])

    for cluster in clusters:
        arn = cluster.get('ClusterArn') or cluster.get('Provisioned', {}).get('ClusterArn') or cluster.get('Serverless', {}).get('ClusterArn')
        name = cluster.get('ClusterName') or cluster.get('Provisioned', {}).get('ClusterName') or cluster.get('Serverless', {}).get('ClusterName')
        resources.append({
            'type': 'cluster',
            'name': name,
            'arn': arn,
            'state': cluster.get('State'),
            'cluster_type': cluster.get('ClusterType'),
        })
        if arn:
            resources.extend(
                {
                    'type': 'node',
                    'cluster': name,
                    'arn': node.get('NodeARN'),
                    'node_type': node.get('NodeType'),
                }
                for node in _safe_value(lambda arn=arn: _paginate(kafka, 'list_nodes', 'NodeInfoList', ClusterArn=arn), [])
            )
            resources.extend(
                {
                    'type': 'operation',
                    'cluster': name,
                    'arn': operation.get('ClusterOperationArn'),
                    'operation_type': operation.get('OperationType'),
                    'state': operation.get('OperationState'),
                }
                for operation in _safe_value(lambda arn=arn: _paginate(kafka, 'list_cluster_operations', 'ClusterOperationInfoList', ClusterArn=arn), [])
            )

    resources.extend(
        {
            'type': 'configuration',
            'name': configuration.get('Name'),
            'arn': configuration.get('Arn'),
            'state': configuration.get('State'),
        }
        for configuration in _safe_value(lambda: _paginate(kafka, 'list_configurations', 'Configurations'), [])
    )
    return resources


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


def list_costexplorer_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    ce = factory.client('ce')
    operations = set(ce.meta.service_model.operation_names)
    today = date.today()
    time_period = {
        'Start': today.replace(day=1).isoformat(),
        'End': (today + timedelta(days=1)).isoformat(),
    }
    resources: list[dict[str, Any]] = []

    if 'GetDimensionValues' in operations:
        for dimension in ['SERVICE', 'REGION']:
            resources.extend(
                {
                    'type': 'cost_dimension',
                    'name': value.get('Value'),
                    'dimension': dimension,
                    'attributes': value.get('Attributes'),
                }
                for value in _safe_value(
                    lambda dimension=dimension: _paginate(
                        ce,
                        'get_dimension_values',
                        'DimensionValues',
                        TimePeriod=time_period,
                        Dimension=dimension,
                    ),
                    [],
                )
            )

    if 'GetCostCategories' in operations:
        resources.extend(
            {
                'type': 'cost_category',
                'name': category,
            }
            for category in _safe_value(
                lambda: _paginate(ce, 'get_cost_categories', 'CostCategoryNames', TimePeriod=time_period),
                [],
            )
        )

    return resources


def list_cur_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    cur = factory.client('cur')
    operations = set(cur.meta.service_model.operation_names)

    if 'DescribeReportDefinitions' not in operations:
        return []

    return [
        {
            'type': 'report_definition',
            'name': report.get('ReportName'),
            's3_bucket': report.get('S3Bucket'),
            'time_unit': report.get('TimeUnit'),
        }
        for report in _safe_value(
            lambda: _operation_items(cur, 'describe_report_definitions', 'ReportDefinitions'),
            [],
        )
    ]


def list_emr_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    emr = factory.client('emr')
    operations = set(emr.meta.service_model.operation_names)
    if 'ListClusters' not in operations:
        return []

    return [
        {
            'type': 'cluster',
            'name': cluster.get('Name') or cluster.get('Id'),
            'id': cluster.get('Id'),
            'state': (cluster.get('Status') or {}).get('State'),
        }
        for cluster in _safe_value(lambda: _operation_items(emr, 'list_clusters', 'Clusters'), [])
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


def list_iot_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    iot = factory.client('iot')
    operations = set(iot.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListThings' in operations:
        resources.extend(
            {
                'type': 'thing',
                'name': thing.get('thingName'),
                'arn': thing.get('thingArn'),
                'thing_type': thing.get('thingTypeName'),
            }
            for thing in _safe_value(lambda: _operation_items(iot, 'list_things', 'things'), [])
        )
    if 'ListPolicies' in operations:
        resources.extend(
            {
                'type': 'policy',
                'name': policy.get('policyName'),
                'arn': policy.get('policyArn'),
            }
            for policy in _safe_value(lambda: _operation_items(iot, 'list_policies', 'policies'), [])
        )
    if 'ListCertificates' in operations:
        resources.extend(
            {
                'type': 'certificate',
                'id': certificate.get('certificateId'),
                'arn': certificate.get('certificateArn'),
                'status': certificate.get('status'),
            }
            for certificate in _safe_value(lambda: _operation_items(iot, 'list_certificates', 'certificates'), [])
        )

    return resources


def list_elasticbeanstalk_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    beanstalk = factory.client('elasticbeanstalk')
    operations = set(beanstalk.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'DescribeApplications' in operations:
        resources.extend(
            {
                'type': 'application',
                'name': application.get('ApplicationName'),
                'arn': application.get('ApplicationArn'),
            }
            for application in _safe_value(lambda: _operation_items(beanstalk, 'describe_applications', 'Applications'), [])
        )
    if 'DescribeEnvironments' in operations:
        resources.extend(
            {
                'type': 'environment',
                'name': environment.get('EnvironmentName'),
                'id': environment.get('EnvironmentId'),
                'arn': environment.get('EnvironmentArn'),
                'status': environment.get('Status'),
            }
            for environment in _safe_value(lambda: _operation_items(beanstalk, 'describe_environments', 'Environments'), [])
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


def list_docdb_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    docdb = factory.client('docdb')
    operations = set(docdb.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'DescribeDBClusters' in operations:
        resources.extend(
            {
                'type': 'db_cluster',
                'name': cluster.get('DBClusterIdentifier'),
                'arn': cluster.get('DBClusterArn'),
                'status': cluster.get('Status'),
            }
            for cluster in _safe_value(lambda: _operation_items(docdb, 'describe_db_clusters', 'DBClusters'), [])
        )

    if 'DescribeDBInstances' in operations:
        resources.extend(
            {
                'type': 'db_instance',
                'name': instance.get('DBInstanceIdentifier'),
                'arn': instance.get('DBInstanceArn'),
                'status': instance.get('DBInstanceStatus'),
            }
            for instance in _safe_value(lambda: _operation_items(docdb, 'describe_db_instances', 'DBInstances'), [])
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


def list_ec2_resources(factory: FlociClientFactory) -> list[dict[str, Any]]:
    ec2 = factory.client('ec2')
    resources: list[dict[str, Any]] = []
    reservations = _safe_value(lambda: ec2.describe_instances().get('Reservations', []), [])
    resources.extend(
        {
            'type': 'instance',
            'id': instance.get('InstanceId'),
            'state': instance.get('State', {}).get('Name'),
        }
        for reservation in reservations
        for instance in reservation.get('Instances', [])
    )
    resources.extend({'type': 'vpc', 'id': item.get('VpcId')} for item in _safe_value(lambda: ec2.describe_vpcs().get('Vpcs', []), []))
    resources.extend({'type': 'subnet', 'id': item.get('SubnetId')} for item in _safe_value(lambda: ec2.describe_subnets().get('Subnets', []), []))
    resources.extend({'type': 'security_group', 'id': item.get('GroupId')} for item in _safe_value(lambda: ec2.describe_security_groups().get('SecurityGroups', []), []))
    resources.extend({'type': 'internet_gateway', 'id': item.get('InternetGatewayId')} for item in _safe_value(lambda: ec2.describe_internet_gateways().get('InternetGateways', []), []))
    resources.extend({'type': 'route_table', 'id': item.get('RouteTableId')} for item in _safe_value(lambda: ec2.describe_route_tables().get('RouteTables', []), []))
    resources.extend({'type': 'network_acl', 'id': item.get('NetworkAclId')} for item in _safe_value(lambda: ec2.describe_network_acls().get('NetworkAcls', []), []))
    resources.extend({'type': 'vpc_endpoint', 'id': item.get('VpcEndpointId')} for item in _safe_value(lambda: _paginate(ec2, 'describe_vpc_endpoints', 'VpcEndpoints'), []))
    resources.extend({'type': 'elastic_ip', 'id': item.get('AllocationId') or item.get('PublicIp')} for item in _safe_value(lambda: ec2.describe_addresses().get('Addresses', []), []))
    resources.extend({'type': 'key_pair', 'id': item.get('KeyName')} for item in _safe_value(lambda: ec2.describe_key_pairs().get('KeyPairs', []), []))
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
        ('eks-resources', 'EKS resources', list_eks_resources),
        ('elasticache-resources', 'ElastiCache resources', list_elasticache_resources),
        ('elasticbeanstalk-resources', 'Elastic Beanstalk resources', list_elasticbeanstalk_resources),
        ('elasticloadbalancing-resources', 'Elastic Load Balancing resources', list_elasticloadbalancing_resources),
        ('emr-resources', 'EMR resources', list_emr_resources),
        ('firehose-resources', 'Data Firehose resources', list_firehose_resources),
        ('kinesis-resources', 'Kinesis resources', list_kinesis_resources),
        ('kafka-resources', 'MSK / Kafka resources', list_kafka_resources),
        ('opensearch-resources', 'OpenSearch resources', list_opensearch_resources),
        ('pipes-resources', 'EventBridge Pipes resources', list_pipes_resources),
        ('pricing-resources', 'AWS Price List resources', list_pricing_resources),
        ('costexplorer-resources', 'Cost Explorer resources', list_costexplorer_resources),
        ('cur-resources', 'Cost and Usage Reports', list_cur_resources),
        ('bcmdataexports-resources', 'BCM Data Exports', bcmdataexports.list_bcmdataexports_resources),
        ('memorydb-resources', 'MemoryDB resources', list_memorydb_resources),
        ('neptune-resources', 'Neptune resources', list_neptune_resources),
        ('resourcegroupstagging-resources', 'Resource Groups Tagging resources', list_resourcegroupstagging_resources),
        ('ssm-resources', 'SSM resources', list_ssm_resources),
        ('cloudformation-resources', 'CloudFormation resources', cloudformation.list_cloudformation_resources),
        ('ecr-resources', 'ECR resources', list_ecr_resources),
        ('glue-resources', 'Glue resources', list_glue_resources),
        ('rds-resources', 'RDS resources', list_rds_resources),
        ('rdsdata-resources', 'RDS Data API operations', list_rdsdata_resources),
        ('route53-resources', 'Route 53 resources', list_route53_resources),
        ('iam-users', 'IAM users', list_iam_users),
        ('iam-roles', 'IAM roles', list_iam_roles),
        ('iot-resources', 'IoT Core resources', list_iot_resources),
        ('ec2-resources', 'EC2 resources', list_ec2_resources),
        ('ecs-resources', 'ECS resources', list_ecs_resources),
        ('s3-buckets', 'S3 buckets', s3.list_s3_buckets),
        ('s3vectors-resources', 'S3 Vectors resources', list_s3vectors_resources),
        ('sqs-queues', 'SQS queues', list_sqs_queues),
        ('dynamodb-tables', 'DynamoDB tables', list_dynamodb_tables),
        ('docdb-resources', 'DocumentDB resources', list_docdb_resources),
        ('sns-topics', 'SNS topics', list_sns_topics),
        ('ses-resources', 'SES resources', list_ses_resources),
        ('scheduler-resources', 'EventBridge Scheduler resources', list_scheduler_resources),
        ('stepfunctions-resources', 'Step Functions resources', list_stepfunctions_resources),
        ('textract-resources', 'Textract resources', list_textract_resources),
        ('transcribe-resources', 'Transcribe resources', list_transcribe_resources),
        ('transfer-resources', 'Transfer Family resources', list_transfer_resources),
        ('wafv2-resources', 'WAF v2 resources', list_wafv2_resources),
        ('lambda-functions', 'Lambda functions', list_lambda_functions),
        ('kms-keys', 'KMS keys', list_kms_keys),
        ('secrets', 'Secrets Manager secrets', list_secrets),
        ('log-groups', 'CloudWatch log groups', cloudwatch.list_log_groups),
        ('cloudwatch-metrics', 'CloudWatch metrics and alarms', cloudwatch.list_cloudwatch_metrics),
        ('cognito-resources', 'Cognito resources', cognito.list_cognito_resources),
        ('eventbridge-resources', 'EventBridge resources', list_eventbridge_resources),
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
from .s3 import create_s3_bucket, s3_inventory  # noqa: E402
