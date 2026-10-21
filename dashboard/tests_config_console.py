import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class ConfigPageTemplateTests(SimpleTestCase):

    def test_config_registry_marks_service_interactive(self):
        service = get_service('config')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'put_config_rule' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_conformance_pack' for action in service.actions))


class ConfigActionsApiTests(SimpleTestCase):
    @patch('dashboard.config_views.put_config_rule')
    def test_put_config_rule_success(self, put_mock):
        put_mock.return_value = {'name': 's3-bucket-versioning'}
        rule = {'ConfigRuleName': 's3-bucket-versioning', 'Source': {'Owner': 'AWS'}}

        response = self.client.post(
            reverse('dashboard:config-rules'),
            data=json.dumps({'config_rule': rule}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 's3-bucket-versioning')
        put_mock.assert_called_once_with(rule)

    def test_put_config_rule_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:config-rules'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'config')
        self.assertEqual(response.json()['operation'], 'put_config_rule')

    @patch('dashboard.config_views.delete_config_rule')
    def test_delete_config_rule_success(self, delete_mock):
        delete_mock.return_value = {'name': 's3-bucket-versioning', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:config-rule-detail', kwargs={'rule_name': 's3-bucket-versioning'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('s3-bucket-versioning')

    @patch('dashboard.config_views.start_config_rules_evaluation')
    def test_start_rule_evaluation_success(self, evaluation_mock):
        evaluation_mock.return_value = {'rule_names': ['s3-bucket-versioning']}

        response = self.client.post(
            reverse('dashboard:config-rules-evaluation-start'),
            data=json.dumps({'rule_names': ['s3-bucket-versioning']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['rule_names'], ['s3-bucket-versioning'])
        evaluation_mock.assert_called_once_with(['s3-bucket-versioning'])

    @patch('dashboard.config_views.put_configuration_recorder')
    def test_put_configuration_recorder_success(self, put_mock):
        put_mock.return_value = {'name': 'default'}
        recorder = {'name': 'default', 'roleARN': 'arn:aws:iam::000000000000:role/config-role'}

        response = self.client.post(
            reverse('dashboard:config-recorders'),
            data=json.dumps({'configuration_recorder': recorder}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'default')
        put_mock.assert_called_once_with(recorder)

    @patch('dashboard.config_views.start_configuration_recorder')
    def test_start_recorder_success(self, start_mock):
        start_mock.return_value = {'name': 'default', 'recording': True}

        response = self.client.post(reverse('dashboard:config-recorder-start', kwargs={'recorder_name': 'default'}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['recording'])
        start_mock.assert_called_once_with('default')

    @patch('dashboard.config_views.stop_configuration_recorder')
    def test_stop_recorder_success(self, stop_mock):
        stop_mock.return_value = {'name': 'default', 'recording': False}

        response = self.client.post(reverse('dashboard:config-recorder-stop', kwargs={'recorder_name': 'default'}))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['recording'])
        stop_mock.assert_called_once_with('default')

    @patch('dashboard.config_views.put_delivery_channel')
    def test_put_delivery_channel_success(self, put_mock):
        put_mock.return_value = {'name': 'default'}
        channel = {'name': 'default', 's3BucketName': 'config-bucket'}

        response = self.client.post(
            reverse('dashboard:config-delivery-channels'),
            data=json.dumps({'delivery_channel': channel}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'default')
        put_mock.assert_called_once_with(channel)

    @patch('dashboard.config_views.put_conformance_pack')
    def test_put_conformance_pack_success(self, put_mock):
        put_mock.return_value = {'name': 'local-pack'}

        response = self.client.post(
            reverse('dashboard:config-conformance-packs'),
            data=json.dumps({'name': 'local-pack', 'template_body': 'Resources: {}', 'input_parameters': []}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'local-pack')
        put_mock.assert_called_once_with('local-pack', template_body='Resources: {}', template_s3_uri='', input_parameters=[])

    @patch('dashboard.config_views.delete_conformance_pack')
    def test_delete_conformance_pack_success(self, delete_mock):
        delete_mock.return_value = {'name': 'local-pack', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:config-conformance-pack-detail', kwargs={'pack_name': 'local-pack'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('local-pack')

    @patch('dashboard.config_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn:aws:config:us-east-1:000000000000:config-rule/config-rule-1'}
        payload = {
            'resource_arn': 'arn:aws:config:us-east-1:000000000000:config-rule/config-rule-1',
            'tags': [{'Key': 'env', 'Value': 'dev'}],
        }

        response = self.client.post(
            reverse('dashboard:config-tags'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_arn'], payload['resource_arn'])
        tag_mock.assert_called_once_with(payload['resource_arn'], payload['tags'])

    @patch('dashboard.config_views.untag_resource')
    def test_untag_resource_success(self, untag_mock):
        untag_mock.return_value = {'resource_arn': 'arn:aws:config:us-east-1:000000000000:config-rule/config-rule-1'}
        payload = {
            'resource_arn': 'arn:aws:config:us-east-1:000000000000:config-rule/config-rule-1',
            'tag_keys': ['env'],
        }

        response = self.client.delete(
            reverse('dashboard:config-tags'),
            data=json.dumps(payload),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['resource_arn'], payload['resource_arn'])
        untag_mock.assert_called_once_with(payload['resource_arn'], payload['tag_keys'])
