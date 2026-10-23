"""Lambda async config, Step Functions aliases, DynamoDB exports and Kinesis resharding endpoints."""

import json
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase
from django.urls import reverse

MACHINE = 'arn:aws:states:us-east-1:000000000000:stateMachine:orders'
V1 = f'{MACHINE}:1'
V2 = f'{MACHINE}:2'
ALIAS = f'{MACHINE}:prod'


def send(client, method, url, body=None):
    kwargs = {} if body is None else {'data': json.dumps(body), 'content_type': 'application/json'}
    return getattr(client, method)(url, **kwargs)


@patch('dashboard.lambda_api.FlociClientFactory')
class LambdaAsyncConfigTests(SimpleTestCase):
    def url(self):
        return reverse('dashboard:lambda-function-event-invoke-config', kwargs={'function_name': 'worker'})

    def test_defaults_when_no_config(self, factory):
        factory.return_value.client.return_value.get_function_event_invoke_config.side_effect = ClientError(
            {'Error': {'Code': 'ResourceNotFoundException', 'Message': 'The function worker has no EventInvokeConfig'}}, 'Get')
        self.assertEqual(self.client.get(self.url()).json(), {'config': None})

    def test_put_config_with_destinations(self, factory):
        lam = factory.return_value.client.return_value
        lam.get_function_event_invoke_config.return_value = {
            'MaximumRetryAttempts': 1, 'MaximumEventAgeInSeconds': 3600,
            'DestinationConfig': {'OnFailure': {'Destination': 'arn:aws:sqs:us-east-1:000000000000:dlq'}}}
        response = send(self.client, 'put', self.url(), {
            'maximum_retry_attempts': 1, 'maximum_event_age_in_seconds': '3600',
            'on_failure': 'arn:aws:sqs:us-east-1:000000000000:dlq', 'on_success': ''})
        self.assertEqual(response.status_code, 200)
        lam.put_function_event_invoke_config.assert_called_once_with(
            FunctionName='worker', MaximumRetryAttempts=1, MaximumEventAgeInSeconds=3600,
            DestinationConfig={'OnFailure': {'Destination': 'arn:aws:sqs:us-east-1:000000000000:dlq'}})
        self.assertEqual(response.json()['config']['on_failure'], 'arn:aws:sqs:us-east-1:000000000000:dlq')

    def test_put_checks_aws_limits(self, factory):
        for body, message in (({'maximum_retry_attempts': 3}, '0, 1 or 2'),
                              ({'maximum_event_age_in_seconds': 30}, '60 seconds to 6 hours'),
                              ({'on_success': 'not-an-arn'}, 'ARN of an SQS queue')):
            response = send(self.client, 'put', self.url(), body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn(message, response.json()['error'])
        factory.return_value.client.return_value.put_function_event_invoke_config.assert_not_called()

    def test_delete_config(self, factory):
        response = send(self.client, 'delete', self.url())
        self.assertEqual(response.json(), {'config': None, 'deleted': True})
        factory.return_value.client.return_value.delete_function_event_invoke_config.assert_called_once_with(FunctionName='worker')


@patch('dashboard.stepfunctions_api.FlociClientFactory')
class StepFunctionsAliasTests(SimpleTestCase):
    def test_list_versions_and_aliases(self, factory):
        sfn = factory.return_value.client.return_value
        sfn.list_state_machine_versions.return_value = {'stateMachineVersions': [
            {'stateMachineVersionArn': V1}, {'stateMachineVersionArn': V2}]}
        sfn.describe_state_machine.side_effect = lambda stateMachineArn: {'description': f'd{stateMachineArn[-1]}'}
        sfn.list_state_machine_aliases.return_value = {'stateMachineAliases': [{'stateMachineAliasArn': ALIAS}]}
        sfn.describe_state_machine_alias.return_value = {'name': 'prod', 'routingConfiguration': [
            {'stateMachineVersionArn': V2, 'weight': 90}, {'stateMachineVersionArn': V1, 'weight': 10}]}
        data = self.client.get(reverse('dashboard:stepfunctions-versions-aliases'), {'state_machine_arn': MACHINE}).json()
        self.assertEqual([v['version'] for v in data['versions']], ['2', '1'])
        self.assertEqual(data['aliases'][0]['routing'], [{'version_arn': V2, 'weight': 90}, {'version_arn': V1, 'weight': 10}])

    def test_create_weighted_alias(self, factory):
        sfn = factory.return_value.client.return_value
        sfn.create_state_machine_alias.return_value = {'stateMachineAliasArn': ALIAS}
        response = send(self.client, 'post', reverse('dashboard:stepfunctions-aliases'), {
            'name': 'prod', 'routing': [{'version_arn': V2, 'weight': 90}, {'version_arn': V1, 'weight': 10}]})
        self.assertEqual(response.json(), {'alias_arn': ALIAS})
        sfn.create_state_machine_alias.assert_called_once_with(name='prod', routingConfiguration=[
            {'stateMachineVersionArn': V2, 'weight': 90}, {'stateMachineVersionArn': V1, 'weight': 10}])

    def test_alias_routing_rules(self, factory):
        for routing, message in (([{'version_arn': V1, 'weight': 90}], 'add up to 100'),
                                 ([{'version_arn': V1, 'weight': 50}, {'version_arn': V1, 'weight': 50}], 'two different versions'),
                                 ([], 'one version, or to two'),
                                 ([{'version_arn': V1, 'weight': 'x'}], 'whole numbers')):
            response = send(self.client, 'post', reverse('dashboard:stepfunctions-aliases'), {'name': 'prod', 'routing': routing})
            self.assertEqual(response.status_code, 400, routing)
            self.assertIn(message, response.json()['error'])
        response = send(self.client, 'post', reverse('dashboard:stepfunctions-aliases'), {'name': '123', 'routing': [{'version_arn': V1, 'weight': 100}]})
        self.assertEqual(response.status_code, 400)
        factory.return_value.client.return_value.create_state_machine_alias.assert_not_called()

    def test_update_and_delete_alias(self, factory):
        sfn = factory.return_value.client.return_value
        send(self.client, 'put', reverse('dashboard:stepfunctions-aliases'), {'alias_arn': ALIAS, 'routing': [{'version_arn': V2, 'weight': 100}]})
        sfn.update_state_machine_alias.assert_called_once_with(stateMachineAliasArn=ALIAS, routingConfiguration=[{'stateMachineVersionArn': V2, 'weight': 100}])
        self.assertTrue(send(self.client, 'delete', reverse('dashboard:stepfunctions-aliases'), {'alias_arn': ALIAS}).json()['deleted'])
        sfn.delete_state_machine_alias.assert_called_once_with(stateMachineAliasArn=ALIAS)


@patch('dashboard.dynamodb_api.FlociClientFactory')
class DynamoDBExportTests(SimpleTestCase):
    def url(self):
        return reverse('dashboard:dynamodb-table-exports', kwargs={'table_name': 'orders'})

    def setUp(self):
        self.table_arn = 'arn:aws:dynamodb:us-east-1:000000000000:table/orders'

    def test_list_exports(self, factory):
        ddb = factory.return_value.client.return_value
        ddb.describe_table.return_value = {'Table': {'TableArn': self.table_arn}}
        ddb.list_exports.return_value = {'ExportSummaries': [{'ExportArn': 'e1'}]}
        ddb.describe_export.return_value = {'ExportDescription': {
            'ExportArn': 'e1', 'ExportStatus': 'FAILED', 'S3Bucket': 'missing', 'FailureCode': 'S3NoSuchBucket'}}
        data = self.client.get(self.url()).json()
        self.assertEqual(data['exports'][0]['status'], 'FAILED')
        self.assertEqual(data['exports'][0]['failure_code'], 'S3NoSuchBucket')
        ddb.list_exports.assert_called_once_with(TableArn=self.table_arn)

    def test_export_requires_pitr(self, factory):
        ddb = factory.return_value.client.return_value
        ddb.describe_continuous_backups.return_value = {'ContinuousBackupsDescription': {
            'PointInTimeRecoveryDescription': {'PointInTimeRecoveryStatus': 'DISABLED'}}}
        response = send(self.client, 'post', self.url(), {'s3_bucket': 'exports'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('point-in-time recovery', response.json()['error'])
        ddb.export_table_to_point_in_time.assert_not_called()

    def test_export_to_s3(self, factory):
        ddb = factory.return_value.client.return_value
        ddb.describe_continuous_backups.return_value = {'ContinuousBackupsDescription': {
            'PointInTimeRecoveryDescription': {'PointInTimeRecoveryStatus': 'ENABLED'}}}
        ddb.describe_table.return_value = {'Table': {'TableArn': self.table_arn}}
        ddb.export_table_to_point_in_time.return_value = {'ExportDescription': {'ExportArn': 'e2', 'ExportStatus': 'IN_PROGRESS'}}
        response = send(self.client, 'post', self.url(), {'s3_bucket': 'exports', 's3_prefix': '/daily/'})
        self.assertEqual(response.json()['status'], 'IN_PROGRESS')
        ddb.export_table_to_point_in_time.assert_called_once_with(
            TableArn=self.table_arn, S3Bucket='exports', ExportFormat='DYNAMODB_JSON', S3Prefix='daily')


MAX_HASH = 2 ** 128 - 1
HALF = 2 ** 127


@patch('dashboard.kinesis_api.FlociClientFactory')
class KinesisReshardingTests(SimpleTestCase):
    def shards(self, factory, *ranges):
        factory.return_value.client.return_value.list_shards.return_value = {'Shards': [
            {'ShardId': f'shardId-{index:012d}', 'HashKeyRange': {'StartingHashKey': str(low), 'EndingHashKey': str(high)},
             'SequenceNumberRange': {'StartingSequenceNumber': '1'}}
            for index, (low, high) in enumerate(ranges)]}
        return factory.return_value.client.return_value

    def test_update_shard_count(self, factory):
        kinesis = factory.return_value.client.return_value
        kinesis.update_shard_count.return_value = {'CurrentShardCount': 1, 'TargetShardCount': 2}
        response = send(self.client, 'post', reverse('dashboard:kinesis-shard-count', kwargs={'stream_name': 'clicks'}), {'target_shard_count': 2})
        self.assertEqual(response.json()['target_shard_count'], 2)
        kinesis.update_shard_count.assert_called_once_with(StreamName='clicks', TargetShardCount=2, ScalingType='UNIFORM_SCALING')

    def test_split_defaults_to_midpoint(self, factory):
        kinesis = self.shards(factory, (0, MAX_HASH))
        url = reverse('dashboard:kinesis-shard-split', kwargs={'stream_name': 'clicks', 'shard_id': 'shardId-000000000000'})
        response = send(self.client, 'post', url, {})
        self.assertEqual(response.json()['new_starting_hash_key'], str(HALF))
        kinesis.split_shard.assert_called_once_with(StreamName='clicks', ShardToSplit='shardId-000000000000', NewStartingHashKey=str(HALF))

    def test_split_key_must_be_inside_range(self, factory):
        kinesis = self.shards(factory, (0, MAX_HASH))
        url = reverse('dashboard:kinesis-shard-split', kwargs={'stream_name': 'clicks', 'shard_id': 'shardId-000000000000'})
        for key in ('0', str(MAX_HASH + 1), 'abc'):
            self.assertEqual(send(self.client, 'post', url, {'new_starting_hash_key': key}).status_code, 400, key)
        self.assertEqual(send(self.client, 'post', reverse('dashboard:kinesis-shard-split', kwargs={'stream_name': 'clicks', 'shard_id': 'shardId-9'}), {}).status_code, 400)
        kinesis.split_shard.assert_not_called()

    def test_merge_needs_adjacent_shards(self, factory):
        kinesis = self.shards(factory, (0, HALF - 1), (HALF, MAX_HASH - 10), (MAX_HASH - 9, MAX_HASH))
        url = reverse('dashboard:kinesis-shard-merge', kwargs={'stream_name': 'clicks', 'shard_id': 'shardId-000000000000'})
        response = send(self.client, 'post', url, {'adjacent_shard_id': 'shardId-000000000002'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('adjacent', response.json()['error'])
        send(self.client, 'post', url, {'adjacent_shard_id': 'shardId-000000000001'})
        kinesis.merge_shards.assert_called_once_with(StreamName='clicks', ShardToMerge='shardId-000000000000', AdjacentShardToMerge='shardId-000000000001')
