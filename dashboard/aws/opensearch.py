"""OpenSearch inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from botocore import xform_name

from dashboard import aws as core
from dashboard.aws import _chunks, _clean_response, _safe_value


def opensearch_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    opensearch = factory.client('opensearch')

    operations = set(opensearch.meta.service_model.operation_names)

    def call_if_supported(operation_name: str, result_key: str, **kwargs) -> Any:
        if operation_name not in operations:
            return []

        operation = getattr(opensearch, xform_name(operation_name))
        return _safe_value(lambda: _clean_response(operation(**kwargs)).get(result_key, []), [])

    def paginate_if_supported(operation_name: str, result_key: str, **kwargs) -> list[Any]:
        if operation_name not in operations:
            return []

        return _safe_value(lambda: core._paginate(opensearch, xform_name(operation_name), result_key, **kwargs), [])

    domain_names = call_if_supported('ListDomainNames', 'DomainNames')
    names = [domain.get('DomainName') for domain in domain_names if domain.get('DomainName')]
    domain_statuses: list[dict[str, Any]] = []
    for chunk in _chunks(names, 5):
        domain_statuses.extend(
            _safe_value(lambda chunk=chunk: opensearch.describe_domains(DomainNames=chunk).get('DomainStatusList', []), [])
        )

    def domain_detail(status: dict[str, Any]) -> dict[str, Any]:
        name = status.get('DomainName')
        arn = status.get('ARN') or status.get('DomainArn')
        config = _safe_value(lambda name=name: _clean_response(opensearch.describe_domain_config(DomainName=name).get('DomainConfig', {})), {}) if name else {}
        health = _safe_value(lambda name=name: _clean_response(opensearch.describe_domain_health(DomainName=name)), {}) if name else {}
        nodes = _safe_value(lambda name=name: _clean_response(opensearch.describe_domain_nodes(DomainName=name).get('DomainNodesStatusList', [])), []) if name else []
        packages = paginate_if_supported('ListPackagesForDomain', 'DomainPackageDetailsList', DomainName=name) if name else []
        maintenances = paginate_if_supported('ListDomainMaintenances', 'DomainMaintenances', DomainName=name) if name else []
        scheduled_actions = paginate_if_supported('ListScheduledActions', 'ScheduledActions', DomainName=name) if name else []
        endpoint_access = paginate_if_supported('ListVpcEndpointAccess', 'AuthorizedPrincipalList', DomainName=name) if name else []
        data_sources = call_if_supported('ListDataSources', 'DataSources', DomainName=name) if name else []
        tags = _safe_value(lambda arn=arn: opensearch.list_tags(ARN=arn).get('TagList', []), []) if arn else []

        return {
            'name': name,
            'arn': arn,
            'engine_version': status.get('EngineVersion') or status.get('ElasticsearchVersion'),
            'created': status.get('Created'),
            'deleted': status.get('Deleted'),
            'processing': status.get('Processing'),
            'upgrade_processing': status.get('UpgradeProcessing'),
            'endpoint': status.get('Endpoint'),
            'endpoints': status.get('Endpoints'),
            'cluster_config': status.get('ClusterConfig'),
            'ebs_options': status.get('EBSOptions'),
            'vpc_options': status.get('VPCOptions'),
            'endpoint_options': status.get('DomainEndpointOptions'),
            'advanced_security_options': status.get('AdvancedSecurityOptions'),
            'encryption_at_rest_options': status.get('EncryptionAtRestOptions'),
            'node_to_node_encryption_options': status.get('NodeToNodeEncryptionOptions'),
            'service_software_options': status.get('ServiceSoftwareOptions'),
            'health': health,
            'config': config,
            'nodes': nodes,
            'package_count': len(packages),
            'maintenance_count': len(maintenances),
            'scheduled_action_count': len(scheduled_actions),
            'vpc_endpoint_principal_count': len(endpoint_access),
            'data_source_count': len(data_sources),
            'packages': packages,
            'maintenance': maintenances,
            'scheduled_actions': scheduled_actions,
            'vpc_endpoint_access': endpoint_access,
            'data_sources': data_sources,
            'tags': tags,
        }

    domains = [domain_detail(status) for status in domain_statuses]
    packages = paginate_if_supported('DescribePackages', 'PackageDetailsList')
    versions = paginate_if_supported('ListVersions', 'Versions')
    vpc_endpoint_summaries = paginate_if_supported('ListVpcEndpoints', 'VpcEndpointSummaryList')
    vpc_endpoint_ids = [endpoint.get('VpcEndpointId') for endpoint in vpc_endpoint_summaries if endpoint.get('VpcEndpointId')]
    vpc_endpoints: list[dict[str, Any]] = []
    for chunk in _chunks(vpc_endpoint_ids, 25):
        vpc_endpoints.extend(
            _safe_value(lambda chunk=chunk: _clean_response(opensearch.describe_vpc_endpoints(VpcEndpointIds=chunk).get('VpcEndpoints', [])), [])
        )
    if not vpc_endpoints:
        vpc_endpoints = vpc_endpoint_summaries

    inbound_connections = paginate_if_supported('DescribeInboundConnections', 'Connections')
    outbound_connections = paginate_if_supported('DescribeOutboundConnections', 'Connections')
    applications = call_if_supported('ListApplications', 'ApplicationSummaries')
    direct_query_data_sources = paginate_if_supported('ListDirectQueryDataSources', 'DirectQueryDataSources')
    nodes = [
        {
            'name': node.get('NodeId') or node.get('NodeName'),
            'domain_name': domain.get('name'),
            'node_id': node.get('NodeId'),
            'availability_zone': node.get('AvailabilityZone'),
            'instance_type': node.get('InstanceType'),
            'node_status': node.get('NodeStatus'),
            'storage_type': node.get('StorageType'),
            'storage_volume_type': node.get('StorageVolumeType'),
            'node_options': node,
        }
        for domain in domains
        for node in domain.get('nodes', [])
    ]

    return {
        'summary': {
            'domains': len(domains),
            'nodes': len(nodes),
            'packages': len(packages),
            'versions': len(versions),
            'vpc_endpoints': len(vpc_endpoints),
            'inbound_connections': len(inbound_connections),
            'outbound_connections': len(outbound_connections),
            'applications': len(applications),
            'direct_query_data_sources': len(direct_query_data_sources),
        },
        'domains': domains,
        'nodes': nodes,
        'packages': packages,
        'versions': versions,
        'vpc_endpoints': vpc_endpoints,
        'inbound_connections': inbound_connections,
        'outbound_connections': outbound_connections,
        'applications': applications,
        'direct_query_data_sources': direct_query_data_sources,
        'supported_from_sdk': [
            'CreateDomain',
            'DescribeDomain',
            'DescribeDomains',
            'DescribeDomainConfig',
            'DescribeDomainHealth',
            'DescribeDomainNodes',
            'ListDomainNames',
            'DeleteDomain',
            'CreatePackage',
            'DescribePackages',
            'ListPackagesForDomain',
            'AssociatePackage',
            'CreateVpcEndpoint',
            'ListVpcEndpoints',
            'DescribeVpcEndpoints',
            'CreateOutboundConnection',
            'DescribeInboundConnections',
            'DescribeOutboundConnections',
            'ListVersions',
            'ListTags',
        ],
        'notes': [
            'Endpoint is the container address Floci reports. From your machine, use the host port Floci publishes for the domain (9400-9499).',
            'Floci OpenSearch supports mock metadata mode and real container-backed domains exposed on ports 9400-9499.',
            'Supported interactive workflows include domain lifecycle, config updates, upgrades, tags, versions, and instance type limits.',
            'Real mode picks an OpenSearch or Elasticsearch image from the requested EngineVersion and marks the domain ready after cluster health is green or yellow.',
            'Access policies, VPC options, fine-grained access control and encryption settings are stored and returned, but not enforced.',
        ],
    }


def list_opensearch_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    opensearch = factory.client('opensearch')
    resources: list[dict[str, Any]] = []
    domain_names = _safe_value(lambda: opensearch.list_domain_names().get('DomainNames', []), [])
    names = [domain.get('DomainName') for domain in domain_names if domain.get('DomainName')]
    for chunk in _chunks(names, 5):
        resources.extend(
            {
                'type': 'domain',
                'name': domain.get('DomainName'),
                'arn': domain.get('ARN') or domain.get('DomainArn'),
                'engine_version': domain.get('EngineVersion') or domain.get('ElasticsearchVersion'),
                'processing': domain.get('Processing'),
                'endpoint': domain.get('Endpoint'),
            }
            for domain in _safe_value(lambda chunk=chunk: opensearch.describe_domains(DomainNames=chunk).get('DomainStatusList', []), [])
        )

    resources.extend(
        {
            'type': 'package',
            'name': package.get('PackageName'),
            'id': package.get('PackageID'),
            'status': package.get('PackageStatus'),
            'package_type': package.get('PackageType'),
        }
        for package in _safe_value(lambda: core._paginate(opensearch, 'describe_packages', 'PackageDetailsList'), [])
    )
    resources.extend(
        {
            'type': 'vpc_endpoint',
            'name': endpoint.get('VpcEndpointId'),
            'id': endpoint.get('VpcEndpointId'),
            'domain_arn': endpoint.get('DomainArn'),
            'status': endpoint.get('Status'),
        }
        for endpoint in _safe_value(lambda: core._paginate(opensearch, 'list_vpc_endpoints', 'VpcEndpointSummaryList'), [])
    )
    return resources
