"""Cloud Control inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response


_CLOUDCONTROL_TYPE_PRESETS = [
    'AWS::ApiGateway::RestApi',
    'AWS::CloudFormation::Stack',
    'AWS::CloudWatch::Alarm',
    'AWS::DynamoDB::Table',
    'AWS::EC2::Instance',
    'AWS::EC2::SecurityGroup',
    'AWS::EC2::Subnet',
    'AWS::EC2::VPC',
    'AWS::Events::Rule',
    'AWS::IAM::Role',
    'AWS::IAM::User',
    'AWS::Kinesis::Stream',
    'AWS::Lambda::Function',
    'AWS::Logs::LogGroup',
    'AWS::S3::Bucket',
    'AWS::SecretsManager::Secret',
    'AWS::SNS::Topic',
    'AWS::SQS::Queue',
    'AWS::StepFunctions::StateMachine',
]


def _cloudcontrol_resource_items(cloudcontrol, type_name: str) -> tuple[list[dict[str, Any]], str | None]:
    try:
        resource_descriptions = core._paginate(
            cloudcontrol,
            'list_resources',
            'ResourceDescriptions',
            TypeName=type_name,
            MaxResults=100,
        )
    except Exception as exc:
        return [], str(exc)

    items = []
    for resource in resource_descriptions:
        properties = resource.get('Properties')
        parsed_properties = properties
        if isinstance(properties, str):
            try:
                parsed_properties = json.loads(properties)
            except ValueError:
                parsed_properties = properties
        items.append({
            'name': resource.get('Identifier') or type_name,
            'type_name': type_name,
            'identifier': resource.get('Identifier'),
            'properties': _clean_response(parsed_properties),
        })
    return items, None


def cloudcontrol_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    cloudcontrol = factory.client('cloudcontrol')
    operations = set(cloudcontrol.meta.service_model.operation_names)

    type_results = []
    resources = []
    errors = []
    if 'ListResources' in operations:
        for type_name in _CLOUDCONTROL_TYPE_PRESETS:
            items, error = _cloudcontrol_resource_items(cloudcontrol, type_name)
            type_results.append({
                'name': type_name,
                'type_name': type_name,
                'count': len(items),
                'error': error,
            })
            resources.extend(items)
            if error:
                errors.append({
                    'name': type_name,
                    'type_name': type_name,
                    'error': error,
                })

    return {
        'summary': {
            'type_presets': len(_CLOUDCONTROL_TYPE_PRESETS),
            'resource_types_with_results': sum(1 for result in type_results if result['count']),
            'resources': len(resources),
            'errors': len(errors),
            'available_sdk_operations': len(operations),
        },
        'resources': resources,
        'type_results': type_results,
        'errors': errors,
        'supported_from_sdk': [
            operation
            for operation in [
                'ListResources',
                'GetResource',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'This read-only inspector queries common AWS type-name presets and keeps per-type errors visible so unsupported gaps are easy to spot.',
            'Cloud Control is useful for generic resource discovery tools and complements the service-specific dashboard inventories.',
        ],
    }
