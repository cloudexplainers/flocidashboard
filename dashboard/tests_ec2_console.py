import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .ec2_api import delete_network_acl_entry, put_network_acl_entry
from .services import get_service


class EC2ConsoleTests(SimpleTestCase):

    def test_ec2_registry_actions(self):
        service = get_service('ec2')
        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        action_names = {action.name for action in service.actions}
        self.assertTrue({
            'run_instances',
            'start_instance',
            'stop_instance',
            'reboot_instance',
            'terminate_instance',
            'create_security_group',
            'delete_security_group',
            'import_key_pair',
            'create_vpc',
            'delete_vpc',
            'create_subnet',
            'delete_subnet',
            'put_network_acl_entry',
            'delete_network_acl_entry',
        } <= action_names)

    @patch('dashboard.ec2_views.put_network_acl_entry')
    def test_put_network_acl_entry_endpoint(self, put_mock):
        put_mock.return_value = {
            'network_acl_id': 'acl-123',
            'entry': {'rule_number': 100, 'cidr': '10.0.0.0/16'},
            'replaced': False,
        }

        response = self.client.put(
            reverse('dashboard:ec2-network-acl-entries', kwargs={'network_acl_id': 'acl-123'}),
            data=json.dumps({
                'entry': {'rule_number': 100, 'cidr': '10.0.0.0/16'},
                'replace': False,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['network_acl_id'], 'acl-123')
        put_mock.assert_called_once_with('acl-123', {'rule_number': 100, 'cidr': '10.0.0.0/16'}, replace=False)

    @patch('dashboard.ec2_views.delete_network_acl_entry')
    def test_delete_network_acl_entry_endpoint(self, del_mock):
        del_mock.return_value = {
            'network_acl_id': 'acl-123',
            'rule_number': 100,
            'egress': False,
            'deleted': True,
        }

        response = self.client.delete(
            reverse('dashboard:ec2-network-acl-entries', kwargs={'network_acl_id': 'acl-123'}),
            data=json.dumps({'rule_number': 100, 'egress': False}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        del_mock.assert_called_once_with('acl-123', 100, egress=False)

    @patch('dashboard.ec2_api._ec2_client')
    def test_put_network_acl_entry_api_validation(self, client_mock):
        client = MagicMock()
        client_mock.return_value = client

        # Missing rule_number raises ValueError
        with self.assertRaises(ValueError):
            put_network_acl_entry('acl-123', {'cidr': '10.0.0.0/16'})

        # Invalid rule_number raises ValueError
        with self.assertRaises(ValueError):
            put_network_acl_entry('acl-123', {'rule_number': 'not-an-int', 'cidr': '10.0.0.0/16'})

        # Valid entry calls create_network_acl_entry
        res = put_network_acl_entry('acl-123', {'rule_number': 100, 'cidr': '10.0.0.0/16'})
        self.assertEqual(res['network_acl_id'], 'acl-123')
        client.create_network_acl_entry.assert_called_once()

    @patch('dashboard.ec2_api._ec2_client')
    def test_delete_network_acl_entry_api_validation(self, client_mock):
        client = MagicMock()
        client_mock.return_value = client

        # Missing rule_number raises ValueError
        with self.assertRaises(ValueError):
            delete_network_acl_entry('acl-123', None)

        # Invalid rule_number raises ValueError
        with self.assertRaises(ValueError):
            delete_network_acl_entry('acl-123', 'invalid-int')

        # Valid rule calls delete_network_acl_entry
        res = delete_network_acl_entry('acl-123', 100)
        self.assertTrue(res['deleted'])
        client.delete_network_acl_entry.assert_called_once_with(NetworkAclId='acl-123', RuleNumber=100, Egress=False)
