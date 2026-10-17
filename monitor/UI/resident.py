"""Resident empty view: presentation only, never a fake monitored task."""
import time
from PIL import Image, ImageDraw, ImageFont, ImageTk

from .completion_panel import summary_lines
from ..tasks.schedule import stamp_label, repeat_label

IDLE_GREEN = "#43d994"
PREVIEW_BLUE = "#1e40af"
PREVIEW_RED = "#991b1b"
MARQUEE_ENTER = MARQUEE_LEAVE = 2.0
MARQUEE_HOLD = 1.0
MARQUEE_GAP = .3
MARQUEE_CYCLE = MARQUEE_ENTER + MARQUEE_HOLD + MARQUEE_LEAVE + MARQUEE_GAP
COMPACT_CARD_HEIGHT = 266
DETAIL_CARD_HEIGHT = 354


def preview_count(snapshot):
    if snapshot.get("task_data_error") or not snapshot.get("preferences",{}).get("task_monitor_enabled",True):
        return 0
    value = snapshot.get("upcoming_reminder_count",int(bool(snapshot.get("next_reminder"))))
    return max(0,value) if type(value) is int else 0


def marquee_label(snapshot):
    if snapshot.get("task_data_error"):
        return "读取失败"
    if not snapshot.get("preferences",{}).get("task_monitor_enabled",True):
        return "提醒关闭"
    count = preview_count(snapshot)
    return f"{count}项任务" if count else "无任务！"


def marquee_offset(elapsed, width, text_width):
    """Enter from right, hold the complete label for 1s, exit left, repeat."""
    enter, hold, leave, gap = MARQUEE_ENTER, MARQUEE_HOLD, MARQUEE_LEAVE, MARQUEE_GAP
    phase = max(0.,elapsed) % (enter+hold+leave+gap)
    rest = (width-text_width)/2
    if phase < enter:
        return width+(rest-width)*phase/enter
    if phase < enter+hold:
        return rest
    if phase < enter+hold+leave:
        return rest+(-text_width-rest)*(phase-enter-hold)/leave
    return -text_width


