---
name: sfc-margin
description: 查询融资融券榜单或单只股票的两融记录。
---

调用 `sfc_margin`，例如 `{"sort":"net_margin","page":1,"limit":10}`；可加 `code` 指定六位股票代码，或用 `date` 指定 `YYYY-MM-DD`。`sort` 可用 `margin_buy`、`net_margin`、`margin_balance`、`change_rate`。
务必报告实际 `dateFilter` 和记录交易日；省略日期时工具查询最近可确定的交易日，不能称作今天。榜单只覆盖返回页，不代表整个市场。
保留数值的原始单位与字段意义，缺字段不估算。无链接时注明南方财经两融接口；不据此直接给买卖指令。Skill 不授权交易或脚本执行。
