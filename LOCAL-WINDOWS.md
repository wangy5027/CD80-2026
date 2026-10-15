# Windows 本地启动

双击项目根目录的 `start-local.cmd`，保留启动窗口。

- 网站：http://127.0.0.1:8894/desktop/
- 数据接口文档：http://127.0.0.1:8897/docs
- 按 Ctrl+C 停止网站和数据服务。端口已被使用时不要重复启动。

也可以在 PowerShell 中执行：

```powershell
cd D:\cd80-2026\platform\main-code
.\start-local.cmd
```

两个服务共用根目录的 Windows `.venv`，无需激活环境，也无需另外建立 `cd80-data-api/.venv`。网页使用已有的 `frontend/dist`，日常启动不需要 Node.js。

如果本次配置后服务已在后台启动，直接打开上述地址即可，不要重复启动。后台启动日志保存在 `data/windows-local.stdout.log` 和 `data/windows-local.stderr.log`。

服务必须在允许访问外网的本机进程中启动。若从受限沙箱启动，可能出现网页正常但对话提示“模型服务连接失败”的情况；此时需要停止旧服务，再从 Windows 双击启动文件，或让代理获得联网启动权限后重启。无需修改 API 密钥或关闭防火墙。

## 重建环境

需要 Python 3.11 或更新版本。不要复制其他系统的虚拟环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r cd80-data-api/requirements-dev.txt
.\.venv\Scripts\python.exe scripts/setup_agent_runtime.py
```

保留根目录及 `cd80-data-api` 下的 `.env`；启动器会加载数据服务配置，主站再加载自己的模型配置，已有环境变量优先。模型与数据密钥不需要填入浏览器。

保留 `data/` 及 `cd80-data-api/data/` 中的账号、上传文件和金融数据库。隔离运行时的编译缓存与操作系统和处理器相关，换电脑后重新执行安装脚本。

如需修改端口：

```powershell
.\start-local.cmd --website-port 8898 --api-port 8899
```

修改前端源码后，在 `frontend` 目录执行 `npm ci` 和 `npm run build`。开发前按当前依赖锁文件检查环境；不将旧安装记录视为当前依赖验证。

修改后端后，必须重启常驻服务才能加载新代码；只刷新浏览器或重建前端不会更新后端。先确认没有正在执行的会话、子任务或应用，再停止旧实例并重新启动，检查主站 `/desktop/health` 和数据服务 `/health`，最后刷新页面。未完成子任务会在服务重启后标记中断并暂停队列，失败请求不会自动重跑。最近一次维护及日志见[运行维护](docs/运行维护.md)。

## 状态与验收入口

原本机验收结果保存在 历史记录（本地资料，未随源码发布），当前模块状态见 项目状态（本地资料，未随源码发布）。本次文档整理没有重跑真实模型或业务回归。

配置来源和端口差异见 [配置说明](docs/配置说明.md)，备份和恢复见 [运行维护](docs/运行维护.md)。
