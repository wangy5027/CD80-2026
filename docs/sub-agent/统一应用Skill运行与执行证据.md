# 统一应用 Skill 运行与执行证据

状态：2026-10-14，实施契约；实现和验证状态另见开发进度。本契约落实[统一应用目标](../asset-authoring/技术规范.md)，保留现有对话队列与历史记录。

## 内部调用

`SubAgents.run_skill(owner, snapshot, task, inputs, delivery_requirements, *, parent_run_id, node_id, stopped=None, files=None, invoke_tool=None, request_id=None, answer=None, bindings=None)` 是服务端 Python 接口，不开放客户端注入源码/字节/回调的 HTTP 参数。

snapshot 包含 name、entry、files 和 tools，平台可附 constraints、allowTemporaryCode；files 是路径到文本/字节的映射，也接受 `{path,text|base64}` 列表。标准 Skill 正文本身无需这些平台字段。tools 是已经按 App 快照解析的 `{name,description,inputSchema}` 列表；工具调用通过受信回调 `invoke_tool(name, arguments, files, stopped)` 返回 `{result,files}`，回调负责冻结实现、连接授权及自身预算，不能自动改用个人库最新版本。资源和脚本仅限该快照内安全路径，脚本仍在现有 WASI 中执行。

正式 App 可直接传 capability_library/components.py 的完整组件快照；内部使用 skill_material 展开标准内容，按依赖顺序提供 tool_1 等安全别名，调用 invoke_snapshot/invoke_skill_script。支持其 `{invocationId,status,result:{content,...},files}` 返回，文件交接必须是实际字节。bindings 为本账号连接绑定，独立于可移植快照，调用时重新准入。

正式完整快照另存 records(kind=skill_snapshot,id=runId)，run仅保存componentSnapshotId/componentSnapshotDigest；内部Store.skill_snapshot(owner,runId)返回核对所属与摘要后的完整原件，删除个人库来源后仍可恢复。旧记录没有原件则明确缺口，不以当前新版补历史。

每次调用建立独立子任务 run 与 skillCallId，持久保存 parentRunId/nodeId；不创建工作台会话、不占用原会话消息队列、不生成重复对话答复。复用现有 act/approve/review 模型阶段、有限纠错、实际工具与 WASI 执行。用户问题返回 awaiting_user；同一 request_id 携带专门回答继续，已完成调用同内容重放，异内容冲突，活动调用重复请求不得重复执行。服务重启标中断，不自动重放。

inputs 为非凭据结构化输入，files 为调用方完成授权的当前输入字节。组件 permissions.inputFiles=false 时拒绝传入文件；outputFiles=false 时拒绝工具、脚本及临时代码生成文件和最终文件交付，不能通过临时代码绕过。纯结构化返回不作为文件权限。初始输入落为该 Skill 调用的工件。Skill 只看到快照工具和资源、授权工件、本节点任务与约束；不提供其他 App 节点或个人库自动发现权限。

## 交付与结果

delivery_requirements 使用 `{schema,files:[{name,mediaType?}],evidenceFields:[字段路径]}`，兼容 outputSchema、requiredFiles，并支持 requiredExtensions、requiredTools、requireEvidence。finish 可用 resultStepId 指向成功动作的实际结构化结果，也可用 resultArtifactId 指向本调用完整 JSON 工件；结构化结果不能由 finish 随意填值替代执行结果。没有结构化要求时可交付实际模型 answer。平台先检查实际字段/文件/能力使用，再由现有 review 验收；不满足条件消耗原三次纠错预算，耗尽停止。

平台自动持久化的结果 JSON 和实际源码分别标记 `artifactRole=execution_result/executed_source`，与用户输出文件 `output` 分开。outputFiles=false 不禁止内部证据：若 finish 显式选择唯一一个实际结果 JSON，可作为 resultArtifactId；内部证据引用不导出为用户文件。多结果歧义不能自动推断，真实生成或交付文件仍需文件权限。模型提示明确结构化完成格式及此边界。

文件媒体类型由平台稳定内建映射生成，CSV恒为text/csv、JSON为application/json、PNG为image/png，不采纳Windows应用关联或宿主全局mimetypes覆写；声明类型匹配与实际字节验证分别执行，不能通过错误的宿主类型判断制造交付缺项。

返回 `{skillCallId,revision,status,result,files,artifacts,steps,question?,error?}`；files 是交付文件字节，只用于服务端与 App 层交接，不直接 JSON 序列化。awaiting_user 的 question 含 id、prompt、revision；继续时所有原参数与 request_id 保持不变，仅传 `answer={questionId,revision,answer}`，回答追加到本 Skill 的 observations，不修改父 App 输入摘要。主站可沿用 owner 隔离的子任务观察和工件下载。取消事件传递给模型和沙箱；迟到输出不接受，错误与部分产物保留。

## 权威动作证据

新增 records(kind=execution_evidence)，以每个真实 stepId 定位，包含 run/parentRun/node/skillCall、完整非凭据参数、输入引用、实际源码、调用ID及输出引用。展示字段仍保持裁剪；新证据不从摘要恢复。凭据脱敏造成改变时明确 `reconstructable=false` 与原因，不把脱敏占位符当可执行输入。记录独立有界，超限在执行前报错，不能静默截断。失败代码保留提交原件，成功代码另记录实际嵌入辅助模块的完整执行源码。

父动作的真实Skill事件保存为execution_evidence.skillCallIds，去重关联实际子调用；事件payload保留skillCallId。与父run自身skillCallId字段区分，不能根据提示里“加载Skill”字样伪造调用关系。

普通主对话同样记录这些证据；旧任务不回填。能力调用事件写入当前 stepId 的关联，形成可检查的调用链。新记录沿用 sub-agent.sqlite3 的逻辑表，不新增物理库；备份包含主库及正确处理 WAL，回退保留新增记录，不影响旧字段读取。

Python读取使用 `SubAgents.execution_evidence` 或 `Store.execution_evidence(owner,run_id,step_id)`，HTTP只读入口为 `GET /desktop/api/sub-agent-runs/{runId}/execution-evidence/{stepId}`，同owner及确切run校验。最终验收成功后按真实交付工件、来源引用及显式结构化输出来源计算 adopted；无显式依赖的纯文本交付标记 adopted=null，不猜测全部成功步骤均被采用。

## 验证范围

隔离测试需覆盖真实 WASI 多步骤 Skill、快照资源与脚本、真实交付校验、主agent退回有限纠错、取消、等待回答继续、幂等/冲突、跨owner隔离、重启中断及超过展示限额的完整参数/代码证据。使用模型替身的测试不等于真实模型场景验收。
