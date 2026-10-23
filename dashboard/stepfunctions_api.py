"""Interactive Step Functions helpers for the execution workbench."""

from __future__ import annotations

import json
from typing import Any

from .aws import FlociClientFactory


def _stepfunctions_client():
    return FlociClientFactory().client('stepfunctions')


def validate_state_machine_arn(arn: str) -> str:
    value = (arn or '').strip()
    if not value or ':states:' not in value or ':stateMachine:' not in value:
        raise ValueError('A valid Step Functions state machine ARN is required')
    return value


def validate_execution_arn(arn: str) -> str:
    value = (arn or '').strip()
    if not value or ':states:' not in value or ':execution:' not in value:
        raise ValueError('A valid Step Functions execution ARN is required')
    return value


def _json_input(value: Any) -> str:
    if value in (None, ''):
        return '{}'
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError('Execution input must be valid JSON') from exc
        return value
    return json.dumps(value)


def start_execution(
    state_machine_arn: str,
    execution_input: Any = None,
    *,
    name: str | None = None,
    trace_header: str | None = None,
) -> dict[str, Any]:
    arn = validate_state_machine_arn(state_machine_arn)
    payload: dict[str, Any] = {
        'stateMachineArn': arn,
        'input': _json_input(execution_input),
    }
    if name:
        payload['name'] = name.strip()
    if trace_header:
        payload['traceHeader'] = trace_header.strip()

    response = _stepfunctions_client().start_execution(**payload)
    return {
        'state_machine_arn': arn,
        'execution_arn': response.get('executionArn'),
        'start_date': response.get('startDate'),
    }


