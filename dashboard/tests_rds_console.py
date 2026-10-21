import json
from unittest.mock import patch
from django.test import SimpleTestCase
from .aws import rds_inventory


class RDSWorkspaceTests(SimpleTestCase):
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_preserves_networking_security_and_zero_values(self, factory_mock, paginate_mock):
        client = factory_mock.return_value.client.return_value
        client.list_tags_for_resource.return_value = {'TagList': []}
        db = {'DBInstanceIdentifier': 'orders', 'DBInstanceStatus': 'creating', 'AllocatedStorage': 0, 'PubliclyAccessible': False, 'MultiAZ': False, 'IAMDatabaseAuthenticationEnabled': False, 'StorageEncrypted': False, 'DeletionProtection': False, 'BackupRetentionPeriod': 0, 'AvailabilityZone': 'eu-west-1a', 'Endpoint': {'Address': 'reported.local', 'Port': 17001}, 'DBSubnetGroup': {'DBSubnetGroupName': 'private', 'VpcId': 'vpc-real'}, 'VpcSecurityGroups': [{'VpcSecurityGroupId': 'sg-real', 'Status': 'active'}], 'PendingModifiedValues': {'DBInstanceClass': 'db.t3.small'}}
        cluster = {'DBClusterIdentifier': 'cluster', 'DBClusterMembers': [{'DBInstanceIdentifier': 'orders', 'IsClusterWriter': False, 'PromotionTier': 0}], 'ReaderEndpoint': 'read.local', 'StorageEncrypted': False, 'BackupRetentionPeriod': 0, 'AvailabilityZones': ['eu-west-1a']}
        paginate_mock.side_effect = lambda _client, operation, _key, **kwargs: {'describe_db_instances': [db], 'describe_db_clusters': [cluster], 'describe_db_parameter_groups': []}[operation]
        result = rds_inventory()
        instance = result['instances'][0]
        self.assertEqual(instance['allocated_storage'], 0)
        self.assertEqual(instance['connect_port'], 17001)
        self.assertFalse(instance['publicly_accessible'])
        self.assertFalse(instance['iam_authentication'])
        self.assertEqual(instance['security_groups'][0]['VpcSecurityGroupId'], 'sg-real')
        self.assertEqual(instance['backup_retention_period'], 0)
        self.assertEqual(instance['pending_modifications']['DBInstanceClass'], 'db.t3.small')
        self.assertFalse(result['clusters'][0]['members'][0]['IsClusterWriter'])
        self.assertEqual(result['clusters'][0]['members'][0]['PromotionTier'], 0)
        self.assertEqual(result['issues'], [])

    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_reports_read_failures_instead_of_silent_empty_success(self, factory_mock, paginate_mock):
        from botocore.exceptions import ClientError
        paginate_mock.side_effect = ClientError({'Error': {'Code': 'AccessDenied', 'Message': 'Describe denied'}}, 'DescribeDBInstances')
        result = rds_inventory()
        self.assertEqual(len(result['issues']), 3)
        self.assertIn('Describe denied', result['issues'][0])
        self.assertEqual(result['instances'], [])

    @patch('dashboard.rds_api._client')
    def test_modify_maintains_explicit_deferred_timing(self, client_mock):
        client = client_mock.return_value
        client.modify_db_instance.return_value = {'DBInstance': {'DBInstanceIdentifier': 'orders'}}
        response = self.client.put('/api/rds/instances/orders/', data=json.dumps({'db_instance_class': 'db.t3.small', 'apply_immediately': False}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.modify_db_instance.assert_called_once_with(DBInstanceIdentifier='orders', DBInstanceClass='db.t3.small', ApplyImmediately=False)

    @patch('dashboard.rds_api._client')
    def test_final_snapshot_choice_and_identifier_reach_sdk_for_instance_and_cluster(self, client_mock):
        client = client_mock.return_value
        client.delete_db_instance.return_value = {}
        client.delete_db_cluster.return_value = {}
        payload = json.dumps({'skip_final_snapshot': False, 'final_snapshot_identifier': 'orders-final'})
        self.assertEqual(self.client.delete('/api/rds/instances/orders/', data=payload, content_type='application/json').status_code, 200)
        self.assertEqual(self.client.delete('/api/rds/clusters/orders/', data=payload, content_type='application/json').status_code, 200)
        client.delete_db_instance.assert_called_once_with(DBInstanceIdentifier='orders', SkipFinalSnapshot=False, FinalDBSnapshotIdentifier='orders-final')
        client.delete_db_cluster.assert_called_once_with(DBClusterIdentifier='orders', SkipFinalSnapshot=False, FinalDBSnapshotIdentifier='orders-final')

    @patch('dashboard.rds_api._client')
    def test_final_snapshot_identifier_is_required_before_deletion(self, client_mock):
        for resource in ('instances', 'clusters'):
            with self.subTest(resource=resource):
                response = self.client.delete(f'/api/rds/{resource}/orders/', data=json.dumps({'skip_final_snapshot': False}), content_type='application/json')
                self.assertEqual(response.status_code, 400)
                self.assertIn('Final snapshot identifier', response.json()['error'])
        client_mock.assert_not_called()

    @patch('dashboard.rds_api._client')
    def test_zero_creation_storage_is_rejected_without_substituting_twenty(self, client_mock):
        response = self.client.post('/api/rds/instances/', data=json.dumps({'identifier': 'orders', 'engine': 'postgres', 'username': 'admin', 'password': 'temporary-password', 'allocated_storage': 0}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('greater than zero', response.json()['error'])
        client_mock.assert_not_called()
