"""SES inventory and resource-list loaders."""

from __future__ import annotations

import json
from urllib.error import URLError
from urllib.request import urlopen
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _chunks, _clean_response, _error_code, _safe_value, _string_items


def _ses_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def _ses_mailbox(factory: core.FlociClientFactory) -> Any:
    mailbox_url = f'{factory.endpoint_url.rstrip("/")}/_aws/ses'

    try:
        with urlopen(mailbox_url, timeout=2) as response:
            body = response.read().decode('utf-8')
    except URLError as exc:
        return {'error': str(exc.reason)}
    except TimeoutError as exc:
        return {'error': str(exc)}

    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {'raw': body}


def _ses_message_preview(message: dict[str, Any]) -> dict[str, Any]:
    body = message.get('body') or message.get('Body') or message.get('text') or message.get('html')
    preview = None

    if isinstance(body, str):
        preview = body[:160]

    return {
        'id': message.get('id') or message.get('Id') or message.get('messageId') or message.get('MessageId'),
        'from': message.get('from') or message.get('From') or message.get('source') or message.get('Source'),
        'to': message.get('to') or message.get('To') or message.get('destination') or message.get('Destination'),
        'subject': message.get('subject') or message.get('Subject'),
        'timestamp': message.get('timestamp') or message.get('Timestamp'),
        'body_preview': preview,
    }


def _sesv2_items(client, operation_name: str, result_key: str) -> list[Any]:
    # SES v2 list operations expose NextToken without SDK paginator definitions.
    items = []
    tokens = set()
    options = {}
    while True:
        response = getattr(client, operation_name)(**options)
        items.extend(response.get(result_key, []))
        token = response.get('NextToken')
        if not token:
            return items
        if token in tokens:
            raise ValueError(f'{operation_name} returned a repeated NextToken')
        tokens.add(token)
        options = {'NextToken': token}


