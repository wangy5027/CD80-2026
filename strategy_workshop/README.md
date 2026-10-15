# 策略工坊计算包

本目录是策略工坊的独立计算内核：十种固定策略、共享指标、交易账本、绩效指标、行情读取与映射工具。交互、账号隔离、会话、快照和受控执行位于 `backend/app/modules/strategy_workshop/`；页面位于 `frontend/src/modules/strategy-workshop/`。接入契约与本次验收状态见 [策略工坊接入](../docs/integration/策略工坊接入.md)。

每个 `strategies/<strategy_id>.py` 导出 `SPEC` 和 `build_targets(bars, parameters=None)`。目标序列只含 `None`、`0`、`1`，分别表示未就绪、空仓和持有。策略信号使用截至当日的行情字段，参考账本按下一交易日开盘及声明的交易限制模拟成交。十策略使用同一套费用、成交和指标实现。自定义规则由后端白名单 DSL 编译，在已校验的 CPython/WASI 中计算目标；宿主账本只读取校验后的目标，不执行模型生成的 Python。

本阶段限定日频、单标的、做多、无杠杆。数据门槛由后端固定：公共行情用于明确标记的工程诊断，`formalReportAvailable=false`。缺少完整交易日历、公司行动、复权或可成交状态时不能输出正式收益报告。历史回测记录保留运行时冻结的行情、策略代码和规则摘要；个人策略复跑沿用原代码，只允许重新配置原流程开放的标的、日期与资金。

行情源库、规范化样本及映射属于公共运行资源，位置由 `STRATEGY_WORKSHOP_SOURCE_DB`、`STRATEGY_WORKSHOP_MARKET_DB`、`STRATEGY_WORKSHOP_MAPPING_DIR` 配置。账号、会话和回测结果属于 `DATA_DIR/strategy-workshop.sqlite3`；不要复制来源项目的业务数据库。本包没有独立的模型凭据或账号配置。

快速检查：

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_strategy_workshop.py' -q
.venv/bin/python -B -m unittest discover -s tests -p 'test_cd80_preparation.py' -q
.venv/bin/python -B -m unittest discover -s tests -p 'test_workshop*.py' -q
```

这些测试使用临时账号目录或合成行情。真实模型、公共行情、浏览器和完整服务的验收须分别记录，不能由计算包单测代替。
