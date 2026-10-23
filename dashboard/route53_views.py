"""HTTP endpoints for the Route 53 workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .route53_api import (
    associate_vpc,
    disassociate_vpc,
    list_vpc_choices,
    change_record_set,
    change_tags,
    create_health_check,
    create_hosted_zone,
    delete_health_check,
    delete_hosted_zone,
    update_health_check,
)


@require_http_methods(['POST'])
def route53_hosted_zones_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_hosted_zone(
            body.get('name', ''),
            body.get('caller_reference', ''),
            comment=body.get('comment', ''),
            vpc_id=body.get('vpc_id') or '',
            vpc_region=body.get('vpc_region') or '',
        ))
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation='create_hosted_zone')


@require_http_methods(['DELETE'])
def route53_hosted_zone_delete(request, zone_id: str):
    try:
        return JsonResponse(delete_hosted_zone(zone_id))
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation='delete_hosted_zone')


@require_http_methods(['POST'])
def route53_record_sets_change(request, zone_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(change_record_set(
            zone_id,
            body.get('action', 'UPSERT'),
            body.get('name', ''),
            body.get('type', ''),
            ttl=body.get('ttl', 300),
            values=body.get('values'),
            comment=body.get('comment', ''),
            record_set=body.get('record_set'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation='change_record_set')


@require_http_methods(['POST'])
def route53_health_checks_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_health_check(
            body.get('caller_reference', ''),
            body.get('type', ''),
            domain_name=body.get('domain_name', ''),
            ip_address=body.get('ip_address', ''),
            port=body.get('port'),
            resource_path=body.get('resource_path', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation='create_health_check')


@require_http_methods(['PUT', 'DELETE'])
def route53_health_check_detail(request, health_check_id: str):
    if request.method == 'DELETE':
        try:
            return JsonResponse(delete_health_check(health_check_id))
        except Exception as exc:
            return handle_action_error(exc, service='route53', operation='delete_health_check')

    try:
        body = parse_json_body(request)
        return JsonResponse(update_health_check(
            health_check_id,
            domain_name=body.get('domain_name', ''),
            ip_address=body.get('ip_address', ''),
            port=body.get('port'),
            resource_path=body.get('resource_path', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation='update_health_check')


@require_http_methods(['POST'])
def route53_tags_change(request, resource_type: str, resource_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(change_tags(
            resource_type,
            resource_id,
            add_tags=body.get('add_tags'),
            remove_tag_keys=body.get('remove_tag_keys'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation='change_tags')


@require_http_methods(['GET'])
def route53_vpc_choices(request):
    try:
        return JsonResponse(list_vpc_choices())
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation='describe_vpcs')


@require_http_methods(['POST', 'DELETE'])
def route53_hosted_zone_vpcs(request, zone_id: str):
    operation = 'disassociate_vpc_from_hosted_zone' if request.method == 'DELETE' else 'associate_vpc_with_hosted_zone'
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(disassociate_vpc(zone_id, body.get('vpc_id', ''), body.get('vpc_region') or ''))
        return JsonResponse(associate_vpc(zone_id, body.get('vpc_id', ''), body.get('vpc_region') or '', body.get('comment') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='route53', operation=operation)
