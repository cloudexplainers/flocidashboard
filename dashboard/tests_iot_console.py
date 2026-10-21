import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class IoTConsoleTests(SimpleTestCase):

    def test_iot_registry_actions(self):
        service = get_service('iot')
        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        action_names = {action.name for action in service.actions}
        self.assertTrue({
            'publish_mqtt_message',
            'create_thing',
            'delete_thing',
            'get_thing_shadow',
            'update_thing_shadow',
            'delete_thing_shadow',
            'create_topic_rule',
            'delete_topic_rule',
        } <= action_names)

    @patch('dashboard.iot_views.publish_mqtt_message')
    def test_mqtt_publish_endpoint(self, pub_mock):
        pub_mock.return_value = {
            'topic': 'sensors/temp/1',
            'qos': 1,
            'published': True,
            'payload_size_bytes': 32,
        }

        response = self.client.post(
            reverse('dashboard:iot-mqtt-publish'),
            data=json.dumps({
                'topic': 'sensors/temp/1',
                'qos': 1,
                'payload': {'temp': 24.5},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['published'])
        pub_mock.assert_called_once_with(
            topic='sensors/temp/1',
            payload={'temp': 24.5},
            qos=1,
        )

    @patch('dashboard.iot_views.get_thing_shadow')
    def test_get_thing_shadow_endpoint(self, shadow_mock):
        shadow_mock.return_value = {
            'thing_name': 'sensor-01',
            'shadow_name': None,
            'payload': {'state': {'reported': {'power': 'ON'}}},
        }

        response = self.client.get(
            reverse('dashboard:iot-thing-shadow', kwargs={'thing_name': 'sensor-01'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['payload']['state']['reported']['power'], 'ON')
        shadow_mock.assert_called_once_with('sensor-01', shadow_name=None)

    @patch('dashboard.iot_views.update_thing_shadow')
    def test_update_thing_shadow_endpoint(self, shadow_mock):
        shadow_mock.return_value = {
            'thing_name': 'sensor-01',
            'shadow_name': None,
            'payload': {'state': {'desired': {'power': 'OFF'}}},
        }

        response = self.client.post(
            reverse('dashboard:iot-thing-shadow', kwargs={'thing_name': 'sensor-01'}),
            data=json.dumps({
                'payload': {'state': {'desired': {'power': 'OFF'}}},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        shadow_mock.assert_called_once_with(
            'sensor-01',
            payload={'state': {'desired': {'power': 'OFF'}}},
            shadow_name=None,
        )

    @patch('dashboard.iot_views.delete_thing_shadow')
    def test_delete_thing_shadow_endpoint(self, shadow_mock):
        shadow_mock.return_value = {
            'thing_name': 'sensor-01',
            'shadow_name': None,
            'deleted': True,
        }

        response = self.client.delete(
            reverse('dashboard:iot-thing-shadow', kwargs={'thing_name': 'sensor-01'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        shadow_mock.assert_called_once_with('sensor-01', shadow_name=None)

    @patch('dashboard.iot_views.create_thing')
    def test_create_thing_endpoint(self, create_mock):
        create_mock.return_value = {
            'thing_name': 'sensor-01',
            'thing_arn': 'arn:aws:iot:us-east-1:000000000000:thing/sensor-01',
        }

        response = self.client.post(
            reverse('dashboard:iot-things'),
            data=json.dumps({
                'thing_name': 'sensor-01',
                'thing_type_name': 'SensorDevice',
                'attributes': {'location': 'lab'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            thing_name='sensor-01',
            thing_type_name='SensorDevice',
            attributes={'location': 'lab'},
        )

    @patch('dashboard.iot_views.delete_thing')
    def test_delete_thing_endpoint(self, del_mock):
        del_mock.return_value = {'thing_name': 'sensor-01', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:iot-thing-detail', kwargs={'thing_name': 'sensor-01'}),
        )

        self.assertEqual(response.status_code, 200)
        del_mock.assert_called_once_with('sensor-01')

    @patch('dashboard.iot_views.create_topic_rule')
    def test_create_topic_rule_endpoint(self, rule_mock):
        rule_mock.return_value = {'rule_name': 'temp_rule', 'sql': "SELECT * FROM 'sensors/#'"}

        response = self.client.post(
            reverse('dashboard:iot-topic-rules'),
            data=json.dumps({
                'rule_name': 'temp_rule',
                'sql': "SELECT * FROM 'sensors/#'",
                'description': 'Test rule',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        rule_mock.assert_called_once_with(
            rule_name='temp_rule',
            sql="SELECT * FROM 'sensors/#'",
            actions=None,
            description='Test rule',
            rule_disabled=False,
        )

    @patch('dashboard.iot_views.delete_topic_rule')
    def test_delete_topic_rule_endpoint(self, del_mock):
        del_mock.return_value = {'rule_name': 'temp_rule', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:iot-topic-rule-detail', kwargs={'rule_name': 'temp_rule'}),
        )

        self.assertEqual(response.status_code, 200)
        del_mock.assert_called_once_with('temp_rule')

    @patch('dashboard.iot_views.delete_thing_shadow')
    def test_delete_thing_shadow_endpoint_with_delete_method(self, del_shadow_mock):
        del_shadow_mock.return_value = {'thing_name': 'sensor-01', 'deleted': True, 'payload': None}

        response = self.client.delete(
            reverse('dashboard:iot-thing-shadow', kwargs={'thing_name': 'sensor-01'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        del_shadow_mock.assert_called_once_with('sensor-01', shadow_name=None)
