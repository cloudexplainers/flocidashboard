"""Interactive Auto Scaling helpers for local capacity workflows."""

from __future__ import annotations

import base64
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('autoscaling')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _int_value(value: Any, label: str, default: int | None = None) -> int:
    if value in (None, ''):
        if default is None:
            raise ValueError(f'{label} is required')
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a number') from exc


def _string_list(value: Any) -> list[str]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError('Value must be a comma-separated string or list')


def _tags(value: Any) -> list[dict[str, Any]]:
    if value in (None, '', []):
        return []
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item, 'PropagateAtLaunch': True} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Tags must be an object or list')

    tags = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = item.get('Key') or item.get('key')
        if not key:
            raise ValueError('Each tag needs a Key')
        tag_value = item.get('Value')
        if tag_value is None:
            tag_value = item.get('value')
        tags.append({
            'Key': str(key),
            'Value': '' if tag_value is None else str(tag_value),
            'PropagateAtLaunch': item.get('PropagateAtLaunch', item.get('propagate_at_launch', True)),
        })
    return tags


def _encoded_user_data(value: str) -> str:
    if not value:
        return ''
    return base64.b64encode(value.encode('utf-8')).decode('ascii')


def create_launch_configuration(
    name: str,
    image_id: str,
    instance_type: str,
    *,
    key_name: str = '',
    security_groups: Any = None,
    user_data: str = '',
    iam_instance_profile: str = '',
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'LaunchConfigurationName': _required(name, 'Launch configuration name'),
        'ImageId': _required(image_id, 'Image ID'),
        'InstanceType': _required(instance_type, 'Instance type'),
    }
    if key_name:
        kwargs['KeyName'] = key_name
    groups = _string_list(security_groups)
    if groups:
        kwargs['SecurityGroups'] = groups
    encoded = _encoded_user_data(user_data)
    if encoded:
        kwargs['UserData'] = encoded
    if iam_instance_profile:
        kwargs['IamInstanceProfile'] = iam_instance_profile
    response = _client().create_launch_configuration(**kwargs)
    return {'name': kwargs['LaunchConfigurationName'], 'response': _clean_response(response)}


