"""KMS key management lab: key policy, rotation, grant, envelope encryption, SSE-KMS bucket, key states."""

import importlib
import itertools
import json
import secrets
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

km = importlib.import_module('dashboard.labs.kms_key_management_labs')

SERVICE = 'kms'
LAB = 'key-management'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeAws:
    """Just enough KMS, IAM, and S3 for the lab. Like Floci, it stores key policies and grants without using
    them, enforces the encryption context and key states, and S3 never calls KMS."""

    def __init__(self):
        self.ids = itertools.count(1)
        self.roles, self.keys, self.aliases, self.buckets = {}, {}, {}, {}
        self.cancelled_state = 'Enabled'  # Floci; AWS leaves a cancelled key Disabled

    def _id(self):
        return f'{next(self.ids):08x}-0000-4000-8000-000000000000'

    # IAM
    def get_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        return {'Role': self.roles[RoleName]}

    def create_role(self, RoleName, **kwargs):
        self.roles[RoleName] = {'RoleName': RoleName, 'Arn': f'arn:aws:iam::000000000000:role/{RoleName}'}
        return {'Role': self.roles[RoleName]}

    def delete_role(self, RoleName):
        if RoleName not in self.roles:
            raise client_error('NoSuchEntity')
        del self.roles[RoleName]

    # KMS keys
    def _resolve(self, KeyId):
        key_id = self.aliases.get(KeyId, KeyId).rsplit('/', 1)[-1]
        if key_id not in self.keys:
            raise client_error('NotFoundException')
        return self.keys[key_id]

    def _metadata(self, key):
        return {name: key[name] for name in ('KeyId', 'Arn', 'KeyState', 'KeyManager', 'KeySpec', 'KeyUsage')}

    def create_key(self, Policy=None, Tags=None, **kwargs):
        key_id = self._id()
        self.keys[key_id] = {
            'KeyId': key_id, 'Arn': f'arn:aws:kms:us-east-1:000000000000:key/{key_id}', 'KeyState': 'Enabled',
            'KeyManager': 'CUSTOMER', 'KeySpec': 'SYMMETRIC_DEFAULT', 'KeyUsage': 'ENCRYPT_DECRYPT',
            'Policy': Policy or '{}', 'Tags': Tags or [], 'Rotation': False, 'Versions': 1, 'Grants': [],
        }
        return {'KeyMetadata': self._metadata(self.keys[key_id])}

    def describe_key(self, KeyId):
        return {'KeyMetadata': self._metadata(self._resolve(KeyId))}

    def list_keys(self):
        return {'Keys': [{'KeyId': key_id} for key_id in self.keys]}

    def list_resource_tags(self, KeyId):
        return {'Tags': self._resolve(KeyId)['Tags']}

    def get_key_policy(self, KeyId, PolicyName):
        return {'Policy': self._resolve(KeyId)['Policy']}

    def put_key_policy(self, KeyId, PolicyName, Policy):
        self._resolve(KeyId)['Policy'] = Policy  # no validation, like Floci

    def create_alias(self, AliasName, TargetKeyId):
        self.aliases[AliasName] = TargetKeyId

    def update_alias(self, AliasName, TargetKeyId):
        self.aliases[AliasName] = TargetKeyId

    def delete_alias(self, AliasName):
        if AliasName not in self.aliases:
            raise client_error('NotFoundException')
        del self.aliases[AliasName]

    def list_aliases(self, KeyId=None):
        return {'Aliases': [{'AliasName': name, 'TargetKeyId': target} for name, target in self.aliases.items()
                            if KeyId is None or target == KeyId]}

    # Rotation
    def enable_key_rotation(self, KeyId, RotationPeriodInDays=365):
        self._resolve(KeyId)['Rotation'] = True

    def disable_key_rotation(self, KeyId):
        self._resolve(KeyId)['Rotation'] = False

    def get_key_rotation_status(self, KeyId):
        return {'KeyRotationEnabled': self._resolve(KeyId)['Rotation']}  # Floci omits the period

    def rotate_key_on_demand(self, KeyId):
        self._resolve(KeyId)['Versions'] += 1
        return {'KeyId': KeyId}

    # Crypto: the "ciphertext" names the key, its version, and the context.
    def _usable(self, KeyId):
        key = self._resolve(KeyId)
        if key['KeyState'] == 'Disabled':
            raise client_error('DisabledException')
        if key['KeyState'] == 'PendingDeletion':
            raise client_error('KMSInvalidStateException')
        return key

    def encrypt(self, KeyId, Plaintext, EncryptionContext=None):
        key = self._usable(KeyId)
        blob = json.dumps({'k': key['KeyId'], 'v': key['Versions'], 'c': EncryptionContext or {}, 'p': Plaintext.hex()})
        return {'CiphertextBlob': blob.encode(), 'KeyId': key['Arn']}

    def decrypt(self, CiphertextBlob, EncryptionContext=None):
        blob = json.loads(CiphertextBlob)
        key = self._usable(blob['k'])
        if blob['c'] != (EncryptionContext or {}):
            raise client_error('InvalidCiphertextException')
        return {'Plaintext': bytes.fromhex(blob['p']), 'KeyId': key['Arn']}

    def generate_data_key(self, KeyId, KeySpec, EncryptionContext=None):
        data_key = secrets.token_bytes(32)
        return {**self.encrypt(KeyId, data_key, EncryptionContext), 'Plaintext': data_key}

    # Grants
    def create_grant(self, KeyId, **grant):
        key = self._resolve(KeyId)
        grant = {'KeyId': key['Arn'], 'GrantId': self._id(), **grant}
        key['Grants'].append(grant)
        return {'GrantId': grant['GrantId'], 'GrantToken': 'token-' + grant['GrantId']}

    def list_grants(self, KeyId):
        return {'Grants': list(self._resolve(KeyId)['Grants'])}

    def revoke_grant(self, KeyId, GrantId):
        key = self._resolve(KeyId)
        key['Grants'] = [grant for grant in key['Grants'] if grant['GrantId'] != GrantId]

    # Key states
    def disable_key(self, KeyId):
        self._resolve(KeyId)['KeyState'] = 'Disabled'

    def enable_key(self, KeyId):
        self._resolve(KeyId)['KeyState'] = 'Enabled'

    def schedule_key_deletion(self, KeyId, PendingWindowInDays):
        key = self._resolve(KeyId)
        if key['KeyState'] == 'PendingDeletion':
            raise client_error('KMSInvalidStateException')
        key['KeyState'] = 'PendingDeletion'
        return {'KeyId': key['Arn'], 'DeletionDate': '2026-11-22T00:00:00Z'}

    def cancel_key_deletion(self, KeyId):
        self._resolve(KeyId)['KeyState'] = self.cancelled_state

    # S3
    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('404')

    def create_bucket(self, Bucket):
        self.buckets[Bucket] = {'encryption': None, 'objects': {}}

    def put_bucket_encryption(self, Bucket, ServerSideEncryptionConfiguration):
        self.buckets[Bucket]['encryption'] = ServerSideEncryptionConfiguration

    def get_bucket_encryption(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('NoSuchBucket')
        return {'ServerSideEncryptionConfiguration': self.buckets[Bucket]['encryption']
                or {'Rules': [{'ApplyServerSideEncryptionByDefault': {'SSEAlgorithm': 'AES256'}}]}}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.buckets[Bucket]['objects'][Key] = Body  # stored without SSE headers, like Floci

    def head_object(self, Bucket, Key):
        if Key not in self.buckets.get(Bucket, {}).get('objects', {}):
            raise client_error('404')
        return {'ContentLength': len(self.buckets[Bucket]['objects'][Key])}

    def list_objects_v2(self, Bucket):
        return {'Contents': [{'Key': key} for key in self.buckets[Bucket]['objects']]}

    def delete_object(self, Bucket, Key):
        self.buckets[Bucket]['objects'].pop(Key, None)

    def delete_bucket(self, Bucket):
        del self.buckets[Bucket]


class KmsKeyManagementLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeAws()
        patcher = patch('dashboard.labs.kms_key_management_labs._client', return_value=self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step: run_lab_step(SERVICE, LAB, step) for step in km.STEP_KEYS}

    def run_until(self, last):
        for step in km.STEP_KEYS:
            run_lab_step(SERVICE, LAB, step)
            if step == last:
                return

    def key(self):
        return self.fake.keys[self.fake.aliases[km.ALIAS]]

    def test_lab_is_registered_with_eight_steps_and_exam_tags(self):
        lab = next(item for item in labs_for_service(SERVICE) if item['key'] == LAB)
        self.assertEqual(len(lab['steps']), 8)
        self.assertEqual(lab['steps'][0]['key'], 'create-roles')
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '1.3'), tasks)

    def test_every_step_says_how_floci_emulates_it(self):
        for step in km.LAB['steps']:
            self.assertIn('How Floci emulates this', step['explanation'], step['key'])

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 8)

    def test_the_finished_key_has_the_designed_shape(self):
        self.run_all()
        key = self.key()
        self.assertEqual(json.loads(key['Policy']), km.KEY_POLICY)
        self.assertTrue(key['Rotation'])
        self.assertEqual(key['Versions'], 2)
        self.assertEqual(key['KeyState'], 'Enabled')
        [grant] = key['Grants']
        self.assertEqual(grant['Constraints'], {'EncryptionContextSubset': km.CONTEXT})
        rule = self.fake.buckets[km.BUCKET]['encryption']['Rules'][0]
        self.assertEqual(rule['ApplyServerSideEncryptionByDefault']['KMSMasterKeyID'], key['Arn'])
        self.assertTrue(rule['BucketKeyEnabled'])

    def test_floci_gaps_are_named_in_results(self):
        results = self.run_all()
        self.assertIn('does not report the rotation period', results['rotate-key']['verification']['message'])
        self.assertIn('did not report the object as SSE-KMS', results['sse-kms-bucket']['verification']['message'])
        self.assertIn('AWS leaves it Disabled', results['disable-and-schedule-deletion']['verification']['message'])
        self.assertEqual(results['envelope-encrypt']['json']['PlaintextDataKey'], 'discarded after encrypting')

    def test_envelope_round_trip_and_wrong_context(self):
        self.run_until('envelope-decrypt')
        result = run_lab_step(SERVICE, LAB, 'envelope-decrypt')
        self.assertEqual(result['json']['DecryptedRecord'], km.RECORD)
        self.assertEqual(result['json']['WrongContext']['Result'], 'InvalidCiphertextException')
        envelope = cache.get(km.CACHE_PREFIX + 'envelope-encrypt')
        self.assertNotIn(km.RECORD_TEXT, json.dumps(envelope))

    def test_tampered_record_does_not_open(self):
        data_key = secrets.token_bytes(32)
        sealed = km._seal(data_key, b'{"total":"129.90"}')
        self.assertEqual(km._open(data_key, sealed), b'{"total":"129.90"}')
        flipped = bytearray(km.base64.b64decode(sealed['Ciphertext']))
        flipped[0] ^= 1
        self.assertIsNone(km._open(data_key, {**sealed, 'Ciphertext': km._b64(bytes(flipped))}))
        self.assertIsNone(km._open(secrets.token_bytes(32), sealed))

    def test_context_not_enforced_fails_the_decrypt_step(self):
        self.run_until('envelope-encrypt')
        decrypt = self.fake.decrypt

        def lenient(CiphertextBlob, EncryptionContext=None):
            return decrypt(CiphertextBlob, json.loads(CiphertextBlob)['c'])
        with patch.object(self.fake, 'decrypt', side_effect=lenient):
            result = run_lab_step(SERVICE, LAB, 'envelope-decrypt')
        self.assertFalse(result['verified'])
        self.assertIn('InvalidCiphertextException', result['verification']['message'])

    def test_rerunning_steps_reuses_resources(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        self.assertEqual(len(self.fake.keys), 1)
        self.assertEqual(len(self.key()['Grants']), 1)

    def test_steps_out_of_order_explain_what_is_missing(self):
        result = run_lab_step(SERVICE, LAB, 'create-key-with-policy')
        self.assertFalse(result['verified'])
        self.assertIn('roles', result['verification']['message'])
        result = run_lab_step(SERVICE, LAB, 'rotate-key')
        self.assertIn('Create a customer managed key', result['verification']['message'])
        result = run_lab_step(SERVICE, LAB, 'envelope-decrypt')
        self.assertIn('Encrypt a record', result['verification']['message'])

    def test_policy_without_root_statement_fails_and_the_step_repairs_it(self):
        self.run_until('create-key-with-policy')
        policy = {**km.KEY_POLICY, 'Statement': km.KEY_POLICY['Statement'][1:]}
        self.key()['Policy'] = json.dumps(policy)
        ok, message = km._check_key()
        self.assertFalse(ok)
        self.assertIn('lockout safety check', message)
        self.assertTrue(run_lab_step(SERVICE, LAB, 'create-key-with-policy')['verified'])
        self.assertEqual(json.loads(self.key()['Policy']), km.KEY_POLICY)

    def test_administrator_with_decrypt_fails(self):
        self.run_until('create-key-with-policy')
        admin = {**km.KEY_POLICY['Statement'][1], 'Action': [*km.ADMIN_ACTIONS, 'kms:Decrypt']}
        policy = {**km.KEY_POLICY, 'Statement': [km.KEY_POLICY['Statement'][0], admin, km.KEY_POLICY['Statement'][2]]}
        self.key()['Policy'] = json.dumps(policy)
        ok, message = km._check_key()
        self.assertFalse(ok)
        self.assertIn('Separate duties', message)

    def test_user_with_admin_rights_and_malformed_policy_fail(self):
        self.run_until('create-key-with-policy')
        user = {**km.KEY_POLICY['Statement'][2], 'Action': 'kms:*'}
        self.key()['Policy'] = json.dumps({**km.KEY_POLICY, 'Statement': [*km.KEY_POLICY['Statement'][:2], user]})
        self.assertIn('administer the key', km._check_key()[1])
        self.key()['Policy'] = '{not json'
        self.assertIn('not valid JSON', km._check_key()[1])

    def test_rotation_turned_off_fails(self):
        self.run_until('rotate-key')
        self.key()['Rotation'] = False
        self.assertFalse(km._check_rotation()[0])
        self.assertFalse(lab_status(SERVICE, LAB)['steps']['rotate-key']['verified'])

    def test_unconstrained_grant_fails_and_the_step_replaces_it(self):
        self.run_until('create-grant')
        self.key()['Grants'][0].pop('Constraints')
        self.assertIn('encryption context', km._check_grant()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'create-grant')['verified'])
        self.assertEqual(len(self.key()['Grants']), 1)

    def test_bucket_back_on_sse_s3_fails(self):
        self.run_until('sse-kms-bucket')
        self.fake.buckets[km.BUCKET]['encryption'] = None
        self.assertIn('does not default to SSE-KMS', km._check_bucket()[1])

    def test_aws_cancel_behavior_also_passes(self):
        self.fake.cancelled_state = 'Disabled'
        self.run_until('disable-and-schedule-deletion')
        result = run_lab_step(SERVICE, LAB, 'disable-and-schedule-deletion')
        self.assertTrue(result['verified'])
        self.assertEqual(result['json']['StateAfterCancel'], 'Disabled')
        self.assertNotIn('Floci put', result['verification']['message'])
        self.assertEqual(self.key()['KeyState'], 'Enabled')

    def test_key_states_not_enforced_fail_the_last_step(self):
        self.run_until('sse-kms-bucket')
        with patch.object(self.fake, '_usable', side_effect=self.fake._resolve):
            result = run_lab_step(SERVICE, LAB, 'disable-and-schedule-deletion')
        self.assertFalse(result['verified'])

    def test_key_left_pending_deletion_is_recovered_by_the_key_step(self):
        self.run_until('create-key-with-policy')
        self.key()['KeyState'] = 'PendingDeletion'
        self.assertIn('pending deletion', km._check_key()[1])
        self.assertTrue(run_lab_step(SERVICE, LAB, 'create-key-with-policy')['verified'])
        self.assertEqual(len(self.fake.keys), 1)

    def test_reset_removes_everything_and_clears_progress(self):
        self.run_all()
        key_id = self.fake.aliases[km.ALIAS]
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual((self.fake.roles, self.fake.aliases, self.fake.buckets), ({}, {}, {}))
        self.assertEqual(self.fake.keys[key_id]['KeyState'], 'PendingDeletion')
        self.assertEqual(self.fake.keys[key_id]['Grants'], [])
        self.assertEqual(lab_status(SERVICE, LAB)['status'], 'not_started')
        results = self.run_all()
        self.assertTrue(all(item['verified'] for item in results.values()))
        self.assertEqual(len(self.fake.keys), 2)

    def test_reset_with_nothing_to_delete_is_clean(self):
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['deleted_resources'], [])

    def test_status_before_any_step_is_not_started(self):
        status = lab_status(SERVICE, LAB)
        self.assertEqual(status['status'], 'not_started')
        self.assertEqual(status['total_steps'], 8)
