# Explore 澄清与材料确认

日期：2026-10-13。项目：`/Users/jaylen/Desktop/成都八零/平台开发`。

> 历史验收记录：本页对应上一轮“澄清后核对并确认材料”流程。新运行已调整为选项点击即执行、明确需求直接生成报告，并加入会话文件池；最新行为和本轮重新执行的测试见 [直接执行与会话文件池](Explore-直接执行与会话文件池-交付验收.md)。本页测试计数与产物保留为当时证据。

## 本轮行为

三个模块现在使用同一条有暂停点的流程：检索网页与快讯 → 检查研究意图 → 必要时请求澄清 → 核对问题及材料 → 用户确认 → 生成并保存报告。

需求不清楚时不会生成报告。页面展示已检索的信息、一个澄清问题，以及最多三个基于实际材料的候选方向。候选方向带材料编号，点击只填入可编辑问题框，用户提交后才重新查找。补充后仍不明确则继续澄清；用户明确的问题直接进入材料确认，不额外强迫选择方向。最新选择是当前需求，初始输入作为背景保留，不能反过来覆盖已经明确的问题。历史模块缺少待验证判断时，也先检索，再基于材料引导用户明确判断。

材料窗口提供来源、原始链接、发布时间、检索时间，以及实际使用的 `text_used` 文本。网页搜索只返回摘要、快讯只返回接口摘录时，页面明确标注其范围，不声称取得文章全文。选择来源后才调用报告模型，未选中的材料不会进入本次报告的来源输入。报告仍保留已选来源，支持查看和 Markdown 下载。

材料、检索记录、澄清历史、修订号和确认来源保存在服务端。刷新只读取；等待状态不会自动重新联网或生成报告。创建、澄清、确认均有幂等标识和状态约束，重复网络提交不重复执行；旧版本确认、跨账号访问、取消或删除后的迟到结果会被拒绝。继续探索只使用选定报告的必要祖先上下文。

## 新闻搜索修复

继续复用平台已有能力：

- `backend/app/modules/workbench/builtin_tools.py` 注册的 `web_search`，由 `workbench/search.py:GiteeSearch` 访问既有联网搜索接口。
- `backend/app/modules/news/service.py:NewsService.get_news` 的东方财富与财联社 AkShare 快讯窗口。
- `backend/app/modules/workbench/graph_runtime.py:GiteeProvider`，意图预判和报告分别使用独立提示模板。

本轮修正自然问句的快讯匹配，并把检索结果保存和展示提前到报告生成之前。因而检索已经完成但报告尚未生成、正在澄清或生成失败时，也能查看取得的材料。快讯窗口有限，不保证每个主题都能匹配；联网搜索失败、空结果与无日期来源仍如实显示。

## 文件范围

| 范围 | 文件 |
| --- | --- |
| 后端 | `backend/app/modules/explore/service.py`、`repository.py`、`routes.py`、`contracts.py`、新增 `prompts/clarify_v1.txt` 和 `clarify_v2.txt`、三个模块的 `*_v3.txt` 提示模板、模块 README |
| 前端 | `frontend/src/modules/explore/ExplorePanel.vue`、`useExplore.js`、`api.js`、`model.js`、`explore.css`、`explore.test.mjs`、README |
| 测试 | `tests/test_explore.py`、`tests/browser_explore_fixture.py`、`tests/browser_explore.mjs` |
| 真实接口验收 | 新增 `scripts/smoke_explore_guided_live.py`；更新 `scripts/smoke_explore_live.py`、`scripts/capture_explore_live.mjs` |
| 交付 | 根 README、本验收记录、旧验收文档版本说明、`frontend/dist/` |

本轮未改造工作台整体布局、普通聊天、Build、Evolve 或共享搜索/新闻/模型适配器。仓库其他已有改动不属于本轮。

## 启动与访问

```bash
cd '/Users/jaylen/Desktop/成都八零/平台开发'
./启动LangGraph网站.command
```

访问 <http://127.0.0.1:8894/desktop/#explore>，登录原账号后使用。服务已运行时不需重复启动；已有页面刷新即可加载最新前端。

## 验证方式

自动化和浏览器交互使用明确的新闻、搜索、模型替身，检查流程与权限，不作为真实接口联调证据。

真实烟测使用临时账号和数据库，保留公开测试问题、真实材料和模型输出；不写入原账号历史。脚本模拟测试用户明确选择方向、确认来源，应用本身不会自动确认：

```bash
export PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules'
.venv/bin/python scripts/smoke_explore_guided_live.py --modules event --screenshot --output data/explore-guided-fix/live-verified/event
.venv/bin/python scripts/smoke_explore_guided_live.py --modules relation --screenshot --output data/explore-guided-fix/live-verified/relation
.venv/bin/python scripts/smoke_explore_guided_live.py --modules history --screenshot --output data/explore-guided-fix/live-verified/history
```

