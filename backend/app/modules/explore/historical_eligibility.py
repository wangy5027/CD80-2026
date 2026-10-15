"""Structural date eligibility for historical cases in an Explore research brief.

This checks that an actual, non-background support quote contains the claimed
event date. It cannot prove that the date describes the claimed event; a report
review must still examine that semantic relationship. It is not a historical
point-in-time information or market validation.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any


_DATE_ISO = re.compile(r"(?<![0-9])([0-9]{4})-([0-9]{2})-([0-9]{2})(?![0-9])")
_DATE_CHINESE = re.compile(
    r"(?<![0-9])([0-9]{4})\s*年\s*([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日(?![0-9])"
)
_EXACT_ISO = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _exact_date(value: Any) -> date | None:
    if not isinstance(value, str) or _EXACT_ISO.fullmatch(value) is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _has_date(text: str, expected: date) -> bool:
    for pattern in (_DATE_ISO, _DATE_CHINESE):
        for match in pattern.finditer(text):
            try:
                if date(*(int(part) for part in match.groups())) == expected:
                    return True
            except ValueError:
                continue
    return False


def eligible_historical_ids(claims: list[dict], as_of: str) -> set[str]:
    """Return source IDs structurally eligible to substantiate an old event.

    ``claims`` must be the validated research brief, not raw model output.
    Published-at dates never confer eligibility: a recently published
    retrospective can qualify when its read support quote states an older
    event date, while an old publication alone cannot.
    """
    if not isinstance(as_of, str) or not isinstance(claims, list):
        return set()
    as_of_day = _exact_date(as_of[:10])
    if as_of_day is None:
        return set()

    eligible: set[str] = set()
    for claim in claims:
        if not isinstance(claim, dict) or claim.get("support_status") != "traceable":
            continue
        if claim.get("type") == "hypothesis":
            continue
        event_day = _exact_date(claim.get("event_at"))
        if event_day is None or event_day >= as_of_day:
            continue
        date_quote = claim.get("event_date_quote")
        if not isinstance(date_quote, str) or not date_quote or not _has_date(date_quote, event_day):
            continue
        supports = claim.get("supports")
        if not isinstance(supports, list):
            continue
        for support in supports:
            if not isinstance(support, dict) or support.get("background_only") is not False:
                continue
            source_ref = support.get("source_ref")
            quote = support.get("quote")
            if isinstance(source_ref, str) and source_ref and isinstance(quote, str) and date_quote in quote:
                eligible.add(source_ref)
    return eligible
