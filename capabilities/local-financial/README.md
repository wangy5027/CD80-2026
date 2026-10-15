# 本地金融公共能力

2026-10-13：4 个 Tool 与 5 个 Skill 已完成真实示例验证和效果复核，已登记公共发布目录。正式后端现已重启并加载，刷新网站即可在公共目录查看。

| Tool | Skill | 范围 |
| --- | --- | --- |
| cd80-history-tool | cd80-history | 本地股票、ETF、指数不复权日线 |
| cd80-announcement-tool | cd80-announcement | 公告元数据、原始标题与原文链接 |
| cd80-margin-tool | cd80-margin | 两融原值与融资净买入；金额单位尚待核对 |
| cd80-news-tool | cd80-news | 已接入 AkShare 新闻与缓存信息 |
| 无 | cd80-table | 把已有数据排成对话表格，保留缺失值 |

使用顺序：进入扩展公共库 → 保存官方Skill个人副本（平台自动准备连接和精确依赖工具，并实际验证依赖） → 测试Skill效果 → 决定是否启用。官方Tool也直接保存、测试即可，无需MCP或数据源参数。已保存的旧副本可点“准备官方依赖”修复。表格Skill无取数依赖。主Skill不会自动通过效果审核；用户明确停用的工具不会被自动恢复。

包使用平台 `cd80.capability/1` 与 `python_connector` 扩展，Skill 正文为标准 SKILL.md。其他平台须提供相应连接协议，不保证 ZIP 跨平台即装即用。金融逻辑位于 src/query.py，经构建复制到独立包内；宿主只负责受控接口、安全与生命周期。保留现有南财入口，包不携带或调用南财凭据。

构建：`.venv\Scripts\python.exe -X utf8 scripts/build_local_financial_packages.py`。构建只产生候选包，不修改公共发布目录。

真实验收：`scripts/verify_local_financial.py` 使用临时账户、真实服务与现有模型，会发生模型调用；输出位于忽略的 data/，保留可丢弃的临时库用于效果复核及主对话验收。`--resume` 用于重跑未通过或内容变化的示例。由维护者逐项复核后通过 `scripts/review_local_financial.py` 处理隔离验证记录，最后 `scripts/publish_local_financial.py` 写入经过检查的目录与证据。不把运行成功等同效果合格。

已发布证据见 release-evidence.json（本地资料，未随源码发布），范围与限制见 验收报告（本地资料，未随源码发布）。股票以外的成交量/成交额及两融金额未核实单位时展示源表原值；不推断、不换算。新闻的真实样例覆盖东方财富，财联社接口参数受支持但本轮未做真实上游验证。
