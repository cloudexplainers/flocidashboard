import json
from unittest.mock import MagicMock
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .cloudmap_api import create_namespace, register_instance
from .services import get_service


class CloudMapInventoryTests(SimpleTestCase):

    def test_cloudmap_registry_marks_service_interactive(self):
        service = get_service('cloudmap')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertEqual(service.api_path, None)
        self.assertEqual(service.docs_url, 'https://floci.io/floci/services/cloudmap/')
        self.assertTrue(any(action.name == 'create_namespace' for action in service.actions))
        self.assertTrue(any(action.name == 'register_instance' for action in service.actions))
        self.assertTrue(any(action.name == 'discover_instances' for action in service.actions))

    @patch('dashboard.views.cloudmap_inventory')
    def test_cloudmap_api_returns_inventory(self, inventory):
        inventory.return_value = {
            'summary': {'namespaces': 1, 'services': 1, 'instances': 1, 'operations': 0},
            'namespaces': [{'name': 'local.test', 'id': 'ns-1'}],
            'services': [{'name': 'api', 'id': 'srv-1'}],
            'instances': [{'id': 'instance-1', 'service_id': 'srv-1'}],
            'operations': [],
        }

        response = self.client.get(reverse('dashboard:cloudmap'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['namespaces'], 1)


class CloudMapActionsTests(SimpleTestCase):
    @patch('dashboard.cloudmap_views.create_namespace')
    def test_create_namespace(self, create_mock):
        create_mock.return_value = {'operation_id': 'op-1'}

        response = self.client.post(
            reverse('dashboard:cloudmap-namespaces'),
            data=json.dumps({'name': 'local.test', 'namespace_type': 'HTTP', 'description': 'Local', 'tags': {'env': 'local'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(name='local.test', namespace_type='HTTP', description='Local', vpc='', tags={'env': 'local'})

    @patch('dashboard.cloudmap_views.create_service')
    def test_create_service(self, create_mock):
        create_mock.return_value = {'service_id': 'srv-1'}

        response = self.client.post(
            reverse('dashboard:cloudmap-services'),
            data=json.dumps({'name': 'api', 'namespace_id': 'ns-1', 'health_check_custom_config': {'FailureThreshold': 1}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            name='api',
            namespace_id='ns-1',
            description='',
            dns_config={},
            health_check_config={},
            health_check_custom_config={'FailureThreshold': 1},
            tags={},
        )

    @patch('dashboard.cloudmap_views.register_instance')
    def test_register_instance(self, register_mock):
        register_mock.return_value = {'operation_id': 'op-1'}

        response = self.client.post(
            reverse('dashboard:cloudmap-instances', kwargs={'service_id': 'srv-1'}),
            data=json.dumps({'instance_id': 'instance-1', 'attributes': {'AWS_INSTANCE_IPV4': '127.0.0.1'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        register_mock.assert_called_once_with('srv-1', 'instance-1', {'AWS_INSTANCE_IPV4': '127.0.0.1'})

    @patch('dashboard.cloudmap_views.discover_instances')
    def test_discover_instances(self, discover_mock):
        discover_mock.return_value = {'instances': []}

        response = self.client.post(
            reverse('dashboard:cloudmap-discover'),
            data=json.dumps({'namespace_name': 'local.test', 'service_name': 'api', 'query_parameters': {'stage': 'local'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        discover_mock.assert_called_once_with('local.test', 'api', query_parameters={'stage': 'local'})

    @patch('dashboard.cloudmap_views.delete_namespace')
    def test_delete_namespace(self, delete_mock):
        delete_mock.return_value = {'operation_id': 'op-delete-namespace'}

        response = self.client.delete(reverse('dashboard:cloudmap-namespace-detail', kwargs={'namespace_id': 'ns-1'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('ns-1')

    @patch('dashboard.cloudmap_views.delete_service')
    def test_delete_service(self, delete_mock):
        delete_mock.return_value = {'operation_id': 'op-delete-service'}

        response = self.client.delete(reverse('dashboard:cloudmap-service-detail', kwargs={'service_id': 'srv-1'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('srv-1')

    @patch('dashboard.cloudmap_views.deregister_instance')
    def test_deregister_instance(self, deregister_mock):
        deregister_mock.return_value = {'operation_id': 'op-deregister-instance'}

        response = self.client.delete(
            reverse('dashboard:cloudmap-instance-detail', kwargs={'service_id': 'srv-1', 'instance_id': 'instance-1'})
        )

        self.assertEqual(response.status_code, 200)
        deregister_mock.assert_called_once_with('srv-1', 'instance-1')

    @patch('dashboard.cloudmap_views.tag_resource')
    def test_tag_resource(self, tag_mock):
        tag_mock.return_value = {'tags': {'env': 'local'}}

        response = self.client.post(
            reverse('dashboard:cloudmap-tags'),
            data=json.dumps({'resource_arn': 'arn:aws:servicediscovery:us-east-1:000000000000:service/srv-1', 'tags': {'env': 'local'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        tag_mock.assert_called_once_with('arn:aws:servicediscovery:us-east-1:000000000000:service/srv-1', {'env': 'local'})

    @patch('dashboard.cloudmap_views.untag_resource')
    def test_untag_resource(self, untag_mock):
        untag_mock.return_value = {'tag_keys': ['env']}

        response = self.client.delete(
            reverse('dashboard:cloudmap-tags'),
            data=json.dumps({'resource_arn': 'arn:aws:servicediscovery:us-east-1:000000000000:service/srv-1', 'tag_keys': ['env']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        untag_mock.assert_called_once_with('arn:aws:servicediscovery:us-east-1:000000000000:service/srv-1', ['env'])

    def test_create_namespace_rejects_invalid_json(self):
        response = self.client.post(reverse('dashboard:cloudmap-namespaces'), data='bad', content_type='application/json')

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['operation'], 'create_namespace')


class CloudMapApiHelperTests(SimpleTestCase):
    @patch('dashboard.cloudmap_api._client')
    def test_create_http_namespace_uses_management_api(self, client_mock):
        client = MagicMock()
        client.create_http_namespace.return_value = {'OperationId': 'op-1'}
        client_mock.return_value = client

        result = create_namespace(name='local.test', tags={'env': 'local'})

        self.assertEqual(result['operation_id'], 'op-1')
        client.create_http_namespace.assert_called_once()
        kwargs = client.create_http_namespace.call_args.kwargs
        self.assertEqual(kwargs['Name'], 'local.test')
        self.assertEqual(kwargs['Tags'], [{'Key': 'env', 'Value': 'local'}])

    @patch('dashboard.cloudmap_api._client')
    def test_register_instance_uses_attributes(self, client_mock):
        client = MagicMock()
        client.register_instance.return_value = {'OperationId': 'op-1'}
        client_mock.return_value = client

        result = register_instance('srv-1', 'instance-1', {'AWS_INSTANCE_IPV4': '127.0.0.1', 'port': 8080})

        self.assertEqual(result['operation_id'], 'op-1')
        client.register_instance.assert_called_once()
        kwargs = client.register_instance.call_args.kwargs
        self.assertEqual(kwargs['ServiceId'], 'srv-1')
        self.assertEqual(kwargs['InstanceId'], 'instance-1')
        self.assertEqual(kwargs['Attributes']['port'], '8080')
