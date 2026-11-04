"""Fleet monitoring lab: Floci evaluates the alarms, delivers SNS notifications, and runs Application Auto Scaling from
an alarm, but has no CloudWatch agent and stores EC2 alarm actions only. These tests cover parsing what the instances
report, the agent's metric shapes, the notifications, the step policy, the checks, and registration."""

import importlib
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudwatch_fleet_monitoring_labs')


class CollectionTests(SimpleTestCase):
    def test_collection_output_is_parsed(self):
        self.assertEqual(lab.parse_collection('mem=5.6\ndisk=27\npids=1\nnoise\nbad=x'),
                         {'mem': 5.6, 'disk': 27.0, 'pids': 1.0})

    def test_agent_metrics_use_the_agent_dimensions(self):
        data = {item['MetricName']: item for item in lab.agent_metric_data('i-1', {'mem': 5, 'disk': 27, 'pids': 0})}
        self.assertEqual(set(data), {lab.MEM_METRIC, lab.DISK_METRIC, lab.PROC_METRIC})
        self.assertEqual(data[lab.MEM_METRIC]['Dimensions'], [{'Name': 'InstanceId', 'Value': 'i-1'}])
        names = [item['Name'] for item in data[lab.PROC_METRIC]['Dimensions']]
        self.assertEqual(names, ['InstanceId', 'pattern', 'pid_finder'])
        self.assertEqual(lab.agent_metric_data('i-1', {}), [])

    def test_process_count_ignores_zombies_and_the_running_shell(self):
        for script in (lab.COLLECT_SCRIPT, lab.START_SCRIPT, lab.STOP_SCRIPT):
            self.assertIn(f"grep -c '^{lab.PROCESS}'", script)
            self.assertNotIn('pgrep', script)

    def test_agent_config_collects_memory_disk_and_the_process(self):
        collected = lab.AGENT_CONFIG['metrics']['metrics_collected']
        self.assertEqual(collected['procstat'][0]['pattern'], lab.PROCESS)
        self.assertEqual(lab.AGENT_CONFIG['metrics']['namespace'], lab.AGENT_NAMESPACE)


class AlertTests(SimpleTestCase):
    def test_sns_envelopes_become_alarm_notifications(self):
        body = json.dumps({'Type': 'Notification', 'Message': json.dumps(
            {'AlarmName': 'a', 'NewStateValue': 'ALARM', 'NewStateReason': 'r'})})
        found = lab.notifications([{'Body': body}, {'Body': 'not json'}, {'Body': json.dumps({'Message': 'text'})}])
        self.assertEqual(found, [{'alarm': 'a', 'state': 'ALARM', 'reason': 'r'}])

    def test_step_policy_adds_more_tasks_for_a_bigger_breach(self):
        self.assertEqual(lab.desired_after_step(1, 50), 1)
        self.assertEqual(lab.desired_after_step(1, 150), 2)
        self.assertEqual(lab.desired_after_step(1, lab.BACKLOG_SPIKE), 3)
        self.assertEqual(lab.desired_after_step(3, 500), lab.MAX_WORKERS)

    def test_fleet_rule_covers_every_instance(self):
        for alarm in lab.DOWN_ALARMS.values():
            self.assertIn(f'ALARM("{alarm}")', lab.FLEET_RULE)

    def test_queue_policy_allows_only_the_topic(self):
        statement = lab.queue_policy('arn:queue', 'arn:topic')['Statement'][0]
        self.assertEqual(statement['Condition'], {'ArnEquals': {'aws:SourceArn': 'arn:topic'}})

    def test_dashboard_reads_another_region_and_account(self):
        body = lab.dashboard_body({'web-1': 'i-1', 'web-2': 'i-2'}, ['arn:alarm'])
        regions = {widget['properties'].get('region') for widget in body['widgets']}
        self.assertIn(lab.OTHER_REGION, regions)
        with patch.object(lab, '_dashboard', return_value=body):
            self.assertTrue(lab._check_dashboard()[0])
        body['widgets'] = [widget for widget in body['widgets'] if widget['type'] != 'alarm']
        with patch.object(lab, '_dashboard', return_value=body):
            self.assertFalse(lab._check_dashboard()[0])


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_failure_check_needs_the_failing_alarm_the_fleet_alarm_and_a_page(self):
        states = {lab.DOWN_ALARMS['web-2']: 'ALARM', lab.FLEET_ALARM: 'ALARM', lab.DOWN_ALARMS['web-1']: 'OK'}
        page = [{'alarm': lab.DOWN_ALARMS['web-2'], 'state': 'ALARM', 'reason': ''}]
        lab._marker('process-failure', {'states': states, 'notifications': page})
        self.assertTrue(lab._check_failure()[0])
        lab._marker('process-failure', {'states': {**states, lab.DOWN_ALARMS['web-1']: 'ALARM'}, 'notifications': page})
        self.assertFalse(lab._check_failure()[0])
        lab._marker('process-failure', {'states': states, 'notifications': []})
        self.assertFalse(lab._check_failure()[0])

    def test_restore_check_needs_ok_and_the_ok_notification(self):
        states = {lab.DOWN_ALARMS['web-2']: 'OK', lab.FLEET_ALARM: 'OK'}
        lab._marker('restore-service', {'states': states, 'notifications': [
            {'alarm': lab.DOWN_ALARMS['web-2'], 'state': 'OK', 'reason': ''}]})
        self.assertTrue(lab._check_restored()[0])
        lab._marker('restore-service', {'states': states, 'notifications': []})
        self.assertFalse(lab._check_restored()[0])

    def test_fleet_check_needs_the_process_on_both_instances(self):
        instances = {'web-1': 'i-1', 'web-2': 'i-2'}
        with patch.object(lab, '_instance_ids', return_value=instances):
            lab._marker('launch-fleet', {'instances': instances, 'processes': {'web-1': 1, 'web-2': 1}})
            self.assertTrue(lab._check_fleet()[0])
            lab._marker('launch-fleet', {'instances': instances, 'processes': {'web-1': 1, 'web-2': 0}})
            self.assertFalse(lab._check_fleet()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('fleet-monitoring', [item['key'] for item in labs_for_service('cloudwatch')])
        tags = LAB_EXAM_TAGS[('cloudwatch', 'fleet-monitoring')]
        self.assertIn({'exam': 'SOA-C03', 'task': '1.1', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'cloudwatch-agent-alarms')
        self.assertIn(('cloudwatch', 'fleet-monitoring'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
