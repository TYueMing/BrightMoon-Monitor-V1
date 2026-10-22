"""Portable, atomic settings and project-owned background photographs."""
from copy import deepcopy
import hashlib
import json
import os
import re
from pathlib import Path
from threading import RLock
from uuid import uuid4

from PIL import Image

from .src.preferences import normalize_preferences
from .src.wallpapers import EXTENSIONS, valid_reference, read_photo
from .src.rope_assets import valid_rope_reference
from .src.cartoon_assets import valid_cartoon_reference
from .src.paths import PROJECT_ROOT, data_root
from .music.play.catalog import sound_files, default_sound
from .phone.config import normalize as normalize_phone
from .multi_diverse.config import normalize as normalize_peers

DATA_ROOT = data_root()


class SettingsStore:
    def __init__(self, root=None):
        self.root = Path(data_root() if root is None else root).resolve()
        self.path = self.root / "settings.json"
        self.lock = RLock()
        self.warning = ""
        raw = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Expected object")
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            self.warning = "设置文件无法读取，已使用默认设置。"
        self._value = self.normalize(raw if isinstance(raw, dict) else {})

    @staticmethod
    def normalize(raw, *, strict=False):
        prefs = normalize_preferences(raw, strict=strict)
        mode = raw.get("background_mode", "white")
        photo = raw.get("background_photo", "")
        # Saved paths are relative to this project's data folder, never external.
        valid = valid_reference(photo)
        if mode not in {"white", "photo"} or not valid:
            if strict:
                raise ValueError("背景设置无效")
            mode, photo = "white", ""
        island_mode = raw.get("monitor_background_mode", "solid")
        island_photo = raw.get("monitor_background_photo", "")
        if island_mode not in {"solid", "photo"} or not valid_reference(island_photo):
            if strict:
                raise ValueError("灵动岛壁纸设置无效")
            island_mode, island_photo = "solid", ""
        try:
            raw_transparency = raw.get("panel_transparency", 50)
            transparency = int(raw_transparency)
            if (isinstance(raw_transparency, bool) or transparency != float(raw_transparency)
                    or not 0 <= transparency <= 100):
                raise ValueError()
        except (TypeError, ValueError, OverflowError):
            if strict:
                raise ValueError("面板透明度须为 0 到 100 的整数") from None
            transparency = 50
        panel_color = raw.get("panel_color", "#ffffff")
        if not isinstance(panel_color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", panel_color):
            if strict:
                raise ValueError("面板颜色须为 #RRGGBB")
            panel_color = "#ffffff"
        rope_enabled = raw.get("rope_enabled", False)
        rope_image = raw.get("rope_image", "")
        if not isinstance(rope_enabled, bool) or not valid_rope_reference(rope_image):
            if strict:
                raise ValueError("悬挂卡通设置无效")
            rope_enabled, rope_image = False, ""
        task_sound = raw.get("task_reminder_sound", "sound-1")
        cartoon_enabled = raw.get("cartoon_enabled", True)
        cartoon_image = raw.get("cartoon_image", "")
        if not isinstance(cartoon_enabled,bool) or not valid_cartoon_reference(cartoon_image):
            if strict:
                raise ValueError("空闲角色设置无效")
            cartoon_enabled, cartoon_image = True, ""
        files = sound_files()
        if not isinstance(task_sound,str) or task_sound not in files:
            if strict and task_sound != "sound-1":
                raise ValueError("待办提示音不可用")
            task_sound = default_sound(files)
        task_sound_enabled = raw.get("task_sound_enabled", True)
        if not isinstance(task_sound_enabled, bool):
            if strict:
                raise ValueError("待办声音开关无效")
            task_sound_enabled = True
        task_monitor_enabled = raw.get("task_monitor_enabled", True)
        if not isinstance(task_monitor_enabled, bool):
            if strict:
                raise ValueError("任务监控开关无效")
            task_monitor_enabled = True
        resident = raw.get("island_resident", False)
        if not isinstance(resident, bool):
            if strict:
                raise ValueError("灵动岛常驻开关无效")
            resident = False
        task_view = raw.get("task_view", "list")
        if task_view not in ("list", "grid"):
            if strict:
                raise ValueError("任务视图无效")
            task_view = "list"
        try:
            raw_position = raw.get("rope_position", 50)
            rope_position = int(raw_position)
            if isinstance(raw_position, bool) or float(raw_position) != rope_position or not 0 <= rope_position <= 100:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            if strict:
                raise ValueError("挂件横向位置须为 0–100 的整数") from None
            rope_position = 50
        return {**prefs, "background_mode": mode, "background_photo": photo,
                "monitor_background_mode": island_mode, "monitor_background_photo": island_photo,
                "panel_transparency": transparency, "panel_color": panel_color.lower(),
                "rope_enabled": rope_enabled, "rope_image": rope_image, "schema_version": 5,
                "phone": normalize_phone(raw.get("phone"), strict=strict),
                "peers": normalize_peers(raw.get("peers"), strict=strict),
                "task_reminder_sound": task_sound, "task_sound_enabled": task_sound_enabled,
                "task_monitor_enabled": task_monitor_enabled, "task_view": task_view, "rope_position": rope_position,
                "island_resident": resident, "cartoon_enabled": cartoon_enabled,
                "cartoon_image": cartoon_image}

    def snapshot(self):
        with self.lock:
            return deepcopy(self._value)

    def update(self, **changes):
        with self.lock:
            value = self.normalize({**self._value, **changes}, strict=True)
            self.root.mkdir(parents=True, exist_ok=True)
            temp = self.root / f"settings-{uuid4().hex}.tmp"
            try:
                temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(temp, self.path)
            finally:
                temp.unlink(missing_ok=True)
            self._value = value
            return deepcopy(value)

    def set_source(self, app_id, **changes):
        with self.lock:
            sources = deepcopy(self._value["monitor_sources"])
            if app_id not in sources:
                raise ValueError("未知监控来源")
            sources[app_id].update(changes)
            return self.update(monitor_sources=sources)

    def photo_path(self):
        return self.background_path(self.snapshot()["background_photo"])

    def background_path(self, relative):
        """Resolve one project-owned wallpaper without accepting external paths."""
        if not relative or not valid_reference(relative):
            return None
        try:
            candidate = (self.root / relative).resolve()
            # Symlinks/junctions must not turn a saved local selection into an
            # external path, including a redirected backgrounds directory.
            if candidate.is_relative_to(self.root / "backgrounds") and candidate.is_file():
                return candidate
        except (OSError, ValueError, RuntimeError):
            pass
        return None

    def list_backgrounds(self):
        """Return saved wallpaper paths, newest first, relative to Solo data."""
        folder = self.root / "backgrounds"
        entries = []
        try:
            for path in folder.iterdir():
                relative = f"backgrounds/{path.name}"
                if path.suffix.lower() not in EXTENSIONS or not self.background_path(relative):
                    continue
                try:
                    entries.append((path.stat().st_mtime_ns, relative))
                except OSError:
                    continue  # A file may be removed while the picker is open.
        except OSError:
            return []
        return [relative for _, relative in sorted(entries, reverse=True)]

    def select_photo(self, relative, *, target="app"):
        if target not in {"app", "island"}:
            raise ValueError("未知壁纸目标")
        # Validate before committing, so a missing/corrupt thumbnail cannot
        # replace the user's last working background.
        read_photo(self.background_path(relative))
        fields = ({"background_mode": "photo", "background_photo": relative} if target == "app" else
                  {"monitor_background_mode": "photo", "monitor_background_photo": relative})
        self.update(**fields)

    def import_photo(self, source, *, target="app"):
        source = Path(source)
        if target not in {"app", "island"}:
            raise ValueError("未知壁纸目标")
        destination = target
        if source.stat().st_size > 40 * 1024 * 1024:
            raise ValueError("请选择小于 40 MB 的照片")
        # Validate first, then copy exact bytes. The original is never modified.
        with Image.open(source) as image:
            if image.format not in {"JPEG", "PNG", "WEBP", "BMP"}:
                raise ValueError("请选择 JPG、PNG、WebP 或 BMP 照片")
            if image.width * image.height > 40_000_000:
                raise ValueError("照片分辨率过大，请缩小到 4000 万像素以内")
            suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp"}[image.format]
            image.verify()
        content = source.read_bytes()
        relative = f"backgrounds/{hashlib.sha256(content).hexdigest()}{suffix}"
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            # Avoid appending another UUID to the 64-character content hash:
            # long Windows user/install paths can exceed MAX_PATH during import.
            temp = target.parent / f"photo-{uuid4().hex}.tmp"
            try:
                temp.write_bytes(content)
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
        self.select_photo(relative, target=destination)
        return target
