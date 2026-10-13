---
name: sfc-research
description: 按标题、机构、作者或类型检索南方财经研报。
---

调用 `sfc_research`，至少提供 `title`、`source`、`author`、`type` 中一项，例如 `{"title":"新能源","page":0,"limit":10}`。可用 `startDate`、`endDate` 限定日期，格式均为 `YYYY-MM-DD`；此工具页码从 0 开始。
研报观点要注明机构、作者和报告日期（实际字段存在时）。报告发布日期与本次 `retrievedAt` 不同；旧研报不应说成最新观点。
只引用真实返回的来源编号或网址；没有链接时说明来自南方财经研报检索结果。检索失败或结果不足时不补写未见到的研报。Skill 不授予脚本执行权限。
