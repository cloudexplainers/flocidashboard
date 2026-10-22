"""Interactive EKS helpers for local Kubernetes control plane workflows."""

from __future__ import annotations

import time
from typing import Any

from botocore.config import Config
from botocore.exceptions import ClientError, ConnectionClosedError

from .aws import FlociClientFactory, _clean_response


def _client(*, long_running: bool = False):
    factory = FlociClientFactory()
    if long_running:
        # Real EKS cluster operations can wait for a local k3s container to start/stop.
        # Do not retry a mutation that may already have succeeded after a read timeout.
        factory.config = factory.config.merge(Config(read_timeout=120, retries={'max_attempts': 0}))
    return factory.client('eks')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _list(value: Any, label: str) -> list[Any]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return value
    raise ValueError(f'{label} must be a list')


def _dict(value: Any, label: str) -> dict[str, Any]:
    if value in (None, ''):
        return {}
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    return value


def _int(value: Any, label: str) -> int | None:
    if value in (None, ''):
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a number') from exc


def _tags(value: Any) -> dict[str, str]:
    if value in (None, '', [], {}):
        return {}
    if isinstance(value, list):
        value = {
            item.get('key') or item.get('Key'): item.get('value') if item.get('value') is not None else item.get('Value')
            for item in value
            if isinstance(item, dict)
        }
    if not isinstance(value, dict):
        raise ValueError('Tags must be an object or list')

    tags: dict[str, str] = {}
    for key, item in value.items():
        clean_key = _required(key, 'Tag key')
        tags[clean_key] = '' if item is None else str(item)
    return tags


def _tag_keys(value: Any) -> list[str]:
    keys = [str(item).strip() for item in _list(value, 'Tag keys') if str(item).strip()]
    if not keys:
        raise ValueError('At least one tag key is required')
    return keys


def create_cluster(
    *,
    name: str,
    role_arn: str,
    version: str = '',
    subnet_ids: Any = None,
    security_group_ids: Any = None,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'name': _required(name, 'Cluster name'),
        'roleArn': _required(role_arn, 'Role ARN'),
        'resourcesVpcConfig': {
            'subnetIds': _list(subnet_ids, 'Subnet IDs'),
            'securityGroupIds': _list(security_group_ids, 'Security group IDs'),
        },
    }
    if version:
        kwargs['version'] = str(version).strip()
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['tags'] = clean_tags

    try:
        response = _client(long_running=True).create_cluster(**kwargs)
    except ConnectionClosedError:
        response = _find_created_cluster(kwargs['name'])
        if response is None:
            raise
    cluster = response.get('cluster', {})
    return {
        'name': cluster.get('name') or kwargs['name'],
        'arn': cluster.get('arn'),
        'status': cluster.get('status'),
        'endpoint': cluster.get('endpoint'),
        'version': cluster.get('version'),
        'response': _clean_response(response),
    }


def _find_created_cluster(name: str, attempts: int = 5) -> dict[str, Any] | None:
    # On Linux, Floci can drop the connection while it attaches its own container to the
    # cluster's new VPC network, even though the cluster was created. Look the cluster up
    # rather than resending the create.
    for attempt in range(attempts):
        try:
            return _client().describe_cluster(name=name)
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') != 'ResourceNotFoundException':
                raise
        if attempt + 1 < attempts:
            time.sleep(1)
    return None


