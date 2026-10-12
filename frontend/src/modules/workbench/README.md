# 工作台模块

`App.vue` 负责登录后页面、导航、弹窗与各功能视图；`useWorkbench.js` 管理响应式状态以及登录、聊天、项目、文件、日程、模型和智能体操作。`api.js` 调用同源 `/desktop/api`，并逐块解析聊天 SSE。`components/Composer.vue` 和 `components/Icon.vue` 提供输入区与图标；`pages/format.js`、`pages/styles.css` 保留消息格式化和现有视觉样式。

工作台只在浏览器运行。Coze 管理按钮打开网站后端的 `/desktop/coze/studio` 或 `/desktop/coze/models`；开发热更新时使用 `VITE_BACKEND_TARGET` 指向的后端。登录、聊天和模型配置依赖网站后端连接正在运行的 Coze 服务。

更新源码后执行 `npm --prefix frontend run build`，将新资源写入 `frontend/dist/`。正常使用由网站根目录的 `python main.py` 提供已构建资源，不需要 npm。
