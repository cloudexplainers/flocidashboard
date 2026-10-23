"""Interactive Lambda helpers for the invoke workbench."""

from __future__ import annotations

import base64
import io
import json
import urllib.request
import zipfile
from typing import Any
from urllib.parse import urlparse

from botocore.exceptions import ClientError

from .aws import FlociClientFactory, _clean_response, browser_reachable_url


def _lambda_client():
    return FlociClientFactory().client('lambda')


def validate_function_name(name: str) -> str:
    value = (name or '').strip()
    if not value:
        raise ValueError('Function name is required')
    return value


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _object(value: Any, label: str, *, empty: bool = True) -> dict[str, Any]:
    if value in (None, '') and empty:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f'{label} must be valid JSON') from exc
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object')
    return value


def _function_code(code: Any) -> dict[str, Any]:
    """Build the Code parameter. JSON cannot carry bytes, so the console sends
    either {"InlineSource": {"FileName", "Content"}} (zipped here, like the
    console's "Author from scratch" editor) or {"ZipFile": "<base64>"}."""
    value = _object(code, 'Code', empty=False)
    inline = value.pop('InlineSource', None)
    if inline is not None:
        if not isinstance(inline, dict):
            raise ValueError('InlineSource must be an object with FileName and Content')
        file_name = _required(inline.get('FileName'), 'Code file name')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(file_name, str(inline.get('Content') or ''))
        value['ZipFile'] = buffer.getvalue()
    elif isinstance(value.get('ZipFile'), str):
        try:
            value['ZipFile'] = base64.b64decode(value['ZipFile'], validate=True)
        except ValueError as exc:
            raise ValueError('ZipFile must be base64-encoded') from exc
    return value


def create_function(name: str, role: str, code: Any, *, configuration: Any = None, tags: Any = None) -> dict[str, Any]:
    kwargs = {
        **_object(configuration, 'Configuration'),
        'FunctionName': validate_function_name(name),
        'Role': _required(role, 'Execution role ARN'),
        'Code': _function_code(code),
    }
    clean_tags = _object(tags, 'Tags')
    if clean_tags:
        kwargs['Tags'] = {str(key): str(value) for key, value in clean_tags.items()}
    response = _lambda_client().create_function(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'arn': response.get('FunctionArn'), 'state': response.get('State'), 'response': _clean_response(response)}


def update_function_configuration(name: str, configuration: Any) -> dict[str, Any]:
    kwargs = _object(configuration, 'Configuration', empty=False)
    kwargs['FunctionName'] = validate_function_name(name)
    response = _lambda_client().update_function_configuration(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'state': response.get('State'), 'response': _clean_response(response)}


def update_function_code(name: str, code: Any, *, publish: bool = False) -> dict[str, Any]:
    kwargs = _function_code(code)
    kwargs.update(FunctionName=validate_function_name(name), Publish=bool(publish))
    response = _lambda_client().update_function_code(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'version': response.get('Version'), 'response': _clean_response(response)}


MAX_PACKAGE_BYTES = 10 * 1024 * 1024
MAX_EDITABLE_FILE_BYTES = 256 * 1024


def _download_package(name: str) -> tuple[dict[str, Any], bytes]:
    """GetFunction plus the deployment package from Code.Location.

    Only the path of Code.Location is used; the request goes to the configured Floci
    endpoint, so a function can't point the dashboard at another host.
    """
    function = _lambda_client().get_function(FunctionName=validate_function_name(name))
    configuration = function.get('Configuration', {})
    if configuration.get('PackageType') == 'Image':
        raise ValueError('This function is deployed as a container image. Its code is edited by pushing a new image.')
    location = urlparse((function.get('Code') or {}).get('Location') or '')
    if not location.path:
        raise ValueError('Floci did not return a code location for this function.')
    endpoint = urlparse(FlociClientFactory().endpoint_url)
    url = endpoint._replace(path=location.path, query=location.query, params='', fragment='').geturl()
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - fixed scheme/host from config
        package = response.read(MAX_PACKAGE_BYTES + 1)
    if len(package) > MAX_PACKAGE_BYTES:
        raise ValueError('The deployment package is larger than 10 MB, too large to edit in the console.')
    return configuration, package


