import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .aws import eks_inventory
from .eks_api import create_fargate_profile, create_nodegroup
from .services import get_service


class EKSPageTemplateTests(SimpleTestCase):

    def test_eks_registry_marks_service_interactive(self):
        service = get_service('eks')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'create_cluster' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_cluster' for action in service.actions))
        self.assertTrue(any(action.name == 'create_nodegroup' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_nodegroup' for action in service.actions))
        self.assertTrue(any(action.name == 'create_fargate_profile' for action in service.actions))
        self.assertTrue(any(action.name == 'delete_fargate_profile' for action in service.actions))
        self.assertTrue(any(action.name == 'tag_resource' for action in service.actions))


class EKSActionsApiTests(SimpleTestCase):
    @patch('dashboard.eks_views.create_cluster')
    def test_create_cluster_success(self, create_mock):
        create_mock.return_value = {'name': 'local', 'arn': 'arn:aws:eks:us-east-1:000000000000:cluster/local'}

        response = self.client.post(
            reverse('dashboard:eks-clusters'),
            data=json.dumps({
                'name': 'local',
                'role_arn': 'arn:aws:iam::000000000000:role/eks-role',
                'version': '1.29',
                'subnet_ids': ['subnet-1'],
                'security_group_ids': ['sg-1'],
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'local')
        create_mock.assert_called_once_with(
            name='local',
            role_arn='arn:aws:iam::000000000000:role/eks-role',
            version='1.29',
            subnet_ids=['subnet-1'],
            security_group_ids=['sg-1'],
            tags={'env': 'local'},
        )

    def test_create_cluster_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:eks-clusters'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'eks')
        self.assertEqual(response.json()['operation'], 'create_cluster')

    @patch('dashboard.eks_views.delete_cluster')
    def test_delete_cluster_success(self, delete_mock):
        delete_mock.return_value = {'name': 'local', 'status': 'DELETING'}

        response = self.client.post(
            reverse('dashboard:eks-clusters-delete'),
            data=json.dumps({'name': 'local'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'DELETING')
        delete_mock.assert_called_once_with('local')

    @patch('dashboard.eks_views.tag_resource')
    def test_tag_resource_success(self, tag_mock):
        tag_mock.return_value = {'resource_arn': 'arn:cluster/local', 'tags': {'env': 'local'}}

        response = self.client.post(
            reverse('dashboard:eks-tags'),
            data=json.dumps({'resource_arn': 'arn:cluster/local', 'tags': {'env': 'local'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tags'], {'env': 'local'})
        tag_mock.assert_called_once_with('arn:cluster/local', {'env': 'local'})

    @patch('dashboard.eks_views.untag_resource')
    def test_untag_resource_success(self, untag_mock):
        untag_mock.return_value = {'resource_arn': 'arn:cluster/local', 'tag_keys': ['env']}

        response = self.client.delete(
            reverse('dashboard:eks-tags'),
            data=json.dumps({'resource_arn': 'arn:cluster/local', 'tag_keys': ['env']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tag_keys'], ['env'])
        untag_mock.assert_called_once_with('arn:cluster/local', ['env'])

    @patch('dashboard.eks_views.list_tags')
    def test_list_tags_success(self, list_mock):
        list_mock.return_value = {'resource_arn': 'arn:cluster/local', 'tags': {'env': 'local'}}

        response = self.client.post(
            reverse('dashboard:eks-tags-list'),
            data=json.dumps({'resource_arn': 'arn:cluster/local'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['tags'], {'env': 'local'})
        list_mock.assert_called_once_with('arn:cluster/local')

    @patch('dashboard.eks_views.create_nodegroup')
    def test_create_nodegroup_success(self, create_mock):
        create_mock.return_value = {'cluster_name': 'local', 'nodegroup_name': 'workers', 'status': 'CREATING'}

        response = self.client.post(
            reverse('dashboard:eks-nodegroups', kwargs={'cluster_name': 'local'}),
            data=json.dumps({
                'nodegroup_name': 'workers',
                'node_role': 'arn:aws:iam::000000000000:role/eks-node-role',
                'subnets': ['subnet-1'],
                'scaling_config': {'minSize': 1, 'maxSize': 2, 'desiredSize': 1},
                'instance_types': ['t3.small'],
                'ami_type': 'AL2_x86_64',
                'capacity_type': 'ON_DEMAND',
                'disk_size': 20,
                'labels': {'role': 'worker'},
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'CREATING')
        create_mock.assert_called_once_with(
            cluster_name='local',
            nodegroup_name='workers',
            node_role='arn:aws:iam::000000000000:role/eks-node-role',
            subnets=['subnet-1'],
            scaling_config={'minSize': 1, 'maxSize': 2, 'desiredSize': 1},
            instance_types=['t3.small'],
            ami_type='AL2_x86_64',
            capacity_type='ON_DEMAND',
            disk_size=20,
            labels={'role': 'worker'},
            tags={'env': 'local'},
        )

    @patch('dashboard.eks_views.delete_nodegroup')
    def test_delete_nodegroup_success(self, delete_mock):
        delete_mock.return_value = {'cluster_name': 'local', 'nodegroup_name': 'workers', 'status': 'DELETING'}

        response = self.client.delete(
            reverse('dashboard:eks-nodegroup-detail', kwargs={'cluster_name': 'local', 'nodegroup_name': 'workers'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'DELETING')
        delete_mock.assert_called_once_with('local', 'workers')

    def test_create_nodegroup_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:eks-nodegroups', kwargs={'cluster_name': 'local'}),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'eks')
        self.assertEqual(response.json()['operation'], 'create_nodegroup')

    @patch('dashboard.eks_views.create_fargate_profile')
    def test_create_fargate_profile_success(self, create_mock):
        create_mock.return_value = {'cluster_name': 'local', 'fargate_profile_name': 'default', 'status': 'ACTIVE'}
        response = self.client.post(
            reverse('dashboard:eks-fargate-profiles', kwargs={'cluster_name': 'local'}),
            data=json.dumps({
                'profile_name': 'default',
                'pod_execution_role_arn': 'arn:aws:iam::000000000000:role/fargate-role',
                'subnets': ['subnet-1'],
                'selectors': [{'namespace': 'default', 'labels': {'app': 'web'}}],
                'tags': {'env': 'local'},
            }),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ACTIVE')
        create_mock.assert_called_once_with(
            cluster_name='local', profile_name='default',
            pod_execution_role_arn='arn:aws:iam::000000000000:role/fargate-role',
            subnets=['subnet-1'], selectors=[{'namespace': 'default', 'labels': {'app': 'web'}}],
            tags={'env': 'local'},
        )

    @patch('dashboard.eks_views.delete_fargate_profile')
    def test_delete_fargate_profile_success(self, delete_mock):
        delete_mock.return_value = {'cluster_name': 'local', 'fargate_profile_name': 'default', 'status': 'DELETING'}
        response = self.client.delete(reverse(
            'dashboard:eks-fargate-profile-detail',
            kwargs={'cluster_name': 'local', 'profile_name': 'default'},
        ))
        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('local', 'default')


class EKSApiHelperTests(SimpleTestCase):
    @patch('dashboard.eks_api._client')
    def test_create_nodegroup_uses_managed_nodegroup_api(self, client_mock):
        client = MagicMock()
        client.create_nodegroup.return_value = {'nodegroup': {'nodegroupName': 'workers', 'status': 'CREATING'}}
        client_mock.return_value = client

        result = create_nodegroup(
            cluster_name='local',
            nodegroup_name='workers',
            node_role='arn:aws:iam::000000000000:role/eks-node-role',
            subnets=['subnet-1'],
            scaling_config={'minSize': 1, 'maxSize': 2, 'desiredSize': 1},
            instance_types=['t3.small'],
            disk_size='20',
            labels={'role': 'worker'},
            tags={'env': 'local'},
        )

        self.assertEqual(result['nodegroup_name'], 'workers')
        self.assertEqual(result['status'], 'CREATING')
        client.create_nodegroup.assert_called_once()
        kwargs = client.create_nodegroup.call_args.kwargs
        self.assertEqual(kwargs['clusterName'], 'local')
        self.assertEqual(kwargs['nodegroupName'], 'workers')
        self.assertEqual(kwargs['nodeRole'], 'arn:aws:iam::000000000000:role/eks-node-role')
        self.assertEqual(kwargs['subnets'], ['subnet-1'])
        self.assertEqual(kwargs['diskSize'], 20)
        self.assertEqual(kwargs['labels'], {'role': 'worker'})
        self.assertEqual(kwargs['tags'], {'env': 'local'})

    @patch('dashboard.eks_api._client')
    def test_create_fargate_profile_uses_eks_api(self, client_mock):
        client = MagicMock()
        client.create_fargate_profile.return_value = {
            'fargateProfile': {'fargateProfileName': 'default', 'status': 'ACTIVE'},
        }
        client_mock.return_value = client
        result = create_fargate_profile(
            cluster_name='local', profile_name='default',
            pod_execution_role_arn='arn:aws:iam::000000000000:role/fargate-role',
            subnets=['subnet-1'], selectors=[{'namespace': 'default'}], tags={'env': 'local'},
        )
        self.assertEqual(result['fargate_profile_name'], 'default')
        client.create_fargate_profile.assert_called_once_with(
            clusterName='local', fargateProfileName='default',
            podExecutionRoleArn='arn:aws:iam::000000000000:role/fargate-role',
            subnets=['subnet-1'], selectors=[{'namespace': 'default'}], tags={'env': 'local'},
        )

    @patch('dashboard.eks_views.get_kubeconfig')
    def test_get_kubeconfig_endpoint(self, kube_mock):
        kube_mock.return_value = {
            'cluster_name': 'local',
            'aws_cli_command': 'aws eks update-kubeconfig --name local --region us-east-1',
            'kubeconfig_yaml': 'apiVersion: v1',
        }

        response = self.client.get(
            reverse('dashboard:eks-cluster-kubeconfig', kwargs={'cluster_name': 'local'}),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('aws_cli_command', response.json())
        kube_mock.assert_called_once_with('local', region=None)


class EKSInventoryCapabilityTests(SimpleTestCase):
    @patch('dashboard.aws._paginate')
    @patch('dashboard.aws.FlociClientFactory')
    def test_inventory_only_calls_operations_supported_by_floci(self, factory_mock, paginate_mock):
        client = MagicMock()
        factory_mock.return_value.client.return_value = client
        paginate_mock.side_effect = lambda _client, operation, _key, **_kwargs: ['local'] if operation == 'list_clusters' else []
        client.describe_cluster.return_value = {'cluster': {'name': 'local', 'status': 'ACTIVE', 'health': {'issues': [{'code': 'AccessDenied', 'message': 'Cannot access cluster resources'}]}}}

        inventory = eks_inventory()

        self.assertEqual(inventory['summary'], {'clusters': 1, 'nodegroups': 0, 'fargate_profiles': 0})
        self.assertEqual(inventory['clusters'][0]['health']['issues'][0]['code'], 'AccessDenied')
        client.list_addons.assert_not_called()
        client.list_identity_provider_configs.assert_not_called()
        client.list_access_entries.assert_not_called()
        self.assertNotIn('addons', inventory)
        self.assertNotIn('access_entries', inventory)


class EKSCreateClusterRecoveryTests(SimpleTestCase):
    def _closed(self):
        from botocore.exceptions import ConnectionClosedError
        return ConnectionClosedError(endpoint_url='http://localhost:4566/clusters')

    @patch('dashboard.eks_api.time.sleep')
    @patch('dashboard.eks_api._client')
    def test_dropped_create_returns_the_cluster_floci_created(self, client_mock, _sleep):
        from .eks_api import create_cluster
        client = client_mock.return_value
        client.create_cluster.side_effect = self._closed()
        client.describe_cluster.return_value = {'cluster': {'name': 'demo', 'arn': 'arn:demo', 'status': 'CREATING'}}

        result = create_cluster(name='demo', role_arn='arn:aws:iam::000000000000:role/eks', subnet_ids='subnet-1')

        self.assertEqual(result['status'], 'CREATING')
        self.assertEqual(result['arn'], 'arn:demo')
        client.create_cluster.assert_called_once()
        client.describe_cluster.assert_called_once_with(name='demo')

    @patch('dashboard.eks_api.time.sleep')
    @patch('dashboard.eks_api._client')
    def test_dropped_create_raises_when_no_cluster_appears(self, client_mock, sleep_mock):
        from botocore.exceptions import ClientError, ConnectionClosedError
        from .eks_api import create_cluster
        client = client_mock.return_value
        client.create_cluster.side_effect = self._closed()
        client.describe_cluster.side_effect = ClientError(
            {'Error': {'Code': 'ResourceNotFoundException', 'Message': 'missing'}}, 'DescribeCluster')

        with self.assertRaises(ConnectionClosedError):
            create_cluster(name='demo', role_arn='arn:aws:iam::000000000000:role/eks', subnet_ids='subnet-1')

        self.assertEqual(client.describe_cluster.call_count, 5)
        client.create_cluster.assert_called_once()


class EKSClientTimeoutTests(SimpleTestCase):
    @patch('dashboard.eks_api.FlociClientFactory')
    def test_cluster_mutations_allow_runtime_startup_without_retries(self, factory_mock):
        from botocore.config import Config
        from .eks_api import _client
        factory = factory_mock.return_value
        factory.config = Config(connect_timeout=2, read_timeout=5, retries={'max_attempts': 2})
        _client(long_running=True)
        self.assertEqual(factory.config.read_timeout, 120)
        self.assertEqual(factory.config.retries['max_attempts'], 0)
        factory.client.assert_called_once_with('eks')

    @patch('dashboard.eks_api.FlociClientFactory')
    def test_regular_eks_calls_retain_short_timeouts(self, factory_mock):
        from botocore.config import Config
        from .eks_api import _client
        factory = factory_mock.return_value
        factory.config = Config(read_timeout=5)
        _client()
        self.assertEqual(factory.config.read_timeout, 5)
