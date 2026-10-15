# Explore UI 第三轮交付与验收

日期：2026-10-13。实际项目：`/Users/jaylen/Desktop/成都八零/平台开发`。

本轮在 V2 上增量修改，没有重写工作台，没有新增依赖。报告、文件、账号、新闻和模型继续使用现有数据流。UI 不改写报告结论，也不把部分完成改成成功。

## 实际修改文件

| 文件 | 修改内容 |
| --- | --- |
| `frontend/src/modules/explore/ExploreComposer.vue` | 单一玻璃外壳、去掉内层卡片/焦点环、边缘光避开文字；稳定来源槽；保留同一个 textarea，公开只读几何/选区快照 |
| `frontend/src/modules/explore/ExplorePanel.vue` | 提交衔接、局部报告落位、材料与真实活动、紧凑边界区、来源片段、日期、保存反馈和模板问题 |
| `frontend/src/modules/explore/explore.css` | Explore 局部层次、阅读密度、首屏重心、阶段和局部动效、移动端适配 |
| `frontend/src/modules/explore/useExplore.js` | 报告完成时，不覆盖用户已清除或另选的下一题来源 |
| `frontend/src/modules/explore/model.js` | 新来源范围/阶段标签，报告实际引用材料计数优先读报告 |
| `frontend/src/modules/explore/presentation.js`（新增） | 日期精度、显式证据映射、公开活动白名单投影 |
| `frontend/src/modules/explore/reportMarkdown.js`、`reportMarkdown.test.mjs`（新增） | Explore 专用安全 pipe 表格，不改共用格式器；单元格复用现有 HTML 转义 |
| `frontend/src/modules/explore/presentation.test.mjs`（新增） | 日期、来源映射、敏感字段排除、材料计数测试 |
| `frontend/src/modules/explore/explore.test.mjs` | 完成期间下一题来源保护回归 |
| `frontend/src/modules/explore/README.md` | V3 行为与验收入口 |
| `tests/browser_explore_ui_v3.mjs`（新增） | U01—U20 分层验收、截图、固定视口录制、性能记录 |
| `tests/browser_explore_ui_v3_representative.mjs`（新增） | 2–4千字代表性报告、时间线、表格、来源失败和限制的专门布局验收 |
| `tests/browser_explore.mjs` | 展开 V3 的检索/使用文件详情后继续原业务断言；没有删除安全/权限断言 |
| `scripts/smoke_explore_ui_v3_live.py`（新增） | 临时账号、临时库、随机端口的真实烟测入口 |
| `scripts/capture_explore_ui_v3_live.mjs`（新增） | 真实运行、来源、刷新与分支记录 |
| 本文 | 交付证据与未测边界 |
| `frontend/dist/index.html` 与 `assets/index-CNiBk_jh.js`、`assets/index-Cge2IaZB.css` | 实际构建产物；旧摘要产物随构建替换 |

没有修改后端源码。工作期间检测到并行研究工具链对 `backend/app/modules/explore/service.py`、`research_runtime.py` 的变化，已保留；没有重启或覆盖 8894 后端进程。后端回归另记录执行前后源码摘要，避免把较早结果笼统称为最新代码结果。

## D01—D08

| 条目 | 状态 | 实际交付 / 边界 |
| --- | --- | --- |
| D01 输入框 | 完成 | 单外壳、统一焦点、边缘光、固定来源槽，单 textarea 与 IME 防误发逻辑保留 |
| D02 报告密度 | 完成 | 核心发现紧凑，不确定性与限制默认展开；方法原文保留；提供正文入口。短合成与长布局内容分别验收 |
| D03 连续过渡 | 完成 | 提交即时请求；仅空草稿外壳位移；真实阶段局部反馈；完成仅页首/前三块落位；新分支只强调新节点 |
| D04 运行信息 | 完成 | 材料标题、范围与真实阶段优先；最多三条公开活动；字符数与全程放详情，不展示原始工具参数/内部错误，不生成百分比 |
| D05 首屏 | 完成 | 上移重心，具体可编辑模板只填入不执行；保留用户明确设置的历史显示偏好 |
| D06 来源/保存 | 完成，按证据降级 | 定位指定来源；仅显式关联才显示主张/引用片段；没有句级映射则明确说明。报告实际使用文本与仅检索材料分开。保存提示去重、2.8 秒收起，操作中保留 |
| D07 可选阅读增强 | 部分 | 已有“阅读正文”入口；完整目录、当前章节跟踪、宽屏双栏来源面板未做，继续使用已验收的模态来源抽屉 |
| D08 验收 | 分层完成 | 合成业务、布局压力、真实接口分别记录；原生输入法、原生缩放与 Safari 的未测项不记通过 |

