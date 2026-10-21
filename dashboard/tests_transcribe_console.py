import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


class TranscribePageTemplateTests(SimpleTestCase):

    def test_transcribe_registry_marks_service_interactive(self):
        service = get_service('transcribe')
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'start_transcription_job' for action in service.actions))
        self.assertTrue(any(action.name == 'create_vocabulary' for action in service.actions))


class TranscribeActionsApiTests(SimpleTestCase):
    @patch('dashboard.transcribe_views.start_transcription_job')
    def test_start_job(self, mock):
        mock.return_value = {'job_name': 'floci-demo'}
        response = self.client.post(reverse('dashboard:transcribe-jobs-start'), data=json.dumps({'name': 'floci-demo', 'media_uri': 's3://bucket/audio.mp3', 'language_code': 'en-US', 'media_format': 'mp3', 'options': {}}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('floci-demo', media_uri='s3://bucket/audio.mp3', language_code='en-US', media_format='mp3', options={})

    def test_start_job_rejects_invalid_json(self):
        response = self.client.post(reverse('dashboard:transcribe-jobs-start'), data='bad', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['operation'], 'start_transcription_job')

    @patch('dashboard.transcribe_views.get_transcription_job')
    def test_get_job(self, mock):
        mock.return_value = {'job_name': 'floci-demo'}
        response = self.client.post(reverse('dashboard:transcribe-job-get'), data=json.dumps({'name': 'floci-demo'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('floci-demo')

    @patch('dashboard.transcribe_views.delete_transcription_job')
    def test_delete_job(self, mock):
        mock.return_value = {'job_name': 'floci-demo'}
        response = self.client.delete(reverse('dashboard:transcribe-job-detail', kwargs={'job_name': 'floci-demo'}))
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('floci-demo')

    @patch('dashboard.transcribe_views.create_vocabulary')
    def test_create_vocabulary(self, mock):
        mock.return_value = {'vocabulary_name': 'floci-vocab'}
        response = self.client.post(reverse('dashboard:transcribe-vocabularies'), data=json.dumps({'name': 'floci-vocab', 'language_code': 'en-US', 'phrases': ['Floci'], 'vocabulary_file_uri': ''}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('floci-vocab', language_code='en-US', phrases=['Floci'], vocabulary_file_uri='')

    @patch('dashboard.transcribe_views.get_vocabulary')
    def test_get_vocabulary(self, mock):
        mock.return_value = {'vocabulary_name': 'floci-vocab'}
        response = self.client.post(reverse('dashboard:transcribe-vocabulary-get'), data=json.dumps({'name': 'floci-vocab'}), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('floci-vocab')

    @patch('dashboard.transcribe_views.delete_vocabulary')
    def test_delete_vocabulary(self, mock):
        mock.return_value = {'vocabulary_name': 'floci-vocab'}
        response = self.client.delete(reverse('dashboard:transcribe-vocabulary-detail', kwargs={'vocabulary_name': 'floci-vocab'}))
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('floci-vocab')
