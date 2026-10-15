# Explore 直接执行与会话文件池

日期：2026-10-13。项目：`/Users/jaylen/Desktop/成都八零/平台开发`。

本轮按用户确认的范围，只覆盖 Explore 的理解事件、发现关联、历史验证三个研究模块。普通聊天、Build、Evolve 不接入此文件池。

## 使用流程

提交问题后，系统先用已有联网搜索和 AkShare 快讯检索材料，再判断需求是否明确。明确的问题直接分析并保存报告。仍不明确时展示检索信息和三个可执行方向；点击其中任何一个方向即开始执行，不再要求重复提交或额外确认。三个方向都不符合时，可以另填需求；系统再检查补充后的需求，清晰即执行，仍缺少信息才继续追问。

旧版本停在材料确认的记录仍可手动确认继续；刷新、读记录或切模块不会擅自执行等待中的记录。材料窗口保留来源、原链接、时间与实际可得文本；网页摘要和快讯摘录不会标成全文。

每个探索会话有一个服务端持久化的“研究文件”池。实际检索到的材料和完成的报告自动保存为有名称的 Markdown 文件，三个模块共用，可预览、下载、重命名和移除。移除文件不修改已经生成的报告及其历史使用记录。删除探索会话会删除其运行、报告、文件和偏好。

每个模块分别保存上下文选择：

- **自动选择**：围绕所选来源报告及必要祖先整理文件，优先当前模块；不会无提示地混入其他分支或普通聊天。
- **自行选择**：用户可以明确选入本会话其他分支的文件，组合研究；历史文件上下文只来自勾选文件。当前问题的新检索材料仍会独立加入，并在来源区展示。

每次运行冻结实际使用的文件名称、文本节选与截断说明。每次最多取 8 个历史文件、每个至多 1000 字；报告优先提取概述、核心发现与不确定性，材料优先提取实际可用摘录。界面显示节选及原因，完整文件可另行查看和下载。以后改名、移除文件或修改模块偏好，不改变旧报告的快照。模型仍按当前模块的分析任务工作，旧报告作为 AI 推论材料，不冒充新闻原始证据。

## 复用服务与变更范围

继续复用 `backend/app/modules/workbench/builtin_tools.py` 注册的 `web_search`、`workbench/search.py:GiteeSearch`、`news/service.py:NewsService.get_news` 和 `workbench/graph_runtime.py:GiteeProvider`。没有另建新闻采集平台或模型适配层。登录仍用原账号 Cookie，数据仍在 `DATA_DIR/explore.sqlite3`。

| 范围 | 本轮实际文件 |
| --- | --- |
| Explore 后端 | `backend/app/modules/explore/repository.py`、`service.py`、`routes.py`、`contracts.py`、新增 `prompts/event_v4.txt`、`relation_v4.txt`、`history_v4.txt`、`clarify_v3.txt` 及模块 README |
| Explore 前端 | `frontend/src/modules/explore/ExplorePanel.vue`、`useExplore.js`、`api.js`、`model.js`、`explore.css`、`explore.test.mjs`、README |
| 自动化验收 | `tests/test_explore.py`、新增 `tests/test_explore_pool.py`、`tests/browser_explore_fixture.py`、`tests/browser_explore.mjs` |
| 真实接口验收 | 新增 `scripts/smoke_explore_pool_live.py`；更新 `scripts/smoke_explore_guided_live.py`、`scripts/smoke_explore_live.py`、`scripts/capture_explore_live.mjs` |
| 文档 | `README.md`、新增 `docs/Explore-直接执行与会话文件池-交付验收.md`、`docs/Explore-澄清与材料确认-交付验收.md` 的版本说明 |
| 构建 | `frontend/dist/index.html`、`frontend/dist/assets/index-CpysPFVF.js`、`frontend/dist/assets/index-D6J9R72C.css`；替换上一轮生成的 assets |

仓库中其他早先改动不计入本轮；没有重构工作台和其他模块。

## 本地访问

