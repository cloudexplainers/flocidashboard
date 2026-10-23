"""HTTP endpoints for the EventBridge event sender workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .eventbridge_api import (cancel_replay, create_archive, create_event_bus, delete_archive,
                              delete_event_bus, delete_rule, list_archives_and_replays, put_event,
                              put_rule, put_target, remove_target, set_rule_state, start_replay)


@require_http_methods(['POST'])
def eventbridge_events_put(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(put_event(
            body.get('event_bus_name') or 'default',
            body.get('source', ''),
            body.get('detail_type', ''),
            body.get('detail', {}),
            resources=body.get('resources') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='eventbridge', operation='put_event')


def _action(request, operation, callback):
    try:
        return JsonResponse(callback(parse_json_body(request)))
    except Exception as exc:
        return handle_action_error(exc, service='eventbridge', operation=operation)


@require_http_methods(['POST'])
def eventbridge_buses_create(request):
    return _action(request, 'create_event_bus', lambda b: create_event_bus(b.get('name', ''), description=b.get('description') or ''))


@require_http_methods(['POST'])
def eventbridge_buses_delete(request):
    return _action(request, 'delete_event_bus', lambda b: delete_event_bus(b.get('name', '')))


@require_http_methods(['POST'])
def eventbridge_rules_put(request):
    return _action(request, 'put_rule', lambda b: put_rule(b.get('name', ''), b.get('event_bus_name', ''), event_pattern=b.get('event_pattern'), schedule_expression=b.get('schedule_expression') or '', description=b.get('description') or '', state=b.get('state') or 'ENABLED'))


@require_http_methods(['POST'])
def eventbridge_rules_state(request):
    return _action(request, 'set_rule_state', lambda b: set_rule_state(b.get('name', ''), b.get('event_bus_name', ''), enabled=bool(b.get('enabled'))))


@require_http_methods(['POST'])
def eventbridge_rules_delete(request):
    return _action(request, 'delete_rule', lambda b: delete_rule(b.get('name', ''), b.get('event_bus_name', '')))


@require_http_methods(['POST'])
def eventbridge_targets_put(request):
    return _action(request, 'put_target', lambda b: put_target(b.get('rule_name', ''), b.get('event_bus_name', ''), b.get('target_id', ''), b.get('arn', ''), role_arn=b.get('role_arn') or '', input_value=b.get('input')))


@require_http_methods(['POST'])
def eventbridge_targets_remove(request):
    return _action(request, 'remove_target', lambda b: remove_target(b.get('rule_name', ''), b.get('event_bus_name', ''), b.get('target_id', '')))


@require_http_methods(['POST'])
def eventbridge_pattern_test(request):
    try:
        from .eventbridge_api import test_event_pattern
        body = parse_json_body(request)
        if body.get('engine') == 'emulator':
            from .eventbridge_api import test_emulator_event_pattern
            return JsonResponse(test_emulator_event_pattern(body.get('event_pattern'), body.get('event')))
        return JsonResponse(test_event_pattern(
            event_pattern=body.get('event_pattern'),
            event=body.get('event'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='eventbridge', operation='test_event_pattern')


@require_http_methods(['GET'])
def eventbridge_sample_events(request):
    try:
        from .eventbridge_api import get_sample_events
        return JsonResponse(get_sample_events())
    except Exception as exc:
        return handle_action_error(exc, service='eventbridge', operation='get_sample_events')


@require_http_methods(['GET'])
def eventbridge_archives(request):
    try:
        return JsonResponse(list_archives_and_replays())
    except Exception as exc:
        return handle_action_error(exc, service='eventbridge', operation='list_archives')


@require_http_methods(['POST'])
def eventbridge_archives_create(request):
    return _action(request, 'create_archive', lambda b: create_archive(
        b.get('name', ''), b.get('event_bus_name', ''), event_pattern=b.get('event_pattern'),
        retention_days=b.get('retention_days', 0), description=b.get('description') or ''))


@require_http_methods(['POST'])
def eventbridge_archives_delete(request):
    return _action(request, 'delete_archive', lambda b: delete_archive(b.get('name', '')))


@require_http_methods(['POST'])
def eventbridge_replays_start(request):
    return _action(request, 'start_replay', lambda b: start_replay(
        b.get('name', ''), b.get('archive_name', ''), start_time=b.get('start_time'), end_time=b.get('end_time'),
        destination_bus=b.get('destination_bus') or '', rule_arns=b.get('rule_arns') or None,
        description=b.get('description') or ''))


@require_http_methods(['POST'])
def eventbridge_replays_cancel(request):
    return _action(request, 'cancel_replay', lambda b: cancel_replay(b.get('name', '')))
