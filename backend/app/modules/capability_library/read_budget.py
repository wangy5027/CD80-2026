"""Host-only task accounting; never accepted from model/tool arguments."""
from contextlib import contextmanager
from contextvars import ContextVar

_charge = ContextVar('capability_read_charge', default=None)
_scope = ContextVar('capability_read_scope', default=None)


@contextmanager
def task_reads(charge, scope=None):
    token = _charge.set(charge)
    scope_token = _scope.set(scope)
    try:
        yield
    finally:
        _charge.reset(token)
        _scope.reset(scope_token)


def current_scope():
    return _scope.get()


def charge(reads=0, rows=0):
    callback = _charge.get()
    if callback:
        from ..workbench.errors import GatewayError
        from .contracts import fail
        try:
            callback(reads, rows)
        except GatewayError as error:
            if error.code == 'READ_BUDGET_EXCEEDED':
                fail(error.code,str(error),429)
            raise