地址：[Explore 工作区](http://127.0.0.1:8894/desktop/#explore)。登录原账号后使用；已有页面刷新可加载新前端。

```bash
cd '/Users/jaylen/Desktop/成都八零/平台开发'
./启动LangGraph网站.command
```

现有启动器同时运行网站 8894 和数据服务 8897。已运行时不需要重复启动。

## 验证记录

本轮实际执行以下检查。自动化与真实接口结果分开记录；历史报告中的证据不足不等于模型或新闻接口未调用。

```bash
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
cd frontend
npm test
npm run check
cd ..
# 浏览器验收前，在另一终端启动临时测试服务：
# .venv/bin/python tests/browser_explore_fixture.py
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore.mjs
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' .venv/bin/python scripts/smoke_explore_pool_live.py --screenshot --output data/explore-pool/live-attempt-1
```

单元和浏览器测试使用明确的搜索、新闻、模型替身。真实烟测使用配置中的真实新闻、联网搜索和模型，但只使用临时账号、临时数据库及公开问题，不读写原账号的研究历史。两类结果分别记录。

### 自动化、构建与服务检查结果

- 完整后端：**231/231 通过**，34.575 秒；其中 Explore 流程与文件池专项共 42 项。[完整日志](../data/explore-pool/backend-final-tests.log)。
- 前端：**61/61 通过**；`npm run check` 成功构建。[测试日志](../data/explore-pool/frontend-tests.log)、[构建日志](../data/explore-pool/frontend-build.log)。
- 浏览器：**27/27 通过**，页面脚本错误 0，19 张实际页面截图、2 份下载核验。[最终结果](../data/explore-acceptance/2026-10-13T05-52-19-355Z/report.json)。这组使用明确的合成搜索、新闻、模型替身。
- 浏览器覆盖：首页入口、三个模块分别选择第 1/2/3 个方向、自定义清晰直达、分支与刷新、文件池和模型实际取用、账号/会话隔离、取消重试、迟到结果、安全渲染、手机界面，以及普通聊天、Build、Evolve 页面回归。
- 额外修复并验证：文件日期含不同时区时仍按实际时间排序；澄清新轮检索尚未落盘就重启时，不重复迁移旧材料；用户移除的文件重启后不恢复。取消保留此前已入池的资料，阻止迟到结果新增。
- 本地网站已更新并重启；8894 网站及 8897 数据服务健康检查返回 200，新构建已加载，未登录的 Explore、新闻、文件池与上下文接口均返回 401。[服务检查记录](../data/explore-pool/local-service-check.json)。重启前运行中的 Explore 任务为 0。

新版文件池截图：[桌面](../data/explore-acceptance/2026-10-13T05-52-19-355Z/03d-session-file-pool.png)、[手机](../data/explore-acceptance/2026-10-13T05-52-19-355Z/08b-mobile-file-pool.png)。图中使用明确标注的合成验收材料。自动选中文件清单默认折叠，可展开查看文件名和选择理由。

### 已完成的真实接口烟测

[实际结果](../data/explore-pool/live-attempt-1/result.json) 使用同一临时探索会话，调用现有 GLM-5.3 模型、联网搜索和东方财富/财联社新闻服务，未替换任何响应。

| 模块 | 实际调用及交互 | 结果 |
| --- | --- | --- |
| 理解事件 | 模糊问题取得 3 个方向；点击选项；1 次意图模型、1 次报告模型 | 172.30 秒完成并保存，选项未再次经过意图模型 |
| 发现关联 | 使用事件报告和一份网页摘要；1 次意图模型、1 次报告模型 | 119.25 秒完成；模型实际旧上下文严格为选中的 2 份文件 |
| 历史验证 | 模糊问题后提交自定义明确判断；2 次意图模型、1 次报告模型 | 111.58 秒完成；模型实际旧上下文仅为选中的 1 份材料，没有隐式带入来源报告 |

三份报告均为 `partial`，保留原始材料限制。历史模块此次未查到符合日期条件的旧材料，因此案例为 0，明确说明未能完成历史证据验证；未伪造历史案例、行情或收益率。其他两个模块也明确标注摘要、转述、缺少独立核实等限制。此烟测证明调用、保存和交互闭环，不作为专业分析正确性或投资效果验证。

烟测还验证了创建和澄清幂等、材料和报告自动入池、精确正文下载、模块偏好保存、读取不调用模型、三个运行恢复，以及文件改名/移除不改变已有报告快照。公开请求、真实返回、Markdown 和截图保存在上述结果同目录。

### 实际运行截图

- [真实选项界面](../data/explore-pool/live-attempt-1/event-clarification-directions.png)
- [真实关联分析报告](../data/explore-pool/live-attempt-1/relation-report.png)
- [真实文件池及来源正文](../data/explore-pool/live-attempt-1/history-report-pool.png)

截图来自构建后的实际网页。自动化浏览器截图中的合成材料另有明确标识，不用设计稿替代成品。

## 当前边界

本轮文件池用于系统已取得的材料和报告，不包含本地文件上传、全文爬虫或全平台文件共享。搜索摘要与快讯窗口可能缺少完整正文、发布时间或历史证据，报告据实显示不足。未接行情，因此历史分析仍是定性对照，不生成收益率或量化回测结果。

完整自动画像、复杂关系图、严格历史时点验证、专业正确性评分、投资效果评估和研究质量优化仍属后续工作。本轮不表示两份原始产品文档已经全部实现。
