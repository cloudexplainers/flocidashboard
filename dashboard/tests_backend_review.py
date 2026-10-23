"""Regression tests for the backend API review (docs/console-accuracy-review.md)."""

import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, TestCase


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


class InventedRegistryAndClusterValuesTests(SimpleTestCase):
    def test_ecr_push_commands_use_floci_repository_uri_and_login_endpoint(self):
        from dashboard.ecr_api import get_push_commands

        client = MagicMock()
        client.describe_repositories.return_value = {'repositories': [{'repositoryUri': '000000000000.dkr.ecr.eu-west-1.localhost:4566/web'}]}
        client.get_authorization_token.return_value = {'authorizationData': [{'proxyEndpoint': 'http://000000000000.dkr.ecr.eu-west-1.localhost:4566'}]}
        factory = MagicMock(region='eu-west-1')

        with patch('dashboard.ecr_api._client', return_value=client), \
                patch('dashboard.ecr_api.FlociClientFactory', return_value=factory), \
                patch('dashboard.ecr_api.public_endpoint_url', return_value='http://localhost:4566'):
            data = get_push_commands('web')

        commands = '\n'.join(data['commands']['mac_linux'])
        self.assertEqual(data['registry_uri'], '000000000000.dkr.ecr.eu-west-1.localhost:4566/web')
        self.assertIn('--region eu-west-1 --endpoint-url http://localhost:4566', commands)
        self.assertIn('docker login --username AWS --password-stdin http://000000000000.dkr.ecr.eu-west-1.localhost:4566', commands)
        self.assertIn('docker push 000000000000.dkr.ecr.eu-west-1.localhost:4566/web:latest', commands)
        self.assertNotIn('us-east-1', commands)

    def test_ecr_push_commands_for_missing_repository_is_404(self):
        client = MagicMock()
        client.describe_repositories.side_effect = client_error('RepositoryNotFoundException', 'DescribeRepositories', status=400)

        with patch('dashboard.ecr_api._client', return_value=client):
            response = self.client.get('/api/ecr/repositories/missing/push-commands/')

        self.assertEqual(response.status_code, 404)

    def _kubeconfig(self, cluster=None, error=None):
        from dashboard.eks_api import get_kubeconfig

        client = MagicMock()
        if error:
            client.describe_cluster.side_effect = error
        else:
            client.describe_cluster.return_value = {'cluster': cluster}
        with patch('dashboard.eks_api._client', return_value=client), \
                patch('dashboard.eks_api.FlociClientFactory', return_value=MagicMock(region='eu-west-1')), \
                patch('dashboard.eks_api.public_endpoint_url', return_value='http://localhost:4566'):
            return get_kubeconfig('demo')

    def test_kubeconfig_for_missing_cluster_raises(self):
        with self.assertRaises(ClientError):
            self._kubeconfig(error=client_error('ResourceNotFoundException', 'DescribeCluster', status=404))

    def test_kubeconfig_without_endpoint_is_not_filled_with_placeholders(self):
        with self.assertRaisesRegex(ValueError, 'status CREATING'):
            self._kubeconfig({'name': 'demo', 'status': 'CREATING', 'arn': 'arn:aws:eks:eu-west-1:000000000000:cluster/demo'})

    def test_kubeconfig_uses_reported_values_and_configured_region(self):
        data = self._kubeconfig({
            'name': 'demo', 'status': 'ACTIVE', 'endpoint': 'https://localhost:6443',
            'certificateAuthority': {'data': 'Q0E='}, 'arn': 'arn:aws:eks:eu-west-1:000000000000:cluster/demo',
        })

        self.assertIn('server: https://localhost:6443', data['kubeconfig_yaml'])
        self.assertIn('certificate-authority-data: Q0E=', data['kubeconfig_yaml'])
        self.assertIn('- eu-west-1', data['kubeconfig_yaml'])
        self.assertIn('- http://localhost:4566', data['kubeconfig_yaml'])
        self.assertNotIn('us-east-1', data['aws_cli_command'])


class EcrPutImageTests(SimpleTestCase):
    def test_put_image_failure_is_reported_not_simulated(self):
        client = MagicMock()
        client.put_image.side_effect = client_error('UnsupportedOperation', 'PutImage', 'Operation PutImage is not supported.')

        with patch('dashboard.ecr_api._client', return_value=client):
            response = self.client.post('/api/ecr/mock-image/', data=json.dumps({'repository_name': 'web', 'image_tag': 'v1'}), content_type='application/json')

        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertEqual(data['code'], 'UnsupportedOperation')
        self.assertNotIn('image', data)


