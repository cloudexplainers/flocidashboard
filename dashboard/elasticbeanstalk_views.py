"""HTTP endpoints for the Elastic Beanstalk workbench."""

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .elasticbeanstalk_api import (
    check_dns_availability,
    create_application,
    create_application_version,
    create_environment,
    delete_application,
    delete_application_version,
    terminate_environment,
    update_application,
    update_environment,
)


@require_http_methods(['POST'])
def applications_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_application(body.get('name', ''), description=body.get('description') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='elasticbeanstalk', operation='create_application')


@require_http_methods(['PATCH', 'DELETE'])
def application_detail(request, application_name: str):
    try:
        body = parse_json_body(request) if request.body else {}
        if request.method == 'DELETE':
            return JsonResponse(
                delete_application(application_name, terminate_environments=body.get('terminate_environments') is True)
            )
        return JsonResponse(update_application(application_name, description=body.get('description') or ''))
    except Exception as exc:
        return handle_action_error(
            exc,
            service='elasticbeanstalk',
            operation='delete_application' if request.method == 'DELETE' else 'update_application',
        )


@require_http_methods(['POST'])
def versions_create(request, application_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(
            create_application_version(
                application_name,
                body.get('version_label', ''),
                description=body.get('description') or '',
                s3_bucket=body.get('s3_bucket') or '',
                s3_key=body.get('s3_key') or '',
            )
        )
    except Exception as exc:
        return handle_action_error(exc, service='elasticbeanstalk', operation='create_application_version')


@require_http_methods(['DELETE'])
def version_delete(request, application_name: str, version_label: str):
    try:
        return JsonResponse(delete_application_version(application_name, version_label))
    except Exception as exc:
        return handle_action_error(exc, service='elasticbeanstalk', operation='delete_application_version')


@require_http_methods(['POST'])
def environments_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(
            create_environment(
                body.get('application_name', ''),
                body.get('environment_name', ''),
                description=body.get('description') or '',
                version_label=body.get('version_label') or '',
                cname_prefix=body.get('cname_prefix') or '',
                solution_stack_name=body.get('solution_stack_name') or '',
                platform_arn=body.get('platform_arn') or '',
                option_settings=body.get('option_settings') or [],
            )
        )
    except Exception as exc:
        return handle_action_error(exc, service='elasticbeanstalk', operation='create_environment')


@require_http_methods(['PATCH'])
def environment_detail(request, environment_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(
            update_environment(
                environment_name,
                description=body.get('description'),
                version_label=body.get('version_label') or '',
                solution_stack_name=body.get('solution_stack_name') or '',
                platform_arn=body.get('platform_arn') or '',
                option_settings=body.get('option_settings') or [],
            )
        )
    except Exception as exc:
        return handle_action_error(exc, service='elasticbeanstalk', operation='update_environment')


@require_http_methods(['POST'])
def environment_terminate(request, environment_name: str):
    try:
        return JsonResponse(terminate_environment(environment_name))
    except Exception as exc:
        return handle_action_error(exc, service='elasticbeanstalk', operation='terminate_environment')


@require_http_methods(['POST'])
def dns_check(request):
    try:
        return JsonResponse(check_dns_availability(parse_json_body(request).get('cname_prefix', '')))
    except Exception as exc:
        return handle_action_error(exc, service='elasticbeanstalk', operation='check_dns_availability')
