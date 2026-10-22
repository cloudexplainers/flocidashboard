"""Step Functions inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _stepfunctions_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def stepfunctions_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    stepfunctions = factory.client('stepfunctions')
    operations = set(stepfunctions.meta.service_model.operation_names)
    state_machines = _safe_value(lambda: core._paginate(stepfunctions, 'list_state_machines', 'stateMachines'), [])

    def execution_detail(execution: dict[str, Any]) -> dict[str, Any]:
        arn = execution.get('executionArn')
        details = _stepfunctions_optional(
            lambda: stepfunctions.describe_execution(executionArn=arn),
            {'ExecutionDoesNotExist'},
        )
        history = _stepfunctions_optional(
            lambda: stepfunctions.get_execution_history(executionArn=arn, maxResults=25).get('events', []),
            {'ExecutionDoesNotExist'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}

        return {
            'name': execution.get('name') or described.get('name') or arn,
            'arn': arn,
            'state_machine_arn': execution.get('stateMachineArn') or described.get('stateMachineArn'),
            'status': execution.get('status') or described.get('status'),
            'started': execution.get('startDate') or described.get('startDate'),
            'stopped': execution.get('stopDate') or described.get('stopDate'),
            'input': described.get('input'),
            'output': described.get('output'),
            'trace_header': described.get('traceHeader'),
            'history': history,
            'history_event_count': len(history) if isinstance(history, list) else 0,
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def state_machine_detail(machine: dict[str, Any]) -> dict[str, Any]:
        arn = machine.get('stateMachineArn')
        details = _stepfunctions_optional(
            lambda: stepfunctions.describe_state_machine(stateMachineArn=arn),
            {'StateMachineDoesNotExist'},
        )
        executions = _stepfunctions_optional(
            lambda: core._paginate(stepfunctions, 'list_executions', 'executions', stateMachineArn=arn),
            {'StateMachineDoesNotExist'},
        )
        versions = _stepfunctions_optional(
            lambda: core._operation_items(stepfunctions, 'list_state_machine_versions', 'stateMachineVersions', stateMachineArn=arn),
            {'StateMachineDoesNotExist'},
        ) if 'ListStateMachineVersions' in operations else []
        described = details if isinstance(details, dict) and not details.get('error') else {}
        execution_details = [
            execution_detail(execution)
            for execution in executions
        ] if isinstance(executions, list) else []
        version_details = [
            {
                'arn': version.get('stateMachineVersionArn'),
                'revision_id': version.get('revisionId'),
                'creation_date': version.get('creationDate'),
            }
            for version in versions
        ] if isinstance(versions, list) else []

        return {
            'name': machine.get('name') or described.get('name') or arn,
            'arn': arn,
            'type': machine.get('type') or described.get('type'),
            'status': machine.get('status') or described.get('status'),
            'created': machine.get('creationDate') or described.get('creationDate'),
            'role_arn': described.get('roleArn'),
            'definition': described.get('definition'),
            'logging_configuration': described.get('loggingConfiguration'),
            'tracing_configuration': described.get('tracingConfiguration'),
            'revision_id': described.get('revisionId'),
            'label': described.get('label'),
            'versions': version_details,
            'version_count': len(version_details),
            'executions': execution_details,
            'execution_count': len(execution_details),
            'running_executions': sum(1 for execution in execution_details if execution.get('status') == 'RUNNING'),
            'succeeded_executions': sum(1 for execution in execution_details if execution.get('status') == 'SUCCEEDED'),
            'failed_executions': sum(1 for execution in execution_details if execution.get('status') == 'FAILED'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_state_machines = [state_machine_detail(machine) for machine in state_machines]

    return {
        'summary': {
            'state_machines': len(detailed_state_machines),
            'executions': sum(machine.get('execution_count') or 0 for machine in detailed_state_machines),
            'versions': sum(machine.get('version_count') or 0 for machine in detailed_state_machines),
            'running': sum(machine.get('running_executions') or 0 for machine in detailed_state_machines),
            'succeeded': sum(machine.get('succeeded_executions') or 0 for machine in detailed_state_machines),
            'failed': sum(machine.get('failed_executions') or 0 for machine in detailed_state_machines),
        },
        'state_machines': detailed_state_machines,
        'supported': [
            'CreateStateMachine',
            'DescribeStateMachine',
            'ListStateMachines',
            'DeleteStateMachine',
            'StartExecution',
            'DescribeExecution',
            'ListExecutions',
            'StopExecution',
            'GetExecutionHistory',
            'PublishStateMachineVersion',
            'ListStateMachineVersions',
            'DeleteStateMachineVersion',
            'SendTaskSuccess',
            'SendTaskFailure',
            'SendTaskHeartbeat',
        ],
        'protocol': 'JSON 1.1',
        'notes': [
            'Floci exposes Step Functions through the AmazonStatesService JSON 1.1 target.',
            'Floci 1.5.31 supports Map ItemReader for S3-backed JSON datasets and resolves qualified Lambda function ARNs in lambda:invoke service integrations.',
            'Floci 1.5.30 adds state machine version APIs, ECS runTask integration, ResultSelector, ResultPath wildcard projection, ArrayContains, and account-aware execution routing.',
            'Floci 1.5.29 adds aws-sdk integrations for CloudFormation, EC2, and S3 PutObject.',
            'Floci 1.5.28 returns no execution data through the SDK where AWS omits it.',
            'Execution histories are previewed with a small event limit on the dashboard.',
            'Task token callbacks are available for waitForTaskToken workflows.',
        ],
    }


def list_stepfunctions_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    stepfunctions = factory.client('stepfunctions')
    resources = [
        {
            'type': 'state_machine',
            'name': machine.get('name'),
            'arn': machine.get('stateMachineArn'),
            'type_name': machine.get('type'),
        }
        for machine in _safe_value(lambda: core._paginate(stepfunctions, 'list_state_machines', 'stateMachines'), [])
    ]
    for machine in list(resources):
        arn = machine.get('arn')
        resources.extend(
            {
                'type': 'execution',
                'state_machine': arn,
                'name': execution.get('name'),
                'arn': execution.get('executionArn'),
                'status': execution.get('status'),
            }
            for execution in _safe_value(
                lambda arn=arn: core._paginate(stepfunctions, 'list_executions', 'executions', stateMachineArn=arn),
                [],
            )
        )
    return resources