检查三个模块检索与澄清、确认前报告调用为零、刷新无额外模型调用、澄清与确认幂等、仅使用所选材料及准确文本、真实报告保存、恢复和 Markdown 下载。实际结果在验收完成后记录于下节。

## 实际结果

真实首轮已取得新闻并显示材料，但选定方向后模型仍被最初“尚未确定”的输入影响，连续澄清而未进入报告。失败证据保留于 `data/explore-guided-fix/live/result.json`，没有计作通过。修复采用 `clarify@2`：显式区分当前请求与初始背景，保留未加标签的短/长新闻材料，将方向选项写为可直接执行的研究请求；增加了对应回归测试。

真实第二轮已通过一次方向选择进入材料确认，并验证确认前未调用报告模型；随后模型将事件概述返回为三个字符串字段，而原校验只允许一个字符串，因此报告保存失败。证据保留于 `data/explore-guided-fix/live-final/result.json`。对应修复明确三模块提示模板的字段类型，并只对实际出现的三键事件概述对象进行有界、无字段丢失的文字转换；未知形状仍拒绝。

| 命令／范围 | 已执行结果 |
| --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | 219/219 通过，34.505 秒；`data/explore-guided-fix/backend-final-tests.log` |
| `.venv/bin/python -m unittest discover -s tests -p 'test_explore.py' -v` | Explore 专项 30/30 通过；明确测试替身 |
| `npm --prefix frontend test` | 58/58 通过；`data/explore-guided-fix/frontend-tests.log` |
| `npm --prefix frontend run check` | 语法检查及生产构建通过；`data/explore-guided-fix/frontend-build.log` |
| `PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore.mjs` | 24/24 通过；0 页面错误，16 张实际 UI 截图，1 份下载；新闻、搜索、模型均为明确测试替身 |
| `git diff --check`、真实烟测脚本 Python/JS 语法检查 | 通过 |

浏览器结果：[验收报告](../data/explore-acceptance/2026-10-13T04-34-44-572Z/report.json)、[材料窗口实际截图](../data/explore-acceptance/2026-10-13T04-34-44-572Z/03a-materials-window.png)。覆盖三个模块、旧报告分支、澄清后材料确认、勾选过滤、幂等及过期版本拒绝、账号隔离、取消重试、刷新只读、安全 Markdown、手机抽屉和普通聊天/Build/Evolve 回归。

最终真实验收全部完成，汇总为 [真实接口结果](../data/explore-guided-fix/live-verified/result.json)。三个模块均从模糊问题开始，各调用两次意图模型（初次预判与选择方向后预判），明确选择一次后进入材料确认；确认之前报告模型调用为零，确认之后各调用一次报告模型。服务使用真实联网搜索、AkShare 和既有 GLM-5.3 配置，非测试替身。

| 模块 | 方向澄清次数 | 确认使用来源 | 全流程用时 | 结果 |
| --- | --- | --- | --- | --- |
| 理解事件 | 1 | 3 条网页、4 条快讯 | 164.05 秒 | 报告保存、读取恢复、Markdown 下载通过 |
| 发现关联 | 1 | 3 条网页、4 条快讯 | 126.80 秒 | 报告保存、读取恢复、Markdown 下载通过 |
| 历史验证 | 1 | 3 条网页、4 条快讯 | 146.17 秒 | 保存 3 项定性对照、读取恢复、Markdown 下载通过 |

三份报告均为 `partial`，明确保留搜索摘要、原文未核验、行情能力缺失等限制。历史报告的三项对照包含产业背景和两件 2024 年事件，关键案例主要依赖同一篇历史文章摘要，不能解释为三条独立证据或严格验证结论。没有把最新快讯冒充历史案例，也没有生成收益率。

真实烟测还确认：创建、澄清、确认的重复提交不额外执行；刷新等待状态不增加模型调用；报告模型收到的来源 ID 和 `text_used` 与勾选材料完全一致，未选来源不会传入；原始问题与明确后的问题均保留。三次下载均为 HTTP 200。生成 30 张实际页面截图（澄清、候选方向、确认材料、报告及检索区），页面脚本错误为 0。真实测试使用临时账号和数据库，未向正常用户历史写入烟测记录。

实际成品：

- [真实候选研究方向](../data/explore-guided-fix/live-verified/event/event-clarification-directions.png)
- [真实新闻材料确认窗口](../data/explore-guided-fix/live-verified/event/event-review-materials.png)
- [真实事件报告](../data/explore-guided-fix/live-verified/event/event-report.md)
- [真实关联报告](../data/explore-guided-fix/live-verified/relation/relation-report.md)
- [真实历史报告](../data/explore-guided-fix/live-verified/history/history-report.md)
- [本地服务健康及权限检查](../data/explore-guided-fix/local-service-check.json)

## 仍未实现

网页全文抓取、完整历史库、严格历史时点验证、行情回测、专业正确性或投资效果评分、自动长期画像和复杂图表不在本轮范围。意图判断和候选方向使用模型预判，用户仍需核对材料及问题；不能据此宣称原始两份产品文档已全部实现。
