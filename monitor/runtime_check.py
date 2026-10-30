"""Check the active interpreter before importing the GUI; never install packages.

Source requirements are authoritative. Frozen apps carry their own runtime and
need import checks, without depending on optional distribution metadata.
"""
import importlib
from importlib import metadata
from pathlib import Path
import re
import sys

MODULES = ("tkinter", "PIL.Image", "zstandard", "websocket", "aiohttp",
           "cryptography.hazmat.primitives.serialization", "tzdata", "tzlocal", "serial")


def dependency_errors():
    errors = []
    if sys.version_info < (3, 10):
        errors.append("Python 3.10 or newer is required.")
    if not getattr(sys, "frozen", False):
        path = Path(__file__).resolve().parents[1] / "requirements.txt"
        try:
            requirements = path.read_text(encoding="utf-8-sig").splitlines()
        except OSError:
            errors.append("Missing requirements.txt; obtain a complete source download.")
            requirements = []
        for line in requirements:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            match = re.fullmatch(r"([A-Za-z0-9_.-]+)(>=\d+(?:\.\d+)*(?:,<\d+(?:\.\d+)*)?)", line)
            if not match:
                errors.append("Unsupported requirement in runtime check: " + line)
                continue
            name, limits = match.groups()
            try:
                installed = metadata.version(name)
            except metadata.PackageNotFoundError:
                errors.append(name + ": not installed (requires " + limits + ")")
                continue
            # Project requirements use numeric stable-release bounds. Do not
            # silently accept a prerelease or an unrecognised requirement.
            if not re.fullmatch(r"\d+(?:\.\d+)*(?:\+[A-Za-z0-9.]+)?", installed):
                errors.append(name + ": unsupported version " + installed)
                continue
            version = tuple(map(int, installed.split("+", 1)[0].split(".")))
            for operator, bound in re.findall(r"(>=|<)(\d+(?:\.\d+)*)", limits):
                wanted = tuple(map(int, bound.split(".")))
                size = max(len(version), len(wanted))
                a, b = version + (0,)*(size-len(version)), wanted + (0,)*(size-len(wanted))
                if (operator == ">=" and a < b) or (operator == "<" and a >= b):
                    errors.append(name + ": " + installed + " installed; requires " + limits)
                    break
    for name in MODULES:
        try:
            module = importlib.import_module(name)
            if name == "aiohttp" and not hasattr(module, "ClientWSTimeout"):
                errors.append("aiohttp.ClientWSTimeout is unavailable in the loaded package.")
        except Exception as error:
            errors.append(name + ": " + str(error))
    return errors


def ensure_runtime():
    errors = dependency_errors()
    if not errors:
        return True
    message = "Monitor Solo cannot start in this Python environment:\n\n" + "\n".join(errors)
    if getattr(sys, "frozen", False):
        message += "\n\nPlease reinstall the complete Monitor Solo application."
    else:
        # PowerShell quoting handles spaces/apostrophes in profile names.
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        executable = Path(sys.executable)
        if executable.name.lower() == "pythonw.exe":
            executable = executable.with_name("python.exe")
        requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
        message += "\n\nRun in PowerShell:\n& " + quote(executable) + " -m pip install -r " + quote(requirements)
        message += "\n\nInstall Tkinter through your Python/Conda distribution if it is missing."
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    else:
        # pythonw has no console; this message must not depend on Tk.
        import os
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Monitor Solo", 0x10)
    return False
