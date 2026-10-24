"""Backup inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _safe_value


def backup_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    backup = factory.client('backup')
    vaults = _safe_value(lambda: core._paginate(backup, 'list_backup_vaults', 'BackupVaultList'), [])
    plans = _safe_value(lambda: core._paginate(backup, 'list_backup_plans', 'BackupPlansList'), [])
    backup_jobs = _safe_value(lambda: core._paginate(backup, 'list_backup_jobs', 'BackupJobs'), [])
    restore_jobs = _safe_value(lambda: core._paginate(backup, 'list_restore_jobs', 'RestoreJobs'), [])
    protected_resources = _safe_value(lambda: core._paginate(backup, 'list_protected_resources', 'Results'), [])

    def vault_detail(vault: dict[str, Any]) -> dict[str, Any]:
        name = vault.get('BackupVaultName')
        recovery_points = _safe_value(
            lambda: core._paginate(backup, 'list_recovery_points_by_backup_vault', 'RecoveryPoints', BackupVaultName=name),
            [],
        ) if name else []

        return {
            'name': name,
            'arn': vault.get('BackupVaultArn'),
            'created': vault.get('CreationDate'),
            'creator_request_id': vault.get('CreatorRequestId'),
            'encryption_key_arn': vault.get('EncryptionKeyArn'),
            'recovery_points': vault.get('NumberOfRecoveryPoints'),
            'locked': vault.get('Locked'),
            'min_retention_days': vault.get('MinRetentionDays'),
            'max_retention_days': vault.get('MaxRetentionDays'),
            'recovery_point_count': len(recovery_points),
            'recovery_point_details': recovery_points,
        }

    def plan_detail(plan: dict[str, Any]) -> dict[str, Any]:
        plan_id = plan.get('BackupPlanId')
        details = _safe_value(lambda: backup.get_backup_plan(BackupPlanId=plan_id), {}) if plan_id else {}
        selections = _safe_value(
            lambda: core._paginate(backup, 'list_backup_selections', 'BackupSelectionsList', BackupPlanId=plan_id),
            [],
        ) if plan_id else []

        return {
            'name': plan.get('BackupPlanName'),
            'arn': plan.get('BackupPlanArn'),
            'id': plan_id,
            'version_id': plan.get('VersionId'),
            'created': plan.get('CreationDate'),
            'deleted': plan.get('DeletionDate'),
            'last_execution': plan.get('LastExecutionDate'),
            'advanced_backup_settings': details.get('AdvancedBackupSettings'),
            'rules': details.get('BackupPlan', {}).get('Rules'),
            'selections': selections,
            'selection_count': len(selections),
        }

    detailed_vaults = [vault_detail(vault) for vault in vaults]
    detailed_plans = [plan_detail(plan) for plan in plans]

    return {
        'summary': {
            'vaults': len(detailed_vaults),
            'plans': len(detailed_plans),
            'backup_jobs': len(backup_jobs),
            'restore_jobs': len(restore_jobs),
            'protected_resources': len(protected_resources),
            'recovery_points': sum(vault.get('recovery_point_count') or 0 for vault in detailed_vaults),
        },
        'vaults': detailed_vaults,
        'plans': detailed_plans,
        'backup_jobs': backup_jobs,
        'restore_jobs': restore_jobs,
        'protected_resources': protected_resources,
        'supported': [
            'CreateBackupVault',
            'DescribeBackupVault',
            'ListBackupVaults',
            'DeleteBackupVault',
            'CreateBackupPlan',
            'GetBackupPlan',
            'UpdateBackupPlan',
            'ListBackupPlans',
            'DeleteBackupPlan',
            'CreateBackupSelection',
            'GetBackupSelection',
            'ListBackupSelections',
            'DeleteBackupSelection',
            'StartBackupJob',
            'DescribeBackupJob',
            'StopBackupJob',
            'ListBackupJobs',
            'DescribeRecoveryPoint',
            'ListRecoveryPointsByBackupVault',
            'DeleteRecoveryPoint',
            'ListTags',
            'TagResource',
            'UntagResource',
            'GetSupportedResourceTypes',
        ],
        'notes': [
            'Backup jobs are simulated and transition from CREATED to RUNNING to COMPLETED after the configured delay.',
            'When a job completes, Floci creates a recovery point in the target vault and increments the vault recovery-point counter.',
            'Supported resource type codes include S3, RDS, DynamoDB, EFS, EC2, EBS, Aurora, DocumentDB, Neptune, FSx, and VirtualMachine.',
            'A recovery point is metadata only: Floci copies no data, so there is nothing to restore. Restore jobs, copy jobs, report plans, frameworks and legal holds are not supported.',
            'Backup plan schedules never run. Start a backup job yourself to create a recovery point.',
            'Vault notifications are stored but never published to SNS. Vault access policies are stored but not evaluated: a Deny does not block any vault operation.',
            'A vault lock is enforced: deleting a recovery point younger than the minimum retention is refused. Backup selections drop ListOfTags and Conditions, so a tag-based selection matches nothing, and plan lifecycle rules are not validated.',
        ],
    }


def list_backup_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    backup = factory.client('backup')
    resources = [
        {
            'type': 'backup_vault',
            'name': vault.get('BackupVaultName'),
            'arn': vault.get('BackupVaultArn'),
            'recovery_points': vault.get('NumberOfRecoveryPoints'),
        }
        for vault in _safe_value(lambda: core._paginate(backup, 'list_backup_vaults', 'BackupVaultList'), [])
    ]
    resources.extend(
        {
            'type': 'backup_plan',
            'name': plan.get('BackupPlanName'),
            'arn': plan.get('BackupPlanArn'),
            'id': plan.get('BackupPlanId'),
            'version_id': plan.get('VersionId'),
        }
        for plan in _safe_value(lambda: core._paginate(backup, 'list_backup_plans', 'BackupPlansList'), [])
    )
    resources.extend(
        {
            'type': 'protected_resource',
            'name': resource.get('ResourceName') or resource.get('ResourceArn'),
            'arn': resource.get('ResourceArn'),
            'resource_type': resource.get('ResourceType'),
            'last_backup_time': resource.get('LastBackupTime'),
        }
        for resource in _safe_value(lambda: core._paginate(backup, 'list_protected_resources', 'Results'), [])
    )
    return resources
