import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .lambda_api import create_function, save_alias, save_event_source_mapping, set_concurrency
from .services import get_service


class LambdaPageTemplateTests(SimpleTestCase):


    def test_lambda_registry_exposes_management_lifecycle(self):
        actions = {action.name for action in get_service('lambda').actions}
        self.assertTrue({'create_function', 'update_function_configuration', 'update_function_code', 'delete_function', 'publish_version', 'create_alias', 'create_event_source_mapping', 'create_function_url_config', 'put_function_concurrency', 'add_permission', 'tag_resource'} <= actions)


class LambdaFunctionsApiTests(SimpleTestCase):
    def test_management_action_routes_resolve(self):
        self.assertEqual(reverse('dashboard:lambda-function-code', kwargs={'function_name': 'worker'}), '/api/lambda/functions/worker/code/')
        self.assertEqual(reverse('dashboard:lambda-function-versions', kwargs={'function_name': 'worker'}), '/api/lambda/functions/worker/versions/')
        self.assertEqual(reverse('dashboard:lambda-function-mappings', kwargs={'function_name': 'worker'}), '/api/lambda/functions/worker/event-source-mappings/')
        self.assertEqual(reverse('dashboard:lambda-function-url', kwargs={'function_name': 'worker'}), '/api/lambda/functions/worker/url/')
        self.assertEqual(reverse('dashboard:lambda-function-permissions', kwargs={'function_name': 'worker'}), '/api/lambda/functions/worker/permissions/')
        self.assertEqual(reverse('dashboard:lambda-function-tags', kwargs={'function_name': 'worker'}), '/api/lambda/functions/worker/tags/')

    @patch('dashboard.lambda_api._lambda_client')
    def test_detail_routes_list_versions_aliases_triggers_and_url(self, client_factory):
        from botocore.exceptions import ClientError

        client = MagicMock()
        client_factory.return_value = client
        pages = {
            'list_versions_by_function': [{'Versions': [{'Version': '$LATEST'}, {'Version': '1'}]}],
            'list_aliases': [{'Aliases': [{'Name': 'prod'}]}],
            'list_event_source_mappings': [{'EventSourceMappings': [{'UUID': 'abc'}]}],
        }
        client.get_paginator.side_effect = lambda op: MagicMock(paginate=MagicMock(return_value=pages[op]))
        client.get_function_url_config.side_effect = ClientError(
            {'Error': {'Code': 'ResourceNotFoundException', 'Message': 'no url'}}, 'GetFunctionUrlConfig')

        def get(name):
            return self.client.get(reverse(name, kwargs={'function_name': 'worker'}))

        self.assertEqual(get('dashboard:lambda-function-versions').json()['Versions'][1]['Version'], '1')
        self.assertEqual(get('dashboard:lambda-function-aliases').json()['Aliases'], [{'Name': 'prod'}])
        self.assertEqual(get('dashboard:lambda-function-mappings').json()['EventSourceMappings'], [{'UUID': 'abc'}])
        url_response = get('dashboard:lambda-function-url')
        self.assertEqual(url_response.status_code, 200)
        self.assertIsNone(url_response.json())

        client.get_function_url_config.side_effect = None
        client.get_function_url_config.return_value = {'FunctionUrl': 'http://x/', 'ResponseMetadata': {}}
        self.assertEqual(get('dashboard:lambda-function-url').json(), {'FunctionUrl': 'http://x/'})

    @patch('dashboard.lambda_views.create_function')
    def test_create_function_success(self, create_mock):
        create_mock.return_value = {'function_name': 'worker', 'state': 'Active'}
        response = self.client.post(reverse('dashboard:lambda-functions'), data=json.dumps({
            'name': 'worker', 'role': 'arn:role', 'code': {'S3Bucket': 'code', 'S3Key': 'worker.zip'},
            'configuration': {'Runtime': 'python3.13', 'Handler': 'worker.handler'}, 'tags': {'env': 'local'},
        }), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('worker', 'arn:role', {'S3Bucket': 'code', 'S3Key': 'worker.zip'}, configuration={'Runtime': 'python3.13', 'Handler': 'worker.handler'}, tags={'env': 'local'})

    @patch('dashboard.lambda_views.update_function_configuration')
    def test_update_configuration_success(self, update_mock):
        update_mock.return_value = {'function_name': 'worker'}
        response = self.client.patch(reverse('dashboard:lambda-function-detail', kwargs={'function_name': 'worker'}), data=json.dumps({'configuration': {'MemorySize': 256}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with('worker', {'MemorySize': 256})

    @patch('dashboard.lambda_views.save_alias')
    def test_create_alias_success(self, save_mock):
        save_mock.return_value = {'alias': 'prod', 'version': '2'}
        response = self.client.post(reverse('dashboard:lambda-function-aliases', kwargs={'function_name': 'worker'}), data=json.dumps({'name': 'prod', 'function_version': '2'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        save_mock.assert_called_once_with('worker', 'prod', '2', description='', update=False)

    @patch('dashboard.lambda_views.set_concurrency')
    def test_clear_concurrency_success(self, concurrency_mock):
        concurrency_mock.return_value = {'function_name': 'worker', 'reserved_concurrency': None}
        response = self.client.delete(reverse('dashboard:lambda-function-concurrency', kwargs={'function_name': 'worker'}))
        self.assertEqual(response.status_code, 200)
        concurrency_mock.assert_called_once_with('worker', None)

    def test_invoke_function_rejects_invalid_payload(self):
        response = self.client.post(
            reverse('dashboard:lambda-functions-invoke', kwargs={'function_name': 'worker'}),
            data=json.dumps({'payload': '{bad json'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'lambda')
        self.assertEqual(response.json()['operation'], 'invoke_function')

    @patch('dashboard.lambda_views.invoke_function')
    def test_invoke_function_success(self, invoke_mock):
        invoke_mock.return_value = {
            'function_name': 'worker',
            'status_code': 200,
            'payload': {'json': {'ok': True}, 'raw': '{"ok":true}'},
        }

        response = self.client.post(
            reverse('dashboard:lambda-functions-invoke', kwargs={'function_name': 'worker'}),
            data=json.dumps({'payload': {'hello': 'world'}, 'qualifier': 'prod'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status_code'], 200)
        invoke_mock.assert_called_once_with(
            'worker',
            {'hello': 'world'},
            qualifier='prod',
            invocation_type='RequestResponse',
        )

    def test_get_event_templates_endpoint(self):
        response = self.client.get(reverse('dashboard:lambda-test-event-templates'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('apigateway_v2_http', data)
        self.assertIn('sqs_standard', data)
        self.assertIn('sns_notification', data)
        self.assertIn('dynamodb_streams', data)
        self.assertIn('s3_put_object', data)

    @patch('dashboard.lambda_views.invoke_function_url')
    def test_function_url_test_endpoint_success(self, url_mock):
        url_mock.return_value = {
            'url': 'http://localhost:4566/functions/worker',
            'status_code': 200,
            'body': '{"ok": true}',
            'latency_ms': 12.5,
        }

        response = self.client.post(
            reverse('dashboard:lambda-function-url-test'),
            data=json.dumps({
                'url': 'http://localhost:4566/functions/worker',
                'method': 'POST',
                'body': '{"test": 1}',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status_code'], 200)
        url_mock.assert_called_once_with(
            url='http://localhost:4566/functions/worker',
            method='POST',
            headers=None,
            body='{"test": 1}',
            query_params=None,
        )


class LambdaApiHelperTests(SimpleTestCase):
    @patch('dashboard.lambda_api._lambda_client')
    def test_create_function_preserves_aws_request_shape(self, client_mock):
        client = MagicMock()
        client.create_function.return_value = {'FunctionArn': 'arn:function:worker', 'State': 'Active'}
        client_mock.return_value = client
        result = create_function('worker', 'arn:role', {'S3Bucket': 'code', 'S3Key': 'worker.zip'}, configuration={'Runtime': 'python3.13', 'Handler': 'worker.handler'}, tags={'env': 'local'})
        self.assertEqual(result['state'], 'Active')
        client.create_function.assert_called_once_with(FunctionName='worker', Role='arn:role', Code={'S3Bucket': 'code', 'S3Key': 'worker.zip'}, Runtime='python3.13', Handler='worker.handler', Tags={'env': 'local'})

    @patch('dashboard.lambda_api._lambda_client')
    def test_alias_update_uses_update_alias(self, client_mock):
        client = MagicMock()
        client.update_alias.return_value = {'FunctionVersion': '3'}
        client_mock.return_value = client
        save_alias('worker', 'prod', '3', update=True)
        client.update_alias.assert_called_once_with(FunctionName='worker', Name='prod', FunctionVersion='3')

    @patch('dashboard.lambda_api._lambda_client')
    def test_mapping_update_uses_uuid_without_function_name(self, client_mock):
        client = MagicMock()
        client.update_event_source_mapping.return_value = {'UUID': 'mapping-1', 'State': 'Enabled'}
        client_mock.return_value = client
        save_event_source_mapping('', {'BatchSize': 5}, uuid='mapping-1')
        client.update_event_source_mapping.assert_called_once_with(UUID='mapping-1', BatchSize=5)

    @patch('dashboard.lambda_api._lambda_client')
    def test_concurrency_accepts_number_string(self, client_mock):
        client = MagicMock()
        client.put_function_concurrency.return_value = {'ReservedConcurrentExecutions': 5}
        client_mock.return_value = client
        result = set_concurrency('worker', '5')
        self.assertEqual(result['reserved_concurrency'], 5)
        client.put_function_concurrency.assert_called_once_with(FunctionName='worker', ReservedConcurrentExecutions=5)
