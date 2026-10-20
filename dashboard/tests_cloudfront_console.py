import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse


class CloudFrontPageTemplateTests(SimpleTestCase):
    def test_cloudfront_service_page_embeds_console(self):
        response = self.client.get(reverse('dashboard:service-page', kwargs={'service_key': 'cloudfront'}))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '<h2>CloudFront inventory</h2>', html=True)
        self.assertContains(response, 'id="cloudfront-summary"')
        self.assertContains(response, 'id="cloudfront-console-root"')
        self.assertNotContains(response, 'id="cloudfront-grid"')
        self.assertContains(response, 'dashboard/cloudfront-console.css')
        self.assertContains(response, 'dashboard/service-console.js')
        self.assertContains(response, 'dashboard/cloudfront-console.js')


class CloudFrontApiTests(SimpleTestCase):
    @patch('dashboard.cloudfront_views.create_distribution')
    def test_create_distribution_success(self, create_mock):
        create_mock.return_value = {'distribution': {'Id': 'E123'}}

        response = self.client.post(
            reverse('dashboard:cloudfront-distributions'),
            data=json.dumps({
                'caller_reference': 'ref-1',
                'origin_id': 'origin-1',
                'origin_domain_name': 'bucket.s3.amazonaws.com',
                'aliases': ['cdn.local.test'],
                'enabled': True,
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['distribution']['Id'], 'E123')
        create_mock.assert_called_once_with(
            'ref-1',
            'origin-1',
            'bucket.s3.amazonaws.com',
            comment='',
            enabled=True,
            aliases=['cdn.local.test'],
            viewer_protocol_policy='redirect-to-https',
            cache_policy_id='',
        )

    @patch('dashboard.cloudfront_views.update_distribution')
    def test_update_distribution_success(self, update_mock):
        update_mock.return_value = {'distribution': {'Id': 'E123'}}

        response = self.client.put(
            reverse('dashboard:cloudfront-distribution-detail', kwargs={'distribution_id': 'E123'}),
            data=json.dumps({'comment': 'disabled locally', 'enabled': False, 'if_match': 'etag-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with(
            'E123',
            None,
            if_match='etag-1',
            comment='disabled locally',
            enabled=False,
        )

    @patch('dashboard.cloudfront_views.create_invalidation')
    def test_create_invalidation_success(self, invalidation_mock):
        invalidation_mock.return_value = {'invalidation': {'Id': 'INV1'}}

        response = self.client.post(
            reverse('dashboard:cloudfront-invalidations', kwargs={'distribution_id': 'E123'}),
            data=json.dumps({'paths': ['/*'], 'caller_reference': 'inv-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        invalidation_mock.assert_called_once_with('E123', ['/*'], caller_reference='inv-1')

    @patch('dashboard.cloudfront_views.create_cache_policy')
    def test_create_cache_policy_success(self, policy_mock):
        policy_mock.return_value = {'cache_policy': {'Id': 'policy-1'}}

        response = self.client.post(
            reverse('dashboard:cloudfront-cache-policies'),
            data=json.dumps({'name': 'local-cache', 'default_ttl': 60}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        policy_mock.assert_called_once_with(
            'local-cache',
            default_ttl=60,
            max_ttl=31536000,
            min_ttl=0,
            comment='',
        )

    @patch('dashboard.cloudfront_views.create_origin_access_identity')
    def test_create_origin_access_identity_success(self, oai_mock):
        oai_mock.return_value = {'origin_access_identity': {'Id': 'OAI123'}}

        response = self.client.post(
            reverse('dashboard:cloudfront-origin-access-identities'),
            data=json.dumps({'caller_reference': 'oai-ref-1', 'comment': 'Local OAI'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        oai_mock.assert_called_once_with('oai-ref-1', comment='Local OAI')

    @patch('dashboard.cloudfront_views.create_function')
    def test_create_function_success(self, function_mock):
        function_mock.return_value = {'function': {'Name': 'rewrite'}}

        response = self.client.post(
            reverse('dashboard:cloudfront-functions'),
            data=json.dumps({'name': 'rewrite', 'code': 'function handler(event) { return event.request; }'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        function_mock.assert_called_once_with(
            'rewrite',
            'function handler(event) { return event.request; }',
            comment='',
            runtime='cloudfront-js-1.0',
        )

    @patch('dashboard.cloudfront_views.publish_function')
    def test_publish_function_success(self, publish_mock):
        publish_mock.return_value = {'function': {'Name': 'rewrite'}}

        response = self.client.post(
            reverse('dashboard:cloudfront-function-detail', kwargs={'function_name': 'rewrite'}),
            data=json.dumps({'if_match': 'etag-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        publish_mock.assert_called_once_with('rewrite', if_match='etag-1')

    @patch('dashboard.cloudfront_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn:aws:cloudfront::000000000000:distribution/E123'}

        response = self.client.post(
            reverse('dashboard:cloudfront-tags'),
            data=json.dumps({
                'resource_arn': 'arn:aws:cloudfront::000000000000:distribution/E123',
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        tag_mock.assert_called_once_with(
            'arn:aws:cloudfront::000000000000:distribution/E123',
            {'env': 'local'},
        )


class CloudFrontConfigurationInventoryTests(SimpleTestCase):
    @patch('dashboard.aws._nested_operation_items')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_reads_behavior_configuration_not_just_distribution_summaries(self, factory_mock, nested_mock):
        from .aws import cloudfront_inventory
        client = factory_mock.return_value.client.return_value
        client.meta.service_model.operation_names = ['ListDistributions', 'GetDistributionConfig']
        nested_mock.return_value = [{'Id': 'DIST', 'DomainName': 'cdn.local'}]
        client.get_distribution_config.return_value = {'ETag': 'version-1', 'DistributionConfig': {
            'DefaultCacheBehavior': {'TargetOriginId': 'assets'},
            'CacheBehaviors': {'Items': [{'PathPattern': '/api/*', 'TargetOriginId': 'api'}]},
        }}
        distribution = cloudfront_inventory()['distributions'][0]
        self.assertEqual(distribution['DefaultCacheBehavior']['TargetOriginId'], 'assets')
        self.assertEqual(distribution['CacheBehaviors']['Items'][0]['TargetOriginId'], 'api')
        client.get_distribution_config.assert_called_once_with(Id='DIST')
