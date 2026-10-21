import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .dynamodb_api import execute_select_statement


class DynamoDBApiTests(SimpleTestCase):
    @patch('dashboard.dynamodb_views.scan_table')
    def test_scan_table_success(self, scan_mock):
        scan_mock.return_value = {
            'table': 'orders',
            'count': 1,
            'scanned_count': 1,
            'items': [{'pk': '1'}],
            'last_evaluated_key': None,
        }

        response = self.client.post(
            reverse('dashboard:dynamodb-table-scan', kwargs={'table_name': 'orders'}),
            data=json.dumps({'limit': 10}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        scan_mock.assert_called_once_with(
            'orders',
            limit=10,
            exclusive_start_key=None,
            filter_expression=None,
            expression_attribute_names=None,
            expression_attribute_values=None,
            index_name=None,
        )

    @patch('dashboard.dynamodb_views.query_table')
    def test_query_table_success(self, query_mock):
        query_mock.return_value = {
            'table': 'orders',
            'count': 1,
            'items': [{'id': '101', 'status': 'ACTIVE'}],
            'last_evaluated_key': None,
        }

        response = self.client.post(
            reverse('dashboard:dynamodb-table-query', kwargs={'table_name': 'orders'}),
            data=json.dumps({
                'key_condition_expression': 'id = :pk',
                'expression_attribute_values': {':pk': '101'},
                'limit': 10,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        query_mock.assert_called_once_with(
            'orders',
            key_condition_expression='id = :pk',
            expression_attribute_values={':pk': '101'},
            expression_attribute_names=None,
            filter_expression=None,
            index_name=None,
            limit=10,
            scan_index_forward=True,
            exclusive_start_key=None,
        )

    @patch('dashboard.dynamodb_views.put_item')
    def test_put_item_success(self, put_mock):
        put_mock.return_value = {'table': 'orders', 'item': {'id': '101', 'name': 'Widget'}}

        response = self.client.post(
            reverse('dashboard:dynamodb-item-put', kwargs={'table_name': 'orders'}),
            data=json.dumps({'item': {'id': '101', 'name': 'Widget'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['item']['name'], 'Widget')
        put_mock.assert_called_once_with('orders', {'id': '101', 'name': 'Widget'}, return_values='NONE')

    @patch('dashboard.dynamodb_views.get_item')
    def test_get_item_success(self, get_mock):
        get_mock.return_value = {'table': 'orders', 'item': {'id': '101'}, 'found': True}

        response = self.client.post(
            reverse('dashboard:dynamodb-item-get', kwargs={'table_name': 'orders'}),
            data=json.dumps({'key': {'id': '101'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['found'])
        get_mock.assert_called_once_with('orders', {'id': '101'}, consistent_read=False, projection_expression=None)

    @patch('dashboard.dynamodb_views.delete_item')
    def test_delete_item_success(self, delete_mock):
        delete_mock.return_value = {'table': 'orders', 'key': {'id': '101'}, 'deleted': True}

        response = self.client.post(
            reverse('dashboard:dynamodb-item-delete', kwargs={'table_name': 'orders'}),
            data=json.dumps({'key': {'id': '101'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('orders', {'id': '101'}, return_values='NONE')

    @patch('dashboard.dynamodb_views.update_time_to_live')
    def test_update_ttl_success(self, ttl_mock):
        ttl_mock.return_value = {'table': 'orders', 'time_to_live_specification': {'AttributeName': 'ttl', 'Enabled': True}}

        response = self.client.post(
            reverse('dashboard:dynamodb-ttl', kwargs={'table_name': 'orders'}),
            data=json.dumps({'attribute_name': 'ttl', 'enabled': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['time_to_live_specification']['AttributeName'], 'ttl')
        ttl_mock.assert_called_once_with('orders', attribute_name='ttl', enabled=True)

    @patch('dashboard.dynamodb_views.execute_select_statement')
    def test_partiql_select_success(self, execute_mock):
        execute_mock.return_value = {
            'statement': 'SELECT * FROM "orders"',
            'count': 1,
            'items': [{'pk': '1'}],
        }

        response = self.client.post(
            reverse('dashboard:dynamodb-partiql'),
            data=json.dumps({'statement': 'SELECT * FROM "orders"', 'limit': 5}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['count'], 1)
        execute_mock.assert_called_once_with('SELECT * FROM "orders"', limit=5)

    def test_partiql_rejects_non_select(self):
        with self.assertRaisesMessage(ValueError, 'Only read-only SELECT statements are allowed'):
            execute_select_statement('DELETE FROM "orders"')
