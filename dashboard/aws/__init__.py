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
        ('opensearch-resources', 'OpenSearch resources', opensearch.list_opensearch_resources),
        ('pipes-resources', 'EventBridge Pipes resources', pipes.list_pipes_resources),
        ('pricing-resources', 'AWS Price List resources', pricing.list_pricing_resources),
        ('costexplorer-resources', 'Cost Explorer resources', costexplorer.list_costexplorer_resources),
        ('cur-resources', 'Cost and Usage Reports', cur.list_cur_resources),
        ('bcmdataexports-resources', 'BCM Data Exports', bcmdataexports.list_bcmdataexports_resources),
        ('memorydb-resources', 'MemoryDB resources', memorydb.list_memorydb_resources),
        ('neptune-resources', 'Neptune resources', neptune.list_neptune_resources),
        ('resourcegroupstagging-resources', 'Resource Groups Tagging resources', resourcegroupstagging.list_resourcegroupstagging_resources),
        ('ssm-resources', 'SSM resources', list_ssm_resources),
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
        ('stepfunctions-resources', 'Step Functions resources', list_stepfunctions_resources),
        ('textract-resources', 'Textract resources', list_textract_resources),
        ('transcribe-resources', 'Transcribe resources', list_transcribe_resources),
        ('transfer-resources', 'Transfer Family resources', list_transfer_resources),
        ('wafv2-resources', 'WAF v2 resources', list_wafv2_resources),
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
from .lambda_ import lambda_inventory  # noqa: E402
from .memorydb import memorydb_inventory  # noqa: E402
from .neptune import neptune_inventory  # noqa: E402
from .opensearch import opensearch_inventory  # noqa: E402
from .pipes import pipes_inventory  # noqa: E402
from .pricing import pricing_inventory  # noqa: E402
from .rds import rds_inventory  # noqa: E402
from .rdsdata import rdsdata_inventory  # noqa: E402
from .resourcegroupstagging import resourcegroupstagging_inventory  # noqa: E402
from .route53 import route53_inventory  # noqa: E402
from .s3 import create_s3_bucket, s3_inventory  # noqa: E402
from .s3vectors import s3vectors_inventory  # noqa: E402
from .scheduler import scheduler_inventory  # noqa: E402
from .secretsmanager import secretsmanager_inventory  # noqa: E402
from .ses import ses_inventory  # noqa: E402
from .sns import sns_inventory  # noqa: E402
from .sqs import sqs_inventory  # noqa: E402
