"""Generate one fixed morning phrase, then reuse it locally for every alarm."""
import hashlib
import json
import math
from pathlib import Path
import struct
import wave
from providers import Cloud
from workday_alarm import PHRASE

root=Path(__file__).resolve().parent;target=root/'assets/workday-alarm.wav'
if target.exists():raise SystemExit('Existing alarm audio retained; no API call.')
cloud=Cloud(max_calls=1)
speech=cloud.synthesize(PHRASE)
tone=[]
for i in range(14400):
    part=i//7200;t=i%7200/24000
    envelope=min(1,t/.015)*max(0,1-t/.3)**2
    tone.append(int(6000*envelope*math.sin(2*math.pi*(523.25 if part==0 else 659.25)*t)))
pcm=struct.pack('<'+'h'*len(tone),*tone)+b'\0'*12000+speech
if not 48000<len(pcm)<24000*12*2:raise ValueError('Unexpected alarm duration')
target.parent.mkdir(exist_ok=True)
with wave.open(str(target),'wb') as wav:
    wav.setparams((1,2,24000,0,'NONE','not compressed'));wav.writeframes(pcm)
receipt=dict(phrase=PHRASE,model='qwen-audio-3.1-tts-flash',voice='longanhuan_v3.1',
 calls=cloud.calls,seconds=len(pcm)/48000,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
 runtime_cloud_calls=0,ledger='../usage.jsonl')
(target.with_suffix('.json')).write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({k:v for k,v in receipt.items() if k!='phrase'}))
