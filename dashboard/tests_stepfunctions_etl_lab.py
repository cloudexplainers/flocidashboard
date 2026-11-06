"""Event-driven batch ETL lab: Floci runs the Lambda transform, the state machine, the S3 event rule, and Athena.
These tests run the transform's code against fake S3 and DynamoDB clients, and cover the workflow definition, the
checks, the step-function dispatch, and registration."""

import importlib
import io
import json
import sys
import types
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import stepfunctions_labs
from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.stepfunctions_etl_labs')


class FakeStore:
    """Just enough of S3 and DynamoDB for the transform."""

    def __init__(self, objects):
        self.objects = dict(objects)
        self.items = {}

    def client(self, name):
        fake = MagicMock()
        if name == 's3':
            fake.head_object.side_effect = lambda Bucket, Key: {'ETag': f'"{hash(self.objects[Key])}"'}
            fake.get_object.side_effect = lambda Bucket, Key: {'Body': io.BytesIO(self.objects[Key].encode())}
            fake.put_object.side_effect = lambda Bucket, Key, Body, **_: self.objects.__setitem__(Key, Body.decode())
        else:
            fake.get_item.side_effect = lambda TableName, Key: (
                {'Item': self.items[Key['object_key']['S']]} if Key['object_key']['S'] in self.items else {})
            fake.put_item.side_effect = lambda TableName, Item: self.items.__setitem__(Item['object_key']['S'], Item)
        return fake


def _transform(store):
    boto3 = types.ModuleType('boto3')
    boto3.client = store.client
    module = types.ModuleType('transform')
    env = {'MANIFEST_TABLE': lab.MANIFEST, 'CURATED_PREFIX': lab.CURATED, 'REJECTED_PREFIX': lab.REJECTED}
    with patch.dict(sys.modules, {'boto3': boto3}), patch.dict('os.environ', env):
        exec(compile(lab.TRANSFORM_SOURCE, 'index.py', 'exec'), module.__dict__)
    return module.handler


def _store():
    return FakeStore({**{lab.incoming(name): body for name, body in lab.FILES.items()},
                      lab.incoming(lab.LEGACY_FILE): lab.LEGACY_BODY})


class TransformTests(SimpleTestCase):
    def test_rows_land_in_their_order_date_partition_and_bad_rows_are_rejected(self):
        store = _store()
        result = _transform(store)({'bucket': lab.BUCKET, 'key': lab.incoming(lab.BATCH[1])}, None)
        self.assertEqual((result['status'], result['rows'], result['rejected']), ('processed', 4, 1))
        late = store.objects[f'{lab.CURATED}dt={lab.DAY_ONE}/orders-{lab.DAY_TWO}.json']
        self.assertEqual(json.loads(late), {'order_id': 204, 'customer': 'ben', 'amount': 5.5})
        self.assertIn(f'{lab.REJECTED}orders-{lab.DAY_TWO}.json', store.objects)
        self.assertEqual(store.items[lab.incoming(lab.BATCH[1])]['status'], {'S': 'DONE'})

    def test_a_rerun_of_the_same_file_is_skipped(self):
        store = _store()
        handler = _transform(store)
        event = {'bucket': lab.BUCKET, 'key': lab.incoming(lab.BATCH[0])}
        handler(event, None)
        written = dict(store.objects)
        self.assertEqual(handler(event, None)['status'], 'skipped')
        self.assertEqual(store.objects, written)

    def test_a_changed_file_is_processed_again(self):
        store = _store()
        handler = _transform(store)
        event = {'bucket': lab.BUCKET, 'key': lab.incoming(lab.LEGACY_FILE)}
        with self.assertRaisesRegex(ValueError, 'BadHeader'):
            handler(event, None)
        self.assertEqual(store.items[event['key']]['status'], {'S': 'FAILED'})
        store.objects[event['key']] = lab.FIXED_LEGACY_BODY
        self.assertEqual(handler(event, None)['rows'], 3)
        self.assertEqual(store.items[event['key']]['status'], {'S': 'DONE'})

    def test_dry_run_writes_nothing(self):
        store = _store()
        before = dict(store.objects)
        result = _transform(store)({'bucket': lab.BUCKET, 'key': lab.incoming(lab.BATCH[1]), 'dryRun': True}, None)
        self.assertEqual(result['days'], [lab.DAY_ONE, lab.DAY_TWO])
        self.assertEqual(store.objects, before)
        self.assertEqual(store.items, {})

    def test_expected_totals_match_the_sample_files(self):
        store = _store()
        handler = _transform(store)
        store.objects[lab.incoming(lab.LEGACY_FILE)] = lab.FIXED_LEGACY_BODY
        store.objects[lab.incoming(lab.NEW_FILE)] = lab.NEW_BODY
        for name in [*lab.BATCH, lab.NEW_FILE]:
            handler({'bucket': lab.BUCKET, 'key': lab.incoming(name)}, None)
        counts, revenue = {}, {}
        for key, body in store.objects.items():
            if key.startswith(lab.CURATED):
                day = key[len(lab.CURATED):].split('/')[0].removeprefix('dt=')
                rows = [json.loads(line) for line in body.splitlines()]
                counts[day] = counts.get(day, 0) + len(rows)
                revenue[day] = round(revenue.get(day, 0) + sum(row['amount'] for row in rows), 2)
        self.assertEqual(counts, lab.EXPECTED_FINAL)
        self.assertEqual(revenue, lab.EXPECTED_AMOUNTS)


