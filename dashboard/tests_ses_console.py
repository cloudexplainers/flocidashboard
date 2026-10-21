import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .aws import ses_inventory
from .services import get_service


class SESPageTemplateTests(SimpleTestCase):
    def test_ses_service_page_keeps_readonly_inventory_and_embeds_console(self):
        response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': 'ses'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2>SES inventory</h2>', html=True)
        self.assertContains(response, 'id="ses-summary"')
        self.assertContains(response, 'id="ses-console-root"')
        self.assertNotContains(response, 'id="ses-grid"')
        self.assertContains(response, 'dashboard/ses-console.css')
        self.assertContains(response, 'dashboard/service-console.js')
        self.assertContains(response, 'dashboard/ses-console.js')

    def test_ses_registry_marks_service_interactive(self):
        service = get_service('ses')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertEqual(service.console_js, 'dashboard/ses-console.js')
        self.assertTrue(any(action.name == 'verify_email_identity' for action in service.actions))
        self.assertTrue(any(action.name == 'send_email' for action in service.actions))
        self.assertTrue(any(action.name == 'create_template' for action in service.actions))
        self.assertTrue(any(action.name == 'clear_mailbox' for action in service.actions))
        self.assertTrue(any(action.name == 'create_contact' for action in service.actions))
        self.assertTrue(any(action.name == 'update_contact' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_contact' for action in service.actions))


class SESActionsApiTests(SimpleTestCase):
    @patch('dashboard.ses_views.create_contact')
    def test_create_contact_success(self, create_mock):
        create_mock.return_value = {'email_address': 'person@example.com'}
        response = self.client.post(
            reverse('dashboard:ses-contacts'),
            data=json.dumps({'contact_list_name': 'customers', 'email_address': 'person@example.com'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            'customers', 'person@example.com', topic_preferences=None, unsubscribe_all=None,
        )

    @patch('dashboard.ses_views.update_contact')
    def test_update_contact_success(self, update_mock):
        update_mock.return_value = {'email_address': 'person@example.com'}
        response = self.client.patch(
            reverse('dashboard:ses-contact-detail', kwargs={
                'contact_list_name': 'customers', 'email_address': 'person@example.com',
            }),
            data=json.dumps({'unsubscribe_all': True, 'topic_preferences': []}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(
            'customers', 'person@example.com', topic_preferences=[], unsubscribe_all=True,
        )

    @patch('dashboard.ses_views.delete_contact')
    def test_delete_contact_success(self, delete_mock):
        delete_mock.return_value = {'deleted': True}
        response = self.client.delete(reverse('dashboard:ses-contact-detail', kwargs={
            'contact_list_name': 'customers', 'email_address': 'person@example.com',
        }))
        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('customers', 'person@example.com')

    @patch('dashboard.ses_views.verify_email_identity')
    def test_verify_email_identity_success(self, verify_mock):
        verify_mock.return_value = {'identity': 'sender@example.com'}

        response = self.client.post(
            reverse('dashboard:ses-identities-email'),
            data=json.dumps({'email_address': 'sender@example.com'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        verify_mock.assert_called_once_with('sender@example.com')

    def test_verify_email_identity_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:ses-identities-email'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'ses')
        self.assertEqual(response.json()['operation'], 'verify_email_identity')

    @patch('dashboard.ses_views.verify_domain_identity')
    def test_verify_domain_identity_success(self, verify_mock):
        verify_mock.return_value = {'identity': 'example.com'}

        response = self.client.post(
            reverse('dashboard:ses-identities-domain'),
            data=json.dumps({'domain': 'example.com'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        verify_mock.assert_called_once_with('example.com')

    @patch('dashboard.ses_views.delete_identity')
    def test_delete_identity_success(self, delete_mock):
        delete_mock.return_value = {'identity': 'sender@example.com'}

        response = self.client.delete(reverse('dashboard:ses-identity-detail', kwargs={'identity': 'sender@example.com'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('sender@example.com')

    @patch('dashboard.ses_views.send_email')
    def test_send_email_success(self, send_mock):
        send_mock.return_value = {'message_id': 'msg-1'}

        response = self.client.post(
            reverse('dashboard:ses-email-send'),
            data=json.dumps({
                'source': 'sender@example.com',
                'to_addresses': ['recipient@example.com'],
                'subject': 'Hello',
                'text': 'Body',
                'html': '',
                'cc_addresses': [],
                'bcc_addresses': [],
                'configuration_set_name': 'local-events',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        send_mock.assert_called_once_with(
            source='sender@example.com',
            to_addresses=['recipient@example.com'],
            subject='Hello',
            text='Body',
            html='',
            cc_addresses=[],
            bcc_addresses=[],
            configuration_set_name='local-events',
        )

    @patch('dashboard.ses_views.send_raw_email')
    def test_send_raw_email_success(self, send_mock):
        send_mock.return_value = {'message_id': 'msg-1'}

        response = self.client.post(
            reverse('dashboard:ses-raw-email-send'),
            data=json.dumps({
                'source': 'sender@example.com',
                'destinations': ['recipient@example.com'],
                'raw_message': 'Subject: Raw\r\n\r\nBody',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        send_mock.assert_called_once_with(
            source='sender@example.com',
            destinations=['recipient@example.com'],
            raw_message='Subject: Raw\r\n\r\nBody',
        )

    @patch('dashboard.ses_views.send_templated_email')
    def test_send_templated_email_success(self, send_mock):
        send_mock.return_value = {'message_id': 'msg-1'}

        response = self.client.post(
            reverse('dashboard:ses-templated-email-send'),
            data=json.dumps({
                'source': 'sender@example.com',
                'to_addresses': ['recipient@example.com'],
                'template_name': 'order-receipt',
                'template_data': '{"name":"Alice"}',
                'configuration_set_name': '',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        send_mock.assert_called_once_with(
            source='sender@example.com',
            to_addresses=['recipient@example.com'],
            template_name='order-receipt',
            template_data='{"name":"Alice"}',
            configuration_set_name='',
        )

    @patch('dashboard.ses_views.create_template')
    def test_create_template_success(self, create_mock):
        create_mock.return_value = {'template_name': 'order-receipt'}

        response = self.client.post(
            reverse('dashboard:ses-templates'),
            data=json.dumps({'template_name': 'order-receipt', 'subject': 'Hi', 'text': 'Text', 'html': '<p>Hi</p>'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('order-receipt', subject='Hi', text='Text', html='<p>Hi</p>')

    @patch('dashboard.ses_views.update_template')
    def test_update_template_success(self, update_mock):
        update_mock.return_value = {'template_name': 'order-receipt'}

        response = self.client.patch(
            reverse('dashboard:ses-template-detail', kwargs={'template_name': 'order-receipt'}),
            data=json.dumps({'subject': 'Hi', 'text': 'Text', 'html': '<p>Hi</p>'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with('order-receipt', subject='Hi', text='Text', html='<p>Hi</p>')

    @patch('dashboard.ses_views.delete_template')
    def test_delete_template_success(self, delete_mock):
        delete_mock.return_value = {'template_name': 'order-receipt'}

        response = self.client.delete(reverse('dashboard:ses-template-detail', kwargs={'template_name': 'order-receipt'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('order-receipt')

    @patch('dashboard.ses_views.render_template')
    def test_render_template_success(self, render_mock):
        render_mock.return_value = {'template_name': 'order-receipt', 'rendered_template': 'Hi Alice'}

        response = self.client.post(
            reverse('dashboard:ses-template-render', kwargs={'template_name': 'order-receipt'}),
            data=json.dumps({'template_data': '{"name":"Alice"}'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        render_mock.assert_called_once_with('order-receipt', '{"name":"Alice"}')

    @patch('dashboard.ses_views.update_sending_enabled')
    def test_update_sending_enabled_success(self, update_mock):
        update_mock.return_value = {'enabled': False}

        response = self.client.put(
            reverse('dashboard:ses-account-sending'),
            data=json.dumps({'enabled': False}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(False)

    @patch('dashboard.ses_views.create_configuration_set')
    def test_create_configuration_set_success(self, create_mock):
        create_mock.return_value = {'configuration_set_name': 'local-events'}

        response = self.client.post(
            reverse('dashboard:ses-configuration-sets'),
            data=json.dumps({'name': 'local-events', 'tags': [{'Key': 'env', 'Value': 'dev'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('local-events', tags=[{'Key': 'env', 'Value': 'dev'}])

    @patch('dashboard.ses_views.delete_configuration_set')
    def test_delete_configuration_set_success(self, delete_mock):
        delete_mock.return_value = {'configuration_set_name': 'local-events'}

        response = self.client.delete(reverse('dashboard:ses-configuration-set-detail', kwargs={'name': 'local-events'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('local-events')

    @patch('dashboard.ses_views.update_configuration_set_sending_enabled')
    def test_update_configuration_set_sending_success(self, update_mock):
        update_mock.return_value = {'configuration_set_name': 'local-events', 'enabled': False}

        response = self.client.put(
            reverse('dashboard:ses-configuration-set-sending', kwargs={'name': 'local-events'}),
            data=json.dumps({'enabled': False}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with('local-events', False)

    @patch('dashboard.ses_views.put_event_destination')
    def test_put_event_destination_success(self, put_mock):
        event_destination = {'Enabled': True, 'MatchingEventTypes': ['SEND'], 'SnsDestination': {'TopicArn': 'arn'}}
        put_mock.return_value = {'configuration_set_name': 'local-events', 'event_destination_name': 'sns-events'}

        response = self.client.post(
            reverse('dashboard:ses-event-destinations', kwargs={'name': 'local-events'}),
            data=json.dumps({'event_destination_name': 'sns-events', 'event_destination': event_destination}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        put_mock.assert_called_once_with('local-events', 'sns-events', event_destination)

    @patch('dashboard.ses_views.delete_event_destination')
    def test_delete_event_destination_success(self, delete_mock):
        delete_mock.return_value = {'configuration_set_name': 'local-events', 'event_destination_name': 'sns-events'}

        response = self.client.delete(reverse(
            'dashboard:ses-event-destination-detail',
            kwargs={'name': 'local-events', 'event_destination_name': 'sns-events'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('local-events', 'sns-events')

    @patch('dashboard.ses_views.clear_mailbox')
    def test_clear_mailbox_success(self, clear_mock):
        clear_mock.return_value = {'status': 200}

        response = self.client.delete(reverse('dashboard:ses-mailbox'))

        self.assertEqual(response.status_code, 200)
        clear_mock.assert_called_once_with()


class SESInventoryTests(SimpleTestCase):
    @patch('dashboard.aws._ses_mailbox')
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_surfaces_v2_configuration_set_options(self, factory_mock, paginate_mock, mailbox_mock):
        ses = MagicMock()
        ses.list_verified_email_addresses.return_value = {'VerifiedEmailAddresses': []}
        sesv2 = MagicMock()
        sesv2.meta.service_model.operation_names = ['GetAccount']
        sesv2.get_account.return_value = {'ProductionAccessEnabled': False, 'SendingEnabled': False}
        sesv2.list_email_identities.return_value = {'EmailIdentities': []}
        sesv2.list_email_templates.return_value = {'TemplatesMetadata': []}
        sesv2.list_configuration_sets.return_value = {'ConfigurationSets': [{'ConfigurationSetName': 'local-events'}]}
        sesv2.get_configuration_set.return_value = {
            'SendingOptions': {'SendingEnabled': False},
            'DeliveryOptions': {'TlsPolicy': 'REQUIRE'},
            'ReputationOptions': {'ReputationMetricsEnabled': True},
            'SuppressionOptions': {'SuppressedReasons': ['BOUNCE']},
            'TrackingOptions': {'CustomRedirectDomain': 'track.local'},
        }
        sesv2.get_configuration_set_event_destinations.return_value = {'EventDestinations': [{'Name': 'events'}]}

        factory = MagicMock(endpoint_url='http://localhost:4566')
        factory.client.side_effect = lambda service: sesv2 if service == 'sesv2' else ses
        factory_mock.return_value = factory
        mailbox_mock.return_value = {'messages': []}

        def paginate(client, operation, result_key, **kwargs):
            return []

        paginate_mock.side_effect = paginate

        result = ses_inventory()
        config_set = result['v2_configuration_sets'][0]
        self.assertFalse(result['account']['ProductionAccessEnabled'])
        self.assertFalse(result['account']['SendingEnabled'])
        sesv2.get_account.assert_called_once_with()

        self.assertEqual(result['summary']['v2_configuration_sets'], 1)
        self.assertEqual(result['summary']['v2_event_destinations'], 1)
        self.assertFalse(config_set['sending_enabled'])
        self.assertEqual(config_set['sending_options'], {'SendingEnabled': False})
        self.assertEqual(config_set['delivery_options'], {'TlsPolicy': 'REQUIRE'})
        self.assertEqual(config_set['reputation_options'], {'ReputationMetricsEnabled': True})
        self.assertEqual(config_set['suppression_options'], {'SuppressedReasons': ['BOUNCE']})
        self.assertEqual(config_set['tracking_options'], {'CustomRedirectDomain': 'track.local'})
