import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class ACMPageTemplateTests(SimpleTestCase):

    def test_acm_registry_marks_service_interactive(self):
        service = get_service('acm')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'request_certificate' for action in service.actions))
        self.assertTrue(any(action.name == 'export_certificate' for action in service.actions))


class ACMActionsApiTests(SimpleTestCase):
    certificate_arn = 'arn:aws:acm:us-east-1:123456789012:certificate/cert-1'

    @patch('dashboard.acm_views.request_certificate')
    def test_request_certificate_success(self, request_mock):
        request_mock.return_value = {'certificate_arn': self.certificate_arn}

        response = self.client.post(
            reverse('dashboard:acm-certificates'),
            data=json.dumps({
                'domain_name': 'example.com',
                'subject_alternative_names': ['www.example.com'],
                'validation_method': 'DNS',
                'key_algorithm': 'RSA_2048',
                'certificate_authority_arn': '',
                'tags': [{'Key': 'env', 'Value': 'local'}],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['certificate_arn'], self.certificate_arn)
        request_mock.assert_called_once_with(
            domain_name='example.com',
            subject_alternative_names=['www.example.com'],
            validation_method='DNS',
            key_algorithm='RSA_2048',
            certificate_authority_arn='',
            tags=[{'Key': 'env', 'Value': 'local'}],
        )

    def test_request_certificate_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:acm-certificates'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'acm')
        self.assertEqual(response.json()['operation'], 'request_certificate')

    @patch('dashboard.acm_views.get_certificate')
    def test_get_certificate_success(self, get_mock):
        get_mock.return_value = {'Certificate': 'pem'}

        response = self.client.get(
            reverse('dashboard:acm-certificate-detail', kwargs={'certificate_arn': self.certificate_arn}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['Certificate'], 'pem')
        get_mock.assert_called_once_with(self.certificate_arn)

    @patch('dashboard.acm_views.delete_certificate')
    def test_delete_certificate_success(self, delete_mock):
        delete_mock.return_value = {'certificate_arn': self.certificate_arn, 'deleted': True}

        response = self.client.delete(
            reverse('dashboard:acm-certificate-detail', kwargs={'certificate_arn': self.certificate_arn}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with(self.certificate_arn)

    @patch('dashboard.acm_views.renew_certificate')
    def test_renew_certificate_success(self, renew_mock):
        renew_mock.return_value = {'certificate_arn': self.certificate_arn, 'renewed': True}

        response = self.client.post(
            reverse('dashboard:acm-certificate-renew', kwargs={'certificate_arn': self.certificate_arn}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['renewed'])
        renew_mock.assert_called_once_with(self.certificate_arn)

    @patch('dashboard.acm_views.export_certificate')
    def test_export_certificate_success(self, export_mock):
        export_mock.return_value = {'Certificate': 'pem', 'PrivateKey': 'key'}

        response = self.client.post(
            reverse('dashboard:acm-certificate-export', kwargs={'certificate_arn': self.certificate_arn}),
            data=json.dumps({'passphrase': 'bXlwYXNz'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['PrivateKey'], 'key')
        export_mock.assert_called_once_with(self.certificate_arn, 'bXlwYXNz')

    @patch('dashboard.acm_views.add_tags')
    def test_add_tags_success(self, add_tags_mock):
        add_tags_mock.return_value = {'certificate_arn': self.certificate_arn, 'tags': [{'Key': 'env', 'Value': 'local'}]}

        response = self.client.post(
            reverse('dashboard:acm-certificate-tags', kwargs={'certificate_arn': self.certificate_arn}),
            data=json.dumps({'tags': [{'Key': 'env', 'Value': 'local'}]}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tags'][0]['Key'], 'env')
        add_tags_mock.assert_called_once_with(self.certificate_arn, [{'Key': 'env', 'Value': 'local'}])

    @patch('dashboard.acm_views.remove_tags')
    def test_remove_tags_success(self, remove_tags_mock):
        remove_tags_mock.return_value = {'certificate_arn': self.certificate_arn, 'tag_keys': ['env']}

        response = self.client.delete(
            reverse('dashboard:acm-certificate-tags', kwargs={'certificate_arn': self.certificate_arn}),
            data=json.dumps({'tag_keys': ['env']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tag_keys'], ['env'])
        remove_tags_mock.assert_called_once_with(self.certificate_arn, ['env'])

    @patch('dashboard.acm_views.put_account_configuration')
    def test_put_account_configuration_success(self, put_mock):
        put_mock.return_value = {'expiry_events': {'DaysBeforeExpiry': 30}}

        response = self.client.put(
            reverse('dashboard:acm-account-configuration'),
            data=json.dumps({'days_before_expiry': 30}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['expiry_events']['DaysBeforeExpiry'], 30)
        put_mock.assert_called_once_with(30)
