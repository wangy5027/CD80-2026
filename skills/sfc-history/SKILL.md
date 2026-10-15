---
name: sfc-history
description: 查询股票日、周、月 K 线，并说明实际返回记录的日期覆盖范围。
---

调用 `sfc_history`，例如 `{"code":"000001.SZ","period":"dayk","limit":20}`。`code` 必填；`period` 可用 `dayk`、`weekk`、`monthk`；需要指定区间时再填写 `startDate`、`endDate`，格式为 `YYYY-MM-DD`。`adjustment` 仅用工具 schema 允许的 1、2、3，不自行解释其含义。
必须检查 `coverage.oldestFetched`、`newestFetched`、`requestedStart`、`requestedEnd` 和 `completeForRequestedRange`。日期筛选只针对本次接口返回记录；分页完整性未证实，不得声称覆盖整个指定区间。
按真实 K 线记录说明价格与日期，空结果不能推断该股没有交易。无网址时说明来自南方财经行情接口。Skill 不授权脚本或交易操作。
