"""Offline checks for the weather adapter: no network, no cloud, no child data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import weather as weather_module
from providers import Cloud
from weather import UNAVAILABLE, Weather, summarize

ROOT = Path(__file__).resolve().parent

SAMPLE = {
    'real': {
        'station': {'code': 'zUeIF', 'province': '山东省', 'city': '泰安'},
        'publish_time': '2026-10-18 00:10',
        'weather': {'temperature': 19.7, 'humidity': 72.0, 'rain': 0.0, 'info': '多云',
                    'feelst': 20.1, 'airpressure': 9999.0},
        'wind': {'direct': '西南风', 'power': '微风', 'speed': 2.5},
        'warn': {'alert': '泰安市气象台发布大风蓝色预警信号', 'signaltype': '大风',
                 'signallevel': '蓝色',
                 'issuecontent': '预计17日下午至18日白天，泰山区、岱岳区将出现大风天气。'
                                 '请注意防范。'},
        'sunriseSunset': {'sunrise': '2026-10-18 06:20', 'sunset': '2026-10-18 17:32'},
    },
    'predict': {
        'publish_time': '2026-10-17 20:00',
        'detail': [
            {'date': '2026-10-18',
             'day': {'weather': {'info': '多云', 'temperature': '20'},
                     'wind': {'direct': '北风', 'power': '3~4级'}},
             'night': {'weather': {'info': '晴', 'temperature': '10'},
                       'wind': {'direct': '北风', 'power': '微风'}}},
            {'date': '2026-10-19',
             'day': {'weather': {'info': '9999', 'temperature': '9999'},
                     'wind': {'direct': '9999', 'power': '9999'}},
             'night': {'weather': {'info': '小雨', 'temperature': '11'},
                       'wind': {'direct': '南风', 'power': '3~4级'}}},
        ],
    },
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.calls = payload, status, 0

    def mount(self, *args, **kwargs):
        pass

    def get(self, url, **kwargs):
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return FakeResponse(self.payload, self.status_code)


class ProxyFallbackWeather(Weather):
    """Fails on the proxied session, succeeds on the direct one; no sockets."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sessions_used = []

    def _request(self, session):
        self.sessions_used.append(session is self.session)
        if session is self.session:
            import requests
            raise requests.exceptions.ProxyError('proxy is down')
        return SAMPLE


