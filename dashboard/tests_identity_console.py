import json
from unittest.mock import MagicMock, patch
from botocore.exceptions import ClientError
from django.test import TestCase
from django.urls import reverse
from .aws import RUNTIME_IDENTITY_SESSION_KEY


class IdentityConsoleTests(TestCase):
    def factory(self):
        factory = MagicMock()
        factory.endpoint_url = 'http://localhost:4566'
        factory.region = 'eu-west-1'
        factory.credential_context.return_value = {'credential_source': 'session_identity'}
        factory.identity.return_value = {
            'account': '123456789012',
            'arn': 'arn:aws:iam::123456789012:user/alice',
            'user_id': 'AIDAUSER',
        }
        factory.local_identity_hint.return_value = None
        return factory

    @patch('dashboard.identity_views.FlociClientFactory')
    @patch('dashboard.aws._paginate')
    def test_options_use_current_identity_and_return_only_target_metadata(self, paginate, factory_class):
        session = self.client.session
        session[RUNTIME_IDENTITY_SESSION_KEY] = {
            'type': 'user',
            'access_key_id': 'CURRENT',
            'secret_access_key': 'secret',
        }
        session.save()
        factory = self.factory()
        factory_class.return_value = factory
        paginate.side_effect = [
            [{'UserName': 'alice', 'Arn': 'arn:user', 'Path': '/', 'SecretAccessKey': 'never-return'}],
            [{'RoleName': 'App', 'Arn': 'arn:aws:iam::123456789012:role/team/App', 'Path': '/team/'}],
        ]
        response = self.client.get(reverse('dashboard:session-identity-options'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['users'], [{'name': 'alice', 'arn': 'arn:user', 'path': '/'}])
        self.assertEqual(response.json()['roles'][0]['path'], '/team/')
        self.assertNotContains(response, 'never-return')
        self.assertEqual(factory_class.call_args.kwargs['identity']['access_key_id'], 'CURRENT')

    @patch('dashboard.identity_views.FlociClientFactory')
    @patch('dashboard.aws._paginate')
    def test_one_list_can_fail_without_hiding_other_targets(self, paginate, factory_class):
        factory_class.return_value = self.factory()
        paginate.side_effect = [
            ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'Users denied'}}, 'ListUsers'),
            [{'RoleName': 'App', 'Arn': 'arn:role'}],
        ]
        payload = self.client.get(reverse('dashboard:session-identity-options')).json()
        self.assertEqual(payload['users'], [])
        self.assertIn('Users denied', payload['errors']['users'])
        self.assertEqual(payload['roles'][0]['name'], 'App')

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_role_arn_preserves_partition_and_path_and_does_not_require_a_role_name(self, factory_class):
        factory = self.factory()
        factory_class.return_value = factory
        factory.client.return_value.assume_role.return_value = {
            'Credentials': {
                'AccessKeyId': 'ASIATEST',
                'SecretAccessKey': 'never-return-secret',
                'SessionToken': 'never-return-token',
                'Expiration': '2027-01-01T12:00:00Z',
            }
        }
        arn = 'arn:aws-us-gov:iam::123456789012:role/team/application/App'
        response = self.client.post(
            reverse('dashboard:session-identity-assume-role'),
            data=json.dumps({'role_arn': arn, 'session_name': 'review-session'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        factory.client.return_value.assume_role.assert_called_once_with(RoleArn=arn, RoleSessionName='review-session')
        self.assertEqual(self.client.session[RUNTIME_IDENTITY_SESSION_KEY]['label'], 'App/review-session')
        self.assertEqual(response.json()['session_identity']['expires_at'], '2027-01-01T12:00:00Z')
        self.assertNotContains(response, 'never-return-secret')
        self.assertNotContains(response, 'never-return-token')

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_invalid_role_inputs_do_not_call_sts_or_replace_session(self, factory_class):
        for body in [
            {'role_arn': 'arn:aws:iam::123:role/App'},
            {'role_name': 'App', 'account_id': '123'},
            {'role_arn': 'arn:aws:iam::123456789012:role/App', 'session_name': 'x'},
            {'role_arn': 'arn:aws:iam::123456789012:role/team/'},
        ]:
            with self.subTest(body=body):
                response = self.client.post(
                    reverse('dashboard:session-identity-assume-role'),
                    data=json.dumps(body),
                    content_type='application/json',
                )
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(RUNTIME_IDENTITY_SESSION_KEY, self.client.session)
        factory_class.assert_not_called()

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_unresolved_caller_is_explicit_even_when_a_local_hint_exists(self, factory_class):
        factory = self.factory()
        factory_class.return_value = factory
        factory.identity.side_effect = ValueError('STS unavailable')
        factory.local_identity_hint.return_value = {'account': 'local', 'arn': 'test (local credentials)'}
        response = self.client.get(reverse('dashboard:session-identity-detail'))
        self.assertFalse(response.json()['identity_resolved'])
        self.assertEqual(response.json()['identity_error'], 'STS unavailable')

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_failed_role_assumption_preserves_current_override(self, factory_class):
        session = self.client.session
        session[RUNTIME_IDENTITY_SESSION_KEY] = {
            'type': 'user',
            'label': 'alice',
            'access_key_id': 'CURRENT',
            'secret_access_key': 'secret',
        }
        session.save()
        factory = self.factory()
        factory_class.return_value = factory
        factory.client.return_value.assume_role.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'Trust policy denied'}}, 'AssumeRole'
        )
        response = self.client.post(
            reverse('dashboard:session-identity-assume-role'),
            data=json.dumps({'role_arn': 'arn:aws:iam::123456789012:role/App'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.session[RUNTIME_IDENTITY_SESSION_KEY]['access_key_id'], 'CURRENT')

    @patch('dashboard.identity_views.FlociClientFactory')
    def test_clear_resolves_configured_credentials_instead_of_stale_request_context(self, factory_class):
        session = self.client.session
        session[RUNTIME_IDENTITY_SESSION_KEY] = {
            'type': 'user',
            'label': 'old-user',
            'access_key_id': 'OLDKEY',
            'secret_access_key': 'secret',
        }
        session.save()
        factory_class.return_value = self.factory()
        response = self.client.post(reverse('dashboard:session-identity-clear'))
        self.assertEqual(response.status_code, 200)
        factory_class.assert_called_once_with(identity=None, use_runtime_identity=False)
        self.assertIsNone(response.json()['session_identity'])
        self.assertNotIn(RUNTIME_IDENTITY_SESSION_KEY, self.client.session)
