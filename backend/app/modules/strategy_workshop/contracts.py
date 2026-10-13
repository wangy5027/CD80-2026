"""Small, strict contracts shared by the workshop HTTP and storage layers."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from ..workbench.errors import GatewayError

TERMINAL = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
ACTIVE = frozenset({"queued", "validating", "running", "cancelling"})


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def custom_runtime_digest():
    """Bind replay to the exact trusted compiler and guest bootstrap release."""
    from pathlib import Path
    root = Path(__file__).parent
    return digest({name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in ("custom_rules.py", "custom_sandbox.py")})


def fail(message, status=400, code="invalid_request", field=None, stage=None, retryable=False):
    error = GatewayError(message, status, code)
    error.field, error.stage, error.retryable = field, stage, retryable
    raise error


def fields(body, allowed, required=()):
    if not isinstance(body, dict):
        fail("请求必须为 JSON 对象。")
    unexpected = set(body) - set(allowed)
    missing = set(required) - set(body)
    if unexpected:
        fail("请求不支持这些输入字段：" + "、".join(sorted(unexpected)), field=sorted(unexpected)[0])
    if missing:
        fail("缺少必要字段：" + "、".join(sorted(missing)), field=sorted(missing)[0])


def key(body):
    value = body.get("idempotencyKey")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", value):
        fail("请求标识须为 8–128 个字母、数字或 ._:-。", field="idempotencyKey")
    return value


def idea(body):
    value = body.get("idea")
    if not isinstance(value, str) or not value.strip() or len(value) > 4000:
        fail("请用 1–4000 个字符描述投资想法。", field="idea")
    return value.strip()


def revision(value, name):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        fail("修订号不正确。", field=name)
    return value


def object_id(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        fail("记录标识不正确。", field=name)
    return value


def sha256_value(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
        fail("确认摘要格式不正确。", field=name)
    return value
