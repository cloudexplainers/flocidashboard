"""Cost visibility lab: Floci stores budgets and anomaly monitors but never alerts, forecasts or finds anomalies, and
has no RI or Savings Plans offerings, so the lab works these out from sample spend. These tests cover that logic,
the checks, and registration."""

import importlib
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.costexplorer_labs')


class TagTests(SimpleTestCase):
    def test_missing_tags(self):
        resources = {'a': {'CostCenter': 'platform', 'Environment': 'prod'}, 'b': {'Environment': 'dev'},
                     'c': {'CostCenter': '', 'Environment': ''}}
        self.assertEqual(lab.missing_tags(resources), {'b': ['CostCenter'], 'c': ['CostCenter', 'Environment']})

    def test_check_requires_every_lab_resource_tagged(self):
        good = {arn: {**tags, 'Lab': lab.LAB_KEY} for arn, tags in lab.RESOURCE_TAGS.items()}
        with patch.object(lab, '_tagged_resources', return_value=good):
            self.assertTrue(lab._check_tags()[0])
        queue = f'arn:aws:sqs:{lab.REGION}:{lab.ACCOUNT}:{lab.ORPHAN_QUEUE}'
        with patch.object(lab, '_tagged_resources', return_value={**good, queue: {'Environment': 'dev'}}):
            ok, message = lab._check_tags()
        self.assertFalse(ok)
        self.assertIn(lab.ORPHAN_QUEUE, message)


class BudgetAndForecastTests(SimpleTestCase):
    def test_forecast_is_a_straight_line(self):
        self.assertEqual(lab.forecast([2.0] * 10), 60.0)
        self.assertEqual(lab.forecast([]), 0.0)

    def test_alerts_before_and_after_the_spike(self):
        day20 = sum(lab.SAMPLE_DAILY)
        self.assertEqual(lab.budget_alerts(100, day20, lab.forecast(lab.SAMPLE_DAILY)), [])
        after = [*lab.SAMPLE_DAILY, lab.SPIKE_DAY]
        self.assertEqual(lab.budget_alerts(100, sum(after), lab.forecast(after)), ['ACTUAL > 80%', 'FORECASTED > 100%'])

    def test_forecast_alert_fires_before_actual(self):
        self.assertEqual(lab.budget_alerts(100, 60, 120), ['FORECASTED > 100%'])

    def test_absolute_threshold(self):
        notifications = [{'NotificationType': 'ACTUAL', 'ComparisonOperator': 'GREATER_THAN', 'Threshold': 50,
                          'ThresholdType': 'ABSOLUTE_VALUE'}]
        self.assertEqual(lab.budget_alerts(100, 51, 0, notifications), ['ACTUAL > 50%'])


class AnomalyTests(SimpleTestCase):
    def test_only_the_spike_passes_the_threshold(self):
        found = lab.find_anomalies(lab.SAMPLE_DAILY, [3.30, 6.10, lab.SPIKE_DAY])
        self.assertEqual([item['Spend'] for item in found], [6.10, lab.SPIKE_DAY])
        self.assertEqual([item['Alerts'] for item in found], [False, True])
        self.assertEqual(found[1]['Day'], len(lab.SAMPLE_DAILY) + 3)


class PricingTests(SimpleTestCase):
    def test_on_demand_price_parses_the_price_list(self):
        item = json.dumps({'terms': {'OnDemand': {'X.Y': {'priceDimensions': {'X.Y.Z': {
            'pricePerUnit': {'USD': '0.0960000000'}}}}}}})
        self.assertEqual(lab.on_demand_price(item), 0.096)
        self.assertIsNone(lab.on_demand_price(json.dumps({'terms': {}})))

    def test_option_costs(self):
        rows = {row['Option']: row for row in lab.option_costs(0.096, spot_discount=0.58)}
        self.assertEqual(rows['On-Demand']['3YearCostUSD'], 2522.88)
        self.assertEqual(rows['Standard RI, 3 years, all upfront']['3YearCostUSD'], 1009.15)
        self.assertEqual(rows['Spot']['DiscountVsOnDemand'], '58%')
        ordered = [row['3YearCostUSD'] for row in lab.option_costs(0.096)]
        self.assertEqual(ordered[0], max(ordered))

    def test_every_workload_has_a_known_option(self):
        names = {name for name, *_ in lab.OPTIONS} | {'Dedicated Host', 'On-Demand Capacity Reservation'}
        for _workload, choice in lab.WORKLOADS:
            self.assertIn(choice, names)

    def test_purchasing_card_covers_w3_7(self):
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'pricing-models')
        text = ' '.join(card['points'])
        for phrase in ('Compute Savings Plans', 'EC2 Instance Savings Plans', 'Convertible', 'All Upfront',
                       'Dedicated Hosts', 'Capacity Reservations'):
            self.assertIn(phrase, text)
        self.assertIsNone(card['planned'])


class SpotCheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_open_request_or_running_instance_fails(self):
        lab._marker('spot-request', {'message': 'ok', 'RequestIds': ['sir-1']})
        cases = [
            ([{'SpotInstanceRequestId': 'sir-1', 'State': 'cancelled', 'InstanceId': 'i-1'}], 'terminated', True),
            ([{'SpotInstanceRequestId': 'sir-1', 'State': 'active', 'InstanceId': 'i-1'}], 'terminated', False),
            ([{'SpotInstanceRequestId': 'sir-1', 'State': 'cancelled', 'InstanceId': 'i-1'}], 'running', False),
        ]
        for requests, state, expected in cases:
            with patch.object(lab, '_spot_requests', return_value=requests), \
                    patch.object(lab, '_instance_state', return_value=state):
                self.assertEqual(lab._check_spot()[0], expected, (requests, state))

    def test_needs_the_step_to_have_run(self):
        self.assertFalse(lab._check_spot()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_the_last_batch(self):
        self.assertEqual([item['key'] for item in labs_for_service('costexplorer')], ['cost-visibility'])
        self.assertIn(('costexplorer', 'cost-visibility'), LAB_EXAM_TAGS)
        self.assertEqual(LAB_BATCH_ORDER[-1]['service'], 'costexplorer')

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('costexplorer', 'cost-visibility', 'nope')
