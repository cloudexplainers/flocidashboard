import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class AppSyncConsoleTests(SimpleTestCase):

    def test_appsync_registry_actions(self):
        actions = {action.name for action in get_service('appsync').actions}
        self.assertTrue({'create_graphql_api', 'delete_graphql_api', 'start_schema_creation', 'create_api_key', 'delete_api_key', 'create_data_source', 'delete_data_source', 'create_resolver', 'delete_resolver', 'execute_graphql'} <= actions)

    @patch('dashboard.appsync_views.execute_graphql')
    def test_graphql_run_endpoint_success(self, run_mock):
        run_mock.return_value = {
            'api_id': 'api-123',
            'status_code': 200,
            'latency_ms': 15.2,
            'data': {'hello': 'world'},
            'errors': None,
        }

        response = self.client.post(
            reverse('dashboard:appsync-graphql-run'),
            data=json.dumps({
                'api_id': 'api-123',
                'query': 'query { hello }',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data'], {'hello': 'world'})
        run_mock.assert_called_once_with(
            api_id='api-123',
            query='query { hello }',
            variables=None,
            operation_name=None,
            api_key=None,
            endpoint_url=None,
        )


class AppSyncWorkspaceTests(SimpleTestCase):
    @patch('dashboard.appsync_api._client')
    def test_schema_read_returns_actual_sdl_and_status(self, client_mock):
        from io import BytesIO
        client = client_mock.return_value
        stream = BytesIO(b'type Query { hello: String }')
        client.get_schema_creation_status.return_value = {'status': 'SUCCESS'}
        client.get_introspection_schema.return_value = {'schema': stream}
        response = self.client.get('/api/appsync/apis/orders/schema/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['definition'], 'type Query { hello: String }')
        self.assertEqual(response.json()['status'], 'SUCCESS')
        client.get_introspection_schema.assert_called_once_with(apiId='orders', format='SDL')
        self.assertTrue(stream.closed)

    @patch('dashboard.appsync_api._client')
    def test_processing_and_failed_schemas_do_not_claim_deployed_sdl(self, client_mock):
        client = client_mock.return_value
        for status in ('PROCESSING', 'FAILED', 'NOT_APPLICABLE'):
            with self.subTest(status=status):
                client.get_schema_creation_status.return_value = {'status': status, 'details': 'Reported by service'}
                response = self.client.get('/api/appsync/apis/orders/schema/')
                self.assertEqual(response.json(), {'api_id': 'orders', 'status': status, 'details': 'Reported by service'})
        client.get_introspection_schema.assert_not_called()

    @patch('dashboard.appsync_api._client')
    def test_dynamodb_configuration_and_role_reach_sdk(self, client_mock):
        client = client_mock.return_value
        client.create_data_source.return_value = {'dataSource': {'name': 'orders'}}
        response = self.client.post('/api/appsync/apis/orders/data-sources/', data=json.dumps({
            'name': 'orders', 'source_type': 'AMAZON_DYNAMODB',
            'service_role_arn': 'arn:aws:iam::123456789012:role/appsync',
            'config': {'tableName': 'Orders', 'awsRegion': 'eu-west-1', 'useCallerCredentials': False},
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.create_data_source.assert_called_once_with(apiId='orders', name='orders', type='AMAZON_DYNAMODB', serviceRoleArn='arn:aws:iam::123456789012:role/appsync', dynamodbConfig={'tableName': 'Orders', 'awsRegion': 'eu-west-1', 'useCallerCredentials': False})

    @patch('dashboard.appsync_api._client')
    def test_resolver_mapping_templates_reach_sdk(self, client_mock):
        client = client_mock.return_value
        client.create_resolver.return_value = {'resolver': {'fieldName': 'hello'}}
        request_template = '{"version":"2018-05-29","payload":{"value":0}}'
        response_template = '$util.toJson($ctx.result.value)'
        response = self.client.post('/api/appsync/apis/orders/resolvers/', data=json.dumps({
            'type_name': 'Query', 'field_name': 'hello', 'data_source_name': 'local',
            'request_mapping_template': request_template, 'response_mapping_template': response_template,
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.create_resolver.assert_called_once_with(apiId='orders', typeName='Query', fieldName='hello', dataSourceName='local', requestMappingTemplate=request_template, responseMappingTemplate=response_template)

    @patch('urllib.request.urlopen')
    def test_query_preserves_partial_data_errors_and_zero_variables(self, urlopen_mock):
        from .appsync_api import execute_graphql
        upstream = urlopen_mock.return_value.__enter__.return_value
        upstream.status = 200
        upstream.read.return_value = b'{"data":{"count":0,"hello":null},"errors":[{"message":"Resolver failed","path":["hello"]}]}'
        result = execute_graphql('orders', 'query Test { count hello }', variables={'count': 0}, operation_name='Test', api_key='real-key', endpoint_url='http://localhost:4566/graphql/orders')
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(json.loads(request.data)['variables'], {'count': 0})
        self.assertEqual(json.loads(request.data)['operationName'], 'Test')
        self.assertEqual(request.get_header('X-api-key'), 'real-key')
        self.assertEqual(result['data'], {'count': 0, 'hello': None})
        self.assertEqual(result['errors'][0]['path'], ['hello'])
        self.assertEqual(result['endpoint_url'], 'http://localhost:4566/graphql/orders')

    @patch('urllib.request.urlopen')
    def test_query_rejects_nonobject_variables_before_request(self, urlopen_mock):
        from .appsync_api import execute_graphql
        for value in ([], '[]', 'false', 0):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Variables must be a JSON object'):
                execute_graphql('orders', '{ hello }', variables=value, endpoint_url='http://localhost:4566/graphql/orders')
        urlopen_mock.assert_not_called()

    @patch('dashboard.appsync_api._client')
    def test_schema_read_unwraps_floci_json_without_changing_raw_sdl(self, client_mock):
        from io import BytesIO
        client = client_mock.return_value
        client.get_schema_creation_status.return_value = {'status': 'SUCCESS'}
        client.get_introspection_schema.return_value = {'schema': BytesIO(b'{"schema":{"definition":"type Root { hello: String }"}}')}
        response = self.client.get('/api/appsync/apis/orders/schema/')
        self.assertEqual(response.json()['definition'], 'type Root { hello: String }')

    @patch('dashboard.appsync_api._client')
    def test_new_api_without_schema_does_not_report_a_retrieval_error(self, client_mock):
        from botocore.exceptions import ClientError
        client = client_mock.return_value
        client.get_schema_creation_status.side_effect = ClientError({'Error': {'Code': 'NotFoundException', 'Message': 'No schema creation status'}}, 'GetSchemaCreationStatus')
        client.get_graphql_api.return_value = {'graphqlApi': {'apiId': 'orders'}}
        response = self.client.get('/api/appsync/apis/orders/schema/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['status'])
        self.assertNotIn('definition', response.json())
        client.get_graphql_api.assert_called_once_with(apiId='orders')

    def test_type_discovery_ignores_comments_and_descriptions(self):
        from .appsync_schema import resolver_type_names
        self.assertEqual(resolver_type_names('''
          # type Comment { fake: String }
          """A description containing type Example and interface Wrong"""
          type RootQuery @tag(text: "type AlsoWrong") { hello: String }
          interface Node { id: ID! }
          extend type RootQuery { more: String }
        '''), ['RootQuery', 'Node', 'RootQuery'])

    @patch('urllib.request.urlopen')
    @patch('dashboard.appsync_api.FlociClientFactory')
    def test_query_uses_floci_graphql_execution_route(self, factory_mock, urlopen_mock):
        from .appsync_api import execute_graphql
        factory_mock.return_value.endpoint_url = 'http://localhost:4566'
        upstream = urlopen_mock.return_value.__enter__.return_value
        upstream.status = 200
        upstream.read.return_value = b'{"data":{"hello":"world"}}'
        result = execute_graphql('orders', '{ hello }')
        self.assertEqual(urlopen_mock.call_args.args[0].full_url, 'http://localhost:4566/v1/apis/orders/graphql')
        self.assertEqual(result['endpoint_url'], 'http://localhost:4566/v1/apis/orders/graphql')

    @patch('dashboard.aws.FlociClientFactory')
    @patch('dashboard.aws._operation_items')
    def test_resolvers_are_discovered_from_sdl_when_type_registry_is_empty(self, items_mock, factory_mock):
        from io import BytesIO
        from .aws import appsync_inventory
        client = factory_mock.return_value.client.return_value
        client.get_schema_creation_status.return_value = {'status': 'SUCCESS'}
        client.get_introspection_schema.return_value = {'schema': BytesIO(b'{"schema":{"definition":"type RootQuery { hello: String }"}}')}
        def items(_client, operation, _result_key, **kwargs):
            if operation == 'list_graphql_apis':
                return [{'apiId': 'orders', 'name': 'Orders'}]
            if operation == 'list_resolvers':
                self.assertEqual(kwargs['typeName'], 'RootQuery')
                return [{'fieldName': 'hello', 'dataSourceName': 'local'}]
            return []
        items_mock.side_effect = items
        result = appsync_inventory()
        self.assertEqual(result['graphql_apis'][0]['resolvers'][0]['typeName'], 'RootQuery')
        self.assertEqual(result['summary']['resolvers'], 1)
