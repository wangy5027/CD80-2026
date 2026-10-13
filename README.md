# 成都八零 AI 原生金融应用平台

成都八零是面向个人金融研究与应用创作的本地网站，包含工作台、AI 应用平台和 AI 策略工坊。技术栈为 Vue 3 + Vite、Flask + Waitress、LangGraph、SQLite 和 Wasmtime。

## 当前功能

- **工作台**：本地账号、流式聊天、会话历史、项目、文件和日程；未提交草稿按账号保存在当前浏览器。
- **AI 应用平台**：主助手识别需求、澄清参数、调用已有应用，或生成包含工具和说明的新应用；用户确认流程和文件授权后执行，可保存为个人应用复用。
- **AI 策略工坊**：分析自然语言投资想法，从十种固定策略中返回最接近的一条，解释相同点和不同点，供用户选择原方案或坚持自己的思路。
- **自定义策略**：阶段二支持基于现有行情字段整理和补充买卖规则，用户确认后生成策略代码，完成隔离验证并确认采用，再进入回测。需要外部数据的策略会说明缺口，目前未开放外源数据上传或接入。
- **回测与历史**：设置标的、区间和资金，查看费用口径并检查数据覆盖，展示收益指标、收益曲线、回撤和可复制代码；历史记录可回看各阶段完整对话及对应结果。
- **语言风格**：策略工坊顶部默认“小白模式”，可切换“专家模式”，按账号记住选择。页面说明与后续回复使用所选风格，既有对话保留原文；切换不改变规则、代码或回测数值。

新应用的流程预览不执行工具；生成代码只在 WASI 隔离环境运行。策略工坊的正式收益报告仍受数据完整性门槛约束，数据未满足门槛时只开放明确标记的诊断回测。目前不提供实盘自动交易。

## 本地启动

后端使用 Python 3.10 或更高版本。Windows PowerShell 在项目根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe main.py
```

打开 [本地工作台](http://127.0.0.1:8893/desktop/)，注册或登录本地账号；从侧栏进入“应用”或“策略工坊”。服务仅监听 `127.0.0.1`，可使用 `main.py --port 8894` 更换端口。macOS/Linux 使用 `.venv/bin/python`。

日常启动直接使用仓库内的 `frontend/dist/`，无需安装 Node.js。模型、行情数据及隔离运行时需要在部署环境另行配置；仅克隆源码不能完成全部策略生成与回测功能。

## 部署配置

在本地自行创建根目录 `.env`，或设置以下环境变量。进程环境变量优先于 `.env`；模型密钥仅由后端读取。

| 配置 | 用途与默认值 |
| --- | --- |
| `GITEE_AI_API_KEY` | 模型服务凭据，启用 AI 功能前需配置 |
| `GITEE_AI_BASE_URL` | 模型接口，默认 `https://ai.gitee.com/v1` |
| `GITEE_AI_MODEL` | 默认 `GLM-5.3` |
| `PORT` | 默认 `8893` |
| `DATA_DIR` | 账号、业务记录及产物目录，默认 `data` |
| `FRONTEND_DIST` | 前端构建目录，默认 `frontend/dist` |
| `AGENT_RUNTIME_DIR` | 已配置并通过完整性校验的 WASI 运行时目录，默认 `data/agent-runtime` |
| `STRATEGY_WORKSHOP_SOURCE_DB` | 部署环境提供的原始行情库路径 |
| `STRATEGY_WORKSHOP_MARKET_DB` | 规范化行情库路径 |
| `STRATEGY_WORKSHOP_MAPPING_DIR` | 行情映射及血缘说明目录 |
| `STRATEGY_WORKSHOP_INSTRUMENTS` | 需要额外配置时使用的证券交易元数据 JSON |

具体配置默认值见 [backend/app/config.py](backend/app/config.py)。相对路径以项目根目录为基准；更改 `DATA_DIR` 不会自动迁移共享行情库或隔离运行时。

行情库、映射和运行时应使用与当前代码契约兼容的部署资源。运行时缺失或校验失败时，生成工具与自定义策略执行不可用。数据库文件、用户记录、行情、密钥、安装环境以及本地开发资料均不随源码发布。

## 前端开发

使用 Node.js 20.19+ 或 22.12+，在项目根目录执行：

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev
```

开发服务默认代理到 `http://127.0.0.1:8893`，需要同时启动后端。完成修改后构建发布资源：

```powershell
npm --prefix frontend run build
```

更多配置见 [前端说明](frontend/README.md)。

## 源码目录

| 路径 | 职责 |
| --- | --- |
| `main.py` | 加载本地配置，启动 Flask / Waitress |
| `backend/app/` | 应用工厂、配置和公共接口边界 |
| `backend/app/modules/workbench/` | 账号、聊天、项目、文件和日程 |
| `backend/app/modules/agent_platform/` | 意图分流、应用生成、确认、隔离执行与应用复用 |
| `backend/app/modules/strategy_workshop/` | 策略匹配、改写、对话历史、语言风格和回测任务协调 |
| `frontend/src/modules/workbench/` | 工作台与应用平台界面 |
| `frontend/src/modules/strategy-workshop/` | 策略工坊、语言切换、对话与图表 |
| `frontend/dist/` | Python 服务直接提供的已构建网页 |
| `strategy_workshop/` | 十种策略、指标、回测引擎、行情读取及数据校验代码 |

数据库访问代码属于运行源码，实际数据库和业务内容由部署环境独立管理。
