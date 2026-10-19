from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from bridge import Bridge
from daily_routines import DailyRoutines, hourly_pcm, hour_phrase
from idle_scenes import BEIJING


def at(day=19,hour=10,minute=0,second=0):
    return datetime(2026,10,day,hour,minute,second,tzinfo=BEIJING)


class ClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'daily.json';self.daily=DailyRoutines(self.path,at())

    def test_daytime_install_waits_until_23_and_survives_midnight(self):
        self.daily.save()
        for now in [at(),at(hour=22,minute=59,second=59)]:self.assertFalse(self.daily.sleeping(now))
        self.assertTrue(self.daily.sleeping(at(hour=23)))
        self.assertTrue(DailyRoutines(self.path,at(day=20,hour=1)).sleeping(at(day=20,hour=1)))

    def test_alarm_ends_current_night_and_next_night_starts_again(self):
        self.assertTrue(self.daily.sleeping(at(day=20,hour=6)))
        self.daily.alarm_started(at(day=20,hour=6,minute=40))
        self.assertFalse(self.daily.sleeping(at(day=20,hour=7)))
        self.assertTrue(self.daily.sleeping(at(day=20,hour=23)))

    def test_weekend_remains_visual_sleep_until_next_workday_alarm(self):
        self.assertTrue(self.daily.sleeping(at(day=24,hour=12)))
        self.assertIsNone(self.daily.hourly_due(at(day=24,hour=12)))
        self.daily.alarm_started(at(day=26,hour=6,minute=40))
        self.assertFalse(self.daily.sleeping(at(day=26,hour=7)))

    def test_hourly_window_dedup_reopen_and_clock_rollback(self):
        slot=self.daily.hourly_due(at().astimezone(timezone.utc))
        self.assertIsNotNone(slot);self.daily.claim_hour(slot)
        restored=DailyRoutines(self.path,at())
        self.assertIsNone(restored.hourly_due(at(second=30)))
        self.assertIsNone(restored.hourly_due(at(hour=9)))
        self.assertIsNotNone(restored.hourly_due(at(hour=11,minute=2,second=59)))
        self.assertIsNone(restored.hourly_due(at(hour=11,minute=3)))

    def test_switches_independent_and_persist(self):
        self.daily.set_enabled('hourly',False)
        self.assertIsNone(self.daily.hourly_due(at()))
        self.assertTrue(self.daily.sleeping(at(hour=23)))
        self.daily.set_enabled('sleep',False)
        self.assertFalse(DailyRoutines(self.path,at()).sleeping(at(hour=23)))
        self.daily.set_enabled('hourly',True)
        self.assertIsNotNone(self.daily.hourly_due(at(hour=23)))

    def test_all_24_cached_assets_are_valid_and_different(self):
        assets=[hourly_pcm(hour) for hour in range(24)]
        self.assertEqual(len(set(assets)),24)
        self.assertEqual(len(set(hour_phrase(hour) for hour in range(24))),24)
        for pcm in assets:self.assertTrue(12000<len(pcm)<480000)
        with self.assertRaises(ValueError):hourly_pcm(24)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path(__file__).parent);self.addCleanup(self.tmp.cleanup)
        self.b=Bridge('unused',lambda *_:None);self.addCleanup(self.b.pool.shutdown,False)
        self.b.daily=DailyRoutines(Path(self.tmp.name)/'daily.json',at())
        self.b.device_scenes=True;self.b.device_sleep=True;self.b.state='ready'
        self.writes=[];self.b.send=self.writes.append;self.b.wake_retry_at=0

    def test_sleep_does_not_stop_wake_or_claim_conversation(self):
        self.b.wake_enabled=True;self.b.device_wake=True
        self.b.service_daily(at(hour=23))
        self.assertIn(b'Z',self.writes);self.assertTrue(self.b.wake_eligible())
        self.assertIsNone(self.b.reminder_token);self.assertEqual(self.b.turns,0)
        self.b.phase='listening';self.b.service_daily(at(day=20,hour=1))
        self.assertEqual(self.writes.count(b'Z'),1)

    def test_alarm_has_priority_and_scheduled_alarm_ends_sleep(self):
        self.b.alarm_token=self.b.epoch;self.b.alarm_scheduled=True;self.b.alarm_slot=None
        with patch.object(self.b.daily,'alarm_started') as ended:
            self.b.claim_alarm();ended.assert_called_once()
        self.b.service_daily(at());self.assertIsNone(self.b.reminder_token)

    def test_preview_alarm_does_not_end_night(self):
        self.b.alarm_token=self.b.epoch;self.b.alarm_scheduled=False
        with patch.object(self.b.daily,'alarm_started') as ended:
            self.b.claim_alarm();ended.assert_not_called()

    def test_hourly_waits_for_busy_then_uses_shared_pcm_channel(self):
        self.b.phase='thinking'
        with patch.object(self.b.pool,'submit') as submit:
            self.b.service_daily(at());submit.assert_not_called()
            self.b.phase='idle';self.b.service_daily(at(minute=1))
            self.assertEqual(submit.call_count,1)
        self.assertEqual(self.b.reminder_kind,'hourly')
        self.assertEqual(self.b.reminder_token,self.b.epoch)
        self.assertEqual(self.b.daily.last_hour,at().isoformat())
        self.assertEqual(self.b.cloud.calls,0);self.assertEqual(self.b.turns,0)

    def test_no_sleep_command_inside_upload_or_repeated_each_tick(self):
        self.b.uploading=True;self.b.service_daily(at(hour=23));self.assertEqual(self.writes,[])
        self.b.uploading=False;self.b.service_daily(at(hour=23));self.b.service_daily(at(hour=23))
        self.assertEqual(self.writes,[b'Z'])

    def test_manual_hourly_available_with_schedule_off_cancel_clears(self):
        self.b.daily.hourly_enabled=False;self.b.request_hourly()
        self.b.cancel();self.assertFalse(self.b.hourly_requested)
        self.b.wake_retry_at=0;self.b.request_hourly()
        with patch.object(self.b.pool,'submit') as submit:
            self.b.service_daily(at(minute=20));self.assertEqual(submit.call_count,1)
        self.assertEqual(self.b.daily.last_hour,'')

    def test_sleep_suppresses_scheduled_drink_but_manual_scene_survives(self):
        self.b.daily.completed_night=''
        self.b.service_scenes(at(hour=10));self.assertNotIn(b'C',self.writes)
        self.b.scene_request='love';self.b.service_scenes(at(hour=10));self.assertIn(b'A',self.writes)


if __name__=='__main__':unittest.main()
