"""Interactive AppConfig helpers for local configuration workflows."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory, _clean_response
from .aws.appconfig import _appconfig_content_preview


def _client():
    return FlociClientFactory().client('appconfig')


def _data_client():
    return FlociClientFactory().client('appconfigdata')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def create_application(name: str, *, description: str = '') -> dict[str, Any]:
    kwargs = {'Name': _required(name, 'Application name')}
    if description:
        kwargs['Description'] = description
    response = _client().create_application(**kwargs)
    return {'application_id': response.get('Id'), 'application': _clean_response(response)}


def delete_application(application_id: str) -> dict[str, Any]:
    clean_id = _required(application_id, 'Application ID')
    response = _client().delete_application(ApplicationId=clean_id)
    return {'application_id': clean_id, 'response': _clean_response(response)}


def create_environment(application_id: str, name: str, *, description: str = '') -> dict[str, Any]:
    kwargs = {'ApplicationId': _required(application_id, 'Application ID'), 'Name': _required(name, 'Environment name')}
    if description:
        kwargs['Description'] = description
    response = _client().create_environment(**kwargs)
    return {'application_id': kwargs['ApplicationId'], 'environment_id': response.get('Id'), 'environment': _clean_response(response)}


def create_configuration_profile(application_id: str, name: str, *, location_uri: str = 'hosted', profile_type: str = 'AWS.Freeform', description: str = '') -> dict[str, Any]:
    kwargs = {
        'ApplicationId': _required(application_id, 'Application ID'),
        'Name': _required(name, 'Configuration profile name'),
        'LocationUri': location_uri or 'hosted',
        'Type': profile_type or 'AWS.Freeform',
    }
    if description:
        kwargs['Description'] = description
    response = _client().create_configuration_profile(**kwargs)
    return {'application_id': kwargs['ApplicationId'], 'configuration_profile_id': response.get('Id'), 'configuration_profile': _clean_response(response)}


def create_hosted_configuration_version(application_id: str, profile_id: str, content: Any, *, content_type: str = 'application/json', description: str = '') -> dict[str, Any]:
    if isinstance(content, bytes):
        raw_content = content
    elif isinstance(content, str):
        raw_content = content.encode('utf-8')
    else:
        raise ValueError('Content must be a string')
    kwargs = {
        'ApplicationId': _required(application_id, 'Application ID'),
        'ConfigurationProfileId': _required(profile_id, 'Configuration profile ID'),
        'Content': raw_content,
        'ContentType': content_type or 'application/json',
    }
    if description:
        kwargs['Description'] = description
    response = _client().create_hosted_configuration_version(**kwargs)
    return {
        'application_id': kwargs['ApplicationId'],
        'configuration_profile_id': kwargs['ConfigurationProfileId'],
        'version_number': response.get('VersionNumber'),
        'version': _clean_response(response),
    }


def create_deployment_strategy(name: str, *, duration_minutes: int = 0, growth_factor: float = 100, final_bake_minutes: int = 0, description: str = '') -> dict[str, Any]:
    kwargs = {
        'Name': _required(name, 'Deployment strategy name'),
        'DeploymentDurationInMinutes': int(duration_minutes),
        'GrowthFactor': float(growth_factor),
        'FinalBakeTimeInMinutes': int(final_bake_minutes),
        'ReplicateTo': 'NONE',
    }
    if description:
        kwargs['Description'] = description
    response = _client().create_deployment_strategy(**kwargs)
    return {'deployment_strategy_id': response.get('Id'), 'deployment_strategy': _clean_response(response)}


def start_deployment(application_id: str, environment_id: str, profile_id: str, configuration_version: Any, deployment_strategy_id: str, *, description: str = '') -> dict[str, Any]:
    kwargs = {
        'ApplicationId': _required(application_id, 'Application ID'),
        'EnvironmentId': _required(environment_id, 'Environment ID'),
        'ConfigurationProfileId': _required(profile_id, 'Configuration profile ID'),
        'ConfigurationVersion': _required(configuration_version, 'Configuration version'),
        'DeploymentStrategyId': _required(deployment_strategy_id, 'Deployment strategy ID'),
    }
    if description:
        kwargs['Description'] = description
    response = _client().start_deployment(**kwargs)
    return {'deployment_number': response.get('DeploymentNumber'), 'deployment': _clean_response(response)}


def start_configuration_session(application_id: str, environment_id: str, profile_id: str) -> dict[str, Any]:
    response = _data_client().start_configuration_session(
        ApplicationIdentifier=_required(application_id, 'Application ID'),
        EnvironmentIdentifier=_required(environment_id, 'Environment ID'),
        ConfigurationProfileIdentifier=_required(profile_id, 'Configuration profile ID'),
    )
    return {'initial_configuration_token': response.get('InitialConfigurationToken'), 'response': _clean_response(response)}


def get_latest_configuration(configuration_token: str) -> dict[str, Any]:
    response = _data_client().get_latest_configuration(ConfigurationToken=_required(configuration_token, 'Configuration token'))
    preview = _appconfig_content_preview(response.get('Configuration'))
    return {
        'next_poll_configuration_token': response.get('NextPollConfigurationToken'),
        'next_poll_interval_seconds': response.get('NextPollIntervalInSeconds'),
        'content_type': response.get('ContentType'),
        'configuration': preview,
        'response_metadata': _clean_response(response.get('ResponseMetadata', {})),
    }