class InventedAccountAndActionTests(SimpleTestCase):
    def test_account_root_trust_policy_uses_caller_account(self):
        from dashboard.iam_api import trust_policy_from_template

        sts = MagicMock()
        sts.get_caller_identity.return_value = {'Account': '111122223333'}
        with patch('dashboard.iam_api._sts_client', return_value=sts):
            document = trust_policy_from_template('account-root')

        self.assertEqual(document['Statement'][0]['Principal'], {'AWS': 'arn:aws:iam::111122223333:root'})

    def test_iot_rule_without_actions_gets_no_invented_action(self):
        from dashboard.iot_api import create_topic_rule

        client = MagicMock()
        client.create_topic_rule.return_value = {}
        with patch('dashboard.iot_api._iot_client', return_value=client):
            create_topic_rule('alerts', "SELECT * FROM 'sensors/#'")

        self.assertEqual(client.create_topic_rule.call_args.kwargs['topicRulePayload']['actions'], [])

    def test_sqs_message_move_reports_only_floci_handle(self):
        from dashboard.sqs_api import start_message_move_task

        client = MagicMock()
        client.start_message_move_task.return_value = {'TaskHandle': 'handle-1'}
        with patch('dashboard.sqs_api._sqs_client', return_value=client):
            data = start_message_move_task('arn:aws:sqs:eu-west-1:111122223333:dlq')

        self.assertEqual(data['task_handle'], 'handle-1')
        self.assertNotIn('status', data)

    def test_codedeploy_simulated_lifecycle_endpoint_is_gone(self):
        from dashboard.services import SERVICE_REGISTRY

        self.assertNotIn('simulate_lifecycle_event', [action.name for action in SERVICE_REGISTRY['codedeploy'].actions])
        self.assertEqual(self.client.post('/api/codedeploy/deployments/lifecycle/', data='{}', content_type='application/json').status_code, 404)


class AssumeRoleAccountTests(TestCase):
    @patch('dashboard.identity_views.FlociClientFactory')
    def test_assume_role_by_name_uses_caller_account(self, factory_class):
        factory = factory_class.return_value
        factory.endpoint_url = 'http://localhost:4566'
        factory.region = 'eu-west-1'
        factory.credential_context.return_value = {'credential_source': 'session_identity'}
        factory.identity.return_value = {'account': '111122223333', 'arn': 'arn:aws:iam::111122223333:user/alice', 'user_id': 'AIDA'}
        factory.local_identity_hint.return_value = None
        sts = factory.client.return_value
        sts.get_caller_identity.return_value = {'Account': '111122223333'}
        sts.assume_role.return_value = {'Credentials': {'AccessKeyId': 'ASIA', 'SecretAccessKey': 's', 'SessionToken': 't'}}

        self.client.post('/api/session-identity/assume-role/', data=json.dumps({'role_name': 'App', 'session_name': 'review'}), content_type='application/json')

        sts.assume_role.assert_called_once_with(RoleArn='arn:aws:iam::111122223333:role/App', RoleSessionName='review')


class PagingAndParameterTests(SimpleTestCase):
    def test_s3_version_listing_carries_both_markers(self):
        from dashboard.s3_api import list_s3_objects

        client = MagicMock()
        client.list_object_versions.return_value = {
            'Versions': [{'Key': 'a.txt', 'VersionId': 'v2', 'IsLatest': True}],
            'IsTruncated': True, 'NextKeyMarker': 'a.txt', 'NextVersionIdMarker': 'v1',
        }
        with patch('dashboard.s3_api._s3_client', return_value=client):
            data = list_s3_objects('bucket', versions=True, continuation_token='a.txt', version_id_marker='v3')

        self.assertEqual(client.list_object_versions.call_args.kwargs['KeyMarker'], 'a.txt')
        self.assertEqual(client.list_object_versions.call_args.kwargs['VersionIdMarker'], 'v3')
        self.assertEqual((data['next_continuation_token'], data['next_version_id_marker']), ('a.txt', 'v1'))

    def test_opensearch_update_rejects_engine_version_with_pointer_to_upgrade(self):
        from dashboard.opensearch_api import update_domain_config

        with self.assertRaisesRegex(ValueError, 'UpgradeDomain'):
            update_domain_config(domain_name='search', engine_version='OpenSearch_2.19')


