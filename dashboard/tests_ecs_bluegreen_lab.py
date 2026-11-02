"""Container blue/green lab: Floci enforces immutable ECR tags and runs CodeDeploy ECS blue/green deployments with
Lambda hooks and a canary on the production listener, but does not scan images, apply lifecycle policies, or roll
back. These tests cover the lifecycle preview, the listener reading, the hook, the checks, and registration."""

import importlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.ecs_bluegreen_labs')


def _image(digest, tags, pushed):
    return {'imageDigest': digest, 'imageTags': tags, 'imagePushedAt': datetime.fromtimestamp(pushed, timezone.utc)}


class LifecyclePreviewTests(SimpleTestCase):
    def test_lab_policy_expires_only_the_older_untagged_build(self):
        images = [_image('a', [], 100), _image('b', [], 200), _image('r1', ['release-1'], 10),
                  _image('r2', ['release-2'], 20)]
        rows = {row['imageDigest']: row for row in lab.lifecycle_preview(lab.LIFECYCLE_POLICY, images)}
        self.assertEqual(rows['a']['action'], 'expire')
        self.assertEqual(rows['a']['rule'], 1)
        self.assertEqual(rows['b']['action'], 'keep')
        self.assertEqual({rows['r1']['rule'], rows['r2']['rule']}, {2})
        self.assertEqual([row['imageDigest'] for row in rows.values() if row['action'] == 'expire'], ['a'])

    def test_count_rule_keeps_the_newest_releases(self):
        policy = {'rules': [{'rulePriority': 1, 'selection': {
            'tagStatus': 'tagged', 'tagPrefixList': ['release-'], 'countType': 'imageCountMoreThan', 'countNumber': 2},
            'action': {'type': 'expire'}}]}
        images = [_image(f'r{index}', [f'release-{index}'], index) for index in range(1, 5)]
        images.append(_image('dev', ['dev-1'], 1))
        rows = {row['imageDigest']: row['action'] for row in lab.lifecycle_preview(policy, images)}
        self.assertEqual(rows, {'r1': 'expire', 'r2': 'expire', 'r3': 'keep', 'r4': 'keep', 'dev': 'keep'})

    def test_images_in_use_are_blocked_not_expired(self):
        policy = {'rules': [{'rulePriority': 1, 'selection': {
            'tagStatus': 'tagged', 'tagPrefixList': ['release-'], 'countType': 'imageCountMoreThan', 'countNumber': 1},
            'action': {'type': 'expire'}}]}
        images = [_image('r1', ['release-1'], 1), _image('r2', ['release-2'], 2)]
        rows = {row['imageDigest']: row['action'] for row in lab.lifecycle_preview(
            policy, images, protected={'release-1'})}
        self.assertEqual(rows, {'r1': 'blocked', 'r2': 'keep'})

    def test_since_pushed_rule_uses_days(self):
        policy = {'rules': [{'rulePriority': 1, 'selection': {
            'tagStatus': 'untagged', 'countType': 'sinceImagePushed', 'countUnit': 'days', 'countNumber': 1},
            'action': {'type': 'expire'}}]}
        now = 10 * 86400
        images = [_image('old', [], now - 2 * 86400), _image('new', [], now - 3600)]
        rows = {row['imageDigest']: row['action'] for row in lab.lifecycle_preview(policy, images, now=now)}
        self.assertEqual(rows, {'old': 'expire', 'new': 'keep'})


class ListenerTests(SimpleTestCase):
    BLUE = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/lab-storefront-blue/abc'
    GREEN = 'arn:aws:elasticloadbalancing:us-east-1:000000000000:targetgroup/lab-storefront-green/def'

    def test_plain_forward_sends_everything_to_one_group(self):
        weights = lab.forward_weights([{'Type': 'forward', 'TargetGroupArn': self.BLUE}])
        self.assertEqual(weights, {'lab-storefront-blue': 100})
        self.assertEqual(lab.serving(weights), 'lab-storefront-blue')

    def test_weighted_forward_is_a_split(self):
        weights = lab.forward_weights([{'Type': 'forward', 'ForwardConfig': {'TargetGroups': [
            {'TargetGroupArn': self.BLUE, 'Weight': 90}, {'TargetGroupArn': self.GREEN, 'Weight': 10}]}}])
        self.assertEqual(weights, {'lab-storefront-blue': 90, 'lab-storefront-green': 10})
        self.assertIsNone(lab.serving(weights))
        self.assertEqual(lab.serving({'lab-storefront-blue': 0, 'lab-storefront-green': 100}), 'lab-storefront-green')


