"""Failures found by executing all lab steps against Floci 2.3.0."""

import json
import importlib
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from django.core.cache import cache
from django.core.management import call_command, CommandError
from django.test import SimpleTestCase

sfn = importlib.import_module('dashboard.labs.stepfunctions_labs')
ec2_guided = importlib.import_module('dashboard.labs.ec2_guided')


class LabRegressionTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_failed_stepfunctions_execution_never_marks_complete(self):
        sfn.marker('parallel-sm', {'stateMachineArn': sfn.PARALLEL_SM_ARN})
        sfn.marker('parallel-exec', {'executionArn': 'exec-failed', 'status': 'FAILED'})
        sfn.marker('parallel-desc', {'status': 'FAILED'})
        status = sfn.status('stepfunctions', 'parallel-retry-workflow')
        self.assertFalse(status['complete'])
        self.assertFalse(status['steps']['execute-parallel-success']['verified'])

    @patch('dashboard.labs.stepfunctions_labs.client')
    def test_parallel_description_checks_actual_execution_outputs(self, client):
        sfn.marker('parallel-exec', {'executionArn': 'exec-1', 'status': 'SUCCEEDED'})
        client.return_value.describe_execution.return_value = {
            'status': 'SUCCEEDED',
            'output': json.dumps({'checks': [{'payment': 'VERIFIED_OK'}, {'inventory': 'IN_STOCK'}],
                                  'overall_status': {'status': 'ALL_CHECKS_PASSED'}}),
        }
        self.assertTrue(sfn.step_describe_parallel()['verified'])
        client.return_value.describe_execution.return_value = {'status': 'FAILED', 'error': 'States.ResultPathMatchFailure'}
        self.assertFalse(sfn.step_describe_parallel()['verified'])
        self.assertFalse(sfn.status('stepfunctions', 'parallel-retry-workflow')['steps']['describe-parallel-execution']['verified'])

    @patch('dashboard.labs.stepfunctions_labs.time.sleep')
    def test_execution_waits_for_completion(self, sleep):
        client = Mock()
        client.describe_execution.side_effect = [{'status': 'RUNNING'}, {'status': 'SUCCEEDED'}]
        self.assertEqual(sfn.wait_for_execution(client, 'exec-1')['status'], 'SUCCEEDED')
        self.assertEqual(client.describe_execution.call_count, 2)

    @patch('dashboard.labs.ec2_guided._ec2')
    def test_guided_network_uses_real_owned_resource_ids(self, client):
        ec2 = client.return_value
        ec2.create_vpc.return_value = {'Vpc': {'VpcId': 'vpc-new'}}
        ec2.create_subnet.return_value = {'Subnet': {'SubnetId': 'subnet-new'}}
        ec2.describe_security_groups.return_value = {'SecurityGroups': [{'GroupId': 'sg-new'}]}
        network = ec2_guided._network('guided-imds')
        self.assertEqual(network, {'vpc_id': 'vpc-new', 'subnet_id': 'subnet-new', 'group_id': 'sg-new'})
        ec2_guided._network('guided-imds')
        ec2.create_vpc.assert_called_once()
        ec2.create_subnet.assert_called_once()

    def test_failed_guided_result_does_not_mark_verified(self):
        ec2_guided._step_result('guided-imds', 'launch-instance', '', {}, False, 'failed', 0)
        self.assertFalse(ec2_guided.guided_status('guided-imds')['steps']['launch-instance']['verified'])

    @patch.dict('os.environ')
    @patch('dashboard.management.commands.check_labs.FlociClientFactory')
    @patch('dashboard.management.commands.check_labs.all_labs')
    @patch('dashboard.management.commands.check_labs.run_lab_step')
    @patch('dashboard.management.commands.check_labs.lab_status')
    @patch('dashboard.management.commands.check_labs.reset_lab')
    def test_live_audit_fails_unverified_steps_and_still_cleans_up(self, reset, status, run, labs, factory):
        labs.return_value = [{'service': 'iam', 'key': 'example', 'steps': [{'key': 'first'}]}]
        factory.return_value.health.return_value = {'ok': True, 'data': {'version': '2.3.0'}}
        run.return_value = {'verified': False}
        status.return_value = {'complete': True}  # Must not erase a failed step.
        reset.return_value = {'reset': True}
        with TemporaryDirectory() as directory:
            report = Path(directory) / 'report.json'
            with self.assertRaises(CommandError):
                call_command('check_labs', endpoint='http://localhost:14566', report=str(report), stdout=StringIO())
            self.assertFalse(json.loads(report.read_text())['labs'][0]['passed'])
        reset.assert_called_once_with('iam', 'example')
