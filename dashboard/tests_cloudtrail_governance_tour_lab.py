"""Governance tour lab: CloudTrail, Config, GuardDuty and Security Hub. Floci stores these services' settings but
records nothing, so the lab matches findings and evaluates the Config rule itself; these tests cover that logic,
the checks, and the lab's registration."""

import importlib
import json
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.cloudtrail_labs')

TOPIC_ARN = f'arn:aws:sns:us-east-1:000000000000:{lab.TOPIC}'


def hub_event(label: str, status: str = 'NEW', product: str = 'GuardDuty') -> dict:
    return {'source': 'aws.securityhub', 'detail-type': 'Security Hub Findings - Imported',
            'detail': {'findings': [{'ProductName': product, 'Severity': {'Label': label},
                                     'Workflow': {'Status': status}}]}}


class PatternMatchingTests(SimpleTestCase):
    def test_guardduty_rule_alerts_on_severity_seven_and_up(self):
        results = {finding: lab.event_matches(lab.FINDINGS_PATTERN, lab._sample_event(finding, severity))
                   for finding, severity in lab.SAMPLE_FINDINGS}
        self.assertEqual([finding for finding, alerts in results.items() if alerts],
                         [lab.SAMPLE_FINDINGS[0][0], lab.SAMPLE_FINDINGS[1][0]])
        self.assertTrue(lab.event_matches(lab.FINDINGS_PATTERN, lab._sample_event('Edge', 7)))
        self.assertFalse(lab.event_matches(lab.FINDINGS_PATTERN, lab._sample_event('Edge', 6.9)))

    def test_guardduty_rule_ignores_other_sources(self):
        event = {**lab._sample_event('X', 9), 'source': 'aws.securityhub'}
        self.assertFalse(lab.event_matches(lab.FINDINGS_PATTERN, event))

    def test_security_hub_rule_matches_any_finding_in_the_array(self):
        self.assertTrue(lab.event_matches(lab.HUB_PATTERN, hub_event('CRITICAL', product='Inspector')))
        self.assertTrue(lab.event_matches(lab.HUB_PATTERN, hub_event('HIGH')))
        self.assertFalse(lab.event_matches(lab.HUB_PATTERN, hub_event('MEDIUM')))
        self.assertFalse(lab.event_matches(lab.HUB_PATTERN, hub_event('HIGH', status='RESOLVED')))
        mixed = hub_event('LOW')
        mixed['detail']['findings'].append({'Severity': {'Label': 'HIGH'}, 'Workflow': {'Status': 'NEW'}})
        self.assertTrue(lab.event_matches(lab.HUB_PATTERN, mixed))

    def test_floci_event_adds_the_envelope(self):
        event = lab._floci_event(hub_event('HIGH'))
        for key in ('version', 'id', 'account', 'region', 'time', 'resources', 'source', 'detail'):
            self.assertIn(key, event)


class TrailBucketPolicyTests(SimpleTestCase):
    def test_lab_policy_has_both_cloudtrail_statements(self):
        self.assertEqual(lab.missing_trail_statements(lab.TRAIL_BUCKET_POLICY), [])

    def test_missing_policy_lacks_both(self):
        self.assertEqual(lab.missing_trail_statements(None), ['s3:GetBucketAcl', 's3:PutObject'])

    def test_put_object_without_owner_acl_does_not_count(self):
        policy = json.loads(json.dumps(lab.TRAIL_BUCKET_POLICY))
        for statement in policy['Statement']:
            statement.pop('Condition', None) if statement.get('Action') == 's3:PutObject' else None
        self.assertIn('s3:PutObject', lab.missing_trail_statements(policy))

    def test_other_principals_do_not_count(self):
        policy = {'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'config.amazonaws.com'},
                                 'Action': 's3:GetBucketAcl', 'Resource': f'arn:aws:s3:::{lab.TRAIL_BUCKET}'}]}
        self.assertEqual(lab.missing_trail_statements(policy), ['s3:GetBucketAcl', 's3:PutObject'])


