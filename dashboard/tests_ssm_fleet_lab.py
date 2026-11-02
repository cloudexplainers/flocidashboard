"""Fleet release lab: Floci runs the instances, Run Command, the instance refresh and the stack rollback for real, but
its CodeDeploy reaches no EC2 instances. These tests cover the release scripts, the rollout plan, the drift and root
cause helpers, the checks, and registration."""

import base64
import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.ssm_fleet_labs')


class ScriptTests(SimpleTestCase):
    def test_user_data_installs_the_release(self):
        script = base64.b64decode(lab.user_data('release-1')).decode()
        self.assertTrue(script.startswith('#!/bin/sh'))
        self.assertIn('echo release-1 > /opt/storefront/release', script)

    def test_release_3_validation_needs_the_secrets_file(self):
        self.assertNotIn('secrets.env', lab.release_script('release-2'))
        script = lab.release_script('release-3', require_secrets=True)
        self.assertIn('secrets.env is missing', script)
        self.assertIn('release.previous', script)
        self.assertIn('release.previous', lab.rollback_script())


class HelperTests(SimpleTestCase):
    def test_rolling_plan_is_one_instance_per_batch(self):
        self.assertEqual(lab.plan_rolling(['i-b', 'i-a', 'i-c']), [['i-a'], ['i-b'], ['i-c']])
        self.assertEqual(lab.plan_rolling(['i-a', 'i-b', 'i-c'], 2), [['i-a', 'i-b'], ['i-c']])

    def test_drift_groups_instances_by_release(self):
        groups = lab.drifted({'i-1': 'release-2', 'i-2': 'release-2', 'i-3': 'release-1'})
        self.assertEqual(groups, {'release-1': ['i-3'], 'release-2': ['i-1', 'i-2']})

    def test_root_cause_is_the_first_failed_resource(self):
        events = [
            {'Timestamp': '3', 'LogicalResourceId': 'stack', 'ResourceType': 'AWS::CloudFormation::Stack',
             'ResourceStatus': 'UPDATE_ROLLBACK_FAILED'},
            {'Timestamp': '2', 'LogicalResourceId': 'Later', 'ResourceType': 'AWS::SNS::Topic',
             'ResourceStatus': 'UPDATE_FAILED', 'ResourceStatusReason': 'cancelled'},
            {'Timestamp': '1', 'LogicalResourceId': 'DeployEvents', 'ResourceType': 'AWS::SQS::Queue',
             'ResourceStatus': 'CREATE_FAILED', 'ResourceStatusReason': 'Invalid value'},
        ]
        self.assertEqual(lab.root_cause(events)['LogicalResourceId'], 'DeployEvents')
        self.assertIsNone(lab.root_cause([]))

    def test_checklist_flags_the_missing_agent(self):
        checks = {item['check']: item['ok'] for item in lab.no_instances_checklist(3, 0, True)}
        self.assertTrue(checks['The tag filter matches running instances'])
        self.assertFalse(checks['An agent on each instance is reporting'])
        self.assertFalse(lab.no_instances_checklist(0, 0, True)[0]['ok'])

    def test_broken_template_only_changes_the_retention(self):
        broken = lab.STACK_BROKEN['Resources']['DeployEvents']['Properties']
        fixed = lab.STACK_FIXED['Resources']['DeployEvents']['Properties']
        self.assertLess(broken['MessageRetentionPeriod'], 60)
        self.assertEqual({**broken, 'MessageRetentionPeriod': fixed['MessageRetentionPeriod']}, fixed)
        self.assertNotIn('DeployEvents', lab.STACK_V1['Resources'])


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_failed_check_needs_the_stop_and_the_rollback(self):
        failed = {'Status': 'Failed', 'ResponseCode': 1, 'Stderr': 'ValidateService: secrets.env is missing'}
        lab._marker('failed-release', {'failed': failed, 'skipped': 0,
                                       'rollback': {'Status': 'Success', 'Stdout': 'rolled back to release-2'}})
        self.assertFalse(lab._check_failed()[0])
        lab._marker('failed-release', {'failed': failed, 'skipped': 1,
                                       'rollback': {'Status': 'Success', 'Stdout': 'rolled back to release-2'}})
        self.assertTrue(lab._check_failed()[0])

    def test_drift_check_needs_an_old_and_a_new_release(self):
        lab._marker('scale-out-drift', {'releases_by_version': {'release-2': ['i-1', 'i-2']}})
        self.assertFalse(lab._check_drift()[0])
        lab._marker('scale-out-drift', {'releases_by_version': {'release-2': ['i-1', 'i-2'], 'release-1': ['i-3']}})
        self.assertTrue(lab._check_drift()[0])

    def test_rollback_check_needs_the_root_cause_and_restored_parameter(self):
        marker = {'status': 'UPDATE_ROLLBACK_COMPLETE', 'rootCause': {'LogicalResourceId': 'DeployEvents'},
                  'parameterAfterRollback': '1'}
        lab._marker('stack-rollback', marker)
        self.assertTrue(lab._check_rollback()[0])
        lab._marker('stack-rollback', {**marker, 'parameterAfterRollback': '2'})
        self.assertFalse(lab._check_rollback()[0])

    def test_refresh_check_needs_every_instance_on_release_2(self):
        with patch.object(lab, '_client') as client, \
                patch.object(lab, '_launch_template', return_value={'LatestVersionNumber': 2}):
            client.return_value.describe_instance_refreshes.return_value = {
                'InstanceRefreshes': [{'Status': 'Successful'}]}
            lab._marker('instance-refresh', {'releases': {'i-1': 'release-2', 'i-2': 'release-1'}})
            self.assertFalse(lab._check_refresh()[0])
            lab._marker('instance-refresh', {'releases': {'i-1': 'release-2', 'i-2': 'release-2'}})
            self.assertTrue(lab._check_refresh()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertEqual([item['key'] for item in labs_for_service('ssm')],
                         ['parameter-store-config', 'fleet-release-troubleshooting'])
        tags = LAB_EXAM_TAGS[('ssm', 'fleet-release-troubleshooting')]
        self.assertIn({'exam': 'DOP-C02', 'task': '5.3', 'primary': True}, tags)
        for key in ('failure-troubleshooting', 'deployment-at-scale'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('ssm', 'fleet-release-troubleshooting'), card['related_labs'])
            self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
