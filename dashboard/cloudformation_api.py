"""Interactive CloudFormation helpers for local stack workflows."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('cloudformation')


def _required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _template_body(value: Any) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, indent=2)
    return _required(str(value or ''), 'Template body')


def _parameters(value: Any) -> list[dict[str, Any]]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, dict):
        value = [
            {'ParameterKey': key, 'ParameterValue': item}
            for key, item in value.items()
        ]
    if not isinstance(value, list):
        raise ValueError('Parameters must be an object or list')

    parameters = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError('Each parameter must be an object')
        key = item.get('ParameterKey') or item.get('key') or item.get('name')
        if not key:
            raise ValueError('Each parameter needs a ParameterKey')
        parameter = {'ParameterKey': str(key)}
        if item.get('UsePreviousValue') is True:
            parameter['UsePreviousValue'] = True
        else:
            parameter_value = item.get('ParameterValue')
            if parameter_value is None:
                parameter_value = item.get('value')
            parameter['ParameterValue'] = '' if parameter_value is None else str(parameter_value)
        parameters.append(parameter)
    return parameters


def _capabilities(value: Any) -> list[str]:
    if value in (None, '', []):
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError('Capabilities must be a comma-separated string or list')


def validate_template(template_body: Any) -> dict[str, Any]:
    response = _client().validate_template(TemplateBody=_template_body(template_body))
    return {'validation': _clean_response(response)}


def create_stack(
    stack_name: str,
    template_body: Any,
    *,
    parameters: Any = None,
    capabilities: Any = None,
    disable_rollback: bool = False,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'StackName': _required(stack_name, 'Stack name'),
        'TemplateBody': _template_body(template_body),
        'DisableRollback': bool(disable_rollback),
    }
    parsed_parameters = _parameters(parameters)
    parsed_capabilities = _capabilities(capabilities)
    if parsed_parameters:
        kwargs['Parameters'] = parsed_parameters
    if parsed_capabilities:
        kwargs['Capabilities'] = parsed_capabilities

    response = _client().create_stack(**kwargs)
    return {
        'stack_name': kwargs['StackName'],
        'stack_id': response.get('StackId'),
        'response': _clean_response(response),
    }


def update_stack(
    stack_name: str,
    template_body: Any,
    *,
    parameters: Any = None,
    capabilities: Any = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        'StackName': _required(stack_name, 'Stack name'),
        'TemplateBody': _template_body(template_body),
    }
    parsed_parameters = _parameters(parameters)
    parsed_capabilities = _capabilities(capabilities)
    if parsed_parameters:
        kwargs['Parameters'] = parsed_parameters
    if parsed_capabilities:
        kwargs['Capabilities'] = parsed_capabilities

    response = _client().update_stack(**kwargs)
    return {
        'stack_name': kwargs['StackName'],
        'stack_id': response.get('StackId'),
        'response': _clean_response(response),
    }


def delete_stack(stack_name: str) -> dict[str, Any]:
    clean_name = _required(stack_name, 'Stack name')
    response = _client().delete_stack(StackName=clean_name)
    return {
        'stack_name': clean_name,
        'response': _clean_response(response),
    }


def create_change_set(
    stack_name: str,
    change_set_name: str,
    template_body: Any,
    *,
    change_set_type: str = 'UPDATE',
    parameters: Any = None,
    capabilities: Any = None,
) -> dict[str, Any]:
    clean_type = (change_set_type or 'UPDATE').strip().upper()
    if clean_type not in {'CREATE', 'UPDATE'}:
        raise ValueError('Change set type must be CREATE or UPDATE')

    kwargs: dict[str, Any] = {
        'StackName': _required(stack_name, 'Stack name'),
        'ChangeSetName': _required(change_set_name, 'Change set name'),
        'TemplateBody': _template_body(template_body),
        'ChangeSetType': clean_type,
    }
    parsed_parameters = _parameters(parameters)
    parsed_capabilities = _capabilities(capabilities)
    if parsed_parameters:
        kwargs['Parameters'] = parsed_parameters
    if parsed_capabilities:
        kwargs['Capabilities'] = parsed_capabilities

    response = _client().create_change_set(**kwargs)
    return {
        'stack_name': kwargs['StackName'],
        'change_set_name': kwargs['ChangeSetName'],
        'change_set_id': response.get('Id'),
        'stack_id': response.get('StackId'),
        'response': _clean_response(response),
    }


def describe_change_set(stack_name: str, change_set_name: str) -> dict[str, Any]:
    client = _client()
    kwargs = {
        'StackName': _required(stack_name, 'Stack name'),
        'ChangeSetName': _required(change_set_name, 'Change set name'),
    }
    response = client.describe_change_set(**kwargs)
    changes = list(response.get('Changes', []))
    token = response.get('NextToken')
    while token:
        page = client.describe_change_set(**kwargs, NextToken=token)
        changes.extend(page.get('Changes', []))
        token = page.get('NextToken')
    response['Changes'] = changes
    response.pop('NextToken', None)
    return {'change_set': _clean_response(response)}


def execute_change_set(stack_name: str, change_set_name: str) -> dict[str, Any]:
    response = _client().execute_change_set(
        StackName=_required(stack_name, 'Stack name'),
        ChangeSetName=_required(change_set_name, 'Change set name'),
    )
    return {
        'stack_name': stack_name,
        'change_set_name': change_set_name,
        'response': _clean_response(response),
    }


def delete_change_set(stack_name: str, change_set_name: str) -> dict[str, Any]:
    response = _client().delete_change_set(
        StackName=_required(stack_name, 'Stack name'),
        ChangeSetName=_required(change_set_name, 'Change set name'),
    )
    return {
        'stack_name': stack_name,
        'change_set_name': change_set_name,
        'response': _clean_response(response),
    }
