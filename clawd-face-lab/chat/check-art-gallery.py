"""Observe natural MCU gallery rotation and exact LCD pixels, then night priority."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import threading
import time
from daily_routines import DailyRoutines
from idle_scenes import BEIJING

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    b=Probe('COM5');b.set_continuous(False)
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,camera_photos_saved=False,
        day_clock_method='natural MCU uptime; no forced gallery commands',
        night_clock_method='inject host instance 23:00 only; system clock unchanged',
        touch_method='existing n callback; not physical human touch')
    with TemporaryDirectory(dir=ROOT) as tmp:
        clock=[datetime(2026,10,19,14,10,tzinfo=BEIJING)]
        b.daily=DailyRoutines(Path(tmp)/'daily.json',clock[0]);b.daily.hourly_enabled=False
        daily=b.service_daily;b.service_daily=lambda now=None:daily(clock[0])
        worker=threading.Thread(target=b.run,daemon=True);worker.start()
        def wait(predicate,seconds=15):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                if predicate():return
                if b.closed.is_set():raise RuntimeError(b.last_error)
                time.sleep(.03)
            raise RuntimeError('State wait timed out')
        def pixels(name):return (ROOT.parent/f'device/captures/{name}.rgb565').read_bytes()
        try:
            b.wait_line('CHAT_READY.*art_gallery=3')
            report['gallery']=[];started=time.monotonic()
            for name in ('scream','pearl','starry'):
                began,_=b.wait_line('SCENE kind=art_'+name,started,timeout=140)
                files=[]
                for i in range(2):
                    capture=f'art-{name}-native-{i}'
                    b.capture(capture);files.append(capture)
                    if i==0:time.sleep(1)
                expected=(ROOT.parent/f'device/art-gallery/{name}.rgb565').read_bytes()
                assert pixels(files[0])==expected,'LCD differs from approved RGB565: '+name
                assert pixels(files[0])==pixels(files[1]),'Still artwork changed: '+name
                item=dict(name=name,lcd_matches_approved=True,static_frames_identical=True,
                    sha256=hashlib.sha256(expected).hexdigest(),captures=files)
                report['gallery'].append(item)
                if name=='scream':
                    start=time.monotonic();b.send(b'n');b.wait_line('LISTENING',start)
                    b.wait_line('SCENE kind=laptop_or_face',start)
                    cancel=time.monotonic();b.cancel();b.wait_line('CANCELLED',cancel)
                    b.wait_line('SCENE kind=art_scream',cancel)
                    report['gallery_touch_chat_cancel_returns']=True
                if name!='starry':
                    next_name='pearl' if name=='scream' else 'starry'
                    ended,_=b.wait_line('SCENE kind=art_'+next_name,began,timeout=19)
                    item['slot_ms']=round((ended-began)*1000)
                    assert 14000<=item['slot_ms']<=16500,item
            transitions=[line for t,line in b.lines if t>=started and line.startswith('CLAWD SCENE kind=')]
            for kind in ('love','drink','sleep'):
                assert any(line.endswith('='+kind) for line in transitions),transitions
            report['day_animations_retained']=True
            # Start night while the third painting is actually visible.
            clock[0]=datetime(2026,10,19,23,0,tzinfo=BEIJING);start=time.monotonic()
            b.wait_line('SLEEP enabled=1',start);b.wait_line('SCENE kind=sleep',start)
            time.sleep(1);b.capture('art-night-sleep-native')
            until=time.monotonic()+34
            while time.monotonic()<until:
                if b.closed.is_set():raise RuntimeError(b.last_error)
                time.sleep(.1)
            changes=[line for t,line in b.lines if t>=start and line.startswith('CLAWD SCENE kind=')]
            assert changes and all(line.endswith('=sleep') for line in changes),changes
            report['night_replaces_gallery_and_stays_sleep']=True
            start=time.monotonic();b.send(b'n');b.wait_line('LISTENING',start);b.cancel()
            b.wait_line('CANCELLED',start);b.wait_line('SCENE kind=sleep',start)
            b.set_wake(True);wait(lambda:b.wake_session is not None)
            report['night_touch_cancel_and_wake']=True
            assert b.control_status['volume']==100 and b.cloud.calls==0
            report['status']='PASS_DEVICE_ART_GALLERY'
        except Exception as exc:
            report['error']=str(exc);raise
        finally:
            b.close();worker.join(8)
            report['events']=[line for _,line in b.lines if 'UPLOAD_ACK' not in line]
            (ROOT/'art-gallery-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
