"""One fixed reminder, generated once through the existing voice adapter."""
import hashlib
import json
from pathlib import Path
import wave
from providers import Cloud
from idle_scenes import PHRASE

root=Path(__file__).resolve().parent
target=root/'assets/drink-reminder.wav'
if target.exists():raise SystemExit('Existing asset retained; no cloud call.')
cloud=Cloud(max_calls=1)
pcm=cloud.synthesize(PHRASE)
if not pcm or len(pcm)>24000*10*2:raise ValueError('Unexpected reminder duration')
target.parent.mkdir(exist_ok=True)
with wave.open(str(target),'wb') as wav:
    wav.setparams((1,2,24000,0,'NONE','not compressed'));wav.writeframes(pcm)
evidence={'phrase':PHRASE,'model':'qwen-audio-3.1-tts-flash','voice':'longanhuan_v3.1',
          'calls':cloud.calls,'seconds':len(pcm)/48000,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
          'usage_ledger':'usage.jsonl','runtime_cloud_calls_per_reminder':0}
(root/'assets/drink-reminder.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps(evidence,ensure_ascii=False))
