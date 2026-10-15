# 官方本地数据目录接口

日期：2026-10-15。状态：接口已实现、隔离测试通过；正式服务及页面联调由主任务验收。此文档只约束数据服务 `GET /api/v1/catalog`；平台数据管理页面见 `../../docs/financial-data/官方数据目录.md`。

目录只读取当前 `CD80_DB_PATH` 指向的 SQLite。仅展示 `_datasets`、`_columns`、`sqlite_master` 与实际表字段一致的 `cd80_*` 表；读取权限沿用 `CD80_READ_API_KEY`，不向客户端暴露数据库路径、任意 SQL 或密钥。现有 `GET /api/v1/tables` 保持轻量合同，不增加日期扫描。

响应 `{data:[...],total:n}`。每项保留 `/tables` 的 `name,title,row_count,source_rows,source_file,imported_at`，增加 `provider:"Tushare"`、`storage:"local_sqlite"`、`summary`、`primaryColumns:[{name,description,data_type,logical_type}]`、`dateRange:{field,label,start,end,granularity,status}` 与 `sourceNote`。`source_file` 只返回文件名；`summary` 默认沿用当前登记标题（可能是英文），`cd80_ci_index_member` 的错误行业名称只在展示摘要中更正为中信行业，不修改原始标题或数据。主要列从真实表与登记元数据的交集选择，最多六列。`provider` 是本产品经用户确认的统一来源标签：当前库仅保存原始文件名与摘要，没有逐表上游提供方证明，也不表示在线同步。

日期字段按代码中 37 个表的业务白名单选择，不从 `_columns.logical_type` 猜测。状态为 `available`、`no_date_field`、`empty`、`no_valid_date` 或 `too_many_values`。`available` 时 `start/end` 为实际有效本地值的范围；日粒度为 `YYYY-MM-DD`，月粒度为 `YYYY-MM`。其他状态的 `start/end` 为 null；无主日期的 `field/label/granularity` 也为 null。日期解析排除 SQL NULL、空白、字面 `NULL`、非法公历日期，接受 `YYYYMMDD`、`YYYY-MM-DD`、`YYYY/MM/DD`、`YYYYMMDD.0`；月度字段接受 `YYYYMM` 或 `YYYY-MM`。不能把月度补成每月 1 日，也不能将公告日、报告期、日历日标为交易日。

服务仅对列在白名单且在实际表中存在的主日期列执行固定 SQL。每表最多取 100001 个不同非空原值；超过 100000 个返回 `too_many_values` 而不宣称不完整范围。每个请求依然受现有 SQLite 进度处理器超时约束。目录计算按数据库及有数据帧的 WAL 文件标识/大小/修改时间、最新审计 ID 与最长 30 秒 TTL 缓存；空 WAL 创建/删除不代表业务写入，归为同一状态。服务内写入会产生审计记录；外部提交改变 WAL 或检查点后的数据库文件状态，会立即失效，其他异常最迟在 TTL 后重新计算，避免反复扫描未索引的较大日期列。不新增表、索引、触发器或正式数据写入。元数据不一致的登记表不展示。

隔离测试验证授权、实际字段交集、各日期格式和口径、字面 NULL、缺字段/空表、更新后的缓存失效及原 `/tables` 不变。正式 SQLite 只读核验 37 张表的范围，不用旧静态 JSON 推断当前范围。

本轮数据服务测试：`../.venv/bin/python -m pytest tests -q`；目录专项包含连续请求缓存命中、授权更新、外部更新及读取期间并发提交的失效回归。对当前正式 SQLite 使用 immutable 只读连接执行目录计算，37 张登记表均可见，耗时约 0.4 秒；36 张表有有效业务主日期，`cd80_factor_list` 无主日期。此核验未通过正式 HTTP 服务，也未写入正式数据。最终通过项数以本轮测试日志为准。

## 正式服务复验

最终正式服务已由主任务重启（supervisor 11847，8894/8897健康）。真实HTTP目录37表和36个主日期范围与独立只读基线逐项一致；连续请求0.3790/0.0023/0.0022秒，缓存复用有效。页面搜索/选择/记录分页筛选及桌面手机短屏检查通过，未发起金融数据写请求。见主任务验收（本地资料，未随源码发布）。
