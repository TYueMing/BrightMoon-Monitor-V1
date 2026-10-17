# Windows x64 onedir build. This file is configuration, not an executed build.
from pathlib import Path
import sys

packaging_root = Path(SPECPATH)
project_root = packaging_root.parent
sys.path.insert(0, str(packaging_root))
from release_files import resource_files, version_info

a = Analysis(
    [str(project_root / "run.py")], pathex=[str(project_root)],
    binaries=[], datas=resource_files(),
    hiddenimports=["PIL._tkinter_finder", "zstandard.backend_c"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "_pytest", "unittest", "pip", "setuptools", "IPython", "numpy", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True,
    name="MonitorSolo", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    icon=str(packaging_root / "assets" / "MonitorSolo.ico"),
    version=version_info(), contents_directory="_internal",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="MonitorSolo")
