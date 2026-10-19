from concurrent.futures import Future
from datetime import date, datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from unittest.mock import patch
from bridge import Bridge
from idle_scenes import BEIJING
from workday_alarm import ChinaWorkdays, WorkdayAlarm, alarm_pcm
from daily_routines import DailyRoutines


def at(day=19,hour=6,minute=40,second=0,year=2026,month=10):
    return datetime(year,month,day,hour,minute,second,tzinfo=BEIJING)


class CalendarTests(unittest.TestCase):
    def test_official_makeup_weekends_and_national_day_boundaries(self):
        calendar=ChinaWorkdays()
        for day in ['2026-01-04','2026-02-14','2026-02-28','2026-05-09','2026-09-20','2026-10-10']:
            self.assertTrue(calendar.is_workday(date.fromisoformat(day)),day)
        for day in ['2026-01-01','2026-02-23','2026-04-06','2026-05-05','2026-06-19','2026-09-25','2026-10-01','2026-10-07','2026-10-11','2026-10-18']:
            self.assertFalse(calendar.is_workday(date.fromisoformat(day)),day)
        for day in ['2026-09-30','2026-10-08','2026-10-19']:
            self.assertTrue(calendar.is_workday(date.fromisoformat(day)),day)
        self.assertEqual(len(calendar.years[2026][0]),33)

    def test_unknown_year_has_no_weekday_fallback(self):
        self.assertIsNone(ChinaWorkdays().is_workday(date(2027,1,4)))

    def test_malformed_calendar_is_unavailable(self):
        with TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            path=Path(tmp)/'calendar.json';path.write_text('{bad')
            self.assertIsNone(ChinaWorkdays(path).is_workday(date(2026,10,19)))


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'settings.json';self.alarm=WorkdayAlarm(self.path)

    def test_exact_minute_beijing_and_next_alarm(self):
        self.assertIsNone(self.alarm.due(at(minute=39,second=59)))
        slot=self.alarm.due(at().astimezone(timezone.utc));self.assertEqual(slot,'2026-10-19')
        self.alarm.claim(slot)
        self.assertIsNone(self.alarm.due(at(second=50)))
        self.assertEqual(self.alarm.next_alarm(at()),at(day=20))

    def test_claim_survives_relaunch_and_clock_rollback(self):
        self.alarm.claim(self.alarm.due(at()))
        restored=WorkdayAlarm(self.path)
        self.assertIsNone(restored.due(at(second=20)))
        self.assertIsNone(restored.due(at(day=16)))
        self.assertEqual(restored.due(at(day=20)),'2026-10-20')

    def test_busy_or_reconnected_within_five_minutes_gets_alarm(self):
        self.assertEqual(self.alarm.due(at(minute=41)),'2026-10-19')
        slot=self.alarm.due(at())
        self.assertEqual(self.alarm.due(at(minute=44,second=59)),slot)
        self.assertIsNone(self.alarm.due(at(minute=45)))

    def test_holidays_regular_weekends_and_unknown_year_are_silent(self):
        for now in [at(day=18),at(day=1),at(day=4,year=2027,month=1)]:
            self.assertIsNone(self.alarm.due(now))
        self.assertEqual(self.alarm.due(at(day=10)),'2026-10-10')

    def test_disable_persists_and_drops_pending(self):
        self.alarm.due(at());self.alarm.set_enabled(False)
        self.assertIsNone(self.alarm.due(at(minute=41)))
        self.assertFalse(WorkdayAlarm(self.path).enabled)

    def test_next_time_from_today_is_monday_not_sunday(self):
        self.assertEqual(self.alarm.next_alarm(at(day=18,hour=20,minute=0)),at())
        self.assertIsNone(self.alarm.next_alarm(at(day=31,hour=23,minute=0,month=12)))

    def test_fixed_pcm_is_valid_short_and_reusable(self):
        self.assertTrue(48000<len(alarm_pcm())<24000*12*2)


class BridgeAlarmTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.b=Bridge('unused',lambda *_:None)
        self.b.workday_alarm=WorkdayAlarm(Path(self.tmp.name)/'settings.json')
        self.b.daily=DailyRoutines(Path(self.tmp.name)/'daily.json',at(hour=10))
        self.b.device_scenes=True;self.b.device_wake=True;self.b.state='ready'
        self.b.connection_event=lambda *_a,**_kw:None
        self.writes=[]
        class Device:
            is_open=True
            def write(_,data):self.writes.append(data)
        self.b.serial=Device();self.addCleanup(self.b.pool.shutdown)

    def test_busy_is_not_interrupted_then_schedule_is_claimed_once(self):
        self.b.phase='speaking';self.b.service_alarm(at());self.assertEqual(self.writes,[])
        self.b.phase='idle';self.b.service_alarm(at(minute=41))
        self.assertEqual(self.b.alarm_remaining,3);self.assertEqual(self.b.phase,'alarming')
        self.assertEqual(self.writes,[b'i']);self.assertFalse(self.b.wake_eligible())
        self.assertFalse(self.b.scene_idle());self.assertEqual(self.b.workday_alarm.last,'')
        self.b.claim_alarm();self.assertEqual(self.b.workday_alarm.last,'2026-10-19')

    def test_three_playbacks_then_release_and_no_cloud(self):
        self.b.service_alarm(at())
        played=[]
        with patch.object(self.b,'play',side_effect=lambda *_a,**_kw:played.append(True)):
            for i in range(3):
                self.b.alarm_next_at=0;self.b.service_alarm(at())
                self.b.future.result(2)
                self.assertTrue(self.b.alarm_play_done(b'CLAWD PLAY_DONE cancelled=0'))
            self.assertEqual(len(played),3)
        self.assertIsNone(self.b.alarm_token);self.assertEqual(self.b.phase,'idle')
        self.b.service_alarm(at(second=30));self.assertEqual(len(played),3)
        self.assertEqual(self.b.cloud.calls,0)

    def test_cancel_during_gap_cannot_restart(self):
        self.b.service_alarm(at());token=self.b.alarm_token
        self.b.cancel();self.b.service_alarm(at(second=45))
        with patch.object(self.b,'play') as play:
            self.b.play_alarm(b'\0\0',token);play.assert_not_called()
        self.assertIsNone(self.b.alarm_token)

    def test_local_alarm_survives_chat_guard_without_reopening_cloud(self):
        self.b.activity_s=1801
        self.assertFalse(self.b.enforce_chat_limit())
        self.assertTrue(self.b.chat_expired);self.assertFalse(self.b.wake_eligible())
        self.b.listen();self.assertNotIn(b'r',self.writes)
        self.assertFalse(self.b.arm_parent())
        self.b.wake_retry_at=0  # The cancel/codec cooldown has completed.
        self.b.service_alarm(at());self.assertIsNotNone(self.b.alarm_token)
        self.b.workday_alarm.enabled=False
        self.b.daily.hourly_enabled=False;self.b.daily.sleep_enabled=False
        self.assertTrue(self.b.enforce_chat_limit())

    def test_disabled_schedule_keeps_manual_test_available(self):
        self.b.workday_alarm.enabled=False;self.b.request_alarm();self.b.service_alarm(at())
        self.assertIsNotNone(self.b.alarm_token);self.assertFalse(self.b.alarm_scheduled)
        self.assertEqual(self.b.workday_alarm.last,'')

    def test_alarm_waits_for_cancel_codec_cooldown(self):
        import time
        self.b.wake_retry_at=time.monotonic()+10
        self.b.request_alarm();self.b.service_alarm(at())
        self.assertIsNone(self.b.alarm_token)
        self.b.wake_retry_at=0;self.b.service_alarm(at())
        self.assertIsNotNone(self.b.alarm_token)

    def check_boot_lines(self, reboot_after_ready=False):
        self.b.state='connecting'
        self.b.workday_alarm.enabled=False;self.b.drink_reminder.enabled=False
        events=[];self.b.connection_event=lambda event,**kw:events.append(event)
        lines=[b'CLAWD emotion=neutral\n',b'CLAWD READY version=1.5.1-clawd.3\n',
               b'CLAWD CHAT_READY rate=24000\n']
        if reboot_after_ready:lines.append(b'CLAWD READY version=1.5.1-clawd.3\n')
        owner=self
        class Device:
            is_open=True
            def open(self):pass
            def close(self):self.is_open=False
            def write(self,data):pass
            def readline(self):
                if lines:return lines.pop(0)
                owner.b.closed.set();return b''
        with patch('bridge.serial.Serial',return_value=Device()):self.b.run()
        return events

    def test_boot_ready_during_handshake_does_not_abort_connection(self):
        events=self.check_boot_lines()
        self.assertIn('ready',events);self.assertNotIn('error',events)

    def test_device_reboot_after_handshake_still_closes_old_connection(self):
        events=self.check_boot_lines(reboot_after_ready=True)
        self.assertIn('error',events);self.assertIn('已重启',self.b.last_error)


if __name__=='__main__':unittest.main()
