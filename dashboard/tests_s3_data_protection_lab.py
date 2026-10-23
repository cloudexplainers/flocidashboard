"""S3 data protection and storage tiers lab: Object Lock, storage classes, lifecycle, replication."""

import importlib
import itertools
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.cache import cache
from django.test import SimpleTestCase

from .labs import lab_status, reset_lab, run_lab_step
from .labs.exams import LAB_EXAM_TAGS
from .labs.registry import labs_for_service

dp = importlib.import_module('dashboard.labs.s3_data_protection_labs')

SERVICE = 's3'
LAB = 'data-protection-tiers'


def client_error(code: str, message: str = 'refused') -> ClientError:
    return ClientError({'Error': {'Code': code, 'Message': message}, 'ResponseMetadata': {'HTTPStatusCode': 400}}, 'Operation')


class FakeS3:
    """Just enough S3 for the lab, with Object Lock enforcement that can be turned off."""

    def __init__(self, enforce_lock: bool = True):
        self.enforce_lock = enforce_lock
        self.buckets: dict[str, dict] = {}
        self.ids = itertools.count(1)
        self.clock = itertools.count(1)

    def _bucket(self, name):
        if name not in self.buckets:
            raise client_error('NoSuchBucket')
        return self.buckets[name]

    def create_bucket(self, Bucket, ObjectLockEnabledForBucket=False, CreateBucketConfiguration=None):
        if Bucket in self.buckets:
            raise client_error('BucketAlreadyOwnedByYou')
        self.buckets[Bucket] = {
            'versioning': 'Enabled' if ObjectLockEnabledForBucket else None,
            'lock': {'ObjectLockEnabled': 'Enabled'} if ObjectLockEnabledForBucket else None,
            'location': (CreateBucketConfiguration or {}).get('LocationConstraint'),
            'versions': [],  # dicts: Key, VersionId, Body, StorageClass, Mode, RetainUntil, Hold, seq
            'config': {},
        }
        return {'Location': f'/{Bucket}'}

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise client_error('404', 'Not Found')
        return {}

    def delete_bucket(self, Bucket):
        if self._bucket(Bucket)['versions']:
            raise client_error('BucketNotEmpty')
        del self.buckets[Bucket]

    def get_bucket_versioning(self, Bucket):
        status = self._bucket(Bucket)['versioning']
        return {'Status': status} if status else {}

    def put_bucket_versioning(self, Bucket, VersioningConfiguration):
        self._bucket(Bucket)['versioning'] = VersioningConfiguration['Status']
        return {}

    def get_bucket_location(self, Bucket):
        return {'LocationConstraint': self._bucket(Bucket)['location']}

    def put_object_lock_configuration(self, Bucket, ObjectLockConfiguration):
        self._bucket(Bucket)['lock'] = ObjectLockConfiguration
        return {}

    def get_object_lock_configuration(self, Bucket):
        lock = self._bucket(Bucket)['lock']
        if not lock:
            raise client_error('ObjectLockConfigurationNotFoundError')
        return {'ObjectLockConfiguration': lock}

    # Objects
    def put_object(self, Bucket, Key, Body, ContentType=None, StorageClass='STANDARD', ObjectLockMode=None,
                   ObjectLockRetainUntilDate=None):
        bucket = self._bucket(Bucket)
        mode, until = ObjectLockMode, ObjectLockRetainUntilDate
        default = ((bucket['lock'] or {}).get('Rule') or {}).get('DefaultRetention')
        if not mode and default:
            mode, until = default['Mode'], datetime.now(timezone.utc) + timedelta(days=default['Days'])
        version = {
            'Key': Key, 'VersionId': f'v{next(self.ids)}', 'Body': Body, 'StorageClass': StorageClass,
            'Mode': mode, 'RetainUntil': until, 'Hold': 'OFF', 'seq': next(self.clock),
        }
        if bucket['versioning'] != 'Enabled':
            bucket['versions'] = [item for item in bucket['versions'] if item['Key'] != Key]
        bucket['versions'].append(version)
        return {'VersionId': version['VersionId']}

    def _version(self, Bucket, Key, VersionId=None):
        matches = [item for item in self._bucket(Bucket)['versions'] if item['Key'] == Key]
        if VersionId:
            matches = [item for item in matches if item['VersionId'] == VersionId]
        if not matches:
            raise client_error('404', 'Not Found')
        return max(matches, key=lambda item: item['seq'])

    def head_object(self, Bucket, Key, VersionId=None):
        item = self._version(Bucket, Key, VersionId)
        head = {'VersionId': item['VersionId'], 'StorageClass': item['StorageClass']}
        if item['Mode']:
            head.update(ObjectLockMode=item['Mode'], ObjectLockRetainUntilDate=item['RetainUntil'])
        return head

    def get_object(self, Bucket, Key):
        item = self._version(Bucket, Key)

        class Body:
            def read(self_inner):
                return item['Body']
        return {'Body': Body()}

    def list_object_versions(self, Bucket, Prefix=''):
        items = [item for item in self._bucket(Bucket)['versions'] if item['Key'].startswith(Prefix)]
        return {'Versions': [
            {'Key': item['Key'], 'VersionId': item['VersionId'],
             'LastModified': datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=item['seq'])}
            for item in items
        ]}

    def get_paginator(self, name):
        assert name == 'list_object_versions'
        fake = self

        class Paginator:
            def paginate(self_inner, Bucket):
                return [fake.list_object_versions(Bucket=Bucket)]
        return Paginator()

    def list_objects_v2(self, Bucket):
        return {'KeyCount': len({item['Key'] for item in self._bucket(Bucket)['versions']})}

    def _check_delete(self, item, bypass):
        if not self.enforce_lock:
            return
        if item['Hold'] == 'ON':
            raise client_error('AccessDenied', 'Object has an active legal hold')
        if item['Mode'] and item['RetainUntil'] > datetime.now(timezone.utc):
            if item['Mode'] == 'COMPLIANCE' or not bypass:
                raise client_error('AccessDenied', f"Object is protected by {item['Mode']} retention")

    def delete_object(self, Bucket, Key, VersionId, BypassGovernanceRetention=False):
        item = self._version(Bucket, Key, VersionId)
        self._check_delete(item, BypassGovernanceRetention)
        self._bucket(Bucket)['versions'].remove(item)
        return {'VersionId': VersionId}

    def delete_objects(self, Bucket, Delete, BypassGovernanceRetention=False):
        errors = []
        for entry in Delete['Objects']:
            try:
                self.delete_object(Bucket, entry['Key'], entry['VersionId'], BypassGovernanceRetention)
            except ClientError as exc:
                errors.append({'Key': entry['Key'], 'Code': 'AccessDenied', 'Message': exc.response['Error']['Message']})
        return {'Errors': errors} if errors else {}

    def put_object_legal_hold(self, Bucket, Key, VersionId, LegalHold):
        self._version(Bucket, Key, VersionId)['Hold'] = LegalHold['Status']
        return {}

    def get_object_legal_hold(self, Bucket, Key, VersionId):
        return {'LegalHold': {'Status': self._version(Bucket, Key, VersionId)['Hold']}}

    def get_object_retention(self, Bucket, Key, VersionId):
        item = self._version(Bucket, Key, VersionId)
        if not item['Mode']:
            raise client_error('NoSuchObjectLockConfiguration')
        return {'Retention': {'Mode': item['Mode'], 'RetainUntilDate': item['RetainUntil']}}

    # Bucket configurations stored as given
    def _put(self, Bucket, name, value):
        self._bucket(Bucket)['config'][name] = value
        return {}

    def _get(self, Bucket, name, missing):
        value = self._bucket(Bucket)['config'].get(name)
        if value is None:
            raise client_error(missing)
        return value

    def put_bucket_intelligent_tiering_configuration(self, Bucket, Id, IntelligentTieringConfiguration):
        return self._put(Bucket, f'tiering:{Id}', IntelligentTieringConfiguration)

    def get_bucket_intelligent_tiering_configuration(self, Bucket, Id):
        return {'IntelligentTieringConfiguration': self._get(Bucket, f'tiering:{Id}', 'NoSuchConfiguration')}

    def put_bucket_lifecycle_configuration(self, Bucket, LifecycleConfiguration):
        return self._put(Bucket, 'lifecycle', LifecycleConfiguration)

    def get_bucket_lifecycle_configuration(self, Bucket):
        return self._get(Bucket, 'lifecycle', 'NoSuchLifecycleConfiguration')

    def put_bucket_replication(self, Bucket, ReplicationConfiguration):
        return self._put(Bucket, 'replication', ReplicationConfiguration)

    def get_bucket_replication(self, Bucket):
        return {'ReplicationConfiguration': self._get(Bucket, 'replication', 'ReplicationConfigurationNotFoundError')}

    def delete_bucket_replication(self, Bucket):
        self._bucket(Bucket)['config'].pop('replication', None)
        return {}

    def put_bucket_request_payment(self, Bucket, RequestPaymentConfiguration):
        return self._put(Bucket, 'payer', RequestPaymentConfiguration['Payer'])

    def get_bucket_request_payment(self, Bucket):
        return {'Payer': self._bucket(Bucket)['config'].get('payer', 'BucketOwner')}


