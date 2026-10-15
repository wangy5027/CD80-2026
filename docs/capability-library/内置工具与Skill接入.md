# 内置工具与 Skill 接入（兼容目录）

本文件描述内置ToolRegistry和本地SKILL.md兼容机制，不是个人能力库的导入/验证/版本规则。新库见 [模块入口](README.md)。Windows日常使用项目根 `start-local.cmd`，不需单独启动工具进程或Node。前端构建结果已在 `frontend/dist/`；改前端源码才需要重新构建。

## 三种能力的职责

- **工具（Tool）**：执行代码的接口。`ToolRegistry` 提供名称、JSON 参数说明、可用状态和 Python handler。工具可以不加载任何 Skill 独立执行。
- **Skill**：可选的任务说明书。`skills/<name>/SKILL.md` 定义何时用工具、如何核对和组织结果。读取 Skill 不执行它包含的命令，也不安装依赖。
- **应用（AgentSpec）**：可保存、复用的顺序流程。它可以调用平台注册工具，也可执行生成的 Python；生成代码继续在 WASI 中隔离运行。

同一份注册目录供普通聊天、独立 HTTP 接口和应用执行使用。工具 handler 不读取 Skill，Skill 加载器不依赖具体工具。新增工具无需改 LangGraph 的节点或前端分支；新增 Skill 无需改后端代码。

## 从页面使用

左侧进入 **扩展**，打开旧内置目录折叠区；以下操作适用于内置卡片，个人能力库需先完成配置、验证和启用：

1. 在工具卡片点击 **调用**，填写参数，点击 **执行工具**。结果在弹窗中返回；这条路径直接访问工具接口，不经过模型，也不要求选择 Skill。留空的可选参数不会发送。
2. 在 Skill 卡片点击 **查看说明** 阅读正文，或点击 **用于对话**。网站切换到普通对话，输入框上方显示所选技能；填写问题并发送后，后端确定加载这些 Skill。
3. 每条消息最多选择 3 个 Skill，点击技能标签的移除按钮即可取消选择。选择仅作用于下一条消息；成功提交后清除，发送前失败则保留以便重试。草稿按账号和任务保存选择。

工具卡片显示“未配置”时不能执行，请在设置中检查配置。Skill 提供操作规则，选用后是否需要查询由问题决定；选用 Skill 本身不等于自动运行所有相关工具。

## 当前接入

| 工具 | 可选 Skill | 范围 |
| --- | --- | --- |
| `web_search` | `news-search` | Gitee 网页搜索 |
| `sfc_news` | `sfc-news` | 最新、关键词、个股资讯 |
| `sfc_timeline` | `sfc-timeline` | 有界时间窗口内的财经快讯 |
| `sfc_search` | `sfc-search` | 已支持分类的金融对象检索 |
| `sfc_research` | `sfc-research` | 研报查询 |
| `sfc_announcements` | `sfc-announcement` | 公告列表和详情 |
| `sfc_quotes` | `sfc-quote` | 个股行情、名称代码检索 |
| `sfc_history` | `sfc-history` | K 线及日期范围筛选 |
| `sfc_finance` | `sfc-finance` | 财务指标和三张财务报表 |
| `sfc_macro` | `sfc-macro` | 顶层分类及已知指标代码的序列 |
| `sfc_margin` | `sfc-margin` | 融资融券记录 |
| `sfc_lhb` | `sfc-lhb` | 龙虎榜汇总和明细 |
| `sfc_calendar` | `sfc-invest-calendar` | 经济、投资、公司和新股日历 |
| `sfc_themes` | `sfc-theme` | 主题推荐、详情和成分股 |

这是对已验证接口的适配，并未宣称南财原有全部子命令已开放。模拟交易、自选股账户、图表图片、HTML 截图、盘前聚合和实验分析模板不在本轮注册目录中。

