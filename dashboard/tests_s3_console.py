import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .s3_api import list_s3_objects, s3_inventory_summary


class S3BucketsApiTests(SimpleTestCase):
    def test_create_bucket_rejects_invalid_name(self):
        response = self.client.post(
            reverse('dashboard:s3-buckets'),
            data=json.dumps({'name': 'AB'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('error', response.json())
        self.assertEqual(response.json()['service'], 's3')

    @patch('dashboard.s3_views.upload_s3_object')
    def test_json_upload_serializes_non_string_body(self, upload_mock):
        upload_mock.return_value = {'key': 'data.json'}

        response = self.client.post(
            reverse('dashboard:s3-bucket-objects', args=['demo-bucket']),
            data=json.dumps({'key': 'data.json', 'body': {'hello': 'world'}}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        upload_mock.assert_called_once_with(
            'demo-bucket', 'data.json', b'{"hello": "world"}', 'text/plain; charset=utf-8',
        )

    @patch('dashboard.s3_views.list_s3_buckets')
    def test_list_buckets(self, list_mock):
        list_mock.return_value = [{'name': 'a', 'region': 'us-east-1', 'created': None}]

        response = self.client.get(reverse('dashboard:s3-buckets'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()['buckets']), 1)

    @patch('dashboard.s3_views.create_s3_bucket')
    def test_create_bucket_success(self, create_mock):
        create_mock.return_value = {'name': 'my-test-bucket', 'region': 'us-east-1'}

        response = self.client.post(
            reverse('dashboard:s3-buckets'),
            data=json.dumps({'name': 'my-test-bucket'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['name'], 'my-test-bucket')
        create_mock.assert_called_once_with('my-test-bucket', None)

    @patch('dashboard.s3_views.delete_s3_bucket')
    def test_delete_bucket(self, delete_mock):
        delete_mock.return_value = {'deleted': True}

        response = self.client.delete(reverse('dashboard:s3-bucket-detail', kwargs={'bucket_name': 'my-bucket'}))

        self.assertEqual(response.status_code, 200)
        delete_mock.assert_called_once_with('my-bucket')

    @patch('dashboard.s3_views.empty_s3_bucket')
    def test_empty_bucket(self, empty_mock):
        empty_mock.return_value = {'deleted': 3}

        response = self.client.post(reverse('dashboard:s3-bucket-empty', kwargs={'bucket_name': 'my-bucket'}))

        self.assertEqual(response.status_code, 200)
        empty_mock.assert_called_once_with('my-bucket')


class S3ListObjectsTests(SimpleTestCase):
    @patch('dashboard.s3_api._s3_client')
    def test_list_objects_splits_folders_and_files(self, client_mock):
        s3 = MagicMock()
        client_mock.return_value = s3
        s3.list_objects_v2.return_value = {
            'CommonPrefixes': [{'Prefix': 'logs/'}],
            'Contents': [
                {'Key': 'logs/', 'Size': 0},
                {
                    'Key': 'logs/app.txt',
                    'Size': 10,
                    'ETag': '"x"',
                    'LastModified': '2024-01-01',
                    'StorageClass': 'STANDARD',
                },
            ],
            'IsTruncated': False,
            'KeyCount': 2,
        }

        result = list_s3_objects('my-bucket', prefix='', delimiter='/', max_keys=100)

        self.assertEqual(len(result['folders']), 1)
        self.assertEqual(len(result['objects']), 1)
        self.assertEqual(result['objects'][0]['key'], 'logs/app.txt')

    def test_folder_display_name(self):
        from .s3_api import _folder_display_name

        self.assertEqual(_folder_display_name('logs/2024/', ''), 'logs')

    @patch('dashboard.s3_api.list_s3_buckets')
    def test_inventory_summary_defaults_empty_object_counts_to_zero(self, list_mock):
        list_mock.return_value = []
        result = s3_inventory_summary()

        self.assertEqual(result['summary']['objects'], 0)
        self.assertEqual(result['summary']['total_bytes'], 0)

    @patch('dashboard.s3_api.get_s3_bucket')
    @patch('dashboard.s3_api.list_s3_buckets')
    def test_inventory_summary_counts_bucket_objects_and_bytes(self, list_mock, get_mock):
        list_mock.return_value = [{'name': 'my-bucket'}]
        get_mock.return_value = {
            'name': 'my-bucket',
            'versioning': {'Status': 'Enabled'},
            'object_count': 2,
            'total_bytes': 12,
            'objects': [
                {'key': 'folder/', 'size': 0},
                {'key': 'folder/file.txt', 'size': 12},
            ],
            'object_versions': {'versions': [], 'delete_markers': []},
        }

        result = s3_inventory_summary()

        self.assertEqual(result['summary']['objects'], 2)
        self.assertEqual(result['summary']['total_bytes'], 12)
        self.assertEqual(result['summary']['versioned_buckets'], 1)

    @patch('dashboard.s3_api.list_s3_buckets')
    def test_inventory_summary_marks_website_hosting_supported(self, list_mock):
        list_mock.return_value = []

        result = s3_inventory_summary()

        self.assertIn('Website hosting', result['supported']['bucket_configuration'])
        self.assertNotIn('Website hosting', result['supported']['not_implemented'])


class S3ObjectApiTests(SimpleTestCase):
    @patch('dashboard.s3_views.list_s3_objects')
    def test_list_objects_query_params(self, list_mock):
        list_mock.return_value = {'bucket': 'b', 'prefix': '', 'folders': [], 'objects': []}

        response = self.client.get(
            reverse('dashboard:s3-bucket-objects', kwargs={'bucket_name': 'my-bucket'}),
            {'prefix': 'a/', 'max_keys': '50'},
        )

        self.assertEqual(response.status_code, 200)
        list_mock.assert_called_once()
        _, kwargs = list_mock.call_args
        self.assertEqual(kwargs['prefix'], 'a/')
        self.assertEqual(kwargs['max_keys'], 50)

    @patch('dashboard.s3_views.put_s3_versioning')
    def test_put_versioning(self, put_mock):
        put_mock.return_value = {'Status': 'Enabled'}

        response = self.client.put(
            reverse('dashboard:s3-bucket-versioning', kwargs={'bucket_name': 'my-bucket'}),
            data=json.dumps({'status': 'Enabled'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        put_mock.assert_called_once_with('my-bucket', 'Enabled')

    @patch('dashboard.s3_views.download_s3_object')
    def test_download_object(self, download_mock):
        download_mock.return_value = (b'hello', {'content_type': 'text/plain', 'content_length': 5})

        response = self.client.get(
            reverse('dashboard:s3-object-download', kwargs={'bucket_name': 'my-bucket'}),
            {'key': 'folder/file.txt', 'version_id': 'v1'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'hello')
        self.assertEqual(response['Content-Type'], 'text/plain')
        self.assertEqual(response['Content-Length'], '5')
        self.assertEqual(response['Content-Disposition'], 'attachment; filename="file.txt"')
        download_mock.assert_called_once_with('my-bucket', 'folder/file.txt', 'v1')

    @patch('dashboard.s3_views.copy_s3_object')
    def test_copy_object(self, copy_mock):
        copy_mock.return_value = {'copied': True}

        response = self.client.post(
            reverse('dashboard:s3-object-copy', kwargs={'bucket_name': 'my-bucket'}),
            data=json.dumps({
                'source_key': 'source.txt',
                'dest_key': 'copies/source.txt',
                'dest_bucket': 'other-bucket',
                'source_version_id': 'v1',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        copy_mock.assert_called_once_with('my-bucket', 'source.txt', 'copies/source.txt', 'other-bucket', 'v1')

    @patch('dashboard.s3_views.create_s3_folder')
    def test_create_folder(self, create_mock):
        create_mock.return_value = {'key': 'folder/'}

        response = self.client.put(
            reverse('dashboard:s3-folder-create', kwargs={'bucket_name': 'my-bucket'}),
            data=json.dumps({'folder': 'folder'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        create_mock.assert_called_once_with('my-bucket', 'folder')

    @patch('dashboard.s3_views.put_s3_object_tags')
    def test_put_object_tags(self, put_mock):
        put_mock.return_value = [{'Key': 'env', 'Value': 'local'}]

        response = self.client.put(
            reverse('dashboard:s3-object-tags', kwargs={'bucket_name': 'my-bucket'}),
            data=json.dumps({'key': 'file.txt', 'tags': [{'Key': 'env', 'Value': 'local'}], 'version_id': 'v1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        put_mock.assert_called_once_with('my-bucket', 'file.txt', [{'Key': 'env', 'Value': 'local'}], 'v1')

    @patch('dashboard.s3_views.presign_s3_object')
    def test_presign_object(self, presign_mock):
        presign_mock.return_value = 'http://localhost:4566/my-bucket/file.txt?signature=sample'

        response = self.client.post(
            reverse('dashboard:s3-object-presign', kwargs={'bucket_name': 'my-bucket'}),
            data=json.dumps({'key': 'file.txt', 'version_id': 'v1', 'expires_in': 120}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['expires_in'], 120)
        presign_mock.assert_called_once_with('my-bucket', 'file.txt', 'v1', 120)

    @patch('dashboard.s3_views.put_s3_website')
    def test_put_bucket_website(self, put_mock):
        configuration = {
            'IndexDocument': {'Suffix': 'index.html'},
            'ErrorDocument': {'Key': 'error.html'},
        }
        put_mock.return_value = configuration

        response = self.client.put(
            reverse('dashboard:s3-bucket-website', kwargs={'bucket_name': 'my-bucket'}),
            data=json.dumps({'configuration': configuration}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['configuration'], configuration)
        put_mock.assert_called_once_with('my-bucket', configuration)

    @patch('dashboard.s3_views.delete_s3_website')
    def test_delete_bucket_website(self, delete_mock):
        delete_mock.return_value = {'deleted': True}

        response = self.client.delete(reverse('dashboard:s3-bucket-website', kwargs={'bucket_name': 'my-bucket'}))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        delete_mock.assert_called_once_with('my-bucket')
