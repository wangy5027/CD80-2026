# Explore 联网搜索修复与验收

日期：2026-10-13。项目：`/Users/jaylen/Desktop/成都八零/平台开发`。

版本说明：本文保留第二轮联网接入时的实际验证记录。随后增加了检索后澄清与材料确认，当前提交不会直接生成报告；最新行为和测试见 [澄清与材料确认验收](Explore-澄清与材料确认-交付验收.md)。以下历史测试数量不代表新版本测试结果。

## 问题与修复

首轮 Explore 只调用了 AkShare 最近快讯窗口，在已有记录中匹配问题，遗漏了平台已注册的 `web_search` 工具。因此，一般主题问题在快讯窗口中没有匹配材料时，无法继续进行真正的联网搜索。这是 Explore 的接入缺口，不是平台没有搜索工具。

现在提交研究问题后，Explore 通过现有 `workbench.runtime.tools` 调用 `web_search`，并继续使用 AkShare 的东方财富、财联社快讯。没有另建搜索平台，也没有新增服务密钥。

- 三个模块都主动搜索本次问题；历史模块另外查询历史案例材料。
- 搜索支持近一天、一周、一月或不限时间，按问题选择；放宽范围时明确记录，不把旧文或日期未知的网页说成最新新闻。
- 搜索与快讯至少一方取得材料时仍可分析；失败、空结果与缓存状态如实保留。
- 报告新增“本次检索”，展示搜索词、范围、检索时间、结果数量和状态；来源区区分联网网页、新闻快讯、用户输入。
- 取消信号传给原搜索工具，停止后不继续调用模型；重复提交、只读刷新、账号与分支隔离沿用原闭环。
- 链接用于搜索定位，不任意抓取链接正文。搜索摘要不等同于原文全文，未知发布日期不补造，也不作为有日期的历史证据。

复用位置：`backend/app/modules/workbench/builtin_tools.py` 注册 `web_search`，`search.py` 的 `GiteeSearch` 调用现有 Gitee `/v1/web-search`；AkShare 仍使用 `backend/app/modules/news/service.py`。模型仍是原 `GiteeProvider`。本次没有修改这些共享适配器，也没有改变普通聊天的工具流程。

## 使用

访问 <http://127.0.0.1:8894/desktop/#explore>，在“理解事件”输入类似“请搜索最近一周半导体行业的重要新闻，分析影响”，直接提交即可。无需预先提供新闻原文。也可使用“发现关联”或“历史验证”，不要求固定模块顺序。

旧报告保留原记录，不自动重新联网；要按新能力分析，可选旧报告后重新生成，或发起新的探索。

## 实际验证记录

先单独调用原有真实工具确认能力：`半导体 最新新闻`、`oneWeek` 返回 3 条带日期来源；`半导体产业 历史 政策`、`noLimit` 返回 5 条，含 2023 年材料。公开结果保存在 `data/explore-search-fix/search-live.json`。这只是工具连通性验证，完整 Explore 联调另列在最终验收结果中。

真实应用烟测从纯主题问题开始，不再先向问题粘贴一条已知新闻：

```bash
cd '/Users/jaylen/Desktop/成都八零/平台开发'
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' .venv/bin/python scripts/smoke_explore_live.py --topic '半导体产业' --require-web-results --screenshot --capture-model-output --output data/explore-search-fix/live-final
```

脚本使用临时账号和数据库，调用真实搜索、AkShare 和模型。每个模块必须保存真实网页来源和成功搜索记录；同时验证重复提交、读取恢复、Markdown 下载与页面截图。不会修改原账号的研究历史。模型调用会消耗既有额度。

自动化与浏览器测试使用明确的新闻、搜索和模型替身，不能作为真实服务联调的替代。

## 文件范围

- 后端：`backend/app/modules/explore/` 的检索流程、提示模板、版本、报告导出及 README。
- 前端：`frontend/src/modules/explore/` 的检索状态、报告检索记录、来源类型、测试及 README。
- 测试：`tests/test_explore.py`、`tests/browser_explore_fixture.py`、`tests/browser_explore.mjs`。
- 真实验收：`scripts/smoke_explore_live.py`、`scripts/capture_explore_live.mjs`。
- 文档和构建：根 README、Explore 两份验收记录、`frontend/dist/`。

