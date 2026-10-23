"""Bedrock Runtime inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core


def bedrockruntime_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    endpoint = factory.endpoint_url.rstrip('/')
    runtime = factory.client('bedrock-runtime')
    available_sdk_operations = runtime.meta.service_model.operation_names

    supported_operations = [
        {
            'name': 'Converse',
            'operation': 'Converse',
            'method': 'POST',
            'endpoint': '/model/{modelId}/converse',
            'status': 'supported',
            'notes': 'Returns a static assistant message with synthetic token usage.',
        },
        {
            'name': 'InvokeModel',
            'operation': 'InvokeModel',
            'method': 'POST',
            'endpoint': '/model/{modelId}/invoke',
            'status': 'supported',
            'notes': 'Returns an Anthropic-shaped body for anthropic model IDs and a generic outputs body otherwise.',
        },
        {
            'name': 'ConverseStream',
            'operation': 'ConverseStream',
            'method': 'POST',
            'endpoint': '/model/{modelId}/converse-stream',
            'status': 'unsupported',
            'notes': 'Returns 501 UnsupportedOperationException.',
        },
        {
            'name': 'InvokeModelWithResponseStream',
            'operation': 'InvokeModelWithResponseStream',
            'method': 'POST',
            'endpoint': '/model/{modelId}/invoke-with-response-stream',
            'status': 'unsupported',
            'notes': 'Returns 501 UnsupportedOperationException.',
        },
    ]

    examples = [
        {
            'name': 'Converse',
            'cli': (
                'aws bedrock-runtime converse '
                '--model-id anthropic.claude-3-haiku-20240307-v1:0 '
                '--messages \'[{"role":"user","content":[{"text":"hi"}]}]\''
            ),
        },
        {
            'name': 'InvokeModel',
            'cli': (
                'aws bedrock-runtime invoke-model '
                '--model-id anthropic.claude-3-haiku-20240307-v1:0 '
                '--body \'{"anthropic_version":"bedrock-2023-05-31","max_tokens":100,"messages":[{"role":"user","content":"hi"}]}\' '
                '--cli-binary-format raw-in-base64-out /tmp/response.json'
            ),
        },
    ]

    return {
        'summary': {
            'supported_operations': sum(1 for operation in supported_operations if operation.get('status') == 'supported'),
            'unsupported_streaming_operations': sum(1 for operation in supported_operations if operation.get('status') == 'unsupported'),
            'management_plane_resources': 0,
            'real_inference': 0,
        },
        'operations': supported_operations,
        'configuration': {
            'service_key': 'bedrock-runtime',
            'enabled_variable': 'floci.services.bedrock-runtime.enabled',
            'endpoint_pattern': f'{endpoint}/model/{{modelId}}/...',
            'protocol': 'REST JSON',
        },
        'model_id_support': [
            {
                'name': 'Plain model ID',
                'example': 'anthropic.claude-3-haiku-20240307-v1:0',
            },
            {
                'name': 'Inference profile ID',
                'example': 'us.anthropic.claude-3-5-sonnet-20241022-v2:0',
            },
            {
                'name': 'Inference profile ARN',
                'example': 'arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.anthropic.claude-3-5-sonnet-20241022-v2:0',
            },
        ],
        'request_behavior': [
            {
                'name': 'Converse validation',
                'detail': 'messages must be a non-empty array; system, inferenceConfig, and toolConfig are accepted and ignored.',
            },
            {
                'name': 'InvokeModel body handling',
                'detail': 'request bodies are accepted as opaque bytes and are not parsed by the stub.',
            },
            {
                'name': 'Tool use',
                'detail': 'tool-use round-tripping is not implemented.',
            },
        ],
        'examples': examples,
        'available_sdk_operations': available_sdk_operations,
        'out_of_scope': [
            'Real model inference; responses are fixed stubs.',
            'Streaming responses.',
            'Bedrock management plane operations such as ListFoundationModels and GetFoundationModel.',
            'Bedrock Agents, Knowledge Bases, Guardrails, provisioned throughput, and customization.',
            'Tool-use round-tripping in Converse.',
        ],
        'notes': [
            'Floci keeps no Bedrock Runtime resources, so this page describes the data-plane calls instead.',
        ],
    }


def list_bedrockruntime_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'type': 'operation',
            'name': 'Converse',
            'endpoint': '/model/{modelId}/converse',
            'status': 'supported',
        },
        {
            'type': 'operation',
            'name': 'InvokeModel',
            'endpoint': '/model/{modelId}/invoke',
            'status': 'supported',
        },
        {
            'type': 'operation',
            'name': 'ConverseStream',
            'endpoint': '/model/{modelId}/converse-stream',
            'status': 'unsupported',
        },
        {
            'type': 'operation',
            'name': 'InvokeModelWithResponseStream',
            'endpoint': '/model/{modelId}/invoke-with-response-stream',
            'status': 'unsupported',
        },
    ]
