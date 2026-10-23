"""AWS Config inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def config_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    config = factory.client('config')
    operations = set(config.meta.service_model.operation_names)

    def items(operation: str, method: str, result_key: str, **kwargs) -> list[Any]:
        return _safe_value(
            lambda: core._operation_items(config, method, result_key, **kwargs),
            [],
        ) if operation in operations else []

    recorders = items('DescribeConfigurationRecorders', 'describe_configuration_recorders', 'ConfigurationRecorders')
    recorder_statuses = items(
        'DescribeConfigurationRecorderStatus',
        'describe_configuration_recorder_status',
        'ConfigurationRecordersStatus',
    )
    delivery_channels = items('DescribeDeliveryChannels', 'describe_delivery_channels', 'DeliveryChannels')
    delivery_channel_statuses = items(
        'DescribeDeliveryChannelStatus',
        'describe_delivery_channel_status',
        'DeliveryChannelsStatus',
    )
    config_rules = items('DescribeConfigRules', 'describe_config_rules', 'ConfigRules')
    conformance_packs = items(
        'DescribeConformancePacks',
        'describe_conformance_packs',
        'ConformancePackDetails',
    )
    aggregators = items(
        'DescribeConfigurationAggregators',
        'describe_configuration_aggregators',
        'ConfigurationAggregators',
    )
    # DescribeRemediationConfigurations requires ConfigRuleNames (at most 25 per call).
    rule_names = [rule.get('ConfigRuleName') for rule in config_rules if rule.get('ConfigRuleName')]
    remediation_configurations = [
        item
        for chunk in core._chunks(rule_names, 25)
        for item in items(
            'DescribeRemediationConfigurations',
            'describe_remediation_configurations',
            'RemediationConfigurations',
            ConfigRuleNames=chunk,
        )
    ]
    retention_configurations = items(
        'DescribeRetentionConfigurations',
        'describe_retention_configurations',
        'RetentionConfigurations',
    )
    compliance_by_rule = _safe_value(
        lambda: config.get_compliance_summary_by_config_rule(),
        {},
    ) if 'GetComplianceSummaryByConfigRule' in operations else {}
    compliance_by_resource_type = _safe_value(
        lambda: config.get_compliance_summary_by_resource_type(),
        {},
    ) if 'GetComplianceSummaryByResourceType' in operations else {}

    return {
        'summary': {
            'configuration_recorders': len(recorders),
            'delivery_channels': len(delivery_channels),
            'config_rules': len(config_rules),
            'conformance_packs': len(conformance_packs),
            'configuration_aggregators': len(aggregators),
            'remediation_configurations': len(remediation_configurations),
            'retention_configurations': len(retention_configurations),
            'available_sdk_operations': len(operations),
        },
        'configuration_recorders': _clean_response(recorders),
        'configuration_recorder_statuses': _clean_response(recorder_statuses),
        'delivery_channels': _clean_response(delivery_channels),
        'delivery_channel_statuses': _clean_response(delivery_channel_statuses),
        'config_rules': _clean_response(config_rules),
        'conformance_packs': _clean_response(conformance_packs),
        'configuration_aggregators': _clean_response(aggregators),
        'remediation_configurations': _clean_response(remediation_configurations),
        'retention_configurations': _clean_response(retention_configurations),
        'compliance_by_rule': _clean_response(compliance_by_rule),
        'compliance_by_resource_type': _clean_response(compliance_by_resource_type),
        'supported_from_sdk': [
            operation
            for operation in [
                'PutConfigurationRecorder',
                'DescribeConfigurationRecorders',
                'StartConfigurationRecorder',
                'StopConfigurationRecorder',
                'PutDeliveryChannel',
                'DescribeDeliveryChannels',
                'PutConfigRule',
                'DeleteConfigRule',
                'DescribeConfigRules',
                'DescribeComplianceByConfigRule',
                'DescribeConfigRuleEvaluationStatus',
                'StartConfigRulesEvaluation',
                'PutConformancePack',
                'DeleteConformancePack',
                'DescribeConformancePacks',
                'DescribeConformancePackStatus',
                'TagResource',
                'UntagResource',
                'ListTagsForResource',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'This page shows the management-plane inventory most useful for checking local compliance and recorder setup.',
            'Compliance status always returns INSUFFICIENT_DATA because Floci stores resources but does not perform actual evaluation.',
        ],
    }


def list_config_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    config = factory.client('config')
    operations = set(config.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'DescribeConfigurationRecorders' in operations:
        resources.extend(
            {
                'type': 'configuration_recorder',
                'name': recorder.get('name'),
                'role_arn': recorder.get('roleARN'),
            }
            for recorder in _safe_value(
                lambda: core._operation_items(config, 'describe_configuration_recorders', 'ConfigurationRecorders'),
                [],
            )
        )

    if 'DescribeConfigRules' in operations:
        resources.extend(
            {
                'type': 'config_rule',
                'name': rule.get('ConfigRuleName'),
                'arn': rule.get('ConfigRuleArn'),
                'state': rule.get('ConfigRuleState'),
            }
            for rule in _safe_value(
                lambda: core._operation_items(config, 'describe_config_rules', 'ConfigRules'),
                [],
            )
        )

    if 'DescribeConformancePacks' in operations:
        resources.extend(
            {
                'type': 'conformance_pack',
                'name': pack.get('ConformancePackName'),
                'arn': pack.get('ConformancePackArn'),
            }
            for pack in _safe_value(
                lambda: core._operation_items(config, 'describe_conformance_packs', 'ConformancePackDetails'),
                [],
            )
        )

    return resources
