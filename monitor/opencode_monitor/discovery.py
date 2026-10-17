"""Auto-discovery of the local opencode server (port + Basic auth credentials).

opencode runs a local HTTP server in two shapes:

* ``opencode serve``            -> fixed port, no auth by default.
* opencode desktop "sidecar"    -> random port + Basic auth, where the
  username/password live in the sidecar process environment
  (``OPENCODE_SERVER_USERNAME`` / ``OPENCODE_SERVER_PASSWORD``).

This module locates that server without configuration:

1. Enumerate listening TCP ports and the processes that own them.
2. For candidate processes (opencode / OpenCode.exe sidecar / bun / node) read
   their environment to recover the Basic auth credentials.
3. Fall back to a plain health-probe over all localhost ports (works for an
   unauthenticated ``opencode serve``).

Windows is fully supported; other platforms fall back to the health probe.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

_CANDIDATE_NAMES = {"opencode", "opencode.exe", "bun", "bun.exe",
                    "node", "node.exe"}
_HEALTH_PATHS = ("/global/health", "/api/global/health")


def _run_powershell(script: str) -> str:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return completed.stdout or ""


def _parse_netstat(text: str) -> dict[int, set[tuple[str, int]]]:
    """Parse ``netstat -ano`` output into {pid: {(address, port), ...}}."""
    ports: dict[int, set[tuple[str, int]]] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP":
            continue
        if parts[3].upper() not in ("LISTENING", "LISTEN"):
            continue
        address, _, port_text = parts[1].rpartition(":")
        if not port_text.isdigit():
            continue
        address = address.strip("[]")
        ports.setdefault(int(parts[4]), set()).add((address, int(port_text)))
    return ports


def _listen_map() -> list[dict[str, Any]]:
    """Return [{pid, address, port, name}] for LISTEN sockets (Windows)."""
    if os.name != "nt":
        return []

    # netstat is far faster than Get-NetTCPConnection (ms vs seconds).
    completed = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True, errors="replace",
        timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    ports = _parse_netstat(completed.stdout)

    names: dict[int, str] = {}
    raw = _run_powershell(
        "Get-Process -ErrorAction SilentlyContinue | "
        "Select-Object Id,ProcessName | ConvertTo-Json -Compress"
    )
    try:
        data = json.loads(raw)
    except ValueError:
        data = []
    if isinstance(data, dict):
        data = [data]
    for item in data:
        if isinstance(item, dict) and item.get("Id"):
            names[int(item["Id"])] = str(item.get("ProcessName") or "").casefold()

    result: list[dict[str, Any]] = []
    for pid, entries in ports.items():
        for address, port in entries:
            result.append({"pid": pid, "address": address, "port": port,
                           "name": names.get(pid, "")})
    return result


def _read_process_env_windows(pid: int) -> dict[str, str]:
    """Best-effort read of a 64-bit process environment block."""
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    kernel32.ReadProcessMemory.argtypes = (
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID, ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    )
    kernel32.ReadProcessMemory.restype = wintypes.BOOL

    class PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [("Reserved1", ctypes.c_void_p),
                    ("PebBaseAddress", ctypes.c_void_p),
                    ("Reserved2", ctypes.c_void_p * 2),
                    ("UniqueProcessId", ctypes.c_void_p),
                    ("Reserved3", ctypes.c_void_p)]

    handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        return {}

    def read(addr: int, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        nread = ctypes.c_size_t(0)
        kernel32.ReadProcessMemory(handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(nread))
        return buf.raw[:nread.value]

    try:
        pbi = PROCESS_BASIC_INFORMATION()
        if ntdll.NtQueryInformationProcess(handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None) != 0:
            return {}
        param_bytes = read(pbi.PebBaseAddress + 0x20, ctypes.sizeof(ctypes.c_void_p))
        if len(param_bytes) < ctypes.sizeof(ctypes.c_void_p):
            return {}
        parameters = ctypes.c_void_p.from_buffer_copy(param_bytes).value
        env_bytes = read(parameters + 0x80, ctypes.sizeof(ctypes.c_void_p))
        if len(env_bytes) < ctypes.sizeof(ctypes.c_void_p):
            return {}
        env_ptr = ctypes.c_void_p.from_buffer_copy(env_bytes).value

        # Read the env block in chunks until the double-NUL terminator, never
        # crossing into uncommitted memory (which makes ReadProcessMemory fail).
        data = b""
        offset = 0
        for _ in range(64):
            chunk = read(env_ptr + offset, 4096)
            data += chunk
            if b"\x00\x00\x00\x00" in chunk or len(chunk) < 4096:
                break
            offset += len(chunk)
        text = data.decode("utf-16-le", "replace")
    except (OSError, ValueError, ctypes.ArgumentError):
        return {}
    finally:
        kernel32.CloseHandle(handle)

    result: dict[str, str] = {}
    for match in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)=([^\x00]*)", text):
        result[match.group(1)] = match.group(2)
    return result


def _read_process_env(pid: int) -> dict[str, str]:
    if os.name == "nt":
        return _read_process_env_windows(pid)
    try:
        text = open(f"/proc/{pid}/environ", "rb").read().decode("utf-8", "replace")
    except OSError:
        return {}
    result = {}
    for chunk in text.split("\x00"):
        if "=" in chunk:
            key, _, value = chunk.partition("=")
            result[key] = value
    return result


def _health_ok(url: str, username: str = "", password: str = "") -> bool:
    for path in _HEALTH_PATHS:
        request = urllib.request.Request(url + path)
        if username and password:
            import base64
            token = base64.b64encode(f"{username}:{password}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=1.0) as response:
                body = response.read(512).decode("utf-8", "replace")
            if "healthy" in body or "version" in body:
                return True
        except Exception:  # any non-HTTP/refused/malformed port is simply "not opencode"
            continue
    return False


def discover(preferred_url: str | None = None) -> dict[str, Any] | None:
    """Return {"url", "username", "password"} for the local opencode server."""
    env_username = os.environ.get("OPENCODE_SERVER_USERNAME", "opencode")
    env_password = os.environ.get("OPENCODE_SERVER_PASSWORD", "")

    if preferred_url:
        url = preferred_url.rstrip("/")
        return {"url": url, "username": env_username, "password": env_password}

    listen_items: list[dict[str, Any]] = _listen_map() if os.name == "nt" else []
    candidates: list[str] = []
    seen: set[int] = set()
    named: list[dict[str, Any]] = []
    for item in listen_items:
        if item["address"] not in ("127.0.0.1", "0.0.0.0", "::1", "::"):
            continue
        if item["name"] in _CANDIDATE_NAMES:
            named.append(item)
        if item["port"] not in seen:
            seen.add(item["port"])
            candidates.append(f"http://127.0.0.1:{item['port']}")

    # 1. Candidate-named processes with inherited credentials (fast path).
    if env_password:
        for item in named:
            url = f"http://127.0.0.1:{item['port']}"
            if _health_ok(url, env_username, env_password):
                return {"url": url, "username": env_username, "password": env_password}

    # 2. Candidate-named process environment (monitor launched externally).
    for item in named:
        env = _read_process_env(item["pid"])
        password = env.get("OPENCODE_SERVER_PASSWORD", "")
        username = env.get("OPENCODE_SERVER_USERNAME", "opencode")
        url = f"http://127.0.0.1:{item['port']}"
        if password and _health_ok(url, username, password):
            return {"url": url, "username": username, "password": password}

    # 3. Concurrent full scan (authenticated, then unauthenticated).
    if env_password:
        found = _find_url(candidates, env_username, env_password)
        if found:
            return {"url": found, "username": env_username, "password": env_password}
    found = _find_url(candidates)
    if found:
        return {"url": found, "username": "", "password": ""}
    return None


def _find_url(candidates: list[str], username: str = "", password: str = "") -> str | None:
    """Probe candidates concurrently and return the first opencode-looking URL."""
    def probe(url: str) -> str | None:
        return url if _health_ok(url, username, password) else None
    executor = ThreadPoolExecutor(max_workers=32)
    futures = [executor.submit(probe, url) for url in candidates]
    try:
        for future in as_completed(futures):
            result = future.result()
            if result:
                for pending in futures:
                    pending.cancel()
                return result
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    return None
