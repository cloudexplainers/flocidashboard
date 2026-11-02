"""Pipeline quality gates lab: Floci runs the builds, resolves Parameter Store and Secrets Manager references, runs the
pipeline, and publishes its failed stage events, but creates no test reports. These tests run the generated test
runner for each release, and cover the report parsing, the pipeline and rule documents, the checks, and registration."""

import importlib
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.codepipeline_gates_labs')


def _run_unit_gate(release):
    with tempfile.TemporaryDirectory() as folder:
        for name, body in lab.source_files(release).items():
            with open(os.path.join(folder, name), 'w') as handle:
                handle.write(body)
        env = {**os.environ, 'COVERAGE_MINIMUM': str(lab.COVERAGE_MINIMUM), 'PAYMENTS_API_KEY': 'secret-value',
               'ORDERS_TABLE': lab.TABLE}
        process = subprocess.run([sys.executable, 'run_tests.py'], cwd=folder, env=env, capture_output=True,
                                 text=True, timeout=60)
        with open(os.path.join(folder, 'reports', 'coverage.json')) as handle:
            coverage = json.load(handle)
        with open(os.path.join(folder, 'reports', 'settings.json')) as handle:
            settings = handle.read()
        with open(os.path.join(folder, 'reports', 'junit.xml')) as handle:
            junit = handle.read()
    return process, coverage, settings, junit


class UnitGateTests(SimpleTestCase):
    def test_release_1_passes_with_full_coverage(self):
        process, coverage, settings, junit = _run_unit_gate(lab.RELEASE_1)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(coverage['percent'], 100)
        self.assertEqual(lab.junit_summary(junit)['statusCounts'], {'SUCCEEDED': 3})
        self.assertNotIn('secret-value', settings)
        self.assertEqual(json.loads(settings)['paymentsKeyLength'], len('secret-value'))

    def test_untested_refunds_fail_the_coverage_gate(self):
        process, coverage, _settings, _junit = _run_unit_gate(lab.RELEASE_2)
        self.assertEqual(process.returncode, 2)
        self.assertLess(coverage['percent'], lab.COVERAGE_MINIMUM)
        self.assertIn('below the 80% minimum', process.stdout)

    def test_refund_tests_restore_coverage(self):
        process, coverage, _settings, junit = _run_unit_gate(lab.RELEASE_2_TESTED)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        self.assertEqual(coverage['percent'], 100)
        self.assertEqual(lab.junit_summary(junit)['total'], 6)

    def test_release_3_only_changes_the_key_attribute(self):
        self.assertIn("KEY_ATTRIBUTE = 'order_id'", lab.source_files(lab.RELEASE_3)['store.py'])
        self.assertIn("KEY_ATTRIBUTE = 'id'", lab.source_files(lab.RELEASE_3_FIXED)['store.py'])
        for name in ('app.py', 'test_app.py'):
            self.assertEqual(lab.source_files(lab.RELEASE_3)[name].replace(lab.RELEASE_3, 'x'),
                             lab.source_files(lab.RELEASE_3_FIXED)[name].replace(lab.RELEASE_3_FIXED, 'x'))
        for release in lab.RELEASES:
            for name, body in lab.source_files(release).items():
                compile(body, name, 'exec')


