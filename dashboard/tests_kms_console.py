import json
from base64 import b64encode
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .kms_api import create_grant, generate_random, put_key_policy, set_key_enabled
from .services import get_service


class KMSPageTemplateTests(SimpleTestCase):

    def test_kms_registry_marks_service_interactive(self):
        service = get_service('kms')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'encrypt' for action in service.actions))
        self.assertTrue(any(action.name == 'generate_random' for action in service.actions))
        self.assertTrue(any(action.name == 'set_key_enabled' for action in service.actions))
        self.assertTrue(any(action.name == 'schedule_key_deletion' for action in service.actions))


class KMSActionsApiTests(SimpleTestCase):
    @patch('dashboard.kms_views.create_key')
    def test_create_key_success(self, create_mock):
        create_mock.return_value = {'key_id': 'key-1', 'key_state': 'Enabled'}

        response = self.client.post(
            reverse('dashboard:kms-keys'),
            data=json.dumps({
                'description': 'Local key',
                'key_usage': 'ENCRYPT_DECRYPT',
                'key_spec': 'SYMMETRIC_DEFAULT',
                'override_id': 'key-1',
                'tags': [{'TagKey': 'env', 'TagValue': 'local'}],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['key_id'], 'key-1')
        create_mock.assert_called_once_with(
            description='Local key',
            key_usage='ENCRYPT_DECRYPT',
            key_spec='SYMMETRIC_DEFAULT',
            override_id='key-1',
            tags=[{'TagKey': 'env', 'TagValue': 'local'}],
        )

    def test_create_key_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:kms-keys'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'kms')
        self.assertEqual(response.json()['operation'], 'create_key')

    @patch('dashboard.kms_views.create_alias')
    def test_create_alias_success(self, create_alias_mock):
        create_alias_mock.return_value = {'alias_name': 'alias/local', 'target_key_id': 'key-1'}

        response = self.client.post(
            reverse('dashboard:kms-aliases'),
            data=json.dumps({'alias_name': 'alias/local', 'target_key_id': 'key-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['alias_name'], 'alias/local')
        create_alias_mock.assert_called_once_with('alias/local', 'key-1')

    @patch('dashboard.kms_views.delete_alias')
    def test_delete_alias_success(self, delete_alias_mock):
        delete_alias_mock.return_value = {'alias_name': 'alias/local', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:kms-aliases'),
            data=json.dumps({'alias_name': 'alias/local'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_alias_mock.assert_called_once_with('alias/local')

    @patch('dashboard.kms_views.encrypt')
    def test_encrypt_success(self, encrypt_mock):
        encrypt_mock.return_value = {'key_id': 'key-1', 'ciphertext_blob': 'abc'}

        response = self.client.post(
            reverse('dashboard:kms-encrypt'),
            data=json.dumps({'key_id': 'alias/local', 'plaintext': 'hello'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ciphertext_blob'], 'abc')
        encrypt_mock.assert_called_once_with('alias/local', 'hello')

    @patch('dashboard.kms_views.decrypt')
    def test_decrypt_success(self, decrypt_mock):
        decrypt_mock.return_value = {'key_id': 'key-1', 'plaintext': 'hello'}

        response = self.client.post(
            reverse('dashboard:kms-decrypt'),
            data=json.dumps({'ciphertext_blob': 'abc'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['plaintext'], 'hello')
        decrypt_mock.assert_called_once_with('abc')

    @patch('dashboard.kms_views.generate_data_key')
    def test_generate_data_key_success(self, data_key_mock):
        data_key_mock.return_value = {'key_id': 'key-1', 'plaintext_base64': 'abc', 'ciphertext_blob': 'def'}

        response = self.client.post(
            reverse('dashboard:kms-data-keys'),
            data=json.dumps({'key_id': 'alias/local', 'key_spec': 'AES_256'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['ciphertext_blob'], 'def')
        data_key_mock.assert_called_once_with('alias/local', key_spec='AES_256', number_of_bytes=None)

    @patch('dashboard.kms_views.generate_random')
    def test_generate_random_success(self, random_mock):
        random_mock.return_value = {'plaintext_base64': 'AQID', 'size_bytes': 3}

        response = self.client.post(
            reverse('dashboard:kms-random'),
            data=json.dumps({'number_of_bytes': 3}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['plaintext_base64'], 'AQID')
        random_mock.assert_called_once_with(3)

    def test_generate_random_rejects_zero_bytes(self):
        response = self.client.post(
            reverse('dashboard:kms-random'),
            data=json.dumps({'number_of_bytes': 0}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['error'], 'Number of bytes must be greater than zero')

    @patch('dashboard.kms_views.set_key_rotation')
    def test_rotation_success(self, rotation_mock):
        rotation_mock.return_value = {'key_id': 'key-1', 'rotation_enabled': True}

        response = self.client.post(
            reverse('dashboard:kms-rotation'),
            data=json.dumps({'key_id': 'key-1', 'enabled': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['rotation_enabled'])
        rotation_mock.assert_called_once_with('key-1', True)

    @patch('dashboard.kms_views.set_key_enabled')
    def test_key_state_success(self, state_mock):
        state_mock.return_value = {'key_id': 'key-1', 'enabled': False, 'key_state': 'Disabled'}

        response = self.client.post(
            reverse('dashboard:kms-key-state'),
            data=json.dumps({'key_id': 'key-1', 'enabled': False}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['enabled'])
        state_mock.assert_called_once_with('key-1', False)

    @patch('dashboard.kms_views.schedule_key_deletion')
    def test_schedule_deletion_success(self, schedule_mock):
        schedule_mock.return_value = {'key_id': 'key-1', 'key_state': 'PendingDeletion'}

        response = self.client.post(
            reverse('dashboard:kms-deletion-schedule'),
            data=json.dumps({'key_id': 'key-1', 'pending_window_in_days': 10}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['key_state'], 'PendingDeletion')
        schedule_mock.assert_called_once_with('key-1', pending_window_in_days=10)

    @patch('dashboard.kms_views.cancel_key_deletion')
    def test_cancel_deletion_success(self, cancel_mock):
        cancel_mock.return_value = {'key_id': 'key-1', 'key_state': 'Enabled'}

        response = self.client.post(
            reverse('dashboard:kms-deletion-cancel'),
            data=json.dumps({'key_id': 'key-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['key_state'], 'Enabled')
        cancel_mock.assert_called_once_with('key-1')

    @patch('dashboard.kms_views.update_key_description')
    def test_update_description_success(self, update_mock):
        update_mock.return_value = {'key_id': 'key-1', 'description': 'Application key'}
        response = self.client.patch(reverse('dashboard:kms-key-metadata'), data=json.dumps({'key_id': 'key-1', 'description': 'Application key'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with('key-1', 'Application key')

    @patch('dashboard.kms_views.put_key_policy')
    def test_put_policy_success(self, policy_mock):
        policy = {'Version': '2012-10-17', 'Statement': []}
        policy_mock.return_value = {'key_id': 'key-1', 'policy': policy}
        response = self.client.put(reverse('dashboard:kms-key-policy'), data=json.dumps({'key_id': 'key-1', 'policy': policy}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        policy_mock.assert_called_once_with('key-1', policy)

    @patch('dashboard.kms_views.create_grant')
    def test_create_grant_success(self, grant_mock):
        grant_mock.return_value = {'key_id': 'key-1', 'grant_id': 'grant-1'}
        response = self.client.post(reverse('dashboard:kms-grants'), data=json.dumps({'key_id': 'key-1', 'grantee_principal': 'arn:role/app', 'operations': ['Encrypt']}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        grant_mock.assert_called_once_with('key-1', 'arn:role/app', ['Encrypt'], name='', retiring_principal='')

    @patch('dashboard.kms_views.revoke_grant')
    def test_revoke_grant_success(self, revoke_mock):
        revoke_mock.return_value = {'key_id': 'key-1', 'grant_id': 'grant-1', 'revoked': True}
        response = self.client.delete(reverse('dashboard:kms-grants'), data=json.dumps({'key_id': 'key-1', 'grant_id': 'grant-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        revoke_mock.assert_called_once_with('key-1', 'grant-1')

    @patch('dashboard.kms_views.rotate_key_on_demand')
    def test_rotate_on_demand_success(self, rotate_mock):
        rotate_mock.return_value = {'key_id': 'key-1'}
        response = self.client.post(reverse('dashboard:kms-rotation-on-demand'), data=json.dumps({'key_id': 'key-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        rotate_mock.assert_called_once_with('key-1')

    @patch('dashboard.kms_views.get_public_key')
    def test_get_public_key_success(self, public_mock):
        public_mock.return_value = {'key_id': 'key-1', 'public_key_base64': 'AQID'}
        response = self.client.post(reverse('dashboard:kms-public-key'), data=json.dumps({'key_id': 'key-1'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        public_mock.assert_called_once_with('key-1')

    @patch('dashboard.kms_views.tag_key')
    def test_tag_key_success(self, tag_mock):
        tags = [{'TagKey': 'env', 'TagValue': 'local'}]
        tag_mock.return_value = {'key_id': 'key-1', 'tags': tags}
        response = self.client.post(reverse('dashboard:kms-tags'), data=json.dumps({'key_id': 'key-1', 'tags': tags}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        tag_mock.assert_called_once_with('key-1', tags)

    @patch('dashboard.kms_views.untag_key')
    def test_untag_key_success(self, untag_mock):
        untag_mock.return_value = {'key_id': 'key-1', 'tag_keys': ['env']}
        response = self.client.delete(reverse('dashboard:kms-tags'), data=json.dumps({'key_id': 'key-1', 'tag_keys': ['env']}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        untag_mock.assert_called_once_with('key-1', ['env'])

    @patch('dashboard.kms_views.sign')
    def test_sign_success(self, sign_mock):
        sign_mock.return_value = {'key_id': 'key-1', 'signature': 'AQID'}
        response = self.client.post(reverse('dashboard:kms-sign'), data=json.dumps({'key_id': 'key-1', 'message': 'hello', 'signing_algorithm': 'RSASSA_PSS_SHA_256'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        sign_mock.assert_called_once_with('key-1', 'hello', 'RSASSA_PSS_SHA_256')

    @patch('dashboard.kms_views.verify')
    def test_verify_success(self, verify_mock):
        verify_mock.return_value = {'key_id': 'key-1', 'signature_valid': True}
        response = self.client.post(reverse('dashboard:kms-verify'), data=json.dumps({'key_id': 'key-1', 'message': 'hello', 'signature': 'AQID', 'signing_algorithm': 'RSASSA_PSS_SHA_256'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        verify_mock.assert_called_once_with('key-1', 'hello', 'AQID', 'RSASSA_PSS_SHA_256')

    @patch('dashboard.kms_views.generate_mac')
    def test_generate_mac_success(self, mac_mock):
        mac_mock.return_value = {'key_id': 'key-1', 'mac': 'AQID'}
        response = self.client.post(reverse('dashboard:kms-generate-mac'), data=json.dumps({'key_id': 'key-1', 'message': 'hello', 'mac_algorithm': 'HMAC_SHA_256'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mac_mock.assert_called_once_with('key-1', 'hello', 'HMAC_SHA_256')

    @patch('dashboard.kms_views.verify_mac')
    def test_verify_mac_success(self, verify_mock):
        verify_mock.return_value = {'key_id': 'key-1', 'mac_valid': True}
        response = self.client.post(reverse('dashboard:kms-verify-mac'), data=json.dumps({'key_id': 'key-1', 'message': 'hello', 'mac': 'AQID', 'mac_algorithm': 'HMAC_SHA_256'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        verify_mock.assert_called_once_with('key-1', 'hello', 'AQID', 'HMAC_SHA_256')


class KMSApiHelperTests(SimpleTestCase):
    @patch('dashboard.kms_api._client')
    def test_put_key_policy_serializes_document(self, client_factory):
        policy = {'Version': '2012-10-17', 'Statement': []}
        put_key_policy('key-1', policy)
        client_factory.return_value.put_key_policy.assert_called_once_with(KeyId='key-1', PolicyName='default', Policy=json.dumps(policy))

    @patch('dashboard.kms_api._client')
    def test_create_grant_passes_supported_fields(self, client_factory):
        client_factory.return_value.create_grant.return_value = {'GrantId': 'grant-1', 'GrantToken': 'token'}
        result = create_grant('key-1', 'arn:role/app', ['Encrypt', 'Decrypt'], name='app')
        client_factory.return_value.create_grant.assert_called_once_with(KeyId='key-1', GranteePrincipal='arn:role/app', Operations=['Encrypt', 'Decrypt'], Name='app')
        self.assertEqual(result['grant_id'], 'grant-1')

    @patch('dashboard.kms_api._client')
    def test_generate_random_calls_kms_and_encodes_plaintext(self, client_factory):
        client_factory.return_value.generate_random.return_value = {'Plaintext': b'\x01\x02\x03'}

        result = generate_random(3)

        client_factory.return_value.generate_random.assert_called_once_with(NumberOfBytes=3)
        self.assertEqual(result, {
            'plaintext_base64': b64encode(b'\x01\x02\x03').decode('ascii'),
            'size_bytes': 3,
        })

    def test_generate_random_rejects_more_than_aws_limit(self):
        with self.assertRaisesMessage(ValueError, 'Number of bytes must be 1024 or less'):
            generate_random(1025)

    @patch('dashboard.kms_api._client')
    def test_set_key_enabled_calls_kms_enable_key(self, client_factory):
        result = set_key_enabled('key-1', True)

        client_factory.return_value.enable_key.assert_called_once_with(KeyId='key-1')
        client_factory.return_value.disable_key.assert_not_called()
        self.assertEqual(result['key_state'], 'Enabled')

    @patch('dashboard.kms_api._client')
    def test_set_key_enabled_calls_kms_disable_key(self, client_factory):
        result = set_key_enabled('key-1', False)

        client_factory.return_value.disable_key.assert_called_once_with(KeyId='key-1')
        client_factory.return_value.enable_key.assert_not_called()
        self.assertEqual(result['key_state'], 'Disabled')
