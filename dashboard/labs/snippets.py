"""Multi-SDK code snippet generators (AWS CLI, Python boto3, Terraform) for lab steps."""

from __future__ import annotations

import re
import shlex
from typing import Any


def _to_snake(name: str) -> str:
    """Convert kebab-case or camelCase to snake_case."""
    s = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    s = re.sub('([a-z0-9])([A-Z])', r'\1_\2', s)
    return s.replace('-', '_').lower()


def _to_pascal(name: str) -> str:
    """Convert kebab-case or snake_case to PascalCase."""
    return ''.join(word.capitalize() for word in name.replace('-', '_').split('_'))


def _clean_arg_val(val: str) -> Any:
    val = val.strip()
    if val.startswith(('"', "'")) and val.endswith(('"', "'")):
        val = val[1:-1]
    if val.lower() == 'true':
        return True
    if val.lower() == 'false':
        return False
    if val.isdigit():
        return int(val)
    return val


def _parse_cli_command(cmd_line: str) -> dict[str, Any] | None:
    """Parse an aws cli command string into structured parts."""
    cmd_line = cmd_line.strip()
    if not cmd_line.startswith('aws '):
        return None

    try:
        tokens = shlex.split(cmd_line)
    except Exception:
        tokens = cmd_line.split()

    if len(tokens) < 3 or tokens[0] != 'aws':
        return None

    service = tokens[1]
    operation = tokens[2]
    args: dict[str, Any] = {}
    positional: list[str] = []

    i = 3
    while i < len(tokens):
        token = tokens[i]
        if token.startswith('--'):
            key = token[2:]
            if i + 1 < len(tokens) and not tokens[i + 1].startswith('--'):
                args[key] = _clean_arg_val(tokens[i + 1])
                i += 2
            else:
                args[key] = True
                i += 1
        else:
            positional.append(token)
            i += 1

    return {
        'service': service,
        'operation': operation,
        'args': args,
        'positional': positional,
    }


def generate_boto3_snippet(command: str, service_hint: str = '') -> str:
    """Generate an idiomatic Python boto3 snippet for an AWS CLI command or pipeline."""
    lines = [line.strip() for line in command.strip().split('\n') if line.strip()]

    # If multi-line script or compound command
    if len(lines) > 1:
        # Check if lines are multiple aws commands
        parsed_cmds = [_parse_cli_command(line) for line in lines if line.startswith('aws ')]
        if parsed_cmds and all(p is not None for p in parsed_cmds):
            services_used = sorted({p['service'].replace('s3api', 's3') for p in parsed_cmds if p})
            out = ['import boto3\n']
            for s in services_used:
                client_var = f"{s.replace('-', '_')}_client" if s in ('lambda', 'import') else f"{s.replace('-', '_')}"
                out.append(f"{client_var} = boto3.client('{s}')")
            out.append('')
            for p in parsed_cmds:
                if not p:
                    continue
                s = p['service'].replace('s3api', 's3')
                client_var = f"{s.replace('-', '_')}_client" if s in ('lambda', 'import') else f"{s.replace('-', '_')}"
                method = _to_snake(p['operation'])
                kwargs = []
                for k, v in p['args'].items():
                    pk = _to_pascal(k)
                    if isinstance(v, str):
                        kwargs.append(f"    {pk}='{v}'")
                    elif isinstance(v, bool):
                        kwargs.append(f"    {pk}={v}")
                    elif isinstance(v, int):
                        kwargs.append(f"    {pk}={v}")
                    else:
                        kwargs.append(f"    {pk}={repr(v)}")
                if kwargs:
                    call_str = f"response = {client_var}.{method}(\n" + ',\n'.join(kwargs) + '\n)'
                else:
                    call_str = f"response = {client_var}.{method}()"
                out.append(call_str)
                out.append("print(response)\n")
            return '\n'.join(out).strip()

    parsed = _parse_cli_command(lines[0]) if lines else None
    if not parsed:
        svc = service_hint or 'aws'
        return (
            f"import boto3\n\n"
            f"# Initialize client for local Floci endpoint\n"
            f"client = boto3.client('{svc}')\n"
            f"# Execute corresponding SDK action\n"
            f"# (CLI command: {command[:60]}...)"
        )

    service = parsed['service'].replace('s3api', 's3')
    client_name = f"{service.replace('-', '_')}_client" if service in ('lambda', 'import') else f"{service.replace('-', '_')}"
    method = _to_snake(parsed['operation'])

    kwargs = []
    for k, v in parsed['args'].items():
        pk = _to_pascal(k)
        if isinstance(v, str):
            if v.startswith('file://'):
                kwargs.append(f"    {pk}=open('{v[7:]}').read()")
            else:
                kwargs.append(f"    {pk}='{v}'")
        elif isinstance(v, bool):
            kwargs.append(f"    {pk}={v}")
        elif isinstance(v, int):
            kwargs.append(f"    {pk}={v}")
        else:
            kwargs.append(f"    {pk}={repr(v)}")

    if kwargs:
        args_formatted = ',\n'.join(kwargs)
        body = f"response = {client_name}.{method}(\n{args_formatted},\n)"
    else:
        body = f"response = {client_name}.{method}()"

    return f"import boto3\n\n{client_name} = boto3.client('{service}')\n{body}\nprint(response)"


