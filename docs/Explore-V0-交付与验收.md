# Explore V0 交付与验收

> 本页记录首轮实现与当时验收结果。首轮遗漏了平台已有的联网搜索工具，后续已补接，见 [联网搜索修复与验收](Explore-联网搜索修复与验收.md)。当前又增加了检索后澄清和材料确认，以 [澄清与材料确认验收](Explore-澄清与材料确认-交付验收.md) 为准。下文行为和测试数量仅代表首轮版本。

日期：2026-10-13（Asia/Shanghai）  
项目位置：`/Users/jaylen/Desktop/成都八零/平台开发`

本轮完成独立研究工作区的页面、真实模型分析、服务端报告保存和连续探索闭环。实现依据当前仓库和首页，复用已经存在的新闻接口。此交付不表示《产品需求文档》《产品设计文档》的全部功能已经完成。

## 访问与启动

地址：<http://127.0.0.1:8894/desktop/#explore>。沿用原有平台账号登录，也可从首页 Explore 卡片的“开始探索”进入；“返回工作台”返回首页。

双击项目内 `启动LangGraph网站.command`，或在终端运行：

```bash
cd '/Users/jaylen/Desktop/成都八零/平台开发'
./启动LangGraph网站.command
```

原启动器同时启动网站 8894 和现有数据服务 8897。若已经运行，直接访问页面，不重复启动。开发后更新前端产物使用 `npm --prefix frontend run build`；后端源码变更后需重启网站进程。依赖、登录和模型配置沿用现有项目，未新增需要填写的服务密钥。

选中报告后的地址为 `/desktop/#explore?session=…&run=…`，可收藏、直接打开、刷新和前进后退。记录以服务端为准；账号登录后恢复自己的数据。网页刷新不创建运行、不重新调用模型。

## 实现与当前边界

| 范围 | 已实现 |
| --- | --- |
| 独立页面 | 保留现有页面切换，浅色、青绿色、圆角与细边框；研究页使用一条历史栏，小屏折叠为抽屉；不向普通聊天填提示词 |
| 历史管理 | 新建探索、会话列表、重命名及删除会话、会话内报告回看和分支树 |
| 输入 | 任意选择三个模块；问题、新闻原文、链接共用多行输入；可编辑示例问题；来源报告持续显示 |
| 理解事件 | 概述、背景、主体、动因、影响路径、时间线、不确定性与来源 |
| 发现关联 | 主体、关系路径、方向、机制、条件、来源；列表及详情，未制作复杂图谱 |
| 历史验证 | 明确判断直接执行；只有事件先给可编辑判断，确认后才执行；对真实可得材料做定性对照 |
| 运行 | 实际服务端阶段、取消、失败重试、重新生成、超时中止；取消/删除后迟到返回不得写成成功 |
| 连续探索 | 任意旧报告创建新分支；原报告不覆盖；上下文只沿所选报告及最多四层必要祖先传递，截断会披露 |
| 结果 | 结构化报告、真实来源和检索时间、信息限制、0–3 条推荐、Markdown 下载；推荐点击只填输入框 |
| 保存 | SQLite 保存问题、模块、状态、来源报告、时间口径、模板版本、结果、来源与阶段事件 |
| 隔离 | 当前账号验证所有资源；重复网络提交复用运行；服务重启后未完成记录标为中断，不自动重跑 |

新闻服务提供东方财富/财联社的近期快讯窗口，**不具备完整历史全文检索**。Explore 每次有界读取两个来源各至多 100 条，按问题筛选；明确链接只匹配已有窗口中的真实 URL，匹配成功后不混入其他无关快讯。无法匹配的单独链接会提示粘贴原文，不声称抓取成功。用户原文保存为 `U1`，标为未经独立核实；与新闻同源时不构成独立交叉验证。

历史模块只接受本次实际取得、发布时间距运行截止至少 90 天的新闻作为历史案例来源。这是当前窗口能力内的保守筛选，**不是严格历史时点验证**。当前快讯不会被当成历史证据；没有足够材料时报告标记“部分完成”并说明缺口。Explore 尚未接入行情分析，不输出虚构收益率，也不声称做过量化回测。

以下列入后续开发，不用空按钮占位：长期知识画像及自动更新/删除联动、解释深度与多种呈现版本、复杂关系图和 K 线、完整历史检索和严格历史时点验证、行情与量化回测、跨材料交叉核验与研究质量优化。专业分析正确性评分和投资效果评估未执行。

## 复用位置和存储

