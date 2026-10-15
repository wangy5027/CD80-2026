"""Small, deterministic search intent for the lightweight event workflow.

This module only prepares one bounded search and checks whether a returned
headline/summary concerns the question. It does not perform a search or infer
market data from the wording of a question.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .research_task import qualified_source, search_queries


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_CSI_300 = re.compile(r"(?<![A-Za-z0-9])(?:沪深\s*300|CSI\s*300)(?!\d)", re.I)
_INDEX_EVENTS = (
    (re.compile(r"(?:成[分份]股|样本).{0,6}(?:调整|更换)|调样|纳入|剔除"), "成分股调整"),
    (re.compile(r"分红|派息|现金分配"), "分红"),
    (re.compile(r"编制(?:规则|方法|方案)|选样(?:规则|方法)"), "编制规则"),
)
_TWO_TRADING_DAYS = re.compile(r"(?:这|近|最近|过去)\s*(?:两|2)\s*(?:个)?交易日")
_TWO_CALENDAR_DAYS = re.compile(
    r"(?:这|近|最近|过去)\s*(?:两|2)\s*(?:个)?天|今天.{0,8}昨天|昨天.{0,8}今天"
)
_MONTH = re.compile(r"本月|近一个月|最近一个月|过去一个月|近30天|最近30天")
_WEEK = re.compile(r"本周|这周|近一周|一周内|最近一周|近期|最近|最新")
_DAY = re.compile(r"今天|今日|昨晚|昨天|近24小时|24小时内")
_TREND = re.compile(r"涨|跌|走势|行情|收盘|波动|市场表现|股价|这两天|近两天|最近两天")
_MARKET = re.compile(r"A股|股市|股票|大盘|上证|深证|沪深|创业板|科创板|港股|美股", re.I)
_MARKET_NEWS = re.compile(r"A股|股市|股票|大盘|上证|深证|沪深|创业板|科创板|收盘|指数", re.I)
_MARKET_GENERIC = re.compile(
    r"A股|股市|股票|大盘|上证|深证|沪深|创业板|科创板|港股|美股|市场|指数|行情|走势|"
    r"本周|这周|一周|今天|今日|昨天|昨晚|最近|近期|最新|现在|目前|的|是|有|看|请|帮|我|"
    r"怎么样|为什么|为何|如何|什么|情况|原因|波动|涨跌|涨|跌|吗|呢|了|一下|[\s，,。？?！!：:；;]", re.I
)
_STOCK_SUBJECT = re.compile(r"^([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·&.-]{1,30}?)(?:的)?(?:股价|股票|市值|估值)", re.I)
_STOCK_NEWS = re.compile(r"股价|股票|上涨|下跌|涨|跌|回调|反弹|市值|估值|收盘|美股|股市", re.I)
_AI_NEWS = re.compile(r"AI|人工智能|算力|芯片|GPU|数据中心|大模型", re.I)
_QUESTION_LEAD = re.compile(r"^(?:(?:最近|近期|本周|这周|今天|今日|请问|请分析|请解释|分析|看看|研究|为什么|为何)\s*)+")
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

    `window_start` only denotes an explicit today-and-yesterday calendar window.
    Two *trading* days stay within provider ``oneWeek`` candidates because this
    function has no exchange holiday calendar and must not guess trading dates.
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
    elif _DAY.search(focus):
        freshness = "oneDay"
    elif _WEEK.search(focus):
        freshness = "oneWeek"
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
            "focus_question": focus, "window_start": window_start}


def relevant_news(question, item):
    """For a named index, require the index and every explicit extra actor.

    Unrecognized topics retain the repository's conservative source rule.
    Neither a bare ``300`` nor news about another index establishes identity.
    """
    if not isinstance(item, dict):
        return False
    if not _CSI_300.search(str(question or "")):
        if qualified_source(question, item):
            return True
        # A genuinely broad market question may use a broad market report.
        # A named subject such as Huawei leaves a meaningful residual and
        # cannot be admitted by this exception.
        broad = bool(_MARKET.search(str(question or "")) and not re.search(
            r"[\u4e00-\u9fffA-Za-z]{2,}", _MARKET_GENERIC.sub("", str(question or ""))))
        text = " ".join(str(item.get(key) or "") for key in ("title", "summary", "snippet"))
        if broad and _MARKET_NEWS.search(text):
            return True
        # Preserve the full-company rule used by the event stock workflow.
        # A bare topic such as AI cannot stand in for the requested company.
        focus = _QUESTION_LEAD.sub("", _clean_question(question))
        match = _STOCK_SUBJECT.match(focus)
        if not match:
            return False
        subject = match.group(1)
        aliases = {"英伟达": ("英伟达", "NVIDIA")}.get(subject, (subject,))
        return bool(any(alias.casefold() in text.casefold() for alias in aliases)
                    and _STOCK_NEWS.search(text)
                    and (not re.search(r"AI|人工智能|算力", str(question), re.I) or _AI_NEWS.search(text)))
    text = " ".join(str(item.get(key) or "") for key in ("title", "summary", "snippet"))
    if not _CSI_300.search(text):
        return False
    if any(pattern.search(str(question)) and not pattern.search(text)
           for pattern, _label in _INDEX_EVENTS):
        return False
    return all(actor.casefold() in text.casefold() for actor in _explicit_actors(str(question or "")))
