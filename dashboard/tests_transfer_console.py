import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class TransferPageTemplateTests(SimpleTestCase):

    def test_transfer_registry_marks_service_interactive(self):
        service = get_service('transfer')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_server' for action in service.actions))
        self.assertTrue(any(action.name == 'start_server' for action in service.actions))
        self.assertTrue(any(action.name == 'create_user' for action in service.actions))
        self.assertTrue(any(action.name == 'import_ssh_public_key' for action in service.actions))


class TransferActionsApiTests(SimpleTestCase):
    server_id = 's-01234567890abcdef'

    @patch('dashboard.transfer_views.create_server')
    def test_create_server_success(self, create_mock):
        create_mock.return_value = {'server_id': self.server_id}

        response = self.client.post(
            reverse('dashboard:transfer-servers'),
            data=json.dumps({
                'protocols': ['SFTP'],
                'endpoint_type': 'PUBLIC',
                'domain': 'S3',
                'logging_role': '',
                'security_policy_name': '',
                'tags': [{'Key': 'env', 'Value': 'dev'}],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            protocols=['SFTP'],
            endpoint_type='PUBLIC',
            domain='S3',
            logging_role='',
            security_policy_name='',
            tags=[{'Key': 'env', 'Value': 'dev'}],
        )

    def test_create_server_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:transfer-servers'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'transfer')
        self.assertEqual(response.json()['operation'], 'create_server')

    @patch('dashboard.transfer_views.update_server')
    def test_update_server_success(self, update_mock):
        update_mock.return_value = {'server_id': self.server_id}

        response = self.client.patch(
            reverse('dashboard:transfer-server-detail', kwargs={'server_id': self.server_id}),
            data=json.dumps({'options': {'Protocols': ['SFTP']}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(self.server_id, {'Protocols': ['SFTP']})

    @patch('dashboard.transfer_views.start_server')
    def test_start_server_success(self, start_mock):
        start_mock.return_value = {'server_id': self.server_id}

        response = self.client.post(reverse('dashboard:transfer-server-start', kwargs={'server_id': self.server_id}))

        self.assertEqual(response.status_code, 200)
        start_mock.assert_called_once_with(self.server_id)

    @patch('dashboard.transfer_views.stop_server')
    def test_stop_server_success(self, stop_mock):
        stop_mock.return_value = {'server_id': self.server_id}

        response = self.client.post(reverse('dashboard:transfer-server-stop', kwargs={'server_id': self.server_id}))

        self.assertEqual(response.status_code, 200)
        stop_mock.assert_called_once_with(self.server_id)

    @patch('dashboard.transfer_views.delete_server')
    def test_delete_server_success(self, delete_mock):
        delete_mock.return_value = {'server_id': self.server_id}

        response = self.client.delete(reverse('dashboard:transfer-server-detail', kwargs={'server_id': self.server_id}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with(self.server_id)

    @patch('dashboard.transfer_views.create_user')
    def test_create_user_success(self, create_mock):
        create_mock.return_value = {'server_id': self.server_id, 'user_name': 'alice'}

        response = self.client.post(
            reverse('dashboard:transfer-users', kwargs={'server_id': self.server_id}),
            data=json.dumps({
                'user_name': 'alice',
                'role': 'arn:aws:iam::000000000000:role/transfer-role',
                'home_directory': '/uploads',
                'home_directory_mappings': [],
                'policy': '',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            self.server_id,
            'alice',
            role='arn:aws:iam::000000000000:role/transfer-role',
            home_directory='/uploads',
            home_directory_mappings=[],
            policy='',
        )

    @patch('dashboard.transfer_views.update_user')
    def test_update_user_success(self, update_mock):
        update_mock.return_value = {'server_id': self.server_id, 'user_name': 'alice'}

        response = self.client.patch(
            reverse('dashboard:transfer-user-detail', kwargs={'server_id': self.server_id, 'user_name': 'alice'}),
            data=json.dumps({'options': {'HomeDirectory': '/uploads'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(self.server_id, 'alice', {'HomeDirectory': '/uploads'})

    @patch('dashboard.transfer_views.delete_user')
    def test_delete_user_success(self, delete_mock):
        delete_mock.return_value = {'server_id': self.server_id, 'user_name': 'alice'}

        response = self.client.delete(reverse(
            'dashboard:transfer-user-detail',
            kwargs={'server_id': self.server_id, 'user_name': 'alice'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with(self.server_id, 'alice')

    @patch('dashboard.transfer_views.import_ssh_public_key')
    def test_import_ssh_public_key_success(self, import_mock):
        import_mock.return_value = {'server_id': self.server_id, 'user_name': 'alice', 'ssh_public_key_id': 'key-1'}

        response = self.client.post(
            reverse('dashboard:transfer-ssh-public-keys', kwargs={'server_id': self.server_id, 'user_name': 'alice'}),
            data=json.dumps({'ssh_public_key_body': 'ssh-rsa AAAA alice@example'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        import_mock.assert_called_once_with(self.server_id, 'alice', 'ssh-rsa AAAA alice@example')

    @patch('dashboard.transfer_views.delete_ssh_public_key')
    def test_delete_ssh_public_key_success(self, delete_mock):
        delete_mock.return_value = {'server_id': self.server_id, 'user_name': 'alice', 'ssh_public_key_id': 'key-1'}

        response = self.client.delete(reverse(
            'dashboard:transfer-ssh-public-key-detail',
            kwargs={'server_id': self.server_id, 'user_name': 'alice', 'ssh_public_key_id': 'key-1'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with(self.server_id, 'alice', 'key-1')

    @patch('dashboard.transfer_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'arn': 'arn-1'}

        response = self.client.post(
            reverse('dashboard:transfer-tags'),
            data=json.dumps({'arn': 'arn-1', 'tags': [{'Key': 'env', 'Value': 'dev'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        tag_mock.assert_called_once_with('arn-1', [{'Key': 'env', 'Value': 'dev'}])

    @patch('dashboard.transfer_views.untag_resource')
    def test_untag_resource_success(self, untag_mock):
        untag_mock.return_value = {'arn': 'arn-1', 'tag_keys': ['env']}

        response = self.client.delete(
            reverse('dashboard:transfer-tags'),
            data=json.dumps({'arn': 'arn-1', 'tag_keys': ['env']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        untag_mock.assert_called_once_with('arn-1', ['env'])
