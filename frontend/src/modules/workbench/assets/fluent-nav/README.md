# Fluent 侧栏图标模块

继承项目根 README 与 workbench 模块 README。仅提供静态图标，不拥有路由、状态、事件、网络或业务逻辑。

- `svg/`：34 个已适配 currentColor 的运行素材，22 个语义名称。
- `upstream/`：34 个原始 SVG；原始 Git blob SHA-1 已逐一校验。
- `index.js`：从 SVG 几何生成的静态注册表，供 SidebarIcon.vue 使用，不发网络请求。
- `icon-map.json`：22 个图标与中文功能的对应关系。
- `sources.json`：每个文件的来源、ref、上游 Git blob 校验、交付文件 SHA-256。
- `LICENSE.microsoft.txt`：微软 MIT 许可，必须保留。

普通导航使用 Regular，当前项使用其官方 Filled 文件；辅助动作按钮只提供 Regular，保持稳定。
20px 原生 viewBox 保持不变；侧栏主图标显示 20px，小工具可显示 16px/18px。
不要通过全局 stroke 重画轮廓，不要把远程或用户 SVG 注入注册表。维护后运行 `fluentNavIcons.test.mjs`。

来源：
- https://github.com/microsoft/fluentui-system-icons
- https://fluent2.microsoft.design/iconography

配色与菜单映射为本项目的适配，不代表微软的品牌背书。本包不包含微软产品商标图标或字体。
