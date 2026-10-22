"""Trusted app identities; adapters reuse the island without supplying file paths."""
from dataclasses import dataclass
from pathlib import Path

ICON_ROOT = Path(__file__).resolve().parents[1] / "icon_asset"


@dataclass(frozen=True)
class AppSource:
    app_id: str
    name: str
    icon: Path | None = None


_APPS: dict[str, AppSource] = {}


def register_app_source(source: AppSource) -> None:
    """Host-only registration, never callable through a monitor snapshot."""
    if not source.app_id or not source.name:
        raise ValueError("App identity and display name are required")
    _APPS[source.app_id] = source


def app_source(app_id: str = "unknown") -> AppSource:
    return _APPS.get(app_id, AppSource("unknown", "Bright Moon Monitor"))


register_app_source(AppSource("deepseek_harness", "DeepSeek Harness", ICON_ROOT / "Deepseek_logo.png"))
register_app_source(AppSource("codex", "Codex", ICON_ROOT / "Codex_logo.png"))
register_app_source(AppSource("opencode", "OpenCode", ICON_ROOT / "Opencode_logo.png"))
register_app_source(AppSource("claude", "Claude Code", ICON_ROOT / "claude_logo.png"))
register_app_source(AppSource("codeagent", "CodeAgent", ICON_ROOT / "Codeagent_logo.png"))
register_app_source(AppSource("solo_task", "待办任务", ICON_ROOT / "LOGO_EXE.png"))
register_app_source(AppSource("solo_idle", "Bright Moon", ICON_ROOT / "MonitorSolo_logo.png"))
