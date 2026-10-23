"""Interactive Transcribe helpers for local control-plane workflows."""

from __future__ import annotations

from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client():
    return FlociClientFactory().client('transcribe')


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


def _list_value(value: Any, label: str) -> list[Any]:
    if value in (None, ''):
        return []
    if not isinstance(value, list):
        raise ValueError(f'{label} must be a JSON array')
    return value


def start_transcription_job(
    name: str,
    *,
    media_uri: str,
    language_code: str = 'en-US',
    media_format: str = '',
    options: Any = None,
) -> dict[str, Any]:
    clean_name = _required(name, 'Transcription job name')
    payload = {
        'TranscriptionJobName': clean_name,
        'Media': {'MediaFileUri': _required(media_uri, 'Media URI')},
        'LanguageCode': language_code or 'en-US',
    }
    if (media_format or '').strip():
        payload['MediaFormat'] = media_format.strip()
    if options not in (None, ''):
        if not isinstance(options, dict):
            raise ValueError('Job options must be a JSON object')
        payload.update(options)
    response = _client().start_transcription_job(**payload)
    return {'job_name': clean_name, 'job': _clean_response(response.get('TranscriptionJob', {})), 'response': _clean_response(response)}


def get_transcription_job(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Transcription job name')
    response = _client().get_transcription_job(TranscriptionJobName=clean_name)
    return {'job_name': clean_name, 'job': _clean_response(response.get('TranscriptionJob', {})), 'response': _clean_response(response)}


def delete_transcription_job(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Transcription job name')
    response = _client().delete_transcription_job(TranscriptionJobName=clean_name)
    return {'job_name': clean_name, 'response': _clean_response(response)}


def create_vocabulary(name: str, *, language_code: str = 'en-US', phrases: Any = None, vocabulary_file_uri: str = '') -> dict[str, Any]:
    clean_name = _required(name, 'Vocabulary name')
    payload: dict[str, Any] = {'VocabularyName': clean_name, 'LanguageCode': language_code or 'en-US'}
    clean_phrases = [str(phrase) for phrase in _list_value(phrases, 'Phrases')]
    if clean_phrases:
        payload['Phrases'] = clean_phrases
    if vocabulary_file_uri:
        payload['VocabularyFileUri'] = vocabulary_file_uri
    response = _client().create_vocabulary(**payload)
    return {'vocabulary_name': clean_name, 'vocabulary_state': response.get('VocabularyState'), 'response': _clean_response(response)}


def get_vocabulary(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Vocabulary name')
    response = _client().get_vocabulary(VocabularyName=clean_name)
    return {'vocabulary_name': clean_name, 'vocabulary': _clean_response(response), 'response': _clean_response(response)}


def delete_vocabulary(name: str) -> dict[str, Any]:
    clean_name = _required(name, 'Vocabulary name')
    response = _client().delete_vocabulary(VocabularyName=clean_name)
    return {'vocabulary_name': clean_name, 'response': _clean_response(response)}
