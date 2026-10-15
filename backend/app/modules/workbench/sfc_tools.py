"""Independent read-only Southern Finance (SFC) tool declarations.

The model sees small fixed schemas. Handlers accept only those fields and call
fixed HTTPS endpoints through SFCClient; Skill instructions are optional.
"""

from __future__ import annotations

import html
import ipaddress
import json
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo

from .errors import GatewayError
from .sfc_client import SFCClient
from .tool_registry import ToolResult, ToolSpec


BEIJING = ZoneInfo("Asia/Shanghai")
_MONGO = "/terminal/zxt-web/mongo/query"
_FINANCE_COLLECTIONS = {
    "index": "TQ_SK_OVERVIEW_INDEX_2", "balance": "NC_TQ_FIN_PRGBALSHEETNEW_2",
    "income": "NC_TQ_FIN_PROINCSTATEMENTNEW_2", "cashflow": "NC_TQ_FIN_PRGCFSTATEMENTNEW_2",
}
_ROOTS = {"china": "M000000001", "international": "Y000000001", "industry": "S000000001"}
_MARGIN_SORT = {
    "margin_buy": "融资买入额", "net_margin": "融资净买入额",
    "margin_balance": "融资融券余额", "change_rate": "涨跌幅",
}
_URL_FIELDS = ("url", "link", "articleUrl", "articleLink", "article_url", "newsUrl", "newsLink",
               "sourceUrl", "announcementLink", "pdfUrl", "pdf_url", "download_url")
_DATE_FIELDS = ("trade_date", "TRADEDATE", "ENDDATE", "END_DATE", "date", "report_date",
                "date_updated", "publishTime", "infoPublDate", "time")
_SECRET_FIELDS = {"token", "api_key", "apikey", "authorization", "password", "secret"}
_OUTPUT_BUDGET = 440 * 1024  # Leaves space for separately numbered source links.
_PRIORITY_FIELDS = {"date", "trade_date", "TRADEDATE", "ENDDATE", "END_DATE", "report_date",
                    "date_updated", "SYMBOL", "code", "secuCode", "name", "title", "infoTitle",
                    "report_title", "AMOUNT", "CASHNETR", "CASHFINALBALA"}


def _schema(properties: dict, required=()) -> dict:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _string(description: str, maximum: int = 100, *, choices=None) -> dict:
    value = {"type": "string", "description": description, "maxLength": maximum}
    if choices is not None:
        value["enum"] = list(choices)
    return value


def _integer(description: str, maximum: int = 20, minimum: int = 1) -> dict:
    return {"type": "integer", "description": description, "minimum": minimum, "maximum": maximum}


def _check_keys(args, allowed):
    if not isinstance(args, dict) or any(key not in allowed for key in args):
        raise GatewayError("南财工具参数包含不支持的字段。", 400)


def _text(args, key, *, required=False, maximum=100, pattern=None, default=""):
    value = args.get(key, default)
    if value == "" and not required:
        return ""
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise GatewayError(f"南财工具参数 {key} 无效。", 400)
    value = value.strip()
    if pattern and not re.fullmatch(pattern, value):
        raise GatewayError(f"南财工具参数 {key} 无效。", 400)
    return value


def _choice(args, key, choices, *, default=None):
    value = args.get(key, default)
    if type(value) not in {str, int} or value not in choices:
        raise GatewayError(f"南财工具参数 {key} 无效。", 400)
    return value


def _int(args, key, *, minimum=1, maximum=20, default=10):
    value = args.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise GatewayError(f"南财工具参数 {key} 无效。", 400)
    return value


def _bool(args, key, default=False):
    value = args.get(key, default)
    if type(value) is not bool:
        raise GatewayError(f"南财工具参数 {key} 无效。", 400)
    return value


def _date(args, key, *, default=""):
    value = _text(args, key, maximum=10, default=default)
    if value:
        try:
            datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            raise GatewayError(f"南财工具日期 {key} 格式无效。", 400) from None
    return value


def _code(args, key="code", *, required=True):
    code = _text(args, key, required=required, maximum=9, pattern=r"\d{6}(?:\.(?:SH|SZ|BJ))?")
    return code.upper()


def _item_date(item):
    if not isinstance(item, dict):
        return None
    for field in _DATE_FIELDS:
        value = item.get(field)
        if value is not None and value != "":
            if isinstance(value, (int, float)) and value > 10**11:
                return datetime.fromtimestamp(value / 1000, BEIJING).date().isoformat()
            if isinstance(value, (int, float)) and value > 10**9:
                return datetime.fromtimestamp(value, BEIJING).date().isoformat()
            text = str(value)
            if re.match(r"\d{4}-\d{2}-\d{2}", text):
                try:
                    return datetime.strptime(text[:10], "%Y-%m-%d").date().isoformat()
                except ValueError:
                    continue
            if re.match(r"\d{8}", text):
                try:
                    return datetime.strptime(text[:8], "%Y%m%d").date().isoformat()
                except ValueError:
                    continue
    return None


