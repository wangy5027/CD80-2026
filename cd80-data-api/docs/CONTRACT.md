# 数据服务内部契约

## 2026-10-15 当前本地官方数据目录

新增只读 `GET /api/v1/catalog`，沿用读取鉴权，原 `/api/v1/tables` 保持轻量且不扫描日期。目录只列当前 SQLite 中 `_datasets`、`_columns` 与实际表交集的 `cd80_*` 业务表；表级日期口径、状态、缓存与来源标签边界见 [目录接口设计](CATALOG.md)。响应 `{data:[...],total:n}`，每项在既有表列表字段上增加 `provider`、`storage`、`summary`、`primaryColumns`、`dateRange`、`sourceNote`。`provider:"Tushare"` 是本产品统一展示约定；当前导入元数据没有逐表上游证明，不表示实时在线同步。`source_file` 只返回 basename。机器路径与读取权限保持私有。

## 2026-10-13 有界读取增量（候选包使用）

新增POST `/api/v1/tables/{table}/scan`及`/aggregate`，沿用读取鉴权；机器字段约束见openapi.json。scan限制10000匹配行、100条/页、512KiB/页；不可变快照4MiB、总32MiB、6400游标、120秒。X-CD80-Scope由主站写入账号/子任务作用域，不从模型参数透传；直接API调用者仍需遵守原读取密钥保护。

scan只允许columns/filters/order_by/page_size/cursor。date_gte/date_lte规范化紧凑日期及ISO，非法源日期失败；cursor绑定相同查询与scope，过期返回409 CURSOR_EXPIRED，数据库变化不会改变已有快照。aggregate只允许filters/group_by/metrics/order_by/limit，count/sum/min/max，最多3组键/8指标/100组；超过10000行或组数失败，不对截断数据计算。数值Decimal计算，空值传播，不修复REAL源字段原有精度损失。聚合order_by限分组字段/指标别名，数字按Decimal排序、空值最后，稳定决胜。

新异常detail为`{code,message}`，主站保留有限业务code；既有/query异常格式不改。新增接口不初始化任何新数据库表；验收与未完成范围见迁移记录（本地资料，未随源码发布）。

数据库默认 `data/cd80.sqlite3`，通过 `CD80_DB_PATH` 覆盖。保留全部 37 张 `cd80_*` 原始表、所有记录与字段名，不做业务去重。每表增加 `_row_id INTEGER PRIMARY KEY AUTOINCREMENT`，原 `id`/`ID` 保持原字段，不能当主键。

源代码、标识符和日期一律 TEXT，保留前导零及源日期格式。其他字段经全量检查后选择 INTEGER/REAL/TEXT；空 CSV 单元格变 NULL，非空文本不随意改写。REAL 为双精度，原 CSV 保留以备精确金额核对。表名、列名只能从数据库登记元数据白名单选择。

元数据表：

```sql
CREATE TABLE _datasets (
  name TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  row_count INTEGER NOT NULL,
  source_rows INTEGER NOT NULL,
  source_file TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  encoding TEXT NOT NULL,
  imported_at TEXT NOT NULL
);
CREATE TABLE _columns (
  table_name TEXT NOT NULL,
  name TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  data_type TEXT NOT NULL,
  logical_type TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (table_name,name)
);
```

`_columns` 登记源字段，不登记 `_row_id`。导入后建触发器维护 `_datasets.row_count`，保留 `source_rows` 不变。API 写入不手动增减 row_count。数据库可另设 `_audit_log` 记录写操作，服务自行管理，不能通过通用接口访问内部表。

服务接口约定：

- `GET /health`：服务与数据库健康。
- `GET /api/v1/tables`：表列表及数量。
- `GET /api/v1/catalog`：当前本地官方数据目录、真实主日期范围与主要字段，字段及状态见 [目录接口设计](CATALOG.md)。
- `GET /api/v1/tables/{table}/schema`：字段类型、描述、样例、索引。
- `GET /api/v1/tables/{table}/rows`：`limit` 默认 100 最大 1000、`offset` 默认 0；`after_id` 可选用于大表游标翻页；`sort_by` 默认 `_row_id`、`order=asc|desc`；可选 `columns` 逗号分隔投影；`filters` 是 JSON 数组，每项 `{field,op,value}`，op 支持 eq/ne/gt/gte/lt/lte/in/contains/isnull。筛选值参数化。`include_total=false` 默认不做全表 COUNT。响应 `{data:[...],pagination:{limit,offset,has_more,next_after_id,total}}`，total 未请求时 null。after_id 仅允许 `_row_id asc` 且 offset=0。
- `POST /api/v1/tables/{table}/query`：同等查询参数的 JSON 请求体，属于读取操作。
- `GET /api/v1/tables/{table}/rows/{row_id}`：单行 `{data:{...}}`。
- `POST /api/v1/tables/{table}/rows`：请求 `{data:{field:value}}`，返回 201 `{data:{...}}`。
- `POST /api/v1/tables/{table}/rows/batch`：请求 `{rows:[{...}]}`，最多 1000 行，原子事务，返回 201 `{data:[...],inserted:n}`。
- `PATCH /api/v1/tables/{table}/rows/{row_id}`：请求 `{data:{field:value}}`，返回 `{data:{...}}`。
- `DELETE /api/v1/tables/{table}/rows/{row_id}`：物理删除单行，成功 204；不存在 404。

写接口都必须匹配环境变量 `CD80_ADMIN_API_KEY` 的 `X-API-Key`；未配置时关闭写入。可选 `CD80_READ_API_KEY` 保护读接口（若配置，也接受管理密钥），默认本地只读查询无需密钥。所有运行实例绑定 127.0.0.1。部署 CORS 域名用 `CD80_CORS_ORIGINS` 逗号分隔，默认 localhost:3000/5173、127.0.0.1:3000/5173。启动文档 `/docs`、OpenAPI `/openapi.json`。不给客户端执行任意 SQL 或修改表结构。

应用入口 `app.main:create_app` 和 `app.main:app`。配置从环境读取，`.env` 由启动命令加载。每请求独立 SQLite 连接，busy_timeout、参数化查询、WAL、有限查询超时。找不到数据库时失败并提示导入，不悄悄建空库。
