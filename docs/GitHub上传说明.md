# GitHub 源码上传范围

本仓库保存成都八零平台的应用代码与可重建材料。完整本机副本、运行数据和真实配置单独备份，不随源码提交。项目根目录的 `.gitignore` 是上传范围的依据；它不删除本机文件。

## 应提交的内容

| 范围 | 内容与理由 |
| --- | --- |
| `backend/`、`main.py` | Flask 后端、聊天、Explore、应用执行、金融数据代理、新闻与工具接口，以及模块内提示词和 Skills |
| `frontend/src/`、`frontend/index.html`、`frontend/vite.config.js` | Vue 前端源码、页面资源和构建配置 |
| `frontend/dist/` | 保留当前构建版本，约 0.5 MB；现有 Flask 启动流程直接服务该目录，新电脑日常启动不需要 Node |
| `frontend/package.json`、`frontend/package-lock.json` | 前端依赖和锁文件，不能忽略锁文件 |
| 根目录 `requirements.txt`、`cd80-data-api/requirements*.txt` | Python 依赖、开发依赖及已有锁定清单 |
| `cd80-data-api/app/`、`cd80-data-api/scripts/` | 数据 API、CSV 导入、校验、备份和启动代码 |
| `scripts/` | 环境准备、诊断及校验脚本 |
| `skills/` | 产品运行时读取的 Skill 定义，不是开发助手个人配置 |
| `.env.example`、`cd80-data-api/.env.example` | 空值或占位符配置模板，不包含实际账号和密钥 |
| README、使用说明、工程文档、接口契约、`cd80-data-api/docs/openapi.json` | 安装、架构、接口及开发说明；本机生成的证据目录除外 |
| `.gitignore`、`启动LangGraph网站.command` | 忽略规则及现有相对路径启动入口 |

保留体积很小且已被 README 引用的文字验收说明；其中的历史本机路径仅作为记录。截图、录屏、原始验证 JSON 和本机迁移清单单独留在完整备份。不要用 `*.json`、`*.csv`、`*.png` 等全局规则排除所有此类文件，以免漏掉合法源码资源和测试夹具。

## 不提交的内容

| 范围 | 原因 |
| --- | --- |
| 根目录和子目录的真实 `.env`、环境变体、私钥及凭据文件 | 模型、金融接口及数据库的实际凭据和本机配置 |
| `/data/` | 本地账号、密码校验信息、聊天、附件、研究记录、生成产物、WASI 二进制及大量验收录屏和性能轨迹 |
| `/cd80-data-api/data/` | 约 3.29 GB 的金融 SQLite 数据库，以及原始导入/验证清单；源码发布不包含比赛数据 |
| SQLite/DB 文件及其日志、临时构建文件 | 防止数据库复制到其他目录后误提交 |
| 比赛 `cd80_*.csv`、`Dataset Overview.xlsx`、`datasets/`、`raw_data/` | 原始数据另行管理；不因发布代码而一并公开 |
| 所有 `.venv/`、`node_modules/`、缓存、日志和覆盖率输出 | 可以重新安装或生成，且虚拟环境依赖原机器路径 |
| `docs/sqlite-sync-qa/`、`docs/project-relocation/`、`docs/news-live-check.json` | 本机截图、账号操作验收、迁移信息及实时验证结果 |
| `cd80-data-api/docs/data_catalog.json` | 从实际比赛数据生成的目录及样例，不是 API 的运行依赖 |
| 所有 `tests/` 目录 | 按发布要求排除，保留在本地，不上传 GitHub |
| `.agents/`、`.codex/`、编辑器配置、系统文件 | 本机开发工具材料，与运行时 `skills/` 不同 |
| 备份目录、压缩包、临时输出 | 完整备份含私有数据和配置，应保存在仓库外 |

## 在新电脑运行

以下命令从克隆后的项目根目录执行，适用于 macOS/Linux，Python 3.11 或更新。命令中的路径占位符必须替换为自己的实际路径。依赖版本以本仓库清单为准。

### 先运行网站

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
chmod 600 .env
.venv/bin/python scripts/setup_agent_runtime.py
.venv/bin/python main.py --port 8894
```

在本机 `.env` 中填写模型凭据，相关金融工具另行配置 `SFC_TOKEN`。新账号、会话数据库会在本机生成，GitHub 不提供原有账号或聊天记录。WASI 安装脚本需联网下载固定版本并校验，应用执行功能依赖它。金融数据服务未准备时，网站仍可启动，但数据管理功能不可用。

### 再准备金融数据服务

```bash
python3 -m venv cd80-data-api/.venv
cd80-data-api/.venv/bin/python -m pip install -r cd80-data-api/requirements.txt -r requirements.txt
cd80-data-api/.venv/bin/python cd80-data-api/scripts/init_config.py
```

数据库必须通过以下任一方式单独准备：

1. 从自己的私有完整备份恢复 `cd80-data-api/data/cd80.sqlite3`，不要把包含原账号和聊天记录的根 `data/` 当作公开示例分发。
2. 已有授权源数据时，显式指定 CSV 目录和字段字典执行导入，避免使用脚本中原机器的默认路径：

```bash
cd80-data-api/.venv/bin/python cd80-data-api/scripts/import_csv.py \
  --source '/path/to/authorized-csv-directory' \
  --dictionary '/path/to/Dataset Overview.xlsx' \
  --database cd80-data-api/data/cd80.sqlite3
```

没有数据库时，`start_all.py` 会明确拒绝启动；GitHub 源码本身不能恢复比赛数据。准备好数据库后，先停止前面单独运行的网站，再运行：

```bash
cd80-data-api/.venv/bin/python cd80-data-api/scripts/start_all.py
```

网站使用 8894 端口，数据 API 使用 8897。macOS 也可使用现有 `启动LangGraph网站.command`。管理权限根据新电脑注册账号的 ID 在本机配置，见数据服务 README；不复制原有用户的登录信息。Windows 将 `bin/python` 换成对应虚拟环境的 `Scripts/python.exe`。

修改前端后才需要 Node，并同步提交构建结果：

```bash
cd frontend
npm ci
npm run build
```

## 提交前核对

在项目根目录运行以下只读检查：

```bash
git status --short
git status --short --ignored
git ls-files -ci --exclude-standard
git add --dry-run --all
```

`git ls-files -ci --exclude-standard` 应无输出：它检查已经被 Git 跟踪、却又命中新忽略规则的文件。`.gitignore` 不能取消已有跟踪，也不能清除旧提交里的内容。若以后发现已跟踪的敏感文件，先核对具体路径，再仅取消其索引跟踪；若曾提交真实凭据，还需单独处理凭据和历史。

`final_v2` 使用当前源码的独立快照，排除所有 `tests/` 目录；本地原有测试文件和当前开发分支保留。`frontend/dist/` 为有意保留的构建文件。禁止使用 `git add -f` 绕过忽略规则上传本地运行数据。
