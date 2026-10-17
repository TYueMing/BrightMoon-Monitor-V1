"""Bounded, incremental UTF-8 rollout tailing; never open auth/config files."""
import json
import os
from pathlib import Path

READ_BYTES = 512 * 1024
TAIL_BYTES = 2 * 1024 * 1024
MAX_LINE = 1024 * 1024


def discover(root, limit=32):
    root = Path(root).resolve()
    found, stack, scanned = [], [(root, 0)], 0
    while stack:
        directory, depth = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                scanned += 1
                if scanned > 16000:
                    # Do not return arbitrary old entries if the source is too large.
                    raise ValueError("Codex 会话目录过大，请选择最近日期的子目录")
                path = Path(entry.path)
                if entry.is_symlink() or not path.resolve().is_relative_to(root):
                    continue
                if entry.is_dir(follow_symlinks=False) and depth < 3:
                    stack.append((path, depth + 1))
                elif entry.is_file(follow_symlinks=False) and path.name.startswith("rollout-") and path.suffix == ".jsonl":
                    found.append((entry.stat().st_mtime_ns, str(path)))
    return [Path(p) for _, p in sorted(found, reverse=True)[:limit]]


class RolloutReader:
    def __init__(self, path, root):
        self.path, self.root = Path(path), Path(root).resolve()
        self.offset, self.identity, self.pending = 0, None, b""
        self.dropping = False
        self.errors = 0
        self.prefix = b""

    def _decode(self, line):
        try:
            value = json.loads(line.decode("utf-8"))
            return value if isinstance(value, dict) else None
        except (UnicodeError, ValueError, RecursionError):
            self.errors += 1
            return None

    def poll(self):
        if not self.path.resolve().is_relative_to(self.root) or self.path.is_symlink():
            raise ValueError("Codex 日志路径超出数据目录")
        records = []
        with self.path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            identity = (stat.st_dev, stat.st_ino)
            prefix = stream.read(128)
            reset = (self.identity != identity or stat.st_size < self.offset
                     or (self.prefix and not prefix.startswith(self.prefix)))
            if reset:
                self.identity, self.pending, self.dropping = identity, b"", False
                self.offset = max(0, stat.st_size-TAIL_BYTES)
                if self.offset:
                    stream.seek(0)
                    header = stream.readline(MAX_LINE+1)
                    if len(header) <= MAX_LINE:
                        value = self._decode(header)
                        if value and value.get("type") == "session_meta":
                            records.append(value)
                    # Start only at a whole record; do not decode a UTF-8 tail.
                    stream.seek(self.offset)
                    stream.readline(MAX_LINE+1)
                    self.offset = stream.tell()
            self.prefix = prefix
            stream.seek(self.offset)
            data = stream.read(READ_BYTES)
            self.offset = stream.tell()
        chunks = (self.pending+data).split(b"\n")
        self.pending = chunks.pop()
        for line in chunks:
            if self.dropping:
                self.dropping = False
                continue
            if len(line) > MAX_LINE:
                self.errors += 1
                continue
            value = self._decode(line)
            if value:
                records.append(value)
        if len(self.pending) > MAX_LINE:
            self.pending, self.dropping = b"", True
            self.errors += 1
        return reset, records, self.offset >= stat.st_size