class ResidentView:
    def __init__(self, cartoon_reader=None):
        self.since = time.monotonic()
        self._key = self._photo = self._frame_key = None
        self._source = None
        self._label = None
        self._character_source = None
        self.cartoon_reader = cartoon_reader or (lambda:None)
        self._character_key = self._character_photo = None

    def reset(self):
        self.since = time.monotonic()

    def draw_header(self, ui):
        width = ui._size[0]
        ui._register((8,5,width-8,58),"toggle")
        ui._draw_ring(34,31,IDLE_GREEN,"idle",run={"app_id":"solo_idle"},focus=1.)
        ui._text(width/2,31,"Bright Moon",width=width-174,font="brand",anchor="center")
        self._marquee(ui,width-86,31,68)

    def _marquee(self, ui, x, y, width):
        # A small transparent, clipped text layer slides over either wallpaper
        # or solid color. Cache glyph rasterization; never load fonts per frame.
        label = marquee_label(ui.snapshot)
        count = preview_count(ui.snapshot)
        ink = PREVIEW_BLUE if count else IDLE_GREEN
        bg = ui.canvas.palette.get("#141d2c", "#141d2c")
        if not count and sum(int(bg[i:i+2],16)*v for i,v in zip((1,3,5),(.2126,.7152,.0722))) > 140:
            ink = "#087a43"
        if self._label is not None and self._label != label:
            self.since = time.monotonic()
        self._label = label
        key = (ui.scale,ink,label)
        if key != self._key:
            font = None
            for name in ("msyh.ttc","msyhbd.ttc","simhei.ttf"):
                try:
                    size = max(1,round(13*ui.scale))
                    font = ImageFont.truetype(name,size)
                    while font.getlength(label) > (width-4)*ui.scale and size > 1:
                        size -= 1
                        font = ImageFont.truetype(name,size)
                    break
                except OSError:
                    continue
            self._key, self._frame_key = key, None
            self._source = None
            if font:
                box = font.getbbox(label)
                source = Image.new("RGBA",(box[2]-box[0]+2,box[3]-box[1]+2))
                draw = ImageDraw.Draw(source)
                if count:
                    # Both colors share one transparent layer and baseline,
                    # so the number and label scroll together without a box.
                    digits = str(count)
                    draw.text((1-box[0],1-box[1]),digits,font=font,fill=PREVIEW_RED)
                    draw.text((1-box[0]+font.getlength(digits),1-box[1]),label[len(digits):],font=font,fill=ink)
                else:
                    draw.text((1-box[0],1-box[1]),label,font=font,fill=ink)
                self._source = source
        if self._source is None:
            # Font files may differ on non-Windows hosts; keep a legible native
            # label instead of drawing missing-glyph boxes or failing the UI.
            if count:
                name = "body" if ui._fonts["body"].measure(label)/ui.scale <= width else "small"
                font = ui._fonts[name]
                left = x+(width-font.measure(label)/ui.scale)/2
                digits = str(count)
                ui._text(left,y,digits,font=name,fill=PREVIEW_RED)
                ui._text(left+font.measure(digits)/ui.scale,y,label[len(digits):],font=name,fill=ink)
            else:
                ui._text(x+width/2,y,label,font="body",fill=ink,anchor="center",width=width)
            return
        pixels = round(width*ui.scale)
        offset = round(marquee_offset(time.monotonic()-self.since,pixels,self._source.width))
        frame_key = (key,pixels,offset)
        if frame_key != self._frame_key:
            frame = Image.new("RGBA",(pixels,self._source.height))
            frame.paste(self._source,(offset,0))
            self._photo = ImageTk.PhotoImage(frame,master=ui.root)
            self._frame_key = frame_key
        ui.canvas.create_image(x,y,image=self._photo,anchor="w",tags=("resident_marquee",))

    def header(self, run, status):
        return "Bright Moon"

    def rows(self, ui):
        return []

    def summary_lines(self, ui):
        next_item = ui.snapshot.get("next_reminder") or {}
        body = next_item.get("task",{}).get("body") or "到了时间，我会轻轻提醒你。"
        lines = summary_lines(body,lambda s:ui._fonts["body"].measure(s)/ui.scale,width=ui.WIDTH-60)
        result = lines[:2]
        if result and (len(lines)>2 or len(body)>200):
            result[-1] = result[-1].rstrip("…")+"…"
        return result

    def size(self, ui):
        return ui.WIDTH, DETAIL_CARD_HEIGHT if ui.details else COMPACT_CARD_HEIGHT

    def _character(self, ui):
        # The app resolves user-owned assets outside the animation loop. Image
        # identity changes on selection/refresh; no disk access on each frame.
        source = self.cartoon_reader()
        if source is None:
            self._character_source = self._character_photo = self._character_key = None
            return None
        height = max(1, round((DETAIL_CARD_HEIGHT-COMPACT_CARD_HEIGHT-8)*ui.scale))
        key = (id(source),height)
        if self._character_key != key:
            # Very wide custom art must leave room for the copy on its left.
            ratio = min(height/source.height,120*ui.scale/source.width)
            image = source.resize((max(1,round(source.width*ratio)),max(1,round(source.height*ratio))),
                                  Image.Resampling.LANCZOS)
            self._character_photo = ImageTk.PhotoImage(image,master=ui.root)
            self._character_source = source
            self._character_key = key
        return self._character_photo

    def draw(self, ui, color):
        width = ui._size[0]
        item = ui.snapshot.get("next_reminder")
        enabled = ui.snapshot.get("preferences",{}).get("task_monitor_enabled",True)
        character = self._character(ui) if (ui.details or not item) else None
        art_width = character.width()/ui.scale if character else 0
        ui._text(26,89,"忙完了，给自己一点空闲。",font="title",width=width-52)
        if ui.snapshot.get("task_data_error"):
            ui._text(26,128,"暂时无法读取提醒计划",width=width-52)
            ui._text(26,160,"可以在控制面板检查任务数据。",font="small",fill="#9eafc7",width=width-52)
        elif not enabled:
            ui._text(26,128,"任务提醒已关闭",width=width-52)
            ui._text(26,160,"开启后，下一件小事会在这里等你。",font="small",fill="#9eafc7",width=width-52)
        elif item:
            count = preview_count(ui.snapshot)
            ui._text(26,121,"未来 1 小时 · 最近一条",font="small",fill="#9eafc7")
            if count > 1:
                ui._text(width-26,121,f"另有 {count-1} 项",font="small",fill="#9eafc7",anchor="e",width=110)
            ui._text(26,148,item["task"]["title"],font="title",width=width-52)
            label = "再次提醒" if item.get("snoozed") else "开始提醒"
            ui._text(26,177,f'{label}  {stamp_label(item["remind_at"])}',fill="#9eafc7",width=width-52)
            for i,line in enumerate(self.summary_lines(ui)):
                ui._text(26,205+i*22,line,width=width-52)
        else:
            ui._text(width/2,145,"未来 1 小时暂无提醒",font="title",anchor="center",width=width-52)
            text_width = width-52-(art_width+16 if character and not ui.details else 0)
            center = 26+text_width/2
            ui._text(center,179,"安心享受这一刻。",anchor="center",fill="#9eafc7",width=text_width)
            ui._text(center,216,"想起什么，再去「任务」记下来。",font="small",anchor="center",fill="#9eafc7",width=text_width)
        if ui.details:
            message = repeat_label(item["task"]) if item and enabled else "把琐事交给提醒，把此刻留给自己。"
            ui._text(26,300,message,font="small",fill="#9eafc7",width=width-52-art_width-16)
        if character:
            height = DETAIL_CARD_HEIGHT if ui.details else COMPACT_CARD_HEIGHT
            ui.canvas.create_image(width-26,height-10,image=character,
                anchor="se",tags=("resident_idle_character",))
        return False
