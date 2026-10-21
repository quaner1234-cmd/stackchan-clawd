"""Decode only this task's real LCD snapshots, never camera photographs."""
import json
from pathlib import Path
from PIL import Image

out=Path(__file__).resolve().parents[1]/'device/captures'
names=['singer-quiet-native','singer-singing-native','singer-back-to-listening-native','singer-normal-chat-native']
frames=[]
for name in names:
    meta=json.loads((out/f'{name}.json').read_text(encoding='utf-8'))
    assert (meta['width'],meta['height'])==(320,240)
    raw=(out/f'{name}.rgb565').read_bytes();assert len(raw)==meta['bytes']
    pixels=bytearray()
    for y in range(240):
        for x in range(320):
            p=y*meta['stride']+x*2;v=raw[p]|raw[p+1]<<8
            pixels.extend(((v>>11)*255//31,((v>>5)&63)*255//63,(v&31)*255//31))
    frame=Image.frombytes('RGB',(320,240),bytes(pixels));frame.save(out/f'{name}.png');frames.append(frame)
review=Image.new('RGB',(640,480),'#faf7f2')
for i,frame in enumerate(frames):review.paste(frame,((i%2)*320,(i//2)*240))
review.save(out/'singer-native-review.png')
print(out/'singer-native-review.png')
