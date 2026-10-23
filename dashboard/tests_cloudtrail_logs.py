import gzip
import io
import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase

from . import cloudtrail_logs

TRAIL = {'Name': 'audit', 'TrailARN': 'arn:aws:cloudtrail:us-east-1:000000000000:trail/audit', 'S3BucketName': 'logs', 'S3KeyPrefix': 'team'}


def log_key(region: str, day: str, stamp: str, prefix: str = 'team/') -> str:
    return f'{prefix}AWSLogs/000000000000/CloudTrail/{region}/{day}/000000000000_CloudTrail_{region}_{stamp}_abc.json.gz'


def gz(document) -> bytes:
    return gzip.compress(json.dumps(document).encode())


class FakeS3:
    """Enough of ListObjectsV2 (with Delimiter and continuation) and GetObject for the viewer."""

    def __init__(self, objects: dict[str, bytes], page_size: int = 1000):
        self.objects = objects
        self.page_size = page_size
        self.get_calls: list[str] = []

    def _modified(self, key: str) -> datetime:
        parts = key.rsplit('_', 2)
        if len(parts) < 3:
            return datetime(2026, 1, 1, tzinfo=timezone.utc)
        stamp = parts[-2]
        return datetime.strptime(stamp, '%Y%m%dT%H%MZ').replace(tzinfo=timezone.utc)

    def list_objects_v2(self, Bucket, Prefix='', Delimiter=None, ContinuationToken=None):
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        if Delimiter:
            folders = sorted({Prefix + key[len(Prefix):].split(Delimiter, 1)[0] + Delimiter for key in keys if Delimiter in key[len(Prefix):]})
            return {'CommonPrefixes': [{'Prefix': folder} for folder in folders], 'IsTruncated': False}
        start = int(ContinuationToken or 0)
        page = keys[start:start + self.page_size]
        more = start + self.page_size < len(keys)
        response = {'Contents': [{'Key': key, 'Size': len(self.objects[key]), 'LastModified': self._modified(key)} for key in page], 'IsTruncated': more}
        if more:
            response['NextContinuationToken'] = str(start + self.page_size)
        return response

    def get_paginator(self, name):
        assert name == 'list_objects_v2'
        paginator = MagicMock()
        paginator.paginate.side_effect = lambda **kwargs: [self.list_objects_v2(**kwargs)]
        return paginator

    def get_object(self, Bucket, Key):
        self.get_calls.append(Key)
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': 'NoSuchKey', 'Message': 'The specified key does not exist.'}}, 'GetObject')
        data = self.objects[Key]
        return {'Body': io.BytesIO(data), 'ContentLength': len(data)}


