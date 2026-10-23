"""CloudTrail helpers limited to operations implemented by Floci."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory


def _client():
    return FlociClientFactory().client('cloudtrail')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def create_trail(name: str, s3_bucket_name: str, *, include_global_service_events: bool = True,
                 is_multi_region_trail: bool = False, is_organization_trail: bool = False) -> dict[str, Any]:
    clean_name = _required(name, 'Trail name')
    bucket = _required(s3_bucket_name, 'S3 bucket name')
    response = _client().create_trail(
        Name=clean_name,
        S3BucketName=bucket,
        IncludeGlobalServiceEvents=bool(include_global_service_events),
        IsMultiRegionTrail=bool(is_multi_region_trail),
        IsOrganizationTrail=bool(is_organization_trail),
    )
    return {'name': response.get('Name') or clean_name, 'arn': response.get('TrailARN'), 's3_bucket_name': response.get('S3BucketName') or bucket}


def update_trail(name: str, *, s3_bucket_name: str = '', include_global_service_events: Any = None,
                 is_multi_region_trail: Any = None) -> dict[str, Any]:
    clean_name = _required(name, 'Trail name')
    kwargs: dict[str, Any] = {'Name': clean_name}
    if str(s3_bucket_name or '').strip():
        kwargs['S3BucketName'] = str(s3_bucket_name).strip()
    if include_global_service_events is not None:
        kwargs['IncludeGlobalServiceEvents'] = bool(include_global_service_events)
    if is_multi_region_trail is not None:
        kwargs['IsMultiRegionTrail'] = bool(is_multi_region_trail)
    response = _client().update_trail(**kwargs)
    return {'name': response.get('Name') or clean_name, 'arn': response.get('TrailARN'), 's3_bucket_name': response.get('S3BucketName')}


def set_trail_logging(name: str, enabled: bool) -> dict[str, Any]:
    clean_name = _required(name, 'Trail name')
    client = _client()
    if enabled:
        client.start_logging(Name=clean_name)
    else:
        client.stop_logging(Name=clean_name)
    return {'name': clean_name, 'is_logging': bool(enabled)}


def delete_trail(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Trail name')
    _client().delete_trail(Name=clean_name)
    return {'name': clean_name, 'deleted': True}


def event_selectors(name: str, selectors: Any = None) -> dict[str, Any]:
    from .aws import _clean_response
    client = _client()
    clean_name = _required(name, 'Trail name')
    if selectors is None:
        response = client.get_event_selectors(TrailName=clean_name)
    else:
        if not isinstance(selectors, dict) or not set(selectors).issubset({'EventSelectors', 'AdvancedEventSelectors'}):
            raise ValueError('Use EventSelectors or AdvancedEventSelectors')
        if len(selectors) != 1 or not isinstance(next(iter(selectors.values())), list):
            raise ValueError('Provide exactly one selector list')
        response = client.put_event_selectors(TrailName=clean_name, **selectors)
    return _clean_response({key: value for key, value in response.items() if key != 'ResponseMetadata'})


def lookup_events(*, attribute: str = '', value: str = '', next_token: str = '') -> dict[str, Any]:
    from .aws import _clean_response
    kwargs: dict[str, Any] = {'MaxResults': 50}
    if attribute or value:
        if attribute not in {'EventId', 'EventName', 'ReadOnly', 'Username', 'ResourceType', 'ResourceName', 'EventSource', 'AccessKeyId'}:
            raise ValueError('Invalid lookup attribute')
        kwargs['LookupAttributes'] = [{'AttributeKey': attribute, 'AttributeValue': _required(value, 'Lookup value')}]
    if next_token:
        kwargs['NextToken'] = next_token
    response = _client().lookup_events(**kwargs)
    return {'events': _clean_response(response.get('Events', [])), 'next_token': response.get('NextToken')}
