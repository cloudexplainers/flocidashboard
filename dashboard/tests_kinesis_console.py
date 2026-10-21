import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class KinesisActionsApiTests(SimpleTestCase):
    @patch('dashboard.kinesis_views.create_stream')
    def test_create_stream_success(self, create_mock):
        create_mock.return_value = {
            'name': 'orders',
            'mode': 'PROVISIONED',
            'shard_count': 1,
        }

        response = self.client.post(
            reverse('dashboard:kinesis-streams'),
            data=json.dumps({'name': 'orders', 'mode': 'PROVISIONED', 'shard_count': 1}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'orders')
        create_mock.assert_called_once_with('orders', shard_count=1, mode='PROVISIONED')

    def test_create_stream_rejects_missing_name(self):
        response = self.client.post(
            reverse('dashboard:kinesis-streams'),
            data=json.dumps({'mode': 'PROVISIONED'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'kinesis')
        self.assertEqual(response.json()['operation'], 'create_stream')

    @patch('dashboard.kinesis_views.put_record')
    def test_put_record_success(self, put_mock):
        put_mock.return_value = {
            'stream_name': 'orders',
            'partition_key': 'customer-1',
            'sequence_number': '123',
            'shard_id': 'shardId-000000000000',
        }

        response = self.client.post(
            reverse('dashboard:kinesis-records-put', kwargs={'stream_name': 'orders'}),
            data=json.dumps({'partition_key': 'customer-1', 'data': {'order_id': 'abc'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sequence_number'], '123')
        put_mock.assert_called_once_with('orders', 'customer-1', {'order_id': 'abc'})

    @patch('dashboard.kinesis_views.get_records')
    def test_get_records_success(self, get_mock):
        get_mock.return_value = {
            'stream_name': 'orders',
            'shard_id': 'shardId-000000000000',
            'records': [{'sequence_number': '123', 'data_json': {'ok': True}}],
        }

        response = self.client.post(
            reverse('dashboard:kinesis-records-get', kwargs={
                'stream_name': 'orders',
                'shard_id': 'shardId-000000000000',
            }),
            data=json.dumps({'iterator_type': 'TRIM_HORIZON', 'limit': 10}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['records'][0]['data_json'], {'ok': True})
        get_mock.assert_called_once_with(
            'orders',
            'shardId-000000000000',
            iterator_type='TRIM_HORIZON',
            limit=10,
            sequence_number='',
        )
