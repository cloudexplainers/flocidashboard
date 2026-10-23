"""MemoryDB inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def memorydb_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    memorydb = factory.client('memorydb')
    operations = set(memorydb.meta.service_model.operation_names)

    clusters = _safe_value(lambda: core._operation_items(memorydb, 'describe_clusters', 'Clusters'), []) if 'DescribeClusters' in operations else []
    users = _safe_value(lambda: core._operation_items(memorydb, 'describe_users', 'Users'), []) if 'DescribeUsers' in operations else []
    acls = _safe_value(lambda: core._operation_items(memorydb, 'describe_acls', 'ACLs'), []) if 'DescribeACLs' in operations else []
    subnet_groups = _safe_value(lambda: core._operation_items(memorydb, 'describe_subnet_groups', 'SubnetGroups'), []) if 'DescribeSubnetGroups' in operations else []
    parameter_groups = _safe_value(lambda: core._operation_items(memorydb, 'describe_parameter_groups', 'ParameterGroups'), []) if 'DescribeParameterGroups' in operations else []
    snapshots = _safe_value(lambda: core._operation_items(memorydb, 'describe_snapshots', 'Snapshots'), []) if 'DescribeSnapshots' in operations else []

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
        'notes': [],
    }


def list_memorydb_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
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
            for cluster in _safe_value(lambda: core._operation_items(memorydb, 'describe_clusters', 'Clusters'), [])
        )
    if 'DescribeUsers' in operations:
        resources.extend(
            {
                'type': 'user',
                'name': user.get('Name'),
                'arn': user.get('ARN'),
                'status': user.get('Status'),
            }
            for user in _safe_value(lambda: core._operation_items(memorydb, 'describe_users', 'Users'), [])
        )
    if 'DescribeACLs' in operations:
        resources.extend(
            {
                'type': 'acl',
                'name': acl.get('Name'),
                'arn': acl.get('ARN'),
                'status': acl.get('Status'),
            }
            for acl in _safe_value(lambda: core._operation_items(memorydb, 'describe_acls', 'ACLs'), [])
        )

    return resources
