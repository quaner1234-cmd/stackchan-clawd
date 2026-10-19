"""Real LCD/USB checks. Fixed local audio; no cloud calls or saved camera/room audio."""
from datetime import datetime
import json
from pathlib import Path
import runpy
import threading
import time
from tempfile import TemporaryDirectory
from idle_scenes import DrinkReminder, BEIJING

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    bridge=Probe('COM5');bridge.set_wake(True);bridge.set_continuous(False)
    thread=threading.Thread(target=bridge.run,daemon=True)
    report={'status':'FAIL','cloud_calls':0,'camera_photos_saved':False,'room_audio_saved':False,
            'touch_test_method':'existing n short-click event command, not physical touch',
            'schedule_test_method':'inject Beijing clock into this probe only, system clock unchanged'}
    with TemporaryDirectory(dir=ROOT) as scratch:
        bridge.drink_reminder=DrinkReminder(Path(scratch)/'settings.json')
        bridge.drink_reminder.enabled=False
        thread.start()
        def standby():
            deadline=time.monotonic()+15
            while bridge.wake_session is None and time.monotonic()<deadline and not bridge.closed.is_set():time.sleep(.03)
            assert bridge.wake_session is not None,'Wake not recovered'
        try:
            bridge.wait_line('CHAT_READY');assert bridge.device_scenes
            standby();report['initial_wake']=True
            # LCD snapshots are 153KB and serialize stdout. Pause diagnostic mic
            # traffic while capturing, then restore normal wake before testing taps.
            started=time.monotonic();bridge.set_wake(False)
            bridge.wait_line('WAKE_STOPPED',started)
            started=time.monotonic();bridge.request_scene('love')
            bridge.wait_line('SCENE_START kind=love',started)
            for i in range(4):
                time.sleep(.3);bridge.capture(f'love-native-{i}')
            bridge.set_wake(True);standby();time.sleep(.4)
            bridge.send(b'n');bridge.wait_line('LISTENING',started)
            bridge.capture('idle-scenes-face-listening')
            bridge.cancel();standby();report['love_to_touch_listen_and_cancel']=True
            started=time.monotonic();bridge.request_scene('drink')
            bridge.wait_line('SCENE_START kind=drink',started)
            bridge.wait_line('SPEAKING volume=100 pcm_gain_percent=100',started)
            for i in range(4):
                time.sleep(.3);bridge.capture(f'drink-native-{i}')
            bridge.wait_line('PLAY_DONE cancelled=0',started)
            ended,_=bridge.wait_line('SCENE_DONE kind=drink reason=complete',started)
            report['reminder_duration_ms']=round((ended-started)*1000)
            standby();report['cached_voice_and_wake_recovery']=True
            # Busy schedule: show a due slot, hold busy, then release. One reminder only.
            bridge.continuous_active=True
            fixed=datetime(2026,10,18,12,0,10,tzinfo=BEIJING)
            original_due=bridge.drink_reminder.due
            bridge.drink_reminder.due=lambda now=None:original_due(fixed)
            bridge.drink_reminder.enabled=True
            before=len([l for _,l in bridge.lines if 'SCENE_START kind=drink' in l])
            time.sleep(1)
            assert len([l for _,l in bridge.lines if 'SCENE_START kind=drink' in l])==before
            bridge.continuous_active=False;started=time.monotonic()
            bridge.wait_line('SCENE_START kind=drink',started)
            bridge.wait_line('SPEAKING',started)
            bridge.send(b'n');bridge.wait_line('PLAY_DONE cancelled=1',started)
            bridge.wait_line('CANCELLED',started);standby()
            time.sleep(1)
            assert len([l for _,l in bridge.lines if 'SCENE_START kind=drink' in l])==before+1
            report.update(schedule_waited_for_idle=True,same_slot_did_not_repeat=True,reminder_touch_cancelled=True)
            bridge.drink_reminder.enabled=False
            # Camera preview still yields to the same touch event; image stays in memory.
            started=time.monotonic();bridge.stop_wake();bridge.send(b'g')
            bridge.wait_line('PHOTO_PREVIEW shown',started)
            bridge.send(b'n');bridge.wait_line('PHOTO_PREVIEW closed',started)
            standby();report['photo_preview_cancel_and_wake']=True
            started=time.monotonic();bridge.stop_wake();bridge.send(b'B')
            bridge.wait_line('DANCE_START',started);time.sleep(.6)
            bridge.send(b'n');bridge.wait_line('DANCE_DONE cancelled=1',started)
            standby();report['dance_cancel_and_wake']=True
            report.update(status='PASS_DEVICE_IDLE_SCENES',captures=bridge.captured)
        finally:
            bridge.close();thread.join(4)
            report['device_events']=[l for _,l in bridge.lines]
            (ROOT/'idle-scenes-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
