import json
from unittest.mock import patch
from django.test import SimpleTestCase
from . import cloudtrail_api


class CloudTrailConsoleContractTests(SimpleTestCase):
    @patch('dashboard.cloudtrail_api._client')
    def test_lookup_passes_filter_and_token_to_aws(self, factory):
        factory.return_value.lookup_events.return_value = {'Events': [{'EventId': 'one'}], 'NextToken': 'next'}
        response = self.client.get('/api/cloudtrail/events/', {'attribute': 'Username', 'value': 'learner', 'next_token': 'previous'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'events': [{'EventId': 'one'}], 'next_token': 'next'})
        factory.return_value.lookup_events.assert_called_once_with(MaxResults=50, LookupAttributes=[{'AttributeKey': 'Username', 'AttributeValue': 'learner'}], NextToken='previous')

    @patch('dashboard.cloudtrail_api._client')
    def test_default_lookup_has_no_fabricated_filter(self, factory):
        factory.return_value.lookup_events.return_value = {}
        self.assertEqual(cloudtrail_api.lookup_events(), {'events': [], 'next_token': None})
        factory.return_value.lookup_events.assert_called_once_with(MaxResults=50)

    @patch('dashboard.cloudtrail_api._client')
    def test_invalid_filter_does_not_call_aws(self, factory):
        response = self.client.get('/api/cloudtrail/events/', {'attribute': 'bad', 'value': 'x'})
        self.assertEqual(response.status_code, 400)
        factory.assert_not_called()

    @patch('dashboard.cloudtrail_api._client')
    def test_selector_read_and_write_preserve_false(self, factory):
        client = factory.return_value
        client.get_event_selectors.return_value = {'TrailARN': 'arn:trail', 'EventSelectors': [{'IncludeManagementEvents': False}]}
        client.put_event_selectors.return_value = {'EventSelectors': [{'IncludeManagementEvents': False}]}
        response = self.client.get('/api/cloudtrail/trails/audit/selectors/')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['EventSelectors'][0]['IncludeManagementEvents'])
        response = self.client.put('/api/cloudtrail/trails/audit/selectors/', json.dumps({'EventSelectors': [{'IncludeManagementEvents': False, 'ReadWriteType': 'WriteOnly'}]}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client.get_event_selectors.assert_called_once_with(TrailName='audit')
        client.put_event_selectors.assert_called_once_with(TrailName='audit', EventSelectors=[{'IncludeManagementEvents': False, 'ReadWriteType': 'WriteOnly'}])

    @patch('dashboard.cloudtrail_api._client')
    def test_selector_write_rejects_mixed_and_invalid_shapes(self, factory):
        for value in ({}, [], {'Other': []}, {'EventSelectors': {}, 'AdvancedEventSelectors': []}, {'EventSelectors': [], 'AdvancedEventSelectors': []}):
            with self.subTest(value=value):
                response = self.client.put('/api/cloudtrail/trails/audit/selectors/', json.dumps(value), content_type='application/json')
                self.assertEqual(response.status_code, 400)
        factory.return_value.put_event_selectors.assert_not_called()

    @patch('dashboard.cloudtrail_api._client')
    def test_sdk_read_errors_are_visible(self, factory):
        factory.return_value.get_event_selectors.side_effect = ValueError('Selectors unavailable')
        response = self.client.get('/api/cloudtrail/trails/audit/selectors/')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Selectors unavailable', response.json()['error'])

    @patch('dashboard.aws.FlociClientFactory')
    def test_trail_inventory_failure_is_not_an_empty_success(self, factory):
        from .aws import cloudtrail_inventory
        factory.return_value.client.return_value.describe_trails.side_effect = ValueError('Trail inventory denied')
        with self.assertRaisesRegex(ValueError, 'Trail inventory denied'):
            cloudtrail_inventory()
