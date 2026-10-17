"""Discover existing same-user Codex listeners; never start or reconfigure Codex."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from ..src.source_config import codex_endpoint

# Only selected Codex processes belonging to this Windows user are inspected.
# The fixed script is never constructed from Settings input. Its output remains
# internal; arbitrary process command lines must never be logged or shown in UI.
_WINDOWS_PROCESSES = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$rows = @(Get-CimInstance Win32_Process -Filter "Name = 'codex.exe'" | Select-Object -First 64 | ForEach-Object {
    $owner = Invoke-CimMethod -InputObject $_ -MethodName GetOwnerSid -ErrorAction SilentlyContinue
    if ($owner.ReturnValue -eq 0 -and $owner.Sid -eq $sid) {
        @{pid = $_.ProcessId; command = $_.CommandLine}
    }
})
ConvertTo-Json -InputObject $rows -Compress
"""


def process_arguments():
    if os.name == "nt":
        powershell = Path(os.environ.get("SystemRoot", r"C:\Windows"))/"System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", _WINDOWS_PROCESSES],
                                capture_output=True, timeout=3, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
            raise OSError("Codex process discovery unavailable")
        rows = json.loads(result.stdout.decode("utf-8-sig") or "[]")
        for row in rows[:64]:
            command = row.get("command") or ""
            if len(command) <= 32768:
                # Windows executable/URL arguments may be quoted; preserve backslashes.
                yield row["pid"], [part.strip('"') for part in shlex.split(command, posix=False)]
    elif Path("/proc").is_dir():
        count = 0
        for path in Path("/proc").iterdir():
            if not path.name.isdigit():
                continue
            try:
                if path.stat().st_uid != os.getuid() or (path/"comm").read_text().strip() != "codex":
                    continue
                with (path/"cmdline").open("rb") as stream:
                    data = stream.read(32769)
                if len(data) <= 32768:
                    yield int(path.name), [part.decode("utf-8") for part in data.rstrip(b"\0").split(b"\0")]
                count += 1
                if count >= 64:
                    break
            except (OSError, UnicodeError):
                continue


def listener_from_arguments(arguments):
    """Conservative opt-in: only an explicit app-server --listen loopback URL."""
    if (len(arguments) < 3 or arguments[1] != "app-server"
            or arguments[0].replace("\\", "/").rsplit("/", 1)[-1].lower() not in {"codex", "codex.exe"}):
        return ""
    if any(arg == "--ws-auth" or arg.startswith("--ws-auth=") for arg in arguments[2:]):
        return ""  # Never read tokens or bypass a service's authentication.
    for index, arg in enumerate(arguments[2:], 2):
        value = arg.split("=", 1)[1] if arg.startswith("--listen=") else (
            arguments[index+1] if arg == "--listen" and index+1 < len(arguments) else "")
        if value:
            try:
                return codex_endpoint(value.strip('"'))
            except ValueError:
                return ""
    return ""


class ServerDiscovery:
    INTERVAL = 10.0

    def __init__(self, reader=None):
        self.reader = reader or process_arguments
        self.next_scan = 0.0
        self.listeners = {}
        self.message = ""

    def poll(self):
        if time.monotonic() < self.next_scan:
            return dict(self.listeners)
        self.next_scan = time.monotonic() + self.INTERVAL
        try:
            found = {}
            for pid, arguments in self.reader():
                endpoint = listener_from_arguments(arguments)
                if endpoint:
                    found[endpoint] = pid
                if len(found) >= 3:
                    break
            self.listeners = found
            self.message = ""
        except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
            # A transient discovery failure does not drop an established socket.
            self.message = "暂时无法自动识别实时连接；本机会话监控继续运行"
        return dict(self.listeners)
