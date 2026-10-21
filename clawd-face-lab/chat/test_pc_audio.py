import binascii
import struct
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import numpy as np
from bridge import Bridge
from pc_audio import packet, mono_pcm, SpeakerSession, FRAME_BYTES, WINDOW
from daily_routines import DailyRoutines
from idle_scenes import BEIJING


class AudioPacketTests(unittest.TestCase):
    def test_crc_protects_header_and_pcm_even_command_like_bytes(self):
        pcm=(b'qxrhF\x00'*160)
        data=packet(123,pcm)
        self.assertEqual(struct.unpack('<4sIHH',data[:12]),(b'KPCM',123,960,0))
        self.assertEqual(struct.unpack('<I',data[12:16])[0],binascii.crc32(data[:12]+data[16:]))
        changed=bytearray(data);changed[4]^=1
        self.assertNotEqual(binascii.crc32(changed[:12]+changed[16:]),struct.unpack('<I',data[12:16])[0])
        self.assertEqual(len(packet(1,flags=1)),16)
        for payload,flag in [(b'x',0),(b'xx',1),(struct.pack('<H',101),2)]:
            with self.assertRaises(ValueError):packet(0,payload,flag)

    def test_stereo_downmix_finite_clipped_signed_pcm(self):
        data=np.zeros((480,2),dtype='float32')
        data[:4]=[[1,1],[-1,-1],[.5,-.5],[float('nan'),0]]
        pcm=np.frombuffer(mono_pcm(data),dtype='<i2')
        self.assertEqual(pcm[:4].tolist(),[32767,-32768,0,0])
        with self.assertRaises(ValueError):mono_pcm(np.zeros((480,1)))


class SpeakerStateTests(unittest.TestCase):
    def setUp(self):
        self.b=Bridge('unused',lambda *_:None);self.addCleanup(self.b.pool.shutdown,False)
        self.b.state='ready';self.b.device_scenes=True;self.b.device_pc_audio=True
        self.b.connection_event=MagicMock();self.b.serial=MagicMock();self.b.serial.is_open=True

    def test_feature_requires_idle_and_supported_firmware(self):
        self.b.phase='listening';self.assertFalse(self.b.request_pc_audio())
        self.b.phase='idle';self.b.device_pc_audio=False;self.assertFalse(self.b.request_pc_audio())
        self.b.device_pc_audio=True;self.assertTrue(self.b.request_pc_audio())
        self.assertFalse(self.b.wake_eligible());self.assertFalse(self.b.scene_idle())

    def test_pending_stream_waits_for_actual_wake_release(self):
        self.b.request_pc_audio();self.b.wake_requested=True;self.b.wake_device_active=True
        self.b.service_pc_audio();self.b.serial.write.assert_called_once_with(b'U')
        self.b.service_pc_audio();self.assertIsNone(self.b.pc_session)
        self.b.request_pc_audio(False);self.assertFalse(self.b.pc_requested)

    def test_no_unframed_command_can_enter_live_pcm(self):
        session=SpeakerSession(self.b);self.b.pc_session=session
        self.b.send(b'r');self.b.send(b'k');self.b.send(b'g');self.b.send(b'G'+b'0'*10)
        self.b.serial.write.assert_not_called()
        self.b.cancel(reset=True);self.assertTrue(session.stop_event.is_set())
        self.b.serial.write.assert_not_called()

    def test_listen_waits_until_terminal_ack_releases_usb(self):
        session=SpeakerSession(self.b);self.b.pc_session=session;self.b.phase='pc_audio'
        self.b.listen();self.assertTrue(self.b.pc_resume);self.b.serial.write.assert_not_called()
        session.ready=True;session.device_done=True;session.done.set()
        self.b.service_pc_audio();self.assertIsNone(self.b.pc_session)
        self.b.serial.write.assert_any_call(b'r');self.assertEqual(self.b.phase,'arming')

    def test_volume_during_stream_uses_framed_path_only(self):
        self.b.pc_session=SpeakerSession(self.b)
        self.assertTrue(self.b.request_control('volume',45));self.assertEqual(self.b.pc_session.volume,45)
        self.assertFalse(self.b.request_control('pose',0,150));self.b.serial.write.assert_not_called()

    def test_missing_terminal_confirmation_forces_reconnect(self):
        session=SpeakerSession(self.b);session.ready=True;session.done.set();self.b.pc_session=session
        with self.assertRaises(RuntimeError):self.b.service_pc_audio()

    def test_stream_does_not_consume_chat_time(self):
        self.b.pc_session=SpeakerSession(self.b);self.b.phase='pc_audio'
        self.b.service_wake();self.assertEqual(self.b.activity_s,0)
        self.b.started-=4000
        self.assertFalse(self.b.enforce_chat_limit())

    def test_due_alarm_stops_stream_without_injecting_alarm_bytes(self):
        self.b.pc_session=SpeakerSession(self.b)
        with patch.object(self.b.workday_alarm,'due',return_value='scheduled-slot'):
            self.b.service_alarm()
        self.assertTrue(self.b.pc_session.stop_event.is_set())
        self.b.serial.write.assert_not_called()
        self.assertIsNone(self.b.alarm_token)


