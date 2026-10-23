"""S3 workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from botocore.exceptions import ClientError

from dashboard.aws import _clean_response
from . import s3_data_protection_labs as data_protection
from .monolith import (
    AWS_ACCOUNT_ID,
    _error_code,
    _json_text,
    _s3_client,
    _sqs_client,
)


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
S3_LIFECYCLE_RULE_ID = 'TierThenExpireApplicationLogs'
S3_LIFECYCLE_TRANSITIONS = [
    {'Days': 30, 'StorageClass': 'STANDARD_IA'},
    {'Days': 90, 'StorageClass': 'GLACIER'},
]
S3_LIFECYCLE_EXPIRATION_DAYS = 365
S3_LIFECYCLE_CONFIGURATION = {
    'Rules': [
        {
            'ID': S3_LIFECYCLE_RULE_ID,
            'Status': 'Enabled',
            'Filter': {'Prefix': 'logs/'},
            'Transitions': S3_LIFECYCLE_TRANSITIONS,
            'Expiration': {'Days': S3_LIFECYCLE_EXPIRATION_DAYS},
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
    'title': 'Tier and expire logs with an S3 lifecycle rule',
    'description': (
        'Upload an application log, then add one lifecycle rule for logs/ that moves objects to cheaper storage '
        'classes as they age (Standard-IA at 30 days, Glacier Flexible Retrieval at 90) and deletes them after a year.'
    ),
    'steps': [
        {
            'key': 'create-bucket',
            'title': 'Create the lifecycle lab bucket',
            'command': f'aws s3api create-bucket --bucket {S3_LIFECYCLE_BUCKET_NAME}',
            'explanation': 'Creates a dedicated bucket for learning automated S3 storage tiering and retention.',
        },
        {
            'key': 'put-object',
            'title': 'Upload an application log',
            'command': f'aws s3api put-object --bucket {S3_LIFECYCLE_BUCKET_NAME} --key {S3_LIFECYCLE_OBJECT_KEY} --body app.log --content-type text/plain',
            'explanation': 'Uploads a sample log beneath the logs/ prefix targeted by the lifecycle rule. New objects start in S3 Standard.',
            'artifact_label': 'app.log',
            'artifact': S3_LIFECYCLE_LOG_TEXT,
        },
        {
            'key': 'put-lifecycle-configuration',
            'title': 'Tier logs to colder storage, then expire them',
            'command': f'aws s3api put-bucket-lifecycle-configuration --bucket {S3_LIFECYCLE_BUCKET_NAME} --lifecycle-configuration file://lifecycle.json',
            'explanation': (
                'Logs are read often for a few days and rarely after that. The rule moves them to Standard-IA after 30 days '
                '(cheaper storage, per-GB retrieval fee, 30-day minimum), to Glacier Flexible Retrieval after 90 days '
                '(archive pricing, minutes-to-hours restores), and deletes them after 365 days. AWS runs lifecycle actions '
                'asynchronously; Floci saves the rule but does not move or delete objects.'
            ),
            'artifact_label': 'lifecycle.json',
            'artifact': json.dumps(S3_LIFECYCLE_CONFIGURATION, indent=2),
        },
        {
            'key': 'get-lifecycle-configuration',
            'title': 'Inspect the tiering and retention rule',
            'command': f'aws s3api get-bucket-lifecycle-configuration --bucket {S3_LIFECYCLE_BUCKET_NAME}',
            'explanation': 'Reads the saved lifecycle configuration and verifies the rule scope, both transitions, and the 365-day expiration.',
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


def _verify_s3_bucket() -> dict[str, Any]:
    try:
        response = _s3_client().head_bucket(Bucket=S3_BASICS_BUCKET_NAME)
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

    transitions = sorted(
        ((item.get('Days'), item.get('StorageClass')) for item in (rule or {}).get('Transitions', [])),
        key=lambda item: item[0] or 0,
    )
    expected_transitions = [(item['Days'], item['StorageClass']) for item in S3_LIFECYCLE_TRANSITIONS]
    if (
        rule
        and rule.get('Status') == 'Enabled'
        and prefix == 'logs/'
        and transitions == expected_transitions
        and rule.get('Expiration', {}).get('Days') == S3_LIFECYCLE_EXPIRATION_DAYS
    ):
        return {
            'status': 'passed',
            'message': (
                'The enabled rule moves logs/ to STANDARD_IA at 30 days and GLACIER at 90 days, '
                f'then expires them at {S3_LIFECYCLE_EXPIRATION_DAYS} days.'
            ),
            'resource': _clean_response(rule),
        }

    return {
        'status': 'failed',
        'message': 'The bucket does not have the expected logs/ tiering and expiration rule.',
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


LABS = [
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
    data_protection.LAB,
]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 's3' and lab_key == 'data-protection-tiers':
        from .s3_data_protection_labs import run_step as data_protection_run_step
        return data_protection_run_step(step_key)

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

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 's3' and lab_key == 'data-protection-tiers':
        from .s3_data_protection_labs import status as data_protection_status
        return data_protection_status()

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

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 's3' and lab_key == 'data-protection-tiers':
        from .s3_data_protection_labs import reset as data_protection_reset
        return data_protection_reset()

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

    raise ValueError('Lab reset not found')
