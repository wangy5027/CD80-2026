# Ficagent 网站：Vue 3 + Flask + LangGraph

Python 同时提供网页、账号、业务接口和流式对话。AI 使用 Gitee AI 的 GLM-5.3，由本地 LangGraph 管理上下文、模型调用和状态持久化。

## 启动

本机独立 Python 环境与模型密钥已经配置。在终端执行：

```bash
cd "/Users/jaylen/Desktop/成都八零/网站"
python main.py
```

浏览器打开 **http://127.0.0.1:8893/**。保持终端运行，按 `Ctrl+C` 停止。启动无需 Node.js、npm、桌面 App 或打包步骤。

首次使用请点击页面的 **创建账号**，设置本网站的邮箱和密码。账号保存在本机，不要求邮箱验证码；请使用自己能记住的密码。现有外部平台账号不作为本站凭证。

本机已启用 `IMPORT_LEGACY_HISTORY=1`：首次创建的本地账号会接续唯一一份旧数据中的 **4 条任务记录**。之后注册的账号拥有各自独立的数据。导入只进行一次，不会把同一份旧记录分给后续账号。原始文件已有外部备份。

## 模型与对话

- 模型服务：`https://ai.gitee.com/v1`
- 模型：`GLM-5.3`
- 内置角色：通用助手、编程专家、视频策划助手。
- 执行流程：准备系统提示词与历史 → 流式调用模型 → 保存最终回复。
- LangGraph 状态保存在本机 SQLite；任务消息、项目、文件和日程按账号保存，重启后可继续对话。

网页中的“已配置”表示后端已设置密钥，实际连通性在发送消息时确认。认证失败、额度/限流、网络错误会显示明确提示。模型返回内容会逐步显示；停止生成或连接中断时保留已经收到的内容。

附件上传、预览和下载继续可用；聊天支持文本、代码、CSV 等文本附件。当前不解析 PDF、图片和视频作为模型输入，也未接入代码执行、网页搜索或视频生成工具。视频策划角色输出脚本和分镜文本。

## 私有配置

后端自动读取网站根目录的 `.env`。本机真实密钥仅存放在该文件中，权限为 `600`，并已加入 `.gitignore`；前端和运行状态接口不会返回密钥。

换环境时参考 `.env.example`，设置以下变量：

```dotenv
PORT=8893
GITEE_AI_BASE_URL=https://ai.gitee.com/v1
GITEE_AI_MODEL=GLM-5.3
GITEE_AI_API_KEY=填写自己的访问令牌
DATA_DIR=data
FRONTEND_DIST=frontend/dist
IMPORT_LEGACY_HISTORY=0
```

终端环境变量优先于 `.env`，命令行 `--port`、`--data-dir` 可覆盖对应配置。路径相对于网站目录，不受终端当前目录影响。例如：

```bash
python main.py --port 8894
```

更改模型配置或后端代码后需要重启。网站只监听本机 `127.0.0.1`。

## 目录

```text
网站/
├── main.py                         日常启动入口
├── requirements.txt                Python 依赖
├── .env / .env.example              私有配置 / 无密钥示例
├── frontend/src/                   Vue 源码
├── frontend/dist/                  已生成的网页资源
├── backend/app/
│   ├── config.py                   配置
│   └── modules/workbench/
│       ├── auth.py                 本地账号和会话
│       ├── graph_runtime.py        LangGraph 与模型接口
│       ├── service.py              对话、项目、文件和日程
│       ├── routes.py               HTTP 与 SSE 接口
│       ├── store.py                账号数据持久化
│       └── errors.py               安全错误响应
├── data/
│   ├── auth.sqlite3                密码哈希与登录会话
│   ├── graph-checkpoints.sqlite3   LangGraph 状态
│   └── users/                     各账号任务、项目和文件
├── tests/                          自动回归测试
└── .venv/                          本机 Python 环境
```

网站业务代码和网页资源均不依赖相邻“开发”目录。`.env` 和 `data/` 应私下备份，不要放入公开代码包。

## 重建环境与开发

换电脑时使用 Python 3.10 或更高版本重新创建环境，不要直接搬用 `.venv`：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

本机验证环境为 Python 3.11，LangGraph 1.2.14，SQLite checkpoint 3.1.1。修改 Vue 源码后的可选构建步骤见 [前端说明](frontend/README.md)，日常启动使用现成 `dist/`。

运行 Python 回归测试：

```bash
.venv/bin/python -m unittest discover -s tests -v
```

自动测试使用临时账号、临时数据和本地模拟模型。2026-10-12 另已通过真实 GLM-5.3 → LangGraph → Flask 的流式连通性测试，模型返回“连接成功”；未向模型发送现有任务或文件内容。

本次验收通过 27 项 Python 回归测试、6 项前端测试及 8 项浏览器流程，涵盖登录、账号隔离、流式对话、停止生成、历史恢复、项目、附件和日程。完整记录保存在网站外的 `../整理备份/20261012-LangGraph验收/`。
