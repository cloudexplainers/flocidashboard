import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service
from .stepfunctions_api import create_state_machine, describe_execution, start_execution, stop_execution


class StepFunctionsPageTemplateTests(SimpleTestCase):
    def test_stepfunctions_service_page_embeds_console(self):
        response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': 'stepfunctions'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="stepfunctions-summary"')
        self.assertContains(response, 'id="stepfunctions-console-root"')
        self.assertContains(response, 'dashboard/stepfunctions-console.css')
        self.assertContains(response, 'dashboard/service-console.js')
        self.assertContains(response, 'dashboard/stepfunctions-console.js')

    def test_stepfunctions_registry_exposes_management_lifecycle(self):
        actions = {action.name for action in get_service('stepfunctions').actions}
        self.assertTrue({'create_state_machine', 'delete_state_machine', 'start_execution', 'stop_execution', 'publish_state_machine_version', 'delete_state_machine_version'} <= actions)


class StepFunctionsApiTests(SimpleTestCase):
    @patch('dashboard.stepfunctions_views.create_state_machine')
    def test_create_state_machine_endpoint_success(self, create_mock):
        create_mock.return_value = {
            'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow',
            'creation_date': '2026-08-25T12:00:00Z',
        }

        response = self.client.post(
            reverse('dashboard:stepfunctions-state-machines'),
            data=json.dumps({
                'name': 'order-flow',
                'role_arn': 'arn:aws:iam::000000000000:role/ExecutionRole',
                'definition': {'StartAt': 'Pass', 'States': {'Pass': {'Type': 'Pass', 'End': True}}},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state_machine_arn'], 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow')

    @patch('dashboard.stepfunctions_views.delete_state_machine')
    def test_delete_state_machine_endpoint_success(self, delete_mock):
        delete_mock.return_value = {
            'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow',
            'deleted': True,
        }

        response = self.client.delete(
            reverse('dashboard:stepfunctions-state-machine-detail', kwargs={'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])

    @patch('dashboard.stepfunctions_views.start_execution')
    def test_start_execution_endpoint_success(self, start_mock):
        start_mock.return_value = {
            'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1',
            'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow',
        }

        response = self.client.post(
            reverse('dashboard:stepfunctions-executions-start'),
            data=json.dumps({
                'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow',
                'input': {'orderId': '123'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['execution_arn'], 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1')

    @patch('dashboard.stepfunctions_views.describe_execution')
    def test_describe_execution_endpoint_success(self, describe_mock):
        describe_mock.return_value = {
            'executionArn': 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1',
            'status': 'SUCCEEDED',
            'input': '{"orderId": "123"}',
            'output': '{"status": "CONFIRMED"}',
        }

        response = self.client.get(
            reverse('dashboard:stepfunctions-execution-detail', kwargs={'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'SUCCEEDED')

    @patch('dashboard.stepfunctions_views.get_execution_history')
    def test_get_execution_history_endpoint_success(self, history_mock):
        history_mock.return_value = {
            'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1',
            'events': [{'id': 1, 'type': 'ExecutionStarted'}],
        }

        response = self.client.get(
            reverse('dashboard:stepfunctions-execution-history', kwargs={'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['events']), 1)

    @patch('dashboard.stepfunctions_views.stop_execution')
    def test_stop_execution_endpoint_success(self, stop_mock):
        stop_mock.return_value = {
            'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1',
            'stop_date': '2026-08-25T12:05:00Z',
        }

        response = self.client.post(
            reverse('dashboard:stepfunctions-executions-stop'),
            data=json.dumps({
                'execution_arn': 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1',
                'error': 'UserCancel',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['execution_arn'], 'arn:aws:states:us-east-1:000000000000:execution:order-flow:exec-1')

    @patch('dashboard.stepfunctions_views.publish_state_machine_version')
    def test_publish_version_endpoint_success(self, publish_mock):
        publish_mock.return_value = {
            'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow',
            'state_machine_version_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow:1',
        }

        response = self.client.post(
            reverse('dashboard:stepfunctions-state-machine-versions-publish'),
            data=json.dumps({
                'state_machine_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state_machine_version_arn'], 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow:1')

    @patch('dashboard.stepfunctions_views.delete_state_machine_version')
    def test_delete_version_endpoint_success(self, delete_v_mock):
        delete_v_mock.return_value = {
            'state_machine_version_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow:1',
        }

        response = self.client.delete(
            reverse('dashboard:stepfunctions-state-machine-version-delete'),
            data=json.dumps({
                'state_machine_version_arn': 'arn:aws:states:us-east-1:000000000000:stateMachine:order-flow:1',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)


class StepFunctionsConsoleContractTests(SimpleTestCase):
    @patch('dashboard.stepfunctions_api._stepfunctions_client')
    def test_history_continuation_token_reaches_sdk(self, client_mock):
        from .stepfunctions_api import get_execution_history
        client_mock.return_value.get_execution_history.return_value = {'events': [{'id': 101}], 'nextToken': 'later'}
        arn = 'arn:aws:states:us-east-1:000000000000:execution:workflow:run'
        response = self.client.get(reverse('dashboard:stepfunctions-execution-history', kwargs={'execution_arn': arn}), {'next_token': 'page-two'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['next_token'], 'later')
        client_mock.return_value.get_execution_history.assert_called_once_with(executionArn=arn, maxResults=100, reverseOrder=False, nextToken='page-two')

    @patch('dashboard.stepfunctions_api._stepfunctions_client')
    def test_definition_update_preserves_role_unless_explicitly_changed(self, client_mock):
        arn = 'arn:aws:states:us-east-1:000000000000:stateMachine:workflow'
        client_mock.return_value.update_state_machine.return_value = {'revisionId': 'updated'}
        definition = {'StartAt': 'Done', 'States': {'Done': {'Type': 'Succeed'}}}
        response = self.client.patch(reverse('dashboard:stepfunctions-state-machine-detail', kwargs={'state_machine_arn': arn}), data=json.dumps({'definition': definition}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        arguments = client_mock.return_value.update_state_machine.call_args.kwargs
        self.assertNotIn('roleArn', arguments)
        self.assertEqual(json.loads(arguments['definition']), definition)

    @patch('dashboard.stepfunctions_api._stepfunctions_client')
    def test_invalid_definition_never_updates_sdk(self, client_mock):
        arn = 'arn:aws:states:us-east-1:000000000000:stateMachine:workflow'
        response = self.client.patch(reverse('dashboard:stepfunctions-state-machine-detail', kwargs={'state_machine_arn': arn}), data=json.dumps({'definition': '{broken'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        client_mock.assert_not_called()

    @patch('dashboard.aws.FlociClientFactory')
    def test_activity_create_and_delete_use_distinct_sdk_operations(self, factory_mock):
        client = factory_mock.return_value.client.return_value
        client.create_activity.return_value = {'activityArn': 'arn:aws:states:us-east-1:000000000000:activity:worker'}
        response = self.client.post('/api/stepfunctions/activities/', data=json.dumps({'name': 'worker'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.create_activity.assert_called_once_with(name='worker')
        arn = response.json()['activityArn']
        response = self.client.delete('/api/stepfunctions/activities/', data=json.dumps({'activity_arn': arn}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.delete_activity.assert_called_once_with(activityArn=arn)

    @patch('dashboard.aws.FlociClientFactory')
    def test_callback_validates_json_and_keeps_zero_output(self, factory_mock):
        response = self.client.post('/api/stepfunctions/tasks/callback/', data=json.dumps({'task_token': 'token', 'success': True, 'output': '0'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        factory_mock.return_value.client.return_value.send_task_success.assert_called_once_with(taskToken='token', output='0')
        response = self.client.post('/api/stepfunctions/tasks/callback/', data=json.dumps({'task_token': 'token', 'success': 'false'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
