"""Code snippets (AWS CLI, Python boto3, Terraform) for lab steps.

Each lab step carries one AWS CLI command (sometimes a short shell script).
The boto3 and Terraform tabs are generated from it:

* Every ``aws <service> <operation>`` line goes through the AWS CLI's own
  argument parser (awscli is already a dependency), so the boto3 call gets
  exactly the parameters the CLI would send: shorthand such as
  ``Key=Name,Value=web``, JSON, lists, booleans and numbers all come out as
  the right Python types. The parser never calls AWS: an event hook stops it
  right before the request. When the CLI cannot parse a line (an abbreviated
  ``...`` command or a ``<placeholder>`` in a number slot), a smaller parser
  built on the same botocore service model takes over.
* Terraform only manages long-lived resources. A step that creates a resource
  with a Terraform equivalent gets that resource; every other step (reads,
  invocations, data-plane writes) says so plainly instead of inventing a
  resource type that does not exist.

A step can still override any tab with ``code_snippets``.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import keyword
import os
import re
import shlex
import threading
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from botocore import xform_name


# ---------------------------------------------------------------------------
# Shell script -> statements
# ---------------------------------------------------------------------------

_OPERATORS = {'|', '||', '&&', ';', '>', '>>', '<', '&', '2>', '2>&1'}


def _split_comment(line: str) -> tuple[str, str]:
    """Split a shell line into (code, trailing comment) like bash does."""
    quote = ''
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = ''
            elif ch == '\\' and quote == '"':
                continue
        elif ch in ('"', "'"):
            quote = ch
        elif ch == '#' and (i == 0 or line[i - 1] in ' \t'):
            return line[:i].rstrip(), line[i + 1:].strip()
    return line, ''


_ANGLE_PLACEHOLDER_RE = re.compile(r'<[A-Za-z0-9][^<>\n]*?>')


def _tokenize(code: str) -> list[str] | None:
    """Tokenize shell code, keeping operators such as | && ; > as their own tokens.

    ``<placeholder>`` values from the lab text are kept whole (they are not redirects).
    """
    held: list[str] = []

    def hold(m: re.Match) -> str:
        held.append(m.group(0))
        return f'__FLOCI_PH_{len(held) - 1}__'

    lexer = shlex.shlex(_ANGLE_PLACEHOLDER_RE.sub(hold, code), posix=True, punctuation_chars='|&;<>')
    lexer.whitespace_split = True
    lexer.commenters = ''
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    return [re.sub(r'__FLOCI_PH_(\d+)__', lambda m: held[int(m.group(1))], t) for t in tokens]


def _logical_lines(command: str) -> list[str]:
    """Join backslash continuations and drop blank lines."""
    joined = re.sub(r'\\\n\s*', ' ', command.strip())
    return [line.rstrip() for line in joined.split('\n') if line.strip()]


# ---------------------------------------------------------------------------
# Python value markers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FileRef:
    path: str
    binary: bool = False
    as_json: bool = False


@dataclass(frozen=True)
class _Name:
    """A bare Python name, such as a loop variable or a placeholder constant."""

    name: str


@dataclass(frozen=True)
class _FStr:
    template: str  # already escaped for an f-string body


@dataclass(frozen=True)
class _JsonDoc:
    value: Any


@dataclass
class _Call:
    service: str  # botocore / boto3 client name
    method: str
    params: dict[str, Any]
    region: str = ''
    profile: str = ''
    query: str = ''
    outfile: str = ''
    streaming_member: str = ''
    note: str = ''
    waiter: str = ''
    raw: str = ''  # set for helper calls rendered verbatim (s3 transfer helpers)
    cli: str = ''  # "service operation", for comments


@dataclass
class _Script:
    imports: set[str] = field(default_factory=set)
    clients: dict[tuple[str, str, str], str] = field(default_factory=dict)
    placeholders: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (placeholder, value)
    body: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# AWS CLI parsing through awscli itself
# ---------------------------------------------------------------------------

# CLI options that only change how the CLI behaves; they are not API parameters.
_GLOBAL_WITH_VALUE = {
    '--cli-binary-format', '--profile', '--output', '--query', '--region', '--endpoint-url',
    '--color', '--cli-read-timeout', '--cli-connect-timeout', '--ca-bundle',
}
_GLOBAL_FLAGS = {
    '--no-cli-pager', '--debug', '--no-paginate', '--no-verify-ssl', '--no-sign-request',
    '--cli-auto-prompt', '--no-cli-auto-prompt',
}
_FILE_TOKEN = '{"__floci_snippet_file__": %d}'
_FILE_TOKEN_RE = re.compile(r'^\{"__floci_snippet_file__": (\d+)\}$')
_PLACEHOLDER_RE = re.compile(r'^<[^<>]+>$')

_driver_lock = threading.Lock()
_driver_state: dict[str, Any] = {}


_SAFE_GLOBALS = ['--region', 'us-east-1', '--endpoint-url', 'http://127.0.0.1:9']


def _captured(call_parameters, parsed_args, parsed_globals, **_kwargs):
    _driver_state['captured'] = (call_parameters, parsed_args)
    _driver_state.setdefault('globals', parsed_globals)
    return 0  # stop before the request is sent


def _driver():
    """One AWS CLI driver, set up once through main() so its session handlers register once."""
    driver = _driver_state.get('driver')
    if driver is None:
        # Importing awscli sets AWS_DATA_PATH for the whole process, which changes how every
        # boto3 session in the dashboard loads config. Let the CLI's own session load its data,
        # then put the variable back.
        saved = os.environ.get('AWS_DATA_PATH')
        try:
            from awscli.clidriver import create_clidriver

            driver = create_clidriver()
            driver.session.get_component('data_loader')
            driver.session.register('calling-command.*.*', _captured)
            sink = io.StringIO()
            with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
                driver.main(['sts', 'get-caller-identity', *_SAFE_GLOBALS])
        finally:
            if saved is None:
                os.environ.pop('AWS_DATA_PATH', None)
            else:
                os.environ['AWS_DATA_PATH'] = saved
        _driver_state['driver'] = driver
    return driver


def _operation_command(cli_service: str, cli_operation: str):
    """Return (service command, operation command) when it is a plain API operation."""
    table = _driver()._get_command_table()  # imports awscli safely before anything else does
    from awscli.clidriver import ServiceOperation

    service_cmd = table.get(cli_service)
    if service_cmd is None or not hasattr(service_cmd, 'service_model'):
        return None, None
    try:
        op_cmd = service_cmd._get_command_table().get(cli_operation)
    except Exception:
        return service_cmd, None
    if type(op_cmd) is not ServiceOperation and not (
        isinstance(op_cmd, ServiceOperation) and type(op_cmd).__module__.startswith('awscli.customizations')
    ):
        return service_cmd, None
    return service_cmd, op_cmd


def _cli_parse(cli_service: str, cli_operation: str, args: list[str]) -> tuple[dict, Any] | None:
    """Run the AWS CLI argument parser and return the API parameters it would send."""
    with _driver_lock:
        driver = _driver()
        _driver_state.pop('captured', None)
        sink = io.StringIO()
        try:
            # Calling the service command directly (not main()) skips session setup, which would
            # register another copy of the CLI's argument handlers on every call.
            with contextlib.redirect_stderr(sink), contextlib.redirect_stdout(sink):
                driver._get_command_table()[cli_service]([cli_operation, *args], _driver_state['globals'])
        except BaseException:  # argparse exits on bad input
            pass
        return _driver_state.pop('captured', None)


def _model_parse(op_model, args: list[str]) -> dict[str, Any]:
    """Best-effort parse for commands the CLI rejects (abbreviated or with placeholders)."""
    from awscli.shorthand import BackCompatVisitor, ShorthandParser

    members = op_model.input_shape.members if op_model.input_shape else {}
    by_flag = {xform_name(name, '-'): name for name in members}
    params: dict[str, Any] = {}
    i = 0
    while i < len(args):
        token = args[i]
        i += 1
        if not token.startswith('--'):
            continue
        flag = token[2:]
        values = []
        while i < len(args) and not args[i].startswith('--'):
            values.append(args[i])
            i += 1
        negated = flag.startswith('no-') and flag[3:] in by_flag
        name = by_flag.get(flag[3:] if negated else flag)
        if not name:
            continue
        shape = members[name]
        if shape.type_name == 'boolean' and not values:
            params[name] = not negated
            continue
        if not values or values == ['...']:
            continue

        def convert(raw: str, shape=shape):
            if _FILE_TOKEN_RE.match(raw) or _PLACEHOLDER_RE.match(raw) or raw.startswith('$'):
                return raw
            if shape.type_name in ('structure', 'map', 'list'):
                if raw.lstrip()[:1] in '{[':
                    try:
                        return json.loads(raw)
                    except ValueError:
                        pass
                try:
                    parsed = ShorthandParser().parse(raw)
                    BackCompatVisitor().visit(parsed, shape)
                    return parsed
                except Exception:
                    return raw
            if shape.type_name in ('integer', 'long'):
                return int(raw) if re.fullmatch(r'-?\d+', raw) else raw
            if shape.type_name in ('float', 'double'):
                try:
                    return float(raw)
                except ValueError:
                    return raw
            if shape.type_name == 'boolean':
                return raw.lower() == 'true' if raw.lower() in ('true', 'false') else raw
            return raw

        if shape.type_name == 'list':
            if len(values) == 1 and (values[0].lstrip()[:1] == '[' or _FILE_TOKEN_RE.match(values[0])):
                params[name] = convert(values[0])
            else:
                params[name] = [convert(v, shape.member) for v in values]
        else:
            params[name] = convert(values[0])
    return params


@lru_cache(maxsize=None)
def _service_model(service_name: str):
    import botocore.session

    return botocore.session.get_session().get_service_model(service_name)


def _parse_aws(tokens: list[str], files: list[tuple[str, bool]]) -> tuple[_Call | None, str]:
    """Translate one tokenized ``aws ...`` command into a _Call (or explain why not).

    ``files`` collects the file:// paths the call refers to; it must start empty.
    """
    call, parsed_files, reason = _parse_aws_cached(tuple(tokens))
    files.extend(parsed_files)
    return copy.deepcopy(call), reason


@lru_cache(maxsize=4096)
def _parse_aws_cached(tokens: tuple[str, ...]) -> tuple[_Call | None, list[tuple[str, bool]], str]:
    files: list[tuple[str, bool]] = []
    call, reason = _parse_aws_uncached(list(tokens), files)
    return call, files, reason


def _parse_aws_uncached(tokens: list[str], files: list[tuple[str, bool]]) -> tuple[_Call | None, str]:
    profile = ''
    while tokens and re.match(r'^[A-Z_][A-Z0-9_]*=', tokens[0]):
        var, _, value = tokens.pop(0).partition('=')
        if var == 'AWS_PROFILE':
            profile = value
    if len(tokens) < 3 or tokens[0] != 'aws':
        return None, 'not an AWS CLI command'
    cli_service, cli_operation, rest = tokens[1], tokens[2], tokens[3:]

    region = query = ''
    args: list[str] = []
    i = 0
    while i < len(rest):
        token = rest[i]
        if token in _GLOBAL_WITH_VALUE and i + 1 < len(rest):
            if token == '--region':
                region = rest[i + 1]
            elif token == '--query':
                query = rest[i + 1]
            elif token == '--profile':
                profile = rest[i + 1]
            i += 2
            continue
        if token in _GLOBAL_FLAGS:
            i += 1
            continue
        if token.startswith(('file://', 'fileb://')):
            binary = token.startswith('fileb://')
            files.append((token.split('://', 1)[1], binary))
            token = _FILE_TOKEN % (len(files) - 1)
        args.append(token)
        i += 1
    abbreviated = '...' in args

    if cli_service == 's3':
        call = _s3_highlevel(cli_operation, args, files)
        if call:
            call.region, call.profile, call.cli = region, profile, f's3 {cli_operation}'
        return call, '' if call else f'`aws s3 {cli_operation}` has no single boto3 call'
    if cli_operation == 'wait' and args:
        call, reason = _waiter_call(cli_service, args, region, profile)
        if call:
            call.cli = f'{cli_service} wait {args[0]}'
        return call, reason
    if (cli_service, cli_operation) == ('logs', 'tail') and args and not args[0].startswith('--'):
        since = args[args.index('--since') + 1] if '--since' in args[:-1] else '10m'
        unit = re.fullmatch(r'(\d+)([smhdw])', since)
        seconds = int(unit.group(1)) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}[unit.group(2)] if unit else 600
        return _Call('logs', '', {}, region=region, profile=profile, cli='logs tail', raw=(
            "# `aws logs tail` is a CLI helper; filter_log_events reads the same events.\n"
            "response = {client}.filter_log_events(\n"
            f"    logGroupName={args[0]!r},\n"
            f"    startTime=int((time.time() - {seconds}) * 1000),\n"
            ")\nfor event in response['events']:\n    print(event['message'])"
        ), note=''), ''

    service_cmd, op_cmd = _operation_command(cli_service, cli_operation)
    if service_cmd is None:
        return None, f'`aws {cli_service}` is not an AWS CLI service'
    if op_cmd is None:
        return None, f'`aws {cli_service} {cli_operation}` is an AWS CLI helper, not an API operation'
    op_model = op_cmd._operation_model
    service_name = op_model.service_model.service_name

    # The Lambda customization reads --zip-file from disk; put the file reference where boto3 expects it.
    zip_file = None
    if op_model.name in ('CreateFunction', 'UpdateFunctionCode') and '--zip-file' in args:
        idx = args.index('--zip-file')
        if idx + 1 < len(args):
            zip_file = args[idx + 1]
            del args[idx:idx + 2]

    # The S3 customization reads --body from disk; hand it a file reference instead.
    if op_model.name in ('PutObject', 'UploadPart') and '--body' in args:
        idx = args.index('--body')
        if idx + 1 < len(args) and not _FILE_TOKEN_RE.match(args[idx + 1]):
            files.append((args[idx + 1], True))
            args[idx + 1] = _FILE_TOKEN % (len(files) - 1)

    outfile = ''
    output_shape = op_model.output_shape
    streaming = ''
    if output_shape is not None:
        payload = output_shape.serialization.get('payload')
        if payload and output_shape.members[payload].serialization.get('streaming', False) or (
            payload and output_shape.members[payload].type_name == 'blob'
        ):
            streaming = payload
        elif op_model.name == 'Invoke':
            streaming = 'Payload'

    params = None
    if not abbreviated:
        parse_args = list(args)
        if streaming:
            # Pull the positional outfile out first so placeholders in it don't confuse the parser.
            positional = [a for j, a in enumerate(parse_args) if not a.startswith('--') and (j == 0 or not parse_args[j - 1].startswith('--'))]
            if positional:
                outfile = positional[-1]
        captured = _cli_parse(cli_service, cli_operation, parse_args)
        if captured is not None:
            params, parsed_args = captured
            outfile = getattr(parsed_args, 'outfile', '') or outfile
    if params is None:
        params = _model_parse(op_model, [a for a in args if a != '...'])
    if zip_file is not None:
        if op_model.name == 'CreateFunction':
            params['Code'] = {**(params.get('Code') or {}), 'ZipFile': zip_file}
        else:
            params['ZipFile'] = zip_file
    if op_model.name == 'RunInstances':
        # The CLI's --count option defaults to 1 and fills these in.
        params.setdefault('MinCount', 1)
        params.setdefault('MaxCount', params['MinCount'])
    return _Call(
        cli=f'{cli_service} {cli_operation}',
        service=service_name,
        method=xform_name(op_model.name),
        params=params,
        region=region,
        profile=profile,
        query=query,
        outfile=outfile if streaming else '',
        streaming_member=streaming,
        note='The lab abbreviates this command (...); fill in the remaining parameters.' if abbreviated else '',
    ), ''


def _waiter_call(cli_service: str, args: list[str], region: str, profile: str) -> tuple[_Call | None, str]:
    service_cmd, _ = _operation_command(cli_service, '')
    if service_cmd is None:
        return None, f'`aws {cli_service}` is not an AWS CLI service'
    service_model = service_cmd.service_model
    import botocore.session

    try:
        waiter_model = botocore.session.get_session().get_waiter_model(service_model.service_name)
    except Exception:
        return None, 'no waiter model'
    names = {xform_name(n, '-'): n for n in waiter_model.waiter_names}
    waiter_name = names.get(args[0])
    if not waiter_name:
        return None, f'unknown waiter {args[0]}'
    op_name = waiter_model.get_waiter(waiter_name).operation
    op_model = service_model.operation_model(op_name)
    captured = _cli_parse(cli_service, xform_name(op_name, '-'), args[1:])
    params = captured[0] if captured else _model_parse(op_model, args[1:])
    return _Call(
        service=service_model.service_name,
        method='',
        params=params,
        region=region,
        profile=profile,
        waiter=xform_name(waiter_name),
    ), ''


def _s3_uri(value: str) -> tuple[str, str] | None:
    if not value.startswith('s3://'):
        return None
    bucket, _, key = value[5:].partition('/')
    return bucket, key


_S3_CP_EXTRA = {
    '--sse': 'ServerSideEncryption',
    '--sse-kms-key-id': 'SSEKMSKeyId',
    '--storage-class': 'StorageClass',
    '--content-type': 'ContentType',
    '--cache-control': 'CacheControl',
    '--acl': 'ACL',
}


def _s3_highlevel(operation: str, args: list[str], files: list[tuple[str, bool]]) -> _Call | None:
    paths = []
    options: dict[str, Any] = {}
    i = 0
    while i < len(args):
        token = args[i]
        if token.startswith('--'):
            if i + 1 < len(args) and not args[i + 1].startswith('--') and token not in ('--recursive', '--force'):
                options[token] = args[i + 1]
                i += 2
            else:
                options[token] = True
                i += 1
            continue
        match = _FILE_TOKEN_RE.match(token)
        paths.append(files[int(match.group(1))][0] if match else token)
        i += 1

    if operation == 'mb' and paths and _s3_uri(paths[0]):
        return _Call('s3', 'create_bucket', {'Bucket': _s3_uri(paths[0])[0]})
    if operation == 'rb' and paths and _s3_uri(paths[0]):
        return _Call('s3', 'delete_bucket', {'Bucket': _s3_uri(paths[0])[0]},
                     note='delete_bucket needs an empty bucket (the CLI --force flag empties it first).' if options.get('--force') else '')
    if operation == 'ls':
        if not paths:
            return _Call('s3', 'list_buckets', {}, query='Buckets[].Name')
        bucket, prefix = _s3_uri(paths[0]) or ('', '')
        params = {'Bucket': bucket}
        if prefix:
            params['Prefix'] = prefix
        if not options.get('--recursive'):
            params['Delimiter'] = '/'
        return _Call('s3', 'list_objects_v2', params, query='Contents[].Key')
    if operation == 'presign' and paths and _s3_uri(paths[0]):
        bucket, key = _s3_uri(paths[0])
        expires = int(options.get('--expires-in', 3600))
        return _Call('s3', '', {}, raw=(
            "url = {client}.generate_presigned_url(\n"
            "    'get_object',\n"
            f"    Params={{'Bucket': {bucket!r}, 'Key': {key!r}}},\n"
            f"    ExpiresIn={expires},\n"
            ")\nprint(url)"
        ))
    if operation in ('cp', 'mv') and len(paths) == 2 and not options.get('--recursive'):
        src, dst = paths
        src_s3, dst_s3 = _s3_uri(src), _s3_uri(dst)
        extra = {_S3_CP_EXTRA[k]: v for k, v in options.items() if k in _S3_CP_EXTRA}
        if '--metadata' in options:
            extra['Metadata'] = dict(pair.split('=', 1) for pair in str(options['--metadata']).split(',') if '=' in pair)
        if src_s3 and dst_s3:
            dst_key = dst_s3[1] if dst_s3[1] and not dst_s3[1].endswith('/') else dst_s3[1] + src_s3[1].rsplit('/', 1)[-1]
            return _Call('s3', 'copy_object', {
                'CopySource': {'Bucket': src_s3[0], 'Key': src_s3[1]}, 'Bucket': dst_s3[0], 'Key': dst_key, **extra,
            })
        if dst_s3:
            key = dst_s3[1] if dst_s3[1] and not dst_s3[1].endswith('/') else dst_s3[1] + src.rsplit('/', 1)[-1]
            extra_args = f',\n    ExtraArgs={_py(extra, 1)}' if extra else ''
            return _Call('s3', '', {}, raw=(
                "# upload_file switches to a multipart upload for large files, like the CLI does.\n"
                f"{{client}}.upload_file(\n    {src!r},\n    {dst_s3[0]!r},\n    {key!r}{extra_args},\n)"
            ))
        if src_s3:
            if dst == '-':
                return _Call('s3', 'get_object', {'Bucket': src_s3[0], 'Key': src_s3[1]}, streaming_member='Body', outfile='-')
            target = src_s3[1].rsplit('/', 1)[-1] if dst in ('.', './') or dst.endswith('/') else dst
            if dst.endswith('/'):
                target = dst + target
            return _Call('s3', '', {}, raw=f"{{client}}.download_file({src_s3[0]!r}, {src_s3[1]!r}, {target!r})")
    return None


# ---------------------------------------------------------------------------
# Python rendering
# ---------------------------------------------------------------------------

_SHELL_VAR_RE = re.compile(r'\$\(\((.+?)\)\)|\$\{(\w+)\}|\$(\w+)')


def _resolve(value: Any, files: list[tuple[str, bool]], scope: set[str], script: _Script, shape=None) -> Any:
    """Swap file tokens, loop variables and placeholders for Python markers."""
    if isinstance(value, dict):
        if set(value) == {'__floci_snippet_file__'}:
            path, binary = files[value['__floci_snippet_file__']]
            return _FileRef(path, binary, as_json=True)
        members = getattr(shape, 'members', None)
        value_shape = getattr(shape, 'value', None)
        return {
            k: _resolve(v, files, scope, script, (members or {}).get(k) if members is not None else value_shape)
            for k, v in value.items()
        }
    if isinstance(value, list):
        if len(value) == 1 and isinstance(value[0], dict) and set(value[0]) == {'__floci_snippet_file__'}:
            return _resolve(value[0], files, scope, script, shape)  # the file holds the whole list
        return [_resolve(v, files, scope, script, getattr(shape, 'member', None)) for v in value]
    if isinstance(value, bytes):
        value = value.decode('utf-8', 'replace')
    if not isinstance(value, str):
        return value
    match = _FILE_TOKEN_RE.match(value)
    if match:
        path, binary = files[int(match.group(1))]
        return _FileRef(path, binary)
    type_name = getattr(shape, 'type_name', 'string')
    if type_name in ('integer', 'long', 'float', 'double', 'boolean', 'timestamp'):
        var = re.fullmatch(r'\$\{?(\w+)\}?', value)
        if var and var.group(1) in scope:
            return _Name(var.group(1))
        if _PLACEHOLDER_RE.match(value):
            return _placeholder_name(value, type_name, script)
    if scope and _SHELL_VAR_RE.search(value):
        def repl(m: re.Match) -> str:
            name = m.group(2) or m.group(3)
            if m.group(1):
                return '{' + m.group(1) + '}'
            return '{' + name + '}' if name in scope else m.group(0)

        parts, last = [], 0
        for m in _SHELL_VAR_RE.finditer(value):
            parts.append(value[last:m.start()].replace('{', '{{').replace('}', '}}'))
            parts.append(repl(m))
            last = m.end()
        parts.append(value[last:].replace('{', '{{').replace('}', '}}'))
        template = ''.join(parts)
        if '{' in template.replace('{{', ''):
            return _FStr(template)
    if type_name == 'string' and value.lstrip()[:1] in '{[' and len(value) > 30:
        try:
            doc = json.loads(value)
        except ValueError:
            return value
        if isinstance(doc, (dict, list)):
            script.imports.add('json')
            return _JsonDoc(doc)
    return value


_AGO_RE = re.compile(r'^<\s*(\d+|a|an|one|five|ten|fifteen|thirty)\s+(second|minute|hour|day)s?\s+ago\s*>$', re.I)
_WORD_NUMBERS = {'a': 1, 'an': 1, 'one': 1, 'five': 5, 'ten': 10, 'fifteen': 15, 'thirty': 30}


def _placeholder_name(placeholder: str, type_name: str, script: _Script) -> _Name:
    """Turn a <placeholder> in a number or time slot into a named constant at the top."""
    text = placeholder[1:-1].strip()
    name = re.sub(r'\W+', '_', text).strip('_').upper() or 'VALUE'
    if name[0].isdigit():
        name = 'AT_' + name
    ago = _AGO_RE.match(placeholder)
    if ago or text.lower() == 'now':
        amount = 0
        if ago:
            count = ago.group(1).lower()
            amount = int(count) if count.isdigit() else _WORD_NUMBERS[count]
            amount *= {'second': 1, 'minute': 60, 'hour': 3600, 'day': 86400}[ago.group(2).lower()]
        if type_name == 'timestamp':
            script.imports.add('datetime')
            value = 'datetime.datetime.now(datetime.timezone.utc)' + (f' - datetime.timedelta(seconds={amount})' if amount else '')
        else:
            script.imports.add('time')
            value = 'int(time.time())' + (f' - {amount}' if amount else '')
    else:
        value = 'False' if type_name == 'boolean' else '0'
    script.placeholders.setdefault(name, (placeholder, value))
    return _Name(name)


def _py(value: Any, level: int = 0) -> str:
    """Render a Python literal, wrapping long dicts and lists over several lines."""
    pad = '    ' * level
    inner = '    ' * (level + 1)
    if isinstance(value, _FileRef):
        if value.as_json:
            return f"json.load(open({value.path!r}))"
        return f"open({value.path!r}, 'rb').read()" if value.binary else f"open({value.path!r}).read()"
    if isinstance(value, _Name):
        return value.name
    if isinstance(value, _FStr):
        quote = "'" if "'" not in value.template else '"'
        return f'f{quote}{value.template}{quote}'
    if isinstance(value, _JsonDoc):
        return f'json.dumps({_py(value.value, level)})'
    if isinstance(value, dict):
        items = [f'{k!r}: {_py(v, level + 1)}' for k, v in value.items()]
        flat = '{' + ', '.join(items) + '}'
        if len(flat) + len(pad) <= 72 and '\n' not in flat:
            return flat
        return '{\n' + ''.join(f'{inner}{item},\n' for item in items) + pad + '}'
    if isinstance(value, (list, tuple)):
        items = [_py(v, level + 1) for v in value]
        flat = '[' + ', '.join(items) + ']'
        if len(flat) + len(pad) <= 72 and '\n' not in flat:
            return flat
        return '[\n' + ''.join(f'{inner}{item},\n' for item in items) + pad + ']'
    return repr(value)


def _client_var(script: _Script, service: str, region: str, profile: str) -> str:
    key = (service, region, profile)
    if key in script.clients:
        return script.clients[key]
    base = service.replace('-', '_')
    if keyword.iskeyword(base):
        base += '_client'
    if region:
        base += '_' + region.replace('-', '_')
    name = base
    n = 2
    while name in script.clients.values():
        name, n = f'{base}_{n}', n + 1
    script.clients[key] = name
    return name


def _render_call(call: _Call, files, scope: set[str], script: _Script, indent: str = '') -> list[str]:
    client = _client_var(script, call.service, call.region, call.profile)
    lines: list[str] = []
    if call.note:
        lines.append(f'# {call.note}')
    if call.raw:
        if 'time.time()' in call.raw:
            script.imports.add('time')
        lines.extend(call.raw.replace('{client}', client).split('\n'))
        return [indent + line if line else line for line in lines]
    shape = None
    try:
        model = _service_model(call.service)
        if call.waiter:
            import botocore.session

            waiter_op = botocore.session.get_session().get_waiter_model(call.service).get_waiter(
                ''.join(part.capitalize() for part in call.waiter.split('_'))
            ).operation
            shape = model.operation_model(waiter_op).input_shape
        else:
            shape = model.operation_model(model.operation_names[[xform_name(n) for n in model.operation_names].index(call.method)]).input_shape
    except Exception:
        shape = None
    params = _resolve(call.params, files, scope, script, shape)
    args = [f'    {k}={_py(v, 1)},' for k, v in params.items()]
    if call.waiter:
        target = f"{client}.get_waiter({call.waiter!r}).wait"
        lines.append(f'{target}(\n' + '\n'.join(args) + '\n)' if args else f'{target}()')
        return [indent + line if line else line for line in '\n'.join(lines).split('\n')]
    head = f'response = {client}.{call.method}'
    lines.append(f'{head}(\n' + '\n'.join(args) + '\n)' if args else f'{head}()')
    if call.streaming_member and call.outfile and call.outfile != '-':
        mode = 'wb'
        lines.append(f"with open({call.outfile!r}, {mode!r}) as f:")
        lines.append(f"    f.write(response[{call.streaming_member!r}].read())")
    elif call.streaming_member:
        lines.append(f"print(response[{call.streaming_member!r}].read().decode())")
    elif call.query:
        script.imports.add('jmespath')
        lines.append(f'print(jmespath.search({call.query!r}, response))')
    else:
        lines.append('print(response)')
    return [indent + line if line else line for line in '\n'.join(lines).split('\n')]


def _segments(tokens: list[str]) -> list[list[str]]:
    """Split tokens at && ; || into commands; drop pipes and redirects after a command."""
    commands: list[list[str]] = []
    current: list[str] = []
    skipping = False
    for token in tokens:
        if token in ('&&', ';', '||', '&'):
            if current:
                commands.append(current)
            current, skipping = [], False
        elif token in ('|', '>', '>>', '<', '2>'):
            skipping = True
        elif not skipping:
            current.append(token)
    if current:
        commands.append(current)
    return commands


def _aws_index(tokens: list[str]) -> int:
    for i, token in enumerate(tokens):
        if token == 'aws':
            return i
        if not re.match(r'^[A-Z_][A-Z0-9_]*=', token):
            return -1
    return -1


_FOR_RE = re.compile(r'^for\s+(\w+)\s+in\s+(.+?)\s*;\s*do\b\s*(.*)$')


def _loop_header(var: str, words: str) -> str:
    seq = re.fullmatch(r'\$\(seq\s+(\d+)(?:\s+(\d+))?\)', words.strip())
    if seq:
        start, end = (1, int(seq.group(1))) if seq.group(2) is None else (int(seq.group(1)), int(seq.group(2)))
        return f'for {var} in range({start}, {end + 1}):'
    items = shlex.split(words)
    values = [int(w) if w.isdigit() else w for w in items]
    return f'for {var} in {values!r}:'


def generate_boto3_snippet(command: str, service_hint: str = '') -> str:
    """Translate a lab step's AWS CLI command (or short script) into boto3."""
    return _generate_boto3(command or '', service_hint)


