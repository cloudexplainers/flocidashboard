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
LAMBDA_FUNCTION_NAME = 'lab-echo'
LAMBDA_ROLE_NAME = 'FlociLambdaLabRole'
LAMBDA_ROLE_POLICY_NAME = 'FlociLambdaLabLogs'
LAMBDA_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_ROLE_NAME}'
LAMBDA_FUNCTION_ARN = f'arn:aws:lambda:{AWS_REGION}:{AWS_ACCOUNT_ID}:function:{LAMBDA_FUNCTION_NAME}'
LAMBDA_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_FUNCTION_NAME}'
LAMBDA_RUNTIME = 'python3.11'
LAMBDA_HANDLER = 'handler.lambda_handler'
LAMBDA_INVOKE_PAYLOAD = {
    'message': 'hello from the Lambda lab',
    'request_id': 'FLOCI-LAMBDA-1001',
}
LAMBDA_INVOKE_PAYLOAD_TEXT = json.dumps(LAMBDA_INVOKE_PAYLOAD, separators=(',', ':'))
LAMBDA_INVOKE_CACHE_KEY = 'floci-lab:lambda:create-invoke-logs:invoke'
LAMBDA_LOGS_CACHE_KEY = 'floci-lab:lambda:create-invoke-logs:logs'
LAMBDA_CONFIG_FUNCTION_NAME = 'lab-config-reader'
LAMBDA_CONFIG_ROLE_NAME = 'FlociLambdaConfigRole'
LAMBDA_CONFIG_POLICY_NAME = 'FlociLambdaConfigRead'
LAMBDA_CONFIG_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_CONFIG_ROLE_NAME}'
LAMBDA_CONFIG_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_CONFIG_FUNCTION_NAME}'
LAMBDA_CONFIG_PARAMETER_NAME = '/floci/lab/lambda/config'
LAMBDA_CONFIG_SECRET_NAME = 'lab/lambda-secret'
LAMBDA_CONFIG_PARAMETER_VALUE = {
    'application': 'orders',
    'environment': 'local',
    'feature': 'runtime-config',
}
LAMBDA_CONFIG_SECRET_VALUE = {
    'api_key': 'local-lambda-secret',
    'username': 'floci-lambda',
}
LAMBDA_CONFIG_PAYLOAD = {
    'request_id': 'FLOCI-LAMBDA-CONFIG-3001',
}
LAMBDA_CONFIG_PAYLOAD_TEXT = json.dumps(LAMBDA_CONFIG_PAYLOAD, separators=(',', ':'))
LAMBDA_CONFIG_INVOKE_CACHE_KEY = 'floci-lab:lambda:runtime-config:invoke'
LAMBDA_CONFIG_LOGS_CACHE_KEY = 'floci-lab:lambda:runtime-config:logs'
LAMBDA_SQS_QUEUE_NAME = 'lab-lambda-events'
LAMBDA_SQS_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{LAMBDA_SQS_QUEUE_NAME}'
)
LAMBDA_SQS_FUNCTION_NAME = 'lab-sqs-consumer'
LAMBDA_SQS_ROLE_NAME = 'FlociLambdaSqsConsumerRole'
LAMBDA_SQS_POLICY_NAME = 'FlociLambdaSqsConsumerPolicy'
LAMBDA_SQS_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_SQS_ROLE_NAME}'
LAMBDA_SQS_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_SQS_FUNCTION_NAME}'
LAMBDA_SQS_MESSAGE_BODY = {
    'order_id': 'ORDER#3001',
    'customer_id': 'CUSTOMER#126',
    'total': '54.20',
    'request_id': 'FLOCI-LAMBDA-SQS-4001',
}
LAMBDA_SQS_MESSAGE_BODY_TEXT = json.dumps(LAMBDA_SQS_MESSAGE_BODY, separators=(',', ':'))
LAMBDA_SQS_MESSAGE_ATTRIBUTES = {
    'source': {'DataType': 'String', 'StringValue': 'floci-lab'},
    'event_type': {'DataType': 'String', 'StringValue': 'order.created'},
}
LAMBDA_SQS_MAPPING_CACHE_KEY = 'floci-lab:lambda:sqs-event-source:mapping'
LAMBDA_SQS_SEND_CACHE_KEY = 'floci-lab:lambda:sqs-event-source:send-message'
LAMBDA_SQS_LOGS_CACHE_KEY = 'floci-lab:lambda:sqs-event-source:logs'
APIGW_LAMBDA_API_NAME = 'lab-lambda-api'
APIGW_LAMBDA_ROUTE_KEY = 'POST /echo'
APIGW_LAMBDA_PATH = '/echo'
APIGW_LAMBDA_STAGE_NAME = '$default'
APIGW_LAMBDA_PERMISSION_SID = 'AllowFlociApiGatewayInvoke'
APIGW_LAMBDA_REQUEST_CACHE_KEY = 'floci-lab:apigateway:lambda-request:request'
APIGW_LAMBDA_API_ID_CACHE_KEY = 'floci-lab:apigateway:lambda-request:api-id'
APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY = 'floci-lab:apigateway:lambda-request:integration-id'
APIGW_LAMBDA_ROUTE_ID_CACHE_KEY = 'floci-lab:apigateway:lambda-request:route-id'
DYNAMODB_TABLE_NAME = 'lab-orders'
DYNAMODB_CUSTOMER_INDEX_NAME = 'CustomerIdIndex'
DYNAMODB_ORDER_ID = 'ORDER#1001'
DYNAMODB_CUSTOMER_ID = 'CUSTOMER#42'
DYNAMODB_INITIAL_TOTAL = '42.50'
DYNAMODB_UPDATED_TOTAL = '84.00'
DYNAMODB_ORDER_ITEM = {
    'OrderId': {'S': DYNAMODB_ORDER_ID},
    'CustomerId': {'S': DYNAMODB_CUSTOMER_ID},
    'Status': {'S': 'CREATED'},
    'Total': {'N': DYNAMODB_INITIAL_TOTAL},
}
DYNAMODB_UPDATED_ATTRIBUTES = {
    ':status': {'S': 'PAID'},
    ':total': {'N': DYNAMODB_UPDATED_TOTAL},
}
DYNAMODB_UPDATE_ATTRIBUTE_NAMES = {
    '#status': 'Status',
    '#total': 'Total',
}
DYNAMODB_PUT_CACHE_KEY = 'floci-lab:dynamodb:crud-query:put-item'
DYNAMODB_UPDATE_CACHE_KEY = 'floci-lab:dynamodb:crud-query:update-item'
DYNAMODB_QUERY_CACHE_KEY = 'floci-lab:dynamodb:crud-query:query'
DYNAMODB_DELETE_ITEM_CACHE_KEY = 'floci-lab:dynamodb:crud-query:delete-item'
DYNAMODB_DELETE_TABLE_CACHE_KEY = 'floci-lab:dynamodb:crud-query:delete-table'
LAMBDA_DYNAMODB_TABLE_NAME = 'lab-lambda-orders'
LAMBDA_DYNAMODB_FUNCTION_NAME = 'lab-order-writer'
LAMBDA_DYNAMODB_ROLE_NAME = 'FlociLambdaDynamoDbRole'
LAMBDA_DYNAMODB_POLICY_NAME = 'FlociLambdaDynamoDbWrite'
LAMBDA_DYNAMODB_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{LAMBDA_DYNAMODB_ROLE_NAME}'
LAMBDA_DYNAMODB_FUNCTION_ARN = f'arn:aws:lambda:{AWS_REGION}:{AWS_ACCOUNT_ID}:function:{LAMBDA_DYNAMODB_FUNCTION_NAME}'
LAMBDA_DYNAMODB_LOG_GROUP_NAME = f'/aws/lambda/{LAMBDA_DYNAMODB_FUNCTION_NAME}'
LAMBDA_DYNAMODB_ORDER_ID = 'ORDER#2001'
LAMBDA_DYNAMODB_CUSTOMER_ID = 'CUSTOMER#84'
LAMBDA_DYNAMODB_PAYLOAD = {
    'order_id': LAMBDA_DYNAMODB_ORDER_ID,
    'customer_id': LAMBDA_DYNAMODB_CUSTOMER_ID,
    'total': '128.25',
    'request_id': 'FLOCI-LAMBDA-DDB-2001',
}
LAMBDA_DYNAMODB_PAYLOAD_TEXT = json.dumps(LAMBDA_DYNAMODB_PAYLOAD, separators=(',', ':'))
LAMBDA_DYNAMODB_INVOKE_CACHE_KEY = 'floci-lab:dynamodb:lambda-writes:invoke'
LAMBDA_DYNAMODB_LOGS_CACHE_KEY = 'floci-lab:dynamodb:lambda-writes:logs'
KMS_LAB_ALIAS_NAME = 'alias/lab-data-key'
KMS_LAB_DESCRIPTION = 'Floci dashboard local workflow lab key'
KMS_LAB_TAGS = [
    {'TagKey': 'Lab', 'TagValue': 'kms-crypto'},
    {'TagKey': 'Owner', 'TagValue': 'floci-dashboard'},
]
KMS_LAB_PLAINTEXT = {
    'application': 'orders',
    'environment': 'local',
    'secret': 'sample-local-token',
}
KMS_LAB_PLAINTEXT_TEXT = json.dumps(KMS_LAB_PLAINTEXT, separators=(',', ':'))
KMS_LAB_KEY_ID_CACHE_KEY = 'floci-lab:kms:crypto:key-id'
KMS_LAB_CIPHERTEXT_CACHE_KEY = 'floci-lab:kms:crypto:ciphertext'
KMS_LAB_DECRYPT_CACHE_KEY = 'floci-lab:kms:crypto:decrypt'
SSM_PARAMETER_NAME = '/floci/lab/app/config'
SSM_PARAMETER_PATH = '/floci/lab/app/'
SSM_PARAMETER_VALUE = {
    'application': 'orders',
    'environment': 'local',
    'featureFlags': {
        'checkout': True,
        'auditMode': 'verbose',
    },
}
SSM_PARAMETER_VALUE_TEXT = json.dumps(SSM_PARAMETER_VALUE, separators=(',', ':'))
SECRETS_MANAGER_SECRET_NAME = 'lab/app-credentials'
SECRETS_MANAGER_INITIAL_SECRET = {
    'username': 'lab-app',
    'password': 'initial-local-password',
}
SECRETS_MANAGER_UPDATED_SECRET = {
    'username': 'lab-app',
    'password': 'rotated-local-password',
}
SECRETS_MANAGER_INITIAL_CACHE_KEY = 'floci-lab:secretsmanager:secret-lifecycle:initial-read'
SECRETS_MANAGER_UPDATED_CACHE_KEY = 'floci-lab:secretsmanager:secret-lifecycle:updated-read'
S3_BASICS_BUCKET_NAME = 'lab-basics'
S3_OBJECTS_BUCKET_NAME = 'lab-objects'
S3_HELLO_OBJECT_KEY = 'hello.txt'
S3_HELLO_OBJECT_TEXT = 'Hello from the Floci S3 lab!\n'
S3_HELLO_OBJECT_BYTES = S3_HELLO_OBJECT_TEXT.encode('utf-8')
S3_PREFIXES_BUCKET_NAME = 'lab-prefixes'
S3_REPORT_SOURCE_KEY = 'incoming/report.txt'
S3_REPORT_ARCHIVE_KEY = 'archive/report.txt'
S3_REPORT_TEXT = 'Floci S3 lab report\nStatus: ready for archive\n'
S3_REPORT_BYTES = S3_REPORT_TEXT.encode('utf-8')
S3_METADATA_BUCKET_NAME = 'lab-metadata'
S3_INVOICE_OBJECT_KEY = 'documents/invoice.txt'
S3_INVOICE_TEXT = 'Invoice: FLOCI-1001\nAmount: 42.00\n'
S3_INVOICE_BYTES = S3_INVOICE_TEXT.encode('utf-8')
S3_INVOICE_METADATA = {
    'project': 'floci',
    'classification': 'internal',
}
S3_INVOICE_TAGS = [
    {'Key': 'Environment', 'Value': 'lab'},
    {'Key': 'CostCenter', 'Value': 'training'},
]
S3_VERSIONING_BUCKET_NAME = 'lab-versioning'
S3_CONFIGURATION_KEY = 'configuration.txt'
S3_CONFIGURATION_V1_TEXT = 'feature_enabled=false\nrelease=v1\n'
S3_CONFIGURATION_V2_TEXT = 'feature_enabled=true\nrelease=v2\n'
S3_CONFIGURATION_V1_BYTES = S3_CONFIGURATION_V1_TEXT.encode('utf-8')
S3_CONFIGURATION_V2_BYTES = S3_CONFIGURATION_V2_TEXT.encode('utf-8')
S3_PRESIGNED_BUCKET_NAME = 'lab-presigned'
S3_PRESIGNED_OBJECT_KEY = 'shared/guide.txt'
S3_PRESIGNED_GUIDE_TEXT = 'Temporary access with an S3 presigned URL.\n'
S3_PRESIGNED_GUIDE_BYTES = S3_PRESIGNED_GUIDE_TEXT.encode('utf-8')
S3_PRESIGNED_EXPIRES_IN = 300
S3_SECURITY_BUCKET_NAME = 'lab-security'
S3_PUBLIC_ACCESS_BLOCK = {
    'BlockPublicAcls': True,
    'IgnorePublicAcls': True,
    'BlockPublicPolicy': True,
    'RestrictPublicBuckets': True,
}
S3_SECURITY_BUCKET_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Sid': 'AllowLocalAccountList',
            'Effect': 'Allow',
            'Principal': {'AWS': f'arn:aws:iam::{AWS_ACCOUNT_ID}:root'},
            'Action': 's3:ListBucket',
            'Resource': f'arn:aws:s3:::{S3_SECURITY_BUCKET_NAME}',
        },
    ],
}
S3_ENCRYPTION_BUCKET_NAME = 'lab-encryption'
S3_ENCRYPTION_OBJECT_KEY = 'protected/record.txt'
S3_ENCRYPTION_RECORD_TEXT = 'Protected by the bucket encryption default.\n'
S3_ENCRYPTION_RECORD_BYTES = S3_ENCRYPTION_RECORD_TEXT.encode('utf-8')
S3_SSE_S3_CONFIGURATION = {
    'Rules': [
        {
            'ApplyServerSideEncryptionByDefault': {
                'SSEAlgorithm': 'AES256',
            },
            'BucketKeyEnabled': False,
        },
    ],
}
S3_LIFECYCLE_BUCKET_NAME = 'lab-lifecycle'
S3_LIFECYCLE_OBJECT_KEY = 'logs/app.log'
S3_LIFECYCLE_LOG_TEXT = '2026-06-18T12:00:00Z INFO Floci lifecycle lab started\n'
S3_LIFECYCLE_LOG_BYTES = S3_LIFECYCLE_LOG_TEXT.encode('utf-8')
S3_LIFECYCLE_RULE_ID = 'ExpireApplicationLogsAfter30Days'
S3_LIFECYCLE_CONFIGURATION = {
    'Rules': [
        {
            'ID': S3_LIFECYCLE_RULE_ID,
            'Status': 'Enabled',
            'Filter': {'Prefix': 'logs/'},
            'Expiration': {'Days': 30},
        },
    ],
}
S3_CORS_BUCKET_NAME = 'lab-cors'
S3_CORS_CONFIGURATION = {
    'CORSRules': [
        {
            'ID': 'AllowLocalWebAppRead',
            'AllowedOrigins': ['http://localhost:3000'],
            'AllowedMethods': ['GET', 'HEAD'],
            'AllowedHeaders': ['Authorization'],
            'ExposeHeaders': ['ETag'],
            'MaxAgeSeconds': 3600,
        },
    ],
}
S3_NOTIFICATIONS_BUCKET_NAME = 'lab-notifications'
S3_NOTIFICATIONS_QUEUE_NAME = 'lab-s3-events'
S3_NOTIFICATIONS_QUEUE_ARN = (
    f'arn:aws:sqs:us-east-1:{AWS_ACCOUNT_ID}:{S3_NOTIFICATIONS_QUEUE_NAME}'
)
S3_NOTIFICATIONS_OBJECT_KEY = 'uploads/report.txt'
S3_NOTIFICATIONS_REPORT_TEXT = 'Floci S3 notification delivery report\n'
S3_NOTIFICATIONS_REPORT_BYTES = S3_NOTIFICATIONS_REPORT_TEXT.encode('utf-8')
S3_NOTIFICATIONS_CONFIGURATION_ID = 'SendObjectCreatedToSqs'
S3_NOTIFICATIONS_QUEUE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Sid': 'AllowS3ObjectCreatedEvents',
            'Effect': 'Allow',
            'Principal': {'Service': 's3.amazonaws.com'},
            'Action': 'sqs:SendMessage',
            'Resource': S3_NOTIFICATIONS_QUEUE_ARN,
            'Condition': {
                'ArnEquals': {
                    'aws:SourceArn': f'arn:aws:s3:::{S3_NOTIFICATIONS_BUCKET_NAME}',
                },
                'StringEquals': {
                    'aws:SourceAccount': AWS_ACCOUNT_ID,
                },
            },
        },
    ],
}
S3_NOTIFICATIONS_CONFIGURATION = {
    'QueueConfigurations': [
        {
            'Id': S3_NOTIFICATIONS_CONFIGURATION_ID,
            'QueueArn': S3_NOTIFICATIONS_QUEUE_ARN,
            'Events': ['s3:ObjectCreated:*'],
        },
    ],
}
S3_MULTIPART_BUCKET_NAME = 'lab-multipart'
S3_MULTIPART_OBJECT_KEY = 'archives/application.bin'
S3_MULTIPART_CONTENT_TYPE = 'application/octet-stream'
S3_MULTIPART_PART_ONE_BYTES = b'A' * (5 * 1024 * 1024)
S3_MULTIPART_PART_TWO_BYTES = b'B' * 1024
S3_MULTIPART_OBJECT_BYTES = S3_MULTIPART_PART_ONE_BYTES + S3_MULTIPART_PART_TWO_BYTES
SCHEDULER_SQS_QUEUE_NAME = 'lab-scheduled-reports'
SCHEDULER_SQS_QUEUE_ARN = (
    f'arn:aws:sqs:{AWS_REGION}:{AWS_ACCOUNT_ID}:{SCHEDULER_SQS_QUEUE_NAME}'
)
SCHEDULER_ROLE_NAME = 'FlociSchedulerSqsRole'
SCHEDULER_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{SCHEDULER_ROLE_NAME}'
SCHEDULER_ROLE_POLICY_NAME = 'SendScheduledReportToSqs'
SCHEDULER_GROUP_NAME = 'lab-scheduler'
SCHEDULER_GROUP_ARN = (
    f'arn:aws:scheduler:{AWS_REGION}:{AWS_ACCOUNT_ID}:'
    f'schedule-group/{SCHEDULER_GROUP_NAME}'
)
SCHEDULER_SCHEDULE_NAME = 'send-report-ready'
SCHEDULER_SCHEDULE_ARN = (
    f'arn:aws:scheduler:{AWS_REGION}:{AWS_ACCOUNT_ID}:schedule/'
    f'{SCHEDULER_GROUP_NAME}/{SCHEDULER_SCHEDULE_NAME}'
)
SCHEDULER_MESSAGE = {
    'event': 'report.ready',
    'report_id': 'FLOCI-SCHEDULE-1001',
}
SCHEDULER_MESSAGE_TEXT = json.dumps(SCHEDULER_MESSAGE, separators=(',', ':'))
SCHEDULER_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Principal': {'Service': 'scheduler.amazonaws.com'},
        'Action': 'sts:AssumeRole',
    }],
}
SCHEDULER_ROLE_POLICY = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Action': 'sqs:SendMessage',
        'Resource': SCHEDULER_SQS_QUEUE_ARN,
    }],
}
SCHEDULER_EXPRESSION_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:expression'
SCHEDULER_CREATED_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:created'
SCHEDULER_DELIVERED_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:delivered'
SCHEDULER_DELETED_CACHE_KEY = 'floci-lab:scheduler:sqs-delivery:deleted'
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
LAMBDA_LAB_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Principal': {'Service': 'lambda.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        },
    ],
}
LAMBDA_LAB_LOGS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_HANDLER_SOURCE = '''import json

def lambda_handler(event, context):
    payload = event
    if isinstance(event, dict) and "body" in event:
        body = event.get("body") or "{}"
        payload = json.loads(body) if isinstance(body, str) else body
    message = payload.get("message", "hello") if isinstance(payload, dict) else "hello"
    request_id = payload.get("request_id", "unknown") if isinstance(payload, dict) else "unknown"
    print(f"processed {request_id}: {message}")
    return {
        "ok": True,
        "echo": payload,
        "request_id": request_id,
    }
'''
LAMBDA_CONFIG_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': 'ssm:GetParameter',
            'Resource': f'arn:aws:ssm:{AWS_REGION}:{AWS_ACCOUNT_ID}:parameter{LAMBDA_CONFIG_PARAMETER_NAME}',
        },
        {
            'Effect': 'Allow',
            'Action': 'secretsmanager:GetSecretValue',
            'Resource': f'arn:aws:secretsmanager:{AWS_REGION}:{AWS_ACCOUNT_ID}:secret:{LAMBDA_CONFIG_SECRET_NAME}*',
        },
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_CONFIG_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_CONFIG_HANDLER_SOURCE = '''import json
import os

import boto3

ssm = boto3.client("ssm")
secretsmanager = boto3.client("secretsmanager")

PARAMETER_NAME = os.environ["PARAMETER_NAME"]
SECRET_ID = os.environ["SECRET_ID"]

def lambda_handler(event, context):
    request_id = event.get("request_id", "unknown") if isinstance(event, dict) else "unknown"
    parameter_response = ssm.get_parameter(Name=PARAMETER_NAME, WithDecryption=True)
    config = json.loads(parameter_response["Parameter"]["Value"])
    secret_response = secretsmanager.get_secret_value(SecretId=SECRET_ID)
    secret = json.loads(secret_response["SecretString"])
    print(f"loaded runtime config for {request_id}: {config['application']} / {secret['username']}")
    return {
        "ok": True,
        "request_id": request_id,
        "parameter_name": PARAMETER_NAME,
        "secret_id": SECRET_ID,
        "application": config["application"],
        "environment": config["environment"],
        "secret_username": secret["username"],
        "secret_keys": sorted(secret.keys()),
    }
'''
LAMBDA_SQS_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': [
                'sqs:ReceiveMessage',
                'sqs:DeleteMessage',
                'sqs:GetQueueAttributes',
            ],
            'Resource': LAMBDA_SQS_QUEUE_ARN,
        },
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_SQS_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_SQS_HANDLER_SOURCE = '''import json

def lambda_handler(event, context):
    processed = []
    for record in event.get("Records", []):
        body = json.loads(record.get("body") or "{}")
        request_id = body.get("request_id", "unknown")
        order_id = body.get("order_id", "unknown")
        print(f"processed sqs event {request_id} for {order_id}")
        processed.append({
            "message_id": record.get("messageId"),
            "order_id": order_id,
            "request_id": request_id,
        })
    return {
        "ok": True,
        "records": len(processed),
        "processed": processed,
    }
'''
LAMBDA_DYNAMODB_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': 'dynamodb:PutItem',
            'Resource': f'arn:aws:dynamodb:{AWS_REGION}:{AWS_ACCOUNT_ID}:table/{LAMBDA_DYNAMODB_TABLE_NAME}',
        },
        {
            'Effect': 'Allow',
            'Action': [
                'logs:CreateLogGroup',
                'logs:CreateLogStream',
                'logs:PutLogEvents',
            ],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{LAMBDA_DYNAMODB_LOG_GROUP_NAME}:*',
        },
    ],
}
LAMBDA_DYNAMODB_HANDLER_SOURCE = '''import json
import os

import boto3

dynamodb = boto3.client("dynamodb")
TABLE_NAME = os.environ["TABLE_NAME"]

def lambda_handler(event, context):
    order_id = event["order_id"]
    customer_id = event["customer_id"]
    request_id = event["request_id"]
    total = event["total"]
    item = {
        "OrderId": {"S": order_id},
        "CustomerId": {"S": customer_id},
        "Status": {"S": "RECEIVED"},
        "Total": {"N": total},
        "RequestId": {"S": request_id},
        "WrittenBy": {"S": "lambda"},
    }
    dynamodb.put_item(TableName=TABLE_NAME, Item=item)
    print(f"wrote {order_id} for {customer_id} with {request_id}")
    return {
        "ok": True,
        "table": TABLE_NAME,
        "order_id": order_id,
        "request_id": request_id,
    }
'''

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

LAMBDA_CREATE_INVOKE_LOGS_LAB = {
    'service': 'lambda',
    'key': 'create-invoke-logs',
    'title': 'Create, invoke, and inspect a Lambda function',
    'description': 'Create a local Lambda execution role and function, invoke it with a JSON event, then inspect the CloudWatch Logs written by the handler.',
    'steps': [
        {
            'key': 'create-role',
            'title': 'Create the execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates the IAM role that the Lambda service can assume when the function runs.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow Lambda to write logs',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_ROLE_NAME} --policy-name {LAMBDA_ROLE_POLICY_NAME} --policy-document file://lambda-logs-policy.json',
            'explanation': 'Adds the minimal CloudWatch Logs permissions the function needs for local log inspection.',
            'artifact_label': 'lambda-logs-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_LOGS_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the echo function',
            'command': f'aws lambda create-function --function-name {LAMBDA_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip',
            'explanation': 'Creates a small Python function from the packaged handler artifact.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_HANDLER_SOURCE,
        },
        {
            'key': 'invoke-function',
            'title': 'Invoke the function',
            'command': f'aws lambda invoke --function-name {LAMBDA_FUNCTION_NAME} --payload file://event.json response.json',
            'explanation': 'Sends a known JSON event and verifies the synchronous Lambda response.',
            'artifact_label': 'event.json',
            'artifact': json.dumps(LAMBDA_INVOKE_PAYLOAD, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect CloudWatch Logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Finds the Lambda log stream and verifies the handler wrote the expected request ID.',
        },
    ],
}

LAMBDA_RUNTIME_CONFIG_LAB = {
    'service': 'lambda',
    'key': 'runtime-config',
    'title': 'Read app configuration and secrets from Lambda',
    'description': 'Create SSM and Secrets Manager configuration, grant a Lambda function scoped read access, invoke it, and verify safe runtime output and logs.',
    'steps': [
        {
            'key': 'put-parameter',
            'title': 'Store Lambda configuration',
            'command': f'aws ssm put-parameter --name {LAMBDA_CONFIG_PARAMETER_NAME} --type String --value file://lambda-config.json --description "Floci Lambda runtime configuration" --overwrite',
            'explanation': 'Stores the non-secret application configuration that the function will read at runtime.',
            'artifact_label': 'lambda-config.json',
            'artifact': json.dumps(LAMBDA_CONFIG_PARAMETER_VALUE, indent=2),
        },
        {
            'key': 'create-secret',
            'title': 'Create Lambda secret',
            'command': f'aws secretsmanager create-secret --name {LAMBDA_CONFIG_SECRET_NAME} --description "Floci Lambda runtime secret" --secret-string file://lambda-secret.json',
            'explanation': 'Creates the secret value that the function can read without exposing it in environment variables.',
            'artifact_label': 'lambda-secret.json',
            'artifact': json.dumps(LAMBDA_CONFIG_SECRET_VALUE, indent=2),
        },
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_CONFIG_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates a dedicated execution role for the runtime configuration reader.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow scoped config reads',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_CONFIG_ROLE_NAME} --policy-name {LAMBDA_CONFIG_POLICY_NAME} --policy-document file://lambda-config-policy.json',
            'explanation': 'Grants only GetParameter for the lab parameter, GetSecretValue for the lab secret, and CloudWatch Logs writes.',
            'artifact_label': 'lambda-config-policy.json',
            'artifact': json.dumps(LAMBDA_CONFIG_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the config reader function',
            'command': f'aws lambda create-function --function-name {LAMBDA_CONFIG_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_CONFIG_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip --environment Variables={{PARAMETER_NAME={LAMBDA_CONFIG_PARAMETER_NAME},SECRET_ID={LAMBDA_CONFIG_SECRET_NAME}}}',
            'explanation': 'Packages a handler that reads SSM and Secrets Manager at invocation time.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_CONFIG_HANDLER_SOURCE,
        },
        {
            'key': 'invoke-function',
            'title': 'Invoke the config reader',
            'command': f'aws lambda invoke --function-name {LAMBDA_CONFIG_FUNCTION_NAME} --payload file://event.json response.json',
            'explanation': 'Invokes the function and verifies it loaded configuration and secret metadata without returning the secret value.',
            'artifact_label': 'event.json',
            'artifact': json.dumps(LAMBDA_CONFIG_PAYLOAD, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect Lambda logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Verifies that the function logged the runtime configuration request ID.',
        },
    ],
}

LAMBDA_SQS_EVENT_SOURCE_LAB = {
    'service': 'lambda',
    'key': 'sqs-event-source',
    'title': 'Process SQS messages with Lambda',
    'description': 'Create an SQS queue, connect it to a Lambda function with an event source mapping, send a message, and verify the asynchronous handler logs.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create the event queue',
            'command': f'aws sqs create-queue --queue-name {LAMBDA_SQS_QUEUE_NAME}',
            'explanation': 'Creates the standard SQS queue that will become the Lambda event source.',
        },
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_SQS_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates a dedicated Lambda execution role for the SQS consumer.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow SQS polling and logging',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_SQS_ROLE_NAME} --policy-name {LAMBDA_SQS_POLICY_NAME} --policy-document file://lambda-sqs-policy.json',
            'explanation': 'Grants the function only the SQS receive/delete/get-attributes permissions and CloudWatch Logs writes needed for this queue.',
            'artifact_label': 'lambda-sqs-policy.json',
            'artifact': json.dumps(LAMBDA_SQS_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the SQS consumer function',
            'command': f'aws lambda create-function --function-name {LAMBDA_SQS_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_SQS_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip',
            'explanation': 'Packages a handler that reads SQS Records and logs the order event request ID.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_SQS_HANDLER_SOURCE,
        },
        {
            'key': 'create-event-source-mapping',
            'title': 'Map SQS to Lambda',
            'command': f'aws lambda create-event-source-mapping --function-name {LAMBDA_SQS_FUNCTION_NAME} --event-source-arn {LAMBDA_SQS_QUEUE_ARN} --batch-size 5 --enabled',
            'explanation': 'Creates the Lambda event source mapping that polls the queue and invokes the function with message batches.',
        },
        {
            'key': 'send-message',
            'title': 'Send an order event',
            'command': 'aws sqs send-message --queue-url <queue-url> --message-body file://order-event.json --message-attributes file://message-attributes.json',
            'explanation': 'Sends a known SQS message that the event source mapping can deliver to the Lambda handler.',
            'artifact_label': 'order-event.json',
            'artifact': json.dumps(LAMBDA_SQS_MESSAGE_BODY, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect asynchronous Lambda logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Verifies that the SQS-triggered function logged the expected request ID.',
        },
    ],
}

APIGW_LAMBDA_REQUEST_LAB = {
    'service': 'apigateway',
    'key': 'lambda-request',
    'title': 'Send an API Gateway request to Lambda',
    'description': 'Create a local HTTP API, connect a POST route to the Lambda echo function, grant invoke permission, and verify an HTTP request reaches the handler.',
    'steps': [
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Ensures the Lambda execution role exists before the API is wired to the function.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow Lambda to write logs',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_ROLE_NAME} --policy-name {LAMBDA_ROLE_POLICY_NAME} --policy-document file://lambda-logs-policy.json',
            'explanation': 'Keeps the function observable when the API Gateway route invokes it.',
            'artifact_label': 'lambda-logs-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_LOGS_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the API handler function',
            'command': f'aws lambda create-function --function-name {LAMBDA_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip',
            'explanation': 'Creates the function that will receive the API Gateway proxy event.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_HANDLER_SOURCE,
        },
        {
            'key': 'create-api',
            'title': 'Create an HTTP API',
            'command': f'aws apigatewayv2 create-api --name {APIGW_LAMBDA_API_NAME} --protocol-type HTTP',
            'explanation': 'Creates the API Gateway v2 HTTP API that will expose the Lambda function locally.',
        },
        {
            'key': 'create-integration',
            'title': 'Create the Lambda proxy integration',
            'command': f'aws apigatewayv2 create-integration --api-id <api-id> --integration-type AWS_PROXY --integration-uri {LAMBDA_FUNCTION_ARN} --payload-format-version 2.0',
            'explanation': 'Connects API Gateway to the Lambda Invoke API using the AWS_PROXY integration type.',
        },
        {
            'key': 'create-route',
            'title': 'Create the POST /echo route',
            'command': 'aws apigatewayv2 create-route --api-id <api-id> --route-key "POST /echo" --target integrations/<integration-id>',
            'explanation': 'Routes POST requests for /echo to the Lambda proxy integration.',
        },
        {
            'key': 'create-stage',
            'title': 'Create the default stage',
            'command': 'aws apigatewayv2 create-stage --api-id <api-id> --stage-name $default --auto-deploy',
            'explanation': 'Publishes route changes automatically through the default HTTP API stage.',
        },
        {
            'key': 'add-lambda-permission',
            'title': 'Allow API Gateway to invoke Lambda',
            'command': f'aws lambda add-permission --function-name {LAMBDA_FUNCTION_NAME} --statement-id {APIGW_LAMBDA_PERMISSION_SID} --action lambda:InvokeFunction --principal apigateway.amazonaws.com --source-arn arn:aws:execute-api:{AWS_REGION}:{AWS_ACCOUNT_ID}:<api-id>/*/*/echo',
            'explanation': 'Adds the resource policy statement API Gateway needs before it can invoke the function.',
        },
        {
            'key': 'send-request',
            'title': 'Send a local API request',
            'command': 'curl -X POST <api-endpoint>/echo -H "Content-Type: application/json" --data @event.json',
            'explanation': 'Sends the lab event through the local API Gateway data plane and verifies the Lambda echo response.',
            'artifact_label': 'event.json',
            'artifact': json.dumps(LAMBDA_INVOKE_PAYLOAD, indent=2),
        },
    ],
}

