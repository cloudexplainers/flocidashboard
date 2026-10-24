"""Streaming data lake lab: buckets, Kinesis stream, Firehose delivery, landing, Glue table, Athena workgroup, query,
and the Parquet copy. The fake delivers stream records to S3 the way Floci's Firehose does and answers the lab's
Athena queries by scanning the fake lake."""

import copy
import importlib
import io
import itertools
import json
import re
from datetime import datetime, timezone
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.kinesis_labs')

SERVICE = 'kinesis'
LAB = 'streaming-data-lake'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeAws:
    def __init__(self, deliver=True):
        self.ids = itertools.count(1)
        self.deliver = deliver
        self.buckets, self.bucket_config = {}, {}
        self.streams, self.delivery_streams = {}, {}
        self.roles, self.databases, self.tables, self.workgroups, self.queries = {}, {}, {}, {}, {}
        self.calls = []

    def client(self, name):
        return self

    # S3
    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('404')

    def create_bucket(self, Bucket):
        self.buckets[Bucket] = {}
        self.bucket_config[Bucket] = {}

    def _config(self, Bucket, key, value=None, missing='NoSuchConfiguration'):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        if value is not None:
            self.bucket_config[Bucket][key] = copy.deepcopy(value)
            return {}
        if key not in self.bucket_config[Bucket]:
            raise client_error(missing)
        return copy.deepcopy(self.bucket_config[Bucket][key])

    def put_public_access_block(self, Bucket, PublicAccessBlockConfiguration):
        self._config(Bucket, 'pab', PublicAccessBlockConfiguration)

    def get_public_access_block(self, Bucket):
        return {'PublicAccessBlockConfiguration': self._config(Bucket, 'pab', missing='NoSuchPublicAccessBlockConfiguration')}

    def put_bucket_encryption(self, Bucket, ServerSideEncryptionConfiguration):
        self._config(Bucket, 'sse', ServerSideEncryptionConfiguration)

    def get_bucket_encryption(self, Bucket):
        return {'ServerSideEncryptionConfiguration': self._config(Bucket, 'sse', missing='ServerSideEncryptionConfigurationNotFoundError')}

    def put_bucket_lifecycle_configuration(self, Bucket, LifecycleConfiguration):
        self._config(Bucket, 'lifecycle', LifecycleConfiguration['Rules'])

    def get_bucket_lifecycle_configuration(self, Bucket):
        return {'Rules': self._config(Bucket, 'lifecycle', missing='NoSuchLifecycleConfiguration')}

    def put_object(self, Bucket, Key, Body):
        self.buckets[Bucket][Key] = Body if isinstance(Body, bytes) else Body.encode()

    def list_objects_v2(self, Bucket, Prefix='', ContinuationToken=None):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        return {'Contents': [{'Key': key, 'Size': len(body)} for key, body in sorted(self.buckets[Bucket].items())
                             if key.startswith(Prefix)], 'IsTruncated': False}

    def get_object(self, Bucket, Key):
        return {'Body': io.BytesIO(self.buckets[Bucket][Key])}

    def delete_objects(self, Bucket, Delete):
        for item in Delete['Objects']:
            self.buckets[Bucket].pop(item['Key'], None)

    def delete_bucket(self, Bucket):
        if self.buckets.get(Bucket):
            raise client_error('BucketNotEmpty')
        self.buckets.pop(Bucket)
        self.bucket_config.pop(Bucket)

    # Kinesis
    def _stream(self, StreamName):
        if StreamName not in self.streams:
            raise client_error('ResourceNotFoundException')
        return self.streams[StreamName]

    def create_stream(self, StreamName, StreamModeDetails):
        self.streams[StreamName] = {'StreamName': StreamName, 'StreamARN': lab.STREAM_ARN, 'StreamStatus': 'ACTIVE',
                                    'StreamModeDetails': dict(StreamModeDetails), 'RetentionPeriodHours': 24,
                                    'OpenShardCount': 1, 'records': []}

    def describe_stream_summary(self, StreamName):
        stream = self._stream(StreamName)
        return {'StreamDescriptionSummary': {k: copy.deepcopy(v) for k, v in stream.items() if k != 'records'}}

    def update_stream_mode(self, StreamARN, StreamModeDetails):
        next(s for s in self.streams.values() if s['StreamARN'] == StreamARN)['StreamModeDetails'] = dict(StreamModeDetails)

    def increase_stream_retention_period(self, StreamName, RetentionPeriodHours):
        self._stream(StreamName)['RetentionPeriodHours'] = RetentionPeriodHours

    def decrease_stream_retention_period(self, StreamName, RetentionPeriodHours):
        self._stream(StreamName)['RetentionPeriodHours'] = RetentionPeriodHours

    def put_records(self, StreamName, Records):
        stream = self._stream(StreamName)
        stream['records'].extend(record['Data'] for record in Records)
        delivery = next((d for d in self.delivery_streams.values()
                         if d['Source']['KinesisStreamSourceDescription']['KinesisStreamARN'] == stream['StreamARN']), None)
        if delivery and self.deliver:  # like Floci, Firehose flushes to the evaluated prefix
            destination = delivery['Destinations'][0]['ExtendedS3DestinationDescription']
            prefix = destination['Prefix'].replace('!{timestamp:yyyy-MM-dd}',
                                                   datetime.now(timezone.utc).strftime('%Y-%m-%d'))
            bucket = destination['BucketARN'].rsplit(':', 1)[-1]
            self.put_object(bucket, f'{prefix}{lab.DELIVERY_STREAM}-1-{next(self.ids)}', b''.join(
                record['Data'] for record in Records))
        return {'FailedRecordCount': 0, 'Records': [{'ShardId': 'shardId-000000000000', 'SequenceNumber': str(i)}
                                                    for i, _ in enumerate(Records)]}

    def list_shards(self, StreamName):
        self._stream(StreamName)
        return {'Shards': [{'ShardId': 'shardId-000000000000'}]}

    def get_shard_iterator(self, StreamName, ShardId, ShardIteratorType):
        return {'ShardIterator': f'{StreamName}|0'}

    def get_records(self, ShardIterator, Limit):
        name = ShardIterator.split('|')[0]
        return {'Records': [{'Data': data} for data in self._stream(name)['records']], 'MillisBehindLatest': 0}

    def delete_stream(self, StreamName, EnforceConsumerDeletion=False):
        self._stream(StreamName)
        del self.streams[StreamName]

    # Firehose
    def describe_delivery_stream(self, DeliveryStreamName):
        if DeliveryStreamName not in self.delivery_streams:
            raise client_error('ResourceNotFoundException')
        return {'DeliveryStreamDescription': copy.deepcopy(self.delivery_streams[DeliveryStreamName])}

    def create_delivery_stream(self, DeliveryStreamName, DeliveryStreamType, KinesisStreamSourceConfiguration,
                               ExtendedS3DestinationConfiguration, Tags=None):
        self.calls.append(('create_delivery_stream', DeliveryStreamName))
        self.delivery_streams[DeliveryStreamName] = {
            'DeliveryStreamName': DeliveryStreamName, 'DeliveryStreamStatus': 'ACTIVE',
            'DeliveryStreamType': DeliveryStreamType,
            'Source': {'KinesisStreamSourceDescription': dict(KinesisStreamSourceConfiguration)},
            'Destinations': [{'ExtendedS3DestinationDescription': copy.deepcopy(ExtendedS3DestinationConfiguration)}],
        }

    def delete_delivery_stream(self, DeliveryStreamName):
        if DeliveryStreamName not in self.delivery_streams:
            raise client_error('ResourceNotFoundException')
        del self.delivery_streams[DeliveryStreamName]

    # IAM
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': {'RoleName': RoleName}}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description=''):
        self.roles[RoleName] = {}

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.roles[RoleName][PolicyName] = PolicyDocument

    def get_role_policy(self, RoleName, PolicyName):
        if PolicyName not in self.roles.get(RoleName, {}):
            raise client_error('NoSuchEntity')
        return {'PolicyDocument': json.loads(self.roles[RoleName][PolicyName])}

    def delete_role_policy(self, RoleName, PolicyName):
        if PolicyName not in self.roles.get(RoleName, {}):
            raise client_error('NoSuchEntity')
        del self.roles[RoleName][PolicyName]

    def delete_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        del self.roles[RoleName]

    # Glue
    def get_database(self, Name):
        if Name not in self.databases:
            raise client_error('EntityNotFoundException')
        return {'Database': {'Name': Name}}

    def create_database(self, DatabaseInput):
        self.databases[DatabaseInput['Name']] = True

    def delete_database(self, Name):
        if Name not in self.databases:
            raise client_error('EntityNotFoundException')
        del self.databases[Name]

    def get_table(self, DatabaseName, Name):
        if (DatabaseName, Name) not in self.tables:
            raise client_error('EntityNotFoundException')
        return {'Table': copy.deepcopy(self.tables[(DatabaseName, Name)])}

    def create_table(self, DatabaseName, TableInput):
        self.tables[(DatabaseName, TableInput['Name'])] = copy.deepcopy(TableInput)

    update_table = create_table

    def delete_table(self, DatabaseName, Name):
        if (DatabaseName, Name) not in self.tables:
            raise client_error('EntityNotFoundException')
        del self.tables[(DatabaseName, Name)]

    # Athena (results go to Floci's own bucket, no bytes scanned)
    def get_work_group(self, WorkGroup):
        if WorkGroup not in self.workgroups:
            raise client_error('InvalidRequestException')
        return {'WorkGroup': copy.deepcopy(self.workgroups[WorkGroup])}

    def create_work_group(self, Name, Configuration, Description='', Tags=None):
        self.workgroups[Name] = {'Name': Name, 'State': 'ENABLED', 'Configuration': copy.deepcopy(Configuration)}

    def update_work_group(self, WorkGroup, ConfigurationUpdates):
        config = self.workgroups[WorkGroup]['Configuration']
        config['ResultConfiguration'] = ConfigurationUpdates['ResultConfigurationUpdates']
        for key in ('EnforceWorkGroupConfiguration', 'PublishCloudWatchMetricsEnabled', 'BytesScannedCutoffPerQuery'):
            config[key] = ConfigurationUpdates[key]

    def delete_work_group(self, WorkGroup, RecursiveDeleteOption=False):
        if WorkGroup not in self.workgroups:
            raise client_error('InvalidRequestException')
        del self.workgroups[WorkGroup]

    def _events(self, prefix):
        for key, body in self.buckets.get(lab.LAKE_BUCKET, {}).items():
            if key.startswith(prefix) and not key.endswith('.parquet'):
                dt = key[len(prefix):].split('/', 1)[0].removeprefix('dt=')
                for line in body.decode().splitlines():
                    if line.strip():
                        yield {**json.loads(line), 'dt': dt}

    def start_query_execution(self, QueryString, QueryExecutionContext, WorkGroup=None, ResultConfiguration=None):
        query_id = f'q-{next(self.ids)}'
        self.calls.append(('query', QueryString))
        rows, state, reason = [], 'SUCCEEDED', None
        if QueryString.startswith('COPY'):
            events = list(self._events(lab.RAW_PREFIX))
            for dt in {event['dt'] for event in events}:
                body = '\n'.join(json.dumps(e) for e in events if e['dt'] == dt).encode()
                self.put_object(lab.LAKE_BUCKET, f'{lab.CURATED_PREFIX}dt={dt}/data_0.parquet', body[: len(body) // 3])
                self._parquet = events
        elif QueryString.startswith('SELECT page'):
            table = re.search(r'FROM (\w+)', QueryString).group(1)
            batch = re.search(r"batch_id = '([^']+)'", QueryString).group(1)
            dt = re.search(r"dt = '([^']+)'", QueryString)
            if (lab.DATABASE, table) not in self.tables:
                state, reason = 'FAILED', f'Table {table} does not exist'
            else:
                events = self._parquet if table == lab.CURATED_TABLE else list(self._events(lab.RAW_PREFIX))
                counts = {}
                for event in events:
                    if event['batch_id'] == batch and (not dt or event['dt'] == dt.group(1)):
                        counts[event['page']] = counts.get(event['page'], 0) + 1
                rows = [{'Data': [{'VarCharValue': 'page'}, {'VarCharValue': 'views'}]}] + [
                    {'Data': [{'VarCharValue': page}, {'VarCharValue': str(count)}]} for page, count in sorted(counts.items())]
        self.queries[query_id] = {'state': state, 'reason': reason, 'rows': rows}
        return {'QueryExecutionId': query_id}

    def get_query_execution(self, QueryExecutionId):
        query = self.queries[QueryExecutionId]
        return {'QueryExecution': {'Status': {'State': query['state'], 'StateChangeReason': query['reason']},
                                   'ResultConfiguration': {'OutputLocation': f's3://floci-athena-results/results/{QueryExecutionId}.csv'},
                                   'Statistics': {'DataScannedInBytes': 0}}}

    def get_query_results(self, QueryExecutionId):
        return {'ResultSet': {'Rows': self.queries[QueryExecutionId]['rows']}}


class KinesisDataLakeLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        patcher = patch('dashboard.labs.kinesis_labs._client', side_effect=lambda name: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.kinesis_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def test_lab_is_registered_with_nine_steps_exam_tags_and_a_batch(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 9)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '3.5'), tasks)
        self.assertIn(('CLF-C02', '3.7'), tasks)
        self.assertIn(SERVICE, [batch['service'] for batch in LAB_BATCH_ORDER])

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 9)

    def test_queries_count_the_batch_and_note_what_floci_skips(self):
        results = self.run_all()
        self.assertEqual(results['query-clicks']['json']['PageViews'], dict(lab.PAGES))
        message = results['query-clicks']['verification']['message']
        self.assertIn('dt=', message)
        self.assertIn('floci-athena-results', message)
        self.assertIn('0 bytes scanned', message)
        self.assertEqual(results['convert-parquet']['json']['PageViews'], dict(lab.PAGES))
        self.assertTrue(any(key.endswith('.parquet') for key in self.fake.buckets[lab.LAKE_BUCKET]))
        self.assertIn(('query', lab.FLOCI_PARQUET_SQL), self.fake.calls)
        self.assertIn((lab.DATABASE, lab.CURATED_TABLE), self.fake.tables)

    def test_a_new_batch_needs_landing_and_queries_again(self):
        self.run_all()
        run_lab_step(SERVICE, LAB, 'produce-events')
        self.assertEqual(self.failed(), ['land-in-s3', 'query-clicks', 'convert-parquet'])
        for step in ('land-in-s3', 'query-clicks', 'convert-parquet'):
            self.assertTrue(run_lab_step(SERVICE, LAB, step)['verified'], step)

    def test_events_that_never_land_fail_after_the_timeout(self):
        self.fake.deliver = False
        for step in lab.STEP_KEYS[:4]:
            run_lab_step(SERVICE, LAB, step)
        with patch.object(lab, 'LANDING_TIMEOUT_SECONDS', 0):
            result = run_lab_step(SERVICE, LAB, 'land-in-s3')
        self.assertFalse(result['verified'])
        self.assertIn('0 of 24', result['verification']['message'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual([call for call in self.fake.calls if call[0] == 'create_delivery_stream'],
                         [('create_delivery_stream', lab.DELIVERY_STREAM)])
        self.assertEqual(len(self.fake.streams), 1)

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'delivery-stream')['verification']['message'])
        self.assertIn('Put events', run_lab_step(SERVICE, LAB, 'land-in-s3')['verification']['message'])
        self.assertIn('Land this batch', run_lab_step(SERVICE, LAB, 'query-clicks')['verification']['message'])

    def test_missing_error_prefix_fails_and_the_step_recreates_the_stream(self):
        self.run_all()
        destination = self.fake.delivery_streams[lab.DELIVERY_STREAM]['Destinations'][0]['ExtendedS3DestinationDescription']
        destination.pop('ErrorOutputPrefix')
        self.assertIn('error output prefix', lab._check_delivery()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'delivery-stream')['verified'])
        self.assertEqual(len([c for c in self.fake.calls if c[0] == 'create_delivery_stream']), 2)

    def test_provisioned_stream_and_short_retention_are_fixed(self):
        self.run_all()
        self.fake.streams[lab.STREAM]['StreamModeDetails'] = {'StreamMode': 'PROVISIONED'}
        self.assertIn('on-demand', lab._check_stream()[1])
        self.fake.streams[lab.STREAM]['RetentionPeriodHours'] = 24
        self.assertTrue(run_lab_step(SERVICE, LAB, 'data-stream')['verified'])
        self.assertEqual(self.fake.streams[lab.STREAM]['RetentionPeriodHours'], lab.RETENTION_HOURS)

    def test_table_without_projection_and_workgroup_without_limit_fail(self):
        self.run_all()
        self.fake.tables[(lab.DATABASE, lab.RAW_TABLE)]['Parameters'] = {'classification': 'json'}
        self.assertIn('projection', lab._check_catalog()[1])
        self.fake.workgroups[lab.WORKGROUP]['Configuration'].pop('BytesScannedCutoffPerQuery')
        self.assertIn('scan limit', lab._check_workgroup()[1])
        self.assertEqual(self.failed(), ['glue-catalog', 'athena-workgroup'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'glue-catalog')['verified'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'athena-workgroup')['verified'])

    def test_public_lake_bucket_fails(self):
        self.run_all()
        self.fake.bucket_config[lab.LAKE_BUCKET]['pab']['BlockPublicPolicy'] = False
        self.assertEqual(self.failed(), ['lake-buckets'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'lake-buckets')['verified'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in (self.fake.buckets, self.fake.streams, self.fake.delivery_streams, self.fake.roles,
                      self.fake.databases, self.fake.tables, self.fake.workgroups):
            self.assertEqual(store, {})
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
