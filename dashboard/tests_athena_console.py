import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class AthenaPageTemplateTests(SimpleTestCase):

    def test_athena_registry_marks_service_interactive(self):
        service = get_service('athena')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'start_query_execution' for action in service.actions))
        self.assertTrue(any(action.name == 'get_query_results' for action in service.actions))
        self.assertTrue(any(action.name == 'create_work_group' for action in service.actions))


class AthenaActionsApiTests(SimpleTestCase):
    @patch('dashboard.athena_views.start_query_execution')
    def test_start_query_success(self, start_mock):
        start_mock.return_value = {'query_execution_id': 'query-1'}

        response = self.client.post(
            reverse('dashboard:athena-queries-start'),
            data=json.dumps({
                'query_string': 'SELECT 42 AS answer',
                'database': 'analytics',
                'catalog': 'AwsDataCatalog',
                'workgroup': 'primary',
                'output_location': 's3://query-results/',
                'execution_parameters': ['a'],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['query_execution_id'], 'query-1')
        start_mock.assert_called_once_with(
            query_string='SELECT 42 AS answer',
            database='analytics',
            catalog='AwsDataCatalog',
            workgroup='primary',
            output_location='s3://query-results/',
            execution_parameters=['a'],
        )

    def test_start_query_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:athena-queries-start'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'athena')
        self.assertEqual(response.json()['operation'], 'start_query_execution')

    @patch('dashboard.athena_views.stop_query_execution')
    def test_stop_query_success(self, stop_mock):
        stop_mock.return_value = {'query_execution_id': 'query-1'}

        response = self.client.post(
            reverse('dashboard:athena-queries-stop'),
            data=json.dumps({'query_execution_id': 'query-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        stop_mock.assert_called_once_with('query-1')

    @patch('dashboard.athena_views.get_query_results')
    def test_get_query_results_success(self, results_mock):
        results_mock.return_value = {'query_execution_id': 'query-1', 'result_set': {}}

        response = self.client.post(
            reverse('dashboard:athena-queries-results'),
            data=json.dumps({'query_execution_id': 'query-1', 'max_results': 10, 'next_token': 'next'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        results_mock.assert_called_once_with('query-1', max_results=10, next_token='next')

    @patch('dashboard.athena_views.get_query_execution')
    def test_get_query_execution_success(self, detail_mock):
        detail_mock.return_value = {'query_execution_id': 'query-1', 'query_execution': {}}

        response = self.client.post(
            reverse('dashboard:athena-queries-detail'),
            data=json.dumps({'query_execution_id': 'query-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        detail_mock.assert_called_once_with('query-1')

    @patch('dashboard.athena_views.create_work_group')
    def test_create_workgroup_success(self, create_mock):
        create_mock.return_value = {'name': 'analytics'}

        response = self.client.post(
            reverse('dashboard:athena-workgroups'),
            data=json.dumps({
                'name': 'analytics',
                'description': 'Local analytics',
                'output_location': 's3://query-results/',
                'configuration': {'EnforceWorkGroupConfiguration': False},
                'tags': [{'Key': 'env', 'Value': 'local'}],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            name='analytics',
            description='Local analytics',
            output_location='s3://query-results/',
            configuration={'EnforceWorkGroupConfiguration': False},
            tags=[{'Key': 'env', 'Value': 'local'}],
        )