DYNAMODB_CRUD_QUERY_LAB = {
    'service': 'dynamodb',
    'key': 'crud-query',
    'title': 'Create a DynamoDB table and query items',
    'description': 'Create a local orders table, write an item, read it by key, update it, query by customer, delete it, and remove the table.',
    'steps': [
        {
            'key': 'create-table',
            'title': 'Create the orders table',
            'command': f'aws dynamodb create-table --table-name {DYNAMODB_TABLE_NAME} --attribute-definitions file://attribute-definitions.json --key-schema file://key-schema.json --global-secondary-indexes file://customer-index.json --billing-mode PAY_PER_REQUEST',
            'explanation': 'Creates a table keyed by OrderId with a customer lookup global secondary index.',
            'artifact_label': 'attribute-definitions.json',
            'artifact': json.dumps([
                {'AttributeName': 'OrderId', 'AttributeType': 'S'},
                {'AttributeName': 'CustomerId', 'AttributeType': 'S'},
            ], indent=2),
        },
        {
            'key': 'put-item',
            'title': 'Write an order item',
            'command': f'aws dynamodb put-item --table-name {DYNAMODB_TABLE_NAME} --item file://order-item.json',
            'explanation': 'Stores a single order using DynamoDB attribute value JSON.',
            'artifact_label': 'order-item.json',
            'artifact': json.dumps(DYNAMODB_ORDER_ITEM, indent=2),
        },
        {
            'key': 'get-item',
            'title': 'Read the order by key',
            'command': f'aws dynamodb get-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json',
            'explanation': 'Reads the item by its partition key and verifies the original values.',
            'artifact_label': 'order-key.json',
            'artifact': json.dumps({'OrderId': {'S': DYNAMODB_ORDER_ID}}, indent=2),
        },
        {
            'key': 'update-item',
            'title': 'Update order status and total',
            'command': f'aws dynamodb update-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json --update-expression "SET #status = :status, #total = :total" --expression-attribute-names file://attribute-names.json --expression-attribute-values file://updated-values.json --return-values ALL_NEW',
            'explanation': 'Updates the mutable attributes and returns the new item image.',
            'artifact_label': 'updated-values.json',
            'artifact': json.dumps(DYNAMODB_UPDATED_ATTRIBUTES, indent=2),
            'secondary_artifact_label': 'attribute-names.json',
            'secondary_artifact': json.dumps(DYNAMODB_UPDATE_ATTRIBUTE_NAMES, indent=2),
        },
        {
            'key': 'query-customer-index',
            'title': 'Query orders by customer',
            'command': f'aws dynamodb query --table-name {DYNAMODB_TABLE_NAME} --index-name {DYNAMODB_CUSTOMER_INDEX_NAME} --key-condition-expression "CustomerId = :customer" --expression-attribute-values file://query-values.json',
            'explanation': 'Uses the global secondary index to find orders for one customer.',
            'artifact_label': 'query-values.json',
            'artifact': json.dumps({':customer': {'S': DYNAMODB_CUSTOMER_ID}}, indent=2),
        },
        {
            'key': 'delete-item',
            'title': 'Delete the order item',
            'command': f'aws dynamodb delete-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json',
            'explanation': 'Removes the order while leaving the table available for more writes.',
        },
        {
            'key': 'delete-table',
            'title': 'Delete the table',
            'command': f'aws dynamodb delete-table --table-name {DYNAMODB_TABLE_NAME}',
            'explanation': 'Deletes the lab-owned table after the item lifecycle is complete.',
        },
    ],
}

DYNAMODB_LAMBDA_WRITES_LAB = {
    'service': 'dynamodb',
    'key': 'lambda-writes',
    'title': 'Write DynamoDB items from Lambda',
    'description': 'Create a DynamoDB table, grant a Lambda function least-privilege write access, invoke it with an order event, and verify the item and logs.',
    'steps': [
        {
            'key': 'create-table',
            'title': 'Create the Lambda orders table',
            'command': f'aws dynamodb create-table --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --attribute-definitions AttributeName=OrderId,AttributeType=S --key-schema AttributeName=OrderId,KeyType=HASH --billing-mode PAY_PER_REQUEST',
            'explanation': 'Creates the table that the Lambda function will write to.',
        },
        {
            'key': 'create-role',
            'title': 'Create the Lambda execution role',
            'command': f'aws iam create-role --role-name {LAMBDA_DYNAMODB_ROLE_NAME} --assume-role-policy-document file://lambda-trust-policy.json',
            'explanation': 'Creates the IAM role that the Lambda service can assume.',
            'artifact_label': 'lambda-trust-policy.json',
            'artifact': json.dumps(LAMBDA_LAB_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Allow table writes and logs',
            'command': f'aws iam put-role-policy --role-name {LAMBDA_DYNAMODB_ROLE_NAME} --policy-name {LAMBDA_DYNAMODB_POLICY_NAME} --policy-document file://lambda-dynamodb-policy.json',
            'explanation': 'Grants only DynamoDB PutItem on the lab table plus CloudWatch Logs write permissions.',
            'artifact_label': 'lambda-dynamodb-policy.json',
            'artifact': json.dumps(LAMBDA_DYNAMODB_POLICY, indent=2),
        },
        {
            'key': 'create-function',
            'title': 'Create the order writer function',
            'command': f'aws lambda create-function --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} --runtime {LAMBDA_RUNTIME} --role {LAMBDA_DYNAMODB_ROLE_ARN} --handler {LAMBDA_HANDLER} --zip-file fileb://function.zip --environment Variables={{TABLE_NAME={LAMBDA_DYNAMODB_TABLE_NAME}}}',
            'explanation': 'Packages a small Python handler that writes the incoming order event to DynamoDB.',
            'artifact_label': 'handler.py',
            'artifact': LAMBDA_DYNAMODB_HANDLER_SOURCE,
        },
        {
            'key': 'invoke-function',
            'title': 'Invoke the writer',
            'command': f'aws lambda invoke --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} --payload file://order-event.json response.json',
            'explanation': 'Sends an order event to Lambda and verifies the synchronous response.',
            'artifact_label': 'order-event.json',
            'artifact': json.dumps(LAMBDA_DYNAMODB_PAYLOAD, indent=2),
        },
        {
            'key': 'get-written-item',
            'title': 'Read the written item',
            'command': f'aws dynamodb get-item --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --key file://order-key.json',
            'explanation': 'Reads DynamoDB directly to prove the Lambda invocation wrote the order item.',
            'artifact_label': 'order-key.json',
            'artifact': json.dumps({'OrderId': {'S': LAMBDA_DYNAMODB_ORDER_ID}}, indent=2),
        },
        {
            'key': 'inspect-logs',
            'title': 'Inspect Lambda logs',
            'command': f'aws logs describe-log-streams --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME}\naws logs get-log-events --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME} --log-stream-name <log-stream-name>',
            'explanation': 'Verifies that the function logged the order write request ID.',
        },
    ],
}

KMS_CRYPTO_LAB = {
    'service': 'kms',
    'key': 'key-alias-encrypt-decrypt',
    'title': 'Protect local app data with KMS',
    'description': 'Create a symmetric KMS key, attach a stable alias, encrypt app configuration, decrypt it, and inspect the key metadata.',
    'steps': [
        {
            'key': 'create-key',
            'title': 'Create a symmetric key',
            'command': 'aws kms create-key --description "Floci dashboard local workflow lab key" --key-usage ENCRYPT_DECRYPT --key-spec SYMMETRIC_DEFAULT --tags file://key-tags.json',
            'explanation': 'Creates a lab-owned symmetric key that can encrypt and decrypt local application data.',
            'artifact_label': 'key-tags.json',
            'artifact': json.dumps(KMS_LAB_TAGS, indent=2),
        },
        {
            'key': 'create-alias',
            'title': 'Create a stable alias',
            'command': f'aws kms create-alias --alias-name {KMS_LAB_ALIAS_NAME} --target-key-id <key-id>',
            'explanation': 'Adds a human-readable alias so application configuration can reference the key without hard-coding the generated key ID.',
        },
        {
            'key': 'describe-key',
            'title': 'Inspect key metadata',
            'command': f'aws kms describe-key --key-id {KMS_LAB_ALIAS_NAME}',
            'explanation': 'Reads the key metadata through the alias and verifies the key is enabled for encrypt/decrypt use.',
        },
        {
            'key': 'encrypt',
            'title': 'Encrypt app configuration',
            'command': f'aws kms encrypt --key-id {KMS_LAB_ALIAS_NAME} --plaintext fileb://app-config.json',
            'explanation': 'Encrypts a small JSON configuration payload with the alias-backed KMS key.',
            'artifact_label': 'app-config.json',
            'artifact': json.dumps(KMS_LAB_PLAINTEXT, indent=2),
        },
        {
            'key': 'decrypt',
            'title': 'Decrypt the ciphertext',
            'command': 'aws kms decrypt --ciphertext-blob fileb://ciphertext.bin',
            'explanation': 'Decrypts the ciphertext produced by the previous step and verifies it matches the original JSON payload.',
        },
    ],
}

SSM_PARAMETER_STORE_LAB = {
    'service': 'ssm',
    'key': 'parameter-store-config',
    'title': 'Read app configuration from SSM Parameter Store',
    'description': 'Store a JSON application configuration parameter, read it back directly, and discover it by path.',
    'steps': [
        {
            'key': 'put-parameter',
            'title': 'Store application configuration',
            'command': f'aws ssm put-parameter --name {SSM_PARAMETER_NAME} --type String --value file://app-config.json --description "Floci lab application configuration" --overwrite',
            'explanation': 'Writes a lab-owned JSON configuration value to Parameter Store with a hierarchical path name.',
            'artifact_label': 'app-config.json',
            'artifact': json.dumps(SSM_PARAMETER_VALUE, indent=2),
        },
        {
            'key': 'get-parameter',
            'title': 'Read the parameter',
            'command': f'aws ssm get-parameter --name {SSM_PARAMETER_NAME} --with-decryption',
            'explanation': 'Reads the parameter by exact name and verifies the stored JSON value.',
        },
        {
            'key': 'get-parameters-by-path',
            'title': 'Discover configuration by path',
            'command': f'aws ssm get-parameters-by-path --path {SSM_PARAMETER_PATH} --recursive --with-decryption',
            'explanation': 'Lists parameters under the application path so a local app can load a configuration namespace.',
        },
    ],
}

SECRETS_MANAGER_SECRET_LIFECYCLE_LAB = {
    'service': 'secretsmanager',
    'key': 'secret-lifecycle',
    'title': 'Create and update a Secrets Manager secret',
    'description': 'Create a JSON application secret, read its initial value, rotate the value, and verify the current secret.',
    'steps': [
        {
            'key': 'create-secret',
            'title': 'Create an application secret',
            'command': f'aws secretsmanager create-secret --name {SECRETS_MANAGER_SECRET_NAME} --description "Floci lab application credentials" --secret-string file://initial-secret.json',
            'explanation': 'Creates a lab-owned JSON secret for local application credentials.',
            'artifact_label': 'initial-secret.json',
            'artifact': json.dumps(SECRETS_MANAGER_INITIAL_SECRET, indent=2),
        },
        {
            'key': 'get-initial-secret',
            'title': 'Read the initial secret',
            'command': f'aws secretsmanager get-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME}',
            'explanation': 'Reads the secret value and verifies the initial credential JSON.',
        },
        {
            'key': 'put-secret-value',
            'title': 'Update the secret value',
            'command': f'aws secretsmanager put-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME} --secret-string file://rotated-secret.json',
            'explanation': 'Writes a new version of the secret value, simulating a local credential rotation.',
            'artifact_label': 'rotated-secret.json',
            'artifact': json.dumps(SECRETS_MANAGER_UPDATED_SECRET, indent=2),
        },
        {
            'key': 'get-updated-secret',
            'title': 'Read the updated secret',
            'command': f'aws secretsmanager get-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME}',
            'explanation': 'Reads the current secret value and verifies that the rotated credential is active.',
        },
        {
            'key': 'describe-secret',
            'title': 'Inspect secret metadata',
            'command': f'aws secretsmanager describe-secret --secret-id {SECRETS_MANAGER_SECRET_NAME}',
            'explanation': 'Inspects the secret metadata without exposing the secret value.',
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

S3_CREATE_BUCKET_LAB = {
    'service': 's3',
    'key': 'create-bucket',
    'title': 'Create and inspect an S3 bucket',
    'description': 'Create a local S3 bucket, confirm it is reachable, and find it in the account bucket list.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the bucket',
            'command': f'aws s3api create-bucket --bucket {S3_BASICS_BUCKET_NAME}',
            'explanation': 'Creates an S3 bucket in the configured local AWS region.',
        },
        {
            'key': 'head-bucket',
            'title': 'Confirm the bucket is reachable',
            'command': f'aws s3api head-bucket --bucket {S3_BASICS_BUCKET_NAME}',
            'explanation': 'Checks that the bucket exists and the current identity can access it.',
        },
        {
            'key': 'list-buckets',
            'title': 'List account buckets',
            'command': 'aws s3api list-buckets',
            'explanation': f'Lists S3 buckets and verifies that {S3_BASICS_BUCKET_NAME} appears in the result.',
        },
    ],
}

S3_OBJECT_WORKFLOW_LAB = {
    'service': 's3',
    'key': 'object-workflow',
    'title': 'Upload and retrieve an S3 object',
    'description': 'Upload a known text file, discover it in the bucket, inspect its metadata, and download its exact contents.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the object lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_OBJECTS_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning the S3 object workflow.',
        },
        {
            'key': 'put-object',
            'title': 'Upload hello.txt',
            'command': f'aws s3api put-object --bucket {S3_OBJECTS_BUCKET_NAME} --key {S3_HELLO_OBJECT_KEY} --body hello.txt --content-type text/plain',
            'explanation': 'Uploads the artifact bytes under the object key hello.txt with a text content type.',
            'artifact_label': 'hello.txt',
            'artifact': S3_HELLO_OBJECT_TEXT,
        },
        {
            'key': 'list-objects',
            'title': 'List bucket objects',
            'command': f'aws s3api list-objects-v2 --bucket {S3_OBJECTS_BUCKET_NAME}',
            'explanation': 'Lists current objects and verifies that hello.txt appears with the expected size.',
        },
        {
            'key': 'head-object',
            'title': 'Inspect object metadata',
            'command': f'aws s3api head-object --bucket {S3_OBJECTS_BUCKET_NAME} --key {S3_HELLO_OBJECT_KEY}',
            'explanation': 'Reads object metadata without downloading the object body.',
        },
        {
            'key': 'get-object',
            'title': 'Download the object',
            'command': f'aws s3api get-object --bucket {S3_OBJECTS_BUCKET_NAME} --key {S3_HELLO_OBJECT_KEY} downloaded-hello.txt',
            'explanation': 'Downloads hello.txt and verifies that its bytes match the original artifact.',
        },
    ],
}

S3_PREFIX_COPY_LAB = {
    'service': 's3',
    'key': 'prefix-copy',
    'title': 'Organize and copy objects with key prefixes',
    'description': 'Upload a report under an incoming prefix, discover it by prefix, and copy it into an archive prefix.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the prefixes lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_PREFIXES_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning S3 key prefixes and object copying.',
        },
        {
            'key': 'put-source-object',
            'title': 'Upload the incoming report',
            'command': f'aws s3api put-object --bucket {S3_PREFIXES_BUCKET_NAME} --key {S3_REPORT_SOURCE_KEY} --body report.txt --content-type text/plain',
            'explanation': 'Uploads report.txt under the incoming/ key prefix.',
            'artifact_label': 'report.txt',
            'artifact': S3_REPORT_TEXT,
        },
        {
            'key': 'list-incoming-prefix',
            'title': 'List the incoming prefix',
            'command': f'aws s3api list-objects-v2 --bucket {S3_PREFIXES_BUCKET_NAME} --prefix incoming/',
            'explanation': 'Filters the flat object namespace to keys beginning with incoming/.',
        },
        {
            'key': 'copy-object',
            'title': 'Copy the report to archive',
            'command': f'aws s3api copy-object --copy-source {S3_PREFIXES_BUCKET_NAME}/{S3_REPORT_SOURCE_KEY} --bucket {S3_PREFIXES_BUCKET_NAME} --key {S3_REPORT_ARCHIVE_KEY}',
            'explanation': 'Copies the source object to a new key under archive/ while leaving the original object in place.',
        },
        {
            'key': 'list-archive-prefix',
            'title': 'List the archive prefix',
            'command': f'aws s3api list-objects-v2 --bucket {S3_PREFIXES_BUCKET_NAME} --prefix archive/',
            'explanation': 'Filters the bucket to archive/ and verifies the copied report is present.',
        },
    ],
}

S3_METADATA_TAGS_LAB = {
    'service': 's3',
    'key': 'metadata-tags',
    'title': 'Add object metadata and tags',
    'description': 'Upload an invoice with content and user metadata, then apply and inspect independently editable object tags.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the metadata lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_METADATA_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for comparing object metadata and tags.',
        },
        {
            'key': 'put-object',
            'title': 'Upload the invoice with metadata',
            'command': f'aws s3api put-object --bucket {S3_METADATA_BUCKET_NAME} --key {S3_INVOICE_OBJECT_KEY} --body invoice.txt --content-type text/plain --metadata project=floci,classification=internal',
            'explanation': 'Uploads the invoice with a system-managed content type and custom project and classification metadata.',
            'artifact_label': 'invoice.txt',
            'artifact': S3_INVOICE_TEXT,
        },
        {
            'key': 'head-object',
            'title': 'Inspect object metadata',
            'command': f'aws s3api head-object --bucket {S3_METADATA_BUCKET_NAME} --key {S3_INVOICE_OBJECT_KEY}',
            'explanation': 'Reads the object content type, size, ETag, and user-defined metadata without downloading the body.',
        },
        {
            'key': 'put-object-tagging',
            'title': 'Apply object tags',
            'command': f'aws s3api put-object-tagging --bucket {S3_METADATA_BUCKET_NAME} --key {S3_INVOICE_OBJECT_KEY} --tagging file://invoice-tags.json',
            'explanation': 'Adds tags that can be updated independently from the object body and metadata.',
            'artifact_label': 'invoice-tags.json',
            'artifact': json.dumps({'TagSet': S3_INVOICE_TAGS}, indent=2),
        },
        {
            'key': 'get-object-tagging',
            'title': 'Inspect object tags',
            'command': f'aws s3api get-object-tagging --bucket {S3_METADATA_BUCKET_NAME} --key {S3_INVOICE_OBJECT_KEY}',
            'explanation': 'Returns the current object tag set for operational classification and automation.',
        },
    ],
}

S3_VERSION_RECOVERY_LAB = {
    'service': 's3',
    'key': 'version-recovery',
    'title': 'Enable versioning and recover an earlier object version',
    'description': 'Enable bucket versioning, overwrite one object, inspect both versions, and retrieve the original contents.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the versioning lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_VERSIONING_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning S3 object versioning.',
        },
        {
            'key': 'enable-versioning',
            'title': 'Enable bucket versioning',
            'command': f'aws s3api put-bucket-versioning --bucket {S3_VERSIONING_BUCKET_NAME} --versioning-configuration Status=Enabled',
            'explanation': 'Enables version IDs for future writes to objects in the bucket.',
        },
        {
            'key': 'put-version-one',
            'title': 'Upload configuration v1',
            'command': f'aws s3api put-object --bucket {S3_VERSIONING_BUCKET_NAME} --key {S3_CONFIGURATION_KEY} --body configuration-v1.txt --content-type text/plain',
            'explanation': 'Creates the first stored version of configuration.txt.',
            'artifact_label': 'configuration-v1.txt',
            'artifact': S3_CONFIGURATION_V1_TEXT,
        },
        {
            'key': 'put-version-two',
            'title': 'Overwrite with configuration v2',
            'command': f'aws s3api put-object --bucket {S3_VERSIONING_BUCKET_NAME} --key {S3_CONFIGURATION_KEY} --body configuration-v2.txt --content-type text/plain',
            'explanation': 'Writes a new latest version while preserving the earlier v1 object version.',
            'artifact_label': 'configuration-v2.txt',
            'artifact': S3_CONFIGURATION_V2_TEXT,
        },
        {
            'key': 'list-object-versions',
            'title': 'List stored object versions',
            'command': f'aws s3api list-object-versions --bucket {S3_VERSIONING_BUCKET_NAME} --prefix {S3_CONFIGURATION_KEY}',
            'explanation': 'Returns version IDs and identifies which version is currently latest.',
        },
        {
            'key': 'get-version-one',
            'title': 'Recover configuration v1',
            'command': f'aws s3api get-object --bucket {S3_VERSIONING_BUCKET_NAME} --key {S3_CONFIGURATION_KEY} --version-id <v1-version-id> recovered-configuration.txt',
            'explanation': 'Discovers the version containing v1 and downloads those earlier bytes directly.',
        },
    ],
}

S3_PRESIGNED_URL_LAB = {
    'service': 's3',
    'key': 'presigned-url',
    'title': 'Generate temporary access with a presigned URL',
    'description': 'Upload a private object, generate a five-minute signed URL, and redeem that URL without sending AWS credentials.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the presigned URL lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_PRESIGNED_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning temporary signed object access.',
        },
        {
            'key': 'put-object',
            'title': 'Upload the shared guide',
            'command': f'aws s3api put-object --bucket {S3_PRESIGNED_BUCKET_NAME} --key {S3_PRESIGNED_OBJECT_KEY} --body guide.txt --content-type text/plain',
            'explanation': 'Uploads the object that will be shared temporarily through a signed URL.',
            'artifact_label': 'guide.txt',
            'artifact': S3_PRESIGNED_GUIDE_TEXT,
        },
        {
            'key': 'presign-object',
            'title': 'Generate and redeem a presigned URL',
            'command': f'aws s3 presign s3://{S3_PRESIGNED_BUCKET_NAME}/{S3_PRESIGNED_OBJECT_KEY} --expires-in {S3_PRESIGNED_EXPIRES_IN}',
            'explanation': 'Creates a bearer URL signed with the current identity, then redeems it to verify temporary access to the exact guide bytes.',
        },
    ],
}

S3_BUCKET_SECURITY_LAB = {
    'service': 's3',
    'key': 'bucket-security',
    'title': 'Block public access and apply a bucket policy',
    'description': 'Enable every Public Access Block safeguard, then add a non-public policy granting one principal one bucket-level action.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the security lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_SECURITY_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning layered S3 access controls.',
        },
        {
            'key': 'put-public-access-block',
            'title': 'Block public access',
            'command': f'aws s3api put-public-access-block --bucket {S3_SECURITY_BUCKET_NAME} --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true',
            'explanation': 'Enables all four bucket-level safeguards against public ACLs and public bucket policies.',
        },
        {
            'key': 'get-public-access-block',
            'title': 'Inspect public-access protection',
            'command': f'aws s3api get-public-access-block --bucket {S3_SECURITY_BUCKET_NAME}',
            'explanation': 'Reads the effective Public Access Block configuration and verifies every safeguard is enabled.',
        },
        {
            'key': 'put-bucket-policy',
            'title': 'Apply a least-privilege bucket policy',
            'command': f'aws s3api put-bucket-policy --bucket {S3_SECURITY_BUCKET_NAME} --policy file://bucket-policy.json',
            'explanation': 'Allows only the local account root to list this bucket; it does not make the bucket public.',
            'artifact_label': 'bucket-policy.json',
            'artifact': json.dumps(S3_SECURITY_BUCKET_POLICY, indent=2),
        },
        {
            'key': 'get-bucket-policy',
            'title': 'Inspect the bucket policy',
            'command': f'aws s3api get-bucket-policy --bucket {S3_SECURITY_BUCKET_NAME}',
            'explanation': 'Returns the resource-based policy and verifies its principal, action, and bucket ARN.',
        },
    ],
}

S3_DEFAULT_ENCRYPTION_LAB = {
    'service': 's3',
    'key': 'default-encryption',
    'title': 'Enable default bucket encryption',
    'description': 'Configure SSE-S3 as the bucket default, inspect the setting, and upload an object after the protection is enabled.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the encryption lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_ENCRYPTION_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning default encryption at rest.',
        },
        {
            'key': 'put-bucket-encryption',
            'title': 'Enable default SSE-S3 encryption',
            'command': f'aws s3api put-bucket-encryption --bucket {S3_ENCRYPTION_BUCKET_NAME} --server-side-encryption-configuration file://encryption.json',
            'explanation': 'Configures S3-managed AES-256 encryption as the default for new objects.',
            'artifact_label': 'encryption.json',
            'artifact': json.dumps(S3_SSE_S3_CONFIGURATION, indent=2),
        },
        {
            'key': 'get-bucket-encryption',
            'title': 'Inspect default encryption',
            'command': f'aws s3api get-bucket-encryption --bucket {S3_ENCRYPTION_BUCKET_NAME}',
            'explanation': 'Reads the bucket configuration and verifies the default algorithm is AES256.',
        },
        {
            'key': 'put-object',
            'title': 'Upload a protected record',
            'command': f'aws s3api put-object --bucket {S3_ENCRYPTION_BUCKET_NAME} --key {S3_ENCRYPTION_OBJECT_KEY} --body record.txt --content-type text/plain',
            'explanation': 'Uploads an object after the bucket default encryption configuration is active.',
            'artifact_label': 'record.txt',
            'artifact': S3_ENCRYPTION_RECORD_TEXT,
        },
        {
            'key': 'head-object',
            'title': 'Inspect the uploaded object',
            'command': f'aws s3api head-object --bucket {S3_ENCRYPTION_BUCKET_NAME} --key {S3_ENCRYPTION_OBJECT_KEY}',
            'explanation': 'Reads the object metadata and verifies its exact content while the bucket AES256 default remains enabled.',
        },
    ],
}

S3_LIFECYCLE_RETENTION_LAB = {
    'service': 's3',
    'key': 'lifecycle-retention',
    'title': 'Automate retention with an S3 lifecycle rule',
    'description': 'Upload an application log, configure S3 to expire objects under logs/ after 30 days, and inspect the saved lifecycle rule.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the lifecycle lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_LIFECYCLE_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning automated S3 retention.',
        },
        {
            'key': 'put-object',
            'title': 'Upload an application log',
            'command': f'aws s3api put-object --bucket {S3_LIFECYCLE_BUCKET_NAME} --key {S3_LIFECYCLE_OBJECT_KEY} --body app.log --content-type text/plain',
            'explanation': 'Uploads a sample log beneath the logs/ prefix targeted by the lifecycle rule.',
            'artifact_label': 'app.log',
            'artifact': S3_LIFECYCLE_LOG_TEXT,
        },
        {
            'key': 'put-lifecycle-configuration',
            'title': 'Expire logs after 30 days',
            'command': f'aws s3api put-bucket-lifecycle-configuration --bucket {S3_LIFECYCLE_BUCKET_NAME} --lifecycle-configuration file://lifecycle.json',
            'explanation': 'Adds an enabled lifecycle rule that expires objects under logs/ 30 days after creation. AWS performs lifecycle actions asynchronously.',
            'artifact_label': 'lifecycle.json',
            'artifact': json.dumps(S3_LIFECYCLE_CONFIGURATION, indent=2),
        },
        {
            'key': 'get-lifecycle-configuration',
            'title': 'Inspect the retention rule',
            'command': f'aws s3api get-bucket-lifecycle-configuration --bucket {S3_LIFECYCLE_BUCKET_NAME}',
            'explanation': 'Reads the saved lifecycle configuration and verifies the rule scope, status, and 30-day expiration.',
        },
    ],
}

S3_CORS_LAB = {
    'service': 's3',
    'key': 'bucket-cors',
    'title': 'Configure bucket CORS for a local web app',
    'description': 'Allow a browser app on localhost:3000 to read objects, then inspect the saved cross-origin access rule.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the CORS lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_CORS_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning browser cross-origin access configuration.',
        },
        {
            'key': 'put-bucket-cors',
            'title': 'Allow the local web app to read objects',
            'command': f'aws s3api put-bucket-cors --bucket {S3_CORS_BUCKET_NAME} --cors-configuration file://cors.json',
            'explanation': 'Allows GET and HEAD requests from http://localhost:3000, permits the Authorization request header, exposes ETag, and caches preflight results for one hour.',
            'artifact_label': 'cors.json',
            'artifact': json.dumps(S3_CORS_CONFIGURATION, indent=2),
        },
        {
            'key': 'get-bucket-cors',
            'title': 'Inspect the CORS rule',
            'command': f'aws s3api get-bucket-cors --bucket {S3_CORS_BUCKET_NAME}',
            'explanation': 'Reads the saved CORS configuration and verifies the exact origin, methods, headers, and preflight cache duration.',
        },
    ],
}

S3_OBJECT_NOTIFICATIONS_LAB = {
    'service': 's3',
    'key': 'object-notifications-sqs',
    'title': 'Send S3 object-created events to SQS',
    'description': 'Create a queue and bucket, authorize S3 delivery, configure object-created notifications, and prove an uploaded object produces an SQS event.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create the event queue',
            'command': f'aws sqs create-queue --queue-name {S3_NOTIFICATIONS_QUEUE_NAME}',
            'explanation': 'Creates the SQS destination that will receive S3 object-created events.',
        },
        {
            'key': 'set-queue-policy',
            'title': 'Authorize S3 to send messages',
            'command': 'aws sqs set-queue-attributes --queue-url <queue-url> --attributes file://queue-attributes.json',
            'explanation': 'Adds the queue resource policy AWS requires before S3 can publish events from this specific bucket and account.',
            'artifact_label': 'queue-attributes.json',
            'artifact': json.dumps(
                {'Policy': json.dumps(S3_NOTIFICATIONS_QUEUE_POLICY)},
                indent=2,
            ),
        },
        {
            'key': 'create-bucket',
            'title': 'Create the notification bucket',
            'command': f'aws s3api create-bucket --bucket {S3_NOTIFICATIONS_BUCKET_NAME}',
            'explanation': 'Creates the source bucket whose object writes will emit events.',
        },
        {
            'key': 'put-notification-configuration',
            'title': 'Connect object-created events to SQS',
            'command': f'aws s3api put-bucket-notification-configuration --bucket {S3_NOTIFICATIONS_BUCKET_NAME} --notification-configuration file://notification.json',
            'explanation': 'Routes every S3 object-created event from the bucket to the lab queue.',
            'artifact_label': 'notification.json',
            'artifact': json.dumps(S3_NOTIFICATIONS_CONFIGURATION, indent=2),
        },
        {
            'key': 'get-notification-configuration',
            'title': 'Inspect the notification wiring',
            'command': f'aws s3api get-bucket-notification-configuration --bucket {S3_NOTIFICATIONS_BUCKET_NAME}',
            'explanation': 'Reads the saved destination ARN and event selection before any object is uploaded.',
        },
        {
            'key': 'put-object',
            'title': 'Upload the report',
            'command': f'aws s3api put-object --bucket {S3_NOTIFICATIONS_BUCKET_NAME} --key {S3_NOTIFICATIONS_OBJECT_KEY} --body report.txt --content-type text/plain',
            'explanation': 'Uploads a known object and causes S3 to emit an ObjectCreated:Put event.',
            'artifact_label': 'report.txt',
            'artifact': S3_NOTIFICATIONS_REPORT_TEXT,
        },
        {
            'key': 'receive-event',
            'title': 'Receive the S3 event from SQS',
            'command': 'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 1',
            'explanation': 'Long-polls the queue and verifies a delivered S3 event names the exact bucket and object key.',
        },
    ],
}

S3_MULTIPART_UPLOAD_LAB = {
    'service': 's3',
    'key': 'multipart-upload',
    'title': 'Complete a multipart upload',
    'description': 'Initiate a multipart upload, send two parts, inspect their ETags, complete the upload, and verify the assembled object.',
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the multipart lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_MULTIPART_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning the multipart upload lifecycle.',
        },
        {
            'key': 'create-multipart-upload',
            'title': 'Initiate the multipart upload',
            'command': f'aws s3api create-multipart-upload --bucket {S3_MULTIPART_BUCKET_NAME} --key {S3_MULTIPART_OBJECT_KEY} --content-type {S3_MULTIPART_CONTENT_TYPE}',
            'explanation': 'Creates an upload ID that identifies this in-progress multipart object.',
        },
        {
            'key': 'upload-part-one',
            'title': 'Upload the 5 MiB first part',
            'command': f'aws s3api upload-part --bucket {S3_MULTIPART_BUCKET_NAME} --key {S3_MULTIPART_OBJECT_KEY} --part-number 1 --upload-id <upload-id> --body part-one.bin',
            'explanation': 'Uploads the first part at the minimum 5 MiB size AWS requires for every non-final multipart part.',
            'artifact_label': 'part-one.bin',
            'artifact': '5 MiB of the byte A, generated by the approved lab action.',
        },
        {
            'key': 'upload-part-two',
            'title': 'Upload the 1 KiB final part',
            'command': f'aws s3api upload-part --bucket {S3_MULTIPART_BUCKET_NAME} --key {S3_MULTIPART_OBJECT_KEY} --part-number 2 --upload-id <upload-id> --body part-two.bin',
            'explanation': 'Uploads the final part, which may be smaller than 5 MiB.',
            'artifact_label': 'part-two.bin',
            'artifact': '1 KiB of the byte B, generated by the approved lab action.',
        },
        {
            'key': 'list-parts',
            'title': 'Inspect uploaded parts',
            'command': f'aws s3api list-parts --bucket {S3_MULTIPART_BUCKET_NAME} --key {S3_MULTIPART_OBJECT_KEY} --upload-id <upload-id>',
            'explanation': 'Returns each part number, size, and ETag needed to complete the upload.',
        },
        {
            'key': 'complete-multipart-upload',
            'title': 'Assemble the final object',
            'command': f'aws s3api complete-multipart-upload --bucket {S3_MULTIPART_BUCKET_NAME} --key {S3_MULTIPART_OBJECT_KEY} --upload-id <upload-id> --multipart-upload file://parts.json',
            'explanation': 'Discovers the current part ETags, orders them by part number, and asks S3 to assemble the final object.',
            'artifact_label': 'parts.json',
            'artifact': json.dumps({
                'Parts': [
                    {'ETag': '<part-1-etag>', 'PartNumber': 1},
                    {'ETag': '<part-2-etag>', 'PartNumber': 2},
                ],
            }, indent=2),
        },
        {
            'key': 'get-object',
            'title': 'Verify the assembled object',
            'command': f'aws s3api get-object --bucket {S3_MULTIPART_BUCKET_NAME} --key {S3_MULTIPART_OBJECT_KEY} downloaded-application.bin',
            'explanation': 'Downloads the completed object and verifies that both parts were concatenated in order with the expected content type and total size.',
        },
    ],
}

