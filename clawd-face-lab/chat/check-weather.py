"""Adult-authored weather check: a handful of real chat calls, no child audio.

Each case is sent as a parent would type it. The weather block itself comes from
the live public feed, so a passing case proves the model used real data.
"""
import argparse
import json
from pathlib import Path
import time

from providers import Cloud
from weather import UNAVAILABLE, Weather

CASES = [
    ('today', '今天天气怎么样？', []),
    ('rain_tomorrow', '明天会下雨吗？', []),
    ('other_city', '北京的天气怎么样？', []),
    ('follow_up', '那我要不要穿外套？', [
        {'role': 'user', 'content': '今天天气怎么样？'},
        {'role': 'assistant', 'content': '今天是多云，白天大概二十度。'}]),
    ('date_regression', '今天星期几呀？', []),
    ('story_regression', '讲一个很短的小故事吧。', []),
]

parser = argparse.ArgumentParser()
parser.add_argument('--model', default='qwen3.8-flash')
args = parser.parse_args()

cloud = Cloud(model=args.model, max_calls=len(CASES) + 2)
results = []
weather_block = None
for name, text, history in CASES:
    if Weather.wants(text, history):
        # Same call the bridge makes, captured here for the evidence file.
        block = cloud.weather.context()
        weather_block = block
        prompt_has_weather = True
    else:
        block, prompt_has_weather = None, False
    answer = cloud.reply(text, history)
    record = {'case': name, 'adult_test_input': text, 'reply': answer,
              'length': len(answer), 'weather_attached': prompt_has_weather,
              'mentions_unavailable': UNAVAILABLE[:6] in answer}
    results.append(record)
    print(json.dumps(record, ensure_ascii=False), flush=True)

# The failure path must be honest and must not read a guess aloud.
cloud.weather.context = lambda: UNAVAILABLE
failure = cloud.reply('现在外面下雨了吗？', [])
failure_record = {'case': 'lookup_failure', 'adult_test_input': '现在外面下雨了吗？',
                  'reply': failure, 'length': len(failure), 'weather_attached': True,
                  'injected_unavailable': True}
results.append(failure_record)
print(json.dumps(failure_record, ensure_ascii=False), flush=True)

payload = {'scope': 'Synthetic adult-authored scenarios only; manual review required',
           'model': cloud.model, 'weather_source': 'nmc.cn live feed',
           'weather_block_sample': weather_block, 'results': results}
out = Path(__file__).parent / f"weather-check-{time.strftime('%Y%m%d-%H%M%S')}.json"
out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
print('WROTE', out.name)
