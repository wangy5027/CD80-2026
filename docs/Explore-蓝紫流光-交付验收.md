# Explore 蓝紫流光视觉交付

日期：2026-10-13。实际项目：`/Users/jaylen/Desktop/成都八零/平台开发`。

本轮在现有 Explore 上完成局部蓝紫主题。已完整阅读用户提供的视觉指令、动态 HTML 样张及受影响模块说明，并在本机 Chrome 打开样张，查看入口、研究中、报告阅读及同一入口状态下光影的连续变化。样张提供视觉参考，其预览状态按钮、示例报告与示例运行状态没有进入产品。本轮没有重写首页、工作台、后端服务或其他页面的品牌配色。

## 本地使用

打开 [Explore](http://127.0.0.1:8894/desktop/#explore)，使用原有账号登录；已打开的页面刷新后加载新版本。首页 Explore 卡片继续进入同一路由。

```bash
cd '/Users/jaylen/Desktop/成都八零/平台开发'
./启动LangGraph网站.command
```

8894 现有服务已返回最终构建 `index-DXBFpdyd.js`、`index-BX9VJlPp.css`，不需要因为本次前端更新重启后端。服务检查见 [local-service-check.json](../data/explore-visual/local-service-check.json)。

## 实现与保留能力

- 三个状态共用大面积蓝紫光场、黑色文字、近白输入及阅读面、少量胶囊控件。保留 Ficagent 标识，历史改为可从顶部打开的抽屉，桌面和手机都不会占用固定宽侧栏。
- `ExploreAmbientBackground.vue` 是工作区根下的独立装饰层，三个大范围渐变使用 23/28/31 秒 CSS transform 动画；不使用彩球、粒子、模糊滤镜、外部图像、逐帧 Vue 更新或全页重绘动画。手机减少一层。背景不参与事件命中和辅助阅读。
- 实际运行状态只改变背景上白色遮罩的透明度：入口 0、研究 0.48、报告 0.65。阅读面为 0.97 近白色，正文不跟随背景移动，侧边继续保留蓝紫光影。
- 暂停通过 `animation-play-state` 保持当前相位；支持用户暂停偏好、系统减少动态效果、页面隐藏暂停和离开时清理监听。系统要求减少动效时保持静态背景。
- 唯一的主输入框在三个状态中持续存在；装饰控件变化不重建输入框或丢失草稿、选择范围。加入组合输入状态、Safari `keyCode=229` 和候选确认后的短时保护。
- 当前执行阶段及取消入口优先显示，文件使用快照移到报告末尾；选择新记录、重试或重新生成回到记录顶部，同一记录轮询不强制重置阅读位置。
- 保留真实材料窗口、三模块、澄清选项、历史/分支、文件池与模块选择、取消/重试、保存、来源和 Markdown 下载。来源/文件窗口不改变底层报告宽度。

主题变量仅定义在 `.explore-theme`；传送到 body 的材料和文件弹窗带自己的主题作用域。普通聊天、Build、Evolve、账号和数据库逻辑没有在本轮重写。

新闻继续复用 `backend/app/modules/news/service.py:NewsService.get_news`；联网搜索继续复用 `backend/app/modules/workbench/builtin_tools.py` 注册的 `web_search` 与 `search.py:GiteeSearch`；模型继续复用 `graph_runtime.py:GiteeProvider`。没有替换服务或添加模拟业务到生产代码。

## 实际截图与录屏

以下是构建后的真实页面，使用临时账号和配置中的真实新闻、搜索与模型服务取得结果：

- [入口](../data/explore-visual/live-final/01-entry-desktop.png)
- [研究中](../data/explore-visual/live-final/02-research-desktop.png)
- [报告阅读](../data/explore-visual/live-final/03-report-desktop.png)
- [来源材料窗口](../data/explore-visual/live-final/04-materials-desktop.png)
- [会话文件池](../data/explore-visual/live-final/05-pool-desktop.png)
- [手机报告](../data/explore-visual/live-final/06-report-mobile.png)
- [约 31 秒三态短录屏](../data/explore-visual/live-final/actual-live-three-states.mp4)
- [完整未剪辑录屏](../data/explore-visual/live-final/actual-live-full.webm)

短录屏保留实际入口→研究、研究→报告切换，以原速播放，在第 18 秒接入后半段，**仅省略中间模型等待**；没有用设计稿、预览按钮或假模型返回替代真实业务。完整录屏保留等待过程。本次实际运行在约 156 秒取得保存结果。录制时的背景和输入框节点从头至尾保持同一实例。录制后补上的记录切换归顶修复由最终浏览器回归另行验证，不影响录屏中的首次运行路径。

另有明确标注合成材料的自动化验收截图和 [28.6 秒流程录屏](../data/explore-visual/2026-10-13T06-26-11-305Z/explore-entry-research-report-synthetic.mp4)。该录屏同样是实际 Explore 页面，但接口使用测试替身，不能作为真实接口联调证明。

## 实际执行的测试

在项目目录执行：

```bash
npm --prefix frontend test
npm --prefix frontend run check
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v

# 临时自动化站点，新闻和模型明确为测试替身：
.venv/bin/python tests/browser_explore_fixture.py
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore.mjs
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' node tests/browser_explore_visual.mjs

# 独立临时账号/数据库，复用现有真实配置；会调用真实模型：
PLAYWRIGHT_MODULE_DIR='/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules' .venv/bin/python scripts/smoke_explore_visual_live.py --output data/explore-visual/live-final
```

| 检查 | 本轮实际结果 | 证据 |
| --- | --- | --- |
| 前端单元 | 64/64 通过，包含动效偏好、页面隐藏、系统减少动效与监听清理 | [日志](../data/explore-visual/frontend-tests.log) |
| 前端检查/构建 | 成功，49 modules | [日志](../data/explore-visual/build.log) |
| 完整后端 | 231/231 通过，37.649 秒；接口使用明确测试替身 | [日志](../data/explore-visual/backend-tests.log) |
| 原业务浏览器 | 27/27 通过，页面错误 0；三模块、澄清、分支、刷新、文件池、取消重试、隔离、安全 Markdown、下载及聊天/Build/Evolve 回归 | [报告](../data/explore-acceptance/2026-10-13T06-18-07-866Z/report.json) |
| 最终视觉浏览器 | 9/9 通过，页面错误 0 | [报告及截图目录](../data/explore-visual/2026-10-13T06-26-11-305Z/result.json) |
| 本机 Safari | 实际打开并操作入口、中文粘贴/换行/提交、运行状态、报告、材料窗口、背景暂停/恢复、刷新、重新生成 | [版本与人工观察记录](../data/explore-visual/native-browser-check.json) |
| 真实接口 | 新闻、联网搜索、意图模型、报告模型完成调用，真实报告保存为 `partial`，8 条来源，背景和输入实例未重建 | [运行记录](../data/explore-visual/live-final/result.json) / [保存的完整运行](../data/explore-visual/live-final/saved-run.json) |

真实新闻检索记录：东方财富返回 100 条窗口内快讯、财联社返回 20 条；本次网页搜索返回 4 条。本次报告明确写出摘要未核验、缺官方原文和部分材料关联度不足等限制，所以结果是“部分完成”。这些数量证明接口返回，不代表所有材料都相关或专业分析正确。本轮未进行投资效果或分析质量评分。

最终视觉测试还验证：三状态背景及输入 DOM 不重建；暂停后动画时间冻结、恢复不重置；减少动效静态；草稿与选择范围不变；来源窗口与文件池打开前后报告宽度不变；390px 无横向溢出；1000×790 和 390×844 运行阶段/取消均在输入区上方；重新生成从旧报告底部 3352px 回到新记录 0px，随后的同记录轮询保留用户滚动位置。

### 动效与性能证据边界

机器：Apple M5 Pro、macOS 27.2；Chrome 154.0.8037.99、Safari 27.2。版本取自本机安装应用。

最终 Chrome 1440×900 的 CDP 记录分开采集：

| 采样 | 主线程 Layout | RunTask 数 / >50ms 数 / 最长 | rAF 间隔中位数 / P95 |
| --- | --- | --- | --- |
| 约 2.5 秒空闲背景 | 0 | 480 / 0 / 1.18 ms | 16.7 / 16.7 ms |
| 约 2.2 秒报告滚动 | 0 | 673 / 0 / 1.85 ms | 16.7 / 16.8 ms |

最终采样同时启用 `PerformanceObserver` 的 longtask 观察，两段均为 0 条。早先未采到 RunTask 的 trace 不用于长任务结论；上表仅引用补齐分类后的最终记录。

[空闲 trace](../data/explore-visual/2026-10-13T06-26-11-305Z/09-idle-ambient.trace.json) 与 [滚动 trace](../data/explore-visual/2026-10-13T06-26-11-305Z/10-report-scroll.trace.json) 可在浏览器 Performance 面板导入。录屏与这组测量支持本机此次操作未发现明显卡顿，不能据此保证所有设备、所有时长恒定 60fps。网络/模型的 156 秒等待单独记录，不能算成渲染耗时。

仍未完成的验收边界：

- Chrome 无头模式打开第二标签并未产生真实 `document.hidden`，该处理用模拟 `visibilitychange` 与单元测试验证；没有将其写成系统实际标签切换的相位测试。
- 本机 Safari 已实操，但未采集 Safari 帧时间 trace。没有把 Playwright WebKit 当作 Safari。
- 本机自动操作未取得原生中文输入法候选窗口。中文粘贴、多行输入与组合事件（包括 Safari 229）测试通过，**不等同于原生候选窗口已验证**。
- 本轮沿用既有只读轮询，没有新增文本流式输出或伪造打字效果。

## 本轮实际文件清单

| 文件 | 改动 |
| --- | --- |
| `frontend/src/modules/explore/ExploreAmbientBackground.vue` | 新增独立 CSS 光影及状态遮罩 |
| `frontend/src/modules/explore/useExploreMotion.js` | 新增动效偏好和浏览器生命周期管理 |
| `frontend/src/modules/explore/motion.test.mjs` | 新增 3 项动效生命周期测试 |
| `frontend/src/modules/explore/ExplorePanel.vue` | 局部主题接入、真实场景、抽屉、入口排版、持久输入、IME 保护、阅读顺序及切记录归顶 |
| `frontend/src/modules/explore/explore.css` | Explore 专属蓝紫色与响应式布局 |
| `frontend/src/modules/explore/README.md` | 更新主题和交互说明 |
| `tests/browser_explore_visual.mjs` | 新增视觉/交互、截图、录屏及性能采样 |
| `tests/browser_explore.mjs` | 原业务测试适应历史抽屉和会话管理入口，保留原断言 |
| `scripts/smoke_explore_visual_live.py` | 新增真实服务临时账号验收启动器 |
| `scripts/capture_explore_visual_live.mjs` | 新增真实三态录制、截图和持久化结果核验 |
| `README.md` | 新增本轮视觉导航 |
| `docs/Explore-蓝紫流光-交付验收.md` | 本交付记录 |
| `frontend/dist/index.html`、`frontend/dist/assets/index-DXBFpdyd.js`、`frontend/dist/assets/index-BX9VJlPp.css` | 重建并替换旧构建资源 |

`data/explore-visual/` 和 `data/explore-acceptance/` 为本轮本地证据，不是运行依赖。仓库中其余先前未提交的前后端改动没有计入本轮视觉范围。

完整自动画像、复杂关系图、严格历史时点验证、行情回测、研究质量优化等仍按既有计划后续推进；本轮不表示两份原始产品文档已全部实现。