def delete_launch_configuration(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Launch configuration name')
    response = _client().delete_launch_configuration(LaunchConfigurationName=clean_name)
    return {'name': clean_name, 'response': _clean_response(response)}


def create_auto_scaling_group(
    name: str,
    launch_configuration_name: str,
    *,
    min_size: Any = 1,
    max_size: Any = 1,
    desired_capacity: Any = None,
    availability_zones: Any = None,
    target_group_arns: Any = None,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'AutoScalingGroupName': _required(name, 'Auto Scaling group name'),
        'LaunchConfigurationName': _required(launch_configuration_name, 'Launch configuration name'),
        'MinSize': _int_value(min_size, 'Min size', 1),
        'MaxSize': _int_value(max_size, 'Max size', 1),
    }
    if desired_capacity not in (None, ''):
        kwargs['DesiredCapacity'] = _int_value(desired_capacity, 'Desired capacity')
    zones = _string_list(availability_zones)
    if zones:
        kwargs['AvailabilityZones'] = zones
    target_groups = _string_list(target_group_arns)
    if target_groups:
        kwargs['TargetGroupARNs'] = target_groups
    parsed_tags = _tags(tags)
    if parsed_tags:
        kwargs['Tags'] = parsed_tags
    response = _client().create_auto_scaling_group(**kwargs)
    return {'name': kwargs['AutoScalingGroupName'], 'response': _clean_response(response)}


def update_auto_scaling_group(
    name: str,
    *,
    launch_configuration_name: str = '',
    min_size: Any = None,
    max_size: Any = None,
    desired_capacity: Any = None,
    availability_zones: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {'AutoScalingGroupName': _required(name, 'Auto Scaling group name')}
    if launch_configuration_name:
        kwargs['LaunchConfigurationName'] = launch_configuration_name
    if min_size not in (None, ''):
        kwargs['MinSize'] = _int_value(min_size, 'Min size')
    if max_size not in (None, ''):
        kwargs['MaxSize'] = _int_value(max_size, 'Max size')
    if desired_capacity not in (None, ''):
        kwargs['DesiredCapacity'] = _int_value(desired_capacity, 'Desired capacity')
    zones = _string_list(availability_zones)
    if zones:
        kwargs['AvailabilityZones'] = zones
    if len(kwargs) == 1:
        raise ValueError('At least one group setting must be changed')
    response = _client().update_auto_scaling_group(**kwargs)
    return {'name': kwargs['AutoScalingGroupName'], 'response': _clean_response(response)}


def delete_auto_scaling_group(name: str, *, force_delete: bool = True) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    response = _client().delete_auto_scaling_group(AutoScalingGroupName=clean_name, ForceDelete=bool(force_delete))
    return {'name': clean_name, 'force_delete': bool(force_delete), 'response': _clean_response(response)}


def set_desired_capacity(name: str, desired_capacity: Any, *, honor_cooldown: bool = False) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    desired = _int_value(desired_capacity, 'Desired capacity')
    response = _client().set_desired_capacity(
        AutoScalingGroupName=clean_name,
        DesiredCapacity=desired,
        HonorCooldown=bool(honor_cooldown),
    )
    return {'name': clean_name, 'desired_capacity': desired, 'response': _clean_response(response)}


def attach_instances(name: str, instance_ids: Any) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    ids = _string_list(instance_ids)
    if not ids:
        raise ValueError('At least one instance ID is required')
    response = _client().attach_instances(AutoScalingGroupName=clean_name, InstanceIds=ids)
    return {'name': clean_name, 'instance_ids': ids, 'response': _clean_response(response)}


def detach_instances(name: str, instance_ids: Any, *, decrement_desired_capacity: bool = False) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    ids = _string_list(instance_ids)
    if not ids:
        raise ValueError('At least one instance ID is required')
    response = _client().detach_instances(
        AutoScalingGroupName=clean_name,
        InstanceIds=ids,
        ShouldDecrementDesiredCapacity=bool(decrement_desired_capacity),
    )
    return {'name': clean_name, 'instance_ids': ids, 'response': _clean_response(response)}


def terminate_instance(instance_id: str, *, decrement_desired_capacity: bool = False) -> dict[str, Any]:
    clean_id = _required(instance_id, 'Instance ID')
    response = _client().terminate_instance_in_auto_scaling_group(
        InstanceId=clean_id,
        ShouldDecrementDesiredCapacity=bool(decrement_desired_capacity),
    )
    return {'instance_id': clean_id, 'response': _clean_response(response)}


def attach_target_groups(name: str, target_group_arns: Any) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    arns = _string_list(target_group_arns)
    if not arns:
        raise ValueError('At least one target group ARN is required')
    response = _client().attach_load_balancer_target_groups(AutoScalingGroupName=clean_name, TargetGroupARNs=arns)
    return {'name': clean_name, 'target_group_arns': arns, 'response': _clean_response(response)}


def detach_target_groups(name: str, target_group_arns: Any) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    arns = _string_list(target_group_arns)
    if not arns:
        raise ValueError('At least one target group ARN is required')
    response = _client().detach_load_balancer_target_groups(AutoScalingGroupName=clean_name, TargetGroupARNs=arns)
    return {'name': clean_name, 'target_group_arns': arns, 'response': _clean_response(response)}


def put_lifecycle_hook(
    name: str,
    hook_name: str,
    transition: str,
    *,
    default_result: str = 'ABANDON',
    heartbeat_timeout: Any = 3600,
) -> dict[str, Any]:
    response = _client().put_lifecycle_hook(
        AutoScalingGroupName=_required(name, 'Auto Scaling group name'),
        LifecycleHookName=_required(hook_name, 'Lifecycle hook name'),
        LifecycleTransition=_required(transition, 'Lifecycle transition'),
        DefaultResult=(default_result or 'ABANDON').strip().upper(),
        HeartbeatTimeout=_int_value(heartbeat_timeout, 'Heartbeat timeout', 3600),
    )
    return {'name': name, 'hook_name': hook_name, 'response': _clean_response(response)}


def delete_lifecycle_hook(name: str, hook_name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    clean_hook = _required(hook_name, 'Lifecycle hook name')
    response = _client().delete_lifecycle_hook(AutoScalingGroupName=clean_name, LifecycleHookName=clean_hook)
    return {'name': clean_name, 'hook_name': clean_hook, 'response': _clean_response(response)}


def put_scaling_policy(
    name: str,
    policy_name: str,
    *,
    adjustment_type: str = 'ChangeInCapacity',
    scaling_adjustment: Any = None,
    cooldown: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'AutoScalingGroupName': _required(name, 'Auto Scaling group name'),
        'PolicyName': _required(policy_name, 'Policy name'),
        'PolicyType': 'SimpleScaling',
        'AdjustmentType': (adjustment_type or 'ChangeInCapacity').strip(),
        # A simple scaling policy needs an adjustment; don't guess one.
        'ScalingAdjustment': _int_value(scaling_adjustment, 'Scaling adjustment'),
    }
    # Without Cooldown, AWS uses the group's default cooldown (300 seconds unless changed).
    if cooldown not in (None, ''):
        kwargs['Cooldown'] = _int_value(cooldown, 'Cooldown')
    response = _client().put_scaling_policy(**kwargs)
    return {'name': name, 'policy_name': policy_name, 'response': _clean_response(response)}


def delete_policy(name: str, policy_name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    clean_policy = _required(policy_name, 'Policy name')
    response = _client().delete_policy(AutoScalingGroupName=clean_name, PolicyName=clean_policy)
    return {'name': clean_name, 'policy_name': clean_policy, 'response': _clean_response(response)}


def start_instance_refresh(
    name: str,
    *,
    min_healthy_percentage: Any = None,
    instance_warmup: Any = None,
    skip_matching: bool = False,
) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    preferences: dict[str, Any] = {}
    if min_healthy_percentage not in (None, ''):
        percentage = _int_value(min_healthy_percentage, 'Minimum healthy percentage')
        if not 0 <= percentage <= 100:
            raise ValueError('Minimum healthy percentage must be from 0 to 100')
        preferences['MinHealthyPercentage'] = percentage
    if instance_warmup not in (None, ''):
        warmup = _int_value(instance_warmup, 'Instance warmup')
        if warmup < 0:
            raise ValueError('Instance warmup must be 0 or more')
        preferences['InstanceWarmup'] = warmup
    if skip_matching:
        preferences['SkipMatching'] = True
    kwargs: dict[str, Any] = {'AutoScalingGroupName': clean_name, 'Strategy': 'Rolling'}
    if preferences:
        kwargs['Preferences'] = preferences
    response = _client().start_instance_refresh(**kwargs)
    return {
        'name': clean_name,
        'instance_refresh_id': response.get('InstanceRefreshId'),
        'response': _clean_response(response),
    }


def describe_instance_refreshes(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Auto Scaling group name')
    response = _client().describe_instance_refreshes(AutoScalingGroupName=clean_name)
    return {'name': clean_name, 'instance_refreshes': _clean_response(response.get('InstanceRefreshes', []))}


def set_instance_health(instance_id: str, health_status: str, *, should_respect_grace_period: bool = False) -> dict[str, Any]:
    clean_id = _required(instance_id, 'Instance ID')
    status = (health_status or '').strip()
    if status not in ('Healthy', 'Unhealthy'):
        raise ValueError('Health status must be Healthy or Unhealthy')
    response = _client().set_instance_health(
        InstanceId=clean_id,
        HealthStatus=status,
        ShouldRespectGracePeriod=bool(should_respect_grace_period),
    )
    return {'instance_id': clean_id, 'health_status': status, 'response': _clean_response(response)}
