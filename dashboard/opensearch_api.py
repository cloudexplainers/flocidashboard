"""Interactive OpenSearch helpers for local search domain workflows."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('opensearch')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {'1', 'true', 'yes', 'on'}
    return bool(value)


def _int_value(value: Any, label: str, default: int | None = None) -> int | None:
    if value in (None, ''):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a number') from exc
    if number < 0:
        raise ValueError(f'{label} must be zero or greater')
    return number


def _dict(value: Any, label: str) -> dict[str, Any]:
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    return value


def _list(value: Any, label: str = 'Value') -> list[Any]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return value
    raise ValueError(f'{label} must be a list')


def _tags(value: Any) -> list[dict[str, str]]:
    if value in (None, '', [], {}):
        return []
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Tags must be an object or list')

    tags = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = _required(item.get('Key') or item.get('key'), 'Tag key')
        tag_value = item.get('Value')
        if tag_value is None:
            tag_value = item.get('value')
        tags.append({'Key': key, 'Value': '' if tag_value is None else str(tag_value)})
    return tags


def create_domain(
    *,
    domain_name: str,
    engine_version: str = 'OpenSearch_2.19',
    instance_type: str = 'm5.large.search',
    instance_count: Any = 1,
    ebs_enabled: Any = True,
    volume_type: str = 'gp3',
    volume_size: Any = 10,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'DomainName': _required(domain_name, 'Domain name'),
        'EngineVersion': engine_version or 'OpenSearch_2.19',
        'ClusterConfig': {
            'InstanceType': instance_type or 'm5.large.search',
            'InstanceCount': _int_value(instance_count, 'Instance count', 1),
        },
        'EBSOptions': {
            'EBSEnabled': _bool(ebs_enabled),
            'VolumeType': volume_type or 'gp3',
            'VolumeSize': _int_value(volume_size, 'Volume size', 10),
        },
    }
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['TagList'] = clean_tags

    response = _client().create_domain(**kwargs)
    status = response.get('DomainStatus', {})
    return {
        'domain_name': status.get('DomainName') or kwargs['DomainName'],
        'arn': status.get('ARN') or status.get('DomainArn'),
        'engine_version': status.get('EngineVersion'),
        'created': status.get('Created'),
        'processing': status.get('Processing'),
        'endpoint': status.get('Endpoint'),
        'response': _clean_response(response),
    }


def update_domain_config(
    *,
    domain_name: str,
    engine_version: str = '',
    cluster_config: Any = None,
    ebs_options: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {'DomainName': _required(domain_name, 'Domain name')}
    if engine_version:
        raise ValueError('UpdateDomainConfig cannot change the engine version; use Upgrade domain (UpgradeDomain).')
    cluster = _dict(cluster_config, 'Cluster config')
    if cluster:
        kwargs['ClusterConfig'] = cluster
    ebs = _dict(ebs_options, 'EBS options')
    if ebs:
        kwargs['EBSOptions'] = ebs
    if len(kwargs) == 1:
        raise ValueError('At least one domain config change is required')

    response = _client().update_domain_config(**kwargs)
    return {
        'domain_name': kwargs['DomainName'],
        'response': _clean_response(response),
    }


def delete_domain(domain_name: str) -> dict[str, Any]:
    clean_name = _required(domain_name, 'Domain name')
    response = _client().delete_domain(DomainName=clean_name)
    status = response.get('DomainStatus', {})
    return {
        'domain_name': status.get('DomainName') or clean_name,
        'deleted': status.get('Deleted'),
        'processing': status.get('Processing'),
        'response': _clean_response(response),
    }


def upgrade_domain(domain_name: str, target_version: str, *, perform_check_only: Any = False) -> dict[str, Any]:
    response = _client().upgrade_domain(
        DomainName=_required(domain_name, 'Domain name'),
        TargetVersion=_required(target_version, 'Target version'),
        PerformCheckOnly=_bool(perform_check_only),
    )
    return {
        'domain_name': domain_name,
        'target_version': target_version,
        'perform_check_only': _bool(perform_check_only),
        'response': _clean_response(response),
    }


def add_tags(arn: str, tags: Any) -> dict[str, Any]:
    clean_arn = _required(arn, 'ARN')
    clean_tags = _tags(tags)
    if not clean_tags:
        raise ValueError('At least one tag is required')
    response = _client().add_tags(ARN=clean_arn, TagList=clean_tags)
    return {
        'arn': clean_arn,
        'tags': clean_tags,
        'response': _clean_response(response),
    }


def remove_tags(arn: str, tag_keys: Any) -> dict[str, Any]:
    clean_arn = _required(arn, 'ARN')
    clean_keys = [str(item).strip() for item in _list(tag_keys, 'Tag keys') if str(item).strip()]
    if not clean_keys:
        raise ValueError('At least one tag key is required')
    response = _client().remove_tags(ARN=clean_arn, TagKeys=clean_keys)
    return {
        'arn': clean_arn,
        'tag_keys': clean_keys,
        'response': _clean_response(response),
    }


def list_tags(arn: str) -> dict[str, Any]:
    clean_arn = _required(arn, 'ARN')
    response = _client().list_tags(ARN=clean_arn)
    return {
        'arn': clean_arn,
        'tags': response.get('TagList', []),
        'response': _clean_response(response),
    }


def list_versions() -> dict[str, Any]:
    response = _client().list_versions()
    return {
        'versions': response.get('Versions', []),
        'response': _clean_response(response),
    }


def get_compatible_versions(domain_name: str = '') -> dict[str, Any]:
    kwargs = {'DomainName': domain_name} if domain_name else {}
    response = _client().get_compatible_versions(**kwargs)
    return {
        'domain_name': domain_name,
        'compatible_versions': response.get('CompatibleVersions', []),
        'response': _clean_response(response),
    }


def list_instance_type_details(engine_version: str) -> dict[str, Any]:
    response = _client().list_instance_type_details(EngineVersion=_required(engine_version, 'Engine version'))
    return {
        'engine_version': engine_version,
        'instance_type_details': response.get('InstanceTypeDetails', []),
        'response': _clean_response(response),
    }


def describe_instance_type_limits(engine_version: str, instance_type: str) -> dict[str, Any]:
    response = _client().describe_instance_type_limits(
        EngineVersion=_required(engine_version, 'Engine version'),
        InstanceType=_required(instance_type, 'Instance type'),
    )
    return {
        'engine_version': engine_version,
        'instance_type': instance_type,
        'limits_by_role': response.get('LimitsByRole', {}),
        'response': _clean_response(response),
    }


def execute_domain_request(
    domain_name: str,
    *,
    method: str = 'GET',
    path: str = '/_cluster/health',
    body: Any = None,
    headers: dict[str, str] | None = None,
    endpoint_url: str | None = None,
) -> dict[str, Any]:
    import json
    import time
    import urllib.error
    import urllib.parse
    import urllib.request
    clean_domain = _required(domain_name, 'Domain name')
    clean_method = (method or 'GET').strip().upper()
    clean_path = (path or '/_cluster/health').strip()
    if not clean_path.startswith('/'):
        clean_path = f'/{clean_path}'

    target_url = endpoint_url
    if not target_url:
        floci_endpoint = FlociClientFactory().endpoint_url.rstrip('/')
        target_url = f'{floci_endpoint}/opensearch/{clean_domain}{clean_path}'

    req_headers = {
        'User-Agent': 'Floci-Dashboard-OpenSearch-DevTools/1.0',
    }
    if headers and isinstance(headers, dict):
        for k, v in headers.items():
            req_headers[str(k)] = str(v)

    req_data = None
    if body is not None and clean_method not in {'GET', 'HEAD'}:
        if isinstance(body, str):
            req_data = body.encode('utf-8')
        elif isinstance(body, (dict, list)):
            req_data = json.dumps(body).encode('utf-8')
            if 'Content-Type' not in req_headers:
                req_headers['Content-Type'] = 'application/json'

    request = urllib.request.Request(target_url, data=req_data, headers=req_headers, method=clean_method)
    start_time = time.perf_counter()

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            resp_body_str = response.read().decode('utf-8', errors='replace')
            try:
                parsed_json = json.loads(resp_body_str)
            except Exception:
                parsed_json = None
            return {
                'domain_name': clean_domain,
                'method': clean_method,
                'path': clean_path,
                'status_code': response.status,
                'latency_ms': latency_ms,
                'headers': dict(response.headers.items()),
                'body': resp_body_str,
                'json': parsed_json,
            }
    except urllib.error.HTTPError as err:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        err_body_str = err.read().decode('utf-8', errors='replace')
        try:
            parsed_json = json.loads(err_body_str)
        except Exception:
            parsed_json = None
        return {
            'domain_name': clean_domain,
            'method': clean_method,
            'path': clean_path,
            'status_code': err.code,
            'latency_ms': latency_ms,
            'headers': dict(err.headers.items()) if err.headers else {},
            'body': err_body_str,
            'json': parsed_json,
            'error': str(err),
        }
    except Exception as err:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            'domain_name': clean_domain,
            'method': clean_method,
            'path': clean_path,
            'status_code': 500,
            'latency_ms': latency_ms,
            'error': str(err),
        }

