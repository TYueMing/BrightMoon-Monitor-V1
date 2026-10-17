"""OpenCode-specific renderer for the host's source-neutral DynamicIsland."""

from __future__ import annotations

import time
from weakref import WeakKeyDictionary

from ..UI.common_model import FINAL, STATUS_TEXT, elapsed, pending_confirmation
from ..UI.completion_panel import summary_lines
from ..UI.theme import ACTIVE, COLORS


def _number(value: object) -> int:
    try:
        return max(0, int(float(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _compact(value: object) -> str:
    number = _number(value)
    return (f"{number / 1000:.1f}k".replace(".0k", "k") if number >= 1000 else str(number))


def metric_labels(run: dict) -> list[str]:
    result: list[str] = []
    compaction = run.get("compaction") if isinstance(run.get("compaction"), dict) else {}
    if compaction.get("active") or compaction.get("status") == "running":
        result.append("正在压缩上下文")
    context = run.get("context") if isinstance(run.get("context"), dict) else {}
    percent = context.get("percent")
    if isinstance(percent, (int, float)):
        result.append(f"上下文 {percent:g}%")
    elif _number(context.get("used_tokens")):
        result.append(f"上下文 {_compact(context.get('used_tokens'))}")
    changes = run.get("changes") if isinstance(run.get("changes"), dict) else {}
    files = _number(changes.get("files"))
    if files:
        result.append(
            f"改动 {files} · +{_number(changes.get('additions'))} -{_number(changes.get('deletions'))}"
        )
    children = run.get("child_summary") if isinstance(run.get("child_summary"), dict) else {}
    total = _number(children.get("total"))
    if total:
        result.append(f"子任务 {_number(children.get('completed'))}/{total}")
    return result


def chain_model(run: dict, limit: int = 6) -> dict:
    """Build an honest visual chain from todos, then observed OpenCode stages."""
    tasks = [item for item in run.get("tasks", []) if isinstance(item, dict)]
    overall = str(run.get("status") or "pending")

    def visible_state(value: object) -> str:
        state = str(value or "pending")
        if state in ACTIVE and overall in {"waiting_confirmation", "failed", "cancelled"}:
            return overall
        if overall in FINAL and state in ACTIVE:
            return "completed" if overall == "completed" else overall
        return state if state in COLORS else "pending"

    if tasks:
        current = next(
            (index for index, task in enumerate(tasks)
             if str(task.get("id")) == str(run.get("task_id")) and run.get("task_id") not in (None, "")),
            None,
        )
        if current is None:
            current = next(
                (index for index, task in enumerate(tasks)
                 if str(task.get("status") or "pending") not in FINAL),
                len(tasks) - 1,
            )
        start = min(max(0, current - 2), max(0, len(tasks) - limit))
        selected = tasks[start:start + limit]
        completed = sum(str(task.get("status")) == "completed" for task in tasks)
        label = f"{completed}/{len(tasks)} 已完成"
        if len(tasks) > limit:
            label += f" · 步骤 {start + 1}-{start + len(selected)}"
        return {
            "nodes": [{"status": visible_state(task.get("status"))} for task in selected],
            "label": label,
            "kind": "tasks",
        }

    stages = [item for item in run.get("stages", []) if isinstance(item, dict)]
    if stages:
        selected = stages[-limit:]
        label = f"执行轨迹 · {len(stages)} 个已观察阶段"
        if len(stages) > limit:
            label += f" · 最近 {limit} 个"
        return {
            "nodes": [{"status": visible_state(stage.get("status"))} for stage in selected],
            "label": label,
            "kind": "stages",
        }

    state = visible_state(overall)
    labels = {
        "completed": "直接回答 · 已完成",
        "failed": "直接回答 · 执行失败",
        "cancelled": "直接回答 · 已停止",
        "waiting_confirmation": "等待你的决定",
    }
    return {
        "nodes": [{"status": state}],
        "label": labels.get(overall, "处理中 · 等待 OpenCode 生成任务链"),
        "kind": "state",
    }


class OpenCodePanel:
    app_id = "opencode"
    PROGRESS_HEIGHT = 242
    AUTH_HEIGHT = 388

    def __init__(self) -> None:
        self._questions = WeakKeyDictionary()

    def header(self, run, status):
        pending = pending_confirmation(run)
        if pending.get("kind") == "question":
            return "OpenCode 决策"
        if status == "waiting_confirmation":
            return "OpenCode 授权"
        if run.get("phase") == "compress":
            return "OpenCode 压缩"
        return str(run.get("agent") or "OpenCode")

    def rows(self, ui):
        result = []
        for item in ui._run().get("stages", []):
            if isinstance(item, dict) and item.get("agent"):
                result.append(str(item["agent"]))
        return result[-7:]

    def summary_lines(self, ui):
        run = ui._run()
        value = run.get("summary_preview") or run.get("error") or run.get("message") or "OpenCode 未提供摘要"
        return summary_lines(value, lambda text: ui._fonts["body"].measure(text) / ui.scale)

    def size(self, ui):
        if not ui.expanded:
            return ui.MINI_WIDTH, ui.MINI_HEIGHT
        pending = pending_confirmation(ui._run())
        if pending.get("kind") == "question":
            questions = pending.get("questions") if isinstance(pending.get("questions"), list) else []
            page = self._question_state(ui, pending)[0]
            question = questions[page] if questions else {}
            options = question.get("options", []) if isinstance(question.get("options"), list) else []
            controls = min(5, len(options))
            controls += int(len(options) > 5)
            controls += int(question.get("custom", True))
            controls += int(question.get("multiple", False))
            return ui.WIDTH, max(410, 332 + controls * 42)
        if ui._state() == "waiting_confirmation":
            return ui.WIDTH, self.AUTH_HEIGHT
        if ui._state() in FINAL:
            opened = ui._completion_rows.get(ui._run().get("run_id"), False)
            metric_offset = 16 if len(metric_labels(ui._run())) > 2 else 0
            return ui.WIDTH, 300 + metric_offset + (34 * max(1, len(self.rows(ui))) if opened else 0)
        metric_offset = 16 if len(metric_labels(ui._run())) > 2 else 0
        return ui.WIDTH, 410 + metric_offset if ui.details else self.PROGRESS_HEIGHT

    def draw(self, ui, color):
        pending = pending_confirmation(ui._run())
        if pending.get("kind") == "question":
            self._draw_question(ui, pending, color)
            return False
        if ui._state() == "waiting_confirmation":
            return self._draw_authorization(ui, pending, color)
        metric_offset = 16 if len(metric_labels(ui._run())) > 2 else 0
        self._draw_chain(ui, color, 105 + metric_offset)
        if ui._state() in FINAL:
            self._draw_finished(ui, color, metric_offset)
        else:
            self._draw_progress(ui, color, metric_offset)
        return False

    def _draw_metrics(self, ui, y):
        labels = metric_labels(ui._run())
        if not labels:
            return
        context = ui._run().get("context") if isinstance(ui._run().get("context"), dict) else {}
        percent = context.get("percent")
        color = ("#ff6c7c" if isinstance(percent, (int, float)) and percent >= 95 else
                 "#ffbf63" if isinstance(percent, (int, float)) and percent >= 80 else "#9eafc7")
        rows = [labels] if len(labels) <= 2 else [labels[:2], labels[2:]]
        for index, row in enumerate(rows):
            ui._text(ui._size[0] / 2, y + index * 17, " · ".join(row),
                     width=ui._size[0]-42, anchor="center", font="small", fill=color)

    def _draw_chain(self, ui, color, y):
        chain = chain_model(ui._run())
        nodes = chain["nodes"]
        w, canvas = ui._size[0], ui.canvas
        xs = [w/2] if len(nodes) == 1 else [34+i*(w-68)/(len(nodes)-1) for i in range(len(nodes))]
        for index, (x, nx) in enumerate(zip(xs, xs[1:])):
            canvas.create_line(
                x, y, nx, y,
                fill="#3ed6a1" if nodes[index]["status"] == "completed" else "#354259",
                width=2,
            )
        for x, node in zip(xs, nodes):
            state = node["status"]
            item_color = COLORS.get(state, COLORS["pending"])
            radius = 8 if state in ACTIVE else 5
            canvas.create_oval(x-radius, y-radius, x+radius, y+radius,
                               fill=item_color if state == "completed" else "#141d2c",
                               outline=item_color, width=2)
            if state in ACTIVE:
                canvas.create_oval(x-3, y-3, x+3, y+3, fill=item_color, outline="")
        ui._text(w/2, y+27, chain["label"],
                 anchor="center", font="small", fill="#9eafc7")

    def _draw_progress(self, ui, color, offset=0):
        run, w = ui._run(), ui._size[0]
        self._draw_metrics(ui, 70 if offset else 76)
        message = ("正在压缩上下文，保存当前执行进度" if run.get("phase") == "compress"
                   else run.get("message") or run.get("task_summary") or "OpenCode 正在处理")
        ui._text(26,151+offset, message, width=w-52, fill="#d4e0f2")
        ui._text(26,176+offset, run.get("task_summary") or run.get("project_id") or "OpenCode",
                 width=w-115, font="small", fill="#9eafc7")
        ui._text(w-26,176+offset,elapsed(run),anchor="e",font="time",fill="#a1b0c6")
        ui._text(26,ui._target_size()[1]-24,"收起详情 ⌃" if ui.details else "展开详情 ⌄",
                 font="small",fill="#9eafc7")
        ui._register((18,190+offset,w-18,ui._target_size()[1]-8),"details")
        if ui.details:
            self._draw_detail_rows(ui, 216+offset)
        cancel = next((item for item in ui.snapshot.get("actions", [])
                       if item.get("type") == "cancel_run" and item.get("run_id") == run.get("run_id")), None)
        if cancel and ui.details:
            ui._text(w-26,ui._target_size()[1]-24,"请求停止",anchor="e",font="small",fill="#d5aa84")
            ui._register((w-100,ui._target_size()[1]-38,w-18,ui._target_size()[1]-10),cancel["id"],True,cancel)

    def _draw_detail_rows(self, ui, y):
        w = ui._size[0]
        stages = {str(item.get("agent")): str(item.get("status") or "pending")
                  for item in ui._run().get("stages", []) if isinstance(item, dict)}
        ui._rounded(ui.canvas,18,y-14,w-18,ui._target_size()[1]-48,14,"#1a2639")
        for index, role in enumerate(self.rows(ui)[:4]):
            yy = y+12+34*index
            state = stages.get(role,"pending")
            color = COLORS.get(state,COLORS["pending"])
            ui.canvas.create_oval(28,yy-4,36,yy+4,fill=color,outline="")
            ui._text(48,yy,role,width=w-150)
            ui._text(w-30,yy,STATUS_TEXT.get(state,state),anchor="e",font="small",fill=color)

    def _draw_finished(self, ui, color, offset=0):
        w = ui._size[0]
        self._draw_metrics(ui, 70 if offset else 76)
        lines = self.summary_lines(ui)[:4]
        ui._text(26,151+offset,"任务摘要",font="small",fill="#9eafc7")
        for index, line in enumerate(lines):
            ui._text(26,174+offset+21*index,line,width=w-52,fill="#d4e0f2")
        y = 184 + offset + 21*len(lines)
        opened = ui._completion_rows.get(ui._run().get("run_id"),False)
        ui._text(26,y,"执行步骤",font="small")
        ui._text(w-26,y,"收起 ⌃" if opened else "展开 ⌄",anchor="e",font="small",fill="#9eafc7")
        ui._register((18,y-16,w-18,y+18),"completion_rows")
        if opened:
            self._draw_detail_rows(ui,y+36)

    def _draw_authorization(self, ui, pending, color):
        w = ui._size[0]
        self._draw_chain(ui,color,94)
        ui._text(26,140,"OpenCode 需要授权执行以下操作：",font="body")
        ui._rounded(ui.canvas,20,166,w-20,286,17,"#25334b")
        ui._text(36,190,pending.get("tool_name") or "待确认工具",width=w-72,font="title")
        ui._text(36,216,pending.get("reason") or "仅授权当前一次操作",width=w-72,font="small",fill="#b4c3d9")
        arguments = pending.get("arguments") if isinstance(pending.get("arguments"),dict) else {}
        preview = " · ".join(f"{key}: {value}" for key,value in list(arguments.items())[:2])
        ui._text(36,247,preview or "操作参数未提供",width=w-72,font="small",fill="#c4d1e5")
        actions = {item.get("type"):item for item in ui.snapshot.get("actions", [])
                   if item.get("run_id") == ui._run().get("run_id")
                   and item.get("confirmation_id") == pending.get("id")}
        for key,label,box,fill in (
            ("deny_tool","拒绝",(20,323,w/2-6,360),"#303d54"),
            ("approve_tool","同意一次",(w/2+6,323,w-20,360),"#3168ff"),
        ):
            action = actions.get(key)
            ui._button(box,action.get("id",key) if action else key,label,fill,action,bool(action and ui.command_sender))
        return False

    def _question_state(self, ui, pending):
        state = self._questions.setdefault(ui,{})
        request_id = str(pending.get("id") or "")
        questions = pending.get("questions") if isinstance(pending.get("questions"),list) else []
        record = state.setdefault(request_id,{
            "page":0,"answers":[[] for _ in questions],"offsets":[0 for _ in questions]
        })
        if len(record["answers"]) != len(questions):
            record = state[request_id] = {
                "page":0,"answers":[[] for _ in questions],"offsets":[0 for _ in questions]
            }
        record.setdefault("offsets", [0 for _ in questions])
        if len(record["offsets"]) != len(questions):
            record["offsets"] = [0 for _ in questions]
        record["page"] = min(max(0,record["page"]),max(0,len(questions)-1))
        return record["page"],record["answers"],questions

    def _draw_question(self, ui, pending, color):
        page,answers,questions = self._question_state(ui,pending)
        question = questions[page] if questions else {}
        w = ui._size[0]
        self._draw_chain(ui, color, 82)
        ui._text(26,140,question.get("header") or "OpenCode 需要你的决定",font="small",fill=color)
        if len(questions)>1:
            ui._text(w-26,140,f"{page+1}/{len(questions)}",anchor="e",font="small",fill="#9eafc7")
        lines = summary_lines(question.get("question") or "请选择下一步操作",
                              lambda text:ui._fonts["body"].measure(text)/ui.scale,w-52)
        y = 168
        for line in lines[:3]:
            ui._text(26,y,line,width=w-52);y+=21
        y += 8
        selected = answers[page] if answers and page<len(answers) else []
        options = question.get("options",[]) if isinstance(question.get("options"),list) else []
        record = self._questions[ui][str(pending.get("id") or "")]
        offset = min(max(0, record["offsets"][page]), max(0, len(options)-5))
        record["offsets"][page] = offset
        for index,option in enumerate(options[offset:offset+5], start=offset):
            label = str(option.get("label") or "选项")
            ui._button((20,y,w-20,y+34),f"panel:ocq:option:{index}",
                       ("✓  " if label in selected else "")+label,
                       "#3168ff" if label in selected else "#25334b",enabled=True)
            y += 42
        if len(options)>5:
            ui._button((20,y,w/2-6,y+34),"panel:ocq:options_prev","上一组","#25334b",enabled=offset>0)
            ui._button((w/2+6,y,w-20,y+34),"panel:ocq:options_next","下一组","#25334b",
                       enabled=offset+5<len(options));y+=42
        if question.get("custom",True):
            ui._button((20,y,w-20,y+34),"panel:ocq:custom","自定义回答...","#25334b",enabled=True);y+=42
        if question.get("multiple"):
            ui._button((20,y,w-20,y+34),"panel:ocq:confirm","确认所选项目","#3168ff",enabled=bool(selected));y+=42
        footer = ui._target_size()[1]-43
        if page>0:
            ui._button((20,footer,w/2-6,footer+34),"panel:ocq:back","上一题","#25334b",enabled=True)
            ui._button((w/2+6,footer,w-20,footer+34),"panel:ocq:reject","拒绝回答","#4a2b3a",enabled=True)
        else:
            ui._button((20,footer,w-20,footer+34),"panel:ocq:reject","拒绝回答","#4a2b3a",enabled=True)

    def activate(self, ui, key):
        if not key.startswith("panel:ocq:"):
            return
        pending = pending_confirmation(ui._run())
        page,answers,questions = self._question_state(ui,pending)
        if not questions:
            return
        question = questions[page]
        record = self._questions[ui][str(pending.get("id") or "")]
        if key == "panel:ocq:options_prev":
            record["offsets"][page] = max(0, record["offsets"][page]-5)
            ui.draw();return
        if key == "panel:ocq:options_next":
            record["offsets"][page] = min(
                max(0,len(question.get("options",[]))-5), record["offsets"][page]+5
            )
            ui.draw();return
        if key.startswith("panel:ocq:option:"):
            index = int(key.rsplit(":",1)[1])
            options = question.get("options",[])
            if index >= len(options):
                return
            label = str(options[index].get("label") or "")
            if question.get("multiple"):
                answers[page] = [item for item in answers[page] if item != label]
                if label not in answers[page]:
                    answers[page].append(label)
                ui.draw();return
            answers[page] = [label]
            self._advance_or_send(ui,pending,page,answers,questions)
        elif key == "panel:ocq:confirm" and answers[page]:
            self._advance_or_send(ui,pending,page,answers,questions)
        elif key == "panel:ocq:custom":
            from tkinter import simpledialog
            answer = simpledialog.askstring(
                str(question.get("header") or "OpenCode 决策"),
                str(question.get("question") or "请输入你的回答"),
                parent=ui.root,
            )
            if answer is not None and answer.strip():
                answers[page] = [answer.strip()]
                self._advance_or_send(ui,pending,page,answers,questions)
        elif key == "panel:ocq:back" and page>0:
            record["page"] = page-1
            ui._resize();ui.draw()
        elif key == "panel:ocq:reject":
            self._send(ui,"reject_question",pending,{
                "request_id":pending.get("id"),"confirmation_id":pending.get("id")})

    def _advance_or_send(self,ui,pending,page,answers,questions):
        if page+1<len(questions):
            self._questions[ui][str(pending.get("id"))]["page"] = page+1
            ui._resize();ui.draw();return
        self._send(ui,"answer_question",pending,{"request_id":pending.get("id"),
                   "confirmation_id":pending.get("id"),"answers":answers})

    @staticmethod
    def _send(ui,action,pending,payload):
        if not ui.command_sender or ui._pending:
            return
        payload["expected_revision"] = pending.get("revision")
        try:
            command_id = ui.command_sender(action,str(ui._run().get("run_id")),payload)
            ui._pending = (ui._run().get("run_id"),pending.get("id"),time.monotonic())
            ui._pending_command_id = command_id if isinstance(command_id,str) else ""
            ui._notice_run_id = str(ui._run().get("run_id"))
            ui._notice = "已发送，等待 OpenCode 确认..."
            ui._notice_until = time.monotonic()+8
        except Exception:
            ui._notice = "发送失败，请在 OpenCode 中处理"
            ui._notice_until = time.monotonic()+8
        ui.draw()
