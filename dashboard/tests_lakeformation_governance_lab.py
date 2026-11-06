"""Lake governance lab: Floci stores Lake Formation tags and grants without enforcing them, so the lab evaluates the
column grant and scans for PII itself. These tests cover those helpers, the policies, and registration."""

import importlib
import json

from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.lakeformation_governance_labs')


class HelperTests(SimpleTestCase):
    def test_pii_scan_finds_every_email_and_phone(self):
        self.assertEqual(lab.find_pii(lab.raw_csv()),
                         {'EMAIL_ADDRESS': len(lab.CUSTOMERS), 'PHONE_NUMBER': len(lab.CUSTOMERS)})

    def test_curated_copy_has_no_pii_and_stable_keys(self):
        curated = lab.curated_csv(lab.raw_csv())
        self.assertEqual(lab.find_pii(curated), {})
        self.assertNotIn('Ana Silva', curated)
        self.assertEqual(lab.pseudonym('Ana.Silva@example.com'), lab.pseudonym('ana.silva@example.com'))
        self.assertIn(lab.pseudonym('ana.silva@example.com'), curated)

    def test_tag_grant_hides_pii_columns(self):
        self.assertEqual(lab.visible_columns(lab.ANALYST_CAN_SEE),
                         ['customer_id', 'country', 'lifetime_spend'])
        self.assertEqual(lab.visible_columns(['public']), ['customer_id'])

    def test_policies_scope_the_analyst_to_the_curated_zone_and_home_region(self):
        policy = json.dumps(lab.access_policy('arn:aws:kms:us-east-1:000000000000:key/x'))
        self.assertIn('curated/', policy)
        self.assertNotIn('raw/*', policy)
        self.assertIn('aws:RequestedRegion', json.dumps(lab.REGION_GUARDRAIL))


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('lake-governance', [item['key'] for item in labs_for_service('lakeformation')])
        tags = LAB_EXAM_TAGS[('lakeformation', 'lake-governance')]
        self.assertIn({'exam': 'DEA-C01', 'task': '4.2', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'data-authorization')
        self.assertIn(('lakeformation', 'lake-governance'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