## 使用与复用位置

本地页面：<http://127.0.0.1:8894/desktop/#explore>。现有 8894 静态页面已返回本轮最终 JS/CSS，HTTP 200。已有页面请刷新。启动仍使用项目根目录的 `启动LangGraph网站.command`；开发前端可在项目根目录运行 `npm --prefix frontend run dev`，正式静态页在修改源码后使用 `npm --prefix frontend run build`。

网页搜索和模型适配仍由 `backend/app/modules/workbench/search.py`、`graph_runtime.py` 提供；Explore 继续通过自己的现有后端服务与研究运行流程使用它们。AkShare 新闻仍为 `backend/app/modules/news/service.py`、`provider.py` 和现有登录路由。浏览器只访问同源 API，不持有供应商密钥。

## 自动化命令与已核实结果

以下均在项目根目录执行。Playwright 使用本机已有依赖，没有添加应用依赖：

```sh
export PLAYWRIGHT_MODULE_DIR='/Users/jaylen/Library/Application Support/Qianwen/qwen-agent/resources/packages/npm/local/sets/5d1e1d04ae53027920a6f6fd56cf21e4/node_modules'
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python -m unittest discover -s tests -p 'test_explore*.py' -v
.venv/bin/python tests/browser_explore_fixture.py
```

浏览器脚本连接隔离替身服务 `http://127.0.0.1:48994/desktop/`，测试账号和数据均为临时生成。该 `TESTING` 服务走旧 provider 替身，只用于前端与业务回归，不代表生产 Tool/Skill 研究循环。

前端单元测试 90/90 通过；构建 53 modules 通过。第一次 Explore 后端测试 113/113 通过；后续并行版本的 121 项测试出现 3 项 `claim_review` 次数断言失败，保留在 `backend-tests-final.log`。并行后端继续更新后重测 **131/131 通过**，见 `backend-tests-verified.log` 与 `backend-verified-integrity.json`；该次执行前后后端源码摘要一致。本轮没有修改这些后端文件或断言。浏览器业务回归 27/27 通过，记录在 `data/explore-ui-v3/business-final-v2/report.json`。

较早的浏览器业务运行因 V3 将检索/上下文详情默认折叠，旧脚本在第 4 步的可见文案断言失败；适配脚本先展开详情后保留原内容断言，再跑满 27 项。该较早失败记录保留在 `data/explore-ui-v3/business-final/report.json`，没有覆盖成通过。

## 真实接口烟测

```sh
.venv/bin/python scripts/smoke_explore_ui_v3_live.py --output data/explore-ui-v3/live
.venv/bin/python scripts/smoke_explore_ui_v3_live.py --output data/explore-ui-v3/live-factual
.venv/bin/python scripts/smoke_news_live.py --output data/explore-ui-v3/news-live.json
```

第一次执行时录制器内置宽泛问题，第二次已改为窄事实问题；各次实际问题以对应 result.json 的 question 为准。上述 Explore 真实烟测使用当时的 `index-C63OJ31y.js`，后续最终构建追加了保存提示操作保护、来源使用范围/主张标签修复、真实模型子阶段文案和 Explore 安全表格支持；最终前端交互另在最终构建上验收，不冒称真实烟测发生于后来的摘要版本。

