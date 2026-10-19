import threading
import binascii
import io
import unittest
from concurrent.futures import Future
from unittest.mock import patch
from bridge import Bridge


class FakeEngine:
    def __init__(self):self.fed=0;self.resets=0
    def reset(self):self.resets+=1
    def feed(self,*args):self.fed+=1;return True
    def metrics(self):return {}


class WakeTests(unittest.TestCase):
    def setUp(self):
        self.b=Bridge('unused',lambda *_:None)
        self.b.state='ready';self.b.device_wake=True;self.b.set_wake(True)
        self.b.wake_engine=FakeEngine()
        self.writes=[]
        class Device:
            is_open=True
            def write(_,data):self.writes.append(data)
        self.b.serial=Device()
        self.b.connection_event=lambda *_,**__:None
        self.b.cloud.recognize=lambda *_:self.fail('Standby must never call cloud')
        self.addCleanup(self.b.pool.shutdown)

    def arm(self):
        self.b.service_wake();self.b.wake_session=7

    def test_hit_uses_existing_listen_once_and_rejects_late_frames(self):
        self.arm()
        self.b.wake_frame(7,0,b'\0'*4800)
        self.b.wake_frame(7,1,b'\0'*4800)
        self.assertEqual(self.writes,[b'L',b'U',b'r'])
        self.assertEqual(self.b.phase,'arming')
        self.assertTrue(self.b.wake_first_record)
        self.assertTrue(self.b.continuous_active)

    def test_disable_closes_idle_mic_and_no_cloud_or_new_listen(self):
        self.arm();self.b.set_wake(False);self.b.service_wake()
        self.b.wake_frame(7,0,b'\0'*4800)
        self.assertEqual(self.writes,[b'L',b'U'])
        self.assertEqual(self.b.wake_engine.fed,0)

    def test_touch_competes_with_wake_no_duplicate_record(self):
        self.arm();self.b.listen()
        self.b.wake_frame(7,0,b'\0'*4800)
        self.assertEqual(self.writes,[b'L',b'U',b'r'])
        self.assertFalse(self.b.wake_first_record)

    def test_cancel_during_decode_drops_result(self):
        self.arm()
        def feed(*_):self.b.cancel();return True
        self.b.wake_engine.feed=feed
        self.b.wake_frame(7,0,b'\0'*4800)
        self.assertEqual(self.writes,[b'L',b'U',b'x'])

    def test_stale_session_is_drained_without_detection(self):
        self.arm();self.b.wake_frame(6,0,b'\0'*4800)
        self.assertEqual(self.b.wake_engine.fed,0)

    def test_all_busy_modes_suspend_and_rearm(self):
        for attribute,value in [('phase','listening'),('phase','thinking'),('phase','speaking'),
                ('phase','dancing'),('phase','arming'),('uploading',True),('photo_pending',True),
                ('photo_preview',True),('dance_token',0),('pending_dance',0),
                ('parent_capture',True),('parent_restart_at',10),('continuous_active',True),
                ('next_listen_at',10),('future',Future())]:
            with self.subTest(attribute=attribute,value=value):
                old=getattr(self.b,attribute)
                setattr(self.b,attribute,value)
                self.assertFalse(self.b.wake_eligible())
                setattr(self.b,attribute,old)
        self.assertTrue(self.b.wake_eligible())

    def test_budgets_and_disconnect_refuse_wake(self):
        self.b.turns=20;self.assertFalse(self.b.wake_eligible())
        self.b.turns=0;self.b.closed.set();self.assertFalse(self.b.wake_eligible())

    def test_decode_failure_leaves_touch_working(self):
        self.arm()
        def broken(*_):raise RuntimeError('test')
        self.b.wake_engine.feed=broken
        self.b.wake_frame(7,0,b'\0'*4800)
        self.assertFalse(self.b.wake_enabled)
        self.b.listen()
        self.assertEqual(self.writes,[b'L',b'U',b'r'])

    def test_old_firmware_never_gets_wake_commands(self):
        self.b.device_wake=False;self.b.service_wake()
        self.assertEqual(self.writes,[])

    def test_cancelled_binary_frame_is_drained_without_cloud_or_disconnect(self):
        for gui_cancel in (False,True):
            with self.subTest(gui_cancel=gui_cancel):
                b=Bridge('unused',lambda *_:None)
                b.connection_event=lambda *_,**__:None
                pcm=b'\0'*4800
                wire=(b'CLAWD READY test\nCLAWD CHAT_READY pcm_status=1\n'+
                      f'CLAWD_PCM 24000 4800 {binascii.crc32(pcm)}\n'.encode()+pcm+
                      b'\nCLAWD_PCM_END\nCLAWD PCM_STATUS cancelled='+
                      (b'0' if gui_cancel else b'1')+b'\nCLAWD CANCELLED\n')
                class Serial:
                    is_open=True
                    def __init__(self,**_):self.wire=io.BytesIO(wire);self.cancelled=False
                    def open(self):pass
                    def close(self):self.is_open=False
                    def write(self,data):return len(data)
                    def readline(self):
                        line=self.wire.readline()
                        if not line:b.closed.set()
                        return line
                    def read(self,n):
                        if gui_cancel and not self.cancelled:
                            self.cancelled=True;b.cancel()
                        return self.wire.read(n)
                try:
                    with patch('bridge.serial.Serial',Serial),patch.object(b.pool,'submit') as cloud:
                        b.run()
                        cloud.assert_not_called()
                    self.assertNotEqual(b.state,'error')
                    self.assertEqual(b.turns,0)
                finally:b.pool.shutdown()


if __name__=='__main__':unittest.main()