| 能力 | 位置及说明 |
| --- | --- |
| 新闻 | `backend/app/modules/news/service.py` 的 `NewsService.get_news`，底层为同模块 `provider.py`；复用缓存、真实标题/摘要、原始链接和时间，不另建采集平台 |
| 模型 | `backend/app/modules/workbench/graph_runtime.py` 的 `GiteeProvider`、可取消响应及 JSON 传输；复用当前模型配置；结构解析复用 `agent_platform/planner.py` 的兼容解析器 |
| 登录 | `workbench_service.authenticate` 与现有 Cookie；无独立登录体系 |
| 页面 | `workbench/App.vue`、`useWorkbench.js` 的页面机制，增加 Explore hash 地址；保留普通聊天、Build、Evolve 入口 |
| Markdown | 复用 `workbench/pages/format.js` 的 HTML 转义渲染；模型链接不直接成为可执行外链，来源区另作 URL 校验 |
| 数据库 | 沿用现有 `DATA_DIR` 与 SQLite 部署；新增 `data/explore.sqlite3` 存研究记录，按账号隔离，不改普通聊天历史表 |

三个独立提示模板共用一个后端流程。API 前缀 `/desktop/api/explore`；详细契约与状态见 [后端 README](../backend/app/modules/explore/README.md)，页面说明见 [前端 README](../frontend/src/modules/explore/README.md)。

## 本轮新增与修改文件

下面只列本轮 Explore 工作范围。仓库开始时已有大量其他变更，未将它们作为本轮成果，也未回滚。

新增后端：

- `backend/app/modules/explore/__init__.py`
- `backend/app/modules/explore/repository.py`
- `backend/app/modules/explore/contracts.py`
- `backend/app/modules/explore/service.py`
- `backend/app/modules/explore/routes.py`
- `backend/app/modules/explore/README.md`
- `backend/app/modules/explore/prompts/event_v1.txt`
- `backend/app/modules/explore/prompts/relation_v1.txt`
- `backend/app/modules/explore/prompts/history_v1.txt`

新增前端：

- `frontend/src/modules/explore/ExplorePanel.vue`
- `frontend/src/modules/explore/useExplore.js`
- `frontend/src/modules/explore/api.js`
- `frontend/src/modules/explore/model.js`
- `frontend/src/modules/explore/explore.css`
- `frontend/src/modules/explore/explore.test.mjs`
- `frontend/src/modules/explore/README.md`

新增测试、验收脚本和文档：

- `tests/test_explore.py`
- `tests/browser_explore_fixture.py`
- `tests/browser_explore.mjs`
- `scripts/smoke_explore_live.py`
- `scripts/capture_explore_live.mjs`
- `docs/Explore-V0-交付与验收.md`（本文）

修改已有文件：

- `backend/app/__init__.py`：注册 Explore 服务和路由。
- `backend/app/config.py`：Explore 超时、并发及检索窗口配置。
- `backend/app/modules/workbench/service.py`：平台关闭时关闭 Explore 活动任务。
- `frontend/src/modules/workbench/App.vue`：首页 Explore 入口、页面挂载及侧栏协调。
- `frontend/src/modules/workbench/useWorkbench.js`：地址、刷新和前进后退恢复。
- `README.md`：Explore 入口、当前边界与本文链接。
- `frontend/dist/index.html`、构建生成的 `index-Cywst848.css` 和 `index-DseQ-nXE.js`；旧 hash 构建产物由构建工具替换。

## 实际验证

所有命令均在上述项目目录执行。自动化替身和真实服务分别记录，替身通过不代表真实接口通过。验收使用临时账号和临时数据库，没有对原账号的聊天、文件或比赛数据进行测试写入。

### 自动化与构建

| 实际命令 | 结果及证据 |
| --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | 201 项通过，32.057 秒；`data/explore-acceptance/backend-final-tests.log` |
| `.venv/bin/python -m unittest discover -s tests -p 'test_explore.py' -v` | Explore 专项 12 项通过；新闻和模型为明确替身，包含在上述全量测试中 |
| `npm --prefix frontend test` | 49 项通过，0 失败；`data/explore-acceptance/frontend-final-tests.log` |
| `npm --prefix frontend run build` | 成功构建；随后由下项再次执行生产构建 |
| `npm --prefix frontend run check` | 语法检查及生产构建成功，46 模块；`data/explore-acceptance/frontend-final-build.log` |

专项覆盖三模块直接/重复运行、旧报告分支和祖先范围、刷新只读、幂等冲突、取消/删除/超时后迟到结果、重启恢复、账号隔离、无历史材料、非法来源引用、无效模型结构、安全 Markdown 和非法请求类型。普通聊天、认证、模型传输、现有应用平台与工具执行等原有测试也在 201 项全量回归范围内。

浏览器验收命令（两个终端，运行后停止专用 fixture）：

