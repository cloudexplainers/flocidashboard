import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse


class InspectorPageTests(SimpleTestCase):
    def test_inspector_page_renders_shell(self):
        response = self.client.get(reverse('dashboard:inspector'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="root"')
        self.assertContains(response, 'app.js')


class InspectorApiTests(SimpleTestCase):
    @patch('dashboard.inspector_api.FlociClientFactory')
    def test_sqs_queues_endpoint_returns_queue_summaries(self, factory_mock):
        sqs = MagicMock()
        sqs.list_queues.return_value = {'QueueUrls': ['http://localhost:4566/000000000000/jobs']}
        sqs.get_queue_attributes.return_value = {
            'Attributes': {
                'QueueArn': 'arn:aws:sqs:us-east-1:000000000000:jobs',
                'ApproximateNumberOfMessages': '2',
                'ApproximateNumberOfMessagesNotVisible': '1',
            },
        }
        factory_mock.return_value.client.return_value = sqs

        response = self.client.get(reverse('dashboard:inspector-sqs-queues'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['queues'][0]['name'], 'jobs')
        self.assertEqual(payload['queues'][0]['available'], 2)
        self.assertEqual(payload['queues'][0]['in_flight'], 1)

    def test_sqs_messages_endpoint_requires_queue_url(self):
        response = self.client.get(reverse('dashboard:inspector-sqs-messages'))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['operation'], 'receive_sqs_messages')
        self.assertIn('Queue URL is required', response.json()['error'])

    @patch('dashboard.inspector_api.FlociClientFactory')
    def test_sqs_messages_endpoint_peeks_with_zero_visibility(self, factory_mock):
        sqs = MagicMock()
        sqs.receive_message.return_value = {'Messages': [{'MessageId': 'm-1', 'Body': 'hello'}]}
        factory_mock.return_value.client.return_value = sqs
        queue_url = 'http://localhost:4566/000000000000/jobs'

        response = self.client.get(reverse('dashboard:inspector-sqs-messages'), {'queue_url': queue_url})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'][0]['Body'], 'hello')
        sqs.receive_message.assert_called_once()
        self.assertEqual(sqs.receive_message.call_args.kwargs['VisibilityTimeout'], 0)

    @patch('dashboard.inspector_api.urlopen')
    def test_ses_messages_endpoint_reads_local_mailbox(self, urlopen_mock):
        response_mock = MagicMock()
        response_mock.status = 200
        response_mock.read.return_value = json.dumps({'messages': [{'subject': 'Hello'}]}).encode()
        urlopen_mock.return_value.__enter__.return_value = response_mock

        response = self.client.get(reverse('dashboard:inspector-ses-messages'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['messages'][0]['subject'], 'Hello')

    @patch('dashboard.inspector_views.clear_ses_mailbox')
    def test_ses_clear_endpoint_requires_delete(self, clear_mock):
        get_response = self.client.get(reverse('dashboard:inspector-ses-messages-clear'))
        self.assertEqual(get_response.status_code, 405)

        clear_mock.return_value = {'status': 200, 'body': ''}
        delete_response = self.client.delete(reverse('dashboard:inspector-ses-messages-clear'))
        self.assertEqual(delete_response.status_code, 200)
        clear_mock.assert_called_once_with()

    @patch('dashboard.inspector_api.FlociClientFactory')
    def test_lambda_log_events_endpoint_returns_recent_events(self, factory_mock):
        logs = MagicMock()
        logs.describe_log_streams.return_value = {'logStreams': [{'logStreamName': '2026/07/07/[$LATEST]abc'}]}
        logs.get_log_events.return_value = {'events': [{'timestamp': 1, 'message': 'hello'}]}
        factory_mock.return_value.client.return_value = logs

        response = self.client.get(
            reverse('dashboard:inspector-lambda-log-events'),
            {'log_group_name': '/aws/lambda/demo'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['events'][0]['message'], 'hello')
        self.assertEqual(response.json()['events'][0]['logStreamName'], '2026/07/07/[$LATEST]abc')
