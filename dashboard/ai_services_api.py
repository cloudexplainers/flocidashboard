"""Interactive helpers for Comprehend, Rekognition, and Translate.

Floci returns fixed answers for these services; the helpers still send real,
AWS-shaped requests so the forms show what each call needs.
"""

from __future__ import annotations

import base64
from typing import Any

from .aws import FlociClientFactory, _clean_response


def _client(name: str):
    return FlociClientFactory().client(name)


def _required(value: Any, label: str) -> str:
    cleaned = str(value or '').strip()
    if not cleaned:
        raise ValueError(f'{label} is required')
    return cleaned


COMPREHEND_OPERATIONS = {
    'detect_sentiment': True,
    'detect_key_phrases': True,
    'detect_dominant_language': False,
    'detect_pii_entities': True,
    'contains_pii_entities': True,
}


def comprehend_detect(operation: str, text: str, language_code: str = 'en') -> dict[str, Any]:
    if operation not in COMPREHEND_OPERATIONS:
        raise ValueError(f'Unknown Comprehend operation: {operation}')
    kwargs: dict[str, Any] = {'Text': _required(text, 'Text')}
    if COMPREHEND_OPERATIONS[operation]:
        kwargs['LanguageCode'] = language_code or 'en'
    response = getattr(_client('comprehend'), operation)(**kwargs)
    return {'operation': operation, 'result': _clean_response(response)}


def _image(bucket: str = '', key: str = '', image_base64: str = '') -> dict[str, Any]:
    if (image_base64 or '').strip():
        try:
            return {'Bytes': base64.b64decode(image_base64.strip(), validate=False)}
        except ValueError as exc:
            raise ValueError('Image bytes must be base64') from exc
    return {'S3Object': {'Bucket': _required(bucket, 'Bucket'), 'Name': _required(key, 'Object key')}}


REKOGNITION_OPERATIONS = ('detect_labels', 'detect_moderation_labels', 'detect_text', 'detect_faces')


def rekognition_detect(operation: str, *, bucket: str = '', key: str = '', image_base64: str = '') -> dict[str, Any]:
    if operation not in REKOGNITION_OPERATIONS:
        raise ValueError(f'Unknown Rekognition operation: {operation}')
    response = getattr(_client('rekognition'), operation)(Image=_image(bucket, key, image_base64))
    return {'operation': operation, 'result': _clean_response(response)}


def rekognition_compare_faces(*, source_bucket: str, source_key: str, target_bucket: str, target_key: str,
                              similarity_threshold: float = 80) -> dict[str, Any]:
    response = _client('rekognition').compare_faces(
        SourceImage=_image(source_bucket, source_key),
        TargetImage=_image(target_bucket, target_key),
        SimilarityThreshold=float(similarity_threshold or 80),
    )
    return {'operation': 'compare_faces', 'result': _clean_response(response)}


def translate_text(text: str, *, source_language: str = 'auto', target_language: str) -> dict[str, Any]:
    response = _client('translate').translate_text(
        Text=_required(text, 'Text'),
        SourceLanguageCode=source_language or 'auto',
        TargetLanguageCode=_required(target_language, 'Target language'),
    )
    return {
        'translated_text': response.get('TranslatedText'),
        'source_language': response.get('SourceLanguageCode'),
        'target_language': response.get('TargetLanguageCode'),
        'response': _clean_response(response),
    }