@lru_cache(maxsize=2048)
def _generate_boto3(command: str, service_hint: str) -> str:
    script = _Script()
    scope: set[str] = set()
    indent = ''
    calls = 0
    for line in _logical_lines(command):
        code, comment = _split_comment(line.strip())
        if not code:
            script.body.append(f'{indent}# {comment}' if comment else '')
            continue
        loop = _FOR_RE.match(code)
        if loop:
            var, words, rest = loop.groups()
            script.body.append(indent + _loop_header(var, words) + (f'  # {comment}' if comment else ''))
            scope.add(var)
            indent += '    '
            code, comment = re.sub(r';?\s*done$', '', rest).strip(), ''
            if not code:
                continue
            if rest.rstrip().endswith('done'):
                loop_closes = True
            else:
                loop_closes = False
        else:
            loop_closes = False
        if re.match(r'^done\b', code):
            indent = indent[:-4]
            if comment:
                script.body.append(f'{indent}# {comment}')
            continue
        tokens = _tokenize(code)
        segments = _segments(tokens) if tokens is not None else []
        aws_segments = [seg for seg in segments if _aws_index(seg) >= 0]
        plain = len(segments) >= 1 and all(_aws_index(seg) == 0 or (_aws_index(seg) > 0 and all(
            re.match(r'^[A-Z_][A-Z0-9_]*=', t) for t in seg[:_aws_index(seg)])) for seg in segments)
        if not aws_segments:
            script.body.append(f'{indent}# Shell (run in a terminal): {code}' + (f'  # {comment}' if comment else ''))
        else:
            if not plain or (tokens and any(t in ('|', '>', '>>') for t in tokens)):
                script.body.append(f'{indent}# Shell: {code}')
            if comment:
                script.body.append(f'{indent}# {comment}')
            for seg in aws_segments:
                files: list[tuple[str, bool]] = []
                call, reason = _parse_aws(list(seg), files)
                if call is None:
                    script.body.append(f'{indent}# No boto3 equivalent ({reason}); run with the CLI:')
                    script.body.append(f'{indent}# {shlex.join(seg)}')
                    continue
                script.body.extend(_render_call(call, files, scope, script, indent))
                calls += 1
                if any(isinstance(v, _FileRef) and v.as_json for v in _walk(call.params, files)):
                    script.imports.add('json')
        if loop_closes:
            indent = indent[:-4]
        script.body.append('')

    header = ['import boto3']
    for mod in sorted(script.imports):
        header.append(f'import {mod}')
    header.append('')
    for (service, region, profile), var in script.clients.items():
        opts = ''
        if region:
            opts += f', region_name={region!r}'
        if profile:
            header.append(f'{var} = boto3.Session(profile_name={profile!r}).client({service!r}{opts})')
        else:
            header.append(f'{var} = boto3.client({service!r}{opts})')
    if not script.clients:
        body = '\n'.join(script.body).strip('\n')
        return '# This step makes no AWS API call, so there is no boto3 version.\n' + body + '\n'
    if script.placeholders:
        header.append('')
        for name, (placeholder, value) in script.placeholders.items():
            note = 'fill in' if value in ('0', 'False') else 'stands for'
            header.append(f'{name} = {value}  # {note} {placeholder}')
    body = '\n'.join(script.body).strip('\n')
    body = re.sub(r'\n{3,}', '\n\n', body)
    return ('\n'.join(header).rstrip() + '\n\n' + body).strip() + '\n'


