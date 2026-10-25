"""Network scale-out lab: Floci stores peering, Transit Gateway and flow log settings but moves no packets, so the lab
works out routing itself. These tests cover that logic, the checks that cope with Floci's read-back gaps, and the
lab's registration."""

import importlib
from unittest.mock import patch

from django.test import SimpleTestCase

from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.ec2_network_labs')

SHARED, APP, DATA = lab.SHARED, lab.APP, lab.DATA


def route(cidr, **target):
    return {'DestinationCidrBlock': cidr, **target}


class CidrTests(SimpleTestCase):
    def test_lab_ranges_do_not_overlap_and_fit_the_summary(self):
        self.assertEqual(lab.overlapping({name: spec['cidr'] for name, spec in lab.VPCS.items()}), [])
        import ipaddress
        summary = ipaddress.ip_network(lab.SUMMARY_CIDR)
        for spec in lab.VPCS.values():
            self.assertTrue(ipaddress.ip_network(spec['cidr']).subnet_of(summary))

    def test_overlap_is_found(self):
        self.assertEqual(lab.overlapping({APP: '10.41.0.0/16', 'other': '10.41.128.0/17'}), [(APP, 'other')])


class VpcRoutingTests(SimpleTestCase):
    routes = {
        SHARED: [route('10.40.0.0/16', GatewayId='local'), route('10.41.0.0/16', VpcPeeringConnectionId='pcx-1'),
                 route(lab.SUMMARY_CIDR)],
        APP: [route('10.41.0.0/16', GatewayId='local'), route('10.40.0.0/16', VpcPeeringConnectionId='pcx-1'),
              route(lab.SUMMARY_CIDR)],
        DATA: [route('10.42.0.0/16', GatewayId='local'), route(lab.SUMMARY_CIDR)],
    }
    peerings = {'pcx-1': (SHARED, APP)}

    def test_longest_prefix_prefers_the_peering_route(self):
        self.assertEqual(lab.next_hop(self.routes[APP], '10.40.1.10')['VpcPeeringConnectionId'], 'pcx-1')
        self.assertEqual(lab.next_hop(self.routes[APP], '10.42.1.30')['DestinationCidrBlock'], lab.SUMMARY_CIDR)
        self.assertIsNone(lab.next_hop(self.routes[APP], '192.168.0.1'))

    def test_peering_works_both_ways(self):
        self.assertTrue(lab.peering_reachable(SHARED, APP, self.routes, self.peerings))

    def test_peering_is_not_transitive(self):
        routes = {**self.routes, APP: [*self.routes[APP], route('10.42.0.0/16', VpcPeeringConnectionId='pcx-1')]}
        self.assertFalse(lab.peering_reachable(APP, DATA, routes, self.peerings))

    def test_one_sided_route_is_not_enough(self):
        routes = {**self.routes, APP: [route('10.41.0.0/16', GatewayId='local')]}
        self.assertFalse(lab.peering_reachable(SHARED, APP, routes, self.peerings))

    def test_summary_route_without_target_counts_but_other_targets_do_not(self):
        self.assertIsNotNone(lab._summary_route({'Routes': [route(lab.SUMMARY_CIDR)]}, 'tgw-1'))
        self.assertIsNotNone(lab._summary_route({'Routes': [route(lab.SUMMARY_CIDR, TransitGatewayId='tgw-1')]}, 'tgw-1'))
        self.assertIsNone(lab._summary_route({'Routes': [route(lab.SUMMARY_CIDR, TransitGatewayId='tgw-2')]}, 'tgw-1'))
        self.assertIsNone(lab._summary_route({'Routes': [route(lab.SUMMARY_CIDR, GatewayId='igw-1')]}, 'tgw-1'))


