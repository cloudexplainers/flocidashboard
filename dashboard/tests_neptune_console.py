import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class NeptunePageTemplateTests(SimpleTestCase):

    def test_neptune_registry_marks_service_interactive(self):
        service = get_service('neptune')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_db_cluster' for action in service.actions))
        self.assertTrue(any(action.name == 'create_db_instance' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_db_cluster' for action in service.actions))


class NeptuneActionsApiTests(SimpleTestCase):
    @patch('dashboard.neptune_views.create_db_cluster')
    def test_create_db_cluster_success(self, create_mock):
        create_mock.return_value = {'cluster_identifier': 'my-neptune'}

        response = self.client.post(
            reverse('dashboard:neptune-clusters'),
            data=json.dumps({'identifier': 'my-neptune', 'engine': 'neptune', 'options': {'BackupRetentionPeriod': 1}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('my-neptune', engine='neptune', options={'BackupRetentionPeriod': 1})

    def test_create_db_cluster_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:neptune-clusters'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'neptune')
        self.assertEqual(response.json()['operation'], 'create_db_cluster')

    @patch('dashboard.neptune_views.modify_db_cluster')
    def test_modify_db_cluster_success(self, modify_mock):
        modify_mock.return_value = {'cluster_identifier': 'my-neptune'}

        response = self.client.patch(
            reverse('dashboard:neptune-cluster-detail', kwargs={'cluster_identifier': 'my-neptune'}),
            data=json.dumps({'options': {'ApplyImmediately': True}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        modify_mock.assert_called_once_with('my-neptune', options={'ApplyImmediately': True})

    @patch('dashboard.neptune_views.delete_db_cluster')
    def test_delete_db_cluster_success(self, delete_mock):
        delete_mock.return_value = {'cluster_identifier': 'my-neptune'}

        response = self.client.delete(
            reverse('dashboard:neptune-cluster-detail', kwargs={'cluster_identifier': 'my-neptune'}),
            data=json.dumps({'skip_final_snapshot': True}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('my-neptune', skip_final_snapshot=True)

    @patch('dashboard.neptune_views.create_db_instance')
    def test_create_db_instance_success(self, create_mock):
        create_mock.return_value = {'instance_identifier': 'my-neptune-instance'}

        response = self.client.post(
            reverse('dashboard:neptune-instances'),
            data=json.dumps({
                'identifier': 'my-neptune-instance',
                'cluster_identifier': 'my-neptune',
                'instance_class': 'db.r5.large',
                'engine': 'neptune',
                'options': {'AutoMinorVersionUpgrade': True},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with(
            'my-neptune-instance',
            cluster_identifier='my-neptune',
            instance_class='db.r5.large',
            engine='neptune',
            options={'AutoMinorVersionUpgrade': True},
        )

    @patch('dashboard.neptune_views.modify_db_instance')
    def test_modify_db_instance_success(self, modify_mock):
        modify_mock.return_value = {'instance_identifier': 'my-neptune-instance'}

        response = self.client.patch(
            reverse('dashboard:neptune-instance-detail', kwargs={'instance_identifier': 'my-neptune-instance'}),
            data=json.dumps({'options': {'ApplyImmediately': True}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        modify_mock.assert_called_once_with('my-neptune-instance', options={'ApplyImmediately': True})

    @patch('dashboard.neptune_views.delete_db_instance')
    def test_delete_db_instance_success(self, delete_mock):
        delete_mock.return_value = {'instance_identifier': 'my-neptune-instance'}

        response = self.client.delete(reverse(
            'dashboard:neptune-instance-detail',
            kwargs={'instance_identifier': 'my-neptune-instance'},
        ))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('my-neptune-instance')
