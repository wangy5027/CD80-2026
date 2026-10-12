"""Run the standalone 成都八零 website with its own Python environment."""

from __future__ import annotations

import argparse
import errno
import os
import signal
import sys
from pathlib import Path


SITE_ROOT = Path(__file__).resolve().parent


def prefer_site_venv():
    """Use this website's fixed dependencies even when launched by Conda Python."""
    venv = SITE_ROOT / ".venv"
    interpreter = venv / "bin" / "python"
    if interpreter.is_file() and Path(sys.prefix).resolve() != venv.resolve():
        os.execv(str(interpreter), [str(interpreter), str(SITE_ROOT / "main.py"), *sys.argv[1:]])


def load_dependencies():
    try:
        from dotenv import load_dotenv
        from waitress import create_server

        load_dotenv(SITE_ROOT / ".env", override=False)
        from backend.app import create_app
    except ModuleNotFoundError as error:
        print(
            f"缺少 Python 依赖：{error.name}。请在网站目录安装依赖：\n"
            "  uv pip install --python .venv/bin/python -r requirements.txt\n"
            "若未安装 uv：python -m venv .venv && .venv/bin/python -m pip install -r requirements.txt",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    return create_server, create_app


def site_path(value):
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else SITE_ROOT / path).resolve())


def main():
    prefer_site_venv()
    create_server, create_app = load_dependencies()

    parser = argparse.ArgumentParser(description="成都八零独立网站（本机 Flask + Waitress）")
    parser.add_argument("--port", type=int, help="监听端口，默认 8893")
    parser.add_argument("--data-dir", help="用户记录目录，默认 网站/data")
    args = parser.parse_args()
    overrides = {}
    if args.port is not None:
        overrides["DESKTOP_PORT"] = args.port
    if args.data_dir is not None:
        overrides["DATA_DIR"] = site_path(args.data_dir)
    app = create_app(overrides)
    try:
        server = create_server(
            app,
            host="127.0.0.1",
            port=app.config["DESKTOP_PORT"],
            threads=8,
            max_request_body_size=app.config["MAX_CONTENT_LENGTH"],
        )
    except OSError as error:
        app.extensions["workbench_service"].close()
        if error.errno == errno.EADDRINUSE:
            port = app.config["DESKTOP_PORT"]
            alternate = 8894 if port == 8893 else 8893
            print(
                f"端口 {port} 已被占用。请停止已有的网站进程，或换一个端口：\n"
                f"  python main.py --port {alternate}",
                file=sys.stderr,
            )
        else:
            print(f"网站启动失败：{error}", file=sys.stderr)
        raise SystemExit(2) from None
    port = server.effective_port
    app.config["DESKTOP_PORT"] = port

    def stop(_signal, _frame):
        app.extensions["workbench_service"].close()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"成都八零网站已启动：http://127.0.0.1:{port}/desktop/", flush=True)
    model = app.extensions["workbench_service"].model_status()
    print(f"LangGraph 已就绪；{model['provider']} {model['model']} {'已配置' if model['configured'] else '未配置'}。", flush=True)
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        app.extensions["workbench_service"].close()
        server.close()


if __name__ == "__main__":
    main()
