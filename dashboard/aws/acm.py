"""ACM inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _acm_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def acm_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    acm = factory.client('acm')
    certificates = _safe_value(lambda: core._paginate(acm, 'list_certificates', 'CertificateSummaryList'), [])

    def certificate_detail(summary: dict[str, Any]) -> dict[str, Any]:
        arn = summary.get('CertificateArn')
        details = _acm_optional(
            lambda: acm.describe_certificate(CertificateArn=arn).get('Certificate', {}),
            {'ResourceNotFoundException'},
        )
        cert_pem = _acm_optional(
            lambda: acm.get_certificate(CertificateArn=arn),
            {'ResourceNotFoundException', 'RequestInProgressException'},
        )
        described = details if isinstance(details, dict) and not details.get('error') else {}
        pem = cert_pem if isinstance(cert_pem, dict) and not cert_pem.get('error') else {}

        return {
            'name': summary.get('DomainName') or described.get('DomainName') or arn,
            'arn': arn,
            'domain_name': summary.get('DomainName') or described.get('DomainName'),
            'status': summary.get('Status') or described.get('Status'),
            'type': summary.get('Type') or described.get('Type'),
            'key_algorithm': summary.get('KeyAlgorithm') or described.get('KeyAlgorithm'),
            'created': described.get('CreatedAt'),
            'issued_at': described.get('IssuedAt'),
            'not_before': described.get('NotBefore'),
            'not_after': described.get('NotAfter'),
            'renewal_eligibility': described.get('RenewalEligibility'),
            'subject_alternative_names': described.get('SubjectAlternativeNames'),
            'domain_validation_options': described.get('DomainValidationOptions'),
            'in_use_by': described.get('InUseBy'),
            'signature_algorithm': described.get('SignatureAlgorithm'),
            'has_certificate_pem': bool(pem.get('Certificate')),
            'has_certificate_chain': bool(pem.get('CertificateChain')),
            'tags': _acm_optional(
                lambda: acm.list_tags_for_certificate(CertificateArn=arn).get('Tags', []),
                {'ResourceNotFoundException'},
            ),
            'details': details if isinstance(details, dict) and details.get('error') else None,
            'certificate_error': cert_pem if isinstance(cert_pem, dict) and cert_pem.get('error') else None,
        }

    detailed_certificates = [certificate_detail(certificate) for certificate in certificates]
    account_configuration = _acm_optional(
        lambda: acm.get_account_configuration(),
        {'AccessDeniedException'},
    )

    return {
        'summary': {
            'certificates': len(detailed_certificates),
            'issued': sum(1 for certificate in detailed_certificates if certificate.get('status') == 'ISSUED'),
            'amazon_issued': sum(1 for certificate in detailed_certificates if certificate.get('type') == 'AMAZON_ISSUED'),
            'private': sum(1 for certificate in detailed_certificates if certificate.get('type') == 'PRIVATE'),
        },
        'certificates': detailed_certificates,
        'account_configuration': account_configuration,
        'supported': [
            'RequestCertificate',
            'DescribeCertificate',
            'GetCertificate',
            'ListCertificates',
            'DeleteCertificate',
            'AddTagsToCertificate',
            'RemoveTagsFromCertificate',
            'ListTagsForCertificate',
            'ExportCertificate',
            'GetAccountConfiguration',
            'PutAccountConfiguration',
            'RenewCertificate',
        ],
        'key_algorithms': [
            'RSA_2048',
            'RSA_3072',
            'RSA_4096',
            'EC_prime256v1',
            'EC_secp384r1',
            'EC_secp521r1',
        ],
        'certificate_types': [
            'AMAZON_ISSUED',
            'PRIVATE',
        ],
        'notes': [
            'Floci issues requested certificates immediately, with no Pending validation state, unless FLOCI_SERVICES_ACM_VALIDATION_WAIT_SECONDS is set above 0. In AWS, DNS or email validation must complete first.',
            'Certificates use real RSA or EC keys and valid X.509 structure.',
            'Only PRIVATE certificates can be exported with an encrypted private key.',
            'Floci 1.5.27 restores ACM certificates after restart by ignoring computed getters in persisted state.',
            'The dashboard intentionally shows PEM availability instead of dumping certificate material.',
        ],
    }


def list_acm_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    return [
        {
            'type': 'certificate',
            'arn': certificate.get('CertificateArn'),
            'domain': certificate.get('DomainName'),
            'status': certificate.get('Status'),
            'key_algorithm': certificate.get('KeyAlgorithm'),
        }
        for certificate in _safe_value(lambda: core._paginate(factory.client('acm'), 'list_certificates', 'CertificateSummaryList'), [])
    ]
