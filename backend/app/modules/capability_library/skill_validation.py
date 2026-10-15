"""Bounded model-only retries and durable evidence for explicit Skill tests."""
import copy
import contextvars
import inspect
import queue
import threading
import time

from ..workbench.errors import GatewayError
from .contracts import canonical, digest, fail, now, uid


class ValidationBudget:
    def __init__(self, seconds=300, requests=6):
        self.deadline = time.monotonic() + seconds
        self.seconds, self.limit, self.used = seconds, requests, 0

    def check(self, stopped):
        if stopped.is_set():
            fail('CANCELLED', '验证已取消，未继续请求模型或执行工具。', 409)
        if time.monotonic() >= self.deadline:
            fail('VALIDATION_DEADLINE', '验证达到整体时间上限；已保留实际调用记录，本次未通过验证。', 504)


class _Stop:
    def __init__(self, parent):
        self.parent, self.local = parent, threading.Event()

    def is_set(self):
        return self.parent.is_set() or self.local.is_set()

    def wait(self, timeout=None):
        end = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            if end is not None and time.monotonic() >= end:
                return False
            self.local.wait(.02 if end is None else min(.02, max(0, end-time.monotonic())))
        return True


class BudgetStop(_Stop):
    def __init__(self, parent, budget):
        super().__init__(parent)
        self.budget = budget

    def is_set(self):
        return super().is_set() or time.monotonic() >= self.budget.deadline

    def set(self):
        self.local.set()


def _abort(response):
    if response is not None:
        try:
            response.abort()
        except Exception:
            pass


def model_turn(library, messages, model, stopped, on_response, tools, budget, round_number):
    """Accept one complete turn; a timed-out partial turn never reaches tools."""
    input_chars = len(canonical(messages)) + len(canonical(tools))
    if input_chars > 180000:
        fail('MODEL_CONTEXT_TOO_LARGE', '验证模型上下文超过180000字符，请缩小测试输入。', 413)
    for attempt in (1, 2):
        budget.check(stopped)
        if budget.used >= budget.limit:
            fail('MODEL_REQUEST_LIMIT', '验证已达到6次模型请求上限，本次未通过验证。', 429)
        while not library.validation_model_slots.acquire(timeout=.05):
            budget.check(stopped)
        try:
            budget.check(stopped)
        except Exception:
            library.validation_model_slots.release()
            raise
        budget.used += 1
        row = {'requestId': uid('model'), 'round': round_number, 'attempt': attempt,
               'requestNumber': budget.used, 'status': 'running', 'stage': 'awaiting_headers',
               'startedAt': now(), 'inputChars': input_chars, 'toolCount': len(tools),
               'contextDigest': digest({'messages': messages, 'tools': tools}),
               'limits': {'totalSeconds': budget.seconds, 'modelRequests': budget.limit}}
        pending = queue.Queue(maxsize=64)
        local_stop = _Stop(stopped)
        response_lock, response = threading.Lock(), [None]
        provider = library.workbench.runtime.provider
        # Per-request limits shrink with the task budget; shared provider settings
        # and configured timeouts are never lengthened or mutated.
        from ..workbench.graph_runtime import GiteeProvider
        if isinstance(provider, GiteeProvider):
            provider = copy.copy(provider)
            remaining = max(.001, budget.deadline - time.monotonic())
            provider.connect_timeout = min(provider.connect_timeout, remaining)
            provider.read_timeout = min(provider.read_timeout, remaining)

        def send(kind, value):
            while not local_stop.is_set():
                try:
                    pending.put((kind, value), timeout=.05)
                    return
                except queue.Full:
                    pass

        def changed(value):
            with response_lock:
                response[0] = value
            if local_stop.is_set():
                _abort(value)
                return
            if value is not None:
                send('headers', {})
            on_response(value)

        def trace(kind, value):
            if kind == 'transport':
                send('transport', {k: v for k, v in value.items()
                                   if k in {'provider', 'model', 'httpStatus', 'requestId', 'responseId', 'usage', 'finishReason'}})

        def worker():
            stream = None
            try:
                parameters = inspect.signature(provider.events).parameters
                options = {'on_trace': trace} if 'on_trace' in parameters else {}
                stream = provider.events(messages, model, local_stop, changed, tools=tools, **options)
                for event in stream:
                    if local_stop.is_set():
                        break
                    send('event', event)
                send('done', None)
            except Exception as error:
                send('error', error)
            finally:
                try:
                    close = getattr(stream, 'close', None)
                    if close:
                        close()
                finally:
                    library.validation_model_slots.release()

        # The unified authoring budget counts nested provider calls in this worker.
        context = contextvars.copy_context()
        thread = threading.Thread(target=context.run, args=(worker,), name='capability-validation-model', daemon=True)
        try:
            thread.start()
        except Exception:
            library.validation_model_slots.release()
            raise
        events, partial, length, complete, error = [], [], 0, False, None
        try:
            yield {'type': 'validation_model', 'request': copy.deepcopy(row)}
            while True:
                budget.check(stopped)
                try:
                    kind, value = pending.get(timeout=min(.05, max(.001, budget.deadline-time.monotonic())))
                except queue.Empty:
                    continue
                if kind == 'headers':
                    row['stage'] = 'streaming'
                elif kind == 'transport':
                    row.setdefault('transport', {}).update(value)
                elif kind == 'error':
                    raise value
                elif kind == 'done':
                    if not complete:
                        fail('MODEL_INCOMPLETE', '模型本轮没有完整结束，未接受部分工具调用。', 502)
                    break
                elif kind == 'event':
                    length += len(canonical(value))
                    if length > 512 * 1024:
                        fail('MODEL_OUTPUT_LIMIT', '验证模型本轮输出超过512 KiB限制。', 502)
                    if value.get('type') == 'token':
                        partial.append(value['text'])
                        row['stage'] = 'generating'
                    if value.get('type') == 'turn_end':
                        complete = True
                    events.append(value)
        except Exception as caught:
            error = caught
        finally:
            local_stop.local.set()
            with response_lock:
                current = response[0]
            _abort(current)
            row.update(status='cancelled' if stopped.is_set() else ('failed' if error else 'succeeded'),
                       completedAt=now(), outputChars=sum(len(p) for p in partial))
            if error:
                row.update(error={'code': getattr(error, 'code', None) or ('MODEL_TIMEOUT' if getattr(error, 'status', None) == 504 else 'MODEL_FAILED'),
                                  'error': str(error)[:1000] if isinstance(error, GatewayError) else '模型请求失败，请检查服务端记录。'},
                           partialOutputDigest=digest(''.join(partial)), partialOutputAccepted=False)
        yield {'type': 'validation_model', 'request': copy.deepcopy(row)}
        budget.check(stopped)
        if error is None:
            yield from events
            return
        retryable = isinstance(error, GatewayError) and error.status == 504 and getattr(error, 'code', None) != 'VALIDATION_DEADLINE'
        if retryable and attempt == 1 and budget.used < budget.limit:
            yield {'type': 'validation_model_retry', 'requestId': row['requestId'],
                   'message': '本轮模型请求超时，仅重试模型；保留已完成工具结果，不重放工具。'}
            continue
        if retryable:
            fail('MODEL_TIMEOUT', '模型连接或响应超时，同轮有界重试未恢复；本次未通过验证。', 504,
                 {'stage': row['stage'], 'modelRequestId': row['requestId']})
        raise error


