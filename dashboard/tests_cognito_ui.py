import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .cognito_api import change_user_state


class CognitoUserStateTests(SimpleTestCase):
    @patch('dashboard.cognito_api._client')
    def test_dispatches_supported_state_changes(self, client):
        for action, operation in [('confirm', 'admin_confirm_sign_up'), ('enable', 'admin_enable_user'), ('disable', 'admin_disable_user')]:
            client.return_value = MagicMock()
            getattr(client.return_value, operation).return_value = {}
            result = change_user_state('pool-1', 'alice', action)
            getattr(client.return_value, operation).assert_called_once_with(UserPoolId='pool-1', Username='alice')
            self.assertEqual(result['action'], action)

    @patch('dashboard.cognito_api._client')
    def test_rejects_unknown_action_without_calling_aws(self, client):
        with self.assertRaises(ValueError):
            change_user_state('pool-1', 'alice', 'delete')
        client.assert_not_called()

    @patch('dashboard.cognito_views.change_user_state')
    def test_state_endpoint(self, change):
        change.return_value = {'action': 'confirm'}
        response = self.client.post(reverse('dashboard:cognito-user-state', kwargs={'user_pool_id': 'pool-1', 'username': 'alice'}), data=json.dumps({'action': 'confirm'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        change.assert_called_once_with('pool-1', 'alice', 'confirm')

    @patch('dashboard.cognito_api._client')
    def test_public_client_explicitly_enables_password_flow(self, client):
        from .cognito_api import create_user_pool_client
        client.return_value.create_user_pool_client.return_value = {'UserPoolClient': {'ClientId': 'public-client'}}
        result = create_user_pool_client('pool-1', 'Browser app', generate_secret=False, explicit_auth_flows=['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'])
        client.return_value.create_user_pool_client.assert_called_once_with(
            UserPoolId='pool-1', ClientName='Browser app', GenerateSecret=False,
            ExplicitAuthFlows=['ALLOW_USER_PASSWORD_AUTH', 'ALLOW_REFRESH_TOKEN_AUTH'],
        )
        self.assertEqual(result['client_id'], 'public-client')
