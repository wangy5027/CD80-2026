# Explore 流光玻璃 V2 实施与验收

日期：2026-10-13。范围仅限现有 Explore 页面，保留账号、研究会话、新闻检索、模型适配、文件池、分支、取消重试与导出数据流。

## 实施结果

- 输入区拆为稳定的 `ExploreComposer.vue`：冰蓝紫玻璃外壳、独立边缘光、聚焦反馈、按按钮真实宽度测量的模块滑块。自动/自选文件设置移入参考材料抽屉，取消多层横条。草稿不以动画为由重新挂载；研究中可编辑下一条草稿，提交仍遵守原有并发约束。
- 报告页首合并模块、结果状态、标题与信息截止时间；原始问题、确认问题、创建时间进入研究详情，澄清改变有可见提示。核心发现、重要不确定性和缺口提前。部分完成与已保存分别表述。
- 报告和输入槽使用同一阅读列；右侧文件与来源抽屉覆盖展示，打开时不单独缩窄报告。引用打开对应材料并短暂定位高亮，关闭恢复焦点。
- 顶栏标题原位编辑；会话行的“⋯”菜单继续使用独立重命名/删除确认窗口，没有新增全宽表单。
- 本页观察到运行完成才触发报告落位；刷新旧报告不补播成功动画。同一记录轮询不改变阅读位置，切回看过的报告恢复本页记录的位置。后台完成只提示，不强行切页。
- 默认开启动态效果，可手动关闭；系统减少动效和页面隐藏时暂停装饰。报告页首离开视口暂停其边缘光。静态模式仍保留配色、材质和功能。
- 极矮视口会收紧输入区高度、顶栏折叠重复标题，保留发送、参考材料、模块、返回和历史入口；正在编辑的标题不被隐藏。使用实际可见视口处理软键盘缩小布局的路径，草稿和光标仍在原输入节点。

任务书 M17 的“推荐仅填入”与用户此前明确要求相冲突，继续保留 **点击推荐直接提交并创建新分支**。动画不决定业务成功状态，没有假百分比、假打字或假材料。

## 动效对应

| 清单 | 本次实现 |
| --- | --- |
| M01 环境流光 | 复用常驻环境组件；阅读态降低白色遮罩，系统/手动/不可见时暂停 |
| M02–M04 输入外壳、聚焦、展开 | 15 秒装饰光带、220ms 聚焦反馈；高度按内容与视口即时测量，不逐字播放尺寸动画 |
| M05–M06 模块与发送 | 200ms 实测滑块；按钮短反馈，请求立即发出 |
| M07–M08 上下文与材料标签 | 来源标签与实际材料数量变化触发约 190ms 过渡 |
| M09–M10 阶段与事件 | 真实阶段软脉冲；最近三条服务端阶段事件进入，完整过程可展开 |
| M11–M12 报告落位与页首光 | 新报告 300ms/8px 一次落位；页首装饰 19 秒低幅移动，离开视口暂停 |
| M13–M14 来源抽屉与定位 | 260ms 侧向进入；具体来源单次 850ms 高亮 |
| M15–M16 历史与分支 | 新节点 220ms 进入；历史栏 240ms 展开/收起，列布局只在开关时改变 |
| M17 推荐 | 悬停/键盘聚焦强调，依照用户直接指令保留点击执行 |
| M18 保存文件反馈 | 服务端文件计数变化后更新徽标和新增文件提示 |
| M19 报告切换 | 主动切换的局部淡入与阅读位置恢复；不重播整篇报告 |
| M20 标题编辑 | 同一顶栏区域内短过渡，保存/取消不另占一行 |

## 本轮修改文件

不把仓库此前已有的未提交改动计入本轮交付。

| 文件 | 责任 |
| --- | --- |
| `frontend/src/modules/explore/ExploreComposer.vue` | 新增独立玻璃输入组件与模块滑块 |
| `frontend/src/modules/explore/ExplorePanel.vue` | 局部交互状态、报告结构、抽屉、标题、阅读位置和阶段反馈 |
| `frontend/src/modules/explore/explore.css` | Explore 专属材质、布局、动效与响应式样式 |
| `frontend/src/modules/explore/ExploreAmbientBackground.vue` | 阅读态环境光强度与暂停接入 |
| `frontend/src/modules/explore/useExploreMotion.js` | 用户偏好、减少动效、页面可见性与清理 |
| `frontend/src/modules/explore/motion.test.mjs` | 动效偏好与生命周期验证 |
| `frontend/src/modules/explore/README.md` | 实际交互与边界说明 |
| `tests/browser_explore_glass.mjs` | V2 操作、截图、录屏及 CDP 性能记录 |
| `tests/browser_explore.mjs`、`tests/browser_explore_visual.mjs` | 保留原验证，适配抽屉内文件设置与阅读恢复行为 |
| `frontend/dist/index.html`、`frontend/dist/assets/*` | 重新构建的页面产物 |
| `docs/Explore-流光玻璃-V2-验收.md` | 本次验收记录 |

