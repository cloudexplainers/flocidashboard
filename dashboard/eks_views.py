"""HTTP endpoints for the EKS cluster workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .eks_api import (
    create_cluster,
    create_fargate_profile,
    create_nodegroup,
    delete_cluster,
    delete_fargate_profile,
    delete_nodegroup,
    get_kubeconfig,
    list_tags,
    tag_resource,
    untag_resource,
)


@require_http_methods(['POST'])
def eks_clusters_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_cluster(
            name=body.get('name') or '',
            role_arn=body.get('role_arn') or '',
            version=body.get('version') or '',
            subnet_ids=body.get('subnet_ids') or [],
            security_group_ids=body.get('security_group_ids') or [],
            tags=body.get('tags') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='create_cluster')


@require_http_methods(['POST'])
def eks_clusters_delete(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(delete_cluster(body.get('name') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='delete_cluster')


@require_http_methods(['POST'])
def eks_nodegroups_create(request, cluster_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_nodegroup(
            cluster_name=cluster_name,
            nodegroup_name=body.get('nodegroup_name') or '',
            node_role=body.get('node_role') or '',
            subnets=body.get('subnets') or [],
            scaling_config=body.get('scaling_config') or {},
            instance_types=body.get('instance_types') or [],
            ami_type=body.get('ami_type') or '',
            capacity_type=body.get('capacity_type') or '',
            disk_size=body.get('disk_size'),
            labels=body.get('labels') or {},
            tags=body.get('tags') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='create_nodegroup')


@require_http_methods(['DELETE'])
def eks_nodegroup_delete(request, cluster_name: str, nodegroup_name: str):
    try:
        return JsonResponse(delete_nodegroup(cluster_name, nodegroup_name))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='delete_nodegroup')


@require_http_methods(['POST'])
def eks_fargate_profiles_create(request, cluster_name: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_fargate_profile(
            cluster_name=cluster_name,
            profile_name=body.get('profile_name') or '',
            pod_execution_role_arn=body.get('pod_execution_role_arn') or '',
            subnets=body.get('subnets') or [],
            selectors=body.get('selectors') or [],
            tags=body.get('tags') or {},
        ))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='create_fargate_profile')


@require_http_methods(['DELETE'])
def eks_fargate_profile_delete(request, cluster_name: str, profile_name: str):
    try:
        return JsonResponse(delete_fargate_profile(cluster_name, profile_name))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='delete_fargate_profile')


@require_http_methods(['POST', 'DELETE'])
def eks_tags(request):
    try:
        body = parse_json_body(request)
        if request.method == 'POST':
            return JsonResponse(tag_resource(body.get('resource_arn') or '', body.get('tags') or {}))
        return JsonResponse(untag_resource(body.get('resource_arn') or '', body.get('tag_keys') or []))
    except Exception as exc:
        operation = 'tag_resource' if request.method == 'POST' else 'untag_resource'
        return handle_action_error(exc, service='eks', operation=operation)


@require_http_methods(['POST'])
def eks_tags_list(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(list_tags(body.get('resource_arn') or ''))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='list_tags_for_resource')


@require_http_methods(['GET', 'POST'])
def eks_cluster_kubeconfig(request, cluster_name: str = ''):
    try:
        if not cluster_name:
            body = parse_json_body(request) if request.method == 'POST' else {}
            cluster_name = body.get('cluster_name') or body.get('name') or request.GET.get('cluster_name') or ''
        region = request.GET.get('region') or None
        return JsonResponse(get_kubeconfig(cluster_name, region=region))
    except Exception as exc:
        return handle_action_error(exc, service='eks', operation='get_kubeconfig')
