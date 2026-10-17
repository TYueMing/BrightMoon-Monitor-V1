"""Current-user login startup. Windows registration is the source of truth."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys

from .paths import PROJECT_ROOT, data_root, is_installed

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
VALUE_NAME = "BrightMoonMonitorSolo"


class StartupManager:
    def __init__(self, *, registry=None, executable=None, installed=None, value_name=None):
        if registry is None and os.name == "nt":
            import winreg
            registry = winreg
        self.registry = registry
        installed = is_installed() if installed is None else installed
        executable = Path(executable or sys.executable).resolve()
        if not installed and executable.name.lower() == "python.exe":
            executable = executable.with_name("pythonw.exe")
        args = [str(executable)]
        if not installed:
            args.append(str(PROJECT_ROOT/"run.py"))
        args.append("--background")
        # Match the installer's upgrade command even for paths without spaces.
        self.command = '"'+str(executable)+'" '+subprocess.list2cmdline(args[1:])
        suffix = hashlib.sha256(str(data_root()).encode()).hexdigest()[:12]
        self.value_name = value_name or (VALUE_NAME if installed else VALUE_NAME+"-dev-"+suffix)

    def status(self):
        if self.registry is None:
            return False, "此功能仅支持 Windows"
        reg = self.registry
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER,RUN_KEY,0,reg.KEY_READ) as key:
                command,_ = reg.QueryValueEx(key,self.value_name)
            if command != self.command:
                return False, "未开启"
            try:
                with reg.OpenKey(reg.HKEY_CURRENT_USER,APPROVED_KEY,0,reg.KEY_READ) as key:
                    value,_ = reg.QueryValueEx(key,self.value_name)
                if isinstance(value,bytes) and value and value[0] in (3,7):
                    return False, "已被 Windows 启动管理禁用，可重新开启"
            except FileNotFoundError:
                pass
            return True, "登录 Windows 后在后台启动"
        except FileNotFoundError:
            return False, "未开启"
        except OSError:
            return False, "无法读取系统启动项"

    def set_enabled(self, enabled):
        if type(enabled) is not bool or self.registry is None:
            raise ValueError("开机自启仅支持 Windows 布尔开关")
        reg = self.registry
        # Touch only this app's current-user value, never other startup entries.
        with reg.CreateKeyEx(reg.HKEY_CURRENT_USER,RUN_KEY,0,reg.KEY_SET_VALUE) as key:
            if enabled:
                reg.SetValueEx(key,self.value_name,0,reg.REG_SZ,self.command)
            else:
                try:
                    reg.DeleteValue(key,self.value_name)
                except FileNotFoundError:
                    pass
        try:
            with reg.OpenKey(reg.HKEY_CURRENT_USER,APPROVED_KEY,0,reg.KEY_SET_VALUE) as key:
                reg.DeleteValue(key,self.value_name)
        except FileNotFoundError:
            pass
        actual,message = self.status()
        if actual != enabled:
            raise OSError(message)
