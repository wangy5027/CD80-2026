"""Bounded Streamable HTTP client with DNS pinning and explicit trust policy.

Protocol: https://modelcontextprotocol.io/specification/2025-11-25/basic/transports
No retries, redirect following, proxy environment, stdio, or server-side requests.
"""
import ctypes
import binascii
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import stat
import time
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import canonical, fail, uid


_MAC_KEY_NAME = '.capability-mcp-key-v1'
_MAC_TOKEN_PREFIX = b'CD80F1:'


def _instance_key(data_dir, *, create):
    """Read an owner-only instance key, never replacing a damaged or lost one."""
    if not data_dir:
        fail('CREDENTIAL_STORE_UNAVAILABLE', '凭据实例目录未配置。', 503)
    directory = Path(data_dir)
    if not directory.is_dir() or directory.is_symlink():
        fail('CREDENTIAL_STORE_UNAVAILABLE', '凭据实例目录不可用。', 503)
    path = directory / _MAC_KEY_NAME
    flags = os.O_CLOEXEC | os.O_NOFOLLOW
    if create:
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | flags, 0o600)
        except FileExistsError:
            pass
        except OSError:
            fail('CREDENTIAL_STORE_UNAVAILABLE', '无法创建实例凭据密钥。', 503)
        else:
            try:
                from cryptography.fernet import Fernet
                key = Fernet.generate_key()
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, 'wb', closefd=False) as stream:
                    stream.write(key)
                    stream.flush()
                    os.fsync(descriptor)
            except OSError:
                fail('CREDENTIAL_STORE_UNAVAILABLE', '无法保存实例凭据密钥。', 503)
            finally:
                os.close(descriptor)
    try:
        descriptor = os.open(path, os.O_RDONLY | flags)
    except OSError:
        fail('CREDENTIAL_STORE_UNAVAILABLE', '实例凭据密钥不存在或不可读取。', 503)
    try:
        details = os.fstat(descriptor)
        if (not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid()
                or details.st_mode & 0o077 or details.st_nlink != 1 or details.st_size != 44):
            fail('CREDENTIAL_STORE_UNAVAILABLE', '实例凭据密钥归属、权限或格式不正确。', 503)
        key = os.read(descriptor, 45)
        from cryptography.fernet import Fernet
        try:
            Fernet(key)
        except (TypeError, ValueError, binascii.Error):
            fail('CREDENTIAL_STORE_UNAVAILABLE', '实例凭据密钥格式不正确。', 503)
        return key
    except OSError:
        fail('CREDENTIAL_STORE_UNAVAILABLE', '无法读取实例凭据密钥。', 503)
    finally:
        os.close(descriptor)


def protect_secret(raw, decrypt=False, *, data_dir=None, create_key=True):
    if not isinstance(raw, bytes):
        fail('INVALID_REQUEST', '凭据输入格式无效。')
    if os.name != 'nt':
        from cryptography.fernet import Fernet, InvalidToken
        if decrypt and not raw.startswith(_MAC_TOKEN_PREFIX):
            fail('CREDENTIAL_STORE_UNAVAILABLE', '凭据密文版本不受支持或已损坏。', 503)
        key = _instance_key(data_dir, create=not decrypt and create_key)
        cipher = Fernet(key)
        if decrypt:
            try:
                return cipher.decrypt(raw[len(_MAC_TOKEN_PREFIX):])
            except (InvalidToken, ValueError):
                fail('CREDENTIAL_STORE_UNAVAILABLE', '凭据无法解密；密钥或密文可能已损坏。', 503)
        return _MAC_TOKEN_PREFIX + cipher.encrypt(raw)
    class Blob(ctypes.Structure):
        _fields_ = [('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL('crypt32',use_last_error=True)
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ulong,ctypes.POINTER(Blob)]
    function.restype = ctypes.c_int
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(result)):
        fail('CREDENTIAL_STORE_UNAVAILABLE','凭据无法解密或保存，请用服务运行账户重新配置。',503)
    try:
        return ctypes.string_at(result.data,result.size)
    finally:
        kernel.LocalFree(ctypes.cast(result.data,ctypes.c_void_p))


def endpoint(value, testing=False):
    try:
        parsed = urlsplit(value)
        local = testing and parsed.hostname in {'127.0.0.1','localhost'}
        if parsed.scheme!='https' and not (local and parsed.scheme=='http'):
            raise ValueError()
        if not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port==0:
            raise ValueError()
    except (TypeError,ValueError):
        fail('INVALID_REQUEST','MCP 地址必须是无凭据、查询参数或片段的 HTTPS 地址。')
    return parsed