class HourlySpeakerTests(unittest.TestCase):
    def setUp(self):
        SpeakerStateTests.setUp(self)
        self.tmp=TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.now=datetime(2026,10,20,20,0,1,tzinfo=BEIJING)
        self.b.daily=DailyRoutines(Path(self.tmp.name)/'daily.json',self.now)
        self.b.wake_enabled=True;self.b.device_wake=True
        self.session=SpeakerSession(self.b);self.b.pc_session=self.session
        self.b.pc_requested=True;self.b.phase='pc_audio'
        self.submit=patch.object(self.b.pool,'submit').start();self.addCleanup(patch.stopall)
        self.submit.return_value.done.return_value=True

    def release(self):
        self.session.device_done=True;self.session.done.set()
        self.b.service_pc_audio();self.b.wake_retry_at=0

    def test_hourly_yields_framed_usb_then_resumes_only_after_play_done(self):
        self.b.service_daily(self.now)
        self.assertTrue(self.session.stop_event.is_set());self.assertTrue(self.b.pc_hourly_resume)
        self.b.serial.write.assert_not_called();self.submit.assert_not_called()
        self.assertFalse(self.b.wake_eligible())
        self.release();self.b.service_daily(self.now)
        self.assertEqual(self.b.reminder_kind,'hourly');self.assertEqual(self.submit.call_count,1)
        self.assertFalse(self.b.pc_requested);self.assertTrue(self.b.pc_hourly_resume)
        self.b.service_daily(self.now);self.assertFalse(self.b.pc_requested)
        self.b.hourly_play_done(b'CLAWD PLAY_DONE cancelled=0')
        self.assertTrue(self.b.pc_requested);self.assertFalse(self.b.pc_hourly_resume)
        self.b.service_daily(self.now);self.assertEqual(self.submit.call_count,1)
        self.assertEqual(self.b.cloud.calls,0)

    def test_manual_preview_can_interrupt_with_hourly_switch_off(self):
        self.b.daily.hourly_enabled=False;self.b.request_hourly()
        self.b.service_daily(self.now);self.assertTrue(self.session.stop_event.is_set())
        self.release();self.b.service_daily(self.now)
        self.assertEqual(self.submit.call_count,1);self.assertEqual(self.b.daily.last_hour,'')

    def test_night_and_disabled_schedule_leave_music_uninterrupted(self):
        self.b.daily.hourly_enabled=False;self.b.service_daily(self.now)
        self.assertFalse(self.session.stop_event.is_set())
        self.b.daily.hourly_enabled=True;self.b.daily.completed_night=''
        self.b.service_daily(self.now);self.assertFalse(self.session.stop_event.is_set())

    def test_user_stop_while_yielding_cannot_be_rearmed_by_same_due_hour(self):
        self.b.service_daily(self.now);self.b.request_pc_audio(False)
        self.b.service_daily(self.now);self.assertFalse(self.b.pc_hourly_resume)
        self.release();self.b.service_daily(self.now)
        self.b.hourly_play_done(b'CLAWD PLAY_DONE cancelled=0')
        self.assertFalse(self.b.pc_requested)

    def test_cancel_during_report_never_restarts_music(self):
        self.b.service_daily(self.now);self.release();self.b.service_daily(self.now)
        self.b.cancel();self.b.hourly_play_done(b'CLAWD PLAY_DONE cancelled=1')
        self.assertFalse(self.b.pc_requested);self.assertFalse(self.b.pc_hourly_resume)

    def test_listen_during_yield_replaces_auto_resume_with_chat(self):
        self.b.service_daily(self.now);self.b.listen()
        self.assertFalse(self.b.pc_hourly_resume);self.release()
        self.assertEqual(self.b.phase,'arming');self.assertFalse(self.b.pc_requested)

    def test_alarm_overrides_pending_auto_resume(self):
        self.b.service_daily(self.now)
        with patch.object(self.b.workday_alarm,'due',return_value='alarm-slot'):
            self.b.service_alarm()
        self.assertFalse(self.b.pc_hourly_resume);self.assertTrue(self.session.stop_event.is_set())

    def test_missing_asset_restores_music_instead_of_stranding_it(self):
        self.b.service_daily(self.now);self.release()
        with patch('bridge.hourly_pcm',side_effect=OSError):self.b.service_daily(self.now)
        self.assertTrue(self.b.pc_requested);self.assertIsNone(self.b.reminder_token)
        self.assertFalse(self.b.pc_hourly_resume);self.submit.assert_not_called()

    def test_disabled_or_expired_while_stopping_restores_music_without_stale_report(self):
        self.b.service_daily(self.now);self.release();self.b.daily.hourly_enabled=False
        self.b.service_daily(self.now)
        self.assertTrue(self.b.pc_requested);self.submit.assert_not_called()

    def test_stream_error_drops_resume_intent(self):
        self.b.service_daily(self.now);self.session.error='source gone';self.release()
        self.assertFalse(self.b.pc_hourly_resume);self.assertFalse(self.b.pc_requested)


class SessionFlowTests(unittest.TestCase):
    def test_ack_window_bounds_unconfirmed_audio_and_stop_is_framed(self):
        class Source:
            name='synthetic';count=0
            def __enter__(self):return self
            def __exit__(self,*_):pass
            def check_device(self):pass
            def read(self):time.sleep(.001);return b'\0'*FRAME_BYTES
        b=MagicMock();b.closed=threading.Event();b.write_lock=threading.RLock()
        session=SpeakerSession(b,Source);sent=[]
        def write(data):
            sent.append(data)
            if struct.unpack('<H',data[10:12])[0]==1:session.event(b'CLAWD STREAM_DONE terminal=1')
            return len(data)
        b.serial.write.side_effect=write
        session.event(b'CLAWD STREAM_READY');session.start()
        deadline=time.monotonic()+2
        while len(sent)<WINDOW and time.monotonic()<deadline:time.sleep(.005)
        time.sleep(.04);self.assertEqual(len(sent),WINDOW)
        session.stop();session.thread.join(2)
        self.assertTrue(session.done.is_set());self.assertEqual(len(sent),WINDOW+1)
        self.assertEqual(struct.unpack('<H',sent[-1][10:12])[0],1)
        self.assertTrue(session.device_done);self.assertEqual(session.audio.qsize(),0)


if __name__=='__main__':unittest.main()