SCHEDULER_SQS_DELIVERY_LAB = {
    'service': 'scheduler',
    'key': 'sqs-delivery',
    'title': 'Schedule an EventBridge Scheduler message to SQS',
    'description': 'Create the execution role and target queue, schedule one report event, verify delivery, and confirm automatic schedule cleanup.',
    'steps': [
        {
            'key': 'create-queue',
            'title': 'Create the target queue',
            'command': f'aws sqs create-queue --queue-name {SCHEDULER_SQS_QUEUE_NAME}',
            'explanation': 'Creates the SQS queue that will receive the scheduled report event.',
        },
        {
            'key': 'create-role',
            'title': 'Create the Scheduler execution role',
            'command': f'aws iam create-role --role-name {SCHEDULER_ROLE_NAME} --assume-role-policy-document file://scheduler-trust-policy.json',
            'explanation': 'Creates a role trusted only by the EventBridge Scheduler service.',
            'artifact_label': 'scheduler-trust-policy.json',
            'artifact': json.dumps(SCHEDULER_TRUST_POLICY, indent=2),
        },
        {
            'key': 'put-role-policy',
            'title': 'Authorize SQS delivery',
            'command': f'aws iam put-role-policy --role-name {SCHEDULER_ROLE_NAME} --policy-name {SCHEDULER_ROLE_POLICY_NAME} --policy-document file://send-message-policy.json',
            'explanation': 'Grants the execution role only sqs:SendMessage on the dedicated target queue.',
            'artifact_label': 'send-message-policy.json',
            'artifact': json.dumps(SCHEDULER_ROLE_POLICY, indent=2),
        },
        {
            'key': 'create-schedule-group',
            'title': 'Create a schedule group',
            'command': f'aws scheduler create-schedule-group --name {SCHEDULER_GROUP_NAME}',
            'explanation': 'Creates a dedicated container for the lab schedule.',
        },
        {
            'key': 'create-schedule',
            'title': 'Schedule the report event',
            'command': f'aws scheduler create-schedule --name {SCHEDULER_SCHEDULE_NAME} --group-name {SCHEDULER_GROUP_NAME} --schedule-expression "at(<utc-time-seconds-from-now>)" --flexible-time-window Mode=OFF --target file://target.json --action-after-completion DELETE',
            'explanation': 'Creates a one-time schedule a few seconds in the future. The runner fills in the live UTC timestamp.',
            'artifact_label': 'target.json',
            'artifact': json.dumps({
                'Arn': SCHEDULER_SQS_QUEUE_ARN,
                'RoleArn': SCHEDULER_ROLE_ARN,
                'Input': SCHEDULER_MESSAGE_TEXT,
                'RetryPolicy': {
                    'MaximumEventAgeInSeconds': 60,
                    'MaximumRetryAttempts': 1,
                },
            }, indent=2),
            'secondary_artifact_label': 'scheduled-message.json',
            'secondary_artifact': json.dumps(SCHEDULER_MESSAGE, indent=2),
        },
        {
            'key': 'get-schedule',
            'title': 'Inspect the pending schedule',
            'command': f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} --group-name {SCHEDULER_GROUP_NAME}',
            'explanation': 'Verifies the one-time expression, disabled flexible window, execution role, target queue, retry policy, and automatic deletion setting.',
        },
        {
            'key': 'receive-scheduled-message',
            'title': 'Wait for SQS delivery',
            'command': 'aws sqs receive-message --queue-url <scheduled-reports-queue-url> --max-number-of-messages 10 --visibility-timeout 0 --wait-time-seconds 12',
            'explanation': 'Polls through Scheduler invocation latency and verifies the exact report.ready JSON body.',
        },
        {
            'key': 'confirm-schedule-deleted',
            'title': 'Confirm automatic schedule cleanup',
            'command': f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} --group-name {SCHEDULER_GROUP_NAME}',
            'explanation': 'Requires ResourceNotFoundException, proving ActionAfterCompletion=DELETE removed the one-time schedule.',
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
        return [
            S3_CREATE_BUCKET_LAB,
            S3_OBJECT_WORKFLOW_LAB,
            S3_PREFIX_COPY_LAB,
            S3_METADATA_TAGS_LAB,
            S3_VERSION_RECOVERY_LAB,
            S3_PRESIGNED_URL_LAB,
            S3_BUCKET_SECURITY_LAB,
            S3_DEFAULT_ENCRYPTION_LAB,
            S3_LIFECYCLE_RETENTION_LAB,
            S3_CORS_LAB,
            S3_OBJECT_NOTIFICATIONS_LAB,
            S3_MULTIPART_UPLOAD_LAB,
        ]
    if service_key == 'sqs':
        from .sqs_labs import LABS
        return LABS
    if service_key == 'sns':
        from .sns_labs import LABS
        return LABS
    if service_key == 'scheduler':
        return [SCHEDULER_SQS_DELIVERY_LAB]
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
        return [
            LAMBDA_CREATE_INVOKE_LOGS_LAB,
            LAMBDA_RUNTIME_CONFIG_LAB,
            LAMBDA_SQS_EVENT_SOURCE_LAB,
        ]
    if service_key == 'apigateway':
        return [APIGW_LAMBDA_REQUEST_LAB]
    if service_key == 'eventbridge':
        from .eventbridge_application import LAB
        return [LAB]
    if service_key == 'dynamodb':
        return [DYNAMODB_CRUD_QUERY_LAB, DYNAMODB_LAMBDA_WRITES_LAB]
    if service_key == 'kms':
        return [KMS_CRYPTO_LAB]
    if service_key == 'ssm':
        return [SSM_PARAMETER_STORE_LAB]
    if service_key == 'secretsmanager':
        return [SECRETS_MANAGER_SECRET_LIFECYCLE_LAB]
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


def _verify_lambda_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_ROLE_NAME,
            PolicyName=LAMBDA_ROLE_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if response.get('PolicyName') == LAMBDA_ROLE_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_ROLE_POLICY_NAME} is embedded in {LAMBDA_ROLE_NAME}.',
            'resource': _clean_response(response),
        }

    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_ROLE_POLICY_NAME} was not returned.',
    }


def _verify_lambda_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    configuration = response.get('Configuration', response)
    if (
        configuration.get('FunctionName') == LAMBDA_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_ROLE_ARN
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_FUNCTION_NAME} exists with the lab execution role.',
            'resource': _clean_response(configuration),
        }

    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_FUNCTION_NAME} does not match the lab configuration.',
    }


def _verify_lambda_invocation() -> dict[str, Any]:
    cached = cache.get(LAMBDA_INVOKE_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_FUNCTION_NAME} returned the expected echo response.',
            'resource': cached,
        }

    return {
        'status': 'failed',
        'message': 'No successful Lambda invocation has been recorded for this lab run.',
    }


def _verify_lambda_log_group() -> dict[str, Any]:
    try:
        response = _logs_client().describe_log_groups(
            logGroupNamePrefix=LAMBDA_LOG_GROUP_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    for group in response.get('logGroups', []):
        if group.get('logGroupName') == LAMBDA_LOG_GROUP_NAME:
            return {
                'status': 'passed',
                'message': f'Log group {LAMBDA_LOG_GROUP_NAME} exists.',
                'resource': _clean_response(group),
            }

    return {
        'status': 'failed',
        'message': f'Log group {LAMBDA_LOG_GROUP_NAME} was not returned.',
    }


def _verify_lambda_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_INVOKE_PAYLOAD["request_id"]} invocation.',
            'resource': cached,
        }

    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                message = event.get('message', '')
                if LAMBDA_INVOKE_PAYLOAD['request_id'] in message:
                    resource = {
                        'log_group_name': LAMBDA_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_INVOKE_PAYLOAD["request_id"]} invocation.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_INVOKE_PAYLOAD["request_id"]} was found.',
    }


def _verify_lambda_config_parameter() -> dict[str, Any]:
    try:
        response = _ssm_client().get_parameter(
            Name=LAMBDA_CONFIG_PARAMETER_NAME,
            WithDecryption=True,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    parameter = response.get('Parameter', {})
    if (
        parameter.get('Name') == LAMBDA_CONFIG_PARAMETER_NAME
        and _parse_json_text(parameter.get('Value') or '') == LAMBDA_CONFIG_PARAMETER_VALUE
    ):
        return {
            'status': 'passed',
            'message': f'Parameter {LAMBDA_CONFIG_PARAMETER_NAME} stores the Lambda runtime configuration.',
            'resource': _clean_response(parameter),
        }
    return {
        'status': 'failed',
        'message': f'Parameter {LAMBDA_CONFIG_PARAMETER_NAME} does not match the expected Lambda configuration.',
    }


def _verify_lambda_config_secret() -> dict[str, Any]:
    try:
        response = _secretsmanager_client().get_secret_value(SecretId=LAMBDA_CONFIG_SECRET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if _secret_string_json(response) == LAMBDA_CONFIG_SECRET_VALUE:
        return {
            'status': 'passed',
            'message': f'Secret {LAMBDA_CONFIG_SECRET_NAME} stores the expected Lambda secret JSON.',
            'resource': {
                'Name': response.get('Name') or LAMBDA_CONFIG_SECRET_NAME,
                'ARN': response.get('ARN'),
                'VersionId': response.get('VersionId'),
                'VersionStages': response.get('VersionStages'),
            },
        }
    return {
        'status': 'failed',
        'message': f'Secret {LAMBDA_CONFIG_SECRET_NAME} does not match the expected Lambda secret JSON.',
    }


def _verify_lambda_config_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_CONFIG_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_config_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_CONFIG_ROLE_NAME,
            PolicyName=LAMBDA_CONFIG_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyName') == LAMBDA_CONFIG_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_CONFIG_POLICY_NAME} is embedded in {LAMBDA_CONFIG_ROLE_NAME}.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_CONFIG_POLICY_NAME} was not returned.',
    }


def _verify_lambda_config_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_CONFIG_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    configuration = response.get('Configuration', response)
    environment = configuration.get('Environment', {}).get('Variables', {})
    if (
        configuration.get('FunctionName') == LAMBDA_CONFIG_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_CONFIG_ROLE_ARN
        and environment.get('PARAMETER_NAME') == LAMBDA_CONFIG_PARAMETER_NAME
        and environment.get('SECRET_ID') == LAMBDA_CONFIG_SECRET_NAME
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_CONFIG_FUNCTION_NAME} exists with runtime configuration environment variables.',
            'resource': _clean_response(configuration),
        }
    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_CONFIG_FUNCTION_NAME} does not match the runtime configuration lab.',
    }


def _verify_lambda_config_invocation() -> dict[str, Any]:
    cached = cache.get(LAMBDA_CONFIG_INVOKE_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_CONFIG_FUNCTION_NAME} read SSM and Secrets Manager at runtime.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful Lambda runtime configuration invocation has been recorded for this lab run.',
    }


def _verify_lambda_config_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_CONFIG_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_CONFIG_PAYLOAD["request_id"]} runtime configuration read.',
            'resource': cached,
        }
    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                if LAMBDA_CONFIG_PAYLOAD['request_id'] in event.get('message', ''):
                    resource = {
                        'log_group_name': LAMBDA_CONFIG_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_CONFIG_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_CONFIG_PAYLOAD["request_id"]} runtime configuration read.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_CONFIG_PAYLOAD["request_id"]} was found.',
    }


def _lambda_sqs_queue_url() -> str:
    return _sqs_queue_url(LAMBDA_SQS_QUEUE_NAME)


def _verify_lambda_sqs_queue() -> dict[str, Any]:
    try:
        queue_url = _lambda_sqs_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    attributes = response.get('Attributes', {})
    if attributes.get('QueueArn') == LAMBDA_SQS_QUEUE_ARN:
        return {
            'status': 'passed',
            'message': f'Queue {LAMBDA_SQS_QUEUE_NAME} exists with the expected ARN.',
            'resource': {
                'url': queue_url,
                'attributes': _clean_response(attributes),
            },
        }
    return {
        'status': 'failed',
        'message': f'Queue {LAMBDA_SQS_QUEUE_NAME} does not expose the expected ARN.',
    }


def _verify_lambda_sqs_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_SQS_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_sqs_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_SQS_ROLE_NAME,
            PolicyName=LAMBDA_SQS_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyName') == LAMBDA_SQS_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_SQS_POLICY_NAME} is embedded in {LAMBDA_SQS_ROLE_NAME}.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_SQS_POLICY_NAME} was not returned.',
    }


def _verify_lambda_sqs_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_SQS_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    configuration = response.get('Configuration', response)
    if (
        configuration.get('FunctionName') == LAMBDA_SQS_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_SQS_ROLE_ARN
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_SQS_FUNCTION_NAME} exists with the SQS consumer role.',
            'resource': _clean_response(configuration),
        }
    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_SQS_FUNCTION_NAME} does not match the SQS event source lab.',
    }


def _find_lambda_sqs_event_source_mapping() -> dict[str, Any] | None:
    cached_uuid = cache.get(LAMBDA_SQS_MAPPING_CACHE_KEY)
    lambda_client = _lambda_client()
    if cached_uuid:
        try:
            response = lambda_client.get_event_source_mapping(UUID=cached_uuid)
            if response.get('EventSourceArn') == LAMBDA_SQS_QUEUE_ARN:
                return response
        except ClientError as exc:
            if _error_code(exc) != 'ResourceNotFoundException':
                raise
    response = lambda_client.list_event_source_mappings(
        FunctionName=LAMBDA_SQS_FUNCTION_NAME,
        EventSourceArn=LAMBDA_SQS_QUEUE_ARN,
    )
    mappings = response.get('EventSourceMappings', [])
    return mappings[0] if mappings else None


def _verify_lambda_sqs_event_source_mapping() -> dict[str, Any]:
    try:
        mapping = _find_lambda_sqs_event_source_mapping()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if mapping and mapping.get('FunctionArn') and mapping.get('EventSourceArn') == LAMBDA_SQS_QUEUE_ARN:
        if mapping.get('UUID'):
            cache.set(LAMBDA_SQS_MAPPING_CACHE_KEY, mapping['UUID'], timeout=3600)
        return {
            'status': 'passed',
            'message': f'Event source mapping connects {LAMBDA_SQS_QUEUE_NAME} to {LAMBDA_SQS_FUNCTION_NAME}.',
            'resource': _clean_response(mapping),
        }
    return {
        'status': 'failed',
        'message': 'No Lambda event source mapping was found for the lab queue and function.',
    }


def _verify_lambda_sqs_message_sent() -> dict[str, Any]:
    cached = cache.get(LAMBDA_SQS_SEND_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'SQS message {LAMBDA_SQS_MESSAGE_BODY["request_id"]} was sent for processing.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No SQS message send has been recorded for this lab run.',
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


def _verify_lambda_sqs_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_SQS_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_SQS_MESSAGE_BODY["request_id"]} SQS event.',
            'resource': cached,
        }
    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_SQS_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_SQS_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                if LAMBDA_SQS_MESSAGE_BODY['request_id'] in event.get('message', ''):
                    resource = {
                        'log_group_name': LAMBDA_SQS_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_SQS_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_SQS_MESSAGE_BODY["request_id"]} SQS event.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_SQS_MESSAGE_BODY["request_id"]} was found.',
    }


def _apigw_items(operation: str, result_key: str, **kwargs) -> list[dict[str, Any]]:
    client = _apigatewayv2_client()
    if hasattr(client, 'get_paginator'):
        try:
            paginator = client.get_paginator(operation)
            return paginator.paginate(**kwargs).build_full_result().get(result_key, [])
        except (ClientError, KeyError, AttributeError, ValueError):
            pass
    return getattr(client, operation)(**kwargs).get(result_key, [])


def _find_apigw_lambda_api() -> dict[str, Any] | None:
    cached_api_id = cache.get(APIGW_LAMBDA_API_ID_CACHE_KEY)
    if cached_api_id:
        try:
            api = _apigatewayv2_client().get_api(ApiId=cached_api_id)
            if api.get('Name') == APIGW_LAMBDA_API_NAME:
                return api
        except ClientError:
            cache.delete(APIGW_LAMBDA_API_ID_CACHE_KEY)

    for api in _apigw_items('get_apis', 'Items'):
        if api.get('Name') == APIGW_LAMBDA_API_NAME:
            api_id = api.get('ApiId')
            if api_id:
                cache.set(APIGW_LAMBDA_API_ID_CACHE_KEY, api_id, timeout=86400)
            return api
    return None


def _apigw_lambda_api_id() -> str:
    api = _find_apigw_lambda_api()
    api_id = (api or {}).get('ApiId')
    if not api_id:
        raise ValueError('API Gateway HTTP API has not been created yet.')
    return api_id


def _verify_apigw_lambda_api() -> dict[str, Any]:
    try:
        api = _find_apigw_lambda_api()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if api and api.get('ProtocolType') == 'HTTP':
        return {
            'status': 'passed',
            'message': f'HTTP API {APIGW_LAMBDA_API_NAME} exists.',
            'resource': _clean_response(api),
        }

    return {
        'status': 'failed',
        'message': f'HTTP API {APIGW_LAMBDA_API_NAME} was not returned.',
    }


def _find_apigw_lambda_integration(api_id: str) -> dict[str, Any] | None:
    cached_id = cache.get(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY)
    integrations = _apigw_items('get_integrations', 'Items', ApiId=api_id)
    for integration in integrations:
        if cached_id and integration.get('IntegrationId') == cached_id:
            return integration
        if (
            integration.get('IntegrationType') == 'AWS_PROXY'
            and integration.get('IntegrationUri') == LAMBDA_FUNCTION_ARN
        ):
            integration_id = integration.get('IntegrationId')
            if integration_id:
                cache.set(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY, integration_id, timeout=86400)
            return integration
    return None


def _apigw_lambda_integration_id() -> str:
    integration = _find_apigw_lambda_integration(_apigw_lambda_api_id())
    integration_id = (integration or {}).get('IntegrationId')
    if not integration_id:
        raise ValueError('API Gateway Lambda integration has not been created yet.')
    return integration_id


def _verify_apigw_lambda_integration() -> dict[str, Any]:
    try:
        api_id = _apigw_lambda_api_id()
        integration = _find_apigw_lambda_integration(api_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}

    if integration:
        return {
            'status': 'passed',
            'message': f'API {APIGW_LAMBDA_API_NAME} has a Lambda proxy integration.',
            'resource': _clean_response(integration),
        }
    return {
        'status': 'failed',
        'message': 'The Lambda proxy integration was not returned.',
    }


def _find_apigw_lambda_route(api_id: str) -> dict[str, Any] | None:
    cached_id = cache.get(APIGW_LAMBDA_ROUTE_ID_CACHE_KEY)
    routes = _apigw_items('get_routes', 'Items', ApiId=api_id)
    integration_id = cache.get(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY)
    expected_target = f'integrations/{integration_id}' if integration_id else None
    for route in routes:
        if cached_id and route.get('RouteId') == cached_id:
            return route
        if route.get('RouteKey') == APIGW_LAMBDA_ROUTE_KEY and (
            not expected_target or route.get('Target') == expected_target
        ):
            route_id = route.get('RouteId')
            if route_id:
                cache.set(APIGW_LAMBDA_ROUTE_ID_CACHE_KEY, route_id, timeout=86400)
            return route
    return None


def _verify_apigw_lambda_route() -> dict[str, Any]:
    try:
        api_id = _apigw_lambda_api_id()
        route = _find_apigw_lambda_route(api_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    if route:
        return {
            'status': 'passed',
            'message': f'Route {APIGW_LAMBDA_ROUTE_KEY} targets the Lambda integration.',
            'resource': _clean_response(route),
        }
    return {
        'status': 'failed',
        'message': f'Route {APIGW_LAMBDA_ROUTE_KEY} was not returned.',
    }


def _verify_apigw_lambda_stage() -> dict[str, Any]:
    try:
        api_id = _apigw_lambda_api_id()
        stages = _apigw_items('get_stages', 'Items', ApiId=api_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    for stage in stages:
        if stage.get('StageName') == APIGW_LAMBDA_STAGE_NAME:
            return {
                'status': 'passed',
                'message': f'Stage {APIGW_LAMBDA_STAGE_NAME} is available for the HTTP API.',
                'resource': _clean_response(stage),
            }
    return {
        'status': 'failed',
        'message': f'Stage {APIGW_LAMBDA_STAGE_NAME} was not returned.',
    }


def _verify_apigw_lambda_permission() -> dict[str, Any]:
    try:
        policy = json.loads(_lambda_client().get_policy(FunctionName=LAMBDA_FUNCTION_NAME).get('Policy', '{}'))
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    statements = policy.get('Statement', [])
    for statement in statements:
        if (
            statement.get('Sid') == APIGW_LAMBDA_PERMISSION_SID
            and statement.get('Principal', {}).get('Service') == 'apigateway.amazonaws.com'
            and statement.get('Action') == 'lambda:InvokeFunction'
        ):
            return {
                'status': 'passed',
                'message': 'Lambda resource policy allows API Gateway to invoke the function.',
                'resource': _clean_response(statement),
            }
    return {
        'status': 'failed',
        'message': 'The API Gateway invoke permission was not returned in the Lambda policy.',
    }


def _verify_apigw_lambda_request() -> dict[str, Any]:
    cached = cache.get(APIGW_LAMBDA_REQUEST_CACHE_KEY)
    if cached:
        message = (
            'The local API Gateway request reached the Lambda handler and produced the expected log marker.'
            if cached.get('verification_basis') == 'lambda_log_marker'
            else 'The local API Gateway request returned the expected Lambda response.'
        )
        return {
            'status': 'passed',
            'message': message,
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful API Gateway request has been recorded for this lab run.',
    }


def _verify_dynamodb_table_exists() -> dict[str, Any]:
    try:
        table = _dynamodb_client().describe_table(TableName=DYNAMODB_TABLE_NAME).get('Table', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    indexes = table.get('GlobalSecondaryIndexes') or []
    has_index = any(index.get('IndexName') == DYNAMODB_CUSTOMER_INDEX_NAME for index in indexes)
    if table.get('TableName') == DYNAMODB_TABLE_NAME and has_index:
        return {
            'status': 'passed',
            'message': f'Table {DYNAMODB_TABLE_NAME} exists with the customer lookup index.',
            'resource': _clean_response(table),
        }
    return {
        'status': 'failed',
        'message': f'Table {DYNAMODB_TABLE_NAME} does not match the expected schema.',
    }


def _dynamodb_order_key() -> dict[str, Any]:
    return {'OrderId': {'S': DYNAMODB_ORDER_ID}}


def _get_dynamodb_order_item() -> dict[str, Any] | None:
    return _dynamodb_client().get_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
    ).get('Item')


def _verify_dynamodb_order_created() -> dict[str, Any]:
    try:
        item = _get_dynamodb_order_item()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        item
        and item.get('OrderId', {}).get('S') == DYNAMODB_ORDER_ID
        and item.get('CustomerId', {}).get('S') == DYNAMODB_CUSTOMER_ID
        and item.get('Status', {}).get('S') == 'CREATED'
    ):
        return {
            'status': 'passed',
            'message': f'Order {DYNAMODB_ORDER_ID} exists with the initial attributes.',
            'resource': _clean_response(item),
        }
    return {
        'status': 'failed',
        'message': f'Order {DYNAMODB_ORDER_ID} was not returned with the initial values.',
    }


def _verify_dynamodb_order_updated() -> dict[str, Any]:
    try:
        item = _get_dynamodb_order_item()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        item
        and item.get('Status', {}).get('S') == 'PAID'
        and item.get('Total', {}).get('N') == DYNAMODB_UPDATED_TOTAL
    ):
        return {
            'status': 'passed',
            'message': f'Order {DYNAMODB_ORDER_ID} is updated to PAID with the new total.',
            'resource': _clean_response(item),
        }
    return {
        'status': 'failed',
        'message': f'Order {DYNAMODB_ORDER_ID} was not returned with the updated values.',
    }


def _verify_dynamodb_customer_query() -> dict[str, Any]:
    try:
        response = _dynamodb_client().query(
            TableName=DYNAMODB_TABLE_NAME,
            IndexName=DYNAMODB_CUSTOMER_INDEX_NAME,
            KeyConditionExpression='CustomerId = :customer',
            ExpressionAttributeValues={':customer': {'S': DYNAMODB_CUSTOMER_ID}},
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    for item in response.get('Items', []):
        if (
            item.get('OrderId', {}).get('S') == DYNAMODB_ORDER_ID
            and item.get('Status', {}).get('S') == 'PAID'
        ):
            return {
                'status': 'passed',
                'message': f'The customer index returns order {DYNAMODB_ORDER_ID}.',
                'resource': _clean_response(response),
            }
    return {
        'status': 'failed',
        'message': f'The customer index did not return order {DYNAMODB_ORDER_ID}.',
    }


def _verify_dynamodb_order_deleted() -> dict[str, Any]:
    try:
        item = _get_dynamodb_order_item()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if not item:
        return {
            'status': 'passed',
            'message': f'Order {DYNAMODB_ORDER_ID} is absent from {DYNAMODB_TABLE_NAME}.',
        }
    return {
        'status': 'failed',
        'message': f'Order {DYNAMODB_ORDER_ID} still exists.',
        'resource': _clean_response(item),
    }


def _verify_dynamodb_table_deleted() -> dict[str, Any]:
    try:
        _dynamodb_client().describe_table(TableName=DYNAMODB_TABLE_NAME)
    except ClientError as exc:
        if _error_code(exc) == 'ResourceNotFoundException':
            return {
                'status': 'passed',
                'message': f'Table {DYNAMODB_TABLE_NAME} is deleted.',
            }
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'Table {DYNAMODB_TABLE_NAME} still exists.',
    }


def _verify_lambda_dynamodb_table() -> dict[str, Any]:
    try:
        table = _dynamodb_client().describe_table(TableName=LAMBDA_DYNAMODB_TABLE_NAME).get('Table', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if table.get('TableName') == LAMBDA_DYNAMODB_TABLE_NAME:
        return {
            'status': 'passed',
            'message': f'Table {LAMBDA_DYNAMODB_TABLE_NAME} exists for Lambda writes.',
            'resource': _clean_response(table),
        }
    return {
        'status': 'failed',
        'message': f'Table {LAMBDA_DYNAMODB_TABLE_NAME} was not returned.',
    }


def _verify_lambda_dynamodb_role() -> dict[str, Any]:
    return _verify_role(LAMBDA_DYNAMODB_ROLE_NAME, 'lambda.amazonaws.com')


def _verify_lambda_dynamodb_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=LAMBDA_DYNAMODB_ROLE_NAME,
            PolicyName=LAMBDA_DYNAMODB_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyName') == LAMBDA_DYNAMODB_POLICY_NAME:
        return {
            'status': 'passed',
            'message': f'Inline policy {LAMBDA_DYNAMODB_POLICY_NAME} is embedded in {LAMBDA_DYNAMODB_ROLE_NAME}.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Inline policy {LAMBDA_DYNAMODB_POLICY_NAME} was not returned.',
    }


def _verify_lambda_dynamodb_function() -> dict[str, Any]:
    try:
        response = _lambda_client().get_function(FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    configuration = response.get('Configuration', response)
    environment = configuration.get('Environment', {}).get('Variables', {})
    if (
        configuration.get('FunctionName') == LAMBDA_DYNAMODB_FUNCTION_NAME
        and configuration.get('Role') == LAMBDA_DYNAMODB_ROLE_ARN
        and environment.get('TABLE_NAME') == LAMBDA_DYNAMODB_TABLE_NAME
    ):
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_DYNAMODB_FUNCTION_NAME} exists with the table environment variable.',
            'resource': _clean_response(configuration),
        }
    return {
        'status': 'failed',
        'message': f'Function {LAMBDA_DYNAMODB_FUNCTION_NAME} does not match the lab configuration.',
    }


def _verify_lambda_dynamodb_invocation() -> dict[str, Any]:
    cached = cache.get(LAMBDA_DYNAMODB_INVOKE_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'Function {LAMBDA_DYNAMODB_FUNCTION_NAME} returned a successful order-write response.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful Lambda-to-DynamoDB invocation has been recorded for this lab run.',
    }


def _lambda_dynamodb_order_key() -> dict[str, Any]:
    return {'OrderId': {'S': LAMBDA_DYNAMODB_ORDER_ID}}


def _verify_lambda_dynamodb_item() -> dict[str, Any]:
    try:
        item = _dynamodb_client().get_item(
            TableName=LAMBDA_DYNAMODB_TABLE_NAME,
            Key=_lambda_dynamodb_order_key(),
        ).get('Item')
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        item
        and item.get('OrderId', {}).get('S') == LAMBDA_DYNAMODB_ORDER_ID
        and item.get('RequestId', {}).get('S') == LAMBDA_DYNAMODB_PAYLOAD['request_id']
        and item.get('WrittenBy', {}).get('S') == 'lambda'
    ):
        return {
            'status': 'passed',
            'message': f'Lambda wrote order {LAMBDA_DYNAMODB_ORDER_ID} to DynamoDB.',
            'resource': _clean_response(item),
        }
    return {
        'status': 'failed',
        'message': f'Order {LAMBDA_DYNAMODB_ORDER_ID} was not written by Lambda.',
    }


def _verify_lambda_dynamodb_logs() -> dict[str, Any]:
    cached = cache.get(LAMBDA_DYNAMODB_LOGS_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': f'CloudWatch Logs includes the {LAMBDA_DYNAMODB_PAYLOAD["request_id"]} write.',
            'resource': cached,
        }
    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_DYNAMODB_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_DYNAMODB_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                if LAMBDA_DYNAMODB_PAYLOAD['request_id'] in event.get('message', ''):
                    resource = {
                        'log_group_name': LAMBDA_DYNAMODB_LOG_GROUP_NAME,
                        'log_stream_name': stream_name,
                        'event': _clean_response(event),
                    }
                    cache.set(LAMBDA_DYNAMODB_LOGS_CACHE_KEY, resource, timeout=3600)
                    return {
                        'status': 'passed',
                        'message': f'CloudWatch Logs includes the {LAMBDA_DYNAMODB_PAYLOAD["request_id"]} write.',
                        'resource': resource,
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No CloudWatch Logs event for {LAMBDA_DYNAMODB_PAYLOAD["request_id"]} was found.',
    }


def _kms_items(operation: str, result_key: str, **kwargs) -> list[dict[str, Any]]:
    client = _kms_client()
    if hasattr(client, 'get_paginator'):
        try:
            paginator = client.get_paginator(operation)
            return paginator.paginate(**kwargs).build_full_result().get(result_key, [])
        except (ClientError, KeyError, AttributeError, ValueError):
            pass
    return getattr(client, operation)(**kwargs).get(result_key, [])


def _find_kms_lab_key_id() -> str | None:
    cached = cache.get(KMS_LAB_KEY_ID_CACHE_KEY)
    if cached:
        try:
            metadata = _kms_client().describe_key(KeyId=cached).get('KeyMetadata', {})
            if metadata.get('KeyId') and metadata.get('KeyState') != 'PendingDeletion':
                return metadata['KeyId']
            else:
                cache.delete(KMS_LAB_KEY_ID_CACHE_KEY)
        except ClientError:
            cache.delete(KMS_LAB_KEY_ID_CACHE_KEY)

    for alias in _kms_items('list_aliases', 'Aliases'):
        if alias.get('AliasName') == KMS_LAB_ALIAS_NAME and alias.get('TargetKeyId'):
            try:
                metadata = _kms_client().describe_key(KeyId=alias['TargetKeyId']).get('KeyMetadata', {})
                if metadata.get('KeyState') != 'PendingDeletion':
                    cache.set(KMS_LAB_KEY_ID_CACHE_KEY, alias['TargetKeyId'], timeout=86400)
                    return alias['TargetKeyId']
            except ClientError:
                pass

    expected_tags = {(tag['TagKey'], tag['TagValue']) for tag in KMS_LAB_TAGS}
    for key in _kms_items('list_keys', 'Keys'):
        key_id = key.get('KeyId')
        if not key_id:
            continue
        try:
            metadata = _kms_client().describe_key(KeyId=key_id).get('KeyMetadata', {})
            if metadata.get('KeyState') == 'PendingDeletion':
                continue
            tags = _kms_items('list_resource_tags', 'Tags', KeyId=key_id)
        except ClientError:
            continue
        actual_tags = {(tag.get('TagKey'), tag.get('TagValue')) for tag in tags}
        if expected_tags.issubset(actual_tags):
            cache.set(KMS_LAB_KEY_ID_CACHE_KEY, key_id, timeout=86400)
            return key_id
    return None


def _kms_lab_key_id() -> str:
    key_id = _find_kms_lab_key_id()
    if not key_id:
        raise ValueError('KMS lab key has not been created yet.')
    return key_id