def collect_skill(library, owner, version, prompt, file_ids, stopped, record, persist, *, inputs=None, budget=None):
    """Persist failures as well as actual completed calls; never bless partial text."""
    budget = budget or ValidationBudget()
    record.setdefault('executionEvidence', [])
    record.setdefault('modelRequests', [])
    chunks = []
    stream = library.chat_events(owner, [{'assetId': version['assetId'], 'versionId': version['versionId']}],
                prompt, [], stopped, lambda _: None, file_ids, trial=True, skill_inputs=inputs,
                validation_budget=budget)
    try:
        for event in stream:
            kind = event['type']
            if kind == 'token':
                chunks.append(event['text'])
            elif kind == 'validation_model':
                request = event['request']
                index = next((i for i, r in enumerate(record['modelRequests']) if r['requestId'] == request['requestId']), None)
                if index is None:
                    record['modelRequests'].append(request)
                else:
                    record['modelRequests'][index] = request
                persist()
            elif kind in {'tool_completed', 'tool_failed', 'skill_completed'}:
                record['executionEvidence'].append(copy.deepcopy(event))
                persist()
        budget.check(stopped)
        return {'content': ''.join(chunks)}
    except Exception as error:
        record['result'] = {'content': ''.join(chunks), 'incomplete': True}
        record['completedAt'] = now()
        info = {'code': getattr(error, 'code', None) or 'EXECUTION_FAILED', 'error': str(error),
                'completedToolCalls': sum(e['type'] == 'tool_completed' for e in record['executionEvidence'])}
        if record['modelRequests']:
            info['stage'] = record['modelRequests'][-1]['stage']
        record['error'] = info
        persist()
        raise
    finally:
        stream.close()
