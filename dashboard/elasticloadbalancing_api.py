"""Interactive ELBv2 helpers for local load-balancing workflows."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('elbv2')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _int_value(value: Any, label: str, default: int | None = None) -> int:
    if value in (None, '') and default is not None:
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


def _tags(value: Any) -> list[dict[str, str]]:
    if value in (None, '', []):
        return []
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item} for key, item in value.items()]
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
        tags.append({'Key': str(key), 'Value': '' if tag_value is None else str(tag_value)})
    return tags


def _targets(value: Any) -> list[dict[str, Any]]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        items = _string_list(value)
        targets = []
        for item in items:
            target_id, _, port = item.partition(':')
            target = {'Id': _required(target_id, 'Target ID')}
            if port:
                target['Port'] = _int_value(port, 'Target port')
            targets.append(target)
        return targets
    if not isinstance(value, list):
        raise ValueError('Targets must be a list or comma-separated string')
    targets = []
    for item in value:
        if isinstance(item, str):
            targets.extend(_targets(item))
            continue
        if not isinstance(item, dict):
            raise ValueError('Each target must be an object')
        target_id = item.get('Id') or item.get('id')
        target = {'Id': _required(target_id, 'Target ID')}
        port = item.get('Port') if item.get('Port') is not None else item.get('port')
        if port not in (None, ''):
            target['Port'] = _int_value(port, 'Target port')
        targets.append(target)
    return targets


def create_load_balancer(
    name: str,
    *,
    lb_type: str = 'application',
    scheme: str = 'internet-facing',
    subnets: Any = None,
    security_groups: Any = None,
    ip_address_type: str = '',
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'Name': _required(name, 'Load balancer name'),
        'Type': (lb_type or 'application').strip(),
        'Scheme': (scheme or 'internet-facing').strip(),
    }
    parsed_subnets = _string_list(subnets)
    if parsed_subnets:
        kwargs['Subnets'] = parsed_subnets
    parsed_groups = _string_list(security_groups)
    if parsed_groups:
        kwargs['SecurityGroups'] = parsed_groups
    if ip_address_type:
        kwargs['IpAddressType'] = ip_address_type
    response = _client().create_load_balancer(**kwargs)
    return {'load_balancers': _clean_response(response.get('LoadBalancers', [])), 'response': _clean_response(response)}


def delete_load_balancer(load_balancer_arn: str) -> dict[str, Any]:
    arn = _required(load_balancer_arn, 'Load balancer ARN')
    response = _client().delete_load_balancer(LoadBalancerArn=arn)
    return {'load_balancer_arn': arn, 'response': _clean_response(response)}


def create_target_group(
    name: str,
    protocol: str,
    port: Any,
    *,
    target_type: str = 'instance',
    vpc_id: str = '',
    health_check_path: str = '',
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'Name': _required(name, 'Target group name'),
        'Protocol': _required(protocol, 'Protocol').upper(),
        'Port': _int_value(port, 'Port'),
        'TargetType': (target_type or 'instance').strip(),
    }
    if vpc_id:
        kwargs['VpcId'] = vpc_id
    if health_check_path:
        kwargs['HealthCheckPath'] = health_check_path
    response = _client().create_target_group(**kwargs)
    return {'target_groups': _clean_response(response.get('TargetGroups', [])), 'response': _clean_response(response)}


def delete_target_group(target_group_arn: str) -> dict[str, Any]:
    arn = _required(target_group_arn, 'Target group ARN')
    response = _client().delete_target_group(TargetGroupArn=arn)
    return {'target_group_arn': arn, 'response': _clean_response(response)}


def register_targets(target_group_arn: str, targets: Any) -> dict[str, Any]:
    arn = _required(target_group_arn, 'Target group ARN')
    parsed_targets = _targets(targets)
    if not parsed_targets:
        raise ValueError('At least one target is required')
    response = _client().register_targets(TargetGroupArn=arn, Targets=parsed_targets)
    return {'target_group_arn': arn, 'targets': parsed_targets, 'response': _clean_response(response)}


def deregister_targets(target_group_arn: str, targets: Any) -> dict[str, Any]:
    arn = _required(target_group_arn, 'Target group ARN')
    parsed_targets = _targets(targets)
    if not parsed_targets:
        raise ValueError('At least one target is required')
    response = _client().deregister_targets(TargetGroupArn=arn, Targets=parsed_targets)
    return {'target_group_arn': arn, 'targets': parsed_targets, 'response': _clean_response(response)}


def create_listener(load_balancer_arn: str, protocol: str, port: Any, target_group_arn: str) -> dict[str, Any]:
    response = _client().create_listener(
        LoadBalancerArn=_required(load_balancer_arn, 'Load balancer ARN'),
        Protocol=_required(protocol, 'Protocol').upper(),
        Port=_int_value(port, 'Port'),
        DefaultActions=[{'Type': 'forward', 'TargetGroupArn': _required(target_group_arn, 'Target group ARN')}],
    )
    return {'listeners': _clean_response(response.get('Listeners', [])), 'response': _clean_response(response)}


def delete_listener(listener_arn: str) -> dict[str, Any]:
    arn = _required(listener_arn, 'Listener ARN')
    response = _client().delete_listener(ListenerArn=arn)
    return {'listener_arn': arn, 'response': _clean_response(response)}


def create_rule(listener_arn: str, priority: Any, path_pattern: str, target_group_arn: str) -> dict[str, Any]:
    response = _client().create_rule(
        ListenerArn=_required(listener_arn, 'Listener ARN'),
        Priority=_int_value(priority, 'Priority'),
        Conditions=[{'Field': 'path-pattern', 'Values': [_required(path_pattern, 'Path pattern')]}],
        Actions=[{'Type': 'forward', 'TargetGroupArn': _required(target_group_arn, 'Target group ARN')}],
    )
    return {'rules': _clean_response(response.get('Rules', [])), 'response': _clean_response(response)}


def delete_rule(rule_arn: str) -> dict[str, Any]:
    arn = _required(rule_arn, 'Rule ARN')
    response = _client().delete_rule(RuleArn=arn)
    return {'rule_arn': arn, 'response': _clean_response(response)}


def add_tags(resource_arns: Any, tags: Any) -> dict[str, Any]:
    arns = _string_list(resource_arns)
    parsed_tags = _tags(tags)
    if not arns:
        raise ValueError('At least one resource ARN is required')
    if not parsed_tags:
        raise ValueError('At least one tag is required')
    response = _client().add_tags(ResourceArns=arns, Tags=parsed_tags)
    return {'resource_arns': arns, 'tags': parsed_tags, 'response': _clean_response(response)}


def remove_tags(resource_arns: Any, tag_keys: Any) -> dict[str, Any]:
    arns = _string_list(resource_arns)
    keys = _string_list(tag_keys)
    if not arns:
        raise ValueError('At least one resource ARN is required')
    if not keys:
        raise ValueError('At least one tag key is required')
    response = _client().remove_tags(ResourceArns=arns, TagKeys=keys)
    return {'resource_arns': arns, 'tag_keys': keys, 'response': _clean_response(response)}


# AWS limits for the target group settings the console edits. Floci stores any value it is
# given, so the dashboard checks them itself rather than saving a setting AWS would refuse.
STICKINESS_TYPES = ('lb_cookie', 'app_cookie')
STICKINESS_DURATION_RANGE = (1, 604800)
DEREGISTRATION_DELAY_RANGE = (0, 3600)
HEALTH_CHECK_INTERVAL_RANGE = (5, 300)
HEALTH_CHECK_TIMEOUT_RANGE = (2, 120)
HEALTH_CHECK_THRESHOLD_RANGE = (2, 10)


def _ranged(value: Any, label: str, bounds: tuple[int, int]) -> int:
    number = _int_value(value, label)
    low, high = bounds
    if not low <= number <= high:
        raise ValueError(f'{label} must be from {low} to {high}')
    return number


def _bool_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() == 'true'
    return value is True


def _attributes_dict(response: dict[str, Any]) -> dict[str, str]:
    return {item.get('Key'): item.get('Value') for item in response.get('Attributes', []) if item.get('Key')}


def describe_target_group_attributes(target_group_arn: str) -> dict[str, Any]:
    arn = _required(target_group_arn, 'Target group ARN')
    response = _client().describe_target_group_attributes(TargetGroupArn=arn)
    return {'target_group_arn': arn, 'attributes': _attributes_dict(response)}


def modify_target_group_attributes(
    target_group_arn: str,
    *,
    stickiness_enabled: Any = None,
    stickiness_type: str = '',
    stickiness_duration_seconds: Any = None,
    app_cookie_name: str = '',
    deregistration_delay_seconds: Any = None,
) -> dict[str, Any]:
    """Saves stickiness and deregistration delay, as the console's Edit attributes does."""
    arn = _required(target_group_arn, 'Target group ARN')
    attributes: list[dict[str, str]] = []
    if deregistration_delay_seconds not in (None, ''):
        delay = _ranged(deregistration_delay_seconds, 'Deregistration delay', DEREGISTRATION_DELAY_RANGE)
        attributes.append({'Key': 'deregistration_delay.timeout_seconds', 'Value': str(delay)})
    if stickiness_enabled is not None:
        enabled = _bool_value(stickiness_enabled)
        attributes.append({'Key': 'stickiness.enabled', 'Value': 'true' if enabled else 'false'})
        if enabled:
            kind = (stickiness_type or 'lb_cookie').strip()
            if kind not in STICKINESS_TYPES:
                raise ValueError('Stickiness type must be lb_cookie or app_cookie')
            attributes.append({'Key': 'stickiness.type', 'Value': kind})
            duration = _ranged(
                stickiness_duration_seconds if stickiness_duration_seconds not in (None, '') else 86400,
                'Stickiness duration',
                STICKINESS_DURATION_RANGE,
            )
            attributes.append({'Key': f'stickiness.{kind}.duration_seconds', 'Value': str(duration)})
            if kind == 'app_cookie':
                cookie = _required(app_cookie_name, 'App cookie name')
                if cookie.upper().startswith(('AWSALB', 'AWSALBAPP', 'AWSALBTG')):
                    raise ValueError('App cookie names starting with AWSALB, AWSALBAPP or AWSALBTG are reserved')
                attributes.append({'Key': 'stickiness.app_cookie.cookie_name', 'Value': cookie})
    if not attributes:
        raise ValueError('At least one attribute must be changed')
    response = _client().modify_target_group_attributes(TargetGroupArn=arn, Attributes=attributes)
    return {'target_group_arn': arn, 'attributes': _attributes_dict(response), 'response': _clean_response(response)}