def _walk(params, files):
    if isinstance(params, dict):
        if set(params) == {'__floci_snippet_file__'}:
            path, binary = files[params['__floci_snippet_file__']]
            yield _FileRef(path, binary, as_json=True)
            return
        for v in params.values():
            yield from _walk(v, files)
    elif isinstance(params, list):
        for v in params:
            yield from _walk(v, files)
# ---------------------------------------------------------------------------
# Terraform
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Hcl:
    """An HCL expression written as-is (file(...), jsonencode(...), var.x)."""

    expr: str


@dataclass(frozen=True)
class _JsonEncode:
    """jsonencode(<value>), rendered at the indentation where it lands."""

    value: Any


@dataclass
class _Res:
    type: str
    label: str
    body: list[tuple[str, Any]]  # (attribute, value) or (block name, _Body)


@dataclass
class _Body:
    entries: list[tuple[str, Any]]


_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]*$')
_INLINE_PLACEHOLDER_RE = re.compile(r'<([A-Za-z0-9][^<>\n]*?)>')


class _Tracked(dict):
    """A params dict that remembers which keys a Terraform builder looked at."""

    def __init__(self, *args):
        super().__init__(*args)
        self.seen: set[str] = set()

    def __getitem__(self, key):
        self.seen.add(key)
        return super().__getitem__(key)

    def get(self, key, default=None):
        self.seen.add(key)
        return super().get(key, default)

    def __contains__(self, key):
        self.seen.add(key)
        return super().__contains__(key)


# Parameters with no Terraform argument because Terraform handles them itself.
_TF_IMPLICIT = {'MinCount', 'MaxCount', 'ClientToken', 'CreatorRequestId', 'Overwrite'}


class _Tf:
    def __init__(self, files: list[tuple[str, bool]]):
        self.files = files
        self.variables: dict[str, str] = {}

    def var(self, placeholder: str) -> str:
        name = re.sub(r'\W+', '_', placeholder).strip('_').lower() or 'value'
        if name[0].isdigit():
            name = 'v_' + name
        self.variables.setdefault(name, placeholder)
        return name

    def value(self, v: Any) -> Any:
        """Swap file tokens and <placeholders> for HCL expressions."""
        if isinstance(v, dict):
            if set(v) == {'__floci_snippet_file__'}:
                return _Hcl(f'jsondecode(file({json.dumps(self.files[v["__floci_snippet_file__"]][0])}))')
            return {k: self.value(x) for k, x in v.items()}
        if isinstance(v, list):
            return [self.value(x) for x in v]
        if isinstance(v, bytes):
            v = v.decode('utf-8', 'replace')
        if isinstance(v, str):
            m = _FILE_TOKEN_RE.match(v)
            if m:
                return _Hcl(f'file({json.dumps(self.files[int(m.group(1))][0])})')
            whole = _PLACEHOLDER_RE.match(v)
            if whole:
                return _Hcl('var.' + self.var(v[1:-1]))
        return v

    def path(self, v: Any) -> str | None:
        """The local path behind a file:// or fileb:// value, if any."""
        if isinstance(v, list) and len(v) == 1:
            v = v[0]  # the CLI wraps a file:// value for a list parameter in a list
        if isinstance(v, str):
            m = _FILE_TOKEN_RE.match(v)
            if m:
                return self.files[int(m.group(1))][0]
        if isinstance(v, dict) and set(v) == {'__floci_snippet_file__'}:
            return self.files[v['__floci_snippet_file__']][0]
        return None

    def doc(self, v: Any) -> Any:
        """A JSON document attribute (policy, definition): jsonencode(...) or file(...)."""
        path = self.path(v)
        if path:
            return _Hcl(f'file({json.dumps(path)})')
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
            except ValueError:
                return self.value(v)
            return _JsonEncode(self.value(parsed))
        if isinstance(v, (dict, list)):
            return _JsonEncode(self.value(v))
        return self.value(v)


def _hcl_str(s: str, tf: _Tf | None = None) -> str:
    out = json.dumps(s, ensure_ascii=False).replace('${', '$${').replace('%{', '%%{')
    if tf is not None:
        out = _INLINE_PLACEHOLDER_RE.sub(lambda m: '${var.' + tf.var(m.group(1)) + '}', out)
    return out


def _hcl(value: Any, level: int, tf: _Tf | None = None) -> str:
    pad = '  ' * level
    inner = '  ' * (level + 1)
    if isinstance(value, _Hcl):
        return value.expr
    if isinstance(value, _JsonEncode):
        return 'jsonencode(' + _hcl(value.value, level, tf) + ')'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, float)):
        return repr(value)
    if value is None:
        return 'null'
    if isinstance(value, str):
        return _hcl_str(value, tf)
    if isinstance(value, (list, tuple)):
        items = [_hcl(v, level + 1, tf) for v in value]
        flat = '[' + ', '.join(items) + ']'
        if len(flat) + len(pad) <= 80 and '\n' not in flat:
            return flat
        return '[\n' + ''.join(f'{inner}{item},\n' for item in items) + pad + ']'
    if isinstance(value, dict):
        if not value:
            return '{}'
        keys = [k if _IDENT_RE.match(str(k)) else json.dumps(str(k)) for k in value]
        items = [f'{k} = {_hcl(v, level + 1, tf)}' for k, v in zip(keys, value.values())]
        flat = '{ ' + ', '.join(items) + ' }'
        if len(flat) + len(pad) <= 80 and '\n' not in flat:
            return flat
        return '{\n' + ''.join(f'{inner}{item}\n' for item in items) + pad + '}'
    return _hcl_str(str(value), tf)


def _align(lines: list[str]) -> list[str]:
    """Align '=' across consecutive single-line attributes, the way terraform fmt does."""
    out = list(lines)
    assign = re.compile(r'^(\s*)("[^"]*"|[A-Za-z_][A-Za-z0-9_-]*)\s*=\s?(.*)$')
    i = 0
    while i < len(out):
        m = assign.match(out[i])
        if not m:
            i += 1
            continue
        indent = m.group(1)
        opens = re.compile(r'[\[{(]$')
        if opens.search(out[i]):
            # A value that spans lines is never aligned and ends the group.
            out[i] = f'{indent}{m.group(2)} = {m.group(3)}'
            i += 1
            continue
        group = [i]
        j = i
        while j + 1 < len(out):
            nxt = assign.match(out[j + 1])
            if not nxt or nxt.group(1) != indent or opens.search(out[j + 1]):
                break
            j += 1
            group.append(j)
        width = max(len(assign.match(out[k]).group(2)) for k in group)
        for k in group:
            g = assign.match(out[k])
            out[k] = f'{g.group(1)}{g.group(2).ljust(width)} = {g.group(3)}'
        i = j + 1
    return out


def _render_body(entries: list[tuple[str, Any]], level: int, tf: _Tf) -> list[str]:
    pad = '  ' * level
    lines: list[str] = []
    attrs = [(k, v) for k, v in entries if not isinstance(v, _Body) and v is not None]
    blocks = [(k, v) for k, v in entries if isinstance(v, _Body)]
    for k, v in attrs:
        lines.extend(f'{pad}{k} = {_hcl(v, level, tf)}'.split('\n'))
    for k, v in blocks:
        if lines:
            lines.append('')
        lines.append(f'{pad}{k} {{')
        lines.extend(_render_body(v.entries, level + 1, tf))
        lines.append(f'{pad}}}')
    return lines


def _label(hint: Any, fallback: str, used: set[str]) -> str:
    text = hint if isinstance(hint, str) and '<' not in hint and hint != 'lab' else ''
    text = re.sub(r'^(arn:.*[:/])', '', text)
    label = re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_') or fallback
    if label[0].isdigit():
        label = f'{fallback}_{label}'
    candidate, n = label, 2
    while candidate in used:
        candidate, n = f'{label}_{n}', n + 1
    used.add(candidate)
    return candidate


def _tags(p: dict, tf: _Tf, key: str = 'Tags') -> dict | None:
    """Tags from a Key/Value list, a plain map, or EC2-style TagSpecifications."""
    raw = p.get(key)
    if raw is None and p.get('TagSpecifications'):
        specs = p['TagSpecifications']
        raw = specs[0].get('Tags') if isinstance(specs, list) and specs and isinstance(specs[0], dict) else None
    if isinstance(raw, list):
        return {t.get('Key', t.get('key')): tf.value(t.get('Value', t.get('value'))) for t in raw if isinstance(t, dict)}
    if isinstance(raw, dict) and set(raw) != {'__floci_snippet_file__'}:
        return {k: tf.value(v) for k, v in raw.items()}
    return None


def _pick(p: dict, tf: _Tf, mapping: dict[str, str]) -> list[tuple[str, Any]]:
    return [(tf_name, tf.value(p[api])) for api, tf_name in mapping.items() if api in p]


def _num(v: Any) -> Any:
    if isinstance(v, str) and re.fullmatch(r'-?\d+', v):
        return int(v)
    if isinstance(v, str) and v.lower() in ('true', 'false'):
        return v.lower() == 'true'
    return v


# Each builder takes the API parameters and returns resources (or None to fall back to a note).

def _tf_s3_bucket(p, tf):
    return [_Res('aws_s3_bucket', p.get('Bucket'), [('bucket', tf.value(p.get('Bucket')))])]


def _tf_s3_versioning(p, tf):
    status = (p.get('VersioningConfiguration') or {}).get('Status', 'Enabled')
    return [_Res('aws_s3_bucket_versioning', p.get('Bucket'), [
        ('bucket', tf.value(p.get('Bucket'))),
        ('versioning_configuration', _Body([('status', tf.value(status))])),
    ])]


def _tf_s3_policy(p, tf):
    return [_Res('aws_s3_bucket_policy', p.get('Bucket'), [('bucket', tf.value(p.get('Bucket'))), ('policy', tf.doc(p.get('Policy')))])]


def _tf_s3_encryption(p, tf):
    config = p.get('ServerSideEncryptionConfiguration')
    if not isinstance(config, dict) or tf.path(config):
        return None
    rules = []
    for rule in config.get('Rules', []):
        default = rule.get('ApplyServerSideEncryptionByDefault', {})
        entries = [('apply_server_side_encryption_by_default', _Body(_pick(default, tf, {
            'SSEAlgorithm': 'sse_algorithm', 'KMSMasterKeyID': 'kms_master_key_id'})))]
        if 'BucketKeyEnabled' in rule:
            entries.insert(0, ('bucket_key_enabled', rule['BucketKeyEnabled']))
        rules.append(('rule', _Body(entries)))
    return [_Res('aws_s3_bucket_server_side_encryption_configuration', p.get('Bucket'), [('bucket', tf.value(p.get('Bucket'))), *rules])]


def _tf_s3_public_access_block(p, tf):
    config = p.get('PublicAccessBlockConfiguration') or {}
    return [_Res('aws_s3_bucket_public_access_block', p.get('Bucket'), [('bucket', tf.value(p.get('Bucket'))), *_pick(config, tf, {
        'BlockPublicAcls': 'block_public_acls', 'IgnorePublicAcls': 'ignore_public_acls',
        'BlockPublicPolicy': 'block_public_policy', 'RestrictPublicBuckets': 'restrict_public_buckets'})])]


def _tf_s3_object(p, tf):
    body = [('bucket', tf.value(p.get('Bucket'))), ('key', tf.value(p.get('Key')))]
    source = tf.path(p.get('Body'))
    if source:
        body.append(('source', source))
    body += _pick(p, tf, {'ContentType': 'content_type', 'ServerSideEncryption': 'server_side_encryption',
                          'SSEKMSKeyId': 'kms_key_id', 'StorageClass': 'storage_class', 'CacheControl': 'cache_control'})
    if isinstance(p.get('Metadata'), dict):
        body.append(('metadata', tf.value(p['Metadata'])))
    return [_Res('aws_s3_object', p.get('Key'), body)]


_SQS_ATTRS = {
    'DelaySeconds': 'delay_seconds', 'MaximumMessageSize': 'max_message_size',
    'MessageRetentionPeriod': 'message_retention_seconds', 'ReceiveMessageWaitTimeSeconds': 'receive_wait_time_seconds',
    'VisibilityTimeout': 'visibility_timeout_seconds', 'FifoQueue': 'fifo_queue',
    'ContentBasedDeduplication': 'content_based_deduplication', 'KmsMasterKeyId': 'kms_master_key_id',
    'SqsManagedSseEnabled': 'sqs_managed_sse_enabled', 'DeduplicationScope': 'deduplication_scope',
    'FifoThroughputLimit': 'fifo_throughput_limit',
}


