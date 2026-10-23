"""Interactive IAM helpers for the identity workbench."""

from __future__ import annotations

import json
from typing import Any, Literal

from django.core.cache import cache

from .aws import FlociClientFactory

PrincipalType = Literal['user', 'group', 'role']

BASELINE_IDENTITY_POLICY_NAME = 'FlociDashboardGetCallerIdentity'
BASELINE_IDENTITY_POLICY_DOCUMENT = {
    'Version': '2012-10-17',
    'Statement': [{
        'Effect': 'Allow',
        'Action': 'sts:GetCallerIdentity',
        'Resource': '*',
    }],
}
BOUNDARY_CACHE_KEY = 'dashboard:iam-permissions-boundaries'

TRUST_POLICY_TEMPLATES = {
    'lambda': {
        'Version': '2012-10-17',
        'Statement': [{
            'Effect': 'Allow',
            'Principal': {'Service': 'lambda.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        }],
    },
    'ec2': {
        'Version': '2012-10-17',
        'Statement': [{
            'Effect': 'Allow',
            'Principal': {'Service': 'ec2.amazonaws.com'},
            'Action': 'sts:AssumeRole',
        }],
    },
}


def _account_root_trust_policy() -> dict[str, Any]:
    # Trust the caller's own account, as the console's "This account" option does.
    account = _sts_client().get_caller_identity().get('Account')
    if not account:
        raise ValueError('Floci did not return the caller account for the account-root trust policy')
    return {
        'Version': '2012-10-17',
        'Statement': [{
            'Effect': 'Allow',
            'Principal': {'AWS': f'arn:aws:iam::{account}:root'},
            'Action': 'sts:AssumeRole',
        }],
    }


def _iam_client():
    return FlociClientFactory().client('iam')


def _sts_client():
    return FlociClientFactory().client('sts')


def _boundary_cache_scope(factory: FlociClientFactory) -> str:
    return '|'.join([
        str(factory.endpoint_url),
        str(factory.region),
        str(factory.credential_source),
        str(factory.profile or ''),
        str(factory.access_key_id or ''),
    ])


def _boundary_cache_key(principal_type: str, principal_name: str) -> str:
    return f'{principal_type}:{principal_name}'


def _cache_permissions_boundary(factory: FlociClientFactory, principal_type: str, principal_name: str, policy_arn: str | None) -> None:
    cache_data = cache.get(BOUNDARY_CACHE_KEY, {})
    scope = _boundary_cache_scope(factory)
    scoped = dict(cache_data.get(scope, {}))
    key = _boundary_cache_key(principal_type, principal_name)
    if policy_arn:
        scoped[key] = {
            'PermissionsBoundaryType': 'PermissionsBoundaryPolicy',
            'PermissionsBoundaryArn': policy_arn,
        }
    else:
        scoped.pop(key, None)
    cache_data = {**cache_data, scope: scoped}
    cache.set(BOUNDARY_CACHE_KEY, cache_data, None)


def validate_name(value: str, label: str) -> str:
    name = (value or '').strip()
    if not name:
        raise ValueError(f'{label} is required')
    return name


def validate_principal_type(value: str) -> PrincipalType:
    if value not in {'user', 'group', 'role'}:
        raise ValueError('Principal type must be user, group, or role')
    return value  # type: ignore[return-value]


def policy_document(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError('Policy document must be valid JSON') from exc
    if not isinstance(value, dict):
        raise ValueError('Policy document must be a JSON object')
    return value


def policy_arns(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError('Session policy ARNs must be valid JSON') from exc
    if not isinstance(value, list):
        raise ValueError('Session policy ARNs must be a JSON array')

    arns = []
    for item in value:
        raw_arn = item.get('arn') if isinstance(item, dict) else item
        arn = str(raw_arn or '').strip()
        if arn:
            arns.append({'arn': arn})
    return arns


def create_access_key(user_name: str) -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    response = _iam_client().create_access_key(UserName=name)['AccessKey']
    return {
        'user_name': name,
        'access_key_id': response.get('AccessKeyId'),
        'secret_access_key': response.get('SecretAccessKey'),
        'status': response.get('Status'),
        'created': response.get('CreateDate'),
    }


def create_user(user_name: str, *, add_baseline_policy: bool = True) -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    iam = _iam_client()
    response = iam.create_user(UserName=name)['User']
    policy_name = None
    if add_baseline_policy:
        policy_name = BASELINE_IDENTITY_POLICY_NAME
        iam.put_user_policy(
            UserName=name,
            PolicyName=policy_name,
            PolicyDocument=json.dumps(BASELINE_IDENTITY_POLICY_DOCUMENT),
        )
    return {
        'user_name': response.get('UserName') or name,
        'arn': response.get('Arn'),
        'user_id': response.get('UserId'),
        'created': response.get('CreateDate'),
        'baseline_policy': policy_name,
    }


def create_group(group_name: str) -> dict[str, Any]:
    name = validate_name(group_name, 'Group name')
    group = _iam_client().create_group(GroupName=name)['Group']
    return {
        'group_name': group.get('GroupName') or name,
        'arn': group.get('Arn'),
        'created': group.get('CreateDate'),
    }


def update_user(user_name: str, *, new_name: str = '', new_path: str = '') -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    kwargs: dict[str, Any] = {'UserName': name}
    if new_name:
        kwargs['NewUserName'] = validate_name(new_name, 'New user name')
    if new_path:
        kwargs['NewPath'] = str(new_path).strip()
    if len(kwargs) == 1:
        raise ValueError('A new user name or path is required')
    _iam_client().update_user(**kwargs)
    return {'user_name': name, 'new_user_name': kwargs.get('NewUserName'), 'new_path': kwargs.get('NewPath'), 'updated': True}


def update_role(role_name: str, *, description: str | None = None, max_session_duration: Any = None) -> dict[str, Any]:
    name = validate_name(role_name, 'Role name')
    kwargs: dict[str, Any] = {'RoleName': name}
    if description is not None:
        kwargs['Description'] = str(description)
    if max_session_duration not in (None, ''):
        kwargs['MaxSessionDuration'] = int(max_session_duration)
    if len(kwargs) == 1:
        raise ValueError('A description or maximum session duration is required')
    _iam_client().update_role(**kwargs)
    return {'role_name': name, 'description': kwargs.get('Description'), 'max_session_duration': kwargs.get('MaxSessionDuration'), 'updated': True}


def trust_policy_from_template(template: str, document: Any = None) -> dict[str, Any]:
    if document:
        return policy_document(document)
    key = (template or '').strip() or 'lambda'
    if key == 'account-root':
        return _account_root_trust_policy()
    if key not in TRUST_POLICY_TEMPLATES:
        raise ValueError('Trust template must be lambda, ec2, account-root, or custom JSON')
    return TRUST_POLICY_TEMPLATES[key]


def create_role(role_name: str, trust_template: str = 'lambda', trust_policy: Any = None) -> dict[str, Any]:
    name = validate_name(role_name, 'Role name')
    doc = trust_policy_from_template(trust_template, trust_policy)
    role = _iam_client().create_role(
        RoleName=name,
        AssumeRolePolicyDocument=json.dumps(doc),
    )['Role']
    return {
        'role_name': role.get('RoleName') or name,
        'arn': role.get('Arn'),
        'created': role.get('CreateDate'),
        'trust_policy': doc,
    }


def create_instance_profile(profile_name: str) -> dict[str, Any]:
    name = validate_name(profile_name, 'Instance profile name')
    profile = _iam_client().create_instance_profile(InstanceProfileName=name)['InstanceProfile']
    return {
        'instance_profile_name': profile.get('InstanceProfileName') or name,
        'arn': profile.get('Arn'),
        'created': profile.get('CreateDate'),
        'roles': profile.get('Roles', []),
    }


def add_role_to_instance_profile(profile_name: str, role_name: str) -> dict[str, Any]:
    profile = validate_name(profile_name, 'Instance profile name')
    role = validate_name(role_name, 'Role name')
    _iam_client().add_role_to_instance_profile(InstanceProfileName=profile, RoleName=role)
    return {'instance_profile_name': profile, 'role_name': role, 'added': True}


def remove_role_from_instance_profile(profile_name: str, role_name: str) -> dict[str, Any]:
    profile = validate_name(profile_name, 'Instance profile name')
    role = validate_name(role_name, 'Role name')
    _iam_client().remove_role_from_instance_profile(InstanceProfileName=profile, RoleName=role)
    return {'instance_profile_name': profile, 'role_name': role, 'removed': True}


def delete_instance_profile(profile_name: str) -> dict[str, Any]:
    name = validate_name(profile_name, 'Instance profile name')
    _iam_client().delete_instance_profile(InstanceProfileName=name)
    return {'instance_profile_name': name, 'deleted': True}


def update_access_key(user_name: str, access_key_id: str, status: str) -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    key_id = validate_name(access_key_id, 'Access key ID')
    clean_status = validate_name(status, 'Status')
    if clean_status not in {'Active', 'Inactive'}:
        raise ValueError('Access key status must be Active or Inactive')
    _iam_client().update_access_key(UserName=name, AccessKeyId=key_id, Status=clean_status)
    return {'user_name': name, 'access_key_id': key_id, 'status': clean_status}


def delete_access_key(user_name: str, access_key_id: str) -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    key_id = validate_name(access_key_id, 'Access key ID')
    _iam_client().delete_access_key(UserName=name, AccessKeyId=key_id)
    return {'user_name': name, 'access_key_id': key_id, 'deleted': True}


def get_access_key_last_used(access_key_id: str) -> dict[str, Any]:
    key_id = validate_name(access_key_id, 'Access key ID')
    response = _iam_client().get_access_key_last_used(AccessKeyId=key_id)
    return {'access_key_id': key_id, 'user_name': response.get('UserName'), 'last_used': response.get('AccessKeyLastUsed', {})}


def save_login_profile(user_name: str, password: str, *, reset_required: bool = False, update: bool = False) -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    clean_password = validate_name(password, 'Password')
    kwargs = {'UserName': name, 'Password': clean_password, 'PasswordResetRequired': bool(reset_required)}
    client = _iam_client()
    response = client.update_login_profile(**kwargs) if update else client.create_login_profile(**kwargs)
    profile = response.get('LoginProfile', {}) if isinstance(response, dict) else {}
    return {'user_name': name, 'created': profile.get('CreateDate'), 'password_reset_required': bool(reset_required), 'updated': update}


def delete_login_profile(user_name: str) -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    _iam_client().delete_login_profile(UserName=name)
    return {'user_name': name, 'deleted': True}


def _client_error_code(exc: Exception) -> str:
    if isinstance(exc, Exception) and hasattr(exc, 'response'):
        response = getattr(exc, 'response', {}) or {}
        error = response.get('Error', {}) if isinstance(response, dict) else {}
        return error.get('Code', '')
    return ''


def _safe_iam(call, missing_codes: set[str] | None = None):
    try:
        return call()
    except Exception as exc:
        if _client_error_code(exc) in (missing_codes or {'NoSuchEntity'}):
            return None
        raise


def _policy_arn_list(iam, method_name: str, result_key: str, **kwargs) -> list[dict[str, str]]:
    return _safe_iam(lambda: getattr(iam, method_name)(**kwargs).get(result_key, [])) or []


def cleanup_user(user_name: str, *, force: bool = False) -> dict[str, Any]:
    name = validate_name(user_name, 'User name')
    iam = _iam_client()
    keys = _safe_iam(lambda: iam.list_access_keys(UserName=name).get('AccessKeyMetadata', [])) or []
    groups = _safe_iam(lambda: iam.list_groups_for_user(UserName=name).get('Groups', [])) or []
    attached = _policy_arn_list(iam, 'list_attached_user_policies', 'AttachedPolicies', UserName=name)
    inline = _safe_iam(lambda: iam.list_user_policies(UserName=name).get('PolicyNames', [])) or []
    login_profile = _safe_iam(lambda: iam.get_login_profile(UserName=name).get('LoginProfile'))
    blockers = []
    if keys:
        blockers.append(f'{len(keys)} access keys')
    if groups:
        blockers.append(f'{len(groups)} group memberships')
    if attached:
        blockers.append(f'{len(attached)} managed policies')
    if inline:
        blockers.append(f'{len(inline)} inline policies')
    if login_profile:
        blockers.append('a login profile')
    if blockers and not force:
        raise ValueError(f'User {name} has dependencies: {", ".join(blockers)}. Confirm cleanup to delete them first.')
    for key in keys:
        if key.get('AccessKeyId'):
            _safe_iam(lambda key=key: iam.delete_access_key(UserName=name, AccessKeyId=key['AccessKeyId']))
    for group in groups:
        if group.get('GroupName'):
            _safe_iam(lambda group=group: iam.remove_user_from_group(UserName=name, GroupName=group['GroupName']))
    for policy in attached:
        if policy.get('PolicyArn'):
            _safe_iam(lambda policy=policy: iam.detach_user_policy(UserName=name, PolicyArn=policy['PolicyArn']))
    for policy_name in inline:
        _safe_iam(lambda policy_name=policy_name: iam.delete_user_policy(UserName=name, PolicyName=policy_name))
    if login_profile:
        _safe_iam(lambda: iam.delete_login_profile(UserName=name))
    _safe_iam(lambda: iam.delete_user(UserName=name))
    return {'user_name': name, 'deleted': True, 'cleaned': {'access_keys': len(keys), 'groups': len(groups), 'attached_policies': len(attached), 'inline_policies': len(inline), 'login_profile': bool(login_profile)}}


def cleanup_group(group_name: str, *, force: bool = False) -> dict[str, Any]:
    name = validate_name(group_name, 'Group name')
    iam = _iam_client()
    group_response = _safe_iam(lambda: iam.get_group(GroupName=name)) or {}
    users = group_response.get('Users', [])
    attached = _policy_arn_list(iam, 'list_attached_group_policies', 'AttachedPolicies', GroupName=name)
    inline = _safe_iam(lambda: iam.list_group_policies(GroupName=name).get('PolicyNames', [])) or []
    blockers = []
    if users:
        blockers.append(f'{len(users)} users')
    if attached:
        blockers.append(f'{len(attached)} managed policies')
    if inline:
        blockers.append(f'{len(inline)} inline policies')
    if blockers and not force:
        raise ValueError(f'Group {name} has dependencies: {", ".join(blockers)}. Confirm cleanup to delete them first.')
    for user in users:
        if user.get('UserName'):
            _safe_iam(lambda user=user: iam.remove_user_from_group(GroupName=name, UserName=user['UserName']))
    for policy in attached:
        if policy.get('PolicyArn'):
            _safe_iam(lambda policy=policy: iam.detach_group_policy(GroupName=name, PolicyArn=policy['PolicyArn']))
    for policy_name in inline:
        _safe_iam(lambda policy_name=policy_name: iam.delete_group_policy(GroupName=name, PolicyName=policy_name))
    _safe_iam(lambda: iam.delete_group(GroupName=name))
    return {'group_name': name, 'deleted': True, 'cleaned': {'users': len(users), 'attached_policies': len(attached), 'inline_policies': len(inline)}}


def cleanup_role(role_name: str, *, force: bool = False) -> dict[str, Any]:
    name = validate_name(role_name, 'Role name')
    iam = _iam_client()
    profiles = _safe_iam(lambda: iam.list_instance_profiles_for_role(RoleName=name).get('InstanceProfiles', [])) or []
    attached = _policy_arn_list(iam, 'list_attached_role_policies', 'AttachedPolicies', RoleName=name)
    inline = _safe_iam(lambda: iam.list_role_policies(RoleName=name).get('PolicyNames', [])) or []
    blockers = []
    if profiles:
        blockers.append(f'{len(profiles)} instance profiles')
    if attached:
        blockers.append(f'{len(attached)} managed policies')
    if inline:
        blockers.append(f'{len(inline)} inline policies')
    if blockers and not force:
        raise ValueError(f'Role {name} has dependencies: {", ".join(blockers)}. Confirm cleanup to delete them first.')
    for profile in profiles:
        if profile.get('InstanceProfileName'):
            _safe_iam(lambda profile=profile: iam.remove_role_from_instance_profile(InstanceProfileName=profile['InstanceProfileName'], RoleName=name))
    for policy in attached:
        if policy.get('PolicyArn'):
            _safe_iam(lambda policy=policy: iam.detach_role_policy(RoleName=name, PolicyArn=policy['PolicyArn']))
    for policy_name in inline:
        _safe_iam(lambda policy_name=policy_name: iam.delete_role_policy(RoleName=name, PolicyName=policy_name))
    _safe_iam(lambda: iam.delete_role(RoleName=name))
    return {'role_name': name, 'deleted': True, 'cleaned': {'instance_profiles': len(profiles), 'attached_policies': len(attached), 'inline_policies': len(inline)}}


def simulate_principal_policy(principal_arn: str, action_names: Any, resource_arns: Any = None) -> dict[str, Any]:
    arn = validate_name(principal_arn, 'Principal ARN')
    if isinstance(action_names, str):
        actions = [item.strip() for item in action_names.replace('\n', ',').split(',') if item.strip()]
    else:
        actions = [str(item).strip() for item in (action_names or []) if str(item).strip()]
    if not actions:
        raise ValueError('At least one action is required')
    if isinstance(resource_arns, str):
        resources = [item.strip() for item in resource_arns.replace('\n', ',').split(',') if item.strip()]
    else:
        resources = [str(item).strip() for item in (resource_arns or []) if str(item).strip()]
    payload: dict[str, Any] = {'PolicySourceArn': arn, 'ActionNames': actions}
    if resources:
        payload['ResourceArns'] = resources
    iam = _iam_client()
    operations = set(getattr(iam.meta.service_model, 'operation_names', []))
    if 'SimulatePrincipalPolicy' not in operations:
        return {'principal_arn': arn, 'actions': actions, 'resources': resources, 'supported': False, 'message': 'SimulatePrincipalPolicy is not available in this local Floci IAM runtime.'}
    response = iam.simulate_principal_policy(**payload)
    return {
        'principal_arn': arn,
        'actions': actions,
        'resources': resources,
        'supported': True,
        'evaluations': response.get('EvaluationResults', []),
    }


def assume_role(
    role_arn: str,
    session_name: str,
    duration_seconds: int | None = None,
    *,
    session_policy: Any = None,
    session_policy_arns: Any = None,
) -> dict[str, Any]:
    arn = validate_name(role_arn, 'Role ARN')
    session = validate_name(session_name, 'Session name')
    payload: dict[str, Any] = {
        'RoleArn': arn,
        'RoleSessionName': session,
    }
    if duration_seconds:
        payload['DurationSeconds'] = int(duration_seconds)
    if session_policy:
        payload['Policy'] = json.dumps(policy_document(session_policy))
    if session_policy_arns:
        payload['PolicyArns'] = policy_arns(session_policy_arns)
    response = _sts_client().assume_role(**payload)
    credentials = response.get('Credentials', {})
    return {
        'role_arn': arn,
        'session_name': session,
        'assumed_role_user': response.get('AssumedRoleUser'),
        'credentials': {
            'access_key_id': credentials.get('AccessKeyId'),
            'secret_access_key': credentials.get('SecretAccessKey'),
            'session_token': credentials.get('SessionToken'),
            'expiration': credentials.get('Expiration'),
        },
    }


def _attach_method(iam, principal_type: PrincipalType):
    return {
        'user': iam.attach_user_policy,
        'group': iam.attach_group_policy,
        'role': iam.attach_role_policy,
    }[principal_type]


def _detach_method(iam, principal_type: PrincipalType):
    return {
        'user': iam.detach_user_policy,
        'group': iam.detach_group_policy,
        'role': iam.detach_role_policy,
    }[principal_type]


def _inline_put_method(iam, principal_type: PrincipalType):
    return {
        'user': iam.put_user_policy,
        'group': iam.put_group_policy,
        'role': iam.put_role_policy,
    }[principal_type]


def _inline_delete_method(iam, principal_type: PrincipalType):
    return {
        'user': iam.delete_user_policy,
        'group': iam.delete_group_policy,
        'role': iam.delete_role_policy,
    }[principal_type]


def _inline_get_method(iam, principal_type: PrincipalType):
    return {
        'user': iam.get_user_policy,
        'group': iam.get_group_policy,
        'role': iam.get_role_policy,
    }[principal_type]


def _principal_arg(principal_type: PrincipalType, principal_name: str) -> dict[str, str]:
    return {
        'user': {'UserName': principal_name},
        'group': {'GroupName': principal_name},
        'role': {'RoleName': principal_name},
    }[principal_type]


def attach_managed_policy(principal_type: str, principal_name: str, policy_arn: str) -> dict[str, Any]:
    clean_type = validate_principal_type(principal_type)
    name = validate_name(principal_name, 'Principal name')
    arn = validate_name(policy_arn, 'Policy ARN')
    iam = _iam_client()
    _attach_method(iam, clean_type)(**_principal_arg(clean_type, name), PolicyArn=arn)
    return {'principal_type': clean_type, 'principal_name': name, 'policy_arn': arn, 'attached': True}


def detach_managed_policy(principal_type: str, principal_name: str, policy_arn: str) -> dict[str, Any]:
    clean_type = validate_principal_type(principal_type)
    name = validate_name(principal_name, 'Principal name')
    arn = validate_name(policy_arn, 'Policy ARN')
    iam = _iam_client()
    _detach_method(iam, clean_type)(**_principal_arg(clean_type, name), PolicyArn=arn)
    return {'principal_type': clean_type, 'principal_name': name, 'policy_arn': arn, 'detached': True}


def put_permissions_boundary(principal_type: str, principal_name: str, policy_arn: str) -> dict[str, Any]:
    clean_type = validate_principal_type(principal_type)
    if clean_type == 'group':
        raise ValueError('Permission boundaries are supported for users and roles only')
    name = validate_name(principal_name, 'Principal name')
    arn = validate_name(policy_arn, 'Policy ARN')
    factory = FlociClientFactory()
    iam = factory.client('iam')
    if clean_type == 'user':
        iam.put_user_permissions_boundary(UserName=name, PermissionsBoundary=arn)
    else:
        iam.put_role_permissions_boundary(RoleName=name, PermissionsBoundary=arn)
    _cache_permissions_boundary(factory, clean_type, name, arn)
    return {'principal_type': clean_type, 'principal_name': name, 'policy_arn': arn, 'saved': True}


def delete_permissions_boundary(principal_type: str, principal_name: str) -> dict[str, Any]:
    clean_type = validate_principal_type(principal_type)
    if clean_type == 'group':
        raise ValueError('Permission boundaries are supported for users and roles only')
    name = validate_name(principal_name, 'Principal name')
    factory = FlociClientFactory()
    iam = factory.client('iam')
    if clean_type == 'user':
        iam.delete_user_permissions_boundary(UserName=name)
    else:
        iam.delete_role_permissions_boundary(RoleName=name)
    _cache_permissions_boundary(factory, clean_type, name, None)
    return {'principal_type': clean_type, 'principal_name': name, 'deleted': True}


def put_inline_policy(principal_type: str, principal_name: str, policy_name: str, document: Any) -> dict[str, Any]:
    clean_type = validate_principal_type(principal_type)
    name = validate_name(principal_name, 'Principal name')
    policy = validate_name(policy_name, 'Policy name')
    doc = policy_document(document)
    iam = _iam_client()
    _inline_put_method(iam, clean_type)(
        **_principal_arg(clean_type, name),
        PolicyName=policy,
        PolicyDocument=json.dumps(doc),
    )
    return {'principal_type': clean_type, 'principal_name': name, 'policy_name': policy, 'saved': True}


def delete_inline_policy(principal_type: str, principal_name: str, policy_name: str) -> dict[str, Any]:
    clean_type = validate_principal_type(principal_type)
    name = validate_name(principal_name, 'Principal name')
    policy = validate_name(policy_name, 'Policy name')
    iam = _iam_client()
    _inline_delete_method(iam, clean_type)(**_principal_arg(clean_type, name), PolicyName=policy)
    return {'principal_type': clean_type, 'principal_name': name, 'policy_name': policy, 'deleted': True}


def get_inline_policy(principal_type: str, principal_name: str, policy_name: str) -> dict[str, Any]:
    clean_type = validate_principal_type(principal_type)
    name = validate_name(principal_name, 'Principal name')
    policy = validate_name(policy_name, 'Policy name')
    iam = _iam_client()
    response = _inline_get_method(iam, clean_type)(**_principal_arg(clean_type, name), PolicyName=policy)
    return {
        'principal_type': clean_type,
        'principal_name': name,
        'policy_name': policy,
        'document': response.get('PolicyDocument'),
    }


def get_managed_policy(policy_arn: str, version_id: str | None = None) -> dict[str, Any]:
    arn = validate_name(policy_arn, 'Policy ARN')
    iam = _iam_client()
    policy = iam.get_policy(PolicyArn=arn)['Policy']
    version = version_id or policy.get('DefaultVersionId')
    document = iam.get_policy_version(PolicyArn=arn, VersionId=version)['PolicyVersion'].get('Document')
    return {
        'name': policy.get('PolicyName'),
        'arn': arn,
        'default_version': policy.get('DefaultVersionId'),
        'version_id': version,
        'document': document,
    }


def create_managed_policy(name: str, document: Any, description: str | None = None, path: str | None = None) -> dict[str, Any]:
    policy_name = validate_name(name, 'Policy name')
    doc = policy_document(document)
    payload: dict[str, Any] = {
        'PolicyName': policy_name,
        'PolicyDocument': json.dumps(doc),
    }
    if description:
        payload['Description'] = description
    if path:
        payload['Path'] = path
    policy = _iam_client().create_policy(**payload)['Policy']
    return {
        'name': policy.get('PolicyName'),
        'arn': policy.get('Arn'),
        'default_version': policy.get('DefaultVersionId'),
    }


def delete_managed_policy(policy_arn: str) -> dict[str, Any]:
    arn = validate_name(policy_arn, 'Policy ARN')
    _iam_client().delete_policy(PolicyArn=arn)
    return {'policy_arn': arn, 'deleted': True}


def update_tags(resource_type: str, resource_name: str, tags: Any = None, *, remove: Any = None) -> dict[str, Any]:
    if resource_type not in {'user', 'role', 'policy'}:
        raise ValueError('Resource type must be user, role, or policy')
    name = validate_name(resource_name, 'Resource name or ARN')
    client = _iam_client()
    if remove not in (None, '', []):
        keys = remove if isinstance(remove, list) else [item.strip() for item in str(remove).split(',') if item.strip()]
        if not keys:
            raise ValueError('At least one tag key is required')
        method = getattr(client, f'untag_{resource_type}')
        argument = 'PolicyArn' if resource_type == 'policy' else f'{resource_type.title()}Name'
        method(**{argument: name, 'TagKeys': keys})
        return {'resource_type': resource_type, 'resource_name': name, 'removed': keys}
    document = policy_document(tags)
    clean_tags = [{'Key': str(key), 'Value': str(value)} for key, value in document.items()]
    method = getattr(client, f'tag_{resource_type}')
    argument = 'PolicyArn' if resource_type == 'policy' else f'{resource_type.title()}Name'
    method(**{argument: name, 'Tags': clean_tags})
    return {'resource_type': resource_type, 'resource_name': name, 'tags': clean_tags}


def create_policy_version(policy_arn: str, document: Any, set_as_default: bool = True) -> dict[str, Any]:
    arn = validate_name(policy_arn, 'Policy ARN')
    doc = policy_document(document)
    response = _iam_client().create_policy_version(
        PolicyArn=arn,
        PolicyDocument=json.dumps(doc),
        SetAsDefault=bool(set_as_default),
    )['PolicyVersion']
    return {
        'arn': arn,
        'version_id': response.get('VersionId'),
        'default': response.get('IsDefaultVersion'),
        'created': response.get('CreateDate'),
    }


def set_default_policy_version(policy_arn: str, version_id: str) -> dict[str, Any]:
    arn = validate_name(policy_arn, 'Policy ARN')
    version = validate_name(version_id, 'Version ID')
    _iam_client().set_default_policy_version(PolicyArn=arn, VersionId=version)
    return {'arn': arn, 'version_id': version, 'default': True}


def delete_policy_version(policy_arn: str, version_id: str) -> dict[str, Any]:
    arn = validate_name(policy_arn, 'Policy ARN')
    version = validate_name(version_id, 'Version ID')
    _iam_client().delete_policy_version(PolicyArn=arn, VersionId=version)
    return {'arn': arn, 'version_id': version, 'deleted': True}


def add_user_to_group(user_name: str, group_name: str) -> dict[str, Any]:
    user = validate_name(user_name, 'User name')
    group = validate_name(group_name, 'Group name')
    _iam_client().add_user_to_group(UserName=user, GroupName=group)
    return {'user_name': user, 'group_name': group, 'added': True}


def remove_user_from_group(user_name: str, group_name: str) -> dict[str, Any]:
    user = validate_name(user_name, 'User name')
    group = validate_name(group_name, 'Group name')
    _iam_client().remove_user_from_group(UserName=user, GroupName=group)
    return {'user_name': user, 'group_name': group, 'removed': True}


def update_role_trust_policy(role_name: str, document: Any) -> dict[str, Any]:
    role = validate_name(role_name, 'Role name')
    doc = policy_document(document)
    _iam_client().update_assume_role_policy(
        RoleName=role,
        PolicyDocument=json.dumps(doc),
    )
    return {'role_name': role, 'saved': True, 'document': doc}


PASSWORD_POLICY_BOOLEANS = {
    'require_symbols': 'RequireSymbols',
    'require_numbers': 'RequireNumbers',
    'require_uppercase_characters': 'RequireUppercaseCharacters',
    'require_lowercase_characters': 'RequireLowercaseCharacters',
    'allow_users_to_change_password': 'AllowUsersToChangePassword',
    'hard_expiry': 'HardExpiry',
}


def get_password_policy() -> dict[str, Any]:
    """Returns the account password policy, or None when the account uses the IAM default."""
    from botocore.exceptions import ClientError
    try:
        policy = _iam_client().get_account_password_policy()['PasswordPolicy']
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') in {'NoSuchEntity', 'NoSuchEntityException'}:
            return {'policy': None}
        raise
    return {'policy': policy}


def _bounded_int(body: dict[str, Any], key: str, low: int, high: int, label: str) -> int | None:
    value = body.get(key)
    if value in (None, '', 0, '0'):
        return None
    if isinstance(value, bool):
        raise ValueError(f'{label} must be a number')
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f'{label} must be a whole number') from exc
    if not low <= number <= high:
        raise ValueError(f'{label} must be from {low} to {high}')
    return number


def update_password_policy(body: dict[str, Any]) -> dict[str, Any]:
    """UpdateAccountPasswordPolicy with the AWS limits checked first. Unset fields take IAM's defaults."""
    kwargs: dict[str, Any] = {}
    length = _bounded_int(body, 'minimum_password_length', 6, 128, 'Minimum password length')
    if length is not None:
        kwargs['MinimumPasswordLength'] = length
    for key, name in PASSWORD_POLICY_BOOLEANS.items():
        if key in body:
            kwargs[name] = body[key] is True
    max_age = _bounded_int(body, 'max_password_age', 1, 1095, 'Password expiration (days)')
    if max_age is not None:
        kwargs['MaxPasswordAge'] = max_age
    reuse = _bounded_int(body, 'password_reuse_prevention', 1, 24, 'Password reuse prevention')
    if reuse is not None:
        kwargs['PasswordReusePrevention'] = reuse
    _iam_client().update_account_password_policy(**kwargs)
    return get_password_policy()


def delete_password_policy() -> dict[str, Any]:
    _iam_client().delete_account_password_policy()
    return {'policy': None, 'deleted': True}


def generate_credential_report() -> dict[str, Any]:
    response = _iam_client().generate_credential_report()
    return {'state': response.get('State'), 'description': response.get('Description') or ''}


def get_credential_report() -> dict[str, Any]:
    """Returns the latest credential report parsed from CSV, or report=None when none was generated."""
    import csv
    import io
    from botocore.exceptions import ClientError
    try:
        response = _iam_client().get_credential_report()
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') in {'ReportNotPresent', 'ReportExpired', 'ReportInProgress'}:
            return {'report': None, 'reason': exc.response['Error']['Code']}
        raise
    content = response.get('Content') or b''
    text = content.decode('utf-8') if isinstance(content, bytes) else str(content)
    rows = list(csv.DictReader(io.StringIO(text)))
    generated = response.get('GeneratedTime')
    return {
        'report': {
            'generated_time': generated.isoformat() if hasattr(generated, 'isoformat') else generated,
            'columns': list(rows[0].keys()) if rows else [],
            'rows': rows,
            'csv': text,
        },
    }
