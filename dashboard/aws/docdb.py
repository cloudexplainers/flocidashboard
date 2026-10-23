"""DocumentDB inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def docdb_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    docdb = factory.client('docdb')
    operations = set(docdb.meta.service_model.operation_names)

    clusters = _safe_value(
        lambda: core._operation_items(docdb, 'describe_db_clusters', 'DBClusters'),
        [],
    ) if 'DescribeDBClusters' in operations else []
    instances = _safe_value(
        lambda: core._operation_items(docdb, 'describe_db_instances', 'DBInstances'),
        [],
    ) if 'DescribeDBInstances' in operations else []
    # DescribeDBClusters/Instances return every RDS-family resource (RDS, Neptune,
    # DocumentDB share one API). The docdb console lists only engine "docdb".
    clusters = [item for item in clusters if item.get('Engine') == 'docdb']
    instances = [item for item in instances if item.get('Engine') == 'docdb']

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
            'DocumentDB can run in mock mode or start a MongoDB-compatible data container for created clusters.',
        ],
    }


def list_docdb_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
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
            for cluster in _safe_value(lambda: core._operation_items(docdb, 'describe_db_clusters', 'DBClusters'), [])
            if cluster.get('Engine') == 'docdb'
        )

    if 'DescribeDBInstances' in operations:
        resources.extend(
            {
                'type': 'db_instance',
                'name': instance.get('DBInstanceIdentifier'),
                'arn': instance.get('DBInstanceArn'),
                'status': instance.get('DBInstanceStatus'),
            }
            for instance in _safe_value(lambda: core._operation_items(docdb, 'describe_db_instances', 'DBInstances'), [])
            if instance.get('Engine') == 'docdb'
        )

    return resources
