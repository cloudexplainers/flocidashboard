"""Serverless observability lab: an order function with JSON logs, metric filters, traffic, metrics, Logs Insights, an
alarm, a dashboard, and active tracing. The fake runs the real handler (with a fake boto3 for PutMetricData), applies the
lab's two JSON metric filters to its output, and behaves like Floci 2.3.0: no EMF extraction, no stats queries, alarms
that reach ALARM without running their actions (newer Floci publishes them: notifies=True), and no X-Ray."""

import copy
import importlib
import io
import json
import sys
import types
import uuid
from contextlib import redirect_stdout
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudwatch_observability_labs')

SERVICE = 'cloudwatch'
LAB = 'serverless-observability'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}},
                       'Operation')


class Payload:
    def __init__(self, body):
        self.body = body

    def read(self):
        return json.dumps(self.body).encode()


class FakeAws:
    def __init__(self, emf=False, stats=False, alarm_fires=True, filters_publish=True, notifies=False):
        self.emf, self.stats, self.alarm_fires, self.filters_publish = emf, stats, alarm_fires, filters_publish
        self.notifies = notifies
        self.queues: dict[str, list[dict]] = {}
        self.queue_attributes: dict[str, dict] = {}
        self.subscriptions: list[dict] = []
        self.groups: dict[str, dict] = {}
        self.events: list[dict] = []
        self.filters: dict[str, dict] = {}
        self.roles: dict[str, dict] = {}
        self.role_policies: dict[tuple[str, str], str] = {}
        self.function = None
        self.datapoints: list[tuple[str, str, float, tuple]] = []   # namespace, metric, value, dimensions
        self.alarms: dict[str, dict] = {}
        self.topics: set[str] = set()
        self.dashboards: dict[str, str] = {}
        self.queries: dict[str, str] = {}

    # Logs
    def create_log_group(self, logGroupName):
        self.groups[logGroupName] = {'logGroupName': logGroupName}

    def describe_log_groups(self, logGroupNamePrefix):
        return {'logGroups': [copy.deepcopy(group) for name, group in self.groups.items()
                              if name.startswith(logGroupNamePrefix)]}

    def put_retention_policy(self, logGroupName, retentionInDays):
        self.groups[logGroupName]['retentionInDays'] = retentionInDays

    def delete_log_group(self, logGroupName):
        if logGroupName not in self.groups:
            raise client_error('ResourceNotFoundException')
        del self.groups[logGroupName]
        self.filters.clear()

    def put_metric_filter(self, logGroupName, filterName, filterPattern, metricTransformations):
        self.filters[filterName] = {'filterName': filterName, 'filterPattern': filterPattern,
                                    'metricTransformations': metricTransformations}

    def describe_metric_filters(self, logGroupName):
        if logGroupName not in self.groups:
            raise client_error('ResourceNotFoundException')
        return {'metricFilters': copy.deepcopy(list(self.filters.values()))}

    def _ingest(self, message):
        self.events.append({'message': message, 'timestamp': 0})
        try:
            line = json.loads(message)
        except ValueError:
            return
        if not self.filters_publish or not isinstance(line, dict):
            return
        if lab.ERRORS_FILTER in self.filters and line.get('level') == 'ERROR':
            self.datapoints.append((lab.NAMESPACE, lab.ERRORS_METRIC, 1.0, ()))
        if lab.LATENCY_FILTER in self.filters and (line.get('latency_ms') or 0) > 0:
            self.datapoints.append((lab.NAMESPACE, lab.LATENCY_METRIC, float(line['latency_ms']), ()))
        if self.emf and '_aws' in line:
            self.datapoints.append((lab.EMF_NAMESPACE, 'OrderValue', line['OrderValue'], ()))

    def filter_log_events(self, logGroupName, startTime=0, filterPattern='', nextToken=None):
        term = filterPattern.strip('"')
        return {'events': [event for event in self.events if term in event['message']]}

    def start_query(self, logGroupName, startTime, endTime, queryString):
        query_id = uuid.uuid4().hex
        self.queries[query_id] = queryString
        return {'queryId': query_id}

    def get_query_results(self, queryId):
        query = self.queries[queryId]
        if query.startswith('stats'):
            if not self.stats:
                return {'status': 'Failed', 'results': []}
            return {'status': 'Complete', 'results': [[{'field': 'level', 'value': 'ERROR'}]]}
        rows = []
        for event in self.events:
            line = json.loads(event['message']) if event['message'].startswith('{') else {}
            if line.get('level') == 'ERROR':
                rows.append([{'field': 'order_id', 'value': line['order_id']},
                             {'field': 'reason', 'value': line['reason']},
                             {'field': '@ptr', 'value': 'x'}])
        return {'status': 'Complete', 'results': rows[:20]}

    # IAM
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': self.roles[RoleName]}

    def create_role(self, RoleName, **kwargs):
        self.roles[RoleName] = {'RoleName': RoleName}

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.role_policies[(RoleName, PolicyName)] = PolicyDocument

    def get_role_policy(self, RoleName, PolicyName):
        if (RoleName, PolicyName) not in self.role_policies:
            raise client_error('NoSuchEntity')
        return {'PolicyDocument': json.loads(self.role_policies[(RoleName, PolicyName)])}

    def delete_role_policy(self, RoleName, PolicyName):
        if (RoleName, PolicyName) not in self.role_policies:
            raise client_error('NoSuchEntity')
        del self.role_policies[(RoleName, PolicyName)]

    def delete_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        del self.roles[RoleName]

    # Lambda
    def get_function_configuration(self, FunctionName):
        if not self.function:
            raise client_error('ResourceNotFoundException')
        return copy.deepcopy(self.function)

    def create_function(self, FunctionName, Code, Description='', **settings):
        self.function = {'FunctionName': FunctionName, 'State': 'Active', 'LastUpdateStatus': 'Successful', **settings}

    def update_function_code(self, FunctionName, ZipFile):
        pass

    def update_function_configuration(self, FunctionName, **settings):
        self.function.update(settings)

    def delete_function(self, FunctionName):
        if not self.function:
            raise client_error('ResourceNotFoundException')
        self.function = None

    def _put_from_function(self, Namespace, MetricData):
        for item in MetricData:
            dimensions = tuple((d['Name'], d['Value']) for d in item.get('Dimensions', []))
            self.datapoints.append((Namespace, item['MetricName'], float(item['Value']), dimensions))

    def invoke(self, FunctionName, Payload):
        fake_boto3 = types.ModuleType('boto3')
        fake_boto3.client = lambda name: types.SimpleNamespace(put_metric_data=self._put_from_function)
        namespace: dict = {}
        output = io.StringIO()
        environment = (self.function.get('Environment') or {}).get('Variables') or {}
        with patch.dict(sys.modules, {'boto3': fake_boto3}), patch.dict('os.environ', environment), \
                redirect_stdout(output):
            exec(compile(lab.ORDERS_SOURCE, 'orders.py', 'exec'), namespace)
            body = namespace['handler'](json.loads(Payload), types.SimpleNamespace(aws_request_id=uuid.uuid4().hex))
        for message in output.getvalue().splitlines():
            self._ingest(message)
        self._evaluate_alarms()
        return {'StatusCode': 200, 'Payload': globals()['Payload'](body)}

    # CloudWatch
    def get_metric_statistics(self, Namespace, MetricName, Dimensions, StartTime, EndTime, Period, Statistics):
        dimensions = tuple((d['Name'], d['Value']) for d in Dimensions)
        values = [value for ns, metric, value, dims in self.datapoints
                  if ns == Namespace and metric == MetricName and dims == dimensions]
        if not values:
            return {'Datapoints': []}
        statistic = Statistics[0]
        result = {'Sum': sum(values), 'Maximum': max(values), 'Average': sum(values) / len(values)}[statistic]
        return {'Datapoints': [{statistic: result}]}

    def list_metrics(self, Namespace):
        names = {metric for ns, metric, _, _ in self.datapoints if ns == Namespace}
        return {'Metrics': [{'Namespace': Namespace, 'MetricName': name} for name in names]}

    def put_metric_alarm(self, AlarmName, **settings):
        self.alarms[AlarmName] = {'AlarmName': AlarmName, 'StateValue': 'INSUFFICIENT_DATA', **settings}
        self.burst_start = len([1 for _, metric, _, _ in self.datapoints if metric == lab.ERRORS_METRIC])

    def _evaluate_alarms(self):
        for alarm in self.alarms.values():
            recent = len([1 for _, metric, _, _ in self.datapoints if metric == lab.ERRORS_METRIC]) - self.burst_start
            if self.alarm_fires and recent >= alarm['Threshold'] and alarm['StateValue'] != 'ALARM':
                alarm['StateValue'] = 'ALARM'
                if self.notifies:
                    self._notify(alarm)

    def _notify(self, alarm):
        message = json.dumps({'AlarmName': alarm['AlarmName'], 'NewStateValue': 'ALARM',
                              'NewStateReason': 'Threshold Crossed'})
        for subscription in self.subscriptions:
            if subscription['TopicArn'] in alarm.get('AlarmActions', []):
                name = subscription['Endpoint'].rsplit(':', 1)[-1]
                body = json.dumps({'Type': 'Notification', 'Subject': f'ALARM: "{alarm["AlarmName"]}"',
                                   'Message': message})
                self.queues.setdefault(name, []).append({'Body': body, 'ReceiptHandle': uuid.uuid4().hex})

    def describe_alarms(self, AlarmNames):
        return {'MetricAlarms': [copy.deepcopy(self.alarms[name]) for name in AlarmNames if name in self.alarms]}

    def delete_alarms(self, AlarmNames):
        for name in AlarmNames:
            self.alarms.pop(name, None)

    def put_dashboard(self, DashboardName, DashboardBody):
        self.dashboards[DashboardName] = DashboardBody
        return {'DashboardValidationMessages': []}

    def get_dashboard(self, DashboardName):
        if DashboardName not in self.dashboards:
            raise client_error('ResourceNotFound')
        return {'DashboardName': DashboardName, 'DashboardBody': self.dashboards[DashboardName]}

    def delete_dashboards(self, DashboardNames):
        for name in DashboardNames:
            self.dashboards.pop(name, None)

    # SNS
    def create_topic(self, Name):
        self.topics.add(Name)
        return {'TopicArn': lab.TOPIC_ARN}

    def list_topics(self):
        return {'Topics': [{'TopicArn': f'arn:aws:sns:us-east-1:000000000000:{name}'} for name in self.topics]}

    def delete_topic(self, TopicArn):
        self.topics.discard(TopicArn.rsplit(':', 1)[-1])
        self.subscriptions = [item for item in self.subscriptions if item['TopicArn'] != TopicArn]

    def subscribe(self, TopicArn, Protocol, Endpoint):
        self.subscriptions.append({'TopicArn': TopicArn, 'Protocol': Protocol, 'Endpoint': Endpoint})
        return {'SubscriptionArn': f'{TopicArn}:{uuid.uuid4()}'}

    def list_subscriptions_by_topic(self, TopicArn):
        return {'Subscriptions': [item for item in self.subscriptions if item['TopicArn'] == TopicArn]}

    # SQS
    def create_queue(self, QueueName, Attributes):
        self.queues.setdefault(QueueName, [])
        self.queue_attributes[QueueName] = Attributes
        return {'QueueUrl': f'http://floci:4566/000000000000/{QueueName}'}

    def get_queue_url(self, QueueName):
        if QueueName not in self.queues:
            raise client_error('AWS.SimpleQueueService.NonExistentQueue')
        return {'QueueUrl': f'http://floci:4566/000000000000/{QueueName}'}

    def receive_message(self, QueueUrl, MaxNumberOfMessages, WaitTimeSeconds):
        messages = self.queues.get(QueueUrl.rsplit('/', 1)[-1], [])
        return {'Messages': list(messages[:MaxNumberOfMessages])} if messages else {}

    def delete_message(self, QueueUrl, ReceiptHandle):
        name = QueueUrl.rsplit('/', 1)[-1]
        self.queues[name] = [item for item in self.queues.get(name, []) if item['ReceiptHandle'] != ReceiptHandle]

    def delete_queue(self, QueueUrl):
        name = QueueUrl.rsplit('/', 1)[-1]
        self.queues.pop(name, None)
        self.queue_attributes.pop(name, None)

    # X-Ray
    def get_trace_summaries(self, StartTime, EndTime):
        raise client_error('UnknownOperationException', 'Unknown operation')


class ServerlessObservabilityLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.use(FakeAws())
        sleeper = patch('dashboard.labs.cloudwatch_observability_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def use(self, fake):
        self.fake = fake
        patcher = patch('dashboard.labs.cloudwatch_observability_labs._client', side_effect=lambda name: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        waiter = patch('dashboard.labs.cloudwatch_observability_labs._wait',
                       side_effect=lambda predicate, timeout, delay=1.0: predicate())
        waiter.start()
        self.addCleanup(waiter.stop)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failures(self, results):
        return {key: result['verification']['message'] for key, result in results.items() if not result['verified']}

    def test_lab_is_registered_with_eight_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 8)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('DVA-C02', '4.2'), tasks)
        self.assertIn(('DVA-C02', '4.1'), tasks)
        self.assertIn(('SAA-C03', '2.2'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_every_step_verifies_twice_and_status_is_complete(self):
        self.assertEqual(self.failures(self.run_all()), {})
        self.assertEqual(self.failures(self.run_all()), {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_messages_report_floci_gaps(self):
        results = self.run_all()
        self.assertIn('9 INFO and 3 ERROR', results['send-traffic']['verification']['message'])
        self.assertIn('does not extract EMF', results['read-metrics']['verification']['message'])
        self.assertIn('did not run the stats query', results['logs-insights']['verification']['message'])
        self.assertIn('does not run alarm actions', results['error-alarm']['verification']['message'])
        self.assertIn('no X-Ray service', results['active-tracing']['verification']['message'])

    def test_published_alarm_notification_is_reported(self):
        self.use(FakeAws(notifies=True))
        results = self.run_all()
        self.assertEqual(self.failures(results), {})
        message = results['error-alarm']['verification']['message']
        self.assertIn(f'notification reached {lab.INBOX}', message)
        self.assertNotIn('does not run alarm actions', message)
        self.assertEqual(results['error-alarm']['json']['Notification']['NewStateValue'], 'ALARM')

    def test_rerun_recreates_the_alarm_and_subscribes_once(self):
        self.use(FakeAws(notifies=True))
        self.run_all()
        result = run_lab_step(SERVICE, LAB, 'error-alarm')
        self.assertIn(f'notification reached {lab.INBOX}', result['verification']['message'])
        self.assertEqual(len(self.fake.subscriptions), 1)
        policy = json.loads(self.fake.queue_attributes[lab.INBOX]['Policy'])
        self.assertEqual(policy['Statement'][0]['Condition']['ArnEquals']['aws:SourceArn'], lab.TOPIC_ARN)

    def test_aws_style_emf_and_stats_are_reported(self):
        self.use(FakeAws(emf=True, stats=True))
        results = self.run_all()
        self.assertEqual(self.failures(results), {})
        self.assertIn('extracted 1 metrics', results['read-metrics']['verification']['message'])
        self.assertIn('stats query returned', results['logs-insights']['verification']['message'])

    def test_logs_are_structured_json_with_request_ids(self):
        self.run_all()
        lines = [json.loads(event['message']) for event in self.fake.events]
        orders = [line for line in lines if line.get('level')]
        self.assertTrue(orders)
        self.assertTrue(all(line.get('request_id') and line.get('latency_ms') for line in orders))
        self.assertTrue(any('_aws' in line for line in lines))

    def test_filters_that_publish_nothing_fail_the_metrics_step(self):
        self.use(FakeAws(filters_publish=False))
        failures = self.failures(self.run_all())
        self.assertIn('read-metrics', failures)
        self.assertIn(lab.ERRORS_METRIC, failures['read-metrics'])

    def test_alarm_that_never_fires_fails(self):
        self.use(FakeAws(alarm_fires=False))
        failures = self.failures(self.run_all())
        self.assertEqual(list(failures), ['error-alarm'])
        self.assertIn('stayed', failures['error-alarm'])

    def test_alarm_without_ok_action_fails_status(self):
        self.run_all()
        self.fake.alarms[lab.ALARM]['OKActions'] = []
        failed = [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]
        self.assertEqual(failed, ['error-alarm'])

    def test_dashboard_has_metric_alarm_and_log_widgets(self):
        self.run_all()
        body = json.loads(self.fake.dashboards[lab.DASHBOARD])
        self.assertEqual({widget['type'] for widget in body['widgets']}, {'metric', 'alarm', 'log'})
        self.assertIn(lab.LOG_GROUP, json.dumps(body))

    def test_tracing_off_fails_status(self):
        self.run_all()
        self.fake.function['TracingConfig'] = {'Mode': 'PassThrough'}
        failed = [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]
        self.assertEqual(failed, ['active-tracing'])

    def test_steps_out_of_order_explain_what_is_missing(self):
        for step in ('metric-filters', 'send-traffic', 'read-metrics', 'logs-insights', 'error-alarm',
                     'active-tracing'):
            result = run_lab_step(SERVICE, LAB, step)
            self.assertFalse(result['verified'], step)
            self.assertIn('first', result['verification']['message'], step)

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertIsNone(self.fake.function)
        for store in (self.fake.groups, self.fake.filters, self.fake.roles, self.fake.role_policies,
                      self.fake.alarms, self.fake.topics, self.fake.dashboards, self.fake.queues):
            self.assertFalse(store)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
