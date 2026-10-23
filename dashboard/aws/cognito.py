"""Cognito inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _cognito_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def cognito_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    cognito = factory.client('cognito-idp')
    pools = _safe_value(lambda: core._paginate(cognito, 'list_user_pools', 'UserPools', MaxResults=60), [])

    def pool_detail(pool: dict[str, Any]) -> dict[str, Any]:
        pool_id = pool.get('Id')
        details = _cognito_optional(
            lambda: cognito.describe_user_pool(UserPoolId=pool_id).get('UserPool', {}),
            {'ResourceNotFoundException'},
        )
        # The ARN Floci reports (it carries the real account), not one built from a fixed account.
        pool_arn = details.get('Arn') if isinstance(details, dict) else None
        clients = _cognito_optional(
            lambda: core._paginate(cognito, 'list_user_pool_clients', 'UserPoolClients', UserPoolId=pool_id),
            {'ResourceNotFoundException'},
        )
        resource_servers = _cognito_optional(
            lambda: core._paginate(cognito, 'list_resource_servers', 'ResourceServers', UserPoolId=pool_id, MaxResults=50),
            {'ResourceNotFoundException'},
        )
        users = _cognito_optional(
            lambda: core._paginate(cognito, 'list_users', 'Users', UserPoolId=pool_id),
            {'ResourceNotFoundException'},
        )
        groups = _cognito_optional(
            lambda: core._paginate(cognito, 'list_groups', 'Groups', UserPoolId=pool_id),
            {'ResourceNotFoundException'},
        )
        group_details = []
        if isinstance(groups, list):
            for group in groups:
                group_name = group.get('GroupName')
                group_users = _cognito_optional(
                    lambda group_name=group_name: core._paginate(
                        cognito,
                        'list_users_in_group',
                        'Users',
                        UserPoolId=pool_id,
                        GroupName=group_name,
                    ),
                    {'ResourceNotFoundException', 'GroupNotFoundException'},
                )
                group_details.append({
                    **group,
                    'users': group_users,
                    'user_count': len(group_users) if isinstance(group_users, list) else 0,
                })

        client_details = []
        if isinstance(clients, list):
            for client in clients:
                client_id = client.get('ClientId')
                client_detail = _cognito_optional(
                    lambda: cognito.describe_user_pool_client(
                        UserPoolId=pool_id,
                        ClientId=client_id,
                    ).get('UserPoolClient', {}),
                    {'ResourceNotFoundException'},
                )
                if isinstance(client_detail, dict):
                    client_detail.pop('ClientSecret', None)
                client_details.append({
                    'name': client.get('ClientName') or client_id,
                    'client_id': client_id,
                    'details': client_detail,
                })

        user_details = []
        if isinstance(users, list):
            for user in users:
                username = user.get('Username')
                groups_for_user = _cognito_optional(
                    lambda: cognito.admin_list_groups_for_user(
                        UserPoolId=pool_id,
                        Username=username,
                    ).get('Groups', []),
                    {'ResourceNotFoundException', 'UserNotFoundException'},
                )
                user_details.append({
                    'name': username,
                    'status': user.get('UserStatus'),
                    'enabled': user.get('Enabled'),
                    'created': user.get('UserCreateDate'),
                    'modified': user.get('UserLastModifiedDate'),
                    'attributes': user.get('Attributes', []),
                    'groups': groups_for_user,
                })

        return {
            'name': pool.get('Name') or pool_id,
            'id': pool_id,
            'arn': pool_arn,
            'status': pool.get('Status'),
            'created': pool.get('CreationDate'),
            'last_modified': pool.get('LastModifiedDate'),
            'details': details,
            'tags': _cognito_optional(
                lambda: cognito.list_tags_for_resource(ResourceArn=pool_arn).get('Tags', {}),
                {'ResourceNotFoundException'},
            ) if pool_arn else None,
            'clients': client_details,
            'resource_servers': resource_servers,
            'users': user_details,
            'groups': group_details if isinstance(groups, list) else groups,
            # Browser-facing links use the public endpoint, not the container hostname.
            'discovery_url': f'{core.public_endpoint_url()}/{pool_id}/.well-known/openid-configuration' if pool_id else None,
            'jwks_url': f'{core.public_endpoint_url()}/{pool_id}/.well-known/jwks.json' if pool_id else None,
            'oauth_token_url': f'{factory.endpoint_url.rstrip("/")}/cognito-idp/oauth2/token',
            'oauth_userinfo_url': f'{factory.endpoint_url.rstrip("/")}/cognito-idp/oauth2/userInfo',
        }

    detailed_pools = [pool_detail(pool) for pool in pools]

    return {
        'summary': {
            'user_pools': len(detailed_pools),
            'clients': sum(len(pool.get('clients') or []) for pool in detailed_pools),
            'users': sum(len(pool.get('users') or []) for pool in detailed_pools),
            'groups': sum(
                len(pool.get('groups') or [])
                for pool in detailed_pools
                if isinstance(pool.get('groups'), list)
            ),
            'resource_servers': sum(
                len(pool.get('resource_servers') or [])
                for pool in detailed_pools
                if isinstance(pool.get('resource_servers'), list)
            ),
        },
        'user_pools': detailed_pools,
        'supported': [
            'CreateUserPool',
            'DescribeUserPool',
            'ListUserPools',
            'UpdateUserPool',
            'DeleteUserPool',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
            'CreateUserPoolClient',
            'DescribeUserPoolClient',
            'ListUserPoolClients',
            'DeleteUserPoolClient',
            'CreateResourceServer',
            'DescribeResourceServer',
            'ListResourceServers',
            'DeleteResourceServer',
            'AdminCreateUser',
            'AdminGetUser',
            'AdminDeleteUser',
            'AdminSetUserPassword',
            'AdminUpdateUserAttributes',
            'AdminConfirmSignUp',
            'SignUp',
            'ConfirmSignUp',
            'GetUser',
            'UpdateUserAttributes',
            'ChangePassword',
            'ForgotPassword',
            'ConfirmForgotPassword',
            'InitiateAuth',
            'AdminInitiateAuth',
            'RespondToAuthChallenge',
            'ListUsers',
            'CreateGroup',
            'GetGroup',
            'ListGroups',
            'DeleteGroup',
            'UpdateGroup',
            'AdminAddUserToGroup',
            'AdminRemoveUserFromGroup',
            'AdminListGroupsForUser',
            'ListUsersInGroup',
        ],
        'endpoints': [
            'GET /{userPoolId}/.well-known/openid-configuration',
            'GET /{userPoolId}/.well-known/jwks.json',
            'POST /cognito-idp/oauth2/token',
            'GET /cognito-idp/oauth2/userInfo',
        ],
        'notes': [
            'Client secrets are intentionally omitted from dashboard output.',
            'floci:override-id can pin a user pool ID at creation time and is stripped from persisted tags.',
            'OAuth client_credentials is emulator-friendly and does not require a Cognito domain.',
            'Floci 1.5.28 aligns sign-up confirmation, CUSTOM_AUTH trigger failures, and AdminGetUser lookup behavior with AWS.',
            'Floci 1.5.29 adds Cognito overrides for client ID and secret.',
            'Floci 1.5.26 aligns user pool client configuration APIs, validates missing clients more closely, and tightens password recovery behavior.',
            'Floci 1.5.21 adds the OAuth2 userInfo endpoint plus custom schema attributes and attribute deletion APIs.',
            'Floci 1.5.24 adds a verification code store and dispatcher so email/SMS signup and recovery codes can be inspected locally through SNS/SES-backed flows.',
            'Floci 1.5.25 includes USERNAME in SRP PASSWORD_VERIFIER challenges for closer AWS client compatibility.',
            'Tokens use the local emulator base URL plus pool ID as issuer.',
            'Floci 1.5.19 provisions UserPool and UserPoolClient resources from CloudFormation and includes user attributes in ID token claims.',
            'Floci 1.5.17 fires PreSignUp and PostConfirmation Lambda triggers for local auth flows.',
        ],
    }


def list_cognito_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    cognito = factory.client('cognito-idp')
    resources: list[dict[str, Any]] = []
    pools = _safe_value(lambda: core._paginate(cognito, 'list_user_pools', 'UserPools', MaxResults=60), [])

    for pool in pools:
        pool_id = pool.get('Id')
        resources.append({
            'type': 'user_pool',
            'id': pool_id,
            'name': pool.get('Name'),
        })
        resources.extend(
            {
                'type': 'user_pool_client',
                'pool_id': pool_id,
                'id': client.get('ClientId'),
                'name': client.get('ClientName'),
            }
            for client in _safe_value(lambda pool_id=pool_id: core._paginate(cognito, 'list_user_pool_clients', 'UserPoolClients', UserPoolId=pool_id), [])
        )
        resources.extend(
            {
                'type': 'user',
                'pool_id': pool_id,
                'name': user.get('Username'),
                'status': user.get('UserStatus'),
            }
            for user in _safe_value(lambda pool_id=pool_id: core._paginate(cognito, 'list_users', 'Users', UserPoolId=pool_id), [])
        )
        resources.extend(
            {
                'type': 'group',
                'pool_id': pool_id,
                'name': group.get('GroupName'),
            }
            for group in _safe_value(lambda pool_id=pool_id: core._paginate(cognito, 'list_groups', 'Groups', UserPoolId=pool_id), [])
        )

    return resources
