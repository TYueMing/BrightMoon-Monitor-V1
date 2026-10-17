"""Source-owned controls using the island's existing panel: activation seam."""
from copy import deepcopy
import time

from . import bridge_client
from .question_form import QuestionForm


class InteractionControls:
    def __init__(self):
        self.form = QuestionForm()
        self.pending = {}
        self.receipts = {}
        self.notices = {}
        self.displayed = None

    @staticmethod
    def key(request):
        identity = request["id"] if request["kind"] == "approval" else request.get("call_id") or request["id"]
        return (request["instance"], request["session_id"], identity)

    def update(self, ui):
        for key, future in list(self.pending.items()):
            if not future.done():
                continue
            self.pending.pop(key)
            try:
                receipt = future.result()
                self.receipts[key] = receipt
                self.notices[key] = (receipt.get("message", "已发送"), time.monotonic()+12)
            except bridge_client.BridgeError as error:
                self.notices[key] = (str(error), time.monotonic()+12)
                # An uncertain write must not be automatically repeated.
                if error.code == "DISCONNECTED":
                    self.receipts[key] = {"status": "unknown", "message": "连接中断，请在 Harness 核对结果",
                                          "command_id": getattr(future, "command_id", "")}
        for receipt in ui._run().get("deepseek_receipts", []):
            for key, old in list(self.receipts.items()):
                if old.get("command_id") == receipt.get("command_id"):
                    self.receipts[key] = receipt
                    self.notices[key] = (receipt.get("message", ""), time.monotonic()+4)

    def busy(self, request):
        key = self.key(request)
        return key in self.pending or self.receipts.get(key, {}).get("status") in {"submitted", "queued", "unknown", "confirmed"}

    def draw(self, ui, request, color):
        self.update(ui)
        self.displayed = deepcopy(request)
        w = ui._size[0]
        question = request["kind"] == "question"
        ui._text(26,150,"DeepSeek Harness 等待你的回答" if question else "DeepSeek Harness 正在等待授权",width=w-52,fill=color)
        ui._text(26,176,f"共 {len(request['questions'])} 个问题" if question else request.get("tool_name", "工具"),width=w-52,font="title")
        ui._text(26,201,("可补充回答" if request.get("mode") == "continued" else "填写后提交本组回答") if question
                 else request.get("reason") or "仅授权当前这一次操作",width=w-52,font="small",fill="#9eafc7")
        busy = self.busy(request)
        panel_y = 220+ui.canvas.offset_y
        visible = ui._size[1] >= panel_y+148
        if visible:
            body = {"questions": request["questions"]} if question else request.get("arguments", {})
            if not isinstance(body, dict):
                body = {"参数": body}
            details = ui._approval_details
            details.show({**request, "arguments": body}, x=26, y=panel_y, width=w-52, height=144, scale=ui.scale,
                         background=ui.canvas.palette.get("#25334b", "#25334b"),
                         foreground=ui.canvas.palette.get("#eef4ff", "#eef4ff"), run_id=ui._run()["run_id"])
            if question:
                self.form.mount(details, request, width=w-52, scale=ui.scale,
                                background=ui.canvas.palette.get("#25334b", "#25334b"),
                                foreground=ui.canvas.palette.get("#eef4ff", "#eef4ff"), enabled=not busy)
                if self.form.focused() and not busy:
                    bridge_client.hold(request)
        notice, until = self.notices.get(self.key(request), ("", 0))
        if until > time.monotonic():
            ui._text(26,373,notice,width=w-52,font="small",fill=color)
        for action, label, box, fill in (
            ("later" if question else "deny", "稍后回答" if question else "拒绝", (20,383,w/2-6,420), "#303d54"),
            ("answer" if question else "approve", "提交回答" if question else "同意", (w/2+6,383,w-20,420), "#3168ff"),
        ):
            ui._button(box, "panel:deepseek:"+action, label, fill, enabled=not busy)
        return visible

    def activate(self, ui, action):
        request = self.displayed
        if request is None or self.busy(request):
            return
        if action == "later":
            ui._toggle()
            return
        key = self.key(request)
        try:
            # Re-read account/source configuration at click time. A stale card
            # must not act after its source is disabled or repointed.
            snapshot = ui.snapshot_reader()
            run = next((r for r in snapshot.get("runs", []) if r.get("run_id") == ui._run().get("run_id")), {})
            current = next((i for i in run.get("deepseek_interactions", []) if i.get("id") == request["id"]
                            and i.get("revision") == request["revision"] and i.get("control_key") == request["control_key"]), None)
            if current is None:
                raise bridge_client.BridgeError("请求已更新或来源已关闭，请等待同步", "STALE_REQUEST")
            answer = self.form.answer(request) if action == "answer" else None
            self.pending[key] = bridge_client.submit(request, action, answer)
            self.notices[key] = ("正在发送…", time.monotonic()+8)
        except bridge_client.BridgeError as error:
            self.notices[key] = (str(error), time.monotonic()+8)
        ui.draw()


def controls_for(ui):
    if not hasattr(ui, "_deepseek_controls"):
        ui._deepseek_controls = InteractionControls()
    return ui._deepseek_controls
