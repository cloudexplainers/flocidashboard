"""Glue catalog and data quality lab: Floci stores crawlers and data quality rulesets but runs neither, so the lab
infers the schema and scores the rules itself. These tests cover those helpers, the sample data, and registration."""

import importlib

from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.glue_catalog_quality_labs')


class HelperTests(SimpleTestCase):
    def test_only_the_third_day_has_broken_rows(self):
        self.assertEqual([len(lab.day_rows(day)) for day in lab.DAYS], [6, 6, 10])

    def test_infer_schema_matches_the_table_columns(self):
        names = [name for name, _ in lab.COLUMNS]
        body = lab.to_csv(lab.day_rows(lab.DAYS[0]), names)
        self.assertEqual(lab.infer_schema(body), lab.COLUMNS)

    def test_partition_values_reads_dt_folders(self):
        keys = [lab.raw_key(day) for day in lab.DAYS] + [f'{lab.RAW_PREFIX}README.txt']
        self.assertEqual(lab.partition_values(keys), lab.DAYS)

    def test_score_partition_counts_failed_rules(self):
        clean = {'row_count': 6, 'missing_customer': 0, 'duplicate_ids': 0, 'bad_amount': 0, 'bad_country': 0}
        self.assertEqual(lab.score_partition(clean)['score'], 1.0)
        broken = {**clean, 'missing_customer': 1, 'duplicate_ids': 1, 'bad_amount': 1, 'bad_country': 1}
        self.assertEqual(lab.score_partition(broken)['score'], 0.2)
        self.assertLess(lab.score_partition(broken)['score'], lab.THRESHOLD)

    def test_split_rows_quarantines_each_broken_row_with_reasons(self):
        good, bad = lab.split_rows(lab.day_rows(lab.DAYS[2]))
        self.assertEqual(len(good), 6)
        self.assertEqual(len(bad), 4)
        self.assertTrue(all(row['dq_reasons'] for row in bad))
        self.assertIn('IsUnique "order_id"', [reason for row in bad for reason in row['dq_reasons']])

    def test_schema_versions_add_a_defaulted_field_or_break_a_type(self):
        self.assertIn('"default": "web"', lab.AVRO_V2)
        self.assertNotIn('channel', lab.AVRO_V1)
        self.assertIn('"order_id", "type": "string"', lab.AVRO_BREAKING)


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertEqual([item['key'] for item in labs_for_service('glue')], ['catalog-quality'])
        tags = LAB_EXAM_TAGS[('glue', 'catalog-quality')]
        self.assertIn({'exam': 'DEA-C01', 'task': '2.2', 'primary': True}, tags)
        for key in ('data-cataloging', 'data-quality'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('glue', 'catalog-quality'), card['related_labs'])
            self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
