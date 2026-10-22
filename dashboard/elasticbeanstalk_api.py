"""Elastic Beanstalk helpers limited to Floci's persisted management plane."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory


def _client():
    return FlociClientFactory().client('elasticbeanstalk')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _options(value: Any) -> list[dict[str, str]]:
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ValueError('Option settings must be an array')
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each option setting must be an object')
        result.append(
            {
                'Namespace': _required(item.get('Namespace') or item.get('namespace'), 'Option namespace'),
                'OptionName': _required(item.get('OptionName') or item.get('option_name'), 'Option name'),
                'Value': str(item.get('Value') if item.get('Value') is not None else item.get('value', '')),
                **(
                    {'ResourceName': str(item.get('ResourceName') or item.get('resource_name'))}
                    if item.get('ResourceName') or item.get('resource_name')
                    else {}
                ),
            }
        )
    return result


def create_application(name: str, *, description: str = '') -> dict[str, Any]:
    response = _client().create_application(
        ApplicationName=_required(name, 'Application name'), Description=str(description or '')
    )
    return response.get('Application', {})


def update_application(name: str, *, description: str = '') -> dict[str, Any]:
    response = _client().update_application(
        ApplicationName=_required(name, 'Application name'), Description=str(description or '')
    )
    return response.get('Application', {})


def delete_application(name: str, *, terminate_environments: bool = False) -> dict[str, Any]:
    clean = _required(name, 'Application name')
    _client().delete_application(ApplicationName=clean, TerminateEnvByForce=bool(terminate_environments))
    return {'application_name': clean, 'deleted': True}


def create_application_version(
    application_name: str, version_label: str, *, description: str = '', s3_bucket: str = '', s3_key: str = ''
) -> dict[str, Any]:
    app = _required(application_name, 'Application name')
    label = _required(version_label, 'Version label')
    kwargs: dict[str, Any] = {'ApplicationName': app, 'VersionLabel': label, 'Description': str(description or '')}
    if s3_bucket or s3_key:
        kwargs['SourceBundle'] = {'S3Bucket': _required(s3_bucket, 'S3 bucket'), 'S3Key': _required(s3_key, 'S3 key')}
    return _client().create_application_version(**kwargs).get('ApplicationVersion', {})


def delete_application_version(application_name: str, version_label: str) -> dict[str, Any]:
    app = _required(application_name, 'Application name')
    label = _required(version_label, 'Version label')
    _client().delete_application_version(ApplicationName=app, VersionLabel=label)
    return {'application_name': app, 'version_label': label, 'deleted': True}


def create_environment(
    application_name: str,
    environment_name: str,
    *,
    description: str = '',
    version_label: str = '',
    cname_prefix: str = '',
    solution_stack_name: str = '',
    platform_arn: str = '',
    option_settings: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'ApplicationName': _required(application_name, 'Application name'),
        'EnvironmentName': _required(environment_name, 'Environment name'),
    }
    for key, value in [
        ('Description', description),
        ('VersionLabel', version_label),
        ('CNAMEPrefix', cname_prefix),
        ('SolutionStackName', solution_stack_name),
        ('PlatformArn', platform_arn),
    ]:
        if value:
            kwargs[key] = str(value)
    options = _options(option_settings)
    if options:
        kwargs['OptionSettings'] = options
    return _client().create_environment(**kwargs)


def update_environment(
    environment_name: str,
    *,
    description: Any = None,
    version_label: str = '',
    solution_stack_name: str = '',
    platform_arn: str = '',
    option_settings: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {'EnvironmentName': _required(environment_name, 'Environment name')}
    if description is not None:
        kwargs['Description'] = str(description)
    for key, value in [
        ('VersionLabel', version_label),
        ('SolutionStackName', solution_stack_name),
        ('PlatformArn', platform_arn),
    ]:
        if value:
            kwargs[key] = str(value)
    options = _options(option_settings)
    if options:
        kwargs['OptionSettings'] = options
    return _client().update_environment(**kwargs)


def terminate_environment(environment_name: str) -> dict[str, Any]:
    return _client().terminate_environment(EnvironmentName=_required(environment_name, 'Environment name'))


def check_dns_availability(cname_prefix: str) -> dict[str, Any]:
    clean = _required(cname_prefix, 'CNAME prefix')
    response = _client().check_dns_availability(CNAMEPrefix=clean)
    return {
        'cname_prefix': clean,
        'available': response.get('Available'),
        'fully_qualified_cname': response.get('FullyQualifiedCNAME'),
    }
