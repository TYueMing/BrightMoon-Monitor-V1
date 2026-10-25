@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "SOLO_PYTHON=".venv\Scripts\python.exe""
    goto run
)
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        set "SOLO_PYTHON=py -%%V"
        goto run
    )
)
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.10 or newer with Tkinter, then try again.
    pause
    exit /b 1
)
set "SOLO_PYTHON=python"
:run
rem run.py checks every selected interpreter, including .venv and PATH.
%SOLO_PYTHON% run.py %*
set "SOLO_EXIT=%ERRORLEVEL%"
if not "%SOLO_EXIT%"=="0" pause
exit /b %SOLO_EXIT%