| 实际运行 | 结果 | 限制 |
| --- | --- | --- |
| 第一次宽泛问题 | **失败**；实际取得 14 条材料、8 次工具调用，复核拒绝保存报告 | 模型先出现信息缺口结构问题，修复后仍有无依据表述/引用问题；不能记为报告生成通过 |
| 第二次窄事实问题 | `partial` 报告保存成功，5 个真实网页来源，刷新恢复成功 | 明确声明未读取央行公告原文，仅有转载搜索摘要，未完成原文核对 |
| 从第二份报告创建关系分支 | `succeeded`，来源报告 ID 正确，6 个上下文文件，7 个来源 | 只证明本样例技术闭环，不构成专业判断正确性或完整新闻覆盖保证 |
| 独立 AkShare HTTP 烟测 | 东财/财联社均 200；分页、标题搜索和缓存复用通过；匿名 401、非法 limit 400、写请求 405 | 该独立验证不代表上述研究运行调用过新闻工具；它们的实际工具轨迹没有新闻工具调用 |

真实运行证据：`data/explore-ui-v3/live/result.json`、`live-factual/result.json`、`news-live.json`。真实模型长录屏为 `live-factual/real-v3-full.webm`，截图为 `03-report.png`、`07-branch-report.png`。`04-real-sources.png` 抓在入场初帧，不能作为抽屉展开的视觉通过证据；同轮读取到的 5 个原始来源链接保存在结果 JSON。没有使用设计稿替代运行画面。

## 截图、U01—U20、性能记录

改版前同视口实拍：`data/explore-ui-v3/before-v2-baseline/`。用户交付包原始参考材料保留在 `data/explore-ui-v3/reference/`，与本轮成品截图分开。

U01—U20 分项如下。"通过"只指表内已执行的路径，不把替身运行当作真实模型联调，也不把自动化中文插入当成原生输入法。

| 编号 | 状态 | 结果与边界 |
| --- | --- | --- |
| U01 | 通过 | 首屏、可编辑模板、点击模板不发研究请求 |
| U02 | 通过 | 鼠标焦点和 Tab，单一外壳反馈、独立按钮焦点 |
| U03 | 部分 | 合成组合事件未误发、选区保留；原生拼音候选回车未测 |
| U04 | 通过 | 即时请求、连续位置采样、只有一个输入节点 |
| U05 | 通过 | 运行期间可写下一题，草稿和选区保留 |
| U06 | 部分，分层验证 | 最终前端替身流程通过；真实 partial→保存和关系分支完成另在 C63 版本验证，未在最终构建重跑真实链路 |
| U07 | 通过 | 后台完成不选走当前旧报告 |
| U08 | 通过 | 快速模块与抽屉操作最终状态一致 |
| U09 | 部分 | S5 定位、摘录、回焦通过；精确第五条材料为明确标注的浏览器响应替身 |
| U10 | 通过 | 旧报告分支不覆盖原报告；替身及真实烟测均核对 source_report_id |
| U11 | 通过 | 轮询/刷新不重复提示或增加文件；操作中提示保持，不是 SSE 协议覆盖声明 |
| U12 | 部分 | 21,000+ 字报告与 12,569 字测试 DOM 追加可读，节点/草稿保留；生产模型报告流式增量未验证 |
| U13 | 通过 | 1440×900、1280×720、1440×700 控件可访问、正文与输入同轴 |
| U14 | 部分 | 1024×768、390×844、720×450 视口通过；原生文字缩放未测 |
| U15 | 未测 | 浏览器原生 125% / 200% 缩放未执行，缩小视口不等价 |
| U16 | 通过 | 开启/关闭/系统减少动效路径均可用，on/off trace 同负载对比 |
| U17 | 通过 | 取消、失败、重试、刷新、删除、迟到结果，业务套覆盖 |
| U18 | 通过 | 普通聊天、Build、Evolve 路由与业务回归 |
| U19 | 通过 | Markdown/来源安全、账号隔离，另有表格恶意内容专项单元测试 |
| U20 | 部分 | 两个固定视口独立录制，本次未复现旧视频灰边；不推断旧录制环境的原因 |

