"""One composited surface: translucent panel, opaque text and native input events."""
from dataclasses import dataclass
import math
import tkinter as tk

from PIL import Image, ImageDraw, ImageOps, ImageTk

from .app_icons import circular_icon
from .wallpapers import WallpaperThumbnails
from .panel_palette import panel_palette
from .cartoon_panel import CartoonPanel
from ..src.app_sources import ICON_ROOT, app_source
from ..src.source_config import SOURCE_IDS

INK, MUTED, BLUE = "#1c2940", "#697589", "#426de7"
CARD_WIDTH, CARD_HEIGHT, CARD_RADIUS = 748, 626, 6
CONTENT_LEFT, CONTENT_RIGHT = 180, 718
ISLAND_COLORS = ("#141d2c", "#334155", "#3f3c6d", "#255b51", "#724b5c", "#f1f5f9")


def compose_background(size, photo, card_box, transparency, scale=1, color="#ffffff"):
    """Composite only the panel fill. Foreground controls never inherit alpha."""
    base = ImageOps.fit(photo.convert("RGB"), size, method=Image.Resampling.LANCZOS) if photo else Image.new("RGB", size, "white")
    # Supersampling gives an accurate, antialiased 6 logical-pixel corner.
    factor = 3
    mask = Image.new("L", (size[0]*factor, size[1]*factor), 0)
    alpha = round(255 * (1-transparency/100)) if photo else 255
    ImageDraw.Draw(mask).rounded_rectangle(tuple(round(n*factor) for n in card_box),
        radius=round(CARD_RADIUS*scale*factor), fill=alpha)
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    return Image.composite(Image.new("RGB", size, color), base, mask)


@dataclass
class Control:
    key: str
    rect: tuple
    callback: object
    enabled: bool = True
    slider: bool = False
    minimum: int = 0
    maximum: int = 100
    value: int = 0


