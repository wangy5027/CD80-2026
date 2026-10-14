"""Run the standalone 成都八零 website with its own Python environment."""

from __future__ import annotations

import argparse
import errno
import os
import signal
import socket
import sys
from pathlib import Path


SITE_ROOT = Path(__file__).resolve().parent


def site_interpreter():
    return SITE_ROOT / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def prefer_site_venv():
    """Use this website's fixed dependencies even when launched by Conda Python."""
    venv = SITE_ROOT / ".venv"
    interpreter = site_interpreter()
    if interpreter.is_file() and Path(sys.prefix).resolve() != venv.resolve():
        os.execv(str(interpreter), [str(interpreter), str(SITE_ROOT / "main.py"), *sys.argv[1:]])


def load_dependencies():
    try:
        from dotenv import load_dotenv
        from waitress import create_server

        load_dotenv(SITE_ROOT / ".env", override=False)
        from backend.app import create_app
    except ModuleNotFoundError as error:
        interpreter = site_interpreter()
        invoke = "& " if sys.platform == "win32" else ""
        print(
            f"缺少 Python 依赖：{error.name}。请在网站目录安装依赖：\n"
            "  python -m venv .venv\n"
            f'  {invoke}"{interpreter}" -m pip install -r requirements.txt',
            file=sys.stderr,
        )
        raise SystemExit(2) from None
    return create_server, create_app


def site_path(value):
    path = Path(value).expanduser()
    return str((path if path.is_absolute() else SITE_ROOT / path).resolve())


def reserve_listener(port):
    """Own the port before initializing services that recover persisted jobs.

    Waitress enables SO_REUSEADDR, which permits competing listeners on Windows.
    A supplied socket with SO_EXCLUSIVEADDRUSE keeps that setting from being
    enabled; Waitress tolerates the failed setsockopt and uses this listener.
    Listening here also reserves the port on Unix before create_app runs.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        option = socket.SO_EXCLUSIVEADDRUSE if sys.platform == "win32" else socket.SO_REUSEADDR
        listener.setsockopt(socket.SOL_SOCKET, option, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1024)
        return listener
    except BaseException:
        listener.close()
        raise


def startup_error(error, port):
    if error.errno in {errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", 10048), errno.EACCES, 10013}:
        alternate = 8894 if port == 8893 else 8893
        print(
            f"端口 {port} 已被占用或不允许绑定。请检查已有进程和端口权限，或换一个端口：\n"
            f"  python main.py --port {alternate}",
            file=sys.stderr,
        )
    else:
        print(f"网站启动失败：{error}", file=sys.stderr)
    raise SystemExit(2) from None


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
    from backend.app.config import Config
    port = overrides.get("DESKTOP_PORT", Config.DESKTOP_PORT)
    try:
        listener = reserve_listener(port)
    except OSError as error:
        startup_error(error, port)
    overrides["DESKTOP_PORT"] = listener.getsockname()[1]
    app = None
    try:
        app = create_app(overrides)
        server = create_server(
            app,
            sockets=[listener],
            threads=8,
            max_request_body_size=app.config["MAX_CONTENT_LENGTH"],
        )
    except OSError as error:
        if app is not None:
            app.extensions["workbench_service"].close()
        listener.close()
        startup_error(error, port)
    except BaseException:
        if app is not None:
            app.extensions["workbench_service"].close()
        listener.close()
        raise
    port = int(server.effective_port)
    app.config["DESKTOP_PORT"] = port

    def stop(_signal, _frame):
        app.extensions["workbench_service"].close()
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, stop)
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
        listener.close()


if __name__ == "__main__":
    main()
