"""Real-time weather for the child's home district, from the national weather service.

The host fetches one small public JSON document; no key and no account are used.
Only the district is passed to the chat model, never the residential address.
Failures return an honest sentence so the model cannot invent weather.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import time

import requests
from requests.adapters import HTTPAdapter

ROOT = Path(__file__).resolve().parent
BEIJING = timezone(timedelta(hours=8))
# 中央气象台 泰安 站号（山东省）。换城市时改这三项即可，见 README。
STATION = 'zUeIF'
LOCATION = '山东省泰安市泰山区'
SOURCE = '中国气象局中央气象台（nmc.cn）'
PAGE = 'https://www.nmc.cn/publish/forecast/ASD/taian.html'
API = 'https://www.nmc.cn/rest/weather'
CACHE_SECONDS = 600
SENTINEL = '9999'
LABELS = ('今天', '明天', '后天')

# 只在这些词出现时查询，普通聊天和讲故事不增加请求。
# 单字“雪/冰/太阳/雷/冷/热”和“雨伞/小雨”常出现在故事和画画里，
# 所以只收明确在问外面天气的说法。
WEATHER_WORDS = ('天气', '气温', '温度', '预报', '下雨', '雨点', '雨停', '带伞', '撑伞',
                 '下雪', '雪天', '积雪', '刮风', '风大', '大风', '阴天', '晴天', '多云',
                 '雾', '霾', '湿度', '打雷', '台风', '预警', '空气', '外套', '穿厚',
                 '穿薄', '好冷', '好热', '冷不冷', '热不热', '晒不晒')

UNAVAILABLE = ('电脑这次没有查到实时天气，不要编造天气、气温或预报。'
               '可以说现在查不到天气，过一会儿再问，或者请家长一起看看窗外。')


def _clean(value):
    """Treat the service's 9999 sentinel and blanks as missing data."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value or value == SENTINEL:
            return None
    elif value == 9999 or value == 9999.0:
        return None
    return value


def _num(value):
    """Render a number without a pointless trailing .0; missing stays missing."""
    value = _clean(value)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return str(int(number)) if number == int(number) else f'{number:g}'


def _short(text, limit=110):
    text = _clean(text)
    if not text:
        return None
    for mark in ('。', '；'):
        head = text.split(mark)[0]
        if head and len(head) <= limit:
            return head + mark
    return text[:limit] + ('…' if len(text) > limit else '')


def summarize(data, now=None):
    """Turn the weather document into a short factual block for the chat model."""
    now = (now or datetime.now(BEIJING)).astimezone(BEIJING)
    if not isinstance(data, dict) or not isinstance(data.get('real'), dict):
        raise ValueError('no observation section')
    real = data['real']
    weather = real.get('weather') or {}
    wind = real.get('wind') or {}
    lines = [f'电脑查到的实时天气资料（地点：{LOCATION}，泰安市区观测）：']

    observed = []
    for label, key, unit in (('天气', 'info', ''), ('气温', 'temperature', '℃'),
                             ('体感温度', 'feelst', '℃'), ('湿度', 'humidity', '%'),
                             ('一小时降水', 'rain', '毫米')):
        value = _num(weather.get(key))
        if value is not None:
            observed.append(f'{label}{value}{unit}')
    direct, power = _clean(wind.get('direct')), _clean(wind.get('power'))
    if direct and power:
        observed.append(f'{direct}{power}')
    if observed:
        published = _clean(real.get('publish_time'))
        lines.append((f'观测时间{published}：' if published else '实况：') + '，'.join(observed))

    warn = real.get('warn') or {}
    alert = _clean(warn.get('alert'))
    if alert:
        signal = ''.join(part for part in (_clean(warn.get('signaltype')),
                                           _clean(warn.get('signallevel'))) if part)
        lines.append(f'气象预警：{alert}' + (f'（{signal}）' if signal else ''))
        key_point = _short(warn.get('issuecontent'))
        if key_point:
            lines.append(f'预警要点：{key_point}')

    detail = ((data.get('predict') or {}).get('detail') or [])
    by_date = {entry.get('date'): entry for entry in detail if isinstance(entry, dict)}
    today = now.date()
    for offset, label in enumerate(LABELS):
        date = today + timedelta(days=offset)
        entry = by_date.get(date.isoformat())
        if not entry:
            continue
        pieces = []
        for part_label, key in (('白天', 'day'), ('夜间', 'night')):
            part = entry.get(key) or {}
            part_weather = part.get('weather') or {}
            info = _clean(part_weather.get('info'))
            temperature = _num(part_weather.get('temperature'))
            if info is None and temperature is None:
                continue
            text = f'{part_label}{info or ""}'
            if temperature is not None:
                text += f'{temperature}℃'
            part_wind = part.get('wind') or {}
            part_direct, part_power = _clean(part_wind.get('direct')), _clean(part_wind.get('power'))
            if part_direct and part_power:
                text += f'，{part_direct}{part_power}'
            pieces.append(text)
        if pieces:
            lines.append(f'{label}（{date.month}月{date.day}日）：' + '；'.join(pieces))

    sun = real.get('sunriseSunset') or {}
    sunrise, sunset = _clean(sun.get('sunrise')), _clean(sun.get('sunset'))
    if sunrise and sunset:
        lines.append(f'日出{sunrise[-5:]}，日落{sunset[-5:]}')

    # A document with headers but no actual readings is a failure, not "no data today".
    if len(lines) == 1:
        raise ValueError('no usable weather facts')

    published = _clean((data.get('predict') or {}).get('publish_time'))
    lines.append(f'数据来源：{SOURCE}' + (f'，预报发布时间{published}' if published else '')
                 + f'；电脑查询时间{now.strftime("%Y-%m-%d %H:%M")}。'
                 '只按这份资料回答；资料里没有的项目就说不知道；'
                 '问到泰安以外的地方，就说只会看泰安的天气。')
    return '\n'.join(lines)


