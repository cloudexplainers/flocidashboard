"""Neptune inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def neptune_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    neptune = factory.client('neptune')
    operations = set(neptune.meta.service_model.operation_names)

    clusters = _safe_value(
        lambda: core._operation_items(neptune, 'describe_db_clusters', 'DBClusters'),
        [],
    ) if 'DescribeDBClusters' in operations else []
    instances = _safe_value(
        lambda: core._operation_items(neptune, 'describe_db_instances', 'DBInstances'),
        [],
    ) if 'DescribeDBInstances' in operations else []
    subnet_groups = _safe_value(
        lambda: core._operation_items(neptune, 'describe_db_subnet_groups', 'DBSubnetGroups'),
        [],
    ) if 'DescribeDBSubnetGroups' in operations else []
    cluster_snapshots = _safe_value(
        lambda: core._operation_items(neptune, 'describe_db_cluster_snapshots', 'DBClusterSnapshots'),
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


def list_neptune_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
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
            for cluster in _safe_value(lambda: core._operation_items(neptune, 'describe_db_clusters', 'DBClusters'), [])
        )

    if 'DescribeDBInstances' in operations:
        resources.extend(
            {
                'type': 'db_instance',
                'name': instance.get('DBInstanceIdentifier'),
                'arn': instance.get('DBInstanceArn'),
                'status': instance.get('DBInstanceStatus'),
            }
            for instance in _safe_value(lambda: core._operation_items(neptune, 'describe_db_instances', 'DBInstances'), [])
        )

    return resources
