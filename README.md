# 成都八零 · 金融 AI 应用平台

> `final_v1` 为源码发布版本，不含 Token、私人历史、数据库或已安装依赖。上传范围与首次配置见[源码发布说明](docs/GitHub上传说明.md)。本机原有历史记录保留。

成都八零将金融研究、策略诊断和应用创作整合到同一个 AI 工作台。用户可以围绕问题整理资料、调用工具、查看执行过程，再将可复用的流程保存为 App、Skill 或 Tool，供后续使用。

当前项目由**平台底座 + Explore + 策略工坊 + 应用工坊**组成，共用平台账号和工作台导航，按需使用平台公共服务。研究资料、策略历史和工坊会话分别由各应用管理；应用工坊生成的 App 保存到平台资产库。三个应用保留各自的业务流程，统一入口不代表会自动串联执行。

本文以“最终版”当前源码和 2026-10-14 的项目记录为依据。详细状态见项目状态（本地资料，未随源码发布），逐项接入说明见[功能对应表](整合说明与功能对应表.md)。

## 1. 项目功能

| 模块 | 主要能力 | 使用入口与交付 |
| --- | --- | --- |
| 平台工作台 | 流式对话、项目与文件管理、历史任务、日程提醒、研究子任务及执行过程查看 | 首页对话、侧栏项目/文件/日程；日程在工作台运行时提醒 |
| Explore | 理解事件、发现关联、历史验证；支持会话资料池、来源查看、研究记忆和恢复 | 首页或“专有应用 → Explore”；输出带来源的研究解释与报告 |
| 策略工坊 | 从十种内置策略匹配起点，确认和改写规则，数据预检、诊断回测、图表与代码查看，保存个人策略及复跑 | 首页或“专有应用 → 策略工坊”；个人策略保留实现和参数 |
| 应用工坊 | 根据需求推荐现有应用，澄清和细化需求，组合生成应用并保存 | 首页或“专有应用 → 应用工坊”；生成结果进入平台个人应用库 |
| 技能池 | Apps、Skills、Tools 三类目录，分别展示“我的”和“平台”资产；支持详情、添加、创建、管理与验证 | 侧栏“技能池”；官方资产及用户主动公开版本可添加为个人副本 |
| 金融数据与新闻 | 本地 SQLite 数据表浏览、筛选查询及授权写入；新闻检索和相关研究工具 | 侧栏“数据管理”及对话/研究工具；具体外部能力取决于后端配置 |
| 资产复用与共创 | 对话成果沉淀、版本管理、导入导出、验证启用、显式公开发布和复制 | 资产详情及管理页面；个人草稿不会自动公开 |

Explore 的“历史验证”通过历史材料研究和对照提供证据。策略工坊负责行情上的诊断回测，两者的输出用途不同。

### App、Skill、Tool

| 类型 | 用途 | 当前创建方式 |
| --- | --- | --- |
| App | 保存完整的可复用应用或多步骤流程，包括实现、资源、组件版本和输入输出绑定 | 技能池只提供外部导入、自然语言创建两种入口；应用工坊和对话沿用同一保存规范 |
| Skill | 保存执行目标、操作指令及配套资源，由平台在授权能力范围内执行 | 分项填写、完整 `SKILL.md`、兼容 ZIP 导入或对话沉淀 |
| Tool | 保存工具接口和独立执行实现，完成明确的计算或工具调用 | 分项填写、工具 JSON、兼容 ZIP 导入或对话沉淀；原生 Python 实现使用 `run(payload)` |

资产的**已保存、已验证、已启用**是不同状态。APP 从任何创建入口生成完整定义后先保存为未验证，再在详情修改、验证和启用；策略工坊新增规则也先保存未验证，不再以回测成功作为保存前提。手动创建、导入、添加不会自动执行应用。缺少执行实现的 Tool 可以保存，但会保持待配置。

平台目录只展示内置资产及主动公开的版本。官方和已公开发布的 Skills/Tools 精确副本免重复验证，添加后由用户主动启用，仍需满足连接和权限要求；不会继承作者的私人凭据、文件授权和运行历史，也不会制造个人“验证通过”记录。用户上传、手写、模型生成或修改的 Skills/Tools 仍需验证。APP 不继承能力来源的免验证资格，始终先保存未验证。详见[来源规则](docs/integration/SkillsTools免验证规范.md)和[APP接口、提示词与存储](docs/integration/APP接口与提示词存储.md)。

