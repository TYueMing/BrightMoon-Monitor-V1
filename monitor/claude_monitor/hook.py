"""Short observational hooks, with an optional bounded human decision bridge.

No model calls, no network listener and no dependency on a system Python package.
The installed command passes an explicit mailbox path; stdout is reserved for
Claude's official hook response JSON. All failures leave the original flow alone.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from .events import normalize
from . import spool


def handle(payload, root, timeout=60):
    event=normalize(payload)
    alive, approvals=spool.active(root)
    if not alive:
        return None  # Disabled/closed monitor must not add work or latency.
    from .process import claude_parent
    event['process'] = claude_parent()
    name=event['hook_event_name']
    if name=='UserPromptSubmit' or name=='SessionEnd':
        spool.expire_session(root,event['session_id'])
    elif name in ('Stop','StopFailure'):
        spool.expire_session(root,event['session_id'],event.get('prompt_id') or None)
    # Parameters that cannot be shown in full require the original Claude UI.
    controllable=(name=='PermissionRequest' and approvals and not event.get('truncated')
                  and event.get('tool_name') not in ('AskUserQuestion','ExitPlanMode'))
    if not controllable:
        spool.append_event(event,root)
        return None
    request=spool.new_request(root,event,timeout)
    event['request']=request
    spool.append_event(event,root)
    deadline=time.monotonic()+timeout
    try:
        while time.monotonic()<deadline:
            if not spool.active(root)[1]:return None
            answer=spool.consume(root,request['id'])
            if answer:
                decision={'behavior':answer}
                if answer=='deny':decision['message']='用户通过 Bright Moon Monitor 拒绝了本次操作'
                return {'hookSpecificOutput':{'hookEventName':'PermissionRequest','decision':decision}}
            state=spool.requests(root).get(request['id'],{})
            if state.get('state')=='expired':return None
            time.sleep(.15)
    finally:
        spool.expire(root,request['id'])
    return None


def restore_pipes():
    """PyInstaller windowed applications may set stdio to None.

    Claude launches the helper with inherited pipes. Recover those handles
    before reading hook JSON; never attach a console or start the desktop UI.
    """
    import os
    if os.name != 'nt' or (sys.stdin is not None and sys.stdout is not None):
        return
    import ctypes
    import io
    import msvcrt
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetStdHandle.argtypes=(ctypes.c_ulong,)
    kernel.GetStdHandle.restype=ctypes.c_void_p
    for attr,code,flags,mode in [('stdin',-10,os.O_RDONLY,'rb'),('stdout',-11,os.O_WRONLY,'wb')]:
        if getattr(sys,attr) is not None:continue
        handle=kernel.GetStdHandle(code & 0xffffffff)
        if handle in (None,ctypes.c_void_p(-1).value):raise OSError('Hook pipe unavailable')
        fd=msvcrt.open_osfhandle(handle,flags|os.O_BINARY)
        setattr(sys,attr,io.TextIOWrapper(os.fdopen(fd,mode),encoding='utf-8'))


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--spool',required=True)
    parser.add_argument('--wait',type=float,default=60)
    parser.add_argument('--monitor-solo-claude-v1',action='store_true')
    args=parser.parse_args(argv)
    try:
        restore_pipes()
        # Bound malformed/huge tool inputs before parsing or retaining anything.
        data=sys.stdin.buffer.read(1024*1024+1)
        if len(data)>1024*1024:return 0
        payload=json.loads(data.decode('utf-8-sig'))
        if not isinstance(payload,dict):return 0
        result=handle(payload,Path(args.spool),min(120,max(1,args.wait)))
        if result:
            sys.stdout.write(json.dumps(result,ensure_ascii=True))
            sys.stdout.flush()
    except (OSError,ValueError,TypeError,KeyError,RecursionError,sqlite3.Error):
        # Monitor failure must never become a Claude tool failure.
        return 0
    return 0


if __name__=='__main__':
    raise SystemExit(main())
