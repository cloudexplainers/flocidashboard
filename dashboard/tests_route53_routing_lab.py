"""Route 53 routing and failover lab: hosted zones, health checks, routing policies, failover drill, private zone."""

import copy
import importlib
import itertools
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

r53 = importlib.import_module('dashboard.labs.route53_labs')

SERVICE = 'route53'
LAB = 'routing-failover'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


def fqdn(name: str) -> str:
    return name.rstrip('.') + '.'


class FakeAws:
    """Just enough Route 53 and EC2 for the lab. Like Floci 2.3.0, it stores records and health checks without
    validating routing rules, reports every health check healthy, and refuses only duplicate creates, mismatched
    deletes, and deleting a zone that still has records."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.zones, self.checks, self.vpcs, self.tags = {}, {}, {}, {}

    # Route 53 zones
    def create_hosted_zone(self, Name, CallerReference, HostedZoneConfig=None, VPC=None):
        zone_id = f'Z{next(self.ids):012d}'
        config = {'PrivateZone': bool((HostedZoneConfig or {}).get('PrivateZone') or VPC)}
        apex = fqdn(Name)
        self.zones[zone_id] = {
            'zone': {'Id': f'/hostedzone/{zone_id}', 'Name': apex, 'CallerReference': CallerReference, 'Config': config},
            'vpcs': [VPC] if VPC else [],
            'records': [
                {'Name': apex, 'Type': 'NS', 'TTL': 172800, 'ResourceRecords': [{'Value': f'ns-{i}.awsdns-0{i}.org.'} for i in range(1, 5)]},
                {'Name': apex, 'Type': 'SOA', 'TTL': 900, 'ResourceRecords': [{'Value': 'ns-1.awsdns-01.org. hostmaster. 1 7200 900 1209600 86400'}]},
            ],
        }
        return {'HostedZone': self.zones[zone_id]['zone']}

    def _zone(self, Id):
        zone_id = Id.rsplit('/', 1)[-1]
        if zone_id not in self.zones:
            raise client_error('NoSuchHostedZone')
        return self.zones[zone_id]

    def list_hosted_zones_by_name(self, DNSName=None):
        return {'HostedZones': [item['zone'] for item in self.zones.values()]}  # Floci lists every zone

    def get_hosted_zone(self, Id):
        zone = self._zone(Id)
        response = {'HostedZone': zone['zone'], 'DelegationSet': {'NameServers': [f'ns-{i}.awsdns-0{i}.org' for i in range(1, 5)]}}
        if zone['zone']['Config']['PrivateZone']:
            response['VPCs'] = list(zone['vpcs'])
        return response

    def associate_vpc_with_hosted_zone(self, HostedZoneId, VPC):
        self._zone(HostedZoneId)['vpcs'].append(VPC)

    def delete_hosted_zone(self, Id):
        zone = self._zone(Id)
        if any(record['Type'] not in {'NS', 'SOA'} for record in zone['records']):
            raise client_error('HostedZoneNotEmpty')
        del self.zones[Id.rsplit('/', 1)[-1]]

    def change_tags_for_resource(self, ResourceType, ResourceId, AddTags):
        self.tags.setdefault((ResourceType, ResourceId), {}).update({tag['Key']: tag['Value'] for tag in AddTags})

    # Records
    def list_resource_record_sets(self, HostedZoneId, **kwargs):
        return {'ResourceRecordSets': copy.deepcopy(self._zone(HostedZoneId)['records']), 'IsTruncated': False}

    def change_resource_record_sets(self, HostedZoneId, ChangeBatch):
        records = self._zone(HostedZoneId)['records']

        def key(record):
            return (fqdn(record['Name']).lower(), record['Type'], record.get('SetIdentifier'))
        for change in ChangeBatch['Changes']:
            record = {**copy.deepcopy(change['ResourceRecordSet']), 'Name': fqdn(change['ResourceRecordSet']['Name'])}
            for field in ('GeoLocation', 'MultiValueAnswer'):  # Floci drops these
                record.pop(field, None)
            match = next((item for item in records if key(item) == key(record)), None)
            if change['Action'] == 'CREATE' and match:
                raise client_error('InvalidChangeBatch', 'already exists')
            if change['Action'] == 'DELETE':
                if not match or match != record:
                    raise client_error('InvalidChangeBatch', 'not found or values do not match')
                records.remove(match)
                continue
            if match:
                records.remove(match)
            records.append(record)
        return {'ChangeInfo': {'Status': 'INSYNC'}}

    # Health checks
    def list_health_checks(self, **kwargs):
        return {'HealthChecks': copy.deepcopy(list(self.checks.values())), 'IsTruncated': False}

    def create_health_check(self, CallerReference, HealthCheckConfig):
        if any(check['CallerReference'] == CallerReference for check in self.checks.values()):
            raise client_error('HealthCheckAlreadyExists')
        check_id = f'{next(self.ids):08d}-hc'
        self.checks[check_id] = {'Id': check_id, 'CallerReference': CallerReference,
                                 'HealthCheckConfig': dict(HealthCheckConfig), 'HealthCheckVersion': 1}
        return {'HealthCheck': self.checks[check_id]}

    def delete_health_check(self, HealthCheckId):
        if HealthCheckId not in self.checks:
            raise client_error('NoSuchHealthCheck')
        del self.checks[HealthCheckId]

    def get_health_check_status(self, HealthCheckId):
        if HealthCheckId not in self.checks:
            raise client_error('NoSuchHealthCheck')
        return {'HealthCheckObservations': [{'Region': 'us-east-1', 'IPAddress': '1.2.3.4',
                                             'StatusReport': {'Status': 'Success: HTTP Status Code 200, OK'}}]}

    # EC2
    def describe_vpcs(self, Filters):
        names = Filters[0]['Values']
        return {'Vpcs': [vpc for vpc in self.vpcs.values() if vpc['Name'] in names]}

    def create_vpc(self, CidrBlock, TagSpecifications):
        vpc_id = f'vpc-{next(self.ids):08d}'
        name = next(tag['Value'] for tag in TagSpecifications[0]['Tags'] if tag['Key'] == 'Name')
        self.vpcs[vpc_id] = {'VpcId': vpc_id, 'Name': name, 'enableDnsSupport': True, 'enableDnsHostnames': False}
        return {'Vpc': self.vpcs[vpc_id]}

    def modify_vpc_attribute(self, VpcId, **kwargs):
        for attribute, value in kwargs.items():
            self.vpcs[VpcId][attribute[0].lower() + attribute[1:]] = value['Value']

    def describe_vpc_attribute(self, VpcId, Attribute):
        return {Attribute[0].upper() + Attribute[1:]: {'Value': self.vpcs[VpcId][Attribute]}, 'VpcId': VpcId}

    def delete_vpc(self, VpcId):
        del self.vpcs[VpcId]


class Route53RoutingLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        patcher = patch('dashboard.labs.route53_labs._client', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in r53.STEP_KEYS}

    def zone(self, private=False):
        return next(zone for zone in self.fake.zones.values() if zone['zone']['Config']['PrivateZone'] == private)

    def change(self, action, record, private=False):
        self.fake.change_resource_record_sets(self.zone(private)['zone']['Id'],
                                              {'Changes': [{'Action': action, 'ResourceRecordSet': record}]})

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def test_lab_is_registered_with_eight_steps_exam_tags_and_a_batch(self):
        lab = next(item for item in labs_for_service(SERVICE) if item['key'] == LAB)
        self.assertEqual(len(lab['steps']), 8)
        self.assertEqual(lab['steps'][0]['key'], 'create-hosted-zone')
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '2.2'), tasks)
        self.assertIn(('CLF-C02', '3.5'), tasks)
        self.assertIn(SERVICE, [batch['service'] for batch in LAB_BATCH_ORDER])

    def test_every_step_says_how_floci_emulates_it(self):
        for step in r53.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_the_finished_zones_have_the_designed_records(self):
        self.run_all()
        records = self.zone()['records']
        www = {record['Failover']: record for record in records if record['Name'] == fqdn(r53.WWW)}
        self.assertEqual(www['PRIMARY']['ResourceRecords'], [{'Value': r53.PRIMARY_IP}])
        self.assertEqual(www['SECONDARY']['ResourceRecords'], [{'Value': r53.SECONDARY_IP}])
        self.assertNotEqual(www['PRIMARY']['HealthCheckId'], www['SECONDARY']['HealthCheckId'])
        [alias] = [record for record in records if record['Name'] == fqdn(r53.ZONE) and record['Type'] == 'A']
        self.assertTrue(alias['AliasTarget']['EvaluateTargetHealth'])
        weights = {record['SetIdentifier']: record['Weight'] for record in records if record['Name'] == fqdn(r53.API)}
        self.assertEqual(weights, {'blue': 90, 'green': 10})
        private = self.zone(private=True)
        self.assertEqual(len(private['vpcs']), 1)
        inside = [record for record in private['records'] if record['Name'] == fqdn(r53.WWW)]
        self.assertEqual(inside[0]['ResourceRecords'], [{'Value': r53.INTERNAL_WWW_IP}])

    def test_drill_answers_primary_then_secondary_then_primary(self):
        results = self.run_all()
        scenarios = results['failover-drill']['json']['Scenarios']
        self.assertEqual(scenarios['AsFlociReports'][r53.WWW]['Answer'], [r53.PRIMARY_IP])
        self.assertEqual(scenarios['PrimaryFailing'][r53.WWW]['Answer'], [r53.SECONDARY_IP])
        self.assertEqual(scenarios['PrimaryFailing'][r53.ZONE]['Answer'], [r53.SECONDARY_IP])
        self.assertEqual(scenarios['BothFailing'][r53.WWW]['Answer'], [r53.PRIMARY_IP])
        self.assertIn('without probing', results['failover-drill']['verification']['message'])
        self.assertIn('without probing', results['create-health-checks']['verification']['message'])

    def test_resolver_rules(self):
        records = [
            {'Name': 'w.', 'Type': 'A', 'SetIdentifier': 'a', 'Weight': 0, 'ResourceRecords': [{'Value': '1'}], 'HealthCheckId': 'h1'},
            {'Name': 'w.', 'Type': 'A', 'SetIdentifier': 'b', 'Weight': 5, 'ResourceRecords': [{'Value': '2'}], 'HealthCheckId': 'h2'},
            {'Name': 'f.', 'Type': 'A', 'SetIdentifier': 'p', 'Failover': 'PRIMARY', 'ResourceRecords': [{'Value': '1'}], 'HealthCheckId': 'h1'},
            {'Name': 'f.', 'Type': 'A', 'SetIdentifier': 's', 'Failover': 'SECONDARY', 'ResourceRecords': [{'Value': '2'}]},
        ]
        # A secondary without a health check is always healthy.
        self.assertEqual(r53._answer(records, 'f', {'h1': False})['values'], ['2'])
        # A weighted group with every record unhealthy answers with all of them.
        self.assertEqual(r53._answer(records, 'w', {'h1': False, 'h2': False})['values'], ['1', '2'])
        self.assertEqual(r53._answer(records, 'w', {'h1': False, 'h2': True})['values'], ['2'])
        self.assertEqual(r53._weighted_shares(records[:2]), {'a': 0.0, 'b': 100.0})

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len(self.fake.zones), 2)
        self.assertEqual(len(self.fake.checks), 2)
        self.assertEqual(len(self.fake.vpcs), 1)

    def test_steps_out_of_order_explain_what_is_missing(self):
        result = run_lab_step(SERVICE, LAB, 'failover-records')
        self.assertFalse(result['verified'])
        self.assertIn('Create a public hosted zone', result['verification']['message'])
        run_lab_step(SERVICE, LAB, 'create-hosted-zone')
        self.assertIn('health checks first', run_lab_step(SERVICE, LAB, 'failover-records')['verification']['message'])
        self.assertIn('failover records first', run_lab_step(SERVICE, LAB, 'alias-apex')['verification']['message'])
        self.assertFalse(run_lab_step(SERVICE, LAB, 'failover-drill')['verified'])

    def test_second_primary_fails_and_the_step_repairs_it(self):
        self.run_all()
        self.change('CREATE', {'Name': r53.WWW, 'Type': 'A', 'TTL': 60, 'SetIdentifier': 'rogue', 'Failover': 'PRIMARY',
                               'ResourceRecords': [{'Value': '192.0.2.99'}]})
        self.assertEqual(self.failed(), ['failover-records', 'failover-drill'])
        self.assertIn('exactly one PRIMARY', r53._check_failover()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'failover-records')['verified'])

    def test_missing_health_check_and_long_ttl_fail(self):
        self.run_all()
        self.change('UPSERT', {**r53._failover_records('no-such-check', r53._health_check('secondary')['Id'])[0]})
        self.assertIn('does not exist', r53._check_failover()[1])
        primary = r53._failover_records(r53._health_check('primary')['Id'], r53._health_check('secondary')['Id'])[0]
        self.change('UPSERT', {**primary, 'TTL': 3600})
        self.assertIn('TTL', r53._check_failover()[1])

    def test_cname_at_apex_and_alias_to_missing_name_fail(self):
        self.run_all()
        self.change('CREATE', {'Name': r53.ZONE, 'Type': 'CNAME', 'TTL': 60, 'ResourceRecords': [{'Value': r53.WWW}]})
        self.assertIn('CNAME', r53._check_alias()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'alias-apex')['verified'])
        for record in [item for item in self.zone()['records'] if item['Name'] == fqdn(r53.WWW)]:
            self.change('DELETE', record)
        self.assertIn('no A records', r53._check_alias()[1])

    def test_out_of_range_weight_and_mixed_policies_fail(self):
        self.run_all()
        self.change('UPSERT', {'Name': r53.API, 'Type': 'A', 'TTL': 60, 'SetIdentifier': 'green', 'Weight': 300,
                               'ResourceRecords': [{'Value': '192.0.2.31'}]})
        self.assertIn('0 to 255', r53._check_weighted()[1])
        self.change('CREATE', {'Name': r53.APP, 'Type': 'A', 'TTL': 60, 'SetIdentifier': 'w', 'Weight': 5,
                               'ResourceRecords': [{'Value': '192.0.2.41'}]})
        self.assertIn('mixes routing policies', r53._check_latency()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'weighted-records')['verified'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'latency-records')['verified'])

    def test_invalid_health_check_is_replaced(self):
        self.run_all()
        check = r53._health_check('primary')
        self.fake.checks[check['Id']]['HealthCheckConfig']['RequestInterval'] = 20
        self.assertIn('not 10 or 30', r53._check_health_checks()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'create-health-checks')['verified'])
        self.assertNotEqual(r53._health_check('primary')['Id'], check['Id'])
        self.assertIn('failover-records', self.failed())
        self.assertTrue(run_lab_step(SERVICE, LAB, 'failover-records')['verified'])

    def test_drill_is_stale_after_records_change(self):
        self.run_all()
        self.change('UPSERT', {'Name': r53.API, 'Type': 'A', 'TTL': 60, 'SetIdentifier': 'green', 'Weight': 50,
                               'ResourceRecords': [{'Value': '192.0.2.31'}]})
        self.assertIn('changed since the drill', r53._check_drill()[1])

    def test_private_zone_needs_dns_hostnames(self):
        self.run_all()
        vpc = next(iter(self.fake.vpcs.values()))
        vpc['enableDnsHostnames'] = False
        self.assertEqual(self.failed(), ['private-hosted-zone'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'private-hosted-zone')['verified'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual((self.fake.zones, self.fake.checks, self.fake.vpcs), ({}, {}, {}))
        self.assertEqual(lab_status(SERVICE, LAB)['status'], 'not_started')

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
