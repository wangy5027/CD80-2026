"""Small, local syntax repairs for the event deep-report response only.

Never complete truncated output or infer missing fields/values. The shared
strict parser and the subsequent report/source validators remain authoritative.
"""

from __future__ import annotations

import json
import re

from .contracts import parse_json_object
from ..workbench.errors import GatewayError


MAX_REPAIRS = 8
_TEXT_VALUE = re.compile(r'"(?:title|summary|heading|body|question|reason)"\s*:\s*$')


def _syntax_error(error):
    # JSONDecodeError.doc stays in memory; no response fragments enter logs.
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, json.JSONDecodeError):
            return error
        error = error.__context__
    return None


def _last_value_string(text, end):
    start = closed_start = None
    escaped = False
    for index, char in enumerate(text[:end + 1]):
        if start is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                closed_start, start = start, None
        elif char == '"':
            start = index
    if start is None and closed_start is not None and _TEXT_VALUE.search(text[:closed_start]):
        return closed_start
    return None


def _repair_at_error(syntax, quote_starts):
    text, pos = syntax.doc, syntax.pos
    if pos >= len(text):
        return None  # Never synthesize closing quotes/brackets for truncation.
    char, message = text[pos], syntax.msg
    if ((char == "，" and message == "Expecting ',' delimiter") or
            (char == "：" and message == "Expecting ':' delimiter")):
        return text[:pos] + {"，": ",", "：": ":"}[char] + text[pos + 1:], "structural_punctuation", pos
    before = pos - 1
    while before >= 0 and text[before].isspace():
        before -= 1
    if (before >= 0 and text[before] == "," and
            ((char == "]" and message == "Expecting value") or
             (char == "}" and message == "Expecting property name enclosed in double quotes"))):
        return text[:before] + text[before + 1:], "trailing_comma", before
    if message != "Expecting ',' delimiter" or before < 0 or text[before] != '"':
        return None
    start = _last_value_string(text, before)
    if start is None:
        return None
    # A CJK word after a premature closing quote cannot be another JSON value.
    # Restrict this repair to narrative object fields, never keys/arrays/IDs.
    cjk_text = "\u3400" <= char <= "\u9fff"
    # Handle an inner quotation ending immediately before the value's closing
    # quote only after repairing an earlier inner quote in this SAME value.
    # Do not merge arbitrary adjacent strings or guess a missing comma.
    quoted_end = (char == '"' and pos == before + 1 and start in quote_starts
                  and text[pos + 1:].lstrip()[:1] in {",", "}", "]"})
    if not cjk_text and not quoted_end:
        return None
    quote_starts.add(start)
    return text[:before] + "\\" + text[before:], "text_quote_escape", before


def parse_event_report_json(content):
    """Return (strictly parsed object, safe repair metadata), without model calls."""
    try:
        return parse_json_object(content), []
    except GatewayError as original:
        if getattr(original, "diagnostic", {}).get("reason") != "syntax":
            raise
        current, repairs, quote_starts = original, [], set()
        for _ in range(MAX_REPAIRS):
            syntax = _syntax_error(current)
            repair = _repair_at_error(syntax, quote_starts) if syntax is not None else None
            if repair is None:
                break
            text, kind, position = repair
            repairs.append({"kind": kind, "position": position, "line": syntax.lineno,
                            "column": syntax.colno, "decoder_message": syntax.msg,
                            "coordinate_system": "normalized_json_before_repair"})
            try:
                return parse_json_object(text), repairs
            except GatewayError as next_error:
                if getattr(next_error, "diagnostic", {}).get("reason") != "syntax":
                    break
                current = next_error
        # Report the original failure location; do not leak repaired fragments or
        # return a partial object when any ambiguity/semantic parser error remains.
        raise original from None
