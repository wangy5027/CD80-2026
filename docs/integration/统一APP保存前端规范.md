# 统一 APP 先保存与能力来源准入：前端规范

状态：2026-10-15 前端与服务端契约已实现，定向自动测试通过；全量构建和真实浏览器验收另记。

## 用户流程

所有创建现代 APP 的入口遵守同一顺序：形成可保存的完整定义，先写入当前账号的个人应用池，显示精确版本的“未验证、未启用”；用户随后从该版本详情检查或修改步骤、源码、资源和连接，再主动发起两组实际验证，验证通过后才可启用与正常运行。保存和查看不触发验证或业务执行。修改已启用 APP 时，新版本仍先保存为未验证，旧可用版本和运行历史保持原状。

应用工坊选择“创建新的 APP”后，生成出的版本由服务端立即纯保存；第三步展示已保存的精确版本及“打开应用详情”，不再要求第二次点击加入应用池。策略工坊创建的个人策略也先入池并标记未验证，再从个人策略详情修改规则、配置回测和查看验证。策略回测是诊断性结果，是否满足验证准入由服务端按证据和数据门槛裁定，不从 UI 的“回测成功”推断。

官方 Skill / Tool、其他用户主动公开发布且服务端可证明内容与来源未变的精确版本，加入个人池后免重复验证；它们仍须具备所需配置、权限和依赖，并由用户主动启用。手写、上传、模型新生成及修改后的 Skill / Tool 版本必须验证。前端只使用服务端的 `admission` 和 `readiness` 判断，不能凭名称、卡片来源或作者声明放行；免验证状态不伪造 `passed` 验证记录。

## 创建入口盘点

| 可见入口 | 当前调用路径 | 本轮目标 |
| --- | --- | --- |
| 技能池创建 APP：外部 Python/JSON/ZIP，或自然语言创建 | 外部由 `AppCreatePanel` 调 `/asset-apps/import/file-preview`、`/asset-apps/import`；自然语言由统一创作会话以 `kind:app,mode:prepare` 生成 | 仅两种顶层入口；格式预览不执行，合法定义即纯保存未验证版本，详情再配置、修改、验证与启用 |
| 应用工坊创建 | `/app-workshop/sessions/{id}/create` 后另调 `/save` | create 完成即由服务端幂等保存；旧 `/save` 保留结果恢复 |
| 主对话、子任务、旧研究/流程升级、统一应用详情的新建和修改、导入原件准备 | `AuthoringWorkspace` 以 `mode:prepare` 创建 `/authoring-sessions`，旧流程先验证再 `confirm(save_and_enable)` | APP 仍以 `mode:prepare` 生成定义，但生成后立即纯保存；详情凭 `session.result` 打开个人应用版本，不调用 APP 旧 confirm |
| 统一应用高级编辑、兼容应用包导入 | `AssetAuthoringPanel` 调 `/asset-apps` 或 `/asset-apps/import` | 保留直接纯保存；验证、启用仍在版本详情 |
| 策略工坊个人策略 | 旧 `/strategy-workshop/runs/{runId}/personal-app` 要求先完成回测 | 模板确认或新规则生成后即保存 `savedPersonalApp`；个人策略详情可从冻结版本修改或主动验证，旧回测仍可查看 |
| 平台预置、公开成果和旧版应用的复制 | 各自原池复制接口 | 平台预置与旧版共创复制同时生成未验证现代 APP；成功返回精确 `assetId/versionId` 并打开统一详情，旧运行和历史保持兼容 |

未转换的旧版运行记录继续按既有路径查看。外部未知格式可完整保存原件，但不能据此显示成“已验证 APP”；准备为平台 APP 后才有可验证的个人版本。

技能池的两种创建入口共用一个 `AppCreatePanel`。外部导入提供当前后端合同生成的 JSON、Python、源码 ZIP 示例、`cd80.app/2` 机器 Schema、APP HTTP 接口说明和独立数据服务资料下载；静态 `issues` 可提示运行缺口，但不阻止已解析完整定义的私人保存。格式或路径安全错误由预览接口明确拒绝。保存结果未知时保留同一包和幂等键重试；账号切换取消旧请求。自然语言入口固定 APP 类型，复用 `AuthoringWorkspace` 的 `mode:prepare`，在此入口隐藏“只存草稿”；会话生成完整定义后通过 `session.result` 展示未验证收据，用户可打开精确版本详情。已保存 APP 的高级编辑仍在详情中，不列为第三种创建方式。详细来源格式与下载合同见[技能池 APP 两种创建入口](APP两种创建入口.md)。

## 精确接口与状态

