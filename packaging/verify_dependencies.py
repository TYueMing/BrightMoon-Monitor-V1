"""Offline integrity, target compatibility, dependency closure and resource checks.

No installation, network access, agent connection or application build occurs.
"""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    manifest = json.loads((ROOT / "dependency-manifest.json").read_text(encoding="utf-8"))
    all_records = manifest["wheels"] + manifest["tools"] + manifest["license_sources"]
    for entry in all_records:
        path = ROOT / entry["path"]
        if not path.resolve().is_relative_to(ROOT):
            raise ValueError("Manifest contains an external path")
        data = path.read_bytes()
        if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"Missing, truncated or changed dependency: {entry['path']}")
    # Load only the already hash-verified, pure-Python packaging wheel. This lets
    # verification run in a clean official Python before installing dependencies.
    package = next(r for r in manifest["wheels"] if r["name"] == "packaging")
    sys.path.insert(0, str(ROOT / package["path"]))
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.tags import compatible_tags, cpython_tags
    from packaging.utils import canonicalize_name, parse_wheel_filename
    from packaging.version import Version

    python = manifest["target"]["python"]
    target = {**default_environment(), "python_version": "3.13", "python_full_version": python,
              "implementation_version": python, "implementation_name": "cpython", "os_name": "nt",
              "sys_platform": "win32", "platform_system": "Windows", "platform_machine": "AMD64",
              "platform_python_implementation": "CPython", "extra": ""}
    tags = set(cpython_tags((3, 13), abis=["cp313"], platforms=["win_amd64"]))
    tags.update(compatible_tags((3, 13), interpreter="cp313", platforms=["win_amd64"]))
    packages = {}
    for entry in manifest["wheels"]:
        path = ROOT / entry["path"]
        name, version, _, wheel_tags = parse_wheel_filename(path.name)
        if not tags.intersection(wheel_tags):
            raise ValueError(f"Incompatible wheel: {path.name}")
        if name in packages:
            raise ValueError(f"Multiple wheels for {name}; refresh into a clean vendor directory")
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"Corrupt wheel member: {path.name}/{bad}")
            metadata = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
            info = BytesParser().parsebytes(archive.read(metadata))
        if canonicalize_name(info["Name"]) != name or Version(info["Version"]) != version:
            raise ValueError(f"Wheel metadata mismatch: {path.name}")
        if info["Requires-Python"] and Version(python) not in SpecifierSet(info["Requires-Python"]):
            raise ValueError(f"Wrong Python version for {name}")
        packages[name] = (version, info.get_all("Requires-Dist", []), entry)

    def check_closure(selected):
        for name in selected:
            for raw in packages[name][1]:
                dependency = Requirement(raw)
                if dependency.marker and not dependency.marker.evaluate(target):
                    continue
                dep = canonicalize_name(dependency.name)
                if dep not in selected or packages[dep][0] not in dependency.specifier:
                    raise ValueError(f"Unresolved target dependency: {name} -> {raw}")

    for lock in ("requirements-runtime.lock", "requirements-build.lock"):
        selected = set()
        for line in (ROOT / lock).read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"([\w.-]+)==([^ ]+) --hash=sha256:([0-9a-f]{64})", line)
            if not match:
                raise ValueError(f"Invalid pinned requirement: {line}")
            name, version, digest = match.groups()
            name = canonicalize_name(name)
            entry = packages[name][2]
            if name in selected or str(packages[name][0]) != version or entry["sha256"] != digest:
                raise ValueError(f"Lock differs from downloaded files: {name}")
            selected.add(name)
        check_closure(selected)
        if lock.endswith("build.lock") and selected != set(packages):
            raise ValueError("Build lock does not cover the entire wheelhouse")

    sys.path.insert(0, str(ROOT))
    from release_files import resource_files
    resources = resource_files()
    icon = (ROOT / "assets/MonitorSolo.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", icon)
    if (reserved, kind) != (0, 1) or count != 9:
        raise ValueError("Expected nine Windows icon resolutions")
    for i in range(count):
        *_, size, offset = struct.unpack_from("<BBBBHHII", icon, 6 + i * 16)
        if size <= 0 or offset + size > len(icon):
            raise ValueError("Truncated ICO image")
    for filename in ("MonitorSolo.spec", "release_files.py"):
        compile((ROOT / filename).read_text(encoding="utf-8"), filename, "exec")
    total = sum(r["bytes"] for r in manifest["wheels"] + manifest["tools"])
    print(f"PASS: {len(packages)} compatible wheels; dependency closure and SHA256 verified.")
    print(f"PASS: 2 installer hashes; {len(resources)} allowlisted resources; 9 icon resolutions.")
    print(f"Downloaded cache: {total:,} bytes ({total / 1024**2:.2f} MiB). No build performed.")


if __name__ == "__main__":
    main()
