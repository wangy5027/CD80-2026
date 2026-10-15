"""Small, deterministic search intent for the lightweight event workflow.

This module prepares one bounded search and ranks returned headlines/summaries.
It does not perform a search or infer market data from the wording of a question.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .research_task import (search_queries, search_result_acceptable,
                            search_result_score)


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CSI_300 = re.compile(r"(?<![A-Za-z0-9])(?:沪深\s*300|CSI\s*300)(?!\d)", re.I)
_INDEX_EVENTS = (
    (re.compile(r"(?:成[分份]股|样本).{0,6}(?:调整|更换)|调样|纳入|剔除"), "成分股调整"),
    (re.compile(r"分红|派息|派现|现金分配|现金派发"), "分红"),
    (re.compile(r"编制(?:规则|方法|方案)|选样(?:规则|方法)"), "编制规则"),
)
_TWO_TRADING_DAYS = re.compile(r"(?:这|近|最近|过去)\s*(?:两|2)\s*(?:个)?交易日")
_TWO_CALENDAR_DAYS = re.compile(
    r"(?:这|近|最近|过去)\s*(?:两|2)\s*(?:个)?天|今天.{0,8}昨天|昨天.{0,8}今天"
)
_MONTH = re.compile(r"本月|近一个月|最近一个月|过去一个月|近30天|最近30天")
_THIS_WEEK = re.compile(r"本周|这周")
_WEEK = re.compile(r"本周|这周|近一周|一周内|最近一周|近期|最近|最新")
_DAY = re.compile(r"今天|今日|昨晚|昨天|近24小时|24小时内")
_TREND = re.compile(r"涨|跌|走势|行情|收盘|波动|市场表现|股价|这两天|近两天|最近两天")
_INTRO = re.compile(
    r"^(?:(?:请问|我想知道|我想了解|我想看看|想知道|想了解|想看看|"
    r"请帮我|帮我看看|帮我看下|帮我查查|帮我分析|能不能帮我|可以帮我|"
    r"带我了解一下|帮我了解一下|告诉我|说一下|解释一下|给我讲讲|请|帮我|我想)\s*)+"
)
_OTHER_ACTORS = (
    "鸿蒙智行", "江淮汽车", "小米汽车", "宁德时代", "贵州茅台", "中芯国际",
    "赛力斯", "比亚迪", "华为", "小米", "江淮", "问界", "尊界", "智界", "享界",
)
_COMPANY_NAME = re.compile(
    r"(?:^|[与和及跟对，,])(?P<name>[\u4e00-\u9fff]{2,8}(?:集团|汽车|科技|股份|证券|银行|能源))"
)


def _as_of_shanghai(value):
    if isinstance(value, datetime):
        instant = value
    elif isinstance(value, str):
        try:
            instant = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            instant = datetime.now(_SHANGHAI)
    else:
        instant = datetime.now(_SHANGHAI)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=_SHANGHAI)
    return instant.astimezone(_SHANGHAI)


def _clean_question(question):
    text = re.sub(r"\s+", " ", str(question or "")).strip()
    text = _INTRO.sub("", text).strip(" ，,。；;？?！!：: ")
    return text or str(question or "").strip()


def _explicit_actors(question):
    text = _CSI_300.sub(" ", _clean_question(question))
    found = [name for name in _OTHER_ACTORS if name in text]
    found.extend(match.group("name") for match in _COMPANY_NAME.finditer(text))
    # Keep the most specific form: "小米汽车" already entails "小米".
    return [name for name in dict.fromkeys(found)
            if not any(name != other and name in other for other in found)]


def search_intent(question, as_of):
    """Return one query, provider freshness, and an optional local date floor.

    `window_start` is anchored to the frozen Shanghai run time. Two *trading*
    days stay within provider ``oneWeek`` candidates because this function has
    no exchange holiday calendar and must not guess trading dates.
    """
    original = str(question or "").strip()
    focus = _clean_question(original)
    now = _as_of_shanghai(as_of)
    index_question = bool(_CSI_300.search(focus))
    two_trading_days = bool(_TWO_TRADING_DAYS.search(focus))
    two_calendar_days = not two_trading_days and bool(_TWO_CALENDAR_DAYS.search(focus))

    window_start = None
    if two_calendar_days:
        freshness = "oneWeek"
        yesterday = now.date() - timedelta(days=1)
        window_start = datetime.combine(yesterday, datetime.min.time(), _SHANGHAI).isoformat()
    elif two_trading_days:
        freshness = "oneWeek"
    elif _MONTH.search(focus):
        # "最近一个月" must not be reduced to a one-week search by "最近".
        freshness = "oneMonth"
    elif _THIS_WEEK.search(focus):
        freshness = "oneWeek"
        monday = now.date() - timedelta(days=now.weekday())
        window_start = datetime.combine(monday, datetime.min.time(), _SHANGHAI).isoformat()
    elif _DAY.search(focus):
        freshness = "oneDay"
    elif _WEEK.search(focus):
        freshness = "oneWeek"
        window_start = (now - timedelta(days=7)).isoformat()
    else:
        freshness = "noLimit"

    if index_question:
        focus = _CSI_300.sub("沪深300", focus)
        terms = ["沪深300"]
        terms.extend(_explicit_actors(focus))
        terms.extend(label for pattern, label in _INDEX_EVENTS if pattern.search(focus))
        if two_calendar_days:
            terms.append(f"{yesterday:%Y年%m月%d日}至{now:%Y年%m月%d日}")
        elif two_trading_days:
            terms.append(f"截至{now:%Y年%m月%d日}近两交易日")
        elif _THIS_WEEK.search(focus) and freshness == "oneWeek" and window_start:
            monday = now.date() - timedelta(days=now.weekday())
            terms.append(f"{monday:%Y年%m月%d日}至{now:%Y年%m月%d日}")
        if _TREND.search(focus) or two_trading_days:
            terms.extend(("指数", "收盘", "涨跌"))
        else:
            remainder = _CSI_300.sub("", focus).strip(" ，,。；;？?！!：: ")
            if remainder:
                terms.append(remainder[:90])
        terms.append("新闻")
        query = " ".join(dict.fromkeys(terms))[:200]
    else:
        leads = search_queries({"question": original, "resolved_question": focus,
                                "module": "event", "context_snapshot": []})
        query = next((lead for lead in leads if not lead.startswith(("http://", "https://"))), "")
        query = (query or focus or original)[:200].strip()

    return {"query": query, "freshness": freshness,
            "focus_question": focus, "window_start": window_start,
            "as_of_shanghai": now.isoformat(), "as_of_date": now.date().isoformat()}


def relevant_news(question, item):
    """Reject only a clear identity conflict; missing keywords are not proof of mismatch."""
    return search_result_acceptable(question, item)


def news_relevance_score(question, item):
    """Prefer closer news without mistaking this ordering for fact verification."""
    score = search_result_score(question, item)
    if not isinstance(item, dict):
        return score
    text = " ".join(str(item.get(key) or "") for key in ("title", "summary", "snippet"))
    score += 4 * sum(term.casefold() in str(question or "").casefold() and
                     term.casefold() in text.casefold()
                     for term in ("A股", "港股", "美股", "股市"))
    if not _CSI_300.search(str(question or "")):
        return score
    score += 4 * sum(actor.casefold() in text.casefold()
                     for actor in _explicit_actors(str(question or "")))
    score += 8 * sum(pattern.search(str(question or "")) is not None and
                     pattern.search(text) is not None for pattern, _label in _INDEX_EVENTS)
    return score
