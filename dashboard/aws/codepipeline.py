"""CodePipeline inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def codepipeline_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    codepipeline = factory.client('codepipeline')
    operations = set(codepipeline.meta.service_model.operation_names)

    pipelines = _safe_value(lambda: core._operation_items(codepipeline, 'list_pipelines', 'pipelines'), []) if 'ListPipelines' in operations else []
    webhooks = _safe_value(lambda: core._operation_items(codepipeline, 'list_webhooks', 'webhooks'), []) if 'ListWebhooks' in operations else []
    action_types = _safe_value(lambda: core._operation_items(codepipeline, 'list_action_types', 'actionTypes'), []) if 'ListActionTypes' in operations else []

    def pipeline_detail(pipeline: dict[str, Any]) -> dict[str, Any]:
        name = pipeline.get('name')
        detail = _safe_value(lambda name=name: codepipeline.get_pipeline(name=name), {}) if name and 'GetPipeline' in operations else {}
        state = _safe_value(lambda name=name: codepipeline.get_pipeline_state(name=name), {}) if name and 'GetPipelineState' in operations else {}
        executions = _safe_value(
            lambda name=name: core._operation_items(codepipeline, 'list_pipeline_executions', 'pipelineExecutionSummaries', pipelineName=name),
            [],
        ) if name and 'ListPipelineExecutions' in operations else []
        return {
            'name': name,
            'version': pipeline.get('version') or detail.get('pipeline', {}).get('version'),
            'created': pipeline.get('created') or detail.get('metadata', {}).get('created'),
            'updated': pipeline.get('updated') or detail.get('metadata', {}).get('updated'),
            'pipeline_type': detail.get('pipeline', {}).get('pipelineType'),
            'stages': detail.get('pipeline', {}).get('stages'),
            'stage_states': state.get('stageStates'),
            'execution_count': len(executions),
            'executions': executions,
        }

    pipeline_details = [pipeline_detail(pipeline) for pipeline in pipelines[:50]]
    core.record_truncation('GetPipeline', 'pipelines (pipeline_summaries lists them all)', len(pipeline_details), len(pipelines))

    return {
        'summary': {
            'pipelines': len(pipelines),
            'sampled_executions': sum(pipeline.get('execution_count') or 0 for pipeline in pipeline_details),
            'webhooks': len(webhooks),
            'action_types': len(action_types),
            'available_sdk_operations': len(operations),
        },
        'pipelines': _clean_response(pipeline_details),
        'pipeline_summaries': _clean_response(pipelines),
        'webhooks': _clean_response(webhooks),
        'action_types': _clean_response(action_types),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreatePipeline',
                'ListPipelines',
                'GetPipeline',
                'GetPipelineState',
                'ListPipelineExecutions',
                'StartPipelineExecution',
                'DeletePipeline',
                'ListWebhooks',
                'ListActionTypes',
                'TagResource',
                'UntagResource',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.27 adds AWS CodePipeline emulation.',
            'This read-only inspector expands the first 50 pipelines with pipeline definition, state, and execution summaries when supported.',
        ],
    }


def list_codepipeline_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    codepipeline = factory.client('codepipeline')
    operations = set(codepipeline.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListPipelines' in operations:
        resources.extend(
            {
                'type': 'pipeline',
                'name': pipeline.get('name'),
                'version': pipeline.get('version'),
            }
            for pipeline in _safe_value(lambda: core._operation_items(codepipeline, 'list_pipelines', 'pipelines'), [])
        )
    if 'ListWebhooks' in operations:
        resources.extend(
            {
                'type': 'webhook',
                'name': webhook.get('name'),
                'arn': webhook.get('arn'),
            }
            for webhook in _safe_value(lambda: core._operation_items(codepipeline, 'list_webhooks', 'webhooks'), [])
        )

    return resources
