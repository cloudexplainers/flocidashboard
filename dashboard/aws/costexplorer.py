"""Cost Explorer inventory and resource-list loaders."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def costexplorer_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    ce = factory.client('ce')
    operations = set(ce.meta.service_model.operation_names)
    today = date.today()
    time_period = {
        'Start': today.replace(day=1).isoformat(),
        'End': (today + timedelta(days=1)).isoformat(),
    }

    dimension_values = []
    for dimension in ['SERVICE', 'REGION', 'LINKED_ACCOUNT', 'USAGE_TYPE']:
        values = _safe_value(
            lambda dimension=dimension: core._paginate(
                ce,
                'get_dimension_values',
                'DimensionValues',
                TimePeriod=time_period,
                Dimension=dimension,
            ),
            [],
        ) if 'GetDimensionValues' in operations else []
        dimension_values.append({
            'name': dimension,
            'dimension': dimension,
            'value_count': len(values),
            'values': values[:50],
        })

    tags = _safe_value(
        lambda: core._paginate(ce, 'get_tags', 'Tags', TimePeriod=time_period),
        [],
    ) if 'GetTags' in operations else []

    cost_categories = _safe_value(
        lambda: core._paginate(ce, 'get_cost_categories', 'CostCategoryNames', TimePeriod=time_period),
        [],
    ) if 'GetCostCategories' in operations else []

    cost_and_usage = _safe_value(
        lambda: ce.get_cost_and_usage(
            TimePeriod=time_period,
            Granularity='MONTHLY',
            Metrics=['UnblendedCost'],
            GroupBy=[{'Type': 'DIMENSION', 'Key': 'SERVICE'}],
        ).get('ResultsByTime', []),
        [],
    ) if 'GetCostAndUsage' in operations else []

    return {
        'summary': {
            'dimension_groups': len(dimension_values),
            'dimension_values': sum(item.get('value_count') or 0 for item in dimension_values),
            'tags': len(tags),
            'cost_categories': len(cost_categories),
            'cost_periods': len(cost_and_usage),
            'available_sdk_operations': len(operations),
        },
        'time_period': time_period,
        'dimension_values': dimension_values,
        'tags': [{'name': tag, 'tag': tag} for tag in tags],
        'cost_categories': [{'name': category, 'category': category} for category in cost_categories],
        'cost_and_usage': _clean_response(cost_and_usage),
        'supported_from_sdk': [
            operation
            for operation in [
                'GetCostAndUsage',
                'GetDimensionValues',
                'GetTags',
                'GetCostCategories',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.16 adds Cost Explorer support under the ce service name.',
            'The dashboard samples the current month to keep Cost Explorer reads bounded.',
        ],
    }


def list_costexplorer_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    ce = factory.client('ce')
    operations = set(ce.meta.service_model.operation_names)
    today = date.today()
    time_period = {
        'Start': today.replace(day=1).isoformat(),
        'End': (today + timedelta(days=1)).isoformat(),
    }
    resources: list[dict[str, Any]] = []

    if 'GetDimensionValues' in operations:
        for dimension in ['SERVICE', 'REGION']:
            resources.extend(
                {
                    'type': 'cost_dimension',
                    'name': value.get('Value'),
                    'dimension': dimension,
                    'attributes': value.get('Attributes'),
                }
                for value in _safe_value(
                    lambda dimension=dimension: core._paginate(
                        ce,
                        'get_dimension_values',
                        'DimensionValues',
                        TimePeriod=time_period,
                        Dimension=dimension,
                    ),
                    [],
                )
            )

    if 'GetCostCategories' in operations:
        resources.extend(
            {
                'type': 'cost_category',
                'name': category,
            }
            for category in _safe_value(
                lambda: core._paginate(ce, 'get_cost_categories', 'CostCategoryNames', TimePeriod=time_period),
                [],
            )
        )

    return resources
