"""HTTP endpoints for the SageMaker AI workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from . import sagemaker_api as api
from .actions import handle_action_error, parse_json_body


def _run(request, operation, call):
    try:
        body = parse_json_body(request) if request.method in ('POST', 'PUT', 'PATCH') else {}
        return JsonResponse(call(body))
    except Exception as exc:
        return handle_action_error(exc, service='sagemaker', operation=operation)


@require_http_methods(['POST'])
def training_jobs(request):
    return _run(request, 'create_training_job', lambda body: api.create_training_job(
        body.get('name') or '',
        role_arn=body.get('role_arn') or '',
        image=body.get('image') or '',
        input_s3_uri=body.get('input_s3_uri') or '',
        output_s3_path=body.get('output_s3_path') or '',
        entrypoint=body.get('entrypoint'),
        hyperparameters=body.get('hyperparameters'),
        instance_type=body.get('instance_type') or 'ml.m5.large',
        max_runtime_seconds=body.get('max_runtime_seconds') or 600,
    ))


@require_http_methods(['POST'])
def training_job_describe(request):
    return _run(request, 'describe_training_job', lambda body: api.describe_training_job(body.get('name') or ''))


@require_http_methods(['POST'])
def training_job_stop(request, job_name: str):
    return _run(request, 'stop_training_job', lambda body: api.stop_training_job(job_name))


@require_http_methods(['POST'])
def models(request):
    return _run(request, 'create_model', lambda body: api.create_model(
        body.get('name') or '',
        role_arn=body.get('role_arn') or '',
        image=body.get('image') or '',
        model_data_url=body.get('model_data_url') or '',
        environment=body.get('environment'),
    ))


@require_http_methods(['DELETE'])
def model_detail(request, model_name: str):
    return _run(request, 'delete_model', lambda body: api.delete_model(model_name))


@require_http_methods(['POST'])
def endpoint_configs(request):
    return _run(request, 'create_endpoint_config', lambda body: api.create_endpoint_config(
        body.get('name') or '',
        model_name=body.get('model_name') or '',
        instance_type=body.get('instance_type') or 'ml.t2.medium',
        variant_name=body.get('variant_name') or 'AllTraffic',
    ))


@require_http_methods(['DELETE'])
def endpoint_config_detail(request, config_name: str):
    return _run(request, 'delete_endpoint_config', lambda body: api.delete_endpoint_config(config_name))


@require_http_methods(['POST'])
def endpoints(request):
    return _run(request, 'create_endpoint', lambda body: api.create_endpoint(
        body.get('name') or '', endpoint_config_name=body.get('endpoint_config_name') or '',
    ))


@require_http_methods(['PATCH', 'DELETE'])
def endpoint_detail(request, endpoint_name: str):
    if request.method == 'DELETE':
        return _run(request, 'delete_endpoint', lambda body: api.delete_endpoint(endpoint_name))
    return _run(request, 'update_endpoint', lambda body: api.update_endpoint(
        endpoint_name, endpoint_config_name=body.get('endpoint_config_name') or '',
    ))


@require_http_methods(['POST'])
def endpoint_invoke(request, endpoint_name: str):
    return _run(request, 'invoke_endpoint', lambda body: api.invoke_endpoint(
        endpoint_name, body=body.get('body') or '', content_type=body.get('content_type') or 'text/csv',
    ))