没有修改本轮后端业务，也没有引入新的动画库、框架、新闻供应商或付费服务。

现有调用仍由 `backend/app/modules/explore/service.py` 组织，复用工作台 `web_search` 工具、`backend/app/modules/news/service.py` 的 `NewsService.get_news`，以及 `backend/app/modules/workbench/graph_runtime.py` 的 `GiteeProvider` 模型传输。持久化继续由 Explore `repository.py` 管理。

## 访问与复现

本地入口：<http://127.0.0.1:8894/desktop/#explore>。已运行的网站刷新即可加载新构建。

```sh
cd /Users/jaylen/Desktop/成都八零/平台开发
zsh ./启动LangGraph网站.command
```

构建、单元测试：

```sh
npm --prefix frontend run check
npm --prefix frontend test
.venv/bin/python -m unittest tests.test_explore tests.test_explore_pool -v
```

浏览器验收使用 `tests/browser_explore_fixture.py` 的隔离临时账号与数据库。新闻、模型输出和长标题材料均为明确测试替身，页面、请求、状态、保存及文件操作运行真实应用代码；这不等于新闻或模型真实接口联调通过。

先在一个终端启动测试实例，在另一个终端依次执行浏览器套件（不是对 8894 真实账号执行）：

```sh
.venv/bin/python tests/browser_explore_fixture.py --port 48994
```

```sh
export PLAYWRIGHT_MODULE_DIR=/Users/jaylen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules
node tests/browser_explore_glass.mjs
node tests/browser_explore.mjs
node tests/browser_explore_sessions.mjs
node tests/browser_explore_visual.mjs
```

## 已核验记录

- 构建：51 个模块成功构建，日志 `data/explore-glass-v2/build.log`。
- 前端单元测试：77/77，通过日志 `data/explore-glass-v2/frontend-tests.log`。
- Explore 后端与文件池测试：44/44，通过日志 `data/explore-glass-v2/backend-tests.log`。覆盖账号/来源权限、幂等、取消与迟到写入、模块/分支及文件上下文等既有约束。
- 会话菜单浏览器回归：8/8，0 页面脚本错误；覆盖非当前会话菜单不切换报告或草稿、重命名全选/回车/中文组合输入保护、模拟请求失败后重试、删除目标隔离和手机菜单。报告 `data/explore-glass-v2/session-regression/report.json`，日志 `run.log`。
- Explore 业务浏览器回归：27/27，0 页面脚本错误，`CidoOV0g` 构建通过。覆盖三个模块直接运行、三种澄清选项和自定义需求、旧报告分支/上下文隔离、跨模块文件池、文件重命名/移除及快照保留、刷新不调用模型、请求幂等、下载、取消/迟到结果、失败重试/重新生成、账号隔离、安全 Markdown/URL，以及普通聊天、Build、Evolve 返回与导航。报告 `data/explore-glass-v2/business-final-verified/report.json`。其后中断的重复运行不计入通过记录。
- V2 动态专项：15/15，0 页面脚本错误，采集构建 `index-CidoOV0g.js` / `index-8PstlbTL.css`。覆盖边缘光实际变化、聚焦、实测滑块、鼠标/键盘/组合输入事件保护、运行中保留草稿、实际保存材料的提示、报告落位、抽屉/引用/恢复焦点、新分支、刷新不重播、标题原位编辑、长标题/长报告、小屏、关闭动效和系统减少动效。结果 `data/explore-glass-v2/final-verified/result.json`。随后仅补齐跨会话加载完成后恢复阅读位置的时序；当前本地构建为 `index-JoSvofBM.js` / `index-8PstlbTL.css`，样式相同，相关导航由旧视觉回归复验。
- 既有视觉浏览器回归：11/11，0 页面脚本错误，最终 `JoSvofBM` 构建通过，报告 `data/explore-glass-v2/visual-jos-second/result.json`。保留原有动态背景、隐藏标签/减少动效、运行中输入与材料、手机抽屉、长草稿和底部可达性检查；更新因新布局而改变的控件位置，并补查阅读恢复：跨会话及同会话返回旧报告均恢复 `scrollTop=3240`，新记录从 0 开始，同记录轮询不抢滚动。没有删除业务或安全断言来获得通过。
- Safari 27.2：本机安装可识别，但 Safari Driver 创建空白测试会话返回 `session not created`，提示未启用远程自动化。因此真实 Safari 操作与性能 **未测**；没有改变系统设置，没有将 Chrome 或 WebKit 结果冒充 Safari。

测试宿主为 macOS 27.2、Apple M5 Pro、Google Chrome 154.0.8037.99、Node v25.9.0；自动化使用 headless Chrome，不能由这台机器的记录推断所有设备的体验。

