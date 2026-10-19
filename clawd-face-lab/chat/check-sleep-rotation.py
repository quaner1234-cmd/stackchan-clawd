"""Observe the real 80s firmware loop, then night override. No cloud/room audio."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import runpy
import threading
import time
from tempfile import TemporaryDirectory
from daily_routines import DailyRoutines
from idle_scenes import BEIJING

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    b=Probe('COM5')
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,
        day_clock_method='real MCU uptime, no forced scene commands',
        night_clock_method='injected host test instance clock; system clock unchanged',
        touch_method='existing n callback, not physical human touch')
    with TemporaryDirectory(dir=ROOT) as tmp:
        clock=[datetime(2026,10,19,14,10,tzinfo=BEIJING)]
        b.daily=DailyRoutines(Path(tmp)/'daily.json',clock[0])
        b.daily.hourly_enabled=False
        service=b.service_daily;b.service_daily=lambda now=None:service(clock[0])
        worker=threading.Thread(target=b.run,daemon=True);worker.start()
        try:
            b.wait_line('CHAT_READY.*sleep_scene=1')
            started=time.monotonic()
            sleep_at,_=b.wait_line('SCENE kind=sleep',started,timeout=90)
            assert not b.daily.sleeping(clock[0]) and b.sent_sleep is False
            b.capture('sleep-day-native-0');time.sleep(.8);b.capture('sleep-day-native-1')
            def sha(i):return hashlib.sha256((ROOT.parent/f'device/captures/sleep-day-native-{i}.rgb565').read_bytes()).hexdigest()
            assert sha(0)!=sha(1)
            ended,_=b.wait_line('SCENE kind=laptop_or_face',sleep_at,timeout=14)
            duration=round((ended-sleep_at)*1000)
            assert 9000<=duration<=11500,duration
            def observed_kinds():
                return [l.rsplit('=',1)[-1] for t,l in b.lines if t>=started and l.startswith('CLAWD SCENE kind=')]
            deadline=started+115
            while not all(x in observed_kinds() for x in ['love','drink','sleep','laptop_or_face']) and time.monotonic()<deadline:
                if b.closed.is_set():raise RuntimeError(b.last_error)
                time.sleep(.1)
            kinds=observed_kinds()
            assert all(x in kinds for x in ['love','drink','sleep','laptop_or_face']),kinds
            report.update(day_rotation_scene_order=kinds,day_sleep_duration_ms=duration,
                daytime_sleep_uses_no_night_flag=True,day_sleep_frames_distinct=True)
            # Hold night mode longer than the longest daytime segment (30 seconds).
            clock[0]=datetime(2026,10,19,23,0,tzinfo=BEIJING);start=time.monotonic()
            b.wait_line('SLEEP enabled=1',start);b.wait_line('SCENE kind=sleep',start)
            until=time.monotonic()+34
            while time.monotonic()<until and not b.closed.is_set():time.sleep(.1)
            assert not b.closed.is_set(),b.last_error
            transitions=[l for t,l in b.lines if t>=start and l.startswith('CLAWD SCENE kind=')]
            assert transitions and all(l.endswith('=sleep') for l in transitions),transitions
            b.capture('sleep-night-native');report['night_only_sleep_across_day_rotation_boundaries']=True
            start=time.monotonic();b.send(b'n');b.wait_line('LISTENING',start)
            b.wait_line('SCENE kind=laptop_or_face',start);b.cancel()
            b.wait_line('CANCELLED',start);b.wait_line('SCENE kind=sleep',start)
            report['night_touch_chat_cancel_returns_to_sleep']=True
            # Existing scheduled-alarm completion is unchanged; apply its stored state transition.
            clock[0]=datetime(2026,10,20,6,40,tzinfo=BEIJING)
            b.daily.alarm_started(clock[0]);start=time.monotonic()
            b.wait_line('SLEEP enabled=0',start)
            report.update(alarm_state_transition_restores_day_flag=True,
                scheduled_alarm_full_playback_this_probe=False,
                previous_full_alarm_report='daily-routines-device-check.json',
                status='PASS_DEVICE_SLEEP_ROTATION')
        except Exception as exc:
            report['error']=str(exc);raise
        finally:
            report['device_events']=[l for _,l in b.lines if 'UPLOAD_ACK' not in l]
            (ROOT/'sleep-rotation-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            b.close();worker.join(8)
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
