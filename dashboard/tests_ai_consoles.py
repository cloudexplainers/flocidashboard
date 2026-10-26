"""Consoles for SageMaker AI, Bedrock guardrails, Comprehend, Rekognition, and Translate."""

import json
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.urls import reverse

from .services import get_service


def _post(client, url, body, method='post'):
    return getattr(client, method)(url, data=json.dumps(body), content_type='application/json')


class AiServiceRegistryTests(SimpleTestCase):
    def test_ai_services_are_interactive_workbenches(self):
        expected = {
            'sagemaker': {'create_training_job', 'create_model', 'create_endpoint_config', 'create_endpoint', 'update_endpoint', 'invoke_endpoint'},
            'bedrock': {'create_guardrail', 'create_guardrail_version', 'delete_guardrail'},
            'comprehend': {'detect_sentiment', 'detect_pii_entities'},
            'rekognition': {'detect_labels', 'compare_faces'},
            'translate': {'translate_text'},
        }
        for key, actions in expected.items():
            with self.subTest(service=key):
                service = get_service(key)
                self.assertIsNotNone(service)
                self.assertEqual(service.category, 'AI')
                self.assertEqual(service.maturity, 'interactive_workbench')
                self.assertLessEqual(actions, {action.name for action in service.actions})


class SageMakerConsoleTests(SimpleTestCase):
    @patch('dashboard.sagemaker_api._client')
    def test_training_job_sends_entrypoint_channel_and_string_hyperparameters(self, client):
        client.return_value.create_training_job.return_value = {'TrainingJobArn': 'arn'}
        response = _post(self.client, reverse('dashboard:sagemaker-training-jobs'), {
            'name': 'train-1', 'role_arn': 'arn:aws:iam::000000000000:role/r', 'image': 'python:3.12-alpine',
            'entrypoint': ['python', '-c', 'print(1)'], 'input_s3_uri': 's3://b/train/', 'output_s3_path': 's3://b/out/',
            'hyperparameters': {'epochs': 3},
        })
        self.assertEqual(response.status_code, 200)
        kwargs = client.return_value.create_training_job.call_args.kwargs
        self.assertEqual(kwargs['AlgorithmSpecification']['ContainerEntrypoint'], ['python', '-c', 'print(1)'])
        self.assertEqual(kwargs['InputDataConfig'][0]['ChannelName'], 'train')
        self.assertEqual(kwargs['HyperParameters'], {'epochs': '3'})

    @patch('dashboard.sagemaker_api._client')
    def test_model_endpoint_and_update(self, client):
        client.return_value.create_model.return_value = {'ModelArn': 'arn'}
        client.return_value.update_endpoint.return_value = {'EndpointArn': 'arn'}
        self.assertEqual(_post(self.client, reverse('dashboard:sagemaker-models'), {
            'name': 'm', 'role_arn': 'arn:aws:iam::000000000000:role/r', 'image': 'node:22-alpine',
            'model_data_url': 's3://b/model.tar.gz', 'environment': {'A': 1},
        }).status_code, 200)
        container = client.return_value.create_model.call_args.kwargs['PrimaryContainer']
        self.assertEqual(container['Environment'], {'A': '1'})
        self.assertEqual(_post(self.client, reverse('dashboard:sagemaker-endpoint-detail', kwargs={'endpoint_name': 'ep'}), {'endpoint_config_name': 'c2'}, 'patch').status_code, 200)
        client.return_value.update_endpoint.assert_called_once_with(EndpointName='ep', EndpointConfigName='c2')

    @patch('dashboard.sagemaker_api._client')
    def test_invoke_endpoint_parses_json(self, client):
        body = MagicMock()
        body.read.return_value = b'{"predictions": [2]}'
        client.return_value.invoke_endpoint.return_value = {'Body': body, 'ContentType': 'application/json'}
        response = _post(self.client, reverse('dashboard:sagemaker-endpoint-invoke', kwargs={'endpoint_name': 'ep'}), {'body': '1'})
        self.assertEqual(response.json()['body'], {'predictions': [2]})

    def test_sagemaker_client_never_retries(self):
        from dashboard import sagemaker_api
        from dashboard.aws import FlociClientFactory

        with patch.object(FlociClientFactory, 'client', autospec=True) as client:
            sagemaker_api._client()
        config = client.call_args.args[0].config
        self.assertEqual(config.retries['max_attempts'], 0)
        self.assertEqual(config.read_timeout, 60)


