---
name: sfc-lhb
description: 查询指定交易日龙虎榜汇总或个股明细。
---

调用 `sfc_lhb`。汇总可用 `{"action":"summary","type":1,"page":1,"limit":10}`；明细可用 `{"action":"detail","code":"000001.SZ","limit":10}`。需要指定交易日时加 `date:"YYYY-MM-DD"` 并填实际日期。`type` 仅使用 schema 允许的 1–4，不在未知时猜测类别含义。
核对返回的 `tradeDate` 和每条记录日期；非交易日结果可能为空。有限页数据不能概括全部龙虎榜，也不能把历史记录当作实时交易。
只引用真实来源编号；无链接时注明南方财经龙虎榜接口。本 Skill 只指导读取数据，不授权交易或代码执行。
