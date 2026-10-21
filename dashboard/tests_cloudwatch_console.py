import json
from pathlib import Path
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class CloudWatchPageTemplateTests(SimpleTestCase):
    def test_cloudwatch_service_page_keeps_readonly_inventory_and_embeds_console(self):
        response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': 'cloudwatch'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2>CloudWatch inventory</h2>', html=True)
        self.assertContains(response, 'id="cloudwatch-summary"')
        self.assertContains(response, 'id="cloudwatch-console-root"')
        self.assertNotContains(response, 'id="cloudwatch-grid"')
        self.assertContains(response, 'dashboard/cloudwatch-console.css')
        self.assertContains(response, 'dashboard/service-console.js')
        self.assertContains(response, 'dashboard/cloudwatch-console.js')


class CloudWatchStaticConsoleTests(SimpleTestCase):
    def test_log_groups_and_streams_use_shared_collection_helper(self):
        script = Path(__file__).resolve().parent / 'static' / 'dashboard' / 'cloudwatch-console.js'
        source = script.read_text()

        self.assertIn('consoleUi.renderCollection({', source)
        self.assertIn("filterPlaceholder: 'Find log groups'", source)
        self.assertIn('state.groupFilterText = value', source)
        self.assertIn("emptyFilteredTitle: 'No log groups match this filter.'", source)
        self.assertIn("filterPlaceholder: 'Find streams'", source)
        self.assertIn('state.streamFilterText = value', source)
        self.assertIn("emptyFilteredTitle: 'No streams match this filter.'", source)


