"""Bounded JSON contracts shared by HTTP, jobs, and conversation consumers."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import uuid4

from ..workbench.errors import GatewayError


class CapabilityError(GatewayError):
    def __init__(self, code, message, status=400, details=None):
        super().__init__(message, status)
        self.code = code
        self.details = details or {}
        if self.details.get('invocationId'):
            self.invocationId = self.details['invocationId']


def fail(code, message, status=400, details=None):
    raise CapabilityError(code, message, status, details)


def uid(prefix):
    return prefix + '_' + uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else canonical(value).encode()).hexdigest()


def parse_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        fail('INVALID_PACKAGE', 'JSON 无效、键重复或嵌套过深。')


def issue(code, message, severity='blocker', path='', **extra):
    return dict(code=code, message=message, severity=severity, path=path, **extra)


TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted', 'unknown'}
