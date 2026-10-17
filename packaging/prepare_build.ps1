param([Parameter(Mandatory=$true)][string]$PythonExe)
$ErrorActionPreference = 'Stop'
$soloRoot = Split-Path -Parent $PSScriptRoot
$release = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$detected = & $PythonExe -I -c 'import platform,sys; print(platform.python_version()+"/"+platform.machine()+"/"+sys.platform)'
if ($LASTEXITCODE -ne 0 -or $detected.Trim() -ne "$($release.python)/AMD64/win32") {
    throw "Use official CPython $($release.python) x64 on Windows. Detected: $detected"
}
& $PythonExe -I (Join-Path $PSScriptRoot 'verify_dependencies.py')
if ($LASTEXITCODE -ne 0) { throw 'Dependency verification failed.' }
& (Join-Path $PSScriptRoot 'verify_signatures.ps1')
# Do not reuse a developer's Conda environment or copy installed site-packages.
$venv = Join-Path $soloRoot '.build-venv'
& $PythonExe -I -m venv $venv
if ($LASTEXITCODE -ne 0) { throw 'Cannot create private build environment.' }
$buildPython = Join-Path $venv 'Scripts\python.exe'
& $buildPython -I -m pip install --disable-pip-version-check --no-index --only-binary=:all: --require-hashes --find-links (Join-Path $PSScriptRoot 'vendor\wheels') -r (Join-Path $PSScriptRoot 'requirements-build.lock')
if ($LASTEXITCODE -ne 0) { throw 'Offline dependency installation failed.' }
& $buildPython -I -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Dependency conflict.' }
# Remove inherited Conda Tcl paths only in this process; never change the system.
Remove-Item Env:TCL_LIBRARY -ErrorAction SilentlyContinue
Remove-Item Env:TK_LIBRARY -ErrorAction SilentlyContinue
& $buildPython -I -c 'import tkinter,sqlite3,ctypes,PIL,zstandard,websocket; t=tkinter.Tk(); t.withdraw(); print("Tcl/Tk", t.tk.call("info","patchlevel")); t.destroy()'
if ($LASTEXITCODE -ne 0) { throw 'Python must include functioning Tcl/Tk support.' }
Write-Output 'Private offline build environment ready. No application or installer was built.'