def delete_cluster(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Cluster name')
    response = _client(long_running=True).delete_cluster(name=clean_name)
    cluster = response.get('cluster', {})
    return {
        'name': cluster.get('name') or clean_name,
        'arn': cluster.get('arn'),
        'status': cluster.get('status'),
        'response': _clean_response(response),
    }


def create_nodegroup(
    *,
    cluster_name: str,
    nodegroup_name: str,
    node_role: str,
    subnets: Any = None,
    scaling_config: Any = None,
    instance_types: Any = None,
    ami_type: str = '',
    capacity_type: str = '',
    disk_size: Any = None,
    labels: Any = None,
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'clusterName': _required(cluster_name, 'Cluster name'),
        'nodegroupName': _required(nodegroup_name, 'Node group name'),
        'nodeRole': _required(node_role, 'Node role ARN'),
        'subnets': _list(subnets, 'Subnets'),
    }
    if not kwargs['subnets']:
        raise ValueError('At least one subnet is required')

    clean_scaling = _dict(scaling_config, 'Scaling config')
    if clean_scaling:
        kwargs['scalingConfig'] = clean_scaling
    clean_instance_types = _list(instance_types, 'Instance types')
    if clean_instance_types:
        kwargs['instanceTypes'] = clean_instance_types
    if ami_type:
        kwargs['amiType'] = str(ami_type).strip()
    if capacity_type:
        kwargs['capacityType'] = str(capacity_type).strip().upper()
    clean_disk_size = _int(disk_size, 'Disk size')
    if clean_disk_size is not None:
        kwargs['diskSize'] = clean_disk_size
    clean_labels = _dict(labels, 'Labels')
    if clean_labels:
        kwargs['labels'] = {str(key): str(item) for key, item in clean_labels.items()}
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['tags'] = clean_tags

    response = _client().create_nodegroup(**kwargs)
    nodegroup = response.get('nodegroup', {})
    return {
        'cluster_name': kwargs['clusterName'],
        'nodegroup_name': nodegroup.get('nodegroupName') or kwargs['nodegroupName'],
        'arn': nodegroup.get('nodegroupArn'),
        'status': nodegroup.get('status'),
        'response': _clean_response(response),
    }


def delete_nodegroup(cluster_name: str, nodegroup_name: str) -> dict[str, Any]:
    clean_cluster = _required(cluster_name, 'Cluster name')
    clean_nodegroup = _required(nodegroup_name, 'Node group name')
    response = _client().delete_nodegroup(clusterName=clean_cluster, nodegroupName=clean_nodegroup)
    nodegroup = response.get('nodegroup', {})
    return {
        'cluster_name': clean_cluster,
        'nodegroup_name': nodegroup.get('nodegroupName') or clean_nodegroup,
        'arn': nodegroup.get('nodegroupArn'),
        'status': nodegroup.get('status'),
        'response': _clean_response(response),
    }


def create_fargate_profile(
    *,
    cluster_name: str,
    profile_name: str,
    pod_execution_role_arn: str,
    subnets: Any = None,
    selectors: Any = None,
    tags: Any = None,
) -> dict[str, Any]:
    clean_selectors = _list(selectors, 'Selectors')
    if not clean_selectors:
        raise ValueError('At least one selector is required')
    if not all(isinstance(selector, dict) for selector in clean_selectors):
        raise ValueError('Selectors must contain objects')

    kwargs = {
        'clusterName': _required(cluster_name, 'Cluster name'),
        'fargateProfileName': _required(profile_name, 'Fargate profile name'),
        'podExecutionRoleArn': _required(pod_execution_role_arn, 'Pod execution role ARN'),
        'subnets': _list(subnets, 'Subnets'),
        'selectors': clean_selectors,
    }
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['tags'] = clean_tags

    response = _client().create_fargate_profile(**kwargs)
    profile = response.get('fargateProfile', {})
    return {
        'cluster_name': kwargs['clusterName'],
        'fargate_profile_name': profile.get('fargateProfileName') or kwargs['fargateProfileName'],
        'arn': profile.get('fargateProfileArn'),
        'status': profile.get('status'),
        'response': _clean_response(response),
    }


def delete_fargate_profile(cluster_name: str, profile_name: str) -> dict[str, Any]:
    clean_cluster = _required(cluster_name, 'Cluster name')
    clean_profile = _required(profile_name, 'Fargate profile name')
    response = _client().delete_fargate_profile(
        clusterName=clean_cluster,
        fargateProfileName=clean_profile,
    )
    profile = response.get('fargateProfile', {})
    return {
        'cluster_name': clean_cluster,
        'fargate_profile_name': profile.get('fargateProfileName') or clean_profile,
        'arn': profile.get('fargateProfileArn'),
        'status': profile.get('status'),
        'response': _clean_response(response),
    }


def tag_resource(resource_arn: str, tags: Any) -> dict[str, Any]:
    clean_arn = _required(resource_arn, 'Resource ARN')
    clean_tags = _tags(tags)
    if not clean_tags:
        raise ValueError('At least one tag is required')
    response = _client().tag_resource(resourceArn=clean_arn, tags=clean_tags)
    return {
        'resource_arn': clean_arn,
        'tags': clean_tags,
        'response': _clean_response(response),
    }


def untag_resource(resource_arn: str, tag_keys: Any) -> dict[str, Any]:
    clean_arn = _required(resource_arn, 'Resource ARN')
    clean_keys = _tag_keys(tag_keys)
    response = _client().untag_resource(resourceArn=clean_arn, tagKeys=clean_keys)
    return {
        'resource_arn': clean_arn,
        'tag_keys': clean_keys,
        'response': _clean_response(response),
    }


def list_tags(resource_arn: str) -> dict[str, Any]:
    clean_arn = _required(resource_arn, 'Resource ARN')
    response = _client().list_tags_for_resource(resourceArn=clean_arn)
    return {
        'resource_arn': clean_arn,
        'tags': response.get('tags', {}),
        'response': _clean_response(response),
    }


def get_kubeconfig(cluster_name: str, *, region: str = 'us-east-1') -> dict[str, Any]:
    clean_name = _required(cluster_name, 'Cluster name')
    endpoint_url = FlociClientFactory().endpoint_url

    try:
        response = _client().describe_cluster(name=clean_name)
        cluster = response.get('cluster', {})
    except Exception:
        cluster = {}

    server_endpoint = cluster.get('endpoint') or f'{endpoint_url}/eks/{clean_name}'
    ca_data = cluster.get('certificateAuthority', {}).get('data') or 'LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCg=='
    arn = cluster.get('arn') or f'arn:aws:eks:{region}:000000000000:cluster/{clean_name}'

    kubeconfig_yaml = f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {ca_data}
    server: {server_endpoint}
  name: {arn}
contexts:
- context:
    cluster: {arn}
    user: {arn}
  name: {arn}
current-context: {arn}
kind: Config
preferences: {{}}
users:
- name: {arn}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      args:
      - --region
      - {region}
      - eks
      - get-token
      - --cluster-name
      - {clean_name}
      command: aws
"""

    return {
        'cluster_name': clean_name,
        'cluster_arn': arn,
        'endpoint': server_endpoint,
        'aws_cli_command': f'aws eks update-kubeconfig --name {clean_name} --region {region}',
        'kubeconfig_yaml': kubeconfig_yaml,
    }