class Weather:
    """Cached, on-demand weather lookup for one fixed home location."""

    def __init__(self, ledger=None, session=None, ttl=CACHE_SECONDS, timeout=(3, 6)):
        # Kept apart from usage.jsonl so cloud billing lines stay uncontaminated.
        self.ledger = Path(ledger or ROOT / 'weather.jsonl')
        self.ttl, self.timeout = ttl, timeout
        self.session = session or requests.Session()
        # Only a session we created can be swapped for an environment-free one.
        self._owns_session = session is None
        self._direct_session = None
        self.session.mount('https://', HTTPAdapter(max_retries=0))
        self._data, self._fetched_at = None, 0.0

    @staticmethod
    def wants(text, history=()):
        """True when the child is likely asking about real weather."""
        if any(word in text for word in WEATHER_WORDS):
            return True
        # Short follow-ups such as “明天呢” stay in the weather thread.
        if len(text) > 8:
            return False
        earlier = [message.get('content') or '' for message in history
                   if isinstance(message, dict) and message.get('role') == 'user']
        return bool(earlier) and any(word in earlier[-1] for word in WEATHER_WORDS)

    def context(self):
        """Always returns text for the prompt; never raises."""
        started = time.monotonic()
        cached = self._data is not None and started - self._fetched_at < self.ttl
        if not cached:
            try:
                self._data = self._fetch()
                self._fetched_at = started
            except (requests.RequestException, OSError, ValueError, KeyError, TypeError) as exc:
                self._log('failed', started, error_type=type(exc).__name__)
                return UNAVAILABLE
        try:
            block = summarize(self._data)
        except (KeyError, TypeError, ValueError):
            self._data = None
            self._log('unusable', started)
            return UNAVAILABLE
        self._log('ok', started, cached=cached)
        return block

    def _fetch(self):
        """One request; a dead local proxy falls back to a direct connection.

        The launcher exports the Clash proxy for the cloud calls. nmc.cn answers
        directly too, so a stopped proxy must not turn weather into "unavailable".
        """
        try:
            return self._request(self.session)
        except (requests.exceptions.ProxyError, requests.exceptions.SSLError):
            if not (self._owns_session and self._proxied()):
                raise
            self._log('direct_fallback', time.monotonic())
            return self._request(self.direct_session)

    @staticmethod
    def _proxied():
        return any(os.environ.get(name) for name in
                   ('HTTPS_PROXY', 'https_proxy', 'ALL_PROXY', 'all_proxy'))

    @property
    def direct_session(self):
        """A session that ignores HTTP(S)_PROXY; built once, on first fallback."""
        if self._direct_session is None:
            self._direct_session = requests.Session()
            self._direct_session.trust_env = False
            self._direct_session.mount('https://', HTTPAdapter(max_retries=0))
        return self._direct_session

    def _request(self, session):
        with session.get(API, params={'stationid': STATION}, timeout=self.timeout,
                         headers={'User-Agent': 'clawd-face-lab/1.0', 'Referer': PAGE},
                         allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError(f'HTTP {response.status_code}')
            document = response.json()
        if document.get('code') != 0 or not isinstance(document.get('data'), dict):
            raise ValueError('unexpected payload')
        return document['data']

    def _log(self, status, started, **metadata):
        """Weather metadata only; never the child's words."""
        record = {'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'operation': 'weather',
                  'model': 'nmc.cn', 'source': SOURCE, 'station': STATION,
                  'status': status, 'billing_confirmed': False,
                  'elapsed_ms': round((time.monotonic() - started) * 1000), **metadata}
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with self.ledger.open('a', encoding='utf-8') as output:
                output.write(json.dumps(record, ensure_ascii=False) + '\n')
        except OSError:
            pass