### 布局与操作实测

- 1440×900、1640×900：报告与输入区同宽 980px，中心线误差 0；有来源标签的阅读态输入区高 159px，核心发现从视口 y≈257px 开始。打开文件/来源抽屉不改变该轴线。
- 长标题测试保留 122 个字符，来源标题 104 个字符；全文仍可阅读，没有以短假标题代替溢出检查。
- 390×844 与 720×450：无横向溢出，滑块、来源与参考材料保留。720×450 用于模拟 200% 缩放下可用布局空间，**不是原生浏览器 200% 缩放实测**。
- 390×300 极矮视口：780 字草稿与来源标签同时存在，输入区内部可滚动，发送按钮底部 y=280px、整体输入区底部 y=291px；报告仍有 34px 可滚动空间。文件抽屉为 390×300，不横向溢出。该边界首次测试发现超出底部的问题，修复后重新通过。
- 组合输入使用浏览器合成事件验证误发送保护，不冒充真实中文输入法候选窗或手机软键盘实测。

## 实际截图与操作录屏

以下均为本轮真实应用页面，使用隔离测试材料；不是设计稿，也不是线上新闻/模型烟测。主体操作录屏及 V2 截图在跨会话恢复时序补丁前采集；补丁不改变材质与动画样式，构建对应关系见上方测试记录。

| 场景 | 文件 |
| --- | --- |
| 入口 | [入口玻璃面板](../data/explore-glass-v2/final-verified/02-entry-after-five-seconds.png) |
| 研究中 | [阶段与已保存材料](../data/explore-glass-v2/final-verified/05-real-fixture-research-stage.png) |
| 报告阅读 | [核心发现与紧凑输入](../data/explore-glass-v2/final-verified/06-report-arrival.png) |
| 输入聚焦 | [聚焦与高光](../data/explore-glass-v2/final-verified/03-input-focused.png) |
| 来源抽屉 | [来源定位高亮](../data/explore-glass-v2/final-verified/09-citation-focused-drawer.png) |
| 小屏 | [390px 长标题](../data/explore-glass-v2/final-verified/390-responsive-report.png) |
| 极矮屏补充 | [390×300 长草稿](../data/explore-glass-v2/final-verified/16-mobile-short-long-draft.png) |
| 标题原位编辑 | [顶栏编辑](../data/explore-glass-v2/final-verified/13-inline-title-edit.png) |

[27.12 秒实际操作录屏（MP4）](../data/explore-glass-v2/final-verified/explore-glass-motion-synthetic.mp4)；[WebM 原文件](../data/explore-glass-v2/final-verified/explore-glass-motion-synthetic.webm)。录屏包含入口与已有报告静置、输入聚焦、模块滑块、材料选择、来源抽屉、新分支、服务端测试运行变化、保存和关闭动态效果。录屏编码帧率不作为应用渲染帧率。

## 性能对照

在独占的 Chrome 测试阶段，使用同一份 23,750 字合成报告、1440×900 CSS 视口、DPR 1、同样从顶部滚动 5,000px 的路径，各采集约 5.216 秒 CDP Performance 记录。没有同时运行另一套浏览器性能测试。

| 观测 | 动效开启 | 动效关闭 |
| --- | ---: | ---: |
| rAF 回调间隔中位数 / P95 | 16.7 / 16.7ms | 16.7 / 16.7ms |
| 观察到的长任务 | 0 | 0 |
| CDP TaskDuration 增量 | 0.515s | 0.304s |
| CDP LayoutCount 增量 | 0 | 0 |
| Trace Paint 事件数 | 18 | 21 |

这次短时样本未观察到长任务或滚动中的布局重排；开启装饰增加了主线程任务时间。rAF 是回调节奏，不是实际呈现帧率；事件数也不是跨设备性能评分。没有据此宣称全设备 60fps、Safari 流畅或长期稳定。

原始记录：[开启动效 Trace](../data/explore-glass-v2/final-verified/chrome-motion-on.trace.json)、[关闭动效 Trace](../data/explore-glass-v2/final-verified/chrome-motion-off.trace.json)、[完整结果 JSON](../data/explore-glass-v2/final-verified/result.json)。可在 Chrome Performance 面板导入 Trace 复查。

验收结束后已停止本轮启动的 48994 临时测试实例，临时账号和数据库随实例清理。8894 正常网站保持运行并返回 HTTP 200，已提供最新构建。

## 范围边界

这次是已有 Explore 的布局、材质与交互改造，不代表两份原始产品文档全部实现。不新增研究质量评分、行情回测、严格历史时点验证、多智能体框架或长期用户画像；不以界面动效证明报告内容正确。

未在本轮重新请求真实新闻或付费模型；相关真实能力沿用现有服务。移动端真实软键盘、触屏设备与未测试浏览器不能由桌面视口模拟推断为已验证。
