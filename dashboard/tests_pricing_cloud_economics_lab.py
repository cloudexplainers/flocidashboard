"""Cloud economics lab: Floci prices only a few instance types and leaves Pricing Calculator totals at zero, so the lab
does the TCO, elasticity, rightsizing, licensing and bundle math itself. These tests cover that logic, the checks, and
registration."""

import importlib
import json
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.pricing_labs')


def _price_item(usd: str) -> str:
    return json.dumps({'terms': {'OnDemand': {'a': {'priceDimensions': {'b': {'pricePerUnit': {'USD': usd}}}}}}})


class PriceTests(SimpleTestCase):
    def test_on_demand_price_is_read_from_the_terms(self):
        self.assertEqual(lab.on_demand_price(_price_item('0.0960000000')), 0.096)
        self.assertIsNone(lab.on_demand_price(json.dumps({'terms': {}})))

    def test_price_for_falls_back_to_list_prices(self):
        self.assertEqual(lab.price_for('m5.large', {'m5.large': 0.096}), (0.096, 'Price List API'))
        price, source = lab.price_for('m5.2xlarge', {})
        self.assertEqual(price, 0.384)
        self.assertIn('not in Floci', source)


class EconomicsTests(SimpleTestCase):
    def test_tco_cloud_is_cheaper_with_nothing_up_front(self):
        result = lab.tco(0.096)
        self.assertEqual(result['CloudUpfront'], 0.0)
        self.assertGreater(result['OnPremisesUpfront'], 0)
        self.assertLess(result['CloudTotal'], result['OnPremisesTotal'])
        self.assertAlmostEqual(result['Savings'], result['OnPremisesTotal'] - result['CloudTotal'], places=1)

    def test_elastic_hours(self):
        self.assertEqual(lab.elastic_hours([1, 3, 2]), {'PeakInstances': 3, 'FixedForPeakHours': 9,
                                                        'ElasticHours': 6, 'SavedPercent': 33.3})
        default = lab.elastic_hours()
        self.assertLess(default['ElasticHours'], default['FixedForPeakHours'])

    def test_recommend_size(self):
        self.assertEqual(lab.recommend_size(31, 'm5.2xlarge'), 'm5.xlarge')
        self.assertEqual(lab.recommend_size(95, 'm5.2xlarge'), 'm5.2xlarge')
        peak = max(lab.cpu_samples())
        self.assertEqual(peak, 31.0)
        self.assertEqual(len(lab.cpu_samples(2)), 48)

    def test_license_costs(self):
        small = lab.license_costs(4)
        self.assertEqual(small['Cheaper'], 'BYOL on the Dedicated Host')
        self.assertEqual(lab.license_costs(48)['Cheaper'], 'License included')

    def test_ec2_month_charges_transfer_past_the_free_tier(self):
        month = lab.ec2_month(0.0104, storage_gb=20, transfer_gb=1500)
        self.assertEqual(month['TransferOut'], 126.0)
        self.assertEqual(lab.ec2_month(0.0104, transfer_gb=50)['TransferOut'], 0)

    def test_pick_bundle_takes_the_cheapest_that_fits(self):
        bundles = [
            {'bundleId': 'nano', 'price': 3.5, 'ramSizeInGb': 0.5, 'transferPerMonthInGb': 1024},
            {'bundleId': 'micro', 'price': 5.0, 'ramSizeInGb': 1.0, 'transferPerMonthInGb': 2048},
            {'bundleId': 'small', 'price': 10.0, 'ramSizeInGb': 2.0, 'transferPerMonthInGb': 3072},
            {'bundleId': 'old', 'price': 1.0, 'ramSizeInGb': 4.0, 'transferPerMonthInGb': 9999, 'isActive': False},
        ]
        self.assertEqual(lab.pick_bundle(bundles)['bundleId'], 'micro')
        self.assertIsNone(lab.pick_bundle(bundles, memory_gb=64))


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_rightsizing_needs_metrics_and_a_marker(self):
        with patch.object(lab, '_cpu_stats', return_value=[]):
            self.assertFalse(lab._check_rightsizing()[0])
        with patch.object(lab, '_cpu_stats', return_value=[{'Maximum': 31.0}]):
            self.assertFalse(lab._check_rightsizing()[0])
            lab._marker('rightsizing', {'message': 'done'})
            self.assertTrue(lab._check_rightsizing()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_tagged_and_batched(self):
        self.assertEqual([item['key'] for item in labs_for_service('pricing')], ['cloud-economics'])
        self.assertIn({'exam': 'CLF-C02', 'task': '1.4', 'primary': True}, LAB_EXAM_TAGS[('pricing', 'cloud-economics')])
        self.assertIn('pricing', [batch['service'] for batch in LAB_BATCH_ORDER])
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'cloud-economics')
        self.assertIn(('pricing', 'cloud-economics'), card['related_labs'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 7)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('pricing', 'cloud-economics', 'nope')
