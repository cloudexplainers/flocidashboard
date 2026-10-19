"""Interactive EC2 helpers for the instance workbench."""

from __future__ import annotations

import base64
import gzip
from typing import Any
from botocore.config import Config

from .aws import FlociClientFactory


def _ec2_client():
    factory = FlociClientFactory()
    factory.config = factory.config.merge(Config(read_timeout=120, retries={'max_attempts': 0}))
    return factory.client('ec2')


def _ssm_client():
    return FlociClientFactory().client('ssm')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _string_list(values: Any) -> list[str]:
    if values in (None, ''):
        return []
    if not isinstance(values, list):
        raise ValueError('Expected a JSON array')
    return [str(value).strip() for value in values if str(value).strip()]


def _encoded_user_data(user_data: str | None) -> str | None:
    if not user_data:
        return None
    return base64.b64encode(user_data.encode('utf-8')).decode('ascii')


def _tag_list(tags: Any) -> list[dict[str, str]]:
    if tags in (None, ''):
        return []
    if not isinstance(tags, dict):
        raise ValueError('Tags must be a JSON object')
    result = []
    for key, value in tags.items():
        cleaned_key = str(key).strip()
        if not cleaned_key:
            raise ValueError('Tag keys cannot be empty')
        result.append({'Key': cleaned_key, 'Value': str(value)})
    return result


def _instance_summary(instance: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': instance.get('InstanceId'),
        'image_id': instance.get('ImageId'),
        'instance_type': instance.get('InstanceType'),
        'state': instance.get('State', {}).get('Name'),
        'private_ip': instance.get('PrivateIpAddress'),
        'public_ip': instance.get('PublicIpAddress'),
        'public_dns': instance.get('PublicDnsName'),
        'key_name': instance.get('KeyName'),
        'subnet_id': instance.get('SubnetId'),
        'vpc_id': instance.get('VpcId'),
    }