class BedrockConsoleTests(SimpleTestCase):
    @patch('dashboard.bedrock_api._client')
    def test_create_guardrail_builds_policy_configs(self, client):
        client.return_value.create_guardrail.return_value = {'guardrailId': 'g1', 'version': 'DRAFT'}
        response = _post(self.client, reverse('dashboard:bedrock-guardrails'), {
            'name': 'support', 'blocked_input_message': 'no', 'blocked_output_message': 'no',
            'denied_topics': [{'name': 'investing', 'definition': 'Investment advice'}],
            'blocked_words': ['bitcoin'], 'pii_entities': [{'type': 'EMAIL', 'action': 'ANONYMIZE'}],
        })
        self.assertEqual(response.json()['guardrail_id'], 'g1')
        kwargs = client.return_value.create_guardrail.call_args.kwargs
        self.assertEqual(kwargs['topicPolicyConfig']['topicsConfig'][0]['type'], 'DENY')
        self.assertEqual(kwargs['wordPolicyConfig'], {'wordsConfig': [{'text': 'bitcoin'}]})
        self.assertNotIn('contentPolicyConfig', kwargs)

    def test_create_guardrail_requires_messages(self):
        response = _post(self.client, reverse('dashboard:bedrock-guardrails'), {'name': 'x'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()['service'], 'bedrock')


class StatelessAiServiceTests(SimpleTestCase):
    @patch('dashboard.ai_services_api._client')
    def test_comprehend_language_detection_sends_no_language_code(self, client):
        _post(self.client, reverse('dashboard:comprehend-detect', kwargs={'operation': 'detect-dominant-language'}), {'text': 'hola'})
        client.return_value.detect_dominant_language.assert_called_once_with(Text='hola')
        _post(self.client, reverse('dashboard:comprehend-detect', kwargs={'operation': 'detect-sentiment'}), {'text': 'hi', 'language_code': 'es'})
        client.return_value.detect_sentiment.assert_called_once_with(Text='hi', LanguageCode='es')

    def test_comprehend_rejects_unknown_operation(self):
        response = _post(self.client, reverse('dashboard:comprehend-detect', kwargs={'operation': 'delete-everything'}), {'text': 'x'})
        self.assertEqual(response.status_code, 400)

    @patch('dashboard.ai_services_api._client')
    def test_rekognition_uses_s3_object_or_bytes(self, client):
        _post(self.client, reverse('dashboard:rekognition-detect', kwargs={'operation': 'detect-labels'}), {'bucket': 'b', 'key': 'k.png'})
        client.return_value.detect_labels.assert_called_once_with(Image={'S3Object': {'Bucket': 'b', 'Name': 'k.png'}})
        _post(self.client, reverse('dashboard:rekognition-detect', kwargs={'operation': 'detect-text'}), {'image_base64': 'aGk='})
        client.return_value.detect_text.assert_called_once_with(Image={'Bytes': b'hi'})

    @patch('dashboard.ai_services_api._client')
    def test_translate_defaults_to_auto_source(self, client):
        client.return_value.translate_text.return_value = {'TranslatedText': 'Hola', 'SourceLanguageCode': 'en', 'TargetLanguageCode': 'es'}
        response = _post(self.client, reverse('dashboard:translate-text'), {'text': 'Hello', 'target_language': 'es'})
        self.assertEqual(response.json()['translated_text'], 'Hola')
        client.return_value.translate_text.assert_called_once_with(Text='Hello', SourceLanguageCode='auto', TargetLanguageCode='es')


class AiInventoryTests(SimpleTestCase):
    def test_sagemaker_inventory_summarizes_resources(self):
        from dashboard.aws.sagemaker import sagemaker_inventory

        sm = MagicMock()
        sm.can_paginate.return_value = False
        sm.list_training_jobs.return_value = {'TrainingJobSummaries': [{'TrainingJobName': 't', 'TrainingJobStatus': 'Completed'}]}
        sm.describe_training_job.return_value = {'ModelArtifacts': {'S3ModelArtifacts': 's3://b/model.tar.gz'}}
        sm.list_models.return_value = {'Models': []}
        sm.list_endpoint_configs.return_value = {'EndpointConfigs': []}
        sm.list_endpoints.return_value = {'Endpoints': [{'EndpointName': 'e', 'EndpointStatus': 'InService'}]}
        sm.describe_endpoint.return_value = {'EndpointConfigName': 'c'}
        with patch('dashboard.aws.FlociClientFactory') as factory:
            factory.return_value.client.return_value = sm
            result = sagemaker_inventory()
        self.assertEqual(result['summary']['endpoints_in_service'], 1)
        self.assertEqual(result['training_jobs'][0]['model_artifacts'], 's3://b/model.tar.gz')
        self.assertEqual(result['endpoints'][0]['endpoint_config'], 'c')

    def test_bedrock_inventory_counts_numbered_versions(self):
        from dashboard.aws.bedrock import bedrock_inventory

        bedrock = MagicMock()
        bedrock.can_paginate.return_value = False

        def list_guardrails(**kwargs):
            if kwargs.get('guardrailIdentifier'):
                return {'guardrails': [{'version': 'DRAFT'}, {'version': '1'}]}
            return {'guardrails': [{'id': 'g1', 'name': 'support', 'status': 'READY'}]}

        bedrock.list_guardrails.side_effect = list_guardrails
        bedrock.get_guardrail.return_value = {'topicPolicy': {'topics': [{'name': 'investing'}]}}
        with patch('dashboard.aws.FlociClientFactory') as factory:
            factory.return_value.client.return_value = bedrock
            result = bedrock_inventory()
        self.assertEqual(result['summary'], {'guardrails': 1, 'guardrail_versions': 1})
        self.assertEqual(result['guardrails'][0]['denied_topics'], ['investing'])


class AiConsoleRouteTests(SimpleTestCase):
    @patch('dashboard.sagemaker_api._client')
    def test_remaining_sagemaker_routes(self, client):
        for method in ('describe_training_job', 'stop_training_job', 'delete_model', 'create_endpoint_config',
                       'delete_endpoint_config', 'create_endpoint', 'delete_endpoint'):
            getattr(client.return_value, method).return_value = {}
        calls = [
            (reverse('dashboard:sagemaker-training-job-describe'), {'name': 't'}, 'post'),
            (reverse('dashboard:sagemaker-training-job-stop', kwargs={'job_name': 't'}), {}, 'post'),
            (reverse('dashboard:sagemaker-model-detail', kwargs={'model_name': 'm'}), {}, 'delete'),
            (reverse('dashboard:sagemaker-endpoint-configs'), {'name': 'c', 'model_name': 'm'}, 'post'),
            (reverse('dashboard:sagemaker-endpoint-config-detail', kwargs={'config_name': 'c'}), {}, 'delete'),
            (reverse('dashboard:sagemaker-endpoints'), {'name': 'e', 'endpoint_config_name': 'c'}, 'post'),
            (reverse('dashboard:sagemaker-endpoint-detail', kwargs={'endpoint_name': 'e'}), {}, 'delete'),
        ]
        for url, body, method in calls:
            with self.subTest(url=url):
                self.assertEqual(_post(self.client, url, body, method).status_code, 200)
        client.return_value.create_endpoint_config.assert_called_once_with(EndpointConfigName='c', ProductionVariants=[
            {'VariantName': 'AllTraffic', 'ModelName': 'm', 'InitialInstanceCount': 1, 'InstanceType': 'ml.t2.medium'},
        ])

    @patch('dashboard.bedrock_api._client')
    def test_remaining_bedrock_routes(self, client):
        client.return_value.get_guardrail.return_value = {'name': 'g'}
        client.return_value.create_guardrail_version.return_value = {'guardrailId': 'g1', 'version': '1'}
        client.return_value.delete_guardrail.return_value = {}
        self.assertEqual(_post(self.client, reverse('dashboard:bedrock-guardrail-get'), {'guardrail_id': 'g1'}).status_code, 200)
        client.return_value.get_guardrail.assert_called_once_with(guardrailIdentifier='g1')
        response = _post(self.client, reverse('dashboard:bedrock-guardrail-versions', kwargs={'guardrail_id': 'g1'}), {})
        self.assertEqual(response.json()['version'], '1')
        self.assertEqual(_post(self.client, reverse('dashboard:bedrock-guardrail-detail', kwargs={'guardrail_id': 'g1'}), {}, 'delete').status_code, 200)

    @patch('dashboard.ai_services_api._client')
    def test_compare_faces_sends_both_images(self, client):
        client.return_value.compare_faces.return_value = {'FaceMatches': []}
        response = _post(self.client, reverse('dashboard:rekognition-compare-faces'), {
            'source_bucket': 'b', 'source_key': 'a.jpg', 'target_bucket': 'b', 'target_key': 'c.jpg',
        })
        self.assertEqual(response.status_code, 200)
        kwargs = client.return_value.compare_faces.call_args.kwargs
        self.assertEqual(kwargs['TargetImage'], {'S3Object': {'Bucket': 'b', 'Name': 'c.jpg'}})
        self.assertEqual(kwargs['SimilarityThreshold'], 80.0)
