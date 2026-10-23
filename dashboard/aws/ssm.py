"""SSM inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def ssm_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    ssm = factory.client('ssm')

    parameters = _safe_value(lambda: core._paginate(ssm, 'describe_parameters', 'Parameters'), [])
    documents = _safe_value(lambda: core._paginate(ssm, 'list_documents', 'DocumentIdentifiers'), [])
    instances = _safe_value(lambda: core._paginate(ssm, 'describe_instance_information', 'InstanceInformationList'), [])
    sessions = _safe_value(lambda: core._paginate(ssm, 'describe_sessions', 'Sessions', State='History'), [])
    automation_executions = _safe_value(lambda: core._paginate(ssm, 'describe_automation_executions', 'AutomationExecutionMetadataList'), [])
    maintenance_windows = _safe_value(lambda: core._paginate(ssm, 'describe_maintenance_windows', 'WindowIdentities'), [])
    patch_baselines = _safe_value(lambda: core._paginate(ssm, 'describe_patch_baselines', 'BaselineIdentities'), [])
    default_patch_baselines = [
        {
            'name': operating_system,
            'operating_system': operating_system,
            'baseline_id': response.get('BaselineId'),
        }
        for operating_system in [
            'AMAZON_LINUX',
            'AMAZON_LINUX_2',
            'AMAZON_LINUX_2022',
            'AMAZON_LINUX_2023',
            'CENTOS',
            'DEBIAN',
            'MACOS',
            'ORACLE_LINUX',
            'RASPBIAN',
            'REDHAT_ENTERPRISE_LINUX',
            'ROCKY_LINUX',
            'SUSE',
            'UBUNTU',
            'WINDOWS',
        ]
        for response in [_safe_value(lambda operating_system=operating_system: ssm.get_default_patch_baseline(OperatingSystem=operating_system), {})]
        if response.get('BaselineId')
    ]
    associations = _safe_value(lambda: core._paginate(ssm, 'list_associations', 'Associations'), [])
    commands = _safe_value(lambda: core._paginate(ssm, 'list_commands', 'Commands'), [])
    command_invocations = _safe_value(lambda: core._paginate(ssm, 'list_command_invocations', 'CommandInvocations', Details=True), [])
    compliance_summaries = _safe_value(lambda: core._paginate(ssm, 'list_compliance_summaries', 'ComplianceSummaryItems'), [])
    resource_compliance_summaries = _safe_value(lambda: core._paginate(ssm, 'list_resource_compliance_summaries', 'ResourceComplianceSummaryItems'), [])
    data_syncs = _safe_value(lambda: core._paginate(ssm, 'list_resource_data_sync', 'ResourceDataSyncItems'), [])
    ops_items = _safe_value(lambda: core._paginate(ssm, 'describe_ops_items', 'OpsItemSummaries'), [])
    ops_metadata = _safe_value(lambda: core._paginate(ssm, 'list_ops_metadata', 'OpsMetadataList'), [])

    def parameter_detail(parameter: dict[str, Any]) -> dict[str, Any]:
        name = parameter.get('Name')
        tags = _safe_value(lambda name=name: ssm.list_tags_for_resource(ResourceType='Parameter', ResourceId=name).get('TagList', []), []) if name else []
        return {
            'name': name,
            'type': parameter.get('Type'),
            'key_id': parameter.get('KeyId'),
            'last_modified': parameter.get('LastModifiedDate'),
            'last_modified_user': parameter.get('LastModifiedUser'),
            'description': parameter.get('Description'),
            'version': parameter.get('Version'),
            'tier': parameter.get('Tier'),
            'policies': parameter.get('Policies'),
            'data_type': parameter.get('DataType'),
            'tags': tags,
        }

    def document_detail(document: dict[str, Any]) -> dict[str, Any]:
        name = document.get('Name')
        detail = _safe_value(lambda name=name: _clean_response(ssm.describe_document(Name=name).get('Document', {})), {}) if name else {}
        return {
            'name': name,
            'owner': detail.get('Owner') or document.get('Owner'),
            'platform_types': detail.get('PlatformTypes') or document.get('PlatformTypes'),
            'document_version': detail.get('DocumentVersion') or document.get('DocumentVersion'),
            'document_type': detail.get('DocumentType') or document.get('DocumentType'),
            'schema_version': detail.get('SchemaVersion') or document.get('SchemaVersion'),
            'document_format': detail.get('DocumentFormat') or document.get('DocumentFormat'),
            'target_type': detail.get('TargetType') or document.get('TargetType'),
            'status': detail.get('Status'),
            'created': detail.get('CreatedDate') or document.get('CreatedDate'),
            'description': detail.get('Description'),
            'parameters': detail.get('Parameters'),
            'tags': detail.get('Tags'),
        }

    parameter_details = [parameter_detail(parameter) for parameter in parameters]
    document_details = [document_detail(document) for document in documents[:50]]
    core.record_truncation('DescribeDocument', 'documents', len(document_details), len(documents))
    parameter_types: dict[str, int] = {}
    for parameter in parameter_details:
        parameter_type = parameter.get('type') or 'Unknown'
        parameter_types[parameter_type] = parameter_types.get(parameter_type, 0) + 1

    return {
        'summary': {
            'parameters': len(parameter_details),
            'documents': len(documents),
            'managed_instances': len(instances),
            'sessions': len(sessions),
            'automations': len(automation_executions),
            'maintenance_windows': len(maintenance_windows),
            'patch_baselines': len(patch_baselines),
            'default_patch_baselines': len(default_patch_baselines),
            'associations': len(associations),
            'commands': len(commands),
            'ops_items': len(ops_items),
        },
        'parameters': parameter_details,
        'parameter_types': [{'name': key, 'type': key, 'count': count} for key, count in sorted(parameter_types.items())],
        'documents': document_details,
        'document_identifiers': documents,
        'managed_instances': instances,
        'sessions': sessions,
        'automation_executions': automation_executions,
        'maintenance_windows': maintenance_windows,
        'patch_baselines': patch_baselines,
        'default_patch_baselines': default_patch_baselines,
        'associations': associations,
        'commands': commands,
        'command_invocations': command_invocations,
        'compliance_summaries': compliance_summaries,
        'resource_compliance_summaries': resource_compliance_summaries,
        'resource_data_syncs': data_syncs,
        'ops_items': ops_items,
        'ops_metadata': ops_metadata,
        'supported_from_sdk': [
            'PutParameter',
            'GetParameter',
            'GetParameters',
            'GetParametersByPath',
            'DescribeParameters',
            'DeleteParameter',
            'CreateDocument',
            'DescribeDocument',
            'ListDocuments',
            'SendCommand',
            'ListCommands',
            'ListCommandInvocations',
            'StartSession',
            'DescribeSessions',
            'StartAutomationExecution',
            'DescribeAutomationExecutions',
            'CreateMaintenanceWindow',
            'DescribeMaintenanceWindows',
            'CreatePatchBaseline',
            'DescribePatchBaselines',
            'GetDefaultPatchBaseline',
            'ListAssociations',
            'DescribeInstanceInformation',
            'ListTagsForResource',
        ],
        'notes': [
            'Parameter values are intentionally not fetched; the page shows metadata, types, tiers, policies, and tags.',
            'Document details are expanded for the first 50 document identifiers to keep page loads bounded.',
            'AWS-owned patch baselines are shown separately from the per-operating-system defaults returned by GetDefaultPatchBaseline.',
            'Floci 1.5.26 executes SendCommand in EC2 containers and adds patch baseline discovery APIs.',
            'Floci 1.5.27 rejects send-command timeouts below the AWS minimum.',
            'Floci 1.5.29 retains local service diagnostics on command failure.',
        ],
    }


def list_ssm_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    ssm = factory.client('ssm')
    resources = [
        {
            'type': 'parameter',
            'name': parameter.get('Name'),
            'parameter_type': parameter.get('Type'),
            'tier': parameter.get('Tier'),
            'version': parameter.get('Version'),
        }
        for parameter in _safe_value(lambda: core._paginate(ssm, 'describe_parameters', 'Parameters'), [])
    ]
    resources.extend(
        {
            'type': 'document',
            'name': document.get('Name'),
            'owner': document.get('Owner'),
            'document_type': document.get('DocumentType'),
            'document_version': document.get('DocumentVersion'),
        }
        for document in _safe_value(lambda: core._paginate(ssm, 'list_documents', 'DocumentIdentifiers'), [])
    )
    resources.extend(
        {
            'type': 'managed_instance',
            'id': instance.get('InstanceId'),
            'name': instance.get('Name') or instance.get('InstanceId'),
            'ping_status': instance.get('PingStatus'),
            'platform_type': instance.get('PlatformType'),
        }
        for instance in _safe_value(lambda: core._paginate(ssm, 'describe_instance_information', 'InstanceInformationList'), [])
    )
    return resources
