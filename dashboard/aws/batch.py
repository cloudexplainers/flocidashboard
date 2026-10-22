"""AWS Batch inventory and resource-list loaders."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value



def _epoch_ms(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return value
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat().replace('+00:00', 'Z')

def batch_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    batch = factory.client('batch')
    operations = set(batch.meta.service_model.operation_names)

    compute_environments = _safe_value(
        lambda: core._operation_items(batch, 'describe_compute_environments', 'computeEnvironments'),
        [],
    ) if 'DescribeComputeEnvironments' in operations else []
    job_queues = _safe_value(
        lambda: core._operation_items(batch, 'describe_job_queues', 'jobQueues'),
        [],
    ) if 'DescribeJobQueues' in operations else []
    job_definitions = _safe_value(
        lambda: core._operation_items(batch, 'describe_job_definitions', 'jobDefinitions', status='ACTIVE'),
        _safe_value(lambda: core._operation_items(batch, 'describe_job_definitions', 'jobDefinitions'), []),
    ) if 'DescribeJobDefinitions' in operations else []

    jobs: list[dict[str, Any]] = []
    job_statuses = ['SUBMITTED', 'PENDING', 'RUNNABLE', 'STARTING', 'RUNNING', 'SUCCEEDED', 'FAILED']
    if 'ListJobs' in operations:
        for queue in job_queues[:10]:
            queue_name = queue.get('jobQueueName') or queue.get('jobQueueArn')
            for status in job_statuses:
                summaries = _safe_value(
                    lambda queue_name=queue_name, status=status: core._operation_items(
                        batch,
                        'list_jobs',
                        'jobSummaryList',
                        jobQueue=queue_name,
                        jobStatus=status,
                    ),
                    [],
                )
                for summary in summaries[:25]:
                    jobs.append({
                        'name': summary.get('jobName') or summary.get('jobId'),
                        'job_id': summary.get('jobId'),
                        'job_name': summary.get('jobName'),
                        'job_queue': queue_name,
                        'status': summary.get('status') or status,
                        # Batch reports epoch milliseconds.
                        'created_at': _epoch_ms(summary.get('createdAt')),
                        'started_at': _epoch_ms(summary.get('startedAt')),
                        'stopped_at': _epoch_ms(summary.get('stoppedAt')),
                    })

    return {
        'summary': {
            'compute_environments': len(compute_environments),
            'job_queues': len(job_queues),
            'job_definitions': len(job_definitions),
            'sampled_jobs': len(jobs),
            'available_sdk_operations': len(operations),
        },
        'compute_environments': _clean_response(compute_environments),
        'job_queues': _clean_response(job_queues),
        'job_definitions': _clean_response(job_definitions),
        'jobs': _clean_response(jobs),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateComputeEnvironment',
                'DescribeComputeEnvironments',
                'CreateJobQueue',
                'DescribeJobQueues',
                'RegisterJobDefinition',
                'DescribeJobDefinitions',
                'SubmitJob',
                'ListJobs',
                'DescribeJobs',
                'CancelJob',
                'TerminateJob',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.25 adds AWS Batch service support for local batch compute management workflows.',
            'The dashboard samples jobs from the first ten queues across common Batch job statuses to keep refreshes bounded.',
        ],
    }


def list_batch_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    batch = factory.client('batch')
    operations = set(batch.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'DescribeComputeEnvironments' in operations:
        resources.extend(
            {
                'type': 'compute_environment',
                'name': environment.get('computeEnvironmentName'),
                'arn': environment.get('computeEnvironmentArn'),
                'state': environment.get('state'),
                'status': environment.get('status'),
            }
            for environment in _safe_value(
                lambda: core._operation_items(batch, 'describe_compute_environments', 'computeEnvironments'),
                [],
            )
        )

    if 'DescribeJobQueues' in operations:
        resources.extend(
            {
                'type': 'job_queue',
                'name': queue.get('jobQueueName'),
                'arn': queue.get('jobQueueArn'),
                'state': queue.get('state'),
                'status': queue.get('status'),
            }
            for queue in _safe_value(lambda: core._operation_items(batch, 'describe_job_queues', 'jobQueues'), [])
        )

    if 'DescribeJobDefinitions' in operations:
        resources.extend(
            {
                'type': 'job_definition',
                'name': definition.get('jobDefinitionName'),
                'arn': definition.get('jobDefinitionArn'),
                'status': definition.get('status'),
            }
            for definition in _safe_value(
                lambda: core._operation_items(batch, 'describe_job_definitions', 'jobDefinitions', status='ACTIVE'),
                [],
            )
        )

    return resources
