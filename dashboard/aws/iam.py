"""IAM inventory and resource-list loaders."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from django.core.cache import cache

from dashboard import aws as core
from dashboard.aws import IAM_BOUNDARY_CACHE_KEY, _safe_value


def iam_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    iam = factory.client('iam')

    boundary_cache = cache.get(IAM_BOUNDARY_CACHE_KEY, {})
    boundary_cache_scope = '|'.join([
        str(factory.endpoint_url),
        str(factory.region),
        str(factory.credential_source),
        str(factory.profile or ''),
        str(factory.access_key_id or ''),
    ])
    scoped_boundaries = boundary_cache.get(boundary_cache_scope, {})

    def cached_boundary(principal_type: str, principal_name: str) -> dict[str, Any] | None:
        value = scoped_boundaries.get(f'{principal_type}:{principal_name}')
        return value if isinstance(value, dict) else None

    def user_detail(user: dict[str, Any]) -> dict[str, Any]:
        name = user.get('UserName')
        full_user = _safe_value(lambda: iam.get_user(UserName=name).get('User', {}), {})

        return {
            'name': name,
            'arn': user.get('Arn'),
            'created': user.get('CreateDate'),
            'path': full_user.get('Path') or user.get('Path'),
            'login_profile': _safe_value(lambda: iam.get_login_profile(UserName=name).get('LoginProfile'), None),
            'permissions_boundary': full_user.get('PermissionsBoundary') or cached_boundary('user', name),
            'groups': [
                group.get('GroupName')
                for group in _safe_value(
                    lambda: core._paginate(iam, 'list_groups_for_user', 'Groups', UserName=name),
                    [],
                )
            ],
            'attached_policies': [
                {
                    'name': policy.get('PolicyName'),
                    'arn': policy.get('PolicyArn'),
                }
                for policy in _safe_value(
                    lambda: core._paginate(iam, 'list_attached_user_policies', 'AttachedPolicies', UserName=name),
                    [],
                )
            ],
            'inline_policies': _safe_value(
                lambda: core._paginate(iam, 'list_user_policies', 'PolicyNames', UserName=name),
                [],
            ),
            'access_keys': [
                {
                    'id': key.get('AccessKeyId'),
                    'status': key.get('Status'),
                    'created': key.get('CreateDate'),
                    'last_used': _safe_value(
                        lambda key_id=key.get('AccessKeyId'): iam.get_access_key_last_used(AccessKeyId=key_id).get('AccessKeyLastUsed', {}),
                        {},
                    ),
                }
                for key in _safe_value(
                    lambda: core._paginate(iam, 'list_access_keys', 'AccessKeyMetadata', UserName=name),
                    [],
                )
            ],
            'tags': _safe_value(lambda: iam.list_user_tags(UserName=name).get('Tags', []), []),
        }

    def group_detail(group: dict[str, Any]) -> dict[str, Any]:
        name = group.get('GroupName')
        group_response = _safe_value(lambda: iam.get_group(GroupName=name), {})

        return {
            'name': name,
            'arn': group.get('Arn'),
            'created': group.get('CreateDate'),
            'users': [
                user.get('UserName')
                for user in group_response.get('Users', [])
            ],
            'attached_policies': [
                {
                    'name': policy.get('PolicyName'),
                    'arn': policy.get('PolicyArn'),
                }
                for policy in _safe_value(
                    lambda: core._paginate(iam, 'list_attached_group_policies', 'AttachedPolicies', GroupName=name),
                    [],
                )
            ],
            'inline_policies': _safe_value(
                lambda: core._paginate(iam, 'list_group_policies', 'PolicyNames', GroupName=name),
                [],
            ),
        }

    def role_detail(role: dict[str, Any]) -> dict[str, Any]:
        name = role.get('RoleName')
        full_role = _safe_value(lambda: iam.get_role(RoleName=name).get('Role', {}), {})

        return {
            'name': name,
            'arn': role.get('Arn'),
            'created': role.get('CreateDate'),
            'path': full_role.get('Path') or role.get('Path'),
            'description': full_role.get('Description') or role.get('Description'),
            'max_session_duration': full_role.get('MaxSessionDuration') or role.get('MaxSessionDuration'),
            'permissions_boundary': full_role.get('PermissionsBoundary') or cached_boundary('role', name),
            'trust_policy': full_role.get('AssumeRolePolicyDocument') or role.get('AssumeRolePolicyDocument'),
            'attached_policies': [
                {
                    'name': policy.get('PolicyName'),
                    'arn': policy.get('PolicyArn'),
                }
                for policy in _safe_value(
                    lambda: core._paginate(iam, 'list_attached_role_policies', 'AttachedPolicies', RoleName=name),
                    [],
                )
            ],
            'inline_policies': _safe_value(
                lambda: core._paginate(iam, 'list_role_policies', 'PolicyNames', RoleName=name),
                [],
            ),
            'instance_profiles': [
                profile.get('InstanceProfileName')
                for profile in _safe_value(
                    lambda: core._paginate(iam, 'list_instance_profiles_for_role', 'InstanceProfiles', RoleName=name),
                    [],
                )
            ],
            'tags': _safe_value(lambda: iam.list_role_tags(RoleName=name).get('Tags', []), []),
        }

    def policy_detail(policy: dict[str, Any]) -> dict[str, Any]:
        arn = policy.get('Arn')

        return {
            'name': policy.get('PolicyName'),
            'arn': arn,
            'default_version': policy.get('DefaultVersionId'),
            'attachment_count': policy.get('AttachmentCount'),
            'permissions_boundary_usage_count': policy.get('PermissionsBoundaryUsageCount'),
            'created': policy.get('CreateDate'),
            'updated': policy.get('UpdateDate'),
            'path': policy.get('Path'),
            'description': policy.get('Description'),
            'versions': [
                {
                    'id': version.get('VersionId'),
                    'default': version.get('IsDefaultVersion'),
                    'created': version.get('CreateDate'),
                }
                for version in _safe_value(
                    lambda: core._paginate(iam, 'list_policy_versions', 'Versions', PolicyArn=arn),
                    [],
                )
            ],
            'tags': _safe_value(lambda: iam.list_policy_tags(PolicyArn=arn).get('Tags', []), []),
        }

    users = core._paginate(iam, 'list_users', 'Users')
    groups = core._paginate(iam, 'list_groups', 'Groups')
    roles = core._paginate(iam, 'list_roles', 'Roles')
    policies = _safe_value(
        lambda: core._paginate(iam, 'list_policies', 'Policies', Scope='Local'),
        core._paginate(iam, 'list_policies', 'Policies'),
    )
    instance_profiles = core._paginate(iam, 'list_instance_profiles', 'InstanceProfiles')

    def enrich(items: list[dict[str, Any]], loader: Callable[[dict[str, Any]], dict[str, Any]]) -> list[dict[str, Any]]:
        if len(items) < 2:
            return [loader(item) for item in items]
        with ThreadPoolExecutor(max_workers=min(8, len(items))) as executor:
            return list(executor.map(loader, items))

    return {
        'summary': {
            'users': len(users),
            'groups': len(groups),
            'roles': len(roles),
            'customer_policies': len(policies),
            'instance_profiles': len(instance_profiles),
        },
        'users': enrich(users, user_detail),
        'groups': enrich(groups, group_detail),
        'roles': enrich(roles, role_detail),
        'policies': enrich(policies, policy_detail),
        'instance_profiles': [
            {
                'name': profile.get('InstanceProfileName'),
                'arn': profile.get('Arn'),
                'created': profile.get('CreateDate'),
                'roles': [
                    role.get('RoleName')
                    for role in profile.get('Roles', [])
                ],
            }
            for profile in instance_profiles
        ],
        'notes': [
            'Floci 1.5.30 enforces AssumeRole trust policies when enforcement is enabled and evaluates STS session policies.',
            'Floci 1.5.29 routes assumed-role credentials to the target account and adds the AmazonRDSEnhancedMonitoringRole managed policy.',
            'Floci 1.5.28 makes IAM entity stores thread-safe under concurrent mutation and resolves AWS-managed policies from any account context.',
        ],
    }


def list_iam_users(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': user.get('UserName'),
            'arn': user.get('Arn'),
            'created': user.get('CreateDate'),
        }
        for user in factory.client('iam').get_paginator('list_users').paginate().build_full_result().get('Users', [])
    ]


def list_iam_roles(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'name': role.get('RoleName'),
            'arn': role.get('Arn'),
            'created': role.get('CreateDate'),
        }
        for role in factory.client('iam').get_paginator('list_roles').paginate().build_full_result().get('Roles', [])
    ]
