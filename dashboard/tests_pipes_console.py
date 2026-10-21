import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class PipesPageTemplateTests(SimpleTestCase):

    def test_pipes_registry_marks_service_interactive(self):
        service = get_service('pipes')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_pipe' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_pipe' for action in service.actions))


class PipesActionsApiTests(SimpleTestCase):
    @patch('dashboard.pipes_views.create_pipe')
    def test_create_pipe_success(self, create_mock):
        create_mock.return_value = {'name': 'orders-to-worker', 'current_state': 'RUNNING'}
        payload = {
            'name': 'orders-to-worker',
            'source': 'arn:aws:sqs:us-east-1:000000000000:orders',
            'target': 'arn:aws:lambda:us-east-1:000000000000:function:worker',
            'role_arn': 'arn:aws:iam::000000000000:role/pipe-role',
            'desired_state': 'RUNNING',
            'source_parameters': {},
            'target_parameters': {},
            'tags': {'env': 'local'},
        }

        response = self.client.post(
            reverse('dashboard:pipes-pipes'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'orders-to-worker')
        create_mock.assert_called_once()
        called_kwargs = create_mock.call_args.kwargs
        self.assertEqual(called_kwargs['name'], 'orders-to-worker')
        self.assertEqual(called_kwargs['source'], payload['source'])
        self.assertEqual(called_kwargs['target'], payload['target'])
        self.assertEqual(called_kwargs['role_arn'], payload['role_arn'])
        self.assertEqual(called_kwargs['tags'], {'env': 'local'})

    def test_create_pipe_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:pipes-pipes'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'pipes')
        self.assertEqual(response.json()['operation'], 'create_pipe')

    @patch('dashboard.pipes_views.update_pipe')
    def test_update_pipe_success(self, update_mock):
        update_mock.return_value = {'name': 'orders-to-worker', 'desired_state': 'STOPPED'}
        payload = {
            'source': 'arn:aws:sqs:us-east-1:000000000000:orders',
            'target': 'arn:aws:sqs:us-east-1:000000000000:processed',
            'role_arn': 'arn:aws:iam::000000000000:role/pipe-role',
            'desired_state': 'STOPPED',
        }

        response = self.client.put(
            reverse('dashboard:pipes-pipe-detail', kwargs={'pipe_name': 'orders-to-worker'}),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['desired_state'], 'STOPPED')
        update_mock.assert_called_once()
        called_kwargs = update_mock.call_args.kwargs
        self.assertEqual(called_kwargs['name'], 'orders-to-worker')
        self.assertEqual(called_kwargs['desired_state'], 'STOPPED')

    @patch('dashboard.pipes_views.delete_pipe')
    def test_delete_pipe_success(self, delete_mock):
        delete_mock.return_value = {'name': 'orders-to-worker', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:pipes-pipe-detail', kwargs={'pipe_name': 'orders-to-worker'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('orders-to-worker')

    @patch('dashboard.pipes_views.start_pipe')
    def test_start_pipe_success(self, start_mock):
        start_mock.return_value = {'name': 'orders-to-worker', 'current_state': 'RUNNING'}

        response = self.client.post(reverse('dashboard:pipes-pipe-start', kwargs={'pipe_name': 'orders-to-worker'}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['current_state'], 'RUNNING')
        start_mock.assert_called_once_with('orders-to-worker')

    @patch('dashboard.pipes_views.stop_pipe')
    def test_stop_pipe_success(self, stop_mock):
        stop_mock.return_value = {'name': 'orders-to-worker', 'current_state': 'STOPPED'}

        response = self.client.post(reverse('dashboard:pipes-pipe-stop', kwargs={'pipe_name': 'orders-to-worker'}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['current_state'], 'STOPPED')
        stop_mock.assert_called_once_with('orders-to-worker')

    @patch('dashboard.pipes_views.tag_pipe')
    def test_tag_pipe_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn:aws:pipes:us-east-1:000000000000:pipe/orders-to-worker'}
        payload = {
            'resource_arn': 'arn:aws:pipes:us-east-1:000000000000:pipe/orders-to-worker',
            'tags': {'env': 'local'},
        }

        response = self.client.post(
            reverse('dashboard:pipes-tags'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_arn'], payload['resource_arn'])
        tag_mock.assert_called_once_with(payload['resource_arn'], payload['tags'])

    @patch('dashboard.pipes_views.untag_pipe')
    def test_untag_pipe_success(self, untag_mock):
        untag_mock.return_value = {'resource_arn': 'arn:aws:pipes:us-east-1:000000000000:pipe/orders-to-worker'}
        payload = {
            'resource_arn': 'arn:aws:pipes:us-east-1:000000000000:pipe/orders-to-worker',
            'tag_keys': ['env'],
        }

        response = self.client.delete(
            reverse('dashboard:pipes-tags'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_arn'], payload['resource_arn'])
        untag_mock.assert_called_once_with(payload['resource_arn'], payload['tag_keys'])