最终 CNiBk_jh 专项 **24/24**、代表性长文 **7/7**，均为 **0 页面异常**。业务 27/27 在上一 CjJ 构建执行；其后仅追加 Explore 展示层的真实模型子阶段文案与安全表格，未在最终摘要上重跑全部业务套。早一轮原始记录 `data/explore-ui-v3/final-cj/result.json` 保留版本信息。最终原始分项见下方链接。

性能设备：Apple M5 Pro，Mac17,9，15 逻辑 CPU；Playwright headless Chrome 154.0.8037.99，1440×900，DPR 1，系统减少动效关闭；未加 CPU 节流，未修改 headless 默认后台限速。不是可见 Safari/Chrome 窗口的人眼流畅度保证。原始 trace 可用 Chrome Performance 加载。

CjJ 同轮采样（后续最终增补版单列原始证据）：

| 负载 | 测量时长 | Layout 次数 | 主线程任务时间 | 观察到的 >50ms 长任务 |
| --- | --- | --- | --- | --- |
| 页面空闲动效 | 5.128s | 0 | 0.085s | 0 |
| 脚本插入 216 个中文字符 | 0.653s | 74 | 0.044s | 0 |
| 抽屉和控件连续操作 | 2.623s | 33 | 0.131s | 0 |
| 12,569 字测试 DOM 追加 | 1.426s | 22 | 0.093s | 0 |
| 21,721 字长报告滚动，动效开 | 5.216s | 1 | 0.472s | 0 |
| 同内容、同路径，动效关 | 5.216s | 0 | 0.272s | 0 |

长报告两种模式 rAF 间隔 p95 与最大值均 16.8ms；这是 **rAF 回调间隔，不是实际渲染 FPS**。六份 trace 在 `final-cj/`；视频 25fps 编码与这些指标没有等价关系。Safari 未测；原生 IME、原生浏览器缩放和持续 Vue/模型报告增量的限制保持明确，不宣称全设备流畅。

## 最终构建的截图与操作录屏

最终版本为 `index-CNiBk_jh.js` / `index-Cge2IaZB.css`；本地 8894 页面及两个资源均重新核实 HTTP 200。已关闭本次临时 48994 测试服务，保留原来的 8894 服务。

以下是实际应用运行截图，使用明确标注的合成测试材料；不是设计稿，也不代表真实金融结论。前后均来自 1440×900 浏览器窗口；两次运行日期、账号和记录 ID 不同。

| 状态 | 改造前 V2 | 改造后 V3 |
| --- | --- | --- |
| 入口与重心 | [前](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/before-v2-baseline/01-entry-glass.png) | [后](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/01-entry-glass.png) |
| 输入框聚焦 | [前](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/before-v2-baseline/03-input-focused.png) | [后](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/03-input-focused.png) |
| 研究过程 | [前](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/before-v2-baseline/05-real-fixture-research-stage.png) | [后](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/05-real-fixture-research-stage.png) |
| 报告首屏 | [前](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/before-v2-baseline/06-report-arrival.png) | [后](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/06-report-arrival.png) |
| 引用定位 | [前](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/before-v2-baseline/09-citation-focused-drawer.png) | [后](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/09-citation-focused-drawer.png) |

固定窗口录屏（浏览器测试替身）：

- [25.56 秒：入口 → 研究 → 报告 → 第五条来源 → 新分支](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/v3-fixed-1440-story-synthetic.mp4)。1440×900，报告静置超过 5 秒，来源约 3 秒；第五来源仅为浏览器响应替身。
- [6.40 秒：极短窗口中的报告、长草稿和文件抽屉](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/v3-fixed-390x300-short-window-synthetic.mp4)。390×300，独立固定窗口，没有中途改变视口造成黑边。
- [真实模型运行长录屏（较早 C63 构建）](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/live-factual/real-v3-full.webm)，对应前述 partial 报告与关系分支。不能与最终合成材料录屏混称。

