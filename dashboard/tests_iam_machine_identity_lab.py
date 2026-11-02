"""Machine identity lab: Floci enforces boundaries, the iam:PermissionsBoundary condition and session policies, and runs
the rotation function, but does not resolve tag condition keys. These tests cover the lab's own ABAC evaluation, the
rotation function against fake clients, the label helpers, the checks, and registration."""

import importlib
import json
import sys
import types
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import SimpleTestCase

from .labs.exams import CONCEPT_CARDS, LAB_EXAM_TAGS
from .labs.registry import labs_for_service

lab = importlib.import_module('dashboard.labs.iam_machine_identity_labs')


class AbacTests(SimpleTestCase):
    def test_same_team_is_allowed_and_other_teams_are_denied(self):
        orders = {'team': 'orders'}
        self.assertTrue(lab.abac_allows(lab.ABAC_POLICY, 'sqs:SendMessage', orders, {'team': 'orders'}))
        self.assertFalse(lab.abac_allows(lab.ABAC_POLICY, 'sqs:SendMessage', orders, {'team': 'payments'}))

    def test_missing_tags_and_other_actions_are_denied(self):
        self.assertFalse(lab.abac_allows(lab.ABAC_POLICY, 'sqs:SendMessage', {}, {'team': 'orders'}))
        self.assertFalse(lab.abac_allows(lab.ABAC_POLICY, 'sqs:SendMessage', {'team': 'orders'}, {}))
        self.assertFalse(lab.abac_allows(lab.ABAC_POLICY, 'sqs:DeleteQueue', {'team': 'orders'}, {'team': 'orders'}))

    def test_static_principal_tag_condition(self):
        policy = {'Statement': [{'Effect': 'Allow', 'Action': '*', 'Resource': '*',
                                 'Condition': {'StringEquals': {'aws:PrincipalTag/team': 'orders'}}}]}
        self.assertTrue(lab.abac_allows(policy, 'sqs:SendMessage', {'team': 'orders'}, {}))
        self.assertFalse(lab.abac_allows(policy, 'sqs:SendMessage', {'team': 'payments'}, {}))


class PolicyTests(SimpleTestCase):
    def test_team_admin_needs_the_boundary_and_cannot_remove_it(self):
        statements = {statement['Sid']: statement for statement in lab.TEAM_ADMIN_POLICY['Statement']}
        create = statements['CreateOnlyWithTheBoundary']
        self.assertEqual(create['Condition']['StringEquals']['iam:PermissionsBoundary'], lab.BOUNDARY_ARN)
        self.assertTrue(create['Resource'].endswith(f'role/{lab.TEAM_PREFIX}*'))
        self.assertEqual(statements['KeepTheCeiling']['Effect'], 'Deny')
        self.assertIn('iam:DeleteRolePermissionsBoundary', statements['KeepTheCeiling']['Action'])
        self.assertIn('iam:CreatePolicyVersion', statements['KeepTheCeiling']['Action'])

    def test_session_policy_allows_only_the_jobs_queue(self):
        resource = lab.SESSION_POLICY['Statement'][0]['Resource']
        self.assertTrue(resource.endswith(f':{lab.JOBS_QUEUE}'))


class StageTests(SimpleTestCase):
    def test_unfinished_pending_version_wins(self):
        versions = {'old': ['AWSPREVIOUS'], 'cur': ['AWSPENDING', 'AWSCURRENT'], 'new': ['AWSPENDING']}
        self.assertEqual(lab.stage_map(versions), {'AWSPREVIOUS': 'old', 'AWSCURRENT': 'cur', 'AWSPENDING': 'new'})

    def test_finished_rotation_leaves_pending_on_current(self):
        labels = lab.stage_map({'old': ['AWSPREVIOUS'], 'cur': ['AWSPENDING', 'AWSCURRENT']})
        self.assertEqual(labels['AWSPENDING'], labels['AWSCURRENT'])

    def test_log_helpers_split_steps_and_failures(self):
        log = [{'token': 'a', 'step': 'createSecret', 'result': 'ok'},
               {'token': 'b', 'step': 'testSecret', 'result': 'failed: locked'}]
        self.assertEqual(lab.steps_for(log, 'a'), ['createSecret'])
        self.assertEqual(lab.failures_for(log, 'b'), ['testSecret: failed: locked'])


