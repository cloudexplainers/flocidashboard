"""Regression tests for the second console accuracy review."""

from unittest.mock import MagicMock

from django.test import SimpleTestCase

from dashboard.aws import _paginate


class ManualPaginationTests(SimpleTestCase):
    def test_follows_next_token_when_botocore_has_no_paginator(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.list_work_groups.side_effect = [
            {'WorkGroups': [{'Name': 'primary'}], 'NextToken': 't1'},
            {'WorkGroups': [{'Name': 'analytics'}]},
        ]

        self.assertEqual(
            [item['Name'] for item in _paginate(client, 'list_work_groups', 'WorkGroups')],
            ['primary', 'analytics'],
        )
        client.get_paginator.assert_not_called()
        self.assertEqual(client.list_work_groups.call_args_list[1].kwargs, {'NextToken': 't1'})

    def test_firehose_uses_last_stream_name_as_start(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.list_delivery_streams.side_effect = [
            {'DeliveryStreamNames': ['a'], 'HasMoreDeliveryStreams': True},
            {'DeliveryStreamNames': ['b'], 'HasMoreDeliveryStreams': False},
        ]

        self.assertEqual(_paginate(client, 'list_delivery_streams', 'DeliveryStreamNames'), ['a', 'b'])
        self.assertEqual(
            client.list_delivery_streams.call_args_list[1].kwargs,
            {'ExclusiveStartDeliveryStreamName': 'a'},
        )

    def test_repeated_token_stops(self):
        client = MagicMock()
        client.can_paginate.return_value = False
        client.list_things.return_value = {'items': [1], 'NextToken': 'same'}

        self.assertEqual(_paginate(client, 'list_things', 'items'), [1, 1])


class RdsFamilyEngineFilterTests(SimpleTestCase):
    def test_neptune_inventory_lists_only_neptune_clusters(self):
        from unittest.mock import patch

        from dashboard.aws.neptune import neptune_inventory

        client = MagicMock()
        client.meta.service_model.operation_names = ['DescribeDBClusters', 'DescribeDBInstances']
        client.can_paginate.return_value = False
        client.describe_db_clusters.return_value = {'DBClusters': [
            {'DBClusterIdentifier': 'graph', 'Engine': 'neptune'},
            {'DBClusterIdentifier': 'docs', 'Engine': 'docdb'},
            {'DBClusterIdentifier': 'aurora', 'Engine': 'aurora-postgresql'},
        ]}
        client.describe_db_instances.return_value = {'DBInstances': []}
        with patch('dashboard.aws.FlociClientFactory') as factory:
            factory.return_value.client.return_value = client
            result = neptune_inventory()

        self.assertEqual([item['DBClusterIdentifier'] for item in result['clusters']], ['graph'])


class FirehoseCreateTests(SimpleTestCase):
    def test_create_requires_s3_destination(self):
        from unittest.mock import patch

        from dashboard.firehose_api import create_delivery_stream

        with self.assertRaisesMessage(ValueError, 'S3 bucket ARN'):
            create_delivery_stream('orders')
        with patch('dashboard.firehose_api._client') as client:
            client.return_value.create_delivery_stream.return_value = {'DeliveryStreamARN': 'arn'}
            create_delivery_stream('orders', s3_bucket_arn='lake', role_arn='arn:aws:iam::000000000000:role/fh')
        kwargs = client.return_value.create_delivery_stream.call_args.kwargs
        self.assertEqual(kwargs['DeliveryStreamType'], 'DirectPut')
        self.assertEqual(kwargs['ExtendedS3DestinationConfiguration']['BucketARN'], 'arn:aws:s3:::lake')
        self.assertEqual(kwargs['ExtendedS3DestinationConfiguration']['BufferingHints'], {'SizeInMBs': 5, 'IntervalInSeconds': 300})


class WorkbenchFieldDefaultsTests(SimpleTestCase):
    def _field(self, service_key, action_name, field_name):
        from dashboard.services import SERVICES

        service = next(item for item in SERVICES if item.key == service_key)
        action = next(item for item in service.actions if item.name == action_name)
        return next(item for item in action.fields if item.name == field_name)

    def test_enum_fields_list_aws_values_and_console_defaults(self):
        mode = self._field('kinesis', 'create_stream', 'mode')
        self.assertEqual(mode.options, ('ON_DEMAND', 'PROVISIONED'))
        self.assertEqual(mode.default, 'ON_DEMAND')
        self.assertEqual(self._field('codedeploy', 'create_application', 'compute_platform').default, 'Server')
        self.assertEqual(self._field('autoscaling', 'put_lifecycle_hook', 'default_result').default, 'ABANDON')

    def test_neptune_delete_shows_that_no_snapshot_is_taken(self):
        skip = self._field('neptune', 'delete_db_cluster', 'skip_final_snapshot')
        self.assertIs(skip.default, True)
        self.assertIn('does not create Neptune snapshots', skip.help_text)

    def test_transfer_actions_stay_in_transfer(self):
        from dashboard.services import SERVICES

        transfer = next(item for item in SERVICES if item.key == 'transfer')
        self.assertTrue(all(action.path.startswith('/api/transfer/') for action in transfer.actions))


class AmazonMqUserCountTests(SimpleTestCase):
    def test_rabbitmq_user_count_is_unknown_not_zero(self):
        from unittest.mock import patch

        from dashboard.aws.amazonmq import amazonmq_inventory

        mq = MagicMock()
        mq.meta.service_model.operation_names = ['ListBrokers', 'DescribeBroker', 'ListUsers']
        mq.can_paginate.return_value = False
        mq.list_brokers.return_value = {'BrokerSummaries': [{'BrokerId': 'b-1', 'BrokerName': 'events', 'EngineType': 'RABBITMQ'}]}
        mq.describe_broker.return_value = {'BrokerId': 'b-1', 'EngineType': 'RABBITMQ'}
        with patch('dashboard.aws.FlociClientFactory') as factory:
            factory.return_value.client.return_value = mq
            result = amazonmq_inventory()

        self.assertIsNone(result['brokers'][0]['user_count'])
        mq.list_users.assert_not_called()


class S3VersionListingTests(SimpleTestCase):
    def test_versions_keep_folders_and_list_current_version_first(self):
        from unittest.mock import patch

        from dashboard.s3_api import list_s3_objects

        with patch('dashboard.s3_api._s3_client') as client:
            client.return_value.list_object_versions.return_value = {
                'CommonPrefixes': [{'Prefix': 'docs/'}],
                'Versions': [{'Key': 'a.txt', 'VersionId': 'v1', 'IsLatest': False}],
                'DeleteMarkers': [{'Key': 'a.txt', 'VersionId': 'm1', 'IsLatest': True}],
            }
            result = list_s3_objects('bucket', versions=True)

        client.return_value.list_object_versions.assert_called_once_with(Bucket='bucket', MaxKeys=100, Delimiter='/')
        self.assertEqual(result['folders'], [{'prefix': 'docs/', 'name': 'docs'}])
        self.assertEqual([item['version_id'] for item in result['objects']], ['m1', 'v1'])


class LambdaSourceTests(SimpleTestCase):
    def _package(self, files):
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        return buffer.getvalue()

    def test_deploy_replaces_only_the_edited_file(self):
        import io
        import zipfile
        from unittest.mock import patch

        from dashboard import lambda_api

        package = self._package({'app.py': 'old', 'lib/helper.py': 'keep', 'bin/tool': b'\x00\x01'})
        with patch.object(lambda_api, '_download_package', return_value=({}, package)), patch.object(lambda_api, '_lambda_client') as client:
            client.return_value.update_function_code.return_value = {'LastUpdateStatus': 'Successful'}
            lambda_api.deploy_function_source('worker', {'app.py': 'new'})

        sent = zipfile.ZipFile(io.BytesIO(client.return_value.update_function_code.call_args.kwargs['ZipFile']))
        self.assertEqual(sent.read('app.py'), b'new')
        self.assertEqual(sent.read('lib/helper.py'), b'keep')
        self.assertEqual(sent.read('bin/tool'), b'\x00\x01')

    def test_source_marks_binary_files_read_only_and_finds_the_handler_file(self):
        from unittest.mock import patch

        from dashboard import lambda_api

        package = self._package({'app.py': 'def handler(e, c): pass', 'native.so': b'\xff\xfe'})
        with patch.object(lambda_api, '_download_package', return_value=({'Handler': 'app.handler'}, package)):
            source = lambda_api.get_function_source('worker')

        self.assertEqual(source['handler_file'], 'app.py')
        self.assertEqual({item['name']: item['editable'] for item in source['files']}, {'app.py': True, 'native.so': False})

    def test_download_uses_only_the_location_path_on_the_configured_endpoint(self):
        from unittest.mock import MagicMock, patch

        from dashboard import lambda_api

        client = MagicMock()
        client.get_function.return_value = {'Configuration': {}, 'Code': {'Location': 'http://attacker.example/pkg/worker?sig=1'}}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'zip'
        with patch.object(lambda_api, '_lambda_client', return_value=client), \
                patch.object(lambda_api, 'FlociClientFactory') as factory, \
                patch.object(lambda_api.urllib.request, 'urlopen', return_value=response) as urlopen:
            factory.return_value.endpoint_url = 'http://floci:4566'
            lambda_api._download_package('worker')

        self.assertEqual(urlopen.call_args.args[0], 'http://floci:4566/pkg/worker?sig=1')


class SnsTopicAttributeTests(SimpleTestCase):
    def test_policy_must_be_json_and_other_attributes_are_refused(self):
        from unittest.mock import patch

        from dashboard.sns_api import set_topic_attribute

        arn = 'arn:aws:sns:us-east-1:000000000000:orders'
        with patch('dashboard.sns_api._sns_client') as client:
            client.return_value.get_topic_attributes.return_value = {'Attributes': {}}
            with self.assertRaisesMessage(ValueError, 'Policy must be valid JSON'):
                set_topic_attribute(arn, 'Policy', '{bad')
            with self.assertRaisesMessage(ValueError, 'Attribute must be one of'):
                set_topic_attribute(arn, 'Owner', 'x')
            set_topic_attribute(arn, 'Policy', {'Version': '2012-10-17', 'Statement': []})
        client.return_value.set_topic_attributes.assert_called_once_with(
            TopicArn=arn, AttributeName='Policy', AttributeValue='{"Version": "2012-10-17", "Statement": []}',
        )
