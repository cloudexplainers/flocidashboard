"""Endpoints for Floci features the console didn't show (see docs/console-accuracy-review.md)."""

import json
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase
from django.urls import reverse


def client_error(code):
    return ClientError({'Error': {'Code': code, 'Message': code}}, 'Operation')


class JsonClientMixin:
    def send(self, method, url, body=None):
        kwargs = {} if body is None else {'data': json.dumps(body), 'content_type': 'application/json'}
        return getattr(self.client, method)(url, **kwargs)


@patch('dashboard.iam_api.FlociClientFactory')
class IamAccountSecurityTests(JsonClientMixin, SimpleTestCase):
    def test_missing_password_policy_is_null(self, factory):
        factory.return_value.client.return_value.get_account_password_policy.side_effect = client_error('NoSuchEntity')
        response = self.client.get(reverse('dashboard:iam-password-policy'))
        self.assertEqual(response.json(), {'policy': None})

    def test_update_password_policy_sends_only_chosen_limits(self, factory):
        iam = factory.return_value.client.return_value
        iam.get_account_password_policy.return_value = {'PasswordPolicy': {'MinimumPasswordLength': 14}}
        response = self.send('put', reverse('dashboard:iam-password-policy'), {
            'minimum_password_length': 14, 'require_symbols': True, 'require_numbers': False,
            'max_password_age': 0, 'password_reuse_prevention': '5'})
        self.assertEqual(response.status_code, 200)
        iam.update_account_password_policy.assert_called_once_with(
            MinimumPasswordLength=14, RequireSymbols=True, RequireNumbers=False, PasswordReusePrevention=5)
        self.assertEqual(response.json()['policy']['MinimumPasswordLength'], 14)

    def test_update_password_policy_checks_aws_limits(self, factory):
        for body, message in (({'minimum_password_length': 5}, 'from 6 to 128'),
                              ({'max_password_age': 2000}, 'from 1 to 1095'),
                              ({'password_reuse_prevention': 25}, 'from 1 to 24')):
            response = self.send('put', reverse('dashboard:iam-password-policy'), body)
            self.assertEqual(response.status_code, 400, body)
            self.assertIn(message, response.json()['error'])
        factory.return_value.client.return_value.update_account_password_policy.assert_not_called()

    def test_delete_password_policy(self, factory):
        response = self.send('delete', reverse('dashboard:iam-password-policy'))
        self.assertTrue(response.json()['deleted'])
        factory.return_value.client.return_value.delete_account_password_policy.assert_called_once_with()

    def test_generate_and_read_credential_report(self, factory):
        iam = factory.return_value.client.return_value
        iam.generate_credential_report.return_value = {'State': 'COMPLETE'}
        self.assertEqual(self.client.post(reverse('dashboard:iam-credential-report')).json()['state'], 'COMPLETE')
        iam.get_credential_report.return_value = {'Content': b'user,arn,password_enabled\nalice,arn:a,true\n'}
        report = self.client.get(reverse('dashboard:iam-credential-report')).json()['report']
        self.assertEqual(report['columns'], ['user', 'arn', 'password_enabled'])
        self.assertEqual(report['rows'], [{'user': 'alice', 'arn': 'arn:a', 'password_enabled': 'true'}])

    def test_credential_report_not_generated_yet(self, factory):
        factory.return_value.client.return_value.get_credential_report.side_effect = client_error('ReportNotPresent')
        response = self.client.get(reverse('dashboard:iam-credential-report'))
        self.assertEqual(response.json(), {'report': None, 'reason': 'ReportNotPresent'})


