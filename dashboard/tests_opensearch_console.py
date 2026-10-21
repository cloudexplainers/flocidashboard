import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class OpenSearchPageTemplateTests(SimpleTestCase):

    def test_opensearch_registry_marks_service_interactive(self):
        service = get_service('opensearch')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_domain' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_domain' for action in service.actions))
        self.assertTrue(any(action.name == 'list_versions' for action in service.actions))


class OpenSearchActionsApiTests(SimpleTestCase):
    @patch('dashboard.opensearch_views.create_domain')
    def test_create_domain_success(self, create_mock):
        create_mock.return_value = {'domain_name': 'my-search', 'created': True}

        response = self.client.post(
            reverse('dashboard:opensearch-domains'),
            data=json.dumps({
                'domain_name': 'my-search',
                'engine_version': 'OpenSearch_2.19',
                'instance_type': 'm5.large.search',
                'instance_count': 1,
                'ebs_enabled': True,
                'volume_type': 'gp2',
                'volume_size': 10,
                'tags': [{'Key': 'env', 'Value': 'local'}],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['domain_name'], 'my-search')
        create_mock.assert_called_once_with(
            domain_name='my-search',
            engine_version='OpenSearch_2.19',
            instance_type='m5.large.search',
            instance_count=1,
            ebs_enabled=True,
            volume_type='gp2',
            volume_size=10,
            tags=[{'Key': 'env', 'Value': 'local'}],
        )

    def test_create_domain_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:opensearch-domains'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'opensearch')
        self.assertEqual(response.json()['operation'], 'create_domain')

    @patch('dashboard.opensearch_views.update_domain_config')
    def test_update_domain_config_success(self, update_mock):
        update_mock.return_value = {'domain_name': 'my-search'}

        response = self.client.post(
            reverse('dashboard:opensearch-domains-update'),
            data=json.dumps({
                'domain_name': 'my-search',
                'cluster_config': {'InstanceCount': 3},
                'ebs_options': {'VolumeSize': 20},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(
            domain_name='my-search',
            engine_version='',
            cluster_config={'InstanceCount': 3},
            ebs_options={'VolumeSize': 20},
        )

    @patch('dashboard.opensearch_views.delete_domain')
    def test_delete_domain_success(self, delete_mock):
        delete_mock.return_value = {'domain_name': 'my-search', 'deleted': True}

        response = self.client.post(
            reverse('dashboard:opensearch-domains-delete'),
            data=json.dumps({'domain_name': 'my-search'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('my-search')

    @patch('dashboard.opensearch_views.upgrade_domain')
    def test_upgrade_domain_success(self, upgrade_mock):
        upgrade_mock.return_value = {'domain_name': 'my-search', 'target_version': 'OpenSearch_3.0'}

        response = self.client.post(
            reverse('dashboard:opensearch-domains-upgrade'),
            data=json.dumps({
                'domain_name': 'my-search',
                'target_version': 'OpenSearch_3.0',
                'perform_check_only': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        upgrade_mock.assert_called_once_with('my-search', 'OpenSearch_3.0', perform_check_only=True)

    @patch('dashboard.opensearch_views.add_tags')
    def test_add_tags_success(self, add_mock):
        add_mock.return_value = {'arn': 'arn:domain/my-search', 'tags': [{'Key': 'env', 'Value': 'local'}]}

        response = self.client.post(
            reverse('dashboard:opensearch-tags'),
            data=json.dumps({'arn': 'arn:domain/my-search', 'tags': [{'Key': 'env', 'Value': 'local'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        add_mock.assert_called_once_with('arn:domain/my-search', [{'Key': 'env', 'Value': 'local'}])

    @patch('dashboard.opensearch_views.remove_tags')
    def test_remove_tags_success(self, remove_mock):
        remove_mock.return_value = {'arn': 'arn:domain/my-search', 'tag_keys': ['env']}

        response = self.client.delete(
            reverse('dashboard:opensearch-tags'),
            data=json.dumps({'arn': 'arn:domain/my-search', 'tag_keys': ['env']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        remove_mock.assert_called_once_with('arn:domain/my-search', ['env'])

    @patch('dashboard.opensearch_views.list_versions')
    def test_list_versions_success(self, versions_mock):
        versions_mock.return_value = {'versions': ['OpenSearch_2.19']}

        response = self.client.get(reverse('dashboard:opensearch-versions'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['versions'], ['OpenSearch_2.19'])
        versions_mock.assert_called_once_with()

    @patch('dashboard.opensearch_views.get_compatible_versions')
    def test_get_compatible_versions_success(self, versions_mock):
        versions_mock.return_value = {'compatible_versions': ['OpenSearch_3.0']}

        response = self.client.post(
            reverse('dashboard:opensearch-compatible-versions'),
            data=json.dumps({'domain_name': 'my-search'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        versions_mock.assert_called_once_with('my-search')

    @patch('dashboard.opensearch_views.describe_instance_type_limits')
    def test_instance_type_limits_success(self, limits_mock):
        limits_mock.return_value = {'limits_by_role': {'data': []}}

        response = self.client.post(
            reverse('dashboard:opensearch-instance-type-limits'),
            data=json.dumps({'engine_version': 'OpenSearch_2.19', 'instance_type': 'm5.large.search'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        limits_mock.assert_called_once_with('OpenSearch_2.19', 'm5.large.search')

    @patch('dashboard.opensearch_views.execute_domain_request')
    def test_devtools_request_success(self, devtools_mock):
        devtools_mock.return_value = {
            'domain_name': 'my-search',
            'method': 'GET',
            'path': '/_cluster/health',
            'status_code': 200,
            'latency_ms': 5.4,
            'json': {'status': 'green'},
        }

        response = self.client.post(
            reverse('dashboard:opensearch-devtools-request'),
            data=json.dumps({
                'domain_name': 'my-search',
                'method': 'GET',
                'path': '/_cluster/health',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status_code'], 200)
        devtools_mock.assert_called_once_with(
            domain_name='my-search',
            method='GET',
            path='/_cluster/health',
            body=None,
            headers=None,
            endpoint_url=None,
        )