def generate_terraform_snippet(command: str, service_hint: str = '') -> str:
    """Generate an idiomatic Terraform (HCL) resource or data source block."""
    parsed = _parse_cli_command(command.split('\n')[0].strip()) if command.strip() else None
    if not parsed:
        return (
            f'# Terraform Configuration\n'
            f'# Provider setup for local Floci\n'
            f'provider "aws" {{\n'
            f'  region                      = "us-east-1"\n'
            f'  skip_credentials_validation = true\n'
            f'  skip_requesting_account_id  = true\n'
            f'  endpoints {{\n'
            f'    {service_hint or "s3"} = "http://localhost:4566"\n'
            f'  }}\n'
            f'}}\n'
        )

    svc = parsed['service'].replace('s3api', 's3')
    op = parsed['operation']
    args = parsed['args']

    # S3
    if svc == 's3' and 'bucket' in op:
        bucket_name = args.get('bucket', 'my-lab-bucket')
        return (
            f'resource "aws_s3_bucket" "lab_bucket" {{\n'
            f'  bucket        = "{bucket_name}"\n'
            f'  force_destroy = true\n'
            f'}}\n'
        )
    if svc == 's3' and 'object' in op:
        bucket_name = args.get('bucket', 'my-lab-bucket')
        key = args.get('key', 'data.txt')
        return (
            f'resource "aws_s3_object" "lab_object" {{\n'
            f'  bucket  = "{bucket_name}"\n'
            f'  key     = "{key}"\n'
            f'  content = "Hello from Floci Terraform!"\n'
            f'}}\n'
        )

    # SQS
    if svc == 'sqs' and 'create-queue' in op:
        qname = args.get('queue-name', 'lab-queue')
        is_fifo = str(qname).endswith('.fifo')
        fifo_attrs = '  fifo_queue                  = true\n  content_based_deduplication = true\n' if is_fifo else ''
        return (
            f'resource "aws_sqs_queue" "lab_queue" {{\n'
            f'  name                        = "{qname}"\n'
            f'{fifo_attrs}'
            f'}}\n'
        )

    # SNS
    if svc == 'sns' and 'create-topic' in op:
        tname = args.get('name', 'lab-topic')
        return (
            f'resource "aws_sns_topic" "lab_topic" {{\n'
            f'  name = "{tname}"\n'
            f'}}\n'
        )
    if svc == 'sns' and 'subscribe' in op:
        return (
            f'resource "aws_sns_topic_subscription" "lab_sub" {{\n'
            f'  topic_arn = aws_sns_topic.lab_topic.arn\n'
            f'  protocol  = "{args.get("protocol", "sqs")}"\n'
            f'  endpoint  = "{args.get("notification-endpoint", "aws_sqs_queue.lab_queue.arn")}"\n'
            f'}}\n'
        )

    # IAM
    if svc == 'iam' and 'create-user' in op:
        uname = args.get('user-name', 'Alice')
        return (
            f'resource "aws_iam_user" "lab_user" {{\n'
            f'  name = "{uname}"\n'
            f'}}\n'
        )
    if svc == 'iam' and 'create-role' in op:
        rname = args.get('role-name', 'LabExecutionRole')
        return (
            f'resource "aws_iam_role" "lab_role" {{\n'
            f'  name = "{rname}"\n'
            f'  assume_role_policy = jsonencode({{\n'
            f'    Version = "2012-10-17"\n'
            f'    Statement = [{{ Action = "sts:AssumeRole", Effect = "Allow", Principal = {{ Service = "lambda.amazonaws.com" }} }}]\n'
            f'  }})\n'
            f'}}\n'
        )

    # DynamoDB
    if svc == 'dynamodb' and 'create-table' in op:
        tname = args.get('table-name', 'Orders')
        return (
            f'resource "aws_dynamodb_table" "lab_table" {{\n'
            f'  name         = "{tname}"\n'
            f'  billing_mode = "PAY_PER_REQUEST"\n'
            f'  hash_key     = "id"\n\n'
            f'  attribute {{\n'
            f'    name = "id"\n'
            f'    type = "S"\n'
            f'  }}\n'
            f'}}\n'
        )

    # Lambda
    if svc == 'lambda' and 'create-function' in op:
        fname = args.get('function-name', 'lab-handler')
        return (
            f'resource "aws_lambda_function" "lab_function" {{\n'
            f'  function_name = "{fname}"\n'
            f'  runtime       = "python3.12"\n'
            f'  handler       = "index.handler"\n'
            f'  role          = aws_iam_role.lab_role.arn\n'
            f'  filename      = "lambda_payload.zip"\n'
            f'}}\n'
        )

    # KMS
    if svc == 'kms' and 'create-key' in op:
        return (
            'resource "aws_kms_key" "lab_key" {\n'
            '  description             = "Floci lab customer master key"\n'
            '  deletion_window_in_days = 7\n'
            '}\n'
        )

    # SSM
    if svc == 'ssm' and 'put-parameter' in op:
        pname = args.get('name', '/app/config/key')
        pval = args.get('value', 'default-value')
        ptype = args.get('type', 'String')
        return (
            f'resource "aws_ssm_parameter" "lab_param" {{\n'
            f'  name  = "{pname}"\n'
            f'  type  = "{ptype}"\n'
            f'  value = "{pval}"\n'
            f'}}\n'
        )

    # Secrets Manager
    if svc == 'secretsmanager' and 'create-secret' in op:
        sname = args.get('name', 'app/database/credentials')
        return (
            f'resource "aws_secretsmanager_secret" "lab_secret" {{\n'
            f'  name = "{sname}"\n'
            f'}}\n\n'
            f'resource "aws_secretsmanager_secret_version" "lab_secret_val" {{\n'
            f'  secret_id     = aws_secretsmanager_secret.lab_secret.id\n'
            f'  secret_string = jsonencode({{ username = "admin", password = "secretpassword" }})\n'
            f'}}\n'
        )

    # Cognito
    if svc == 'cognito-idp' and 'create-user-pool' in op:
        pname = args.get('pool-name', 'LabUserPool')
        return (
            f'resource "aws_cognito_user_pool" "lab_pool" {{\n'
            f'  name = "{pname}"\n'
            f'}}\n'
        )

    # Step Functions
    if svc == 'stepfunctions' and 'create-state-machine' in op:
        sm_name = args.get('name', 'OrderWorkflow')
        return (
            f'resource "aws_sfn_state_machine" "lab_state_machine" {{\n'
            f'  name     = "{sm_name}"\n'
            f'  role_arn = aws_iam_role.lab_role.arn\n'
            f'  definition = jsonencode({{\n'
            f'    StartAt = "ProcessOrder"\n'
            f'    States = {{ ProcessOrder = {{ Type = "Pass", End = true }} }}\n'
            f'  }})\n'
            f'}}\n'
        )

    # CloudWatch
    if svc == 'cloudwatch' and 'put-metric-alarm' in op:
        aname = args.get('alarm-name', 'HighErrorRate')
        operator = args.get('comparison-operator', 'GreaterThanThreshold')
        missing = args.get('treat-missing-data')
        missing_line = f'  treat_missing_data  = "{missing}"\n' if missing else ''
        return (
            f'resource "aws_cloudwatch_metric_alarm" "lab_alarm" {{\n'
            f'  alarm_name          = "{aname}"\n'
            f'  comparison_operator = "{operator}"\n'
            f'  evaluation_periods  = {args.get("evaluation-periods", 1)}\n'
            f'  metric_name         = "{args.get("metric-name", "Errors")}"\n'
            f'  namespace           = "{args.get("namespace", "Floci/App")}"\n'
            f'  period              = {args.get("period", 60)}\n'
            f'  statistic           = "{args.get("statistic", "Sum")}"\n'
            f'  threshold           = {args.get("threshold", 5)}\n'
            f'{missing_line}'
            f'}}\n'
        )
    if svc == 'logs' and 'create-log-group' in op:
        gname = args.get('log-group-name', '/aws/floci/app-logs')
        return (
            f'resource "aws_cloudwatch_log_group" "lab_logs" {{\n'
            f'  name              = "{gname}"\n'
            f'  retention_in_days = 7\n'
            f'}}\n'
        )

    # EC2 / VPC
    if svc == 'ec2' and 'create-vpc' in op:
        cidr = args.get('cidr-block', '10.0.0.0/16')
        return (
            f'resource "aws_vpc" "lab_vpc" {{\n'
            f'  cidr_block           = "{cidr}"\n'
            f'  enable_dns_hostnames = true\n'
            f'  enable_dns_support   = true\n'
            f'}}\n'
        )
    if svc == 'ec2' and 'create-subnet' in op:
        cidr = args.get('cidr-block', '10.0.1.0/24')
        return (
            f'resource "aws_subnet" "lab_subnet" {{\n'
            f'  vpc_id     = aws_vpc.lab_vpc.id\n'
            f'  cidr_block = "{cidr}"\n'
            f'}}\n'
        )

    # Default fallback Terraform representation
    res_type = f"aws_{svc}_{_to_snake(op)}"
    hcl_lines = [f'# Terraform resource/action for {svc} {op}']
    hcl_lines.append(f'resource "{res_type}" "main" {{')
    for k, v in args.items():
        k_snake = _to_snake(k)
        if isinstance(v, str):
            hcl_lines.append(f'  {k_snake:<20} = "{v}"')
        elif isinstance(v, bool):
            hcl_lines.append(f'  {k_snake:<20} = {str(v).lower()}')
        elif isinstance(v, int):
            hcl_lines.append(f'  {k_snake:<20} = {v}')
    hcl_lines.append('}\n')
    return '\n'.join(hcl_lines)


def get_step_snippets(step: dict[str, Any], service_key: str = '') -> dict[str, str]:
    """Return dictionary of code snippets: cli, boto3, terraform."""
    cmd = step.get('command', '')
    cli_snippet = step.get('code_snippets', {}).get('cli') or cmd
    boto3_snippet = step.get('code_snippets', {}).get('boto3') or generate_boto3_snippet(cmd, service_key)
    tf_snippet = step.get('code_snippets', {}).get('terraform') or generate_terraform_snippet(cmd, service_key)

    return {
        'cli': cli_snippet,
        'boto3': boto3_snippet,
        'terraform': tf_snippet,
    }
