import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class CloudTrailInventoryTests(SimpleTestCase):


    def test_cloudtrail_registry_marks_service_interactive(self):
        service = get_service('cloudtrail')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertEqual(len(service.actions), 4)
        self.assertEqual(service.category, 'Observability')

    @patch('dashboard.views.cloudtrail_inventory')
    def test_cloudtrail_api_returns_inventory(self, inventory):
        inventory.return_value = {
            'summary': {'trails': 1, 'logging': 1, 'multi_region_trails': 0},
            'trails': [{'name': 'local-audit', 'arn': 'arn:aws:cloudtrail:us-east-1:000000000000:trail/local-audit'}],
            'supported_from_sdk': ['CreateTrail', 'StartLogging', 'StopLogging', 'DeleteTrail'],
        }

        response = self.client.get(reverse('dashboard:cloudtrail'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['trails'], 1)

    @patch('dashboard.cloudtrail_views.create_trail')
    def test_create_trail(self, create_mock):
        create_mock.return_value = {'name': 'local-audit', 'arn': 'arn:trail'}
        response = self.client.post(reverse('dashboard:cloudtrail-trails'), data=json.dumps({'name': 'local-audit', 's3_bucket_name': 'audit-logs', 'include_global_service_events': True, 'is_multi_region_trail': False, 'is_organization_trail': False}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('local-audit', 'audit-logs', include_global_service_events=True, is_multi_region_trail=False, is_organization_trail=False)

    @patch('dashboard.cloudtrail_views.update_trail')
    def test_update_trail(self, update_mock):
        update_mock.return_value = {'name': 'local-audit'}
        response = self.client.patch(reverse('dashboard:cloudtrail-trail-detail', kwargs={'trail_name': 'local-audit'}), data=json.dumps({'s3_bucket_name': 'new-logs', 'include_global_service_events': False, 'is_multi_region_trail': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with('local-audit', s3_bucket_name='new-logs', include_global_service_events=False, is_multi_region_trail=True)

    @patch('dashboard.cloudtrail_views.set_trail_logging')
    def test_set_logging(self, logging_mock):
        logging_mock.return_value = {'name': 'local-audit', 'is_logging': True}
        response = self.client.post(reverse('dashboard:cloudtrail-trail-logging', kwargs={'trail_name': 'local-audit'}), data=json.dumps({'enabled': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        logging_mock.assert_called_once_with('local-audit', True)

    @patch('dashboard.cloudtrail_views.delete_trail')
    def test_delete_trail(self, delete_mock):
        delete_mock.return_value = {'name': 'local-audit', 'deleted': True}
        response = self.client.delete(reverse('dashboard:cloudtrail-trail-detail', kwargs={'trail_name': 'local-audit'}))
        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('local-audit')

    def test_logging_rejects_non_boolean(self):
        response = self.client.post(reverse('dashboard:cloudtrail-trail-logging', kwargs={'trail_name': 'local-audit'}), data=json.dumps({'enabled': 'yes'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
