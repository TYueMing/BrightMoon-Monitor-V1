"""Bounded native detail cards. Peer data is text, never executable UI."""
import json
from ..UI.common_model import STATUS_TEXT, FINAL
from ..UI.completion_panel import summary_lines

class PeerPanel:
    def __init__(self):
        from ..deepseek_monitor.question_form import QuestionForm
        self.form=QuestionForm();self.question_request=None
    def header(self,run,status):return run.get('remote_name','远程')
    def rows(self,ui):return ui._run().get('steps',[])
    def summary_lines(self,ui):return summary_lines(ui._run().get('message',''),lambda s:ui._fonts['body'].measure(s)/ui.scale,width=ui._size[0]-52)
    def size(self,ui):
        from .UI.geometry import detail_size
        return detail_size(ui)
    def draw(self,ui,color):
        run=ui._run();w,h=ui._size
        ui.canvas.create_image(26,88,image=ui.device_icons.get('devices',round(18*ui.scale),color))
        ui._text(42,88,'远程 · '+run.get('remote_name',''),font='small',width=w-186)
        count=len({r['remote_origin'] for r in ui.all_visible.values() if r.get('remote_origin')})
        ui._button((w-132,74,w-20,100),'peer:picker','返回详情' if ui.picker else f'切换来源 · {count}')
        if ui.picker:return self.picker(ui)
        lines=self.summary_lines(ui)[:4 if run.get('status') in FINAL else 1 if h<240 else 2]
        pending=run.get('remote_entity',{}).get('approval')
        if pending:lines=[pending.get('tool_name') or '需要你的决定',pending.get('reason') or '请核对完整操作内容']
        for i,line in enumerate(lines):ui._text(24,116+i*19,line,font='small',width=w-48)
        chain_y=198 if run.get('status') in FINAL else 142 if h<240 else 166
        steps=run.get('steps',[])[:6]
        for i,step in enumerate(steps):
            x=w/2 if len(steps)==1 else 30+i*(w-60)/(len(steps)-1)
            if i:ui.canvas.create_line(last,chain_y,x,chain_y,fill='#354259',width=2)
            state=step.get('status');ink='#71d9b2' if state=='completed' else '#76b0ff' if state=='running' else '#8191a5'
            radius=8 if state=='running' else 5
            ui.canvas.create_oval(x-radius,chain_y-radius,x+radius,chain_y+radius,fill=ink,outline=ink)
            last=x
        if not steps:
            label='来源未提供执行计划'
            if run.get('remote_source')=='solo_task':
                from ..tasks.schedule import stamp_label
                label='提醒 '+stamp_label(run['remote_entity']['reminder']['due_at'])+' · 来源电脑调度'
            ui._text(24,chain_y,label,font='small',fill='#9eafc7')
        elif not run.get('remote_entity',{}).get('actions'):
            model=run['remote_entity'];total=model.get('chain_total',len(run.get('steps',[])))
            label=f"已观察 {total} 个阶段" if model.get('chain_kind')=='observed' else f"来源计划 · {sum(s.get('status')=='completed' for s in run.get('steps',[]))}/{total}"
            if model.get('chain_truncated'):label=f"展示 {len(run.get('steps',[]))}/{total} 步 · 未提供完整完成数"
            ui._text(w/2,chain_y+20,label,anchor='center',font='small',fill='#9eafc7')
        approval=run.get('remote_entity',{}).get('approval')
        showing=False
        if approval:
            try:body=json.loads(approval['detail'])
            except (ValueError,TypeError):body={'操作详情':approval.get('detail','请在来源核对完整操作')}
            ui._approval_details.show({**approval,'arguments':body},
                x=24,y=196+ui.canvas.offset_y,width=w-48,height=max(40,h-312),scale=ui.scale,
                background=ui.canvas.palette.get('#25334b','#25334b'),foreground=ui.canvas.palette.get('#eef4ff','#eef4ff'),run_id=run['run_id'])
            showing=True
            questions=run.get('remote_entity',{}).get('questions') or []
            if questions:
                self.question_request=dict(control_key=run['remote_origin'],session_id=run['run_id'],id=approval['id'],call_id=approval.get('revision'),
                    questions=[dict(id=str(i),question=q['question'],multiSelect=q['multiple'],options=[{'label':o} for o in q['options']]) for i,q in enumerate(questions)])
                self.form.mount(ui._approval_details,self.question_request,width=w-48,scale=ui.scale,
                    background=ui.canvas.palette.get('#25334b','#25334b'),foreground=ui.canvas.palette.get('#eef4ff','#eef4ff'),
                    enabled=not run.get('connection_error') and ui.pending_key(run) not in ui.peer_pending)
        elif ui.details:
            entries=[(s.get('name','步骤'),STATUS_TEXT.get(s.get('status'),s.get('status',''))) for s in run.get('steps',[])]
            entries += [(t.get('name','工具'),str(t.get('summary') or t.get('status',''))) for t in run.get('tools',[])]
            entries += [(t.get('name','指标'),t.get('summary','')) for t in run.get('metrics',[])]
            offset=ui.peer_scroll.get(run.get('run_id',''),0)
            start=chain_y+42;capacity=max(1,min(8,int((h-94-start)//34)+1))
            for i,(name,value) in enumerate(entries[offset:offset+capacity]):
                ui._text(24,start+i*34,name,width=110,font='small');ui._text(140,start+i*34,value,width=w-164,font='small')
            if entries and h>=350:ui._text(24,h-84,f'{offset+1}–{min(offset+capacity,len(entries))} / {len(entries)} · 滚轮查看更多',font='small')
        bottom=h-28
        ui._button((w-110,bottom-16,w-20,bottom+14),'peer:details','返回进度' if ui.details else '查看详情')
        ui._button((w-180,bottom-16,w-120,bottom+14),'peer:collapse','收起')
        actions=ui.remote_actions(run)
        controls=[]
        labels={'approve_tool':'同意一次','deny_tool':'拒绝','stop':'停止','cancel':'停止','cancel_run':'停止','reject_question':'拒绝回答','answer_question':'提交回答'}
        for a in actions:
            if a['type']=='local_reminder':controls.extend([('忽略 5 分钟',{**a,'variant':'snooze'}),('已知晓',{**a,'variant':'acknowledge'})])
            elif a['type'] in labels:controls.append((labels[a['type']],a))
        controls.sort(key=lambda pair: {'deny_tool':0,'reject_question':0,'local_reminder':1,'approve_tool':2,'answer_question':2}.get(pair[1]['type'],3))
        pending=ui.pending_key(run) in ui.peer_pending
        if controls:
            # Separate row for approval/reminder decisions, always inside this card.
            y=h-97 if approval else h-68
            for i,(label,a) in enumerate(controls[:2]):
                width=(w-48)/len(controls[:2]);x=20+i*(width+8)
                ui._button((x,y,x+width,y+30),'peer:act:'+a['type']+str(i),label,action=a,enabled=not pending)
        else:ui._text(24,bottom,'来源失联' if run.get('connection_error') else '实时状态 · 远程',font='small',width=w-210)
        receipt=run.get('command_receipt')
        if receipt and receipt.get('entity_revision')!=run['remote_entity']['revision']:receipt=None
        notice=(receipt.get('message') or receipt.get('status')) if receipt else ui.peer_notice if ui.peer_notice_key==ui.pending_key(run) else ''
        if notice:ui._text(24,h-116 if approval else h-94,notice,font='small',width=w-48,fill='#dba955')
        return showing
    def picker(self,ui):
        w,h=ui._size
        rows=[r for r in ui.all_visible.values() if r.get('remote_origin')]
        capacity=max(1,int((h-158)//32));start=min(ui.picker_offset,max(0,len(rows)-capacity));ui.picker_offset=start
        for i,r in enumerate(rows[start:start+capacity]):
            ui._button((20,110+i*32,w-20,138+i*32),'peer:select:'+r['run_id'],r.get('remote_name','')+' · '+r.get('task_summary','任务'))
        ui._button((20,h-40,92,h-12),'peer:picker:prev','上一页',enabled=start>0)
        ui._text(w/2,h-26,f'{len(rows)} 个当前任务',anchor='center',font='small')
        ui._button((w-92,h-40,w-20,h-12),'peer:picker:next','下一页',enabled=start+capacity<len(rows))
        return False
    def scroll(self,ui,direction):
        if ui.picker:ui.picker_offset=max(0,ui.picker_offset+direction)
        else:
            key=ui._run().get('run_id','');run=ui._run();n=sum(len(run.get(k,[])) for k in ('steps','tools','metrics'))
            ui.peer_scroll[key]=max(0,min(max(0,n-1),ui.peer_scroll.get(key,0)+direction))
        ui.draw();return True
