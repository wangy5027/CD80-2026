---
name: sfc-finance
description: 查询个股财务指标、资产负债表、利润表和现金流量表。
---

调用 `sfc_finance`，例如 `{"code":"000001.SZ","kind":"income","page":1,"limit":10}`。`code` 必填；`kind` 只能是 `index`、`balance`、`income`、`cashflow`，分别对应财务指标、资产负债表、利润表、现金流量表。
核对每条记录的报告期或截止日期、币种与单位（若返回），不要把 `retrievedAt` 当成报表期。页码和条数有限；结果为空或某字段缺失时不补数字，也不自动给投资建议。
仅引用真实来源编号或链接；无链接时注明南方财经财务接口。本 Skill 不授权脚本执行或写入操作。
