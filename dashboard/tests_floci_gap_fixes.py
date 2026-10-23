"""Tests for the Floci gap fixes: labels where Floci only stores configuration, and actions
Floci doesn't implement."""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import NoReverseMatch, reverse

from .aws import FlociClientFactory
from .services import get_service


def _action_names(service_key):
    return {action.name for action in get_service(service_key).actions}


class ClientConfigTests(SimpleTestCase):

    def test_host_prefix_injection_is_off_for_every_client(self):
        # botocore would send Cloud Map DiscoverInstances to data-localhost, which doesn't resolve.
        factory = FlociClientFactory()
        self.assertFalse(factory.config.inject_host_prefix)
        for name in ('servicediscovery', 's3', 'logs', 'stepfunctions'):
            client = factory.client(name)
            self.assertFalse(client.meta.config.inject_host_prefix, name)

    def test_discover_instances_request_goes_to_the_floci_endpoint(self):
        client = FlociClientFactory().client('servicediscovery')
        seen = {}

        def capture(request, **kwargs):
            seen['url'] = request.url
            raise RuntimeError('stop')

        client.meta.events.register('before-send.servicediscovery.DiscoverInstances', capture)
        with self.assertRaises(RuntimeError):
            client.discover_instances(NamespaceName='ns', ServiceName='web')
        self.assertNotIn('data-', seen['url'])


class CloudMapTests(SimpleTestCase):

    def test_update_instance_health_is_not_offered(self):
        self.assertNotIn('update_instance_custom_health_status', _action_names('cloudmap'))
        with self.assertRaises(NoReverseMatch):
            reverse('dashboard:cloudmap-instance-health', kwargs={'service_id': 's', 'instance_id': 'i'})

    @patch('dashboard.aws.cloudmap.core.FlociClientFactory')
    def test_inventory_marks_custom_health_status_not_emulated(self, factory_cls):
        from .aws.cloudmap import cloudmap_inventory

        client = MagicMock()
        client.meta.service_model.operation_names = ['ListNamespaces', 'UpdateInstanceCustomHealthStatus']
        factory_cls.return_value.client.return_value = client
        with patch('dashboard.aws.cloudmap.core._operation_items', return_value=[]):
            data = cloudmap_inventory()
        self.assertNotIn('UpdateInstanceCustomHealthStatus', data['supported_from_sdk'])
        self.assertTrue(any('UpdateInstanceCustomHealthStatus' in item for item in data['not_supported']))


class VersionClaimTests(SimpleTestCase):

    def test_inventory_notes_carry_no_release_changelog_lines(self):
        # Compose pulls floci/floci:latest, so "Floci 1.5.x adds ..." lines describe some other
        # version than the one running. Notes say what Floci does, without version numbers.
        import re
        from pathlib import Path

        pattern = re.compile(r"""^\s*['"]Floci \d+\.\d+\.\d+ """)
        root = Path(__file__).resolve().parent
        offenders = [
            f'{path.relative_to(root)}:{number}'
            for path in root.rglob('*.py') if 'tests' not in path.name
            for number, line in enumerate(path.read_text().splitlines(), 1) if pattern.match(line)
        ]
        self.assertEqual(offenders, [])


class S3AnonymousAccessTests(SimpleTestCase):

    @patch('dashboard.s3_api.boto3.client')
    def test_probe_sends_an_unsigned_list_and_reports_success(self, client_factory):
        from botocore import UNSIGNED

        from .s3_api import probe_anonymous_list

        result = probe_anonymous_list('private-bucket')

        config = client_factory.call_args.kwargs['config']
        self.assertIs(config.signature_version, UNSIGNED)
        client_factory.return_value.list_objects_v2.assert_called_once_with(Bucket='private-bucket', MaxKeys=0)
        self.assertEqual(result, {'bucket': 'private-bucket', 'anonymous_list_allowed': True, 'status_code': 200, 'error_code': None})

    @patch('dashboard.s3_api.boto3.client')
    def test_probe_reports_a_refused_request(self, client_factory):
        from botocore.exceptions import ClientError

        from .s3_api import probe_anonymous_list

        client_factory.return_value.list_objects_v2.side_effect = ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'Access Denied'}, 'ResponseMetadata': {'HTTPStatusCode': 403}},
            'ListObjectsV2',
        )
        result = probe_anonymous_list('private-bucket')
        self.assertFalse(result['anonymous_list_allowed'])
        self.assertEqual((result['status_code'], result['error_code']), (403, 'AccessDenied'))

    @patch('dashboard.s3_views.probe_anonymous_list')
    def test_endpoint_returns_the_probe_result(self, probe):
        probe.return_value = {'bucket': 'b-1', 'anonymous_list_allowed': True, 'status_code': 200, 'error_code': None}
        response = self.client.get(reverse('dashboard:s3-bucket-anonymous-access', kwargs={'bucket_name': 'b-1'}))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['anonymous_list_allowed'])
        probe.assert_called_once_with('b-1')

    def test_endpoint_rejects_invalid_bucket_names(self):
        response = self.client.get(reverse('dashboard:s3-bucket-anonymous-access', kwargs={'bucket_name': 'Bad_Name'}))
        self.assertEqual(response.status_code, 400)


