"""IAM workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from .monolith import (
    AWS_ACCOUNT_ID,
    _error_code,
    _floci_client_factory,
    _iam_client,
    _ignore_missing,
    _json_text,
    _sts_client,
    _verify_role,
)


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
    response = _floci_client_factory(identity=identity).client('sts').get_caller_identity()
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
            response = _floci_client_factory(identity={
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
        response = _floci_client_factory(identity=_iam_enforcement_user_identity()).client('sts').assume_role(
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
        _floci_client_factory(identity=_iam_enforcement_user_identity()).client('s3').list_buckets()
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
    response = _floci_client_factory(identity=_iam_enforcement_user_identity()).client('sts').assume_role(
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
    response = _floci_client_factory(identity=_iam_enforcement_role_identity()).client('sqs').list_queues()
    verification = {'status': 'passed', 'message': 'The assumed role listed SQS queues.', 'resource': _clean_response(response)}
    cache.set(IAM_ENFORCEMENT_ROLE_SQS_CACHE_KEY, verification['resource'], timeout=3600)
    return _lab_step_result('identity-enforcement-capstone', 'verify-role-sqs', command, _json_text(response), verification, started)


def _run_iam_enforcement_verify_role_s3_denied() -> dict[str, Any]:
    command = 'aws s3 ls  # using assumed-role credentials'
    started = time.perf_counter()
    try:
        _floci_client_factory(identity=_iam_enforcement_role_identity()).client('s3').list_buckets()
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


LABS = [
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


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
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

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
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

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
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

    raise ValueError('Lab reset not found')
