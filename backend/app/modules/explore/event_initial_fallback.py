"""Finish the first explanation without another search or model request."""

from __future__ import annotations

import re

from ..workbench.search import _provider_plain_text


_SENTENCE = re.compile(r".+?(?:[。！？!?](?:[’”\"」』）)]*)|\.(?=\s|$))(?:\s*\[S\d+\])*", re.S)
_SERVICE_NOTICE = re.compile(
    r"(?:不能|无法|没有|未能|未|不具备).{0,12}(?:联网|实时(?:访问|获取|查询))|"
    r"(?:联网|搜索|检索|模型|生成|接口|网络).{0,16}(?:超时|失败|无结果|未取得|未找到|不可用)|"
    r"(?:未取得|未找到|没有找到|没有可用).{0,12}(?:新闻|资料|来源|结果)|"
    r"(?:无法|不能|没法).{0,12}(?:确切|准确|实时|最新).{0,8}(?:排名|排行)|"
    r"(?:请|建议).{0,8}(?:稍后)?重试|(?:我已明白|我需要进一步解释)|"
    r"(?:cannot|can't|unable to|no access to).{0,25}(?:internet|browse|real.time)", re.I)
_QUALIFYING_TAIL = re.compile(r"^(?:但|不过|然而|只是|实际上|仅|尚|未|不|可能|如果|除非|条件|取决于|这个说法|这一说法)")
_CONCRETE_FACT = re.compile(
    r"(?:排名|排行|排在)\s*(?:第|前|[一二三四五六七八九十\d])|位居|位列|第[一二三四五六七八九十\d]+|"
    r"\d+(?:\.\d+)?\s*(?:%|％|点|元|美元|亿|万|年|月|日)|"
    r"(?:最新|今日|今天|本周|当前|现在).{0,30}(?:报|上涨|下跌|增长|下滑|涨|跌|收盘|达到)")
_UNCERTAIN_FACT = re.compile(r"尚不能证明|不能确认|尚未证实|未获证实|未经核实|如果|假设|未必|不代表")
_SOURCE_MARKER = re.compile(r"\s*\[(?:S\d+\s*[,，、]\s*)*S\d+\]")
_MARKDOWN_LINK = re.compile(r"\[([^\]\n]+)\]\((https?://[^)\s]+)\)")
_URL = re.compile(r"https?://[^\s<>\]\)。，；！？,;!?]+")
_BARE_SOURCE_ID = re.compile(r"(?<![A-Za-z0-9])S\d+(?![A-Za-z0-9])")


def _unfinished_qualification(text):
    matches = list(_SENTENCE.finditer(text[:8000]))
    tail = text[matches[-1].end():].strip() if matches else text.strip()
    return bool(tail and _QUALIFYING_TAIL.search(tail))


def _complete_sentences(text, limit=600):
    """Drop an unfinished tail, keeping qualification with its sentence."""
    if not isinstance(text, str):
        return ""
    rows, length = [], 0
    for match in _SENTENCE.finditer(text[:8000]):
        sentence = match.group().strip()
        if not sentence or _SERVICE_NOTICE.search(sentence):
            continue
        if length + len(sentence) > limit:
            break
        rows.append(sentence)
        length += len(sentence)
    return "\n\n".join(rows).strip()