class SettingsAuthFlagTests(SimpleTestCase):

    def test_unset_flags_are_reported_as_unknown(self):
        from .settings_views import _env_flag

        with patch.dict('os.environ', {}, clear=False) as env:
            env.pop('FLOCI_SERVICES_S3_ENFORCE_AUTH', None)
            self.assertIsNone(_env_flag('FLOCI_SERVICES_S3_ENFORCE_AUTH'))
        with patch.dict('os.environ', {'FLOCI_SERVICES_S3_ENFORCE_AUTH': 'false'}):
            self.assertFalse(_env_flag('FLOCI_SERVICES_S3_ENFORCE_AUTH'))
        with patch.dict('os.environ', {'FLOCI_SERVICES_S3_ENFORCE_AUTH': 'TRUE'}):
            self.assertTrue(_env_flag('FLOCI_SERVICES_S3_ENFORCE_AUTH'))


class RDSSnapshotTests(SimpleTestCase):

    @patch('dashboard.rds_api._client')
    def test_take_snapshot_sends_identifiers_and_tags(self, client_factory):
        import json

        client = client_factory.return_value
        client.create_db_snapshot.return_value = {'DBSnapshot': {'DBSnapshotIdentifier': 'orders-snap', 'Status': 'available'}}
        response = self.client.post(
            reverse('dashboard:rds-snapshots'),
            data=json.dumps({'instance_identifier': 'orders', 'snapshot_identifier': 'orders-snap', 'tags': {'team': 'data'}}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        client.create_db_snapshot.assert_called_once_with(
            DBInstanceIdentifier='orders', DBSnapshotIdentifier='orders-snap', Tags=[{'Key': 'team', 'Value': 'data'}],
        )
        self.assertEqual(response.json()['snapshot']['Status'], 'available')
        client_factory.assert_called_with(long_running=True)

    @patch('dashboard.rds_api._client')
    def test_snapshot_refused_by_floci_returns_the_aws_error(self, client_factory):
        import json

        from botocore.exceptions import ClientError

        client_factory.return_value.create_db_snapshot.side_effect = ClientError(
            {'Error': {'Code': 'InvalidDBInstanceState', 'Message': 'Operation CreateDBSnapshot is not supported for engine MYSQL.'}, 'ResponseMetadata': {'HTTPStatusCode': 400}},
            'CreateDBSnapshot',
        )
        response = self.client.post(
            reverse('dashboard:rds-snapshots'),
            data=json.dumps({'instance_identifier': 'shop', 'snapshot_identifier': 'shop-snap'}),
            content_type='application/json',
        )
        self.assertGreaterEqual(response.status_code, 400)
        self.assertIn('not supported for engine MYSQL', response.json()['error'])

    @patch('dashboard.rds_api._client')
    def test_restore_creates_a_new_instance_from_the_snapshot(self, client_factory):
        import json

        client = client_factory.return_value
        client.restore_db_instance_from_db_snapshot.return_value = {'DBInstance': {'DBInstanceIdentifier': 'orders-restored'}}
        response = self.client.post(
            reverse('dashboard:rds-snapshot-restore', kwargs={'identifier': 'orders-snap'}),
            data=json.dumps({'instance_identifier': 'orders-restored', 'db_instance_class': 'db.t3.small', 'publicly_accessible': False}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        client.restore_db_instance_from_db_snapshot.assert_called_once_with(
            DBSnapshotIdentifier='orders-snap', DBInstanceIdentifier='orders-restored',
            DBInstanceClass='db.t3.small', PubliclyAccessible=False,
        )

    @patch('dashboard.rds_api._client')
    def test_restore_requires_a_new_identifier(self, client_factory):
        import json

        response = self.client.post(
            reverse('dashboard:rds-snapshot-restore', kwargs={'identifier': 'orders-snap'}),
            data=json.dumps({}), content_type='application/json',
        )
        self.assertEqual(response.status_code, 400)
        client_factory.return_value.restore_db_instance_from_db_snapshot.assert_not_called()

    @patch('dashboard.rds_api._client')
    def test_delete_snapshot(self, client_factory):
        client = client_factory.return_value
        client.delete_db_snapshot.return_value = {'DBSnapshot': {'DBSnapshotIdentifier': 'orders-snap', 'Status': 'deleted'}}
        response = self.client.delete(reverse('dashboard:rds-snapshot-detail', kwargs={'identifier': 'orders-snap'}))
        self.assertEqual(response.status_code, 200)
        client.delete_db_snapshot.assert_called_once_with(DBSnapshotIdentifier='orders-snap')

    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_lists_snapshots(self, factory_mock, paginate_mock):
        from .aws.rds import rds_inventory

        snapshot = {'DBSnapshotIdentifier': 'orders-snap', 'DBInstanceIdentifier': 'orders', 'Status': 'available', 'SnapshotType': 'manual', 'Engine': 'postgres', 'AllocatedStorage': 20}
        paginate_mock.side_effect = lambda _client, operation, _key, **kwargs: {'describe_db_snapshots': [snapshot]}.get(operation, [])
        result = rds_inventory()
        self.assertEqual(result['summary']['snapshots'], 1)
        self.assertEqual(result['snapshots'][0]['name'], 'orders-snap')
        self.assertEqual(result['snapshots'][0]['instance'], 'orders')
        self.assertEqual(result['snapshots'][0]['type'], 'manual')


class StoredOnlyLabelTests(SimpleTestCase):
    """Actions whose configuration Floci stores without running it say so in the form."""

    def _action(self, service_key, name):
        return next(action for action in get_service(service_key).actions if action.name == name)

    def test_auto_scaling_policy_and_hook_say_they_do_not_run(self):
        policy = self._action('autoscaling', 'put_scaling_policy')
        self.assertIn('never runs it', policy.description)
        self.assertIn('does not run it', policy.success_message)
        hook = self._action('autoscaling', 'put_lifecycle_hook')
        self.assertIn('never pause', hook.description)

    def test_other_stored_only_actions_are_labelled(self):
        self.assertIn('never run on their schedule', self._action('backup', 'create_backup_plan').description)
        self.assertIn('holds no copy', self._action('backup', 'start_backup_job').description)
        self.assertIn('does not evaluate it', self._action('config', 'start_config_rules_evaluation').description)
        self.assertIn('routes no traffic', self._action('ec2', 'create_nat_gateway').description)
        self.assertIn('never used', self._action('iam', 'get_access_key_last_used').description)

    @patch('dashboard.aws.autoscaling.core.FlociClientFactory')
    def test_auto_scaling_inventory_does_not_claim_unsupported_operations(self, factory_cls):
        from .aws.autoscaling import autoscaling_inventory

        with patch('dashboard.aws.autoscaling._safe_value', return_value=[]):
            data = autoscaling_inventory()
        for operation in ('PutNotificationConfiguration', 'DescribeNotificationConfigurations', 'DeleteNotificationConfiguration', 'DescribeTags'):
            self.assertNotIn(operation, data['supported_from_sdk'])
        self.assertTrue(any('Scaling policies are stored but never run' in note for note in data['notes']))

    @patch('dashboard.aws.cloudtrail.core.FlociClientFactory')
    def test_cloudtrail_notes_say_which_events_floci_records(self, factory_cls):
        from .aws.cloudtrail import cloudtrail_inventory

        factory_cls.return_value.client.return_value.meta.service_model.operation_names = []
        with patch('dashboard.aws.cloudtrail.core._operation_items', return_value=[]):
            notes = ' '.join(cloudtrail_inventory()['notes'])
        self.assertIn('LookupEvents always returns an empty list', notes)
        self.assertIn('S3 data events', notes)
        self.assertNotIn('does not record live API activity', notes)

    @patch('dashboard.aws.secretsmanager.core._paginate')
    @patch('dashboard.aws.secretsmanager.core.FlociClientFactory')
    def test_secrets_manager_inventory_returns_the_resource_policy(self, factory_cls, paginate):
        from .aws.secretsmanager import secretsmanager_inventory

        client = factory_cls.return_value.client.return_value
        paginate.side_effect = lambda _client, operation, *args, **kwargs: [{'Name': 'db', 'ARN': 'arn:secret:db'}] if operation == 'list_secrets' else []
        client.describe_secret.return_value = {'Name': 'db', 'ARN': 'arn:secret:db'}
        client.get_resource_policy.return_value = {'Name': 'db', 'ResourcePolicy': '{"Statement": []}'}
        secret = secretsmanager_inventory()['secrets'][0]
        self.assertEqual(secret['resource_policy'], '{"Statement": []}')

        client.get_resource_policy.return_value = {'Name': 'db'}
        self.assertIsNone(secretsmanager_inventory()['secrets'][0]['resource_policy'])
