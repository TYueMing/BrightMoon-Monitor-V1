"""Discover physical Private Windows interfaces, never change OS policy."""
from dataclasses import dataclass
import ipaddress
import json
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class Interface:
    id: str
    name: str
    address: str
    prefix: int
    profile: str

    @property
    def subnet(self):
        return ipaddress.ip_network(f"{self.address}/{self.prefix}", strict=False)

    def allows(self, address):
        try:
            return ipaddress.ip_address(address) in self.subnet
        except ValueError:
            return False


SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$ErrorActionPreference = 'Stop'
$result = @(Get-NetAdapter -Physical | Where-Object Status -eq 'Up' | ForEach-Object {
  $adapter = $_
  $profile = Get-NetConnectionProfile -InterfaceIndex $adapter.ifIndex -ErrorAction SilentlyContinue
  if ($profile.NetworkCategory -eq 'Private') {
    Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 | Where-Object AddressState -eq 'Preferred' | ForEach-Object {
      [pscustomobject]@{ id=[string]$adapter.InterfaceGuid; name=$adapter.Name; address=$_.IPAddress; prefix=$_.PrefixLength; profile=[string]$profile.Name }
    }
  }
})
ConvertTo-Json -InputObject $result -Compress
"""


def discover():
    if os.name != "nt":
        return []
    shell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = subprocess.run([str(shell), "-NoProfile", "-NonInteractive", "-Command", SCRIPT],
                            capture_output=True, encoding="utf-8-sig", errors="replace", timeout=8,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
    values = json.loads(result.stdout or "[]")
    if isinstance(values, dict):
        values = [values]
    interfaces = []
    for value in values:
        ip = ipaddress.ip_address(value["address"])
        # Do not treat all non-global addresses (loopback/link-local) as a LAN.
        if ip.version == 4 and ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified):
            interfaces.append(Interface(str(value["id"]), str(value["name"]), str(ip),
                                        int(value["prefix"]), str(value["profile"])))
    return interfaces


def choose(config, interfaces):
    matches = [item for item in interfaces if item.id == config["interface_id"]]
    if len(matches) != 1:
        raise ValueError("请选择唯一的私人网络 IPv4 网卡；没有可用网卡时请检查 Windows 网络设置。")
    return matches[0]
