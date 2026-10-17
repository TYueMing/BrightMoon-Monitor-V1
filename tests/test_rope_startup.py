"""Regression for saved, enabled pendants missing after restarting run.py."""
import json
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scenario", ["saved", "delayed", "recover", "idle"])
def test_restart_restores_and_maps_saved_character_without_user_click(tmp_path, scenario):
    probe = Path(__file__).with_name("rope_startup_probe.py")
    # A fresh interpreter catches startup ordering hidden by old tests, which
    # enabled pendants only after creating/mapping the application's windows.
    # Repeat the saved-choice case using the same disk settings/history, as
    # closing and rerunning run.py does; the probe does not rewrite the settings.
    for _ in range(2 if scenario == "saved" else 1):
        result = subprocess.run([sys.executable, "-I", str(probe), str(tmp_path / "data"), scenario],
                                capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert result.returncode == 0, result.stdout + result.stderr
        report = json.loads(result.stdout.strip().splitlines()[-1])
        if scenario == "idle":
            assert report["idle_hidden"]
        else:
            assert report["visible_after"] < 4
        assert not report["created_before_map"]
