import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class SchedulerPageTemplateTests(SimpleTestCase):

    def test_scheduler_registry_marks_service_interactive(self):
        service = get_service('scheduler')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_schedule' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_schedule_group' for action in service.actions))


class SchedulerActionsApiTests(SimpleTestCase):
    @patch('dashboard.scheduler_views.create_schedule_group')
    def test_create_schedule_group_success(self, create_mock):
        create_mock.return_value = {'group_name': 'local-jobs'}

        response = self.client.post(
            reverse('dashboard:scheduler-groups'),
            data=json.dumps({'name': 'local-jobs', 'tags': [{'Key': 'env', 'Value': 'local'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['group_name'], 'local-jobs')
        create_mock.assert_called_once_with('local-jobs', tags=[{'Key': 'env', 'Value': 'local'}])

    def test_create_schedule_group_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:scheduler-groups'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'scheduler')
        self.assertEqual(response.json()['operation'], 'create_schedule_group')

    @patch('dashboard.scheduler_views.delete_schedule_group')
    def test_delete_schedule_group_success(self, delete_mock):
        delete_mock.return_value = {'group_name': 'local-jobs', 'deleted': True}

        response = self.client.delete(reverse('dashboard:scheduler-group-detail', kwargs={'group_name': 'local-jobs'}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('local-jobs')

    @patch('dashboard.scheduler_views.create_schedule')
    def test_create_schedule_success(self, create_mock):
        create_mock.return_value = {'name': 'hourly-worker', 'group': 'default'}
        payload = {
            'name': 'hourly-worker',
            'group_name': 'default',
            'schedule_expression': 'rate(1 hour)',
            'target': {'Arn': 'arn:aws:lambda:us-east-1:000000000000:function:worker'},
            'state': 'ENABLED',
        }

        response = self.client.post(
            reverse('dashboard:scheduler-schedules'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'hourly-worker')
        create_mock.assert_called_once()
        called_kwargs = create_mock.call_args.kwargs
        self.assertEqual(called_kwargs['name'], 'hourly-worker')
        self.assertEqual(called_kwargs['group_name'], 'default')
        self.assertEqual(called_kwargs['schedule_expression'], 'rate(1 hour)')
        self.assertEqual(called_kwargs['target'], payload['target'])
        self.assertEqual(called_kwargs['state'], 'ENABLED')

    @patch('dashboard.scheduler_views.update_schedule')
    def test_update_schedule_success(self, update_mock):
        update_mock.return_value = {'name': 'hourly-worker', 'group': 'default', 'state': 'DISABLED'}
        payload = {
            'schedule_expression': 'rate(30 minutes)',
            'target': {'Arn': 'arn:aws:sqs:us-east-1:000000000000:queue'},
            'state': 'DISABLED',
        }

        response = self.client.put(
            reverse('dashboard:scheduler-schedule-detail', kwargs={
                'group_name': 'default',
                'schedule_name': 'hourly-worker',
            }),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['state'], 'DISABLED')
        update_mock.assert_called_once()
        called_kwargs = update_mock.call_args.kwargs
        self.assertEqual(called_kwargs['name'], 'hourly-worker')
        self.assertEqual(called_kwargs['group_name'], 'default')
        self.assertEqual(called_kwargs['schedule_expression'], 'rate(30 minutes)')

    @patch('dashboard.scheduler_views.delete_schedule')
    def test_delete_schedule_success(self, delete_mock):
        delete_mock.return_value = {'name': 'hourly-worker', 'group': 'default', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:scheduler-schedule-detail', kwargs={
                'group_name': 'default',
                'schedule_name': 'hourly-worker',
            }),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('hourly-worker', group_name='default')

    @patch('dashboard.scheduler_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn:aws:scheduler:us-east-1:000000000000:schedule-group/local-jobs'}
        payload = {
            'resource_arn': 'arn:aws:scheduler:us-east-1:000000000000:schedule-group/local-jobs',
            'tags': [{'Key': 'env', 'Value': 'local'}],
        }

        response = self.client.post(
            reverse('dashboard:scheduler-tags'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_arn'], payload['resource_arn'])
        tag_mock.assert_called_once_with(payload['resource_arn'], payload['tags'])

    @patch('dashboard.scheduler_views.untag_resource')
    def test_untag_resource_success(self, untag_mock):
        untag_mock.return_value = {'resource_arn': 'arn:aws:scheduler:us-east-1:000000000000:schedule-group/local-jobs'}
        payload = {
            'resource_arn': 'arn:aws:scheduler:us-east-1:000000000000:schedule-group/local-jobs',
            'tag_keys': ['env'],
        }

        response = self.client.delete(
            reverse('dashboard:scheduler-tags'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_arn'], payload['resource_arn'])
        untag_mock.assert_called_once_with(payload['resource_arn'], payload['tag_keys'])
