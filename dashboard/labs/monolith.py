"""Curated local AWS workflow labs."""

from __future__ import annotations

import json
import zipfile
import base64
from io import BytesIO
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from botocore.exceptions import ClientError
from django.core.cache import cache

from ..aws import FlociClientFactory, _clean_response

AWS_ACCOUNT_ID = '000000000000'
AWS_REGION = 'us-east-1'
ALICE_USER_NAME = 'Alice'
FLOCI_ADMIN_USER_NAME = 'floci-admin'
FLOCI_ADMIN_POLICY_NAME = 'AdministratorAccess'
FLOCI_ADMIN_ACCESS_KEY_CACHE_KEY = 'floci-lab:iam:create-admin-user:access-key-id'
FLOCI_ADMIN_SECRET_KEY_CACHE_KEY = 'floci-lab:iam:create-admin-user:secret-access-key'
FLOCI_ADMIN_IDENTITY_CACHE_KEY = 'floci-lab:iam:create-admin-user:identity'
FLOCI_ADMIN_POLICY_DOCUMENT = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': '*',
            'Resource': '*',
        },
    ],
}
ALICE_POLICY_NAME = 'AliceListBucketsPolicy'
ALICE_POLICY_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:policy/{ALICE_POLICY_NAME}'
ALICE_INLINE_POLICY_NAME = 'AliceInlineListBuckets'
FLOCI_DEVELOPERS_GROUP_NAME = 'FlociDevelopers'
FLOCI_DEVELOPERS_POLICY_NAME = 'FlociDevelopersListBucketsPolicy'
FLOCI_DEVELOPERS_POLICY_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:policy/{FLOCI_DEVELOPERS_POLICY_NAME}'
FLOCI_APPLICATION_ROLE_NAME = 'FlociApplicationRole'
FLOCI_APPLICATION_ROLE_POLICY_NAME = 'FlociApplicationListBuckets'
FLOCI_STS_SESSION_ROLE_NAME = 'FlociStsSessionRole'
FLOCI_STS_SESSION_NAME = 'floci-session-policy-lab'
FLOCI_STS_ROLE_POLICY_NAME = 'FlociStsListBuckets'
FLOCI_EC2_ROLE_NAME = 'FlociEc2Role'
FLOCI_EC2_INSTANCE_PROFILE_NAME = 'FlociEc2InstanceProfile'
CLOUDFORMATION_STACK_NAME = 'lab-storage-messaging'
CLOUDFORMATION_BUCKET_NAME = 'lab-cfn-storage'
CLOUDFORMATION_QUEUE_NAME = 'lab-cfn-jobs'
CLOUDFORMATION_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{CLOUDFORMATION_QUEUE_NAME}'
)
CLOUDFORMATION_TEMPLATE = {
    'AWSTemplateFormatVersion': '2010-09-09',
    'Description': 'Provision local S3 storage and an SQS work queue.',
    'Resources': {
        'StorageBucket': {
            'Type': 'AWS::S3::Bucket',
            'Properties': {
                'BucketName': CLOUDFORMATION_BUCKET_NAME,
            },
        },
        'JobsQueue': {
            'Type': 'AWS::SQS::Queue',
            'Properties': {
                'QueueName': CLOUDFORMATION_QUEUE_NAME,
                'VisibilityTimeout': 30,
            },
        },
    },
    'Outputs': {
        'BucketName': {
            'Description': 'Name of the provisioned storage bucket.',
            'Value': {'Ref': 'StorageBucket'},
        },
        'QueueUrl': {
            'Description': 'URL of the provisioned work queue.',
            'Value': {'Ref': 'JobsQueue'},
        },
        'QueueArn': {
            'Description': 'ARN of the provisioned work queue.',
            'Value': {'Fn::GetAtt': ['JobsQueue', 'Arn']},
        },
    },
}
CLOUDFORMATION_TEMPLATE_TEXT = json.dumps(
    CLOUDFORMATION_TEMPLATE,
    indent=2,
)
CLOUDFORMATION_CREATED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:created'
CLOUDFORMATION_INSPECTED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:inspected'
CLOUDFORMATION_DELETED_CACHE_KEY = 'floci-lab:cloudformation:s3-sqs:deleted'
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
ALICE_LIST_BUCKETS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': 's3:ListAllMyBuckets',
            'Resource': '*',
        },
    ],
}
FLOCI_APPLICATION_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Principal': {'Service': 'lambda.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        },
    ],
}
FLOCI_STS_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Principal': {'AWS': f'arn:aws:iam::{AWS_ACCOUNT_ID}:root'},
            'Action': 'sts:AssumeRole',
        },
    ],
}
FLOCI_STS_SESSION_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': 's3:ListAllMyBuckets',
            'Resource': '*',
        },
    ],
}
FLOCI_STS_ASSUME_CACHE_KEY = 'floci-lab:iam:sts-session-policy:assumed'
IAM_ENFORCEMENT_USER_NAME = 'Charlie'
IAM_ENFORCEMENT_ROLE_NAME = 'CharlieSqsReadRole'
IAM_ENFORCEMENT_ROLE_POLICY_NAME = 'CharlieSqsRead'
IAM_ENFORCEMENT_SESSION_NAME = 'floci-enforcement-lab'
IAM_ENFORCEMENT_USER_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:user/{IAM_ENFORCEMENT_USER_NAME}'
IAM_ENFORCEMENT_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{IAM_ENFORCEMENT_ROLE_NAME}'
IAM_ENFORCEMENT_USER_KEY_CACHE_KEY = 'floci-lab:iam:enforcement:user-key'
IAM_ENFORCEMENT_USER_SECRET_CACHE_KEY = 'floci-lab:iam:enforcement:user-secret'
IAM_ENFORCEMENT_USER_DENY_CACHE_KEY = 'floci-lab:iam:enforcement:user-s3-denied'
IAM_ENFORCEMENT_ROLE_CREDENTIALS_CACHE_KEY = 'floci-lab:iam:enforcement:role-credentials'
IAM_ENFORCEMENT_ROLE_SQS_CACHE_KEY = 'floci-lab:iam:enforcement:role-sqs-allowed'
IAM_ENFORCEMENT_ROLE_S3_DENY_CACHE_KEY = 'floci-lab:iam:enforcement:role-s3-denied'
IAM_ENFORCEMENT_SELF_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': 'sts:GetCallerIdentity',
            'Resource': '*',
        },
        {
            'Effect': 'Allow',
            'Action': 'sts:AssumeRole',
            'Resource': '*',
        },
    ],
}
IAM_ENFORCEMENT_ROLE_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Principal': {'AWS': IAM_ENFORCEMENT_USER_ARN},
        'Action': 'sts:AssumeRole',
    }],
}
IAM_ENFORCEMENT_ROLE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': ['sqs:ListQueues', 'sts:GetCallerIdentity'],
            'Resource': '*',
        },
    ],
}
FLOCI_EC2_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Principal': {'Service': 'ec2.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        },
    ],
}
IAM_CREATE_USER_LAB = {
    'service': 'iam',
    'key': 'create-user-alice',
    'title': 'Create an IAM user',
    'description': 'Create a local IAM user named Alice and verify that IAM can read it back.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create user Alice',
            'command': 'aws iam create-user --user-name Alice',
            'explanation': 'Creates a local IAM user named Alice through the IAM CreateUser API.',
        },
    ],
}

IAM_CREATE_ADMIN_USER_LAB = {
    'service': 'iam',
    'key': 'create-admin-user',
    'title': 'Create a local admin user',
    'description': 'Follow the first responsible AWS habit: use bootstrap credentials only to create an administrator identity, then do daily work as that admin user.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create the admin user',
            'command': f'aws iam create-user --user-name {FLOCI_ADMIN_USER_NAME}',
            'explanation': 'Creates a local IAM user that will become the administrator identity for normal Floci work.',
        },
        {
            'key': 'put-admin-policy',
            'title': 'Attach administrator permissions',
            'command': f'aws iam put-user-policy --user-name {FLOCI_ADMIN_USER_NAME} --policy-name {FLOCI_ADMIN_POLICY_NAME} --policy-document file://administrator-access.json',
            'explanation': 'Adds an inline administrator policy. In real AWS, this is the moment where you stop using root for daily work.',
            'artifact_label': 'administrator-access.json',
            'artifact': json.dumps(FLOCI_ADMIN_POLICY_DOCUMENT, indent=2),
        },
        {
            'key': 'create-access-key',
            'title': 'Create admin access keys',
            'command': f'aws iam create-access-key --user-name {FLOCI_ADMIN_USER_NAME}',
            'explanation': 'Creates programmatic credentials for the admin user so local tools can use that identity.',
        },
        {
            'key': 'verify-admin-identity',
            'title': 'Verify the admin identity',
            'command': 'aws sts get-caller-identity',
            'explanation': 'Uses the generated admin access key to confirm that the daily-work identity is the admin IAM user, not the bootstrap root-like credentials.',
        },
    ],
}

IAM_ATTACH_POLICY_LAB = {
    'service': 'iam',
    'key': 'attach-policy-alice',
    'title': 'Attach a managed policy to Alice',
    'description': 'Create a customer managed policy, attach it to Alice, and inspect the policy attachment.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create user Alice',
            'command': 'aws iam create-user --user-name Alice',
            'explanation': 'Ensures the IAM user Alice exists before permissions are attached.',
        },
        {
            'key': 'create-policy',
            'title': 'Create a customer managed policy',
            'command': 'aws iam create-policy --policy-name AliceListBucketsPolicy --policy-document file://alice-list-buckets-policy.json',
            'explanation': 'Creates a reusable customer managed policy that allows listing S3 buckets.',
            'artifact_label': 'alice-list-buckets-policy.json',
            'artifact': json.dumps(ALICE_LIST_BUCKETS_POLICY, indent=2),
        },
        {
            'key': 'attach-policy',
            'title': 'Attach the policy to Alice',
            'command': f'aws iam attach-user-policy --user-name Alice --policy-arn {ALICE_POLICY_ARN}',
            'explanation': 'Attaches the customer managed policy to Alice as an identity-based permission.',
        },
        {
            'key': 'list-attached-policies',
            'title': 'List Alice attached policies',
            'command': 'aws iam list-attached-user-policies --user-name Alice',
            'explanation': 'Reads Alice policy attachments and verifies the managed policy is attached.',
        },
    ],
}

IAM_ACCESS_KEY_LAB = {
    'service': 'iam',
    'key': 'access-key-alice',
    'title': 'Create an access key for Alice',
    'description': 'Create programmatic credentials for Alice, then list the key metadata IAM stores.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create user Alice',
            'command': 'aws iam create-user --user-name Alice',
            'explanation': 'Ensures the IAM user Alice exists before issuing programmatic credentials.',
        },
        {
            'key': 'create-access-key',
            'title': 'Create Alice access key',
            'command': 'aws iam create-access-key --user-name Alice',
            'explanation': 'Creates an access key pair for Alice. In real AWS, the secret access key is shown only once.',
        },
        {
            'key': 'list-access-keys',
            'title': 'List Alice access keys',
            'command': 'aws iam list-access-keys --user-name Alice',
            'explanation': 'Lists Alice access key metadata, including key ID, status, and create date.',
        },
    ],
}

IAM_GROUP_MEMBERSHIP_LAB = {
    'service': 'iam',
    'key': 'group-membership-alice',
    'title': 'Add Alice to an IAM group',
    'description': 'Create an IAM group, add Alice to it, and inspect the group membership.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create user Alice',
            'command': 'aws iam create-user --user-name Alice',
            'explanation': 'Ensures the IAM user Alice exists before adding her to a group.',
        },
        {
            'key': 'create-group',
            'title': 'Create the FlociDevelopers group',
            'command': f'aws iam create-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME}',
            'explanation': 'Creates an IAM group that can collect users under shared permissions.',
        },
        {
            'key': 'add-user-to-group',
            'title': 'Add Alice to the group',
            'command': f'aws iam add-user-to-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME} --user-name Alice',
            'explanation': 'Adds Alice as a member of the FlociDevelopers group.',
        },
        {
            'key': 'get-group',
            'title': 'Inspect group membership',
            'command': f'aws iam get-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME}',
            'explanation': 'Reads the group and verifies that Alice appears in the returned Users list.',
        },
    ],
}

IAM_GROUP_POLICY_LAB = {
    'service': 'iam',
    'key': 'group-policy-floci-developers',
    'title': 'Attach a policy to an IAM group',
    'description': 'Create a group policy, attach it to FlociDevelopers, and inspect the group policy attachment.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create user Alice',
            'command': 'aws iam create-user --user-name Alice',
            'explanation': 'Ensures the IAM user Alice exists before building the group permissions workflow.',
        },
        {
            'key': 'create-group',
            'title': 'Create the FlociDevelopers group',
            'command': f'aws iam create-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME}',
            'explanation': 'Ensures the group exists before permissions are attached to it.',
        },
        {
            'key': 'add-user-to-group',
            'title': 'Add Alice to the group',
            'command': f'aws iam add-user-to-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME} --user-name Alice',
            'explanation': 'Adds Alice to the group so group permissions can apply to a user.',
        },
        {
            'key': 'create-policy',
            'title': 'Create a group managed policy',
            'command': f'aws iam create-policy --policy-name {FLOCI_DEVELOPERS_POLICY_NAME} --policy-document file://floci-developers-list-buckets-policy.json',
            'explanation': 'Creates a reusable customer managed policy for the FlociDevelopers group.',
            'artifact_label': 'floci-developers-list-buckets-policy.json',
            'artifact': json.dumps(ALICE_LIST_BUCKETS_POLICY, indent=2),
        },
        {
            'key': 'attach-group-policy',
            'title': 'Attach the policy to the group',
            'command': f'aws iam attach-group-policy --group-name {FLOCI_DEVELOPERS_GROUP_NAME} --policy-arn {FLOCI_DEVELOPERS_POLICY_ARN}',
            'explanation': 'Attaches the managed policy to the group so members inherit the permission.',
        },
        {
            'key': 'list-attached-group-policies',
            'title': 'List group attached policies',
            'command': f'aws iam list-attached-group-policies --group-name {FLOCI_DEVELOPERS_GROUP_NAME}',
            'explanation': 'Reads group policy attachments and verifies the managed policy is attached.',
        },
    ],
}

IAM_INLINE_POLICY_LAB = {
    'service': 'iam',
    'key': 'inline-policy-alice',
    'title': 'Attach an inline policy to Alice',
    'description': 'Create an inline policy embedded in Alice, then list and inspect the policy document.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create user Alice',
            'command': 'aws iam create-user --user-name Alice',
            'explanation': 'Ensures the IAM user Alice exists before an inline policy is added.',
        },
        {
            'key': 'put-user-policy',
            'title': 'Add an inline policy to Alice',
            'command': f'aws iam put-user-policy --user-name Alice --policy-name {ALICE_INLINE_POLICY_NAME} --policy-document file://alice-inline-list-buckets-policy.json',
            'explanation': 'Embeds a policy directly in Alice instead of creating a separately reusable managed policy.',
            'artifact_label': 'alice-inline-list-buckets-policy.json',
            'artifact': json.dumps(ALICE_LIST_BUCKETS_POLICY, indent=2),
        },
        {
            'key': 'list-user-policies',
            'title': 'List Alice inline policies',
            'command': 'aws iam list-user-policies --user-name Alice',
            'explanation': 'Lists the names of inline policies embedded in Alice.',
        },
        {
            'key': 'get-user-policy',
            'title': 'Inspect the inline policy',
            'command': f'aws iam get-user-policy --user-name Alice --policy-name {ALICE_INLINE_POLICY_NAME}',
            'explanation': 'Retrieves the inline policy document and verifies its permissions.',
        },
    ],
}

