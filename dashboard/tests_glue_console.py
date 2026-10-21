import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .aws import glue_inventory
from .glue_api import batch_delete_tables, delete_table_column_statistics, update_database
from .services import get_service


class GluePageTemplateTests(SimpleTestCase):

    def test_glue_registry_marks_service_interactive(self):
        service = get_service('glue')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_database' for action in service.actions))
        self.assertTrue(any(action.name == 'update_database' for action in service.actions))
        self.assertTrue(any(action.name == 'create_table' for action in service.actions))
        self.assertTrue(any(action.name == 'batch_delete_tables' for action in service.actions))
        self.assertTrue(any(action.name == 'create_user_defined_function' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_table_column_statistics' for action in service.actions))
        self.assertTrue(any(action.name == 'create_registry' for action in service.actions))
        self.assertTrue(any(action.name == 'register_schema_version' for action in service.actions))


class GlueActionsApiTests(SimpleTestCase):
    @patch('dashboard.glue_views.create_database')
    def test_create_database_success(self, create_mock):
        create_mock.return_value = {'database': 'analytics'}

        response = self.client.post(
            reverse('dashboard:glue-databases'),
            data=json.dumps({
                'name': 'analytics',
                'description': 'Local analytics',
                'location_uri': 's3://my-bucket/',
                'parameters': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['database'], 'analytics')
        create_mock.assert_called_once_with(
            'analytics',
            description='Local analytics',
            location_uri='s3://my-bucket/',
            parameters={'env': 'local'},
        )

    def test_create_database_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:glue-databases'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'glue')
        self.assertEqual(response.json()['operation'], 'create_database')

    @patch('dashboard.glue_views.delete_database')
    def test_delete_database_success(self, delete_mock):
        delete_mock.return_value = {'database': 'analytics'}

        response = self.client.delete(reverse('dashboard:glue-database-detail', kwargs={'database_name': 'analytics'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('analytics')

    @patch('dashboard.glue_views.update_database')
    def test_update_database_success(self, update_mock):
        update_mock.return_value = {'database': 'analytics'}

        response = self.client.put(
            reverse('dashboard:glue-database-detail', kwargs={'database_name': 'analytics'}),
            data=json.dumps({
                'new_name': 'analytics_v2',
                'description': 'Updated',
                'location_uri': 's3://updated/',
                'parameters': {'team': 'data'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(
            'analytics',
            new_name='analytics_v2',
            description='Updated',
            location_uri='s3://updated/',
            parameters={'team': 'data'},
        )

    @patch('dashboard.glue_views.create_table')
    def test_create_table_success(self, create_mock):
        table_input = {'Name': 'orders', 'StorageDescriptor': {'Columns': []}}
        create_mock.return_value = {'database': 'analytics', 'table': 'orders'}

        response = self.client.post(
            reverse('dashboard:glue-tables', kwargs={'database_name': 'analytics'}),
            data=json.dumps({'table_input': table_input}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('analytics', table_input)

    @patch('dashboard.glue_views.delete_table')
    def test_delete_table_success(self, delete_mock):
        delete_mock.return_value = {'database': 'analytics', 'table': 'orders'}

        response = self.client.delete(reverse(
            'dashboard:glue-table-detail',
            kwargs={'database_name': 'analytics', 'table_name': 'orders'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('analytics', 'orders')

    @patch('dashboard.glue_views.batch_delete_tables')
    def test_batch_delete_tables_success(self, batch_delete_mock):
        batch_delete_mock.return_value = {'database': 'analytics', 'tables': ['orders', 'customers']}

        response = self.client.post(
            reverse('dashboard:glue-tables-batch-delete', kwargs={'database_name': 'analytics'}),
            data=json.dumps({'table_names': ['orders', 'customers']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        batch_delete_mock.assert_called_once_with('analytics', ['orders', 'customers'])

    @patch('dashboard.glue_views.create_partition')
    def test_create_partition_success(self, create_mock):
        create_mock.return_value = {'database': 'analytics', 'table': 'orders', 'values': ['2026']}

        response = self.client.post(
            reverse('dashboard:glue-partitions', kwargs={'database_name': 'analytics', 'table_name': 'orders'}),
            data=json.dumps({
                'values': ['2026'],
                'storage_descriptor': {'Location': 's3://my-bucket/orders/year=2026/'},
                'parameters': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            'analytics',
            'orders',
            values=['2026'],
            storage_descriptor={'Location': 's3://my-bucket/orders/year=2026/'},
            parameters={'env': 'local'},
        )

    @patch('dashboard.glue_views.delete_table_column_statistics')
    def test_delete_table_column_statistics_success(self, delete_mock):
        delete_mock.return_value = {'database': 'analytics', 'table': 'orders', 'column': 'amount'}

        response = self.client.delete(reverse(
            'dashboard:glue-table-column-statistics-detail',
            kwargs={'database_name': 'analytics', 'table_name': 'orders', 'column_name': 'amount'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('analytics', 'orders', 'amount')

    @patch('dashboard.glue_views.create_user_defined_function')
    def test_create_user_defined_function_success(self, create_mock):
        function_input = {
            'FunctionName': 'normalize_order',
            'ClassName': 'com.example.NormalizeOrder',
            'OwnerName': 'local',
            'OwnerType': 'USER',
            'ResourceUris': [{'ResourceType': 'JAR', 'Uri': 's3://bucket/udf.jar'}],
        }
        create_mock.return_value = {'database': 'analytics', 'function': 'normalize_order'}

        response = self.client.post(
            reverse('dashboard:glue-functions', kwargs={'database_name': 'analytics'}),
            data=json.dumps({'function_input': function_input}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('analytics', function_input)

    @patch('dashboard.glue_views.delete_user_defined_function')
    def test_delete_user_defined_function_success(self, delete_mock):
        delete_mock.return_value = {'database': 'analytics', 'function': 'normalize_order'}

        response = self.client.delete(reverse(
            'dashboard:glue-function-detail',
            kwargs={'database_name': 'analytics', 'function_name': 'normalize_order'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('analytics', 'normalize_order')

    @patch('dashboard.glue_views.create_registry')
    def test_create_registry_success(self, create_mock):
        create_mock.return_value = {'registry': 'local-registry'}

        response = self.client.post(
            reverse('dashboard:glue-registries'),
            data=json.dumps({
                'registry_name': 'local-registry',
                'description': 'Local schemas',
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            'local-registry',
            description='Local schemas',
            tags={'env': 'local'},
        )

    @patch('dashboard.glue_views.create_schema')
    def test_create_schema_success(self, create_mock):
        definition = '{"type":"record","name":"Order","fields":[{"name":"id","type":"long"}]}'
        create_mock.return_value = {'registry': 'local-registry', 'schema': 'orders'}

        response = self.client.post(
            reverse('dashboard:glue-schemas'),
            data=json.dumps({
                'registry_name': 'local-registry',
                'schema_name': 'orders',
                'data_format': 'AVRO',
                'compatibility': 'BACKWARD',
                'schema_definition': definition,
                'description': 'Order events',
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            'local-registry',
            'orders',
            data_format='AVRO',
            compatibility='BACKWARD',
            schema_definition=definition,
            description='Order events',
            tags={'env': 'local'},
        )

    @patch('dashboard.glue_views.register_schema_version')
    def test_register_schema_version_success(self, register_mock):
        definition = '{"type":"record","name":"Order","fields":[{"name":"id","type":"long"}]}'
        register_mock.return_value = {'registry': 'local-registry', 'schema': 'orders'}

        response = self.client.post(
            reverse('dashboard:glue-schema-versions'),
            data=json.dumps({
                'registry_name': 'local-registry',
                'schema_name': 'orders',
                'schema_definition': definition,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        register_mock.assert_called_once_with('local-registry', 'orders', definition)


class GlueInventoryTests(SimpleTestCase):
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_surfaces_table_versions_and_database_tags(self, factory_mock, paginate_mock):
        client = MagicMock()
        client.meta.service_model.operation_names = ['GetColumnStatisticsForTable', 'GetTableVersions', 'GetTags']
        client.get_database.return_value = {'Database': {'DatabaseArn': 'arn:aws:glue:us-east-1:000000000000:database/analytics'}}
        client.get_tags.return_value = {'Tags': {'env': 'local'}}
        client.get_column_statistics_for_table.return_value = {
            'ColumnStatisticsList': [{'ColumnName': 'amount', 'ColumnType': 'double'}],
        }
        factory_mock.return_value.client.return_value = client

        def paginate(_client, operation, _result_key, **kwargs):
            if operation == 'get_databases':
                return [{'Name': 'analytics', 'DatabaseArn': 'arn:aws:glue:us-east-1:000000000000:database/analytics'}]
            if operation == 'get_tables':
                return [{'Name': 'orders', 'StorageDescriptor': {'Columns': [{'Name': 'amount', 'Type': 'double'}]}}]
            if operation == 'get_table_versions':
                return [{'VersionId': '1'}, {'VersionId': '2'}]
            if operation == 'get_partitions':
                return []
            if operation == 'get_user_defined_functions':
                return []
            if operation == 'list_registries':
                return []
            return []

        paginate_mock.side_effect = paginate

        result = glue_inventory()

        self.assertEqual(result['summary']['table_versions'], 2)
        self.assertEqual(result['summary']['column_statistics'], 1)
        self.assertEqual(result['databases'][0]['tags'], {'env': 'local'})
        self.assertEqual(result['databases'][0]['tables'][0]['version_count'], 2)
        self.assertEqual(result['databases'][0]['tables'][0]['column_statistics_count'], 1)
        client.get_column_statistics_for_table.assert_called_once_with(
            DatabaseName='analytics',
            TableName='orders',
            ColumnNames=['amount'],
        )


class GlueApiHelperTests(SimpleTestCase):
    @patch('dashboard.glue_api._client')
    def test_update_database_uses_database_input(self, client_mock):
        client = MagicMock()
        client.update_database.return_value = {}
        client_mock.return_value = client

        result = update_database(
            'analytics',
            new_name='analytics_v2',
            description='Updated',
            location_uri='s3://updated/',
            parameters={'team': 'data'},
        )

        self.assertEqual(result['database'], 'analytics')
        client.update_database.assert_called_once_with(
            Name='analytics',
            DatabaseInput={
                'Name': 'analytics_v2',
                'Description': 'Updated',
                'LocationUri': 's3://updated/',
                'Parameters': {'team': 'data'},
            },
        )

    @patch('dashboard.glue_api._client')
    def test_batch_delete_tables_uses_batch_api(self, client_mock):
        client = MagicMock()
        client.batch_delete_table.return_value = {}
        client_mock.return_value = client

        result = batch_delete_tables('analytics', ['orders', 'customers'])

        self.assertEqual(result['tables'], ['orders', 'customers'])
        client.batch_delete_table.assert_called_once_with(
            DatabaseName='analytics',
            TablesToDelete=['orders', 'customers'],
        )

    @patch('dashboard.glue_api._client')
    def test_delete_table_column_statistics_uses_delete_api(self, client_mock):
        client = MagicMock()
        client.delete_column_statistics_for_table.return_value = {}
        client_mock.return_value = client

        result = delete_table_column_statistics('analytics', 'orders', 'amount')

        self.assertEqual(result['column'], 'amount')
        client.delete_column_statistics_for_table.assert_called_once_with(
            DatabaseName='analytics',
            TableName='orders',
            ColumnName='amount',
        )
