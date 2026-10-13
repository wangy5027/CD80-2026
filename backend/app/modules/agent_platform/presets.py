"""Built-in application definitions, executed by the same runner as personal agents.

Registration validates definitions only. It never imports or executes the source
stored in a tool, reads an input file, or calls the model provider.
"""
from __future__ import annotations

import copy


CSV_SUMMARY_REF = "csv-summary@1"

CSV_SUMMARY_CODE = r'''import csv
import re
from decimal import Decimal, InvalidOperation, Inexact, localcontext

NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")

def run(payload):
    group_column = payload["groupBy"]
    value_column = payload["valueColumn"]
    if payload.get("aggregation", "sum") != "sum":
        raise ValueError("此应用只支持求和（sum）")
    totals = {}
    row_count = 0
    with localcontext() as context:
        # Bounded decimal inputs + row count fit exactly in this precision.
        # Inexact is trapped so unsupported magnitudes never silently round.
        context.prec = 128
        context.traps[Inexact] = True
        total = Decimal(0)
        with open(payload["filePath"], newline="", encoding="utf-8-sig") as stream:
            rows = csv.reader(stream, strict=True)
            headers = next(rows, None)
            if not headers or any(not name.strip() for name in headers):
                raise ValueError("CSV 必须有非空列名，不能是空文件")
            if len(set(headers)) != len(headers):
                raise ValueError("CSV 含重复列名，请先修正")
            missing = [name for name in (group_column, value_column) if name not in headers]
            if missing:
                raise ValueError("CSV 缺少指定列：" + "、".join(missing))
            group_index = headers.index(group_column)
            value_index = headers.index(value_column)
            for row in rows:
                row_count += 1
                line = rows.line_num
                if row_count > 100000:
                    raise ValueError("CSV 数据行超过 100000 行上限")
                if len(row) != len(headers):
                    raise ValueError("CSV 第 %d 行与表头列数不一致" % line)
                group = row[group_index]
                if not group.strip():
                    raise ValueError("CSV 第 %d 行分组值为空" % line)
                if len(group) > 80:
                    raise ValueError("CSV 第 %d 行分组值超过 80 字符" % line)
                raw_value = row[value_index].strip()
                if not raw_value:
                    raise ValueError("CSV 第 %d 行数值为空" % line)
                if len(raw_value) > 128 or NUMBER.fullmatch(raw_value) is None:
                    raise ValueError("CSV 第 %d 行不是有效有限数字" % line)
                try:
                    value = Decimal(raw_value)
                except InvalidOperation:
                    raise ValueError("CSV 第 %d 行不是有效有限数字" % line) from None
                if not value.is_finite():
                    raise ValueError("CSV 第 %d 行数字必须为有限值" % line)
                digits = value.as_tuple()
                if len(digits.digits) > 50 or not -28 <= digits.exponent <= 28:
                    raise ValueError("CSV 第 %d 行超出数值范围（50 位有效数字，小数指数 -28 至 28）" % line)
                if group not in totals and len(totals) >= 5000:
                    raise ValueError("CSV 分组超过 5000 组上限")
                totals[group] = totals.get(group, Decimal(0)) + value
                total += value
        if not row_count:
            raise ValueError("CSV 只有表头，没有数据行")
        # Fixed-point text preserves Decimal precision and avoids binary floats.
        groups = sorted(totals)
        with open("/output/summary.csv", "w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream, lineterminator="\n")
            writer.writerow([group_column, "total_amount"])
            for group in groups:
                writer.writerow([group, format(totals[group], "f")])
        preview = [{"group": group, "total": format(totals[group], "f")} for group in groups[:20]]
        return {"artifacts": ["summary.csv"], "fileId": payload["fileId"],
                "groupBy": group_column, "valueColumn": value_column,
                "rowCount": row_count, "groupCount": len(groups), "total": format(total, "f"),
                "preview": preview, "previewTruncated": len(groups) > len(preview)}
'''

_INPUT_SCHEMA = {
    "type": "object", "required": ["fileId", "groupBy", "valueColumn"],
    "properties": {
        "fileId": {"type": "string", "minLength": 1, "description": "本次选择的 CSV 文件 ID"},
        "groupBy": {"type": "string", "minLength": 1, "maxLength": 200, "description": "分组列名，区分大小写"},
        "valueColumn": {"type": "string", "minLength": 1, "maxLength": 200, "description": "需要求和的数值列名"},
        "aggregation": {"type": "string", "const": "sum", "default": "sum", "description": "首版仅支持求和"},
    },
    "additionalProperties": False,
}