IAM_ROLE_TRUST_LAB = {
    'service': 'iam',
    'key': 'role-trust-policy',
    'title': 'Create a role with a trust policy',
    'description': 'Create an application role, inspect who can assume it, and add the permissions the role receives.',
    'steps': [
        {
            'key': 'create-role',
            'title': 'Create the application role',
            'command': f'aws iam create-role --role-name {FLOCI_APPLICATION_ROLE_NAME} --assume-role-policy-document file://floci-application-role-trust-policy.json',
            'explanation': 'Creates a role whose trust policy allows the Lambda service to assume it.',
            'artifact_label': 'floci-application-role-trust-policy.json',
            'artifact': json.dumps(FLOCI_APPLICATION_TRUST_POLICY, indent=2),
        },
        {
            'key': 'get-role',
            'title': 'Inspect the role and trust policy',
            'command': f'aws iam get-role --role-name {FLOCI_APPLICATION_ROLE_NAME}',
            'explanation': 'Returns the role metadata and its assume-role trust policy.',
        },
        {
            'key': 'put-role-policy',
            'title': 'Add role permissions',
            'command': f'aws iam put-role-policy --role-name {FLOCI_APPLICATION_ROLE_NAME} --policy-name {FLOCI_APPLICATION_ROLE_POLICY_NAME} --policy-document file://floci-application-list-buckets-policy.json',
            'explanation': 'Adds an inline permissions policy that defines what the assumed role can do.',
            'artifact_label': 'floci-application-list-buckets-policy.json',
            'artifact': json.dumps(ALICE_LIST_BUCKETS_POLICY, indent=2),
        },
        {
            'key': 'get-role-policy',
            'title': 'Inspect the role permissions',
            'command': f'aws iam get-role-policy --role-name {FLOCI_APPLICATION_ROLE_NAME} --policy-name {FLOCI_APPLICATION_ROLE_POLICY_NAME}',
            'explanation': 'Retrieves the inline permissions policy so it can be compared with the role trust policy.',
        },
    ],
}

IAM_STS_SESSION_POLICY_LAB = {
    'service': 'iam',
    'key': 'sts-session-policy',
    'title': 'Assume a role with an STS session policy',
    'description': 'Create a trusted role, attach base permissions, then assume it with an inline session policy so STS policy enforcement can be exercised locally.',
    'steps': [
        {
            'key': 'create-role',
            'title': 'Create the STS test role',
            'command': f'aws iam create-role --role-name {FLOCI_STS_SESSION_ROLE_NAME} --assume-role-policy-document file://floci-sts-role-trust-policy.json',
            'explanation': 'Creates a role whose trust policy allows the local account root principal to call sts:AssumeRole.',
            'artifact_label': 'floci-sts-role-trust-policy.json',
            'artifact': json.dumps(FLOCI_STS_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Add base role permissions',
            'command': f'aws iam put-role-policy --role-name {FLOCI_STS_SESSION_ROLE_NAME} --policy-name {FLOCI_STS_ROLE_POLICY_NAME} --policy-document file://floci-sts-list-buckets-policy.json',
            'explanation': 'Adds the role identity policy that the session policy can further constrain.',
            'artifact_label': 'floci-sts-list-buckets-policy.json',
            'artifact': json.dumps(ALICE_LIST_BUCKETS_POLICY, indent=2),
        },
        {
            'key': 'assume-role',
            'title': 'Assume the role with a session policy',
            'command': f'aws sts assume-role --role-arn arn:aws:iam::{AWS_ACCOUNT_ID}:role/{FLOCI_STS_SESSION_ROLE_NAME} --role-session-name {FLOCI_STS_SESSION_NAME} --policy file://session-policy.json',
            'explanation': 'Requests temporary credentials with an inline STS session policy, exercising the session-policy enforcement path added in Floci 1.5.30.',
            'artifact_label': 'session-policy.json',
            'artifact': json.dumps(FLOCI_STS_SESSION_POLICY, indent=2),
        },
    ],
}

IAM_INSTANCE_PROFILE_LAB = {
    'service': 'iam',
    'key': 'ec2-instance-profile',
    'title': 'Create an EC2 instance profile',
    'description': 'Create an EC2-trusted role, place it in an instance profile, and verify the association.',
    'steps': [
        {
            'key': 'create-role',
            'title': 'Create the EC2 role',
            'command': f'aws iam create-role --role-name {FLOCI_EC2_ROLE_NAME} --assume-role-policy-document file://floci-ec2-role-trust-policy.json',
            'explanation': 'Creates a role whose trust policy allows the EC2 service to assume it.',
            'artifact_label': 'floci-ec2-role-trust-policy.json',
            'artifact': json.dumps(FLOCI_EC2_TRUST_POLICY, indent=2),
        },
        {
            'key': 'create-instance-profile',
            'title': 'Create the instance profile',
            'command': f'aws iam create-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME}',
            'explanation': 'Creates the IAM container EC2 uses to make a role available to an instance.',
        },
        {
            'key': 'add-role-to-instance-profile',
            'title': 'Add the role to the profile',
            'command': f'aws iam add-role-to-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME} --role-name {FLOCI_EC2_ROLE_NAME}',
            'explanation': 'Associates the EC2-trusted role with the instance profile.',
        },
        {
            'key': 'get-instance-profile',
            'title': 'Inspect the instance profile',
            'command': f'aws iam get-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME}',
            'explanation': 'Returns the instance profile and verifies that it contains FlociEc2Role.',
        },
        {
            'key': 'list-instance-profiles-for-role',
            'title': 'List profiles for the role',
            'command': f'aws iam list-instance-profiles-for-role --role-name {FLOCI_EC2_ROLE_NAME}',
            'explanation': 'Reads the relationship from the role side and verifies the same association.',
        },
    ],
}

IAM_ENFORCEMENT_CAPSTONE_LAB = {
    'service': 'iam',
    'key': 'identity-enforcement-capstone',
    'title': 'Switch identities and verify IAM enforcement',
    'description': 'Create Charlie, prove direct S3 access is denied, assume an SQS-scoped role, then verify SQS succeeds while S3 remains denied.',
    'steps': [
        {
            'key': 'create-user',
            'title': 'Create user Charlie',
            'command': f'aws iam create-user --user-name {IAM_ENFORCEMENT_USER_NAME}',
            'explanation': 'Creates the IAM user that will become the restricted dashboard identity.',
        },
        {
            'key': 'verify-user-denied',
            'title': 'Verify Charlie cannot list S3 buckets',
            'command': f'aws s3 ls  # using {IAM_ENFORCEMENT_USER_NAME} credentials',
            'explanation': 'Uses Charlie credentials and expects S3 ListBuckets to fail because Charlie has no S3 permissions.',
        },
        {
            'key': 'create-role',
            'title': 'Create the SQS read role',
            'command': f'aws iam create-role --role-name {IAM_ENFORCEMENT_ROLE_NAME} --assume-role-policy-document file://charlie-sqs-role-trust-policy.json',
            'explanation': 'Creates a role that Charlie is allowed to assume.',
            'artifact_label': 'charlie-sqs-role-trust-policy.json',
            'artifact': json.dumps(IAM_ENFORCEMENT_ROLE_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow SQS listing on the role',
            'command': f'aws iam put-role-policy --role-name {IAM_ENFORCEMENT_ROLE_NAME} --policy-name {IAM_ENFORCEMENT_ROLE_POLICY_NAME} --policy-document file://charlie-sqs-read-policy.json',
            'explanation': 'Adds only SQS ListQueues and STS identity permissions to the role.',
            'artifact_label': 'charlie-sqs-read-policy.json',
            'artifact': json.dumps(IAM_ENFORCEMENT_ROLE_POLICY, indent=2),
        },
        {
            'key': 'assume-role',
            'title': 'Assume the SQS role as Charlie',
            'command': f'aws sts assume-role --role-arn {IAM_ENFORCEMENT_ROLE_ARN} --role-session-name {IAM_ENFORCEMENT_SESSION_NAME}  # using {IAM_ENFORCEMENT_USER_NAME} credentials',
            'explanation': 'Requests temporary role credentials using Charlie as the caller. Charlie needs both an identity policy allowing sts:AssumeRole and a role trust policy that names Charlie as the trusted principal. Floci currently accepts the local AssumeRole identity allow with a wildcard resource while the role trust policy keeps this lab scoped to Charlie.',
        },
        {
            'key': 'verify-role-sqs',
            'title': 'Verify the role can list SQS queues',
            'command': 'aws sqs list-queues  # using assumed-role credentials',
            'explanation': 'Uses the assumed-role credentials and expects SQS ListQueues to succeed.',
        },
        {
            'key': 'verify-role-s3-denied',
            'title': 'Verify the role still cannot list S3 buckets',
            'command': 'aws s3 ls  # using assumed-role credentials',
            'explanation': 'Uses the same assumed-role credentials and expects S3 ListBuckets to remain denied.',
        },
    ],
}

CLOUDFORMATION_S3_SQS_LAB = {
    'service': 'cloudformation',
    'key': 's3-sqs-stack',
    'title': 'Provision S3 and SQS resources with CloudFormation',
    'description': 'Validate one infrastructure template, create a stack, inspect its outputs, resources, and events, then delete the stack and verify cleanup.',
    'steps': [
        {
            'key': 'validate-template',
            'title': 'Validate the infrastructure template',
            'command': 'aws cloudformation validate-template --template-body file://storage-messaging-stack.json',
            'explanation': 'Checks the template structure before creating any resources.',
            'artifact_label': 'storage-messaging-stack.json',
            'artifact': CLOUDFORMATION_TEMPLATE_TEXT,
        },
        {
            'key': 'create-stack',
            'title': 'Create the stack',
            'command': f'aws cloudformation create-stack --stack-name {CLOUDFORMATION_STACK_NAME} --template-body file://storage-messaging-stack.json',
            'explanation': 'Asks CloudFormation to provision the named S3 bucket and SQS queue as one managed unit.',
        },
        {
            'key': 'describe-stack',
            'title': 'Inspect stack status and outputs',
            'command': f'aws cloudformation describe-stacks --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Requires CREATE_COMPLETE and verifies the bucket name, queue URL, and queue ARN outputs.',
        },
        {
            'key': 'describe-stack-resources',
            'title': 'Map logical resources to physical resources',
            'command': f'aws cloudformation describe-stack-resources --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Shows how StorageBucket and JobsQueue map to their real AWS-shaped identifiers.',
        },
        {
            'key': 'describe-stack-events',
            'title': 'Inspect the provisioning timeline',
            'command': f'aws cloudformation describe-stack-events --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Verifies CREATE_IN_PROGRESS and CREATE_COMPLETE events for the stack and both resources.',
        },
        {
            'key': 'inspect-provisioned-resources',
            'title': 'Verify the provisioned services',
            'command': f'aws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\naws sqs get-queue-attributes --queue-url <queue-url> --attribute-names QueueArn VisibilityTimeout',
            'explanation': 'Confirms that S3 and SQS expose the resources with the expected queue ARN and visibility timeout.',
        },
        {
            'key': 'delete-stack',
            'title': 'Delete the stack',
            'command': f'aws cloudformation delete-stack --stack-name {CLOUDFORMATION_STACK_NAME}',
            'explanation': 'Deletes the CloudFormation ownership unit instead of deleting the bucket and queue manually.',
        },
        {
            'key': 'confirm-resources-deleted',
            'title': 'Confirm stack-owned cleanup',
            'command': f'aws cloudformation describe-stacks --stack-name {CLOUDFORMATION_STACK_NAME}\naws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\naws sqs get-queue-url --queue-name {CLOUDFORMATION_QUEUE_NAME}',
            'explanation': 'Requires the stack, bucket, and queue to be absent after deletion.',
        },
    ],
}

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
            'command': f'aws ec2 authorize-security-group-ingress --group-id <web-sg-id> --ip-permissions file://trusted-https.json',
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
            'command': f'aws ec2 describe-vpc-endpoints --vpc-endpoint-ids <endpoint-id>\naws ec2 describe-network-interfaces --network-interface-ids <eni-id>\naws sqs get-queue-attributes --queue-url <queue-url> --attribute-names QueueArn',
            'explanation': 'Checks endpoint subnet placement, security groups, private DNS, endpoint ENIs, and the queue protected by the endpoint policy.',
        },
    ],
}


def labs_for_service(service_key: str) -> list[dict[str, Any]]:
    if service_key == 'iam':
        return [
            IAM_CREATE_ADMIN_USER_LAB,
            IAM_CREATE_USER_LAB,
            IAM_ATTACH_POLICY_LAB,
            IAM_ACCESS_KEY_LAB,
            IAM_GROUP_MEMBERSHIP_LAB,
            IAM_GROUP_POLICY_LAB,
            IAM_INLINE_POLICY_LAB,
            IAM_ROLE_TRUST_LAB,
            IAM_STS_SESSION_POLICY_LAB,
            IAM_INSTANCE_PROFILE_LAB,
            IAM_ENFORCEMENT_CAPSTONE_LAB,
        ]
    if service_key == 's3':
        from .s3_labs import LABS
        return LABS
    if service_key == 'sqs':
        from .sqs_labs import LABS
        return LABS
    if service_key == 'sns':
        from .sns_labs import LABS
        return LABS
    if service_key == 'scheduler':
        from .scheduler_labs import LABS
        return LABS
    if service_key == 'cloudformation':
        return [CLOUDFORMATION_S3_SQS_LAB]
    if service_key == 'ec2':
        from .ec2_guided import GUIDED_EC2_LABS
        return [
            EC2_VPC_NETWORKING_LAB,
            EC2_SECURITY_CONTROLS_LAB,
            EC2_S3_GATEWAY_ENDPOINT_LAB,
            EC2_SQS_INTERFACE_ENDPOINT_LAB,
            *GUIDED_EC2_LABS,
        ]
    if service_key == 'lambda':
        from .lambda_labs import LABS
        return LABS
    if service_key == 'apigateway':
        from .apigateway_labs import LABS
        return LABS
    if service_key == 'eventbridge':
        from .eventbridge_application import LAB
        return [LAB]
    if service_key == 'dynamodb':
        from .dynamodb_labs import LABS
        return LABS
    if service_key == 'kms':
        from .kms_labs import LABS
        return LABS
    if service_key == 'ssm':
        from .ssm_labs import LABS
        return LABS
    if service_key == 'secretsmanager':
        from .secretsmanager_labs import LABS
        return LABS
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import LABS
        return LABS
    if service_key == 'cognito':
        from .cognito_labs import LABS
        return LABS
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import LABS
        return LABS
    if service_key == 'rds':
        from .rds_labs import LABS
        return LABS
    if service_key == 'ecs':
        from .ecs_labs import LABS
        return LABS
    if service_key == 'eks':
        from .eks_labs import LABS
        return LABS
    return []


