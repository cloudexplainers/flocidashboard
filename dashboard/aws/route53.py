"""Route 53 inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def route53_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    route53 = factory.client('route53')
    hosted_zones = _safe_value(lambda: core._paginate(route53, 'list_hosted_zones', 'HostedZones'), [])
    health_checks = _safe_value(lambda: core._paginate(route53, 'list_health_checks', 'HealthChecks'), [])
    traffic_policies = _safe_value(lambda: route53.list_traffic_policies().get('TrafficPolicySummaries', []), [])
    traffic_policy_instances = _safe_value(
        lambda: route53.list_traffic_policy_instances().get('TrafficPolicyInstances', []),
        [],
    )
    delegation_sets = _safe_value(
        lambda: route53.list_reusable_delegation_sets().get('DelegationSets', []),
        [],
    )

    def zone_detail(zone: dict[str, Any]) -> dict[str, Any]:
        zone_id = zone.get('Id')
        clean_zone_id = zone_id.rsplit('/', 1)[-1] if zone_id else None
        details = _safe_value(lambda: route53.get_hosted_zone(Id=zone_id), {}) if zone_id else {}
        records = _safe_value(
            lambda: core._paginate(route53, 'list_resource_record_sets', 'ResourceRecordSets', HostedZoneId=zone_id),
            [],
        ) if zone_id else []
        query_logging_configs = _safe_value(
            lambda: route53.list_query_logging_configs(HostedZoneId=clean_zone_id).get('QueryLoggingConfigs', []),
            [],
        ) if clean_zone_id else []

        return {
            'name': zone.get('Name'),
            'id': zone_id,
            'clean_id': clean_zone_id,
            'caller_reference': zone.get('CallerReference'),
            'private_zone': zone.get('Config', {}).get('PrivateZone'),
            'comment': zone.get('Config', {}).get('Comment'),
            'resource_record_set_count': zone.get('ResourceRecordSetCount'),
            'delegation_set': details.get('DelegationSet'),
            'vpcs': details.get('VPCs'),
            'query_logging_configs': query_logging_configs,
            'record_count': len(records),
            'records': _clean_response(records),
        }

    detailed_zones = [zone_detail(zone) for zone in hosted_zones]

    return {
        'summary': {
            'hosted_zones': len(detailed_zones),
            'record_sets': sum(zone.get('record_count') or 0 for zone in detailed_zones),
            'health_checks': len(health_checks),
            'traffic_policies': len(traffic_policies),
            'traffic_policy_instances': len(traffic_policy_instances),
            'delegation_sets': len(delegation_sets),
            'private_zones': sum(1 for zone in detailed_zones if zone.get('private_zone')),
        },
        'hosted_zones': detailed_zones,
        'health_checks': _clean_response(health_checks),
        'traffic_policies': _clean_response(traffic_policies),
        'traffic_policy_instances': _clean_response(traffic_policy_instances),
        'delegation_sets': _clean_response(delegation_sets),
        'supported': [
            'CreateHostedZone',
            'GetHostedZone',
            'ListHostedZones',
            'DeleteHostedZone',
            'ChangeResourceRecordSets',
            'ListResourceRecordSets',
            'ListHealthChecks',
            'CreateHealthCheck',
            'DeleteHealthCheck',
            'ListTrafficPolicies',
            'ListTrafficPolicyInstances',
            'ListReusableDelegationSets',
        ],
        'notes': [],
    }


def list_route53_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    route53 = factory.client('route53')
    resources = [
        {
            'type': 'hosted_zone',
            'name': zone.get('Name'),
            'id': zone.get('Id'),
            'private_zone': zone.get('Config', {}).get('PrivateZone'),
            'record_sets': zone.get('ResourceRecordSetCount'),
        }
        for zone in _safe_value(lambda: core._paginate(route53, 'list_hosted_zones', 'HostedZones'), [])
    ]
    resources.extend(
        {
            'type': 'health_check',
            'name': check.get('Id'),
            'id': check.get('Id'),
            'health_check_version': check.get('HealthCheckVersion'),
            'config': check.get('HealthCheckConfig'),
        }
        for check in _safe_value(lambda: core._paginate(route53, 'list_health_checks', 'HealthChecks'), [])
    )
    resources.extend(
        {
            'type': 'traffic_policy',
            'name': policy.get('Name'),
            'id': policy.get('Id'),
            'policy_type': policy.get('Type'),
            'latest_version': policy.get('LatestVersion'),
        }
        for policy in _safe_value(lambda: route53.list_traffic_policies().get('TrafficPolicySummaries', []), [])
    )
    return resources