_CSV_SUMMARY = {
    "schemaVersion": "1", "id": "csv-summary", "version": "1", "origin": "preset",
    "name": "CSV 分组求和",
    "description": "按指定列分组，对数值列精确求和，导出 CSV 和实际结果说明。需要 UTF-8、逗号分隔、有表头的 CSV；不自动填补、丢弃或修复数据。",
    "goal": "依据用户明确指定的分组列和数值列计算汇总，输出可下载的 summary.csv 和基于实际结果的说明。",
    "prompt": "你是 CSV 分组汇总应用。使用 csv-summary-report skill，只解释已经执行的 aggregate 步骤结果。说明来源、行数、组数和总计，展示实际预览。不猜测字段、不增加计算、不声称其他文件也已通过验证。",
    "inputSchema": _INPUT_SCHEMA,
    "outputSchema": {
        "type": "object", "required": ["explanation", "csvArtifactId", "artifactIds", "stepOutputs"],
        "properties": {"explanation": {"type": "string"}, "csvArtifactId": {"type": "string"},
                       "artifactIds": {"type": "array", "items": {"type": "string"}},
                       "stepOutputs": {"type": "object"}},
        "additionalProperties": False,
    },
    "tools": [{
        "id": "csv-group-sum", "version": "1", "entrypoint": "run", "code": CSV_SUMMARY_CODE,
        "description": "只读取当前授权文件，用 Decimal 精确求和。上限 100000 数据行、5000 组、分组值 80 字符；数字最多 50 位有效数字，小数指数 -28 至 28。发现无效输入立即失败。",
        "inputSchema": _INPUT_SCHEMA,
        "outputSchema": {
            "type": "object", "required": ["artifacts", "fileId", "groupBy", "valueColumn", "rowCount", "groupCount", "total", "preview", "previewTruncated"],
            "properties": {
                "artifacts": {"type": "array", "items": {"type": "string"}, "maxItems": 1},
                "fileId": {"type": "string"}, "groupBy": {"type": "string"}, "valueColumn": {"type": "string"},
                "rowCount": {"type": "integer", "minimum": 1, "maximum": 100000},
                "groupCount": {"type": "integer", "minimum": 1, "maximum": 5000},
                "total": {"type": "string"}, "previewTruncated": {"type": "boolean"},
                "preview": {"type": "array", "maxItems": 20, "items": {"type": "object",
                            "required": ["group", "total"], "properties": {"group": {"type": "string"}, "total": {"type": "string"}},
                            "additionalProperties": False}},
            },
            "additionalProperties": False,
        },
        "permissions": {"files": ["fileId"], "network": False},
    }],
    "skills": [{
        "id": "csv-summary-report", "version": "1", "requiredTools": ["csv-group-sum"],
        "content": "引用 aggregate 实际输出中的 fileId、groupBy、valueColumn、rowCount、groupCount 和 total，保留金额文本精度。preview 仅含前 20 个按分组键升序排列的分组；previewTruncated 为真时必须说明仅展示部分结果，完整结果见 summary.csv。分组使用原始文本，区分大小写，不自行合并或清洗。输入错误时停止，不补值、不丢行、不编造结果。工具执行成功仅证明本次输入完成计算，不能宣称所有金融数据或策略均经过验证。",
    }],
    "pipeline": [
        {"id": "aggregate", "kind": "tool", "tool": "csv-group-sum", "inputFrom": "run.input", "purpose": "读取授权 CSV、检查格式、分组精确求和并导出"},
        {"id": "explain", "kind": "model", "inputFrom": "steps.aggregate.output", "purpose": "依据实际行数、组数、总计和有界预览说明结果"},
    ],
    "permissions": {"network": False, "files": ["fileId"]},
}


def csv_summary_spec():
    """Return a fresh definition; callers cannot modify the registered template."""
    return copy.deepcopy(_CSV_SUMMARY)


def register_builtin_agents(platform):
    """Register built-ins on any platform exposing register_preset(spec)."""
    platform.register_preset(csv_summary_spec())
    return [CSV_SUMMARY_REF]
