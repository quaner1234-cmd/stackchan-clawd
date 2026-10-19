"""Geometry sketch of the native scenes; real LCD captures remain the acceptance source."""
import math
from pathlib import Path
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'device/captures';OUT.mkdir(exist_ok=True)
BG='#faf7f2'


def frame(kind,t):
    image=Image.new('RGB',(320,240),BG);draw=ImageDraw.Draw(image)
    def block(x,y,w,h,c,r=0):draw.rounded_rectangle((x,y,x+w-1,y+h-1),r,fill=c)
    def heart(x,y,u,c):
        for row,bits in enumerate([0x66,0xff,0xff,0x7e,0x3c,0x18,0x18]):
            for col in range(8):
                if bits&(0x80>>col):block(x+col*u,y+row*u,u,u,c)
    love=kind=='love';dy=int(2*math.sin(t*.004)) if love else 0
    block(78,187,165,5,'#e8e0d5',3)
    block(111,105+dy,98,64,'#d97757');block(111,105+dy,22,64,'#ba6249')
    for x in [111,137,172,198]:block(x,163+dy,10,17,'#d97757')
    for x in [105,131,166,192]:block(x,177+dy,12,11,'#d97757')
    block(111,163+dy,10,17,'#ba6249');block(105,177+dy,12,11,'#ba6249')
    for x in [141,196]:block(x,139+dy,12,4,'#eca28b',2)
    block(174,144+dy,3,4,'#633c32');block(177,148+dy,9,3,'#633c32');block(186,144+dy,3,4,'#633c32')
    if love:
        for x in [144,190]:heart(x,119+dy,2,'#633247')
        block(97,132+int(4*math.sin(t*.004))+dy,20,10,'#ba6249')
        block(204,137-int(5*math.sin(t*.004))+dy,24,11,'#d97757')
        heart(71,112-int(5*(1+math.sin(t*.003)))+dy,4,'#e77f95')
        for i in range(6):
            p=((t+i*530)%3400)/3400
            opacity=max(0,220*math.sin(p*math.pi)/255)
            color='#d99ab0' if i%2 else '#e77f95'
            rgb=tuple(round(a*(1-opacity)+b*opacity) for a,b in zip((250,247,242),tuple(bytes.fromhex(color[1:]))))
            heart(38+i*44+int(5*math.sin(p*6.28+i)),162-int(p*135),3 if i%3==0 else 2,rgb)
        for b in range(2):
            p=((t+b*1100)%2600)/2600
            if p<.72:
                for i in range(8):
                    a=i*.78539816
                    block((268 if b else 53)+int((5+28*p)*math.cos(a)),(69 if b else 65)+int((5+28*p)*math.sin(a)),3,3,'#e9bc68' if i%2 else '#e7a0ac',1)
    else:
        for x in [149,198]:block(x,121,10,3 if t%3800>3550 else 10,'#282522')
        lift=int(15*(.5-.5*math.cos((t%4000)*.0015707963)))
        block(97,151,20,10,'#ba6249');block(204,166-lift,24,11,'#d97757')
        x,y=205-lift//4,145-lift
        block(x+26,y+8,16,23,'#64a8c9',7);block(x+29,y+12,9,14,BG,3)
        block(x,y+3,31,32,'#76b9d8',3);block(x+3,y+7,5,22,'#bde0ed',2)
        block(x,y+1,31,5,'#ddeef4',2);block(x+4,y+2,23,2,'#4d91b6',1);heart(x+12,y+15,1,'#f6f3ea')
        for i in range(3):
            p=((t+i*900)%3000)/3000;x,y=241+i*15,117-int(62*p)
            block(x+4,y,4,5,'#76b9d8',1);block(x+2,y+4,8,6,'#76b9d8',2)
            block(x,y+8,12,9,'#76b9d8',6);block(x+3,y+9,2,4,'#ddeef4',1)
    return image


for kind in ('love','drink'):
    frames=[frame(kind,t) for t in range(0,4000,160)]
    frames[0].save(OUT/f'{kind}-geometry-preview.gif',save_all=True,append_images=frames[1:],duration=160,loop=0)
review=Image.new('RGB',(640,480),BG)
for row,kind in enumerate(('love','drink')):
    for col,t in enumerate((640,1920)):review.paste(frame(kind,t),(col*320,row*240))
review.save(OUT/'idle-scenes-geometry-review.png')
print(OUT/'idle-scenes-geometry-review.png')
