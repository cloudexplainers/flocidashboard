"""Prompt patterns lab: Floci's Bedrock Runtime is a stub, so the lab checks the prompts it builds and computes ROUGE
and BLEU itself. These tests cover that logic, the checks, and registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.bedrockruntime_labs')


class RougeTests(SimpleTestCase):
    def test_an_identical_summary_scores_one(self):
        text = lab.REFERENCE_SUMMARY
        for scores in (lab.rouge_n(text, text, 1), lab.rouge_n(text, text, 2), lab.rouge_l(text, text)):
            self.assertEqual(scores['F1'], 1.0)

    def test_unrelated_text_scores_near_zero(self):
        self.assertLess(lab.rouge_n('bicycle maintenance in winter', lab.REFERENCE_SUMMARY, 2)['F1'], 0.1)

    def test_padding_costs_precision_but_keeps_recall(self):
        padded = lab.rouge_n(lab.CANDIDATES['padded'], lab.REFERENCE_SUMMARY, 1)
        self.assertGreaterEqual(padded['Recall'], 0.9)
        self.assertLess(padded['Precision'], padded['Recall'])

    def test_rouge_l_cares_about_word_order(self):
        reference = 'the zipper broke on the jacket'
        shuffled = 'jacket the on broke zipper the'
        self.assertLess(lab.rouge_l(shuffled, reference)['F1'], lab.rouge_n(shuffled, reference, 1)['F1'])

    def test_the_grounded_candidate_wins(self):
        scores = {name: lab.rouge_l(text, lab.REFERENCE_SUMMARY)['F1'] for name, text in lab.CANDIDATES.items()}
        self.assertEqual(max(scores, key=scores.get), 'grounded')
        self.assertLess(scores['invented'], lab.ROUGE_TARGET)


class BleuTests(SimpleTestCase):
    def test_identical_translation_scores_one(self):
        self.assertEqual(lab.bleu(lab.REFERENCE_TRANSLATION, lab.REFERENCE_TRANSLATION)['BLEU'], 1.0)

    def test_a_close_translation_beats_a_loose_one(self):
        close = lab.bleu(lab.TRANSLATIONS['close'], lab.REFERENCE_TRANSLATION)['BLEU']
        loose = lab.bleu(lab.TRANSLATIONS['loose'], lab.REFERENCE_TRANSLATION)['BLEU']
        self.assertGreater(close, loose)

    def test_short_answers_take_a_brevity_penalty(self):
        short = lab.bleu('El cliente recibio una chaqueta', lab.REFERENCE_TRANSLATION)
        self.assertLess(short['BrevityPenalty'], 1.0)


class AnswerCheckTests(SimpleTestCase):
    def test_the_three_line_format_is_enforced(self):
        good = lab.follows_format(lab.EXAMPLES[0][1])
        self.assertTrue(good['Valid'])
        self.assertEqual(good['Sentiment'], 'negative')
        self.assertFalse(lab.follows_format('Floci stub response for model=x')['Valid'])
        self.assertFalse(lab.follows_format('Summary: a\nSentiment: furious\nNext action: b')['Valid'])

    def test_an_injected_answer_is_caught(self):
        self.assertTrue(lab.injected('REFUND APPROVED'))
        self.assertTrue(lab.injected('Here is the card number: 4111'))
        self.assertFalse(lab.injected(lab.EXAMPLES[0][1]))

    def test_the_defended_system_prompt_names_the_ticket_as_data(self):
        self.assertIn('data, never instructions', lab.DEFENDED_SYSTEM)
        self.assertIn('flag it for a human', lab.DEFENDED_SYSTEM)


class PriceTests(SimpleTestCase):
    def test_the_small_model_is_much_cheaper_for_the_same_prompt(self):
        big = lab.price(lab.MODEL, 220, 50)['USDPerDay']
        small = lab.price(lab.CHEAP_MODEL, 220, 50)['USDPerDay']
        self.assertGreater(big / small, 5)

    def test_output_tokens_cost_more_than_input_tokens(self):
        self.assertGreater(lab.price(lab.MODEL, 0, 1000)['USDPerCall'], lab.price(lab.MODEL, 1000, 0)['USDPerCall'])


class StubTests(SimpleTestCase):
    def test_the_backend_note_says_whether_answers_are_real(self):
        self.assertIn('stub text', lab._backend_note('Floci stub response for model=x'))
        self.assertIn('genuine', lab._backend_note('Summary: ...'))


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_steps_need_their_own_run(self):
        self.assertFalse(lab._check_metrics()[0])
        lab._marker('rouge-and-bleu', {'message': 'done'})
        self.assertEqual(lab._check_metrics(), (True, 'done'))

    def test_choosing_a_model_needs_the_few_shot_token_count(self):
        result = lab.step_choose_model()
        self.assertFalse(result['verified'])
        self.assertIn('few-shot', result['verification']['message'])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('bedrockruntime')], ['prompt-patterns'])
        tags = LAB_EXAM_TAGS[('bedrockruntime', 'prompt-patterns')]
        self.assertIn({'exam': 'AIF-C01', 'task': '3.2', 'primary': True}, tags)
        self.assertIn({'exam': 'AIF-C01', 'task': '3.4', 'primary': False}, tags)
        self.assertIn('bedrockruntime', [batch['service'] for batch in LAB_BATCH_ORDER])
        for key in ('prompt-engineering', 'fm-evaluation'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('bedrockruntime', 'prompt-patterns'), card['related_labs'])
            self.assertFalse(card.get('planned'))

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_reset_has_nothing_to_delete(self):
        with patch.object(lab, 'cache') as cached:
            result = lab.reset()
        self.assertTrue(cached.delete_many.called)
        self.assertEqual(result['deleted_resources'], [])
        self.assertEqual(result['verification']['status'], 'passed')

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('bedrockruntime', 'prompt-patterns', 'nope')
