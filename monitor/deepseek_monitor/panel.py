"""Harness detail cards with source-local approval and question controls."""
from ..UI.common_model import FINAL, STATUS_TEXT, pending_confirmation
from ..UI.completion_panel import summary_lines
from ..UI.theme import COLORS
from .controls import controls_for


class DeepSeekPanel:
    def header(self, run, status):
        return str(run.get("agent") or "DeepSeek Harness")

    def rows(self, ui):
        return ui._run().get("tools", [])[-5:]

    def summary_lines(self, ui):
        run = ui._run()
        return summary_lines(run.get("summary_preview") or run.get("error") or run.get("message"),
                             lambda value: ui._fonts["body"].measure(value)/ui.scale)

    def size(self, ui):
        status = ui._state()
        if status == "waiting_confirmation" or ui._run().get("deepseek_interactions"):
            return ui.WIDTH, 434
        if status in FINAL:
            opened = ui._completion_rows.get(ui._run().get("run_id"), False)
            return ui.WIDTH, 240+21*len(self.summary_lines(ui))+(36*max(1,len(self.rows(ui))) if opened else 0)
        return ui.WIDTH, 212+(38+36*max(1,len(self.rows(ui))) if ui.details else 0)

    def _chain(self, ui, y):
        steps = ui._run().get("steps", [])[-6:]
        w, c = ui._size[0], ui.canvas
        if not steps:
            ui._text(w/2, y, "等待 Harness 的模型轮次事件", anchor="center", font="small", fill="#9eafc7")
            return
        xs = [w/2] if len(steps) == 1 else [34+i*(w-68)/(len(steps)-1) for i in range(len(steps))]
        for x, nx in zip(xs, xs[1:]):
            c.create_line(x,y,nx,y,fill="#354259",width=2)
        for x, step in zip(xs, steps):
            color = COLORS.get(step.get("status"), COLORS["pending"])
            active = step.get("status") == "running"
            radius = 8 if active else 5
            c.create_oval(x-radius,y-radius,x+radius,y+radius,fill=color if not active else "#141d2c",outline=color,width=2)
            if active:
                c.create_oval(x-3,y-3,x+3,y+3,fill=color,outline="")
        detail = ui._run().get("monitor_detail", {})
        ui._text(w/2,y+26,f"第 {detail.get('turn', 0)} 次任务 · 已观察 {detail.get('step', 0)} 轮模型调用",
                 anchor="center",font="small",fill="#9eafc7")

    def _tools(self, ui, y):
        w = ui._size[0]
        rows = self.rows(ui)
        ui._text(26,y,"近期工具调用（最多 5 条）",font="small",fill="#9eafc7")
        for index, item in enumerate(rows):
            yy = y+30+36*index
            color = COLORS.get(item.get("status"), COLORS["pending"])
            ui.canvas.create_oval(27,yy-4,35,yy+4,fill=color,outline="")
            ui._text(46,yy,item.get("name","工具"),width=w-155)
            ui._text(w-27,yy,STATUS_TEXT.get(item.get("status"),"等待数据"),anchor="e",font="small",fill=color)
        if not rows:
            ui._text(26,y+28,"当前任务尚未观察到工具调用",font="small",fill="#9eafc7")

    def draw(self, ui, color):
        run, status, w = ui._run(), ui._state(), ui._size[0]
        controls = controls_for(ui)
        controls.update(ui)
        if run.get("deepseek_interactions"):
            self._chain(ui,94)
            return controls.draw(ui, run["deepseek_interactions"][0], color)
        if status == "waiting_confirmation":
            self._chain(ui,94)
            pending = pending_confirmation(run)
            ui._text(26,150,"DeepSeek Harness 正在等待授权",width=w-52,fill=color)
            ui._text(26,176,pending.get("tool_name","工具"),width=w-52,font="title")
            ui._text(26,201,pending.get("reason") or "等待用户确认此操作",width=w-52,font="small",fill="#9eafc7")
            ui._text(26,383,"请在 DeepSeek Harness 应用中同意或拒绝。",width=w-52,font="small",fill=color)
            ui._text(26,407,"当前通过会话日志监控，不代替原应用授权。",width=w-52,font="small",fill="#9eafc7")
            panel_y = 220+ui.canvas.offset_y
            if ui._size[1] >= panel_y+148:
                args = pending.get("arguments", {})
                args = args if isinstance(args, dict) else {"参数": args}
                ui._approval_details.show({**pending,"arguments":args},x=26,y=panel_y,width=w-52,height=144,
                    scale=ui.scale,background=ui.canvas.palette.get("#25334b","#25334b"),
                    foreground=ui.canvas.palette.get("#eef4ff","#eef4ff"),run_id=run["run_id"])
                return True
            return False
        if status in FINAL:
            ui._text(26,85,"任务摘要",font="small",fill="#9eafc7")
            lines = self.summary_lines(ui)
            for index, line in enumerate(lines):
                ui._text(26,108+21*index,line,width=w-52)
            y = 130+21*len(lines)
            self._chain(ui,y)
            opened = ui._completion_rows.get(run.get("run_id"), False)
            ui._text(26,y+64,"工具记录",font="small")
            ui._text(w-26,y+64,"收起 ⌃" if opened else "展开 ⌄",anchor="e",font="small")
            ui._register((18,y+48,w-18,y+82),"completion_rows")
            if opened:
                # Row title already appears in the accordion header.
                self._tools(ui,y+92)
        else:
            self._chain(ui,94)
            ui._text(26,152,run.get("message") or "等待已落盘事件",width=w-52)
            ui._text(26,179,"会话事件监控 · 轮次数不代表任务总进度",width=w-52,font="small",fill="#9eafc7")
            ui._register((12,66,w-12,204),"details")
            if ui.details:
                self._tools(ui,216)
        return False

    def activate(self, ui, key):
        if key.startswith("panel:deepseek:"):
            controls_for(ui).activate(ui, key.removeprefix("panel:deepseek:"))
