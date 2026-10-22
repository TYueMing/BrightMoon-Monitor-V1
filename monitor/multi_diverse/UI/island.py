"""Production two-origin shell; local-only DynamicIsland remains unchanged."""
import time
import tkinter as tk
from tkinter import messagebox
from PIL import Image,ImageDraw,ImageTk
from ...UI.island import DynamicIsland
from ...UI.theme import COLORS
from ...UI.common_model import elapsed,STATUS_TEXT
from ...src.task_identity import task_identity
from ..panel import PeerPanel
from .icons import DeviceIcons

class CollaborativeIsland(DynamicIsland):
    def __init__(self,*args,phone_status=None,peer_catalog=None,**kwargs):
        from .motion import reduced_motion
        self.reduced_motion=reduced_motion()
        self.origin='local';self.origin_focus={};self.all_visible={};self.peer_scroll={};self.picker=False
        self.picker_offset=0;self.peer_pending={};self.peer_notice='';self.peer_notice_key=None
        self.peer_catalog=peer_catalog;self.manual_peer=None
        self.device_icons=DeviceIcons(args[0]);self.peer_panel=PeerPanel();self.phone_status=phone_status
        self._split_frame=None;self._split_key=None;self._split_tk=None;self._base_frame=None
        self._origin_amount=0.;self._origin_start=0.;self._origin_at=0.;self._origin_target=0.
        super().__init__(*args,**kwargs)
    def _split(self):return any(r.get('remote_origin') for r in self.all_visible.values())
    def _run(self):
        if not self._split():return super()._run()
        choices=[r for r in self.all_visible.values() if bool(r.get('remote_origin'))==(self.origin=='remote')]
        wanted=self.origin_focus.get(self.origin)
        return next((r for r in choices if r['run_id']==wanted),choices[0] if choices else {})
    def _panel(self):return self.peer_panel if self._run().get('remote_origin') else super()._panel()
    def _is_idle(self):
        if self._split():return self.origin=='local' and not self._run()
        return super()._is_idle()
    def _target_size(self):
        if self._split():
            from ...UI.display import screen_bounds
            left,top,right,bottom=screen_bounds(self.root)
            width=min(420,int((right-left)/self.scale)-32)
            height=self._panel().size(self)[1]+12 if self.expanded else 62
            return width,min(height,int((bottom-top)/self.scale)-40)
        return super()._target_size()
    def _resize(self):
        if self._split() and self.reduced_motion:
            target=self._target_size();center=self._position[0]+self._size[0]*self.scale/2
            self._animation=None;self._opening=False;self._size=target
            self._geometry(*target,center-target[0]*self.scale/2,self._position[1]);return
        return super()._resize()
    def _draw_ring(self,*args,**kwargs):
        if not self._split() or not self.reduced_motion:return super()._draw_ring(*args,**kwargs)
        phase=self.phase;self.phase=0;kwargs['focus']=0
        try:return super()._draw_ring(*args,**kwargs)
        finally:self.phase=phase
    def _poll(self):
        expanded,details,origin=self.expanded,self.details,self.origin
        previous=set(self.all_visible)
        super()._poll()
        self.all_visible=self.selection.lifetime.visible(self.snapshot)
        if self.manual_peer and self.peer_catalog:
            manual=next((r for r in self.peer_catalog() if r['run_id']==self.manual_peer),None)
            if manual:self.all_visible[manual['run_id']]=manual
            else:self.manual_peer=None
        if not self._split():
            self.origin='local';self._origin_amount=self._origin_target=0.;self.picker=False
        elif previous.intersection(self.all_visible) and expanded:
            self.expanded,self.details,self.origin=expanded,details,origin
        # The full visible catalog is independent of the five recommended rows.
        if self._split() and self._hidden and self.all_visible:
            self._hidden=False;self.root.deiconify()
        if not self._hidden:self._resize();self.draw()
    def _draw_surface(self,w,h,radius):
        if not self._split() or self._opening:return super()._draw_surface(w,h,radius)
        if self._base_frame is not None:self._surface_frame=self._base_frame
        # The split shell uses its full bounds (62px circles, 12px gap),
        # rather than the connected shell's legacy 5px painting inset.
        has=super()._draw_surface(w+10,h+11,radius)
        self.canvas.delete('island_surface')
        frame=self._surface_frame
        self._base_frame=frame
        amount=self._origin_amount
        key=(id(frame),round(amount,3),w,h,self.scale,self.expanded)
        if key!=self._split_key:
            # Mask a single wallpaper scene into three disconnected rounded
            # surfaces, preserving color/opacity and exposing the real desktop.
            image=frame.copy();mask=Image.new('L',image.size);d=ImageDraw.Draw(mask)
            sc=self.scale;circle=62;long=w-circle-10
            lengths=(long+(circle-long)*amount,circle+(long-circle)*amount)
            x=0
            for width in lengths:
                d.rounded_rectangle((round(x*sc),0,min(image.width-1,round((x+width)*sc)-1),round(62*sc)-1),radius=round(31*sc),fill=255)
                x+=width+10
            if self.expanded:d.rounded_rectangle((0,round(74*sc),image.width-1,image.height-1),radius=round(26*sc),fill=255)
            from PIL import ImageChops
            image.putalpha(ImageChops.multiply(image.getchannel('A'),mask))
            self._split_frame=image;self._split_tk=ImageTk.PhotoImage(image,master=self.root);self._split_key=key
        self._surface_frame=self._split_frame
        self.canvas.create_image(0,0,image=self._split_tk,anchor='nw',tags=('island_surface',))
        return has
    def _draw_halos(self):
        if not self._split() or self._opening:return super()._draw_halos()
        requests=[(x+5,y+5,r,color,pulse) for x,y,r,color,pulse in self._halo_requests]
        frame=self._surface.halos(self._surface_frame,requests,self.scale)
        if frame is None:return
        if frame is not self._halo_frame:
            self._halo_tk=ImageTk.PhotoImage(frame,master=self.root);self._halo_frame=frame
        item=self.canvas.create_image(0,0,image=self._halo_tk,anchor='nw',tags=('task_ring_halo',))
        self.canvas.tag_raise(item,'island_surface')
    def draw(self):
        if not getattr(self,'canvas',None):return
        if not self._split() or self._opening:
            super().draw()
            if not self._hidden and not self.expanded and self.phone_status and self.phone_status().get('connected_device_count',0):
                # Local header reserves room via TaskStrip's optional inset.
                self.canvas.create_image(self._size[0]-32,31,image=self.device_icons.get('phone',round(16*self.scale),'#71d9b2'),tags=('phone_connected',))
            return
        if self._hidden:return
        self.canvas.offset_y=0;self.canvas.delete('all');self._buttons=[];self._halo_requests=[]
        self._sync_pendant();w,h=self._size
        t=min(1,max(0,(time.monotonic()-self._origin_at)/.42));ease=1-(1-t)**3
        if self.reduced_motion:ease=1
        self._origin_amount=self._origin_start+(self._origin_target-self._origin_start)*ease
        self.canvas.wallpaper_active=self._draw_surface(w,h,27)
        long=w-72;lx=0 if self.origin=='local' else 72
        local_width=long+(62-long)*self._origin_amount
        for origin,x,width in [('local',0,local_width),('remote',local_width+10,w-local_width-10)]:
            focused=origin==self.origin
            rows=[r for r in self.all_visible.values() if bool(r.get('remote_origin'))==(origin=='remote')]
            run=next((r for r in rows if r['run_id']==self.origin_focus.get(origin)),rows[0] if rows else {})
            state=('offline' if origin=='remote' else 'error') if run.get('connection_error') else run.get('status','idle')
            color=COLORS.get(state,'#43d994' if state=='idle' else '#76b0ff')
            self._register((x+5,5,x+width-5,57),'peer:origin:'+origin)
            keyboard=self._focus_key=='peer:origin:'+origin
            if origin=='local':self._draw_ring(x+31,31,color,state,run=run or {'app_id':'solo_idle'},focus=float(focused),keyboard_focus=keyboard)
            else:
                self._draw_ring(x+31,31,color,state,run={'app_id':'solo_peer'},focus=float(focused),keyboard_focus=keyboard)
                if state not in ('completed','failed','error','stale'):
                    self.canvas.create_image(x+31,31,image=self.device_icons.get('devices',round(22*self.scale),'#26364b'))
            if focused and width>200:
                phone=origin=='local' and self.phone_status and self.phone_status().get('connected_device_count',0)
                inset=24 if phone else 0
                label=run.get('remote_connection_message') if run.get('connection_error') and origin=='remote' else STATUS_TEXT.get(state,'空闲')
                self._text(x+60,24,('本地' if origin=='local' else '远程')+' · '+label,width=width-140-inset,font='title')
                self._text(x+60,42,run.get('remote_name') or run.get('task_summary') or 'Bright Moon',width=width-145-inset,font='small',fill='#9eafc7')
                self._text(x+width-22-inset,31,elapsed(run) if run else '空闲',anchor='e',font='time')
                if phone:self.canvas.create_image(x+width-32,31,image=self.device_icons.get('phone',round(16*self.scale),'#71d9b2'),tags=('phone_connected',))
        self._draw_halos()
        if self.expanded and h>100:
            old=self._size
            self.canvas.offset_y=12;self._size=(w,h-12)
            approval=self._panel().draw(self,COLORS.get(self._state(),'#76b0ff'))
            if not approval:self._approval_details.hide()
            self._size=old;self.canvas.offset_y=0
        else:self._approval_details.hide()
    def phone_inset(self):
        return 24 if self.phone_status and not self.expanded and self.phone_status().get('connected_device_count',0) else 0
    def _play_new_completions(self,snapshot):
        super()._play_new_completions({**snapshot,'runs':[r for r in snapshot.get('runs',[]) if not r.get('remote_origin')]})
    def _activate(self,button):
        key=button['key']
        if key.startswith('peer:origin:'):
            self.picker=False;self.peer_notice=''
            origin=key.split(':')[-1]
            if origin==self.origin:
                self.expanded=not self.expanded;self.details=False
                if not self.expanded:self.manual_peer=None
            else:
                self.origin=origin;self._origin_start=self._origin_amount;self._origin_target=float(origin=='remote');self._origin_at=time.monotonic()
            self._resize();self.draw();return
        if key=='peer:details':self.details=not self.details;self._resize();self.draw();return
        if key=='peer:collapse':self.expanded=self.details=False;self.manual_peer=None;self._resize();self.draw();return
        if key=='peer:picker':self.open_picker();return
        if key.startswith('peer:select:'):
            self.origin_focus[self.origin]=key.removeprefix('peer:select:');self.picker=False;self.peer_notice=''
            self._resize();self.draw();return
        if key=='peer:picker:prev' or key=='peer:picker:next':
            self.picker_offset=max(0,self.picker_offset+(-2 if key.endswith('prev') else 2));self.draw();return
        if key.startswith('peer:act:'):
            self.send_peer_action(button.get('action',{}));return
        return super()._activate(button)
    @staticmethod
    def remote_actions(run):return [] if run.get('connection_error') or not run.get('remote_entity',{}).get('capability') else run.get('remote_entity',{}).get('actions',[])
    @staticmethod
    def pending_key(run):
        entity=run.get('remote_entity',{});approval=entity.get('approval') or {}
        return (run.get('run_id'),approval.get('revision') or entity.get('revision'))
    def local_actions(self,run):return [a for a in self.snapshot.get('actions',[]) if a.get('run_id')==run.get('run_id')]
    def open_picker(self):
        # Picker replaces the detail area; it never opens a floating window.
        self.picker=not self.picker;self.picker_offset=0;self.draw()
    def inspect_remote(self,run):
        self.manual_peer=run['run_id'];self.all_visible[run['run_id']]=run
        self.origin='remote';self.origin_focus['remote']=run['run_id'];self._origin_target=1.;self._origin_at=0
        self.expanded=True;self.details=False;self._hidden=False;self.root.deiconify();self._resize();self.draw()
    def _escape(self):
        if self.picker:self.picker=False;self.draw();return 'break'
        self.manual_peer=None
        return super()._escape()
    def send_peer_action(self,action):
        run=self._run();entity=run.get('remote_entity',{})
        key=self.pending_key(run);self.peer_notice_key=key
        if key in self.peer_pending or run.get('connection_error'):return
        payload={k:action[k] for k in ('confirmation_id','expected_revision','cycle') if k in action}
        if action.get('variant'):payload['action']=action['variant']
        if 'answers' in action:payload['answers']=action['answers']
        try:
            if action['type']=='answer_question':
                values=self.peer_panel.form.answer(self.peer_panel.question_request)['answers']
                questions=entity.get('questions',[])
                payload['answers']=[]
                for q,v in zip(questions,values):
                    answers=list(v['selected'])
                    if v.get('custom'):
                        if q.get('custom') is False:raise ValueError('此问题仅允许选择提供的选项')
                        answers.append(v['custom'])
                    if not q.get('multiple') and len(answers)!=1:raise ValueError('单选问题只能提供一个答案')
                    payload['answers'].append(answers)
            receipt=self.command_sender(action['type'],run['run_id'],payload)
            if len(self.peer_pending)>=500:self.peer_pending.pop(next(iter(self.peer_pending)))
            self.peer_pending[key]=receipt.get('command_id','')
            self.peer_notice='已发送，等待来源确认'
        except Exception as error:self.peer_notice=str(error) or '发送失败，请核对设备连接'
        self.draw()
