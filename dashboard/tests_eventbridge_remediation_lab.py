"""Drift remediation lab: Floci publishes Parameter Store and S3 events, runs the fixer and the workflow, records Config
evaluations, and evaluates the alarms, but never invokes a custom Config rule's function. These tests run the fixer's
logic against fake clients, and cover the workflow, the event pattern, the checks, and registration."""

import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.eventbridge_remediation_labs')


class FakeClients:
    """Just enough of SSM, S3, DynamoDB, Config, and CloudWatch for the fixer."""

    def __init__(self, parameters, grants=None, storm='OK'):
        self.parameters = dict(parameters)
        self.grants = grants or []
        self.storm = storm
        self.evaluations, self.items, self.metrics, self.acl_calls = [], [], [], []
        no_rule = type('NoSuchConfigRuleException', (Exception,), {})
        self.config_exceptions = types.SimpleNamespace(NoSuchConfigRuleException=no_rule)

    def client(self, name):
        fake = MagicMock()
        if name == 'ssm':
            fake.get_parameter.side_effect = lambda Name: {'Parameter': {'Value': self.parameters[Name]}}
            fake.put_parameter.side_effect = lambda Name, Value, **_: self.parameters.__setitem__(Name, Value)
        elif name == 's3':
            fake.get_object_acl.side_effect = lambda **_: {'Grants': self.grants}
            fake.put_object_acl.side_effect = lambda **kwargs: self.acl_calls.append(kwargs)
        elif name == 'dynamodb':
            fake.put_item.side_effect = lambda **kwargs: self.items.append(kwargs['Item'])
        elif name == 'config':
            fake.exceptions = self.config_exceptions
            fake.put_evaluations.side_effect = lambda **kwargs: self.evaluations.extend(kwargs['Evaluations'])
        elif name == 'cloudwatch':
            fake.describe_alarms.side_effect = lambda **_: {'CompositeAlarms': [{'StateValue': self.storm}]}
            fake.put_metric_data.side_effect = lambda **kwargs: self.metrics.extend(kwargs['MetricData'])
        return fake


def _load_fixer(fakes):
    env = {'BASELINE_PARAMETER': lab.BASELINE_PARAMETER, 'LOG_TABLE': lab.LOG_TABLE, 'CONFIG_RULE': lab.CONFIG_RULE,
           'NAMESPACE': lab.NAMESPACE, 'STORM_ALARM': lab.STORM_ALARM, 'BUCKET': lab.BUCKET}
    boto3 = types.ModuleType('boto3')
    boto3.client = fakes.client
    module = types.ModuleType('fixer')
    module.print = lambda *args, **kwargs: None  # keep the function's log line out of the test output
    with patch.dict(sys.modules, {'boto3': boto3}), patch.dict('os.environ', env):
        exec(compile(lab.FIXER_SOURCE, 'index.py', 'exec'), module.__dict__)
    return module


def _parameters(**overrides):
    values = {**lab.APPROVED, **overrides}
    values[lab.BASELINE_PARAMETER] = json.dumps(lab.APPROVED)
    return values


def _ssm_event(name, operation='Update'):
    return {'source': 'aws.ssm', 'detail-type': 'Parameter Store Change',
            'detail': {'name': name, 'operation': operation, 'type': 'String'}}


