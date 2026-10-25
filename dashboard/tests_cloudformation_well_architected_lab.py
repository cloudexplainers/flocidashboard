"""Well-Architected review lab: CloudFormation, SQS redrive and the resources are real on Floci, but the
Well-Architected Tool is not, so the lab scores the pillars itself. These tests cover the review, the checks, and
registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudformation_well_architected_labs')

GOOD = {
    'managed_by_stack': True,
    'bucket': {'encrypted': True, 'public_access_blocked': True, 'versioning': 'Enabled', 'tiering': True,
               'trims_old_versions': True, 'cost_tag': True},
    'queue': {'exists': True, 'encrypted': True, 'dead_letter_queue': True, 'wait_seconds': '20', 'cost_tag': True},
    'alarm': {'exists': True, 'actions': ['arn:aws:sns:us-east-1:000000000000:alerts']},
}
DRAFT = {
    'managed_by_stack': True,
    'bucket': {'encrypted': True, 'public_access_blocked': True, 'versioning': None, 'tiering': False,
               'trims_old_versions': False, 'cost_tag': False},
    'queue': {'exists': True, 'encrypted': True, 'dead_letter_queue': False, 'wait_seconds': '0', 'cost_tag': False},
    'alarm': {'exists': False, 'actions': []},
}


class ReviewTests(SimpleTestCase):
    def test_improved_workload_passes_every_pillar(self):
        rows = lab.review(GOOD)
        self.assertEqual([row['Pillar'] for row in rows], lab.PILLARS)
        self.assertEqual(lab.summarize(rows), (6, 0))

    def test_first_draft_findings(self):
        rows = {row['Pillar']: row for row in lab.review(DRAFT)}
        self.assertEqual(rows['Security']['Status'], 'pass')  # new buckets and queues are encrypted by default
        self.assertEqual(rows['Reliability']['Passed'], 0)
        self.assertEqual(lab.summarize(list(rows.values())), (1, 7))

    def test_unencrypted_bucket_fails_security(self):
        observed = {**GOOD, 'bucket': {**GOOD['bucket'], 'encrypted': False}}
        security = next(row for row in lab.review(observed) if row['Pillar'] == 'Security')
        self.assertEqual(security['Findings'], ['Is the bucket encrypted by default?'])

    def test_seconds(self):
        self.assertEqual(lab._seconds(0.2), 'under a second')
        self.assertEqual(lab._seconds(2.5), '2.5 s')


class TemplateTests(SimpleTestCase):
    def test_improved_template_keeps_logical_ids(self):
        self.assertLessEqual(set(lab.BASELINE_TEMPLATE['Resources']), set(lab.IMPROVED_TEMPLATE['Resources']))
        jobs = lab.IMPROVED_TEMPLATE['Resources']['Jobs']['Properties']
        self.assertEqual(jobs['RedrivePolicy']['maxReceiveCount'], lab.MAX_RECEIVES)

    def test_names_follow_the_environment(self):
        self.assertEqual(lab.PROD['bucket'], 'lab-wa-orders-prod-uploads')
        self.assertEqual(lab.TEST['queue'], 'lab-wa-orders-test-jobs')


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_plan_requires_the_expected_changes(self):
        def change(action, logical, replacement=None):
            return {'ResourceChange': {'Action': action, 'LogicalResourceId': logical, 'Replacement': replacement}}
        good = {'Changes': [change('Modify', 'Uploads', 'False'), change('Modify', 'Jobs', 'False'),
                            change('Add', 'JobsDlq'), change('Add', 'Alerts'), change('Add', 'JobsAgeAlarm')]}
        with patch.object(lab, '_check_baseline', return_value=(True, '')), patch.object(lab, '_improved', return_value=False):
            with patch.object(lab, '_change_set', return_value=good):
                self.assertTrue(lab._check_plan()[0])
            replaced = {'Changes': [*good['Changes'][1:], change('Modify', 'Uploads', 'True')]}
            with patch.object(lab, '_change_set', return_value=replaced):
                ok, message = lab._check_plan()
            self.assertFalse(ok)
            self.assertIn('replace Uploads', message)

    def test_rereview_needs_zero_findings(self):
        with patch.object(lab, '_improved', return_value=True), patch.object(lab, '_observe', return_value=DRAFT):
            self.assertFalse(lab._check_rereview()[0])
        lab._marker('review-again', {'message': 'ok'})
        with patch.object(lab, '_improved', return_value=True), patch.object(lab, '_observe', return_value=GOOD):
            self.assertTrue(lab._check_rereview()[0])

    def test_copy_must_be_deleted(self):
        lab._marker('disposable-copy', {'message': 'ok'})
        with patch.object(lab, '_stack', return_value={'StackStatus': 'CREATE_COMPLETE'}):
            self.assertFalse(lab._check_copy()[0])
        with patch.object(lab, '_stack', return_value=None):
            self.assertTrue(lab._check_copy()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('well-architected-review', [item['key'] for item in labs_for_service('cloudformation')])
        tags = LAB_EXAM_TAGS[('cloudformation', 'well-architected-review')]
        self.assertIn({'exam': 'CLF-C02', 'task': '1.2', 'primary': True}, tags)
        for key in ('well-architected', 'cloud-value'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('cloudformation', 'well-architected-review'), card['related_labs'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