## 最终验收结果

| 命令／范围 | 实际结果 |
| --- | --- |
| `.venv/bin/python -m unittest discover -s tests -v` | 207 项通过，33.666 秒；`data/explore-search-fix/backend-final-tests.log` |
| `.venv/bin/python -m unittest discover -s tests -p 'test_explore.py' -v` | Explore 专项 18/18，通过；明确的搜索、新闻和模型替身 |
| `npm --prefix frontend test` | 50/50，通过；`data/explore-search-fix/frontend-tests.log` |
| `npm --prefix frontend run check` | 语法检查和生产构建通过；`data/explore-search-fix/frontend-build.log` |
| `PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore.mjs` | 20/20，通过；0 页面错误，12 张实际页面截图、1 份 Markdown 下载；明确的服务替身 |
| `git diff --check`、脚本 Python/JS 语法检查 | 通过 |

浏览器验收报告：`data/explore-acceptance/2026-10-13T04-01-51-090Z/report.json`。除了原有闭环，新增检查：显示真实查询参数、阶段和结果状态；来源类型、历史不限时间查询、Markdown 检索记录；刷新、直达、幂等重放、点击推荐均不增加搜索调用。该组为替身测试，不作为真实联网证据。48994 专用测试服务已停止。

第一次真实主题联调确实执行了搜索，但模型返回 11 个事件主体，而原校验上限是 8，报告因此失败。失败结果保留在 `data/explore-search-fix/live/result.json`，没有计作通过。修复将主体上限调为与关系主体一致的 16，并在 v2 模板明确列表上限，按真实 11 主体结构补了回归测试。

本地服务于 12:06 重启更新。`data/explore-search-fix/local-service-check.json` 确认首页和健康接口 200、当前构建已提供、Explore/新闻匿名请求 401、8897 数据服务健康 200。没有改写原有研究报告。

最终真实联调在 12:03–12:07 完成，`data/explore-search-fix/live-final/result.json` 为 `ok=true`，实际调用新闻、搜索和 GLM-5.3。没有预先粘贴新闻正文，三个模块均保存了网页来源与实际搜索记录：

| 模块 | 搜索调用／每次可用返回 | 保存的网页来源 | 报告用时 | 结果 |
| --- | --- | --- | --- | --- |
| 理解事件 | 1 次／5 条，近一周 | 5 条 | 85.76 秒 | 报告保存、恢复读取和 Markdown 下载通过 |
| 发现关联 | 1 次／5 条，近一周 | 5 条 | 79.71 秒 | 报告保存、恢复读取和 Markdown 下载通过 |
| 历史验证 | 2 次／各 5 条，不限时间 | 去重后保留 5 条 | 47.46 秒 | 报告保存、恢复读取和 Markdown 下载通过 |

三个真实运行状态均为 `partial`：网页材料主要是搜索摘要，未阅读原文全文或独立核验；历史主题虽然完成了两次真实搜索，但本次没有取得符合历史案例资格的有日期材料，因此案例为空，不能验证判断。没有把“工具可用”说成“每个问题都能搜全”，也没有把近期新闻改成历史证据。搜索词提炼、资料覆盖率和历史检索质量仍可继续优化。

三个 Markdown 下载均为 HTTP 200；幂等重放返回原运行；最终读取保留三条记录；六张真实页面截图（含三张检索区截图）成功，页面脚本错误为 0。

证据与成品：

- [真实联调结果](../data/explore-search-fix/live-final/result.json)
- [真实理解事件报告](../data/explore-search-fix/live-final/event-report.md)
- [实际页面](../data/explore-search-fix/live-final/event-desktop.png)
- [实际联网搜索记录截图](../data/explore-search-fix/live-final/event-desktop-search.png)
- [历史模块实际搜索记录截图](../data/explore-search-fix/live-final/history-desktop-search.png)
- [浏览器交互回归报告（替身）](../data/explore-acceptance/2026-10-13T04-01-51-090Z/report.json)
