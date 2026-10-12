# Ficagent 网站

这是从“开发”整理出的浏览器版工作台，采用 **Vue 3 + Flask**。Python 同时提供网页、接口和聊天流，正常使用只启动一个 Python 进程。

## 安装和启动

使用 Python 3.10 或更高版本，下载仓库后在项目目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

然后用浏览器打开 [http://127.0.0.1:8893/](http://127.0.0.1:8893/)。保持终端运行，按 `Ctrl+C` 停止网站。

`main.py` 会优先使用本目录的 `.venv`，从其他目录用它的绝对路径启动也可以。无需启动桌面 App，也无需执行 npm、构建或打包命令。

## 功能与 Coze

保留账号注册和登录、智能体与模型选择、流式聊天、停止生成、任务历史、项目管理、文件上传/预览/下载、日程以及 Coze Studio/模型管理入口。

账号与 AI 功能需要可访问的 **Coze 服务**，默认地址为 `http://127.0.0.1:8888`；网站不会启动或停止它。实际聊天还需要 Coze 中有可用的模型配置。若 Coze 不在线，网页仍可显示，登录和模型功能会给出连接错误。换电脑运行时也需要配置可访问的 Coze 服务。

默认网站端口为 `8893`，可与原桌面应用同时运行。临时更换端口或 Coze 地址：

```bash
python main.py --port 8894 --coze-base http://127.0.0.1:8888
```

持久配置可将 `.env.example` 复制为 `.env` 后修改。程序读取网站根目录的 `.env`；终端环境变量和命令行参数可覆盖相应配置。

## 目录

```text
CD80-2026/
├── main.py                 唯一日常启动入口
├── requirements.txt        Python 依赖
├── .env.example            配置示例
├── frontend/
│   ├── src/                Vue 源码
│   ├── dist/               Python 直接提供的网页 JS/CSS/HTML
│   └── package*.json       仅修改前端源码时使用
├── backend/app/            Flask 接口、Coze 客户端和数据存储
├── data/                   本网站独立的用户记录与文件
├── tests/                  后端与启动入口回归测试
└── .venv/                  本网站独立 Python 环境
```

这里没有 Electron、App、安装包、发布压缩包和桌面打包脚本。`frontend/dist` 是运行网页需要的静态资源，已生成并随源码保留。网站的业务代码不引用相邻“开发”目录。

## 用户数据

运行时产生的用户记录与文件保存在 `data/`，应单独备份；仓库不包含已有用户数据。`.gitignore` 已排除 `data/`、实际 `.env` 配置、虚拟环境和临时文件。

## 换电脑或重建环境

使用 Python 3.10 或更高版本，在网站目录执行：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

`.venv` 不应直接跨电脑复制；用以上命令重建。前端静态资源已包含在项目中，启动网站无需 Node.js。

只有修改 Vue 源码后才需要重新生成静态资源，具体见 [前端说明](frontend/README.md)。后端文件修改后，停止并重新执行 `python main.py`。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
```

自动功能验证使用临时数据和模拟 Coze，不调用真实模型，不修改实际账号。真实模型的可用性取决于现有 Coze 配置。