def _item_timestamp(item):
    if not isinstance(item, dict):
        return None
    for field in ("time", "createTime", "date_updated", "publishTime"):
        value = item.get(field)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float)):
            if value > 10**11:
                value /= 1000
            if value > 10**9:
                return datetime.fromtimestamp(value, BEIJING)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return parsed.replace(tzinfo=BEIJING) if parsed.tzinfo is None else parsed.astimezone(BEIJING)
            except ValueError:
                continue
    return None


def _clean(value, *, depth=0, secret="", max_fields=256, max_string=800, stats=None):
    if stats is None:
        stats = {"fieldsOmitted": 0, "stringsTruncated": 0, "nestedItemsOmitted": 0, "depthOmitted": 0}
    if depth > 5:
        stats["depthOmitted"] += 1
        return None
    if isinstance(value, dict):
        fields = [(k, v) for k, v in value.items() if str(k).lower() not in _SECRET_FIELDS]
        # Preserve dates, identifiers, and amount fields even in wide financial reports.
        fields.sort(key=lambda pair: (0 if str(pair[0]) in _PRIORITY_FIELDS else
                                     1 if re.search(r"date|time|symbol|code|amount|cash|revenue|profit", str(pair[0]), re.I)
                                     else 2))
        stats["fieldsOmitted"] += max(0, len(fields) - max_fields)
        return {str(k)[:80]: _clean(v, depth=depth + 1, secret=secret, max_fields=max_fields,
                                    max_string=max_string, stats=stats) for k, v in fields[:max_fields]}
    if isinstance(value, list):
        stats["nestedItemsOmitted"] += max(0, len(value) - 20)
        return [_clean(v, depth=depth + 1, secret=secret, max_fields=max_fields,
                       max_string=max_string, stats=stats) for v in value[:20]]
    if isinstance(value, str):
        value = html.unescape(re.sub(r"<[^>]*>", " ", value))
        value = re.sub(r"\s+", " ", value).strip()
        if secret:
            value = value.replace(secret, "[redacted]")
        if len(value) > max_string:
            stats["stringsTruncated"] += 1
            return value[: max_string - 6] + "…[截断]"
        return value
    if isinstance(value, (float, int, bool)) or value is None:
        return value
    return str(value)[:200]


def _items(result, *, key="data"):
    if isinstance(result, list):
        return result
    if not isinstance(result, dict):
        raise GatewayError("南财数据结构无效。", 502)
    data = result.get(key, [])
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Some endpoints wrap records in a nested page object.
        for inner in ("list", "records", "items"):
            if isinstance(data.get(inner), list):
                return data[inner]
        return [data]
    if data is None:
        return []
    raise GatewayError("南财数据结构无效。", 502)


def _sources(rows, *, secret=""):
    sources, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        for field in _URL_FIELDS:
            url = row.get(field)
            if not isinstance(url, str):
                continue
            try:
                parsed = urlsplit(url.strip())
            except ValueError:
                continue
            hostname = parsed.hostname or ""
            if (parsed.scheme not in {"https", "http"} or not hostname or parsed.username or parsed.password
                    or any(ord(character) < 32 for character in url)
                    or hostname.lower() in {"localhost", "localhost.localdomain"}
                    or hostname.lower().endswith((".local", ".internal"))
                    or any(key.lower() in {"token", "api_key", "apikey", "authorization", "key", "secret"}
                           for key, _ in parse_qsl(parsed.query, keep_blank_values=True))):
                continue
            try:
                if not ipaddress.ip_address(hostname).is_global:
                    continue
            except ValueError:
                pass
            if len(url) > 1500 or url in seen or (secret and secret in url):
                continue
            seen.add(url)
            title = str(row.get("title") or row.get("infoTitle") or row.get("report_title") or parsed.hostname)[:160]
            if secret:
                title = title.replace(secret, "[redacted]")
            sources.append({"title": title, "url": url})
            break
    return sources[:20]


