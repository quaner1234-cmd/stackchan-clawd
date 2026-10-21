"""Real Windows render, idle KWS, automatic USB playback and priority handoff.

Frozen gates: short 250ms tone ignored; sustained sound starts within 4s;
10s silence ends playback and returns KWS within 16s; manual/touch stop blocks
same sound; hourly resumes; OFF releases capture. No cloud or saved audio.
"""
from copy import deepcopy
import json
from pathlib import Path
import runpy
import time
import numpy as np
import sounddevice as sd

ROOT=Path(__file__).resolve().parent
h=runpy.run_path(str(ROOT/'check-pc-speaker.py'))
connect=h['connect'];wait=h['wait'];memory_mb=h['memory_mb']


def main():
    report=dict(status='FAIL',cloud_calls=0,audio_saved=False,firmware_changed=False,
                physical_touch_tested=False,human_hearing_tested=False,streams=[])
    b=None;thread=None;audio=None;playing=[False];index=[0]
    def render(outdata,frames,times,status):
        n=np.arange(index[0],index[0]+frames);index[0]+=frames
        outdata[:]=(.04*np.sin(2*np.pi*440*n/48000))[:,None] if playing[0] else 0
    def start_sound():
        stamp=time.monotonic();playing[0]=True
        wait(b,lambda:b.pc_session is not None and b.pc_session.metrics['sent']>=20,'automatic sound start',4)
        latency=round((time.monotonic()-stamp)*1000)
        assert b.pc_auto_playing and b.wake_session is None
        return b.pc_session,latency
    def terminal(session):
        assert session.done.is_set() and not session.error,session.error
        assert session.metrics['device']['terminal']==1 and session.metrics['device']['bad']==0
        assert session.metrics['device']['underruns']<=1,session.metrics
        assert session.metrics['device']['peak']>100
        report['streams'].append(deepcopy(session.metrics))
    try:
        b,thread=connect();b.set_pc_auto(True)
        wait(b,lambda:b.pc_watch is not None and b.pc_watch.ready,'local audio monitor')
        watch=b.pc_watch
        audio=sd.OutputStream(samplerate=48000,channels=2,blocksize=960,dtype='float32',callback=render)
        audio.start();time.sleep(1)
        assert watch.rms<80,'Other Windows audio is active; pause it before this test'
        assert b.pc_session is None and b.wake_session is not None
        cpu=time.process_time();wall=time.monotonic();mem=memory_mb()
        playing[0]=True;time.sleep(.25);playing[0]=False;time.sleep(1.5)
        assert b.pc_session is None;report['short_notification_ignored']=True
        session,latency=start_sound();time.sleep(2)
        silent=time.monotonic();playing[0]=False
        audio.stop();audio.close();audio=None  # Real players close the render stream at EOF.
        time.sleep(2);assert b.pc_session is session,'Song gap ended playback too early'
        wait(b,lambda:b.pc_session is None and b.wake_session is not None,'silence returns KWS',14)
        quiet_ms=round((time.monotonic()-silent)*1000)
        assert 9500<=quiet_ms<=16000
        terminal(session);report.update(sound_start_ms=latency,silence_to_kws_ms=quiet_ms)
        elapsed=time.monotonic()-wall
        report['resources']=dict(cpu_percent_one_core=round((time.process_time()-cpu)/elapsed*100,2),
            initial_working_set_mb=mem,final_working_set_mb=memory_mb(),retained_audio_bytes=sum(len(p) for _,p in watch.audio),
            capture_workers=1,warnings=watch.warnings,warning_types=dict(watch.warning_types))
        report['render_stream_closed_at_eof']=True
        audio=sd.OutputStream(samplerate=48000,channels=2,blocksize=960,dtype='float32',callback=render)
        audio.start()
        # Existing touch-start path must preempt music and suppress its re-entry.
        session,_=start_sound();stamp=time.monotonic();b.listen()
        b.wait_line('LISTENING',stamp);b.cancel();b.wait_line('CANCELLED',stamp)
        wait(b,lambda:b.wake_session is not None,'touch/chat cancel restores KWS')
        time.sleep(2);assert b.pc_session is None and watch.blocked
        terminal(session);report['touch_listen_cancel_blocks_same_music']=True
        playing[0]=False;wait(b,lambda:not watch.blocked,'silence rearms new episode',3)
        session,_=start_sound()
        # A normal hourly preview can interrupt automatic mode and then restore it.
        stamp=time.monotonic();b.request_hourly();b.wait_line('SPEAKING',stamp)
        b.wait_line('PLAY_DONE cancelled=0',stamp)
        wait(b,lambda:b.pc_session is not None and b.pc_session is not session and
             b.pc_session.metrics['sent']>=20,'hourly returns automatic playback')
        terminal(session);session=b.pc_session
        assert b.pc_auto_playing and b.pc_watch is watch
        report['hourly_preserves_single_capture_and_resumes']=True
        b.request_pc_audio(False)
        wait(b,lambda:b.pc_session is None and b.wake_session is not None,'manual OFF restores KWS')
        time.sleep(2);assert b.pc_session is None and watch.blocked
        terminal(session);report['manual_stop_blocks_same_music']=True
        playing[0]=False;wait(b,lambda:not watch.blocked,'next episode rearm',3)
        session,_=start_sound();b.set_pc_auto(False)
        wait(b,lambda:b.pc_session is None and b.pc_watch is None and b.wake_session is not None,'switch OFF releases monitor')
        assert not watch.thread.is_alive();time.sleep(2);assert b.pc_session is None
        terminal(session);report['independent_off_restores_original_behavior']=True
        # Native short-click still starts and cancels recording with auto disabled.
        stamp=time.monotonic();b.send(b'n');b.wait_line('LISTENING',stamp)
        b.send(b'n');b.wait_line('CANCELLED',stamp)
        wait(b,lambda:b.wake_session is not None,'original touch callback still works')
        assert b.cloud.calls==0
        report['original_touch_and_kws_preserved']=True
        report['status']='PASS_DEVICE_AUTO_SPEAKER_AWAITING_HUMAN_LISTENING'
    except Exception as exc:
        report['error']=str(exc)
        if b and b.pc_watch:report['watch_error']=b.pc_watch.error
        raise
    finally:
        if audio:audio.stop();audio.close()
        if b:b.close()
        if thread:thread.join(8)
        if b and b.pc_watch and b.pc_watch.thread:b.pc_watch.thread.join(3)
        report['device_events']=[l for _,l in b.lines] if b else []
        (ROOT/'auto-speaker-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
