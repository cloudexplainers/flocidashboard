import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class SecretsManagerPageTemplateTests(SimpleTestCase):
    def test_secretsmanager_service_page_keeps_readonly_inventory_and_embeds_console(self):
        response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': 'secretsmanager'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2>Secrets Manager inventory</h2>', html=True)
        self.assertContains(response, 'id="secretsmanager-summary"')
        self.assertContains(response, 'id="secretsmanager-console-root"')
        self.assertNotContains(response, 'id="secretsmanager-grid"')
        self.assertContains(response, 'dashboard/secretsmanager-console.css')
        self.assertContains(response, 'dashboard/service-console.js')
        self.assertContains(response, 'dashboard/secretsmanager-console.js')


class SecretsManagerActionsApiTests(SimpleTestCase):
    @patch('dashboard.secretsmanager_views.create_secret')
    def test_create_secret_success(self, create_mock):
        create_mock.return_value = {
            'name': '/local/app/db',
            'arn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:/local/app/db',
            'version_id': 'version-1',
        }

        response = self.client.post(
            reverse('dashboard:secretsmanager-secrets'),
            data=json.dumps({
                'name': '/local/app/db',
                'value': {'username': 'local', 'password': 'secret'},
                'description': 'local database',
                'kms_key_id': 'alias/local',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version_id'], 'version-1')
        create_mock.assert_called_once_with(
            '/local/app/db',
            {'username': 'local', 'password': 'secret'},
            description='local database',
            kms_key_id='alias/local',
        )

    def test_create_secret_rejects_missing_name(self):
        response = self.client.post(
            reverse('dashboard:secretsmanager-secrets'),
            data=json.dumps({'value': 'secret'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'secretsmanager')
        self.assertEqual(response.json()['operation'], 'create_secret')

    @patch('dashboard.secretsmanager_views.get_secret_value')
    def test_get_secret_value_success(self, get_mock):
        get_mock.return_value = {
            'name': '/local/app/db',
            'type': 'string',
            'value': '{"ok": true}',
            'json': {'ok': True},
        }

        response = self.client.get(
            reverse('dashboard:secretsmanager-secret-value', kwargs={'secret_id': '/local/app/db'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['json'], {'ok': True})
        get_mock.assert_called_once_with('/local/app/db', version_id='', version_stage='')

    @patch('dashboard.secretsmanager_views.put_secret_value')
    def test_put_secret_value_success(self, put_mock):
        put_mock.return_value = {
            'name': '/local/app/db',
            'version_id': 'version-2',
        }

        response = self.client.put(
            reverse('dashboard:secretsmanager-secret-value', kwargs={'secret_id': '/local/app/db'}),
            data=json.dumps({'value': {'ok': True}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version_id'], 'version-2')
        put_mock.assert_called_once_with('/local/app/db', {'ok': True})

    @patch('dashboard.secretsmanager_views.delete_secret')
    def test_delete_secret_success(self, delete_mock):
        delete_mock.return_value = {
            'name': '/local/app/db',
            'deletion_date': '2026-05-27T00:00:00',
        }

        response = self.client.delete(
            reverse('dashboard:secretsmanager-secret-value', kwargs={'secret_id': '/local/app/db'}),
            data=json.dumps({'recovery_window_days': 7}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], '/local/app/db')
        delete_mock.assert_called_once_with(
            '/local/app/db',
            recovery_window_days=7,
            force_delete_without_recovery=False,
        )

    @patch('dashboard.secretsmanager_views.update_secret')
    def test_update_secret_metadata_success(self, update_mock):
        update_mock.return_value = {'name': '/local/app/db'}
        response = self.client.patch(reverse('dashboard:secretsmanager-secret-metadata', kwargs={'secret_id': '/local/app/db'}), data=json.dumps({'description': 'database', 'kms_key_id': 'alias/local'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with('/local/app/db', description='database', kms_key_id='alias/local')

    @patch('dashboard.secretsmanager_views.restore_secret')
    def test_restore_secret_success(self, restore_mock):
        restore_mock.return_value = {'name': '/local/app/db', 'restored': True}
        response = self.client.post(reverse('dashboard:secretsmanager-secret-restore', kwargs={'secret_id': '/local/app/db'}))
        self.assertEqual(response.status_code, 200)
        restore_mock.assert_called_once_with('/local/app/db')

    @patch('dashboard.secretsmanager_views.rotate_secret')
    def test_rotate_secret_success(self, rotate_mock):
        rotate_mock.return_value = {'name': '/local/app/db', 'version_id': 'version-2'}
        rules = {'AutomaticallyAfterDays': 30}
        response = self.client.post(reverse('dashboard:secretsmanager-secret-rotate', kwargs={'secret_id': '/local/app/db'}), data=json.dumps({'rotation_lambda_arn': 'arn:lambda:rotate', 'rotation_rules': rules, 'rotate_immediately': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        rotate_mock.assert_called_once_with('/local/app/db', rotation_lambda_arn='arn:lambda:rotate', rotation_rules=rules, rotate_immediately=True)

    @patch('dashboard.secretsmanager_views.tag_secret')
    def test_tag_secret_success(self, tag_mock):
        tags = [{'Key': 'env', 'Value': 'local'}]; tag_mock.return_value = {'name': '/local/app/db', 'tags': tags}
        response = self.client.post(reverse('dashboard:secretsmanager-secret-tags', kwargs={'secret_id': '/local/app/db'}), data=json.dumps({'tags': tags}), content_type='application/json')
        self.assertEqual(response.status_code, 200); tag_mock.assert_called_once_with('/local/app/db', tags)

    @patch('dashboard.secretsmanager_views.untag_secret')
    def test_untag_secret_success(self, untag_mock):
        untag_mock.return_value = {'name': '/local/app/db', 'tag_keys': ['env']}
        response = self.client.delete(reverse('dashboard:secretsmanager-secret-tags', kwargs={'secret_id': '/local/app/db'}), data=json.dumps({'tag_keys': ['env']}), content_type='application/json')
        self.assertEqual(response.status_code, 200); untag_mock.assert_called_once_with('/local/app/db', ['env'])

    @patch('dashboard.secretsmanager_views.update_version_stage')
    def test_update_version_stage_success(self, stage_mock):
        stage_mock.return_value = {'name': '/local/app/db', 'version_stage': 'AWSCURRENT'}
        response = self.client.post(reverse('dashboard:secretsmanager-secret-version-stage', kwargs={'secret_id': '/local/app/db'}), data=json.dumps({'version_stage': 'AWSCURRENT', 'move_to_version_id': 'v2', 'remove_from_version_id': 'v1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200); stage_mock.assert_called_once_with('/local/app/db', 'AWSCURRENT', 'v2', remove_from_version_id='v1')

    @patch('dashboard.secretsmanager_views.get_random_password')
    def test_get_random_password_success(self, password_mock):
        password_mock.return_value = {'random_password': 'abc123'}
        response = self.client.post(reverse('dashboard:secretsmanager-random-password'), data=json.dumps({'PasswordLength': 6}), content_type='application/json')
        self.assertEqual(response.status_code, 200); password_mock.assert_called_once_with({'PasswordLength': 6})


class SecretsManagerMetadataInventoryTests(SimpleTestCase):
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_lists_deletion_and_versions_without_retrieving_values(self, factory_mock, paginate_mock):
        from .aws import secretsmanager_inventory
        client = factory_mock.return_value.client.return_value
        paginate_mock.side_effect = [[{'Name': 'app/db', 'ARN': 'arn:secret:db'}], [
            {'VersionId': 'v1', 'VersionStages': ['AWSCURRENT']},
        ]]
        client.describe_secret.return_value = {
            'Name': 'app/db', 'ARN': 'arn:secret:db', 'DeletedDate': '2026-10-30T00:00:00Z',
            'KmsKeyId': 'alias/application', 'RotationEnabled': False,
        }
        result = secretsmanager_inventory()
        paginate_mock.assert_any_call(client, 'list_secrets', 'SecretList', IncludePlannedDeletion=True)
        client.get_secret_value.assert_not_called()
        self.assertEqual(result['summary']['scheduled_for_deletion'], 1)
        self.assertEqual(result['secrets'][0]['versions'][0]['VersionId'], 'v1')
        self.assertNotIn('current_value', result['secrets'][0])

    @patch('dashboard.secretsmanager_api._client')
    def test_metadata_edit_preserves_encryption_when_key_is_blank(self, client_mock):
        from .secretsmanager_api import update_secret
        client_mock.return_value.update_secret.return_value = {'Name': 'app/db'}
        update_secret('app/db', description='Updated')
        client_mock.return_value.update_secret.assert_called_once_with(SecretId='app/db', Description='Updated')

    @patch('dashboard.secretsmanager_api._client')
    def test_binary_value_response_keeps_base64_and_version_metadata(self, client_mock):
        from .secretsmanager_api import get_secret_value
        client_mock.return_value.get_secret_value.return_value = {
            'SecretBinary': b'\x00\xff', 'VersionId': 'binary-v1', 'VersionStages': ['AWSPREVIOUS'],
        }
        result = get_secret_value('app/db', version_id='binary-v1')
        self.assertEqual(result['type'], 'binary')
        self.assertEqual(result['value'], 'AP8=')
        self.assertEqual(result['size_bytes'], 2)
        self.assertEqual(result['version_stages'], ['AWSPREVIOUS'])
        client_mock.return_value.get_secret_value.assert_called_once_with(SecretId='app/db', VersionId='binary-v1')

    @patch('dashboard.aws._paginate', side_effect=ValueError('Inventory unavailable'))
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_errors_are_visible_instead_of_reporting_no_secrets(self, factory_mock, paginate_mock):
        response = self.client.get('/api/secretsmanager/')
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()['error'], 'Inventory unavailable')
