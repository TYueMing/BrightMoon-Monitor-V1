"""Trusted per-source detail renderers; the capsule never interprets agent stages."""
from typing import Protocol

from .common_model import STATUS_TEXT
from ..src.app_sources import app_source


class AgentMonitorPanel(Protocol):
    # Providers may additionally expose activate(ui, "panel:...") and
    # scroll(ui, direction). These optional local controls have no execution
    # authority and are deliberately absent from the required registration API.
    def header(self, run: dict, status: str) -> str: ...
    def size(self, ui) -> tuple[int, int]: ...
    def draw(self, ui, color: str) -> bool:
        """Draw expanded content; return whether the native text panel is visible."""
        ...
    def rows(self, ui) -> list: ...
    def summary_lines(self, ui) -> list[str]: ...


class UnknownPanel:
    def header(self, run, status):
        return app_source(run.get("app_id")).name

    def size(self, ui):
        return ui.WIDTH, 164

    def draw(self, ui, color):
        ui._text(26, 90, "此来源尚未安装详情适配器", width=ui._size[0]-52)
        ui._text(26, 122, STATUS_TEXT.get(ui._state(), "等待数据"), fill=color)
        return False

    def rows(self, ui):
        return []

    def summary_lines(self, ui):
        return []


_PANELS = {}
_UNKNOWN = UnknownPanel()
_INITIALIZED = False


def register_panel(app_id: str, panel: AgentMonitorPanel) -> None:
    """Called by trusted application code, never by a path in a snapshot."""
    if not app_id or not all(callable(getattr(panel, method, None))
                             for method in ("header", "size", "draw", "rows", "summary_lines")):
        raise ValueError("Invalid liveview panel")
    _PANELS[app_id] = panel


def panel_for(app_id: str) -> AgentMonitorPanel:
    global _INITIALIZED
    if not _INITIALIZED:
        from ..deepseek_monitor.panel import DeepSeekPanel
        from ..codex_monitor.panel import CodexPanel
        from ..opencode_monitor.panel import OpenCodePanel
        from .tasks.detail_panel import ReminderPanel
        _PANELS.setdefault("deepseek_harness", DeepSeekPanel())
        _PANELS.setdefault("codex", CodexPanel())
        _PANELS.setdefault("opencode", OpenCodePanel())
        _PANELS.setdefault("solo_task", ReminderPanel())
        _INITIALIZED = True
    return _PANELS.get(app_id or "unknown", _UNKNOWN)
