"""EC2 networking workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from . import ec2_nat_labs as nat_three_tier
from . import ec2_network_labs as network_scale_out
from . import ec2_shared_responsibility_labs as shared_responsibility
from . import ec2_storage_labs as block_file_storage
from .ec2_guided import GUIDED_EC2_LABS
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _ec2_client,
    _error_code,
    _json_text,
    _s3_client,
    _sqs_client,
    _sqs_queue_url,
)


EC2_VPC_CIDR = '10.42.0.0/16'
EC2_PUBLIC_SUBNET_CIDR = '10.42.1.0/24'
EC2_PRIVATE_SUBNET_CIDR = '10.42.2.0/24'
EC2_PUBLIC_AZ = 'us-east-1a'
EC2_PRIVATE_AZ = 'us-east-1b'
EC2_VPC_TAGS = [
    {'Key': 'Name', 'Value': 'lab-vpc'},
    {'Key': 'Lab', 'Value': 'vpc-public-private'},
]
EC2_VPC_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:vpc-id'
EC2_PUBLIC_SUBNET_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:public-subnet-id'
EC2_PRIVATE_SUBNET_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:private-subnet-id'
EC2_IGW_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:igw-id'
EC2_PUBLIC_RT_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:public-rt-id'
EC2_PRIVATE_RT_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:private-rt-id'
EC2_PUBLIC_ASSOC_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:public-assoc-id'
EC2_PRIVATE_ASSOC_ID_CACHE_KEY = 'floci-lab:ec2:vpc-networking:private-assoc-id'
EC2_SECURITY_VPC_CIDR = '10.43.0.0/16'
EC2_SECURITY_SUBNET_CIDR = '10.43.1.0/24'
EC2_SECURITY_AZ = 'us-east-1a'
EC2_SECURITY_VPC_TAGS = [
    {'Key': 'Name', 'Value': 'lab-security-vpc'},
    {'Key': 'Lab', 'Value': 'security-controls'},
]
EC2_WEB_SG_NAME = 'lab-web-sg'
EC2_APP_SG_NAME = 'lab-app-sg'
EC2_TRUSTED_CLIENT_CIDR = '203.0.113.0/24'
EC2_NACL_DESIGN = {
    'InboundRules': [
        {
            'RuleNumber': 90,
            'Protocol': 'tcp',
            'RuleAction': 'deny',
            'CidrBlock': '0.0.0.0/0',
            'PortRange': {'From': 22, 'To': 22},
        },
        {
            'RuleNumber': 100,
            'Protocol': 'tcp',
            'RuleAction': 'allow',
            'CidrBlock': EC2_TRUSTED_CLIENT_CIDR,
            'PortRange': {'From': 443, 'To': 443},
        },
    ],
    'OutboundRules': [
        {
            'RuleNumber': 100,
            'Protocol': 'tcp',
            'RuleAction': 'allow',
            'CidrBlock': EC2_TRUSTED_CLIENT_CIDR,
            'PortRange': {'From': 1024, 'To': 65535},
        },
    ],
    'DefaultRule': {
        'RuleNumber': '*',
        'RuleAction': 'deny',
    },
}
EC2_SECURITY_VPC_ID_CACHE_KEY = 'floci-lab:ec2:security-controls:vpc-id'
EC2_SECURITY_SUBNET_ID_CACHE_KEY = 'floci-lab:ec2:security-controls:subnet-id'
EC2_WEB_SG_ID_CACHE_KEY = 'floci-lab:ec2:security-controls:web-sg-id'
EC2_APP_SG_ID_CACHE_KEY = 'floci-lab:ec2:security-controls:app-sg-id'
EC2_SG_REFERENCE_CACHE_KEY = 'floci-lab:ec2:security-controls:sg-reference'
EC2_NACL_ID_CACHE_KEY = 'floci-lab:ec2:security-controls:nacl-id'
EC2_NACL_ASSOC_ID_CACHE_KEY = 'floci-lab:ec2:security-controls:nacl-association-id'
EC2_ENDPOINT_VPC_CIDR = '10.44.0.0/16'
EC2_ENDPOINT_SUBNET_CIDR = '10.44.1.0/24'
EC2_ENDPOINT_AZ = 'us-east-1a'
EC2_ENDPOINT_BUCKET_NAME = 'lab-private-s3-data'
EC2_ENDPOINT_VPC_TAGS = [
    {'Key': 'Name', 'Value': 'lab-s3-endpoint-vpc'},
    {'Key': 'Lab', 'Value': 's3-gateway-endpoint'},
]
EC2_ENDPOINT_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Principal': '*',
        'Action': ['s3:ListBucket', 's3:GetObject'],
        'Resource': [
            f'arn:aws:s3:::{EC2_ENDPOINT_BUCKET_NAME}',
            f'arn:aws:s3:::{EC2_ENDPOINT_BUCKET_NAME}/*',
        ],
    }],
}
EC2_ENDPOINT_VPC_ID_CACHE_KEY = 'floci-lab:ec2:s3-endpoint:vpc-id'
EC2_ENDPOINT_SUBNET_ID_CACHE_KEY = 'floci-lab:ec2:s3-endpoint:subnet-id'
EC2_ENDPOINT_RT_ID_CACHE_KEY = 'floci-lab:ec2:s3-endpoint:route-table-id'
EC2_ENDPOINT_ASSOC_ID_CACHE_KEY = 'floci-lab:ec2:s3-endpoint:association-id'
EC2_ENDPOINT_ID_CACHE_KEY = 'floci-lab:ec2:s3-endpoint:endpoint-id'
EC2_ENDPOINT_CONFIGURED_CACHE_KEY = 'floci-lab:ec2:s3-endpoint:configured'
EC2_ENDPOINT_ROUTE_BOUNDARY_CACHE_KEY = 'floci-lab:ec2:s3-endpoint:route-boundary'
EC2_INTERFACE_VPC_CIDR = '10.45.0.0/16'
EC2_INTERFACE_SUBNET_CIDR = '10.45.1.0/24'
EC2_INTERFACE_AZ = 'us-east-1a'
EC2_INTERFACE_QUEUE_NAME = 'lab-private-sqs'
EC2_INTERFACE_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{EC2_INTERFACE_QUEUE_NAME}'
)
EC2_INTERFACE_SG_NAME = 'lab-sqs-endpoint-sg'
EC2_INTERFACE_VPC_TAGS = [
    {'Key': 'Name', 'Value': 'lab-sqs-interface-endpoint-vpc'},
    {'Key': 'Lab', 'Value': 'sqs-interface-endpoint'},
]
EC2_INTERFACE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Principal': '*',
        'Action': ['sqs:GetQueueAttributes', 'sqs:SendMessage'],
        'Resource': EC2_INTERFACE_QUEUE_ARN,
    }],
}
EC2_INTERFACE_VPC_ID_CACHE_KEY = 'floci-lab:ec2:sqs-interface:vpc-id'
EC2_INTERFACE_SUBNET_ID_CACHE_KEY = 'floci-lab:ec2:sqs-interface:subnet-id'
EC2_INTERFACE_SG_ID_CACHE_KEY = 'floci-lab:ec2:sqs-interface:sg-id'
EC2_INTERFACE_ENDPOINT_ID_CACHE_KEY = 'floci-lab:ec2:sqs-interface:endpoint-id'
EC2_INTERFACE_CONFIGURED_CACHE_KEY = 'floci-lab:ec2:sqs-interface:configured'
EC2_INTERFACE_INSPECTED_CACHE_KEY = 'floci-lab:ec2:sqs-interface:inspected'
EC2_VPC_NETWORKING_LAB = {
    'service': 'ec2',
    'key': 'vpc-public-private',
    'title': 'Build a VPC with public and private subnets',
    'description': 'Create an isolated network, give one subnet an internet route and public-IP behavior, and keep the second subnet private.',
    'steps': [
        {
            'key': 'create-vpc',
            'title': 'Create the VPC',
            'command': f'aws ec2 create-vpc --cidr-block {EC2_VPC_CIDR} --tag-specifications file://vpc-tags.json',
            'explanation': 'Creates the private IPv4 address space for the complete topology.',
            'artifact_label': 'vpc-tags.json',
            'artifact': json.dumps([{
                'ResourceType': 'vpc',
                'Tags': EC2_VPC_TAGS,
            }], indent=2),
        },
        {
            'key': 'create-public-subnet',
            'title': 'Create the public subnet',
            'command': f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {EC2_PUBLIC_SUBNET_CIDR} --availability-zone {EC2_PUBLIC_AZ}',
            'explanation': 'Allocates a /24 subnet intended for resources that can use an internet route.',
        },
        {
            'key': 'enable-public-ip',
            'title': 'Enable public IP assignment',
            'command': 'aws ec2 modify-subnet-attribute --subnet-id <public-subnet-id> --map-public-ip-on-launch',
            'explanation': 'Makes newly launched instances in the public subnet request public IPv4 addresses.',
        },
        {
            'key': 'create-private-subnet',
            'title': 'Create the private subnet',
            'command': f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {EC2_PRIVATE_SUBNET_CIDR} --availability-zone {EC2_PRIVATE_AZ}',
            'explanation': 'Allocates a separate /24 subnet without automatic public IP assignment.',
        },
        {
            'key': 'create-internet-gateway',
            'title': 'Create an internet gateway',
            'command': 'aws ec2 create-internet-gateway',
            'explanation': 'Creates the horizontally scaled VPC edge used for internet routing.',
        },
        {
            'key': 'attach-internet-gateway',
            'title': 'Attach the gateway to the VPC',
            'command': 'aws ec2 attach-internet-gateway --internet-gateway-id <igw-id> --vpc-id <vpc-id>',
            'explanation': 'Attaches the internet gateway to this VPC; attachment alone does not make a subnet public.',
        },
        {
            'key': 'create-public-route-table',
            'title': 'Create the public route table',
            'command': 'aws ec2 create-route-table --vpc-id <vpc-id>',
            'explanation': 'Creates a dedicated route table for the public subnet.',
        },
        {
            'key': 'create-internet-route',
            'title': 'Add the default internet route',
            'command': 'aws ec2 create-route --route-table-id <public-route-table-id> --destination-cidr-block 0.0.0.0/0 --gateway-id <igw-id>',
            'explanation': 'Routes non-local IPv4 traffic from the public route table to the internet gateway.',
        },
        {
            'key': 'associate-public-route-table',
            'title': 'Associate the public subnet',
            'command': 'aws ec2 associate-route-table --route-table-id <public-route-table-id> --subnet-id <public-subnet-id>',
            'explanation': 'Makes the public subnet use the route table containing the internet route.',
        },
        {
            'key': 'create-private-route-table',
            'title': 'Create the private route table',
            'command': 'aws ec2 create-route-table --vpc-id <vpc-id>',
            'explanation': 'Creates a separate route table whose only route is the automatic local VPC route.',
        },
        {
            'key': 'associate-private-route-table',
            'title': 'Associate the private subnet',
            'command': 'aws ec2 associate-route-table --route-table-id <private-route-table-id> --subnet-id <private-subnet-id>',
            'explanation': 'Keeps private-subnet traffic on the route table with no internet-gateway default route.',
        },
        {
            'key': 'inspect-network-topology',
            'title': 'Inspect the complete topology',
            'command': 'aws ec2 describe-vpcs --vpc-ids <vpc-id>\naws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id>\naws ec2 describe-internet-gateways --filters Name=attachment.vpc-id,Values=<vpc-id>\naws ec2 describe-route-tables --filters Name=vpc-id,Values=<vpc-id>',
            'explanation': 'Proves the public/private distinction from subnet attributes, route-table associations, and the presence or absence of 0.0.0.0/0.',
        },
    ],
}

EC2_SECURITY_CONTROLS_LAB = {
    'service': 'ec2',
    'key': 'security-controls',
    'title': 'Control VPC traffic with security groups and network ACLs',
    'description': 'Build stateful security-group rules, use one group as another group’s source, and apply stateless network ACL rules to the subnet.',
    'steps': [
        {
            'key': 'create-vpc',
            'title': 'Create the security VPC',
            'command': f'aws ec2 create-vpc --cidr-block {EC2_SECURITY_VPC_CIDR} --tag-specifications file://vpc-tags.json',
            'explanation': 'Creates an isolated VPC owned only by this traffic-control lab.',
        },
        {
            'key': 'create-subnet',
            'title': 'Create the application subnet',
            'command': f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {EC2_SECURITY_SUBNET_CIDR} --availability-zone {EC2_SECURITY_AZ}',
            'explanation': 'Creates the subnet whose traffic controls are being designed.',
        },
        {
            'key': 'create-web-security-group',
            'title': 'Create the web security group',
            'command': f'aws ec2 create-security-group --group-name {EC2_WEB_SG_NAME} --description "HTTPS web tier" --vpc-id <vpc-id>',
            'explanation': 'Creates a stateful virtual firewall for the public-facing web tier.',
        },
        {
            'key': 'allow-trusted-https',
            'title': 'Allow HTTPS from trusted clients',
            'command': 'aws ec2 authorize-security-group-ingress --group-id <web-sg-id> --ip-permissions file://trusted-https.json',
            'explanation': 'Allows TCP 443 only from the documentation CIDR 203.0.113.0/24.',
            'artifact_label': 'trusted-https.json',
            'artifact': json.dumps([{
                'IpProtocol': 'tcp',
                'FromPort': 443,
                'ToPort': 443,
                'IpRanges': [{
                    'CidrIp': EC2_TRUSTED_CLIENT_CIDR,
                    'Description': 'Trusted HTTPS clients',
                }],
            }], indent=2),
        },
        {
            'key': 'create-app-security-group',
            'title': 'Create the application security group',
            'command': f'aws ec2 create-security-group --group-name {EC2_APP_SG_NAME} --description "Private application tier" --vpc-id <vpc-id>',
            'explanation': 'Creates a separate stateful firewall for the private application tier.',
        },
        {
            'key': 'allow-web-to-app',
            'title': 'Allow web-to-app traffic',
            'command': 'aws ec2 authorize-security-group-ingress --group-id <app-sg-id> --ip-permissions file://web-to-app.json',
            'explanation': 'Allows TCP 8080 only when the source network interface uses the web security group.',
            'artifact_label': 'web-to-app.json',
            'artifact': json.dumps([{
                'IpProtocol': 'tcp',
                'FromPort': 8080,
                'ToPort': 8080,
                'UserIdGroupPairs': [{
                    'GroupId': '<web-sg-id>',
                    'Description': 'Web tier to application tier',
                }],
            }], indent=2),
        },
        {
            'key': 'inspect-security-groups',
            'title': 'Inspect stateful rules',
            'command': 'aws ec2 describe-security-groups --group-ids <web-sg-id> <app-sg-id>\naws ec2 describe-security-group-rules --filters Name=group-id,Values=<web-sg-id>,<app-sg-id>',
            'explanation': 'Verifies the CIDR-based HTTPS rule, security-group reference, and default outbound behavior.',
        },
        {
            'key': 'create-network-acl',
            'title': 'Create a custom network ACL',
            'command': 'aws ec2 create-network-acl --vpc-id <vpc-id>',
            'explanation': 'Creates a stateless subnet firewall separate from the default VPC network ACL.',
        },
        {
            'key': 'deny-ssh-network-acl',
            'title': 'Deny inbound SSH first',
            'command': 'aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> --rule-number 90 --protocol 6 --rule-action deny --ingress --cidr-block 0.0.0.0/0 --port-range From=22,To=22',
            'explanation': 'Adds the lower-numbered stateless deny rule that is evaluated before the allow rule.',
        },
        {
            'key': 'allow-https-network-acl',
            'title': 'Allow inbound HTTPS from trusted clients',
            'command': f'aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> --rule-number 100 --protocol 6 --rule-action allow --ingress --cidr-block {EC2_TRUSTED_CLIENT_CIDR} --port-range From=443,To=443',
            'explanation': 'Allows TCP 443 from the trusted documentation CIDR after the explicit SSH deny.',
        },
        {
            'key': 'allow-return-network-acl',
            'title': 'Allow stateless return traffic',
            'command': f'aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> --rule-number 100 --protocol 6 --rule-action allow --egress --cidr-block {EC2_TRUSTED_CLIENT_CIDR} --port-range From=1024,To=65535',
            'explanation': 'Because network ACLs are stateless, outbound ephemeral ports need their own explicit allow rule.',
        },
        {
            'key': 'associate-network-acl',
            'title': 'Associate the custom network ACL',
            'command': 'aws ec2 replace-network-acl-association --association-id <default-association-id> --network-acl-id <network-acl-id>',
            'explanation': 'Replaces the subnet’s default network ACL association with the custom rule set.',
        },
        {
            'key': 'inspect-network-acl-support',
            'title': 'Inspect stateless NACL rules',
            'command': 'aws ec2 describe-network-acls --filters Name=vpc-id,Values=<vpc-id>',
            'explanation': 'Verifies the custom network ACL association and ordered stateless inbound and outbound rules.',
            'artifact_label': 'network-acl-design.json',
            'artifact': json.dumps(EC2_NACL_DESIGN, indent=2),
        },
    ],
}

EC2_S3_GATEWAY_ENDPOINT_LAB = {
    'service': 'ec2',
    'key': 's3-gateway-endpoint',
    'title': 'Connect a private VPC to S3 with a gateway endpoint',
    'description': 'Give an isolated private subnet an S3 path without an internet gateway, NAT gateway, public IP, or endpoint security group.',
    'steps': [
        {
            'key': 'create-vpc',
            'title': 'Create the endpoint VPC',
            'command': f'aws ec2 create-vpc --cidr-block {EC2_ENDPOINT_VPC_CIDR} --tag-specifications file://vpc-tags.json',
            'explanation': 'Creates a dedicated VPC with no internet gateway or NAT gateway.',
        },
        {
            'key': 'create-private-subnet',
            'title': 'Create the private subnet',
            'command': f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {EC2_ENDPOINT_SUBNET_CIDR} --availability-zone {EC2_ENDPOINT_AZ}',
            'explanation': 'Creates an isolated subnet with no automatic public IP assignment.',
        },
        {
            'key': 'create-private-route-table',
            'title': 'Create the private route table',
            'command': 'aws ec2 create-route-table --vpc-id <vpc-id>',
            'explanation': 'Creates the route table that the gateway endpoint will modify in AWS.',
        },
        {
            'key': 'associate-private-route-table',
            'title': 'Associate the private subnet',
            'command': 'aws ec2 associate-route-table --route-table-id <route-table-id> --subnet-id <subnet-id>',
            'explanation': 'Makes the private subnet use the route table selected for S3 connectivity.',
        },
        {
            'key': 'create-s3-bucket',
            'title': 'Create the private data bucket',
            'command': f'aws s3api create-bucket --bucket {EC2_ENDPOINT_BUCKET_NAME}',
            'explanation': 'Creates the bucket named in the endpoint policy.',
        },
        {
            'key': 'create-s3-gateway-endpoint',
            'title': 'Create the S3 gateway endpoint',
            'command': 'aws ec2 create-vpc-endpoint --vpc-id <vpc-id> --vpc-endpoint-type Gateway --service-name com.amazonaws.us-east-1.s3 --route-table-ids <route-table-id> --policy-document file://endpoint-policy.json',
            'explanation': 'Associates the regional S3 gateway service with the private route table and limits endpoint use to the lab bucket.',
            'artifact_label': 'endpoint-policy.json',
            'artifact': json.dumps(EC2_ENDPOINT_POLICY, indent=2),
        },
        {
            'key': 'inspect-private-s3-path',
            'title': 'Inspect the private S3 path',
            'command': 'aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <endpoint-id>\naws ec2 describe-route-tables --route-table-ids <route-table-id>\naws s3api head-bucket --bucket lab-private-s3-data',
            'explanation': 'Verifies the endpoint and bucket, then examines the route-table behavior. AWS injects an S3 prefix-list route; Floci currently does not persist that managed route.',
        },
    ],
}

EC2_SQS_INTERFACE_ENDPOINT_LAB = {
    'service': 'ec2',
    'key': 'sqs-interface-endpoint',
    'title': 'Connect a private subnet to SQS with an interface endpoint',
    'description': 'Place an SQS endpoint in a private subnet, protect its endpoint network interfaces with HTTPS-only security-group ingress, and enable private DNS.',
    'steps': [
        {
            'key': 'create-vpc',
            'title': 'Create the interface endpoint VPC',
            'command': f'aws ec2 create-vpc --cidr-block {EC2_INTERFACE_VPC_CIDR} --tag-specifications file://vpc-tags.json',
            'explanation': 'Creates a dedicated VPC with no internet gateway or NAT gateway.',
        },
        {
            'key': 'create-private-subnet',
            'title': 'Create the private subnet',
            'command': f'aws ec2 create-subnet --vpc-id <vpc-id> --cidr-block {EC2_INTERFACE_SUBNET_CIDR} --availability-zone {EC2_INTERFACE_AZ}',
            'explanation': 'Creates the subnet where the interface endpoint network interface will live.',
        },
        {
            'key': 'create-endpoint-security-group',
            'title': 'Create the endpoint security group',
            'command': f'aws ec2 create-security-group --group-name {EC2_INTERFACE_SG_NAME} --description "Private SQS endpoint HTTPS" --vpc-id <vpc-id>',
            'explanation': 'Creates the stateful firewall attached to the endpoint network interface.',
        },
        {
            'key': 'allow-vpc-https',
            'title': 'Allow HTTPS from the VPC',
            'command': 'aws ec2 authorize-security-group-ingress --group-id <endpoint-sg-id> --ip-permissions file://endpoint-https.json',
            'explanation': 'Allows private clients in the VPC CIDR to reach the endpoint on TCP 443.',
            'artifact_label': 'endpoint-https.json',
            'artifact': json.dumps([{
                'IpProtocol': 'tcp',
                'FromPort': 443,
                'ToPort': 443,
                'IpRanges': [{
                    'CidrIp': EC2_INTERFACE_VPC_CIDR,
                    'Description': 'HTTPS from private VPC clients',
                }],
            }], indent=2),
        },
        {
            'key': 'create-sqs-queue',
            'title': 'Create the private SQS target',
            'command': f'aws sqs create-queue --queue-name {EC2_INTERFACE_QUEUE_NAME}',
            'explanation': 'Creates the only queue allowed by the endpoint policy.',
        },
        {
            'key': 'create-sqs-interface-endpoint',
            'title': 'Create the SQS interface endpoint',
            'command': 'aws ec2 create-vpc-endpoint --vpc-id <vpc-id> --vpc-endpoint-type Interface --service-name com.amazonaws.us-east-1.sqs --subnet-ids <subnet-id> --security-group-ids <endpoint-sg-id> --private-dns-enabled --policy-document file://endpoint-policy.json',
            'explanation': 'Places the regional SQS service endpoint in the private subnet with private DNS and queue-scoped permissions.',
            'artifact_label': 'endpoint-policy.json',
            'artifact': json.dumps(EC2_INTERFACE_POLICY, indent=2),
        },
        {
            'key': 'inspect-private-sqs-path',
            'title': 'Inspect the private SQS path',
            'command': 'aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <endpoint-id>\naws ec2 describe-network-interfaces --network-interface-ids <eni-id>\naws sqs get-queue-attributes --queue-url <queue-url> --attribute-names QueueArn',
            'explanation': 'Checks endpoint subnet placement, security groups, private DNS, endpoint ENIs, and the queue protected by the endpoint policy.',
        },
    ],
}


def _ec2_lab_vpc() -> dict[str, Any] | None:
    response = _ec2_client().describe_vpcs(
        Filters=[
            {'Name': 'cidr-block', 'Values': [EC2_VPC_CIDR]},
            {'Name': 'tag:Lab', 'Values': ['vpc-public-private']},
        ],
    )
    return next(iter(response.get('Vpcs', [])), None)


def _ec2_lab_subnets(vpc_id: str) -> dict[str, dict[str, Any]]:
    response = _ec2_client().describe_subnets(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}],
    )
    return {
        subnet.get('CidrBlock'): subnet
        for subnet in response.get('Subnets', [])
    }


def _ec2_lab_igw(vpc_id: str) -> dict[str, Any] | None:
    response = _ec2_client().describe_internet_gateways(
        Filters=[{'Name': 'attachment.vpc-id', 'Values': [vpc_id]}],
    )
    return next(iter(response.get('InternetGateways', [])), None)


def _ec2_lab_route_tables(vpc_id: str) -> list[dict[str, Any]]:
    return _ec2_client().describe_route_tables(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}],
    ).get('RouteTables', [])


def _verify_ec2_vpc() -> dict[str, Any]:
    try:
        vpc = _ec2_lab_vpc()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if vpc and vpc.get('State') == 'available':
        cache.set(EC2_VPC_ID_CACHE_KEY, vpc['VpcId'], timeout=86400)
        return {
            'status': 'passed',
            'message': f'The VPC owns the {EC2_VPC_CIDR} address space.',
            'resource': _clean_response(vpc),
        }
    return {'status': 'failed', 'message': 'The lab VPC does not exist.'}


def _verify_ec2_subnets() -> tuple[dict[str, Any], dict[str, Any]]:
    vpc = _ec2_lab_vpc()
    if not vpc:
        failed = {'status': 'failed', 'message': 'Create the VPC first.'}
        return failed, failed
    subnets = _ec2_lab_subnets(vpc['VpcId'])
    public = subnets.get(EC2_PUBLIC_SUBNET_CIDR)
    private = subnets.get(EC2_PRIVATE_SUBNET_CIDR)
    public_verification = (
        {
            'status': 'passed',
            'message': 'The public subnet uses the expected CIDR, AZ, and public-IP setting.',
            'resource': _clean_response(public),
        }
        if public
        and public.get('AvailabilityZone') == EC2_PUBLIC_AZ
        and public.get('MapPublicIpOnLaunch') is True
        else {'status': 'failed', 'message': 'The public subnet is incomplete.'}
    )
    private_verification = (
        {
            'status': 'passed',
            'message': 'The private subnet uses the expected CIDR and has no automatic public IPs.',
            'resource': _clean_response(private),
        }
        if private
        and private.get('AvailabilityZone') == EC2_PRIVATE_AZ
        and private.get('MapPublicIpOnLaunch') is False
        else {'status': 'failed', 'message': 'The private subnet is incomplete.'}
    )
    if public:
        cache.set(EC2_PUBLIC_SUBNET_ID_CACHE_KEY, public['SubnetId'], timeout=86400)
    if private:
        cache.set(EC2_PRIVATE_SUBNET_ID_CACHE_KEY, private['SubnetId'], timeout=86400)
    return public_verification, private_verification


def _verify_ec2_igw() -> dict[str, Any]:
    vpc = _ec2_lab_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    igw = _ec2_lab_igw(vpc['VpcId'])
    if igw:
        cache.set(EC2_IGW_ID_CACHE_KEY, igw['InternetGatewayId'], timeout=86400)
        return {
            'status': 'passed',
            'message': 'The internet gateway is attached to the lab VPC.',
            'resource': _clean_response(igw),
        }
    return {'status': 'failed', 'message': 'No internet gateway is attached.'}


def _verify_ec2_routes() -> tuple[dict[str, Any], dict[str, Any]]:
    vpc = _ec2_lab_vpc()
    if not vpc:
        failed = {'status': 'failed', 'message': 'Create the VPC first.'}
        return failed, failed
    subnets = _ec2_lab_subnets(vpc['VpcId'])
    public_subnet = subnets.get(EC2_PUBLIC_SUBNET_CIDR, {})
    private_subnet = subnets.get(EC2_PRIVATE_SUBNET_CIDR, {})
    igw = _ec2_lab_igw(vpc['VpcId']) or {}
    route_tables = _ec2_lab_route_tables(vpc['VpcId'])

    def associated_table(subnet_id: str | None):
        return next(
            (
                table for table in route_tables
                if any(
                    association.get('SubnetId') == subnet_id
                    for association in table.get('Associations', [])
                )
            ),
            None,
        )

    public_table = associated_table(public_subnet.get('SubnetId'))
    private_table = associated_table(private_subnet.get('SubnetId'))
    public_default = next(
        (
            route for route in (public_table or {}).get('Routes', [])
            if route.get('DestinationCidrBlock') == '0.0.0.0/0'
        ),
        None,
    )
    private_default = next(
        (
            route for route in (private_table or {}).get('Routes', [])
            if route.get('DestinationCidrBlock') == '0.0.0.0/0'
        ),
        None,
    )
    public_verification = (
        {
            'status': 'passed',
            'message': 'The public subnet route table sends 0.0.0.0/0 to the internet gateway.',
            'resource': _clean_response(public_table),
        }
        if public_table
        and public_default
        and public_default.get('GatewayId') == igw.get('InternetGatewayId')
        else {'status': 'failed', 'message': 'The public route is incomplete.'}
    )
    private_verification = (
        {
            'status': 'passed',
            'message': 'The private subnet has an associated route table with no internet default route.',
            'resource': _clean_response(private_table),
        }
        if private_table and not private_default
        else {'status': 'failed', 'message': 'The private route table is incomplete.'}
    )
    if public_table:
        cache.set(EC2_PUBLIC_RT_ID_CACHE_KEY, public_table['RouteTableId'], timeout=86400)
    if private_table:
        cache.set(EC2_PRIVATE_RT_ID_CACHE_KEY, private_table['RouteTableId'], timeout=86400)
    return public_verification, private_verification


def _ec2_cached_id(cache_key: str, label: str) -> str:
    value = cache.get(cache_key)
    if not value:
        raise ValueError(f'{label} is not available; run the prerequisite step.')
    return value


def _ec2_security_vpc() -> dict[str, Any] | None:
    response = _ec2_client().describe_vpcs(
        Filters=[
            {'Name': 'cidr-block', 'Values': [EC2_SECURITY_VPC_CIDR]},
            {'Name': 'tag:Lab', 'Values': ['security-controls']},
        ],
    )
    return next(iter(response.get('Vpcs', [])), None)


def _ec2_security_groups(vpc_id: str) -> dict[str, dict[str, Any]]:
    response = _ec2_client().describe_security_groups(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}],
    )
    return {
        group.get('GroupName'): group
        for group in response.get('SecurityGroups', [])
    }


def _ec2_security_network_acls(vpc_id: str) -> list[dict[str, Any]]:
    response = _ec2_client().describe_network_acls(
        Filters=[{'Name': 'vpc-id', 'Values': [vpc_id]}],
    )
    return response.get('NetworkAcls', [])


def _ec2_security_custom_nacl(vpc_id: str) -> dict[str, Any] | None:
    cached_nacl_id = cache.get(EC2_NACL_ID_CACHE_KEY)
    acls = _ec2_security_network_acls(vpc_id)
    if cached_nacl_id:
        cached_acl = next(
            (acl for acl in acls if acl.get('NetworkAclId') == cached_nacl_id),
            None,
        )
        if cached_acl:
            return cached_acl
    custom_acl = next(
        (acl for acl in acls if not acl.get('IsDefault')),
        None,
    )
    if custom_acl:
        cache.set(EC2_NACL_ID_CACHE_KEY, custom_acl['NetworkAclId'], timeout=86400)
    return custom_acl


def _ec2_default_nacl_association_id(vpc_id: str, subnet_id: str) -> str | None:
    for acl in _ec2_security_network_acls(vpc_id):
        for association in acl.get('Associations', []):
            if association.get('SubnetId') == subnet_id:
                return association.get('NetworkAclAssociationId')
    return None


def _nacl_rule_matches(
    entry: dict[str, Any],
    *,
    egress: bool,
    rule_number: int,
    action: str,
    cidr: str,
    from_port: int,
    to_port: int,
) -> bool:
    port_range = entry.get('PortRange') or {}
    return (
        entry.get('Egress') is egress
        and entry.get('RuleNumber') == rule_number
        and entry.get('RuleAction') == action
        and entry.get('CidrBlock') == cidr
        and str(entry.get('Protocol')) in {'6', 'tcp'}
        and port_range.get('From') == from_port
        and port_range.get('To') == to_port
    )


def _verify_ec2_security_vpc() -> dict[str, Any]:
    try:
        vpc = _ec2_security_vpc()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if vpc:
        cache.set(EC2_SECURITY_VPC_ID_CACHE_KEY, vpc['VpcId'], timeout=86400)
        return {
            'status': 'passed',
            'message': 'The dedicated security-controls VPC exists.',
            'resource': _clean_response(vpc),
        }
    return {'status': 'failed', 'message': 'The security-controls VPC is missing.'}


def _verify_ec2_security_subnet() -> dict[str, Any]:
    vpc = _ec2_security_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    response = _ec2_client().describe_subnets(
        Filters=[
            {'Name': 'vpc-id', 'Values': [vpc['VpcId']]},
            {'Name': 'cidr-block', 'Values': [EC2_SECURITY_SUBNET_CIDR]},
        ],
    )
    subnet = next(iter(response.get('Subnets', [])), None)
    if subnet and subnet.get('AvailabilityZone') == EC2_SECURITY_AZ:
        cache.set(
            EC2_SECURITY_SUBNET_ID_CACHE_KEY,
            subnet['SubnetId'],
            timeout=86400,
        )
        return {
            'status': 'passed',
            'message': 'The application subnet exists in the expected AZ.',
            'resource': _clean_response(subnet),
        }
    return {'status': 'failed', 'message': 'The application subnet is missing.'}


def _verify_ec2_security_group_rules() -> tuple[dict[str, Any], dict[str, Any]]:
    vpc = _ec2_security_vpc()
    if not vpc:
        failed = {'status': 'failed', 'message': 'Create the VPC first.'}
        return failed, failed
    groups = _ec2_security_groups(vpc['VpcId'])
    web = groups.get(EC2_WEB_SG_NAME)
    app = groups.get(EC2_APP_SG_NAME)
    if web:
        cache.set(EC2_WEB_SG_ID_CACHE_KEY, web['GroupId'], timeout=86400)
    if app:
        cache.set(EC2_APP_SG_ID_CACHE_KEY, app['GroupId'], timeout=86400)
    web_rule = next(
        (
            permission for permission in (web or {}).get('IpPermissions', [])
            if permission.get('IpProtocol') == 'tcp'
            and permission.get('FromPort') == 443
            and permission.get('ToPort') == 443
            and any(
                item.get('CidrIp') == EC2_TRUSTED_CLIENT_CIDR
                for item in permission.get('IpRanges', [])
            )
        ),
        None,
    )
    app_rule = next(
        (
            permission for permission in (app or {}).get('IpPermissions', [])
            if permission.get('IpProtocol') == 'tcp'
            and permission.get('FromPort') == 8080
            and permission.get('ToPort') == 8080
        ),
        None,
    )
    app_reference_verified = bool(cache.get(EC2_SG_REFERENCE_CACHE_KEY))
    web_egress = any(
        permission.get('IpProtocol') == '-1'
        for permission in (web or {}).get('IpPermissionsEgress', [])
    )
    app_egress = any(
        permission.get('IpProtocol') == '-1'
        for permission in (app or {}).get('IpPermissionsEgress', [])
    )
    web_verification = (
        {
            'status': 'passed',
            'message': 'The web group allows trusted HTTPS and keeps default stateful egress.',
            'resource': _clean_response(web),
        }
        if web and web_rule and web_egress
        else {'status': 'failed', 'message': 'The web security group is incomplete.'}
    )
    app_verification = (
        {
            'status': 'passed',
            'message': 'The app group allows port 8080 only from the web security group.',
            'resource': _clean_response(app),
        }
        if app and app_rule and app_egress and app_reference_verified
        else {'status': 'failed', 'message': 'The app security group is incomplete.'}
    )
    return web_verification, app_verification


def _verify_ec2_network_acl_created() -> dict[str, Any]:
    vpc = _ec2_security_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    acl = _ec2_security_custom_nacl(vpc['VpcId'])
    if acl:
        return {
            'status': 'passed',
            'message': 'The custom network ACL exists for the security VPC.',
            'resource': _clean_response(acl),
        }
    return {'status': 'failed', 'message': 'The custom network ACL is missing.'}


def _verify_ec2_network_acl_rules() -> dict[str, Any]:
    vpc = _ec2_security_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    acl = _ec2_security_custom_nacl(vpc['VpcId'])
    if not acl:
        return {'status': 'failed', 'message': 'Create the custom network ACL first.'}
    entries = acl.get('Entries', [])
    expected_rules = [
        ('deny inbound SSH', any(_nacl_rule_matches(
            entry,
            egress=False,
            rule_number=90,
            action='deny',
            cidr='0.0.0.0/0',
            from_port=22,
            to_port=22,
        ) for entry in entries)),
        ('allow inbound HTTPS', any(_nacl_rule_matches(
            entry,
            egress=False,
            rule_number=100,
            action='allow',
            cidr=EC2_TRUSTED_CLIENT_CIDR,
            from_port=443,
            to_port=443,
        ) for entry in entries)),
        ('allow outbound ephemeral return traffic', any(_nacl_rule_matches(
            entry,
            egress=True,
            rule_number=100,
            action='allow',
            cidr=EC2_TRUSTED_CLIENT_CIDR,
            from_port=1024,
            to_port=65535,
        ) for entry in entries)),
    ]
    missing = [name for name, present in expected_rules if not present]
    if not missing:
        return {
            'status': 'passed',
            'message': 'The custom network ACL contains the intended stateless rules.',
            'resource': _clean_response(acl),
        }
    return {
        'status': 'failed',
        'message': f'The custom network ACL is missing: {", ".join(missing)}.',
    }


def _verify_ec2_network_acl_rule(
    *,
    egress: bool,
    rule_number: int,
    action: str,
    cidr: str,
    from_port: int,
    to_port: int,
    success_message: str,
) -> dict[str, Any]:
    vpc = _ec2_security_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    acl = _ec2_security_custom_nacl(vpc['VpcId'])
    if not acl:
        return {'status': 'failed', 'message': 'Create the custom network ACL first.'}
    if any(_nacl_rule_matches(
        entry,
        egress=egress,
        rule_number=rule_number,
        action=action,
        cidr=cidr,
        from_port=from_port,
        to_port=to_port,
    ) for entry in acl.get('Entries', [])):
        return {
            'status': 'passed',
            'message': success_message,
            'resource': _clean_response(acl),
        }
    return {
        'status': 'failed',
        'message': 'The expected network ACL entry is missing.',
    }


def _verify_ec2_network_acl_association() -> dict[str, Any]:
    vpc = _ec2_security_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    subnet_verification = _verify_ec2_security_subnet()
    if subnet_verification.get('status') != 'passed':
        return subnet_verification
    subnet_id = _ec2_cached_id(EC2_SECURITY_SUBNET_ID_CACHE_KEY, 'Subnet ID')
    acl = _ec2_security_custom_nacl(vpc['VpcId'])
    if not acl:
        return {'status': 'failed', 'message': 'Create the custom network ACL first.'}
    association = next(
        (
            item for item in acl.get('Associations', [])
            if item.get('SubnetId') == subnet_id
        ),
        None,
    )
    if association:
        cache.set(
            EC2_NACL_ASSOC_ID_CACHE_KEY,
            association['NetworkAclAssociationId'],
            timeout=86400,
        )
        return {
            'status': 'passed',
            'message': 'The custom network ACL is associated with the lab subnet.',
            'resource': _clean_response(acl),
        }
    return {
        'status': 'failed',
        'message': 'The custom network ACL is not associated with the lab subnet.',
    }


def _ec2_endpoint_vpc() -> dict[str, Any] | None:
    response = _ec2_client().describe_vpcs(Filters=[
        {'Name': 'cidr-block', 'Values': [EC2_ENDPOINT_VPC_CIDR]},
        {'Name': 'tag:Lab', 'Values': ['s3-gateway-endpoint']},
    ])
    return next(iter(response.get('Vpcs', [])), None)


def _verify_ec2_endpoint_vpc() -> dict[str, Any]:
    vpc = _ec2_endpoint_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'The endpoint VPC is missing.'}
    cache.set(EC2_ENDPOINT_VPC_ID_CACHE_KEY, vpc['VpcId'], timeout=86400)
    return {
        'status': 'passed',
        'message': 'The isolated endpoint VPC exists.',
        'resource': _clean_response(vpc),
    }


def _verify_ec2_endpoint_subnet() -> dict[str, Any]:
    vpc = _ec2_endpoint_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    response = _ec2_client().describe_subnets(Filters=[
        {'Name': 'vpc-id', 'Values': [vpc['VpcId']]},
        {'Name': 'cidr-block', 'Values': [EC2_ENDPOINT_SUBNET_CIDR]},
    ])
    subnet = next(iter(response.get('Subnets', [])), None)
    verified = (
        subnet
        and subnet.get('AvailabilityZone') == EC2_ENDPOINT_AZ
        and subnet.get('MapPublicIpOnLaunch') is False
    )
    if verified:
        cache.set(EC2_ENDPOINT_SUBNET_ID_CACHE_KEY, subnet['SubnetId'], timeout=86400)
    return {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The private subnet has no automatic public IPs.'
            if verified else 'The private subnet is incomplete.'
        ),
        'resource': _clean_response(subnet or {}),
    }


def _verify_ec2_endpoint_route_table() -> dict[str, Any]:
    vpc = _ec2_endpoint_vpc()
    subnet_id = cache.get(EC2_ENDPOINT_SUBNET_ID_CACHE_KEY)
    if not vpc or not subnet_id:
        return {'status': 'failed', 'message': 'Create the subnet first.'}
    tables = _ec2_lab_route_tables(vpc['VpcId'])
    table = next((
        item for item in tables
        if any(
            association.get('SubnetId') == subnet_id
            for association in item.get('Associations', [])
        )
    ), None)
    has_public_default = any(
        route.get('DestinationCidrBlock') == '0.0.0.0/0'
        for route in (table or {}).get('Routes', [])
    )
    verified = bool(table and not has_public_default)
    if table:
        cache.set(EC2_ENDPOINT_RT_ID_CACHE_KEY, table['RouteTableId'], timeout=86400)
    return {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The subnet uses a private route table with no internet default route.'
            if verified else 'The private route-table association is incomplete.'
        ),
        'resource': _clean_response(table or {}),
    }


def _verify_ec2_endpoint_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=EC2_ENDPOINT_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'passed',
        'message': 'The endpoint-policy bucket exists.',
        'resource': _clean_response(response),
    }


def _ec2_s3_gateway_endpoint() -> dict[str, Any] | None:
    vpc = _ec2_endpoint_vpc()
    if not vpc:
        return None
    service_name = f'com.amazonaws.{_ec2_client().meta.region_name}.s3'
    response = _ec2_client().describe_vpc_endpoints()
    return next((
        endpoint
        for endpoint in response.get('VpcEndpoints', [])
        if endpoint.get('VpcId') == vpc['VpcId']
        and endpoint.get('ServiceName') == service_name
    ), None)


def _verify_ec2_s3_gateway_endpoint() -> dict[str, Any]:
    endpoint = _ec2_s3_gateway_endpoint()
    configured = bool(cache.get(EC2_ENDPOINT_CONFIGURED_CACHE_KEY))
    verified = (
        endpoint
        and endpoint.get('VpcEndpointType') == 'Gateway'
        and endpoint.get('State') == 'available'
        and configured
    )
    if endpoint:
        cache.set(EC2_ENDPOINT_ID_CACHE_KEY, endpoint['VpcEndpointId'], timeout=86400)
    return {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The available S3 gateway endpoint was requested with the private route table and bucket-scoped policy.'
            if verified else 'The S3 gateway endpoint is incomplete.'
        ),
        'resource': _clean_response(endpoint or {}),
    }


def _verify_ec2_endpoint_route_boundary() -> dict[str, Any]:
    if not cache.get(EC2_ENDPOINT_ROUTE_BOUNDARY_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'Inspect the route table after creating the endpoint.',
        }
    return {
        'status': 'passed',
        'message': 'Floci did not persist the gateway endpoint route-table ID or inject the managed S3 prefix-list route; AWS does both.',
    }


def _ec2_interface_vpc() -> dict[str, Any] | None:
    response = _ec2_client().describe_vpcs(Filters=[
        {'Name': 'cidr-block', 'Values': [EC2_INTERFACE_VPC_CIDR]},
        {'Name': 'tag:Lab', 'Values': ['sqs-interface-endpoint']},
    ])
    return next(iter(response.get('Vpcs', [])), None)


def _verify_ec2_interface_vpc() -> dict[str, Any]:
    vpc = _ec2_interface_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'The interface endpoint VPC is missing.'}
    cache.set(EC2_INTERFACE_VPC_ID_CACHE_KEY, vpc['VpcId'], timeout=86400)
    return {
        'status': 'passed',
        'message': 'The isolated interface endpoint VPC exists.',
        'resource': _clean_response(vpc),
    }


def _verify_ec2_interface_subnet() -> dict[str, Any]:
    vpc = _ec2_interface_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    response = _ec2_client().describe_subnets(Filters=[
        {'Name': 'vpc-id', 'Values': [vpc['VpcId']]},
        {'Name': 'cidr-block', 'Values': [EC2_INTERFACE_SUBNET_CIDR]},
    ])
    subnet = next(iter(response.get('Subnets', [])), None)
    verified = (
        subnet
        and subnet.get('AvailabilityZone') == EC2_INTERFACE_AZ
        and subnet.get('MapPublicIpOnLaunch') is False
    )
    if verified:
        cache.set(EC2_INTERFACE_SUBNET_ID_CACHE_KEY, subnet['SubnetId'], timeout=86400)
    return {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The endpoint subnet has no automatic public IPs.'
            if verified else 'The endpoint subnet is incomplete.'
        ),
        'resource': _clean_response(subnet or {}),
    }


def _verify_ec2_interface_security_group() -> dict[str, Any]:
    vpc = _ec2_interface_vpc()
    if not vpc:
        return {'status': 'failed', 'message': 'Create the VPC first.'}
    response = _ec2_client().describe_security_groups(Filters=[
        {'Name': 'vpc-id', 'Values': [vpc['VpcId']]},
        {'Name': 'group-name', 'Values': [EC2_INTERFACE_SG_NAME]},
    ])
    group = next(iter(response.get('SecurityGroups', [])), None)
    https_rule = next((
        permission
        for permission in (group or {}).get('IpPermissions', [])
        if permission.get('IpProtocol') == 'tcp'
        and permission.get('FromPort') == 443
        and permission.get('ToPort') == 443
        and any(
            item.get('CidrIp') == EC2_INTERFACE_VPC_CIDR
            for item in permission.get('IpRanges', [])
        )
    ), None)
    verified = bool(group and https_rule)
    if group:
        cache.set(EC2_INTERFACE_SG_ID_CACHE_KEY, group['GroupId'], timeout=86400)
    return {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The endpoint security group allows TCP 443 only from the lab VPC.'
            if verified else 'The endpoint security group is incomplete.'
        ),
        'resource': _clean_response(group or {}),
    }


def _verify_ec2_interface_queue() -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(EC2_INTERFACE_QUEUE_NAME)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    verified = response.get('Attributes', {}).get('QueueArn') == EC2_INTERFACE_QUEUE_ARN
    return {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The queue protected by the endpoint policy exists.'
            if verified else 'The private SQS queue is incomplete.'
        ),
        'resource': _clean_response(response),
    }


def _ec2_sqs_interface_endpoint() -> dict[str, Any] | None:
    vpc = _ec2_interface_vpc()
    if not vpc:
        return None
    service_name = f'com.amazonaws.{_ec2_client().meta.region_name}.sqs'
    response = _ec2_client().describe_vpc_endpoints()
    return next((
        endpoint
        for endpoint in response.get('VpcEndpoints', [])
        if endpoint.get('VpcId') == vpc['VpcId']
        and endpoint.get('ServiceName') == service_name
    ), None)


def _verify_ec2_sqs_interface_endpoint() -> dict[str, Any]:
    endpoint = _ec2_sqs_interface_endpoint()
    configured = bool(cache.get(EC2_INTERFACE_CONFIGURED_CACHE_KEY))
    verified = (
        endpoint
        and endpoint.get('VpcEndpointType') == 'Interface'
        and endpoint.get('State') == 'available'
        and configured
    )
    if endpoint:
        cache.set(
            EC2_INTERFACE_ENDPOINT_ID_CACHE_KEY,
            endpoint['VpcEndpointId'],
            timeout=86400,
        )
    return {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The available SQS interface endpoint was requested with private DNS, subnet placement, endpoint security, and a queue-scoped policy.'
            if verified else 'The SQS interface endpoint is incomplete.'
        ),
        'resource': _clean_response(endpoint or {}),
    }


def _verify_ec2_interface_inspection() -> dict[str, Any]:
    if not cache.get(EC2_INTERFACE_INSPECTED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'Inspect the endpoint topology after creating it.',
        }
    return {
        'status': 'passed',
        'message': 'The endpoint topology and Floci persistence boundary were inspected.',
    }


def _ec2_network_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'ec2',
        'lab': 'vpc-public-private',
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _ec2_security_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    result = _ec2_network_step_result(
        step_key,
        command,
        response,
        verification,
        started,
    )
    result['lab'] = 'security-controls'
    return result


def _ec2_endpoint_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    result = _ec2_network_step_result(
        step_key, command, response, verification, started,
    )
    result['lab'] = 's3-gateway-endpoint'
    return result


def _ec2_interface_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    result = _ec2_network_step_result(
        step_key, command, response, verification, started,
    )
    result['lab'] = 'sqs-interface-endpoint'
    return result


def _run_ec2_create_vpc() -> dict[str, Any]:
    command = (
        f'aws ec2 create-vpc --cidr-block {EC2_VPC_CIDR} '
        '--tag-specifications file://vpc-tags.json'
    )
    started = time.perf_counter()
    response = _ec2_client().create_vpc(
        CidrBlock=EC2_VPC_CIDR,
        TagSpecifications=[{
            'ResourceType': 'vpc',
            'Tags': EC2_VPC_TAGS,
        }],
    )
    vpc_id = response.get('Vpc', {}).get('VpcId')
    if vpc_id:
        cache.set(EC2_VPC_ID_CACHE_KEY, vpc_id, timeout=86400)
    verification = _verify_ec2_vpc()
    return _ec2_network_step_result(
        'create-vpc', command, response, verification, started,
    )


def _run_ec2_create_subnet(
    cidr: str,
    availability_zone: str,
    cache_key: str,
    step_key: str,
) -> dict[str, Any]:
    command = (
        'aws ec2 create-subnet --vpc-id <vpc-id> '
        f'--cidr-block {cidr} --availability-zone {availability_zone}'
    )
    started = time.perf_counter()
    response = _ec2_client().create_subnet(
        VpcId=_ec2_cached_id(EC2_VPC_ID_CACHE_KEY, 'VPC ID'),
        CidrBlock=cidr,
        AvailabilityZone=availability_zone,
    )
    subnet = response.get('Subnet', {})
    if subnet.get('SubnetId'):
        cache.set(cache_key, subnet['SubnetId'], timeout=86400)
    verified = (
        subnet.get('CidrBlock') == cidr
        and subnet.get('AvailabilityZone') == availability_zone
    )
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            f'The {cidr} subnet was created in {availability_zone}.'
            if verified else 'The subnet response does not match the request.'
        ),
        'resource': _clean_response(subnet),
    }
    return _ec2_network_step_result(
        step_key, command, response, verification, started,
    )


def _run_ec2_create_public_subnet() -> dict[str, Any]:
    return _run_ec2_create_subnet(
        EC2_PUBLIC_SUBNET_CIDR,
        EC2_PUBLIC_AZ,
        EC2_PUBLIC_SUBNET_ID_CACHE_KEY,
        'create-public-subnet',
    )


def _run_ec2_create_private_subnet() -> dict[str, Any]:
    return _run_ec2_create_subnet(
        EC2_PRIVATE_SUBNET_CIDR,
        EC2_PRIVATE_AZ,
        EC2_PRIVATE_SUBNET_ID_CACHE_KEY,
        'create-private-subnet',
    )


def _run_ec2_enable_public_ip() -> dict[str, Any]:
    command = (
        'aws ec2 modify-subnet-attribute --subnet-id <public-subnet-id> '
        '--map-public-ip-on-launch'
    )
    started = time.perf_counter()
    subnet_id = _ec2_cached_id(
        EC2_PUBLIC_SUBNET_ID_CACHE_KEY,
        'Public subnet ID',
    )
    response = _ec2_client().modify_subnet_attribute(
        SubnetId=subnet_id,
        MapPublicIpOnLaunch={'Value': True},
    )
    public, _ = _verify_ec2_subnets()
    return _ec2_network_step_result(
        'enable-public-ip', command, response, public, started,
    )


def _run_ec2_create_igw() -> dict[str, Any]:
    command = 'aws ec2 create-internet-gateway'
    started = time.perf_counter()
    response = _ec2_client().create_internet_gateway()
    igw_id = response.get('InternetGateway', {}).get('InternetGatewayId')
    if igw_id:
        cache.set(EC2_IGW_ID_CACHE_KEY, igw_id, timeout=86400)
    verification = {
        'status': 'passed' if igw_id else 'failed',
        'message': 'The internet gateway was created.' if igw_id else 'No gateway ID was returned.',
        'resource': _clean_response(response.get('InternetGateway', {})),
    }
    return _ec2_network_step_result(
        'create-internet-gateway', command, response, verification, started,
    )


def _run_ec2_attach_igw() -> dict[str, Any]:
    command = (
        'aws ec2 attach-internet-gateway --internet-gateway-id <igw-id> '
        '--vpc-id <vpc-id>'
    )
    started = time.perf_counter()
    response = _ec2_client().attach_internet_gateway(
        InternetGatewayId=_ec2_cached_id(EC2_IGW_ID_CACHE_KEY, 'Gateway ID'),
        VpcId=_ec2_cached_id(EC2_VPC_ID_CACHE_KEY, 'VPC ID'),
    )
    verification = _verify_ec2_igw()
    return _ec2_network_step_result(
        'attach-internet-gateway', command, response, verification, started,
    )


def _run_ec2_create_route_table(
    cache_key: str,
    step_key: str,
    label: str,
) -> dict[str, Any]:
    command = 'aws ec2 create-route-table --vpc-id <vpc-id>'
    started = time.perf_counter()
    response = _ec2_client().create_route_table(
        VpcId=_ec2_cached_id(EC2_VPC_ID_CACHE_KEY, 'VPC ID'),
    )
    route_table = response.get('RouteTable', {})
    route_table_id = route_table.get('RouteTableId')
    if route_table_id:
        cache.set(cache_key, route_table_id, timeout=86400)
    verification = {
        'status': 'passed' if route_table_id else 'failed',
        'message': f'The {label} route table was created.',
        'resource': _clean_response(route_table),
    }
    return _ec2_network_step_result(
        step_key, command, response, verification, started,
    )


def _run_ec2_create_public_route_table() -> dict[str, Any]:
    return _run_ec2_create_route_table(
        EC2_PUBLIC_RT_ID_CACHE_KEY,
        'create-public-route-table',
        'public',
    )


def _run_ec2_create_private_route_table() -> dict[str, Any]:
    return _run_ec2_create_route_table(
        EC2_PRIVATE_RT_ID_CACHE_KEY,
        'create-private-route-table',
        'private',
    )


def _run_ec2_create_internet_route() -> dict[str, Any]:
    command = (
        'aws ec2 create-route --route-table-id <public-route-table-id> '
        '--destination-cidr-block 0.0.0.0/0 --gateway-id <igw-id>'
    )
    started = time.perf_counter()
    response = _ec2_client().create_route(
        RouteTableId=_ec2_cached_id(
            EC2_PUBLIC_RT_ID_CACHE_KEY,
            'Public route table ID',
        ),
        DestinationCidrBlock='0.0.0.0/0',
        GatewayId=_ec2_cached_id(EC2_IGW_ID_CACHE_KEY, 'Gateway ID'),
    )
    verification = {
        'status': 'passed' if response.get('Return') is True else 'failed',
        'message': 'The public route table now has an internet default route.',
        'resource': _clean_response(response),
    }
    return _ec2_network_step_result(
        'create-internet-route', command, response, verification, started,
    )


def _run_ec2_associate_route_table(
    route_table_cache_key: str,
    subnet_cache_key: str,
    association_cache_key: str,
    step_key: str,
    route_label: str,
) -> dict[str, Any]:
    command = (
        f'aws ec2 associate-route-table --route-table-id '
        f'<{route_label}-route-table-id> --subnet-id <{route_label}-subnet-id>'
    )
    started = time.perf_counter()
    response = _ec2_client().associate_route_table(
        RouteTableId=_ec2_cached_id(route_table_cache_key, 'Route table ID'),
        SubnetId=_ec2_cached_id(subnet_cache_key, 'Subnet ID'),
    )
    association_id = response.get('AssociationId')
    if association_id:
        cache.set(association_cache_key, association_id, timeout=86400)
    public, private = _verify_ec2_routes()
    verification = public if route_label == 'public' else private
    return _ec2_network_step_result(
        step_key, command, response, verification, started,
    )


def _run_ec2_associate_public_route_table() -> dict[str, Any]:
    return _run_ec2_associate_route_table(
        EC2_PUBLIC_RT_ID_CACHE_KEY,
        EC2_PUBLIC_SUBNET_ID_CACHE_KEY,
        EC2_PUBLIC_ASSOC_ID_CACHE_KEY,
        'associate-public-route-table',
        'public',
    )


def _run_ec2_associate_private_route_table() -> dict[str, Any]:
    return _run_ec2_associate_route_table(
        EC2_PRIVATE_RT_ID_CACHE_KEY,
        EC2_PRIVATE_SUBNET_ID_CACHE_KEY,
        EC2_PRIVATE_ASSOC_ID_CACHE_KEY,
        'associate-private-route-table',
        'private',
    )


def _run_ec2_inspect_network_topology() -> dict[str, Any]:
    command = (
        'aws ec2 describe-vpcs --vpc-ids <vpc-id>\n'
        'aws ec2 describe-subnets --filters Name=vpc-id,Values=<vpc-id>\n'
        'aws ec2 describe-internet-gateways '
        '--filters Name=attachment.vpc-id,Values=<vpc-id>\n'
        'aws ec2 describe-route-tables '
        '--filters Name=vpc-id,Values=<vpc-id>'
    )
    started = time.perf_counter()
    vpc = _ec2_lab_vpc()
    public_subnet, private_subnet = _verify_ec2_subnets()
    igw = _verify_ec2_igw()
    public_route, private_route = _verify_ec2_routes()
    verified = all(
        item.get('status') == 'passed'
        for item in [
            _verify_ec2_vpc(),
            public_subnet,
            private_subnet,
            igw,
            public_route,
            private_route,
        ]
    )
    response = {
        'Vpc': _clean_response(vpc),
        'Subnets': _clean_response(
            list(_ec2_lab_subnets(vpc['VpcId']).values()) if vpc else []
        ),
        'InternetGateway': igw.get('resource'),
        'RouteTables': _clean_response(
            _ec2_lab_route_tables(vpc['VpcId']) if vpc else []
        ),
    }
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The VPC has one internet-routed public subnet and one isolated private subnet.'
            if verified else 'The network topology is incomplete.'
        ),
        'resource': response,
    }
    return _ec2_network_step_result(
        'inspect-network-topology', command, response, verification, started,
    )


def _run_ec2_security_create_vpc() -> dict[str, Any]:
    command = (
        f'aws ec2 create-vpc --cidr-block {EC2_SECURITY_VPC_CIDR} '
        '--tag-specifications file://vpc-tags.json'
    )
    started = time.perf_counter()
    response = _ec2_client().create_vpc(
        CidrBlock=EC2_SECURITY_VPC_CIDR,
        TagSpecifications=[{
            'ResourceType': 'vpc',
            'Tags': EC2_SECURITY_VPC_TAGS,
        }],
    )
    vpc_id = response.get('Vpc', {}).get('VpcId')
    if vpc_id:
        cache.set(EC2_SECURITY_VPC_ID_CACHE_KEY, vpc_id, timeout=86400)
    verification = _verify_ec2_security_vpc()
    return _ec2_security_step_result(
        'create-vpc', command, response, verification, started,
    )


def _run_ec2_security_create_subnet() -> dict[str, Any]:
    command = (
        'aws ec2 create-subnet --vpc-id <vpc-id> '
        f'--cidr-block {EC2_SECURITY_SUBNET_CIDR} '
        f'--availability-zone {EC2_SECURITY_AZ}'
    )
    started = time.perf_counter()
    response = _ec2_client().create_subnet(
        VpcId=_ec2_cached_id(EC2_SECURITY_VPC_ID_CACHE_KEY, 'VPC ID'),
        CidrBlock=EC2_SECURITY_SUBNET_CIDR,
        AvailabilityZone=EC2_SECURITY_AZ,
    )
    subnet_id = response.get('Subnet', {}).get('SubnetId')
    if subnet_id:
        cache.set(EC2_SECURITY_SUBNET_ID_CACHE_KEY, subnet_id, timeout=86400)
    verification = _verify_ec2_security_subnet()
    return _ec2_security_step_result(
        'create-subnet', command, response, verification, started,
    )


def _run_ec2_create_security_group(
    group_name: str,
    description: str,
    cache_key: str,
    step_key: str,
) -> dict[str, Any]:
    command = (
        f'aws ec2 create-security-group --group-name {group_name} '
        f'--description "{description}" --vpc-id <vpc-id>'
    )
    started = time.perf_counter()
    response = _ec2_client().create_security_group(
        GroupName=group_name,
        Description=description,
        VpcId=_ec2_cached_id(EC2_SECURITY_VPC_ID_CACHE_KEY, 'VPC ID'),
    )
    group_id = response.get('GroupId')
    if group_id:
        cache.set(cache_key, group_id, timeout=86400)
    verification = {
        'status': 'passed' if group_id else 'failed',
        'message': f'Security group {group_name} was created.',
        'resource': _clean_response(response),
    }
    return _ec2_security_step_result(
        step_key, command, response, verification, started,
    )


def _run_ec2_allow_trusted_https() -> dict[str, Any]:
    command = (
        'aws ec2 authorize-security-group-ingress --group-id <web-sg-id> '
        '--ip-permissions file://trusted-https.json'
    )
    permission = {
        'IpProtocol': 'tcp',
        'FromPort': 443,
        'ToPort': 443,
        'IpRanges': [{
            'CidrIp': EC2_TRUSTED_CLIENT_CIDR,
            'Description': 'Trusted HTTPS clients',
        }],
    }
    started = time.perf_counter()
    response = _ec2_client().authorize_security_group_ingress(
        GroupId=_ec2_cached_id(EC2_WEB_SG_ID_CACHE_KEY, 'Web security group ID'),
        IpPermissions=[permission],
    )
    web, _ = _verify_ec2_security_group_rules()
    return _ec2_security_step_result(
        'allow-trusted-https', command, response, web, started,
    )


def _run_ec2_allow_web_to_app() -> dict[str, Any]:
    command = (
        'aws ec2 authorize-security-group-ingress --group-id <app-sg-id> '
        '--ip-permissions file://web-to-app.json'
    )
    web_group_id = _ec2_cached_id(
        EC2_WEB_SG_ID_CACHE_KEY,
        'Web security group ID',
    )
    permission = {
        'IpProtocol': 'tcp',
        'FromPort': 8080,
        'ToPort': 8080,
        'UserIdGroupPairs': [{
            'GroupId': web_group_id,
            'Description': 'Web tier to application tier',
        }],
    }
    started = time.perf_counter()
    response = _ec2_client().authorize_security_group_ingress(
        GroupId=_ec2_cached_id(EC2_APP_SG_ID_CACHE_KEY, 'App security group ID'),
        IpPermissions=[permission],
    )
    if response.get('Return') is True:
        cache.set(EC2_SG_REFERENCE_CACHE_KEY, True, timeout=86400)
    _, app = _verify_ec2_security_group_rules()
    return _ec2_security_step_result(
        'allow-web-to-app', command, response, app, started,
    )


def _run_ec2_inspect_security_groups() -> dict[str, Any]:
    command = (
        'aws ec2 describe-security-groups '
        '--group-ids <web-sg-id> <app-sg-id>\n'
        'aws ec2 describe-security-group-rules '
        '--filters Name=group-id,Values=<web-sg-id>,<app-sg-id>'
    )
    started = time.perf_counter()
    group_ids = [
        _ec2_cached_id(EC2_WEB_SG_ID_CACHE_KEY, 'Web security group ID'),
        _ec2_cached_id(EC2_APP_SG_ID_CACHE_KEY, 'App security group ID'),
    ]
    response = {
        'SecurityGroups': _ec2_client().describe_security_groups(
            GroupIds=group_ids,
        ).get('SecurityGroups', []),
        'SecurityGroupRules': _ec2_client().describe_security_group_rules(
            Filters=[{'Name': 'group-id', 'Values': group_ids}],
        ).get('SecurityGroupRules', []),
    }
    web, app = _verify_ec2_security_group_rules()
    verified = web.get('status') == 'passed' and app.get('status') == 'passed'
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The web and app security groups implement the intended stateful trust boundaries.'
            if verified else 'The security group rules are incomplete.'
        ),
        'resource': _clean_response(response),
    }
    return _ec2_security_step_result(
        'inspect-security-groups', command, response, verification, started,
    )


def _run_ec2_create_network_acl() -> dict[str, Any]:
    command = 'aws ec2 create-network-acl --vpc-id <vpc-id>'
    started = time.perf_counter()
    response = _ec2_client().create_network_acl(
        VpcId=_ec2_cached_id(EC2_SECURITY_VPC_ID_CACHE_KEY, 'VPC ID'),
    )
    nacl_id = response.get('NetworkAcl', {}).get('NetworkAclId')
    if nacl_id:
        cache.set(EC2_NACL_ID_CACHE_KEY, nacl_id, timeout=86400)
    verification = _verify_ec2_network_acl_created()
    return _ec2_security_step_result(
        'create-network-acl', command, response, verification, started,
    )


def _run_ec2_create_network_acl_entry(
    *,
    step_key: str,
    command: str,
    rule_number: int,
    rule_action: str,
    egress: bool,
    cidr_block: str,
    from_port: int,
    to_port: int,
    success_message: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    response = _ec2_client().create_network_acl_entry(
        NetworkAclId=_ec2_cached_id(EC2_NACL_ID_CACHE_KEY, 'Network ACL ID'),
        RuleNumber=rule_number,
        Protocol='6',
        RuleAction=rule_action,
        Egress=egress,
        CidrBlock=cidr_block,
        PortRange={'From': from_port, 'To': to_port},
    )
    verification = _verify_ec2_network_acl_rule(
        egress=egress,
        rule_number=rule_number,
        action=rule_action,
        cidr=cidr_block,
        from_port=from_port,
        to_port=to_port,
        success_message=success_message,
    )
    return _ec2_security_step_result(
        step_key, command, response, verification, started,
    )


def _run_ec2_deny_ssh_network_acl() -> dict[str, Any]:
    return _run_ec2_create_network_acl_entry(
        step_key='deny-ssh-network-acl',
        command=(
            'aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> '
            '--rule-number 90 --protocol 6 --rule-action deny --ingress '
            '--cidr-block 0.0.0.0/0 --port-range From=22,To=22'
        ),
        rule_number=90,
        rule_action='deny',
        egress=False,
        cidr_block='0.0.0.0/0',
        from_port=22,
        to_port=22,
        success_message='The network ACL denies inbound SSH before later allow rules.',
    )


def _run_ec2_allow_https_network_acl() -> dict[str, Any]:
    return _run_ec2_create_network_acl_entry(
        step_key='allow-https-network-acl',
        command=(
            'aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> '
            '--rule-number 100 --protocol 6 --rule-action allow --ingress '
            f'--cidr-block {EC2_TRUSTED_CLIENT_CIDR} '
            '--port-range From=443,To=443'
        ),
        rule_number=100,
        rule_action='allow',
        egress=False,
        cidr_block=EC2_TRUSTED_CLIENT_CIDR,
        from_port=443,
        to_port=443,
        success_message='The network ACL allows inbound HTTPS from trusted clients.',
    )


def _run_ec2_allow_return_network_acl() -> dict[str, Any]:
    return _run_ec2_create_network_acl_entry(
        step_key='allow-return-network-acl',
        command=(
            'aws ec2 create-network-acl-entry --network-acl-id <network-acl-id> '
            '--rule-number 100 --protocol 6 --rule-action allow --egress '
            f'--cidr-block {EC2_TRUSTED_CLIENT_CIDR} '
            '--port-range From=1024,To=65535'
        ),
        rule_number=100,
        rule_action='allow',
        egress=True,
        cidr_block=EC2_TRUSTED_CLIENT_CIDR,
        from_port=1024,
        to_port=65535,
        success_message='The network ACL allows outbound ephemeral return traffic.',
    )


def _run_ec2_associate_network_acl() -> dict[str, Any]:
    command = (
        'aws ec2 replace-network-acl-association '
        '--association-id <default-association-id> '
        '--network-acl-id <network-acl-id>'
    )
    started = time.perf_counter()
    vpc_id = _ec2_cached_id(EC2_SECURITY_VPC_ID_CACHE_KEY, 'VPC ID')
    subnet_id = _ec2_cached_id(EC2_SECURITY_SUBNET_ID_CACHE_KEY, 'Subnet ID')
    association_id = _ec2_default_nacl_association_id(vpc_id, subnet_id)
    if not association_id:
        raise ValueError('Default network ACL association ID is not available.')
    response = _ec2_client().replace_network_acl_association(
        AssociationId=association_id,
        NetworkAclId=_ec2_cached_id(EC2_NACL_ID_CACHE_KEY, 'Network ACL ID'),
    )
    new_association_id = response.get('NewAssociationId')
    if new_association_id:
        cache.set(EC2_NACL_ASSOC_ID_CACHE_KEY, new_association_id, timeout=86400)
    verification = _verify_ec2_network_acl_association()
    return _ec2_security_step_result(
        'associate-network-acl', command, response, verification, started,
    )


def _run_ec2_inspect_nacl_support() -> dict[str, Any]:
    command = (
        'aws ec2 describe-network-acls '
        '--filters Name=vpc-id,Values=<vpc-id>'
    )
    started = time.perf_counter()
    response = _ec2_client().describe_network_acls(
        Filters=[{
            'Name': 'vpc-id',
            'Values': [
                _ec2_cached_id(
                    EC2_SECURITY_VPC_ID_CACHE_KEY,
                    'VPC ID',
                ),
            ],
        }],
    )
    rules_verification = _verify_ec2_network_acl_rules()
    association_verification = _verify_ec2_network_acl_association()
    verified = (
        rules_verification.get('status') == 'passed'
        and association_verification.get('status') == 'passed'
    )
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'The custom network ACL is associated with the subnet and contains the intended stateless rules.'
            if verified else 'The custom network ACL rules or subnet association are incomplete.'
        ),
        'resource': _clean_response(response),
    }
    return _ec2_security_step_result(
        'inspect-network-acl-support',
        command,
        response,
        verification,
        started,
    )


def _run_ec2_endpoint_create_vpc() -> dict[str, Any]:
    command = (
        f'aws ec2 create-vpc --cidr-block {EC2_ENDPOINT_VPC_CIDR} '
        '--tag-specifications file://vpc-tags.json'
    )
    started = time.perf_counter()
    response = _ec2_client().create_vpc(
        CidrBlock=EC2_ENDPOINT_VPC_CIDR,
        TagSpecifications=[{
            'ResourceType': 'vpc',
            'Tags': EC2_ENDPOINT_VPC_TAGS,
        }],
    )
    vpc_id = response.get('Vpc', {}).get('VpcId')
    if vpc_id:
        cache.set(EC2_ENDPOINT_VPC_ID_CACHE_KEY, vpc_id, timeout=86400)
    return _ec2_endpoint_step_result(
        'create-vpc', command, response, _verify_ec2_endpoint_vpc(), started,
    )


def _run_ec2_endpoint_create_subnet() -> dict[str, Any]:
    command = (
        'aws ec2 create-subnet --vpc-id <vpc-id> '
        f'--cidr-block {EC2_ENDPOINT_SUBNET_CIDR} '
        f'--availability-zone {EC2_ENDPOINT_AZ}'
    )
    started = time.perf_counter()
    response = _ec2_client().create_subnet(
        VpcId=_ec2_cached_id(EC2_ENDPOINT_VPC_ID_CACHE_KEY, 'VPC ID'),
        CidrBlock=EC2_ENDPOINT_SUBNET_CIDR,
        AvailabilityZone=EC2_ENDPOINT_AZ,
    )
    subnet_id = response.get('Subnet', {}).get('SubnetId')
    if subnet_id:
        cache.set(EC2_ENDPOINT_SUBNET_ID_CACHE_KEY, subnet_id, timeout=86400)
    return _ec2_endpoint_step_result(
        'create-private-subnet',
        command,
        response,
        _verify_ec2_endpoint_subnet(),
        started,
    )


def _run_ec2_endpoint_create_route_table() -> dict[str, Any]:
    command = 'aws ec2 create-route-table --vpc-id <vpc-id>'
    started = time.perf_counter()
    response = _ec2_client().create_route_table(
        VpcId=_ec2_cached_id(EC2_ENDPOINT_VPC_ID_CACHE_KEY, 'VPC ID'),
    )
    route_table = response.get('RouteTable', {})
    route_table_id = route_table.get('RouteTableId')
    if route_table_id:
        cache.set(EC2_ENDPOINT_RT_ID_CACHE_KEY, route_table_id, timeout=86400)
    verification = {
        'status': 'passed' if route_table_id else 'failed',
        'message': 'The private route table was created.',
        'resource': _clean_response(route_table),
    }
    return _ec2_endpoint_step_result(
        'create-private-route-table', command, response, verification, started,
    )


def _run_ec2_endpoint_associate_route_table() -> dict[str, Any]:
    command = (
        'aws ec2 associate-route-table --route-table-id <route-table-id> '
        '--subnet-id <subnet-id>'
    )
    started = time.perf_counter()
    response = _ec2_client().associate_route_table(
        RouteTableId=_ec2_cached_id(EC2_ENDPOINT_RT_ID_CACHE_KEY, 'Route table ID'),
        SubnetId=_ec2_cached_id(EC2_ENDPOINT_SUBNET_ID_CACHE_KEY, 'Subnet ID'),
    )
    association_id = response.get('AssociationId')
    if association_id:
        cache.set(EC2_ENDPOINT_ASSOC_ID_CACHE_KEY, association_id, timeout=86400)
    return _ec2_endpoint_step_result(
        'associate-private-route-table',
        command,
        response,
        _verify_ec2_endpoint_route_table(),
        started,
    )


def _run_ec2_endpoint_create_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {EC2_ENDPOINT_BUCKET_NAME}'
    started = time.perf_counter()
    response = _s3_client().create_bucket(Bucket=EC2_ENDPOINT_BUCKET_NAME)
    return _ec2_endpoint_step_result(
        'create-s3-bucket',
        command,
        response,
        _verify_ec2_endpoint_bucket(),
        started,
    )


def _run_ec2_create_s3_gateway_endpoint() -> dict[str, Any]:
    ec2 = _ec2_client()
    service_name = f'com.amazonaws.{ec2.meta.region_name}.s3'
    command = (
        'aws ec2 create-vpc-endpoint --vpc-id <vpc-id> '
        '--vpc-endpoint-type Gateway '
        f'--service-name {service_name} '
        '--route-table-ids <route-table-id> '
        '--policy-document file://endpoint-policy.json'
    )
    started = time.perf_counter()
    response = ec2.create_vpc_endpoint(
        VpcId=_ec2_cached_id(EC2_ENDPOINT_VPC_ID_CACHE_KEY, 'VPC ID'),
        VpcEndpointType='Gateway',
        ServiceName=service_name,
        RouteTableIds=[
            _ec2_cached_id(EC2_ENDPOINT_RT_ID_CACHE_KEY, 'Route table ID'),
        ],
        PolicyDocument=json.dumps(EC2_ENDPOINT_POLICY),
    )
    endpoint_id = response.get('VpcEndpoint', {}).get('VpcEndpointId')
    if endpoint_id:
        cache.set(EC2_ENDPOINT_ID_CACHE_KEY, endpoint_id, timeout=86400)
        cache.set(EC2_ENDPOINT_CONFIGURED_CACHE_KEY, True, timeout=86400)
    return _ec2_endpoint_step_result(
        'create-s3-gateway-endpoint',
        command,
        response,
        _verify_ec2_s3_gateway_endpoint(),
        started,
    )


def _run_ec2_inspect_private_s3_path() -> dict[str, Any]:
    command = (
        'aws ec2 describe-vpc-endpoints '
        '--vpc-endpoint-ids <endpoint-id>\n'
        'aws ec2 describe-route-tables --route-table-ids <route-table-id>\n'
        f'aws s3api head-bucket --bucket {EC2_ENDPOINT_BUCKET_NAME}'
    )
    started = time.perf_counter()
    endpoint_id = _ec2_cached_id(EC2_ENDPOINT_ID_CACHE_KEY, 'Endpoint ID')
    route_table_id = _ec2_cached_id(
        EC2_ENDPOINT_RT_ID_CACHE_KEY,
        'Route table ID',
    )
    ec2 = _ec2_client()
    endpoints = ec2.describe_vpc_endpoints(
        VpcEndpointIds=[endpoint_id],
    ).get('VpcEndpoints', [])
    route_tables = ec2.describe_route_tables(
        RouteTableIds=[route_table_id],
    ).get('RouteTables', [])
    route_injected = any(
        route.get('DestinationPrefixListId')
        for table in route_tables
        for route in table.get('Routes', [])
    )
    if not route_injected:
        cache.set(EC2_ENDPOINT_ROUTE_BOUNDARY_CACHE_KEY, True, timeout=86400)
    response = {
        'VpcEndpoints': endpoints,
        'RouteTables': route_tables,
        'Bucket': _clean_response(
            _s3_client().head_bucket(Bucket=EC2_ENDPOINT_BUCKET_NAME)
        ),
        'AwsExpectedManagedPrefixListRoute': True,
        'FlociInjectedManagedPrefixListRoute': route_injected,
    }
    verification = _verify_ec2_endpoint_route_boundary()
    return _ec2_endpoint_step_result(
        'inspect-private-s3-path',
        command,
        response,
        verification,
        started,
    )


def _run_ec2_interface_create_vpc() -> dict[str, Any]:
    command = (
        f'aws ec2 create-vpc --cidr-block {EC2_INTERFACE_VPC_CIDR} '
        '--tag-specifications file://vpc-tags.json'
    )
    started = time.perf_counter()
    response = _ec2_client().create_vpc(
        CidrBlock=EC2_INTERFACE_VPC_CIDR,
        TagSpecifications=[{
            'ResourceType': 'vpc',
            'Tags': EC2_INTERFACE_VPC_TAGS,
        }],
    )
    vpc_id = response.get('Vpc', {}).get('VpcId')
    if vpc_id:
        cache.set(EC2_INTERFACE_VPC_ID_CACHE_KEY, vpc_id, timeout=86400)
    return _ec2_interface_step_result(
        'create-vpc', command, response, _verify_ec2_interface_vpc(), started,
    )


def _run_ec2_interface_create_subnet() -> dict[str, Any]:
    command = (
        'aws ec2 create-subnet --vpc-id <vpc-id> '
        f'--cidr-block {EC2_INTERFACE_SUBNET_CIDR} '
        f'--availability-zone {EC2_INTERFACE_AZ}'
    )
    started = time.perf_counter()
    response = _ec2_client().create_subnet(
        VpcId=_ec2_cached_id(EC2_INTERFACE_VPC_ID_CACHE_KEY, 'VPC ID'),
        CidrBlock=EC2_INTERFACE_SUBNET_CIDR,
        AvailabilityZone=EC2_INTERFACE_AZ,
    )
    subnet_id = response.get('Subnet', {}).get('SubnetId')
    if subnet_id:
        cache.set(EC2_INTERFACE_SUBNET_ID_CACHE_KEY, subnet_id, timeout=86400)
    return _ec2_interface_step_result(
        'create-private-subnet',
        command,
        response,
        _verify_ec2_interface_subnet(),
        started,
    )


def _run_ec2_interface_create_security_group() -> dict[str, Any]:
    command = (
        f'aws ec2 create-security-group --group-name {EC2_INTERFACE_SG_NAME} '
        '--description "Private SQS endpoint HTTPS" --vpc-id <vpc-id>'
    )
    started = time.perf_counter()
    response = _ec2_client().create_security_group(
        GroupName=EC2_INTERFACE_SG_NAME,
        Description='Private SQS endpoint HTTPS',
        VpcId=_ec2_cached_id(EC2_INTERFACE_VPC_ID_CACHE_KEY, 'VPC ID'),
    )
    group_id = response.get('GroupId')
    if group_id:
        cache.set(EC2_INTERFACE_SG_ID_CACHE_KEY, group_id, timeout=86400)
    verification = {
        'status': 'passed' if group_id else 'failed',
        'message': 'The endpoint security group was created.',
        'resource': _clean_response(response),
    }
    return _ec2_interface_step_result(
        'create-endpoint-security-group',
        command,
        response,
        verification,
        started,
    )


def _run_ec2_interface_allow_https() -> dict[str, Any]:
    command = (
        'aws ec2 authorize-security-group-ingress '
        '--group-id <endpoint-sg-id> '
        '--ip-permissions file://endpoint-https.json'
    )
    started = time.perf_counter()
    response = _ec2_client().authorize_security_group_ingress(
        GroupId=_ec2_cached_id(EC2_INTERFACE_SG_ID_CACHE_KEY, 'Security group ID'),
        IpPermissions=[{
            'IpProtocol': 'tcp',
            'FromPort': 443,
            'ToPort': 443,
            'IpRanges': [{
                'CidrIp': EC2_INTERFACE_VPC_CIDR,
                'Description': 'HTTPS from private VPC clients',
            }],
        }],
    )
    return _ec2_interface_step_result(
        'allow-vpc-https',
        command,
        response,
        _verify_ec2_interface_security_group(),
        started,
    )


def _run_ec2_interface_create_queue() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {EC2_INTERFACE_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=EC2_INTERFACE_QUEUE_NAME)
    return _ec2_interface_step_result(
        'create-sqs-queue',
        command,
        response,
        _verify_ec2_interface_queue(),
        started,
    )


def _run_ec2_create_sqs_interface_endpoint() -> dict[str, Any]:
    ec2 = _ec2_client()
    service_name = f'com.amazonaws.{ec2.meta.region_name}.sqs'
    command = (
        'aws ec2 create-vpc-endpoint --vpc-id <vpc-id> '
        '--vpc-endpoint-type Interface '
        f'--service-name {service_name} '
        '--subnet-ids <subnet-id> '
        '--security-group-ids <endpoint-sg-id> '
        '--private-dns-enabled '
        '--policy-document file://endpoint-policy.json'
    )
    started = time.perf_counter()
    response = ec2.create_vpc_endpoint(
        VpcId=_ec2_cached_id(EC2_INTERFACE_VPC_ID_CACHE_KEY, 'VPC ID'),
        VpcEndpointType='Interface',
        ServiceName=service_name,
        SubnetIds=[
            _ec2_cached_id(EC2_INTERFACE_SUBNET_ID_CACHE_KEY, 'Subnet ID'),
        ],
        SecurityGroupIds=[
            _ec2_cached_id(EC2_INTERFACE_SG_ID_CACHE_KEY, 'Security group ID'),
        ],
        PrivateDnsEnabled=True,
        PolicyDocument=json.dumps(EC2_INTERFACE_POLICY),
    )
    endpoint_id = response.get('VpcEndpoint', {}).get('VpcEndpointId')
    if endpoint_id:
        cache.set(EC2_INTERFACE_ENDPOINT_ID_CACHE_KEY, endpoint_id, timeout=86400)
        cache.set(EC2_INTERFACE_CONFIGURED_CACHE_KEY, True, timeout=86400)
    return _ec2_interface_step_result(
        'create-sqs-interface-endpoint',
        command,
        response,
        _verify_ec2_sqs_interface_endpoint(),
        started,
    )


def _run_ec2_inspect_private_sqs_path() -> dict[str, Any]:
    command = (
        'aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <endpoint-id>\n'
        'aws ec2 describe-network-interfaces --network-interface-ids <eni-id>\n'
        'aws sqs get-queue-attributes --queue-url <queue-url> '
        '--attribute-names QueueArn'
    )
    started = time.perf_counter()
    endpoint_id = _ec2_cached_id(
        EC2_INTERFACE_ENDPOINT_ID_CACHE_KEY,
        'Endpoint ID',
    )
    ec2 = _ec2_client()
    endpoints = ec2.describe_vpc_endpoints(
        VpcEndpointIds=[endpoint_id],
    ).get('VpcEndpoints', [])
    endpoint = next(iter(endpoints), {})
    eni_ids = endpoint.get('NetworkInterfaceIds', [])
    network_interfaces: list[dict[str, Any]] = []
    network_interface_boundary = None
    if eni_ids:
        try:
            network_interfaces = ec2.describe_network_interfaces(
                NetworkInterfaceIds=eni_ids,
            ).get('NetworkInterfaces', [])
        except ClientError as exc:
            if _error_code(exc) != 'UnsupportedOperation':
                raise
            network_interface_boundary = _error_code(exc)
    queue_url = _sqs_queue_url(EC2_INTERFACE_QUEUE_NAME)
    queue = _sqs_client().get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=['QueueArn'],
    )
    cache.set(EC2_INTERFACE_INSPECTED_CACHE_KEY, True, timeout=86400)
    response = {
        'VpcEndpoints': endpoints,
        'NetworkInterfaces': network_interfaces,
        'Queue': _clean_response(queue),
        'Expected': {
            'SubnetIds': [
                _ec2_cached_id(EC2_INTERFACE_SUBNET_ID_CACHE_KEY, 'Subnet ID'),
            ],
            'SecurityGroupIds': [
                _ec2_cached_id(EC2_INTERFACE_SG_ID_CACHE_KEY, 'Security group ID'),
            ],
            'PrivateDnsEnabled': True,
            'NetworkInterfaceCount': 1,
        },
        'NetworkInterfaceBoundary': network_interface_boundary,
    }
    return _ec2_interface_step_result(
        'inspect-private-sqs-path',
        command,
        response,
        _verify_ec2_interface_inspection(),
        started,
    )


def _reset_ec2_vpc_networking() -> dict[str, Any]:
    command = (
        'aws ec2 disassociate-route-table --association-id <association-id>\n'
        'aws ec2 delete-route-table --route-table-id <route-table-id>\n'
        'aws ec2 detach-internet-gateway --internet-gateway-id <igw-id> '
        '--vpc-id <vpc-id>\n'
        'aws ec2 delete-internet-gateway --internet-gateway-id <igw-id>\n'
        'aws ec2 delete-subnet --subnet-id <subnet-id>\n'
        'aws ec2 delete-vpc --vpc-id <vpc-id>'
    )
    started = time.perf_counter()
    ec2 = _ec2_client()
    deleted_associations = 0
    deleted_route_tables = 0
    deleted_subnets = 0
    deleted_igw = False
    deleted_vpc = False
    vpc = None
    try:
        vpc = _ec2_lab_vpc()
    except ClientError:
        vpc = None
    if vpc:
        vpc_id = vpc['VpcId']
        subnets = _ec2_lab_subnets(vpc_id)
        subnet_ids = {
            subnet.get('SubnetId')
            for cidr, subnet in subnets.items()
            if cidr in {EC2_PUBLIC_SUBNET_CIDR, EC2_PRIVATE_SUBNET_CIDR}
        }
        route_tables = _ec2_lab_route_tables(vpc_id)
        custom_tables = []
        for table in route_tables:
            matching_associations = [
                association
                for association in table.get('Associations', [])
                if association.get('SubnetId') in subnet_ids
            ]
            if not matching_associations:
                continue
            custom_tables.append(table)
            for association in matching_associations:
                association_id = association.get('RouteTableAssociationId')
                if association_id:
                    ec2.disassociate_route_table(AssociationId=association_id)
                    deleted_associations += 1
        for table in custom_tables:
            ec2.delete_route_table(RouteTableId=table['RouteTableId'])
            deleted_route_tables += 1

        igw = _ec2_lab_igw(vpc_id)
        if igw:
            igw_id = igw['InternetGatewayId']
            ec2.detach_internet_gateway(
                InternetGatewayId=igw_id,
                VpcId=vpc_id,
            )
            ec2.delete_internet_gateway(InternetGatewayId=igw_id)
            deleted_igw = True

        for subnet_id in subnet_ids:
            if subnet_id:
                ec2.delete_subnet(SubnetId=subnet_id)
                deleted_subnets += 1
        ec2.delete_vpc(VpcId=vpc_id)
        deleted_vpc = True

    cache.delete_many([
        EC2_VPC_ID_CACHE_KEY,
        EC2_PUBLIC_SUBNET_ID_CACHE_KEY,
        EC2_PRIVATE_SUBNET_ID_CACHE_KEY,
        EC2_IGW_ID_CACHE_KEY,
        EC2_PUBLIC_RT_ID_CACHE_KEY,
        EC2_PRIVATE_RT_ID_CACHE_KEY,
        EC2_PUBLIC_ASSOC_ID_CACHE_KEY,
        EC2_PRIVATE_ASSOC_ID_CACHE_KEY,
    ])
    output = {
        'deleted_associations': deleted_associations,
        'deleted_route_tables': deleted_route_tables,
        'deleted_subnets': deleted_subnets,
        'deleted_internet_gateway': deleted_igw,
        'deleted_vpc': deleted_vpc,
    }
    return {
        'service': 'ec2',
        'lab': 'vpc-public-private',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': output,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **output,
        'verification': {
            'status': 'passed',
            'message': 'Route associations, route tables, gateway, subnets, and VPC were removed in dependency order.',
        },
    }


def _reset_ec2_security_controls() -> dict[str, Any]:
    command = (
        'aws ec2 replace-network-acl-association --association-id <custom-association-id> --network-acl-id <default-network-acl-id>\n'
        'aws ec2 delete-network-acl --network-acl-id <network-acl-id>\n'
        'aws ec2 delete-security-group --group-id <app-sg-id>\n'
        'aws ec2 delete-security-group --group-id <web-sg-id>\n'
        'aws ec2 delete-subnet --subnet-id <subnet-id>\n'
        'aws ec2 delete-vpc --vpc-id <vpc-id>'
    )
    started = time.perf_counter()
    ec2 = _ec2_client()
    deleted_groups = 0
    deleted_nacl = False
    deleted_subnet = False
    deleted_vpc = False
    vpc = None
    try:
        vpc = _ec2_security_vpc()
    except ClientError:
        vpc = None
    if vpc:
        subnet_response = ec2.describe_subnets(
            Filters=[
                {'Name': 'vpc-id', 'Values': [vpc['VpcId']]},
                {'Name': 'cidr-block', 'Values': [EC2_SECURITY_SUBNET_CIDR]},
            ],
        )
        subnet = next(iter(subnet_response.get('Subnets', [])), None)
        network_acls = _ec2_security_network_acls(vpc['VpcId'])
        custom_acl = next(
            (acl for acl in network_acls if not acl.get('IsDefault')),
            None,
        )
        default_acl = next(
            (acl for acl in network_acls if acl.get('IsDefault')),
            None,
        )
        if custom_acl and default_acl and subnet:
            custom_association = next(
                (
                    item for item in custom_acl.get('Associations', [])
                    if item.get('SubnetId') == subnet.get('SubnetId')
                ),
                None,
            )
            if custom_association:
                ec2.replace_network_acl_association(
                    AssociationId=custom_association['NetworkAclAssociationId'],
                    NetworkAclId=default_acl['NetworkAclId'],
                )
        if custom_acl:
            ec2.delete_network_acl(NetworkAclId=custom_acl['NetworkAclId'])
            deleted_nacl = True
        groups = _ec2_security_groups(vpc['VpcId'])
        for group_name in (EC2_APP_SG_NAME, EC2_WEB_SG_NAME):
            group = groups.get(group_name)
            if group:
                ec2.delete_security_group(GroupId=group['GroupId'])
                deleted_groups += 1
        if subnet:
            ec2.delete_subnet(SubnetId=subnet['SubnetId'])
            deleted_subnet = True
        ec2.delete_vpc(VpcId=vpc['VpcId'])
        deleted_vpc = True
    cache.delete_many([
        EC2_SECURITY_VPC_ID_CACHE_KEY,
        EC2_SECURITY_SUBNET_ID_CACHE_KEY,
        EC2_WEB_SG_ID_CACHE_KEY,
        EC2_APP_SG_ID_CACHE_KEY,
        EC2_SG_REFERENCE_CACHE_KEY,
        EC2_NACL_ID_CACHE_KEY,
        EC2_NACL_ASSOC_ID_CACHE_KEY,
    ])
    output = {
        'deleted_network_acl': deleted_nacl,
        'deleted_security_groups': deleted_groups,
        'deleted_subnet': deleted_subnet,
        'deleted_vpc': deleted_vpc,
    }
    return {
        'service': 'ec2',
        'lab': 'security-controls',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': output,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **output,
        'verification': {
            'status': 'passed',
            'message': 'The custom network ACL, security groups, subnet, and dedicated VPC were removed.',
        },
    }


def _reset_ec2_s3_gateway_endpoint() -> dict[str, Any]:
    command = (
        'aws ec2 delete-vpc-endpoints --vpc-endpoint-ids <endpoint-id>\n'
        f'aws s3api delete-bucket --bucket {EC2_ENDPOINT_BUCKET_NAME}\n'
        'aws ec2 disassociate-route-table --association-id <association-id>\n'
        'aws ec2 delete-route-table --route-table-id <route-table-id>\n'
        'aws ec2 delete-subnet --subnet-id <subnet-id>\n'
        'aws ec2 delete-vpc --vpc-id <vpc-id>'
    )
    started = time.perf_counter()
    ec2 = _ec2_client()
    endpoint = _ec2_s3_gateway_endpoint()
    deleted_endpoint = False
    if endpoint:
        ec2.delete_vpc_endpoints(VpcEndpointIds=[endpoint['VpcEndpointId']])
        deleted_endpoint = True
    deleted_bucket = False
    try:
        _s3_client().delete_bucket(Bucket=EC2_ENDPOINT_BUCKET_NAME)
        deleted_bucket = True
    except ClientError:
        pass
    vpc = _ec2_endpoint_vpc()
    deleted_route_table = False
    deleted_subnet = False
    deleted_vpc = False
    if vpc:
        subnets = _ec2_lab_subnets(vpc['VpcId'])
        subnet = subnets.get(EC2_ENDPOINT_SUBNET_CIDR)
        tables = _ec2_lab_route_tables(vpc['VpcId'])
        table = next((
            item for item in tables
            if any(
                association.get('SubnetId') == (subnet or {}).get('SubnetId')
                for association in item.get('Associations', [])
            )
        ), None)
        if table:
            for association in table.get('Associations', []):
                association_id = association.get('RouteTableAssociationId')
                if association_id and not association.get('Main'):
                    ec2.disassociate_route_table(AssociationId=association_id)
            ec2.delete_route_table(RouteTableId=table['RouteTableId'])
            deleted_route_table = True
        if subnet:
            ec2.delete_subnet(SubnetId=subnet['SubnetId'])
            deleted_subnet = True
        ec2.delete_vpc(VpcId=vpc['VpcId'])
        deleted_vpc = True
    cache.delete_many([
        EC2_ENDPOINT_VPC_ID_CACHE_KEY,
        EC2_ENDPOINT_SUBNET_ID_CACHE_KEY,
        EC2_ENDPOINT_RT_ID_CACHE_KEY,
        EC2_ENDPOINT_ASSOC_ID_CACHE_KEY,
        EC2_ENDPOINT_ID_CACHE_KEY,
        EC2_ENDPOINT_CONFIGURED_CACHE_KEY,
        EC2_ENDPOINT_ROUTE_BOUNDARY_CACHE_KEY,
    ])
    output = {
        'deleted_endpoint': deleted_endpoint,
        'deleted_bucket': deleted_bucket,
        'deleted_route_table': deleted_route_table,
        'deleted_subnet': deleted_subnet,
        'deleted_vpc': deleted_vpc,
    }
    return {
        'service': 'ec2',
        'lab': 's3-gateway-endpoint',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': output,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **output,
        'verification': {
            'status': 'passed',
            'message': 'The endpoint, bucket, route table, subnet, and dedicated VPC were removed in dependency order.',
        },
    }


def _reset_ec2_sqs_interface_endpoint() -> dict[str, Any]:
    command = (
        'aws ec2 delete-vpc-endpoints --vpc-endpoint-ids <endpoint-id>\n'
        'aws sqs delete-queue --queue-url <queue-url>\n'
        'aws ec2 delete-security-group --group-id <endpoint-sg-id>\n'
        'aws ec2 delete-subnet --subnet-id <subnet-id>\n'
        'aws ec2 delete-vpc --vpc-id <vpc-id>'
    )
    started = time.perf_counter()
    ec2 = _ec2_client()
    endpoint = _ec2_sqs_interface_endpoint()
    deleted_endpoint = False
    if endpoint:
        ec2.delete_vpc_endpoints(VpcEndpointIds=[endpoint['VpcEndpointId']])
        deleted_endpoint = True
    deleted_queue = False
    try:
        queue_url = _sqs_queue_url(EC2_INTERFACE_QUEUE_NAME)
        _sqs_client().delete_queue(QueueUrl=queue_url)
        deleted_queue = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    vpc = _ec2_interface_vpc()
    deleted_group = False
    deleted_subnet = False
    deleted_vpc = False
    if vpc:
        groups = _ec2_client().describe_security_groups(Filters=[
            {'Name': 'vpc-id', 'Values': [vpc['VpcId']]},
            {'Name': 'group-name', 'Values': [EC2_INTERFACE_SG_NAME]},
        ]).get('SecurityGroups', [])
        group = next(iter(groups), None)
        if group:
            ec2.delete_security_group(GroupId=group['GroupId'])
            deleted_group = True
        subnets = ec2.describe_subnets(Filters=[
            {'Name': 'vpc-id', 'Values': [vpc['VpcId']]},
            {'Name': 'cidr-block', 'Values': [EC2_INTERFACE_SUBNET_CIDR]},
        ]).get('Subnets', [])
        subnet = next(iter(subnets), None)
        if subnet:
            ec2.delete_subnet(SubnetId=subnet['SubnetId'])
            deleted_subnet = True
        ec2.delete_vpc(VpcId=vpc['VpcId'])
        deleted_vpc = True
    cache.delete_many([
        EC2_INTERFACE_VPC_ID_CACHE_KEY,
        EC2_INTERFACE_SUBNET_ID_CACHE_KEY,
        EC2_INTERFACE_SG_ID_CACHE_KEY,
        EC2_INTERFACE_ENDPOINT_ID_CACHE_KEY,
        EC2_INTERFACE_CONFIGURED_CACHE_KEY,
        EC2_INTERFACE_INSPECTED_CACHE_KEY,
    ])
    output = {
        'deleted_endpoint': deleted_endpoint,
        'deleted_queue': deleted_queue,
        'deleted_security_group': deleted_group,
        'deleted_subnet': deleted_subnet,
        'deleted_vpc': deleted_vpc,
    }
    return {
        'service': 'ec2',
        'lab': 'sqs-interface-endpoint',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': output,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **output,
        'verification': {
            'status': 'passed',
            'message': 'The interface endpoint, queue, security group, subnet, and dedicated VPC were removed in dependency order.',
        },
    }


LABS = [
    EC2_VPC_NETWORKING_LAB,
    EC2_SECURITY_CONTROLS_LAB,
    EC2_S3_GATEWAY_ENDPOINT_LAB,
    EC2_SQS_INTERFACE_ENDPOINT_LAB,
    nat_three_tier.LAB,
    block_file_storage.LAB,
    network_scale_out.LAB,
    shared_responsibility.LAB,
    *GUIDED_EC2_LABS,
]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'ec2' and lab_key == 'nat-three-tier':
        from .ec2_nat_labs import run_step as nat_run_step
        return nat_run_step(step_key)

    if service_key == 'ec2' and lab_key == 'block-file-storage':
        from .ec2_storage_labs import run_step as storage_run_step
        return storage_run_step(step_key)

    if service_key == 'ec2' and lab_key == 'shared-responsibility':
        from .ec2_shared_responsibility_labs import run_step as srm_run_step
        return srm_run_step(step_key)

    if service_key == 'ec2' and lab_key == 'network-scale-out':
        from .ec2_network_labs import run_step as network_run_step
        return network_run_step(step_key)

    if service_key == 'ec2' and lab_key.startswith('guided-'):
        from .ec2_guided import run_guided_step
        return run_guided_step(lab_key, step_key)

    if service_key == 'ec2' and lab_key == 'vpc-public-private':
        runners = {
            'create-vpc': _run_ec2_create_vpc,
            'create-public-subnet': _run_ec2_create_public_subnet,
            'enable-public-ip': _run_ec2_enable_public_ip,
            'create-private-subnet': _run_ec2_create_private_subnet,
            'create-internet-gateway': _run_ec2_create_igw,
            'attach-internet-gateway': _run_ec2_attach_igw,
            'create-public-route-table': _run_ec2_create_public_route_table,
            'create-internet-route': _run_ec2_create_internet_route,
            'associate-public-route-table': _run_ec2_associate_public_route_table,
            'create-private-route-table': _run_ec2_create_private_route_table,
            'associate-private-route-table': _run_ec2_associate_private_route_table,
            'inspect-network-topology': _run_ec2_inspect_network_topology,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'ec2' and lab_key == 'security-controls':
        runners = {
            'create-vpc': _run_ec2_security_create_vpc,
            'create-subnet': _run_ec2_security_create_subnet,
            'create-web-security-group': lambda: _run_ec2_create_security_group(
                EC2_WEB_SG_NAME,
                'HTTPS web tier',
                EC2_WEB_SG_ID_CACHE_KEY,
                'create-web-security-group',
            ),
            'allow-trusted-https': _run_ec2_allow_trusted_https,
            'create-app-security-group': lambda: _run_ec2_create_security_group(
                EC2_APP_SG_NAME,
                'Private application tier',
                EC2_APP_SG_ID_CACHE_KEY,
                'create-app-security-group',
            ),
            'allow-web-to-app': _run_ec2_allow_web_to_app,
            'inspect-security-groups': _run_ec2_inspect_security_groups,
            'create-network-acl': _run_ec2_create_network_acl,
            'deny-ssh-network-acl': _run_ec2_deny_ssh_network_acl,
            'allow-https-network-acl': _run_ec2_allow_https_network_acl,
            'allow-return-network-acl': _run_ec2_allow_return_network_acl,
            'associate-network-acl': _run_ec2_associate_network_acl,
            'inspect-network-acl-support': _run_ec2_inspect_nacl_support,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'ec2' and lab_key == 's3-gateway-endpoint':
        runners = {
            'create-vpc': _run_ec2_endpoint_create_vpc,
            'create-private-subnet': _run_ec2_endpoint_create_subnet,
            'create-private-route-table': _run_ec2_endpoint_create_route_table,
            'associate-private-route-table': _run_ec2_endpoint_associate_route_table,
            'create-s3-bucket': _run_ec2_endpoint_create_bucket,
            'create-s3-gateway-endpoint': _run_ec2_create_s3_gateway_endpoint,
            'inspect-private-s3-path': _run_ec2_inspect_private_s3_path,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'ec2' and lab_key == 'sqs-interface-endpoint':
        runners = {
            'create-vpc': _run_ec2_interface_create_vpc,
            'create-private-subnet': _run_ec2_interface_create_subnet,
            'create-endpoint-security-group': _run_ec2_interface_create_security_group,
            'allow-vpc-https': _run_ec2_interface_allow_https,
            'create-sqs-queue': _run_ec2_interface_create_queue,
            'create-sqs-interface-endpoint': _run_ec2_create_sqs_interface_endpoint,
            'inspect-private-sqs-path': _run_ec2_inspect_private_sqs_path,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'ec2' and lab_key == 'nat-three-tier':
        from .ec2_nat_labs import status as nat_status
        return nat_status()

    if service_key == 'ec2' and lab_key == 'block-file-storage':
        from .ec2_storage_labs import status as storage_status
        return storage_status()

    if service_key == 'ec2' and lab_key == 'shared-responsibility':
        from .ec2_shared_responsibility_labs import status as srm_status
        return srm_status()

    if service_key == 'ec2' and lab_key == 'network-scale-out':
        from .ec2_network_labs import status as network_status
        return network_status()

    if service_key == 'ec2' and lab_key.startswith('guided-'):
        from .ec2_guided import guided_status
        return guided_status(lab_key)

    if service_key == 'ec2' and lab_key == 'vpc-public-private':
        vpc_verification = _verify_ec2_vpc()
        public_subnet_verification, private_subnet_verification = (
            _verify_ec2_subnets()
        )
        igw_verification = _verify_ec2_igw()
        public_route_verification, private_route_verification = (
            _verify_ec2_routes()
        )
        vpc_verified = vpc_verification.get('status') == 'passed'
        public_subnet_verified = (
            public_subnet_verification.get('status') == 'passed'
        )
        private_subnet_verified = (
            private_subnet_verification.get('status') == 'passed'
        )
        igw_verified = igw_verification.get('status') == 'passed'
        public_route_verified = (
            public_route_verification.get('status') == 'passed'
        )
        private_route_verified = (
            private_route_verification.get('status') == 'passed'
        )
        topology_verified = all([
            vpc_verified,
            public_subnet_verified,
            private_subnet_verified,
            igw_verified,
            public_route_verified,
            private_route_verified,
        ])
        topology_verification = {
            'status': 'passed',
            'message': 'The public and private subnet routing topology is complete.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': topology_verified,
            'steps': {
                'create-vpc': {
                    'verified': vpc_verified,
                    'verification': vpc_verification if vpc_verified else None,
                },
                'create-public-subnet': {
                    'verified': public_subnet_verified,
                    'verification': (
                        public_subnet_verification
                        if public_subnet_verified else None
                    ),
                },
                'enable-public-ip': {
                    'verified': public_subnet_verified,
                    'verification': (
                        public_subnet_verification
                        if public_subnet_verified else None
                    ),
                },
                'create-private-subnet': {
                    'verified': private_subnet_verified,
                    'verification': (
                        private_subnet_verification
                        if private_subnet_verified else None
                    ),
                },
                'create-internet-gateway': {
                    'verified': igw_verified,
                    'verification': igw_verification if igw_verified else None,
                },
                'attach-internet-gateway': {
                    'verified': igw_verified,
                    'verification': igw_verification if igw_verified else None,
                },
                'create-public-route-table': {
                    'verified': public_route_verified,
                    'verification': (
                        public_route_verification
                        if public_route_verified else None
                    ),
                },
                'create-internet-route': {
                    'verified': public_route_verified,
                    'verification': (
                        public_route_verification
                        if public_route_verified else None
                    ),
                },
                'associate-public-route-table': {
                    'verified': public_route_verified,
                    'verification': (
                        public_route_verification
                        if public_route_verified else None
                    ),
                },
                'create-private-route-table': {
                    'verified': private_route_verified,
                    'verification': (
                        private_route_verification
                        if private_route_verified else None
                    ),
                },
                'associate-private-route-table': {
                    'verified': private_route_verified,
                    'verification': (
                        private_route_verification
                        if private_route_verified else None
                    ),
                },
                'inspect-network-topology': {
                    'verified': topology_verified,
                    'verification': (
                        topology_verification if topology_verified else None
                    ),
                },
            },
        }

    if service_key == 'ec2' and lab_key == 'security-controls':
        vpc_verification = _verify_ec2_security_vpc()
        subnet_verification = _verify_ec2_security_subnet()
        web_verification, app_verification = _verify_ec2_security_group_rules()
        nacl_created_verification = _verify_ec2_network_acl_created()
        nacl_deny_ssh_verification = _verify_ec2_network_acl_rule(
            egress=False,
            rule_number=90,
            action='deny',
            cidr='0.0.0.0/0',
            from_port=22,
            to_port=22,
            success_message='The network ACL denies inbound SSH before later allow rules.',
        )
        nacl_allow_https_verification = _verify_ec2_network_acl_rule(
            egress=False,
            rule_number=100,
            action='allow',
            cidr=EC2_TRUSTED_CLIENT_CIDR,
            from_port=443,
            to_port=443,
            success_message='The network ACL allows inbound HTTPS from trusted clients.',
        )
        nacl_allow_return_verification = _verify_ec2_network_acl_rule(
            egress=True,
            rule_number=100,
            action='allow',
            cidr=EC2_TRUSTED_CLIENT_CIDR,
            from_port=1024,
            to_port=65535,
            success_message='The network ACL allows outbound ephemeral return traffic.',
        )
        nacl_rules_verification = _verify_ec2_network_acl_rules()
        nacl_association_verification = _verify_ec2_network_acl_association()
        vpc_verified = vpc_verification.get('status') == 'passed'
        subnet_verified = subnet_verification.get('status') == 'passed'
        web_verified = web_verification.get('status') == 'passed'
        app_verified = app_verification.get('status') == 'passed'
        nacl_created_verified = nacl_created_verification.get('status') == 'passed'
        nacl_deny_ssh_verified = nacl_deny_ssh_verification.get('status') == 'passed'
        nacl_allow_https_verified = (
            nacl_allow_https_verification.get('status') == 'passed'
        )
        nacl_allow_return_verified = (
            nacl_allow_return_verification.get('status') == 'passed'
        )
        nacl_rules_verified = nacl_rules_verification.get('status') == 'passed'
        nacl_association_verified = (
            nacl_association_verification.get('status') == 'passed'
        )
        nacl_verified = (
            nacl_created_verified
            and nacl_rules_verified
            and nacl_association_verified
        )
        groups_verified = web_verified and app_verified
        groups_verification = {
            'status': 'passed',
            'message': 'Both stateful security-group trust boundaries are active.',
        }
        nacl_verification = {
            'status': 'passed',
            'message': 'The custom network ACL has the intended stateless rules and subnet association.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                vpc_verified
                and subnet_verified
                and groups_verified
                and nacl_verified
            ),
            'steps': {
                'create-vpc': {
                    'verified': vpc_verified,
                    'verification': vpc_verification if vpc_verified else None,
                },
                'create-subnet': {
                    'verified': subnet_verified,
                    'verification': (
                        subnet_verification if subnet_verified else None
                    ),
                },
                'create-web-security-group': {
                    'verified': web_verified,
                    'verification': web_verification if web_verified else None,
                },
                'allow-trusted-https': {
                    'verified': web_verified,
                    'verification': web_verification if web_verified else None,
                },
                'create-app-security-group': {
                    'verified': app_verified,
                    'verification': app_verification if app_verified else None,
                },
                'allow-web-to-app': {
                    'verified': app_verified,
                    'verification': app_verification if app_verified else None,
                },
                'inspect-security-groups': {
                    'verified': groups_verified,
                    'verification': (
                        groups_verification if groups_verified else None
                    ),
                },
                'create-network-acl': {
                    'verified': nacl_created_verified,
                    'verification': (
                        nacl_created_verification
                        if nacl_created_verified else None
                    ),
                },
                'deny-ssh-network-acl': {
                    'verified': nacl_deny_ssh_verified,
                    'verification': (
                        nacl_deny_ssh_verification
                        if nacl_deny_ssh_verified else None
                    ),
                },
                'allow-https-network-acl': {
                    'verified': nacl_allow_https_verified,
                    'verification': (
                        nacl_allow_https_verification
                        if nacl_allow_https_verified else None
                    ),
                },
                'allow-return-network-acl': {
                    'verified': nacl_allow_return_verified,
                    'verification': (
                        nacl_allow_return_verification
                        if nacl_allow_return_verified else None
                    ),
                },
                'associate-network-acl': {
                    'verified': nacl_association_verified,
                    'verification': (
                        nacl_association_verification
                        if nacl_association_verified else None
                    ),
                },
                'inspect-network-acl-support': {
                    'verified': nacl_verified,
                    'verification': nacl_verification if nacl_verified else None,
                },
            },
        }

    if service_key == 'ec2' and lab_key == 's3-gateway-endpoint':
        verifications = {
            'vpc': _verify_ec2_endpoint_vpc(),
            'subnet': _verify_ec2_endpoint_subnet(),
            'route': _verify_ec2_endpoint_route_table(),
            'bucket': _verify_ec2_endpoint_bucket(),
            'endpoint': _verify_ec2_s3_gateway_endpoint(),
            'boundary': _verify_ec2_endpoint_route_boundary(),
        }
        passed = {
            key: value.get('status') == 'passed'
            for key, value in verifications.items()
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': all(passed.values()),
            'steps': {
                'create-vpc': {'verified': passed['vpc'], 'verification': verifications['vpc'] if passed['vpc'] else None},
                'create-private-subnet': {'verified': passed['subnet'], 'verification': verifications['subnet'] if passed['subnet'] else None},
                'create-private-route-table': {'verified': passed['route'], 'verification': verifications['route'] if passed['route'] else None},
                'associate-private-route-table': {'verified': passed['route'], 'verification': verifications['route'] if passed['route'] else None},
                'create-s3-bucket': {'verified': passed['bucket'], 'verification': verifications['bucket'] if passed['bucket'] else None},
                'create-s3-gateway-endpoint': {'verified': passed['endpoint'], 'verification': verifications['endpoint'] if passed['endpoint'] else None},
                'inspect-private-s3-path': {
                    'verified': passed['endpoint'] and passed['route'] and passed['bucket'] and passed['boundary'],
                    'verification': verifications['boundary'] if passed['boundary'] else None,
                },
            },
        }

    if service_key == 'ec2' and lab_key == 'sqs-interface-endpoint':
        verifications = {
            'vpc': _verify_ec2_interface_vpc(),
            'subnet': _verify_ec2_interface_subnet(),
            'security_group': _verify_ec2_interface_security_group(),
            'queue': _verify_ec2_interface_queue(),
            'endpoint': _verify_ec2_sqs_interface_endpoint(),
            'inspection': _verify_ec2_interface_inspection(),
        }
        passed = {
            key: value.get('status') == 'passed'
            for key, value in verifications.items()
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': all(passed.values()),
            'steps': {
                'create-vpc': {'verified': passed['vpc'], 'verification': verifications['vpc'] if passed['vpc'] else None},
                'create-private-subnet': {'verified': passed['subnet'], 'verification': verifications['subnet'] if passed['subnet'] else None},
                'create-endpoint-security-group': {'verified': passed['security_group'], 'verification': verifications['security_group'] if passed['security_group'] else None},
                'allow-vpc-https': {'verified': passed['security_group'], 'verification': verifications['security_group'] if passed['security_group'] else None},
                'create-sqs-queue': {'verified': passed['queue'], 'verification': verifications['queue'] if passed['queue'] else None},
                'create-sqs-interface-endpoint': {'verified': passed['endpoint'], 'verification': verifications['endpoint'] if passed['endpoint'] else None},
                'inspect-private-sqs-path': {
                    'verified': passed['endpoint'] and passed['queue'] and passed['inspection'],
                    'verification': verifications['inspection'] if passed['inspection'] else None,
                },
            },
        }

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'ec2' and lab_key == 'nat-three-tier':
        from .ec2_nat_labs import reset as nat_reset
        return nat_reset()

    if service_key == 'ec2' and lab_key == 'block-file-storage':
        from .ec2_storage_labs import reset as storage_reset
        return storage_reset()

    if service_key == 'ec2' and lab_key == 'shared-responsibility':
        from .ec2_shared_responsibility_labs import reset as srm_reset
        return srm_reset()

    if service_key == 'ec2' and lab_key == 'network-scale-out':
        from .ec2_network_labs import reset as network_reset
        return network_reset()

    if service_key == 'ec2' and lab_key.startswith('guided-'):
        from .ec2_guided import reset_guided_lab
        return reset_guided_lab(lab_key)

    if service_key == 'ec2' and lab_key == 'vpc-public-private':
        return _reset_ec2_vpc_networking()

    if service_key == 'ec2' and lab_key == 'security-controls':
        return _reset_ec2_security_controls()

    if service_key == 'ec2' and lab_key == 's3-gateway-endpoint':
        return _reset_ec2_s3_gateway_endpoint()

    if service_key == 'ec2' and lab_key == 'sqs-interface-endpoint':
        return _reset_ec2_sqs_interface_endpoint()

    raise ValueError('Lab reset not found')
