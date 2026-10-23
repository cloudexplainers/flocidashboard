"""CloudFront inventory and resource-list loaders."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def cloudfront_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    cloudfront = factory.client('cloudfront')
    operations = set(cloudfront.meta.service_model.operation_names)

    def nested(operation: str, method: str, container_key: str, **kwargs) -> list[Any]:
        return _safe_value(
            lambda: core._nested_operation_items(cloudfront, method, container_key, **kwargs),
            [],
        ) if operation in operations else []

    distributions = nested('ListDistributions', 'list_distributions', 'DistributionList')

    def distribution_configuration(distribution):
        if 'GetDistributionConfig' not in operations:
            return distribution
        try:
            response = cloudfront.get_distribution_config(Id=distribution['Id'])
            return {**distribution, **response.get('DistributionConfig', {}), 'ETag': response.get('ETag')}
        except (ClientError, BotoCoreError, ValueError) as exc:
            return {**distribution, 'configuration_error': str(exc)}

    if distributions:
        with ThreadPoolExecutor(max_workers=min(8, len(distributions))) as executor:
            distributions = core.map_in_context(executor, distribution_configuration, distributions)

    streaming_distributions = nested(
        'ListStreamingDistributions',
        'list_streaming_distributions',
        'StreamingDistributionList',
    )
    origin_access_identities = nested(
        'ListCloudFrontOriginAccessIdentities',
        'list_cloud_front_origin_access_identities',
        'CloudFrontOriginAccessIdentityList',
    )
    cache_policies = nested('ListCachePolicies', 'list_cache_policies', 'CachePolicyList')
    origin_request_policies = nested(
        'ListOriginRequestPolicies',
        'list_origin_request_policies',
        'OriginRequestPolicyList',
    )
    response_headers_policies = nested(
        'ListResponseHeadersPolicies',
        'list_response_headers_policies',
        'ResponseHeadersPolicyList',
    )
    functions = nested('ListFunctions', 'list_functions', 'FunctionList')
    key_groups = nested('ListKeyGroups', 'list_key_groups', 'KeyGroupList')
    public_keys = nested('ListPublicKeys', 'list_public_keys', 'PublicKeyList')

    return {
        'summary': {
            'distributions': len(distributions),
            'streaming_distributions': len(streaming_distributions),
            'origin_access_identities': len(origin_access_identities),
            'cache_policies': len(cache_policies),
            'origin_request_policies': len(origin_request_policies),
            'response_headers_policies': len(response_headers_policies),
            'functions': len(functions),
            'key_groups': len(key_groups),
            'public_keys': len(public_keys),
            'available_sdk_operations': len(operations),
        },
        'distributions': _clean_response(distributions),
        'streaming_distributions': _clean_response(streaming_distributions),
        'origin_access_identities': _clean_response(origin_access_identities),
        'cache_policies': _clean_response(cache_policies),
        'origin_request_policies': _clean_response(origin_request_policies),
        'response_headers_policies': _clean_response(response_headers_policies),
        'functions': _clean_response(functions),
        'key_groups': _clean_response(key_groups),
        'public_keys': _clean_response(public_keys),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateDistribution',
                'GetDistribution',
                'ListDistributions',
                'UpdateDistribution',
                'DeleteDistribution',
                'CreateInvalidation',
                'ListInvalidations',
                'ListCachePolicies',
                'ListOriginRequestPolicies',
                'ListResponseHeadersPolicies',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Signed URLs and signed cookies are enforced for cache behaviors with trusted key groups.',
        ],
    }


def list_cloudfront_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    cloudfront = factory.client('cloudfront')
    operations = set(cloudfront.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListDistributions' in operations:
        resources.extend(
            {
                'type': 'distribution',
                'id': distribution.get('Id'),
                'arn': distribution.get('ARN'),
                'domain_name': distribution.get('DomainName'),
                'status': distribution.get('Status'),
                'enabled': distribution.get('Enabled'),
            }
            for distribution in _safe_value(
                lambda: core._nested_operation_items(cloudfront, 'list_distributions', 'DistributionList'),
                [],
            )
        )

    if 'ListCachePolicies' in operations:
        resources.extend(
            {
                'type': 'cache_policy',
                'id': (policy.get('CachePolicy') or {}).get('Id'),
                'name': ((policy.get('CachePolicy') or {}).get('CachePolicyConfig') or {}).get('Name'),
                'policy_type': policy.get('Type'),
            }
            for policy in _safe_value(
                lambda: core._nested_operation_items(cloudfront, 'list_cache_policies', 'CachePolicyList'),
                [],
            )
        )

    if 'ListOriginRequestPolicies' in operations:
        resources.extend(
            {
                'type': 'origin_request_policy',
                'id': (policy.get('OriginRequestPolicy') or {}).get('Id'),
                'name': ((policy.get('OriginRequestPolicy') or {}).get('OriginRequestPolicyConfig') or {}).get('Name'),
                'policy_type': policy.get('Type'),
            }
            for policy in _safe_value(
                lambda: core._nested_operation_items(cloudfront, 'list_origin_request_policies', 'OriginRequestPolicyList'),
                [],
            )
        )

    return resources
