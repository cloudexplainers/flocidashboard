"""AppConfig inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _appconfig_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def _appconfig_content_preview(content: Any) -> dict[str, Any]:
    if content is None:
        return {'size_bytes': 0, 'preview': None, 'encoding': None}

    if hasattr(content, 'read'):
        content = content.read()

    if isinstance(content, str):
        raw = content.encode('utf-8')
        text = content
    elif isinstance(content, bytes):
        raw = content
        try:
            text = content.decode('utf-8')
        except UnicodeDecodeError:
            text = None
    else:
        text = json.dumps(content, default=str)
        raw = text.encode('utf-8')

    return {
        'size_bytes': len(raw),
        'preview': text[:500] if text is not None else None,
        'encoding': 'utf-8' if text is not None else 'binary',
    }


def appconfig_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    appconfig = factory.client('appconfig')
    applications = _safe_value(lambda: core._paginate(appconfig, 'list_applications', 'Items'), [])
    deployment_strategies = _safe_value(lambda: core._paginate(appconfig, 'list_deployment_strategies', 'Items'), [])

    def hosted_version_detail(application_id: str, profile_id: str, version: dict[str, Any]) -> dict[str, Any]:
        version_number = version.get('VersionNumber')
        details = _appconfig_optional(
            lambda: appconfig.get_hosted_configuration_version(
                ApplicationId=application_id,
                ConfigurationProfileId=profile_id,
                VersionNumber=version_number,
            ),
            {'BadRequestException', 'NotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}
        content_preview = _appconfig_content_preview(described.get('Content'))

        return {
            'name': str(version_number),
            'version_number': version_number,
            'application_id': application_id,
            'configuration_profile_id': profile_id,
            'description': version.get('Description') or described.get('Description'),
            'content_type': version.get('ContentType') or described.get('ContentType'),
            'version_label': version.get('VersionLabel') or described.get('VersionLabel'),
            'kms_key_arn': described.get('KmsKeyArn'),
            'content_size_bytes': content_preview.get('size_bytes'),
            'content_encoding': content_preview.get('encoding'),
            'content_preview': content_preview.get('preview'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def profile_detail(application_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        profile_id = profile.get('Id')
        details = _appconfig_optional(
            lambda: appconfig.get_configuration_profile(
                ApplicationId=application_id,
                ConfigurationProfileId=profile_id,
            ),
            {'BadRequestException', 'NotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}
        versions = _appconfig_optional(
            lambda: core._paginate(
                appconfig,
                'list_hosted_configuration_versions',
                'Items',
                ApplicationId=application_id,
                ConfigurationProfileId=profile_id,
            ),
            {'BadRequestException', 'NotFoundException'},
        )
        version_details = [
            hosted_version_detail(application_id, profile_id, version)
            for version in versions
        ] if isinstance(versions, list) else []

        return {
            'name': profile.get('Name') or described.get('Name') or profile_id,
            'id': profile_id,
            'application_id': application_id,
            'description': profile.get('Description') or described.get('Description'),
            'location_uri': profile.get('LocationUri') or described.get('LocationUri'),
            'retrieval_role_arn': described.get('RetrievalRoleArn'),
            'type': profile.get('Type') or described.get('Type'),
            'validators': described.get('Validators'),
            'kms_key_arn': described.get('KmsKeyArn'),
            'kms_key_identifier': described.get('KmsKeyIdentifier'),
            'hosted_version_count': len(version_details),
            'hosted_versions': version_details,
            'details': details if isinstance(details, dict) and details.get('error') else None,
            'hosted_versions_error': versions if isinstance(versions, dict) and versions.get('error') else None,
        }

    def deployment_detail(application_id: str, environment_id: str, deployment: dict[str, Any]) -> dict[str, Any]:
        deployment_number = deployment.get('DeploymentNumber')
        details = _appconfig_optional(
            lambda: appconfig.get_deployment(
                ApplicationId=application_id,
                EnvironmentId=environment_id,
                DeploymentNumber=deployment_number,
            ),
            {'BadRequestException', 'NotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}

        return {
            'name': str(deployment_number),
            'deployment_number': deployment_number,
            'application_id': application_id,
            'environment_id': environment_id,
            'state': deployment.get('State') or described.get('State'),
            'configuration_name': deployment.get('ConfigurationName') or described.get('ConfigurationName'),
            'configuration_profile_id': deployment.get('ConfigurationProfileId') or described.get('ConfigurationProfileId'),
            'configuration_version': deployment.get('ConfigurationVersion') or described.get('ConfigurationVersion'),
            'deployment_strategy_id': deployment.get('DeploymentStrategyId') or described.get('DeploymentStrategyId'),
            'percentage_complete': deployment.get('PercentageComplete') or described.get('PercentageComplete'),
            'started_at': deployment.get('StartedAt') or described.get('StartedAt'),
            'completed_at': deployment.get('CompletedAt') or described.get('CompletedAt'),
            'event_log': described.get('EventLog'),
            'applied_extensions': described.get('AppliedExtensions'),
            'version_label': described.get('VersionLabel'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def environment_detail(application_id: str, environment: dict[str, Any]) -> dict[str, Any]:
        environment_id = environment.get('Id')
        details = _appconfig_optional(
            lambda: appconfig.get_environment(
                ApplicationId=application_id,
                EnvironmentId=environment_id,
            ),
            {'BadRequestException', 'NotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}
        deployments = _appconfig_optional(
            lambda: core._paginate(
                appconfig,
                'list_deployments',
                'Items',
                ApplicationId=application_id,
                EnvironmentId=environment_id,
            ),
            {'BadRequestException', 'NotFoundException'},
        )
        deployment_details = [
            deployment_detail(application_id, environment_id, deployment)
            for deployment in deployments
        ] if isinstance(deployments, list) else []

        return {
            'name': environment.get('Name') or described.get('Name') or environment_id,
            'id': environment_id,
            'application_id': application_id,
            'description': environment.get('Description') or described.get('Description'),
            'state': environment.get('State') or described.get('State'),
            'monitors': described.get('Monitors'),
            'deployment_count': len(deployment_details),
            'deployments': deployment_details,
            'details': details if isinstance(details, dict) and details.get('error') else None,
            'deployments_error': deployments if isinstance(deployments, dict) and deployments.get('error') else None,
        }

    def application_detail(application: dict[str, Any]) -> dict[str, Any]:
        application_id = application.get('Id')
        details = _appconfig_optional(
            lambda: appconfig.get_application(ApplicationId=application_id),
            {'BadRequestException', 'NotFoundException'},
        )
        environments = _appconfig_optional(
            lambda: core._paginate(appconfig, 'list_environments', 'Items', ApplicationId=application_id),
            {'BadRequestException', 'NotFoundException'},
        )
        profiles = _appconfig_optional(
            lambda: core._paginate(appconfig, 'list_configuration_profiles', 'Items', ApplicationId=application_id),
            {'BadRequestException', 'NotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}
        environment_details = [
            environment_detail(application_id, environment)
            for environment in environments
        ] if isinstance(environments, list) else []
        profile_details = [
            profile_detail(application_id, profile)
            for profile in profiles
        ] if isinstance(profiles, list) else []

        return {
            'name': application.get('Name') or described.get('Name') or application_id,
            'id': application_id,
            'description': application.get('Description') or described.get('Description'),
            'environment_count': len(environment_details),
            'configuration_profile_count': len(profile_details),
            'hosted_version_count': sum(profile.get('hosted_version_count') or 0 for profile in profile_details),
            'deployment_count': sum(environment.get('deployment_count') or 0 for environment in environment_details),
            'environments': environment_details,
            'configuration_profiles': profile_details,
            'details': details if isinstance(details, dict) and details.get('error') else None,
            'environments_error': environments if isinstance(environments, dict) and environments.get('error') else None,
            'configuration_profiles_error': profiles if isinstance(profiles, dict) and profiles.get('error') else None,
        }

    def deployment_strategy_detail(strategy: dict[str, Any]) -> dict[str, Any]:
        strategy_id = strategy.get('Id')
        details = _appconfig_optional(
            lambda: appconfig.get_deployment_strategy(DeploymentStrategyId=strategy_id),
            {'BadRequestException', 'NotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}

        return {
            'name': strategy.get('Name') or described.get('Name') or strategy_id,
            'id': strategy_id,
            'description': strategy.get('Description') or described.get('Description'),
            'deployment_duration_in_minutes': strategy.get('DeploymentDurationInMinutes') or described.get('DeploymentDurationInMinutes'),
            'growth_type': strategy.get('GrowthType') or described.get('GrowthType'),
            'growth_factor': strategy.get('GrowthFactor') or described.get('GrowthFactor'),
            'final_bake_time_in_minutes': strategy.get('FinalBakeTimeInMinutes') or described.get('FinalBakeTimeInMinutes'),
            'replicate_to': strategy.get('ReplicateTo') or described.get('ReplicateTo'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_applications = [application_detail(application) for application in applications]
    detailed_strategies = [deployment_strategy_detail(strategy) for strategy in deployment_strategies]
    environments = [
        environment
        for application in detailed_applications
        for environment in application.get('environments', [])
    ]
    profiles = [
        profile
        for application in detailed_applications
        for profile in application.get('configuration_profiles', [])
    ]
    hosted_versions = [
        version
        for profile in profiles
        for version in profile.get('hosted_versions', [])
    ]
    deployments = [
        deployment
        for environment in environments
        for deployment in environment.get('deployments', [])
    ]

    return {
        'summary': {
            'applications': len(detailed_applications),
            'environments': len(environments),
            'configuration_profiles': len(profiles),
            'hosted_versions': len(hosted_versions),
            'deployment_strategies': len(detailed_strategies),
            'deployments': len(deployments),
        },
        'applications': detailed_applications,
        'environments': environments,
        'configuration_profiles': profiles,
        'hosted_versions': hosted_versions,
        'deployment_strategies': detailed_strategies,
        'deployments': deployments,
        'management_supported': [
            'CreateApplication',
            'GetApplication',
            'ListApplications',
            'DeleteApplication',
            'CreateEnvironment',
            'GetEnvironment',
            'ListEnvironments',
            'CreateConfigurationProfile',
            'GetConfigurationProfile',
            'ListConfigurationProfiles',
            'CreateHostedConfigurationVersion',
            'GetHostedConfigurationVersion',
            'CreateDeploymentStrategy',
            'GetDeploymentStrategy',
            'StartDeployment',
            'GetDeployment',
        ],
        'data_supported': [
            'StartConfigurationSession',
            'GetLatestConfiguration',
        ],
        'notes': [
            'This page shows hosted configuration content as its size and a short UTF-8 preview.',
            'Deployments finish at once whatever the strategy\'s duration and bake time, and feature flag constraints are not validated when a version is created.',
        ],
    }


def list_appconfig_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    appconfig = factory.client('appconfig')
    resources: list[dict[str, Any]] = []
    applications = _safe_value(lambda: core._paginate(appconfig, 'list_applications', 'Items'), [])

    for application in applications:
        application_id = application.get('Id')
        resources.append({
            'type': 'application',
            'id': application_id,
            'name': application.get('Name'),
        })

        environments = _safe_value(
            lambda application_id=application_id: core._paginate(
                appconfig,
                'list_environments',
                'Items',
                ApplicationId=application_id,
            ),
            [],
        )
        resources.extend(
            {
                'type': 'environment',
                'application_id': application_id,
                'id': environment.get('Id'),
                'name': environment.get('Name'),
                'state': environment.get('State'),
            }
            for environment in environments
        )

        profiles = _safe_value(
            lambda application_id=application_id: core._paginate(
                appconfig,
                'list_configuration_profiles',
                'Items',
                ApplicationId=application_id,
            ),
            [],
        )
        resources.extend(
            {
                'type': 'configuration_profile',
                'application_id': application_id,
                'id': profile.get('Id'),
                'name': profile.get('Name'),
                'location_uri': profile.get('LocationUri'),
            }
            for profile in profiles
        )

        for profile in profiles:
            profile_id = profile.get('Id')
            resources.extend(
                {
                    'type': 'hosted_configuration_version',
                    'application_id': application_id,
                    'configuration_profile_id': profile_id,
                    'version_number': version.get('VersionNumber'),
                    'content_type': version.get('ContentType'),
                }
                for version in _safe_value(
                    lambda application_id=application_id, profile_id=profile_id: core._paginate(
                        appconfig,
                        'list_hosted_configuration_versions',
                        'Items',
                        ApplicationId=application_id,
                        ConfigurationProfileId=profile_id,
                    ),
                    [],
                )
            )

        for environment in environments:
            environment_id = environment.get('Id')
            resources.extend(
                {
                    'type': 'deployment',
                    'application_id': application_id,
                    'environment_id': environment_id,
                    'deployment_number': deployment.get('DeploymentNumber'),
                    'state': deployment.get('State'),
                }
                for deployment in _safe_value(
                    lambda application_id=application_id, environment_id=environment_id: core._paginate(
                        appconfig,
                        'list_deployments',
                        'Items',
                        ApplicationId=application_id,
                        EnvironmentId=environment_id,
                    ),
                    [],
                )
            )

    resources.extend(
        {
            'type': 'deployment_strategy',
            'id': strategy.get('Id'),
            'name': strategy.get('Name'),
        }
        for strategy in _safe_value(lambda: core._paginate(appconfig, 'list_deployment_strategies', 'Items'), [])
    )
    return resources