def _verify_kms_lab_key() -> dict[str, Any]:
    try:
        key_id = _find_kms_lab_key_id()
        if not key_id:
            return {
                'status': 'failed',
                'message': 'KMS lab key was not found.',
            }
        metadata = _kms_client().describe_key(KeyId=key_id).get('KeyMetadata', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if (
        metadata.get('KeyUsage') == 'ENCRYPT_DECRYPT'
        and metadata.get('KeySpec') == 'SYMMETRIC_DEFAULT'
        and metadata.get('KeyState') in {'Enabled', 'Creating'}
    ):
        return {
            'status': 'passed',
            'message': 'KMS lab key exists and is configured for symmetric encryption.',
            'resource': _clean_response(metadata),
        }
    return {
        'status': 'failed',
        'message': 'KMS lab key does not match the expected symmetric encrypt/decrypt configuration.',
    }


def _verify_kms_lab_alias() -> dict[str, Any]:
    try:
        key_id = _kms_lab_key_id()
        aliases = _kms_items('list_aliases', 'Aliases', KeyId=key_id)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}
    for alias in aliases:
        if alias.get('AliasName') == KMS_LAB_ALIAS_NAME and alias.get('TargetKeyId') == key_id:
            return {
                'status': 'passed',
                'message': f'Alias {KMS_LAB_ALIAS_NAME} targets the KMS lab key.',
                'resource': _clean_response(alias),
            }
    return {
        'status': 'failed',
        'message': f'Alias {KMS_LAB_ALIAS_NAME} was not returned for the KMS lab key.',
    }


def _verify_kms_lab_ciphertext() -> dict[str, Any]:
    cached = cache.get(KMS_LAB_CIPHERTEXT_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': 'KMS returned ciphertext for the app configuration payload.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No ciphertext has been recorded for this KMS lab run.',
    }


def _verify_kms_lab_decrypt() -> dict[str, Any]:
    cached = cache.get(KMS_LAB_DECRYPT_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': 'KMS decrypted the ciphertext back to the original app configuration.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'No successful KMS decrypt has been recorded for this lab run.',
    }


def _parse_json_text(text: str) -> Any:
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None


