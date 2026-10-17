"""Ownership and cross-process exclusion for the desktop island (stdlib only)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from .paths import data_root


def runtime_root() -> Path:
    return data_root() / "runtime"


class InstanceLock:
    """An OS lock, never a persisted PID used to kill another process.

    Leave the sentinel file in place after release: deleting it can let two
    processes lock different inodes on Unix. Crashes release the OS lock too.
    """

    def __init__(self, user_key: str) -> None:
        key = hashlib.sha256(user_key.encode("utf-8")).hexdigest()[:24]
        self.path = runtime_root() / f"island-{key}.lock"
        self._handle = None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                # Windows byte locks may cover bytes beyond EOF. No writes or
                # truncation are needed before acquiring the exclusive lock.
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._handle = handle
        return True

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


class OwnerProcess:
    """Keep the originating backend's process handle until the island exits."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self._handle = None
        self._kernel = None
        if os.name == "nt" and pid > 0:
            import ctypes
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
            kernel.CloseHandle.restype = wintypes.BOOL
            self._kernel = kernel
            self._handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE

    def alive(self) -> bool:
        if self.pid <= 0:
            return True  # explicit standalone launch
        if os.name == "nt":
            # Wait zero milliseconds, so the Tk message loop is never blocked.
            # Never use os.kill(pid, 0) on Windows: it terminates the process.
            return bool(self._handle and self._kernel.WaitForSingleObject(self._handle, 0) == 258)
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def close(self) -> None:
        if self._handle:
            self._kernel.CloseHandle(self._handle)
            self._handle = None
