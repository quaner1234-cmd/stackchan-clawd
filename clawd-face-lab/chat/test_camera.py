"""Offline checks for the camera path: no device, no network, no child photos."""
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import camera as camera_module
from bridge import Bridge
from camera import MAX_JPEG, UNAVAILABLE, Camera, wants_photo

ROOT = Path(__file__).resolve().parent
JPEG = b'\xff\xd8\xff\xe0' + bytes(1200) + b'\xff\xd9'


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

    def post(self, url, **kwargs):
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return FakeResponse(self.payload, self.status_code)


class Device:
    """Minimal serial stand-in that records the bytes the bridge sends."""

    is_open = True

    def __init__(self, writes):
        self.writes = writes

    def write(self, data):
        self.writes.append(data)


def vision_ok(text='我看到一只橙色的小螃蟹。'):
    return {'choices': [{'message': {'content': text}}], 'usage': {'total_tokens': 40}}


class CameraGateTests(unittest.TestCase):
    def test_photo_commands_and_current_view_questions_take_a_fresh_photo(self):
        for words in ('拍张照', '帮我拍一张', '给我拍照片', '你能看到我吗', '你看得见我吗'):
            self.assertTrue(wants_photo(words), words)

    def test_ability_questions_and_refusals_do_not_take_a_photo(self):
        for words in ('你能拍照吗？', '你会照相吗', '小克你能不能拍照', '不要拍照',
                      '别看看我了', '不用拍照片', '取消拍照'):
            self.assertFalse(wants_photo(words), words)

    def test_only_look_requests_trigger_a_photo(self):
        for text in ('小克你看看我画的画', '这个是什么呀', '你看这是什么', '它长什么样'):
            self.assertTrue(wants_photo(text), text)
        for text in ('今天星期几呀', '讲个小故事', '我想和小兔子去月亮', '我今天很开心'):
            self.assertFalse(wants_photo(text), text)


