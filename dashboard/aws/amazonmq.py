"""Amazon MQ inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def amazonmq_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    mq = factory.client('mq')
    operations = set(mq.meta.service_model.operation_names)

    brokers = _safe_value(
        lambda: core._operation_items(mq, 'list_brokers', 'BrokerSummaries'),
        [],
    ) if 'ListBrokers' in operations else []

    def broker_detail(broker: dict[str, Any]) -> dict[str, Any]:
        broker_id = broker.get('BrokerId')
        details = _safe_value(
            lambda: mq.describe_broker(BrokerId=broker_id),
            {},
        ) if broker_id and 'DescribeBroker' in operations else {}
        engine_type = broker.get('EngineType') or details.get('EngineType')
        # RabbitMQ brokers have no Amazon MQ user API (AWS rejects ListUsers); their users
        # live in the RabbitMQ web console, so the count is unknown rather than zero.
        rabbitmq = str(engine_type or '').upper() == 'RABBITMQ'
        users = None if rabbitmq else _safe_value(
            lambda: core._operation_items(mq, 'list_users', 'Users', BrokerId=broker_id),
            [],
        ) if broker_id and 'ListUsers' in operations else []
        configurations = _safe_value(
            lambda: core._operation_items(mq, 'list_configurations', 'Configurations'),
            [],
        ) if 'ListConfigurations' in operations else []

        return {
            'name': broker.get('BrokerName') or details.get('BrokerName') or broker_id,
            'broker_id': broker_id,
            'arn': broker.get('BrokerArn') or details.get('BrokerArn'),
            'engine_type': engine_type,
            'engine_version': broker.get('EngineVersion') or details.get('EngineVersion'),
            'instance_type': broker.get('HostInstanceType') or details.get('HostInstanceType'),
            'deployment_mode': broker.get('DeploymentMode') or details.get('DeploymentMode'),
            'state': broker.get('BrokerState') or details.get('BrokerState'),
            'created': broker.get('Created') or details.get('Created'),
            'publicly_accessible': details.get('PubliclyAccessible'),
            'endpoints': details.get('BrokerInstances') or details.get('Endpoints'),
            'users': _clean_response(users),
            'user_count': None if users is None else len(users),
            'configurations': _clean_response(configurations),
            'details': _clean_response(details),
        }

    detailed_brokers = [broker_detail(broker) for broker in brokers]

    return {
        'summary': {
            'brokers': len(detailed_brokers),
            'running_brokers': sum(1 for broker in detailed_brokers if broker.get('state') == 'RUNNING'),
            'users': sum(broker.get('user_count') or 0 for broker in detailed_brokers),
            'available_sdk_operations': len(operations),
        },
        'brokers': detailed_brokers,
        'supported_from_sdk': [
            operation
            for operation in [
                'CreateBroker',
                'DescribeBroker',
                'ListBrokers',
                'DeleteBroker',
                'CreateUser',
                'ListUsers',
                'DeleteUser',
                'CreateConfiguration',
                'ListConfigurations',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci 1.5.30 adds the Amazon MQ broker control plane backed by RabbitMQ.',
            'The dashboard inspects brokers, users, and configurations when the local SDK model exposes those APIs.',
        ],
    }
