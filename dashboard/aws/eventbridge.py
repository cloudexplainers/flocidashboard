"""EventBridge inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _eventbridge_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def eventbridge_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    events = factory.client('events')
    buses = _safe_value(lambda: core._operation_items(events, 'list_event_buses', 'EventBuses'), [])

    if not buses:
        buses = [{'Name': 'default'}]

    def bus_name(bus: dict[str, Any]) -> str:
        return bus.get('Name') or bus.get('Arn') or 'default'

    def bus_detail(bus: dict[str, Any]) -> dict[str, Any]:
        name = bus_name(bus)
        details = _eventbridge_optional(
            lambda: events.describe_event_bus(Name=name),
            {'ResourceNotFoundException'},
        )
        rules = _eventbridge_optional(
            lambda: core._operation_items(events, 'list_rules', 'Rules', EventBusName=name),
            {'ResourceNotFoundException'},
        )
        rule_details = []

        if isinstance(rules, list):
            for rule in rules:
                rule_name = rule.get('Name')
                targets = _eventbridge_optional(
                    lambda: core._operation_items(
                        events,
                        'list_targets_by_rule',
                        'Targets',
                        Rule=rule_name,
                        EventBusName=name,
                    ),
                    {'ResourceNotFoundException'},
                )
                described_rule = _eventbridge_optional(
                    lambda: events.describe_rule(Name=rule_name, EventBusName=name),
                    {'ResourceNotFoundException'},
                )
                rule_details.append({
                    'name': rule_name,
                    'arn': rule.get('Arn'),
                    'state': rule.get('State'),
                    'description': rule.get('Description'),
                    'schedule_expression': rule.get('ScheduleExpression'),
                    'event_pattern': rule.get('EventPattern'),
                    'role_arn': rule.get('RoleArn'),
                    'managed_by': rule.get('ManagedBy'),
                    'details': described_rule,
                    'targets': targets,
                    'target_count': len(targets) if isinstance(targets, list) else 0,
                })

        return {
            'name': name,
            'arn': bus.get('Arn') or (details.get('Arn') if isinstance(details, dict) else None),
            'description': bus.get('Description') or (details.get('Description') if isinstance(details, dict) else None),
            'policy': bus.get('Policy') or (details.get('Policy') if isinstance(details, dict) else None),
            'details': details,
            'rules': rule_details,
            'rule_count': len(rule_details),
            'target_count': sum(rule.get('target_count') or 0 for rule in rule_details),
        }

    detailed_buses = [bus_detail(bus) for bus in buses]

    return {
        'summary': {
            'event_buses': len(detailed_buses),
            'rules': sum(bus.get('rule_count') or 0 for bus in detailed_buses),
            'targets': sum(bus.get('target_count') or 0 for bus in detailed_buses),
            'enabled_rules': sum(
                1
                for bus in detailed_buses
                for rule in bus.get('rules', [])
                if rule.get('state') == 'ENABLED'
            ),
        },
        'event_buses': detailed_buses,
        'supported': [
            'CreateEventBus',
            'DeleteEventBus',
            'DescribeEventBus',
            'ListEventBuses',
            'PutRule',
            'DeleteRule',
            'DescribeRule',
            'ListRules',
            'EnableRule',
            'DisableRule',
            'PutTargets',
            'RemoveTargets',
            'ListTargetsByRule',
            'PutEvents',
        ],
        'notes': [],
    }


def list_eventbridge_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    events = factory.client('events')
    resources: list[dict[str, Any]] = []
    buses = _safe_value(lambda: core._paginate(events, 'list_event_buses', 'EventBuses'), [])

    if not buses:
        buses = [{'Name': 'default'}]

    for bus in buses:
        name = bus.get('Name') or 'default'
        resources.append({
            'type': 'event_bus',
            'name': name,
            'arn': bus.get('Arn'),
        })
        rules = _safe_value(lambda name=name: core._paginate(events, 'list_rules', 'Rules', EventBusName=name), [])
        resources.extend(
            {
                'type': 'rule',
                'event_bus': name,
                'name': rule.get('Name'),
                'arn': rule.get('Arn'),
                'state': rule.get('State'),
            }
            for rule in rules
        )
        for rule in rules:
            rule_name = rule.get('Name')
            resources.extend(
                {
                    'type': 'target',
                    'event_bus': name,
                    'rule': rule_name,
                    'id': target.get('Id'),
                    'arn': target.get('Arn'),
                }
                for target in _safe_value(
                    lambda name=name, rule_name=rule_name: core._paginate(
                        events,
                        'list_targets_by_rule',
                        'Targets',
                        Rule=rule_name,
                        EventBusName=name,
                    ),
                    [],
                )
            )

    return resources