def _tf_sqs_queue(p, tf):
    attrs = p.get('Attributes') or {}
    if tf.path(attrs):
        return None
    body = [('name', tf.value(p.get('QueueName')))]
    if str(p.get('QueueName', '')).endswith('.fifo') and 'FifoQueue' not in attrs:
        body.append(('fifo_queue', True))
    body += [(tf_name, tf.value(_num(attrs[k]))) for k, tf_name in _SQS_ATTRS.items() if k in attrs]
    for k, tf_name in (('RedrivePolicy', 'redrive_policy'), ('RedriveAllowPolicy', 'redrive_allow_policy'), ('Policy', 'policy')):
        if k in attrs:
            body.append((tf_name, tf.doc(attrs[k])))
    tags = _tags({'Tags': p.get('tags')}, tf)
    if tags:
        body.append(('tags', tags))
    return [_Res('aws_sqs_queue', p.get('QueueName'), body)]


def _tf_sns_topic(p, tf):
    attrs = p.get('Attributes') or {}
    body = [('name', tf.value(p.get('Name')))]
    body += [(t, tf.value(_num(attrs[k]))) for k, t in {
        'FifoTopic': 'fifo_topic', 'ContentBasedDeduplication': 'content_based_deduplication',
        'KmsMasterKeyId': 'kms_master_key_id', 'DisplayName': 'display_name'}.items() if k in attrs]
    if 'Policy' in attrs:
        body.append(('policy', tf.doc(attrs['Policy'])))
    tags = _tags(p, tf)
    if tags:
        body.append(('tags', tags))
    return [_Res('aws_sns_topic', p.get('Name'), body)]


def _tf_sns_subscription(p, tf):
    body = [('topic_arn', tf.value(p.get('TopicArn'))), ('protocol', tf.value(p.get('Protocol'))),
            ('endpoint', tf.value(p.get('Endpoint')))]
    attrs = p.get('Attributes') or {}
    if 'RawMessageDelivery' in attrs:
        body.append(('raw_message_delivery', _num(attrs['RawMessageDelivery'])))
    if 'FilterPolicy' in attrs:
        body.append(('filter_policy', tf.doc(attrs['FilterPolicy'])))
    if 'RedrivePolicy' in attrs:
        body.append(('redrive_policy', tf.doc(attrs['RedrivePolicy'])))
    return [_Res('aws_sns_topic_subscription', p.get('Endpoint'), body)]


def _tf_iam_role(p, tf):
    body = [('name', tf.value(p.get('RoleName'))), ('assume_role_policy', tf.doc(p.get('AssumeRolePolicyDocument')))]
    body += _pick(p, tf, {'Description': 'description', 'Path': 'path', 'MaxSessionDuration': 'max_session_duration',
                          'PermissionsBoundary': 'permissions_boundary'})
    tags = _tags(p, tf)
    if tags:
        body.append(('tags', tags))
    return [_Res('aws_iam_role', p.get('RoleName'), body)]


def _tf_iam_inline(kind):
    def build(p, tf):
        owner = {'role': 'RoleName', 'user': 'UserName', 'group': 'GroupName'}[kind]
        return [_Res(f'aws_iam_{kind}_policy', p.get('PolicyName'), [
            ('name', tf.value(p.get('PolicyName'))), (kind, tf.value(p.get(owner))), ('policy', tf.doc(p.get('PolicyDocument')))])]
    return build


def _tf_iam_attach(kind):
    def build(p, tf):
        owner = {'role': 'RoleName', 'user': 'UserName', 'group': 'GroupName'}[kind]
        return [_Res(f'aws_iam_{kind}_policy_attachment', f'{p.get(owner)}_{str(p.get("PolicyArn", "")).rsplit("/", 1)[-1]}', [
            (kind, tf.value(p.get(owner))), ('policy_arn', tf.value(p.get('PolicyArn')))])]
    return build


def _tf_iam_user(p, tf):
    body = [('name', tf.value(p.get('UserName')))] + _pick(p, tf, {'Path': 'path', 'PermissionsBoundary': 'permissions_boundary'})
    tags = _tags(p, tf)
    if tags:
        body.append(('tags', tags))
    return [_Res('aws_iam_user', p.get('UserName'), body)]


def _tf_iam_policy(p, tf):
    body = [('name', tf.value(p.get('PolicyName')))] + _pick(p, tf, {'Description': 'description', 'Path': 'path'})
    body.append(('policy', tf.doc(p.get('PolicyDocument'))))
    return [_Res('aws_iam_policy', p.get('PolicyName'), body)]


def _tf_iam_group(p, tf):
    return [_Res('aws_iam_group', p.get('GroupName'), [('name', tf.value(p.get('GroupName')))])]


def _tf_iam_group_membership(p, tf):
    return [_Res('aws_iam_user_group_membership', p.get('UserName'), [
        ('user', tf.value(p.get('UserName'))), ('groups', [tf.value(p.get('GroupName'))])])]


def _tf_iam_instance_profile(p, tf):
    return [_Res('aws_iam_instance_profile', p.get('InstanceProfileName'), [('name', tf.value(p.get('InstanceProfileName')))])]


def _tf_iam_access_key(p, tf):
    return [_Res('aws_iam_access_key', p.get('UserName'), [('user', tf.value(p.get('UserName')))])]


def _tf_iam_login_profile(p, tf):
    body = [('user', tf.value(p.get('UserName')))]
    if 'PasswordResetRequired' in p:
        body.append(('password_reset_required', p['PasswordResetRequired']))
    return [_Res('aws_iam_user_login_profile', p.get('UserName'), body)]


def _tf_dynamodb_table(p, tf):
    if any(tf.path(p.get(k)) for k in ('AttributeDefinitions', 'KeySchema', 'GlobalSecondaryIndexes')):
        return None
    keys = {k['KeyType']: k['AttributeName'] for k in p.get('KeySchema') or [] if isinstance(k, dict)}
    body = [('name', tf.value(p.get('TableName'))), ('billing_mode', p.get('BillingMode', 'PROVISIONED'))]
    if 'HASH' in keys:
        body.append(('hash_key', keys['HASH']))
    if 'RANGE' in keys:
        body.append(('range_key', keys['RANGE']))
    throughput = p.get('ProvisionedThroughput') or {}
    if 'ReadCapacityUnits' in throughput:
        body.append(('read_capacity', throughput['ReadCapacityUnits']))
    if 'WriteCapacityUnits' in throughput:
        body.append(('write_capacity', throughput['WriteCapacityUnits']))
    stream = p.get('StreamSpecification') or {}
    if stream.get('StreamEnabled'):
        body += [('stream_enabled', True), ('stream_view_type', stream.get('StreamViewType'))]
    if p.get('TableClass'):
        body.append(('table_class', p['TableClass']))
    for attr in p.get('AttributeDefinitions') or []:
        body.append(('attribute', _Body([('name', attr['AttributeName']), ('type', attr['AttributeType'])])))
    for gsi in p.get('GlobalSecondaryIndexes') or []:
        gkeys = {k['KeyType']: k['AttributeName'] for k in gsi.get('KeySchema', [])}
        projection = gsi.get('Projection') or {}
        entries = [('name', gsi.get('IndexName')), ('hash_key', gkeys.get('HASH'))]
        if 'RANGE' in gkeys:
            entries.append(('range_key', gkeys['RANGE']))
        entries.append(('projection_type', projection.get('ProjectionType', 'ALL')))
        if projection.get('NonKeyAttributes'):
            entries.append(('non_key_attributes', projection['NonKeyAttributes']))
        gt = gsi.get('ProvisionedThroughput') or {}
        if 'ReadCapacityUnits' in gt:
            entries += [('read_capacity', gt['ReadCapacityUnits']), ('write_capacity', gt.get('WriteCapacityUnits'))]
        body.append(('global_secondary_index', _Body(entries)))
    tags = _tags(p, tf)
    if tags:
        body.append(('tags', tags))
    return [_Res('aws_dynamodb_table', p.get('TableName'), body)]


def _tf_lambda_function(p, tf):
    body = [('function_name', tf.value(p.get('FunctionName'))), ('role', tf.value(p.get('Role')))]
    body += _pick(p, tf, {'Runtime': 'runtime', 'Handler': 'handler'})
    code = p.get('Code') or {}
    zip_path = tf.path(code.get('ZipFile')) if isinstance(code, dict) else None
    if zip_path:
        body += [('filename', zip_path), ('source_code_hash', _Hcl(f'filebase64sha256({json.dumps(zip_path)})'))]
    elif isinstance(code, dict) and code.get('S3Bucket'):
        body += [('s3_bucket', tf.value(code['S3Bucket'])), ('s3_key', tf.value(code.get('S3Key')))]
    elif isinstance(code, dict) and code.get('ImageUri'):
        body += [('package_type', 'Image'), ('image_uri', tf.value(code['ImageUri']))]
    body += _pick(p, tf, {'Timeout': 'timeout', 'MemorySize': 'memory_size', 'Description': 'description',
                          'Architectures': 'architectures', 'Publish': 'publish', 'KMSKeyArn': 'kms_key_arn'})
    env = (p.get('Environment') or {}).get('Variables')
    if env:
        body.append(('environment', _Body([('variables', tf.value(env))])))
    tracing = (p.get('TracingConfig') or {}).get('Mode')
    if tracing:
        body.append(('tracing_config', _Body([('mode', tracing)])))
    dlq = (p.get('DeadLetterConfig') or {}).get('TargetArn')
    if dlq:
        body.append(('dead_letter_config', _Body([('target_arn', tf.value(dlq))])))
    vpc = p.get('VpcConfig') or {}
    if vpc.get('SubnetIds'):
        body.append(('vpc_config', _Body([('subnet_ids', tf.value(vpc['SubnetIds'])),
                                          ('security_group_ids', tf.value(vpc.get('SecurityGroupIds', [])))])))
    return [_Res('aws_lambda_function', p.get('FunctionName'), body)]


def _tf_lambda_permission(p, tf):
    body = [('statement_id', tf.value(p.get('StatementId'))), ('action', tf.value(p.get('Action'))),
            ('function_name', tf.value(p.get('FunctionName'))), ('principal', tf.value(p.get('Principal')))]
    body += _pick(p, tf, {'SourceArn': 'source_arn', 'SourceAccount': 'source_account', 'Qualifier': 'qualifier',
                          'FunctionUrlAuthType': 'function_url_auth_type'})
    return [_Res('aws_lambda_permission', p.get('StatementId'), body)]


def _tf_lambda_alias(p, tf):
    body = [('name', tf.value(p.get('Name'))), ('function_name', tf.value(p.get('FunctionName'))),
            ('function_version', tf.value(p.get('FunctionVersion')))]
    weights = (p.get('RoutingConfig') or {}).get('AdditionalVersionWeights')
    if weights:
        body.append(('routing_config', _Body([('additional_version_weights', tf.value(weights))])))
    return [_Res('aws_lambda_alias', p.get('Name'), body)]


def _tf_lambda_esm(p, tf):
    body = [('event_source_arn', tf.value(p.get('EventSourceArn'))), ('function_name', tf.value(p.get('FunctionName')))]
    body += _pick(p, tf, {'BatchSize': 'batch_size', 'StartingPosition': 'starting_position', 'Enabled': 'enabled',
                          'MaximumBatchingWindowInSeconds': 'maximum_batching_window_in_seconds',
                          'FunctionResponseTypes': 'function_response_types'})
    return [_Res('aws_lambda_event_source_mapping', p.get('FunctionName'), body)]


def _tf_kms_key(p, tf):
    body = _pick(p, tf, {'Description': 'description', 'KeyUsage': 'key_usage', 'KeySpec': 'customer_master_key_spec',
                         'MultiRegion': 'multi_region'})
    if p.get('Policy'):
        body.append(('policy', tf.doc(p['Policy'])))
    body.append(('deletion_window_in_days', 7))
    tags = _tags(p, tf)
    if tags:
        body.append(('tags', tags))
    return [_Res('aws_kms_key', p.get('Description') or 'lab', body)]


def _tf_kms_alias(p, tf):
    return [_Res('aws_kms_alias', str(p.get('AliasName', '')).replace('alias/', ''), [
        ('name', tf.value(p.get('AliasName'))), ('target_key_id', tf.value(p.get('TargetKeyId')))])]


def _tf_ssm_parameter(p, tf):
    body = [('name', tf.value(p.get('Name'))), ('type', tf.value(p.get('Type', 'String'))), ('value', tf.value(p.get('Value')))]
    body += _pick(p, tf, {'Description': 'description', 'Tier': 'tier', 'KeyId': 'key_id', 'DataType': 'data_type'})
    if p.get('Overwrite'):
        body.append(('overwrite', True))
    return [_Res('aws_ssm_parameter', p.get('Name'), body)]


def _tf_secret(p, tf):
    body = [('name', tf.value(p.get('Name')))] + _pick(p, tf, {'Description': 'description', 'KmsKeyId': 'kms_key_id'})
    resources = [_Res('aws_secretsmanager_secret', p.get('Name'), body)]
    if 'SecretString' in p:
        resources.append(_Res('aws_secretsmanager_secret_version', p.get('Name'), [
            ('secret_id', _Hcl('PREVIOUS.id')), ('secret_string', tf.doc(p['SecretString']))]))
    return resources


def _tf_log_group(p, tf):
    body = [('name', tf.value(p.get('logGroupName')))] + _pick(p, tf, {'kmsKeyId': 'kms_key_id', 'logGroupClass': 'log_group_class'})
    tags = _tags({'Tags': p.get('tags')}, tf)
    if tags:
        body.append(('tags', tags))
    return [_Res('aws_cloudwatch_log_group', p.get('logGroupName'), body)]


def _tf_log_stream(p, tf):
    return [_Res('aws_cloudwatch_log_stream', p.get('logStreamName'), [
        ('name', tf.value(p.get('logStreamName'))), ('log_group_name', tf.value(p.get('logGroupName')))])]


def _tf_metric_filter(p, tf):
    transforms = p.get('metricTransformations') or []
    if not transforms or tf.path(transforms):
        return None
    t = transforms[0]
    entries = [('name', tf.value(t.get('metricName'))), ('namespace', tf.value(t.get('metricNamespace'))),
               ('value', tf.value(t.get('metricValue')))]
    if 'defaultValue' in t:
        entries.append(('default_value', tf.value(str(t['defaultValue']))))
    if 'unit' in t:
        entries.append(('unit', tf.value(t['unit'])))
    return [_Res('aws_cloudwatch_log_metric_filter', p.get('filterName'), [
        ('name', tf.value(p.get('filterName'))), ('log_group_name', tf.value(p.get('logGroupName'))),
        ('pattern', tf.value(p.get('filterPattern', ''))), ('metric_transformation', _Body(entries))])]


def _tf_subscription_filter(p, tf):
    body = [('name', tf.value(p.get('filterName'))), ('log_group_name', tf.value(p.get('logGroupName'))),
            ('filter_pattern', tf.value(p.get('filterPattern', ''))), ('destination_arn', tf.value(p.get('destinationArn')))]
    body += _pick(p, tf, {'roleArn': 'role_arn', 'distribution': 'distribution'})
    return [_Res('aws_cloudwatch_log_subscription_filter', p.get('filterName'), body)]


