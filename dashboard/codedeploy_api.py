"""Interactive CodeDeploy helpers for local deployment workflows."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('codedeploy')


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


def create_application(name: str, compute_platform: str = 'Server') -> dict[str, Any]:
    clean_name = _required(name, 'Application name')
    response = _client().create_application(
        applicationName=clean_name,
        computePlatform=(compute_platform or 'Server').strip(),
    )
    return {'application_name': clean_name, 'application_id': response.get('applicationId'), 'response': _clean_response(response)}


def delete_application(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Application name')
    response = _client().delete_application(applicationName=clean_name)
    return {'application_name': clean_name, 'response': _clean_response(response)}


def create_deployment_group(application_name: str, group_name: str, options: Any) -> dict[str, Any]:
    clean_app = _required(application_name, 'Application name')
    clean_group = _required(group_name, 'Deployment group name')
    clean_options = _dict_value(options, 'Deployment group options')
    payload = {
        'applicationName': clean_app,
        'deploymentGroupName': clean_group,
        'serviceRoleArn': _required(clean_options.pop('serviceRoleArn', clean_options.pop('service_role_arn', '')), 'Service role ARN'),
        **clean_options,
    }
    response = _client().create_deployment_group(**payload)
    return {
        'application_name': clean_app,
        'deployment_group_name': clean_group,
        'deployment_group_id': response.get('deploymentGroupId'),
        'response': _clean_response(response),
    }


def delete_deployment_group(application_name: str, group_name: str) -> dict[str, Any]:
    clean_app = _required(application_name, 'Application name')
    clean_group = _required(group_name, 'Deployment group name')
    response = _client().delete_deployment_group(applicationName=clean_app, deploymentGroupName=clean_group)
    return {'application_name': clean_app, 'deployment_group_name': clean_group, 'response': _clean_response(response)}


def create_deployment_config(name: str, options: Any) -> dict[str, Any]:
    clean_name = _required(name, 'Deployment config name')
    response = _client().create_deployment_config(
        deploymentConfigName=clean_name,
        **_dict_value(options, 'Deployment config options'),
    )
    return {'deployment_config_name': clean_name, 'deployment_config_id': response.get('deploymentConfigId'), 'response': _clean_response(response)}


def delete_deployment_config(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Deployment config name')
    response = _client().delete_deployment_config(deploymentConfigName=clean_name)
    return {'deployment_config_name': clean_name, 'response': _clean_response(response)}


def create_deployment(application_name: str, group_name: str, revision: Any, options: Any = None) -> dict[str, Any]:
    clean_app = _required(application_name, 'Application name')
    clean_group = _required(group_name, 'Deployment group name')
    payload = {
        'applicationName': clean_app,
        'deploymentGroupName': clean_group,
        'revision': _dict_value(revision, 'Revision'),
        **_dict_value(options, 'Deployment options'),
    }
    response = _client().create_deployment(**payload)
    return {'deployment_id': response.get('deploymentId'), 'response': _clean_response(response)}


def get_deployment(deployment_id: str) -> dict[str, Any]:
    clean_id = _required(deployment_id, 'Deployment ID')
    response = _client().get_deployment(deploymentId=clean_id)
    return {'deployment_id': clean_id, 'deployment': _clean_response(response.get('deploymentInfo', {})), 'response': _clean_response(response)}


def stop_deployment(deployment_id: str, auto_rollback_enabled: bool = False) -> dict[str, Any]:
    clean_id = _required(deployment_id, 'Deployment ID')
    response = _client().stop_deployment(deploymentId=clean_id, autoRollbackEnabled=bool(auto_rollback_enabled))
    return {'deployment_id': clean_id, 'status': response.get('status'), 'response': _clean_response(response)}


def continue_deployment(deployment_id: str) -> dict[str, Any]:
    clean_id = _required(deployment_id, 'Deployment ID')
    response = _client().continue_deployment(deploymentId=clean_id)
    return {'deployment_id': clean_id, 'response': _clean_response(response)}


def put_lifecycle_event_hook_execution_status(deployment_id: str, hook_execution_id: str, status: str) -> dict[str, Any]:
    response = _client().put_lifecycle_event_hook_execution_status(
        deploymentId=_required(deployment_id, 'Deployment ID'),
        lifecycleEventHookExecutionId=_required(hook_execution_id, 'Lifecycle hook execution ID'),
        status=_required(status, 'Status'),
    )
    return {'deployment_id': deployment_id, 'hook_execution_id': hook_execution_id, 'status': status, 'response': _clean_response(response)}


def tag_resource(resource_arn: str, tags: Any) -> dict[str, Any]:
    clean_arn = _required(resource_arn, 'Resource ARN')
    clean_tags = _list_value(tags, 'Tags')
    if not clean_tags:
        raise ValueError('Tags are required')
    response = _client().tag_resource(ResourceArn=clean_arn, Tags=clean_tags)
    return {'resource_arn': clean_arn, 'response': _clean_response(response)}


def untag_resource(resource_arn: str, tag_keys: Any) -> dict[str, Any]:
    clean_arn = _required(resource_arn, 'Resource ARN')
    keys = [str(key) for key in _list_value(tag_keys, 'Tag keys')]
    if not keys:
        raise ValueError('Tag keys are required')
    response = _client().untag_resource(ResourceArn=clean_arn, TagKeys=keys)
    return {'resource_arn': clean_arn, 'tag_keys': keys, 'response': _clean_response(response)}