def get_function_source(name: str) -> dict[str, Any]:
    """The function's files, with the text of small text files, as the console's code editor shows them."""
    configuration, package = _download_package(name)
    try:
        archive = zipfile.ZipFile(io.BytesIO(package))
    except zipfile.BadZipFile as exc:
        raise ValueError('The deployment package is not a .zip file archive.') from exc
    module = (configuration.get('Handler') or '').rsplit('.', 1)[0]
    files = []
    for info in archive.infolist():
        if info.is_dir():
            continue
        entry: dict[str, Any] = {'name': info.filename, 'size': info.file_size, 'editable': False}
        if info.file_size <= MAX_EDITABLE_FILE_BYTES:
            try:
                entry['content'] = archive.read(info).decode('utf-8')
                entry['editable'] = True
            except UnicodeDecodeError:
                pass
        files.append(entry)
    handler_file = next((item['name'] for item in files if item['name'].rsplit('.', 1)[0] == module), None)
    return {
        'function_name': configuration.get('FunctionName') or name,
        'runtime': configuration.get('Runtime'),
        'handler': configuration.get('Handler'),
        'code_sha256': configuration.get('CodeSha256'),
        'handler_file': handler_file,
        'files': files,
    }


def deploy_function_source(name: str, files: Any) -> dict[str, Any]:
    """Deploy edited files: rebuild the current package with them replaced, keeping every other file."""
    if not isinstance(files, dict) or not files:
        raise ValueError('Files to deploy must be an object of file name to content')
    _, package = _download_package(name)
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(package)) as current, zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as updated:
        names = set()
        for info in current.infolist():
            names.add(info.filename)
            if info.filename in files:
                updated.writestr(info, str(files[info.filename]))
            else:
                updated.writestr(info, current.read(info))
        for file_name, content in files.items():
            if file_name not in names:
                updated.writestr(_required(file_name, 'File name'), str(content))
    response = _lambda_client().update_function_code(FunctionName=validate_function_name(name), ZipFile=buffer.getvalue())
    return {
        'function_name': response.get('FunctionName') or name,
        'code_sha256': response.get('CodeSha256'),
        'last_update_status': response.get('LastUpdateStatus'),
        'response': _clean_response(response),
    }


def delete_function(name: str, *, qualifier: str = '') -> dict[str, Any]:
    kwargs = {'FunctionName': validate_function_name(name)}
    if qualifier:
        kwargs['Qualifier'] = str(qualifier).strip()
    response = _lambda_client().delete_function(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'qualifier': kwargs.get('Qualifier'), 'deleted': True, 'response': _clean_response(response)}


def publish_version(name: str, *, description: str = '') -> dict[str, Any]:
    kwargs = {'FunctionName': validate_function_name(name)}
    if description:
        kwargs['Description'] = str(description)
    response = _lambda_client().publish_version(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'version': response.get('Version'), 'response': _clean_response(response)}


def save_alias(function_name: str, alias_name: str, function_version: str, *, description: str = '', update: bool = False) -> dict[str, Any]:
    kwargs = {'FunctionName': validate_function_name(function_name), 'Name': _required(alias_name, 'Alias name'), 'FunctionVersion': _required(function_version, 'Function version')}
    if description:
        kwargs['Description'] = str(description)
    client = _lambda_client()
    response = client.update_alias(**kwargs) if update else client.create_alias(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'alias': kwargs['Name'], 'version': response.get('FunctionVersion'), 'response': _clean_response(response)}


def delete_alias(function_name: str, alias_name: str) -> dict[str, Any]:
    clean_function = validate_function_name(function_name)
    clean_alias = _required(alias_name, 'Alias name')
    response = _lambda_client().delete_alias(FunctionName=clean_function, Name=clean_alias)
    return {'function_name': clean_function, 'alias': clean_alias, 'deleted': True, 'response': _clean_response(response)}


