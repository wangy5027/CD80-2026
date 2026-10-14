"""Normalize optional event-report fields without repairing the core answer.

The shared report validator remains responsible for title, summary, findings,
sections, recommendations, and all core source references. This adapter only
lets malformed presentation aids fall back to their empty values.
"""

from __future__ import annotations

from copy import deepcopy

from ..workbench.errors import GatewayError
from .contracts import _event_overview, _refs, _text_list, optional_text


_CORE = ("title", "summary", "key_findings", "sections", "recommendations")
_LIST_FIELDS = {"uncertainties": 8, "limitations": 8, "actors": 16,
                "drivers": 8, "impact_paths": 8}
_AUXILIARY = (*_LIST_FIELDS, "timeline", "event_overview", "background")
_CONDITION_FIELDS = {"uncertainties": "不确定性", "limitations": "信息缺口"}


def _safe_copy(value):
    try:
        return deepcopy(value)
    except RecursionError:
        raise GatewayError("模型报告结构嵌套过深。", 502,
                           "explore_invalid_model_output") from None


def _condition_list(value, label):
    """Keep up to eight valid qualifications, even when sibling items are bad."""
    if not isinstance(value, list):
        return [], GatewayError(f"模型的{label}结构不正确。", 502,
                                "explore_invalid_model_output")
    kept = []
    error = (GatewayError(f"模型的{label}结构不正确。", 502,
                          "explore_invalid_model_output") if len(value) > 8 else None)
    for item in value:
        try:
            clean = optional_text(item, label, 3000)
        except GatewayError as exc:
            if error is None:
                error = exc
            continue
        if not clean:
            continue
        if len(kept) < 8:
            kept.append(clean)
        elif error is None:
            error = GatewayError(f"模型的{label}结构不正确。", 502,
                                 "explore_invalid_model_output")
    return kept, error


def _validate_timeline(value, allowed):
    if not isinstance(value, list) or len(value) > 12:
        raise GatewayError("模型时间线结构不正确。", 502, "explore_invalid_model_output")
    for item in value:
        if not isinstance(item, dict):
            raise GatewayError("模型时间线结构不正确。", 502, "explore_invalid_model_output")
        optional_text(item.get("time", ""), "时间", 120)
        optional_text(item.get("event"), "时间线事件", 1200)
        _refs(item.get("source_refs", []), allowed, "时间线")


def normalize_event_report(raw, sources):
    """Return ``(normalized, issues)`` for one event-lite model response.

    A non-object response is returned untouched so the runtime's existing
    model-output error path can reject it. Unknown top-level fields are omitted.
    Each issue uses a fixed field name and the original validator error code.
    """
    if not isinstance(raw, dict):
        return raw, []
    normalized = {field: _safe_copy(raw[field]) for field in _CORE if field in raw}
    allowed = {item["id"] for item in sources}
    issues = []
    for field in _AUXILIARY:
        default = "" if field in {"event_overview", "background"} else []
        if field not in raw:
            normalized[field] = default
            continue
        value = raw[field]
        if field in _CONDITION_FIELDS:
            kept, error = _condition_list(value, _CONDITION_FIELDS[field])
            normalized[field] = kept
            if error is not None:
                issue = {"stage": "auxiliary_fields", "field": field,
                         "code": error.code, "message": str(error)}
                if not kept:
                    issue["condition_lost"] = True
                issues.append(issue)
            continue
        try:
            if field in _LIST_FIELDS:
                _text_list(value, field, _LIST_FIELDS[field])
            elif field == "timeline":
                _validate_timeline(value, allowed)
            elif field == "event_overview":
                _event_overview(value)
            else:
                optional_text(value, "背景", 4000)
        except GatewayError as exc:
            normalized[field] = default
            issues.append({"stage": "auxiliary_fields", "field": field,
                           "code": exc.code, "message": str(exc)})
        else:
            if field == "timeline":
                # Keep only documented timeline keys. The strict report
                # validator still normalizes text and references afterward.
                normalized[field] = [{key: _safe_copy(item[key]) for key in
                                      ("time", "event", "source_refs") if key in item}
                                     for item in value]
            else:
                normalized[field] = _safe_copy(value)
    return normalized, issues
