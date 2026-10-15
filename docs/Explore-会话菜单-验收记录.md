# Explore 会话菜单与重命名弹窗验收

日期：2026-10-13。对应“参考 ChatGPT 改进探索历史会话操作”的增量修改。

## 实现范围

- 每条探索会话提供“⋯”菜单，包含重命名、删除；可以直接操作非当前会话。
- 重命名使用居中浅色弹窗，自动选中旧名称，支持 Enter 保存、Esc / 取消 / 点击遮罩关闭。中文输入法确认候选词不会误提交。
- 删除使用独立确认弹窗，显示目标会话名称；删除其他会话不会清空当前报告、草稿或阅读位置。
- 请求失败保留弹窗及输入，提交期间防止重复操作。导航后迟到响应不会切换到错误会话；删除正在读取的会话后不会卡在加载状态。
- 移除原侧栏底部操作和报告上方横向改名栏；保留现有 Explore 主题、研究与文件池功能。
- 本轮菜单只接入现有重命名、删除能力，没有添加置顶、分享、归档等新后台功能。

## 修改文件

| 文件 | 作用 |
| --- | --- |
| `frontend/src/modules/explore/ExplorePanel.vue` | 每行菜单、独立弹窗、键盘与焦点管理 |
| `frontend/src/modules/explore/explore.css` | 菜单和弹窗样式、展开按钮可见性 |
| `frontend/src/modules/explore/useExplore.js` | 绑定操作目标、错误和重复提交处理、导航竞态保护 |
| `frontend/src/modules/explore/explore.test.mjs` | 新增 5 条会话操作与迟到响应测试 |
| `frontend/src/modules/explore/README.md` | 更新现有会话操作说明 |
| `tests/browser_explore.mjs` | 原有业务测试适配新的菜单及弹窗 |
| `tests/browser_explore_sessions.mjs` | 新增会话菜单专项浏览器验收 |
| `frontend/dist/index.html` 与引用资源 | 更新本地运行页面的构建产物 |
| `docs/Explore-会话菜单-验收记录.md` | 本验收记录 |

新闻、模型适配、后端接口、数据库结构均未修改。复用原会话 PATCH / DELETE 接口。

## 实际验证

在项目根目录 `/Users/jaylen/Desktop/成都八零/平台开发` 执行：

| 命令 / 验收 | 结果 |
| --- | --- |
| `npm --prefix frontend run check` | 成功，49 个模块构建 |
| `npm --prefix frontend test` | 75 / 75 通过 |
| `node tests/browser_explore.mjs` | 27 / 27 通过，零页面脚本错误 |
| `node tests/browser_explore_sessions.mjs` | 8 / 8 通过，零页面脚本错误 |

浏览器命令使用 `PLAYWRIGHT_MODULE_DIR=/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules`，先启动 `.venv/bin/python tests/browser_explore_fixture.py` 提供本地临时测试服务。业务结果输出目录通过 `EXPLORE_BROWSER_OUTPUT` 设置；会话专项通过 `EXPLORE_SESSIONS_OUTPUT` 设置。

浏览器测试使用临时账号、临时 SQLite 和明确的新闻 / 模型测试替身；其中一次 503 为故意注入的失败重试验证。本轮不声称进行了真实新闻或模型联调。

日志与结果：

- `data/explore-session-actions/build.log`
- `data/explore-session-actions/frontend-tests.log`
- `data/explore-session-actions/business-final/report.json`
- `data/explore-session-actions/sessions-final-verified/report.json`

专项检查包括非当前会话操作、报告滚动与草稿保留、自动选中标题、Enter 保存并刷新恢复、取消 / Esc / 外点、中文输入法、焦点约束、503 后重试、删除目标隔离、390px 屏幕布局。原业务回归包含三个模块、报告分支、文件池、取消重试、账号隔离和原有工作台流程。

最初专项测试把平滑滚动和样式提交首帧作为最终状态读取，产生测试时序失败；已改为即时设置测试滚动位置、等待展开按钮样式达到可见状态后断言，并完成通过的重跑。早期失败记录保留在其他测试子目录中。

## 页面和截图

现有服务地址：<http://127.0.0.1:8894/desktop/#explore>。刷新即可载入；本轮没有重启或修改该服务配置。

最终页面引用 `index-C4YHka3d.js` 与 `index-CWgzHYrR.css`。会话专项最后一轮覆盖该构建；业务回归后只增加了展开菜单按钮保持可见的样式修正，没有再次修改业务逻辑。

以下均为实际构建页面截图，报告内容是合成验收材料：

- `data/explore-session-actions/sessions-final-verified/01-other-session-menu-synthetic.png`
- `data/explore-session-actions/sessions-final-verified/02-rename-dialog-synthetic.png`
- `data/explore-session-actions/sessions-final-verified/03-delete-dialog-synthetic.png`
- `data/explore-session-actions/sessions-final-verified/04-mobile-session-menu-synthetic.png`
- `data/explore-session-actions/sessions-final-verified/05-mobile-rename-dialog-synthetic.png`
