"""Render real robot LCD RGB565 captures, never camera photographs."""
from array import array
from pathlib import Path
from PIL import Image

root=Path(__file__).resolve().parents[1]/'device/captures'
for kind in ('love','drink'):
    frames=[]
    for i in range(4):
        path=root/f'{kind}-native-{i}.rgb565'
        raw=path.read_bytes()
        if len(raw)!=320*240*2:raise ValueError('Incomplete LCD capture')
        pixels=bytearray()
        for v in array('H',raw):
            pixels.extend(((v>>11)*255//31,((v>>5)&63)*255//63,(v&31)*255//31))
        frame=Image.frombytes('RGB',(320,240),bytes(pixels))
        frame.save(path.with_suffix('.png'));frames.append(frame)
    frames[0].save(root/f'{kind}-native-preview.gif',save_all=True,append_images=frames[1:],duration=1600,loop=0)
review=Image.new('RGB',(640,480),'#faf7f2')
for row,kind in enumerate(('love','drink')):
    for col,i in enumerate((0,2)):review.paste(Image.open(root/f'{kind}-native-{i}.png'),(col*320,row*240))
review.save(root/'idle-scenes-native-review.png')
print(root/'idle-scenes-native-review.png')
