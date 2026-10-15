"""Run the supplied Vue/Flask website and SQLite API together on loopback."""
from pathlib import Path
import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import json


ROOT = Path(__file__).resolve().parents[1]


def wait_api(url, process):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('SQLite API exited during startup; see preceding log.')
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    payload = json.load(response)
                    if isinstance(payload, dict):
                        return
        except (OSError, ValueError):
            pass
        time.sleep(0.25)
    raise RuntimeError('SQLite API did not become healthy within 30 seconds.')


def main():
    from dotenv import load_dotenv
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--website', type=Path, default=ROOT.parent)
    parser.add_argument('--website-port', type=int, default=8894)
    parser.add_argument('--api-port', type=int, default=8897)
    args = parser.parse_args()
    website = args.website.resolve()
    if not (website / 'main.py').is_file():
        parser.error('Website main.py not found; pass --website /path/to/website')
    if args.website_port == args.api_port:
        parser.error('Website and data API ports must be different.')
    for port in (args.website_port, args.api_port):
        if not 1 <= port <= 65535:
            parser.error('Port must be between 1 and 65535.')
        with socket.socket() as probe:
            # Permit restart during TIME_WAIT; an active listener still blocks binding.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(('127.0.0.1', port))
            except OSError:
                parser.error(f'Port {port} is already occupied; select another port.')
    load_dotenv(ROOT / '.env', override=False)
    env = os.environ.copy()
    db = Path(env.get('CD80_DB_PATH', 'data/cd80.sqlite3')).expanduser()
    db = db if db.is_absolute() else ROOT / db
    if not db.is_file():
        parser.error('SQLite database is missing; run import_csv.py first.')
    env['CD80_DB_PATH'] = str(db.resolve())
    env['CD80_DATA_API_URL'] = f'http://127.0.0.1:{args.api_port}'
    children = []
    def stop(_sig, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        api = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(args.api_port)], cwd=ROOT, env=env)
        children.append(api)
        wait_api(f'http://127.0.0.1:{args.api_port}/health', api)
        site = subprocess.Popen([sys.executable, str(website / 'main.py'), '--port', str(args.website_port)], cwd=website, env=env)
        children.append(site)
        print(f'Website: http://127.0.0.1:{args.website_port}/desktop/', flush=True)
        print(f'Data API docs: http://127.0.0.1:{args.api_port}/docs', flush=True)
        while all(child.poll() is None for child in children):
            time.sleep(0.5)
        failed = next(child for child in children if child.poll() is not None)
        return failed.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == '__main__':
    raise SystemExit(main())
