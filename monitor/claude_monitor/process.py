"""Optional Windows process evidence; no command-line or credential inspection."""
import os


def process_birth(pid):
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes as W
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=(W.DWORD,W.BOOL,W.DWORD);k.OpenProcess.restype=W.HANDLE
    k.CloseHandle.argtypes=(W.HANDLE,)
    k.GetProcessTimes.argtypes=(W.HANDLE,ctypes.POINTER(W.FILETIME),ctypes.POINTER(W.FILETIME),ctypes.POINTER(W.FILETIME),ctypes.POINTER(W.FILETIME))
    handle=k.OpenProcess(0x1000,False,int(pid))
    if not handle:return False if ctypes.get_last_error()==87 else None
    try:
        born,exit,kernel,user=(W.FILETIME() for _ in range(4))
        if not k.GetProcessTimes(handle,ctypes.byref(born),ctypes.byref(exit),ctypes.byref(kernel),ctypes.byref(user)):return None
        if exit.dwHighDateTime or exit.dwLowDateTime:return False
        return str((born.dwHighDateTime<<32)|born.dwLowDateTime)
    finally:k.CloseHandle(handle)


def claude_parent():
    if os.name!='nt':return {}
    import ctypes
    from ctypes import wintypes as W
    class Entry(ctypes.Structure):
        _fields_=[('dwSize',W.DWORD),('cntUsage',W.DWORD),('pid',W.DWORD),('heap',ctypes.c_size_t),
                  ('module',W.DWORD),('threads',W.DWORD),('parent',W.DWORD),('priority',W.LONG),
                  ('flags',W.DWORD),('exe',W.WCHAR*260)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateToolhelp32Snapshot.argtypes=(W.DWORD,W.DWORD);k.CreateToolhelp32Snapshot.restype=W.HANDLE
    k.Process32FirstW.argtypes=(W.HANDLE,ctypes.POINTER(Entry));k.Process32NextW.argtypes=k.Process32FirstW.argtypes
    k.CloseHandle.argtypes=(W.HANDLE,)
    handle=k.CreateToolhelp32Snapshot(2,0)
    if handle in (None,ctypes.c_void_p(-1).value):return {}
    processes={};entry=Entry();entry.dwSize=ctypes.sizeof(entry)
    try:
        ok=k.Process32FirstW(handle,ctypes.byref(entry))
        while ok:
            processes[entry.pid]=(entry.parent,entry.exe.lower())
            ok=k.Process32NextW(handle,ctypes.byref(entry))
    finally:k.CloseHandle(handle)
    pid=os.getpid()
    for _ in range(16):
        parent,name=processes.get(pid,(0,''))
        if name in ('claude.exe','claude-code.exe'):
            birth=process_birth(pid)
            return {'pid':pid,'birth':birth} if birth else {}
        if not parent or parent==pid:break
        pid=parent
    return {}


def alive(evidence):
    if not isinstance(evidence,dict) or type(evidence.get('pid')) is not int or not isinstance(evidence.get('birth'),str):return None
    current=process_birth(evidence['pid'])
    return None if current is None else bool(current and current==evidence['birth'])
