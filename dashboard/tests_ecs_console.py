import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .ecs_api import register_task_definition, run_task, update_service, update_task_protection
from .services import get_service


class ECSPageTemplateTests(SimpleTestCase):

    def test_ecs_registry_marks_service_interactive(self):
        service = get_service('ecs')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'run_task' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_service' for action in service.actions))


class ECSActionsApiTests(SimpleTestCase):
    @patch('dashboard.ecs_views.create_cluster')
    def test_create_cluster_success(self, create_mock):
        create_mock.return_value = {'name': 'local', 'arn': 'arn:cluster/local'}

        response = self.client.post(
            reverse('dashboard:ecs-clusters'),
            data=json.dumps({'name': 'local', 'tags': [{'key': 'env', 'value': 'local'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'local')
        create_mock.assert_called_once_with(
            'local',
            tags=[{'key': 'env', 'value': 'local'}],
            capacity_providers=[],
        )

    def test_create_cluster_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:ecs-clusters'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'ecs')
        self.assertEqual(response.json()['operation'], 'create_cluster')

    @patch('dashboard.ecs_views.delete_cluster')
    def test_delete_cluster_success(self, delete_mock):
        delete_mock.return_value = {'name': 'local', 'status': 'INACTIVE'}

        response = self.client.post(
            reverse('dashboard:ecs-clusters-delete'),
            data=json.dumps({'cluster': 'local'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'INACTIVE')
        delete_mock.assert_called_once_with('local')

    @patch('dashboard.ecs_views.register_task_definition')
    def test_register_task_definition_success(self, register_mock):
        register_mock.return_value = {'family': 'web', 'revision': 1}
        containers = [{'name': 'app', 'image': 'nginx:latest', 'essential': True}]
        volumes = [{'name': 'app-data'}]

        response = self.client.post(
            reverse('dashboard:ecs-task-definitions'),
            data=json.dumps({
                'family': 'web',
                'container_definitions': containers,
                'volumes': volumes,
                'requires_compatibilities': ['FARGATE'],
                'network_mode': 'awsvpc',
                'cpu': '256',
                'memory': '512',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['family'], 'web')
        register_mock.assert_called_once_with(
            family='web',
            container_definitions=containers,
            requires_compatibilities=['FARGATE'],
            network_mode='awsvpc',
            cpu='256',
            memory='512',
            task_role_arn='',
            execution_role_arn='',
            volumes=volumes,
            tags=[],
            options={},
        )

    @patch('dashboard.ecs_views.deregister_task_definition')
    def test_deregister_task_definition_success(self, deregister_mock):
        deregister_mock.return_value = {'task_definition_arn': 'arn:task-definition/web:1', 'status': 'INACTIVE'}

        response = self.client.post(
            reverse('dashboard:ecs-task-definition-detail'),
            data=json.dumps({'task_definition': 'arn:task-definition/web:1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'INACTIVE')
        deregister_mock.assert_called_once_with('arn:task-definition/web:1')

    @patch('dashboard.ecs_views.delete_task_definitions')
    def test_delete_task_definitions_success(self, delete_mock):
        delete_mock.return_value = {'deleted': ['arn:task-definition/web:1'], 'failures': []}

        response = self.client.delete(
            reverse('dashboard:ecs-task-definition-detail'),
            data=json.dumps({'task_definitions': ['arn:task-definition/web:1']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['deleted'], ['arn:task-definition/web:1'])
        delete_mock.assert_called_once_with(['arn:task-definition/web:1'])

    @patch('dashboard.ecs_views.run_task')
    def test_run_task_success(self, run_mock):
        run_mock.return_value = {'task_arns': ['arn:task/1']}
        overrides = {
            'containerOverrides': [{
                'name': 'web',
                'command': ['echo', 'hello'],
                'environment': [{'name': 'MODE', 'value': 'local'}],
            }],
        }
        tags = [{'key': 'run', 'value': 'local'}]

        response = self.client.post(
            reverse('dashboard:ecs-tasks-run'),
            data=json.dumps({
                'cluster': 'local',
                'task_definition': 'web:1',
                'launch_type': 'FARGATE',
                'count': 1,
                'network_configuration': {'awsvpcConfiguration': {'subnets': ['subnet-1']}},
                'overrides': overrides,
                'tags': tags,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['task_arns'], ['arn:task/1'])
        run_mock.assert_called_once_with(
            cluster='local',
            task_definition='web:1',
            launch_type='FARGATE',
            count=1,
            network_configuration={'awsvpcConfiguration': {'subnets': ['subnet-1']}},
            overrides=overrides,
            started_by='',
            tags=tags,
        )

    @patch('dashboard.ecs_views.stop_task')
    def test_stop_task_success(self, stop_mock):
        stop_mock.return_value = {'task_arn': 'arn:task/1', 'last_status': 'STOPPED'}

        response = self.client.post(
            reverse('dashboard:ecs-tasks-stop'),
            data=json.dumps({'cluster': 'local', 'task': 'arn:task/1', 'reason': 'done'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['last_status'], 'STOPPED')
        stop_mock.assert_called_once_with('local', 'arn:task/1', reason='done')

    @patch('dashboard.ecs_views.update_task_protection')
    def test_update_task_protection_success(self, protection_mock):
        protection_mock.return_value = {'protected_tasks': [{'taskArn': 'arn:task/1', 'protectionEnabled': True}], 'failures': []}
        response = self.client.post(
            reverse('dashboard:ecs-tasks-protection'),
            data=json.dumps({'cluster': 'local', 'tasks': ['arn:task/1'], 'protection_enabled': True, 'expires_in_minutes': 60}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        protection_mock.assert_called_once_with('local', ['arn:task/1'], protection_enabled=True, expires_in_minutes=60)

    @patch('dashboard.ecs_views.update_container_instances_state')
    def test_update_container_instance_state_success(self, state_mock):
        state_mock.return_value = {'container_instances': [{'containerInstanceArn': 'arn:instance/1', 'status': 'DRAINING'}], 'failures': []}
        response = self.client.post(
            reverse('dashboard:ecs-container-instances-state'),
            data=json.dumps({'cluster': 'local', 'container_instances': ['arn:instance/1'], 'status': 'DRAINING'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        state_mock.assert_called_once_with('local', ['arn:instance/1'], 'DRAINING')

    @patch('dashboard.ecs_views.create_service')
    def test_create_service_success(self, create_mock):
        create_mock.return_value = {'service_name': 'web', 'desired_count': 1}
        tags = [{'key': 'service', 'value': 'web'}]

        response = self.client.post(
            reverse('dashboard:ecs-services'),
            data=json.dumps({
                'cluster': 'local',
                'service_name': 'web',
                'task_definition': 'web:1',
                'desired_count': 1,
                'tags': tags,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['service_name'], 'web')
        create_mock.assert_called_once_with(
            cluster='local',
            service_name='web',
            task_definition='web:1',
            desired_count=1,
            launch_type='FARGATE',
            network_configuration={},
            tags=tags,
        )

    @patch('dashboard.ecs_views.create_service')
    def test_create_service_preserves_zero_desired_count_and_network(self, create_mock):
        create_mock.return_value = {'service_name': 'web', 'desired_count': 0}
        network = {'awsvpcConfiguration': {'subnets': ['subnet-test']}}
        response = self.client.post(
            reverse('dashboard:ecs-services'),
            data=json.dumps({'cluster': 'local', 'service_name': 'web',
                             'task_definition': 'web:1', 'desired_count': 0,
                             'network_configuration': network}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(create_mock.call_args.kwargs['desired_count'], 0)
        self.assertEqual(create_mock.call_args.kwargs['network_configuration'], network)

    @patch('dashboard.ecs_views.update_service')
    def test_update_service_success(self, update_mock):
        update_mock.return_value = {'service_name': 'web', 'desired_count': 2}

        response = self.client.post(
            reverse('dashboard:ecs-services-update'),
            data=json.dumps({
                'cluster': 'local',
                'service': 'web',
                'desired_count': 2,
                'network_configuration': {'awsvpcConfiguration': {'subnets': ['subnet-1']}},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['desired_count'], 2)
        update_mock.assert_called_once_with(
            cluster='local',
            service='web',
            desired_count=2,
            task_definition='',
            network_configuration={'awsvpcConfiguration': {'subnets': ['subnet-1']}},
        )

    @patch('dashboard.ecs_views.delete_service')
    def test_delete_service_success(self, delete_mock):
        delete_mock.return_value = {'service_name': 'web', 'force': True}

        response = self.client.post(
            reverse('dashboard:ecs-services-delete'),
            data=json.dumps({'cluster': 'local', 'service': 'web', 'force': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['force'])
        delete_mock.assert_called_once_with('local', 'web', force=True)

    @patch('dashboard.ecs_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn:cluster/local'}

        response = self.client.post(
            reverse('dashboard:ecs-tags'),
            data=json.dumps({'resource_arn': 'arn:cluster/local', 'tags': [{'key': 'env', 'value': 'local'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_arn'], 'arn:cluster/local')
        tag_mock.assert_called_once_with('arn:cluster/local', [{'key': 'env', 'value': 'local'}])

    @patch('dashboard.ecs_views.untag_resource')
    def test_untag_resource_success(self, untag_mock):
        untag_mock.return_value = {'resource_arn': 'arn:cluster/local', 'tag_keys': ['env']}

        response = self.client.delete(
            reverse('dashboard:ecs-tags'),
            data=json.dumps({'resource_arn': 'arn:cluster/local', 'tag_keys': ['env']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tag_keys'], ['env'])
        untag_mock.assert_called_once_with('arn:cluster/local', ['env'])

    @patch('dashboard.ecs_views.put_account_setting')
    def test_put_account_setting_success(self, put_mock):
        put_mock.return_value = {'setting': {'name': 'containerInsights', 'value': 'enabled'}}

        response = self.client.post(
            reverse('dashboard:ecs-account-settings'),
            data=json.dumps({'name': 'containerInsights', 'value': 'enabled', 'principal_arn': ''}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['setting']['value'], 'enabled')
        put_mock.assert_called_once_with('containerInsights', 'enabled', principal_arn='')


class ECSApiHelperTests(SimpleTestCase):
    @patch('dashboard.ecs_api._client')
    def test_update_task_protection_passes_expiration(self, client_factory):
        client_factory.return_value.update_task_protection.return_value = {'protectedTasks': [], 'failures': []}
        update_task_protection('local', ['arn:task/1'], protection_enabled=True, expires_in_minutes=60)
        client_factory.return_value.update_task_protection.assert_called_once_with(
            cluster='local', tasks=['arn:task/1'], protectionEnabled=True, expiresInMinutes=60,
        )

    @patch('dashboard.ecs_api._client')
    def test_update_service_passes_supported_network_configuration(self, client_factory):
        client_factory.return_value.update_service.return_value = {
            'service': {'serviceName': 'web', 'desiredCount': 3},
        }

        result = update_service(
            cluster='local',
            service='web',
            desired_count=3,
            network_configuration={'awsvpcConfiguration': {'subnets': ['subnet-1']}},
        )

        client_factory.return_value.update_service.assert_called_once_with(
            cluster='local',
            service='web',
            desiredCount=3,
            networkConfiguration={'awsvpcConfiguration': {'subnets': ['subnet-1']}},
        )
        self.assertEqual(result['desired_count'], 3)

    @patch('dashboard.ecs_api._client')
    def test_register_task_definition_passes_volumes(self, client_factory):
        containers = [{
            'name': 'app',
            'image': 'nginx:latest',
            'mountPoints': [{'sourceVolume': 'app-data', 'containerPath': '/data'}],
        }]
        volumes = [{'name': 'app-data'}]
        client_factory.return_value.register_task_definition.return_value = {
            'taskDefinition': {
                'family': 'web',
                'revision': 1,
                'taskDefinitionArn': 'arn:task-definition/web:1',
                'status': 'ACTIVE',
            },
        }

        result = register_task_definition(
            family='web',
            container_definitions=containers,
            volumes=volumes,
        )

        client_factory.return_value.register_task_definition.assert_called_once_with(
            family='web',
            containerDefinitions=containers,
            networkMode='awsvpc',
            cpu='256',
            memory='512',
            volumes=volumes,
        )
        self.assertEqual(result['task_definition_arn'], 'arn:task-definition/web:1')

    @patch('dashboard.ecs_api._client')
    def test_run_task_passes_container_overrides(self, client_factory):
        overrides = {
            'containerOverrides': [{
                'name': 'web',
                'command': ['echo', 'hello'],
                'environment': [{'name': 'MODE', 'value': 'local'}],
            }],
        }
        client_factory.return_value.run_task.return_value = {
            'tasks': [{'taskArn': 'arn:task/1'}],
            'failures': [],
        }

        result = run_task(cluster='local', task_definition='web:1', overrides=overrides)

        client_factory.return_value.run_task.assert_called_once_with(
            cluster='local',
            taskDefinition='web:1',
            count=1,
            launchType='FARGATE',
            overrides=overrides,
        )
        self.assertEqual(result['task_arns'], ['arn:task/1'])

    @patch('dashboard.ecs_views.scale_service')
    def test_scale_service_endpoint(self, scale_mock):
        scale_mock.return_value = {'cluster': 'prod', 'service_name': 'web', 'desired_count': 5}

        response = self.client.post(
            reverse('dashboard:ecs-services-scale'),
            data=json.dumps({
                'cluster': 'prod',
                'service': 'web',
                'desired_count': 5,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['desired_count'], 5)
        scale_mock.assert_called_once_with('prod', 'web', 5)

    @patch('dashboard.ecs_views.validate_fargate_configuration')
    def test_fargate_validate_endpoint(self, val_mock):
        val_mock.return_value = {'cpu_units': 256, 'memory_mib': 512, 'valid': True}

        response = self.client.post(
            reverse('dashboard:ecs-fargate-validate'),
            data=json.dumps({'cpu': '256', 'memory': '512'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['valid'])
        val_mock.assert_called_once_with(cpu='256', memory='512')

    @patch('dashboard.ecs_views.diff_task_definitions')
    def test_diff_task_definitions_endpoint(self, diff_mock):
        diff_mock.return_value = {
            'task_definition_a': 'web:1',
            'task_definition_b': 'web:2',
            'cpu_changed': True,
        }

        response = self.client.post(
            reverse('dashboard:ecs-task-definitions-diff'),
            data=json.dumps({'task_definition_a': 'web:1', 'task_definition_b': 'web:2'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['cpu_changed'])
        diff_mock.assert_called_once_with('web:1', 'web:2')

    @patch('dashboard.ecs_api._client')
    def test_diff_task_definitions_api_implementation(self, client_mock):
        from .ecs_api import diff_task_definitions

        client = MagicMock()
        client.describe_task_definition.side_effect = [
            {'taskDefinition': {'family': 'web', 'revision': 1, 'cpu': '256', 'memory': '512', 'containerDefinitions': [{'name': 'app', 'image': 'nginx:1'}]}},
            {'taskDefinition': {'family': 'web', 'revision': 2, 'cpu': '512', 'memory': '1024', 'containerDefinitions': [{'name': 'app', 'image': 'nginx:2'}, {'name': 'sidecar', 'image': 'envoy'}]}},
        ]
        client_mock.return_value = client

        result = diff_task_definitions('web:1', 'web:2')
        self.assertTrue(result['cpu_changed'])
        self.assertTrue(result['memory_changed'])
        self.assertEqual(result['added_containers'], ['sidecar'])
        self.assertEqual(len(result['modified_containers']), 1)
        self.assertEqual(result['modified_containers'][0]['name'], 'app')