def run_instances(
    image_id: str,
    instance_type: str,
    *,
    subnet_id: str | None = None,
    security_group_ids: list[str] | None = None,
    key_name: str | None = None,
    user_data: str | None = None,
    iam_instance_profile_arn: str | None = None,
    tags: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request: dict[str, Any] = {
        'ImageId': _required(image_id, 'Image ID'),
        'InstanceType': _required(instance_type, 'Instance type'),
        'MinCount': 1,
        'MaxCount': 1,
    }
    if subnet_id:
        request['SubnetId'] = subnet_id
    if security_group_ids:
        request['SecurityGroupIds'] = security_group_ids
    if key_name:
        request['KeyName'] = key_name
    # RunInstances models UserData as a blob, so botocore performs the wire-level
    # base64 encoding. Pre-encoding here would encode it twice and leave Floci
    # trying to execute base64 text instead of the script.
    if user_data:
        request['UserData'] = user_data
    if iam_instance_profile_arn:
        request['IamInstanceProfile'] = {'Arn': iam_instance_profile_arn}
    instance_tags = _tag_list(tags)
    if instance_tags:
        request['TagSpecifications'] = [{'ResourceType': 'instance', 'Tags': instance_tags}]

    response = _ec2_client().run_instances(**request)
    instances = response.get('Instances', [])
    return {
        'reservation_id': response.get('ReservationId'),
        'instances': [_instance_summary(instance) for instance in instances],
        'instance_id': instances[0].get('InstanceId') if instances else None,
    }


def start_instance(instance_id: str) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    response = _ec2_client().start_instances(InstanceIds=[instance])
    return {'instance_id': instance, 'state_changes': response.get('StartingInstances', [])}


def stop_instance(instance_id: str) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    response = _ec2_client().stop_instances(InstanceIds=[instance])
    return {'instance_id': instance, 'state_changes': response.get('StoppingInstances', [])}


def reboot_instance(instance_id: str) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    _ec2_client().reboot_instances(InstanceIds=[instance])
    return {'instance_id': instance, 'rebooted': True}


def terminate_instance(instance_id: str) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    response = _ec2_client().terminate_instances(InstanceIds=[instance])
    return {'instance_id': instance, 'state_changes': response.get('TerminatingInstances', [])}


def import_key_pair(key_name: str, public_key_material: str) -> dict[str, Any]:
    name = _required(key_name, 'Key name')
    material = _required(public_key_material, 'Public key material')
    response = _ec2_client().import_key_pair(
        KeyName=name,
        PublicKeyMaterial=material.encode('utf-8'),
    )
    return {
        'key_name': name,
        'key_pair_id': response.get('KeyPairId'),
        'fingerprint': response.get('KeyFingerprint'),
    }


def update_instance_tags(instance_id: str, tags: Any) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    desired = _tag_list(tags)
    ec2 = _ec2_client()
    reservations = ec2.describe_instances(InstanceIds=[instance]).get('Reservations', [])
    instances = [item for reservation in reservations for item in reservation.get('Instances', [])]
    if not instances:
        raise ValueError(f'Instance {instance} was not found')

    existing = {tag.get('Key'): tag.get('Value', '') for tag in instances[0].get('Tags', []) if tag.get('Key')}
    desired_map = {tag['Key']: tag['Value'] for tag in desired}
    removed = [{'Key': key, 'Value': value} for key, value in existing.items() if key not in desired_map]
    if removed:
        ec2.delete_tags(Resources=[instance], Tags=removed)
    if desired:
        ec2.create_tags(Resources=[instance], Tags=desired)
    return {'instance_id': instance, 'tags': desired, 'removed': [tag['Key'] for tag in removed]}


def run_instance_command(instance_id: str, command: str, *, timeout_seconds: int = 3600) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    script = _required(command, 'Command')
    timeout = int(timeout_seconds)
    if timeout < 30 or timeout > 86400:
        raise ValueError('Timeout must be between 30 and 86400 seconds')
    response = _ssm_client().send_command(
        InstanceIds=[instance],
        DocumentName='AWS-RunShellScript',
        Parameters={'commands': [script]},
        TimeoutSeconds=timeout,
        Comment='Floci Dashboard EC2 command runner',
    )
    command_data = response.get('Command', {})
    return {
        'instance_id': instance,
        'command_id': command_data.get('CommandId'),
        'status': command_data.get('Status'),
        'requested_at': command_data.get('RequestedDateTime'),
        'command': script,
    }


def instance_command_detail(instance_id: str, command_id: str) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    command = _required(command_id, 'Command ID')
    response = _ssm_client().get_command_invocation(CommandId=command, InstanceId=instance)
    return {
        'instance_id': instance,
        'command_id': command,
        'document_name': response.get('DocumentName'),
        'status': response.get('Status'),
        'status_details': response.get('StatusDetails'),
        'response_code': response.get('ResponseCode'),
        'stdout': response.get('StandardOutputContent', ''),
        'stderr': response.get('StandardErrorContent', ''),
        'started_at': response.get('ExecutionStartDateTime'),
        'ended_at': response.get('ExecutionEndDateTime'),
    }


def list_instance_commands(instance_id: str) -> dict[str, Any]:
    instance = _required(instance_id, 'Instance ID')
    response = _ssm_client().list_command_invocations(InstanceId=instance, Details=True)
    invocations = []
    for item in response.get('CommandInvocations', []):
        invocations.append({
            'command_id': item.get('CommandId'),
            'instance_id': item.get('InstanceId'),
            'document_name': item.get('DocumentName'),
            'status': item.get('Status'),
            'status_details': item.get('StatusDetails'),
            'requested_at': item.get('RequestedDateTime'),
            'comment': item.get('Comment'),
        })
    return {'instance_id': instance, 'commands': invocations}


def normalize_security_group_ids(values: Any) -> list[str]:
    return _string_list(values)


def create_vpc(cidr_block: str, *, name: str | None = None) -> dict[str, Any]:
    ec2 = _ec2_client()
    response = ec2.create_vpc(CidrBlock=_required(cidr_block, 'CIDR block'))
    vpc = response.get('Vpc', {})
    if name:
        ec2.create_tags(Resources=[vpc.get('VpcId')], Tags=[{'Key': 'Name', 'Value': name.strip()}])
    return {'vpc': vpc, 'vpc_id': vpc.get('VpcId')}


def delete_vpc(vpc_id: str) -> dict[str, Any]:
    vpc = _required(vpc_id, 'VPC ID')
    _ec2_client().delete_vpc(VpcId=vpc)
    return {'vpc_id': vpc, 'deleted': True}


def create_subnet(vpc_id: str, cidr_block: str, *, availability_zone: str | None = None, name: str | None = None) -> dict[str, Any]:
    ec2 = _ec2_client()
    request = {'VpcId': _required(vpc_id, 'VPC ID'), 'CidrBlock': _required(cidr_block, 'CIDR block')}
    if availability_zone:
        request['AvailabilityZone'] = availability_zone.strip()
    response = ec2.create_subnet(**request)
    subnet = response.get('Subnet', {})
    if name:
        ec2.create_tags(Resources=[subnet.get('SubnetId')], Tags=[{'Key': 'Name', 'Value': name.strip()}])
    return {'subnet': subnet, 'subnet_id': subnet.get('SubnetId')}


def delete_subnet(subnet_id: str) -> dict[str, Any]:
    subnet = _required(subnet_id, 'Subnet ID')
    _ec2_client().delete_subnet(SubnetId=subnet)
    return {'subnet_id': subnet, 'deleted': True}


def create_security_group(name: str, description: str, vpc_id: str) -> dict[str, Any]:
    response = _ec2_client().create_security_group(
        GroupName=_required(name, 'Group name'),
        Description=_required(description, 'Description'),
        VpcId=_required(vpc_id, 'VPC ID'),
    )
    return {'group_id': response.get('GroupId'), 'group_name': name.strip(), 'vpc_id': vpc_id.strip()}


def delete_security_group(group_id: str) -> dict[str, Any]:
    group = _required(group_id, 'Security group ID')
    _ec2_client().delete_security_group(GroupId=group)
    return {'group_id': group, 'deleted': True}


def change_security_group_rule(group_id: str, direction: str, rule: Any, *, revoke: bool = False) -> dict[str, Any]:
    group = _required(group_id, 'Security group ID')
    normalized_direction = _required(direction, 'Direction').lower()
    if normalized_direction not in {'ingress', 'egress'}:
        raise ValueError('Direction must be ingress or egress')
    if not isinstance(rule, dict):
        raise ValueError('Rule must be a JSON object')
    protocol = str(rule.get('protocol') or 'tcp')
    permission: dict[str, Any] = {'IpProtocol': protocol}
    if protocol not in {'-1', 'all'}:
        permission['FromPort'] = int(rule.get('from_port'))
        permission['ToPort'] = int(rule.get('to_port', rule.get('from_port')))
    cidr = _required(rule.get('cidr', ''), 'CIDR')
    ip_range = {'CidrIp': cidr}
    if rule.get('description') and not revoke:
        ip_range['Description'] = str(rule['description']).strip()
    permission['IpRanges'] = [ip_range]
    operation = f"{'revoke' if revoke else 'authorize'}_security_group_{normalized_direction}"
    response = getattr(_ec2_client(), operation)(GroupId=group, IpPermissions=[permission])
    return {'group_id': group, 'direction': normalized_direction, 'rule': rule, 'revoked': revoke, 'return': response.get('Return', True)}


def create_internet_gateway() -> dict[str, Any]:
    gateway = _ec2_client().create_internet_gateway().get('InternetGateway', {})
    return {'internet_gateway': gateway, 'internet_gateway_id': gateway.get('InternetGatewayId')}


def delete_internet_gateway(gateway_id: str) -> dict[str, Any]:
    gateway = _required(gateway_id, 'Internet gateway ID')
    _ec2_client().delete_internet_gateway(InternetGatewayId=gateway)
    return {'internet_gateway_id': gateway, 'deleted': True}


def set_internet_gateway_attachment(gateway_id: str, vpc_id: str, *, detach: bool = False) -> dict[str, Any]:
    gateway = _required(gateway_id, 'Internet gateway ID')
    vpc = _required(vpc_id, 'VPC ID')
    operation = 'detach_internet_gateway' if detach else 'attach_internet_gateway'
    getattr(_ec2_client(), operation)(InternetGatewayId=gateway, VpcId=vpc)
    return {'internet_gateway_id': gateway, 'vpc_id': vpc, 'detached': detach}


def create_route_table(vpc_id: str) -> dict[str, Any]:
    table = _ec2_client().create_route_table(VpcId=_required(vpc_id, 'VPC ID')).get('RouteTable', {})
    return {'route_table': table, 'route_table_id': table.get('RouteTableId')}


def delete_route_table(route_table_id: str) -> dict[str, Any]:
    table = _required(route_table_id, 'Route table ID')
    _ec2_client().delete_route_table(RouteTableId=table)
    return {'route_table_id': table, 'deleted': True}


def change_route_table_association(route_table_id: str, *, subnet_id: str | None = None, association_id: str | None = None, disassociate: bool = False) -> dict[str, Any]:
    if disassociate:
        association = _required(association_id or '', 'Association ID')
        _ec2_client().disassociate_route_table(AssociationId=association)
        return {'association_id': association, 'disassociated': True}
    table = _required(route_table_id, 'Route table ID')
    response = _ec2_client().associate_route_table(RouteTableId=table, SubnetId=_required(subnet_id or '', 'Subnet ID'))
    return {'route_table_id': table, 'subnet_id': subnet_id, 'association_id': response.get('AssociationId')}


def change_route(route_table_id: str, destination_cidr: str, gateway_id: str | None = None, *, delete: bool = False) -> dict[str, Any]:
    table = _required(route_table_id, 'Route table ID')
    destination = _required(destination_cidr, 'Destination CIDR')
    if delete:
        _ec2_client().delete_route(RouteTableId=table, DestinationCidrBlock=destination)
    else:
        _ec2_client().create_route(RouteTableId=table, DestinationCidrBlock=destination, GatewayId=_required(gateway_id or '', 'Gateway ID'))
    return {'route_table_id': table, 'destination_cidr': destination, 'gateway_id': gateway_id, 'deleted': delete}


def allocate_elastic_ip() -> dict[str, Any]:
    response = _ec2_client().allocate_address(Domain='vpc')
    return {'allocation_id': response.get('AllocationId'), 'public_ip': response.get('PublicIp'), 'domain': response.get('Domain')}


def release_elastic_ip(allocation_id: str) -> dict[str, Any]:
    allocation = _required(allocation_id, 'Allocation ID')
    _ec2_client().release_address(AllocationId=allocation)
    return {'allocation_id': allocation, 'released': True}


def set_elastic_ip_association(allocation_id: str, *, instance_id: str | None = None, association_id: str | None = None, disassociate: bool = False) -> dict[str, Any]:
    allocation = _required(allocation_id, 'Allocation ID')
    if disassociate:
        association = _required(association_id or '', 'Association ID')
        _ec2_client().disassociate_address(AssociationId=association)
        return {'allocation_id': allocation, 'association_id': association, 'disassociated': True}
    instance = _required(instance_id or '', 'Instance ID')
    response = _ec2_client().associate_address(AllocationId=allocation, InstanceId=instance)
    return {'allocation_id': allocation, 'instance_id': instance, 'association_id': response.get('AssociationId')}


def create_nat_gateway(subnet_id: str, allocation_id: str, *, connectivity_type: str = 'public') -> dict[str, Any]:
    response = _ec2_client().create_nat_gateway(
        SubnetId=_required(subnet_id, 'Subnet ID'),
        AllocationId=_required(allocation_id, 'Allocation ID'),
        ConnectivityType=connectivity_type or 'public',
    )
    gateway = response.get('NatGateway', {})
    return {'nat_gateway': gateway, 'nat_gateway_id': gateway.get('NatGatewayId')}


def delete_nat_gateway(nat_gateway_id: str) -> dict[str, Any]:
    gateway = _required(nat_gateway_id, 'NAT gateway ID')
    response = _ec2_client().delete_nat_gateway(NatGatewayId=gateway)
    return {'nat_gateway_id': gateway, 'nat_gateway': response.get('NatGateway'), 'deleted': True}


def create_vpc_endpoint(vpc_id: str, service_name: str, endpoint_type: str, *, route_table_ids: Any = None, subnet_ids: Any = None, security_group_ids: Any = None, private_dns_enabled: bool = False) -> dict[str, Any]:
    request: dict[str, Any] = {
        'VpcId': _required(vpc_id, 'VPC ID'),
        'ServiceName': _required(service_name, 'Service name'),
        'VpcEndpointType': endpoint_type or 'Gateway',
    }
    routes = _string_list(route_table_ids)
    subnets_value = _string_list(subnet_ids)
    groups = _string_list(security_group_ids)
    if routes:
        request['RouteTableIds'] = routes
    if subnets_value:
        request['SubnetIds'] = subnets_value
    if groups:
        request['SecurityGroupIds'] = groups
    if request['VpcEndpointType'] == 'Interface':
        request['PrivateDnsEnabled'] = bool(private_dns_enabled)
    endpoint = _ec2_client().create_vpc_endpoint(**request).get('VpcEndpoint', {})
    return {'vpc_endpoint': endpoint, 'vpc_endpoint_id': endpoint.get('VpcEndpointId')}


def delete_vpc_endpoint(vpc_endpoint_id: str) -> dict[str, Any]:
    endpoint = _required(vpc_endpoint_id, 'VPC endpoint ID')
    response = _ec2_client().delete_vpc_endpoints(VpcEndpointIds=[endpoint])
    return {'vpc_endpoint_id': endpoint, 'unsuccessful': response.get('Unsuccessful', []), 'deleted': not response.get('Unsuccessful')}


def create_network_acl(vpc_id: str) -> dict[str, Any]:
    acl = _ec2_client().create_network_acl(VpcId=_required(vpc_id, 'VPC ID')).get('NetworkAcl', {})
    return {'network_acl': acl, 'network_acl_id': acl.get('NetworkAclId')}


def delete_network_acl(network_acl_id: str) -> dict[str, Any]:
    acl = _required(network_acl_id, 'Network ACL ID')
    _ec2_client().delete_network_acl(NetworkAclId=acl)
    return {'network_acl_id': acl, 'deleted': True}


def put_network_acl_entry(network_acl_id: str, entry: Any, *, replace: bool = False) -> dict[str, Any]:
    acl = _required(network_acl_id, 'Network ACL ID')
    if not isinstance(entry, dict):
        raise ValueError('Entry must be a JSON object')
    if entry.get('rule_number') in (None, ''):
        raise ValueError('Rule number is required')
    try:
        rule_num = int(entry['rule_number'])
    except (ValueError, TypeError) as exc:
        raise ValueError('Rule number must be an integer') from exc

    request: dict[str, Any] = {
        'NetworkAclId': acl,
        'RuleNumber': rule_num,
        'Protocol': str(entry.get('protocol', '-1')),
        'RuleAction': str(entry.get('rule_action', 'allow')),
        'Egress': bool(entry.get('egress', False)),
        'CidrBlock': _required(entry.get('cidr', ''), 'CIDR'),
    }
    if entry.get('from_port') not in (None, ''):
        request['PortRange'] = {
            'From': int(entry['from_port']),
            'To': int(entry.get('to_port', entry['from_port'])),
        }
    operation = 'replace_network_acl_entry' if replace else 'create_network_acl_entry'
    getattr(_ec2_client(), operation)(**request)
    return {'network_acl_id': acl, 'entry': entry, 'replaced': replace}


def delete_network_acl_entry(network_acl_id: str, rule_number: Any, *, egress: bool = False) -> dict[str, Any]:
    acl = _required(network_acl_id, 'Network ACL ID')
    if rule_number in (None, ''):
        raise ValueError('Rule number is required')
    try:
        rule = int(rule_number)
    except (ValueError, TypeError) as exc:
        raise ValueError('Rule number must be an integer') from exc
    _ec2_client().delete_network_acl_entry(NetworkAclId=acl, RuleNumber=rule, Egress=bool(egress))
    return {'network_acl_id': acl, 'rule_number': rule, 'egress': bool(egress), 'deleted': True}


def replace_network_acl_association(network_acl_id: str, association_id: str) -> dict[str, Any]:
    acl = _required(network_acl_id, 'Network ACL ID')
    association = _required(association_id, 'Association ID')
    response = _ec2_client().replace_network_acl_association(NetworkAclId=acl, AssociationId=association)
    return {'network_acl_id': acl, 'association_id': association, 'new_association_id': response.get('NewAssociationId')}


def create_flow_log(resource_id: str, resource_type: str, traffic_type: str, destination: str, *, max_aggregation_interval: int = 60) -> dict[str, Any]:
    response = _ec2_client().create_flow_logs(
        ResourceIds=[_required(resource_id, 'Resource ID')],
        ResourceType=resource_type or 'VPC',
        TrafficType=traffic_type or 'ALL',
        LogDestinationType='s3',
        LogDestination=_required(destination, 'S3 destination ARN'),
        MaxAggregationInterval=int(max_aggregation_interval),
    )
    ids = response.get('FlowLogIds', [])
    return {'flow_log_ids': ids, 'flow_log_id': ids[0] if ids else None, 'unsuccessful': response.get('Unsuccessful', [])}


def delete_flow_log(flow_log_id: str) -> dict[str, Any]:
    flow_log = _required(flow_log_id, 'Flow log ID')
    response = _ec2_client().delete_flow_logs(FlowLogIds=[flow_log])
    return {'flow_log_id': flow_log, 'unsuccessful': response.get('Unsuccessful', []), 'deleted': not response.get('Unsuccessful')}


def view_flow_log(flow_log_id: str) -> dict[str, Any]:
    flow_log = _required(flow_log_id, 'Flow log ID')
    logs = _ec2_client().describe_flow_logs(FlowLogIds=[flow_log]).get('FlowLogs', [])
    if not logs:
        raise ValueError(f'Flow log {flow_log} was not found')
    config = logs[0]
    destination = config.get('LogDestination') or ''
    bucket = destination.removeprefix('arn:aws:s3:::').split('/', 1)[0]
    if not bucket:
        raise ValueError('Flow log does not have an S3 destination')
    s3 = FlociClientFactory().client('s3')
    objects = s3.list_objects_v2(Bucket=bucket, Prefix='AWSLogs/').get('Contents', [])
    objects = sorted(objects, key=lambda item: item.get('LastModified') or '', reverse=True)
    if not objects:
        return {'flow_log_id': flow_log, 'bucket': bucket, 'files': [], 'records': []}
    latest = objects[0]
    content = s3.get_object(Bucket=bucket, Key=latest['Key'])['Body'].read()
    if latest['Key'].endswith('.gz'):
        content = gzip.decompress(content)
    text = content.decode('utf-8', errors='replace')
    return {
        'flow_log_id': flow_log,
        'bucket': bucket,
        'files': [{'key': item.get('Key'), 'size': item.get('Size'), 'last_modified': item.get('LastModified')} for item in objects[:20]],
        'latest_key': latest.get('Key'),
        'records': text.splitlines()[:500],
    }


def create_volume(availability_zone: str, size: Any, volume_type: str, *, encrypted: bool = False, iops: Any = None, throughput: Any = None, snapshot_id: str | None = None, tags: Any = None) -> dict[str, Any]:
    request: dict[str, Any] = {
        'AvailabilityZone': _required(availability_zone, 'Availability zone'),
        'Size': int(size),
        'VolumeType': volume_type or 'gp3',
        'Encrypted': bool(encrypted),
    }
    if iops not in (None, ''):
        request['Iops'] = int(iops)
    if throughput not in (None, ''):
        request['Throughput'] = int(throughput)
    if snapshot_id:
        request['SnapshotId'] = snapshot_id.strip()
    tag_values = _tag_list(tags)
    if tag_values:
        request['TagSpecifications'] = [{'ResourceType': 'volume', 'Tags': tag_values}]
    volume = _ec2_client().create_volume(**request)
    return {'volume': volume, 'volume_id': volume.get('VolumeId')}


def delete_volume(volume_id: str) -> dict[str, Any]:
    volume = _required(volume_id, 'Volume ID')
    _ec2_client().delete_volume(VolumeId=volume)
    return {'volume_id': volume, 'deleted': True}


def register_image(name: str, description: str, architecture: str, root_device_name: str, block_device_mappings: Any = None) -> dict[str, Any]:
    request: dict[str, Any] = {
        'Name': _required(name, 'Image name'),
        'Description': description or '',
        'Architecture': architecture or 'x86_64',
        'RootDeviceName': root_device_name or '/dev/xvda',
    }
    if block_device_mappings:
        if not isinstance(block_device_mappings, list):
            raise ValueError('Block device mappings must be a JSON array')
        request['BlockDeviceMappings'] = block_device_mappings
    response = _ec2_client().register_image(**request)
    return {'image_id': response.get('ImageId'), 'name': name.strip()}


def _launch_template_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError('Launch template data must be a JSON object')
    result: dict[str, Any] = {}
    mapping = {
        'image_id': 'ImageId',
        'instance_type': 'InstanceType',
        'key_name': 'KeyName',
    }
    for source, target in mapping.items():
        if data.get(source):
            result[target] = str(data[source]).strip()
    groups = _string_list(data.get('security_group_ids'))
    if groups:
        result['SecurityGroupIds'] = groups
    if data.get('user_data'):
        result['UserData'] = _encoded_user_data(str(data['user_data']))
    if data.get('iam_instance_profile_arn'):
        result['IamInstanceProfile'] = {'Arn': str(data['iam_instance_profile_arn']).strip()}
    tags = _tag_list(data.get('tags'))
    if tags:
        result['TagSpecifications'] = [{'ResourceType': 'instance', 'Tags': tags}]
    return result


def create_launch_template(name: str, data: Any) -> dict[str, Any]:
    response = _ec2_client().create_launch_template(
        LaunchTemplateName=_required(name, 'Launch template name'),
        LaunchTemplateData=_launch_template_data(data),
    )
    template = response.get('LaunchTemplate', {})
    return {'launch_template': template, 'launch_template_id': template.get('LaunchTemplateId')}


def create_launch_template_version(launch_template_id: str, data: Any, *, source_version: str | None = None) -> dict[str, Any]:
    request: dict[str, Any] = {
        'LaunchTemplateId': _required(launch_template_id, 'Launch template ID'),
        'LaunchTemplateData': _launch_template_data(data),
    }
    if source_version:
        request['SourceVersion'] = str(source_version)
    version = _ec2_client().create_launch_template_version(**request).get('LaunchTemplateVersion', {})
    return {'launch_template_id': launch_template_id, 'version': version, 'version_number': version.get('VersionNumber')}


def set_launch_template_default_version(launch_template_id: str, version: Any) -> dict[str, Any]:
    template_id = _required(launch_template_id, 'Launch template ID')
    response = _ec2_client().modify_launch_template(LaunchTemplateId=template_id, DefaultVersion=str(version))
    return {'launch_template_id': template_id, 'launch_template': response.get('LaunchTemplate'), 'default_version': str(version)}


def delete_launch_template(launch_template_id: str) -> dict[str, Any]:
    template_id = _required(launch_template_id, 'Launch template ID')
    response = _ec2_client().delete_launch_template(LaunchTemplateId=template_id)
    return {'launch_template_id': template_id, 'launch_template': response.get('LaunchTemplate'), 'deleted': True}


def request_spot_instances(image_id: str, instance_type: str, *, spot_price: str | None = None, instance_count: Any = 1, subnet_id: str | None = None, security_group_ids: Any = None, tags: Any = None) -> dict[str, Any]:
    specification: dict[str, Any] = {
        'ImageId': _required(image_id, 'Image ID'),
        'InstanceType': _required(instance_type, 'Instance type'),
    }
    if subnet_id:
        specification['SubnetId'] = subnet_id.strip()
    groups = _string_list(security_group_ids)
    if groups:
        specification['SecurityGroupIds'] = groups
    request: dict[str, Any] = {
        'InstanceCount': int(instance_count),
        'Type': 'one-time',
        'LaunchSpecification': specification,
    }
    if spot_price:
        request['SpotPrice'] = str(spot_price)
    tag_values = _tag_list(tags)
    if tag_values:
        request['TagSpecifications'] = [{'ResourceType': 'spot-instances-request', 'Tags': tag_values}]
    requests = _ec2_client().request_spot_instances(**request).get('SpotInstanceRequests', [])
    return {'spot_instance_requests': requests, 'spot_request_ids': [item.get('SpotInstanceRequestId') for item in requests]}


def cancel_spot_request(spot_request_id: str) -> dict[str, Any]:
    request_id = _required(spot_request_id, 'Spot request ID')
    requests = _ec2_client().cancel_spot_instance_requests(SpotInstanceRequestIds=[request_id]).get('CancelledSpotInstanceRequests', [])
    return {'spot_request_id': request_id, 'cancelled': requests}