class CloudWatchLogsApiTests(SimpleTestCase):
    @patch('dashboard.cloudwatch_logs_views.list_log_streams')
    def test_list_log_streams_success(self, streams_mock):
        streams_mock.return_value = {
            'log_group_name': '/aws/lambda/worker',
            'streams': [{'logStreamName': 'latest'}],
        }

        response = self.client.post(
            reverse('dashboard:cloudwatch-log-streams'),
            data=json.dumps({'log_group_name': '/aws/lambda/worker', 'limit': 25}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['streams']), 1)
        streams_mock.assert_called_once_with('/aws/lambda/worker', limit=25)

    @patch('dashboard.cloudwatch_logs_views.get_log_events')
    def test_get_log_events_success(self, events_mock):
        events_mock.return_value = {
            'log_group_name': '/aws/lambda/worker',
            'log_stream_name': 'latest',
            'events': [{'message': 'hello'}],
        }

        response = self.client.post(
            reverse('dashboard:cloudwatch-log-events'),
            data=json.dumps({
                'log_group_name': '/aws/lambda/worker',
                'log_stream_name': 'latest',
                'limit': 50,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['events'][0]['message'], 'hello')
        events_mock.assert_called_once_with(
            '/aws/lambda/worker',
            'latest',
            limit=50,
            start_time=None,
        )

    @patch('dashboard.cloudwatch_logs_views.start_logs_insights_query')
    def test_start_logs_insights_query_success(self, query_mock):
        query_mock.return_value = {
            'log_group_name': '/aws/lambda/worker',
            'query_id': 'query-1',
            'query_string': 'fields @message',
        }

        response = self.client.post(
            reverse('dashboard:cloudwatch-logs-insights-query'),
            data=json.dumps({
                'log_group_name': '/aws/lambda/worker',
                'query_string': 'fields @message',
                'limit': 25,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['query_id'], 'query-1')
        query_mock.assert_called_once_with(
            '/aws/lambda/worker',
            'fields @message',
            start_time=None,
            end_time=None,
            limit=25,
        )

    @patch('dashboard.cloudwatch_logs_views.get_logs_insights_query_results')
    def test_get_logs_insights_query_results_success(self, results_mock):
        results_mock.return_value = {
            'query_id': 'query-1',
            'status': 'Complete',
            'results': [[{'field': '@message', 'value': 'hello'}]],
        }

        response = self.client.post(
            reverse('dashboard:cloudwatch-logs-insights-results'),
            data=json.dumps({'query_id': 'query-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Complete')
        results_mock.assert_called_once_with('query-1')


class CloudWatchMetricStatisticsTests(SimpleTestCase):
    @patch('dashboard.aws.FlociClientFactory')
    def test_statistics_preserves_dimensions_and_sorts_datapoints(self, factory):
        from datetime import datetime, timezone
        first = datetime(2026, 10, 20, 10, tzinfo=timezone.utc)
        second = datetime(2026, 10, 20, 11, tzinfo=timezone.utc)
        client = factory.return_value.client.return_value
        client.get_metric_statistics.return_value = {
            'Datapoints': [{'Timestamp': second, 'Average': 2}, {'Timestamp': first, 'Average': 1}],
        }
        response = self.client.get(reverse('dashboard:cloudwatch-metric-statistics'), {
            'namespace': 'Test/App', 'metric_name': 'Latency', 'hours': 3,
            'dimensions': json.dumps([{'Name': 'Service', 'Value': 'worker'}]),
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual([point['Average'] for point in response.json()['datapoints']], [1, 2])
        arguments = client.get_metric_statistics.call_args.kwargs
        self.assertEqual(arguments['Dimensions'], [{'Name': 'Service', 'Value': 'worker'}])
        self.assertEqual(arguments['Statistics'], ['Average'])
        self.assertEqual(arguments['EndTime'] - arguments['StartTime'], timedelta(hours=3))

    @patch('dashboard.aws.FlociClientFactory')
    def test_invalid_requests_do_not_call_aws(self, factory):
        for params in [
            {},
            {'namespace': 'Test/App', 'metric_name': 'Latency', 'hours': 168},
            {'namespace': 'Test/App', 'metric_name': 'Latency', 'dimensions': '{}'},
            {'namespace': 'Test/App', 'metric_name': 'Latency', 'dimensions': '[{"Name": "Service"}]'},
        ]:
            with self.subTest(params=params):
                response = self.client.get(reverse('dashboard:cloudwatch-metric-statistics'), params)
                self.assertEqual(response.status_code, 400)
        factory.assert_not_called()


class CloudWatchConsoleOperationsTests(SimpleTestCase):
    def request(self, path, payload, method='post'):
        return getattr(self.client, method)('/api/cloudwatch/' + path, data=json.dumps(payload), content_type='application/json')

    @patch('dashboard.cloudwatch_console_views.FlociClientFactory')
    def test_alarm_zero_threshold_dimensions_and_evaluation_settings(self, factory):
        response = self.request('alarms/', {
            'alarm_name': 'Failures', 'metric_name': 'Errors', 'namespace': 'App', 'threshold': 0,
            'comparison_operator': 'GreaterThanThreshold', 'period': 60, 'evaluation_periods': 3,
            'datapoints_to_alarm': 2, 'statistic': 'Sum', 'treat_missing_data': 'notBreaching',
            'dimensions': [{'Name': 'Service', 'Value': 'worker'}],
        })
        self.assertEqual(response.status_code, 200)
        arguments = factory.return_value.client.return_value.put_metric_alarm.call_args.kwargs
        self.assertEqual(arguments['Threshold'], 0)
        self.assertEqual(arguments['Statistic'], 'Sum')
        self.assertEqual(arguments['DatapointsToAlarm'], 2)
        self.assertEqual(arguments['EvaluationPeriods'], 3)
        self.assertEqual(arguments['Dimensions'], [{'Name': 'Service', 'Value': 'worker'}])

    @patch('dashboard.cloudwatch_console_views.FlociClientFactory')
    def test_invalid_alarms_never_call_aws(self, factory):
        baseline = dict(alarm_name='Errors', metric_name='Errors', namespace='App', threshold=0,
                        comparison_operator='GreaterThanThreshold', period=60, evaluation_periods=1)
        for overrides in [{'threshold': None}, {'threshold': float('inf')}, {'threshold': True},
                          {'period': 17}, {'evaluation_periods': 1.5}, {'datapoints_to_alarm': 2},
                          {'statistic': 'Invented'}, {'comparison_operator': ''}, {'dimensions': [{}]}]:
            response = self.request('alarms/', {**baseline, **overrides})
            self.assertEqual(response.status_code, 400, response.content)
        factory.assert_not_called()

    @patch('dashboard.cloudwatch_console_views.FlociClientFactory')
    def test_state_override_requires_reason_and_preserves_insufficient_data(self, factory):
        response = self.request('alarms/state/', {'alarm_name': 'Errors', 'state_value': 'INSUFFICIENT_DATA', 'state_reason': 'Manual test'})
        self.assertEqual(response.status_code, 200)
        factory.return_value.client.return_value.set_alarm_state.assert_called_once_with(AlarmName='Errors', StateValue='INSUFFICIENT_DATA', StateReason='Manual test')
        response = self.request('alarms/state/', {'alarm_name': 'Errors', 'state_value': 'OK', 'state_reason': ''})
        self.assertEqual(response.status_code, 400)

    @patch('dashboard.cloudwatch_console_views.FlociClientFactory')
    def test_delete_alarm_uses_explicit_delete_method(self, factory):
        response = self.request('alarms/', {'alarm_name': 'Errors'}, 'delete')
        self.assertEqual(response.status_code, 200)
        factory.return_value.client.return_value.delete_alarms.assert_called_once_with(AlarmNames=['Errors'])

    @patch('dashboard.cloudwatch_console_views.FlociClientFactory')
    def test_retention_remove_and_update_are_distinct_operations(self, factory):
        response = self.request('retention/', {'log_group_name': '/app/worker'}, 'delete')
        self.assertEqual(response.status_code, 200)
        client = factory.return_value.client.return_value
        client.delete_retention_policy.assert_called_once_with(logGroupName='/app/worker')
        client.put_retention_policy.assert_not_called()
        response = self.request('retention/', {'log_group_name': '/app/worker', 'retention_in_days': 7}, 'put')
        self.assertEqual(response.status_code, 200)
        client.put_retention_policy.assert_called_once_with(logGroupName='/app/worker', retentionInDays=7)

    @patch('dashboard.cloudwatch_console_views.FlociClientFactory')
    def test_stream_creation_preserves_log_group_and_stream_names(self, factory):
        response = self.request('log-streams/create/', {'log_group_name': '/app/worker', 'log_stream_name': '2026/10/test'})
        self.assertEqual(response.status_code, 200)
        factory.return_value.client.return_value.create_log_stream.assert_called_once_with(logGroupName='/app/worker', logStreamName='2026/10/test')

    @patch('dashboard.cloudwatch_console_views.FlociClientFactory')
    def test_metric_zero_is_published_and_nonfinite_rejected(self, factory):
        response = self.request('metric-data/', {'namespace': 'App', 'metric_name': 'Errors', 'value': 0, 'unit': 'Count'})
        self.assertEqual(response.status_code, 200)
        factory.return_value.client.return_value.put_metric_data.assert_called_once_with(Namespace='App', MetricData=[{'MetricName': 'Errors', 'Value': 0, 'Unit': 'Count'}])
        response = self.request('metric-data/', {'namespace': 'App', 'metric_name': 'Errors', 'value': 'NaN'})
        self.assertEqual(response.status_code, 400)

    @patch('dashboard.aws.FlociClientFactory')
    def test_statistics_uses_requested_statistic(self, factory):
        factory.return_value.client.return_value.get_metric_statistics.return_value = {'Datapoints': []}
        response = self.client.get('/api/cloudwatch/metric-statistics/', {'namespace': 'App', 'metric_name': 'Errors', 'statistic': 'Sum'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(factory.return_value.client.return_value.get_metric_statistics.call_args.kwargs['Statistics'], ['Sum'])
