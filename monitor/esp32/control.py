"""Request-bound USB controls. No serial caller may supply a provider or shell command.

The Tk owner publishes immutable proposals; this service only issues short-lived
opaque handles. Writes go through OwnerBridge and the existing AgentGate/TaskStore.
"""
from copy import deepcopy
import json
import re
import secrets
import threading
import time

from ..multi_diverse.protocol import project, digest
from ..remote.journal import CommandJournal
from ..src.task_presentation import pending_approvals, timestamp

TOKEN = re.compile(r'^[a-f0-9]{32}$')


def pages(text):
    """Three lines per page, <=34 glyphs at native CJK 16px.

    Never ellipsize approval text. Unsupported/control characters cannot be
    approved; callers with an incomplete proposal do not publish any decisions.
    """
    lines, line, cells = [], '', 0
    for char in text:
        # Proportional Latin W can be as wide as a Han glyph. A conservative
        # glyph budget prevents long shell arguments being clipped on device.
        width = 2
        if char == '\n' or cells+width > 68:
            lines.append(line); line, cells = '', 0
        if char != '\n': line += char; cells += width
    if line or not lines: lines.append(line)
    return ['\n'.join(lines[i:i+3]) for i in range(0, len(lines), 3)]


class ControlBroker:
    def __init__(self, owner, data_root):
        self.owner = owner
        self.journal = CommandJournal(data_root)
        self.lock = threading.RLock()
        self.entries = {}
        self.fresh = 0
        self.enabled = False
        self.read = {}
        self.pending = {}
        self.used = set()
        self.session = 0

    def reset(self):
        with self.lock:
            self.entries.clear(); self.read.clear(); self.enabled = False
            self.session += 1

    def disconnect(self):
        with self.lock:
            self.read.clear(); self.session += 1

    def new_session(self):
        with self.lock:
            self.read.clear(); self.session += 1

    def publish(self, snapshot, runs, key, enabled):
        grants = dict(view_status=True, view_details=True, agent_approve=True, reminder_act=True)
        # Keep registry bounded to exactly what the frame can expose.
        reminders=[r for r in runs if (r.get('remote_source') or r.get('app_id'))=='solo_task'][:5]
        agents=[r for r in runs if (r.get('remote_source') or r.get('app_id'))!='solo_task'][:5]
        runs=agents+reminders
        local, targets = project({**snapshot, 'runs':[r for r in runs if not r.get('remote_origin')]}, 'esp32', grants)
        local_map = {key(targets[row['id']]): row for row in local}
        with self.lock:
            now = time.monotonic(); old = self.entries; new = {}; handles = {}
            self.enabled = enabled; self.fresh = now
            if not enabled:
                self.entries = {}; self.read.clear(); return handles
            for run in runs:
                if run.get('connection_error'): continue
                try:
                    row = run.get('remote_entity') if run.get('remote_origin') else local_map.get(key(run))
                    if not row: continue
                    source = str(run.get('remote_name') or '本机')
                    actions = {}; kind = 'approval'; body = ''
                    if row['source'] == 'solo_task':
                        kind = 'reminder'; rem = row.get('reminder') or {}
                        if any(a['type']=='local_reminder' for a in row['actions']):
                            actions = {op:dict(operation='local_reminder',payload=dict(cycle=rem['cycle'],action=op)) for op in ('snooze','acknowledge')}
                        body = source+' · 待办提醒\n'+row['title']+'\n'+row.get('summary','')
                    else:
                        proposal = row.get('approval') or {}
                        if not proposal: continue
                        complete = proposal.get('complete') is True and not proposal.get('truncated')
                        # Local adapters are checked against the original values,
                        # not just a sanitized remote representation.
                        if not run.get('remote_origin'):
                            request = next(iter(pending_approvals(run)), {})
                            if not request:
                                request = next((v for v in run.get('deepseek_interactions',[]) if v.get('kind')=='approval'
                                    and v.get('status','pending')=='pending' and (not v.get('expires_at') or timestamp(v['expires_at'])>time.time())), {})
                            raw = json.dumps(request.get('arguments') or {}, ensure_ascii=False)
                            complete = bool(request) and raw == proposal.get('detail') and not proposal.get('truncated')
                            complete = complete and str(request.get('reason') or '') == proposal.get('reason','')
                            complete = complete and str(request.get('tool_name') or '') == proposal.get('tool_name','')
                        body = source+' · '+row['title']+'\n操作：'+proposal.get('tool_name','')+'\n原因：'+proposal.get('reason','')+'\n完整参数：\n'+proposal.get('detail','')
                        if complete:
                            for a in row['actions']:
                                if a['type'] in ('approve_tool','deny_tool'):
                                    actions['approve' if a['type']=='approve_tool' else 'deny'] = dict(operation=a['type'],payload={k:a[k] for k in ('confirmation_id','expected_revision') if k in a})
                        else: body += '\n请求不完整或已隐藏，请在来源应用处理。'
                    if not actions: continue
                    # A bounded document and renderable glyphs are prerequisites
                    # to decisions on a small screen (GB2312 font + ASCII).
                    if len(body.encode()) > 12000: continue
                    try: body.replace('·','・').encode('gb2312')
                    except UnicodeEncodeError: continue
                    if any(ord(c)<32 and c not in '\n\t' for c in body): continue
                    body = body.replace('\t','    ')
                    document = pages(body)
                    if len(document)>120: continue
                    signature = digest([key(run),kind,body,actions,row.get('reminder',{})])
                    previous = next((e for e in old.values() if e['signature']==signature and e['expires']>now),None)
                    token = previous['id'] if previous else secrets.token_hex(16)
                    entry = dict(id=token,signature=signature,expires=previous['expires'] if previous else now+120,
                                 target=deepcopy(run),actions=actions,pages=document,kind=kind,title=row['title'],source=source)
                    new[token]=entry; handles[key(run)]=token
                except (ValueError,TypeError,KeyError,AttributeError): continue
            self.entries = new
            self.read = {k:v for k,v in self.read.items() if k in new}
            self.used.intersection_update(new)
            return handles

    def valid(self, token):
        return self.enabled and time.monotonic()-self.fresh<4 and token in self.entries and self.entries[token]['expires']>time.monotonic()

    def handle(self, verb, value, authorized=None):
        with self.lock:
            token = value.get('id',''); cmd = value.get('cmd','')
            if not isinstance(token,str) or not TOKEN.fullmatch(token): raise ValueError('请求无效')
            if verb == 'QUERY':
                if not self.enabled: raise ValueError('设备控制已关闭')
                if not TOKEN.fullmatch(str(cmd)): raise ValueError('命令无效')
                result = self.journal.get('esp32', cmd, digest([token,value.get('op')]))
                if cmd in self.pending and self.pending[cmd]['id']==token:
                    return dict(id=token,cmd=cmd,status='pending',message='来源仍在处理，请勿重复提交')
                return dict(id=token,cmd=cmd,**(result or dict(status='expired',message='未找到已提交操作')))
            if verb=='DO' and TOKEN.fullmatch(str(cmd)):
                previous=self.journal.get('esp32',cmd,digest([token,value.get('op')]))
                if previous is not None: return dict(id=token,cmd=cmd,**previous)
            if not self.valid(token): raise ValueError('请求已更新、连接失效或操作未开启')
            e = self.entries[token]
            if verb=='GET':
                page = value.get('page',0)
                if type(page) is not int or not 0<=page<len(e['pages']): raise ValueError('页码无效')
                self.read.setdefault(token,set()).add(page)
                return dict(id=token,page=page,total=len(e['pages']),text=e['pages'][page],kind=e['kind'],
                            actions=list(e['actions']),expires=max(0,int(e['expires']-time.monotonic())))
            if verb!='DO' or not TOKEN.fullmatch(str(cmd)): raise ValueError('命令无效')
            choice=value.get('op'); signature=digest([token,choice])
            old=self.journal.get('esp32',cmd,signature)
            if old is not None: return dict(id=token,cmd=cmd,**old)
            if choice not in e['actions']: raise ValueError('来源不支持该操作')
            if len(self.read.get(token,set()))!=len(e['pages']): raise ValueError('请先完整阅读请求')
            if token in self.used or self.pending: raise ValueError('操作已提交，请先查询结果')
            action=deepcopy(e['actions'][choice]); run=deepcopy(e['target']); session=self.session
            def allowed():
                with self.lock: return (session==self.session and self.valid(token) and self.entries[token]['signature']==e['signature']
                                       and (authorized is None or authorized()))
            self.journal.begin('esp32',cmd,signature); self.used.add(token)
            if run.get('remote_origin'):
                op='esp32_peer'; data=dict(target=run,operation=action['operation'],payload=action['payload'])
            elif action['operation']=='local_reminder':
                op='reminder'; data=dict(id=run['reminder']['id'],cycle=action['payload']['cycle'],action=choice,command_id='esp32:'+cmd)
            else: op='agent'; data=dict(target=run,**action)
            future=self.owner.submit(op,data,allowed)
            self.pending[cmd]=dict(future=future,id=token,started=time.monotonic())
            return dict(id=token,cmd=cmd,status='pending',message='已发送，等待来源处理')

    def collect(self):
        with self.lock:
            replies=[]
            for cmd,item in list(self.pending.items()):
                f=item['future']
                if not f.done() and time.monotonic()-item['started']<12: continue
                try:
                    if not f.done():
                        f.cancel(); result=dict(status='outcome_unknown',message='结果暂未确认，请查询原请求')
                    else:
                        value=f.result() or {}; result=dict(status=value.get('status','succeeded'),message=value.get('message','电脑端已保存'))
                except ValueError as error:
                    unknown=getattr(error,'code','') in ('DISCONNECTED','BRIDGE_ERROR')
                    result=dict(status='outcome_unknown' if unknown else 'rejected',
                                message='结果暂未确认，请核对来源' if unknown else '请求已变化、已处理或来源拒绝')
                except Exception: result=dict(status='outcome_unknown',message='结果暂未确认，请核对来源')
                result['message']=str(result['message']).encode('utf-8')[:300].decode('utf-8','ignore')
                self.journal.finish('esp32',cmd,result)
                replies.append(dict(id=item['id'],cmd=cmd,**result)); del self.pending[cmd]
            return replies