class S3DataProtectionLabTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.fake = FakeS3()
        patcher = patch.object(dp, '_s3', side_effect=lambda region=None: self.fake)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleep = patch.object(dp.time, 'sleep')
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)
        self.addCleanup(cache.clear)

    def run_all(self):
        return {step['key']: run_lab_step(SERVICE, LAB, step['key']) for step in dp.LAB['steps']}

    def test_lab_is_registered_with_thirteen_steps_and_exam_tags(self):
        lab = next(item for item in labs_for_service(SERVICE) if item['key'] == LAB)
        self.assertEqual(len(lab['steps']), 13)
        self.assertEqual(lab['steps'][0]['key'], 'create-vault-bucket')
        tasks = {(tag['exam'], tag['task']) for tag in LAB_EXAM_TAGS[(SERVICE, LAB)]}
        self.assertIn(('SAA-C03', '4.1'), tasks)
        self.assertIn(('SAA-C03', '1.3'), tasks)

    def test_every_step_verifies_and_status_is_complete(self):
        results = self.run_all()
        failed = {key: result['verification']['message'] for key, result in results.items() if not result['verified']}
        self.assertEqual(failed, {})
        status = lab_status(SERVICE, LAB)
        self.assertTrue(status['complete'])
        self.assertEqual(status['passed_steps'], 13)

    def test_commands_show_the_real_version_id_and_retain_until_date(self):
        results = self.run_all()
        self.assertNotIn(dp.VERSION_PLACEHOLDER, results['delete-governed-version']['command'])
        self.assertNotIn(dp.RETAIN_UNTIL_PLACEHOLDER, results['upload-compliance-record']['command'])

    def test_rerunning_steps_reuses_locked_versions(self):
        self.run_all()
        results = self.run_all()
        self.assertTrue(all(result['verified'] for result in results.values()))
        versions = self.fake.list_object_versions(Bucket=dp.VAULT_BUCKET, Prefix=dp.RECORD_KEY)['Versions']
        self.assertEqual(len(versions), 1)
        self.assertTrue(lab_status(SERVICE, LAB)['complete'])

    def test_deletes_that_are_not_refused_fail_the_steps(self):
        self.fake.enforce_lock = False
        results = self.run_all()
        for key in ('delete-governed-version', 'place-legal-hold', 'upload-compliance-record'):
            self.assertFalse(results[key]['verified'], key)

    def test_record_uploaded_before_the_default_retention_is_not_locked(self):
        run_lab_step(SERVICE, LAB, 'create-vault-bucket')
        result = run_lab_step(SERVICE, LAB, 'upload-governed-record')
        self.assertFalse(result['verified'])
        self.assertIn('no GOVERNANCE retention', result['verification']['message'])

    def test_replication_needs_versioned_buckets(self):
        run_lab_step(SERVICE, LAB, 'create-archive-bucket')
        result = run_lab_step(SERVICE, LAB, 'configure-replication')
        self.assertFalse(result['verified'])
        self.assertIn(dp.DR_BUCKET, result['verification']['message'])

    def test_replica_bucket_must_be_in_the_other_region(self):
        self.fake.create_bucket(Bucket=dp.DR_BUCKET)
        result = run_lab_step(SERVICE, LAB, 'create-replica-bucket')
        self.assertFalse(result['verified'])
        self.assertIn('us-east-1', result['verification']['message'])

    def test_refused_deletes_need_their_marker_in_status(self):
        self.run_all()
        cache.clear()
        steps = lab_status(SERVICE, LAB)['steps']
        self.assertFalse(steps['delete-governed-version']['verified'])
        self.assertFalse(steps['upload-compliance-record']['verified'])
        self.assertTrue(steps['place-legal-hold']['verified'])

    def test_reset_waits_out_compliance_retention_and_removes_everything(self):
        self.run_all()
        real_now = datetime.now(timezone.utc)

        def fast_forward(seconds):
            for item in self.fake.buckets[dp.VAULT_BUCKET]['versions']:
                if item['Mode'] == 'COMPLIANCE':
                    item['RetainUntil'] = real_now - timedelta(seconds=1)
        self.sleep.side_effect = fast_forward
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['errors'], [])
        self.assertTrue(self.sleep.called)
        self.assertEqual(self.fake.buckets, {})
        self.assertEqual(lab_status(SERVICE, LAB)['status'], 'not_started')

    def test_reset_reports_a_long_compliance_retention(self):
        self.run_all()
        for item in self.fake.buckets[dp.VAULT_BUCKET]['versions']:
            if item['Mode'] == 'COMPLIANCE':
                item['RetainUntil'] = datetime.now(timezone.utc) + timedelta(days=1)
        result = reset_lab(SERVICE, LAB)
        self.assertEqual(result['verification']['status'], 'failed')
        self.assertIn('COMPLIANCE retention', result['verification']['message'])
        self.assertFalse(self.sleep.called)
        self.assertIn(dp.VAULT_BUCKET, self.fake.buckets)
        self.assertNotIn(dp.ARCHIVE_BUCKET, self.fake.buckets)

    def test_status_before_any_step_is_not_started(self):
        status = lab_status(SERVICE, LAB)
        self.assertEqual(status['status'], 'not_started')
        self.assertEqual(status['total_steps'], 13)
