---
name: sfc-announcement
description: 查询上市公司公告列表或已知公告 ID 的详情。
---

调用 `sfc_announcements`。列表用 `{"action":"list","code":"000001.SZ","page":1,"limit":10}`；详情用 `{"action":"detail","id":123456}`。列表需要六位股票代码；详情需要实际已知的公告 ID，不猜测 ID。
按真实公告标题、发布或披露日期、公司与正文摘要回答。`retrievedAt` 只表示取数时间；旧公告不得说成新发布。详情缺失时不要用列表摘要冒充全文。
仅引用实际返回的公告网址或来源编号；无链接时注明南方财经接口数据。本 Skill 仅指导只读查询，不授权脚本执行或写入操作。
