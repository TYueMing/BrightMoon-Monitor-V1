"""An explicit resource allowlist, shared by the spec and release checks."""
import json
from pathlib import Path

PACKAGING = Path(__file__).resolve().parent
SOLO = PACKAGING.parent


def resource_files():
    files = []
    for name in ("index.html", "styles.css", "app.js", "transport.js"):
        files.append((SOLO / "monitor/phone/web" / name, "monitor/phone/web"))
    # Never recursively include the project, monitor/, or a developer's data/.
    for directory, pattern in (("monitor/icon_asset", "*.png"), ("monitor/music", "*.mp3"),
                               ("monitor/opencode_monitor/assets", "*.png"),
                               ("monitor/opencode_monitor/music", "*.mp3")):
        files.extend((p, directory) for p in sorted((SOLO / directory).glob(pattern)))
    bridge = "monitor/deepseek_monitor"
    for name in ("index.mjs", "broker.mjs", "server.mjs", "cordis.patch.yml", "package.json"):
        files.append((SOLO / bridge / "bridge" / name, bridge + "/bridge"))
    files.append((SOLO / bridge / "install_bridge.ps1", bridge))
    files.append((PACKAGING / "assets" / "installed-layout.json", bridge))
    for name in ("claude.png", "deepseek.png", "gemini.png", "glm.png", "gpt.png", "grok.png",
                 "kimi.png", "qwen.png", "whale.png", "readme.txt"):
        files.append((PACKAGING / "assets" / "rope" / name, "defaults/rope"))
    # Bundle only the curated default, never a user's uploaded data/cartoon.
    files.append((PACKAGING / "assets" / "cartoon" / "月光星尘.png", "defaults/cartoon"))
    files.append((PACKAGING / "assets" / "MonitorSolo.ico", "monitor/icon_asset"))
    files.extend((p, "licenses/" + p.parent.relative_to(PACKAGING / "licenses").as_posix())
                 for p in sorted((PACKAGING / "licenses").rglob("*")) if p.is_file())
    files.append((PACKAGING / "THIRD_PARTY_NOTICES.md", "licenses"))
    files.append((SOLO / "docs" / "INSTALLED_USER_GUIDE.md", "docs"))
    destinations = set()
    for source, destination in files:
        if not source.is_file() or not source.resolve().is_relative_to(SOLO.resolve()):
            raise ValueError(f"Missing or external release resource: {source}")
        key = f"{destination}/{source.name}".casefold()
        if key in destinations:
            raise ValueError(f"Duplicate release resource: {key}")
        destinations.add(key)
    return [(str(p), destination) for p, destination in files]


def version_info():
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
    )
    release = json.loads((PACKAGING / "release.json").read_text(encoding="utf-8"))
    version = tuple(int(n) for n in release["version"].split("."))
    if len(version) != 3 or not all(0 <= n <= 65535 for n in version):
        raise ValueError("Release version must be three integers in 0..65535")
    strings = {"CompanyName": release["publisher"], "FileDescription": release["name"],
               "FileVersion": release["version"], "InternalName": release["executable"],
               "OriginalFilename": release["executable"] + ".exe", "ProductName": release["name"],
               "ProductVersion": release["version"]}
    return VSVersionInfo(ffi=FixedFileInfo(filevers=(*version, 0), prodvers=(*version, 0),
                                         mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
                         kids=[StringFileInfo([StringTable("040904B0", [StringStruct(k, v) for k, v in strings.items()])]),
                               VarFileInfo([VarStruct("Translation", [1033, 1200])])])
