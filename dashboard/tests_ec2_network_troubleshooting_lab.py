"""Network troubleshooting lab: Floci stores ACLs, routes, gateways, flow logs, Resolver rules and cache policies
without moving packets, so the lab evaluates every path itself. These tests cover the ACL evaluation, flow records,
route tracing, access log reading, DNS resolution, cache keys, and registration."""

import importlib

from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.ec2_network_troubleshooting_labs')

DEFAULT_DENY = [
    {'RuleNumber': 32767, 'Protocol': '-1', 'RuleAction': 'deny', 'Egress': False, 'CidrBlock': '0.0.0.0/0'},
    {'RuleNumber': 32767, 'Protocol': '-1', 'RuleAction': 'deny', 'Egress': True, 'CidrBlock': '0.0.0.0/0'},
]


def _rule(number, egress, start, end, action='allow'):
    return {'RuleNumber': number, 'Protocol': '6', 'RuleAction': action, 'Egress': egress, 'CidrBlock': '0.0.0.0/0',
            'PortRange': {'From': start, 'To': end}}


BROKEN = DEFAULT_DENY + [_rule(100, False, 443, 443), _rule(100, True, 443, 443)]
FIXED = BROKEN + [_rule(110, True, 1024, 65535)]


class AclTests(SimpleTestCase):
    def test_reply_hits_the_final_deny_until_ephemeral_ports_are_allowed(self):
        broken = lab.https_session(BROKEN)
        self.assertEqual(broken['request'], {'action': 'allow', 'rule': 100})
        self.assertEqual(broken['reply'], {'action': 'deny', 'rule': 32767})
        self.assertFalse(broken['works'])
        self.assertTrue(lab.https_session(FIXED)['works'])

    def test_lowest_rule_number_wins(self):
        entries = BROKEN + [_rule(90, False, 443, 443, 'deny')]
        self.assertEqual(lab.acl_decision(entries, False, 443, lab.CLIENT_IP)['action'], 'deny')

    def test_no_match_is_denied(self):
        self.assertEqual(lab.acl_decision([], True, 51544, lab.CLIENT_IP), {'action': 'deny', 'rule': '*'})


class FlowLogTests(SimpleTestCase):
    def test_accept_in_and_reject_out_means_network_acl(self):
        records = lab.flow_records(lab.https_session(BROKEN), 'eni-1', '10.62.1.25', 0)
        rows = lab.parse_flow(records)
        self.assertEqual([(row['flow-direction'], row['action']) for row in rows],
                         [('ingress', 'ACCEPT'), ('egress', 'REJECT')])
        self.assertEqual(lab.diagnose_flows(rows), 'network-acl')

    def test_fixed_acl_accepts_both_ways(self):
        rows = lab.parse_flow(lab.flow_records(lab.https_session(FIXED), 'eni-1', '10.62.1.25', 0))
        self.assertEqual({row['action'] for row in rows}, {'ACCEPT'})
        self.assertEqual(lab.diagnose_flows(rows), 'none')

    def test_inbound_reject_writes_one_record(self):
        session = lab.https_session(DEFAULT_DENY)
        rows = lab.parse_flow(lab.flow_records(session, 'eni-1', '10.62.1.25', 0))
        self.assertEqual(len(rows), 1)
        self.assertEqual(lab.diagnose_flows(rows), 'security-group-or-acl-inbound')


class RouteTests(SimpleTestCase):
    LOCAL = {'DestinationCidrBlock': '10.62.0.0/16', 'GatewayId': 'local', 'State': 'active'}

    def test_longest_prefix_wins(self):
        routes = [self.LOCAL, {'DestinationCidrBlock': '0.0.0.0/0', 'GatewayId': 'igw-1', 'State': 'active'}]
        self.assertEqual(lab.route_target(routes, '10.62.2.5'), 'local')
        self.assertEqual(lab.route_target(routes, lab.MIRROR_IP), 'igw-1')

    def test_private_subnet_reaches_out_only_through_the_nat_gateway(self):
        tables = {'subnet-app': [self.LOCAL], 'subnet-web': [
            self.LOCAL, {'DestinationCidrBlock': '0.0.0.0/0', 'GatewayId': 'igw-1', 'State': 'active'}]}
        before = lab.egress_path(tables, 'subnet-app', lab.MIRROR_IP, {})
        self.assertEqual(before['reason'], 'no route')
        tables['subnet-app'].append({'DestinationCidrBlock': '0.0.0.0/0', 'NatGatewayId': 'nat-1',
                                     'State': 'active'})
        after = lab.egress_path(tables, 'subnet-app', lab.MIRROR_IP, {'nat-1': 'subnet-web'})
        self.assertTrue(after['reaches'])
        self.assertEqual(after['hops'], ['subnet-app', 'nat-1', 'subnet-web', 'igw-1'])

    def test_egress_only_gateway_blocks_inbound(self):
        eigw = [{'DestinationIpv6CidrBlock': '::/0', 'EgressOnlyInternetGatewayId': 'eigw-1', 'State': 'active'}]
        igw = [{'DestinationIpv6CidrBlock': '::/0', 'GatewayId': 'igw-1', 'State': 'active'}]
        self.assertEqual(lab.ipv6_directions(eigw), {'outbound': True, 'inboundFromInternet': False})
        self.assertEqual(lab.ipv6_directions(igw), {'outbound': True, 'inboundFromInternet': True})
        self.assertEqual(lab.ipv6_directions([]), {'outbound': False, 'inboundFromInternet': False})

    def test_ipv6_subnet_block(self):
        self.assertEqual(lab.ipv6_subnet_block('2600:1f18:60d7:1b00::/56', 2), '2600:1f18:60d7:1b02::/64')


