"""Glue inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _glue_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def _glue_reader_for_table(table: dict[str, Any]) -> str:
    storage = table.get('StorageDescriptor') or {}
    input_format = (storage.get('InputFormat') or '').lower()
    serde = ((storage.get('SerdeInfo') or {}).get('SerializationLibrary') or '').lower()
    haystack = f'{input_format} {serde}'

    if 'parquet' in haystack:
        return 'read_parquet'

    if 'json' in haystack or 'hive' in input_format:
        return 'read_json_auto'

    return 'read_csv_auto'


def _glue_table_column_names(table: dict[str, Any]) -> list[str]:
    storage = table.get('StorageDescriptor') or {}
    columns = storage.get('Columns') or []
    partition_keys = table.get('PartitionKeys') or []
    names: list[str] = []
    for column in [*columns, *partition_keys]:
        if not isinstance(column, dict):
            continue
        name = column.get('Name')
        if name and name not in names:
            names.append(name)
    return names


def glue_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    glue = factory.client('glue')
    operations = set(glue.meta.service_model.operation_names)
    databases = _safe_value(lambda: core._paginate(glue, 'get_databases', 'DatabaseList'), [])
    registries = _glue_optional(lambda: core._paginate(glue, 'list_registries', 'Registries'), {'EntityNotFoundException'})

    def table_detail(database_name: str, table: dict[str, Any]) -> dict[str, Any]:
        table_name = table.get('Name')
        storage = table.get('StorageDescriptor') or {}
        column_names = _glue_table_column_names(table)
        partitions = _glue_optional(
            lambda: core._paginate(glue, 'get_partitions', 'Partitions', DatabaseName=database_name, TableName=table_name),
            {'EntityNotFoundException'},
        )
        table_statistics = _glue_optional(
            lambda: glue.get_column_statistics_for_table(
                DatabaseName=database_name,
                TableName=table_name,
                ColumnNames=column_names,
            ).get('ColumnStatisticsList', []),
            {'EntityNotFoundException', 'OperationNotFoundException'},
        ) if column_names and 'GetColumnStatisticsForTable' in operations else []
        if not isinstance(table_statistics, list):
            table_statistics = []
        versions = _glue_optional(
            lambda: core._paginate(glue, 'get_table_versions', 'TableVersions', DatabaseName=database_name, TableName=table_name),
            {'EntityNotFoundException', 'OperationNotFoundException'},
        ) if 'GetTableVersions' in operations else []
        if not isinstance(versions, list):
            versions = []

        return {
            'name': table_name,
            'database': database_name,
            'type': table.get('TableType'),
            'created': table.get('CreateTime'),
            'updated': table.get('UpdateTime'),
            'owner': table.get('Owner'),
            'retention': table.get('Retention'),
            'location': storage.get('Location'),
            'input_format': storage.get('InputFormat'),
            'output_format': storage.get('OutputFormat'),
            'serde': storage.get('SerdeInfo'),
            'columns': storage.get('Columns'),
            'partition_keys': table.get('PartitionKeys'),
            'parameters': table.get('Parameters'),
            'duckdb_reader': _glue_reader_for_table(table),
            'partition_count': len(partitions) if isinstance(partitions, list) else 0,
            'partitions': partitions,
            'column_statistics_count': len(table_statistics),
            'column_statistics': table_statistics,
            'version_count': len(versions),
            'versions': versions,
        }

    def database_detail(database: dict[str, Any]) -> dict[str, Any]:
        name = database.get('Name')
        details = _glue_optional(
            lambda: glue.get_database(Name=name).get('Database', {}),
            {'EntityNotFoundException'},
        )
        tables = _glue_optional(
            lambda: core._paginate(glue, 'get_tables', 'TableList', DatabaseName=name),
            {'EntityNotFoundException'},
        )
        functions = _glue_optional(
            lambda: core._paginate(glue, 'get_user_defined_functions', 'UserDefinedFunctions', DatabaseName=name, Pattern='*'),
            {'EntityNotFoundException'},
        )
        arn = database.get('DatabaseArn') or database.get('Arn') or (details.get('DatabaseArn') if isinstance(details, dict) else None)
        tags = _glue_optional(
            lambda: glue.get_tags(ResourceArn=arn).get('Tags', {}),
            {'EntityNotFoundException', 'OperationNotFoundException'},
        ) if arn and 'GetTags' in operations else None
        table_details = [
            table_detail(name, table)
            for table in tables
        ] if isinstance(tables, list) else []
        function_details = [
            {
                'name': function.get('FunctionName'),
                'database': name,
                'class_name': function.get('ClassName'),
                'owner_name': function.get('OwnerName'),
                'owner_type': function.get('OwnerType'),
                'created': function.get('CreateTime'),
                'resource_uris': function.get('ResourceUris'),
            }
            for function in functions
        ] if isinstance(functions, list) else []

        return {
            'name': name,
            'description': database.get('Description') or (details.get('Description') if isinstance(details, dict) else None),
            'location_uri': database.get('LocationUri') or (details.get('LocationUri') if isinstance(details, dict) else None),
            'parameters': database.get('Parameters') or (details.get('Parameters') if isinstance(details, dict) else None),
            'created': database.get('CreateTime') or (details.get('CreateTime') if isinstance(details, dict) else None),
            'arn': arn,
            'tags': tags,
            'tables': table_details,
            'functions': function_details,
            'table_count': len(table_details),
            'function_count': len(function_details),
            'partition_count': sum(table.get('partition_count') or 0 for table in table_details),
            'table_version_count': sum(table.get('version_count') or 0 for table in table_details),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_databases = [database_detail(database) for database in databases]
    functions = [function for database in detailed_databases for function in database.get('functions', [])]

    def schema_detail(registry_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        schema_name = schema.get('SchemaName') or schema.get('SchemaArn') or schema.get('name')
        versions = _glue_optional(
            lambda: core._paginate(
                glue,
                'list_schema_versions',
                'Schemas',
                SchemaId={'RegistryName': registry_name, 'SchemaName': schema_name},
            ),
            {'EntityNotFoundException'},
        )
        if not isinstance(versions, list):
            versions = _glue_optional(
                lambda: core._paginate(
                    glue,
                    'list_schema_versions',
                    'SchemaVersions',
                    SchemaId={'RegistryName': registry_name, 'SchemaName': schema_name},
                ),
                {'EntityNotFoundException'},
            )

        return {
            'name': schema_name,
            'registry': registry_name,
            'arn': schema.get('SchemaArn'),
            'status': schema.get('SchemaStatus') or schema.get('Status'),
            'data_format': schema.get('DataFormat'),
            'compatibility': schema.get('Compatibility'),
            'created': schema.get('CreatedTime'),
            'updated': schema.get('UpdatedTime'),
            'version_count': len(versions) if isinstance(versions, list) else 0,
            'versions': versions,
        }

    def registry_detail(registry: dict[str, Any]) -> dict[str, Any]:
        name = registry.get('RegistryName') or registry.get('Name')
        schemas = _glue_optional(
            lambda: core._paginate(glue, 'list_schemas', 'Schemas', RegistryId={'RegistryName': name}),
            {'EntityNotFoundException'},
        )
        schema_details = [
            schema_detail(name, schema)
            for schema in schemas
        ] if isinstance(schemas, list) else []

        return {
            'name': name,
            'arn': registry.get('RegistryArn'),
            'description': registry.get('Description'),
            'status': registry.get('Status') or registry.get('RegistryStatus'),
            'created': registry.get('CreatedTime'),
            'updated': registry.get('UpdatedTime'),
            'schema_count': len(schema_details),
            'version_count': sum(schema.get('version_count') or 0 for schema in schema_details),
            'schemas': schema_details,
            'details': schemas if isinstance(schemas, dict) and schemas.get('error') else None,
        }

    detailed_registries = [
        registry_detail(registry)
        for registry in registries
    ] if isinstance(registries, list) else []

    return {
        'summary': {
            'databases': len(detailed_databases),
            'tables': sum(database.get('table_count') or 0 for database in detailed_databases),
            'partitions': sum(database.get('partition_count') or 0 for database in detailed_databases),
            'column_statistics': sum(
                table.get('column_statistics_count') or 0
                for database in detailed_databases
                for table in database.get('tables', [])
            ),
            'table_versions': sum(database.get('table_version_count') or 0 for database in detailed_databases),
            'functions': len(functions),
            'registries': len(detailed_registries),
            'schemas': sum(registry.get('schema_count') or 0 for registry in detailed_registries),
            'schema_versions': sum(registry.get('version_count') or 0 for registry in detailed_registries),
        },
        'databases': detailed_databases,
        # Tables as their own list, as in the Glue console's Tables page.
        'tables': [table for database in detailed_databases for table in database.get('tables', [])],
        'functions': functions,
        'registries': detailed_registries,
        'supported': {
            'Databases': ['CreateDatabase', 'GetDatabase', 'GetDatabases', 'DeleteDatabase', 'UpdateDatabase'],
            'Tables': ['CreateTable', 'GetTable', 'GetTables', 'DeleteTable', 'BatchDeleteTable', 'UpdateTable', 'GetTableVersions'],
            'Partitions': ['CreatePartition', 'BatchCreatePartition', 'GetPartition', 'GetPartitions', 'DeletePartition'],
            'Statistics': ['GetColumnStatisticsForTable', 'DeleteColumnStatisticsForTable'],
            'User-defined functions': ['CreateUserDefinedFunction', 'GetUserDefinedFunction', 'GetUserDefinedFunctions', 'UpdateUserDefinedFunction', 'DeleteUserDefinedFunction'],
            'Registries': ['CreateRegistry', 'GetRegistry', 'ListRegistries', 'UpdateRegistry', 'DeleteRegistry'],
            'Schemas': ['CreateSchema', 'GetSchema', 'ListSchemas', 'UpdateSchema', 'DeleteSchema'],
            'Versions': ['RegisterSchemaVersion', 'GetSchemaVersion', 'ListSchemaVersions', 'DeleteSchemaVersions'],
        },
        'athena_integration': [
            'Athena reads Glue tables for the target database and creates DuckDB views over S3 objects.',
            'The reader function is inferred from StorageDescriptor.InputFormat and SerDe serialization library.',
        ],
        'format_inference': {
            'parquet': 'read_parquet',
            'json_or_hive': 'read_json_auto',
            'default': 'read_csv_auto',
        },
        'notes': [
            'Floci 1.5.25 adds Glue partition APIs, column statistics, table statistics deletion, missing-table delete rejection, and deterministic GetPartitions ordering.',
            'Floci 1.5.23 adds Glue user-defined functions and DeleteDatabase support.',
            'Floci 1.5.23 enforces Glue table version checks and preserves Glue view table fields.',
            'Floci 1.5.24 normalizes catalog names, preserves column parameters, supports UpdateDatabase and BatchDeleteTable, archives table versions, and exposes database tags.',
        ],
    }


def list_glue_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    glue = factory.client('glue')
    resources = [
        {
            'type': 'database',
            'name': database.get('Name'),
        }
        for database in _safe_value(lambda: core._paginate(glue, 'get_databases', 'DatabaseList'), [])
    ]
    for database in list(resources):
        database_name = database.get('name')
        tables = _safe_value(lambda database_name=database_name: core._paginate(glue, 'get_tables', 'TableList', DatabaseName=database_name), [])
        resources.extend(
            {
                'type': 'table',
                'database': database_name,
                'name': table.get('Name'),
                'location': (table.get('StorageDescriptor') or {}).get('Location'),
            }
            for table in tables
        )
        for table in tables:
            table_name = table.get('Name')
            resources.extend(
                {
                    'type': 'partition',
                    'database': database_name,
                    'table': table_name,
                    'values': partition.get('Values'),
                }
                for partition in _safe_value(
                    lambda database_name=database_name, table_name=table_name: core._paginate(
                        glue,
                        'get_partitions',
                        'Partitions',
                        DatabaseName=database_name,
                        TableName=table_name,
                    ),
                    [],
                )
            )
        resources.extend(
            {
                'type': 'user_defined_function',
                'database': database_name,
                'name': function.get('FunctionName'),
                'class_name': function.get('ClassName'),
            }
            for function in _safe_value(
                lambda database_name=database_name: core._paginate(
                    glue,
                    'get_user_defined_functions',
                    'UserDefinedFunctions',
                    DatabaseName=database_name,
                    Pattern='*',
                ),
                [],
            )
        )
    return resources
