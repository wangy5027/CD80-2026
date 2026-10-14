# 浏览器前端

本目录是成都八零平台的 Vue 3 + Vite 前端。`src/` 保存源码，`dist/` 保存构建产物。网站根目录的 Python 服务直接提供 `dist/`，日常运行无需安装 Node.js。

页面通过同源 `/desktop/api` 访问后端。账号、聊天和文件来自工作台服务；应用平台负责需求分流、确认及执行；应用工坊负责需求分析、推荐和创建个人应用；策略工坊负责策略匹配、自定义规则、回测和全过程对话。模型密钥只在后端配置，浏览器不接收或保存密钥。

## 开发与构建

使用 Node.js 20.19+ 或 22.12+，在项目根目录执行：

```powershell
npm --prefix frontend ci
npm --prefix frontend run dev
```

同时启动根目录的 `main.py`。开发代理默认指向 `http://127.0.0.1:8893`；后端端口不同时，将 `VITE_BACKEND_TARGET` 设置为对应的本机 HTTP origin。构建路径固定为 `/desktop/`。

```powershell
npm --prefix frontend run build
```

构建后刷新 Python 服务提供的页面即可加载新资源。后端与部署资源配置见 [项目说明](../README.md)。行情数据、模型凭据和 WASI 运行时需另行配置。

## 主要模块

| 路径 | 职责 |
| --- | --- |
| `src/modules/workbench/App.vue` | 登录、导航、聊天、项目、文件、日程和应用入口 |
| `src/modules/workbench/useWorkbench.js` | 工作台数据、主助手分流和聊天流 |
| `src/modules/workbench/useAgentPlatform.js` | 应用目录、状态轮询、确认、授权、保存和修订 |
| `src/modules/workbench/draftStore.js` | 按账号及任务保存未提交草稿 |
| `src/modules/workbench/fileLabels.js` | 文件名称和内容摘要展示 |
| `src/modules/app-workshop/` | 金融需求输入、分析与推荐、新应用介绍和直接保存 |
| `src/modules/strategy-workshop/StrategyWorkshop.vue` | 策略工坊各阶段界面与顶部风格切换 |
| `src/modules/strategy-workshop/useStrategyWorkshop.js` | 会话、匹配、自定义规则、回测和历史接口 |
| `src/modules/strategy-workshop/WorkshopConversation.vue` | 各阶段对话、规则、代码及结果快照 |
| `src/modules/strategy-workshop/WorkshopChart.vue` | 收益和回撤曲线 |
| `src/modules/strategy-workshop/language.js` | 小白／专家偏好和说明文案 |
| `src/modules/strategy-workshop/ruleLanguage.js` | 将已确定的规则转换为易读说明 |

## 交互约定

应用预览只显示流程。用户确认方案并完成所需文件授权后执行；修改规则或输入会产生新的运行。状态轮询只读取结果，不触发执行。

应用工坊统一使用简单直白的语言，依次展示需求输入、分析推荐、新 APP 介绍。第二页保留需求理解、处理步骤、工具与能力、推荐理由、已有覆盖与独立功能，末尾为“使用推荐 APP”和“创建新的 APP”，不再展示额外资源分析和补充需求表单。推荐范围包含策略工坊、其他内置应用和当前账号的个人应用；采用推荐应用时带入原问题与有效参数，停在可编辑页面。创建新 APP 时重新检索应用池，选取有可复用定义的相关应用，将实际工具、技能和流程组合并按需求修改。第三页展示参考应用、组合与修改方案及新应用功能，点击“添加到我的应用池”即可保存，不要求先运行；生成、跳转和保存均不自动执行。

策略工坊先分析想法并解释最接近的现有策略。用户可采用它，也可进入阶段二，补充并确认自定义条件，验证代码后回测。历史记录保留每个阶段的完整对话；结果页提供指标、曲线和折叠代码。完成结果页底部只保留“添加到我的个人应用”，保存后可在“应用”目录打开对应策略、参数和回测结果。个人策略应用提供“重新设置并回测”，可修改标的、起止日期和初始资金，再检查数据覆盖并明确确认运行；直接复用已保存的买卖规则及代码，每次创建独立运行。个人应用的回测草稿按账号和应用保存，刷新恢复，原结果继续保留。顶部历史入口保留。

策略工坊默认“小白模式”，可以切换“专家模式”。偏好按账号保存在当前浏览器；页面说明立即切换，后续 AI 回复使用所选风格。已有回复保留原文及其风格标记，切换不会重新生成策略或改变回测结果。

聊天和应用表单草稿同样按账号存入当前浏览器。提交失败保留输入，成功后清理已提交草稿；浏览器存储不可用时只保留当前页面会话中的状态。