宏观检索目前仅搜索顶层分类名称，不能冒充全量指标搜索；历史 K 线和快讯有扫描范围限制，响应的 `coverage`、`localTruncated`、`fieldsTruncated` 及内容截断计数必须保留。`truncated`/`localTruncated` 仅说明本次返回记录的本地裁切，不能证明上游没有后续页；`coverage.complete` 为未知或 false 时不可声称数据完整。数据日期不等于查询时间；HTTP/业务失败会报错，不转换为空数据。无真实链接的记录不制造来源 URL。

## 本地配置

```dotenv
GITEE_AI_API_KEY=你的模型令牌
SKILLS_DIR=skills
SFC_TOKEN=你的南财令牌
SFC_REQUEST_TIMEOUT=20
```

已有令牌保留在项目 `.env`，不会通过运行时目录返回浏览器。也可显式设置 `SFC_KEY_FILE` 读取凭据文件；系统不自动依赖 `.codex`、旧网站或用户主目录下的 runner。路径相对网站根目录解析。更改后端配置或工具代码后重启服务；Skill 文件在发现和加载时读取，无需重启。

输入框的“关闭网页搜索”仅阻止 `search` 能力（当前为 `web_search`）；南财专用查询属于 `financial_data`，仍可使用。应用运行同样记录本轮 `searchMode` 并执行该约束。这里不控制普通模型请求。

## 独立 HTTP 接口

接口使用本站登录 Cookie，未登录返回 401。服务只接受本机地址。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /desktop/api/runtime` | 工具、Skill、安全配置状态及原有运行时信息 |
| `GET /desktop/api/tools` | 全部工具的公开描述、参数 schema、可用性 |
| `POST /desktop/api/tools/<name>/execute` | 直接执行一个已注册工具，不调用模型或 Skill |
| `GET /desktop/api/skills` | Skill 名称、说明及加载警告 |
| `GET /desktop/api/skills/<name>` | 按需读取正文 |

在已登录网站的浏览器控制台可以测试：

```javascript
const response = await fetch('/desktop/api/tools/sfc_news/execute', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ arguments: { action: 'recent', limit: 3 } })
});
console.log(await response.json());
```

成功返回 `{name, result, sources}`。参数以 `/tools` 实时返回的 schema 为准。工具是否已配置与上游服务是否本次成功是两件事；调用方必须检查 HTTP 状态。

聊天请求还支持 `skillNames`（最多 3 个名称的数组）；服务端在响应流开始前从真实目录读取正文，未知或非法名称返回 400。客户端不能指定 Skill 正文。未传此字段时仍由模型按需加载。

普通聊天通过模型 function calling 调用工具，SSE 返回 `tool_status`、`skill_loaded`、来源和文字；历史消息分别存储 `toolsUsed` 与 `skillsUsed`。`news-search` 是 Skill，`web_search` 才是搜索工具。来源在正文中使用短引用，完整条目放在可展开来源面板。

## 新增一个独立工具

在 `backend/app/modules/workbench/` 中新增模块，实现 handler 和声明：

```python
from .errors import GatewayError
from .tool_registry import ToolResult, ToolSpec


def measure(arguments, context):
    # schema 提供给模型；handler 仍须校验真实输入。
    if set(arguments) != {"text"} or not isinstance(arguments["text"], str):
        raise GatewayError("需要 text 字符串。", 400)
    if len(arguments["text"]) > 1000:
        raise GatewayError("文本超过长度限制。", 400)
    return ToolResult({"length": len(arguments["text"])})


def build_measure_tool():
    return ToolSpec(
        name="measure_text",
        description="计算最多 1000 字符的文本长度。",
        parameters={"type": "object", "properties": {
            "text": {"type": "string", "maxLength": 1000}},
            "required": ["text"], "additionalProperties": False},
        handler=measure,
        max_calls=3,
    )
