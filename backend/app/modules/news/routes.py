"""Same-origin read-only news routes, protected by the existing website login."""

import re

from flask import Blueprint, current_app, jsonify, request

from ..workbench.errors import GatewayError


news = Blueprint("news", __name__)
_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


def _service():
    current_app.extensions["workbench_service"].authenticate(request.headers.get("Cookie", ""))
    if request.method not in {"GET", "HEAD"}:
        raise GatewayError("新闻接口只支持 GET 请求。", 405)
    return current_app.extensions["news_service"]


def _arguments(allowed):
    if any(key not in allowed or len(request.args.getlist(key)) != 1 for key in request.args):
        raise GatewayError("新闻查询包含不支持或重复的参数。", 400)


def _integer(name, default, minimum, maximum):
    value = request.args.get(name, str(default))
    if not re.fullmatch(r"(?:0|[1-9][0-9]{0,4})", value) or not minimum <= int(value) <= maximum:
        raise GatewayError(f"{name} 必须是 {minimum} 到 {maximum} 的整数。", 400)
    return int(value)


@news.route("/desktop/api/news", methods=_METHODS)
def latest_news():
    service = _service()
    _arguments({"source", "limit", "offset", "q"})
    source = request.args.get("source", "eastmoney")
    if source not in {"eastmoney", "cls"}:
        raise GatewayError("source 只支持 eastmoney 或 cls。", 400)
    raw_query = request.args.get("q", "")
    query = raw_query.strip()
    if len(raw_query) > 80 or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_query):
        raise GatewayError("q 最多 80 个字符，不能包含控制字符。", 400)
    return jsonify(service.get_news(source=source, limit=_integer("limit", 20, 1, 100),
                                    offset=_integer("offset", 0, 0, 1000), q=query))


@news.route("/desktop/api/news/sources", methods=_METHODS)
def news_sources():
    service = _service()
    _arguments(set())
    return jsonify(service.get_sources())
