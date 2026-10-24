"""Block and file storage lab: instances, gp3 and io2 volumes, format and mount over SSM, grow, move, EFS, and EFS
lifecycle. The fake behaves like Floci 2.3.0: attached volumes carry a filesystem that moves with them, attaching
across AZs and shrinking are refused, snapshots are unsupported, IOPS limits are not checked, and EFS mount targets
all report the same AZ."""

import copy
import importlib
import itertools
import json
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.ec2_storage_labs')

SERVICE = 'ec2'
LAB = 'block-file-storage'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


def _name(tags):
    return next((tag['Value'] for tag in tags or [] if tag['Key'] == 'Name'), None)


class FakeAws:
    def __init__(self):
        self.ids = itertools.count(1)
        self.subnets = {f'subnet-{az[-1]}': az for az in ('us-east-1a', 'us-east-1b', 'us-east-1c')}
        self.groups, self.instances, self.volumes, self.modifications = {}, {}, {}, {}
        self.mounts = {}  # instance id -> volume id mounted at /data
        self.file_systems, self.mount_targets, self.access_points = {}, {}, {}
        self.commands = {}
        self.online_resize = False

    def client(self, name):
        return self

    def _id(self, prefix):
        return f'{prefix}-{next(self.ids):04d}'

    # Subnets and security groups
    def describe_subnets(self, Filters=None, SubnetIds=None):
        items = [{'SubnetId': key, 'AvailabilityZone': az, 'DefaultForAz': True} for key, az in self.subnets.items()]
        if Filters:
            items = [item for item in items if item['AvailabilityZone'] in Filters[0]['Values']]
        if SubnetIds:
            items = [item for item in items if item['SubnetId'] in SubnetIds]
        return {'Subnets': items}

    def describe_security_groups(self, Filters):
        name = Filters[0]['Values'][0]
        return {'SecurityGroups': [copy.deepcopy(group) for group in self.groups.values() if group['GroupName'] == name]}

    def create_security_group(self, GroupName, Description, TagSpecifications):
        group_id = self._id('sg')
        self.groups[group_id] = {'GroupId': group_id, 'GroupName': GroupName, 'IpPermissions': []}
        return {'GroupId': group_id}

    def authorize_security_group_ingress(self, GroupId, IpPermissions):
        self.groups[GroupId]['IpPermissions'].extend(copy.deepcopy(IpPermissions))

    def delete_security_group(self, GroupId):
        if any(instance['State']['Name'] != 'terminated' and GroupId in instance['groups']
               for instance in self.instances.values()):
            raise client_error('DependencyViolation')
        self.groups.pop(GroupId)

    # Instances
    def run_instances(self, ImageId, InstanceType, MinCount, MaxCount, SubnetId, SecurityGroupIds, TagSpecifications):
        instance_id = self._id('i')
        self.instances[instance_id] = {
            'InstanceId': instance_id, 'State': {'Name': 'running'}, 'SubnetId': SubnetId,
            'Placement': {'AvailabilityZone': self.subnets[SubnetId]}, 'Tags': TagSpecifications[0]['Tags'],
            'groups': list(SecurityGroupIds)}

    def describe_instances(self, Filters):
        name, states = Filters[0]['Values'][0], Filters[1]['Values']
        found = [{key: copy.deepcopy(value) for key, value in instance.items() if key != 'groups'}
                 for instance in self.instances.values()
                 if _name(instance['Tags']) == name and instance['State']['Name'] in states]
        return {'Reservations': [{'Instances': found}] if found else []}

    def terminate_instances(self, InstanceIds):
        for instance_id in InstanceIds:
            self.instances[instance_id]['State'] = {'Name': 'terminated'}
            self.mounts.pop(instance_id, None)
            for volume in self.volumes.values():
                if volume['Attachments'] and volume['Attachments'][0]['InstanceId'] == instance_id:
                    volume['Attachments'], volume['State'] = [], 'available'

    # Volumes
    def create_volume(self, AvailabilityZone, VolumeType, Size, Encrypted, TagSpecifications, Iops=None, Throughput=None):
        volume_id = self._id('vol')
        self.volumes[volume_id] = {'VolumeId': volume_id, 'State': 'available', 'VolumeType': VolumeType, 'Size': Size,
                                   'Iops': Iops, 'Throughput': Throughput, 'Encrypted': Encrypted,
                                   'AvailabilityZone': AvailabilityZone, 'Tags': TagSpecifications[0]['Tags'],
                                   'Attachments': [], 'fs': None}

    def describe_volumes(self, Filters=None, VolumeIds=None):
        items = list(self.volumes.values())
        if Filters:
            items = [item for item in items if _name(item['Tags']) == Filters[0]['Values'][0]]
        if VolumeIds:
            items = [item for item in items if item['VolumeId'] in VolumeIds]
        return {'Volumes': [{key: copy.deepcopy(value) for key, value in item.items() if key != 'fs'} for item in items]}

    def attach_volume(self, VolumeId, InstanceId, Device):
        volume, instance = self.volumes[VolumeId], self.instances[InstanceId]
        if volume['AvailabilityZone'] != instance['Placement']['AvailabilityZone']:
            raise client_error('InvalidParameterValue', 'The volume and instance are not in the same Availability Zone.')
        if volume['Attachments']:
            raise client_error('VolumeInUse')
        volume['State'] = 'in-use'
        volume['Attachments'] = [{'InstanceId': InstanceId, 'Device': Device, 'State': 'attached', 'VolumeId': VolumeId}]

    def detach_volume(self, VolumeId, Force=False):
        volume = self.volumes[VolumeId]
        if not volume['Attachments']:
            raise client_error('InvalidVolume.NotAttached')
        instance_id = volume['Attachments'][0]['InstanceId']
        if self.mounts.get(instance_id) == VolumeId and not Force:
            raise client_error('VolumeInUse', 'still mounted')
        self.mounts.pop(instance_id, None)
        volume['Attachments'], volume['State'] = [], 'available'

    def delete_volume(self, VolumeId):
        volume = self.volumes[VolumeId]
        if volume['Attachments']:
            raise client_error('VolumeInUse')
        del self.volumes[VolumeId]

    def modify_volume(self, VolumeId, Size, Iops=None):
        volume = self.volumes[VolumeId]
        if Size < volume['Size']:
            raise client_error('InvalidParameterValue', 'New size cannot be smaller than existing size')
        self.modifications[VolumeId] = {'VolumeId': VolumeId, 'ModificationState': 'completed', 'TargetSize': Size}
        volume['Size'] = Size
        if Iops:
            volume['Iops'] = Iops

    def describe_volumes_modifications(self, VolumeIds):
        return {'VolumesModifications': [copy.deepcopy(self.modifications[key]) for key in VolumeIds
                                         if key in self.modifications]}

    def create_snapshot(self, VolumeId, Description, TagSpecifications):
        raise client_error('UnsupportedOperation', 'Operation CreateSnapshot is not supported.')

    # SSM Run Command, interpreting the lab's scripts against the fake disks
    def _attached(self, instance_id, device):
        return next((volume for volume in self.volumes.values() if volume['Attachments']
                     and volume['Attachments'][0]['InstanceId'] == instance_id
                     and volume['Attachments'][0]['Device'] == device), None)

    def _shell(self, instance_id, script):
        size = lambda volume: f'size_gb={volume["fs"]["size"]}'  # noqa: E731
        if script == lab.FORMAT_SCRIPT:
            volume = self._attached(instance_id, lab.DEVICE_1)
            if not volume:
                return 'Failed', '', 'mount: /data: special device /dev/sdf does not exist.'
            volume['fs'] = volume['fs'] or {'size': volume['Size'], 'files': {}}
            volume['fs']['files']['orders.csv'] = lab.ORDERS_LINE
            self.mounts[instance_id] = volume['VolumeId']
            return 'Success', f'{lab.ORDERS_LINE}\n{size(volume)}\n', ''
        if script == lab.GROW_SCRIPT:
            volume = self._attached(instance_id, lab.DEVICE_1)
            volume['fs']['size'] = volume['Size']
            return 'Success', f'{volume["fs"]["files"]["orders.csv"]}\n{size(volume)}\n', ''
        if script == lab.UNMOUNT_SCRIPT:
            self.mounts.pop(instance_id, None)
            return 'Success', 'unmounted\n', ''
        if script == lab.MOVED_SCRIPT:
            volume = self._attached(instance_id, lab.DEVICE_2)
            if not volume or not volume['fs']:
                return 'Failed', '', 'mount: wrong fs type, bad option, bad superblock on /dev/sdg'
            self.mounts[instance_id] = volume['VolumeId']
            return 'Success', f'{volume["fs"]["files"]["orders.csv"]}\n{size(volume)}\n', ''
        raise AssertionError(f'unexpected script: {script}')

    def send_command(self, InstanceIds, DocumentName, Parameters):
        command_id = self._id('cmd')
        status, out, err = self._shell(InstanceIds[0], Parameters['commands'][0])
        self.commands[command_id] = {'Status': status, 'StandardOutputContent': out, 'StandardErrorContent': err}
        return {'Command': {'CommandId': command_id}}

    def get_command_invocation(self, CommandId, InstanceId):
        return dict(self.commands[CommandId])

    # EFS
    def describe_file_systems(self, CreationToken):
        found = [copy.deepcopy(item) for item in self.file_systems.values() if item['CreationToken'] == CreationToken]
        if not found:
            raise client_error('FileSystemNotFound')
        return {'FileSystems': found}

    def create_file_system(self, CreationToken, PerformanceMode, ThroughputMode, Encrypted, Tags):
        file_system_id = self._id('fs')
        self.file_systems[file_system_id] = {
            'FileSystemId': file_system_id, 'CreationToken': CreationToken, 'LifeCycleState': 'available',
            'PerformanceMode': PerformanceMode, 'ThroughputMode': ThroughputMode, 'Encrypted': Encrypted,
            'FileSystemArn': f'arn:aws:elasticfilesystem:us-east-1:000000000000:file-system/{file_system_id}',
            'lifecycle': [], 'backup': 'DISABLED', 'policy': None}

    def delete_file_system(self, FileSystemId):
        if any(target['FileSystemId'] == FileSystemId for target in self.mount_targets.values()):
            raise client_error('FileSystemInUse')
        del self.file_systems[FileSystemId]

    def create_mount_target(self, FileSystemId, SubnetId, SecurityGroups):
        target_id = self._id('fsmt')
        self.mount_targets[target_id] = {'MountTargetId': target_id, 'FileSystemId': FileSystemId, 'SubnetId': SubnetId,
                                         'AvailabilityZoneName': 'us-east-1a', 'IpAddress': '10.0.0.10',
                                         'LifeCycleState': 'available', 'groups': list(SecurityGroups)}

    def describe_mount_targets(self, FileSystemId):
        return {'MountTargets': [{key: value for key, value in target.items() if key != 'groups'}
                                 for target in self.mount_targets.values() if target['FileSystemId'] == FileSystemId]}

    def describe_mount_target_security_groups(self, MountTargetId):
        return {'SecurityGroups': list(self.mount_targets[MountTargetId]['groups'])}

    def delete_mount_target(self, MountTargetId):
        del self.mount_targets[MountTargetId]

    def put_lifecycle_configuration(self, FileSystemId, LifecyclePolicies):
        self.file_systems[FileSystemId]['lifecycle'] = copy.deepcopy(LifecyclePolicies)

    def describe_lifecycle_configuration(self, FileSystemId):
        return {'LifecyclePolicies': copy.deepcopy(self.file_systems[FileSystemId]['lifecycle'])}

    def put_backup_policy(self, FileSystemId, BackupPolicy):
        self.file_systems[FileSystemId]['backup'] = BackupPolicy['Status']

    def describe_backup_policy(self, FileSystemId):
        return {'BackupPolicy': {'Status': self.file_systems[FileSystemId]['backup']}}

    def put_file_system_policy(self, FileSystemId, Policy):
        self.file_systems[FileSystemId]['policy'] = Policy

    def describe_file_system_policy(self, FileSystemId):
        policy = self.file_systems[FileSystemId]['policy']
        if not policy:
            raise client_error('PolicyNotFound')
        return {'Policy': policy}

    def create_access_point(self, FileSystemId, PosixUser, RootDirectory, Tags):
        point_id = self._id('fsap')
        self.access_points[point_id] = {'AccessPointId': point_id, 'FileSystemId': FileSystemId,
                                        'PosixUser': PosixUser, 'RootDirectory': RootDirectory}

    def describe_access_points(self, FileSystemId):
        return {'AccessPoints': [copy.deepcopy(point) for point in self.access_points.values()
                                 if point['FileSystemId'] == FileSystemId]}

    def delete_access_point(self, AccessPointId):
        del self.access_points[AccessPointId]


class BlockFileStorageLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        patcher = patch('dashboard.labs.ec2_storage_labs._client', side_effect=lambda name: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.ec2_storage_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def gp3(self):
        return next(volume for volume in self.fake.volumes.values() if _name(volume['Tags']) == lab.GP3_NAME)

    def test_lab_is_registered_with_eight_steps_and_exam_tags(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 8)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '3.1'), tasks)
        self.assertIn(('SAA-C03', '4.1'), tasks)
        self.assertIn(('CLF-C02', '3.6'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_limits_and_costs(self):
        self.assertIsNone(lab.gp3_limits(20, 3000, 125))
        self.assertIn('10,000', lab.gp3_limits(20, 20000, 125))
        self.assertIn('MiB/s', lab.gp3_limits(40, 3000, 1000))
        self.assertIsNone(lab.io2_limits(100, 16000))
        self.assertIn('20,000', lab.io2_limits(20, 64000))
        self.assertEqual(lab.monthly_cost('gp3', 20, 3000), 1.6)
        self.assertEqual(lab.monthly_cost('gp3', 100, 16000), 73.0)
        self.assertEqual(lab.monthly_cost('io2', 100, 16000), 1052.5)

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_the_data_moves_and_messages_say_what_floci_skips(self):
        results = self.run_all()
        self.assertIn('40 GiB', results['grow-volume']['verification']['message'])
        self.assertIn('Shrinking it back was refused', results['grow-volume']['verification']['message'])
        self.assertIn('UnsupportedOperation', results['move-volume']['verification']['message'])
        app_2 = next(item for item in self.fake.instances.values() if _name(item['Tags']) == lab.APP_2)
        self.assertEqual(self.gp3()['Attachments'][0]['InstanceId'], app_2['InstanceId'])
        self.assertIn('same AZ and IP', results['efs-file-system']['verification']['message'])
        self.assertIn('$73.00', results['io2-volume']['verification']['message'])

    def test_over_limit_throughput_and_open_nfs_fail(self):
        self.run_all()
        self.gp3()['Throughput'] = 2000
        self.assertIn('MiB/s', lab._check_gp3()[1])
        efs_group = next(group for group in self.fake.groups.values() if group['GroupName'] == lab.EFS_SG)
        efs_group['IpPermissions'].append({'IpProtocol': 'tcp', 'FromPort': 2049, 'ToPort': 2049,
                                           'IpRanges': [{'CidrIp': '0.0.0.0/0'}]})
        self.assertIn('only from', lab._check_efs()[1])
        self.assertEqual(self.failed(), ['gp3-volume', 'efs-file-system'])

    def test_mount_targets_in_one_az_fail(self):
        self.run_all()
        target = next(item for item in self.fake.mount_targets.values() if item['SubnetId'] == 'subnet-b')
        target['SubnetId'] = 'subnet-a'
        self.assertIn('us-east-1b', lab._check_efs()[1])

    def test_missing_tls_policy_and_lifecycle_fail(self):
        self.run_all()
        file_system = next(iter(self.fake.file_systems.values()))
        file_system['policy'] = None
        self.assertIn('TLS', lab._check_lifecycle()[1])
        file_system['lifecycle'] = [{'TransitionToIA': 'AFTER_7_DAYS'}]
        self.assertIn('30 days', lab._check_lifecycle()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'efs-lifecycle')['verified'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len([item for item in self.fake.instances.values() if item['State']['Name'] == 'running']), 2)
        self.assertEqual(len(self.fake.volumes), 2)
        self.assertEqual(len(self.fake.mount_targets), 2)
        self.assertEqual(len(self.fake.access_points), 1)

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'attach-and-format')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'grow-volume')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'move-volume')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'efs-file-system')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'efs-lifecycle')['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertFalse(self.fake.volumes)
        self.assertFalse(self.fake.groups)
        self.assertFalse(self.fake.file_systems)
        self.assertFalse(self.fake.mount_targets)
        self.assertTrue(all(item['State']['Name'] == 'terminated' for item in self.fake.instances.values()))
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
        self.assertEqual(json.loads(result['stdout'])['status'], 'reset')
