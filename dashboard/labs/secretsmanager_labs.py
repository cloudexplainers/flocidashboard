"""Secrets Manager workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
import zipfile
from io import BytesIO
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from .monolith import (
    AWS_ACCOUNT_ID,
    AWS_REGION,
    _config_lab_step_result,
    _error_code,
    _iam_client,
    _ignore_missing,
    _ignore_missing_codes,
    _json_text,
    _lambda_client,
    _logs_client,
    _secret_string_json,
    _secretsmanager_client,
    _verify_role,
    _wait_for_verification,
)


SECRETS_MANAGER_SECRET_NAME = 'lab/app-credentials'
SECRETS_MANAGER_INITIAL_SECRET = {
    'username': 'lab-app',
    'password': 'initial-local-password',
}
SECRETS_MANAGER_UPDATED_SECRET = {
    'username': 'lab-app',
    'password': 'updated-local-password',
}
SECRETS_MANAGER_INITIAL_CACHE_KEY = 'floci-lab:secretsmanager:secret-lifecycle:initial-read'
SECRETS_MANAGER_UPDATED_CACHE_KEY = 'floci-lab:secretsmanager:secret-lifecycle:updated-read'
SECRETS_ROTATION_ROLE_NAME = 'FlociSecretsRotationRole'
SECRETS_ROTATION_POLICY_NAME = 'FlociSecretsRotationAccess'
SECRETS_ROTATION_ROLE_ARN = f'arn:aws:iam::{AWS_ACCOUNT_ID}:role/{SECRETS_ROTATION_ROLE_NAME}'
SECRETS_ROTATION_FUNCTION_NAME = 'lab-secret-rotator'
SECRETS_ROTATION_FUNCTION_ARN = f'arn:aws:lambda:{AWS_REGION}:{AWS_ACCOUNT_ID}:function:{SECRETS_ROTATION_FUNCTION_NAME}'
SECRETS_ROTATION_LOG_GROUP_NAME = f'/aws/lambda/{SECRETS_ROTATION_FUNCTION_NAME}'
SECRETS_ROTATION_PERMISSION_SID = 'AllowSecretsManagerRotation'
SECRETS_ROTATION_DAYS = 30
SECRETS_ROTATED_PASSWORD_PREFIX = 'rotated-'
SECRETS_ROTATION_WAIT_SECONDS = 45
SECRETS_ROTATION_TRUST_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Principal': {'Service': 'lambda.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        },
    ],
}
SECRETS_ROTATION_POLICY = {
    'Version': '2012-10-17',
    'Statement': [
        {
            'Effect': 'Allow',
            'Action': [
                'secretsmanager:DescribeSecret',
                'secretsmanager:GetSecretValue',
                'secretsmanager:PutSecretValue',
                'secretsmanager:UpdateSecretVersionStage',
            ],
            'Resource': f'arn:aws:secretsmanager:{AWS_REGION}:{AWS_ACCOUNT_ID}:secret:{SECRETS_MANAGER_SECRET_NAME}-*',
        },
        {
            'Effect': 'Allow',
            'Action': ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents'],
            'Resource': f'arn:aws:logs:{AWS_REGION}:{AWS_ACCOUNT_ID}:log-group:{SECRETS_ROTATION_LOG_GROUP_NAME}:*',
        },
    ],
}
# A minimal rotation function for the four-step protocol. A production function
# would also change the password in the database (setSecret) and log in with it
# (testSecret); this lab has no database, so those steps only log.
SECRETS_ROTATION_HANDLER_SOURCE = '''import json
import secrets as random_tokens

import boto3

client = boto3.client("secretsmanager")


def lambda_handler(event, context):
    secret_id = event["SecretId"]
    token = event["ClientRequestToken"]
    step = event["Step"]
    print(json.dumps({"rotation_step": step, "token": token}))

    if step == "createSecret":
        try:
            pending = client.get_secret_value(SecretId=secret_id, VersionId=token, VersionStage="AWSPENDING")
            if pending.get("SecretString"):
                return
        except client.exceptions.ResourceNotFoundException:
            pass
        current = json.loads(client.get_secret_value(SecretId=secret_id, VersionStage="AWSCURRENT")["SecretString"])
        current["password"] = "rotated-" + random_tokens.token_hex(8)
        client.put_secret_value(
            SecretId=secret_id,
            ClientRequestToken=token,
            SecretString=json.dumps(current),
            VersionStages=["AWSPENDING"],
        )
    elif step == "finishSecret":
        stages = client.describe_secret(SecretId=secret_id)["VersionIdsToStages"]
        current = next(version for version, labels in stages.items() if "AWSCURRENT" in labels)
        if current != token:
            client.update_secret_version_stage(
                SecretId=secret_id,
                VersionStage="AWSCURRENT",
                MoveToVersionId=token,
                RemoveFromVersionId=current,
            )
'''
SECRETS_ROTATION_ROLE_COMMAND = (
    f'aws iam create-role --role-name {SECRETS_ROTATION_ROLE_NAME} '
    '--assume-role-policy-document file://rotation-trust-policy.json\n'
    f'aws iam put-role-policy --role-name {SECRETS_ROTATION_ROLE_NAME} '
    f'--policy-name {SECRETS_ROTATION_POLICY_NAME} --policy-document file://rotation-policy.json'
)
SECRETS_ROTATION_FUNCTION_COMMAND = (
    f'aws lambda create-function --function-name {SECRETS_ROTATION_FUNCTION_NAME} --runtime python3.11 '
    f'--role {SECRETS_ROTATION_ROLE_ARN} --handler handler.lambda_handler --timeout 30 --zip-file fileb://function.zip'
)
SECRETS_ROTATION_PERMISSION_COMMAND = (
    f'aws lambda add-permission --function-name {SECRETS_ROTATION_FUNCTION_NAME} '
    f'--statement-id {SECRETS_ROTATION_PERMISSION_SID} --action lambda:InvokeFunction '
    '--principal secretsmanager.amazonaws.com'
)
SECRETS_ROTATE_COMMAND = (
    f'aws secretsmanager rotate-secret --secret-id {SECRETS_MANAGER_SECRET_NAME} '
    f'--rotation-lambda-arn {SECRETS_ROTATION_FUNCTION_ARN} '
    f'--rotation-rules AutomaticallyAfterDays={SECRETS_ROTATION_DAYS}'
)
SECRETS_ROTATED_READ_COMMAND = f'aws secretsmanager get-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME}'
SECRETS_ROTATED_CACHE_KEY = 'floci-lab:secretsmanager:secret-lifecycle:rotated-read'

SECRETS_MANAGER_SECRET_LIFECYCLE_LAB = {
    'service': 'secretsmanager',
    'key': 'secret-lifecycle',
    'title': 'Create, update, and rotate a Secrets Manager secret',
    'description': (
        'Create a JSON application secret, update it by hand, then turn on automatic rotation with a Lambda '
        'rotation function and watch Secrets Manager stage a new AWSCURRENT version. Built-in rotation is the '
        'main reason to pick Secrets Manager over SSM Parameter Store SecureString.'
    ),
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
            'title': 'Update the secret value by hand',
            'command': f'aws secretsmanager put-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME} --secret-string file://updated-secret.json',
            'explanation': (
                'Writes a new version and moves the AWSCURRENT label to it; the old version keeps AWSPREVIOUS. '
                'A manual update works, but nothing changes the credential again unless someone remembers to.'
            ),
            'artifact_label': 'updated-secret.json',
            'artifact': json.dumps(SECRETS_MANAGER_UPDATED_SECRET, indent=2),
        },
        {
            'key': 'get-updated-secret',
            'title': 'Read the updated secret',
            'command': f'aws secretsmanager get-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME}',
            'explanation': 'Reads the current secret value and verifies that the manually updated credential is active.',
        },
        {
            'key': 'create-rotation-role',
            'title': 'Create the rotation function role',
            'command': SECRETS_ROTATION_ROLE_COMMAND,
            'explanation': (
                'Creates a role Lambda can assume, scoped to read, write, and relabel versions of this one secret, '
                'plus write its own logs. Least privilege applies to automation too.'
            ),
            'artifact_label': 'rotation-policy.json',
            'artifact': json.dumps(SECRETS_ROTATION_POLICY, indent=2),
        },
        {
            'key': 'create-rotation-function',
            'title': 'Create the rotation Lambda function',
            'command': SECRETS_ROTATION_FUNCTION_COMMAND,
            'explanation': (
                'Secrets Manager calls this function four times per rotation: createSecret stages a new value as '
                'AWSPENDING, setSecret and testSecret would apply and check it against the database, and finishSecret '
                'moves AWSCURRENT to the new version. This lab has no database, so only createSecret and finishSecret do work.'
            ),
            'artifact_label': 'handler.py',
            'artifact': SECRETS_ROTATION_HANDLER_SOURCE,
        },
        {
            'key': 'allow-rotation-invoke',
            'title': 'Allow Secrets Manager to invoke the function',
            'command': SECRETS_ROTATION_PERMISSION_COMMAND,
            'explanation': (
                'Adds a resource-based policy statement so the Secrets Manager service principal can invoke the function. '
                'AWS requires this; Floci does not check it, but the lab adds it so the setup matches AWS.'
            ),
        },
        {
            'key': 'rotate-secret',
            'title': 'Turn on rotation and rotate now',
            'command': SECRETS_ROTATE_COMMAND,
            'explanation': (
                f'Attaches the rotation function, schedules rotation every {SECRETS_ROTATION_DAYS} days, and starts the '
                'first rotation immediately. Rotation runs in the background.'
            ),
        },
        {
            'key': 'get-rotated-secret',
            'title': 'Read the rotated secret',
            'command': SECRETS_ROTATED_READ_COMMAND,
            'explanation': (
                'Waits for rotation to finish, then reads AWSCURRENT. The password is new and the previous value is '
                'still readable as AWSPREVIOUS, so apps that cached the old credential can recover.'
            ),
        },
        {
            'key': 'describe-secret',
            'title': 'Inspect the rotation configuration',
            'command': f'aws secretsmanager describe-secret --secret-id {SECRETS_MANAGER_SECRET_NAME}',
            'explanation': (
                'Shows RotationEnabled, the rotation function, the schedule, the next rotation date, and which '
                'version holds each label, all without exposing the secret value.'
            ),
        },
    ],
}


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
    cached = cache.get(SECRETS_MANAGER_UPDATED_CACHE_KEY)
    for stage in ('AWSCURRENT', 'AWSPREVIOUS'):
        try:
            response = _secretsmanager_client().get_secret_value(
                SecretId=SECRETS_MANAGER_SECRET_NAME,
                VersionStage=stage,
            )
        except ClientError as exc:
            if stage == 'AWSCURRENT':
                return {'status': 'failed', 'message': str(exc)}
            continue
        if _secret_string_json(response) == SECRETS_MANAGER_UPDATED_SECRET:
            resource = _clean_response(response)
            cache.set(SECRETS_MANAGER_UPDATED_CACHE_KEY, resource, timeout=3600)
            where = 'the current value' if stage == 'AWSCURRENT' else 'AWSPREVIOUS after rotation'
            return {
                'status': 'passed',
                'message': f'Secrets Manager holds the manually updated credential JSON as {where}.',
                'resource': resource,
            }
    if cached:
        return {
            'status': 'passed',
            'message': 'A read of the manually updated credential was recorded for this lab run.',
            'resource': cached,
        }
    return {
        'status': 'failed',
        'message': 'The current secret value does not match the manually updated credential JSON.',
    }


def _verify_secretsmanager_manual_update_is_current() -> dict[str, Any]:
    """The manual-update steps run before rotation, so they must see it as AWSCURRENT."""
    try:
        response = _secretsmanager_client().get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if _secret_string_json(response) == SECRETS_MANAGER_UPDATED_SECRET:
        resource = _clean_response(response)
        cache.set(SECRETS_MANAGER_UPDATED_CACHE_KEY, resource, timeout=3600)
        return {
            'status': 'passed',
            'message': 'Secrets Manager returns the manually updated credential JSON as the current value.',
            'resource': resource,
        }
    return {
        'status': 'failed',
        'message': 'The current secret value does not match the manually updated credential JSON.',
    }


def _verify_secrets_rotation_role() -> dict[str, Any]:
    role = _verify_role(SECRETS_ROTATION_ROLE_NAME, 'lambda.amazonaws.com')
    if role.get('status') != 'passed':
        return role
    try:
        policy = _iam_client().get_role_policy(
            RoleName=SECRETS_ROTATION_ROLE_NAME,
            PolicyName=SECRETS_ROTATION_POLICY_NAME,
        )
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    return {
        'status': 'passed',
        'message': f'Role {SECRETS_ROTATION_ROLE_NAME} trusts Lambda and carries {SECRETS_ROTATION_POLICY_NAME}.',
        'resource': _clean_response(policy),
    }


def _verify_secrets_rotation_function() -> dict[str, Any]:
    try:
        configuration = _lambda_client().get_function(FunctionName=SECRETS_ROTATION_FUNCTION_NAME).get('Configuration', {})
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    if configuration.get('Role') != SECRETS_ROTATION_ROLE_ARN:
        return {'status': 'failed', 'message': f'Function {SECRETS_ROTATION_FUNCTION_NAME} does not use {SECRETS_ROTATION_ROLE_NAME}.'}
    state = configuration.get('State', 'Active')
    if state != 'Active':
        return {'status': 'failed', 'message': f'Function {SECRETS_ROTATION_FUNCTION_NAME} is {state}, not Active yet.'}
    return {
        'status': 'passed',
        'message': f'Function {SECRETS_ROTATION_FUNCTION_NAME} is Active and runs as {SECRETS_ROTATION_ROLE_NAME}.',
        'resource': _clean_response(configuration),
    }


def _verify_secrets_rotation_permission() -> dict[str, Any]:
    try:
        policy = json.loads(_lambda_client().get_policy(FunctionName=SECRETS_ROTATION_FUNCTION_NAME).get('Policy') or '{}')
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    allowed = any(
        statement.get('Principal', {}).get('Service') == 'secretsmanager.amazonaws.com'
        and statement.get('Effect') == 'Allow'
        for statement in policy.get('Statement', [])
    )
    if allowed:
        return {
            'status': 'passed',
            'message': f'{SECRETS_ROTATION_FUNCTION_NAME} lets secretsmanager.amazonaws.com invoke it.',
            'resource': policy,
        }
    return {'status': 'failed', 'message': 'No resource policy statement allows Secrets Manager to invoke the function.'}


def _describe_lab_secret() -> dict[str, Any]:
    return _secretsmanager_client().describe_secret(SecretId=SECRETS_MANAGER_SECRET_NAME)


def _verify_secrets_rotation_enabled() -> dict[str, Any]:
    try:
        response = _describe_lab_secret()
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    rules = response.get('RotationRules') or {}
    if (
        response.get('RotationEnabled')
        and response.get('RotationLambdaARN') == SECRETS_ROTATION_FUNCTION_ARN
        and rules.get('AutomaticallyAfterDays') == SECRETS_ROTATION_DAYS
    ):
        return {
            'status': 'passed',
            'message': (
                f'Rotation is enabled every {SECRETS_ROTATION_DAYS} days with {SECRETS_ROTATION_FUNCTION_NAME}; '
                f'next rotation {response.get("NextRotationDate")}.'
            ),
            'resource': _clean_response(response),
        }
    return {'status': 'failed', 'message': f'Rotation is not enabled on {SECRETS_MANAGER_SECRET_NAME} with the lab function.'}


def _verify_secrets_rotated_value() -> dict[str, Any]:
    client = _secretsmanager_client()
    try:
        current = client.get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME, VersionStage='AWSCURRENT')
    except ClientError as exc:
        return {'status': 'failed', 'message': str(exc)}
    value = _secret_string_json(current) or {}
    password = value.get('password', '') if isinstance(value, dict) else ''
    if not password.startswith(SECRETS_ROTATED_PASSWORD_PREFIX):
        return {'status': 'failed', 'message': 'AWSCURRENT still holds a pre-rotation password; rotation has not finished.'}
    if value.get('username') != SECRETS_MANAGER_UPDATED_SECRET['username']:
        return {'status': 'failed', 'message': 'The rotated secret lost its username field.'}
    try:
        previous = client.get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME, VersionStage='AWSPREVIOUS')
        previous_password = (_secret_string_json(previous) or {}).get('password')
    except ClientError:
        previous_password = None
    if previous_password != SECRETS_MANAGER_UPDATED_SECRET['password']:
        return {
            'status': 'failed',
            'message': 'AWSPREVIOUS does not hold the manually updated password yet; rotation has not finished.',
        }
    resource = _clean_response(current)
    cache.set(SECRETS_ROTATED_CACHE_KEY, resource, timeout=3600)
    return {
        'status': 'passed',
        'message': (
            f'AWSCURRENT holds a rotated password ({password[:len(SECRETS_ROTATED_PASSWORD_PREFIX) + 4]}…); '
            f'AWSPREVIOUS holds the manual update ({previous_password}).'
        ),
        'resource': resource,
    }


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
    command = f'aws secretsmanager put-secret-value --secret-id {SECRETS_MANAGER_SECRET_NAME} --secret-string file://updated-secret.json'
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
        _verify_secretsmanager_manual_update_is_current(),
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
        _verify_secretsmanager_manual_update_is_current(),
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
        _verify_secrets_rotation_enabled(),
        started,
    )


def _run_secrets_create_rotation_role() -> dict[str, Any]:
    started = time.perf_counter()
    iam = _iam_client()
    try:
        response = iam.create_role(
            RoleName=SECRETS_ROTATION_ROLE_NAME,
            AssumeRolePolicyDocument=json.dumps(SECRETS_ROTATION_TRUST_POLICY),
        )
    except ClientError as exc:
        if _error_code(exc) != 'EntityAlreadyExists':
            raise
        response = iam.get_role(RoleName=SECRETS_ROTATION_ROLE_NAME)
    iam.put_role_policy(
        RoleName=SECRETS_ROTATION_ROLE_NAME,
        PolicyName=SECRETS_ROTATION_POLICY_NAME,
        PolicyDocument=json.dumps(SECRETS_ROTATION_POLICY),
    )
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'create-rotation-role',
        SECRETS_ROTATION_ROLE_COMMAND,
        response,
        _verify_secrets_rotation_role(),
        started,
    )


def _rotation_function_zip_bytes() -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('handler.py', SECRETS_ROTATION_HANDLER_SOURCE)
    return buffer.getvalue()


def _run_secrets_create_rotation_function() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = _lambda_client().create_function(
            FunctionName=SECRETS_ROTATION_FUNCTION_NAME,
            Runtime='python3.11',
            Role=SECRETS_ROTATION_ROLE_ARN,
            Handler='handler.lambda_handler',
            Code={'ZipFile': _rotation_function_zip_bytes()},
            Description='Floci dashboard Secrets Manager rotation lab.',
            Timeout=30,
            MemorySize=128,
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_function(FunctionName=SECRETS_ROTATION_FUNCTION_NAME)
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'create-rotation-function',
        SECRETS_ROTATION_FUNCTION_COMMAND,
        response,
        _wait_for_verification(_verify_secrets_rotation_function, timeout_seconds=60),
        started,
    )


def _run_secrets_allow_rotation_invoke() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = _lambda_client().add_permission(
            FunctionName=SECRETS_ROTATION_FUNCTION_NAME,
            StatementId=SECRETS_ROTATION_PERMISSION_SID,
            Action='lambda:InvokeFunction',
            Principal='secretsmanager.amazonaws.com',
        )
    except ClientError as exc:
        if _error_code(exc) != 'ResourceConflictException':
            raise
        response = _lambda_client().get_policy(FunctionName=SECRETS_ROTATION_FUNCTION_NAME)
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'allow-rotation-invoke',
        SECRETS_ROTATION_PERMISSION_COMMAND,
        response,
        _verify_secrets_rotation_permission(),
        started,
    )


def _run_secrets_rotate_secret() -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = _secretsmanager_client().rotate_secret(
            SecretId=SECRETS_MANAGER_SECRET_NAME,
            RotationLambdaARN=SECRETS_ROTATION_FUNCTION_ARN,
            RotationRules={'AutomaticallyAfterDays': SECRETS_ROTATION_DAYS},
        )
    except ClientError as exc:
        # A rotation that is still running counts: the next step waits for it.
        if _error_code(exc) != 'InvalidRequestException' or 'rotation' not in str(exc).lower():
            raise
        response = _describe_lab_secret()
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'rotate-secret',
        SECRETS_ROTATE_COMMAND,
        response,
        _verify_secrets_rotation_enabled(),
        started,
    )


def _run_secrets_get_rotated_secret() -> dict[str, Any]:
    started = time.perf_counter()
    verification = _wait_for_verification(_verify_secrets_rotated_value, timeout_seconds=SECRETS_ROTATION_WAIT_SECONDS)
    try:
        response = _secretsmanager_client().get_secret_value(SecretId=SECRETS_MANAGER_SECRET_NAME)
    except ClientError as exc:
        response = {'error': str(exc)}
    return _config_lab_step_result(
        'secretsmanager',
        'secret-lifecycle',
        'get-rotated-secret',
        SECRETS_ROTATED_READ_COMMAND,
        response,
        verification,
        started,
    )


def _reset_secretsmanager_secret_lifecycle() -> dict[str, Any]:
    command = (
        f'aws secretsmanager delete-secret --secret-id {SECRETS_MANAGER_SECRET_NAME} --force-delete-without-recovery\n'
        f'aws lambda delete-function --function-name {SECRETS_ROTATION_FUNCTION_NAME}\n'
        f'aws logs delete-log-group --log-group-name {SECRETS_ROTATION_LOG_GROUP_NAME}\n'
        f'aws iam delete-role-policy --role-name {SECRETS_ROTATION_ROLE_NAME} --policy-name {SECRETS_ROTATION_POLICY_NAME}\n'
        f'aws iam delete-role --role-name {SECRETS_ROTATION_ROLE_NAME}'
    )
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
    lambda_client = _lambda_client()
    iam = _iam_client()
    deleted_function = _ignore_missing_codes(
        lambda: lambda_client.delete_function(FunctionName=SECRETS_ROTATION_FUNCTION_NAME),
        {'ResourceNotFoundException'},
    )
    logs = _logs_client()
    deleted_log_group = _ignore_missing_codes(
        lambda: logs.delete_log_group(logGroupName=SECRETS_ROTATION_LOG_GROUP_NAME),
        {'ResourceNotFoundException'},
    )
    deleted_policy = _ignore_missing(lambda: iam.delete_role_policy(
        RoleName=SECRETS_ROTATION_ROLE_NAME,
        PolicyName=SECRETS_ROTATION_POLICY_NAME,
    ))
    deleted_role = _ignore_missing(lambda: iam.delete_role(RoleName=SECRETS_ROTATION_ROLE_NAME))
    cache.delete_many([
        SECRETS_MANAGER_INITIAL_CACHE_KEY,
        SECRETS_MANAGER_UPDATED_CACHE_KEY,
        SECRETS_ROTATED_CACHE_KEY,
    ])
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
        'json': {
            'deleted': deleted,
            'name': SECRETS_MANAGER_SECRET_NAME,
            'deleted_function': deleted_function,
            'deleted_log_group': deleted_log_group,
            'deleted_policy': deleted_policy,
            'deleted_role': deleted_role,
        },
        'duration_ms': round((time.perf_counter() - started) * 1000),
        'reset': True,
        'deleted': deleted,
        'verification': verification,
    }


LABS = [SECRETS_MANAGER_SECRET_LIFECYCLE_LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'secretsmanager' and lab_key == 'secret-lifecycle':
        runners = {
            'create-secret': _run_secretsmanager_create_secret,
            'get-initial-secret': _run_secretsmanager_get_initial_secret,
            'put-secret-value': _run_secretsmanager_put_secret_value,
            'get-updated-secret': _run_secretsmanager_get_updated_secret,
            'create-rotation-role': _run_secrets_create_rotation_role,
            'create-rotation-function': _run_secrets_create_rotation_function,
            'allow-rotation-invoke': _run_secrets_allow_rotation_invoke,
            'rotate-secret': _run_secrets_rotate_secret,
            'get-rotated-secret': _run_secrets_get_rotated_secret,
            'describe-secret': _run_secretsmanager_describe_secret,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'secretsmanager' and lab_key == 'secret-lifecycle':
        updated = _verify_secretsmanager_updated_secret()
        rotation_enabled = _verify_secrets_rotation_enabled()
        checks = {
            'create-secret': _verify_secretsmanager_secret_exists(),
            'get-initial-secret': _verify_secretsmanager_initial_read(),
            'put-secret-value': updated,
            'get-updated-secret': updated,
            'create-rotation-role': _verify_secrets_rotation_role(),
            'create-rotation-function': _verify_secrets_rotation_function(),
            'allow-rotation-invoke': _verify_secrets_rotation_permission(),
            'rotate-secret': rotation_enabled,
            'get-rotated-secret': _verify_secrets_rotated_value(),
            'describe-secret': rotation_enabled,
        }
        verified = {step: check.get('status') == 'passed' for step, check in checks.items()}
        step_order = [step['key'] for step in SECRETS_MANAGER_SECRET_LIFECYCLE_LAB['steps']]
        return {
            'service': service_key,
            'lab': lab_key,
            'complete': all(verified.values()),
            'steps': {
                step: {'verified': verified[step], 'verification': checks[step] if verified[step] else None}
                for step in step_order
            },
        }

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'secretsmanager' and lab_key == 'secret-lifecycle':
        return _reset_secretsmanager_secret_lifecycle()

    raise ValueError('Lab reset not found')
