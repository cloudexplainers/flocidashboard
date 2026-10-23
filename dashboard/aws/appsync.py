"""AppSync inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def appsync_inventory() -> dict[str, Any]:
    from ..appsync_schema import read_sdl, resolver_type_names

    appsync = core.FlociClientFactory().client('appsync')
    apis = _safe_value(lambda: core._operation_items(appsync, 'list_graphql_apis', 'graphqlApis'), [])

    def api_detail(api: dict[str, Any]) -> dict[str, Any]:
        api_id = api.get('apiId')
        api_keys = _safe_value(
            lambda: core._operation_items(appsync, 'list_api_keys', 'apiKeys', apiId=api_id),
            [],
        )
        data_sources = _safe_value(
            lambda: core._operation_items(appsync, 'list_data_sources', 'dataSources', apiId=api_id),
            [],
        )
        functions = _safe_value(
            lambda: core._operation_items(appsync, 'list_functions', 'functions', apiId=api_id),
            [],
        )
        types = _safe_value(
            lambda: core._operation_items(appsync, 'list_types', 'types', apiId=api_id, format='SDL'),
            [],
        )
        schema_status = _safe_value(
            lambda: appsync.get_schema_creation_status(apiId=api_id), {},
        )
        schema_definition = _safe_value(
            lambda: read_sdl(appsync.get_introspection_schema(apiId=api_id, format='SDL')), '',
        ) if isinstance(schema_status, dict) and schema_status.get('status') in {'SUCCESS', 'ACTIVE'} else ''
        type_names = list(dict.fromkeys([
            *[item.get('name') for item in types if item.get('name')],
            *resolver_type_names(schema_definition),
        ]))
        resolvers = [
            {
                **_clean_response(resolver),
                'apiId': api_id,
                'typeName': type_name,
            }
            for type_name in type_names
            for resolver in _safe_value(
                lambda type_name=type_name: core._operation_items(
                    appsync,
                    'list_resolvers',
                    'resolvers',
                    apiId=api_id,
                    typeName=type_name,
                ),
                [],
            )
        ]
        tags = _safe_value(
            lambda: appsync.list_tags_for_resource(resourceArn=api.get('arn')).get('tags', {}),
            {},
        ) if api.get('arn') else {}

        return {
            **_clean_response(api),
            'name': api.get('name') or api_id,
            'id': api_id,
            'api_keys': _clean_response(api_keys),
            'data_sources': _clean_response(data_sources),
            'functions': _clean_response(functions),
            'types': _clean_response(types),
            'resolvers': resolvers,
            'tags': _clean_response(tags),
            'schema_status': _clean_response(schema_status),
            'api_key_count': len(api_keys),
            'data_source_count': len(data_sources),
            'function_count': len(functions),
            'type_count': len(types),
            'resolver_count': len(resolvers),
        }

    detailed_apis = [api_detail(api) for api in apis]
    api_keys = [item for api in detailed_apis for item in api.get('api_keys', [])]
    data_sources = [item for api in detailed_apis for item in api.get('data_sources', [])]
    functions = [item for api in detailed_apis for item in api.get('functions', [])]
    types = [item for api in detailed_apis for item in api.get('types', [])]
    resolvers = [item for api in detailed_apis for item in api.get('resolvers', [])]

    return {
        'summary': {
            'graphql_apis': len(detailed_apis),
            'api_keys': len(api_keys),
            'data_sources': len(data_sources),
            'functions': len(functions),
            'types': len(types),
            'resolvers': len(resolvers),
        },
        'graphql_apis': detailed_apis,
        'api_keys': api_keys,
        'data_sources': data_sources,
        'functions': functions,
        'types': types,
        'resolvers': resolvers,
        'supported': [
            'GraphQL API CRUD',
            'Schema creation, status, registry, introspection, and AWS scalar types',
            'API key CRUD',
            'Data source CRUDL',
            'Resolver CRUD',
            'Function CRUD',
            'Type CRUD',
            'TagResource, UntagResource, and ListTagsForResource',
        ],
        'notes': [
            'GraphQL workloads that depend on schema introspection and the full data-source surface should now work end to end against Floci.',
        ],
    }


def list_appsync_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    appsync = factory.client('appsync')
    resources: list[dict[str, Any]] = []
    apis = _safe_value(lambda: core._operation_items(appsync, 'list_graphql_apis', 'graphqlApis'), [])

    for api in apis:
        api_id = api.get('apiId')
        resources.append({
            'type': 'graphql_api',
            'id': api_id,
            'name': api.get('name'),
            'arn': api.get('arn'),
        })
        for result_key, operation, resource_type in (
            ('apiKeys', 'list_api_keys', 'api_key'),
            ('dataSources', 'list_data_sources', 'data_source'),
            ('functions', 'list_functions', 'function'),
        ):
            resources.extend(
                {
                    **_clean_response(item),
                    'type': resource_type,
                    'api_id': api_id,
                    **({'source_type': item.get('type')} if resource_type == 'data_source' else {}),
                }
                for item in _safe_value(
                    lambda operation=operation, result_key=result_key: core._operation_items(
                        appsync,
                        operation,
                        result_key,
                        apiId=api_id,
                    ),
                    [],
                )
            )
        types = _safe_value(
            lambda: core._operation_items(appsync, 'list_types', 'types', apiId=api_id, format='SDL'),
            [],
        )
        resources.extend(
            {
                **_clean_response(type_item),
                'type': 'type',
                'api_id': api_id,
            }
            for type_item in types
        )
        resources.extend(
            {
                **_clean_response(resolver),
                'type': 'resolver',
                'api_id': api_id,
                'type_name': type_item.get('name'),
            }
            for type_item in types
            if type_item.get('name')
            for resolver in _safe_value(
                lambda type_name=type_item.get('name'): core._operation_items(
                    appsync,
                    'list_resolvers',
                    'resolvers',
                    apiId=api_id,
                    typeName=type_name,
                ),
                [],
            )
        )

    return resources
