"""Host-side voice path check: synthetic speech -> ASR -> weather chat -> TTS.

Uses the same Cloud calls the bridge makes, with no robot and no child audio.
It proves the weather block reaches a real spoken question, not only typed text.
"""
import json
from pathlib import Path
import time

from providers import Cloud
from weather import Weather

QUESTION = '今天天气怎么样？'
cloud = Cloud(max_calls=4)

speech = cloud.synthesize(QUESTION)
heard = cloud.recognize(speech)
record = {'step': 'asr', 'synthetic_question': QUESTION, 'recognized': heard,
          'seconds': round(len(speech) / 48000, 2)}
print(json.dumps(record, ensure_ascii=False), flush=True)

attached = Weather.wants(heard, [])
block = cloud.weather.context() if attached else None
answer = cloud.reply(heard, [])
speech2 = cloud.synthesize(answer)
result = {'step': 'chat', 'recognized': heard, 'weather_attached': attached,
          'reply': answer, 'reply_length': len(answer),
          'spoken_seconds': round(len(speech2) / 48000, 2),
          'weather_block': block}
print(json.dumps(result, ensure_ascii=False), flush=True)

out = Path(__file__).parent / f"weather-voice-check-{time.strftime('%Y%m%d-%H%M%S')}.json"
out.write_text(json.dumps({'scope': 'Synthetic speech only; no robot, no child audio',
                           'model': cloud.model, 'asr': 'qwen3-asr-flash',
                           'tts': 'qwen-audio-3.1-tts-flash', 'asr_step': record,
                           'chat_step': result}, ensure_ascii=False, indent=2),
               encoding='utf-8')
print('WROTE', out.name)
