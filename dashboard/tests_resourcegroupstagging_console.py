import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class ResourceGroupsTaggingPageTemplateTests(SimpleTestCase):

    def test_registry_marks_service_interactive(self):
        service = get_service('resourcegroupstagging')
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'tag_resources' for action in service.actions))
        self.assertTrue(any(action.name == 'untag_resources' for action in service.actions))
        self.assertTrue(any(action.name == 'get_resources' for action in service.actions))


class ResourceGroupsTaggingActionsApiTests(SimpleTestCase):
    arn = 'arn:aws:lambda:us-east-1:000000000000:function/my-func'

    @patch('dashboard.resourcegroupstagging_views.tag_resources')
    def test_tag_resources(self, mock):
        mock.return_value = {'failed_resources': {}}
        response = self.client.post(reverse('dashboard:resourcegroupstagging-tag'), data=json.dumps({'resource_arns': [self.arn], 'tags': {'Environment': 'dev'}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with([self.arn], {'Environment': 'dev'})

    def test_tag_resources_rejects_invalid_json(self):
        response = self.client.post(reverse('dashboard:resourcegroupstagging-tag'), data='bad', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['operation'], 'tag_resources')

    @patch('dashboard.resourcegroupstagging_views.untag_resources')
    def test_untag_resources(self, mock):
        mock.return_value = {'failed_resources': {}}
        response = self.client.post(reverse('dashboard:resourcegroupstagging-untag'), data=json.dumps({'resource_arns': [self.arn], 'tag_keys': ['Team']}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with([self.arn], ['Team'])

    @patch('dashboard.resourcegroupstagging_views.get_resources')
    def test_search_resources(self, mock):
        mock.return_value = {'resources': []}
        payload = {'resource_arns': [], 'tag_filters': [{'Key': 'Environment', 'Values': ['dev']}], 'resource_type_filters': ['lambda'], 'resources_per_page': 25, 'pagination_token': ''}
        response = self.client.post(reverse('dashboard:resourcegroupstagging-search'), data=json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with(resource_arns=[], tag_filters=payload['tag_filters'], resource_type_filters=['lambda'], resources_per_page=25, pagination_token='')

    @patch('dashboard.resourcegroupstagging_views.get_tag_values')
    def test_get_tag_values(self, mock):
        mock.return_value = {'key': 'Environment', 'values': ['dev']}
        response = self.client.post(reverse('dashboard:resourcegroupstagging-tag-values'), data=json.dumps({'key': 'Environment'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('Environment')
