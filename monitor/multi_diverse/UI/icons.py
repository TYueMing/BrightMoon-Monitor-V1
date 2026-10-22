"""Rasterize the two supplied simple outline SVGs at native DPI; no web engine."""
import re
import xml.etree.ElementTree as ET
from PIL import Image,ImageDraw,ImageTk
from ...src.app_sources import ICON_ROOT

class DeviceIcons:
    def __init__(self,master):self.master=master;self.cache={}
    def get(self,kind,size,color):
        key=(kind,int(size),color)
        if key not in self.cache:
            name='苹果手机_iphone.svg' if kind=='phone' else '设备_devices.svg'
            svg=ET.parse(ICON_ROOT/name).getroot();factor=int(size)*4/48
            image=Image.new('RGBA',(int(size)*4,int(size)*4));d=ImageDraw.Draw(image)
            for e in svg:
                tag=e.tag.split('}')[-1];stroke=max(1,round(float(e.get('stroke-width','4'))*factor))
                if tag=='rect':
                    x,y,w,h=[float(e.get(k,'0'))*factor for k in ('x','y','width','height')]
                    d.rounded_rectangle((x,y,x+w,y+h),radius=float(e.get('rx','0'))*factor,outline=color,width=stroke)
                elif tag=='path':
                    tokens=re.findall(r'[MLHVZmlhvz]|-?\d+(?:\.\d+)?',e.get('d',''));points=[];x=y=0.;i=0;cmd='M'
                    while i<len(tokens):
                        if tokens[i].isalpha():cmd=tokens[i].upper();i+=1
                        if cmd=='Z':
                            if points:points.append(points[0])
                            cmd='M';continue
                        if cmd in ('M','L'):x,y=float(tokens[i]),float(tokens[i+1]);i+=2
                        elif cmd=='H':x=float(tokens[i]);i+=1
                        elif cmd=='V':y=float(tokens[i]);i+=1
                        points.append((x*factor,y*factor))
                    if len(points)>1:d.line(points,fill=color,width=stroke,joint='curve')
            self.cache[key]=ImageTk.PhotoImage(image.resize((int(size),int(size)),Image.Resampling.LANCZOS),master=self.master)
        return self.cache[key]
