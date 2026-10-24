"""CloudFormation inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _cloudformation_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def cloudformation_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    cloudformation = factory.client('cloudformation')
    operations = set(cloudformation.meta.service_model.operation_names)
    stack_summaries = _safe_value(lambda: core._paginate(cloudformation, 'list_stacks', 'StackSummaries'), [])

    def stack_name(summary: dict[str, Any]) -> str:
        return summary.get('StackName') or summary.get('StackId') or 'Unnamed stack'

    def stack_detail(summary: dict[str, Any]) -> dict[str, Any]:
        name = stack_name(summary)
        stack = _cloudformation_optional(
            lambda: cloudformation.describe_stacks(StackName=name).get('Stacks', [{}])[0],
            {'ValidationError'},
        )
        events = _cloudformation_optional(
            lambda: core._paginate(cloudformation, 'describe_stack_events', 'StackEvents', StackName=name),
            {'ValidationError'},
        )
        resources = _cloudformation_optional(
            lambda: core._operation_items(cloudformation, 'describe_stack_resources', 'StackResources', StackName=name),
            {'ValidationError'},
        )
        resource_summaries = _cloudformation_optional(
            lambda: core._paginate(cloudformation, 'list_stack_resources', 'StackResourceSummaries', StackName=name),
            {'ValidationError'},
        )
        template = _cloudformation_optional(
            lambda: cloudformation.get_template(StackName=name),
            {'ValidationError'},
        )
        change_sets = _cloudformation_optional(
            lambda: core._paginate(cloudformation, 'list_change_sets', 'Summaries', StackName=name),
            {'ValidationError'},
        )
        stack_policy = _cloudformation_optional(
            lambda: cloudformation.get_stack_policy(StackName=name),
            {'ValidationError'},
        )

        described_stack = stack if isinstance(stack, dict) and not stack.get('error') else {}

        return {
            'name': name,
            'id': summary.get('StackId') or described_stack.get('StackId'),
            'status': summary.get('StackStatus') or described_stack.get('StackStatus'),
            'status_reason': summary.get('StackStatusReason') or described_stack.get('StackStatusReason'),
            'created': summary.get('CreationTime') or described_stack.get('CreationTime'),
            'updated': summary.get('LastUpdatedTime') or described_stack.get('LastUpdatedTime'),
            'deleted': summary.get('DeletionTime') or described_stack.get('DeletionTime'),
            'description': summary.get('TemplateDescription') or described_stack.get('Description'),
            'parameters': described_stack.get('Parameters'),
            'outputs': described_stack.get('Outputs'),
            'tags': described_stack.get('Tags'),
            'capabilities': described_stack.get('Capabilities'),
            'disable_rollback': described_stack.get('DisableRollback'),
            'rollback_configuration': described_stack.get('RollbackConfiguration'),
            'resources': resources,
            'resource_summaries': resource_summaries,
            'resource_count': len(resource_summaries) if isinstance(resource_summaries, list) else 0,
            'events': events,
            'event_count': len(events) if isinstance(events, list) else 0,
            'change_sets': change_sets,
            'change_set_count': len(change_sets) if isinstance(change_sets, list) else 0,
            'template': template,
            'stack_policy': stack_policy,
            'details': stack if isinstance(stack, dict) and stack.get('error') else None,
        }

    detailed_stacks = [stack_detail(summary) for summary in stack_summaries]
    stack_sets = _cloudformation_optional(
        lambda: core._paginate(cloudformation, 'list_stack_sets', 'Summaries'),
        {'ValidationError'},
    ) if 'ListStackSets' in operations else []

    detailed_stack_sets = []
    if isinstance(stack_sets, list):
        for stack_set in stack_sets:
            name = stack_set.get('StackSetName') or stack_set.get('StackSetId')
            details = _cloudformation_optional(
                lambda name=name: cloudformation.describe_stack_set(StackSetName=name).get('StackSet', {}),
                {'ValidationError'},
            ) if 'DescribeStackSet' in operations else None
            instances = _cloudformation_optional(
                lambda name=name: core._operation_items(
                    cloudformation,
                    'list_stack_instances',
                    'Summaries',
                    StackSetName=name,
                ),
                {'ValidationError'},
            ) if 'ListStackInstances' in operations else []
            stack_set_operations = _cloudformation_optional(
                lambda name=name: core._operation_items(
                    cloudformation,
                    'list_stack_set_operations',
                    'Summaries',
                    StackSetName=name,
                ),
                {'ValidationError'},
            ) if 'ListStackSetOperations' in operations else []
            detailed_stack_sets.append({
                'name': name,
                'id': stack_set.get('StackSetId'),
                'status': stack_set.get('Status'),
                'description': stack_set.get('Description'),
                'created': stack_set.get('CreationTime'),
                'updated': stack_set.get('LastUpdatedTime'),
                'permission_model': (
                    details.get('PermissionModel')
                    if isinstance(details, dict) and not details.get('error')
                    else stack_set.get('PermissionModel')
                ),
                'instance_count': len(instances) if isinstance(instances, list) else 0,
                'operation_count': len(stack_set_operations) if isinstance(stack_set_operations, list) else 0,
                'instances': instances,
                'operations': stack_set_operations,
                'details': details,
            })

    return {
        'summary': {
            'stacks': len(detailed_stacks),
            'active_stacks': sum(1 for stack in detailed_stacks if not str(stack.get('status') or '').startswith('DELETE')),
            'resources': sum(stack.get('resource_count') or 0 for stack in detailed_stacks),
            'events': sum(stack.get('event_count') or 0 for stack in detailed_stacks),
            'change_sets': sum(stack.get('change_set_count') or 0 for stack in detailed_stacks),
            'stack_sets': len(detailed_stack_sets),
            'stack_set_instances': sum(stack_set.get('instance_count') or 0 for stack_set in detailed_stack_sets),
            'stack_set_operations': sum(stack_set.get('operation_count') or 0 for stack_set in detailed_stack_sets),
        },
        'stacks': detailed_stacks,
        'stack_sets': detailed_stack_sets,
        'supported': [
            'CreateStack',
            'UpdateStack',
            'DeleteStack',
            'DescribeStacks',
            'ListStacks',
            'DescribeStackEvents',
            'DescribeStackResources',
            'DescribeStackResource',
            'ListStackResources',
            'GetTemplate',
            'ValidateTemplate',
            'CreateChangeSet',
            'DescribeChangeSet',
            'ExecuteChangeSet',
            'ListChangeSets',
            'DeleteChangeSet',
            'SetStackPolicy',
            'GetStackPolicy',
            'ListStackSets',
            'DescribeStackSet',
            'CreateStackSet',
            'UpdateStackSet',
            'DeleteStackSet',
            'CreateStackInstances',
            'DeleteStackInstances',
            'ListStackInstances',
            'ListStackSetOperations',
        ],
        'protocol': 'Query XML',
        'notes': [
            'ValidateTemplate accepts any template and returns no parameters or capabilities; template errors show up only when a stack is created.',
            'Resource types Floci doesn\'t support still reach CREATE_COMPLETE with a placeholder physical ID; nothing is created for them, and the stack events say so.',
            'Stack policies are accepted but not stored or enforced.',
        ],
    }


def list_cloudformation_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    cloudformation = factory.client('cloudformation')
    resources = [
        {
            'type': 'stack',
            'name': stack.get('StackName'),
            'id': stack.get('StackId'),
            'status': stack.get('StackStatus'),
        }
        for stack in _safe_value(lambda: core._paginate(cloudformation, 'list_stacks', 'StackSummaries'), [])
    ]
    stack_sets = _cloudformation_optional(
        lambda: core._paginate(cloudformation, 'list_stack_sets', 'Summaries'),
        {'ValidationError'},
    )
    if isinstance(stack_sets, list):
        resources.extend(
            {
                'type': 'stack_set',
                'name': stack_set.get('StackSetName'),
                'id': stack_set.get('StackSetId'),
                'status': stack_set.get('Status'),
            }
            for stack_set in stack_sets
        )
    return resources
