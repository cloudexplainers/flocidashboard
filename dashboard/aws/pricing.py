"""AWS Price List inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def pricing_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    pricing = factory.client('pricing')
    operations = set(pricing.meta.service_model.operation_names)

    services = _safe_value(
        lambda: core._paginate(pricing, 'describe_services', 'Services'),
        [],
    ) if 'DescribeServices' in operations else []
    service_details = [
        {
            'name': service.get('ServiceCode'),
            'service_code': service.get('ServiceCode'),
            'attribute_names': service.get('AttributeNames', []),
            'attribute_count': len(service.get('AttributeNames', [])),
        }
        for service in services
    ]

    attribute_values = []
    for service in service_details[:10]:
        service_code = service.get('service_code')
        for attribute_name in service.get('attribute_names', [])[:5]:
            values = _safe_value(
                lambda service_code=service_code, attribute_name=attribute_name: core._paginate(
                    pricing,
                    'get_attribute_values',
                    'AttributeValues',
                    ServiceCode=service_code,
                    AttributeName=attribute_name,
                ),
                [],
            ) if service_code and 'GetAttributeValues' in operations else []
            attribute_values.append({
                'name': f'{service_code}.{attribute_name}',
                'service_code': service_code,
                'attribute_name': attribute_name,
                'values': values[:25],
                'value_count': len(values),
            })

    price_lists = _safe_value(
        lambda: core._paginate(pricing, 'list_price_lists', 'PriceLists', ServiceCode='AmazonS3'),
        [],
    ) if 'ListPriceLists' in operations else []

    return {
        'summary': {
            'services': len(service_details),
            'attribute_value_samples': len(attribute_values),
            'price_lists': len(price_lists),
            'available_sdk_operations': len(operations),
        },
        'services': service_details,
        'attribute_values': attribute_values,
        'price_lists': _clean_response(price_lists),
        'supported_from_sdk': [
            operation
            for operation in [
                'DescribeServices',
                'GetAttributeValues',
                'GetProducts',
                'ListPriceLists',
                'GetPriceListFileUrl',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.15 adds AWS Price List service support under the pricing service name.',
            'Attribute values are sampled from the first 10 services and first 5 attributes per service to keep page loads bounded.',
        ],
    }


def list_pricing_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    pricing = factory.client('pricing')
    operations = set(pricing.meta.service_model.operation_names)

    if 'DescribeServices' not in operations:
        return []

    return [
        {
            'type': 'pricing_service',
            'name': service.get('ServiceCode'),
            'attribute_count': len(service.get('AttributeNames', [])),
        }
        for service in _safe_value(lambda: core._paginate(pricing, 'describe_services', 'Services'), [])
    ]
