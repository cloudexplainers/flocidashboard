"""API Gateway inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _apigateway_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (BotoCoreError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def apigateway_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    apigateway = factory.client('apigateway')
    apigatewayv2 = factory.client('apigatewayv2')

    rest_apis = _safe_value(lambda: core._paginate(apigateway, 'get_rest_apis', 'items'), [])
    http_apis = _safe_value(lambda: core._paginate(apigatewayv2, 'get_apis', 'Items'), [])

    def rest_api_detail(api: dict[str, Any]) -> dict[str, Any]:
        api_id = api.get('id')
        resources = _apigateway_optional(
            lambda: core._paginate(apigateway, 'get_resources', 'items', restApiId=api_id),
            {'NotFoundException'},
        )
        deployments = _apigateway_optional(
            lambda: core._paginate(apigateway, 'get_deployments', 'items', restApiId=api_id),
            {'NotFoundException'},
        )
        stages = _apigateway_optional(
            lambda: apigateway.get_stages(restApiId=api_id).get('item', []),
            {'NotFoundException'},
        )
        authorizers = _apigateway_optional(
            lambda: core._paginate(apigateway, 'get_authorizers', 'items', restApiId=api_id),
            {'NotFoundException'},
        )
        validators = _apigateway_optional(
            lambda: core._paginate(apigateway, 'get_request_validators', 'items', restApiId=api_id),
            {'NotFoundException'},
        )
        models = _apigateway_optional(
            lambda: core._paginate(apigateway, 'get_models', 'items', restApiId=api_id),
            {'NotFoundException'},
        )

        method_count = 0
        integration_count = 0
        if isinstance(resources, list):
            for resource in resources:
                methods = resource.get('resourceMethods') or {}
                method_count += len(methods)
                integration_count += sum(
                    1
                    for method in methods.values()
                    if isinstance(method, dict) and method.get('methodIntegration')
                )

        return {
            'name': api.get('name') or api_id,
            'id': api_id,
            'description': api.get('description'),
            'created': api.get('createdDate'),
            'version': api.get('version'),
            'api_key_source': api.get('apiKeySource'),
            'endpoint_configuration': api.get('endpointConfiguration'),
            'tags': api.get('tags'),
            'execute_url_pattern': f'{factory.endpoint_url.rstrip("/")}/restapis/{api_id}/{{stage}}/_user_request_/{{path}}' if api_id else None,
            'resource_count': len(resources) if isinstance(resources, list) else 0,
            'method_count': method_count,
            'integration_count': integration_count,
            'deployment_count': len(deployments) if isinstance(deployments, list) else 0,
            'stage_count': len(stages) if isinstance(stages, list) else 0,
            'authorizer_count': len(authorizers) if isinstance(authorizers, list) else 0,
            'validator_count': len(validators) if isinstance(validators, list) else 0,
            'model_count': len(models) if isinstance(models, list) else 0,
            'resources': resources,
            'deployments': deployments,
            'stages': stages,
            'authorizers': authorizers,
            'request_validators': validators,
            'models': models,
        }

    def http_api_detail(api: dict[str, Any]) -> dict[str, Any]:
        api_id = api.get('ApiId')
        routes = _apigateway_optional(
            lambda: core._paginate(apigatewayv2, 'get_routes', 'Items', ApiId=api_id),
            {'NotFoundException'},
        )
        integrations = _apigateway_optional(
            lambda: core._paginate(apigatewayv2, 'get_integrations', 'Items', ApiId=api_id),
            {'NotFoundException'},
        )
        authorizers = _apigateway_optional(
            lambda: core._paginate(apigatewayv2, 'get_authorizers', 'Items', ApiId=api_id),
            {'NotFoundException'},
        )
        stages = _apigateway_optional(
            lambda: core._paginate(apigatewayv2, 'get_stages', 'Items', ApiId=api_id),
            {'NotFoundException'},
        )
        deployments = _apigateway_optional(
            lambda: core._paginate(apigatewayv2, 'get_deployments', 'Items', ApiId=api_id),
            {'NotFoundException'},
        )

        return {
            'name': api.get('Name') or api_id,
            'id': api_id,
            'protocol_type': api.get('ProtocolType'),
            'api_endpoint': api.get('ApiEndpoint'),
            'execute_url_pattern': f'{factory.endpoint_url.rstrip("/")}/restapis/{api_id}/{{stage}}/_user_request_/{{path}}' if api_id else None,
            'created': api.get('CreatedDate'),
            'description': api.get('Description'),
            'route_selection_expression': api.get('RouteSelectionExpression'),
            'tags': api.get('Tags'),
            'route_count': len(routes) if isinstance(routes, list) else 0,
            'integration_count': len(integrations) if isinstance(integrations, list) else 0,
            'authorizer_count': len(authorizers) if isinstance(authorizers, list) else 0,
            'stage_count': len(stages) if isinstance(stages, list) else 0,
            'deployment_count': len(deployments) if isinstance(deployments, list) else 0,
            'routes': routes,
            'integrations': integrations,
            'authorizers': authorizers,
            'stages': stages,
            'deployments': deployments,
        }

    detailed_rest_apis = [rest_api_detail(api) for api in rest_apis]
    detailed_http_apis = [http_api_detail(api) for api in http_apis]
    api_keys = _apigateway_optional(lambda: core._paginate(apigateway, 'get_api_keys', 'items', includeValues=False), set())
    usage_plans = _apigateway_optional(lambda: core._paginate(apigateway, 'get_usage_plans', 'items'), set())
    domain_names = _apigateway_optional(lambda: core._paginate(apigateway, 'get_domain_names', 'items'), set())

    return {
        'summary': {
            'rest_apis': len(detailed_rest_apis),
            'http_apis': len(detailed_http_apis),
            'routes_and_resources': sum(api.get('resource_count') or 0 for api in detailed_rest_apis) + sum(api.get('route_count') or 0 for api in detailed_http_apis),
            'methods': sum(api.get('method_count') or 0 for api in detailed_rest_apis),
            'integrations': sum(api.get('integration_count') or 0 for api in detailed_rest_apis + detailed_http_apis),
            'stages': sum(api.get('stage_count') or 0 for api in detailed_rest_apis + detailed_http_apis),
            'api_keys': len(api_keys) if isinstance(api_keys, list) else 0,
            'usage_plans': len(usage_plans) if isinstance(usage_plans, list) else 0,
        },
        'rest_apis': detailed_rest_apis,
        'http_apis': detailed_http_apis,
        'api_keys': api_keys,
        'usage_plans': usage_plans,
        'domain_names': domain_names,
        'supported_v1': {
            'APIs': ['CreateRestApi', 'ImportRestApi', 'PutRestApi', 'GetRestApi', 'GetRestApis', 'UpdateRestApi', 'DeleteRestApi'],
            'Resources': ['CreateResource', 'GetResource', 'GetResources', 'UpdateResource', 'DeleteResource'],
            'Methods': ['PutMethod', 'GetMethod', 'UpdateMethod', 'DeleteMethod'],
            'Integrations': ['PutIntegration', 'GetIntegration', 'UpdateIntegration', 'DeleteIntegration'],
            'Deployments and stages': ['CreateDeployment', 'GetDeployments', 'DeleteDeployment', 'CreateStage', 'GetStage', 'GetStages', 'UpdateStage', 'DeleteStage'],
            'Authorizers and validation': ['CreateAuthorizer', 'GetAuthorizer', 'GetAuthorizers', 'CreateRequestValidator', 'GetRequestValidator', 'GetRequestValidators', 'DeleteRequestValidator'],
            'Keys and plans': ['CreateApiKey', 'GetApiKey', 'GetApiKeys', 'CreateUsagePlan', 'GetUsagePlans', 'DeleteUsagePlan', 'CreateUsagePlanKey', 'GetUsagePlanKey', 'GetUsagePlanKeys', 'DeleteUsagePlanKey'],
            'Models and domains': ['CreateModel', 'GetModel', 'GetModels', 'DeleteModel', 'CreateDomainName', 'GetDomainName', 'GetDomainNames', 'DeleteDomainName'],
            'Tags': ['TagResource', 'UntagResource', 'GetTags'],
        },
        'supported_v2': {
            'APIs': ['CreateApi', 'GetApi', 'GetApis', 'DeleteApi'],
            'Routes': ['CreateRoute', 'GetRoute', 'GetRoutes', 'DeleteRoute'],
            'Integrations': ['CreateIntegration', 'GetIntegration', 'GetIntegrations'],
            'Authorizers': ['CreateAuthorizer', 'GetAuthorizer', 'GetAuthorizers', 'DeleteAuthorizer'],
            'Stages and deployments': ['CreateStage', 'GetStage', 'GetStages', 'DeleteStage', 'CreateDeployment', 'GetDeployments'],
        },
        'not_implemented_v1': [
            'GetDeployment',
            'UpdateDeployment',
            'UpdateAuthorizer',
            'DeleteAuthorizer',
            'TestInvokeAuthorizer',
            'UpdateApiKey',
            'DeleteApiKey',
            'ImportApiKeys',
            'GetUsagePlan',
            'UpdateUsagePlan',
            'Gateway Responses',
            'Documentation parts and versions',
            'VPC Links',
            'Client Certificates',
            'GetExport',
        ],
        'notes': [
            'Floci supports API Gateway v1 REST APIs and API Gateway v2 HTTP APIs.',
            'Floci 1.5.31 persists API key tags and adds the GetApiKey route.',
            'Floci 1.5.22 implements REST API EndpointConfiguration and the ApiGatewayV2 CloudFormation update path.',
            'Floci 1.5.21 implements the API Gateway v1 DeleteDeployment endpoint and returns JSON 404 errors for missing deployments.',
            'Floci 1.5.19 fills REQUEST authorizer events for REST APIs, supports Lambda REQUEST authorizers on HTTP API v2 routes, and persists v1 UpdateStage methodSettings patch operations.',
            'Floci 1.5.18 forwards API Gateway v2 HTTP API requests through ALB listeners.',
            'Floci 1.5.17 adds API Gateway v1 account management endpoints and API Gateway v2 HTTP_PROXY integrations with request parameter mapping.',
            'The v1 execute plane is served under /restapis/{id}/{stage}/_user_request_/...',
            'This page shows management-plane resources; proxied traffic remains available through Floci directly.',
        ],
    }


def list_apigateway_apis(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    apigateway = factory.client('apigateway')
    apigatewayv2 = factory.client('apigatewayv2')
    apis = [
        {
            'type': 'REST',
            'id': api.get('id'),
            'name': api.get('name'),
        }
        for api in _safe_value(lambda: core._paginate(apigateway, 'get_rest_apis', 'items'), [])
    ]
    apis.extend(
        {
            'type': api.get('ProtocolType') or 'HTTP',
            'id': api.get('ApiId'),
            'name': api.get('Name'),
        }
        for api in _safe_value(lambda: core._paginate(apigatewayv2, 'get_apis', 'Items'), [])
    )
    return apis
