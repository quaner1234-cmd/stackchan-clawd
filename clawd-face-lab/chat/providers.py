"""Cloud adapters. Credentials stay in the process environment; logs contain metadata only."""
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import os
import re
from pathlib import Path
import time
import urllib.parse
import wave
import requests
from requests.adapters import HTTPAdapter
from weather import Weather

ROOT = Path(__file__).resolve().parent
PROMPT = (ROOT / 'child-prompt.txt').read_text(encoding='utf-8')
# Existing Beijing workspace API Host read from the user's console; not a credential.
DASH_BASE = 'https://ws-u7a251yu0hf4zg1o.cn-beijing.maas.aliyuncs.com'
DASH_CHAT = DASH_BASE + '/compatible-mode/v1/chat/completions'
DASH_TTS = DASH_BASE + '/api/v1/services/audio/tts/SpeechSynthesizer'


def clock_context(now=None):
    now = now or datetime.now(timezone(timedelta(hours=8)))
    now = now.astimezone(timezone(timedelta(hours=8)))
    weekdays='一二三四五六日'
    dates=[]
    for label,offset in [('昨天',-1),('今天',0),('明天',1)]:
        date=now+timedelta(days=offset)
        dates.append(f'{label}是{date.year}年{date.month}月{date.day}日，星期{weekdays[date.weekday()]}')
    return ('电脑提供的当前北京时间（UTC+8）：'+now.strftime('%Y-%m-%d %H:%M')+'。'
            +'；'.join(dates)+'。询问日期、星期、时间时直接依据这些信息回答，'
            '不要说机器人不知道。每一轮以这条新时钟为准，不沿用旧对话里的日期。')


def home_context(text):
    """Parent-provided departure point, only for questions about nearby outings."""
    if not re.search(r'附近|周边|去哪.{0,3}玩|哪里.{0,3}玩|哪儿.{0,3}玩|好玩的地方|亲子.{0,5}(推荐|去)|推荐.{0,5}(公园|地方|游玩)',text):
        return ''
    try:
        settings=json.loads((ROOT/'home.json').read_text(encoding='utf-8'))
        address=settings.get('departure_address','')
        if not isinstance(address,str) or not address or len(address)>100:return ''
    except (OSError,ValueError,AttributeError):return ''
    return ('家长明确提供的亲子出游出发地：'+address+'。以这里为大致范围推荐，'
            '不要再向孩子询问住址，不要朗读或复述小区和道路名称。优先适合近4岁孩子、家长陪同的轻松活动，'
            '一次给1到2个选择。没有实时地图和营业资料，不编造步行距离、车程、门票、营业或开放情况；'
            '可以推荐你确定存在的泰安公共公园或亲子活动，并请家长出发前查地图和开放情况。')


def decode_wave(raw):
    with wave.open(io.BytesIO(raw), 'rb') as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 24000):
            raise ProviderError('语音格式不是 24kHz 单声道 PCM16。')
        # Some cloud WAV files retain the streaming length sentinel (0x7fffffff).
        # Bound actual PCM bytes instead of trusting that placeholder as a duration.
        pcm=wav.readframes(24000*30+1)
        if not pcm or len(pcm)%2 or len(pcm)>24000*30*2:
            raise ProviderError('语音为空、不完整或超过30秒，停止播放。')
        return pcm


class ProviderError(RuntimeError):
    pass


