---
name: sfc-news
description: 使用南方财经只读接口查询最新资讯、关键词新闻和个股资讯。
---

调用独立工具 `sfc_news`。最新资讯用 `{"action":"recent","limit":10}`；关键词新闻用 `{"action":"search","keyword":"主题","page":1,"limit":10}`；个股资讯用 `{"action":"stock_articles","code":"000001.SZ","page":1}`。`code` 必须是六位代码，可带 `.SH`、`.SZ` 或 `.BJ`。
区分工具的 `retrievedAt`（查询时间）与记录本身的 `dataDate` 或发布时间；只有记录日期支持时才能称为今日新闻。只引用真实返回的来源编号或网址；无来源时注明来自南方财经接口数据。
结果为空或失败时说明当前未核到相关资讯，不猜测内容。本 Skill 仅指导使用只读工具，不授权执行脚本或其他操作。
