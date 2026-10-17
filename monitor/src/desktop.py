"""Windows tray and single-instance activation, on Tk's GUI thread.

The hidden native window receives shell/activation events through the existing
Tk message pump. It never starts another event loop or calls Tk from a worker.
"""
import ctypes
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import time
from queue import SimpleQueue, Empty

from .paths import data_root

SHOW = 0x8001
QUIT = 0x8002
TRAY = 0x8010


def window_class(root=None):
    identity = str(Path(root or data_root()).resolve()).casefold()
    return "BrightMoonMonitorSolo."+hashlib.sha256(identity.encode()).hexdigest()[:24]


def notify_existing(*, quit=False, root=None, timeout=2.):
    if os.name != "nt":
        return False
    user = ctypes.WinDLL("user32",use_last_error=True)
    user.FindWindowW.argtypes = (W.LPCWSTR,W.LPCWSTR)
    user.FindWindowW.restype = W.HWND
    user.PostMessageW.argtypes = (W.HWND,W.UINT,W.WPARAM,W.LPARAM)
    user.PostMessageW.restype = W.BOOL
    user.AllowSetForegroundWindow.argtypes = (W.DWORD,)
    user.GetWindowThreadProcessId.argtypes = (W.HWND,ctypes.POINTER(W.DWORD))
    deadline = time.monotonic()+timeout
    while True:
        hwnd = user.FindWindowW(window_class(root),None)
        if hwnd:
            pid = W.DWORD()
            user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
            user.AllowSetForegroundWindow(pid.value)
            return bool(user.PostMessageW(hwnd,QUIT if quit else SHOW,0,0))
        if time.monotonic() >= deadline:
            return False
        time.sleep(.05)  # Bounded startup race: lock may precede tray creation.


def stop_existing(timeout=8.):
    if not notify_existing(quit=True,timeout=.2):
        return True
    user=ctypes.WinDLL("user32",use_last_error=True)
    user.FindWindowW.argtypes=(W.LPCWSTR,W.LPCWSTR)
    user.FindWindowW.restype=W.HWND
    deadline=time.monotonic()+timeout
    while user.FindWindowW(window_class(),None):
        if time.monotonic()>=deadline:
            return False
        time.sleep(.05)
    return True


