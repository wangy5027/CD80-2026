# 浏览器前端

本目录是 Ficagent 网站的 Vue 3 + Vite 前端。`src/` 保存可编辑源码，`dist/` 是已构建的浏览器资源。日常运行在网站根目录执行 `python main.py` 即可；Python 后端直接提供 `dist/`，无需在运行时安装 Node.js 或执行 npm。

网站继续使用 `/desktop/api` 请求本地账号注册登录、聊天 SSE、项目、文件和日程接口。已登录后向 `/desktop/api/runtime` 读取公开的 LangGraph、Gitee AI 与 GLM-5.3 状态；网页不提供密钥输入，也不保存密钥。要启用模型，在网站根目录的 `.env` 设置 `GITEE_AI_API_KEY` 后重启 `python main.py`。状态中的“已配置”仅表示服务端读取到配置，不代表已验证上游连通。网站后端默认监听 `127.0.0.1:8893`。

仅在修改前端源码时安装 Node.js 依赖并重建：在网站根目录执行 `npm --prefix frontend ci`、`npm --prefix frontend run build`。`npm --prefix frontend test` 检查 API 与消息格式化。开发热更新可运行 `npm --prefix frontend run dev`，默认将 `/desktop/api` 代理到 `http://127.0.0.1:8893`；后端端口不同时设置 `VITE_BACKEND_TARGET` 为本机 Flask HTTP origin。构建产物使用固定 `/desktop/` 资源前缀。
