"""Regression tests for the backend API review (docs/console-accuracy-review.md)."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase


def client_error(code: str, operation: str, message: str = 'boom', status: int = 400) -> ClientError:
    return ClientError(
        {'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': status}},
        operation,
    )


def _raise(exc):
    raise exc


class LoadErrorCollectionTests(SimpleTestCase):
    def test_safe_value_records_failed_call(self):
        from dashboard.aws import _safe_value, collect_load_errors

        def loader():
            return {
                'snapshots': _safe_value(lambda: _raise(client_error('UnsupportedOperation', 'DescribeSnapshots')), []),
            }

        result, errors = collect_load_errors(loader)

        self.assertEqual(result, {'snapshots': []})
        self.assertEqual(errors, [{'operation': 'DescribeSnapshots', 'code': 'UnsupportedOperation', 'error': 'boom'}])

    def test_absent_resource_codes_are_not_errors(self):
        from dashboard.aws import _safe_value, collect_load_errors

        def loader():
            for code in ('NoSuchBucketPolicy', 'ResourceNotFoundException', 'QueueDoesNotExist', 'ServerSideEncryptionConfigurationNotFoundError'):
                _safe_value(lambda code=code: _raise(client_error(code, 'GetThing')), None)

        _, errors = collect_load_errors(loader)

        self.assertEqual(errors, [])

    def test_safe_value_outside_collection_still_returns_fallback(self):
        from dashboard.aws import _safe_value

        self.assertEqual(_safe_value(lambda: _raise(client_error('Throttling', 'ListThings')), ['x']), ['x'])

    def test_errors_from_worker_threads_are_collected(self):
        from dashboard.aws import _safe_value, collect_load_errors, map_in_context

        def detail(name):
            return _safe_value(lambda: _raise(client_error('InternalError', f'Describe{name}')), None)

        def loader():
            with ThreadPoolExecutor(max_workers=2) as executor:
                return map_in_context(executor, detail, ['A', 'B'])

        result, errors = collect_load_errors(loader)

        self.assertEqual(result, [None, None])
        self.assertEqual(sorted(error['operation'] for error in errors), ['DescribeA', 'DescribeB'])

    def test_inventory_endpoint_reports_load_errors(self):
        from dashboard.aws import _safe_value

        def fake_inventory():
            return {
                'clusters': [],
                'snapshots': _safe_value(lambda: _raise(client_error('UnsupportedOperation', 'DescribeSnapshots', 'Operation DescribeSnapshots is not supported.')), []),
                'events': _safe_value(lambda: _raise(client_error('InternalFailure', 'DescribeEvents', 'kaput', 500)), []),
            }

        with patch('dashboard.views.elasticache_inventory', fake_inventory):
            response = self.client.get('/api/elasticache/')

        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertEqual(data['snapshots'], [])
        self.assertEqual(data['load_errors'], [
            {'operation': 'DescribeSnapshots', 'code': 'UnsupportedOperation', 'error': 'Operation DescribeSnapshots is not supported.', 'unsupported': True},
            {'operation': 'DescribeEvents', 'code': 'InternalFailure', 'error': 'kaput', 'unsupported': False},
        ])

    def test_inventory_endpoint_without_errors_has_no_load_errors_key(self):
        with patch('dashboard.views.elasticache_inventory', lambda: {'clusters': []}):
            data = json.loads(self.client.get('/api/elasticache/').content)

        self.assertNotIn('load_errors', data)


class InventoryRequiredParameterTests(SimpleTestCase):
    def test_config_remediation_configurations_send_rule_names(self):
        from dashboard.aws.config import config_inventory

        client = MagicMock()
        client.meta.service_model.operation_names = ['DescribeConfigRules', 'DescribeRemediationConfigurations']
        client.can_paginate.return_value = False
        client.describe_config_rules.return_value = {'ConfigRules': [{'ConfigRuleName': 'rule-a'}, {'ConfigRuleName': 'rule-b'}]}
        client.describe_remediation_configurations.return_value = {'RemediationConfigurations': [{'ConfigRuleName': 'rule-a'}]}
        factory = MagicMock()
        factory.client.return_value = client

        with patch('dashboard.aws.FlociClientFactory', return_value=factory):
            data = config_inventory()

        client.describe_remediation_configurations.assert_called_once_with(ConfigRuleNames=['rule-a', 'rule-b'])
        self.assertEqual(data['remediation_configurations'], [{'ConfigRuleName': 'rule-a'}])

    def test_config_skips_remediation_call_without_rules(self):
        from dashboard.aws.config import config_inventory

        client = MagicMock()
        client.meta.service_model.operation_names = ['DescribeConfigRules', 'DescribeRemediationConfigurations']
        client.can_paginate.return_value = False
        client.describe_config_rules.return_value = {'ConfigRules': []}
        factory = MagicMock()
        factory.client.return_value = client

        with patch('dashboard.aws.FlociClientFactory', return_value=factory):
            config_inventory()

        client.describe_remediation_configurations.assert_not_called()

    def test_pricing_price_lists_send_required_date_and_currency(self):
        from dashboard.aws.pricing import pricing_inventory

        client = MagicMock()
        client.meta.service_model.operation_names = ['ListPriceLists']
        client.can_paginate.return_value = False
        client.list_price_lists.return_value = {'PriceLists': [{'PriceListArn': 'arn:aws:pricing:::price-list/aws/AmazonS3/USD/1'}]}
        factory = MagicMock()
        factory.client.return_value = client

        with patch('dashboard.aws.FlociClientFactory', return_value=factory):
            data = pricing_inventory()

        kwargs = client.list_price_lists.call_args.kwargs
        self.assertEqual(kwargs['ServiceCode'], 'AmazonS3')
        self.assertEqual(kwargs['CurrencyCode'], 'USD')
        self.assertIn('EffectiveDate', kwargs)
        self.assertEqual(len(data['price_lists']), 1)


class AutoScalingDefaultsTests(SimpleTestCase):
    def _post_policy(self, body):
        client = MagicMock()
        client.put_scaling_policy.return_value = {'PolicyARN': 'arn:policy'}
        with patch('dashboard.autoscaling_api._client', return_value=client):
            response = self.client.post('/api/autoscaling/groups/web/policies/', data=json.dumps(body), content_type='application/json')
        return response, client

    def test_blank_cooldown_is_not_sent(self):
        response, client = self._post_policy({'policy_name': 'up', 'scaling_adjustment': 2})

        self.assertEqual(response.status_code, 200)
        self.assertNotIn('Cooldown', client.put_scaling_policy.call_args.kwargs)
        self.assertEqual(client.put_scaling_policy.call_args.kwargs['ScalingAdjustment'], 2)

    def test_zero_cooldown_is_kept(self):
        _, client = self._post_policy({'policy_name': 'up', 'scaling_adjustment': 1, 'cooldown': 0})

        self.assertEqual(client.put_scaling_policy.call_args.kwargs['Cooldown'], 0)

    def test_missing_adjustment_is_rejected_not_guessed(self):
        response, client = self._post_policy({'policy_name': 'up'})

        self.assertEqual(response.status_code, 400)
        client.put_scaling_policy.assert_not_called()

    def test_delete_group_honors_force_delete_false(self):
        client = MagicMock()
        client.delete_auto_scaling_group.return_value = {}
        with patch('dashboard.autoscaling_api._client', return_value=client):
            self.client.delete('/api/autoscaling/groups/web/', data=json.dumps({'force_delete': False}), content_type='application/json')

        client.delete_auto_scaling_group.assert_called_once_with(AutoScalingGroupName='web', ForceDelete=False)


class VisibleDefaultsTests(SimpleTestCase):
    """Defaults the backend applies when a field is blank are shown in the generic form."""

    def _field(self, service, action_name, field_name):
        from dashboard.services import SERVICE_REGISTRY

        action = next(item for item in SERVICE_REGISTRY[service].actions if item.name == action_name)
        return next(item for item in action.fields if item.name == field_name)

    def test_backend_defaults_are_form_defaults(self):
        cases = [
            ('acm', 'put_account_configuration', 'days_before_expiry', 45),
            ('autoscaling', 'put_lifecycle_hook', 'heartbeat_timeout', 3600),
            ('autoscaling', 'delete_auto_scaling_group', 'force_delete', True),
            ('codepipeline', 'set_stage_transition', 'transition_type', 'Inbound'),
            ('elasticache', 'create_user', 'access_string', 'on ~* +@all'),
            ('kafka', 'create_cluster', 'kafka_version', '3.6.1'),
            ('opensearch', 'create_domain', 'instance_type', 'm5.large.search'),
            ('transcribe', 'create_vocabulary', 'language_code', 'en-US'),
        ]
        for service, action_name, field_name, default in cases:
            with self.subTest(action=action_name, field=field_name):
                self.assertEqual(self._field(service, action_name, field_name).default, default)

    def test_scaling_adjustment_is_required(self):
        self.assertTrue(self._field('autoscaling', 'put_scaling_policy', 'scaling_adjustment').required)
        self.assertIsNone(self._field('autoscaling', 'put_scaling_policy', 'cooldown').default)
