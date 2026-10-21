"""Interactive SSM Parameter Store helpers for local configuration workflows."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory

PARAMETER_TYPES = {'String', 'StringList', 'SecureString'}


def _client():
    return FlociClientFactory().client('ssm')


def _clean_required(value: str, label: str) -> str:
    cleaned = (value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _parameter_type(value: str) -> str:
    parameter_type = (value or 'String').strip()
    if parameter_type not in PARAMETER_TYPES:
        raise ValueError('Parameter type must be String, StringList, or SecureString')
    return parameter_type


def _parameter_value(value: Any) -> str:
    if value in (None, ''):
        raise ValueError('Parameter value is required')
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _json_datetime(value: Any) -> Any:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def _decoded_value(value: str) -> dict[str, Any]:
    parsed: Any = None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        parsed = None
    return {
        'value': value,
        'json': parsed,
        'size_bytes': len(value.encode('utf-8')),
    }


def put_parameter(
    name: str,
    value: Any,
    *,
    parameter_type: str = 'String',
    description: str = '',
    overwrite: bool = False,
    key_id: str = '',
    tier: str = '',
    data_type: str = '',
) -> dict[str, Any]:
    parameter_name = _clean_required(name, 'Parameter name')
    kwargs: dict[str, Any] = {
        'Name': parameter_name,
        'Value': _parameter_value(value),
        'Type': _parameter_type(parameter_type),
        'Overwrite': bool(overwrite),
    }
    if description:
        kwargs['Description'] = description

    if key_id:
        kwargs['KeyId'] = key_id
    if tier:
        kwargs['Tier'] = tier
    if data_type:
        kwargs['DataType'] = data_type

    response = _client().put_parameter(**kwargs)
    return {
        'name': parameter_name,
        'type': kwargs['Type'],
        'version': response.get('Version'),
        'tier': response.get('Tier'),
    }


def get_parameter(name: str, *, with_decryption: bool = True) -> dict[str, Any]:
    parameter_name = _clean_required(name, 'Parameter name')
    response = _client().get_parameter(
        Name=parameter_name,
        WithDecryption=bool(with_decryption),
    )
    parameter = response.get('Parameter') or {}
    decoded = _decoded_value(parameter.get('Value') or '')
    return {
        'name': parameter.get('Name') or parameter_name,
        'type': parameter.get('Type'),
        'version': parameter.get('Version'),
        'selector': parameter.get('Selector'),
        'source_result': parameter.get('SourceResult'),
        'last_modified': _json_datetime(parameter.get('LastModifiedDate')),
        'arn': parameter.get('ARN'),
        'data_type': parameter.get('DataType'),
        **decoded,
    }


def delete_parameter(name: str) -> dict[str, Any]:
    parameter_name = _clean_required(name, 'Parameter name')
    response = _client().delete_parameter(Name=parameter_name)
    return {
        'name': parameter_name,
        'response': response,
    }


def parameter_history(name: str) -> dict[str, Any]:
    from .aws import _clean_response, _paginate
    versions = _paginate(_client(), 'get_parameter_history', 'Parameters', Name=_clean_required(name, 'Parameter name'), WithDecryption=False)
    # History is metadata only. Values are retrieved explicitly by name:version.
    return {'parameters': _clean_response([{key: value for key, value in version.items() if key != 'Value'} for version in versions])}


def parameter_tags(name: str, *, tags=None, tag_keys=None) -> dict[str, Any]:
    client = _client()
    kwargs = {'ResourceType': 'Parameter', 'ResourceId': _clean_required(name, 'Parameter name')}
    if tags is not None:
        if not isinstance(tags, list) or not tags or any(not isinstance(tag, dict) or not tag.get('Key') for tag in tags):
            raise ValueError('Tags must contain a key')
        client.add_tags_to_resource(**kwargs, Tags=tags)
    elif tag_keys is not None:
        if not isinstance(tag_keys, list) or not tag_keys:
            raise ValueError('Tag keys are required')
        client.remove_tags_from_resource(**kwargs, TagKeys=tag_keys)
    from .aws import _clean_response
    return {'tags': _clean_response(client.list_tags_for_resource(**kwargs).get('TagList', []))}


def create_document(name: str, content: str, document_type: str = 'Command') -> dict[str, Any]:
    from .aws import _clean_response
    json.loads(content)
    if document_type not in {'Command', 'Automation', 'Package'}:
        raise ValueError('Unsupported document type')
    response = _client().create_document(Name=_clean_required(name, 'Document name'), Content=content, DocumentType=document_type, DocumentFormat='JSON')
    return _clean_response(response.get('DocumentDescription', {}))


def delete_document(name: str) -> dict[str, Any]:
    _client().delete_document(Name=_clean_required(name, 'Document name'))
    return {'name': name, 'deleted': True}
