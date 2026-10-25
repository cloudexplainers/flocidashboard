"""Edge security and throttling lab: Floci stores WAF and usage plan settings but never blocks or throttles, so the
lab evaluates the web ACL and a token bucket itself. These tests cover that logic, the checks, and registration."""

import importlib
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.wafv2_labs')

IP_SET_ARN = 'arn:aws:wafv2:us-east-1:000000000000:regional/ipset/lab-edge-blocked-ips/1'
IP_SETS = {IP_SET_ARN: lab.BLOCKED_ADDRESSES}
RULES = lab.acl_rules(IP_SET_ARN)


def request(**overrides):
    return {'ip': '192.0.2.10', 'country': 'US', 'query': 'symbol=AMZN', 'headers': {}, 'count': 1, **overrides}


class WafEvaluationTests(SimpleTestCase):
    def test_every_sample_gets_its_expected_outcome(self):
        for sample in lab.SAMPLE_REQUESTS:
            self.assertEqual(lab.replay(RULES, sample, IP_SETS), sample['Expected'], sample['Name'])

    def test_priority_decides_when_two_rules_match(self):
        # A bad address in a blocked country is stopped by the IP set (priority 0), not the geo rule.
        action, rule = lab.evaluate_waf(RULES, request(ip='198.51.100.7', country='KP'), IP_SETS)
        self.assertEqual((action, rule), ('BLOCK', 'block-bad-ips'))

    def test_count_action_lets_the_request_through(self):
        rules = [dict(RULES[1], Action={'Count': {}})]
        self.assertEqual(lab.evaluate_waf(rules, request(country='KP'), IP_SETS), ('ALLOW', None))

    def test_excluded_managed_rule_does_not_match(self):
        rules = [{**RULES[3], 'Statement': {'ManagedRuleGroupStatement': {
            'VendorName': 'AWS', 'Name': 'AWSManagedRulesCommonRuleSet',
            'ExcludedRules': [{'Name': 'CrossSiteScripting_QUERYARGUMENTS'}]}}}]
        self.assertEqual(lab.evaluate_waf(rules, request(query='<script>x</script>'), IP_SETS)[0], 'ALLOW')

    def test_rate_rule_starts_after_the_limit(self):
        self.assertEqual(lab.replay(RULES, request(count=lab.RATE_LIMIT), IP_SETS), f'ALLOW {lab.RATE_LIMIT}')

    def test_acl_problems(self):
        self.assertEqual(lab.acl_problems(RULES), [])
        duplicate = [RULES[0], dict(RULES[1], Priority=0)]
        self.assertIn('duplicate priorities [0]', lab.acl_problems(duplicate))
        group_with_action = [{**RULES[3], 'Action': {'Block': {}}}]
        del group_with_action[0]['OverrideAction']
        self.assertTrue(any('OverrideAction' in problem for problem in lab.acl_problems(group_with_action)))


class TokenBucketTests(SimpleTestCase):
    def test_burst_then_refill(self):
        self.assertEqual(lab.token_bucket(5, 10, [0.0] * 20), {'Allowed': 10, 'Throttled429': 10})
        self.assertEqual(lab.token_bucket(5, 10, [0.0] * 10 + [1.0] * 10), {'Allowed': 15, 'Throttled429': 5})

    def test_bucket_never_exceeds_its_size(self):
        self.assertEqual(lab.token_bucket(5, 10, [100.0] * 20)['Allowed'], 10)

    def test_lab_burst(self):
        bronze = lab.token_bucket(5, 10, lab.BURST_ARRIVALS)
        gold = lab.token_bucket(50, 100, lab.BURST_ARRIVALS)
        self.assertEqual(len(lab.BURST_ARRIVALS), 70)
        self.assertIn(bronze['Allowed'], (24, 25))
        self.assertEqual(gold, {'Allowed': 70, 'Throttled429': 0})


class UsageTierCheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def patched(self, plans, keys_in_plans=True):
        keys = {spec['key']: {'id': spec['key'], 'value': 'v'} for spec in lab.PLANS.values()}
        keys[lab.UNPLANNED_KEY] = {'id': 'u', 'value': 'u'}
        return (patch.object(lab, '_plans', return_value=plans),
                patch.object(lab, '_keys', return_value=keys),
                patch.object(lab, '_plan_key_names',
                             side_effect=lambda plan_id: [lab.PLANS[plan_id]['key']] if keys_in_plans else []))

    def check(self, plans, **kwargs):
        a, b, c = self.patched(plans, **kwargs)
        with a, b, c:
            return lab._check_tiers()

    def test_floci_read_back_without_throttle_uses_what_was_sent(self):
        plans = {name: {'id': name, 'name': name} for name in lab.PLANS}
        self.assertFalse(self.check(plans)[0])
        lab._marker('usage-tiers', {'SentPlans': {name: {'throttle': spec['throttle'], 'quota': spec['quota']}
                                                  for name, spec in lab.PLANS.items()}})
        self.assertTrue(self.check(plans)[0])

    def test_plan_that_reads_back_wrong_fails(self):
        plans = {name: {'id': name, 'name': name, 'throttle': {'rateLimit': 1.0, 'burstLimit': 1},
                        'quota': spec['quota']} for name, spec in lab.PLANS.items()}
        self.assertFalse(self.check(plans)[0])

    def test_key_must_be_in_the_plan(self):
        plans = {name: {'id': name, 'name': name, **{k: spec[k] for k in ('throttle', 'quota')}}
                 for name, spec in lab.PLANS.items()}
        self.assertTrue(self.check(plans)[0])
        self.assertFalse(self.check(plans, keys_in_plans=False)[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('wafv2')], ['edge-throttling'])
        self.assertIn(('wafv2', 'edge-throttling'), LAB_EXAM_TAGS)
        self.assertIn('wafv2', [batch['service'] for batch in LAB_BATCH_ORDER])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('wafv2', 'edge-throttling', 'nope')
