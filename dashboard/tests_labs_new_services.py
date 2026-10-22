"""Tests for new Top 15 AWS service workflow labs (RDS, ECS, EKS)."""

from django.test import TestCase
from unittest.mock import MagicMock, patch

from dashboard.labs.registry import all_labs, labs_for_service
from dashboard.labs.monolith import lab_status, reset_lab, run_lab_step


class NewServiceLabsLifecycleTests(TestCase):
    def test_registry_contains_new_services(self):
        services = {lab['service'] for lab in all_labs()}
        self.assertIn('rds', services)
        self.assertIn('ecs', services)
        self.assertIn('eks', services)

    @patch('dashboard.labs.rds_labs.client')
    def test_rds_lab_lifecycle(self, client_mock):
        from django.core.cache import cache
        cache.clear()
        mock_rds = MagicMock()
        client_mock.return_value = mock_rds

        mock_rds.create_db_parameter_group.return_value = {'DBParameterGroup': {'DBParameterGroupName': 'lab-pg-ecommerce', 'DBParameterGroupFamily': 'postgres15'}}
        mock_rds.describe_db_parameter_groups.return_value = {'DBParameterGroups': [{'DBParameterGroupName': 'lab-pg-ecommerce'}]}
        mock_rds.create_db_instance.return_value = {'DBInstance': {'DBInstanceIdentifier': 'lab-db-orders', 'Engine': 'postgres'}}
        mock_rds.describe_db_instances.return_value = {'DBInstances': [{
            'DBInstanceIdentifier': 'lab-db-orders',
            'DBInstanceStatus': 'available',
            'StorageEncrypted': True,
            'BackupRetentionPeriod': 7,
            'AllocatedStorage': 40,
            'Endpoint': {'Address': 'localhost', 'Port': 5432},
        }]}
        mock_rds.modify_db_instance.return_value = {'DBInstance': {'DBInstanceIdentifier': 'lab-db-orders', 'AllocatedStorage': 40}}
        mock_rds.reboot_db_instance.return_value = {'DBInstance': {'DBInstanceIdentifier': 'lab-db-orders', 'DBInstanceStatus': 'rebooting'}}
        mock_rds.delete_db_instance.return_value = {}
        mock_rds.delete_db_parameter_group.return_value = {}

        labs = labs_for_service('rds')
        self.assertEqual(len(labs), 1)
        lab = labs[0]
        self.assertEqual(lab['key'], 'db-instance-lifecycle')

        # Reset
        reset_res = reset_lab('rds', 'db-instance-lifecycle')
        self.assertEqual(reset_res['status'], 'reset')

        # Step 1: Create parameter group
        res1 = run_lab_step('rds', 'db-instance-lifecycle', 'create-parameter-group')
        self.assertTrue(res1['verified'])

        # Step 2: Create DB instance
        res2 = run_lab_step('rds', 'db-instance-lifecycle', 'create-db-instance')
        self.assertTrue(res2['verified'])

        # Step 3: Describe DB instance
        res3 = run_lab_step('rds', 'db-instance-lifecycle', 'describe-db-instance')
        self.assertTrue(res3['verified'])

        # Step 4: Modify DB instance
        res4 = run_lab_step('rds', 'db-instance-lifecycle', 'modify-db-instance')
        self.assertTrue(res4['verified'])

        # Step 5: Reboot DB instance
        res5 = run_lab_step('rds', 'db-instance-lifecycle', 'reboot-db-instance')
        self.assertTrue(res5['verified'])

        # Status check
        st = lab_status('rds', 'db-instance-lifecycle')
        self.assertEqual(st['status'], 'passed')
        self.assertEqual(st['passed_steps'], 5)

        # Teardown reset
        reset_lab('rds', 'db-instance-lifecycle')

    @patch('dashboard.labs.ecs_labs.network_configuration', return_value={'awsvpcConfiguration': {'subnets': ['subnet-lab'], 'securityGroups': ['sg-lab'], 'assignPublicIp': 'ENABLED'}})
    @patch('dashboard.labs.ecs_labs.client')
    def test_ecs_lab_lifecycle(self, client_mock, network_mock):
        from django.core.cache import cache
        cache.clear()
        mock_ecs = MagicMock()
        client_mock.return_value = mock_ecs

        mock_ecs.create_cluster.return_value = {'cluster': {'clusterName': 'lab-microservices-cluster', 'status': 'ACTIVE'}}
        mock_ecs.describe_clusters.return_value = {'clusters': [{'clusterName': 'lab-microservices-cluster', 'status': 'ACTIVE'}]}
        mock_ecs.register_task_definition.return_value = {'taskDefinition': {'taskDefinitionArn': 'arn:aws:ecs:us-east-1:000000000000:task-definition/lab-web-service:1', 'family': 'lab-web-service'}}
        mock_ecs.run_task.return_value = {'tasks': [{'taskArn': 'arn:aws:ecs:us-east-1:000000000000:task/lab-microservices-cluster/task1', 'lastStatus': 'RUNNING'}]}
        mock_ecs.describe_tasks.return_value = {'tasks': [{'taskArn': 'arn:aws:ecs:us-east-1:000000000000:task/lab-microservices-cluster/task1', 'lastStatus': 'RUNNING'}]}
        mock_ecs.create_service.return_value = {'service': {'serviceName': 'lab-order-service', 'desiredCount': 2}}
        mock_ecs.describe_services.return_value = {'services': [{'serviceName': 'lab-order-service', 'desiredCount': 4, 'status': 'ACTIVE'}]}
        mock_ecs.update_service.return_value = {'service': {'serviceName': 'lab-order-service', 'desiredCount': 4}}
        mock_ecs.delete_service.return_value = {}
        mock_ecs.stop_task.return_value = {}
        mock_ecs.delete_cluster.return_value = {}
        mock_ecs.deregister_task_definition.return_value = {}

        labs = labs_for_service('ecs')
        self.assertEqual(len(labs), 1)
        lab = labs[0]
        self.assertEqual(lab['key'], 'fargate-microservice')

        # Reset
        reset_res = reset_lab('ecs', 'fargate-microservice')
        self.assertEqual(reset_res['status'], 'reset')

        # Step 1: Create cluster
        res1 = run_lab_step('ecs', 'fargate-microservice', 'create-cluster')
        self.assertTrue(res1['verified'])

        # Step 2: Register task def
        res2 = run_lab_step('ecs', 'fargate-microservice', 'register-task-definition')
        self.assertTrue(res2['verified'])

        # Step 3: Run task
        res3 = run_lab_step('ecs', 'fargate-microservice', 'run-task')
        self.assertTrue(res3['verified'])

        # Step 4: Describe task
        res4 = run_lab_step('ecs', 'fargate-microservice', 'describe-tasks')
        self.assertTrue(res4['verified'])

        # Step 5: Create service
        res5 = run_lab_step('ecs', 'fargate-microservice', 'create-service')
        self.assertTrue(res5['verified'])

        # Step 6: Update service
        res6 = run_lab_step('ecs', 'fargate-microservice', 'update-service')
        self.assertTrue(res6['verified'])

        # Status check
        st = lab_status('ecs', 'fargate-microservice')
        self.assertEqual(st['status'], 'passed')
        self.assertEqual(st['passed_steps'], 6)

        # Teardown reset
        reset_lab('ecs', 'fargate-microservice')

    @patch('dashboard.labs.eks_labs.client')
    def test_eks_lab_lifecycle(self, client_mock):
        from django.core.cache import cache
        cache.clear()
        mock_eks = MagicMock()
        mock_ec2 = MagicMock()
        client_mock.side_effect = lambda name: mock_eks if name == 'eks' else mock_ec2

        mock_ec2.describe_subnets.return_value = {'Subnets': [{'SubnetId': 'subnet-1'}, {'SubnetId': 'subnet-2'}]}
        mock_eks.create_cluster.return_value = {'cluster': {'name': 'lab-k8s-cluster', 'status': 'ACTIVE', 'endpoint': 'https://eks.local'}}
        mock_eks.describe_cluster.return_value = {'cluster': {'name': 'lab-k8s-cluster', 'status': 'ACTIVE', 'endpoint': 'https://eks.local'}}
        mock_eks.create_nodegroup.return_value = {'nodegroup': {'nodegroupName': 'lab-workers', 'status': 'ACTIVE'}}
        mock_eks.describe_nodegroup.return_value = {'nodegroup': {'nodegroupName': 'lab-workers', 'status': 'ACTIVE'}}
        mock_eks.create_fargate_profile.return_value = {'fargateProfile': {'fargateProfileName': 'lab-fargate-profile', 'status': 'ACTIVE'}}
        mock_eks.describe_fargate_profile.return_value = {'fargateProfile': {'fargateProfileName': 'lab-fargate-profile', 'status': 'ACTIVE'}}
        mock_eks.delete_fargate_profile.return_value = {}
        mock_eks.delete_nodegroup.return_value = {}
        mock_eks.delete_cluster.return_value = {}

        labs = labs_for_service('eks')
        self.assertEqual(len(labs), 1)
        lab = labs[0]
        self.assertEqual(lab['key'], 'control-plane-nodegroup')

        # Reset
        reset_res = reset_lab('eks', 'control-plane-nodegroup')
        self.assertEqual(reset_res['status'], 'reset')

        # Step 1: Create cluster
        res1 = run_lab_step('eks', 'control-plane-nodegroup', 'create-cluster')
        self.assertTrue(res1['verified'])

        # Step 2: Describe cluster
        res2 = run_lab_step('eks', 'control-plane-nodegroup', 'describe-cluster')
        self.assertTrue(res2['verified'])

        # Step 3: Create nodegroup
        res3 = run_lab_step('eks', 'control-plane-nodegroup', 'create-nodegroup')
        self.assertTrue(res3['verified'])

        # Step 4: Create fargate profile
        res4 = run_lab_step('eks', 'control-plane-nodegroup', 'create-fargate-profile')
        self.assertTrue(res4['verified'])

        # Step 5: Generate kubeconfig
        res5 = run_lab_step('eks', 'control-plane-nodegroup', 'generate-kubeconfig')
        self.assertTrue(res5['verified'])

        # Status check
        st = lab_status('eks', 'control-plane-nodegroup')
        self.assertEqual(st['status'], 'passed')
        self.assertEqual(st['passed_steps'], 5)

        # Teardown reset
        reset_lab('eks', 'control-plane-nodegroup')