class WeatherTests(unittest.TestCase):
    def test_dead_local_proxy_falls_back_to_a_direct_request(self):
        import os
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            ledger = self.temp_ledger(temp)
            lookup = ProxyFallbackWeather(ledger=ledger)
            with patch.dict(os.environ, {'HTTPS_PROXY': 'http://127.0.0.1:9'}, clear=False):
                block = lookup.context()
            self.assertNotEqual(block, UNAVAILABLE)
            self.assertEqual(lookup.sessions_used, [True, False])
            self.assertFalse(lookup.direct_session.trust_env)
            statuses = [json.loads(line)['status']
                        for line in ledger.read_text(encoding='utf-8').splitlines()]
            self.assertEqual(statuses, ['direct_fallback', 'ok'])

    def test_a_failed_lookup_is_not_retried_while_unproxied(self):
        import os, requests
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            ledger = self.temp_ledger(temp)
            lookup = Weather(ledger=ledger)
            attempts = []

            def broken(session):
                attempts.append(session)
                raise requests.exceptions.ConnectionError('offline')

            lookup._request = broken
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(lookup.context(), UNAVAILABLE)
            self.assertEqual(len(attempts), 1)

    def temp_ledger(self, temp):
        return Path(temp) / 'usage.jsonl'

    def test_summary_uses_real_values_and_labels_the_place(self):
        from datetime import datetime, timezone, timedelta
        now = datetime(2026, 10, 18, 8, 0, tzinfo=timezone(timedelta(hours=8)))
        block = summarize(SAMPLE, now=now)
        self.assertIn('山东省泰安市泰山区', block)
        self.assertIn('气温19.7℃', block)
        self.assertIn('多云', block)
        self.assertIn('大风蓝色预警', block)
        self.assertIn('今天（10月18日）', block)
        self.assertIn('明天（10月19日）', block)
        self.assertIn('小雨11℃', block)  # a missing daytime part must not hide the night part
        self.assertIn('中央气象台', block)
        self.assertIn('只按这份资料回答', block)
        self.assertNotIn('9999', block)

    def test_ordinary_chat_does_not_look_up_weather(self):
        self.assertFalse(Weather.wants('讲一个恐龙的睡前故事', []))
        self.assertFalse(Weather.wants('我想画一只蓝色的鲸鱼', []))
        # Story and daily words that merely look weather-ish must not trigger a lookup.
        self.assertFalse(Weather.wants('小雪花为什么是白的', []))
        self.assertFalse(Weather.wants('太阳去哪儿睡觉了', []))
        self.assertFalse(Weather.wants('我把小雨伞画成红色的了', []))
        self.assertFalse(Weather.wants('小兔子撑着雨伞去散步', []))
        self.assertFalse(Weather.wants('我今天好开心', []))
        self.assertTrue(Weather.wants('今天天气怎么样', []))
        self.assertTrue(Weather.wants('外面下雨了吗', []))
        self.assertTrue(Weather.wants('明天要不要带伞', []))
        self.assertTrue(Weather.wants('今天冷不冷', []))
        # A short follow-up stays in the weather thread, a long new topic does not.
        history = [{'role': 'user', 'content': '今天天气怎么样'},
                   {'role': 'assistant', 'content': '今天是多云。'}]
        self.assertTrue(Weather.wants('明天呢', history))
        self.assertFalse(Weather.wants('我们来讲一只很慢很慢的小乌龟的故事吧', history))

    def test_weather_is_attached_to_the_prompt_only_when_asked(self):
        cloud = Cloud()
        response = {'choices': [{'message': {'content': '今天是多云，20度。'},
                                 'finish_reason': 'stop'}]}
        with patch.object(cloud, 'post', return_value=response) as post, \
             patch.object(cloud.weather, 'context', return_value='WEATHER_BLOCK') as context, \
             patch('providers.clock_context', return_value='CURRENT_CLOCK'):
            cloud.reply('今天天气怎么样', [])
            self.assertEqual(context.call_count, 1)
            system = post.call_args.args[1]['messages'][0]['content']
            self.assertIn('WEATHER_BLOCK', system)
            self.assertIn('CURRENT_CLOCK', system)
            post.reset_mock()
            cloud.reply('讲个小兔子的故事', [])
            self.assertEqual(context.call_count, 1)
            system = post.call_args.args[1]['messages'][0]['content']
            self.assertNotIn('WEATHER_BLOCK', system)
            self.assertIn('CURRENT_CLOCK', system)

    def test_lookup_is_cached_and_logs_metadata_only(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            ledger = self.temp_ledger(temp)
            session = FakeSession({'code': 0, 'data': SAMPLE})
            lookup = Weather(ledger=ledger, session=session)
            first = lookup.context()
            second = lookup.context()
            self.assertEqual(session.calls, 1)
            self.assertEqual(first, second)
            entries = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([entry['operation'] for entry in entries], ['weather', 'weather'])
            self.assertEqual([entry.get('cached') for entry in entries], [False, True])
            self.assertTrue(all(entry['billing_confirmed'] is False for entry in entries))
            self.assertNotIn('多云', ledger.read_text(encoding='utf-8'))

    def test_failure_speaks_honestly_and_never_raises(self):
        import requests
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            ledger = self.temp_ledger(temp)
            for broken in (requests.ConnectionError('offline'),
                           ValueError('bad json'), FakeSession({'msg': 'success', 'code': 0,
                                                                'data': ''})):
                session = broken if isinstance(broken, FakeSession) else FakeSession(broken)
                lookup = Weather(ledger=ledger, session=session)
                self.assertEqual(lookup.context(), UNAVAILABLE)
            # A well-formed response without the expected fields is logged separately.
            lookup = Weather(ledger=ledger,
                             session=FakeSession({'code': 0, 'data': {'real': {}}}))
            self.assertEqual(lookup.context(), UNAVAILABLE)
            entries = [json.loads(line) for line in ledger.read_text(encoding='utf-8').splitlines()]
            self.assertEqual([entry['status'] for entry in entries],
                             ['failed', 'failed', 'failed', 'unusable'])
            self.assertNotIn('offline', ledger.read_text(encoding='utf-8'))

    def test_http_error_status_is_reported_as_unavailable(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            lookup = Weather(ledger=self.temp_ledger(temp),
                             session=FakeSession({'code': 0, 'data': SAMPLE}, status=503))
            self.assertEqual(lookup.context(), UNAVAILABLE)

    def test_bridge_turn_attaches_weather_and_keeps_history(self):
        """The real bridge path: ASR text -> weather-aware chat -> history."""
        from bridge import Bridge
        bridge = Bridge('unused', lambda *_: None)
        cloud = Cloud()
        chat = {'choices': [{'message': {'content': '今天是多云，二十度左右。'},
                             'finish_reason': 'stop'}]}
        sent = []

        def fake_post(url, payload, key_name, operation):
            sent.append((operation, payload))
            if operation == 'chat':
                return chat
            raise AssertionError(f'unexpected cloud call: {operation}')

        with patch.object(cloud, 'recognize', return_value='今天天气怎么样'), \
             patch.object(cloud, 'post', side_effect=fake_post), \
             patch.object(cloud, 'synthesize', return_value=b'\0\0'), \
             patch.object(cloud.weather, 'context', return_value='WEATHER_BLOCK'), \
             patch.object(bridge, 'play'):
            bridge.cloud = cloud
            bridge.respond(b'\0\0', 0)
        operations = [operation for operation, _ in sent]
        self.assertEqual(operations, ['chat'])
        self.assertIn('WEATHER_BLOCK', sent[0][1]['messages'][0]['content'])
        self.assertEqual(bridge.pending_history[1:], ('今天天气怎么样', '今天是多云，二十度左右。'))
        bridge.pool.shutdown()

    def test_weather_log_is_separate_from_the_cloud_usage_ledger(self):
        cloud = Cloud()
        self.assertNotEqual(cloud.weather.ledger, cloud.ledger)
        self.assertEqual(cloud.weather.ledger.name, 'weather.jsonl')

    def test_unavailable_text_forbids_inventing_weather(self):
        self.assertIn('不要编造', UNAVAILABLE)
        self.assertIn('家长', UNAVAILABLE)

    def test_station_configuration_matches_the_home_district(self):
        self.assertEqual(weather_module.STATION, 'zUeIF')
        self.assertIn('泰安市泰山区', weather_module.LOCATION)
        # The residential address is never part of the request or the prompt.
        # Asserted structurally, without spelling any real street or estate name.
        self.assertLess(len(weather_module.LOCATION.split('区')[0].split('省')[-1]), 12)
        self.assertNotIn('小区', weather_module.PAGE)
        self.assertNotIn('路', weather_module.PAGE)


if __name__ == '__main__':
    unittest.main()
