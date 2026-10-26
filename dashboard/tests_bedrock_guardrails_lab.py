"""Responsible AI lab: Floci stores guardrails but never applies them, so the lab evaluates prompts against the
stored policies itself. These tests cover that evaluation, the masking and policy logic, the checks, and
registration."""

import importlib
import json
import re
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.bedrock_labs')

GUARDRAIL = {
    'topicPolicy': {'topics': [lab.DENIED_TOPIC]},
    'wordPolicy': {'words': [{'text': word} for word in lab.BLOCKED_WORDS]},
    'contentPolicy': {'filters': lab.CONTENT_FILTERS},
    'sensitiveInformationPolicy': {'piiEntities': lab.PII_ENTITIES, 'regexes': lab.PII_REGEXES},
}


class EvaluateTests(SimpleTestCase):
    def test_every_sample_prompt_is_judged_as_the_lab_expects(self):
        for prompt, expected in lab.SAMPLE_PROMPTS:
            with self.subTest(prompt=prompt[:40]):
                verdict = lab.evaluate(prompt, GUARDRAIL)
                if expected is None:
                    self.assertEqual(verdict['Action'], 'allow')
                    self.assertEqual(verdict['Findings'], [])
                else:
                    self.assertIn(expected, verdict['Findings'])

    def test_a_denied_topic_blocks_and_shows_the_blocked_message(self):
        verdict = lab.evaluate('Should I put my refund into bitcoin?', GUARDRAIL)
        self.assertEqual(verdict['Action'], 'block')
        self.assertEqual(verdict['UserSees'], lab.BLOCKED_INPUT_MESSAGE)

    def test_masking_rewrites_the_prompt_without_blocking_it(self):
        verdict = lab.evaluate('Reach me at jane@example.com please', GUARDRAIL)
        self.assertEqual(verdict['Action'], 'mask')
        self.assertIn('{EMAIL}', verdict['Masked'])
        self.assertNotIn('jane@example.com', verdict['Masked'])

    def test_a_card_number_blocks_rather_than_masks(self):
        verdict = lab.evaluate('Charge card 4111111111111111 please', GUARDRAIL)
        self.assertEqual(verdict['Action'], 'block')
        self.assertIn('PII blocked: CREDIT_DEBIT_CARD_NUMBER', verdict['Findings'])

    def test_an_empty_guardrail_allows_everything(self):
        self.assertEqual(lab.evaluate('Should I buy bitcoin with my card 4111111111111111?', {})['Action'], 'allow')

    def test_topic_matching_ignores_unrelated_text(self):
        self.assertFalse(lab.topic_hit('My jacket zipper broke', lab.DENIED_TOPIC))
        self.assertTrue(lab.topic_hit('which stock should I pick', lab.DENIED_TOPIC))


class MaskTests(SimpleTestCase):
    def test_masking_covers_every_pii_rule_whatever_its_action(self):
        masked, found = lab.mask_pii('jane@example.com, 555-0100, order 10492, card 4111111111111111', GUARDRAIL)
        self.assertEqual(set(found), {'EMAIL', 'PHONE', 'CREDIT_DEBIT_CARD_NUMBER', 'OrderNumber'})
        for pattern in lab.PII_PATTERNS.values():
            self.assertIsNone(re.search(pattern, masked))

    def test_clean_text_is_untouched(self):
        masked, found = lab.mask_pii('My jacket zipper broke', GUARDRAIL)
        self.assertEqual(masked, 'My jacket zipper broke')
        self.assertEqual(found, [])


class PolicyTests(SimpleTestCase):
    def test_the_policy_allows_one_model_through_one_guardrail(self):
        policy = lab.invoke_policy('abc123')
        statement = policy['Statement'][0]
        self.assertEqual(statement['Resource'], [f'arn:aws:bedrock:{lab.REGION}::foundation-model/{lab.ALLOWED_MODEL}'])
        self.assertIn('bedrock:GuardrailIdentifier', statement['Condition']['StringEquals'])
        self.assertNotIn('bedrock:CreateGuardrail', json.dumps(policy))
        self.assertEqual([item['Sid'] for item in policy['Statement']],
                         ['InvokeOneModelThroughTheGuardrail', 'UseTheGuardrail'])


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_guardrail_check_names_a_missing_policy(self):
        with patch.object(lab, '_guardrail', return_value=None):
            self.assertFalse(lab._check_guardrail()[0])
        partial = {'status': 'READY', 'contentPolicy': {'filters': lab.CONTENT_FILTERS}}
        with patch.object(lab, '_guardrail', return_value=partial):
            ok, message = lab._check_guardrail()
            self.assertFalse(ok)
            self.assertIn('denied topic', message)
        with patch.object(lab, '_guardrail', return_value={'status': 'READY', **GUARDRAIL}):
            self.assertTrue(lab._check_guardrail()[0])

    def test_version_check_wants_version_one(self):
        lab._marker('version-the-guardrail', {'message': 'done'})
        with patch.object(lab, '_guardrail', return_value={'status': 'READY', **GUARDRAIL}), \
                patch.object(lab, '_versions', return_value=['DRAFT']):
            self.assertFalse(lab._check_version()[0])
        with patch.object(lab, '_guardrail', return_value={'status': 'READY', **GUARDRAIL}), \
                patch.object(lab, '_versions', return_value=['1', 'DRAFT']):
            self.assertTrue(lab._check_version()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('bedrock')], ['guardrails-least-privilege'])
        tags = LAB_EXAM_TAGS[('bedrock', 'guardrails-least-privilege')]
        self.assertIn({'exam': 'AIF-C01', 'task': '4.1', 'primary': True}, tags)
        self.assertIn({'exam': 'AIF-C01', 'task': '5.1', 'primary': False}, tags)
        self.assertIn('bedrock', [batch['service'] for batch in LAB_BATCH_ORDER])
        for key in ('responsible-ai', 'securing-ai', 'ai-governance'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('bedrock', 'guardrails-least-privilege'), card['related_labs'])
            self.assertFalse(card.get('planned'))

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_the_checklist_names_where_each_practice_is_covered(self):
        self.assertGreaterEqual(len(lab.CHECKLIST), 8)
        for practice, where in lab.CHECKLIST:
            self.assertTrue(practice and where)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('bedrock', 'guardrails-least-privilege', 'nope')