class FakeRotationClients:
    """Secrets Manager versions and the database item, for the rotation function."""

    def __init__(self, locked=False):
        current = json.dumps({'username': lab.DB_USER, 'password': lab.INITIAL_PASSWORD})
        self.versions = {'v1': {'stages': ['AWSCURRENT'], 'value': current}, 'tok': {'stages': ['AWSPENDING'],
                                                                                   'value': None}}
        self.item = {'user': {'S': lab.DB_USER}, 'passwords': {'SS': [lab.INITIAL_PASSWORD]}}
        if locked:
            self.item['locked'] = {'BOOL': True}
        self.log = []
        error = type('ClientError', (Exception,), {})
        self.client_error = error

    def _get(self, SecretId, VersionId=None, VersionStage=None):
        for version, data in self.versions.items():
            if (VersionId in (None, version)) and (VersionStage is None or VersionStage in data['stages']):
                if data['value'] is None:
                    raise self._not_found()
                return {'SecretString': data['value'], 'VersionId': version}
        raise self._not_found()

    def _not_found(self):
        from botocore.exceptions import ClientError
        return ClientError({'Error': {'Code': 'ResourceNotFoundException'}}, 'GetSecretValue')

    def _put(self, SecretId, ClientRequestToken, SecretString, VersionStages):
        self.versions[ClientRequestToken] = {'stages': VersionStages, 'value': SecretString}

    def _move(self, SecretId, VersionStage, MoveToVersionId, RemoveFromVersionId):
        self.versions[RemoveFromVersionId]['stages'].remove(VersionStage)
        self.versions[MoveToVersionId]['stages'].append(VersionStage)

    def _update_item(self, **kwargs):
        if kwargs['UpdateExpression'].startswith('ADD passwords'):
            self.item['passwords']['SS'].extend(kwargs['ExpressionAttributeValues'][':new']['SS'])
        else:
            self.log.append(kwargs['ExpressionAttributeValues'][':step']['L'][0]['M'])

    def client(self, name):
        fake = MagicMock()
        if name == 'secretsmanager':
            fake.get_secret_value.side_effect = self._get
            fake.put_secret_value.side_effect = self._put
            fake.update_secret_version_stage.side_effect = self._move
            fake.describe_secret.side_effect = lambda SecretId: {'VersionIdsToStages': {
                version: data['stages'] for version, data in self.versions.items()}}
        else:
            fake.get_item.side_effect = lambda **_: {'Item': self.item}
            fake.update_item.side_effect = self._update_item
        return fake


def _load_rotator(fakes):
    boto3 = types.ModuleType('boto3')
    boto3.client = fakes.client
    module = types.ModuleType('rotator')
    module.print = lambda *args, **kwargs: None
    with patch.dict(sys.modules, {'boto3': boto3}), patch.dict('os.environ', {'DB_TABLE': lab.DB_TABLE,
                                                                             'DB_USER': lab.DB_USER}):
        exec(compile(lab.ROTATOR_SOURCE, 'index.py', 'exec'), module.__dict__)
    return module


def _event(step):
    return {'Step': step, 'SecretId': 'arn:secret', 'ClientRequestToken': 'tok'}


