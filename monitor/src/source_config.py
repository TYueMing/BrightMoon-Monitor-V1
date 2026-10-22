"""Local source configuration, separate from executable code discovery."""
import ntpath
import os
from pathlib import Path
from urllib.parse import urlsplit

SOURCE_IDS = ("deepseek_harness", "opencode", "codex", "claude", "codeagent")
PLACEHOLDERS = {"codeagent": "CodeAgent"}


def codex_endpoint(value):
    """Only explicit local App Servers; do not send local data to remote URLs."""
    if not isinstance(value, str) or len(value) > 512:
        raise ValueError("Codex App Server 地址格式无效")
    value = value.strip()
    if not value:
        return ""
    try:
        url = urlsplit(value)
        valid = (url.scheme == "ws" and url.hostname in {"127.0.0.1", "localhost", "::1"}
                 and url.port and not url.username and not url.password
                 and url.path in {"", "/"} and not url.query and not url.fragment
                 and not any(c.isspace() or ord(c) < 32 for c in value))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("请填写本机 ws://127.0.0.1:端口 地址，不支持远程地址、凭据或查询参数")
    return value.rstrip("/")


def normalize_sources(value=None, *, strict=False):
    if value is not None and not isinstance(value, dict):
        if strict:
            raise ValueError("监控来源配置必须是对象")
        value = {}
    value = value or {}
    if strict and set(value) - set(SOURCE_IDS):
        raise ValueError("未知监控来源")
    result = {}
    for app_id in SOURCE_IDS:
        # Migrate the old saved Codex paths, but never silently enable monitoring.
        item = value.get(app_id, {})
        if not isinstance(item, dict):
            if strict:
                raise ValueError("监控来源配置格式无效")
            item = {}
        enabled = item.get("enabled", False)
        if app_id == "codex" and "codex" not in value:
            enabled = False
        if not isinstance(enabled, bool):
            if strict:
                raise ValueError("监控来源开关必须为布尔值")
            enabled = False
        # A saved future-provider path is not a promise that its connector exists.
        if app_id in PLACEHOLDERS and enabled:
            if strict:
                raise ValueError(f"{PLACEHOLDERS[app_id]} 适配器尚未接入，当前显示预留入口")
            enabled = False
        record = {"enabled": enabled}
        for field in ("app_path", "data_path"):
            path = item.get(field, "")
            valid = isinstance(path, str) and len(path) <= 2048 and not any(ord(c) < 32 for c in path)
            path = path.strip() if valid else ""
            # Configuration locates data only; never import code or launch this path.
            valid = valid and (not path or ((Path(path).is_absolute() or ntpath.isabs(path) or path.startswith(("~/", "~\\")))
                                           and not path.startswith(("\\\\", "//")) and "://" not in path))
            if not valid:
                if strict:
                    raise ValueError("请填写本机绝对路径（或 ~/ 开头的路径），不支持网络地址")
                path = ""
            record[field] = path
        if app_id == "codex":
            try:
                record["server_url"] = codex_endpoint(item.get("server_url", ""))
            except ValueError:
                if strict:
                    raise
                record["server_url"] = ""
            mode = item.get("connection_mode", "manual" if record["server_url"] else "auto")
            if mode not in ("auto", "logs", "manual"):
                if strict:
                    raise ValueError("请选择有效的 Codex 连接方式")
                mode = "auto"
            record["connection_mode"] = mode
        if app_id == "claude":
            approvals = item.get("allow_approvals", False)
            if not isinstance(approvals, bool):
                if strict:
                    raise ValueError("Claude 授权桥开关必须为布尔值")
                approvals = False
            record["allow_approvals"] = approvals
        result[app_id] = record
    return result


def codex_session_root(config):
    path = str(config.get("data_path") or "").strip()
    if not path:
        return Path(os.environ.get("CODEX_HOME") or Path.home()/".codex").expanduser()/"sessions"
    root = Path(path).expanduser()
    return root/"sessions" if (root/"sessions").is_dir() or root.name == ".codex" else root


