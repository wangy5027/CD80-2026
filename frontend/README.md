# 浏览器前端

本目录是成都八零平台的 Vue 3 + Vite 前端。`src/` 保存可编辑源码，`dist/` 是已构建的浏览器资源。日常运行在网站根目录执行 `python main.py` 即可；Python 后端直接提供 `dist/`，无需在运行时安装 Node.js 或执行 npm。

网站继续使用 `/desktop/api` 请求本地账号注册登录、聊天 SSE、项目、文件和日程接口。已登录后向 `/desktop/api/runtime` 读取公开的 LangGraph、Gitee AI 与 GLM-5.3 状态；网页不提供密钥输入，也不保存密钥。要启用模型，在网站根目录的 `.env` 设置 `GITEE_AI_API_KEY` 后重启 `python main.py`。状态中的“已配置”仅表示服务端读取到配置，不代表已验证上游连通。网站后端默认监听 `127.0.0.1:8893`。

修改源码时使用 Node 20.19+ 或 22.12+（本轮使用 Node 24.19.0）。在网站根目录执行 `npm --prefix frontend ci`、`npm --prefix frontend test`、`npm --prefix frontend run build`。测试包含 API、消息格式化及平台表单、幂等确认、账户切换、状态观察和修订参数。开发热更新可运行 `npm --prefix frontend run dev`，默认将 `/desktop/api` 代理到 `http://127.0.0.1:8893`；后端端口不同时设置 `VITE_BACKEND_TARGET` 为本机 Flask HTTP origin。构建产物使用固定 `/desktop/` 资源前缀。

## 应用平台入口

默认主助手模式通过 `/agent-dispatch` 识别普通对话、澄清、调用与创建。普通对话仍使用聊天 SSE；显式普通对话模式可以跳过意图识别。侧栏“应用”包含预置与个人应用、参数表单和运行记录。

| 文件 | 职责 |
| --- | --- |
| `useWorkbench.js` | 原工作台、主助手分流、识别取消和按账户恢复最近任务 |
| `useAgentPlatform.js` | 目录读取、只读轮询、确认/授权/取消/保存/修订动作 |
| `agentForms.js` | schema 参数、类型转换及本次文件授权 |
| `AgentInputForm.vue` | 文件与参数输入；个人应用默认不选择旧文件 |
| `AgentRunCard.vue` | 预览、步骤、确认、产物、反馈、保存及可编辑参数的修订 |
| `AgentPlatformPanel.vue` | 应用列表和运行列表 |

预览只显示流程，不运行工具或测试数据。修改规则、参数或文件会创建新的运行并重新确认。轮询只读取服务端状态；退出账号或切换任务后丢弃迟到响应，后台运行不会因页面断开自动停止。

## 浏览器验收

在项目根目录的两个终端依次运行：

```powershell
.\.venv\Scripts\python.exe tests/browser_fixture.py
node tests/browser_platform.mjs
```

需要已安装 WASI 运行时、本机 Edge，以及可解析的 Playwright 包；可通过 `PLAYWRIGHT_MODULE_DIR` 指向含 `playwright` 的 node_modules 目录。测试固定使用 `127.0.0.1:48993`，在临时数据库中操作合成账户和 CSV，使用模型替身和真实 WASI。结果及截图保存到忽略提交的 `data/browser-acceptance/`。结束后在服务终端按 Ctrl+C；真实模型验收另见[阶段验收记录](../docs/阶段验收记录.md)。
