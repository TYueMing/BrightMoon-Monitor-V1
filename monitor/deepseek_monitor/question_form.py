"""Stable native fields embedded in the existing scrollable approval Text widget."""
import tkinter as tk
from ..UI.focus import focus_path


class QuestionForm:
    def __init__(self):
        self.drafts = {}
        self.key = None
        self.fields = []
        self.frames = []
        self.appearance = None

    @staticmethod
    def draft_key(request):
        # Keeps a draft when a live timed request becomes a continued question.
        return (request["control_key"], request["session_id"], request.get("call_id") or request["id"])

    def mount(self, details, request, *, width, scale, background, foreground, enabled=True):
        text = details.text
        key = (details.key, tuple((q["id"], q["question"]) for q in request["questions"]))
        if self.key != key or not self.frames or str(self.frames[0]) not in text.window_names():
            # The shared details.show() clears embedded windows when switching
            # providers. Never replace/hook that shared component or UI methods.
            for frame in self.frames:
                frame.destroy()
            self.frames, self.fields = [], []
            text.configure(state="normal")
            text.delete("1.0", "end")
            draft = self.drafts.setdefault(self.draft_key(request), {})
            for index, question in enumerate(request["questions"]):
                saved = draft.setdefault(question["id"], {"selected": [], "custom": ""})
                frame = tk.Frame(text, borderwidth=0)
                self.frames.append(frame)
                label = tk.Label(frame, text=f"{index+1}. {question['question']}", anchor="w", justify="left")
                label.pack(fill="x", pady=(2, 5))
                if question.get("detail"):
                    tk.Label(frame, text=question["detail"], anchor="w", justify="left").pack(fill="x", pady=(0, 5))
                options = question.get("options") or []
                variables = []
                if question.get("multiSelect"):
                    for option in options:
                        variable = tk.BooleanVar(master=text, value=option["label"] in saved["selected"])
                        variables.append((option["label"], variable))
                        widget = tk.Checkbutton(frame, text=self._label(option), variable=variable, anchor="w", justify="left",
                                                borderwidth=0, highlightthickness=0)
                        widget.pack(fill="x")
                    def changed(*_, entries=variables, answer=saved):
                        answer["selected"] = [label for label, variable in entries if variable.get()]
                    for _, variable in variables:
                        variable.trace_add("write", changed)
                else:
                    variable = tk.StringVar(master=text, value=next(iter(saved["selected"]), ""))
                    variables.append(("", variable))
                    for option in options:
                        tk.Radiobutton(frame, text=self._label(option), variable=variable, value=option["label"], anchor="w", justify="left",
                                       borderwidth=0, highlightthickness=0).pack(fill="x")
                    def changed(*_, variable=variable, answer=saved):
                        answer["selected"] = [variable.get()] if variable.get() else []
                    variable.trace_add("write", changed)
                tk.Label(frame, text="补充回答" if options else "你的回答", anchor="w").pack(fill="x", pady=(5, 2))
                entry = tk.Text(frame, height=2, wrap="word", borderwidth=1, relief="solid", highlightthickness=0, undo=True)
                entry.insert("1.0", saved["custom"])
                entry.edit_modified(False)
                def edited(_=None, entry=entry, answer=saved):
                    if entry.edit_modified():
                        answer["custom"] = entry.get("1.0", "end-1c")
                        entry.edit_modified(False)
                entry.bind("<<Modified>>", edited)
                entry.pack(fill="x", pady=(0, 12))
                self.fields.append((question, saved, entry, variables))
                text.window_create("end", window=frame)
                text.insert("end", "\n")
            text.configure(state="disabled")
            text.yview_moveto(0)
            self.key, self.appearance = key, None
        appearance = (round(width*scale)-34, round(12*scale), background, foreground, enabled)
        if appearance != self.appearance:
            for frame in self.frames:
                frame.configure(bg=background, width=appearance[0])
                for widget in frame.winfo_children():
                    widget.configure(bg=background, fg=foreground, font=("Microsoft YaHei UI", -appearance[1]))
                    if isinstance(widget, (tk.Label, tk.Checkbutton, tk.Radiobutton)):
                        widget.configure(wraplength=max(100, appearance[0]-28))
                    if isinstance(widget, (tk.Checkbutton, tk.Radiobutton)):
                        widget.configure(activebackground=background, activeforeground=foreground, selectcolor=background,
                                         state="normal" if enabled else "disabled")
                    if isinstance(widget, tk.Text):
                        widget.configure(width=max(10, int(appearance[0]/(appearance[1]*.65))), insertbackground=foreground,
                                         state="normal" if enabled else "disabled")
            self.appearance = appearance

    @staticmethod
    def _label(option):
        return option["label"] + (" — " + option["description"] if option.get("description") else "")

    def focused(self):
        if not self.frames:
            return False
        focus = focus_path(self.frames[0])
        return bool(focus and any(focus.startswith(str(frame) + ".") for frame in self.frames))

    def answer(self, request):
        from .bridge_client import BridgeError
        draft = self.drafts.get(self.draft_key(request), {})
        # Read the widget too: a click can precede its queued Modified event.
        for question, saved, entry, _ in self.fields:
            if entry.winfo_exists() and draft.get(question["id"]) is saved:
                saved["custom"] = entry.get("1.0", "end-1c")
        answers = []
        for question in request["questions"]:
            value = draft.get(question["id"], {})
            selected, custom = list(value.get("selected", [])), str(value.get("custom", ""))
            if not selected and not custom.strip():
                raise BridgeError("请回答本组全部问题后提交", "BAD_ANSWER")
            answers.append({"id": question["id"], "selected": selected, **({"custom": custom} if custom.strip() else {})})
        return {"answers": answers}
