@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py
    if errorlevel 1 pause
    exit /b
)
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
    py -%%V -c "import sys" >nul 2>nul
    if not errorlevel 1 (
        py -%%V -c "import tkinter, PIL, zstandard, websocket" >nul 2>nul
        if errorlevel 1 (
            echo Install dependencies with: py -%%V -m pip install -r requirements.txt
            pause
            exit /b 1
        )
        py -%%V run.py
        if errorlevel 1 pause
        exit /b
    )
)
python -c "import sys; assert sys.version_info >= (3,10)" >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.10 or newer with Tkinter, then try again.
    pause
    exit /b 1
)
python run.py
if errorlevel 1 pause