def _tf_metric_alarm(p, tf):
    body = [('alarm_name', tf.value(p.get('AlarmName')))]
    body += _pick(p, tf, {
        'AlarmDescription': 'alarm_description', 'ComparisonOperator': 'comparison_operator',
        'EvaluationPeriods': 'evaluation_periods', 'DatapointsToAlarm': 'datapoints_to_alarm',
        'MetricName': 'metric_name', 'Namespace': 'namespace', 'Period': 'period', 'Statistic': 'statistic',
        'ExtendedStatistic': 'extended_statistic', 'Threshold': 'threshold', 'Unit': 'unit',
        'TreatMissingData': 'treat_missing_data', 'ActionsEnabled': 'actions_enabled',
        'AlarmActions': 'alarm_actions', 'OKActions': 'ok_actions', 'InsufficientDataActions': 'insufficient_data_actions',
        'EvaluateLowSampleCountPercentile': 'evaluate_low_sample_count_percentiles',
        'ThresholdMetricId': 'threshold_metric_id',
    })
    dims = p.get('Dimensions')
    if isinstance(dims, list) and dims:
        body.append(('dimensions', {d['Name']: tf.value(d['Value']) for d in dims if isinstance(d, dict)}))
    for metric in p.get('Metrics') or []:
        if not isinstance(metric, dict):
            return None
        entries = _pick(metric, tf, {'Id': 'id', 'Expression': 'expression', 'Label': 'label', 'ReturnData': 'return_data'})
        stat = metric.get('MetricStat')
        if stat:
            m = stat.get('Metric', {})
            inner = _pick(m, tf, {'MetricName': 'metric_name', 'Namespace': 'namespace'})
            inner += _pick(stat, tf, {'Period': 'period', 'Stat': 'stat', 'Unit': 'unit'})
            if m.get('Dimensions'):
                inner.append(('dimensions', {d['Name']: tf.value(d['Value']) for d in m['Dimensions']}))
            entries.append(('metric', _Body(inner)))
        body.append(('metric_query', _Body(entries)))
    return [_Res('aws_cloudwatch_metric_alarm', p.get('AlarmName'), body)]


def _tf_composite_alarm(p, tf):
    body = [('alarm_name', tf.value(p.get('AlarmName'))), ('alarm_rule', tf.value(p.get('AlarmRule')))]
    body += _pick(p, tf, {'AlarmDescription': 'alarm_description', 'AlarmActions': 'alarm_actions', 'OKActions': 'ok_actions',
                          'ActionsEnabled': 'actions_enabled'})
    return [_Res('aws_cloudwatch_composite_alarm', p.get('AlarmName'), body)]


def _tf_dashboard(p, tf):
    return [_Res('aws_cloudwatch_dashboard', p.get('DashboardName'), [
        ('dashboard_name', tf.value(p.get('DashboardName'))), ('dashboard_body', tf.doc(p.get('DashboardBody')))])]


def _tf_event_rule(p, tf):
    body = [('name', tf.value(p.get('Name')))]
    body += _pick(p, tf, {'EventBusName': 'event_bus_name', 'Description': 'description',
                          'ScheduleExpression': 'schedule_expression', 'RoleArn': 'role_arn', 'State': 'state'})
    if p.get('EventPattern'):
        body.append(('event_pattern', tf.doc(p['EventPattern'])))
    return [_Res('aws_cloudwatch_event_rule', p.get('Name'), body)]


def _tf_event_targets(p, tf):
    targets = p.get('Targets')
    if not isinstance(targets, list) or tf.path(targets):
        return None
    resources = []
    for target in targets:
        body = [('rule', tf.value(p.get('Rule')))]
        body += _pick(p, tf, {'EventBusName': 'event_bus_name'})
        body += _pick(target, tf, {'Id': 'target_id', 'Arn': 'arn', 'RoleArn': 'role_arn', 'InputPath': 'input_path'})
        if 'Input' in target:
            body.append(('input', tf.doc(target['Input'])))
        dlq = (target.get('DeadLetterConfig') or {}).get('Arn')
        if dlq:
            body.append(('dead_letter_config', _Body([('arn', tf.value(dlq))])))
        retry = target.get('RetryPolicy')
        if retry:
            body.append(('retry_policy', _Body(_pick(retry, tf, {
                'MaximumEventAgeInSeconds': 'maximum_event_age_in_seconds', 'MaximumRetryAttempts': 'maximum_retry_attempts'}))))
        transformer = target.get('InputTransformer')
        if transformer:
            entries = []
            if transformer.get('InputPathsMap'):
                entries.append(('input_paths', tf.value(transformer['InputPathsMap'])))
            entries.append(('input_template', tf.value(transformer.get('InputTemplate'))))
            body.append(('input_transformer', _Body(entries)))
        resources.append(_Res('aws_cloudwatch_event_target', f'{p.get("Rule")}_{target.get("Id")}', body))
    return resources


def _tf_event_bus(p, tf):
    return [_Res('aws_cloudwatch_event_bus', p.get('Name'), [('name', tf.value(p.get('Name')))])]


def _tf_event_archive(p, tf):
    body = [('name', tf.value(p.get('ArchiveName'))), ('event_source_arn', tf.value(p.get('EventSourceArn')))]
    body += _pick(p, tf, {'RetentionDays': 'retention_days', 'Description': 'description'})
    if p.get('EventPattern'):
        body.append(('event_pattern', tf.doc(p['EventPattern'])))
    return [_Res('aws_cloudwatch_event_archive', p.get('ArchiveName'), body)]


def _with_tags(body, p, tf):
    tags = _tags(p, tf)
    if tags:
        body.append(('tags', tags))
    return body


def _name_tag(p, fallback):
    tags = p.get('TagSpecifications') or []
    for spec in tags if isinstance(tags, list) else []:
        for t in spec.get('Tags', []) if isinstance(spec, dict) else []:
            if t.get('Key') == 'Name':
                return t.get('Value')
    return fallback


def _tf_vpc(p, tf):
    body = _pick(p, tf, {'CidrBlock': 'cidr_block', 'InstanceTenancy': 'instance_tenancy'})
    return [_Res('aws_vpc', _name_tag(p, 'lab'), _with_tags(body, p, tf))]


def _tf_subnet(p, tf):
    body = _pick(p, tf, {'VpcId': 'vpc_id', 'CidrBlock': 'cidr_block', 'AvailabilityZone': 'availability_zone',
                         'Ipv6CidrBlock': 'ipv6_cidr_block'})
    return [_Res('aws_subnet', _name_tag(p, p.get('AvailabilityZone') or 'lab'), _with_tags(body, p, tf))]


def _tf_igw(p, tf):
    return [_Res('aws_internet_gateway', _name_tag(p, 'lab'), _with_tags([], p, tf))]


def _tf_igw_attachment(p, tf):
    return [_Res('aws_internet_gateway_attachment', 'lab', [
        ('internet_gateway_id', tf.value(p.get('InternetGatewayId'))), ('vpc_id', tf.value(p.get('VpcId')))])]


def _tf_route_table(p, tf):
    return [_Res('aws_route_table', _name_tag(p, 'lab'), _with_tags(_pick(p, tf, {'VpcId': 'vpc_id'}), p, tf))]


def _tf_route(p, tf):
    body = _pick(p, tf, {
        'RouteTableId': 'route_table_id', 'DestinationCidrBlock': 'destination_cidr_block',
        'DestinationIpv6CidrBlock': 'destination_ipv6_cidr_block', 'DestinationPrefixListId': 'destination_prefix_list_id',
        'GatewayId': 'gateway_id', 'NatGatewayId': 'nat_gateway_id', 'TransitGatewayId': 'transit_gateway_id',
        'VpcPeeringConnectionId': 'vpc_peering_connection_id', 'EgressOnlyInternetGatewayId': 'egress_only_gateway_id',
        'NetworkInterfaceId': 'network_interface_id', 'VpcEndpointId': 'vpc_endpoint_id',
    })
    return [_Res('aws_route', 'default' if p.get('DestinationCidrBlock') == '0.0.0.0/0' else 'lab', body)]


def _tf_route_table_association(p, tf):
    body = _pick(p, tf, {'SubnetId': 'subnet_id', 'GatewayId': 'gateway_id', 'RouteTableId': 'route_table_id'})
    return [_Res('aws_route_table_association', p.get('SubnetId') or 'lab', body)]


def _tf_security_group(p, tf):
    body = _pick(p, tf, {'GroupName': 'name', 'Description': 'description', 'VpcId': 'vpc_id'})
    return [_Res('aws_security_group', p.get('GroupName'), _with_tags(body, p, tf))]


def _tf_sg_ingress(p, tf):
    group = tf.value(p.get('GroupId') or p.get('GroupName'))
    perms = p.get('IpPermissions')
    if perms is None:
        perms = [{'IpProtocol': p.get('IpProtocol', 'tcp'), 'FromPort': p.get('FromPort'), 'ToPort': p.get('ToPort'),
                  'IpRanges': [{'CidrIp': p['CidrIp']}] if p.get('CidrIp') else [],
                  'UserIdGroupPairs': [{'GroupId': p['SourceSecurityGroupName']}] if p.get('SourceSecurityGroupName') else []}]
    if not isinstance(perms, list) or tf.path(perms):
        return None
    resources = []
    for perm in perms:
        base = [('security_group_id', group), ('ip_protocol', tf.value(str(perm.get('IpProtocol', 'tcp'))))]
        if perm.get('FromPort') is not None and str(perm.get('IpProtocol')) != '-1':
            base += [('from_port', perm.get('FromPort')), ('to_port', perm.get('ToPort', perm.get('FromPort')))]
        sources = [('cidr_ipv4', r.get('CidrIp')) for r in perm.get('IpRanges') or []]
        sources += [('cidr_ipv6', r.get('CidrIpv6')) for r in perm.get('Ipv6Ranges') or []]
        sources += [('referenced_security_group_id', r.get('GroupId') or r.get('GroupName'))
                    for r in perm.get('UserIdGroupPairs') or []]
        sources += [('prefix_list_id', r.get('PrefixListId')) for r in perm.get('PrefixListIds') or []]
        for key, source in sources or [(None, None)]:
            body = list(base)
            if key:
                body.append((key, tf.value(source)))
            port = perm.get('FromPort')
            resources.append(_Res('aws_vpc_security_group_ingress_rule', f'port_{port}' if port is not None else 'all', body))
    return resources


def _tf_instance(p, tf):
    body = _pick(p, tf, {'ImageId': 'ami', 'InstanceType': 'instance_type', 'SubnetId': 'subnet_id',
                         'KeyName': 'key_name', 'SecurityGroupIds': 'vpc_security_group_ids'})
    if 'InstanceType' not in p and not p.get('LaunchTemplate'):
        body.append(('instance_type', 'm1.small'))  # the API default when the CLI omits it
    profile = p.get('IamInstanceProfile') or {}
    if profile.get('Name') or profile.get('Arn'):
        body.append(('iam_instance_profile', tf.value(profile.get('Name') or profile.get('Arn'))))
    if p.get('UserData'):
        body.append(('user_data', tf.value(p['UserData'])))
    if p.get('MaxCount', 1) > 1 if isinstance(p.get('MaxCount', 1), int) else False:
        body.insert(0, ('count', p['MaxCount']))
    lt = p.get('LaunchTemplate')
    if isinstance(lt, dict):
        body.append(('launch_template', _Body(_pick(lt, tf, {
            'LaunchTemplateName': 'name', 'LaunchTemplateId': 'id', 'Version': 'version'}))))
    metadata = p.get('MetadataOptions')
    if isinstance(metadata, dict):
        body.append(('metadata_options', _Body(_pick(metadata, tf, {
            'HttpTokens': 'http_tokens', 'HttpEndpoint': 'http_endpoint', 'HttpPutResponseHopLimit': 'http_put_response_hop_limit'}))))
    return [_Res('aws_instance', _name_tag(p, 'lab'), _with_tags(body, p, tf))]


def _tf_eip(p, tf):
    return [_Res('aws_eip', _name_tag(p, 'lab'), _with_tags([('domain', 'vpc')], p, tf))]


def _tf_nat_gateway(p, tf):
    body = _pick(p, tf, {'AllocationId': 'allocation_id', 'SubnetId': 'subnet_id', 'ConnectivityType': 'connectivity_type'})
    return [_Res('aws_nat_gateway', _name_tag(p, 'lab'), _with_tags(body, p, tf))]


def _tf_volume(p, tf):
    body = _pick(p, tf, {'AvailabilityZone': 'availability_zone', 'Size': 'size', 'VolumeType': 'type', 'Iops': 'iops',
                         'Throughput': 'throughput', 'Encrypted': 'encrypted', 'KmsKeyId': 'kms_key_id',
                         'SnapshotId': 'snapshot_id'})
    return [_Res('aws_ebs_volume', _name_tag(p, 'lab'), _with_tags(body, p, tf))]


def _tf_vpc_endpoint(p, tf):
    body = _pick(p, tf, {'VpcId': 'vpc_id', 'ServiceName': 'service_name', 'VpcEndpointType': 'vpc_endpoint_type',
                         'RouteTableIds': 'route_table_ids', 'SubnetIds': 'subnet_ids', 'SecurityGroupIds': 'security_group_ids',
                         'PrivateDnsEnabled': 'private_dns_enabled'})
    if p.get('PolicyDocument'):
        body.append(('policy', tf.doc(p['PolicyDocument'])))
    return [_Res('aws_vpc_endpoint', str(p.get('ServiceName', 'lab')).rsplit('.', 1)[-1], _with_tags(body, p, tf))]


def _tf_network_acl(p, tf):
    return [_Res('aws_network_acl', _name_tag(p, 'lab'), _with_tags(_pick(p, tf, {'VpcId': 'vpc_id'}), p, tf))]


def _tf_network_acl_rule(p, tf):
    body = _pick(p, tf, {'NetworkAclId': 'network_acl_id', 'RuleNumber': 'rule_number', 'Egress': 'egress',
                         'Protocol': 'protocol', 'RuleAction': 'rule_action', 'CidrBlock': 'cidr_block'})
    ports = p.get('PortRange') or {}
    if 'From' in ports:
        body += [('from_port', ports['From']), ('to_port', ports.get('To', ports['From']))]
    return [_Res('aws_network_acl_rule', f'rule_{p.get("RuleNumber")}', body)]


def _tf_lb(p, tf):
    body = _pick(p, tf, {'Name': 'name', 'Type': 'load_balancer_type', 'Scheme': 'internal', 'Subnets': 'subnets',
                         'SecurityGroups': 'security_groups', 'IpAddressType': 'ip_address_type'})
    body = [(k, (v == 'internal') if k == 'internal' else v) for k, v in body]
    return [_Res('aws_lb', p.get('Name'), _with_tags(body, p, tf))]


def _tf_target_group(p, tf):
    body = _pick(p, tf, {'Name': 'name', 'Protocol': 'protocol', 'Port': 'port', 'VpcId': 'vpc_id', 'TargetType': 'target_type'})
    health = _pick(p, tf, {'HealthCheckPath': 'path', 'HealthCheckProtocol': 'protocol',
                           'HealthCheckIntervalSeconds': 'interval', 'HealthyThresholdCount': 'healthy_threshold',
                           'UnhealthyThresholdCount': 'unhealthy_threshold', 'HealthCheckTimeoutSeconds': 'timeout',
                           'HealthCheckPort': 'port'})
    if health:
        body.append(('health_check', _Body(health)))
    return [_Res('aws_lb_target_group', p.get('Name'), _with_tags(body, p, tf))]


