"""Run-scoped Explore evidence IDs and bounded, authorized continuation reads."""

from __future__ import annotations

from ..workbench.errors import GatewayError
from ..workbench.tool_registry import ToolResult, ToolSpec


class EvidenceSession:
    """Only material registered for this execution or frozen at run creation is readable."""

    def __init__(self, repository, owner: str, run: dict, execution_id: str | None = None):
        self.repository = repository
        self.owner = owner
        self.run_id = run["id"]
        self.execution_id = execution_id or run.get("execution_id")
        if not isinstance(self.execution_id, str) or not self.execution_id:
            raise GatewayError("研究运行标识无效。", 409)

    def add_source(self, source: dict) -> dict:
        return self.repository.register_evidence(
            self.owner, self.run_id, source, execution_id=self.execution_id)

    def sources(self) -> list[dict]:
        return self.repository.active_evidence_sources(
            self.owner, self.run_id, execution_id=self.execution_id)

    def selected_catalog(self) -> list[dict]:
        return self.repository.selected_evidence(
            self.owner, self.run_id, execution_id=self.execution_id)

    def read(self, arguments: dict) -> ToolResult:
        if not isinstance(arguments, dict) or set(arguments) - {"evidence_id", "offset", "limit"}:
            raise GatewayError("证据读取参数无效。", 400)
        result = self.repository.read_evidence(
            self.owner, self.run_id, arguments.get("evidence_id"),
            offset=arguments.get("offset", 0), limit=arguments.get("limit", 4000),
            execution_id=self.execution_id)
        return ToolResult(content=result)

    def tool_spec(self) -> ToolSpec:
        return ToolSpec(
            name="read_evidence",
            description="仅按本轮授权证据编号读取指定正文范围；返回固定内容哈希、位置和续读起点。旧 AI 报告只作背景。",
            parameters={
                "type": "object",
                "properties": {
                    "evidence_id": {"type": "string", "description": "本轮来源的 evidence_id"},
                    "offset": {"type": "integer", "minimum": 0, "description": "从零开始的字符偏移"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 6000,
                              "description": "本次最多读取的字符数，默认 4000"},
                },
                "required": ["evidence_id"],
                "additionalProperties": False,
            },
            handler=lambda arguments, _context: self.read(arguments),
            capabilities=frozenset({"research_evidence"}),
            max_calls=24,
        )
