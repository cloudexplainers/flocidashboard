"""Regressions found while validating the dashboard against Floci 2.3.0."""

from unittest.mock import Mock

from botocore.exceptions import ClientError
from django.test import SimpleTestCase

from .aws import _sesv2_items
from .aws.autoscaling import _autoscaling_optional


class CurrentCompatibilityTests(SimpleTestCase):
    def test_sesv2_lists_follow_tokens_without_sdk_paginator(self):
        client = Mock()
        client.list_email_identities.side_effect = [
            {'EmailIdentities': [{'IdentityName': 'first@example.com'}], 'NextToken': 'page2'},
            {'EmailIdentities': [{'IdentityName': 'second@example.com'}]},
        ]
        identities = _sesv2_items(client, 'list_email_identities', 'EmailIdentities')
        self.assertEqual([item['IdentityName'] for item in identities], [
            'first@example.com', 'second@example.com',
        ])
        self.assertEqual(client.list_email_identities.call_args_list[1].kwargs, {'NextToken': 'page2'})
        client.get_paginator.assert_not_called()

    def test_sesv2_repeated_token_stops_instead_of_hanging_inventory(self):
        client = Mock()
        client.list_email_templates.return_value = {'TemplatesMetadata': [], 'NextToken': 'same'}
        with self.assertRaisesRegex(ValueError, 'repeated NextToken'):
            _sesv2_items(client, 'list_email_templates', 'TemplatesMetadata')

    def test_autoscaling_optional_unsupported_operation(self):
        loader = Mock(side_effect=ClientError(
            {'Error': {'Code': 'UnsupportedOperation', 'Message': 'Not supported'}},
            'DescribeScalingProcessTypes',
        ))
        self.assertIsNone(_autoscaling_optional(loader, set()))

    def test_autoscaling_optional_keeps_unexpected_errors_visible(self):
        loader = Mock(side_effect=ClientError(
            {'Error': {'Code': 'AccessDenied', 'Message': 'Denied'}},
            'DescribeScalingProcessTypes',
        ))
        self.assertIn('AccessDenied', _autoscaling_optional(loader, set())['error'])


class ConsoleDeepLinkTests(SimpleTestCase):
    def test_every_registered_service_and_subpage_mounts_the_spa(self):
        from .services import SERVICE_REGISTRY
        for key in SERVICE_REGISTRY:
            for suffix in ('', '/', '/overview'):
                with self.subTest(service=key, suffix=suffix):
                    response = self.client.get(f'/{key}{suffix}')
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'id="root"')

    def test_unregistered_paths_are_not_swallowed_by_spa(self):
        self.assertEqual(self.client.get('/not-a-service/').status_code, 404)
        self.assertEqual(self.client.get('/api/not-a-service/').status_code, 404)
