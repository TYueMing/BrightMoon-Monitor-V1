"""Peer controls painted over the same wallpaper as the monitor panel."""
from tkinter import ttk
from ...phone.settings_surface import SettingsSurface
from ...UI.theme import blend

class PeerSurface(SettingsSurface):
    def __init__(self,parent,scale):
        super().__init__(parent,scale)
        self.buttons={};self.serial=0;self.focus=None;self.bg='#ffffff'
        self.canvas.configure(takefocus=True)
        self.canvas.bind('<Tab>',self.next_focus)
        self.canvas.bind('<Return>',self.activate)
        self.canvas.bind('<space>',self.activate)
    def button(self,x,y,width,text,command,group='content',accent=False):
        self.serial+=1;tag='peer_button_'+str(self.serial);c=self.canvas;s=self.scale
        self.buttons[tag]=(command,group,text)
        role='peer_accent' if accent else 'peer_button'
        c.create_line((x+15)*s,(y+16)*s,(x+width-15)*s,(y+16)*s,width=30*s,
                      capstyle='round',fill=self.color(role),tags=(tag,group,role))
        c.create_text((x+width/2)*s,(y+16)*s,text=text,fill=self.foreground,
                      font=('Microsoft YaHei UI',-round(11*s)),tags=(tag,group,'text'))
        c.tag_bind(tag,'<Button-1>',lambda e,t=tag:self.click(t))
        c.tag_bind(tag,'<Enter>',lambda e:c.configure(cursor='hand2'))
        c.tag_bind(tag,'<Leave>',lambda e:c.configure(cursor=''))
        return tag
    def click(self,tag):
        self.focus=tag;self.canvas.focus_set();self.mark_focus()
        if tag in self.buttons:self.buttons[tag][0]()
    def next_focus(self,event=None):
        keys=list(self.buttons)
        if keys:
            self.focus=keys[(keys.index(self.focus)+1)%len(keys)] if self.focus in keys else keys[0]
            self.mark_focus()
        return 'break'
    def mark_focus(self):
        self.canvas.delete('peer_focus')
        box=self.canvas.bbox(self.focus) if self.focus else None
        if box:self.canvas.create_rectangle(*box,outline=self.foreground,dash=(2,2),tags='peer_focus')
    def activate(self,event=None):
        if self.focus in self.buttons:self.buttons[self.focus][0]()
        return 'break'
    def card(self,x,y,width,height,group='content'):
        s=self.scale;c=self.canvas;r=12
        points=[x+r,y,x+width-r,y,x+width,y,x+width,y+r,x+width,y+height-r,
                x+width,y+height,x+width-r,y+height,x+r,y+height,x,y+height,x,y+height-r,x,y+r,x,y]
        c.create_polygon(*[v*s for v in points],smooth=True,fill='',outline=self.color('peer_edge'),
                         width=1,tags=(group,'peer_edge'))
    def color(self,role):
        return blend(self.bg,self.foreground,{'peer_button':.10,'peer_accent':.19,'peer_edge':.18}[role])
    def set_theme(self,background,foreground):
        self.bg=background;super().set_theme(background,foreground)
        for role in ('peer_button','peer_accent'):self.canvas.itemconfigure(role,fill=self.color(role))
        self.canvas.itemconfigure('peer_edge',outline=self.color('peer_edge'))
        style=ttk.Style(self.canvas)
        for name in ('Peer.TEntry','Peer.TCombobox'):
            style.configure(name,fieldbackground=background,foreground=foreground,padding=5,
                            bordercolor=self.color('peer_edge'),arrowcolor=foreground)
        for group in self.widgets.values():
            for widget in group:
                if widget.winfo_class()=='Text':widget.configure(bg=background,fg=foreground,insertbackground=foreground)
    def clear_group(self,group):
        self.buttons={k:v for k,v in self.buttons.items() if v[1]!=group}
        self.canvas.delete('peer_focus');super().clear_group(group)
