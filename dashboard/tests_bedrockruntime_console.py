import io
import json
from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse

from .bedrockruntime_api import invoke_model
from .services import get_service


class BedrockRuntimePageTemplateTests(SimpleTestCase):

    def test_registry_marks_service_interactive(self):
        service = get_service('bedrockruntime')
        self.assertEqual(service.maturity, 'interactive_workbench')
        self.assertTrue(any(action.name == 'converse' for action in service.actions))
        self.assertTrue(any(action.name == 'invoke_model' for action in service.actions))


class BedrockRuntimeActionsApiTests(SimpleTestCase):
    @patch('dashboard.bedrockruntime_views.converse')
    def test_converse(self, mock):
        mock.return_value = {'model_id': 'anthropic.test'}
        messages = [{'role': 'user', 'content': [{'text': 'hi'}]}]
        response = self.client.post(
            reverse('dashboard:bedrockruntime-converse'),
            data=json.dumps({'model_id': 'anthropic.test', 'messages': messages, 'system': [], 'inference_config': {'maxTokens': 100}, 'tool_config': {}}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('anthropic.test', messages, system=[], inference_config={'maxTokens': 100}, tool_config={})

    def test_converse_rejects_invalid_json(self):
        response = self.client.post(reverse('dashboard:bedrockruntime-converse'), data='bad', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'bedrockruntime')
        self.assertEqual(response.json()['operation'], 'converse')

    @patch('dashboard.bedrockruntime_views.invoke_model')
    def test_invoke_model(self, mock):
        mock.return_value = {'model_id': 'anthropic.test', 'body': {'content': []}}
        body = {'max_tokens': 100}
        response = self.client.post(
            reverse('dashboard:bedrockruntime-invoke'),
            data=json.dumps({'model_id': 'anthropic.test', 'body': body, 'content_type': 'application/json', 'accept': 'application/json'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        mock.assert_called_once_with('anthropic.test', body, content_type='application/json', accept='application/json')

    @patch('dashboard.bedrockruntime_api._client')
    def test_invoke_model_decodes_streaming_response_body(self, client_mock):
        client_mock.return_value.invoke_model.return_value = {
            'body': io.BytesIO(b'{"outputs":[{"text":"Floci"}]}'),
            'contentType': 'application/json',
            'ResponseMetadata': {'HTTPStatusCode': 200},
        }
        result = invoke_model('generic.model', {'prompt': 'hi'})
        self.assertEqual(result['body'], {'outputs': [{'text': 'Floci'}]})
        self.assertEqual(result['response_metadata']['HTTPStatusCode'], 200)