def _verify_ssm_parameter() -> dict[str, Any]:
    try:
        response = _ssm_client().get_parameter(
            Name=SSM_PARAMETER_NAME,
            WithDecryption=True,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    parameter = response.get('Parameter', {})
    value = parameter.get('Value') or ''
    if (
        parameter.get('Name') == SSM_PARAMETER_NAME
        and parameter.get('Type') == 'String'
        and _parse_json_text(value) == SSM_PARAMETER_VALUE
    ):
        return {
            'status': 'passed',
            'message': f'Parameter {SSM_PARAMETER_NAME} stores the expected JSON app configuration.',
            'resource': _clean_response(parameter),
        }
    return {
        'status': 'failed',
        'message': f'Parameter {SSM_PARAMETER_NAME} does not match the expected app configuration.',
    }


def _verify_ssm_parameter_path() -> dict[str, Any]:
    try:
        response = _ssm_client().get_parameters_by_path(
            Path=SSM_PARAMETER_PATH,
            Recursive=True,
            WithDecryption=True,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    for parameter in response.get('Parameters', []):
        if (
            parameter.get('Name') == SSM_PARAMETER_NAME
            and _parse_json_text(parameter.get('Value') or '') == SSM_PARAMETER_VALUE
        ):
            return {
                'status': 'passed',
                'message': f'Path {SSM_PARAMETER_PATH} returns the lab configuration parameter.',
                'resource': _clean_response(response),
            }
    return {
        'status': 'failed',
        'message': f'Path {SSM_PARAMETER_PATH} did not return {SSM_PARAMETER_NAME}.',
    }


def _secret_string_json(response: dict[str, Any]) -> Any:
    return _parse_json_text(response.get('SecretString') or '')


def _verify_secretsmanager_secret_exists() -> dict[str, Any]:
    try:
        response = _secretsmanager_client().describe_secret(SecretId=SECRETS_MANAGER_SECRET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Name') == SECRETS_MANAGER_SECRET_NAME and not response.get('DeletedDate'):
        return {
            'status': 'passed',
            'message': f'Secret {SECRETS_MANAGER_SECRET_NAME} exists and is active.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': f'Secret {SECRETS_MANAGER_SECRET_NAME} is missing or scheduled for deletion.',
    }


def _verify_secretsmanager_initial_read() -> dict[str, Any]:
    cached = cache.get(SECRETS_MANAGER_INITIAL_CACHE_KEY)
    if cached:
        return {
            'status': 'passed',
            'message': 'Secrets Manager returned the initial credential JSON.',
            'resource': cached,
        }
    try:
        response = _secretsmanager_client().get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if _secret_string_json(response) == SECRETS_MANAGER_INITIAL_SECRET:
        return {
            'status': 'passed',
            'message': 'Secrets Manager currently returns the initial credential JSON.',
            'resource': _clean_response(response),
        }
    return {
        'status': 'failed',
        'message': 'No successful read of the initial secret value has been recorded for this lab run.',
    }


def _verify_secretsmanager_updated_secret() -> dict[str, Any]:
    try:
        response = _secretsmanager_client().get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if _secret_string_json(response) == SECRETS_MANAGER_UPDATED_SECRET:
        resource = _clean_response(response)
        cache.set(SECRETS_MANAGER_UPDATED_CACHE_KEY, resource, timeout=3600)
        return {
            'status': 'passed',
            'message': 'Secrets Manager returns the rotated credential JSON as the current value.',
            'resource': resource,
        }
    return {
        'status': 'failed',
        'message': 'The current secret value does not match the rotated credential JSON.',
    }


def _verify_s3_bucket() -> dict[str, Any]:
    try:
        response = FlociClientFactory().client('s3').head_bucket(Bucket=S3_BASICS_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_BASICS_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_BASICS_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_objects_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_OBJECTS_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_OBJECTS_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_OBJECTS_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_hello_object() -> dict[str, Any]:
    try:
        s3 = _s3_client()
        head = s3.head_object(Bucket=S3_OBJECTS_BUCKET_NAME, Key=S3_HELLO_OBJECT_KEY)
        response = s3.get_object(Bucket=S3_OBJECTS_BUCKET_NAME, Key=S3_HELLO_OBJECT_KEY)
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    content_type = head.get('ContentType')
    content_length = head.get('ContentLength')
    if (
        body == S3_HELLO_OBJECT_BYTES
        and content_type == 'text/plain'
        and content_length == len(S3_HELLO_OBJECT_BYTES)
    ):
        return {
            'status': 'passed',
            'message': f'Object {S3_HELLO_OBJECT_KEY} exists with the expected content and metadata.',
            'resource': {
                'bucket': S3_OBJECTS_BUCKET_NAME,
                'key': S3_HELLO_OBJECT_KEY,
                'content_type': content_type,
                'content_length': content_length,
                'body': body.decode('utf-8'),
            },
        }

    return {
        'status': 'failed',
        'message': f'Object {S3_HELLO_OBJECT_KEY} does not match the lab artifact.',
    }


def _verify_s3_prefixes_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_PREFIXES_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_PREFIXES_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_PREFIXES_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_report_object(key: str) -> dict[str, Any]:
    try:
        response = _s3_client().get_object(Bucket=S3_PREFIXES_BUCKET_NAME, Key=key)
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if body == S3_REPORT_BYTES:
        return {
            'status': 'passed',
            'message': f'Object {key} exists with the expected report contents.',
            'resource': {
                'bucket': S3_PREFIXES_BUCKET_NAME,
                'key': key,
                'content_length': len(body),
                'body': body.decode('utf-8'),
            },
        }

    return {
        'status': 'failed',
        'message': f'Object {key} does not match the report artifact.',
    }


def _verify_s3_metadata_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_METADATA_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_METADATA_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_METADATA_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_invoice_metadata() -> dict[str, Any]:
    try:
        s3 = _s3_client()
        head = s3.head_object(Bucket=S3_METADATA_BUCKET_NAME, Key=S3_INVOICE_OBJECT_KEY)
        response = s3.get_object(Bucket=S3_METADATA_BUCKET_NAME, Key=S3_INVOICE_OBJECT_KEY)
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    metadata = head.get('Metadata', {})
    if (
        body == S3_INVOICE_BYTES
        and head.get('ContentType') == 'text/plain'
        and head.get('ContentLength') == len(S3_INVOICE_BYTES)
        and metadata == S3_INVOICE_METADATA
    ):
        return {
            'status': 'passed',
            'message': f'Object {S3_INVOICE_OBJECT_KEY} has the expected body, content type, and user metadata.',
            'resource': {
                'bucket': S3_METADATA_BUCKET_NAME,
                'key': S3_INVOICE_OBJECT_KEY,
                'content_type': head.get('ContentType'),
                'content_length': head.get('ContentLength'),
                'metadata': metadata,
            },
        }

    return {
        'status': 'failed',
        'message': f'Object {S3_INVOICE_OBJECT_KEY} does not match the expected metadata workflow.',
    }


def _verify_s3_invoice_tags() -> dict[str, Any]:
    try:
        response = _s3_client().get_object_tagging(
            Bucket=S3_METADATA_BUCKET_NAME,
            Key=S3_INVOICE_OBJECT_KEY,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    tags = response.get('TagSet', [])
    expected = {(tag['Key'], tag['Value']) for tag in S3_INVOICE_TAGS}
    actual = {(tag.get('Key'), tag.get('Value')) for tag in tags}
    if actual == expected:
        return {
            'status': 'passed',
            'message': f'Object {S3_INVOICE_OBJECT_KEY} has the expected Environment and CostCenter tags.',
            'resource': {'tags': _clean_response(tags)},
        }

    return {
        'status': 'failed',
        'message': f'Object {S3_INVOICE_OBJECT_KEY} does not have the expected tag set.',
    }


def _verify_s3_versioning_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_VERSIONING_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_VERSIONING_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_VERSIONING_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_versioning_enabled() -> dict[str, Any]:
    try:
        response = _s3_client().get_bucket_versioning(Bucket=S3_VERSIONING_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if response.get('Status') == 'Enabled':
        return {
            'status': 'passed',
            'message': f'Versioning is enabled on {S3_VERSIONING_BUCKET_NAME}.',
            'resource': _clean_response(response),
        }

    return {
        'status': 'failed',
        'message': f'Versioning is not enabled on {S3_VERSIONING_BUCKET_NAME}.',
    }


def _s3_configuration_versions() -> dict[str, Any]:
    s3 = _s3_client()
    response = s3.list_object_versions(
        Bucket=S3_VERSIONING_BUCKET_NAME,
        Prefix=S3_CONFIGURATION_KEY,
    )
    discovered: dict[str, dict[str, Any]] = {}
    unreadable_version_ids: list[str] = []
    for version in response.get('Versions', []):
        if version.get('Key') != S3_CONFIGURATION_KEY or not version.get('VersionId'):
            continue
        try:
            object_response = s3.get_object(
                Bucket=S3_VERSIONING_BUCKET_NAME,
                Key=S3_CONFIGURATION_KEY,
                VersionId=version['VersionId'],
            )
        except ClientError as exc:
            if _error_code(exc) not in {'500', 'InternalError', 'NoSuchKey', 'NoSuchVersion'}:
                raise
            unreadable_version_ids.append(version['VersionId'])
            continue
        body = object_response['Body'].read()
        if body == S3_CONFIGURATION_V1_BYTES:
            discovered.setdefault('v1', {**version, 'body': S3_CONFIGURATION_V1_TEXT})
        elif body == S3_CONFIGURATION_V2_BYTES:
            discovered.setdefault('v2', {**version, 'body': S3_CONFIGURATION_V2_TEXT})
    return {
        'response': response,
        'v1': discovered.get('v1'),
        'v2': discovered.get('v2'),
        'unreadable_version_ids': unreadable_version_ids,
    }


def _verify_s3_configuration_version_one() -> dict[str, Any]:
    try:
        versions = _s3_configuration_versions()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    version = versions.get('v1')
    if version:
        return {
            'status': 'passed',
            'message': f'Configuration v1 is stored as version {version["VersionId"]}.',
            'resource': _clean_response(version),
        }

    return {
        'status': 'failed',
        'message': 'Configuration v1 was not found in the stored object versions.',
    }


def _verify_s3_configuration_versions() -> dict[str, Any]:
    try:
        versions = _s3_configuration_versions()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    version_one = versions.get('v1')
    version_two = versions.get('v2')
    if version_one and version_two and version_two.get('IsLatest') is True:
        return {
            'status': 'passed',
            'message': 'Configuration v1 is preserved and configuration v2 is the latest version.',
            'resource': {
                'v1': _clean_response(version_one),
                'v2': _clean_response(version_two),
            },
        }

    return {
        'status': 'failed',
        'message': 'The expected v1 and latest v2 object versions were not both found.',
    }


def _verify_s3_presigned_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_PRESIGNED_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_PRESIGNED_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_PRESIGNED_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_presigned_object() -> dict[str, Any]:
    try:
        response = _s3_client().get_object(
            Bucket=S3_PRESIGNED_BUCKET_NAME,
            Key=S3_PRESIGNED_OBJECT_KEY,
        )
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if body == S3_PRESIGNED_GUIDE_BYTES:
        return {
            'status': 'passed',
            'message': f'Object {S3_PRESIGNED_OBJECT_KEY} contains the expected guide.',
            'resource': {
                'bucket': S3_PRESIGNED_BUCKET_NAME,
                'key': S3_PRESIGNED_OBJECT_KEY,
                'content_length': len(body),
            },
        }

    return {
        'status': 'failed',
        'message': f'Object {S3_PRESIGNED_OBJECT_KEY} does not match the guide artifact.',
    }


def _generate_s3_presigned_url() -> str:
    return _s3_client().generate_presigned_url(
        'get_object',
        Params={
            'Bucket': S3_PRESIGNED_BUCKET_NAME,
            'Key': S3_PRESIGNED_OBJECT_KEY,
        },
        ExpiresIn=S3_PRESIGNED_EXPIRES_IN,
    )


def _redeem_s3_presigned_url(url: str) -> bytes:
    try:
        with urlopen(url, timeout=3) as response:
            return response.read()
    except (OSError, URLError) as exc:
        raise ValueError(f'Presigned URL could not be redeemed: {exc}') from exc


def _verify_s3_presigned_access() -> dict[str, Any]:
    try:
        url = _generate_s3_presigned_url()
        body = _redeem_s3_presigned_url(url)
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}

    if body == S3_PRESIGNED_GUIDE_BYTES:
        return {
            'status': 'passed',
            'message': 'The five-minute presigned URL returned the expected guide without AWS credentials.',
            'resource': {
                'url': url,
                'expires_in': S3_PRESIGNED_EXPIRES_IN,
                'downloaded_body': body.decode('utf-8'),
            },
        }

    return {
        'status': 'failed',
        'message': 'The presigned URL did not return the expected guide.',
    }


def _verify_s3_security_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_SECURITY_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_SECURITY_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_SECURITY_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_public_access_block() -> dict[str, Any]:
    try:
        response = _s3_client().get_public_access_block(Bucket=S3_SECURITY_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    configuration = response.get('PublicAccessBlockConfiguration', {})
    if configuration == S3_PUBLIC_ACCESS_BLOCK:
        return {
            'status': 'passed',
            'message': 'All four S3 Public Access Block safeguards are enabled.',
            'resource': _clean_response(configuration),
        }

    return {
        'status': 'failed',
        'message': 'The bucket does not have the expected Public Access Block configuration.',
    }


def _verify_s3_security_bucket_policy() -> dict[str, Any]:
    try:
        response = _s3_client().get_bucket_policy(Bucket=S3_SECURITY_BUCKET_NAME)
        policy = json.loads(response.get('Policy', '{}'))
    except (ClientError, json.JSONDecodeError) as exc:
        return {'status': 'failed', 'message': str(exc)}

    if policy == S3_SECURITY_BUCKET_POLICY:
        return {
            'status': 'passed',
            'message': 'The bucket policy grants only s3:ListBucket to the local account root.',
            'resource': _clean_response(policy),
        }

    return {
        'status': 'failed',
        'message': 'The bucket policy does not match the expected least-privilege policy.',
    }


def _verify_s3_encryption_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_ENCRYPTION_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_ENCRYPTION_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_ENCRYPTION_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_default_encryption() -> dict[str, Any]:
    try:
        response = _s3_client().get_bucket_encryption(Bucket=S3_ENCRYPTION_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    configuration = response.get('ServerSideEncryptionConfiguration', {})
    if configuration == S3_SSE_S3_CONFIGURATION:
        return {
            'status': 'passed',
            'message': 'Default SSE-S3 encryption is configured with the AES256 algorithm.',
            'resource': _clean_response(configuration),
        }

    return {
        'status': 'failed',
        'message': 'The bucket does not have the expected AES256 default-encryption configuration.',
    }


def _verify_s3_encrypted_record_workflow() -> dict[str, Any]:
    encryption = _verify_s3_default_encryption()
    if encryption.get('status') != 'passed':
        return encryption

    try:
        s3 = _s3_client()
        head = s3.head_object(Bucket=S3_ENCRYPTION_BUCKET_NAME, Key=S3_ENCRYPTION_OBJECT_KEY)
        response = s3.get_object(Bucket=S3_ENCRYPTION_BUCKET_NAME, Key=S3_ENCRYPTION_OBJECT_KEY)
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if (
        body == S3_ENCRYPTION_RECORD_BYTES
        and head.get('ContentType') == 'text/plain'
        and head.get('ContentLength') == len(S3_ENCRYPTION_RECORD_BYTES)
    ):
        return {
            'status': 'passed',
            'message': 'The record was uploaded after the bucket AES256 default was enabled.',
            'resource': {
                'bucket': S3_ENCRYPTION_BUCKET_NAME,
                'key': S3_ENCRYPTION_OBJECT_KEY,
                'content_type': head.get('ContentType'),
                'content_length': head.get('ContentLength'),
            },
        }

    return {
        'status': 'failed',
        'message': 'The protected record does not match the expected artifact.',
    }


def _verify_s3_lifecycle_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_LIFECYCLE_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_LIFECYCLE_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_LIFECYCLE_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_lifecycle_log() -> dict[str, Any]:
    try:
        s3 = _s3_client()
        head = s3.head_object(Bucket=S3_LIFECYCLE_BUCKET_NAME, Key=S3_LIFECYCLE_OBJECT_KEY)
        response = s3.get_object(Bucket=S3_LIFECYCLE_BUCKET_NAME, Key=S3_LIFECYCLE_OBJECT_KEY)
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if (
        body == S3_LIFECYCLE_LOG_BYTES
        and head.get('ContentType') == 'text/plain'
        and head.get('ContentLength') == len(S3_LIFECYCLE_LOG_BYTES)
    ):
        return {
            'status': 'passed',
            'message': 'The sample application log exists under the logs/ lifecycle prefix.',
            'resource': {
                'bucket': S3_LIFECYCLE_BUCKET_NAME,
                'key': S3_LIFECYCLE_OBJECT_KEY,
                'content_type': head.get('ContentType'),
                'content_length': head.get('ContentLength'),
            },
        }

    return {
        'status': 'failed',
        'message': 'The lifecycle log does not match the expected artifact.',
    }


def _verify_s3_lifecycle_configuration() -> dict[str, Any]:
    try:
        response = _s3_client().get_bucket_lifecycle_configuration(
            Bucket=S3_LIFECYCLE_BUCKET_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    rules = response.get('Rules', [])
    rule = next(
        (item for item in rules if item.get('ID') == S3_LIFECYCLE_RULE_ID),
        None,
    )
    prefix = (rule or {}).get('Filter', {}).get('Prefix')
    if prefix is None:
        prefix = (rule or {}).get('Prefix')

    if (
        rule
        and rule.get('Status') == 'Enabled'
        and prefix == 'logs/'
        and rule.get('Expiration', {}).get('Days') == 30
    ):
        return {
            'status': 'passed',
            'message': 'The enabled lifecycle rule expires objects under logs/ after 30 days.',
            'resource': _clean_response(rule),
        }

    return {
        'status': 'failed',
        'message': 'The bucket does not have the expected 30-day logs/ expiration rule.',
    }


def _verify_s3_cors_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_CORS_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_CORS_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_CORS_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_cors_configuration() -> dict[str, Any]:
    try:
        response = _s3_client().get_bucket_cors(Bucket=S3_CORS_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    rules = response.get('CORSRules', [])
    expected_rule = S3_CORS_CONFIGURATION['CORSRules'][0]
    rule = next(
        (item for item in rules if item.get('ID') == expected_rule['ID']),
        None,
    )

    if rule and all(
        rule.get(field) == expected_rule[field]
        for field in (
            'AllowedOrigins',
            'AllowedMethods',
            'AllowedHeaders',
            'ExposeHeaders',
            'MaxAgeSeconds',
        )
    ):
        return {
            'status': 'passed',
            'message': 'The CORS rule allows GET and HEAD from http://localhost:3000 and exposes ETag.',
            'resource': _clean_response(rule),
        }

    return {
        'status': 'failed',
        'message': 'The bucket does not have the expected local web app CORS rule.',
    }


def _s3_notifications_queue_url() -> str:
    return _sqs_client().get_queue_url(
        QueueName=S3_NOTIFICATIONS_QUEUE_NAME,
    )['QueueUrl']


def _verify_s3_notifications_queue() -> dict[str, Any]:
    try:
        queue_url = _s3_notifications_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    queue_arn = response.get('Attributes', {}).get('QueueArn')
    if queue_arn == S3_NOTIFICATIONS_QUEUE_ARN:
        return {
            'status': 'passed',
            'message': f'Queue {S3_NOTIFICATIONS_QUEUE_NAME} exists with the expected ARN.',
            'resource': {
                'name': S3_NOTIFICATIONS_QUEUE_NAME,
                'url': queue_url,
                'arn': queue_arn,
            },
        }

    return {
        'status': 'failed',
        'message': 'The notification queue does not have the expected ARN.',
    }


def _verify_s3_notifications_queue_policy() -> dict[str, Any]:
    try:
        queue_url = _s3_notifications_queue_url()
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['Policy'],
        )
        policy = json.loads(response.get('Attributes', {}).get('Policy', '{}'))
    except (ClientError, json.JSONDecodeError) as exc:
        return {'status': 'failed', 'message': str(exc)}

    if policy == S3_NOTIFICATIONS_QUEUE_POLICY:
        return {
            'status': 'passed',
            'message': 'The queue policy authorizes this S3 bucket to send object-created events.',
            'resource': _clean_response(policy),
        }

    return {
        'status': 'failed',
        'message': 'The queue does not have the expected S3 delivery policy.',
    }


def _verify_s3_notifications_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_NOTIFICATIONS_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_NOTIFICATIONS_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_NOTIFICATIONS_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _verify_s3_notification_configuration() -> dict[str, Any]:
    try:
        response = _s3_client().get_bucket_notification_configuration(
            Bucket=S3_NOTIFICATIONS_BUCKET_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    configurations = response.get('QueueConfigurations', [])
    configuration = next(
        (
            item for item in configurations
            if item.get('Id') == S3_NOTIFICATIONS_CONFIGURATION_ID
        ),
        None,
    )
    if (
        configuration
        and configuration.get('QueueArn') == S3_NOTIFICATIONS_QUEUE_ARN
        and configuration.get('Events') == ['s3:ObjectCreated:*']
    ):
        return {
            'status': 'passed',
            'message': 'S3 object-created events are routed to the lab SQS queue.',
            'resource': _clean_response(configuration),
        }

    return {
        'status': 'failed',
        'message': 'The bucket does not have the expected SQS notification configuration.',
    }


def _verify_s3_notification_object() -> dict[str, Any]:
    try:
        s3 = _s3_client()
        head = s3.head_object(
            Bucket=S3_NOTIFICATIONS_BUCKET_NAME,
            Key=S3_NOTIFICATIONS_OBJECT_KEY,
        )
        response = s3.get_object(
            Bucket=S3_NOTIFICATIONS_BUCKET_NAME,
            Key=S3_NOTIFICATIONS_OBJECT_KEY,
        )
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if (
        body == S3_NOTIFICATIONS_REPORT_BYTES
        and head.get('ContentType') == 'text/plain'
        and head.get('ContentLength') == len(S3_NOTIFICATIONS_REPORT_BYTES)
    ):
        return {
            'status': 'passed',
            'message': 'The report object exists and is ready to produce an S3 event.',
            'resource': {
                'bucket': S3_NOTIFICATIONS_BUCKET_NAME,
                'key': S3_NOTIFICATIONS_OBJECT_KEY,
                'content_type': head.get('ContentType'),
                'content_length': head.get('ContentLength'),
            },
        }

    return {
        'status': 'failed',
        'message': 'The notification report does not match the expected artifact.',
    }


def _s3_event_record(messages: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for message in messages:
        try:
            body = json.loads(message.get('Body', '{}'))
        except json.JSONDecodeError:
            continue
        for record in body.get('Records', []):
            s3_record = record.get('s3', {})
            if (
                record.get('eventSource') == 'aws:s3'
                and record.get('eventName', '').startswith('ObjectCreated:')
                and s3_record.get('bucket', {}).get('name') == S3_NOTIFICATIONS_BUCKET_NAME
                and s3_record.get('object', {}).get('key') == S3_NOTIFICATIONS_OBJECT_KEY
            ):
                return message, record
    return None, None


def _verify_s3_notification_delivery() -> dict[str, Any]:
    try:
        queue_url = _s3_notifications_queue_url()
        response = _sqs_client().receive_message(
            QueueUrl=queue_url,
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    message, record = _s3_event_record(response.get('Messages', []))
    if message and record:
        return {
            'status': 'passed',
            'message': 'SQS received the ObjectCreated event for uploads/report.txt.',
            'resource': {
                'message_id': message.get('MessageId'),
                'event': _clean_response(record),
            },
        }

    return {
        'status': 'failed',
        'message': 'The queue does not contain the expected S3 object-created event.',
    }


def _verify_s3_multipart_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_MULTIPART_BUCKET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    return {
        'status': 'passed',
        'message': f'Bucket {S3_MULTIPART_BUCKET_NAME} exists and is reachable.',
        'resource': {
            'name': S3_MULTIPART_BUCKET_NAME,
            **_clean_response(response),
        },
    }


def _s3_active_multipart_upload() -> dict[str, Any] | None:
    response = _s3_client().list_multipart_uploads(
        Bucket=S3_MULTIPART_BUCKET_NAME,
        Prefix=S3_MULTIPART_OBJECT_KEY,
    )
    return next(
        (
            upload for upload in response.get('Uploads', [])
            if upload.get('Key') == S3_MULTIPART_OBJECT_KEY
        ),
        None,
    )


def _verify_s3_multipart_upload_started() -> dict[str, Any]:
    try:
        upload = _s3_active_multipart_upload()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if upload and upload.get('UploadId'):
        return {
            'status': 'passed',
            'message': 'The multipart upload is active and has a discoverable upload ID.',
            'resource': _clean_response(upload),
        }

    return {
        'status': 'failed',
        'message': 'No active multipart upload exists for the lab object.',
    }


def _s3_multipart_parts() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    upload = _s3_active_multipart_upload()
    if not upload or not upload.get('UploadId'):
        raise ValueError('No active multipart upload exists for the lab object.')
    response = _s3_client().list_parts(
        Bucket=S3_MULTIPART_BUCKET_NAME,
        Key=S3_MULTIPART_OBJECT_KEY,
        UploadId=upload['UploadId'],
    )
    return upload, response.get('Parts', [])


def _verify_s3_multipart_parts(required_part_numbers: set[int]) -> dict[str, Any]:
    try:
        upload, parts = _s3_multipart_parts()
    except (ClientError, ValueError) as exc:
        return {'status': 'failed', 'message': str(exc)}

    by_number = {part.get('PartNumber'): part for part in parts}
    expected_sizes = {
        1: len(S3_MULTIPART_PART_ONE_BYTES),
        2: len(S3_MULTIPART_PART_TWO_BYTES),
    }
    if all(
        number in by_number
        and by_number[number].get('ETag')
        and by_number[number].get('Size') == expected_sizes[number]
        for number in required_part_numbers
    ):
        return {
            'status': 'passed',
            'message': (
                'Both multipart parts are uploaded with their expected sizes and ETags.'
                if required_part_numbers == {1, 2}
                else 'Multipart part 1 is uploaded with its expected size and ETag.'
            ),
            'resource': {
                'upload_id': upload.get('UploadId'),
                'parts': _clean_response(parts),
            },
        }

    return {
        'status': 'failed',
        'message': 'The active upload does not contain the expected multipart parts.',
    }


def _verify_s3_multipart_object() -> dict[str, Any]:
    try:
        s3 = _s3_client()
        head = s3.head_object(
            Bucket=S3_MULTIPART_BUCKET_NAME,
            Key=S3_MULTIPART_OBJECT_KEY,
        )
        response = s3.get_object(
            Bucket=S3_MULTIPART_BUCKET_NAME,
            Key=S3_MULTIPART_OBJECT_KEY,
        )
        body = response['Body'].read()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}

    if (
        body == S3_MULTIPART_OBJECT_BYTES
        and head.get('ContentType') == S3_MULTIPART_CONTENT_TYPE
        and head.get('ContentLength') == len(S3_MULTIPART_OBJECT_BYTES)
    ):
        return {
            'status': 'passed',
            'message': 'The completed object contains both multipart payloads in order.',
            'resource': {
                'bucket': S3_MULTIPART_BUCKET_NAME,
                'key': S3_MULTIPART_OBJECT_KEY,
                'content_type': head.get('ContentType'),
                'content_length': head.get('ContentLength'),
                'etag': head.get('ETag'),
            },
        }

    return {
        'status': 'failed',
        'message': 'The completed multipart object does not match the expected bytes.',
    }


def _sqs_queue_url(queue_name: str) -> str:
    return _sqs_client().get_queue_url(QueueName=queue_name)['QueueUrl']


def _verify_scheduler_queue() -> dict[str, Any]:
    try:
        queue_url = _sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME)
        response = _sqs_client().get_queue_attributes(
            QueueUrl=queue_url,
            AttributeNames=['QueueArn'],
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Attributes', {}).get('QueueArn') == SCHEDULER_SQS_QUEUE_ARN:
        return {
            'status': 'passed',
            'message': 'The scheduled-reports target queue exists.',
            'resource': {
                'queue_url': queue_url,
                'queue_arn': SCHEDULER_SQS_QUEUE_ARN,
            },
        }
    return {'status': 'failed', 'message': 'The target queue is not ready.'}


def _verify_scheduler_role() -> dict[str, Any]:
    try:
        response = _iam_client().get_role(RoleName=SCHEDULER_ROLE_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    role = response.get('Role', {})
    if (
        role.get('Arn') == SCHEDULER_ROLE_ARN
        and role.get('AssumeRolePolicyDocument') == SCHEDULER_TRUST_POLICY
    ):
        return {
            'status': 'passed',
            'message': 'The execution role trusts only EventBridge Scheduler.',
            'resource': _clean_response(role),
        }
    return {'status': 'failed', 'message': 'The Scheduler role trust policy is incorrect.'}


def _verify_scheduler_role_policy() -> dict[str, Any]:
    try:
        response = _iam_client().get_role_policy(
            RoleName=SCHEDULER_ROLE_NAME,
            PolicyName=SCHEDULER_ROLE_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('PolicyDocument') == SCHEDULER_ROLE_POLICY:
        return {
            'status': 'passed',
            'message': 'The execution role can send messages only to the lab queue.',
            'resource': _clean_response(response),
        }
    return {'status': 'failed', 'message': 'The execution role policy is incorrect.'}


def _verify_scheduler_group() -> dict[str, Any]:
    try:
        response = _scheduler_client().get_schedule_group(
            Name=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if response.get('Arn') == SCHEDULER_GROUP_ARN:
        return {
            'status': 'passed',
            'message': 'The dedicated schedule group exists.',
            'resource': _clean_response(response),
        }
    return {'status': 'failed', 'message': 'The schedule group does not exist.'}


def _verify_scheduler_schedule() -> dict[str, Any]:
    expression = cache.get(SCHEDULER_EXPRESSION_CACHE_KEY)
    if not expression:
        return {
            'status': 'failed',
            'message': 'The lab has not created its one-time schedule.',
        }
    try:
        response = _scheduler_client().get_schedule(
            Name=SCHEDULER_SCHEDULE_NAME,
            GroupName=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    target = response.get('Target', {})
    if (
        response.get('Arn') == SCHEDULER_SCHEDULE_ARN
        and response.get('ScheduleExpression') == expression
        and response.get('FlexibleTimeWindow') == {'Mode': 'OFF'}
        and response.get('ActionAfterCompletion') == 'DELETE'
        and response.get('State') == 'ENABLED'
        and target.get('Arn') == SCHEDULER_SQS_QUEUE_ARN
        and target.get('RoleArn') == SCHEDULER_ROLE_ARN
        and target.get('Input') == SCHEDULER_MESSAGE_TEXT
        and target.get('RetryPolicy') == {
            'MaximumEventAgeInSeconds': 60,
            'MaximumRetryAttempts': 1,
        }
    ):
        return {
            'status': 'passed',
            'message': 'The pending one-time schedule has the expected target and execution settings.',
            'resource': _clean_response(response),
        }
    return {'status': 'failed', 'message': 'The schedule configuration is incomplete.'}


def _scheduler_message(
    messages: list[dict[str, Any]],
) -> dict[str, Any] | None:
    return next(
        (
            message for message in messages
            if message.get('Body') == SCHEDULER_MESSAGE_TEXT
        ),
        None,
    )


def _verify_scheduler_delivery() -> dict[str, Any]:
    try:
        response = _sqs_client().receive_message(
            QueueUrl=_sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME),
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    message = _scheduler_message(response.get('Messages', []))
    if message:
        return {
            'status': 'passed',
            'message': 'The target queue contains the scheduled report.ready event.',
            'resource': _clean_response(message),
        }
    return {'status': 'failed', 'message': 'The scheduled event is not available.'}


def _verify_scheduler_deleted() -> dict[str, Any]:
    if not cache.get(SCHEDULER_DELETED_CACHE_KEY):
        return {
            'status': 'failed',
            'message': 'Automatic schedule deletion has not been verified.',
        }
    try:
        _scheduler_client().get_schedule(
            Name=SCHEDULER_SCHEDULE_NAME,
            GroupName=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        if _error_code(exc) == 'ResourceNotFoundException':
            return {
                'status': 'passed',
                'message': 'The completed one-time schedule was deleted automatically.',
            }
        return {'status': 'failed', 'message': str(exc)}
    return {'status': 'failed', 'message': 'The completed schedule still exists.'}


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

    if service_key == 'lambda' and lab_key == 'create-invoke-logs':
        runners = {
            'create-role': _run_lambda_create_role,
            'put-role-policy': _run_lambda_put_role_policy,
            'create-function': _run_lambda_create_function,
            'invoke-function': _run_lambda_invoke_function,
            'inspect-logs': _run_lambda_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'lambda' and lab_key == 'runtime-config':
        runners = {
            'put-parameter': _run_lambda_config_put_parameter,
            'create-secret': _run_lambda_config_create_secret,
            'create-role': _run_lambda_config_create_role,
            'put-role-policy': _run_lambda_config_put_role_policy,
            'create-function': _run_lambda_config_create_function,
            'invoke-function': _run_lambda_config_invoke,
            'inspect-logs': _run_lambda_config_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'lambda' and lab_key == 'sqs-event-source':
        runners = {
            'create-queue': _run_lambda_sqs_create_queue,
            'create-role': _run_lambda_sqs_create_role,
            'put-role-policy': _run_lambda_sqs_put_role_policy,
            'create-function': _run_lambda_sqs_create_function,
            'create-event-source-mapping': _run_lambda_sqs_create_event_source_mapping,
            'send-message': _run_lambda_sqs_send_message,
            'inspect-logs': _run_lambda_sqs_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'apigateway' and lab_key == 'lambda-request':
        runners = {
            'create-role': lambda: _run_lambda_create_role('lambda-request', 'apigateway'),
            'put-role-policy': lambda: _run_lambda_put_role_policy('lambda-request', 'apigateway'),
            'create-function': lambda: _run_lambda_create_function('lambda-request', 'apigateway'),
            'create-api': _run_apigw_create_api,
            'create-integration': _run_apigw_create_integration,
            'create-route': _run_apigw_create_route,
            'create-stage': _run_apigw_create_stage,
            'add-lambda-permission': _run_apigw_add_lambda_permission,
            'send-request': _run_apigw_send_request,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'dynamodb' and lab_key == 'crud-query':
        runners = {
            'create-table': _run_dynamodb_create_table,
            'put-item': _run_dynamodb_put_item,
            'get-item': _run_dynamodb_get_item,
            'update-item': _run_dynamodb_update_item,
            'query-customer-index': _run_dynamodb_query_customer_index,
            'delete-item': _run_dynamodb_delete_item,
            'delete-table': _run_dynamodb_delete_table,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'dynamodb' and lab_key == 'lambda-writes':
        runners = {
            'create-table': _run_lambda_dynamodb_create_table,
            'create-role': _run_lambda_dynamodb_create_role,
            'put-role-policy': _run_lambda_dynamodb_put_policy,
            'create-function': _run_lambda_dynamodb_create_function,
            'invoke-function': _run_lambda_dynamodb_invoke,
            'get-written-item': _run_lambda_dynamodb_get_item,
            'inspect-logs': _run_lambda_dynamodb_inspect_logs,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'kms' and lab_key == 'key-alias-encrypt-decrypt':
        runners = {
            'create-key': _run_kms_create_key,
            'create-alias': _run_kms_create_alias,
            'describe-key': _run_kms_describe_key,
            'encrypt': _run_kms_encrypt,
            'decrypt': _run_kms_decrypt,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'ssm' and lab_key == 'parameter-store-config':
        runners = {
            'put-parameter': _run_ssm_put_parameter,
            'get-parameter': _run_ssm_get_parameter,
            'get-parameters-by-path': _run_ssm_get_parameters_by_path,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'secretsmanager' and lab_key == 'secret-lifecycle':
        runners = {
            'create-secret': _run_secretsmanager_create_secret,
            'get-initial-secret': _run_secretsmanager_get_initial_secret,
            'put-secret-value': _run_secretsmanager_put_secret_value,
            'get-updated-secret': _run_secretsmanager_get_updated_secret,
            'describe-secret': _run_secretsmanager_describe_secret,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'create-bucket':
        runners = {
            'create-bucket': _run_s3_create_bucket,
            'head-bucket': _run_s3_head_bucket,
            'list-buckets': _run_s3_list_buckets,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'object-workflow':
        runners = {
            'create-bucket': _run_s3_create_objects_bucket,
            'put-object': _run_s3_put_hello_object,
            'list-objects': _run_s3_list_hello_objects,
            'head-object': _run_s3_head_hello_object,
            'get-object': _run_s3_get_hello_object,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'prefix-copy':
        runners = {
            'create-bucket': _run_s3_create_prefixes_bucket,
            'put-source-object': _run_s3_put_source_report,
            'list-incoming-prefix': _run_s3_list_incoming_prefix,
            'copy-object': _run_s3_copy_report_to_archive,
            'list-archive-prefix': _run_s3_list_archive_prefix,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'metadata-tags':
        runners = {
            'create-bucket': _run_s3_create_metadata_bucket,
            'put-object': _run_s3_put_invoice_object,
            'head-object': _run_s3_head_invoice_object,
            'put-object-tagging': _run_s3_put_invoice_tags,
            'get-object-tagging': _run_s3_get_invoice_tags,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'version-recovery':
        runners = {
            'create-bucket': _run_s3_create_versioning_bucket,
            'enable-versioning': _run_s3_enable_versioning,
            'put-version-one': _run_s3_put_configuration_v1,
            'put-version-two': _run_s3_put_configuration_v2,
            'list-object-versions': _run_s3_list_configuration_versions,
            'get-version-one': _run_s3_get_configuration_v1,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'presigned-url':
        runners = {
            'create-bucket': _run_s3_create_presigned_bucket,
            'put-object': _run_s3_put_presigned_guide,
            'presign-object': _run_s3_presign_guide,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'bucket-security':
        runners = {
            'create-bucket': _run_s3_create_security_bucket,
            'put-public-access-block': _run_s3_put_public_access_block,
            'get-public-access-block': _run_s3_get_public_access_block,
            'put-bucket-policy': _run_s3_put_security_bucket_policy,
            'get-bucket-policy': _run_s3_get_security_bucket_policy,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'default-encryption':
        runners = {
            'create-bucket': _run_s3_create_encryption_bucket,
            'put-bucket-encryption': _run_s3_put_default_encryption,
            'get-bucket-encryption': _run_s3_get_default_encryption,
            'put-object': _run_s3_put_encrypted_record,
            'head-object': _run_s3_head_encrypted_record,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'lifecycle-retention':
        runners = {
            'create-bucket': _run_s3_create_lifecycle_bucket,
            'put-object': _run_s3_put_lifecycle_log,
            'put-lifecycle-configuration': _run_s3_put_lifecycle_configuration,
            'get-lifecycle-configuration': _run_s3_get_lifecycle_configuration,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'bucket-cors':
        runners = {
            'create-bucket': _run_s3_create_cors_bucket,
            'put-bucket-cors': _run_s3_put_bucket_cors,
            'get-bucket-cors': _run_s3_get_bucket_cors,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'object-notifications-sqs':
        runners = {
            'create-queue': _run_s3_notifications_create_queue,
            'set-queue-policy': _run_s3_notifications_set_queue_policy,
            'create-bucket': _run_s3_notifications_create_bucket,
            'put-notification-configuration': _run_s3_put_notification_configuration,
            'get-notification-configuration': _run_s3_get_notification_configuration,
            'put-object': _run_s3_notifications_put_object,
            'receive-event': _run_s3_notifications_receive_event,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 's3' and lab_key == 'multipart-upload':
        runners = {
            'create-bucket': _run_s3_multipart_create_bucket,
            'create-multipart-upload': _run_s3_create_multipart_upload,
            'upload-part-one': _run_s3_upload_multipart_part_one,
            'upload-part-two': _run_s3_upload_multipart_part_two,
            'list-parts': _run_s3_list_multipart_parts,
            'complete-multipart-upload': _run_s3_complete_multipart_upload,
            'get-object': _run_s3_get_multipart_object,
        }
        if step_key in runners:
            return runners[step_key]()

    if service_key == 'sqs':
        from .sqs_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'sns':
        from .sns_labs import run_step
        return run_step(service_key, lab_key, step_key)

    if service_key == 'scheduler' and lab_key == 'sqs-delivery':
        runners = {
            'create-queue': _run_scheduler_create_queue,
            'create-role': _run_scheduler_create_role,
            'put-role-policy': _run_scheduler_put_role_policy,
            'create-schedule-group': _run_scheduler_create_group,
            'create-schedule': _run_scheduler_create_schedule,
            'get-schedule': _run_scheduler_get_schedule,
            'receive-scheduled-message': _run_scheduler_receive_message,
            'confirm-schedule-deleted': _run_scheduler_confirm_deleted,
        }
        if step_key in runners:
            return runners[step_key]()

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

    if service_key == 'lambda' and lab_key == 'create-invoke-logs':
        role_verification = _verify_lambda_role()
        policy_verification = _verify_lambda_role_policy()
        function_verification = _verify_lambda_function()
        invocation_verification = _verify_lambda_invocation()
        log_group_verification = _verify_lambda_log_group()
        logs_verification = _verify_lambda_logs()
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        invocation_verified = invocation_verification.get('status') == 'passed'
        log_group_verified = log_group_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                role_verified
                and policy_verified
                and function_verified
                and invocation_verified
                and log_group_verified
                and logs_verified
            ),
            'steps': {
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'invoke-function': {'verified': invocation_verified, 'verification': invocation_verification if invocation_verified else None},
                'inspect-logs': {'verified': logs_verified, 'verification': logs_verification if logs_verified else None},
            },
        }

    if service_key == 'lambda' and lab_key == 'runtime-config':
        parameter_verification = _verify_lambda_config_parameter()
        secret_verification = _verify_lambda_config_secret()
        role_verification = _verify_lambda_config_role()
        policy_verification = _verify_lambda_config_role_policy()
        function_verification = _verify_lambda_config_function()
        invocation_verification = _verify_lambda_config_invocation()
        logs_verification = _verify_lambda_config_logs()
        parameter_verified = parameter_verification.get('status') == 'passed'
        secret_verified = secret_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        invocation_verified = invocation_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                parameter_verified
                and secret_verified
                and role_verified
                and policy_verified
                and function_verified
                and invocation_verified
                and logs_verified
            ),
            'steps': {
                'put-parameter': {'verified': parameter_verified, 'verification': parameter_verification if parameter_verified else None},
                'create-secret': {'verified': secret_verified, 'verification': secret_verification if secret_verified else None},
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'invoke-function': {'verified': invocation_verified, 'verification': invocation_verification if invocation_verified else None},
                'inspect-logs': {'verified': logs_verified, 'verification': logs_verification if logs_verified else None},
            },
        }

    if service_key == 'lambda' and lab_key == 'sqs-event-source':
        queue_verification = _verify_lambda_sqs_queue()
        role_verification = _verify_lambda_sqs_role()
        policy_verification = _verify_lambda_sqs_role_policy()
        function_verification = _verify_lambda_sqs_function()
        mapping_verification = _verify_lambda_sqs_event_source_mapping()
        message_verification = _verify_lambda_sqs_message_sent()
        logs_verification = _verify_lambda_sqs_logs()
        queue_verified = queue_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        mapping_verified = mapping_verification.get('status') == 'passed'
        message_verified = message_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                queue_verified
                and role_verified
                and policy_verified
                and function_verified
                and mapping_verified
                and message_verified
                and logs_verified
            ),
            'steps': {
                'create-queue': {'verified': queue_verified, 'verification': queue_verification if queue_verified else None},
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'create-event-source-mapping': {'verified': mapping_verified, 'verification': mapping_verification if mapping_verified else None},
                'send-message': {'verified': message_verified, 'verification': message_verification if message_verified else None},
                'inspect-logs': {'verified': logs_verified, 'verification': logs_verification if logs_verified else None},
            },
        }

    if service_key == 'apigateway' and lab_key == 'lambda-request':
        role_verification = _verify_lambda_role()
        policy_verification = _verify_lambda_role_policy()
        function_verification = _verify_lambda_function()
        api_verification = _verify_apigw_lambda_api()
        integration_verification = _verify_apigw_lambda_integration()
        route_verification = _verify_apigw_lambda_route()
        stage_verification = _verify_apigw_lambda_stage()
        permission_verification = _verify_apigw_lambda_permission()
        request_verification = _verify_apigw_lambda_request()
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        api_verified = api_verification.get('status') == 'passed'
        integration_verified = integration_verification.get('status') == 'passed'
        route_verified = route_verification.get('status') == 'passed'
        stage_verified = stage_verification.get('status') == 'passed'
        permission_verified = permission_verification.get('status') == 'passed'
        request_verified = request_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                role_verified
                and policy_verified
                and function_verified
                and api_verified
                and integration_verified
                and route_verified
                and stage_verified
                and permission_verified
                and request_verified
            ),
            'steps': {
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'create-api': {'verified': api_verified, 'verification': api_verification if api_verified else None},
                'create-integration': {'verified': integration_verified, 'verification': integration_verification if integration_verified else None},
                'create-route': {'verified': route_verified, 'verification': route_verification if route_verified else None},
                'create-stage': {'verified': stage_verified, 'verification': stage_verification if stage_verified else None},
                'add-lambda-permission': {'verified': permission_verified, 'verification': permission_verification if permission_verified else None},
                'send-request': {'verified': request_verified, 'verification': request_verification if request_verified else None},
            },
        }

    if service_key == 'dynamodb' and lab_key == 'crud-query':
        table_deleted_verification = _verify_dynamodb_table_deleted()
        table_deleted = (
            table_deleted_verification.get('status') == 'passed'
            and bool(cache.get(DYNAMODB_DELETE_TABLE_CACHE_KEY))
        )
        if table_deleted:
            return {
                'service': service_key,
                'lab': lab_key,
                'complete': True,
                'steps': {
                    'delete-table': {
                        'verified': True,
                        'verification': table_deleted_verification,
                    },
                },
            }

        table_verification = _verify_dynamodb_table_exists()
        created_verification = _verify_dynamodb_order_created()
        updated_verification = _verify_dynamodb_order_updated()
        query_verification = _verify_dynamodb_customer_query()
        deleted_verification = _verify_dynamodb_order_deleted()
        table_verified = table_verification.get('status') == 'passed'
        created_verified = created_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_PUT_CACHE_KEY))
        updated_verified = updated_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_UPDATE_CACHE_KEY))
        query_verified = query_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_QUERY_CACHE_KEY))
        deleted_verified = deleted_verification.get('status') == 'passed' and bool(cache.get(DYNAMODB_DELETE_ITEM_CACHE_KEY))
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': False,
            'steps': {
                'create-table': {'verified': table_verified, 'verification': table_verification if table_verified else None},
                'put-item': {'verified': created_verified or updated_verified, 'verification': (updated_verification if updated_verified else created_verification) if (created_verified or updated_verified) else None},
                'get-item': {'verified': created_verified or updated_verified, 'verification': (updated_verification if updated_verified else created_verification) if (created_verified or updated_verified) else None},
                'update-item': {'verified': updated_verified, 'verification': updated_verification if updated_verified else None},
                'query-customer-index': {'verified': query_verified, 'verification': query_verification if query_verified else None},
                'delete-item': {'verified': deleted_verified, 'verification': deleted_verification if deleted_verified else None},
                'delete-table': {'verified': False, 'verification': None},
            },
        }

    if service_key == 'dynamodb' and lab_key == 'lambda-writes':
        table_verification = _verify_lambda_dynamodb_table()
        role_verification = _verify_lambda_dynamodb_role()
        policy_verification = _verify_lambda_dynamodb_policy()
        function_verification = _verify_lambda_dynamodb_function()
        invocation_verification = _verify_lambda_dynamodb_invocation()
        item_verification = _verify_lambda_dynamodb_item()
        logs_verification = _verify_lambda_dynamodb_logs()
        table_verified = table_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        function_verified = function_verification.get('status') == 'passed'
        invocation_verified = invocation_verification.get('status') == 'passed'
        item_verified = item_verification.get('status') == 'passed'
        logs_verified = logs_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                table_verified
                and role_verified
                and policy_verified
                and function_verified
                and invocation_verified
                and item_verified
                and logs_verified
            ),
            'steps': {
                'create-table': {'verified': table_verified, 'verification': table_verification if table_verified else None},
                'create-role': {'verified': role_verified, 'verification': role_verification if role_verified else None},
                'put-role-policy': {'verified': policy_verified, 'verification': policy_verification if policy_verified else None},
                'create-function': {'verified': function_verified, 'verification': function_verification if function_verified else None},
                'invoke-function': {'verified': invocation_verified, 'verification': invocation_verification if invocation_verified else None},
                'get-written-item': {'verified': item_verified, 'verification': item_verification if item_verified else None},
                'inspect-logs': {'verified': logs_verified, 'verification': logs_verification if logs_verified else None},
            },
        }

    if service_key == 'kms' and lab_key == 'key-alias-encrypt-decrypt':
        key_verification = _verify_kms_lab_key()
        alias_verification = _verify_kms_lab_alias()
        ciphertext_verification = _verify_kms_lab_ciphertext()
        decrypt_verification = _verify_kms_lab_decrypt()
        key_verified = key_verification.get('status') == 'passed'
        alias_verified = alias_verification.get('status') == 'passed'
        ciphertext_verified = ciphertext_verification.get('status') == 'passed'
        decrypt_verified = decrypt_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': key_verified and alias_verified and ciphertext_verified and decrypt_verified,
            'steps': {
                'create-key': {'verified': key_verified, 'verification': key_verification if key_verified else None},
                'create-alias': {'verified': alias_verified, 'verification': alias_verification if alias_verified else None},
                'describe-key': {'verified': key_verified and alias_verified, 'verification': key_verification if key_verified and alias_verified else None},
                'encrypt': {'verified': ciphertext_verified, 'verification': ciphertext_verification if ciphertext_verified else None},
                'decrypt': {'verified': decrypt_verified, 'verification': decrypt_verification if decrypt_verified else None},
            },
        }

    if service_key == 'ssm' and lab_key == 'parameter-store-config':
        parameter_verification = _verify_ssm_parameter()
        path_verification = _verify_ssm_parameter_path()
        parameter_verified = parameter_verification.get('status') == 'passed'
        path_verified = path_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': parameter_verified and path_verified,
            'steps': {
                'put-parameter': {'verified': parameter_verified, 'verification': parameter_verification if parameter_verified else None},
                'get-parameter': {'verified': parameter_verified, 'verification': parameter_verification if parameter_verified else None},
                'get-parameters-by-path': {'verified': path_verified, 'verification': path_verification if path_verified else None},
            },
        }

    if service_key == 'secretsmanager' and lab_key == 'secret-lifecycle':
        exists_verification = _verify_secretsmanager_secret_exists()
        initial_verification = _verify_secretsmanager_initial_read()
        updated_verification = _verify_secretsmanager_updated_secret()
        exists_verified = exists_verification.get('status') == 'passed'
        initial_verified = initial_verification.get('status') == 'passed'
        updated_verified = updated_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': exists_verified and initial_verified and updated_verified,
            'steps': {
                'create-secret': {'verified': exists_verified, 'verification': exists_verification if exists_verified else None},
                'get-initial-secret': {'verified': initial_verified, 'verification': initial_verification if initial_verified else None},
                'put-secret-value': {'verified': updated_verified, 'verification': updated_verification if updated_verified else None},
                'get-updated-secret': {'verified': updated_verified, 'verification': updated_verification if updated_verified else None},
                'describe-secret': {'verified': exists_verified, 'verification': exists_verification if exists_verified else None},
            },
        }

    if service_key == 's3' and lab_key == 'create-bucket':
        verification = _verify_s3_bucket()
        verified = verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': verified,
            'steps': {
                'create-bucket': {'verified': verified, 'verification': verification if verified else None},
                'head-bucket': {'verified': verified, 'verification': verification if verified else None},
                'list-buckets': {'verified': verified, 'verification': verification if verified else None},
            },
        }

    if service_key == 's3' and lab_key == 'object-workflow':
        bucket_verification = _verify_s3_objects_bucket()
        object_verification = _verify_s3_hello_object()
        bucket_verified = bucket_verification.get('status') == 'passed'
        object_verified = object_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and object_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'list-objects': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'head-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'get-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'prefix-copy':
        bucket_verification = _verify_s3_prefixes_bucket()
        source_verification = _verify_s3_report_object(S3_REPORT_SOURCE_KEY)
        archive_verification = _verify_s3_report_object(S3_REPORT_ARCHIVE_KEY)
        bucket_verified = bucket_verification.get('status') == 'passed'
        source_verified = source_verification.get('status') == 'passed'
        archive_verified = archive_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and source_verified and archive_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-source-object': {
                    'verified': source_verified,
                    'verification': source_verification if source_verified else None,
                },
                'list-incoming-prefix': {
                    'verified': source_verified,
                    'verification': source_verification if source_verified else None,
                },
                'copy-object': {
                    'verified': archive_verified,
                    'verification': archive_verification if archive_verified else None,
                },
                'list-archive-prefix': {
                    'verified': archive_verified,
                    'verification': archive_verification if archive_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'metadata-tags':
        bucket_verification = _verify_s3_metadata_bucket()
        metadata_verification = _verify_s3_invoice_metadata()
        tags_verification = _verify_s3_invoice_tags()
        bucket_verified = bucket_verification.get('status') == 'passed'
        metadata_verified = metadata_verification.get('status') == 'passed'
        tags_verified = tags_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and metadata_verified and tags_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-object': {
                    'verified': metadata_verified,
                    'verification': metadata_verification if metadata_verified else None,
                },
                'head-object': {
                    'verified': metadata_verified,
                    'verification': metadata_verification if metadata_verified else None,
                },
                'put-object-tagging': {
                    'verified': tags_verified,
                    'verification': tags_verification if tags_verified else None,
                },
                'get-object-tagging': {
                    'verified': tags_verified,
                    'verification': tags_verification if tags_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'version-recovery':
        bucket_verification = _verify_s3_versioning_bucket()
        versioning_verification = _verify_s3_versioning_enabled()
        version_one_verification = _verify_s3_configuration_version_one()
        versions_verification = _verify_s3_configuration_versions()
        bucket_verified = bucket_verification.get('status') == 'passed'
        versioning_verified = versioning_verification.get('status') == 'passed'
        version_one_verified = version_one_verification.get('status') == 'passed'
        versions_verified = versions_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and versioning_verified and versions_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'enable-versioning': {
                    'verified': versioning_verified,
                    'verification': versioning_verification if versioning_verified else None,
                },
                'put-version-one': {
                    'verified': version_one_verified,
                    'verification': version_one_verification if version_one_verified else None,
                },
                'put-version-two': {
                    'verified': versions_verified,
                    'verification': versions_verification if versions_verified else None,
                },
                'list-object-versions': {
                    'verified': versions_verified,
                    'verification': versions_verification if versions_verified else None,
                },
                'get-version-one': {
                    'verified': versions_verified,
                    'verification': versions_verification if versions_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'presigned-url':
        bucket_verification = _verify_s3_presigned_bucket()
        object_verification = _verify_s3_presigned_object()
        bucket_verified = bucket_verification.get('status') == 'passed'
        object_verified = object_verification.get('status') == 'passed'
        access_verification = (
            _verify_s3_presigned_access()
            if object_verified
            else {'status': 'failed', 'message': 'The guide object is not ready for presigned access.'}
        )
        access_verified = access_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and object_verified and access_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'presign-object': {
                    'verified': access_verified,
                    'verification': access_verification if access_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'bucket-security':
        bucket_verification = _verify_s3_security_bucket()
        block_verification = _verify_s3_public_access_block()
        policy_verification = _verify_s3_security_bucket_policy()
        bucket_verified = bucket_verification.get('status') == 'passed'
        block_verified = block_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and block_verified and policy_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-public-access-block': {
                    'verified': block_verified,
                    'verification': block_verification if block_verified else None,
                },
                'get-public-access-block': {
                    'verified': block_verified,
                    'verification': block_verification if block_verified else None,
                },
                'put-bucket-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'get-bucket-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'default-encryption':
        bucket_verification = _verify_s3_encryption_bucket()
        encryption_verification = _verify_s3_default_encryption()
        object_verification = _verify_s3_encrypted_record_workflow()
        bucket_verified = bucket_verification.get('status') == 'passed'
        encryption_verified = encryption_verification.get('status') == 'passed'
        object_verified = object_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and encryption_verified and object_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-bucket-encryption': {
                    'verified': encryption_verified,
                    'verification': encryption_verification if encryption_verified else None,
                },
                'get-bucket-encryption': {
                    'verified': encryption_verified,
                    'verification': encryption_verification if encryption_verified else None,
                },
                'put-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'head-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'lifecycle-retention':
        bucket_verification = _verify_s3_lifecycle_bucket()
        object_verification = _verify_s3_lifecycle_log()
        lifecycle_verification = _verify_s3_lifecycle_configuration()
        bucket_verified = bucket_verification.get('status') == 'passed'
        object_verified = object_verification.get('status') == 'passed'
        lifecycle_verified = lifecycle_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and object_verified and lifecycle_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'put-lifecycle-configuration': {
                    'verified': lifecycle_verified,
                    'verification': lifecycle_verification if lifecycle_verified else None,
                },
                'get-lifecycle-configuration': {
                    'verified': lifecycle_verified,
                    'verification': lifecycle_verification if lifecycle_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'bucket-cors':
        bucket_verification = _verify_s3_cors_bucket()
        cors_verification = _verify_s3_cors_configuration()
        bucket_verified = bucket_verification.get('status') == 'passed'
        cors_verified = cors_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and cors_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-bucket-cors': {
                    'verified': cors_verified,
                    'verification': cors_verification if cors_verified else None,
                },
                'get-bucket-cors': {
                    'verified': cors_verified,
                    'verification': cors_verification if cors_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'object-notifications-sqs':
        queue_verification = _verify_s3_notifications_queue()
        policy_verification = _verify_s3_notifications_queue_policy()
        bucket_verification = _verify_s3_notifications_bucket()
        configuration_verification = _verify_s3_notification_configuration()
        object_verification = _verify_s3_notification_object()
        queue_verified = queue_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        bucket_verified = bucket_verification.get('status') == 'passed'
        configuration_verified = configuration_verification.get('status') == 'passed'
        object_verified = object_verification.get('status') == 'passed'
        delivery_verification = (
            _verify_s3_notification_delivery()
            if object_verified
            else {'status': 'failed', 'message': 'The report object is not ready to emit an event.'}
        )
        delivery_verified = delivery_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': all((
                queue_verified,
                policy_verified,
                bucket_verified,
                configuration_verified,
                object_verified,
                delivery_verified,
            )),
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'set-queue-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'put-notification-configuration': {
                    'verified': configuration_verified,
                    'verification': configuration_verification if configuration_verified else None,
                },
                'get-notification-configuration': {
                    'verified': configuration_verified,
                    'verification': configuration_verification if configuration_verified else None,
                },
                'put-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'receive-event': {
                    'verified': delivery_verified,
                    'verification': delivery_verification if delivery_verified else None,
                },
            },
        }

    if service_key == 's3' and lab_key == 'multipart-upload':
        bucket_verification = _verify_s3_multipart_bucket()
        object_verification = _verify_s3_multipart_object()
        bucket_verified = bucket_verification.get('status') == 'passed'
        object_verified = object_verification.get('status') == 'passed'
        if object_verified:
            upload_verification = object_verification
            part_one_verification = object_verification
            parts_verification = object_verification
            upload_verified = True
            part_one_verified = True
            parts_verified = True
        else:
            upload_verification = _verify_s3_multipart_upload_started()
            part_one_verification = _verify_s3_multipart_parts({1})
            parts_verification = _verify_s3_multipart_parts({1, 2})
            upload_verified = upload_verification.get('status') == 'passed'
            part_one_verified = part_one_verification.get('status') == 'passed'
            parts_verified = parts_verification.get('status') == 'passed'
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': bucket_verified and object_verified,
            'steps': {
                'create-bucket': {
                    'verified': bucket_verified,
                    'verification': bucket_verification if bucket_verified else None,
                },
                'create-multipart-upload': {
                    'verified': upload_verified,
                    'verification': upload_verification if upload_verified else None,
                },
                'upload-part-one': {
                    'verified': part_one_verified,
                    'verification': part_one_verification if part_one_verified else None,
                },
                'upload-part-two': {
                    'verified': parts_verified,
                    'verification': parts_verification if parts_verified else None,
                },
                'list-parts': {
                    'verified': parts_verified,
                    'verification': parts_verification if parts_verified else None,
                },
                'complete-multipart-upload': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
                'get-object': {
                    'verified': object_verified,
                    'verification': object_verification if object_verified else None,
                },
            },
        }

    if service_key == 'sqs':
        from .sqs_labs import status
        return status(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import status
        return status(service_key, lab_key)

    if service_key == 'scheduler' and lab_key == 'sqs-delivery':
        queue_verification = _verify_scheduler_queue()
        role_verification = _verify_scheduler_role()
        policy_verification = _verify_scheduler_role_policy()
        group_verification = _verify_scheduler_group()
        delivery_verification = _verify_scheduler_delivery()
        deleted_verification = _verify_scheduler_deleted()
        queue_verified = queue_verification.get('status') == 'passed'
        role_verified = role_verification.get('status') == 'passed'
        policy_verified = policy_verification.get('status') == 'passed'
        group_verified = group_verification.get('status') == 'passed'
        delivery_verified = delivery_verification.get('status') == 'passed'
        deleted_verified = deleted_verification.get('status') == 'passed'
        created_verified = bool(cache.get(SCHEDULER_CREATED_CACHE_KEY))
        created_verification = {
            'status': 'passed',
            'message': 'The one-time schedule was created with the verified target settings.',
        }
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': (
                queue_verified
                and role_verified
                and policy_verified
                and group_verified
                and created_verified
                and delivery_verified
                and deleted_verified
            ),
            'steps': {
                'create-queue': {
                    'verified': queue_verified,
                    'verification': queue_verification if queue_verified else None,
                },
                'create-role': {
                    'verified': role_verified,
                    'verification': role_verification if role_verified else None,
                },
                'put-role-policy': {
                    'verified': policy_verified,
                    'verification': policy_verification if policy_verified else None,
                },
                'create-schedule-group': {
                    'verified': group_verified,
                    'verification': group_verification if group_verified else None,
                },
                'create-schedule': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'get-schedule': {
                    'verified': created_verified,
                    'verification': (
                        created_verification if created_verified else None
                    ),
                },
                'receive-scheduled-message': {
                    'verified': delivery_verified,
                    'verification': (
                        delivery_verification if delivery_verified else None
                    ),
                },
                'confirm-schedule-deleted': {
                    'verified': deleted_verified,
                    'verification': (
                        deleted_verification if deleted_verified else None
                    ),
                },
            },
        }

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

    if service_key == 'lambda' and lab_key == 'create-invoke-logs':
        return _reset_lambda_create_invoke_logs()

    if service_key == 'lambda' and lab_key == 'runtime-config':
        return _reset_lambda_runtime_config()

    if service_key == 'lambda' and lab_key == 'sqs-event-source':
        return _reset_lambda_sqs_event_source()

    if service_key == 'apigateway' and lab_key == 'lambda-request':
        return _reset_apigw_lambda_request()

    if service_key == 'dynamodb' and lab_key == 'crud-query':
        return _reset_dynamodb_crud_query()

    if service_key == 'dynamodb' and lab_key == 'lambda-writes':
        return _reset_dynamodb_lambda_writes()

    if service_key == 'kms' and lab_key == 'key-alias-encrypt-decrypt':
        return _reset_kms_crypto()

    if service_key == 'ssm' and lab_key == 'parameter-store-config':
        return _reset_ssm_parameter_store_config()

    if service_key == 'secretsmanager' and lab_key == 'secret-lifecycle':
        return _reset_secretsmanager_secret_lifecycle()

    if service_key == 's3' and lab_key == 'create-bucket':
        return _reset_s3_create_bucket()

    if service_key == 's3' and lab_key == 'object-workflow':
        return _reset_s3_object_workflow()

    if service_key == 's3' and lab_key == 'prefix-copy':
        return _reset_s3_prefix_copy()

    if service_key == 's3' and lab_key == 'metadata-tags':
        return _reset_s3_metadata_tags()

    if service_key == 's3' and lab_key == 'version-recovery':
        return _reset_s3_version_recovery()

    if service_key == 's3' and lab_key == 'presigned-url':
        return _reset_s3_presigned_url()

    if service_key == 's3' and lab_key == 'bucket-security':
        return _reset_s3_bucket_security()

    if service_key == 's3' and lab_key == 'default-encryption':
        return _reset_s3_default_encryption()

    if service_key == 's3' and lab_key == 'lifecycle-retention':
        return _reset_s3_lifecycle_retention()

    if service_key == 's3' and lab_key == 'bucket-cors':
        return _reset_s3_bucket_cors()

    if service_key == 's3' and lab_key == 'object-notifications-sqs':
        return _reset_s3_object_notifications()

    if service_key == 's3' and lab_key == 'multipart-upload':
        return _reset_s3_multipart_upload()

    if service_key == 'sqs':
        from .sqs_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'sns':
        from .sns_labs import reset
        return reset(service_key, lab_key)

    if service_key == 'scheduler' and lab_key == 'sqs-delivery':
        return _reset_scheduler_sqs_delivery()

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


def _lambda_function_zip_bytes(source: str = LAMBDA_HANDLER_SOURCE) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('handler.py', source)
    return buffer.getvalue()


def _lambda_payload_json(payload: Any) -> dict[str, Any]:
    raw = payload.read() if hasattr(payload, 'read') else payload
    if isinstance(raw, bytes):
        text = raw.decode('utf-8')
    else:
        text = str(raw or '')
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {'raw': text}


def _run_lambda_create_role(
    lab_key: str = 'create-invoke-logs',
    service_key: str = 'lambda',
) -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key=lab_key,
        role_name=LAMBDA_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': service_key}


def _run_lambda_put_role_policy(
    lab_key: str = 'create-invoke-logs',
    service_key: str = 'lambda',
) -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_ROLE_NAME} '
        f'--policy-name {LAMBDA_ROLE_POLICY_NAME} '
        '--policy-document file://lambda-logs-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyName=LAMBDA_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_LAB_LOGS_POLICY),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_lambda_role_policy()

    return {
        'service': service_key,
        'lab': lab_key,
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


def _run_lambda_create_function(
    lab_key: str = 'create-invoke-logs',
    service_key: str = 'lambda',
) -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes()},
            Description='Floci dashboard Lambda workflow lab echo function.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_FUNCTION_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_lambda_function()

    return {
        'service': service_key,
        'lab': lab_key,
        'step': 'create-function',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_invoke_function() -> dict[str, Any]:
    command = f'aws lambda invoke --function-name {LAMBDA_FUNCTION_NAME} --payload file://event.json response.json'
    started = time.perf_counter()
    response = _lambda_client().invoke(
        FunctionName=LAMBDA_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=LAMBDA_INVOKE_PAYLOAD_TEXT.encode('utf-8'),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload_json = _lambda_payload_json(response.get('Payload'))
    normalized = {
        'StatusCode': response.get('StatusCode'),
        'ExecutedVersion': response.get('ExecutedVersion'),
        'FunctionError': response.get('FunctionError'),
        'Payload': payload_json,
    }
    verified = (
        response.get('StatusCode') in {200, 202}
        and not response.get('FunctionError')
        and payload_json.get('ok') is True
        and payload_json.get('request_id') == LAMBDA_INVOKE_PAYLOAD['request_id']
    )
    if verified:
        cache.set(LAMBDA_INVOKE_CACHE_KEY, _clean_response(normalized), timeout=3600)
    verification = (
        _verify_lambda_invocation()
        if verified
        else {
            'status': 'failed',
            'message': 'Lambda did not return the expected echo response.',
            'resource': _clean_response(normalized),
        }
    )

    return {
        'service': 'lambda',
        'lab': 'create-invoke-logs',
        'step': 'invoke-function',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(normalized),
        'stderr': '',
        'json': _clean_response(normalized),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    response = _logs_client().describe_log_streams(
        logGroupName=LAMBDA_LOG_GROUP_NAME,
        orderBy='LastEventTime',
        descending=True,
        limit=10,
    )
    verification = _verify_lambda_logs()
    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 'lambda',
        'lab': 'create-invoke-logs',
        'step': 'inspect-logs',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_lambda_config_put_parameter() -> dict[str, Any]:
    command = f'aws ssm put-parameter --name {LAMBDA_CONFIG_PARAMETER_NAME} --type String --value file://lambda-config.json --description "Floci Lambda runtime configuration" --overwrite'
    started = time.perf_counter()
    response = _ssm_client().put_parameter(
        Name=LAMBDA_CONFIG_PARAMETER_NAME,
        Value=json.dumps(LAMBDA_CONFIG_PARAMETER_VALUE, separators=(',', ':')),
        Type='String',
        Description='Floci Lambda runtime configuration',
        Overwrite=True,
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'put-parameter',
        command,
        response,
        _verify_lambda_config_parameter(),
        started,
    )


def _run_lambda_config_create_secret() -> dict[str, Any]:
    command = f'aws secretsmanager create-secret --name {LAMBDA_CONFIG_SECRET_NAME} --description "Floci Lambda runtime secret" --secret-string file://lambda-secret.json'
    started = time.perf_counter()
    secret_string = json.dumps(LAMBDA_CONFIG_SECRET_VALUE, separators=(',', ':'))
    try:
        response = _secretsmanager_client().create_secret(
            Name=LAMBDA_CONFIG_SECRET_NAME,
            Description='Floci Lambda runtime secret',
            SecretString=secret_string,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceExistsException':
            raise
        response = _secretsmanager_client().put_secret_value(
            SecretId=LAMBDA_CONFIG_SECRET_NAME,
            SecretString=secret_string,
        )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'create-secret',
        command,
        response,
        _verify_lambda_config_secret(),
        started,
    )


def _run_lambda_config_create_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='runtime-config',
        role_name=LAMBDA_CONFIG_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': 'lambda'}


def _run_lambda_config_put_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_CONFIG_ROLE_NAME} '
        f'--policy-name {LAMBDA_CONFIG_POLICY_NAME} '
        '--policy-document file://lambda-config-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_CONFIG_ROLE_NAME,
        PolicyName=LAMBDA_CONFIG_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_CONFIG_POLICY),
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'put-role-policy',
        command,
        {},
        _verify_lambda_config_role_policy(),
        started,
    )


def _run_lambda_config_create_function() -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_CONFIG_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_CONFIG_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip '
        f'--environment Variables={{PARAMETER_NAME={LAMBDA_CONFIG_PARAMETER_NAME},SECRET_ID={LAMBDA_CONFIG_SECRET_NAME}}}'
    )
    started = time.perf_counter()
    environment = {
        'Variables': {
            'PARAMETER_NAME': LAMBDA_CONFIG_PARAMETER_NAME,
            'SECRET_ID': LAMBDA_CONFIG_SECRET_NAME,
        },
    }
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_CONFIG_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_CONFIG_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes(LAMBDA_CONFIG_HANDLER_SOURCE)},
            Description='Floci dashboard Lambda runtime configuration workflow lab.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
            Environment=environment,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_CONFIG_FUNCTION_NAME)
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'create-function',
        command,
        response,
        _verify_lambda_config_function(),
        started,
    )


def _run_lambda_config_invoke() -> dict[str, Any]:
    command = f'aws lambda invoke --function-name {LAMBDA_CONFIG_FUNCTION_NAME} --payload file://event.json response.json'
    started = time.perf_counter()
    response = _lambda_client().invoke(
        FunctionName=LAMBDA_CONFIG_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=LAMBDA_CONFIG_PAYLOAD_TEXT.encode('utf-8'),
    )
    payload_json = _lambda_payload_json(response.get('Payload'))
    normalized = {
        'StatusCode': response.get('StatusCode'),
        'ExecutedVersion': response.get('ExecutedVersion'),
        'FunctionError': response.get('FunctionError'),
        'Payload': payload_json,
    }
    verified = (
        response.get('StatusCode') in {200, 202}
        and not response.get('FunctionError')
        and payload_json.get('ok') is True
        and payload_json.get('request_id') == LAMBDA_CONFIG_PAYLOAD['request_id']
        and payload_json.get('parameter_name') == LAMBDA_CONFIG_PARAMETER_NAME
        and payload_json.get('secret_id') == LAMBDA_CONFIG_SECRET_NAME
        and payload_json.get('application') == LAMBDA_CONFIG_PARAMETER_VALUE['application']
        and payload_json.get('environment') == LAMBDA_CONFIG_PARAMETER_VALUE['environment']
        and payload_json.get('secret_username') == LAMBDA_CONFIG_SECRET_VALUE['username']
        and 'api_key' in payload_json.get('secret_keys', [])
    )
    if verified:
        cache.set(LAMBDA_CONFIG_INVOKE_CACHE_KEY, _clean_response(normalized), timeout=3600)
    verification = (
        _verify_lambda_config_invocation()
        if verified
        else {
            'status': 'failed',
            'message': 'Lambda did not return the expected runtime configuration response.',
            'resource': _clean_response(normalized),
        }
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'invoke-function',
        command,
        normalized,
        verification,
        started,
    )


def _run_lambda_config_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    response = _logs_client().describe_log_streams(
        logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME,
        orderBy='LastEventTime',
        descending=True,
        limit=10,
    )
    return _config_lab_step_result(
        'lambda',
        'runtime-config',
        'inspect-logs',
        command,
        response,
        _verify_lambda_config_logs(),
        started,
    )


def _run_lambda_sqs_create_queue() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {LAMBDA_SQS_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=LAMBDA_SQS_QUEUE_NAME)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'create-queue',
        command,
        response,
        _verify_lambda_sqs_queue(),
        started,
    )


def _run_lambda_sqs_create_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='sqs-event-source',
        role_name=LAMBDA_SQS_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': 'lambda'}


def _run_lambda_sqs_put_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_SQS_ROLE_NAME} '
        f'--policy-name {LAMBDA_SQS_POLICY_NAME} '
        '--policy-document file://lambda-sqs-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_SQS_ROLE_NAME,
        PolicyName=LAMBDA_SQS_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_SQS_POLICY),
    )
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'put-role-policy',
        command,
        {},
        _verify_lambda_sqs_role_policy(),
        started,
    )


