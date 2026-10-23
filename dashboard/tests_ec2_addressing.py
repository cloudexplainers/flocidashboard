"""Elastic IP and NAT gateway helpers behind the EC2 console tables."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .ec2_api import allocate_elastic_ip, create_nat_gateway, set_elastic_ip_association


class ElasticIpHelperTests(SimpleTestCase):

    @patch('dashboard.ec2_api._ec2_client')
    def test_allocate_without_name_sends_only_domain(self, client_factory):
        client = client_factory.return_value
        client.allocate_address.return_value = {'AllocationId': 'eipalloc-1', 'PublicIp': '54.0.0.1', 'Domain': 'vpc'}
        result = allocate_elastic_ip()
        client.allocate_address.assert_called_once_with(Domain='vpc')
        self.assertEqual(result, {'allocation_id': 'eipalloc-1', 'public_ip': '54.0.0.1', 'domain': 'vpc'})

    @patch('dashboard.ec2_api._ec2_client')
    def test_allocate_with_name_tags_the_address(self, client_factory):
        client = client_factory.return_value
        client.allocate_address.return_value = {'AllocationId': 'eipalloc-1'}
        allocate_elastic_ip(name=' web-eip ', tags={'env': 'lab'})
        client.allocate_address.assert_called_once_with(Domain='vpc', TagSpecifications=[{
            'ResourceType': 'elastic-ip',
            'Tags': [{'Key': 'env', 'Value': 'lab'}, {'Key': 'Name', 'Value': 'web-eip'}],
        }])

    @patch('dashboard.ec2_api._ec2_client')
    def test_associate_passes_allow_reassociation_only_when_set(self, client_factory):
        client = client_factory.return_value
        client.associate_address.return_value = {'AssociationId': 'eipassoc-1'}
        set_elastic_ip_association('eipalloc-1', instance_id='i-1')
        client.associate_address.assert_called_with(AllocationId='eipalloc-1', InstanceId='i-1')
        set_elastic_ip_association('eipalloc-1', instance_id='i-1', allow_reassociation=True)
        client.associate_address.assert_called_with(AllocationId='eipalloc-1', InstanceId='i-1', AllowReassociation=True)

    @patch('dashboard.ec2_views.allocate_elastic_ip', return_value={'allocation_id': 'eipalloc-1'})
    def test_allocate_endpoint_accepts_a_name(self, allocate):
        response = self.client.post(reverse('dashboard:ec2-elastic-ips-allocate'), data=json.dumps({'name': 'web-eip'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        allocate.assert_called_once_with(name='web-eip', tags=None)

    @patch('dashboard.ec2_views.set_elastic_ip_association', return_value={'association_id': 'eipassoc-1'})
    def test_associate_endpoint_forwards_allow_reassociation(self, associate):
        route = reverse('dashboard:ec2-elastic-ip-association', kwargs={'allocation_id': 'eipalloc-1'})
        self.client.put(route, data=json.dumps({'instance_id': 'i-1', 'allow_reassociation': True}), content_type='application/json')
        associate.assert_called_once_with('eipalloc-1', instance_id='i-1', association_id=None, disassociate=False, allow_reassociation=True)


class NatGatewayHelperTests(SimpleTestCase):

    def setUp(self):
        patcher = patch('dashboard.ec2_api._ec2_client')
        self.client_factory = patcher.start()
        self.addCleanup(patcher.stop)
        self.ec2 = MagicMock()
        self.ec2.create_nat_gateway.return_value = {'NatGateway': {'NatGatewayId': 'nat-1'}}
        self.client_factory.return_value = self.ec2

    def test_public_gateway_needs_an_elastic_ip(self):
        # Floci 2.3.0 accepts this; AWS rejects it, so the helper does.
        with self.assertRaisesMessage(ValueError, 'needs an Elastic IP'):
            create_nat_gateway('subnet-1', None, connectivity_type='public')
        self.ec2.create_nat_gateway.assert_not_called()

    def test_private_gateway_rejects_an_elastic_ip(self):
        with self.assertRaisesMessage(ValueError, 'private NAT gateways'):
            create_nat_gateway('subnet-1', 'eipalloc-1', connectivity_type='private')

    def test_unknown_connectivity_type_is_rejected(self):
        with self.assertRaisesMessage(ValueError, 'public or private'):
            create_nat_gateway('subnet-1', 'eipalloc-1', connectivity_type='internet')

    def test_public_gateway_with_name(self):
        result = create_nat_gateway('subnet-1', 'eipalloc-1', connectivity_type='public', name='nat-a')
        self.ec2.create_nat_gateway.assert_called_once_with(
            SubnetId='subnet-1', ConnectivityType='public', AllocationId='eipalloc-1',
            TagSpecifications=[{'ResourceType': 'natgateway', 'Tags': [{'Key': 'Name', 'Value': 'nat-a'}]}],
        )
        self.assertEqual(result['nat_gateway_id'], 'nat-1')

    def test_private_gateway_sends_no_allocation(self):
        create_nat_gateway('subnet-1', '', connectivity_type='private')
        self.ec2.create_nat_gateway.assert_called_once_with(SubnetId='subnet-1', ConnectivityType='private')

    def test_create_endpoint_reports_a_missing_elastic_ip(self):
        response = self.client.post(reverse('dashboard:ec2-nat-gateways-create'), data=json.dumps({'subnet_id': 'subnet-1', 'connectivity_type': 'public'}), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('Elastic IP', json.dumps(response.json()))
