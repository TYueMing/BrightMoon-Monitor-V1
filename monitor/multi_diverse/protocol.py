"""Bounded, non-executable projection. Remote data never enters a local adapter."""
import hashlib
import json
import math
import time
from ..src.task_identity import task_identity
from ..phone.privacy import text as clean_text

MAX_MESSAGE=256*1024
from ..UI.common_model import FINAL
OPERATIONS={'approve_tool':'agent_approve','deny_tool':'agent_approve','answer_question':'agent_answer',
            'reject_question':'agent_answer','stop':'agent_stop','cancel':'agent_stop','cancel_run':'agent_stop','local_reminder':'reminder_act'}

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def text(value,n=500):
    return clean_text(str(value or ''), n)


def timestamp(value):
    from datetime import datetime,timezone
    from ..src.task_presentation import timestamp as parse
    stamp=parse(value)
    try:return datetime.fromtimestamp(stamp,timezone.utc).isoformat() if stamp else None
    except (OverflowError,OSError,ValueError):return None

def validate(value):
    def walk(v,depth=0):
        if depth>12:raise ValueError('消息嵌套过深')
        if isinstance(v,dict):
            if len(v)>200:raise ValueError('字段过多')
            for k,x in v.items():
                if not isinstance(k,str) or len(k)>100:raise ValueError('字段无效')
                walk(x,depth+1)
        elif isinstance(v,list):
            if len(v)>200:raise ValueError('数组过长')
            for x in v:walk(x,depth+1)
        elif isinstance(v,str):
            if len(v)>16000:raise ValueError('字符串过长')
        elif isinstance(v,float) and not math.isfinite(v):raise ValueError('数字无效')
        elif v is not None and not isinstance(v,(str,int,float,bool)):raise ValueError('值无效')
    walk(value)
    if len(json.dumps(value,ensure_ascii=False).encode())>MAX_MESSAGE:raise ValueError('消息过大')
    return value

