"""RDS inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _rds_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def rds_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    rds = factory.client('rds')
    raw_instances = _rds_optional(lambda: core._paginate(rds, 'describe_db_instances', 'DBInstances'), set())
    raw_clusters = _rds_optional(lambda: core._paginate(rds, 'describe_db_clusters', 'DBClusters'), set())
    raw_groups = _rds_optional(lambda: core._paginate(rds, 'describe_db_parameter_groups', 'DBParameterGroups'), set())
    issues = [f'{label}: {value["error"]}' for label, value in [('Databases', raw_instances), ('Clusters', raw_clusters), ('Parameter groups', raw_groups)] if isinstance(value, dict) and value.get('error')]
    instances = raw_instances if isinstance(raw_instances, list) else []
    clusters = raw_clusters if isinstance(raw_clusters, list) else []
    parameter_groups = raw_groups if isinstance(raw_groups, list) else []

    def instance_detail(instance: dict[str, Any]) -> dict[str, Any]:
        endpoint = instance.get('Endpoint') or {}
        return {
            'name': instance.get('DBInstanceIdentifier'),
            'arn': instance.get('DBInstanceArn'),
            'status': instance.get('DBInstanceStatus'),
            'engine': instance.get('Engine'),
            'engine_version': instance.get('EngineVersion'),
            'class': instance.get('DBInstanceClass'),
            'allocated_storage': instance.get('AllocatedStorage'),
            'storage_type': instance.get('StorageType'),
            'multi_az': instance.get('MultiAZ'),
            'publicly_accessible': instance.get('PubliclyAccessible'),
            'iam_authentication': instance.get('IAMDatabaseAuthenticationEnabled'),
            'master_username': instance.get('MasterUsername'),
            'endpoint': endpoint,
            'connect_host': endpoint.get('Address'),
            'connect_port': endpoint.get('Port'),
            'db_name': instance.get('DBName'),
            'parameter_groups': instance.get('DBParameterGroups'),
            'subnet_group': instance.get('DBSubnetGroup'),
            'security_groups': instance.get('VpcSecurityGroups'),
            'created': instance.get('InstanceCreateTime'),
            'cluster_identifier': instance.get('DBClusterIdentifier'),
            'availability_zone': instance.get('AvailabilityZone'),
            'secondary_availability_zone': instance.get('SecondaryAvailabilityZone'),
            'storage_encrypted': instance.get('StorageEncrypted'),
            'deletion_protection': instance.get('DeletionProtection'),
            'backup_retention_period': instance.get('BackupRetentionPeriod'),
            'pending_modifications': instance.get('PendingModifiedValues'),
            'tags': _rds_optional(
                lambda: rds.list_tags_for_resource(ResourceName=instance.get('DBInstanceArn')).get('TagList', []),
                {'DBInstanceNotFound', 'InvalidParameterValue'},
            ) if instance.get('DBInstanceArn') else None,
        }

    def cluster_detail(cluster: dict[str, Any]) -> dict[str, Any]:
        return {
            'name': cluster.get('DBClusterIdentifier'),
            'arn': cluster.get('DBClusterArn'),
            'status': cluster.get('Status'),
            'engine': cluster.get('Engine'),
            'engine_version': cluster.get('EngineVersion'),
            'database_name': cluster.get('DatabaseName'),
            'master_username': cluster.get('MasterUsername'),
            'endpoint': cluster.get('Endpoint'),
            'reader_endpoint': cluster.get('ReaderEndpoint'),
            'storage_encrypted': cluster.get('StorageEncrypted'),
            'deletion_protection': cluster.get('DeletionProtection'),
            'multi_az': cluster.get('MultiAZ'),
            'availability_zones': cluster.get('AvailabilityZones'),
            'subnet_group': cluster.get('DBSubnetGroup'),
            'security_groups': cluster.get('VpcSecurityGroups'),
            'backup_retention_period': cluster.get('BackupRetentionPeriod'),
            'port': cluster.get('Port'),
            'members': cluster.get('DBClusterMembers'),
            'parameter_group': cluster.get('DBClusterParameterGroup'),
            'created': cluster.get('ClusterCreateTime'),
            'iam_authentication': cluster.get('IAMDatabaseAuthenticationEnabled'),
            'tags': _rds_optional(
                lambda: rds.list_tags_for_resource(ResourceName=cluster.get('DBClusterArn')).get('TagList', []),
                {'DBClusterNotFoundFault', 'InvalidParameterValue'},
            ) if cluster.get('DBClusterArn') else None,
        }

    def parameter_group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group.get('DBParameterGroupName')
        parameters = _rds_optional(
            lambda: core._paginate(rds, 'describe_db_parameters', 'Parameters', DBParameterGroupName=name),
            {'DBParameterGroupNotFound'},
        )

        return {
            'name': name,
            'arn': group.get('DBParameterGroupArn'),
            'family': group.get('DBParameterGroupFamily'),
            'description': group.get('Description'),
            'parameters': parameters,
            'parameter_count': len(parameters) if isinstance(parameters, list) else 0,
        }

    detailed_instances = [instance_detail(instance) for instance in instances]
    detailed_clusters = [cluster_detail(cluster) for cluster in clusters]
    detailed_parameter_groups = [parameter_group_detail(group) for group in parameter_groups]

    return {
        'issues': issues,
        'summary': {
            'instances': len(detailed_instances),
            'clusters': len(detailed_clusters),
            'parameter_groups': len(detailed_parameter_groups),
            'available_instances': sum(1 for instance in detailed_instances if instance.get('status') == 'available'),
            'proxy_endpoints': sum(1 for instance in detailed_instances if instance.get('connect_port')),
        },
        'instances': detailed_instances,
        'clusters': detailed_clusters,
        'parameter_groups': detailed_parameter_groups,
        'supported': [
            'CreateDBInstance',
            'DescribeDBInstances',
            'DeleteDBInstance',
            'ModifyDBInstance',
            'RebootDBInstance',
            'CreateDBCluster',
            'DescribeDBClusters',
            'DeleteDBCluster',
            'ModifyDBCluster',
            'CreateDBParameterGroup',
            'DescribeDBParameterGroups',
            'DeleteDBParameterGroup',
            'ModifyDBParameterGroup',
            'DescribeDBParameters',
        ],
        'supported_engines': [
            {'name': 'postgres', 'default_image': 'postgres:16-alpine'},
            {'name': 'mysql', 'default_image': 'mysql:8.0'},
            {'name': 'mariadb', 'default_image': 'mariadb:11'},
        ],
        'configuration': {
            'proxy_base_port': 7001,
            'proxy_max_port': 7099,
            'default_postgres_image': 'postgres:16-alpine',
            'default_mysql_image': 'mysql:8.0',
            'default_mariadb_image': 'mariadb:11',
        },
        'environment_variables': [
            'FLOCI_SERVICES_RDS_ENABLED',
            'FLOCI_SERVICES_RDS_PROXY_BASE_PORT',
            'FLOCI_SERVICES_RDS_PROXY_MAX_PORT',
            'FLOCI_SERVICES_RDS_DEFAULT_POSTGRES_IMAGE',
            'FLOCI_SERVICES_RDS_DEFAULT_MYSQL_IMAGE',
            'FLOCI_SERVICES_RDS_DEFAULT_MARIADB_IMAGE',
            'FLOCI_SERVICES_DOCKER_NETWORK',
            'FLOCI_STORAGE_SERVICES_RDS_MODE',
        ],
        'notes': [
            'RDS management uses Query XML and the data plane uses PostgreSQL or MySQL wire protocol.',
            'Floci manages real PostgreSQL, MySQL, and MariaDB Docker containers.',
            'Floci 1.5.31 adds RDS mock mode so clusters and instances can be created without Docker for CI and fast unit-style tests.',
            'Instances expose local TCP proxy endpoints on localhost:<proxy-port>.',
            'Floci 1.5.28 aligns CreateDBInstance/CreateDBCluster parameter group validation, subnet group placement, and requested database-name proxy handling with AWS behavior.',
            'Floci 1.5.25 adds RDS provisioning lifecycle support for create, describe, modify, and delete flows.',
            'Floci 1.5.25 persists STS session secret keys so RDS IAM database authentication validates across requests.',
            'Floci 1.5.21 restores persisted runtime state on startup for local RDS resources.',
            'IAM database authentication is supported when enabled at instance creation time.',
        ],
    }


def list_rds_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    rds = factory.client('rds')
    resources = [
        {
            'type': 'instance',
            'name': instance.get('DBInstanceIdentifier'),
            'arn': instance.get('DBInstanceArn'),
            'status': instance.get('DBInstanceStatus'),
            'endpoint': instance.get('Endpoint'),
        }
        for instance in _safe_value(lambda: core._paginate(rds, 'describe_db_instances', 'DBInstances'), [])
    ]
    resources.extend(
        {
            'type': 'cluster',
            'name': cluster.get('DBClusterIdentifier'),
            'arn': cluster.get('DBClusterArn'),
            'status': cluster.get('Status'),
        }
        for cluster in _safe_value(lambda: core._paginate(rds, 'describe_db_clusters', 'DBClusters'), [])
    )
    resources.extend(
        {
            'type': 'parameter_group',
            'name': group.get('DBParameterGroupName'),
            'arn': group.get('DBParameterGroupArn'),
        }
        for group in _safe_value(lambda: core._paginate(rds, 'describe_db_parameter_groups', 'DBParameterGroups'), [])
    )
    return resources
