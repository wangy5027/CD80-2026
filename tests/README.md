# 测试指南

更新：2026-10-14。**当前交付环境是 Mac 的 `/Users/jaylen/Desktop/26CD80_v1/最终版`**；本节命令均从该目录执行，使用本工程 `.venv`。下方保留的 Windows 命令、原平台阶段数量和旧浏览器夹具说明是来源项目历史资料，不代表它们已在本次 Mac 最终版重跑。实际结果请以整合日志（本地资料，未随源码发布）、[各模块接入记录](../docs/integration/README.md)和最终验收报告（本地资料，未随源码发布）为准。

## 当前 Mac 最终版

| 范围 | 从最终版根目录执行 | 已记录的结果与边界 |
| --- | --- | --- |
| 主站全量 | `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m unittest discover -s tests` | 后端日志（本地资料，未随源码发布）记录 1178 项、`OK`。最终全量已包含模型切换、策略匹配及固定解释分类等增量；此前 1172 项和 72 项专项只作各自阶段证据，不重复相加。 |
| 前端测试 | `npm --prefix frontend test` | 前端日志（本地资料，未随源码发布）记录 279 项通过。 |
| 前端生产构建 | `npm --prefix frontend run build` | 构建日志（本地资料，未随源码发布）记录 Vite 构建成功；命令会更新 `frontend/dist`，改源码后由集成主线执行。 |
| 金融数据服务 | `cd cd80-data-api && ../.venv/bin/python -m pytest tests -q` | 数据服务日志（本地资料，未随源码发布）记录 50 项通过；需安装 `cd80-data-api/requirements-dev.txt`。 |

上述为**各自运行的结果**，不相加成“一个总测试数”。真实模型、行情、新闻、WASI 和浏览器另有独立验收，参见[整合入口](../docs/integration/README.md)；测试替身通过不代表真实上游可用。浏览器验收使用 `data/integration-acceptance/` 下的隔离账号与业务库，不得指向正式 `DATA_DIR`。用户授权切到 GLM-5.3-Flash 后，平台聊天和新 App 解释均已真实通过。旧 GLM-5.3 超时、Skill 首次澄清和 Flash 一次解释格式失败/恢复仍保留，不能把多次验收写成一次无故障全过。

## 来源平台历史操作参考

以下各节原按 Windows 编写，保留原测试和夹具说明供追溯。含中文夹具当时需设置 `PYTHONUTF8=1` 或 `python -X utf8`；旧结果与本次 Mac 数字严格分开。

## 自动化检查

| 范围 | 命令 | 条件与边界 |
| --- | --- | --- |
| 主站 | .\.venv\Scripts\python.exe -m unittest discover -s tests -v | 全量会包含并行模块新增测试；依赖和真实 WASI 需就绪，不当作本次整理必跑项 |
| 前端 | npm --prefix frontend test | Node 内置测试运行器；目录中新增测试也会被发现 |
| 数据服务 | 在 cd80-data-api/ 执行 ..\.venv\Scripts\python.exe -m pytest tests -q | 需要 requirements-dev；错误工作目录会导致 app 导入失败 |
| 前端生产构建 | npm --prefix frontend run build | 改 src 后执行；会重写 dist，不用于本次纯文档验收 |

测试应使用临时账号、合成输入和临时数据库，不把正式比赛库作为写入目标。具体隔离行为以所运行脚本为准，不保证任意未来测试都无副作用。

## 统一对话创作与外部原件

2026-10-14最终修复后，`test_authoring_sessions`+`test_authoring_budget`33项通过（17.341秒）；`test_authoring_adapters`+`test_authoring_integration`+`test_asset_creator`+`test_developer_kit`30项通过（47.669秒）。两批覆盖63项，不称一次联合执行；此前16:38启动的全量584项为583通过、1跳过，未全部包含后续修复。前端152项、最终构建及8项隔离浏览器通过；真实GLM-5.3三类各完成合成双例及提交，17:03:34正式加载只读核验通过。范围与未覆盖项见本轮验收（本地资料，未随源码发布）。

从项目根执行`.\.venv\Scripts\python.exe -X utf8 -m unittest tests.test_authoring_sessions tests.test_authoring_budget tests.test_authoring_adapters tests.test_authoring_integration tests.test_portable_assets -v`。会话测试用可控适配器，预算测试走真实provider代码并mock HTTP；候选与集成用模型替身、临时账号/库及真实WASI，不调用真实模型。实际数量、初轮失败、修复与正式环境边界见本轮验收（本地资料，未随源码发布），不与历史全量相加。

