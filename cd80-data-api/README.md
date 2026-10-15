# 成都八零 SQLite 数据服务

把比赛 CSV 转为保留原始记录的 SQLite 数据库，通过 HTTP 提供查询、增加、修改和删除。配套网站位于上一级目录，沿用桌面网站现有的 Vue 3 + Flask、登录系统和 `/desktop/api` 请求方式。

GitHub 源码不包含下列 `data/` 文件、真实 `.env` 或虚拟环境。克隆后先按 [GitHub 上传说明](../docs/GitHub上传说明.md) 安装依赖并从私有备份恢复数据库，或用授权 CSV 与字段字典重新导入；未准备数据库时双服务启动入口会拒绝启动。

## 交付内容

- `data/cd80.sqlite3`：37 张数据表。每行独立 `_row_id`，重复业务记录全部保留。
- `data/import_manifest.json`：实际文件名、行数、字段类型、源文件 SHA256 和导入检查结果（以生成文件为准）。
- `app/`：FastAPI 服务，交互文档 `/docs`，机器可读规范 `/openapi.json`。
- `scripts/import_csv.py`、`verify_db.py`：可重建与验证数据库。
- `scripts/backup_db.py`：数据库运行时的一致性备份。
- `../backend/app/modules/financial_data/`：复用网站登录的同源 API。
- `../frontend/src/modules/workbench/financialDataApi.js`：Vue 页面可以直接导入的调用模块。
- 网站侧栏“数据管理”：浏览 37 张表、字段筛选、排序分页、记录详情，以及有权限账号的新增、修改与确认删除。

## 当前电脑启动

在终端运行：

```bash
cd '/Users/jaylen/Desktop/成都八零/平台开发/cd80-data-api'
./.venv/bin/python scripts/start_all.py
```

网站打开 `http://127.0.0.1:8894/desktop/`，数据 API 文档打开 `http://127.0.0.1:8897/docs`。Ctrl+C 同时关闭两个服务。端口被占用时可加 `--website-port 8898 --api-port 8899`。

只运行数据 API：

```bash
./.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8897 --env-file .env
```

## 迁移到另一台电脑

保留整个网站文件夹及其内部的 `cd80-data-api/`，数据库位于网站内 `cd80-data-api/data/cd80.sqlite3`。使用 Python 3.11 或更新版本，重新建立虚拟环境，不复制原机器 `.venv`。

```bash
cd cd80-data-api
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt -r ../requirements.txt
./.venv/bin/python scripts/init_config.py
./.venv/bin/python scripts/start_all.py
```

Windows 对应使用 `.venv\Scripts\python.exe`。网站自带包含“数据管理”页面的 `frontend/dist`，日常运行不需要 Node 或重新构建。后续修改前端源码时，在 `../frontend` 下执行 `npm ci` 和 `npm run build`。

## 网站接入与权限

浏览器使用 `/desktop/api/financial-data/...`，由 Flask 代理到数据服务 `/api/v1/...`。浏览器复用本站登录 Cookie，不接收管理密钥。

- 已登录账号可以查询。
- 增删改除了登录，还要求账号 ID 位于 `CD80_DATA_ADMIN_USERS`。模板默认名单为空；本机已按用户要求配置指定账号。
- 在网站 `/desktop/api/session` 的 `user.id` 取得本人账号 ID，把它填入数据服务 `.env` 的 `CD80_DATA_ADMIN_USERS`，重启两个服务后生效。多个 ID 用逗号分隔。
- `scripts/init_config.py` 生成本地管理密钥，已有 `.env` 不会被覆盖。不要把 `.env` 放入前端或源码仓库。
- 直接调用数据服务的写接口要发送 `X-API-Key`，值为后端的 `CD80_ADMIN_API_KEY`。
- 如设置 `CD80_READ_API_KEY`，直接读接口也要求密钥；网站代理会在后端自动附加。

本次同步保留了桌面网站已有的模型配置、账号、会话和功能，仅迁入用户指定邮箱的账号记录并配置数据管理权限。原密码校验信息保持一致。现有模型聊天功能仍使用网站自己的配置；数据服务不调用模型或外部金融 API。

## 数据 API

