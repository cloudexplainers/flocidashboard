"""Explicit live-Floci checks: python manage.py test dashboard.integration_tests.

These checks use the configured local emulator. Each write test creates a unique
resource and registers cleanup immediately; existing resources are never removed.
Kept outside tests*.py so the default unit suite needs no running emulator.
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor

from django.test import Client, SimpleTestCase

from .aws import FlociClientFactory
from .services import SERVICES


# Error codes Floci returns for an operation it doesn't implement. Inventories report
# those lists under load_errors (and every console names them), so they are capability
# gaps to keep visible, not failures. An empty code is a bare 404 (Route 53).
UNIMPLEMENTED_CODES = {'UnsupportedOperation', 'UnknownOperationException', 'InvalidAction', '404', '405', ''}


class FlociCompatibilityTests(SimpleTestCase):
    def assert_load_errors(self, entries, service):
        for entry in entries or []:
            self.assertIn(entry.get('code'), UNIMPLEMENTED_CODES, f"{service}: {entry.get('operation')}: {entry.get('error')}")

    def assert_inventory_errors(self, data, service):
        if isinstance(data, dict):
            for key, value in data.items():
                if key == 'load_errors':
                    self.assert_load_errors(value, service)
                elif key == 'error' and value:
                    # Floci explicitly rejects Cloud Control types it cannot list.
                    # Keep those capability gaps visible; fail on other errors.
                    self.assertTrue(
                        service == 'cloudcontrol' and 'UnsupportedActionException' in str(value),
                        f'{service}: {value}',
                    )
                else:
                    self.assert_inventory_errors(value, service)
        elif isinstance(data, list):
            for value in data:
                self.assert_inventory_errors(value, service)

    def setUp(self):
        self.factory = FlociClientFactory()
        self.name = f'dashboard-smoke-{uuid.uuid4().hex[:16]}'
        health = self.factory.health()
        self.assertTrue(health['ok'], health)

    def test_all_registered_inventories(self):
        def inspect(service):
            response = Client().get(service.as_dict()['api_path'])
            return service.key, response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=6) as executor:
            for key, status, data in executor.map(inspect, SERVICES):
                with self.subTest(service=key):
                    self.assertEqual(status, 200, data)
                    self.assertNotIn('error', data, data)
                    self.assertIn('summary', data)
                    self.assert_inventory_errors(data, key)

    def test_s3_object_round_trip(self):
        s3 = self.factory.client('s3')
        options = {'Bucket': self.name}
        if self.factory.region != 'us-east-1':
            options['CreateBucketConfiguration'] = {'LocationConstraint': self.factory.region}
        s3.create_bucket(**options)
        self.addCleanup(s3.delete_bucket, Bucket=self.name)
        s3.put_object(Bucket=self.name, Key='smoke.txt', Body=b'Floci compatibility')
        self.addCleanup(s3.delete_object, Bucket=self.name, Key='smoke.txt')
        response = s3.get_object(Bucket=self.name, Key='smoke.txt')
        with response['Body'] as body:
            self.assertEqual(body.read(), b'Floci compatibility')
        self.assertEqual(s3.list_objects_v2(Bucket=self.name)['KeyCount'], 1)

    def test_sqs_message_round_trip(self):
        sqs = self.factory.client('sqs')
        queue = sqs.create_queue(QueueName=self.name)['QueueUrl']
        self.addCleanup(sqs.delete_queue, QueueUrl=queue)
        sqs.send_message(QueueUrl=queue, MessageBody=self.name)
        messages = sqs.receive_message(QueueUrl=queue, WaitTimeSeconds=1)['Messages']
        self.assertEqual(messages[0]['Body'], self.name)
        sqs.delete_message(QueueUrl=queue, ReceiptHandle=messages[0]['ReceiptHandle'])

    def test_dynamodb_item_round_trip(self):
        dynamodb = self.factory.client('dynamodb')
        dynamodb.create_table(
            TableName=self.name,
            KeySchema=[{'AttributeName': 'id', 'KeyType': 'HASH'}],
            AttributeDefinitions=[{'AttributeName': 'id', 'AttributeType': 'S'}],
            BillingMode='PAY_PER_REQUEST',
        )
        self.addCleanup(dynamodb.delete_table, TableName=self.name)
        dynamodb.get_waiter('table_exists').wait(
            TableName=self.name, WaiterConfig={'Delay': 1, 'MaxAttempts': 15},
        )
        item = {'id': {'S': self.name}, 'value': {'N': '42'}}
        dynamodb.put_item(TableName=self.name, Item=item)
        response = dynamodb.get_item(
            TableName=self.name, Key={'id': {'S': self.name}}, ConsistentRead=True,
        )
        self.assertEqual(response['Item'], item)

    def test_ssm_secure_parameter_round_trip(self):
        ssm = self.factory.client('ssm')
        name = f'/{self.name}/secret'
        ssm.put_parameter(Name=name, Value='smoke-value', Type='SecureString')
        self.addCleanup(ssm.delete_parameter, Name=name)
        response = ssm.get_parameter(Name=name, WithDecryption=True)
        self.assertEqual(response['Parameter']['Value'], 'smoke-value')

    def test_iam_role_and_sts_session(self):
        iam = self.factory.client('iam')
        trust = {
            'Version': '2012-10-17',
            'Statement': [{'Effect': 'Allow', 'Principal': {'AWS': '*'}, 'Action': 'sts:AssumeRole'}],
        }
        role = iam.create_role(
            RoleName=self.name, AssumeRolePolicyDocument=json.dumps(trust),
        )['Role']
        self.addCleanup(iam.delete_role, RoleName=self.name)
        session = self.factory.client('sts').assume_role(
            RoleArn=role['Arn'], RoleSessionName=self.name,
        )
        credentials = session['Credentials']
        assumed = FlociClientFactory(identity={
            'access_key_id': credentials['AccessKeyId'],
            'secret_access_key': credentials['SecretAccessKey'],
            'session_token': credentials['SessionToken'],
        })
        self.assertIn(f'assumed-role/{self.name}/', assumed.identity()['arn'])


class ConsoleFeatureLiveTests(SimpleTestCase):
    """Drives the console endpoints for features added from the accuracy review against Floci."""

    def setUp(self):
        self.factory = FlociClientFactory()
        self.assertTrue(self.factory.health()['ok'])
        self.name = f'console-{uuid.uuid4().hex[:10]}'
        self.http = Client()

    def call(self, method, path, body=None, expect=200):
        kwargs = {} if body is None else {'data': json.dumps(body), 'content_type': 'application/json'}
        response = getattr(self.http, method)(path, **kwargs)
        self.assertEqual(response.status_code, expect, response.content)
        return response.json()

    def test_eventbridge_archive_and_replay(self):
        from datetime import datetime, timedelta, timezone
        events = self.factory.client('events')
        self.addCleanup(lambda: events.delete_archive(ArchiveName=self.name))
        self.call('post', '/api/eventbridge/archives/create/', {
            'name': self.name, 'event_bus_name': 'default', 'event_pattern': {'source': [self.name]}})
        events.put_events(Entries=[{'Source': self.name, 'DetailType': 'Test', 'Detail': '{}'}])
        archives = self.call('get', '/api/eventbridge/archives/')['archives']
        archive = next(item for item in archives if item['name'] == self.name)
        self.assertEqual(archive['event_count'], 1)
        now = datetime.now(timezone.utc)
        replay = self.call('post', '/api/eventbridge/replays/start/', {
            'name': self.name, 'archive_name': self.name,
            'start_time': (now - timedelta(hours=1)).isoformat(), 'end_time': now.isoformat()})
        self.assertIn(replay['state'], {'STARTING', 'RUNNING', 'COMPLETED'})

    def test_cloudwatch_dashboard_round_trip(self):
        body = {'widgets': [{'type': 'text', 'x': 0, 'y': 0, 'width': 6, 'height': 2, 'properties': {'markdown': 'hi'}}]}
        self.call('put', '/api/cloudwatch/dashboards/', {'dashboard_name': self.name, 'dashboard_body': body})
        listed = self.call('get', '/api/cloudwatch/dashboards/')['dashboards']
        saved = next(item for item in listed if item['name'] == self.name)
        self.assertEqual(json.loads(saved['body']), body)
        self.call('delete', '/api/cloudwatch/dashboards/', {'dashboard_name': self.name})

    def test_logs_subscription_filter_round_trip(self):
        logs, kinesis = self.factory.client('logs'), self.factory.client('kinesis')
        group = f'/console/{self.name}'
        logs.create_log_group(logGroupName=group)
        self.addCleanup(lambda: logs.delete_log_group(logGroupName=group))
        kinesis.create_stream(StreamName=self.name, ShardCount=1)
        self.addCleanup(lambda: kinesis.delete_stream(StreamName=self.name))
        arn = kinesis.describe_stream_summary(StreamName=self.name)['StreamDescriptionSummary']['StreamARN']
        self.call('put', '/api/cloudwatch/subscription-filters/', {
            'log_group_name': group, 'filter_name': 'errors', 'filter_pattern': 'ERROR', 'destination_arn': arn})
        listed = self.call('get', f'/api/cloudwatch/subscription-filters/?log_group_name={group}')
        self.assertEqual([item['filter_name'] for item in listed['subscription_filters']], ['errors'])
        self.call('delete', '/api/cloudwatch/subscription-filters/', {'log_group_name': group, 'filter_name': 'errors'})
