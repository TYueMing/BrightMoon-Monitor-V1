"""Exercise installed data ownership without freezing or starting real agents."""
from pathlib import Path
import sys

import pytest

from monitor.settings import SettingsStore
from monitor.src import paths
from monitor.src.display_history import DisplayHistory
from monitor.src.lifecycle import InstanceLock, runtime_root
from monitor.opencode_monitor.config import config_path


@pytest.fixture
def installed(tmp_path, monkeypatch):
    bundle = tmp_path / "Read only application" / "_internal"
    bundle.mkdir(parents=True)
    user = tmp_path / "中文 Windows 用户" / "AppData" / "Local"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", bundle)
    monkeypatch.setenv("LOCALAPPDATA", str(user))
    return bundle, user / "MonitorSolo" / "data"


def test_installed_settings_and_locks_use_same_user_directory(installed):
    bundle, user_data = installed
    assert paths.data_root() == user_data
    assert not user_data.exists()  # Merely importing/resolving paths has no effects.
    assert runtime_root() == user_data / "runtime"
    assert config_path() == user_data / "settings.json"
    assert paths.deepseek_runtime_root() == user_data / "runtime" / "deepseek"
    store = SettingsStore()
    store.update(monitor_hide_minutes=4, monitor_sound_enabled=False)
    restored = SettingsStore()
    assert restored.snapshot()["monitor_hide_minutes"] == 4
    assert not restored.snapshot()["monitor_sound_enabled"]
    lock = InstanceLock("release-test")
    second = InstanceLock("release-test")
    try:
        assert lock.acquire()
        assert not second.acquire()
    finally:
        lock.close()
        second.close()
    assert second.acquire()
    second.close()
    assert list(bundle.iterdir()) == []


def test_installed_defaults_do_not_copy_private_files_or_overwrite_selection(installed):
    bundle, user_data = installed
    defaults = bundle / "defaults" / "rope"
    defaults.mkdir(parents=True)
    (defaults / "gpt.png").write_bytes(b"bundled-version")
    (defaults / "deepseek.png").write_bytes(b"new-bundled-image")
    (defaults / "readme.txt").write_text("original attribution", encoding="utf-8")
    (defaults / "settings.json").write_text('{"private":"do not copy"}', encoding="utf-8")
    cartoons=bundle/"defaults"/"cartoon"
    cartoons.mkdir()
    (cartoons/"月光星尘.png").write_bytes(b"curated-cartoon")
    (user_data / "rope").mkdir(parents=True)
    (user_data / "rope/gpt.png").write_bytes(b"user-version")
    store = SettingsStore()
    store.update(rope_enabled=True, rope_image="rope/gpt.png")
    old_settings = store.path.read_bytes()
    for _ in range(2):
        paths.initialize_installed_assets()
    assert (user_data / "rope/gpt.png").read_bytes() == b"user-version"
    assert (user_data / "rope/deepseek.png").read_bytes() == b"new-bundled-image"
    assert not (user_data / "rope/settings.json").exists()
    assert store.path.read_bytes() == old_settings
    assert (user_data/"cartoon"/"月光星尘.png").read_bytes()==b"curated-cartoon"


def test_installed_history_survives_an_application_directory_upgrade(installed, monkeypatch):
    bundle, user_data = installed
    store = SettingsStore()
    history = DisplayHistory.for_data_root(store.root)
    history.put("completed-fixture", {"phase": "finished", "since": 1, "touched": 2,
                                     "watermark": 2, "evidence": [], "retired": True})
    history.flush(3, force=True)
    assert not history.error
    history.close()
    # Updating/replacing the application directory must not replay old tasks.
    monkeypatch.setattr(paths, "PROJECT_ROOT", bundle.parent / "new-version" / "_internal")
    restored = DisplayHistory.for_data_root(SettingsStore().root)
    assert restored.path == user_data / "display_history.sqlite3"
    assert restored.get("completed-fixture")["retired"]
    restored.close()


def test_source_default_paths_remain_portable(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)
    assert paths.data_root() == tmp_path / "data"
    assert paths.deepseek_runtime_root() == tmp_path / "monitor/deepseek_monitor/runtime"
    paths.initialize_installed_assets()
    assert list(tmp_path.iterdir()) == []


def test_installed_localappdata_fallback(installed, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: installed[0].parent / "User"))
    assert paths.data_root() == Path.home() / "AppData/Local/MonitorSolo/data"


def test_release_resources_do_not_contain_developer_data():
    packaging = Path(__file__).resolve().parents[1] / "packaging"
    sys.path.insert(0, str(packaging))
    try:
        from release_files import resource_files
        entries = resource_files()
    finally:
        sys.path.remove(str(packaging))
    root = packaging.parent.resolve()
    for name, destination in entries:
        source = Path(name).resolve().relative_to(root)
        # aiohttp includes llhttp's license under licenses/vendor/. This is
        # legal attribution, not our packaging/vendor executable cache.
        license_text = source.parts[:2] == ("packaging", "licenses") and Path(name).name.lower() in {"license", "license.txt"}
        prohibited = {"data", "tests", "preview", "runtime", ".env"}
        if not license_text:
            prohibited.add("vendor")
        assert not set(source.parts).intersection(prohibited)
        assert ".." not in Path(destination).parts
    targets = {f"{destination}/{Path(name).name}" for name, destination in entries}
    assert "monitor/icon_asset/MonitorSolo_logo.png" in targets
    assert "monitor/deepseek_monitor/installed-layout.json" in targets
    assert "defaults/rope/gpt.png" in targets
    assert "monitor/phone/web/index.html" in targets
    assert "monitor/phone/web/transport.js" in targets
