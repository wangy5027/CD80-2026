"""One persistent allowance for authoring, validation and nested model calls."""
import contextvars
import threading
import time
from contextlib import contextmanager

from ..capability_library.contracts import fail, now, uid
from ..sub_agent.diagnostics import bounded_record

_CURRENT = contextvars.ContextVar('authoring_budget', default=None)
LIMITS = {'activeMilliseconds': 600000, 'modelRequests': 32, 'repairRounds': 2}


def current_budget():
    return _CURRENT.get()


def new_round(session_id, previous=None, confirmation=None):
    return {'id': uid('budget'), 'sessionId': session_id, 'number': (previous['number'] + 1 if previous else 1),
            'status': 'active', 'limits': dict(LIMITS), 'consumed': {key: 0 for key in LIMITS},
            'reservedRequests': 0, 'previousRoundId': previous['id'] if previous else None,
            'extensionConfirmationId': confirmation, 'startedAt': now(), 'closedAt': None}


class BudgetContext:
    def __init__(self, store, owner, session_id, round_id, attempt_id, stop=None, clock=time.monotonic, revision=None):
        self.store, self.owner, self.session_id = store, owner, session_id
        self.round_id, self.attempt_id = round_id, attempt_id
        self.revision = revision
        self.stop = stop or threading.Event()
        self.clock, self.started, self.base = clock, None, 0
        self.responses, self.lock = set(), threading.RLock()
        self.expired = False
        self.request_denied = False
        self.timer = None

    @contextmanager
    def bind(self):
        with self.store.transaction() as db:
            budget = self.store.get(db, 'budget_rounds', self.owner, self.round_id)
            self.base = budget['consumed']['activeMilliseconds']
            self.started = self.clock()
            budget['activeSince'] = now()
            self.store.put(db, 'budget_rounds', self.owner, budget)
        token = _CURRENT.set(self)
        self.timer = threading.Timer(max(0, (LIMITS['activeMilliseconds'] - self.base) / 1000), self._expire)
        self.timer.daemon = True
        self.timer.start()
        try:
            self.check()
            yield self
        finally:
            self.timer.cancel()
            self.checkpoint(close=True)
            _CURRENT.reset(token)

    def _expire(self):
        self.expired = True
        self.set()

    def set(self):
        self.stop.set()
        with self.lock:
            values = list(self.responses)
        for response in values:
            try:
                response.abort()
            except Exception:
                pass

    def is_set(self):
        if self.started is not None and self.remaining_seconds() <= 0:
            self.expired = True
        return self.stop.is_set() or self.expired or self.request_denied

    def wait(self, timeout=None):
        return self.stop.wait(min(timeout or self.remaining_seconds(), max(0, self.remaining_seconds()))) or self.is_set()

    def remaining_seconds(self):
        elapsed = max(0, self.clock() - self.started) if self.started is not None else 0
        return max(0, (LIMITS['activeMilliseconds'] - self.base) / 1000 - elapsed)

    def check(self):
        if self.expired or (self.started is not None and self.remaining_seconds() <= 0):
            self.expired = True
            fail('AUTHORING_DEADLINE', '本轮有效执行时间已用完，草稿和结果已保留。', 504)
        if self.request_denied:
            fail('BUDGET_EXHAUSTED', '本轮模型请求额度已用完，草稿和结果已保留。', 409)
        if self.stop.is_set():
            fail('AUTHORING_CANCELLED', '已停止当前处理，草稿已保留。', 409)

    def checkpoint(self, close=False):
        if self.started is None:
            return
        with self.store.transaction() as db:
            budget = self.store.get(db, 'budget_rounds', self.owner, self.round_id)
            used = self.base + int(max(0, self.clock() - self.started) * 1000)
            budget['consumed']['activeMilliseconds'] = min(LIMITS['activeMilliseconds'], used)
            if close:
                budget.pop('activeSince', None)
            self.store.put(db, 'budget_rounds', self.owner, budget)

    def response(self, response):
        if response is not None:
            with self.lock:
                self.responses.add(response)
            if self.is_set():
                response.abort()

    def forget_response(self, response):
        with self.lock:
            self.responses.discard(response)

    def begin_request(self, provider, model, request_payload=None):
        self.check()
        self.checkpoint()
        trace = bounded_record(request_payload) if request_payload is not None else None
        with self.store.transaction() as db:
            budget = self.store.get(db, 'budget_rounds', self.owner, self.round_id)
            if budget['consumed']['modelRequests'] + budget.get('reservedRequests', 0) >= LIMITS['modelRequests']:
                self.request_denied = True
                fail('BUDGET_EXHAUSTED', '本轮模型请求额度已用完，草稿和结果已保留。', 409)
            budget['consumed']['modelRequests'] += 1
            self.store.put(db, 'budget_rounds', self.owner, budget)
            call = {'id': uid('model'), 'sessionId': self.session_id, 'attemptId': self.attempt_id,
                    'budgetRoundId': self.round_id, 'provider': provider, 'model': model,
                    'revision': self.revision, 'state': 'sent', 'sentAt': now(), 'usageStatus': 'unknown'}
            if trace is not None:
                call['traceId'] = uid('trace')
                self.store.put(db, 'model_traces', self.owner, {
                    'id': call['traceId'], 'sessionId': self.session_id, 'attemptId': self.attempt_id,
                    'requestId': call['id'], 'revision': self.revision, 'provider': provider,
                    'model': model, 'request': trace, 'createdAt': now()})
            self.store.put(db, 'model_requests', self.owner, call)
            return call['id']

    def finish_request(self, request_id, *, succeeded=False, usage=None, unknown=False):
        with self.store.transaction() as db:
            call = self.store.get(db, 'model_requests', self.owner, request_id)
            call.update(state='unknown' if unknown else 'succeeded' if succeeded else 'failed', completedAt=now())
            if usage:
                call.update(usage=usage, usageStatus='reported')
            self.store.put(db, 'model_requests', self.owner, call)

    def repair(self, candidate_id, evidence_ids):
        self.check()
        with self.store.transaction() as db:
            budget = self.store.get(db, 'budget_rounds', self.owner, self.round_id)
            if budget['consumed']['repairRounds'] >= LIMITS['repairRounds']:
                fail('BUDGET_EXHAUSTED', '本轮自动修复次数已用完，草稿和检查结果已保留。', 409)
            budget['consumed']['repairRounds'] += 1
            self.store.put(db, 'budget_rounds', self.owner, budget)
            repair = {'id': uid('repair'), 'sessionId': self.session_id, 'budgetRoundId': self.round_id,
                      'number': budget['consumed']['repairRounds'], 'causeEvidenceIds': evidence_ids,
                      'candidateBeforeId': candidate_id, 'status': 'running', 'startedAt': now()}
            self.store.put(db, 'repair_rounds', self.owner, repair)
            return repair['id']