def _tf_listener(p, tf):
    body = _pick(p, tf, {'LoadBalancerArn': 'load_balancer_arn', 'Port': 'port', 'Protocol': 'protocol',
                         'SslPolicy': 'ssl_policy'})
    certs = p.get('Certificates') or []
    if certs:
        body.append(('certificate_arn', tf.value(certs[0].get('CertificateArn'))))
    for action in p.get('DefaultActions') or []:
        if not isinstance(action, dict):
            return None
        entries = _pick(action, tf, {'Type': 'type', 'TargetGroupArn': 'target_group_arn'})
        fixed = action.get('FixedResponseConfig')
        if fixed:
            entries.append(('fixed_response', _Body(_pick(fixed, tf, {
                'ContentType': 'content_type', 'MessageBody': 'message_body', 'StatusCode': 'status_code'}))))
        redirect = action.get('RedirectConfig')
        if redirect:
            entries.append(('redirect', _Body(_pick(redirect, tf, {
                'Protocol': 'protocol', 'Port': 'port', 'StatusCode': 'status_code'}))))
        forward = action.get('ForwardConfig')
        if forward and not action.get('TargetGroupArn'):
            groups = [('target_group', _Body(_pick(g, tf, {'TargetGroupArn': 'arn', 'Weight': 'weight'})))
                      for g in forward.get('TargetGroups', [])]
            entries.append(('forward', _Body(groups)))
        body.append(('default_action', _Body(entries)))
    return [_Res('aws_lb_listener', f'port_{p.get("Port")}', body)]


def _tf_asg(p, tf):
    body = [('name', tf.value(p.get('AutoScalingGroupName')))]
    body += _pick(p, tf, {'MinSize': 'min_size', 'MaxSize': 'max_size', 'DesiredCapacity': 'desired_capacity',
                          'HealthCheckType': 'health_check_type', 'HealthCheckGracePeriod': 'health_check_grace_period',
                          'TargetGroupARNs': 'target_group_arns', 'AvailabilityZones': 'availability_zones',
                          'DefaultCooldown': 'default_cooldown'})
    if p.get('VPCZoneIdentifier'):
        body.append(('vpc_zone_identifier', [tf.value(s.strip()) for s in str(p['VPCZoneIdentifier']).split(',')]))
    lt = p.get('LaunchTemplate')
    if isinstance(lt, dict):
        body.append(('launch_template', _Body(_pick(lt, tf, {
            'LaunchTemplateName': 'name', 'LaunchTemplateId': 'id', 'Version': 'version'}))))
    return [_Res('aws_autoscaling_group', p.get('AutoScalingGroupName'), body)]


def _tf_db_instance(p, tf):
    body = [('identifier', tf.value(p.get('DBInstanceIdentifier')))]
    body += _pick(p, tf, {'Engine': 'engine', 'EngineVersion': 'engine_version', 'DBInstanceClass': 'instance_class',
                          'AllocatedStorage': 'allocated_storage', 'StorageType': 'storage_type',
                          'MasterUsername': 'username', 'MasterUserPassword': 'password', 'DBName': 'db_name',
                          'MultiAZ': 'multi_az', 'DBSubnetGroupName': 'db_subnet_group_name',
                          'VpcSecurityGroupIds': 'vpc_security_group_ids', 'StorageEncrypted': 'storage_encrypted',
                          'KmsKeyId': 'kms_key_id', 'BackupRetentionPeriod': 'backup_retention_period',
                          'PubliclyAccessible': 'publicly_accessible', 'DBClusterIdentifier': 'cluster_identifier',
                          'DBParameterGroupName': 'parameter_group_name', 'DeletionProtection': 'deletion_protection',
                          'ManageMasterUserPassword': 'manage_master_user_password'})
    if p.get('DBClusterIdentifier'):
        # An instance inside an Aurora cluster is its own resource type in Terraform.
        body = [('identifier', tf.value(p.get('DBInstanceIdentifier')))]
        body += _pick(p, tf, {'DBClusterIdentifier': 'cluster_identifier', 'DBInstanceClass': 'instance_class',
                              'Engine': 'engine', 'AvailabilityZone': 'availability_zone',
                              'PromotionTier': 'promotion_tier', 'PubliclyAccessible': 'publicly_accessible'})
        return [_Res('aws_rds_cluster_instance', p.get('DBInstanceIdentifier'), _with_tags(body, p, tf))]
    body.append(('skip_final_snapshot', True))
    return [_Res('aws_db_instance', p.get('DBInstanceIdentifier'), _with_tags(body, p, tf))]


def _tf_db_subnet_group(p, tf):
    body = _pick(p, tf, {'DBSubnetGroupName': 'name', 'DBSubnetGroupDescription': 'description', 'SubnetIds': 'subnet_ids'})
    return [_Res('aws_db_subnet_group', p.get('DBSubnetGroupName'), body)]


def _tf_rds_cluster(p, tf):
    body = [('cluster_identifier', tf.value(p.get('DBClusterIdentifier')))]
    body += _pick(p, tf, {'Engine': 'engine', 'EngineVersion': 'engine_version', 'MasterUsername': 'master_username',
                          'MasterUserPassword': 'master_password', 'DatabaseName': 'database_name',
                          'DBSubnetGroupName': 'db_subnet_group_name', 'VpcSecurityGroupIds': 'vpc_security_group_ids',
                          'StorageEncrypted': 'storage_encrypted', 'BackupRetentionPeriod': 'backup_retention_period',
                          'ManageMasterUserPassword': 'manage_master_user_password'})
    body.append(('skip_final_snapshot', True))
    return [_Res('aws_rds_cluster', p.get('DBClusterIdentifier'), _with_tags(body, p, tf))]


def _tf_cfn_stack(p, tf):
    body = [('name', tf.value(p.get('StackName')))]
    if p.get('TemplateBody') is not None:
        path = tf.path(p['TemplateBody'])
        body.append(('template_body', _Hcl(f'file({json.dumps(path)})') if path else tf.value(p['TemplateBody'])))
    body += _pick(p, tf, {'TemplateURL': 'template_url', 'Capabilities': 'capabilities', 'OnFailure': 'on_failure',
                          'RoleARN': 'iam_role_arn', 'NotificationARNs': 'notification_arns',
                          'TimeoutInMinutes': 'timeout_in_minutes'})
    params = p.get('Parameters')
    if isinstance(params, list) and params:
        body.append(('parameters', {x.get('ParameterKey'): tf.value(x.get('ParameterValue')) for x in params}))
    return [_Res('aws_cloudformation_stack', p.get('StackName'), _with_tags(body, p, tf))]


def _tf_ecs_cluster(p, tf):
    return [_Res('aws_ecs_cluster', p.get('clusterName'), [('name', tf.value(p.get('clusterName')))])]


def _tf_task_definition(p, tf):
    containers = p.get('containerDefinitions')
    body = [('family', tf.value(p.get('family')))]
    body += _pick(p, tf, {'requiresCompatibilities': 'requires_compatibilities', 'networkMode': 'network_mode',
                          'cpu': 'cpu', 'memory': 'memory', 'executionRoleArn': 'execution_role_arn',
                          'taskRoleArn': 'task_role_arn'})
    body.append(('container_definitions', tf.doc(containers if containers is not None else [])))
    return [_Res('aws_ecs_task_definition', p.get('family'), body)]


def _tf_ecs_service(p, tf):
    body = [('name', tf.value(p.get('serviceName')))]
    body += _pick(p, tf, {'cluster': 'cluster', 'taskDefinition': 'task_definition', 'desiredCount': 'desired_count',
                          'launchType': 'launch_type'})
    net = (p.get('networkConfiguration') or {}).get('awsvpcConfiguration')
    if isinstance(net, dict):
        entries = _pick(net, tf, {'subnets': 'subnets', 'securityGroups': 'security_groups'})
        if 'assignPublicIp' in net:
            entries.append(('assign_public_ip', net['assignPublicIp'] == 'ENABLED'))
        body.append(('network_configuration', _Body(entries)))
    for lb in p.get('loadBalancers') or []:
        body.append(('load_balancer', _Body(_pick(lb, tf, {
            'targetGroupArn': 'target_group_arn', 'containerName': 'container_name', 'containerPort': 'container_port'}))))
    controller = (p.get('deploymentController') or {}).get('type')
    if controller:
        body.append(('deployment_controller', _Body([('type', controller)])))
    return [_Res('aws_ecs_service', p.get('serviceName'), body)]


def _tf_hosted_zone(p, tf):
    body = [('name', tf.value(p.get('Name')))]
    vpc = p.get('VPC')
    if isinstance(vpc, dict):
        body.append(('vpc', _Body(_pick(vpc, tf, {'VPCId': 'vpc_id', 'VPCRegion': 'vpc_region'}))))
    comment = (p.get('HostedZoneConfig') or {}).get('Comment')
    if comment:
        body.insert(1, ('comment', tf.value(comment)))
    return [_Res('aws_route53_zone', p.get('Name'), body)]


def _tf_user_pool(p, tf):
    return [_Res('aws_cognito_user_pool', p.get('PoolName'), [('name', tf.value(p.get('PoolName')))])]


def _tf_user_pool_client(p, tf):
    body = [('name', tf.value(p.get('ClientName'))), ('user_pool_id', tf.value(p.get('UserPoolId')))]
    body += _pick(p, tf, {'GenerateSecret': 'generate_secret', 'ExplicitAuthFlows': 'explicit_auth_flows'})
    return [_Res('aws_cognito_user_pool_client', p.get('ClientName'), body)]


def _tf_user_group(p, tf):
    body = [('name', tf.value(p.get('GroupName'))), ('user_pool_id', tf.value(p.get('UserPoolId')))]
    body += _pick(p, tf, {'Description': 'description', 'Precedence': 'precedence', 'RoleArn': 'role_arn'})
    return [_Res('aws_cognito_user_group', p.get('GroupName'), body)]


def _tf_state_machine(p, tf):
    body = [('name', tf.value(p.get('name'))), ('role_arn', tf.value(p.get('roleArn')))]
    if p.get('type'):
        body.append(('type', p['type']))
    body.append(('definition', tf.doc(p.get('definition'))))
    return [_Res('aws_sfn_state_machine', p.get('name'), body)]


def _tf_kinesis_stream(p, tf):
    body = [('name', tf.value(p.get('StreamName')))] + _pick(p, tf, {'ShardCount': 'shard_count'})
    mode = (p.get('StreamModeDetails') or {}).get('StreamMode')
    if mode:
        body.append(('stream_mode_details', _Body([('stream_mode', mode)])))
    return [_Res('aws_kinesis_stream', p.get('StreamName'), body)]


def _tf_ecr_repository(p, tf):
    body = [('name', tf.value(p.get('repositoryName')))] + _pick(p, tf, {'imageTagMutability': 'image_tag_mutability'})
    scan = (p.get('imageScanningConfiguration') or {}).get('scanOnPush')
    if scan is not None:
        body.append(('image_scanning_configuration', _Body([('scan_on_push', scan)])))
    return [_Res('aws_ecr_repository', p.get('repositoryName'), body)]


def _tf_rest_api(p, tf):
    body = [('name', tf.value(p.get('name')))] + _pick(p, tf, {'description': 'description'})
    return [_Res('aws_api_gateway_rest_api', p.get('name'), body)]


def _tf_http_api(p, tf):
    body = [('name', tf.value(p.get('Name'))), ('protocol_type', tf.value(p.get('ProtocolType', 'HTTP')))]
    body += _pick(p, tf, {'Target': 'target', 'RouteKey': 'route_key'})
    return [_Res('aws_apigatewayv2_api', p.get('Name'), body)]


def _tf_cloudtrail(p, tf):
    body = [('name', tf.value(p.get('Name'))), ('s3_bucket_name', tf.value(p.get('S3BucketName')))]
    body += _pick(p, tf, {'IsMultiRegionTrail': 'is_multi_region_trail', 'EnableLogFileValidation': 'enable_log_file_validation',
                          'IncludeGlobalServiceEvents': 'include_global_service_events', 'KmsKeyId': 'kms_key_id',
                          'S3KeyPrefix': 's3_key_prefix', 'IsOrganizationTrail': 'is_organization_trail'})
    return [_Res('aws_cloudtrail', p.get('Name'), body)]


def _tf_backup_vault(p, tf):
    body = [('name', tf.value(p.get('BackupVaultName')))] + _pick(p, tf, {'EncryptionKeyArn': 'kms_key_arn'})
    return [_Res('aws_backup_vault', p.get('BackupVaultName'), body)]


def _tf_efs(p, tf):
    body = _pick(p, tf, {'CreationToken': 'creation_token', 'PerformanceMode': 'performance_mode',
                         'ThroughputMode': 'throughput_mode', 'Encrypted': 'encrypted', 'KmsKeyId': 'kms_key_id'})
    return [_Res('aws_efs_file_system', p.get('CreationToken') or 'lab', _with_tags(body, p, tf))]


def _tf_efs_mount_target(p, tf):
    body = _pick(p, tf, {'FileSystemId': 'file_system_id', 'SubnetId': 'subnet_id', 'SecurityGroups': 'security_groups'})
    return [_Res('aws_efs_mount_target', p.get('SubnetId') or 'lab', body)]


def _tf_flow_log(p, tf):
    ids = p.get('ResourceIds') or []
    kind = {'VPC': 'vpc_id', 'Subnet': 'subnet_id', 'NetworkInterface': 'eni_id'}.get(p.get('ResourceType'), 'vpc_id')
    body = [(kind, tf.value(ids[0] if ids else None)), ('traffic_type', tf.value(p.get('TrafficType', 'ALL')))]
    body += _pick(p, tf, {'LogDestinationType': 'log_destination_type', 'LogDestination': 'log_destination',
                          'LogGroupName': 'log_group_name', 'DeliverLogsPermissionArn': 'iam_role_arn',
                          'MaxAggregationInterval': 'max_aggregation_interval', 'LogFormat': 'log_format'})
    return [_Res('aws_flow_log', ids[0] if ids else 'lab', body)]


def _tf_cache_policy(p, tf):
    return None


def _tf_launch_template(p, tf):
    data = p.get('LaunchTemplateData')
    if not isinstance(data, dict) or tf.path(data):
        return None
    body = [('name', tf.value(p.get('LaunchTemplateName')))]
    body += _pick(data, tf, {'ImageId': 'image_id', 'InstanceType': 'instance_type', 'KeyName': 'key_name',
                             'UserData': 'user_data', 'SecurityGroupIds': 'vpc_security_group_ids'})
    profile = data.get('IamInstanceProfile') or {}
    if profile:
        body.append(('iam_instance_profile', _Body(_pick(profile, tf, {'Name': 'name', 'Arn': 'arn'}))))
    metadata = data.get('MetadataOptions')
    if isinstance(metadata, dict):
        body.append(('metadata_options', _Body(_pick(metadata, tf, {
            'HttpTokens': 'http_tokens', 'HttpEndpoint': 'http_endpoint', 'HttpPutResponseHopLimit': 'http_put_response_hop_limit'}))))
    return [_Res('aws_launch_template', p.get('LaunchTemplateName'), body)]


