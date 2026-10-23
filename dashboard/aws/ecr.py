"""ECR inventory and resource-list loaders."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _ecr_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def ecr_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    ecr = factory.client('ecr')
    repositories = _safe_value(lambda: core._paginate(ecr, 'describe_repositories', 'repositories'), [])

    def repository_detail(repository: dict[str, Any]) -> dict[str, Any]:
        name = repository.get('repositoryName')
        arn = repository.get('repositoryArn')
        image_ids = _ecr_optional(
            lambda: core._paginate(ecr, 'list_images', 'imageIds', repositoryName=name),
            {'RepositoryNotFoundException'},
        )
        image_details = _ecr_optional(
            lambda: core._paginate(ecr, 'describe_images', 'imageDetails', repositoryName=name),
            {'RepositoryNotFoundException', 'ImageNotFoundException'},
        )

        return {
            'name': name,
            'arn': arn,
            'registry_id': repository.get('registryId'),
            'uri': repository.get('repositoryUri'),
            'created': repository.get('createdAt'),
            'tag_mutability': repository.get('imageTagMutability'),
            'encryption_configuration': repository.get('encryptionConfiguration'),
            'scanning_configuration': repository.get('imageScanningConfiguration'),
            'image_count': len(image_ids) if isinstance(image_ids, list) else 0,
            'images': image_ids,
            'image_details': image_details,
            'tags': _ecr_optional(
                lambda: ecr.list_tags_for_resource(resourceArn=arn).get('tags', []),
                {'RepositoryNotFoundException', 'InvalidParameterException'},
            ) if arn else None,
            'lifecycle_policy': _ecr_optional(
                lambda: ecr.get_lifecycle_policy(repositoryName=name),
                {'LifecyclePolicyNotFoundException', 'RepositoryNotFoundException'},
            ),
            'repository_policy': _ecr_optional(
                lambda: ecr.get_repository_policy(repositoryName=name),
                {'RepositoryPolicyNotFoundException', 'RepositoryNotFoundException'},
            ),
        }

    auth_data = _ecr_optional(
        lambda: ecr.get_authorization_token().get('authorizationData', []),
        {'InvalidParameterException'},
    )
    auth_endpoints = []
    if isinstance(auth_data, list):
        auth_endpoints = [
            {
                'name': item.get('proxyEndpoint') or 'ECR proxy endpoint',
                'proxy_endpoint': item.get('proxyEndpoint'),
                'expires_at': item.get('expiresAt'),
            }
            for item in auth_data
        ]

    if len(repositories) > 1:
        with ThreadPoolExecutor(max_workers=min(8, len(repositories))) as executor:
            detailed_repositories = core.map_in_context(executor, repository_detail, repositories)
    else:
        detailed_repositories = [repository_detail(repository) for repository in repositories]

    return {
        'summary': {
            'repositories': len(detailed_repositories),
            'images': sum(repository.get('image_count') or 0 for repository in detailed_repositories),
            'tagged_images': sum(
                1
                for repository in detailed_repositories
                for image in repository.get('images', [])
                if isinstance(image, dict) and image.get('imageTag')
            ),
            'auth_endpoints': len(auth_endpoints),
            'storage_bytes': sum(
                image.get('imageSizeInBytes') or 0
                for repository in detailed_repositories
                for image in (repository.get('image_details') if isinstance(repository.get('image_details'), list) else [])
            ),
        },
        'repositories': detailed_repositories,
        'auth_endpoints': auth_endpoints,
        'supported': [
            'CreateRepository',
            'DescribeRepositories',
            'DeleteRepository',
            'GetAuthorizationToken',
            'ListImages',
            'DescribeImages',
            'BatchGetImage',
            'BatchDeleteImage',
            'PutImageTagMutability',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
            'PutLifecyclePolicy',
            'GetLifecyclePolicy',
            'DeleteLifecyclePolicy',
            'SetRepositoryPolicy',
            'GetRepositoryPolicy',
            'DeleteRepositoryPolicy',
        ],
        'admin_endpoints': ['POST /_floci/ecr/gc'],
        'configuration': {
            'registry_image': 'registry:2',
            'registry_container_name': 'floci-ecr-registry',
            'registry_base_port': 5100,
            'registry_max_port': 5199,
            'data_path': './data/ecr',
            'tls_enabled': False,
            'keep_running_on_shutdown': True,
            'uri_style': 'hostname',
        },
        'environment_variables': [
            'FLOCI_SERVICES_ECR_ENABLED',
            'FLOCI_SERVICES_ECR_REGISTRY_IMAGE',
            'FLOCI_SERVICES_ECR_REGISTRY_CONTAINER_NAME',
            'FLOCI_SERVICES_ECR_REGISTRY_BASE_PORT',
            'FLOCI_SERVICES_ECR_REGISTRY_MAX_PORT',
            'FLOCI_SERVICES_ECR_DATA_PATH',
            'FLOCI_SERVICES_ECR_KEEP_RUNNING_ON_SHUTDOWN',
            'FLOCI_SERVICES_ECR_URI_STYLE',
            'FLOCI_SERVICES_ECR_TLS_ENABLED',
        ],
        'not_implemented': [
            'Replication and pull-through cache',
            'Image scanning',
            'Image signing and notary attachments',
            'Lifecycle policy enforcement',
            'Repository policy enforcement',
            'TLS via emulated ACM',
        ],
        'notes': [
            'The control plane uses JSON 1.1 and the data plane is a real OCI Distribution v2 registry.',
            'Repository URIs use loopback hostnames like <account>.dkr.ecr.<region>.localhost:<port>/<repo> by default.',
            'GetAuthorizationToken returns a docker login token, but the dashboard only displays proxy endpoints.',
            'Deleted image blobs are reclaimed by POST /_floci/ecr/gc.',
            'Floci 1.5.19 publishes official Floci images to the AWS ECR Public Gallery as well as Docker Hub.',
        ],
    }


def list_ecr_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    ecr = factory.client('ecr')
    resources = [
        {
            'type': 'repository',
            'name': repository.get('repositoryName'),
            'arn': repository.get('repositoryArn'),
            'uri': repository.get('repositoryUri'),
        }
        for repository in _safe_value(lambda: core._paginate(ecr, 'describe_repositories', 'repositories'), [])
    ]

    for repository in list(resources):
        name = repository.get('name')
        resources.extend(
            {
                'type': 'image',
                'repository': name,
                'tag': image.get('imageTag'),
                'digest': image.get('imageDigest'),
            }
            for image in _safe_value(lambda name=name: core._paginate(ecr, 'list_images', 'imageIds', repositoryName=name), [])
        )

    return resources
