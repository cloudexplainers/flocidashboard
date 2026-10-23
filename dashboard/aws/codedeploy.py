"""CodeDeploy inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _codedeploy_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def codedeploy_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    codedeploy = factory.client('codedeploy')
    application_names = _safe_value(lambda: core._paginate(codedeploy, 'list_applications', 'applications'), [])
    deployment_config_names = _safe_value(lambda: core._paginate(codedeploy, 'list_deployment_configs', 'deploymentConfigsList'), [])
    on_prem_instance_names = _safe_value(lambda: core._paginate(codedeploy, 'list_on_premises_instances', 'instanceNames'), [])

    def application_detail(name: str) -> dict[str, Any]:
        response = _codedeploy_optional(
            lambda: codedeploy.get_application(applicationName=name),
            {'ApplicationDoesNotExistException', 'InvalidApplicationNameException'},
        )
        application = response.get('application', {}) if isinstance(response, dict) and not response.get('error') else {}
        group_names = _codedeploy_optional(
            lambda: core._paginate(codedeploy, 'list_deployment_groups', 'deploymentGroups', applicationName=name),
            {'ApplicationDoesNotExistException', 'InvalidApplicationNameException'},
        )
        deployment_ids = _codedeploy_optional(
            lambda: core._paginate(codedeploy, 'list_deployments', 'deployments', applicationName=name),
            {'ApplicationDoesNotExistException', 'InvalidApplicationNameException'},
        )

        return {
            'name': name,
            'application_id': application.get('applicationId'),
            'compute_platform': application.get('computePlatform'),
            'linked_to_github': application.get('linkedToGitHub'),
            'git_hub_account_name': application.get('gitHubAccountName'),
            'deployment_group_count': len(group_names) if isinstance(group_names, list) else 0,
            'deployment_count': len(deployment_ids) if isinstance(deployment_ids, list) else 0,
            'deployment_groups': group_names if isinstance(group_names, list) else [],
            'deployments': deployment_ids if isinstance(deployment_ids, list) else [],
            'details': response if isinstance(response, dict) and response.get('error') else None,
            'deployment_groups_error': group_names if isinstance(group_names, dict) and group_names.get('error') else None,
            'deployments_error': deployment_ids if isinstance(deployment_ids, dict) and deployment_ids.get('error') else None,
        }

    def deployment_group_detail(application_name: str, group_name: str) -> dict[str, Any]:
        response = _codedeploy_optional(
            lambda: codedeploy.get_deployment_group(
                applicationName=application_name,
                deploymentGroupName=group_name,
            ),
            {'ApplicationDoesNotExistException', 'DeploymentGroupDoesNotExistException', 'InvalidApplicationNameException'},
        )
        group = response.get('deploymentGroupInfo', {}) if isinstance(response, dict) and not response.get('error') else {}
        deployment_ids = _codedeploy_optional(
            lambda: core._paginate(
                codedeploy,
                'list_deployments',
                'deployments',
                applicationName=application_name,
                deploymentGroupName=group_name,
            ),
            {'ApplicationDoesNotExistException', 'DeploymentGroupDoesNotExistException', 'InvalidApplicationNameException'},
        )

        return {
            'name': group_name,
            'application_name': application_name,
            'deployment_group_id': group.get('deploymentGroupId'),
            'service_role_arn': group.get('serviceRoleArn'),
            'deployment_config_name': group.get('deploymentConfigName'),
            'ec2_tag_filters': group.get('ec2TagFilters'),
            'on_premises_instance_tag_filters': group.get('onPremisesInstanceTagFilters'),
            'auto_scaling_groups': group.get('autoScalingGroups'),
            'trigger_configurations': group.get('triggerConfigurations'),
            'alarm_configuration': group.get('alarmConfiguration'),
            'auto_rollback_configuration': group.get('autoRollbackConfiguration'),
            'deployment_style': group.get('deploymentStyle'),
            'blue_green_deployment_configuration': group.get('blueGreenDeploymentConfiguration'),
            'load_balancer_info': group.get('loadBalancerInfo'),
            'last_successful_deployment': group.get('lastSuccessfulDeployment'),
            'last_attempted_deployment': group.get('lastAttemptedDeployment'),
            'outdated_instances_strategy': group.get('outdatedInstancesStrategy'),
            'compute_platform': group.get('computePlatform'),
            'ecs_services': group.get('ecsServices'),
            'termination_hook_enabled': group.get('terminationHookEnabled'),
            'deployment_count': len(deployment_ids) if isinstance(deployment_ids, list) else 0,
            'deployments': deployment_ids if isinstance(deployment_ids, list) else [],
            'details': response if isinstance(response, dict) and response.get('error') else None,
            'deployments_error': deployment_ids if isinstance(deployment_ids, dict) and deployment_ids.get('error') else None,
        }

    def deployment_detail(deployment_id: str) -> dict[str, Any]:
        response = _codedeploy_optional(
            lambda: codedeploy.get_deployment(deploymentId=deployment_id),
            {'DeploymentDoesNotExistException', 'InvalidDeploymentIdException'},
        )
        deployment = response.get('deploymentInfo', {}) if isinstance(response, dict) and not response.get('error') else {}

        return {
            'name': deployment_id,
            'deployment_id': deployment_id,
            'application_name': deployment.get('applicationName'),
            'deployment_group_name': deployment.get('deploymentGroupName'),
            'deployment_config_name': deployment.get('deploymentConfigName'),
            'status': deployment.get('status'),
            'error_information': deployment.get('errorInformation'),
            'create_time': deployment.get('createTime'),
            'start_time': deployment.get('startTime'),
            'complete_time': deployment.get('completeTime'),
            'deployment_overview': deployment.get('deploymentOverview'),
            'description': deployment.get('description'),
            'creator': deployment.get('creator'),
            'ignore_application_stop_failures': deployment.get('ignoreApplicationStopFailures'),
            'auto_rollback_configuration': deployment.get('autoRollbackConfiguration'),
            'update_outdated_instances_only': deployment.get('updateOutdatedInstancesOnly'),
            'rollback_info': deployment.get('rollbackInfo'),
            'deployment_style': deployment.get('deploymentStyle'),
            'target_instances': deployment.get('targetInstances'),
            'instance_termination_wait_time_started': deployment.get('instanceTerminationWaitTimeStarted'),
            'blue_green_deployment_configuration': deployment.get('blueGreenDeploymentConfiguration'),
            'load_balancer_info': deployment.get('loadBalancerInfo'),
            'additional_deployment_status_info': deployment.get('additionalDeploymentStatusInfo'),
            'file_exists_behavior': deployment.get('fileExistsBehavior'),
            'deployment_status_messages': deployment.get('deploymentStatusMessages'),
            'compute_platform': deployment.get('computePlatform'),
            'external_id': deployment.get('externalId'),
            'related_deployments': deployment.get('relatedDeployments'),
            'override_alarm_configuration': deployment.get('overrideAlarmConfiguration'),
            'revision': deployment.get('revision'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def deployment_config_detail(name: str) -> dict[str, Any]:
        response = _codedeploy_optional(
            lambda: codedeploy.get_deployment_config(deploymentConfigName=name),
            {'DeploymentConfigDoesNotExistException', 'InvalidDeploymentConfigNameException'},
        )
        config = response.get('deploymentConfigInfo', {}) if isinstance(response, dict) and not response.get('error') else {}

        return {
            'name': name,
            'deployment_config_id': config.get('deploymentConfigId'),
            'minimum_healthy_hosts': config.get('minimumHealthyHosts'),
            'traffic_routing_config': config.get('trafficRoutingConfig'),
            'compute_platform': config.get('computePlatform'),
            'zonal_config': config.get('zonalConfig'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    applications = [application_detail(name) for name in application_names]
    deployment_groups = [
        deployment_group_detail(application.get('name'), group_name)
        for application in applications
        for group_name in application.get('deployment_groups', [])
    ]
    deployment_ids = sorted({
        deployment_id
        for application in applications
        for deployment_id in application.get('deployments', [])
    } | {
        deployment_id
        for group in deployment_groups
        for deployment_id in group.get('deployments', [])
    })
    deployments = [deployment_detail(deployment_id) for deployment_id in deployment_ids]
    deployment_configs = [deployment_config_detail(name) for name in deployment_config_names]
    on_prem_instances_response = _codedeploy_optional(
        lambda: codedeploy.batch_get_on_premises_instances(instanceNames=on_prem_instance_names),
        {'InvalidInstanceNameException'},
    ) if on_prem_instance_names else {'instanceInfos': []}
    on_prem_instances = (
        on_prem_instances_response.get('instanceInfos', [])
        if isinstance(on_prem_instances_response, dict) and not on_prem_instances_response.get('error')
        else []
    )

    return {
        'summary': {
            'applications': len(applications),
            'deployment_groups': len(deployment_groups),
            'deployments': len(deployments),
            'deployment_configs': len(deployment_configs),
            'on_prem_instances': len(on_prem_instance_names),
        },
        'applications': applications,
        'deployment_groups': deployment_groups,
        'deployments': deployments or [{'name': deployment_id, 'deployment_id': deployment_id} for deployment_id in deployment_ids],
        'deployment_configs': deployment_configs,
        'on_prem_instances': on_prem_instances or [{'name': name} for name in on_prem_instance_names],
        'supported_from_sdk': [
            'CreateApplication',
            'GetApplication',
            'ListApplications',
            'DeleteApplication',
            'CreateDeploymentGroup',
            'GetDeploymentGroup',
            'ListDeploymentGroups',
            'UpdateDeploymentGroup',
            'DeleteDeploymentGroup',
            'CreateDeployment',
            'GetDeployment',
            'ListDeployments',
            'StopDeployment',
            'CreateDeploymentConfig',
            'GetDeploymentConfig',
            'ListDeploymentConfigs',
            'DeleteDeploymentConfig',
            'RegisterApplicationRevision',
            'GetApplicationRevision',
            'ListApplicationRevisions',
            'RegisterOnPremisesInstance',
            'BatchGetOnPremisesInstances',
            'ListOnPremisesInstances',
            'DeregisterOnPremisesInstance',
        ],
        'notes': [
            'Deployment lists are collected from applications and deployment groups, then de-duplicated before detail lookup.',
            'ContinueDeployment is accepted but does nothing. PutLifecycleEventHookExecutionStatus does not check that the deployment exists.',
        ],
    }


def list_codedeploy_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    codedeploy = factory.client('codedeploy')
    resources: list[dict[str, Any]] = []
    application_names = _safe_value(lambda: core._paginate(codedeploy, 'list_applications', 'applications'), [])
    deployment_config_names = _safe_value(lambda: core._paginate(codedeploy, 'list_deployment_configs', 'deploymentConfigsList'), [])
    on_prem_instance_names = _safe_value(lambda: core._paginate(codedeploy, 'list_on_premises_instances', 'instanceNames'), [])

    for application_name in application_names:
        resources.append({
            'type': 'application',
            'name': application_name,
        })
        group_names = _safe_value(
            lambda application_name=application_name: core._paginate(
                codedeploy,
                'list_deployment_groups',
                'deploymentGroups',
                applicationName=application_name,
            ),
            [],
        )
        resources.extend(
            {
                'type': 'deployment_group',
                'application_name': application_name,
                'name': group_name,
            }
            for group_name in group_names
        )
        deployment_ids = _safe_value(
            lambda application_name=application_name: core._paginate(
                codedeploy,
                'list_deployments',
                'deployments',
                applicationName=application_name,
            ),
            [],
        )
        resources.extend(
            {
                'type': 'deployment',
                'application_name': application_name,
                'id': deployment_id,
            }
            for deployment_id in deployment_ids
        )

    resources.extend(
        {
            'type': 'deployment_config',
            'name': name,
        }
        for name in deployment_config_names
    )
    resources.extend(
        {
            'type': 'on_prem_instance',
            'name': name,
        }
        for name in on_prem_instance_names
    )
    return resources
