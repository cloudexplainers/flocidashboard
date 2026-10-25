"""Shared responsibility lab: the checks read what was deployed on EC2, RDS, Lambda and S3, and the matrix and task
sort are pure logic. These tests cover that logic, the checks, and registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.ec2_shared_responsibility_labs')


class PureLogicTests(SimpleTestCase):
    def test_parse_os_report(self):
        report = lab.parse_os_report('os=Ubuntu 22.04.5 LTS\nkernel=6.1\npackages=101\nnoise\n')
        self.assertEqual(report, {'os': 'Ubuntu 22.04.5 LTS', 'kernel': '6.1', 'packages': '101'})

    def test_rds_choices(self):
        good = {key: value for key, value in lab.DB_SETTINGS.items()}
        self.assertEqual(lab.rds_customer_choices(good), [])
        bad = {**good, 'PubliclyAccessible': True, 'BackupRetentionPeriod': 0}
        problems = lab.rds_customer_choices(bad)
        self.assertEqual(len(problems), 2)
        self.assertTrue(any('PubliclyAccessible' in item for item in problems))

    def test_bucket_problems(self):
        public = {key: True for key in ('BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets')}
        encryption = [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]
        self.assertEqual(lab.bucket_problems(public, encryption, lab.TLS_ONLY_POLICY), [])
        self.assertEqual(len(lab.bucket_problems({**public, 'BlockPublicPolicy': False}, None, None)), 3)

    def test_matrix_moves_the_line_with_the_service(self):
        rows = lab.responsibility_matrix({'EC2': True, 'RDS': True, 'Lambda': True, 'S3': True})
        by_layer = {row['Layer']: row for row in rows}
        guest_os = by_layer['Guest operating system patches']
        self.assertEqual((guest_os['EC2'], guest_os['RDS'], guest_os['Lambda']), ('Customer', 'AWS', 'AWS'))
        for layer in ('Identity and access management (IAM)', 'Data classification and encryption choices'):
            self.assertEqual({by_layer[layer][service] for service in ('EC2', 'RDS', 'Lambda', 'S3')}, {'Customer'})
        physical = by_layer['Physical data centers, hardware, and the global network']
        self.assertEqual({physical[service] for service in ('EC2', 'RDS', 'Lambda', 'S3')}, {'AWS'})
        self.assertEqual(len(rows), len(lab.LAYERS))

    def test_matrix_marks_missing_services(self):
        rows = lab.responsibility_matrix({'EC2': True})
        self.assertEqual(rows[0]['RDS'], 'not deployed')

    def test_tasks_include_the_three_shared_controls(self):
        grouped = lab.classify_tasks()
        shared = ' '.join(item['Task'] for item in grouped['Shared']).lower()
        for control in ('patch management', 'configuration management', 'train'):
            self.assertIn(control, shared)
        self.assertEqual(sum(len(items) for items in grouped.values()), len(lab.TASKS))


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_group_must_allow_only_https(self):
        good = {'GroupId': 'sg-1', 'IpPermissions': [{'IpProtocol': 'tcp', 'FromPort': 443, 'ToPort': 443}]}
        with patch.object(lab, '_group', return_value=good):
            self.assertTrue(lab._check_group()[0])
        ssh = {'GroupId': 'sg-1', 'IpPermissions': [*good['IpPermissions'], {'IpProtocol': 'tcp', 'FromPort': 22, 'ToPort': 22}]}
        with patch.object(lab, '_group', return_value=ssh):
            self.assertFalse(lab._check_group()[0])

    def test_instance_needs_the_os_report(self):
        instance = {'InstanceId': 'i-1', 'State': {'Name': 'running'}, 'SecurityGroups': [{'GroupId': 'sg-1'}]}
        with patch.object(lab, '_instance', return_value=instance), patch.object(lab, '_group', return_value={'GroupId': 'sg-1'}):
            self.assertFalse(lab._check_instance()[0])
            lab._marker('ec2-guest-os', {'os': 'Ubuntu 22.04.5 LTS', 'packages': '101'})
            ok, message = lab._check_instance()
        self.assertTrue(ok)
        self.assertIn('Ubuntu', message)

    def test_function_needs_the_runtime_report(self):
        function = {'Runtime': lab.RUNTIME, 'Role': lab.ROLE_ARN}
        with patch.object(lab, '_function', return_value=function):
            self.assertFalse(lab._check_function()[0])
            lab._marker('lambda-managed-runtime', {'execution_env': 'AWS_Lambda_python3.12', 'python': '3.12.7'})
            self.assertTrue(lab._check_function()[0])

    def test_matrix_step_needs_every_service(self):
        with patch.object(lab, '_check_instance', return_value=(True, '')), \
                patch.object(lab, '_check_db', return_value=(False, '')), \
                patch.object(lab, '_check_function', return_value=(True, '')), \
                patch.object(lab, '_check_bucket', return_value=(True, '')):
            result = lab.step_responsibility_matrix()
        self.assertFalse(result['verified'])
        self.assertIn('RDS', result['verification']['message'])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('shared-responsibility', [item['key'] for item in labs_for_service('ec2')])
        tags = LAB_EXAM_TAGS[('ec2', 'shared-responsibility')]
        self.assertIn({'exam': 'CLF-C02', 'task': '2.1', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'shared-responsibility')
        self.assertIn(('ec2', 'shared-responsibility'), card['related_labs'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('nope')
