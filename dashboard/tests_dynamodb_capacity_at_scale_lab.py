"""DynamoDB at scale lab: provisioned table, capacity math, auto scaling, on-demand sessions, TTL, the filtered stream
processor, PITR, and the global table replica. The fake behaves like Floci 2.3.0: TTL deletes expired items, the
stream mapping runs the real audit function on matching records, restores are unsupported, and a replica is listed
without a table in the other Region."""

import copy
import importlib
import itertools
import json
import os
import sys
import time
import types
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.dynamodb_scale_labs')

SERVICE = 'dynamodb'
LAB = 'capacity-at-scale'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


def matches(pattern, value) -> bool:
    if isinstance(pattern, list):
        return value in pattern
    if not isinstance(value, dict):
        return False
    return all(matches(sub, value.get(key)) for key, sub in pattern.items())


class OtherRegion:
    """The replica Region: Floci lists the replica but has no table there."""

    def describe_table(self, TableName):
        raise client_error('ResourceNotFoundException')


class FakeAws:
    def __init__(self, ttl_deletes=True, run_stream=True):
        self.ids = itertools.count(1)
        self.ttl_deletes, self.run_stream = ttl_deletes, run_stream
        self.tables, self.items, self.ttl = {}, {}, {}
        self.targets, self.policies, self.alarms = {}, {}, set()
        self.roles, self.role_policies, self.functions, self.mappings = {}, {}, {}, {}
        self.calls = []

    # DynamoDB
    def describe_table(self, TableName):
        if TableName not in self.tables:
            raise client_error('ResourceNotFoundException')
        return {'Table': copy.deepcopy(self.tables[TableName])}

    def create_table(self, TableName, AttributeDefinitions, KeySchema, BillingMode='PROVISIONED',
                     ProvisionedThroughput=None, StreamSpecification=None, Tags=None):
        self.calls.append(('create_table', TableName))
        table = {'TableName': TableName, 'TableStatus': 'ACTIVE', 'KeySchema': KeySchema,
                 'BillingModeSummary': {'BillingMode': BillingMode},
                 'ProvisionedThroughput': ProvisionedThroughput or {'ReadCapacityUnits': 0, 'WriteCapacityUnits': 0}}
        self.tables[TableName] = table
        self.items[TableName] = {}
        if StreamSpecification:
            self._stream(TableName, StreamSpecification)

    def _stream(self, name, spec):
        self.tables[name]['StreamSpecification'] = spec
        if spec.get('StreamEnabled'):
            self.tables[name]['LatestStreamArn'] = f'arn:aws:dynamodb:us-east-1:000000000000:table/{name}/stream/1'

    def update_table(self, TableName, ProvisionedThroughput=None, StreamSpecification=None, BillingMode=None,
                     ReplicaUpdates=None):
        table = self.tables[TableName]
        if ProvisionedThroughput:
            table['ProvisionedThroughput'] = ProvisionedThroughput
        if StreamSpecification:
            self._stream(TableName, StreamSpecification)
        if BillingMode:
            table['BillingModeSummary'] = {'BillingMode': BillingMode}
        for update in ReplicaUpdates or []:
            table.setdefault('Replicas', []).append(
                {'RegionName': update['Create']['RegionName'], 'ReplicaStatus': 'ACTIVE'})

    def delete_table(self, TableName):
        if TableName not in self.tables:
            raise client_error('ResourceNotFoundException')
        del self.tables[TableName]
        self.items.pop(TableName, None)

    def _key(self, table, item):
        return tuple(item[key['AttributeName']]['S'] for key in self.tables[table]['KeySchema'])

    def put_item(self, TableName, Item):
        key = self._key(TableName, Item)
        old = self.items[TableName].get(key)
        self.items[TableName][key] = copy.deepcopy(Item)
        self._record(TableName, 'MODIFY' if old else 'INSERT', old, Item)

    def update_item(self, TableName, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues):
        key = self._key(TableName, Key)
        old = copy.deepcopy(self.items[TableName][key])
        name = next(iter(ExpressionAttributeNames.values()))
        value = next(iter(ExpressionAttributeValues.values()))
        self.items[TableName][key][name] = value
        self._record(TableName, 'MODIFY', old, self.items[TableName][key])

    def _record(self, table, event, old, new):
        arn = self.tables[table].get('LatestStreamArn')
        record = {'eventID': f'event-{next(self.ids)}', 'eventName': event,
                  'dynamodb': {'NewImage': copy.deepcopy(new), **({'OldImage': copy.deepcopy(old)} if old else {})}}
        for mapping in self.mappings.values():
            if not self.run_stream or mapping['EventSourceArn'] != arn:
                continue
            patterns = [json.loads(item['Pattern']) for item in mapping['FilterCriteria']['Filters']]
            if any(matches(pattern, record) for pattern in patterns):
                self._invoke(mapping['FunctionName'], {'Records': [record]})

    def _invoke(self, name, event):
        function = self.functions[name]
        namespace = {}
        fake_boto3 = types.SimpleNamespace(client=lambda service: self)
        with patch.dict(sys.modules, {'boto3': fake_boto3}), \
                patch.dict(os.environ, function['Environment']['Variables']):
            exec(compile(function['source'], 'audit.py', 'exec'), namespace)
            namespace['handler'](event, None)

    def scan(self, TableName, ProjectionExpression=None):
        ttl = self.ttl.get(TableName)
        if self.ttl_deletes and ttl:
            now = time.time()
            for key, item in list(self.items[TableName].items()):
                if float(item.get(ttl, {}).get('N', 'inf')) < now:
                    del self.items[TableName][key]
        return {'Items': copy.deepcopy(list(self.items[TableName].values()))}

    def update_time_to_live(self, TableName, TimeToLiveSpecification):
        self.ttl[TableName] = TimeToLiveSpecification['AttributeName']

    def describe_time_to_live(self, TableName):
        if TableName not in self.ttl:
            return {'TimeToLiveDescription': {'TimeToLiveStatus': 'DISABLED'}}
        return {'TimeToLiveDescription': {'TimeToLiveStatus': 'ENABLED', 'AttributeName': self.ttl[TableName]}}

    def update_continuous_backups(self, TableName, PointInTimeRecoverySpecification):
        self.tables[TableName]['pitr'] = PointInTimeRecoverySpecification

    def describe_continuous_backups(self, TableName):
        spec = self.tables[TableName].get('pitr') or {}
        description = {'PointInTimeRecoveryStatus': 'ENABLED' if spec.get('PointInTimeRecoveryEnabled') else 'DISABLED'}
        if spec.get('RecoveryPeriodInDays'):
            description['RecoveryPeriodInDays'] = spec['RecoveryPeriodInDays']
        return {'ContinuousBackupsDescription': {'ContinuousBackupsStatus': 'ENABLED',
                                                 'PointInTimeRecoveryDescription': description}}

    def restore_table_to_point_in_time(self, **kwargs):
        raise client_error('UnknownOperationException', 'Unknown operation')

    # Application Auto Scaling and CloudWatch
    def register_scalable_target(self, ServiceNamespace, ResourceId, ScalableDimension, MinCapacity, MaxCapacity):
        self.targets[ScalableDimension] = {'ResourceId': ResourceId, 'ScalableDimension': ScalableDimension,
                                           'MinCapacity': MinCapacity, 'MaxCapacity': MaxCapacity}

    def put_scaling_policy(self, PolicyName, ServiceNamespace, ResourceId, ScalableDimension, PolicyType,
                           TargetTrackingScalingPolicyConfiguration):
        self.policies[ScalableDimension] = {'PolicyName': PolicyName, 'ScalableDimension': ScalableDimension,
                                            'TargetTrackingScalingPolicyConfiguration':
                                                TargetTrackingScalingPolicyConfiguration}
        for kind in ('AlarmHigh', 'AlarmLow'):
            self.alarms.add(f'TargetTracking-{ResourceId}-{kind}-{PolicyName}')

    def describe_scalable_targets(self, ServiceNamespace, ResourceIds):
        return {'ScalableTargets': copy.deepcopy(list(self.targets.values()))}

    def describe_scaling_policies(self, ServiceNamespace, ResourceId):
        return {'ScalingPolicies': copy.deepcopy(list(self.policies.values()))}

    def delete_scaling_policy(self, PolicyName, ServiceNamespace, ResourceId, ScalableDimension):
        if ScalableDimension not in self.policies:
            raise client_error('ObjectNotFoundException')
        del self.policies[ScalableDimension]

    def deregister_scalable_target(self, ServiceNamespace, ResourceId, ScalableDimension):
        if ScalableDimension not in self.targets:
            raise client_error('ObjectNotFoundException')
        del self.targets[ScalableDimension]

    def describe_alarms(self, AlarmNamePrefix, NextToken=None):
        return {'MetricAlarms': [{'AlarmName': name} for name in sorted(self.alarms) if name.startswith(AlarmNamePrefix)]}

    def delete_alarms(self, AlarmNames):
        self.alarms -= set(AlarmNames)

    # IAM
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': self.roles[RoleName]}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description):
        self.roles[RoleName] = {'RoleName': RoleName}

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.role_policies[(RoleName, PolicyName)] = json.loads(PolicyDocument)

    def get_role_policy(self, RoleName, PolicyName):
        if (RoleName, PolicyName) not in self.role_policies:
            raise client_error('NoSuchEntity')
        return {'PolicyDocument': copy.deepcopy(self.role_policies[(RoleName, PolicyName)])}

    def delete_role_policy(self, RoleName, PolicyName):
        if self.role_policies.pop((RoleName, PolicyName), None) is None:
            raise client_error('NoSuchEntity')

    def delete_role(self, RoleName):
        if self.roles.pop(RoleName, None) is None:
            raise client_error('NoSuchEntity')

    # Lambda
    def _source(self, archive):
        import io
        import zipfile
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            return handle.read('audit.py').decode()

    def get_function_configuration(self, FunctionName):
        if FunctionName not in self.functions:
            raise client_error('ResourceNotFoundException')
        return {key: value for key, value in self.functions[FunctionName].items() if key != 'source'}

    def create_function(self, FunctionName, Code, Description, **settings):
        self.calls.append(('create_function', FunctionName))
        self.functions[FunctionName] = {'FunctionName': FunctionName, 'State': 'Active', 'LastUpdateStatus': 'Successful',
                                        'source': self._source(Code['ZipFile']), **settings}

    def update_function_code(self, FunctionName, ZipFile):
        self.functions[FunctionName]['source'] = self._source(ZipFile)

    def update_function_configuration(self, FunctionName, **settings):
        self.functions[FunctionName].update(settings)

    def delete_function(self, FunctionName):
        if self.functions.pop(FunctionName, None) is None:
            raise client_error('ResourceNotFoundException')

    def list_event_source_mappings(self, FunctionName, EventSourceArn):
        return {'EventSourceMappings': [copy.deepcopy(mapping) for mapping in self.mappings.values()
                                        if mapping['FunctionName'] == FunctionName
                                        and mapping['EventSourceArn'] == EventSourceArn]}

    def create_event_source_mapping(self, FunctionName, EventSourceArn, StartingPosition, **settings):
        self.calls.append(('create_event_source_mapping', FunctionName))
        uuid = f'mapping-{next(self.ids)}'
        self.mappings[uuid] = {'UUID': uuid, 'FunctionName': FunctionName, 'EventSourceArn': EventSourceArn,
                               'StartingPosition': StartingPosition, 'State': 'Enabled', **settings}

    def update_event_source_mapping(self, UUID, Enabled, **settings):
        self.mappings[UUID].update(settings)

    def delete_event_source_mapping(self, UUID):
        if self.mappings.pop(UUID, None) is None:
            raise client_error('ResourceNotFoundException')


class DynamoDbCapacityAtScaleLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        self.other_region = OtherRegion()
        patcher = patch('dashboard.labs.dynamodb_scale_labs._client',
                        side_effect=lambda name, region=None: self.other_region if region == lab.REPLICA_REGION
                        else self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.dynamodb_scale_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def test_lab_is_registered_with_eight_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 8)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '4.3'), tasks)
        self.assertIn(('CLF-C02', '3.4'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_capacity_math(self):
        self.assertEqual(lab.REQUIRED_WCU, 120)
        self.assertEqual(lab.REQUIRED_RCU, 240)

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_steps_say_what_floci_does_not_enforce(self):
        results = self.run_all()
        self.assertIn('never throttles', results['size-capacity']['verification']['message'])
        self.assertIn('4 CloudWatch alarm(s)', results['auto-scaling']['verification']['message'])
        self.assertIn('UnknownOperationException', results['point-in-time-recovery']['verification']['message'])
        self.assertIn('no table in us-west-2', results['global-table-replica']['verification']['message'])
        self.assertFalse(results['global-table-replica']['json']['TableInReplicaRegion'])

    def test_stream_audits_only_the_shipped_order(self):
        results = self.run_all()
        audit = list(self.fake.items[lab.AUDIT].values())
        self.assertEqual(len(audit), 1)
        self.assertEqual(audit[0]['order_id']['S'], results['stream-processor']['json']['ShippedOrder'])
        self.assertEqual((audit[0]['from_status']['S'], audit[0]['to_status']['S']), ('PENDING', 'SHIPPED'))

    def test_no_filter_audits_every_update_and_fails(self):
        for step in lab.STEP_KEYS[:5]:
            run_lab_step(SERVICE, LAB, step)
        with patch.object(lab, 'SHIPPED_FILTER', {'eventName': ['INSERT', 'MODIFY']}):
            result = run_lab_step(SERVICE, LAB, 'stream-processor')
        self.assertFalse(result['verified'])
        self.assertIn('Expected one audit item', result['verification']['message'])

    def test_a_stream_that_never_delivers_fails(self):
        self.fake.run_stream = False
        for step in lab.STEP_KEYS[:5]:
            run_lab_step(SERVICE, LAB, step)
        with patch.object(lab, 'STREAM_TIMEOUT_SECONDS', 0):
            result = run_lab_step(SERVICE, LAB, 'stream-processor')
        self.assertFalse(result['verified'])
        self.assertIn('has nothing', result['verification']['message'])

    def test_ttl_that_never_deletes_fails_after_the_timeout(self):
        self.fake.ttl_deletes = False
        for step in lab.STEP_KEYS[:4]:
            run_lab_step(SERVICE, LAB, step)
        with patch.object(lab, 'TTL_TIMEOUT_SECONDS', 0):
            result = run_lab_step(SERVICE, LAB, 'ttl-expiry')
        self.assertFalse(result['verified'])
        self.assertIn('still there', result['verification']['message'])

    def test_role_writing_everywhere_fails(self):
        self.run_all()
        policy = self.fake.role_policies[(lab.ROLE_NAME, lab.ROLE_POLICY)]
        policy['Statement'][1]['Resource'] = '*'
        self.assertIn('may write only', lab._check_stream_setup()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'stream-processor')['verified'])

    def test_under_provisioned_table_fails_and_is_resized(self):
        self.run_all()
        self.fake.tables[lab.ORDERS]['ProvisionedThroughput'] = {'ReadCapacityUnits': 5, 'WriteCapacityUnits': 5}
        self.assertIn('below', lab._check_capacity()[1])
        self.assertEqual(self.failed(), ['size-capacity'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'size-capacity')['verified'])

    def test_wrong_scaling_target_fails(self):
        self.run_all()
        self.fake.targets['dynamodb:table:WriteCapacityUnits']['MaxCapacity'] = 1000
        self.assertIn('should scale between', lab._check_scaling()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'auto-scaling')['verified'])

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'size-capacity')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'auto-scaling')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'ttl-expiry')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'stream-processor')['verification']['message'])
        run_lab_step(SERVICE, LAB, 'provisioned-table')
        self.assertIn('auto scaling first', run_lab_step(SERVICE, LAB, 'global-table-replica')['verification']['message'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual([call for call in self.fake.calls if call[0] != 'create_table'],
                         [('create_function', lab.FUNCTION), ('create_event_source_mapping', lab.FUNCTION)])
        self.assertEqual(len(self.fake.tables[lab.ORDERS]['Replicas']), 1)

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in (self.fake.tables, self.fake.targets, self.fake.policies, self.fake.alarms, self.fake.roles,
                      self.fake.role_policies, self.fake.functions, self.fake.mappings):
            self.assertFalse(store)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
