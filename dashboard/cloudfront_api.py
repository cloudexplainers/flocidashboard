"""Interactive CloudFront helpers for local distribution workflows."""

from __future__ import annotations

import base64
import json
import uuid
from typing import Any

from .aws import FlociClientFactory, _clean_response, _nested_operation_items


def _client():
    return FlociClientFactory().client('cloudfront')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _string_list(value: Any) -> list[str]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError('Value must be a comma-separated string or list')


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object')
    return value


def _tags(value: Any) -> dict[str, list[dict[str, str]]]:
    if value in (None, '', []):
        return {'Items': []}
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Tags must be an object or list')
    items = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = item.get('Key') or item.get('key')
        if not key:
            raise ValueError('Each tag needs a Key')
        tag_value = item.get('Value')
        if tag_value is None:
            tag_value = item.get('value')
        items.append({'Key': str(key), 'Value': '' if tag_value is None else str(tag_value)})
    return {'Items': items}


def _distribution_config(
    caller_reference: str,
    origin_id: str,
    origin_domain_name: str,
    *,
    comment: str = '',
    enabled: bool = True,
    aliases: Any = None,
    viewer_protocol_policy: str = 'redirect-to-https',
    cache_policy_id: str = '',
    origin_type: str = 's3',
    origin_protocol_policy: str = 'https-only',
    http_port: int = 80,
    https_port: int = 443,
    origin_access_control_id: str = '',
) -> dict[str, Any]:
    clean_origin_id = _required(origin_id, 'Origin ID')
    config: dict[str, Any] = {
        'CallerReference': _required(caller_reference, 'Caller reference'),
        'Comment': comment or '',
        'Enabled': bool(enabled),
        'Origins': {
            'Quantity': 1,
            'Items': [{
                'Id': clean_origin_id,
                'DomainName': _required(origin_domain_name, 'Origin domain name'),
                'S3OriginConfig': {'OriginAccessIdentity': ''},
            }],
        },
        'DefaultCacheBehavior': {
            'TargetOriginId': clean_origin_id,
            'ViewerProtocolPolicy': viewer_protocol_policy or 'redirect-to-https',
            'AllowedMethods': {'Quantity': 2, 'Items': ['GET', 'HEAD'], 'CachedMethods': {'Quantity': 2, 'Items': ['GET', 'HEAD']}},
            'Compress': True,
            'TrustedSigners': {'Enabled': False, 'Quantity': 0},
            'ForwardedValues': {
                'QueryString': False,
                'Cookies': {'Forward': 'none'},
            },
            'MinTTL': 0,
        },
    }
    origin = config['Origins']['Items'][0]
    if any(character in origin['DomainName'] for character in '/:'):
        raise ValueError('Origin domain must be a hostname without a scheme, port, or path')
    if origin_type == 'custom':
        if origin_protocol_policy not in {'http-only', 'https-only', 'match-viewer'}:
            raise ValueError('Invalid origin protocol policy')
        try:
            ports = [int(http_port), int(https_port)]
        except (TypeError, ValueError) as exc:
            raise ValueError('Origin ports must be integers') from exc
        if any(port < 1 or port > 65535 for port in ports):
            raise ValueError('Origin ports must be between 1 and 65535')
        origin.pop('S3OriginConfig')
        origin['CustomOriginConfig'] = {'HTTPPort': ports[0], 'HTTPSPort': ports[1], 'OriginProtocolPolicy': origin_protocol_policy}
    elif origin_type != 's3':
        raise ValueError('Origin type must be s3 or custom')
    if origin_access_control_id:
        if origin_type == 'custom':
            raise ValueError('Origin access control applies to S3 origins only')
        origin['OriginAccessControlId'] = str(origin_access_control_id).strip()
    if cache_policy_id:
        behavior = config['DefaultCacheBehavior']
        behavior['CachePolicyId'] = cache_policy_id
        behavior.pop('ForwardedValues', None)
    alias_items = _string_list(aliases)
    if alias_items:
        config['Aliases'] = {'Quantity': len(alias_items), 'Items': alias_items}
    else:
        config['Aliases'] = {'Quantity': 0}
    return config


def create_distribution(
    caller_reference: str,
    origin_id: str,
    origin_domain_name: str,
    *,
    comment: str = '',
    enabled: bool = True,
    aliases: Any = None,
    viewer_protocol_policy: str = 'redirect-to-https',
    cache_policy_id: str = '',
    origin_type: str = 's3',
    origin_protocol_policy: str = 'https-only',
    http_port: int = 80,
    https_port: int = 443,
    origin_access_control_id: str = '',
) -> dict[str, Any]:
    response = _client().create_distribution(DistributionConfig=_distribution_config(
        caller_reference,
        origin_id,
        origin_domain_name,
        comment=comment,
        enabled=enabled,
        aliases=aliases,
        viewer_protocol_policy=viewer_protocol_policy,
        cache_policy_id=cache_policy_id,
        origin_type=origin_type,
        origin_protocol_policy=origin_protocol_policy,
        http_port=http_port,
        https_port=https_port,
        origin_access_control_id=origin_access_control_id,
    ))
    return {'distribution': _clean_response(response.get('Distribution', {})), 'etag': response.get('ETag'), 'response': _clean_response(response)}