# Every resource type the builders above can emit (checked against the AWS provider schema).
_TF_RESOURCE_TYPES = frozenset({
    'aws_api_gateway_rest_api',
    'aws_apigatewayv2_api',
    'aws_autoscaling_group',
    'aws_backup_vault',
    'aws_cloudformation_stack',
    'aws_cloudtrail',
    'aws_cloudwatch_composite_alarm',
    'aws_cloudwatch_dashboard',
    'aws_cloudwatch_event_archive',
    'aws_cloudwatch_event_bus',
    'aws_cloudwatch_event_rule',
    'aws_cloudwatch_event_target',
    'aws_cloudwatch_log_group',
    'aws_cloudwatch_log_metric_filter',
    'aws_cloudwatch_log_stream',
    'aws_cloudwatch_log_subscription_filter',
    'aws_cloudwatch_metric_alarm',
    'aws_cognito_user_group',
    'aws_cognito_user_pool',
    'aws_cognito_user_pool_client',
    'aws_db_instance',
    'aws_db_subnet_group',
    'aws_dynamodb_table',
    'aws_ebs_volume',
    'aws_ecr_repository',
    'aws_ecs_cluster',
    'aws_ecs_service',
    'aws_ecs_task_definition',
    'aws_efs_file_system',
    'aws_efs_mount_target',
    'aws_eip',
    'aws_flow_log',
    'aws_iam_access_key',
    'aws_iam_group',
    'aws_iam_group_policy',
    'aws_iam_group_policy_attachment',
    'aws_iam_instance_profile',
    'aws_iam_policy',
    'aws_iam_role',
    'aws_iam_role_policy',
    'aws_iam_role_policy_attachment',
    'aws_iam_user',
    'aws_iam_user_group_membership',
    'aws_iam_user_login_profile',
    'aws_iam_user_policy',
    'aws_iam_user_policy_attachment',
    'aws_instance',
    'aws_internet_gateway',
    'aws_internet_gateway_attachment',
    'aws_kinesis_stream',
    'aws_kms_alias',
    'aws_kms_key',
    'aws_lambda_alias',
    'aws_lambda_event_source_mapping',
    'aws_lambda_function',
    'aws_lambda_permission',
    'aws_launch_template',
    'aws_lb',
    'aws_lb_listener',
    'aws_lb_target_group',
    'aws_nat_gateway',
    'aws_network_acl',
    'aws_network_acl_rule',
    'aws_rds_cluster',
    'aws_rds_cluster_instance',
    'aws_route',
    'aws_route53_zone',
    'aws_route_table',
    'aws_route_table_association',
    'aws_s3_bucket',
    'aws_s3_bucket_policy',
    'aws_s3_bucket_public_access_block',
    'aws_s3_bucket_server_side_encryption_configuration',
    'aws_s3_bucket_versioning',
    'aws_s3_object',
    'aws_secretsmanager_secret',
    'aws_secretsmanager_secret_version',
    'aws_security_group',
    'aws_sfn_state_machine',
    'aws_sns_topic',
    'aws_sns_topic_subscription',
    'aws_sqs_queue',
    'aws_ssm_parameter',
    'aws_subnet',
    'aws_vpc',
    'aws_vpc_endpoint',
    'aws_vpc_security_group_ingress_rule',
})

_TF_BUILDERS = {
    ('s3', 'create_bucket'): _tf_s3_bucket,
    ('s3', 'put_bucket_versioning'): _tf_s3_versioning,
    ('s3', 'put_bucket_policy'): _tf_s3_policy,
    ('s3', 'put_bucket_encryption'): _tf_s3_encryption,
    ('s3', 'put_public_access_block'): _tf_s3_public_access_block,
    ('s3', 'put_object'): _tf_s3_object,
    ('sqs', 'create_queue'): _tf_sqs_queue,
    ('sns', 'create_topic'): _tf_sns_topic,
    ('sns', 'subscribe'): _tf_sns_subscription,
    ('iam', 'create_role'): _tf_iam_role,
    ('iam', 'put_role_policy'): _tf_iam_inline('role'),
    ('iam', 'put_user_policy'): _tf_iam_inline('user'),
    ('iam', 'put_group_policy'): _tf_iam_inline('group'),
    ('iam', 'attach_role_policy'): _tf_iam_attach('role'),
    ('iam', 'attach_user_policy'): _tf_iam_attach('user'),
    ('iam', 'attach_group_policy'): _tf_iam_attach('group'),
    ('iam', 'create_user'): _tf_iam_user,
    ('iam', 'create_policy'): _tf_iam_policy,
    ('iam', 'create_group'): _tf_iam_group,
    ('iam', 'add_user_to_group'): _tf_iam_group_membership,
    ('iam', 'create_instance_profile'): _tf_iam_instance_profile,
    ('iam', 'create_access_key'): _tf_iam_access_key,
    ('iam', 'create_login_profile'): _tf_iam_login_profile,
    ('dynamodb', 'create_table'): _tf_dynamodb_table,
    ('lambda', 'create_function'): _tf_lambda_function,
    ('lambda', 'add_permission'): _tf_lambda_permission,
    ('lambda', 'create_alias'): _tf_lambda_alias,
    ('lambda', 'create_event_source_mapping'): _tf_lambda_esm,
    ('kms', 'create_key'): _tf_kms_key,
    ('kms', 'create_alias'): _tf_kms_alias,
    ('ssm', 'put_parameter'): _tf_ssm_parameter,
    ('secretsmanager', 'create_secret'): _tf_secret,
    ('logs', 'create_log_group'): _tf_log_group,
    ('logs', 'create_log_stream'): _tf_log_stream,
    ('logs', 'put_metric_filter'): _tf_metric_filter,
    ('logs', 'put_subscription_filter'): _tf_subscription_filter,
    ('cloudwatch', 'put_metric_alarm'): _tf_metric_alarm,
    ('cloudwatch', 'put_composite_alarm'): _tf_composite_alarm,
    ('cloudwatch', 'put_dashboard'): _tf_dashboard,
    ('events', 'put_rule'): _tf_event_rule,
    ('events', 'put_targets'): _tf_event_targets,
    ('events', 'create_event_bus'): _tf_event_bus,
    ('events', 'create_archive'): _tf_event_archive,
    ('ec2', 'create_vpc'): _tf_vpc,
    ('ec2', 'create_subnet'): _tf_subnet,
    ('ec2', 'create_internet_gateway'): _tf_igw,
    ('ec2', 'attach_internet_gateway'): _tf_igw_attachment,
    ('ec2', 'create_route_table'): _tf_route_table,
    ('ec2', 'create_route'): _tf_route,
    ('ec2', 'associate_route_table'): _tf_route_table_association,
    ('ec2', 'create_security_group'): _tf_security_group,
    ('ec2', 'authorize_security_group_ingress'): _tf_sg_ingress,
    ('ec2', 'run_instances'): _tf_instance,
    ('ec2', 'allocate_address'): _tf_eip,
    ('ec2', 'create_nat_gateway'): _tf_nat_gateway,
    ('ec2', 'create_volume'): _tf_volume,
    ('ec2', 'create_vpc_endpoint'): _tf_vpc_endpoint,
    ('ec2', 'create_network_acl'): _tf_network_acl,
    ('ec2', 'create_network_acl_entry'): _tf_network_acl_rule,
    ('ec2', 'create_launch_template'): _tf_launch_template,
    ('ec2', 'create_flow_logs'): _tf_flow_log,
    ('elbv2', 'create_load_balancer'): _tf_lb,
    ('elbv2', 'create_target_group'): _tf_target_group,
    ('elbv2', 'create_listener'): _tf_listener,
    ('autoscaling', 'create_auto_scaling_group'): _tf_asg,
    ('rds', 'create_db_instance'): _tf_db_instance,
    ('rds', 'create_db_subnet_group'): _tf_db_subnet_group,
    ('rds', 'create_db_cluster'): _tf_rds_cluster,
    ('cloudformation', 'create_stack'): _tf_cfn_stack,
    ('ecs', 'create_cluster'): _tf_ecs_cluster,
    ('ecs', 'register_task_definition'): _tf_task_definition,
    ('ecs', 'create_service'): _tf_ecs_service,
    ('route53', 'create_hosted_zone'): _tf_hosted_zone,
    ('cognito-idp', 'create_user_pool'): _tf_user_pool,
    ('cognito-idp', 'create_user_pool_client'): _tf_user_pool_client,
    ('cognito-idp', 'create_group'): _tf_user_group,
    ('stepfunctions', 'create_state_machine'): _tf_state_machine,
    ('kinesis', 'create_stream'): _tf_kinesis_stream,
    ('ecr', 'create_repository'): _tf_ecr_repository,
    ('apigateway', 'create_rest_api'): _tf_rest_api,
    ('apigatewayv2', 'create_api'): _tf_http_api,
    ('cloudtrail', 'create_trail'): _tf_cloudtrail,
    ('backup', 'create_backup_vault'): _tf_backup_vault,
    ('efs', 'create_file_system'): _tf_efs,
    ('efs', 'create_mount_target'): _tf_efs_mount_target,
}

