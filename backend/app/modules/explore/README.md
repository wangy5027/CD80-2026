# Explore 后端

Explore 以现有登录账号和会话为边界保存独立研究运行。`routes.py` 负责鉴权，`repository.py` 在 `DATA_DIR/explore.sqlite3` 保存会话、运行、阶段事件、材料、报告、文件选择和证据版本；`service.py` 管理研究生命周期。研究输入只来自本轮问题、用户明确选择的同会话文件快照及本轮工具结果，不读取普通聊天历史。本轮没有改动前端 UI。

## 当前研究流程

1. 创建运行时冻结账号、会话、分支、时间口径、原始问题和文件选择。`research_task.py` 保留确认后的完整目标、URL/DOI/章节锚点和 `Q1…Qn` 子问题；查询词只是有界检索线索，不替代原始请求。新闻标题后的多个问题不会在首个问号处截断；材料相关性不再用中文滑动二字片段判定。泛新闻问题可以获取候选快讯，具体事件则须同时匹配实体和主题；明确 URL 或论文编号优先。
2. 非模糊任务进入 `research_runtime.py`。它通过 `SkillRegistry` 自动加载所选模块的 `skills/explore-event|explore-relation|explore-history/SKILL.md`，记录 Skill 内容哈希及工作流/提示版本。相关数据工具 Skill 只在本轮获准且模型请求时按需加载，不因文件存在就获得执行权限。
3. 研究循环使用现有 `GiteeProvider.events` 和 `ToolRegistry`。模型提出函数调用，服务端按模块白名单、参数 schema、可用性、次数、时间与取消状态校验，执行已注册 handler，再用对应 `tool_call_id` 将真实结果或受控错误回传。每轮保存查询、工具参数、结果状态、耗时和材料引用；持久轨迹只保存结果元数据与哈希，正文通过受权限控制的证据库续读；根据子问题缺口继续查找、读取或停止。`web_search` 和相关 SFC 工具来自工作台注册表，不开放 shell、任意代码、SQL 或普通聊天图。生产流程不再固定“搜索一次＋两个新闻窗口＋直接生成报告”；该流程只供旧测试替身兼容。
4. `fetch_public_page` 首先尝试读取用户明确给出的 URL，也可以读取服务端按本轮实体批准的精确公开入口、本轮公开搜索结果或已取得页面中的 URL。它保留 fragment 的章节意图，区分成功、仅元数据、受限、未找到、超时等状态，归档实际可得正文及定位信息；标题或摘要不能冒充全文。`read_evidence` 按本轮不透明 `evidence_id` 和受控范围续读已归档正文或创建运行时冻结的所选文件，并返回哈希、位置、是否还有内容及下一段起点。旧 AI 报告只作背景，不能当作独立外部证据。PDF 原文提取和 OCR 尚未纳入本轮工具。
5. 研究阶段冻结 `coverage`、`claims`、`conflicts` 与真实证据片段。每个主张保存类型、逐字支持、位置和支持状态；可追溯校验不保证语义或外部事实正确。历史研究以事件日期、资料可得时间和证据用途判断案例资格，允许今天读新发表的旧事回顾，不把文章发表早于当前 90 天作为新运行时的资格规则。`contracts.historical_source_ids` 的旧 90 天 helper 仅保留旧测试/旧流程兼容。
6. 写作前增加一次有界主张语义审阅：只能批准、收窄或拒绝已有可追溯主张，选择原支持片段，不能新增证据、提升断言类型或抹掉未回答状态；再次以当前授权正文校验引用。Writer 只能使用审核后的冻结账本和准许的来源编号。服务端校验结构及引用，复核稿件是否超出证据或误述实际检索动作；发现具体问题时最多修正一次，再复核一次。`answer_status` 为 `answered`、`partial` 或 `unanswered`，与执行状态及方法说明分别保存。有方法限制但已回答的报告可以是 `succeeded`；没有任何有效非假设主张支持的子问题必须为 `unanswered`。核心问题未回答时生成短版 `insufficient_evidence` 报告，不填充虚假的事件/关系/历史结论。报告、来源文件和状态按原有事务保存。