def publish_state_machine_version(
    state_machine_arn: str,
    *,
    revision_id: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    arn = validate_state_machine_arn(state_machine_arn)
    payload: dict[str, Any] = {'stateMachineArn': arn}
    if revision_id:
        payload['revisionId'] = revision_id.strip()
    if description:
        payload['description'] = description.strip()

    response = _stepfunctions_client().publish_state_machine_version(**payload)
    return {
        'state_machine_arn': arn,
        'state_machine_version_arn': response.get('stateMachineVersionArn'),
        'creation_date': response.get('creationDate'),
    }


def delete_state_machine_version(state_machine_version_arn: str) -> dict[str, Any]:
    arn = validate_state_machine_arn(state_machine_version_arn)
    response = _stepfunctions_client().delete_state_machine_version(stateMachineVersionArn=arn)
    return {
        'state_machine_version_arn': arn,
        'response': response,
    }


def stop_execution(
    execution_arn: str,
    *,
    error: str | None = None,
    cause: str | None = None,
) -> dict[str, Any]:
    arn = validate_execution_arn(execution_arn)
    payload: dict[str, Any] = {'executionArn': arn}
    if error:
        payload['error'] = error.strip()
    if cause:
        payload['cause'] = cause.strip()

    response = _stepfunctions_client().stop_execution(**payload)
    return {
        'execution_arn': arn,
        'stop_date': response.get('stopDate'),
    }


def describe_execution(execution_arn: str) -> dict[str, Any]:
    from .aws import _clean_response
    arn = validate_execution_arn(execution_arn)
    response = _stepfunctions_client().describe_execution(executionArn=arn)
    return _clean_response(response)


def get_execution_history(execution_arn: str, *, max_results: int = 100, reverse_order: bool = False, next_token: str = '') -> dict[str, Any]:
    from .aws import _clean_response
    arn = validate_execution_arn(execution_arn)
    payload = {'executionArn': arn, 'maxResults': max_results, 'reverseOrder': reverse_order}
    if next_token:
        payload['nextToken'] = next_token
    response = _stepfunctions_client().get_execution_history(**payload)
    return {
        'execution_arn': arn,
        'events': _clean_response(response.get('events', [])),
        'next_token': response.get('nextToken'),
    }


def describe_state_machine(state_machine_arn: str) -> dict[str, Any]:
    from .aws import _clean_response
    arn = validate_state_machine_arn(state_machine_arn)
    response = _stepfunctions_client().describe_state_machine(stateMachineArn=arn)
    return _clean_response(response)


def create_state_machine(
    name: str,
    definition: Any,
    role_arn: str,
    *,
    state_machine_type: str = 'STANDARD',
    logging_configuration: Any = None,
    tracing_configuration: Any = None,
    tags: Any = None,
) -> dict[str, Any]:
    from .aws import _clean_response
    clean_name = (name or '').strip()
    if not clean_name:
        raise ValueError('State machine name is required')
    clean_role = (role_arn or '').strip()
    if not clean_role:
        raise ValueError('Role ARN is required')

    clean_def = _json_input(definition)
    kwargs: dict[str, Any] = {
        'name': clean_name,
        'definition': clean_def,
        'roleArn': clean_role,
        'type': state_machine_type.strip().upper() if state_machine_type else 'STANDARD',
    }
    if logging_configuration and isinstance(logging_configuration, dict):
        kwargs['loggingConfiguration'] = logging_configuration
    if tracing_configuration and isinstance(tracing_configuration, dict):
        kwargs['tracingConfiguration'] = tracing_configuration
    if tags and isinstance(tags, list):
        kwargs['tags'] = tags

    response = _stepfunctions_client().create_state_machine(**kwargs)
    return {
        'state_machine_arn': response.get('stateMachineArn'),
        'creation_date': response.get('creationDate'),
        'response': _clean_response(response),
    }


def delete_state_machine(state_machine_arn: str) -> dict[str, Any]:
    arn = validate_state_machine_arn(state_machine_arn)
    response = _stepfunctions_client().delete_state_machine(stateMachineArn=arn)
    return {
        'state_machine_arn': arn,
        'deleted': True,
        'response': response,
    }



def update_state_machine(state_machine_arn: str, definition: Any, role_arn: str = '') -> dict[str, Any]:
    from .aws import _clean_response
    value = json.loads(definition) if isinstance(definition, str) else definition
    if not isinstance(value, dict) or not isinstance(value.get('States'), dict) or value.get('StartAt') not in value['States']:
        raise ValueError('Definition must contain States and a valid StartAt state')
    payload = {'stateMachineArn': validate_state_machine_arn(state_machine_arn), 'definition': json.dumps(value)}
    if role_arn:
        payload['roleArn'] = role_arn
    return _clean_response(_stepfunctions_client().update_state_machine(**payload))


def _iso(value: Any) -> Any:
    return value.isoformat() if hasattr(value, 'isoformat') else value


def list_versions_and_aliases(state_machine_arn: str) -> dict[str, Any]:
    arn = validate_state_machine_arn(state_machine_arn)
    client = _stepfunctions_client()
    versions = []
    for item in client.list_state_machine_versions(stateMachineArn=arn).get('stateMachineVersions', []):
        detail = client.describe_state_machine(stateMachineArn=item['stateMachineVersionArn'])
        versions.append({
            'arn': item['stateMachineVersionArn'],
            'version': item['stateMachineVersionArn'].rsplit(':', 1)[-1],
            'description': detail.get('description') or '',
            'revision_id': detail.get('revisionId') or '',
            'creation_date': _iso(item.get('creationDate')),
        })
    versions.sort(key=lambda item: int(item['version']) if item['version'].isdigit() else 0, reverse=True)
    aliases = []
    for item in client.list_state_machine_aliases(stateMachineArn=arn).get('stateMachineAliases', []):
        detail = client.describe_state_machine_alias(stateMachineAliasArn=item['stateMachineAliasArn'])
        aliases.append({
            'arn': item['stateMachineAliasArn'],
            'name': detail.get('name'),
            'description': detail.get('description') or '',
            'routing': [{'version_arn': route['stateMachineVersionArn'], 'weight': route['weight']}
                        for route in detail.get('routingConfiguration', [])],
            'creation_date': _iso(detail.get('creationDate')),
            'update_date': _iso(detail.get('updateDate')),
        })
    return {'state_machine_arn': arn, 'versions': versions, 'aliases': aliases}


def _routing(routing: Any) -> list[dict[str, Any]]:
    """An alias routes to one version, or splits traffic between two with weights adding up to 100."""
    if not isinstance(routing, list) or not 1 <= len(routing) <= 2:
        raise ValueError('Route the alias to one version, or to two versions with weights')
    result = []
    for route in routing:
        if not isinstance(route, dict):
            raise ValueError('Each route needs a version and a weight')
        version = validate_state_machine_arn(route.get('version_arn', ''))
        try:
            weight = int(route.get('weight'))
        except (TypeError, ValueError) as exc:
            raise ValueError('Weights must be whole numbers from 0 to 100') from exc
        if not 0 <= weight <= 100:
            raise ValueError('Weights must be whole numbers from 0 to 100')
        result.append({'stateMachineVersionArn': version, 'weight': weight})
    if sum(route['weight'] for route in result) != 100:
        raise ValueError('Weights must add up to 100')
    if len({route['stateMachineVersionArn'] for route in result}) != len(result):
        raise ValueError('Choose two different versions')
    return result


def create_state_machine_alias(name: str, routing: Any, description: str = '') -> dict[str, Any]:
    clean = (name or '').strip()
    if not clean or len(clean) > 80 or clean.isdigit():
        raise ValueError("Alias name is required, up to 80 characters, and can't be only digits")
    kwargs: dict[str, Any] = {'name': clean, 'routingConfiguration': _routing(routing)}
    if description:
        kwargs['description'] = description
    response = _stepfunctions_client().create_state_machine_alias(**kwargs)
    return {'alias_arn': response.get('stateMachineAliasArn')}


def update_state_machine_alias(alias_arn: str, routing: Any, description: str | None = None) -> dict[str, Any]:
    arn = validate_state_machine_arn(alias_arn)
    kwargs: dict[str, Any] = {'stateMachineAliasArn': arn, 'routingConfiguration': _routing(routing)}
    if description is not None:
        kwargs['description'] = description
    _stepfunctions_client().update_state_machine_alias(**kwargs)
    return {'alias_arn': arn}


def delete_state_machine_alias(alias_arn: str) -> dict[str, Any]:
    arn = validate_state_machine_arn(alias_arn)
    _stepfunctions_client().delete_state_machine_alias(stateMachineAliasArn=arn)
    return {'alias_arn': arn, 'deleted': True}
