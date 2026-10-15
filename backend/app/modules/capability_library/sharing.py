"""Conservative checks for explicitly reviewed portable source packages.

This rejects suspicious content; it never edits source or transfers credential
stores. A clean scan is not a claim that arbitrary prose contains no secrets.
"""
import ast
import io
import json
import re
import tomllib
import tokenize
from pathlib import PurePosixPath
from urllib.parse import parse_qsl, urlsplit

import yaml

from .contracts import fail
from .packages import has_secret, safe_path

_SECRET_NAME = re.compile(r'(?i)^(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|password|passwd|secret|client[_-]?secret|cookie|credential(?:s)?)$')
_URL_SECRET_NAME = re.compile(r'(?i)^(?:key|auth|bearer|session|signature|sig|x-amz-signature|x-amz-credential|x-amz-security-token)$')
_ASSIGNMENT = re.compile(r'''(?ix)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|password|passwd|client[_-]?secret|secret|cookie)\s*["']?\s*[:=]\s*["']([^"'\r\n]+)["']''')
_TOKEN = re.compile(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16})\b')
_PRIVATE_RESOURCE = re.compile(r'(?i)(?:/desktop/api/(?:files|sub-agent-artifacts|agent-artifacts|capabilities/downloads)/|\bfile://|\b(?:project-file|library-file):|\b[A-Z]:[\\/]|\\\\[A-Za-z0-9_.-]+[\\/]|/(?:Users|home)/[^\s/]+/)')
_URL = re.compile(r'https?://[^\s<>"\x27]+', re.I)
_TEXT_SUFFIXES = {'.py', '.md', '.txt', '.json', '.csv', '.tsv', '.yaml', '.yml', '.toml', '.rst', '.sql', '.html', '.css', '.xml', '.svg'}


def _placeholder(value):
    text = str(value).strip()
    return value is None or not text or text in {'none', 'null', 'true', 'false'} or bool(re.fullmatch(r'(?:<[^<>]+>|\$\{[A-Z0-9_]+\}|YOUR_[A-Z0-9_]+|REPLACE_[A-Z0-9_]+)', text))


def _check_private_paths(text):
    if _PRIVATE_RESOURCE.search(text):
        fail('SHARING_PRIVATE_RESOURCE', '共享内容包含私人文件、运行工件或本机路径，请改为可复用参数。', 403)


def _check_python_paths(text):
    """Inspect decoded literals and comments, not escaped PNG bytes as UNC paths."""
    total = 0

    def inspect(source, depth):
        nonlocal total
        total += len(source.encode('utf-8', errors='surrogatepass'))
        if depth > 8 or total > 4 * 1024 * 1024:
            fail('SHARING_UNREVIEWABLE_FILE', '嵌入Python源码超过可审阅的深度或大小，请拆分文件。', 413)
        try:
            tree = ast.parse(source)
            tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
        except (SyntaxError, ValueError, UnicodeError, RecursionError, tokenize.TokenError, IndentationError):
            # Malformed source does not gain an exemption from conservative checks.
            _check_private_paths(source)
            return
        lines = source.splitlines(keepends=True)
        offsets, position = [], 0
        for line in lines:
            offsets.append(position)
            position += len(line)
        remaining = list(source)
        for token in tokens:
            if token.type != tokenize.STRING:
                continue
            start = offsets[token.start[0] - 1] + token.start[1]
            end = offsets[token.end[0] - 1] + token.end[1]
            remaining[start:end] = ['\n' if char == '\n' else ' ' for char in source[start:end]]
        # Comments and all non-literal text still undergo the original path rule.
        _check_private_paths(''.join(remaining))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant):
                continue
            if isinstance(node.value, str):
                inspect(node.value, depth + 1)
            elif isinstance(node.value, bytes):
                _check_private_paths(node.value.decode('utf-8', errors='replace'))

    inspect(text, 0)


