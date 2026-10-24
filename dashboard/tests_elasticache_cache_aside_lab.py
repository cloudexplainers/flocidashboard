"""ElastiCache cache-aside lab: network, eviction policy, replication group, Aurora source, Lambda reader, miss, hit,
invalidation. The fake Lambda runs the lab's real reader code against a fake Redis server and a fake Data API."""

import copy
import importlib
import io
import itertools
import json
import re
import types
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.elasticache_labs')

SERVICE = 'elasticache'
LAB = 'cache-aside'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeRedis:
    """A Redis server that speaks just enough RESP for the reader. Like Floci's Valkey container, it keeps
    maxmemory-policy noeviction whatever the parameter group says."""

    def __init__(self):
        self.data, self.ttl = {}, {}

    def execute(self, args):
        command = args[0].upper()
        if command == 'PING':
            return b'+PONG\r\n'
        if command == 'GET':
            value = self.data.get(args[1])
            return b'$-1\r\n' if value is None else b'$%d\r\n%s\r\n' % (len(value.encode()), value.encode())
        if command == 'SET':
            self.data[args[1]] = args[2]
            self.ttl[args[1]] = int(args[4]) if len(args) > 4 and args[3].upper() == 'EX' else -1
            return b'+OK\r\n'
        if command == 'TTL':
            return b':%d\r\n' % (self.ttl.get(args[1], -2) if args[1] in self.data else -2)
        if command == 'DEL':
            removed = int(self.data.pop(args[1], None) is not None)
            self.ttl.pop(args[1], None)
            return b':%d\r\n' % removed
        if command == 'CONFIG':
            return b'*2\r\n$16\r\nmaxmemory-policy\r\n$10\r\nnoeviction\r\n'
        return b'-ERR unknown command\r\n'