class RotatorTests(SimpleTestCase):
    def test_four_steps_rotate_and_keep_the_old_password(self):
        fakes = FakeRotationClients()
        rotator = _load_rotator(fakes)
        for step in lab.ROTATION_STEPS:
            rotator.handler(_event(step), None)
        new_password = json.loads(fakes.versions['tok']['value'])['password']
        self.assertIn('AWSCURRENT', fakes.versions['tok']['stages'])
        self.assertEqual(sorted(fakes.item['passwords']['SS']), sorted([lab.INITIAL_PASSWORD, new_password]))
        self.assertEqual([entry['step']['S'] for entry in fakes.log], lab.ROTATION_STEPS)

    def test_create_secret_is_idempotent(self):
        fakes = FakeRotationClients()
        rotator = _load_rotator(fakes)
        rotator.handler(_event('createSecret'), None)
        first = fakes.versions['tok']['value']
        self.assertEqual(rotator.handler(_event('createSecret'), None)['result'], 'pending value already exists')
        self.assertEqual(fakes.versions['tok']['value'], first)

    def test_locked_database_fails_the_test_and_records_it(self):
        fakes = FakeRotationClients(locked=True)
        rotator = _load_rotator(fakes)
        rotator.handler(_event('createSecret'), None)
        rotator.handler(_event('setSecret'), None)
        with self.assertRaises(RuntimeError):
            rotator.handler(_event('testSecret'), None)
        self.assertTrue(fakes.log[-1]['result']['S'].startswith('failed: login refused'))
        self.assertIn('AWSCURRENT', fakes.versions['v1']['stages'])

    def test_rotator_source_compiles(self):
        compile(lab.ROTATOR_SOURCE, 'index.py', 'exec')


class CheckTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_delegated_check_needs_every_decision(self):
        marker = {'createWithoutBoundary': {'allowed': False}, 'createWithBoundary': {'allowed': True},
                  'removeBoundary': {'allowed': False}, 'widenBoundary': {'allowed': False}}
        lab._marker('delegated-roles', marker)
        with patch.object(lab, '_role', return_value={'PermissionsBoundary': {
                'PermissionsBoundaryArn': lab.BOUNDARY_ARN}}):
            self.assertTrue(lab._check_delegated()[0])
            lab._marker('delegated-roles', {**marker, 'removeBoundary': {'allowed': True}})
            self.assertFalse(lab._check_delegated()[0])

    def test_ceiling_and_chain_checks(self):
        lab._marker('boundary-ceiling', {'sqs': {'allowed': True}, 'iam': {'allowed': False},
                                         'ssm': {'allowed': False}})
        self.assertTrue(lab._check_ceiling()[0])
        lab._marker('boundary-ceiling', {'sqs': {'allowed': True}, 'iam': {'allowed': True},
                                         'ssm': {'allowed': False}})
        self.assertFalse(lab._check_ceiling()[0])
        lab._marker('chained-session', {'caller': f'arn:aws:sts::0:assumed-role/{lab.DEPLOYER}/x',
                                        'jobs': {'allowed': True}, 'audit': {'allowed': False}})
        self.assertTrue(lab._check_chain()[0])

    def test_rotation_checks(self):
        lab._marker('rotate-db-credentials', {'steps': lab.ROTATION_STEPS, 'currentLogsIn': True,
                                              'previousLogsIn': True})
        self.assertTrue(lab._check_rotated()[0])
        lab._marker('failed-rotation', {'failures': ['testSecret: failed: locked'], 'currentUnchanged': True,
                                        'pendingLeft': True, 'retry': {'allowed': False}})
        self.assertTrue(lab._check_failed()[0])
        lab._marker('redrive-rotation', {'steps': lab.ROTATION_STEPS, 'pendingIsCurrent': True,
                                         'currentLogsIn': True, 'passwords': 3})
        self.assertFalse(lab._check_redrive()[0])


class RegistrationTests(SimpleTestCase):
    def test_lab_is_listed_and_tagged(self):
        self.assertIn('machine-identity', [item['key'] for item in labs_for_service('iam')])
        tags = LAB_EXAM_TAGS[('iam', 'machine-identity')]
        self.assertIn({'exam': 'DOP-C02', 'task': '6.1', 'primary': True}, tags)
        card = next(card for card in CONCEPT_CARDS if card['key'] == 'identity-at-scale')
        self.assertIn(('iam', 'machine-identity'), card['related_labs'])
        self.assertIsNone(card['planned'])

    def test_every_step_says_how_floci_emulates_it(self):
        self.assertEqual(len(lab.STEP_KEYS), 8)
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])
            self.assertIn(step['key'], lab.RUNNERS)
            self.assertIn(step['key'], lab.CHECKS)
