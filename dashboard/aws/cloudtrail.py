"""CloudTrail inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _cloudtrail_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def cloudtrail_inventory() -> dict[str, Any]:
    cloudtrail = core.FlociClientFactory().client('cloudtrail')
    operations = set(cloudtrail.meta.service_model.operation_names)
    trail_summaries = cloudtrail.describe_trails(includeShadowTrails=False).get('trailList', [])

    def trail_name(trail: dict[str, Any]) -> str | None:
        return trail.get('Name') or trail.get('TrailARN') or trail.get('TrailArn')

    def trail_arn(trail: dict[str, Any]) -> str | None:
        return trail.get('TrailARN') or trail.get('TrailArn')

    def trail_detail(trail: dict[str, Any]) -> dict[str, Any]:
        name = trail_name(trail)
        arn = trail_arn(trail)
        described = trail
        clean_name = described.get('Name') or name
        clean_arn = trail_arn(described) or arn
        status = _cloudtrail_optional(
            lambda: cloudtrail.get_trail_status(Name=clean_arn or clean_name),
            {'TrailNotFoundException'},
        ) if (clean_arn or clean_name) and 'GetTrailStatus' in operations else None

        return {
            'name': clean_name,
            'arn': clean_arn,
            's3_bucket_name': described.get('S3BucketName'),
            'include_global_service_events': described.get('IncludeGlobalServiceEvents'),
            'is_multi_region_trail': described.get('IsMultiRegionTrail'),
            'home_region': described.get('HomeRegion'),
            'is_organization_trail': described.get('IsOrganizationTrail'),
            'status': status if isinstance(status, dict) and not status.get('error') else None,
            'status_details': status if isinstance(status, dict) and status.get('error') else None,
        }

    trails = [trail_detail(trail) for trail in trail_summaries]
    lookup_events = _cloudtrail_optional(
        lambda: core._operation_items(cloudtrail, 'lookup_events', 'Events'),
        {'InvalidLookupAttributesException'},
    ) if 'LookupEvents' in operations else []

    return {
        'summary': {
            'trails': len(trails),
            'logging': sum(1 for trail in trails if (trail.get('status') or {}).get('IsLogging')),
            'multi_region_trails': sum(1 for trail in trails if trail.get('is_multi_region_trail')),
            'lookup_events': len(lookup_events) if isinstance(lookup_events, list) else 0,
        },
        'trails': trails,
        'lookup_events': lookup_events,
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateTrail',
                'DeleteTrail',
                'DescribeTrails',
                'GetTrailStatus',
                'StartLogging',
                'StopLogging',
                'PutEventSelectors',
                'LookupEvents',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci persists trail configuration and logging state but does not record live API activity into trails.',
            'PutEventSelectors is accepted by Floci but selector payloads are not stored, so the dashboard does not expose that no-op control.',
        ],
    }


def list_cloudtrail_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    cloudtrail = factory.client('cloudtrail')
    operations = set(cloudtrail.meta.service_model.operation_names)
    trails = _safe_value(
        lambda: core._operation_items(cloudtrail, 'list_trails', 'Trails'),
        [],
    ) if 'ListTrails' in operations else []
    if not trails and 'DescribeTrails' in operations:
        trails = _safe_value(
            lambda: cloudtrail.describe_trails(includeShadowTrails=False).get('trailList', []),
            [],
        )

    return [
        {
            'type': 'trail',
            'name': trail.get('Name') or trail.get('TrailARN') or trail.get('TrailArn'),
            'arn': trail.get('TrailARN') or trail.get('TrailArn'),
            'home_region': trail.get('HomeRegion'),
            's3_bucket_name': trail.get('S3BucketName'),
        }
        for trail in trails
    ]
