"""Play one local completion cue without blocking Tk or the Agent.

Windows ships an MP3-capable MCI backend, so the monitor does not need a
third-party Python audio package. On Unix-like systems an installed ffplay,
mpg123, or afplay is used when available; if none exists the monitor simply
continues silently.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid

from .catalog import sound_files, default_sound, DEFAULT_COMPLETION_SOUND


class CompletionSoundPlayer:
    def __init__(self, music_dir: Path | None = None) -> None:
        self.music_dir = music_dir or Path(__file__).resolve().parents[1]

    def path_for(self, sound: str = DEFAULT_COMPLETION_SOUND) -> Path | None:
        files = sound_files(self.music_dir)
        return files.get(str(sound or DEFAULT_COMPLETION_SOUND), files.get(default_sound(files)))

    def play(self, sound: str = DEFAULT_COMPLETION_SOUND) -> bool:
        path = self.path_for(sound)
        if path is None:
            return False
        threading.Thread(target=self._play, args=(path,), daemon=True,
                         name="brightmoon-completion-sound").start()
        return True

    def _play(self, path: Path) -> None:
        if os.name == "nt" and self._play_mci(path):
            return
        for command in ("ffplay", "mpg123", "afplay"):
            executable = shutil.which(command)
            if not executable:
                continue
            args = ([executable, "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)]
                    if command == "ffplay" else [executable, str(path)])
            try:
                subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                return
            except OSError:
                continue

    @staticmethod
    def _play_mci(path: Path) -> bool:
        try:
            winmm = ctypes.WinDLL("winmm")
            send = winmm.mciSendStringW
            send.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p)
            send.restype = ctypes.c_uint
            alias = f"brightmoon_{uuid.uuid4().hex}"
            quoted = str(path).replace('"', "")
            if send(f'open "{quoted}" type mpegvideo alias {alias}', None, 0, None):
                return False
            try:
                return send(f"play {alias} wait", None, 0, None) == 0
            finally:
                send(f"close {alias}", None, 0, None)
        except (AttributeError, OSError, TypeError):
            return False