class S3EmptyBucketTests(SimpleTestCase):
    def _client(self, delete_response):
        client = MagicMock()
        client.get_paginator.return_value.paginate.return_value.build_full_result.return_value = {
            'Versions': [{'Key': 'a.txt', 'VersionId': 'v1'}, {'Key': 'a.txt', 'VersionId': 'v2'}, {'Key': 'plain.txt', 'VersionId': 'null'}],
            'DeleteMarkers': [{'Key': 'b.txt', 'VersionId': 'm1'}],
        }
        client.delete_objects.return_value = delete_response
        return client

    def test_empty_deletes_every_version_and_counts_deleted(self):
        client = self._client({'Deleted': [{}, {}, {}, {}]})
        with patch('dashboard.s3_api._s3_client', return_value=client):
            response = self.client.post('/api/s3/buckets/bucket-a/empty/')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content)['deleted_count'], 4)
        delete = client.delete_objects.call_args.kwargs['Delete']
        self.assertFalse(delete['Quiet'])
        self.assertEqual(delete['Objects'], [
            {'Key': 'a.txt', 'VersionId': 'v1'}, {'Key': 'a.txt', 'VersionId': 'v2'},
            {'Key': 'plain.txt'}, {'Key': 'b.txt', 'VersionId': 'm1'},
        ])

    def test_partial_failure_is_not_reported_as_success(self):
        client = self._client({'Deleted': [{}, {}, {}], 'Errors': [{'Key': 'a.txt', 'Code': 'AccessDenied', 'Message': 'Access Denied'}]})
        with patch('dashboard.s3_api._s3_client', return_value=client):
            response = self.client.post('/api/s3/buckets/bucket-a/empty/')

        self.assertEqual(response.status_code, 409)
        data = json.loads(response.content)
        self.assertEqual(data['deleted_count'], 3)
        self.assertIn('1 of 4 objects could not be deleted', data['error'])


class TruncationAndCognitoTests(SimpleTestCase):
    def test_record_truncation_only_when_items_are_left_out(self):
        from dashboard.aws import collect_load_errors, record_truncation

        def loader():
            record_truncation('GetPipeline', 'pipelines', 50, 50)
            record_truncation('DescribeDocument', 'documents', 50, 73)

        _, errors = collect_load_errors(loader)

        self.assertEqual(errors, [{'operation': 'DescribeDocument', 'code': 'Truncated', 'error': 'Shown for the first 50 of 73 documents.'}])

    def test_manual_page_limit_is_reported(self):
        from dashboard.aws import _MANUAL_PAGE_LIMIT, _paginate, collect_load_errors

        client = MagicMock()
        client.can_paginate.return_value = False
        pages = iter(range(10_000))
        client.list_things.side_effect = lambda **_: {'items': [1], 'NextToken': f't{next(pages)}'}

        items, errors = collect_load_errors(lambda: _paginate(client, 'list_things', 'items'))

        self.assertEqual(len(items), _MANUAL_PAGE_LIMIT)
        self.assertEqual(errors[0]['code'], 'Truncated')

    def test_cognito_pages_users_and_uses_reported_pool_arn(self):
        from dashboard.aws.cognito import cognito_inventory

        client = MagicMock()
        client.can_paginate.return_value = True

        def full_result(operation):
            results = {
                'list_user_pools': {'UserPools': [{'Id': 'eu-west-1_abc', 'Name': 'pool'}]},
                'list_users': {'Users': [{'Username': f'user-{index}'} for index in range(75)]},
            }
            paginator = MagicMock()
            paginator.paginate.return_value.build_full_result.return_value = results.get(operation, {})
            return paginator

        client.get_paginator.side_effect = full_result
        client.describe_user_pool.return_value = {'UserPool': {'Id': 'eu-west-1_abc', 'Arn': 'arn:aws:cognito-idp:eu-west-1:111122223333:userpool/eu-west-1_abc'}}
        client.list_tags_for_resource.return_value = {'Tags': {}}
        factory = MagicMock(region='eu-west-1', endpoint_url='http://floci:4566')
        factory.client.return_value = client

        with patch('dashboard.aws.FlociClientFactory', return_value=factory), \
                patch('dashboard.aws.public_endpoint_url', return_value='http://localhost:4566'):
            data = cognito_inventory()

        pool = data['user_pools'][0] if 'user_pools' in data else data['pools'][0]
        self.assertEqual(pool['arn'], 'arn:aws:cognito-idp:eu-west-1:111122223333:userpool/eu-west-1_abc')
        client.list_tags_for_resource.assert_called_once_with(ResourceArn=pool['arn'])
        self.assertEqual(len(pool['users']), 75)
        self.assertTrue(pool['discovery_url'].startswith('http://localhost:4566/'))
