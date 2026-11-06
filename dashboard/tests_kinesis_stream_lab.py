"""Kinesis stream processing lab: these tests cover the click data, the poison record, the record counting, the
dispatch from the Kinesis module, and registration."""

import importlib
import json
from unittest.mock import patch

from django.test import SimpleTestCase

from .labs import kinesis_labs
from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.kinesis_stream_labs')


class DataTests(SimpleTestCase):
    def test_only_the_poison_record_is_not_json(self):
        for event_id in lab.FIRST_BATCH + lab.SECOND_BATCH:
            data = lab.record_data(event_id)
            if event_id == lab.POISON_ID:
                with self.assertRaises(ValueError):
                    json.loads(data)
            else:
                self.assertEqual(json.loads(data), lab.click(event_id))

    def test_count_records_matches_expected_views_and_flags_the_poison(self):
        records = [{'Data': lab.record_data(event_id)} for event_id in lab.FIRST_BATCH]
        views, invalid = lab.count_records(records)
        self.assertEqual(views, lab.expected_views(lab.FIRST_BATCH))
        self.assertEqual(invalid, 1)
        self.assertEqual(sum(views.values()), len(lab.FIRST_BATCH) - 1)


class DispatchTests(SimpleTestCase):
    def test_kinesis_module_dispatches_to_the_stream_lab(self):
        # kinesis_labs imports this module relative to its own package, so patch that copy.
        target = importlib.import_module(f'{kinesis_labs.__package__}.kinesis_stream_labs')
        with patch.object(target, 'run_step', return_value={'ok': True}) as run, \
                patch.object(target, 'status', return_value={'lab': 'stream'}), \
                patch.object(target, 'reset', return_value={'reset': True}):
            self.assertEqual(kinesis_labs.run_step('kinesis', 'stream-processing', 'create-stream'), {'ok': True})
            run.assert_called_once_with('create-stream')
            self.assertEqual(kinesis_labs.status('kinesis', 'stream-processing'), {'lab': 'stream'})
            self.assertEqual(kinesis_labs.reset('kinesis', 'stream-processing'), {'reset': True})


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertEqual([item['key'] for item in labs_for_service('kinesis')],
                         ['streaming-data-lake', 'stream-processing'])
        tags = LAB_EXAM_TAGS[('kinesis', 'stream-processing')]
        self.assertIn({'exam': 'DEA-C01', 'task': '1.1', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'streaming-ingestion')
        self.assertIn(('kinesis', 'stream-processing'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
