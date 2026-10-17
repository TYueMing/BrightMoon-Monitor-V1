"""Incrementally read committed v3/v4 JSONL or checksummed Zstandard frames.

Never acquires a writer lease, repairs a torn tail, migrates or changes a log.
Only complete frames and newline-terminated records are exposed to projection.
"""
import io
import json
from pathlib import Path

MAX_FRAME = 32 * 1024 * 1024
READ_BYTES = 1024 * 1024
DECODE_BYTES = 4 * 1024 * 1024


class LogFormatError(ValueError):
    pass


def frame_length(data):
    """Return the first full standard Zstd frame length, or None for a torn tail."""
    if len(data) < 5:
        return None
    if data[:4] != b"\x28\xb5\x2f\xfd":
        raise LogFormatError("Zstandard 文件头无效")
    descriptor = data[4]
    if descriptor & 0x18:
        raise LogFormatError("Zstandard 帧头保留位无效")
    flag, single = descriptor >> 6, bool(descriptor & 0x20)
    dictionary = (0, 1, 2, 4)[descriptor & 3]
    content_size = (1 if single else 0) if flag == 0 else 1 << flag
    offset = 5 + (0 if single else 1) + dictionary + content_size
    if len(data) < offset:
        return None
    while True:
        if len(data) < offset + 3:
            return None
        block = int.from_bytes(data[offset:offset+3], "little")
        kind = (block >> 1) & 3
        if kind == 3:
            raise LogFormatError("Zstandard 数据块类型无效")
        offset += 3 + (1 if kind == 1 else block >> 3)
        if offset > MAX_FRAME:
            raise LogFormatError("单个压缩帧超出监控读取上限")
        if len(data) < offset:
            return None
        if block & 1:
            break
    offset += 4 if descriptor & 4 else 0
    return offset if len(data) >= offset else None


class SessionLogReader:
    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.buffer = b""
        self.header = None
        self.identity = None
        self.seq = None
        self.generation = 0
        self.decode_backlog = False

    def poll(self):
        stat = self.path.stat()
        identity = (stat.st_dev, stat.st_ino)
        reset = self.identity is not None and (identity != self.identity or stat.st_size < self.offset)
        if reset:
            self.offset, self.buffer, self.header, self.seq = 0, b"", None, None
            self.decode_backlog = False
            self.generation += 1
        self.identity = identity
        with self.path.open("rb") as stream:
            stream.seek(self.offset)
            # Drain complete buffered frames before reading ahead. Otherwise a
            # highly compressed backlog could grow even with bounded decoding.
            block = b"" if self.decode_backlog else stream.read(READ_BYTES)
        self.offset += len(block)
        self.buffer += block
        chunks = []
        decoded_bytes = 0
        budget_exhausted = False
        if self.path.suffix == ".zstd":
            try:
                import zstandard as zstd
            except ImportError:
                raise LogFormatError("缺少 zstandard 运行依赖") from None
            while (length := frame_length(self.buffer)) is not None:
                frame = self.buffer[:length]
                try:
                    # Read with a cap even when the frame doesn't declare its size.
                    with zstd.ZstdDecompressor(max_window_size=MAX_FRAME).stream_reader(io.BytesIO(frame)) as reader:
                        decoded = reader.read(MAX_FRAME+1)
                        if len(decoded) > MAX_FRAME:
                            raise LogFormatError("解压后的帧超出监控读取上限")
                except zstd.ZstdError:
                    raise LogFormatError("压缩会话帧校验失败") from None
                if not decoded.endswith(b"\n"):
                    raise LogFormatError("已提交压缩帧缺少记录边界")
                chunks.append(decoded)
                decoded_bytes += len(decoded)
                self.buffer = self.buffer[length:]
                if decoded_bytes >= DECODE_BYTES and self.buffer:
                    budget_exhausted = True
                    break
        else:
            end = self.buffer.rfind(b"\n") + 1
            if end:
                chunks.append(self.buffer[:end])
                self.buffer = self.buffer[end:]
        if len(self.buffer) > MAX_FRAME:
            raise LogFormatError("未完成会话记录超出监控读取上限")
        events = []
        for chunk in chunks:
            for line in chunk.splitlines():
                try:
                    row = json.loads(line)
                except (UnicodeDecodeError, ValueError):
                    raise LogFormatError("会话记录不是有效 UTF-8 JSON") from None
                if not isinstance(row, dict):
                    raise LogFormatError("会话记录格式无效")
                if self.header is None:
                    if row.get("type") != "session" or row.get("version") not in (3, 4) or not isinstance(row.get("id"), str):
                        raise LogFormatError("仅支持 Harness 会话格式 v3/v4；请核对应用版本")
                    if f"session.v{row['version']}.jsonl" not in self.path.name:
                        raise LogFormatError("会话版本与文件名不一致")
                    self.header = row
                else:
                    if not isinstance(row.get("type"), str) or (not isinstance(row.get("data"), dict)
                                                              and not row["type"].startswith("plugin:")):
                        raise LogFormatError("会话事件缺少 type/data")
                    seq = row.get("seq")
                    if not isinstance(seq, int) or isinstance(seq, bool) or (self.seq is not None and seq != self.seq+1):
                        raise LogFormatError("会话事件序号不连续")
                    self.seq = seq
                    # Experimental plugin payloads are not a known agent phase.
                    if isinstance(row.get("data"), dict):
                        events.append(row)
        self.decode_backlog = budget_exhausted
        return reset, events, self.offset >= stat.st_size and not budget_exhausted
