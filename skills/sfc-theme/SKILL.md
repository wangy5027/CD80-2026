---
name: sfc-theme
description: 查询金融主题推荐、列表、详情、成分股或按关键词搜索主题。
---

调用 `sfc_themes`。推荐用 `{"action":"recommend","type":2,"limit":10}`；搜索用 `{"action":"search","keyword":"人工智能","limit":10}`；详情或成分股需先取得实际 `themeId`，分别用 `{"action":"detail","themeId":123}`、`{"action":"stocks","themeId":123}`。还可用 `action:"list"` 看列表。
核对主题名称、ID、成分股代码和更新日期（若返回）；推荐或成分股列表只是当前接口返回，不等于主题长期固定范围。`retrievedAt` 只表示查询时间。
无链接时注明南方财经主题接口，空结果或失败不推测成分。Skill 只指导只读查询，不授权脚本执行或交易。