LAB_BATCH_ORDER = [
    {
        'service': 'iam',
        'title': 'IAM labs',
        'summary': 'Continue into storage after the identity foundation: users, policies, groups, roles, STS, and instance profiles.',
    },
    {
        'service': 's3',
        'title': 'S3 labs',
        'summary': 'Continue into application encryption and keys after storage: buckets, objects, versioning, policies, encryption, lifecycle, notifications, and multipart uploads.',
    },
    {
        'service': 'kms',
        'title': 'KMS labs',
        'summary': 'Continue into application configuration after customer managed keys, aliases, and encrypt/decrypt round trips.',
    },
    {
        'service': 'ssm',
        'title': 'SSM Parameter Store labs',
        'summary': 'Continue into secrets management after hierarchical application configuration in Parameter Store.',
    },
    {
        'service': 'secretsmanager',
        'title': 'Secrets Manager labs',
        'summary': 'Continue into user identity directories after secret lifecycle creation, reads, and value updates.',
    },
    {
        'service': 'cognito',
        'title': 'Cognito authentication labs',
        'summary': 'Continue into asynchronous queueing after user pools, app clients, registration, and JWT token authentication.',
    },
    {
        'service': 'sqs',
        'title': 'SQS labs',
        'summary': 'Continue into pub/sub after queueing: message lifecycle, visibility, delays, batches, DLQs, FIFO, purge, and deletion.',
    },
    {
        'service': 'sns',
        'title': 'SNS labs',
        'summary': 'Continue into scheduled delivery after pub/sub fan-out and message filtering.',
    },
    {
        'service': 'scheduler',
        'title': 'EventBridge Scheduler lab',
        'summary': 'Continue into database persistence after scheduled SQS delivery through a scoped IAM role.',
    },
    {
        'service': 'dynamodb',
        'title': 'DynamoDB labs',
        'summary': 'Continue into serverless functions after table CRUD, primary/hash keys, query patterns, and Lambda write foundations.',
    },
    {
        'service': 'lambda',
        'title': 'Lambda labs',
        'summary': 'Continue into API Gateway after Lambda creation, runtime configuration reads, SQS event sources, invocation, and CloudWatch Logs inspection.',
    },
    {
        'service': 'apigateway',
        'title': 'API Gateway labs',
        'summary': 'Continue into event-driven architecture after exposing Lambda through a local HTTP API.',
    },
    {
        'service': 'eventbridge',
        'title': 'EventBridge application lab',
        'summary': 'Continue into state machine workflows after building a complete API-to-event fan-out workflow with transformed queues, Lambda notification logs, and failure experiments.',
    },
    {
        'service': 'stepfunctions',
        'title': 'Step Functions workflow labs',
        'summary': 'Continue into observability and incident response after ASL state machines, Choice branching, and Parallel task execution.',
    },
    {
        'service': 'cloudwatch',
        'title': 'CloudWatch observability labs',
        'summary': 'Continue into VPC networking after custom metric data, threshold alarms, Log Groups, and structured event streams.',
    },
    {
        'service': 'ec2',
        'title': 'EC2 networking labs',
        'summary': 'Continue into declarative infrastructure as code after VPC routing, security controls, gateway endpoints, and interface endpoints.',
    },
    {
        'service': 'rds',
        'title': 'RDS & Aurora labs',
        'summary': 'Master relational database provisioning, custom parameter groups, endpoint configuration, storage scaling, and reboot recovery.',
    },
    {
        'service': 'ecs',
        'title': 'ECS & Fargate container labs',
        'summary': 'Deploy containerized microservices: cluster creation, task definitions, standalone Fargate tasks, and autoscaling services.',
    },
    {
        'service': 'eks',
        'title': 'EKS Kubernetes labs',
        'summary': 'Deploy managed Kubernetes control planes, EC2 worker node groups, serverless Fargate pod profiles, and local kubeconfig setup.',
    },
    {
        'service': 'cloudformation',
        'title': 'CloudFormation lab',
        'summary': 'Complete the core AWS learning path with infrastructure as code: template validation, provisioning S3 + SQS, outputs, events, and dependency-aware teardown.',
    },
]


NEXT_BUILD_RECOMMENDATIONS = [
    'KMS key, alias, encrypt, and decrypt workflow',
    'SSM Parameter Store app configuration',
    'Secrets Manager secret lifecycle',
    'Cognito User Pool user registration and authentication',
    'SQS dead-letter queue redrive and FIFO deduplication',
    'DynamoDB table CRUD and query patterns',
    'Lambda create, invoke, and inspect CloudWatch logs',
    'Lambda reads configuration and secrets at runtime',
    'API Gateway to Lambda request workflow',
    'EventBridge application spine distributed fan-out',
    'Step Functions order workflow with Choice branching',
    'CloudWatch custom metrics and alarms',
]


def next_lab_batch(service_key: str, lab_key: str) -> dict[str, Any] | None:
    labs = labs_for_service(service_key)
    if not labs or labs[-1]['key'] != lab_key:
        return None

    batch_services = [batch['service'] for batch in LAB_BATCH_ORDER]
    if service_key not in batch_services:
        return None

    batch_index = batch_services.index(service_key)
    if batch_index + 1 >= len(LAB_BATCH_ORDER):
        return {
            'title': 'All learning batches complete',
            'summary': 'You have completed all 17 AWS service lab batches in Floci.',
            'service': None,
            'lab': None,
            'lab_title': None,
        }

    next_batch = LAB_BATCH_ORDER[batch_index + 1]
    next_labs = labs_for_service(next_batch['service'])
    if not next_labs:
        return None
    first_lab = next_labs[0]
    return {
        **next_batch,
        'lab': first_lab['key'],
        'lab_title': first_lab['title'],
    }


def get_lab(service_key: str, lab_key: str) -> dict[str, Any] | None:
    for lab in labs_for_service(service_key):
        if lab['key'] == lab_key:
            return lab
    return None


def _iam_client():
    return FlociClientFactory().client('iam')


def _sts_client():
    return FlociClientFactory().client('sts')


def _s3_client():
    return FlociClientFactory().client('s3')


def _sqs_client():
    return FlociClientFactory().client('sqs')


def _sns_client():
    return FlociClientFactory().client('sns')


def _scheduler_client():
    return FlociClientFactory().client('scheduler')


def _cloudformation_client():
    return FlociClientFactory().client('cloudformation')


def _ec2_client():
    return FlociClientFactory().client('ec2')


def _lambda_client():
    return FlociClientFactory().client('lambda')


def _logs_client():
    return FlociClientFactory().client('logs')


def _apigatewayv2_client():
    return FlociClientFactory().client('apigatewayv2')


def _dynamodb_client():
    return FlociClientFactory().client('dynamodb')


def _kms_client():
    return FlociClientFactory().client('kms')


def _ssm_client():
    return FlociClientFactory().client('ssm')


def _secretsmanager_client():
    return FlociClientFactory().client('secretsmanager')


def _floci_endpoint_url() -> str:
    return FlociClientFactory().endpoint_url


def _json_text(value: Any) -> str:
    return json.dumps(_clean_response(value), indent=2, default=str)


def _error_code(exc: ClientError) -> str | None:
    return exc.response.get('Error', {}).get('Code')


def _ignore_missing(action) -> bool:
    try:
        action()
        return True
    except ClientError as exc:
        if _error_code(exc) == 'NoSuchEntity':
            return False
        raise


def _verify_iam_user(user_name: str) -> dict[str, Any]:
    try:
        user = _iam_client().get_user(UserName=user_name).get('User', {})
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    if user.get('UserName') == user_name:
        return {
            'status': 'passed',
            'message': f'User {user_name} exists in local IAM.',
            'resource': _clean_response(user),
        }

    return {
        'status': 'failed',
        'message': f'User {user_name} was not returned by IAM.',
    }


def _verify_alice_policy() -> dict[str, Any]:
    try:
        policy = _iam_client().get_policy(PolicyArn=ALICE_POLICY_ARN).get('Policy', {})
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    if policy.get('Arn') == ALICE_POLICY_ARN:
        return {
            'status': 'passed',
            'message': f'Policy {ALICE_POLICY_NAME} exists in local IAM.',
            'resource': _clean_response(policy),
        }

    return {
        'status': 'failed',
        'message': f'Policy {ALICE_POLICY_NAME} was not returned by IAM.',
    }


def _verify_alice_policy_attachment() -> dict[str, Any]:
    try:
        policies = _iam_client().list_attached_user_policies(UserName=ALICE_USER_NAME).get('AttachedPolicies', [])
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    for policy in policies:
        if policy.get('PolicyArn') == ALICE_POLICY_ARN:
            return {
                'status': 'passed',
                'message': f'Policy {ALICE_POLICY_NAME} is attached to Alice.',
                'resource': _clean_response(policy),
            }

    return {
        'status': 'failed',
        'message': f'Policy {ALICE_POLICY_NAME} is not attached to Alice.',
    }


def _alice_access_keys() -> list[dict[str, Any]]:
    return _iam_client().list_access_keys(UserName=ALICE_USER_NAME).get('AccessKeyMetadata', [])


def _verify_alice_access_key() -> dict[str, Any]:
    try:
        keys = _alice_access_keys()
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    if keys:
        return {
            'status': 'passed',
            'message': 'Alice has at least one access key in local IAM.',
            'resource': _clean_response(keys[0]),
        }

    return {
        'status': 'failed',
        'message': 'Alice does not have an access key.',
    }


def _verify_floci_developers_group() -> dict[str, Any]:
    try:
        group = _iam_client().get_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME).get('Group', {})
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    if group.get('GroupName') == FLOCI_DEVELOPERS_GROUP_NAME:
        return {
            'status': 'passed',
            'message': f'Group {FLOCI_DEVELOPERS_GROUP_NAME} exists in local IAM.',
            'resource': _clean_response(group),
        }

    return {
        'status': 'failed',
        'message': f'Group {FLOCI_DEVELOPERS_GROUP_NAME} was not returned by IAM.',
    }


def _verify_alice_group_membership() -> dict[str, Any]:
    try:
        users = _iam_client().get_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME).get('Users', [])
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    for user in users:
        if user.get('UserName') == ALICE_USER_NAME:
            return {
                'status': 'passed',
                'message': f'Alice is a member of {FLOCI_DEVELOPERS_GROUP_NAME}.',
                'resource': _clean_response(user),
            }

    return {
        'status': 'failed',
        'message': f'Alice is not a member of {FLOCI_DEVELOPERS_GROUP_NAME}.',
    }


def _verify_floci_developers_policy() -> dict[str, Any]:
    try:
        policy = _iam_client().get_policy(PolicyArn=FLOCI_DEVELOPERS_POLICY_ARN).get('Policy', {})
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    if policy.get('Arn') == FLOCI_DEVELOPERS_POLICY_ARN:
        return {
            'status': 'passed',
            'message': f'Policy {FLOCI_DEVELOPERS_POLICY_NAME} exists in local IAM.',
            'resource': _clean_response(policy),
        }

    return {
        'status': 'failed',
        'message': f'Policy {FLOCI_DEVELOPERS_POLICY_NAME} was not returned by IAM.',
    }


def _verify_floci_developers_policy_attachment() -> dict[str, Any]:
    try:
        policies = _iam_client().list_attached_group_policies(
            GroupName=FLOCI_DEVELOPERS_GROUP_NAME,
        ).get('AttachedPolicies', [])
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    for policy in policies:
        if policy.get('PolicyArn') == FLOCI_DEVELOPERS_POLICY_ARN:
            return {
                'status': 'passed',
                'message': f'Policy {FLOCI_DEVELOPERS_POLICY_NAME} is attached to {FLOCI_DEVELOPERS_GROUP_NAME}.',
                'resource': _clean_response(policy),
            }

    return {
        'status': 'failed',
        'message': f'Policy {FLOCI_DEVELOPERS_POLICY_NAME} is not attached to {FLOCI_DEVELOPERS_GROUP_NAME}.',
    }


def _verify_alice_inline_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_user_policy(
            UserName=ALICE_USER_NAME,
            PolicyName=ALICE_INLINE_POLICY_NAME,
        )
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    if response.get('PolicyName') == ALICE_INLINE_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {ALICE_INLINE_POLICY_NAME} is embedded in Alice.',
            'resource': _clean_response(response),
        }

    return {
        'status': 'failed',
        'message': f'Inline policy {ALICE_INLINE_POLICY_NAME} was not returned for Alice.',
    }


def _verify_role(role_name: str, trusted_service: str) -> dict[str, Any]:
    try:
        role = _iam_client().get_role(RoleName=role_name).get('Role', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    trust_policy = role.get('AssumeRolePolicyDocument') or {}
    statements = trust_policy.get('Statement', [])
    trusted = any(
        statement.get('Effect') == 'Allow'
        and statement.get('Action') == 'sts:AssumeRole'
        and (
            statement.get('Principal', {}).get('Service') == trusted_service
            or statement.get('Principal', {}).get('AWS') == trusted_service
        )
        for statement in statements
    )
    if role.get('RoleName') == role_name and trusted:
        return {
            'status': 'passed',
            'message': f'Role {role_name} exists and trusts {trusted_service}.',
            'resource': _clean_response(role),
        }

    return {
        'status': 'failed',
        'message': f'Role {role_name} does not have the expected {trusted_service} trust policy.',
    }


def _verify_application_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=FLOCI_APPLICATION_ROLE_NAME,
            PolicyName=FLOCI_APPLICATION_ROLE_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if response.get('PolicyName') == FLOCI_APPLICATION_ROLE_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {FLOCI_APPLICATION_ROLE_POLICY_NAME} is embedded in {FLOCI_APPLICATION_ROLE_NAME}.',
            'resource': _clean_response(response),
        }

    return {
        'status': 'failed',
        'message': f'Inline policy {FLOCI_APPLICATION_ROLE_POLICY_NAME} was not returned.',
    }


def _verify_sts_session_role() -> dict[str, Any]:
    try:
        role = _iam_client().get_role(RoleName=FLOCI_STS_SESSION_ROLE_NAME).get('Role', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    trust_policy = role.get('AssumeRolePolicyDocument') or {}
    statements = trust_policy.get('Statement', [])
    trusted = any(
        statement.get('Effect') == 'Allow'
        and statement.get('Action') == 'sts:AssumeRole'
        and statement.get('Principal', {}).get('AWS') == f'arn:aws:iam::{AWS_ACCOUNT_ID}:root'
        for statement in statements
    )
    if role.get('RoleName') == FLOCI_STS_SESSION_ROLE_NAME and trusted:
        return {
            'status': 'passed',
            'message': f'Role {FLOCI_STS_SESSION_ROLE_NAME} trusts the local account root principal.',
            'resource': _clean_response(role),
        }

    return {
        'status': 'failed',
        'message': f'Role {FLOCI_STS_SESSION_ROLE_NAME} does not have the expected account-root trust policy.',
    }


def _verify_sts_session_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=FLOCI_STS_SESSION_ROLE_NAME,
            PolicyName=FLOCI_STS_ROLE_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if response.get('PolicyName') == FLOCI_STS_ROLE_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {FLOCI_STS_ROLE_POLICY_NAME} is embedded in {FLOCI_STS_SESSION_ROLE_NAME}.',
            'resource': _clean_response(response),
        }

    return {
        'status': 'failed',
        'message': f'Inline policy {FLOCI_STS_ROLE_POLICY_NAME} was not returned.',
    }


def _verify_sts_assumed_role_session() -> dict[str, Any]:
    cached = cache.get(FLOCI_STS_ASSUME_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': 'STS returned temporary credentials for the role and inline session policy.',
            'resource': cached,
        }

    return {
        'status': 'failed',
        'message': 'No successful STS AssumeRole session has been recorded for this lab run.',
    }


def _verify_cached_marker(cache_key: str, message: str) -> dict[str, Any]:
    cached = cache.get(cache_key)
    if cached:
        return {
            'status': 'passed',
            'message': message,
            'resource': cached if isinstance(cached, dict) else {'value': cached},
        }
    return {'status': 'failed', 'message': message}


def _verify_iam_enforcement_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=IAM_ENFORCEMENT_ROLE_NAME,
            PolicyName=IAM_ENFORCEMENT_ROLE_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if response.get('PolicyName') == IAM_ENFORCEMENT_ROLE_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {IAM_ENFORCEMENT_ROLE_POLICY_NAME} is embedded in {IAM_ENFORCEMENT_ROLE_NAME}.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Inline policy {IAM_ENFORCEMENT_ROLE_POLICY_NAME} was not returned.',
    }