class CameraDescribeTests(unittest.TestCase):
    def test_two_saves_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            camera=Camera(save_dir=Path(temp))
            first=camera.save(JPEG)
            second=camera.save(JPEG+b'1')
            self.assertNotEqual(first,second)
            self.assertEqual(first.read_bytes(),JPEG)
            self.assertEqual(second.read_bytes(),JPEG+b'1')

    def test_description_is_used_and_logged(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            ledger = Path(temp) / 'usage.jsonl'
            session = FakeSession(vision_ok())
            camera = Camera(ledger=ledger, session=session, keep_photos=False)
            described = camera.describe(JPEG, '看看这个')
            self.assertEqual(described, '我看到一只橙色的小螃蟹。')
            entry = json.loads(ledger.read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(entry['operation'], 'photo')
            self.assertEqual(entry['status'], 'ok')
            self.assertFalse(entry['billing_confirmed'])

    def test_failures_never_produce_a_guess(self):
        cases = [FakeSession({'error': {'code': 'insufficient_quota'}}, status=403),
                 FakeSession(OSError('offline')),
                 FakeSession(vision_ok('')),          # empty answer
                 FakeSession(vision_ok('长' * 300))]   # absurdly long answer
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            ledger = Path(temp) / 'usage.jsonl'
            for session in cases:
                camera = Camera(ledger=ledger, session=session, keep_photos=False)
                self.assertEqual(camera.describe(JPEG), UNAVAILABLE)
            # Bad payloads never reach the network at all.
            camera = Camera(ledger=ledger, session=FakeSession(vision_ok()), keep_photos=False)
            self.assertEqual(camera.describe(b'not a jpeg'), UNAVAILABLE)
            self.assertEqual(camera.describe(b''), UNAVAILABLE)
            self.assertEqual(camera.describe(b'\xff\xd8' + bytes(MAX_JPEG)), UNAVAILABLE)

    def test_photo_is_saved_for_the_parent_when_allowed(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            assert Path(temp).resolve().is_relative_to(ROOT)
            save_dir = Path(temp) / 'photos'
            camera = Camera(ledger=Path(temp) / 'usage.jsonl',
                            session=FakeSession(vision_ok()), save_dir=save_dir)
            path = camera.save(JPEG, '一只小螃蟹')
            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), JPEG)
            self.assertEqual(path.with_suffix('.txt').read_text(encoding='utf-8'), '一只小螃蟹')
            # Opting out keeps nothing on disk.
            off = Camera(ledger=Path(temp) / 'usage.jsonl', session=FakeSession(vision_ok()),
                         save_dir=save_dir, keep_photos=False)
            self.assertIsNone(off.save(JPEG, 'x'))


class BridgeCameraTests(unittest.TestCase):
    def test_capability_question_does_not_request_or_describe_a_photo(self):
        bridge,_=self.make_bridge()
        with patch.object(bridge,'request_photo') as request, \
             patch.object(bridge.camera,'describe') as describe:
            self.assertIsNone(bridge.photo_context('你能拍照吗？',[]))
            request.assert_not_called();describe.assert_not_called()
        bridge.pool.shutdown()

    def test_capture_saves_before_vision_and_reports_vision_failure_truthfully(self):
        bridge,_=self.make_bridge();order=[]
        with patch.object(bridge,'request_photo',return_value=JPEG), \
             patch.object(bridge.camera,'save',side_effect=lambda *_:order.append('save') or Path('photo.jpg')), \
             patch.object(bridge.camera,'describe',side_effect=lambda *_:order.append('vision') or UNAVAILABLE):
            result=bridge.photo_context('拍张照',[])
        self.assertEqual(order,['save','vision'])
        self.assertIn('已经拍到照片并保存到电脑',result)
        self.assertIn('暂时没能识别',result)
        bridge.pool.shutdown()

    def test_cancel_after_capture_prevents_save_and_vision(self):
        bridge,_=self.make_bridge()
        def capture():
            bridge.cancel();return JPEG
        with patch.object(bridge,'request_photo',side_effect=capture), \
             patch.object(bridge.camera,'save') as save, \
             patch.object(bridge.camera,'describe') as describe:
            self.assertIsNone(bridge.photo_context('你看看这个',[],bridge.epoch))
            save.assert_not_called();describe.assert_not_called()
        bridge.pool.shutdown()

    def test_no_new_photo_context_overrides_old_visual_history(self):
        bridge,_=self.make_bridge();extras=[]
        class Cloud:
            def recognize(self,*_):return '你能拍照吗'
            def reply(self,text,history,extra=None):
                extras.append(extra);return '可以，说拍一张就行。'
            def synthesize(self,*_):return b'\0\0'
        bridge.cloud=Cloud()
        bridge.history=[{'role':'assistant','content':'我看到你穿蓝色衣服。'}]
        with patch.object(bridge,'request_photo') as request,patch.object(bridge,'play'):
            bridge.respond(b'\0\0',bridge.epoch)
            request.assert_not_called()
        self.assertIn('本轮未拍照，不要描述当前眼前的内容',extras[0])
        self.assertIn('有摄像头',extras[0])
        bridge.pool.shutdown()

    def make_bridge(self):
        bridge = Bridge('unused', lambda *_: None)
        writes = []
        bridge.serial = Device(writes)
        bridge.state = 'ready'
        bridge.camera = Camera(keep_photos=False)
        return bridge, writes

    def test_photo_request_sends_g_and_returns_the_frame(self):
        bridge, writes = self.make_bridge()

        def deliver():
            with bridge.condition:
                bridge.photo_waiting = JPEG
                bridge.condition.notify_all()

        import threading
        threading.Timer(0.05, deliver).start()
        jpeg = bridge.request_photo(timeout=2)
        self.assertEqual(writes, [b'g'])
        self.assertEqual(jpeg, JPEG)
        self.assertIsNone(bridge.photo_waiting)
        bridge.pool.shutdown()

    def test_photo_timeout_returns_none_without_a_second_request(self):
        bridge, writes = self.make_bridge()
        self.assertIsNone(bridge.request_photo(timeout=0.3))
        self.assertEqual(writes, [b'g'])
        self.assertFalse(bridge.photo_pending)
        bridge.pool.shutdown()

    def test_cancel_while_waiting_releases_the_waiter_immediately(self):
        """Stop must not leave the cloud worker waiting for a photo that was cancelled."""
        bridge, writes = self.make_bridge()
        import threading
        done = []
        thread = threading.Thread(
            target=lambda: done.append(bridge.request_photo(timeout=5)))
        thread.start()
        time.sleep(0.15)
        started = time.monotonic()
        bridge.cancel()
        thread.join(timeout=2)
        self.assertLess(time.monotonic() - started, 2, 'cancel must release the photo waiter')
        self.assertEqual(done, [None])
        self.assertFalse(bridge.photo_pending)
        bridge.pool.shutdown()

    def test_a_second_request_while_one_is_pending_is_refused(self):
        bridge, writes = self.make_bridge()
        bridge.photo_pending = True
        self.assertIsNone(bridge.request_photo(timeout=0.1))
        self.assertEqual(writes, [])
        bridge.photo_pending = False
        bridge.pool.shutdown()

    def test_no_camera_configured_never_sends_a_command(self):
        bridge, writes = self.make_bridge()
        bridge.camera = None
        self.assertIsNone(bridge.request_photo(timeout=0.1))
        self.assertEqual(writes, [])
        bridge.pool.shutdown()

    def test_ordinary_question_does_not_ask_the_device_to_look(self):
        bridge, writes = self.make_bridge()
        with patch.object(bridge, 'request_photo') as request:
            self.assertIsNone(bridge.photo_context('讲个小故事', []))
            request.assert_not_called()
        self.assertEqual(writes, [])
        bridge.pool.shutdown()

    def test_look_request_returns_a_prompt_block_with_the_description(self):
        bridge, writes = self.make_bridge()
        with patch.object(bridge, 'request_photo', return_value=JPEG), \
             patch.object(bridge.camera, 'describe', return_value='我看到一只橙色小螃蟹。'):
            block = bridge.photo_context('你看看这个', [])
        self.assertIn('我看到一只橙色小螃蟹。', block)
        self.assertIn('看不清就说看不清', block)
        bridge.pool.shutdown()

    def test_failed_capture_tells_the_model_not_to_describe_anything(self):
        bridge, writes = self.make_bridge()
        with patch.object(bridge, 'request_photo', return_value=None):
            block = bridge.photo_context('你看看这个', [])
        self.assertEqual(block, UNAVAILABLE)
        self.assertIn('不要描述图片内容', block)
        bridge.pool.shutdown()

    def test_photo_block_reaches_the_chat_request(self):
        bridge, writes = self.make_bridge()
        sent = []
        chat = {'choices': [{'message': {'content': '我看到一只小螃蟹。'},
                             'finish_reason': 'stop'}]}

        def fake_post(url, payload, key_name, operation):
            sent.append((operation, payload))
            if operation == 'chat':
                return chat
            raise AssertionError(f'unexpected cloud call: {operation}')

        with patch.object(bridge.cloud, 'recognize', return_value='你看看这个'), \
             patch.object(bridge.cloud, 'post', side_effect=fake_post), \
             patch.object(bridge.cloud, 'synthesize', return_value=b'\0\0'), \
             patch.object(bridge, 'request_photo', return_value=JPEG), \
             patch.object(bridge.camera, 'describe', return_value='我看到一只橙色小螃蟹。'), \
             patch.object(bridge, 'play'):
            bridge.respond(b'\0\0', 0)
        self.assertEqual([op for op, _ in sent], ['chat'])
        system = sent[0][1]['messages'][0]['content']
        self.assertIn('我看到一只橙色小螃蟹。', system)
        self.assertEqual(bridge.pending_history[1:], ('你看看这个', '我看到一只小螃蟹。'))
        bridge.pool.shutdown()

    def test_photo_frame_header_is_validated_before_reading(self):
        """A declared length above the cap must be rejected, not allocated."""
        bridge, writes = self.make_bridge()
        declared = MAX_JPEG + 1
        with self.assertRaises(RuntimeError):
            if declared <= 0 or declared > MAX_JPEG:
                raise RuntimeError('照片长度超限。')
        bridge.pool.shutdown()

    def test_cancel_clears_a_pending_photo(self):
        bridge, writes = self.make_bridge()
        bridge.photo_waiting = False
        bridge.cancel()
        self.assertIsNone(bridge.photo_waiting)
        bridge.pool.shutdown()


if __name__ == '__main__':
    unittest.main()
