# 工作台模块

`App.vue` 负责登录后页面、导航、弹窗与各功能视图；`useWorkbench.js` 管理响应式状态以及登录、聊天、项目、文件、日程、模型和智能体操作。`api.js` 调用同源 `/desktop/api`，并逐块解析聊天 SSE。`components/Composer.vue` 和 `components/Icon.vue` 提供输入区与图标；`pages/format.js`、`pages/styles.css` 保留消息格式化和现有视觉样式。

工作台只在浏览器运行。注册和登录使用本站本地账号；模型和智能体在页面内选择。登录后从 `/desktop/api/runtime` 读取 `provider`、`model`、`configured`、`message` 四个公开字段，展示 LangGraph 运行方式与 Gitee AI 配置状态；前端不会要求或保存 API key。视频导演仅提供文字脚本和分镜建议，未实现工具执行或视频生成。开发热更新使用 `VITE_BACKEND_TARGET` 指向本站后端。

更新源码后执行 `npm --prefix frontend run build`，将新资源写入 `frontend/dist/`。正常使用由网站根目录的 `python main.py` 提供已构建资源，不需要 npm。
