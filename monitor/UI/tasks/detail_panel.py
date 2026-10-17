"""Reminder-only second/third levels; no Agent approval or tool semantics."""
from ..completion_panel import summary_lines
import sqlite3
from ...tasks.schedule import repeat_label, stamp_label


class ReminderPanel:
    def header(self, run, status):
        return run.get("task_summary", "待办")

    def rows(self, ui):
        return []

    def summary_lines(self, ui):
        body = ui._run().get("message") or "未添加内容，可在任务页编辑。"
        lines = summary_lines(body, lambda s:ui._fonts["body"].measure(s)/ui.scale, width=ui.WIDTH-56)
        result = lines[:3]
        if result and (len(lines)>3 or len(body)>200):
            result[-1] = result[-1].rstrip("…")+"…"
        return result

    def size(self, ui):
        return ui.WIDTH, 412 if ui.details else 282

    def draw(self, ui, color):
        run = ui._run()
        reminder = run["reminder"]
        task = reminder["task"]
        width = ui._size[0]
        icon = ui._icons.get("solo_task", round(20*ui.scale))
        if icon:
            ui.canvas.create_image(36,86,image=icon,tags=("reminder_detail_logo",))
        ui._text(54,86,task["title"],width=width-80,font="title")
        ui._text(26,113,"预定 "+stamp_label(reminder["due_at"]),width=width-52,font="small",fill="#9eafc7")
        for index,line in enumerate(self.summary_lines(ui)[:3]):
            ui._text(26,143+index*22,line,width=width-52)
        ui._button((26,210,width/2-7,247),"panel:reminder-snooze","忽略 · 5分钟后")
        ui._button((width/2+7,210,width-26,247),"panel:reminder-acknowledge","已知晓",fill="#28364e")
        if ui.details:
            for index,text in enumerate(("重复："+repeat_label(task),"首次日期："+task["start_date"],
                f'提前提醒：{task["lead_minutes"]} 分钟 · 本轮收起：{stamp_label(reminder["expires_at"])}',
                "下次计划："+stamp_label(reminder["next_due"]))):
                ui._text(26,275+index*25,text,width=width-52,font="small",fill="#9eafc7")
        if ui._notice_run_id == run["run_id"] and ui._notice:
            ui._text(26,self.size(ui)[1]-13,ui._notice,width=width-52,font="small",fill=color)
        return False

    def activate(self, ui, key):
        action = {"panel:reminder-snooze":"snooze","panel:reminder-acknowledge":"acknowledge"}.get(key)
        if action and ui.command_sender:
            run = ui._run()
            try:
                ui.command_sender("local_reminder",run["run_id"],{"cycle":run["reminder"]["cycle"],"action":action})
                ui._notice = ""
                ui._poll()
            except (OSError,ValueError,sqlite3.Error):
                ui._notice_run_id = run["run_id"]
                ui._notice = "处理失败或提醒已更新，请重试"
                ui.draw()
