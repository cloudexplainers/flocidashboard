"""HTTP endpoints for the Floci CloudTrail workbench."""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .cloudtrail_api import create_trail, delete_trail, set_trail_logging, update_trail


@require_http_methods(['POST'])
def cloudtrail_trails_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_trail(body.get('name', ''), body.get('s3_bucket_name', ''), include_global_service_events=body.get('include_global_service_events', False), is_multi_region_trail=body.get('is_multi_region_trail', False), is_organization_trail=body.get('is_organization_trail', False)))
    except Exception as exc:
        return handle_action_error(exc, service='cloudtrail', operation='create_trail')


@require_http_methods(['PATCH', 'DELETE'])
def cloudtrail_trail_detail(request, trail_name: str):
    try:
        if request.method == 'DELETE':
            return JsonResponse(delete_trail(trail_name))
        body = parse_json_body(request)
        return JsonResponse(update_trail(trail_name, s3_bucket_name=body.get('s3_bucket_name') or '', include_global_service_events=body.get('include_global_service_events'), is_multi_region_trail=body.get('is_multi_region_trail')))
    except Exception as exc:
        operation = 'delete_trail' if request.method == 'DELETE' else 'update_trail'
        return handle_action_error(exc, service='cloudtrail', operation=operation)


@require_http_methods(['POST'])
def cloudtrail_trail_logging(request, trail_name: str):
    try:
        body = parse_json_body(request)
        if not isinstance(body.get('enabled'), bool):
            raise ValueError('Enabled must be true or false')
        return JsonResponse(set_trail_logging(trail_name, body['enabled']))
    except Exception as exc:
        return handle_action_error(exc, service='cloudtrail', operation='set_trail_logging')


@require_http_methods(['GET', 'PUT'])
def cloudtrail_event_selectors(request, trail_name: str):
    from .cloudtrail_api import event_selectors
    try:
        return JsonResponse(event_selectors(trail_name, parse_json_body(request) if request.method == 'PUT' else None))
    except Exception as exc:
        return handle_action_error(exc, service='cloudtrail', operation='put_event_selectors' if request.method == 'PUT' else 'get_event_selectors')


@require_http_methods(['GET'])
def cloudtrail_events(request):
    from .cloudtrail_api import lookup_events
    try:
        return JsonResponse(lookup_events(attribute=request.GET.get('attribute', ''), value=request.GET.get('value', ''), next_token=request.GET.get('next_token', '')))
    except Exception as exc:
        return handle_action_error(exc, service='cloudtrail', operation='lookup_events')
