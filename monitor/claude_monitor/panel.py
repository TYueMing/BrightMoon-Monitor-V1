"""Claude-specific second/third state; capsule animation remains source-neutral."""
import time
from weakref import WeakKeyDictionary
from ..UI.common_model import FINAL, STATUS_TEXT, authorization_actions, pending_confirmation
from ..UI.completion_panel import summary_lines
from ..UI.theme import COLORS
from ..UI.display import screen_bounds


class ClaudePanel:
    def __init__(self):self.pages=WeakKeyDictionary();self.layouts=WeakKeyDictionary()
    def header(self,run,status):
        label=STATUS_TEXT.get(status,status) if status in FINAL or status in ('stale','paused','error') else run.get('claude',{}).get('stage','执行中')
        return 'Claude · '+label
    def rows(self,ui):return ui._run().get('tools',[])
    def summary_lines(self,ui):
        run=ui._run()
        return summary_lines(run.get('summary_preview') or run.get('message'),
            lambda s:ui._fonts['body'].measure(s)/ui.scale,width=ui.WIDTH-52)[:3]
    def opened(self,ui):
        return ui._completion_rows.get(ui._run().get('run_id'),False) if ui._state() in FINAL else ui.details
    def size(self,ui):
        _,top,_,bottom=screen_bounds(ui.root)
        limit=max(350,int((bottom-top-80)/ui.scale)-24)
        height=440 if ui._state()=='waiting_confirmation' else 580 if self.opened(ui) else 310 if ui._state() in FINAL else 244
        return ui.WIDTH,min(limit,height)
    def chain(self,ui,y):
        info=ui._run().get('claude',{});plan=info.get('plan') or []
        steps=plan or ui._run().get('steps',[])
        active=next((i for i,s in enumerate(steps) if s.get('status')=='running'),len(steps)-1)
        start=max(0,min(active-2,len(steps)-6));nodes=steps[start:start+6];w=ui._size[0]
        if not nodes:
            ui._text(26,y,'等待 Claude 阶段事件',font='small',fill='#9eafc7');return
        xs=[w/2] if len(nodes)==1 else [34+i*(w-68)/(len(nodes)-1) for i in range(len(nodes))]
        for x,nx in zip(xs,xs[1:]):ui.canvas.create_line(x,y,nx,y,fill='#354259',width=2)
        for x,step in zip(xs,nodes):
            color=COLORS.get(step.get('status'),COLORS['pending']);running=step.get('status')=='running';r=8 if running else 5
            ui.canvas.create_oval(x-r,y-r,x+r,y+r,fill='#141d2c' if running else color,outline=color,width=2)
            if running:ui.canvas.create_oval(x-3,y-3,x+3,y+3,fill=color,outline='')
        label=(f"Claude 计划 {sum(s.get('status')=='completed' for s in plan)}/{len(plan)}" if plan else
               '已观察阶段 · '+info.get('stage','等待事件'))
        ui._text(w/2,y+25,label,anchor='center',font='small',fill='#9eafc7',width=w-52)
    def entries(self,ui):
        run=ui._run();info=run.get('claude',{});entries=['阶段时间线（非总进度百分比）']
        entries += [s.get('name','')+' · '+STATUS_TEXT.get(s.get('status'),'待执行') for s in run.get('steps',[])]
        if info.get('plan'):
            entries.append('Claude 报告的任务计划')
            entries += [s.get('name','')+' · '+STATUS_TEXT.get(s.get('status'),'待执行') for s in info['plan']]
        entries.append('近期工具结果（从新到旧）')
        for tool in reversed(self.rows(ui)):
            entries.append(tool.get('name','工具')+' · '+STATUS_TEXT.get(tool.get('status'),'待核对'))
            if tool.get('detail'):entries.append(tool['detail'])
            if tool.get('result'):entries.append('结果：'+tool['result'])
            if tool.get('exit_code') is not None:entries.append('退出码：'+str(tool['exit_code']))
        entries += ['子 Agent',*[v['name']+' · '+STATUS_TEXT.get(v['status'],'待核对') for v in info.get('children',{}).values()],
                    f"已观察上下文压缩 {info.get('compactions',0)} 次",'上下文 tokens / 百分比：Hook 未提供可靠用量',
                    '模型：'+(info.get('model') or '未报告'),'目录：'+(info.get('cwd') or '未报告'),
                    '最新事件：'+info.get('event',''),
                    '计时依据：'+('本轮请求提交' if info.get('time_basis')=='reported' else '首次观察到本轮事件'),
                    '响应结束不等于目标验证成功；验证结果以实际工具输出为准。']
        key=(tuple(entries),ui.WIDTH,ui.scale)
        cached=self.layouts.get(ui)
        if cached and cached[0]==key:return cached[1]
        lines=[line for entry in entries for line in summary_lines(entry,
            lambda s:ui._fonts['body'].measure(s)/ui.scale,width=ui.WIDTH-64)]
        self.layouts[ui]=(key,lines);return lines
    def details(self,ui,y):
        w,h=self.size(ui);lines=self.entries(ui);capacity=max(1,int((h-y-64)//22));rid=ui._run()['run_id']
        page=self.pages.setdefault(ui,{})
        start=max(0,min(page.get(rid,0),max(0,len(lines)-capacity)));page[rid]=start
        for old in set(page)-{r.get('run_id') for r in ui.snapshot.get('runs',[])}:page.pop(old,None)
        ui._rounded(ui.canvas,18,y-15,w-18,h-18,12,'#1a2639')
        ui._text(28,y+2,'Claude 执行详情',font='small',fill='#9eafc7')
        for key,label,x,enabled in [('prev','上页',w-92,start>0),('next','下页',w-44,start+capacity<len(lines))]:
            ui._text(x,y+2,label,anchor='center',font='small',fill='#d4e0f2' if enabled else '#8292ab')
            ui._register((x-20,y-12,x+20,y+14),'panel:claude:'+key,enabled)
        for i,line in enumerate(lines[start:start+capacity]):ui._text(30,y+30+22*i,line,width=w-60)
    def scroll(self,ui,direction):
        if not self.opened(ui) or ui._state()=='waiting_confirmation':return False
        page=self.pages.setdefault(ui,{});rid=ui._run()['run_id'];page[rid]=max(0,page.get(rid,0)+direction);ui.draw();return True
    def activate(self,ui,key):
        if key in ('panel:claude:prev','panel:claude:next'):self.scroll(ui,-4 if key.endswith('prev') else 4)
    def approval(self,ui,color):
        run=ui._run();w,h=self.size(ui);pending=pending_confirmation(run)
        ui._text(26,87,pending.get('tool_name') or 'Claude 授权请求',font='title',fill=color,width=w-52)
        ui._text(26,115,pending.get('reason') or '请核对本次操作',font='small',fill='#c4d1e5',width=w-52)
        actions=authorization_actions(ui.snapshot,run)
        label='仅授权本次，不修改 Claude 权限策略' if actions else '请在 Claude 原界面处理或等待 Hook 响应'
        if pending.get('truncated'):label='参数未完整显示，必须在 Claude 原界面审批'
        if ui._notice_run_id==run.get('run_id') and ui._notice_until>time.monotonic() and ui._notice:label=ui._notice
        ui._text(26,h-80,label,font='small',fill='#dba955',width=w-52)
        for typ,label,x1,x2,fill in [('deny_tool','拒绝',20,w/2-6,'#303d54'),('approve_tool','同意一次',w/2+6,w-20,'#3168ff')]:
            action=actions.get(typ);enabled=bool(action and ui.command_sender and not ui._pending and not run.get('connection_error'))
            ui._button((x1,h-58,x2,h-20),action['id'] if action else typ,label,fill,action,enabled)
        y=139+ui.canvas.offset_y;dh=h-240
        if ui._size[1]>=y+dh+6:
            ui._approval_details.show({**pending,'arguments':pending.get('arguments') or {'说明':'请在 Claude 核对操作'}},
                x=26,y=y,width=w-52,height=dh,scale=ui.scale,
                background=ui.canvas.palette.get('#25334b','#25334b'),foreground=ui.canvas.palette.get('#eef4ff','#eef4ff'),run_id=run['run_id'])
            return True
        return False
    def draw(self,ui,color):
        if ui._state()=='waiting_confirmation':return self.approval(ui,color)
        run=ui._run();w=ui._size[0]
        ui._text(26,85,'Claude Code · 本机 Hook 事件',font='small',fill='#9eafc7');y=110
        if ui._state() in FINAL:
            for line in self.summary_lines(ui):ui._text(26,y,line,width=w-52);y+=21
            y+=10
        else:ui._text(26,y,run.get('message') or '等待事件',width=w-52);y+=38
        self.chain(ui,y);y+=57
        ui._text(26,y,'执行详情',font='small');ui._text(w-26,y,'收起 ⌃' if self.opened(ui) else '展开 ⌄',font='small',anchor='e')
        ui._register((18,y-16,w-18,y+16),'completion_rows' if ui._state() in FINAL else 'details')
        if self.opened(ui):self.details(ui,y+41)
        return False
