"""Local install/upgrade/uninstall acceptance using a disposable app profile.

Refuses to replace an existing installation or startup entry. This is not a
clean-VM test: it verifies the real installer and EXE on the build machine.
"""
import argparse
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
import winreg

UNINSTALL=r"Software\Microsoft\Windows\CurrentVersion\Uninstall\{CE50658E-62AC-48E1-8C15-D734E8F10150}_is1"
RUN=r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME="BrightMoonMonitorSolo"


def registry_value(key,name):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,key) as handle:
            return winreg.QueryValueEx(handle,name)[0]
    except FileNotFoundError:
        return None


def wait_until(condition,timeout=15):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if condition(): return
        time.sleep(.1)
    raise AssertionError("Timed out waiting for lifecycle transition")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("installer",type=Path)
    parser.add_argument("work",type=Path)
    args=parser.parse_args()
    work=args.work.resolve()
    allowed=Path(__file__).resolve().parents[1]/".artifacts"
    if not work.is_relative_to(allowed) or work.exists():
        raise ValueError("Use a new directory within Solo .artifacts")
    if registry_value(UNINSTALL,"InstallLocation") or registry_value(RUN,NAME):
        raise RuntimeError("Existing installed Solo/startup entry: isolated account required")
    desktop=ctypes.create_unicode_buffer(32768)
    if ctypes.windll.shell32.SHGetFolderPathW(None,0x10,None,0,desktop):
        raise OSError("Cannot resolve desktop")
    shortcut=Path(desktop.value)/"Bright Moon Monitor (Solo).lnk"
    if shortcut.exists():
        raise RuntimeError("Existing desktop shortcut; do not overwrite it")
    work.mkdir(parents=True)
    install=work/"安装目录 with spaces"
    profile=work/"测试用户"/"Local"
    data=profile/"MonitorSolo"/"data"
    data.mkdir(parents=True)
    marker=data/"keep-user-data.txt"
    marker.write_text("retain after upgrade and uninstall",encoding="utf-8")
    (data/"settings.json").write_text(json.dumps({"monitor_hide_minutes":7,"monitor_sound_enabled":False,
        "task_sound_enabled":False}),encoding="utf-8")
    env=os.environ.copy()
    env["LOCALAPPDATA"]=str(profile)
    system_root=os.environ["SystemRoot"]
    env["PATH"]=str(Path(system_root)/"System32")+os.pathsep+system_root
    for name in ("TCL_LIBRARY","TK_LIBRARY","PYTHONHOME","PYTHONPATH","CONDA_PREFIX","CONDA_DEFAULT_ENV"):
        env.pop(name,None)
    user=ctypes.WinDLL("user32",use_last_error=True)
    user.FindWindowW.argtypes=(W.LPCWSTR,W.LPCWSTR)
    user.FindWindowW.restype=W.HWND
    user.PostMessageW.argtypes=(W.HWND,W.UINT,W.WPARAM,W.LPARAM)
    user.IsWindowVisible.argtypes=(W.HWND,)
    user.GetWindowThreadProcessId.argtypes=(W.HWND,ctypes.POINTER(W.DWORD))
    callback_type=ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    user.EnumWindows.argtypes=(callback_type,W.LPARAM)
    identity="BrightMoonMonitorSolo."+hashlib.sha256(str(data.resolve()).casefold().encode()).hexdigest()[:24]
    host=lambda:user.FindWindowW(identity,None)
    def visible_windows(pid):
        windows=[]
        @callback_type
        def callback(hwnd,_):
            owner=W.DWORD()
            user.GetWindowThreadProcessId(hwnd,ctypes.byref(owner))
            if owner.value==pid and user.IsWindowVisible(hwnd): windows.append(hwnd)
            return True
        user.EnumWindows(callback,0)
        return windows
    def run(command,timeout=90):
        result=subprocess.run([str(x) for x in command],env=env,cwd=work,timeout=timeout,
                              creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode==0,(command,result.returncode)
    installer=args.installer.resolve()
    exe=install/"MonitorSolo.exe"
    process=None
    report={"status":"failed","environment":"local Windows host, clean PATH and isolated data; not a clean VM","checks":[]}
    try:
        run([installer,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART",f"/DIR={install}","/MERGETASKS=desktopicon",f"/LOG={work/'install.log'}"])
        assert exe.is_file() and (install/"unins000.exe").is_file() and shortcut.is_file()
        assert Path(registry_value(UNINSTALL,"InstallLocation")).resolve()==install
        report["checks"].append("installer_desktop_shortcut_uninstaller")
        run([exe,"--self-test",work/"installed-smoke.json"])
        smoke=json.loads((work/"installed-smoke.json").read_text(encoding="utf-8"))
        assert smoke["status"]=="passed",smoke
        report["checks"].append("installed_runtime_smoke")
        process=subprocess.Popen([str(exe),"--background"],env=env,cwd=work,creationflags=subprocess.CREATE_NO_WINDOW)
        wait_until(host)
        assert not visible_windows(process.pid)
        run([exe])  # Second launch exits and asks the original instance to show.
        wait_until(lambda:visible_windows(process.pid))
        assert process.poll() is None
        main_window=visible_windows(process.pid)[0]
        user.PostMessageW(main_window,0x10,0,0)
        wait_until(lambda:not visible_windows(process.pid))
        assert process.poll() is None and host()
        report["checks"].append("background_start_relaunch_and_close_to_tray")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,RUN) as key:
            winreg.SetValueEx(key,NAME,0,winreg.REG_SZ,f'"{exe}" --background')
        run([installer,"/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART",f"/DIR={install}",f"/LOG={work/'upgrade.log'}"])
        wait_until(lambda:process.poll() is not None)
        assert marker.is_file() and json.loads((data/"settings.json").read_text(encoding="utf-8"))["monitor_hide_minutes"]==7
        assert registry_value(RUN,NAME)==f'"{exe}" --background'
        report["checks"].append("upgrade_graceful_shutdown_data_and_startup_preserved")
        process=subprocess.Popen([str(exe),"--background"],env=env,cwd=work,creationflags=subprocess.CREATE_NO_WINDOW)
        wait_until(host)
        run([install/"unins000.exe","/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART",f"/LOG={work/'uninstall.log'}"])
        wait_until(lambda:process.poll() is not None and not exe.exists() and not shortcut.exists())
        # Inno's uninstaller may finish registry cleanup after its initial
        # bootstrap process exits; wait for the complete observable result.
        wait_until(lambda:registry_value(RUN,NAME) is None and registry_value(UNINSTALL,"InstallLocation") is None)
        assert registry_value(RUN,NAME) is None and registry_value(UNINSTALL,"InstallLocation") is None
        assert marker.is_file() and (data/"settings.json").is_file()
        report["checks"].append("uninstall_exits_removes_shortcut_startup_preserves_data")
        report["status"]="passed"
    except Exception:
        report["error"]=traceback.format_exc()
    finally:
        if process and process.poll() is None and exe.exists():
            run([exe,"--quit"])
            process.wait(timeout=15)
        # Remove only the temporary startup command created by this validation.
        if registry_value(RUN,NAME)==f'"{exe}" --background':
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,RUN,0,winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key,NAME)
        (work/"validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["status"]=="passed" else 1


if __name__=="__main__":
    raise SystemExit(main())
