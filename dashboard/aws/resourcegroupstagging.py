"""Resource Groups Tagging inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _safe_value


def resourcegroupstagging_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    tagging = factory.client('resourcegroupstaggingapi')
    resources = _safe_value(
        lambda: core._paginate(tagging, 'get_resources', 'ResourceTagMappingList', IncludeComplianceDetails=True),
        [],
    )
    tag_keys = _safe_value(lambda: core._paginate(tagging, 'get_tag_keys', 'TagKeys'), [])
    tag_values = []
    for key in tag_keys[:25]:
        values = _safe_value(lambda key=key: core._paginate(tagging, 'get_tag_values', 'TagValues', Key=key), [])
        tag_values.append({
            'name': key,
            'key': key,
            'values': values,
            'value_count': len(values),
        })

    resource_type_counts: dict[str, int] = {}
    tag_counts: dict[str, int] = {}
    resource_details = []
    for mapping in resources:
        arn = mapping.get('ResourceARN')
        arn_parts = arn.split(':') if arn else []
        resource_type = arn_parts[2] if len(arn_parts) > 2 else 'unknown'
        resource_type_counts[resource_type] = resource_type_counts.get(resource_type, 0) + 1
        tags = mapping.get('Tags', [])
        for tag in tags:
            key = tag.get('Key')
            if key:
                tag_counts[key] = tag_counts.get(key, 0) + 1

        resource_details.append({
            'name': arn.rsplit('/', 1)[-1].rsplit(':', 1)[-1] if arn else None,
            'arn': arn,
            'resource_type': resource_type,
            'tags': tags,
            'tag_count': len(tags),
            'compliance_details': mapping.get('ComplianceDetails'),
        })

    resource_types = [
        {'name': resource_type, 'resource_type': resource_type, 'count': count}
        for resource_type, count in sorted(resource_type_counts.items())
    ]
    top_tags = [
        {'name': key, 'key': key, 'resource_count': count}
        for key, count in sorted(tag_counts.items(), key=lambda item: (-item[1], item[0]))[:50]
    ]

    return {
        'summary': {
            'tagged_resources': len(resource_details),
            'tag_keys': len(tag_keys),
            'tag_value_keys_sampled': len(tag_values),
            'resource_types': len(resource_types),
        },
        'resources': resource_details,
        'resource_types': resource_types,
        'tag_keys': [{'name': key, 'key': key} for key in tag_keys],
        'tag_values': tag_values,
        'top_tags': top_tags,
        'supported_operations': [
            'GetResources',
            'GetTagKeys',
            'GetTagValues',
            'TagResources',
            'UntagResources',
        ],
        'notes': [
            'Floci stores tags for arbitrary AWS-shaped ARNs without validating that the referenced resource exists.',
            'GetResources supports ARN, tag, resource type, and pagination filters.',
            'Global ARNs with an empty region segment, such as S3 bucket ARNs, are visible across regions.',
            'Tag values are sampled for the first 25 tag keys to keep page loads bounded.',
            'AWS Organizations tag policy enforcement and persistent storage across restarts are out of scope.',
        ],
    }


def list_resourcegroupstagging_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    tagging = factory.client('resourcegroupstaggingapi')
    return [
        {
            'type': 'tagged_resource',
            'name': mapping.get('ResourceARN'),
            'arn': mapping.get('ResourceARN'),
            'tag_count': len(mapping.get('Tags', [])),
            'tags': mapping.get('Tags', []),
            'compliance_details': mapping.get('ComplianceDetails'),
        }
        for mapping in _safe_value(
            lambda: core._paginate(tagging, 'get_resources', 'ResourceTagMappingList', IncludeComplianceDetails=True),
            [],
        )
    ]
