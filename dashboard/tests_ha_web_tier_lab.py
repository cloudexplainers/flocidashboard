"""Highly available web tier lab: ALB, target group health checks, and an Auto Scaling group."""

import base64
import importlib
import itertools
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.registry import labs_for_service

# The module the lab dispatcher runs (dashboard.labs is an alias of its monolith module).
ha = importlib.import_module('dashboard.labs.elasticloadbalancing_labs')

SERVICE = 'elasticloadbalancing'
LAB = 'ha-web-tier'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


def _name(item):
    return next((tag['Value'] for tag in item.get('Tags') or [] if tag['Key'] == 'Name'), None)


def _tags(specs):
    return [tag for spec in specs or [] for tag in spec['Tags']]


class FakeCloud:
    """Just enough EC2, ELBv2 and Auto Scaling for the lab, with instant health checks."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.vpcs, self.subnets, self.gateways, self.tables, self.groups = {}, {}, {}, {}, {}
        self.templates = {}  # name -> {'LaunchTemplateId', 'versions': [version dicts]}
        self.target_groups, self.balancers, self.listeners = {}, {}, {}
        self.asgs, self.policies, self.refreshes = {}, {}, []
        self.instances = {}  # id -> {'version', 'az', 'state'}
        self.healthy = True
        self.calls = []

    def _id(self, prefix):
        return f'{prefix}-{next(self.ids):04x}'

    @staticmethod
    def _vpc_filter(items, Filters):
        for flt in Filters or []:
            if flt['Name'] in ('vpc-id', 'attachment.vpc-id'):
                items = [item for item in items if item.get('VpcId') in flt['Values']]
            if flt['Name'] == 'tag:Name':
                items = [item for item in items if _name(item) in flt['Values']]
            if flt['Name'] == 'group-name':
                items = [item for item in items if item['GroupName'] in flt['Values']]
        return items

    # EC2 network
    def create_vpc(self, CidrBlock, TagSpecifications=None):
        vpc = {'VpcId': self._id('vpc'), 'CidrBlock': CidrBlock, 'Tags': _tags(TagSpecifications)}
        self.vpcs[vpc['VpcId']] = vpc
        return {'Vpc': vpc}

    def describe_vpcs(self, Filters=None):
        return {'Vpcs': self._vpc_filter(list(self.vpcs.values()), Filters)}

    def delete_vpc(self, VpcId):
        if any(item['VpcId'] == VpcId for item in [*self.subnets.values(), *self.groups.values()]):
            raise client_error('DependencyViolation')
        del self.vpcs[VpcId]

    def create_subnet(self, VpcId, CidrBlock, AvailabilityZone, TagSpecifications=None):
        subnet = {'SubnetId': self._id('subnet'), 'VpcId': VpcId, 'CidrBlock': CidrBlock,
                  'AvailabilityZone': AvailabilityZone, 'Tags': _tags(TagSpecifications)}
        self.subnets[subnet['SubnetId']] = subnet
        return {'Subnet': subnet}

    def describe_subnets(self, Filters=None):
        return {'Subnets': self._vpc_filter(list(self.subnets.values()), Filters)}

    def delete_subnet(self, SubnetId):
        del self.subnets[SubnetId]

    def create_internet_gateway(self, TagSpecifications=None):
        gateway = {'InternetGatewayId': self._id('igw'), 'Tags': _tags(TagSpecifications)}
        self.gateways[gateway['InternetGatewayId']] = gateway
        return {'InternetGateway': gateway}

    def attach_internet_gateway(self, InternetGatewayId, VpcId):
        self.gateways[InternetGatewayId]['VpcId'] = VpcId

    def describe_internet_gateways(self, Filters=None):
        return {'InternetGateways': self._vpc_filter(list(self.gateways.values()), Filters)}

    def detach_internet_gateway(self, InternetGatewayId, VpcId):
        self.gateways[InternetGatewayId].pop('VpcId', None)

    def delete_internet_gateway(self, InternetGatewayId):
        del self.gateways[InternetGatewayId]

    def create_route_table(self, VpcId, TagSpecifications=None):
        table = {'RouteTableId': self._id('rtb'), 'VpcId': VpcId, 'Routes': [], 'Associations': [], 'Tags': _tags(TagSpecifications)}
        self.tables[table['RouteTableId']] = table
        return {'RouteTable': table}

    def describe_route_tables(self, Filters=None):
        return {'RouteTables': self._vpc_filter(list(self.tables.values()), Filters)}

    def create_route(self, RouteTableId, DestinationCidrBlock, GatewayId):
        self.tables[RouteTableId]['Routes'].append({'DestinationCidrBlock': DestinationCidrBlock, 'GatewayId': GatewayId})

    def associate_route_table(self, RouteTableId, SubnetId):
        self.tables[RouteTableId]['Associations'].append({'RouteTableAssociationId': self._id('rtbassoc'), 'SubnetId': SubnetId})

    def disassociate_route_table(self, AssociationId):
        for table in self.tables.values():
            table['Associations'] = [a for a in table['Associations'] if a['RouteTableAssociationId'] != AssociationId]

    def delete_route_table(self, RouteTableId):
        del self.tables[RouteTableId]

    def create_security_group(self, GroupName, Description, VpcId):
        group = {'GroupId': self._id('sg'), 'GroupName': GroupName, 'VpcId': VpcId, 'IpPermissions': []}
        self.groups[group['GroupId']] = group
        return {'GroupId': group['GroupId']}

    def describe_security_groups(self, Filters=None):
        return {'SecurityGroups': self._vpc_filter(list(self.groups.values()), Filters)}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        permissions = self.groups[GroupId]['IpPermissions']
        for permission in IpPermissions:
            if any(p['FromPort'] == permission['FromPort'] for p in permissions):
                raise client_error('InvalidPermission.Duplicate')
            permissions.append({'IpRanges': [], 'UserIdGroupPairs': [], **permission})

    def delete_security_group(self, GroupId):
        del self.groups[GroupId]

    # EC2 launch templates and instances
    def create_launch_template(self, LaunchTemplateName, LaunchTemplateData, VersionDescription=None):
        if LaunchTemplateName in self.templates:
            raise client_error('InvalidLaunchTemplateName.AlreadyExistsException')
        template = {'LaunchTemplateId': self._id('lt'), 'LaunchTemplateName': LaunchTemplateName,
                    'versions': [{'VersionNumber': 1, 'LaunchTemplateData': LaunchTemplateData}]}
        self.templates[LaunchTemplateName] = template
        return {'LaunchTemplate': {k: v for k, v in template.items() if k != 'versions'}}

    def describe_launch_templates(self, LaunchTemplateNames):
        found = [self.templates[name] for name in LaunchTemplateNames if name in self.templates]
        if not found:
            raise client_error('InvalidLaunchTemplateName.NotFoundException')
        return {'LaunchTemplates': [{k: v for k, v in item.items() if k != 'versions'} for item in found]}

    def describe_launch_template_versions(self, LaunchTemplateName):
        if LaunchTemplateName not in self.templates:
            raise client_error('InvalidLaunchTemplateName.NotFoundException')
        return {'LaunchTemplateVersions': self.templates[LaunchTemplateName]['versions']}

    def create_launch_template_version(self, LaunchTemplateName, SourceVersion, VersionDescription, LaunchTemplateData):
        versions = self.templates[LaunchTemplateName]['versions']
        source = next(v for v in versions if str(v['VersionNumber']) == SourceVersion)
        version = {'VersionNumber': len(versions) + 1, 'VersionDescription': VersionDescription,
                   'LaunchTemplateData': {**source['LaunchTemplateData'], **LaunchTemplateData}}
        versions.append(version)
        return {'LaunchTemplateVersion': version}

    def delete_launch_template(self, LaunchTemplateName):
        del self.templates[LaunchTemplateName]

    def terminate_instances(self, InstanceIds):
        self.calls.append('terminate_instances')
        for instance_id in InstanceIds:
            self.instances.pop(instance_id, None)
        for group in self.asgs.values():
            group['ids'] = [i for i in group['ids'] if i not in InstanceIds]
        self._reconcile()
        return {'TerminatingInstances': [{'InstanceId': i} for i in InstanceIds]}

    def describe_instances(self, Filters=None):
        return {'Reservations': []}

    # ELBv2
    def create_target_group(self, Name, **kwargs):
        if Name in self.target_groups:
            raise client_error('DuplicateTargetGroupName')
        group = {'TargetGroupName': Name, 'TargetGroupArn': f'arn:tg/{Name}', **kwargs}
        self.target_groups[Name] = group
        return {'TargetGroups': [group]}

    def describe_target_groups(self, Names):
        if Names[0] not in self.target_groups:
            raise client_error('TargetGroupNotFound')
        return {'TargetGroups': [self.target_groups[Names[0]]]}

    def describe_target_health(self, TargetGroupArn):
        targets = [i for group in self.asgs.values() if TargetGroupArn in group['TargetGroupARNs'] for i in group['ids']]
        state = 'healthy' if self.healthy else 'unhealthy'
        return {'TargetHealthDescriptions': [{'Target': {'Id': i}, 'TargetHealth': {'State': state}} for i in targets]}

    def delete_target_group(self, TargetGroupArn):
        self.target_groups = {k: v for k, v in self.target_groups.items() if v['TargetGroupArn'] != TargetGroupArn}

    def create_load_balancer(self, Name, Type, Scheme, Subnets, SecurityGroups):
        zones = [{'ZoneName': self.subnets[s]['AvailabilityZone'], 'SubnetId': s} for s in Subnets]
        balancer = {'LoadBalancerName': Name, 'LoadBalancerArn': f'arn:lb/{Name}', 'Type': Type, 'Scheme': Scheme,
                    'AvailabilityZones': zones, 'State': {'Code': 'active'}, 'DNSName': f'{Name}.elb.floci'}
        self.balancers[Name] = balancer
        return {'LoadBalancers': [balancer]}

    def describe_load_balancers(self, Names):
        if Names[0] not in self.balancers:
            raise client_error('LoadBalancerNotFound')
        return {'LoadBalancers': [self.balancers[Names[0]]]}

    def delete_load_balancer(self, LoadBalancerArn):
        self.balancers = {k: v for k, v in self.balancers.items() if v['LoadBalancerArn'] != LoadBalancerArn}
        self.listeners.pop(LoadBalancerArn, None)

    def create_listener(self, LoadBalancerArn, Protocol, Port, DefaultActions):
        listener = {'ListenerArn': f'{LoadBalancerArn}/listener', 'Protocol': Protocol, 'Port': Port, 'DefaultActions': DefaultActions}
        self.listeners.setdefault(LoadBalancerArn, []).append(listener)
        return {'Listeners': [listener]}

    def describe_listeners(self, LoadBalancerArn):
        return {'Listeners': self.listeners.get(LoadBalancerArn, [])}

    # Auto Scaling
    def create_auto_scaling_group(self, AutoScalingGroupName, DesiredCapacity, VPCZoneIdentifier, **kwargs):
        if AutoScalingGroupName in self.asgs:
            raise client_error('AlreadyExists')
        self.asgs[AutoScalingGroupName] = {'AutoScalingGroupName': AutoScalingGroupName, 'DesiredCapacity': DesiredCapacity,
                                           'VPCZoneIdentifier': VPCZoneIdentifier, 'ids': [], **kwargs}
        self._reconcile()

    def _reconcile(self):
        for group in self.asgs.values():
            subnets = group['VPCZoneIdentifier'].split(',')
            template = self.templates[group['LaunchTemplate']['LaunchTemplateName']]
            while len(group['ids']) < group['DesiredCapacity']:
                instance_id = f'i-{next(self.ids):012x}'
                az = self.subnets[subnets[len(group['ids']) % len(subnets)]]['AvailabilityZone']
                self.instances[instance_id] = {'version': len(template['versions']), 'az': az}
                group['ids'].append(instance_id)
            while len(group['ids']) > group['DesiredCapacity']:
                self.instances.pop(group['ids'].pop(), None)

    def describe_auto_scaling_groups(self, AutoScalingGroupNames):
        out = []
        for name in AutoScalingGroupNames:
            group = self.asgs.get(name)
            if group:
                instances = [{'InstanceId': i, 'LifecycleState': 'InService', 'AvailabilityZone': self.instances[i]['az'],
                              'LaunchTemplate': {'Version': str(self.instances[i]['version'])}} for i in group['ids']]
                out.append({**{k: v for k, v in group.items() if k != 'ids'}, 'Instances': instances})
        return {'AutoScalingGroups': out}

    def set_desired_capacity(self, AutoScalingGroupName, DesiredCapacity):
        self.asgs[AutoScalingGroupName]['DesiredCapacity'] = DesiredCapacity
        self._reconcile()
        return {}

    def put_scaling_policy(self, AutoScalingGroupName, PolicyName, **kwargs):
        self.policies[PolicyName] = {'PolicyName': PolicyName, **kwargs}
        return {'PolicyARN': f'arn:policy/{PolicyName}'}

    def describe_policies(self, AutoScalingGroupName, PolicyNames):
        return {'ScalingPolicies': [self.policies[n] for n in PolicyNames if n in self.policies]}

    def start_instance_refresh(self, AutoScalingGroupName, Preferences):
        self.calls.append('start_instance_refresh')
        group = self.asgs[AutoScalingGroupName]
        for instance_id in list(group['ids']):
            self.instances.pop(instance_id)
        group['ids'] = []
        self._reconcile()
        self.refreshes.append({'InstanceRefreshId': f'r{len(self.refreshes)}', 'Status': 'Successful', 'StartTime': str(len(self.refreshes))})
        return {'InstanceRefreshId': self.refreshes[-1]['InstanceRefreshId']}

    def describe_instance_refreshes(self, AutoScalingGroupName):
        return {'InstanceRefreshes': list(self.refreshes)}

    def delete_auto_scaling_group(self, AutoScalingGroupName, ForceDelete):
        group = self.asgs.pop(AutoScalingGroupName)
        self.policies = {}  # Deleting a group deletes its scaling policies.
        for instance_id in group['ids']:
            self.instances.pop(instance_id, None)

    # SSM: requests through the load balancer answer from every instance
    def answers(self):
        lines = []
        group = next(iter(self.asgs.values()), None)
        template = self.templates.get(ha.LAUNCH_TEMPLATE_NAME)
        ids = group['ids'] if group else []
        for n in range(ha.PROBE_REQUESTS):
            instance_id = ids[n % len(ids)]
            version = self.instances[instance_id]['version']
            user_data = base64.b64decode(template['versions'][version - 1]['LaunchTemplateData']['UserData']).decode()
            release = ha.V2_BODY if ha.V2_BODY in user_data else ha.V1_BODY
            lines.append(f'{release} {instance_id}')
        return '\n'.join(lines) + '\n'


class HaWebTierLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeCloud()
        for target, kwargs in (
            ('dashboard.labs.elasticloadbalancing_labs._client', {'return_value': self.fake}),
            ('dashboard.labs.elasticloadbalancing_labs.time.sleep', {}),
            ('dashboard.labs.elasticloadbalancing_labs.run_instance_command',
             {'side_effect': lambda instance_id, command, **kw: {'command_id': 'c1', 'instance_id': instance_id}}),
            ('dashboard.labs.elasticloadbalancing_labs.instance_command_detail',
             {'side_effect': lambda instance_id, command_id: {'status': 'Success', 'stdout': self.fake.answers()}}),
        ):
            patcher = patch(target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Waits give up at once instead of polling.
        patcher = patch.multiple('dashboard.labs.elasticloadbalancing_labs', HEALTHY_WAIT_SECONDS=0, REFRESH_WAIT_SECONDS=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def lab(self):
        return next(item for item in labs_for_service(SERVICE) if item['key'] == LAB)

    def run_all(self):
        return {step['key']: run_lab_step(SERVICE, LAB, step['key']) for step in self.lab()['steps']}

    def test_lab_is_registered_with_twelve_steps_and_exam_tags(self):
        from .labs.exams import LAB_EXAM_TAGS

        self.assertEqual(len(self.lab()['steps']), 12)
        tags = LAB_EXAM_TAGS[(SERVICE, LAB)]
        primary = {(tag['exam'], tag['task']) for tag in tags if tag['primary']}
        self.assertIn(('SAA-C03', '2.2'), primary)
        self.assertIn(('SAA-C03', '3.2'), {(tag['exam'], tag['task']) for tag in tags})

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: value['verification']['message'] for key, value in results.items() if not value['verified']}
        self.assertEqual(failed, {})
        self.assertIn('us-east-1a, us-east-1b', results['create-auto-scaling-group']['verification']['message'])
        self.assertIn('answered by ha-web-v1', results['test-load-balancer']['verification']['message'])
        self.assertIn('lab-ha-web-alb.elb.floci', results['test-load-balancer']['command'])
        self.assertIn('answered by ha-web-v2', results['instance-refresh']['verification']['message'])
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'], status['steps'])
        self.assertEqual(status['passed_steps'], 12)

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        again = self.run_all()
        self.assertTrue(all(result['verified'] for result in again.values()), {k: v['verification'] for k, v in again.items()})
        self.assertEqual(len(self.fake.vpcs), 1)
        self.assertEqual(len(self.fake.templates[ha.LAUNCH_TEMPLATE_NAME]['versions']), 2)

    def test_instance_group_must_only_accept_the_load_balancer(self):
        run_lab_step(SERVICE, LAB, 'create-network')
        run_lab_step(SERVICE, LAB, 'create-security-groups')
        web = next(g for g in self.fake.groups.values() if g['GroupName'] == ha.WEB_SG_NAME)
        web['IpPermissions'].append({'IpProtocol': 'tcp', 'FromPort': 22, 'ToPort': 22, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]})
        result = run_lab_step(SERVICE, LAB, 'create-security-groups')
        self.assertFalse(result['verified'])
        self.assertIn('only lab-ha-web-alb-sg should reach it', result['verification']['message'])

    def test_unhealthy_targets_do_not_verify(self):
        for step in self.lab()['steps'][:6]:
            run_lab_step(SERVICE, LAB, step['key'])
        self.fake.healthy = False
        result = run_lab_step(SERVICE, LAB, 'wait-healthy-targets')
        self.assertFalse(result['verified'])
        self.assertIn('unhealthy', result['verification']['message'])

    def test_traffic_must_reach_every_instance(self):
        for step in self.lab()['steps'][:7]:
            run_lab_step(SERVICE, LAB, step['key'])
        only_one = f"{ha.V1_BODY} {next(iter(self.fake.asgs.values()))['ids'][0]}\n" * ha.PROBE_REQUESTS
        with patch('dashboard.labs.elasticloadbalancing_labs.instance_command_detail',
                   return_value={'status': 'Success', 'stdout': only_one}):
            result = run_lab_step(SERVICE, LAB, 'test-load-balancer')
        self.assertFalse(result['verified'])
        self.assertIn('not every instance', result['verification']['message'])

    def test_self_heal_terminates_one_instance_and_needs_a_replacement(self):
        for step in self.lab()['steps'][:10]:
            run_lab_step(SERVICE, LAB, step['key'])
        before = set(self.fake.asgs[ha.ASG_NAME]['ids'])
        result = run_lab_step(SERVICE, LAB, 'self-heal')
        self.assertTrue(result['verified'], result['verification'])
        after = set(self.fake.asgs[ha.ASG_NAME]['ids'])
        self.assertEqual(len(after), ha.SCALED_CAPACITY)
        self.assertEqual(len(before - after), 1)
        self.assertEqual(self.fake.calls.count('terminate_instances'), 1)

    def test_instance_refresh_fails_while_old_instances_remain(self):
        for step in self.lab()['steps'][:11]:
            run_lab_step(SERVICE, LAB, step['key'])
        with patch.object(self.fake, 'start_instance_refresh', return_value={}):
            self.fake.refreshes.append({'Status': 'InProgress', 'StartTime': '9'})
            result = run_lab_step(SERVICE, LAB, 'instance-refresh')
        self.assertFalse(result['verified'])
        self.assertIn('InProgress', result['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in ('vpcs', 'subnets', 'gateways', 'tables', 'groups', 'templates', 'target_groups', 'balancers', 'asgs', 'instances'):
            self.assertEqual(getattr(self.fake, store), {}, store)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_status_before_any_step_is_not_started(self):
        status = lab_status(SERVICE, LAB)
        self.assertEqual(status['status'], 'not_started')
        self.assertEqual(status['total_steps'], 12)
