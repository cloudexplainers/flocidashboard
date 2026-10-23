"""Transfer Family inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def transfer_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    transfer = factory.client('transfer')
    servers = _safe_value(lambda: core._paginate(transfer, 'list_servers', 'Servers'), [])
    workflows = _safe_value(lambda: core._paginate(transfer, 'list_workflows', 'Workflows'), [])
    profiles = _safe_value(lambda: core._paginate(transfer, 'list_profiles', 'Profiles'), [])
    certificates = _safe_value(lambda: core._paginate(transfer, 'list_certificates', 'Certificates'), [])
    connectors = _safe_value(lambda: core._paginate(transfer, 'list_connectors', 'Connectors'), [])
    security_policies = _safe_value(lambda: core._paginate(transfer, 'list_security_policies', 'SecurityPolicyNames'), [])
    web_apps = _safe_value(lambda: core._paginate(transfer, 'list_web_apps', 'WebApps'), [])

    def server_detail(server: dict[str, Any]) -> dict[str, Any]:
        server_id = server.get('ServerId')
        details = _safe_value(lambda: transfer.describe_server(ServerId=server_id).get('Server', {}), {}) if server_id else {}
        users = _safe_value(lambda: core._paginate(transfer, 'list_users', 'Users', ServerId=server_id), []) if server_id else []
        host_keys = _safe_value(lambda: transfer.list_host_keys(ServerId=server_id).get('HostKeys', []), []) if server_id else []
        agreements = _safe_value(lambda: core._paginate(transfer, 'list_agreements', 'Agreements', ServerId=server_id), []) if server_id else []

        return {
            'name': server_id,
            'id': server_id,
            'arn': server.get('Arn') or details.get('Arn'),
            'state': server.get('State') or details.get('State'),
            'endpoint_type': server.get('EndpointType') or details.get('EndpointType'),
            'domain': server.get('Domain') or details.get('Domain'),
            'identity_provider_type': server.get('IdentityProviderType') or details.get('IdentityProviderType'),
            'protocols': details.get('Protocols'),
            'endpoint_details': details.get('EndpointDetails'),
            'logging_role': details.get('LoggingRole'),
            'structured_log_destinations': details.get('StructuredLogDestinations'),
            'security_policy_name': details.get('SecurityPolicyName'),
            'workflow_details': details.get('WorkflowDetails'),
            'certificate': details.get('Certificate'),
            'tags': details.get('Tags'),
            'user_count': len(users),
            'users': _clean_response(users),
            'host_key_count': len(host_keys),
            'host_keys': _clean_response(host_keys),
            'agreement_count': len(agreements),
            'agreements': _clean_response(agreements),
        }

    def profile_detail(profile: dict[str, Any]) -> dict[str, Any]:
        profile_id = profile.get('ProfileId')
        details = _safe_value(lambda: transfer.describe_profile(ProfileId=profile_id).get('Profile', {}), {}) if profile_id else {}
        return {
            'name': profile_id,
            'id': profile_id,
            'arn': profile.get('Arn') or details.get('Arn'),
            'as2_id': details.get('As2Id'),
            'profile_type': details.get('ProfileType'),
            'certificate_ids': details.get('CertificateIds'),
            'tags': details.get('Tags'),
        }

    def certificate_detail(certificate: dict[str, Any]) -> dict[str, Any]:
        certificate_id = certificate.get('CertificateId')
        details = _safe_value(
            lambda: transfer.describe_certificate(CertificateId=certificate_id).get('Certificate', {}),
            {},
        ) if certificate_id else {}
        return {
            'name': certificate_id,
            'id': certificate_id,
            'arn': certificate.get('Arn') or details.get('Arn'),
            'status': details.get('Status'),
            'type': details.get('Type'),
            'usage': details.get('Usage'),
            'certificate': details.get('Certificate'),
            'active_date': details.get('ActiveDate'),
            'inactive_date': details.get('InactiveDate'),
            'serial': details.get('Serial'),
            'not_before_date': details.get('NotBeforeDate'),
            'not_after_date': details.get('NotAfterDate'),
            'description': details.get('Description'),
            'tags': details.get('Tags'),
        }

    def connector_detail(connector: dict[str, Any]) -> dict[str, Any]:
        connector_id = connector.get('ConnectorId')
        details = _safe_value(
            lambda: transfer.describe_connector(ConnectorId=connector_id).get('Connector', {}),
            {},
        ) if connector_id else {}
        return {
            'name': connector_id,
            'id': connector_id,
            'arn': connector.get('Arn') or details.get('Arn'),
            'url': details.get('Url'),
            'as2_config': details.get('As2Config'),
            'access_role': details.get('AccessRole'),
            'logging_role': details.get('LoggingRole'),
            'security_policy_name': details.get('SecurityPolicyName'),
            'tags': details.get('Tags'),
        }

    detailed_servers = [server_detail(server) for server in servers]
    detailed_profiles = [profile_detail(profile) for profile in profiles]
    detailed_certificates = [certificate_detail(certificate) for certificate in certificates]
    detailed_connectors = [connector_detail(connector) for connector in connectors]

    return {
        'summary': {
            'servers': len(detailed_servers),
            'users': sum(server.get('user_count') or 0 for server in detailed_servers),
            'host_keys': sum(server.get('host_key_count') or 0 for server in detailed_servers),
            'agreements': sum(server.get('agreement_count') or 0 for server in detailed_servers),
            'workflows': len(workflows),
            'profiles': len(detailed_profiles),
            'certificates': len(detailed_certificates),
            'connectors': len(detailed_connectors),
            'security_policies': len(security_policies),
            'web_apps': len(web_apps),
        },
        'servers': detailed_servers,
        # Users as their own list (the console shows them per server), with the server ID
        # so user actions can target them.
        'users': [
            {
                'name': user.get('UserName'),
                'user_name': user.get('UserName'),
                'server_id': server.get('id'),
                'arn': user.get('Arn'),
                'role': user.get('Role'),
                'home_directory': user.get('HomeDirectory'),
                'home_directory_type': user.get('HomeDirectoryType'),
                'ssh_public_key_count': user.get('SshPublicKeyCount'),
            }
            for server in detailed_servers
            for user in server.get('users') or []
        ],
        'workflows': _clean_response(workflows),
        'profiles': detailed_profiles,
        'certificates': detailed_certificates,
        'connectors': detailed_connectors,
        'security_policies': _clean_response(security_policies),
        'web_apps': _clean_response(web_apps),
        'supported': [
            'CreateServer',
            'DescribeServer',
            'UpdateServer',
            'ListServers',
            'StartServer',
            'StopServer',
            'DeleteServer',
            'CreateUser',
            'DescribeUser',
            'UpdateUser',
            'ListUsers',
            'DeleteUser',
            'ImportSshPublicKey',
            'DeleteSshPublicKey',
            'TagResource',
            'UntagResource',
            'ListTagsForResource',
        ],
        'notes': [
            'Floci Transfer Family covers the management-plane API only; actual SFTP, FTP, FTPS, and AS2 file transfer is not emulated.',
            'Server EndpointType defaults to PUBLIC, and State transitions between ONLINE and OFFLINE through StartServer and StopServer.',
            'SSH key bodies are stored and returned as-is without cryptographic validation.',
            'Server ARNs follow arn:aws:transfer:{region}:{accountId}:server/{serverId}; user ARNs include user/{serverId}/{userName}.',
        ],
    }


def list_transfer_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    transfer = factory.client('transfer')
    resources = [
        {
            'type': 'server',
            'name': server.get('ServerId'),
            'arn': server.get('Arn'),
            'state': server.get('State'),
            'endpoint_type': server.get('EndpointType'),
        }
        for server in _safe_value(lambda: core._paginate(transfer, 'list_servers', 'Servers'), [])
    ]
    resources.extend(
        {
            'type': 'workflow',
            'name': workflow.get('WorkflowId'),
            'arn': workflow.get('Arn'),
            'description': workflow.get('Description'),
        }
        for workflow in _safe_value(lambda: core._paginate(transfer, 'list_workflows', 'Workflows'), [])
    )
    resources.extend(
        {
            'type': 'profile',
            'name': profile.get('ProfileId'),
            'arn': profile.get('Arn'),
            'profile_type': profile.get('ProfileType'),
        }
        for profile in _safe_value(lambda: core._paginate(transfer, 'list_profiles', 'Profiles'), [])
    )
    resources.extend(
        {
            'type': 'connector',
            'name': connector.get('ConnectorId'),
            'arn': connector.get('Arn'),
            'url': connector.get('Url'),
        }
        for connector in _safe_value(lambda: core._paginate(transfer, 'list_connectors', 'Connectors'), [])
    )
    return resources
