import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class ElasticBeanstalkConsoleTests(SimpleTestCase):

    def test_registry_is_interactive(self):
        service = get_service('elasticbeanstalk')
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertEqual(len(service.actions), 9)

    @patch('dashboard.elasticbeanstalk_views.create_application')
    def test_create_application(self, helper):
        helper.return_value = {'ApplicationName': 'web'}
        response = self.client.post(reverse('dashboard:elasticbeanstalk-applications'), data=json.dumps({'name': 'web', 'description': 'app'}), content_type='application/json')
        self.assertEqual(response.status_code, 200); helper.assert_called_once_with('web', description='app')

    @patch('dashboard.elasticbeanstalk_views.update_application')
    def test_update_application(self, helper):
        helper.return_value = {'ApplicationName': 'web'}
        response = self.client.patch(reverse('dashboard:elasticbeanstalk-application-detail', kwargs={'application_name': 'web'}), data=json.dumps({'description': 'new'}), content_type='application/json')
        self.assertEqual(response.status_code, 200); helper.assert_called_once_with('web', description='new')

    @patch('dashboard.elasticbeanstalk_views.delete_application')
    def test_delete_application(self, helper):
        helper.return_value = {'deleted': True}
        response = self.client.delete(reverse('dashboard:elasticbeanstalk-application-detail', kwargs={'application_name': 'web'}), data=json.dumps({'terminate_environments': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200); helper.assert_called_once_with('web', terminate_environments=True)

    @patch('dashboard.elasticbeanstalk_views.create_application_version')
    def test_create_version(self, helper):
        helper.return_value = {'VersionLabel': 'v1'}
        response = self.client.post(reverse('dashboard:elasticbeanstalk-versions', kwargs={'application_name': 'web'}), data=json.dumps({'version_label': 'v1', 'description': '', 's3_bucket': 'source', 's3_key': 'v1.zip'}), content_type='application/json')
        self.assertEqual(response.status_code, 200); helper.assert_called_once_with('web', 'v1', description='', s3_bucket='source', s3_key='v1.zip')

    @patch('dashboard.elasticbeanstalk_views.delete_application_version')
    def test_delete_version(self, helper):
        helper.return_value = {'deleted': True}
        response = self.client.delete(reverse('dashboard:elasticbeanstalk-version-detail', kwargs={'application_name': 'web', 'version_label': 'v1'}))
        self.assertEqual(response.status_code, 200); helper.assert_called_once_with('web', 'v1')

    @patch('dashboard.elasticbeanstalk_views.create_environment')
    def test_create_environment(self, helper):
        helper.return_value = {'EnvironmentName': 'web-dev'}
        body = {'application_name': 'web', 'environment_name': 'web-dev', 'version_label': 'v1', 'option_settings': []}
        response = self.client.post(reverse('dashboard:elasticbeanstalk-environments'), data=json.dumps(body), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        helper.assert_called_once_with('web', 'web-dev', description='', version_label='v1', cname_prefix='', solution_stack_name='', platform_arn='', option_settings=[])

    @patch('dashboard.elasticbeanstalk_views.update_environment')
    def test_update_environment(self, helper):
        helper.return_value = {'EnvironmentName': 'web-dev'}
        response = self.client.patch(reverse('dashboard:elasticbeanstalk-environment-detail', kwargs={'environment_name': 'web-dev'}), data=json.dumps({'description': 'dev'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        helper.assert_called_once_with('web-dev', description='dev', version_label='', solution_stack_name='', platform_arn='', option_settings=[])

    @patch('dashboard.elasticbeanstalk_views.terminate_environment')
    def test_terminate_environment(self, helper):
        helper.return_value = {'Status': 'Terminated'}
        response = self.client.post(reverse('dashboard:elasticbeanstalk-environment-terminate', kwargs={'environment_name': 'web-dev'}))
        self.assertEqual(response.status_code, 200); helper.assert_called_once_with('web-dev')

    @patch('dashboard.elasticbeanstalk_views.check_dns_availability')
    def test_dns_check(self, helper):
        helper.return_value = {'available': True}
        response = self.client.post(reverse('dashboard:elasticbeanstalk-dns-check'), data=json.dumps({'cname_prefix': 'web-dev'}), content_type='application/json')
        self.assertEqual(response.status_code, 200); helper.assert_called_once_with('web-dev')
