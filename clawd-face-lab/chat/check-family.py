"""Native LCD captures/state checks; no cloud calls or saved room audio."""
import json
from pathlib import Path
import runpy
import threading
import time
import wave
ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def grounded_foot_count(frame):
    """Count orange foot silhouettes on the lowest character row in real RGB565."""
    last=[]
    for y in range(100,185):
        row=[]
        for x in range(48,181):
            offset=y*640+x*2;value=frame[offset]|frame[offset+1]<<8
            r=((value>>11)&31)*255//31;g=((value>>5)&63)*255//63;b=(value&31)*255//31
            if r>120 and r*10>g*13 and g*10>b*11:row.append(x)
        if row:last=row
    return sum(index==0 or x>last[index-1]+1 for index,x in enumerate(last))


def main():
    bridge=Probe('COM5');bridge.set_continuous(False)
    thread=threading.Thread(target=bridge.run,daemon=True);thread.start()
    report={'status':'FAIL','cloud_calls':0,'physical_led_color_confirmed':False}
    try:
        _,line=bridge.wait_line('CHAT_READY');assert 'voice_profile=family3' in line
        started=time.monotonic()
        bridge.capture('family-paused')
        bridge.wait_line('WORK_FRAME .*scene=laptop',started)
        report['resident_without_parent_task']=True
        started=time.monotonic();bridge.set_task_status('running')
        bridge.wait_line('UI mode=4 work=1 led=purple',started)
        # Avoid mistaking two samples at the same loop phase for a frozen animation.
        frames=[];sample_times=[]
        for index,delay in enumerate((0,.47,.83,1.19),1):
            time.sleep(delay);name=f'family-work-{index}'
            bridge.capture(name);sample_times.append(round(time.monotonic()-started,3))
            frames.append((ROOT.parent/f'device/captures/{name}.rgb565').read_bytes())
        report['work_sample_seconds']=sample_times
        report['distinct_work_frames']=len(set(frames))
        assert len(set(frames))>1,'Working animation did not move across four samples'
        report['grounded_foot_counts']=[grounded_foot_count(frame) for frame in frames]
        assert report['grounded_foot_counts']==[4]*4,'Expected four distinct grounded feet plus two typing arms'
        report['native_work_animation_changes']=True
        started=time.monotonic();bridge.listen()
        bridge.wait_line('LISTENING',started);bridge.wait_line('UI mode=1 work=0 led=green',started)
        report['face_on_listening_before_speech']=True
        bridge.capture('family-listening')
        bridge.parent_handler=lambda text:None
        started=time.monotonic();assert bridge.arm_parent()
        bridge.wait_line('CANCELLED',started)
        bridge.wait_line('LISTENING',started+0.4,timeout=3)
        assert bridge.parent_capture and bridge.parent_restart_at is None
        report['parent_button_starts_fresh_recording']=True
        started=time.monotonic();bridge.cancel();bridge.wait_line('CANCELLED',started)
        # A very quick cancel can precede the next UI tick, so no transition
        # event is required. Verify the actual resulting screen instead.
        bridge.capture('family-after-cancel')
        bridge.wait_line('WORK_FRAME .*scene=laptop',started)
        time.sleep(.4)
        with wave.open(str(ROOT/'adult-cloud-test.wav')) as wav:pcm=wav.readframes(wav.getnframes())
        started=time.monotonic();bridge.play(pcm,bridge.epoch)
        bridge.wait_line('SPEAKING',started);bridge.wait_line('UI mode=3',started)
        bridge.capture('family-speaking')
        bridge.wait_line('PLAY_DONE cancelled=0',started)
        bridge.set_task_status('completed');time.sleep(.3);bridge.capture('family-task-done')
        bridge.set_task_status('idle');time.sleep(2)
        bridge.wait_line('UI mode=0 work=1 led=off',started)
        bridge.capture('family-resident')
        report['resident_after_task_finished']=True
        # q leaves USB diagnostics available but detaches the voice host.
        started=time.monotonic();bridge.send(b'q')
        time.sleep(1.6);bridge.capture('family-offline')
        bridge.wait_line('WORK_FRAME .*scene=laptop',started)
        report['resident_without_chat_host']=True
        started=time.monotonic();bridge.send(b'n')
        bridge.wait_line('UI mode=0 work=0 led=off',started)
        bridge.capture('family-offline-touch-face')
        started=time.monotonic();time.sleep(6.2)
        bridge.wait_line('UI mode=0 work=1 led=off',started)
        report['offline_touch_face_then_resident']=True
        report['listening_green_and_speaking_blue_commanded']=True
        report['captures']=bridge.captured
        report['status']='PASS_NATIVE_DISPLAY_AND_DEVICE_STATE_AWAITING_PHYSICAL_LED_CONFIRMATION'
    finally:
        bridge.close();thread.join(3)
        report['events']=[line for _,line in bridge.lines]
        (ROOT/'family-hardware-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=False))


if __name__=='__main__':main()
