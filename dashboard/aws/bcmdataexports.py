"""BCM Data Exports inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def bcmdataexports_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    exports = factory.client('bcm-data-exports')
    operations = set(exports.meta.service_model.operation_names)

    export_summaries = _safe_value(
        lambda: core._operation_items(exports, 'list_exports', 'Exports'),
        [],
    ) if 'ListExports' in operations else []

    def export_detail(export: dict[str, Any]) -> dict[str, Any]:
        export_arn = export.get('ExportArn') or export.get('Arn')
        details = _safe_value(
            lambda: exports.get_export(ExportArn=export_arn).get('Export', {}),
            None,
        ) if export_arn and 'GetExport' in operations else None

        return {
            'name': export.get('Name') or export_arn,
            'arn': export_arn,
            'description': export.get('Description'),
            'status': export.get('Status'),
            'created_at': export.get('CreatedAt'),
            'last_updated_at': export.get('LastUpdatedAt'),
            'details': _clean_response(details),
        }

    tables = _safe_value(
        lambda: core._operation_items(exports, 'list_tables', 'Tables'),
        [],
    ) if 'ListTables' in operations else []

    return {
        'summary': {
            'exports': len(export_summaries),
            'tables': len(tables),
            'available_sdk_operations': len(operations),
        },
        'exports': [export_detail(export) for export in export_summaries],
        'tables': _clean_response(tables),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateExport',
                'DeleteExport',
                'GetExport',
                'ListExports',
                'ListTables',
                'UpdateExport',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'BCM exports round out the local cost-services flow alongside Pricing, Cost Explorer, and CUR.',
        ],
    }


def list_bcmdataexports_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    exports = factory.client('bcm-data-exports')
    operations = set(exports.meta.service_model.operation_names)

    if 'ListExports' not in operations:
        return []

    return [
        {
            'type': 'export',
            'name': export.get('Name') or export.get('ExportArn') or export.get('Arn'),
            'arn': export.get('ExportArn') or export.get('Arn'),
            'status': export.get('Status'),
        }
        for export in _safe_value(lambda: core._operation_items(exports, 'list_exports', 'Exports'), [])
    ]
