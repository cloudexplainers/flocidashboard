"""Access troubleshooting lab: Floci enforces IAM on object and key ARNs, the Region guardrail, and disabled keys, but
records no denied calls, has no Access Analyzer findings, and stores conformance packs without running them. These
tests cover the external-access scan, the rule evaluation, the checks, and registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.iam_access_troubleshooting_labs')


class ExternalAccessTests(SimpleTestCase):
    def test_finds_accounts_outside_the_zone_of_trust(self):
        policy = lab._doc(
            lab.PARTNER_STATEMENT,
            {'Effect': 'Allow', 'Principal': {'AWS': [f'arn:aws:iam::{lab.ACCOUNT}:role/app', '444455556666']},
             'Action': 's3:GetObject', 'Resource': '*'},
            {'Effect': 'Deny', 'Principal': '*', 'Action': 's3:*', 'Resource': '*'},
        )
        self.assertEqual(lab.external_principals(policy), [lab.PARTNER_ACCOUNT, '444455556666'])
        self.assertEqual(lab.unintended(lab.external_principals(policy)), [lab.PARTNER_ACCOUNT])

    def test_wildcard_principal_is_public(self):
        policy = lab._doc({'Effect': 'Allow', 'Principal': '*', 'Action': 's3:GetObject', 'Resource': '*'})
        self.assertEqual(lab.external_principals(policy), ['*'])
        self.assertEqual(lab.external_principals(None), [])


class RuleEvaluationTests(SimpleTestCase):
    def test_both_rules_fail_then_pass(self):
        before = lab.evaluate_bucket(None, None)
        self.assertEqual({item['compliance'] for item in before.values()}, {'NON_COMPLIANT'})
        after = lab.evaluate_bucket('Enabled', lab._doc(lab.TLS_STATEMENT))
        self.assertEqual({item['compliance'] for item in after.values()}, {'COMPLIANT'})

    def test_tls_rule_needs_a_deny_for_everyone(self):
        allow = {**lab.TLS_STATEMENT, 'Effect': 'Allow'}
        self.assertFalse(lab.denies_insecure_transport(lab._doc(allow)))
        scoped = {**lab.TLS_STATEMENT, 'Principal': {'AWS': 'arn:aws:iam::000000000000:root'}}
        self.assertFalse(lab.denies_insecure_transport(lab._doc(scoped)))
        self.assertTrue(lab.denies_insecure_transport(lab._doc(lab.TLS_STATEMENT)))

    def test_suspended_versioning_is_noncompliant(self):
        result = lab.evaluate_bucket('Suspended', None)['lab-access-s3-versioning']
        self.assertEqual(result['compliance'], 'NON_COMPLIANT')

    def test_pack_template_declares_both_rules(self):
        names = [item['Properties']['ConfigRuleName'] for item in lab.PACK_TEMPLATE['Resources'].values()]
        self.assertEqual(sorted(names), sorted(lab.RULES))


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_denied_check_needs_the_recorded_denial(self):
        with patch.object(lab, '_user', return_value={'UserName': lab.ANALYST}), \
                patch.object(lab, '_bucket_exists', return_value=True):
            self.assertFalse(lab._check_denied()[0])
            lab._marker('denied-call', {'list': {'allowed': True}, 'get': {'allowed': False, 'error': 'AccessDenied'}})
            self.assertTrue(lab._check_denied()[0])

    def test_external_check_needs_the_finding_and_a_clean_policy(self):
        with patch.object(lab, '_analyzer', return_value={'status': 'ACTIVE'}), \
                patch.object(lab, '_bucket_policy', return_value=lab._doc(lab.PARTNER_STATEMENT)):
            lab._marker('external-access', {'found': [lab.PARTNER_ACCOUNT]})
            self.assertFalse(lab._check_external()[0])
        with patch.object(lab, '_analyzer', return_value={'status': 'ACTIVE'}), \
                patch.object(lab, '_bucket_policy', return_value=None):
            self.assertTrue(lab._check_external()[0])

    def test_compliance_check_needs_live_and_recorded_results(self):
        lab._marker('compliance-pack', {'before': ['NON_COMPLIANT', 'NON_COMPLIANT']})
        compliant = {name: 'COMPLIANT' for name in lab.RULES}
        with patch.object(lab, '_pack', return_value={'ConformancePackName': lab.PACK}), \
                patch.object(lab, '_versioning', return_value='Enabled'), \
                patch.object(lab, '_bucket_policy', return_value=lab._doc(lab.TLS_STATEMENT)), \
                patch.object(lab, '_compliance', return_value=compliant):
            self.assertTrue(lab._check_compliance()[0])
        with patch.object(lab, '_pack', return_value={'ConformancePackName': lab.PACK}), \
                patch.object(lab, '_versioning', return_value='Suspended'), \
                patch.object(lab, '_bucket_policy', return_value=lab._doc(lab.TLS_STATEMENT)), \
                patch.object(lab, '_compliance', return_value=compliant):
            self.assertFalse(lab._check_compliance()[0])

    def test_analyst_client_needs_a_key(self):
        with self.assertRaises(ValueError):
            lab._analyst_client('s3')


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('access-troubleshooting', [item['key'] for item in labs_for_service('iam')])
        tags = LAB_EXAM_TAGS[('iam', 'access-troubleshooting')]
        self.assertIn({'exam': 'SOA-C03', 'task': '4.1', 'primary': True}, tags)
        for key in ('access-troubleshooting', 'data-protection-operations'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('iam', 'access-troubleshooting'), card['related_labs'])
            self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
