"""EC2 inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def ec2_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    ec2 = factory.client('ec2')
    operations = set(ec2.meta.service_model.operation_names)

    reservations = _clean_response(_safe_value(lambda: ec2.describe_instances().get('Reservations', []), []))
    instances = [
        {
            'id': instance.get('InstanceId'),
            'image_id': instance.get('ImageId'),
            'instance_type': instance.get('InstanceType'),
            'state': instance.get('State', {}).get('Name'),
            'state_code': instance.get('State', {}).get('Code'),
            'key_name': instance.get('KeyName'),
            'architecture': instance.get('Architecture'),
            'vpc_id': instance.get('VpcId'),
            'subnet_id': instance.get('SubnetId'),
            'private_ip': instance.get('PrivateIpAddress'),
            'public_ip': instance.get('PublicIpAddress'),
            'private_dns': instance.get('PrivateDnsName'),
            'public_dns': instance.get('PublicDnsName'),
            'security_groups': instance.get('SecurityGroups', []),
            'network_interfaces': instance.get('NetworkInterfaces', []),
            'block_device_mappings': instance.get('BlockDeviceMappings', []),
            'iam_instance_profile': instance.get('IamInstanceProfile'),
            'placement': instance.get('Placement'),
            'root_device_name': instance.get('RootDeviceName'),
            'root_device_type': instance.get('RootDeviceType'),
            'monitoring': instance.get('Monitoring'),
            'launch_time': instance.get('LaunchTime'),
            'tags': instance.get('Tags', []),
        }
        for reservation in reservations
        for instance in reservation.get('Instances', [])
    ]

    vpcs = _clean_response(_safe_value(lambda: ec2.describe_vpcs().get('Vpcs', []), []))
    subnets = _clean_response(_safe_value(lambda: ec2.describe_subnets().get('Subnets', []), []))
    security_groups = _clean_response(_safe_value(lambda: ec2.describe_security_groups().get('SecurityGroups', []), []))
    security_group_rules = _clean_response(_safe_value(lambda: ec2.describe_security_group_rules().get('SecurityGroupRules', []), []))
    key_pairs = _clean_response(_safe_value(lambda: ec2.describe_key_pairs().get('KeyPairs', []), []))
    images = _clean_response(_safe_value(lambda: ec2.describe_images().get('Images', []), []))
    tags = _clean_response(_safe_value(lambda: ec2.describe_tags().get('Tags', []), []))
    internet_gateways = _clean_response(_safe_value(lambda: ec2.describe_internet_gateways().get('InternetGateways', []), []))
    route_tables = _clean_response(_safe_value(lambda: ec2.describe_route_tables().get('RouteTables', []), []))
    network_acls = _clean_response(_safe_value(lambda: ec2.describe_network_acls().get('NetworkAcls', []), []))
    nat_gateways = _clean_response(_safe_value(
        lambda: core._paginate(ec2, 'describe_nat_gateways', 'NatGateways'),
        [],
    ))
    network_interfaces = _clean_response(_safe_value(
        lambda: core._paginate(ec2, 'describe_network_interfaces', 'NetworkInterfaces'),
        [],
    ))
    prefix_lists = _clean_response(_safe_value(lambda: ec2.describe_prefix_lists().get('PrefixLists', []), []))
    vpc_endpoints = _clean_response(_safe_value(lambda: core._paginate(ec2, 'describe_vpc_endpoints', 'VpcEndpoints'), []))
    addresses = _clean_response(_safe_value(lambda: ec2.describe_addresses().get('Addresses', []), []))
    iam_instance_profile_associations = _clean_response(_safe_value(
        lambda: core._paginate(ec2, 'describe_iam_instance_profile_associations', 'IamInstanceProfileAssociations'),
        [],
    ))
    availability_zones = _clean_response(_safe_value(lambda: ec2.describe_availability_zones().get('AvailabilityZones', []), []))
    regions = _clean_response(_safe_value(lambda: ec2.describe_regions().get('Regions', []), []))
    account_attributes = _clean_response(_safe_value(lambda: ec2.describe_account_attributes().get('AccountAttributes', []), []))
    instance_types = _clean_response(_safe_value(lambda: ec2.describe_instance_types().get('InstanceTypes', []), []))
    flow_logs = _clean_response(_safe_value(
        lambda: core._paginate(ec2, 'describe_flow_logs', 'FlowLogs'),
        [],
    )) if 'DescribeFlowLogs' in operations else []
    snapshots = _clean_response(_safe_value(
        lambda: core._paginate(ec2, 'describe_snapshots', 'Snapshots'),
        [],
    )) if 'DescribeSnapshots' in operations else []
    volumes = _clean_response(_safe_value(lambda: core._paginate(ec2, 'describe_volumes', 'Volumes'), []))
    launch_templates = _clean_response(_safe_value(
        lambda: core._paginate(ec2, 'describe_launch_templates', 'LaunchTemplates'),
        [],
    ))
    launch_template_versions = []
    for template in launch_templates:
        template_id = template.get('LaunchTemplateId')
        if not template_id:
            continue
        versions = _safe_value(
            lambda template_id=template_id: core._paginate(
                ec2,
                'describe_launch_template_versions',
                'LaunchTemplateVersions',
                LaunchTemplateId=template_id,
                Versions=[
                    str(number)
                    for number in range(1, int(template.get('LatestVersionNumber') or 1) + 1)
                ],
            ),
            [],
        )
        launch_template_versions.extend(_clean_response(versions))
    spot_instance_requests = _clean_response(_safe_value(
        lambda: core._paginate(ec2, 'describe_spot_instance_requests', 'SpotInstanceRequests'),
        [],
    ))

    diagnostics = []
    vpc_ids = {vpc.get('VpcId') for vpc in vpcs if vpc.get('VpcId')}
    attached_igws = {
        attachment.get('VpcId')
        for gateway in internet_gateways
        for attachment in gateway.get('Attachments', [])
        if attachment.get('VpcId')
    }
    routed_subnets = {
        association.get('SubnetId')
        for table in route_tables
        for association in table.get('Associations', [])
        if association.get('SubnetId')
    }
    vpcs_with_main_routes = {
        table.get('VpcId')
        for table in route_tables
        if any(association.get('Main') for association in table.get('Associations', []))
    }
    acl_subnets = {
        association.get('SubnetId')
        for acl in network_acls
        for association in acl.get('Associations', [])
        if association.get('SubnetId')
    }
    for vpc in vpcs:
        if vpc.get('VpcId') not in attached_igws:
            diagnostics.append({
                'severity': 'warning',
                'resource_type': 'vpc',
                'resource_id': vpc.get('VpcId'),
                'title': 'No internet gateway attached',
                'detail': 'Instances in this VPC do not have an internet-gateway path.',
            })
    for subnet_item in subnets:
        subnet_id = subnet_item.get('SubnetId')
        vpc_id = subnet_item.get('VpcId')
        if subnet_id not in routed_subnets and vpc_id not in vpcs_with_main_routes:
            diagnostics.append({
                'severity': 'error',
                'resource_type': 'subnet',
                'resource_id': subnet_id,
                'title': 'No route table association',
                'detail': 'Associate this subnet with a route table or create a main route table for its VPC.',
            })
        if subnet_id not in acl_subnets:
            diagnostics.append({
                'severity': 'warning',
                'resource_type': 'subnet',
                'resource_id': subnet_id,
                'title': 'No network ACL association',
                'detail': 'The subnet is not represented in any current network ACL association.',
            })
    for group in security_groups:
        if group.get('GroupName') != 'default' and not group.get('IpPermissions'):
            diagnostics.append({
                'severity': 'info',
                'resource_type': 'security_group',
                'resource_id': group.get('GroupId'),
                'title': 'No inbound rules',
                'detail': 'Instances using this security group cannot publish CIDR-sourced TCP application ports.',
            })
    for address in addresses:
        if not address.get('AssociationId') and not address.get('InstanceId'):
            diagnostics.append({
                'severity': 'info',
                'resource_type': 'elastic_ip',
                'resource_id': address.get('AllocationId') or address.get('PublicIp'),
                'title': 'Elastic IP is unassociated',
                'detail': 'Associate it with an instance or release it when it is no longer needed.',
            })
    for resource_type, resource_id, vpc_id in [
        *[('security_group', group.get('GroupId'), group.get('VpcId')) for group in security_groups],
        *[('route_table', table.get('RouteTableId'), table.get('VpcId')) for table in route_tables],
        *[('network_acl', acl.get('NetworkAclId'), acl.get('VpcId')) for acl in network_acls],
    ]:
        if vpc_id and vpc_id not in vpc_ids:
            diagnostics.append({
                'severity': 'warning',
                'resource_type': resource_type,
                'resource_id': resource_id,
                'title': 'Resource references a missing VPC',
                'detail': f'{resource_id} still references deleted VPC {vpc_id}. This can indicate an emulator cleanup gap.',
            })

    return {
        'summary': {
            'instances': len(instances),
            'vpcs': len(vpcs),
            'subnets': len(subnets),
            'security_groups': len(security_groups),
            'internet_gateways': len(internet_gateways),
            'route_tables': len(route_tables),
            'network_acls': len(network_acls),
            'nat_gateways': len(nat_gateways),
            'network_interfaces': len(network_interfaces),
            'vpc_endpoints': len(vpc_endpoints),
            'elastic_ips': len(addresses),
            'key_pairs': len(key_pairs),
            'iam_instance_profile_associations': len(iam_instance_profile_associations),
            'flow_logs': len(flow_logs),
            'snapshots': len(snapshots),
            'volumes': len(volumes),
            'launch_templates': len(launch_templates),
            'spot_requests': len(spot_instance_requests),
        },
        'instances': instances,
        'vpcs': vpcs,
        'subnets': subnets,
        'security_groups': security_groups,
        'security_group_rules': security_group_rules,
        'key_pairs': key_pairs,
        'images': images,
        'tags': tags,
        'internet_gateways': internet_gateways,
        'route_tables': route_tables,
        'network_acls': network_acls,
        'nat_gateways': nat_gateways,
        'network_interfaces': network_interfaces,
        'prefix_lists': prefix_lists,
        'vpc_endpoints': vpc_endpoints,
        'addresses': addresses,
        'iam_instance_profile_associations': iam_instance_profile_associations,
        'availability_zones': availability_zones,
        'regions': regions,
        'account_attributes': account_attributes,
        'instance_types': instance_types,
        'flow_logs': flow_logs,
        'snapshots': snapshots,
        'volumes': volumes,
        'launch_templates': launch_templates,
        'launch_template_versions': launch_template_versions,
        'spot_instance_requests': spot_instance_requests,
        'diagnostics': diagnostics,
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateFlowLogs',
                'DescribeFlowLogs',
                'DeleteFlowLogs',
                'DescribeImages',
                'DescribeInstanceTypes',
                'DescribeInstanceAttribute',
                'DescribeSecurityGroupRules',
                'DescribeSnapshots',
            ]
            if operation in operations
        ],
        'default_resources': [
            {'type': 'Default VPC', 'id': 'vpc-default', 'details': 'CIDR 172.31.0.0/16'},
            {'type': 'Default Subnet', 'id': 'subnet-default-a', 'details': 'CIDR 172.31.0.0/20'},
            {'type': 'Default Subnet', 'id': 'subnet-default-b', 'details': 'CIDR 172.31.16.0/20'},
            {'type': 'Default Subnet', 'id': 'subnet-default-c', 'details': 'CIDR 172.31.32.0/20'},
            {'type': 'Default Security Group', 'id': 'sg-default', 'details': 'groupName=default, all-traffic egress'},
            {'type': 'Default Internet Gateway', 'id': 'igw-default', 'details': 'Attached to default VPC'},
            {'type': 'Main Route Table', 'id': 'rtb-default', 'details': 'Associated with default VPC'},
        ],
        'notes': [
            'RunInstances launches a Docker-backed local instance unless EC2 mock mode is enabled.',
            'DescribeImages returns a static list of common AMIs and Floci-native AMI IDs.',
            'CreateKeyPair returns dummy RSA PEM material that is not usable for real SSH.',
            'Use ImportKeyPair with a real public key for working SSH access.',
            'Floci serves IMDS-compatible metadata on the configured host IMDS port, default 9169.',
            'Security group rules are stored and returned but local Docker networking handles enforcement.',
            'Floci 1.5.30 adds VPC Flow Logs and publishes security-group TCP ingress ports on the host.',
            'Floci 1.5.31 adds instance metadata catalog architecture parity plus registered images and snapshots support.',
            'Floci 1.5.27 adds EC2 Network ACL support for VPC subnet traffic-control workflows.',
            'Floci 1.5.28 adds a Java-built Ubuntu AMI guest image, instance type metadata catalog, VPC endpoint compatibility improvements, and persisted default egress security group rules.',
            'Floci 1.5.26 adds Spot Instance request actions and embedded DNS support for instances.',
            'Floci 1.5.25 embeds the EC2 image catalog in the native image and loads it lazily.',
            'Floci 1.5.21 improves EC2 parity for VPC, subnet, and instance methods, subnet attributes, block-device attachTime, volume throughput, DescribeAddressesAttribute, and IAM instance profiles.',
            'Floci 1.5.24 auto-creates the default security group and main route table when CreateVpc is used.',
        ],
    }


def list_ec2_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    ec2 = factory.client('ec2')
    resources: list[dict[str, Any]] = []
    reservations = _safe_value(lambda: ec2.describe_instances().get('Reservations', []), [])
    resources.extend(
        {
            'type': 'instance',
            'id': instance.get('InstanceId'),
            'state': instance.get('State', {}).get('Name'),
        }
        for reservation in reservations
        for instance in reservation.get('Instances', [])
    )
    resources.extend({'type': 'vpc', 'id': item.get('VpcId')} for item in _safe_value(lambda: ec2.describe_vpcs().get('Vpcs', []), []))
    resources.extend({'type': 'subnet', 'id': item.get('SubnetId')} for item in _safe_value(lambda: ec2.describe_subnets().get('Subnets', []), []))
    resources.extend({'type': 'security_group', 'id': item.get('GroupId')} for item in _safe_value(lambda: ec2.describe_security_groups().get('SecurityGroups', []), []))
    resources.extend({'type': 'internet_gateway', 'id': item.get('InternetGatewayId')} for item in _safe_value(lambda: ec2.describe_internet_gateways().get('InternetGateways', []), []))
    resources.extend({'type': 'route_table', 'id': item.get('RouteTableId')} for item in _safe_value(lambda: ec2.describe_route_tables().get('RouteTables', []), []))
    resources.extend({'type': 'network_acl', 'id': item.get('NetworkAclId')} for item in _safe_value(lambda: ec2.describe_network_acls().get('NetworkAcls', []), []))
    resources.extend({'type': 'vpc_endpoint', 'id': item.get('VpcEndpointId')} for item in _safe_value(lambda: core._paginate(ec2, 'describe_vpc_endpoints', 'VpcEndpoints'), []))
    resources.extend({'type': 'elastic_ip', 'id': item.get('AllocationId') or item.get('PublicIp')} for item in _safe_value(lambda: ec2.describe_addresses().get('Addresses', []), []))
    resources.extend({'type': 'key_pair', 'id': item.get('KeyName')} for item in _safe_value(lambda: ec2.describe_key_pairs().get('KeyPairs', []), []))
    return resources
