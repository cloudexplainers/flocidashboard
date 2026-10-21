"""HTTP endpoints for the RDS database workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .rds_api import (
    create_db_cluster,
    create_db_instance,
    create_db_parameter_group,
    delete_db_cluster,
    delete_db_instance,
    delete_db_parameter_group,
    modify_db_instance,
    reboot_db_instance,
)


@require_http_methods(['POST'])
def rds_instances_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_db_instance(
            body.get('identifier', ''),
            body.get('engine', ''),
            body.get('username', ''),
            body.get('password', ''),
            db_instance_class=body.get('db_instance_class') or 'db.t3.micro',
            allocated_storage=body.get('allocated_storage', 20),
            db_name=body.get('db_name') or '',
            engine_version=body.get('engine_version') or '',
            enable_iam_auth=body.get('enable_iam_auth') is True,
            tags=body.get('tags'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='rds', operation='create_db_instance')


@require_http_methods(['PUT', 'DELETE'])
def rds_instance_detail(request, identifier: str):
    if request.method == 'DELETE':
        try:
            body = parse_json_body(request) if request.body else {}
            return JsonResponse(delete_db_instance(
                identifier,
                skip_final_snapshot=body.get('skip_final_snapshot') is not False,
                final_snapshot_identifier=body.get('final_snapshot_identifier') or '',
            ))
        except Exception as exc:
            return handle_action_error(exc, service='rds', operation='delete_db_instance')

    try:
        body = parse_json_body(request)
        return JsonResponse(modify_db_instance(
            identifier,
            db_instance_class=body.get('db_instance_class') or '',
            allocated_storage=body.get('allocated_storage'),
            master_user_password=body.get('master_user_password') or '',
            apply_immediately=body.get('apply_immediately') is not False,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='rds', operation='modify_db_instance')


@require_http_methods(['POST'])
def rds_instance_reboot(request, identifier: str):
    try:
        return JsonResponse(reboot_db_instance(identifier))
    except Exception as exc:
        return handle_action_error(exc, service='rds', operation='reboot_db_instance')


@require_http_methods(['POST'])
def rds_clusters_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_db_cluster(
            body.get('identifier', ''),
            body.get('engine', ''),
            body.get('username', ''),
            body.get('password', ''),
            database_name=body.get('database_name') or '',
            engine_version=body.get('engine_version') or '',
            enable_iam_auth=body.get('enable_iam_auth') is True,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='rds', operation='create_db_cluster')


@require_http_methods(['DELETE'])
def rds_cluster_delete(request, identifier: str):
    try:
        body = parse_json_body(request) if request.body else {}
        return JsonResponse(delete_db_cluster(
            identifier,
            skip_final_snapshot=body.get('skip_final_snapshot') is not False,
            final_snapshot_identifier=body.get('final_snapshot_identifier') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='rds', operation='delete_db_cluster')


@require_http_methods(['POST'])
def rds_parameter_groups_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_db_parameter_group(
            body.get('name', ''),
            body.get('family', ''),
            body.get('description', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='rds', operation='create_db_parameter_group')


@require_http_methods(['DELETE'])
def rds_parameter_group_delete(request, name: str):
    try:
        return JsonResponse(delete_db_parameter_group(name))
    except Exception as exc:
        return handle_action_error(exc, service='rds', operation='delete_db_parameter_group')
