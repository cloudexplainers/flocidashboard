import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class TextractPageTemplateTests(SimpleTestCase):

    def test_textract_registry_marks_service_interactive(self):
        service = get_service('textract')

        self.assertIsNotNone(service)
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'detect_document_text' for action in service.actions))
        self.assertTrue(any(action.name == 'analyze_document' for action in service.actions))
        self.assertTrue(any(action.name == 'start_document_text_detection' for action in service.actions))
        self.assertTrue(any(action.name == 'get_document_analysis' for action in service.actions))


class TextractActionsApiTests(SimpleTestCase):
    document = {'S3Object': {'Bucket': 'my-bucket', 'Name': 'test.pdf'}}

    @patch('dashboard.textract_views.detect_document_text')
    def test_detect_document_text_success(self, detect_mock):
        detect_mock.return_value = {'block_count': 3}

        response = self.client.post(
            reverse('dashboard:textract-document-text-detect'),
            data=json.dumps({'document': self.document}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        detect_mock.assert_called_once_with(self.document)

    def test_detect_document_text_rejects_invalid_json(self):
        response = self.client.post(
            reverse('dashboard:textract-document-text-detect'),
            data='not-json',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'textract')
        self.assertEqual(response.json()['operation'], 'detect_document_text')

    @patch('dashboard.textract_views.analyze_document')
    def test_analyze_document_success(self, analyze_mock):
        analyze_mock.return_value = {'block_count': 3}

        response = self.client.post(
            reverse('dashboard:textract-document-analyze'),
            data=json.dumps({'document': self.document, 'feature_types': ['TABLES', 'FORMS']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        analyze_mock.assert_called_once_with(self.document, ['TABLES', 'FORMS'])

    @patch('dashboard.textract_views.start_document_text_detection')
    def test_start_document_text_detection_success(self, start_mock):
        start_mock.return_value = {'job_id': 'job-1'}

        response = self.client.post(
            reverse('dashboard:textract-jobs-text-start'),
            data=json.dumps({'document_location': self.document}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        start_mock.assert_called_once_with(self.document)

    @patch('dashboard.textract_views.get_document_text_detection')
    def test_get_document_text_detection_success(self, get_mock):
        get_mock.return_value = {'job_id': 'job-1', 'job_status': 'SUCCEEDED'}

        response = self.client.post(
            reverse('dashboard:textract-jobs-text-get'),
            data=json.dumps({'job_id': 'job-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        get_mock.assert_called_once_with('job-1')

    @patch('dashboard.textract_views.start_document_analysis')
    def test_start_document_analysis_success(self, start_mock):
        start_mock.return_value = {'job_id': 'job-1'}

        response = self.client.post(
            reverse('dashboard:textract-jobs-analysis-start'),
            data=json.dumps({'document_location': self.document, 'feature_types': ['TABLES']}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        start_mock.assert_called_once_with(self.document, ['TABLES'])

    @patch('dashboard.textract_views.get_document_analysis')
    def test_get_document_analysis_success(self, get_mock):
        get_mock.return_value = {'job_id': 'job-1', 'job_status': 'SUCCEEDED'}

        response = self.client.post(
            reverse('dashboard:textract-jobs-analysis-get'),
            data=json.dumps({'job_id': 'job-1'}),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        get_mock.assert_called_once_with('job-1')