class DesktopHost:
    def __init__(self, root, icon_path, on_show, on_quit, *, data_directory=None):
        if os.name != "nt":
            raise OSError("Windows tray unavailable")
        self.root, self.on_show, self.on_quit = root,on_show,on_quit
        self.name = window_class(data_directory)
        self.hwnd = self.icon = None
        self.available = self.closed = False
        self.events = SimpleQueue()
        self.job = None
        self.user = ctypes.WinDLL("user32",use_last_error=True)
        self.shell = ctypes.WinDLL("shell32",use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32",use_last_error=True)
        self._bind()
        self.hinstance = self.kernel.GetModuleHandleW(None)
        self.callback = self.WNDPROC(self._message)
        cls = self.WNDCLASS()
        cls.lpfnWndProc,cls.hInstance,cls.lpszClassName = self.callback,self.hinstance,self.name
        self.atom = self.user.RegisterClassW(ctypes.byref(cls))
        if not self.atom:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.hwnd = self.user.CreateWindowExW(0x80,self.name,self.name,0,0,0,0,0,None,None,self.hinstance,None)
            if not self.hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self.icon = self.user.LoadImageW(None,str(icon_path),1,32,32,0x10)
            if not self.icon:
                raise OSError("无法加载托盘图标")
            self.restart_message = self.user.RegisterWindowMessageW("TaskbarCreated")
            if not self._add_icon():
                raise OSError("无法创建系统托盘图标")
            self.job = root.after(80,self._drain)
        except Exception:
            self.close()
            raise

    def _bind(self):
        self.WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t,W.HWND,W.UINT,W.WPARAM,W.LPARAM)
        class WNDCLASS(ctypes.Structure):
            _fields_ = [("style",W.UINT),("lpfnWndProc",self.WNDPROC),("cbClsExtra",ctypes.c_int),
                ("cbWndExtra",ctypes.c_int),("hInstance",W.HINSTANCE),("hIcon",W.HICON),
                ("hCursor",W.HANDLE),("hbrBackground",W.HBRUSH),("lpszMenuName",W.LPCWSTR),("lpszClassName",W.LPCWSTR)]
        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [("cbSize",W.DWORD),("hWnd",W.HWND),("uID",W.UINT),("uFlags",W.UINT),
                ("uCallbackMessage",W.UINT),("hIcon",W.HICON),("szTip",W.WCHAR*128),
                ("dwState",W.DWORD),("dwStateMask",W.DWORD),("szInfo",W.WCHAR*256),
                ("uVersion",W.UINT),("szInfoTitle",W.WCHAR*64),("dwInfoFlags",W.DWORD),
                ("guidItem",ctypes.c_byte*16),("hBalloonIcon",W.HICON)]
        self.WNDCLASS,self.NOTIFYICONDATA = WNDCLASS,NOTIFYICONDATA
        signatures = {
            "RegisterClassW":([ctypes.POINTER(WNDCLASS)],W.ATOM),
            "CreateWindowExW":([W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.HWND,W.HMENU,W.HINSTANCE,W.LPVOID],W.HWND),
            "DefWindowProcW":([W.HWND,W.UINT,W.WPARAM,W.LPARAM],ctypes.c_ssize_t),
            "LoadImageW":([W.HINSTANCE,W.LPCWSTR,W.UINT,ctypes.c_int,ctypes.c_int,W.UINT],W.HANDLE),
            "RegisterWindowMessageW":([W.LPCWSTR],W.UINT),
            "CreatePopupMenu":([],W.HMENU),
            "AppendMenuW":([W.HMENU,W.UINT,ctypes.c_size_t,W.LPCWSTR],W.BOOL),
            "TrackPopupMenu":([W.HMENU,W.UINT,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.HWND,W.LPVOID],W.UINT),
            "GetCursorPos":([ctypes.POINTER(W.POINT)],W.BOOL),
            "SetForegroundWindow":([W.HWND],W.BOOL),
            "PostMessageW":([W.HWND,W.UINT,W.WPARAM,W.LPARAM],W.BOOL),
            "DestroyMenu":([W.HMENU],W.BOOL),
            "DestroyWindow":([W.HWND],W.BOOL),
            "DestroyIcon":([W.HICON],W.BOOL),
            "UnregisterClassW":([W.LPCWSTR,W.HINSTANCE],W.BOOL),
        }
        for name,(args,result) in signatures.items():
            function = getattr(self.user,name)
            function.argtypes,function.restype = args,result
        self.kernel.GetModuleHandleW.argtypes = [W.LPCWSTR]
        self.kernel.GetModuleHandleW.restype = W.HMODULE
        self.shell.Shell_NotifyIconW.argtypes = [W.DWORD,ctypes.POINTER(NOTIFYICONDATA)]
        self.shell.Shell_NotifyIconW.restype = W.BOOL

    def _data(self):
        data = self.NOTIFYICONDATA()
        data.cbSize,data.hWnd,data.uID = ctypes.sizeof(data),self.hwnd,1
        data.uFlags,data.uCallbackMessage,data.hIcon = 7,TRAY,self.icon
        data.szTip = "Bright Moon Monitor · 双击打开 / 右键退出"
        return data

    def _add_icon(self):
        self.available = bool(self.shell.Shell_NotifyIconW(0,ctypes.byref(self._data())))
        return self.available

    def _menu(self):
        menu = self.user.CreatePopupMenu()
        if not menu:
            return
        try:
            self.user.AppendMenuW(menu,0,1,"打开控制面板")
            self.user.AppendMenuW(menu,0x800,0,None)
            self.user.AppendMenuW(menu,0,2,"退出")
            point = W.POINT()
            self.user.GetCursorPos(ctypes.byref(point))
            self.user.SetForegroundWindow(self.hwnd)
            selected = self.user.TrackPopupMenu(menu,0x100|0x2,point.x,point.y,0,self.hwnd,None)
            self.user.PostMessageW(self.hwnd,0,0,0)
            if selected in (1,2):
                self.events.put("show" if selected==1 else "quit")
        finally:
            self.user.DestroyMenu(menu)

    def _message(self, hwnd, message, wp, lp):
        # Never call Tk (even after_idle) from a ctypes WndProc: _tkinter may
        # have released its Tcl thread state while dispatching Windows events.
        try:
            if message in (SHOW,QUIT,0x10):
                self.events.put("show" if message==SHOW else "quit")
                return 0
            if message==0x11:  # WM_QUERYENDSESSION: never block logout/shutdown.
                return 1
            if message==0x16 and wp:
                self.events.put("quit")
                return 0
            if message==TRAY:
                if lp==0x203:
                    self.events.put("show")
                elif lp in (0x205,0x7B):
                    self._menu()
                return 0
            if message==getattr(self,"restart_message",None):
                if not self._add_icon():
                    self.events.put("show")
                return 0
        except Exception:
            import logging
            logging.exception("Desktop event failed")
        return self.user.DefWindowProcW(hwnd,message,wp,lp)

    def _drain(self):
        self.job = None
        for _ in range(32):
            if self.closed:
                return
            try:
                event = self.events.get_nowait()
            except Empty:
                break
            (self.on_show if event=="show" else self.on_quit)()
        if not self.closed:
            self.job = self.root.after(80,self._drain)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.job:
            self.root.after_cancel(self.job)
            self.job = None
        if self.hwnd:
            self.shell.Shell_NotifyIconW(2,ctypes.byref(self._data()))
            self.user.DestroyWindow(self.hwnd)
            self.hwnd = None
        if self.icon:
            self.user.DestroyIcon(self.icon)
            self.icon = None
        self.user.UnregisterClassW(self.name,self.hinstance)
        self.available = False
