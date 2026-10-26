"""Inventories for the stateless AI services: Comprehend, Rekognition, and Translate.

None of them keeps resources in Floci, so each page describes the calls it can make
and what Floci returns for them.
"""

from __future__ import annotations

from typing import Any

from dashboard import aws as core
from dashboard.aws import _safe_value


def _operations(items: list[tuple[str, str, str]]) -> list[dict[str, str]]:
    return [{'name': name, 'status': status, 'notes': notes} for name, status, notes in items]


def comprehend_inventory() -> dict[str, Any]:
    operations = _operations([
        ('DetectSentiment', 'stub', 'Always NEUTRAL (Positive 0.1, Negative 0.1, Neutral 0.7, Mixed 0.1).'),
        ('DetectKeyPhrases', 'stub', 'Always one stub phrase.'),
        ('DetectDominantLanguage', 'stub', 'Always en with score 0.99.'),
        ('DetectPiiEntities', 'stub', 'Always finds no PII. Only en and es are accepted, as on AWS.'),
        ('ContainsPiiEntities', 'stub', 'Always reports no PII labels.'),
        ('DetectEntities, DetectSyntax, custom classifiers and recognizers', 'unsupported', 'Not emulated.'),
    ])
    return {
        'summary': {'stub_operations': 5, 'unsupported_operations': 1, 'stored_resources': 0},
        'operations': operations,
        'notes': [
            'Floci Comprehend validates Text and LanguageCode but performs no text analysis: every call returns a fixed, AWS-shaped answer.',
            'Floci can return chosen answers from an AI_MOCK_CONFIG JSON file keyed by the exact text. The dashboard does not mount one.',
        ],
    }


def rekognition_inventory() -> dict[str, Any]:
    operations = _operations([
        ('DetectLabels', 'stub', 'Always one label named Floci.'),
        ('DetectText', 'stub', 'Always one Floci text detection.'),
        ('DetectModerationLabels', 'stub', 'Always finds nothing.'),
        ('DetectFaces', 'stub', 'Always finds no faces.'),
        ('CompareFaces', 'stub', 'Always finds no matches.'),
        ('Collections, face search, custom labels, and video', 'unsupported', 'Not emulated.'),
    ])
    return {
        'summary': {'stub_operations': 5, 'unsupported_operations': 1, 'stored_resources': 0},
        'operations': operations,
        'notes': [
            'Floci Rekognition checks images as AWS does (JPEG or PNG only, at most 5 MB as bytes, S3 objects must exist) but never analyzes what an image shows.',
            'Floci can return chosen answers from an AI_MOCK_CONFIG JSON file. The dashboard does not mount one.',
        ],
    }


def translate_inventory() -> dict[str, Any]:
    translate = core.FlociClientFactory().client('translate')
    languages = _safe_value(lambda: translate.list_languages().get('Languages', []), [])
    return {
        'summary': {'languages': len(languages), 'stored_resources': 0},
        'languages': [{'name': item.get('LanguageName'), 'code': item.get('LanguageCode')} for item in languages],
        'operations': _operations([
            ('TranslateText', 'echo', 'Returns the input text unchanged. Unknown language codes are refused, and text over 10,000 bytes is refused.'),
            ('TranslateDocument', 'echo', 'Returns the document unchanged. One side of the pair must be English.'),
            ('ListLanguages', 'supported', 'A fixed list of about 35 languages.'),
            ('Custom terminology, parallel data, and batch jobs', 'unsupported', 'Not emulated.'),
        ]),
        'notes': [
            'Floci Translate performs no machine translation. A source language of auto is reported back as en.',
        ],
    }