真实模型另用`.\.venv\Scripts\python.exe -X utf8 scripts/verify_conversation_authoring_live.py --run --kind all`，`--kind`也可单选`app|tool|skill`。此入口读取环境变量或`.env`中的模型配置并消费正常模型额度，须按任务授权执行；只用合成整数、临时账号和临时`DATA_DIR`，不读正式历史/金融数据。每类按统一600秒/32请求/2修复运行双例并确认保存；报告逐次写入`data/conversation-authoring-live/`，失败报告同样保留。详见[开发指南](../docs/asset-authoring/开发与外部接入指南.md)，不能用替身用例替代该结果。

## 浏览器与真实服务

原应用浏览器验收：先运行 .\.venv\Scripts\python.exe tests/browser_fixture.py，再在另一个终端运行 node tests/browser_platform.mjs。默认 48993，需要 Edge、可解析的 Playwright 和真实 WASI，模型使用替身。PLAYWRIGHT_MODULE_DIR 可指定依赖位置。结束后关闭临时服务。

数据接入另有 financial_browser_fixture.py、account_financial_fixture.py 及对应浏览器脚本，运行前核对脚本端口和当前构建，不直接指向日常实例。验收入口见 [金融模块](../docs/financial-data/README.md)。

账号专项 `account_financial_fixture.py`、`browser_account_financial_data.mjs`、`desktop_data_page_visual.mjs` 均要求先设置 `ACCOUNT_TEST_EMAIL`，例如 PowerShell 执行 `$env:ACCOUNT_TEST_EMAIL = Read-Host "待核验账号邮箱"`，再运行对应脚本；夹具和浏览器脚本所在终端必须设置相同值。缺失或空白时立即报错，不默认选择真实账号。此项仅指定身份，原有正式库只读、临时库测试和身份一致性校验保持不变；报告发布前仍需脱敏账号信息。

真实模型和新闻网络验证见 [脚本说明](../scripts/README.md)。模型调用可能产生费用；模拟服务通过不等于真实上游通过。本次文档整理不启动这些脚本。

## 已有结果与新验收

2026-10-14最终集成：主站unittest全量361项中360项通过（281.071秒），1项在创建符号链接夹具时报WinError1314（未跳过）；前端71项、原24及新增6个浏览器场景通过；独立数据服务50项通过。最后完整主站已纳入绘图、开发包、绑定槽位及沉淀增量，详见增量验收（本地资料，未随源码发布）。真实GLM-5.3完整研究及首次失败见原集成验收（本地资料，未随源码发布）。Windows权限前提尚未满足，不能省略该测试后声称全通过。

旧 Windows 记录为189项中185通过；能力库实现报告的上一次全量为203项中199通过，仍有3项取消时限失败、1项符号链接权限错误；之后只重跑能力库专项。历史数据服务 44 项、前端 43 项通过；这些数字不是随代码自动更新的基线。最新可用记录从 项目状态（本地资料，未随源码发布） 查询。

每次结果按 [验收模板](../docs/验收记录模板.md) 记录环境、命令、代码快照和限制。文档验收检查新链接、路径、内容一致性及保护范围，不为低影响文档修改执行业务全量回归。

manual_inputs/ 中 CSV 是人工样例，第一份 amount 汇总 A=15/B=7，第二份 A=1/C=10；是否适合新增算法由测试者确认。原零散反馈已归档，状态见 [问题台账](../docs/已知问题.md)。

## 能力库专项

从项目根执行 `.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_capability_library.py -v`。当前源码有15个专项测试，涉及真实WASI、本机MCP及Windows DPAPI；本次文档同步未执行。

浏览器：先运行 `.\.venv\Scripts\python.exe tests/capability_browser_fixture.py`，再在另一终端运行 `node tests/browser_capability_library.mjs`。夹具固定48995、临时库、模型替身和真实WASI；写入data/capability-browser，不能在纯文档核验时运行。历史结果及边界见 能力库验收（本地资料，未随源码发布）。

## 主对话子 agent