下表路径均以 `/api/v1` 为前缀；经网站调用时将前缀换成 `/desktop/api/financial-data`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/tables` | 数据表列表、中文描述、原始与当前行数 |
| GET | `/tables/{table}/schema` | 字段、类型、描述、样例和索引 |
| GET | `/tables/{table}/rows` | 分页、条件筛选、排序、字段投影 |
| POST | `/tables/{table}/query` | 用 JSON 提交复杂查询，属于读取操作 |
| GET | `/tables/{table}/rows/{row_id}` | 查看一行 |
| POST | `/tables/{table}/rows` | 新增一行，返回 201 |
| POST | `/tables/{table}/rows/batch` | 原子批量新增，最多 1000 行 |
| PATCH | `/tables/{table}/rows/{row_id}` | 只修改给定字段 |
| DELETE | `/tables/{table}/rows/{row_id}` | 删除一行，成功返回 204 |

独立服务 `/health` 为健康检查。网站还提供 `/desktop/api/financial-data/capabilities` 查看当前账号的权限。

查询示例：

```bash
curl -X POST 'http://127.0.0.1:8897/api/v1/tables/cd80_daily/query' \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <你的读取密钥>' \
  -d '{"filters":[{"field":"ts_code","op":"eq","value":"000001.SZ"},{"field":"trade_date","op":"gte","value":"20260901"}],"sort_by":"trade_date","order":"desc","limit":20,"columns":["_row_id","ts_code","trade_date","close","vol"]}'
```

筛选条件以 AND 连接，支持 `eq`、`ne`、`gt`、`gte`、`lt`、`lte`、`in`、`contains`、`isnull`。最多 20 条条件。`contains` 为字面子串，不解释 SQL 通配符。数值字段传 JSON number，代码与日期字段传字符串。

分页默认 `limit=100`，最多 1000；`offset` 最大 100 万。大表遍历优先使用 `_row_id` 升序及响应中的 `pagination.next_after_id` 作为下一页 `after_id`，此时不设 offset。`include_total` 默认 false；无需为每次翻页扫描大表。全表总数由触发器维护，带条件的精确计数受查询超时限制。

新增和修改的请求格式是 `{"data":{"字段名":"值"}}`；批量新增是 `{"rows":[{"字段名":"值"}]}`。`_row_id` 由服务分配，客户端不可指定或修改。原来的 `id`、`ID` 不是唯一主键。未提供的新增字段为 NULL。不存在的表或行返回 404；无权限 401/403；参数错误 422；约束冲突 409；数据库忙 503；查询超时 504。

## 数据口径

保留原表名、字段名和全部源记录，不自动处理重复和冲突。代码、标识符、日期保存为 TEXT，以保留前导零和原日期格式（例如 `20260930`、`2026-09-30` 或 `20260930.0`）；可在 schema 的 example 中确认。其他字段通过全量扫描推断 INTEGER/REAL/TEXT，全空列使用 TEXT。空 CSV 单元格转为 SQL NULL；不把缺失值补成 0。

REAL 是双精度浮点数，不能替代需要逐分精确核算的十进制定点数。原始 CSV 保留不改。日期、金额单位、公告日与报告期含义通过原 Excel 字段说明进入 schema。数据库索引改善代码/日期查询，但不会把不完整的历史数据变成实时数据。

已知源数据限制包括：资产负债表只有 8 行、基金净资产字段缺失严重、因子目录大量重复、转债行情存在重复与冲突、部分历史表截止年份较早。接口的原始记录数不等于去重后的经济观测数。

所有 API 写操作和其 before/after 值记录在内部 `_audit_log`，与对应写入同一事务提交。原始 `source_rows` 保留，当前 `row_count` 由数据库触发器维护。内部元数据和审计表不通过通用接口开放。

## 重建、验证与备份

```bash
./.venv/bin/python scripts/import_csv.py --help
./.venv/bin/python scripts/verify_db.py --help
./.venv/bin/python scripts/backup_db.py --output backups/cd80-backup.sqlite3
```

导入器通过 `.building` 临时文件构建，成功核验后才生成正式库，默认拒绝覆盖已有库。`verify_db.py` 的源哈希核验以实际导入目录为准。`--pristine` 只适用于服务首次启动前的初始导入文件：服务启用 WAL、创建审计表或执行写操作都会改变 SQLite 文件哈希，此后使用常规验证。导入时移动硬盘离线，因此实际源是桌面同名副本，详情见导入 manifest；尚未证明它与离线硬盘逐字节相同。

数据库启用 WAL。运行中的库使用备份脚本迁移，不单独拷贝主库文件。[SQLite 官方备份说明](https://www.sqlite.org/backup.html)。同源网站代理沿用现有登录边界；独立 API 的跨域来源由 `CD80_CORS_ORIGINS` 指定。[FastAPI CORS 说明](https://fastapi.tiangolo.com/tutorial/cors/)。

## 测试

```bash
./.venv/bin/python -m pip install -r requirements-dev.txt
./.venv/bin/python -m pytest -q tests
```

测试使用临时数据库，真实比赛库不作为写入测试目标。SQLite 适用于单机读多写少场景；后续多实例部署或大量并发写入时可保留 HTTP 契约，替换数据库存储层。
