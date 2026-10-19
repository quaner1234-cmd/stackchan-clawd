"""Real USB/LCD checks, cached audio and injected Beijing clock; no cloud calls."""
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
from workday_alarm import WorkdayAlarm

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    b=Probe('COM5');b.set_wake(True);b.set_continuous(False)
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,camera_photos_saved=False,
        clock_method='injected test instance only; system clock unchanged',
        touch_method='existing n short-click callback path, not physical human touch')
    clock=[datetime(2026,10,19,10,10,tzinfo=BEIJING)]
    with TemporaryDirectory(dir=ROOT) as tmp:
        b.daily=DailyRoutines(Path(tmp)/'daily.json',clock[0]);b.daily.save()
        b.workday_alarm=WorkdayAlarm(Path(tmp)/'alarm.json');b.workday_alarm.enabled=False
        original_daily=b.service_daily;b.service_daily=lambda now=None:original_daily(clock[0])
        original_scenes=b.service_scenes;b.service_scenes=lambda now=None:original_scenes(clock[0])
        original_end=b.daily.alarm_started;b.daily.alarm_started=lambda now=None:original_end(clock[0])
        thread=threading.Thread(target=b.run,daemon=True);thread.start()
        def standby():
            deadline=time.monotonic()+15
            while b.wake_session is None and time.monotonic()<deadline and not b.closed.is_set():time.sleep(.03)
            assert b.wake_session is not None,'wake not recovered'
        def wait_until(condition,timeout=20):
            deadline=time.monotonic()+timeout
            while not condition() and time.monotonic()<deadline and not b.closed.is_set():time.sleep(.03)
            assert condition(),'condition timeout'
        try:
            b.wait_line('CHAT_READY.*sleep_scene=1');standby()
            start=time.monotonic();clock[0]=datetime(2026,10,19,23,0,tzinfo=BEIJING)
            b.wait_line('SLEEP enabled=1',start);b.wait_line('SCENE kind=sleep',start)
            b.set_wake(False);b.wait_line('WAKE_STOPPED',start)
            hashes=[]
            for i in range(4):
                b.capture('sleep-native-'+str(i));time.sleep(.7)
                hashes.append(hashlib.sha256((ROOT.parent/f'device/captures/sleep-native-{i}.rgb565').read_bytes()).hexdigest())
            assert len(set(hashes))>=3,'sleep frames static'
            assert not any(t>=start and 'SPEAKING' in l for t,l in b.lines)
            report.update(sleep_at_23=True,night_hourly_silent=True,distinct_sleep_frames=len(set(hashes)))
            b.set_wake(True);standby()
            start=time.monotonic();b.send(b'n');b.wait_line('LISTENING',start)
            b.wait_line('SCENE kind=laptop_or_face',start)
            b.cancel();b.wait_line('CANCELLED',start);standby()
            b.wait_line('SCENE kind=sleep',start)
            report['sleep_touch_record_cancel_and_return']=True
            # The scheduled morning alarm takes priority and ends the night at actual SPEAKING.
            clock[0]=datetime(2026,10,20,6,40,1,tzinfo=BEIJING)
            due=b.workday_alarm.due;b.workday_alarm.due=lambda now=None:due(clock[0])
            b.workday_alarm.enabled=True;start=time.monotonic()
            b.wait_line('SPEAKING',start);b.wait_line('SLEEP enabled=0',start)
            wait_until(lambda:len([l for t,l in b.lines if t>=start and 'PLAY_DONE cancelled=0' in l])>=3,55)
            standby();assert not b.daily.sleeping(clock[0]);b.workday_alarm.enabled=False
            report['scheduled_alarm_three_plays_end_sleep']=True
            # Busy host waits; reopening settings cannot repeat this hour.
            b.phase='thinking';clock[0]=datetime(2026,10,20,10,0,tzinfo=BEIJING)
            start=time.monotonic();time.sleep(.8)
            assert not any(t>=start and 'SPEAKING' in l for t,l in b.lines)
            b.phase='idle';start=time.monotonic();b.wait_line('SPEAKING',start)
            b.wait_line('PLAY_DONE cancelled=0',start);standby();time.sleep(.5)
            assert len([l for t,l in b.lines if t>=start and 'SPEAKING' in l])==1
            assert DailyRoutines(b.daily.path,clock[0]).hourly_due(clock[0]) is None
            report.update(hourly_waited_for_idle=True,hourly_cached_playback_and_wake=True,hourly_persistent_dedup=True)
            # Cancel a manual announcement using the unchanged hardware touch event.
            b.daily.hourly_enabled=False;b.request_hourly();start=time.monotonic()
            b.wait_line('SPEAKING',start);b.send(b'n');b.wait_line('PLAY_DONE cancelled=1',start);standby()
            report['hourly_touch_cancel_and_wake']=True
            # Both off removes visual sleep intent without disabling ordinary touch/wake.
            b.daily.sleep_enabled=False;b.preview_sleep();start=time.monotonic()
            b.wait_line('SLEEP enabled=1',start);b.cancel();b.wait_line('SLEEP enabled=0',start);standby()
            report.update(independent_switch_and_preview_cancel=True,chat_turns=b.turns,status='PASS_DEVICE_DAILY_ROUTINES')
        except Exception as exc:
            report['error']=str(exc);raise
        finally:
            report['device_events']=[l for _,l in b.lines if 'UPLOAD_ACK' not in l]
            (ROOT/'daily-routines-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            b.close();thread.join(8)
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
