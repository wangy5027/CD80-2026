"""Owner-scoped actual model messages, independent of generated rule validity."""
from uuid import uuid4
import json

from ..sub_agent.diagnostics import ModelTrace, bounded_record
from .contracts import canonical, fail, now


class TraceFactory:
    def __init__(self, repository, owner, stage):
        self.repository, self.owner, self.stage = repository, owner, stage
        self.ids = []

    def __call__(self):
        trace = ModelTrace()
        record = {"id": "strategy_call_" + uuid4().hex, "stage": self.stage,
                  "retryOf": self.ids[-1] if self.ids else None, "startedAt": now(), "status": "running"}
        self.ids.append(record["id"])
        def capture(event, value):
            if event == "finished":
                record.update(status=value, completedAt=now())
            else:
                trace.receive(event, value)
            with self.repository.transaction() as db:
                db.execute("INSERT OR REPLACE INTO workshop_model_traces VALUES(?,?,?)", (
                    record["id"], self.owner,
                    canonical({**record, "detail": bounded_record(trace.record(), max_chars=1000000)})))
        return capture


def read_trace(repository, owner, trace_id):
    with repository.transaction() as db:
        row = db.execute("SELECT doc FROM workshop_model_traces WHERE owner=? AND id=?", (owner, trace_id)).fetchone()
        if not row:
            fail("调用记录不存在或无权访问。", 404, "not_found")
        return json.loads(row["doc"])