代表性内容共 **3,506 汉字、8 个自定义小节、6 条时间线、5 行×8 列表格、5 类来源、1 次失败检索、3 条限制**。只替换浏览器 GET 回应，验收前后服务端原报告内容保持一致。

- [代表性报告首屏](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/representative-final-cnib-v3/01-representative-report-first-screen-1440x900.png)
- [安全表格与局部横滚](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/representative-final-cnib-v3/05-wide-markdown-table-1440x900.png)：表格宽 1,488px，局部容器 914px，整页保持 1,440px。
- [失败来源与限制](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/representative-final-cnib-v3/09-source-failure-limits-1440x900.png)
- [390px 手机宽度](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/representative-final-cnib-v3/10-representative-390x844.png)

这份新增代表性材料没有相同内容的旧版截图，不把普通短报告冒充它的改版前对照。最初脚本曾误要求正文普通 Markdown 链接转为可点击链接而失败；调整为现有安全契约（正文换行、来源抽屉中的安全原链接可点）后重新通过，早期失败目录保留。

## 最终性能与复现记录

最终构建沿用上文同一设备、浏览器与负载设置。以下六次观察到的 >50ms 长任务均为 0，只适用于这些测试路径。

| 负载 | 测量时长 | Layout 次数 | 主线程任务时间 |
| --- | --- | --- | --- |
| 页面空闲动效 | 5.121s | 0 | 0.076s |
| 脚本插入 216 个中文字符 | 0.644s | 74 | 0.042s |
| 抽屉和控件连续操作 | 2.611s | 32 | 0.117s |
| 12,569 字测试 DOM 追加 | 1.426s | 22 | 0.117s |
| 21,721 字长报告滚动，动效开 | 5.216s | 1 | 0.586s |
| 同内容、同路径，动效关 | 5.216s | 0 | 0.303s |

同一长报告滚动 on/off 的 rAF 间隔 p95 均 16.7ms、最大 16.8ms；不等同于渲染帧率。提交响应样例为 54ms，连续 20 帧记录输入框移动，始终只有一个 textarea；该耗时是本地替身服务数据，不是实际模型延迟。

最终浏览器运行命令如下（先在另一终端启动上文的临时 fixture，使用前述 `PLAYWRIGHT_MODULE_DIR`）：

```sh
EXPLORE_BROWSER_OUTPUT='data/explore-ui-v3/business-final-v2' node tests/browser_explore.mjs
EXPLORE_V3_BUSINESS_REPORT='data/explore-ui-v3/business-final-v2/report.json' EXPLORE_V3_BUSINESS_BUILD='/desktop/assets/index-CjJblpap.js' EXPLORE_UI_V3_OUTPUT='data/explore-ui-v3/final-cnib' node tests/browser_explore_ui_v3.mjs
EXPLORE_V3_REPRESENTATIVE_OUTPUT='data/explore-ui-v3/representative-final-cnib-v3' node tests/browser_explore_ui_v3_representative.mjs
```

第一条业务命令实际运行在 CjJ 构建，后两条在最终 CNiBk 构建。原始资料：

- [最终 24 项与 U01—U20、性能明细](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib/result.json)
- [代表性报告 7 项](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/representative-final-cnib-v3/result.json)
- [27 项业务回归](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/business-final-v2/report.json)
- [90 项前端日志](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/frontend-tests-final.log)
- [131 项后端日志](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/backend-tests-verified.log)
- [最终原始性能 traces 文件夹](/Users/jaylen/Desktop/成都八零/平台开发/data/explore-ui-v3/final-cnib)

待补验证：原生中文输入法候选确认、浏览器原生 125% / 200% 缩放、Safari、生产模型持续增量更新。可选目录/章节跟踪/宽屏双栏来源不属于本轮已交付功能。
