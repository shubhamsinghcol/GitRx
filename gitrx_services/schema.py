"""Small strict validator for the checked-in schema vocabulary, stdlib only."""
from datetime import date
import re
import math
from urllib.parse import urlsplit
from uuid import UUID

def errors(value, schema, path='$'):
    out = []
    kind = schema.get('type')
    types = {'object': lambda x: isinstance(x, dict), 'array': lambda x: isinstance(x, list),
             'string': lambda x: isinstance(x, str), 'integer': lambda x: type(x) is int,
             'number': lambda x: type(x) is int or (type(x) is float and math.isfinite(x)),
             'boolean': lambda x: type(x) is bool, 'null': lambda x: x is None}
    if kind and (kind not in types or not types[kind](value)):
        return [path + ': expected ' + kind]
    if 'enum' in schema and value not in schema['enum']:
        out.append(path + ': value outside enum')
    if 'const' in schema and (type(value) is not type(schema['const']) or value != schema['const']):
        out.append(path + ': constant mismatch')
    if isinstance(value, dict):
        props = schema.get('properties', {})
        out += [path + '.' + k + ': required' for k in schema.get('required', []) if k not in value]
        if schema.get('additionalProperties') is False:
            out += [path + '.' + k + ': unknown field' for k in value.keys() - props.keys()]
        for k in value.keys() & props.keys():
            out += errors(value[k], props[k], path + '.' + k)
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', float('inf')):
            out.append(path + ': array size')
        if schema.get('uniqueItems') and any(x in value[:i] for i, x in enumerate(value)):
            out.append(path + ': duplicate items')
        for i, x in enumerate(value):
            out += errors(x, schema.get('items', {}), f'{path}[{i}]')
    if isinstance(value, str):
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', float('inf')):
            out.append(path + ': string size')
        if schema.get('pattern') and not re.search(schema['pattern'], value):
            out.append(path + ': pattern mismatch')
        try:
            if schema.get('format') == 'date':
                if date.fromisoformat(value).isoformat() != value: raise ValueError()
            if schema.get('format') == 'uuid':
                if str(UUID(value)) != value: raise ValueError()
            if schema.get('format') == 'uri':
                u = urlsplit(value)
                if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password or any(c.isspace() for c in value): raise ValueError()
                _ = u.port
        except ValueError:
            out.append(path + ': invalid ' + schema.get('format', 'format'))
    if type(value) in (int,float):
        if value < schema.get('minimum', value) or value > schema.get('maximum', value):
            out.append(path + ': numeric bounds')
    for child in schema.get('allOf', []):
        out += errors(value, child, path)
    if 'if' in schema and not errors(value, schema['if'], path):
        out += errors(value, schema.get('then', {}), path)
    return out
