"""CodeBuild inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _chunks, _clean_response, _error_code, _safe_value


def _codebuild_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def codebuild_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    codebuild = factory.client('codebuild')
    project_names = _safe_value(lambda: core._paginate(codebuild, 'list_projects', 'projects'), [])
    build_ids = _safe_value(lambda: core._paginate(codebuild, 'list_builds', 'ids'), [])
    report_group_arns = _safe_value(lambda: core._paginate(codebuild, 'list_report_groups', 'reportGroups'), [])
    report_arns = _safe_value(lambda: core._paginate(codebuild, 'list_reports', 'reports'), [])
    source_credentials = _safe_value(lambda: codebuild.list_source_credentials().get('sourceCredentialsInfos', []), [])

    def batch_get_projects(names: list[str]) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for chunk in _chunks(names, 100):
            response = _codebuild_optional(
                lambda chunk=chunk: codebuild.batch_get_projects(names=chunk),
                {'InvalidInputException'},
            )
            if isinstance(response, dict) and not response.get('error'):
                projects.extend(response.get('projects', []))
        return projects

    def batch_get_builds(ids: list[str]) -> list[dict[str, Any]]:
        builds: list[dict[str, Any]] = []
        for chunk in _chunks(ids, 100):
            response = _codebuild_optional(
                lambda chunk=chunk: codebuild.batch_get_builds(ids=chunk),
                {'InvalidInputException'},
            )
            if isinstance(response, dict) and not response.get('error'):
                builds.extend(response.get('builds', []))
        return builds

    def batch_get_report_groups(arns: list[str]) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        for chunk in _chunks(arns, 100):
            response = _codebuild_optional(
                lambda chunk=chunk: codebuild.batch_get_report_groups(reportGroupArns=chunk),
                {'InvalidInputException'},
            )
            if isinstance(response, dict) and not response.get('error'):
                groups.extend(response.get('reportGroups', []))
        return groups

    def batch_get_reports(arns: list[str]) -> list[dict[str, Any]]:
        reports: list[dict[str, Any]] = []
        for chunk in _chunks(arns, 100):
            response = _codebuild_optional(
                lambda chunk=chunk: codebuild.batch_get_reports(reportArns=chunk),
                {'InvalidInputException'},
            )
            if isinstance(response, dict) and not response.get('error'):
                reports.extend(response.get('reports', []))
        return reports

    projects = batch_get_projects(project_names) if project_names else []
    builds = batch_get_builds(build_ids) if build_ids else []
    report_groups = batch_get_report_groups(report_group_arns) if report_group_arns else []
    reports = batch_get_reports(report_arns) if report_arns else []

    project_details = [
        {
            'name': project.get('name'),
            'arn': project.get('arn'),
            'description': project.get('description'),
            'source': project.get('source'),
            'artifacts': project.get('artifacts'),
            'secondary_sources': project.get('secondarySources'),
            'secondary_artifacts': project.get('secondaryArtifacts'),
            'environment': project.get('environment'),
            'service_role': project.get('serviceRole'),
            'timeout_in_minutes': project.get('timeoutInMinutes'),
            'queued_timeout_in_minutes': project.get('queuedTimeoutInMinutes'),
            'encryption_key': project.get('encryptionKey'),
            'tags': project.get('tags'),
            'created': project.get('created'),
            'last_modified': project.get('lastModified'),
            'webhook': project.get('webhook'),
            'vpc_config': project.get('vpcConfig'),
            'badge': project.get('badge'),
            'logs_config': project.get('logsConfig'),
            'file_system_locations': project.get('fileSystemLocations'),
            'build_batch_config': project.get('buildBatchConfig'),
            'concurrent_build_limit': project.get('concurrentBuildLimit'),
            'project_visibility': project.get('projectVisibility'),
            'resource_access_role': project.get('resourceAccessRole'),
        }
        for project in projects
    ]

    build_details = [
        {
            'name': build.get('id'),
            'id': build.get('id'),
            'arn': build.get('arn'),
            'build_number': build.get('buildNumber'),
            'project_name': build.get('projectName'),
            'status': build.get('buildStatus'),
            'source_version': build.get('sourceVersion'),
            'resolved_source_version': build.get('resolvedSourceVersion'),
            'start_time': build.get('startTime'),
            'end_time': build.get('endTime'),
            'current_phase': build.get('currentPhase'),
            'phases': build.get('phases'),
            'source': build.get('source'),
            'secondary_sources': build.get('secondarySources'),
            'artifacts': build.get('artifacts'),
            'secondary_artifacts': build.get('secondaryArtifacts'),
            'environment': build.get('environment'),
            'logs': build.get('logs'),
            'timeout_in_minutes': build.get('timeoutInMinutes'),
            'queued_timeout_in_minutes': build.get('queuedTimeoutInMinutes'),
            'build_complete': build.get('buildComplete'),
            'initiator': build.get('initiator'),
            'vpc_config': build.get('vpcConfig'),
            'debug_session': build.get('debugSession'),
            'encryption_key': build.get('encryptionKey'),
            'exported_environment_variables': build.get('exportedEnvironmentVariables'),
            'report_arns': build.get('reportArns'),
            'file_system_locations': build.get('fileSystemLocations'),
            'build_batch_arn': build.get('buildBatchArn'),
        }
        for build in builds
    ]

    report_group_details = [
        {
            'name': group.get('name') or group.get('arn'),
            'arn': group.get('arn'),
            'type': group.get('type'),
            'export_config': group.get('exportConfig'),
            'created': group.get('created'),
            'last_modified': group.get('lastModified'),
            'tags': group.get('tags'),
            'status': group.get('status'),
        }
        for group in report_groups
    ]

    report_details = [
        {
            'name': report.get('name') or report.get('arn'),
            'arn': report.get('arn'),
            'type': report.get('type'),
            'report_group_arn': report.get('reportGroupArn'),
            'execution_id': report.get('executionId'),
            'status': report.get('status'),
            'created': report.get('created'),
            'expired': report.get('expired'),
            'export_config': report.get('exportConfig'),
            'truncated': report.get('truncated'),
            'test_summary': report.get('testSummary'),
            'code_coverage_summary': report.get('codeCoverageSummary'),
        }
        for report in reports
    ]

    return {
        'summary': {
            'projects': len(project_names),
            'builds': len(build_ids),
            'report_groups': len(report_group_arns),
            'reports': len(report_arns),
            'source_credentials': len(source_credentials),
        },
        'projects': project_details or [{'name': name} for name in project_names],
        'builds': build_details or [{'name': build_id, 'id': build_id} for build_id in build_ids],
        'report_groups': report_group_details or [{'name': arn, 'arn': arn} for arn in report_group_arns],
        'reports': report_details or [{'name': arn, 'arn': arn} for arn in report_arns],
        'source_credentials': source_credentials,
        'supported_from_sdk': [
            'CreateProject',
            'BatchGetProjects',
            'ListProjects',
            'UpdateProject',
            'DeleteProject',
            'StartBuild',
            'BatchGetBuilds',
            'ListBuilds',
            'ListBuildsForProject',
            'StopBuild',
            'RetryBuild',
            'CreateReportGroup',
            'UpdateReportGroup',
            'BatchGetReportGroups',
            'ListReportGroups',
            'DeleteReportGroup',
            'BatchGetReports',
            'ListReports',
            'ListReportsForReportGroup',
            'ImportSourceCredentials',
            'ListSourceCredentials',
            'DeleteSourceCredentials',
            'ListCuratedEnvironmentImages',
        ],
        'notes': [
            'Floci CodeBuild stores project, report-group, and source-credential state and runs builds inside real Docker containers.',
            'Buildspec install, pre_build, build, and post_build phases run sequentially, with output streamed to /aws/codebuild/<project> in CloudWatch Logs.',
            'NO_SOURCE builds skip source injection; S3 artifacts are copied from the build container and uploaded to an existing bucket.',
            'Build execution is asynchronous: StartBuild returns IN_PROGRESS and BatchGetBuilds can be polled until buildComplete is true.',
        ],
    }


def list_codebuild_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    codebuild = factory.client('codebuild')
    resources: list[dict[str, Any]] = []
    project_names = _safe_value(lambda: core._paginate(codebuild, 'list_projects', 'projects'), [])
    build_ids = _safe_value(lambda: core._paginate(codebuild, 'list_builds', 'ids'), [])
    report_group_arns = _safe_value(lambda: core._paginate(codebuild, 'list_report_groups', 'reportGroups'), [])
    report_arns = _safe_value(lambda: core._paginate(codebuild, 'list_reports', 'reports'), [])
    source_credentials = _safe_value(lambda: codebuild.list_source_credentials().get('sourceCredentialsInfos', []), [])

    resources.extend(
        {
            'type': 'project',
            'name': name,
        }
        for name in project_names
    )
    resources.extend(
        {
            'type': 'build',
            'id': build_id,
        }
        for build_id in build_ids
    )
    resources.extend(
        {
            'type': 'report_group',
            'arn': arn,
        }
        for arn in report_group_arns
    )
    resources.extend(
        {
            'type': 'report',
            'arn': arn,
        }
        for arn in report_arns
    )
    resources.extend(
        {
            'type': 'source_credential',
            'server_type': credential.get('serverType'),
            'auth_type': credential.get('authType'),
            'arn': credential.get('arn'),
        }
        for credential in source_credentials
    )
    return resources