def _check_text(text, *, private_paths=True):
    if has_secret({'text': text.encode('utf-8')}) or _TOKEN.search(text):
        fail('SHARING_CONTAINS_SECRET', '共享内容疑似包含凭据，请删除实际秘密并重新保存版本。', 403)
    for match in _ASSIGNMENT.finditer(text):
        if not _placeholder(match.group(1)):
            fail('SHARING_CONTAINS_SECRET', '共享内容包含凭据赋值，请改为独立连接配置。', 403)
    if private_paths:
        _check_private_paths(text)
    for match in _URL.finditer(text):
        try:
            url = urlsplit(match.group(0))
            parameters = parse_qsl(url.query) + parse_qsl(url.fragment)
            if url.username is not None or url.password is not None or any((_SECRET_NAME.fullmatch(k) or _URL_SECRET_NAME.fullmatch(k)) and not _placeholder(v) for k, v in parameters):
                fail('SHARING_CONTAINS_SECRET', '共享地址含认证信息，请通过独立连接配置。', 403)
        except ValueError:
            fail('SHARING_CONTAINS_SECRET', '共享内容包含无法安全检查的地址。', 403)


def _check_values(value, depth=0, seen=None):
    if depth > 40:
        fail('INVALID_PACKAGE', '共享内容嵌套过深。')
    seen = set() if seen is None else seen
    if isinstance(value, (dict, list)):
        if id(value) in seen:
            return
        seen.add(id(value))
    if isinstance(value, dict):
        for key, child in value.items():
            if _SECRET_NAME.fullmatch(str(key)) and not isinstance(child, (dict, list, bool)) and not _placeholder(child):
                fail('SHARING_CONTAINS_SECRET', '共享内容包含凭据字段，请改为独立连接配置。', 403)
            _check_values(child, depth + 1, seen)
    elif isinstance(value, list):
        for child in value:
            _check_values(child, depth + 1, seen)
    elif isinstance(value, str):
        _check_text(value)


def assert_shareable(metadata, files=None):
    """Reject secrets/private references across metadata and declared UTF-8 files."""
    _check_values(metadata)
    for path, data in (files or {}).items():
        safe_path(path)
        parts = [part.casefold() for part in PurePosixPath(path).parts]
        if any(part == '.env' or part.startswith('.env.') or part in {'.aws', '.ssh', 'credentials', 'credentials.json', 'credential.json', 'secrets.json', 'id_rsa', 'id_ed25519'} for part in parts) or PurePosixPath(path).suffix.casefold() in {'.pem', '.key', '.p12', '.pfx', '.db', '.sqlite', '.sqlite3'}:
            fail('SHARING_CONTAINS_SECRET', '共享包包含凭据或私人数据库文件，请移除后重新保存。', 403)
        if PurePosixPath(path).suffix.casefold() not in _TEXT_SUFFIXES:
            fail('SHARING_UNREVIEWABLE_FILE', '社区只接受可审阅的文本源码与示例，请移除二进制或未知资源后重新保存。', 403)
        try:
            text = data.decode('utf-8')
        except (UnicodeError, AttributeError):
            fail('SHARING_UNREVIEWABLE_FILE', '共享资源必须是UTF-8文本，请移除二进制资源后重新保存。', 403)
        if '\x00' in text:
            fail('SHARING_UNREVIEWABLE_FILE', '共享资源包含二进制内容，请移除后重新保存。', 403)
        suffix = PurePosixPath(path).suffix.casefold()
        _check_text(text, private_paths=suffix != '.py')
        if suffix == '.py':
            _check_python_paths(text)
        if suffix in {'.json', '.yaml', '.yml', '.toml'}:
            try:
                if suffix == '.json':
                    _check_values(json.loads(text))
                elif suffix == '.toml':
                    _check_values(tomllib.loads(text))
                else:
                    for document in yaml.safe_load_all(text):
                        _check_values(document)
            except (ValueError, RecursionError, yaml.YAMLError):
                fail('INVALID_PACKAGE', '共享JSON、YAML或TOML资源无效。')
