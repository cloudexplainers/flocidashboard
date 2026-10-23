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
