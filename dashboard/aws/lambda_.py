"""Lambda inventory and resource-list loaders."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _lambda_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def lambda_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    lambda_client = factory.client('lambda')
    functions = _safe_value(lambda: core._paginate(lambda_client, 'list_functions', 'Functions'), [])
    event_source_mappings = _safe_value(
        lambda: core._paginate(lambda_client, 'list_event_source_mappings', 'EventSourceMappings'),
        [],
    )

    mappings_by_function: dict[str, list[dict[str, Any]]] = {}
    for mapping in event_source_mappings:
        function_arn = mapping.get('FunctionArn')
        if function_arn:
            function_name = function_arn.rsplit(':', 1)[-1]
            mappings_by_function.setdefault(function_name, []).append(_clean_response(mapping))

    def function_detail(function: dict[str, Any]) -> dict[str, Any]:
        name = function.get('FunctionName')
        arn = function.get('FunctionArn')
        function_response = _lambda_optional(
            lambda: lambda_client.get_function(FunctionName=name),
            {'ResourceNotFoundException'},
        )
        configuration = _lambda_optional(
            lambda: lambda_client.get_function_configuration(FunctionName=name),
            {'ResourceNotFoundException'},
        )
        policy = _lambda_optional(
            lambda: json.loads(lambda_client.get_policy(FunctionName=name).get('Policy', '{}')),
            {'ResourceNotFoundException'},
        )

        return {
            'name': name,
            'arn': arn,
            'runtime': function.get('Runtime'),
            'handler': function.get('Handler'),
            'package_type': function.get('PackageType'),
            'state': function.get('State'),
            'last_modified': function.get('LastModified'),
            'role': function.get('Role'),
            'memory_size': function.get('MemorySize'),
            'timeout': function.get('Timeout'),
            'architectures': function.get('Architectures'),
            'environment': function.get('Environment'),
            'tracing_config': function.get('TracingConfig'),
            'layers': function.get('Layers'),
            'code': function_response.get('Code') if isinstance(function_response, dict) else function_response,
            'configuration': configuration,
            'versions': _lambda_optional(
                lambda: core._paginate(lambda_client, 'list_versions_by_function', 'Versions', FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'aliases': _lambda_optional(
                lambda: core._paginate(lambda_client, 'list_aliases', 'Aliases', FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'event_source_mappings': mappings_by_function.get(name, []),
            'policy': policy,
            'function_url': _lambda_optional(
                lambda: lambda_client.get_function_url_config(FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'code_signing_config': _lambda_optional(
                lambda: lambda_client.get_function_code_signing_config(FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'concurrency': _lambda_optional(
                lambda: lambda_client.get_function_concurrency(FunctionName=name),
                {'ResourceNotFoundException'},
            ),
            'tags': _lambda_optional(
                lambda: lambda_client.list_tags(Resource=arn).get('Tags', {}),
                {'ResourceNotFoundException'},
            ) if arn else None,
        }

    # Detail enrichment is independent per function and otherwise requires many
    # sequential round trips. Preserve list ordering while bounding concurrency.
    if len(functions) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(functions))) as executor:
            detailed_functions = core.map_in_context(executor, function_detail, functions)
    else:
        detailed_functions = [function_detail(function) for function in functions]

    def layer_name(layer: dict[str, Any]) -> str | None:
        arn = layer.get('Arn') or layer.get('LayerVersionArn') or layer.get('LayerArn')
        if not arn or ':layer:' not in arn:
            return None
        return arn.split(':layer:', 1)[1].split(':', 1)[0]

    attached_layer_names = sorted({
        name
        for function in detailed_functions
        for name in [
            layer_name(layer)
            for layer in function.get('layers') or []
            if isinstance(layer, dict)
        ]
        if name
    })
    layer_versions = [
        {
            'name': name,
            'versions': _lambda_optional(
                lambda name=name: core._paginate(lambda_client, 'list_layer_versions', 'LayerVersions', LayerName=name),
                {'ResourceNotFoundException'},
            ),
        }
        for name in attached_layer_names
    ]

    return {
        'summary': {
            'functions': len(detailed_functions),
            'event_source_mappings': len(event_source_mappings),
            'function_urls': sum(1 for function in detailed_functions if function.get('function_url')),
            'attached_layers': len(attached_layer_names),
            'aliases': sum(
                len(function.get('aliases') or [])
                for function in detailed_functions
                if isinstance(function.get('aliases'), list)
            ),
            'published_versions': sum(
                len(function.get('versions') or [])
                for function in detailed_functions
                if isinstance(function.get('versions'), list)
            ),
        },
        'functions': detailed_functions,
        'event_source_mappings': _clean_response(event_source_mappings),
        'layer_versions': layer_versions,
        'supported': [
            'CreateFunction',
            'GetFunction',
            'GetFunctionConfiguration',
            'ListFunctions',
            'UpdateFunctionCode',
            'UpdateFunctionConfiguration',
            'DeleteFunction',
            'Invoke',
            'CreateEventSourceMapping',
            'GetEventSourceMapping',
            'ListEventSourceMappings',
            'UpdateEventSourceMapping',
            'DeleteEventSourceMapping',
            'PublishVersion',
            'ListVersionsByFunction',
            'CreateAlias',
            'GetAlias',
            'ListAliases',
            'UpdateAlias',
            'DeleteAlias',
            'AddPermission',
            'GetPolicy',
            'RemovePermission',
            'GetFunctionCodeSigningConfig',
            'CreateFunctionUrlConfig',
            'GetFunctionUrlConfig',
            'UpdateFunctionUrlConfig',
            'DeleteFunctionUrlConfig',
            'ListTags',
            'TagResource',
            'UntagResource',
            'PutFunctionConcurrency',
            'GetFunctionConcurrency',
            'DeleteFunctionConcurrency',
            'PublishLayerVersion',
            'GetLayerVersion',
            'ListLayerVersions',
            'DeleteLayerVersion',
        ],
        'not_implemented': [
            'Layer permission operations',
            'Provisioned concurrency operations',
            'InvokeWithResponseStream',
            'Code signing management beyond GetFunctionCodeSigningConfig',
            'GetAccountSettings',
        ],
        'notes': [
            'Lambda runs function code inside real Docker containers.',
            'S3-based deployments support reactive hot reload when the source object changes.',
            'Bind-mount hot reload uses S3Bucket=hot-reload and requires explicit configuration.',
            'Reserved concurrency is enforced; provisioned concurrency is not implemented.',
            'The memory size is passed to the function but CPU does not scale with it. Function URLs with AWS_IAM auth also answer unsigned requests, and UpdateAlias does not clear routing weights.',
            'Async invocations are real: event invoke configs (retries, maximum event age), SQS destinations, and dead-letter queues work, with the first retry about a minute later. Once an OnFailure destination is set, failed events go only there, not to the DLQ. Synchronous invokes return no LogResult tail.',
            'Inside a function, KMS calls use the execution role and Floci checks kms:Decrypt against key/* rather than the key ARN.',
        ],
    }


def list_lambda_functions(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': function.get('FunctionName'),
            'runtime': function.get('Runtime'),
            'arn': function.get('FunctionArn'),
        }
        for function in factory.client('lambda').get_paginator('list_functions').paginate().build_full_result().get('Functions', [])
    ]
