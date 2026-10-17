"""Startup uses isolated registry values; tray uses a temporary instance identity."""
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from monitor.src.startup import StartupManager, RUN_KEY, APPROVED_KEY
from monitor.src.desktop import DesktopHost, notify_existing, TRAY
from test_wallpapers import native_app
from test_rope_personalization import settle
from test_resident_island import empty_source


class Registry:
    HKEY_CURRENT_USER=1
    KEY_READ=1
    KEY_SET_VALUE=2
    REG_SZ=1
    def __init__(self):
        self.values={}
    class Key:
        def __init__(self,name): self.name=name
        def __enter__(self): return self.name
        def __exit__(self,*args): pass
    def OpenKey(self,root,key,*args): return self.Key(key)
    CreateKeyEx=OpenKey
    def QueryValueEx(self,key,name):
        if (key,name) not in self.values: raise FileNotFoundError()
        return self.values[key,name],1
    def SetValueEx(self,key,name,unused,kind,value): self.values[key,name]=value
    def DeleteValue(self,key,name):
        if (key,name) not in self.values: raise FileNotFoundError()
        del self.values[key,name]


def test_startup_quote_authoritative_state_disable_and_other_entries():
    registry=Registry()
    manager=StartupManager(registry=registry,executable="C:/Apps with spaces/Solo/MonitorSolo.exe",installed=True)
    assert manager.status()[0] is False
    registry.values[RUN_KEY,"OtherApp"]="preserve"
    manager.set_enabled(True)
    assert manager.command.startswith('"') and manager.command.endswith('" --background')
    assert manager.status()[0]
    registry.values[APPROVED_KEY,manager.value_name]=bytes([3])+bytes(11)
    assert not manager.status()[0]
    manager.set_enabled(True)
    assert manager.status()[0]
    manager.set_enabled(False)
    assert not manager.status()[0] and registry.values=={(RUN_KEY,"OtherApp"):"preserve"}
    with pytest.raises(ValueError): manager.set_enabled("true")


def test_startup_failure_is_not_reported_as_enabled():
    registry=Registry()
    manager=StartupManager(registry=registry,installed=True)
    registry.SetValueEx=Mock(side_effect=PermissionError("fixture denied"))
    with pytest.raises(OSError): manager.set_enabled(True)
    assert not manager.status()[0]


def test_native_tray_hide_activate_menu_dispatch_recovery_and_exit(native_app,monkeypatch):
    app,tmp=native_app
    empty_source(app)
    app.toggle_resident(True)
    settle(app.island)
    app.desktop=DesktopHost(app.root,Path(__file__).resolve().parents[1]/"packaging/assets/MonitorSolo.ico",
        app.show_control_panel,app.close,data_directory=tmp/"native-identity")
    host=app.desktop
    assert host.available
    app.root.tk.call(app.root.protocol("WM_DELETE_WINDOW"))
    assert app.root.state()=="withdrawn" and not app.closed and not app.island._hidden
    assert notify_existing(root=tmp/"native-identity")
    deadline=time.monotonic()+.3
    while time.monotonic()<deadline:
        app.root.update()
        time.sleep(.01)
    assert app.root.state()=="normal"
    # Native right-click dispatch and menu selection take the same queued path.
    menu=Mock()
    monkeypatch.setattr(host,"_menu",menu)
    host._message(host.hwnd,TRAY,0,0x205)
    menu.assert_called_once()
    host.shell.Shell_NotifyIconW(2,__import__('ctypes').byref(host._data()))
    host._message(host.hwnd,host.restart_message,0,0)
    assert host.available  # Explorer restart restores its icon.
    assert notify_existing(quit=True,root=tmp/"native-identity")
    deadline=time.monotonic()+.3
    while time.monotonic()<deadline and not app.closed:
        app.root.update()
        time.sleep(.01)
    assert app.closed and not host.available


def test_general_startup_switch_and_tray_failure_remain_reachable(native_app):
    app,_=native_app
    app.startup=StartupManager(registry=Registry(),installed=True)
    app.panel.select_personalization("general")
    app.panel.controls["startup-enabled"].callback()
    assert app.startup.status()[0] and app.startup_enabled
    app.panel.controls["startup-enabled"].callback()
    assert not app.startup.status()[0]
    app.hide_to_tray()
    assert app.root.state()=="iconic" and not app.closed
    app.show_control_panel()
    assert app.root.state()=="normal"
