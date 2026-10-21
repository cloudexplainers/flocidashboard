import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse


class ApiGatewayPageTemplateTests(SimpleTestCase):
    def test_apigateway_service_page_keeps_readonly_inventory_and_embeds_console(self):
        response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': 'apigateway'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2>API Gateway inventory</h2>', html=True)
        self.assertContains(response, 'id="apigateway-summary"')
        self.assertContains(response, 'id="apigateway-console-root"')
        self.assertNotContains(response, 'id="apigateway-grid"')
        self.assertContains(response, 'dashboard/apigateway-console.css')
        self.assertContains(response, 'dashboard/service-console.js')
        self.assertContains(response, 'dashboard/apigateway-console.js')


class ApiGatewayRequestsApiTests(SimpleTestCase):
    @patch('dashboard.apigateway_views.create_api')
    def test_create_api_endpoint(self, create_api):
        create_api.return_value = {'api_type': 'http', 'api_id': 'api123'}
        response = self.client.post(reverse('dashboard:apigateway-apis-create'), data=json.dumps({'api_type': 'http', 'name': 'orders', 'description': 'Orders API'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['api_id'], 'api123')
        create_api.assert_called_once_with('http', 'orders', description='Orders API')

    @patch('dashboard.apigateway_views.delete_api')
    def test_delete_api_endpoint(self, delete_api):
        delete_api.return_value = {'api_type': 'rest', 'api_id': 'api123', 'deleted': True}
        response = self.client.post(reverse('dashboard:apigateway-apis-delete'), data=json.dumps({'api_type': 'rest', 'api_id': 'api123'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_api.assert_called_once_with('rest', 'api123')

    @patch('dashboard.apigateway_api.urlopen')
    def test_test_request_success(self, urlopen_mock):
        response_mock = MagicMock()
        response_mock.__enter__.return_value = response_mock
        response_mock.read.return_value = b'{"ok": true}'
        response_mock.getcode.return_value = 200
        response_mock.headers.items.return_value = [('Content-Type', 'application/json')]
        urlopen_mock.return_value = response_mock

        response = self.client.post(
            reverse('dashboard:apigateway-requests-test'),
            data=json.dumps({
                'api_type': 'rest',
                'api_id': 'abc123',
                'stage': 'dev',
                'method': 'POST',
                'path': '/orders',
                'query': {'debug': True},
                'headers': {'X-Test': 'yes'},
                'body': {'order_id': '123'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['status_code'], 200)
        self.assertEqual(payload['json'], {'ok': True})
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertIn('/restapis/abc123/dev/_user_request_/orders?debug=True', request.full_url)

    @patch('dashboard.apigateway_api.FlociClientFactory')
    @patch('dashboard.apigateway_api.urlopen')
    def test_test_request_http_api_uses_local_execute_plane_for_execute_api_endpoint(
        self,
        urlopen_mock,
        factory_mock,
    ):
        factory_mock.return_value.endpoint_url = 'http://localhost:4566'
        response_mock = MagicMock()
        response_mock.__enter__.return_value = response_mock
        response_mock.read.return_value = b'{"ok": true}'
        response_mock.getcode.return_value = 200
        response_mock.headers.items.return_value = [('Content-Type', 'application/json')]
        urlopen_mock.return_value = response_mock

        response = self.client.post(
            reverse('dashboard:apigateway-requests-test'),
            data=json.dumps({
                'api_type': 'http',
                'api_id': 'abc123',
                'endpoint': 'https://abc123.execute-api.us-east-1.amazonaws.com',
                'stage': '$default',
                'method': 'POST',
                'path': '/echo',
                'body': '{"hello":"world"}',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(
            request.full_url,
            'http://localhost:4566/restapis/abc123/$default/_user_request_/echo',
        )

    def test_test_request_rejects_external_http_api_endpoint(self):
        response = self.client.post(
            reverse('dashboard:apigateway-requests-test'),
            data=json.dumps({
                'api_type': 'http',
                'api_id': 'abc123',
                'endpoint': 'https://example.com',
                'method': 'GET',
                'path': '/',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'apigateway')
        self.assertEqual(response.json()['operation'], 'test_request')


class ApiGatewayResponsePresentationTests(SimpleTestCase):
    @patch('dashboard.apigateway_api.monotonic', side_effect=[10.0, 10.0125])
    @patch('dashboard.apigateway_api.urlopen')
    def test_zero_json_body_raw_headers_and_duration_are_preserved(self, urlopen_mock, monotonic_mock):
        from .apigateway_api import test_api_request
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'0'
        response.getcode.return_value = 200
        response.headers.items.return_value = [('content-type', 'application/json')]
        urlopen_mock.return_value = response
        result = test_api_request('rest', 'review', 'GET', '/orders', stage='dev')
        self.assertEqual(result['body'], '0')
        self.assertEqual(result['json'], 0)
        self.assertEqual(result['duration_ms'], 12.5)
        self.assertEqual(result['headers']['content-type'], 'application/json')

    @patch('dashboard.apigateway_api.urlopen')
    def test_http_error_is_a_test_response_with_body_and_headers(self, urlopen_mock):
        from io import BytesIO
        from urllib.error import HTTPError
        from .apigateway_api import test_api_request
        urlopen_mock.side_effect = HTTPError('http://localhost/denied', 403, 'Forbidden', {'x-request-id': 'review'}, BytesIO(b'Denied'))
        result = test_api_request('rest', 'review', 'GET', '/orders', stage='dev')
        self.assertEqual(result['status_code'], 403)
        self.assertEqual(result['body'], 'Denied')
        self.assertEqual(result['headers']['x-request-id'], 'review')
        self.assertIsNone(result['json'])
        self.assertGreaterEqual(result['duration_ms'], 0)