class FixerTests(SimpleTestCase):
    def test_drifted_parameter_is_restored_reported_and_recorded(self):
        fakes = FakeClients(_parameters(**{lab.LOG_LEVEL: 'DEBUG'}))
        result = _load_fixer(fakes).handler(_ssm_event(lab.LOG_LEVEL), None)
        self.assertTrue(result['fixed'])
        self.assertEqual(fakes.parameters[lab.LOG_LEVEL], 'INFO')
        self.assertEqual([item['ComplianceType'] for item in fakes.evaluations], ['NON_COMPLIANT', 'COMPLIANT'])
        self.assertEqual(fakes.items[0]['found'], {'S': 'DEBUG'})
        self.assertEqual([metric['MetricName'] for metric in fakes.metrics], ['DriftDetected', 'DriftFixed'])

    def test_its_own_fix_event_is_a_no_op(self):
        fakes = FakeClients(_parameters())
        result = _load_fixer(fakes).handler(_ssm_event(lab.LOG_LEVEL), None)
        self.assertFalse(result['drifted'])
        self.assertEqual(fakes.items, [])
        self.assertEqual(fakes.metrics, [])

    def test_unmanaged_and_deleted_parameters_are_left_alone(self):
        fakes = FakeClients(_parameters())
        fixer = _load_fixer(fakes)
        self.assertEqual(fixer.handler(_ssm_event('/lab-remediate/app/other'), None)['reason'], 'not managed')
        self.assertFalse(fixer.handler(_ssm_event(lab.LOG_LEVEL, 'Delete'), None)['drifted'])

    def test_public_object_is_made_private(self):
        grants = [{'Grantee': {'Type': 'Group', 'URI': lab.ALL_USERS}, 'Permission': 'READ'}]
        fakes = FakeClients(_parameters(), grants=grants)
        event = {'source': 'aws.s3', 'detail-type': 'Object Created',
                 'detail': {'bucket': {'name': lab.BUCKET}, 'object': {'key': lab.PUBLIC_KEY}}}
        result = _load_fixer(fakes).handler(event, None)
        self.assertEqual(result['current'], 'public READ')
        self.assertEqual(fakes.acl_calls, [{'Bucket': lab.BUCKET, 'Key': lab.PUBLIC_KEY, 'ACL': 'private'}])
        self.assertEqual(fakes.evaluations, [])

    def test_inspect_reports_the_storm_state_for_the_workflow(self):
        fakes = FakeClients(_parameters(**{lab.LOG_LEVEL: 'TRACE'}), storm='ALARM')
        finding = _load_fixer(fakes).handler({'action': 'inspect', 'event': _ssm_event(lab.LOG_LEVEL)}, None)
        self.assertEqual((finding['drifted'], finding['storm']), (True, 'ALARM'))
        self.assertEqual(fakes.parameters[lab.LOG_LEVEL], 'TRACE')

    def test_missing_config_rule_does_not_stop_the_fix(self):
        fakes = FakeClients(_parameters(**{lab.MAX_CONNECTIONS: '500'}))
        fixer = _load_fixer(fakes)
        fixer.config.put_evaluations.side_effect = fakes.config_exceptions.NoSuchConfigRuleException()
        self.assertTrue(fixer.handler(_ssm_event(lab.MAX_CONNECTIONS), None)['fixed'])
        self.assertEqual(fakes.parameters[lab.MAX_CONNECTIONS], '50')

    def test_periodic_evaluation_reports_every_parameter(self):
        fakes = FakeClients(_parameters(**{lab.MAX_CONNECTIONS: '500'}))
        results = _load_fixer(fakes).handler({'action': 'evaluate'}, None)
        self.assertEqual({row['resource']: row['compliant'] for row in results},
                         {lab.LOG_LEVEL: True, lab.MAX_CONNECTIONS: False})


class DocumentTests(SimpleTestCase):
    def test_workflow_escalates_only_during_a_storm(self):
        states = lab.workflow_definition('http://queue')['States']
        choices = states['Drifted?']['Choices']
        self.assertEqual(choices[0]['Next'], 'NoDrift')
        self.assertEqual((choices[1]['StringEquals'], choices[1]['Next']), ('ALARM', 'Escalate'))
        self.assertEqual(states['Drifted?']['Default'], 'Fix')
        self.assertEqual(states['Escalate']['Next'], 'RecordEscalation')

    def test_event_pattern_covers_parameters_and_uploads(self):
        sources = [pattern['source'][0] for pattern in lab.event_pattern()['$or']]
        self.assertEqual(sources, ['aws.ssm', 'aws.s3'])
        self.assertEqual(lab.event_pattern()['$or'][0]['detail']['name'], [{'prefix': lab.PARAMETER_PREFIX}])

    def test_execution_path_lists_entered_states(self):
        history = [{'type': 'ExecutionStarted'},
                   {'type': 'TaskStateEntered', 'stateEnteredEventDetails': {'name': 'Inspect'}},
                   {'type': 'ChoiceStateEntered', 'stateEnteredEventDetails': {'name': 'Drifted?'}},
                   {'type': 'TaskStateEntered', 'stateEnteredEventDetails': {'name': 'Escalate'}}]
        self.assertEqual(lab.execution_path(history), ['Inspect', 'Drifted?', 'Escalate'])

    def test_fixer_source_compiles(self):
        compile(lab.FIXER_SOURCE, 'index.py', 'exec')


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_escalation_check_needs_an_unfixed_queued_change(self):
        marker = {'storm': 'ALARM', 'path': ['Inspect', 'Drifted?', 'Escalate', 'RecordEscalation'],
                  'queued': 1, 'valueAfter': 'TRACE'}
        lab._marker('storm-escalation', marker)
        self.assertTrue(lab._check_escalation()[0])
        lab._marker('storm-escalation', {**marker, 'valueAfter': 'INFO'})
        self.assertFalse(lab._check_escalation()[0])
        lab._marker('storm-escalation', {**marker, 'storm': 'OK'})
        self.assertFalse(lab._check_escalation()[0])

    def test_resume_check_needs_the_storm_over_and_the_fix(self):
        lab._marker('resume-after-storm', {'storm': 'OK', 'path': ['Inspect', 'Drifted?', 'Fix']})
        with patch.object(lab, '_parameter', return_value='INFO'):
            self.assertTrue(lab._check_resume()[0])
        with patch.object(lab, '_parameter', return_value='TRACE'):
            self.assertFalse(lab._check_resume()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertEqual([item['key'] for item in labs_for_service('eventbridge')],
                         ['application-spine', 'drift-remediation', 'ops-automation'])
        tags = LAB_EXAM_TAGS[('eventbridge', 'drift-remediation')]
        self.assertIn({'exam': 'DOP-C02', 'task': '5.2', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'auto-remediation')
        self.assertIn(('eventbridge', 'drift-remediation'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
