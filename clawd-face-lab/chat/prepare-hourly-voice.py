"""Prepare bounded, resumable cached time phrases through the existing provider."""
import hashlib
import json
from pathlib import Path
import wave
from providers import Cloud
from daily_routines import hour_phrase

root=Path(__file__).resolve().parent
folder=root/'assets/hourly';folder.mkdir(parents=True,exist_ok=True)
cloud=Cloud(max_calls=24)
for hour in range(24):
    target=folder/f'{hour:02d}.wav'
    if target.exists():continue
    phrase=hour_phrase(hour)
    pcm=cloud.synthesize(phrase)
    if not 12000<len(pcm)<24000*10*2:raise ValueError('Unexpected hourly audio')
    with wave.open(str(target),'wb') as wav:
        wav.setparams((1,2,24000,0,'NONE','not compressed'));wav.writeframes(pcm)
    receipts=[json.loads(line) for line in (root/'usage.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    receipt=next(x for x in reversed(receipts) if x.get('operation')=='tts')
    target.with_suffix('.json').write_text(json.dumps(dict(hour=hour,phrase=phrase,
        model='qwen-audio-3.1-tts-flash',voice='longanhuan_v3.1',api_receipt=receipt,
        seconds=len(pcm)/48000,sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
        runtime_cloud_calls=0,billing_confirmed=False),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(hour=hour,status=receipt.get('status'),seconds=len(pcm)/48000)),flush=True)
