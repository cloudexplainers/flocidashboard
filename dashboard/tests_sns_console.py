import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class SNSMessagesApiTests(SimpleTestCase):
    def test_publish_message_rejects_missing_topic(self):
        response = self.client.post(
            reverse('dashboard:sns-messages-publish'),
            data=json.dumps({'message': 'hello'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'sns')
        self.assertEqual(response.json()['operation'], 'publish_message')

    @patch('dashboard.sns_views.publish_message')
    def test_publish_message_success(self, publish_mock):
        publish_mock.return_value = {'message_id': 'abc123'}
        topic_arn = 'arn:aws:sns:us-east-1:000000000000:orders'

        response = self.client.post(
            reverse('dashboard:sns-messages-publish'),
            data=json.dumps({
                'topic_arn': topic_arn,
                'message': 'hello',
                'subject': 'Test',
                'message_attributes': {'eventType': 'created'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['message_id'], 'abc123')
        publish_mock.assert_called_once_with(
            topic_arn,
            'hello',
            subject='Test',
            message_attributes={'eventType': 'created'},
            message_structure=None,
            message_group_id=None,
            message_deduplication_id=None,
        )

    @patch('dashboard.sns_views.create_topic')
    def test_create_topic_success(self, create_mock):
        create_mock.return_value = {'name': 'events', 'topic_arn': 'arn:aws:sns:us-east-1:000000000000:events'}

        response = self.client.post(
            reverse('dashboard:sns-topics-create'),
            data=json.dumps({'name': 'events', 'fifo': False}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'events')
        create_mock.assert_called_once_with('events', fifo=False, display_name=None, kms_master_key_id=None)

    @patch('dashboard.sns_views.delete_topic')
    def test_delete_topic_success(self, delete_mock):
        delete_mock.return_value = {'topic_arn': 'arn:aws:sns:us-east-1:000000000000:events', 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:sns-topic-detail', kwargs={'topic_arn': 'arn:aws:sns:us-east-1:000000000000:events'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('arn:aws:sns:us-east-1:000000000000:events')

    @patch('dashboard.sns_views.subscribe')
    def test_subscribe_success(self, sub_mock):
        sub_mock.return_value = {
            'topic_arn': 'arn:aws:sns:us-east-1:000000000000:events',
            'subscription_arn': 'arn:aws:sns:us-east-1:000000000000:events:sub-101',
        }

        response = self.client.post(
            reverse('dashboard:sns-subscriptions-create'),
            data=json.dumps({
                'topic_arn': 'arn:aws:sns:us-east-1:000000000000:events',
                'protocol': 'sqs',
                'endpoint': 'arn:aws:sqs:us-east-1:000000000000:orders',
                'filter_policy': {'event_type': ['order_created']},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['subscription_arn'], 'arn:aws:sns:us-east-1:000000000000:events:sub-101')
        sub_mock.assert_called_once_with(
            'arn:aws:sns:us-east-1:000000000000:events',
            'sqs',
            'arn:aws:sqs:us-east-1:000000000000:orders',
            filter_policy={'event_type': ['order_created']},
            raw_message_delivery=False,
        )

    @patch('dashboard.sns_views.unsubscribe')
    def test_unsubscribe_success(self, unsub_mock):
        unsub_mock.return_value = {'subscription_arn': 'arn:aws:sns:...:sub-101', 'unsubscribed': True}

        response = self.client.delete(
            reverse('dashboard:sns-subscription-detail', kwargs={'subscription_arn': 'arn:aws:sns:...:sub-101'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['unsubscribed'])
        unsub_mock.assert_called_once_with('arn:aws:sns:...:sub-101')


class SNSDeliveryContractTests(SimpleTestCase):
    @patch('dashboard.sns_api._sns_client')
    def test_fifo_creation_can_require_explicit_deduplication(self, client_mock):
        client_mock.return_value.create_topic.return_value = {'TopicArn': 'arn:aws:sns:us-east-1:000000000000:orders.fifo'}
        response = self.client.post('/api/sns/topics/', data=json.dumps({'name': 'orders.fifo', 'fifo': True, 'content_based_deduplication': False}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        client_mock.return_value.create_topic.assert_called_once_with(Name='orders.fifo', Attributes={'FifoTopic': 'true', 'ContentBasedDeduplication': 'false'})

    @patch('dashboard.sns_api._sns_client')
    def test_subscribe_serializes_filter_scope_and_raw_delivery(self, client_mock):
        client_mock.return_value.subscribe.return_value = {'SubscriptionArn': 'arn:subscription'}
        response = self.client.post('/api/sns/subscriptions/', data=json.dumps({'topic_arn': 'arn:aws:sns:us-east-1:000000000000:orders', 'protocol': 'sqs', 'endpoint': 'arn:aws:sqs:us-east-1:000000000000:orders', 'filter_policy': {'kind': ['order']}, 'filter_policy_scope': 'MessageBody', 'raw_message_delivery': True}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        attrs = client_mock.return_value.subscribe.call_args.kwargs['Attributes']
        self.assertEqual(json.loads(attrs['FilterPolicy']), {'kind': ['order']})
        self.assertEqual(attrs['FilterPolicyScope'], 'MessageBody')
        self.assertEqual(attrs['RawMessageDelivery'], 'true')

    @patch('dashboard.sns_api._sns_client')
    def test_attribute_url_resolves_put_and_serializes_boolean_false(self, client_mock):
        arn = 'arn:aws:sns:us-east-1:000000000000:orders:subscription'
        response = self.client.put(reverse('dashboard:sns-subscription-attributes', kwargs={'subscription_arn': arn}), data=json.dumps({'attribute_name': 'RawMessageDelivery', 'attribute_value': False}), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        client_mock.return_value.set_subscription_attributes.assert_called_once_with(SubscriptionArn=arn, AttributeName='RawMessageDelivery', AttributeValue='false')

    @patch('dashboard.sns_api._sns_client')
    def test_invalid_filter_policy_never_calls_sdk(self, client_mock):
        response = self.client.post('/api/sns/subscriptions/', data=json.dumps({'topic_arn': 'arn:aws:sns:us-east-1:000000000000:orders', 'protocol': 'sqs', 'endpoint': 'arn:queue', 'filter_policy': ['invalid']}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        client_mock.assert_not_called()

    @patch('dashboard.sns_api._sns_client')
    def test_fifo_publish_preserves_typed_attributes_and_deduplication(self, client_mock):
        client_mock.return_value.publish.return_value = {'MessageId': 'message-1', 'SequenceNumber': '0'}
        response = self.client.post('/api/sns/messages/publish/', data=json.dumps({'topic_arn': 'arn:aws:sns:us-east-1:000000000000:orders.fifo', 'message': '0', 'message_group_id': 'customer-7', 'message_deduplication_id': 'request-1', 'message_attributes': {'amount': 0}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        args = client_mock.return_value.publish.call_args.kwargs
        self.assertEqual(args['MessageGroupId'], 'customer-7')
        self.assertEqual(args['MessageDeduplicationId'], 'request-1')
        self.assertEqual(args['MessageAttributes']['amount'], {'DataType': 'Number', 'StringValue': '0'})
        self.assertEqual(response.json()['sequence_number'], '0')