class WorkflowTests(SimpleTestCase):
    def test_map_task_retries_transient_errors_and_catches_the_rest(self):
        definition = lab.workflow_definition()
        task = definition['States']['TransformFiles']['ItemProcessor']['States']['Transform']
        self.assertNotIn('States.ALL', task['Retry'][0]['ErrorEquals'])
        self.assertEqual(task['Catch'][0]['ErrorEquals'], ['States.ALL'])
        self.assertEqual(definition['States']['AnyFailed']['Default'], 'Done')

    def test_event_pattern_only_matches_the_landing_prefix(self):
        self.assertEqual(lab.event_pattern()['detail']['object']['key'], [{'prefix': lab.INCOMING}])

    def test_results_by_file_reads_the_execution_output(self):
        output = {'results': [{'key': 'a', 'status': 'processed'}, {'key': 'b', 'status': 'failed', 'failed': True}]}
        self.assertEqual(lab.results_by_file(output), {'a': 'processed', 'b': 'failed'})


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_sweep_check_needs_skips_and_unchanged_rows(self):
        schedule = {'ScheduleExpression': lab.SCHEDULE_CRON,
                    'Target': {'Arn': lab.WORKFLOW_ARN, 'DeadLetterConfig': {'Arn': 'arn:dlq'}}}
        statuses = {lab.incoming(name): 'skipped' for name in [*lab.FILES, lab.NEW_FILE]}
        statuses[lab.incoming(lab.LEGACY_FILE)] = 'failed'
        lab._marker('nightly-sweep', {'statuses': statuses, 'before': {'a': 1}, 'after': {'a': 1}})
        with patch.object(lab, '_schedule', return_value=schedule):
            self.assertTrue(lab._check_sweep()[0])
            lab._marker('nightly-sweep', {'statuses': statuses, 'before': {'a': 1}, 'after': {'a': 2}})
            self.assertFalse(lab._check_sweep()[0])

    def test_query_check_compares_with_expected_counts(self):
        lab._marker('query-curated', {'counts': lab.EXPECTED_FINAL, 'revenue': lab.EXPECTED_AMOUNTS})
        with patch.object(lab, '_table', return_value={'Name': lab.TABLE}):
            self.assertTrue(lab._check_query()[0])
            lab._marker('query-curated', {'counts': {**lab.EXPECTED_FINAL, lab.DAY_ONE: 7},
                                          'revenue': lab.EXPECTED_AMOUNTS})
            self.assertFalse(lab._check_query()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_after_the_existing_step_functions_labs(self):
        self.assertEqual([item['key'] for item in labs_for_service('stepfunctions')],
                         ['order-processing-workflow', 'parallel-retry-workflow', 'etl-pipeline'])
        tags = LAB_EXAM_TAGS[('stepfunctions', 'etl-pipeline')]
        self.assertIn({'exam': 'DEA-C01', 'task': '1.2', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'data-transformation')
        self.assertIn(('stepfunctions', 'etl-pipeline'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_step_functions_module_dispatches_to_the_etl_lab(self):
        # stepfunctions_labs imports the ETL module relative to its own package, so patch that copy.
        target = importlib.import_module(f'{stepfunctions_labs.__package__}.stepfunctions_etl_labs')
        with patch.object(target, 'run_step', return_value={'ok': True}) as run, \
                patch.object(target, 'status', return_value={'lab': 'etl'}), \
                patch.object(target, 'reset', return_value={'reset': True}):
            self.assertEqual(stepfunctions_labs.run_step('stepfunctions', 'etl-pipeline', 'land-files'), {'ok': True})
            run.assert_called_once_with('land-files')
            self.assertEqual(stepfunctions_labs.status('stepfunctions', 'etl-pipeline'), {'lab': 'etl'})
            self.assertEqual(stepfunctions_labs.reset('stepfunctions', 'etl-pipeline'), {'reset': True})

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
