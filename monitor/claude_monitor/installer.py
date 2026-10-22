"""Explicit, reversible installation of owned command hooks; no policy changes."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import sys
from uuid import uuid4
from .events import EVENTS
from ..src.source_config import claude_event_root

MARKER='--monitor-solo-claude-v1'


def settings_path(path=None):
    return Path(path).expanduser() if path else Path(os.environ.get('CLAUDE_CONFIG_DIR') or Path.home()/'.claude')/'settings.json'


def hook_command(spool=None, interpreter=None):
    executable=Path(interpreter or sys.executable)
    # pythonw has no reliable stdin/stdout for command-hook decision JSON.
    if executable.name.lower()=='pythonw.exe':executable=executable.with_name('python.exe')
    if not executable.is_file():raise ValueError('没有找到 Hook 使用的 Python 解释器')
    entry=Path(__file__).with_name('hook_entry.py').resolve()
    root=Path(spool or claude_event_root({})).resolve()
    # Claude Windows command hooks run through its shell. Forward slashes and
    # POSIX quoting also handle spaces, Unicode, quotes and shell metacharacters.
    prefix = (executable.as_posix(),'--claude-hook') if getattr(sys,'frozen',False) else (executable.as_posix(),entry.as_posix())
    return ' '.join(shlex.quote(x) for x in (*prefix,'--spool',root.as_posix(),MARKER))


def _load(path):
    try:
        raw=path.read_bytes()
        value=json.loads(raw.decode('utf-8-sig'))
    except FileNotFoundError:return {},None
    if not isinstance(value,dict) or ('hooks' in value and not isinstance(value['hooks'],dict)):
        raise ValueError('Claude 设置结构无效，未作修改')
    for rows in value.get('hooks',{}).values():
        if not isinstance(rows,list) or any(not isinstance(row,dict) or not isinstance(row.get('hooks'),list) for row in rows):
            raise ValueError('Claude Hook 配置结构无效，未作修改')
    return value,raw


def owned(hook):
    if not isinstance(hook,dict) or hook.get('type')!='command':return False
    try:args=shlex.split(hook.get('command',''))
    except ValueError:return False
    return MARKER in args and (any(a.endswith('/claude_monitor/hook_entry.py') for a in args)
                              or ('--claude-hook' in args and args[0].lower().endswith('/monitorsolo.exe')))


def strip_owned(hooks):
    for event,rows in list(hooks.items()):
        new=[];changed=False
        for row in rows:
            if not any(owned(h) for h in row['hooks']):
                new.append(row)
                continue
            changed=True
            children=[h for h in row['hooks'] if not owned(h)]
            if children:new.append({**row,'hooks':children})
        if not changed:continue
        if new:hooks[event]=new
        else:hooks.pop(event)


def _write(path,value,original):
    path.parent.mkdir(parents=True,exist_ok=True)
    # Detect a concurrent settings edit instead of silently replacing it.
    current=path.read_bytes() if path.exists() else None
    if current!=original:raise ValueError('Claude 设置刚被修改，请重试')
    if original is not None:
        backup=path.with_name(path.name+'.solo-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+uuid4().hex[:6])
        backup.write_bytes(original)
    temp=path.with_name(path.name+'.'+uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        os.replace(temp,path)
    finally:temp.unlink(missing_ok=True)


def install(path=None,spool=None,events=EVENTS):
    path=settings_path(path);value,raw=_load(path)
    if value.get('disableAllHooks') is True or value.get('allowManagedHooksOnly') is True:
        raise ValueError('Claude 当前禁止用户 Hook；请在 Claude/组织策略中核对')
    command=hook_command(spool)
    hooks=value.setdefault('hooks',{});strip_owned(hooks)
    for event in events:
        if event not in EVENTS:raise ValueError('未知 Hook 事件')
        group={'hooks':[dict(type='command',command=command,timeout=65 if event=='PermissionRequest' else 3)]}
        if event in ('PreToolUse','PermissionRequest','PostToolUse','PostToolUseFailure','PermissionDenied',
                     'Notification','PreCompact','PostCompact','SubagentStart','SubagentStop','SessionStart','SessionEnd','StopFailure'):
            group['matcher']='*'
        hooks.setdefault(event,[]).append(group)
    _write(path,value,raw)
    return path


def uninstall(path=None):
    path=settings_path(path);value,raw=_load(path)
    if raw is None:return path
    before=json.dumps(value,sort_keys=True)
    strip_owned(value.get('hooks',{}))
    if not value.get('hooks'):value.pop('hooks',None)
    if json.dumps(value,sort_keys=True)!=before:_write(path,value,raw)
    return path


def status(path=None,spool=None):
    try:
        value,_=_load(settings_path(path))
        installed=set();matching=set()
        for event,rows in value.get('hooks',{}).items():
            for row in rows:
                for hook in row['hooks']:
                    if not owned(hook):continue
                    installed.add(event)
                    args=shlex.split(hook['command'])
                    if spool is None or ('--spool' in args and
                        Path(args[args.index('--spool')+1]).resolve()==Path(spool).resolve()):matching.add(event)
        required={'UserPromptSubmit','PreToolUse','PostToolUse','Stop','SessionEnd'}
        ok=required<=matching and not value.get('disableAllHooks') and not value.get('allowManagedHooksOnly')
        return dict(installed=bool(ok),events=sorted(installed),message='Hook 已安装，需在 Claude 中新建会话验证' if ok else 'Hook 未完整配置或路径已变化，请安装 / 修复')
    except (OSError,ValueError,IndexError):return dict(installed=False,events=[],message='Claude 设置无法读取或格式错误；未修改原文件')


def is_installed(path=None):return status(path)['installed']
