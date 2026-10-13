"""Trusted bootstrap copied into the WASI guest; never imported on the host.

The output adapter is an ergonomic in-memory file API, NOT a security boundary.
Guest code can introspect/replace it; host-side WASI capabilities and bounded
protocol validation still prevent any host mutation or unbounded result delivery.
"""
import base64
import builtins
import io
import json
import os
import re
import sys

_real_open = builtins.open
_files = {}
_handles = []
_limit = int(sys.argv[1])
_count_limit = int(sys.argv[2])
_name_pattern = re.compile(r"^[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,127}$")


class _Output(io.BytesIO):
    def __init__(self, name, initial=b""):
        self.output_name = name
        super().__init__(initial)

    def write(self, data):
        if max(len(self.getbuffer()), self.tell() + len(data)) > _limit:
            raise OSError("Output file size limit exceeded")
        return super().write(data)

    def seek(self, offset, whence=0):
        if whence == 0 and (offset < 0 or offset > _limit):
            raise OSError("Output seek exceeds limit")
        return super().seek(offset, whence)

    def truncate(self, size=None):
        if (self.tell() if size is None else size) > _limit:
            raise OSError("Output truncate exceeds limit")
        return super().truncate(size)

    def close(self):
        if not self.closed:
            _files[self.output_name] = self.getvalue()
        super().close()


def _open(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
    path = os.fspath(file) if not isinstance(file, int) else file
    if isinstance(path, str) and path.startswith("/output/"):
        name = path[len("/output/"):]
        if not _name_pattern.fullmatch(name):
            raise OSError("Only flat safe output file names are supported")
        if mode not in ("r", "rb", "w", "wb", "a", "ab", "x", "xb", "w+", "wb+", "w+b", "r+", "rb+", "r+b", "a+", "ab+", "a+b"):
            raise ValueError("Unsupported output mode")
        if "x" in mode and name in _files:
            raise FileExistsError(path)
        if "r" in mode and name not in _files:
            raise FileNotFoundError(path)
        if name not in _files and len(_files) >= _count_limit:
            raise OSError("Output file count limit exceeded")
        initial = _files.get(name, b"") if "a" in mode or "r" in mode else b""
        _files.setdefault(name, b"")
        stream = _Output(name, initial)
        if "a" in mode:
            stream.seek(0, 2)
        handle = stream if "b" in mode else io.TextIOWrapper(stream, encoding=encoding or "utf-8", errors=errors, newline=newline)
        _handles.append(handle)
        return handle
    return _real_open(file, mode, buffering, encoding, errors, newline, closefd, opener)


builtins.open = _open
io.open = _open
with _real_open("/program/payload.json", encoding="utf-8") as source:
    _payload = json.load(source)
with _real_open("/program/tool.py", encoding="utf-8") as source:
    _code = source.read()
_namespace = {"__name__": "generated_tool", "__file__": "/program/tool.py"}
# Normal print logging is kept separate from the result protocol. Writes directly
# to FD 1 are still bounded by the host and rejected if they corrupt the protocol.
_protocol_stdout = sys.stdout
sys.stdout = sys.stderr
exec(compile(_code, "/program/tool.py", "exec"), _namespace)
_result = _namespace["run"](_payload)
for _handle in _handles:
    _handle.close()
if sum(map(len, _files.values())) > _limit:
    raise OSError("Total output file size limit exceeded")
_encoded = {name: base64.b64encode(data).decode("ascii") for name, data in _files.items()}
_protocol_stdout.write(json.dumps({"result": _result, "files": _encoded}, ensure_ascii=False, allow_nan=False))
_protocol_stdout.flush()
