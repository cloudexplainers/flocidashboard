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
