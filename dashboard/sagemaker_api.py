"""Interactive SageMaker AI helpers: training jobs, models, endpoints, and invocations."""

from __future__ import annotations

import json
from typing import Any

from botocore.config import Config

from .aws import FlociClientFactory, _clean_response


def _client(name: str = 'sagemaker'):
    # Floci starts and stops Docker containers inside CreateEndpoint, UpdateEndpoint, and
    # DeleteEndpoint, which can outlast the default 5-second read timeout. A retried
    # UpdateEndpoint starts the container twice and can lose the endpoint, so never retry.
    factory = FlociClientFactory()
    factory.config = factory.config.merge(Config(read_timeout=60, retries={'max_attempts': 0}))
    return factory.client(name)


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _dict_value(value: Any, label: str) -> dict[str, Any]:
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object')
    return value


def _list_value(value: Any, label: str) -> list[Any]:
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a JSON array')
    return value


def create_training_job(
    name: str,
    *,
    role_arn: str,
    image: str,
    input_s3_uri: str,
    output_s3_path: str,
    entrypoint: Any = None,
    hyperparameters: Any = None,
    instance_type: str = 'ml.m5.large',
    max_runtime_seconds: int = 600,
) -> dict[str, Any]:
    algorithm: dict[str, Any] = {'TrainingImage': _required(image, 'Training image'), 'TrainingInputMode': 'File'}
    command = [str(part) for part in _list_value(entrypoint, 'Container entrypoint')]
    if command:
        algorithm['ContainerEntrypoint'] = command
    payload: dict[str, Any] = {
        'TrainingJobName': _required(name, 'Training job name'),
        'RoleArn': _required(role_arn, 'Role ARN'),
        'AlgorithmSpecification': algorithm,
        'InputDataConfig': [{
            'ChannelName': 'train',
            'DataSource': {'S3DataSource': {'S3DataType': 'S3Prefix', 'S3Uri': _required(input_s3_uri, 'Input S3 URI')}},
        }],
        'OutputDataConfig': {'S3OutputPath': _required(output_s3_path, 'Output S3 path')},
        'ResourceConfig': {'InstanceType': instance_type or 'ml.m5.large', 'InstanceCount': 1, 'VolumeSizeInGB': 1},
        'StoppingCondition': {'MaxRuntimeInSeconds': int(max_runtime_seconds or 600)},
    }
    params = {str(key): str(value) for key, value in _dict_value(hyperparameters, 'Hyperparameters').items()}
    if params:
        payload['HyperParameters'] = params
    response = _client().create_training_job(**payload)
    return {'training_job_name': payload['TrainingJobName'], 'response': _clean_response(response)}


def describe_training_job(name: str) -> dict[str, Any]:
    return {'training_job': _clean_response(_client().describe_training_job(TrainingJobName=_required(name, 'Training job name')))}


def stop_training_job(name: str) -> dict[str, Any]:
    clean = _required(name, 'Training job name')
    return {'training_job_name': clean, 'response': _clean_response(_client().stop_training_job(TrainingJobName=clean))}


def create_model(name: str, *, role_arn: str, image: str, model_data_url: str = '', environment: Any = None) -> dict[str, Any]:
    container: dict[str, Any] = {'Image': _required(image, 'Image')}
    if (model_data_url or '').strip():
        container['ModelDataUrl'] = model_data_url.strip()
    env = {str(key): str(value) for key, value in _dict_value(environment, 'Environment').items()}
    if env:
        container['Environment'] = env
    clean = _required(name, 'Model name')
    response = _client().create_model(ModelName=clean, ExecutionRoleArn=_required(role_arn, 'Role ARN'), PrimaryContainer=container)
    return {'model_name': clean, 'response': _clean_response(response)}


def delete_model(name: str) -> dict[str, Any]:
    clean = _required(name, 'Model name')
    return {'model_name': clean, 'response': _clean_response(_client().delete_model(ModelName=clean))}


def create_endpoint_config(name: str, *, model_name: str, instance_type: str = 'ml.t2.medium', variant_name: str = 'AllTraffic') -> dict[str, Any]:
    clean = _required(name, 'Endpoint config name')
    response = _client().create_endpoint_config(
        EndpointConfigName=clean,
        ProductionVariants=[{
            'VariantName': variant_name or 'AllTraffic',
            'ModelName': _required(model_name, 'Model name'),
            'InitialInstanceCount': 1,
            'InstanceType': instance_type or 'ml.t2.medium',
        }],
    )
    return {'endpoint_config_name': clean, 'response': _clean_response(response)}


def delete_endpoint_config(name: str) -> dict[str, Any]:
    clean = _required(name, 'Endpoint config name')
    return {'endpoint_config_name': clean, 'response': _clean_response(_client().delete_endpoint_config(EndpointConfigName=clean))}


def create_endpoint(name: str, *, endpoint_config_name: str) -> dict[str, Any]:
    clean = _required(name, 'Endpoint name')
    response = _client().create_endpoint(EndpointName=clean, EndpointConfigName=_required(endpoint_config_name, 'Endpoint config name'))
    return {'endpoint_name': clean, 'response': _clean_response(response)}


def update_endpoint(name: str, *, endpoint_config_name: str) -> dict[str, Any]:
    clean = _required(name, 'Endpoint name')
    response = _client().update_endpoint(EndpointName=clean, EndpointConfigName=_required(endpoint_config_name, 'Endpoint config name'))
    return {'endpoint_name': clean, 'response': _clean_response(response)}


def delete_endpoint(name: str) -> dict[str, Any]:
    clean = _required(name, 'Endpoint name')
    return {'endpoint_name': clean, 'response': _clean_response(_client().delete_endpoint(EndpointName=clean))}


def invoke_endpoint(name: str, *, body: str, content_type: str = 'text/csv') -> dict[str, Any]:
    clean = _required(name, 'Endpoint name')
    response = _client('sagemaker-runtime').invoke_endpoint(
        EndpointName=clean, ContentType=content_type or 'text/csv', Body=str(body or '').encode('utf-8'),
    )
    raw = response['Body'].read().decode('utf-8', errors='replace')
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError:
        parsed = raw
    return {'endpoint_name': clean, 'content_type': response.get('ContentType'), 'body': parsed}
