"""Migration toolkit lab: Floci runs the DMS, DataSync and Beanstalk control planes but moves no data, so the lab sorts
the portfolio, compares DataSync with Snowball, and applies the DMS table mappings itself. These tests cover that
logic, the checks, and registration."""

import importlib
import json
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.dms_labs')


class PortfolioTests(SimpleTestCase):
    def test_every_app_gets_a_strategy_and_all_7_rs_appear(self):
        plan = lab.assign_strategies(lab.portfolio_csv())
        self.assertEqual(len(plan), len(lab.PORTFOLIO))
        self.assertEqual({row['Strategy'] for row in plan},
                         {'Retire', 'Retain', 'Relocate', 'Rehost', 'Repurchase', 'Replatform', 'Refactor'})
        by_app = {row['App']: row['Strategy'] for row in plan}
        self.assertEqual(by_app['legacy-crm'], 'Repurchase')
        self.assertEqual(by_app['inventory-api'], 'Rehost')

    def test_unknown_signal(self):
        plan = lab.assign_strategies('app,description,signal\nx,y,mystery\n')
        self.assertEqual(plan[0]['Strategy'], 'Unknown')

    def test_six_caf_perspectives(self):
        self.assertEqual([name for name, _ in lab.CAF_PERSPECTIVES],
                         ['Business', 'People', 'Governance', 'Platform', 'Security', 'Operations'])


class TransferTests(SimpleTestCase):
    def test_network_time(self):
        self.assertEqual(lab.transfer_days(1), 0.1)
        self.assertEqual(lab.transfer_days(120), 13.9)
        self.assertEqual(lab.transfer_days(120, gbps=10), 1.4)

    def test_choice(self):
        self.assertEqual(lab.transfer_choice(1)['Choice'], 'AWS DataSync over the network')
        self.assertEqual(lab.transfer_choice(120)['Choice'], 'AWS Snowball Edge')
        self.assertEqual(lab.transfer_choice(120, gbps=10)['Choice'], 'AWS DataSync over the network')


class DmsLogicTests(SimpleTestCase):
    def test_migration_kind(self):
        self.assertEqual(lab.migration_kind('mysql', 'aurora'), 'homogeneous')
        self.assertEqual(lab.migration_kind('oracle', 'aurora-postgresql'), 'heterogeneous')
        self.assertEqual(lab.migration_kind('notanengine', 'aurora'), 'unknown')

    def test_table_mappings(self):
        tables = lab.mapped_tables(lab.SOURCE_TABLES)
        self.assertEqual([row['Source'] for row in tables], ['shop.customers', 'shop.orders', 'shop.order_items', 'shop.products'])
        self.assertTrue(all(row['Target'].startswith('orders.') for row in tables))
        prefix = {'rules': [{'rule-type': 'selection', 'rule-id': '1', 'rule-name': 'o', 'rule-action': 'include',
                             'object-locator': {'schema-name': 'shop', 'table-name': 'order%'}}]}
        self.assertEqual([row['Target'] for row in lab.mapped_tables(lab.SOURCE_TABLES, prefix)],
                         ['shop.orders', 'shop.order_items'])


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_endpoints_must_be_homogeneous(self):
        source = {'EndpointType': 'SOURCE', 'EngineName': 'mysql'}
        with patch.object(lab, '_endpoint', side_effect=[source, {'EndpointType': 'TARGET', 'EngineName': 'aurora'}]):
            self.assertTrue(lab._check_endpoints()[0])
        with patch.object(lab, '_endpoint', side_effect=[source, {'EndpointType': 'TARGET', 'EngineName': 'aurora-postgresql'}]):
            ok, message = lab._check_endpoints()
        self.assertFalse(ok)
        self.assertIn('heterogeneous', message)

    def test_task_settings(self):
        instance = {'ReplicationInstanceArn': 'ri'}
        source, target = {'EndpointArn': 's'}, {'EndpointArn': 't'}
        task = {'MigrationType': 'full-load-and-cdc', 'SourceEndpointArn': 's', 'TargetEndpointArn': 't',
                'ReplicationInstanceArn': 'ri', 'TableMappings': json.dumps(lab.TABLE_MAPPINGS)}
        endpoints = {lab.SOURCE_ENDPOINT: source, lab.TARGET_ENDPOINT: target}
        with patch.object(lab, '_instance', return_value=instance), \
                patch.object(lab, '_endpoint', side_effect=lambda name: endpoints[name]):
            self.assertIsNone(lab._check_task_settings(task))
            self.assertIn('full-load-and-cdc', lab._check_task_settings({**task, 'MigrationType': 'full-load'}))
            wide = {'rules': [lab.TABLE_MAPPINGS['rules'][0]]}
            self.assertIn('audit_log', lab._check_task_settings({**task, 'TableMappings': json.dumps(wide)}))

    def test_cutover_needs_a_stopped_task(self):
        with patch.object(lab, '_task', return_value={'Status': 'running'}):
            self.assertFalse(lab._check_cutover()[0])
        lab._marker('cutover', {'message': 'done'})
        with patch.object(lab, '_task', return_value={'Status': 'stopped'}):
            self.assertTrue(lab._check_cutover()[0])

    def test_beanstalk_needs_ready_and_green_python(self):
        ready = {'SolutionStackName': '64bit Amazon Linux 2023 v4.4.0 running Python 3.13', 'Status': 'Ready',
                 'Health': 'Green', 'CNAME': 'x'}
        with patch.object(lab, '_eb_application', return_value=True), patch.object(lab, '_eb_environment', return_value=ready):
            self.assertTrue(lab._check_beanstalk()[0])
        with patch.object(lab, '_eb_application', return_value=True), \
                patch.object(lab, '_eb_environment', return_value={**ready, 'Health': 'Grey'}):
            self.assertFalse(lab._check_beanstalk()[0])

    def test_environment_name_gets_a_suffix_when_taken(self):
        taken = ClientError({'Error': {'Code': 'InvalidParameterValue', 'Message': 'Environment x already exists.'}},
                            'CreateEnvironment')

        class FakeEb:
            calls: list[str] = []

            def create_environment(self, **kwargs):
                self.calls.append(kwargs['EnvironmentName'])
                if len(self.calls) < 3:
                    raise taken

        fake = FakeEb()
        with patch.object(lab, '_client', return_value=fake):
            self.assertEqual(lab._create_environment('stack'), f'{lab.EB_ENV}-3')
        self.assertEqual(fake.calls, [lab.EB_ENV, f'{lab.EB_ENV}-2', f'{lab.EB_ENV}-3'])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('dms')], ['migration-toolkit'])
        self.assertIn({'exam': 'CLF-C02', 'task': '1.3', 'primary': True}, LAB_EXAM_TAGS[('dms', 'migration-toolkit')])
        self.assertIn('dms', [batch['service'] for batch in LAB_BATCH_ORDER])
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'migration-caf')
        self.assertIn(('dms', 'migration-toolkit'), card['related_labs'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('dms', 'migration-toolkit', 'nope')