class Client:
    def __init__(self, connection, secret='', testing=False, stopped=None):
        self.connection = connection
        self.url = endpoint(connection['endpoint'],testing)
        self.testing = testing
        self.stopped = stopped
        self.deadline = time.monotonic()+30
        self.session = None
        self.protocol = None
        self.secret = secret

    def rpc(self, method, params=None, notification=False):
        if self.connection['authMode']=='api_key':
            key=self.connection.get('headerName','')
            if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',key) or key.lower() in {'host','connection','content-length','transfer-encoding','cookie'}:
                fail('INVALID_REQUEST','认证请求头不受支持。')
        if self.stopped and self.stopped.is_set():
            fail('CANCELLED','操作已取消。',409)
        remaining = self.deadline-time.monotonic()
        if remaining<=0:
            fail('EXECUTION_TIMEOUT','MCP 响应超时；未自动重试。',504)
        host = self.url.hostname
        port = self.url.port or (443 if self.url.scheme=='https' else 80)
        try:
            addresses = socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
            if not addresses:
                raise OSError()
            for item in addresses:
                ip = ipaddress.ip_address(item[4][0])
                if not ip.is_global and not (self.testing and ip.is_loopback):
                    fail('CAPABILITY_BLOCKED','MCP 地址指向不允许访问的网络。',403)
            # Dial the checked numeric address, then verify TLS for the original
            # hostname. No second DNS lookup can redirect credentials elsewhere.
            sock = socket.create_connection(addresses[0][4][:2],timeout=min(10,remaining))
            if self.url.scheme=='https':
                try:
                    sock = ssl.create_default_context().wrap_socket(sock,server_hostname=host)
                except BaseException:
                    sock.close()
                    raise
            conn = http.client.HTTPConnection(host,port,timeout=min(10,remaining))
            conn.sock = sock
            def interrupt_socket():
                try: sock.shutdown(socket.SHUT_RDWR)
                except OSError: pass
                try: sock.close()
                except OSError: pass
            watchdog=threading.Timer(max(.01,self.deadline-time.monotonic()),interrupt_socket)
            watchdog.daemon=True
            watchdog.start()
            request_id = uid('rpc')
            body = {'jsonrpc':'2.0','method':method}
            if not notification: body['id'] = request_id
            if params is not None: body['params'] = params
            headers = {'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
            if self.protocol: headers['MCP-Protocol-Version'] = self.protocol
            if self.session: headers['MCP-Session-Id'] = self.session
            if self.secret:
                key = 'Authorization' if self.connection['authMode']=='bearer' else self.connection.get('headerName','')
                if not re.fullmatch(r'[A-Za-z][A-Za-z0-9-]{0,63}',key) or key.lower() in {'host','content-length','connection','transfer-encoding','cookie'}:
                    fail('INVALID_REQUEST','认证请求头不受支持。')
                headers[key] = ('Bearer ' if self.connection['authMode']=='bearer' else '')+self.secret
            try:
                conn.request('POST',self.url.path or '/',body=canonical(body).encode(),headers=headers)
                response = conn.getresponse()
                if notification and response.status==202: return {}
                if response.status!=200:
                    fail('UPSTREAM_FAILED','MCP 请求失败；不跟随重定向或自动重试。',502)
                session = response.getheader('Mcp-Session-Id')
                if session:
                    if not re.fullmatch(r'[\x21-\x7e]{1,256}',session):
                        fail('UPSTREAM_FAILED','MCP 会话标识无效。',502)
                    self.session = session
                content_type = response.getheader('Content-Type','')
                if 'text/event-stream' in content_type:
                    size, data, result = 0, [], None
                    while time.monotonic()<self.deadline:
                        if self.stopped and self.stopped.is_set():
                            fail('CANCELLED','请求已发出，远端结果未知。',409)
                        line = response.readline(512*1024+1)
                        size += len(line)
                        if size>512*1024: fail('UPSTREAM_FAILED','MCP 结果超过限制。',502)
                        if not line: break
                        if line.strip()==b'':
                            if data:
                                item = json.loads(b'\n'.join(data))
                                data=[]
                                if item.get('id')==request_id and 'method' not in item:
                                    result=item
                                    break
                                if 'id' in item: fail('UNSUPPORTED_EXECUTION','不支持服务器发起的执行请求。',422)
                        elif line.startswith(b'data:'): data.append(line[5:].strip())
                    if result is None: fail('UPSTREAM_FAILED','MCP 连接结束，结果未知；未自动重试。',502)
                elif 'application/json' in content_type:
                    raw = response.read(512*1024+1)
                    if len(raw)>512*1024: fail('UPSTREAM_FAILED','MCP 结果超过限制。',502)
                    result = json.loads(raw)
                else: fail('UPSTREAM_FAILED','MCP 返回不支持的内容格式。',502)
                if not isinstance(result,dict) or result.get('id')!=request_id or result.get('jsonrpc')!='2.0' or 'error' in result or not isinstance(result.get('result'),dict):
                    fail('UPSTREAM_FAILED','MCP 返回错误或无效协议消息。',502)
                if self.secret and self.secret in canonical(result):
                    fail('UPSTREAM_FAILED','远端结果含私密凭据，已阻止保存和传给模型。',502)
                return result['result']
            finally:
                watchdog.cancel()
                conn.close()
        except (OSError,ValueError,http.client.HTTPException):
            fail('UPSTREAM_FAILED','MCP 连接失败或结果无法确认；未自动重试。',502)

    def initialize(self):
        result = self.rpc('initialize',{'protocolVersion':'2025-11-25','capabilities':{},'clientInfo':{'name':'cd80-capabilities','version':'1.0'}})
        self.protocol = result.get('protocolVersion')
        if self.protocol not in {'2025-11-25','2025-06-18','2025-03-26'}:
            fail('UNSUPPORTED_EXECUTION','MCP 协议版本不受支持。',422)
        self.rpc('notifications/initialized',notification=True)

    def tools(self):
        result, cursor = [], None
        for _ in range(8):
            page = self.rpc('tools/list',{'cursor':cursor} if cursor else {})
            items = page.get('tools')
            if not isinstance(items,list) or any(not isinstance(item,dict) or not isinstance(item.get('name'),str) for item in items):
                fail('UPSTREAM_FAILED','MCP 工具目录无效。',502)
            result.extend(items)
            if len(result)>128: fail('LIMIT_EXCEEDED','MCP 工具目录超过 128 项。',413)
            cursor = page.get('nextCursor')
            if not cursor: return result
        fail('LIMIT_EXCEEDED','MCP 目录分页超过限制。',413)
