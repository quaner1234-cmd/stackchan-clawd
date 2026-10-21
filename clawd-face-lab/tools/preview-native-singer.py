"""Preview literal singer LVGL geometry; real LCD frames remain authoritative."""
import ast
import math
from pathlib import Path
import re
from PIL import Image,ImageDraw

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'device/upstream/firmware/main/hal/clawd_family_ui.h'
if not SOURCE.is_file():SOURCE=ROOT/'firmware/hal/clawd_family_ui.h'
scene=SOURCE.read_text(encoding='utf-8').split('void init_singer(',1)[1].split('void update_singer(',1)[0]
BG=(250,247,242)


def number(text,env):
    def read(node):
        if isinstance(node,ast.Constant) and isinstance(node.value,int):return node.value
        if isinstance(node,ast.Name):return env[node.id]
        if isinstance(node,ast.Subscript):return read(node.value)[read(node.slice)]
        if isinstance(node,ast.BinOp):
            a,b=read(node.left),read(node.right)
            if isinstance(node.op,ast.Add):return a+b
            if isinstance(node.op,ast.Sub):return a-b
            if isinstance(node.op,ast.Mult):return a*b
        raise ValueError(text)
    return read(ast.parse(text.strip(),mode='eval').body)


def frame(t,level,blink=False):
    image=Image.new('RGB',(320,240),BG);draw=ImageDraw.Draw(image)
    dy=int(2*math.sin(t*.006)) if level else 0
    groups={'panel':(0,0),'singer':(0,0),'singer_body':(0,dy),'singer_mouth':(174,148-(4+level//10)//2+dy)}
    env={'xs':[64,257,84],'ys':[109,95,65],'colors':[0xd3a1b3,0x7eacc0,0xd8b36d]}
    note_loop=False
    for line in scene.splitlines():
        if 'for(int i=0;i<3;i++) {' in line:note_loop=True;continue
        if note_loop and line.strip()=='}':note_loop=False
        if 'auto note=group' in line:continue
        match=re.search(r'block\(([^)]+)\)',line)
        if not match:continue
        args=match[1].split(',');parent=args[0]
        xs=re.search(r'for\(int x:\{([\d,]+)\}',line)
        loop=re.search(r'for\(int i=0;i<(\d+);',line)
        iterations=[{'x':int(x)} for x in xs[1].split(',')] if xs else [{'i':i} for i in range(int(loop[1]))] if loop else [{'i':i} for i in range(3)] if note_loop else [{}]
        for variables in iterations:
            current={**env,**variables}
            if parent=='note':
                i=current['i'];groups['note']=(env['xs'][i],env['ys'][i]-(int(3+3*math.sin(t*.003+i*2)) if level else 0))
            px,py,w,h,color=[number(a,current) for a in args[1:6]]
            radius=number(args[6],current) if len(args)>6 else 0
            if 'singer_smile[' in line and level:continue
            if 'singer_mouth=block' in line:
                if not level:continue
                h=4+level//10;py=148-h//2
            if 'singer_tongue=' in line:
                if not level or 4+level//10<9:continue
                py=level//10
            if 'singer_eyes[' in line and blink:py=124;h=3
            if 'singer_arm=' in line and level:py+=int(3*math.sin(t*.007))
            gx,gy=groups[parent];px+=gx;py+=gy
            rgb=((color>>16)&255,(color>>8)&255,color&255)
            if parent=='note':
                opacity=(180+int(65*math.sin(t*.003+current['i']))) if level else 60
                rgb=tuple(round(a+(b-a)*opacity/255) for a,b in zip(BG,rgb))
            draw.rounded_rectangle((px,py,px+w-1,py+h-1),radius=radius,fill=rgb)
    return image


out=ROOT/'device/captures';out.mkdir(exist_ok=True)
frames=[frame(t,round(40+35*math.sin(t*.008))) for t in range(0,2400,100)]
frames[0].save(out/'singer-source-preview.gif',save_all=True,append_images=frames[1:],duration=100,loop=0)
review=Image.new('RGB',(1280,240),BG)
for i,(t,level,blink) in enumerate([(0,0,False),(400,20,False),(880,85,False),(4900,65,True)]):
    review.paste(frame(t,level,blink),(i*320,0))
review.save(out/'singer-source-review.png')
print(out/'singer-source-review.png')
