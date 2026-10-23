"""RDS Data API inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core


def rdsdata_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    rdsdata = factory.client('rds-data')
    operations = set(rdsdata.meta.service_model.operation_names)

    return {
        'summary': {
            'statements': 0,
            'transactions': 0,
            'available_sdk_operations': len(operations),
        },
        'statement_operations': [
            {'name': operation}
            for operation in [
                'ExecuteStatement',
                'BatchExecuteStatement',
            ]
            if operation in operations
        ],
        'transaction_operations': [
            {'name': operation}
            for operation in [
                'BeginTransaction',
                'CommitTransaction',
                'RollbackTransaction',
            ]
            if operation in operations
        ],
        'request_fields': [
            {'name': 'resourceArn', 'description': 'RDS cluster ARN for the Data API target.'},
            {'name': 'secretArn', 'description': 'Secret ARN used by the Data API request.'},
            {'name': 'database', 'description': 'Optional database name.'},
            {'name': 'sql', 'description': 'SQL statement for ExecuteStatement or BatchExecuteStatement.'},
            {'name': 'transactionId', 'description': 'Transaction token returned by BeginTransaction.'},
        ],
        'supported_from_sdk': [
            operation
            for operation in [
                'ExecuteStatement',
                'BatchExecuteStatement',
                'BeginTransaction',
                'CommitTransaction',
                'RollbackTransaction',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'The RDS Data API has no resources to list, so this page shows the calls it supports and their required fields.',
        ],
    }


def list_rdsdata_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    operations = set(factory.client('rds-data').meta.service_model.operation_names)
    return [
        {
            'type': 'operation',
            'name': operation,
            'status': 'supported',
        }
        for operation in [
            'ExecuteStatement',
            'BatchExecuteStatement',
            'BeginTransaction',
            'CommitTransaction',
            'RollbackTransaction',
        ]
        if operation in operations
    ]
