from datetime import datetime, timezone, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from concurrent.futures import Future
from idle_scenes import DrinkReminder, drink_pcm, BEIJING
from daily_routines import DailyRoutines
from bridge import Bridge


def at(hour,minute,second=0,day=18):
    return datetime(2026,10,day,hour,minute,second,tzinfo=BEIJING)


class ReminderClockTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'settings.json';self.r=DrinkReminder(self.path)

    def test_schedule_boundary_and_beijing_timezone(self):
        for h,m in [(8,30),(20,30),(23,0),(9,29)]:self.assertIsNone(self.r.due(at(h,m)))
        for h,m in [(9,0),(9,30),(20,0)]:
            slot=self.r.due(at(h,m));self.assertIsNotNone(slot);self.r.claim(slot)
        r=DrinkReminder(Path(self.tmp.name)/'fresh.json')
        self.assertEqual(r.due(at(9,0).astimezone(timezone.utc)),at(9,0).isoformat())

    def test_busy_defers_and_claim_survives_reopen(self):
        slot=self.r.due(at(10,30))
        self.assertEqual(self.r.due(at(10,34)),slot)
        self.r.claim(slot)
        reopened=DrinkReminder(self.path)
        self.assertIsNone(reopened.due(at(10,30,50)))
        self.assertIsNotNone(reopened.due(at(11,0)))

    def test_no_startup_backlog_and_busy_expiry(self):
        self.assertIsNone(self.r.due(at(10,5)))
        self.r.due(at(10,30));self.assertIsNone(self.r.due(at(10,40)))
        self.assertIsNotNone(self.r.due(at(11,0)))

    def test_disable_clears_pending_and_persists(self):
        self.r.due(at(9,0));self.r.set_enabled(False)
        self.assertIsNone(self.r.due(at(9,1)))
        self.assertFalse(DrinkReminder(self.path).enabled)

    def test_clock_rollback_does_not_replay_old_slots(self):
        self.r.claim(self.r.due(at(14,0)))
        self.assertIsNone(self.r.due(at(13,30)))
        self.assertIsNotNone(self.r.due(at(9,0,day=19)))

    def test_asset_is_short_valid_pcm(self):
        self.assertTrue(48000<len(drink_pcm())<480000)


class SceneStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory(dir=Path(__file__).resolve().parent);self.addCleanup(self.tmp.cleanup)
        self.b=Bridge('unused',lambda *_:None)
        self.b.drink_reminder=DrinkReminder(Path(self.tmp.name)/'settings.json')
        # Isolate the daily schedule too: reading the machine's live
        # daily-settings.json made these tests depend on local state.
        self.b.daily=DailyRoutines(Path(self.tmp.name)/'daily.json',at(10,0))
        self.b.device_scenes=True;self.b.state='ready'
        self.writes=[]
        class Device:
            is_open=True
            def write(_,data):self.writes.append(data)
        self.b.serial=Device();self.b.connection_event=lambda *_a,**_k:None
        self.b.cloud.synthesize=lambda *_:self.fail('Runtime reminders must be local')
        self.addCleanup(self.b.pool.shutdown)

    def test_busy_paths_do_not_start_and_idle_claims_once(self):
        for field,value in [('phase','listening'),('phase','thinking'),('phase','speaking'),
                            ('phase','dancing'),('uploading',True),('photo_pending',True),
                            ('photo_preview',True),('continuous_active',True),('parent_capture',True)]:
            original=getattr(self.b,field);setattr(self.b,field,value)
            self.b.service_scenes(at(10,0));self.assertEqual(self.writes,[])
            setattr(self.b,field,original)
        self.b.service_scenes(at(10,2))
        self.assertEqual(self.writes,[b'C']);self.assertEqual(self.b.phase,'reminding')
        self.b.service_scenes(at(10,2));self.assertEqual(self.writes,[b'C'])
        self.assertFalse(self.b.wake_eligible())

    def test_native_ack_required_before_audio_and_stale_event_ignored(self):
        self.b.service_scenes(at(10,0))
        self.assertIsNone(self.b.future)
        played=[]
        with patch.object(self.b,'play',side_effect=lambda *_a,**_k:played.append(True)):
            self.b.scene_event(b'CLAWD SCENE_START kind=drink duration_ms=12000')
            self.b.future.result(2)
            self.assertEqual(played,[True])
            self.b.cancel()
            self.b.scene_event(b'CLAWD SCENE_START kind=drink duration_ms=12000')
            self.assertEqual(played,[True])
        self.assertIsNone(self.b.reminder_token)

    def test_completed_reminder_returns_idle_without_starting_chat(self):
        self.b.service_scenes(at(10,0))
        self.b.scene_event(b'CLAWD SCENE_DONE kind=drink reason=complete')
        self.assertEqual(self.b.phase,'idle');self.assertFalse(self.b.continuous_active)
        self.b.service_scenes(at(10,0,50));self.assertEqual(self.writes,[b'C'])

    def test_manual_love_works_with_schedule_disabled(self):
        self.b.drink_reminder.enabled=False
        self.b.request_scene('love');self.b.service_scenes(at(21,15))
        self.assertEqual(self.writes,[b'A']);self.assertEqual(self.b.phase,'idle')

    def test_old_firmware_gets_no_scene_command(self):
        self.b.device_scenes=False;self.b.request_scene('drink')
        self.b.service_scenes(at(10,0));self.assertEqual(self.writes,[])


if __name__=='__main__':unittest.main()
