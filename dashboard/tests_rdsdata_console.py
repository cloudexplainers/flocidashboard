import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .rdsdata_api import _format_field_value, _format_records


class RDSDataApiTests(SimpleTestCase):
    def test_format_field_value_handles_data_types(self):
        self.assertEqual(_format_field_value({'stringValue': 'Alice'}), 'Alice')
        self.assertEqual(_format_field_value({'longValue': 42}), 42)
        self.assertEqual(_format_field_value({'doubleValue': 3.14}), 3.14)
        self.assertEqual(_format_field_value({'booleanValue': True}), True)
        self.assertIsNone(_format_field_value({'isNull': True}))

    def test_format_records_maps_columns(self):
        col_meta = [{'name': 'id'}, {'name': 'name'}]
        records = [[{'longValue': 1}, {'stringValue': 'Alice'}]]
        formatted = _format_records(records, col_meta)
        self.assertEqual(formatted, [{'id': 1, 'name': 'Alice'}])

    @patch('dashboard.rdsdata_views.execute_statement')
    def test_execute_statement_endpoint_success(self, exec_mock):
        exec_mock.return_value = {
            'sql': 'SELECT 1;',
            'columns': ['col_1'],
            'records': [{'col_1': 1}],
            'row_count': 1,
            'number_of_records_updated': 0,
        }

        response = self.client.post(
            reverse('dashboard:rdsdata-execute'),
            data=json.dumps({
                'resource_arn': 'arn:aws:rds:us-east-1:000000000000:cluster:floci-db',
                'secret_arn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:db-cred',
                'sql': 'SELECT 1;',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['row_count'], 1)
        exec_mock.assert_called_once_with(
            resource_arn='arn:aws:rds:us-east-1:000000000000:cluster:floci-db',
            secret_arn='arn:aws:secretsmanager:us-east-1:000000000000:secret:db-cred',
            sql='SELECT 1;',
            database=None,
            schema=None,
            parameters=None,
            transaction_id=None,
            include_result_metadata=True,
        )

    @patch('dashboard.rdsdata_views.begin_transaction')
    def test_begin_transaction_endpoint_success(self, begin_mock):
        begin_mock.return_value = {'transaction_id': 'tx-12345'}

        response = self.client.post(
            reverse('dashboard:rdsdata-transaction-begin'),
            data=json.dumps({
                'resource_arn': 'arn:aws:rds:us-east-1:000000000000:cluster:floci-db',
                'secret_arn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:db-cred',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['transaction_id'], 'tx-12345')

    @patch('dashboard.rdsdata_views.batch_execute_statement')
    def test_batch_execute_statement_endpoint_success(self, batch_mock):
        batch_mock.return_value = {'sql': 'INSERT INTO users VALUES (1);', 'update_results': []}

        response = self.client.post(
            reverse('dashboard:rdsdata-batch-execute'),
            data=json.dumps({
                'resource_arn': 'arn:aws:rds:us-east-1:000000000000:cluster:floci-db',
                'secret_arn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:db-cred',
                'sql': 'INSERT INTO users VALUES (1);',
                'parameter_sets': [],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['sql'], 'INSERT INTO users VALUES (1);')

    @patch('dashboard.rdsdata_views.commit_transaction')
    def test_commit_transaction_endpoint_success(self, commit_mock):
        commit_mock.return_value = {'transaction_id': 'tx-123', 'transaction_status': 'Transaction Committed'}

        response = self.client.post(
            reverse('dashboard:rdsdata-transaction-commit'),
            data=json.dumps({
                'resource_arn': 'arn:aws:rds:us-east-1:000000000000:cluster:floci-db',
                'secret_arn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:db-cred',
                'transaction_id': 'tx-123',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['transaction_status'], 'Transaction Committed')

    @patch('dashboard.rdsdata_views.rollback_transaction')
    def test_rollback_transaction_endpoint_success(self, rollback_mock):
        rollback_mock.return_value = {'transaction_id': 'tx-123', 'transaction_status': 'Rollback Complete'}

        response = self.client.post(
            reverse('dashboard:rdsdata-transaction-rollback'),
            data=json.dumps({
                'resource_arn': 'arn:aws:rds:us-east-1:000000000000:cluster:floci-db',
                'secret_arn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:db-cred',
                'transaction_id': 'tx-123',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['transaction_status'], 'Rollback Complete')
