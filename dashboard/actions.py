"""Shared action metadata and HTTP helpers for interactive service workbenches."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError
from django.http import JsonResponse

ActionKind = Literal['read', 'create', 'update', 'delete', 'execute']
ActionSafety = Literal['safe', 'mutating', 'destructive']


@dataclass(frozen=True)
class ActionField:
    name: str
    label: str
    required: bool = False
    field_type: str = 'string'
    help_text: str = ''
    # Allowed values, shown as a select (AWS enum values).
    options: tuple[str, ...] = ()
    # Value the form starts with. It is sent unless changed, so the form shows what the
    # backend will use instead of hiding a default.
    default: str | int | bool | None = None


@dataclass(frozen=True)
class ServiceAction:
    name: str
    label: str
    method: str
    path: str
    kind: ActionKind
    safety: ActionSafety = 'mutating'
    description: str = ''
    fields: tuple[ActionField, ...] = field(default_factory=tuple)
    confirm: str = ''
    success_message: str = ''

    def as_dict(self) -> dict:
        data = asdict(self)
        data['fields'] = [asdict(field) for field in self.fields]
        return data


def action(
    name: str,
    label: str,
    method: str,
    path: str,
    kind: ActionKind,
    *,
    safety: ActionSafety = 'mutating',
    description: str = '',
    fields: tuple[ActionField, ...] = (),
    confirm: str = '',
    success_message: str = '',
) -> ServiceAction:
    return ServiceAction(
        name=name,
        label=label,
        method=method,
        path=path,
        kind=kind,
        safety=safety,
        description=description,
        fields=fields,
        confirm=confirm,
        success_message=success_message,
    )


def field(
    name: str,
    label: str,
    *,
    required: bool = False,
    field_type: str = 'string',
    help_text: str = '',
    options: tuple[str, ...] = (),
    default: str | int | bool | None = None,
) -> ActionField:
    return ActionField(
        name=name,
        label=label,
        required=required,
        field_type=field_type,
        help_text=help_text,
        options=tuple(options),
        default=default,
    )


def parse_json_body(request) -> dict:
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError as exc:
        raise ValueError('Invalid JSON body') from exc
    if not isinstance(body, dict):
        raise ValueError('Invalid JSON body')
    return body


def operation_label(operation: str) -> str:
    return str(operation or '').replace('_', ' ').strip().capitalize()


def error_payload(exc: Exception, *, service: str = '', operation: str = '') -> dict:
    payload: dict = {
        'ok': False,
        'error': str(exc),
        'service': service,
        'operation': operation,
        'operation_label': operation_label(operation),
        'type': exc.__class__.__name__,
    }
    if isinstance(exc, ClientError):
        error = exc.response.get('Error', {})
        payload['error'] = error.get('Message') or str(exc)
        payload['code'] = error.get('Code')
    return {key: value for key, value in payload.items() if value is not None and value != ''}


NOT_FOUND_CODES = {
    '404',
    'AWS.SimpleQueueService.NonExistentQueue',
    'DBClusterNotFoundFault',
    'DBInstanceNotFound',
    'DBInstanceNotFoundFault',
    'EntityNotFoundException',
    'NoSuchBucket',
    'NoSuchKey',
    'NotFound',
    'NotFoundException',
    'QueueDoesNotExist',
    'ResourceNotFoundException',
    'ResourceNotFoundFault',
    'StackNotFoundException',
}

BAD_REQUEST_CODES = {
    'BadRequest',
    'BadRequestException',
    'InvalidParameter',
    'InvalidParameterException',
    'InvalidParameterValue',
    'InvalidParameterValueException',
    'InvalidRequest',
    'InvalidRequestException',
    'MalformedPolicyDocument',
    'OperationNotFoundException',
    'ValidationError',
    'ValidationException',
}

FORBIDDEN_CODES = {
    'AccessDenied',
    'AccessDeniedException',
    'Forbidden',
    'UnauthorizedOperation',
}

THROTTLE_CODES = {
    'RequestLimitExceeded',
    'ThrottledException',
    'Throttling',
    'ThrottlingException',
    'TooManyRequestsException',
}


def error_status(exc: Exception) -> int:
    if isinstance(exc, ValueError):
        return 400
    if isinstance(exc, ClientError):
        code = exc.response.get('Error', {}).get('Code')
        code_text = str(code or '')
        # AWS spells "missing" several ways: NoSuchDistribution, NoSuchEntity,
        # QueueDoesNotExist, ResourceNotFoundException, ...
        if (
            code in NOT_FOUND_CODES
            or code_text.endswith('NotFound')
            or code_text.startswith('NoSuch')
            or code_text.endswith('DoesNotExist')
        ):
            return 404
        if code in BAD_REQUEST_CODES:
            return 400
        if code in FORBIDDEN_CODES:
            return 403
        if code in THROTTLE_CODES:
            return 429
        return 502
    if isinstance(exc, BotoCoreError):
        return 502
    return 500


def json_error(
    message: str,
    *,
    status: int = 400,
    code: str | None = None,
    service: str = '',
    operation: str = '',
) -> JsonResponse:
    payload: dict = {'ok': False, 'error': message, 'status': status, 'type': 'ActionError'}
    if code:
        payload['code'] = code
    if service:
        payload['service'] = service
    if operation:
        payload['operation'] = operation
        payload['operation_label'] = operation_label(operation)
    return JsonResponse(payload, status=status)


def handle_action_error(exc: Exception, *, service: str = '', operation: str = '') -> JsonResponse:
    status = error_status(exc)
    payload = error_payload(exc, service=service, operation=operation)
    payload['status'] = status
    return JsonResponse(
        payload,
        status=status,
    )
