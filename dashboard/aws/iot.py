"""IoT Core inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def iot_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    iot = factory.client('iot')
    operations = set(iot.meta.service_model.operation_names)

    things = _safe_value(lambda: core._operation_items(iot, 'list_things', 'things'), []) if 'ListThings' in operations else []
    thing_types = _safe_value(lambda: core._operation_items(iot, 'list_thing_types', 'thingTypes'), []) if 'ListThingTypes' in operations else []
    policies = _safe_value(lambda: core._operation_items(iot, 'list_policies', 'policies'), []) if 'ListPolicies' in operations else []
    certificates = _safe_value(lambda: core._operation_items(iot, 'list_certificates', 'certificates'), []) if 'ListCertificates' in operations else []
    topic_rules = _safe_value(lambda: core._operation_items(iot, 'list_topic_rules', 'rules'), []) if 'ListTopicRules' in operations else []
    jobs = _safe_value(lambda: core._operation_items(iot, 'list_jobs', 'jobs'), []) if 'ListJobs' in operations else []
    role_aliases = _safe_value(lambda: core._operation_items(iot, 'list_role_aliases', 'roleAliases'), []) if 'ListRoleAliases' in operations else []

    return {
        'summary': {
            'things': len(things),
            'thing_types': len(thing_types),
            'policies': len(policies),
            'certificates': len(certificates),
            'topic_rules': len(topic_rules),
            'jobs': len(jobs),
            'role_aliases': len(role_aliases),
            'available_sdk_operations': len(operations),
        },
        'things': _clean_response(things),
        'thing_types': _clean_response(thing_types),
        'policies': _clean_response(policies),
        'certificates': _clean_response(certificates),
        'topic_rules': _clean_response(topic_rules),
        'jobs': _clean_response(jobs),
        'role_aliases': _clean_response(role_aliases),
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateThing',
                'ListThings',
                'DescribeThing',
                'DeleteThing',
                'CreatePolicy',
                'ListPolicies',
                'CreateKeysAndCertificate',
                'ListCertificates',
                'CreateTopicRule',
                'ListTopicRules',
                'CreateJob',
                'ListJobs',
                'CreateRoleAlias',
                'ListRoleAliases',
                'AttachPolicy',
                'AttachThingPrincipal',
                'ListTagsForResource',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'This read-only inspector focuses on registry, policy, certificate, topic rule, job, and role-alias inventory.',
        ],
    }


def list_iot_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    iot = factory.client('iot')
    operations = set(iot.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListThings' in operations:
        resources.extend(
            {
                'type': 'thing',
                'name': thing.get('thingName'),
                'arn': thing.get('thingArn'),
                'thing_type': thing.get('thingTypeName'),
            }
            for thing in _safe_value(lambda: core._operation_items(iot, 'list_things', 'things'), [])
        )
    if 'ListPolicies' in operations:
        resources.extend(
            {
                'type': 'policy',
                'name': policy.get('policyName'),
                'arn': policy.get('policyArn'),
            }
            for policy in _safe_value(lambda: core._operation_items(iot, 'list_policies', 'policies'), [])
        )
    if 'ListCertificates' in operations:
        resources.extend(
            {
                'type': 'certificate',
                'id': certificate.get('certificateId'),
                'arn': certificate.get('certificateArn'),
                'status': certificate.get('status'),
            }
            for certificate in _safe_value(lambda: core._operation_items(iot, 'list_certificates', 'certificates'), [])
        )

    return resources
