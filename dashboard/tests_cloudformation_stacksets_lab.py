"""Multi-Region baseline lab: StackSets, custom resources and termination protection are real on Floci, while drift
detection, stack policies and Service Catalog provisioning are not, so the lab evaluates those itself. These tests
cover the stack policy evaluator, the drift comparison, the templates, the checks, and registration."""

import importlib
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudformation_stacksets_labs')


class StackPolicyTests(SimpleTestCase):
    def test_lab_policy_denies_replacing_the_cidr_parameter_only(self):
        policy = lab.STACK_POLICY
        self.assertEqual(lab.stack_policy_decision(policy, 'Update:Replace', 'CidrParameter'), 'Deny')
        self.assertEqual(lab.stack_policy_decision(policy, 'Update:Delete', 'CidrParameter'), 'Deny')
        self.assertEqual(lab.stack_policy_decision(policy, 'Update:Modify', 'CidrParameter'), 'Allow')
        self.assertEqual(lab.stack_policy_decision(policy, 'Update:Replace', 'Allocation'), 'Allow')

    def test_no_matching_allow_denies_by_default(self):
        policy = {'Statement': [{'Effect': 'Allow', 'Action': 'Update:Modify', 'Principal': '*', 'Resource': '*'}]}
        self.assertEqual(lab.stack_policy_decision(policy, 'Update:Replace', 'Anything'), 'Deny')
        self.assertEqual(lab.stack_policy_decision({'Statement': []}, 'Update:Modify', 'Anything'), 'Deny')

    def test_resource_patterns_use_wildcards(self):
        policy = {'Statement': [
            {'Effect': 'Allow', 'Action': 'Update:*', 'Principal': '*', 'Resource': '*'},
            {'Effect': 'Deny', 'Action': 'Update:*', 'Principal': '*', 'Resource': 'LogicalResourceId/Prod*'}]}
        self.assertEqual(lab.stack_policy_decision(policy, 'Update:Modify', 'ProdDatabase'), 'Deny')
        self.assertEqual(lab.stack_policy_decision(policy, 'Update:Modify', 'DevDatabase'), 'Allow')

    def test_change_set_actions_map_to_policy_actions(self):
        self.assertEqual(lab.update_action({'Action': 'Modify', 'Replacement': 'True'}), 'Update:Replace')
        self.assertEqual(lab.update_action({'Action': 'Modify', 'Replacement': 'Conditional'}), 'Update:Replace')
        self.assertEqual(lab.update_action({'Action': 'Modify', 'Replacement': 'False'}), 'Update:Modify')
        self.assertEqual(lab.update_action({'Action': 'Remove'}), 'Update:Delete')
        self.assertIsNone(lab.update_action({'Action': 'Add'}))

    def test_rename_change_set_is_denied_at_the_parameter(self):
        changes = [{'Action': 'Modify', 'LogicalResourceId': 'Allocation', 'Replacement': 'False'},
                   {'Action': 'Modify', 'LogicalResourceId': 'CidrParameter', 'Replacement': 'True'}]
        rows = {row['LogicalId']: row['Decision'] for row in lab.evaluate_change_set(lab.STACK_POLICY, changes)}
        self.assertEqual(rows, {'Allocation': 'Allow', 'CidrParameter': 'Deny'})


class DriftTests(SimpleTestCase):
    def test_compare_reports_modified_and_deleted_properties(self):
        expected = {'ConfigValue': lab.config_value(2), 'AuditLogRetentionDays': 90}
        rows = {row['Property']: row['Status'] for row in lab.compare_baseline(
            expected, {'ConfigValue': lab.config_value(2, owner='someone'), 'AuditLogRetentionDays': None})}
        self.assertEqual(rows, {'ConfigValue': 'MODIFIED', 'AuditLogRetentionDays': 'DELETED'})
        in_sync = lab.compare_baseline(expected, dict(expected))
        self.assertTrue(all(row['Status'] == 'IN_SYNC' for row in in_sync))

    def test_config_value_matches_the_template_sub(self):
        value = json.loads(lab.config_value(1))
        self.assertEqual(value, {'owner': lab.OWNER, 'retentionDays': lab.RETENTION_DAYS, 'version': 1})


