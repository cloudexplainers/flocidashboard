"""ElastiCache inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _elasticache_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def elasticache_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    elasticache = factory.client('elasticache')

    cache_clusters = _safe_value(
        lambda: core._paginate(
            elasticache,
            'describe_cache_clusters',
            'CacheClusters',
            ShowCacheNodeInfo=True,
            ShowCacheClustersNotInReplicationGroups=True,
        ),
        [],
    )
    replication_groups = _safe_value(lambda: core._paginate(elasticache, 'describe_replication_groups', 'ReplicationGroups'), [])
    subnet_groups = _safe_value(lambda: core._paginate(elasticache, 'describe_cache_subnet_groups', 'CacheSubnetGroups'), [])
    parameter_groups = _safe_value(lambda: core._paginate(elasticache, 'describe_cache_parameter_groups', 'CacheParameterGroups'), [])
    security_groups = _safe_value(lambda: core._paginate(elasticache, 'describe_cache_security_groups', 'CacheSecurityGroups'), [])
    snapshots = _safe_value(lambda: core._paginate(elasticache, 'describe_snapshots', 'Snapshots'), [])
    users = _safe_value(lambda: core._paginate(elasticache, 'describe_users', 'Users'), [])
    user_groups = _safe_value(lambda: core._paginate(elasticache, 'describe_user_groups', 'UserGroups'), [])
    serverless_caches = _safe_value(lambda: core._paginate(elasticache, 'describe_serverless_caches', 'ServerlessCaches'), [])
    global_replication_groups = _safe_value(lambda: core._paginate(elasticache, 'describe_global_replication_groups', 'GlobalReplicationGroups'), [])
    events = _safe_value(lambda: core._paginate(elasticache, 'describe_events', 'Events', MaxRecords=50), [])

    return {
        'summary': {
            'cache_clusters': len(cache_clusters),
            'replication_groups': len(replication_groups),
            'serverless_caches': len(serverless_caches),
            'subnet_groups': len(subnet_groups),
            'parameter_groups': len(parameter_groups),
            'snapshots': len(snapshots),
            'users': len(users),
            'user_groups': len(user_groups),
            'global_replication_groups': len(global_replication_groups),
            'events': len(events),
        },
        'cache_clusters': [
            {
                'name': cluster.get('CacheClusterId'),
                'arn': cluster.get('ARN'),
                'engine': cluster.get('Engine'),
                'engine_version': cluster.get('EngineVersion'),
                'status': cluster.get('CacheClusterStatus'),
                'node_type': cluster.get('CacheNodeType'),
                'num_cache_nodes': cluster.get('NumCacheNodes'),
                'preferred_availability_zone': cluster.get('PreferredAvailabilityZone'),
                'cache_subnet_group_name': cluster.get('CacheSubnetGroupName'),
                'replication_group_id': cluster.get('ReplicationGroupId'),
                'security_groups': cluster.get('SecurityGroups'),
                'cache_security_groups': cluster.get('CacheSecurityGroups'),
                'cache_parameter_group': cluster.get('CacheParameterGroup'),
                'cache_nodes': cluster.get('CacheNodes'),
                'configuration_endpoint': cluster.get('ConfigurationEndpoint'),
                'notification_configuration': cluster.get('NotificationConfiguration'),
                'pending_modified_values': cluster.get('PendingModifiedValues'),
                'created': cluster.get('CacheClusterCreateTime'),
                'preferred_maintenance_window': cluster.get('PreferredMaintenanceWindow'),
                'auto_minor_version_upgrade': cluster.get('AutoMinorVersionUpgrade'),
                'snapshot_retention_limit': cluster.get('SnapshotRetentionLimit'),
                'snapshot_window': cluster.get('SnapshotWindow'),
                'auth_token_enabled': cluster.get('AuthTokenEnabled'),
                'transit_encryption_enabled': cluster.get('TransitEncryptionEnabled'),
                'at_rest_encryption_enabled': cluster.get('AtRestEncryptionEnabled'),
                'log_delivery_configurations': cluster.get('LogDeliveryConfigurations'),
            }
            for cluster in cache_clusters
        ],
        'replication_groups': [
            {
                'name': group.get('ReplicationGroupId'),
                'arn': group.get('ARN'),
                'description': group.get('Description'),
                'status': group.get('Status'),
                'pending_modified_values': group.get('PendingModifiedValues'),
                'member_clusters': group.get('MemberClusters'),
                'node_groups': group.get('NodeGroups'),
                'snapshotting_cluster_id': group.get('SnapshottingClusterId'),
                'automatic_failover': group.get('AutomaticFailover'),
                'multi_az': group.get('MultiAZ'),
                'configuration_endpoint': group.get('ConfigurationEndpoint'),
                'snapshot_retention_limit': group.get('SnapshotRetentionLimit'),
                'snapshot_window': group.get('SnapshotWindow'),
                'cluster_enabled': group.get('ClusterEnabled'),
                'cache_node_type': group.get('CacheNodeType'),
                'auth_token_enabled': group.get('AuthTokenEnabled'),
                'transit_encryption_enabled': group.get('TransitEncryptionEnabled'),
                'at_rest_encryption_enabled': group.get('AtRestEncryptionEnabled'),
                'kms_key_id': group.get('KmsKeyId'),
                'user_group_ids': group.get('UserGroupIds'),
                'log_delivery_configurations': group.get('LogDeliveryConfigurations'),
                'data_tiering': group.get('DataTiering'),
            }
            for group in replication_groups
        ],
        'serverless_caches': [
            {
                'name': cache.get('ServerlessCacheName'),
                'arn': cache.get('ARN'),
                'engine': cache.get('Engine'),
                'major_engine_version': cache.get('MajorEngineVersion'),
                'full_engine_version': cache.get('FullEngineVersion'),
                'status': cache.get('Status'),
                'endpoint': cache.get('Endpoint'),
                'reader_endpoint': cache.get('ReaderEndpoint'),
                'description': cache.get('Description'),
                'cache_usage_limits': cache.get('CacheUsageLimits'),
                'kms_key_id': cache.get('KmsKeyId'),
                'security_group_ids': cache.get('SecurityGroupIds'),
                'subnet_ids': cache.get('SubnetIds'),
                'user_group_id': cache.get('UserGroupId'),
                'created': cache.get('CreateTime'),
                'snapshot_retention_limit': cache.get('SnapshotRetentionLimit'),
                'daily_snapshot_time': cache.get('DailySnapshotTime'),
            }
            for cache in serverless_caches
        ],
        'subnet_groups': subnet_groups,
        'parameter_groups': parameter_groups,
        'security_groups': security_groups,
        'snapshots': snapshots,
        'users': users,
        'user_groups': user_groups,
        'global_replication_groups': global_replication_groups,
        'events': events,
        'supported_from_sdk': [
            'CreateCacheCluster',
            'DescribeCacheClusters',
            'ModifyCacheCluster',
            'DeleteCacheCluster',
            'CreateReplicationGroup',
            'DescribeReplicationGroups',
            'ModifyReplicationGroup',
            'DeleteReplicationGroup',
            'CreateServerlessCache',
            'DescribeServerlessCaches',
            'ModifyServerlessCache',
            'DeleteServerlessCache',
            'CreateCacheSubnetGroup',
            'DescribeCacheSubnetGroups',
            'CreateCacheParameterGroup',
            'DescribeCacheParameterGroups',
            'CreateUser',
            'DescribeUsers',
            'CreateUserGroup',
            'DescribeUserGroups',
            'CreateSnapshot',
            'DescribeSnapshots',
            'DescribeEvents',
        ],
        'notes': [
            'Floci ElastiCache uses the AWS-compatible Query management API and Redis RESP for the data plane.',
            'CreateReplicationGroup starts a real Valkey or Redis Docker container and exposes it through a local TCP proxy port.',
            'Supported interactive workflows include replication-group lifecycle, ElastiCache users, user access strings, and IAM auth token validation.',
        ],
    }


def list_elasticache_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    elasticache = factory.client('elasticache')
    resources: list[dict[str, Any]] = []
    resources.extend(
        {
            'type': 'cache_cluster',
            'name': cluster.get('CacheClusterId'),
            'engine': cluster.get('Engine'),
            'status': cluster.get('CacheClusterStatus'),
        }
        for cluster in _safe_value(lambda: core._paginate(elasticache, 'describe_cache_clusters', 'CacheClusters'), [])
    )
    resources.extend(
        {
            'type': 'replication_group',
            'name': group.get('ReplicationGroupId'),
            'status': group.get('Status'),
        }
        for group in _safe_value(lambda: core._paginate(elasticache, 'describe_replication_groups', 'ReplicationGroups'), [])
    )
    resources.extend(
        {
            'type': 'serverless_cache',
            'name': cache.get('ServerlessCacheName'),
            'engine': cache.get('Engine'),
            'status': cache.get('Status'),
        }
        for cache in _safe_value(lambda: core._paginate(elasticache, 'describe_serverless_caches', 'ServerlessCaches'), [])
    )
    resources.extend(
        {
            'type': 'subnet_group',
            'name': group.get('CacheSubnetGroupName'),
        }
        for group in _safe_value(lambda: core._paginate(elasticache, 'describe_cache_subnet_groups', 'CacheSubnetGroups'), [])
    )
    resources.extend(
        {
            'type': 'parameter_group',
            'name': group.get('CacheParameterGroupName'),
        }
        for group in _safe_value(lambda: core._paginate(elasticache, 'describe_cache_parameter_groups', 'CacheParameterGroups'), [])
    )
    resources.extend(
        {
            'type': 'snapshot',
            'name': snapshot.get('SnapshotName'),
            'status': snapshot.get('SnapshotStatus'),
        }
        for snapshot in _safe_value(lambda: core._paginate(elasticache, 'describe_snapshots', 'Snapshots'), [])
    )
    resources.extend(
        {
            'type': 'user',
            'name': user.get('UserName') or user.get('UserId'),
            'id': user.get('UserId'),
        }
        for user in _safe_value(lambda: core._paginate(elasticache, 'describe_users', 'Users'), [])
    )
    resources.extend(
        {
            'type': 'user_group',
            'name': group.get('UserGroupId'),
            'status': group.get('Status'),
        }
        for group in _safe_value(lambda: core._paginate(elasticache, 'describe_user_groups', 'UserGroups'), [])
    )
    return resources
