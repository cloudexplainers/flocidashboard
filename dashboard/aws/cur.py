"""Cost and Usage Reports inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def cur_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    cur = factory.client('cur')
    operations = set(cur.meta.service_model.operation_names)

    report_definitions = _safe_value(
        lambda: core._operation_items(cur, 'describe_report_definitions', 'ReportDefinitions'),
        [],
    ) if 'DescribeReportDefinitions' in operations else []

    return {
        'summary': {
            'report_definitions': len(report_definitions),
            'available_sdk_operations': len(operations),
        },
        'report_definitions': _clean_response(report_definitions),
        'supported_from_sdk': [
            operation
            for operation in [
                'DescribeReportDefinitions',
                'PutReportDefinition',
                'ModifyReportDefinition',
                'DeleteReportDefinition',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [],
    }


def list_cur_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    cur = factory.client('cur')
    operations = set(cur.meta.service_model.operation_names)

    if 'DescribeReportDefinitions' not in operations:
        return []

    return [
        {
            'type': 'report_definition',
            'name': report.get('ReportName'),
            's3_bucket': report.get('S3Bucket'),
            'time_unit': report.get('TimeUnit'),
        }
        for report in _safe_value(
            lambda: core._operation_items(cur, 'describe_report_definitions', 'ReportDefinitions'),
            [],
        )
    ]
