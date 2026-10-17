"""Paginated local task list on the existing composited settings surface."""
import time
import sqlite3
from tkinter.font import Font
from PIL import Image, ImageOps, ImageTk
from ...src.app_sources import ICON_ROOT
from ...tasks.schedule import next_due, repeat_label, stamp_label


def bounded_text(panel, x, y, value, width, size=13, color="#1c2940", *, bold=False):
    """Keep variable-width names and weekday labels inside their card row."""
    font = Font(root=panel, family="Microsoft YaHei UI", size=-round(size*panel.scale),
                weight="bold" if bold else "normal")
    value = str(value)
    if font.measure(value) > width*panel.scale:
        while value and font.measure(value+"…") > width*panel.scale:
            value = value[:-1]
        value += "…"
    return panel.text(x, y, value, size, color, bold=bold)


def draw_tasks(panel):
    app = panel.app
    panel.text(180, 136, "任务", 17, bold=True)
    panel.text(180, 161, "把重要的小事，放在抬眼可见的地方。", 11, "#697589")
    prefs = app.store.snapshot()
    grid = prefs["task_view"] == "grid"
    page_size = 6 if grid else 4
    panel.button("task-new", 542, 120, 124, "＋ 新增任务", app.task_dialog, accent=True)
    icon_name = "navigation.png" if grid else "grid.png"
    cache = getattr(panel, "_task_view_icons", {})
    icon_key = (icon_name, panel.scale, panel.palette.get("#1c2940"))
    if icon_key not in cache:
        with Image.open(ICON_ROOT/icon_name) as source:
            icon = ImageOps.contain(source.convert("RGBA"), (round(20*panel.scale),)*2, Image.Resampling.LANCZOS)
        # The supplied assets are monochrome; tint their alpha for dark panels.
        tinted = Image.new("RGBA",icon.size,panel.palette.get("#1c2940","#1c2940"))
        tinted.putalpha(icon.getchannel("A"))
        cache = {icon_key: ImageTk.PhotoImage(tinted, master=panel)}
        panel._task_view_icons = cache
    panel.create_image(*panel.point(699,135),image=cache[icon_key],tags=("front","task_view_icon"))
    def switch_view():
        panel.task_page = 0
        app.save(task_view="list" if grid else "grid")
    panel.control("task-view",(680,120,718,150),switch_view)
    try:
        tasks = app.tasks.store.list()
        now = time.time()
        states = {t["id"]: app.tasks.store.state_for(t["id"]) for t in tasks}
        upcoming = {}
        for task in tasks:
            state = states[task["id"]]
            after = max(now, state["due_at"]) if state.get("state") == "acknowledged" else now
            upcoming[task["id"]] = (state["remind_at"] if state.get("state") == "snoozed"
                                    else next_due(task, after))
        tasks.sort(key=lambda t: (not t["enabled"], upcoming[t["id"]] or float("inf"), t["created_at"]))
        page = max(0, min(getattr(panel, "task_page", 0), max(0, (len(tasks)-1)//page_size)))
        panel.task_page = page
        if not tasks:
            panel.text(449, 310, "还没有安排任务", 17, bold=True, anchor="center")
            panel.text(449, 346, "添加一个时间，让灵动岛提前提醒你。", 12, "#697589", anchor="center")
        for index, task in enumerate(tasks[page*page_size:page*page_size+page_size]):
            state = states[task["id"]]
            due = upcoming[task["id"]]
            label = "已暂停" if not task["enabled"] else "提醒中" if state.get("state")=="active" else (
                "稍后提醒 " + stamp_label(state["remind_at"]) if state.get("state")=="snoozed" else
                "下一次 " + stamp_label(due) if due else
                "本次已知晓" if state.get("state")=="acknowledged" else "已到期")
            if not prefs["task_monitor_enabled"] and task["enabled"]:
                label = "监控已关闭 · " + stamp_label(due)
            if grid:
                x,y = 180+(index%2)*276,184+(index//2)*119
                clock = due or next_due({**task,"enabled":True},now)
                grid_label = label
                if state.get("state") == "active" and prefs["task_monitor_enabled"] and task["enabled"]:
                    grid_label = "提醒中 · " + stamp_label(state["due_at"])
                elif not task["enabled"]:
                    grid_label = "已暂停 · " + stamp_label(clock)
                panel.rounded_frame((x,y,x+262,y+108),radius=10,
                                    fill=panel.palette.get("#f0f2f6"),outline="")
                bounded_text(panel,x+14,y+22,task["title"],234,13,bold=True)
                bounded_text(panel,x+14,y+47,grid_label,234,11,"#697589")
                panel.button("task-edit:"+task["id"],x+14,y+66,64,"编辑",lambda t=task:app.task_dialog(t))
                panel.text(x+153,y+81,"开启",11,"#697589")
                panel.toggle("task-enable:"+task["id"],x+200,y+66,task["enabled"],
                             lambda enabled,t=task:app.task_toggle(t,enabled))
                continue
            y = 184 + index*89
            panel.rounded_frame((180, y, 718, y+80), radius=10,
                                fill=panel.palette.get("#f0f2f6", "#f0f2f6"), outline="")
            bounded_text(panel, 194, y+19, task["title"], 380, 13, bold=True)
            body = " ".join(task["body"].split()) or "未添加内容"
            bounded_text(panel, 194, y+40, body[:200], 510, 11, "#697589")
            bounded_text(panel, 194, y+63, f'{label} · {repeat_label(task)} · 提前{task["lead_minutes"]}分钟',
                         510, 10, "#697589")
            panel.button("task-edit:"+task["id"], 588, y+7, 56, "编辑", lambda t=task:app.task_dialog(t))
            panel.toggle("task-enable:"+task["id"], 658, y+7, task["enabled"],
                         lambda enabled,t=task:app.task_toggle(t, enabled))
        def move(delta):
            panel.task_page = page + delta
            panel.draw()
        if page:
            panel.button("task-prev", 180, 552, 76, "上一页", lambda:move(-1))
        if (page+1)*page_size < len(tasks):
            panel.button("task-next", 642, 552, 76, "下一页", lambda:move(1))
        panel.text(449, 567, f"{page+1} / {max(1,(len(tasks)+page_size-1)//page_size)} · {len(tasks)} 个任务", 11, "#697589", anchor="center")
    except (OSError, ValueError, sqlite3.Error):
        panel.text(180, 220, "任务数据暂不可用，请检查数据目录后重试。", 12, "#c24c43")


def draw_sounds(panel, prefs):
    for index, (kind, title, note, field, enabled) in enumerate((
        ("agent", "Agent 完成提示音", "任务完成时播放 1 遍", "monitor_completion_sound", "monitor_sound_enabled"),
        ("task", "待办任务提示音", "每轮提醒播放 3 遍；已知晓后不再播放", "task_reminder_sound", "task_sound_enabled"),
    )):
        from ...music.play.catalog import sound_label
        y = 194 + index*156
        panel.text(180, y, title, 14, bold=True)
        panel.text(180, y+27, note, 11, "#697589")
        panel.text(180, y+58, "已选择："+sound_label(prefs[field]), 12, "#697589")
        panel.button(kind+"-sounds", 542, y+42, 112, "选择与试听", lambda k=kind:panel.app.sound_dialog(k))
        panel.toggle(kind+"-sound", 670, y-15, prefs[enabled],
                     lambda value, f=enabled:panel.app.save(**{f:value}))
