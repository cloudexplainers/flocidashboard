import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class BackupPageTemplateTests(SimpleTestCase):

    def test_backup_registry_marks_service_interactive(self):
        service = get_service('backup')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_backup_vault' for action in service.actions))
        self.assertTrue(any(action.name == 'create_backup_plan' for action in service.actions))
        self.assertTrue(any(action.name == 'start_backup_job' for action in service.actions))


class BackupActionsApiTests(SimpleTestCase):
    @patch('dashboard.backup_views.create_backup_vault')
    def test_create_backup_vault_success(self, create_mock):
        create_mock.return_value = {'vault_name': 'my-vault'}

        response = self.client.post(
            reverse('dashboard:backup-vaults'),
            data=json.dumps({'name': 'my-vault', 'tags': {'env': 'dev'}, 'encryption_key_arn': 'key-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('my-vault', tags={'env': 'dev'}, encryption_key_arn='key-1')

    def test_create_backup_vault_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:backup-vaults'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'backup')
        self.assertEqual(response.json()['operation'], 'create_backup_vault')

    @patch('dashboard.backup_views.delete_backup_vault')
    def test_delete_backup_vault_success(self, delete_mock):
        delete_mock.return_value = {'vault_name': 'my-vault'}

        response = self.client.delete(reverse('dashboard:backup-vault-detail', kwargs={'vault_name': 'my-vault'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('my-vault')

    @patch('dashboard.backup_views.create_backup_plan')
    def test_create_backup_plan_success(self, create_mock):
        plan = {'BackupPlanName': 'daily', 'Rules': []}
        create_mock.return_value = {'backup_plan_id': 'plan-1'}

        response = self.client.post(
            reverse('dashboard:backup-plans'),
            data=json.dumps({'backup_plan': plan}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(plan)

    @patch('dashboard.backup_views.update_backup_plan')
    def test_update_backup_plan_success(self, update_mock):
        plan = {'BackupPlanName': 'daily', 'Rules': []}
        update_mock.return_value = {'backup_plan_id': 'plan-1'}

        response = self.client.patch(
            reverse('dashboard:backup-plan-detail', kwargs={'plan_id': 'plan-1'}),
            data=json.dumps({'backup_plan': plan}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        update_mock.assert_called_once_with('plan-1', plan)

    @patch('dashboard.backup_views.delete_backup_plan')
    def test_delete_backup_plan_success(self, delete_mock):
        delete_mock.return_value = {'backup_plan_id': 'plan-1'}

        response = self.client.delete(reverse('dashboard:backup-plan-detail', kwargs={'plan_id': 'plan-1'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('plan-1')

    @patch('dashboard.backup_views.create_backup_selection')
    def test_create_backup_selection_success(self, create_mock):
        selection = {'SelectionName': 'tables', 'IamRoleArn': 'role', 'Resources': ['arn']}
        create_mock.return_value = {'backup_plan_id': 'plan-1', 'selection_id': 'selection-1'}

        response = self.client.post(
            reverse('dashboard:backup-selections', kwargs={'plan_id': 'plan-1'}),
            data=json.dumps({'backup_selection': selection}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('plan-1', selection)

    @patch('dashboard.backup_views.delete_backup_selection')
    def test_delete_backup_selection_success(self, delete_mock):
        delete_mock.return_value = {'backup_plan_id': 'plan-1', 'selection_id': 'selection-1'}

        response = self.client.delete(reverse(
            'dashboard:backup-selection-detail',
            kwargs={'plan_id': 'plan-1', 'selection_id': 'selection-1'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('plan-1', 'selection-1')

    @patch('dashboard.backup_views.start_backup_job')
    def test_start_backup_job_success(self, start_mock):
        start_mock.return_value = {'backup_job_id': 'job-1'}

        response = self.client.post(
            reverse('dashboard:backup-jobs'),
            data=json.dumps({
                'backup_vault_name': 'my-vault',
                'resource_arn': 'arn:aws:dynamodb:us-east-1:000000000000:table/my-table',
                'iam_role_arn': 'arn:aws:iam::000000000000:role/backup-role',
                'lifecycle': {'DeleteAfterDays': 30},
                'recovery_point_tags': {'team': 'platform'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        start_mock.assert_called_once_with(
            'my-vault',
            resource_arn='arn:aws:dynamodb:us-east-1:000000000000:table/my-table',
            iam_role_arn='arn:aws:iam::000000000000:role/backup-role',
            lifecycle={'DeleteAfterDays': 30},
            recovery_point_tags={'team': 'platform'},
        )

    @patch('dashboard.backup_views.stop_backup_job')
    def test_stop_backup_job_success(self, stop_mock):
        stop_mock.return_value = {'backup_job_id': 'job-1'}

        response = self.client.post(reverse('dashboard:backup-job-stop', kwargs={'job_id': 'job-1'}))

        self.assertEqual(response.status_code, 200)
        stop_mock.assert_called_once_with('job-1')

    @patch('dashboard.backup_views.delete_recovery_point')
    def test_delete_recovery_point_success(self, delete_mock):
        delete_mock.return_value = {'backup_vault_name': 'my-vault', 'recovery_point_arn': 'rp-1'}

        response = self.client.delete(
            reverse('dashboard:backup-recovery-point-detail', kwargs={'vault_name': 'my-vault'}),
            data=json.dumps({'recovery_point_arn': 'rp-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('my-vault', 'rp-1')

    @patch('dashboard.backup_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn-1'}

        response = self.client.post(
            reverse('dashboard:backup-tags'),
            data=json.dumps({'resource_arn': 'arn-1', 'tags': {'team': 'platform'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        tag_mock.assert_called_once_with('arn-1', {'team': 'platform'})

    @patch('dashboard.backup_views.untag_resource')
    def test_untag_resource_success(self, untag_mock):
        untag_mock.return_value = {'resource_arn': 'arn-1', 'tag_keys': ['team']}

        response = self.client.delete(
            reverse('dashboard:backup-tags'),
            data=json.dumps({'resource_arn': 'arn-1', 'tag_keys': ['team']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        untag_mock.assert_called_once_with('arn-1', ['team'])
