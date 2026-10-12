# 浏览器前端

本目录是 Ficagent 网站的 Vue 3 + Vite 前端。`src/` 保存可编辑源码，`dist/` 是已构建的浏览器资源。日常运行在网站根目录执行 `python main.py` 即可；Python 后端直接提供 `dist/`，无需在运行时安装 Node.js 或执行 npm。

网站继续使用 `/desktop/api` 请求登录、聊天 SSE、项目、文件和日程接口；Coze Studio 与模型管理按钮使用后端的 `/desktop/coze/studio`、`/desktop/coze/models` 跳转。网站后端默认监听 `127.0.0.1:8893`，Coze 是否可用取决于其独立服务。

仅在修改前端源码时安装 Node.js 依赖并重建：在网站根目录执行 `npm --prefix frontend ci`、`npm --prefix frontend run build`。`npm --prefix frontend test` 检查 API 与消息格式化。开发热更新可运行 `npm --prefix frontend run dev`，默认将 `/desktop/api` 代理到 `http://127.0.0.1:8893`；后端端口不同时设置 `VITE_BACKEND_TARGET` 为本机 Flask HTTP origin。构建产物使用固定 `/desktop/` 资源前缀。
