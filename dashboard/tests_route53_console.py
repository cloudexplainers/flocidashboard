import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class Route53ApiTests(SimpleTestCase):
    @patch('dashboard.route53_views.create_hosted_zone')
    def test_create_hosted_zone_success(self, create_mock):
        create_mock.return_value = {'hosted_zone': {'Id': '/hostedzone/Z123'}}

        response = self.client.post(
            reverse('dashboard:route53-hosted-zones'),
            data=json.dumps({'name': 'example.com', 'caller_reference': 'ref-1', 'comment': 'local'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['hosted_zone']['Id'], '/hostedzone/Z123')
        create_mock.assert_called_once_with('example.com', 'ref-1', comment='local')

    @patch('dashboard.route53_views.delete_hosted_zone')
    def test_delete_hosted_zone_success(self, delete_mock):
        delete_mock.return_value = {'zone_id': 'Z123'}

        response = self.client.delete(reverse('dashboard:route53-hosted-zone-detail', kwargs={'zone_id': 'Z123'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('Z123')

    @patch('dashboard.route53_views.change_record_set')
    def test_change_record_set_success(self, change_mock):
        change_mock.return_value = {'change_info': {'Status': 'INSYNC'}}

        response = self.client.post(
            reverse('dashboard:route53-record-sets', kwargs={'zone_id': 'Z123'}),
            data=json.dumps({
                'action': 'UPSERT',
                'name': 'www.example.com',
                'type': 'A',
                'ttl': 300,
                'values': ['1.2.3.4'],
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        change_mock.assert_called_once_with(
            'Z123',
            'UPSERT',
            'www.example.com',
            'A',
            ttl=300,
            values=['1.2.3.4'],
            comment='',
            record_set=None,
        )

    @patch('dashboard.route53_views.create_health_check')
    def test_create_health_check_success(self, create_mock):
        create_mock.return_value = {'health_check': {'Id': 'hc-1'}}

        response = self.client.post(
            reverse('dashboard:route53-health-checks'),
            data=json.dumps({
                'caller_reference': 'hc-ref',
                'type': 'HTTPS',
                'domain_name': 'example.com',
                'port': 443,
                'resource_path': '/health',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            'hc-ref',
            'HTTPS',
            domain_name='example.com',
            ip_address='',
            port=443,
            resource_path='/health',
        )

    @patch('dashboard.route53_views.update_health_check')
    def test_update_health_check_success(self, update_mock):
        update_mock.return_value = {'health_check': {'Id': 'hc-1'}}

        response = self.client.put(
            reverse('dashboard:route53-health-check-detail', kwargs={'health_check_id': 'hc-1'}),
            data=json.dumps({'domain_name': 'example.com', 'port': 443}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(
            'hc-1',
            domain_name='example.com',
            ip_address='',
            port=443,
            resource_path='',
        )

    @patch('dashboard.route53_views.change_tags')
    def test_change_tags_success(self, tags_mock):
        tags_mock.return_value = {'resource_type': 'hostedzone', 'resource_id': 'Z123'}

        response = self.client.post(
            reverse('dashboard:route53-tags', kwargs={'resource_type': 'hostedzone', 'resource_id': 'Z123'}),
            data=json.dumps({'add_tags': {'env': 'local'}, 'remove_tag_keys': ['old']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        tags_mock.assert_called_once_with(
            'hostedzone',
            'Z123',
            add_tags={'env': 'local'},
            remove_tag_keys=['old'],
        )
