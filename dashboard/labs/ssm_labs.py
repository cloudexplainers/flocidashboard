"""SSM Parameter Store workflow labs: definitions, step runners, verifiers, status, and reset."""

from __future__ import annotations

import json
import time
from typing import Any

from botocore.exceptions import ClientError

from dashboard.aws import _clean_response
from .monolith import (
    _config_lab_step_result,
    _error_code,
    _json_text,
    _parse_json_text,
    _ssm_client,
)


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


LABS = [SSM_PARAMETER_STORE_LAB]


def run_step(service_key: str, lab_key: str, step_key: str) -> dict[str, Any]:
    if service_key == 'ssm' and lab_key == 'parameter-store-config':
        runners = {
            'put-parameter': _run_ssm_put_parameter,
            'get-parameter': _run_ssm_get_parameter,
            'get-parameters-by-path': _run_ssm_get_parameters_by_path,
        }
        if step_key in runners:
            return runners[step_key]()

    raise ValueError('Lab step not found')


def status(service_key: str, lab_key: str) -> dict[str, Any]:
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

    return {
        'service': service_key,
        'lab': lab_key,
        'complete': False,
        'steps': {},
    }


def reset(service_key: str, lab_key: str) -> dict[str, Any]:
    if service_key == 'ssm' and lab_key == 'parameter-store-config':
        return _reset_ssm_parameter_store_config()

    raise ValueError('Lab reset not found')
