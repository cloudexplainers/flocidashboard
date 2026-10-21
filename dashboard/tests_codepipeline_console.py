import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class CodePipelineConsoleTests(SimpleTestCase):

    def test_codepipeline_registry_actions(self):
        service = get_service('codepipeline')
        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        action_names = {action.name for action in service.actions}
        self.assertTrue({
            'create_pipeline',
            'delete_pipeline',
            'start_pipeline_execution',
            'retry_stage_execution',
            'put_approval_result',
            'set_stage_transition',
        } <= action_names)

    @patch('dashboard.codepipeline_views.create_pipeline')
    def test_create_pipeline_endpoint(self, create_mock):
        create_mock.return_value = {'pipeline': {'name': 'test-pipe'}}

        response = self.client.post(
            reverse('dashboard:codepipeline-pipelines-create'),
            data=json.dumps({'pipeline': {'name': 'test-pipe'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with({'name': 'test-pipe'})

    @patch('dashboard.codepipeline_views.start_pipeline_execution')
    def test_start_pipeline_execution_endpoint(self, start_mock):
        start_mock.return_value = {'pipeline_name': 'test-pipe', 'pipeline_execution_id': 'exec-123'}

        response = self.client.post(
            reverse('dashboard:codepipeline-pipeline-start', kwargs={'name': 'test-pipe'}),
            data=json.dumps({}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['pipeline_execution_id'], 'exec-123')
        start_mock.assert_called_once_with('test-pipe')

    @patch('dashboard.codepipeline_views.put_approval_result')
    def test_put_approval_result_endpoint(self, approve_mock):
        approve_mock.return_value = {'pipeline_name': 'test-pipe', 'status': 'Approved'}

        response = self.client.post(
            reverse('dashboard:codepipeline-pipeline-approve', kwargs={'name': 'test-pipe'}),
            data=json.dumps({
                'stage_name': 'Approval',
                'action_name': 'ManualGate',
                'status': 'Approved',
                'summary': 'LGTM',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Approved')
        approve_mock.assert_called_once_with(
            pipeline_name='test-pipe',
            stage_name='Approval',
            action_name='ManualGate',
            status='Approved',
            summary='LGTM',
            token=None,
        )

    @patch('dashboard.codepipeline_views.enable_stage_transition')
    def test_enable_stage_transition_endpoint(self, enable_mock):
        enable_mock.return_value = {'pipeline_name': 'test-pipe', 'stage_name': 'Deploy', 'enabled': True}

        response = self.client.post(
            reverse('dashboard:codepipeline-pipeline-transition', kwargs={'name': 'test-pipe'}),
            data=json.dumps({
                'stage_name': 'Deploy',
                'enabled': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['enabled'])
        enable_mock.assert_called_once_with('test-pipe', 'Deploy', transition_type='Inbound')

    @patch('dashboard.codepipeline_views.retry_stage_execution')
    def test_retry_stage_execution_endpoint(self, retry_mock):
        retry_mock.return_value = {'pipeline_name': 'test-pipe', 'pipeline_execution_id': 'exec-123'}

        response = self.client.post(
            reverse('dashboard:codepipeline-pipeline-retry', kwargs={'name': 'test-pipe'}),
            data=json.dumps({
                'stage_name': 'Build',
                'pipeline_execution_id': 'exec-123',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        retry_mock.assert_called_once_with(
            pipeline_name='test-pipe',
            stage_name='Build',
            pipeline_execution_id='exec-123',
            retry_mode='FAILED_ACTIONS',
        )

    @patch('dashboard.codepipeline_views.delete_pipeline')
    def test_delete_pipeline_endpoint(self, delete_mock):
        delete_mock.return_value = {'pipeline_name': 'test-pipe', 'deleted': True}

        response = self.client.post(
            reverse('dashboard:codepipeline-pipelines-delete'),
            data=json.dumps({'name': 'test-pipe'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('test-pipe')