def update_distribution(distribution_id: str, config: Any = None, *, if_match: str = '', comment: str = '', enabled: Any = None) -> dict[str, Any]:
    cloudfront = _client()
    clean_id = _required(distribution_id, 'Distribution ID')
    if config:
        distribution_config = _json_object(config, 'Distribution config')
        etag = _required(if_match, 'If-Match ETag')
    else:
        current = cloudfront.get_distribution_config(Id=clean_id)
        distribution_config = current.get('DistributionConfig', {})
        etag = if_match or current.get('ETag')
        if comment != '':
            distribution_config['Comment'] = comment
        if enabled is not None:
            distribution_config['Enabled'] = bool(enabled)
    response = cloudfront.update_distribution(Id=clean_id, IfMatch=_required(etag, 'If-Match ETag'), DistributionConfig=distribution_config)
    return {'distribution': _clean_response(response.get('Distribution', {})), 'etag': response.get('ETag'), 'response': _clean_response(response)}


def delete_distribution(distribution_id: str, *, if_match: str = '') -> dict[str, Any]:
    cloudfront = _client()
    clean_id = _required(distribution_id, 'Distribution ID')
    etag = if_match
    if not etag:
        etag = cloudfront.get_distribution_config(Id=clean_id).get('ETag')
    response = cloudfront.delete_distribution(Id=clean_id, IfMatch=_required(etag, 'If-Match ETag'))
    return {'distribution_id': clean_id, 'response': _clean_response(response)}


def create_invalidation(distribution_id: str, paths: Any, *, caller_reference: str = '') -> dict[str, Any]:
    clean_paths = _string_list(paths)
    if not clean_paths:
        raise ValueError('At least one invalidation path is required')
    if any(not path.startswith('/') for path in clean_paths):
        raise ValueError('Invalidation paths must start with /')
    reference = caller_reference or f'dashboard-{uuid.uuid4()}'
    response = _client().create_invalidation(
        DistributionId=_required(distribution_id, 'Distribution ID'),
        InvalidationBatch={
            'CallerReference': reference,
            'Paths': {'Quantity': len(clean_paths), 'Items': clean_paths},
        },
    )
    return {'invalidation': _clean_response(response.get('Invalidation', {})), 'response': _clean_response(response)}


def list_invalidations(distribution_id: str) -> dict[str, Any]:
    identifier = _required(distribution_id, 'Distribution ID')
    items = _nested_operation_items(_client(), 'list_invalidations', 'InvalidationList', DistributionId=identifier)
    return {'distribution_id': identifier, 'invalidations': _clean_response(items)}


def get_invalidation(distribution_id: str, invalidation_id: str) -> dict[str, Any]:
    response = _client().get_invalidation(DistributionId=_required(distribution_id, 'Distribution ID'), Id=_required(invalidation_id, 'Invalidation ID'))
    return {'invalidation': _clean_response(response.get('Invalidation', {}))}


def create_cache_policy(name: str, *, default_ttl: Any = 86400, max_ttl: Any = 31536000, min_ttl: Any = 0, comment: str = '') -> dict[str, Any]:
    try:
        values = [int(min_ttl), int(default_ttl), int(max_ttl)]
        if any(isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()) for value in (min_ttl, default_ttl, max_ttl)):
            raise ValueError()
    except (TypeError, ValueError) as exc:
        raise ValueError('Cache TTLs must be whole numbers') from exc
    if not 0 <= values[0] <= values[1] <= values[2]:
        raise ValueError('Cache TTLs must satisfy 0 <= minimum <= default <= maximum')
    response = _client().create_cache_policy(CachePolicyConfig={
        'Name': _required(name, 'Cache policy name'),
        'Comment': comment or '',
        'DefaultTTL': values[1],
        'MaxTTL': values[2],
        'MinTTL': values[0],
        'ParametersInCacheKeyAndForwardedToOrigin': {
            'EnableAcceptEncodingGzip': values[2] > 0,
            'EnableAcceptEncodingBrotli': False,
            'HeadersConfig': {'HeaderBehavior': 'none'},
            'CookiesConfig': {'CookieBehavior': 'none'},
            'QueryStringsConfig': {'QueryStringBehavior': 'none'},
        },
    })
    return {'cache_policy': _clean_response(response.get('CachePolicy', {})), 'etag': response.get('ETag'), 'response': _clean_response(response)}


def create_origin_access_identity(caller_reference: str, *, comment: str = '') -> dict[str, Any]:
    response = _client().create_cloud_front_origin_access_identity(
        CloudFrontOriginAccessIdentityConfig={
            'CallerReference': _required(caller_reference, 'Caller reference'),
            'Comment': comment or '',
        },
    )
    return {'origin_access_identity': _clean_response(response.get('CloudFrontOriginAccessIdentity', {})), 'etag': response.get('ETag'), 'response': _clean_response(response)}


