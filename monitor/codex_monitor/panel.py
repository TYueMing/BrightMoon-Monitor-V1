"""Codex-specific expanded cards; capsule, task switching and animation are shared."""
import time
from weakref import WeakKeyDictionary

from ..UI.common_model import FINAL, STATUS_TEXT, authorization_actions, pending_confirmation
from ..UI.completion_panel import summary_lines
from ..UI.theme import COLORS
from ..UI.display import screen_bounds


class CodexPanel:
    def __init__(self):
        self.pages = WeakKeyDictionary()
        self.layouts = WeakKeyDictionary()

    def header(self, run, status):
        return "Codex · 待授权" if status == "waiting_confirmation" else "Codex"

    def rows(self, ui):
        return ui._run().get("tools", [])

    def summary_lines(self, ui):
        run = ui._run()
        return summary_lines(run.get("summary_preview") or run.get("error") or run.get("message"),
                             lambda s: ui._fonts["body"].measure(s)/ui.scale, width=ui.WIDTH-52)[:5]

    def _open(self, ui):
        return ui._completion_rows.get(ui._run().get("run_id"), False) if ui._state() in FINAL else ui.details

    def size(self, ui):
        _, top, _, bottom = screen_bounds(ui.root)
        limit = max(360, int((bottom-top-80)/ui.scale)-24)
        if ui._state() == "waiting_confirmation":
            height = 440
        elif self._open(ui):
            height = 580
        else:
            height = 310 if ui._state() in FINAL else 244
        return ui.WIDTH, min(limit, height)

    def _chain(self, ui, y):
        steps, w = ui._run().get("steps", []), ui._size[0]
        if not steps:
            ui._text(26,y,"当前任务未提供执行计划",font="small",fill="#9eafc7",width=w-52)
            return
        active = next((i for i, s in enumerate(steps) if s.get("status") == "running"), 0)
        start = min(max(0, active-2), max(0, len(steps)-6))
        nodes = steps[start:start+6]
        xs = [w/2] if len(nodes) == 1 else [34+i*(w-68)/(len(nodes)-1) for i in range(len(nodes))]
        for i, (x, nx) in enumerate(zip(xs, xs[1:])):
            ui.canvas.create_line(x,y,nx,y,fill=COLORS["completed"] if nodes[i]["status"] == "completed" else "#354259",width=2)
        for x, step in zip(xs, nodes):
            color = COLORS.get(step.get("status"), COLORS["pending"])
            running = step.get("status") == "running"
            radius = 8 if running else 5
            ui.canvas.create_oval(x-radius,y-radius,x+radius,y+radius,fill="#141d2c" if running else color,outline=color,width=2)
            if running:
                ui.canvas.create_oval(x-3,y-3,x+3,y+3,fill=color,outline="")
        completed = sum(s.get("status") == "completed" for s in steps)
        label = "最后计划记录" if ui._state() in FINAL else "执行计划"
        ui._text(w/2,y+25,f"{label} {completed}/{len(steps)} · 按 Codex 报告",anchor="center",font="small",fill="#9eafc7",width=w-52)

    def _entries(self, ui):
        run, result = ui._run(), []
        info = run.get("codex", {})
        if run.get("steps"):
            result.append("执行计划")
            result.extend(f"{STATUS_TEXT.get(s.get('status'), '待执行')} · {s.get('name', '')}" for s in run["steps"])
        result.append("近期工具与文件变更")
        for tool in reversed(self.rows(ui)):
            result.append(f"{tool['name']} · {STATUS_TEXT.get(tool.get('status'), '未知')}")
            if tool.get("detail"):
                result.append(tool["detail"])
            if tool.get("exit_code") is not None:
                result.append(f"退出码：{tool['exit_code']}")
        if not self.rows(ui):
            result.append("暂无已观察到的工具调用")
        usage = info.get("usage") or {}
        result.append("上下文与执行环境")
        if usage.get("input") is not None:
            result.append(f"最近输入 {usage['input']:,} tokens / 窗口 {usage.get('window') or '未报告'}")
        else:
            result.append("上下文用量：Codex 尚未报告")
        if usage.get("total") is not None:
            result.append(f"会话累计用量 {usage['total']:,} tokens（非当前上下文）")
        result.append(f"本次观察到压缩 {info.get('compactions', 0)} 次")
        result.append(f"模型：{info.get('model') or '未报告'}")
        if info.get("time_basis") == "observed":
            result.append("计时从首次观察到本轮事件开始，启动记录不在日志尾部")
        result.append(f"目录：{info.get('cwd') or '未报告'}")
        result.append(info.get("connection_message") or "日志监控 · 审批请在 Codex 中处理")
        signature = (tuple(result), ui.WIDTH, ui.scale)
        cached = self.layouts.get(ui)
        if cached and cached[0] == signature:
            return cached[1]
        # The shell redraws breathing rings every 55 ms. Measure long tool
        # records only when content or DPI changes, not on every animation frame.
        lines = [line for entry in result for line in summary_lines(entry, lambda s: ui._fonts["body"].measure(s)/ui.scale, width=ui.WIDTH-64)]
        self.layouts[ui] = (signature, lines)
        return lines

    def _details(self, ui, y):
        w, height = self.size(ui)
        lines = self._entries(ui)
        capacity = max(1, int((height-y-54)//22))
        state = self.pages.setdefault(ui, {})
        rid = ui._run().get("run_id")
        start = max(0, min(state.get(rid, 0), len(lines)-capacity))
        state[rid] = start
        for obsolete in set(state)-{r.get("run_id") for r in ui.snapshot.get("runs", [])}:
            state.pop(obsolete, None)
        ui._rounded(ui.canvas,18,y-15,w-18,height-18,12,"#1a2639")
        ui._text(28,y+2,"Codex 执行详情",font="small",fill="#9eafc7")
        for key,label,x,enabled in (("prev","上页",w-92,start>0),("next","下页",w-44,start+capacity<len(lines))):
            ui._text(x,y+2,label,anchor="center",font="small",fill="#d4e0f2" if enabled else "#8292ab")
            ui._register((x-20,y-12,x+20,y+14),f"panel:codex:{key}",enabled)
        for i, line in enumerate(lines[start:start+capacity]):
            ui._text(30,y+30+22*i,line,width=w-60)

    def activate(self, ui, key):
        if key in {"panel:codex:prev", "panel:codex:next"}:
            self.scroll(ui, -4 if key.endswith("prev") else 4)

    def scroll(self, ui, direction):
        if not self._open(ui) or ui._state() == "waiting_confirmation":
            return False
        state = self.pages.setdefault(ui, {})
        rid = ui._run().get("run_id")
        state[rid] = max(0, state.get(rid, 0)+direction)
        ui.draw()
        return True

    def _approval(self, ui, color):
        run, w = ui._run(), ui._size[0]
        pending = pending_confirmation(run)
        height = self.size(ui)[1]
        ui._text(26,87,pending.get("tool_name") or "Codex 授权请求",font="title",width=w-52,fill=color)
        ui._text(26,115,pending.get("reason") or "请核对本次操作",font="small",width=w-52,fill="#c4d1e5")
        actions = authorization_actions(ui.snapshot, run)
        if not pending.get("controllable"):
            label = "此请求请在 Codex 原界面处理"
        elif run.get("connection_error"):
            label = "连接已断开；等待重新同步请求"
        elif not actions:
            label = "决定已发送，等待 Codex 处理"
        else:
            label = "仅授权本次操作，不更改权限策略"
        if ui._notice_run_id == run.get("run_id") and ui._notice and ui._notice_until > time.monotonic():
            label = ui._notice
        ui._text(26,height-80,label,font="small",width=w-52,fill="#dba955")
        for action_type, label, x1, x2, fill in (
            ("deny_tool", "拒绝", 20, w/2-6, "#303d54"),
            ("approve_tool", "同意一次", w/2+6, w-20, "#3168ff")):
            action = actions.get(action_type)
            enabled = bool(action and ui.command_sender and not ui._pending and not run.get("connection_error"))
            label = action.get("label", label) if action else label
            ui._button((x1,height-58,x2,height-20),action["id"] if action else action_type,label,fill,action,enabled)
        y = 139+ui.canvas.offset_y
        details_height = height-240
        if ui._size[1] >= y+details_height+6:
            ui._approval_details.show({**pending,"arguments":pending.get("arguments") or {"说明":"请在 Codex 核对完整操作"}},
                x=26,y=y,width=w-52,height=details_height,scale=ui.scale,
                background=ui.canvas.palette.get("#25334b","#25334b"),
                foreground=ui.canvas.palette.get("#eef4ff","#eef4ff"),run_id=run.get("run_id",""))
            return True
        return False

    def draw(self, ui, color):
        run, w = ui._run(), ui._size[0]
        if ui._state() == "waiting_confirmation":
            return self._approval(ui, color)
        info = run.get("codex", {})
        ui._text(26,85,"实时连接 · Codex" if info.get("mode") == "live" else "日志监控 · Codex",font="small",fill="#9eafc7")
        y = 110
        if ui._state() in FINAL:
            for line in self.summary_lines(ui):
                ui._text(26,y,line,width=w-52)
                y += 21
            y += 10
        else:
            ui._text(26,y,run.get("message") or "等待 Codex 事件",width=w-52)
            y += 38
        self._chain(ui,y)
        y += 57
        key = "completion_rows" if ui._state() in FINAL else "details"
        ui._text(26,y,"执行详情",font="small")
        ui._text(w-26,y,"收起 ⌃" if self._open(ui) else "展开 ⌄",font="small",anchor="e")
        ui._register((18,y-16,w-18,y+16),key)
        if self._open(ui):
            self._details(ui,y+41)
        return False
