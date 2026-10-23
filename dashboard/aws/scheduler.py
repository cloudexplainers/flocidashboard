"""EventBridge Scheduler inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _scheduler_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def scheduler_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    scheduler = factory.client('scheduler')
    groups = _safe_value(lambda: core._paginate(scheduler, 'list_schedule_groups', 'ScheduleGroups'), [])
    schedules = _safe_value(lambda: core._paginate(scheduler, 'list_schedules', 'Schedules'), [])

    def group_name(group: dict[str, Any]) -> str:
        return group.get('Name') or group.get('Arn') or 'default'

    def group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group_name(group)
        details = _scheduler_optional(
            lambda: scheduler.get_schedule_group(Name=name),
            {'ResourceNotFoundException'},
        )
        group_schedules = _scheduler_optional(
            lambda: core._paginate(scheduler, 'list_schedules', 'Schedules', GroupName=name),
            {'ResourceNotFoundException'},
        )

        return {
            'name': name,
            'arn': group.get('Arn') or (details.get('Arn') if isinstance(details, dict) else None),
            'state': group.get('State') or (details.get('State') if isinstance(details, dict) else None),
            'created': group.get('CreationDate') or (details.get('CreationDate') if isinstance(details, dict) else None),
            'last_modified': group.get('LastModificationDate') or (details.get('LastModificationDate') if isinstance(details, dict) else None),
            'schedule_count': len(group_schedules) if isinstance(group_schedules, list) else 0,
            'schedules': group_schedules,
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    def schedule_detail(schedule: dict[str, Any]) -> dict[str, Any]:
        name = schedule.get('Name')
        group = schedule.get('GroupName') or 'default'
        details = _scheduler_optional(
            lambda: scheduler.get_schedule(Name=name, GroupName=group),
            {'ResourceNotFoundException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}

        return {
            'name': name,
            'arn': schedule.get('Arn') or described.get('Arn'),
            'group': group,
            'state': schedule.get('State') or described.get('State'),
            'expression': schedule.get('ScheduleExpression') or described.get('ScheduleExpression'),
            'timezone': described.get('ScheduleExpressionTimezone'),
            'start_date': described.get('StartDate'),
            'end_date': described.get('EndDate'),
            'action_after_completion': described.get('ActionAfterCompletion'),
            'flexible_time_window': described.get('FlexibleTimeWindow'),
            'target': described.get('Target') or schedule.get('Target'),
            'created': schedule.get('CreationDate') or described.get('CreationDate'),
            'last_modified': schedule.get('LastModificationDate') or described.get('LastModificationDate'),
            'description': described.get('Description'),
            'kms_key_arn': described.get('KmsKeyArn'),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_groups = [group_detail(group) for group in groups]
    detailed_schedules = [schedule_detail(schedule) for schedule in schedules]

    return {
        'summary': {
            'groups': len(detailed_groups),
            'schedules': len(detailed_schedules),
            'enabled': sum(1 for schedule in detailed_schedules if schedule.get('state') == 'ENABLED'),
            'disabled': sum(1 for schedule in detailed_schedules if schedule.get('state') == 'DISABLED'),
        },
        'groups': detailed_groups,
        'schedules': detailed_schedules,
        'supported': [
            'CreateScheduleGroup',
            'GetScheduleGroup',
            'DeleteScheduleGroup',
            'ListScheduleGroups',
            'CreateSchedule',
            'GetSchedule',
            'UpdateSchedule',
            'DeleteSchedule',
            'ListSchedules',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'supported_expressions': [
            'at(YYYY-MM-DDTHH:mm:ss)',
            'rate(N minutes|hours|days|weeks)',
            'cron(minute hour day-of-month month day-of-week year)',
        ],
        'supported_targets': [
            'SQS',
            'Lambda',
            'SNS',
            'ECS RunTask',
            'EventBridge PutEvents',
            'Universal AWS SDK targets for SNS Publish and SQS SendMessage',
        ],
        'not_supported': [
            'RetryPolicy and DeadLetterConfig enforcement',
            'FlexibleTimeWindow jitter',
            'NextToken-based pagination',
        ],
        'configuration': {
            'invocation_enabled': True,
            'tick_interval_seconds': 10,
        },
        'notes': [
            'The default schedule group is created automatically on first access.',
            'Schedules without an explicit group are placed in the default group.',
            'The default group cannot be deleted.',
            'State=DISABLED schedules and schedules outside StartDate/EndDate are skipped.',
            'FIFO SQS targets can carry MessageGroupId through Target.SqsParameters.',
        ],
    }


def list_scheduler_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    scheduler = factory.client('scheduler')
    resources = [
        {
            'type': 'schedule_group',
            'name': group.get('Name'),
            'arn': group.get('Arn'),
            'state': group.get('State'),
        }
        for group in _safe_value(lambda: core._paginate(scheduler, 'list_schedule_groups', 'ScheduleGroups'), [])
    ]
    resources.extend(
        {
            'type': 'schedule',
            'name': schedule.get('Name'),
            'arn': schedule.get('Arn'),
            'group': schedule.get('GroupName'),
            'state': schedule.get('State'),
            'expression': schedule.get('ScheduleExpression'),
        }
        for schedule in _safe_value(lambda: core._paginate(scheduler, 'list_schedules', 'Schedules'), [])
    )
    return resources