OAC_ORIGIN_TYPES = {'s3', 'mediastore', 'lambda', 'mediapackagev2'}
OAC_SIGNING_BEHAVIORS = {'always', 'never', 'no-override'}


def list_origin_access_controls() -> dict[str, Any]:
    client = _client()
    controls: list[dict[str, Any]] = []
    marker = ''
    while True:
        kwargs = {'Marker': marker} if marker else {}
        page = client.list_origin_access_controls(**kwargs).get('OriginAccessControlList', {})
        controls.extend(_clean_response(item) for item in page.get('Items', []) or [])
        marker = page.get('NextMarker') or ''
        if not page.get('IsTruncated') or not marker:
            break
    used_by: dict[str, list[str]] = {}
    distributions = client.list_distributions().get('DistributionList', {}).get('Items', []) or []
    for distribution in distributions:
        for origin in distribution.get('Origins', {}).get('Items', []) or []:
            control_id = origin.get('OriginAccessControlId')
            if control_id:
                used_by.setdefault(control_id, []).append(distribution.get('Id', ''))
    for control in controls:
        control['UsedBy'] = used_by.get(control.get('Id', ''), [])
    return {'origin_access_controls': controls}


def create_origin_access_control(
    name: str,
    *,
    description: str = '',
    origin_type: str = 's3',
    signing_behavior: str = 'always',
) -> dict[str, Any]:
    clean_name = _required(name, 'Name')
    if len(clean_name) > 64:
        raise ValueError('Name must be 64 characters or fewer')
    if origin_type not in OAC_ORIGIN_TYPES:
        raise ValueError('Origin type must be s3, mediastore, lambda or mediapackagev2')
    if signing_behavior not in OAC_SIGNING_BEHAVIORS:
        raise ValueError('Signing behavior must be always, never or no-override')
    response = _client().create_origin_access_control(OriginAccessControlConfig={
        'Name': clean_name,
        'Description': description or '',
        'SigningProtocol': 'sigv4',
        'SigningBehavior': signing_behavior,
        'OriginAccessControlOriginType': origin_type,
    })
    return {'origin_access_control': _clean_response(response.get('OriginAccessControl', {})), 'etag': response.get('ETag')}


def delete_origin_access_control(control_id: str, *, if_match: str = '') -> dict[str, Any]:
    client = _client()
    clean_id = _required(control_id, 'Origin access control ID')
    etag = if_match or client.get_origin_access_control(Id=clean_id).get('ETag', '')
    client.delete_origin_access_control(Id=clean_id, IfMatch=etag)
    return {'deleted': clean_id}


def create_function(name: str, code: str, *, comment: str = '', runtime: str = 'cloudfront-js-1.0') -> dict[str, Any]:
    raw_code = _required(code, 'Function code')
    if raw_code.startswith('base64:'):
        function_code = base64.b64decode(raw_code.removeprefix('base64:'))
    else:
        function_code = raw_code.encode('utf-8')
    response = _client().create_function(
        Name=_required(name, 'Function name'),
        FunctionConfig={'Comment': comment or '', 'Runtime': runtime or 'cloudfront-js-1.0'},
        FunctionCode=function_code,
    )
    return {'function': _clean_response(response.get('FunctionSummary', {})), 'etag': response.get('ETag'), 'response': _clean_response(response)}


def publish_function(name: str, *, if_match: str = '') -> dict[str, Any]:
    cloudfront = _client()
    clean_name = _required(name, 'Function name')
    etag = if_match or cloudfront.describe_function(Name=clean_name).get('ETag')
    response = cloudfront.publish_function(Name=clean_name, IfMatch=_required(etag, 'If-Match ETag'))
    return {'function': _clean_response(response.get('FunctionSummary', {})), 'etag': response.get('ETag'), 'response': _clean_response(response)}


def delete_function(name: str, *, if_match: str = '') -> dict[str, Any]:
    cloudfront = _client()
    clean_name = _required(name, 'Function name')
    etag = if_match or cloudfront.describe_function(Name=clean_name).get('ETag')
    response = cloudfront.delete_function(Name=clean_name, IfMatch=_required(etag, 'If-Match ETag'))
    return {'function_name': clean_name, 'response': _clean_response(response)}


def tag_resource(resource_arn: str, tags: Any) -> dict[str, Any]:
    parsed_tags = _tags(tags)
    if not parsed_tags['Items']:
        raise ValueError('At least one tag is required')
    response = _client().tag_resource(Resource=_required(resource_arn, 'Resource ARN'), Tags=parsed_tags)
    return {'resource_arn': resource_arn, 'tags': parsed_tags['Items'], 'response': _clean_response(response)}


def untag_resource(resource_arn: str, tag_keys: Any) -> dict[str, Any]:
    keys = _string_list(tag_keys)
    if not keys:
        raise ValueError('At least one tag key is required')
    response = _client().untag_resource(Resource=_required(resource_arn, 'Resource ARN'), TagKeys={'Items': keys})
    return {'resource_arn': resource_arn, 'tag_keys': keys, 'response': _clean_response(response)}
