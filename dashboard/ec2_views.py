"""HTTP endpoints for the EC2 instance workbench."""

from __future__ import annotations

from django.http import JsonResponse
from django.views.decorators.http import require_http_methods

from .actions import handle_action_error, parse_json_body
from .ec2_api import (
    allocate_elastic_ip,
    cancel_spot_request,
    change_route,
    change_route_table_association,
    change_security_group_rule,
    create_internet_gateway,
    create_flow_log,
    create_launch_template,
    create_launch_template_version,
    create_nat_gateway,
    create_network_acl,
    create_route_table,
    create_security_group,
    create_subnet,
    create_vpc,
    create_vpc_endpoint,
    create_volume,
    delete_flow_log,
    delete_internet_gateway,
    delete_nat_gateway,
    delete_network_acl,
    delete_network_acl_entry,
    delete_route_table,
    delete_security_group,
    delete_subnet,
    delete_vpc,
    delete_vpc_endpoint,
    delete_volume,
    delete_launch_template,
    instance_command_detail,
    import_key_pair,
    list_key_pairs,
    create_key_pair,
    delete_key_pair,
    list_instance_commands,
    normalize_security_group_ids,
    reboot_instance,
    run_instance_command,
    run_instances,
    release_elastic_ip,
    register_image,
    replace_network_acl_association,
    request_spot_instances,
    set_elastic_ip_association,
    set_internet_gateway_attachment,
    set_launch_template_default_version,
    start_instance,
    stop_instance,
    terminate_instance,
    update_instance_tags,
    put_network_acl_entry,
    view_flow_log,
)