class TransitGatewayTests(SimpleTestCase):
    def test_lab_design_isolates_the_spokes(self):
        self.assertEqual(lab.tgw_reachability(lab.TGW_TABLES), lab.EXPECTED_REACH)

    def test_one_shared_table_connects_everything(self):
        flat = {'all': {'associations': list(lab.VPCS),
                        'routes': {spec['cidr']: name for name, spec in lab.VPCS.items()}}}
        self.assertTrue(all(lab.tgw_reachability(flat).values()))

    def test_return_route_is_required(self):
        tables = {lab.SHARED_RT: lab.TGW_TABLES[lab.SHARED_RT],
                  lab.SPOKES_RT: {'associations': [APP, DATA], 'routes': {}}}
        self.assertFalse(lab.tgw_reachability(tables)[(SHARED, APP)])

    def test_blackhole_beats_a_wider_route(self):
        table = {'associations': [APP], 'routes': {lab.SUMMARY_CIDR: SHARED, '10.42.0.0/16': None}}
        self.assertIsNone(lab.tgw_lookup(table, '10.42.1.30'))
        self.assertEqual(lab.tgw_lookup(table, '10.40.1.10'), SHARED)

    def test_segmentation_check_reads_the_model(self):
        model = {name: {**spec, 'associations': sorted(spec['associations']), 'propagated': []}
                 for name, spec in lab.TGW_TABLES.items()}
        with patch.object(lab, '_tgw', return_value={'TransitGatewayId': 'tgw-1'}), \
                patch.object(lab, '_tgw_table_model', return_value=model):
            self.assertTrue(lab._check_segmentation()[0])
        broken = {**model, lab.SPOKES_RT: {**model[lab.SPOKES_RT],
                                           'routes': {**model[lab.SPOKES_RT]['routes'], '10.42.0.0/16': DATA}}}
        with patch.object(lab, '_tgw', return_value={'TransitGatewayId': 'tgw-1'}), \
                patch.object(lab, '_tgw_table_model', return_value=broken):
            ok, message = lab._check_segmentation()
        self.assertFalse(ok)
        self.assertIn('blackhole', message)


class FlowLogTests(SimpleTestCase):
    def test_sample_analysis(self):
        analysis = lab.analyze_flow_records(lab.SAMPLE_RECORDS)
        self.assertEqual((analysis['Records'], analysis['Rejected']), (6, 3))
        self.assertEqual(analysis['RejectedPorts'], {'22': 2, '3389': 1})
        self.assertEqual(analysis['RejectedSources'], ['198.51.100.7', '203.0.113.50'])

    def flow_check(self, logs):
        policy = lab.FLOW_BUCKET_POLICY
        with patch.object(lab, '_vpcs', return_value={APP: {'VpcId': 'vpc-app'}}), \
                patch.object(lab, '_flow_logs', return_value=logs), \
                patch.object(lab, '_bucket_policy', return_value=policy), \
                patch.object(lab, '_role_exists', return_value=True):
            return lab._check_flow_logs()

    s3_log = {'LogDestinationType': 's3', 'LogDestination': f'arn:aws:s3:::{lab.FLOW_BUCKET}/{lab.FLOW_PREFIX}',
              'TrafficType': 'ALL', 'LogFormat': lab.FLOW_FORMAT, 'MaxAggregationInterval': 60}
    cw_log = {'LogDestinationType': 'cloud-watch-logs', 'TrafficType': 'REJECT',
              'DeliverLogsPermissionArn': lab.FLOW_ROLE_ARN}

    def test_cloudwatch_flow_log_without_group_name_counts(self):
        self.assertTrue(self.flow_check([self.s3_log, self.cw_log])[0])

    def test_wrong_group_or_format_fails(self):
        self.assertFalse(self.flow_check([self.s3_log, {**self.cw_log, 'LogGroupName': '/other'}])[0])
        self.assertFalse(self.flow_check([{**self.s3_log, 'LogFormat': '${version}'}, self.cw_log])[0])


class CostTests(SimpleTestCase):
    def test_mesh_grows_quadratically(self):
        self.assertEqual([lab.mesh_connections(n) for n in (2, 3, 10, 50)], [1, 3, 45, 1225])

    def test_costs(self):
        small = lab.network_costs(3, 1000)
        self.assertEqual((small['PeeringMonthlyUSD'], small['TgwMonthlyUSD']), (20.0, 129.5))
        large = lab.network_costs(10, 1000)
        self.assertEqual((large['TgwMonthlyUSD'], large['PeeringConnections']), (385.0, 45))

    def test_choice(self):
        self.assertEqual(lab.choose_network(3), 'VPC peering')
        self.assertEqual(lab.choose_network(10), 'Transit Gateway')
        self.assertEqual(lab.choose_network(2, shared_vpn=True), 'Transit Gateway')


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('network-scale-out', [item['key'] for item in labs_for_service('ec2')])
        self.assertIn(('ec2', 'network-scale-out'), LAB_EXAM_TAGS)

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('nope')
