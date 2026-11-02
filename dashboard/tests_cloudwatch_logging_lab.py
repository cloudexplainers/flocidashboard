"""Centralized logging lab: Floci delivers subscription matches to Kinesis and Firehose as gzip envelopes, evaluates the
alarms, and runs simple Logs Insights queries. These tests cover unpacking the envelopes, the checks, the policies, and
registration."""

import gzip
import importlib
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudwatch_logging_labs')


def _envelope(group, messages, message_type='DATA_MESSAGE'):
    return gzip.compress(json.dumps({
        'messageType': message_type, 'owner': lab.ACCOUNT, 'logGroup': group, 'logStream': lab.STREAM,
        'subscriptionFilters': [lab.ERROR_SUBSCRIPTION],
        'logEvents': [{'id': str(index), 'timestamp': index, 'message': message}
                      for index, message in enumerate(messages)]}).encode())


class EnvelopeTests(SimpleTestCase):
    def test_back_to_back_envelopes_are_split(self):
        lines = lab.log_lines('run-1')
        data = (_envelope(lab.GROUPS['orders'], lines['orders']) + b'\n'
                + _envelope(lab.GROUPS['payments'], lines['payments']) + b'\n')
        envelopes = lab.unpack_envelopes(data)
        self.assertEqual([item['logGroup'] for item in envelopes], list(lab.GROUPS.values()))
        events = lab.events_from(envelopes, 'run-1')
        self.assertEqual(len(events), lab.EXPECTED_EVENTS)
        self.assertEqual(lab.count_by([e for e in events if e['level'] == 'ERROR'], 'service'), lab.EXPECTED_ERRORS)

    def test_other_runs_control_messages_and_plain_text_are_ignored(self):
        old = lab.log_lines('run-0')['orders']
        envelopes = lab.unpack_envelopes(
            _envelope(lab.GROUPS['orders'], [*old, 'not json', lab.log_lines('run-1')['orders'][0]])
            + _envelope(lab.GROUPS['orders'], ['CWL CONTROL MESSAGE'], message_type='CONTROL_MESSAGE'))
        events = lab.events_from(envelopes, 'run-1')
        self.assertEqual([event['request'] for event in events], ['orders-1'])
        self.assertEqual(events[0]['logGroup'], lab.GROUPS['orders'])

    def test_traffic_fires_only_the_payments_alarm(self):
        self.assertGreaterEqual(lab.EXPECTED_ERRORS['payments'], lab.ALARM_THRESHOLD)
        self.assertLess(lab.EXPECTED_ERRORS['orders'], lab.ALARM_THRESHOLD)

    def test_insights_rows_become_dicts(self):
        results = [[{'field': 'service', 'value': 'orders'}, {'field': 'level', 'value': 'ERROR'}]]
        self.assertEqual(lab.insights_rows(results), [{'service': 'orders', 'level': 'ERROR'}])


class PolicyTests(SimpleTestCase):
    def test_key_policy_limits_logs_to_the_lab_groups(self):
        statement = lab.key_policy()['Statement'][1]
        self.assertEqual(statement['Principal'], {'Service': f'logs.{lab.REGION}.amazonaws.com'})
        arn = statement['Condition']['ArnLike']['kms:EncryptionContext:aws:logs:arn']
        self.assertTrue(arn.endswith('log-group:/lab-central/*'))

    def test_destination_policy_lets_the_workload_account_subscribe(self):
        statement = lab.destination_policy('arn:dest')['Statement'][0]
        self.assertEqual((statement['Principal']['AWS'], statement['Action']), (lab.ACCOUNT, 'logs:PutSubscriptionFilter'))

    def test_incident_rule_covers_every_service_alarm(self):
        for alarm in lab.ALARMS.values():
            self.assertIn(f'ALARM("{alarm}")', lab.INCIDENT_RULE)
        self.assertIn(' OR ', lab.INCIDENT_RULE)

    def test_lifecycle_rule_archives_then_expires(self):
        self.assertEqual([item['StorageClass'] for item in lab.LIFECYCLE_RULE['Transitions']], ['STANDARD_IA', 'GLACIER'])
        self.assertEqual(lab.LIFECYCLE_RULE['Expiration'], {'Days': 365})


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_shipped_check_needs_only_errors_from_both_services(self):
        lab._marker('ship-logs', {'run': 'r', 'streamedByService': lab.EXPECTED_ERRORS, 'streamedOtherLevels': 0})
        self.assertTrue(lab._check_shipped()[0])
        lab._marker('ship-logs', {'run': 'r', 'streamedByService': lab.EXPECTED_ERRORS, 'streamedOtherLevels': 2})
        self.assertFalse(lab._check_shipped()[0])
        lab._marker('ship-logs', {'run': 'r', 'streamedByService': {'payments': 3}, 'streamedOtherLevels': 0})
        self.assertFalse(lab._check_shipped()[0])

    def test_alarm_check_needs_payments_and_incident_but_not_orders(self):
        states = {lab.ALARMS['payments']: 'ALARM', lab.INCIDENT_ALARM: 'ALARM', lab.ALARMS['orders']: 'OK'}
        lab._marker('error-alarm', {'states': states})
        self.assertTrue(lab._check_alarm()[0])
        lab._marker('error-alarm', {'states': {**states, lab.ALARMS['orders']: 'ALARM'}})
        self.assertFalse(lab._check_alarm()[0])
        lab._marker('error-alarm', {'states': {**states, lab.INCIDENT_ALARM: 'OK'}})
        self.assertFalse(lab._check_alarm()[0])

    def test_archive_and_insights_checks_count_this_run(self):
        lab._marker('archive-delivered', {'archived': lab.EXPECTED_EVENTS})
        self.assertTrue(lab._check_delivered()[0])
        lab._marker('archive-delivered', {'archived': lab.EXPECTED_EVENTS - 1})
        self.assertFalse(lab._check_delivered()[0])
        lab._marker('insights-query', {'errorsByService': lab.EXPECTED_ERRORS})
        self.assertTrue(lab._check_insights()[0])

    def test_central_check_reads_the_stored_destination_policy(self):
        policy = json.dumps(lab.destination_policy('arn:dest'))
        lab._marker('central-account', {'destination': {'accessPolicy': policy}})
        with patch.object(lab, '_safe_call', return_value={'Name': lab.METRIC_STREAM}):
            self.assertTrue(lab._check_central()[0])
        with patch.object(lab, '_safe_call', return_value=None):
            self.assertFalse(lab._check_central()[0])
        lab._marker('central-account', {'destination': {}})
        self.assertFalse(lab._check_central()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('centralized-logging', [item['key'] for item in labs_for_service('cloudwatch')])
        tags = LAB_EXAM_TAGS[('cloudwatch', 'centralized-logging')]
        self.assertIn({'exam': 'DOP-C02', 'task': '4.1', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'centralized-logging')
        self.assertIn(('cloudwatch', 'centralized-logging'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 9)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
