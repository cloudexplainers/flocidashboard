"""EventBridge Pipes inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def pipes_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    pipes = factory.client('pipes')
    summaries = _safe_value(lambda: core._paginate(pipes, 'list_pipes', 'Pipes'), [])

    def pipe_detail(summary: dict[str, Any]) -> dict[str, Any]:
        name = summary.get('Name')
        detail = _safe_value(lambda name=name: _clean_response(pipes.describe_pipe(Name=name)), {}) if name else {}
        arn = detail.get('Arn') or summary.get('Arn')
        tags = detail.get('Tags')
        if tags is None and arn:
            tags = _safe_value(lambda arn=arn: pipes.list_tags_for_resource(resourceArn=arn).get('tags', {}), {})

        return {
            'name': name,
            'arn': arn,
            'description': detail.get('Description') or summary.get('Description'),
            'desired_state': detail.get('DesiredState') or summary.get('DesiredState'),
            'current_state': detail.get('CurrentState') or summary.get('CurrentState'),
            'state_reason': detail.get('StateReason') or summary.get('StateReason'),
            'source': detail.get('Source') or summary.get('Source'),
            'source_parameters': detail.get('SourceParameters'),
            'enrichment': detail.get('Enrichment'),
            'enrichment_parameters': detail.get('EnrichmentParameters'),
            'target': detail.get('Target') or summary.get('Target'),
            'target_parameters': detail.get('TargetParameters'),
            'role_arn': detail.get('RoleArn'),
            'log_configuration': detail.get('LogConfiguration'),
            'kms_key_identifier': detail.get('KmsKeyIdentifier'),
            'created': detail.get('CreationTime') or summary.get('CreationTime'),
            'last_modified': detail.get('LastModifiedTime') or summary.get('LastModifiedTime'),
            'tags': tags or {},
        }

    pipe_details = [pipe_detail(summary) for summary in summaries]
    states: dict[str, int] = {}
    for pipe in pipe_details:
        state = pipe.get('current_state') or 'UNKNOWN'
        states[state] = states.get(state, 0) + 1

    return {
        'summary': {
            'pipes': len(pipe_details),
            'running': states.get('RUNNING', 0),
            'stopped': states.get('STOPPED', 0),
            'creating': states.get('CREATING', 0),
            'deleting': states.get('DELETING', 0),
        },
        'pipes': pipe_details,
        'states': [{'name': state, 'state': state, 'count': count} for state, count in sorted(states.items())],
        'supported_from_sdk': [
            'CreatePipe',
            'DescribePipe',
            'ListPipes',
            'StartPipe',
            'StopPipe',
            'UpdatePipe',
            'DeletePipe',
            'ListTagsForResource',
            'TagResource',
            'UntagResource',
        ],
        'supported_sources': [
            'SQS queues',
            'Kinesis streams',
            'DynamoDB streams',
            'Kafka topics (MSK)',
        ],
        'supported_targets': [
            'Lambda functions',
            'SQS queues',
            'SNS topics',
            'Kinesis streams',
            'Step Functions state machines',
        ],
        'pipe_states': [
            'STARTING',
            'RUNNING',
            'STOPPING',
            'STOPPED',
            'DELETED',
        ],
        'notes': [
            'EventBridge Pipes uses a REST-JSON API against the local Floci endpoint.',
            'Pipe summaries are expanded with DescribePipe so source, enrichment, target, logging, role, KMS, and tag data are visible.',
            'Supported sources include SQS, Kinesis, DynamoDB streams, and Kafka topics.',
            'Floci 1.5.28 adds Kafka pipe sources and polling support.',
            'Supported targets include Lambda, SQS, SNS, Kinesis, and Step Functions.',
        ],
    }


def list_pipes_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    pipes = factory.client('pipes')
    return [
        {
            'type': 'pipe',
            'name': pipe.get('Name'),
            'arn': pipe.get('Arn'),
            'desired_state': pipe.get('DesiredState'),
            'current_state': pipe.get('CurrentState'),
            'source': pipe.get('Source'),
            'target': pipe.get('Target'),
        }
        for pipe in _safe_value(lambda: core._paginate(pipes, 'list_pipes', 'Pipes'), [])
    ]