def modify_target_group_health_check(
    target_group_arn: str,
    *,
    path: str = '',
    interval_seconds: Any = None,
    timeout_seconds: Any = None,
    healthy_threshold: Any = None,
    unhealthy_threshold: Any = None,
    matcher: str = '',
) -> dict[str, Any]:
    """ModifyTargetGroup with only the health check settings the console's Edit health check has."""
    arn = _required(target_group_arn, 'Target group ARN')
    kwargs: dict[str, Any] = {'TargetGroupArn': arn}
    if path:
        cleaned = path.strip()
        if not cleaned.startswith('/'):
            raise ValueError('Health check path must start with /')
        kwargs['HealthCheckPath'] = cleaned
    if interval_seconds not in (None, ''):
        kwargs['HealthCheckIntervalSeconds'] = _ranged(interval_seconds, 'Interval', HEALTH_CHECK_INTERVAL_RANGE)
    if timeout_seconds not in (None, ''):
        kwargs['HealthCheckTimeoutSeconds'] = _ranged(timeout_seconds, 'Timeout', HEALTH_CHECK_TIMEOUT_RANGE)
    if healthy_threshold not in (None, ''):
        kwargs['HealthyThresholdCount'] = _ranged(healthy_threshold, 'Healthy threshold', HEALTH_CHECK_THRESHOLD_RANGE)
    if unhealthy_threshold not in (None, ''):
        kwargs['UnhealthyThresholdCount'] = _ranged(unhealthy_threshold, 'Unhealthy threshold', HEALTH_CHECK_THRESHOLD_RANGE)
    if matcher:
        kwargs['Matcher'] = {'HttpCode': matcher.strip()}
    interval = kwargs.get('HealthCheckIntervalSeconds')
    timeout = kwargs.get('HealthCheckTimeoutSeconds')
    if interval is not None and timeout is not None and timeout >= interval:
        raise ValueError('Timeout must be shorter than the interval')
    if len(kwargs) == 1:
        raise ValueError('At least one health check setting must be changed')
    response = _client().modify_target_group(**kwargs)
    return {'target_group_arn': arn, 'target_groups': _clean_response(response.get('TargetGroups', [])), 'response': _clean_response(response)}


