# 外部 APP 导入示例

本目录展示同一个 `cd80.app/2` 运行合同的四种文件运输形式。导入只解析、检查并保存到当前账号应用池；新版本默认**未验证、未启用**。保存不会执行源码、Skill、Tool 或调用模型。定义的 `testCases` 首项保存原任务输入及独立预期；无法独立确定预期时填 `null`，随后在详情中补齐。至少通过这一组原任务验证后才可启用，其他用例可选。

| 文件 | 用途 |
| --- | --- |
| `complete-app.json` | 完整标准定义，代码与资源可内嵌；可直接选择上传。 |
| `export-package.json` | `cd80.app-package/2` 外层包；`digest` 是内部 definition 规范化 JSON 的 SHA-256，修改定义后须重新计算。 |
| `single-file.py` | 简化的单文件 Python 入口，只有静态 `APP_INTERFACE` 和 `def run(payload)`；平台用 AST 读取声明，不导入或执行文件。 |
| `source-app.zip` | 源码包：根 `app.json` 通过 `codeFile` / `resourceFiles` 指向 `src/main.py` 与 `assets/notes.txt`。导入后引用被转换为标准节点的 `code` / `files`，字节完整保留。 |
| `cd80.app-2.schema.json` | 完整标准定义的机器可读 JSON Schema。 |
| `app-import.openapi.json` | 预览、保存和下载接口的 OpenAPI 3.1 描述，另说明 ZIP/Python 运输字段。 |
| `自然语言调用.txt` | 验证、启用后可复制到主对话的调用示例。 |

网站内同样提供六个下载：`/desktop/api/asset-apps/import/schema.json`、`specification.json`、`template.json`、`example.py`、`template.zip`、`data-interface.zip`。最后一项单独打包 `cd80-data-api` 的公开数据服务合同与数据目录，不包含真实数据或配置。

## ZIP 的源码映射

ZIP 须包含根目录或一层文件夹中的 `app.json` / `application.json`。只在 `kind:"code"` 节点使用 `codeFile` 和 `resourceFiles`，路径相对于入口所在文件夹，例如：

```json
{
  "codeFile": "src/main.py",
  "resourceFiles": ["assets/notes.txt"]
}
```

`codeFile` 与 `code` 互斥。`resourceFiles` 是字符串路径数组，资源归一为同名的 `files` 条目。除 Finder 元数据外，每个 ZIP 文件都要被声明且只能引用一次；不接受逃逸路径、链接、加密、重名或大小写冲突文件。内嵌 `files` 与外部资源同名也会拒绝。若只有 JSON 定义而不带 ZIP，必须直接填入标准 `code` / `files`，不能留 `codeFile`。

## Python 简化入口

`APP_INTERFACE` 须是唯一的顶层静态字面量对象，包含 `name`、`description`、`inputSchema`、`outputSchema`、`rules`，并可附 `testCases`；另需唯一的顶层同步 `def run(payload)`。`inputSchema` 须为 object，明确列出 `properties`，每个属性都出现在 `required` 中，属性名不含点号，且 `additionalProperties` 为 `false`。这保证每个输入字段都能无损映射到现有标准 APP 的 `inputBindings`；可选字段、复杂绑定或多步骤流程请直接上传完整 JSON/ZIP 定义。`outputSchema` 映射到代码步骤的 `delivery.schema`，最终结果固定为该步骤的完整输出。源 Python 文本完整留在标准节点的 `code` 中。

文件上传使用 `POST /desktop/api/asset-apps/import/file-preview` 的单个 `file` multipart 字段；预览响应中 `package` 可以原样作为 `POST /desktop/api/asset-apps/import` 的请求体 `{ "package": ... }`，并提供 `Idempotency-Key` 请求头。格式/安全错误会直接拒绝；可表示的静态运行缺口会在 `issues` 中展示，仍可先保存，之后修复并验证。