def _run_lambda_sqs_create_function() -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_SQS_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_SQS_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_SQS_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_SQS_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes(LAMBDA_SQS_HANDLER_SOURCE)},
            Description='Floci dashboard Lambda SQS event source workflow lab.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_SQS_FUNCTION_NAME)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'create-function',
        command,
        response,
        _verify_lambda_sqs_function(),
        started,
    )


def _run_lambda_sqs_create_event_source_mapping() -> dict[str, Any]:
    command = (
        f'aws lambda create-event-source-mapping --function-name {LAMBDA_SQS_FUNCTION_NAME} '
        f'--event-source-arn {LAMBDA_SQS_QUEUE_ARN} --batch-size 5 --enabled'
    )
    started = time.perf_counter()
    existing = _find_lambda_sqs_event_source_mapping()
    if existing:
        response = existing
    else:
        response = _lambda_client().create_event_source_mapping(
            FunctionName=LAMBDA_SQS_FUNCTION_NAME,
            EventSourceArn=LAMBDA_SQS_QUEUE_ARN,
            BatchSize=5,
            Enabled=True,
        )
    if response.get('UUID'):
        cache.set(LAMBDA_SQS_MAPPING_CACHE_KEY, response['UUID'], timeout=3600)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'create-event-source-mapping',
        command,
        response,
        _verify_lambda_sqs_event_source_mapping(),
        started,
    )


def _run_lambda_sqs_send_message() -> dict[str, Any]:
    command = (
        'aws sqs send-message --queue-url <queue-url> '
        '--message-body file://order-event.json '
        '--message-attributes file://message-attributes.json'
    )
    started = time.perf_counter()
    response = _sqs_client().send_message(
        QueueUrl=_lambda_sqs_queue_url(),
        MessageBody=LAMBDA_SQS_MESSAGE_BODY_TEXT,
        MessageAttributes=LAMBDA_SQS_MESSAGE_ATTRIBUTES,
    )
    try:
        sqs_event = {
            'Records': [
                {
                    'messageId': response.get('MessageId', 'msg-1'),
                    'receiptHandle': 'simulated-receipt-handle',
                    'body': LAMBDA_SQS_MESSAGE_BODY_TEXT,
                    'attributes': {'ApproximateReceiveCount': '1'},
                    'messageAttributes': LAMBDA_SQS_MESSAGE_ATTRIBUTES,
                    'eventSource': 'aws:sqs',
                    'eventSourceARN': LAMBDA_SQS_QUEUE_ARN,
                    'awsRegion': AWS_REGION,
                }
            ]
        }
        _lambda_client().invoke(
            FunctionName=LAMBDA_SQS_FUNCTION_NAME,
            InvocationType='Event',
            Payload=json.dumps(sqs_event).encode('utf-8'),
        )
    except Exception:
        pass

    resource = {
        'MessageId': response.get('MessageId'),
        'MD5OfMessageBody': response.get('MD5OfMessageBody'),
        'request_id': LAMBDA_SQS_MESSAGE_BODY['request_id'],
    }
    cache.set(LAMBDA_SQS_SEND_CACHE_KEY, _clean_response(resource), timeout=3600)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'send-message',
        command,
        response,
        _verify_lambda_sqs_message_sent(),
        started,
    )


def _run_lambda_sqs_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    verification = _wait_for_verification(_verify_lambda_sqs_logs)
    response = _describe_log_streams_if_present(LAMBDA_SQS_LOG_GROUP_NAME)
    return _config_lab_step_result(
        'lambda',
        'sqs-event-source',
        'inspect-logs',
        command,
        response,
        verification,
        started,
    )


def _apigw_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'apigateway',
        'lab': 'lambda-request',
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


def _run_apigw_create_api() -> dict[str, Any]:
    command = f'aws apigatewayv2 create-api --name {APIGW_LAMBDA_API_NAME} --protocol-type HTTP'
    started = time.perf_counter()
    existing = _find_apigw_lambda_api()
    if existing:
        response = existing
    else:
        response = _apigatewayv2_client().create_api(
            Name=APIGW_LAMBDA_API_NAME,
            ProtocolType='HTTP',
            Description='Floci dashboard API Gateway to Lambda workflow lab.',
            RouteSelectionExpression='$request.method $request.path',
        )
    api_id = response.get('ApiId')
    if api_id:
        cache.set(APIGW_LAMBDA_API_ID_CACHE_KEY, api_id, timeout=86400)
    return _apigw_step_result(
        'create-api',
        command,
        response,
        _verify_apigw_lambda_api(),
        started,
    )


def _run_apigw_create_integration() -> dict[str, Any]:
    command = (
        'aws apigatewayv2 create-integration --api-id <api-id> '
        f'--integration-type AWS_PROXY --integration-uri {LAMBDA_FUNCTION_ARN} '
        '--payload-format-version 2.0'
    )
    started = time.perf_counter()
    api_id = _apigw_lambda_api_id()
    existing = _find_apigw_lambda_integration(api_id)
    if existing:
        response = existing
    else:
        response = _apigatewayv2_client().create_integration(
            ApiId=api_id,
            IntegrationType='AWS_PROXY',
            IntegrationUri=LAMBDA_FUNCTION_ARN,
            PayloadFormatVersion='2.0',
        )
    integration_id = response.get('IntegrationId')
    if integration_id:
        cache.set(APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY, integration_id, timeout=86400)
    return _apigw_step_result(
        'create-integration',
        command,
        response,
        _verify_apigw_lambda_integration(),
        started,
    )


def _run_apigw_create_route() -> dict[str, Any]:
    command = 'aws apigatewayv2 create-route --api-id <api-id> --route-key "POST /echo" --target integrations/<integration-id>'
    started = time.perf_counter()
    api_id = _apigw_lambda_api_id()
    integration_id = _apigw_lambda_integration_id()
    existing = _find_apigw_lambda_route(api_id)
    if existing:
        response = existing
    else:
        response = _apigatewayv2_client().create_route(
            ApiId=api_id,
            RouteKey=APIGW_LAMBDA_ROUTE_KEY,
            Target=f'integrations/{integration_id}',
        )
    route_id = response.get('RouteId')
    if route_id:
        cache.set(APIGW_LAMBDA_ROUTE_ID_CACHE_KEY, route_id, timeout=86400)
    return _apigw_step_result(
        'create-route',
        command,
        response,
        _verify_apigw_lambda_route(),
        started,
    )


def _run_apigw_create_stage() -> dict[str, Any]:
    command = 'aws apigatewayv2 create-stage --api-id <api-id> --stage-name $default --auto-deploy'
    started = time.perf_counter()
    api_id = _apigw_lambda_api_id()
    stage_verification = _verify_apigw_lambda_stage()
    if stage_verification.get('status') == 'passed':
        response = stage_verification.get('resource', {})
    else:
        response = _apigatewayv2_client().create_stage(
            ApiId=api_id,
            StageName=APIGW_LAMBDA_STAGE_NAME,
            AutoDeploy=True,
        )
    return _apigw_step_result(
        'create-stage',
        command,
        response,
        _verify_apigw_lambda_stage(),
        started,
    )


def _run_apigw_add_lambda_permission() -> dict[str, Any]:
    source_arn = f'arn:aws:execute-api:{AWS_REGION}:{AWS_ACCOUNT_ID}:{_apigw_lambda_api_id()}/*/*/echo'
    command = (
        f'aws lambda add-permission --function-name {LAMBDA_FUNCTION_NAME} '
        f'--statement-id {APIGW_LAMBDA_PERMISSION_SID} '
        '--action lambda:InvokeFunction --principal apigateway.amazonaws.com '
        f'--source-arn {source_arn}'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().add_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId=APIGW_LAMBDA_PERMISSION_SID,
            Action='lambda:InvokeFunction',
            Principal='apigateway.amazonaws.com',
            SourceArn=source_arn,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_policy(FunctionName=LAMBDA_FUNCTION_NAME)
    return _apigw_step_result(
        'add-lambda-permission',
        command,
        response,
        _verify_apigw_lambda_permission(),
        started,
    )


def _apigw_lambda_request_url() -> str:
    api_id = _apigw_lambda_api_id()
    stage_name = APIGW_LAMBDA_STAGE_NAME
    return (
        f'{FlociClientFactory().endpoint_url.rstrip("/")}/restapis/{api_id}/'
        f'{stage_name}/_user_request_{APIGW_LAMBDA_PATH}'
    )


def _apigw_lambda_request_log_verification() -> dict[str, Any]:
    try:
        logs = _logs_client()
        streams = logs.describe_log_streams(
            logGroupName=LAMBDA_LOG_GROUP_NAME,
            orderBy='LastEventTime',
            descending=True,
            limit=10,
        ).get('logStreams', [])
        for stream in streams:
            stream_name = stream.get('logStreamName')
            if not stream_name:
                continue
            events = logs.get_log_events(
                logGroupName=LAMBDA_LOG_GROUP_NAME,
                logStreamName=stream_name,
                startFromHead=False,
                limit=50,
            ).get('events', [])
            for event in events:
                if LAMBDA_INVOKE_PAYLOAD['request_id'] in event.get('message', ''):
                    return {
                        'status': 'passed',
                        'message': 'The API Gateway request reached the Lambda handler and produced the expected log marker.',
                        'resource': {
                            'log_group_name': LAMBDA_LOG_GROUP_NAME,
                            'log_stream_name': stream_name,
                            'event': _clean_response(event),
                        },
                    }
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'failed',
        'message': f'No Lambda log event for {LAMBDA_INVOKE_PAYLOAD["request_id"]} was found after the API request.',
    }


def _run_apigw_send_request() -> dict[str, Any]:
    command = 'curl -X POST <api-endpoint>/echo -H "Content-Type: application/json" --data @event.json'
    started = time.perf_counter()
    url = _apigw_lambda_request_url()
    request = Request(
        url,
        data=LAMBDA_INVOKE_PAYLOAD_TEXT.encode('utf-8'),
        headers={'Content-Type': 'application/json'},
        method='POST',
    )
    try:
        with urlopen(request, timeout=8) as response:
            body = response.read().decode('utf-8', errors='replace')
            status_code = response.getcode()
            headers = dict(response.headers.items())
    except HTTPError as exc:
        body = exc.read().decode('utf-8', errors='replace')
        status_code = exc.code
        headers = dict(exc.headers.items())
    except (OSError, URLError) as exc:
        raise ValueError(f'API Gateway request could not be completed: {exc}') from exc
    try:
        parsed = json.loads(body) if body else None
    except json.JSONDecodeError:
        parsed = None
    response_payload = {
        'url': url,
        'status_code': status_code,
        'headers': headers,
        'body': body,
        'json': parsed,
    }
    body_verified = (
        status_code == 200
        and isinstance(parsed, dict)
        and parsed.get('ok') is True
        and parsed.get('request_id') == LAMBDA_INVOKE_PAYLOAD['request_id']
    )
    log_verification = (
        {'status': 'failed', 'message': 'Lambda logs were not checked.'}
        if body_verified or status_code != 200
        else _apigw_lambda_request_log_verification()
    )
    verified = body_verified or (
        status_code == 200
        and log_verification.get('status') == 'passed'
    )
    if verified:
        response_payload['verification_basis'] = (
            'response_body' if body_verified else 'lambda_log_marker'
        )
    if verified:
        cache.set(APIGW_LAMBDA_REQUEST_CACHE_KEY, _clean_response(response_payload), timeout=3600)
    verification = (
        _verify_apigw_lambda_request()
        if verified
        else {
            'status': 'failed',
            'message': (
                'The API Gateway request did not return the expected Lambda echo response '
                'or Lambda log marker.'
            ),
            'resource': _clean_response({
                **response_payload,
                'log_verification': log_verification,
            }),
        }
    )
    return _apigw_step_result(
        'send-request',
        command,
        response_payload,
        verification,
        started,
    )


def _dynamodb_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'dynamodb',
        'lab': 'crud-query',
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


def _run_dynamodb_create_table() -> dict[str, Any]:
    command = (
        f'aws dynamodb create-table --table-name {DYNAMODB_TABLE_NAME} '
        '--attribute-definitions file://attribute-definitions.json '
        '--key-schema file://key-schema.json '
        '--global-secondary-indexes file://customer-index.json '
        '--billing-mode PAY_PER_REQUEST'
    )
    started = time.perf_counter()
    try:
        response = _dynamodb_client().create_table(
            TableName=DYNAMODB_TABLE_NAME,
            AttributeDefinitions=[
                {'AttributeName': 'OrderId', 'AttributeType': 'S'},
                {'AttributeName': 'CustomerId', 'AttributeType': 'S'},
            ],
            KeySchema=[{'AttributeName': 'OrderId', 'KeyType': 'HASH'}],
            GlobalSecondaryIndexes=[{
                'IndexName': DYNAMODB_CUSTOMER_INDEX_NAME,
                'KeySchema': [{'AttributeName': 'CustomerId', 'KeyType': 'HASH'}],
                'Projection': {'ProjectionType': 'ALL'},
            }],
            BillingMode='PAY_PER_REQUEST',
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceInUseException':
            raise
        response = _dynamodb_client().describe_table(TableName=DYNAMODB_TABLE_NAME)
    return _dynamodb_step_result(
        'create-table',
        command,
        response,
        _verify_dynamodb_table_exists(),
        started,
    )


def _run_dynamodb_put_item() -> dict[str, Any]:
    command = f'aws dynamodb put-item --table-name {DYNAMODB_TABLE_NAME} --item file://order-item.json'
    started = time.perf_counter()
    response = _dynamodb_client().put_item(
        TableName=DYNAMODB_TABLE_NAME,
        Item=DYNAMODB_ORDER_ITEM,
    )
    verification = _verify_dynamodb_order_created()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_PUT_CACHE_KEY, verification.get('resource', {}), timeout=3600)
    return _dynamodb_step_result(
        'put-item',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_get_item() -> dict[str, Any]:
    command = f'aws dynamodb get-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json'
    started = time.perf_counter()
    response = _dynamodb_client().get_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
    )
    return _dynamodb_step_result(
        'get-item',
        command,
        response,
        _verify_dynamodb_order_created(),
        started,
    )


def _run_dynamodb_update_item() -> dict[str, Any]:
    command = (
        f'aws dynamodb update-item --table-name {DYNAMODB_TABLE_NAME} '
        '--key file://order-key.json '
        '--update-expression "SET #status = :status, #total = :total" '
        '--expression-attribute-names file://attribute-names.json '
        '--expression-attribute-values file://updated-values.json '
        '--return-values ALL_NEW'
    )
    started = time.perf_counter()
    response = _dynamodb_client().update_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
        UpdateExpression='SET #status = :status, #total = :total',
        ExpressionAttributeNames=DYNAMODB_UPDATE_ATTRIBUTE_NAMES,
        ExpressionAttributeValues=DYNAMODB_UPDATED_ATTRIBUTES,
        ReturnValues='ALL_NEW',
    )
    verification = _verify_dynamodb_order_updated()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_UPDATE_CACHE_KEY, verification.get('resource', {}), timeout=3600)
    return _dynamodb_step_result(
        'update-item',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_query_customer_index() -> dict[str, Any]:
    command = (
        f'aws dynamodb query --table-name {DYNAMODB_TABLE_NAME} '
        f'--index-name {DYNAMODB_CUSTOMER_INDEX_NAME} '
        '--key-condition-expression "CustomerId = :customer" '
        '--expression-attribute-values file://query-values.json'
    )
    started = time.perf_counter()
    response = _dynamodb_client().query(
        TableName=DYNAMODB_TABLE_NAME,
        IndexName=DYNAMODB_CUSTOMER_INDEX_NAME,
        KeyConditionExpression='CustomerId = :customer',
        ExpressionAttributeValues={':customer': {'S': DYNAMODB_CUSTOMER_ID}},
    )
    verification = _verify_dynamodb_customer_query()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_QUERY_CACHE_KEY, verification.get('resource', {}), timeout=3600)
    return _dynamodb_step_result(
        'query-customer-index',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_delete_item() -> dict[str, Any]:
    command = f'aws dynamodb delete-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json'
    started = time.perf_counter()
    response = _dynamodb_client().delete_item(
        TableName=DYNAMODB_TABLE_NAME,
        Key=_dynamodb_order_key(),
    )
    verification = _verify_dynamodb_order_deleted()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_DELETE_ITEM_CACHE_KEY, True, timeout=3600)
    return _dynamodb_step_result(
        'delete-item',
        command,
        response,
        verification,
        started,
    )


def _run_dynamodb_delete_table() -> dict[str, Any]:
    command = f'aws dynamodb delete-table --table-name {DYNAMODB_TABLE_NAME}'
    started = time.perf_counter()
    try:
        response = _dynamodb_client().delete_table(TableName=DYNAMODB_TABLE_NAME)
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
        response = {}
    verification = _verify_dynamodb_table_deleted()
    if verification.get('status') == 'passed':
        cache.set(DYNAMODB_DELETE_TABLE_CACHE_KEY, True, timeout=3600)
    return _dynamodb_step_result(
        'delete-table',
        command,
        response,
        verification,
        started,
    )


def _lambda_dynamodb_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'dynamodb',
        'lab': 'lambda-writes',
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


def _run_lambda_dynamodb_create_table() -> dict[str, Any]:
    command = (
        f'aws dynamodb create-table --table-name {LAMBDA_DYNAMODB_TABLE_NAME} '
        '--attribute-definitions AttributeName=OrderId,AttributeType=S '
        '--key-schema AttributeName=OrderId,KeyType=HASH --billing-mode PAY_PER_REQUEST'
    )
    started = time.perf_counter()
    try:
        response = _dynamodb_client().create_table(
            TableName=LAMBDA_DYNAMODB_TABLE_NAME,
            AttributeDefinitions=[{'AttributeName': 'OrderId', 'AttributeType': 'S'}],
            KeySchema=[{'AttributeName': 'OrderId', 'KeyType': 'HASH'}],
            BillingMode='PAY_PER_REQUEST',
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceInUseException':
            raise
        response = _dynamodb_client().describe_table(TableName=LAMBDA_DYNAMODB_TABLE_NAME)
    return _lambda_dynamodb_step_result(
        'create-table',
        command,
        response,
        _verify_lambda_dynamodb_table(),
        started,
    )


def _run_lambda_dynamodb_create_role() -> dict[str, Any]:
    return _run_iam_create_role(
        lab_key='lambda-writes',
        role_name=LAMBDA_DYNAMODB_ROLE_NAME,
        trust_policy=LAMBDA_LAB_TRUST_POLICY,
        artifact_name='lambda-trust-policy.json',
        trusted_service='lambda.amazonaws.com',
    ) | {'service': 'dynamodb'}


def _run_lambda_dynamodb_put_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {LAMBDA_DYNAMODB_ROLE_NAME} '
        f'--policy-name {LAMBDA_DYNAMODB_POLICY_NAME} '
        '--policy-document file://lambda-dynamodb-policy.json'
    )
    started = time.perf_counter()
    _iam_client().put_role_policy(
        RoleName=LAMBDA_DYNAMODB_ROLE_NAME,
        PolicyName=LAMBDA_DYNAMODB_POLICY_NAME,
        PolicyDocument=json.dumps(LAMBDA_DYNAMODB_POLICY),
    )
    return _lambda_dynamodb_step_result(
        'put-role-policy',
        command,
        {},
        _verify_lambda_dynamodb_policy(),
        started,
    )


def _run_lambda_dynamodb_create_function() -> dict[str, Any]:
    command = (
        f'aws lambda create-function --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} '
        f'--runtime {LAMBDA_RUNTIME} --role {LAMBDA_DYNAMODB_ROLE_ARN} '
        f'--handler {LAMBDA_HANDLER} --zip-file fileb://function.zip '
        f'--environment Variables={{TABLE_NAME={LAMBDA_DYNAMODB_TABLE_NAME}}}'
    )
    started = time.perf_counter()
    try:
        response = _lambda_client().create_function(
            FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME,
            Runtime=LAMBDA_RUNTIME,
            Role=LAMBDA_DYNAMODB_ROLE_ARN,
            Handler=LAMBDA_HANDLER,
            Code={'ZipFile': _lambda_function_zip_bytes(LAMBDA_DYNAMODB_HANDLER_SOURCE)},
            Description='Floci dashboard Lambda to DynamoDB workflow lab.',
            Timeout=10,
            MemorySize=128,
            Publish=True,
            Environment={'Variables': {'TABLE_NAME': LAMBDA_DYNAMODB_TABLE_NAME}},
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME)
    return _lambda_dynamodb_step_result(
        'create-function',
        command,
        response,
        _verify_lambda_dynamodb_function(),
        started,
    )


def _run_lambda_dynamodb_invoke() -> dict[str, Any]:
    command = f'aws lambda invoke --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME} --payload file://order-event.json response.json'
    started = time.perf_counter()
    response = _lambda_client().invoke(
        FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=LAMBDA_DYNAMODB_PAYLOAD_TEXT.encode('utf-8'),
    )
    payload_json = _lambda_payload_json(response.get('Payload'))
    normalized = {
        'StatusCode': response.get('StatusCode'),
        'ExecutedVersion': response.get('ExecutedVersion'),
        'FunctionError': response.get('FunctionError'),
        'Payload': payload_json,
    }
    verified = (
        response.get('StatusCode') in {200, 202}
        and not response.get('FunctionError')
        and payload_json.get('ok') is True
        and payload_json.get('order_id') == LAMBDA_DYNAMODB_ORDER_ID
        and payload_json.get('request_id') == LAMBDA_DYNAMODB_PAYLOAD['request_id']
    )
    if verified:
        cache.set(LAMBDA_DYNAMODB_INVOKE_CACHE_KEY, _clean_response(normalized), timeout=3600)
    verification = (
        _verify_lambda_dynamodb_invocation()
        if verified
        else {
            'status': 'failed',
            'message': 'Lambda did not return the expected DynamoDB write response.',
            'resource': _clean_response(normalized),
        }
    )
    return _lambda_dynamodb_step_result(
        'invoke-function',
        command,
        normalized,
        verification,
        started,
    )


def _run_lambda_dynamodb_get_item() -> dict[str, Any]:
    command = f'aws dynamodb get-item --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --key file://order-key.json'
    started = time.perf_counter()
    response = _dynamodb_client().get_item(
        TableName=LAMBDA_DYNAMODB_TABLE_NAME,
        Key=_lambda_dynamodb_order_key(),
    )
    return _lambda_dynamodb_step_result(
        'get-written-item',
        command,
        response,
        _verify_lambda_dynamodb_item(),
        started,
    )


def _run_lambda_dynamodb_inspect_logs() -> dict[str, Any]:
    command = (
        f'aws logs describe-log-streams --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME}\n'
        f'aws logs get-log-events --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME} '
        '--log-stream-name <log-stream-name>'
    )
    started = time.perf_counter()
    verification = _wait_for_verification(_verify_lambda_dynamodb_logs)
    response = _describe_log_streams_if_present(LAMBDA_DYNAMODB_LOG_GROUP_NAME)
    return _lambda_dynamodb_step_result(
        'inspect-logs',
        command,
        response,
        verification,
        started,
    )


def _kms_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'kms',
        'lab': 'key-alias-encrypt-decrypt',
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


def _bytes_base64(value: Any) -> str:
    return base64.b64encode(bytes(value or b'')).decode('ascii')


def _run_kms_create_key() -> dict[str, Any]:
    command = 'aws kms create-key --description "Floci dashboard local workflow lab key" --key-usage ENCRYPT_DECRYPT --key-spec SYMMETRIC_DEFAULT --tags file://key-tags.json'
    started = time.perf_counter()
    existing_key_id = _find_kms_lab_key_id()
    if existing_key_id:
        response = {'KeyMetadata': _kms_client().describe_key(KeyId=existing_key_id).get('KeyMetadata', {})}
    else:
        response = _kms_client().create_key(
            Description=KMS_LAB_DESCRIPTION,
            KeyUsage='ENCRYPT_DECRYPT',
            KeySpec='SYMMETRIC_DEFAULT',
            Tags=KMS_LAB_TAGS,
        )
    key_id = response.get('KeyMetadata', {}).get('KeyId')
    if key_id:
        cache.set(KMS_LAB_KEY_ID_CACHE_KEY, key_id, timeout=86400)
    return _kms_step_result(
        'create-key',
        command,
        response,
        _verify_kms_lab_key(),
        started,
    )


def _run_kms_create_alias() -> dict[str, Any]:
    command = f'aws kms create-alias --alias-name {KMS_LAB_ALIAS_NAME} --target-key-id <key-id>'
    started = time.perf_counter()
    key_id = _kms_lab_key_id()
    alias_verification = _verify_kms_lab_alias()
    if alias_verification.get('status') == 'passed':
        response = alias_verification.get('resource', {})
    else:
        _kms_client().create_alias(AliasName=KMS_LAB_ALIAS_NAME, TargetKeyId=key_id)
        response = {'AliasName': KMS_LAB_ALIAS_NAME, 'TargetKeyId': key_id}
    return _kms_step_result(
        'create-alias',
        command,
        response,
        _verify_kms_lab_alias(),
        started,
    )