class ConfigRuleTests(SimpleTestCase):
    def test_versioning_rule_evaluation(self):
        states = {lab.TRAIL_BUCKET: 'Enabled', lab.PAYROLL_BUCKET: 'Off', 'gone': 'Missing'}
        with patch.object(lab, '_versioning', side_effect=states.get):
            self.assertEqual(lab.evaluate_versioning([lab.TRAIL_BUCKET, lab.PAYROLL_BUCKET, 'gone']),
                             lab.EXPECTED_COMPLIANCE)

    def test_suspended_versioning_is_noncompliant(self):
        with patch.object(lab, '_versioning', return_value='Suspended'):
            self.assertEqual(lab.evaluate_versioning([lab.TRAIL_BUCKET]), {lab.TRAIL_BUCKET: 'NON_COMPLIANT'})


class SecurityHubCheckTests(SimpleTestCase):
    def patched(self, hub=True, pattern=None, targets=(TOPIC_ARN,)):
        rule = {'EventPattern': json.dumps(pattern or lab.HUB_PATTERN)}
        return (patch.object(lab, '_hub', return_value={'HubArn': 'arn:hub'} if hub else None),
                patch.object(lab, '_findings_rule', return_value=rule),
                patch.object(lab, '_topic_arn', return_value=TOPIC_ARN),
                patch.object(lab, '_rule_targets', return_value=list(targets)))

    def check(self, **kwargs):
        a, b, c, d = self.patched(**kwargs)
        with a, b, c, d:
            return lab._check_hub()

    def test_passes_with_hub_rule_and_topic(self):
        ok, message = self.check()
        self.assertTrue(ok, message)

    def test_hub_left_on_after_reset_is_not_enough(self):
        with patch.object(lab, '_hub', return_value={'HubArn': 'arn:hub'}), \
                patch.object(lab, '_findings_rule', return_value=None):
            ok, message = lab._check_hub()
        self.assertFalse(ok)
        self.assertIn(lab.HUB_RULE, message)

    def test_needs_the_alerts_target(self):
        ok, message = self.check(targets=())
        self.assertFalse(ok)
        self.assertIn(lab.TOPIC, message)

    def test_disabled_hub_fails(self):
        self.assertFalse(self.check(hub=False)[0])


class GuardDutyCheckTests(SimpleTestCase):
    def test_wrong_pattern_fails(self):
        with patch.object(lab, '_detector', return_value={'DetectorId': 'd1', 'Status': 'ENABLED'}), \
                patch.object(lab, '_findings_rule', return_value={'EventPattern': json.dumps({'source': ['aws.guardduty']})}):
            ok, message = lab._check_guardduty()
        self.assertFalse(ok)
        self.assertIn(lab.FINDINGS_RULE, message)


class ResetTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_misrouted_disable_security_hub_is_not_an_error(self):
        misrouted = ClientError({'Error': {'Code': '400', 'Message': '<Code>AuthorizationHeaderMalformed</Code>'}},
                                'DisableSecurityHub')

        class Client:
            def __getattr__(self, name):
                def call(**kwargs):
                    if name == 'disable_security_hub':
                        raise misrouted
                    if name == 'list_object_versions':
                        raise ClientError({'Error': {'Code': 'NoSuchBucket'}}, name)
                    if name in ('describe_configuration_recorders', 'describe_delivery_channels'):
                        return {}
                    if name in ('list_topics', 'list_detectors'):
                        return {}
                    raise ClientError({'Error': {'Code': 'ResourceNotFoundException'}}, name)
                return call

        with patch.object(lab, '_client', return_value=Client()):
            result = lab.reset()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['verification']['status'], 'passed')


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        keys = [item['key'] for item in labs_for_service('cloudtrail')]
        self.assertEqual(keys, ['governance-tour'])
        self.assertIn(('cloudtrail', 'governance-tour'), LAB_EXAM_TAGS)

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(ValueError):
            lab.run_step('cloudtrail', 'governance-tour', 'nope')

    def test_lab_is_the_last_batch(self):
        from .labs.registry import LAB_BATCH_ORDER
        self.assertEqual(LAB_BATCH_ORDER[-1]['service'], 'cloudtrail')