研究中的工具轮次按主张 ID 增量合并账本，最终无工具轮次才可整体替换；本轮唯一、实际读取过的 `S#` 可解析为不透明证据 ID，但不放宽逐字引用要求。`claim_reviews` 保存审阅决定，`research_validation_notes` 单独保留研究阶段被丢弃引用的诊断，不能把旧诊断误当作当前有效引用的错误。当前写作流程版本为模块 `@6`、主张审阅 `claim-review@1`。模型审阅仍可能漏掉语义问题，不能当作独立事实核验；模型改写引文或使用未知 ID 时仍会降为无有效支持。

## 权限、预算与生命周期

公开网页读取只接受 HTTP(S) 默认端口；拒绝 URL 凭据、敏感查询参数、本机/内网/云元数据地址，逐次验证 DNS 与跳转目标，并连接已核验的公网地址。下载、解压后正文、重定向、解析及总时间都有上限；仅导航与页脚的壳页标为 `metadata_only`，不宣称取得正文；不转发用户 Cookie、Authorization 或自定义 header，不绕过登录/付费。网页正文是外部不可信材料，不能修改工具权限、Skill 或提示；本轮 URL 白名单阻止把私有选中文件内容拼到新 URL 外传。`EXPLORE_PUBLIC_DNS_MODE=system` 为默认；`cloudflare_doh` 仅在显式配置时启用。生产环境网络策略及可访问性仍须单独实测。

`EXPLORE_PUBLIC_ENTRY_CATALOG` 是服务端显式批准的入口目录，默认 `[]`，格式为 `[{"keywords":["机构名称"],"urls":["https://official.example/"]}]`。按本轮问题匹配实体，最多 16 个精确入口冻结进 `run.authorized_public_urls` 并提供给模型，模型按需要读取；不会预先抓取所有入口。只允许无路径参数、查询串或 fragment 的网站首页，随后可以沿真实返回的公开链接继续读取。浏览器请求不能自行提交这个权限字段，普通正文、私人选中文件和旧 AI 报告也不能扩大授权。西财案例的本站配置显式批准学校官网、新闻网、教育部和金融监管总局的 HTTP/HTTPS 入口；其他研究问题不会继承这组入口。研究提示版本为 `research-loop@2`。

合格的本轮公开搜索结果和公开网页读取器实际返回的 final/canonical URL，可额外授权同协议、同主机的 `/` 首页，便于返回官网查找栏目；不开放任意路径、查询串或兄弟主机。问题在澄清、确认或补充判断时改变，入口清单随新的执行版本原子更新，避免换主题后继承旧入口。

研究预算含模型轮数、工具总次数、单工具次数、结果和上下文长度、运行超时，以及为写作/复核预留的时间。`EXPLORE_RUN_TIMEOUT_SECONDS` 默认 600 秒，其中最多 200 秒（总预算三分之一）留给写作与复核；在模型轮次上限或最后 60 秒研究窗口内停止新增工具轮次并整理证据。若最后整理请求因预算截短而超时，保留已经验证的主张和材料；尚无有效主张时明确记录“整理超时、尚未形成答案”，不会伪装成分析完成。`EXPLORE_RESEARCH_ROUNDS`、`EXPLORE_TOOL_CALLS` 可配置但受服务端上限约束；重复动作和无效参数也计入预算。工具调用目前串行，沿用单个可中止上游响应槽位。取消、超时、删除或 `execution_id` 改变后，迟到的模型/工具结果不能写回；删除后的旧证据 ID 也不能从缓存恢复。若已冻结选择的文件被移除（包括只作背景的旧 AI 报告），研究在下一轮调用或写作前中止，避免其模型复述继续进入上下文。重启时旧活动运行转为 `interrupted`，不会自动重放；重试创建新运行，不覆盖旧报告。

已有 `awaiting_clarification`、`awaiting_review`、`awaiting_input` 状态与旧确认入口继续兼容。创建、澄清及确认使用幂等键和 review 版本；`auto` 上下文只取所选报告分支及必要祖先，`manual` 只取用户明确选定的同会话文件，最多八份。文件移除是墓碑处理；报告和运行快照保留历史记录，但新读取仍检查文件是否存在且仍被本轮授权。读取报告、文件、事件不触发模型或工具。

