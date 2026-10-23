"""Auto Scaling inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _autoscaling_operation_supported(client, operation_name: str) -> bool:
    return operation_name in client.meta.service_model.operation_names


def _autoscaling_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes | {'UnsupportedOperation'}:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def autoscaling_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    autoscaling = factory.client('autoscaling')

    groups = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_auto_scaling_groups', 'AutoScalingGroups'),
        [],
    )
    launch_configurations = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_launch_configurations', 'LaunchConfigurations'),
        [],
    )
    policies = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_policies', 'ScalingPolicies'),
        [],
    )
    scheduled_actions = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_scheduled_actions', 'ScheduledUpdateGroupActions'),
        [],
    )
    activities = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_scaling_activities', 'Activities'),
        [],
    )
    notification_configurations = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_notification_configurations', 'NotificationConfigurations'),
        [],
    )
    tags = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_tags', 'Tags'),
        [],
    )
    account_limits = _autoscaling_optional(
        lambda: autoscaling.describe_account_limits(),
        {'ResourceContention'},
    )
    adjustment_types = _autoscaling_optional(
        lambda: autoscaling.describe_adjustment_types().get('AdjustmentTypes', []),
        {'ResourceContention'},
    )
    metric_collection_types = _autoscaling_optional(
        lambda: autoscaling.describe_metric_collection_types(),
        {'ResourceContention'},
    )
    scaling_process_types = _autoscaling_optional(
        lambda: autoscaling.describe_scaling_process_types().get('Processes', []),
        {'ResourceContention'},
    )
    termination_policy_types = _autoscaling_optional(
        lambda: autoscaling.describe_termination_policy_types().get('TerminationPolicyTypes', []),
        {'ResourceContention'},
    )

    policies_by_group: dict[str, list[dict[str, Any]]] = {}
    for policy in policies:
        group_name = policy.get('AutoScalingGroupName')
        if group_name:
            policies_by_group.setdefault(group_name, []).append(_clean_response(policy))

    scheduled_actions_by_group: dict[str, list[dict[str, Any]]] = {}
    for action in scheduled_actions:
        group_name = action.get('AutoScalingGroupName')
        if group_name:
            scheduled_actions_by_group.setdefault(group_name, []).append(_clean_response(action))

    activities_by_group: dict[str, list[dict[str, Any]]] = {}
    for activity in activities:
        group_name = activity.get('AutoScalingGroupName')
        if group_name:
            activities_by_group.setdefault(group_name, []).append(_clean_response(activity))

    notifications_by_group: dict[str, list[dict[str, Any]]] = {}
    for configuration in notification_configurations:
        group_name = configuration.get('AutoScalingGroupName')
        if group_name:
            notifications_by_group.setdefault(group_name, []).append(_clean_response(configuration))

    tags_by_group: dict[str, list[dict[str, Any]]] = {}
    for tag in tags:
        resource_type = tag.get('ResourceType')
        resource_id = tag.get('ResourceId')
        if resource_type == 'auto-scaling-group' and resource_id:
            tags_by_group.setdefault(resource_id, []).append(_clean_response(tag))

    def group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group.get('AutoScalingGroupName')
        lifecycle_hooks = _autoscaling_optional(
            lambda: autoscaling.describe_lifecycle_hooks(AutoScalingGroupName=name).get('LifecycleHooks', []),
            {'ResourceContention', 'ValidationError'},
        )
        warm_pool = _autoscaling_optional(
            lambda: autoscaling.describe_warm_pool(AutoScalingGroupName=name),
            {'ResourceContention', 'ValidationError'},
        ) if _autoscaling_operation_supported(autoscaling, 'DescribeWarmPool') else None
        instance_refreshes = _autoscaling_optional(
            lambda: core._paginate(
                autoscaling,
                'describe_instance_refreshes',
                'InstanceRefreshes',
                AutoScalingGroupName=name,
            ),
            {'ResourceContention', 'ValidationError'},
        ) if _autoscaling_operation_supported(autoscaling, 'DescribeInstanceRefreshes') else None

        instances = group.get('Instances', [])

        return {
            'name': name,
            'arn': group.get('AutoScalingGroupARN'),
            'launch_configuration_name': group.get('LaunchConfigurationName'),
            'launch_template': group.get('LaunchTemplate'),
            # The console's "Launch template/configuration" column.
            'launch_template_name': (group.get('LaunchTemplate') or {}).get('LaunchTemplateName'),
            'mixed_instances_policy': group.get('MixedInstancesPolicy'),
            'min_size': group.get('MinSize'),
            'max_size': group.get('MaxSize'),
            'desired_capacity': group.get('DesiredCapacity'),
            'default_cooldown': group.get('DefaultCooldown'),
            'availability_zones': group.get('AvailabilityZones'),
            'load_balancer_names': group.get('LoadBalancerNames'),
            'target_group_arns': group.get('TargetGroupARNs'),
            'health_check_type': group.get('HealthCheckType'),
            'health_check_grace_period': group.get('HealthCheckGracePeriod'),
            'placement_group': group.get('PlacementGroup'),
            'vpc_zone_identifier': group.get('VPCZoneIdentifier'),
            'status': group.get('Status'),
            'created_time': group.get('CreatedTime'),
            'suspended_processes': group.get('SuspendedProcesses'),
            'enabled_metrics': group.get('EnabledMetrics'),
            'termination_policies': group.get('TerminationPolicies'),
            'new_instances_protected_from_scale_in': group.get('NewInstancesProtectedFromScaleIn'),
            'service_linked_role_arn': group.get('ServiceLinkedRoleARN'),
            'capacity_rebalance': group.get('CapacityRebalance'),
            'traffic_sources': group.get('TrafficSources'),
            'availability_zone_distribution': group.get('AvailabilityZoneDistribution'),
            'availability_zone_impairment_policy': group.get('AvailabilityZoneImpairmentPolicy'),
            'capacity_reservation_specification': group.get('CapacityReservationSpecification'),
            'instances': instances,
            'instance_count': len(instances),
            'in_service_instances': sum(
                1
                for instance in instances
                if instance.get('LifecycleState') == 'InService'
            ),
            'policies': policies_by_group.get(name, []),
            'scheduled_actions': scheduled_actions_by_group.get(name, []),
            'activities': activities_by_group.get(name, []),
            'lifecycle_hooks': lifecycle_hooks,
            'warm_pool': warm_pool,
            'instance_refreshes': instance_refreshes,
            'notification_configurations': notifications_by_group.get(name, []),
            'tags': tags_by_group.get(name, group.get('Tags', [])),
        }

    detailed_groups = [group_detail(group) for group in groups]

    return {
        'summary': {
            'groups': len(detailed_groups),
            'instances': sum(group.get('instance_count') or 0 for group in detailed_groups),
            'in_service_instances': sum(group.get('in_service_instances') or 0 for group in detailed_groups),
            'launch_configurations': len(launch_configurations),
            'scaling_policies': len(policies),
            'scheduled_actions': len(scheduled_actions),
            'activities': len(activities),
            'lifecycle_hooks': sum(
                len(group.get('lifecycle_hooks') or [])
                for group in detailed_groups
                if isinstance(group.get('lifecycle_hooks'), list)
            ),
        },
        'groups': detailed_groups,
        'launch_configurations': _clean_response(launch_configurations),
        'scaling_policies': _clean_response(policies),
        'scheduled_actions': _clean_response(scheduled_actions),
        'activities': _clean_response(activities),
        'notification_configurations': _clean_response(notification_configurations),
        'tags': _clean_response(tags),
        'account_limits': account_limits,
        # Reference values, not resources.
        'reference': {
            'adjustment_types': adjustment_types,
            'metric_collection_types': metric_collection_types,
            'scaling_process_types': scaling_process_types,
            'termination_policy_types': termination_policy_types,
        },
        'supported_from_sdk': [
            'CreateAutoScalingGroup',
            'UpdateAutoScalingGroup',
            'DeleteAutoScalingGroup',
            'DescribeAutoScalingGroups',
            'CreateLaunchConfiguration',
            'DescribeLaunchConfigurations',
            'DeleteLaunchConfiguration',
            'PutScalingPolicy',
            'DescribePolicies',
            'DeletePolicy',
            'PutScheduledUpdateGroupAction',
            'DescribeScheduledActions',
            'DeleteScheduledAction',
            'DescribeScalingActivities',
            'SetDesiredCapacity',
            'TerminateInstanceInAutoScalingGroup',
            'SuspendProcesses',
            'ResumeProcesses',
            'PutLifecycleHook',
            'DescribeLifecycleHooks',
            'DeleteLifecycleHook',
            'PutNotificationConfiguration',
            'DescribeNotificationConfigurations',
            'DeleteNotificationConfiguration',
            'DescribeTags',
            'CreateOrUpdateTags',
            'DeleteTags',
            'DescribeAccountLimits',
        ],
        'notes': [
            'This page uses the EC2 Auto Scaling SDK API and tolerates missing optional operations from local implementations.',
            'Group detail expands lifecycle hooks, warm pool data, instance refreshes, policies, activities, notifications, and tags when available.',
            'Floci 1.5.27 rejects launch templates without image IDs.',
            'Floci 1.5.26 adds group reconciliation and refresh behavior, preserves mixed instances policy parity, and validates mixed instances launch templates.',
        ],
    }


def list_autoscaling_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    autoscaling = factory.client('autoscaling')
    resources: list[dict[str, Any]] = []
    groups = _safe_value(
        lambda: core._paginate(autoscaling, 'describe_auto_scaling_groups', 'AutoScalingGroups'),
        [],
    )
    resources.extend(
        {
            'type': 'auto_scaling_group',
            'name': group.get('AutoScalingGroupName'),
            'arn': group.get('AutoScalingGroupARN'),
            'desired_capacity': group.get('DesiredCapacity'),
            'min_size': group.get('MinSize'),
            'max_size': group.get('MaxSize'),
            'instance_count': len(group.get('Instances', [])),
        }
        for group in groups
    )
    resources.extend(
        {
            'type': 'launch_configuration',
            'name': configuration.get('LaunchConfigurationName'),
            'arn': configuration.get('LaunchConfigurationARN'),
            'image_id': configuration.get('ImageId'),
            'instance_type': configuration.get('InstanceType'),
        }
        for configuration in _safe_value(
            lambda: core._paginate(autoscaling, 'describe_launch_configurations', 'LaunchConfigurations'),
            [],
        )
    )
    resources.extend(
        {
            'type': 'scaling_policy',
            'name': policy.get('PolicyName'),
            'arn': policy.get('PolicyARN'),
            'group': policy.get('AutoScalingGroupName'),
            'policy_type': policy.get('PolicyType'),
        }
        for policy in _safe_value(
            lambda: core._paginate(autoscaling, 'describe_policies', 'ScalingPolicies'),
            [],
        )
    )
    resources.extend(
        {
            'type': 'scheduled_action',
            'name': action.get('ScheduledActionName'),
            'arn': action.get('ScheduledActionARN'),
            'group': action.get('AutoScalingGroupName'),
            'recurrence': action.get('Recurrence'),
        }
        for action in _safe_value(
            lambda: core._paginate(autoscaling, 'describe_scheduled_actions', 'ScheduledUpdateGroupActions'),
            [],
        )
    )
    return resources
