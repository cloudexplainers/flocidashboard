"""Athena inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _athena_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def athena_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    athena = factory.client('athena')
    workgroups = _safe_value(lambda: core._paginate(athena, 'list_work_groups', 'WorkGroups'), [])
    query_execution_ids = _safe_value(lambda: core._paginate(athena, 'list_query_executions', 'QueryExecutionIds'), [])

    def workgroup_detail(workgroup: dict[str, Any]) -> dict[str, Any]:
        name = workgroup.get('Name')
        details = _athena_optional(
            lambda: athena.get_work_group(WorkGroup=name).get('WorkGroup', {}),
            {'InvalidRequestException'},
        )

        return {
            'name': name,
            'state': workgroup.get('State') or (details.get('State') if isinstance(details, dict) else None),
            'description': workgroup.get('Description') or (details.get('Description') if isinstance(details, dict) else None),
            'creation_time': workgroup.get('CreationTime') or (details.get('CreationTime') if isinstance(details, dict) else None),
            'configuration': details.get('Configuration') if isinstance(details, dict) else None,
            'details': details,
        }

    def query_detail(query_execution_id: str) -> dict[str, Any]:
        execution = _athena_optional(
            lambda: athena.get_query_execution(QueryExecutionId=query_execution_id).get('QueryExecution', {}),
            {'InvalidRequestException'},
        )

        if not isinstance(execution, dict) or execution.get('error'):
            return {
                'name': query_execution_id,
                'id': query_execution_id,
                'details': execution,
            }

        status = execution.get('Status', {})
        state = status.get('State')
        preview = None

        if state == 'SUCCEEDED':
            preview = _athena_optional(
                lambda: athena.get_query_results(
                    QueryExecutionId=query_execution_id,
                    MaxResults=5,
                ).get('ResultSet', {}),
                {'InvalidRequestException'},
            )

        return {
            'name': query_execution_id,
            'id': query_execution_id,
            'state': state,
            'state_change_reason': status.get('StateChangeReason'),
            'submission_time': status.get('SubmissionDateTime'),
            'completion_time': status.get('CompletionDateTime'),
            'query': execution.get('Query'),
            'database': execution.get('QueryExecutionContext', {}).get('Database'),
            'catalog': execution.get('QueryExecutionContext', {}).get('Catalog'),
            'workgroup': execution.get('WorkGroup'),
            'engine_version': execution.get('EngineVersion'),
            'statement_type': execution.get('StatementType'),
            'substatement_type': execution.get('SubstatementType'),
            'result_configuration': execution.get('ResultConfiguration'),
            'statistics': execution.get('Statistics'),
            'result_preview': preview,
        }

    detailed_workgroups = [workgroup_detail(workgroup) for workgroup in workgroups]
    detailed_queries = [query_detail(query_id) for query_id in query_execution_ids]

    return {
        'summary': {
            'workgroups': len(detailed_workgroups),
            'query_executions': len(detailed_queries),
            'succeeded': sum(1 for query in detailed_queries if query.get('state') == 'SUCCEEDED'),
            'failed': sum(1 for query in detailed_queries if query.get('state') == 'FAILED'),
            'running': sum(1 for query in detailed_queries if query.get('state') == 'RUNNING'),
            'queued': sum(1 for query in detailed_queries if query.get('state') == 'QUEUED'),
        },
        'workgroups': detailed_workgroups,
        'query_executions': detailed_queries,
        'supported': [
            'StartQueryExecution',
            'GetQueryExecution',
            'GetQueryResults',
            'ListQueryExecutions',
            'StopQueryExecution',
            'CreateWorkGroup',
            'GetWorkGroup',
            'ListWorkGroups',
        ],
        'how_it_works': [
            'Floci runs Athena SQL through a floci-duck sidecar powered by DuckDB.',
            'Glue tables are mapped to DuckDB views over S3 data before execution.',
            'Query results are written as CSV to S3 and read back through GetQueryResults.',
        ],
        'format_inference': {
            'parquet': 'read_parquet',
            'json_or_hive': 'read_json_auto',
            'default': 'read_csv_auto',
        },
        'configuration': {
            'mock': False,
            'default_image': 'floci/floci-duck:latest',
            'duck_url': None,
        },
        'environment_variables': [
            'FLOCI_SERVICES_ATHENA_MOCK',
            'FLOCI_SERVICES_ATHENA_DEFAULT_IMAGE',
            'FLOCI_SERVICES_ATHENA_DUCK_URL',
        ],
        'notes': [
            'Mock mode makes queries transition to SUCCEEDED immediately with empty results.',
            'The DuckDB sidecar is started lazily on the first StartQueryExecution call.',
            'The dashboard only previews a few result rows for completed queries.',
            'Floci 1.5.28 serializes timestamps as epoch seconds in GetWorkGroup and GetTableMetadata.',
            'Floci 1.5.24 aligns CreateWorkGroup, DeleteWorkGroup, and ListWorkGroups behavior with AWS.',
            'Floci 1.5.25 aligns GetWorkGroup behavior with AWS.',
        ],
    }


def list_athena_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    athena = factory.client('athena')
    resources = [
        {
            'type': 'workgroup',
            'name': workgroup.get('Name'),
            'state': workgroup.get('State'),
        }
        for workgroup in _safe_value(lambda: core._paginate(athena, 'list_work_groups', 'WorkGroups'), [])
    ]
    resources.extend(
        {
            'type': 'query_execution',
            'id': query_id,
        }
        for query_id in _safe_value(lambda: core._paginate(athena, 'list_query_executions', 'QueryExecutionIds'), [])
    )
    return resources
