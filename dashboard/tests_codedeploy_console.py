import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class CodeDeployPageTemplateTests(SimpleTestCase):

    def test_codedeploy_registry_marks_service_interactive(self):
        service = get_service('codedeploy')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_application' for action in service.actions))
        self.assertTrue(any(action.name == 'create_deployment_group' for action in service.actions))
        self.assertTrue(any(action.name == 'create_deployment' for action in service.actions))
        self.assertTrue(any(action.name == 'put_lifecycle_event_hook_execution_status' for action in service.actions))


class CodeDeployActionsApiTests(SimpleTestCase):
    @patch('dashboard.codedeploy_views.create_application')
    def test_create_application_success(self, create_mock):
        create_mock.return_value = {'application_name': 'my-app'}

        response = self.client.post(
            reverse('dashboard:codedeploy-applications'),
            data=json.dumps({'name': 'my-app', 'compute_platform': 'Lambda'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('my-app', 'Lambda')

    def test_create_application_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:codedeploy-applications'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'codedeploy')
        self.assertEqual(response.json()['operation'], 'create_application')

    @patch('dashboard.codedeploy_views.delete_application')
    def test_delete_application_success(self, delete_mock):
        delete_mock.return_value = {'application_name': 'my-app'}

        response = self.client.delete(reverse('dashboard:codedeploy-application-detail', kwargs={'application_name': 'my-app'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('my-app')

    @patch('dashboard.codedeploy_views.create_deployment_group')
    def test_create_deployment_group_success(self, create_mock):
        options = {'serviceRoleArn': 'arn:aws:iam::000000000000:role/codedeploy-role'}
        create_mock.return_value = {'deployment_group_name': 'my-group'}

        response = self.client.post(
            reverse('dashboard:codedeploy-deployment-groups', kwargs={'application_name': 'my-app'}),
            data=json.dumps({'deployment_group_name': 'my-group', 'options': options}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('my-app', 'my-group', options)

    @patch('dashboard.codedeploy_views.delete_deployment_group')
    def test_delete_deployment_group_success(self, delete_mock):
        delete_mock.return_value = {'deployment_group_name': 'my-group'}

        response = self.client.delete(reverse(
            'dashboard:codedeploy-deployment-group-detail',
            kwargs={'application_name': 'my-app', 'group_name': 'my-group'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('my-app', 'my-group')

    @patch('dashboard.codedeploy_views.create_deployment_config')
    def test_create_deployment_config_success(self, create_mock):
        options = {'minimumHealthyHosts': {'type': 'HOST_COUNT', 'value': 0}}
        create_mock.return_value = {'deployment_config_name': 'LocalAllAtOnce'}

        response = self.client.post(
            reverse('dashboard:codedeploy-deployment-configs'),
            data=json.dumps({'name': 'LocalAllAtOnce', 'options': options}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('LocalAllAtOnce', options)

    @patch('dashboard.codedeploy_views.delete_deployment_config')
    def test_delete_deployment_config_success(self, delete_mock):
        delete_mock.return_value = {'deployment_config_name': 'LocalAllAtOnce'}

        response = self.client.delete(reverse('dashboard:codedeploy-deployment-config-detail', kwargs={'config_name': 'LocalAllAtOnce'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('LocalAllAtOnce')

    @patch('dashboard.codedeploy_views.create_deployment')
    def test_create_deployment_success(self, create_mock):
        revision = {'revisionType': 'AppSpecContent'}
        options = {'description': 'local'}
        create_mock.return_value = {'deployment_id': 'd-1'}

        response = self.client.post(
            reverse('dashboard:codedeploy-deployments'),
            data=json.dumps({'application_name': 'my-app', 'deployment_group_name': 'my-group', 'revision': revision, 'options': options}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('my-app', 'my-group', revision, options)

    @patch('dashboard.codedeploy_views.get_deployment')
    def test_get_deployment_success(self, get_mock):
        get_mock.return_value = {'deployment_id': 'd-1'}

        response = self.client.post(
            reverse('dashboard:codedeploy-deployment-get'),
            data=json.dumps({'deployment_id': 'd-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        get_mock.assert_called_once_with('d-1')

    @patch('dashboard.codedeploy_views.stop_deployment')
    def test_stop_deployment_success(self, stop_mock):
        stop_mock.return_value = {'deployment_id': 'd-1'}

        response = self.client.post(
            reverse('dashboard:codedeploy-deployment-stop'),
            data=json.dumps({'deployment_id': 'd-1', 'auto_rollback_enabled': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        stop_mock.assert_called_once_with('d-1', True)

    @patch('dashboard.codedeploy_views.continue_deployment')
    def test_continue_deployment_success(self, continue_mock):
        continue_mock.return_value = {'deployment_id': 'd-1'}

        response = self.client.post(
            reverse('dashboard:codedeploy-deployment-continue'),
            data=json.dumps({'deployment_id': 'd-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        continue_mock.assert_called_once_with('d-1')

    @patch('dashboard.codedeploy_views.put_lifecycle_event_hook_execution_status')
    def test_lifecycle_hook_status_success(self, hook_mock):
        hook_mock.return_value = {'deployment_id': 'd-1'}

        response = self.client.post(
            reverse('dashboard:codedeploy-lifecycle-hook-status'),
            data=json.dumps({'deployment_id': 'd-1', 'hook_execution_id': 'hook-1', 'status': 'Succeeded'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        hook_mock.assert_called_once_with('d-1', 'hook-1', 'Succeeded')

    @patch('dashboard.codedeploy_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn-1'}

        response = self.client.post(
            reverse('dashboard:codedeploy-tags'),
            data=json.dumps({'resource_arn': 'arn-1', 'tags': [{'Key': 'env', 'Value': 'dev'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        tag_mock.assert_called_once_with('arn-1', [{'Key': 'env', 'Value': 'dev'}])

    @patch('dashboard.codedeploy_views.untag_resource')
    def test_untag_resource_success(self, untag_mock):
        untag_mock.return_value = {'resource_arn': 'arn-1'}

        response = self.client.delete(
            reverse('dashboard:codedeploy-tags'),
            data=json.dumps({'resource_arn': 'arn-1', 'tag_keys': ['env']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        untag_mock.assert_called_once_with('arn-1', ['env'])
