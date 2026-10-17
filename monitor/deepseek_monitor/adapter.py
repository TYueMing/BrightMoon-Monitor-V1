"""Durable progress plus live Harness interactions, polled off the Tk thread."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import re
import time

from ..src.source_config import deepseek_session_root
from .log_reader import SessionLogReader, LogFormatError
from .projection import SessionProjection
from .bridge_client import BridgeClient
from .interactions import merge_interactions

CANONICAL = re.compile(r"session(?:\.v([1-9][0-9]*))?\.jsonl(?:\.zstd)?$")
MAX_SESSIONS = 128


def discover_logs(root):
    """Two upstream-owned directory levels; do not follow links outside the root."""
    root = root.resolve()
    result = []
    scanned = 0
    for project in root.iterdir():
        if not project.is_dir() or not project.resolve().is_relative_to(root):
            continue
        for session in project.iterdir():
            scanned += 1
            if scanned > 4096:
                raise LogFormatError("会话目录超过 4096 个，请选择更小的会话数据目录")
            if not session.is_dir() or not session.resolve().is_relative_to(root):
                continue
            generations = []
            for path in session.iterdir():
                match = CANONICAL.fullmatch(path.name)
                if match and path.is_file() and path.resolve().is_relative_to(root):
                    generations.append((int(match[1] or 0), path))
            if generations:
                version = max(v for v, _ in generations)
                selected = [p for v, p in generations if v == version]
                if len(selected) != 1:
                    raise LogFormatError("同一会话存在混合压缩格式，请核对 Harness 数据目录")
                result.append((selected[0].stat().st_mtime_ns, selected[0]))
    return [p for _, p in sorted(result, reverse=True)[:MAX_SESSIONS]], len(result)


class DeepSeekSource:
    app_id = "deepseek_harness"

    def __init__(self, config):
        self.root = deepseek_session_root(config).resolve()
        self.readers = {}
        self.projections = {}
        self.baselined = set()
        self.observed_runs = set()
        self.failed = {}
        self.published = {}
        self.started_at = time.time()
        self.bridge = BridgeClient(self.root)
        self._interaction_identities = {}

    def _last_known(self, path, *, error=False):
        value = deepcopy(self.published.get(path))
        if value:
            value.update(connection_error=error, monitor_syncing=not error,
                         message="会话读取失败，显示最后已知状态" if error else "正在同步后续会话事件…")
        return value

    def _unavailable(self, status, message):
        return {"runs": [self._last_known(p, error=True) for p in self.published], "actions": [],
                "status": status, "message": message, "read_only": True}

    def poll(self):
        # SourceHub already runs this on its reader thread. The source-specific
        # panel submits through this client's validated capability registry;
        # no changes to the shared Hub, mailbox or BrightMoon actions are needed.
        return merge_interactions(self._poll_logs(), self.bridge.poll(), self.root, self._interaction_identities)

    def _poll_logs(self):
        if not self.root.is_dir():
            return self._unavailable("missing", "未找到 DeepSeek 会话目录")
        try:
            paths, total = discover_logs(self.root)
        except (OSError, ValueError) as exc:
            return self._unavailable("error", str(exc)[:150] if isinstance(exc, LogFormatError) else "DeepSeek 会话目录无法读取")
        keep = set(paths)
        for mapping in (self.readers, self.projections, self.failed, self.published):
            for path in set(mapping)-keep:
                mapping.pop(path, None)
        self.baselined.intersection_update(keep)
        errors, runs = [], []
        for path in paths:
            signature = None
            try:
                signature = (path.stat().st_size, path.stat().st_mtime_ns)
                failure = self.failed.get(path)
                if failure and failure[0] == signature and time.monotonic() < failure[2]:
                    errors.append(self.failed[path][1])
                    if cached := self._last_known(path, error=True):
                        runs.append(cached)
                    continue
                if path in self.failed:
                    self.readers.pop(path, None)
                    self.projections.pop(path, None)
                    self.failed.pop(path, None)
                reader = self.readers.setdefault(path, SessionLogReader(path))
                reset, events, caught_up = reader.poll()
                if reset:
                    self.projections.pop(path, None)
                if reader.header and path not in self.projections:
                    self.projections[path] = SessionProjection(reader.header, str(path.parent))
                projection = self.projections.get(path)
                if projection:
                    for event in events:
                        projection.apply(event)
                    run = projection.snapshot()
                    if run and caught_up:
                        historical = path not in self.baselined and bool(run.get("ended_at"))
                        if historical:
                            run["historical"] = True
                        if path in self.baselined and events:
                            self.observed_runs.add(run["run_id"])
                        if not run.get("ended_at") or datetime.fromisoformat(run["ended_at"]).timestamp() >= self.started_at:
                            self.observed_runs.add(run["run_id"])
                        # Baseline completion history doesn't flood the capsule or
                        # replay completion sounds when a source is first enabled.
                        if not run.get("ended_at") or run["run_id"] in self.observed_runs:
                            runs.append(run)
                            self.published[path] = deepcopy(run)
                        else:
                            self.published.pop(path, None)
                    elif not caught_up and (cached := self._last_known(path)):
                        # Large appends must not remove an existing task while
                        # the bounded reader catches up in subsequent polls.
                        runs.append(cached)
                    elif caught_up and not run:
                        self.published.pop(path, None)
                if caught_up:
                    self.baselined.add(path)
            except (OSError, ValueError, TypeError, OverflowError) as exc:
                message = str(exc)[:150] if isinstance(exc, LogFormatError) else "一个 DeepSeek 会话暂不可读取"
                errors.append(message)
                # A Windows writer may briefly deny read sharing without
                # changing size/mtime. Retry even an unchanged file after a
                # short backoff instead of keeping that error forever.
                self.failed[path] = (signature, message, time.monotonic()+5)
                if cached := self._last_known(path, error=True):
                    runs.append(cached)
        active_ids = {p.run["run_id"] for p in self.projections.values() if p.run}
        self.observed_runs.intersection_update(active_ids)
        return {"runs": runs, "actions": [], "status": "degraded" if errors or total > MAX_SESSIONS else "ready",
                "message": errors[0] if errors else f"只读监控 {min(total, MAX_SESSIONS)} 个会话；等待已落盘事件",
                "session_count": total, "read_only": True}

    def send(self, action, run_id, payload):
        raise ValueError("DeepSeek 日志监控为只读，请在 DeepSeek Harness 中处理授权或停止")
