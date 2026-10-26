"""SageMaker AI inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _safe_value


def _list(client, operation: str, key: str) -> list[dict[str, Any]]:
    return _safe_value(lambda: core._paginate(client, operation, key), [])


def sagemaker_inventory() -> dict[str, Any]:
    sagemaker = core.FlociClientFactory().client('sagemaker')

    training_jobs = []
    for summary in _list(sagemaker, 'list_training_jobs', 'TrainingJobSummaries'):
        name = summary.get('TrainingJobName')
        detail = _safe_value(lambda name=name: sagemaker.describe_training_job(TrainingJobName=name), {}) or {}
        algorithm = detail.get('AlgorithmSpecification') or {}
        training_jobs.append({
            'name': name,
            'status': summary.get('TrainingJobStatus'),
            'secondary_status': detail.get('SecondaryStatus'),
            'image': algorithm.get('TrainingImage'),
            'instance_type': (detail.get('ResourceConfig') or {}).get('InstanceType'),
            'hyperparameters': detail.get('HyperParameters'),
            'model_artifacts': (detail.get('ModelArtifacts') or {}).get('S3ModelArtifacts'),
            'failure_reason': detail.get('FailureReason'),
            'created': summary.get('CreationTime'),
            'arn': summary.get('TrainingJobArn'),
        })

    models = []
    for summary in _list(sagemaker, 'list_models', 'Models'):
        name = summary.get('ModelName')
        detail = _safe_value(lambda name=name: sagemaker.describe_model(ModelName=name), {}) or {}
        container = detail.get('PrimaryContainer') or {}
        models.append({
            'name': name,
            'image': container.get('Image'),
            'model_data_url': container.get('ModelDataUrl'),
            'environment': container.get('Environment'),
            'execution_role': detail.get('ExecutionRoleArn'),
            'created': summary.get('CreationTime'),
            'arn': summary.get('ModelArn'),
        })

    endpoint_configs = []
    for summary in _list(sagemaker, 'list_endpoint_configs', 'EndpointConfigs'):
        name = summary.get('EndpointConfigName')
        detail = _safe_value(lambda name=name: sagemaker.describe_endpoint_config(EndpointConfigName=name), {}) or {}
        variants = detail.get('ProductionVariants') or []
        endpoint_configs.append({
            'name': name,
            'models': ', '.join(str(variant.get('ModelName')) for variant in variants),
            'instance_types': ', '.join(str(variant.get('InstanceType')) for variant in variants),
            'variants': variants,
            'created': summary.get('CreationTime'),
            'arn': summary.get('EndpointConfigArn'),
        })

    endpoints = []
    for summary in _list(sagemaker, 'list_endpoints', 'Endpoints'):
        name = summary.get('EndpointName')
        detail = _safe_value(lambda name=name: sagemaker.describe_endpoint(EndpointName=name), {}) or {}
        endpoints.append({
            'name': name,
            'status': summary.get('EndpointStatus'),
            'endpoint_config': detail.get('EndpointConfigName'),
            'failure_reason': detail.get('FailureReason'),
            'created': summary.get('CreationTime'),
            'arn': summary.get('EndpointArn'),
        })

    return {
        'summary': {
            'training_jobs': len(training_jobs),
            'models': len(models),
            'endpoint_configs': len(endpoint_configs),
            'endpoints': len(endpoints),
            'endpoints_in_service': sum(1 for endpoint in endpoints if endpoint['status'] == 'InService'),
        },
        'training_jobs': training_jobs,
        'models': models,
        'endpoint_configs': endpoint_configs,
        'endpoints': endpoints,
        'notes': [
            'Floci runs training jobs and endpoints as real Docker containers under the SageMaker /opt/ml contract.',
            'A training job copies each S3 input channel to /opt/ml/input/data/<channel> and uploads what the container writes to /opt/ml/model as model.tar.gz. The container must create /opt/ml/model itself.',
            'An endpoint starts the model image with the command "serve", copies ModelDataUrl into /opt/ml/model, waits for GET /ping on port 8080, and sends InvokeEndpoint to POST /invocations.',
            'Endpoints are CPU-only. Hyperparameter tuning, batch transform, Pipelines, Model Monitor, Clarify, Feature Store, and JumpStart are not emulated.',
        ],
    }


def list_sagemaker_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    sagemaker = factory.client('sagemaker')
    resources: list[dict[str, Any]] = []
    for job in _list(sagemaker, 'list_training_jobs', 'TrainingJobSummaries'):
        resources.append({'type': 'training_job', 'name': job.get('TrainingJobName'), 'arn': job.get('TrainingJobArn'),
                          'status': job.get('TrainingJobStatus')})
    for model in _list(sagemaker, 'list_models', 'Models'):
        resources.append({'type': 'model', 'name': model.get('ModelName'), 'arn': model.get('ModelArn')})
    for config in _list(sagemaker, 'list_endpoint_configs', 'EndpointConfigs'):
        resources.append({'type': 'endpoint_config', 'name': config.get('EndpointConfigName'), 'arn': config.get('EndpointConfigArn')})
    for endpoint in _list(sagemaker, 'list_endpoints', 'Endpoints'):
        resources.append({'type': 'endpoint', 'name': endpoint.get('EndpointName'), 'arn': endpoint.get('EndpointArn'),
                          'status': endpoint.get('EndpointStatus')})
    return resources
