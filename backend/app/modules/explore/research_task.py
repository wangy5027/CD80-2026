"""Pure, bounded task extraction for Explore research runs.

The task keeps the user's complete request. Search strings are disposable leads,
never a replacement for the request or for evidence from a fetched document.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit


_URL = re.compile(r"https?://[^\s<>\"'‘’“”，。；;！？（）()\[\]{}]+", re.I)
_DOI = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.I)
_ARTICLE_ID = re.compile(r"\bs\d{5}-\d{2,4}-\d{4,8}-[a-z0-9]+\b", re.I)
_MODEL = re.compile(r"(?<![a-z0-9])[a-z]{1,8}[- ]?\d{2,8}(?:-[a-z0-9]+)*(?![a-z0-9])", re.I)
_LATIN = re.compile(r"\b[a-z][a-z0-9.-]{2,}\b", re.I)
_GENERIC = re.compile(
    r"^(?:请|帮我|想|我想|研究|分析|理解|验证|梳理|核查|看一下|研究一下|一下|这个|该|上述|这条|这篇|文章|事件|新闻|材料|的|关于|及其|其)+"
)
_STOP = frozenset({
    "nature", "article", "news", "http", "https", "www", "com", "org", "html",
    "最新", "最近", "近期", "今日", "今天", "发生", "研究", "分析", "理解", "事件",
    "新闻", "消息", "报道", "问题", "情况", "影响", "是否", "如何", "哪些", "什么",
    "公司", "企业", "相关", "官方", "媒体", "发表", "文章", "材料", "结果", "说明",
})
# Complete named anchors and subject terms. Do not make two-character sliding
# windows out of a longer Chinese phrase (e.g. 公信力争议 -> 力争).
_ENTITIES = (
    "鸿蒙智行", "懂车帝", "江淮汽车", "尊界汽车", "华为", "江淮", "尊界", "问界",
    "智界", "享界", "小米汽车", "小米", "财联社", "自然科学", "北京", "上海",
)
_TOPICS = (
    "数据可用性", "研发费用率", "研发费用", "研发投入", "资本化", "操作系统",
    "踏板支架", "刹车踏板", "制动踏板", "制动系统", "免费升级", "合同责任", "责任边界",
    "责任划分", "质量争议", "公信力", "资质", "刹车", "制动", "踏板", "支架",
    "断裂", "汽车", "车辆", "车企", "自燃", "召回", "事故", "安全", "测试",
    "检测", "回应", "表态", "合同", "责任", "供应商", "股价", "跌停", "股票",
    "历史先例", "历史案例", "先例", "年报", "研发", "芯片", "通信", "产出",
    "作者", "标题", "期刊", "论文", "发表日期", "可用性声明", "原文", "升级",
)
_ORG = re.compile(r"[\u4e00-\u9fff]{2,12}(?:集团|汽车|大学|研究院|实验室|交易所|委员会)")
_FOCUSED = frozenset(_TOPICS) - {"原文", "标题", "作者", "发表日期"}
_QUESTION_LABEL = re.compile(r"^(?:研究问题|问题|我想知道|待验证判断)\s*[:：]\s*")
_UNTRUSTED_MATERIAL_LABEL = re.compile(r"^(?:新闻原文|原文摘录|正文|材料原文)\s*[:：]")
_META_INSTRUCTION = re.compile(
    r"^(?:我还未|我不确定|尚未确定|还没确定|暂未确定|不用|不要|请不要|先别|暂时|"
    r"请先给我材料|先给我材料|请勿)"
)
_QUESTION_SUFFIX = re.compile(
    r"(?:发生了什么|发生什么|有什么(?:最新|最近)?(?:新闻|消息|事件|快讯)?|"
    r"怎么回事|是什么情况|怎么样|有哪些(?:新闻|消息|事件|影响)?|吗|呢|么)$"
)
_BROAD_NEWS = re.compile(
    r"^(?:请)?(?:最近|最新|近期|今天|今日)?(?:有|有什么|发生了)?(?:新闻|消息|快讯)(?:吗|呢)?[？?。]?$"
)


def _dedupe(items):
    return list(dict.fromkeys(value for value in items if value))


def _urls(text):
    return _dedupe(match.group().rstrip(".,;:") for match in _URL.finditer(text or ""))


def _ids(text, pattern):
    return _dedupe(match.group().rstrip(".,!?;:") for match in pattern.finditer(text or ""))


def _mask_urls(text):
    values = []

    def replace(match):
        values.append(match.group())
        return f"\uf000{len(values) - 1}\uf001"

    return _URL.sub(replace, text), values


def _unmask(text, values):
    for index, url in enumerate(values):
        text = text.replace(f"\uf000{index}\uf001", url)
    return text


def _split_questions(text):
    """Separate explicit asks without altering URL query strings or the input."""
    if not text:
        return []
    masked, urls = _mask_urls(text)
    lines = []
    has_question_line = bool(re.search(r"(?:研究问题|问题|我想知道)\s*[:：]", masked))
    for raw in masked.splitlines():
        line = raw.strip()
        if not line or _UNTRUSTED_MATERIAL_LABEL.match(line):
            continue
        if has_question_line and re.match(r"^(?:新闻标题|来源|链接)\s*[:：]", line):
            continue
        line = _QUESTION_LABEL.sub("", line)
        line = re.sub(r"^(?:[-*•]|[（(]?\d{1,2}[）).、])\s*", "", line)
        if line:
            lines.append(line)
    if not lines:
        lines = [masked.strip()]
    parts = []
    for line in lines:
        # An explicit "covers: A, B, C" list is a set of research targets. Keep
        # its parent goal too so that no qualifier is lost during extraction.
        cover = re.search(r"(?:涵盖|包括|分别核查|分别分析)\s*[:：]", line)
        if cover:
            parent, tail = line[:cover.start()].strip(" ，,：:"), line[cover.end():]
            if parent:
                parts.append(parent)
            parts.extend(f"{parent}：{piece.strip()}" if parent else piece.strip()
                         for piece in re.split(r"[、；;]|，\s*(?=[^，]{2,80}(?:回应|说法|安排|影响|争议|结果))", tail)
                         if piece.strip())
            continue
        parts.extend(piece.strip(" ，,。；;？? ") for piece in re.split(r"[？?；;]|(?:，\s*)?(?:以及|并且|同时)(?=[\u4e00-\u9fffA-Za-z])", line)
                     if piece.strip(" ，,。；;？? "))
    return _dedupe(_unmask(part, urls) for part in parts if not _META_INSTRUCTION.match(part))


def _input_type(text, urls, dois, article_ids):
    if urls:
        return "public_url"
    if dois:
        return "doi"
    if article_ids:
        return "document_id"
    if re.search(r"(?m)^\s*(?:新闻标题|新闻原文|原文摘录)\s*[:：]", text):
        return "quoted_article"
    return "question"


def _history_mode(module, goal):
    if module != "history":
        return None
    if re.search(r"当时|事后|历史时点|回测|事前", goal):
        return "as_of_backtest"
    if re.search(r"待验证判断|是否会|会不会|会导致|将导致|会产生|能否", goal):
        return "claim_test"
    return "precedent_search"


def build_research_task(run):
    """Freeze all user asks as stable Q1..Qn; never silently truncate them."""
    original = str(run.get("question") or "")
    goal = str(run.get("resolved_question") or original)
    module = str(run.get("module") or "event")
    urls = _dedupe([*_urls(goal), *_urls(original)])
    dois = _dedupe([*_ids(goal, _DOI), *_ids(original, _DOI)])
    article_ids = _dedupe([*_ids(goal, _ARTICLE_ID), *_ids(original, _ARTICLE_ID)])
    questions = _split_questions(goal)
    if not questions and goal.strip():
        questions = [goal.strip()]
    return {
        "original_input": original,
        "resolved_goal": goal,
        "module": module,
        "input_type": _input_type(goal, urls, dois, article_ids),
        "urls": urls,
        "dois": dois,
        "article_ids": article_ids,
        "subquestions": [
            {"id": f"Q{index}", "question": question, "status": "unanswered",
             "evidence_ids": [], "gap": "需要取得与本子问题直接相关的可定位材料。"}
            for index, question in enumerate(questions, 1)
        ],
        "as_of": (run.get("time_policy") or {}).get("as_of") or run.get("created_at"),
        "history_mode": _history_mode(module, goal),
    }


def _anchors(text):
    names = [word for word in _ENTITIES if word.casefold() in text.casefold()]
    ids = _ids(text, _ARTICLE_ID) + _ids(text, _MODEL)
    # Prefer an exact article/product identifier, then a named actor. Broad
    # brand names alone are deliberately weaker than a specified model.
    return _dedupe([*ids, *names])


def _clean_query(text):
    text = _QUESTION_LABEL.sub("", text.strip())
    text = _GENERIC.sub("", text).strip(" ，,。；;？? ：:")
    return re.sub(r"\s+", " ", text)


def search_queries(run):
    """Produce up to eight search leads; the full task remains in ResearchTask."""
    task = build_research_task(run)
    goal = task["resolved_goal"]
    anchors = _anchors(goal)
    context_lead = ""
    if re.search(r"^(?:该|这|上述|其|继续|接着|再分析|基于上)", goal):
        # Only this run's explicitly frozen branch/file context is eligible.
        context = run.get("context_snapshot") or []
        if context:
            ancestor = str(context[0].get("title") or context[0].get("question") or "")
            context_lead = ancestor[:80].strip()
            anchors = _dedupe([*_anchors(ancestor), *anchors])
    lead = " ".join(anchors[:2])[:65]
    queries = []
    for url in task["urls"]:
        if len(url) <= 200:
            queries.append(url)
    for ident in [*task["dois"], *task["article_ids"]]:
        if ident.casefold() not in " ".join(queries).casefold():
            queries.append(ident)
    for entry in task["subquestions"]:
        body = _URL.sub(" ", entry["question"])
        body = _clean_query(body)
        if not body or re.fullmatch(r"(?:一下)?(?:这个|该|上述)?(?:文章|论文|事件|材料)", body):
            continue
        # Repeat the most specific document/product identifier after a question
        # boundary, even if a broad actor also appears in the next clause.
        if lead and anchors[0].casefold() not in body.casefold():
            missing = [anchor for anchor in anchors[:2] if anchor.casefold() not in body.casefold()]
            body = f"{' '.join(missing)} {body}"
        if context_lead and context_lead not in body:
            body = f"{context_lead} {body}"
        # Preserve each subquestion as a separate lead. Long lists break at
        # punctuation; a bounded query is never misrepresented as full intent.
        chunks = re.split(r"(?<=[，,、：:])", body) if len(body) > 200 else [body]
        current = ""
        for piece in chunks:
            if len(current) + len(piece) > 200 and current:
                queries.append(current.strip(" ，,、：:"))
                current = f"{lead} {piece}".strip() if lead else piece
            else:
                current += piece
        if current:
            queries.append(current.strip(" ，,、：:"))
    return _dedupe(query[:200].strip() for query in queries)[:8]


def keywords(question):
    """Return whole recognized terms, never arbitrary Chinese substrings."""
    text = _URL.sub(" ", " ".join(_split_questions(question or ""))).casefold()
    terms = {word.casefold() for word in (*_ENTITIES, *_TOPICS) if word.casefold() in text}
    terms.update(match.casefold() for match in _MODEL.findall(text))
    terms.update(match.casefold() for match in _ARTICLE_ID.findall(text))
    terms.update(match.casefold() for match in _DOI.findall(text))
    terms.update(match.casefold() for match in _ORG.findall(text))
    terms.update(word.casefold() for word in _LATIN.findall(text) if word.casefold() not in _STOP)
    if not terms:
        # Conservative exact-phrase fallback for a new topic outside the small
        # subject lexicon. A complete two-character subject such as a country
        # name is valid; it is not an arbitrary substring of a longer phrase.
        for piece in re.split(r"[\s，,。；;：:？！?（）()]+", text):
            subject = _QUESTION_SUFFIX.sub("", _GENERIC.sub("", piece)).strip()
            if 2 <= len(subject) <= 32 and re.search(r"[\u4e00-\u9fff]", subject):
                terms.add(subject.casefold())
    return terms - _STOP


def _direct_url_match(question, item):
    target = str(item.get("url") or "")
    if not target:
        return False

    def identity(url):
        try:
            value = urlsplit(url)
            return urlunsplit((value.scheme.lower(), value.netloc.lower(), value.path.rstrip("/"), value.query, ""))
        except ValueError:
            return ""

    return any(identity(url) == identity(target) for url in _urls(question))


def relevance(item, words):
    """Score a result only when named actors and topic both fit a focused ask."""
    if not isinstance(item, dict):
        return 0
    title = str(item.get("title") or "").casefold()
    summary = str(item.get("summary") or item.get("snippet") or "").casefold()
    url = str(item.get("url") or "").casefold()
    terms = {str(word).casefold() for word in words if word}
    article_ids = {word for word in terms if _ARTICLE_ID.fullmatch(word) or _DOI.fullmatch(word)}
    if article_ids and not any(word in (title + " " + summary + " " + url) for word in article_ids):
        return 0
    if article_ids:
        # A precise paper identifier establishes identity even when a search
        # result exposes metadata only; content completeness is recorded later.
        return 100 + 6 * sum(word in (title + " " + summary) for word in terms - article_ids)
    entity = {word for word in terms if word in {e.casefold() for e in _ENTITIES}
              or _MODEL.fullmatch(word) or _ORG.fullmatch(word)}
    topic = {word for word in terms if word in {t.casefold() for t in _FOCUSED}}
    entity_hits = {word for word in entity if word in title or word in summary}
    topic_hits = {word for word in topic if word in title or word in summary}
    if entity and not entity_hits and not article_ids:
        return 0
    if topic and not topic_hits:
        return 0
    if not entity and not topic and not article_ids:
        exact_hits = {word for word in terms if word in title or word in summary}
        return 8 if exact_hits else 0
    if not entity and len(topic_hits) < 2 and not article_ids:
        return 0
    return 8 * len(entity_hits) + 6 * len(topic_hits) + 20 * len(article_ids)


def qualified_source(question, item):
    """Accept an exact requested URL or a result satisfying the focused ask."""
    if _direct_url_match(question, item):
        return True
    words = keywords(question)
    if not words and _BROAD_NEWS.fullmatch(str(question or "").strip()):
        return bool(str(item.get("title") or item.get("summary") or "").strip())
    return relevance(item, words) > 0
