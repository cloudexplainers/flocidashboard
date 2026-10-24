"""Edge delivery lab: private S3 origins, OAC and cache policies, the distribution, OAC bucket policies, serving and
invalidating through the edge, Global Accelerator, the traffic dial drill, and the CloudFront vs Global Accelerator
chooser. The fake behaves like Floci 2.3.0: the distribution routes requests by path without caching, anonymous S3
reads are served, behaviors may target missing origins, and Global Accelerator accepts any endpoint ID."""

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
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.cloudfront_labs')

SERVICE = 'cloudfront'
LAB = 'edge-delivery'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class Body:
    def __init__(self, data: bytes):
        self.data = data

    def read(self):
        return self.data


class FakeAws:
    def __init__(self):
        self.ids = itertools.count(1)
        self.buckets = {}  # name -> {'objects': {}, 'block': {}, 'policy': None}
        self.oacs, self.cache_policies, self.distributions, self.invalidations = {}, {}, {}, {}
        self.accelerators, self.listeners, self.groups = {}, {}, {}
        self.addresses = {}  # allocation id -> address with Region
        self.region = 'us-east-1'
        self.anonymous_reads = True

    def for_region(self, region):
        view = copy.copy(self)
        view.region = region or 'us-east-1'
        return view

    def _next(self, prefix):
        return f'{prefix}{next(self.ids):04d}'

    # S3
    def _bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        return self.buckets[Bucket]

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('404')

    def create_bucket(self, Bucket):
        self.buckets[Bucket] = {'objects': {}, 'block': {}, 'policy': None}

    def put_public_access_block(self, Bucket, PublicAccessBlockConfiguration):
        self._bucket(Bucket)['block'] = dict(PublicAccessBlockConfiguration)

    def get_public_access_block(self, Bucket):
        block = self._bucket(Bucket)['block']
        if not block:
            raise client_error('NoSuchPublicAccessBlockConfiguration')
        return {'PublicAccessBlockConfiguration': dict(block)}

    def put_object(self, Bucket, Key, Body, ContentType):
        self._bucket(Bucket)['objects'][Key] = Body

    def get_object(self, Bucket, Key):
        objects = self._bucket(Bucket)['objects']
        if Key not in objects:
            raise client_error('NoSuchKey')
        return {'Body': Body(objects[Key])}

    def list_objects_v2(self, Bucket):
        return {'Contents': [{'Key': key} for key in self._bucket(Bucket)['objects']]}

    def delete_object(self, Bucket, Key):
        self._bucket(Bucket)['objects'].pop(Key, None)

    def delete_bucket(self, Bucket):
        if self._bucket(Bucket)['objects']:
            raise client_error('BucketNotEmpty')
        del self.buckets[Bucket]

    def put_bucket_policy(self, Bucket, Policy):
        self._bucket(Bucket)['policy'] = Policy

    def get_bucket_policy(self, Bucket):
        policy = self._bucket(Bucket)['policy']
        if not policy:
            raise client_error('NoSuchBucketPolicy')
        return {'Policy': policy}

    # CloudFront
    def list_origin_access_controls(self):
        return {'OriginAccessControlList': {'Items': [
            {'Id': oac_id, **config} for oac_id, config in self.oacs.items()]}}

    def create_origin_access_control(self, OriginAccessControlConfig):
        oac_id = self._next('OAC')
        self.oacs[oac_id] = dict(OriginAccessControlConfig)
        return {'OriginAccessControl': {'Id': oac_id}}

    def get_origin_access_control(self, Id):
        return {'ETag': f'etag-{Id}', 'OriginAccessControl': {'Id': Id}}

    def delete_origin_access_control(self, Id, IfMatch):
        del self.oacs[Id]

    def list_cache_policies(self, Type):
        return {'CachePolicyList': {'Items': [
            {'Type': 'custom', 'CachePolicy': {'Id': policy_id, 'CachePolicyConfig': copy.deepcopy(config)}}
            for policy_id, config in self.cache_policies.items()]}}

    def create_cache_policy(self, CachePolicyConfig):
        policy_id = self._next('cp-')
        self.cache_policies[policy_id] = copy.deepcopy(CachePolicyConfig)
        return {'CachePolicy': {'Id': policy_id}}

    def get_cache_policy(self, Id):
        return {'ETag': f'etag-{Id}', 'CachePolicy': {'Id': Id, 'CachePolicyConfig': self.cache_policies[Id]}}

    def update_cache_policy(self, Id, IfMatch, CachePolicyConfig):
        self.cache_policies[Id] = copy.deepcopy(CachePolicyConfig)

    def delete_cache_policy(self, Id, IfMatch):
        del self.cache_policies[Id]

    def _distribution(self, Id):
        if Id not in self.distributions:
            raise client_error('NoSuchDistribution')
        return self.distributions[Id]

    def list_distributions(self):
        return {'DistributionList': {'Items': [
            {'Id': dist_id, 'Comment': item['config'].get('Comment'), 'DomainName': item['domain'],
             'Enabled': item['config'].get('Enabled')} for dist_id, item in self.distributions.items()]}}

    def get_distribution(self, Id):
        item = self._distribution(Id)
        return {'ETag': f'etag-{Id}', 'Distribution': {
            'Id': Id, 'ARN': f'arn:aws:cloudfront::000000000000:distribution/{Id}', 'Status': 'Deployed',
            'DomainName': item['domain'], 'DistributionConfig': copy.deepcopy(item['config'])}}

    def get_distribution_config(self, Id):
        return {'ETag': f'etag-{Id}', 'DistributionConfig': copy.deepcopy(self._distribution(Id)['config'])}

    def create_distribution(self, DistributionConfig):
        dist_id = self._next('E')
        self.distributions[dist_id] = {'config': copy.deepcopy(DistributionConfig), 'domain': f'{dist_id.lower()}.cloudfront.net'}
        return {'Distribution': {'Id': dist_id}}

    def update_distribution(self, Id, IfMatch, DistributionConfig):
        self._distribution(Id)['config'] = copy.deepcopy(DistributionConfig)

    def delete_distribution(self, Id, IfMatch):
        if self._distribution(Id)['config'].get('Enabled'):
            raise client_error('DistributionNotDisabled')
        del self.distributions[Id]

    def create_invalidation(self, DistributionId, InvalidationBatch):
        invalidation_id = self._next('I')
        self.invalidations[invalidation_id] = {'Id': invalidation_id, 'Status': 'Completed',
                                               'InvalidationBatch': InvalidationBatch}
        return {'Invalidation': {'Id': invalidation_id}}

    def get_invalidation(self, DistributionId, Id):
        return {'Invalidation': dict(self.invalidations[Id])}

    def edge_get(self, path, host):
        """Route a request like Floci's CloudFront data plane: by path, with no caching."""
        found = next((item for item in self.distributions.values() if item['domain'] == host), None)
        if not found or not found['config'].get('Enabled'):
            return 404, ''
        config = found['config']
        if path == '/':
            path = '/' + config.get('DefaultRootObject', '')
        behavior = config['DefaultCacheBehavior']
        for item in (config.get('CacheBehaviors') or {}).get('Items') or []:
            if path.startswith(item['PathPattern'].rstrip('*')):
                behavior = item
        origins = {item['Id']: item for item in config['Origins']['Items']}
        origin = origins.get(behavior['TargetOriginId'])
        if not origin:
            return 502, ''
        bucket = origin['DomainName'].split('.s3.')[0]
        objects = self.buckets.get(bucket, {}).get('objects', {})
        if path[1:] in objects:
            return 200, objects[path[1:]].decode()
        errors = {item['ErrorCode']: item for item in (config.get('CustomErrorResponses') or {}).get('Items') or []}
        if 404 in errors:
            default_bucket = origins[config['DefaultCacheBehavior']['TargetOriginId']]['DomainName'].split('.s3.')[0]
            page = self.buckets[default_bucket]['objects'].get(errors[404]['ResponsePagePath'][1:], b'')
            return int(errors[404]['ResponseCode']), page.decode()
        return 404, ''

    # Global Accelerator
    def list_accelerators(self):
        return {'Accelerators': [copy.deepcopy(item) for item in self.accelerators.values()]}

    def create_accelerator(self, Name, IpAddressType, Enabled, IdempotencyToken, Tags):
        arn = f'arn:aws:globalaccelerator::000000000000:accelerator/{self._next("acc-")}'
        self.accelerators[arn] = {'AcceleratorArn': arn, 'Name': Name, 'Enabled': Enabled, 'Status': 'DEPLOYED',
                                  'IpSets': [{'IpFamily': 'IPv4', 'IpAddresses': ['75.2.0.1', '99.83.0.1']}],
                                  'DnsName': 'a1.awsglobalaccelerator.com'}
        return {'Accelerator': copy.deepcopy(self.accelerators[arn])}

    def update_accelerator(self, AcceleratorArn, Enabled):
        self.accelerators[AcceleratorArn]['Enabled'] = Enabled

    def delete_accelerator(self, AcceleratorArn):
        if self.accelerators[AcceleratorArn]['Enabled']:
            raise client_error('AcceleratorNotDisabledException')
        if any(item['AcceleratorArn'] == AcceleratorArn for item in self.listeners.values()):
            raise client_error('AssociatedListenerFoundException')
        del self.accelerators[AcceleratorArn]

    def list_listeners(self, AcceleratorArn):
        return {'Listeners': [{key: value for key, value in item.items() if key != 'AcceleratorArn'}
                              for item in self.listeners.values() if item['AcceleratorArn'] == AcceleratorArn]}

    def create_listener(self, AcceleratorArn, PortRanges, Protocol, ClientAffinity, IdempotencyToken):
        arn = f'{AcceleratorArn}/listener/{self._next("l")}'
        self.listeners[arn] = {'ListenerArn': arn, 'AcceleratorArn': AcceleratorArn, 'PortRanges': PortRanges,
                               'Protocol': Protocol, 'ClientAffinity': ClientAffinity}
        return {'Listener': {key: value for key, value in self.listeners[arn].items() if key != 'AcceleratorArn'}}

    def update_listener(self, ListenerArn, PortRanges, Protocol, ClientAffinity):
        self.listeners[ListenerArn].update(PortRanges=PortRanges, Protocol=Protocol, ClientAffinity=ClientAffinity)
        return {'Listener': dict(self.listeners[ListenerArn])}

    def delete_listener(self, ListenerArn):
        if any(item['ListenerArn'] == ListenerArn for item in self.groups.values()):
            raise client_error('AssociatedEndpointGroupFoundException')
        del self.listeners[ListenerArn]

    def list_endpoint_groups(self, ListenerArn):
        return {'EndpointGroups': [copy.deepcopy(item) for item in self.groups.values()
                                   if item['ListenerArn'] == ListenerArn]}

    def _apply_group(self, group, settings):
        dial = settings.get('TrafficDialPercentage')
        if dial is not None and not 0 <= dial <= 100:
            raise client_error('InvalidArgumentException', 'TrafficDialPercentage must be between 0 and 100.')
        for key in ('TrafficDialPercentage', 'HealthCheckProtocol', 'HealthCheckPort', 'HealthCheckIntervalSeconds',
                    'ThresholdCount'):
            if key in settings:
                group[key] = settings[key]
        if 'EndpointConfigurations' in settings:
            group['EndpointDescriptions'] = [{'EndpointId': item['EndpointId'], 'Weight': item['Weight'],
                                              'HealthState': 'HEALTHY'} for item in settings['EndpointConfigurations']]

    def create_endpoint_group(self, ListenerArn, EndpointGroupRegion, IdempotencyToken, **settings):
        if any(item['ListenerArn'] == ListenerArn and item['EndpointGroupRegion'] == EndpointGroupRegion
               for item in self.groups.values()):
            raise client_error('EndpointGroupAlreadyExistsException')
        arn = f'{ListenerArn}/endpoint-group/{self._next("g")}'
        group = {'EndpointGroupArn': arn, 'ListenerArn': ListenerArn, 'EndpointGroupRegion': EndpointGroupRegion}
        self._apply_group(group, settings)
        self.groups[arn] = group

    def update_endpoint_group(self, EndpointGroupArn, **settings):
        self._apply_group(self.groups[EndpointGroupArn], settings)
        return {'EndpointGroup': copy.deepcopy(self.groups[EndpointGroupArn])}

    def delete_endpoint_group(self, EndpointGroupArn):
        del self.groups[EndpointGroupArn]

    # EC2 Elastic IPs
    def describe_addresses(self, Filters):
        names = Filters[0]['Values']
        return {'Addresses': [dict(item) for item in self.addresses.values()
                              if item['Region'] == self.region and item['Name'] in names]}

    def allocate_address(self, Domain, TagSpecifications):
        allocation = self._next('eipalloc-')
        name = next(tag['Value'] for tag in TagSpecifications[0]['Tags'] if tag['Key'] == 'Name')
        self.addresses[allocation] = {'AllocationId': allocation, 'PublicIp': f'3.0.0.{len(self.addresses) + 1}',
                                      'Region': self.region, 'Name': name}

    def release_address(self, AllocationId):
        del self.addresses[AllocationId]


