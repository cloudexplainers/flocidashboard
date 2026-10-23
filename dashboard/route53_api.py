"""Interactive Route 53 helpers for local DNS management workflows."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('route53')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _string_list(value: Any) -> list[str]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError('Value must be a comma-separated string or list')


def _json_object(value: Any, label: str) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object')
    return value


def _tags(value: Any) -> list[dict[str, str]]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Tags must be an object or list')
    tags = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = item.get('Key') or item.get('key')
        if not key:
            raise ValueError('Each tag needs a Key')
        tag_value = item.get('Value')
        if tag_value is None:
            tag_value = item.get('value')
        tags.append({'Key': str(key), 'Value': '' if tag_value is None else str(tag_value)})
    return tags


def create_hosted_zone(name: str, caller_reference: str, *, comment: str = '', vpc_id: str = '', vpc_region: str = '') -> dict[str, Any]:
    zone_name = _required(name, 'Hosted zone name')
    if not zone_name.endswith('.'):
        zone_name = f'{zone_name}.'
    kwargs: dict[str, Any] = {
        'Name': zone_name,
        'CallerReference': _required(caller_reference, 'Caller reference'),
    }
    config: dict[str, Any] = {}
    if comment:
        config['Comment'] = comment
    if (vpc_id or '').strip():
        # A private hosted zone: created with its first VPC.
        region = (vpc_region or '').strip() or FlociClientFactory().region
        kwargs['VPC'] = {'VPCRegion': region, 'VPCId': _existing_vpc(vpc_id, region)}
        config['PrivateZone'] = True
    if config:
        kwargs['HostedZoneConfig'] = config
    response = _client().create_hosted_zone(**kwargs)
    return {
        'hosted_zone': _clean_response(response.get('HostedZone', {})),
        'delegation_set': _clean_response(response.get('DelegationSet', {})),
        'change_info': _clean_response(response.get('ChangeInfo', {})),
        'response': _clean_response(response),
    }


def delete_hosted_zone(zone_id: str) -> dict[str, Any]:
    clean_id = _required(zone_id, 'Hosted zone ID')
    response = _client().delete_hosted_zone(Id=clean_id)
    return {'zone_id': clean_id, 'change_info': _clean_response(response.get('ChangeInfo', {})), 'response': _clean_response(response)}


def change_record_set(
    zone_id: str,
    action: str,
    name: str,
    record_type: str,
    *,
    ttl: Any = 300,
    values: Any = None,
    comment: str = '',
    record_set: Any = None,
) -> dict[str, Any]:
    clean_action = _required(action, 'Action').upper()
    if clean_action not in {'CREATE', 'UPSERT', 'DELETE'}:
        raise ValueError('Action must be CREATE, UPSERT, or DELETE')

    if record_set:
        rrset = _json_object(record_set, 'Resource record set')
    else:
        record_name = _required(name, 'Record name')
        if not record_name.endswith('.'):
            record_name = f'{record_name}.'
        record_values = _string_list(values)
        if not record_values:
            raise ValueError('At least one record value is required')
        rrset = {
            'Name': record_name,
            'Type': _required(record_type, 'Record type').upper(),
            'TTL': int(ttl or 0),
            'ResourceRecords': [{'Value': value} for value in record_values],
        }

    response = _client().change_resource_record_sets(
        HostedZoneId=_required(zone_id, 'Hosted zone ID'),
        ChangeBatch={
            'Comment': comment or '',
            'Changes': [{'Action': clean_action, 'ResourceRecordSet': rrset}],
        },
    )
    return {'change_info': _clean_response(response.get('ChangeInfo', {})), 'record_set': _clean_response(rrset), 'response': _clean_response(response)}


def create_health_check(caller_reference: str, check_type: str, *, domain_name: str = '', ip_address: str = '', port: Any = None, resource_path: str = '') -> dict[str, Any]:
    config: dict[str, Any] = {'Type': _required(check_type, 'Health check type').upper()}
    if domain_name:
        config['FullyQualifiedDomainName'] = domain_name
    if ip_address:
        config['IPAddress'] = ip_address
    if port not in (None, ''):
        config['Port'] = int(port)
    if resource_path:
        config['ResourcePath'] = resource_path
    response = _client().create_health_check(
        CallerReference=_required(caller_reference, 'Caller reference'),
        HealthCheckConfig=config,
    )
    return {'health_check': _clean_response(response.get('HealthCheck', {})), 'response': _clean_response(response)}


def update_health_check(health_check_id: str, *, domain_name: str = '', ip_address: str = '', port: Any = None, resource_path: str = '') -> dict[str, Any]:
    kwargs: dict[str, Any] = {'HealthCheckId': _required(health_check_id, 'Health check ID')}
    if domain_name:
        kwargs['FullyQualifiedDomainName'] = domain_name
    if ip_address:
        kwargs['IPAddress'] = ip_address
    if port not in (None, ''):
        kwargs['Port'] = int(port)
    if resource_path:
        kwargs['ResourcePath'] = resource_path
    if len(kwargs) == 1:
        raise ValueError('At least one health check field is required')
    response = _client().update_health_check(**kwargs)
    return {'health_check': _clean_response(response.get('HealthCheck', {})), 'response': _clean_response(response)}


def delete_health_check(health_check_id: str) -> dict[str, Any]:
    clean_id = _required(health_check_id, 'Health check ID')
    response = _client().delete_health_check(HealthCheckId=clean_id)
    return {'health_check_id': clean_id, 'response': _clean_response(response)}


def change_tags(resource_type: str, resource_id: str, *, add_tags: Any = None, remove_tag_keys: Any = None) -> dict[str, Any]:
    tags = _tags(add_tags)
    keys = _string_list(remove_tag_keys)
    if not tags and not keys:
        raise ValueError('At least one tag or tag key is required')
    response = _client().change_tags_for_resource(
        ResourceType=_required(resource_type, 'Resource type'),
        ResourceId=_required(resource_id, 'Resource ID'),
        AddTags=tags,
        RemoveTagKeys=keys,
    )
    return {'resource_type': resource_type, 'resource_id': resource_id, 'added_tags': tags, 'removed_tag_keys': keys, 'response': _clean_response(response)}


def _existing_vpc(vpc_id: str, region: str) -> str:
    """Route 53 refuses a VPC that doesn't exist; Floci accepts any ID, so this checks it."""
    from botocore.exceptions import ClientError
    clean = _required(vpc_id, 'VPC ID')
    factory = FlociClientFactory()
    factory.region = region
    try:
        found = factory.client('ec2').describe_vpcs(VpcIds=[clean]).get('Vpcs', [])
    except ClientError as exc:
        if 'NotFound' in exc.response.get('Error', {}).get('Code', ''):
            found = []
        else:
            raise
    if not found:
        raise ValueError(f'VPC {clean} does not exist in {region}')
    return clean