class AccessLogTests(SimpleTestCase):
    def test_each_sample_entry_has_a_cause(self):
        causes = [lab.diagnose_alb(lab.parse_alb_entry(line)) for line in lab.SAMPLE_ALB_LOG]
        self.assertEqual(causes, ['ok', 'target closed or refused the connection',
                                  'target did not answer within the idle timeout', 'no healthy targets',
                                  'application error on the target'])

    def test_request_field_is_unquoted(self):
        entry = lab.parse_alb_entry(lab.SAMPLE_ALB_LOG[1])
        self.assertEqual(entry['request'], 'GET https://shop.lab.example:443/cart HTTP/1.1')
        self.assertEqual(entry['elb_status_code'], '502')


class DnsTests(SimpleTestCase):
    ZONES = {lab.PRIVATE_ZONE: {lab.DB_RECORD: lab.DB_IP}}
    RULE = {'domain': lab.CORP_DOMAIN, 'targets': [lab.CORP_DNS], 'associated': True}

    def test_private_zone_needs_dns_hostnames(self):
        off = lab.resolve(lab.DB_RECORD, True, False, self.ZONES, [], [])
        self.assertIsNone(off['answer'])
        on = lab.resolve(lab.DB_RECORD, True, True, self.ZONES, [], [])
        self.assertEqual(on['answer'], lab.DB_IP)

    def test_forwarding_rule_must_be_associated(self):
        name = f'hr.{lab.CORP_DOMAIN}'
        unassociated = lab.resolve(name, True, True, {}, [{**self.RULE, 'associated': False}], [])
        self.assertEqual(unassociated['source'], 'public-dns')
        self.assertEqual(lab.resolve(name, True, True, {}, [self.RULE], [])['source'], 'forwarding-rule')

    def test_more_specific_domain_wins(self):
        zones = {f'hr.{lab.CORP_DOMAIN}': {f'web.hr.{lab.CORP_DOMAIN}': '10.62.2.9'}}
        result = lab.resolve(f'web.hr.{lab.CORP_DOMAIN}', True, True, zones, [self.RULE], [])
        self.assertEqual(result['source'], 'private-zone')

    def test_firewall_blocks_exact_and_wildcard(self):
        for name in ('malware.example', 'cdn.bad.example'):
            self.assertEqual(lab.resolve(name, True, True, {}, [], lab.BLOCKED_DOMAINS)['source'], 'dns-firewall')
        self.assertEqual(lab.resolve('bad.example', True, True, {}, [], lab.BLOCKED_DOMAINS)['source'], 'public-dns')

    def test_no_dns_support_answers_nothing(self):
        self.assertEqual(lab.resolve('aws.amazon.com', False, True, {}, [], [])['source'], 'none')


class CacheKeyTests(SimpleTestCase):
    def test_trimmed_cache_key_raises_the_hit_ratio(self):
        slow = lab.hit_ratio(lab.CDN_REQUESTS, lab.SLOW_PARAMETERS)
        fast = lab.hit_ratio(lab.CDN_REQUESTS, lab.FAST_PARAMETERS)
        self.assertEqual(slow['hitRatio'], 0.0)
        self.assertEqual(fast['cacheEntries'], 2)
        self.assertEqual(fast['hitRatio'], 0.83)

    def test_cache_key_keeps_only_listed_query_strings(self):
        request = {'path': '/p', 'query': {'lang': 'en', 'utm_source': 'x'}, 'cookies': {'session': 's'}}
        self.assertEqual(lab.cache_key(request, lab.FAST_PARAMETERS), '/p?lang=en|')
        self.assertEqual(lab.cache_key(request, lab.SLOW_PARAMETERS), '/p?lang=en&utm_source=x|session=s')


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('network-troubleshooting', [item['key'] for item in labs_for_service('ec2')])
        tags = LAB_EXAM_TAGS[('ec2', 'network-troubleshooting')]
        self.assertIn({'exam': 'SOA-C03', 'task': '5.3', 'primary': True}, tags)
        for key in ('network-operations', 'dns-content-delivery'):
            card = next(card for card in CONCEPT_CARDS if card['key'] == key)
            self.assertIn(('ec2', 'network-troubleshooting'), card['related_labs'])
            self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
