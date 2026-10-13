# Explore 交互优化交付记录

日期：2026-10-13。范围仅为现有 Explore 的文件池、历史、推荐操作和执行反馈，不代表原始产品文档全部实现。

## 已实现

- 右上角常驻当前会话文件池。材料在服务端保存后，执行页约每 1.6 秒刷新一次；查看旧报告时，同会话仍有后台任务则约每 2 秒观察一次。新文件带名称、类型和查看入口，逐项进入新增列表；旧会话初次载入的文件不误标新增。宽屏列表悬浮在右侧并为正文留空间；窄屏使用一行新增提示，点击展开全部新增文件。
- 桌面研究历史默认固定在最左侧；选会话、旧记录、推荐分支或新建探索不会自动收起。用户手动隐藏的偏好按账号保存。手机保持抽屉形式，遵照本轮“只有主动隐藏才隐藏”的要求，需用户手动关闭；模态抽屉打开时才约束焦点和隔离正文。
- 理解事件用蓝色，发现关联用紫色，历史验证用琥珀色。推荐卡、选中模块和报告标识保持一致，并保留文字标签。
- 报告中的推荐卡点击后直接执行，生成以该报告为来源的新分支。目标模块使用自己的文件上下文，保留用户尚未提交的草稿；重复点击与网络重试继续使用原有幂等保护。入口示例仍只填入输入框。
- 当前进度显示实际阶段、已取得的材料和原文入口、明确后的问题、公开模型输出字符数与阶段动画；已结束阶段用勾选标记。计数来自真实流式内容，不是完成百分比，不展示未校验 JSON 或模型内部推理。取消、删除、换执行轮次后，迟到进度与报告仍受同一事务条件保护。
- 调整历史长文本省略、底部“选择参考文件”文案、首屏进度与取消位置。保留蓝紫背景、统一输入框、来源核对、报告保存、下载和原有工作台行为。

## 修改文件

| 文件 | 变更 |
| --- | --- |
| `frontend/src/modules/explore/ExplorePanel.vue` | 悬浮文件池、持久历史、模块配色标识、推荐直接运行、真实进度与焦点行为 |
| `frontend/src/modules/explore/explore.css` | 左侧常驻布局、右上文件区、窄屏反馈、模块颜色、阶段与文件进入动画 |
| `frontend/src/modules/explore/useExplore.js` | 文件池运行中与后台观察、账号历史偏好、直接推荐提交与草稿保护 |
| `frontend/src/modules/explore/explore.test.mjs` | 新推荐、幂等、后台文件更新、迟到响应和历史偏好测试 |
| `backend/app/modules/explore/service.py` | 意图与报告模型输出计数，按秒节流，复用状态和执行标识校验 |
| `tests/test_explore.py` | 分块计数中途可见、只读恢复、账号隔离、取消/删除/旧执行禁止回写 |
| `tests/browser_explore_fixture.py` | 明确标识的分块模型替身和三模块推荐材料 |
| `tests/browser_explore.mjs` | 推荐直接执行与来源、草稿、历史/手机交互回归 |
| `tests/browser_explore_visual.mjs` | 新增文件、进度、三色推荐、首屏、小屏、动画和录屏验收 |
| `scripts/capture_explore_visual_live.mjs` | 真实模型进度采样、运行中材料与最终页面截图录屏 |
| `README.md`、前后端 Explore 的 `README.md` | 更新本轮实际行为及轮询边界 |
| `frontend/dist/index.html` 与对应 `assets/index-*.js/css` | 已重建的本地运行资源 |
| 本文 | 本轮交付及验证证据 |

没有重新选型新闻服务或模型：继续使用 `backend/app/modules/workbench/builtin_tools.py` 注册的 `web_search` 和 `search.py`、`backend/app/modules/news/service.py` 的东方财富/财联社快讯、`backend/app/modules/workbench/graph_runtime.py` 的 `GiteeProvider.json_tokens`。

## 启动与访问

现有本地服务地址：<http://127.0.0.1:8894/desktop/#explore>。本轮已在无活跃 Explore 任务时正常重启服务以加载后端进度字段；页面和健康检查返回 200，未登录 Explore API 返回 401，数据服务健康检查返回 200。

若服务未启动，在项目根目录运行：

```bash
cd '/Users/jaylen/Desktop/成都八零/平台开发'
zsh ./启动LangGraph网站.command
```

