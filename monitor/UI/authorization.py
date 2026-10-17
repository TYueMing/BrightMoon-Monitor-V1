"""Full, selectable approval arguments inside the island, without truncation."""
import json
import tkinter as tk
from copy import deepcopy


class ApprovalDetails:
    def __init__(self, parent):
        self.frame = tk.Frame(parent, borderwidth=0)
        self.text = tk.Text(self.frame, wrap="word", borderwidth=0, highlightthickness=0,
                            padx=8, pady=6, takefocus=True, state="disabled")
        scroll = tk.Scrollbar(self.frame, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.key = None
        self._arguments = None
        self._appearance = None
        self._geometry = None
        self._visible = False

    def hide(self):
        if self._visible:
            self.frame.place_forget()
            self._visible = False

    def show(self, approval, *, x, y, width, height, scale, background, foreground, run_id=""):
        key = (run_id, approval.get("id"), approval.get("revision"))
        arguments = approval["arguments"]
        if key != self.key or arguments != self._arguments:
            body = json.dumps(arguments, ensure_ascii=False, indent=2)
            self.text.configure(state="normal")
            self.text.delete("1.0", "end")
            self.text.insert("1.0", body)
            self.text.configure(state="disabled")
            self.text.yview_moveto(0)
            self.key = key
            self._arguments = deepcopy(arguments)
        # This native Text widget stays mounted while the canvas animates.
        # Repeated place_forget/place or font reconfiguration every 55 ms
        # flickers on layered Windows windows and disrupts reading/copying.
        appearance = (background, foreground, round(12*scale))
        if appearance != self._appearance:
            self.frame.configure(bg=background)
            self.text.configure(bg=background, fg=foreground, insertbackground=foreground,
                                font=("Microsoft YaHei UI", -appearance[2]))
            self._appearance = appearance
        geometry = tuple(round(value*scale) for value in (x, y, width, height))
        if not self._visible or geometry != self._geometry:
            self.frame.place(x=geometry[0], y=geometry[1], width=geometry[2], height=geometry[3])
            self._geometry = geometry
            self._visible = True