class DocumentTests(SimpleTestCase):
    def test_junit_summary_counts_each_status(self):
        xml = ('<testsuites><testsuite name="s"><testcase classname="A" name="ok" time="0.002"/>'
               '<testcase classname="A" name="bad" time="0"><failure message="x"/></testcase>'
               '<testcase classname="A" name="boom"><error message="y"/></testcase></testsuite></testsuites>')
        summary = lab.junit_summary(xml)
        self.assertEqual(summary['statusCounts'], {'SUCCEEDED': 1, 'FAILED': 1, 'ERROR': 1})
        self.assertEqual(summary['passRate'], 33.3)
        self.assertEqual(summary['cases'][0], {'name': 'A.ok', 'status': 'SUCCEEDED', 'durationMs': 2.0})

    def test_pipeline_runs_unit_tests_before_the_integration_test(self):
        document = lab.pipeline_document()
        self.assertEqual([stage['name'] for stage in document['stages']],
                         [lab.SOURCE_STAGE, lab.UNIT_STAGE, lab.INTEGRATION_STAGE])
        projects = [stage['actions'][0]['configuration'].get('ProjectName') for stage in document['stages'][1:]]
        self.assertEqual(projects, [lab.UNIT_PROJECT, lab.INTEGRATION_PROJECT])

    def test_rule_matches_only_failed_stages_of_this_pipeline(self):
        pattern = lab.event_pattern()
        self.assertEqual(pattern['detail-type'], ['CodePipeline Stage Execution State Change'])
        self.assertEqual(pattern['detail'], {'pipeline': [lab.PIPELINE], 'state': ['FAILED']})
        policy = lab.queue_policy('arn:queue', 'arn:rule')['Statement'][0]
        self.assertEqual(policy['Condition'], {'ArnEquals': {'aws:SourceArn': 'arn:rule'}})

    def test_buildspec_reads_settings_from_parameter_store_and_secrets_manager(self):
        self.assertIn(f'ORDERS_TABLE: {lab.TABLE_PARAMETER}', lab.UNIT_BUILDSPEC)
        self.assertIn(f'PAYMENTS_API_KEY: {lab.SECRET}', lab.UNIT_BUILDSPEC)
        self.assertNotIn(lab.SECRET_VALUE, lab.UNIT_BUILDSPEC + json.dumps(lab.BUILD_POLICY))


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_unit_check_needs_coverage_and_settings(self):
        marker = {'release': lab.RELEASE_1, 'build': {'status': 'SUCCEEDED'}, 'coverage': {'percent': 100},
                  'settings': {'ordersTable': lab.TABLE, 'paymentsKeyPresent': True, 'paymentsKeyLength': 17}}
        lab._marker('unit-tests', marker)
        self.assertTrue(lab._check_unit()[0])
        lab._marker('unit-tests', {**marker, 'settings': {'ordersTable': None}})
        self.assertFalse(lab._check_unit()[0])

    def test_coverage_check_needs_the_blocked_and_the_passing_build(self):
        blocked = {'status': 'FAILED', 'coverage': 70,
                   'failedPhase': {'phaseType': 'BUILD', 'message': 'Exit code 2: coverage 70% is below the 80% minimum'}}
        lab._marker('coverage-gate', {'blocked': blocked, 'passed': {'status': 'FAILED'}})
        self.assertFalse(lab._check_coverage()[0])
        lab._marker('coverage-gate', {'blocked': blocked, 'passed': {'status': 'SUCCEEDED', 'coverage': 100}})
        self.assertTrue(lab._check_coverage()[0])

    def test_events_check_needs_the_failed_integration_stage(self):
        with patch.object(lab, '_safe_call', return_value={'Name': lab.RULE}):
            lab._marker('failure-events', {'status': 'Failed', 'reason': 'ValidationException',
                                           'events': [{'stage': lab.UNIT_STAGE, 'state': 'FAILED'}]})
            self.assertFalse(lab._check_events()[0])
            lab._marker('failure-events', {'status': 'Failed', 'reason': 'ValidationException',
                                           'events': [{'stage': lab.INTEGRATION_STAGE, 'state': 'FAILED'}]})
            self.assertTrue(lab._check_events()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertEqual([item['key'] for item in labs_for_service('codepipeline')],
                         ['lambda-cicd', 'pipeline-quality-gates'])
        tags = LAB_EXAM_TAGS[('codepipeline', 'pipeline-quality-gates')]
        self.assertIn({'exam': 'DOP-C02', 'task': '1.2', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'pipeline-testing')
        self.assertIn(('codepipeline', 'pipeline-quality-gates'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
