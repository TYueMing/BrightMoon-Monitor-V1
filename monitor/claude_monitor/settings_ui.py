"""Claude-owned settings UI; the shared application only routes to this page."""
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
from . import installer
from ..src.source_config import claude_event_root


def open_settings(app):
    dialog=app.dialog=tk.Toplevel(app.root)
    dialog.title('Claude Code · 监控设置');dialog.transient(app.root);dialog.resizable(False,False)
    prefs=app.store.snapshot();bg=prefs.get('panel_color','#ffffff')
    dialog.configure(bg=bg)
    body=tk.Frame(dialog,bg=bg,padx=24,pady=20);body.pack(fill='both',expand=True)
    fg='#1c2940' if sum(int(bg[i:i+2],16) for i in (1,3,5))>380 else '#eef4ff'
    def label(value,**kwargs):
        w=tk.Label(body,text=value,bg=bg,fg=fg,font=app.font,justify='left',wraplength=520,**kwargs)
        w.pack(anchor='w',pady=5);return w
    label('Claude Code · 真实阶段与单次授权')
    label('支持桌面 Code 标签页及 CLI 本机会话。普通 Chat / Cowork 不支持。\nWSL、SSH、云端须在执行主机另行部署，不能只填写桌面程序路径。')
    config=prefs['monitor_sources']['claude']
    label('本机事件目录（留空使用默认位置）')
    path=tk.StringVar(value=config['data_path'])
    row=tk.Frame(body,bg=bg);row.pack(fill='x',pady=6)
    entry=tk.Entry(row,textvariable=path,font=app.font,width=48);entry.pack(side='left',ipady=5)
    def browse():
        chosen=filedialog.askdirectory(parent=dialog,title='选择 Claude 事件目录')
        if chosen:path.set(chosen)
    app.button(row,'浏览',browse).pack(side='left',padx=8)
    approve=tk.BooleanVar(value=config.get('allow_approvals',False))
    tk.Checkbutton(body,text='允许在灵动岛同意 / 拒绝本次工具授权',variable=approve,bg=bg,fg=fg,
                   selectcolor=bg,activebackground=bg,font=app.font).pack(anchor='w',pady=8)
    label('安装仅添加 Monitor SOLO 的 Hook，不改变 Claude 权限策略。\n未开启监控时 Hook 快速返回；授权等待最长 60 秒，超时交回 Claude。\n隐藏、过长参数及网络类授权请在 Claude 原界面处理。')
    status_label=label('')
    def root():return claude_event_root({'data_path':path.get().strip()})
    def check():status_label.configure(text=installer.status(spool=root())['message'])
    def save():
        app.store.set_source('claude',data_path=path.get().strip(),allow_approvals=approve.get())
        app.hub.read()
    def install():
        try:
            # Validate paths/preferences before changing external Claude settings.
            from ..src.source_config import normalize_sources
            normalize_sources({'claude':{'data_path':path.get().strip(),'allow_approvals':approve.get()}},strict=True)
            installer.install(spool=root());save();check()
            app.notice('Claude Hook 已安装 / 修复，请在 Claude 中新建 Code 会话')
        except (OSError,ValueError) as exc:messagebox.showerror('未能安装 Hook',str(exc),parent=dialog)
    def remove():
        try:
            app.store.set_source('claude',enabled=False);app.hub.read()
            installer.uninstall();check();app.notice('已卸载 SOLO 的 Claude Hook，其他 Hook 保留')
        except (OSError,ValueError) as exc:messagebox.showerror('未能卸载 Hook',str(exc),parent=dialog)
    def apply():
        try:save();dialog.destroy();app.notice('Claude 监控设置已保存；路径变化后请重新安装 Hook')
        except (OSError,ValueError) as exc:messagebox.showerror('未能保存',str(exc),parent=dialog)
    buttons=tk.Frame(body,bg=bg);buttons.pack(fill='x',pady=12)
    for title,callback in [('安装 / 修复 Hook',install),('检查',check),('卸载 Hook',remove),('保存',apply)]:
        app.button(buttons,title,callback,accent=title=='安装 / 修复 Hook').pack(side='left',padx=4)
    # Exposed callbacks let native tests exercise the same user flow in a temp profile.
    dialog.claude_controls=dict(install=install,check=check,remove=remove,save=apply,path=path,approvals=approve)
    check();app.center_dialog(dialog);dialog.grab_set()
    return dialog