def save_event_source_mapping(function_name: str, options: Any, *, uuid: str = '') -> dict[str, Any]:
    kwargs = _object(options, 'Event source mapping', empty=False)
    client = _lambda_client()
    if uuid:
        kwargs['UUID'] = _required(uuid, 'Mapping UUID')
        response = client.update_event_source_mapping(**kwargs)
    else:
        kwargs['FunctionName'] = validate_function_name(function_name)
        response = client.create_event_source_mapping(**kwargs)
    return {'function_name': function_name, 'uuid': response.get('UUID'), 'state': response.get('State'), 'response': _clean_response(response)}


def delete_event_source_mapping(uuid: str) -> dict[str, Any]:
    clean_uuid = _required(uuid, 'Mapping UUID')
    response = _lambda_client().delete_event_source_mapping(UUID=clean_uuid)
    return {'uuid': clean_uuid, 'deleted': True, 'response': _clean_response(response)}


def _paginate(operation: str, result_key: str, **kwargs) -> list[dict[str, Any]]:
    client = _lambda_client()
    items: list[dict[str, Any]] = []
    for page in client.get_paginator(operation).paginate(**kwargs):
        items.extend(page.get(result_key, []))
    return _clean_response(items)


def list_versions(function_name: str) -> dict[str, Any]:
    name = validate_function_name(function_name)
    return {'Versions': _paginate('list_versions_by_function', 'Versions', FunctionName=name)}


def list_aliases(function_name: str) -> dict[str, Any]:
    name = validate_function_name(function_name)
    return {'Aliases': _paginate('list_aliases', 'Aliases', FunctionName=name)}


def list_event_source_mappings(function_name: str) -> dict[str, Any]:
    name = validate_function_name(function_name)
    return {'EventSourceMappings': _paginate('list_event_source_mappings', 'EventSourceMappings', FunctionName=name)}


def get_function_url(function_name: str) -> dict[str, Any] | None:
    """Return the function URL config, or None when the function has no URL."""
    name = validate_function_name(function_name)
    try:
        response = _lambda_client().get_function_url_config(FunctionName=name)
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') == 'ResourceNotFoundException':
            return None
        raise
    response.pop('ResponseMetadata', None)
    result = _clean_response(response)
    # FunctionUrl uses Floci's container hostname; BrowserUrl is the same endpoint reached from the host.
    result['BrowserUrl'] = browser_reachable_url(result.get('FunctionUrl'))
    return result


def save_function_url(function_name: str, options: Any, *, update: bool = False) -> dict[str, Any]:
    kwargs = _object(options, 'Function URL configuration', empty=False)
    kwargs['FunctionName'] = validate_function_name(function_name)
    client = _lambda_client()
    response = client.update_function_url_config(**kwargs) if update else client.create_function_url_config(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'function_url': response.get('FunctionUrl'), 'response': _clean_response(response)}


def delete_function_url(function_name: str) -> dict[str, Any]:
    clean_name = validate_function_name(function_name)
    response = _lambda_client().delete_function_url_config(FunctionName=clean_name)
    return {'function_name': clean_name, 'deleted': True, 'response': _clean_response(response)}


def set_concurrency(function_name: str, value: Any = None) -> dict[str, Any]:
    clean_name = validate_function_name(function_name)
    client = _lambda_client()
    if value in (None, ''):
        response = client.delete_function_concurrency(FunctionName=clean_name)
        return {'function_name': clean_name, 'reserved_concurrency': None, 'response': _clean_response(response)}
    try:
        concurrency = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError('Reserved concurrency must be a number') from exc
    response = client.put_function_concurrency(FunctionName=clean_name, ReservedConcurrentExecutions=concurrency)
    return {'function_name': clean_name, 'reserved_concurrency': response.get('ReservedConcurrentExecutions'), 'response': _clean_response(response)}


def add_permission(function_name: str, statement: Any) -> dict[str, Any]:
    kwargs = _object(statement, 'Permission statement', empty=False)
    kwargs['FunctionName'] = validate_function_name(function_name)
    response = _lambda_client().add_permission(**kwargs)
    return {'function_name': kwargs['FunctionName'], 'statement': response.get('Statement'), 'response': _clean_response(response)}