```

然后只在 `builtin_tools.py` 的 `build_tool_registry` 组合入口中导入并注册：

```python
registry.register(build_measure_tool())
```

工具代码必须经开发者显式注册；不会扫描、导入任意 Python 文件。若需外部查询，凭据由后端配置注入，目标地址由适配器固定，限制超时和返回大小。使用 `context.stopped` 响应停止，HTTP 响应通过 `context.on_response` 登记以支持中止。可用状态由 `available` 提供。`max_calls` 是每轮聊天/每次应用的同名工具上限；独立 HTTP 请求一次执行一个工具。

`ToolResult.content` 必须是 JSON 对象，返回总量最多 512 KiB；可选 `sources` 必须来自真实结果。当前共享接口用于可信只读工具。以后新增下单、文件修改等写工具时，须扩展明确授权流程后再对模型开放。

## 新增一个 Skill

新建 `skills/report-guide/SKILL.md`：

```markdown
---
name: report-guide
description: 当用户希望解释文本长度时，说明测量结果及字符计数口径。
---
必要时调用 measure_text，参数遵循工具目录。
仅引用真实返回的 length；调用失败时说明失败，不猜测结果。
用户只要数值时直接给数值；需要解释时再说明字符数口径。
```

目录名与 `name` 必须一致，使用小写字母、数字、单连字符，最长 64 字符。`description` 说明触发场景，正文写步骤及结果要求。不放密钥、账户凭据、机器绝对路径或安装/启动命令。

加载器检查顶层直接子目录、YAML 前言和正文，不接受符号链接；单文件最多 64 KiB，最多发现 64 个条目。它实现的是 `SKILL.md` 指令加载，不是可自动执行 `scripts/` 或完整第三方插件的运行器。搬入带脚本的第三方 Skill，先把需要执行的能力适配成独立注册工具，再调整正文。

`load_skill` 是模型按需加载正文的控制入口；它不是任何数据工具的前置条件。删除某个 Skill 后对应工具仍可正常调用。应用已确认的 Skill 正文快照不随外部文件变化而变更。

## 生成应用如何引用工具与 Skill

Planner 动态接收可用工具的实际 schema，以及 Skill 的名称、说明。生成的定义可以使用：

```json
{
  "tools": [{
    "id": "query_news",
    "version": "1",
    "registeredTool": "sfc_news",
    "permissions": {"network": false, "files": []}
  }],
  "skills": [{"id": "news_guide", "registeredSkill": "sfc-news"}],
  "pipeline": [
    {"id": "query", "kind": "tool", "tool": "query_news", "inputFrom": "run.input"},
    {"id": "explain", "kind": "model", "purpose": "解释实际结果", "inputFrom": ["steps.query.output"]}
  ]
}
```

这是引用部分示例，完整定义仍需要 `schemaVersion`、名称、目标、prompt、输入输出 schema 和权限等字段。`skills` 可为 `[]`。注册引用不得同时写生成代码或申请文件权限。

在预览前，平台以注册器为准补全工具描述、参数 schema、输出 schema，以及真实 Skill 正文；模型不能伪造注册 Skill 的正文。用户确认的是冻结后的方案。执行时只传 JSON 参数，不注入宿主路径、文件内容或令牌。工具参数 schema 改变后，旧方案会要求重新生成确认；同名工具超出调用上限的方案会在预览前拒绝。

`permissions.network:false` 保持生成 Python 无网络。声明的 `registeredTool` 通过后端固定适配器执行查询，与沙箱内任意联网不同。生成代码需要变换参数时可先执行独立 WASI 步骤，再引用其输出调用注册工具。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
npm --prefix frontend test
npm --prefix frontend run build
```

`tests/test_shared_capabilities.py` 覆盖不加载 Skill、预览不执行、确认执行、保存复用、Skill 快照、schema 漂移和关闭网页搜索。`tests/test_sfc_tools.py` 验证输入边界、错误/取消和数据筛选。真实接口抽测与浏览器结果见同目录验收记录；单个样例成功不代表所有上游数据永久可用。
