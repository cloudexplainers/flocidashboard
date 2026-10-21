"""Normalize AWS SDL streams and Floci's JSON-wrapped SDL responses."""

import json
import re


def read_sdl(response):
    stream = response['schema']
    try:
        definition = stream.read().decode('utf-8')
    finally:
        stream.close()
    try:
        wrapped = json.loads(definition)
    except json.JSONDecodeError:
        return definition
    if isinstance(wrapped, dict):
        schema = wrapped.get('schema', wrapped)
        if isinstance(schema, dict) and isinstance(schema.get('definition'), str):
            return schema['definition']
    raise ValueError('Schema response did not contain SDL')


def resolver_type_names(definition):
    # Strip descriptions, quoted directive arguments and comments before finding
    # object/interface definitions. No fields or routing configuration are inferred.
    without_literals = re.sub(r'""".*?"""|"(?:\\.|[^"\\])*"|#[^\n]*', ' ', definition, flags=re.S)
    return re.findall(r'\b(?:type|interface)\s+([_A-Za-z][_0-9A-Za-z]*)', without_literals)
