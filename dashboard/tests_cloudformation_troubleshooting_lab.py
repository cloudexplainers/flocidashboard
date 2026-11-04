"""Provisioning troubleshooting lab: Floci enforces IAM on CloudFormation calls, fails stacks on service errors, counts
subnet addresses, copies AMIs, stores RAM shares, and enforces termination protection. These tests cover the
templates, the event reading, the subnet math, the checks, and registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudformation_troubleshooting_labs')


class TemplateTests(SimpleTestCase):
    def test_ops_template_carries_the_retention(self):
        queue = lab.ops_template(lab.BAD_RETENTION)['Resources']['Events']
        self.assertEqual(queue['Properties']['MessageRetentionPeriod'], 30)
        self.assertNotIn('MessageRetentionPeriod', lab.QUEUE_RESOURCE['Properties'])

    def test_network_template_adds_the_second_subnet(self):
        self.assertNotIn('SubnetB', lab.network_template(False)['Resources'])
        template = lab.network_template(True)
        self.assertEqual(template['Resources']['SubnetB']['Properties']['CidrBlock'], lab.SUBNET_B_CIDR)
        self.assertEqual(template['Outputs']['VpcId']['Export']['Name'], lab.VPC_EXPORT)

    def test_app_template_imports_the_named_export(self):
        group = lab.app_template(lab.TYPO_EXPORT)['Resources']['WebSecurityGroup']
        self.assertEqual(group['Properties']['VpcId'], {'Fn::ImportValue': lab.TYPO_EXPORT})

    def test_deployer_policy_adds_create_and_tagging(self):
        def actions(policy):
            return [action for statement in policy['Statement'] for action in statement['Action']]
        self.assertNotIn('cloudformation:CreateStack', actions(lab.deployer_policy(False)))
        self.assertIn('cloudformation:CreateStack', actions(lab.deployer_policy(True)))
        self.assertIn('cloudformation:TagResource', actions(lab.deployer_policy(True)))


class HelperTests(SimpleTestCase):
    def test_first_failure_reads_oldest_first_and_skips_the_stack(self):
        events = [
            {'Timestamp': '3', 'LogicalResourceId': 'Other', 'StackName': 's', 'ResourceStatus': 'DELETE_FAILED',
             'ResourceStatusReason': 'rollback'},
            {'Timestamp': '2', 'LogicalResourceId': 'Events', 'StackName': 's', 'ResourceStatus': 'CREATE_FAILED',
             'ResourceStatusReason': 'Invalid value'},
            {'Timestamp': '4', 'LogicalResourceId': 's', 'StackName': 's', 'ResourceStatus': 'CREATE_FAILED',
             'ResourceStatusReason': 'stack'},
        ]
        self.assertEqual(lab.first_failure(events)['resource'], 'Events')
        self.assertIsNone(lab.first_failure([]))

    def test_subnet_math(self):
        self.assertEqual(lab.usable_addresses('10.0.0.0/28'), 11)
        self.assertEqual(lab.usable_addresses('10.0.0.0/26'), 59)
        self.assertIn('out of addresses', lab.capacity_diagnosis(4, 2, {'subnet-a': 0}))
        self.assertIn('4 of 4', lab.capacity_diagnosis(4, 4, {'subnet-a': 0}))


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_capacity_check_needs_the_stuck_count_and_two_subnets(self):
        group = {'VPCZoneIdentifier': 'subnet-a,subnet-b',
                 'Instances': [{'LifecycleState': 'InService'}] * lab.DESIRED}
        lab._marker('subnet-capacity', {'stuckAt': 2})
        with patch.object(lab, '_group', return_value=group):
            self.assertTrue(lab._check_capacity()[0])
        with patch.object(lab, '_group', return_value={**group, 'VPCZoneIdentifier': 'subnet-a'}):
            self.assertFalse(lab._check_capacity()[0])
        lab._marker('subnet-capacity', {'stuckAt': lab.DESIRED})
        with patch.object(lab, '_group', return_value=group):
            self.assertFalse(lab._check_capacity()[0])

    def test_protect_check_needs_the_refused_delete(self):
        with patch.object(lab, '_stack', return_value={'EnableTerminationProtection': True}):
            self.assertFalse(lab._check_protect()[0])
            lab._marker('protect-network', {'deleteRefused': True})
            self.assertTrue(lab._check_protect()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('deployment-troubleshooting', [item['key'] for item in labs_for_service('cloudformation')])
        tags = LAB_EXAM_TAGS[('cloudformation', 'deployment-troubleshooting')]
        self.assertIn({'exam': 'SOA-C03', 'task': '3.1', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'provisioning-troubleshooting')
        self.assertIn(('cloudformation', 'deployment-troubleshooting'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
