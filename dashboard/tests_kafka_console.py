import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class KafkaPageTemplateTests(SimpleTestCase):

    def test_kafka_registry_marks_service_interactive(self):
        service = get_service('kafka')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_cluster' for action in service.actions))
        self.assertTrue(any(action.name == 'create_cluster_v2' for action in service.actions))
        self.assertTrue(any(action.name == 'get_bootstrap_brokers' for action in service.actions))


class KafkaActionsApiTests(SimpleTestCase):
    @patch('dashboard.kafka_views.create_cluster')
    def test_create_cluster_success(self, create_mock):
        broker_info = {'InstanceType': 'kafka.m5.large', 'ClientSubnets': ['subnet-1']}
        create_mock.return_value = {'name': 'orders', 'arn': 'arn:aws:kafka:local:000:cluster/orders/1'}

        response = self.client.post(
            reverse('dashboard:kafka-clusters'),
            data=json.dumps({
                'name': 'orders',
                'kafka_version': '3.6.1',
                'number_of_broker_nodes': 1,
                'broker_node_group_info': broker_info,
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'orders')
        create_mock.assert_called_once_with(
            'orders',
            kafka_version='3.6.1',
            number_of_broker_nodes=1,
            broker_node_group_info=broker_info,
            tags={'env': 'local'},
        )

    def test_create_cluster_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:kafka-clusters'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'kafka')
        self.assertEqual(response.json()['operation'], 'create_cluster')

    @patch('dashboard.kafka_views.create_cluster_v2')
    def test_create_cluster_v2_success(self, create_mock):
        create_mock.return_value = {'name': 'orders-v2', 'arn': 'arn:aws:kafka:local:000:cluster/orders-v2/1'}

        response = self.client.post(
            reverse('dashboard:kafka-clusters-v2'),
            data=json.dumps({
                'name': 'orders-v2',
                'kafka_version': '3.6.1',
                'number_of_broker_nodes': 1,
                'broker_node_group_info': {},
                'tags': {},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            'orders-v2',
            kafka_version='3.6.1',
            number_of_broker_nodes=1,
            broker_node_group_info={},
            tags={},
        )

    @patch('dashboard.kafka_views.delete_cluster')
    def test_delete_cluster_success(self, delete_mock):
        cluster_arn = 'arn:aws:kafka:local:000:cluster/orders/1'
        delete_mock.return_value = {'arn': cluster_arn}

        response = self.client.delete(reverse('dashboard:kafka-cluster-detail', kwargs={'cluster_arn': cluster_arn}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with(cluster_arn)

    @patch('dashboard.kafka_views.get_bootstrap_brokers')
    def test_get_bootstrap_brokers_success(self, bootstrap_mock):
        cluster_arn = 'arn:aws:kafka:local:000:cluster/orders/1'
        bootstrap_mock.return_value = {'arn': cluster_arn, 'bootstrap_brokers': {'BootstrapBrokerString': 'localhost:19092'}}

        response = self.client.get(reverse('dashboard:kafka-bootstrap-brokers', kwargs={'cluster_arn': cluster_arn}))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['bootstrap_brokers']['BootstrapBrokerString'], 'localhost:19092')
        bootstrap_mock.assert_called_once_with(cluster_arn)
