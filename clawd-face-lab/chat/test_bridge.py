import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from bridge import Bridge
from providers import Cloud, ProviderError, decode_wave, clock_context, home_context
ROOT=Path(__file__).resolve().parent


class BridgeTests(unittest.TestCase):
    def test_home_departure_point_only_sent_for_nearby_outings(self):
        private='FAKE_PARENT_DEPARTURE_POINT'
        with patch('providers.Path.read_text',return_value=json.dumps({'departure_address':private})):
            for question in ('附近哪里好玩呀','周末去哪玩','推荐一个公园','周边适合小朋友的地方'):
                self.assertIn(private,home_context(question))
            for question in ('今天星期几','我住在哪里','你好小克','讲个故事','天气怎么样'):
                self.assertEqual('',home_context(question))

    def test_parent_button_restarts_recording_and_stop_cancels_pending_restart(self):
        bridge=Bridge('unused',lambda *_:None)
        bridge.state='ready';bridge.phase='listening';bridge.parent_handler=lambda *_:None
        writes=[]
        class Device:
            is_open=True
            def write(self,data):writes.append(data)
        bridge.serial=Device()
        with patch('bridge.time.monotonic',return_value=10):
            self.assertTrue(bridge.arm_parent())
            bridge.resume_parent_if_due()
        self.assertEqual(writes,[b'x'])
        with patch('bridge.time.monotonic',return_value=11):bridge.resume_parent_if_due()
        self.assertEqual(writes,[b'x',b'r'])
        self.assertTrue(bridge.parent_capture)
        bridge.arm_parent();bridge.cancel()
        self.assertIsNone(bridge.parent_restart_at)
        bridge.pool.shutdown()

    def test_parent_route_is_explicit_and_never_enters_child_history(self):
        bridge=Bridge('unused',lambda *_:None)
        captured=[]
        bridge.parent_handler=captured.append
        class Fake:
            def recognize(self,pcm):return '请帮我制定周末计划'
            def synthesize(self,text):return b'\0\0'
            def reply(self,*_,**__):raise AssertionError('Parent request must not enter child chat')
        bridge.cloud=Fake()
        with patch.object(bridge,'play'):
            bridge.respond_parent(b'\0\0',0)
        self.assertEqual(captured,['请帮我制定周末计划'])
        self.assertEqual(bridge.history,[])
        self.assertEqual(bridge.pending_parent_ack,0)
        self.assertTrue(bridge.continuous_active)
        bridge.cancel()
        self.assertIsNone(bridge.pending_parent_ack)
        bridge.pool.shutdown()

    def test_cancelled_parent_asr_never_dispatches_task(self):
        bridge=Bridge('unused',lambda *_:None)
        bridge.parent_handler=lambda *_:self.fail('Cancelled parent task must not dispatch')
        class Fake:
            def recognize(self,pcm):
                bridge.epoch+=1
                return '制定计划'
        bridge.cloud=Fake()
        bridge.respond_parent(b'\0\0',0)
        bridge.pool.shutdown()

    def test_silent_continuous_retry_does_not_play_chime(self):
        bridge=Bridge('unused',lambda *_:None)
        bridge.state='ready';bridge.continuous_active=True
        writes=[]
        class Device:
            is_open=True
            def write(self,data):writes.append(data)
        bridge.serial=Device()
        with patch('bridge.time.monotonic',return_value=10):bridge.schedule_listen(cue=False)
        with patch('bridge.time.monotonic',return_value=11):bridge.resume_if_due()
        self.assertEqual(writes,[b't'])
        bridge.pool.shutdown()

    def test_clock_uses_beijing_day_across_midnight(self):
        from datetime import datetime,timezone
        context=clock_context(datetime(2026,10,17,17,0,tzinfo=timezone.utc))
        self.assertIn('今天是2026年10月18日，星期日',context)
        self.assertIn('昨天是2026年10月17日，星期六',context)
        self.assertIn('明天是2026年10月19日，星期一',context)

    def test_clock_is_added_each_request_after_prior_day_history(self):
        cloud=Cloud()
        response={'choices':[{'message':{'content':'今天星期六。'},'finish_reason':'stop'}]}
        with patch('providers.clock_context',return_value='CURRENT_CLOCK'), \
             patch.object(cloud,'post',return_value=response) as post:
            cloud.reply('今天星期几？',[{'role':'assistant','content':'OLDER_DATE'}])
        messages=post.call_args.args[1]['messages']
        self.assertIn('CURRENT_CLOCK',messages[0]['content'])
        self.assertEqual(messages[-1]['content'],'今天星期几？')

    def test_continuous_rearms_only_after_deadline_and_stops_on_cancel(self):
        bridge=Bridge('unused',lambda *_:None)
        bridge.state='ready'
        bridge.continuous_active=True
        writes=[]
        class Device:
            is_open=True
            def write(self,data):writes.append(data)
        bridge.serial=Device()
        with patch('bridge.time.monotonic',return_value=10):
            bridge.schedule_listen()
            bridge.resume_if_due()
        self.assertEqual(writes,[])
        with patch('bridge.time.monotonic',return_value=11):
            bridge.resume_if_due()
            bridge.resume_if_due()
        self.assertEqual(writes,[b'r'])
        bridge.schedule_listen()
        bridge.cancel()
        self.assertFalse(bridge.continuous_active)
        self.assertIsNone(bridge.next_listen_at)
        bridge.pool.shutdown()

    def test_stop_words_are_narrow_and_disable_continuous(self):
        self.assertTrue(Bridge.wants_to_stop('小克，再见！'))
        self.assertTrue(Bridge.wants_to_stop('小橙，再见！'))
        self.assertTrue(Bridge.wants_to_stop('不聊了'))
        self.assertFalse(Bridge.wants_to_stop('不要停，讲个再见的故事'))
        bridge=Bridge('unused',lambda *_:None)
        bridge.continuous_active=True
        class Fake:
            def recognize(self,pcm):return '小克再见'
            def reply(self,*_,**__):return '再见，祝你玩得开心。'
            def synthesize(self,*_):return b'\0\0'
        bridge.cloud=Fake()
        with patch.object(bridge,'play'):
            bridge.respond(b'\0\0',0)
        self.assertFalse(bridge.continuous_active)
        bridge.pool.shutdown()

    def test_connection_failure_is_not_hidden_by_listen_or_stop(self):
        messages=[]
        bridge=Bridge('unused',lambda message,*_:messages.append(message))
        with patch('bridge.serial.Serial',side_effect=OSError('test busy')), \
             patch.object(bridge,'connection_event'):
            bridge.run()
        self.assertEqual(bridge.state,'error')
        self.assertTrue(bridge.closed.is_set())
        error=messages[-1]
        bridge.listen()
        bridge.cancel()
        self.assertEqual(messages[-2:],[error,error])
        self.assertIn('重新连接',error)

    def test_lost_first_handshake_is_retried_without_audio_commands(self):
        bridge=Bridge('unused',lambda *_:None)
        clock=[0.0]
        writes=[]
        class Device:
            is_open=True
            def open(self):pass
            def close(self):self.is_open=False
            def write(self,data):writes.append(data)
            def readline(self):
                clock[0]+=.25
                if not writes:return b'CLAWD alive uptime_ms=10000\n'
                if writes.count(b'h')>=2:
                    if bridge.state=='ready':
                        bridge.closed.set()
                        return b''
                    return b'CLAWD CHAT_READY rate=24000\n'
                return b''
        with patch('bridge.serial.Serial',return_value=Device()), \
             patch('bridge.time.monotonic',side_effect=lambda:clock[0]), \
             patch.object(bridge,'connection_event'):
            bridge.run()
        self.assertEqual(writes.count(b'h'),2)
        self.assertNotIn(b'r',writes)
        self.assertNotIn(b'p',writes)
        self.assertIsNone(bridge.last_error)

    def test_unidentified_device_is_never_sent_commands(self):
        bridge=Bridge('unused',lambda *_:None)
        clock=[0.0]
        class Device:
            is_open=True
            def open(self):pass
            def close(self):self.is_open=False
            def write(self,data):
                raise AssertionError('Unidentified device must not receive commands')
            def readline(self):
                clock[0]+=1
                return b'unknown firmware\n'
        with patch('bridge.serial.Serial',return_value=Device()), \
             patch('bridge.time.monotonic',side_effect=lambda:clock[0]), \
             patch.object(bridge,'connection_event'):
            bridge.run()
        self.assertEqual(bridge.state,'error')
        self.assertIn('没有识别',bridge.last_error)

    def test_wav_streaming_length_sentinel_is_not_actual_duration(self):
        import io,struct,wave
        out=io.BytesIO()
        with wave.open(out,'wb') as wav:
            wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(bytes(4800))
        raw=bytearray(out.getvalue())
        struct.pack_into('<I',raw,4,0x7fffffff)
        struct.pack_into('<I',raw,40,0x7fffffff)
        self.assertEqual(len(decode_wave(raw)),4800)

    def test_cancel_after_asr_never_calls_chat(self):
        bridge=Bridge('unused',lambda *_:None)
        class Fake:
            def recognize(self,pcm):
                bridge.epoch += 1
                return 'test'
            def reply(self,*_,**__):
                self.fail('cancelled request must not reach chat')
        bridge.cloud=Fake()
        bridge.respond(b'\0\0',0)
        self.assertEqual(bridge.history,[])
        bridge.pool.shutdown()

    def test_cancel_after_chat_never_calls_tts(self):
        bridge=Bridge('unused',lambda *_:None)
        class Fake:
            def recognize(self,pcm):return 'test'
            def reply(self,*_,**__):
                bridge.epoch += 1
                return 'answer'
            def synthesize(self,*_):raise AssertionError('cancelled request must not reach TTS')
        bridge.cloud=Fake()
        bridge.respond(b'\0\0',0)
        self.assertIsNone(bridge.pending_history)
        bridge.pool.shutdown()

    def test_upload_completes_framing_before_cancel_command(self):
        bridge=Bridge('unused',lambda *_:None)
        writes=[]
        class FakeSerial:
            is_open=True
            def write(self,data):
                writes.append(data)
                with bridge.condition:
                    if data == b'p':bridge.upload_ready=True
                    elif len(writes)>2:
                        bridge.upload_ack += len(data)
                        bridge.epoch=1
                    bridge.condition.notify_all()
        bridge.serial=FakeSerial()
        bridge.play(bytes(5000),0)
        self.assertEqual([len(data) for data in writes],[1,8,2048,2048,904,1])
        self.assertEqual(writes[-1],b'x')
        self.assertFalse(bridge.uploading)
        bridge.pool.shutdown()

    def test_call_ceiling_stops_before_network(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            cloud=Cloud(ledger=Path(temp)/'usage.jsonl',max_calls=0)
            with patch.object(cloud.session,'post') as network:
                with self.assertRaises(ProviderError):cloud.reply('test',[])
                network.assert_not_called()

    def test_paid_request_not_retried_and_only_metadata_logged(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            ledger=Path(temp)/'usage.jsonl'
            cloud=Cloud(ledger=ledger)
            import requests
            error=requests.ConnectionError('do not print transport internals')
            with patch.dict('os.environ',{'DASHSCOPE_API_KEY':'unit-test-dummy'}), \
                 patch.object(cloud.session,'post',side_effect=error) as network:
                with self.assertRaises(ProviderError):cloud.reply('PRIVATE_INPUT',[])
                self.assertEqual(network.call_count,1)
            log=ledger.read_text()
            self.assertNotIn('PRIVATE_INPUT',log)
            self.assertNotIn('unit-test-dummy',log)
            self.assertFalse(json.loads(log)['billing_confirmed'])


if __name__=='__main__':unittest.main()