def _project_unchecked(snapshot,owner,grant):
    result=[];targets={}
    if not grant.get('view_status'):return result,targets
    for run in snapshot.get('runs',[]):
        if run.get('remote_origin') or not run.get('run_id'):continue
        if grant.get('sources') is not None and run.get('app_id') not in grant['sources']:continue
        identity=task_identity(run)
        key=digest([owner,identity])
        targets[key]=run
        item=dict(id=key,source=run.get('app_id','unknown'),status=run.get('status','offline'),
                  title=text(run.get('task_summary') or run.get('message'),120),
                  started_at=timestamp(run.get('started_at')),updated_at=timestamp(run.get('updated_at')),ended_at=timestamp(run.get('ended_at')),
                  connection_error=bool(run.get('connection_error')),steps=[],summary='',actions=[])
        if grant.get('anonymous_titles'):item['title']='待办提醒' if run.get('app_id')=='solo_task' else 'Agent 任务'
        if grant.get('view_details'):
            item['summary']=text(run.get('summary_preview') or run.get('message') or run.get('error'),1000)
            item['steps']=[dict(name=text(s.get('name') or s.get('title'),100),status=s.get('status','pending')) for s in run.get('steps',[])[:30] if isinstance(s,dict)]
            item['tools']=[dict(name=text(t.get('name') or t.get('tool_name'),80),status=text(t.get('status'),40),summary=text(t.get('summary') or t.get('output_preview'),180)) for t in run.get('tools',[])[:12] if isinstance(t,dict)]
            # Reuse the reviewed source-to-safe-block translation. It knows
            # OpenCode todos versus observed Harness rounds and Codex metrics.
            from ..phone.projector import PhoneProjector
            safe=PhoneProjector(b'peer-projection-schema').task(run,False,0)
            item['steps']=[dict(name=node['label'],status=node['status']) for node in safe['chain']['nodes'][:30]]
            item['chain_kind']=safe['chain']['kind']
            item['chain_total']=safe['chain']['total']
            item['chain_truncated']=safe['chain']['total']>len(item['steps'])
            item['metrics']=[dict(name=entry['label'],summary=str(entry['value'])) for block in safe['detail_blocks'] if block['type']=='metric_list' for entry in block['items']]
            item['tools']=[dict(name=t['name'],status=t['status'],summary=t['detail']+(' · 退出码 '+str(t['exit_code']) if t.get('exit_code') is not None else ''))
                           for block in safe['detail_blocks'] if block['type']=='tool_list' for t in block['items'][:12]]
            if run.get('app_id')=='solo_task':
                item['metrics'] += [dict(name=b['title'],summary=b['text']) for b in safe['detail_blocks'] if b['id']=='schedule']
        # Only numeric/boolean hints required by the local source size policy.
        # No renderer names, executable components, paths or arbitrary heights.
        item['layout']={}
        if grant.get('view_details') and run.get('app_id')=='opencode':
            for field,keys in [('context',('percent','used_tokens')),('changes',('files',)),('child_summary',('total',)),('compaction',('active',))]:
                value=run.get(field) or {}
                item['layout'][field]={k:value[k] for k in keys if type(value.get(k)) in (int,float,bool)}
            item['layout']['stages']=[{'agent':'stage'} for s in run.get('stages',[]) if isinstance(s,dict) and s.get('agent')][:7]
        item.setdefault('metrics',[])
        if grant.get('view_details'):
            for name,field in [('验证','verification'),('执行环境','environment'),('上下文','context_usage'),('文件变更','file_changes')]:
                value=run.get(field)
                if value:item['metrics'].append(dict(name=name,summary=text(json.dumps(value,ensure_ascii=False),500)))
        from ..src.task_presentation import pending_approvals
        pending=next(iter(pending_approvals(run)),{})
        interactions=[i for i in run.get('deepseek_interactions',[]) if isinstance(i,dict)]
        if not pending and interactions:pending=interactions[0]
        if pending and grant.get('view_details'):
            detail=json.dumps(pending.get('arguments') or pending.get('questions') or {},ensure_ascii=False)
            item['approval']=dict(id=str(pending['id']),tool_name=text(pending.get('tool_name'),100),
                reason=text(pending.get('reason'),500),detail=text(detail,6000),truncated=len(detail)>6000,
                revision=digest([pending.get('revision'),detail]))
        available=list(snapshot.get('actions',[]))
        if run.get('app_id')=='deepseek_harness' and interactions:
            request=interactions[0]
            for op in (('approve_tool','deny_tool') if request.get('kind')=='approval' else ('answer_question',)):
                available.append(dict(type=op,run_id=run['run_id'],app_id='deepseek_harness',confirmation_id=request['id'],expected_revision=request['revision']))
        for action in available:
            op=action.get('type');permission=OPERATIONS.get(op)
            if action.get('app_id',run.get('app_id'))!=run.get('app_id') or action.get('run_id')!=run['run_id'] or not permission or not grant.get(permission):continue
            if action.get('confirmation_id') and action['confirmation_id']!=pending.get('id'):continue
            # Unsupported question formats remain viewable but never produce a
            # misleading generic approval. Native OpenCode questions are mapped below.
            if op=='answer_question' and run.get('app_id') not in ('opencode','deepseek_harness'):continue
            if op=='approve_tool' and item.get('approval',{}).get('truncated'):continue
            item['actions'].append({k:action[k] for k in ('type','confirmation_id','expected_revision') if k in action})
        if pending and run.get('app_id') in ('opencode','deepseek_harness') and grant.get('agent_answer'):
            questions=pending.get('questions') or []
            if len(questions)<=12:
                item['questions']=[dict(question=text(q.get('question'),500),multiple=bool(q.get('multiple') or q.get('multiSelect')),
                    options=[text(o.get('label'),100) for o in q.get('options',[])[:12]],custom=q.get('custom',True)) for q in questions if isinstance(q,dict)]
            else:item['actions']=[a for a in item['actions'] if a['type']!='answer_question']
            safe_questions=bool(questions) and all(isinstance(q,dict) and len(str(q.get('question','')))<=500
                and len(q.get('options',[]))<=12 and all(isinstance(o,dict) and text(o.get('label'),100)==o.get('label') for o in q.get('options',[])) for q in questions)
            if not safe_questions:item['actions']=[a for a in item['actions'] if a['type']!='answer_question']
        if run.get('app_id')=='solo_task':
            rem=run['reminder']
            item['reminder']={k:rem[k] for k in ('id','cycle','revision','due_at','remind_at','expires_at')}
            if grant.get('reminder_act'):item['actions']=[dict(type='local_reminder',cycle=rem['cycle'])]
        if item['connection_error'] or item['status'] in FINAL:item['actions']=[]
        from ..src.task_identity import activity_evidence
        item['activity']=activity_evidence(run)
        item['revision']=digest(item)
        from ..UI.common_model import elapsed
        try:
            minutes,seconds=elapsed(run).split(':');item['elapsed_ms']=(int(minutes)*60+int(seconds))*1000
        except ValueError:item['elapsed_ms']=0
        result.append(item)
    result.sort(key=lambda r:(r['source']!='solo_task',r.get('reminder',{}).get('remind_at',0),not bool(r['actions']),r['status'] in FINAL,r['id']))
    # Byte cap applies to the complete view, not just each entry.
    bounded=[]
    for item in result[:200]:
        if len(json.dumps(bounded+[item],ensure_ascii=False).encode())>180*1024:break
        bounded.append(item)
    return bounded,targets


