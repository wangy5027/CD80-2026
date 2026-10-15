# 工作台模块

`App.vue` 负责登录后页面、导航、弹窗与各功能视图；`useWorkbench.js` 管理响应式状态以及登录、聊天、项目、文件、日程、模型和智能体操作。`api.js` 调用同源 `/desktop/api`，并逐块解析聊天 SSE。`components/Composer.vue` 和 `components/Icon.vue` 提供输入区与图标；`pages/format.js`、`pages/styles.css` 保留消息格式化和现有视觉样式。

工作台只在浏览器运行。注册和登录使用本站本地账号；模型和智能体在页面内选择。登录后从 `/desktop/api/runtime` 读取 `provider`、`model`、`configured`、`message` 四个公开字段，展示 LangGraph 运行方式与 Gitee AI 配置状态；前端不会要求或保存 API key。视频导演仅提供文字脚本和分镜建议，未实现工具执行或视频生成。开发热更新使用 `VITE_BACKEND_TARGET` 指向本站后端。

更新源码后执行 `npm --prefix frontend run build`，将新资源写入 `frontend/dist/`。正常使用由网站根目录的 `python main.py` 提供已构建资源，不需要 npm。

## Aurora Rose Glass 第一阶段（2026-10-15）

`auroraRose.css` 在登录后的 `.theme-aurora-rose` 下集中维护语义颜色、共享侧栏和首页顶部工具区；`HomeLanding.vue`、首页模式的 `Composer.vue` 维护对应玻璃表面。登录页、业务内容面板和状态语义色保留原实现；`LiquidAuroraRibbon.vue` 的几何与动画参数保持不变。范围、降级与验收见 [主题说明](../../../../docs/workbench/AuroraRose主题.md)。

## Fluent侧栏图标（2026-10-15）

`components/SidebarIcon.vue` 和 `assets/fluent-nav/` 提供用户指定的22个语义名、34个静态Fluent图标及原件/来源/许可，`pages/sidebar-fluent-icons.css` 仅约束侧栏与移动导航开关。App只替换相应图标标签；skills图标仍随extensions路由选中，旧Icon继续服务正文和首页。包内7项测试已纳入现有前端测试，许可副本位于public/third-party并随构建分发。固定映射、验证与限制见接入验收（本地资料，未随源码发布）。

## 当前结构补充（2026-10-13）

上方保留早期概述；当前模块还包含以下职责：

| 文件 | 职责 |
| --- | --- |
| useAgentPlatform.js | 应用目录、运行轮询、输入、确认、授权、取消、反馈、保存和修订 |
| AgentInputForm.vue / AgentRunCard.vue / AgentPlatformPanel.vue | 应用参数、单次运行与目录面板 |
| FinancialDataPanel.vue / financialDataApi.js / financialDataModel.js | 数据页面、同源接口和表单类型处理 |
| draftStore.js | 按账号和用途保存草稿，避免切页丢失与迟到响应覆盖 |
| fileLabels.js | 文件显示与区分 |
| newsApi.js | 独立新闻请求封装，当前不等于已提供新闻页面 |

能力库页面已落在 components/CapabilityLibraryPanel.vue，capabilityApi.js 负责接口封装；useWorkbench.js衔接能力选择、版本锁及调用事件。旧CapabilityDialog继续服务内置目录。详情见 [能力库规范](../../../../docs/capability-library/技术规范.md)。产品与状态见 [总索引](../../../../docs/README.md)，源码修改后需重建，纯文档整理不重建 dist。
