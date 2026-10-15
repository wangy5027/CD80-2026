# 本地历史日线分析 App

这是一个可导入的 `cd80.app/2` 三步应用实例：先调用平台官方 `cd80-history-tool` 读取本地日线，再由官方 `cd80-history` Skill 使用其冻结的 Tool 依赖解释结果，最后用代码核对证券、日期、来源并汇总。它只查本地历史数据，不声称实时行情或交易日覆盖完整。

## 文件与导入

- `app.zip`：直接在「技能池 → Apps → 创建 APP → 上传」选择的 ZIP；内含 `app.json`。
- `app.json`：完整 `cd80.app/2` 定义，也可单独上传或在完整定义编辑器中使用。
- `application.json`：平台导出的 `cd80.app-package/2` 包，同样可上传。
- `verification-cases.json`：两组可复制的验证输入和预期字段。
- `自然语言调用.txt`：在主对话里调用已启用应用的示例文本。
- `acceptance.json`：隔离账号中真实模型、只读数据服务、验证及运行的精简证据；不包含密钥或模型正文。
- `chat-acceptance.json`：主对话按文本选择精确 App 版本并真实运行的精简证据；`import-acceptance.json`：三种文件格式的隔离预览与纯保存证据。

三种导入格式任选一种即可。导入只是保存一个新的个人 App 版本，**仍为未验证、未启用**。导入后在该 App 的管理页检查绑定与运行条件，使用下面两组不同日期的用例完成 App 自身验证，结果通过后再主动启用。官方 Skill/Tool 的可信来源准入不替代 App 验证。导入包内含冻结组件快照；调用时仍受本机模型配置、只读数据服务、权限和运行沙箱限制。若导入环境没有这些运行条件，预览或验证会给出相应问题，不能把保存成功当作运行成功。

## 两组验证用例

用例 1 输入：

```json
{"code":"600000.SH","date":"2025-07-02","asset":"stock"}
```

预期核对：`summary.source=cd80_daily`、`summary.code=600000.SH`、`summary.date=2025-07-02`、`summary.close=14.2`、`summary.marketCompleteness=unknown`。价格只对应本次隔离验收读取的数据；在其他数据版本或时间运行时，应以实际只读服务返回值为准，不能固定假定收盘价。

用例 2 输入：

```json
{"code":"600000.SH","date":"2025-07-03","asset":"stock"}
```

预期核对：同样保留来源、证券、日期、单位与交易日完整性标记。本次隔离验收读到的收盘价是 `14.18` 元；换数据源时以实际返回为准。

验证用例的完整服务格式在 `verification-cases.json`。启用后还用 `2025-07-04` 做了第三次独立运行；本次只读服务返回收盘价 `14.58` 元，三步均成功。详见 `acceptance.json` 和 验收记录（本地资料，未随源码发布）。

## 本地重建

在当前工程已经配置 GLM-5.3-Flash 和本机只读金融数据服务的条件下，运行 `./.venv/bin/python scripts/build_official_history_app.py --run-live`。脚本显式创建独立测试账号和 `data/integration-acceptance/official-history-app-*` 临时运行目录，不写正式用户；它会实际调用模型与本地数据服务，并覆盖本目录的导出包及验收摘要。再运行 `./.venv/bin/python scripts/verify_official_history_chat.py --run-live` 可实际提交上面的主对话文本，检查是否选择精确已启用版本并完成运行。本机 UI 验收需要的隔离目录和临时登录信息保存在被 Git 排除的 `.run/official-history-app-runtime.json`，不随示例发布。请勿把隔离运行目录作为可分发包。
