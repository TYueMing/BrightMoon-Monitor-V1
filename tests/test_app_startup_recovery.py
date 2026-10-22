"""Exercise full desktop startup using isolated data and no Agent connection."""
import builtins
import tkinter as tk
from unittest.mock import Mock, patch

import pytest

from monitor.app import MonitorApp
from monitor.settings import SettingsStore


@pytest.fixture
def root():
    window = tk.Tk()
    window.withdraw()
    yield window
    try:
        window.destroy()
    except tk.TclError:
        pass


def hub():
    return Mock(read=Mock(return_value={"status": "connected", "runs": [],
                                        "actions": [], "source_states": {}}))


def test_missing_timezone_dependency_keeps_agents_available_and_recovers(root, tmp_path):
    original_import = builtins.__import__

    def missing_timezone(name, *args, **kwargs):
        if name == "tzlocal":
            raise ModuleNotFoundError("No module named 'tzlocal'")
        return original_import(name, *args, **kwargs)

    app = None
    try:
        with patch("builtins.__import__", side_effect=missing_timezone):
            app = MonitorApp(root, SettingsStore(tmp_path), hub())
            assert "tzlocal" in app.tasks.error
            assert app.island.snapshot["status"] == "connected"
            assert app.island.snapshot["task_data_error"]
            assert app.job is not None
            assert app.tasks.store.connection is None
        app.island._poll()
        app.refresh_status()
        assert not app.tasks.error
        assert not app.island.snapshot["task_data_error"]
        assert app.tasks.store.connection.execute("PRAGMA user_version").fetchone()[0] == 3
    finally:
        if app:
            app.close()


def test_first_snapshot_exception_does_not_crash_settings_and_recovers(root, tmp_path):
    app = None
    try:
        with patch.object(MonitorApp, "read_snapshot", side_effect=OSError("fixture outage")):
            app = MonitorApp(root, SettingsStore(tmp_path), hub())
            assert app.island.snapshot["status"] == "error"
            assert app.island.snapshot["source_states"] == {}
            assert app.job is not None
        app.island.snapshot_reader = app.read_snapshot
        app.island._poll()
        app.refresh_status()
        assert app.island.snapshot["status"] == "connected"
    finally:
        if app:
            app.close()


def test_incomplete_source_status_does_not_trigger_a_second_business_read(root, tmp_path):
    source = hub()
    app = MonitorApp(root, SettingsStore(tmp_path), source)
    try:
        for states in (None, [], "invalid", {"claude": None}):
            app.island.snapshot = {"status": "error", "source_states": states}
            source.read.reset_mock()
            app.root.after_cancel(app.job)
            app.refresh_status()
            source.read.assert_not_called()
            assert app.job is not None
    finally:
        app.close()
