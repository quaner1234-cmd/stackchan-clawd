"""Local geometry preview read from the native LVGL work-scene declarations.

This does not replace the LCD capture check. No reference image is modified.
"""
from pathlib import Path
import re
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'device/upstream/firmware/main/hal/clawd_family_ui.h').read_text(encoding='utf-8')
scene = source.split('work=block(panel,', 1)[1].split('visible(work,false)', 1)[0]


def value(expression, x=0, i=0):
    if expression == 'x': return x
    if expression.isdigit(): return int(expression)
    match = re.fullmatch(r'(\d+)([+-])i\*(\d+)', expression)
    if not match: raise ValueError(expression)
    start, sign, step = match.groups()
    return int(start) + (1 if sign == '+' else -1)*i*int(step)


def frame(phase):
    image = Image.new('RGB', (320,240), '#faf7f2')
    draw = ImageDraw.Draw(image)
    for line in scene.splitlines():
        if 'block(work,' not in line: continue
        args = line.split('block(work,',1)[1].split(')',1)[0].split(',')
        xs = re.search(r'for\(int x:\{([\d,]+)\}',line)
        count = re.search(r'for\(int i=0;i<(\d+);',line)
        iterations = [(int(x),0) for x in xs[1].split(',')] if xs else [(0,i) for i in range(int(count[1]))] if count else [(0,0)]
        for x,i in iterations:
            px,py,w,h = [value(arg,x,i) for arg in args[:4]]
            color = int(args[4],0)
            arm = re.search(r'arms\[(\d)\]',line)
            if arm:
                side = 'upper' if arm[1]=='0' else 'lower'
                py = int(re.search(side+r'_y\[\]=\{([^}]+)',source)[1].split(',')[phase])
                w = int(re.search(side+r'_w\[\]=\{([^}]+)',source)[1].split(',')[phase])
            if 'keys[i]' in line and i==phase: color=0xe2d7c6
            radius=int(args[5]) if len(args)>5 else 0
            draw.rounded_rectangle((px,py,px+w-1,py+h-1),radius=radius,fill=f'#{color:06x}')
    return image


output = ROOT/'device/captures'
frames = [frame(i) for i in range(4)]
frames[0].save(output/'xiaoke-source-preview.gif',save_all=True,append_images=frames[1:],duration=180,loop=0)
review=Image.new('RGB',(640,520),'#eee8df')
draw=ImageDraw.Draw(review)
for i,image in enumerate(frames):
    x=i%2*320;y=i//2*260
    draw.text((x+8,y+4),'Source geometry phase '+str(i),fill='black')
    review.paste(image,(x,y+20))
review.save(output/'xiaoke-source-review.png')
print(output/'xiaoke-source-review.png')