- 现代 APP：`POST /desktop/api/asset-apps` 或 `/import` 返回 `appId`/`id`、`versionId`；`GET /asset-apps/{appId}?versionId={versionId}` 的 `verification.status` 是版本状态。新保存版本为 `unverified`、未启用。`/verify` 和 `/enable` 仍只从详情按当前 `revision`、精确版本调用；前端按钮不代表服务端准入已通过。
- 统一创作 APP：`POST /desktop/api/authoring-sessions` 的 `mode:prepare` 保持兼容，但后台只整理并静态检查 APP，生成可保存定义后立即写入个人应用池；不执行两例测试或启用。完成后 `session.phase=completed` 且 `session.result={assetId,versionId}` 在详情和列表可读。`detail.commit` 可展示保存收据。APP 不再提供 `confirm(save_and_enable)` 绕过路径。创建初始空白入口不造占位资产。
- 应用工坊：create 完成返回 `status:saved`、`savedAppId`、`savedVersionId`；相同请求重试须返回同一保存结果。旧 save 端点仍可恢复既有会话。
- 能力库：个人列表、资产详情、版本列表和精确版本详情提供 `admission:{mode:'trusted_release'|'validation_required',source:'official'|'community'|null,validationRequired:boolean}`。来源可信且当前条件齐备时 `readiness:'ready'`，允许用户主动启用，不创建伪造的 passed 验证；仍需验证的版本保持 `unverified`→`verified`。同一资产不同版本的准入要读取所选精确版本；旧已复制且内容未改的可信版本也由服务端识别。目录的“添加”只复制，不自动运行官方示例、验证或启用。
  可信精确版本在管理页只展示方法、依赖、配置和主动启用，隐藏验证表单与测试用例入口；自建、上传或修改后的版本继续显示原验证流程。
- 策略个人应用：模板 `selection` 采用或自定义规则生成到 `rules_ready` 后，服务端先保存 `session.savedPersonalApp={id,versionId,verification:{status:'unverified'}}`。`POST /strategy-workshop/sessions/{sessionId}/personal-app` 可补存已形成定义的会话；`GET /strategy-workshop/personal-apps/{id}` 在未回测时返回 `{app,run:null,definition}`。详情再用 `POST /personal-apps/{id}/sessions` 的 `mode:'verify'` 或 `mode:'edit'` 开新会话；编辑生成新的未验证个人版本，验证状态只来自匹配冻结源码的实际诊断回测。旧已有回测个人策略继续保留原详情与结果。
- 旧版复制：`POST /agents/copy-preset` 和 `POST /evolution/publications/{id}/copy` 保留旧结果，并增加顶层 `assetId/versionId/detail`。新现代版本为未验证、未启用；列表“已拥有”必须由现代个人 APP 的服务端 `provenance` 关联判定，旧流程的 `personalCopies` 不能替代现代归属。公开研究复制对应 `legacy_publication/publicationId`，公开流程复制对应 `legacy_pipeline_import/source.kind=publication`。

## 失败与隔离

保存结果未知时复用原内容、账号和幂等键核实，不创建第二个版本；如果后台已经返回精确 `assetId/versionId`，以该结果为保存事实，目录刷新失败只显示同步错误。账号切换取消旧请求、清空旧账号详情和草稿上下文。静态格式问题可阻止形成可保存完整定义；缺连接、依赖或实际效果未测不妨碍已定义版本的纯保存，但阻止启用和正常运行。

## 前端验收条件

1. 每个现代 APP 创建入口成功后能在“我的 APP”看到精确未验证版本；技能池入口仅显示外部导入与自然语言创建两种方式，Python/JSON/ZIP 格式检查和保存期间没有 `/verify`、`/enable` 或业务运行请求；详情能修改、验证、主动启用，未通过前运行按钮不可用。
2. 应用工坊 create 完成即显示已保存，并由原结果直达精确版本；重复点击、刷新或未知网络结果不产生重复应用。
3. 统一创作 APP 完成后显示“已保存、未验证”，提供精确“打开应用详情”；没有旧“确认保存并使用”按钮。Skill/Tool 既有创作审阅行为保持独立。
4. 可信官方/公开 Skill/Tool 在配置齐备时显示“免验证 · 可启用”，可主动启用并用于对话；手写、上传、模型新版本和可信副本修改后的新版本仍要求验证。缺依赖、配置或权限时均不可启用。
5. 策略新建在回测前进入个人池且显示未验证；从其详情可继续修改、回测和核对验证结果，历史策略与旧回测可继续查看。
6. 切换账号、异步迟到响应、旧版本选择及结果未知重试不串号、不打开错误版本。

本页记录已实现的前端流程与跨模块接口，不替代服务端资产格式规范。权威格式和提交字段仍见[统一创作接口与存储契约](../asset-authoring/接口与存储契约.md)、[能力库技术规范](../capability-library/技术规范.md)及[整合技术规范](技术规范.md)。


## 2026-10-15 未验证 APP 的平台审核提示

按用户指定文案，在我的 APP 的未验证详情（目录弹窗、现代 APP 完整详情及已保存个人策略详情）显示：“您的应用创建成功，平台会在24小时之内对该应用进行验证审核，请注意查收邮箱”。仅缺省/未验证状态显示；已通过、正在验证或失败状态继续显示其真实状态。原测试、修改与启用入口及服务端门槛保留。

本次仅为展示文案更新，不新增审核队列、24小时定时审核或邮件发送实现；目前服务端仍按显式验证请求执行，不能将文案作为已投递审核或邮件的证据。
