"""Host side of the camera: ask the robot for one photo and describe it.

The robot streams a JPEG over the same USB link it uses for audio, so the two
must never overlap. A photo is kept in memory for the vision call and, when the
parent allowed it, one copy is written to photos/ for later review.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import time

import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent
BEIJING = timezone(timedelta(hours=8))
# Only this model answered image requests on the current account; see README.
VISION_MODEL = 'qwen3.8-omni-flash'
DASH_BASE = 'https://ws-u7a251yu0hf4zg1o.cn-beijing.maas.aliyuncs.com'
DASH_CHAT = DASH_BASE + '/compatible-mode/v1/chat/completions'
PHOTO_DIR = ROOT / 'photos'
MAX_JPEG = 96 * 1024

# The child only needs to ask; the model decides when a photo actually helps.
LOOK_WORDS = ('看看', '看一下', '瞧一瞧', '瞧瞧', '这是什么', '这个是什么', '那是什么',
              '这是啥', '这个是什么呀', '什么样子', '长什么样', '你看', '帮我看看',
              '认识这个', '哪个颜色', '什么颜色', '看到我', '看见我', '看得到我',
              '看得见我', '看到了什么', '看到什么', '拍照', '照相', '拍张照',
              '拍一张', '拍个照', '拍照片', '拍张照片', '拍个照片')

UNAVAILABLE = ('这次没有拿到可用的照片识别结果，不要描述图片内容。'
               '可以说这次看不清楚，请她举到小克面前再说一次，或者问问爸爸妈妈。')

TOOL = {'type': 'function', 'function': {
    'name': 'look_at_thing',
    'description': ('当小朋友让你看看某样东西、问这是什么、问颜色或形状，'
                    '或者需要你观察眼前的物品时调用。只是聊天、讲故事、问日期时不要调用。'),
    'parameters': {'type': 'object', 'properties': {
        'what': {'type': 'string', 'description': '小朋友想让你看的东西，用她的话简单说'}},
        'required': ['what']}}}


def wants_photo(text):
    """Cheap gate: only utterances that plausibly ask the robot to look."""
    compact=re.sub(r'[\s，。！？、,.!?～~]','',text)
    if re.search(r'(不要|别|不用|不许|停止|取消).{0,8}(拍|看|摄像)',compact):
        return False
    # Asking about the ability alone does not take a photo.
    if re.fullmatch(r'(?:小克)?(?:你)?(?:会|能|可以|能不能|可不可以)(?:拍照|照相|拍照片)(?:吗|么|嘛)?',compact):
        return False
    return any(word in text for word in LOOK_WORDS)


def capability_context(available):
    if not available:
        return '电脑确认：本次未启用相机。不能拍照或看到当前画面，不得编造眼前的内容。'
    return ('电脑确认的相机能力：小克有摄像头，能应要求拍一张照片，响快门并在屏幕显示约5秒；'
            '不是实时视频，也不是一直看着人。问能否拍照时直接说明可以，邀请说“拍一张”。'
            '本轮没有新的照片资料时，不能根据历史描述猜测眼前的人、物品或穿着；'
            '只有本轮系统附的照片资料才能作为当前看到的内容。')


class Camera:
    """Requests photos from the device and turns them into a short description."""

    def __init__(self, model=VISION_MODEL, ledger=None, session=None,
                 save_dir=None, keep_photos=True):
        self.model = model
        self.ledger = Path(ledger or ROOT / 'usage.jsonl')
        self.save_dir = Path(save_dir or PHOTO_DIR)
        self.keep_photos = keep_photos
        self.session = session or requests.Session()
        self.session.mount('https://', HTTPAdapter(max_retries=0))

    def describe(self, jpeg, question=''):
        """Ask the vision model what is in the photo; never raises."""
        if not jpeg:
            return UNAVAILABLE
        if len(jpeg) > MAX_JPEG:
            self._log('too_large', bytes=len(jpeg))
            return UNAVAILABLE
        if not jpeg.startswith(b'\xff\xd8'):
            self._log('not_jpeg', bytes=len(jpeg))
            return UNAVAILABLE
        import base64
        uri = 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')
        prompt = ('你是桌上的橙色小螃蟹机器人小克，正在陪一个3岁9个月的小朋友。'
                  '看她给你看的东西，用日常中文说1到3句、20到70个汉字。'
                  '只说这张照片能看见的，看不清就说看不清；不要编造，不猜人的身份、健康或住址。'
                  '如果她问颜色、形状或数量，先直接答这个，再补一句你看到的细节。')
        asked = f'小朋友说：{question}。' if question else '小朋友让你看看这个。'
        body = {'model': self.model, 'messages': [
                    {'role': 'system', 'content': prompt},
                    {'role': 'user', 'content': [
                        {'type': 'image_url', 'image_url': {'url': uri}},
                        {'type': 'text', 'text': asked + '你看到了什么？'}]}],
                'max_tokens': 200, 'enable_thinking': False}
        started = time.monotonic()
        try:
            with self.session.post(DASH_CHAT, json=body, timeout=(10, 45),
                                   headers={'Authorization':
                                            'Bearer ' + os.environ.get('DASHSCOPE_API_KEY', '')},
                                   allow_redirects=False) as response:
                data = response.json()
                if response.status_code != 200:
                    code = (data.get('error') or {}).get('code') or 'unknown'
                    self._log('failed', http_status=response.status_code,
                              code=str(code)[:60],
                              elapsed_ms=round((time.monotonic() - started) * 1000))
                    return UNAVAILABLE
                text = (data['choices'][0]['message'].get('content') or '').strip()
                self._log('ok', elapsed_ms=round((time.monotonic() - started) * 1000),
                          request_id=data.get('request_id') or data.get('id') or
                          getattr(response,'headers',{}).get('x-request-id'),
                          usage=data.get('usage'))
        except (requests.RequestException, OSError, ValueError, KeyError) as exc:
            self._log('failed', error_type=type(exc).__name__)
            return UNAVAILABLE
        if not text or len(text) > 200:
            return UNAVAILABLE
        return text

    def save(self, jpeg, note=''):
        """Write one copy for the parent to review later; returns the path or None."""
        if not self.keep_photos or not jpeg:
            return None
        try:
            self.save_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(BEIJING).strftime('%Y%m%d-%H%M%S-%f')
            for suffix in range(1000):
                path = self.save_dir / f'photo-{stamp}{"-"+str(suffix) if suffix else ""}.jpg'
                try:
                    with path.open('xb') as output:output.write(jpeg)
                    break
                except FileExistsError:continue
            else:return None
            if note:
                path.with_suffix('.txt').write_text(note, encoding='utf-8')
            return path
        except OSError:
            return None

    def _log(self, status, **metadata):
        record = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'operation': 'photo',
                  'model': self.model, 'status': status, 'billing_confirmed': False,
                  **metadata}
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open('a', encoding='utf-8') as output:
                output.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            pass
