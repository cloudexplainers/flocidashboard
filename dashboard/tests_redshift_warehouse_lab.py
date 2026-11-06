"""Redshift Serverless warehouse lab: Floci runs PostgreSQL behind the workgroup, so these tests cover the pure
helpers (sample data, CSV parsing, totals, masking), the SQL the lab sends, and registration."""

import importlib

from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.redshift_warehouse_labs')


class HelperTests(SimpleTestCase):
    def test_sample_data_is_deterministic_and_sized(self):
        rows = lab.sale_rows()
        self.assertEqual(len(rows), lab.ROW_COUNT)
        self.assertEqual(rows, lab.sale_rows())
        self.assertEqual(len({row['sale_id'] for row in rows}), len(rows))

    def test_staged_files_split_rows_and_carry_one_bad_line(self):
        files = lab.staged_files()
        self.assertEqual(len(files), 2)
        good, errors = [], []
        for body in files.values():
            rows, bad = lab.parse_rows(body)
            good += rows
            errors += bad
        self.assertEqual(len(good), lab.ROW_COUNT)
        self.assertEqual(len(errors), 1)
        self.assertIn(str(lab.BAD_SALE_ID), errors[0]['raw_line'])

    def test_manifest_lists_every_file_as_mandatory(self):
        files = lab.staged_files()
        entries = lab.manifest(files)['entries']
        self.assertEqual(len(entries), len(files))
        self.assertTrue(all(entry['mandatory'] for entry in entries))

    def test_rolling_average_uses_at_most_seven_days(self):
        totals = {(f'2026-10-{day:02d}', 'S1'): float(day) for day in range(1, 11)}
        averages = lab.rolling_average(totals, 'S1')
        self.assertEqual(averages['2026-10-01'], 1.0)
        self.assertEqual(averages['2026-10-10'], round(sum(range(4, 11)) / 7, 2))

    def test_mask_email_keeps_first_letter_and_domain(self):
        self.assertEqual(lab.mask_email('dara@example.com'), 'd***@example.com')

    def test_insert_sql_quotes_strings(self):
        sql = lab.insert_sql([{**lab.sale_rows()[0], 'customer_email': "o'neil@example.com"}])
        self.assertIn("'o''neil@example.com'", sql)

    def test_records_to_rows_reads_data_api_fields(self):
        result = {'Records': [[{'stringValue': 'S1'}, {'isNull': True}, {'doubleValue': 2.5}]]}
        self.assertEqual(lab.records_to_rows(result), [['S1', None, 2.5]])

    def test_portable_ddl_has_no_redshift_only_keywords(self):
        for sql in lab.PORTABLE_DDL:
            for word in ('DISTKEY', 'SORTKEY', 'DISTSTYLE', 'ENCODE'):
                self.assertNotIn(word, sql.upper())
        self.assertTrue(any('DISTKEY' in sql.upper() for sql in lab.REDSHIFT_DDL))


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertEqual([item['key'] for item in labs_for_service('redshift')], ['serverless-warehouse'])
        tags = LAB_EXAM_TAGS[('redshift', 'serverless-warehouse')]
        self.assertIn({'exam': 'DEA-C01', 'task': '2.1', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'choosing-data-stores')
        self.assertIn(('redshift', 'serverless-warehouse'), card['related_labs'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