2026-10-14模型日志/格式纠错专项：`python -X utf8 -m unittest tests.test_sub_agent_model_records tests.test_sub_agent_protocol tests.test_sub_agent_diagnostics tests.test_sub_agent_evidence -v`，35项通过；与原子任务/规划器/传输回归合并83项互不重复测试通过。前端78项、`tests/browser_sub_agent.mjs`隔离浏览器18场景通过，未调用真实模型。最新验收（本地资料，未随源码发布）包含本轮维护证据；下文历史数量仅代表对应阶段。

愿景交付的隔离浏览器使用`tests/browser_evolution_fixture.py`与`tests/browser_evolution.mjs`，默认24场景；设置`EVOLUTION_BROWSER_RECOVERY_ONLY=1`运行6项复制/编辑恢复、独立槽位与沉淀步骤回归。`tests/browser_publication_warnings.mjs`使用相同夹具，单独验证应用发布提示及确认状态。各模式分别启动空白临时实例，不能指向正式服务。最新报告及71项前端测试范围见[前端交付说明](../docs/vision-delivery/前端交付说明.md)。

真实模型验收单独运行并使用普通模型额度：`scripts/verify_vision_reuse.py --live-analysis`验证两轮取数/计算/研究复用，`--live-edge-cases`验证真实空数据与缺指定工具；`scripts/verify_vision_application.py --live`验证固定应用生成、执行、保存和自然语言改写逻辑。均使用临时账号和库，详细预算与输入见[脚本说明](../scripts/README.md)，不能把脚本存在当作验证已经通过。

项目根目录执行：

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_sub_agent -v
```

20项测试使用临时DATA_DIR、合成5行数据和模型替身，但执行真实能力准入与WASI；必须先按既有指南准备隔离运行时，不自动下载依赖或回退宿主。详细 [TC-SA-001](../docs/sub-agent/测试用例/TC-SA-001-金融分析子任务端到端.md) 及 验收记录（本地资料，未随源码发布）。

浏览器测试先按前端指南构建dist；在独立终端启动临时服务，另一个终端运行脚本（需要本机Edge和Playwright）：

```powershell
.\.venv\Scripts\python.exe tests/browser_sub_agent_fixture.py
# 另一个终端；如Playwright不在模块路径，将PLAYWRIGHT_MODULE_DIR设为其已安装包目录
node tests/browser_sub_agent.mjs
```

服务固定127.0.0.1:49002，不读生产.env；专用测试控制端点仅在此夹具注册。脚本完成后Ctrl+C关闭该夹具，清理临时DATA_DIR。截图、JSON报告、事件和下载文件保留在忽略目录data/sub-agent-conversation-browser/。不得把该夹具作为正式服务使用。


真实模型路由回归需明确启用（会使用项目配置的模型服务及正常API额度）：

```powershell
.\.venv\Scripts\python.exe scripts/verify_sub_agent_routing.py --live
```

仅发送六条测试文本与合成能力元数据，不执行金融Tool、不改正式数据库。结果为data/sub-agent-routing-live.json；真实模型路由通过不代表真实数据分析端到端通过。

最新专项结果见2026-10-14验收（本地资料，未随源码发布）：子任务20项、前端54项、构建和浏览器回归通过。浏览器脚本另覆盖旧后端仅保存失败回复、无用户消息/无步骤的快照，检查失败原因可见及消息顺序；不会修改正式历史记录。10月13日真实模型6项路由证据保留在原验收（本地资料，未随源码发布），未当作本轮重新执行。


## 统一可复用资产

`test_asset_apps`、`test_asset_creator`、`test_asset_creator_sources`、`test_asset_apps_recovery_audit`、`test_asset_apps_skill_integration`、`test_asset_skill_runtime`、`test_asset_components`、`test_asset_legacy` 与 `test_validation_suite_helpers` 覆盖完整实现保存、当前权限、版本、双验证、固定/动态执行、真实文件交付、旧升级及脚本兼容。运行时使用项目虚拟环境和既有WASI；模型与外部连接默认替身。`browser_asset_apps_fixture.py`/`browser_asset_apps.mjs` 为一次性账号浏览器验收，不得指向正式端口；范围、命令及截图见前端浏览器记录（本地资料，未随源码发布）。最终后端489项中488通过、1项Windows权限跳过，前端103通过；证据与未覆盖项统一见模块进度（本地资料，未随源码发布）。
