"""DynamoDB on-demand backups, restore from backup and point-in-time restore endpoints."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

BACKUP_ARN = 'arn:aws:dynamodb:us-east-1:000000000000:table/orders/backup/01793633629055-6181e5a3'


def send(client, method, url, body):
    return getattr(client, method)(url, data=json.dumps(body), content_type='application/json')


@patch('dashboard.dynamodb_api.FlociClientFactory')
class DynamoDBBackupTests(SimpleTestCase):
    def ddb(self, factory):
        return factory.return_value.client.return_value

    def backups_url(self):
        return reverse('dashboard:dynamodb-table-backups', kwargs={'table_name': 'orders'})

    def test_list_backups_newest_first_with_pitr_window(self, factory):
        ddb = self.ddb(factory)
        ddb.list_backups.side_effect = [
            {'BackupSummaries': [{'BackupArn': 'b1', 'BackupName': 'old', 'BackupStatus': 'AVAILABLE', 'BackupType': 'USER',
                                  'TableName': 'orders', 'BackupCreationDateTime': datetime(2026, 11, 1, tzinfo=timezone.utc)}],
             'LastEvaluatedBackupArn': 'b1'},
            {'BackupSummaries': [{'BackupArn': 'b2', 'BackupName': 'new', 'BackupStatus': 'AVAILABLE', 'BackupType': 'USER',
                                  'TableName': 'orders', 'BackupCreationDateTime': datetime(2026, 11, 2, tzinfo=timezone.utc)}]},
        ]
        ddb.describe_continuous_backups.return_value = {'ContinuousBackupsDescription': {
            'PointInTimeRecoveryDescription': {'PointInTimeRecoveryStatus': 'ENABLED'}}}
        data = self.client.get(self.backups_url()).json()
        self.assertEqual([item['name'] for item in data['backups']], ['new', 'old'])
        self.assertEqual(data['continuous_backups']['PointInTimeRecoveryDescription']['PointInTimeRecoveryStatus'], 'ENABLED')
        self.assertEqual(ddb.list_backups.call_args_list[1].kwargs, {'TableName': 'orders', 'ExclusiveStartBackupArn': 'b1'})

    def test_create_backup(self, factory):
        ddb = self.ddb(factory)
        ddb.create_backup.return_value = {'BackupDetails': {'BackupArn': BACKUP_ARN, 'BackupName': 'orders-nightly', 'BackupStatus': 'CREATING'}}
        response = send(self.client, 'post', self.backups_url(), {'backup_name': ' orders-nightly '})
        self.assertEqual(response.json()['arn'], BACKUP_ARN)
        ddb.create_backup.assert_called_once_with(TableName='orders', BackupName='orders-nightly')

    def test_create_backup_rejects_short_name(self, factory):
        response = send(self.client, 'post', self.backups_url(), {'backup_name': 'b1'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Backup name', response.json()['error'])
        self.ddb(factory).create_backup.assert_not_called()

    def test_pitr_toggle_still_uses_put(self, factory):
        ddb = self.ddb(factory)
        ddb.update_continuous_backups.return_value = {'ContinuousBackupsDescription': {}}
        send(self.client, 'put', self.backups_url(), {'enabled': True})
        ddb.update_continuous_backups.assert_called_once_with(
            TableName='orders', PointInTimeRecoverySpecification={'PointInTimeRecoveryEnabled': True})

    def test_restore_from_backup(self, factory):
        ddb = self.ddb(factory)
        ddb.restore_table_from_backup.return_value = {'TableDescription': {'TableStatus': 'CREATING'}}
        response = send(self.client, 'post', reverse('dashboard:dynamodb-backup'), {'backup_arn': BACKUP_ARN, 'target_table_name': 'orders-restored'})
        self.assertEqual(response.json(), {'table': 'orders-restored', 'status': 'CREATING', 'backup_arn': BACKUP_ARN})
        ddb.restore_table_from_backup.assert_called_once_with(TargetTableName='orders-restored', BackupArn=BACKUP_ARN)

    def test_restore_from_backup_rejects_bad_table_name(self, factory):
        response = send(self.client, 'post', reverse('dashboard:dynamodb-backup'), {'backup_arn': BACKUP_ARN, 'target_table_name': 'a b'})
        self.assertEqual(response.status_code, 400)
        self.ddb(factory).restore_table_from_backup.assert_not_called()

    def test_delete_backup(self, factory):
        ddb = self.ddb(factory)
        ddb.delete_backup.return_value = {'BackupDescription': {'BackupDetails': {'BackupStatus': 'DELETED'}}}
        response = send(self.client, 'delete', reverse('dashboard:dynamodb-backup'), {'backup_arn': BACKUP_ARN})
        self.assertTrue(response.json()['deleted'])
        ddb.delete_backup.assert_called_once_with(BackupArn=BACKUP_ARN)

    def restore_url(self):
        return reverse('dashboard:dynamodb-table-restore', kwargs={'table_name': 'orders'})

    def test_point_in_time_restore_at_custom_time(self, factory):
        ddb = self.ddb(factory)
        ddb.restore_table_to_point_in_time.return_value = {'TableDescription': {'TableStatus': 'CREATING'}}
        response = send(self.client, 'post', self.restore_url(), {'target_table_name': 'orders-pitr', 'restore_time': '2026-11-02T15:30:00Z'})
        self.assertEqual(response.json()['restore_time'], '2026-11-02T15:30:00+00:00')
        ddb.restore_table_to_point_in_time.assert_called_once_with(
            SourceTableName='orders', TargetTableName='orders-pitr', RestoreDateTime=datetime(2026, 11, 2, 15, 30, tzinfo=timezone.utc))
        ddb.describe_continuous_backups.assert_not_called()

    def test_point_in_time_restore_rejects_bad_time(self, factory):
        response = send(self.client, 'post', self.restore_url(), {'target_table_name': 'orders-pitr', 'restore_time': 'yesterday'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('ISO 8601', response.json()['error'])

    def test_latest_restore_sends_current_time_not_floci_stale_latest(self, factory):
        ddb = self.ddb(factory)
        stale = datetime.now(timezone.utc) - timedelta(hours=1)
        ddb.describe_continuous_backups.return_value = {'ContinuousBackupsDescription': {'PointInTimeRecoveryDescription': {
            'PointInTimeRecoveryStatus': 'ENABLED', 'EarliestRestorableDateTime': stale, 'LatestRestorableDateTime': stale}}}
        ddb.restore_table_to_point_in_time.return_value = {'TableDescription': {'TableStatus': 'CREATING'}}
        send(self.client, 'post', self.restore_url(), {'target_table_name': 'orders-pitr'})
        kwargs = ddb.restore_table_to_point_in_time.call_args.kwargs
        self.assertNotIn('UseLatestRestorableTime', kwargs)
        self.assertLess(datetime.now(timezone.utc) - kwargs['RestoreDateTime'], timedelta(seconds=30))

    def test_latest_restore_needs_pitr(self, factory):
        ddb = self.ddb(factory)
        ddb.describe_continuous_backups.return_value = {'ContinuousBackupsDescription': {
            'PointInTimeRecoveryDescription': {'PointInTimeRecoveryStatus': 'DISABLED'}}}
        response = send(self.client, 'post', self.restore_url(), {'target_table_name': 'orders-pitr'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('point-in-time recovery', response.json()['error'])
        ddb.restore_table_to_point_in_time.assert_not_called()