# Operations that Terraform manages, but through a resource whose shape differs a lot from the
# API call. The snippet names the resource (or attribute) instead of guessing its arguments.
_TF_POINTERS = {
    ('accessanalyzer', 'create_analyzer'): 'aws_accessanalyzer_analyzer',
    ('acm', 'request_certificate'): 'aws_acm_certificate',
    ('apigateway', 'create_api_key'): 'aws_api_gateway_api_key',
    ('apigateway', 'create_authorizer'): 'aws_api_gateway_authorizer',
    ('apigateway', 'create_deployment'): 'aws_api_gateway_deployment',
    ('apigateway', 'create_model'): 'aws_api_gateway_model',
    ('apigateway', 'create_request_validator'): 'aws_api_gateway_request_validator',
    ('apigateway', 'create_resource'): 'aws_api_gateway_resource',
    ('apigateway', 'create_stage'): 'aws_api_gateway_stage',
    ('apigateway', 'create_usage_plan'): 'aws_api_gateway_usage_plan',
    ('apigateway', 'create_usage_plan_key'): 'aws_api_gateway_usage_plan_key',
    ('apigateway', 'put_integration'): 'aws_api_gateway_integration',
    ('apigateway', 'put_integration_response'): 'aws_api_gateway_integration_response',
    ('apigateway', 'put_method'): 'aws_api_gateway_method',
    ('apigateway', 'update_method'): 'aws_api_gateway_method',
    ('apigateway', 'update_stage'): 'aws_api_gateway_stage',
    ('apigatewayv2', 'create_integration'): 'aws_apigatewayv2_integration',
    ('apigatewayv2', 'create_route'): 'aws_apigatewayv2_route',
    ('apigatewayv2', 'create_stage'): 'aws_apigatewayv2_stage',
    ('appconfig', 'create_application'): 'aws_appconfig_application',
    ('appconfig', 'create_configuration_profile'): 'aws_appconfig_configuration_profile',
    ('appconfig', 'create_deployment_strategy'): 'aws_appconfig_deployment_strategy',
    ('appconfig', 'create_environment'): 'aws_appconfig_environment',
    ('appconfig', 'create_hosted_configuration_version'): 'aws_appconfig_hosted_configuration_version',
    ('appconfig', 'start_deployment'): 'aws_appconfig_deployment',
    ('application-autoscaling', 'put_scaling_policy'): 'aws_appautoscaling_policy',
    ('application-autoscaling', 'register_scalable_target'): 'aws_appautoscaling_target',
    ('athena', 'create_work_group'): 'aws_athena_workgroup',
    ('autoscaling', 'put_scaling_policy'): 'aws_autoscaling_policy',
    ('autoscaling', 'set_desired_capacity'): 'desired_capacity on aws_autoscaling_group',
    ('backup', 'create_backup_plan'): 'aws_backup_plan',
    ('backup', 'create_backup_selection'): 'aws_backup_selection',
    ('backup', 'put_backup_vault_access_policy'): 'aws_backup_vault_policy',
    ('backup', 'put_backup_vault_lock_configuration'): 'aws_backup_vault_lock_configuration',
    ('bedrock', 'create_guardrail'): 'aws_bedrock_guardrail',
    ('bedrock', 'create_guardrail_version'): 'aws_bedrock_guardrail_version',
    ('bedrock', 'update_guardrail'): 'aws_bedrock_guardrail',
    ('budgets', 'create_budget'): 'aws_budgets_budget',
    ('ce', 'create_anomaly_monitor'): 'aws_ce_anomaly_monitor',
    ('ce', 'create_anomaly_subscription'): 'aws_ce_anomaly_subscription',
    ('ce', 'update_cost_allocation_tags_status'): 'aws_ce_cost_allocation_tag',
    ('cloudformation', 'create_stack_instances'): 'aws_cloudformation_stack_set_instance',
    ('cloudformation', 'create_stack_set'): 'aws_cloudformation_stack_set',
    ('cloudformation', 'update_stack_set'): 'aws_cloudformation_stack_set',
    ('cloudformation', 'update_stack'): 'aws_cloudformation_stack (change it and run terraform apply)',
    ('cloudfront', 'create_cache_policy'): 'aws_cloudfront_cache_policy',
    ('cloudfront', 'create_distribution'): 'aws_cloudfront_distribution',
    ('cloudfront', 'create_origin_access_control'): 'aws_cloudfront_origin_access_control',
    ('cloudfront', 'update_distribution'): 'aws_cloudfront_distribution',
    ('cloudtrail', 'put_event_selectors'): 'event_selector blocks on aws_cloudtrail',
    ('cloudtrail', 'start_logging'): 'enable_logging on aws_cloudtrail',
    ('cloudwatch', 'put_metric_stream'): 'aws_cloudwatch_metric_stream',
    ('codebuild', 'create_project'): 'aws_codebuild_project',
    ('codebuild', 'create_report_group'): 'aws_codebuild_report_group',
    ('codedeploy', 'create_application'): 'aws_codedeploy_app',
    ('codedeploy', 'create_deployment_group'): 'aws_codedeploy_deployment_group',
    ('codepipeline', 'create_pipeline'): 'aws_codepipeline',
    ('config', 'put_config_rule'): 'aws_config_config_rule',
    ('config', 'put_configuration_recorder'): 'aws_config_configuration_recorder',
    ('config', 'put_conformance_pack'): 'aws_config_conformance_pack',
    ('config', 'put_delivery_channel'): 'aws_config_delivery_channel',
    ('config', 'start_configuration_recorder'): 'aws_config_configuration_recorder_status',
    ('datasync', 'create_agent'): 'aws_datasync_agent',
    ('datasync', 'create_location_nfs'): 'aws_datasync_location_nfs',
    ('datasync', 'create_location_s3'): 'aws_datasync_location_s3',
    ('datasync', 'create_task'): 'aws_datasync_task',
    ('dms', 'create_endpoint'): 'aws_dms_endpoint',
    ('dms', 'create_replication_instance'): 'aws_dms_replication_instance',
    ('dms', 'create_replication_subnet_group'): 'aws_dms_replication_subnet_group',
    ('dms', 'create_replication_task'): 'aws_dms_replication_task',
    ('dynamodb', 'update_continuous_backups'): 'point_in_time_recovery on aws_dynamodb_table',
    ('dynamodb', 'update_table'): 'aws_dynamodb_table (change it and run terraform apply)',
    ('dynamodb', 'update_time_to_live'): 'ttl block on aws_dynamodb_table',
    ('ec2', 'allocate_hosts'): 'aws_ec2_host',
    ('ec2', 'associate_transit_gateway_route_table'): 'aws_ec2_transit_gateway_route_table_association',
    ('ec2', 'attach_volume'): 'aws_volume_attachment',
    ('ec2', 'create_egress_only_internet_gateway'): 'aws_egress_only_internet_gateway',
    ('ec2', 'create_image'): 'aws_ami_from_instance',
    ('ec2', 'create_launch_template_version'): 'aws_launch_template (each change publishes a new version)',
    ('ec2', 'create_snapshot'): 'aws_ebs_snapshot',
    ('ec2', 'create_transit_gateway'): 'aws_ec2_transit_gateway',
    ('ec2', 'create_transit_gateway_route'): 'aws_ec2_transit_gateway_route',
    ('ec2', 'create_transit_gateway_route_table'): 'aws_ec2_transit_gateway_route_table',
    ('ec2', 'create_transit_gateway_vpc_attachment'): 'aws_ec2_transit_gateway_vpc_attachment',
    ('ec2', 'create_vpc_peering_connection'): 'aws_vpc_peering_connection',
    ('ec2', 'modify_launch_template'): 'default_version on aws_launch_template',
    ('ec2', 'modify_subnet_attribute'): 'map_public_ip_on_launch on aws_subnet',
    ('ec2', 'modify_volume'): 'aws_ebs_volume (change size or type and run terraform apply)',
    ('ec2', 'modify_vpc_attribute'): 'enable_dns_support / enable_dns_hostnames on aws_vpc',
    ('ec2', 'modify_vpc_peering_connection_options'): 'aws_vpc_peering_connection_options',
    ('ecr', 'put_lifecycle_policy'): 'aws_ecr_lifecycle_policy',
    ('ecs', 'update_service'): 'aws_ecs_service (change it and run terraform apply)',
    ('efs', 'create_access_point'): 'aws_efs_access_point',
    ('efs', 'put_backup_policy'): 'aws_efs_backup_policy',
    ('efs', 'put_file_system_policy'): 'aws_efs_file_system_policy',
    ('efs', 'put_lifecycle_configuration'): 'lifecycle_policy blocks on aws_efs_file_system',
    ('eks', 'create_cluster'): 'aws_eks_cluster',
    ('eks', 'create_fargate_profile'): 'aws_eks_fargate_profile',
    ('eks', 'create_nodegroup'): 'aws_eks_node_group',
    ('elasticache', 'create_cache_parameter_group'): 'aws_elasticache_parameter_group',
    ('elasticache', 'create_cache_subnet_group'): 'aws_elasticache_subnet_group',
    ('elasticache', 'create_replication_group'): 'aws_elasticache_replication_group',
    ('elasticache', 'modify_cache_parameter_group'): 'parameter blocks on aws_elasticache_parameter_group',
    ('elasticbeanstalk', 'create_application'): 'aws_elastic_beanstalk_application',
    ('elasticbeanstalk', 'create_environment'): 'aws_elastic_beanstalk_environment',
    ('elbv2', 'modify_load_balancer_attributes'): 'attributes such as access_logs on aws_lb',
    ('events', 'enable_rule'): 'state = "ENABLED" on aws_cloudwatch_event_rule',
    ('firehose', 'create_delivery_stream'): 'aws_kinesis_firehose_delivery_stream',
    ('globalaccelerator', 'create_accelerator'): 'aws_globalaccelerator_accelerator',
    ('globalaccelerator', 'create_endpoint_group'): 'aws_globalaccelerator_endpoint_group',
    ('globalaccelerator', 'create_listener'): 'aws_globalaccelerator_listener',
    ('globalaccelerator', 'update_endpoint_group'): 'aws_globalaccelerator_endpoint_group',
    ('glue', 'create_database'): 'aws_glue_catalog_database',
    ('guardduty', 'create_detector'): 'aws_guardduty_detector',
    ('iam', 'add_role_to_instance_profile'): 'role on aws_iam_instance_profile',
    ('iam', 'create_policy_version'): 'aws_iam_policy (change policy and run terraform apply)',
    ('iam', 'create_virtual_mfa_device'): 'aws_iam_virtual_mfa_device',
    ('iam', 'update_account_password_policy'): 'aws_iam_account_password_policy',
    ('kms', 'create_grant'): 'aws_kms_grant',
    ('kms', 'enable_key'): 'is_enabled on aws_kms_key',
    ('kms', 'enable_key_rotation'): 'enable_key_rotation on aws_kms_key',
    ('lambda', 'create_function_url_config'): 'aws_lambda_function_url',
    ('lambda', 'publish_version'): 'publish = true on aws_lambda_function',
    ('lambda', 'put_function_concurrency'): 'reserved_concurrent_executions on aws_lambda_function',
    ('lambda', 'put_function_event_invoke_config'): 'aws_lambda_function_event_invoke_config',
    ('lambda', 'update_alias'): 'aws_lambda_alias (change it and run terraform apply)',
    ('lambda', 'update_function_code'): 'aws_lambda_function (new filename / source_code_hash)',
    ('lambda', 'update_function_configuration'): 'aws_lambda_function (change it and run terraform apply)',
    ('lambda', 'update_function_event_invoke_config'): 'aws_lambda_function_event_invoke_config',
    ('logs', 'put_destination'): 'aws_cloudwatch_log_destination',
    ('logs', 'put_destination_policy'): 'aws_cloudwatch_log_destination_policy',
    ('logs', 'put_retention_policy'): 'retention_in_days on aws_cloudwatch_log_group',
    ('organizations', 'attach_policy'): 'aws_organizations_policy_attachment',
    ('organizations', 'create_account'): 'aws_organizations_account',
    ('organizations', 'create_organization'): 'aws_organizations_organization',
    ('organizations', 'create_organizational_unit'): 'aws_organizations_organizational_unit',
    ('organizations', 'create_policy'): 'aws_organizations_policy',
    ('organizations', 'enable_aws_service_access'): 'aws_service_access_principals on aws_organizations_organization',
    ('organizations', 'enable_policy_type'): 'enabled_policy_types on aws_organizations_organization',
    ('organizations', 'register_delegated_administrator'): 'aws_organizations_delegated_administrator',
    ('ram', 'create_resource_share'): 'aws_ram_resource_share',
    ('rds', 'create_db_cluster_snapshot'): 'aws_db_cluster_snapshot',
    ('rds', 'create_db_instance_read_replica'): 'aws_db_instance with replicate_source_db',
    ('rds', 'create_db_parameter_group'): 'aws_db_parameter_group',
    ('rds', 'create_db_proxy'): 'aws_db_proxy',
    ('rds', 'create_db_snapshot'): 'aws_db_snapshot',
    ('rds', 'modify_db_instance'): 'aws_db_instance (change it and run terraform apply)',
    ('rds', 'register_db_proxy_targets'): 'aws_db_proxy_target',
    ('route53', 'create_health_check'): 'aws_route53_health_check',
    ('route53', 'change_resource_record_sets'): 'aws_route53_record',
    ('route53resolver', 'associate_resolver_rule'): 'aws_route53_resolver_rule_association',
    ('route53resolver', 'create_firewall_domain_list'): 'aws_route53_resolver_firewall_domain_list',
    ('route53resolver', 'create_resolver_endpoint'): 'aws_route53_resolver_endpoint',
    ('route53resolver', 'create_resolver_rule'): 'aws_route53_resolver_rule',
    ('s3', 'put_bucket_accelerate_configuration'): 'aws_s3_bucket_accelerate_configuration',
    ('s3', 'put_bucket_cors'): 'aws_s3_bucket_cors_configuration',
    ('s3', 'put_bucket_intelligent_tiering_configuration'): 'aws_s3_bucket_intelligent_tiering_configuration',
    ('s3', 'put_bucket_lifecycle_configuration'): 'aws_s3_bucket_lifecycle_configuration',
    ('s3', 'put_bucket_notification_configuration'): 'aws_s3_bucket_notification',
    ('s3', 'put_bucket_ownership_controls'): 'aws_s3_bucket_ownership_controls',
    ('s3', 'put_bucket_replication'): 'aws_s3_bucket_replication_configuration',
    ('s3', 'put_bucket_request_payment'): 'aws_s3_bucket_request_payment_configuration',
    ('s3', 'put_bucket_tagging'): 'tags on aws_s3_bucket',
    ('s3', 'put_object_lock_configuration'): 'aws_s3_bucket_object_lock_configuration',
    ('s3', 'put_object_tagging'): 'tags on aws_s3_object',
    ('s3vectors', 'create_index'): 'aws_s3vectors_index',
    ('s3vectors', 'create_vector_bucket'): 'aws_s3vectors_vector_bucket',
    ('sagemaker', 'create_endpoint'): 'aws_sagemaker_endpoint',
    ('sagemaker', 'create_endpoint_config'): 'aws_sagemaker_endpoint_configuration',
    ('sagemaker', 'create_model'): 'aws_sagemaker_model',
    ('sagemaker', 'create_monitoring_schedule'): 'aws_sagemaker_monitoring_schedule',
    ('sagemaker', 'update_endpoint'): 'aws_sagemaker_endpoint (point it at a new endpoint configuration)',
    ('scheduler', 'create_schedule'): 'aws_scheduler_schedule',
    ('scheduler', 'create_schedule_group'): 'aws_scheduler_schedule_group',
    ('secretsmanager', 'put_secret_value'): 'aws_secretsmanager_secret_version',
    ('secretsmanager', 'rotate_secret'): 'aws_secretsmanager_secret_rotation',
    ('securityhub', 'enable_security_hub'): 'aws_securityhub_account',
    ('servicecatalog', 'associate_principal_with_portfolio'): 'aws_servicecatalog_principal_portfolio_association',
    ('servicecatalog', 'associate_product_with_portfolio'): 'aws_servicecatalog_product_portfolio_association',
    ('servicecatalog', 'create_constraint'): 'aws_servicecatalog_constraint',
    ('servicecatalog', 'create_portfolio'): 'aws_servicecatalog_portfolio',
    ('servicecatalog', 'create_product'): 'aws_servicecatalog_product',
    ('sqs', 'set_queue_attributes'): 'attributes on aws_sqs_queue (such as redrive_policy)',
    ('sqs', 'tag_queue'): 'tags on aws_sqs_queue',
    ('ssm', 'create_document'): 'aws_ssm_document',
    ('stepfunctions', 'create_state_machine_alias'): 'aws_sfn_alias',
    ('wafv2', 'associate_web_acl'): 'aws_wafv2_web_acl_association',
    ('wafv2', 'create_ip_set'): 'aws_wafv2_ip_set',
    ('wafv2', 'create_web_acl'): 'aws_wafv2_web_acl',
    ('wafv2', 'put_logging_configuration'): 'aws_wafv2_web_acl_logging_configuration',
}

_READ_PREFIXES = ('describe_', 'get_', 'list_', 'search_', 'lookup_', 'query', 'scan', 'batch_get_', 'filter_',
                  'test_', 'simulate_', 'validate_', 'detect_', 'estimate_', 'select_', 'preview_', 'download_',
                  'head_', 'generate_', 'decrypt', 'verify', 'translate_', 'converse', 'invoke_model', 'retrieve',
                  'query_vectors', 'evaluate_')


def _no_tf_reason(call: _Call) -> str:
    name = call.method or call.waiter or ''
    if call.waiter:
        return 'waits for a resource state'
    if not name or name.startswith(_READ_PREFIXES) or call.cli in ('s3 ls', 's3 presign', 'logs tail'):
        return 'reads data'
    return 'is a one-off action'


def _terraform_calls(command: str) -> list[tuple[_Call | None, list, str]]:
    """Every AWS CLI call in a step, with its file list (or a reason it could not be parsed)."""
    calls = []
    for line in _logical_lines(command):
        code, _comment = _split_comment(line.strip())
        loop = _FOR_RE.match(code)
        if loop:
            code = re.sub(r';?\s*done$', '', loop.group(3)).strip()
        if not code or re.match(r'^done\b', code):
            continue
        tokens = _tokenize(code) or []
        for seg in _segments(tokens):
            if _aws_index(seg) < 0:
                continue
            files: list[tuple[str, bool]] = []
            call, reason = _parse_aws(list(seg), files)
            calls.append((call, files, reason or shlex.join(seg[_aws_index(seg):])[:60]))
    return calls


def generate_terraform_snippet(command: str, service_hint: str = '') -> str:
    """Terraform for the resources a lab step creates, or a plain note when there are none."""
    return _generate_terraform(command or '')


@lru_cache(maxsize=2048)
def _generate_terraform(command: str) -> str:
    blocks: list[list[str]] = []
    notes: list[str] = []
    used: set[str] = set()
    variables: dict[str, str] = {}
    previous: dict[str, str] = {}
    for call, files, reason in _terraform_calls(command):
        if call is None:
            notes.append(f'# aws {reason}: not managed by Terraform')
            continue
        cli = f'aws {call.cli}' if call.cli else f'{call.service} {call.method}'
        key = (call.service, call.method)
        builder = _TF_BUILDERS.get(key)
        tf = _Tf(files)
        resources = None
        params = _Tracked(call.params)
        if builder and call.note.startswith('The lab abbreviates'):
            try:
                kind = builder(_Tracked(call.params), _Tf(files))[0].type
            except Exception:
                kind = ''
            if kind:
                notes.append(f'# {cli}: abbreviated in the lab; in Terraform, use {kind}')
                continue
        if builder and not call.raw:
            try:
                resources = builder(params, tf)
            except (AttributeError, KeyError, TypeError, IndexError):
                resources = None
        if resources:
            variables.update({k: v for k, v in tf.variables.items()})
            for res in resources:
                label = _label(res.label, res.type[4:], used)
                body = [(k, _Hcl(v.expr.replace('PREVIOUS', previous.get('aws_secretsmanager_secret', '')))
                         if isinstance(v, _Hcl) and 'PREVIOUS' in v.expr else v) for k, v in res.body]
                previous[res.type] = f'{res.type}.{label}'
                lines = [f'resource "{res.type}" "{label}" {{', *_render_body(body, 1, tf), '}']
                blocks.append(lines)
            skipped = sorted(set(params) - params.seen - _TF_IMPLICIT)
            if skipped:
                blocks[-1][-1:-1] = ['', f'  # The lab also sets {", ".join(skipped)}; add the matching arguments if you need them.']
            variables.update(tf.variables)
            continue
        pointer = _TF_POINTERS.get(key)
        if pointer:
            notes.append(f'# {cli}: in Terraform, use {pointer}')
        else:
            notes.append(f'# {cli}: no Terraform equivalent (it {_no_tf_reason(call)})')

    if not blocks:
        if not notes:
            return ('# This step makes no AWS API call, so there is no Terraform version.\n')
        header = ['# No Terraform resource to declare for this step.',
                  '# Terraform manages long-lived infrastructure; run these calls with the CLI or boto3.']
        return '\n'.join(header + [''] + _dedupe(notes)) + '\n'

    out: list[str] = []
    for name, placeholder in variables.items():
        out += [f'variable "{name}" {{', '  type        = string', f'  description = {_hcl_str("Value for <" + placeholder + ">")}', '}', '']
    for lines in blocks:
        out += lines + ['']
    if notes:
        out += _dedupe(notes) + ['']
    return '\n'.join(_align(out)).rstrip() + '\n'


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    return [x for x in items if not (x in seen or seen.add(x))]


def get_step_snippets(step: dict[str, Any], service_key: str = '') -> dict[str, str]:
    """Return dictionary of code snippets: cli, boto3, terraform."""
    cmd = step.get('command', '') or ''
    overrides = step.get('code_snippets', {})
    return {
        'cli': overrides.get('cli') or cmd,
        'boto3': overrides.get('boto3') or _safe(generate_boto3_snippet, cmd, service_key),
        'terraform': overrides.get('terraform') or _safe(generate_terraform_snippet, cmd, service_key),
    }


def _safe(generate, command: str, service_key: str) -> str:
    try:
        return generate(command, service_key)
    except Exception:  # never break the Labs page over a snippet
        return '# Could not translate this step automatically; use the AWS CLI tab.\n'
