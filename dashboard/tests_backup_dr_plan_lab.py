"""Backup and DR plan lab: vault, lock, DR vault, plan, tag selection, on-demand backup, and the DR chooser. The fake
behaves like Floci 2.3.0: backup jobs complete with a recovery point, the vault lock refuses early deletion, restores
are unsupported, and selections lose their tag conditions."""

import copy
import importlib
import itertools
import json
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import LAB_BATCH_ORDER, labs_for_service

lab = importlib.import_module('dashboard.labs.backup_labs')

SERVICE = 'backup'
LAB = 'backup-dr-plan'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeAws:
    def __init__(self, keep_tags=False, enforce_lock=True):
        self.ids = itertools.count(1)
        self.keep_tags, self.enforce_lock = keep_tags, enforce_lock
        self.keys, self.aliases = {}, {}
        self.vaults = {}  # (region, name) -> vault
        self.plans, self.selections, self.jobs, self.points = {}, {}, {}, {}
        self.roles, self.attached, self.tables = {}, set(), {}
        self.region = 'us-east-1'

    def for_region(self, region):
        view = copy.copy(self)
        view.region = region or 'us-east-1'
        return view

    def _next(self, prefix):
        return f'{prefix}-{next(self.ids):04d}'

    # KMS
    def create_key(self, Description, Tags):
        key_id = self._next('key')
        self.keys[key_id] = {'KeyId': key_id, 'Arn': f'arn:aws:kms:us-east-1:000000000000:key/{key_id}',
                             'KeyState': 'Enabled'}
        return {'KeyMetadata': dict(self.keys[key_id])}

    def create_alias(self, AliasName, TargetKeyId):
        if AliasName in self.aliases:
            raise client_error('AlreadyExistsException')
        self.aliases[AliasName] = TargetKeyId

    def update_alias(self, AliasName, TargetKeyId):
        self.aliases[AliasName] = TargetKeyId

    def delete_alias(self, AliasName):
        del self.aliases[AliasName]

    def describe_key(self, KeyId):
        key_id = self.aliases.get(KeyId, KeyId)
        if key_id not in self.keys:
            raise client_error('NotFoundException')
        return {'KeyMetadata': dict(self.keys[key_id])}

    def schedule_key_deletion(self, KeyId, PendingWindowInDays):
        key_id = next(key for key, value in self.keys.items() if value['Arn'] == KeyId)
        self.keys[key_id]['KeyState'] = 'PendingDeletion'

    # Vaults
    def _vault(self, name):
        if (self.region, name) not in self.vaults:
            raise client_error('ResourceNotFoundException')
        return self.vaults[(self.region, name)]

    def create_backup_vault(self, BackupVaultName, BackupVaultTags, EncryptionKeyArn=None):
        self.vaults[(self.region, BackupVaultName)] = {
            'BackupVaultName': BackupVaultName, 'EncryptionKeyArn': EncryptionKeyArn, 'Locked': False,
            'NumberOfRecoveryPoints': 0, 'policy': None}

    def describe_backup_vault(self, BackupVaultName):
        vault = self._vault(BackupVaultName)
        return {key: copy.deepcopy(value) for key, value in vault.items() if key != 'policy'}

    def delete_backup_vault(self, BackupVaultName):
        vault = self._vault(BackupVaultName)
        if vault['NumberOfRecoveryPoints']:
            raise client_error('InvalidRequestException', 'Backup vault cannot be deleted because it contains recovery points.')
        del self.vaults[(self.region, BackupVaultName)]

    def put_backup_vault_access_policy(self, BackupVaultName, Policy):
        self._vault(BackupVaultName)['policy'] = Policy

    def get_backup_vault_access_policy(self, BackupVaultName):
        policy = self._vault(BackupVaultName)['policy']
        if not policy:
            raise client_error('ResourceNotFoundException')
        return {'Policy': policy}

    def delete_backup_vault_access_policy(self, BackupVaultName):
        self._vault(BackupVaultName)['policy'] = None

    def put_backup_vault_lock_configuration(self, BackupVaultName, MinRetentionDays, MaxRetentionDays, ChangeableForDays):
        self._vault(BackupVaultName).update(Locked=True, MinRetentionDays=MinRetentionDays, MaxRetentionDays=MaxRetentionDays)

    def delete_backup_vault_lock_configuration(self, BackupVaultName):
        vault = self._vault(BackupVaultName)
        vault['Locked'] = False
        vault.pop('MinRetentionDays', None)
        vault.pop('MaxRetentionDays', None)

    # Plans and selections
    def list_backup_plans(self):
        return {'BackupPlansList': [{'BackupPlanId': key, 'BackupPlanName': plan['BackupPlanName']}
                                    for key, plan in self.plans.items()]}

    def create_backup_plan(self, BackupPlan, BackupPlanTags):
        plan_id = self._next('plan')
        self.plans[plan_id] = copy.deepcopy(BackupPlan)
        return {'BackupPlanId': plan_id}

    def update_backup_plan(self, BackupPlanId, BackupPlan):
        self.plans[BackupPlanId] = copy.deepcopy(BackupPlan)

    def get_backup_plan(self, BackupPlanId):
        return {'BackupPlanId': BackupPlanId, 'BackupPlan': copy.deepcopy(self.plans[BackupPlanId])}

    def delete_backup_plan(self, BackupPlanId):
        if any(item['plan'] == BackupPlanId for item in self.selections.values()):
            raise client_error('InvalidRequestException', 'Plan has selections')
        del self.plans[BackupPlanId]

    def create_backup_selection(self, BackupPlanId, BackupSelection):
        selection = copy.deepcopy(BackupSelection)
        if not self.keep_tags:
            selection.pop('ListOfTags', None)
            selection.setdefault('Resources', [])
        self.selections[self._next('sel')] = {'plan': BackupPlanId, 'selection': selection}

    def list_backup_selections(self, BackupPlanId):
        return {'BackupSelectionsList': [{'SelectionId': key, 'SelectionName': item['selection']['SelectionName']}
                                         for key, item in self.selections.items() if item['plan'] == BackupPlanId]}

    def get_backup_selection(self, BackupPlanId, SelectionId):
        return {'SelectionId': SelectionId, 'BackupSelection': copy.deepcopy(self.selections[SelectionId]['selection'])}

    def delete_backup_selection(self, BackupPlanId, SelectionId):
        del self.selections[SelectionId]

    # Jobs and recovery points
    def start_backup_job(self, BackupVaultName, ResourceArn, IamRoleArn, Lifecycle):
        vault = self._vault(BackupVaultName)
        job_id, point = self._next('job'), f'arn:aws:backup:us-east-1:000000000000:recovery-point:{next(self.ids)}'
        self.points[point] = {'RecoveryPointArn': point, 'ResourceArn': ResourceArn, 'Status': 'COMPLETED',
                              'Lifecycle': Lifecycle, 'vault': BackupVaultName}
        vault['NumberOfRecoveryPoints'] += 1
        self.jobs[job_id] = {'BackupJobId': job_id, 'State': 'COMPLETED', 'RecoveryPointArn': point,
                             'ResourceArn': ResourceArn, 'BackupVaultName': BackupVaultName}
        return {'BackupJobId': job_id}

    def describe_backup_job(self, BackupJobId):
        return dict(self.jobs[BackupJobId])

    def list_recovery_points_by_backup_vault(self, BackupVaultName):
        self._vault(BackupVaultName)
        return {'RecoveryPoints': [{key: value for key, value in point.items() if key != 'vault'}
                                   for point in self.points.values() if point['vault'] == BackupVaultName]}

    def delete_recovery_point(self, BackupVaultName, RecoveryPointArn):
        vault = self._vault(BackupVaultName)
        if self.enforce_lock and vault.get('Locked'):
            raise client_error('InvalidRequestException', 'Recovery point is protected by a vault lock')
        del self.points[RecoveryPointArn]
        vault['NumberOfRecoveryPoints'] -= 1

    def start_restore_job(self, **kwargs):
        raise client_error('AuthorizationHeaderMalformed', 'This endpoint belongs to "s3".')

    # IAM and DynamoDB
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': self.roles[RoleName]}

    def create_role(self, RoleName, AssumeRolePolicyDocument, Description):
        self.roles[RoleName] = {'RoleName': RoleName}

    def attach_role_policy(self, RoleName, PolicyArn):
        self.attached.add((RoleName, PolicyArn))

    def detach_role_policy(self, RoleName, PolicyArn):
        if (RoleName, PolicyArn) not in self.attached:
            raise client_error('NoSuchEntity')
        self.attached.discard((RoleName, PolicyArn))

    def delete_role(self, RoleName):
        if self.roles.pop(RoleName, None) is None:
            raise client_error('NoSuchEntity')

    def describe_table(self, TableName):
        if TableName not in self.tables:
            raise client_error('ResourceNotFoundException')
        return {'Table': {key: value for key, value in self.tables[TableName].items() if key != 'tags'}}

    def create_table(self, TableName, AttributeDefinitions, KeySchema, BillingMode, Tags):
        self.tables[TableName] = {'TableName': TableName, 'TableStatus': 'ACTIVE', 'TableArn': lab.TABLE_ARN,
                                  'tags': {tag['Key']: tag['Value'] for tag in Tags}, 'items': []}

    def tag_resource(self, ResourceArn, Tags):
        self.tables[lab.TABLE]['tags'].update({tag['Key']: tag['Value'] for tag in Tags})

    def list_tags_of_resource(self, ResourceArn):
        return {'Tags': [{'Key': key, 'Value': value} for key, value in self.tables[lab.TABLE]['tags'].items()]}

    def put_item(self, TableName, Item):
        self.tables[TableName]['items'].append(Item)

    def delete_table(self, TableName):
        if self.tables.pop(TableName, None) is None:
            raise client_error('ResourceNotFoundException')


class BackupDrPlanLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        patcher = patch('dashboard.labs.backup_labs._client',
                        side_effect=lambda name, region=None: self.fake.for_region(region))
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = patch('dashboard.labs.backup_labs.time.sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in lab.STEP_KEYS}

    def failed(self):
        return [step for step, item in lab_status(SERVICE, LAB)['steps'].items() if not item['verified']]

    def plan(self):
        return next(iter(self.fake.plans.values()))

    def test_lab_is_registered_with_seven_steps_exam_tags_and_a_batch(self):
        item = next(entry for entry in labs_for_service(SERVICE) if entry['key'] == LAB)
        self.assertEqual(len(item['steps']), 7)
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '2.2'), tasks)
        self.assertIn(('CLF-C02', '3.6'), tasks)
        self.assertIn(SERVICE, [batch['service'] for batch in LAB_BATCH_ORDER])

    def test_every_step_says_how_floci_emulates_it(self):
        for step in lab.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_chooser_and_schedule_parsing(self):
        self.assertEqual(lab.choose_strategy(24 * 60, 12 * 60), 'backup-and-restore')
        self.assertEqual(lab.choose_strategy(15, 60), 'pilot-light')
        self.assertEqual(lab.choose_strategy(30, 24 * 60), 'pilot-light')
        self.assertEqual(lab.choose_strategy(1, 10), 'warm-standby')
        self.assertEqual(lab.choose_strategy(0, 0), 'multi-site')
        self.assertEqual(lab.schedule_interval_minutes('cron(0 5 * * ? *)'), 24 * 60)
        self.assertEqual(lab.schedule_interval_minutes('cron(0 5 1 * ? *)'), 31 * 24 * 60)
        self.assertEqual(lab.schedule_interval_minutes('cron(0 * * * ? *)'), 60)
        self.assertIsNone(lab.schedule_interval_minutes('rate(1 day)'))

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 7)

    def test_messages_say_what_floci_skips(self):
        results = self.run_all()
        self.assertIn('without evaluating', results['backup-vault']['verification']['message'])
        self.assertIn('dropped the tag condition', results['tag-selection']['verification']['message'])
        self.assertIn('refused by the vault lock', results['on-demand-backup']['verification']['message'])
        self.assertIn('restore jobs', results['on-demand-backup']['verification']['message'])
        strategies = [row['Strategy'] for row in results['dr-strategy']['json']['Scenarios']]
        self.assertEqual(strategies, ['Backup and restore', 'Pilot light', 'Warm standby', 'Multi-site active-active'])

    def test_kept_tags_need_no_fallback(self):
        self.fake.keep_tags = True
        results = self.run_all()
        self.assertTrue(results['tag-selection']['verified'])
        self.assertNotIn('dropped', results['tag-selection']['verification']['message'])

    def test_unenforced_lock_fails_the_backup(self):
        self.fake.enforce_lock = False
        for step in lab.STEP_KEYS[:5]:
            run_lab_step(SERVICE, LAB, step)
        result = run_lab_step(SERVICE, LAB, 'on-demand-backup')
        self.assertFalse(result['verified'])
        self.assertIn('should have refused', result['verification']['message'])

    def test_bad_lifecycle_and_missing_copy_fail_the_plan(self):
        self.run_all()
        rules = {rule['RuleName']: rule for rule in self.plan()['Rules']}
        rules[lab.MONTHLY_RULE]['Lifecycle'] = {'MoveToColdStorageAfterDays': 90, 'DeleteAfterDays': 120}
        self.assertIn('at least 180 days', lab._check_plan()[1])
        rules[lab.MONTHLY_RULE]['Lifecycle'] = {'MoveToColdStorageAfterDays': 90, 'DeleteAfterDays': 2555}
        rules[lab.DAILY_RULE]['CopyActions'] = []
        self.assertIn('copy to', lab._check_plan()[1])
        self.assertEqual(self.failed(), ['backup-plan', 'dr-strategy'])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'backup-plan')['verified'])

    def test_vault_policy_and_lock_are_checked(self):
        self.run_all()
        vault = self.fake.vaults[('us-east-1', lab.VAULT)]
        vault['policy'] = json.dumps({'Version': '2012-10-17', 'Statement': []})
        self.assertIn('deny', lab._check_vault()[1])
        vault['MaxRetentionDays'] = 365
        self.assertIn('2555', lab._check_lock()[1])
        self.assertEqual(self.failed()[:2], ['backup-vault', 'vault-lock'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len(self.fake.plans), 1)
        self.assertEqual(len(self.fake.selections), 1)
        self.assertEqual(len(self.fake.keys), 1)
        self.assertEqual(len(self.fake.vaults), 2)

    def test_steps_out_of_order_explain_what_is_missing(self):
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'vault-lock')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'backup-plan')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'tag-selection')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'on-demand-backup')['verification']['message'])
        self.assertIn('first', run_lab_step(SERVICE, LAB, 'dr-strategy')['verification']['message'])

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        for store in (self.fake.vaults, self.fake.plans, self.fake.selections, self.fake.points, self.fake.roles,
                      self.fake.attached, self.fake.tables, self.fake.aliases):
            self.assertFalse(store)
        self.assertEqual({key['KeyState'] for key in self.fake.keys.values()}, {'PendingDeletion'})
        self.assertEqual(lab_status(SERVICE, LAB)['passed_steps'], 0)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])
