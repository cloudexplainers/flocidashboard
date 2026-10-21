"""Secrets Manager workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError
from django.core.cache import cache

from dashboard.aws import _clean_response
from .monolith import (
    _config_lab_step_result,
    _error_code,
    _json_text,
    _secret_string_json,
    _secretsmanager_client,
)


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


LABS = [SECRETS_MANAGER_SECRET_LIFECYCLE_LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
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

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
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
