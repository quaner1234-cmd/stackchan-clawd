import unittest
from unittest.mock import patch

from bridge import Bridge


class DanceTests(unittest.TestCase):
    def make_bridge(self):
        bridge = Bridge('unused', lambda *_: None)
        self.addCleanup(bridge.pool.shutdown)
        bridge.state = 'ready'
        bridge.device_dance = True
        bridge.continuous_active = True
        writes = []

        class Device:
            is_open = True

            def write(self, data):
                writes.append(data)

        bridge.serial = Device()
        return bridge, writes

    def test_only_direct_invitations_trigger(self):
        for text in ('跳个舞', '小克，你跳个舞吧！', '请跳一段舞', '能不能给我跳支舞',
                     '你给我跳个舞看看', '请你跳个舞', '跳一下舞', '跳个舞蹈看看吧', '来跳舞', '表演一段舞蹈'):
            with self.subTest(text=text):
                self.assertTrue(Bridge.wants_dance(text))
        for text in ('不要跳舞', '别跳个舞', '你会跳舞吗', '我想跳舞', '我跳个舞给你看',
                     '讲个小兔子跳舞的故事', '跳舞是什么', '不要停下来，讲个跳舞的故事',
                     '先拍照再跳个舞', '小克再见'):
            with self.subTest(text=text):
                self.assertFalse(Bridge.wants_dance(text))

    def test_recognized_invitation_plays_intro_before_motion(self):
        bridge, writes = self.make_bridge()
        synthesized = []

        class Cloud:
            def recognize(self, pcm):
                return '小克，跳个舞吧'

            def synthesize(self, text):
                synthesized.append(text)
                return b'\0\0'

            def reply(self, *args, **kwargs):
                raise AssertionError('A direct dance invitation needs no chat-model call')

        bridge.cloud = Cloud()
        with patch.object(bridge, 'play') as play:
            bridge.respond(b'\0\0', bridge.epoch)
        play.assert_called_once()
        self.assertIn('小螃蟹', synthesized[0])
        self.assertEqual(writes, [])
        self.assertEqual(bridge.pending_dance, bridge.epoch)
        self.assertTrue(bridge.dance_after_playback(True))
        self.assertEqual(writes, [b'B'])
        self.assertEqual(bridge.phase, 'dancing')

    def test_no_microphone_until_dance_completion(self):
        bridge, writes = self.make_bridge()
        bridge.pending_dance = bridge.epoch
        with patch('bridge.time.monotonic', return_value=10):
            bridge.dance_after_playback(True)
        bridge.next_listen_at = 1  # Even a stale listening deadline must be held.
        with patch('bridge.time.monotonic', return_value=11):
            bridge.resume_if_due()
        self.assertEqual(writes, [b'B'])
        with patch('bridge.time.monotonic', return_value=18):
            bridge.dance_event(b'CLAWD DANCE_DONE cancelled=0 elapsed_ms=8000')
        self.assertEqual(bridge.next_listen_at, 18.65)
        with patch('bridge.time.monotonic', return_value=19):
            bridge.resume_if_due()
        self.assertEqual(writes, [b'B', b'r'])

    def test_cancelled_intro_never_starts_dance(self):
        bridge, writes = self.make_bridge()
        bridge.pending_dance = bridge.epoch
        bridge.cancel()
        self.assertFalse(bridge.dance_after_playback(True))
        self.assertEqual(writes, [b'x'])
        self.assertIsNone(bridge.dance_token)

    def test_cancelled_synthesis_never_queues_motion(self):
        bridge, writes = self.make_bridge()

        class Cloud:
            def recognize(self, pcm):
                return '跳个舞'

            def synthesize(self, text):
                bridge.cancel()
                return b'\0\0'

        bridge.cloud = Cloud()
        with patch.object(bridge, 'play') as play:
            bridge.respond(b'\0\0', bridge.epoch)
        play.assert_not_called()
        self.assertEqual(writes, [b'x'])
        self.assertIsNone(bridge.pending_dance)

    def test_cancelled_dance_does_not_rearm_microphone(self):
        bridge, _ = self.make_bridge()
        bridge.dance_token = bridge.epoch
        bridge.dance_deadline = 30
        bridge.dance_event(b'CLAWD DANCE_DONE cancelled=1 elapsed_ms=800')
        self.assertIsNone(bridge.next_listen_at)
        self.assertFalse(bridge.continuous_active)
        self.assertIsNone(bridge.dance_deadline)

    def test_timeout_stops_device_and_ignores_late_completion(self):
        bridge, writes = self.make_bridge()
        bridge.dance_token = bridge.epoch
        bridge.dance_deadline = 10
        with patch('bridge.time.monotonic', return_value=11), patch.object(bridge, 'connection_event'):
            bridge.check_dance_timeout()
        self.assertEqual(writes, [b'x'])
        bridge.dance_event(b'CLAWD DANCE_DONE cancelled=0 elapsed_ms=8000')
        self.assertIsNone(bridge.next_listen_at)
        self.assertIsNone(bridge.dance_token)

    def test_old_firmware_does_not_receive_unknown_command(self):
        bridge, writes = self.make_bridge()
        bridge.device_dance = False
        bridge.pending_dance = bridge.epoch
        self.assertFalse(bridge.dance_after_playback(True))
        self.assertEqual(writes, [])


if __name__ == '__main__':
    unittest.main()
