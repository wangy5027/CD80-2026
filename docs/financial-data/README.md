# 金融数据网站接入

状态：2026-10-13 现状整理。源码：backend/app/modules/financial_data/、frontend/src/modules/workbench/financialDataApi.js 与 FinancialDataPanel.vue；独立服务在 cd80-data-api/。

范围：网站数据管理和跨服务接入。独立数据服务的 CONTRACT、OpenAPI、data_catalog 保留原位作为唯一契约来源。

- [产品设计](产品设计.md)：对当前用户行为的整理。
- [技术规范](技术规范.md)：实现边界和契约入口。
- 开发进度（本地资料，未随源码发布）：实现、验证和后续任务。
- 验收记录（本地资料，未随源码发布）：历史与新证据入口。
- [个人数据文件与跨页引用](个人数据文件.md)：本地文件上传、账号归属与问答引用的本轮设计及接口边界。

2026-10-15 起，数据管理同时包含官方本地数据目录和当前账号的“我的数据文件”。私人文件复用工作台文件池，具体接口实现位于 `backend/app/modules/workbench/data_files.py`、`service.py`，与官方 SQLite 表的读取、写入权限分别管理。

更新本模块进度时同步 项目状态（本地资料，未随源码发布），遵循 [文档流程](../文档与开发流程规范.md)。
