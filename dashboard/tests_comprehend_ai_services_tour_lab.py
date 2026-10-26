"""AI services tour lab: Floci answers the AI services with fixed results, so the lab judges each use case itself.
These tests cover that logic, the checks, and registration."""

import importlib
import io
import wave
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.comprehend_labs')


class InputTests(SimpleTestCase):
    def test_png_is_a_valid_image(self):
        image = lab.png(4, 3, (1, 2, 3))
        self.assertTrue(image.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertIn(b'IHDR', image)
        self.assertTrue(image.endswith(b'IEND\xaeB`\x82'))

    def test_silent_wav_has_the_requested_length(self):
        with wave.open(io.BytesIO(lab.silent_wav(seconds=0.5, rate=8000))) as audio:
            self.assertEqual(audio.getnframes(), 4000)
            self.assertEqual(audio.getnchannels(), 1)


class LogicTests(SimpleTestCase):
    def test_keyword_rule(self):
        self.assertEqual(lab.keyword_sentiment('The zipper broke. I want a refund.'), 'NEGATIVE')
        self.assertEqual(lab.keyword_sentiment('Love it, fast delivery'), 'POSITIVE')
        self.assertEqual(lab.keyword_sentiment('It arrived on Tuesday'), 'NEUTRAL')
        # The rule's weakness: it cannot read negation.
        self.assertEqual(lab.keyword_sentiment('Not bad, but not great either.'), 'POSITIVE')

    def test_route_splits_on_the_threshold(self):
        routed = lab.route([{'Confidence': 99.9}, {'Confidence': 90}, {'Confidence': 70.0}, {}])
        self.assertEqual(len(routed['Automatic']), 2)
        self.assertEqual(len(routed['HumanReview']), 2)

    def test_use_cases_include_problems_that_need_no_ai(self):
        choices = [choice for _, choice, _ in lab.USE_CASES]
        self.assertEqual(sum(choice.startswith('No AI') for choice in choices), 2)
        for service in ('Amazon Comprehend', 'Amazon Rekognition', 'Amazon Textract', 'Amazon Translate',
                        'Amazon Transcribe', 'Amazon SageMaker AI'):
            self.assertIn(service, choices)

    def test_predictions_come_from_earlier_steps(self):
        cache.clear()
        self.assertEqual(lab._predictions_from_markers(), [])
        lab._marker('comprehend-reviews', {'message': 'x', 'sentiments': [{'id': 'r1', 'sentiment': 'NEUTRAL',
                                                                             'confidence': 70.0}]})
        lab._marker('rekognition-images', {'message': 'x', 'labels': [{'Name': 'Jacket', 'Confidence': 98.0}]})
        predictions = lab._predictions_from_markers()
        self.assertEqual([item['Source'] for item in predictions], ['Comprehend sentiment', 'Rekognition label'])


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_steps_need_the_inputs_in_s3(self):
        lab._marker('comprehend-reviews', {'message': 'done'})
        with patch.object(lab, '_objects', return_value={lab.REVIEWS_KEY}):
            ok, message = lab._check_comprehend()
            self.assertFalse(ok)
            self.assertIn(lab.PHOTO_KEY, message)
        with patch.object(lab, '_objects', return_value={lab.REVIEWS_KEY, lab.PHOTO_KEY, lab.RECEIPT_KEY, lab.CALL_KEY}):
            self.assertTrue(lab._check_comprehend()[0])

    def test_transcribe_needs_a_completed_job(self):
        lab._marker('transcribe-call', {'message': 'done'})
        with patch.object(lab, '_job', return_value=None):
            self.assertFalse(lab._check_transcribe()[0])
        with patch.object(lab, '_job', return_value={'TranscriptionJobStatus': 'IN_PROGRESS'}):
            self.assertFalse(lab._check_transcribe()[0])
        with patch.object(lab, '_job', return_value={'TranscriptionJobStatus': 'COMPLETED'}):
            self.assertTrue(lab._check_transcribe()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('comprehend')], ['ai-services-tour'])
        self.assertIn({'exam': 'AIF-C01', 'task': '1.2', 'primary': True}, LAB_EXAM_TAGS[('comprehend', 'ai-services-tour')])
        self.assertIn({'exam': 'CLF-C02', 'task': '3.7', 'primary': True}, LAB_EXAM_TAGS[('comprehend', 'ai-services-tour')])
        self.assertEqual(LAB_BATCH_ORDER[-1]['service'], 'comprehend')
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'ai-use-cases')
        self.assertIn(('comprehend', 'ai-services-tour'), card['related_labs'])
        self.assertFalse(card.get('planned'))

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('comprehend', 'ai-services-tour', 'nope')