def deepseek_session_root(config):
    """An explicit log root wins; empty means the upstream DSH_HOME convention."""
    path = str(config.get("data_path") or "").strip()
    if not path:
        base = str(os.environ.get("DSH_HOME") or "").strip()
        return (Path(base).expanduser() if base else Path.home()/".dsh")/"sessions"
    candidate = Path(path).expanduser()
    # Accept Harness home as a convenience, as well as an explicit sessions root.
    return candidate/"sessions" if (candidate/"sessions").is_dir() else candidate


def claude_event_root(config):
    """Return SOLO's Claude Code event mailbox, never the conversation database.

    The adapter intentionally consumes a dedicated SQLite mailbox written by our
    hook.  Claude's transcript files contain private conversation content and
    vary by desktop/remote environment, so they are not treated as a monitor
    transport.
    """
    path = str(config.get("data_path") or "").strip()
    if path:
        return Path(path).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "MonitorSolo" / "data" / "claude"


def inspect_source(app_id, config):
    """Read-only diagnostics. Never scan credentials or execute a configured binary."""
    if app_id not in SOURCE_IDS:
        raise ValueError("未知监控来源")
    normalized = normalize_sources({app_id: config}, strict=True)[app_id]
    if app_id in PLACEHOLDERS:
        return {"status": "unsupported", "message": f"路径可保存；{PLACEHOLDERS[app_id]} 监控尚未接入。"}
    if app_id == "claude":
        from ..claude_monitor.adapter import inspect
        return inspect(normalized)
    if app_id == "opencode":
        from ..opencode_monitor.adapter import inspect
        return inspect(normalized)
    if app_id == "codex":
        root = codex_session_root(normalized)
        try:
            readable = root.is_dir()
            if readable:
                next(root.iterdir(), None)
        except OSError:
            return {"status": "unreadable", "message": "Codex 会话目录无法读取。"}
        if normalized["connection_mode"] == "manual" and normalized["server_url"]:
            try:
                import websocket  # noqa: F401
            except ImportError:
                return {"status": "dependency_missing", "message": "请安装 websocket-client 运行依赖以连接 Codex App Server。"}
            return {"status": "configured", "data_path": str(root), "read_only": False,
                    "message": "本机地址格式有效；启用后连接 App Server，连接结果会显示在灵动岛。仅同一服务上的任务支持审批。"}
        if normalized["connection_mode"] == "manual":
            return {"status": "missing", "message": "手动连接需要填写本机 App Server 地址。"}
        if normalized["connection_mode"] == "auto":
            from ..codex_monitor.discovery import ServerDiscovery
            discovery = ServerDiscovery()
            listeners = discovery.poll()
            message = (f"发现 {len(listeners)} 个本机实时服务，开启后自动连接；连接成功后才能审批。" if listeners else
                       "自动监控已就绪，无需终端命令。直接在 Codex 中开始任务即可；桌面未开放实时连接时，审批在 Codex 中完成。")
            if not readable:
                message += " 当前还没有会话目录，运行 Codex 后会自动重试；自定义目录可在高级选项中指定。"
            if discovery.message:
                message += " " + discovery.message
            return {"status": "ready" if readable or listeners else "waiting", "data_path": str(root),
                    "read_only": True, "message": message, "detected_servers": len(listeners)}
        return {"status": "ready" if readable else "waiting", "data_path": str(root), "read_only": True,
                "message": "本机会话监控已就绪，审批请在 Codex 中处理。" if readable else
                           "等待 Codex 创建会话目录，将自动重试。"}
    root = deepseek_session_root(normalized)
    if not root.is_dir():
        return {"status": "missing", "message": "未找到会话目录。请选择 Harness 的 sessions 目录，或其 .dsh 数据目录。",
                "data_path": str(root)}
    try:
        next(root.iterdir(), None)
    except OSError:
        return {"status": "unreadable", "message": "会话目录无法读取。"}
    try:
        import zstandard  # noqa: F401 -- presence check, no data decoding here
    except ImportError:
        return {"status": "dependency_missing", "message": "缺少 zstandard；请安装项目运行依赖以读取 Harness 默认压缩日志。"}
    return {"status": "ready", "message": "目录可读取。启用后监控已落盘的会话事件；授权需在 DeepSeek Harness 中处理。",
            "data_path": str(root), "read_only": True}