```bash
.venv/bin/python tests/browser_explore_fixture.py
```

```bash
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore.mjs
```

此浏览器验收明确使用合成新闻和模型返回，检验真实页面交互、服务端保存和隔离。覆盖三模块、111/222 重复路径、1→2→3、旧报告分支、晚建子分支排序、刷新恢复、仅填入推荐、下载、判断确认、失败重试、重新生成、取消、安全渲染、重命名/删除、手机抽屉、普通聊天和 Build/Evolve 入口。Build/Evolve 检查为页面入口与原有基础行为，不表示本轮真实模型完整执行了它们。

### 真实接口

已运行新闻服务烟测：

```bash
.venv/bin/python scripts/smoke_news_live.py --output data/explore-acceptance/news-live-check.json
```

东方财富、财联社真实 HTTP 均为 200，窗口分别 200 和 20 条；缓存复用、分页和搜索通过；匿名访问 401、非法参数 400、写入方法 405。详见 `data/explore-acceptance/news-live-check.json`。

Explore 三模块真实烟测命令（会消耗现有模型额度）：

```bash
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' .venv/bin/python scripts/smoke_explore_live.py --screenshot --capture-model-output --output data/explore-acceptance/live-release
```

使用本机已配置模型与真实新闻，运行理解事件→发现关联→历史验证，并检查幂等重放、保存读取、三个 Markdown 下载和三个实际页面截图。原始模型输出仅包含本次公开新闻烟测内容，供诊断；不保存模型密钥或登录 Cookie。

早期真实联调出现过结构校验失败；其中一次已明确定位为可选推荐项给出了不支持的模块枚举。后端已兼容丢弃无效可选推荐，并收紧模板、结构解析和新闻关联筛选。最终结论以冻结代码后的 `live-release/result.json` 为准，未把早期失败记为通过。

## 最终验收证据

冻结代码后的真实三模块烟测于北京时间 11:35–11:37 完成，使用 GLM-5.3。流程检查 `ok=true`，三条运行恢复读取一致、重复提交返回同一运行，三份 Markdown 下载均为 HTTP 200，三个报告页面均无脚本错误。

| 真实模块 | 耗时 | 报告状态 | 证据边界 |
| --- | --- | --- | --- |
| 理解事件 | 44.23 秒 | partial／部分完成 | 只有一条真实快讯摘要，用户输入与其同源，无独立交叉验证 |
| 发现关联 | 48.30 秒 | partial／部分完成 | 主体关系依据上述快讯；来源未点名的产业链关系不能建立 |
| 历史验证 | 34.27 秒 | partial／部分完成 | 未取得合格历史来源，历史案例为空，明确不能验证判断；未做行情验证 |

`partial` 表示报告已生成、保存且可下载，但研究材料不足；不能将其描述为历史判断已验证或专业结论已证明。

- [真实联调结果 JSON](../data/explore-acceptance/live-release/result.json)
- 真实报告：[理解事件](../data/explore-acceptance/live-release/event-report.md)、[发现关联](../data/explore-acceptance/live-release/relation-report.md)、[历史验证](../data/explore-acceptance/live-release/history-report.md)
- 真实运行截图：[理解事件](../data/explore-acceptance/live-release/event-desktop.png)、[发现关联](../data/explore-acceptance/live-release/relation-desktop.png)、[历史验证](../data/explore-acceptance/live-release/history-desktop.png)
- 冻结版[浏览器验收报告](../data/explore-acceptance/2026-10-13T03-35-58-481Z/report.json)：19/19 通过、0 页面错误、11 张截图和 1 份下载；该组新闻与模型为测试替身。
- 浏览器交互截图：[旧报告分支](../data/explore-acceptance/2026-10-13T03-35-58-481Z/05a-late-branch-depth-first.png)、[手机历史抽屉](../data/explore-acceptance/2026-10-13T03-35-58-481Z/08-mobile-history-drawer.png)。这些同样是实际页面，但内容为明确的合成测试材料。

本地原启动器已重新启动并持续运行。11:40 的[服务检查](../data/explore-acceptance/local-service-check.json)确认网站健康和首页均为 200、首页使用本轮构建、Explore 与新闻匿名请求为 401、8897 数据服务健康为 200。启动日志在 `data/explore-acceptance/site.log`。临时浏览器测试服务 48994 已停止；真实烟测的临时数据库已清理，仅保留上述公开新闻报告与验收产物，所以烟测会话不会出现在原用户的研究列表中。

另执行了 `git diff --check`、`node --check scripts/capture_explore_live.mjs`、`.venv/bin/python -m py_compile scripts/smoke_explore_live.py`，均通过。
