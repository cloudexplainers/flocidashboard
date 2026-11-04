"""Backup and restore drill: Floci captures instance files in AMIs, restores DynamoDB and RDS to a point in time, and
keeps S3 versions, but has no EBS snapshots or AWS Backup restore jobs. These tests cover the drill math, the version
choice, the checks, and registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.backup_restore_drill_labs')

INCIDENT = '2026-11-04T12:00:00+00:00'


class DrillTests(SimpleTestCase):
    def test_report_measures_rto_and_rpo_against_the_targets(self):
        report = lab.drill_report(INCIDENT, {
            'table': {'restoredAt': '2026-11-04T12:04:00+00:00', 'recoveryPoint': '2026-11-04T11:59:59+00:00'},
            'server': {'restoredAt': '2026-11-04T12:20:00+00:00', 'recoveryPoint': '2026-11-04T11:00:00+00:00'},
        })
        self.assertEqual(report['resources']['table']['rtoMinutes'], 4.0)
        self.assertTrue(report['resources']['table']['meetsRto'])
        self.assertTrue(report['resources']['table']['meetsRpo'])
        self.assertFalse(report['resources']['server']['meetsRto'])
        self.assertFalse(report['resources']['server']['meetsRpo'])
        self.assertFalse(report['passed'])
        self.assertFalse(lab.drill_report(INCIDENT, {})['passed'])

    def test_orders_and_items(self):
        self.assertTrue(lab.orders_match([dict(order) for order in lab.ORDERS]))
        self.assertFalse(lab.orders_match([dict(order) for order in lab.ORDERS[:3]]))
        item = lab.ddb_item(lab.ORDERS[0])
        self.assertEqual(lab.plain_item(item), lab.ORDERS[0])

    def test_previous_good_version_skips_the_latest(self):
        versions = [{'VersionId': 'v2', 'IsLatest': True, 'LastModified': '2026-11-04T12:00:01'},
                    {'VersionId': 'v1', 'IsLatest': False, 'LastModified': '2026-11-04T11:00:00'}]
        bodies = {'v2': lab.BAD_ASSET, 'v1': lab.GOOD_ASSET}
        self.assertEqual(lab.previous_good_version(versions, bodies)['VersionId'], 'v1')
        self.assertIsNone(lab.previous_good_version(versions[:1], bodies))

    def test_minutes_between_handles_bad_input(self):
        self.assertIsNone(lab.minutes_between(None, INCIDENT))
        self.assertEqual(lab.minutes_between(INCIDENT, '2026-11-04T12:30:00Z'), 30.0)


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_report_check_needs_the_config_and_all_four_timings(self):
        config = lab.app_config('i-new', lab.RESTORED_TABLE, lab.BUCKET, lab.RESTORED_DB_ID)
        rows = {name: {'rtoMinutes': 1.0, 'rpoMinutes': 0.1, 'meetsRto': True, 'meetsRpo': True}
                for name in ('server', 'table', 'object', 'database')}
        lab._marker('repoint-and-report', {'report': {'resources': rows, 'passed': True}})
        with patch.object(lab, '_parameter', return_value=config), \
                patch.object(lab, '_instances', return_value={lab.RESTORED_WEB_NAME: {'InstanceId': 'i-new'}}):
            self.assertTrue(lab._check_report()[0])
            lab._marker('repoint-and-report', {'report': {'resources': {'table': rows['table']}, 'passed': True}})
            self.assertFalse(lab._check_report()[0])
        with patch.object(lab, '_parameter', return_value={}), \
                patch.object(lab, '_instances', return_value={lab.RESTORED_WEB_NAME: {'InstanceId': 'i-new'}}):
            self.assertFalse(lab._check_report()[0])

    def test_database_check_needs_a_time_before_the_incident(self):
        lab._marker('incident', {'at': INCIDENT})
        with patch.object(lab, '_db', return_value={'DBInstanceStatus': 'available'}):
            lab._marker('restore-database', {'restoreTime': '2026-11-04T11:59:59+00:00'})
            self.assertTrue(lab._check_database()[0])
            lab._marker('restore-database', {'restoreTime': '2026-11-04T12:00:05+00:00'})
            self.assertFalse(lab._check_database()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertEqual([item['key'] for item in labs_for_service('backup')], ['backup-dr-plan', 'restore-drill'])
        tags = LAB_EXAM_TAGS[('backup', 'restore-drill')]
        self.assertIn({'exam': 'SOA-C03', 'task': '2.3', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'backup-restore-operations')
        self.assertIn(('backup', 'restore-drill'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
