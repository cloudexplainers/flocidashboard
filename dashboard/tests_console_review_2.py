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
