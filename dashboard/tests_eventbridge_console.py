import json

from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .eventbridge_api import test_event_pattern


class EventBridgeFirstClassConsoleTests(SimpleTestCase):


    def test_pattern_matching_engine(self):
        # Exact matching
        res = test_event_pattern({'source': ['aws.ec2']}, {'source': 'aws.ec2'})
        self.assertTrue(res['result'])

        # Numeric range matching
        res = test_event_pattern(
            {'detail': {'price': [{'numeric': ['>=', 50, '<=', 150]}]}},
            {'detail': {'price': 100}},
        )
        self.assertTrue(res['result'])

        # Prefix matching
        res = test_event_pattern(
            {'detail': {'file': [{'prefix': 'uploads/'}]}},
            {'detail': {'file': 'uploads/doc.pdf'}},
        )
        self.assertTrue(res['result'])

        # Suffix matching
        res = test_event_pattern(
            {'detail': {'file': [{'suffix': '.png'}]}},
            {'detail': {'file': 'uploads/image.png'}},
        )
        self.assertTrue(res['result'])

        # Exists matching
        res = test_event_pattern(
            {'detail': {'deleted': [{'exists': False}]}},
            {'detail': {'name': 'test'}},
        )
        self.assertTrue(res['result'])

        # Anything-but matching
        res = test_event_pattern(
            {'detail': {'tier': [{'anything-but': 'FRAUD'}]}},
            {'detail': {'tier': 'SILVER'}},
        )
        self.assertTrue(res['result'])

        # Mismatch
        res = test_event_pattern({'source': ['aws.ec2']}, {'source': 'aws.s3'})
        self.assertFalse(res['result'])
        self.assertEqual(len(res['mismatches']), 1)

    def test_pattern_test_endpoint(self):
        response = self.client.post(
            reverse('dashboard:eventbridge-pattern-test'),
            data=json.dumps({
                'event_pattern': {'source': ['aws.ec2']},
                'event': {'source': 'aws.ec2'},
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['result'])

    def test_sample_events_endpoint(self):
        response = self.client.get(reverse('dashboard:eventbridge-sample-events'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('ec2_instance_state', data)
        self.assertIn('s3_object_created', data)
        self.assertIn('custom_order_event', data)


class EmulatorPatternTests(SimpleTestCase):
    def test_emulator_pattern_endpoint_uses_events_api(self):
        from unittest.mock import patch
        with patch('dashboard.eventbridge_api._events_client') as client:
            client.return_value.test_event_pattern.return_value = {'Result': False}
            response = self.client.post(reverse('dashboard:eventbridge-pattern-test'), data=json.dumps({
                'engine': 'emulator', 'event_pattern': {'source': ['orders']}, 'event': {'source': 'other'},
            }), content_type='application/json')
            self.assertEqual(response.status_code, 200)
            self.assertFalse(response.json()['result'])
            self.assertEqual(response.json()['engine'], 'emulator')
            arguments = client.return_value.test_event_pattern.call_args.kwargs
            self.assertEqual(json.loads(arguments['EventPattern']), {'source': ['orders']})

    def test_emulator_pattern_endpoint_rejects_arrays(self):
        response = self.client.post(reverse('dashboard:eventbridge-pattern-test'), data=json.dumps({
            'engine': 'emulator', 'event_pattern': [], 'event': {},
        }), content_type='application/json')
        self.assertEqual(response.status_code, 400)


class EventBridgeArchiveReplayTests(SimpleTestCase):
    def post(self, url, body):
        return self.client.post(url, data=json.dumps(body), content_type='application/json')

    def test_list_archives_and_replays(self):
        with patch('dashboard.eventbridge_api._events_client') as factory:
            client = factory.return_value
            client.list_archives.return_value = {'Archives': [{'ArchiveName': 'orders'}]}
            client.describe_archive.return_value = {
                'ArchiveName': 'orders', 'ArchiveArn': 'arn:aws:events:us-east-1:000000000000:archive/orders',
                'EventSourceArn': 'arn:aws:events:us-east-1:000000000000:event-bus/default', 'State': 'ENABLED',
                'RetentionDays': 0, 'EventCount': 3, 'EventPattern': '{"source":["shop"]}',
            }
            client.list_replays.return_value = {'Replays': [{'ReplayName': 'r1'}]}
            client.describe_replay.return_value = {'ReplayName': 'r1', 'State': 'COMPLETED', 'Destination': {'Arn': 'bus-arn'}}
            response = self.client.get(reverse('dashboard:eventbridge-archives'))
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['archives'][0]['event_count'], 3)
        self.assertEqual(data['replays'][0]['destination_arn'], 'bus-arn')

    def test_create_archive_resolves_bus_name_to_arn(self):
        with patch('dashboard.eventbridge_api._events_client') as factory:
            client = factory.return_value
            client.describe_event_bus.return_value = {'Arn': 'arn:bus/orders'}
            client.create_archive.return_value = {'ArchiveArn': 'arn:archive/a', 'State': 'ENABLED'}
            response = self.post(reverse('dashboard:eventbridge-archives-create'), {
                'name': 'a', 'event_bus_name': 'orders', 'event_pattern': {'source': ['shop']}, 'retention_days': '7',
            })
        self.assertEqual(response.status_code, 200)
        client.create_archive.assert_called_once_with(
            ArchiveName='a', EventSourceArn='arn:bus/orders', EventPattern='{"source": ["shop"]}', RetentionDays=7)

    def test_create_archive_rejects_negative_retention(self):
        with patch('dashboard.eventbridge_api._events_client') as factory:
            factory.return_value.describe_event_bus.return_value = {'Arn': 'arn:bus/default'}
            response = self.post(reverse('dashboard:eventbridge-archives-create'), {'name': 'a', 'retention_days': -1})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Retention', response.json()['error'])

    def test_delete_archive(self):
        with patch('dashboard.eventbridge_api._events_client') as factory:
            response = self.post(reverse('dashboard:eventbridge-archives-delete'), {'name': 'a'})
        self.assertEqual(response.status_code, 200)
        factory.return_value.delete_archive.assert_called_once_with(ArchiveName='a')

    def test_start_replay_targets_archive_source_bus_and_rules(self):
        with patch('dashboard.eventbridge_api._events_client') as factory:
            client = factory.return_value
            client.describe_archive.return_value = {'ArchiveArn': 'arn:archive/a', 'EventSourceArn': 'arn:bus/default'}
            client.start_replay.return_value = {'ReplayArn': 'arn:replay/r', 'State': 'STARTING'}
            response = self.post(reverse('dashboard:eventbridge-replays-start'), {
                'name': 'r', 'archive_name': 'a', 'start_time': '2026-10-23T10:00:00Z',
                'end_time': '2026-10-23T11:00:00Z', 'rule_arns': ['arn:rule/x'],
            })
        self.assertEqual(response.status_code, 200)
        kwargs = client.start_replay.call_args.kwargs
        self.assertEqual(kwargs['EventSourceArn'], 'arn:archive/a')
        self.assertEqual(kwargs['Destination'], {'Arn': 'arn:bus/default', 'FilterArns': ['arn:rule/x']})
        self.assertEqual(kwargs['EventStartTime'].isoformat(), '2026-10-23T10:00:00+00:00')

    def test_start_replay_rejects_inverted_window(self):
        with patch('dashboard.eventbridge_api._events_client'):
            response = self.post(reverse('dashboard:eventbridge-replays-start'), {
                'name': 'r', 'archive_name': 'a', 'start_time': '2026-10-23T11:00:00Z', 'end_time': '2026-10-23T10:00:00Z',
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn('End time', response.json()['error'])

    def test_cancel_replay(self):
        with patch('dashboard.eventbridge_api._events_client') as factory:
            factory.return_value.cancel_replay.return_value = {'State': 'CANCELLING'}
            response = self.post(reverse('dashboard:eventbridge-replays-cancel'), {'name': 'r'})
        self.assertEqual(response.json()['state'], 'CANCELLING')