def _default_action(action_type: str, target_group_arn: str, status_code: Any, message_body: str) -> dict[str, Any]:
    kind = (action_type or 'forward').strip()
    if kind == 'forward':
        return {'Type': 'forward', 'TargetGroupArn': _required(target_group_arn, 'Target group ARN')}
    if kind == 'fixed-response':
        code = str(_int_value(status_code, 'Response code', 200))
        if not (len(code) == 3 and code[0] in '245'):
            raise ValueError('Response code must be a 2XX, 4XX or 5XX code')
        config: dict[str, Any] = {'StatusCode': code, 'ContentType': 'text/plain'}
        if message_body:
            config['MessageBody'] = message_body
        return {'Type': 'fixed-response', 'FixedResponseConfig': config}
    raise ValueError('Action type must be forward or fixed-response')


def modify_listener(
    listener_arn: str,
    *,
    port: Any = None,
    action_type: str = '',
    target_group_arn: str = '',
    status_code: Any = None,
    message_body: str = '',
) -> dict[str, Any]:
    """Changes a listener's port and default action. The protocol is left as it is."""
    arn = _required(listener_arn, 'Listener ARN')
    kwargs: dict[str, Any] = {'ListenerArn': arn}
    if port not in (None, ''):
        kwargs['Port'] = _ranged(port, 'Port', (1, 65535))
    if action_type:
        kwargs['DefaultActions'] = [_default_action(action_type, target_group_arn, status_code, message_body)]
    if len(kwargs) == 1:
        raise ValueError('Change the port or the default action')
    response = _client().modify_listener(**kwargs)
    return {'listeners': _clean_response(response.get('Listeners', [])), 'response': _clean_response(response)}


