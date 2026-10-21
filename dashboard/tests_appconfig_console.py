import io
import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .appconfig_api import create_hosted_configuration_version, get_latest_configuration
from .services import get_service


class AppConfigPageTemplateTests(SimpleTestCase):

    def test_registry_marks_service_interactive(self):
        service = get_service('appconfig')
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_application' for action in service.actions))
        self.assertTrue(any(action.name == 'start_deployment' for action in service.actions))
        self.assertTrue(any(action.name == 'get_latest_configuration' for action in service.actions))


class AppConfigActionsApiTests(SimpleTestCase):
    @patch('dashboard.appconfig_views.create_application')
    def test_create_application(self, mock):
        mock.return_value = {'application_id': 'app-1'}
        response = self.client.post(reverse('dashboard:appconfig-applications'), data=json.dumps({'name': 'my-app', 'description': 'Local'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('my-app', description='Local')

    def test_create_application_rejects_invalid_json(self):
        response = self.client.post(reverse('dashboard:appconfig-applications'), data='bad', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['operation'], 'create_application')

    @patch('dashboard.appconfig_views.delete_application')
    def test_delete_application(self, mock):
        mock.return_value = {'application_id': 'app-1'}
        response = self.client.delete(reverse('dashboard:appconfig-application-detail', kwargs={'application_id': 'app-1'}))
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('app-1')

    @patch('dashboard.appconfig_views.create_environment')
    def test_create_environment(self, mock):
        mock.return_value = {'environment_id': 'env-1'}
        response = self.client.post(reverse('dashboard:appconfig-environments', kwargs={'application_id': 'app-1'}), data=json.dumps({'name': 'dev', 'description': ''}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('app-1', 'dev', description='')

    @patch('dashboard.appconfig_views.create_configuration_profile')
    def test_create_profile(self, mock):
        mock.return_value = {'configuration_profile_id': 'profile-1'}
        response = self.client.post(reverse('dashboard:appconfig-profiles', kwargs={'application_id': 'app-1'}), data=json.dumps({'name': 'profile', 'location_uri': 'hosted', 'profile_type': 'AWS.Freeform', 'description': ''}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('app-1', 'profile', location_uri='hosted', profile_type='AWS.Freeform', description='')

    @patch('dashboard.appconfig_views.create_hosted_configuration_version')
    def test_create_hosted_version(self, mock):
        mock.return_value = {'version_number': 1}
        response = self.client.post(reverse('dashboard:appconfig-hosted-versions', kwargs={'application_id': 'app-1', 'profile_id': 'profile-1'}), data=json.dumps({'content': '{"foo":"bar"}', 'content_type': 'application/json', 'description': ''}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('app-1', 'profile-1', '{"foo":"bar"}', content_type='application/json', description='')

    @patch('dashboard.appconfig_views.create_deployment_strategy')
    def test_create_strategy(self, mock):
        mock.return_value = {'deployment_strategy_id': 'strategy-1'}
        response = self.client.post(reverse('dashboard:appconfig-deployment-strategies'), data=json.dumps({'name': 'immediate', 'duration_minutes': 0, 'growth_factor': 100, 'final_bake_minutes': 0, 'description': ''}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('immediate', duration_minutes=0, growth_factor=100, final_bake_minutes=0, description='')

    @patch('dashboard.appconfig_views.start_deployment')
    def test_start_deployment(self, mock):
        mock.return_value = {'deployment_number': 1}
        payload = {'application_id': 'app-1', 'environment_id': 'env-1', 'profile_id': 'profile-1', 'configuration_version': '1', 'deployment_strategy_id': 'strategy-1', 'description': ''}
        response = self.client.post(reverse('dashboard:appconfig-deployments-start'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('app-1', 'env-1', 'profile-1', '1', 'strategy-1', description='')

    @patch('dashboard.appconfig_views.start_configuration_session')
    def test_start_session(self, mock):
        mock.return_value = {'initial_configuration_token': 'token-1'}
        response = self.client.post(reverse('dashboard:appconfig-sessions-start'), data=json.dumps({'application_id': 'app-1', 'environment_id': 'env-1', 'profile_id': 'profile-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('app-1', 'env-1', 'profile-1')

    @patch('dashboard.appconfig_views.get_latest_configuration')
    def test_get_latest_configuration(self, mock):
        mock.return_value = {'configuration': {'preview': '{"foo":"bar"}'}}
        response = self.client.post(reverse('dashboard:appconfig-latest-configuration'), data=json.dumps({'configuration_token': 'token-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('token-1')


class AppConfigApiHelperTests(SimpleTestCase):
    @patch('dashboard.appconfig_api._client')
    def test_hosted_version_encodes_content(self, client_mock):
        client_mock.return_value.create_hosted_configuration_version.return_value = {'VersionNumber': 1}
        result = create_hosted_configuration_version('app-1', 'profile-1', '{"foo":"bar"}')
        self.assertEqual(result['version_number'], 1)
        self.assertEqual(client_mock.return_value.create_hosted_configuration_version.call_args.kwargs['Content'], b'{"foo":"bar"}')

    @patch('dashboard.appconfig_api._data_client')
    def test_latest_configuration_decodes_stream(self, client_mock):
        client_mock.return_value.get_latest_configuration.return_value = {
            'Configuration': io.BytesIO(b'{"foo":"bar"}'),
            'ContentType': 'application/json',
            'NextPollConfigurationToken': 'token-2',
            'NextPollIntervalInSeconds': 15,
        }
        result = get_latest_configuration('token-1')
        self.assertEqual(result['configuration']['preview'], '{"foo":"bar"}')
        self.assertEqual(result['next_poll_configuration_token'], 'token-2')
