import json
import subprocess
from unittest.mock import MagicMock, patch
from django.test import TestCase
from django.urls import reverse
from .console_views import OUTPUT_LIMIT


class CliConsoleTests(TestCase):
    def factory(self):
        factory = MagicMock()
        for key, value in {'region': 'eu-west-1', 'endpoint_url': 'http://localhost:4566', 'profile': None, 'access_key_id': 'test', 'secret_access_key': 'test', 'session_token': None, 'credential_source': 'environment', 'identity_expires_at': None, 'identity_label': None, 'identity_type': None}.items():
            setattr(factory, key, value)
        return factory

    def post(self, command, confirmed=False):
        return self.client.post(reverse('dashboard:console-run'), data=json.dumps({'command': command, 'confirmed': confirmed}), content_type='application/json')

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which', return_value='/usr/bin/aws')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_streams_exit_status_and_noninteractive_environment_are_preserved(self, factory, which, run):
        factory.return_value = self.factory()
        run.return_value = subprocess.CompletedProcess([], 7, stdout='partial output\n', stderr='failure detail\n')
        response = self.post('aws s3 ls')
        payload = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['exit_code'], 7)
        self.assertFalse(payload['ok'])
        self.assertEqual(payload['stdout'], 'partial output\n')
        self.assertEqual(payload['stderr'], 'failure detail\n')
        self.assertFalse(payload['stdout_truncated'])
        self.assertFalse(payload['stderr_truncated'])
        environment = run.call_args.kwargs['env']
        self.assertEqual(environment['AWS_PAGER'], '')
        self.assertEqual(environment['AWS_CLI_AUTO_PROMPT'], 'off')
        self.assertFalse(run.call_args.kwargs['shell'])
        self.assertEqual(run.call_args.kwargs['timeout'], 30)

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which', return_value='/usr/bin/aws')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_execution_context_reports_explicit_endpoint_and_region(self, factory, which, run):
        factory.return_value = self.factory()
        run.return_value = subprocess.CompletedProcess([], 0, stdout='[]', stderr='')
        payload = self.post('aws --endpoint-url=http://127.0.0.1:14566 --region us-west-2 s3 ls').json()
        self.assertEqual(payload['region'], 'us-west-2')
        self.assertEqual(payload['endpoint_url'], 'http://127.0.0.1:14566')
        self.assertEqual(payload['json'], [])

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which', return_value='/usr/bin/aws')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_output_clipping_is_explicit_for_each_stream(self, factory, which, run):
        factory.return_value = self.factory()
        run.return_value = subprocess.CompletedProcess([], 0, stdout='x' * (OUTPUT_LIMIT + 3), stderr='y' * OUTPUT_LIMIT)
        payload = self.post('aws s3 ls').json()
        self.assertTrue(payload['stdout_truncated'])
        self.assertFalse(payload['stderr_truncated'])
        self.assertEqual(len(payload['stdout']), OUTPUT_LIMIT)
        self.assertEqual(payload['output_limit'], OUTPUT_LIMIT)

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_confirmation_is_required_with_global_flags_and_string_confirmation_is_not_accepted(self, factory, run):
        factory.return_value = self.factory()
        for command in ['aws s3 rb s3://temporary', 'aws --no-paginate s3 rb s3://temporary', 'aws --no-cli-pager --region eu-west-1 sqs delete-queue --queue-url http://localhost/queue']:
            with self.subTest(command=command):
                response = self.post(command, confirmed='false')
                self.assertEqual(response.status_code, 409)
                self.assertTrue(response.json()['requires_confirmation'])
                self.assertEqual(response.json()['command'], command)
        run.assert_not_called()

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which', return_value='/usr/bin/aws')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_confirmed_command_runs_once(self, factory, which, run):
        factory.return_value = self.factory()
        run.return_value = subprocess.CompletedProcess([], 0, stdout='', stderr='')
        response = self.post('aws s3 rb s3://temporary', confirmed=True)
        self.assertEqual(response.status_code, 200)
        run.assert_called_once()

    @patch('dashboard.console_views.subprocess.run')
    @patch('dashboard.console_views.shutil.which', return_value='/usr/bin/aws')
    @patch('dashboard.console_views.FlociClientFactory')
    def test_timeout_does_not_report_a_successful_exit_code(self, factory, which, run):
        factory.return_value = self.factory()
        run.side_effect = subprocess.TimeoutExpired('aws', 30)
        response = self.post('aws s3 ls')
        self.assertEqual(response.status_code, 408)
        self.assertIn('timed out', response.json()['error'])
        self.assertNotIn('exit_code', response.json())