def _verify_ec2_instance_profile() -> dict[str, Any]:
    try:
        profile = _iam_client().get_instance_profile(
            InstanceProfileName=FLOCI_EC2_INSTANCE_PROFILE_NAME,
        ).get('InstanceProfile', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if profile.get('InstanceProfileName') == FLOCI_EC2_INSTANCE_PROFILE_NAME:
        return {
            'status': 'passed',
            'message': f'Instance profile {FLOCI_EC2_INSTANCE_PROFILE_NAME} exists.',
            'resource': _clean_response(profile),
        }

    return {
        'status': 'failed',
        'message': f'Instance profile {FLOCI_EC2_INSTANCE_PROFILE_NAME} was not returned.',
    }


def _verify_ec2_role_profile_association() -> dict[str, Any]:
    profile_verification = _verify_ec2_instance_profile()
    if profile_verification.get('status') != 'passed':
        return profile_verification

    profile = profile_verification.get('resource', {})
    for role in profile.get('Roles', []):
        if role.get('RoleName') == FLOCI_EC2_ROLE_NAME:
            return {
                'status': 'passed',
                'message': f'Role {FLOCI_EC2_ROLE_NAME} is in {FLOCI_EC2_INSTANCE_PROFILE_NAME}.',
                'resource': profile,
            }

    return {
        'status': 'failed',
        'message': f'Role {FLOCI_EC2_ROLE_NAME} is not in {FLOCI_EC2_INSTANCE_PROFILE_NAME}.',
    }


LAMBDA_LOG_WAIT_SECONDS = 20


def _wait_for_verification(verify, timeout_seconds: float = LAMBDA_LOG_WAIT_SECONDS) -> dict[str, Any]:
    """Re-run a verification until it passes or time runs out.

    Asynchronous Lambda invocations write logs a moment later (longer on a
    cold start), so log checks that run right after the trigger need to wait.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        verification = verify()
        if verification.get('status') == 'passed' or time.monotonic() >= deadline:
            return verification
        time.sleep(0.5)


def _describe_log_streams_if_present(log_group_name: str) -> dict[str, Any]:
    try:
        return _logs_client().describe_log_streams(
            logGroupName=log_group_name,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        )
    except ClientError as exc:
        if _error_code(exc) == 'ResourceNotFoundException':
            return {'logStreams': []}
        raise


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _secret_string_json(response: dict[str, Any]) -> Any:
    return _parse_json_text(response.get('SecretString') or '')


def _sqs_queue_url(queue_name: str) -> str:
    return _sqs_client().get_queue_url(QueueName=queue_name)['QueueUrl']


def _cloudformation_stack() -> dict[str, Any]:
    return _cloudformation_client().describe_stacks(
        StackName=CLOUDFORMATION_STACK_NAME,
    )['Stacks'][0]


def _wait_for_cloudformation_status(
    terminal_statuses: set[str],
    timeout_seconds: float = 10,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        stack = _cloudformation_stack()
        if stack.get('StackStatus') in terminal_statuses:
            return stack
        time.sleep(0.1)
    raise ValueError(
        f'Stack {CLOUDFORMATION_STACK_NAME} did not reach '
        f'{", ".join(sorted(terminal_statuses))}.'
    )


def _verify_cloudformation_stack() -> dict[str, Any]:
    try:
        stack = _cloudformation_stack()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    outputs = {
        item.get('OutputKey'): item.get('OutputValue')
        for item in stack.get('Outputs', [])
    }
    queue_url = outputs.get('QueueUrl', '')
    if (
        stack.get('StackStatus') == 'CREATE_COMPLETE'
        and outputs.get('BucketName') == CLOUDFORMATION_BUCKET_NAME
        and queue_url.rstrip('/').endswith(f'/{CLOUDFORMATION_QUEUE_NAME}')
        and outputs.get('QueueArn') == CLOUDFORMATION_QUEUE_ARN
    ):
        return {
            'status': 'passed',
            'message': 'The stack is CREATE_COMPLETE with all expected outputs.',
            'resource': {
                'stack': _clean_response(stack),
                'outputs': outputs,
            },
        }
    return {'status': 'failed', 'message': 'The stack outputs are incomplete.'}


def _verify_cloudformation_resources() -> dict[str, Any]:
    try:
        response = _cloudformation_client().describe_stack_resources(
            StackName=CLOUDFORMATION_STACK_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    resources = {
        item.get('LogicalResourceId'): item
        for item in response.get('StackResources', [])
    }
    bucket = resources.get('StorageBucket', {})
    queue = resources.get('JobsQueue', {})
    if (
        bucket.get('PhysicalResourceId') == CLOUDFORMATION_BUCKET_NAME
        and bucket.get('ResourceType') == 'AWS::S3::Bucket'
        and bucket.get('ResourceStatus') == 'CREATE_COMPLETE'
        and queue.get('PhysicalResourceId', '').rstrip('/').endswith(
            f'/{CLOUDFORMATION_QUEUE_NAME}'
        )
        and queue.get('ResourceType') == 'AWS::SQS::Queue'
        and queue.get('ResourceStatus') == 'CREATE_COMPLETE'
    ):
        return {
            'status': 'passed',
            'message': 'Both logical resources map to CREATE_COMPLETE physical resources.',
            'resource': {'resources': _clean_response(list(resources.values()))},
        }
    return {'status': 'failed', 'message': 'The stack resource mapping is incomplete.'}


def _verify_cloudformation_events() -> dict[str, Any]:
    try:
        response = _cloudformation_client().describe_stack_events(
            StackName=CLOUDFORMATION_STACK_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    events = response.get('StackEvents', [])
    completed = {
        (item.get('LogicalResourceId'), item.get('ResourceStatus'))
        for item in events
    }
    required = {
        (CLOUDFORMATION_STACK_NAME, 'CREATE_COMPLETE'),
        ('StorageBucket', 'CREATE_COMPLETE'),
        ('JobsQueue', 'CREATE_COMPLETE'),
    }
    if required.issubset(completed):
        return {
            'status': 'passed',
            'message': 'The event timeline records successful creation of the stack and both resources.',
            'resource': {'events': _clean_response(events)},
        }
    return {'status': 'failed', 'message': 'The expected creation events are missing.'}


def _verify_cloudformation_service_resources() -> dict[str, Any]:
    try:
        _s3_client().head_bucket(Bucket=CLOUDFORMATION_BUCKET_NAME)
        queue_url = _sqs_queue_url(CLOUDFORMATION_QUEUE_NAME)
        queue_attributes = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn', 'VisibilityTimeout'],
        ).get('Attributes', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        queue_attributes.get('QueueArn') == CLOUDFORMATION_QUEUE_ARN
        and queue_attributes.get('VisibilityTimeout') == '30'
    ):
        return {
            'status': 'passed',
            'message': 'S3 and SQS expose the stack-managed resources and expected configuration.',
            'resource': {
                'bucket': CLOUDFORMATION_BUCKET_NAME,
                'queue_url': queue_url,
                'queue_attributes': queue_attributes,
            },
        }
    return {'status': 'failed', 'message': 'The provisioned resource configuration is incorrect.'}


def _verify_cloudformation_deleted() -> dict[str, Any]:
    if not cache.get(CLOUDFORMATION_DELETED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'Stack deletion has not been verified by this lab run.',
        }
    stack_absent = False
    bucket_absent = False
    queue_absent = False
    try:
        _cloudformation_stack()
    except ClientError:
        stack_absent = True
    try:
        _s3_client().head_bucket(Bucket=CLOUDFORMATION_BUCKET_NAME)
    except ClientError:
        bucket_absent = True
    try:
        _sqs_queue_url(CLOUDFORMATION_QUEUE_NAME)
    except ClientError as exc:
        queue_absent = _error_code(exc) in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }
    if stack_absent and bucket_absent and queue_absent:
        return {
            'status': 'passed',
            'message': 'The stack, bucket, and queue are all absent.',
        }
    return {'status': 'failed', 'message': 'One or more stack-owned resources still exist.'}


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


def run_lab_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import run_step
        return run_step(step_key)
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'cognito':
        from .cognito_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'rds':
        from .rds_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'ecs':
        from .ecs_labs import run_step
        return run_step(service_key, lab_key, step_key)
    if service_key == 'eks':
        from .eks_labs import run_step
        return run_step(service_key, lab_key, step_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2' and lab_key.startswith('guided-'):
        from .ec2_guided import run_guided_step
        return run_guided_step(lab_key, step_key)

    if service_key == 'iam' and lab_key == 'create-user-alice' and step_key == 'create-user':
        return _run_iam_create_user_alice()

    if service_key == 'iam' and lab_key == 'create-admin-user':
        runners = {
            'create-user': _run_iam_create_admin_user,
            'put-admin-policy': _run_iam_put_admin_policy,
            'create-access-key': _run_iam_create_admin_access_key,
            'verify-admin-identity': _run_iam_verify_admin_identity,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'attach-policy-alice':
        runners = {
            'create-user': lambda: _run_iam_create_user_alice('attach-policy-alice'),
            'create-policy': _run_iam_create_alice_policy,
            'attach-policy': _run_iam_attach_alice_policy,
            'list-attached-policies': _run_iam_list_alice_attached_policies,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'access-key-alice':
        runners = {
            'create-user': lambda: _run_iam_create_user_alice('access-key-alice'),
            'create-access-key': _run_iam_create_alice_access_key,
            'list-access-keys': _run_iam_list_alice_access_keys,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'group-membership-alice':
        runners = {
            'create-user': lambda: _run_iam_create_user_alice('group-membership-alice'),
            'create-group': _run_iam_create_floci_developers_group,
            'add-user-to-group': _run_iam_add_alice_to_floci_developers_group,
            'get-group': _run_iam_get_floci_developers_group,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'group-policy-floci-developers':
        runners = {
            'create-user': lambda: _run_iam_create_user_alice('group-policy-floci-developers'),
            'create-group': lambda: _run_iam_create_floci_developers_group('group-policy-floci-developers'),
            'add-user-to-group': lambda: _run_iam_add_alice_to_floci_developers_group('group-policy-floci-developers'),
            'create-policy': _run_iam_create_floci_developers_policy,
            'attach-group-policy': _run_iam_attach_floci_developers_policy,
            'list-attached-group-policies': _run_iam_list_floci_developers_attached_policies,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'inline-policy-alice':
        runners = {
            'create-user': lambda: _run_iam_create_user_alice('inline-policy-alice'),
            'put-user-policy': _run_iam_put_alice_inline_policy,
            'list-user-policies': _run_iam_list_alice_inline_policies,
            'get-user-policy': _run_iam_get_alice_inline_policy,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'role-trust-policy':
        runners = {
            'create-role': _run_iam_create_application_role,
            'get-role': _run_iam_get_application_role,
            'put-role-policy': _run_iam_put_application_role_policy,
            'get-role-policy': _run_iam_get_application_role_policy,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'sts-session-policy':
        runners = {
            'create-role': _run_iam_create_sts_session_role,
            'put-role-policy': _run_iam_put_sts_session_role_policy,
            'assume-role': _run_sts_assume_session_policy_role,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'ec2-instance-profile':
        runners = {
            'create-role': _run_iam_create_ec2_role,
            'create-instance-profile': _run_iam_create_ec2_instance_profile,
            'add-role-to-instance-profile': _run_iam_add_ec2_role_to_instance_profile,
            'get-instance-profile': _run_iam_get_ec2_instance_profile,
            'list-instance-profiles-for-role': _run_iam_list_instance_profiles_for_ec2_role,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'iam' and lab_key == 'identity-enforcement-capstone':
        runners = {
            'create-user': _run_iam_enforcement_create_user,
            'verify-user-denied': _run_iam_enforcement_verify_user_s3_denied,
            'create-role': _run_iam_enforcement_create_role,
            'put-role-policy': _run_iam_enforcement_put_role_policy,
            'assume-role': _run_iam_enforcement_assume_role,
            'verify-role-sqs': _run_iam_enforcement_verify_role_sqs_allowed,
            'verify-role-s3-denied': _run_iam_enforcement_verify_role_s3_denied,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'lambda':
        from .lambda_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'apigateway':
        from .apigateway_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'kms':
        from .kms_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'ssm':
        from .ssm_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 's3':
        from .s3_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'sqs':
        from .sqs_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'sns':
        from .sns_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'scheduler':
        from .scheduler_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        runners = {
            'validate-template': _run_cloudformation_validate_template,
            'create-stack': _run_cloudformation_create_stack,
            'describe-stack': _run_cloudformation_describe_stack,
            'describe-stack-resources': _run_cloudformation_describe_resources,
            'describe-stack-events': _run_cloudformation_describe_events,
            'inspect-provisioned-resources': _run_cloudformation_inspect_services,
            'delete-stack': _run_cloudformation_delete_stack,
            'confirm-resources-deleted': _run_cloudformation_confirm_deleted,
        }
        if step_key in runners:
            return runners[step_key]()

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


def lab_status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import status
        return status()
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import status
        return status(service_key, lab_key)
    if service_key == 'cognito':
        from .cognito_labs import status
        return status(service_key, lab_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import status
        return status(service_key, lab_key)
    if service_key == 'rds':
        from .rds_labs import status
        return status(service_key, lab_key)
    if service_key == 'ecs':
        from .ecs_labs import status
        return status(service_key, lab_key)
    if service_key == 'eks':
        from .eks_labs import status
        return status(service_key, lab_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2' and lab_key.startswith('guided-'):
        from .ec2_guided import guided_status
        return guided_status(lab_key)

    if service_key == 'iam' and lab_key == 'create-user-alice':
        verification = _verify_iam_user('Alice')
        verified = verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': verified,
            'steps': {
                'create-user': {
                    'verified': verified,
                    'verification': verification if verified else None,
                },
            },
        }

    if service_key == 'iam' and lab_key == 'create-admin-user':
        user_verification = _verify_iam_user(FLOCI_ADMIN_USER_NAME)
        policy_verification = _verify_iam_admin_policy()
        key_verification = _verify_iam_admin_access_key()
        identity_verification = _verify_iam_admin_identity()
        user_verified = user_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        key_verified = key_verification.get('status') == 'passed'
        identity_verified = identity_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': user_verified and policy_verified and key_verified and identity_verified,
            'steps': {
                'create-user': {
                    'verified': user_verified,
                    'verification': user_verification if user_verified else None,
                },
                'put-admin-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'create-access-key': {
                    'verified': key_verified,
                    'verification': key_verification if key_verified else None,
                },
                'verify-admin-identity': {
                    'verified': identity_verified,
                    'verification': identity_verification if identity_verified else None,
                },
            },
        }

    if service_key == 'iam' and lab_key == 'attach-policy-alice':
        user_verification = _verify_iam_user(ALICE_USER_NAME)
        policy_verification = _verify_alice_policy()
        attachment_verification = _verify_alice_policy_attachment()
        user_verified = user_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        attachment_verified = attachment_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': user_verified and policy_verified and attachment_verified,
            'steps': {
                'create-user': {
                    'verified': user_verified,
                    'verification': user_verification if user_verified else None,
                },
                'create-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'attach-policy': {
                    'verified': attachment_verified,
                    'verification': attachment_verification if attachment_verified else None,
                },
                'list-attached-policies': {
                    'verified': attachment_verified,
                    'verification': attachment_verification if attachment_verified else None,
                },
            },
        }

    if service_key == 'iam' and lab_key == 'access-key-alice':
        user_verification = _verify_iam_user(ALICE_USER_NAME)
        key_verification = _verify_alice_access_key()
        user_verified = user_verification.get('status') == 'passed'
        key_verified = key_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': user_verified and key_verified,
            'steps': {
                'create-user': {
                    'verified': user_verified,
                    'verification': user_verification if user_verified else None,
                },
                'create-access-key': {
                    'verified': key_verified,
                    'verification': key_verification if key_verified else None,
                },
                'list-access-keys': {
                    'verified': key_verified,
                    'verification': key_verification if key_verified else None,
                },
            },
        }

    if service_key == 'iam' and lab_key == 'group-membership-alice':
        user_verification = _verify_iam_user(ALICE_USER_NAME)
        group_verification = _verify_floci_developers_group()
        membership_verification = _verify_alice_group_membership()
        user_verified = user_verification.get('status') == 'passed'
        group_verified = group_verification.get('status') == 'passed'
        membership_verified = membership_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': user_verified and group_verified and membership_verified,
            'steps': {
                'create-user': {
                    'verified': user_verified,
                    'verification': user_verification if user_verified else None,
                },
                'create-group': {
                    'verified': group_verified,
                    'verification': group_verification if group_verified else None,
                },
                'add-user-to-group': {
                    'verified': membership_verified,
                    'verification': membership_verification if membership_verified else None,
                },
                'get-group': {
                    'verified': membership_verified,
                    'verification': membership_verification if membership_verified else None,
                },
            },
        }

    if service_key == 'iam' and lab_key == 'group-policy-floci-developers':
        user_verification = _verify_iam_user(ALICE_USER_NAME)
        group_verification = _verify_floci_developers_group()
        membership_verification = _verify_alice_group_membership()
        policy_verification = _verify_floci_developers_policy()
        attachment_verification = _verify_floci_developers_policy_attachment()
        user_verified = user_verification.get('status') == 'passed'
        group_verified = group_verification.get('status') == 'passed'
        membership_verified = membership_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        attachment_verified = attachment_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': user_verified and group_verified and membership_verified and policy_verified and attachment_verified,
            'steps': {
                'create-user': {
                    'verified': user_verified,
                    'verification': user_verification if user_verified else None,
                },
                'create-group': {
                    'verified': group_verified,
                    'verification': group_verification if group_verified else None,
                },
                'add-user-to-group': {
                    'verified': membership_verified,
                    'verification': membership_verification if membership_verified else None,
                },
                'create-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'attach-group-policy': {
                    'verified': attachment_verified,
                    'verification': attachment_verification if attachment_verified else None,
                },
                'list-attached-group-policies': {
                    'verified': attachment_verified,
                    'verification': attachment_verification if attachment_verified else None,
                },
            },
        }

    if service_key == 'iam' and lab_key == 'inline-policy-alice':
        user_verification = _verify_iam_user(ALICE_USER_NAME)
        policy_verification = _verify_alice_inline_policy()
        user_verified = user_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': user_verified and policy_verified,
            'steps': {
                'create-user': {
                    'verified': user_verified,
                    'verification': user_verification if user_verified else None,
                },
                'put-user-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'list-user-policies': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'get-user-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
            },
        }

    if service_key == 'iam' and lab_key == 'role-trust-policy':
        role_verification = _verify_role(FLOCI_APPLICATION_ROLE_NAME, 'lambda.amazonaws.com')
        policy_verification = _verify_application_role_policy()
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': role_verified and policy_verified,
            'steps': {
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'get-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'get-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
            },
        }

    if service_key == 'iam' and lab_key == 'sts-session-policy':
        role_verification = _verify_sts_session_role()
        policy_verification = _verify_sts_session_role_policy()
        session_verification = _verify_sts_assumed_role_session()
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        session_verified = session_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': role_verified and policy_verified and session_verified,
            'steps': {
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'assume-role': {'verified': session_verified, 'verification': session_verification if session_verified else None},
            },
        }

    if service_key == 'iam' and lab_key == 'ec2-instance-profile':
        role_verification = _verify_role(FLOCI_EC2_ROLE_NAME, 'ec2.amazonaws.com')
        profile_verification = _verify_ec2_instance_profile()
        association_verification = _verify_ec2_role_profile_association()
        role_verified = role_verification.get('status') == 'passed'
        profile_verified = profile_verification.get('status') == 'passed'
        association_verified = association_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': role_verified and profile_verified and association_verified,
            'steps': {
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'create-instance-profile': {'verified': profile_verified, 'verification': profile_verification if profile_verified else None},
                'add-role-to-instance-profile': {'verified': association_verified, 'verification': association_verification if association_verified else None},
                'get-instance-profile': {'verified': association_verified, 'verification': association_verification if association_verified else None},
                'list-instance-profiles-for-role': {'verified': association_verified, 'verification': association_verification if association_verified else None},
            },
        }

    if service_key == 'iam' and lab_key == 'identity-enforcement-capstone':
        user_verification = _verify_iam_user(IAM_ENFORCEMENT_USER_NAME)
        user_denied = _verify_cached_marker(IAM_ENFORCEMENT_USER_DENY_CACHE_KEY, 'Charlie was denied S3 ListBuckets.')
        role_verification = _verify_role(IAM_ENFORCEMENT_ROLE_NAME, IAM_ENFORCEMENT_USER_ARN)
        policy_verification = _verify_iam_enforcement_role_policy()
        assumed = _verify_cached_marker(IAM_ENFORCEMENT_ROLE_SQS_CACHE_KEY, 'The assumed role listed SQS queues.')
        role_s3_denied = _verify_cached_marker(IAM_ENFORCEMENT_ROLE_S3_DENY_CACHE_KEY, 'The assumed role was denied S3 ListBuckets.')
        user_verified = user_verification.get('status') == 'passed'
        user_denied_verified = user_denied.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        assumed_verified = assumed.get('status') == 'passed'
        role_s3_denied_verified = role_s3_denied.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': user_verified and user_denied_verified and role_verified and policy_verified and assumed_verified and role_s3_denied_verified,
            'steps': {
                'create-user': {'verified': user_verified, 'verification': user_verification if user_verified else None},
                'verify-user-denied': {'verified': user_denied_verified, 'verification': user_denied if user_denied_verified else None},
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'assume-role': {'verified': assumed_verified, 'verification': assumed if assumed_verified else None},
                'verify-role-sqs': {'verified': assumed_verified, 'verification': assumed if assumed_verified else None},
                'verify-role-s3-denied': {'verified': role_s3_denied_verified, 'verification': role_s3_denied if role_s3_denied_verified else None},
            },
        }

    if service_key == 'lambda':
        from .lambda_labs import status
        return status(service_key, lab_key)

    if service_key == 'apigateway':
        from .apigateway_labs import status
        return status(service_key, lab_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import status
        return status(service_key, lab_key)

    if service_key == 'kms':
        from .kms_labs import status
        return status(service_key, lab_key)

    if service_key == 'ssm':
        from .ssm_labs import status
        return status(service_key, lab_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import status
        return status(service_key, lab_key)

    if service_key == 's3':
        from .s3_labs import status
        return status(service_key, lab_key)

    if service_key == 'sqs':
        from .sqs_labs import status
        return status(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import status
        return status(service_key, lab_key)

    if service_key == 'scheduler':
        from .scheduler_labs import status
        return status(service_key, lab_key)

    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        deleted_verification = _verify_cloudformation_deleted()
        deleted_verified = deleted_verification.get('status') == 'passed'
        created_verified = bool(cache.get(CLOUDFORMATION_CREATED_CACHE_KEY))
        inspected_verified = bool(cache.get(CLOUDFORMATION_INSPECTED_CACHE_KEY))
        created_verification = {
            'status': 'passed',
            'message': 'The stack reached CREATE_COMPLETE with verified outputs.',
        }
        inspected_verification = {
            'status': 'passed',
            'message': 'Stack resources, events, and underlying services were inspected.',
        }
        if deleted_verified:
            stack_verified = created_verified
            resources_verified = inspected_verified
            events_verified = inspected_verified
            services_verified = inspected_verified
        else:
            stack_verified = _verify_cloudformation_stack().get('status') == 'passed'
            resources_verified = (
                _verify_cloudformation_resources().get('status') == 'passed'
            )
            events_verified = (
                _verify_cloudformation_events().get('status') == 'passed'
            )
            services_verified = (
                _verify_cloudformation_service_resources().get('status')
                == 'passed'
            )
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                created_verified
                and inspected_verified
                and deleted_verified
            ),
            'steps': {
                'validate-template': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'create-stack': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'describe-stack': {
                    'verified': stack_verified,
                    'verification': (
                        created_verification if stack_verified else None
                    ),
                },
                'describe-stack-resources': {
                    'verified': resources_verified,
                    'verification': (
                        inspected_verification if resources_verified else None
                    ),
                },
                'describe-stack-events': {
                    'verified': events_verified,
                    'verification': (
                        inspected_verification if events_verified else None
                    ),
                },
                'inspect-provisioned-resources': {
                    'verified': services_verified,
                    'verification': (
                        inspected_verification if services_verified else None
                    ),
                },
                'delete-stack': {
                    'verified': deleted_verified,
                    'verification': (
                        deleted_verification if deleted_verified else None
                    ),
                },
                'confirm-resources-deleted': {
                    'verified': deleted_verified,
                    'verification': (
                        deleted_verification if deleted_verified else None
                    ),
                },
            },
        }

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


def reset_lab(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'eventbridge' and lab_key == 'application-spine':
        from .eventbridge_application import reset
        return reset()
    if service_key == 'stepfunctions':
        from .stepfunctions_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'cognito':
        from .cognito_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'cloudwatch':
        from .cloudwatch_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'rds':
        from .rds_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'ecs':
        from .ecs_labs import reset
        return reset(service_key, lab_key)
    if service_key == 'eks':
        from .eks_labs import reset
        return reset(service_key, lab_key)
    lab = get_lab(service_key, lab_key)
    if not lab:
        raise ValueError('Lab not found')

    if service_key == 'ec2' and lab_key.startswith('guided-'):
        from .ec2_guided import reset_guided_lab
        return reset_guided_lab(lab_key)

    if service_key == 'iam' and lab_key == 'create-user-alice':
        return _reset_iam_create_user_alice()

    if service_key == 'iam' and lab_key == 'create-admin-user':
        return _reset_iam_create_admin_user()

    if service_key == 'iam' and lab_key == 'attach-policy-alice':
        return _reset_iam_attach_policy_alice()

    if service_key == 'iam' and lab_key == 'access-key-alice':
        return _reset_iam_access_key_alice()

    if service_key == 'iam' and lab_key == 'group-membership-alice':
        return _reset_iam_group_membership_alice()

    if service_key == 'iam' and lab_key == 'group-policy-floci-developers':
        return _reset_iam_group_policy_floci_developers()

    if service_key == 'iam' and lab_key == 'inline-policy-alice':
        return _reset_iam_inline_policy_alice()

    if service_key == 'iam' and lab_key == 'role-trust-policy':
        return _reset_iam_role_trust_policy()

    if service_key == 'iam' and lab_key == 'sts-session-policy':
        return _reset_iam_sts_session_policy()

    if service_key == 'iam' and lab_key == 'ec2-instance-profile':
        return _reset_iam_ec2_instance_profile()

    if service_key == 'iam' and lab_key == 'identity-enforcement-capstone':
        return _reset_iam_enforcement_capstone()

    if service_key == 'lambda':
        from .lambda_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'apigateway':
        from .apigateway_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'dynamodb':
        from .dynamodb_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'kms':
        from .kms_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'ssm':
        from .ssm_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'secretsmanager':
        from .secretsmanager_labs import reset
        return reset(service_key, lab_key)

    if service_key == 's3':
        from .s3_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'sqs':
        from .sqs_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'scheduler':
        from .scheduler_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'cloudformation' and lab_key == 's3-sqs-stack':
        return _reset_cloudformation_s3_sqs()

    if service_key == 'ec2' and lab_key == 'vpc-public-private':
        return _reset_ec2_vpc_networking()

    if service_key == 'ec2' and lab_key == 'security-controls':
        return _reset_ec2_security_controls()

    if service_key == 'ec2' and lab_key == 's3-gateway-endpoint':
        return _reset_ec2_s3_gateway_endpoint()

    if service_key == 'ec2' and lab_key == 'sqs-interface-endpoint':
        return _reset_ec2_sqs_interface_endpoint()

    raise ValueError('Lab reset not found')


def _run_iam_create_user_alice(lab_key: str = 'create-user-alice') -> dict[str, Any]:
    user_name = 'Alice'
    command = 'aws iam create-user --user-name Alice'
    started = time.perf_counter()

    try:
        response = _iam_client().create_user(UserName=user_name)
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_user(UserName=user_name)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_iam_user(user_name)

    return {
        'service': 'iam',
        'lab': lab_key,
        'step': 'create-user',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _reset_iam_create_user_alice() -> dict[str, Any]:
    user_name = 'Alice'
    command = 'aws iam delete-user --user-name Alice'
    started = time.perf_counter()
    deleted = False
    iam = _iam_client()

    try:
        for pol in iam.list_attached_user_policies(UserName=user_name).get('AttachedPolicies', []):
            _ignore_missing(lambda: iam.detach_user_policy(UserName=user_name, PolicyArn=pol['PolicyArn']))
        for pol_name in iam.list_user_policies(UserName=user_name).get('PolicyNames', []):
            _ignore_missing(lambda: iam.delete_user_policy(UserName=user_name, PolicyName=pol_name))
        for key in iam.list_access_keys(UserName=user_name).get('AccessKeyMetadata', []):
            _ignore_missing(lambda: iam.delete_access_key(UserName=user_name, AccessKeyId=key['AccessKeyId']))
        for grp in iam.list_groups_for_user(UserName=user_name).get('Groups', []):
            _ignore_missing(lambda: iam.remove_user_from_group(UserName=user_name, GroupName=grp['GroupName']))
    except Exception:
        pass

    try:
        iam.delete_user(UserName=user_name)
        deleted = True
        stdout = _json_text({})
        stderr = ''
        exit_code = 0
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') != 'NoSuchEntity':
            raise
        stdout = _json_text({})
        stderr = 'User Alice did not exist.'
        exit_code = 0

    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'create-user-alice',
        'command': command,
        'exit_code': exit_code,
        'stdout': stdout,
        'stderr': stderr,
        'json': {},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted': deleted,
        'verification': {
            'status': 'passed',
            'message': 'User Alice was removed.' if deleted else 'User Alice was already absent.',
        },
    }


def _run_iam_create_admin_user() -> dict[str, Any]:
    command = f'aws iam create-user --user-name {FLOCI_ADMIN_USER_NAME}'
    started = time.perf_counter()

    try:
        response = _iam_client().create_user(UserName=FLOCI_ADMIN_USER_NAME)
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_user(UserName=FLOCI_ADMIN_USER_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_iam_user(FLOCI_ADMIN_USER_NAME)

    return {
        'service': 'iam',
        'lab': 'create-admin-user',
        'step': 'create-user',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_put_admin_policy() -> dict[str, Any]:
    command = f'aws iam put-user-policy --user-name {FLOCI_ADMIN_USER_NAME} --policy-name {FLOCI_ADMIN_POLICY_NAME} --policy-document file://administrator-access.json'
    started = time.perf_counter()
    _iam_client().put_user_policy(
        UserName=FLOCI_ADMIN_USER_NAME,
        PolicyName=FLOCI_ADMIN_POLICY_NAME,
        PolicyDocument=json.dumps(FLOCI_ADMIN_POLICY_DOCUMENT),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_iam_admin_policy()

    return {
        'service': 'iam',
        'lab': 'create-admin-user',
        'step': 'put-admin-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_create_admin_access_key() -> dict[str, Any]:
    command = f'aws iam create-access-key --user-name {FLOCI_ADMIN_USER_NAME}'
    started = time.perf_counter()
    response = _iam_client().create_access_key(UserName=FLOCI_ADMIN_USER_NAME)
    key = response.get('AccessKey', {})
    if key.get('AccessKeyId') and key.get('SecretAccessKey'):
        cache.set(FLOCI_ADMIN_ACCESS_KEY_CACHE_KEY, key['AccessKeyId'], timeout=3600)
        cache.set(FLOCI_ADMIN_SECRET_KEY_CACHE_KEY, key['SecretAccessKey'], timeout=3600)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_iam_admin_access_key()

    return {
        'service': 'iam',
        'lab': 'create-admin-user',
        'step': 'create-access-key',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_verify_admin_identity() -> dict[str, Any]:
    command = 'aws sts get-caller-identity'
    key_id = cache.get(FLOCI_ADMIN_ACCESS_KEY_CACHE_KEY)
    secret_key = cache.get(FLOCI_ADMIN_SECRET_KEY_CACHE_KEY)
    if not key_id or not secret_key:
        raise ValueError('Run the access key step first so the lab can verify the generated admin credentials.')

    started = time.perf_counter()
    identity = {
        'type': 'user',
        'label': FLOCI_ADMIN_USER_NAME,
        'access_key_id': key_id,
        'secret_access_key': secret_key,
    }
    response = FlociClientFactory(identity=identity).client('sts').get_caller_identity()
    cache.set(FLOCI_ADMIN_IDENTITY_CACHE_KEY, _clean_response(response), timeout=3600)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_iam_admin_identity()

    return {
        'service': 'iam',
        'lab': 'create-admin-user',
        'step': 'verify-admin-identity',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _verify_iam_admin_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_user_policy(
            UserName=FLOCI_ADMIN_USER_NAME,
            PolicyName=FLOCI_ADMIN_POLICY_NAME,
        )
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    document = response.get('PolicyDocument') or {}
    statements = document.get('Statement') if isinstance(document, dict) else []
    statements = statements if isinstance(statements, list) else [statements]
    has_admin_allow = any(
        statement.get('Effect') == 'Allow'
        and statement.get('Action') == '*'
        and statement.get('Resource') == '*'
        for statement in statements
        if isinstance(statement, dict)
    )
    if has_admin_allow:
        return {
            'status': 'passed',
            'message': f'{FLOCI_ADMIN_USER_NAME} has administrator permissions.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'{FLOCI_ADMIN_POLICY_NAME} does not contain the expected administrator allow statement.',
        'resource': _clean_response(response),
    }


def _verify_iam_admin_access_key() -> dict[str, Any]:
    try:
        response = _iam_client().list_access_keys(UserName=FLOCI_ADMIN_USER_NAME)
    except ClientError as exc:
        return {
            'status': 'failed',
            'message': str(exc),
        }

    keys = response.get('AccessKeyMetadata', [])
    active_keys = [key for key in keys if key.get('Status') == 'Active']
    if active_keys:
        return {
            'status': 'passed',
            'message': f'{FLOCI_ADMIN_USER_NAME} has an active access key.',
            'resource': _clean_response(active_keys),
        }
    return {
        'status': 'failed',
        'message': f'{FLOCI_ADMIN_USER_NAME} has no active access keys.',
        'resource': _clean_response(keys),
    }


def _verify_iam_admin_identity() -> dict[str, Any]:
    cached_identity = cache.get(FLOCI_ADMIN_IDENTITY_CACHE_KEY)
    arn = (cached_identity or {}).get('Arn') if isinstance(cached_identity, dict) else None
    if arn and arn.endswith(f':user/{FLOCI_ADMIN_USER_NAME}'):
        return {
            'status': 'passed',
            'message': f'Generated credentials resolve to {FLOCI_ADMIN_USER_NAME}.',
            'resource': cached_identity,
        }

    key_id = cache.get(FLOCI_ADMIN_ACCESS_KEY_CACHE_KEY)
    secret_key = cache.get(FLOCI_ADMIN_SECRET_KEY_CACHE_KEY)
    if key_id and secret_key:
        try:
            response = FlociClientFactory(identity={
                'type': 'user',
                'label': FLOCI_ADMIN_USER_NAME,
                'access_key_id': key_id,
                'secret_access_key': secret_key,
            }).client('sts').get_caller_identity()
        except Exception as exc:
            return {
                'status': 'failed',
                'message': str(exc),
            }
        clean_response = _clean_response(response)
        if str(clean_response.get('Arn', '')).endswith(f':user/{FLOCI_ADMIN_USER_NAME}'):
            cache.set(FLOCI_ADMIN_IDENTITY_CACHE_KEY, clean_response, timeout=3600)
            return {
                'status': 'passed',
                'message': f'Generated credentials resolve to {FLOCI_ADMIN_USER_NAME}.',
                'resource': clean_response,
            }

    return {
        'status': 'failed',
        'message': 'Run the access key and identity verification steps to prove the admin credentials.',
    }


def _reset_iam_create_admin_user() -> dict[str, Any]:
    command = f'aws iam delete-user --user-name {FLOCI_ADMIN_USER_NAME}'
    started = time.perf_counter()
    iam = _iam_client()
    deleted_keys = 0
    deleted_policy = False
    deleted_user = False

    try:
        keys = iam.list_access_keys(UserName=FLOCI_ADMIN_USER_NAME).get('AccessKeyMetadata', [])
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
        keys = []

    for key in keys:
        key_id = key.get('AccessKeyId')
        if key_id:
            _ignore_missing(lambda key_id=key_id: iam.delete_access_key(UserName=FLOCI_ADMIN_USER_NAME, AccessKeyId=key_id))
            deleted_keys += 1

    deleted_policy = _ignore_missing(lambda: iam.delete_user_policy(
        UserName=FLOCI_ADMIN_USER_NAME,
        PolicyName=FLOCI_ADMIN_POLICY_NAME,
    ))
    deleted_user = _ignore_missing(lambda: iam.delete_user(UserName=FLOCI_ADMIN_USER_NAME))
    cache.delete(FLOCI_ADMIN_ACCESS_KEY_CACHE_KEY)
    cache.delete(FLOCI_ADMIN_SECRET_KEY_CACHE_KEY)
    cache.delete(FLOCI_ADMIN_IDENTITY_CACHE_KEY)
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'create-admin-user',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted': deleted_user,
        'verification': {
            'status': 'passed',
            'message': f'Removed {FLOCI_ADMIN_USER_NAME}, {deleted_keys} access keys, and admin policy.' if deleted_user else f'{FLOCI_ADMIN_USER_NAME} was already absent.',
            'resource': {
                'deleted_keys': deleted_keys,
                'deleted_policy': deleted_policy,
                'deleted_user': deleted_user,
            },
        },
    }


def _run_iam_create_alice_policy() -> dict[str, Any]:
    command = 'aws iam create-policy --policy-name AliceListBucketsPolicy --policy-document file://alice-list-buckets-policy.json'
    started = time.perf_counter()
    try:
        response = _iam_client().create_policy(
            PolicyName=ALICE_POLICY_NAME,
            PolicyDocument=json.dumps(ALICE_LIST_BUCKETS_POLICY),
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_policy(PolicyArn=ALICE_POLICY_ARN)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_policy()

    return {
        'service': 'iam',
        'lab': 'attach-policy-alice',
        'step': 'create-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_attach_alice_policy() -> dict[str, Any]:
    command = f'aws iam attach-user-policy --user-name Alice --policy-arn {ALICE_POLICY_ARN}'
    started = time.perf_counter()
    _iam_client().attach_user_policy(UserName=ALICE_USER_NAME, PolicyArn=ALICE_POLICY_ARN)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_policy_attachment()

    return {
        'service': 'iam',
        'lab': 'attach-policy-alice',
        'step': 'attach-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_list_alice_attached_policies() -> dict[str, Any]:
    command = 'aws iam list-attached-user-policies --user-name Alice'
    started = time.perf_counter()
    response = _iam_client().list_attached_user_policies(UserName=ALICE_USER_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_policy_attachment()

    return {
        'service': 'iam',
        'lab': 'attach-policy-alice',
        'step': 'list-attached-policies',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_create_alice_access_key() -> dict[str, Any]:
    command = 'aws iam create-access-key --user-name Alice'
    started = time.perf_counter()
    response = _iam_client().create_access_key(UserName=ALICE_USER_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_access_key()

    return {
        'service': 'iam',
        'lab': 'access-key-alice',
        'step': 'create-access-key',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_list_alice_access_keys() -> dict[str, Any]:
    command = 'aws iam list-access-keys --user-name Alice'
    started = time.perf_counter()
    response = _iam_client().list_access_keys(UserName=ALICE_USER_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_access_key()

    return {
        'service': 'iam',
        'lab': 'access-key-alice',
        'step': 'list-access-keys',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_create_floci_developers_group(lab_key: str = 'group-membership-alice') -> dict[str, Any]:
    command = f'aws iam create-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME}'
    started = time.perf_counter()
    try:
        response = _iam_client().create_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME)
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_floci_developers_group()

    return {
        'service': 'iam',
        'lab': lab_key,
        'step': 'create-group',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_add_alice_to_floci_developers_group(lab_key: str = 'group-membership-alice') -> dict[str, Any]:
    command = f'aws iam add-user-to-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME} --user-name Alice'
    started = time.perf_counter()
    _iam_client().add_user_to_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME, UserName=ALICE_USER_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_group_membership()

    return {
        'service': 'iam',
        'lab': lab_key,
        'step': 'add-user-to-group',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_create_floci_developers_policy() -> dict[str, Any]:
    command = f'aws iam create-policy --policy-name {FLOCI_DEVELOPERS_POLICY_NAME} --policy-document file://floci-developers-list-buckets-policy.json'
    started = time.perf_counter()
    try:
        response = _iam_client().create_policy(
            PolicyName=FLOCI_DEVELOPERS_POLICY_NAME,
            PolicyDocument=json.dumps(ALICE_LIST_BUCKETS_POLICY),
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_policy(PolicyArn=FLOCI_DEVELOPERS_POLICY_ARN)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_floci_developers_policy()

    return {
        'service': 'iam',
        'lab': 'group-policy-floci-developers',
        'step': 'create-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_attach_floci_developers_policy() -> dict[str, Any]:
    command = f'aws iam attach-group-policy --group-name {FLOCI_DEVELOPERS_GROUP_NAME} --policy-arn {FLOCI_DEVELOPERS_POLICY_ARN}'
    started = time.perf_counter()
    _iam_client().attach_group_policy(
        GroupName=FLOCI_DEVELOPERS_GROUP_NAME,
        PolicyArn=FLOCI_DEVELOPERS_POLICY_ARN,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_floci_developers_policy_attachment()

    return {
        'service': 'iam',
        'lab': 'group-policy-floci-developers',
        'step': 'attach-group-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_list_floci_developers_attached_policies() -> dict[str, Any]:
    command = f'aws iam list-attached-group-policies --group-name {FLOCI_DEVELOPERS_GROUP_NAME}'
    started = time.perf_counter()
    response = _iam_client().list_attached_group_policies(GroupName=FLOCI_DEVELOPERS_GROUP_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_floci_developers_policy_attachment()

    return {
        'service': 'iam',
        'lab': 'group-policy-floci-developers',
        'step': 'list-attached-group-policies',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_put_alice_inline_policy() -> dict[str, Any]:
    command = f'aws iam put-user-policy --user-name Alice --policy-name {ALICE_INLINE_POLICY_NAME} --policy-document file://alice-inline-list-buckets-policy.json'
    started = time.perf_counter()
    _iam_client().put_user_policy(
        UserName=ALICE_USER_NAME,
        PolicyName=ALICE_INLINE_POLICY_NAME,
        PolicyDocument=json.dumps(ALICE_LIST_BUCKETS_POLICY),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_inline_policy()

    return {
        'service': 'iam',
        'lab': 'inline-policy-alice',
        'step': 'put-user-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_list_alice_inline_policies() -> dict[str, Any]:
    command = 'aws iam list-user-policies --user-name Alice'
    started = time.perf_counter()
    response = _iam_client().list_user_policies(UserName=ALICE_USER_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_inline_policy()

    return {
        'service': 'iam',
        'lab': 'inline-policy-alice',
        'step': 'list-user-policies',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_get_alice_inline_policy() -> dict[str, Any]:
    command = f'aws iam get-user-policy --user-name Alice --policy-name {ALICE_INLINE_POLICY_NAME}'
    started = time.perf_counter()
    response = _iam_client().get_user_policy(
        UserName=ALICE_USER_NAME,
        PolicyName=ALICE_INLINE_POLICY_NAME,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_inline_policy()

    return {
        'service': 'iam',
        'lab': 'inline-policy-alice',
        'step': 'get-user-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_create_role(
    *,
    lab_key: str,
    role_name: str,
    trust_policy: dict[str, Any],
    artifact_name: str,
    trusted_service: str,
) -> dict[str, Any]:
    command = f'aws iam create-role --role-name {role_name} --assume-role-policy-document file://{artifact_name}'
    started = time.perf_counter()
    try:
        response = _iam_client().create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_role(RoleName=role_name)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_role(role_name, trusted_service)

    return {
        'service': 'iam',
        'lab': lab_key,
        'step': 'create-role',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_create_application_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='role-trust-policy',
        role_name=FLOCI_APPLICATION_ROLE_NAME,
        trust_policy=FLOCI_APPLICATION_TRUST_POLICY,
        artifact_name='floci-application-role-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    )


def _run_iam_get_application_role() -> dict[str, Any]:
    command = f'aws iam get-role --role-name {FLOCI_APPLICATION_ROLE_NAME}'
    started = time.perf_counter()
    response = _iam_client().get_role(RoleName=FLOCI_APPLICATION_ROLE_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_role(FLOCI_APPLICATION_ROLE_NAME, 'lambda.amazonaws.com')

    return {
        'service': 'iam',
        'lab': 'role-trust-policy',
        'step': 'get-role',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_put_application_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {FLOCI_APPLICATION_ROLE_NAME} '
        f'--policy-name {FLOCI_APPLICATION_ROLE_POLICY_NAME} '
        '--policy-document file://floci-application-list-buckets-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=FLOCI_APPLICATION_ROLE_NAME,
        PolicyName=FLOCI_APPLICATION_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(ALICE_LIST_BUCKETS_POLICY),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_application_role_policy()

    return {
        'service': 'iam',
        'lab': 'role-trust-policy',
        'step': 'put-role-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_get_application_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam get-role-policy --role-name {FLOCI_APPLICATION_ROLE_NAME} '
        f'--policy-name {FLOCI_APPLICATION_ROLE_POLICY_NAME}'
    )
    started = time.perf_counter()
    response = _iam_client().get_role_policy(
        RoleName=FLOCI_APPLICATION_ROLE_NAME,
        PolicyName=FLOCI_APPLICATION_ROLE_POLICY_NAME,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_application_role_policy()

    return {
        'service': 'iam',
        'lab': 'role-trust-policy',
        'step': 'get-role-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_create_sts_session_role() -> dict[str, Any]:
    command = (
        f'aws iam create-role --role-name {FLOCI_STS_SESSION_ROLE_NAME} '
        '--assume-role-policy-document file://floci-sts-role-trust-policy.json'
    )
    started = time.perf_counter()
    try:
        response = _iam_client().create_role(
            RoleName=FLOCI_STS_SESSION_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(FLOCI_STS_TRUST_POLICY),
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_role(RoleName=FLOCI_STS_SESSION_ROLE_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sts_session_role()

    return {
        'service': 'iam',
        'lab': 'sts-session-policy',
        'step': 'create-role',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_put_sts_session_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {FLOCI_STS_SESSION_ROLE_NAME} '
        f'--policy-name {FLOCI_STS_ROLE_POLICY_NAME} '
        '--policy-document file://floci-sts-list-buckets-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=FLOCI_STS_SESSION_ROLE_NAME,
        PolicyName=FLOCI_STS_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(ALICE_LIST_BUCKETS_POLICY),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_sts_session_role_policy()

    return {
        'service': 'iam',
        'lab': 'sts-session-policy',
        'step': 'put-role-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_sts_assume_session_policy_role() -> dict[str, Any]:
    role_arn = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{FLOCI_STS_SESSION_ROLE_NAME}'
    command = (
        f'aws sts assume-role --role-arn {role_arn} '
        f'--role-session-name {FLOCI_STS_SESSION_NAME} '
        '--policy file://session-policy.json'
    )
    started = time.perf_counter()
    response = _sts_client().assume_role(
        RoleArn=role_arn,
        RoleSessionName=FLOCI_STS_SESSION_NAME,
        Policy=json.dumps(FLOCI_STS_SESSION_POLICY),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    credentials = response.get('Credentials', {})
    verification_resource = {
        'role_arn': role_arn,
        'session_name': FLOCI_STS_SESSION_NAME,
        'access_key_id': credentials.get('AccessKeyId'),
        'assumed_role_user': _clean_response(response.get('AssumedRoleUser')),
    }
    verified = bool(
        credentials.get('AccessKeyId')
        and credentials.get('SecretAccessKey')
        and credentials.get('SessionToken')
    )
    if verified:
        cache.set(FLOCI_STS_ASSUME_CACHE_KEY, verification_resource, timeout=3600)
    verification = (
        _verify_sts_assumed_role_session()
        if verified
        else {
            'status': 'failed',
            'message': 'STS did not return a complete temporary credential set.',
            'resource': _clean_response(response),
        }
    )

    return {
        'service': 'iam',
        'lab': 'sts-session-policy',
        'step': 'assume-role',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _lab_step_result(lab_key: str, step_key: str, command: str, stdout: str, verification: dict[str, Any], started: float, json_value: Any = None) -> dict[str, Any]:
    return {
        'service': 'iam',
        'lab': lab_key,
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': stdout,
        'stderr': '',
        'json': _clean_response(json_value if json_value is not None else {}),
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _iam_enforcement_user_identity() -> dict[str, Any]:
    access_key_id = cache.get(IAM_ENFORCEMENT_USER_KEY_CACHE_KEY)
    secret_access_key = cache.get(IAM_ENFORCEMENT_USER_SECRET_CACHE_KEY)
    if not access_key_id or not secret_access_key:
        key = _rotate_iam_enforcement_user_access_key()
        access_key_id = key.get('AccessKeyId')
        secret_access_key = key.get('SecretAccessKey')
    return {
        'type': 'user',
        'label': IAM_ENFORCEMENT_USER_NAME,
        'access_key_id': access_key_id,
        'secret_access_key': secret_access_key,
    }


def _rotate_iam_enforcement_user_access_key() -> dict[str, Any]:
    iam = _iam_client()
    try:
        keys = iam.list_access_keys(UserName=IAM_ENFORCEMENT_USER_NAME).get('AccessKeyMetadata', [])
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
        keys = []

    for key in keys:
        key_id = key.get('AccessKeyId')
        if key_id:
            _ignore_missing(lambda key_id=key_id: iam.delete_access_key(
                UserName=IAM_ENFORCEMENT_USER_NAME,
                AccessKeyId=key_id,
            ))

    key = iam.create_access_key(UserName=IAM_ENFORCEMENT_USER_NAME).get('AccessKey', {})
    cache.set(IAM_ENFORCEMENT_USER_KEY_CACHE_KEY, key.get('AccessKeyId'), timeout=3600)
    cache.set(IAM_ENFORCEMENT_USER_SECRET_CACHE_KEY, key.get('SecretAccessKey'), timeout=3600)
    cache.delete(IAM_ENFORCEMENT_ROLE_CREDENTIALS_CACHE_KEY)
    return key


def _iam_enforcement_role_identity() -> dict[str, Any]:
    credentials = cache.get(IAM_ENFORCEMENT_ROLE_CREDENTIALS_CACHE_KEY) or {}
    if not credentials.get('AccessKeyId'):
        response = FlociClientFactory(identity=_iam_enforcement_user_identity()).client('sts').assume_role(
            RoleArn=IAM_ENFORCEMENT_ROLE_ARN,
            RoleSessionName=IAM_ENFORCEMENT_SESSION_NAME,
        )
        credentials = response.get('Credentials', {})
        cache.set(IAM_ENFORCEMENT_ROLE_CREDENTIALS_CACHE_KEY, credentials, timeout=3600)
    return {
        'type': 'assumed_role',
        'label': f'{IAM_ENFORCEMENT_ROLE_NAME}/{IAM_ENFORCEMENT_SESSION_NAME}',
        'access_key_id': credentials.get('AccessKeyId'),
        'secret_access_key': credentials.get('SecretAccessKey'),
        'session_token': credentials.get('SessionToken'),
    }


def _run_iam_enforcement_create_user() -> dict[str, Any]:
    command = f'aws iam create-user --user-name {IAM_ENFORCEMENT_USER_NAME}'
    started = time.perf_counter()
    try:
        response = _iam_client().create_user(UserName=IAM_ENFORCEMENT_USER_NAME)
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_user(UserName=IAM_ENFORCEMENT_USER_NAME)
    _iam_client().put_user_policy(
        UserName=IAM_ENFORCEMENT_USER_NAME,
        PolicyName='CharlieSelfIdentity',
        PolicyDocument=json.dumps(IAM_ENFORCEMENT_SELF_POLICY),
    )
    _rotate_iam_enforcement_user_access_key()
    verification = _verify_iam_user(IAM_ENFORCEMENT_USER_NAME)
    return _lab_step_result('identity-enforcement-capstone', 'create-user', command, _json_text(response), verification, started, response)


def _run_iam_enforcement_verify_user_s3_denied() -> dict[str, Any]:
    command = f'aws s3 ls  # using {IAM_ENFORCEMENT_USER_NAME} credentials'
    started = time.perf_counter()
    try:
        FlociClientFactory(identity=_iam_enforcement_user_identity()).client('s3').list_buckets()
        verification = {'status': 'failed', 'message': 'Charlie unexpectedly listed S3 buckets.'}
        stdout = _json_text({'unexpected': 's3 list succeeded'})
    except ClientError as exc:
        verification = {'status': 'passed', 'message': 'Charlie was denied S3 ListBuckets.', 'resource': {'code': _error_code(exc)}}
        cache.set(IAM_ENFORCEMENT_USER_DENY_CACHE_KEY, verification['resource'], timeout=3600)
        stdout = _json_text({'denied': True, 'code': _error_code(exc)})
    return _lab_step_result('identity-enforcement-capstone', 'verify-user-denied', command, stdout, verification, started)


def _run_iam_enforcement_create_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='identity-enforcement-capstone',
        role_name=IAM_ENFORCEMENT_ROLE_NAME,
        trust_policy=IAM_ENFORCEMENT_ROLE_TRUST_POLICY,
        artifact_name='charlie-sqs-role-trust-policy.json',
        trusted_service=IAM_ENFORCEMENT_USER_ARN,
    )


def _run_iam_enforcement_put_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {IAM_ENFORCEMENT_ROLE_NAME} '
        f'--policy-name {IAM_ENFORCEMENT_ROLE_POLICY_NAME} '
        '--policy-document file://charlie-sqs-read-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=IAM_ENFORCEMENT_ROLE_NAME,
        PolicyName=IAM_ENFORCEMENT_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(IAM_ENFORCEMENT_ROLE_POLICY),
    )
    verification = _verify_iam_enforcement_role_policy()
    return _lab_step_result('identity-enforcement-capstone', 'put-role-policy', command, _json_text({}), verification, started)


def _run_iam_enforcement_assume_role() -> dict[str, Any]:
    command = f'aws sts assume-role --role-arn {IAM_ENFORCEMENT_ROLE_ARN} --role-session-name {IAM_ENFORCEMENT_SESSION_NAME}  # using {IAM_ENFORCEMENT_USER_NAME} credentials'
    started = time.perf_counter()
    _iam_client().put_user_policy(
        UserName=IAM_ENFORCEMENT_USER_NAME,
        PolicyName='CharlieSelfIdentity',
        PolicyDocument=json.dumps(IAM_ENFORCEMENT_SELF_POLICY),
    )
    _rotate_iam_enforcement_user_access_key()
    response = FlociClientFactory(identity=_iam_enforcement_user_identity()).client('sts').assume_role(
        RoleArn=IAM_ENFORCEMENT_ROLE_ARN,
        RoleSessionName=IAM_ENFORCEMENT_SESSION_NAME,
    )
    credentials = response.get('Credentials', {})
    cache.set(IAM_ENFORCEMENT_ROLE_CREDENTIALS_CACHE_KEY, credentials, timeout=3600)
    verification = {
        'status': 'passed' if credentials.get('SessionToken') else 'failed',
        'message': f'{IAM_ENFORCEMENT_USER_NAME} assumed {IAM_ENFORCEMENT_ROLE_NAME}.',
        'resource': {'access_key_id': credentials.get('AccessKeyId'), 'assumed_role_user': _clean_response(response.get('AssumedRoleUser'))},
    }
    return _lab_step_result('identity-enforcement-capstone', 'assume-role', command, _json_text(response), verification, started)


def _run_iam_enforcement_verify_role_sqs_allowed() -> dict[str, Any]:
    command = 'aws sqs list-queues  # using assumed-role credentials'
    started = time.perf_counter()
    response = FlociClientFactory(identity=_iam_enforcement_role_identity()).client('sqs').list_queues()
    verification = {'status': 'passed', 'message': 'The assumed role listed SQS queues.', 'resource': _clean_response(response)}
    cache.set(IAM_ENFORCEMENT_ROLE_SQS_CACHE_KEY, verification['resource'], timeout=3600)
    return _lab_step_result('identity-enforcement-capstone', 'verify-role-sqs', command, _json_text(response), verification, started)


def _run_iam_enforcement_verify_role_s3_denied() -> dict[str, Any]:
    command = 'aws s3 ls  # using assumed-role credentials'
    started = time.perf_counter()
    try:
        FlociClientFactory(identity=_iam_enforcement_role_identity()).client('s3').list_buckets()
        verification = {'status': 'failed', 'message': 'The assumed role unexpectedly listed S3 buckets.'}
        stdout = _json_text({'unexpected': 's3 list succeeded'})
    except ClientError as exc:
        verification = {'status': 'passed', 'message': 'The assumed role was denied S3 ListBuckets.', 'resource': {'code': _error_code(exc)}}
        cache.set(IAM_ENFORCEMENT_ROLE_S3_DENY_CACHE_KEY, verification['resource'], timeout=3600)
        stdout = _json_text({'denied': True, 'code': _error_code(exc)})
    return _lab_step_result('identity-enforcement-capstone', 'verify-role-s3-denied', command, stdout, verification, started)


def _run_iam_create_ec2_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='ec2-instance-profile',
        role_name=FLOCI_EC2_ROLE_NAME,
        trust_policy=FLOCI_EC2_TRUST_POLICY,
        artifact_name='floci-ec2-role-trust-policy.json',
        trusted_service='ec2.amazonaws.com',
    )


def _run_iam_create_ec2_instance_profile() -> dict[str, Any]:
    command = f'aws iam create-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME}'
    started = time.perf_counter()
    try:
        response = _iam_client().create_instance_profile(
            InstanceProfileName=FLOCI_EC2_INSTANCE_PROFILE_NAME,
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_instance_profile(
            InstanceProfileName=FLOCI_EC2_INSTANCE_PROFILE_NAME,
        )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_ec2_instance_profile()

    return {
        'service': 'iam',
        'lab': 'ec2-instance-profile',
        'step': 'create-instance-profile',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_add_ec2_role_to_instance_profile() -> dict[str, Any]:
    command = (
        f'aws iam add-role-to-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME} '
        f'--role-name {FLOCI_EC2_ROLE_NAME}'
    )
    started = time.perf_counter()
    existing = _verify_ec2_role_profile_association()
    if existing.get('status') != 'passed':
        _iam_client().add_role_to_instance_profile(
            InstanceProfileName=FLOCI_EC2_INSTANCE_PROFILE_NAME,
            RoleName=FLOCI_EC2_ROLE_NAME,
        )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_ec2_role_profile_association()

    return {
        'service': 'iam',
        'lab': 'ec2-instance-profile',
        'step': 'add-role-to-instance-profile',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({}),
        'stderr': '',
        'json': {},
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_get_ec2_instance_profile() -> dict[str, Any]:
    command = f'aws iam get-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME}'
    started = time.perf_counter()
    response = _iam_client().get_instance_profile(
        InstanceProfileName=FLOCI_EC2_INSTANCE_PROFILE_NAME,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_ec2_role_profile_association()

    return {
        'service': 'iam',
        'lab': 'ec2-instance-profile',
        'step': 'get-instance-profile',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_iam_list_instance_profiles_for_ec2_role() -> dict[str, Any]:
    command = f'aws iam list-instance-profiles-for-role --role-name {FLOCI_EC2_ROLE_NAME}'
    started = time.perf_counter()
    response = _iam_client().list_instance_profiles_for_role(RoleName=FLOCI_EC2_ROLE_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_ec2_role_profile_association()

    return {
        'service': 'iam',
        'lab': 'ec2-instance-profile',
        'step': 'list-instance-profiles-for-role',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _config_lab_step_result(
    service: str,
    lab: str,
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': service,
        'lab': lab,
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


def _cloudformation_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'cloudformation',
        'lab': 's3-sqs-stack',
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


def _run_cloudformation_validate_template() -> dict[str, Any]:
    command = (
        'aws cloudformation validate-template '
        '--template-body file://storage-messaging-stack.json'
    )
    started = time.perf_counter()
    response = _cloudformation_client().validate_template(
        TemplateBody=CLOUDFORMATION_TEMPLATE_TEXT,
    )
    verified = (
        response.get('Parameters') == []
        and response.get('Capabilities') == []
    )
    verification = {
        'status': 'passed' if verified else 'failed',
        'message': (
            'CloudFormation accepted the template with no parameters or capabilities.'
            if verified
            else 'CloudFormation did not validate the expected template shape.'
        ),
        'resource': _clean_response(response),
    }
    return _cloudformation_step_result(
        'validate-template',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_create_stack() -> dict[str, Any]:
    command = (
        f'aws cloudformation create-stack --stack-name '
        f'{CLOUDFORMATION_STACK_NAME} '
        '--template-body file://storage-messaging-stack.json'
    )
    started = time.perf_counter()
    response = _cloudformation_client().create_stack(
        StackName=CLOUDFORMATION_STACK_NAME,
        TemplateBody=CLOUDFORMATION_TEMPLATE_TEXT,
    )
    stack = _wait_for_cloudformation_status(
        {'CREATE_COMPLETE', 'CREATE_FAILED', 'ROLLBACK_COMPLETE'},
    )
    if stack.get('StackStatus') == 'CREATE_COMPLETE':
        cache.set(CLOUDFORMATION_CREATED_CACHE_KEY, True, timeout=86400)
        cache.delete_many([
            CLOUDFORMATION_INSPECTED_CACHE_KEY,
            CLOUDFORMATION_DELETED_CACHE_KEY,
        ])
    verification = _verify_cloudformation_stack()
    output = {**_clean_response(response), 'Stack': _clean_response(stack)}
    return _cloudformation_step_result(
        'create-stack',
        command,
        output,
        verification,
        started,
    )


def _run_cloudformation_describe_stack() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stacks '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    response = _cloudformation_client().describe_stacks(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    verification = _verify_cloudformation_stack()
    return _cloudformation_step_result(
        'describe-stack',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_describe_resources() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stack-resources '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    response = _cloudformation_client().describe_stack_resources(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    verification = _verify_cloudformation_resources()
    return _cloudformation_step_result(
        'describe-stack-resources',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_describe_events() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stack-events '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    response = _cloudformation_client().describe_stack_events(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    verification = _verify_cloudformation_events()
    return _cloudformation_step_result(
        'describe-stack-events',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_inspect_services() -> dict[str, Any]:
    command = (
        f'aws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\n'
        'aws sqs get-queue-attributes --queue-url <queue-url> '
        '--attribute-names QueueArn VisibilityTimeout'
    )
    started = time.perf_counter()
    verification = _verify_cloudformation_service_resources()
    if verification.get('status') == 'passed':
        cache.set(CLOUDFORMATION_INSPECTED_CACHE_KEY, True, timeout=86400)
    response = verification.get('resource', {})
    return _cloudformation_step_result(
        'inspect-provisioned-resources',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_delete_stack() -> dict[str, Any]:
    command = (
        f'aws cloudformation delete-stack '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    if not cache.get(CLOUDFORMATION_INSPECTED_CACHE_KEY):
        raise ValueError('Inspect the provisioned resources before deleting the stack.')
    started = time.perf_counter()
    response = _cloudformation_client().delete_stack(
        StackName=CLOUDFORMATION_STACK_NAME,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            _cloudformation_stack()
        except ClientError:
            cache.set(CLOUDFORMATION_DELETED_CACHE_KEY, True, timeout=86400)
            break
        time.sleep(0.1)
    verification = _verify_cloudformation_deleted()
    return _cloudformation_step_result(
        'delete-stack',
        command,
        response,
        verification,
        started,
    )


def _run_cloudformation_confirm_deleted() -> dict[str, Any]:
    command = (
        f'aws cloudformation describe-stacks '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}\n'
        f'aws s3api head-bucket --bucket {CLOUDFORMATION_BUCKET_NAME}\n'
        f'aws sqs get-queue-url --queue-name {CLOUDFORMATION_QUEUE_NAME}'
    )
    started = time.perf_counter()
    verification = _verify_cloudformation_deleted()
    response = {
        'stack_absent': verification.get('status') == 'passed',
        'bucket_absent': verification.get('status') == 'passed',
        'queue_absent': verification.get('status') == 'passed',
    }
    return _cloudformation_step_result(
        'confirm-resources-deleted',
        command,
        response,
        verification,
        started,
    )


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


def _run_iam_get_floci_developers_group() -> dict[str, Any]:
    command = f'aws iam get-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME}'
    started = time.perf_counter()
    response = _iam_client().get_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_alice_group_membership()

    return {
        'service': 'iam',
        'lab': 'group-membership-alice',
        'step': 'get-group',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _reset_iam_attach_policy_alice() -> dict[str, Any]:
    command = (
        'aws iam detach-user-policy --user-name Alice '
        f'--policy-arn {ALICE_POLICY_ARN}\n'
        f'aws iam delete-policy --policy-arn {ALICE_POLICY_ARN}'
    )
    started = time.perf_counter()
    iam = _iam_client()
    detached = _ignore_missing(lambda: iam.detach_user_policy(UserName=ALICE_USER_NAME, PolicyArn=ALICE_POLICY_ARN))
    deleted = _ignore_missing(lambda: iam.delete_policy(PolicyArn=ALICE_POLICY_ARN))
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'attach-policy-alice',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'detached': detached, 'deleted': deleted}),
        'stderr': '',
        'json': {'detached': detached, 'deleted': deleted},
        'duration_ms': duration_ms,
        'reset': True,
        'detached': detached,
        'deleted': deleted,
        'verification': {
            'status': 'passed',
            'message': 'Alice policy attachment and customer managed policy were removed.',
        },
    }


def _reset_iam_group_membership_alice() -> dict[str, Any]:
    command = (
        f'aws iam remove-user-from-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME} --user-name Alice\n'
        f'aws iam delete-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME}'
    )
    started = time.perf_counter()
    iam = _iam_client()
    removed = _ignore_missing(lambda: iam.remove_user_from_group(
        GroupName=FLOCI_DEVELOPERS_GROUP_NAME,
        UserName=ALICE_USER_NAME,
    ))
    try:
        for pol in iam.list_attached_group_policies(GroupName=FLOCI_DEVELOPERS_GROUP_NAME).get('AttachedPolicies', []):
            _ignore_missing(lambda: iam.detach_group_policy(GroupName=FLOCI_DEVELOPERS_GROUP_NAME, PolicyArn=pol['PolicyArn']))
    except Exception:
        pass
    deleted = _ignore_missing(lambda: iam.delete_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME))
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'group-membership-alice',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'removed': removed, 'deleted': deleted}),
        'stderr': '',
        'json': {'removed': removed, 'deleted': deleted},
        'duration_ms': duration_ms,
        'reset': True,
        'removed': removed,
        'deleted': deleted,
        'verification': {
            'status': 'passed',
            'message': f'Alice group membership and {FLOCI_DEVELOPERS_GROUP_NAME} were removed.',
        },
    }


def _reset_iam_group_policy_floci_developers() -> dict[str, Any]:
    command = (
        f'aws iam detach-group-policy --group-name {FLOCI_DEVELOPERS_GROUP_NAME} '
        f'--policy-arn {FLOCI_DEVELOPERS_POLICY_ARN}\n'
        f'aws iam delete-policy --policy-arn {FLOCI_DEVELOPERS_POLICY_ARN}\n'
        f'aws iam remove-user-from-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME} --user-name Alice\n'
        f'aws iam delete-group --group-name {FLOCI_DEVELOPERS_GROUP_NAME}'
    )
    started = time.perf_counter()
    iam = _iam_client()
    detached = _ignore_missing(lambda: iam.detach_group_policy(
        GroupName=FLOCI_DEVELOPERS_GROUP_NAME,
        PolicyArn=FLOCI_DEVELOPERS_POLICY_ARN,
    ))
    deleted_policy = _ignore_missing(lambda: iam.delete_policy(PolicyArn=FLOCI_DEVELOPERS_POLICY_ARN))
    removed = _ignore_missing(lambda: iam.remove_user_from_group(
        GroupName=FLOCI_DEVELOPERS_GROUP_NAME,
        UserName=ALICE_USER_NAME,
    ))
    deleted_group = _ignore_missing(lambda: iam.delete_group(GroupName=FLOCI_DEVELOPERS_GROUP_NAME))
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'group-policy-floci-developers',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({
            'detached': detached,
            'deleted_policy': deleted_policy,
            'removed': removed,
            'deleted_group': deleted_group,
        }),
        'stderr': '',
        'json': {
            'detached': detached,
            'deleted_policy': deleted_policy,
            'removed': removed,
            'deleted_group': deleted_group,
        },
        'duration_ms': duration_ms,
        'reset': True,
        'detached': detached,
        'deleted_policy': deleted_policy,
        'removed': removed,
        'deleted_group': deleted_group,
        'verification': {
            'status': 'passed',
            'message': f'{FLOCI_DEVELOPERS_POLICY_NAME}, Alice group membership, and {FLOCI_DEVELOPERS_GROUP_NAME} were removed.',
        },
    }


def _reset_iam_inline_policy_alice() -> dict[str, Any]:
    command = f'aws iam delete-user-policy --user-name Alice --policy-name {ALICE_INLINE_POLICY_NAME}'
    started = time.perf_counter()
    deleted = _ignore_missing(lambda: _iam_client().delete_user_policy(
        UserName=ALICE_USER_NAME,
        PolicyName=ALICE_INLINE_POLICY_NAME,
    ))
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'inline-policy-alice',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted': deleted}),
        'stderr': '',
        'json': {'deleted': deleted},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted': deleted,
        'verification': {
            'status': 'passed',
            'message': f'Inline policy {ALICE_INLINE_POLICY_NAME} was removed from Alice.',
        },
    }


def _reset_iam_role_trust_policy() -> dict[str, Any]:
    command = (
        f'aws iam delete-role-policy --role-name {FLOCI_APPLICATION_ROLE_NAME} '
        f'--policy-name {FLOCI_APPLICATION_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {FLOCI_APPLICATION_ROLE_NAME}'
    )
    started = time.perf_counter()
    iam = _iam_client()
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=FLOCI_APPLICATION_ROLE_NAME,
        PolicyName=FLOCI_APPLICATION_ROLE_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=FLOCI_APPLICATION_ROLE_NAME))
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'role-trust-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted_policy': deleted_policy, 'deleted_role': deleted_role}),
        'stderr': '',
        'json': {'deleted_policy': deleted_policy, 'deleted_role': deleted_role},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'verification': {
            'status': 'passed',
            'message': f'Role {FLOCI_APPLICATION_ROLE_NAME} and its inline policy were removed.',
        },
    }


def _reset_iam_sts_session_policy() -> dict[str, Any]:
    command = (
        f'aws iam delete-role-policy --role-name {FLOCI_STS_SESSION_ROLE_NAME} '
        f'--policy-name {FLOCI_STS_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {FLOCI_STS_SESSION_ROLE_NAME}'
    )
    started = time.perf_counter()
    iam = _iam_client()
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=FLOCI_STS_SESSION_ROLE_NAME,
        PolicyName=FLOCI_STS_ROLE_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=FLOCI_STS_SESSION_ROLE_NAME))
    cache.delete(FLOCI_STS_ASSUME_CACHE_KEY)
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'sts-session-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted_policy': deleted_policy, 'deleted_role': deleted_role}),
        'stderr': '',
        'json': {'deleted_policy': deleted_policy, 'deleted_role': deleted_role},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'verification': {
            'status': 'passed',
            'message': f'Role {FLOCI_STS_SESSION_ROLE_NAME}, its inline policy, and the recorded STS session were removed.',
        },
    }


def _reset_iam_ec2_instance_profile() -> dict[str, Any]:
    command = (
        f'aws iam remove-role-from-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME} '
        f'--role-name {FLOCI_EC2_ROLE_NAME}\n'
        f'aws iam delete-instance-profile --instance-profile-name {FLOCI_EC2_INSTANCE_PROFILE_NAME}\n'
        f'aws iam delete-role --role-name {FLOCI_EC2_ROLE_NAME}'
    )
    started = time.perf_counter()
    iam = _iam_client()
    removed = _ignore_missing(lambda: iam.remove_role_from_instance_profile(
        InstanceProfileName=FLOCI_EC2_INSTANCE_PROFILE_NAME,
        RoleName=FLOCI_EC2_ROLE_NAME,
    ))
    deleted_profile = _ignore_missing(lambda: iam.delete_instance_profile(
        InstanceProfileName=FLOCI_EC2_INSTANCE_PROFILE_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=FLOCI_EC2_ROLE_NAME))
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'ec2-instance-profile',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({
            'removed': removed,
            'deleted_profile': deleted_profile,
            'deleted_role': deleted_role,
        }),
        'stderr': '',
        'json': {
            'removed': removed,
            'deleted_profile': deleted_profile,
            'deleted_role': deleted_role,
        },
        'duration_ms': duration_ms,
        'reset': True,
        'removed': removed,
        'deleted_profile': deleted_profile,
        'deleted_role': deleted_role,
        'verification': {
            'status': 'passed',
            'message': f'Instance profile {FLOCI_EC2_INSTANCE_PROFILE_NAME} and role {FLOCI_EC2_ROLE_NAME} were removed.',
        },
    }


def _reset_iam_enforcement_capstone() -> dict[str, Any]:
    command = (
        f'aws iam delete-role-policy --role-name {IAM_ENFORCEMENT_ROLE_NAME} '
        f'--policy-name {IAM_ENFORCEMENT_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {IAM_ENFORCEMENT_ROLE_NAME}\n'
        f'aws iam delete-access-key --user-name {IAM_ENFORCEMENT_USER_NAME} --access-key-id <CharlieAccessKeyId>\n'
        f'aws iam delete-user-policy --user-name {IAM_ENFORCEMENT_USER_NAME} --policy-name CharlieSelfIdentity\n'
        f'aws iam delete-user --user-name {IAM_ENFORCEMENT_USER_NAME}'
    )
    started = time.perf_counter()
    iam = _iam_client()
    deleted_role_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=IAM_ENFORCEMENT_ROLE_NAME,
        PolicyName=IAM_ENFORCEMENT_ROLE_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=IAM_ENFORCEMENT_ROLE_NAME))
    deleted_keys = []
    try:
        for key in iam.list_access_keys(UserName=IAM_ENFORCEMENT_USER_NAME).get('AccessKeyMetadata', []):
            key_id = key.get('AccessKeyId')
            if key_id and _ignore_missing(lambda key_id=key_id: iam.delete_access_key(UserName=IAM_ENFORCEMENT_USER_NAME, AccessKeyId=key_id)):
                deleted_keys.append(key_id)
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
    deleted_user_policy = _ignore_missing(lambda: iam.delete_user_policy(
        UserName=IAM_ENFORCEMENT_USER_NAME,
        PolicyName='CharlieSelfIdentity',
    ))
    deleted_user = _ignore_missing(lambda: iam.delete_user(UserName=IAM_ENFORCEMENT_USER_NAME))
    for cache_key in [
        IAM_ENFORCEMENT_USER_KEY_CACHE_KEY,
        IAM_ENFORCEMENT_USER_SECRET_CACHE_KEY,
        IAM_ENFORCEMENT_USER_DENY_CACHE_KEY,
        IAM_ENFORCEMENT_ROLE_CREDENTIALS_CACHE_KEY,
        IAM_ENFORCEMENT_ROLE_SQS_CACHE_KEY,
        IAM_ENFORCEMENT_ROLE_S3_DENY_CACHE_KEY,
    ]:
        cache.delete(cache_key)
    result = {
        'deleted_role_policy': deleted_role_policy,
        'deleted_role': deleted_role,
        'deleted_keys': deleted_keys,
        'deleted_user_policy': deleted_user_policy,
        'deleted_user': deleted_user,
    }
    return {
        'service': 'iam',
        'lab': 'identity-enforcement-capstone',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(result),
        'stderr': '',
        'json': result,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'verification': {
            'status': 'passed',
            'message': 'Charlie, the SQS read role, and recorded enforcement checks were removed.',
        },
    }


def _ignore_missing_codes(action, missing_codes: set[str]) -> bool:
    try:
        action()
        return True
    except ClientError as exc:
        if _error_code(exc) in missing_codes:
            return False
        raise


def _reset_cloudformation_s3_sqs() -> dict[str, Any]:
    command = (
        f'aws cloudformation delete-stack '
        f'--stack-name {CLOUDFORMATION_STACK_NAME}'
    )
    started = time.perf_counter()
    deleted_stack = False
    deleted_bucket = False
    deleted_queue = False
    try:
        _cloudformation_client().delete_stack(
            StackName=CLOUDFORMATION_STACK_NAME,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                _cloudformation_stack()
            except ClientError:
                deleted_stack = True
                break
            time.sleep(0.1)
    except ClientError:
        deleted_stack = True

    try:
        _s3_client().head_bucket(Bucket=CLOUDFORMATION_BUCKET_NAME)
    except ClientError:
        deleted_bucket = True
    if not deleted_bucket:
        bucket_result = _reset_s3_bucket(
            's3-sqs-stack',
            CLOUDFORMATION_BUCKET_NAME,
        )
        deleted_bucket = bucket_result.get('deleted_bucket', False)

    try:
        queue_url = _sqs_queue_url(CLOUDFORMATION_QUEUE_NAME)
        _sqs_client().delete_queue(QueueUrl=queue_url)
        deleted_queue = True
    except ClientError as exc:
        if _error_code(exc) in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            deleted_queue = True
        else:
            raise

    cache.delete_many([
        CLOUDFORMATION_CREATED_CACHE_KEY,
        CLOUDFORMATION_INSPECTED_CACHE_KEY,
        CLOUDFORMATION_DELETED_CACHE_KEY,
    ])
    output = {
        'deleted_stack': deleted_stack,
        'deleted_bucket': deleted_bucket,
        'deleted_queue': deleted_queue,
    }
    return {
        'service': 'cloudformation',
        'lab': 's3-sqs-stack',
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
            'message': 'The CloudFormation stack and its S3 and SQS resources were removed.',
        },
    }


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


def _reset_iam_access_key_alice() -> dict[str, Any]:
    command = 'aws iam delete-access-key --user-name Alice --access-key-id <AliceAccessKeyId>'
    started = time.perf_counter()
    iam = _iam_client()
    deleted_keys: list[str] = []

    try:
        keys = iam.list_access_keys(UserName=ALICE_USER_NAME).get('AccessKeyMetadata', [])
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
        keys = []

    for key in keys:
        access_key_id = key.get('AccessKeyId')
        if access_key_id:
            iam.delete_access_key(UserName=ALICE_USER_NAME, AccessKeyId=access_key_id)
            deleted_keys.append(access_key_id)

    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'iam',
        'lab': 'access-key-alice',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({'deleted_access_key_ids': deleted_keys}),
        'stderr': '',
        'json': {'deleted_access_key_ids': deleted_keys},
        'duration_ms': duration_ms,
        'reset': True,
        'deleted': bool(deleted_keys),
        'verification': {
            'status': 'passed',
            'message': 'Alice access keys were removed.' if deleted_keys else 'Alice had no access keys to remove.',
        },
    }


# Service lab modules. Their names stay importable from ``dashboard.labs`` so existing
# callers and tests keep working.
from . import sqs_labs, sns_labs, scheduler_labs, kms_labs, ssm_labs, secretsmanager_labs, dynamodb_labs, apigateway_labs, lambda_labs, s3_labs  # noqa: E402

for _module in (sqs_labs, sns_labs, scheduler_labs, kms_labs, ssm_labs, secretsmanager_labs, dynamodb_labs, apigateway_labs, lambda_labs, s3_labs,):
    for _name, _value in vars(_module).items():
        if not _name.startswith('__') and _name not in ('LABS', 'run_step', 'status', 'reset'):
            globals().setdefault(_name, _value)
del _module, _name, _value
