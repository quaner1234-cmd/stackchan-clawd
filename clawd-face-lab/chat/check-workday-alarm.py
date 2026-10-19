"""Real USB alarm playback with an injected Beijing clock. No runtime cloud calls."""
from datetime import datetime
import json
from pathlib import Path
import runpy
import threading
import time
from tempfile import TemporaryDirectory
from workday_alarm import WorkdayAlarm
from idle_scenes import BEIJING

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    bridge=Probe('COM5');bridge.set_wake(True);bridge.set_continuous(False)
    thread=threading.Thread(target=bridge.run,daemon=True)
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,firmware_flashed=False,
                clock_method='injected 2026-10-19 06:40 Beijing into test instance only; system clock unchanged',
                touch_method='existing n short-click callback path; not physical human touch')
    with TemporaryDirectory(dir=ROOT) as scratch:
        bridge.workday_alarm=WorkdayAlarm(Path(scratch)/'settings.json')
        bridge.workday_alarm.enabled=False
        fixed=datetime(2026,10,19,6,40,1,tzinfo=BEIJING)
        original_due=bridge.workday_alarm.due
        bridge.workday_alarm.due=lambda now=None:original_due(fixed)
        thread.start()
        def standby():
            deadline=time.monotonic()+15
            while bridge.wake_session is None and time.monotonic()<deadline and not bridge.closed.is_set():time.sleep(.03)
            assert bridge.wake_session is not None,'Local wake did not recover'
        def wait_count(needle,count,since,timeout=65):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline and not bridge.closed.is_set():
                found=[(t,l) for t,l in bridge.lines if t>=since and needle in l]
                if len(found)>=count:return found
                time.sleep(.04)
            raise RuntimeError('Missing event count: '+needle)
        try:
            bridge.wait_line('CHAT_READY');standby()
            bridge.phase='thinking';bridge.workday_alarm.enabled=True
            started=time.monotonic();time.sleep(.8)
            assert not any(t>=started and 'SPEAKING' in l for t,l in bridge.lines)
            bridge.phase='idle';started=time.monotonic()
            bridge.wait_line('SPEAKING volume=100 pcm_gain_percent=100',started)
            plays=wait_count('PLAY_DONE cancelled=0',3,started)
            standby();time.sleep(.7)
            assert len([l for t,l in bridge.lines if t>=started and 'SPEAKING' in l])==3
            assert bridge.workday_alarm.last=='2026-10-19'
            assert WorkdayAlarm(bridge.workday_alarm.path).due(fixed) is None
            assert bridge.turns==0 and bridge.cloud.calls==0
            report.update(scheduled_three_playbacks=True,scheduled_duration_ms=round((plays[-1][0]-started)*1000),
                          same_day_persistent_dedup=True,busy_waited_for_idle=True,wake_recovered=True)
            bridge.workday_alarm.enabled=False
            started=time.monotonic();bridge.request_alarm()
            bridge.wait_line('SPEAKING',started);bridge.send(b'n')
            bridge.wait_line('PLAY_DONE cancelled=1',started);standby()
            report['touch_cancel_during_speech']=True
            started=time.monotonic();bridge.request_alarm()
            bridge.wait_line('PLAY_DONE cancelled=0',started)
            bridge.send(b'n');bridge.wait_line('CANCELLED',started);standby()
            time.sleep(6)
            assert len([l for t,l in bridge.lines if t>=started and 'SPEAKING' in l])==1
            assert bridge.turns==0
            report['touch_cancel_during_repeat_gap']=True
            bridge.workday_alarm.enabled=True;bridge.activity_s=1801
            deadline=time.monotonic()+3
            while not bridge.chat_expired and time.monotonic()<deadline:time.sleep(.03)
            assert bridge.chat_expired and bridge.state=='ready' and not bridge.closed.is_set()
            started=time.monotonic();bridge.request_alarm();bridge.wait_line('SPEAKING',started)
            bridge.cancel();bridge.wait_line('PLAY_DONE cancelled=1',started)
            report.update(alarm_available_after_chat_time_limit=True,status='PASS_DEVICE_WORKDAY_ALARM')
        finally:
            bridge.close();thread.join(4)
            report['device_events']=[l for _,l in bridge.lines]
            (ROOT/'workday-alarm-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False,indent=2))


if __name__=='__main__':main()