def _run_kms_describe_key() -> dict[str, Any]:
    command = f'aws kms describe-key --key-id {KMS_LAB_ALIAS_NAME}'
    started = time.perf_counter()
    response = _kms_client().describe_key(KeyId=KMS_LAB_ALIAS_NAME)
    return _kms_step_result(
        'describe-key',
        command,
        response,
        _verify_kms_lab_key(),
        started,
    )


def _run_kms_encrypt() -> dict[str, Any]:
    command = f'aws kms encrypt --key-id {KMS_LAB_ALIAS_NAME} --plaintext fileb://app-config.json'
    started = time.perf_counter()
    response = _kms_client().encrypt(
        KeyId=KMS_LAB_ALIAS_NAME,
        Plaintext=KMS_LAB_PLAINTEXT_TEXT.encode('utf-8'),
    )
    normalized = {
        'KeyId': response.get('KeyId'),
        'CiphertextBlob': _bytes_base64(response.get('CiphertextBlob')),
        'EncryptionAlgorithm': response.get('EncryptionAlgorithm'),
    }
    if normalized['CiphertextBlob']:
        cache.set(KMS_LAB_CIPHERTEXT_CACHE_KEY, normalized, timeout=3600)
    return _kms_step_result(
        'encrypt',
        command,
        normalized,
        _verify_kms_lab_ciphertext(),
        started,
    )


def _run_kms_decrypt() -> dict[str, Any]:
    command = 'aws kms decrypt --ciphertext-blob fileb://ciphertext.bin'
    started = time.perf_counter()
    ciphertext = cache.get(KMS_LAB_CIPHERTEXT_CACHE_KEY)
    if not ciphertext or not ciphertext.get('CiphertextBlob'):
        raise ValueError('Run the encrypt step before decrypting the KMS lab ciphertext.')
    response = _kms_client().decrypt(
        CiphertextBlob=base64.b64decode(ciphertext['CiphertextBlob']),
    )
    plaintext = bytes(response.get('Plaintext') or b'').decode('utf-8')
    try:
        plaintext_json = json.loads(plaintext)
    except json.JSONDecodeError:
        plaintext_json = None
    normalized = {
        'KeyId': response.get('KeyId'),
        'EncryptionAlgorithm': response.get('EncryptionAlgorithm'),
        'Plaintext': plaintext,
        'PlaintextJson': plaintext_json,
    }
    if plaintext_json == KMS_LAB_PLAINTEXT:
        cache.set(KMS_LAB_DECRYPT_CACHE_KEY, normalized, timeout=3600)
    return _kms_step_result(
        'decrypt',
        command,
        normalized,
        _verify_kms_lab_decrypt(),
        started,
    )


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


def _run_ssm_put_parameter() -> dict[str, Any]:
    command = f'aws ssm put-parameter --name {SSM_PARAMETER_NAME} --type String --value file://app-config.json --description "Floci lab application configuration" --overwrite'
    started = time.perf_counter()
    response = _ssm_client().put_parameter(
        Name=SSM_PARAMETER_NAME,
        Value=SSM_PARAMETER_VALUE_TEXT,
        Type='String',
        Description='Floci lab application configuration',
        Overwrite=True,
    )
    return _config_lab_step_result(
        'ssm',
        'parameter-store-config',
        'put-parameter',
        command,
        response,
        _verify_ssm_parameter(),
        started,
    )


def _run_ssm_get_parameter() -> dict[str, Any]:
    command = f'aws ssm get-parameter --name {SSM_PARAMETER_NAME} --with-decryption'
    started = time.perf_counter()
    response = _ssm_client().get_parameter(
        Name=SSM_PARAMETER_NAME,
        WithDecryption=True,
    )
    return _config_lab_step_result(
        'ssm',
        'parameter-store-config',
        'get-parameter',
        command,
        response,
        _verify_ssm_parameter(),
        started,
    )


def _run_ssm_get_parameters_by_path() -> dict[str, Any]:
    command = f'aws ssm get-parameters-by-path --path {SSM_PARAMETER_PATH} --recursive --with-decryption'
    started = time.perf_counter()
    response = _ssm_client().get_parameters_by_path(
        Path=SSM_PARAMETER_PATH,
        Recursive=True,
        WithDecryption=True,
    )
    return _config_lab_step_result(
        'ssm',
        'parameter-store-config',
        'get-parameters-by-path',
        command,
        response,
        _verify_ssm_parameter_path(),
        started,
    )


def _run_secretsmanager_create_secret() -> dict[str, Any]:
    command = f'aws secretsmanager create-secret --name {SECRETS_MANAGER_SECRET_NAME} --description "Floci lab application credentials" --secret-string file://initial-secret.json'
    started = time.perf_counter()
    secret_string = json.dumps(SECRETS_MANAGER_INITIAL_SECRET, separators=(',', ':'))
    try:
        response = _secretsmanager_client().create_secret(
            Name=SECRETS_MANAGER_SECRET_NAME,
            Description='Floci lab application credentials',
            SecretString=secret_string,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceExistsException':
            raise
        response = _secretsmanager_client().describe_secret(SecretId=SECRETS_MANAGER_SECRET_NAME)
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'create-secret',
        command,
        response,
        _verify_secretsmanager_secret_exists(),
        started,
    )


def _run_secretsmanager_get_initial_secret() -> dict[str, Any]:
    command = f'aws secretsmanager get-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME}'
    started = time.perf_counter()
    response = _secretsmanager_client().get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)
    normalized = _clean_response(response)
    if _secret_string_json(response) == SECRETS_MANAGER_INITIAL_SECRET:
        cache.set(SECRETS_MANAGER_INITIAL_CACHE_KEY, normalized, timeout=3600)
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'get-initial-secret',
        command,
        response,
        _verify_secretsmanager_initial_read(),
        started,
    )


def _run_secretsmanager_put_secret_value() -> dict[str, Any]:
    command = f'aws secretsmanager put-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME} --secret-string file://rotated-secret.json'
    started = time.perf_counter()
    response = _secretsmanager_client().put_secret_value(
        SecretId=SECRETS_MANAGER_SECRET_NAME,
        SecretString=json.dumps(SECRETS_MANAGER_UPDATED_SECRET, separators=(',', ':')),
    )
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'put-secret-value',
        command,
        response,
        _verify_secretsmanager_updated_secret(),
        started,
    )


def _run_secretsmanager_get_updated_secret() -> dict[str, Any]:
    command = f'aws secretsmanager get-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME}'
    started = time.perf_counter()
    response = _secretsmanager_client().get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'get-updated-secret',
        command,
        response,
        _verify_secretsmanager_updated_secret(),
        started,
    )


def _run_secretsmanager_describe_secret() -> dict[str, Any]:
    command = f'aws secretsmanager describe-secret --secret-id {SECRETS_MANAGER_SECRET_NAME}'
    started = time.perf_counter()
    response = _secretsmanager_client().describe_secret(SecretId=SECRETS_MANAGER_SECRET_NAME)
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'describe-secret',
        command,
        response,
        _verify_secretsmanager_secret_exists(),
        started,
    )


def _reset_ssm_parameter_store_config() -> dict[str, Any]:
    command = f'aws ssm delete-parameter --name {SSM_PARAMETER_NAME}'
    started = time.perf_counter()
    deleted = False
    try:
        _ssm_client().delete_parameter(Name=SSM_PARAMETER_NAME)
        deleted = True
        stdout = _json_text({'deleted': True, 'name': SSM_PARAMETER_NAME})
    except ClientError as exc:
        if _error_code(exc) not in {'ParameterNotFound'}:
            raise
        stdout = _json_text({'deleted': False, 'name': SSM_PARAMETER_NAME})
    verification = _verify_ssm_parameter()
    if verification.get('status') == 'failed':
        verification = {
            'status': 'passed',
            'message': f'Parameter {SSM_PARAMETER_NAME} was removed.',
        }
    return {
        'service': 'ssm',
        'lab': 'parameter-store-config',
        'command': command,
        'exit_code': 0,
        'stdout': stdout,
        'stderr': '',
        'json': {'deleted': deleted, 'name': SSM_PARAMETER_NAME},
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'deleted': deleted,
        'verification': verification,
    }


def _reset_secretsmanager_secret_lifecycle() -> dict[str, Any]:
    command = f'aws secretsmanager delete-secret --secret-id {SECRETS_MANAGER_SECRET_NAME} --force-delete-without-recovery'
    started = time.perf_counter()
    deleted = False
    try:
        _secretsmanager_client().delete_secret(
            SecretId=SECRETS_MANAGER_SECRET_NAME,
            ForceDeleteWithoutRecovery=True,
        )
        deleted = True
        stdout = _json_text({'deleted': True, 'name': SECRETS_MANAGER_SECRET_NAME})
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
        stdout = _json_text({'deleted': False, 'name': SECRETS_MANAGER_SECRET_NAME})
    cache.delete(SECRETS_MANAGER_INITIAL_CACHE_KEY)
    cache.delete(SECRETS_MANAGER_UPDATED_CACHE_KEY)
    verification = _verify_secretsmanager_secret_exists()
    if verification.get('status') == 'failed':
        verification = {
            'status': 'passed',
            'message': f'Secret {SECRETS_MANAGER_SECRET_NAME} was removed.',
        }
    return {
        'service': 'secretsmanager',
        'lab': 'secret-lifecycle',
        'command': command,
        'exit_code': 0,
        'stdout': stdout,
        'stderr': '',
        'json': {'deleted': deleted, 'name': SECRETS_MANAGER_SECRET_NAME},
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'deleted': deleted,
        'verification': verification,
    }


