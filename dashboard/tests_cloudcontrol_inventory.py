from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .aws.cloudcontrol import _cloudcontrol_resource_items
from .services import get_service


class FakePaginator:
    def __init__(self, result_key, items):
        self.result_key = result_key
        self.items = items

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return self

    def build_full_result(self):
        return {self.result_key: self.items}


class FakeCloudControlClient:
    def __init__(self, items):
        self.paginator = FakePaginator('ResourceDescriptions', items)

    def get_paginator(self, operation_name):
        self.operation_name = operation_name
        return self.paginator


class CloudControlInventoryTests(SimpleTestCase):

    def test_cloudcontrol_registry_marks_service_read_only(self):
        service = get_service('cloudcontrol')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'read_only_inspector')
        self.assertEqual(service.category, 'Management')


    @patch('dashboard.views.cloudcontrol_inventory')
    def test_cloudcontrol_api_returns_inventory(self, inventory):
        inventory.return_value = {
            'summary': {'type_presets': 19, 'resources': 1},
            'resources': [{'name': 'bucket-a', 'type_name': 'AWS::S3::Bucket'}],
            'type_results': [{'name': 'AWS::S3::Bucket', 'count': 1}],
            'supported_from_sdk': ['ListResources'],
        }

        response = self.client.get(reverse('dashboard:cloudcontrol'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['summary']['resources'], 1)

    def test_cloudcontrol_resource_items_parse_properties_json(self):
        client = FakeCloudControlClient([
            {
                'Identifier': 'bucket-a',
                'Properties': '{"BucketName": "bucket-a", "Tags": [{"Key": "env", "Value": "local"}]}',
            },
        ])

        items, error = _cloudcontrol_resource_items(client, 'AWS::S3::Bucket')

        self.assertIsNone(error)
        self.assertEqual(items[0]['identifier'], 'bucket-a')
        self.assertEqual(items[0]['type_name'], 'AWS::S3::Bucket')
        self.assertEqual(items[0]['properties']['BucketName'], 'bucket-a')
        self.assertEqual(client.operation_name, 'list_resources')
        self.assertEqual(client.paginator.kwargs['TypeName'], 'AWS::S3::Bucket')