class CloudTrailLogFileTests(SimpleTestCase):
    def setUp(self):
        cloudtrail = patch('dashboard.cloudtrail_logs._cloudtrail')
        self.cloudtrail = cloudtrail.start().return_value
        self.addCleanup(cloudtrail.stop)
        self.cloudtrail.describe_trails.return_value = {'trailList': [TRAIL]}
        s3 = patch('dashboard.cloudtrail_logs._s3')
        self.s3_factory = s3.start()
        self.addCleanup(s3.stop)

    def use(self, objects, **kwargs) -> FakeS3:
        fake = FakeS3(objects, **kwargs)
        self.s3_factory.return_value = fake
        return fake

    def test_lists_files_newest_first_across_regions_and_ignores_other_objects(self):
        self.use({
            log_key('us-east-1', '2026/10/21', '20261021T0900Z'): b'x',
            log_key('us-east-1', '2026/10/22', '20261022T1000Z'): b'xx',
            log_key('eu-west-1', '2026/10/22', '20261022T1100Z'): b'xxx',
            'team/AWSLogs/000000000000/CloudTrail-Digest/us-east-1/2026/10/22/digest.json.gz': b'd',
            'team/AWSLogs/000000000000/CloudTrail/us-east-1/2026/10/22/notes.txt': b'n',
            'other/file.json.gz': b'o',
        })
        response = self.client.get('/api/cloudtrail/trails/audit/log-files/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body['bucket'], body['prefix'], body['log_prefix']), ('logs', 'team/', 'team/AWSLogs/'))
        self.assertEqual([(item['region'], item['size']) for item in body['files']], [('eu-west-1', 3), ('us-east-1', 2), ('us-east-1', 1)])
        self.assertEqual(body['files'][0]['account'], '000000000000')
        self.assertEqual(body['files'][0]['last_modified'], '2026-10-22T11:00:00+00:00')
        self.assertIsNone(body['next_cursor'])
        self.assertNotIn('load_errors', body)
        self.cloudtrail.describe_trails.assert_called_once_with(trailNameList=['audit'], includeShadowTrails=False)

    def test_pages_by_whole_days_and_continues_before_the_cursor(self):
        objects = {}
        for day in ('20', '21', '22'):
            for minute in range(60):
                objects[log_key('us-east-1', f'2026/10/{day}', f'202610{day}T10{minute:02d}Z')] = b'x'
        self.use(objects)
        with patch.object(cloudtrail_logs, 'PAGE_SIZE', 100):
            first = cloudtrail_logs.list_log_files('audit')
            self.assertEqual(len(first['files']), 120)
            self.assertEqual(first['next_cursor'], '2026/10/21')
            second = cloudtrail_logs.list_log_files('audit', before=first['next_cursor'])
        self.assertEqual(len(second['files']), 60)
        self.assertTrue(all('/2026/10/20/' in item['key'] for item in second['files']))
        self.assertIsNone(second['next_cursor'])

    def test_large_days_are_truncated_and_reported(self):
        self.use({log_key('us-east-1', '2026/10/22', f'20261022T{hour:02d}{minute:02d}Z'): b'x' for hour in range(10) for minute in range(60)}, page_size=100)
        with patch.object(cloudtrail_logs, 'MAX_FILES_PER_DAY', 250):
            response = self.client.get('/api/cloudtrail/trails/audit/log-files/')
        body = response.json()
        self.assertEqual(len(body['files']), 250)
        self.assertEqual(body['load_errors'][0]['code'], 'Truncated')
        self.assertEqual(body['load_errors'][0]['operation'], 'ListObjectsV2')

    def test_trail_without_prefix_uses_bucket_root(self):
        self.cloudtrail.describe_trails.return_value = {'trailList': [{**TRAIL, 'S3KeyPrefix': None}]}
        self.use({log_key('us-east-1', '2026/10/22', '20261022T1000Z', prefix=''): b'x'})
        body = cloudtrail_logs.list_log_files('audit')
        self.assertEqual(body['log_prefix'], 'AWSLogs/')
        self.assertEqual(len(body['files']), 1)

    def test_missing_trail_bucket_and_access_errors_are_reported_not_empty(self):
        self.cloudtrail.describe_trails.return_value = {'trailList': []}
        response = self.client.get('/api/cloudtrail/trails/missing/log-files/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()['code'], 'TrailNotFoundException')
        self.cloudtrail.describe_trails.return_value = {'trailList': [TRAIL]}
        for code, status in (('NoSuchBucket', 404), ('AccessDenied', 403)):
            with self.subTest(code=code):
                failing = MagicMock()
                failing.get_paginator.return_value.paginate.side_effect = ClientError({'Error': {'Code': code, 'Message': f'{code} from S3'}}, 'ListObjectsV2')
                self.s3_factory.return_value = failing
                response = self.client.get('/api/cloudtrail/trails/audit/log-files/')
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['error'], f'{code} from S3')
                self.assertNotIn('files', response.json())

    def test_invalid_cursor_is_rejected_before_any_call(self):
        response = self.client.get('/api/cloudtrail/trails/audit/log-files/', {'before': '../x'})
        self.assertEqual(response.status_code, 400)
        self.cloudtrail.describe_trails.assert_not_called()

    def test_reads_records_from_a_gzipped_file(self):
        key = log_key('us-east-1', '2026/10/22', '20261022T1000Z')
        records = [{'eventName': 'PutObject', 'errorCode': None}, {'eventName': 'GetObject', 'errorCode': 'NoSuchKey'}]
        self.use({key: gz({'Records': records})})
        response = self.client.get('/api/cloudtrail/trails/audit/log-files/read/', {'key': key})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['records'], records)
        self.assertEqual(response.json()['key'], key)

    def test_read_only_accepts_log_files_of_this_trail(self):
        fake = self.use({'team/secret.json.gz': gz({'Records': []}), 'elsewhere/AWSLogs/1/CloudTrail/r/2026/10/22/f.json.gz': gz({'Records': []})})
        for key in ('team/secret.json.gz', 'elsewhere/AWSLogs/1/CloudTrail/r/2026/10/22/f.json.gz', log_key('us-east-1', '2026/10/22', '20261022T1000Z').replace('.json.gz', '.json'), ''):
            with self.subTest(key=key):
                response = self.client.get('/api/cloudtrail/trails/audit/log-files/read/', {'key': key})
                self.assertEqual(response.status_code, 400)
        self.assertEqual(fake.get_calls, [])

    def test_read_errors_are_reported(self):
        key = log_key('us-east-1', '2026/10/22', '20261022T1000Z')
        cases = {
            'not gzip': (b'{"Records": []}', 'not gzip-compressed'),
            'bad json': (gzip.compress(b'{oops'), 'not valid JSON'),
            'no records': (gz({'Other': []}), 'no Records list'),
            'corrupt': (b'\x1f\x8b' + b'\x00' * 20, 'could not be decompressed'),
        }
        for label, (data, message) in cases.items():
            with self.subTest(label):
                self.use({key: data})
                response = self.client.get('/api/cloudtrail/trails/audit/log-files/read/', {'key': key})
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.json()['error'])
        self.use({})
        response = self.client.get('/api/cloudtrail/trails/audit/log-files/read/', {'key': key})
        self.assertEqual(response.status_code, 404)

    def test_size_caps_apply_before_and_after_decompression(self):
        key = log_key('us-east-1', '2026/10/22', '20261022T1000Z')
        self.use({key: gz({'Records': [{'pad': 'x' * 5000}]})})
        with patch.object(cloudtrail_logs, 'MAX_COMPRESSED_BYTES', 10):
            response = self.client.get('/api/cloudtrail/trails/audit/log-files/read/', {'key': key})
        self.assertEqual(response.status_code, 400)
        self.assertIn('viewer reads files up to 10 bytes', response.json()['error'])
        with patch.object(cloudtrail_logs, 'MAX_DECOMPRESSED_BYTES', 1000):
            response = self.client.get('/api/cloudtrail/trails/audit/log-files/read/', {'key': key})
        self.assertEqual(response.status_code, 400)
        self.assertIn('expands to more than 1000 bytes', response.json()['error'])