class TemplateTests(SimpleTestCase):
    def test_v2_adds_the_alerts_topic_and_keeps_logical_ids(self):
        self.assertLess(set(lab.BASELINE_V1['Resources']), set(lab.BASELINE_V2['Resources']))
        self.assertEqual(lab.BASELINE_V2['Resources']['Alerts']['Properties']['TopicName'], lab.ALERTS_TOPIC)

    def test_rename_only_changes_the_parameter_name(self):
        before = lab.NETWORK_TEMPLATE['Resources']
        after = lab.RENAMED_NETWORK_TEMPLATE['Resources']
        self.assertEqual(before['Allocation'], after['Allocation'])
        self.assertNotEqual(before['CidrParameter']['Properties']['Name'], after['CidrParameter']['Properties']['Name'])

    def test_allocator_gives_each_environment_its_own_block(self):
        blocks = {lab.allocate_cidr(env) for env in ('dev', 'test', 'prod')}
        self.assertEqual(len(blocks), 3)
        self.assertIn(json.dumps(lab.CIDR_BLOCKS), lab.CUSTOM_RESOURCE_SOURCE)
        compile(lab.CUSTOM_RESOURCE_SOURCE, 'allocator.py', 'exec')


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_custom_resource_check_needs_the_allocated_cidr(self):
        stack = {'StackStatus': 'CREATE_COMPLETE', 'Outputs': [{'OutputKey': 'VpcCidr', 'OutputValue': '10.30.0.0/16'}]}
        with patch.object(lab, '_stack', return_value=stack):
            self.assertTrue(lab._check_custom()[0])
        stack = {**stack, 'Outputs': [{'OutputKey': 'VpcCidr', 'OutputValue': '42'}]}
        with patch.object(lab, '_stack', return_value=stack):
            self.assertFalse(lab._check_custom()[0])

    def test_update_check_needs_the_topic_in_every_region(self):
        with patch.object(lab, '_check_deploy', return_value=(True, '')), \
                patch.object(lab, '_template_version', return_value=2), \
                patch.object(lab, '_topic_exists', side_effect=lambda region: region == 'us-east-1'):
            ok, message = lab._check_update()
        self.assertFalse(ok)
        self.assertIn('us-west-2', message)

    def test_drift_check_fails_while_a_region_is_drifted(self):
        lab._marker('detect-drift', {'message': 'ok'})
        expected = {'ConfigValue': lab.config_value(2), 'AuditLogRetentionDays': 90}
        drifted = {'us-east-1': expected, 'us-west-2': {**expected, 'AuditLogRetentionDays': 1}}
        with patch.object(lab, '_check_deploy', return_value=(True, '')), \
                patch.object(lab, '_expected_baseline', return_value=expected), \
                patch.object(lab, '_live_baseline', side_effect=lambda region: drifted[region]):
            self.assertFalse(lab._check_drift()[0])
        with patch.object(lab, '_check_deploy', return_value=(True, '')), \
                patch.object(lab, '_expected_baseline', return_value=expected), \
                patch.object(lab, '_live_baseline', return_value=expected):
            self.assertTrue(lab._check_drift()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('stacksets-baseline', [item['key'] for item in labs_for_service('cloudformation')])
        tags = LAB_EXAM_TAGS[('cloudformation', 'stacksets-baseline')]
        self.assertIn({'exam': 'DOP-C02', 'task': '2.1', 'primary': True}, tags)
        self.assertIn({'exam': 'DOP-C02', 'task': '2.2', 'primary': False}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'multi-account-iac')
        self.assertIn(('cloudformation', 'stacksets-baseline'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
