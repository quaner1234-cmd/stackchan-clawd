"""Real USB status/priority/cancellation test; zero cloud, no saved mic/photo."""
import json
from pathlib import Path
import runpy
import time
from agent_status import StatusBook

ROOT=Path(__file__).resolve().parent
helpers=runpy.run_path(str(ROOT/'check-pc-speaker.py'))
connect=helpers['connect'];wait=helpers['wait'];memory_mb=helpers['memory_mb']


class FrozenMonitor:
    def __init__(self):self.book=StatusBook()
    def snapshot(self):return self.book.snapshot()
    def stop(self):pass


def main():
    report=dict(status='FAIL',cloud_calls=0,mic_saved=False,camera_used=False,
                source='controlled metadata through real Bridge and firmware',human_hearing_tested=False,
                physical_touch_tested=False)
    b=None;thread=None
    try:
        b,thread=connect();assert b.device_agent_status
        m=FrozenMonitor();b.agent_display.monitor=m;b.agent_display.enabled=True
        def state(value):
            at=time.monotonic();m.book.put('codex','hardware-check',value);return at
        def capture(name):
            b.set_wake(False);wait(b,lambda:not b.wake_device_active,'mic release');time.sleep(.2)
            b.capture('agent-'+name);b.set_wake(True)
        cpu=time.process_time();wall=time.monotonic();memory=memory_mb()
        at=state('running');b.wait_line('AGENT_STATUS code=W',at)
        capture('running');report['typing']=True
        at=state('waiting');b.wait_line('AGENT_STATUS code=H',at)
        b.wait_line('CONTROL_BEGIN',at);b.wait_line('PLAY_DONE cancelled=0',at,timeout=16)
        wait(b,lambda:b.scene_idle() and not b.control_requests,'attention finished',12)
        b.wait_line('emotion=doubt',at);capture('waiting')
        report['waiting_pose_and_pcm_complete']=True
        starts=sum('CONTROL_BEGIN' in line for t,line in b.lines if t>=at)
        time.sleep(2)
        assert sum('CONTROL_BEGIN' in line for t,line in b.lines if t>=at)==starts,'Repeated attention cue'
        report['waiting_deduplicated']=True
        at=state('failed');b.wait_line('AGENT_STATUS code=E',at);b.wait_line('emotion=sad',at)
        capture('failed');report['sad']=True
        # A real native touch command starts/cancels the same microphone path.
        at=time.monotonic();b.send(b'n');b.wait_line('LISTENING',at)
        state('completed');time.sleep(.6)
        assert not b.agent_display.dancing,'Agent interrupted listening'
        b.send(b'n');b.wait_line('CANCELLED',at)
        b.wait_line('DANCE_START',at,timeout=15);b.wait_line('DANCE_DONE cancelled=0',at,timeout=15)
        wait(b,lambda:b.wake_session is not None,'KWS after completion dance')
        report['chat_priority_and_completion_dance']=True
        # No second dance from the same terminal event; independent switch works.
        count=sum('DANCE_START' in line for _,line in b.lines);time.sleep(1)
        assert sum('DANCE_START' in line for _,line in b.lines)==count
        b.agent_display.enabled=False;at=time.monotonic();state('waiting');time.sleep(1)
        assert not any('CONTROL_BEGIN' in line for t,line in b.lines if t>=at)
        report['off_has_no_action']=True
        report['resources']=dict(seconds=round(time.monotonic()-wall,2),
            cpu_percent_one_core=round((time.process_time()-cpu)/(time.monotonic()-wall)*100,2),
            initial_mb=memory,final_mb=memory_mb())
        report['status']='PASS_DEVICE_AWAITING_HUMAN_AND_NATIVE_AGENT_RESTARTS'
    finally:
        if b:
            b.close();thread.join(5)
            report['device_events']=[line for _,line in b.lines if 'UPLOAD_ACK' not in line]
        (ROOT/'agent-status-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
