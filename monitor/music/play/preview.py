"""Cancellable, non-overlapping local sound previews, independent of alerts."""
import ctypes
import os
import shutil
import subprocess
from threading import Event, Lock, Thread
from uuid import uuid4

from .catalog import sound_files


class SoundPreview:
    def __init__(self):
        self._cancel = None
        self._thread = None
        self._lock = Lock()
        self.current = ""
        self.error = ""

    def play(self, name):
        path = sound_files().get(name)
        if path is None:
            raise ValueError("提示音文件不存在")
        self.stop()
        cancel = Event()
        self._cancel, self.current, self.error = cancel, name, ""
        self._thread = Thread(target=self._work,args=(path,cancel),daemon=True,name="monitor-sound-preview")
        self._thread.start()

    def stop(self):
        if self._cancel:
            self._cancel.set()
        self.current = ""

    def _work(self,path,cancel):
        try:
            # Serialize cleanup + playback, so clicking rapidly never layers clips.
            with self._lock:
                if not cancel.is_set():
                    if os.name == "nt":
                        self._windows(path,cancel)
                    else:
                        self._external(path,cancel)
        except (OSError,RuntimeError,AttributeError):
            if self._cancel is cancel:
                self.error = "当前设备无法播放该提示音"
        finally:
            if self._cancel is cancel:
                self.current = ""

    def _windows(self,path,cancel):
        winmm = ctypes.WinDLL("winmm")
        send = winmm.mciSendStringW
        send.argtypes = (ctypes.c_wchar_p,ctypes.c_wchar_p,ctypes.c_uint,ctypes.c_void_p)
        send.restype = ctypes.c_uint
        alias = "bm_preview_"+uuid4().hex
        quoted = str(path).replace('"', "")
        if send(f'open "{quoted}" type mpegvideo alias {alias}',None,0,None):
            raise RuntimeError("Audio open failed")
        try:
            if cancel.is_set():
                return
            if send(f"play {alias}",None,0,None):
                raise RuntimeError("Audio playback failed")
            while not cancel.wait(.04):
                mode = ctypes.create_unicode_buffer(32)
                if send(f"status {alias} mode",mode,32,None) or mode.value != "playing":
                    break
        finally:
            send(f"stop {alias}",None,0,None)
            send(f"close {alias}",None,0,None)

    def _external(self,path,cancel):
        command = next((c for c in ("ffplay","mpg123","afplay") if shutil.which(c)),None)
        if not command:
            raise RuntimeError("No audio backend")
        args = (["-nodisp","-autoexit","-loglevel","quiet"] if command=="ffplay" else [])
        process = subprocess.Popen([shutil.which(command),*args,str(path)],stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            while process.poll() is None and not cancel.wait(.04):
                pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
