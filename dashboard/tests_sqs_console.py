import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .aws import sqs_inventory


class SQSQueuesApiTests(SimpleTestCase):
    def test_create_queue_rejects_invalid_name(self):
        response = self.client.post(
            reverse('dashboard:sqs-queues'),
            data=json.dumps({'name': 'bad queue'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'sqs')
        self.assertEqual(response.json()['operation'], 'create_queue')

    @patch('dashboard.sqs_views.create_queue')
    def test_create_queue_success(self, create_mock):
        create_mock.return_value = {'name': 'orders', 'url': 'http://localhost/000000000000/orders'}

        response = self.client.post(
            reverse('dashboard:sqs-queues'),
            data=json.dumps({'name': 'orders', 'fifo': False}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'orders')
        create_mock.assert_called_once_with('orders', fifo=False, visibility_timeout=None)

    @patch('dashboard.sqs_views.delete_queue')
    def test_delete_queue_success(self, delete_mock):
        delete_mock.return_value = {'name': 'orders', 'deleted': True}

        response = self.client.delete(reverse('dashboard:sqs-queue-delete', kwargs={'queue_name': 'orders'}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('orders')

    @patch('dashboard.sqs_views.send_message')
    def test_send_message_success(self, send_mock):
        send_mock.return_value = {'message_id': 'abc123'}

        response = self.client.post(
            reverse('dashboard:sqs-messages-send', kwargs={'queue_name': 'orders'}),
            data=json.dumps({'body': 'hello', 'message_attributes': {'TraceId': 't-123'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message_id'], 'abc123')
        send_mock.assert_called_once_with(
            'orders',
            'hello',
            delay_seconds=None,
            message_group_id=None,
            message_deduplication_id=None,
            message_attributes={'TraceId': 't-123'},
        )

    @patch('dashboard.sqs_views.set_queue_attributes')
    def test_set_queue_attributes_success(self, attr_mock):
        attr_mock.return_value = {'queue': 'orders', 'updated': True}

        response = self.client.post(
            reverse('dashboard:sqs-queue-attributes', kwargs={'queue_name': 'orders'}),
            data=json.dumps({'VisibilityTimeout': '45'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['updated'])
        attr_mock.assert_called_once_with('orders', {'VisibilityTimeout': '45'})

    @patch('dashboard.sqs_views.start_message_move_task')
    def test_start_message_move_task_success(self, move_mock):
        move_mock.return_value = {'task_handle': 'task-1001', 'status': 'RUNNING'}

        response = self.client.post(
            reverse('dashboard:sqs-queue-redrive', kwargs={'queue_name': 'orders'}),
            data=json.dumps({'source_arn': 'arn:aws:sqs:us-east-1:000000000000:orders'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['task_handle'], 'task-1001')
        move_mock.assert_called_once_with(
            source_arn='arn:aws:sqs:us-east-1:000000000000:orders',
            destination_arn=None,
            max_number_of_messages_per_second=None,
        )

    @patch('dashboard.sqs_views.receive_messages')
    def test_receive_messages_success(self, receive_mock):
        receive_mock.return_value = {'queue': 'orders', 'messages': [{'MessageId': 'abc123'}]}

        response = self.client.post(
            reverse('dashboard:sqs-messages-receive', kwargs={'queue_name': 'orders'}),
            data=json.dumps({'max_number': 2}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['messages']), 1)
        receive_mock.assert_called_once_with(
            'orders',
            max_number=2,
            visibility_timeout=None,
            wait_time_seconds=0,
        )

    @patch('dashboard.sqs_views.delete_message')
    def test_delete_message_success(self, delete_mock):
        delete_mock.return_value = {'queue': 'orders', 'deleted': 'true'}

        response = self.client.delete(
            reverse('dashboard:sqs-message-delete', kwargs={'queue_name': 'orders'}),
            data=json.dumps({'receipt_handle': 'receipt'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('orders', 'receipt')

    @patch('dashboard.sqs_views.purge_queue')
    def test_purge_queue_success(self, purge_mock):
        purge_mock.return_value = {'name': 'orders', 'purged': 'true'}

        response = self.client.post(reverse('dashboard:sqs-queue-purge', kwargs={'queue_name': 'orders'}))

        self.assertEqual(response.status_code, 200)
        purge_mock.assert_called_once_with('orders')


class SQSInventoryTests(SimpleTestCase):
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_surfaces_1mb_limit_and_delayed_message_count(self, factory_mock):
        sqs = MagicMock()
        sqs.list_queues.return_value = {'QueueUrls': ['http://localhost:4566/000000000000/orders']}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': 'arn:aws:sqs:us-east-1:000000000000:orders',
                'ApproximateNumberOfMessages': '2',
                'ApproximateNumberOfMessagesNotVisible': '1',
                'ApproximateNumberOfMessagesDelayed': '3',
                'MaximumMessageSize': '1048576',
            },
        }
        sqs.list_queue_tags.return_value = {'Tags': {}}
        sqs.list_dead_letter_source_queues.return_value = {'queueUrls': []}
        sqs.list_message_move_tasks.return_value = {'Results': []}
        factory = MagicMock(endpoint_url='http://localhost:4566')
        factory.client.return_value = sqs
        factory_mock.return_value = factory

        result = sqs_inventory()

        self.assertEqual(result['configuration']['max_message_size_bytes'], 1048576)
        self.assertEqual(result['summary']['delayed_messages'], 3)
        self.assertEqual(result['queues'][0]['approximate_delayed'], 3)
        self.assertIn('ApproximateNumberOfMessagesDelayed', result['notes'][0])
