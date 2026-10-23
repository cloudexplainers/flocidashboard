"""Elastic Load Balancing inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _chunks, _safe_value


def _elasticloadbalancing_tags(elbv2, arns: list[str]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    for chunk in _chunks([arn for arn in arns if arn], 20):
        response = _safe_value(lambda chunk=chunk: elbv2.describe_tags(ResourceArns=chunk).get('TagDescriptions', []), [])
        for description in response:
            tags[description.get('ResourceArn')] = description.get('Tags', [])
    return tags


def _classic_elb_tags(elb, names: list[str]) -> dict[str, Any]:
    tags: dict[str, Any] = {}
    for chunk in _chunks([name for name in names if name], 20):
        response = _safe_value(lambda chunk=chunk: elb.describe_tags(LoadBalancerNames=chunk).get('TagDescriptions', []), [])
        for description in response:
            tags[description.get('LoadBalancerName')] = description.get('Tags', [])
    return tags


def elasticloadbalancing_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    elbv2 = factory.client('elbv2')
    elb = factory.client('elb')

    v2_load_balancers = _safe_value(lambda: core._paginate(elbv2, 'describe_load_balancers', 'LoadBalancers'), [])
    v2_target_groups = _safe_value(lambda: core._paginate(elbv2, 'describe_target_groups', 'TargetGroups'), [])
    classic_load_balancers = _safe_value(lambda: core._paginate(elb, 'describe_load_balancers', 'LoadBalancerDescriptions'), [])
    classic_policy_types = _safe_value(lambda: elb.describe_load_balancer_policy_types().get('PolicyTypeDescriptions', []), [])

    v2_tags = _elasticloadbalancing_tags(elbv2, [lb.get('LoadBalancerArn') for lb in v2_load_balancers])
    target_group_tags = _elasticloadbalancing_tags(elbv2, [group.get('TargetGroupArn') for group in v2_target_groups])
    classic_tags = _classic_elb_tags(elb, [lb.get('LoadBalancerName') for lb in classic_load_balancers])

    listeners: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for load_balancer in v2_load_balancers:
        lb_arn = load_balancer.get('LoadBalancerArn')
        lb_listeners = _safe_value(lambda lb_arn=lb_arn: core._paginate(elbv2, 'describe_listeners', 'Listeners', LoadBalancerArn=lb_arn), [])
        for listener in lb_listeners:
            listener['LoadBalancerName'] = load_balancer.get('LoadBalancerName')
            listeners.append(listener)
            listener_arn = listener.get('ListenerArn')
            listener_rules = _safe_value(lambda listener_arn=listener_arn: core._paginate(elbv2, 'describe_rules', 'Rules', ListenerArn=listener_arn), [])
            for rule in listener_rules:
                rule['LoadBalancerName'] = load_balancer.get('LoadBalancerName')
                rule['ListenerArn'] = listener_arn
                rules.append(rule)

    target_group_attributes: dict[str, dict[str, str]] = {}
    for target_group in v2_target_groups:
        arn = target_group.get('TargetGroupArn')
        response = _safe_value(lambda arn=arn: elbv2.describe_target_group_attributes(TargetGroupArn=arn).get('Attributes', []), [])
        target_group_attributes[arn] = {item.get('Key'): item.get('Value') for item in response if item.get('Key')}

    target_health = []
    for target_group in v2_target_groups:
        arn = target_group.get('TargetGroupArn')
        response = _safe_value(lambda arn=arn: elbv2.describe_target_health(TargetGroupArn=arn).get('TargetHealthDescriptions', []), [])
        target_health.append({
            'name': target_group.get('TargetGroupName'),
            'target_group_arn': arn,
            'target_health_descriptions': response,
        })

    classic_instance_health = []
    classic_policies = []
    for load_balancer in classic_load_balancers:
        name = load_balancer.get('LoadBalancerName')
        classic_instance_health.append({
            'name': name,
            'instance_states': _safe_value(lambda name=name: elb.describe_instance_health(LoadBalancerName=name).get('InstanceStates', []), []),
        })
        classic_policies.append({
            'name': name,
            'policies': _safe_value(lambda name=name: elb.describe_load_balancer_policies(LoadBalancerName=name).get('PolicyDescriptions', []), []),
        })

    return {
        'summary': {
            'v2_load_balancers': len(v2_load_balancers),
            'classic_load_balancers': len(classic_load_balancers),
            'listeners': len(listeners),
            'rules': len(rules),
            'target_groups': len(v2_target_groups),
            'target_health_sets': len(target_health),
            'classic_policy_types': len(classic_policy_types),
        },
        'v2_load_balancers': [
            {
                'name': lb.get('LoadBalancerName'),
                'arn': lb.get('LoadBalancerArn'),
                'dns_name': lb.get('DNSName'),
                'canonical_hosted_zone_id': lb.get('CanonicalHostedZoneId'),
                'created': lb.get('CreatedTime'),
                'scheme': lb.get('Scheme'),
                'vpc_id': lb.get('VpcId'),
                'state': lb.get('State'),
                'type': lb.get('Type'),
                'availability_zones': lb.get('AvailabilityZones'),
                'security_groups': lb.get('SecurityGroups'),
                'ip_address_type': lb.get('IpAddressType'),
                'customer_owned_ipv4_pool': lb.get('CustomerOwnedIpv4Pool'),
                'enforce_security_group_inbound_rules_on_private_link_traffic': lb.get('EnforceSecurityGroupInboundRulesOnPrivateLinkTraffic'),
                'attributes': _safe_value(lambda arn=lb.get('LoadBalancerArn'): elbv2.describe_load_balancer_attributes(LoadBalancerArn=arn).get('Attributes', []), []),
                'tags': v2_tags.get(lb.get('LoadBalancerArn'), []),
            }
            for lb in v2_load_balancers
        ],
        'listeners': [
            {
                'name': listener.get('ListenerArn'),
                'load_balancer_name': listener.get('LoadBalancerName'),
                'arn': listener.get('ListenerArn'),
                'port': listener.get('Port'),
                'protocol': listener.get('Protocol'),
                'certificates': listener.get('Certificates'),
                'ssl_policy': listener.get('SslPolicy'),
                'default_actions': listener.get('DefaultActions'),
                'alpn_policy': listener.get('AlpnPolicy'),
                'mutual_authentication': listener.get('MutualAuthentication'),
            }
            for listener in listeners
        ],
        'rules': [
            {
                'name': rule.get('RuleArn'),
                'load_balancer_name': rule.get('LoadBalancerName'),
                'listener_arn': rule.get('ListenerArn'),
                'arn': rule.get('RuleArn'),
                'priority': rule.get('Priority'),
                'conditions': rule.get('Conditions'),
                'actions': rule.get('Actions'),
                'is_default': rule.get('IsDefault'),
            }
            for rule in rules
        ],
        'target_groups': [
            {
                'name': group.get('TargetGroupName'),
                'arn': group.get('TargetGroupArn'),
                'protocol': group.get('Protocol'),
                'port': group.get('Port'),
                'vpc_id': group.get('VpcId'),
                'health_check_protocol': group.get('HealthCheckProtocol'),
                'health_check_port': group.get('HealthCheckPort'),
                'health_check_enabled': group.get('HealthCheckEnabled'),
                'health_check_interval_seconds': group.get('HealthCheckIntervalSeconds'),
                'health_check_timeout_seconds': group.get('HealthCheckTimeoutSeconds'),
                'healthy_threshold_count': group.get('HealthyThresholdCount'),
                'unhealthy_threshold_count': group.get('UnhealthyThresholdCount'),
                'health_check_path': group.get('HealthCheckPath'),
                'matcher': group.get('Matcher'),
                'load_balancer_arns': group.get('LoadBalancerArns'),
                'target_type': group.get('TargetType'),
                'protocol_version': group.get('ProtocolVersion'),
                'ip_address_type': group.get('IpAddressType'),
                'attributes': target_group_attributes.get(group.get('TargetGroupArn'), {}),
                'tags': target_group_tags.get(group.get('TargetGroupArn'), []),
            }
            for group in v2_target_groups
        ],
        'target_health': target_health,
        'classic_load_balancers': [
            {
                'name': lb.get('LoadBalancerName'),
                'dns_name': lb.get('DNSName'),
                'canonical_hosted_zone_name': lb.get('CanonicalHostedZoneName'),
                'canonical_hosted_zone_name_id': lb.get('CanonicalHostedZoneNameID'),
                'listener_descriptions': lb.get('ListenerDescriptions'),
                'policies': lb.get('Policies'),
                'backend_server_descriptions': lb.get('BackendServerDescriptions'),
                'availability_zones': lb.get('AvailabilityZones'),
                'subnets': lb.get('Subnets'),
                'vpc_id': lb.get('VPCId'),
                'instances': lb.get('Instances'),
                'health_check': lb.get('HealthCheck'),
                'source_security_group': lb.get('SourceSecurityGroup'),
                'security_groups': lb.get('SecurityGroups'),
                'created': lb.get('CreatedTime'),
                'scheme': lb.get('Scheme'),
                'attributes': _safe_value(lambda name=lb.get('LoadBalancerName'): elb.describe_load_balancer_attributes(LoadBalancerName=name).get('LoadBalancerAttributes', {}), {}),
                'tags': classic_tags.get(lb.get('LoadBalancerName'), []),
            }
            for lb in classic_load_balancers
        ],
        'classic_instance_health': classic_instance_health,
        'classic_policies': classic_policies,
        'classic_policy_types': classic_policy_types,
        'supported_from_sdk': [
            'CreateLoadBalancer',
            'DescribeLoadBalancers',
            'DeleteLoadBalancer',
            'CreateListener',
            'DescribeListeners',
            'DescribeListenerAttributes',
            'ModifyListenerAttributes',
            'ModifyCapacityReservation',
            'DescribeCapacityReservation',
            'ModifyListener',
            'CreateRule',
            'DescribeRules',
            'ModifyRule',
            'SetRulePriorities',
            'CreateTargetGroup',
            'DescribeTargetGroups',
            'ModifyTargetGroup',
            'DescribeTargetGroupAttributes',
            'ModifyTargetGroupAttributes',
            'RegisterTargets',
            'DescribeTargetHealth',
            'DeregisterTargets',
            'AddTags',
            'DescribeTags',
            'RemoveTags',
            'CreateLoadBalancerListeners',
            'DescribeInstanceHealth',
            'CreateLoadBalancerPolicy',
            'DescribeLoadBalancerPolicies',
        ],
        'notes': [
            'Application Load Balancers serve real traffic: listener ports, rules and health checks apply.',
            'Classic Load Balancers are control plane only: listeners are recorded but Floci serves no traffic for them, and load balancer policies are not supported.',
        ],
    }


def list_elasticloadbalancing_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    elbv2 = factory.client('elbv2')
    elb = factory.client('elb')
    resources: list[dict[str, Any]] = []
    v2_load_balancers = _safe_value(lambda: core._paginate(elbv2, 'describe_load_balancers', 'LoadBalancers'), [])
    target_groups = _safe_value(lambda: core._paginate(elbv2, 'describe_target_groups', 'TargetGroups'), [])
    classic_load_balancers = _safe_value(lambda: core._paginate(elb, 'describe_load_balancers', 'LoadBalancerDescriptions'), [])

    resources.extend(
        {
            'type': 'v2_load_balancer',
            'name': load_balancer.get('LoadBalancerName'),
            'arn': load_balancer.get('LoadBalancerArn'),
            'dns_name': load_balancer.get('DNSName'),
            'state': load_balancer.get('State'),
        }
        for load_balancer in v2_load_balancers
    )
    for load_balancer in v2_load_balancers:
        arn = load_balancer.get('LoadBalancerArn')
        resources.extend(
            {
                'type': 'listener',
                'load_balancer': load_balancer.get('LoadBalancerName'),
                'arn': listener.get('ListenerArn'),
                'protocol': listener.get('Protocol'),
                'port': listener.get('Port'),
            }
            for listener in _safe_value(
                lambda arn=arn: core._paginate(elbv2, 'describe_listeners', 'Listeners', LoadBalancerArn=arn),
                [],
            )
        )
    resources.extend(
        {
            'type': 'target_group',
            'name': group.get('TargetGroupName'),
            'arn': group.get('TargetGroupArn'),
            'protocol': group.get('Protocol'),
            'port': group.get('Port'),
        }
        for group in target_groups
    )
    resources.extend(
        {
            'type': 'classic_load_balancer',
            'name': load_balancer.get('LoadBalancerName'),
            'dns_name': load_balancer.get('DNSName'),
            'scheme': load_balancer.get('Scheme'),
        }
        for load_balancer in classic_load_balancers
    )
    return resources