def ses_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    ses = factory.client('ses')
    sesv2 = factory.client('sesv2')
    sesv2_operations = set(sesv2.meta.service_model.operation_names)
    identities = _safe_value(lambda: core._paginate(ses, 'list_identities', 'Identities'), [])
    templates = _safe_value(lambda: core._paginate(ses, 'list_templates', 'TemplatesMetadata'), [])
    verified_emails = _safe_value(lambda: ses.list_verified_email_addresses().get('VerifiedEmailAddresses', []), [])
    identity_attributes: dict[str, Any] = {}

    for batch in _chunks(identities, 100):
        response = _ses_optional(
            lambda batch=batch: ses.get_identity_verification_attributes(Identities=batch).get('VerificationAttributes', {}),
            {'InvalidParameterValue'},
        )
        if isinstance(response, dict):
            identity_attributes.update(response)

    notification_attributes: dict[str, Any] = {}
    for batch in _chunks(identities, 100):
        response = _ses_optional(
            lambda batch=batch: ses.get_identity_notification_attributes(Identities=batch).get('NotificationAttributes', {}),
            {'InvalidParameterValue'},
        )
        if isinstance(response, dict):
            notification_attributes.update(response)

    dkim_attributes: dict[str, Any] = {}
    for batch in _chunks(identities, 100):
        response = _ses_optional(
            lambda batch=batch: ses.get_identity_dkim_attributes(Identities=batch).get('DkimAttributes', {}),
            {'InvalidParameterValue'},
        )
        if isinstance(response, dict):
            dkim_attributes.update(response)

    detailed_identities = [
        {
            'name': identity,
            'type': 'domain' if '@' not in identity else 'email',
            'verification': identity_attributes.get(identity),
            'notifications': notification_attributes.get(identity),
            'dkim': dkim_attributes.get(identity),
        }
        for identity in identities
    ]

    detailed_templates = [
        {
            'name': template.get('Name'),
            'created': template.get('CreatedTimestamp'),
            'details': _ses_optional(
                lambda template=template: ses.get_template(TemplateName=template.get('Name')).get('Template', {}),
                {'TemplateDoesNotExist'},
            ) if template.get('Name') else None,
        }
        for template in templates
    ]

    mailbox = _ses_mailbox(factory)
    messages: list[dict[str, Any]] = []
    if isinstance(mailbox, list):
        messages = [_ses_message_preview(message) for message in mailbox if isinstance(message, dict)]
    elif isinstance(mailbox, dict):
        raw_messages = mailbox.get('messages') or mailbox.get('Messages') or mailbox.get('items') or mailbox.get('Items') or []
        if isinstance(raw_messages, list):
            messages = [_ses_message_preview(message) for message in raw_messages if isinstance(message, dict)]

    v2_identities = _ses_optional(
        lambda: _sesv2_items(sesv2, 'list_email_identities', 'EmailIdentities'),
        {'NotFoundException', 'BadRequestException'},
    )
    v2_templates = _ses_optional(
        lambda: _sesv2_items(sesv2, 'list_email_templates', 'TemplatesMetadata'),
        {'NotFoundException', 'BadRequestException'},
    )
    v2_configuration_sets = _ses_optional(
        lambda: sesv2.list_configuration_sets().get('ConfigurationSets', []),
        {'NotFoundException', 'BadRequestException'},
    )
    v2_contact_lists = _ses_optional(
        lambda: core._operation_items(sesv2, 'list_contact_lists', 'ContactLists'),
        {'NotFoundException', 'BadRequestException'},
    ) if 'ListContactLists' in sesv2_operations else []

    def configuration_set_name(configuration_set: Any) -> str | None:
        if isinstance(configuration_set, str):
            return configuration_set
        if isinstance(configuration_set, dict):
            return configuration_set.get('Name') or configuration_set.get('ConfigurationSetName')
        return None

    detailed_v2_configuration_sets: list[dict[str, Any]] | Any = v2_configuration_sets
    if isinstance(v2_configuration_sets, list):
        detailed_v2_configuration_sets = []
        for configuration_set in v2_configuration_sets:
            name = configuration_set_name(configuration_set)
            details = _ses_optional(
                lambda name=name: sesv2.get_configuration_set(ConfigurationSetName=name),
                {'NotFoundException', 'BadRequestException'},
            ) if name else None
            event_destinations = _ses_optional(
                lambda name=name: sesv2.get_configuration_set_event_destinations(
                    ConfigurationSetName=name,
                ).get('EventDestinations', []),
                {'NotFoundException', 'BadRequestException'},
            ) if name else []
            sending_options = details.get('SendingOptions') if isinstance(details, dict) else None
            sending_enabled = None
            if isinstance(sending_options, dict):
                sending_enabled = sending_options.get('SendingEnabled')
            elif isinstance(details, dict) and 'SendingEnabled' in details:
                sending_enabled = details.get('SendingEnabled')
            detailed_v2_configuration_sets.append({
                'name': name,
                'details': details,
                'sending_enabled': sending_enabled,
                'sending_options': sending_options,
                'delivery_options': details.get('DeliveryOptions') if isinstance(details, dict) else None,
                'reputation_options': details.get('ReputationOptions') if isinstance(details, dict) else None,
                'suppression_options': details.get('SuppressionOptions') if isinstance(details, dict) else None,
                'tracking_options': details.get('TrackingOptions') if isinstance(details, dict) else None,
                'event_destinations': event_destinations,
                'event_destination_count': len(event_destinations) if isinstance(event_destinations, list) else 0,
            })

    detailed_v2_contact_lists: list[dict[str, Any]] | Any = v2_contact_lists
    if isinstance(v2_contact_lists, list):
        detailed_v2_contact_lists = []
        for contact_list in v2_contact_lists:
            name = contact_list.get('ContactListName')
            details = _ses_optional(
                lambda name=name: sesv2.get_contact_list(ContactListName=name),
                {'NotFoundException', 'BadRequestException'},
            ) if name and 'GetContactList' in sesv2_operations else None
            contacts = _ses_optional(
                lambda name=name: core._operation_items(sesv2, 'list_contacts', 'Contacts', ContactListName=name),
                {'NotFoundException', 'BadRequestException'},
            ) if name and 'ListContacts' in sesv2_operations else []
            detailed_v2_contact_lists.append({
                'name': name,
                'description': contact_list.get('Description') or (
                    details.get('Description') if isinstance(details, dict) else None
                ),
                'created': contact_list.get('CreatedTimestamp') or (
                    details.get('CreatedTimestamp') if isinstance(details, dict) else None
                ),
                'updated': contact_list.get('LastUpdatedTimestamp') or (
                    details.get('LastUpdatedTimestamp') if isinstance(details, dict) else None
                ),
                'topics': details.get('Topics') if isinstance(details, dict) else None,
                'contacts': contacts,
                'contact_count': len(contacts) if isinstance(contacts, list) else 0,
                'details': details,
            })

    return {
        'summary': {
            'identities': len(detailed_identities),
            'verified_email_addresses': len(verified_emails),
            'templates': len(detailed_templates),
            'captured_messages': len(messages),
            'v2_identities': len(v2_identities) if isinstance(v2_identities, list) else 0,
            'v2_templates': len(v2_templates) if isinstance(v2_templates, list) else 0,
            'v2_configuration_sets': (
                len(detailed_v2_configuration_sets) if isinstance(detailed_v2_configuration_sets, list) else 0
            ),
            'v2_contact_lists': (
                len(detailed_v2_contact_lists) if isinstance(detailed_v2_contact_lists, list) else 0
            ),
            'v2_contacts': (
                sum(item.get('contact_count') or 0 for item in detailed_v2_contact_lists)
                if isinstance(detailed_v2_contact_lists, list) else 0
            ),
            'v2_event_destinations': (
                sum(item.get('event_destination_count') or 0 for item in detailed_v2_configuration_sets)
                if isinstance(detailed_v2_configuration_sets, list) else 0
            ),
        },
        'identities': detailed_identities,
        'verified_email_addresses': _string_items(verified_emails, 'email'),
        'templates': detailed_templates,
        'send_quota': _ses_optional(lambda: ses.get_send_quota(), set()),
        'send_statistics': _ses_optional(lambda: ses.get_send_statistics().get('SendDataPoints', []), set()),
        'account': _ses_optional(lambda: sesv2.get_account(), set()) if 'GetAccount' in sesv2_operations else None,
        'account_sending_enabled': _ses_optional(lambda: ses.get_account_sending_enabled(), set()),
        'mailbox': {
            'url': f'{factory.endpoint_url.rstrip("/")}/_aws/ses',
            'messages': messages,
            'raw': mailbox if isinstance(mailbox, dict) and mailbox.get('error') else None,
        },
        'v2_identities': v2_identities,
        'v2_templates': v2_templates,
        'v2_configuration_sets': detailed_v2_configuration_sets,
        'v2_contact_lists': detailed_v2_contact_lists,
        'supported_v1': [
            'VerifyEmailIdentity',
            'VerifyEmailAddress',
            'VerifyDomainIdentity',
            'DeleteIdentity',
            'ListIdentities',
            'GetIdentityVerificationAttributes',
            'SendEmail',
            'SendRawEmail',
            'SendTemplatedEmail',
            'CreateTemplate',
            'GetTemplate',
            'UpdateTemplate',
            'DeleteTemplate',
            'ListTemplates',
            'GetSendQuota',
            'GetSendStatistics',
            'GetAccountSendingEnabled',
            'ListVerifiedEmailAddresses',
            'DeleteVerifiedEmailAddress',
            'SetIdentityNotificationTopic',
            'GetIdentityNotificationAttributes',
            'GetIdentityDkimAttributes',
        ],
        'supported_v2': [
            'CreateEmailIdentity',
            'ListEmailIdentities',
            'GetEmailIdentity',
            'DeleteEmailIdentity',
            'PutEmailIdentityDkimAttributes',
            'PutEmailIdentityFeedbackAttributes',
            'SendEmail',
            'GetAccount',
            'PutAccountSendingAttributes',
            'PutAccountSuppressionAttributes',
            'CreateEmailTemplate',
            'ListEmailTemplates',
            'GetEmailTemplate',
            'UpdateEmailTemplate',
            'DeleteEmailTemplate',
            'CreateConfigurationSet',
            'ListConfigurationSets',
            'GetConfigurationSet',
            'DeleteConfigurationSet',
            'CreateConfigurationSetEventDestination',
            'GetConfigurationSetEventDestinations',
            'UpdateConfigurationSetEventDestination',
            'DeleteConfigurationSetEventDestination',
            'CreateContactList',
            'ListContactLists',
            'GetContactList',
            'DeleteContactList',
            'CreateContact',
            'ListContacts',
            'GetContact',
            'UpdateContact',
            'DeleteContact',
        ],
        'inspection_endpoints': [
            'GET /_aws/ses',
            'GET /_aws/ses?id=<message-id>',
            'DELETE /_aws/ses',
        ],
        'configuration': {
            'smtp_host': None,
            'smtp_port': 25,
            'smtp_starttls': 'DISABLED',
        },
        'environment_variables': [
            'FLOCI_SERVICES_SES_ENABLED',
            'FLOCI_SERVICES_SES_SMTP_HOST',
            'FLOCI_SERVICES_SES_SMTP_PORT',
            'FLOCI_SERVICES_SES_SMTP_USER',
            'FLOCI_SERVICES_SES_SMTP_PASS',
            'FLOCI_SERVICES_SES_SMTP_STARTTLS',
        ],
        'notes': [
            'Identity verification succeeds immediately in Floci.',
            'Emails are always stored locally and can be inspected through /_aws/ses.',
            'SMTP relay failures are logged but do not affect the SES API response.',
            'SES v1 and SES v2 share identity, template, and sent-message state.',
            'Receipt rule sets and rules are stored, but Floci receives no inbound mail, so their actions never run.',
        ],
    }


def list_ses_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    ses = factory.client('ses')
    resources = [
        {'type': 'identity', 'name': identity}
        for identity in _safe_value(lambda: core._paginate(ses, 'list_identities', 'Identities'), [])
    ]
    resources.extend(
        {
            'type': 'template',
            'name': template.get('Name'),
        }
        for template in _safe_value(lambda: core._paginate(ses, 'list_templates', 'TemplatesMetadata'), [])
    )
    mailbox = _ses_mailbox(factory)
    if isinstance(mailbox, list):
        resources.extend(
            {
                'type': 'captured_message',
                'id': message.get('id') or message.get('Id') or message.get('messageId') or message.get('MessageId'),
            }
            for message in mailbox
            if isinstance(message, dict)
        )
    elif isinstance(mailbox, dict):
        messages = mailbox.get('messages') or mailbox.get('Messages') or mailbox.get('items') or mailbox.get('Items') or []
        if isinstance(messages, list):
            resources.extend(
                {
                    'type': 'captured_message',
                    'id': message.get('id') or message.get('Id') or message.get('messageId') or message.get('MessageId'),
                }
                for message in messages
                if isinstance(message, dict)
            )
    return resources
