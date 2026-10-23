"""RDS and Aurora resilience: point-in-time restore, read replicas, failover and cluster snapshots."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase
from django.urls import reverse

from .labs import lab_status, reset_lab, run_lab_step
from .labs.registry import labs_for_service


def client_error(code: str, message: str = 'refused', operation: str = 'Operation') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, operation)


def post(test, name: str, body: dict | None = None, **kwargs):
    return test.client.post(
        reverse(f'dashboard:{name}', kwargs=kwargs or None),
        data=json.dumps(body or {}),
        content_type='application/json',
    )


class ResilienceRouteTests(SimpleTestCase):
    def test_routes_match_the_action_registry_paths(self):
        self.assertEqual(reverse('dashboard:rds-instance-restore-pitr', kwargs={'identifier': 'db'}), '/api/rds/instances/db/restore-point-in-time/')
        self.assertEqual(reverse('dashboard:rds-instance-read-replicas', kwargs={'identifier': 'db'}), '/api/rds/instances/db/read-replicas/')
        self.assertEqual(reverse('dashboard:rds-instance-promote', kwargs={'identifier': 'db'}), '/api/rds/instances/db/promote/')
        self.assertEqual(reverse('dashboard:rds-cluster-instances', kwargs={'identifier': 'c'}), '/api/rds/clusters/c/instances/')
        self.assertEqual(reverse('dashboard:rds-cluster-failover', kwargs={'identifier': 'c'}), '/api/rds/clusters/c/failover/')
        self.assertEqual(reverse('dashboard:rds-cluster-snapshots'), '/api/rds/cluster-snapshots/')
        self.assertEqual(reverse('dashboard:rds-cluster-snapshot-detail', kwargs={'identifier': 's'}), '/api/rds/cluster-snapshots/s/')
        self.assertEqual(reverse('dashboard:rds-cluster-snapshot-restore', kwargs={'identifier': 's'}), '/api/rds/cluster-snapshots/s/restore/')

    def test_new_endpoints_reject_get(self):
        for url in ('/api/rds/instances/db/promote/', '/api/rds/clusters/c/failover/', '/api/rds/cluster-snapshots/'):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 405)


class PointInTimeRestoreApiTests(SimpleTestCase):
    @patch('dashboard.rds_api._client')
    def test_latest_restorable_time_restores_to_a_new_instance(self, client_factory):
        client = client_factory.return_value
        client.restore_db_instance_to_point_in_time.return_value = {'DBInstance': {'DBInstanceIdentifier': 'orders-pitr', 'DBInstanceStatus': 'available'}}
        response = post(self, 'rds-instance-restore-pitr', {'target_identifier': 'orders-pitr', 'use_latest_restorable_time': True}, identifier='orders')
        self.assertEqual(response.status_code, 200)
        client.restore_db_instance_to_point_in_time.assert_called_once_with(
            SourceDBInstanceIdentifier='orders', TargetDBInstanceIdentifier='orders-pitr', UseLatestRestorableTime=True,
        )
        self.assertEqual(response.json()['instance']['DBInstanceStatus'], 'available')

    @patch('dashboard.rds_api._client')
    def test_custom_time_is_sent_as_utc_datetime_with_optional_class(self, client_factory):
        client = client_factory.return_value
        client.restore_db_instance_to_point_in_time.return_value = {'DBInstance': {}}
        response = post(self, 'rds-instance-restore-pitr', {'target_identifier': 'orders-pitr', 'restore_time': '2026-10-23T10:30:00Z', 'db_instance_class': 'db.t3.small'}, identifier='orders')
        self.assertEqual(response.status_code, 200)
        kwargs = client.restore_db_instance_to_point_in_time.call_args.kwargs
        self.assertEqual(kwargs['RestoreTime'], datetime(2026, 10, 23, 10, 30, tzinfo=timezone.utc))
        self.assertEqual(kwargs['DBInstanceClass'], 'db.t3.small')
        self.assertNotIn('UseLatestRestorableTime', kwargs)

    @patch('dashboard.rds_api._client')
    def test_exactly_one_restore_time_choice_is_required(self, client_factory):
        for body in ({'target_identifier': 'x'}, {'target_identifier': 'x', 'use_latest_restorable_time': True, 'restore_time': '2026-10-23T10:30:00Z'}):
            with self.subTest(body=body):
                self.assertEqual(post(self, 'rds-instance-restore-pitr', body, identifier='orders').status_code, 400)
        response = post(self, 'rds-instance-restore-pitr', {'target_identifier': 'x', 'restore_time': 'yesterday'}, identifier='orders')
        self.assertEqual(response.status_code, 400)
        self.assertIn('ISO 8601', response.json()['error'])
        client_factory.return_value.restore_db_instance_to_point_in_time.assert_not_called()

    @patch('dashboard.rds_api._client')
    def test_floci_refusal_is_shown_as_returned(self, client_factory):
        client_factory.return_value.restore_db_instance_to_point_in_time.side_effect = client_error(
            'PointInTimeRestoreNotEnabled', 'Point-in-time restore is not enabled for DB instance orders because its backup retention period is 0.',
        )
        response = post(self, 'rds-instance-restore-pitr', {'target_identifier': 'x', 'use_latest_restorable_time': True}, identifier='orders')
        self.assertEqual(response.status_code, 400)
        self.assertIn('backup retention period is 0', response.json()['error'])


class ReadReplicaApiTests(SimpleTestCase):
    @patch('dashboard.rds_api._client')
    def test_create_read_replica_names_source_and_replica(self, client_factory):
        client = client_factory.return_value
        client.create_db_instance_read_replica.return_value = {'DBInstance': {'DBInstanceIdentifier': 'orders-replica', 'ReadReplicaSourceDBInstanceIdentifier': 'orders'}}
        response = post(self, 'rds-instance-read-replicas', {'replica_identifier': 'orders-replica'}, identifier='orders')
        self.assertEqual(response.status_code, 200)
        client.create_db_instance_read_replica.assert_called_once_with(SourceDBInstanceIdentifier='orders', DBInstanceIdentifier='orders-replica')

    @patch('dashboard.rds_api._client')
    def test_mysql_source_refusal_reaches_the_console(self, client_factory):
        client_factory.return_value.create_db_instance_read_replica.side_effect = client_error(
            'InvalidDBInstanceState', 'Operation CreateDBInstanceReadReplica is not supported for engine MYSQL.',
        )
        response = post(self, 'rds-instance-read-replicas', {'replica_identifier': 'shop-replica'}, identifier='shop')
        self.assertEqual(response.status_code, 400)
        self.assertIn('not supported for engine MYSQL', response.json()['error'])

    @patch('dashboard.rds_api._client')
    def test_promote_sends_retention_and_validates_it(self, client_factory):
        client = client_factory.return_value
        client.promote_read_replica.return_value = {'DBInstance': {'DBInstanceIdentifier': 'orders-replica', 'BackupRetentionPeriod': 7}}
        response = post(self, 'rds-instance-promote', {'backup_retention_period': 7}, identifier='orders-replica')
        self.assertEqual(response.status_code, 200)
        client.promote_read_replica.assert_called_once_with(DBInstanceIdentifier='orders-replica', BackupRetentionPeriod=7)
        self.assertEqual(post(self, 'rds-instance-promote', {'backup_retention_period': 36}, identifier='orders-replica').status_code, 400)
        self.assertEqual(client.promote_read_replica.call_count, 1)

    @patch('dashboard.rds_api._client')
    def test_promote_without_retention_leaves_the_aws_default(self, client_factory):
        client = client_factory.return_value
        client.promote_read_replica.return_value = {'DBInstance': {}}
        self.assertEqual(post(self, 'rds-instance-promote', {}, identifier='r').status_code, 200)
        client.promote_read_replica.assert_called_once_with(DBInstanceIdentifier='r')


class AuroraClusterApiTests(SimpleTestCase):
    @patch('dashboard.rds_api._client')
    def test_add_instance_uses_the_cluster_engine(self, client_factory):
        client = client_factory.return_value
        client.describe_db_clusters.return_value = {'DBClusters': [{'DBClusterIdentifier': 'orders', 'Engine': 'aurora-postgresql'}]}
        client.create_db_instance.return_value = {'DBInstance': {'DBInstanceIdentifier': 'orders-2'}}
        response = post(self, 'rds-cluster-instances', {'instance_identifier': 'orders-2'}, identifier='orders')
        self.assertEqual(response.status_code, 200)
        client.create_db_instance.assert_called_once_with(
            DBInstanceIdentifier='orders-2', DBClusterIdentifier='orders', Engine='aurora-postgresql', DBInstanceClass='db.r6g.large',
        )

    @patch('dashboard.rds_api._client')
    def test_failover_reports_the_new_writer(self, client_factory):
        client = client_factory.return_value
        client.failover_db_cluster.return_value = {'DBCluster': {'DBClusterMembers': [
            {'DBInstanceIdentifier': 'orders-1', 'IsClusterWriter': False},
            {'DBInstanceIdentifier': 'orders-2', 'IsClusterWriter': True},
        ]}}
        response = post(self, 'rds-cluster-failover', {'target_instance_identifier': 'orders-2'}, identifier='orders')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['writer'], 'orders-2')
        client.failover_db_cluster.assert_called_once_with(DBClusterIdentifier='orders', TargetDBInstanceIdentifier='orders-2')

    @patch('dashboard.rds_api._client')
    def test_failover_without_reader_returns_floci_error(self, client_factory):
        client_factory.return_value.failover_db_cluster.side_effect = client_error('InvalidDBClusterStateFault', 'DB cluster orders has no reader instance to fail over to.')
        response = post(self, 'rds-cluster-failover', {}, identifier='orders')
        self.assertEqual(response.status_code, 400)
        self.assertIn('no reader instance', response.json()['error'])
        client_factory.return_value.failover_db_cluster.assert_called_once_with(DBClusterIdentifier='orders')

    @patch('dashboard.rds_api._client')
    def test_cluster_snapshot_create_restore_and_delete(self, client_factory):
        client = client_factory.return_value
        client.create_db_cluster_snapshot.return_value = {'DBClusterSnapshot': {'Status': 'available'}}
        client.describe_db_cluster_snapshots.return_value = {'DBClusterSnapshots': [{'Engine': 'aurora-postgresql'}]}
        client.restore_db_cluster_from_snapshot.return_value = {'DBCluster': {'Status': 'available'}}
        client.delete_db_cluster_snapshot.return_value = {'DBClusterSnapshot': {'Status': 'deleted'}}

        created = post(self, 'rds-cluster-snapshots', {'cluster_identifier': 'orders', 'snapshot_identifier': 'orders-snap'})
        self.assertEqual(created.status_code, 200)
        client.create_db_cluster_snapshot.assert_called_once_with(DBClusterIdentifier='orders', DBClusterSnapshotIdentifier='orders-snap')

        restored = post(self, 'rds-cluster-snapshot-restore', {'cluster_identifier': 'orders-copy'}, identifier='orders-snap')
        self.assertEqual(restored.status_code, 200)
        client.restore_db_cluster_from_snapshot.assert_called_once_with(SnapshotIdentifier='orders-snap', DBClusterIdentifier='orders-copy', Engine='aurora-postgresql')

        deleted = self.client.delete(reverse('dashboard:rds-cluster-snapshot-detail', kwargs={'identifier': 'orders-snap'}))
        self.assertEqual(deleted.status_code, 200)
        client.delete_db_cluster_snapshot.assert_called_once_with(DBClusterSnapshotIdentifier='orders-snap')

    @patch('dashboard.rds_api._client')
    def test_aurora_mysql_snapshot_refusal_is_returned(self, client_factory):
        client_factory.return_value.create_db_cluster_snapshot.side_effect = client_error(
            'InvalidDBClusterStateFault', 'Operation CreateDBClusterSnapshot is not supported for engine MYSQL.',
        )
        response = post(self, 'rds-cluster-snapshots', {'cluster_identifier': 'shop', 'snapshot_identifier': 'shop-snap'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('not supported for engine MYSQL', response.json()['error'])


class ResilienceInventoryTests(SimpleTestCase):
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_reports_replication_restore_window_and_cluster_snapshots(self, factory_mock, paginate_mock):
        from .aws.rds import rds_inventory

        factory_mock.return_value.client.return_value.list_tags_for_resource.return_value = {'TagList': []}
        primary = {'DBInstanceIdentifier': 'orders', 'ReadReplicaDBInstanceIdentifiers': ['orders-replica'], 'LatestRestorableTime': '2026-10-23T11:00:00Z'}
        replica = {'DBInstanceIdentifier': 'orders-replica', 'ReadReplicaSourceDBInstanceIdentifier': 'orders', 'StatusInfos': [{'StatusType': 'read replication', 'Status': 'replicating'}]}
        snapshot = {'DBClusterSnapshotIdentifier': 'aurora-snap', 'DBClusterIdentifier': 'aurora', 'Status': 'available', 'SnapshotType': 'manual', 'Engine': 'aurora-postgresql'}
        paginate_mock.side_effect = lambda _client, operation, _key, **kwargs: {
            'describe_db_instances': [primary, replica], 'describe_db_cluster_snapshots': [snapshot],
        }.get(operation, [])
        result = rds_inventory()
        by_name = {item['name']: item for item in result['instances']}
        self.assertEqual(by_name['orders']['read_replicas'], ['orders-replica'])
        self.assertEqual(by_name['orders']['latest_restorable_time'], '2026-10-23T11:00:00Z')
        self.assertEqual(by_name['orders-replica']['read_replica_source'], 'orders')
        self.assertEqual(by_name['orders-replica']['status_infos'][0]['Status'], 'replicating')
        self.assertEqual(result['summary']['read_replicas'], 1)
        self.assertEqual(result['summary']['cluster_snapshots'], 1)
        self.assertEqual(result['cluster_snapshots'][0]['cluster'], 'aurora')
        self.assertIn('FailoverDBCluster', result['supported'])
        self.assertTrue(any('keeps no transaction log' in note for note in result['notes']))


class FakeRds:
    """Just enough RDS and RDS Data API state to run the resilience lab end to end."""

    def __init__(self):
        self.instances: dict[str, dict] = {}
        self.clusters: dict[str, dict] = {}
        self.snapshots: dict[str, dict] = {}
        self.rows: list[tuple[int, str]] = []
        self.calls: list[str] = []

    # RDS
    def create_db_instance(self, **kw):
        self.calls.append('create_db_instance')
        name = kw['DBInstanceIdentifier']
        if name in self.instances:
            raise client_error('DBInstanceAlreadyExists')
        instance = {'DBInstanceIdentifier': name, 'DBInstanceStatus': 'available', 'Engine': kw['Engine'], 'Endpoint': {'Address': 'floci', 'Port': 7001 + len(self.instances)}}
        if 'DBClusterIdentifier' in kw:
            cluster = self.clusters[kw['DBClusterIdentifier']]
            cluster['DBClusterMembers'].append({'DBInstanceIdentifier': name, 'IsClusterWriter': not cluster['DBClusterMembers']})
            instance['DBClusterIdentifier'] = kw['DBClusterIdentifier']
        else:
            instance.update(MultiAZ=kw.get('MultiAZ', False), StorageEncrypted=kw.get('StorageEncrypted', False), BackupRetentionPeriod=kw.get('BackupRetentionPeriod', 1),
                            ReadReplicaDBInstanceIdentifiers=[], LatestRestorableTime='2026-10-23T11:00:00Z')
        self.instances[name] = instance
        return {'DBInstance': instance}

    def describe_db_instances(self, DBInstanceIdentifier):
        if DBInstanceIdentifier not in self.instances:
            raise client_error('DBInstanceNotFound')
        return {'DBInstances': [self.instances[DBInstanceIdentifier]]}

    def create_db_instance_read_replica(self, DBInstanceIdentifier, SourceDBInstanceIdentifier):
        source = self.instances[SourceDBInstanceIdentifier]
        replica = dict(source, DBInstanceIdentifier=DBInstanceIdentifier, BackupRetentionPeriod=0, ReadReplicaSourceDBInstanceIdentifier=SourceDBInstanceIdentifier,
                       ReadReplicaDBInstanceIdentifiers=[], StatusInfos=[{'StatusType': 'read replication', 'Status': 'replicating'}], LatestRestorableTime=None)
        source['ReadReplicaDBInstanceIdentifiers'].append(DBInstanceIdentifier)
        self.instances[DBInstanceIdentifier] = replica
        return {'DBInstance': replica}

    def restore_db_instance_to_point_in_time(self, SourceDBInstanceIdentifier, TargetDBInstanceIdentifier, UseLatestRestorableTime):
        source = self.instances[SourceDBInstanceIdentifier]
        restored = dict(source, DBInstanceIdentifier=TargetDBInstanceIdentifier, ReadReplicaDBInstanceIdentifiers=[], MultiAZ=False)
        self.instances[TargetDBInstanceIdentifier] = restored
        return {'DBInstance': restored}

    def promote_read_replica(self, DBInstanceIdentifier, BackupRetentionPeriod):
        replica = self.instances[DBInstanceIdentifier]
        source = self.instances[replica.pop('ReadReplicaSourceDBInstanceIdentifier')]
        source['ReadReplicaDBInstanceIdentifiers'].remove(DBInstanceIdentifier)
        replica.update(BackupRetentionPeriod=BackupRetentionPeriod, StatusInfos=None)
        return {'DBInstance': replica}

    def create_db_cluster(self, **kw):
        name = kw['DBClusterIdentifier']
        if name in self.clusters:
            raise client_error('DBClusterAlreadyExistsFault')
        self.clusters[name] = {'DBClusterIdentifier': name, 'DBClusterArn': f'arn:aws:rds:us-east-1:000000000000:cluster:{name}', 'Status': 'available', 'Endpoint': 'floci', 'ReaderEndpoint': 'floci', 'Port': 7010,
                               'MasterUserSecret': {'SecretArn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!cluster-abc'} if kw.get('ManageMasterUserPassword') else None, 'DBClusterMembers': []}
        return {'DBCluster': self.clusters[name]}

    def describe_db_clusters(self, DBClusterIdentifier):
        if DBClusterIdentifier not in self.clusters:
            raise client_error('DBClusterNotFoundFault')
        return {'DBClusters': [self.clusters[DBClusterIdentifier]]}

    def failover_db_cluster(self, DBClusterIdentifier, TargetDBInstanceIdentifier):
        self.calls.append('failover_db_cluster')
        cluster = self.clusters[DBClusterIdentifier]
        for member in cluster['DBClusterMembers']:
            member['IsClusterWriter'] = member['DBInstanceIdentifier'] == TargetDBInstanceIdentifier
        return {'DBCluster': cluster}

    def create_db_cluster_snapshot(self, DBClusterSnapshotIdentifier, DBClusterIdentifier):
        self.snapshots[DBClusterSnapshotIdentifier] = {'DBClusterSnapshotIdentifier': DBClusterSnapshotIdentifier, 'DBClusterIdentifier': DBClusterIdentifier, 'Status': 'available', 'SnapshotType': 'manual', 'Engine': 'aurora-postgresql'}
        return {'DBClusterSnapshot': self.snapshots[DBClusterSnapshotIdentifier]}

    def describe_db_cluster_snapshots(self, DBClusterSnapshotIdentifier):
        if DBClusterSnapshotIdentifier not in self.snapshots:
            raise client_error('DBClusterSnapshotNotFoundFault')
        return {'DBClusterSnapshots': [self.snapshots[DBClusterSnapshotIdentifier]]}

    def delete_db_cluster_snapshot(self, DBClusterSnapshotIdentifier):
        if self.snapshots.pop(DBClusterSnapshotIdentifier, None) is None:
            raise client_error('DBClusterSnapshotNotFoundFault')

    def delete_db_instance(self, DBInstanceIdentifier, SkipFinalSnapshot):
        instance = self.instances.pop(DBInstanceIdentifier, None)
        if instance is None:
            raise client_error('DBInstanceNotFound')
        cluster = self.clusters.get(instance.get('DBClusterIdentifier') or '')
        if cluster:
            cluster['DBClusterMembers'] = [m for m in cluster['DBClusterMembers'] if m['DBInstanceIdentifier'] != DBInstanceIdentifier]

    def delete_db_cluster(self, DBClusterIdentifier, SkipFinalSnapshot):
        cluster = self.clusters.get(DBClusterIdentifier)
        if cluster is None:
            raise client_error('DBClusterNotFoundFault')
        if cluster['DBClusterMembers']:
            raise client_error('InvalidDBClusterStateFault', 'cluster has members')
        del self.clusters[DBClusterIdentifier]

    # RDS Data API
    def execute_statement(self, resourceArn, secretArn, database, sql):
        if sql.startswith('CREATE TABLE'):
            if not self.rows:
                self.rows.append((1001, 'keyboard'))
            return {'numberOfRecordsUpdated': 1}
        return {'records': [[{'longValue': order_id}, {'stringValue': item}] for order_id, item in self.rows]}


class ResilienceLabTests(SimpleTestCase):
    LAB = 'aurora-resilience'

    def setUp(self):
        cache.clear()
        self.fake = FakeRds()
        patcher = patch('dashboard.labs.rds_labs.client', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_all(self):
        lab = next(item for item in labs_for_service('rds') if item['key'] == self.LAB)
        return {step['key']: run_lab_step('rds', self.LAB, step['key']) for step in lab['steps']}

    def test_lab_is_registered_with_ten_steps_and_exam_tags(self):
        from .labs.exams import LAB_EXAM_TAGS

        lab = next(item for item in labs_for_service('rds') if item['key'] == self.LAB)
        self.assertEqual(len(lab['steps']), 10)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[('rds', self.LAB)]}
        self.assertIn(('SAA-C03', '2.2'), tasks)
        self.assertIn(('SAA-C03', '3.3'), tasks)

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: value['verification']['message'] for key, value in results.items() if not value['verified']}
        self.assertEqual(failed, {})
        self.assertIn('lab-orders-primary', results['create-read-replica']['verification']['message'])
        self.assertIn('Order 1001', results['failover-aurora-cluster']['verification']['message'])
        self.assertIn(self.fake.clusters['lab-aurora-orders']['MasterUserSecret']['SecretArn'], results['write-order-row']['command'])
        status = lab_status('rds', self.LAB)
        self.assertTrue(status['complete'], status['steps'])
        self.assertEqual(status['passed_steps'], 10)

    def test_rerunning_steps_is_idempotent(self):
        self.run_all()
        again = self.run_all()
        self.assertTrue(all(result['verified'] for result in again.values()), {k: v['verification'] for k, v in again.items()})
        self.assertEqual(self.fake.calls.count('failover_db_cluster'), 1)

    def test_promote_fails_when_the_replica_is_still_linked(self):
        run_lab_step('rds', self.LAB, 'create-multi-az-primary')
        run_lab_step('rds', self.LAB, 'create-read-replica')
        with patch.object(self.fake, 'promote_read_replica', return_value={}):
            result = run_lab_step('rds', self.LAB, 'promote-read-replica')
        self.assertFalse(result['verified'])
        self.assertIn('still a replica', result['verification']['message'])

    def test_primary_without_multi_az_does_not_verify(self):
        original = self.fake.create_db_instance
        self.fake.create_db_instance = lambda **kw: original(**{**kw, 'MultiAZ': False})
        result = run_lab_step('rds', self.LAB, 'create-multi-az-primary')
        self.assertFalse(result['verified'])
        self.assertIn('Multi-AZ is off', result['verification']['message'])

    def test_failover_needs_the_order_to_survive(self):
        for step in ('create-aurora-cluster', 'add-aurora-writer', 'add-aurora-reader'):
            self.assertTrue(run_lab_step('rds', self.LAB, step)['verified'], step)
        result = run_lab_step('rds', self.LAB, 'failover-aurora-cluster')
        self.assertFalse(result['verified'])
        self.assertIn('data check failed', result['verification']['message'])

    def test_write_order_needs_a_writer(self):
        run_lab_step('rds', self.LAB, 'create-aurora-cluster')
        result = run_lab_step('rds', self.LAB, 'write-order-row')
        self.assertFalse(result['verified'])
        self.assertIn('no writer instance', result['verification']['message'])

    def test_reset_deletes_members_before_the_cluster_and_clears_progress(self):
        self.run_all()
        result = reset_lab('rds', self.LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.fake.instances, {})
        self.assertEqual(self.fake.clusters, {})
        self.assertEqual(self.fake.snapshots, {})
        self.assertEqual(lab_status('rds', self.LAB)['passed_steps'], 0)

    def test_status_before_any_step_is_not_started(self):
        self.assertEqual(lab_status('rds', self.LAB)['status'], 'not_started')
