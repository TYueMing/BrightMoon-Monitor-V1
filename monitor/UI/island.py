"""Three-level, animated desktop island matching monitor/UX-design.png."""

from __future__ import annotations

import math
import time
import tkinter as tk
from tkinter import font as tkfont
from typing import Callable
from PIL import ImageTk
from .display import ScaledCanvas, window_scale, screen_bounds
from .authorization import ApprovalDetails
from .task_selection import TaskSelection
from .task_strip import TaskStrip
from .app_icons import AppIconCache
from .wallpapers import IslandWallpaper
from .island_surface import IslandSurface
from .rope_pendant import RopePendant
from .resident import ResidentView, IDLE_GREEN
from .window_state import LIVE, SnapshotCursor, spring_progress, SPRING_SECONDS
from ..src.preferences import normalize_preferences
from ..music.play import CompletionSoundPlayer

from .common_model import FINAL, STATUS_TEXT, elapsed, status_for
from .theme import COLORS, ACTIVE, blend
from .detail_panels import panel_for
from ..src.task_identity import task_identity

TRANSPARENT = "#ed01ec"


class DynamicIsland:
    MINI_WIDTH = 300
    WIDTH = 420
    MINI_HEIGHT = 62

    def __init__(self, root: tk.Tk, snapshot_reader: Callable[[], dict],
                 command_sender: Callable[[str, str, dict], object] | None = None,
                 completion_player: CompletionSoundPlayer | None = None, *, display_history=None,
                 background_reader=None, rope_reader=None, cartoon_reader=None, view_publisher=None) -> None:
        self.root, self.snapshot_reader, self.command_sender = root, snapshot_reader, command_sender
        self.view_publisher = view_publisher
        self.publish_error = False
        root.withdraw()
        self.expanded = False
        self.details = False
        self.snapshot = {"status": "offline", "message": "正在连接", "runs": [], "actions": [],
                         "source_states": {}}
        self.selected_id = ""
        self.selection = TaskSelection(display_history)
        self.task_strip = TaskStrip()
        self.resident_view = ResidentView(cartoon_reader)
        self._resident_empty = False
        self._notice_run_id = ""
        self.phase = 0.0
        self._closed = False
        self._jobs = {}
        self._animation = None
        self._opening = False
        self._completion_rows = {}
        self._size = (self.MINI_WIDTH, self.MINI_HEIGHT)
        self._drag_origin = None
        self._click_origin = None
        self._dragged = False
        self._buttons = []
        self._press_key = ""
        self._focus_key = ""
        self._pending = None
        self._pending_command_id = ""
        self._notice = ""
        self._notice_until = 0.0
        self._cursor = SnapshotCursor()
        self._hidden = False
        self._appearance = None
        self._background_reader = background_reader
        self._rope_reader = rope_reader
        self.pendant = RopePendant(root, on_activate=self._activate_pendant)
        self._wallpaper = IslandWallpaper()
        self._wallpaper_frame = self._wallpaper_tk = None
        self._surface = IslandSurface()
        self._surface_frame = self._surface_tk = None
        self._halo_frame = self._halo_tk = None
        self._halo_requests = []
        self._completion_player = completion_player or CompletionSoundPlayer()
        self._sound_initialized = False
        # Sources may connect after an initially empty snapshot. Seed durable
        # outcomes too, so a restart during their grace period stays silent.
        self._played_completions: set[str] = {
            key for key, record in self.selection.lifetime.history.records.items() if record["terminal_seen"]
        }
        root.update_idletasks()
        self.scale = window_scale(root)
        self._fonts = {
            "title": tkfont.Font(root=root, family="Microsoft YaHei UI", size=-14, weight="bold"),
            "body": tkfont.Font(root=root, family="Microsoft YaHei UI", size=-13),
            "small": tkfont.Font(root=root, family="Microsoft YaHei UI", size=-11),
            "time": tkfont.Font(root=root, family="Segoe UI", size=-14, weight="bold"),
            "brand": tkfont.Font(root=root, family="Georgia", size=-24, weight="bold", slant="italic"),
        }
        self._scale_fonts()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg=TRANSPARENT)
        try:
            root.attributes("-transparentcolor", TRANSPARENT)
        except tk.TclError:
            root.configure(bg="#0e1522")
        self.canvas = ScaledCanvas(root, scale=self.scale, bg=TRANSPARENT, highlightthickness=0,
                                   width=round(self.MINI_WIDTH*self.scale), height=round(self.MINI_HEIGHT*self.scale))
        # Essential: the canvas must grow with the window during expansion.
        self.canvas.pack(fill="both", expand=True)
        self._approval_details = ApprovalDetails(self.canvas)
        self._icons = AppIconCache(root)
        root.update_idletasks()
        left, top, right, bottom = screen_bounds(root)
        x = left + (right-left-round(self.MINI_WIDTH*self.scale)) // 2
        self._geometry(self.MINI_WIDTH, self.MINI_HEIGHT, x, top+40)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<MouseWheel>", self._panel_scroll)
        self.canvas.bind("<Button-4>", lambda event: self._panel_scroll(event, -1))
        self.canvas.bind("<Button-5>", lambda event: self._panel_scroll(event, 1))
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Escape>", lambda _: self._escape())
        self.canvas.bind("<Tab>", self._tab)
        self.canvas.bind("<Return>", self._activate_focused)
        self.canvas.bind("<space>", self._activate_focused)
        self.canvas.focus_set()
        self._begin_opening()
        self._poll()
        if not self._hidden:
            root.deiconify()
        self._tick()

    def _scale_fonts(self) -> None:
        for key, size in (("title",14),("body",13),("small",11),("time",14),("brand",24)):
            self._fonts[key].configure(size=-round(size*self.scale))

    def _geometry(self, w, h, x, y) -> None:
        # Withdrawn Tk windows can still report (0, 0) until they are mapped.
        # Preserve the requested origin so opening animation never loses it.
        self._position = (round(x), round(y))
        self.root.geometry(f"{round(w*self.scale)}x{round(h*self.scale)}+{round(x)}+{round(y)}")
        self._sync_pendant()

    def _sync_pendant(self):
        image = self._rope_reader() if self._rope_reader else None
        self.pendant.set_image(image)
        x, y = self._position
        self.pendant.sync(ready=not (self._closed or self._hidden or self.expanded
                                    or self._opening or self._animation or self._dragged),
                          anchor=(x+self._size[0]*self.scale/2,
                                  y+(self._size[1]-6)*self.scale),
                          scale=self.scale,
                          opacity=(self._appearance or {}).get("monitor_opacity", 100)/100,
                          horizontal_bounds=(x+5*self.scale,(self._size[0]-10)*self.scale),
                          position=self.snapshot.get("preferences",{}).get("rope_position",50))

    def _apply_appearance(self) -> None:
        prefs = normalize_preferences(self.snapshot.get("preferences"))
        if prefs == self._appearance:
            return
        self._appearance = prefs
        bg = prefs["monitor_background_color"]
        # This exact RGB is the Windows color key, so do not use it as an
        # opaque panel fill even if the user chooses magenta.
        if bg == TRANSPARENT:
            bg = "#ed02ec"
        rgb = [int(bg[i:i+2],16)/255 for i in (1,3,5)]
        light = sum(a*b for a,b in zip(rgb,(.2126,.7152,.0722))) > .55
        ink, muted = ("#19253a","#495970") if light else ("#eef4ff","#a5b4cd")
        edge = blend(bg,ink,.25)
        self.canvas.palette = {
            "#141d2c":bg, "#101724":bg, "#0b101a":blend(bg,"#ffffff" if light else "#000000",.30),
            "#111c31":bg,
            "#1a2639":blend(bg,ink,.06), "#25334b":blend(bg,ink,.11),
            "#263144":edge, "#2a374c":edge, "#354259":blend(bg,ink,.35),
            "#465267":edge, "#eef4ff":ink, "#eff4ff":ink, "#d4e0f2":ink,
            "#8292ab":muted, "#9eafc7":muted, "#a1b0c6":muted, "#c4d1e5":ink,
            "#b4c3d9":muted,
        }
        self.root.attributes("-alpha", prefs["monitor_opacity"]/100)

    def run(self) -> None:
        if not self._closed:
            self.root.mainloop()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for job in self._jobs.values():
            try:
                self.root.after_cancel(job)
            except tk.TclError:
                pass
        self._jobs.clear()
        self.pendant.close()
        self.selection.lifetime.history.close()
        self.root.destroy()

    def _schedule(self, name: str, delay: int, callback) -> None:
        if self._closed:
            return
        old = self._jobs.pop(name, None)
        if old:
            self.root.after_cancel(old)
        self._jobs[name] = self.root.after(delay, callback)

    def _run(self) -> dict:
        # Never fall back to an expired task still retained by a source adapter.
        return self.selection.focused()

    def _state(self) -> str:
        if self._is_idle():
            return "idle"
        return status_for(self.snapshot, self._run())

    def _is_idle(self):
        return bool(self.snapshot.get("preferences",{}).get("island_resident",False) and not self.selection.runs)

    def _panel(self):
        if self._is_idle():
            return self.resident_view
        return panel_for(self._run().get("app_id", "unknown"))

    def _rows(self):
        """Compatibility for existing diagnostics; semantics belong to the provider."""
        return self._panel().rows(self)

    def _summary_lines(self):
        return self._panel().summary_lines(self)

    def _target_size(self) -> tuple[int, int]:
        width, height = ((self.MINI_WIDTH, self.MINI_HEIGHT) if not self.expanded
                         else self._panel().size(self))
        if self._is_idle() and not self.expanded:
            width = 360
        if len(self.selection.runs) > 1:
            width = max(width, 440)
            if self.expanded:
                height += 24
        return width, height


    def _begin_opening(self) -> None:
        self.pendant.hide()
        self.root.update_idletasks()
        x, y = self._position
        center = x + self._size[0]*self.scale/2
        self._opening = True
        self._animation = None
        self.expanded, self.details = False, False
        self._size = (self.MINI_HEIGHT, self.MINI_HEIGHT)
        self._geometry(*self._size, center-self.MINI_HEIGHT*self.scale/2, y)
        self.root.update_idletasks()

    def _resize(self) -> None:
        target = self._target_size()
        if (self._animation and self._animation["target"] == target) or (not self._animation and target == self._size):
            return
        self._animation = {
            "at": time.monotonic() + (.10 if self._opening else 0), "start": self._size, "target": target,
            "center": self._position[0] + self._size[0]*self.scale / 2, "top": self._position[1],
        }
        self._animate()

    def _animate(self) -> None:
        if not self._animation or self._closed:
            return
        anim = self._animation
        t = min(1.0, (time.monotonic() - anim["at"]) / SPRING_SECONDS)
        ease = spring_progress(t)
        w, h = (round(a + (b - a) * ease) for a, b in zip(anim["start"], anim["target"]))
        w, h = max(self.MINI_HEIGHT if self._opening else 240,w), max(48,h)
        self._size = w, h
        x = max(0, min(self.root.winfo_screenwidth() - w*self.scale, round(anim["center"] - w*self.scale / 2)))
        y = max(0, min(self.root.winfo_screenheight() - h*self.scale, anim["top"]))
        self._geometry(w,h,x,y)
        self.draw()
        if t < 1:
            self._schedule("animation", 16, self._animate)
        else:
            self._animation = None
            self._opening = False
            self.draw()

    def _toggle(self) -> None:
        if not self.expanded and self.pendant.request_pull(self._apply_toggle):
            return
        self._apply_toggle()

    def _activate_pendant(self) -> None:
        # A release queued before hide/collapse must not toggle a later card.
        if not (self._closed or self._hidden or self.expanded):
            self._toggle()

    def _apply_toggle(self) -> None:
        if self._closed or self._hidden:
            return
        self.pendant.hide()
        if self._opening:
            self._opening = False
        if not self.expanded:
            self.expanded, self.details = True, False
            self.selection.update(self.snapshot, expanded=True)
        elif self._state() in FINAL:
            # The capsule header always controls the whole island.  A completed
            # task must return to the compact live capsule on the next click;
            # the execution-step accordion has its own completion_rows target.
            run_id = self._run().get("run_id")
            if run_id:
                self._completion_rows[run_id] = False
            self.expanded, self.details = False, False
        elif not self.details and self._state() != "waiting_confirmation":
            self.details = True
        else:
            self.expanded, self.details = False, False
        if not self.expanded:
            self.selection.collapse()
        self._focus_key = ""
        self._resize()

    def _escape(self) -> str:
        self.pendant.hide()  # Also cancel an expansion still in its rope prelude.
        if self.details and self.expanded:
            self.details = False
            self._resize()
        elif self.expanded:
            self.expanded, self.details = False, False
            self._resize()
        if not self.expanded:
            self.selection.collapse()
        return "break"

    def _hit(self, x: float, y: float):
        for button in reversed(self._buttons):
            x1,y1,x2,y2 = button["box"]
            if x1 <= x <= x2 and y1 <= y <= y2:
                # Real controls take priority. A disabled approval must also
                # consume its area, rather than collapsing the card beneath it.
                return button if button["enabled"] else None
        w,h = self._size
        if self.expanded and 8 <= x <= w-8 and 62 <= y <= h-8:
            # Text, artwork and empty space share the existing stage transition.
            # This fallback is not a second tab stop or a huge focus outline.
            return {"box":(8,62,w-8,h-8),"key":"toggle","enabled":True,"action":None}
        return None

    def _press(self, event) -> None:
        self.canvas.focus_set()
        button = self._hit(event.x/self.scale, event.y/self.scale)
        self._press_key = button["key"] if button else ""
        self._dragged = False
        self._click_origin = (event.x,event.y)
        self._drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y()) if event.y/self.scale < 61 else None

    def _drag(self, event) -> None:
        if not self._drag_origin:
            if self._click_origin:
                x,y = self._click_origin
                if abs(event.x-x)+abs(event.y-y) >= 5:
                    self._dragged = True
            return
        ox, oy, wx, wy = self._drag_origin
        dx, dy = event.x_root - ox, event.y_root - oy
        if abs(dx) + abs(dy) < 5 and not self._dragged:
            return
        self._dragged = True
        self.pendant.hide()
        self._animation = None
        w, h = self._size
        x = max(0, min(self.root.winfo_screenwidth() - w*self.scale, wx + dx))
        y = max(0, min(self.root.winfo_screenheight() - h*self.scale, wy + dy))
        self._geometry(w,h,x,y)

    def _release(self, event) -> None:
        button = self._hit(event.x/self.scale, event.y/self.scale)
        if self._click_origin:
            x,y = self._click_origin
            if abs(event.x-x)+abs(event.y-y) >= 5:
                self._dragged = True
        if not self._dragged and button and button["key"] == self._press_key:
            self._activate(button)
        self._drag_origin = None
        self._click_origin = None
        self._press_key = ""
        self._dragged = False
        self._sync_pendant()

    def _hover(self, event) -> None:
        self.canvas.configure(cursor="hand2" if self._hit(event.x/self.scale, event.y/self.scale) else "")

    def _tab(self, _event=None) -> str:
        keys = [b["key"] for b in self._buttons if b["enabled"]]
        if keys:
            index = (keys.index(self._focus_key) + 1) % len(keys) if self._focus_key in keys else 0
            self._focus_key = keys[index]
            self.draw()
        return "break"

    def _activate_focused(self, _event=None) -> str:
        button = next((b for b in self._buttons if b["key"] == self._focus_key and b["enabled"]), None)
        if button:
            self._activate(button)
        elif not self._focus_key:
            self._toggle()
        return "break"

    def select_task(self, run_id: str) -> bool:
        """Select a monitored task without changing its execution or approvals."""
        # A direct circle click is explicit navigation, even if opening would
        # otherwise auto-focus the oldest pending approval.
        self.selection.update(self.snapshot, expanded=True)
        if not self.selection.select(run_id):
            return False
        self.selected_id = run_id
        self._focus_key = "task:" + run_id
        if not self.expanded and self.pendant.request_pull(self._expand_selected_task):
            self.draw()
            return True
        self._expand_selected_task()
        return True

    def _expand_selected_task(self):
        if self._closed or self._hidden:
            return
        self.pendant.hide()
        if not self.expanded:
            self.expanded, self.details = True, False
        self._opening = False
        self._resize()
        self.draw()

    def _activate(self, button: dict) -> None:
        if button["key"].startswith("task:"):
            self.select_task(button["key"][5:])
        elif button["key"] == "task_more":
            self.selection.more = not self.selection.more
            self.draw()
        elif button["key"] == "toggle":
            self._toggle()
        elif button["key"] == "details":
            self.details = not self.details
            self._resize()
        elif button["key"] == "completion_rows":
            run_id = self._run().get("run_id")
            self._completion_rows[run_id] = not self._completion_rows.get(run_id, False)
            self._resize()
        elif button["key"].startswith("panel:"):
            # Optional local provider controls never enter the command mailbox.
            handler = getattr(self._panel(), "activate", None)
            if handler:
                handler(self, button["key"])
        elif button.get("action"):
            self._send_action(button["action"])

    def _panel_scroll(self, event, direction=None):
        handler = getattr(self._panel(), "scroll", None)
        if self.expanded and handler:
            delta = getattr(event, "delta", 0)
            if direction is None and not delta:
                return None
            if handler(self, direction if direction is not None else (-1 if delta > 0 else 1)):
                return "break"
        return None

    def _send_action(self, action: dict) -> None:
        if not self.command_sender or self._pending:
            return
        self._notice_run_id = str(action["run_id"])
        try:
            command_id = self.command_sender(
                str(action["type"]), str(action["run_id"]),
                {"confirmation_id": action["confirmation_id"], "expected_revision": action.get("expected_revision")} if action.get("confirmation_id") else {},
            )
            # Submission is not approval. Wait for the backend snapshot.
            self._pending = (action["run_id"], action.get("confirmation_id", ""), time.monotonic())
            self._pending_command_id = command_id if isinstance(command_id, str) else ""
            self._notice = "已发送，等待 Agent 确认..."
            self._notice_until = time.monotonic() + 8
        except Exception:
            self._notice = "发送失败，请检查主程序后重试"
            self._notice_until = time.monotonic() + 8
        self.draw()

    def _poll(self) -> None:
        try:
            snapshot = self.snapshot_reader()
            if not isinstance(snapshot, dict):
                self._reader_unavailable()
            else:
                if (snapshot.get("status") == "error" and not snapshot.get("runs")
                        and not snapshot.get("source_states") and "next_reminder" not in snapshot):
                    self._reader_unavailable()
                else:
                    accepted, _ = self._cursor.accept(snapshot)
                    if accepted:
                        self.snapshot = snapshot
        except Exception:
            self._reader_unavailable()
        # Lifetime advances even if the reader failed or a stale revision was
        # rejected. A connection failure must not pin the last task forever.
        now = time.time()
        new_batch = self.selection.update(self.snapshot, expanded=self.expanded, now=now)
        idle = self._is_idle()
        entering_idle = idle and not self._resident_empty
        self._resident_empty = idle
        self._play_new_completions(self.snapshot)
        if new_batch or entering_idle:
            self.pendant.hide()
            self._completion_rows.clear()
            self.selected_id = ""
            self.expanded, self.details = False, False
            self._pending, self._notice = None, ""
            self._notice_until = 0
            self._focus_key, self._press_key = "", ""
            if not self._opening:
                self._animation = None
        if entering_idle:
            # Collapse only on the transition; subsequent empty polls must let
            # the user explore the next-reminder card without snapping it shut.
            self.selection.collapse()
            self.resident_view.reset()
        next_scale = window_scale(self.root)
        if abs(self.scale-next_scale) > .01:
            self.scale = self.canvas.scale = next_scale
            self._scale_fonts()
            self._animation = None
            self._geometry(*self._size,self.root.winfo_x(),self.root.winfo_y())
        self._apply_appearance()
        if self._pending:
            run_id, confirmation_id, sent_at = self._pending
            run = next((r for r in self.snapshot.get("runs", []) if r.get("run_id") == run_id), {})
            confirmation = next((p for p in run.get("confirmations", []) if p.get("id") == confirmation_id), {})
            receipt = run.get("command_receipt") or {}
            resolved = (confirmation_id and confirmation.get("status") in {"approved", "denied"}) or (
                not confirmation_id and run.get("status") in {"cancellation_requested", "cancelled"})
            if receipt.get("command_id") == self._pending_command_id and self._pending_command_id and receipt.get("status") == "rejected":
                self._pending = None
                self._notice = {"VERSION_CONFLICT":"授权状态已更新，请查看最新操作", "APPROVAL_EXPIRED":"本次授权已过期，等待 Agent 更新"}.get(receipt.get("error_code"),"操作未被接受，请重试")
                self._notice_until = time.monotonic() + 8
            elif resolved:
                self._pending = None
                self._notice = "操作已确认"
                self._notice_until = time.monotonic() + 2
            elif confirmation.get("status") in {"expired", "superseded"} or run.get("status") in FINAL:
                self._pending = None
                self._notice = "任务已结束或操作已失效"
                self._notice_until = time.monotonic() + 2
            elif time.monotonic() - sent_at > 8:
                self._pending = None
                self._notice = "尚未收到确认，请重试"
                self._notice_until = time.monotonic() + 8
        # TaskLifetime is the sole deadline owner, including independent source
        # failures. Retained backend records never resurrect expired UI cards.
        hide = not self.selection.runs and not idle
        if hide and not self._hidden:
            self.pendant.hide()
            self._hidden = True
            self.expanded, self.details = False, False
            self._animation = None
            self._opening = False
            self.root.withdraw()
        elif self._hidden and (idle or (self.selection.runs and any(
            not run.get("connection_error") and run.get("status") not in {"error", "stale"}
            for run in self.selection.runs
        ))):
            self._hidden = False
            self._begin_opening()
            self.root.deiconify()
            self.root.attributes("-topmost", True)
        # Publish the already-filtered window; remote readers must never rerun
        # reminder scheduling or durable task retirement on their own threads.
        if self.view_publisher:
            try:
                self.view_publisher(self.snapshot, self.selection.runs, self.selection.focus_id, not self._hidden)
                self.publish_error = False
            except Exception:
                self.publish_error = True
        try:
            if not self._hidden:
                self._resize()
            self.draw()
        finally:
            self._schedule("poll", 400, self._poll)

    def _reader_unavailable(self) -> None:
        # Preserve identity and preferences so a transient transport outage
        # neither resets the timeout nor exposes cached approval controls.
        self.snapshot = {**self.snapshot, "status": "error", "actions": [],
                         "runs": [{**run, "connection_error": True}
                                  for run in self.snapshot.get("runs", [])]}

    def _play_new_completions(self, snapshot: dict) -> None:
        """Play one cue for each newly observed completed run.

        The first snapshot only seeds the set, so opening the monitor does not
        replay historical completions. Generation-aware identities make
        polling and backend revisions idempotent even in reused sessions.
        """
        completed = {
            task_identity(run)
            for run in snapshot.get("runs", [])
            if isinstance(run, dict) and run.get("status") == "completed" and run.get("run_id")
            and not self.selection.lifetime.history.records.get(task_identity(run), {}).get("retired")
        }
        if not self._sound_initialized:
            self._played_completions.update(completed)
            self._sound_initialized = True
            return
        prefs = normalize_preferences(snapshot.get("preferences"))
        for run_id in sorted(completed - self._played_completions) if prefs["monitor_sound_enabled"] else ():
            try:
                sound = prefs["monitor_completion_sound"]
                self._completion_player.play(sound)
            except Exception:
                # A missing codec or unavailable audio device must never stop
                # monitoring or affect Agent execution.
                pass
        self._played_completions.update(completed)

    def _tick(self) -> None:
        self.phase += .15
        self.draw()
        self._schedule("tick", 1000 if self._hidden else 55, self._tick)

    @staticmethod
    def _rounded(c, x1, y1, x2, y2, r, fill, outline="", width=1) -> None:
        # Detail containers share the tinted wallpaper instead of covering it
        # with a solid slab. Approval text widgets/buttons retain their own
        # readable surfaces; provider layout and action handling stay intact.
        if fill == "#1a2639" and getattr(c, "wallpaper_active", False):
            fill = ""
        r = max(0, min(r, (x2-x1)/2, (y2-y1)/2))
        points = []
        for cx,cy,start in ((x2-r,y1+r,-90),(x2-r,y2-r,0),(x1+r,y2-r,90),(x1+r,y1+r,180)):
            for step in range(13):
                angle = math.radians(start+step*7.5)
                points.extend((cx+r*math.cos(angle),cy+r*math.sin(angle)))
        c.create_polygon(points, smooth=True, splinesteps=4, fill=fill, outline=outline, width=width)

    def _text(self, x, y, text, width=None, fill="#eef4ff", font="body", anchor="w"):
        value = str(text)
        f = self._fonts[font]
        if width:
            while value and f.measure(value)/self.scale > width:
                value = value[:-2].rstrip("…") + "…" if len(value) > 2 else "…"
                if value == "…":
                    break
        return self.canvas.create_text(x, y, text=value, fill=fill, font=f, anchor=anchor)

    def _register(self, box, key, enabled=True, action=None) -> None:
        offset = getattr(self.canvas, "offset_y", 0)
        box = (box[0], box[1]+offset, box[2], box[3]+offset)
        self._buttons.append({"box": box, "key": key, "enabled": enabled, "action": action})

    def _button(self, box, key, label, fill="#28364e", action=None, enabled=True) -> None:
        x1,y1,x2,y2 = box
        self._rounded(self.canvas,x1,y1,x2,y2,17,fill if enabled else "#202b3c")
        self._text((x1+x2)/2,(y1+y2)/2,label,font="body",anchor="center",fill="#f3f6ff" if enabled else "#8996a9")
        self._register(box,key,enabled,action)

    def _draw_surface(self, w, h, radius):
        photo = self._background_reader() if self._background_reader else None
        size = (max(1,round((w-10)*self.scale)),max(1,round((h-11)*self.scale)))
        radius = round((radius-2)*self.scale)
        color = self.canvas.palette.get("#141d2c", "#141d2c")
        if photo is None:
            self._wallpaper.render(None, (1,1), scale=self.scale, radius=0, color="#141d2c")
            self._wallpaper_frame = self._wallpaper_tk = None
            frame = self._surface.solid(size,radius,color)
        else:
            _, top, _, bottom = screen_bounds(self.root)
            frame = self._wallpaper.render(photo,size,scale=self.scale,radius=radius,color=color,
                scene_height=min(580, (bottom-top)/self.scale-80))
        # Breathing indicators repaint frequently. Reuse both the pixels and
        # the Tk image at rest; no disk I/O or image resize on each tick.
        if frame is not self._surface_frame:
            self._surface_tk = ImageTk.PhotoImage(frame,master=self.root)
            self._surface_frame = frame
        if photo is not None:
            self._wallpaper_frame,self._wallpaper_tk = frame,self._surface_tk
        tags = ("island_surface","island_wallpaper") if photo is not None else ("island_surface",)
        self.canvas.create_image(5,5,image=self._surface_tk,anchor="nw",tags=tags)
        return photo is not None

    def _draw_halos(self):
        frame = self._surface.halos(self._surface_frame,self._halo_requests,self.scale)
        if frame is None:
            self._halo_frame = self._halo_tk = None
            return
        if frame is not self._halo_frame:
            self._halo_tk = ImageTk.PhotoImage(frame,master=self.root)
            self._halo_frame = frame
        item = self.canvas.create_image(5,5,image=self._halo_tk,anchor="nw",tags=("task_ring_halo",))
        # All task glows share one layer below the logos/text, so an adjoining
        # glow cannot paint over a previously drawn task in the multi-task strip.
        self.canvas.tag_raise(item,"island_surface")

    def draw(self) -> None:
        if self._closed:
            return
        self._sync_pendant()
        if self._hidden:
            self._approval_details.hide()
            return
        c, (w,h) = self.canvas, self._size
        c.offset_y = 0
        c.delete("all")
        self._buttons = []
        self._halo_requests = []
        status = self._state()
        color = IDLE_GREEN if status == "idle" else COLORS.get(status, COLORS["offline"])
        radius = 29 if not self.expanded else 27
        # A single fill reaches the rounded silhouette. No stacked outlines or
        # differently colored rim is left behind when changing wallpaper/alpha.
        has_wallpaper = self._draw_surface(w,h,radius)
        c.wallpaper_active = has_wallpaper
        if self._opening and w < 240:
            self._approval_details.hide()
            progress = max(0, min(1, (w-self.MINI_HEIGHT)/(240-self.MINI_HEIGHT)))
            self._draw_ring(31+3*progress,31,color,status,
                            run={"app_id":"solo_idle"} if self._is_idle() else self._run(),
                            focus=1. if self._is_idle() else 0.)
            self._draw_halos()
            return
        if self._is_idle():
            self.resident_view.draw_header(self)
        else:
            self.task_strip.draw(self)
        self._draw_halos()
        approval_visible = False
        if self.expanded and h > 75:
            c.create_line(22,62,w-22,62,fill="#263144")
            if len(self.selection.runs) > 1:
                self._text(26,78,self.task_strip.detail_label(self._run()),width=w-140,font="small",fill="#9eafc7")
                self._text(w-26,78,STATUS_TEXT.get(status,"执行中"),anchor="e",font="small",fill=color)
                c.offset_y = 24
            approval_visible = self._panel().draw(self, color)
        if not approval_visible:
            self._approval_details.hide()
        c.offset_y = 0
        for button in self._buttons:
            if button["key"] == self._focus_key and button["enabled"]:
                # Task selection is expressed by the status ring itself.
                # Keep rectangular focus outlines only for ordinary controls.
                if button["key"].startswith("task:"):
                    continue
                x1,y1,x2,y2 = button["box"]
                self._rounded(c,x1+2,y1+2,x2-2,y2-2,12,"","#9bbaff",1)

    def _draw_ring(self, x, y, color, status, *, run=None, focus=0.0, keyboard_focus=False) -> None:
        c = self.canvas
        background = c.palette.get("#141d2c", "#141d2c")
        logo_radius, radius = 14, 16
        focus = max(0.0, min(1.0, float(focus or 0.0)))
        symbolic = status in {"completed", "failed", "error", "stale"}
        wave = .5+.5*math.sin(time.monotonic()*2.8)
        active_indicator = status in {"running", "waiting_model", "waiting_confirmation", "cancellation_requested", "reminding", "idle"}

        # Composite a continuous glow over the actual surface in one header
        # layer. Three opaque strokes looked like extra rings on light themes.
        if active_indicator and focus > .02:
            pulse = .62 + .38 * wave
            self._halo_requests.append((x-5,y-5,radius,color,pulse*focus))
        if not symbolic and (run or status in LIVE):
            # Keep the 28 logical-pixel logo; the enlarged ring has a white gap.
            # Stop/partial states still identify their source app; only the
            # existing success/failure symbols replace the logo.
            c.create_oval(x-radius,y-radius,x+radius,y+radius,
                          fill="#ffffff",outline="",tags=("task_logo_gap",))
            icon = self._icons.get((run or {}).get("app_id", ""), round(2*logo_radius*self.scale))
            if icon:
                c.create_image(x,y,image=icon,tags=("app_logo",))
            else:
                c.create_oval(x-5,y-5,x+5,y+5,outline=color,width=2)
        # Focus levels ease across tasks; non-selected rings retain a steady
        # color. Blue execution and yellow authorization rings are thicker so
        # their state remains legible at the compact 28px diameter.
        brightness = .78 + focus*(.46+.54*wave-.78)
        indicator = blend(background, color, brightness)
        stroke = 3.6 if active_indicator else 2.2
        if keyboard_focus:
            stroke += .35
        c.create_oval(x-radius,y-radius,x+radius,y+radius,
                      outline=blend(background,indicator,.48) if status in LIVE else indicator,
                      width=stroke,tags=("task_ring", "task_ring_track"))
        if status in LIVE:
            c.create_arc(x-radius,y-radius,x+radius,y+radius,start=(-self.phase*30)%360,extent=265,
                         style="arc",outline=indicator,width=stroke,tags=("task_ring", "task_ring_indicator"))
        if status == "completed":
            c.create_line(x-5,y,x-1,y+4,x+6,y-5,fill=color,width=2,capstyle="round")
        elif status in {"failed","error","stale"}:
            c.create_line(x,y-5,x,y+1,fill=color,width=2)
            c.create_oval(x-1,y+4,x+1,y+6,fill=color,outline="")