def list_vpc_choices() -> dict[str, Any]:
    """VPCs in the dashboard's Region, for associating with a private hosted zone."""
    factory = FlociClientFactory()
    vpcs = factory.client('ec2').describe_vpcs().get('Vpcs', [])
    return {'region': factory.region, 'vpcs': [{
        'vpc_id': vpc.get('VpcId'),
        'cidr_block': vpc.get('CidrBlock'),
        'name': next((tag['Value'] for tag in vpc.get('Tags', []) if tag.get('Key') == 'Name'), ''),
        'is_default': bool(vpc.get('IsDefault')),
    } for vpc in vpcs]}


def associate_vpc(zone_id: str, vpc_id: str, vpc_region: str = '', comment: str = '') -> dict[str, Any]:
    clean_id = _required(zone_id, 'Hosted zone ID')
    region = (vpc_region or '').strip() or FlociClientFactory().region
    kwargs: dict[str, Any] = {'HostedZoneId': clean_id, 'VPC': {'VPCRegion': region, 'VPCId': _existing_vpc(vpc_id, region)}}
    if comment:
        kwargs['Comment'] = comment
    zone = _client().get_hosted_zone(Id=clean_id)
    if any(vpc.get('VPCId') == kwargs['VPC']['VPCId'] for vpc in zone.get('VPCs', [])):
        raise ValueError(f"{kwargs['VPC']['VPCId']} is already associated with this hosted zone")
    response = _client().associate_vpc_with_hosted_zone(**kwargs)
    return {'zone_id': clean_id, 'change_info': _clean_response(response.get('ChangeInfo', {})), 'vpcs': _clean_response(_client().get_hosted_zone(Id=clean_id).get('VPCs', []))}


def disassociate_vpc(zone_id: str, vpc_id: str, vpc_region: str = '') -> dict[str, Any]:
    clean_id = _required(zone_id, 'Hosted zone ID')
    clean_vpc = _required(vpc_id, 'VPC ID')
    region = (vpc_region or '').strip() or FlociClientFactory().region
    vpcs = _client().get_hosted_zone(Id=clean_id).get('VPCs', [])
    # AWS keeps at least one VPC on a private zone (LastVPCAssociation); Floci lets the last one go.
    if len(vpcs) <= 1:
        raise ValueError('A private hosted zone must keep at least one VPC. Associate another VPC first, or delete the zone.')
    response = _client().disassociate_vpc_from_hosted_zone(HostedZoneId=clean_id, VPC={'VPCRegion': region, 'VPCId': clean_vpc})
    return {'zone_id': clean_id, 'change_info': _clean_response(response.get('ChangeInfo', {})), 'vpcs': _clean_response(_client().get_hosted_zone(Id=clean_id).get('VPCs', []))}
