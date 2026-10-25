"""Versioned remote business API, shared by browsers and future native clients.

All writes are journaled commands. An unconfirmed result is never auto-replayed.
The owner bridge is the only path into task storage and source adapters.
"""
import asyncio
from copy import deepcopy
import json
import re
import threading
import time
from aiohttp import web
from .journal import CommandJournal
from .permissions import normalize
from ..multi_diverse.protocol import project, digest, OPERATIONS
from ..phone.privacy import text as clean_text


SESSION_KEY = web.AppKey('solo_remote_session_key', object)


class RemoteAPI:
    def __init__(self, host):
        self.host = host
        self.lock = threading.Lock()
        self.sample, self.sample_at, self.task_revision = {}, 0, 0
        self.journal = CommandJournal(host.data_root)
        self.busy = set()

    def publish(self, snapshot):
        with self.lock:
            self.sample = deepcopy(snapshot)
            self.sample_at = time.monotonic()

    def changed(self):
        with self.lock: self.task_revision += 1

    def reset(self):
        with self.lock: self.sample, self.sample_at = {}, 0

    def grants(self, session):
        if not getattr(self.host, 'secure', False) or self.host.config['privacy_mode'] == 'status_only':
            return normalize()
        return normalize(session.grants)

    def valid(self, session, permission):
        return (not self.host.closing and any(s is session for s in tuple(self.host.pairing.sessions.values()))
                and self.grants(session).get(permission, False))

    def catalog(self, session):
        grants = self.grants(session)
        with self.lock: sample, fresh = deepcopy(self.sample), time.monotonic()-self.sample_at < 5
        if not fresh: return [], {}
        permitted = any(grants[k] for k in ('agent_approve','agent_answer','agent_stop','reminder_act'))
        if not permitted: return [], {}
        rows, targets = project(sample, getattr(self.host, 'server_id', 'solo'),
                               dict(grants, view_status=True, view_details=True))
        for row in rows:
            # Projection redacts secrets/paths. Never approve an operation whose
            # reviewed argument text differs from the original request.
            target = targets[row['id']]
            from ..src.task_presentation import pending_approvals
            pending = next(iter(pending_approvals(target)), {})
            if not pending:
                from ..src.task_presentation import timestamp
                pending = next((p for p in target.get('deepseek_interactions', []) if isinstance(p,dict)
                    and p.get('status','pending')=='pending' and (not p.get('expires_at') or timestamp(p['expires_at'])>time.time())), {})
            if not pending:
                row['actions']=[a for a in row['actions'] if a['type'] not in ('approve_tool','deny_tool','answer_question','reject_question')]
            raw = json.dumps(pending.get('arguments') or pending.get('questions') or {}, ensure_ascii=False)
            if raw != clean_text(raw, 6000):
                row['actions'] = [a for a in row['actions'] if a['type'] not in ('approve_tool','answer_question')]
                if row.get('approval'): row['approval']['notice'] = '参数被隐藏或截断，请到来源应用同意。'
            # Frozen proposal fingerprint: independent of transport timestamps.
            row['revision'] = digest({k:v for k,v in row.items() if k not in ('revision','elapsed_ms')})
        return [r for r in rows if r['actions']], targets

    def change_key(self, session):
        rows, _ = self.catalog(session)
        return digest([self.grants(session), self.task_revision, [(r['id'],r['revision']) for r in rows]])

    async def owner(self, operation, payload, allowed):
        bridge = getattr(self.host, 'owner', None)
        if bridge is None: raise ValueError('主程序操作服务不可用')
        future = bridge.submit(operation, payload, allowed)
        # Timeout cancels work that has not started. Started Agent requests are
        # marked unknown and cannot be duplicated by retrying a new transport.
        return await asyncio.wait_for(asyncio.wrap_future(future), 10)

    def require(self, session, permission):
        if not self.valid(session, permission): raise web.HTTPForbidden()

    async def control(self, request):
        session = request[request.app[SESSION_KEY]]
        rows, _ = self.catalog(session)
        return web.json_response(dict(protocol_version=2, server_id=getattr(self.host,'server_id',''),
            encrypted=bool(getattr(self.host,'secure',False)), grants=self.grants(session), csrf=session.csrf,
            actions=rows, revision=self.change_key(session), task_revision=self.task_revision))

    async def tasks(self, request):
        session = request[request.app[SESSION_KEY]]
        self.require(session, 'view_tasks')
        result = await self.owner('list_tasks', {}, lambda:self.valid(session,'view_tasks'))
        self.require(session, 'view_tasks')
        rows = result['tasks']
        id = request.match_info.get('id')
        if id:
            task = next((t for t in rows if t['id']==id), None)
            if task is None: raise web.HTTPNotFound()
            return web.json_response({'task':task})
        offset = int(request.query.get('offset','0'))
        if offset < 0: raise ValueError('分页无效')
        return web.json_response(dict(tasks=rows[offset:offset+50], total=len(rows), offset=offset,
                                      revision=self.task_revision))

    async def result(self, request):
        session = request[request.app[SESSION_KEY]]
        value = self.journal.get(session.id, request.match_info['id'])
        if value is None: raise web.HTTPNotFound()
        return web.json_response(value)

    async def command(self, request):
        session = request[request.app[SESSION_KEY]]
        value = await request.json()
        if not isinstance(value,dict) or set(value) != {'id','operation','payload'}: raise ValueError('命令格式无效')
        id, op, payload = value['id'], value['operation'], value['payload']
        if not isinstance(id,str) or not re.fullmatch('[a-f0-9]{32}',id) or not isinstance(payload,dict):
            raise ValueError('命令格式无效')
        permission = {'save_task':'edit_tasks','delete_task':'delete_tasks','reminder':'reminder_act','agent':None}.get(op)
        if op == 'agent': permission = OPERATIONS.get(payload.get('operation'))
        if permission not in normalize(): raise ValueError('不支持的操作')
        self.require(session, permission)
        signature = digest(value)
        old = self.journal.get(session.id,id,signature)
        if old is not None: return web.json_response(old)
        if session.id in self.busy: raise web.HTTPTooManyRequests()
        data = deepcopy(payload)
        if op in ('save_task','delete_task'):
            fields = {'task_id','expected_revision','value'} if op=='save_task' else {'task_id','expected_revision'}
            if set(data)-fields: raise ValueError('未知任务字段')
            if op=='save_task':
                from ..tasks.schedule import validate
                if not isinstance(data.get('value'),dict): raise ValueError('缺少任务内容')
                allowed_fields = {'title','body','start_date','time_of_day','weekdays','interval_hours','lead_minutes','enabled','timezone'}
                if set(data['value'])-allowed_fields: raise ValueError('未知任务字段')
                data['value'] = validate(data['value'])
            if op=='delete_task' or data.get('task_id'):
                if not re.fullmatch('[a-f0-9]{32}',str(data.get('task_id',''))) or type(data.get('expected_revision')) is not int:
                    raise ValueError('编辑和删除必须携带任务版本')
        elif op in ('agent','reminder'):
            if set(data)-{'entity','revision','operation','payload'}: raise ValueError('操作字段无效')
            rows, targets = self.catalog(session)
            row = next((r for r in rows if r['id']==data.get('entity')),None)
            if row is None or row['revision'] != data.get('revision'): raise ValueError('请求已更新，请刷新后操作')
            action = data.get('operation'); args = data.get('payload',{})
            if not isinstance(args,dict) or set(args)-{'confirmation_id','expected_revision','cycle','action','answers'}:
                raise ValueError('操作参数无效')
            if not any(a['type']==action and all(a.get(k)==args.get(k) for k in ('confirmation_id','expected_revision','cycle')) for a in row['actions']):
                raise ValueError('来源不支持该操作或请求已过期')
            target = targets[row['id']]
            if action=='local_reminder':
                if op!='reminder' or args.get('action') not in ('snooze','acknowledge'): raise ValueError('提醒操作无效')
                data = dict(id=target['reminder']['id'],cycle=args['cycle'],action=args['action'],command_id=session.id+':'+id)
            else:
                if op!='agent': raise ValueError('操作类别不匹配')
                if action=='answer_question':
                    answers=args.get('answers'); questions=row.get('questions',[])
                    if not questions or not isinstance(answers,list) or len(answers)!=len(questions): raise ValueError('回答数量不匹配')
                    for q,a in zip(questions,answers):
                        if not isinstance(a,list) or not a or len(a)>12 or any(not isinstance(s,str) or not s.strip() or len(s)>2000 for s in a): raise ValueError('回答无效')
                        if not q['multiple'] and len(a)!=1: raise ValueError('请选择一个答案')
                        if not q.get('custom') and any(s not in q['options'] for s in a): raise ValueError('答案不在选项内')
                data = dict(operation=action,target=target,payload=args)
        self.journal.begin(session.id,id,signature)
        self.busy.add(session.id)
        policy_revision = session.policy_revision
        def authorized():
            if session.policy_revision != policy_revision or not self.valid(session,permission): return False
            if op in ('agent','reminder'):
                current, _ = self.catalog(session)
                return any(r['id']==payload.get('entity') and r['revision']==payload.get('revision') for r in current)
            return True
        try:
            result = await self.owner(op, data, authorized)
            # Do not persist task body/Agent parameters in the command journal.
            result = dict(status=result.get('status','succeeded'), message=result.get('message','操作已完成'),
                          **({'task_id':result['task']['id'],'revision':result['task']['revision']} if 'task' in result else {}))
            if op in ('save_task','delete_task','reminder'): self.changed()
        except ValueError as error:
            unknown = getattr(error,'code','') in ('DISCONNECTED','BRIDGE_ERROR')
            result = dict(status='outcome_unknown' if unknown else 'rejected', message='结果待核对，请勿重复提交' if unknown else clean_text(str(error),200))
        except (Exception, asyncio.CancelledError):
            result = dict(status='outcome_unknown',message='结果待核对，请勿重复提交')
        finally: self.busy.discard(session.id)
        self.journal.finish(session.id,id,result)
        return web.json_response(result)

    def routes(self, app, session_key):
        app[SESSION_KEY] = session_key
        app.router.add_get('/api/v2/control',self.control)
        app.router.add_get('/api/v2/tasks',self.tasks)
        app.router.add_get('/api/v2/tasks/{id}',self.tasks)
        app.router.add_post('/api/v2/commands',self.command)
        app.router.add_get('/api/v2/commands/{id}',self.result)