def remove_permission(function_name: str, statement_id: str) -> dict[str, Any]:
    clean_name = validate_function_name(function_name)
    clean_id = _required(statement_id, 'Statement ID')
    response = _lambda_client().remove_permission(FunctionName=clean_name, StatementId=clean_id)
    return {'function_name': clean_name, 'statement_id': clean_id, 'removed': True, 'response': _clean_response(response)}


def update_tags(resource_arn: str, tags: Any = None, *, remove: Any = None) -> dict[str, Any]:
    arn = _required(resource_arn, 'Resource ARN')
    client = _lambda_client()
    if remove not in (None, '', []):
        keys = remove if isinstance(remove, list) else [item.strip() for item in str(remove).split(',') if item.strip()]
        response = client.untag_resource(Resource=arn, TagKeys=keys)
        return {'resource_arn': arn, 'removed': keys, 'response': _clean_response(response)}
    clean_tags = _object(tags, 'Tags', empty=False)
    response = client.tag_resource(Resource=arn, Tags={str(key): str(value) for key, value in clean_tags.items()})
    return {'resource_arn': arn, 'tags': clean_tags, 'response': _clean_response(response)}


def _payload_bytes(payload: Any) -> bytes:
    if payload in (None, ''):
        return b'{}'
    if isinstance(payload, str):
        try:
            json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ValueError('Payload must be valid JSON') from exc
        return payload.encode('utf-8')
    return json.dumps(payload).encode('utf-8')


def _read_payload(stream: Any) -> dict[str, Any]:
    if stream is None:
        return {'raw': ''}

    raw = stream.read() if hasattr(stream, 'read') else stream
    if isinstance(raw, bytes):
        text = raw.decode('utf-8')
    else:
        text = str(raw or '')

    if not text:
        return {'raw': ''}

    try:
        return {'json': json.loads(text), 'raw': text}
    except json.JSONDecodeError:
        return {'raw': text}


def invoke_function(
    name: str,
    payload: Any = None,
    *,
    qualifier: str | None = None,
    invocation_type: str = 'RequestResponse',
) -> dict[str, Any]:
    function_name = validate_function_name(name)
    request: dict[str, Any] = {
        'FunctionName': function_name,
        'InvocationType': invocation_type if invocation_type in {'RequestResponse', 'Event', 'DryRun'} else 'RequestResponse',
        'Payload': _payload_bytes(payload),
    }
    if request['InvocationType'] == 'RequestResponse':
        request['LogType'] = 'Tail'
    if qualifier:
        request['Qualifier'] = qualifier

    response = _lambda_client().invoke(**request)
    log_tail = response.get('LogResult')
    if log_tail:
        try:
            log_tail = base64.b64decode(log_tail).decode('utf-8')
        except (ValueError, UnicodeDecodeError):
            pass

    return {
        'function_name': function_name,
        'status_code': response.get('StatusCode'),
        'executed_version': response.get('ExecutedVersion'),
        'function_error': response.get('FunctionError'),
        'log_tail': log_tail,
        'payload': _read_payload(response.get('Payload')),
        'log_group': f'/aws/lambda/{function_name}',
    }


