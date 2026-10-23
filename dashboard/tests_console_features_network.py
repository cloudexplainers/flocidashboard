"""Route 53 private-zone VPC association and CloudFront origin access control endpoints."""

import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase
from django.urls import reverse


def send(client, method, url, body=None):
    kwargs = {} if body is None else {'data': json.dumps(body), 'content_type': 'application/json'}
    return getattr(client, method)(url, **kwargs)


@patch('dashboard.route53_api.FlociClientFactory')
class Route53VpcAssociationTests(SimpleTestCase):
    def clients(self, factory, vpcs=('vpc-1', 'vpc-2'), zone_vpcs=()):
        route53, ec2 = MagicMock(), MagicMock()
        factory.return_value.region = 'us-east-1'
        factory.return_value.client.side_effect = lambda service: {'route53': route53, 'ec2': ec2}[service]
        ec2.describe_vpcs.side_effect = lambda VpcIds=None: {'Vpcs': [
            {'VpcId': vpc, 'CidrBlock': '10.0.0.0/16', 'Tags': [{'Key': 'Name', 'Value': f'{vpc}-name'}]}
            for vpc in vpcs if VpcIds is None or vpc in VpcIds]}
        route53.get_hosted_zone.return_value = {'VPCs': [{'VPCId': vpc, 'VPCRegion': 'us-east-1'} for vpc in zone_vpcs]}
        route53.associate_vpc_with_hosted_zone.return_value = {'ChangeInfo': {'Status': 'INSYNC'}}
        route53.disassociate_vpc_from_hosted_zone.return_value = {'ChangeInfo': {'Status': 'INSYNC'}}
        return route53, ec2

    def test_vpc_choices(self, factory):
        self.clients(factory)
        data = self.client.get(reverse('dashboard:route53-vpc-choices')).json()
        self.assertEqual(data['region'], 'us-east-1')
        self.assertEqual([(vpc['vpc_id'], vpc['name']) for vpc in data['vpcs']], [('vpc-1', 'vpc-1-name'), ('vpc-2', 'vpc-2-name')])

    def test_create_private_zone_with_vpc(self, factory):
        route53, _ = self.clients(factory)
        route53.create_hosted_zone.return_value = {'HostedZone': {'Id': '/hostedzone/Z1'}}
        response = send(self.client, 'post', reverse('dashboard:route53-hosted-zones'), {
            'name': 'corp.internal', 'caller_reference': 'r1', 'vpc_id': 'vpc-1'})
        self.assertEqual(response.status_code, 200)
        route53.create_hosted_zone.assert_called_once_with(
            Name='corp.internal.', CallerReference='r1', VPC={'VPCRegion': 'us-east-1', 'VPCId': 'vpc-1'},
            HostedZoneConfig={'PrivateZone': True})

    def test_create_private_zone_refuses_missing_vpc(self, factory):
        route53, _ = self.clients(factory)
        response = send(self.client, 'post', reverse('dashboard:route53-hosted-zones'), {
            'name': 'corp.internal', 'caller_reference': 'r1', 'vpc_id': 'vpc-404'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('does not exist', response.json()['error'])
        route53.create_hosted_zone.assert_not_called()

    def test_associate_vpc(self, factory):
        route53, _ = self.clients(factory, zone_vpcs=('vpc-1',))
        url = reverse('dashboard:route53-hosted-zone-vpcs', kwargs={'zone_id': 'Z1'})
        response = send(self.client, 'post', url, {'vpc_id': 'vpc-2'})
        self.assertEqual(response.status_code, 200)
        route53.associate_vpc_with_hosted_zone.assert_called_once_with(HostedZoneId='Z1', VPC={'VPCRegion': 'us-east-1', 'VPCId': 'vpc-2'})

    def test_associate_refuses_duplicate(self, factory):
        route53, _ = self.clients(factory, zone_vpcs=('vpc-1',))
        response = send(self.client, 'post', reverse('dashboard:route53-hosted-zone-vpcs', kwargs={'zone_id': 'Z1'}), {'vpc_id': 'vpc-1'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('already associated', response.json()['error'])
        route53.associate_vpc_with_hosted_zone.assert_not_called()

    def test_disassociate_keeps_last_vpc(self, factory):
        route53, _ = self.clients(factory, zone_vpcs=('vpc-1',))
        url = reverse('dashboard:route53-hosted-zone-vpcs', kwargs={'zone_id': 'Z1'})
        response = send(self.client, 'delete', url, {'vpc_id': 'vpc-1'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('at least one VPC', response.json()['error'])
        route53.disassociate_vpc_from_hosted_zone.assert_not_called()

    def test_disassociate_vpc(self, factory):
        route53, _ = self.clients(factory, zone_vpcs=('vpc-1', 'vpc-2'))
        send(self.client, 'delete', reverse('dashboard:route53-hosted-zone-vpcs', kwargs={'zone_id': 'Z1'}), {'vpc_id': 'vpc-2'})
        route53.disassociate_vpc_from_hosted_zone.assert_called_once_with(HostedZoneId='Z1', VPC={'VPCRegion': 'us-east-1', 'VPCId': 'vpc-2'})


@patch('dashboard.cloudfront_api.FlociClientFactory')
class CloudFrontOriginAccessControlTests(SimpleTestCase):
    def test_list_shows_distributions_using_each_control(self, factory):
        cloudfront = factory.return_value.client.return_value
        cloudfront.list_origin_access_controls.return_value = {'OriginAccessControlList': {'Items': [
            {'Id': 'OAC1', 'Name': 'assets'}, {'Id': 'OAC2', 'Name': 'unused'}]}}
        cloudfront.list_distributions.return_value = {'DistributionList': {'Items': [
            {'Id': 'D1', 'Origins': {'Items': [{'Id': 'o', 'OriginAccessControlId': 'OAC1'}]}}]}}
        controls = self.client.get(reverse('dashboard:cloudfront-origin-access-controls')).json()['origin_access_controls']
        self.assertEqual([(c['Id'], c['UsedBy']) for c in controls], [('OAC1', ['D1']), ('OAC2', [])])

    def test_create_control(self, factory):
        cloudfront = factory.return_value.client.return_value
        cloudfront.create_origin_access_control.return_value = {'OriginAccessControl': {'Id': 'OAC1'}, 'ETag': 'E1'}
        response = send(self.client, 'post', reverse('dashboard:cloudfront-origin-access-controls'), {'name': 'assets'})
        self.assertEqual(response.json()['origin_access_control']['Id'], 'OAC1')
        cloudfront.create_origin_access_control.assert_called_once_with(OriginAccessControlConfig={
            'Name': 'assets', 'Description': '', 'SigningProtocol': 'sigv4', 'SigningBehavior': 'always',
            'OriginAccessControlOriginType': 's3'})

    def test_create_control_checks_values(self, factory):
        for body in ({'name': ''}, {'name': 'x' * 65}, {'name': 'a', 'origin_type': 'ec2'}, {'name': 'a', 'signing_behavior': 'sometimes'}):
            self.assertEqual(send(self.client, 'post', reverse('dashboard:cloudfront-origin-access-controls'), body).status_code, 400, body)
        factory.return_value.client.return_value.create_origin_access_control.assert_not_called()

    def test_delete_control_uses_current_etag(self, factory):
        cloudfront = factory.return_value.client.return_value
        cloudfront.get_origin_access_control.return_value = {'ETag': 'E2'}
        response = send(self.client, 'delete', reverse('dashboard:cloudfront-origin-access-control-detail', kwargs={'control_id': 'OAC1'}))
        self.assertEqual(response.json(), {'deleted': 'OAC1'})
        cloudfront.delete_origin_access_control.assert_called_once_with(Id='OAC1', IfMatch='E2')

    def test_delete_in_use_control_reports_floci_error(self, factory):
        cloudfront = factory.return_value.client.return_value
        cloudfront.get_origin_access_control.return_value = {'ETag': 'E2'}
        cloudfront.delete_origin_access_control.side_effect = ClientError(
            {'Error': {'Code': 'OriginAccessControlInUse', 'Message': 'in use'}, 'ResponseMetadata': {'HTTPStatusCode': 409}}, 'Delete')
        response = send(self.client, 'delete', reverse('dashboard:cloudfront-origin-access-control-detail', kwargs={'control_id': 'OAC1'}))
        self.assertGreaterEqual(response.status_code, 400)

    def test_distribution_with_control_on_s3_origin(self, factory):
        cloudfront = factory.return_value.client.return_value
        cloudfront.create_distribution.return_value = {'Distribution': {'Id': 'D1'}}
        send(self.client, 'post', reverse('dashboard:cloudfront-distributions'), {
            'caller_reference': 'r', 'origin_id': 's3', 'origin_domain_name': 'b.s3.amazonaws.com', 'origin_access_control_id': 'OAC1'})
        origin = cloudfront.create_distribution.call_args.kwargs['DistributionConfig']['Origins']['Items'][0]
        self.assertEqual(origin['OriginAccessControlId'], 'OAC1')
        self.assertEqual(origin['S3OriginConfig'], {'OriginAccessIdentity': ''})

    def test_control_refused_on_custom_origin(self, factory):
        response = send(self.client, 'post', reverse('dashboard:cloudfront-distributions'), {
            'caller_reference': 'r', 'origin_id': 'web', 'origin_domain_name': 'example.com',
            'origin_type': 'custom', 'origin_access_control_id': 'OAC1'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('S3 origins only', response.json()['error'])
