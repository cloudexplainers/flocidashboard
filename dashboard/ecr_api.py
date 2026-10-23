"""Interactive ECR helpers for local image repository workflows."""

from __future__ import annotations

import base64
import json
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .aws import FlociClientFactory, _clean_response, public_endpoint_url


def _client():
    return FlociClientFactory().client('ecr')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _list(value: Any, label: str = 'Value') -> list[Any]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return value
    raise ValueError(f'{label} must be a list')


def _tags(value: Any) -> list[dict[str, str]]:
    if value in (None, '', []):
        return []
    if isinstance(value, dict):
        value = [{'Key': key, 'Value': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Tags must be an object or list')

    tags = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each tag must be an object')
        key = _required(item.get('Key') or item.get('key'), 'Tag key')
        tag_value = item.get('Value')
        if tag_value is None:
            tag_value = item.get('value')
        tags.append({'Key': key, 'Value': '' if tag_value is None else str(tag_value)})
    return tags


def _tag_keys(value: Any) -> list[str]:
    keys = [str(item).strip() for item in _list(value, 'Tag keys') if str(item).strip()]
    if not keys:
        raise ValueError('At least one tag key is required')
    return keys


def _image_ids(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError('At least one image ID is required')
    images = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if not text:
                continue
            if text.startswith('sha256:'):
                images.append({'imageDigest': text})
            else:
                images.append({'imageTag': text})
            continue
        if isinstance(item, dict):
            image: dict[str, str] = {}
            tag = item.get('imageTag') or item.get('tag')
            digest = item.get('imageDigest') or item.get('digest')
            if tag:
                image['imageTag'] = str(tag)
            if digest:
                image['imageDigest'] = str(digest)
            if image:
                images.append(image)
            continue
        raise ValueError('Each image ID must be an object or string')
    if not images:
        raise ValueError('At least one image ID is required')
    return images


def _policy_text(value: Any, label: str) -> str:
    if value in (None, ''):
        raise ValueError(f'{label} is required')
    if isinstance(value, str):
        return value
    return json.dumps(value)


def create_repository(
    name: str,
    *,
    image_tag_mutability: str = 'MUTABLE',
    tags: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'repositoryName': _required(name, 'Repository name'),
        'imageTagMutability': (image_tag_mutability or 'MUTABLE').strip().upper(),
    }
    clean_tags = _tags(tags)
    if clean_tags:
        kwargs['tags'] = clean_tags
    response = _client().create_repository(**kwargs)
    repository = response.get('repository', {})
    return {
        'name': repository.get('repositoryName') or kwargs['repositoryName'],
        'arn': repository.get('repositoryArn'),
        'uri': repository.get('repositoryUri'),
        'tag_mutability': repository.get('imageTagMutability'),
        'response': _clean_response(response),
    }


def delete_repository(name: str, *, force: bool = True) -> dict[str, Any]:
    repository_name = _required(name, 'Repository name')
    response = _client().delete_repository(repositoryName=repository_name, force=bool(force))
    repository = response.get('repository', {})
    return {
        'name': repository.get('repositoryName') or repository_name,
        'arn': repository.get('repositoryArn'),
        'force': bool(force),
        'response': _clean_response(response),
    }


def get_authorization_token() -> dict[str, Any]:
    response = _client().get_authorization_token()
    auth_data = []
    for item in response.get('authorizationData', []):
        token = item.get('authorizationToken') or ''
        username = 'AWS'
        password = ''
        try:
            decoded = base64.b64decode(token).decode('utf-8')
            username, password = decoded.split(':', 1)
        except (ValueError, TypeError):
            password = token
        endpoint = item.get('proxyEndpoint')
        auth_data.append({
            'proxy_endpoint': endpoint,
            'expires_at': item.get('expiresAt'),
            'username': username,
            'password': password,
            'docker_login': f'docker login --username {username} --password-stdin {endpoint}' if endpoint else '',
        })
    return {'authorization_data': auth_data}


def batch_delete_image(repository_name: str, image_ids: Any) -> dict[str, Any]:
    repo = _required(repository_name, 'Repository name')
    images = _image_ids(image_ids)
    response = _client().batch_delete_image(repositoryName=repo, imageIds=images)
    return {
        'repository_name': repo,
        'image_ids': images,
        'image_ids_deleted': response.get('imageIds', []),
        'failures': response.get('failures', []),
        'response': _clean_response(response),
    }


def batch_get_image(repository_name: str, image_ids: Any, accepted_media_types: Any = None) -> dict[str, Any]:
    repo = _required(repository_name, 'Repository name')
    images = _image_ids(image_ids)
    kwargs: dict[str, Any] = {'repositoryName': repo, 'imageIds': images}
    media_types = _list(accepted_media_types, 'Accepted media types')
    if media_types:
        kwargs['acceptedMediaTypes'] = media_types
    response = _client().batch_get_image(**kwargs)
    return {
        'repository_name': repo,
        'images': _clean_response(response.get('images', [])),
        'failures': _clean_response(response.get('failures', [])),
        'response': _clean_response(response),
    }


def put_image_tag_mutability(repository_name: str, image_tag_mutability: str) -> dict[str, Any]:
    repo = _required(repository_name, 'Repository name')
    mutability = _required(image_tag_mutability, 'Image tag mutability').upper()
    response = _client().put_image_tag_mutability(
        repositoryName=repo,
        imageTagMutability=mutability,
    )
    return {'repository_name': repo, 'image_tag_mutability': mutability, 'response': _clean_response(response)}


def put_lifecycle_policy(repository_name: str, lifecycle_policy_text: Any) -> dict[str, Any]:
    repo = _required(repository_name, 'Repository name')
    response = _client().put_lifecycle_policy(
        repositoryName=repo,
        lifecyclePolicyText=_policy_text(lifecycle_policy_text, 'Lifecycle policy'),
    )
    return {'repository_name': repo, 'response': _clean_response(response)}


def delete_lifecycle_policy(repository_name: str) -> dict[str, Any]:
    repo = _required(repository_name, 'Repository name')
    response = _client().delete_lifecycle_policy(repositoryName=repo)
    return {'repository_name': repo, 'response': _clean_response(response)}


def set_repository_policy(repository_name: str, policy_text: Any, *, force: bool = True) -> dict[str, Any]:
    repo = _required(repository_name, 'Repository name')
    response = _client().set_repository_policy(
        repositoryName=repo,
        policyText=_policy_text(policy_text, 'Repository policy'),
        force=bool(force),
    )
    return {'repository_name': repo, 'response': _clean_response(response)}


def delete_repository_policy(repository_name: str) -> dict[str, Any]:
    repo = _required(repository_name, 'Repository name')
    response = _client().delete_repository_policy(repositoryName=repo)
    return {'repository_name': repo, 'response': _clean_response(response)}


def tag_resource(resource_arn: str, tags: Any) -> dict[str, Any]:
    arn = _required(resource_arn, 'Resource ARN')
    clean_tags = _tags(tags)
    if not clean_tags:
        raise ValueError('At least one tag is required')
    response = _client().tag_resource(resourceArn=arn, tags=clean_tags)
    return {'resource_arn': arn, 'tags': clean_tags, 'response': _clean_response(response)}


def untag_resource(resource_arn: str, tag_keys: Any) -> dict[str, Any]:
    arn = _required(resource_arn, 'Resource ARN')
    keys = _tag_keys(tag_keys)
    response = _client().untag_resource(resourceArn=arn, tagKeys=keys)
    return {'resource_arn': arn, 'tag_keys': keys, 'response': _clean_response(response)}


def run_garbage_collection() -> dict[str, Any]:
    endpoint = FlociClientFactory().endpoint_url.rstrip('/')
    request = Request(f'{endpoint}/_floci/ecr/gc', data=b'', method='POST')
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read().decode('utf-8')
    except URLError as exc:
        raise ValueError(f'ECR garbage collection failed: {exc}') from exc
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {'output': raw}
    return {'endpoint': f'{endpoint}/_floci/ecr/gc', 'result': payload}


def get_push_commands(
    repository_name: str,
    *,
    registry_id: str | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """Push commands for a repository, built from the URI and login endpoint Floci reports.

    Floci serves images at the repository's repositoryUri
    (<account>.dkr.ecr.<region>.localhost:<port>/<repo> by default) and returns the
    docker login address as GetAuthorizationToken's proxyEndpoint.
    """
    repo = _required(repository_name, 'Repository name')
    factory = FlociClientFactory()
    clean_region = (region or '').strip() or factory.region
    client = _client()
    describe_kwargs: dict[str, Any] = {'repositoryNames': [repo]}
    if registry_id:
        describe_kwargs['registryId'] = registry_id
    repositories = client.describe_repositories(**describe_kwargs).get('repositories') or []
    registry_uri = (repositories[0].get('repositoryUri') if repositories else '') or ''
    if not registry_uri:
        raise ValueError(f'Floci did not return a repository URI for {repo}')
    auth = (client.get_authorization_token().get('authorizationData') or [{}])[0]
    login_endpoint = auth.get('proxyEndpoint') or registry_uri.split('/', 1)[0]
    # The AWS CLI must talk to Floci, not AWS.
    cli_endpoint = public_endpoint_url()
    login = f'aws ecr get-login-password --region {clean_region} --endpoint-url {cli_endpoint}'

    commands = {
        'mac_linux': [
            '# 1. Retrieve an authentication token and authenticate your Docker client to your registry.',
            f'{login} | docker login --username AWS --password-stdin {login_endpoint}',
            '# 2. Build your Docker image using the following command.',
            f'docker build -t {repo} .',
            '# 3. After the build completes, tag your image so you can push the image to this repository.',
            f'docker tag {repo}:latest {registry_uri}:latest',
            '# 4. Run the following command to push this image to your newly created AWS repository.',
            f'docker push {registry_uri}:latest',
        ],
        'windows_powershell': [
            '# 1. Authenticate Docker client',
            f'{login} | docker login --username AWS --password-stdin {login_endpoint}',
            '# 2. Build image',
            f'docker build -t {repo} .',
            '# 3. Tag image',
            f'docker tag {repo}:latest {registry_uri}:latest',
            '# 4. Push image',
            f'docker push {registry_uri}:latest',
        ],
        'podman': [
            '# 1. Authenticate Podman client',
            f'{login} | podman login -u AWS --password-stdin {login_endpoint}',
            '# 2. Build image',
            f'podman build -t {repo} .',
            '# 3. Tag and push',
            f'podman tag {repo}:latest {registry_uri}:latest',
            f'podman push {registry_uri}:latest',
        ],
    }

    return {
        'repository_name': repo,
        'registry_uri': registry_uri,
        'login_endpoint': login_endpoint,
        'region': clean_region,
        'commands': commands,
    }


def put_mock_image(
    repository_name: str,
    *,
    image_tag: str = 'latest',
    image_digest: str | None = None,
    image_manifest: str | None = None,
) -> dict[str, Any]:
    import hashlib
    repo = _required(repository_name, 'Repository name')
    clean_tag = image_tag.strip() if image_tag else 'latest'

    if not image_manifest:
        manifest_dict = {
            'schemaVersion': 2,
            'mediaType': 'application/vnd.docker.distribution.manifest.v2+json',
            'config': {
                'mediaType': 'application/vnd.docker.container.image.v1+json',
                'size': 1420,
                'digest': 'sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
            },
            'layers': [
                {
                    'mediaType': 'application/vnd.docker.image.rootfs.diff.tar.gzip',
                    'size': 28140200,
                    'digest': 'sha256:' + hashlib.sha256(f'{repo}:{clean_tag}'.encode()).hexdigest(),
                }
            ],
        }
        image_manifest = json.dumps(manifest_dict)

    kwargs: dict[str, Any] = {
        'repositoryName': repo,
        'imageManifest': image_manifest,
        'imageTag': clean_tag,
    }
    if image_digest:
        kwargs['imageDigest'] = image_digest.strip()

    # Errors propagate: Floci 2.3.0 has no PutImage, and a failed write must not look published.
    response = _client().put_image(**kwargs)
    image_data = _clean_response(response.get('image', {}))

    return {
        'repository_name': repo,
        'image_tag': clean_tag,
        'image': image_data,
    }