def _result(client, raw, *, tool: str, rows=None, limit=10, page=None, coverage=None, extra=None):
    rows = _items(raw) if rows is None else rows
    if not isinstance(rows, list):
        raise GatewayError("南财数据结构无效。", 502)
    now = datetime.now(BEIJING).isoformat(timespec="seconds")
    secret = client._token()
    reported_total = raw.get("total") if isinstance(raw, dict) else None
    if isinstance(reported_total, str) and reported_total.isdigit():
        reported_total = int(reported_total)
    if type(reported_total) is not int or reported_total < 0:
        reported_total = None
    coverage_info = {"complete": None, "page": page, "requestedLimit": limit,
                     "upstreamReportedTotal": reported_total, "upstreamResponseItems": len(rows),
                     "note": "仅展示本次接口响应；上游是否还有更多记录未验证。"}
    if coverage is not None:
        coverage_info.update(coverage)
    selected_count = min(len(rows), limit)
    content = None
    selected = []
    for count in [selected_count, max(1, selected_count // 2), 1, 0]:
        count = min(count, selected_count)
        selected = rows[:count]
        for max_fields, max_string in ((256, 800), (256, 400), (128, 400), (128, 200),
                                       (64, 200), (64, 100), (32, 80)):
            stats = {"fieldsOmitted": 0, "stringsTruncated": 0, "nestedItemsOmitted": 0, "depthOmitted": 0}
            dates = [date for row in selected if (date := _item_date(row))]
            local_truncated = len(rows) > count
            candidate = {
                "provider": "南方财经", "tool": tool, "retrievedAt": now,
                "dataDate": max(dates) if dates else None,
                "items": _clean(selected, secret=secret, max_fields=max_fields, max_string=max_string, stats=stats),
                "count": count, "truncated": local_truncated, "localTruncated": local_truncated,
                "omittedItemCount": len(rows) - count,
                "fieldsTruncated": stats["fieldsOmitted"] > 0,
                "omittedFieldCount": stats["fieldsOmitted"],
                "stringsTruncated": stats["stringsTruncated"],
                "nestedItemsOmitted": stats["nestedItemsOmitted"],
                "depthOmitted": stats["depthOmitted"],
                "coverage": coverage_info,
            }
            if extra:
                candidate.update(extra)
            if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) <= _OUTPUT_BUDGET:
                content = candidate
                break
        if content is not None:
            break
    if content is None:
        raise GatewayError("南财数据结果超出输出上限。", 502)
    sources = _sources(selected, secret=secret)
    return ToolResult(content=content, sources=sources or None)


def _mongo(client, context, collection, *, page=1, limit=10, filters=None, sort=None, fields=None):
    payload = {"collectionName": collection, "pagination": {"page": page, "size": limit}}
    if filters:
        payload["filters"] = filters
    if sort:
        payload["sortFields"] = sort
    if fields:
        payload["fields"] = fields
    return client.request("POST", _MONGO, context, body=payload)


def _filter_eq(value, *, field_type="String"):
    return {"operator": "eq", "type": field_type, "value": value}


def _news(client, args, context):
    _check_keys(args, {"action", "keyword", "code", "limit", "page"})
    action = _choice(args, "action", {"recent", "search", "stock_articles"}, default="recent")
    _check_keys(args, {"action", "limit"} if action == "recent" else
                {"action", "keyword", "limit", "page"} if action == "search" else
                {"action", "code", "limit", "page"})
    limit = _int(args, "limit")
    page = _int(args, "page", maximum=100, default=1)
    if action == "recent":
        raw = client.request("GET", "/terminal/rest/article/recent", context,
                             params={"forward": "false", "pageSize": limit}, auth="bearer")
    elif action == "search":
        raw = client.request("GET", "/terminal/rest/esSearch/searchkey/page", context,
                             params={"keyword": _text(args, "keyword", required=True), "category": 4,
                                     "pageNo": page, "pageSize": limit})
    else:
        raw = client.request("GET", "/terminal/rest/theme/stock/articles", context,
                             params={"stockCode": _code(args), "pageNo": page, "pageSize": limit, "type": 1})
    return _result(client, raw, tool="sfc_news", limit=limit, page=None if action == "recent" else page)


def _timeline(client, args, context):
    _check_keys(args, {"keyword", "hours", "limit", "redOnly"})
    keyword = _text(args, "keyword")
    hours = _int(args, "hours", maximum=48, default=24)
    limit = _int(args, "limit")
    red = _bool(args, "redOnly")
    cutoff = datetime.now(BEIJING) - timedelta(hours=hours)
    rows, errors, coverage = [], [], []
    params = {"pageSize": min(max(limit * 3, 60), 120)}
    if keyword:
        params["keyWord"] = keyword
    try:
        raw = client.request("GET", "/terminal/rest/article/flash", context, params=params, auth="bearer")
        rows.extend(_items(raw))
        coverage.append("南财快讯：最近请求的最多120条")
    except GatewayError as exc:
        if exc.status == 409:
            raise
        errors.append("南财快讯接口失败")
    # A strict three-page bound avoids the old 80-page scan on busy news days.
    for page in range(1, 4):
        try:
            raw = client.request("GET", "/console/newsflash/list", context, host="zxtop", auth="none",
                                 params={"pageNo": page, "pageSize": 20, **({"keyword": keyword} if keyword else {})})
            page_rows = _items(raw)
            rows.extend(page_rows)
            if not page_rows or len(page_rows) < 20:
                break
        except GatewayError as exc:
            if exc.status == 409:
                raise
            errors.append("多源快讯接口失败")
            break
    coverage.append("多源快讯：最多3页，每页20条")
    if errors and not rows:
        raise GatewayError("南财快讯数据暂时不可用。", 502)
    selected, seen = [], set()
    unknown_time_count = 0
    for row in rows:
        if not isinstance(row, dict):
            unknown_time_count += 1
            continue
        timestamp = _item_timestamp(row)
        if timestamp is None:
            unknown_time_count += 1
            continue
        if timestamp < cutoff:
            continue
        if red and not bool(row.get("important") or row.get("importance")):
            continue
        if keyword and keyword.lower() not in (str(row.get("title") or "") + " " + str(row.get("content") or "")).lower():
            continue
        ident = str(row.get("id") or row.get("articleId") or row.get("title"))
        if ident in seen:
            continue
        seen.add(ident)
        selected.append(row)
    selected.sort(key=lambda row: _item_timestamp(row) or datetime.min.replace(tzinfo=BEIJING), reverse=True)
    result = _result(client, {}, tool="sfc_timeline", rows=selected, limit=limit,
                     coverage={"requestedHours": hours, "limits": coverage, "complete": False,
                               "unknownTimeCount": unknown_time_count, "pageWindowMayOmitMatches": len(rows) >= 60},
                     extra={"partial": bool(errors), "warnings": errors})
    return result


def _search(client, args, context):
    _check_keys(args, {"keyword", "category", "page", "limit"})
    category = _choice(args, "category", {1, 2, 3, 4, 5, 7, 10, 11, 13, 15, 16}, default=4)
    limit = _int(args, "limit")
    page = _int(args, "page", maximum=100, default=1)
    raw = client.request("GET", "/terminal/rest/esSearch/searchkey/page", context,
                         params={"keyword": _text(args, "keyword", required=True), "category": category,
                                 "pageNo": page, "pageSize": limit})
    return _result(client, raw, tool="sfc_search", limit=limit, page=page, extra={"category": category})


def _research(client, args, context):
    allowed = {"title", "source", "author", "type", "startDate", "endDate", "page", "limit"}
    _check_keys(args, allowed)
    params = {"pageNo": _int(args, "page", minimum=0, maximum=100, default=0),
              "pageSize": _int(args, "limit"), "sort": 1}
    for key, api_key in (("title", "report_title"), ("source", "report_source"),
                         ("author", "report_author"), ("type", "report_type")):
        if value := _text(args, key):
            params[api_key] = value
    for key, api_key in (("startDate", "startDate"), ("endDate", "endDate")):
        if value := _date(args, key):
            params[api_key] = value
    if params.get("startDate") and params.get("endDate") and params["startDate"] > params["endDate"]:
        raise GatewayError("起始日期晚于结束日期。", 400)
    if not any(key in params for key in ("report_title", "report_source", "report_author", "report_type")):
        raise GatewayError("请指定研报标题、机构、作者或类型。", 400)
    raw = client.request("GET", "/terminal/sfc-search/api/v1/sprite/searchReport", context, params=params)
    return _result(client, raw, tool="sfc_research", limit=params["pageSize"], page=params["pageNo"])


def _announcements(client, args, context):
    _check_keys(args, {"action", "code", "id", "page", "limit"})
    action = _choice(args, "action", {"list", "detail"}, default="list")
    _check_keys(args, {"action", "id"} if action == "detail" else {"action", "code", "page", "limit"})
    if action == "detail":
        ident = _int(args, "id", maximum=10**15, default=0)
        raw = client.request("GET", "/terminal/rest/company/notice/detail", context, params={"id": ident})
        return _result(client, raw, tool="sfc_announcements", limit=1)
    code = _code(args).split(".")[0]
    limit = _int(args, "limit")
    page = _int(args, "page", maximum=100, default=1)
    raw = client.request("GET", "/terminal/rest/company/notice", context,
                         params={"secuCode": code, "pageNo": page - 1, "pageSize": limit})
    return _result(client, raw, tool="sfc_announcements", limit=limit, page=page)


def _quotes(client, args, context):
    _check_keys(args, {"action", "code", "keyword", "limit"})
    action = _choice(args, "action", {"quote", "search"}, default="quote")
    _check_keys(args, {"action", "keyword", "limit"} if action == "search" else {"action", "code"})
    if action == "search":
        limit = _int(args, "limit")
        raw = client.request("POST", "/terminal/quotes/api/search_v2", context,
                             body={"keyword": _text(args, "keyword", required=True),
                                   "pageIndex": 1, "pageSize": limit})
    else:
        code = _code(args)
        if "." not in code:
            code += ".SH" if code[0] in "69" else ".SZ"
        limit = 1
        raw = client.request("POST", "/terminal/quotes/api/quote", context,
                             body={"id": "quote_request", "topic": "quote", "action": "subscribe",
                                   "code": code, "fields": ["code", "name", "lastPrice", "preClosePrice", "changeRate",
                                     "volume", "amount", "high", "low", "open"]})
    return _result(client, raw, tool="sfc_quotes", rows=_items(raw, key="searchResults" if action == "search" else "quoteItems"),
                   limit=limit, page=1 if action == "search" else None)


def _history(client, args, context):
    _check_keys(args, {"code", "period", "startDate", "endDate", "adjustment", "limit"})
    code = _code(args)
    if "." not in code:
        code += ".SH" if code[0] in "69" else ".SZ"
    period = _choice(args, "period", {"dayk", "weekk", "monthk"}, default="dayk")
    adjustment = _choice(args, "adjustment", {1, 2, 3}, default=1)
    start, end = _date(args, "startDate"), _date(args, "endDate")
    if start and end and start > end:
        raise GatewayError("起始日期晚于结束日期。", 400)
    limit = _int(args, "limit")
    # The live endpoint treats `datetime` as its lower-bound cursor. It also
    # returns the same records for pageIndex 0/1, so iterating pages would
    # duplicate records. The HTTP body cap remains the bounded coverage gate.
    effective_end = datetime.strptime(end, "%Y-%m-%d").date() if end else datetime.now(BEIJING).date()
    lookback = 365 if period == "dayk" else 1095
    query_start = start or (effective_end - timedelta(days=lookback)).isoformat()
    raw = client.request("POST", f"/terminal/quotes/api/{period}", context,
                         body={"code": code, "requestType": adjustment, "mode": 0,
                               "datetime": query_start.replace("-", ""), "pageIndex": 0, "pageSize": 100})
    rows = _items(raw, key="OHLCItems")
    filtered = [row for row in rows if (date := _item_date(row)) and (not start or date >= start) and (not end or date <= end)]
    filtered.sort(key=lambda row: _item_date(row) or "", reverse=True)
    seen_dates, unique = set(), []
    for row in filtered:
        date = _item_date(row)
        if date not in seen_dates:
            seen_dates.add(date)
            unique.append(row)
    all_dates = [date for row in rows if (date := _item_date(row))]
    result = _result(client, {}, tool="sfc_history", rows=unique, limit=limit,
                     coverage={"pagesFetched": 1, "queryStart": query_start,
                               "oldestFetched": min(all_dates) if all_dates else None,
                               "newestFetched": max(all_dates) if all_dates else None,
                               "requestedStart": start or None, "requestedEnd": end or None,
                               "completeForRequestedRange": False,
                               "note": "上游分页未证实有效；日期筛选仅针对本次返回记录。"})
    return result


def _finance(client, args, context):
    _check_keys(args, {"code", "kind", "page", "limit"})
    code = _code(args).split(".")[0]
    kind = _choice(args, "kind", set(_FINANCE_COLLECTIONS), default="index")
    limit = _int(args, "limit")
    page = _int(args, "page", maximum=100, default=1)
    raw = _mongo(client, context, _FINANCE_COLLECTIONS[kind], page=page,
                 limit=limit, filters={"SYMBOL": _filter_eq(code)},
                 sort=[{"field": "ENDDATE", "direction": "DESC"}])
    return _result(client, raw, tool="sfc_finance", limit=limit, page=page, extra={"kind": kind})


def _macro(client, args, context):
    _check_keys(args, {"action", "scope", "keyword", "indicatorCode", "startDate", "endDate", "limit"})
    action = _choice(args, "action", {"categories", "category_search", "series"}, default="categories")
    _check_keys(args, {"action", "indicatorCode", "startDate", "endDate", "limit"} if action == "series"
                else {"action", "scope", "keyword", "limit"} if action == "category_search"
                else {"action", "scope", "limit"})
    scope = _choice(args, "scope", set(_ROOTS), default="china")
    limit = _int(args, "limit")
    if action == "series":
        indicator = _text(args, "indicatorCode", required=True, maximum=32, pattern=r"[A-Za-z0-9_-]+")
        end = _date(args, "endDate") or datetime.now(BEIJING).date().isoformat()
        start = _date(args, "startDate") or (datetime.now(BEIJING) - timedelta(days=365)).date().isoformat()
        if start > end:
            raise GatewayError("起始日期晚于结束日期。", 400)
        payload = {"collectionName": "CBD_INDEX_ECONOMY_VALUE", "pagination": {"page": 1, "size": limit},
                   "filters": {"END_DATE": {"operator": "between", "value": [start, end]},
                               "INDIC_CODE": {"operator": "eq", "value": indicator}},
                   "sort": {"field": "END_DATE", "direction": "DESC"}}
        raw = client.request("POST", _MONGO, context, body=payload)
        return _result(client, raw, tool="sfc_macro", limit=limit, page=1, extra={"indicatorCode": indicator})
    payload = {"collectionName": "BD_INDEX_TREE_2", "pagination": {"page": 1, "size": 100},
               "filters": {"P_INDIC_KEYNO": {"operator": "eq", "value": _ROOTS[scope]}}}
    raw = client.request("POST", _MONGO, context, body=payload)
    rows = _items(raw)
    if action == "category_search":
        keyword = _text(args, "keyword", required=True)
        rows = [row for row in rows if keyword.lower() in str(row.get("INDIC_NAME", "")).lower()]
    return _result(client, raw, tool="sfc_macro", rows=rows, limit=limit, page=1,
                   coverage={"level": "top-level categories only", "pagesFetched": 1},
                   extra={"scope": scope, "action": action})


def _margin(client, args, context):
    _check_keys(args, {"code", "date", "sort", "page", "limit"})
    code = _code(args, required=False)
    date = _date(args, "date")
    latest_verified = not bool(date)
    sort = _choice(args, "sort", set(_MARGIN_SORT), default="margin_buy")
    limit = _int(args, "limit")
    filters = {}
    if code:
        filters["证券代码"] = _filter_eq(code.split(".")[0])
    if not date:
        latest = _mongo(client, context, "fin_buy_repay_data", limit=1,
                        sort=[{"field": "trade_date", "direction": "DESC"}])
        latest_rows = _items(latest)
        date = _item_date(latest_rows[0]) if latest_rows else None
        if not date:
            raise GatewayError("暂无法确定融资融券数据的最新交易日。", 502)
    # The collection stores this field as YYYYMMDD rather than ISO YYYY-MM-DD.
    filters["trade_date"] = _filter_eq(date.replace("-", ""))
    page = _int(args, "page", maximum=100, default=1)
    raw = _mongo(client, context, "fin_buy_repay_data", page=page,
                 limit=limit, filters=filters, sort=[{"field": _MARGIN_SORT[sort], "direction": "DESC"}])
    return _result(client, raw, tool="sfc_margin", limit=limit, page=page,
                   extra={"dateFilter": date, "dateIsLatestVerified": latest_verified})


def _lhb(client, args, context):
    _check_keys(args, {"action", "date", "type", "code", "page", "limit"})
    action = _choice(args, "action", {"summary", "detail"}, default="summary")
    _check_keys(args, {"action", "date", "code", "page", "limit"} if action == "detail"
                else {"action", "date", "type", "page", "limit"})
    date = (_date(args, "date") or datetime.now(BEIJING).date().isoformat()).replace("-", "")
    limit = _int(args, "limit")
    filters = {"TRADEDATE": _filter_eq(date)}
    if action == "summary":
        filters["TYPE"] = _filter_eq(_choice(args, "type", {1, 2, 3, 4}, default=1), field_type="Int32")
        collection, sort = "TQ_QT_BIZUNITTRD_2", [{"field": "AMOUNT", "direction": "DESC"}]
    else:
        if code := _code(args, required=False):
            filters["SYMBOL"] = _filter_eq(code.split(".")[0])
        collection, sort = "TQ_QT_BIZUNITTRD_DETAIL_2", [
            {"field": "SYMBOL", "direction": "ASC"}, {"field": "TRANSDIR", "direction": "ASC"}]
    page = _int(args, "page", maximum=100, default=1)
    raw = _mongo(client, context, collection, page=page,
                 limit=limit, filters=filters, sort=sort)
    return _result(client, raw, tool="sfc_lhb", limit=limit, page=page,
                   extra={"tradeDate": f"{date[:4]}-{date[4:6]}-{date[6:]}"})


def _calendar(client, args, context):
    _check_keys(args, {"action", "date", "importantOnly", "limit"})
    action = _choice(args, "action", {"eco", "fin", "company", "ipo", "highlights"}, default="eco")
    _check_keys(args, {"action", "date", "importantOnly", "limit"} if action in {"eco", "fin"}
                else {"action", "limit"} if action == "ipo" else {"action", "date", "limit"})
    date = _date(args, "date") or datetime.now(BEIJING).date().isoformat()
    limit = _int(args, "limit")
    if action in {"eco", "fin"}:
        raw = client.request("GET", "/terminal/zxt-web/investCalendar/list", context,
                             params={"eventDate": date, "isroll": "false",
                                     "onlyImportant": str(_bool(args, "importantOnly")).lower(),
                                     "rolltype": 1, "type": ""})
        days = _items(raw)
        field = "ecoData" if action == "eco" else "finEvents"
        rows = [{"date": day.get("nowDate") or date, **item} for day in days if isinstance(day, dict)
                for item in (day.get(field) or []) if isinstance(item, dict)]
    elif action == "company":
        raw = client.request("POST", _MONGO, context,
                             body={"collectionName": "EVENT_REMINDER",
                                   "filters": {"REMINDERDATE": {"operator": "eq", "type": "Date", "value": date}},
                                   "sortFields": [], "groupBy": "PARENTNAME"})
        rows = _items(raw)
    elif action == "ipo":
        raw = client.request("POST", "/terminal/desk/quotes/api/newsharescal", context, body={})
        data = raw.get("data") if isinstance(raw, dict) else raw
        rows = data.get("wsslist", []) if isinstance(data, dict) else data
        if not isinstance(rows, list):
            raise GatewayError("新股日历数据结构无效。", 502)
    else:
        raw = client.request("GET", "/terminal/rest/article/flash", context, auth="bearer",
                             params={"today": date, "forward": "false", "subcat": 0,
                                     "important": "true", "risk": "false", "scene": 1})
        rows = _items(raw)
    return _result(client, raw, tool="sfc_calendar", rows=rows, limit=limit,
                   extra={"date": None if action == "ipo" else date, "category": action})


def _themes(client, args, context):
    _check_keys(args, {"action", "keyword", "themeId", "type", "limit"})
    action = _choice(args, "action", {"recommend", "list", "detail", "stocks", "search"}, default="recommend")
    _check_keys(args, {"action", "type", "limit"} if action == "recommend" else
                {"action", "limit"} if action == "list" else
                {"action", "themeId"} if action == "detail" else
                {"action", "themeId", "limit"} if action == "stocks" else
                {"action", "keyword", "limit"})
    limit = _int(args, "limit")
    paths = {"recommend": "/terminal/rest/theme/recomend", "list": "/terminal/rest/theme/list",
             "detail": "/terminal/rest/theme/detail", "stocks": "/terminal/rest/theme/stocks/list",
             "search": "/terminal/rest/theme/search"}
    params = {"pageSize": limit} if action != "detail" else {}
    if action == "recommend":
        params["type"] = _choice(args, "type", {2, 3, 4}, default=2)
    if action in {"detail", "stocks"}:
        params["themeId"] = _int(args, "themeId", maximum=10**15, default=0)
    if action == "stocks":
        params["leader_yn"] = 0
    if action == "search":
        params["keyword"] = _text(args, "keyword", required=True)
    raw = client.request("GET", paths[action], context, params=params)
    return _result(client, raw, tool="sfc_themes", limit=limit, extra={"action": action})


_TOOL_DEFS = (
    ("sfc_news", "查询南方财经最新资讯、关键词新闻和个股资讯。", _news,
     _schema({"action": _string("recent 最新；search 关键词；stock_articles 个股", choices=["recent", "search", "stock_articles"]),
              "keyword": _string("搜索关键词"), "code": _string("六位股票代码，可带交易所后缀", 9),
              "limit": _integer("返回条数"), "page": _integer("页码", 100)})),
    ("sfc_timeline", "查询近 1 至 48 小时财经快讯，最多覆盖有限页面。", _timeline,
     _schema({"keyword": _string("可选关键词"), "hours": _integer("回溯小时", 48),
              "limit": _integer("返回条数"), "redOnly": {"type": "boolean", "description": "仅标红快讯"}})),
    ("sfc_search", "搜索南财金融资讯、股票、基金、研报等已支持类别。", _search,
     _schema({"keyword": _string("搜索关键词"), "category": {"type": "integer", "enum": [1, 2, 3, 4, 5, 7, 10, 11, 13, 15, 16]},
              "page": _integer("页码", 100), "limit": _integer("返回条数")}, ["keyword"])),
    ("sfc_research", "按标题、机构、作者或类型查询南财研报。", _research,
     _schema({"title": _string("标题关键词"), "source": _string("研究机构"), "author": _string("分析师"),
              "type": _string("报告类型"), "startDate": _string("YYYY-MM-DD", 10),
              "endDate": _string("YYYY-MM-DD", 10), "page": _integer("零起始页码", 100, 0),
              "limit": _integer("返回条数")})),
    ("sfc_announcements", "查询上市公司公告列表或公告详情。", _announcements,
     _schema({"action": _string("list 或 detail", choices=["list", "detail"]),
              "code": _string("六位股票代码", 9), "id": _integer("公告ID", 10**15),
              "page": _integer("页码", 100), "limit": _integer("返回条数")})),
    ("sfc_quotes", "查询单只股票行情或搜索股票名称与代码。", _quotes,
     _schema({"action": _string("quote 或 search", choices=["quote", "search"]),
              "code": _string("六位股票代码", 9), "keyword": _string("股票名称或代码"),
              "limit": _integer("返回条数")})),
    ("sfc_history", "查询股票日、周、月 K 线，可按日期筛选；返回覆盖范围和分页上限。", _history,
     _schema({"code": _string("六位股票代码", 9),
              "period": _string("日、周、月", choices=["dayk", "weekk", "monthk"]),
              "startDate": _string("YYYY-MM-DD", 10), "endDate": _string("YYYY-MM-DD", 10),
              "adjustment": {"type": "integer", "enum": [1, 2, 3]}, "limit": _integer("返回条数")}, ["code"])),
    ("sfc_finance", "查询个股财务指标、资产负债表、利润表、现金流量表，按报告截止日降序。", _finance,
     _schema({"code": _string("六位股票代码", 9),
              "kind": _string("财报类型", choices=list(_FINANCE_COLLECTIONS)),
              "page": _integer("页码", 100), "limit": _integer("返回条数")}, ["code"])),
    ("sfc_macro", "查询宏观顶层分类、顶层分类名称或已知指标代码的序列。", _macro,
     _schema({"action": _string("categories/category_search/series", choices=["categories", "category_search", "series"]),
              "scope": _string("地区或类别", choices=list(_ROOTS)), "keyword": _string("顶层分类名称关键词"),
              "indicatorCode": _string("已知指标代码", 32), "startDate": _string("YYYY-MM-DD", 10),
              "endDate": _string("YYYY-MM-DD", 10), "limit": _integer("返回条数")})),
    ("sfc_margin", "查询融资融券榜单或单只股票记录；请核对返回交易日。", _margin,
     _schema({"code": _string("可选六位股票代码", 9), "date": _string("YYYY-MM-DD", 10),
              "sort": _string("排序字段", choices=list(_MARGIN_SORT)), "page": _integer("页码", 100),
              "limit": _integer("返回条数")})),
    ("sfc_lhb", "查询指定交易日龙虎榜汇总或明细。", _lhb,
     _schema({"action": _string("summary 或 detail", choices=["summary", "detail"]),
              "date": _string("YYYY-MM-DD", 10), "type": {"type": "integer", "enum": [1, 2, 3, 4]},
              "code": _string("明细股票代码", 9), "page": _integer("页码", 100), "limit": _integer("返回条数")})),
    ("sfc_calendar", "查询经济数据、投资事件、公司事件、新股和重点新闻日历。", _calendar,
     _schema({"action": _string("日历种类", choices=["eco", "fin", "company", "ipo", "highlights"]),
              "date": _string("YYYY-MM-DD", 10), "importantOnly": {"type": "boolean", "description": "仅重要事件"},
              "limit": _integer("返回条数")})),
    ("sfc_themes", "查询推荐主题、主题列表、详情、成分股或搜索主题。", _themes,
     _schema({"action": _string("主题动作", choices=["recommend", "list", "detail", "stocks", "search"]),
              "keyword": _string("搜索关键词"), "themeId": _integer("主题ID", 10**15),
              "type": {"type": "integer", "enum": [2, 3, 4]}, "limit": _integer("返回条数")})),
)


def build_sfc_tools(config, *, client=None) -> list[ToolSpec]:
    """Explicit composition point; unavailable tools are hidden without a key."""
    client = client if client is not None else SFCClient(config)
    return [ToolSpec(name=name, description=description, parameters=schema,
                     handler=lambda args, context, function=function: function(client, args, context),
                     capabilities=frozenset({"financial_data"}), available=lambda: client.available,
                     max_calls=3)
            for name, description, function, schema in _TOOL_DEFS]
