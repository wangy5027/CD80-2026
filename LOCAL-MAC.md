# 最终版 Mac 本机运行指南

本指南只针对 `/Users/jaylen/Desktop/26CD80_v1/最终版`。主站使用 `127.0.0.1:8894`，金融数据服务使用 `127.0.0.1:8897`；双击根目录三个 `.command` 文件即可管理这两个服务。它们不会迁移或读取三个来源目录中的私人账号历史。具体业务与证据见[功能对应表](整合说明与功能对应表.md)，数据边界见[运行与数据说明](docs/integration/运行与数据说明.md)。

## 已备好的本机实例

当前交付包含本机 `.venv`、`frontend/dist`、公共金融数据库与 `data/agent-runtime`。在 Finder 双击[启动最终版.command](启动最终版.command)，等待窗口显示两个地址后访问 <http://127.0.0.1:8894/desktop/>。需要停止或重新启动时，分别双击[停止最终版.command](停止最终版.command)或[重启最终版.command](重启最终版.command)。终端中先进入本目录，再**按需要选择一个操作**：

```sh
cd /Users/jaylen/Desktop/26CD80_v1/最终版
```

启动运行 `./启动最终版.command`；停止运行 `./停止最终版.command`；重新启动运行 `./重启最终版.command`。

运行状态和健康检查可在终端单独查看；健康接口不要求把模型或数据服务密钥写进命令行：

```sh
cd /Users/jaylen/Desktop/26CD80_v1/最终版
.venv/bin/python scripts/manage_local.py status
curl -fsS http://127.0.0.1:8894/desktop/health
curl -fsS http://127.0.0.1:8897/health
tail -n 80 .run/server.log
```

管理脚本将当前工程的监督进程写到 `.run/server.json`，服务输出写到 `.run/server.log`。停止时只识别本目录启动的进程；若显示端口占用，先查明占用进程，不要盲目终止其他项目。直接执行 `main.py` 的默认端口也已统一为 8894，但它只启动主站，不能代替本指南的联合启动与 8897 数据服务检查。若旧浏览器标签仍访问 8893，请改为 <http://127.0.0.1:8894/desktop/>；旧进程可能读到最新前端文件，却仍运行未更新的后端，从而报“接口不存在”。显式 `--port` 保留给隔离验收，需同时指定独立 `--data-dir`。

## 在这台 Mac 上重建依赖

重建虚拟环境时使用 Python 3.11 或更新版本；本次 Mac 验证使用的是 Python 3.11.15。已有 `.venv` 时不要无故覆盖。新建环境后安装主站与数据服务依赖，若要运行数据服务测试再装开发依赖。以下命令均从**最终版目录**执行：

```sh
cd /Users/jaylen/Desktop/26CD80_v1/最终版
test -d .venv || python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r cd80-data-api/requirements.txt
.venv/bin/python -m pip install -r cd80-data-api/requirements-dev.txt
```

根目录 `.env` 属于主站配置；从 `.env.example` 创建时只做首次创建，保留已经配置的密钥。数据服务的 `cd80-data-api/.env` 可用其专用初始化脚本首次生成随机管理员密钥，该脚本遇到现有文件会保留原文件，不打印密钥：

```sh
cd /Users/jaylen/Desktop/26CD80_v1/最终版
umask 077
test -e .env || cp .env.example .env
chmod 600 .env
.venv/bin/python cd80-data-api/scripts/init_config.py
```

确认数据服务配置文件中的 `CD80_DB_PATH=data/cd80.sqlite3` 指向 `cd80-data-api/data/cd80.sqlite3`；联合启动脚本以**数据服务目录**解释此相对路径。主站 `.env` 中的 `DATA_DIR=data` 指向最终版 `data/`，`AGENT_RUNTIME_DIR=data/agent-runtime` 指向本工程 WASI 资源，`CD80_DATA_API_URL` 对应 8897，`IMPORT_LEGACY_HISTORY=0`。保留现有配置，缺失时按模板设置；模型、搜索、南财及数据服务令牌只放后端 `.env`，不要复制到前端或文档。联合启动会先加载数据服务 `.env`，再由主站加载其 `.env`，同名字段以前者为准；因此金融数据的管理员密钥和 `CD80_DATA_ADMIN_USERS` 应统一配置在数据服务 `.env`，两边需要一致时核对实际生效值。写入公共金融数据仍需管理员密钥和允许的本地用户 ID；留空的 `CD80_DATA_ADMIN_USERS` 不授予写权限。

日常运行使用已构建的 `frontend/dist`，无需启动 Vite。修改了前端源码后，才在 `frontend/` 中执行 `npm ci`、`npm run test`、`npm run build`；依赖由 `package-lock.json` 锁定。本次构建所在机器使用 Node 25.9.0 与 npm 11.12.1，其他版本需按 Vite 的实际安装要求核对。

## WASI 运行时

平台应用、隔离工具和策略工坊自定义规则依赖最终版 `data/agent-runtime`。更换 Mac、重装 Wasmtime 或搬移工程后，先运行离线校验与本机编译脚本；它会核对运行时清单和来源摘要、生成本机专属的 `python.cwasm`，随后执行最小隔离运行检查：

```sh
cd /Users/jaylen/Desktop/26CD80_v1/最终版
.venv/bin/python scripts/prepare_local_runtime.py
```

仅当 `data/agent-runtime` 的便携运行时文件缺失时，才运行 `scripts/setup_agent_runtime.py`。该脚本从固定发布地址下载、校验固定 SHA256、生成清单并做隔离检查，需要网络；完成后再运行上述 `prepare_local_runtime.py`。不要从来源项目复制另一台机器生成的 `.venv` 或 `python.cwasm`，也不要在校验失败后绕过执行器。

## 启动异常与交付边界

启动失败先查看 `.run/server.log` 和 `scripts/manage_local.py status`。常见原因是 8894/8897 端口被占用、Python 依赖缺失、`cd80-data-api/data/cd80.sqlite3` 缺失、后端配置不完整或 WASI 清单校验失败。数据服务健康并不代表模型令牌有效。当前按用户授权统一使用 `GLM-5.3-Flash`，主站与三个应用的新调用均从后端配置/允许型号读取；旧 GLM-5.3 的超时和切换后 Flash 的成功见真实依赖验收（本地资料，未随源码发布）。模型格式失败或澄清须按页面处理，不把保存当作验证通过。公共策略行情目前只可用于明确标注的工程诊断，不作为正式收益报告或实盘依据。

此实例应由最终版内新建账号开始使用；来源目录原账号和历史保留在那里。浏览器验收使用隔离数据目录，不能把其测试账号当成交付账号。停服备份、恢复和 Mac MCP 凭据密钥的处理见[运行与数据说明](docs/integration/运行与数据说明.md)；整站最终结果以整合验收报告（本地资料，未随源码发布）为准。
