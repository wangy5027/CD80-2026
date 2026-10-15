"""Manage only this checkout's local website and data-service supervisor."""
from pathlib import Path
import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.run'
PID_FILE = STATE / 'server.json'
LOG = STATE / 'server.log'
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def record():
    try:
        return json.loads(PID_FILE.read_text())
    except (OSError, ValueError):
        return {}


def owned(info):
    pid = info.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        return False
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True)
    return result.returncode == 0 and str(ROOT / 'cd80-data-api/scripts/start_all.py') in result.stdout and str(ROOT) in result.stdout


def healthy(port, path):
    try:
        with OPENER.open(f'http://127.0.0.1:{port}{path}', timeout=2) as response:
            return response.status == 200
    except (OSError, ValueError):
        return False


def stop():
    info = record()
    if not owned(info):
        print('本工程没有可识别的运行进程。')
        return
    os.kill(info['pid'], signal.SIGTERM)
    deadline = time.monotonic() + 25
    while owned(info) and time.monotonic() < deadline:
        time.sleep(.2)
    if owned(info):
        raise RuntimeError('本工程仍在结束活动任务，请查看 .run/server.log；没有强制停止其他进程。')
    PID_FILE.unlink(missing_ok=True)
    print('最终版网站和数据服务已停止。')


def start(website_port, api_port):
    info = record()
    if owned(info):
        print(f"最终版已运行：http://127.0.0.1:{info['websitePort']}/desktop/")
        return
    STATE.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    (ROOT / '.cache/tmp').mkdir(parents=True, exist_ok=True)
    env['TMPDIR'] = str(ROOT / '.cache/tmp')
    command = [str(ROOT / '.venv/bin/python'), '-u', str(ROOT / 'cd80-data-api/scripts/start_all.py'),
               '--website', str(ROOT), '--website-port', str(website_port), '--api-port', str(api_port)]
    with LOG.open('ab') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    info = {'pid': process.pid, 'websitePort': website_port, 'apiPort': api_port, 'root': str(ROOT)}
    PID_FILE.write_text(json.dumps(info, ensure_ascii=False, indent=2))
    deadline = time.monotonic() + 50
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('启动失败，请查看 .run/server.log；常见原因是端口占用或依赖缺失。')
        if healthy(website_port, '/desktop/health') and healthy(api_port, '/health'):
            print(f'最终版：http://127.0.0.1:{website_port}/desktop/')
            print(f'数据服务：http://127.0.0.1:{api_port}/docs')
            return
        time.sleep(.4)
    raise RuntimeError('启动健康检查超时，请查看 .run/server.log。')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['start', 'stop', 'restart', 'status'])
    parser.add_argument('--website-port', type=int, default=8894)
    parser.add_argument('--api-port', type=int, default=8897)
    args = parser.parse_args()
    if args.action in {'stop', 'restart'}:
        stop()
    if args.action in {'start', 'restart'}:
        start(args.website_port, args.api_port)
    if args.action == 'status':
        info = record()
        print(json.dumps({**info, 'ownedProcess': owned(info),
                          'websiteHealthy': healthy(info.get('websitePort', args.website_port), '/desktop/health'),
                          'dataHealthy': healthy(info.get('apiPort', args.api_port), '/health')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
