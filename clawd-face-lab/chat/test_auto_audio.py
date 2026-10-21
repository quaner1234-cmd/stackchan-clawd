from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import MagicMock,patch
import numpy as np
from bridge import Bridge
from pc_audio import AutoAudioMonitor,FRAME_BYTES,SpeakerSession,load_auto_enabled,save_auto_enabled

QUIET=b'\0'*FRAME_BYTES
LOUD=(np.ones(480,dtype='<i2')*1500).tobytes()


class ActivityTests(unittest.TestCase):
    def setUp(self):self.m=AutoAudioMonitor()
    def sound(self,start=0,seconds=1.2):
        for t in np.arange(start,start+seconds,.02):self.m.feed(LOUD,float(t))
    def silence(self,start,seconds):
        for t in np.arange(start,start+seconds,.02):self.m.feed(QUIET,float(t))
    def test_short_notifications_do_not_start_and_audio_requires_one_second(self):
        self.sound(seconds=.3);self.silence(.3,.4);self.assertFalse(self.m.active(.7))
        self.sound(start=1,seconds=.9);self.assertFalse(self.m.active(1.9))
        self.sound(start=1.9,seconds=.4);self.assertTrue(self.m.active(2.3))
    def test_song_gap_does_not_exit_until_ten_seconds_quiet(self):
        self.sound();self.silence(1.2,9);self.assertFalse(self.m.quiet(10.2))
        self.silence(10.2,1.2);self.assertTrue(self.m.quiet(11.4))
    def test_manual_stop_blocks_same_audio_until_real_silence_then_new_episode(self):
        self.sound();self.m.suppress();self.sound(1.2,2)
        self.assertFalse(self.m.active(3.2));self.assertTrue(self.m.blocked)
        self.silence(3.2,1.2);self.assertFalse(self.m.blocked)
        self.sound(4.4,1.2);self.assertTrue(self.m.active(5.6))
    def test_pcm_retention_is_bounded_and_bad_length_rejected(self):
        self.sound(seconds=20);self.assertEqual(len(self.m.pcm),FRAME_BYTES)
        self.assertEqual(sum(len(pcm) for _,pcm in self.m.audio),3*FRAME_BYTES)
        with self.assertRaises(RuntimeError):self.m.feed(LOUD*2)
    def test_burst_capture_preserves_each_frame_in_order(self):
        self.m.ready=True
        with self.m.source() as source:
            self.m.feed(LOUD,0);self.m.feed(QUIET,.02)
            self.assertEqual(source.read(),LOUD);self.assertEqual(source.read(),QUIET)
    def test_shared_source_opens_only_one_capture_and_releases_it(self):
        opened=[];closed=[]
        class Source:
            name='fixture'
            def __enter__(self):opened.append(1);return self
            def __exit__(self,*_):closed.append(1)
            def read(self):time.sleep(.005);return LOUD
            def check_device(self):pass
        m=AutoAudioMonitor(Source);m.start()
        try:
            deadline=time.monotonic()+2
            while not m.ready and time.monotonic()<deadline:time.sleep(.01)
            with m.source() as source:
                self.assertEqual(source.read(),LOUD);self.assertTrue(source.shared)
            self.assertEqual(len(opened),1);self.assertEqual(closed,[])
        finally:m.stop();m.thread.join(2)
        self.assertEqual(closed,[1]);self.assertEqual(m.pcm,b'')
    def test_settings_default_off_and_persist_only_switch(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            p=Path(tmp)/'audio.json';self.assertFalse(load_auto_enabled(p))
            save_auto_enabled(True,p);self.assertTrue(load_auto_enabled(p))
            self.assertEqual(p.read_text().strip(),'{"auto_enabled": true}')
            save_auto_enabled(False,p);self.assertFalse(load_auto_enabled(p))


class AutoBridgeTests(unittest.TestCase):
    def setUp(self):
        self.b=Bridge('unused',lambda *_:None);self.addCleanup(self.b.pool.shutdown,False)
        self.b.state='ready';self.b.device_pc_audio=True;self.b.device_scenes=True
        self.b.connection_event=MagicMock();self.b.serial=MagicMock();self.b.serial.is_open=True
        self.m=AutoAudioMonitor();self.m.ready=True;self.m.sound_since=time.monotonic()-2
        self.m.last_sound=time.monotonic();self.b.pc_watch=self.m;self.b.set_pc_auto(True)
    def test_idle_sound_requests_audio_but_busy_chat_photo_dance_cannot_be_taken_over(self):
        for phase in ('listening','thinking','uploading','speaking','dancing'):
            self.b.phase=phase;self.b.service_pc_auto();self.assertFalse(self.b.pc_requested)
        self.b.phase='idle';self.b.photo_preview=True;self.b.service_pc_auto();self.assertFalse(self.b.pc_requested)
        self.b.photo_preview=False;self.b.service_pc_auto()
        self.assertTrue(self.b.pc_requested);self.assertTrue(self.b.pc_auto_playing)
    def test_quiet_stops_automatic_audio_but_preserves_manual_audio(self):
        self.b.pc_session=SpeakerSession(self.b);self.b.pc_auto_playing=True
        self.m.quiet_since=time.monotonic()-11;self.b.service_pc_auto()
        self.assertTrue(self.b.pc_session.stop_event.is_set());self.assertFalse(self.b.pc_auto_playing)
        self.b.pc_session=SpeakerSession(self.b);self.b.pc_auto_playing=False
        self.b.service_pc_auto();self.assertFalse(self.b.pc_session.stop_event.is_set())
    def test_stop_and_listen_suppress_reentry_during_same_music(self):
        self.b.service_pc_auto();self.b.cancel();self.assertTrue(self.m.blocked)
        self.b.service_pc_auto();self.assertFalse(self.b.pc_requested)
        self.m.blocked=False;self.b.listen();self.assertTrue(self.m.blocked)
    def test_priority_alarm_and_hourly_requests_prevent_auto_start(self):
        self.b.alarm_requested=True;self.b.service_pc_auto();self.assertFalse(self.b.pc_requested)
        self.b.alarm_requested=False;self.b.hourly_requested=True
        self.b.service_pc_auto();self.assertFalse(self.b.pc_requested)
    def test_hourly_yield_does_not_exit_on_silence_while_report_owns_usb(self):
        self.b.pc_session=SpeakerSession(self.b);self.b.pc_auto_playing=True;self.b.pc_hourly_resume=True
        self.m.quiet_since=time.monotonic()-11;self.b.service_pc_auto()
        self.assertFalse(self.b.pc_session.stop_event.is_set());self.assertTrue(self.b.pc_hourly_resume)
    def test_switch_off_releases_monitor_and_default_never_captures(self):
        self.b.set_pc_auto(False);self.b.service_pc_auto()
        self.assertTrue(self.m.stop_event.is_set());self.assertIsNone(self.b.pc_watch)
        with patch('bridge.AutoAudioMonitor') as monitor:self.b.service_pc_auto();monitor.assert_not_called()
    def test_manual_request_waits_for_monitor_without_claiming_usb_early(self):
        self.m.ready=False;self.b.request_pc_audio();self.b.service_pc_audio()
        self.assertEqual(self.b.phase,'idle');self.assertTrue(self.b.pc_requested)
        self.b.serial.write.assert_not_called()
    def test_monitor_failure_is_reported_once_and_does_not_restart(self):
        self.m.error='fixture endpoint removed'
        self.b.service_pc_auto();self.b.service_pc_auto()
        self.b.connection_event.assert_called_once_with('pc_auto_error')
        self.assertFalse(self.b.pc_requested)


if __name__=='__main__':unittest.main()
