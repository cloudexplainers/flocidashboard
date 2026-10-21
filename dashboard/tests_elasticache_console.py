import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class ElastiCachePageTemplateTests(SimpleTestCase):

    def test_elasticache_registry_marks_service_interactive(self):
        service = get_service('elasticache')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_replication_group' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_replication_group' for action in service.actions))
        self.assertTrue(any(action.name == 'validate_iam_auth_token' for action in service.actions))


class ElastiCacheActionsApiTests(SimpleTestCase):
    @patch('dashboard.elasticache_views.create_replication_group')
    def test_create_replication_group_success(self, create_mock):
        create_mock.return_value = {'replication_group_id': 'my-cache', 'status': 'creating'}

        response = self.client.post(
            reverse('dashboard:elasticache-replication-groups'),
            data=json.dumps({
                'replication_group_id': 'my-cache',
                'description': 'Dev cache',
                'engine': 'redis',
                'cache_node_type': 'cache.t4g.micro',
                'num_cache_clusters': 1,
                'port': 6379,
                'user_group_ids': ['local-users'],
                'transit_encryption_enabled': True,
                'at_rest_encryption_enabled': False,
                'tags': [{'Key': 'env', 'Value': 'local'}],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['replication_group_id'], 'my-cache')
        create_mock.assert_called_once_with(
            replication_group_id='my-cache',
            description='Dev cache',
            engine='redis',
            cache_node_type='cache.t4g.micro',
            num_cache_clusters=1,
            port=6379,
            user_group_ids=['local-users'],
            transit_encryption_enabled=True,
            at_rest_encryption_enabled=False,
            tags=[{'Key': 'env', 'Value': 'local'}],
        )

    def test_create_replication_group_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:elasticache-replication-groups'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'elasticache')
        self.assertEqual(response.json()['operation'], 'create_replication_group')

    @patch('dashboard.elasticache_views.delete_replication_group')
    def test_delete_replication_group_success(self, delete_mock):
        delete_mock.return_value = {'replication_group_id': 'my-cache', 'status': 'deleting'}

        response = self.client.post(
            reverse('dashboard:elasticache-replication-groups-delete'),
            data=json.dumps({'replication_group_id': 'my-cache'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'deleting')
        delete_mock.assert_called_once_with(
            'my-cache',
            retain_primary_cluster=False,
            final_snapshot_identifier='',
        )

    @patch('dashboard.elasticache_views.create_user')
    def test_create_user_success(self, create_mock):
        create_mock.return_value = {'user_id': 'alice', 'status': 'active'}

        response = self.client.post(
            reverse('dashboard:elasticache-users'),
            data=json.dumps({
                'user_id': 'alice',
                'user_name': 'alice',
                'engine': 'redis',
                'access_string': 'on ~* +@all',
                'auth_type': 'iam',
                'passwords': [],
                'tags': [],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user_id'], 'alice')
        create_mock.assert_called_once_with(
            user_id='alice',
            user_name='alice',
            engine='redis',
            access_string='on ~* +@all',
            auth_type='iam',
            authentication_mode={},
            passwords=[],
            no_password_required=False,
            tags=[],
        )

    @patch('dashboard.elasticache_views.modify_user')
    def test_modify_user_success(self, modify_mock):
        modify_mock.return_value = {'user_id': 'alice', 'status': 'modifying'}

        response = self.client.post(
            reverse('dashboard:elasticache-users-modify'),
            data=json.dumps({'user_id': 'alice', 'access_string': 'on ~cached:* +@read'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'modifying')
        modify_mock.assert_called_once_with(
            user_id='alice',
            access_string='on ~cached:* +@read',
            append_access_string='',
            auth_type='',
            authentication_mode={},
            passwords=[],
            no_password_required=None,
        )

    @patch('dashboard.elasticache_views.delete_user')
    def test_delete_user_success(self, delete_mock):
        delete_mock.return_value = {'user_id': 'alice', 'status': 'deleting'}

        response = self.client.post(
            reverse('dashboard:elasticache-users-delete'),
            data=json.dumps({'user_id': 'alice'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user_id'], 'alice')
        delete_mock.assert_called_once_with('alice')

    @patch('dashboard.elasticache_views.validate_iam_auth_token')
    def test_validate_iam_auth_token_success(self, validate_mock):
        validate_mock.return_value = {'valid': 'true', 'user_id': 'alice'}

        response = self.client.post(
            reverse('dashboard:elasticache-iam-auth-validate'),
            data=json.dumps({'auth_token': 'token', 'user_id': 'alice', 'user_name': 'alice'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['valid'], 'true')
        validate_mock.assert_called_once_with('token', user_id='alice', user_name='alice')
