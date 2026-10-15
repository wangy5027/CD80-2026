---
name: sfc-timeline
description: 使用南方财经只读接口查询最近 1 至 48 小时的财经快讯。
---

调用 `sfc_timeline`，例如 `{"hours":24,"limit":10,"redOnly":true}`；可加 `keyword` 缩小主题。`hours` 为 1–48，`redOnly` 只表示筛选标红快讯。
阅读 `coverage`、`partial`、`warnings` 和每条记录的实际时间。接口只抓取有限条数与最多三页，不得把结果称为该时段全部快讯；出现部分失败时说明覆盖不足。
只引用真实返回的来源编号或网址；没有可核对链接时注明是南方财经接口数据。无记录不等于事件未发生。本 Skill 不授予脚本或额外权限。
