"""ECS inventory and resource-list loaders."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _chunks, _clean_response, _error_code, _safe_value


def _ecs_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def _ecs_operation_supported(client, operation_name: str) -> bool:
    return operation_name in client.meta.service_model.operation_names


def ecs_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    ecs = factory.client('ecs')
    cluster_arns = _safe_value(lambda: core._paginate(ecs, 'list_clusters', 'clusterArns'), [])
    active_task_definition_arns = _safe_value(
        lambda: core._paginate(ecs, 'list_task_definitions', 'taskDefinitionArns', status='ACTIVE'),
        [],
    )
    inactive_task_definition_arns = _safe_value(
        lambda: core._paginate(ecs, 'list_task_definitions', 'taskDefinitionArns', status='INACTIVE'),
        [],
    )
    task_definition_arns = list(dict.fromkeys(active_task_definition_arns + inactive_task_definition_arns))
    task_definition_families = _safe_value(lambda: core._paginate(ecs, 'list_task_definition_families', 'families'), [])

    def list_tags(arn: Optional[str]) -> Any:
        if not arn:
            return None

        return _ecs_optional(
            lambda: ecs.list_tags_for_resource(resourceArn=arn).get('tags', []),
            {'ClusterNotFoundException', 'ClientException', 'InvalidParameterException'},
        )

    def describe_task_definition(arn: str) -> dict[str, Any]:
        response = _ecs_optional(
            lambda: ecs.describe_task_definition(taskDefinition=arn, include=['TAGS']),
            {'ClientException', 'InvalidParameterException'},
        )

        if not isinstance(response, dict) or response.get('error'):
            return {'name': arn, 'arn': arn, 'details': response}

        task_definition = response.get('taskDefinition', {})
        return {
            'name': f"{task_definition.get('family')}:{task_definition.get('revision')}" if task_definition.get('family') else arn,
            'arn': task_definition.get('taskDefinitionArn') or arn,
            'family': task_definition.get('family'),
            'revision': task_definition.get('revision'),
            'status': task_definition.get('status'),
            'network_mode': task_definition.get('networkMode'),
            'requires_compatibilities': task_definition.get('requiresCompatibilities'),
            'cpu': task_definition.get('cpu'),
            'memory': task_definition.get('memory'),
            'task_role_arn': task_definition.get('taskRoleArn'),
            'execution_role_arn': task_definition.get('executionRoleArn'),
            'registered_at': task_definition.get('registeredAt'),
            'deregistered_at': task_definition.get('deregisteredAt'),
            'containers': [
                {
                    'name': container.get('name'),
                    'image': container.get('image'),
                    'cpu': container.get('cpu'),
                    'memory': container.get('memory'),
                    'essential': container.get('essential'),
                    'port_mappings': container.get('portMappings'),
                    'command': container.get('command'),
                    'entry_point': container.get('entryPoint'),
                    'environment': container.get('environment'),
                    'secrets': container.get('secrets'),
                    'mount_points': container.get('mountPoints'),
                    'health_check': container.get('healthCheck'),
                    'log_configuration': container.get('logConfiguration'),
                    'depends_on': container.get('dependsOn'),
                }
                for container in task_definition.get('containerDefinitions', [])
            ],
            'container_definitions': task_definition.get('containerDefinitions', []),
            'volumes': task_definition.get('volumes'),
            'placement_constraints': task_definition.get('placementConstraints'),
            'runtime_platform': task_definition.get('runtimePlatform'),
            'ephemeral_storage': task_definition.get('ephemeralStorage'),
            'proxy_configuration': task_definition.get('proxyConfiguration'),
            'ipc_mode': task_definition.get('ipcMode'),
            'pid_mode': task_definition.get('pidMode'),
            'inference_accelerators': task_definition.get('inferenceAccelerators'),
            'tags': response.get('tags') or list_tags(task_definition.get('taskDefinitionArn') or arn),
        }

    def cluster_detail(cluster_arn: str) -> dict[str, Any]:
        response = _ecs_optional(
            lambda: ecs.describe_clusters(
                clusters=[cluster_arn],
                include=['ATTACHMENTS', 'CONFIGURATIONS', 'SETTINGS', 'STATISTICS', 'TAGS'],
            ),
            {'ClusterNotFoundException'},
        )

        if not isinstance(response, dict) or response.get('error'):
            return {'name': cluster_arn, 'arn': cluster_arn, 'details': response}

        cluster = (response.get('clusters') or [{}])[0]
        name = cluster.get('clusterName') or cluster_arn
        task_arns = _ecs_optional(
            lambda: core._paginate(ecs, 'list_tasks', 'taskArns', cluster=cluster_arn),
            {'ClusterNotFoundException'},
        )
        stopped_task_arns = _ecs_optional(
            lambda: core._paginate(ecs, 'list_tasks', 'taskArns', cluster=cluster_arn, desiredStatus='STOPPED'),
            {'ClusterNotFoundException'},
        )
        if isinstance(task_arns, list) and isinstance(stopped_task_arns, list):
            task_arns = list(dict.fromkeys(task_arns + stopped_task_arns))
        service_arns = _ecs_optional(
            lambda: core._paginate(ecs, 'list_services', 'serviceArns', cluster=cluster_arn),
            {'ClusterNotFoundException'},
        )
        container_instance_arns = _ecs_optional(
            lambda: core._paginate(ecs, 'list_container_instances', 'containerInstanceArns', cluster=cluster_arn),
            {'ClusterNotFoundException'},
        )

        tasks: list[dict[str, Any]] = []
        if isinstance(task_arns, list):
            for batch in _chunks(task_arns, 100):
                task_response = _ecs_optional(
                    lambda batch=batch: ecs.describe_tasks(cluster=cluster_arn, tasks=batch, include=['TAGS']).get('tasks', []),
                    {'ClusterNotFoundException', 'InvalidParameterException'},
                )
                if isinstance(task_response, list):
                    tasks.extend(_clean_response(task_response))
            for batch in _chunks(task_arns, 10):
                protection_response = _ecs_optional(
                    lambda batch=batch: ecs.get_task_protection(cluster=cluster_arn, tasks=batch),
                    {'ClusterNotFoundException', 'InvalidParameterException'},
                )
                protected = protection_response.get('protectedTasks', []) if isinstance(protection_response, dict) else []
                protection_by_task = {item.get('taskArn'): item for item in protected if item.get('taskArn')}
                for task in tasks:
                    if task.get('taskArn') in protection_by_task:
                        task['protection'] = protection_by_task[task['taskArn']]

        services: list[dict[str, Any]] = []
        task_sets: list[dict[str, Any]] = []
        service_deployments: list[Any] = []
        service_revisions: list[Any] = []
        if isinstance(service_arns, list):
            for batch in _chunks(service_arns, 10):
                service_response = _ecs_optional(
                    lambda batch=batch: ecs.describe_services(cluster=cluster_arn, services=batch, include=['TAGS']).get('services', []),
                    {'ClusterNotFoundException', 'ServiceNotFoundException', 'InvalidParameterException'},
                )
                if isinstance(service_response, list):
                    services.extend(_clean_response(service_response))

            for service_arn in service_arns:
                sets = _ecs_optional(
                    lambda service_arn=service_arn: ecs.describe_task_sets(cluster=cluster_arn, service=service_arn).get('taskSets', []),
                    {'ClusterNotFoundException', 'ServiceNotFoundException'},
                )
                if isinstance(sets, list):
                    task_sets.extend(_clean_response(sets))

                if _ecs_operation_supported(ecs, 'ListServiceDeployments'):
                    deployments = _ecs_optional(
                        lambda service_arn=service_arn: core._paginate(
                            ecs,
                            'list_service_deployments',
                            'serviceDeployments',
                            cluster=cluster_arn,
                            service=service_arn,
                        ),
                        {'ClusterNotFoundException', 'ServiceNotFoundException'},
                    )
                    if isinstance(deployments, list):
                        service_deployments.extend(deployments)

                if _ecs_operation_supported(ecs, 'DescribeServiceRevisions') and service_deployments:
                    revision_arns = [
                        item.get('serviceRevisionArn')
                        for item in service_deployments
                        if isinstance(item, dict) and item.get('serviceRevisionArn')
                    ]
                    if revision_arns:
                        revisions = _ecs_optional(
                            lambda revision_arns=revision_arns[:10]: ecs.describe_service_revisions(
                                serviceRevisionArns=revision_arns[:10],
                            ).get('serviceRevisions', []),
                            {'InvalidParameterException'},
                        )
                        if isinstance(revisions, list):
                            service_revisions.extend(_clean_response(revisions))

        container_instances: list[dict[str, Any]] = []
        if isinstance(container_instance_arns, list):
            for batch in _chunks(container_instance_arns, 100):
                container_response = _ecs_optional(
                    lambda batch=batch: ecs.describe_container_instances(
                        cluster=cluster_arn,
                        containerInstances=batch,
                    ).get('containerInstances', []),
                    {'ClusterNotFoundException', 'InvalidParameterException'},
                )
                if isinstance(container_response, list):
                    container_instances.extend(_clean_response(container_response))

        return {
            'name': name,
            'arn': cluster.get('clusterArn') or cluster_arn,
            'status': cluster.get('status'),
            'running_tasks': cluster.get('runningTasksCount'),
            'pending_tasks': cluster.get('pendingTasksCount'),
            'active_services': cluster.get('activeServicesCount'),
            'registered_container_instances': cluster.get('registeredContainerInstancesCount'),
            'capacity_providers': cluster.get('capacityProviders'),
            'default_capacity_provider_strategy': cluster.get('defaultCapacityProviderStrategy'),
            'settings': cluster.get('settings'),
            'configuration': cluster.get('configuration'),
            'statistics': cluster.get('statistics'),
            'attachments': cluster.get('attachments'),
            'tags': cluster.get('tags') or list_tags(cluster.get('clusterArn') or cluster_arn),
            'task_count': len(tasks),
            'service_count': len(services),
            'task_set_count': len(task_sets),
            'container_instance_count': len(container_instances),
            'service_deployment_count': len(service_deployments),
            'tasks': tasks,
            'services': services,
            'task_sets': task_sets,
            'container_instances': container_instances,
            'service_deployments': service_deployments,
            'service_revisions': service_revisions,
        }

    def parallel_map(values: list[str], loader: Callable[[str], dict[str, Any]]) -> list[dict[str, Any]]:
        if len(values) < 2:
            return [loader(value) for value in values]
        with ThreadPoolExecutor(max_workers=min(8, len(values))) as executor:
            return core.map_in_context(executor, loader, values)

    detailed_clusters = parallel_map(cluster_arns, cluster_detail)
    task_definitions = parallel_map(task_definition_arns, describe_task_definition)
    task_definition_groups: dict[str, list[dict[str, Any]]] = {}
    for definition in task_definitions:
        task_definition_groups.setdefault(definition.get('family') or 'Unknown', []).append(definition)
    for revisions in task_definition_groups.values():
        revisions.sort(key=lambda item: item.get('revision') or 0, reverse=True)
    capacity_providers = _ecs_optional(
        lambda: ecs.describe_capacity_providers().get('capacityProviders', []),
        {'ClientException'},
    )
    account_settings = _ecs_optional(
        lambda: core._paginate(ecs, 'list_account_settings', 'settings'),
        {'ClientException'},
    )
    attributes = _ecs_optional(
        lambda: ecs.list_attributes(targetType='container-instance').get('attributes', []),
        {'ClientException'},
    )
    agent_poll_endpoint = _ecs_optional(
        lambda: ecs.discover_poll_endpoint(),
        {'ClientException'},
    )

    return {
        'summary': {
            'clusters': len(detailed_clusters),
            'task_definitions': len(task_definitions),
            'task_definition_families': len(task_definition_families),
            'tasks': sum(cluster.get('task_count') or 0 for cluster in detailed_clusters),
            'services': sum(cluster.get('service_count') or 0 for cluster in detailed_clusters),
            'task_sets': sum(cluster.get('task_set_count') or 0 for cluster in detailed_clusters),
            'container_instances': sum(cluster.get('container_instance_count') or 0 for cluster in detailed_clusters),
            'capacity_providers': len(capacity_providers) if isinstance(capacity_providers, list) else 0,
        },
        'clusters': detailed_clusters,
        'task_definitions': task_definitions,
        'task_definition_groups': [
            {'family': family, 'revisions': revisions}
            for family, revisions in sorted(task_definition_groups.items())
        ],
        'task_definition_families': task_definition_families,
        'capacity_providers': capacity_providers,
        'account_settings': account_settings,
        'attributes': attributes,
        'agent_poll_endpoint': agent_poll_endpoint,
        'supported': {
            'Clusters': ['CreateCluster', 'DescribeClusters', 'ListClusters', 'UpdateCluster', 'UpdateClusterSettings', 'PutClusterCapacityProviders', 'DeleteCluster'],
            'Task definitions': ['RegisterTaskDefinition', 'DescribeTaskDefinition', 'ListTaskDefinitions', 'ListTaskDefinitionFamilies', 'DeregisterTaskDefinition', 'DeleteTaskDefinitions'],
            'Tasks': ['RunTask', 'StartTask', 'StopTask', 'DescribeTasks', 'ListTasks', 'UpdateTaskProtection', 'GetTaskProtection'],
            'Services': ['CreateService', 'UpdateService', 'DeleteService', 'DescribeServices', 'ListServices', 'ListServicesByNamespace'],
            'Task sets': ['CreateTaskSet', 'UpdateTaskSet', 'DeleteTaskSet', 'DescribeTaskSets', 'UpdateServicePrimaryTaskSet'],
            'Container instances': ['RegisterContainerInstance', 'DeregisterContainerInstance', 'DescribeContainerInstances', 'ListContainerInstances', 'UpdateContainerAgent', 'UpdateContainerInstancesState'],
            'Capacity providers': ['CreateCapacityProvider', 'UpdateCapacityProvider', 'DeleteCapacityProvider', 'DescribeCapacityProviders'],
            'Service deployments and revisions': ['DescribeServiceDeployments', 'ListServiceDeployments', 'DescribeServiceRevisions'],
            'Tags': ['TagResource', 'UntagResource', 'ListTagsForResource'],
            'Account settings and attributes': ['PutAccountSetting', 'PutAccountSettingDefault', 'DeleteAccountSetting', 'ListAccountSettings', 'PutAttributes', 'DeleteAttributes', 'ListAttributes'],
            'Agent stubs': ['SubmitTaskStateChange', 'SubmitContainerStateChange', 'SubmitAttachmentStateChanges', 'DiscoverPollEndpoint'],
        },
        'configuration': {
            'mock': False,
            'docker_network': '',
            'default_memory_mb': 512,
            'default_cpu_units': 256,
        },
        'environment_variables': [
            'FLOCI_SERVICES_ECS_ENABLED',
            'FLOCI_SERVICES_ECS_MOCK',
            'FLOCI_SERVICES_ECS_DOCKER_NETWORK',
            'FLOCI_SERVICES_ECS_DEFAULT_MEMORY_MB',
            'FLOCI_SERVICES_ECS_DEFAULT_CPU_UNITS',
        ],
        'notes': [
            'By default Floci ECS launches real Docker containers for tasks.',
            'Set FLOCI_SERVICES_ECS_MOCK=true to run tasks as in-process stubs for CI or tests.',
            'When mock mode is false, Floci needs access to the Docker socket.',
            'Placement strategies and placement constraints are stored and returned, but Floci doesn\'t use them to place tasks: every task runs as a container on the Floci host. Capacity provider strategies (base and weight) are applied.',
        ],
    }


def list_ecs_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    ecs = factory.client('ecs')
    resources: list[dict[str, Any]] = []
    clusters = _safe_value(lambda: core._paginate(ecs, 'list_clusters', 'clusterArns'), [])
    task_definitions = _safe_value(lambda: core._paginate(ecs, 'list_task_definitions', 'taskDefinitionArns'), [])
    resources.extend({'type': 'cluster', 'arn': arn} for arn in clusters)
    resources.extend({'type': 'task_definition', 'arn': arn} for arn in task_definitions)

    for cluster in clusters:
        tasks = _safe_value(lambda cluster=cluster: core._paginate(ecs, 'list_tasks', 'taskArns', cluster=cluster), [])
        services = _safe_value(lambda cluster=cluster: core._paginate(ecs, 'list_services', 'serviceArns', cluster=cluster), [])
        container_instances = _safe_value(lambda cluster=cluster: core._paginate(ecs, 'list_container_instances', 'containerInstanceArns', cluster=cluster), [])
        resources.extend({'type': 'task', 'cluster': cluster, 'arn': arn} for arn in tasks)
        resources.extend({'type': 'service', 'cluster': cluster, 'arn': arn} for arn in services)
        resources.extend({'type': 'container_instance', 'cluster': cluster, 'arn': arn} for arn in container_instances)

    return resources
