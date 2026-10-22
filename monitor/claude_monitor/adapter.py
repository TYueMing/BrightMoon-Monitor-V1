"""Dedicated Claude source: stable per-prompt runs and expiring live approvals."""
from datetime import datetime
import sqlite3
import time
from uuid import uuid4
from ..src.source_config import claude_event_root
from .events import SessionProjection, FINAL, EVENTS
from . import spool


def inspect(config):
    from .installer import status
    root=claude_event_root(config)
    info=status(config.get('settings_path') or None,root)
    return dict(status='ready' if info['installed'] else 'setup_required',message=info['message'],
                data_path=str(root),read_only=not config.get('allow_approvals',False))


class ClaudeSource:
    app_id='claude'
    def __init__(self,config):
        self.root=claude_event_root(config).resolve()
        self.owner=uuid4().hex;self.config=dict(config);self.closed=False
        self.sessions={};self.current={};self.children={};self.meta={};self.errors=0
        self.last_prune=0;self.last_config_check=0;self.hook_ready=False
        # Establish a starting watermark, without replaying old finished tasks.
        # A new event can attach mid-turn; its timer explicitly says observed.
        with spool.connection(self.root) as db:
            self.seq=db.execute('SELECT COALESCE(MAX(seq),0) FROM events').fetchone()[0]
        spool.heartbeat(self.root,self.owner,self.config.get('allow_approvals',False))

    def _apply(self,event):
        name=event.get('hook_event_name');sid=event.get('session_id')
        if name not in EVENTS or not isinstance(sid,str) or not sid:return
        if not isinstance(event.get('event_id'),str):return
        meta=self.meta.setdefault(sid,{})
        for k in ('cwd','model'):
            if event.get(k):meta[k]=event[k]
        if name=='SessionStart':return
        child=event.get('agent_id')
        # Child hooks share the parent row, never become competing top-level tasks.
        parent=self.children.get((sid,child)) if child else None
        turn=parent or event.get('prompt_id') or self.current.get(sid)
        if name=='UserPromptSubmit':
            turn=event.get('prompt_id') or event['event_id']
            old=self.sessions.get((sid,self.current.get(sid)))
            if old and old.turn!=turn and old.run['status'] not in FINAL:
                old.run.update(status='interrupted',message='新的用户请求已开始，上一轮停止跟踪',
                               ended_at=event['timestamp'],updated_at=event['timestamp'],pending_confirmations=[])
            self.current[sid]=turn
        if not turn:
            if name in ('Stop','StopFailure','SessionEnd','SubagentStop','Notification'):return
            turn='observed:'+event['event_id'];self.current[sid]=turn
        key=(sid,turn)
        if key not in self.sessions:
            if name in ('Stop','StopFailure','SessionEnd'):return
            self.sessions[key]=SessionProjection(sid,turn,event.get('timestamp'))
            self.sessions[key].run['claude'].update(meta,time_basis='reported' if name=='UserPromptSubmit' else 'observed')
        projection=self.sessions[key]
        if (name=='PreToolUse' and not child and projection.run['status']=='completed'
                and event.get('timestamp','') > projection.run.get('ended_at','')):
            # Another Stop hook can continue Claude after a response ended.
            # A real new tool-start is fresh execution evidence, unlike a late
            # tool-result. Give it a generation so retired display receipts do
            # not incorrectly suppress this continuation.
            projection=SessionProjection(sid,turn+':continue:'+event['event_id'],event['timestamp'])
            projection.run['claude'].update(meta,time_basis='observed')
            self.sessions[key]=projection
        if child:
            self.children[(sid,child)]=turn
            if name in ('Stop','StopFailure'):
                # Defensive compatibility: child Stop must not complete its parent.
                return
        projection.apply(event)
        if name=='SessionEnd':
            for (session,_),p in self.sessions.items():
                if session==sid and p is not projection:p.apply(event)
        # Bound retained memory independently of five visible island slots.
        if len(self.sessions)>128:
            oldest=min(self.sessions,key=lambda k:(self.sessions[k].run['status'] not in FINAL,self.sessions[k].run['updated_at']))
            if oldest!=key:self.sessions.pop(oldest)
        if len(self.meta)>256:self.meta.pop(next(iter(self.meta)))
        if len(self.current)>256:self.current.pop(next(iter(self.current)))
        if len(self.children)>512:self.children.pop(next(iter(self.children)))

    def poll(self):
        if self.closed:return dict(status='disabled',runs=[],actions=[])
        spool.heartbeat(self.root,self.owner,self.config.get('allow_approvals',False))
        for seq,_,event in spool.read_events(self.root,self.seq):
            self.seq=seq
            try:self._apply(event)
            except (ValueError,TypeError,KeyError,OverflowError):self.errors+=1
        states=spool.requests(self.root)
        runs=[];actions=[];now=time.time()
        from .process import alive
        process_states={}
        for projection in self.sessions.values():
            run=projection.snapshot()
            updated=datetime.fromisoformat(run['updated_at']).timestamp()
            if run['status'] in FINAL and now-updated>1200:continue
            valid=[]
            for request in run['pending_confirmations']:
                state=states.get(request['id'])
                if not request.get('controllable'):
                    if now-updated<60:valid.append(request)
                    continue
                if not state or state['state'] not in ('pending','decided'):
                    continue
                request['revision']=state['revision'];request['expires_at']=state['deadline']
                valid.append(request)
                if state['state']=='pending' and self.config.get('allow_approvals',False):
                    for typ,label in [('approve_tool','同意一次'),('deny_tool','拒绝')]:
                        if typ=='approve_tool' and request.get('truncated'):continue
                        actions.append(dict(id='claude:'+request['id']+':'+typ,type=typ,label=label,
                            run_id=run['run_id'],confirmation_id=request['id'],expected_revision=state['revision']))
            run['pending_confirmations']=valid;run['confirmations']=valid
            if run['status']=='waiting_confirmation' and not valid:
                # No authority to infer approval/cancel from a dead waiter.
                run.update(status='paused',message='授权桥已结束，请在 Claude 核对当前状态')
            if run['status'] not in FINAL and now-updated>900 and not valid:
                run.update(status='stale',message='长时间无新 Hook 事件，执行状态待核对')
            evidence=run['claude'].get('process')
            if evidence and run['status'] not in FINAL:
                key=(evidence.get('pid'),evidence.get('birth'))
                if key not in process_states:process_states[key]=alive(evidence)
                if process_states[key] is False:
                    from .events import stamp
                    ended = stamp(now)
                    run.update(status='interrupted',message='Claude 执行进程已退出',ended_at=ended,updated_at=ended,
                               pending_confirmations=[],confirmations=[])
                    projection.run.update(status='interrupted',message=run['message'],ended_at=ended,updated_at=ended,pending_confirmations=[])
                    spool.expire_session(self.root,run['session_id'],run['turn_id'])
                    actions=[a for a in actions if a['run_id']!=run['run_id']]
            runs.append(run)
        runs.sort(key=lambda r:(r['status'] not in FINAL,r['updated_at']),reverse=True)
        runs=runs[:32];visible={r['run_id'] for r in runs}
        if now-self.last_prune>60:spool.prune(self.root);self.last_prune=now
        if now-self.last_config_check>5:
            from .installer import status
            self.hook_ready=status(self.config.get('settings_path') or None,self.root)['installed']
            self.last_config_check=now
        message='已接收 Claude Code 事件' if self.sessions else 'Hook 已配置，等待新任务' if self.hook_ready else '请在设置中安装 Claude Code Hook'
        if self.errors:message+='；部分无效事件已跳过'
        return dict(status='ready' if self.sessions or self.hook_ready else 'setup_required',message=message,
                    runs=runs,actions=[a for a in actions if a['run_id'] in visible],session_count=len(self.sessions),
                    read_only=not self.config.get('allow_approvals',False))

    def send(self,action,run_id,payload):
        if self.closed or not self.config.get('allow_approvals',False):raise ValueError('Claude 授权桥已关闭')
        if action not in ('approve_tool','deny_tool'):raise ValueError('不支持此 Claude 操作')
        projection=next((p for p in self.sessions.values() if p.run['run_id']==run_id),None)
        request=next((p for p in projection.run['pending_confirmations'] if p['id']==payload.get('confirmation_id')
                      and p['revision']==payload.get('expected_revision')),None) if projection else None
        if not request or not request.get('controllable') or projection.run['status'] in FINAL:
            raise ValueError('此授权已失效或不属于当前任务')
        if action=='approve_tool' and request.get('truncated'):raise ValueError('参数未完整展示，请在 Claude 中审批')
        spool.decide(self.root,request['id'],request['revision'],'allow' if action=='approve_tool' else 'deny')
        return '已提交本次决定，等待 Claude 消费确认'

    def close(self):
        self.closed=True
        try:spool.release(self.root,self.owner)
        except (OSError,ValueError,sqlite3.Error):pass
