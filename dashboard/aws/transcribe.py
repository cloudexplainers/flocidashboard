"""Transcribe inventory and resource-list loaders."""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _clean_response, _safe_value


def transcribe_inventory() -> dict[str, Any]:
    factory = core.FlociClientFactory()
    transcribe = factory.client('transcribe')
    operations = set(transcribe.meta.service_model.operation_names)

    transcription_jobs = _safe_value(
        lambda: core._paginate(transcribe, 'list_transcription_jobs', 'TranscriptionJobSummaries'),
        [],
    ) if 'ListTranscriptionJobs' in operations else []
    medical_transcription_jobs = _safe_value(
        lambda: core._paginate(transcribe, 'list_medical_transcription_jobs', 'MedicalTranscriptionJobSummaries'),
        [],
    ) if 'ListMedicalTranscriptionJobs' in operations else []
    vocabularies = _safe_value(
        lambda: core._paginate(transcribe, 'list_vocabularies', 'Vocabularies'),
        [],
    ) if 'ListVocabularies' in operations else []
    medical_vocabularies = _safe_value(
        lambda: core._paginate(transcribe, 'list_medical_vocabularies', 'Vocabularies'),
        [],
    ) if 'ListMedicalVocabularies' in operations else []
    vocabulary_filters = _safe_value(
        lambda: core._paginate(transcribe, 'list_vocabulary_filters', 'VocabularyFilters'),
        [],
    ) if 'ListVocabularyFilters' in operations else []
    language_models = _safe_value(
        lambda: core._paginate(transcribe, 'list_language_models', 'Models'),
        [],
    ) if 'ListLanguageModels' in operations else []
    call_analytics_jobs = _safe_value(
        lambda: core._paginate(transcribe, 'list_call_analytics_jobs', 'CallAnalyticsJobSummaries'),
        [],
    ) if 'ListCallAnalyticsJobs' in operations else []
    call_analytics_categories = _safe_value(
        lambda: core._paginate(transcribe, 'list_call_analytics_categories', 'Categories'),
        [],
    ) if 'ListCallAnalyticsCategories' in operations else []

    return {
        'summary': {
            'transcription_jobs': len(transcription_jobs),
            'medical_jobs': len(medical_transcription_jobs),
            'vocabularies': len(vocabularies),
            'vocabulary_filters': len(vocabulary_filters),
            'language_models': len(language_models),
            'call_analytics_jobs': len(call_analytics_jobs),
            'available_sdk_operations': len(operations),
        },
        'transcription_jobs': [
            {'name': item.get('TranscriptionJobName'), **_clean_response(item)}
            for item in transcription_jobs
        ],
        'medical_transcription_jobs': [
            {'name': item.get('MedicalTranscriptionJobName'), **_clean_response(item)}
            for item in medical_transcription_jobs
        ],
        'vocabularies': [
            {'name': item.get('VocabularyName'), **_clean_response(item)}
            for item in vocabularies
        ],
        'medical_vocabularies': [
            {'name': item.get('VocabularyName'), **_clean_response(item)}
            for item in medical_vocabularies
        ],
        'vocabulary_filters': [
            {'name': item.get('VocabularyFilterName'), **_clean_response(item)}
            for item in vocabulary_filters
        ],
        'language_models': [
            {'name': item.get('ModelName'), **_clean_response(item)}
            for item in language_models
        ],
        'call_analytics_jobs': [
            {'name': item.get('CallAnalyticsJobName'), **_clean_response(item)}
            for item in call_analytics_jobs
        ],
        'call_analytics_categories': [
            {'name': item.get('CategoryName'), **_clean_response(item)}
            for item in call_analytics_categories
        ],
        'supported_from_sdk': [
            operation
            for operation in [
                'StartTranscriptionJob',
                'GetTranscriptionJob',
                'ListTranscriptionJobs',
                'DeleteTranscriptionJob',
                'CreateVocabulary',
                'GetVocabulary',
                'ListVocabularies',
                'DeleteVocabulary',
            ]
            if operation in operations
        ],
        'available_sdk_operations': sorted(operations),
        'notes': [
            'Floci Transcribe is a control-plane stub: jobs transition to COMPLETED immediately and vocabularies transition to READY immediately.',
            'StartTranscriptionJob accepts a media URI but performs no audio parsing or speech-to-text processing.',
            'LanguageCode defaults to en-US and MediaFormat defaults to mp4 when omitted.',
            'Floci 1.5.29 persists vocabularies across restart; transcription job execution remains stubbed.',
            'Streaming, medical transcription, call analytics, and specialized Transcribe APIs are out of scope.',
        ],
    }


def list_transcribe_resources(factory: core.FlociClientFactory) -> list[dict[str, Any]]:
    transcribe = factory.client('transcribe')
    operations = set(transcribe.meta.service_model.operation_names)
    resources: list[dict[str, Any]] = []

    if 'ListTranscriptionJobs' in operations:
        resources.extend(
            {
                'type': 'transcription_job',
                'name': job.get('TranscriptionJobName'),
                'status': job.get('TranscriptionJobStatus'),
                'language_code': job.get('LanguageCode'),
            }
            for job in _safe_value(
                lambda: core._paginate(transcribe, 'list_transcription_jobs', 'TranscriptionJobSummaries'),
                [],
            )
        )

    if 'ListVocabularies' in operations:
        resources.extend(
            {
                'type': 'vocabulary',
                'name': vocabulary.get('VocabularyName'),
                'state': vocabulary.get('VocabularyState'),
                'language_code': vocabulary.get('LanguageCode'),
            }
            for vocabulary in _safe_value(lambda: core._paginate(transcribe, 'list_vocabularies', 'Vocabularies'), [])
        )

    if 'ListVocabularyFilters' in operations:
        resources.extend(
            {
                'type': 'vocabulary_filter',
                'name': vocabulary_filter.get('VocabularyFilterName'),
                'language_code': vocabulary_filter.get('LanguageCode'),
            }
            for vocabulary_filter in _safe_value(
                lambda: core._paginate(transcribe, 'list_vocabulary_filters', 'VocabularyFilters'),
                [],
            )
        )

    return resources
