from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import time
from urllib.parse import urlparse

from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .actions import parse_json_body
from .aws import FlociClientFactory


SHELL_TOKENS = (';', '&&', '||', '|', '<', '>', '`', '$(')
BLOCKED_AWS_COMMANDS = {'configure'}
BLOCKED_AWS_OPTIONS = {'--profile'}
BLOCKED_TOKEN_PREFIXES = ('file://', 'fileb://')
GLOBAL_FLAG_OPTIONS = {'--debug', '--no-verify-ssl', '--no-paginate', '--cli-auto-prompt', '--no-cli-auto-prompt', '--no-cli-pager', '--version'}
DESTRUCTIVE_PREFIXES = (
    'delete-',
    'remove-',
    'purge-',
    'terminate-',
    'cancel-',
)
DESTRUCTIVE_COMMANDS = {'delete', 'remove', 'purge', 'terminate', 'rm', 'rb'}
OUTPUT_LIMIT = 100_000

def _is_local_endpoint(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or '').rstrip('.').lower()
    allowed_hosts = {'localhost', '127.0.0.1', '::1', 'floci', 'host.docker.internal', 'localhost.floci.io', 'localhost.localstack.cloud'}
    allowed_suffixes = ('.localhost.floci.io', '.localhost.localstack.cloud')
    return (
        parsed.scheme in {'http', 'https'}
        and (
            hostname in allowed_hosts
            or any(hostname.endswith(suffix) for suffix in allowed_suffixes)
        )
    )


