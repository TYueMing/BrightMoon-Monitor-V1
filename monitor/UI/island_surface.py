"""One borderless island surface and smoothly composited status light."""
import math

from PIL import Image, ImageDraw


class IslandSurface:
    def __init__(self):
        self.solid_key = self.solid_frame = None
        self.halo_key = self.halo_frame = self.halo_base = None
        self.masks = {}

    def solid(self, size, radius, color):
        key = (size,radius,color)
        if key != self.solid_key:
            frame = Image.new("RGBA",size,color)
            mask = Image.new("L",size,0)
            ImageDraw.Draw(mask).rounded_rectangle((0,0,size[0]-1,size[1]-1),radius=radius,fill=255)
            frame.putalpha(mask)
            self.solid_frame,self.solid_key = frame,key
        return self.solid_frame

    def glow_mask(self, radius, scale):
        key = (radius,scale)
        if key not in self.masks:
            extent = math.ceil((radius+12)*scale)
            size = extent*2+1
            values = []
            for y in range(size):
                for x in range(size):
                    distance = math.hypot(x-extent,y-extent)/scale
                    # A continuous radial falloff replaces separated outline
                    # circles. Its edge reaches zero before the tile boundary.
                    values.append(0 if distance>=radius+12 else
                                  round(255*math.exp(-.5*((distance-radius)/3.5)**2)))
            mask = Image.new("L",(size,size))
            mask.putdata(values)
            if len(self.masks)>=8:
                self.masks.clear()
            self.masks[key] = mask
        return self.masks[key]

    def halos(self, base, requests, scale):
        if not requests:
            self.halo_key = self.halo_frame = self.halo_base = None
            return None
        # Reuse a resting frame; only the small header band is composited as
        # focus/breathing changes, even when an approval card is very tall.
        requests = tuple((round(x,3),round(y,3),radius,color,round(strength,3))
                         for x,y,radius,color,strength in requests)
        key = (id(base),requests,scale)
        if key == self.halo_key:
            return self.halo_frame
        self.halo_base = base
        frame = base.crop((0,0,base.width,min(base.height,round(60*scale))))
        silhouette = frame.getchannel("A")
        for x,y,radius,color,strength in requests:
            mask = self.glow_mask(radius,scale)
            glow = Image.new("RGBA",mask.size,color)
            glow.putalpha(mask.point(lambda value:round(value*.28*strength)))
            extent = mask.width//2
            frame.alpha_composite(glow,(round(x*scale)-extent,round(y*scale)-extent))
        # Clip to the same body silhouette, including the rounded capsule ends.
        # Blend over the actual wallpaper pixels rather than a flat substitute.
        frame.putalpha(silhouette)
        self.halo_key,self.halo_frame = key,frame
        return frame
