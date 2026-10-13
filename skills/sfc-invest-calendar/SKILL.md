---
name: sfc-invest-calendar
description: 查询经济数据、金融事件、公司事件、新股和重点资讯日历。
---

调用 `sfc_calendar`，例如 `{"action":"eco","importantOnly":true,"limit":10}`。`action` 可用 `eco`、`fin`、`company`、`ipo`、`highlights`；`importantOnly` 适用于经济和金融事件查询。需要指定日期时加 `date`，格式为 `YYYY-MM-DD`；`ipo` 分支不按该日期过滤，应核对每条新股记录的实际日期。
回答时明确是哪种日历、事件日期及已公布或预计状态（若返回）。`retrievedAt` 是查询时间；未来事件会变更，不能把预计值说成已发生事实。
有限返回或接口失败要说明，勿混合不同类别冒充同一事件列表。无链接时注明南方财经日历接口。Skill 不授权下单或代码执行。