def _positional_tokens(args: list[str]) -> list[str]:
    positionals = []
    skip_next = False
    for token in args[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in GLOBAL_FLAG_OPTIONS:
            continue
        if token.startswith('--') and '=' not in token:
            skip_next = True
            continue
        if token.startswith('-'):
            continue
        positionals.append(token)
    return positionals


def _parse_command(command: str) -> list[str]:
    value = str(command or '').strip()
    if not value:
        raise ValueError('Enter an AWS CLI command.')
    if '\n' in value or '\r' in value:
        raise ValueError('Run one AWS CLI command at a time.')
    if any(token in value for token in SHELL_TOKENS):
        raise ValueError('Shell operators are not supported in the AWS CLI Console.')

    try:
        args = shlex.split(value)
    except ValueError as exc:
        raise ValueError(f'Command could not be parsed: {exc}') from exc

    if not args or args[0] != 'aws':
        raise ValueError('Only commands starting with aws are supported.')
    if len(args) < 2:
        raise ValueError('Enter an AWS service command, for example: aws s3 ls')
    positionals = _positional_tokens(args)
    if not positionals:
        raise ValueError('Enter an AWS service command, for example: aws s3 ls')
    if positionals[0] in BLOCKED_AWS_COMMANDS:
        raise ValueError('aws configure is not supported from the dashboard.')
    if any(arg == option or arg.startswith(f'{option}=') for option in BLOCKED_AWS_OPTIONS for arg in args):
        raise ValueError('The AWS CLI Console uses the dashboard credential context; --profile is not supported.')

    lowered = [arg.lower() for arg in args]
    if any(arg.startswith(BLOCKED_TOKEN_PREFIXES) for arg in lowered):
        raise ValueError('file:// and fileb:// inputs are not supported from the dashboard.')
    return args


def _option_arg(args: list[str], option: str) -> str | None:
    value = None
    for index, arg in enumerate(args):
        if arg == option and index + 1 < len(args):
            value = args[index + 1]
        elif arg.startswith(f'{option}='):
            value = arg.split('=', 1)[1]
    return value


def _endpoint_arg(args: list[str]) -> str | None:
    return _option_arg(args, '--endpoint-url')


def _with_endpoint(args: list[str], endpoint_url: str) -> list[str]:
    submitted = _endpoint_arg(args)
    if submitted:
        if not _is_local_endpoint(submitted):
            raise ValueError('Only local AWS endpoint URLs are allowed.')
        return args
    return ['aws', '--endpoint-url', endpoint_url, *args[1:]]


def _is_destructive(args: list[str]) -> bool:
    positionals = _positional_tokens(args)
    return any(
        token in DESTRUCTIVE_COMMANDS or token.startswith(DESTRUCTIVE_PREFIXES)
        for token in positionals[1:]
    )


def _env_for_factory(factory: FlociClientFactory) -> dict[str, str]:
    env = os.environ.copy()
    env['AWS_DEFAULT_REGION'] = factory.region
    env['AWS_REGION'] = factory.region
    env['AWS_PAGER'] = ''
    env['AWS_CLI_AUTO_PROMPT'] = 'off'
    env['AWS_ENDPOINT_URL'] = factory.endpoint_url

    if factory.profile:
        env['AWS_PROFILE'] = factory.profile
        env.pop('AWS_ACCESS_KEY_ID', None)
        env.pop('AWS_SECRET_ACCESS_KEY', None)
        env.pop('AWS_SESSION_TOKEN', None)
    else:
        env.pop('AWS_PROFILE', None)
        if factory.access_key_id:
            env['AWS_ACCESS_KEY_ID'] = factory.access_key_id
        if factory.secret_access_key:
            env['AWS_SECRET_ACCESS_KEY'] = factory.secret_access_key
        if factory.session_token:
            env['AWS_SESSION_TOKEN'] = factory.session_token
        else:
            env.pop('AWS_SESSION_TOKEN', None)
    return env


def _parsed_stdout(stdout: str):
    try:
        return json.loads(stdout) if stdout.strip() else None
    except json.JSONDecodeError:
        return None


@require_POST
def console_run(request):
    try:
        body = parse_json_body(request)
        command = body.get('command') or ''
        confirmed = body.get('confirmed') is True
        args = _parse_command(command)
        factory = FlociClientFactory()
        args = _with_endpoint(args, factory.endpoint_url)
        destructive = _is_destructive(args)
        if destructive and not confirmed:
            return JsonResponse({
                'requires_confirmation': True,
                'destructive': True,
                'command': command,
                'message': 'This AWS CLI command may remove or stop local resources.',
            }, status=409)

        aws_path = shutil.which('aws')
        if not aws_path:
            raise ValueError('AWS CLI is not installed or is not on PATH.')
        args[0] = aws_path

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='floci-aws-console-') as cwd:
            result = subprocess.run(
                args,
                cwd=cwd,
                env=_env_for_factory(factory),
                capture_output=True,
                text=True,
                timeout=30,
                shell=False,
            )
        duration_ms = round((time.perf_counter() - started) * 1000)
        stdout = result.stdout[-OUTPUT_LIMIT:]
        stderr = result.stderr[-OUTPUT_LIMIT:]
        return JsonResponse({
            'ok': result.returncode == 0,
            'command': command,
            'executed_args': ['aws', *args[1:]],
            'exit_code': result.returncode,
            'stdout': stdout,
            'stderr': stderr,
            'json': _parsed_stdout(stdout),
            'duration_ms': duration_ms,
            'endpoint_url': _endpoint_arg(args) or factory.endpoint_url,
            'region': _option_arg(args, '--region') or factory.region,
            'stdout_truncated': len(result.stdout) > OUTPUT_LIMIT,
            'stderr_truncated': len(result.stderr) > OUTPUT_LIMIT,
            'output_limit': OUTPUT_LIMIT,
            'credential_source': factory.credential_source,
            'identity_expires_at': factory.identity_expires_at,
            'identity_label': factory.identity_label,
            'identity_type': factory.identity_type,
            'profile': factory.profile,
            'destructive': destructive,
        })
    except subprocess.TimeoutExpired:
        return JsonResponse({'error': 'AWS CLI command timed out after 30 seconds.'}, status=408)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