def _knowledge_answer(question):
    """Use stable explanations, never guessed current prices or rankings."""
    if re.search(r"排名|排行|前[十两三五\d]|第[一二三\d]|龙头|最大|最强", question):
        return ("比较谁更靠前，先看比较的口径：规模看收入或资产，盈利看利润与利润率，"
                "成长看收入和利润增速，市场表现看同一期间的涨跌幅。不同口径下，领先对象可能不同。"
                "同一行业、同一时间范围内比较，再结合现金流、负债和业务竞争力，结论会更有实际意义。")
    if re.search(r"汇率|人民币|贬值|升值|出口", question):
        return ("一般来说，人民币贬值会使同样的外币收入兑换成更多人民币，可能增厚出口企业的利润；"
                "同时也会增加进口原料和外币债务的成本。升值的作用通常相反。实际影响还取决于结算币种、"
                "进口成本占比、定价能力和汇率套保安排，不能仅凭汇率方向判断所有企业的表现。")
    if re.search(r"利率|降息|加息|美联储|央行|货币政策", question):
        return ("利率变化主要通过融资成本、资金流向和市场预期影响经济与股市。降息通常能降低融资成本，"
                "并改变不同资产的吸引力；加息通常带来相反作用。但若降息同时反映需求走弱，盈利压力可能"
                "抵消部分利好，所以还要结合企业盈利和政策变化是否已被市场预期来判断。")
    if re.search(r"股|行情|大盘|指数|涨|跌|市场", question):
        return ("看股票行情，可以结合指数涨跌、成交量和上涨下跌的股票数量来判断市场强弱。"
                "指数上涨且成交活跃、上涨股票较多，通常说明走势较有支撑；若主要靠少数权重股推动，"
                "整体感受可能不同。短期波动还会受到政策消息、业绩预期和资金流向影响，单个指数点位"
                "或一天表现不能代表整周趋势。")
    return ("理解一个事件，可以从“发生了什么变化、影响谁、通过什么渠道产生影响”这三点入手。"
            "对企业而言，常见渠道包括需求、成本、现金流和竞争格局；影响大小还取决于持续时间、"
            "业务占比与替代方案。将短期情绪变化与长期经营变化分开，更有助于判断事件的实际意义。")


def build_initial_fallback(question, partial_text, sources):
    """Return a usable answer and its honest provenance, without I/O."""
    partial_text = partial_text if isinstance(partial_text, str) else ""
    partial = _complete_sentences(partial_text)
    allowed = {str(item.get("id")): item for item in (sources or [])}
    # A complete sentence can still lose a qualification in the next fragment.
    # Do not publish unsupported current facts from an interrupted answer.
    if _unfinished_qualification(partial_text):
        partial = ""
    for match in _SENTENCE.finditer(partial):
        sentence = match.group()
        refs = re.findall(r"\[(S\d+)\]", sentence)
        if any(ref not in allowed for ref in refs):
            partial = ""
            break
        if _CONCRETE_FACT.search(sentence) and not _UNCERTAIN_FACT.search(sentence):
            if not refs:
                partial = ""
                break
            evidence = " ".join(str(allowed[ref].get("text_used") or allowed[ref].get("summary") or "") for ref in refs)
            values = re.findall(r"第[一二三四五六七八九十\d]+|\d+(?:\.\d+)?(?:%|％)?", re.sub(r"\[S\d+\]", "", sentence))
            if any(value not in evidence for value in values):
                partial = ""
                break
    if partial:
        return partial, "model_partial"
    excerpts = []
    for item in (sources or [])[:2]:
        # Unreviewed zero-match context may help the model, but must not be
        # turned into a direct answer when the model produced no usable text.
        # Older saved source snapshots have no score and retain their behavior.
        if type(item.get("relevance_score")) is int and item["relevance_score"] == 0:
            continue
        ref = str(item.get("id") or "")
        if not re.fullmatch(r"S\d+", ref):
            continue
        content = _provider_plain_text(item.get("text_used") or item.get("summary") or "")
        if _unfinished_qualification(content):
            continue
        excerpt = _complete_sentences(content, 220)
        if not excerpt:
            continue
        excerpts.append(excerpt)
    if excerpts:
        return "\n\n".join(excerpts), "source_excerpt"
    return _knowledge_answer(str(question or "")), "knowledge_fallback"


def present_initial_answer(text, sources, question):
    """Hide temporary source handles and links after evidence checks complete."""
    body = str(text or "")
    original_question = str(question or "")

    def link_text(match):
        return match.group(0) if match.group(2) in original_question else match.group(1)

    def keep_user_url(match):
        return match.group(0) if match.group(0) in original_question else ""

    body = _MARKDOWN_LINK.sub(link_text, body)
    body = _URL.sub(keep_user_url, body)
    body = _SOURCE_MARKER.sub("", body)
    body = _BARE_SOURCE_ID.sub(
        lambda match: match.group(0) if match.group(0) in original_question else "", body)
    body = re.sub(r"(?m)^[ \t]*(?:来源|原文链接|新闻链接)[：:]?[ \t]*$", "", body)
    body = re.sub(r"[ \t]+([，。；：！？,.!?])", r"\1", body)
    return re.sub(r"\n{3,}", "\n\n", body).strip()
