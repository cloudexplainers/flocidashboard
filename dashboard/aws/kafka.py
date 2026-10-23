"""MSK / Kafka inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any, Optional

from dashboard import aws as core
from dashboard.aws import _safe_value


def kafka_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    kafka = factory.client('kafka')
    cluster_v1_summaries = _safe_value(lambda: core._paginate(kafka, 'list_clusters', 'ClusterInfoList'), [])
    cluster_v2_summaries = _safe_value(lambda: core._paginate(kafka, 'list_clusters_v2', 'ClusterInfoList'), [])
    configurations = _safe_value(lambda: core._paginate(kafka, 'list_configurations', 'Configurations'), [])
    kafka_versions = _safe_value(lambda: core._paginate(kafka, 'list_kafka_versions', 'KafkaVersions'), [])

    def cluster_arn(summary: dict[str, Any]) -> Optional[str]:
        return summary.get('ClusterArn') or summary.get('Provisioned', {}).get('ClusterArn') or summary.get('Serverless', {}).get('ClusterArn')

    def cluster_name(summary: dict[str, Any]) -> Optional[str]:
        return summary.get('ClusterName') or summary.get('Provisioned', {}).get('ClusterName') or summary.get('Serverless', {}).get('ClusterName')

    def cluster_detail(summary: dict[str, Any]) -> dict[str, Any]:
        arn = cluster_arn(summary)
        detail = _safe_value(lambda arn=arn: kafka.describe_cluster_v2(ClusterArn=arn).get('ClusterInfo', {}), {}) if arn else {}
        if not detail and arn:
            detail = _safe_value(lambda arn=arn: kafka.describe_cluster(ClusterArn=arn).get('ClusterInfo', {}), {})
        provisioned = detail.get('Provisioned') or summary.get('Provisioned') or {}
        serverless = detail.get('Serverless') or summary.get('Serverless') or {}
        effective = provisioned or serverless or detail or summary
        nodes = _safe_value(lambda arn=arn: core._paginate(kafka, 'list_nodes', 'NodeInfoList', ClusterArn=arn), []) if arn else []
        operations = _safe_value(lambda arn=arn: core._paginate(kafka, 'list_cluster_operations', 'ClusterOperationInfoList', ClusterArn=arn), []) if arn else []
        scram_secrets = _safe_value(lambda arn=arn: core._paginate(kafka, 'list_scram_secrets', 'SecretArnList', ClusterArn=arn), []) if arn else []
        vpc_connections = _safe_value(lambda arn=arn: core._paginate(kafka, 'list_client_vpc_connections', 'ClientVpcConnections', ClusterArn=arn), []) if arn else []
        bootstrap_brokers = _safe_value(lambda arn=arn: kafka.get_bootstrap_brokers(ClusterArn=arn), {}) if arn else {}
        tags = _safe_value(lambda arn=arn: kafka.list_tags_for_resource(ResourceArn=arn).get('Tags', {}), {}) if arn else {}

        return {
            'name': cluster_name(summary) or effective.get('ClusterName') or arn,
            'arn': arn,
            'type': detail.get('ClusterType') or summary.get('ClusterType') or ('SERVERLESS' if serverless else 'PROVISIONED'),
            'state': detail.get('State') or summary.get('State') or effective.get('State'),
            'created': detail.get('CreationTime') or summary.get('CreationTime') or effective.get('CreationTime'),
            'current_version': detail.get('CurrentVersion') or effective.get('CurrentVersion'),
            'kafka_version': effective.get('CurrentBrokerSoftwareInfo', {}).get('KafkaVersion') or effective.get('KafkaVersion'),
            'number_of_broker_nodes': effective.get('NumberOfBrokerNodes'),
            'broker_node_group_info': effective.get('BrokerNodeGroupInfo'),
            'serverless': serverless,
            'provisioned': provisioned,
            'encryption_info': effective.get('EncryptionInfo'),
            'client_authentication': effective.get('ClientAuthentication'),
            'logging_info': effective.get('LoggingInfo'),
            'open_monitoring': effective.get('OpenMonitoring'),
            'storage_mode': effective.get('StorageMode'),
            'connectivity_info': effective.get('ConnectivityInfo'),
            'bootstrap_brokers': bootstrap_brokers,
            'node_count': len(nodes),
            'operation_count': len(operations),
            'scram_secret_count': len(scram_secrets),
            'vpc_connection_count': len(vpc_connections),
            'nodes': nodes,
            'operations': operations,
            'scram_secrets': scram_secrets,
            'client_vpc_connections': vpc_connections,
            'tags': tags,
        }

    clusters_by_arn: dict[str, dict[str, Any]] = {}
    for summary in [*cluster_v1_summaries, *cluster_v2_summaries]:
        arn = cluster_arn(summary) or cluster_name(summary) or str(len(clusters_by_arn))
        clusters_by_arn.setdefault(arn, summary)

    clusters = [cluster_detail(summary) for summary in clusters_by_arn.values()]
    nodes = [
        {
            'name': node.get('BrokerNodeInfo', {}).get('BrokerId') or node.get('NodeARN'),
            'cluster_name': cluster.get('name'),
            'node_arn': node.get('NodeARN'),
            'node_type': node.get('NodeType'),
            'broker_node_info': node.get('BrokerNodeInfo'),
            'zookeeper_node_info': node.get('ZookeeperNodeInfo'),
        }
        for cluster in clusters
        for node in cluster.get('nodes', [])
    ]
    operations = [
        {
            'name': operation.get('ClusterOperationArn'),
            'cluster_name': cluster.get('name'),
            'operation_arn': operation.get('ClusterOperationArn'),
            'operation_type': operation.get('OperationType'),
            'operation_state': operation.get('OperationState'),
            'creation_time': operation.get('CreationTime'),
            'end_time': operation.get('EndTime'),
            'source_cluster_info': operation.get('SourceClusterInfo'),
            'target_cluster_info': operation.get('TargetClusterInfo'),
        }
        for cluster in clusters
        for operation in cluster.get('operations', [])
    ]
    configuration_details = [
        {
            'name': configuration.get('Name'),
            'arn': configuration.get('Arn'),
            'description': configuration.get('Description'),
            'kafka_versions': configuration.get('KafkaVersions'),
            'latest_revision': configuration.get('LatestRevision'),
            'state': configuration.get('State'),
            'created': configuration.get('CreationTime'),
        }
        for configuration in configurations
    ]

    return {
        'summary': {
            'clusters': len(clusters),
            'nodes': len(nodes),
            'operations': len(operations),
            'configurations': len(configuration_details),
            'kafka_versions': len(kafka_versions),
            'scram_secrets': sum(cluster.get('scram_secret_count') or 0 for cluster in clusters),
            'client_vpc_connections': sum(cluster.get('vpc_connection_count') or 0 for cluster in clusters),
        },
        'clusters': clusters,
        'nodes': nodes,
        'operations': operations,
        'configurations': configuration_details,
        'kafka_versions': kafka_versions,
        'supported_from_sdk': [
            'CreateCluster',
            'CreateClusterV2',
            'DescribeCluster',
            'DescribeClusterV2',
            'ListClusters',
            'ListClustersV2',
            'DeleteCluster',
            'GetBootstrapBrokers',
        ],
        'notes': [
            'Floci emulates Amazon MSK by orchestrating one Redpanda container per cluster unless mock mode is enabled.',
            'CreateClusterV2 is mapped to provisioned cluster creation for local compatibility.',
            'Kafka API port 9092 is mapped to a dynamic host port, and GetBootstrapBrokers returns the local connection strings.',
            'Each cluster gets a named Docker volume; in persistent modes the volume is retained on delete unless volume pruning is enabled.',
        ],
    }


def list_kafka_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    kafka = factory.client('kafka')
    resources: list[dict[str, Any]] = []
    clusters = _safe_value(lambda: core._paginate(kafka, 'list_clusters_v2', 'ClusterInfoList'), [])
    if not clusters:
        clusters = _safe_value(lambda: core._paginate(kafka, 'list_clusters', 'ClusterInfoList'), [])

    for cluster in clusters:
        arn = cluster.get('ClusterArn') or cluster.get('Provisioned', {}).get('ClusterArn') or cluster.get('Serverless', {}).get('ClusterArn')
        name = cluster.get('ClusterName') or cluster.get('Provisioned', {}).get('ClusterName') or cluster.get('Serverless', {}).get('ClusterName')
        resources.append({
            'type': 'cluster',
            'name': name,
            'arn': arn,
            'state': cluster.get('State'),
            'cluster_type': cluster.get('ClusterType'),
        })
        if arn:
            resources.extend(
                {
                    'type': 'node',
                    'cluster': name,
                    'arn': node.get('NodeARN'),
                    'node_type': node.get('NodeType'),
                }
                for node in _safe_value(lambda arn=arn: core._paginate(kafka, 'list_nodes', 'NodeInfoList', ClusterArn=arn), [])
            )
            resources.extend(
                {
                    'type': 'operation',
                    'cluster': name,
                    'arn': operation.get('ClusterOperationArn'),
                    'operation_type': operation.get('OperationType'),
                    'state': operation.get('OperationState'),
                }
                for operation in _safe_value(lambda arn=arn: core._paginate(kafka, 'list_cluster_operations', 'ClusterOperationInfoList', ClusterArn=arn), [])
            )

    resources.extend(
        {
            'type': 'configuration',
            'name': configuration.get('Name'),
            'arn': configuration.get('Arn'),
            'state': configuration.get('State'),
        }
        for configuration in _safe_value(lambda: core._paginate(kafka, 'list_configurations', 'Configurations'), [])
    )
    return resources