def project(snapshot,owner,grant):
    """A malformed provider row cannot take down unrelated subscriptions."""
    catalog={};targets={}
    for run in snapshot.get('runs',[]):
        if not isinstance(run,dict):continue
        try:
            rows,mapping=_project_unchecked({**snapshot,'runs':[run]},owner,grant)
            for row in rows:
                validate_items([row])
                old=catalog.get(row['id'])
                if old and old['status'] in FINAL:continue
                catalog[row['id']]=row;targets[row['id']]=mapping[row['id']]
        except (ValueError,KeyError,TypeError,OverflowError,AttributeError):continue
    rows=sorted(catalog.values(),key=lambda r:(r['source']!='solo_task',r.get('reminder',{}).get('remind_at',0),not bool(r['actions']),r['status'] in FINAL,r['id']))
    bounded=[];size=2
    for row in rows[:200]:
        length=len(json.dumps(row,ensure_ascii=False).encode())+2
        if size+length>180*1024:break
        bounded.append(row);size+=length
    return bounded,targets

def remote_run(item,peer,*,offline=False):
    key='peer:'+peer['id']+':'+item['id']
    return dict(run_id=key,monitor_task_id=key,monitor_scope=peer['id'],turn_id=item['id'],app_id='solo_peer',
        remote_origin=peer['id'],remote_name=peer['name'],remote_source=item['source'],remote_entity=item,
        task_summary=item['title'],message=item['summary'],steps=item['steps'],tools=item.get('tools',[]),metrics=item.get('metrics',[]),
        status=item['status'],connection_error=offline or item.get('connection_error',False),
        started_at=item.get('started_at'),updated_at=item.get('updated_at'),ended_at=item.get('ended_at'),
        pending_confirmations=[dict(id=item['approval']['id'],status='pending',arguments={'detail':item['approval']['detail']})] if item.get('approval') else [],
        monitor_activity_id=item.get('activity') or '')


def validate_items(rows):
    validate(rows)
    if not isinstance(rows,list) or len(rows)>200:raise ValueError('任务数量无效')
    seen=set()
    for r in rows:
        if not isinstance(r,dict):raise ValueError('任务无效')
        for key in ('id','source','status','title','summary','revision'):
            if not isinstance(r.get(key),str):raise ValueError('任务字段类型无效')
        if not isinstance(r.get('activity',''),str):raise ValueError('活动标识无效')
        if len(r['id'])!=64 or r['id'] in seen:raise ValueError('任务身份无效')
        seen.add(r['id'])
        if not isinstance(r.get('steps'),list) or not isinstance(r.get('actions'),list):raise ValueError('任务字段类型无效')
        for step in r['steps']:
            if not isinstance(step,dict) or not isinstance(step.get('name'),str) or not isinstance(step.get('status'),str):raise ValueError('步骤无效')
        for action in r['actions']:
            if not isinstance(action,dict) or action.get('type') not in OPERATIONS:raise ValueError('能力无效')
        for field in ('tools','metrics'):
            if not isinstance(r.get(field,[]),list):raise ValueError('详情无效')
            for entry in r.get(field,[]):
                if not isinstance(entry,dict) or any(not isinstance(v,str) for v in entry.values()):raise ValueError('详情无效')
        for field in ('started_at','updated_at','ended_at'):
            if r.get(field) is not None and not isinstance(r[field],str):raise ValueError('日期无效')
        if 'reminder' in r:
            rem=r['reminder']
            if not isinstance(rem,dict):raise ValueError('提醒无效')
            for field in ('due_at','remind_at','expires_at'):
                if type(rem.get(field)) not in (int,float):raise ValueError('提醒时间无效')
        if 'approval' in r:
            if not isinstance(r['approval'],dict) or any(not isinstance(r['approval'].get(k),str) for k in ('id','tool_name','reason','detail')):raise ValueError('审批详情无效')
        if 'questions' in r:
            if not isinstance(r['questions'],list) or len(r['questions'])>12:raise ValueError('问题无效')
            for q in r['questions']:
                if not isinstance(q,dict) or not isinstance(q.get('question'),str) or not isinstance(q.get('options'),list) or any(not isinstance(o,str) for o in q['options']):raise ValueError('问题无效')
        if 'chain_total' in r and (type(r['chain_total']) is not int or r['chain_total']<0):raise ValueError('进度无效')
        layout=r.get('layout',{})
        if not isinstance(layout,dict) or set(layout)-{'context','changes','child_summary','compaction','stages'}:raise ValueError('布局提示无效')
        for field in ('context','changes','child_summary','compaction'):
            if not isinstance(layout.get(field,{}),dict) or any(type(v) not in (int,float,bool) for v in layout.get(field,{}).values()):raise ValueError('布局提示无效')
        if not isinstance(layout.get('stages',[]),list) or len(layout.get('stages',[]))>7 or any(not isinstance(s,dict) or s!={'agent':'stage'} for s in layout.get('stages',[])):raise ValueError('阶段提示无效')
        if type(r.get('elapsed_ms',0)) not in (int,float) or r.get('elapsed_ms',0)<0:raise ValueError('时间无效')
    return rows
