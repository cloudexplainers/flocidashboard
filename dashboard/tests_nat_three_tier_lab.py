"""Three-tier VPC lab: NAT gateway per zone, isolated data tier, S3 gateway endpoint."""

import importlib
import itertools
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

nat = importlib.import_module('dashboard.labs.ec2_nat_labs')

SERVICE = 'ec2'
LAB = 'nat-three-tier'
S3_SERVICE = 'com.amazonaws.us-east-1.s3'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


def _name(item):
    return next((tag['Value'] for tag in item.get('Tags') or [] if tag['Key'] == 'Name'), None)


def _tags(specs):
    return [dict(tag) for spec in specs or [] for tag in spec['Tags']]


def _filter(items, filters):
    for flt in filters or []:
        name, values = flt['Name'], flt['Values']
        if name in ('vpc-id', 'attachment.vpc-id'):
            items = [item for item in items if item.get('VpcId') in values]
        elif name == 'tag:Name':
            items = [item for item in items if _name(item) in values]
        elif name == 'service-name':
            items = [item for item in items if item.get('ServiceName') in values]
    return items


class FakeNetwork:
    """Just enough EC2 and RDS for the lab. Like Floci, it stores NAT settings without checking them."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.vpcs, self.subnets, self.gateways, self.tables = {}, {}, {}, {}
        self.addresses, self.nats, self.endpoints, self.db_groups = {}, {}, {}, {}

    def _id(self, prefix):
        return f'{prefix}-{next(self.ids):04x}'

    # VPCs
    def create_vpc(self, CidrBlock, TagSpecifications=None):
        vpc_id = self._id('vpc')
        self.vpcs[vpc_id] = {'VpcId': vpc_id, 'CidrBlock': CidrBlock, 'Tags': _tags(TagSpecifications),
                             'dns': {'enableDnsSupport': True, 'enableDnsHostnames': False}}
        main = self._id('rtb')
        self.tables[main] = {'RouteTableId': main, 'VpcId': vpc_id, 'Tags': [],
                             'Routes': [{'DestinationCidrBlock': CidrBlock, 'GatewayId': 'local'}],
                             'Associations': [{'RouteTableAssociationId': self._id('rtbassoc'), 'Main': True}]}
        return {'Vpc': {key: value for key, value in self.vpcs[vpc_id].items() if key != 'dns'}}

    def describe_vpcs(self, Filters=None):
        vpcs = [{key: value for key, value in vpc.items() if key != 'dns'} for vpc in self.vpcs.values()]
        return {'Vpcs': _filter(vpcs, Filters)}

    def modify_vpc_attribute(self, VpcId, **kwargs):
        for key, value in kwargs.items():
            self.vpcs[VpcId]['dns'][key[0].lower() + key[1:]] = value['Value']
        return {}

    def describe_vpc_attribute(self, VpcId, Attribute):
        return {Attribute[0].upper() + Attribute[1:]: {'Value': self.vpcs[VpcId]['dns'][Attribute]}}

    def delete_vpc(self, VpcId):
        if any(item['VpcId'] == VpcId for item in [*self.subnets.values(), *self.nats.values()]):
            raise client_error('DependencyViolation')
        for table_id in [key for key, table in self.tables.items() if table['VpcId'] == VpcId]:
            del self.tables[table_id]
        del self.vpcs[VpcId]
        return {}

    # Subnets
    def create_subnet(self, VpcId, CidrBlock, AvailabilityZone, TagSpecifications=None):
        subnet_id = self._id('subnet')
        self.subnets[subnet_id] = {'SubnetId': subnet_id, 'VpcId': VpcId, 'CidrBlock': CidrBlock,
                                   'AvailabilityZone': AvailabilityZone, 'MapPublicIpOnLaunch': False,
                                   'Tags': _tags(TagSpecifications)}
        return {'Subnet': dict(self.subnets[subnet_id])}

    def describe_subnets(self, Filters=None):
        return {'Subnets': _filter([dict(item) for item in self.subnets.values()], Filters)}

    def modify_subnet_attribute(self, SubnetId, MapPublicIpOnLaunch):
        self.subnets[SubnetId]['MapPublicIpOnLaunch'] = MapPublicIpOnLaunch['Value']
        return {}

    def delete_subnet(self, SubnetId):
        if SubnetId not in self.subnets:
            raise client_error('InvalidSubnetID.NotFound')
        del self.subnets[SubnetId]
        return {}

    # Internet gateways
    def create_internet_gateway(self, TagSpecifications=None):
        igw_id = self._id('igw')
        self.gateways[igw_id] = {'InternetGatewayId': igw_id, 'VpcId': None, 'Tags': _tags(TagSpecifications)}
        return {'InternetGateway': {'InternetGatewayId': igw_id}}

    def attach_internet_gateway(self, InternetGatewayId, VpcId):
        self.gateways[InternetGatewayId]['VpcId'] = VpcId
        return {}

    def detach_internet_gateway(self, InternetGatewayId, VpcId):
        self.gateways[InternetGatewayId]['VpcId'] = None
        return {}

    def delete_internet_gateway(self, InternetGatewayId):
        del self.gateways[InternetGatewayId]
        return {}

    def describe_internet_gateways(self, Filters=None):
        return {'InternetGateways': _filter([dict(item) for item in self.gateways.values()], Filters)}

    # Route tables
    def create_route_table(self, VpcId, TagSpecifications=None):
        table_id = self._id('rtb')
        self.tables[table_id] = {'RouteTableId': table_id, 'VpcId': VpcId, 'Tags': _tags(TagSpecifications),
                                 'Routes': [{'DestinationCidrBlock': self.vpcs[VpcId]['CidrBlock'], 'GatewayId': 'local'}],
                                 'Associations': []}
        return {'RouteTable': self._copy_table(table_id)}

    def _copy_table(self, table_id):
        table = self.tables[table_id]
        return {**table, 'Routes': [dict(route) for route in table['Routes']],
                'Associations': [dict(item) for item in table['Associations']]}

    def describe_route_tables(self, Filters=None):
        return {'RouteTables': _filter([self._copy_table(key) for key in self.tables], Filters)}

    def create_route(self, RouteTableId, DestinationCidrBlock, **target):
        routes = self.tables[RouteTableId]['Routes']
        if any(route.get('DestinationCidrBlock') == DestinationCidrBlock for route in routes):
            raise client_error('RouteAlreadyExists')
        routes.append({'DestinationCidrBlock': DestinationCidrBlock, **target})
        return {'Return': True}

    def replace_route(self, RouteTableId, DestinationCidrBlock, **target):
        routes = self.tables[RouteTableId]['Routes']
        for index, route in enumerate(routes):
            if route.get('DestinationCidrBlock') == DestinationCidrBlock:
                routes[index] = {'DestinationCidrBlock': DestinationCidrBlock, **target}
        return {}

    def associate_route_table(self, RouteTableId, SubnetId):
        association_id = self._id('rtbassoc')
        self.tables[RouteTableId]['Associations'].append({'RouteTableAssociationId': association_id, 'SubnetId': SubnetId})
        return {'AssociationId': association_id}

    def replace_route_table_association(self, AssociationId, RouteTableId):
        subnet_id = None
        for table in self.tables.values():
            for item in list(table['Associations']):
                if item['RouteTableAssociationId'] == AssociationId:
                    subnet_id = item['SubnetId']
                    table['Associations'].remove(item)
        return {'NewAssociationId': self.associate_route_table(RouteTableId, subnet_id)['AssociationId']}

    def disassociate_route_table(self, AssociationId):
        for table in self.tables.values():
            table['Associations'] = [item for item in table['Associations'] if item['RouteTableAssociationId'] != AssociationId]
        return {}

    def delete_route_table(self, RouteTableId):
        if self.tables[RouteTableId]['Associations']:
            raise client_error('DependencyViolation')
        del self.tables[RouteTableId]
        return {}

    # Elastic IPs and NAT gateways
    def allocate_address(self, Domain, TagSpecifications=None):
        allocation_id = self._id('eipalloc')
        self.addresses[allocation_id] = {'AllocationId': allocation_id, 'Domain': Domain,
                                         'PublicIp': f'54.0.0.{len(self.addresses) + 1}', 'Tags': _tags(TagSpecifications)}
        return dict(self.addresses[allocation_id])

    def describe_addresses(self, Filters=None):
        return {'Addresses': _filter([dict(item) for item in self.addresses.values()], Filters)}

    def release_address(self, AllocationId):
        del self.addresses[AllocationId]
        return {}

    def create_nat_gateway(self, SubnetId, AllocationId=None, TagSpecifications=None):
        nat_id = self._id('nat')
        address = {'NetworkInterfaceId': self._id('eni'), 'PrivateIp': '10.64.0.10', 'IsPrimary': True}
        if AllocationId:
            address.update(AllocationId=AllocationId, PublicIp=self.addresses[AllocationId]['PublicIp'])
        self.nats[nat_id] = {'NatGatewayId': nat_id, 'SubnetId': SubnetId, 'VpcId': self.subnets[SubnetId]['VpcId'],
                             'State': 'available', 'ConnectivityType': 'public', 'NatGatewayAddresses': [address],
                             'Tags': _tags(TagSpecifications)}
        return {'NatGateway': dict(self.nats[nat_id])}

    def describe_nat_gateways(self, Filter=None):
        return {'NatGateways': _filter([dict(item) for item in self.nats.values()], Filter)}

    def delete_nat_gateway(self, NatGatewayId):
        del self.nats[NatGatewayId]
        return {'NatGatewayId': NatGatewayId}

    # Endpoints
    def create_vpc_endpoint(self, VpcEndpointType, VpcId, ServiceName, RouteTableIds, TagSpecifications=None):
        endpoint_id = self._id('vpce')
        self.endpoints[endpoint_id] = {'VpcEndpointId': endpoint_id, 'VpcEndpointType': VpcEndpointType, 'VpcId': VpcId,
                                       'ServiceName': ServiceName, 'State': 'available', 'RouteTableIds': list(RouteTableIds),
                                       'Tags': _tags(TagSpecifications)}
        return {'VpcEndpoint': dict(self.endpoints[endpoint_id])}

    def modify_vpc_endpoint(self, VpcEndpointId, AddRouteTableIds):
        self.endpoints[VpcEndpointId]['RouteTableIds'] += AddRouteTableIds
        return {'Return': True}

    def describe_vpc_endpoints(self, Filters=None):
        return {'VpcEndpoints': _filter([dict(item) for item in self.endpoints.values()], Filters)}

    def delete_vpc_endpoints(self, VpcEndpointIds):
        for endpoint_id in VpcEndpointIds:
            self.endpoints.pop(endpoint_id, None)
        return {'Unsuccessful': []}

    # RDS
    def _db_group(self, name):
        group = self.db_groups[name]
        return {'DBSubnetGroupName': name, 'Subnets': [
            {'SubnetIdentifier': subnet_id, 'SubnetAvailabilityZone': {'Name': self.subnets[subnet_id]['AvailabilityZone']}}
            for subnet_id in group
        ]}

    def create_db_subnet_group(self, DBSubnetGroupName, DBSubnetGroupDescription, SubnetIds, Tags=None):
        self.db_groups[DBSubnetGroupName] = list(SubnetIds)
        return {'DBSubnetGroup': self._db_group(DBSubnetGroupName)}

    def modify_db_subnet_group(self, DBSubnetGroupName, SubnetIds):
        self.db_groups[DBSubnetGroupName] = list(SubnetIds)
        return {'DBSubnetGroup': self._db_group(DBSubnetGroupName)}

    def describe_db_subnet_groups(self, DBSubnetGroupName):
        if DBSubnetGroupName not in self.db_groups:
            raise client_error('DBSubnetGroupNotFoundFault')
        return {'DBSubnetGroups': [self._db_group(DBSubnetGroupName)]}

    def delete_db_subnet_group(self, DBSubnetGroupName):
        del self.db_groups[DBSubnetGroupName]
        return {}

    # Helpers for the tests
    def by_name(self, store, name):
        return next(item for item in store.values() if _name(item) == name)


class NatThreeTierLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeNetwork()
        for target, kwargs in (
            ('dashboard.labs.ec2_nat_labs._client', {'return_value': self.fake}),
            ('dashboard.labs.ec2_nat_labs._s3_service_name', {'return_value': S3_SERVICE}),
            ('dashboard.labs.ec2_nat_labs.time.sleep', {}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(nat, 'NAT_WAIT_SECONDS', 0)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step['key']: run_lab_step(SERVICE, LAB, step['key']) for step in nat.LAB['steps']}

    def run_until(self, last):
        for step in nat.STEP_KEYS:
            run_lab_step(SERVICE, LAB, step)
            if step == last:
                return

    def test_lab_is_registered_with_ten_steps_and_exam_tags(self):
        lab = next(item for item in labs_for_service(SERVICE) if item['key'] == LAB)
        self.assertEqual(len(lab['steps']), 10)
        self.assertEqual(lab['steps'][0]['key'], 'create-vpc')
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '1.2'), tasks)
        self.assertIn(('SAA-C03', '4.4'), tasks)

    def test_every_step_explains_what_floci_does_not_do_where_it_matters(self):
        notes = {step['key']: step['explanation'] for step in nat.LAB['steps']}
        for key in ('route-public-subnets', 'create-nat-gateways', 'route-app-subnets', 'isolate-data-subnets',
                    'add-s3-gateway-endpoint', 'trace-egress-paths', 'allocate-elastic-ips'):
            self.assertIn('Floci', notes[key], key)

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 10)

    def test_the_finished_network_has_the_designed_shape(self):
        self.run_all()
        fake = self.fake
        self.assertEqual(len(fake.nats), 2)
        self.assertEqual(len({item['NatGatewayAddresses'][0]['AllocationId'] for item in fake.nats.values()}), 2)
        for az in nat.ZONES:
            table = fake.by_name(fake.tables, nat.APP_ROUTE_TABLES[az])
            target = next(route['NatGatewayId'] for route in table['Routes'] if route['DestinationCidrBlock'] == '0.0.0.0/0')
            self.assertEqual(fake.subnets[fake.nats[target]['SubnetId']]['AvailabilityZone'], az)
        data = fake.by_name(fake.tables, nat.DATA_ROUTE_TABLE)
        self.assertEqual([route['GatewayId'] for route in data['Routes']], ['local'])
        trace = run_lab_step(SERVICE, LAB, 'trace-egress-paths')['json']['EgressPaths']
        self.assertEqual({path['Tier'] for path in trace if path['DefaultRoute'] == 'none (local only)'}, {'data'})

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual((len(self.fake.vpcs), len(self.fake.subnets), len(self.fake.nats), len(self.fake.addresses)), (1, 6, 2, 2))
        self.assertEqual(len(self.fake.endpoints), 1)

    def test_steps_out_of_order_explain_what_is_missing(self):
        result = run_lab_step(SERVICE, LAB, 'create-nat-gateways')
        self.assertFalse(result['verified'])
        self.assertIn('Create the VPC', result['verification']['message'])
        self.run_until('allocate-elastic-ips')
        table = self.fake.by_name(self.fake.tables, nat.PUBLIC_ROUTE_TABLE)
        table['Routes'] = [route for route in table['Routes'] if route['DestinationCidrBlock'] != '0.0.0.0/0']
        result = run_lab_step(SERVICE, LAB, 'create-nat-gateways')
        self.assertFalse(result['verified'])
        self.assertIn('internet gateway', result['verification']['message'])
        self.assertEqual(self.fake.nats, {})

    def test_nat_gateways_sharing_an_elastic_ip_fail(self):
        # Floci (like this fake) accepts a second NAT gateway on an address already in use; AWS refuses it.
        self.run_until('allocate-elastic-ips')
        vpc_id = next(iter(self.fake.vpcs))
        subnets = {_name(item): item['SubnetId'] for item in self.fake.subnets.values()}
        allocation = self.fake.by_name(self.fake.addresses, nat.EIP_NAMES['us-east-1a'])['AllocationId']
        for az in nat.ZONES:
            self.fake.create_nat_gateway(SubnetId=subnets[f'lab-three-tier-public-{az[-1]}'], AllocationId=allocation,
                                         TagSpecifications=nat._tags('natgateway', nat.NAT_NAMES[az]))
        result = run_lab_step(SERVICE, LAB, 'create-nat-gateways')
        self.assertFalse(result['verified'])
        self.assertIn('share Elastic IP', result['verification']['message'])
        self.assertTrue(vpc_id)

    def test_public_nat_gateway_without_an_elastic_ip_fails(self):
        self.run_until('allocate-elastic-ips')
        subnets = {_name(item): item['SubnetId'] for item in self.fake.subnets.values()}
        self.fake.create_nat_gateway(SubnetId=subnets['lab-three-tier-public-a'],
                                     TagSpecifications=nat._tags('natgateway', nat.NAT_NAMES['us-east-1a']))
        result = run_lab_step(SERVICE, LAB, 'create-nat-gateways')
        self.assertFalse(result['verified'])
        self.assertIn('has no Elastic IP', result['verification']['message'])

    def test_app_subnet_routed_to_the_other_zone_fails_and_the_step_repairs_it(self):
        self.run_all()
        table = self.fake.by_name(self.fake.tables, nat.APP_ROUTE_TABLES['us-east-1b'])
        nat_a = self.fake.by_name(self.fake.nats, nat.NAT_NAMES['us-east-1a'])['NatGatewayId']
        self.fake.replace_route(table['RouteTableId'], '0.0.0.0/0', NatGatewayId=nat_a)
        self.assertFalse(lab_status(SERVICE, LAB)['steps']['route-app-subnets']['verified'])
        trace = run_lab_step(SERVICE, LAB, 'trace-egress-paths')
        self.assertFalse(trace['verified'])
        self.assertIn('crosses zones', trace['verification']['message'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'route-app-subnets')['verified'])

    def test_data_tier_with_an_internet_route_fails(self):
        self.run_all()
        table = self.fake.by_name(self.fake.tables, nat.DATA_ROUTE_TABLE)
        nat_a = self.fake.by_name(self.fake.nats, nat.NAT_NAMES['us-east-1a'])['NatGatewayId']
        self.fake.create_route(table['RouteTableId'], '0.0.0.0/0', NatGatewayId=nat_a)
        result = run_lab_step(SERVICE, LAB, 'isolate-data-subnets')
        self.assertFalse(result['verified'])
        self.assertIn('must not reach the internet', result['verification']['message'])

    def test_single_zone_db_subnet_group_fails_and_the_step_repairs_it(self):
        self.run_all()
        data_a = self.fake.by_name(self.fake.subnets, 'lab-three-tier-data-a')['SubnetId']
        self.fake.modify_db_subnet_group(nat.DB_SUBNET_GROUP, [data_a])
        self.assertFalse(nat._check_data_tier()[0])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'isolate-data-subnets')['verified'])
        self.assertEqual(len(self.fake.db_groups[nat.DB_SUBNET_GROUP]), 2)

    def test_endpoint_missing_an_app_route_table_fails(self):
        self.run_all()
        endpoint = next(iter(self.fake.endpoints.values()))
        endpoint['RouteTableIds'] = endpoint['RouteTableIds'][:1]
        self.assertFalse(nat._check_endpoint()[0])
        result = run_lab_step(SERVICE, LAB, 'add-s3-gateway-endpoint')
        self.assertTrue(result['verified'])
        self.assertIn('prefix-list route', result['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        fake = self.fake
        self.assertEqual((fake.vpcs, fake.subnets, fake.nats, fake.addresses, fake.endpoints, fake.db_groups),
                         ({}, {}, {}, {}, {}, {}))
        self.assertEqual(lab_status(SERVICE, LAB)['status'], 'not_started')

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])

    def test_status_before_any_step_is_not_started(self):
        status = lab_status(SERVICE, LAB)
        self.assertEqual(status['status'], 'not_started')
        self.assertEqual(status['total_steps'], 10)
