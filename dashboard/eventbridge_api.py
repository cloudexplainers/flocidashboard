"""Interactive EventBridge helpers for the event sender workbench."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory


def _events_client():
    return FlociClientFactory().client('events')


def _clean_required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def normalize_event_detail(detail: Any) -> str:
    if detail in (None, ''):
        return '{}'
    if isinstance(detail, str):
        try:
            json.loads(detail)
        except json.JSONDecodeError as exc:
            raise ValueError('Event detail must be valid JSON') from exc
        return detail
    if isinstance(detail, (dict, list)):
        return json.dumps(detail)
    raise ValueError('Event detail must be a JSON object, array, or string')


def put_event(
    event_bus_name: str,
    source: str,
    detail_type: str,
    detail: Any,
    *,
    resources: list[str] | None = None,
) -> dict[str, Any]:
    bus = (event_bus_name or '').strip() or 'default'
    event_source = _clean_required(source, 'Source')
    event_detail_type = _clean_required(detail_type, 'Detail type')

    entry: dict[str, Any] = {
        'EventBusName': bus,
        'Source': event_source,
        'DetailType': event_detail_type,
        'Detail': normalize_event_detail(detail),
    }
    if resources:
        entry['Resources'] = [str(resource) for resource in resources if str(resource).strip()]

    response = _events_client().put_events(Entries=[entry])
    entries = response.get('Entries', [])
    return {
        'event_bus_name': bus,
        'failed_entry_count': response.get('FailedEntryCount', 0),
        'entries': entries,
        'event_id': entries[0].get('EventId') if entries else None,
        'error_code': entries[0].get('ErrorCode') if entries else None,
        'error_message': entries[0].get('ErrorMessage') if entries else None,
    }


def create_event_bus(name: str, *, description: str = '') -> dict[str, Any]:
    bus_name = _clean_required(name, 'Event bus name')
    kwargs: dict[str, Any] = {'Name': bus_name}
    if description.strip():
        kwargs['Description'] = description.strip()
    response = _events_client().create_event_bus(**kwargs)
    return {'name': bus_name, 'arn': response.get('EventBusArn'), 'response': response}


def delete_event_bus(name: str) -> dict[str, Any]:
    bus_name = _clean_required(name, 'Event bus name')
    if bus_name == 'default':
        raise ValueError('The default event bus cannot be deleted')
    response = _events_client().delete_event_bus(Name=bus_name)
    return {'name': bus_name, 'deleted': True, 'response': response}


def put_rule(name: str, event_bus_name: str, *, event_pattern: Any = None,
             schedule_expression: str = '', description: str = '', state: str = 'ENABLED') -> dict[str, Any]:
    rule_name = _clean_required(name, 'Rule name')
    bus_name = (event_bus_name or '').strip() or 'default'
    clean_state = (state or 'ENABLED').strip().upper()
    if clean_state not in {'ENABLED', 'DISABLED'}:
        raise ValueError('Rule state must be ENABLED or DISABLED')
    kwargs: dict[str, Any] = {'Name': rule_name, 'EventBusName': bus_name, 'State': clean_state}
    if event_pattern not in (None, ''):
        kwargs['EventPattern'] = normalize_event_detail(event_pattern)
    if schedule_expression.strip():
        kwargs['ScheduleExpression'] = schedule_expression.strip()
    if 'EventPattern' not in kwargs and 'ScheduleExpression' not in kwargs:
        raise ValueError('Event pattern or schedule expression is required')
    if description.strip():
        kwargs['Description'] = description.strip()
    response = _events_client().put_rule(**kwargs)
    return {'name': rule_name, 'event_bus_name': bus_name, 'arn': response.get('RuleArn'), 'state': clean_state, 'response': response}


def set_rule_state(name: str, event_bus_name: str, *, enabled: bool) -> dict[str, Any]:
    rule_name = _clean_required(name, 'Rule name')
    bus_name = (event_bus_name or '').strip() or 'default'
    client = _events_client()
    operation = client.enable_rule if enabled else client.disable_rule
    response = operation(Name=rule_name, EventBusName=bus_name)
    return {'name': rule_name, 'event_bus_name': bus_name, 'state': 'ENABLED' if enabled else 'DISABLED', 'response': response}


def delete_rule(name: str, event_bus_name: str) -> dict[str, Any]:
    rule_name = _clean_required(name, 'Rule name')
    bus_name = (event_bus_name or '').strip() or 'default'
    response = _events_client().delete_rule(Name=rule_name, EventBusName=bus_name)
    return {'name': rule_name, 'event_bus_name': bus_name, 'deleted': True, 'response': response}


def put_target(rule_name: str, event_bus_name: str, target_id: str, arn: str, *,
               role_arn: str = '', input_value: Any = None) -> dict[str, Any]:
    rule = _clean_required(rule_name, 'Rule name')
    bus = (event_bus_name or '').strip() or 'default'
    target: dict[str, Any] = {'Id': _clean_required(target_id, 'Target ID'), 'Arn': _clean_required(arn, 'Target ARN')}
    if role_arn.strip():
        target['RoleArn'] = role_arn.strip()
    if input_value not in (None, ''):
        target['Input'] = normalize_event_detail(input_value)
    response = _events_client().put_targets(Rule=rule, EventBusName=bus, Targets=[target])
    return {'rule_name': rule, 'event_bus_name': bus, 'target': target, 'failed_entry_count': response.get('FailedEntryCount', 0), 'failed_entries': response.get('FailedEntries', [])}


def remove_target(rule_name: str, event_bus_name: str, target_id: str) -> dict[str, Any]:
    rule = _clean_required(rule_name, 'Rule name')
    bus = (event_bus_name or '').strip() or 'default'
    clean_id = _clean_required(target_id, 'Target ID')
    response = _events_client().remove_targets(Rule=rule, EventBusName=bus, Ids=[clean_id])
    return {'rule_name': rule, 'event_bus_name': bus, 'target_id': clean_id, 'removed': response.get('FailedEntryCount', 0) == 0, 'failed_entries': response.get('FailedEntries', [])}


def _match_pattern_value(pattern_val: Any, event_val: Any) -> bool:
    if isinstance(pattern_val, list):
        for matcher in pattern_val:
            if _match_single_item(matcher, event_val):
                return True
        return False
    return _match_single_item(pattern_val, event_val)


def _match_single_item(matcher: Any, event_val: Any) -> bool:
    if matcher is None:
        return event_val is None

    if isinstance(matcher, (str, int, float, bool)):
        if isinstance(event_val, list):
            return matcher in event_val
        return matcher == event_val

    if isinstance(matcher, dict):
        if 'prefix' in matcher:
            prefix = str(matcher['prefix'])
            if isinstance(event_val, list):
                return any(isinstance(v, str) and v.startswith(prefix) for v in event_val)
            return isinstance(event_val, str) and event_val.startswith(prefix)

        if 'suffix' in matcher:
            suffix = str(matcher['suffix'])
            if isinstance(event_val, list):
                return any(isinstance(v, str) and v.endswith(suffix) for v in event_val)
            return isinstance(event_val, str) and event_val.endswith(suffix)

        if 'exists' in matcher:
            expected = bool(matcher['exists'])
            is_present = event_val is not None
            return is_present == expected

        if 'anything-but' in matcher:
            excluded = matcher['anything-but']
            if isinstance(excluded, dict) and 'prefix' in excluded:
                p = str(excluded['prefix'])
                return not (isinstance(event_val, str) and event_val.startswith(p))
            if isinstance(excluded, list):
                return event_val not in excluded
            return event_val != excluded

        if 'numeric' in matcher:
            num_rules = matcher['numeric']
            if not isinstance(event_val, (int, float)):
                return False
            # e.g. [">", 0] or [">=", 10, "<=", 100]
            for i in range(0, len(num_rules), 2):
                op = num_rules[i]
                target_num = num_rules[i + 1]
                if op == '>' and not (event_val > target_num):
                    return False
                if op == '>=' and not (event_val >= target_num):
                    return False
                if op == '<' and not (event_val < target_num):
                    return False
                if op == '<=' and not (event_val <= target_num):
                    return False
                if op == '=' and not (event_val == target_num):
                    return False
            return True

    return False


def _evaluate_pattern_match(pattern_obj: dict[str, Any], event_obj: dict[str, Any]) -> tuple[bool, list[str]]:
    mismatches = []
    for key, pattern_val in pattern_obj.items():
        if key not in event_obj:
            # Check if it was an exists: false matcher
            if isinstance(pattern_val, list) and len(pattern_val) == 1 and isinstance(pattern_val[0], dict) and pattern_val[0].get('exists') is False:
                continue
            mismatches.append(f'Missing field: {key}')
            continue

        event_val = event_obj[key]

        if isinstance(pattern_val, dict) and isinstance(event_val, dict):
            sub_matched, sub_mismatches = _evaluate_pattern_match(pattern_val, event_val)
            if not sub_matched:
                mismatches.extend([f'{key}.{m}' for m in sub_mismatches])
        else:
            if not _match_pattern_value(pattern_val, event_val):
                mismatches.append(f'Field mismatch on "{key}"')

    return (len(mismatches) == 0, mismatches)


def test_event_pattern(event_pattern: Any, event: Any) -> dict[str, Any]:
    if isinstance(event_pattern, str):
        try:
            pattern_obj = json.loads(event_pattern)
        except json.JSONDecodeError as exc:
            raise ValueError('Event pattern must be valid JSON') from exc
    elif isinstance(event_pattern, dict):
        pattern_obj = event_pattern
    else:
        raise ValueError('Event pattern must be a JSON object or string')

    if isinstance(event, str):
        try:
            event_obj = json.loads(event)
        except json.JSONDecodeError as exc:
            raise ValueError('Sample event must be valid JSON') from exc
    elif isinstance(event, dict):
        event_obj = event
    else:
        raise ValueError('Sample event must be a JSON object or string')

    matched, mismatches = _evaluate_pattern_match(pattern_obj, event_obj)
    return {
        'result': matched,
        'mismatches': mismatches,
        'summary': 'Pattern matched sample event successfully' if matched else f'Pattern did not match ({len(mismatches)} mismatches)',
    }


def get_sample_events() -> dict[str, dict[str, Any]]:
    return {
        'ec2_instance_state': {
            'name': 'EC2 Instance State-change Notification',
            'pattern': {
                'source': ['aws.ec2'],
                'detail-type': ['EC2 Instance State-change Notification'],
                'detail': {
                    'state': ['running', 'stopped'],
                },
            },
            'event': {
                'version': '0',
                'id': '7bf73129-1428-4cd3-a780-95db273d1602',
                'detail-type': 'EC2 Instance State-change Notification',
                'source': 'aws.ec2',
                'account': '000000000000',
                'time': '2026-08-25T12:00:00Z',
                'region': 'us-east-1',
                'resources': ['arn:aws:ec2:us-east-1:000000000000:instance/i-1234567890abcdef0'],
                'detail': {
                    'instance-id': 'i-1234567890abcdef0',
                    'state': 'running',
                },
            },
        },
        's3_object_created': {
            'name': 'Amazon S3 Object Created (Put)',
            'pattern': {
                'source': ['aws.s3'],
                'detail-type': ['Object Created'],
                'detail': {
                    'bucket': {
                        'name': ['my-app-uploads'],
                    },
                    'object': {
                        'key': [{'prefix': 'invoices/'}, {'suffix': '.pdf'}],
                    },
                },
            },
            'event': {
                'version': '0',
                'id': '9da9c5e0-3bf8-4e18-8798-4d53cb1c83f9',
                'detail-type': 'Object Created',
                'source': 'aws.s3',
                'account': '000000000000',
                'time': '2026-08-25T12:00:00Z',
                'region': 'us-east-1',
                'resources': ['arn:aws:s3:::my-app-uploads'],
                'detail': {
                    'version': '0',
                    'bucket': {'name': 'my-app-uploads'},
                    'object': {'key': 'invoices/august_2026.pdf', 'size': 2048},
                    'reason': 'PutObject',
                },
            },
        },
        'custom_order_event': {
            'name': 'Custom App Order Processed',
            'pattern': {
                'source': ['custom.ecommerce'],
                'detail-type': ['OrderPlaced', 'OrderShipped'],
                'detail': {
                    'totalAmount': [{'numeric': ['>=', 50]}],
                    'customer': {
                        'tier': [{'anything-but': 'FRAUD_RISK'}],
                    },
                },
            },
            'event': {
                'version': '0',
                'id': '43de8011-8930-4e12-b132-7261a8bb4356',
                'detail-type': 'OrderPlaced',
                'source': 'custom.ecommerce',
                'account': '000000000000',
                'time': '2026-08-25T12:00:00Z',
                'region': 'us-east-1',
                'resources': [],
                'detail': {
                    'orderId': 'ord-9921',
                    'totalAmount': 129.50,
                    'customer': {
                        'id': 'cust-1029',
                        'tier': 'GOLD',
                    },
                },
            },
        },
    }


def test_emulator_event_pattern(event_pattern: Any, event: Any) -> dict[str, Any]:
    """Evaluate with the same emulator engine that routes published events."""
    def object_json(value, label):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError(f'{label} must be valid JSON') from exc
        if not isinstance(value, dict):
            raise ValueError(f'{label} must be a JSON object')
        return json.dumps(value)

    pattern_json = object_json(event_pattern, 'Event pattern')
    event_json = object_json(event, 'Sample event')
    response = _events_client().test_event_pattern(EventPattern=pattern_json, Event=event_json)
    matched = response['Result']
    return {'result': matched, 'engine': 'emulator',
            'summary': 'Floci matched the sample event.' if matched else 'Floci did not match the sample event.'}


def _bus_arn(client, bus: str) -> str:
    """Accepts a bus name or ARN and returns the ARN EventBridge archives need."""
    bus = (bus or '').strip() or 'default'
    if bus.startswith('arn:'):
        return bus
    return client.describe_event_bus(Name=bus)['Arn']


def _iso(value: Any) -> str | None:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def list_archives_and_replays() -> dict[str, Any]:
    client = _events_client()
    archives = []
    for summary in client.list_archives().get('Archives', []):
        detail = client.describe_archive(ArchiveName=summary['ArchiveName'])
        archives.append({
            'name': detail.get('ArchiveName'),
            'arn': detail.get('ArchiveArn'),
            'event_source_arn': detail.get('EventSourceArn'),
            'description': detail.get('Description') or '',
            'event_pattern': detail.get('EventPattern') or '',
            'state': detail.get('State'),
            'retention_days': detail.get('RetentionDays') or 0,
            'event_count': detail.get('EventCount') or 0,
            'size_bytes': detail.get('SizeBytes') or 0,
            'creation_time': _iso(detail.get('CreationTime')),
        })
    replays = []
    for summary in client.list_replays().get('Replays', []):
        detail = client.describe_replay(ReplayName=summary['ReplayName'])
        replays.append({
            'name': detail.get('ReplayName'),
            'arn': detail.get('ReplayArn'),
            'state': detail.get('State'),
            'state_reason': detail.get('StateReason') or '',
            'event_source_arn': detail.get('EventSourceArn'),
            'destination_arn': (detail.get('Destination') or {}).get('Arn'),
            'event_start_time': _iso(detail.get('EventStartTime')),
            'event_end_time': _iso(detail.get('EventEndTime')),
            'event_last_replayed_time': _iso(detail.get('EventLastReplayedTime')),
            'replay_start_time': _iso(detail.get('ReplayStartTime')),
            'replay_end_time': _iso(detail.get('ReplayEndTime')),
        })
    return {'archives': archives, 'replays': replays}


def create_archive(name: str, event_bus: str, *, event_pattern: Any = None,
                   retention_days: Any = 0, description: str = '') -> dict[str, Any]:
    archive_name = _clean_required(name, 'Archive name')
    client = _events_client()
    kwargs: dict[str, Any] = {'ArchiveName': archive_name, 'EventSourceArn': _bus_arn(client, event_bus)}
    if event_pattern not in (None, ''):
        kwargs['EventPattern'] = normalize_event_detail(event_pattern)
    try:
        days = int(retention_days or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError('Retention days must be a whole number (0 keeps events indefinitely)') from exc
    if days < 0:
        raise ValueError('Retention days must be 0 or more')
    kwargs['RetentionDays'] = days
    if (description or '').strip():
        kwargs['Description'] = description.strip()
    response = client.create_archive(**kwargs)
    return {'name': archive_name, 'arn': response.get('ArchiveArn'), 'state': response.get('State')}


def delete_archive(name: str) -> dict[str, Any]:
    archive_name = _clean_required(name, 'Archive name')
    _events_client().delete_archive(ArchiveName=archive_name)
    return {'name': archive_name, 'deleted': True}


def _parse_time(value: Any, label: str):
    from datetime import datetime, timezone
    text = (value or '').strip() if isinstance(value, str) else ''
    if not text:
        raise ValueError(f'{label} is required')
    try:
        parsed = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'{label} must be an ISO 8601 date and time') from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def start_replay(name: str, archive_name: str, *, start_time: Any, end_time: Any,
                 destination_bus: str = '', rule_arns: list[str] | None = None,
                 description: str = '') -> dict[str, Any]:
    replay_name = _clean_required(name, 'Replay name')
    archive = _clean_required(archive_name, 'Archive')
    start = _parse_time(start_time, 'Start time')
    end = _parse_time(end_time, 'End time')
    if end <= start:
        raise ValueError('End time must be after start time')
    client = _events_client()
    detail = client.describe_archive(ArchiveName=archive)
    # A replay must go back to the bus the archive records from.
    destination: dict[str, Any] = {'Arn': _bus_arn(client, destination_bus) if (destination_bus or '').strip() else detail['EventSourceArn']}
    filters = [arn.strip() for arn in (rule_arns or []) if isinstance(arn, str) and arn.strip()]
    if filters:
        destination['FilterArns'] = filters
    kwargs: dict[str, Any] = {
        'ReplayName': replay_name,
        'EventSourceArn': detail['ArchiveArn'],
        'EventStartTime': start,
        'EventEndTime': end,
        'Destination': destination,
    }
    if (description or '').strip():
        kwargs['Description'] = description.strip()
    response = client.start_replay(**kwargs)
    return {'name': replay_name, 'arn': response.get('ReplayArn'), 'state': response.get('State')}


def cancel_replay(name: str) -> dict[str, Any]:
    replay_name = _clean_required(name, 'Replay name')
    response = _events_client().cancel_replay(ReplayName=replay_name)
    return {'name': replay_name, 'state': response.get('State')}