class Cloud:
    def __init__(self, model='qwen3.8-flash', provider='dashscope', ledger=None, max_calls=90,
                 weather=None):
        self.model, self.provider = model, provider
        self.ledger = Path(ledger or ROOT / 'usage.jsonl')
        self.calls, self.max_calls = 0, max_calls
        # Weather lookups are free and unbilled; they log to their own file.
        self.weather = weather if weather is not None else Weather()
        self.session=requests.Session()
        self.session.mount('https://',HTTPAdapter(max_retries=0))

    def post(self, url, payload, key_name, operation):
        if self.calls >= self.max_calls:
            raise ProviderError('本次请求次数已到上限，请家长重新启动。')
        if not os.environ.get(key_name):
            raise ProviderError(f'缺少环境变量 {key_name}；不要把 Key 发到聊天中。')
        self.calls += 1  # Failed/uncertain requests count too; never auto-retry billable calls.
        started = time.monotonic()
        record = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'operation': operation,
                  'model': payload['model'], 'billing_confirmed': False}
        try:
            with self.session.post(url,json=payload,headers={
                    'Authorization':'Bearer '+os.environ[key_name]},
                    timeout=(10,45),allow_redirects=False) as response:
                data=response.json()
                record.update(status=response.status_code, request_id=data.get('request_id') or
                              response.headers.get('x-request-id') or data.get('id'), usage=data.get('usage'))
                if response.status_code != 200:
                    code=data.get('code') or data.get('error',{}).get('code') or 'unknown'
                    code=re.sub(r'[^A-Za-z0-9_.-]','',str(code))[:80]
                    record['error_code']=code
                    if code=='AllocationQuota.FreeTierOnly':
                        message='当前模型的免费额度已用尽或过期，请家长检查百炼额度。'
                    elif response.status_code in (401,403):
                        message='云服务暂未授权，请家长检查账户凭据和模型权限。'
                    elif response.status_code==429:
                        message='云服务现在有点忙，稍等后点一下再试。'
                    else:
                        message='云服务暂时无法完成回答，请稍后再试。'
                    raise ProviderError(message)
            return data
        except (requests.RequestException, OSError, ValueError) as exc:
            reason=getattr(exc, 'reason', exc)
            record.update(status='transport_or_parse_error', error_type=type(exc).__name__,
                          cause_type=type(reason).__name__, errno=getattr(reason,'errno',None))
            raise ProviderError('网络暂时没有连上，请稍后点一下再试。') from None
        finally:
            record['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open('a', encoding='utf-8') as output:
                output.write(json.dumps(record, ensure_ascii=False) + '\n')

    def reply(self, text, history, extra=None):
        system = PROMPT+'\n'+clock_context()
        nearby=home_context(text)
        if nearby:system+='\n'+nearby
        # Weather is fetched only for weather-ish questions; ordinary chat stays one call.
        if self.weather.wants(text, history):
            system += '\n'+self.weather.context()
        # extra carries an optional photo description from the camera path.
        if extra:
            system += '\n'+extra
        messages = [{'role': 'system', 'content': system}] + history[-12:] + [
                    {'role': 'user', 'content': text[:400]}]
        body = {'model': self.model, 'messages': messages, 'max_tokens': 250,
                'temperature': 0.7, 'stream': False}
        if self.provider == 'deepseek':
            body['thinking'] = {'type': 'disabled'}
            url, key = 'https://api.deepseek.com/chat/completions', 'DEEPSEEK_API_KEY'
        else:
            body['enable_thinking'] = False
            url, key = DASH_CHAT, 'DASHSCOPE_API_KEY'
        data = self.post(url, body, key, 'chat')
        choice = data['choices'][0]
        text = choice['message'].get('content') or ''
        if not text.strip() or choice.get('finish_reason') == 'length' or len(text) > 240:
            # Avoid reading incomplete or unexpectedly long instructions aloud.
            raise ProviderError('回答为空、被截断或过长，请重新说一句。')
        return text.strip()

    def recognize(self, pcm, rate=24000):
        buffer = io.BytesIO()
        with wave.open(buffer, 'wb') as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(rate); wav.writeframes(pcm)
        uri = 'data:audio/wav;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')
        body = {'model': 'qwen3-asr-flash', 'messages': [{'role': 'user', 'content': [
                  {'type': 'input_audio', 'input_audio': {'data': uri}}]}],
                'stream': False, 'asr_options': {'language': 'zh', 'enable_itn': False}}
        data = self.post(DASH_CHAT, body, 'DASHSCOPE_API_KEY', 'asr')
        return (data['choices'][0]['message'].get('content') or '').strip()

    def synthesize(self, text):
        data = self.post(DASH_TTS, {'model': 'qwen-audio-3.1-tts-flash', 'input': {
             'text': text[:240], 'voice': 'longanhuan_v3.1', 'format':'wav',
             'sample_rate':24000, 'rate':0.95, 'language_hints':['zh'],
             'instruction':'用温暖亲切的普通话说话，吐字清晰，像在轻松地陪小朋友聊天，不要夸张。'}},
             'DASHSCOPE_API_KEY', 'tts')
        audio = data['output']['audio']
        if audio.get('data'):
            raw = base64.b64decode(audio['data'])
        else:
            url = audio['url']
            parsed = urllib.parse.urlsplit(url)
            if not parsed.hostname or not parsed.hostname.endswith('.oss-cn-beijing.aliyuncs.com'):
                raise ProviderError('语音下载地址不在预期的百炼结果域名。')
            # Signed result URL is used in memory only. Fetch over TLS, without API credentials.
            url = urllib.parse.urlunsplit(('https', parsed.netloc, parsed.path, parsed.query, ''))
            try:
                # No Authorization header is set on the session itself.
                with self.session.get(url,timeout=(10,25),stream=True,allow_redirects=False) as response:
                    response.raise_for_status()
                    if response.status_code != 200:
                        raise ProviderError('语音下载未完成，请稍后再试。')
                    raw=bytearray()
                    for chunk in response.iter_content(8192):
                        raw.extend(chunk)
                        if len(raw)>24000*2*30+4096:
                            raise ProviderError('语音下载超过30秒限制。')
            except requests.RequestException:
                raise ProviderError('语音已合成，但下载失败；未重新生成或自动重试。') from None
        return decode_wave(raw)
