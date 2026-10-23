"""Interactive Cloud Map helpers for local service discovery workflows."""

from __future__ import annotations

import uuid
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('servicediscovery')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _dict(value: Any, label: str, *, required: bool = False) -> dict[str, Any]:
    if value in (None, ''):
        if required:
            raise ValueError(f'{label} is required')
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    return value


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
        key = item.get('Key') or item.get('key')
        if not key:
            raise ValueError('Each tag needs a Key')
        item_value = item.get('Value')
        if item_value is None:
            item_value = item.get('value')
        tags.append({'Key': str(key), 'Value': '' if item_value is None else str(item_value)})
    return tags


def _tag_keys(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if not isinstance(value, list) or not value:
        raise ValueError('Tag keys must be a non-empty array')
    return [_required(item, 'Tag key') for item in value]


def create_namespace(
    *,
    name: str,
    namespace_type: str = 'HTTP',
    description: str = '',
    vpc: str = '',
    tags: Any = None,
) -> dict[str, Any]:
    clean_type = _required(namespace_type or 'HTTP', 'Namespace type').upper().replace('-', '_')
    kwargs: dict[str, Any] = {
        'Name': _required(name, 'Namespace name'),
        'CreatorRequestId': str(uuid.uuid4()),
    }
    if description:
        kwargs['Description'] = description
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['Tags'] = clean_tags

    client = _client()
    if clean_type in {'HTTP', 'HTTP_NAMESPACE'}:
        response = client.create_http_namespace(**kwargs)
    elif clean_type in {'PUBLIC_DNS', 'PUBLIC_DNS_NAMESPACE'}:
        response = client.create_public_dns_namespace(**kwargs)
    elif clean_type in {'PRIVATE_DNS', 'PRIVATE_DNS_NAMESPACE'}:
        kwargs['Vpc'] = _required(vpc, 'VPC ID')
        response = client.create_private_dns_namespace(**kwargs)
    else:
        raise ValueError('Namespace type must be HTTP, PUBLIC_DNS, or PRIVATE_DNS')

    operation_id = response.get('OperationId')
    return {
        'namespace': kwargs['Name'],
        'namespace_type': clean_type,
        'operation_id': operation_id,
        'response': _clean_response(response),
    }


def delete_namespace(namespace_id: str) -> dict[str, Any]:
    clean_id = _required(namespace_id, 'Namespace ID')
    response = _client().delete_namespace(Id=clean_id)
    return {'namespace_id': clean_id, 'operation_id': response.get('OperationId'), 'response': _clean_response(response)}


def create_service(
    *,
    name: str,
    namespace_id: str,
    description: str = '',
    dns_config: Any = None,
    health_check_config: Any = None,
    health_check_custom_config: Any = None,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'Name': _required(name, 'Service name'),
        'NamespaceId': _required(namespace_id, 'Namespace ID'),
        'CreatorRequestId': str(uuid.uuid4()),
    }
    if description:
        kwargs['Description'] = description
    for key, value, label in [
        ('DnsConfig', dns_config, 'DNS config'),
        ('HealthCheckConfig', health_check_config, 'Health check config'),
        ('HealthCheckCustomConfig', health_check_custom_config, 'Custom health check config'),
    ]:
        clean_value = _dict(value, label)
        if clean_value:
            kwargs[key] = clean_value
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['Tags'] = clean_tags

    response = _client().create_service(**kwargs)
    service = response.get('Service', {})
    return {'service_id': service.get('Id'), 'service': _clean_response(service), 'response': _clean_response(response)}


def delete_service(service_id: str) -> dict[str, Any]:
    clean_id = _required(service_id, 'Service ID')
    response = _client().delete_service(Id=clean_id)
    return {'service_id': clean_id, 'response': _clean_response(response)}


def register_instance(service_id: str, instance_id: str, attributes: Any) -> dict[str, Any]:
    clean_service = _required(service_id, 'Service ID')
    clean_instance = _required(instance_id, 'Instance ID')
    clean_attributes = {str(key): str(value) for key, value in _dict(attributes, 'Attributes', required=True).items()}
    if not clean_attributes:
        raise ValueError('Attributes are required')
    response = _client().register_instance(
        ServiceId=clean_service,
        InstanceId=clean_instance,
        Attributes=clean_attributes,
        CreatorRequestId=str(uuid.uuid4()),
    )
    return {
        'service_id': clean_service,
        'instance_id': clean_instance,
        'operation_id': response.get('OperationId'),
        'response': _clean_response(response),
    }


def deregister_instance(service_id: str, instance_id: str) -> dict[str, Any]:
    clean_service = _required(service_id, 'Service ID')
    clean_instance = _required(instance_id, 'Instance ID')
    response = _client().deregister_instance(ServiceId=clean_service, InstanceId=clean_instance)
    return {
        'service_id': clean_service,
        'instance_id': clean_instance,
        'operation_id': response.get('OperationId'),
        'response': _clean_response(response),
    }


def discover_instances(namespace_name: str, service_name: str, *, query_parameters: Any = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'NamespaceName': _required(namespace_name, 'Namespace name'),
        'ServiceName': _required(service_name, 'Service name'),
    }
    clean_query = _dict(query_parameters, 'Query parameters')
    if clean_query:
        kwargs['QueryParameters'] = {str(key): str(value) for key, value in clean_query.items()}
    response = _client().discover_instances(**kwargs)
    return {'instances': _clean_response(response.get('Instances', [])), 'response': _clean_response(response)}


def tag_resource(resource_arn: str, tags: Any) -> dict[str, Any]:
    clean_arn = _required(resource_arn, 'Resource ARN')
    clean_tags = _tags(tags)
    if not clean_tags:
        raise ValueError('At least one tag is required')
    response = _client().tag_resource(ResourceARN=clean_arn, Tags=clean_tags)
    return {'resource_arn': clean_arn, 'tags': clean_tags, 'response': _clean_response(response)}


def untag_resource(resource_arn: str, tag_keys: Any) -> dict[str, Any]:
    clean_arn = _required(resource_arn, 'Resource ARN')
    clean_keys = _tag_keys(tag_keys)
    response = _client().untag_resource(ResourceARN=clean_arn, TagKeys=clean_keys)
    return {'resource_arn': clean_arn, 'tag_keys': clean_keys, 'response': _clean_response(response)}