@require_http_methods(['POST'])
def ec2_instances_run(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(run_instances(
            body.get('image_id', ''),
            body.get('instance_type', ''),
            subnet_id=body.get('subnet_id') or None,
            security_group_ids=normalize_security_group_ids(body.get('security_group_ids')),
            key_name=body.get('key_name') or None,
            user_data=body.get('user_data') or None,
            iam_instance_profile_arn=body.get('iam_instance_profile_arn') or None,
            tags=body.get('tags') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='run_instances')


@require_http_methods(['POST'])
def ec2_instance_start(request, instance_id: str):
    try:
        return JsonResponse(start_instance(instance_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='start_instance')


@require_http_methods(['POST'])
def ec2_instance_stop(request, instance_id: str):
    try:
        return JsonResponse(stop_instance(instance_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='stop_instance')


@require_http_methods(['POST'])
def ec2_instance_reboot(request, instance_id: str):
    try:
        return JsonResponse(reboot_instance(instance_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='reboot_instance')


@require_http_methods(['POST'])
def ec2_instance_terminate(request, instance_id: str):
    try:
        return JsonResponse(terminate_instance(instance_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='terminate_instance')


@require_http_methods(['POST'])
def ec2_key_pairs_import(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(import_key_pair(
            body.get('key_name', ''),
            body.get('public_key_material', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='import_key_pair')


@require_http_methods(['GET', 'POST', 'DELETE'])
def ec2_key_pairs(request):
    operation = {'GET': 'describe_key_pairs', 'POST': 'create_key_pair', 'DELETE': 'delete_key_pair'}[request.method]
    try:
        if request.method == 'GET':
            return JsonResponse(list_key_pairs())
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(delete_key_pair(body.get('key_name', '')))
        return JsonResponse(create_key_pair(body.get('key_name', ''), body.get('key_type') or 'rsa'))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['PUT'])
def ec2_instance_tags(request, instance_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(update_instance_tags(instance_id, body.get('tags', {})))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='update_instance_tags')


@require_http_methods(['GET', 'POST'])
def ec2_instance_commands(request, instance_id: str):
    try:
        if request.method == 'GET':
            return JsonResponse(list_instance_commands(instance_id))
        body = parse_json_body(request)
        return JsonResponse(run_instance_command(
            instance_id,
            body.get('command', ''),
            timeout_seconds=body.get('timeout_seconds', 3600),
        ))
    except Exception as exc:
        operation = 'list_instance_commands' if request.method == 'GET' else 'run_instance_command'
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['GET'])
def ec2_instance_command_detail(request, instance_id: str, command_id: str):
    try:
        return JsonResponse(instance_command_detail(instance_id, command_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='instance_command_detail')


@require_http_methods(['POST'])
def ec2_vpcs_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_vpc(body.get('cidr_block', ''), name=body.get('name') or None))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_vpc')


@require_http_methods(['DELETE'])
def ec2_vpc_delete(request, vpc_id: str):
    try:
        return JsonResponse(delete_vpc(vpc_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_vpc')


@require_http_methods(['POST'])
def ec2_subnets_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_subnet(
            body.get('vpc_id', ''),
            body.get('cidr_block', ''),
            availability_zone=body.get('availability_zone') or None,
            name=body.get('name') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_subnet')


@require_http_methods(['DELETE'])
def ec2_subnet_delete(request, subnet_id: str):
    try:
        return JsonResponse(delete_subnet(subnet_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_subnet')


@require_http_methods(['POST'])
def ec2_security_groups_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_security_group(body.get('name', ''), body.get('description', ''), body.get('vpc_id', '')))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_security_group')


@require_http_methods(['DELETE'])
def ec2_security_group_delete(request, group_id: str):
    try:
        return JsonResponse(delete_security_group(group_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_security_group')


@require_http_methods(['POST', 'DELETE'])
def ec2_security_group_rules(request, group_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(change_security_group_rule(
            group_id,
            body.get('direction', ''),
            body.get('rule', {}),
            revoke=request.method == 'DELETE',
        ))
    except Exception as exc:
        operation = 'revoke_security_group_rule' if request.method == 'DELETE' else 'authorize_security_group_rule'
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['POST'])
def ec2_internet_gateways_create(request):
    try:
        return JsonResponse(create_internet_gateway())
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_internet_gateway')


@require_http_methods(['DELETE'])
def ec2_internet_gateway_delete(request, gateway_id: str):
    try:
        return JsonResponse(delete_internet_gateway(gateway_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_internet_gateway')


@require_http_methods(['PUT', 'DELETE'])
def ec2_internet_gateway_attachment(request, gateway_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(set_internet_gateway_attachment(
            gateway_id,
            body.get('vpc_id', ''),
            detach=request.method == 'DELETE',
        ))
    except Exception as exc:
        operation = 'detach_internet_gateway' if request.method == 'DELETE' else 'attach_internet_gateway'
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['POST'])
def ec2_route_tables_create(request):
    try:
        return JsonResponse(create_route_table(parse_json_body(request).get('vpc_id', '')))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_route_table')


@require_http_methods(['DELETE'])
def ec2_route_table_delete(request, route_table_id: str):
    try:
        return JsonResponse(delete_route_table(route_table_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_route_table')


@require_http_methods(['POST', 'DELETE'])
def ec2_route_table_associations(request, route_table_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(change_route_table_association(
            route_table_id,
            subnet_id=body.get('subnet_id'),
            association_id=body.get('association_id'),
            disassociate=request.method == 'DELETE',
        ))
    except Exception as exc:
        operation = 'disassociate_route_table' if request.method == 'DELETE' else 'associate_route_table'
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['POST', 'DELETE'])
def ec2_routes(request, route_table_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(change_route(
            route_table_id,
            body.get('destination_cidr', ''),
            body.get('gateway_id'),
            delete=request.method == 'DELETE',
        ))
    except Exception as exc:
        operation = 'delete_route' if request.method == 'DELETE' else 'create_route'
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['POST'])
def ec2_elastic_ips_allocate(request):
    try:
        body = parse_json_body(request) if request.body and request.content_type == 'application/json' else {}
        return JsonResponse(allocate_elastic_ip(name=body.get('name'), tags=body.get('tags')))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='allocate_elastic_ip')


@require_http_methods(['DELETE'])
def ec2_elastic_ip_release(request, allocation_id: str):
    try:
        return JsonResponse(release_elastic_ip(allocation_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='release_elastic_ip')


@require_http_methods(['PUT', 'DELETE'])
def ec2_elastic_ip_association(request, allocation_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(set_elastic_ip_association(
            allocation_id,
            instance_id=body.get('instance_id'),
            association_id=body.get('association_id'),
            disassociate=request.method == 'DELETE',
            allow_reassociation=bool(body.get('allow_reassociation')),
        ))
    except Exception as exc:
        operation = 'disassociate_elastic_ip' if request.method == 'DELETE' else 'associate_elastic_ip'
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['POST'])
def ec2_nat_gateways_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_nat_gateway(
            body.get('subnet_id', ''),
            body.get('allocation_id') or None,
            connectivity_type=body.get('connectivity_type') or 'public',
            name=body.get('name'),
            tags=body.get('tags'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_nat_gateway')


@require_http_methods(['DELETE'])
def ec2_nat_gateway_delete(request, nat_gateway_id: str):
    try:
        return JsonResponse(delete_nat_gateway(nat_gateway_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_nat_gateway')


@require_http_methods(['POST'])
def ec2_vpc_endpoints_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_vpc_endpoint(
            body.get('vpc_id', ''),
            body.get('service_name', ''),
            body.get('endpoint_type') or 'Gateway',
            route_table_ids=body.get('route_table_ids'),
            subnet_ids=body.get('subnet_ids'),
            security_group_ids=body.get('security_group_ids'),
            private_dns_enabled=body.get('private_dns_enabled', False),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_vpc_endpoint')


@require_http_methods(['DELETE'])
def ec2_vpc_endpoint_delete(request, endpoint_id: str):
    try:
        return JsonResponse(delete_vpc_endpoint(endpoint_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_vpc_endpoint')


@require_http_methods(['POST'])
def ec2_network_acls_create(request):
    try:
        return JsonResponse(create_network_acl(parse_json_body(request).get('vpc_id', '')))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_network_acl')


@require_http_methods(['DELETE'])
def ec2_network_acl_delete(request, network_acl_id: str):
    try:
        return JsonResponse(delete_network_acl(network_acl_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_network_acl')


@require_http_methods(['PUT', 'DELETE'])
def ec2_network_acl_entries(request, network_acl_id: str):
    try:
        body = parse_json_body(request)
        if request.method == 'DELETE':
            return JsonResponse(delete_network_acl_entry(
                network_acl_id,
                body.get('rule_number'),
                egress=body.get('egress', False),
            ))
        return JsonResponse(put_network_acl_entry(
            network_acl_id,
            body.get('entry', {}),
            replace=body.get('replace', False),
        ))
    except Exception as exc:
        operation = 'delete_network_acl_entry' if request.method == 'DELETE' else 'put_network_acl_entry'
        return handle_action_error(exc, service='ec2', operation=operation)


@require_http_methods(['PUT'])
def ec2_network_acl_associations(request, network_acl_id: str):
    try:
        return JsonResponse(replace_network_acl_association(
            network_acl_id,
            parse_json_body(request).get('association_id', ''),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='replace_network_acl_association')


@require_http_methods(['POST'])
def ec2_flow_logs_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_flow_log(
            body.get('resource_id', ''),
            body.get('resource_type') or 'VPC',
            body.get('traffic_type') or 'ALL',
            body.get('destination', ''),
            max_aggregation_interval=body.get('max_aggregation_interval', 60),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_flow_log')


@require_http_methods(['DELETE'])
def ec2_flow_log_delete(request, flow_log_id: str):
    try:
        return JsonResponse(delete_flow_log(flow_log_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_flow_log')


@require_http_methods(['GET'])
def ec2_flow_log_view(request, flow_log_id: str):
    try:
        return JsonResponse(view_flow_log(flow_log_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='view_flow_log')


@require_http_methods(['POST'])
def ec2_volumes_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_volume(
            body.get('availability_zone', ''),
            body.get('size', 8),
            body.get('volume_type') or 'gp3',
            encrypted=body.get('encrypted', False),
            iops=body.get('iops'),
            throughput=body.get('throughput'),
            snapshot_id=body.get('snapshot_id') or None,
            tags=body.get('tags'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_volume')


@require_http_methods(['DELETE'])
def ec2_volume_delete(request, volume_id: str):
    try:
        return JsonResponse(delete_volume(volume_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_volume')


@require_http_methods(['POST'])
def ec2_images_register(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(register_image(
            body.get('name', ''),
            body.get('description', ''),
            body.get('architecture') or 'x86_64',
            body.get('root_device_name') or '/dev/xvda',
            body.get('block_device_mappings'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='register_image')


@require_http_methods(['POST'])
def ec2_launch_templates_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_launch_template(body.get('name', ''), body.get('data', {})))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_launch_template')


@require_http_methods(['DELETE'])
def ec2_launch_template_delete(request, launch_template_id: str):
    try:
        return JsonResponse(delete_launch_template(launch_template_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='delete_launch_template')


@require_http_methods(['POST'])
def ec2_launch_template_versions_create(request, launch_template_id: str):
    try:
        body = parse_json_body(request)
        return JsonResponse(create_launch_template_version(
            launch_template_id,
            body.get('data', {}),
            source_version=body.get('source_version') or None,
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='create_launch_template_version')


@require_http_methods(['PUT'])
def ec2_launch_template_default_version(request, launch_template_id: str):
    try:
        return JsonResponse(set_launch_template_default_version(
            launch_template_id,
            parse_json_body(request).get('version'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='set_launch_template_default_version')


@require_http_methods(['POST'])
def ec2_spot_requests_create(request):
    try:
        body = parse_json_body(request)
        return JsonResponse(request_spot_instances(
            body.get('image_id', ''),
            body.get('instance_type', ''),
            spot_price=body.get('spot_price') or None,
            instance_count=body.get('instance_count', 1),
            subnet_id=body.get('subnet_id') or None,
            security_group_ids=body.get('security_group_ids'),
            tags=body.get('tags'),
        ))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='request_spot_instances')


@require_http_methods(['POST'])
def ec2_spot_request_cancel(request, spot_request_id: str):
    try:
        return JsonResponse(cancel_spot_request(spot_request_id))
    except Exception as exc:
        return handle_action_error(exc, service='ec2', operation='cancel_spot_request')