外部开发者可从创建页面下载 APP 接口 Schema、导入 API 说明、数据服务接口资料、Python 单文件和源码 ZIP 示例。外部文件与自然语言生成的应用都会落为同一个 `cd80.app/2`；解析和保存不执行用户实现。规则见[APP 两种创建入口](docs/integration/APP两种创建入口.md)，可直接使用的文件见[标准导入示例](examples/app-import-contract/README.md)。

## 2. 快速启动

### 当前 Mac 本机实例

在 Finder 中双击根目录的[启动最终版.command](启动最终版.command)，或在项目根目录执行：

```sh
./启动最终版.command
```

| 服务 | 默认地址 |
| --- | --- |
| 工作台 | [http://127.0.0.1:8894/desktop/](http://127.0.0.1:8894/desktop/) |
| 金融数据 API 文档 | [http://127.0.0.1:8897/docs](http://127.0.0.1:8897/docs) |
| 主站健康检查 | [http://127.0.0.1:8894/desktop/health](http://127.0.0.1:8894/desktop/health) |
| 数据服务健康检查 | [http://127.0.0.1:8897/health](http://127.0.0.1:8897/health) |

按需选择一个管理操作：

```sh
# 查看本工程运行状态
.venv/bin/python scripts/manage_local.py status

# 停止本工程
./停止最终版.command

# 重启本工程
./重启最终版.command
```

三个中文脚本由 `scripts/manage_local.py` 管理本工程的联合服务。进程记录位于 `.run/server.json`，服务日志位于 `.run/server.log`；停止前会核对进程归属。

直接运行 `main.py` 只启动主站，默认端口同样为 8894；若已有主站运行，会提示检查现有服务，不再建议另开 8893。日常使用上述联合启动入口，同时管理主站 8894、数据服务 8897 及正确的主站代理地址。浏览器旧标签若仍是 8893，请改用上方的 8894 地址。

### 首次使用

1. 启动服务后，在工作台注册并登录自己的账号。
2. 从首页选择 Explore、策略工坊或应用工坊，也可以直接进行对话并上传资料。
3. 在“技能池”查看个人资产和平台目录；打开详情后添加或创建所需能力。
4. 保存需要复用的应用或策略。普通 App、Skill、Tool 按页面完成验证和启用，再使用新的输入运行。

最终版建立时未迁入来源工程的私人账号和历史。后续使用产生的对话、文件、研究、策略和应用会保存在本实例，需要随业务数据备份。

## 3. 安装与环境准备

### 环境要求

- Python 3.11 或更新版本；依赖以根目录和数据服务的 `requirements.txt` 为准。
- 修改或重新构建前端时需要 Node.js 和 npm；当前 Vite 依赖要求 Node.js `^20.19.0 || >=22.12.0`。
- 模型和联网研究功能需要可用网络及对应服务凭据；当前新调用默认使用 Gitee AI 的 `GLM-5.3-Flash`。
- 应用执行、隔离工具和自定义策略需要本项目的 WASI Python 运行资源。
- 完整联合启动需要 `cd80-data-api/data/cd80.sqlite3`；策略诊断还需要专用行情库与标的映射。

日常主站直接提供 `frontend/dist/` 中的构建页面，无需单独启动前端开发服务器。当前交付目录已经包含本机依赖和运行资源；换机器时应重新创建虚拟环境并校验运行时。仅复制公开源码不足以恢复完整本机实例。

### 重建 Python 环境

以下命令均从项目根目录执行。已有环境可继续使用；只有缺失时创建：

```sh
test -d .venv || python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -r cd80-data-api/requirements.txt
```

首次创建后端配置：

```sh
umask 077
test -e .env || cp .env.example .env
chmod 600 .env
.venv/bin/python cd80-data-api/scripts/init_config.py
```

随后在本机编辑配置，填入自己的服务凭据。上述配置创建命令保留现有文件；数据服务初始化会在首次创建时生成随机管理员密钥。

### WASI 运行资源

已有 `data/agent-runtime/` 时，搬机或重装 Wasmtime 后运行：

```sh
.venv/bin/python scripts/prepare_local_runtime.py
```

脚本核验固定来源及文件摘要，生成本机编译文件，并执行最小隔离运行检查。仅当便携运行资源缺失时，先下载准备：

```sh
.venv/bin/python scripts/setup_agent_runtime.py
.venv/bin/python scripts/prepare_local_runtime.py
```

下载步骤需要网络。另一台机器的 `.venv` 和 `python.cwasm` 不应直接作为当前机器的可用环境。

### 前端安装与构建

前端依赖由 `frontend/package-lock.json` 锁定。需要重建页面时执行：

```sh
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
```

修改前端源码后，需要构建并重启本工程，才能通过正常工作台入口加载新页面。详细本机操作见[Mac 运行指南](LOCAL-MAC.md)；[Windows 指南](LOCAL-WINDOWS.md)保留来源平台操作参考，本次最终版验收环境为 Mac。

## 4. 配置说明

配置模板为根目录的[.env.example](.env.example)和数据服务的[.env.example](cd80-data-api/.env.example)。凭据只放后端环境，不写入前端或公开文档。

| 配置项 | 用途与当前约定 |
| --- | --- |
| `GITEE_AI_API_KEY`、`GITEE_AI_BASE_URL` | 模型服务凭据与接口地址 |
| `GITEE_AI_MODEL` | 主站默认模型，当前为 `GLM-5.3-Flash`；各应用仍有独立的允许型号约束 |
| `EXPLORE_RELATION_MODEL` | Explore 关联研究模型，当前默认 `GLM-5.3-Flash` |
| `DATA_DIR` | 主站业务数据目录，默认 `data`，相对项目根目录 |
| `FRONTEND_DIST`、`SKILLS_DIR` | 构建页面和产品 Skill 资源目录，默认 `frontend/dist`、`skills` |
| `AGENT_RUNTIME_DIR` | WASI 运行时目录，默认 `data/agent-runtime` |
| `IMPORT_LEGACY_HISTORY` | 旧历史接续开关，本实例保持 `0` |
| `CD80_DB_PATH` | 公共金融数据库路径，数据服务目录下默认 `data/cd80.sqlite3` |
| `CD80_DATA_API_URL` | 主站访问数据服务的地址；联合启动按实际端口设置，默认 `http://127.0.0.1:8897` |
| `CD80_ADMIN_API_KEY`、`CD80_READ_API_KEY` | 数据服务写入认证与可选读取认证，由主站后端转发 |
| `CD80_DATA_ADMIN_USERS` | 允许修改公共金融数据的平台用户 ID 白名单；空值不授予写权限 |
| `SFC_TOKEN` / `SFC_KEY_FILE` | 可选南财服务凭据；未配置时相应外部工具不可用 |
| `CAPABILITY_MCP_POLICY_FILE` | 远程 MCP 工具准入依据；留空时远程工具不可调用 |
| `STRATEGY_WORKSHOP_MARKET_DB`、`STRATEGY_WORKSHOP_SOURCE_DB`、`STRATEGY_WORKSHOP_MAPPING_DIR` | 策略诊断的规范行情库、源快照和逐标的映射，默认位于 `data/strategy-workshop-market/` |

联合启动先加载 `cd80-data-api/.env`，主站随后加载根 `.env`；已有进程环境优先，同名字段不会被后加载文件覆盖。金融数据密钥和写入白名单统一维护在数据服务 `.env`，避免两处配置不一致。模板中的旧端口值由联合启动参数覆盖，单独启动服务时需自行核对。

## 5. 技术架构与目录

| 层次 | 主要技术与职责 |
| --- | --- |
| 前端 | Vue 3、Vite；统一工作台及三个应用页面 |
| 主站 | Flask、Waitress；账号鉴权、业务接口、应用适配与资源服务 |
| AI 与研究协调 | LangGraph、Gitee AI；对话、研究流程、工具协调与状态持久化 |
| 数据服务 | FastAPI、Uvicorn、SQLite；表结构、分页筛选、查询与受控写入 |
| 隔离执行 | Wasmtime、WASI Python；应用代码、工具实现及自定义策略执行 |
| 本地存储 | SQLite 与文件目录；账号、会话、版本、执行记录、资料及制品 |

浏览器访问 Flask 主站；主站根据业务调用模型、工具、隔离执行器和独立金融数据 API。三个应用复用平台登录态，并在服务端校验各自数据的账号归属。当前服务面向本机访问。

以下列出主要目录，最多展开至第三级；依赖、缓存和业务数据仅列入口：

```text
最终版/
├── backend/
│   └── app/
│       ├── __init__.py          Flask 应用工厂与模块注册
│       ├── config.py            主站配置
│       └── modules/             平台、Explore、策略工坊、应用工坊等模块
├── frontend/
│   ├── src/
│   │   ├── main.js              前端入口
│   │   └── modules/             工作台与三个应用的页面和状态
│   ├── dist/                    主站实际提供的构建页面
│   └── package.json             前端依赖和开发命令
├── cd80-data-api/
│   ├── app/                     独立金融数据服务
│   ├── data/                    公共金融 SQLite 数据库
│   ├── docs/                    数据接口契约和数据字典
│   ├── scripts/                 导入、校验、配置与联合启动
│   └── tests/                   数据服务测试
├── strategy_workshop/
│   ├── strategies/              十种内置策略实现
│   ├── engine.py                策略计算与诊断回测
│   └── cli.py                   独立策略命令行入口
├── capabilities/
│   └── local-financial/         本地金融能力源码、包与发布资源
├── skills/                      产品运行时 Skill 指令与资源
├── scripts/                     本机管理、运行时准备及验收脚本
├── tests/                       主站、应用与整合测试
├── docs/
│   ├── integration/             当前最终版整合设计、进度与证据
│   ├── asset-authoring/          App / Skill / Tool 统一创作规范
│   ├── capability-library/       能力规范、Schema 与运行依赖示例
│   ├── financial-data/           金融数据接入与使用说明
│   ├── records/                  阶段修复与维护记录
│   └── archive/                  来源项目历史资料
├── data/                        私人业务数据、行情、运行时及执行制品
├── .run/                        本工程进程记录与日志
├── .local-backups/              私有本地备份
├── .venv/                       本机 Python 环境
├── .env.example                 主站配置模板
├── main.py                      主站启动器
├── requirements.txt             主站 Python 依赖
├── 启动最终版.command
├── 停止最终版.command
└── 重启最终版.command
```

`skills/` 是产品执行所用资源；`.agents/` 是开发协作资源，二者用途不同。`docs/capability-library/examples/`、Schema 和能力包也可能被运行时代码读取，整理文档时应保留其实际路径。

## 6. 测试与已记录的验收

从项目根目录执行主站和前端测试：

```sh
PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests
npm --prefix frontend test
```

金融数据服务测试需要额外开发依赖，并从其目录运行：

```sh
.venv/bin/python -m pip install -r cd80-data-api/requirements-dev.txt
cd cd80-data-api
../.venv/bin/python -m pytest tests -q
```

测试使用隔离账号、合成输入和临时数据目录；浏览器验收与真实模型验收另行执行，具体命令及边界见[测试指南](tests/README.md)和[脚本说明](scripts/README.md)。

以下是已有日志所记录的结果，日期均为 **2026-10-14**，不是本次 README 编写重新运行的结果：

| 验收阶段 | 记录结果 | 证据 |
| --- | --- | --- |
| 三应用整合基线 | 后端 1178 项、前端 279 项、数据服务 50 项通过，生产构建通过 | 整合验收报告（本地资料，未随源码发布） |
| 后续 Skills / Tools 目录更新 | 前端全量 326 项、相关后端 72 项通过，隔离浏览器和正式资源检查通过 | 目录更新验收（本地资料，未随源码发布） |
| 真实依赖和业务流程 | 平台聊天、Explore 研究、策略诊断、应用生成与运行分别有真实链路记录；同时保留失败与恢复证据 | [整合证据索引](docs/integration/README.md) |

不同阶段的测试数量存在重叠，不累计为总数；后续专项也不等于再次执行后端全量。已有真实调用记录只证明其时点、环境和样例范围。

## 7. 数据、备份与使用边界

### 数据管理

- 根 `data/` 保存私人业务历史和运行资源；`cd80-data-api/data/` 保存公共金融数据库，二者需要分别备份。
- 公共数据初始导入为 37 张表、19,274,362 条记录；这是导入基线，当前行数以实际接口为准。源表保留原字段，每表增加 `_row_id`，原始 `id` / `ID` 不作为服务主键。
- 数据 API 支持查询与受控写入。前端写入需同时满足平台账号白名单和后端管理密钥要求。
- Mac/Linux 的 MCP 连接凭据由本实例密钥加密。备份能力数据库时须一并保存 `data/.capability-mcp-key-v1`，并保留私密文件权限。
- 搬机、恢复或部署调整前，按[运行与数据说明](docs/integration/运行与数据说明.md)停服备份；恢复时保留当前新增数据。

公开分享源码时，排除两份 `.env`、业务数据、数据库、用户文件、日志、依赖环境和 `.local-backups/`。当前工程保留 `frontend/dist/`，因为主站直接读取构建页面；同时保留产品 Skills、能力包、Schema 与必要示例。

### 当前功能范围

- 策略工坊目前支持日频、单标的、只做多、无杠杆的诊断回测。行情缺失、复权和交易规则尚有待核验项，正式收益报告门槛保持关闭。
- 十种内置策略覆盖 SMA/EMA 趋势、MACD/ROC 动量、RSI/布林带均值回归、Donchian/Keltner 突破、Chandelier 趋势与 Stochastic 反转。
- 应用工坊沿用自身的推荐和组合范围；策略工坊可以作为推荐跳转，Explore、任意社区资产和全平台新 App 不会自动成为组合来源。
- 外部模型、搜索、新闻、金融工具和远程 MCP 的可用性取决于配置、上游服务与准入。服务健康不等于凭据有效，也不证明所有数据实时、完整或具备相同使用权限。
- 当前完成本机整合；跨应用自动编排、来源私人历史迁移、公网部署和实盘交易不在本次交付范围内。

## 8. 常见问题与文档导航

| 现象 | 检查方向 |
| --- | --- |
| 启动失败或提示端口占用 | 查看 `.run/server.log` 和 `manage_local.py status`，核对 8894/8897 的占用与进程归属 |
| 只复制源码后无法启动 | 检查 Python 依赖、`frontend/dist/`、公共金融数据库及 WASI 资源 |
| 模型或某个工具不可用 | 检查后端凭据、联网情况和对应连接准入；健康接口仅反映服务状态 |
| 新应用保存后不能运行 | 检查执行实现、输入绑定、精确版本验证、启用状态和运行连接 |
| 修改页面后仍显示旧内容 | 重新构建 `frontend/dist/`，再重启本工程 |
| 旧文档中的端口、型号或数量不同 | 当前操作以 Mac 指南、整合模块和项目状态为准；归档材料对应历史环境 |

| 文档 | 内容 |
| --- | --- |
| [Mac 运行指南](LOCAL-MAC.md) | 安装、启停、健康检查和 WASI 准备 |
| 项目状态（本地资料，未随源码发布） / 变更记录（本地资料，未随源码发布） | 当前实现与各阶段变更 |
| [功能对应表](整合说明与功能对应表.md) | 平台及三个应用的功能接入位置 |
| [整合文档](docs/integration/README.md) | 产品设计、技术规范、开发进度及验收索引 |
| [Explore 接入](docs/integration/Explore接入.md) | 事件、关联、历史及研究记忆 |
| [策略工坊接入](docs/integration/策略工坊接入.md) | 规则、行情、回测、保存与复跑 |
| [应用工坊接入](docs/integration/应用工坊接入.md) | 推荐、组合、保存及验证边界 |
| [Apps 目录](docs/integration/Apps双栏目录与创建.md) / [Skills 与 Tools 目录](docs/integration/Skills与Tools双栏目录.md) | 个人/平台目录、创建、添加与公开范围 |
| [资产创作规范](docs/asset-authoring/README.md) | App、Skill、Tool 的完整实现、版本与验证 |
| [数据服务契约](cd80-data-api/docs/CONTRACT.md) / [数据管理使用说明](docs/financial-data/数据管理使用说明.md) | 表、字段、查询与写入权限 |
| [文档总索引](docs/README.md) / [贡献指南](CONTRIBUTING.md) | 模块导航和开发协作约定 |