class FakeConnection:
    def __init__(self, server):
        self.server, self.reply = server, b''

    def sendall(self, payload):
        parts = payload.split(b'\r\n')
        count = int(parts[0][1:])
        args = [parts[2 + 2 * i].decode() for i in range(count)]
        self.reply = self.server.execute(args)

    def makefile(self, mode):
        return io.BytesIO(self.reply)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeAws:
    """EC2, ElastiCache, RDS, the Data API, IAM, and Lambda, as Floci 2.3.0 stores them: no AZs or security groups on
    cache nodes, parameter groups not applied, Multi-AZ with one node accepted."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.vpcs, self.subnets, self.groups = {}, {}, {}
        self.subnet_groups, self.parameter_groups, self.replication_groups = {}, {}, {}
        self.db_clusters, self.db_instances, self.tables = {}, {}, {}
        self.roles, self.functions = {}, {}
        self.redis = FakeRedis()
        self.calls = []

    def client(self, name):
        return self

    # EC2
    def describe_vpcs(self, Filters):
        names = Filters[0]['Values']
        return {'Vpcs': [vpc for vpc in self.vpcs.values() if vpc['Name'] in names]}

    def create_vpc(self, CidrBlock, TagSpecifications):
        vpc_id = f'vpc-{next(self.ids):08d}'
        name = next(tag['Value'] for tag in TagSpecifications[0]['Tags'] if tag['Key'] == 'Name')
        self.vpcs[vpc_id] = {'VpcId': vpc_id, 'Name': name, 'CidrBlock': CidrBlock}
        return {'Vpc': self.vpcs[vpc_id]}

    def delete_vpc(self, VpcId):
        if any(subnet['VpcId'] == VpcId for subnet in self.subnets.values()):
            raise client_error('DependencyViolation')
        del self.vpcs[VpcId]

    def describe_subnets(self, Filters):
        vpc_id = Filters[0]['Values'][0]
        return {'Subnets': [copy.deepcopy(s) for s in self.subnets.values() if s['VpcId'] == vpc_id]}

    def create_subnet(self, VpcId, CidrBlock, AvailabilityZone, TagSpecifications=None):
        subnet_id = f'subnet-{next(self.ids):08d}'
        self.subnets[subnet_id] = {'SubnetId': subnet_id, 'VpcId': VpcId, 'CidrBlock': CidrBlock,
                                   'AvailabilityZone': AvailabilityZone}
        return {'Subnet': self.subnets[subnet_id]}

    def delete_subnet(self, SubnetId):
        del self.subnets[SubnetId]

    def describe_security_groups(self, Filters):
        vpc_id, name = Filters[0]['Values'][0], Filters[1]['Values'][0]
        return {'SecurityGroups': [copy.deepcopy(g) for g in self.groups.values()
                                   if g['VpcId'] == vpc_id and g['GroupName'] == name]}

    def create_security_group(self, GroupName, Description, VpcId):
        group_id = f'sg-{next(self.ids):08d}'
        self.groups[group_id] = {'GroupId': group_id, 'GroupName': GroupName, 'VpcId': VpcId, 'IpPermissions': []}
        return {'GroupId': group_id}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        for rule in IpPermissions:
            stored = {'IpRanges': [], 'Ipv6Ranges': [], 'UserIdGroupPairs': [], **copy.deepcopy(rule)}
            if stored in self.groups[GroupId]['IpPermissions']:
                raise client_error('InvalidPermission.Duplicate')
            self.groups[GroupId]['IpPermissions'].append(stored)

    def revoke_security_group_ingress(self, GroupId, IpPermissions):
        rules = self.groups[GroupId]['IpPermissions']
        for rule in IpPermissions:
            normalized = {'IpRanges': [], 'Ipv6Ranges': [], 'UserIdGroupPairs': [], **rule}
            rules[:] = [item for item in rules if item != normalized]

    def delete_security_group(self, GroupId):
        del self.groups[GroupId]

    # ElastiCache
    def describe_cache_subnet_groups(self, CacheSubnetGroupName):
        if CacheSubnetGroupName not in self.subnet_groups:
            raise client_error('CacheSubnetGroupNotFoundFault')
        return {'CacheSubnetGroups': [copy.deepcopy(self.subnet_groups[CacheSubnetGroupName])]}

    def _subnet_entries(self, ids):
        return [{'SubnetIdentifier': i, 'SubnetAvailabilityZone': {'Name': self.subnets[i]['AvailabilityZone']}}
                for i in ids]

    def create_cache_subnet_group(self, CacheSubnetGroupName, CacheSubnetGroupDescription, SubnetIds):
        self.subnet_groups[CacheSubnetGroupName] = {'CacheSubnetGroupName': CacheSubnetGroupName,
                                                    'Subnets': self._subnet_entries(SubnetIds)}

    def modify_cache_subnet_group(self, CacheSubnetGroupName, SubnetIds):
        self.subnet_groups[CacheSubnetGroupName]['Subnets'] = self._subnet_entries(SubnetIds)

    def delete_cache_subnet_group(self, CacheSubnetGroupName):
        if CacheSubnetGroupName not in self.subnet_groups:
            raise client_error('CacheSubnetGroupNotFoundFault')
        del self.subnet_groups[CacheSubnetGroupName]

    def describe_cache_parameter_groups(self, CacheParameterGroupName):
        if CacheParameterGroupName not in self.parameter_groups:
            raise client_error('CacheParameterGroupNotFound')
        return {'CacheParameterGroups': [{'CacheParameterGroupName': CacheParameterGroupName}]}

    def create_cache_parameter_group(self, CacheParameterGroupName, CacheParameterGroupFamily, Description):
        self.parameter_groups[CacheParameterGroupName] = {'maxmemory-policy': 'volatile-lru'}

    def modify_cache_parameter_group(self, CacheParameterGroupName, ParameterNameValues):
        for item in ParameterNameValues:
            self.parameter_groups[CacheParameterGroupName][item['ParameterName']] = item['ParameterValue']

    def describe_cache_parameters(self, CacheParameterGroupName):
        if CacheParameterGroupName not in self.parameter_groups:
            raise client_error('CacheParameterGroupNotFound')
        return {'Parameters': [{'ParameterName': key, 'ParameterValue': value}
                               for key, value in self.parameter_groups[CacheParameterGroupName].items()]}

    def delete_cache_parameter_group(self, CacheParameterGroupName):
        if CacheParameterGroupName not in self.parameter_groups:
            raise client_error('CacheParameterGroupNotFound')
        del self.parameter_groups[CacheParameterGroupName]

    def create_replication_group(self, ReplicationGroupId, NumCacheClusters, AutomaticFailoverEnabled, MultiAZEnabled,
                                 CacheSubnetGroupName, CacheParameterGroupName, AtRestEncryptionEnabled, **kwargs):
        self.calls.append(('create_replication_group', ReplicationGroupId))
        members = [f'{ReplicationGroupId}-{i:03d}' for i in range(1, NumCacheClusters + 1)]
        self.replication_groups[ReplicationGroupId] = {
            'ReplicationGroupId': ReplicationGroupId, 'Status': 'available', 'MemberClusters': members,
            'NodeGroups': [{'NodeGroupId': '0001', 'PrimaryEndpoint': {'Address': 'floci', 'Port': 6379},
                            'ReaderEndpoint': {'Address': 'floci', 'Port': 6379},
                            'NodeGroupMembers': [{'CacheClusterId': m, 'CurrentRole': 'primary' if i == 0 else 'replica'}
                                                 for i, m in enumerate(members)]}],
            'AutomaticFailover': 'enabled' if AutomaticFailoverEnabled else 'disabled',
            'MultiAZ': 'enabled' if MultiAZEnabled else 'disabled',
            'AtRestEncryptionEnabled': AtRestEncryptionEnabled,
            '_subnet_group': CacheSubnetGroupName, '_parameter_group': CacheParameterGroupName,
        }

    def describe_replication_groups(self, ReplicationGroupId):
        if ReplicationGroupId not in self.replication_groups:
            raise client_error('ReplicationGroupNotFoundFault')
        group = copy.deepcopy(self.replication_groups[ReplicationGroupId])
        return {'ReplicationGroups': [{k: v for k, v in group.items() if not k.startswith('_')}]}

    def describe_cache_clusters(self, ShowCacheNodeInfo=False):
        return {'CacheClusters': [
            {'CacheClusterId': member, 'ReplicationGroupId': group_id, 'CacheClusterStatus': 'available',
             'CacheSubnetGroupName': group['_subnet_group'],
             'CacheParameterGroup': {'CacheParameterGroupName': group['_parameter_group']},
             'CacheNodes': [{'Endpoint': {'Address': 'floci', 'Port': 6379}}]}
            for group_id, group in self.replication_groups.items() for member in group['MemberClusters']]}

    def delete_replication_group(self, ReplicationGroupId, RetainPrimaryCluster=False):
        if ReplicationGroupId not in self.replication_groups:
            raise client_error('ReplicationGroupNotFoundFault')
        del self.replication_groups[ReplicationGroupId]

    # RDS and the Data API
    def create_db_cluster(self, DBClusterIdentifier, **kwargs):
        self.db_clusters[DBClusterIdentifier] = {
            'DBClusterIdentifier': DBClusterIdentifier, 'Status': 'available', 'HttpEndpointEnabled': True,
            'MasterUserSecret': {'SecretArn': 'arn:aws:secretsmanager:us-east-1:000000000000:secret:rds!cluster-x'}}

    def describe_db_clusters(self, DBClusterIdentifier):
        if DBClusterIdentifier not in self.db_clusters:
            raise client_error('DBClusterNotFoundFault')
        return {'DBClusters': [copy.deepcopy(self.db_clusters[DBClusterIdentifier])]}

    def delete_db_cluster(self, DBClusterIdentifier, SkipFinalSnapshot):
        if DBClusterIdentifier not in self.db_clusters:
            raise client_error('DBClusterNotFoundFault')
        del self.db_clusters[DBClusterIdentifier]
        self.tables.clear()

    def create_db_instance(self, DBInstanceIdentifier, **kwargs):
        self.db_instances[DBInstanceIdentifier] = {'DBInstanceIdentifier': DBInstanceIdentifier,
                                                   'DBInstanceStatus': 'available'}

    def describe_db_instances(self, DBInstanceIdentifier):
        if DBInstanceIdentifier not in self.db_instances:
            raise client_error('DBInstanceNotFound')
        return {'DBInstances': [copy.deepcopy(self.db_instances[DBInstanceIdentifier])]}

    def delete_db_instance(self, DBInstanceIdentifier, SkipFinalSnapshot):
        if DBInstanceIdentifier not in self.db_instances:
            raise client_error('DBInstanceNotFound')
        del self.db_instances[DBInstanceIdentifier]

    def execute_statement(self, resourceArn, secretArn, database, sql, parameters=()):
        self.calls.append(('sql', sql))
        params = {item['name']: item['value']['stringValue'] for item in parameters}
        if sql.startswith('CREATE TABLE'):
            self.tables.setdefault('products', {})
        elif sql.startswith('INSERT'):
            for sku, name, price in re.findall(r"\('([^']+)', '([^']+)', ([0-9.]+)\)", sql):
                self.tables['products'][sku] = (name, f'{float(price):.2f}')
        elif sql.startswith('UPDATE'):
            name, _ = self.tables['products'][params['sku']]
            self.tables['products'][params['sku']] = (name, f"{float(params['price']):.2f}")
        elif sql.startswith('SELECT'):
            rows = sorted(self.tables.get('products', {}).items())
            if 'sku' in params:
                rows = [row for row in rows if row[0] == params['sku']]
            return {'records': [[{'stringValue': sku}, {'stringValue': name}, {'stringValue': price}]
                                for sku, (name, price) in rows]}
        return {'records': []}

    # IAM
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': {'RoleName': RoleName}}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description=''):
        self.roles[RoleName] = {'attached': set(), 'inline': {}}

    def attach_role_policy(self, RoleName, PolicyArn):
        self.roles[RoleName]['attached'].add(PolicyArn)

    def detach_role_policy(self, RoleName, PolicyArn):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        self.roles[RoleName]['attached'].discard(PolicyArn)

    def put_role_policy(self, RoleName, PolicyName, PolicyDocument):
        self.roles[RoleName]['inline'][PolicyName] = json.loads(PolicyDocument)

    def delete_role_policy(self, RoleName, PolicyName):
        if RoleName not in self.roles or PolicyName not in self.roles[RoleName]['inline']:
            raise client_error('NoSuchEntity')
        del self.roles[RoleName]['inline'][PolicyName]

    def delete_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        del self.roles[RoleName]

    # Lambda
    def get_function_configuration(self, FunctionName):
        if FunctionName not in self.functions:
            raise client_error('ResourceNotFoundException')
        return {k: copy.deepcopy(v) for k, v in self.functions[FunctionName].items() if k != 'code'}

    def create_function(self, FunctionName, Code, Description='', **settings):
        self.functions[FunctionName] = {'FunctionName': FunctionName, 'State': 'Active',
                                        'LastUpdateStatus': 'Successful', 'code': Code['ZipFile'], **settings}

    def update_function_code(self, FunctionName, ZipFile):
        self.functions[FunctionName]['code'] = ZipFile

    def update_function_configuration(self, FunctionName, **settings):
        self.functions[FunctionName].update(settings)

    def delete_function(self, FunctionName):
        if FunctionName not in self.functions:
            raise client_error('ResourceNotFoundException')
        del self.functions[FunctionName]

    def invoke(self, FunctionName, Payload):
        import zipfile
        function = self.functions[FunctionName]
        source = zipfile.ZipFile(io.BytesIO(function['code'])).read('reader.py').decode()
        module = types.ModuleType('reader')
        fake_boto3 = types.SimpleNamespace(client=lambda name: self)
        fake_socket = types.SimpleNamespace(create_connection=lambda address, timeout=None: FakeConnection(self.redis))
        environment = function['Environment']['Variables']
        with patch.dict('os.environ', environment), patch.dict('sys.modules', {'boto3': fake_boto3, 'socket': fake_socket}):
            exec(compile(source, 'reader.py', 'exec'), module.__dict__)
            try:
                result = module.handler(json.loads(Payload), None)
            except Exception as exc:  # noqa: BLE001 - the Lambda runtime reports any handler error
                return {'FunctionError': 'Unhandled', 'Payload': io.BytesIO(json.dumps({'errorMessage': str(exc)}).encode())}
        return {'StatusCode': 200, 'Payload': io.BytesIO(json.dumps(result).encode())}


class ElastiCacheCacheAsideLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        for target in ('_client',):
            patcher = patch(f'dashboard.labs.elasticache_labs.{target}', side_effect=self.fake.client)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.elasticache_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def cache_group(self):
        vpc_id = next(iter(self.fake.vpcs))
        return next(g for g in self.fake.groups.values() if g['VpcId'] == vpc_id and g['GroupName'] == lab.CACHE_SG)

    def test_lab_is_registered_with_eight_steps_exam_tags_and_a_batch(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 8)
        self.assertEqual(item['steps'][0]['key'], 'cache-network')
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '3.3'), tasks)
        self.assertIn(('CLF-C02', '3.4'), tasks)
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
        self.assertEqual(status['passed_steps'], 8)

    def test_miss_then_hits_then_invalidation(self):
        results = self.run_all()
        miss = results['cache-miss']['json']['Read']
        self.assertEqual(miss['source'], 'database')
        self.assertEqual(miss['ttl'], lab.TTL_SECONDS)
        self.assertIn('noeviction', results['cache-miss']['verification']['message'])
        reads = results['cache-hit']['json']['Reads']
        self.assertEqual([read['source'] for read in reads], ['cache'] * lab.HIT_READS)
        after = results['update-invalidate']['json']
        self.assertEqual(after['Update']['invalidated'], 1)
        self.assertEqual(after['ReadAfterUpdate']['source'], 'database')
        self.assertEqual(after['ReadAfterUpdate']['product']['price'], lab.NEW_PRICE)
        self.assertEqual(after['NextRead']['source'], 'cache')
        self.assertEqual(json.loads(self.fake.redis.data['product:SKU-100'])['price'], lab.NEW_PRICE)

    def test_hits_never_query_the_database(self):
        self.run_all()
        self.fake.calls.clear()
        run_lab_step(SERVICE, LAB, 'cache-hit')
        self.assertFalse([call for call in self.fake.calls if call[0] == 'sql'])

    def test_the_role_policy_scopes_the_data_api_and_labels_the_floci_statement(self):
        self.run_all()
        policy = self.fake.roles[lab.ROLE_NAME]['inline'][lab.ROLE_POLICY]
        statements = {statement['Sid']: statement for statement in policy['Statement']}
        self.assertEqual(statements['QueryProductsDatabase']['Resource'], lab.DB_CLUSTER_ARN)
        self.assertTrue(statements['ReadDatabaseCredentials']['Resource'].startswith('arn:aws:secretsmanager:'))
        self.assertEqual(statements['FlociOnlyDataApi']['Resource'], '*')
        self.assertIn(lab.VPC_ACCESS_POLICY, self.fake.roles[lab.ROLE_NAME]['attached'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len(self.fake.vpcs), 1)
        self.assertEqual(len(self.fake.subnets), 2)
        self.assertEqual(len(self.fake.groups), 2)
        self.assertEqual([call for call in self.fake.calls if call[0] == 'create_replication_group'],
                         [('create_replication_group', lab.REPLICATION_GROUP)])
        self.assertEqual(len(self.cache_group()['IpPermissions']), 1)

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('Fix the network first', run_lab_step(SERVICE, LAB, 'replication-group')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'deploy-reader')['verification']['message'])
        self.assertIn('Deploy the reader first', run_lab_step(SERVICE, LAB, 'cache-miss')['verification']['message'])
        run_lab_step(SERVICE, LAB, 'cache-network')
        self.assertIn('parameter group first', run_lab_step(SERVICE, LAB, 'replication-group')['verification']['message'])

    def test_cache_open_to_a_cidr_fails_and_the_step_repairs_it(self):
        self.run_all()
        group = self.cache_group()
        self.fake.authorize_security_group_ingress(group['GroupId'], [
            {'IpProtocol': 'tcp', 'FromPort': 6379, 'ToPort': 6379, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}])
        self.assertEqual(self.failed(), ['cache-network'])
        self.assertIn('0.0.0.0/0', lab._check_network()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'cache-network')['verified'])
        self.assertEqual(len(self.cache_group()['IpPermissions']), 1)

    def test_noeviction_policy_fails(self):
        self.run_all()
        self.fake.modify_cache_parameter_group(lab.PARAMETER_GROUP, [
            {'ParameterName': 'maxmemory-policy', 'ParameterValue': 'noeviction'}])
        self.assertEqual(self.failed(), ['eviction-policy'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'eviction-policy')['verified'])

    def test_single_node_multi_az_group_fails_and_is_recreated(self):
        self.run_all()
        group = self.fake.replication_groups[lab.REPLICATION_GROUP]
        group['MemberClusters'] = group['MemberClusters'][:1]
        group['NodeGroups'][0]['NodeGroupMembers'] = group['NodeGroups'][0]['NodeGroupMembers'][:1]
        self.assertIn('replica', lab._check_replication()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'replication-group')['verified'])
        self.assertEqual(len(self.fake.replication_groups[lab.REPLICATION_GROUP]['MemberClusters']), 2)

    def test_reader_pointing_at_another_cache_fails(self):
        self.run_all()
        self.fake.functions[lab.FUNCTION]['Environment']['Variables']['CACHE_PORT'] = '6390'
        self.assertIn('CACHE_PORT', lab._check_reader()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'deploy-reader')['verified'])

    def test_a_missing_invalidation_is_caught(self):
        self.run_all()
        source = lab.READER_SOURCE.replace('"invalidated": redis("DEL", key(sku))', '"invalidated": 0')
        with patch.object(lab, 'READER_SOURCE', source):
            run_lab_step(SERVICE, LAB, 'deploy-reader')
        result = run_lab_step(SERVICE, LAB, 'update-invalidate')
        self.assertFalse(result['verified'])
        self.assertIn('not invalidated', result['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in (self.fake.vpcs, self.fake.subnets, self.fake.groups, self.fake.subnet_groups,
                      self.fake.parameter_groups, self.fake.replication_groups, self.fake.db_clusters,
                      self.fake.db_instances, self.fake.roles, self.fake.functions):
            self.assertEqual(store, {})
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