def modify_rule(
    rule_arn: str,
    *,
    path_pattern: str = '',
    target_group_arn: str = '',
    priority: Any = None,
) -> dict[str, Any]:
    """Changes a rule's path condition, its forward target and (through SetRulePriorities) its priority."""
    arn = _required(rule_arn, 'Rule ARN')
    client = _client()
    kwargs: dict[str, Any] = {'RuleArn': arn}
    if path_pattern:
        kwargs['Conditions'] = [{'Field': 'path-pattern', 'Values': [path_pattern.strip()]}]
    if target_group_arn:
        kwargs['Actions'] = [{'Type': 'forward', 'TargetGroupArn': target_group_arn.strip()}]
    new_priority = None if priority in (None, '') else _ranged(priority, 'Priority', (1, 50000))
    if len(kwargs) == 1 and new_priority is None:
        raise ValueError('Change the path, the target group or the priority')
    result: dict[str, Any] = {'rule_arn': arn}
    if len(kwargs) > 1:
        response = client.modify_rule(**kwargs)
        result['rules'] = _clean_response(response.get('Rules', []))
    if new_priority is not None:
        response = client.set_rule_priorities(RulePriorities=[{'RuleArn': arn, 'Priority': new_priority}])
        result['rules'] = _clean_response(response.get('Rules', []))
    return result
