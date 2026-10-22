"""Elastic Beanstalk inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def elasticbeanstalk_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    beanstalk = factory.client('elasticbeanstalk')
    operations = set(beanstalk.meta.service_model.operation_names)

    applications = _safe_value(lambda: core._operation_items(beanstalk, 'describe_applications', 'Applications'), []) if 'DescribeApplications' in operations else []
    environments = _safe_value(lambda: core._operation_items(beanstalk, 'describe_environments', 'Environments'), []) if 'DescribeEnvironments' in operations else []
    application_versions = _safe_value(lambda: core._operation_items(beanstalk, 'describe_application_versions', 'ApplicationVersions'), []) if 'DescribeApplicationVersions' in operations else []
    solution_stacks = _safe_value(lambda: beanstalk.list_available_solution_stacks().get('SolutionStacks', []), []) if 'ListAvailableSolutionStacks' in operations else []
    cleaned_environments = _clean_response(environments)
    for environment in cleaned_environments:
        if environment.get('Status') == 'Terminated':
            environment['OptionSettings'] = []
            continue
        settings = _safe_value(lambda env=environment: beanstalk.describe_configuration_settings(
            ApplicationName=env.get('ApplicationName'), EnvironmentName=env.get('EnvironmentName'),
        ).get('ConfigurationSettings', []), [])
        environment['OptionSettings'] = (settings[0].get('OptionSettings', []) if settings else [])

    return {
        'summary': {
            'applications': len(applications),
            'environments': len(environments),
            'application_versions': len(application_versions),
            'solution_stacks': len(solution_stacks),
        },
        'applications': _clean_response(applications),
        'environments': cleaned_environments,
        'application_versions': _clean_response(application_versions),
        'solution_stacks': [{'name': stack} for stack in solution_stacks],
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateApplication',
                'DescribeApplications',
                'UpdateApplication',
                'DeleteApplication',
                'CreateApplicationVersion',
                'DescribeApplicationVersions',
                'DeleteApplicationVersion',
                'CreateEnvironment',
                'DescribeEnvironments',
                'UpdateEnvironment',
                'TerminateEnvironment',
                'DescribeConfigurationSettings',
                'CheckDNSAvailability',
                'ListAvailableSolutionStacks',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.28 adds initial Elastic Beanstalk Query API support.',
            'Floci 1.5.31 persists Elastic Beanstalk applications, versions, and environments across restart.',
            'Environments become Ready and Green immediately; Floci stores management-plane state but does not provision application infrastructure.',
            'Tags supplied during creation are stored internally but cannot be retrieved or updated through Floci’s supported Elastic Beanstalk operations.',
        ],
    }


def list_elasticbeanstalk_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    beanstalk = factory.client('elasticbeanstalk')
    operations = set(beanstalk.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'DescribeApplications' in operations:
        resources.extend(
            {
                'type': 'application',
                'name': application.get('ApplicationName'),
                'arn': application.get('ApplicationArn'),
            }
            for application in _safe_value(lambda: core._operation_items(beanstalk, 'describe_applications', 'Applications'), [])
        )
    if 'DescribeEnvironments' in operations:
        resources.extend(
            {
                'type': 'environment',
                'name': environment.get('EnvironmentName'),
                'id': environment.get('EnvironmentId'),
                'arn': environment.get('EnvironmentArn'),
                'status': environment.get('Status'),
            }
            for environment in _safe_value(lambda: core._operation_items(beanstalk, 'describe_environments', 'Environments'), [])
        )

    return resources