@patch('dashboard.secretsmanager_api.FlociClientFactory')
class SecretReplicationTests(JsonClientMixin, SimpleTestCase):
    def url(self, secret='app/db'):
        return reverse('dashboard:secretsmanager-secret-replication', kwargs={'secret_id': secret})

    def setUpFactory(self, factory):
        factory.return_value.region = 'us-east-1'
        sm = factory.return_value.client.return_value
        sm.describe_secret.return_value = {'PrimaryRegion': 'us-east-1', 'ReplicationStatus': [
            {'Region': 'us-west-2', 'Status': 'InSync', 'StatusMessage': 'Replication succeeded.'}]}
        return sm

    def test_get_replication(self, factory):
        self.setUpFactory(factory)
        data = self.client.get(self.url()).json()
        self.assertEqual(data['primary_region'], 'us-east-1')
        self.assertEqual(data['replicas'][0]['region'], 'us-west-2')

    def test_replicate_to_regions(self, factory):
        sm = self.setUpFactory(factory)
        response = self.send('post', self.url(), {'regions': ['us-west-2', 'eu-west-1'], 'force_overwrite': True})
        self.assertEqual(response.status_code, 200)
        sm.replicate_secret_to_regions.assert_called_once_with(
            SecretId='app/db', AddReplicaRegions=[{'Region': 'us-west-2'}, {'Region': 'eu-west-1'}], ForceOverwriteReplicaSecret=True)

    def test_replicate_rejects_own_and_invalid_regions(self, factory):
        self.setUpFactory(factory)
        self.assertIn('own Region', self.send('post', self.url(), {'regions': ['us-east-1']}).json()['error'])
        self.assertIn('not a Region', self.send('post', self.url(), {'regions': ['mars']}).json()['error'])
        self.assertIn('at least one', self.send('post', self.url(), {'regions': []}).json()['error'])

    def test_remove_regions(self, factory):
        sm = self.setUpFactory(factory)
        self.send('delete', self.url(), {'regions': ['us-west-2']})
        sm.remove_regions_from_replication.assert_called_once_with(SecretId='app/db', RemoveReplicaRegions=['us-west-2'])

    def test_promote_calls_replica_region_with_its_arn(self, factory):
        sm = self.setUpFactory(factory)
        arn = 'arn:aws:secretsmanager:us-east-1:000000000000:secret:app-AbCdEf'
        response = self.send('post', reverse('dashboard:secretsmanager-secret-promote', kwargs={'secret_id': arn}), {'region': 'us-west-2'})
        self.assertEqual(response.json()['promoted_region'], 'us-west-2')
        self.assertEqual(factory.return_value.region, 'us-west-2')
        sm.stop_replication_to_replica.assert_called_once_with(
            SecretId='arn:aws:secretsmanager:us-west-2:000000000000:secret:app-AbCdEf')


@patch('dashboard.ec2_api.FlociClientFactory')
class EC2KeyPairTests(JsonClientMixin, SimpleTestCase):
    def ec2(self, factory):
        factory.return_value.config = MagicMock()
        return factory.return_value.client.return_value

    def test_list_key_pairs(self, factory):
        self.ec2(factory).describe_key_pairs.return_value = {'KeyPairs': [{'KeyName': 'web', 'KeyPairId': 'key-1', 'KeyFingerprint': 'aa'}]}
        data = self.client.get(reverse('dashboard:ec2-key-pairs')).json()
        self.assertEqual(data['key_pairs'][0]['key_name'], 'web')

    def test_create_key_pair_returns_material_and_returned_type(self, factory):
        ec2 = self.ec2(factory)
        ec2.create_key_pair.return_value = {'KeyPairId': 'key-1', 'KeyMaterial': '-----BEGIN RSA PRIVATE KEY-----\nx'}
        data = self.send('post', reverse('dashboard:ec2-key-pairs'), {'key_name': 'web', 'key_type': 'ed25519'}).json()
        ec2.create_key_pair.assert_called_once_with(KeyName='web', KeyType='ed25519', KeyFormat='pem')
        self.assertEqual((data['requested_key_type'], data['returned_key_type']), ('ed25519', 'rsa'))

    def test_create_key_pair_rejects_unknown_type(self, factory):
        response = self.send('post', reverse('dashboard:ec2-key-pairs'), {'key_name': 'web', 'key_type': 'dsa'})
        self.assertEqual(response.status_code, 400)

    def test_delete_key_pair(self, factory):
        ec2 = self.ec2(factory)
        self.send('delete', reverse('dashboard:ec2-key-pairs'), {'key_name': 'web'})
        ec2.delete_key_pair.assert_called_once_with(KeyName='web')
