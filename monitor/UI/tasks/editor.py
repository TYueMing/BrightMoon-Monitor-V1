"""Wallpaper-aware task editor with native keyboard/text controls."""
from datetime import datetime, timedelta
import sqlite3
import tkinter as tk
from tkinter import messagebox

from PIL import ImageTk

from ..settings_panel import compose_background
from ..panel_palette import panel_palette, contrast
from .widgets import RoundedButton


class TaskEditor(tk.Toplevel):
    def __init__(self, app, task=None):
        super().__init__(app.root)
        self.app, self.task = app, task
        self.title("编辑任务" if task else "新增任务")
        self.transient(app.root)
        self.resizable(False, False)
        scale = app.scale
        self.s = app.s
        width, height = self.s(590), self.s(672)
        self.canvas = tk.Canvas(self, width=width, height=height, highlightthickness=0)
        self.canvas.pack()
        prefs = app.store.snapshot()
        box = (self.s(18),self.s(18),width-self.s(18),height-self.s(18))
        self.background = compose_background((width,height),app.photo,box,prefs["panel_transparency"],scale,prefs["panel_color"])
        self.background_tk = ImageTk.PhotoImage(self.background,master=self)
        self.canvas.create_image(0,0,image=self.background_tk,anchor="nw")
        palette = panel_palette(self.background,box)
        self.ink = palette["#1c2940"]
        muted = palette["#697589"]
        surface = "#"+"".join(f"{v:02x}" for v in self.background.getpixel((self.s(285),self.s(310))))
        self.surface = surface
        def label(x,y,text,small=False,bold=False):
            self.canvas.create_text(self.s(x),self.s(y),anchor="w",text=text,fill=muted if small else self.ink,
                font=app.small if small else app.bold if bold else app.font)
        label(38,48,"编辑任务" if task else "新增任务",bold=True)
        from ...tasks.schedule import local_zone
        zone=(task or {}).get('timezone') or local_zone()
        label(38,76,f"提醒时区：{zone} · 由本机调度",small=True)
        future = datetime.now()+timedelta(minutes=15)
        initial = task or dict(title="",body="",start_date=future.date().isoformat(),
                              time_of_day=future.strftime("%H:%M"),weekdays=[],lead_minutes=5,enabled=True)
        self.values = {key:tk.StringVar(self,value=str(initial[key])) for key in ("title","start_date","time_of_day","lead_minutes")}
        def entry(key,x,y,w):
            widget = tk.Entry(self,textvariable=self.values[key],font=app.font,bg=surface,fg=self.ink,
                              insertbackground=self.ink,relief="flat",highlightthickness=1,
                              highlightbackground=muted,highlightcolor=palette["#426de7"])
            self.canvas.create_window(self.s(x),self.s(y),anchor="nw",window=widget,width=self.s(w),height=self.s(32))
            return widget
        label(38,108,"标题",bold=True)
        self.title_entry = entry("title",38,124,514)
        label(38,179,"内容",bold=True)
        self.body = tk.Text(self,font=app.font,bg=surface,fg=self.ink,insertbackground=self.ink,
                            wrap="word",relief="flat",highlightthickness=1,highlightbackground=muted)
        self.canvas.create_window(self.s(38),self.s(195),anchor="nw",window=self.body,width=self.s(514),height=self.s(112))
        self.body.insert("1.0",initial["body"])
        label(38,333,"首次日期 YYYY-MM-DD")
        label(324,333,"时间 HH:MM")
        entry("start_date",38,349,252)
        entry("time_of_day",324,349,228)
        label(38,408,"每周重复",bold=True)
        label(160,408,"与间隔互斥；两项不选为单次",small=True)
        self.weekdays = []
        self.weekday_checks = []
        for day in range(7):
            var = tk.BooleanVar(self,value=day in initial["weekdays"])
            self.weekdays.append(var)
            check = tk.Checkbutton(self,text="周"+"一二三四五六日"[day],variable=var,font=app.small,
                                   bg=surface,fg=self.ink,selectcolor=surface,activebackground=surface,
                                   activeforeground=self.ink,highlightthickness=0,
                                   command=self.select_weekdays)
            self.weekday_checks.append(check)
            self.canvas.create_window(self.s(38+day*74),self.s(427),anchor="nw",window=check)
        self.interval_enabled = tk.BooleanVar(self, value=bool(initial.get("interval_hours", 0)))
        self.interval_hours = tk.StringVar(self, value=str(initial.get("interval_hours") or 2))
        self.interval_check = tk.Checkbutton(self,text="间隔重复",variable=self.interval_enabled,font=app.font,
            bg=surface,fg=self.ink,selectcolor=surface,activebackground=surface,activeforeground=self.ink,
            highlightthickness=0,command=self.select_interval)
        self.canvas.create_window(self.s(38),self.s(466),anchor="nw",window=self.interval_check)
        label(164,482,"每隔")
        self.interval_input = tk.Spinbox(self,from_=1,to=8760,textvariable=self.interval_hours,font=app.font,
            bg=surface,fg=self.ink,insertbackground=self.ink,relief="flat",buttonbackground=surface,
            disabledbackground=surface,disabledforeground=muted)
        self.canvas.create_window(self.s(202),self.s(466),anchor="nw",window=self.interval_input,width=self.s(70),height=self.s(30))
        label(286,482,"小时 · 以首次时间为起点",small=True)
        self.sync_repeat_controls()
        label(38,532,"提前提醒",bold=True)
        self.lead = tk.Spinbox(self,from_=2,to=10,textvariable=self.values["lead_minutes"],font=app.font,
                              bg=surface,fg=self.ink,insertbackground=self.ink,relief="flat",buttonbackground=surface)
        self.canvas.create_window(self.s(136),self.s(516),anchor="nw",window=self.lead,width=self.s(68),height=self.s(30))
        label(218,532,"分钟（2–10）；到预定时间自动收起",small=True)
        self.error = self.canvas.create_text(self.s(38),self.s(569),anchor="w",text="",fill="#c24c43",
                                            font=app.small,width=self.s(514))
        self.buttons = {}
        def button(x,text,command,accent=False):
            fill = palette["#426de7"] if accent else palette["#f0f2f6"]
            ink = max(("#ffffff","#1c2940"),key=lambda c:contrast(fill,c)) if accent else self.ink
            self.buttons[text] = RoundedButton(self,x,604,text,command,
                fill=fill,ink=ink)
        if task:
            button(38,"删除任务",self.delete)
        button(350,"取消",self.close)
        button(458,"保存",self.save,True)
        self.protocol("WM_DELETE_WINDOW",self.close)
        self.bind("<Escape>",lambda _:self.close())
        self.bind("<Control-Return>",lambda _:self.save())
        app.center_dialog(self)
        self.grab_set()
        self.title_entry.focus_set()

    def select_interval(self):
        if self.interval_enabled.get():
            for weekday in self.weekdays:
                weekday.set(False)
        self.sync_repeat_controls()

    def select_weekdays(self):
        if any(day.get() for day in self.weekdays):
            self.interval_enabled.set(False)
        self.sync_repeat_controls()

    def sync_repeat_controls(self):
        self.interval_input.configure(state="normal" if self.interval_enabled.get() else "disabled")

    def save(self):
        value = {key:var.get() for key,var in self.values.items()}
        value.update(body=self.body.get("1.0","end-1c"),weekdays=[i for i,v in enumerate(self.weekdays) if v.get()],
                     enabled=self.task["enabled"] if self.task else True,
                     interval_hours=self.interval_hours.get() if self.interval_enabled.get() else 0)
        try:
            self.app.tasks.store.save(value,self.task["id"] if self.task else None,
                                      expected_revision=self.task["revision"] if self.task else None)
        except (OSError,ValueError,sqlite3.Error) as error:
            self.canvas.itemconfigure(self.error,text=str(error))
            return False
        self.app.tasks_changed()
        self.close()
        return True

    def delete(self):
        if not messagebox.askyesno("删除任务","删除这个本地待办及其提醒记录？",parent=self):
            return
        try:
            self.app.tasks.store.delete(self.task["id"])
        except (OSError,ValueError,sqlite3.Error) as error:
            self.canvas.itemconfigure(self.error,text=str(error))
            return
        self.app.tasks_changed()
        self.close()

    def close(self):
        self.app.task_window = None
        self.destroy()
