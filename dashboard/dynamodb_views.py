"""HTTP endpoints for the DynamoDB table explorer."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .dynamodb_api import (
    export_table_to_s3,
    list_table_exports,
    create_table,
    delete_item,
    delete_table,
    describe_continuous_backups,
    update_point_in_time_recovery,
    update_table_capacity,
    describe_time_to_live,
    execute_select_statement,
    get_item,
    parse_start_key,
    put_item,
    query_table,
    scan_table,
    update_time_to_live,
)


@require_http_methods(['POST'])
def dynamodb_table_scan(request, table_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(scan_table(
            table_name,
            limit=body.get('limit') or 25,
            exclusive_start_key=parse_start_key(body.get('exclusive_start_key')),
            filter_expression=body.get('filter_expression') or None,
            expression_attribute_names=body.get('expression_attribute_names') or None,
            expression_attribute_values=body.get('expression_attribute_values') or None,
            index_name=body.get('index_name') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='scan_table')


@require_http_methods(['POST'])
def dynamodb_table_query(request, table_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(query_table(
            table_name,
            key_condition_expression=body.get('key_condition_expression', ''),
            expression_attribute_values=body.get('expression_attribute_values', {}),
            expression_attribute_names=body.get('expression_attribute_names') or None,
            filter_expression=body.get('filter_expression') or None,
            index_name=body.get('index_name') or None,
            limit=body.get('limit') or 25,
            scan_index_forward=body.get('scan_index_forward', True),
            exclusive_start_key=parse_start_key(body.get('exclusive_start_key')),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='query_table')


@require_http_methods(['POST'])
def dynamodb_item_put(request, table_name: str):
    try:
        body = parse_json_body(request)
        item = body.get('item') or body
        # If wrapped inside {'item': {...}}, extract it
        if isinstance(body.get('item'), dict):
            item = body['item']
        return JsonResponse(put_item(table_name, item, return_values=body.get('return_values', 'NONE')))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='put_item')


@require_http_methods(['POST'])
def dynamodb_item_get(request, table_name: str):
    try:
        body = parse_json_body(request)
        key = body.get('key') or body
        return JsonResponse(get_item(
            table_name,
            key,
            consistent_read=body.get('consistent_read', False),
            projection_expression=body.get('projection_expression') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='get_item')


@require_http_methods(['POST', 'DELETE'])
def dynamodb_item_delete(request, table_name: str):
    try:
        body = parse_json_body(request)
        key = body.get('key') or body
        return JsonResponse(delete_item(table_name, key, return_values=body.get('return_values', 'NONE')))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='delete_item')


@require_http_methods(['GET', 'POST', 'PUT'])
def dynamodb_ttl(request, table_name: str):
    try:
        if request.method == 'GET':
            return JsonResponse(describe_time_to_live(table_name))
        body = parse_json_body(request)
        return JsonResponse(update_time_to_live(
            table_name,
            attribute_name=body.get('attribute_name', ''),
            enabled=body.get('enabled', True),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='update_time_to_live')


@require_http_methods(['POST'])
def dynamodb_partiql_execute(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(execute_select_statement(
            body.get('statement', ''),
            limit=body.get('limit') or 25,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='execute_select_statement')



@require_http_methods(['POST'])
def dynamodb_tables_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_table(
            body.get('table_name', ''),
            partition_key=body.get('partition_key') or {},
            sort_key=body.get('sort_key') or None,
            billing_mode=body.get('billing_mode') or 'PAY_PER_REQUEST',
            read_capacity_units=body.get('read_capacity_units'),
            write_capacity_units=body.get('write_capacity_units'),
            deletion_protection=body.get('deletion_protection') is True,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='create_table')


@require_http_methods(['PATCH', 'DELETE'])
def dynamodb_table_detail(request, table_name: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_table(table_name))
        body = parse_json_body(request)
        return JsonResponse(update_table_capacity(
            table_name,
            billing_mode=body.get('billing_mode', ''),
            read_capacity_units=body.get('read_capacity_units'),
            write_capacity_units=body.get('write_capacity_units'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='delete_table' if request.method == 'DELETE' else 'update_table')


@require_http_methods(['GET', 'PUT'])
def dynamodb_table_backups(request, table_name: str):
    try:
        if request.method == 'GET':
            return JsonResponse(describe_continuous_backups(table_name))
        body = parse_json_body(request)
        return JsonResponse(update_point_in_time_recovery(table_name, enabled=body.get('enabled') is True))
    except Exception as exc:
        operation = 'describe_continuous_backups' if request.method == 'GET' else 'update_continuous_backups'
        return handle_action_error(exc, service='dynamodb', operation=operation)


@require_http_methods(['GET', 'POST'])
def dynamodb_table_exports(request, table_name: str):
    try:
        if request.method == 'GET':
            return JsonResponse(list_table_exports(table_name))
        body = parse_json_body(request)
        return JsonResponse(export_table_to_s3(table_name, body.get('s3_bucket', ''), s3_prefix=body.get('s3_prefix') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='dynamodb', operation='list_exports' if request.method == 'GET' else 'export_table_to_point_in_time')
