"""EKS inventory and resource-list loaders."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _eks_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def eks_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    eks = factory.client('eks')
    cluster_names = _safe_value(lambda: core._paginate(eks, 'list_clusters', 'clusters'), [])

    def access_entries(cluster_name: str) -> list[dict[str, Any]]:
        principals = _eks_optional(
            lambda: core._paginate(eks, 'list_access_entries', 'accessEntries', clusterName=cluster_name),
            {'ResourceNotFoundException', 'InvalidParameterException'},
        )
        if not isinstance(principals, list):
            return []

        entries = []
        for principal in principals:
            detail = _eks_optional(
                lambda principal=principal: eks.describe_access_entry(
                    clusterName=cluster_name,
                    principalArn=principal,
                ).get('accessEntry', {}),
                {'ResourceNotFoundException', 'InvalidParameterException'},
            )
            entry = detail if isinstance(detail, dict) and not detail.get('error') else {'principalArn': principal}
            policies = _eks_optional(
                lambda principal=principal: core._paginate(
                    eks,
                    'list_associated_access_policies',
                    'associatedAccessPolicies',
                    clusterName=cluster_name,
                    principalArn=principal,
                ),
                {'ResourceNotFoundException', 'InvalidParameterException'},
            )
            entries.append({
                'name': entry.get('principalArn') or principal,
                'cluster_name': cluster_name,
                'principal_arn': entry.get('principalArn') or principal,
                'kubernetes_groups': entry.get('kubernetesGroups'),
                'access_entry_arn': entry.get('accessEntryArn'),
                'created_at': entry.get('createdAt'),
                'modified_at': entry.get('modifiedAt'),
                'tags': entry.get('tags'),
                'username': entry.get('username'),
                'type': entry.get('type'),
                'associated_policies': policies if isinstance(policies, list) else [],
                'details': detail if isinstance(detail, dict) and detail.get('error') else None,
            })
        return entries

    def cluster_detail(name: str) -> dict[str, Any]:
        response = _eks_optional(lambda: eks.describe_cluster(name=name), {'ResourceNotFoundException'})
        cluster = response.get('cluster', {}) if isinstance(response, dict) and not response.get('error') else {}
        nodegroups = _eks_optional(lambda: core._paginate(eks, 'list_nodegroups', 'nodegroups', clusterName=name), {'ResourceNotFoundException'})
        fargate_profiles = _eks_optional(lambda: core._paginate(eks, 'list_fargate_profiles', 'fargateProfileNames', clusterName=name), {'ResourceNotFoundException'})

        return {
            'name': name,
            'arn': cluster.get('arn'),
            'created_at': cluster.get('createdAt'),
            'version': cluster.get('version'),
            'endpoint': cluster.get('endpoint'),
            'role_arn': cluster.get('roleArn'),
            'resources_vpc_config': cluster.get('resourcesVpcConfig'),
            'kubernetes_network_config': cluster.get('kubernetesNetworkConfig'),
            'logging': cluster.get('logging'),
            'identity': cluster.get('identity'),
            'status': cluster.get('status'),
            'health': cluster.get('health'),
            'certificate_authority': cluster.get('certificateAuthority'),
            'platform_version': cluster.get('platformVersion'),
            'tags': cluster.get('tags'),
            'encryption_config': cluster.get('encryptionConfig'),
            'connector_config': cluster.get('connectorConfig'),
            'access_config': cluster.get('accessConfig'),
            'upgrade_policy': cluster.get('upgradePolicy'),
            'nodegroup_count': len(nodegroups) if isinstance(nodegroups, list) else 0,
            'fargate_profile_count': len(fargate_profiles) if isinstance(fargate_profiles, list) else 0,
            'nodegroups': nodegroups if isinstance(nodegroups, list) else [],
            'fargate_profiles': fargate_profiles if isinstance(fargate_profiles, list) else [],
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def nodegroup_detail(cluster_name: str, nodegroup_name: str) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_nodegroup(clusterName=cluster_name, nodegroupName=nodegroup_name),
            {'ResourceNotFoundException'},
        )
        nodegroup = response.get('nodegroup', {}) if isinstance(response, dict) and not response.get('error') else {}
        return {
            'name': nodegroup_name,
            'cluster_name': cluster_name,
            'arn': nodegroup.get('nodegroupArn'),
            'created_at': nodegroup.get('createdAt'),
            'modified_at': nodegroup.get('modifiedAt'),
            'status': nodegroup.get('status'),
            'capacity_type': nodegroup.get('capacityType'),
            'scaling_config': nodegroup.get('scalingConfig'),
            'instance_types': nodegroup.get('instanceTypes'),
            'subnets': nodegroup.get('subnets'),
            'remote_access': nodegroup.get('remoteAccess'),
            'ami_type': nodegroup.get('amiType'),
            'node_role': nodegroup.get('nodeRole'),
            'labels': nodegroup.get('labels'),
            'taints': nodegroup.get('taints'),
            'resources': nodegroup.get('resources'),
            'disk_size': nodegroup.get('diskSize'),
            'health': nodegroup.get('health'),
            'update_config': nodegroup.get('updateConfig'),
            'launch_template': nodegroup.get('launchTemplate'),
            'version': nodegroup.get('version'),
            'release_version': nodegroup.get('releaseVersion'),
            'tags': nodegroup.get('tags'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def fargate_profile_detail(cluster_name: str, profile_name: str) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_fargate_profile(clusterName=cluster_name, fargateProfileName=profile_name),
            {'ResourceNotFoundException'},
        )
        profile = response.get('fargateProfile', {}) if isinstance(response, dict) and not response.get('error') else {}
        return {
            'name': profile_name,
            'cluster_name': cluster_name,
            'arn': profile.get('fargateProfileArn'),
            'created_at': profile.get('createdAt'),
            'pod_execution_role_arn': profile.get('podExecutionRoleArn'),
            'subnets': profile.get('subnets'),
            'selectors': profile.get('selectors'),
            'status': profile.get('status'),
            'tags': profile.get('tags'),
            'health': profile.get('health'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def addon_detail(cluster_name: str, addon_name: str) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_addon(clusterName=cluster_name, addonName=addon_name),
            {'ResourceNotFoundException'},
        )
        addon = response.get('addon', {}) if isinstance(response, dict) and not response.get('error') else {}
        return {
            'name': addon_name,
            'cluster_name': cluster_name,
            'arn': addon.get('addonArn'),
            'version': addon.get('addonVersion'),
            'status': addon.get('status'),
            'service_account_role_arn': addon.get('serviceAccountRoleArn'),
            'configuration_values': addon.get('configurationValues'),
            'health': addon.get('health'),
            'created_at': addon.get('createdAt'),
            'modified_at': addon.get('modifiedAt'),
            'tags': addon.get('tags'),
            'publisher': addon.get('publisher'),
            'owner': addon.get('owner'),
            'marketplace_information': addon.get('marketplaceInformation'),
            'pod_identity_associations': addon.get('podIdentityAssociations'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def identity_provider_detail(cluster_name: str, config: dict[str, Any]) -> dict[str, Any]:
        response = _eks_optional(
            lambda: eks.describe_identity_provider_config(clusterName=cluster_name, identityProviderConfig=config),
            {'ResourceNotFoundException', 'InvalidParameterException'},
        )
        provider = response.get('identityProviderConfig', {}) if isinstance(response, dict) and not response.get('error') else {}
        oidc = provider.get('oidc') or {}
        name = config.get('name') or oidc.get('identityProviderConfigName')
        return {
            'name': name,
            'cluster_name': cluster_name,
            'type': config.get('type') or provider.get('type'),
            'status': provider.get('status'),
            'issuer_url': oidc.get('issuerUrl'),
            'client_id': oidc.get('clientId'),
            'username_claim': oidc.get('usernameClaim'),
            'username_prefix': oidc.get('usernamePrefix'),
            'groups_claim': oidc.get('groupsClaim'),
            'groups_prefix': oidc.get('groupsPrefix'),
            'required_claims': oidc.get('requiredClaims'),
            'tags': provider.get('tags'),
            'details': response if isinstance(response, dict) and response.get('error') else None,
        }

    def enrich(items, loader):
        if len(items) < 2:
            return [loader(item) for item in items]
        with ThreadPoolExecutor(max_workers=min(8, len(items))) as executor:
            return core.map_in_context(executor, loader, items)

    clusters = enrich(cluster_names, cluster_detail)
    nodegroup_refs = [(cluster.get('name'), name) for cluster in clusters for name in cluster.get('nodegroups', [])]
    profile_refs = [(cluster.get('name'), name) for cluster in clusters for name in cluster.get('fargate_profiles', [])]
    nodegroups = enrich(nodegroup_refs, lambda ref: nodegroup_detail(*ref))
    fargate_profiles = enrich(profile_refs, lambda ref: fargate_profile_detail(*ref))

    return {
        'summary': {
            'clusters': len(clusters),
            'nodegroups': len(nodegroups),
            'fargate_profiles': len(fargate_profiles),
        },
        'clusters': clusters,
        'nodegroups': nodegroups,
        'fargate_profiles': fargate_profiles,
        'supported_from_sdk': [
            'CreateCluster',
            'DescribeCluster',
            'ListClusters',
            'DeleteCluster',
            'CreateNodegroup',
            'DescribeNodegroup',
            'ListNodegroups',
            'DeleteNodegroup',
            'CreateFargateProfile',
            'DescribeFargateProfile',
            'ListFargateProfiles',
            'DeleteFargateProfile',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'notes': [
            'Floci starts a k3s container for each cluster. In mock mode clusters are marked ACTIVE immediately with no Kubernetes behind them.',
        ],
    }


def list_eks_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    eks = factory.client('eks')
    resources: list[dict[str, Any]] = []
    cluster_names = _safe_value(lambda: core._paginate(eks, 'list_clusters', 'clusters'), [])

    for cluster_name in cluster_names:
        resources.append({
            'type': 'cluster',
            'name': cluster_name,
        })
        resources.extend(
            {
                'type': 'nodegroup',
                'cluster': cluster_name,
                'name': nodegroup,
            }
            for nodegroup in _safe_value(
                lambda cluster_name=cluster_name: core._paginate(eks, 'list_nodegroups', 'nodegroups', clusterName=cluster_name),
                [],
            )
        )
        resources.extend(
            {
                'type': 'fargate_profile',
                'cluster': cluster_name,
                'name': profile,
            }
            for profile in _safe_value(
                lambda cluster_name=cluster_name: core._paginate(eks, 'list_fargate_profiles', 'fargateProfileNames', clusterName=cluster_name),
                [],
            )
        )
        resources.extend(
            {
                'type': 'addon',
                'cluster': cluster_name,
                'name': addon,
            }
            for addon in _safe_value(
                lambda cluster_name=cluster_name: core._paginate(eks, 'list_addons', 'addons', clusterName=cluster_name),
                [],
            )
        )
        resources.extend(
            {
                'type': 'identity_provider_config',
                'cluster': cluster_name,
                'name': config.get('name'),
                'provider_type': config.get('type'),
            }
            for config in _safe_value(
                lambda cluster_name=cluster_name: core._paginate(
                    eks,
                    'list_identity_provider_configs',
                    'identityProviderConfigs',
                    clusterName=cluster_name,
                ),
                [],
            )
            if isinstance(config, dict)
        )
        resources.extend(
            {
                'type': 'access_entry',
                'cluster': cluster_name,
                'principal_arn': principal,
            }
            for principal in _safe_value(
                lambda cluster_name=cluster_name: core._paginate(eks, 'list_access_entries', 'accessEntries', clusterName=cluster_name),
                [],
            )
        )

    return resources