def get_event_templates() -> dict[str, dict[str, Any]]:
    return {
        'apigateway_v2_http': {
            'name': 'API Gateway HTTP API (Payload v2.0)',
            'description': 'Payload format version 2.0 used by API Gateway HTTP APIs and Lambda Function URLs.',
            'event': {
                'version': '2.0',
                'routeKey': '$default',
                'rawPath': '/my/path',
                'rawQueryString': 'parameter1=value1&parameter1=value2&parameter2=value',
                'headers': {
                    'header1': 'value1',
                    'header2': 'value1,value2',
                },
                'queryStringParameters': {
                    'parameter1': 'value1,value2',
                    'parameter2': 'value',
                },
                'requestContext': {
                    'accountId': '000000000000',
                    'apiId': 'r3pmxmplak',
                    'domainName': 'r3pmxmplak.execute-api.us-east-1.amazonaws.com',
                    'domainPrefix': 'r3pmxmplak',
                    'http': {
                        'method': 'POST',
                        'path': '/my/path',
                        'protocol': 'HTTP/1.1',
                        'sourceIp': '127.0.0.1',
                        'userAgent': 'Mozilla/5.0 Custom Browser',
                    },
                    'requestId': 'jk4m-fake-request-id',
                    'routeKey': '$default',
                    'stage': '$default',
                    'time': '12/Mar/2026:19:03:58 +0000',
                    'timeEpoch': 1773342238000,
                },
                'body': '{"message": "Hello from API Gateway!"}',
                'isBase64Encoded': False,
            },
        },
        'apigateway_v1_rest': {
            'name': 'API Gateway REST API (Lambda Proxy)',
            'description': 'Classic REST API proxy integration event.',
            'event': {
                'resource': '/{proxy+}',
                'path': '/hello/world',
                'httpMethod': 'POST',
                'headers': {
                    'Accept': '*/*',
                    'Content-Type': 'application/json',
                    'User-Agent': 'CustomClient/1.0',
                },
                'multiValueHeaders': {
                    'Accept': ['*/*'],
                    'Content-Type': ['application/json'],
                },
                'queryStringParameters': {'foo': 'bar'},
                'multiValueQueryStringParameters': {'foo': ['bar']},
                'pathParameters': {'proxy': 'hello/world'},
                'stageVariables': None,
                'requestContext': {
                    'resourceId': '123456',
                    'resourcePath': '/{proxy+}',
                    'httpMethod': 'POST',
                    'stage': 'prod',
                    'requestId': 'c6af9ac6-7b61-11e6-9a41-97e00429db63',
                    'identity': {'sourceIp': '127.0.0.1'},
                },
                'body': '{\\n  "key1": "value1",\\n  "key2": "value2"\\n}',
                'isBase64Encoded': False,
            },
        },
        'sqs_standard': {
            'name': 'Amazon SQS Message',
            'description': 'SQS batch invocation event with message attributes.',
            'event': {
                'Records': [
                    {
                        'messageId': '19dd0b57-b21e-4ac1-bd88-01bbb068cb78',
                        'receiptHandle': 'MessageReceiptHandle',
                        'body': '{"event": "order_placed", "order_id": 1001}',
                        'attributes': {
                            'ApproximateReceiveCount': '1',
                            'SentTimestamp': '1523232000000',
                            'SenderId': '123456789012',
                            'ApproximateFirstReceiveTimestamp': '1523232000001',
                        },
                        'messageAttributes': {
                            'CorrelationId': {
                                'stringValue': 'req-98765',
                                'dataType': 'String',
                            },
                        },
                        'md5OfBody': '7b270eebdaec70f745f106d9fb7370c2',
                        'eventSource': 'aws:sqs',
                        'eventSourceARN': 'arn:aws:sqs:us-east-1:000000000000:orders-queue',
                        'awsRegion': 'us-east-1',
                    },
                ],
            },
        },
        'sns_notification': {
            'name': 'Amazon SNS Notification',
            'description': 'SNS pub/sub message notification record.',
            'event': {
                'Records': [
                    {
                        'EventSource': 'aws:sns',
                        'EventVersion': '1.0',
                        'EventSubscriptionArn': 'arn:aws:sns:us-east-1:000000000000:topic:sub-123',
                        'Sns': {
                            'Type': 'Notification',
                            'MessageId': '95df01b4-ee98-5cb9-9903-4c221d41eb5e',
                            'TopicArn': 'arn:aws:sns:us-east-1:000000000000:order-events',
                            'Subject': 'New Order Notification',
                            'Message': '{"orderId": "1001", "status": "CONFIRMED"}',
                            'Timestamp': '2026-08-25T12:00:00.000Z',
                            'SignatureVersion': '1',
                            'MessageAttributes': {
                                'eventType': {
                                    'Type': 'String',
                                    'Value': 'OrderConfirmed',
                                },
                            },
                        },
                    },
                ],
            },
        },
        'dynamodb_streams': {
            'name': 'DynamoDB Streams',
            'description': 'DynamoDB item modification record (INSERT, MODIFY, REMOVE).',
            'event': {
                'Records': [
                    {
                        'eventID': '1',
                        'eventName': 'INSERT',
                        'eventVersion': '1.1',
                        'eventSource': 'aws:dynamodb',
                        'awsRegion': 'us-east-1',
                        'dynamodb': {
                            'ApproximateCreationDateTime': 1618300000,
                            'Keys': {
                                'id': {'S': 'user-101'},
                            },
                            'NewImage': {
                                'id': {'S': 'user-101'},
                                'name': {'S': 'Alice'},
                                'status': {'S': 'ACTIVE'},
                            },
                            'SequenceNumber': '111',
                            'SizeBytes': 38,
                            'StreamViewType': 'NEW_AND_OLD_IMAGES',
                        },
                        'eventSourceARN': 'arn:aws:dynamodb:us-east-1:000000000000:table/users/stream/2026-08-25',
                    },
                ],
            },
        },
        's3_put_object': {
            'name': 'Amazon S3 Object Created (Put)',
            'description': 'S3 bucket notification for newly created objects.',
            'event': {
                'Records': [
                    {
                        'eventVersion': '2.1',
                        'eventSource': 'aws:s3',
                        'awsRegion': 'us-east-1',
                        'eventTime': '2026-08-25T12:00:00.000Z',
                        'eventName': 'ObjectCreated:Put',
                        's3': {
                            's3SchemaVersion': '1.0',
                            'configurationId': 'testConfigRule',
                            'bucket': {
                                'name': 'example-bucket',
                                'arn': 'arn:aws:s3:::example-bucket',
                            },
                            'object': {
                                'key': 'uploads/report.pdf',
                                'size': 1024,
                                'eTag': 'd41d8cd98f00b204e9800998ecf8427e',
                                'sequencer': '0A1B2C3D4E5F678901',
                            },
                        },
                    },
                ],
            },
        },
        'eventbridge_scheduled': {
            'name': 'EventBridge / CloudWatch Scheduled Event',
            'description': 'Cron / rate scheduled rule invocation event.',
            'event': {
                'version': '0',
                'id': '53ac4291-7709-43a3-5c4e-4b3da50d96e1',
                'detail-type': 'Scheduled Event',
                'source': 'aws.events',
                'account': '000000000000',
                'time': '2026-08-25T12:00:00Z',
                'region': 'us-east-1',
                'resources': [
                    'arn:aws:events:us-east-1:000000000000:rule/my-scheduled-rule',
                ],
                'detail': {},
            },
        },
    }


