"""Textract inventory and resource-list loaders."""

from __future__ import annotations

import json
from typing import Any, Callable

from botocore import xform_name
from botocore.exceptions import BotoCoreError, ClientError

from dashboard import aws as core
from dashboard.aws import _clean_response, _error_code, _safe_value


def _textract_optional(loader: Callable[[], Any], empty_codes: set[str]) -> Any:
    try:
        return _clean_response(loader())
    except ClientError as exc:
        if _error_code(exc) in empty_codes:
            return None
        return {'error': str(exc)}
    except (AttributeError, BotoCoreError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return {'error': str(exc)}


def textract_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    textract = factory.client('textract')
    operations = set(textract.meta.service_model.operation_names)

    def paginate_if_supported(operation_name: str, result_key: str, **kwargs) -> list[Any]:
        if operation_name not in operations:
            return []

        return _safe_value(lambda: core._paginate(textract, xform_name(operation_name), result_key, **kwargs), [])

    adapters = paginate_if_supported('ListAdapters', 'Adapters')

    def adapter_detail(adapter: dict[str, Any]) -> dict[str, Any]:
        adapter_id = adapter.get('AdapterId')
        details = _textract_optional(
            lambda: textract.get_adapter(AdapterId=adapter_id),
            {'ResourceNotFoundException', 'InvalidParameterException'},
        ) if adapter_id and 'GetAdapter' in operations else None
        versions = paginate_if_supported('ListAdapterVersions', 'AdapterVersions', AdapterId=adapter_id) if adapter_id else []

        version_details = []
        if adapter_id and 'GetAdapterVersion' in operations:
            for version in versions:
                adapter_version = version.get('AdapterVersion')
                if adapter_version:
                    version_details.append(_textract_optional(
                        lambda adapter_version=adapter_version: textract.get_adapter_version(
                            AdapterId=adapter_id,
                            AdapterVersion=adapter_version,
                        ),
                        {'ResourceNotFoundException', 'InvalidParameterException'},
                    ))

        described = details if isinstance(details, dict) and not details.get('error') else {}
        arn = adapter.get('AdapterArn') or described.get('AdapterArn')

        return {
            'name': adapter.get('AdapterName') or described.get('AdapterName') or adapter_id,
            'adapter_id': adapter_id,
            'arn': arn,
            'description': adapter.get('Description') or described.get('Description'),
            'feature_types': adapter.get('FeatureTypes') or described.get('FeatureTypes'),
            'auto_update': described.get('AutoUpdate'),
            'created': adapter.get('CreationTime') or described.get('CreationTime'),
            'version_count': len(versions),
            'versions': versions,
            'version_details': version_details,
            'tags': described.get('Tags') or (
                _textract_optional(
                    lambda arn=arn: textract.list_tags_for_resource(ResourceARN=arn).get('Tags', {}),
                    {'ResourceNotFoundException', 'InvalidParameterException'},
                ) if arn and 'ListTagsForResource' in operations else None
            ),
            'details': details if isinstance(details, dict) and details.get('error') else None,
        }

    detailed_adapters = [adapter_detail(adapter) for adapter in adapters]
    adapter_versions = [
        version
        for adapter in detailed_adapters
        for version in adapter.get('versions', [])
    ]

    supported_operations = [
        'AnalyzeDocument',
        'DetectDocumentText',
        'StartDocumentAnalysis',
        'StartDocumentTextDetection',
        'GetDocumentAnalysis',
        'GetDocumentTextDetection',
    ]

    return {
        'summary': {
            'adapters': len(detailed_adapters),
            'adapter_versions': len(adapter_versions),
            'available_sdk_operations': len(operations),
            'supported_operations': len([operation for operation in supported_operations if operation in operations]),
        },
        'adapters': detailed_adapters,
        'adapter_versions': adapter_versions,
        'supported_from_sdk': supported_operations,
        'available_sdk_operations': sorted(operations),
        'document_operations': [
            'AnalyzeDocument',
            'DetectDocumentText',
        ],
        'async_job_operations': [
            'StartDocumentAnalysis',
            'GetDocumentAnalysis',
            'StartDocumentTextDetection',
            'GetDocumentTextDetection',
        ],
        'stub_blocks': [
            {'BlockType': 'PAGE', 'Text': None, 'Relationships': 'CHILD -> LINE'},
            {'BlockType': 'LINE', 'Text': 'Floci', 'Relationships': 'CHILD -> WORD'},
            {'BlockType': 'WORD', 'Text': 'Floci', 'Relationships': None},
        ],
        'notes': [
            'Floci Textract returns a fixed PAGE, LINE, and WORD block hierarchy with synthetic geometry and 99.9 confidence.',
            'Document and DocumentLocation inputs are accepted but not parsed, so no real OCR or document analysis is performed.',
            'StartDocumentTextDetection and StartDocumentAnalysis return immediately succeeded in-memory jobs.',
            'Job IDs are not persisted across restarts, and text-detection job IDs cannot be fetched through GetDocumentAnalysis or vice versa.',
            'Adapter management, AnalyzeExpense, AnalyzeID, AnalyzeLendingDocument, and async pagination are out of scope.',
        ],
    }


def list_textract_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    textract = factory.client('textract')
    operations = set(textract.meta.service_model.operation_names)

    if 'ListAdapters' not in operations:
        return []

    adapters = [
        {
            'type': 'adapter',
            'name': adapter.get('AdapterName') or adapter.get('AdapterId'),
            'id': adapter.get('AdapterId'),
            'arn': adapter.get('AdapterArn'),
            'feature_types': adapter.get('FeatureTypes'),
        }
        for adapter in _safe_value(lambda: core._paginate(textract, 'list_adapters', 'Adapters'), [])
    ]
    resources = list(adapters)

    if 'ListAdapterVersions' in operations:
        for adapter in adapters:
            adapter_id = adapter.get('id')
            if not adapter_id:
                continue

            resources.extend(
                {
                    'type': 'adapter_version',
                    'name': f'{adapter.get("name") or adapter_id}:{version.get("AdapterVersion")}',
                    'id': adapter_id,
                    'adapter_version': version.get('AdapterVersion'),
                    'status': version.get('Status'),
                }
                for version in _safe_value(
                    lambda adapter_id=adapter_id: core._paginate(
                        textract,
                        'list_adapter_versions',
                        'AdapterVersions',
                        AdapterId=adapter_id,
                    ),
                    [],
                )
            )

    return resources
