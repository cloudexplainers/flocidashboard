import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class CodeBuildPageTemplateTests(SimpleTestCase):

    def test_codebuild_registry_marks_service_interactive(self):
        service = get_service('codebuild')
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_project' for action in service.actions))
        self.assertTrue(any(action.name == 'start_build' for action in service.actions))
        self.assertTrue(any(action.name == 'retry_build' for action in service.actions))


class CodeBuildActionsApiTests(SimpleTestCase):
    @patch('dashboard.codebuild_views.create_project')
    def test_create_project(self, mock):
        mock.return_value = {'project_name': 'my-project'}
        options = {'source': {'type': 'NO_SOURCE'}}
        response = self.client.post(reverse('dashboard:codebuild-projects'), data=json.dumps({'name': 'my-project', 'options': options}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('my-project', options)

    def test_create_project_rejects_invalid_json(self):
        response = self.client.post(reverse('dashboard:codebuild-projects'), data='bad', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['operation'], 'create_project')

    @patch('dashboard.codebuild_views.update_project')
    def test_update_project(self, mock):
        mock.return_value = {'project_name': 'my-project'}
        response = self.client.patch(reverse('dashboard:codebuild-project-detail', kwargs={'project_name': 'my-project'}), data=json.dumps({'options': {'description': 'new'}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('my-project', {'description': 'new'})

    @patch('dashboard.codebuild_views.delete_project')
    def test_delete_project(self, mock):
        mock.return_value = {'project_name': 'my-project'}
        response = self.client.delete(reverse('dashboard:codebuild-project-detail', kwargs={'project_name': 'my-project'}))
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('my-project')

    @patch('dashboard.codebuild_views.start_build')
    def test_start_build(self, mock):
        mock.return_value = {'build_id': 'build-1'}
        response = self.client.post(reverse('dashboard:codebuild-builds-start'), data=json.dumps({'project_name': 'my-project', 'options': {'buildspecOverride': 'version: 0.2'}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('my-project', {'buildspecOverride': 'version: 0.2'})

    @patch('dashboard.codebuild_views.get_build')
    def test_get_build(self, mock):
        mock.return_value = {'build_id': 'build-1'}
        response = self.client.post(reverse('dashboard:codebuild-build-get'), data=json.dumps({'build_id': 'build-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('build-1')

    @patch('dashboard.codebuild_views.stop_build')
    def test_stop_build(self, mock):
        mock.return_value = {'build_id': 'build-1'}
        response = self.client.post(reverse('dashboard:codebuild-build-stop'), data=json.dumps({'build_id': 'build-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('build-1')

    @patch('dashboard.codebuild_views.retry_build')
    def test_retry_build(self, mock):
        mock.return_value = {'build_id': 'build-2'}
        response = self.client.post(reverse('dashboard:codebuild-build-retry'), data=json.dumps({'build_id': 'build-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('build-1')

    @patch('dashboard.codebuild_views.list_curated_images')
    def test_curated_images(self, mock):
        mock.return_value = {'platforms': []}
        response = self.client.get(reverse('dashboard:codebuild-curated-images'))
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with()

    @patch('dashboard.codebuild_views.create_report_group')
    def test_create_report_group(self, mock):
        mock.return_value = {'report_group_name': 'reports'}
        options = {'type': 'TEST'}
        response = self.client.post(reverse('dashboard:codebuild-report-groups'), data=json.dumps({'name': 'reports', 'options': options}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('reports', options)

    @patch('dashboard.codebuild_views.update_report_group')
    def test_update_report_group(self, mock):
        mock.return_value = {'report_group_arn': 'arn-1'}
        response = self.client.patch(reverse('dashboard:codebuild-report-group-detail'), data=json.dumps({'arn': 'arn-1', 'options': {'tags': []}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('arn-1', {'tags': []})

    @patch('dashboard.codebuild_views.delete_report_group')
    def test_delete_report_group(self, mock):
        mock.return_value = {'report_group_arn': 'arn-1'}
        response = self.client.delete(reverse('dashboard:codebuild-report-group-detail'), data=json.dumps({'arn': 'arn-1', 'delete_reports': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('arn-1', True)

    @patch('dashboard.codebuild_views.import_source_credentials')
    def test_import_source_credentials(self, mock):
        mock.return_value = {'arn': 'arn-1'}
        response = self.client.post(reverse('dashboard:codebuild-source-credentials'), data=json.dumps({'server_type': 'GITHUB', 'auth_type': 'PERSONAL_ACCESS_TOKEN', 'token': 'secret', 'username': ''}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('GITHUB', 'PERSONAL_ACCESS_TOKEN', 'secret', '')

    @patch('dashboard.codebuild_views.delete_source_credentials')
    def test_delete_source_credentials(self, mock):
        mock.return_value = {'arn': 'arn-1'}
        response = self.client.delete(reverse('dashboard:codebuild-source-credentials'), data=json.dumps({'arn': 'arn-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('arn-1')
