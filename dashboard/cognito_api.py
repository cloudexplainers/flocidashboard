"""Interactive Cognito helpers for local auth workflows."""

from __future__ import annotations

import base64
import json
import urllib.parse
import urllib.request
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('cognito-idp')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _json_object(value: Any, label: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    if value in (None, ''):
        return default or {}
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    return value


def _string_list(value: Any) -> list[str]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace('\n', ',').split(',') if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError('Value must be a comma-separated string or list')


def _attributes(value: Any) -> list[dict[str, str]]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        value = [{'Name': key, 'Value': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Attributes must be an object or list')

    attributes = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each attribute must be an object')
        name = item.get('Name') or item.get('name')
        if not name:
            raise ValueError('Each attribute needs a Name')
        item_value = item.get('Value')
        if item_value is None:
            item_value = item.get('value')
        attributes.append({'Name': str(name), 'Value': '' if item_value is None else str(item_value)})
    return attributes


def _scopes(value: Any) -> list[dict[str, str]]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        value = [{'ScopeName': key, 'ScopeDescription': item} for key, item in value.items()]
    if not isinstance(value, list):
        raise ValueError('Scopes must be an object or list')

    scopes = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each scope must be an object')
        name = item.get('ScopeName') or item.get('name')
        if not name:
            raise ValueError('Each scope needs a ScopeName')
        description = item.get('ScopeDescription')
        if description is None:
            description = item.get('description') or name
        scopes.append({'ScopeName': str(name), 'ScopeDescription': str(description)})
    return scopes


def create_user_pool(name: str, *, tags: Any = None) -> dict[str, Any]:
    kwargs: dict[str, Any] = {'PoolName': _required(name, 'Pool name')}
    parsed_tags = _json_object(tags, 'Tags') if tags not in (None, '') else {}
    if parsed_tags:
        kwargs['UserPoolTags'] = parsed_tags
    response = _client().create_user_pool(**kwargs)
    pool = response.get('UserPool', {})
    return {
        'pool_id': pool.get('Id'),
        'name': pool.get('Name') or kwargs['PoolName'],
        'user_pool': _clean_response(pool),
    }


def delete_user_pool(user_pool_id: str) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    response = _client().delete_user_pool(UserPoolId=clean_pool)
    return {'user_pool_id': clean_pool, 'response': _clean_response(response)}


def create_user_pool_client(
    user_pool_id: str,
    name: str,
    *,
    generate_secret: bool = True,
    allowed_oauth_scopes: Any = None,
    explicit_auth_flows: Any = None,
) -> dict[str, Any]:
    scopes = _string_list(allowed_oauth_scopes)
    kwargs: dict[str, Any] = {
        'UserPoolId': _required(user_pool_id, 'User pool ID'),
        'ClientName': _required(name, 'Client name'),
        'GenerateSecret': bool(generate_secret),
    }
    if explicit_auth_flows is not None:
        flows = _string_list(explicit_auth_flows)
        if not flows:
            raise ValueError('At least one explicit authentication flow is required')
        kwargs['ExplicitAuthFlows'] = flows
    if scopes:
        kwargs.update({
            'AllowedOAuthFlowsUserPoolClient': True,
            'AllowedOAuthFlows': ['client_credentials'],
            'AllowedOAuthScopes': scopes,
        })
    response = _client().create_user_pool_client(**kwargs)
    client = response.get('UserPoolClient', {})
    return {
        'user_pool_id': kwargs['UserPoolId'],
        'client_id': client.get('ClientId'),
        'client_secret': client.get('ClientSecret'),
        'client': _clean_response(client),
    }


def delete_user_pool_client(user_pool_id: str, client_id: str) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    clean_client = _required(client_id, 'Client ID')
    response = _client().delete_user_pool_client(UserPoolId=clean_pool, ClientId=clean_client)
    return {'user_pool_id': clean_pool, 'client_id': clean_client, 'response': _clean_response(response)}


def create_resource_server(user_pool_id: str, identifier: str, name: str, *, scopes: Any = None) -> dict[str, Any]:
    response = _client().create_resource_server(
        UserPoolId=_required(user_pool_id, 'User pool ID'),
        Identifier=_required(identifier, 'Identifier'),
        Name=_required(name, 'Name'),
        Scopes=_scopes(scopes),
    )
    return {'resource_server': _clean_response(response.get('ResourceServer', {}))}


def delete_resource_server(user_pool_id: str, identifier: str) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    clean_identifier = _required(identifier, 'Identifier')
    response = _client().delete_resource_server(UserPoolId=clean_pool, Identifier=clean_identifier)
    return {'user_pool_id': clean_pool, 'identifier': clean_identifier, 'response': _clean_response(response)}


def admin_create_user(
    user_pool_id: str,
    username: str,
    *,
    temporary_password: str = '',
    attributes: Any = None,
    message_action: str = 'SUPPRESS',
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'UserPoolId': _required(user_pool_id, 'User pool ID'),
        'Username': _required(username, 'Username'),
    }
    if temporary_password:
        kwargs['TemporaryPassword'] = temporary_password
    clean_action = (message_action or '').strip().upper()
    if clean_action:
        kwargs['MessageAction'] = clean_action
    parsed_attributes = _attributes(attributes)
    if parsed_attributes:
        kwargs['UserAttributes'] = parsed_attributes
    response = _client().admin_create_user(**kwargs)
    return {'user': _clean_response(response.get('User', {}))}


def admin_delete_user(user_pool_id: str, username: str) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    clean_username = _required(username, 'Username')
    response = _client().admin_delete_user(UserPoolId=clean_pool, Username=clean_username)
    return {'user_pool_id': clean_pool, 'username': clean_username, 'response': _clean_response(response)}


def admin_set_user_password(user_pool_id: str, username: str, password: str, *, permanent: bool = True) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    clean_username = _required(username, 'Username')
    response = _client().admin_set_user_password(
        UserPoolId=clean_pool,
        Username=clean_username,
        Password=_required(password, 'Password'),
        Permanent=bool(permanent),
    )
    return {'user_pool_id': clean_pool, 'username': clean_username, 'permanent': bool(permanent), 'response': _clean_response(response)}


def create_group(user_pool_id: str, group_name: str, *, description: str = '') -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'UserPoolId': _required(user_pool_id, 'User pool ID'),
        'GroupName': _required(group_name, 'Group name'),
    }
    if description:
        kwargs['Description'] = description
    response = _client().create_group(**kwargs)
    return {'group': _clean_response(response.get('Group', {}))}


def delete_group(user_pool_id: str, group_name: str) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    clean_group = _required(group_name, 'Group name')
    response = _client().delete_group(UserPoolId=clean_pool, GroupName=clean_group)
    return {'user_pool_id': clean_pool, 'group_name': clean_group, 'response': _clean_response(response)}


def add_user_to_group(user_pool_id: str, username: str, group_name: str) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    clean_username = _required(username, 'Username')
    clean_group = _required(group_name, 'Group name')
    response = _client().admin_add_user_to_group(UserPoolId=clean_pool, Username=clean_username, GroupName=clean_group)
    return {'user_pool_id': clean_pool, 'username': clean_username, 'group_name': clean_group, 'response': _clean_response(response)}


def remove_user_from_group(user_pool_id: str, username: str, group_name: str) -> dict[str, Any]:
    clean_pool = _required(user_pool_id, 'User pool ID')
    clean_username = _required(username, 'Username')
    clean_group = _required(group_name, 'Group name')
    response = _client().admin_remove_user_from_group(UserPoolId=clean_pool, Username=clean_username, GroupName=clean_group)
    return {'user_pool_id': clean_pool, 'username': clean_username, 'group_name': clean_group, 'response': _clean_response(response)}


def initiate_auth(client_id: str, username: str, password: str, *, auth_flow: str = 'USER_PASSWORD_AUTH') -> dict[str, Any]:
    response = _client().initiate_auth(
        AuthFlow=(auth_flow or 'USER_PASSWORD_AUTH').strip().upper(),
        ClientId=_required(client_id, 'Client ID'),
        AuthParameters={
            'USERNAME': _required(username, 'Username'),
            'PASSWORD': _required(password, 'Password'),
        },
    )
    return _clean_response(response)


def global_sign_out(access_token: str) -> dict[str, Any]:
    response = _client().global_sign_out(AccessToken=_required(access_token, 'Access token'))
    return {'signed_out': True, 'response': _clean_response(response)}


def revoke_token(client_id: str, token: str, *, client_secret: str = '') -> dict[str, Any]:
    kwargs = {
        'ClientId': _required(client_id, 'Client ID'),
        'Token': _required(token, 'Refresh token'),
    }
    if client_secret:
        kwargs['ClientSecret'] = client_secret
    response = _client().revoke_token(**kwargs)
    return {'revoked': True, 'response': _clean_response(response)}


def oauth_client_credentials(client_id: str, client_secret: str, *, scope: str = '') -> dict[str, Any]:
    factory = FlociClientFactory()
    data = {
        'grant_type': 'client_credentials',
        'client_id': _required(client_id, 'Client ID'),
    }
    if client_secret:
        data['client_secret'] = client_secret
    if scope:
        data['scope'] = scope
    encoded = urllib.parse.urlencode(data).encode('utf-8')
    request = urllib.request.Request(
        f'{factory.endpoint_url.rstrip("/")}/cognito-idp/oauth2/token',
        data=encoded,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        method='POST',
    )
    if client_secret:
        token = base64.b64encode(f'{client_id}:{client_secret}'.encode('utf-8')).decode('ascii')
        request.add_header('Authorization', f'Basic {token}')

    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode('utf-8')
    return json.loads(body)


def change_user_state(user_pool_id: str, username: str, action: str) -> dict[str, Any]:
    operations = {'confirm': 'admin_confirm_sign_up', 'enable': 'admin_enable_user', 'disable': 'admin_disable_user'}
    if action not in operations:
        raise ValueError('Action must be confirm, enable, or disable')
    response = getattr(_client(), operations[action])(
        UserPoolId=_required(user_pool_id, 'User pool ID'), Username=_required(username, 'Username'),
    )
    return {'username': username, 'action': action, 'response': _clean_response(response)}
