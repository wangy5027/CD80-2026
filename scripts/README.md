# 脚本说明

更新：2026-10-14。说明脚本用途、条件与副作用。执行结果记录在所属模块，不把入口说明当作已运行证明。

| 脚本 | 用途 | 副作用与条件 |
| --- | --- | --- |
| smoke_news_live.py | 临时账号和临时网站验证新闻 HTTP | 会访问真实新闻上游；可用 --output 保存报告；不验证模型 |
| smoke_agent_live.py | 应用生成和可选执行、复用验证 | 会调用真实模型并可能计费；--execute 执行，--reuse 复用；临时数据，失败可能在 data 写诊断 |
| verify_vision_reuse.py | 真实研究复用及空数据/缺工具边界 | 显式--live、--live-analysis或--live-edge-cases；临时账号/库，配置模型与本地金融只读接口，不写正式用户及金融库；完整分析与边界验证各最多2次任务，独立时间戳报告 |
| verify_vision_application.py | 真实模型创建固定应用并自然语言修改逻辑 | --preflight不调用模型；--live最多2次任务，每任务540秒/2次模型调用；--retry-adaptation需显式提供原失败报告，正常导入真实已保存定义后仅补验1次改编。合成持仓CSV、临时账号/库、独立数值/CSV核对，不读取正式历史或金融服务 |
| verify_conversation_authoring_live.py | 统一创作App/Tool/Skill的真实生成、双例验证及最终确认 | 显式`--run --kind app\|tool\|skill\|all`；读取环境变量或`.env`模型配置并消费正常额度；每类共享600秒/32请求/2修复，临时账号/`DATA_DIR`、合成整数、真实WASI，不读正式历史或金融库；时间戳报告在`data/conversation-authoring-live/`，失败也保留 |
| setup_agent_runtime.py | 准备既有隔离运行环境 | 联网下载、校验并写运行时和缓存；本次不改、不执行，详细边界沿用原说明 |
| ../cd80-data-api/scripts/start_all.py | 启动主站与数据服务 | 读取配置、创建子进程；默认 8894/8897，Ctrl+C 关闭 |
| ../cd80-data-api/scripts/init_config.py | 初始化数据服务配置 | 创建本地密钥配置；不覆盖已有 .env；本次不执行 |
| ../cd80-data-api/scripts/import_csv.py | 从源 CSV 构建数据库 | 创建数据库和导入记录，不作为日常启动步骤 |
| ../cd80-data-api/scripts/verify_db.py | 数据库及导入来源核验 | --pristine 仅适用初始导入库，不能对已运行库盲用 |
| ../cd80-data-api/scripts/verify_samples.py | 样本字段核对 | 需可访问原始源数据，不能用样本一致证明全部内容一致 |
| ../cd80-data-api/scripts/backup_db.py | 金融库一致性备份 | 创建新的备份文件；路径与恢复限制见运行维护 |

默认从项目根运行根 scripts 下脚本。数据导入、核验的参数先查看脚本帮助及 [数据服务说明](../cd80-data-api/README.md)，不要套用旧 Mac 绝对路径。
能力库测试入口见 [测试指南](../tests/README.md)，data中的契约生成脚本会改写Schema/示例，不能在文档同步中运行。脚本产生的验收证据需要归入对应模块记录并同步 项目状态（本地资料，未随源码发布）。


## 统一资产实施验收（2026-10-14）

- `validation_suite_helpers.py` 为旧验收/发布/审阅脚本提供真实双输入套件结果；不足两种输入明确阻断，Skill 效果需要逐例审阅。覆盖入口、命令和限制见脚本迁移验收（本地资料，未随源码发布）。
- `verify_asset_model_live.py --live` 在一次性账号和数据库中验证 Creator、固定模型 Tool 与独立 Skill，仅用合成整数。会调用已配置真实模型，需当前任务授权；报告保存到 `data/asset-authoring-live/`。`--creator-report <此前通过报告>` 可复用已生成草稿，仍重跑其实际代码验证。
- `verify_asset_authoring_live.py --preflight` 是平安银行指定2025区间只读预检，首次最多5次源读取；`--targeted --prior-report <报告>` 在同一连接用代码与日期过滤继续，累计上限40，分页完成不代表全年市场完整。
- `backup_asset_upgrade.py --data-dir <实际目录> --destination <不存在的备份目录>` 备份六个主站库并在副本验证新增表。先确认执行静止；不含用户文件/配置/金融库，不是跨存储原子恢复工具。
