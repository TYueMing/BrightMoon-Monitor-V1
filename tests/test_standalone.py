from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
from threading import Event
import time
from unittest.mock import Mock

from PIL import Image
import pytest

from monitor.settings import SettingsStore, PROJECT_ROOT
from monitor.src.source_hub import SourceHub
from monitor.UI.task_lifetime import TaskLifetime


def test_settings_roundtrip_and_placeholder_cannot_be_enabled(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_source("codex", enabled=True, data_path=str(tmp_path / "sessions"))
    store.update(monitor_hide_minutes=3, monitor_sound_enabled=False)
    restored = SettingsStore(tmp_path).snapshot()
    assert restored["monitor_sources"]["codex"]["enabled"]
    assert restored["monitor_hide_minutes"] == 3
    assert not restored["monitor_sound_enabled"]
    assert set(restored["monitor_sources"]) == {"codex", "opencode", "deepseek_harness", "codeagent"}
    with pytest.raises(ValueError):
        store.set_source("codeagent", enabled=True)
    assert not store.snapshot()["monitor_sources"]["codeagent"]["enabled"]


def test_photo_copied_and_survives_source_removal_and_project_move(tmp_path):
    photo = tmp_path / "outside.png"
    Image.new("RGB", (40,30), "#4d67b0").save(photo)
    content = photo.read_bytes()
    store = SettingsStore(tmp_path / "project" / "data")
    imported = store.import_photo(photo)
    assert imported.read_bytes() == content
    assert imported != photo
    photo.unlink()
    moved = tmp_path / "relocated" / "data"
    shutil.copytree(store.root, moved)
    restored = SettingsStore(moved)
    assert restored.photo_path().read_bytes() == content
    assert restored.snapshot()["background_mode"] == "photo"
    restored.update(background_mode="white")
    assert restored.photo_path().is_file()
    assert SettingsStore(moved).snapshot()["background_mode"] == "white"


def test_invalid_photo_or_settings_cannot_replace_saved_state(tmp_path):
    store = SettingsStore(tmp_path / "data")
    store.update(monitor_hide_minutes=2)
    bad = tmp_path / "bad.png"
    bad.write_text("not an image",encoding="utf-8")
    with pytest.raises(OSError):
        store.import_photo(bad)
    with pytest.raises(ValueError):
        store.update(background_photo="backgrounds/../../outside.png")
    with pytest.raises(ValueError):
        store.update(monitor_hide_minutes=0)
    assert SettingsStore(store.root).snapshot()["monitor_hide_minutes"] == 2
    store.path.write_text("{invalid",encoding="utf-8")
    restored = SettingsStore(store.root)
    assert restored.warning and restored.snapshot()["monitor_hide_minutes"] == 1


def test_failed_atomic_save_does_not_change_effective_preferences(tmp_path,monkeypatch):
    store = SettingsStore(tmp_path)
    before = store.snapshot()
    def fail(*args):
        raise PermissionError("fixture")
    monkeypatch.setattr("monitor.settings.os.replace",fail)
    with pytest.raises(PermissionError):
        store.set_source("codex",enabled=True)
    assert store.snapshot() == before


def test_hide_delay_applies_to_each_finished_task_and_never_active_task():
    life = TaskLifetime()
    snapshot = {"backend_id":"fixture", "preferences":{"monitor_hide_minutes":1}, "runs":[
        {"run_id":"done","status":"completed","ended_at":100},
        {"run_id":"active","status":"running","started_at":1},
        {"run_id":"approval","status":"waiting_confirmation","started_at":1}]}
    assert len(life.visible(snapshot,now=159.9)) == 3
    assert set(life.visible(snapshot,now=160)) == {"active","approval"}


def test_disable_immediately_revokes_run_and_actions_and_closes_source(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_source("codex",enabled=True)
    source = Mock()
    source.poll.return_value = {"status":"ready","runs":[{"run_id":"codex:t","status":"running"}],
                                "actions":[{"type":"cancel_run","run_id":"codex:t"}]}
    hub = SourceHub(store.snapshot,factories={"codex":lambda _:source},start_worker=False)
    hub.poll_sources()
    assert hub.read()["runs"][0]["app_id"] == "codex"
    hub.send("cancel_run","codex:t",{})
    assert source.send.call_count == 1
    store.set_source("codex",enabled=False)
    assert hub.read()["runs"] == []
    with pytest.raises(ValueError):
        hub.send("cancel_run","codex:t",{})
    assert source.send.call_count == 1
    source.close.assert_called_once()
    hub.close()


def test_slow_source_does_not_block_other_sources_or_publish_after_disable(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_source("codex",enabled=True)
    store.set_source("opencode",enabled=True)
    entered, release, closed = Event(),Event(),Event()
    slow = Mock()
    def poll():
        entered.set()
        release.wait(5)
        return {"status":"ready","runs":[{"run_id":"old","status":"running"}],"actions":[]}
    slow.poll.side_effect = poll
    slow.close.side_effect = closed.set
    fast = Mock()
    fast.poll.return_value = {"status":"ready","runs":[{"run_id":"fast","status":"running"}],"actions":[]}
    hub = SourceHub(store.snapshot,factories={"codex":lambda _:slow,"opencode":lambda _:fast})
    try:
        hub.read()
        assert entered.wait(2)
        deadline = time.monotonic()+2
        while time.monotonic()<deadline and not hub.read()["runs"]:
            time.sleep(.02)
        assert [r["run_id"] for r in hub.read()["runs"]] == ["fast"]
        store.set_source("codex",enabled=False)
        before = time.monotonic()
        hub.read()
        assert time.monotonic()-before < .5
        release.set()
        assert closed.wait(2)
        assert [r["run_id"] for r in hub.read()["runs"]] == ["fast"]
    finally:
        release.set()
        hub.close()


def test_project_imports_after_copy_without_parent_repository(tmp_path):
    isolated = tmp_path / "standalone"
    shutil.copytree(PROJECT_ROOT / "monitor",isolated / "monitor",ignore=shutil.ignore_patterns(
        "__pycache__","runtime","node_modules"))
    script = '''import importlib, pkgutil, pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
import monitor
for info in pkgutil.walk_packages(monitor.__path__, monitor.__name__ + '.'):
    module = importlib.import_module(info.name)
    assert pathlib.Path(module.__file__).resolve().is_relative_to(pathlib.Path.cwd())
from monitor.src.source_hub import source_factories
from monitor.UI.detail_panels import panel_for
for name in source_factories():
    assert type(panel_for(name)).__name__ != 'UnknownPanel'
assert not any(n.split('.')[0] in {'backend','MoonAgent','utils'} for n in sys.modules)
'''
    result = subprocess.run([sys.executable,"-I","-c",script],cwd=isolated,capture_output=True,text=True,timeout=20)
    assert result.returncode == 0, result.stderr


def test_task_receipts_survive_separate_processes_and_moving_the_standalone_folder(tmp_path):
    isolated = tmp_path / "original"
    shutil.copytree(PROJECT_ROOT / "monitor", isolated / "monitor", ignore=shutil.ignore_patterns(
        "__pycache__", "runtime", "node_modules"))
    script = '''import pathlib, sys
sys.path.insert(0, str(pathlib.Path.cwd()))
from monitor.settings import SettingsStore
from monitor.src.display_history import DisplayHistory
from monitor.UI.task_selection import TaskSelection
store = SettingsStore()
history = DisplayHistory.for_data_root(store.root)
selector = TaskSelection(history)
task = {"run_id":"codex:test", "app_id":"codex", "turn_id":"turn", "status":"failed", "ended_at":100}
selector.update({"runs":[task], "preferences":{}, "backend_id":sys.argv[1]}, now=101)
assert bool(selector.runs) is (sys.argv[1] == "initial")
assert history.path.parent == pathlib.Path.cwd() / "data"
history.close()
assert not any(n.split('.')[0] in {'backend','MoonAgent','utils'} for n in sys.modules)
'''
    first = subprocess.run([sys.executable, "-I", "-c", script, "initial"], cwd=isolated,
                           capture_output=True, text=True, timeout=20)
    assert first.returncode == 0, first.stderr
    copied = tmp_path / "moved"
    shutil.copytree(isolated, copied)
    second = subprocess.run([sys.executable, "-I", "-c", script, "restart"], cwd=copied,
                            capture_output=True, text=True, timeout=20)
    assert second.returncode == 0, second.stderr
