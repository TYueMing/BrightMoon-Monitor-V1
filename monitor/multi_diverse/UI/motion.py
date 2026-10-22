"""Honor Windows' client-area animation preference for the new split shell."""
import ctypes
import os


def reduced_motion():
    if os.name!='nt':return False
    enabled=ctypes.c_int(1)
    return bool(ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(enabled),0) and not enabled.value)