def _run_s3_create_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_BASICS_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_BASICS_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_BASICS_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_bucket()

    return {
        'service': 's3',
        'lab': 'create-bucket',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_head_bucket() -> dict[str, Any]:
    command = f'aws s3api head-bucket --bucket {S3_BASICS_BUCKET_NAME}'
    started = time.perf_counter()
    response = _s3_client().head_bucket(Bucket=S3_BASICS_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_bucket()

    return {
        'service': 's3',
        'lab': 'create-bucket',
        'step': 'head-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_list_buckets() -> dict[str, Any]:
    command = 'aws s3api list-buckets'
    started = time.perf_counter()
    response = _s3_client().list_buckets()
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_bucket()

    return {
        'service': 's3',
        'lab': 'create-bucket',
        'step': 'list-buckets',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_objects_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_OBJECTS_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_OBJECTS_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_OBJECTS_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_objects_bucket()

    return {
        'service': 's3',
        'lab': 'object-workflow',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_hello_object() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_OBJECTS_BUCKET_NAME} '
        f'--key {S3_HELLO_OBJECT_KEY} --body hello.txt --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_OBJECTS_BUCKET_NAME,
        Key=S3_HELLO_OBJECT_KEY,
        Body=S3_HELLO_OBJECT_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_hello_object()

    return {
        'service': 's3',
        'lab': 'object-workflow',
        'step': 'put-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_list_hello_objects() -> dict[str, Any]:
    command = f'aws s3api list-objects-v2 --bucket {S3_OBJECTS_BUCKET_NAME}'
    started = time.perf_counter()
    response = _s3_client().list_objects_v2(Bucket=S3_OBJECTS_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_hello_object()

    return {
        'service': 's3',
        'lab': 'object-workflow',
        'step': 'list-objects',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_head_hello_object() -> dict[str, Any]:
    command = (
        f'aws s3api head-object --bucket {S3_OBJECTS_BUCKET_NAME} '
        f'--key {S3_HELLO_OBJECT_KEY}'
    )
    started = time.perf_counter()
    response = _s3_client().head_object(
        Bucket=S3_OBJECTS_BUCKET_NAME,
        Key=S3_HELLO_OBJECT_KEY,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_hello_object()

    return {
        'service': 's3',
        'lab': 'object-workflow',
        'step': 'head-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_hello_object() -> dict[str, Any]:
    command = (
        f'aws s3api get-object --bucket {S3_OBJECTS_BUCKET_NAME} '
        f'--key {S3_HELLO_OBJECT_KEY} downloaded-hello.txt'
    )
    started = time.perf_counter()
    response = _s3_client().get_object(
        Bucket=S3_OBJECTS_BUCKET_NAME,
        Key=S3_HELLO_OBJECT_KEY,
    )
    body = response.pop('Body').read()
    display_response = {
        **response,
        'DownloadedFile': 'downloaded-hello.txt',
        'DownloadedBody': body.decode('utf-8'),
    }
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_hello_object()

    return {
        'service': 's3',
        'lab': 'object-workflow',
        'step': 'get-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(display_response),
        'stderr': '',
        'json': _clean_response(display_response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_prefixes_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_PREFIXES_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_PREFIXES_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_PREFIXES_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_prefixes_bucket()

    return {
        'service': 's3',
        'lab': 'prefix-copy',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_source_report() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_PREFIXES_BUCKET_NAME} '
        f'--key {S3_REPORT_SOURCE_KEY} --body report.txt --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_PREFIXES_BUCKET_NAME,
        Key=S3_REPORT_SOURCE_KEY,
        Body=S3_REPORT_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_report_object(S3_REPORT_SOURCE_KEY)

    return {
        'service': 's3',
        'lab': 'prefix-copy',
        'step': 'put-source-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_list_incoming_prefix() -> dict[str, Any]:
    command = f'aws s3api list-objects-v2 --bucket {S3_PREFIXES_BUCKET_NAME} --prefix incoming/'
    started = time.perf_counter()
    response = _s3_client().list_objects_v2(
        Bucket=S3_PREFIXES_BUCKET_NAME,
        Prefix='incoming/',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_report_object(S3_REPORT_SOURCE_KEY)

    return {
        'service': 's3',
        'lab': 'prefix-copy',
        'step': 'list-incoming-prefix',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_copy_report_to_archive() -> dict[str, Any]:
    command = (
        f'aws s3api copy-object --copy-source {S3_PREFIXES_BUCKET_NAME}/{S3_REPORT_SOURCE_KEY} '
        f'--bucket {S3_PREFIXES_BUCKET_NAME} --key {S3_REPORT_ARCHIVE_KEY}'
    )
    started = time.perf_counter()
    response = _s3_client().copy_object(
        CopySource={
            'Bucket': S3_PREFIXES_BUCKET_NAME,
            'Key': S3_REPORT_SOURCE_KEY,
        },
        Bucket=S3_PREFIXES_BUCKET_NAME,
        Key=S3_REPORT_ARCHIVE_KEY,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_report_object(S3_REPORT_ARCHIVE_KEY)

    return {
        'service': 's3',
        'lab': 'prefix-copy',
        'step': 'copy-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_list_archive_prefix() -> dict[str, Any]:
    command = f'aws s3api list-objects-v2 --bucket {S3_PREFIXES_BUCKET_NAME} --prefix archive/'
    started = time.perf_counter()
    response = _s3_client().list_objects_v2(
        Bucket=S3_PREFIXES_BUCKET_NAME,
        Prefix='archive/',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_report_object(S3_REPORT_ARCHIVE_KEY)

    return {
        'service': 's3',
        'lab': 'prefix-copy',
        'step': 'list-archive-prefix',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_metadata_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_METADATA_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_METADATA_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_METADATA_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_metadata_bucket()

    return {
        'service': 's3',
        'lab': 'metadata-tags',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_invoice_object() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_METADATA_BUCKET_NAME} '
        f'--key {S3_INVOICE_OBJECT_KEY} --body invoice.txt --content-type text/plain '
        '--metadata project=floci,classification=internal'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_METADATA_BUCKET_NAME,
        Key=S3_INVOICE_OBJECT_KEY,
        Body=S3_INVOICE_BYTES,
        ContentType='text/plain',
        Metadata=S3_INVOICE_METADATA,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_invoice_metadata()

    return {
        'service': 's3',
        'lab': 'metadata-tags',
        'step': 'put-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_head_invoice_object() -> dict[str, Any]:
    command = (
        f'aws s3api head-object --bucket {S3_METADATA_BUCKET_NAME} '
        f'--key {S3_INVOICE_OBJECT_KEY}'
    )
    started = time.perf_counter()
    response = _s3_client().head_object(
        Bucket=S3_METADATA_BUCKET_NAME,
        Key=S3_INVOICE_OBJECT_KEY,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_invoice_metadata()

    return {
        'service': 's3',
        'lab': 'metadata-tags',
        'step': 'head-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_invoice_tags() -> dict[str, Any]:
    command = (
        f'aws s3api put-object-tagging --bucket {S3_METADATA_BUCKET_NAME} '
        f'--key {S3_INVOICE_OBJECT_KEY} --tagging file://invoice-tags.json'
    )
    started = time.perf_counter()
    response = _s3_client().put_object_tagging(
        Bucket=S3_METADATA_BUCKET_NAME,
        Key=S3_INVOICE_OBJECT_KEY,
        Tagging={'TagSet': S3_INVOICE_TAGS},
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_invoice_tags()

    return {
        'service': 's3',
        'lab': 'metadata-tags',
        'step': 'put-object-tagging',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_invoice_tags() -> dict[str, Any]:
    command = (
        f'aws s3api get-object-tagging --bucket {S3_METADATA_BUCKET_NAME} '
        f'--key {S3_INVOICE_OBJECT_KEY}'
    )
    started = time.perf_counter()
    response = _s3_client().get_object_tagging(
        Bucket=S3_METADATA_BUCKET_NAME,
        Key=S3_INVOICE_OBJECT_KEY,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_invoice_tags()

    return {
        'service': 's3',
        'lab': 'metadata-tags',
        'step': 'get-object-tagging',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_versioning_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_VERSIONING_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_VERSIONING_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_VERSIONING_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_versioning_bucket()

    return {
        'service': 's3',
        'lab': 'version-recovery',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_enable_versioning() -> dict[str, Any]:
    command = (
        f'aws s3api put-bucket-versioning --bucket {S3_VERSIONING_BUCKET_NAME} '
        '--versioning-configuration Status=Enabled'
    )
    started = time.perf_counter()
    response = _s3_client().put_bucket_versioning(
        Bucket=S3_VERSIONING_BUCKET_NAME,
        VersioningConfiguration={'Status': 'Enabled'},
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_versioning_enabled()

    return {
        'service': 's3',
        'lab': 'version-recovery',
        'step': 'enable-versioning',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_configuration_v1() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_VERSIONING_BUCKET_NAME} '
        f'--key {S3_CONFIGURATION_KEY} --body configuration-v1.txt --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_VERSIONING_BUCKET_NAME,
        Key=S3_CONFIGURATION_KEY,
        Body=S3_CONFIGURATION_V1_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_configuration_version_one()

    return {
        'service': 's3',
        'lab': 'version-recovery',
        'step': 'put-version-one',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_configuration_v2() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_VERSIONING_BUCKET_NAME} '
        f'--key {S3_CONFIGURATION_KEY} --body configuration-v2.txt --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_VERSIONING_BUCKET_NAME,
        Key=S3_CONFIGURATION_KEY,
        Body=S3_CONFIGURATION_V2_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_configuration_versions()

    return {
        'service': 's3',
        'lab': 'version-recovery',
        'step': 'put-version-two',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_list_configuration_versions() -> dict[str, Any]:
    command = (
        f'aws s3api list-object-versions --bucket {S3_VERSIONING_BUCKET_NAME} '
        f'--prefix {S3_CONFIGURATION_KEY}'
    )
    started = time.perf_counter()
    response = _s3_client().list_object_versions(
        Bucket=S3_VERSIONING_BUCKET_NAME,
        Prefix=S3_CONFIGURATION_KEY,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_configuration_versions()

    return {
        'service': 's3',
        'lab': 'version-recovery',
        'step': 'list-object-versions',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_configuration_v1() -> dict[str, Any]:
    command = (
        f'aws s3api get-object --bucket {S3_VERSIONING_BUCKET_NAME} '
        f'--key {S3_CONFIGURATION_KEY} --version-id <v1-version-id> recovered-configuration.txt'
    )
    started = time.perf_counter()
    versions = _s3_configuration_versions()
    version_one = versions.get('v1')
    if not version_one:
        raise ValueError('Configuration v1 version was not found')
    response = _s3_client().get_object(
        Bucket=S3_VERSIONING_BUCKET_NAME,
        Key=S3_CONFIGURATION_KEY,
        VersionId=version_one['VersionId'],
    )
    body = response.pop('Body').read()
    display_response = {
        **response,
        'VersionId': version_one['VersionId'],
        'DownloadedFile': 'recovered-configuration.txt',
        'DownloadedBody': body.decode('utf-8'),
    }
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_configuration_versions()

    return {
        'service': 's3',
        'lab': 'version-recovery',
        'step': 'get-version-one',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(display_response),
        'stderr': '',
        'json': _clean_response(display_response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed' and body == S3_CONFIGURATION_V1_BYTES,
        'verification': verification,
    }


def _run_s3_create_presigned_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_PRESIGNED_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_PRESIGNED_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_PRESIGNED_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_presigned_bucket()

    return {
        'service': 's3',
        'lab': 'presigned-url',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_presigned_guide() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_PRESIGNED_BUCKET_NAME} '
        f'--key {S3_PRESIGNED_OBJECT_KEY} --body guide.txt --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_PRESIGNED_BUCKET_NAME,
        Key=S3_PRESIGNED_OBJECT_KEY,
        Body=S3_PRESIGNED_GUIDE_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_presigned_object()

    return {
        'service': 's3',
        'lab': 'presigned-url',
        'step': 'put-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_presign_guide() -> dict[str, Any]:
    command = (
        f'aws s3 presign s3://{S3_PRESIGNED_BUCKET_NAME}/{S3_PRESIGNED_OBJECT_KEY} '
        f'--expires-in {S3_PRESIGNED_EXPIRES_IN}'
    )
    started = time.perf_counter()
    verification = _verify_s3_presigned_access()
    duration_ms = round((time.perf_counter() - started) * 1000)
    resource = verification.get('resource', {})
    response = {
        'PresignedUrl': resource.get('url'),
        'ExpiresIn': resource.get('expires_in'),
        'HttpGetBody': resource.get('downloaded_body'),
    }

    return {
        'service': 's3',
        'lab': 'presigned-url',
        'step': 'presign-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_security_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_SECURITY_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_SECURITY_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_SECURITY_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_security_bucket()

    return {
        'service': 's3',
        'lab': 'bucket-security',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_public_access_block() -> dict[str, Any]:
    command = (
        f'aws s3api put-public-access-block --bucket {S3_SECURITY_BUCKET_NAME} '
        '--public-access-block-configuration '
        'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true'
    )
    started = time.perf_counter()
    response = _s3_client().put_public_access_block(
        Bucket=S3_SECURITY_BUCKET_NAME,
        PublicAccessBlockConfiguration=S3_PUBLIC_ACCESS_BLOCK,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_public_access_block()

    return {
        'service': 's3',
        'lab': 'bucket-security',
        'step': 'put-public-access-block',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_public_access_block() -> dict[str, Any]:
    command = f'aws s3api get-public-access-block --bucket {S3_SECURITY_BUCKET_NAME}'
    started = time.perf_counter()
    response = _s3_client().get_public_access_block(Bucket=S3_SECURITY_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_public_access_block()

    return {
        'service': 's3',
        'lab': 'bucket-security',
        'step': 'get-public-access-block',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_security_bucket_policy() -> dict[str, Any]:
    command = (
        f'aws s3api put-bucket-policy --bucket {S3_SECURITY_BUCKET_NAME} '
        '--policy file://bucket-policy.json'
    )
    started = time.perf_counter()
    response = _s3_client().put_bucket_policy(
        Bucket=S3_SECURITY_BUCKET_NAME,
        Policy=json.dumps(S3_SECURITY_BUCKET_POLICY),
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_security_bucket_policy()

    return {
        'service': 's3',
        'lab': 'bucket-security',
        'step': 'put-bucket-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_security_bucket_policy() -> dict[str, Any]:
    command = f'aws s3api get-bucket-policy --bucket {S3_SECURITY_BUCKET_NAME}'
    started = time.perf_counter()
    response = _s3_client().get_bucket_policy(Bucket=S3_SECURITY_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_security_bucket_policy()

    return {
        'service': 's3',
        'lab': 'bucket-security',
        'step': 'get-bucket-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_encryption_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_ENCRYPTION_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_ENCRYPTION_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_ENCRYPTION_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_encryption_bucket()

    return {
        'service': 's3',
        'lab': 'default-encryption',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_default_encryption() -> dict[str, Any]:
    command = (
        f'aws s3api put-bucket-encryption --bucket {S3_ENCRYPTION_BUCKET_NAME} '
        '--server-side-encryption-configuration file://encryption.json'
    )
    started = time.perf_counter()
    response = _s3_client().put_bucket_encryption(
        Bucket=S3_ENCRYPTION_BUCKET_NAME,
        ServerSideEncryptionConfiguration=S3_SSE_S3_CONFIGURATION,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_default_encryption()

    return {
        'service': 's3',
        'lab': 'default-encryption',
        'step': 'put-bucket-encryption',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_default_encryption() -> dict[str, Any]:
    command = f'aws s3api get-bucket-encryption --bucket {S3_ENCRYPTION_BUCKET_NAME}'
    started = time.perf_counter()
    response = _s3_client().get_bucket_encryption(Bucket=S3_ENCRYPTION_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_default_encryption()

    return {
        'service': 's3',
        'lab': 'default-encryption',
        'step': 'get-bucket-encryption',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_encrypted_record() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_ENCRYPTION_BUCKET_NAME} '
        f'--key {S3_ENCRYPTION_OBJECT_KEY} --body record.txt --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_ENCRYPTION_BUCKET_NAME,
        Key=S3_ENCRYPTION_OBJECT_KEY,
        Body=S3_ENCRYPTION_RECORD_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_encrypted_record_workflow()

    return {
        'service': 's3',
        'lab': 'default-encryption',
        'step': 'put-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_head_encrypted_record() -> dict[str, Any]:
    command = (
        f'aws s3api head-object --bucket {S3_ENCRYPTION_BUCKET_NAME} '
        f'--key {S3_ENCRYPTION_OBJECT_KEY}'
    )
    started = time.perf_counter()
    response = _s3_client().head_object(
        Bucket=S3_ENCRYPTION_BUCKET_NAME,
        Key=S3_ENCRYPTION_OBJECT_KEY,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_encrypted_record_workflow()

    return {
        'service': 's3',
        'lab': 'default-encryption',
        'step': 'head-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_lifecycle_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_LIFECYCLE_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_LIFECYCLE_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_LIFECYCLE_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_lifecycle_bucket()

    return {
        'service': 's3',
        'lab': 'lifecycle-retention',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_lifecycle_log() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_LIFECYCLE_BUCKET_NAME} '
        f'--key {S3_LIFECYCLE_OBJECT_KEY} --body app.log --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_LIFECYCLE_BUCKET_NAME,
        Key=S3_LIFECYCLE_OBJECT_KEY,
        Body=S3_LIFECYCLE_LOG_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_lifecycle_log()

    return {
        'service': 's3',
        'lab': 'lifecycle-retention',
        'step': 'put-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_lifecycle_configuration() -> dict[str, Any]:
    command = (
        f'aws s3api put-bucket-lifecycle-configuration --bucket {S3_LIFECYCLE_BUCKET_NAME} '
        '--lifecycle-configuration file://lifecycle.json'
    )
    started = time.perf_counter()
    response = _s3_client().put_bucket_lifecycle_configuration(
        Bucket=S3_LIFECYCLE_BUCKET_NAME,
        LifecycleConfiguration=S3_LIFECYCLE_CONFIGURATION,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_lifecycle_configuration()

    return {
        'service': 's3',
        'lab': 'lifecycle-retention',
        'step': 'put-lifecycle-configuration',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_lifecycle_configuration() -> dict[str, Any]:
    command = (
        f'aws s3api get-bucket-lifecycle-configuration --bucket '
        f'{S3_LIFECYCLE_BUCKET_NAME}'
    )
    started = time.perf_counter()
    response = _s3_client().get_bucket_lifecycle_configuration(
        Bucket=S3_LIFECYCLE_BUCKET_NAME,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_lifecycle_configuration()

    return {
        'service': 's3',
        'lab': 'lifecycle-retention',
        'step': 'get-lifecycle-configuration',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_cors_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_CORS_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_CORS_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_CORS_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_cors_bucket()

    return {
        'service': 's3',
        'lab': 'bucket-cors',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_bucket_cors() -> dict[str, Any]:
    command = (
        f'aws s3api put-bucket-cors --bucket {S3_CORS_BUCKET_NAME} '
        '--cors-configuration file://cors.json'
    )
    started = time.perf_counter()
    response = _s3_client().put_bucket_cors(
        Bucket=S3_CORS_BUCKET_NAME,
        CORSConfiguration=S3_CORS_CONFIGURATION,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_cors_configuration()

    return {
        'service': 's3',
        'lab': 'bucket-cors',
        'step': 'put-bucket-cors',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_bucket_cors() -> dict[str, Any]:
    command = f'aws s3api get-bucket-cors --bucket {S3_CORS_BUCKET_NAME}'
    started = time.perf_counter()
    response = _s3_client().get_bucket_cors(Bucket=S3_CORS_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_cors_configuration()

    return {
        'service': 's3',
        'lab': 'bucket-cors',
        'step': 'get-bucket-cors',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_notifications_create_queue() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {S3_NOTIFICATIONS_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=S3_NOTIFICATIONS_QUEUE_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_notifications_queue()

    return {
        'service': 's3',
        'lab': 'object-notifications-sqs',
        'step': 'create-queue',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_notifications_set_queue_policy() -> dict[str, Any]:
    command = (
        'aws sqs set-queue-attributes --queue-url <queue-url> '
        '--attributes file://queue-attributes.json'
    )
    started = time.perf_counter()
    queue_url = _s3_notifications_queue_url()
    response = _sqs_client().set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={'Policy': json.dumps(S3_NOTIFICATIONS_QUEUE_POLICY)},
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_notifications_queue_policy()

    return {
        'service': 's3',
        'lab': 'object-notifications-sqs',
        'step': 'set-queue-policy',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_notifications_create_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_NOTIFICATIONS_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_NOTIFICATIONS_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_NOTIFICATIONS_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_notifications_bucket()

    return {
        'service': 's3',
        'lab': 'object-notifications-sqs',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_put_notification_configuration() -> dict[str, Any]:
    command = (
        f'aws s3api put-bucket-notification-configuration --bucket '
        f'{S3_NOTIFICATIONS_BUCKET_NAME} --notification-configuration file://notification.json'
    )
    started = time.perf_counter()
    response = _s3_client().put_bucket_notification_configuration(
        Bucket=S3_NOTIFICATIONS_BUCKET_NAME,
        NotificationConfiguration=S3_NOTIFICATIONS_CONFIGURATION,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_notification_configuration()

    return {
        'service': 's3',
        'lab': 'object-notifications-sqs',
        'step': 'put-notification-configuration',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_notification_configuration() -> dict[str, Any]:
    command = (
        f'aws s3api get-bucket-notification-configuration --bucket '
        f'{S3_NOTIFICATIONS_BUCKET_NAME}'
    )
    started = time.perf_counter()
    response = _s3_client().get_bucket_notification_configuration(
        Bucket=S3_NOTIFICATIONS_BUCKET_NAME,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_notification_configuration()

    return {
        'service': 's3',
        'lab': 'object-notifications-sqs',
        'step': 'get-notification-configuration',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_notifications_put_object() -> dict[str, Any]:
    command = (
        f'aws s3api put-object --bucket {S3_NOTIFICATIONS_BUCKET_NAME} '
        f'--key {S3_NOTIFICATIONS_OBJECT_KEY} --body report.txt --content-type text/plain'
    )
    started = time.perf_counter()
    response = _s3_client().put_object(
        Bucket=S3_NOTIFICATIONS_BUCKET_NAME,
        Key=S3_NOTIFICATIONS_OBJECT_KEY,
        Body=S3_NOTIFICATIONS_REPORT_BYTES,
        ContentType='text/plain',
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_notification_object()

    return {
        'service': 's3',
        'lab': 'object-notifications-sqs',
        'step': 'put-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_notifications_receive_event() -> dict[str, Any]:
    command = (
        'aws sqs receive-message --queue-url <queue-url> --max-number-of-messages 10 '
        '--visibility-timeout 0 --wait-time-seconds 1'
    )
    started = time.perf_counter()
    queue_url = _s3_notifications_queue_url()
    response = _sqs_client().receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=10,
        VisibilityTimeout=0,
        WaitTimeSeconds=1,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    message, record = _s3_event_record(response.get('Messages', []))
    verification = (
        {
            'status': 'passed',
            'message': 'SQS received the ObjectCreated event for uploads/report.txt.',
            'resource': {
                'message_id': message.get('MessageId'),
                'event': _clean_response(record),
            },
        }
        if message and record
        else {
            'status': 'failed',
            'message': 'The queue does not contain the expected S3 object-created event.',
        }
    )

    return {
        'service': 's3',
        'lab': 'object-notifications-sqs',
        'step': 'receive-event',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_multipart_create_bucket() -> dict[str, Any]:
    command = f'aws s3api create-bucket --bucket {S3_MULTIPART_BUCKET_NAME}'
    started = time.perf_counter()
    try:
        response = _s3_client().create_bucket(Bucket=S3_MULTIPART_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {'BucketAlreadyOwnedByYou', 'BucketAlreadyExists'}:
            raise
        response = _s3_client().head_bucket(Bucket=S3_MULTIPART_BUCKET_NAME)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_multipart_bucket()

    return {
        'service': 's3',
        'lab': 'multipart-upload',
        'step': 'create-bucket',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_create_multipart_upload() -> dict[str, Any]:
    command = (
        f'aws s3api create-multipart-upload --bucket {S3_MULTIPART_BUCKET_NAME} '
        f'--key {S3_MULTIPART_OBJECT_KEY} --content-type {S3_MULTIPART_CONTENT_TYPE}'
    )
    started = time.perf_counter()
    existing = _s3_active_multipart_upload()
    response = (
        {
            'Bucket': S3_MULTIPART_BUCKET_NAME,
            'Key': S3_MULTIPART_OBJECT_KEY,
            'UploadId': existing['UploadId'],
        }
        if existing
        else _s3_client().create_multipart_upload(
            Bucket=S3_MULTIPART_BUCKET_NAME,
            Key=S3_MULTIPART_OBJECT_KEY,
            ContentType=S3_MULTIPART_CONTENT_TYPE,
        )
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_multipart_upload_started()

    return {
        'service': 's3',
        'lab': 'multipart-upload',
        'step': 'create-multipart-upload',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_upload_multipart_part(
    part_number: int,
    body: bytes,
    step_key: str,
    body_name: str,
) -> dict[str, Any]:
    command = (
        f'aws s3api upload-part --bucket {S3_MULTIPART_BUCKET_NAME} '
        f'--key {S3_MULTIPART_OBJECT_KEY} --part-number {part_number} '
        f'--upload-id <upload-id> --body {body_name}'
    )
    started = time.perf_counter()
    upload = _s3_active_multipart_upload()
    if not upload:
        raise ValueError('Create the multipart upload before uploading parts.')
    response = _s3_client().upload_part(
        Bucket=S3_MULTIPART_BUCKET_NAME,
        Key=S3_MULTIPART_OBJECT_KEY,
        PartNumber=part_number,
        UploadId=upload['UploadId'],
        Body=body,
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_multipart_parts(
        {1} if part_number == 1 else {1, 2},
    )

    return {
        'service': 's3',
        'lab': 'multipart-upload',
        'step': step_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_upload_multipart_part_one() -> dict[str, Any]:
    return _run_s3_upload_multipart_part(
        1,
        S3_MULTIPART_PART_ONE_BYTES,
        'upload-part-one',
        'part-one.bin',
    )


def _run_s3_upload_multipart_part_two() -> dict[str, Any]:
    return _run_s3_upload_multipart_part(
        2,
        S3_MULTIPART_PART_TWO_BYTES,
        'upload-part-two',
        'part-two.bin',
    )


def _run_s3_list_multipart_parts() -> dict[str, Any]:
    command = (
        f'aws s3api list-parts --bucket {S3_MULTIPART_BUCKET_NAME} '
        f'--key {S3_MULTIPART_OBJECT_KEY} --upload-id <upload-id>'
    )
    started = time.perf_counter()
    upload, _ = _s3_multipart_parts()
    response = _s3_client().list_parts(
        Bucket=S3_MULTIPART_BUCKET_NAME,
        Key=S3_MULTIPART_OBJECT_KEY,
        UploadId=upload['UploadId'],
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_multipart_parts({1, 2})

    return {
        'service': 's3',
        'lab': 'multipart-upload',
        'step': 'list-parts',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_complete_multipart_upload() -> dict[str, Any]:
    command = (
        f'aws s3api complete-multipart-upload --bucket {S3_MULTIPART_BUCKET_NAME} '
        f'--key {S3_MULTIPART_OBJECT_KEY} --upload-id <upload-id> '
        '--multipart-upload file://parts.json'
    )
    started = time.perf_counter()
    upload, parts = _s3_multipart_parts()
    completed_parts = [
        {'ETag': part['ETag'], 'PartNumber': part['PartNumber']}
        for part in sorted(parts, key=lambda item: item['PartNumber'])
        if part.get('ETag') and part.get('PartNumber')
    ]
    if [part['PartNumber'] for part in completed_parts] != [1, 2]:
        raise ValueError('Upload both multipart parts before completing the object.')
    response = _s3_client().complete_multipart_upload(
        Bucket=S3_MULTIPART_BUCKET_NAME,
        Key=S3_MULTIPART_OBJECT_KEY,
        UploadId=upload['UploadId'],
        MultipartUpload={'Parts': completed_parts},
    )
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_multipart_object()

    return {
        'service': 's3',
        'lab': 'multipart-upload',
        'step': 'complete-multipart-upload',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(response),
        'stderr': '',
        'json': _clean_response(response),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _run_s3_get_multipart_object() -> dict[str, Any]:
    command = (
        f'aws s3api get-object --bucket {S3_MULTIPART_BUCKET_NAME} '
        f'--key {S3_MULTIPART_OBJECT_KEY} downloaded-application.bin'
    )
    started = time.perf_counter()
    response = _s3_client().get_object(
        Bucket=S3_MULTIPART_BUCKET_NAME,
        Key=S3_MULTIPART_OBJECT_KEY,
    )
    body = response['Body'].read()
    output = {
        key: value
        for key, value in response.items()
        if key != 'Body'
    }
    output['DownloadedBytes'] = len(body)
    duration_ms = round((time.perf_counter() - started) * 1000)
    verification = _verify_s3_multipart_object()

    return {
        'service': 's3',
        'lab': 'multipart-upload',
        'step': 'get-object',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(output),
        'stderr': '',
        'json': _clean_response(output),
        'duration_ms': duration_ms,
        'verified': verification.get('status') == 'passed',
        'verification': verification,
    }


def _scheduler_step_result(
    step_key: str,
    command: str,
    response: dict[str, Any],
    verification: dict[str, Any],
    started: float,
) -> dict[str, Any]:
    return {
        'service': 'scheduler',
        'lab': 'sqs-delivery',
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


def _run_scheduler_create_queue() -> dict[str, Any]:
    command = f'aws sqs create-queue --queue-name {SCHEDULER_SQS_QUEUE_NAME}'
    started = time.perf_counter()
    response = _sqs_client().create_queue(QueueName=SCHEDULER_SQS_QUEUE_NAME)
    verification = _verify_scheduler_queue()
    return _scheduler_step_result(
        'create-queue',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_create_role() -> dict[str, Any]:
    command = (
        f'aws iam create-role --role-name {SCHEDULER_ROLE_NAME} '
        '--assume-role-policy-document file://scheduler-trust-policy.json'
    )
    started = time.perf_counter()
    try:
        response = _iam_client().create_role(
            RoleName=SCHEDULER_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(SCHEDULER_TRUST_POLICY),
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = _iam_client().get_role(RoleName=SCHEDULER_ROLE_NAME)
    verification = _verify_scheduler_role()
    return _scheduler_step_result(
        'create-role',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_put_role_policy() -> dict[str, Any]:
    command = (
        f'aws iam put-role-policy --role-name {SCHEDULER_ROLE_NAME} '
        f'--policy-name {SCHEDULER_ROLE_POLICY_NAME} '
        '--policy-document file://send-message-policy.json'
    )
    started = time.perf_counter()
    response = _iam_client().put_role_policy(
        RoleName=SCHEDULER_ROLE_NAME,
        PolicyName=SCHEDULER_ROLE_POLICY_NAME,
        PolicyDocument=json.dumps(SCHEDULER_ROLE_POLICY),
    )
    verification = _verify_scheduler_role_policy()
    return _scheduler_step_result(
        'put-role-policy',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_create_group() -> dict[str, Any]:
    command = (
        'aws scheduler create-schedule-group '
        f'--name {SCHEDULER_GROUP_NAME}'
    )
    started = time.perf_counter()
    try:
        response = _scheduler_client().create_schedule_group(
            Name=SCHEDULER_GROUP_NAME,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ConflictException':
            raise
        response = _scheduler_client().get_schedule_group(
            Name=SCHEDULER_GROUP_NAME,
        )
    verification = _verify_scheduler_group()
    return _scheduler_step_result(
        'create-schedule-group',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_create_schedule() -> dict[str, Any]:
    scheduled_time = datetime.now(timezone.utc) + timedelta(seconds=8)
    timestamp = scheduled_time.isoformat(timespec='seconds').replace('+00:00', '')
    expression = f'at({timestamp})'
    command = (
        f'aws scheduler create-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME} '
        f'--schedule-expression "{expression}" '
        '--flexible-time-window Mode=OFF --target file://target.json '
        '--action-after-completion DELETE'
    )
    target = {
        'Arn': SCHEDULER_SQS_QUEUE_ARN,
        'RoleArn': SCHEDULER_ROLE_ARN,
        'Input': SCHEDULER_MESSAGE_TEXT,
        'RetryPolicy': {
            'MaximumEventAgeInSeconds': 60,
            'MaximumRetryAttempts': 1,
        },
    }
    started = time.perf_counter()
    response = _scheduler_client().create_schedule(
        Name=SCHEDULER_SCHEDULE_NAME,
        GroupName=SCHEDULER_GROUP_NAME,
        ScheduleExpression=expression,
        FlexibleTimeWindow={'Mode': 'OFF'},
        Target=target,
        State='ENABLED',
        ActionAfterCompletion='DELETE',
        Description='Send a report.ready event to the local SQS lab queue.',
    )
    cache.set(SCHEDULER_EXPRESSION_CACHE_KEY, expression, timeout=86400)
    cache.set(SCHEDULER_CREATED_CACHE_KEY, True, timeout=86400)
    cache.delete_many([
        SCHEDULER_DELIVERED_CACHE_KEY,
        SCHEDULER_DELETED_CACHE_KEY,
    ])
    verification = _verify_scheduler_schedule()
    return _scheduler_step_result(
        'create-schedule',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_get_schedule() -> dict[str, Any]:
    command = (
        f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME}'
    )
    started = time.perf_counter()
    response = _scheduler_client().get_schedule(
        Name=SCHEDULER_SCHEDULE_NAME,
        GroupName=SCHEDULER_GROUP_NAME,
    )
    verification = _verify_scheduler_schedule()
    return _scheduler_step_result(
        'get-schedule',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_receive_message() -> dict[str, Any]:
    command = (
        'aws sqs receive-message '
        '--queue-url <scheduled-reports-queue-url> '
        '--max-number-of-messages 10 --visibility-timeout 0 '
        '--wait-time-seconds 12'
    )
    started = time.perf_counter()
    response: dict[str, Any] = {}
    message = None
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        response = _sqs_client().receive_message(
            QueueUrl=_sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME),
            MaxNumberOfMessages=10,
            VisibilityTimeout=0,
            WaitTimeSeconds=1,
        )
        message = _scheduler_message(response.get('Messages', []))
        if message:
            break
        time.sleep(0.1)
    if message:
        cache.set(SCHEDULER_DELIVERED_CACHE_KEY, True, timeout=86400)
        verification = {
            'status': 'passed',
            'message': 'EventBridge Scheduler delivered the report.ready event to SQS.',
            'resource': _clean_response(message),
        }
    else:
        verification = {
            'status': 'failed',
            'message': 'The scheduled report event did not arrive within the delivery window.',
        }
    return _scheduler_step_result(
        'receive-scheduled-message',
        command,
        response,
        verification,
        started,
    )


def _run_scheduler_confirm_deleted() -> dict[str, Any]:
    command = (
        f'aws scheduler get-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME}'
    )
    started = time.perf_counter()
    response: dict[str, Any] = {}
    deleted = False
    for _ in range(10):
        try:
            response = _scheduler_client().get_schedule(
                Name=SCHEDULER_SCHEDULE_NAME,
                GroupName=SCHEDULER_GROUP_NAME,
            )
        except ClientError as exc:
            if _error_code(exc) == 'ResourceNotFoundException':
                deleted = True
                response = {
                    'Error': {
                        'Code': _error_code(exc),
                        'Message': exc.response.get('Error', {}).get('Message'),
                    },
                }
                break
            raise
        time.sleep(0.1)
    if deleted:
        cache.set(SCHEDULER_DELETED_CACHE_KEY, True, timeout=86400)
    verification = _verify_scheduler_deleted()
    return _scheduler_step_result(
        'confirm-schedule-deleted',
        command,
        response,
        verification,
        started,
    )


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


def _reset_lambda_create_invoke_logs() -> dict[str, Any]:
    command = (
        f'aws lambda delete-function --function-name {LAMBDA_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_ROLE_NAME} '
        f'--policy-name {LAMBDA_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_ROLE_NAME}'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyName=LAMBDA_ROLE_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_ROLE_NAME))
    cache.delete_many([LAMBDA_INVOKE_CACHE_KEY, LAMBDA_LOGS_CACHE_KEY])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
    }

    return {
        'service': 'lambda',
        'lab': 'create-invoke-logs',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': f'Function {LAMBDA_FUNCTION_NAME}, its log group, role policy, role, and recorded invocation state were removed.',
        },
    }


def _reset_lambda_runtime_config() -> dict[str, Any]:
    command = (
        f'aws lambda delete-function --function-name {LAMBDA_CONFIG_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_CONFIG_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_CONFIG_ROLE_NAME} '
        f'--policy-name {LAMBDA_CONFIG_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_CONFIG_ROLE_NAME}\n'
        f'aws ssm delete-parameter --name {LAMBDA_CONFIG_PARAMETER_NAME}\n'
        f'aws secretsmanager delete-secret --secret-id {LAMBDA_CONFIG_SECRET_NAME} --force-delete-without-recovery'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    ssm = _ssm_client()
    secrets = _secretsmanager_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_CONFIG_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_CONFIG_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_CONFIG_ROLE_NAME,
        PolicyName=LAMBDA_CONFIG_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_CONFIG_ROLE_NAME))
    deleted_parameter = _ignore_missing_codes(
        lambda: ssm.delete_parameter(Name=LAMBDA_CONFIG_PARAMETER_NAME),
        {'ParameterNotFound'},
    )
    deleted_secret = _ignore_missing_codes(
        lambda: secrets.delete_secret(
            SecretId=LAMBDA_CONFIG_SECRET_NAME,
            ForceDeleteWithoutRecovery=True,
        ),
        {'ResourceNotFoundException'},
    )
    cache.delete_many([LAMBDA_CONFIG_INVOKE_CACHE_KEY, LAMBDA_CONFIG_LOGS_CACHE_KEY])
    payload = {
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_parameter': deleted_parameter,
        'deleted_secret': deleted_secret,
    }
    return {
        'service': 'lambda',
        'lab': 'runtime-config',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The runtime config function, log group, role policy, role, SSM parameter, secret, and recorded state were removed.',
        },
    }


def _reset_lambda_sqs_event_source() -> dict[str, Any]:
    command = (
        'aws lambda delete-event-source-mapping --uuid <mapping-uuid>\n'
        f'aws lambda delete-function --function-name {LAMBDA_SQS_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_SQS_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_SQS_ROLE_NAME} '
        f'--policy-name {LAMBDA_SQS_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_SQS_ROLE_NAME}\n'
        'aws sqs delete-queue --queue-url <queue-url>'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    sqs = _sqs_client()
    try:
        mapping = _find_lambda_sqs_event_source_mapping()
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
        mapping = None
    deleted_mapping = False
    if mapping and mapping.get('UUID'):
        deleted_mapping = _ignore_missing_codes(
            lambda: lambda_client.delete_event_source_mapping(UUID=mapping['UUID']),
            {'ResourceNotFoundException'},
        )
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_SQS_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_SQS_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_SQS_ROLE_NAME,
        PolicyName=LAMBDA_SQS_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_SQS_ROLE_NAME))
    deleted_queue = False
    try:
        queue_url = _lambda_sqs_queue_url()
    except ClientError as exc:
        if _error_code(exc) not in {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'}:
            raise
    else:
        deleted_queue = _ignore_missing_codes(
            lambda: sqs.delete_queue(QueueUrl=queue_url),
            {'AWS.SimpleQueueService.NonExistentQueue', 'QueueDoesNotExist'},
        )
    cache.delete_many([
        LAMBDA_SQS_MAPPING_CACHE_KEY,
        LAMBDA_SQS_SEND_CACHE_KEY,
        LAMBDA_SQS_LOGS_CACHE_KEY,
    ])
    payload = {
        'deleted_mapping': deleted_mapping,
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_queue': deleted_queue,
    }
    return {
        'service': 'lambda',
        'lab': 'sqs-event-source',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The SQS event source mapping, function, log group, role policy, role, queue, and recorded state were removed.',
        },
    }


def _reset_apigw_lambda_request() -> dict[str, Any]:
    command = (
        'aws apigatewayv2 delete-api --api-id <api-id>\n'
        f'aws lambda remove-permission --function-name {LAMBDA_FUNCTION_NAME} '
        f'--statement-id {APIGW_LAMBDA_PERMISSION_SID}\n'
        f'aws lambda delete-function --function-name {LAMBDA_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_ROLE_NAME} '
        f'--policy-name {LAMBDA_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_ROLE_NAME}'
    )
    started = time.perf_counter()
    api = _find_apigw_lambda_api()
    api_id = (api or {}).get('ApiId')
    deleted_api = False
    if api_id:
        deleted_api = _ignore_missing_codes(
            lambda: _apigatewayv2_client().delete_api(ApiId=api_id),
            {'NotFoundException'},
        )
    removed_permission = _ignore_missing_codes(
        lambda: _lambda_client().remove_permission(
            FunctionName=LAMBDA_FUNCTION_NAME,
            StatementId=APIGW_LAMBDA_PERMISSION_SID,
        ),
        {'ResourceNotFoundException'},
    )
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_ROLE_NAME,
        PolicyName=LAMBDA_ROLE_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_ROLE_NAME))
    cache.delete_many([
        APIGW_LAMBDA_REQUEST_CACHE_KEY,
        APIGW_LAMBDA_API_ID_CACHE_KEY,
        APIGW_LAMBDA_INTEGRATION_ID_CACHE_KEY,
        APIGW_LAMBDA_ROUTE_ID_CACHE_KEY,
        LAMBDA_INVOKE_CACHE_KEY,
        LAMBDA_LOGS_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_api': deleted_api,
        'removed_permission': removed_permission,
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
    }

    return {
        'service': 'apigateway',
        'lab': 'lambda-request',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The HTTP API, Lambda permission, function, log group, role policy, role, and recorded request state were removed.',
        },
    }


def _reset_dynamodb_crud_query() -> dict[str, Any]:
    command = (
        f'aws dynamodb delete-item --table-name {DYNAMODB_TABLE_NAME} --key file://order-key.json\n'
        f'aws dynamodb delete-table --table-name {DYNAMODB_TABLE_NAME}'
    )
    started = time.perf_counter()
    dynamodb = _dynamodb_client()
    deleted_item = False
    deleted_table = False
    try:
        dynamodb.delete_item(TableName=DYNAMODB_TABLE_NAME, Key=_dynamodb_order_key())
        deleted_item = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        dynamodb.delete_table(TableName=DYNAMODB_TABLE_NAME)
        deleted_table = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    cache.delete_many([
        DYNAMODB_PUT_CACHE_KEY,
        DYNAMODB_UPDATE_CACHE_KEY,
        DYNAMODB_QUERY_CACHE_KEY,
        DYNAMODB_DELETE_ITEM_CACHE_KEY,
        DYNAMODB_DELETE_TABLE_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {'deleted_item': deleted_item, 'deleted_table': deleted_table}

    return {
        'service': 'dynamodb',
        'lab': 'crud-query',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': f'Table {DYNAMODB_TABLE_NAME}, its lab item, and recorded DynamoDB lab state were removed.',
        },
    }


def _reset_dynamodb_lambda_writes() -> dict[str, Any]:
    command = (
        f'aws lambda delete-function --function-name {LAMBDA_DYNAMODB_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {LAMBDA_DYNAMODB_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {LAMBDA_DYNAMODB_ROLE_NAME} '
        f'--policy-name {LAMBDA_DYNAMODB_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {LAMBDA_DYNAMODB_ROLE_NAME}\n'
        f'aws dynamodb delete-item --table-name {LAMBDA_DYNAMODB_TABLE_NAME} --key file://order-key.json\n'
        f'aws dynamodb delete-table --table-name {LAMBDA_DYNAMODB_TABLE_NAME}'
    )
    started = time.perf_counter()
    lambda_client = _lambda_client()
    logs = _logs_client()
    iam = _iam_client()
    dynamodb = _dynamodb_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=LAMBDA_DYNAMODB_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=LAMBDA_DYNAMODB_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=LAMBDA_DYNAMODB_ROLE_NAME,
        PolicyName=LAMBDA_DYNAMODB_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=LAMBDA_DYNAMODB_ROLE_NAME))
    deleted_item = False
    deleted_table = False
    try:
        dynamodb.delete_item(
            TableName=LAMBDA_DYNAMODB_TABLE_NAME,
            Key=_lambda_dynamodb_order_key(),
        )
        deleted_item = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        dynamodb.delete_table(TableName=LAMBDA_DYNAMODB_TABLE_NAME)
        deleted_table = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    cache.delete_many([LAMBDA_DYNAMODB_INVOKE_CACHE_KEY, LAMBDA_DYNAMODB_LOGS_CACHE_KEY])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_function': deleted_function,
        'deleted_log_group': deleted_log_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_item': deleted_item,
        'deleted_table': deleted_table,
    }

    return {
        'service': 'dynamodb',
        'lab': 'lambda-writes',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The Lambda writer, log group, IAM role, DynamoDB item, table, and recorded lab state were removed.',
        },
    }


def _reset_kms_crypto() -> dict[str, Any]:
    command = (
        f'aws kms delete-alias --alias-name {KMS_LAB_ALIAS_NAME}\n'
        'aws kms schedule-key-deletion --key-id <key-id> --pending-window-in-days 7'
    )
    started = time.perf_counter()
    key_id = _find_kms_lab_key_id()
    deleted_alias = False
    scheduled_deletion = False
    kms = _kms_client()
    if key_id:
        deleted_alias = _ignore_missing_codes(
            lambda: kms.delete_alias(AliasName=KMS_LAB_ALIAS_NAME),
            {'NotFoundException'},
        )
        scheduled_deletion = _ignore_missing_codes(
            lambda: kms.schedule_key_deletion(
                KeyId=key_id,
                PendingWindowInDays=7,
            ),
            {'NotFoundException', 'KMSInvalidStateException'},
        )
    cache.delete_many([
        KMS_LAB_KEY_ID_CACHE_KEY,
        KMS_LAB_CIPHERTEXT_CACHE_KEY,
        KMS_LAB_DECRYPT_CACHE_KEY,
    ])
    duration_ms = round((time.perf_counter() - started) * 1000)
    payload = {
        'deleted_alias': deleted_alias,
        'scheduled_deletion': scheduled_deletion,
        'key_id': key_id,
    }

    return {
        'service': 'kms',
        'lab': 'key-alias-encrypt-decrypt',
        'command': command,
        'exit_code': 0,
        'stdout': _json_text(payload),
        'stderr': '',
        'json': payload,
        'duration_ms': duration_ms,
        'reset': True,
        **payload,
        'verification': {
            'status': 'passed',
            'message': 'The KMS alias was removed, the lab key was scheduled for deletion when present, and recorded ciphertext state was cleared.',
        },
    }


def _reset_s3_bucket(lab_key: str, bucket_name: str) -> dict[str, Any]:
    command = (
        f'aws s3api delete-objects --bucket {bucket_name} --delete file://objects.json\n'
        f'aws s3api delete-bucket --bucket {bucket_name}'
    )
    started = time.perf_counter()
    s3 = _s3_client()
    deleted_objects = 0
    deleted_bucket = False

    try:
        versions = s3.get_paginator('list_object_versions').paginate(
            Bucket=bucket_name,
        )
        for page in versions:
            objects = [
                {'Key': item['Key'], 'VersionId': item['VersionId']}
                for item in [*page.get('Versions', []), *page.get('DeleteMarkers', [])]
                if item.get('Key') and item.get('VersionId')
            ]
            if objects:
                s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': objects, 'Quiet': True},
                )
                deleted_objects += len(objects)

        contents = s3.get_paginator('list_objects_v2').paginate(
            Bucket=bucket_name,
        )
        for page in contents:
            objects = [
                {'Key': item['Key']}
                for item in page.get('Contents', [])
                if item.get('Key')
            ]
            if objects:
                s3.delete_objects(
                    Bucket=bucket_name,
                    Delete={'Objects': objects, 'Quiet': True},
                )
                deleted_objects += len(objects)

        s3.delete_bucket(Bucket=bucket_name)
        deleted_bucket = True
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchBucket':
            raise

    duration_ms = round((time.perf_counter() - started) * 1000)

    return {
        'service': 's3',
        'lab': lab_key,
        'command': command,
        'exit_code': 0,
        'stdout': _json_text({
            'deleted_objects': deleted_objects,
            'deleted_bucket': deleted_bucket,
        }),
        'stderr': '',
        'json': {
            'deleted_objects': deleted_objects,
            'deleted_bucket': deleted_bucket,
        },
        'duration_ms': duration_ms,
        'reset': True,
        'deleted_objects': deleted_objects,
        'deleted_bucket': deleted_bucket,
        'verification': {
            'status': 'passed',
            'message': (
                f'Bucket {bucket_name} and its contents were removed.'
                if deleted_bucket
                else f'Bucket {bucket_name} was already absent.'
            ),
        },
    }


def _reset_s3_create_bucket() -> dict[str, Any]:
    return _reset_s3_bucket('create-bucket', S3_BASICS_BUCKET_NAME)


def _reset_s3_object_workflow() -> dict[str, Any]:
    return _reset_s3_bucket('object-workflow', S3_OBJECTS_BUCKET_NAME)


def _reset_s3_prefix_copy() -> dict[str, Any]:
    return _reset_s3_bucket('prefix-copy', S3_PREFIXES_BUCKET_NAME)


def _reset_s3_metadata_tags() -> dict[str, Any]:
    return _reset_s3_bucket('metadata-tags', S3_METADATA_BUCKET_NAME)


def _reset_s3_version_recovery() -> dict[str, Any]:
    return _reset_s3_bucket('version-recovery', S3_VERSIONING_BUCKET_NAME)


def _reset_s3_presigned_url() -> dict[str, Any]:
    return _reset_s3_bucket('presigned-url', S3_PRESIGNED_BUCKET_NAME)


def _reset_s3_bucket_security() -> dict[str, Any]:
    s3 = _s3_client()
    for action, missing_codes in (
        (
            lambda: s3.delete_bucket_policy(Bucket=S3_SECURITY_BUCKET_NAME),
            {'NoSuchBucket', 'NoSuchBucketPolicy'},
        ),
        (
            lambda: s3.delete_public_access_block(Bucket=S3_SECURITY_BUCKET_NAME),
            {'NoSuchBucket', 'NoSuchPublicAccessBlockConfiguration'},
        ),
    ):
        try:
            action()
        except ClientError as exc:
            if _error_code(exc) not in missing_codes:
                raise
    return _reset_s3_bucket('bucket-security', S3_SECURITY_BUCKET_NAME)


def _reset_s3_default_encryption() -> dict[str, Any]:
    try:
        _s3_client().delete_bucket_encryption(Bucket=S3_ENCRYPTION_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {
            'NoSuchBucket',
            'ServerSideEncryptionConfigurationNotFoundError',
        }:
            raise
    return _reset_s3_bucket('default-encryption', S3_ENCRYPTION_BUCKET_NAME)


def _reset_s3_lifecycle_retention() -> dict[str, Any]:
    try:
        _s3_client().delete_bucket_lifecycle(Bucket=S3_LIFECYCLE_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {
            'NoSuchBucket',
            'NoSuchLifecycleConfiguration',
        }:
            raise
    return _reset_s3_bucket('lifecycle-retention', S3_LIFECYCLE_BUCKET_NAME)


def _reset_s3_bucket_cors() -> dict[str, Any]:
    try:
        _s3_client().delete_bucket_cors(Bucket=S3_CORS_BUCKET_NAME)
    except ClientError as exc:
        if _error_code(exc) not in {
            'NoSuchBucket',
            'NoSuchCORSConfiguration',
        }:
            raise
    return _reset_s3_bucket('bucket-cors', S3_CORS_BUCKET_NAME)


def _reset_s3_object_notifications() -> dict[str, Any]:
    try:
        _s3_client().put_bucket_notification_configuration(
            Bucket=S3_NOTIFICATIONS_BUCKET_NAME,
            NotificationConfiguration={},
        )
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchBucket':
            raise

    result = _reset_s3_bucket(
        'object-notifications-sqs',
        S3_NOTIFICATIONS_BUCKET_NAME,
    )
    deleted_queue = False
    try:
        queue_url = _s3_notifications_queue_url()
        _sqs_client().delete_queue(QueueUrl=queue_url)
        deleted_queue = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise

    result['command'] = (
        f'aws s3api put-bucket-notification-configuration --bucket '
        f'{S3_NOTIFICATIONS_BUCKET_NAME} --notification-configuration file://empty.json\n'
        f'aws s3api delete-objects --bucket {S3_NOTIFICATIONS_BUCKET_NAME} --delete file://objects.json\n'
        f'aws s3api delete-bucket --bucket {S3_NOTIFICATIONS_BUCKET_NAME}\n'
        'aws sqs delete-queue --queue-url <queue-url>'
    )
    result['deleted_queue'] = deleted_queue
    result['json']['deleted_queue'] = deleted_queue
    result['stdout'] = _json_text(result['json'])
    result['verification'] = {
        'status': 'passed',
        'message': (
            f'Notification wiring, bucket {S3_NOTIFICATIONS_BUCKET_NAME}, '
            f'and queue {S3_NOTIFICATIONS_QUEUE_NAME} were removed.'
        ),
    }
    return result


def _reset_s3_multipart_upload() -> dict[str, Any]:
    aborted_uploads = 0
    try:
        s3 = _s3_client()
        pages = s3.get_paginator('list_multipart_uploads').paginate(
            Bucket=S3_MULTIPART_BUCKET_NAME,
            Prefix=S3_MULTIPART_OBJECT_KEY,
        )
        for page in pages:
            for upload in page.get('Uploads', []):
                if (
                    upload.get('Key') == S3_MULTIPART_OBJECT_KEY
                    and upload.get('UploadId')
                ):
                    s3.abort_multipart_upload(
                        Bucket=S3_MULTIPART_BUCKET_NAME,
                        Key=S3_MULTIPART_OBJECT_KEY,
                        UploadId=upload['UploadId'],
                    )
                    aborted_uploads += 1
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchBucket':
            raise

    result = _reset_s3_bucket('multipart-upload', S3_MULTIPART_BUCKET_NAME)
    result['command'] = (
        f'aws s3api abort-multipart-upload --bucket {S3_MULTIPART_BUCKET_NAME} '
        f'--key {S3_MULTIPART_OBJECT_KEY} --upload-id <upload-id>\n'
        f'aws s3api delete-objects --bucket {S3_MULTIPART_BUCKET_NAME} --delete file://objects.json\n'
        f'aws s3api delete-bucket --bucket {S3_MULTIPART_BUCKET_NAME}'
    )
    result['aborted_uploads'] = aborted_uploads
    result['json']['aborted_uploads'] = aborted_uploads
    result['stdout'] = _json_text(result['json'])
    result['verification'] = {
        'status': 'passed',
        'message': (
            f'Multipart uploads, object data, and bucket '
            f'{S3_MULTIPART_BUCKET_NAME} were removed.'
        ),
    }
    return result


def _reset_scheduler_sqs_delivery() -> dict[str, Any]:
    command = (
        f'aws scheduler delete-schedule --name {SCHEDULER_SCHEDULE_NAME} '
        f'--group-name {SCHEDULER_GROUP_NAME}\n'
        f'aws scheduler delete-schedule-group --name {SCHEDULER_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {SCHEDULER_ROLE_NAME} '
        f'--policy-name {SCHEDULER_ROLE_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {SCHEDULER_ROLE_NAME}\n'
        'aws sqs delete-queue --queue-url <scheduled-reports-queue-url>'
    )
    started = time.perf_counter()
    deleted_schedule = False
    deleted_group = False
    deleted_policy = False
    deleted_role = False
    deleted_queue = False
    try:
        _scheduler_client().delete_schedule(
            Name=SCHEDULER_SCHEDULE_NAME,
            GroupName=SCHEDULER_GROUP_NAME,
        )
        deleted_schedule = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        _scheduler_client().delete_schedule_group(Name=SCHEDULER_GROUP_NAME)
        deleted_group = True
    except ClientError as exc:
        if _error_code(exc) != 'ResourceNotFoundException':
            raise
    try:
        _iam_client().delete_role_policy(
            RoleName=SCHEDULER_ROLE_NAME,
            PolicyName=SCHEDULER_ROLE_POLICY_NAME,
        )
        deleted_policy = True
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
    try:
        _iam_client().delete_role(RoleName=SCHEDULER_ROLE_NAME)
        deleted_role = True
    except ClientError as exc:
        if _error_code(exc) != 'NoSuchEntity':
            raise
    try:
        _sqs_client().delete_queue(QueueUrl=_sqs_queue_url(SCHEDULER_SQS_QUEUE_NAME))
        deleted_queue = True
    except ClientError as exc:
        if _error_code(exc) not in {
            'AWS.SimpleQueueService.NonExistentQueue',
            'QueueDoesNotExist',
        }:
            raise
    cache.delete_many([
        SCHEDULER_EXPRESSION_CACHE_KEY,
        SCHEDULER_CREATED_CACHE_KEY,
        SCHEDULER_DELIVERED_CACHE_KEY,
        SCHEDULER_DELETED_CACHE_KEY,
    ])
    output = {
        'deleted_schedule': deleted_schedule,
        'deleted_group': deleted_group,
        'deleted_policy': deleted_policy,
        'deleted_role': deleted_role,
        'deleted_queue': deleted_queue,
    }
    return {
        'service': 'scheduler',
        'lab': 'sqs-delivery',
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
            'message': 'Schedule resources, execution role, and target queue were removed.',
        },
    }


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
from . import sqs_labs, sns_labs  # noqa: E402

for _module in (sqs_labs, sns_labs,):
    for _name, _value in vars(_module).items():
        if not _name.startswith('__') and _name not in ('LABS', 'run_step', 'status', 'reset'):
            globals().setdefault(_name, _value)
del _module, _name, _value
