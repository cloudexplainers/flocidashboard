import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .aws import ssm_inventory


class SSMActionsApiTests(SimpleTestCase):
    @patch('dashboard.ssm_views.put_parameter')
    def test_create_parameter_success(self, put_mock):
        put_mock.return_value = {
            'name': '/local/app/config',
            'type': 'String',
            'version': 1,
        }

        response = self.client.post(
            reverse('dashboard:ssm-parameters'),
            data=json.dumps({
                'name': '/local/app/config',
                'type': 'String',
                'value': {'feature_enabled': True},
                'description': 'local config',
                'overwrite': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version'], 1)
        put_mock.assert_called_once_with(
            '/local/app/config',
            {'feature_enabled': True},
            parameter_type='String',
            description='local config',
            overwrite=True,
        )

    def test_create_parameter_rejects_missing_name(self):
        response = self.client.post(
            reverse('dashboard:ssm-parameters'),
            data=json.dumps({'value': 'enabled'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'ssm')
        self.assertEqual(response.json()['operation'], 'put_parameter')

    @patch('dashboard.ssm_views.get_parameter')
    def test_get_parameter_success(self, get_mock):
        get_mock.return_value = {
            'name': '/local/app/config',
            'type': 'String',
            'value': '{"ok": true}',
            'json': {'ok': True},
        }

        response = self.client.get(
            reverse('dashboard:ssm-parameter-value', kwargs={'parameter_name': '/local/app/config'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['json'], {'ok': True})
        get_mock.assert_called_once_with('/local/app/config', with_decryption=True)

    @patch('dashboard.ssm_views.put_parameter')
    def test_update_parameter_success(self, put_mock):
        put_mock.return_value = {
            'name': '/local/app/config',
            'version': 2,
        }

        response = self.client.put(
            reverse('dashboard:ssm-parameter-value', kwargs={'parameter_name': '/local/app/config'}),
            data=json.dumps({'type': 'String', 'value': 'enabled', 'description': 'updated'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['version'], 2)
        put_mock.assert_called_once_with(
            '/local/app/config',
            'enabled',
            parameter_type='String',
            description='updated',
            overwrite=True,
        )

    @patch('dashboard.ssm_views.delete_parameter')
    def test_delete_parameter_success(self, delete_mock):
        delete_mock.return_value = {
            'name': '/local/app/config',
        }

        response = self.client.delete(
            reverse('dashboard:ssm-parameter-value', kwargs={'parameter_name': '/local/app/config'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], '/local/app/config')
        delete_mock.assert_called_once_with('/local/app/config')


class SSMInventoryTests(SimpleTestCase):
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_includes_default_patch_baselines(self, factory_mock, paginate_mock):
        ssm = MagicMock()
        ssm.get_default_patch_baseline.side_effect = lambda OperatingSystem: (
            {'BaselineId': f'pb-{OperatingSystem.lower()}'}
            if OperatingSystem in {'AMAZON_LINUX_2023', 'WINDOWS'}
            else {}
        )
        factory_mock.return_value.client.return_value = ssm
        paginate_mock.return_value = []

        result = ssm_inventory()

        self.assertEqual(result['summary']['default_patch_baselines'], 2)
        self.assertEqual(
            {baseline['operating_system'] for baseline in result['default_patch_baselines']},
            {'AMAZON_LINUX_2023', 'WINDOWS'},
        )
        self.assertIn('GetDefaultPatchBaseline', result['supported_from_sdk'])


class SSMDetailsApiTests(SimpleTestCase):
    @patch('dashboard.ssm_api._client')
    @patch('dashboard.aws._paginate')
    def test_history_is_metadata_only_and_does_not_decrypt(self, paginate, client):
        paginate.return_value = [{'Name': '/app/password', 'Version': 3, 'Value': 'do-not-return', 'Type': 'SecureString'}]
        response = self.client.get(reverse('dashboard:ssm-history'), {'name': '/app/password'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Value', response.json()['parameters'][0])
        paginate.assert_called_once_with(client.return_value, 'get_parameter_history', 'Parameters', Name='/app/password', WithDecryption=False)

    @patch('dashboard.ssm_views.get_parameter')
    def test_value_preserves_hierarchical_name_and_version_selector(self, get):
        get.return_value = {'name': '/app/password', 'version': 2, 'value': ' value '}
        response = self.client.get('/api/ssm/parameters/%2Fapp%2Fpassword%3A2/value/')
        self.assertEqual(response.status_code, 200)
        get.assert_called_once_with('/app/password:2', with_decryption=True)
        self.assertEqual(response.json()['value'], ' value ')

    @patch('dashboard.ssm_api._client')
    def test_tags_allow_empty_values_and_preserve_parameter_path(self, client):
        client.return_value.list_tags_for_resource.return_value = {'TagList': [{'Key': 'owner', 'Value': ''}]}
        response = self.client.post(reverse('dashboard:ssm-tags'), data=json.dumps({'name': '/app/config', 'tags': [{'Key': 'owner', 'Value': ''}]}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.return_value.add_tags_to_resource.assert_called_once_with(ResourceType='Parameter', ResourceId='/app/config', Tags=[{'Key': 'owner', 'Value': ''}])
        self.assertEqual(response.json()['tags'][0]['Value'], '')
        response = self.client.delete(reverse('dashboard:ssm-tags'), data=json.dumps({'name': '/app/config', 'tag_keys': ['owner']}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.return_value.remove_tags_from_resource.assert_called_once_with(ResourceType='Parameter', ResourceId='/app/config', TagKeys=['owner'])

    @patch('dashboard.ssm_api._client')
    def test_history_errors_are_visible(self, client):
        from botocore.exceptions import ClientError
        client.return_value.get_paginator.side_effect = ClientError({'Error': {'Code': 'AccessDeniedException', 'Message': 'Access denied'}}, 'GetParameterHistory')
        response = self.client.get(reverse('dashboard:ssm-history'), {'name': '/app/password'})
        self.assertGreaterEqual(response.status_code, 400)
        self.assertIn('error', response.json())

    @patch('dashboard.ssm_api._client')
    def test_documents_validate_json_and_use_dedicated_actions(self, client):
        client.return_value.create_document.return_value = {'DocumentDescription': {'Name': 'RunTask'}}
        response = self.client.post(reverse('dashboard:ssm-documents'), data=json.dumps({'name': 'RunTask', 'content': '{}', 'document_type': 'Command'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.return_value.create_document.assert_called_once_with(Name='RunTask', Content='{}', DocumentType='Command', DocumentFormat='JSON')
        response = self.client.post(reverse('dashboard:ssm-documents'), data=json.dumps({'name': 'BadTask', 'content': '{'}), content_type='application/json')
        self.assertGreaterEqual(response.status_code, 400)
        self.assertEqual(client.return_value.create_document.call_count, 1)
        response = self.client.delete(reverse('dashboard:ssm-documents'), data=json.dumps({'name': 'RunTask'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.return_value.delete_document.assert_called_once_with(Name='RunTask')

    @patch('dashboard.ssm_views.put_parameter')
    def test_create_does_not_overwrite_and_keeps_whitespace(self, put):
        put.return_value = {'name': '/app/config', 'version': 1}
        response = self.client.post(reverse('dashboard:ssm-parameters'), data=json.dumps({'name': '/app/config', 'value': '  value\n', 'type': 'String', 'overwrite': False}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        put.assert_called_once_with('/app/config', '  value\n', parameter_type='String', description='', overwrite=False)

    @patch('dashboard.ssm_views.put_parameter')
    def test_edit_preserves_customer_key_and_parameter_configuration(self, put):
        put.return_value = {'version': 4}
        response = self.client.put('/api/ssm/parameters/%2Fapp%2Fpassword/value/', data=json.dumps({'value': 'replacement', 'type': 'SecureString', 'key_id': 'alias/customer-key', 'tier': 'Advanced', 'data_type': 'text'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        put.assert_called_once_with('/app/password', 'replacement', parameter_type='SecureString', description='', overwrite=True, key_id='alias/customer-key', tier='Advanced', data_type='text')

    @patch('dashboard.ssm_api._client')
    def test_put_forwards_existing_encryption_key_and_tier(self, client):
        from .ssm_api import put_parameter
        client.return_value.put_parameter.return_value = {'Version': 4}
        put_parameter('/app/password', 'replacement', parameter_type='SecureString', overwrite=True, key_id='alias/customer-key', tier='Advanced', data_type='text')
        client.return_value.put_parameter.assert_called_once_with(Name='/app/password', Value='replacement', Type='SecureString', Overwrite=True, KeyId='alias/customer-key', Tier='Advanced', DataType='text')
