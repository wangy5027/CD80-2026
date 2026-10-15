"""Detached, credential-redacted model diagnostics; never serialize HTTP objects.

Raw response text remains byte-for-character equivalent unless a sensitive value
is removed. Bounds and SHA-256 refer to the *redacted* canonical JSON, never a
credential-bearing original. This is an owner-visible diagnostic, not telemetry.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import re
from urllib.parse import quote, unquote_plus


MAX_RECORD_CHARS = 1_048_576
REDACTED = '[REDACTED]'
OMITTED = '[OMITTED]'
_MAX_DEPTH = 48
_TEXT_DEPTH = 8
_TRANSPORT_KEYS = {'provider', 'model', 'parameters', 'responseFormat', 'httpStatus',
                   'requestId', 'responseId', 'finishReason', 'usage'}
_LITERAL = re.compile(r'''"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*' ''', re.S | re.X)
_ASSIGNMENT = re.compile(r'''(?<![\w])(?:\\*["'])?(?P<key>[A-Za-z_][\w.-]*)(?:\\*["'])?\s*[:=]\s*''')
_BEARER = re.compile(r'\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', re.I)
_URL = re.compile(r'''(?<![A-Za-z0-9+.-])[A-Za-z][A-Za-z0-9+.-]*://[^\s<>"']+''')


def _key_kind(key):
    # Singular token is a credential; usage.input_tokens / token_count are not.
    words = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', key).lower()
    words = re.split(r'[^a-z0-9]+', words)
    flat = ''.join(words)
    if flat in {'headers', 'httpheaders', 'requestheaders', 'responseheaders', 'reasoningcontent'}:
        return 'omit'
    api_key = (flat.endswith('apikey') or 'apikey' in words
               or any(words[index:index + 2] == ['api', 'key'] for index in range(len(words) - 1)))
    if (any(word in {'password', 'passwd', 'pwd', 'secret', 'authorization', 'cookie', 'credentials'} for word in words)
            or api_key or flat in {'setcookie', 'proxyauthorization'}
            or words[-1:] == ['token']):
        return 'secret'
    return None


def _value_end(text, start, key):
    """Find a whole sensitive value, including containers and broken quotes."""
    if start >= len(text):
        return start
    first = text[start]
    if first in '\"\'':
        escaped = False
        for index in range(start + 1, len(text)):
            char = text[index]
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == first:
                return index + 1
        return len(text)
    if first in '[{':
        stack, quoted, escaped = [first], None, False
        for index in range(start + 1, len(text)):
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == '\\':
                    escaped = True
                elif char == quoted:
                    quoted = None
            elif char in '\"\'':
                quoted = char
            elif char in '[{':
                stack.append(char)
            elif char in ']}':
                stack.pop()
                if not stack:
                    return index + 1
        return len(text)
    if key.lower() in {'authorization', 'proxy-authorization', 'cookie', 'set-cookie'}:
        end = text.find('\n', start)
        return len(text) if end < 0 else end
    match = re.search(r'''[\s,;&}\]<>]''', text[start:])
    return start + match.start() if match else len(text)


def _outside_literals(pattern, replacement, text):
    """Nested literals have already been inspected; never redact them twice."""
    spans = [match.span() for match in _LITERAL.finditer(text)]
    index = 0

    def replace(match):
        nonlocal index
        while index < len(spans) and spans[index][1] <= match.start():
            index += 1
        if index < len(spans) and spans[index][0] <= match.start() and match.end() <= spans[index][1]:
            return match.group()
        return replacement(match)

    return pattern.sub(replace, text)


def _text(text, depth=0):
    # Decode nested JSON/Python string literals only to inspect them. Preserve
    # their exact spelling when no redaction was needed, including malformed JSON.
    def literal(match):
        original = match.group()
        try:
            value = json.loads(original) if original.startswith('"') else ast.literal_eval(original)
        except (ValueError, SyntaxError, RecursionError):
            return original
        if not isinstance(value, str):
            return original
        clean = _text(value, depth + 1) if depth < _TEXT_DEPTH else OMITTED
        if clean == value:
            return original
        return json.dumps(clean, ensure_ascii=False) if original.startswith('"') else repr(clean)

    text = _LITERAL.sub(literal, text)
    text = _outside_literals(_BEARER, lambda match: match[1] + ' ' + REDACTED, text)

    def url(match):
        # Splitting syntactically rather than validating the hostname also
        # redacts malformed URLs (e.g. broken IPv6 brackets) without a bypass.
        scheme, rest = match.group().split('://', 1)
        authority_end = re.search(r'[/\?#]', rest)
        stop = authority_end.start() if authority_end else len(rest)
        netloc, suffix = rest[:stop], rest[stop:]
        if '@' in netloc:
            netloc = quote(REDACTED, safe='') + '@' + netloc.rsplit('@', 1)[1]
        path_query, fragment_separator, fragment = suffix.partition('#')
        path, query_separator, original_query = path_query.partition('?')
        def query_string(original):
            query = []
            for entry in original.split('&'):
                key, separator, value = entry.partition('=')
                if separator and _key_kind(unquote_plus(key)):
                    value = quote(REDACTED, safe='')
                elif separator and depth < _TEXT_DEPTH:
                    decoded = unquote_plus(value)
                    clean = _text(decoded, depth + 1)
                    if clean != decoded:
                        value = quote(clean, safe='')
                query.append(key + separator + value)
            return '&'.join(query)

        return (scheme + '://' + netloc + path + query_separator + query_string(original_query)
                + fragment_separator + query_string(fragment))

    text = _outside_literals(_URL, url, text)
    protected = sorted([match.span() for pattern in (_LITERAL, _URL) for match in pattern.finditer(text)])
    protected_index = 0
    result, cursor = [], 0
    for match in _ASSIGNMENT.finditer(text):
        if match.start() < cursor:
            continue
        while protected_index < len(protected) and protected[protected_index][1] <= match.start():
            protected_index += 1
        if (protected_index < len(protected) and protected[protected_index][0] <= match.start()
                and match.end() <= protected[protected_index][1]):
            continue
        kind = _key_kind(match['key'])
        if not kind:
            continue
        end = _value_end(text, match.end(), match['key'])
        result.append(text[cursor:match.end()])
        result.append(json.dumps(OMITTED if kind == 'omit' else REDACTED))
        cursor = end
    result.append(text[cursor:])
    return ''.join(result)


def redacted(value):
    """Return a detached JSON-safe value, omitting headers and reasoning content.

    Credential fields are identified by complete key words, not substrings such
    as ``input_tokens``. Text is also scanned because messages and raw responses
    can themselves contain JSON, URLs or copied authorization headers. Unknown
    Python objects are labelled by type, never via repr/str (which may leak).
    """
    seen = set()

    def visit(item, depth=0):
        if depth > _MAX_DEPTH:
            return '[OMITTED: depth limit]'
        if item is None or isinstance(item, (bool, int)):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else '[OMITTED: non-finite number]'
        if isinstance(item, str):
            # Lone surrogates can arrive inside parsed JSON; retain their code
            # point spelling without passing invalid Unicode to SQLite/HTTP.
            return _text(item.encode('utf-8', errors='backslashreplace').decode('utf-8'))
        if isinstance(item, (bytes, bytearray)):
            return _text(bytes(item).decode('utf-8', errors='replace'))
        if not isinstance(item, (dict, list, tuple, set, frozenset)):
            return '[OMITTED: ' + type(item).__name__ + ']'
        identity = id(item)
        if identity in seen:
            return '[OMITTED: circular reference]'
        seen.add(identity)
        try:
            if isinstance(item, dict):
                result = {}
                for key, child in item.items():
                    if not isinstance(key, (str, int, float, bool)) and key is not None:
                        continue
                    key = str(key).encode('utf-8', errors='backslashreplace').decode('utf-8')
                    kind = _key_kind(key)
                    if kind != 'omit':
                        result[key] = REDACTED if kind == 'secret' else visit(child, depth + 1)
                return result
            return [visit(child, depth + 1) for child in item]
        finally:
            seen.remove(identity)

    return visit(value)


def _json(value):
    # Match Store's serializer, including its separators, when measuring bounds.
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def bounded_record(value, max_chars=MAX_RECORD_CHARS):
    """Bound the complete returned wrapper, not just its text, to max_chars.

    originalChars/sha256 describe redacted canonical JSON. When truncated, head
    and tail retain exact fragments of that JSON, with zero-based character
    offsets. Consumers must not mistake these fragments for complete JSON.
    """
    if type(max_chars) is not int or max_chars < 512:
        raise ValueError('max_chars must be an integer of at least 512')
    safe = redacted(value)
    serialized = _json(safe)
    record = {'value': safe, 'originalChars': len(serialized),
              'sha256': hashlib.sha256(serialized.encode('utf-8', errors='surrogatepass')).hexdigest(),
              'hashScope': 'redacted-json', 'truncated': False}
    if len(_json(record)) <= max_chars:
        return record
    record['truncated'] = True

    def fragments(count):
        head_count, tail_count = (count + 1) // 2, count // 2
        return {'head': serialized[:head_count], 'headStart': 0,
                'tail': serialized[len(serialized) - tail_count:] if tail_count else '',
                'tailStart': len(serialized) - tail_count}

    low, high = 0, min(len(serialized), max_chars)
    while low < high:
        count = (low + high + 1) // 2
        record['value'] = fragments(count)
        if len(_json(record)) <= max_chars:
            low = count
        else:
            high = count - 1
    record['value'] = fragments(low)
    return record


class ModelTrace:
    """Four fixed callback slots; transport metadata merges, bodies replace."""

    def __init__(self):
        self._values = dict.fromkeys(('request', 'transport', 'response', 'parsed'))

    def receive(self, event, value):
        if event not in self._values:
            return
        if event == 'transport':
            value = {key: child for key, child in value.items() if key in _TRANSPORT_KEYS} if isinstance(value, dict) else {}
        safe = redacted(value)
        if event == 'transport' and isinstance(safe, dict) and isinstance(self._values[event], dict):
            self._values[event].update(safe)
        else:
            self._values[event] = safe

    def record(self):
        return copy.deepcopy(self._values)
