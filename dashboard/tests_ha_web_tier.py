"""HA web tier controls: ELBv2 target group settings, listener and rule edits, Auto Scaling refresh and health."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from dashboard import autoscaling_api, elasticloadbalancing_api
from dashboard.services import SERVICE_REGISTRY

TG_ARN = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/web/abc123'
LISTENER_ARN = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:listener/app/web-alb/abc/def'
RULE_ARN = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:listener-rule/app/web-alb/abc/def/ghi'


def _put(client, url, body):
    return client.put(url, data=json.dumps(body), content_type='application/json')


class TargetGroupAttributeHelperTests(SimpleTestCase):
    def setUp(self):
        self.elbv2 = MagicMock()
        self.elbv2.modify_target_group_attributes.side_effect = lambda **kwargs: {'Attributes': kwargs['Attributes']}
        patcher = patch('dashboard.elasticloadbalancing_api._client', return_value=self.elbv2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self):
        return {item['Key']: item['Value'] for item in self.elbv2.modify_target_group_attributes.call_args.kwargs['Attributes']}

    def test_lb_cookie_stickiness_and_deregistration_delay(self):
        result = elasticloadbalancing_api.modify_target_group_attributes(
            TG_ARN, stickiness_enabled=True, stickiness_type='lb_cookie', stickiness_duration_seconds=120,
            deregistration_delay_seconds=30,
        )
        self.assertEqual(self.sent(), {
            'deregistration_delay.timeout_seconds': '30',
            'stickiness.enabled': 'true',
            'stickiness.type': 'lb_cookie',
            'stickiness.lb_cookie.duration_seconds': '120',
        })
        self.assertEqual(result['attributes']['stickiness.enabled'], 'true')

    def test_app_cookie_needs_an_unreserved_name(self):
        with self.assertRaisesMessage(ValueError, 'App cookie name is required'):
            elasticloadbalancing_api.modify_target_group_attributes(TG_ARN, stickiness_enabled=True, stickiness_type='app_cookie')
        with self.assertRaisesMessage(ValueError, 'reserved'):
            elasticloadbalancing_api.modify_target_group_attributes(
                TG_ARN, stickiness_enabled=True, stickiness_type='app_cookie', app_cookie_name='AWSALBAPP-1',
            )
        elasticloadbalancing_api.modify_target_group_attributes(
            TG_ARN, stickiness_enabled=True, stickiness_type='app_cookie', app_cookie_name='SESSION', stickiness_duration_seconds=60,
        )
        self.assertEqual(self.sent()['stickiness.app_cookie.cookie_name'], 'SESSION')
        self.assertEqual(self.sent()['stickiness.app_cookie.duration_seconds'], '60')

    def test_turning_stickiness_off_sends_only_the_flag(self):
        elasticloadbalancing_api.modify_target_group_attributes(TG_ARN, stickiness_enabled=False, stickiness_duration_seconds=99)
        self.assertEqual(self.sent(), {'stickiness.enabled': 'false'})

    def test_aws_limits_are_checked_because_floci_stores_any_value(self):
        for kwargs, message in (
            ({'deregistration_delay_seconds': 3601}, 'Deregistration delay must be from 0 to 3600'),
            ({'stickiness_enabled': True, 'stickiness_duration_seconds': 0}, 'Stickiness duration must be from 1 to 604800'),
            ({'stickiness_enabled': True, 'stickiness_type': 'source_ip'}, 'Stickiness type must be lb_cookie or app_cookie'),
            ({}, 'At least one attribute must be changed'),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesMessage(ValueError, message):
                elasticloadbalancing_api.modify_target_group_attributes(TG_ARN, **kwargs)
        self.elbv2.modify_target_group_attributes.assert_not_called()

    def test_describe_returns_a_key_value_map(self):
        self.elbv2.describe_target_group_attributes.return_value = {'Attributes': [{'Key': 'stickiness.enabled', 'Value': 'false'}]}
        self.assertEqual(
            elasticloadbalancing_api.describe_target_group_attributes(TG_ARN)['attributes'],
            {'stickiness.enabled': 'false'},
        )


class HealthCheckListenerAndRuleHelperTests(SimpleTestCase):
    def setUp(self):
        self.elbv2 = MagicMock()
        self.elbv2.modify_target_group.return_value = {'TargetGroups': []}
        self.elbv2.modify_listener.return_value = {'Listeners': [{'Port': 8080}]}
        self.elbv2.modify_rule.return_value = {'Rules': []}
        self.elbv2.set_rule_priorities.return_value = {'Rules': [{'Priority': '5'}]}
        patcher = patch('dashboard.elasticloadbalancing_api._client', return_value=self.elbv2)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_health_check_sends_only_changed_settings(self):
        elasticloadbalancing_api.modify_target_group_health_check(TG_ARN, path='/ready', unhealthy_threshold=2, matcher='200-299')
        self.elbv2.modify_target_group.assert_called_once_with(
            TargetGroupArn=TG_ARN, HealthCheckPath='/ready', UnhealthyThresholdCount=2, Matcher={'HttpCode': '200-299'},
        )

    def test_health_check_limits(self):
        for kwargs, message in (
            ({'path': 'ready'}, 'must start with /'),
            ({'interval_seconds': 4}, 'Interval must be from 5 to 300'),
            ({'healthy_threshold': 11}, 'Healthy threshold must be from 2 to 10'),
            ({'interval_seconds': 10, 'timeout_seconds': 10}, 'Timeout must be shorter than the interval'),
            ({}, 'At least one health check setting'),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesMessage(ValueError, message):
                elasticloadbalancing_api.modify_target_group_health_check(TG_ARN, **kwargs)

    def test_listener_port_and_forward_action(self):
        elasticloadbalancing_api.modify_listener(LISTENER_ARN, port=8080, action_type='forward', target_group_arn=TG_ARN)
        self.elbv2.modify_listener.assert_called_once_with(
            ListenerArn=LISTENER_ARN, Port=8080, DefaultActions=[{'Type': 'forward', 'TargetGroupArn': TG_ARN}],
        )

    def test_listener_fixed_response(self):
        elasticloadbalancing_api.modify_listener(LISTENER_ARN, action_type='fixed-response', status_code=503, message_body='maintenance')
        self.elbv2.modify_listener.assert_called_once_with(ListenerArn=LISTENER_ARN, DefaultActions=[{
            'Type': 'fixed-response',
            'FixedResponseConfig': {'StatusCode': '503', 'ContentType': 'text/plain', 'MessageBody': 'maintenance'},
        }])
        with self.assertRaisesMessage(ValueError, '2XX, 4XX or 5XX'):
            elasticloadbalancing_api.modify_listener(LISTENER_ARN, action_type='fixed-response', status_code=302)
        with self.assertRaisesMessage(ValueError, 'Change the port or the default action'):
            elasticloadbalancing_api.modify_listener(LISTENER_ARN)

    def test_rule_edit_and_priority(self):
        result = elasticloadbalancing_api.modify_rule(RULE_ARN, path_pattern='/v2/*', target_group_arn=TG_ARN, priority=5)
        self.elbv2.modify_rule.assert_called_once_with(
            RuleArn=RULE_ARN,
            Conditions=[{'Field': 'path-pattern', 'Values': ['/v2/*']}],
            Actions=[{'Type': 'forward', 'TargetGroupArn': TG_ARN}],
        )
        self.elbv2.set_rule_priorities.assert_called_once_with(RulePriorities=[{'RuleArn': RULE_ARN, 'Priority': 5}])
        self.assertEqual(result['rules'], [{'Priority': '5'}])

    def test_rule_priority_only_skips_modify_rule(self):
        elasticloadbalancing_api.modify_rule(RULE_ARN, priority=7)
        self.elbv2.modify_rule.assert_not_called()
        with self.assertRaisesMessage(ValueError, 'Change the path'):
            elasticloadbalancing_api.modify_rule(RULE_ARN)


class ElasticLoadBalancingEditEndpointTests(SimpleTestCase):
    @patch('dashboard.elasticloadbalancing_views.describe_target_group_attributes')
    def test_get_attributes(self, describe):
        describe.return_value = {'attributes': {}}
        response = self.client.get(reverse('dashboard:elbv2-target-group-attributes', kwargs={'target_group_arn': TG_ARN}))
        self.assertEqual(response.status_code, 200)
        describe.assert_called_once_with(TG_ARN)

    @patch('dashboard.elasticloadbalancing_views.modify_target_group_attributes')
    def test_put_attributes(self, modify):
        modify.return_value = {'attributes': {}}
        response = _put(self.client, reverse('dashboard:elbv2-target-group-attributes', kwargs={'target_group_arn': TG_ARN}), {
            'stickiness_enabled': True, 'stickiness_type': 'lb_cookie', 'stickiness_duration_seconds': 60, 'deregistration_delay_seconds': 10,
        })
        self.assertEqual(response.status_code, 200)
        modify.assert_called_once_with(
            TG_ARN, stickiness_enabled=True, stickiness_type='lb_cookie', stickiness_duration_seconds=60,
            app_cookie_name='', deregistration_delay_seconds=10,
        )

    def test_attribute_validation_error_is_a_400(self):
        with patch('dashboard.elasticloadbalancing_api._client') as client:
            response = _put(self.client, reverse('dashboard:elbv2-target-group-attributes', kwargs={'target_group_arn': TG_ARN}), {
                'deregistration_delay_seconds': 9999,
            })
        self.assertEqual(response.status_code, 400)
        self.assertIn('Deregistration delay', response.json()['error'])
        client.return_value.modify_target_group_attributes.assert_not_called()

    @patch('dashboard.elasticloadbalancing_views.modify_target_group_health_check')
    def test_put_health_check(self, modify):
        modify.return_value = {}
        response = _put(self.client, reverse('dashboard:elbv2-target-group-health-check', kwargs={'target_group_arn': TG_ARN}), {
            'path': '/ready', 'interval_seconds': 10,
        })
        self.assertEqual(response.status_code, 200)
        modify.assert_called_once_with(
            TG_ARN, path='/ready', interval_seconds=10, timeout_seconds=None, healthy_threshold=None,
            unhealthy_threshold=None, matcher='',
        )

    @patch('dashboard.elasticloadbalancing_views.modify_listener')
    def test_put_listener(self, modify):
        modify.return_value = {}
        response = _put(self.client, reverse('dashboard:elbv2-listener-detail', kwargs={'listener_arn': LISTENER_ARN}), {
            'port': 8080, 'action_type': 'forward', 'target_group_arn': TG_ARN,
        })
        self.assertEqual(response.status_code, 200)
        modify.assert_called_once_with(
            LISTENER_ARN, port=8080, action_type='forward', target_group_arn=TG_ARN, status_code=None, message_body='',
        )

    @patch('dashboard.elasticloadbalancing_views.modify_rule')
    def test_put_rule(self, modify):
        modify.return_value = {}
        response = _put(self.client, reverse('dashboard:elbv2-rule-detail', kwargs={'rule_arn': RULE_ARN}), {
            'path_pattern': '/v2/*', 'priority': 5,
        })
        self.assertEqual(response.status_code, 200)
        modify.assert_called_once_with(RULE_ARN, path_pattern='/v2/*', target_group_arn='', priority=5)


class ElasticLoadBalancingInventoryAttributeTests(SimpleTestCase):
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_target_groups_carry_their_attributes_and_the_note_says_they_are_not_applied(self, factory, paginate):
        elbv2 = MagicMock()
        elbv2.describe_target_group_attributes.return_value = {'Attributes': [
            {'Key': 'stickiness.enabled', 'Value': 'true'}, {'Key': 'deregistration_delay.timeout_seconds', 'Value': '30'},
        ]}
        elbv2.describe_target_health.return_value = {'TargetHealthDescriptions': []}
        elbv2.describe_tags.return_value = {'TagDescriptions': []}
        factory.return_value.client.return_value = elbv2

        def pages(_client, operation, _key, **_kwargs):
            if operation == 'describe_target_groups':
                return [{'TargetGroupName': 'web', 'TargetGroupArn': TG_ARN}]
            return []

        paginate.side_effect = pages
        from dashboard.aws.elasticloadbalancing import elasticloadbalancing_inventory

        inventory = elasticloadbalancing_inventory()
        self.assertEqual(
            inventory['target_groups'][0]['attributes'],
            {'stickiness.enabled': 'true', 'deregistration_delay.timeout_seconds': '30'},
        )
        self.assertIn('ModifyTargetGroupAttributes', inventory['supported_from_sdk'])
        self.assertTrue(any('no draining' in note for note in inventory['notes']))


class AutoScalingRefreshAndHealthTests(SimpleTestCase):
    def setUp(self):
        self.autoscaling = MagicMock()
        self.autoscaling.start_instance_refresh.return_value = {'InstanceRefreshId': 'refresh-1'}
        self.autoscaling.describe_instance_refreshes.return_value = {'InstanceRefreshes': [{'InstanceRefreshId': 'refresh-1', 'Status': 'Successful'}]}
        self.autoscaling.set_instance_health.return_value = {}
        patcher = patch('dashboard.autoscaling_api._client', return_value=self.autoscaling)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_refresh_with_preferences(self):
        result = autoscaling_api.start_instance_refresh('web-asg', min_healthy_percentage=50, instance_warmup=30, skip_matching=True)
        self.autoscaling.start_instance_refresh.assert_called_once_with(
            AutoScalingGroupName='web-asg', Strategy='Rolling',
            Preferences={'MinHealthyPercentage': 50, 'InstanceWarmup': 30, 'SkipMatching': True},
        )
        self.assertEqual(result['instance_refresh_id'], 'refresh-1')

    def test_start_refresh_without_preferences_and_limits(self):
        autoscaling_api.start_instance_refresh('web-asg')
        self.autoscaling.start_instance_refresh.assert_called_once_with(AutoScalingGroupName='web-asg', Strategy='Rolling')
        with self.assertRaisesMessage(ValueError, 'from 0 to 100'):
            autoscaling_api.start_instance_refresh('web-asg', min_healthy_percentage=101)
        with self.assertRaisesMessage(ValueError, '0 or more'):
            autoscaling_api.start_instance_refresh('web-asg', instance_warmup=-1)

    def test_describe_refreshes(self):
        self.assertEqual(autoscaling_api.describe_instance_refreshes('web-asg')['instance_refreshes'][0]['Status'], 'Successful')

    def test_set_instance_health(self):
        autoscaling_api.set_instance_health('i-123', 'Unhealthy')
        self.autoscaling.set_instance_health.assert_called_once_with(
            InstanceId='i-123', HealthStatus='Unhealthy', ShouldRespectGracePeriod=False,
        )
        with self.assertRaisesMessage(ValueError, 'Healthy or Unhealthy'):
            autoscaling_api.set_instance_health('i-123', 'Sick')

    @patch('dashboard.autoscaling_views.start_instance_refresh')
    def test_start_refresh_endpoint(self, start):
        start.return_value = {'instance_refresh_id': 'refresh-1'}
        response = self.client.post(
            reverse('dashboard:autoscaling-group-instance-refreshes', kwargs={'name': 'web-asg'}),
            data=json.dumps({'min_healthy_percentage': 90, 'skip_matching': True}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        start.assert_called_once_with('web-asg', min_healthy_percentage=90, instance_warmup=None, skip_matching=True)

    @patch('dashboard.autoscaling_views.describe_instance_refreshes')
    def test_describe_refreshes_endpoint(self, describe):
        describe.return_value = {'instance_refreshes': []}
        response = self.client.get(reverse('dashboard:autoscaling-group-instance-refreshes', kwargs={'name': 'web-asg'}))
        self.assertEqual(response.status_code, 200)
        describe.assert_called_once_with('web-asg')

    @patch('dashboard.autoscaling_views.set_instance_health')
    def test_set_instance_health_endpoint(self, set_health):
        set_health.return_value = {'instance_id': 'i-123'}
        response = self.client.post(
            reverse('dashboard:autoscaling-instance-health', kwargs={'instance_id': 'i-123'}),
            data=json.dumps({'health_status': 'Unhealthy', 'should_respect_grace_period': True}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        set_health.assert_called_once_with('i-123', 'Unhealthy', should_respect_grace_period=True)


class HaWebTierLabelTests(SimpleTestCase):
    """The registry says what Floci doesn't apply, so the workbench doesn't read as if it happens."""

    def actions(self, service_key):
        return {action.name: action for action in SERVICE_REGISTRY[service_key].actions}

    def test_auto_scaling_actions_say_what_floci_does(self):
        actions = self.actions('autoscaling')
        self.assertIn('all at once', actions['start_instance_refresh'].description)
        self.assertIn("can't cancel", actions['start_instance_refresh'].description)
        self.assertIn("doesn't replace an Unhealthy instance", actions['set_instance_health'].description)
        self.assertNotIn('cancel_instance_refresh', actions)

    def test_elb_attribute_action_says_it_is_stored_only(self):
        actions = self.actions('elasticloadbalancing')
        self.assertIn('does not apply them', actions['modify_target_group_attributes'].description)
        self.assertIn('keeps probing', actions['modify_target_group_health_check'].description)
