"""Animated task header; uses the island's native drawing/hit-test primitives."""
import math
import time

from .window_state import spring_progress
from .common_model import elapsed, status_for, STATUS_TEXT
from ..src.app_sources import app_source


class TaskStrip:
    MOTION_SECONDS = .42

    def __init__(self):
        self.amount = 0.0
        self._target = 0.0
        self._start = 0.0
        self._at = 0.0
        self.focus_levels = {}
        self._last_tick = None

    def advance(self, selection, now=None):
        now = time.monotonic() if now is None else now
        target = float(selection.more and len(selection.runs) > 3)
        if target != self._target:
            self._start, self._target, self._at = self.amount, target, now
        progress = min(1.0, max(0.0, (now-self._at)/self.MOTION_SECONDS))
        self.amount = self._start + (self._target-self._start)*spring_progress(progress)
        dt = min(.12, max(0, now-self._last_tick)) if self._last_tick is not None else .05
        self._last_tick = now
        keys = {r["run_id"] for r in selection.runs}
        for key in keys:
            target = 1.0 if key == selection.focus_id else 0.0
            old = self.focus_levels.get(key, 0.0)
            self.focus_levels[key] = old+(target-old)*(1-math.exp(-dt/.12))
        self.focus_levels = {key: value for key, value in self.focus_levels.items() if key in keys}

    def draw(self, island):
        from .theme import COLORS, blend
        selection, w = island.selection, island._size[0]
        self.advance(selection)
        island._register((8, 5, w-8, 58), "toggle")
        run, status = island._run(), island._state()
        if selection.has_reminders():
            self._draw_reminders(island)
            return
        if len(selection.runs) <= 1:
            island._draw_ring(34, 31, COLORS.get(status, COLORS["offline"]), status, run=run,
                              focus=self.focus_levels.get(run.get("run_id"), 0),
                              keyboard_focus=island._focus_key == "task:"+str(run.get("run_id", "")))
            if island.expanded:
                role = island._panel().header(run, status)
                island._text(62, 30, role, width=124, font="title")
                island._text(192, 30, STATUS_TEXT.get(status, "执行中..."), width=w-274, fill=COLORS.get(status, COLORS["offline"]))
            else:
                island._text(62, 31, STATUS_TEXT.get(status, "执行中..."), width=w-140, font="title")
            island._text(w-22, 31, elapsed(run), anchor="e", font="time", fill="#a1b0c6")
            if run:
                island._register((14, 11, 54, 51), "task:"+run["run_id"])
            return
        # The first three circles keep fixed slots. Extra slots emerge while
        # the text contracts; the ellipsis remains a separate reachable button.
        p = max(0.0, min(1.04, self.amount))
        full = p > .03 or selection.more
        runs = selection.runs if full else selection.visible_runs()
        step = 42 + 9*p
        for index, task in enumerate(runs):
            if index >= 3 and p < .35:
                continue
            x = 34 + index*step
            if index >= 3:
                x = 34 + 2*step + (index-2)*step*min(1, (p-.30)/.70)
            state = status_for(island.snapshot, task)
            col = COLORS.get(state, COLORS["offline"])
            island._draw_ring(x, 31, col, state, run=task, focus=self.focus_levels.get(task["run_id"], 0),
                              keyboard_focus=island._focus_key == "task:"+task["run_id"])
            # Emerging circles cannot approve or select until they have settled
            # into non-overlapping targets.
            if index < 3 or p > .85:
                island._register((x-20, 11, x+20, 51), "task:"+task["run_id"])
        has_more = len(selection.runs) > 3
        text_x = 34+min(3, len(selection.runs))*42-14
        if has_more:
            # Keep the collapsed ellipsis independent from the third circle.
            # At rest it sits in a fourth, stable slot; when expanded it
            # slides to the right of the fourth/fifth task circles.
            collapsed_x = 160
            settled_x = 34 + len(selection.runs)*51
            x = collapsed_x + (settled_x-collapsed_x)*p
            c = island.canvas
            c.create_oval(x-15, 16, x+15, 46, fill="#1a2639", outline="#354259", width=1)
            for dx in (-5, 0, 5):
                c.create_oval(x+dx-1, 30, x+dx+1, 32, fill="#c4d1e5", outline="")
            island._register((x-19, 11, x+19, 51), "task_more")
            text_x = x + 27
        if p < .42:
            bg = island.canvas.palette.get("#141d2c", "#141d2c")
            ink = island.canvas.palette.get("#eef4ff", "#eef4ff")
            island._text(text_x, 31, selection.caption(), width=max(0, w-82-text_x), font="body", fill=blend(ink, bg, min(1, p/.42)))
        island._text(w-22, 31, elapsed(run), anchor="e", font="time", fill="#a1b0c6")

    @staticmethod
    def detail_label(run):
        app = app_source(run.get("app_id", "unknown"))
        title = run.get("task_summary") or run.get("project_id") or "任务"
        return f"{app.name} · {title}"

    def _draw_reminders(self, island):
        from .theme import COLORS
        from ..tasks.schedule import stamp_label
        selection, width = island.selection, island._size[0]
        run = island._run()
        if len(selection.runs)==1 and not island.expanded:
            state = status_for(island.snapshot,run)
            island._draw_ring(34,31,COLORS.get(state,COLORS["offline"]),state,run=run,
                              focus=self.focus_levels.get(run["run_id"],0),
                              keyboard_focus=island._focus_key=="task:"+run["run_id"])
            island._register((14,11,54,51),"task:"+run["run_id"])
            island._text(width/2,31,run.get("task_summary", "待办")[:5],font="title",anchor="center",width=width-116)
            return
        for index, task in enumerate(selection.runs):
            x = 34+index*46
            state = status_for(island.snapshot,task)
            island._draw_ring(x,31,COLORS.get(state,COLORS["offline"]),state,run=task,
                              focus=self.focus_levels.get(task["run_id"],0),
                              keyboard_focus=island._focus_key=="task:"+task["run_id"])
            island._register((x-20,11,x+20,51),"task:"+task["run_id"])
        text_x = 60+46*(len(selection.runs)-1)
        caption = run.get("task_summary", "待办")[:5] if run.get("app_id")=="solo_task" else selection.caption()
        island._text(text_x,31,caption,width=max(20,width-text_x-90),font="title")
        clock = stamp_label(run["reminder"]["due_at"])[-5:] if run.get("app_id")=="solo_task" else elapsed(run)
        island._text(width-22,31,clock,anchor="e",font="time",fill="#a1b0c6")