## 已运行的测试

```bash
npm --prefix frontend test
npm --prefix frontend run check
.venv/bin/python -m unittest discover -s tests -v
```

前端 70 项通过；完整后端 233 项通过；生产构建成功。日志保存在 `data/explore-ux/frontend-tests.log`、`backend-tests.log`、`build.log`。

浏览器功能测试使用临时数据库、合成账号、明确的搜索/新闻/模型替身：

```bash
.venv/bin/python tests/browser_explore_fixture.py
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore.mjs
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore_visual.mjs
```

功能套件 27/27 通过，记录位于 `data/explore-ux/business-final/report.json`。包含三模块、旧报告分支、推荐直接执行、刷新只读、取消重试、上下文、文件池、账户隔离、安全 Markdown 和普通聊天/Build/Evolve 回归。视觉测试曾发现展开文件列表挤占阅读高度，失败截图保留在 `data/explore-ux/visual-final/failure.png`，据此改成右侧悬浮布局，再对最终构建复测。

最终视觉套件 11/11 通过，记录位于 `data/explore-ux/visual-final/result.json`，页面错误为 0。报告完成前已经显示 7 份真实落盘的合成材料，后端公开输出计数从 404 增至 707；验证桌面历史与正文并排、隐藏偏好刷新恢复、三色推荐、1000×790 和 390×844 研究进度/取消首屏可见、小屏无横向溢出、旧报告重新生成后归顶且同记录轮询不跳动。

本机 Chrome 采样的静置背景和报告滚动各段均未观察到超过 50ms 的主线程任务；rAF 中位数与 P95 均为 16.7ms，采样段 Layout 事件为 0。这是本次机器和短采样的实测结果，不是所有设备的帧率保证。无头浏览器没有真的把首个标签页切为隐藏，隐藏/恢复事件另由明确模拟验证。

真实新闻与模型另用隔离临时账号/数据库验证，不读取日常用户研究记录：

```bash
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' .venv/bin/python scripts/smoke_explore_visual_live.py --output data/explore-ux/live-final
```

最终真实烟测成功：现有网页搜索返回 4 条结果，东方财富/财联社分别读取到 100/20 条快讯窗口；最终保留 8 份研究材料并生成带信息限制的 `partial` 报告。运行阶段约 151 秒完成，录制开始至观察到报告约 158 秒；分析期间记录到 108 个不同的正值输出计数，材料在报告完成前可见，背景与输入框保持同一节点，页面错误为 0。此处仅验证一次理解事件模块的真实链路，三模块业务组合由前述替身回归覆盖；不把二者混称为全部真实接口测试。

截图和录屏来自实际实现页面，未用设计稿代替。以下桌面截图与录屏使用真实新闻/模型；短片在 18 秒处剪去等待中段，保留原速的入口→研究及研究→报告过程，完整未剪辑 WebM 同目录保留。

- [实际运行：入口](../data/explore-ux/live-final/01-entry-desktop.png)
- [实际运行：资料到达与分析进度](../data/explore-ux/live-final/02d-model-output-progress.png)
- [实际运行：报告与悬浮文件池](../data/explore-ux/live-final/03-report-desktop.png)
- [实际运行：三色推荐](../data/explore-ux/live-final/05b-next-research.png)
- [实际运行：三状态短录屏](../data/explore-ux/live-final/actual-live-three-states.mp4)
- [最终手机布局（合成测试报告）](../data/explore-ux/visual-final/06-report-mobile.png)
- [分块进度录屏（明确的测试替身）](../data/explore-ux/visual-final/explore-material-progress-synthetic.webm)

真实烟测的 `06-report-mobile.png` 抓到关闭抽屉的动画中间帧，不作为最终手机布局证据；手机验收使用上方等待过渡结束后重拍的截图。录制脚本已增加过渡等待与阅读区归顶，追加改动通过语法检查，未为重拍这一张图再次调用模型。模型报告带有资料不足等限制，接口成功不表示专业分析正确性或投资效果通过验证。

## 本轮边界

文件池为服务端落盘后的近实时轮询更新，不是每条上游新闻到达时立即推送；网络延迟会延长可见时间。模型报告仍在完整返回且通过校验后交付，过程中展示来源材料、需求理解与输出计数。专业正确性评估、量化回测、严格历史时点验证、复杂关系图和长期自动画像仍不在本轮范围。