class AnonymousS3:
    def __init__(self, fake):
        self.fake = fake

    def get_object(self, Bucket, Key):
        if not self.fake.anonymous_reads:
            raise client_error('AccessDenied')
        return self.fake.get_object(Bucket=Bucket, Key=Key)


class EdgeDeliveryLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        for target, value in (
                ('dashboard.labs.cloudfront_labs._client', lambda name, region=None: self.fake.for_region(region)),
                ('dashboard.labs.cloudfront_labs._http', lambda path, host: self.fake.edge_get(path, host)),
                ('dashboard.labs.cloudfront_labs.boto3.client', lambda *args, **kwargs: AnonymousS3(self.fake))):
            patcher = patch(target, side_effect=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.cloudfront_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def distribution(self):
        return next(iter(self.fake.distributions.values()))

    def test_lab_is_registered_with_nine_steps_exam_tags_and_a_batch(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 9)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '3.4'), tasks)
        self.assertIn(('SAA-C03', '4.4'), tasks)
        self.assertIn(('CLF-C02', '3.2'), tasks)
        self.assertIn(SERVICE, [batch['service'] for batch in LAB_BATCH_ORDER])

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_ttl_rules_traffic_split_and_chooser(self):
        self.assertEqual(lab.ttl_problem('p', {'MinTTL': 0, 'DefaultTTL': 300, 'MaxTTL': 3600}, 0, 300), '')
        self.assertIn('MinTTL <= DefaultTTL', lab.ttl_problem('p', {'MinTTL': 0, 'DefaultTTL': 900, 'MaxTTL': 60}))
        self.assertIn('at most 300', lab.ttl_problem('p', {'MinTTL': 0, 'DefaultTTL': 900, 'MaxTTL': 3600}, 0, 300))
        self.assertIn('at least 86400', lab.ttl_problem('p', {'MinTTL': 0, 'DefaultTTL': 60, 'MaxTTL': 3600}, 86400))
        self.assertIn('must set', lab.ttl_problem('p', {'DefaultTTL': 60}))
        both = [('us-east-1', 100, True), ('eu-west-1', 100, True)]
        self.assertEqual(lab.traffic_split(both), {'us-east-1': 100.0, 'eu-west-1': 0.0})
        self.assertEqual(lab.traffic_split([('us-east-1', 0, True), ('eu-west-1', 100, True)]),
                         {'us-east-1': 0.0, 'eu-west-1': 100.0})
        self.assertEqual(lab.traffic_split([('us-east-1', 40, True), ('eu-west-1', 100, True)]),
                         {'us-east-1': 40.0, 'eu-west-1': 60.0})
        self.assertEqual(lab.traffic_split([('us-east-1', 100, False), ('eu-west-1', 100, True)]),
                         {'us-east-1': 0.0, 'eu-west-1': 100.0})
        self.assertEqual(lab.traffic_split([('us-east-1', 50, True), ('eu-west-1', 0, True)]),
                         {'us-east-1': 100.0, 'eu-west-1': 0.0})
        self.assertEqual(lab.choose_edge('HTTPS', True, False), 'CloudFront')
        self.assertEqual(lab.choose_edge('UDP', False, False), 'Global Accelerator')
        self.assertEqual(lab.choose_edge('TCP', False, True), 'Global Accelerator')
        self.assertEqual(lab.choose_edge('HTTPS', False, True), 'Global Accelerator')

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 9)

    def test_messages_say_what_floci_skips(self):
        results = self.run_all()
        message = {key: result['verification']['message'] for key, result in results.items()}
        self.assertIn('anonymous read', message['private-origins'])
        self.assertIn('never caches', message['access-and-cache-policies'])
        self.assertIn('missing origin', message['distribution'])
        self.assertIn('without evaluating', message['origin-bucket-policies'])
        self.assertIn('custom 404 page', message['serve-through-edge'])
        self.assertIn('even before the invalidation', message['invalidate'])
        self.assertIn('HEALTHY', message['global-accelerator'])
        self.assertIn('refused (InvalidArgumentException)', message['regional-failover'])
        choices = [row['Choice'] for row in results['choose-edge-service']['json']['Choices']]
        self.assertEqual(choices, ['CloudFront', 'CloudFront', 'Global Accelerator', 'Global Accelerator',
                                   'Global Accelerator'])

    def test_blocked_anonymous_reads_need_no_note(self):
        self.fake.anonymous_reads = False
        result = run_lab_step(SERVICE, LAB, 'private-origins')
        self.assertTrue(result['verified'])
        self.assertNotIn('anonymous read', result['verification']['message'])
        self.assertTrue(result['json']['AnonymousRead'].startswith('refused'))

    def test_public_bucket_and_bad_ttls_fail(self):
        self.run_all()
        self.fake.buckets[lab.ASSETS_BUCKET]['block']['RestrictPublicBuckets'] = False
        self.assertIn('block all public access', lab._check_origins()[1])
        policy = next(config for config in self.fake.cache_policies.values() if config['Name'] == lab.HTML_POLICY)
        policy['DefaultTTL'] = 1800
        self.assertIn('at most 300', lab._check_policies()[1])

    def test_behavior_to_a_missing_origin_and_http_fail_the_distribution(self):
        self.run_all()
        config = self.distribution()['config']
        config['CacheBehaviors']['Items'][0]['TargetOriginId'] = 'nope'
        self.assertIn('missing origin nope', lab._check_distribution()[1])
        config['CacheBehaviors']['Items'][0]['TargetOriginId'] = lab.ASSETS_ORIGIN
        config['DefaultCacheBehavior']['ViewerProtocolPolicy'] = 'allow-all'
        self.assertIn('redirect HTTP to HTTPS', lab._check_distribution()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'distribution')['verified'])

    def test_bucket_policy_for_another_distribution_fails(self):
        self.run_all()
        policy = json.loads(self.fake.buckets[lab.SITE_BUCKET]['policy'])
        policy['Statement'][0]['Condition']['StringEquals']['AWS:SourceArn'] = 'arn:aws:cloudfront::1:distribution/X'
        self.fake.buckets[lab.SITE_BUCKET]['policy'] = json.dumps(policy)
        self.assertIn('AWS:SourceArn', lab._check_bucket_policies()[1])
        self.assertEqual(self.failed(), ['origin-bucket-policies', 'serve-through-edge'])

    def test_disabled_distribution_fails_serving(self):
        for step in lab.STEP_KEYS[:4]:
            run_lab_step(SERVICE, LAB, step)
        self.distribution()['config']['Enabled'] = False
        result = run_lab_step(SERVICE, LAB, 'serve-through-edge')
        self.assertFalse(result['verified'])
        self.assertIn('index.html', result['verification']['message'])

    def test_missing_elastic_ip_and_drained_dial_fail(self):
        self.run_all()
        dial_group = next(group for group in self.fake.groups.values() if group['EndpointGroupRegion'] == 'us-east-1')
        dial_group['TrafficDialPercentage'] = 0
        self.assertIn('back to 100', lab._check_failover()[1])
        dial_group['TrafficDialPercentage'] = 100
        dial_group['EndpointDescriptions'] = [{'EndpointId': 'eipalloc-missing', 'Weight': 100, 'HealthState': 'HEALTHY'}]
        self.assertIn('Elastic IP in us-east-1', lab._check_accelerator()[1])
        self.assertEqual(self.failed(), ['global-accelerator', 'regional-failover', 'choose-edge-service'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'global-accelerator')['verified'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len(self.fake.distributions), 1)
        self.assertEqual(len(self.fake.cache_policies), 2)
        self.assertEqual(len(self.fake.oacs), 1)
        self.assertEqual(len(self.fake.accelerators), 1)
        self.assertEqual(len(self.fake.groups), 2)
        self.assertEqual(len(self.fake.addresses), 2)

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'distribution')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'origin-bucket-policies')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'serve-through-edge')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'invalidate')['verification']['message'])
        self.assertIn('not found', run_lab_step(SERVICE, LAB, 'regional-failover')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'choose-edge-service')['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in (self.fake.buckets, self.fake.oacs, self.fake.cache_policies, self.fake.distributions,
                      self.fake.accelerators, self.fake.listeners, self.fake.groups, self.fake.addresses):
            self.assertFalse(store)
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