def invoke_function_url(
    url: str,
    *,
    method: str = 'POST',
    headers: dict[str, str] | None = None,
    body: str | None = None,
    query_params: dict[str, str] | None = None,
) -> dict[str, Any]:
    import time
    import urllib.error
    import urllib.parse
    import urllib.request

    clean_url = (url or '').strip()
    if not clean_url:
        raise ValueError('Function URL is required')

    if query_params:
        parsed_url = urllib.parse.urlparse(clean_url)
        existing_qs = urllib.parse.parse_qs(parsed_url.query)
        for k, v in query_params.items():
            existing_qs[k] = [v]
        new_qs = urllib.parse.urlencode(existing_qs, doseq=True)
        clean_url = urllib.parse.urlunparse(parsed_url._replace(query=new_qs))

    clean_method = (method or 'POST').strip().upper()
    req_headers = {'User-Agent': 'Floci-Dashboard-Workbench/1.0'}
    if headers and isinstance(headers, dict):
        for k, v in headers.items():
            req_headers[str(k)] = str(v)

    req_data = body.encode('utf-8') if body is not None and clean_method not in {'GET', 'HEAD'} else None
    if req_data and 'Content-Type' not in req_headers and 'content-type' not in req_headers:
        req_headers['Content-Type'] = 'application/json'

    request = urllib.request.Request(clean_url, data=req_data, headers=req_headers, method=clean_method)

    start_time = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            resp_body_bytes = response.read()
            resp_body_str = resp_body_bytes.decode('utf-8', errors='replace')
            resp_headers = dict(response.headers.items())
            try:
                parsed_json = json.loads(resp_body_str)
            except Exception:
                parsed_json = None
            return {
                'url': clean_url,
                'method': clean_method,
                'status_code': response.status,
                'headers': resp_headers,
                'latency_ms': latency_ms,
                'body': resp_body_str,
                'json': parsed_json,
            }
    except urllib.error.HTTPError as err:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        err_body_bytes = err.read()
        err_body_str = err_body_bytes.decode('utf-8', errors='replace')
        try:
            parsed_json = json.loads(err_body_str)
        except Exception:
            parsed_json = None
        return {
            'url': clean_url,
            'method': clean_method,
            'status_code': err.code,
            'headers': dict(err.headers.items()) if err.headers else {},
            'latency_ms': latency_ms,
            'body': err_body_str,
            'json': parsed_json,
            'error': str(err),
        }