class ImageAndHookTests(SimpleTestCase):
    def test_built_image_manifest_matches_its_blobs(self):
        _raw, layer, config = lab.build_image('storefront release-1')
        body = json.loads(lab.manifest(layer, config))
        self.assertEqual(body['config']['digest'], lab._digest(config))
        self.assertEqual(body['layers'][0]['size'], len(layer))
        self.assertEqual(lab.build_image('storefront release-1'), lab.build_image('storefront release-1'))
        self.assertNotEqual(lab.build_image('a')[1], lab.build_image('b')[1])

    def test_appspec_names_the_task_definition_and_hook(self):
        spec = lab.appspec('arn:aws:ecs:us-east-1:000000000000:task-definition/lab-storefront:2')
        props = spec['Resources'][0]['TargetService']['Properties']
        self.assertTrue(props['TaskDefinition'].endswith(':2'))
        self.assertEqual(props['LoadBalancerInfo'], {'ContainerName': 'web', 'ContainerPort': 80})
        self.assertEqual(spec['Hooks'], [{'BeforeAllowTraffic': lab.HOOK_FUNCTION}])

    def test_image_tag_reads_registry_ports(self):
        self.assertEqual(lab.image_tag('000000000000.dkr.ecr.us-east-1.localhost:4566/lab-storefront:release-2'),
                         'release-2')
        self.assertEqual(lab.image_tag('localhost:4566/lab-storefront'), 'latest')

    def test_hook_source_compiles_and_reports_status(self):
        compile(lab.HOOK_SOURCE, 'index.py', 'exec')
        self.assertIn('put_lifecycle_event_hook_execution_status', lab.HOOK_SOURCE)
        self.assertIn('describe_images', lab.HOOK_SOURCE)


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_canary_check_needs_release_2_primary_and_green_serving(self):
        primary = {'externalId': 'd-1', 'taskDefinition': 'td:2', 'status': 'PRIMARY'}
        with patch.object(lab, '_primary_task_set', return_value=primary), \
                patch.object(lab, '_deployment', return_value={'status': 'Succeeded'}), \
                patch.object(lab, '_task_definition_image', return_value='repo/lab-storefront:release-2'), \
                patch.object(lab, '_prod_weights', return_value={'lab-storefront-green': 100}):
            self.assertTrue(lab._check_canary()[0])
        with patch.object(lab, '_primary_task_set', return_value=primary), \
                patch.object(lab, '_deployment', return_value={'status': 'Succeeded'}), \
                patch.object(lab, '_task_definition_image', return_value='repo/lab-storefront:release-2'), \
                patch.object(lab, '_prod_weights', return_value={'lab-storefront-blue': 90, 'lab-storefront-green': 10}):
            self.assertFalse(lab._check_canary()[0])

    def test_failed_check_needs_the_leftover_task_set_removed(self):
        lab._marker('failed-hook', {'deploymentId': 'd-3'})
        failed = {'deploymentId': 'd-3', 'status': 'Failed', 'errorInformation': {'code': 'HOOK_EXECUTION_FAILURE'}}
        with patch.object(lab, '_deployment', return_value=failed), \
                patch.object(lab, '_check_canary', return_value=(True, '')), \
                patch.object(lab, '_task_sets', return_value=[{'externalId': 'd-3', 'status': 'ACTIVE'}]):
            ok, message = lab._check_failed()
        self.assertFalse(ok)
        self.assertIn('Delete the task set', message)
        with patch.object(lab, '_deployment', return_value=failed), \
                patch.object(lab, '_check_canary', return_value=(True, '')), \
                patch.object(lab, '_task_sets', return_value=[{'externalId': 'd-2', 'status': 'PRIMARY'}]):
            self.assertTrue(lab._check_failed()[0])

    def test_push_check_needs_the_refused_overwrite(self):
        with patch.object(lab, '_check_repository', return_value=(True, '')), \
                patch.object(lab, '_image', return_value={'imageDigest': 'sha256:1'}):
            self.assertFalse(lab._check_push()[0])
            lab._marker('push-images', {'overwrite': {'error': 'ImageTagAlreadyExistsException'},
                                        'release1': 'sha256:1'})
            self.assertTrue(lab._check_push()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('container-blue-green', [item['key'] for item in labs_for_service('ecs')])
        tags = LAB_EXAM_TAGS[('ecs', 'container-blue-green')]
        self.assertIn({'exam': 'DOP-C02', 'task': '1.4', 'primary': True}, tags)
        self.assertIn({'exam': 'DOP-C02', 'task': '1.3', 'primary': False}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'artifact-management')
        self.assertIn(('ecs', 'container-blue-green'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
