"""Performance tuning lab: Floci limits each instance to its type's vCPUs and memory, so throttling and out-of-memory
kills are real, but it keeps a resized instance's old limits, records volume changes without checking gp3 limits, and
stores RDS and S3 performance settings. These tests cover parsing, the recommendation, the volume math, and the checks."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.ec2_performance_labs')

CATALOG = [
    {'InstanceType': 't3.micro', 'VCpuInfo': {'DefaultVCpus': 2}, 'MemoryInfo': {'SizeInMiB': 1024},
     'ProcessorInfo': {'SupportedArchitectures': ['x86_64']}},
    {'InstanceType': 't2.xlarge', 'VCpuInfo': {'DefaultVCpus': 4}, 'MemoryInfo': {'SizeInMiB': 16384},
     'ProcessorInfo': {'SupportedArchitectures': ['x86_64']}},
    {'InstanceType': 't2.2xlarge', 'VCpuInfo': {'DefaultVCpus': 8}, 'MemoryInfo': {'SizeInMiB': 32768},
     'ProcessorInfo': {'SupportedArchitectures': ['x86_64']}},
    {'InstanceType': 'm6g.xlarge', 'VCpuInfo': {'DefaultVCpus': 4}, 'MemoryInfo': {'SizeInMiB': 8192},
     'ProcessorInfo': {'SupportedArchitectures': ['arm64']}},
]


class MeasurementTests(SimpleTestCase):
    def test_limits_from_cgroup_v1_and_v2(self):
        self.assertEqual(lab.parse_limits('cpu_quota=200000\ncpu_period=100000\nmem_limit=1073741824'),
                         {'vcpus': 2.0, 'memory_mib': 1024})
        self.assertEqual(lab.parse_limits('cpu_quota=max\ncpu_period=100000\nmem_limit=max'),
                         {'vcpus': None, 'memory_mib': None})
        self.assertEqual(lab.parse_limits('cpu_quota=-1\ncpu_period=100000\nmem_limit=9223372036854771712'),
                         {'vcpus': None, 'memory_mib': None})

    def test_cpu_percent_is_a_share_of_the_vcpus(self):
        sample = lab.cpu_percent('cpu_ns=30000000000\nwall_ns=10000000000\nthrottled=4', 4)
        self.assertEqual(sample, {'cpu': 75.0, 'throttled': 4.0})
        self.assertEqual(lab.cpu_percent('cpu_ns=30000000000\nwall_ns=10000000000', 2)['cpu'], 100.0)
        self.assertIsNone(lab.cpu_percent('', 2))

    def test_report_outcome(self):
        self.assertEqual(lab.report_outcome('exit=137\noom=1'), {'finished': False, 'exit': '137', 'oom_kills': 1})
        self.assertTrue(lab.report_outcome('report=done\nexit=0\noom=0')['finished'])

    def test_scripts_read_both_cgroup_versions(self):
        for script in (lab.LIMITS_SCRIPT, lab.WORKLOAD_SCRIPT, lab.REPORT_SCRIPT):
            self.assertIn('/sys/fs/cgroup/cpu.max', script)
        self.assertIn('memory.events', lab.REPORT_SCRIPT)
        self.assertIn('memory.oom_control', lab.REPORT_SCRIPT)


class SizingTests(SimpleTestCase):
    def test_recommendation_is_the_smallest_x86_type_that_fits(self):
        choice = lab.recommend(CATALOG)
        self.assertEqual(choice['type'], 't2.xlarge')
        self.assertEqual(choice['vcpus_needed'], 4)
        self.assertEqual(choice['memory_needed_mib'], 1500)
        self.assertIsNone(lab.recommend(CATALOG[:1]))

    def test_gp2_and_gp3_math(self):
        self.assertEqual(lab.gp2_baseline_iops(100), 300)
        self.assertEqual(lab.gp2_baseline_iops(10), 100)
        self.assertEqual(lab.gp2_size_for(4000), 1334)
        self.assertEqual(lab.gp3_problems(100, 4000, 250), [])
        self.assertTrue(lab.gp3_problems(100, lab.INVALID_IOPS, 250))
        self.assertTrue(lab.gp3_problems(100, 4000, 1001))
        self.assertTrue(lab.gp3_problems(5, 4000, 250))

    def test_parts_and_bucket_names(self):
        self.assertEqual(lab.part_count('"abc-3"'), 3)
        self.assertEqual(lab.part_count('"abc"'), 1)
        self.assertTrue(lab.accelerate_name_ok(lab.BUCKET))
        self.assertFalse(lab.accelerate_name_ok('my.bucket'))


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_cpu_check_needs_saturation_and_the_alarm(self):
        lab._marker('cpu-saturation', {'sample': {'cpu': 100.0, 'throttled': 50}, 'alarm': 'ALARM'})
        self.assertTrue(lab._check_cpu()[0])
        lab._marker('cpu-saturation', {'sample': {'cpu': 60.0}, 'alarm': 'ALARM'})
        self.assertFalse(lab._check_cpu()[0])

    def test_memory_check_needs_the_kill_and_the_alarm(self):
        lab._marker('memory-pressure', {'outcome': {'finished': False, 'oom_kills': 1, 'exit': '137'}, 'alarm': 'ALARM'})
        self.assertTrue(lab._check_memory()[0])
        lab._marker('memory-pressure', {'outcome': {'finished': True, 'oom_kills': 0}, 'alarm': 'OK'})
        self.assertFalse(lab._check_memory()[0])

    def test_verify_check_needs_the_alarms_on_the_new_instance(self):
        alarms = {name: {'Dimensions': [{'Name': 'InstanceId', 'Value': 'i-new'}]} for name in (lab.CPU_ALARM,
                                                                                                lab.OOM_ALARM)}
        lab._marker('verify-right-size', {'sample': {'cpu': 74.0}, 'outcome': {'finished': True},
                                          'states': {lab.CPU_ALARM: 'OK', lab.OOM_ALARM: 'OK'}})
        with patch.object(lab, '_instance', return_value={'InstanceId': 'i-new'}), \
                patch.object(lab, '_alarms', return_value=alarms):
            self.assertTrue(lab._check_verified()[0])
        with patch.object(lab, '_instance', return_value={'InstanceId': 'i-old'}), \
                patch.object(lab, '_alarms', return_value=alarms):
            self.assertFalse(lab._check_verified()[0])

    def test_s3_check(self):
        lab._marker('s3-transfer', {'accelerate': 'Enabled', 'bigParts': 3, 'smallParts': 1, 'rangeBytes': lab.MIB})
        self.assertTrue(lab._check_s3()[0])
        lab._marker('s3-transfer', {'accelerate': 'Enabled', 'bigParts': 1, 'smallParts': 1, 'rangeBytes': lab.MIB})
        self.assertFalse(lab._check_s3()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('performance-tuning', [item['key'] for item in labs_for_service('ec2')])
        tags = LAB_EXAM_TAGS[('ec2', 'performance-tuning')]
        self.assertIn({'exam': 'SOA-C03', 'task': '1.3', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'performance-optimization')
        self.assertIn(('ec2', 'performance-tuning'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
