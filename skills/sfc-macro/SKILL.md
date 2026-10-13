---
name: sfc-macro
description: 查询宏观顶层分类或已知指标代码的时间序列。
---

调用 `sfc_macro`。看分类用 `{"action":"categories","scope":"china","limit":10}`；分类名筛选用 `{"action":"category_search","scope":"china","keyword":"价格"}`。查序列时设置 `action:"series"`，并填入从实际资料确认的 `indicatorCode`；可用 `startDate`、`endDate` 指定 `YYYY-MM-DD` 区间。`scope` 可用 `china`、`international`、`industry`。
`category_search` 只筛顶层分类，不等于全站指标搜索。序列必须先有真实 `indicatorCode`；核对各数据点日期、单位、修订信息（若返回）与 `coverage`，勿将取数时间称作统计期。
空结果或接口失败时说明未核实。仅引用实际链接；无链接时标明南方财经宏观接口。Skill 不授予执行代码的权限。
