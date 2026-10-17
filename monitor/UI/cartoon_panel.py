"""Personalization picker for idle-card artwork, separate from capsule pendants."""
from PIL import Image, ImageTk


class CartoonPanel:
    def __init__(self):
        self.page = 0
        self.images = {}

    def move(self, panel, delta):
        self.page += delta
        panel.draw()

    def draw(self, panel, prefs):
        from .settings_panel import CONTENT_LEFT, INK, MUTED, BLUE
        app = panel.app
        panel.text(CONTENT_LEFT,184,"空闲卡片角色",15,bold=True)
        panel.toggle("cartoon-enabled",670,169,prefs["cartoon_enabled"],app.toggle_cartoon)
        panel.text(CONTENT_LEFT,217,"未来1小时无提醒时第二态显示；有提醒预告时第三态显示。",11,MUTED)
        panel.text(CONTENT_LEFT,242,"请使用已抠图、透明背景的 PNG / WebP，上传后保存到本地。",11,MUTED)
        panel.button("cartoon-upload",CONTENT_LEFT,266,110,"上传自定义…",app.choose_cartoon)
        panel.button("cartoon-refresh",608,266,110,"刷新目录",app.load_cartoon)
        items = app.cartoon_items
        self.page = min(max(0,self.page),max(0,(len(items)-1)//6))
        images = {}
        if not items:
            panel.text(449,405,"还没有可用的透明背景角色",15,MUTED,anchor="center")
            panel.text(449,439,"上传图像，或放入 data/cartoon 后刷新。",11,MUTED,anchor="center")
        for index,reference in enumerate(items[self.page*6:self.page*6+6]):
            x,y = CONTENT_LEFT+(index%3)*184,316+(index//3)*111
            selected = reference==prefs["cartoon_image"]
            panel.rounded_frame((x,y,x+170,y+102),radius=8,
                fill=panel.palette.get("#eaf0ff" if selected else "#f0f2f6"),
                outline=panel.palette.get(BLUE,BLUE) if selected else "",width=2*panel.scale)
            try:
                source = app.cartoon_catalog.read(reference)
                key = (id(source),panel.scale)
                photo = self.images.get(key)
                if photo is None:
                    image = source.copy()
                    image.thumbnail((round(140*panel.scale),round(70*panel.scale)),Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image,master=panel)
                images[key] = photo
                panel.create_image(*panel.point(x+85,y+39),image=photo,tags=("front","cartoon_preview"))
            except (OSError,ValueError,RuntimeError,Image.DecompressionBombError):
                panel.text(x+85,y+39,"文件不可用",11,MUTED,anchor="center")
            label = app.cartoon_catalog.label(reference)
            panel.text(x+85,y+87,label if len(label)<=18 else label[:17]+"…",11,
                BLUE if selected else INK,bold=selected,anchor="center")
            panel.control("cartoon:"+reference,(x,y,x+170,y+102),
                lambda value=reference:app.select_cartoon(value))
        self.images = images
        if self.page:
            panel.button("cartoon-previous",CONTENT_LEFT,554,76,"上一页",lambda:self.move(panel,-1))
        if (self.page+1)*6<len(items):
            panel.button("cartoon-next",274,554,76,"下一页",lambda:self.move(panel,1))
        panel.text(718,569,f"{len(items)} 个角色 · {self.page+1}/{max(1,(len(items)+5)//6)} 页 · 自动保存",11,MUTED,anchor="e")
