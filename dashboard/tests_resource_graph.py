from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .labs import eventbridge_application as app
from .resource_graph import eventbridge_application_graph
from .aws import eventbridge_inventory


class ResourceGraphApiTests(SimpleTestCase):
    @patch('dashboard.resource_graph_views.resource_graph')
    def test_graph_endpoint_returns_normalized_graph(self, resource_graph):
        resource_graph.return_value = {'scenario': 'eventbridge-application-spine', 'nodes': [], 'edges': [], 'summary': {}}
        response = self.client.get(reverse('dashboard:resource-graph-detail'), {'scenario': 'eventbridge-application-spine'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['scenario'], 'eventbridge-application-spine')
        resource_graph.assert_called_once_with('eventbridge-application-spine')

    def test_unknown_graph_scenario_is_rejected(self):
        response = self.client.get(reverse('dashboard:resource-graph-detail'), {'scenario': 'unknown'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'resource-graph')


class EventBridgeApplicationGraphTests(SimpleTestCase):
    @patch('dashboard.resource_graph._client')
    def test_graph_contains_evidence_statuses_and_deep_links(self, client):
        gateway, lam, iam, events, sqs, logs = [MagicMock() for _ in range(6)]
        clients = {'apigatewayv2': gateway, 'lambda': lam, 'iam': iam, 'events': events, 'sqs': sqs, 'logs': logs}
        client.side_effect = clients.get
        gateway.get_apis.return_value = {'Items': [{'Name': app.API_NAME, 'ApiId': 'api123'}]}
        gateway.get_integrations.return_value = {'Items': [{'IntegrationUri': app.PRODUCER_FUNCTION_ARN}]}
        lam.get_function.side_effect = lambda FunctionName: {'Configuration': {'FunctionName': FunctionName, 'Role': app.PRODUCER_ROLE_ARN if FunctionName == app.PRODUCER_FUNCTION else app.NOTIFIER_ROLE_ARN}}
        iam.get_role.return_value = {'Role': {}}
        events.describe_event_bus.return_value = {'Name': app.BUS}
        events.describe_rule.side_effect = lambda Name, EventBusName: {'Name': Name, 'State': 'DISABLED' if Name == app.PROCESSING_RULE else 'ENABLED', 'EventPattern': '{}'}
        target_by_rule = {
            app.PROCESSING_RULE: [{'Id': 'processing', 'Arn': app.QUEUE_ARNS[app.PROCESSING_QUEUE], 'InputTransformer': app.PROCESSING_TRANSFORMER}],
            app.AUDIT_RULE: [{'Id': 'audit', 'Arn': app.QUEUE_ARNS[app.AUDIT_QUEUE]}],
            app.NOTIFICATION_RULE: [{'Id': 'notification', 'Arn': app.NOTIFIER_FUNCTION_ARN}],
        }
        events.list_targets_by_rule.side_effect = lambda Rule, EventBusName: {'Targets': target_by_rule[Rule]}
        sqs.get_queue_url.side_effect = lambda QueueName: {'QueueUrl': f'http://localhost/{QueueName}'}
        logs.describe_log_groups.side_effect = lambda logGroupNamePrefix, limit: {
            'logGroups': [{'logGroupName': logGroupNamePrefix}],
        }

        graph = eventbridge_application_graph()

        self.assertEqual(graph['layers'], ['entrypoint', 'compute', 'routing', 'target', 'observability'])
        self.assertTrue(all({'id', 'service', 'kind', 'layer', 'status', 'href'} <= node.keys() for node in graph['nodes']))
        self.assertTrue(all(edge.get('evidence', {}).get('label') for edge in graph['edges']))
        processing_rule = next(node for node in graph['nodes'] if node['id'] == f'rule:{app.PROCESSING_RULE}')
        self.assertEqual(processing_rule['status'], 'disabled')
        dlq_edge = next(edge for edge in graph['edges'] if edge['id'] == 'processing-dlq')
        self.assertEqual(dlq_edge['status'], 'unsupported')
        self.assertIn('does not persist or enforce', dlq_edge['detail'])
        producer = next(node for node in graph['nodes'] if node['id'] == 'producer')
        self.assertEqual(producer['href'], f'/lambda?function={app.PRODUCER_FUNCTION}')

        target_by_rule[app.AUDIT_RULE][0]['Arn'] = app.QUEUE_ARNS[app.PROCESSING_QUEUE]
        lam.get_function.side_effect = lambda FunctionName: {
            'Configuration': {'FunctionName': FunctionName, 'Role': 'arn:aws:iam::000000000000:role/wrong-role'},
        }
        logs.describe_log_groups.side_effect = lambda logGroupNamePrefix, limit: {
            'logGroups': [{'logGroupName': f'{logGroupNamePrefix}-old'}],
        }

        miswired_graph = eventbridge_application_graph()

        miswired_edges = {edge['id']: edge for edge in miswired_graph['edges']}
        self.assertEqual(miswired_edges['audit-target']['status'], 'broken')
        self.assertEqual(miswired_edges['producer-role-edge']['status'], 'broken')
        self.assertEqual(miswired_edges['notifier-role-edge']['status'], 'broken')
        self.assertEqual(next(node for node in miswired_graph['nodes'] if node['id'] == 'producer-logs')['status'], 'broken')

    @patch('dashboard.resource_graph._client')
    def test_absent_resources_are_broken_instead_of_crashing(self, client):
        clients = {name: MagicMock() for name in ('apigatewayv2', 'lambda', 'iam', 'events', 'sqs', 'logs')}
        client.side_effect = clients.get
        clients['apigatewayv2'].get_apis.return_value = {'Items': []}
        for service in ('lambda', 'iam', 'events', 'sqs'):
            mock = clients[service]
            for method in ('get_function', 'get_role', 'describe_event_bus', 'describe_rule', 'list_targets_by_rule', 'get_queue_url'):
                getattr(mock, method).side_effect = ValueError('missing')
        clients['logs'].describe_log_groups.return_value = {'logGroups': []}

        graph = eventbridge_application_graph()

        self.assertGreater(graph['summary']['broken'], 0)
        self.assertEqual(next(node for node in graph['nodes'] if node['id'] == 'api')['status'], 'broken')


class EventBridgeInventoryRegressionTests(SimpleTestCase):
    @patch('dashboard.aws.FlociClientFactory')
    def test_non_pageable_list_event_buses_keeps_custom_bus(self, factory):
        events = MagicMock()
        factory.return_value.client.return_value = events
        events.can_paginate.return_value = False
        events.list_event_buses.return_value = {
            'EventBuses': [
                {'Name': app.BUS, 'Arn': f'arn:aws:events:us-east-1:000000000000:event-bus/{app.BUS}'},
                {'Name': 'default', 'Arn': 'arn:aws:events:us-east-1:000000000000:event-bus/default'},
            ],
        }
        events.describe_event_bus.side_effect = lambda Name: {'Name': Name}
        events.list_rules.return_value = {'Rules': []}

        inventory = eventbridge_inventory()

        self.assertEqual([bus['name'] for bus in inventory['event_buses']], [app.BUS, 'default'])
        events.get_paginator.assert_not_called()
