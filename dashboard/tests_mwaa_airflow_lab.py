"""Airflow on MWAA lab: Floci runs a real Airflow behind the MWAA CLI token. These tests cover the DAG source, the
CLI output parsing, and registration without starting Airflow."""

import ast
import importlib
from unittest.mock import patch

from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.mwaa_airflow_labs')


class DagSourceTests(SimpleTestCase):
    def test_dag_parses_and_the_broken_copy_does_not(self):
        ast.parse(lab.DAG_SOURCE)
        with self.assertRaises(SyntaxError):
            ast.parse(lab.BROKEN_DAG_SOURCE)

    def test_dag_is_manual_with_retries_and_a_failure_callback(self):
        self.assertIn('schedule=None', lab.DAG_SOURCE)
        self.assertIn("'retries': 1", lab.DAG_SOURCE)
        self.assertIn('on_failure_callback', lab.DAG_SOURCE)
        for task in lab.TASKS:
            self.assertIn(f"task_id='{task}'", lab.DAG_SOURCE)

    def test_expected_totals_match_the_orders(self):
        for day, rows in lab.ORDERS.items():
            totals: dict[str, float] = {}
            for _, customer, amount in rows:
                totals[customer] = totals.get(customer, 0) + float(amount)
            self.assertEqual(totals, lab.EXPECTED_TOTALS[day])
        self.assertTrue(lab.orders_csv(lab.GOOD_DAY).startswith('order_id,customer,amount\n'))


class CliParsingTests(SimpleTestCase):
    def test_cli_json_skips_log_lines_before_the_json(self):
        with patch.object(lab, 'cli', return_value={'stdout': 'WARNING something\n[{"dag_id": "x"}]', 'stderr': ''}):
            self.assertEqual(lab.cli_json('dags list -o json'), [{'dag_id': 'x'}])
        with patch.object(lab, 'cli', return_value={'stdout': 'no json here', 'stderr': ''}):
            self.assertIsNone(lab.cli_json('dags list -o json'))

    def test_dag_ids_ignore_files_not_yet_in_the_database(self):
        rows = [{'dag_id': 'orders_daily', 'is_paused': None}, {'dag_id': 'other', 'is_paused': True}]
        with patch.object(lab, 'cli_json', return_value=rows):
            self.assertEqual(lab.dag_ids(), ['other'])

    def test_task_states_map_task_ids(self):
        rows = [{'task_id': 'extract', 'state': 'success'}, {'task_id': 'load', 'state': 'failed'}]
        with patch.object(lab, 'cli_json', return_value=rows):
            self.assertEqual(lab.task_states(lab.GOOD_RUN), {'extract': 'success', 'load': 'failed'})


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('airflow-pipeline', [item['key'] for item in labs_for_service('mwaa')])
        tags = LAB_EXAM_TAGS[('mwaa', 'airflow-pipeline')]
        self.assertIn({'exam': 'DEA-C01', 'task': '1.3', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'pipeline-orchestration')
        self.assertIn(('mwaa', 'airflow-pipeline'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