class SettingsPanel(tk.Canvas):
    def __init__(self, root, app):
        super().__init__(root, bg="white", highlightthickness=0, takefocus=True)
        self.app, self.scale = app, app.scale
        self.controls = {}
        self.active_tab = "monitor"
        self.personalization_tab = "appearance"
        self.rope_page = 0
        self.rope_images = {}
        self.cartoon_panel = CartoonPanel()
        self.palette = {}
        self.focus_key = self.drag_key = self.press_key = ""
        self.origin = (0, 24*self.scale)
        self.background_key = None
        self.background_tk = None
        self.background_image = None
        self.thumbnails = WallpaperThumbnails(self, app.store)
        self.history_images = []
        self.icons = {}
        for key in SOURCE_IDS:
            self.icons[key] = ImageTk.PhotoImage(circular_icon(app_source(key).icon,round(36*self.scale)),master=root)
        self.icons["app"] = ImageTk.PhotoImage(circular_icon(ICON_ROOT/"MonitorSolo_logo.png",round(44*self.scale)),master=root)
        self.icons["solo_task"] = ImageTk.PhotoImage(circular_icon(ICON_ROOT/"LOGO_EXE.png",round(36*self.scale)),master=root)
        self.bind("<Configure>", lambda _: self.draw())
        self.bind("<ButtonPress-1>", self.press)
        self.bind("<B1-Motion>", self.drag)
        self.bind("<ButtonRelease-1>", self.release)
        self.bind("<Motion>", self.hover)
        self.bind("<Tab>", self.tab)
        self.bind("<Shift-Tab>", lambda event:self.tab(event,-1))
        self.bind("<ISO_Left_Tab>", lambda event:self.tab(event,-1))
        for key in ("Left","Right","Up","Down","Home","End","space","Return"):
            self.bind("<"+key+">",self.key)
        self.bind("<FocusIn>",lambda _:self.draw())
        self.bind("<FocusOut>",lambda _:self.draw())

    def point(self,x,y):
        return self.origin[0]+x*self.scale, self.origin[1]+y*self.scale

    def text(self,x,y,value,size=13,color=INK,bold=False,anchor="w",family="Microsoft YaHei UI",italic=False,**kwargs):
        style = ("bold" if bold else "normal") + (" italic" if italic else "")
        return self.create_text(*self.point(x,y),text=value,fill=self.palette.get(color,color),anchor=anchor,
            font=(family,-round(size*self.scale),style),tags="front",**kwargs)

    def line(self,x1,y1,x2,y2,**kwargs):
        if "fill" in kwargs:
            kwargs["fill"] = self.palette.get(kwargs["fill"], kwargs["fill"])
        return self.create_line(*self.point(x1,y1),*self.point(x2,y2),tags="front",**kwargs)

    def rounded_frame(self, rect, radius=8, **kwargs):
        """Draw card and focus outlines with the same logical-pixel corners."""
        x1,y1,x2,y2 = rect
        radius = min(radius,(x2-x1)/2,(y2-y1)/2)
        points = []
        for cx,cy,start in ((x2-radius,y1+radius,-90),(x2-radius,y2-radius,0),
                            (x1+radius,y2-radius,90),(x1+radius,y1+radius,180)):
            for step in range(9):
                angle = math.radians(start+step*11.25)
                points.extend(self.point(cx+radius*math.cos(angle),cy+radius*math.sin(angle)))
        return self.create_polygon(points,smooth=True,splinesteps=4,tags="front",**kwargs)

    def control(self,key,rect,callback,enabled=True,**kwargs):
        self.controls[key] = Control(key,rect,callback,enabled,**kwargs)
        if self.focus_key == key and self.focus_get() == self:
            x1,y1,x2,y2 = rect
            if key.startswith(("rope:","cartoon:")):
                self.rounded_frame((x1-3,y1-3,x2+3,y2+3),radius=11,fill="",
                    outline=self.palette.get(BLUE,BLUE),dash=(2,2))
            else:
                self.create_rectangle(*self.point(x1-3,y1-3),*self.point(x2+3,y2+3),
                    outline=self.palette.get(BLUE, BLUE),dash=(2,2),tags="front")

    def button(self,key,x,y,width,label,callback,accent=False):
        fill = "#eaf0ff" if accent else "#f0f2f6"
        self.line(x+12,y+15,x+width-12,y+15,fill=fill,width=28*self.scale,capstyle="round")
        self.text(x+width/2,y+15,label,11,BLUE if accent else INK,anchor="center")
        self.control(key,(x,y,x+width,y+30),callback)

    def toggle(self,key,x,y,value,callback,enabled=True):
        fill = "#e2e6ed" if not enabled else BLUE if value else "#ccd3df"
        self.line(x+14,y+15,x+34,y+15,fill=fill,width=26*self.scale,capstyle="round")
        cx = x+(34 if value else 14)
        self.create_oval(*self.point(cx-10,y+5),*self.point(cx+10,y+25),fill="white",outline="",tags="front")
        self.control(key,(x,y,x+48,y+30),lambda:callback(not value),enabled)

    def slider(self,key,y,value,minimum,maximum,callback,enabled=True):
        left,right = CONTENT_LEFT,CONTENT_RIGHT
        knob = left+(right-left)*(value-minimum)/(maximum-minimum)
        self.line(left,y,right,y,fill="#cbd3df",width=4*self.scale,capstyle="round")
        self.line(left,y,knob,y,fill=BLUE if enabled else "#b7c0d0",width=4*self.scale,capstyle="round")
        if maximum-minimum <= 10:
            for i in range(minimum,maximum+1):
                x = left+(right-left)*(i-minimum)/(maximum-minimum)
                self.create_oval(*self.point(x-2,y-2),*self.point(x+2,y+2),fill=BLUE if i<=value else "#abb5c5",outline="",tags="front")
        self.create_oval(*self.point(knob-8,y-8),*self.point(knob+8,y+8),fill="white",outline=self.palette.get(BLUE,BLUE) if enabled else "#b7c0d0",width=2*self.scale,tags="front")
        self.control(key,(left,y-14,right,y+14),callback,enabled,slider=True,minimum=minimum,maximum=maximum,value=value)

    def select_tab(self, name):
        if name not in {"monitor", "tasks", "personalization", "remote"}:
            return
        self.active_tab = name
        self.focus_key = "tab:"+name
        self.drag_key = self.press_key = ""
        self.draw()

    def select_personalization(self, name):
        self.personalization_tab = name
        self.focus_key = "personalization:"+name
        self.drag_key = self.press_key = ""
        if name == "rope":
            self.app.load_rope()
        if name == "cartoon":
            self.app.load_cartoon()
        self.draw()

    def navigation(self):
        for name,label,y in (("monitor","监控",130),("tasks","任务",182),("personalization","个性化",486),("remote","远程连接",538)):
            selected = self.active_tab == name
            if selected:
                self.line(40,y+18,112,y+18,fill="#eaf0ff",width=36*self.scale,capstyle="round")
                self.line(25,y+10,25,y+26,fill=BLUE,width=3*self.scale,capstyle="round")
            self.text(76,y+18,label,14,BLUE if selected else MUTED,bold=selected,anchor="center")
            self.control("tab:"+name,(24,y,128,y+36),lambda n=name:self.select_tab(n))
        self.line(150,122,150,568,fill="#cbd3df",width=self.scale)

    def draw_monitor(self,prefs):
        self.text(CONTENT_LEFT,132,"灵动岛常驻",14,bold=True)
        self.toggle("island-resident",670,117,prefs["island_resident"],self.app.toggle_resident)
        self.text(CONTENT_LEFT,158,"无任务时也陪伴在桌面 · 任务退场后自动收回胶囊",11,MUTED)
        self.line(CONTENT_LEFT,178,CONTENT_RIGHT,178,fill="#cbd3df",width=self.scale)
        self.text(CONTENT_LEFT,198,"监控来源",14,bold=True)
        for index,key in enumerate(SOURCE_IDS):
            y = 238+index*47
            self.create_image(*self.point(198,y),image=self.icons[key],tags="front")
            self.text(232,y-9,app_source(key).name,14,bold=True)
            status,color = self.app.source_status.get(key,("未开启",MUTED))
            if key == "codeagent":
                status,color = "公司内部 Agent · 即将接入",MUTED
            self.text(232,y+15,status,11,color)
            self.toggle("source:"+key,670,y-15,prefs["monitor_sources"][key]["enabled"],
                lambda value,k=key:self.app.toggle_source(k,value),key!="codeagent")
            if key != "codeagent":
                self.button("settings:"+key,602,y-15,54,"设置",lambda k=key:self.app.source_dialog(k))
        self.create_image(*self.point(198,426),image=self.icons["solo_task"],tags="front")
        self.text(232,417,"任务提醒",14,bold=True)
        self.text(232,441,"按本地计划提醒" if prefs["task_monitor_enabled"] else "已关闭 · 保留任务和计划",11,MUTED)
        self.toggle("task-monitor",670,411,prefs["task_monitor_enabled"],self.app.toggle_task_monitor)
        self.line(CONTENT_LEFT,460,CONTENT_RIGHT,460,fill="#cbd3df",width=self.scale)
        self.text(CONTENT_LEFT,485,"Agent 结束后保留",14,bold=True)
        self.text(CONTENT_RIGHT,485,f'{prefs["monitor_hide_minutes"]} 分钟',13,BLUE,anchor="e")
        self.slider("delay",516,prefs["monitor_hide_minutes"],1,10,self.app.save_delay)
        self.text(CONTENT_LEFT,542,"1 分钟",11,MUTED)
        self.text(CONTENT_RIGHT,542,"10 分钟",11,MUTED,anchor="e")
        self.text(CONTENT_LEFT,566,"待办按自己的提醒时间收起",11,MUTED)

    def draw_personalization(self,prefs):
        for index,(name,label) in enumerate((("general","通用"),("appearance","外观"),("rope","悬挂卡通"),("cartoon","空闲角色"),("sounds","声音"))):
            self.button("personalization:"+name,CONTENT_LEFT+index*108,116,98,label,
                        lambda n=name:self.select_personalization(n),accent=self.personalization_tab==name)
        if self.personalization_tab == "general":
            self.text(CONTENT_LEFT,184,"开机自启",15,bold=True)
            self.toggle("startup-enabled",670,169,self.app.startup_enabled,self.app.toggle_startup,
                        enabled=self.app.startup.registry is not None)
            self.text(CONTENT_LEFT,220,self.app.startup_status,12,MUTED)
            self.text(CONTENT_LEFT,253,"开启后，登录 Windows 时自动在后台监看。",12,MUTED)
            self.line(CONTENT_LEFT,290,CONTENT_RIGHT,290,fill="#cbd3df",width=self.scale)
            self.text(CONTENT_LEFT,326,"关上面板，监看继续",15,bold=True)
            self.text(CONTENT_LEFT,366,"点击窗口 × 会收到右下角系统托盘，任务提醒继续运行。",12,MUTED)
            self.text(CONTENT_LEFT,400,"双击托盘或桌面快捷方式可重新打开面板。",12,MUTED)
            self.text(CONTENT_LEFT,434,"要完全退出，请右键托盘图标选择「退出」。",12,MUTED)
            self.button("exit-app",CONTENT_LEFT,492,140,"退出监控器",self.app.close)
            return
        if self.personalization_tab == "cartoon":
            self.cartoon_panel.draw(self,prefs)
            return
        if self.personalization_tab == "rope":
            self.draw_rope(prefs)
            return
        if self.personalization_tab == "sounds":
            from .tasks.page import draw_sounds
            draw_sounds(self,prefs)
            return
        self.text(CONTENT_LEFT,180,"应用背景",14,bold=True)
        self.button("photo",538,165,96,"选择照片…",self.app.choose_photo,accent=self.app.photo is not None)
        self.button("white",652,165,66,"纯白色",lambda:self.app.set_background("white"),accent=self.app.photo is None)
        # Keep a compact row of local thumbnails below the active wallpaper.
        # The full picker is available when more than the row can show.
        paths = self.app.wallpapers[:4]
        current = prefs.get("background_photo", "") if self.app.photo else ""
        self.history_images.clear()
        if paths:
            for index, relative in enumerate(paths):
                image_tk = self.thumbnails.get(relative, (round(68*self.scale), round(40*self.scale)))
                if image_tk is None:
                    continue
                self.history_images.append(image_tk)
                x = CONTENT_LEFT + 34 + index*80
                self.create_image(*self.point(x,222), image=image_tk, tags="front")
                if relative == current:
                    self.create_rectangle(*self.point(x-37,199), *self.point(x+37,245),
                                          outline=self.palette.get(BLUE,BLUE), width=2*self.scale, tags="front")
                self.control("wallpaper:"+relative, (x-36,199,x+36,245),
                             lambda value=relative:self.app.select_background(value))
        else:
            self.text(CONTENT_LEFT,222,"还没有历史壁纸",11,MUTED)
        self.button("wallpaper-history",542,207,176,f"历史壁纸 · {len(self.app.wallpapers)} 张",self.app.wallpaper_dialog)
        self.text(CONTENT_LEFT,270,"控制面板颜色",13,bold=True)
        self.create_oval(*self.point(304,260), *self.point(324,280), fill=prefs["panel_color"],
                         outline=self.palette.get("#cbd3df","#cbd3df"), tags="front")
        self.button("panel-color-reset",450,255,78,"恢复白色",lambda:self.app.save_panel_color("#ffffff"))
        self.button("panel-color-custom",542,255,176,"选择面板颜色…",self.app.choose_panel_color)
        self.text(CONTENT_LEFT,300,"面板透明度",13)
        self.text(CONTENT_RIGHT,300,f'{prefs["panel_transparency"]}%' if self.app.photo else "添加照片后生效",12,BLUE if self.app.photo else MUTED,anchor="e")
        self.slider("transparency",323,prefs["panel_transparency"],0,100,self.app.save_transparency,self.app.photo is not None)
        self.line(CONTENT_LEFT,342,CONTENT_RIGHT,342,fill="#cbd3df",width=self.scale)
        self.text(CONTENT_LEFT,363,"灵动岛背景",14,bold=True)
        photo_mode = prefs["monitor_background_mode"] == "photo"
        self.button("island-solid",542,348,66,"纯色",lambda:self.app.set_island_background("solid"),accent=not photo_mode)
        self.button("island-photo",622,348,96,"选择壁纸…",lambda:self.app.wallpaper_dialog("island"),accent=photo_mode)
        color = prefs["monitor_background_color"]
        for index,swatch in enumerate(ISLAND_COLORS):
            x,y = CONTENT_LEFT+14+index*40,402
            if color == swatch:
                self.create_oval(*self.point(x-17,y-17),*self.point(x+17,y+17),outline=self.palette.get(BLUE,BLUE),width=2*self.scale,tags="front")
            self.create_oval(*self.point(x-12,y-12),*self.point(x+12,y+12),fill=swatch,outline="#aeb9cb",tags="front")
            self.control("island-color:"+swatch,(x-15,y-15,x+15,y+15),lambda c=swatch:self.app.save_island_color(c))
        self.button("island-color-custom",542,387,176,"自定义颜色…",self.app.choose_island_color)
        self.text(CONTENT_LEFT,430,"壁纸从顶部展开 · 颜色用于衬底，保持文字清晰" if photo_mode else
                  "当前：纯色 · 也可选择本地壁纸",11,MUTED)
        self.text(CONTENT_LEFT,457,"灵动岛透明度",13)
        self.text(CONTENT_RIGHT,457,f'{100-prefs["monitor_opacity"]}%',12,BLUE,anchor="e")
        self.slider("island-transparency",483,100-prefs["monitor_opacity"],0,100,self.app.save_island_transparency)
        self.text(CONTENT_LEFT,503,"不透明",11,MUTED)
        self.text(CONTENT_RIGHT,503,"完全透明",11,MUTED,anchor="e")
        self.text(CONTENT_LEFT,524,"灵动岛当前完全透明，向左拖动可恢复显示。" if prefs["monitor_opacity"]==0 else
                  "三个形态共用透明度，选择会自动保存。",11,MUTED)

    def move_rope_page(self, delta):
        self.rope_page += delta
        self.draw()

    def draw_rope(self, prefs):
        from .rope_pendant import horizontal_center
        enabled = prefs["rope_enabled"]
        self.text(CONTENT_LEFT,184,"胶囊悬挂卡通",15,bold=True)
        self.toggle("rope-enabled",670,169,enabled,self.app.toggle_rope)
        self.text(CONTENT_LEFT,214,"随胶囊渐入 · 点击展开时轻拉两次 · 展开后收起",11,MUTED)
        self.rounded_frame((320,233,578,263),radius=15,fill=self.palette.get("#f0f2f6"),outline="")
        self.text(449,248,"灵动岛",11,anchor="center")
        if enabled and self.app.rope_photo is not None:
            key = (id(self.app.rope_photo),self.scale)
            if getattr(self,"_rope_preview_key",None) != key:
                preview = self.app.rope_photo.copy()
                preview.thumbnail((round(56*self.scale),round(46*self.scale)),Image.Resampling.LANCZOS)
                self._rope_preview_image = ImageTk.PhotoImage(preview,master=self)
                self._rope_preview_key = key
            image = self._rope_preview_image
            x = horizontal_center(320,258,image.width()/self.scale,1,prefs["rope_position"])
            self.create_image(*self.point(x,260),image=image,anchor="n",tags=("front","rope_position_preview"))
        self.text(CONTENT_LEFT,324,"卡通横向位置",12,bold=True)
        self.text(CONTENT_RIGHT,324,"居中" if prefs["rope_position"]==50 else f'{prefs["rope_position"]}%',11,BLUE,anchor="e")
        self.slider("rope-position",347,prefs["rope_position"],0,100,self.app.save_rope_position,enabled)
        items = self.app.rope_items
        self.rope_page = min(max(0,self.rope_page),max(0,(len(items)-1)//6))
        self.rope_images.clear()
        if not enabled:
            self.text(449,421,"开启后，选择你喜欢的悬挂卡通",15,MUTED,anchor="center")
            self.text(449,455,"仅在灵动岛显示任务时出现，不产生额外任务。",11,MUTED,anchor="center")
        elif not items:
            self.text(449,421,"还没有可用的 PNG 图像",15,MUTED,anchor="center")
            self.text(449,455,"把 PNG 放入 data/rope 后，点击刷新列表。",11,MUTED,anchor="center")
        else:
            for index, reference in enumerate(items[self.rope_page*6:self.rope_page*6+6]):
                x,y = CONTENT_LEFT+(index%3)*184,370+(index//3)*86
                selected = reference == prefs["rope_image"]
                fill = self.palette.get("#eaf0ff" if selected else "#f0f2f6")
                self.rounded_frame((x,y,x+170,y+78),radius=8,fill=fill,
                    outline=self.palette.get(BLUE,BLUE) if selected else "",width=2*self.scale)
                try:
                    source = self.app.rope_catalog.read(reference)
                    cache_key = (id(source),self.scale)
                    # Keep Tk objects for the current page only, without resizing
                    # the files on every periodic settings repaint.
                    cache = getattr(self,"_rope_thumb_cache",{})
                    image_tk = cache.get(cache_key)
                    if image_tk is None:
                        image = source.copy()
                        image.thumbnail((round(130*self.scale),round(48*self.scale)),Image.Resampling.LANCZOS)
                        image_tk = ImageTk.PhotoImage(image,master=self)
                    self.rope_images[cache_key] = image_tk
                    self.create_image(*self.point(x+85,y+28),image=image_tk,tags="front")
                except (OSError,ValueError,RuntimeError,Image.DecompressionBombError):
                    self.text(x+85,y+34,"文件不可用",11,MUTED,anchor="center")
                name = self.app.rope_catalog.label(reference)
                label = name if len(name)<=20 else name[:19]+"…"
                self.text(x+85,y+64,label,11,BLUE if selected else INK,bold=selected,anchor="center")
                self.control("rope:"+reference,(x,y,x+170,y+78),lambda value=reference:self.app.select_rope(value))
        self._rope_thumb_cache = dict(self.rope_images)
        if enabled and len(items)>6:
            if self.rope_page>0:
                self.button("rope-previous",CONTENT_LEFT,554,76,"上一页",lambda:self.move_rope_page(-1))
            if (self.rope_page+1)*6<len(items):
                self.button("rope-next",274,554,76,"下一页",lambda:self.move_rope_page(1))
            self.text(375,569,f"{self.rope_page+1} / {(len(items)+5)//6}",11,MUTED)
        else:
            self.text(CONTENT_LEFT,569,f"{len(items)} 个本地卡通 · 选择自动保存",11,MUTED)
        self.button("rope-refresh",608,554,110,"刷新列表",self.app.load_rope)

    def draw(self):
        if self.app.closed:
            return
        prefs = self.app.store.snapshot()
        w,h = max(1,self.winfo_width()),max(1,self.winfo_height())
        # Center the complete control card in both axes.  The canvas itself
        # can be larger than the card on high-DPI screens, so clamp only when
        # a very small window leaves less than a 10px breathing room.
        card_width, card_height = CARD_WIDTH*self.scale, CARD_HEIGHT*self.scale
        self.origin = (max(10*self.scale,(w-card_width)/2),
                       max(10*self.scale,(h-card_height)/2))
        box = (*self.origin,self.origin[0]+CARD_WIDTH*self.scale,self.origin[1]+CARD_HEIGHT*self.scale)
        key = (w,h,id(self.app.photo),prefs["panel_transparency"],prefs["panel_color"],self.scale)
        if self.background_key != key:
            self.background_image = compose_background((w,h),self.app.photo,box,prefs["panel_transparency"],self.scale,prefs["panel_color"])
            self.palette = panel_palette(self.background_image,box)
            self.background_tk = ImageTk.PhotoImage(self.background_image,master=self)
            self.delete("background")
            self.create_image(0,0,anchor="nw",image=self.background_tk,tags="background")
            self.tag_lower("background")
            self.background_key = key
        self.delete("front")
        self.controls.clear()
        self.create_image(*self.point(52,49),image=self.icons["app"],tags="front")
        self.text(90,42,"Bright Moon Monitor",28,family="Georgia",italic=True)
        self.text(91,72,"你的 Agent，抬眼可见。",11,MUTED)
        self.navigation()
        if self.app.phone_dialog and self.active_tab != "remote":
            self.app.phone_dialog.window.place_forget()
        if self.active_tab == "remote":
            if not self.app.phone_dialog:
                from ..phone.settings_panel import PhoneSettings
                self.app.phone_dialog = PhoneSettings(self.app, self)
            x,y = self.point(CONTENT_LEFT,112)
            self.app.phone_dialog.layout(x,y,(CONTENT_RIGHT-CONTENT_LEFT)*self.scale,464*self.scale,
                                        prefs["panel_color"], self.palette.get(INK,INK), self.background_image)
        elif self.active_tab == "monitor":
            self.draw_monitor(prefs)
        elif self.active_tab == "tasks":
            from .tasks.page import draw_tasks
            draw_tasks(self)
        else:
            self.draw_personalization(prefs)
        self.text(30,598,self.app.notice_text,11,self.app.notice_color)
        self.create_text(w/2,h-23*self.scale,text="Tao Yueming",fill="#808080",
            font=("Segoe UI",-round(12*self.scale)),tags="front")

    def hit(self,event):
        x,y = (event.x-self.origin[0])/self.scale,(event.y-self.origin[1])/self.scale
        return next((c for c in self.controls.values() if c.enabled and c.rect[0]-5 <= x <= c.rect[2]+5
                     and c.rect[1] <= y <= c.rect[3]),None)

    def set_slider(self,control,x):
        position = ((x-self.origin[0])/self.scale-control.rect[0])/(control.rect[2]-control.rect[0])
        value = round(control.minimum+min(1,max(0,position))*(control.maximum-control.minimum))
        if value != control.value:
            control.callback(value)

    def press(self,event):
        self.focus_set()
        control = self.hit(event)
        self.press_key = control.key if control else ""
        self.focus_key = self.press_key
        if control and control.slider:
            self.drag_key = control.key
            self.set_slider(control,event.x)
        self.draw()

    def drag(self,event):
        control = self.controls.get(self.drag_key)
        if control:
            self.set_slider(control,event.x)

    def release(self,event):
        control = self.hit(event)
        if control and not self.drag_key and control.key == self.press_key:
            control.callback()
        self.drag_key = self.press_key = ""
        self.draw()

    def hover(self,event):
        self.configure(cursor="hand2" if self.hit(event) else "")

    def tab(self,event=None,direction=1):
        keys = [c.key for c in self.controls.values() if c.enabled]
        if keys:
            index = keys.index(self.focus_key) if self.focus_key in keys else (-1 if direction==1 else 0)
            self.focus_key = keys[(index+direction)%len(keys)]
            self.draw()
        return "break"

    def key(self,event):
        control = self.controls.get(self.focus_key)
        if control and control.enabled:
            if control.slider:
                value = (control.minimum if event.keysym=="Home" else control.maximum if event.keysym=="End" else
                    min(control.maximum,max(control.minimum,control.value+(1 if event.keysym in {"Right","Up"} else -1))))
                if event.keysym in {"Home","End","Left","Right","Up","Down"}:
                    control.callback(value)
            elif event.keysym in {"space","Return"}:
                control.callback()
            self.draw()
        return "break"
