"""Separate immutable installed resources from per-user writable Solo data.

Source checkouts remain portable (their existing data/ directory is unchanged).
PyInstaller preserves resource-relative __file__ paths inside its bundle.
Importing this module never creates directories or copies user data.
"""
import os
from pathlib import Path
import shutil
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def is_installed():
    return bool(getattr(sys, "frozen", False))


def data_root():
    if not is_installed():
        return PROJECT_ROOT / "data"
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "MonitorSolo" / "data"


def deepseek_runtime_root():
    if is_installed():
        return data_root() / "runtime" / "deepseek"
    return PROJECT_ROOT / "monitor" / "deepseek_monitor" / "runtime"


def initialize_installed_assets():
    """Seed only missing, explicitly bundled pendant images; never overwrite.

    Called after the single-instance lock. Settings, wallpapers and history are
    never copied from the developer's checkout into the installed application.
    """
    if not is_installed():
        return
    for folder in ("rope", "cartoon"):
        source = PROJECT_ROOT / "defaults" / folder
        if not source.is_dir():
            continue
        target = data_root() / folder
        target.mkdir(parents=True, exist_ok=True)
        for item in sorted(source.iterdir()):
            if not item.is_file() or (item.suffix.lower() != ".png" and item.name != "readme.txt"):
                continue
            destination = target / item.name
            try:
                output = destination.open("xb")
            except FileExistsError:
                continue
            with output, item.open("rb") as stream:
                shutil.copyfileobj(stream, output)
