"""Cloud Map inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _cloudmap_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def cloudmap_inventory() -> dict[str, Any]:
    discovery = core.FlociClientFactory().client('servicediscovery')
    operations = set(discovery.meta.service_model.operation_names)

    namespaces = _safe_value(
        lambda: core._operation_items(discovery, 'list_namespaces', 'Namespaces'),
        [],
    ) if 'ListNamespaces' in operations else []
    services = _safe_value(
        lambda: core._operation_items(discovery, 'list_services', 'Services'),
        [],
    ) if 'ListServices' in operations else []
    operation_summaries = _safe_value(
        lambda: core._operation_items(discovery, 'list_operations', 'Operations'),
        [],
    ) if 'ListOperations' in operations else []

    def namespace_detail(namespace: dict[str, Any]) -> dict[str, Any]:
        namespace_id = namespace.get('Id')
        details = _cloudmap_optional(
            lambda: discovery.get_namespace(Id=namespace_id).get('Namespace', {}),
            {'NamespaceNotFound'},
        ) if namespace_id and 'GetNamespace' in operations else None

        described = details if isinstance(details, dict) and not details.get('error') else namespace
        arn = described.get('Arn') or namespace.get('Arn')
        return {
            'name': described.get('Name') or namespace.get('Name') or namespace_id,
            'id': namespace_id,
            'arn': arn,
            'type': described.get('Type') or namespace.get('Type'),
            'description': described.get('Description') or namespace.get('Description'),
            'service_count': described.get('ServiceCount') or namespace.get('ServiceCount'),
            'properties': described.get('Properties') or namespace.get('Properties'),
            'create_date': described.get('CreateDate') or namespace.get('CreateDate'),
            'tags': _cloudmap_optional(
                lambda: discovery.list_tags_for_resource(ResourceARN=arn).get('Tags', []),
                {'ResourceNotFoundException', 'NamespaceNotFound'},
            ) if arn and 'ListTagsForResource' in operations else None,
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def instance_detail(service_id: str, instance: dict[str, Any]) -> dict[str, Any]:
        instance_id = instance.get('Id')
        details = _cloudmap_optional(
            lambda: discovery.get_instance(ServiceId=service_id, InstanceId=instance_id).get('Instance', {}),
            {'InstanceNotFound', 'ServiceNotFound'},
        ) if instance_id and 'GetInstance' in operations else None
        described = details if isinstance(details, dict) and not details.get('error') else instance
        return {
            'name': described.get('Id') or instance_id,
            'id': described.get('Id') or instance_id,
            'service_id': service_id,
            'attributes': described.get('Attributes') or instance.get('Attributes'),
            'creator_request_id': described.get('CreatorRequestId') or instance.get('CreatorRequestId'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def service_detail(service: dict[str, Any]) -> dict[str, Any]:
        service_id = service.get('Id')
        details = _cloudmap_optional(
            lambda: discovery.get_service(Id=service_id).get('Service', {}),
            {'ServiceNotFound'},
        ) if service_id and 'GetService' in operations else None
        described = details if isinstance(details, dict) and not details.get('error') else service
        instances = _cloudmap_optional(
            lambda: core._operation_items(discovery, 'list_instances', 'Instances', ServiceId=service_id),
            {'ServiceNotFound'},
        ) if service_id and 'ListInstances' in operations else []
        if not isinstance(instances, list):
            instances = []
        arn = described.get('Arn') or service.get('Arn')

        return {
            'name': described.get('Name') or service.get('Name') or service_id,
            'id': service_id,
            'arn': arn,
            'namespace_id': described.get('NamespaceId') or service.get('NamespaceId'),
            'description': described.get('Description') or service.get('Description'),
            'dns_config': described.get('DnsConfig') or service.get('DnsConfig'),
            'health_check_config': described.get('HealthCheckConfig') or service.get('HealthCheckConfig'),
            'health_check_custom_config': described.get('HealthCheckCustomConfig') or service.get('HealthCheckCustomConfig'),
            'create_date': described.get('CreateDate') or service.get('CreateDate'),
            'instance_count': len(instances),
            'instances': [instance_detail(service_id, instance) for instance in instances],
            'tags': _cloudmap_optional(
                lambda: discovery.list_tags_for_resource(ResourceARN=arn).get('Tags', []),
                {'ResourceNotFoundException', 'ServiceNotFound'},
            ) if arn and 'ListTagsForResource' in operations else None,
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def operation_detail(operation: dict[str, Any]) -> dict[str, Any]:
        operation_id = operation.get('Id')
        details = _cloudmap_optional(
            lambda: discovery.get_operation(OperationId=operation_id).get('Operation', {}),
            {'OperationNotFound'},
        ) if operation_id and 'GetOperation' in operations else None
        described = details if isinstance(details, dict) and not details.get('error') else operation
        return {
            'name': operation_id,
            'id': operation_id,
            'status': described.get('Status') or operation.get('Status'),
            'type': described.get('Type') or operation.get('Type'),
            'targets': described.get('Targets') or operation.get('Targets'),
            'create_date': described.get('CreateDate') or operation.get('CreateDate'),
            'update_date': described.get('UpdateDate') or operation.get('UpdateDate'),
            'error_code': described.get('ErrorCode') or operation.get('ErrorCode'),
            'error_message': described.get('ErrorMessage') or operation.get('ErrorMessage'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_namespaces = [namespace_detail(namespace) for namespace in namespaces]
    detailed_services = [service_detail(service) for service in services]
    instances = [instance for service in detailed_services for instance in service.get('instances', [])]
    detailed_operations = [operation_detail(operation) for operation in operation_summaries]

    return {
        'summary': {
            'namespaces': len(detailed_namespaces),
            'services': len(detailed_services),
            'instances': len(instances),
            'operations': len(detailed_operations),
            'available_sdk_operations': len(operations),
        },
        'namespaces': detailed_namespaces,
        'services': detailed_services,
        'instances': instances,
        'operations': detailed_operations,
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateHttpNamespace',
                'CreatePublicDnsNamespace',
                'CreatePrivateDnsNamespace',
                'DeleteNamespace',
                'GetNamespace',
                'ListNamespaces',
                'CreateService',
                'DeleteService',
                'GetService',
                'ListServices',
                'RegisterInstance',
                'DeregisterInstance',
                'GetInstance',
                'ListInstances',
                'DiscoverInstances',
                'DiscoverInstancesRevision',
                'GetOperation',
                'ListOperations',
                'UpdateInstanceCustomHealthStatus',
                'TagResource',
                'UntagResource',
                'ListTagsForResource',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci main after 1.5.23 adds AWS Cloud Map through the boto3 servicediscovery client.',
            'Namespaces, services, registered instances, async operations, discovery calls, health status, and tags are represented through the management API.',
        ],
    }


def list_cloudmap_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    discovery = factory.client('servicediscovery')
    operations = set(discovery.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    namespaces = _safe_value(
        lambda: core._operation_items(discovery, 'list_namespaces', 'Namespaces'),
        [],
    ) if 'ListNamespaces' in operations else []
    services = _safe_value(
        lambda: core._operation_items(discovery, 'list_services', 'Services'),
        [],
    ) if 'ListServices' in operations else []

    resources.extend(
        {
            'type': 'namespace',
            'id': namespace.get('Id'),
            'name': namespace.get('Name'),
            'arn': namespace.get('Arn'),
            'namespace_type': namespace.get('Type'),
        }
        for namespace in namespaces
    )
    resources.extend(
        {
            'type': 'service',
            'id': service.get('Id'),
            'name': service.get('Name'),
            'arn': service.get('Arn'),
            'namespace_id': service.get('NamespaceId'),
        }
        for service in services
    )
    for service in services:
        service_id = service.get('Id')
        if not service_id or 'ListInstances' not in operations:
            continue
        resources.extend(
            {
                'type': 'instance',
                'id': instance.get('Id'),
                'service_id': service_id,
                'attributes': instance.get('Attributes'),
            }
            for instance in _safe_value(
                lambda service_id=service_id: core._operation_items(
                    discovery,
                    'list_instances',
                    'Instances',
                    ServiceId=service_id,
                ),
                [],
            )
        )

    return resources
