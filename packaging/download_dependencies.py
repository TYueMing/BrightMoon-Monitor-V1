"""Download only: never install tools, freeze an application or run an installer.

Use verify_dependencies.py afterward. Refreshing pins is an intentional release
maintenance operation; ordinary offline builds consume the committed lock files.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parent
PYTHON_VERSION = "3.13.16"
TOOLS = [
    (f"python-{PYTHON_VERSION}-amd64.exe",
     f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe", ""),
    ("innosetup-6.7.3.exe",
     "https://github.com/jrsoftware/issrc/releases/download/is-6_7_3/innosetup-6.7.3.exe",
     "9c73c3bae7ed48d44112a0f48e66742c00090bdb5bef71d9d3c056c66e97b732"),
]


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()


def fetch(url, target, expected=""):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".download")
    with urlopen(url, timeout=60) as response, temporary.open("wb") as stream:
        final_url = response.url
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
    digest = sha256(temporary)
    if expected and digest != expected:
        raise ValueError(f"SHA256 mismatch: {target.name}")
    temporary.replace(target)
    return {"path": target.relative_to(ROOT).as_posix(), "url": url,
            "resolved_url_host": final_url.split('/')[2],
            "sha256": digest, "bytes": target.stat().st_size}


def wheel_record(path):
    with zipfile.ZipFile(path) as archive:
        metadata = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        info = BytesParser().parsebytes(archive.read(metadata))
        name, version = info["Name"], info["Version"]
        with urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60) as response:
            release = json.load(response)
        published = next(f for f in release["urls"] if f["filename"] == path.name)
        digest = sha256(path)
        if digest != published["digests"]["sha256"]:
            raise ValueError(f"PyPI SHA256 mismatch: {path.name}")
        for filename in archive.namelist():
            # Retain license texts from wheels without extracting executable code.
            base = filename.rsplit("/", 1)[-1]
            if ".dist-info/" in filename and ("license" in filename.lower() or base.lower().startswith(("copying", "notice", "authors"))) and not filename.endswith("/"):
                relative = filename.split(".dist-info/", 1)[1]
                target = ROOT / "licenses" / f"{name}-{version}" / relative
                if not target.resolve().is_relative_to((ROOT / "licenses").resolve()):
                    raise ValueError("Unsafe license path")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(filename))
        return {"path": path.relative_to(ROOT).as_posix(), "name": name, "version": version,
                "url": published["url"], "sha256": digest, "bytes": path.stat().st_size,
                "requires_python": info["Requires-Python"], "requires_dist": info.get_all("Requires-Dist", [])}


def main():
    wheels = ROOT / "vendor" / "wheels"
    wheels.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
                    "--no-cache-dir", "--index-url", "https://pypi.org/simple",
                    "--only-binary=:all:", "--platform", "win_amd64", "--implementation", "cp",
                    "--python-version", "3.13", "--abi", "cp313", "--dest", str(wheels),
                    "-r", str(ROOT / "build.in")], check=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(wheel_record, sorted(wheels.glob("*.whl"))))
        futures = [pool.submit(fetch, url, ROOT / "vendor" / "tools" / name, digest)
                   for name, url, digest in TOOLS]
        tools = [future.result() for future in futures]
    licenses = [
        fetch(f"https://raw.githubusercontent.com/python/cpython/v{PYTHON_VERSION}/LICENSE",
              ROOT / "licenses" / "Python-LICENSE.txt"),
        fetch("https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/license.txt",
              ROOT / "licenses" / "Inno-Setup-LICENSE.txt"),
        fetch(f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe.spdx.json",
              ROOT / "licenses" / "python-installer.spdx.json"),
        fetch("https://raw.githubusercontent.com/jrsoftware/issrc/is-6_7_3/Files/Languages/Unofficial/ChineseSimplified.isl",
              ROOT / "languages" / "ChineseSimplified.isl"),
        fetch("https://raw.githubusercontent.com/python/cpython-source-deps/tcl-core-8.6.15.0/license.terms",
              ROOT / "licenses" / "Tcl-8.6.15-LICENSE.txt"),
        fetch("https://raw.githubusercontent.com/python/cpython-source-deps/tk-8.6.15.0/license.terms",
              ROOT / "licenses" / "Tk-8.6.15-LICENSE.txt"),
    ]
    # Include the Web server's transitive runtime dependencies, not only roots.
    runtime = {"pillow", "zstandard", "websocket-client", "aiohttp", "aiohappyeyeballs",
               "aiosignal", "attrs", "frozenlist", "multidict", "propcache", "yarl", "idna"}
    for filename, selected in (("requirements-runtime.lock", [r for r in records if r['name'].lower() in runtime]),
                               ("requirements-build.lock", records)):
        lines = ["# CPython 3.13 / Windows x64; install with --no-index --require-hashes."]
        lines.extend(f"{r['name']}=={r['version']} --hash=sha256:{r['sha256']}" for r in selected)
        (ROOT / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")
    manifest = {"schema": 1, "prepared_at": datetime.now(timezone.utc).isoformat(),
                "target": {"os": "Windows", "arch": "AMD64", "python": PYTHON_VERSION,
                           "abi": "cp313", "pyinstaller": "6.22.3", "inno_setup": "6.7.3"},
                "wheels": records, "tools": tools, "license_sources": licenses}
    (ROOT / "dependency-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Downloaded {len(records)} wheels and {len(tools)} installers. Nothing installed or built.")


if __name__ == "__main__":
    main()
