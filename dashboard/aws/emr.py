"""EMR inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def emr_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    emr = factory.client('emr')
    operations = set(emr.meta.service_model.operation_names)

    clusters = _safe_value(
        lambda: core._operation_items(emr, 'list_clusters', 'Clusters'),
        [],
    ) if 'ListClusters' in operations else []

    def cluster_detail(cluster: dict[str, Any]) -> dict[str, Any]:
        cluster_id = cluster.get('Id')
        details = _safe_value(
            lambda: emr.describe_cluster(ClusterId=cluster_id).get('Cluster', {}),
            {},
        ) if cluster_id and 'DescribeCluster' in operations else {}
        instance_groups = _safe_value(
            lambda: core._operation_items(emr, 'list_instance_groups', 'InstanceGroups', ClusterId=cluster_id),
            [],
        ) if cluster_id and 'ListInstanceGroups' in operations else []
        steps = _safe_value(
            lambda: core._operation_items(emr, 'list_steps', 'Steps', ClusterId=cluster_id),
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
        lambda: core._operation_items(emr, 'list_security_configurations', 'SecurityConfigurations'),
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


def list_emr_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
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
        for cluster in _safe_value(lambda: core._operation_items(emr, 'list_clusters', 'Clusters'), [])
    ]