def _destination(value: Any, label: str) -> str | None:
    if value in (None, ''):
        return None
    arn = str(value).strip()
    if not arn.startswith('arn:') or not any(f':{svc}:' in arn for svc in ('sqs', 'sns', 'lambda', 'events', 's3')):
        raise ValueError(f'{label} must be the ARN of an SQS queue, SNS topic, Lambda function, EventBridge bus or S3 bucket')
    return arn


def get_event_invoke_config(function_name: str) -> dict[str, Any]:
    """Asynchronous invocation settings, or config=None when the function uses the defaults."""
    from botocore.exceptions import ClientError
    try:
        config = _lambda_client().get_function_event_invoke_config(FunctionName=function_name)
    except ClientError as exc:
        if exc.response.get('Error', {}).get('Code') == 'ResourceNotFoundException' and 'EventInvokeConfig' in str(exc):
            return {'config': None}
        raise
    destinations = config.get('DestinationConfig') or {}
    return {'config': {
        'maximum_retry_attempts': config.get('MaximumRetryAttempts'),
        'maximum_event_age_in_seconds': config.get('MaximumEventAgeInSeconds'),
        'on_success': (destinations.get('OnSuccess') or {}).get('Destination') or '',
        'on_failure': (destinations.get('OnFailure') or {}).get('Destination') or '',
        'last_modified': config['LastModified'].isoformat() if hasattr(config.get('LastModified'), 'isoformat') else config.get('LastModified'),
    }}


def put_event_invoke_config(function_name: str, body: dict[str, Any]) -> dict[str, Any]:
    """PutFunctionEventInvokeConfig (replaces the whole config) with the AWS limits Floci doesn't check."""
    kwargs: dict[str, Any] = {'FunctionName': function_name}
    retries = body.get('maximum_retry_attempts')
    if retries not in (None, ''):
        if isinstance(retries, bool) or str(retries) not in ('0', '1', '2'):
            raise ValueError('Retry attempts must be 0, 1 or 2')
        kwargs['MaximumRetryAttempts'] = int(retries)
    age = body.get('maximum_event_age_in_seconds')
    if age not in (None, ''):
        try:
            seconds = int(age)
        except (TypeError, ValueError) as exc:
            raise ValueError('Maximum age of event must be a whole number of seconds') from exc
        if not 60 <= seconds <= 21600:
            raise ValueError('Maximum age of event must be from 60 seconds to 6 hours (21600 seconds)')
        kwargs['MaximumEventAgeInSeconds'] = seconds
    destinations = {}
    for key, name, label in (('on_success', 'OnSuccess', 'On-success destination'), ('on_failure', 'OnFailure', 'On-failure destination')):
        arn = _destination(body.get(key), label)
        if arn:
            destinations[name] = {'Destination': arn}
    kwargs['DestinationConfig'] = destinations
    _lambda_client().put_function_event_invoke_config(**kwargs)
    return get_event_invoke_config(function_name)


def delete_event_invoke_config(function_name: str) -> dict[str, Any]:
    _lambda_client().delete_function_event_invoke_config(FunctionName=function_name)
    return {'config': None, 'deleted': True}
