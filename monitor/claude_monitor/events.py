"""Allowlisted event payloads and real observed phases, not invented progress."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import re
from uuid import uuid4

FINAL = {'completed','failed','cancelled','interrupted'}
EVENTS = ('SessionStart','UserPromptSubmit','PreToolUse','PermissionRequest','PostToolUse',
          'PostToolUseFailure','PermissionDenied','Notification','PreCompact','PostCompact',
          'SubagentStart','SubagentStop','Stop','StopFailure','SessionEnd')
SECRET = re.compile(r'(api.?key|secret|password|authorization|credential|access.?token|refresh.?token)', re.I)


def text(value, limit=240, multiline=False):
    value = str(value or '')
    value = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]' if multiline else r'[\x00-\x1f\x7f]', ' ', value)
    value = re.sub(r'\b(?:sk-|ghp_|github_pat_)[A-Za-z0-9_-]{12,}', '[已隐藏凭据]', value)
    value = re.sub(r'(?i)(bearer\s+)[\w.\-/+=]+', r'\1[已隐藏]', value)
    value = re.sub(r'(?i)((?:api[_-]?key|password|secret|access[_-]?token)\s*[=:]\s*)[^\s,;]+', r'\1[已隐藏]', value)
    return value[:limit]


def stamp(value=None):
    if isinstance(value, (int,float)):
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    if value:
        return datetime.fromisoformat(str(value).replace('Z','+00:00')).isoformat()
    return datetime.now(timezone.utc).isoformat()


def arguments(value):
    """Return bounded, redacted parameters and whether approval would be unsafe."""
    truncated = False
    def walk(v, depth=0):
        nonlocal truncated
        if depth > 5:
            truncated = True
            return '[嵌套过深]'
        if isinstance(v, dict):
            truncated |= len(v)>40
            return {str(k)[:100]: '[已隐藏]' if SECRET.search(str(k)) else walk(x,depth+1)
                    for k,x in list(v.items())[:40]}
        if isinstance(v, list):
            truncated |= len(v)>40
            return [walk(x,depth+1) for x in v[:40]]
        if isinstance(v,str):
            safe = text(v,6000,multiline=True)
            truncated |= len(v)>6000 or safe != v
            return safe
        if v is None or type(v) in (bool,int,float):
            return v
        return text(v)
    result = walk(value if isinstance(value,dict) else {})
    if len(json.dumps(result,ensure_ascii=False).encode())>24000:
        return {'说明':'参数过长，请在 Claude 原界面核对'}, True
    # Redacted fields must not produce a misleading approval of unseen content.
    truncated |= '[已隐藏]' in json.dumps(result,ensure_ascii=False)
    return result, truncated


def normalize(payload):
    name = payload.get('hook_event_name')
    if name not in EVENTS or not isinstance(payload.get('session_id'),str) or not payload['session_id']:
        raise ValueError('Unknown Claude event or missing session')
    event = {k:text(payload.get(k),512 if k=='cwd' else 128) for k in
             ('session_id','prompt_id','agent_id','agent_type','cwd','model','tool_name','tool_use_id','notification_type','reason')}
    event.update(hook_event_name=name,event_id=uuid4().hex,timestamp=stamp())
    inp = payload.get('tool_input') if isinstance(payload.get('tool_input'),dict) else {}
    event['detail'] = text(inp.get('description') or inp.get('command') or inp.get('file_path') or inp.get('path') or inp.get('query'),300)
    if name=='PermissionRequest':
        event['arguments'],event['truncated'] = arguments(inp)
        if isinstance(payload.get('request'), dict):
            event['request'] = {k: payload['request'].get(k) for k in ('id','revision','expires_at')
                                if payload['request'].get(k) is not None}
    if name=='UserPromptSubmit':
        event['title'] = text(payload.get('prompt'),100)
    if name in ('Stop','SubagentStop','StopFailure'):
        event['summary'] = text(payload.get('last_assistant_message') or payload.get('error_details') or payload.get('error'),1000)
        event['background_count'] = len(payload.get('background_tasks') or [])
        event['stop_hook_active'] = payload.get('stop_hook_active') is True
    if name=='PostToolUseFailure':
        event['detail'] = text(payload.get('error') or event['detail'],300)
        event['interrupted'] = payload.get('is_interrupt') is True
    if name=='PostToolUse':
        response=payload.get('tool_response')
        if isinstance(response,dict):
            event['result'] = text(response.get('stdout') or response.get('message') or response.get('output'),400)
            code=response.get('exitCode',response.get('exit_code'))
            if type(code) is int:event['exit_code']=code
        elif isinstance(response,str):event['result']=text(response,400)
        if event['tool_name']=='TodoWrite' and isinstance(inp.get('todos'),list):
            event['plan']=[dict(name=text(s.get('content') or s.get('activeForm'),120),status={
                'pending':'pending','in_progress':'running','completed':'completed'}.get(s.get('status'),'pending'))
                for s in inp['todos'][:40] if isinstance(s,dict)]
    return event


def phase(event):
    name=event.get('tool_name','').lower()
    if name in ('read','glob','grep','ls') or any(s in name for s in ('search','fetch')):return '读取与检索'
    if name in ('write','edit','multiedit','notebookedit'):return '修改文件'
    if name in ('bash','powershell','shell'):
        return '运行验证' if re.search(r'\b(pytest|test|lint|check|build|tsc)\b',event.get('detail',''),re.I) else '执行命令'
    if name in ('task','agent'):return '委派子任务'
    if name in ('todowrite','taskcreate','taskupdate','enterplanmode','exitplanmode'):return '规划任务'
    if name=='askuserquestion':return '等待用户回答'
    return '调用工具'


class SessionProjection:
    def __init__(self, session, turn, when=None):
        self.session,self.turn=session,turn
        ident=hashlib.sha256((session+'\0'+turn).encode()).hexdigest()[:32]
        now=stamp(when)
        self.run=dict(run_id='claude:'+ident,app_id='claude',session_id=session,turn_id=turn,
                      agent='Claude Code',status='running',started_at=now,updated_at=now,
                      task_summary='Claude Code 任务',message='正在分析任务',summary_preview='',
                      steps=[],tools=[],pending_confirmations=[],confirmations=[],monitor_activity_id='',
                      claude=dict(events=0,compactions=0,subagents=0,children={},cwd='',model='',stage='分析任务',plan=[],background_count=0))
        self.seen=set()

    def step(self,label,state='running',ident=''):
        steps=self.run['steps']
        if not ident and steps and steps[-1]['name']==label and steps[-1]['status']=='running':return
        # Observed stage transitions do not claim test/tool success.
        for s in steps:
            if s['status']=='running':s['status']='completed'
        steps.append(dict(id=ident or str(self.run['claude']['events']),name=label,status=state))
        self.run['steps']=steps[-32:]
        self.run['claude']['stage']=label

    def apply(self,event):
        eid=event.get('event_id')
        if not eid or eid in self.seen:return
        self.seen.add(eid)
        if len(self.seen)>2048:self.seen={eid}
        run,info=self.run,self.run['claude'];name=event['hook_event_name']
        run['updated_at']=stamp(event.get('timestamp'));run['monitor_activity_id']=eid
        info['events']+=1;info['event']=name
        for key in ('cwd','model'):
            if event.get(key):info[key]=event[key]
        if event.get('process'):info['process']=event['process']
        # Late child events must not resurrect an already terminal parent turn.
        if run['status'] in FINAL and name not in ('Stop','StopFailure','SessionEnd'):return
        if name=='UserPromptSubmit':
            run['task_summary']=event.get('title') or 'Claude Code 任务'
            self.step('分析任务');run['message']='正在分析任务需求'
        elif name in ('PreToolUse','PostToolUse','PostToolUseFailure'):
            tid=event.get('tool_use_id') or eid
            tool=next((x for x in run['tools'] if x['id']==tid),None)
            if tool is None:
                tool=dict(id=tid,name=event.get('tool_name') or '工具',status='running',detail='')
                run['tools'].append(tool);run['tools']=run['tools'][-64:]
            tool.update(status='running' if name=='PreToolUse' else 'failed' if name=='PostToolUseFailure' else 'completed')
            for key in ('detail','result','exit_code'):
                if event.get(key) is not None:tool[key]=event[key]
            if name=='PreToolUse':
                stage=phase(event);self.step(stage)
                run['status']='waiting_input' if stage=='等待用户回答' else 'running'
                run['message']=stage+' · '+tool['name']
            else:
                run['status']='interrupted' if event.get('interrupted') else 'running'
                run['message']=('工具失败，等待 Claude 后续处理' if name=='PostToolUseFailure' else '工具已返回，继续处理')+' · '+tool['name']
                run['pending_confirmations']=[]
                if event.get('plan'):info['plan']=event['plan']
                self.step('继续处理' if run['status']=='running' else '已中断', 'running' if run['status']=='running' else 'interrupted')
        elif name=='PermissionRequest':
            request=event.get('request') or {}
            run['pending_confirmations'].append(dict(id=request.get('id',eid),revision=request.get('revision',eid),
                requested_at=run['updated_at'],expires_at=request.get('expires_at'),status='pending',
                tool_name=event.get('tool_name') or '工具',reason='请核对本次工具调用',
                arguments=event.get('arguments') or {},truncated=bool(event.get('truncated')),
                controllable=bool(request)))
            run['status']='waiting_confirmation';run['message']='等待授权 · '+(event.get('tool_name') or '工具')
            self.step('等待授权')
        elif name=='Notification':
            if event.get('notification_type')=='permission_prompt':
                run['status']='waiting_confirmation';run['message']='请在 Claude 原界面处理授权';self.step('等待授权')
            elif event.get('notification_type')=='idle_prompt':
                run['status']='paused';run['message']='Claude 正在等待用户输入';self.step('等待输入','paused')
        elif name=='PermissionDenied':
            run['pending_confirmations']=[];run['status']='running';run['message']='权限被拒绝，等待 Claude 处理';self.step('权限受限')
        elif name in ('PreCompact','PostCompact'):
            if name=='PostCompact':info['compactions']+=1
            run['message']='正在压缩上下文' if name=='PreCompact' else '上下文压缩已完成'
            self.step('上下文压缩','running' if name=='PreCompact' else 'completed')
        elif name in ('SubagentStart','SubagentStop'):
            child=event.get('agent_id') or eid
            info['children'][child]=dict(name=event.get('agent_type') or '子 Agent',status='running' if name=='SubagentStart' else 'completed')
            info['children']=dict(list(info['children'].items())[-32:]);info['subagents']=len(info['children'])
            run['message']='子 Agent 运行中' if name=='SubagentStart' else '子 Agent 已返回'
            self.step(run['message'])
        elif name in ('Stop','StopFailure'):
            run['summary_preview']=event.get('summary','')
            info['background_count']=event.get('background_count',0)
            run['status']='failed' if name=='StopFailure' else 'paused' if info['background_count'] else 'completed'
            run['message']='API 请求失败' if name=='StopFailure' else '主响应结束，仍有后台任务' if info['background_count'] else '本轮响应结束'
            run['pending_confirmations']=[]
            self.step(run['message'],run['status'])
        elif name=='SessionEnd':
            if run['status'] not in FINAL:run['status']='interrupted';run['message']='会话已结束';self.step('会话结束','interrupted')
            run['pending_confirmations']=[]
        if run['status'] in FINAL:run['ended_at']=run['updated_at']

    def snapshot(self):
        value=deepcopy(self.run)
        value['confirmations']=deepcopy(value['pending_confirmations'])
        return value