API 前缀仍为 `/desktop/api/explore`，主要包括会话、运行、文件池/上下文偏好、报告/来源/Markdown、澄清、确认、取消和重试。运行阶段、材料、`research_budget`、`skills_used`、`retrieval.tool_trace` 和报告覆盖情况经现有读取接口返回。旧 token-only Provider 替身只在 `TESTING` 下走兼容流程；生产要求支持 `events` 的 Provider，不能把旧测试通过当成工具续接验证。

`model_transport_trace` 记录每次模型请求的阶段、请求大小、模型/工具名、响应头与首事件耗时和错误类别，区分连接超时、等待响应头超时和输出流中断。轨迹不含密钥、请求正文或隐藏推理；回调异常不会改变 Provider 行为。Explore 使用局部 Provider 副本，保留现有 TLS、系统代理、总时间和取消规则。研究与写作共用整轮最多一次瞬态传输重试，只有当前阶段剩余至少 30 秒才可尝试；重试可能产生额外模型费用，但不延长截止时间或增加工具预算。取消、鉴权失败、资料删除、格式错误不重试；失败流中的片段和工具提案丢弃，重试前复核授权。`model_retries` 及研究轨迹的 `attempt` 可核对实际发生次数。该轨迹能定位客户端等待阶段，不能独自判断服务商内部队列或代理故障。

主张语义审阅成功后，`report_checkpoint.py` 从获批且逐字可追溯的主张生成确定性阶段性简报，不读取未复核的模型草稿。之后发生传输超时、总时间用尽或稿件校验失败，可保存此简报为 `partial / review_timeout_checkpoint`；正文明确说明最后稿件核对未完成，不能当作全流程完成。该路径仍检查运行状态、执行版本、所选文件与证据授权，并在保存事务内再次核对删除标记。取消、删除、版本改变优先；迟到结果无法覆盖已保存记录。若尚无获批结论，不使用这一机制伪造答案，原有材料与运行轨迹仍保留。

## 验证与运行

在完整仓库根目录运行替身和既有生命周期测试：

```bash
.venv/bin/python -m unittest tests.test_explore tests.test_explore_pool tests.test_explore_evidence tests.test_explore_public_reader tests.test_explore_research_task tests.test_explore_research_runtime tests.test_explore_historical_eligibility -v
```

旧 11 份报告的离线任务解析、URL、子问题和相关性回归使用原始交接文件夹；输出是诊断 JSON，不是重新生成的报告：

```bash
.venv/bin/python scripts/validate_explore_regressions.py --old-package '/Users/jaylen/Documents/ChatGPT/成都八零/outputs/Explore报告与改造交接_20261013' --output /tmp/explore-11-offline.json
```

真实模型/公开资料验证需由操作者在已配置服务端密钥与合法网络访问的环境显式运行。脚本使用临时隔离数据库，默认仅运行旧 R06、R10 和新问题 `new_pep`，将新报告、运行记录、真实工具轨迹和汇总保存到指定输出目录；它不会自动重跑旧 11 份报告，也不会修改现有账号数据：

```bash
.venv/bin/python scripts/validate_explore_research_live.py --old-package '/Users/jaylen/Documents/ChatGPT/成都八零/outputs/Explore报告与改造交接_20261013' --output /tmp/explore-research-live --cases R06,R10,new_pep
```

指定已有后续问题的真实复测可用 `scripts/validate_explore_followup_live.py --run-id <运行ID> --output <目录>`。它只复制该分支必要祖先和关联研究文件到临时数据库，核对所选文件与原运行一致，不读取普通聊天、复制账号凭据或改动原报告。

测试是否完成、实际模型/资料访问结果及新旧报告对照以交接输出中的原始记录为准，不根据脚本存在推断已验证。源码修改不会自动更新后台进程；每次改动后按项目部署流程重启已有服务，并检查 8894、8897 两个服务健康状态。实际重启时间、进程与验证结果单独记录在本次交接输出中。
