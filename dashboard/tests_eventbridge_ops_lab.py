"""Event-driven operations lab: Floci runs rules, input transformers, archives and replays, target dead-letter
queues, Lambda targets that call Run Command, and schedules, but stores Automation runbooks only and has no numeric
matching. These tests cover the patterns, the alert text, the dead letters, the checks, and registration."""

import importlib
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.eventbridge_ops_automation_labs')


class PatternTests(SimpleTestCase):
    def test_broken_pattern_is_case_sensitive_and_the_fix_is_not(self):
        detail = lab.disk_event('i-1', 'disk-1', 93)
        self.assertEqual(detail['severity'], 'CRITICAL')
        self.assertEqual(lab.BROKEN_PATTERN['detail']['severity'], ['critical'])
        self.assertEqual(lab.FIXED_PATTERN['detail']['severity'], [{'equals-ignore-case': 'critical'}])
        self.assertEqual(lab.sample_event(detail)['detail'], detail)

    def test_alert_text_matches_the_input_template(self):
        detail = lab.disk_event('i-1', 'disk-2', 93)
        text = lab.render_alert(detail)
        rendered = json.loads(lab.INPUT_TEMPLATE)
        for name, path in lab.INPUT_PATHS.items():
            rendered = rendered.replace(f'<{name}>', str(detail[path.rsplit('.', 1)[1]]))
        self.assertEqual(text, rendered)
        self.assertEqual(lab.alert_refs([text, {'trigger': 'x'}, 'noise']), ['disk-2'])

    def test_dead_letters_read_the_error_attributes(self):
        message = {'MessageAttributes': {'TARGET_ARN': {'StringValue': lab.TYPO_ARN},
                                         'ERROR_CODE': {'StringValue': 'NO_RESOURCE'}}}
        letter = lab.dead_letters([message, {}])
        self.assertEqual(letter[0]['TARGET_ARN'], lab.TYPO_ARN)
        self.assertEqual(letter[0]['ERROR_CODE'], 'NO_RESOURCE')
        self.assertEqual(letter[1]['TARGET_ARN'], '')

    def test_queue_policy_allows_only_rules_on_the_ops_bus(self):
        statement = lab.queue_policy('arn:queue')['Statement'][0]
        self.assertEqual(statement['Principal'], {'Service': 'events.amazonaws.com'})
        self.assertIn(f'rule/{lab.BUS}/', statement['Condition']['ArnLike']['aws:SourceArn'])

    def test_runbook_role_is_scoped(self):
        actions = [action for statement in lab.FUNCTION_POLICY['Statement'] for action in statement['Action']]
        self.assertEqual(sorted(actions), ['sqs:GetQueueUrl', 'sqs:SendMessage', 'ssm:GetCommandInvocation',
                                           'ssm:SendCommand'])
        self.assertEqual(lab.RUNBOOK_CONTENT['schemaVersion'], '0.3')
        self.assertEqual(lab.parse_body('{"a": 1}'), {'a': 1})
        self.assertEqual(lab.parse_body('text'), 'text')


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_silent_check_needs_no_delivery_and_a_failed_test(self):
        lab._marker('silent-rule', {'delivered': 0, 'brokenMatches': False})
        self.assertTrue(lab._check_silent()[0])
        lab._marker('silent-rule', {'delivered': 1, 'brokenMatches': False})
        self.assertFalse(lab._check_silent()[0])

    def test_replay_check_needs_the_missed_alert(self):
        lab._marker('replay-missed', {'state': 'COMPLETED', 'refs': [lab.MISSED_REF]})
        self.assertTrue(lab._check_replayed()[0])
        lab._marker('replay-missed', {'state': 'COMPLETED', 'refs': []})
        self.assertFalse(lab._check_replayed()[0])

    def test_target_check_needs_the_dead_letter_and_the_target_removed(self):
        lab._marker('failing-target', {'deadLetters': [{'TARGET_ARN': lab.TYPO_ARN, 'ERROR_CODE': 'NO_RESOURCE'}]})
        with patch.object(lab, '_targets', return_value=[]):
            self.assertTrue(lab._check_target()[0])
        with patch.object(lab, '_targets', return_value=[{'Arn': lab.TYPO_ARN}]):
            self.assertFalse(lab._check_target()[0])

    def test_function_check_needs_the_report_and_an_empty_cache(self):
        report = {'trigger': lab.RUNBOOK_REF, 'cleared': lab.CACHE_FILES}
        with patch.object(lab, '_function', return_value={'FunctionName': lab.FUNCTION}), \
                patch.object(lab, '_targets', return_value=[{'Arn': lab.FUNCTION_ARN}]):
            lab._marker('runbook-function', {'report': report, 'filesAfter': 0})
            self.assertTrue(lab._check_function()[0])
            lab._marker('runbook-function', {'report': report, 'filesAfter': 2})
            self.assertFalse(lab._check_function()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('ops-automation', [item['key'] for item in labs_for_service('eventbridge')])
        tags = LAB_EXAM_TAGS[('eventbridge', 'ops-automation')]
        self.assertIn({'exam': 'SOA-C03', 'task': '1.2', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'operational-runbooks')
        self.assertIn(('eventbridge', 'ops-automation'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
