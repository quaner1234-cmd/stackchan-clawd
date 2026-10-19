"""Three small, billable calls using synthetic adult test text; no child audio."""
import json
import argparse
from pathlib import Path
import wave
from providers import Cloud

parser=argparse.ArgumentParser(); parser.add_argument('--voice-only',action='store_true')
args=parser.parse_args()
cloud = Cloud(max_calls=3)
if not args.voice_only:
    reply = cloud.reply('小克，我想和小兔子坐飞船去月亮。', [])
    print('CHAT_REPLY:', reply, flush=True)
pcm = cloud.synthesize('你好，我是小克。我们一起去月亮找小兔子吧。')
print('TTS:', json.dumps({'bytes': len(pcm), 'seconds': len(pcm) / 48000}), flush=True)
with wave.open(str(Path(__file__).parent/'adult-cloud-test.wav'),'wb') as audio:
    audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000); audio.writeframes(pcm)
recognized = cloud.recognize(pcm)
print('ASR_SYNTHETIC:', recognized, flush=True)
