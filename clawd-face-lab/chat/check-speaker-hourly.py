"""Real system loopback -> framed STOP -> cached hourly speech -> loopback.

Clock injection is confined to a test Bridge; production settings/system time stay
unchanged. No cloud calls, room PCM, camera photos or system audio are saved.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory
import threading
import time
from unittest.mock import MagicMock
import numpy as np
import sounddevice as sd

from daily_routines import DailyRoutines
from idle_scenes import BEIJING

ROOT=Path(__file__).resolve().parent
helpers=runpy.run_path(str(ROOT/'check-pc-speaker.py'))
Probe=helpers['SpeakerProbe'];wait=helpers['wait'];begin=helpers['begin']


def at(hour, minute=0):return datetime(2026,10,20,hour,minute,1,tzinfo=BEIJING)


def main():
    report=dict(status='FAIL',cloud_calls=0,firmware_changed=False,
        clock_method='injected Bridge only; system/production settings unchanged',
        audio_saved=False,physical_touch_tested=False,human_hearing_tested=False,cycles=[])
    b=None;thread=None;audio=None
    with TemporaryDirectory(dir=ROOT) as tmp:
        # Freeze the reported failure against the exact pre-change source.
        old_path=ROOT/'backups/2026-10-20-before-speaker-hourly/chat/bridge.py'
        # Private local rollback snapshots are deliberately absent from GitHub.
        report['baseline_reproduced']=None
        if old_path.is_file():
            old=runpy.run_path(str(old_path))['Bridge']('unused',lambda *_:None)
            try:
                old.daily=DailyRoutines(Path(tmp)/'old.json',at(20))
                old.device_scenes=True;old.device_pc_audio=True;old.state='ready';old.phase='pc_audio'
                old.pc_requested=True;old.pc_session=MagicMock();old.pc_session.stop_event.is_set.return_value=False
                old.service_daily(at(20))
                report['baseline_reproduced']=(not old.pc_session.stop.called and old.reminder_token is None)
                assert report['baseline_reproduced'],'Old source no longer reproduces report'
            finally:old.pool.shutdown(wait=False,cancel_futures=True)
        clock=[at(20,10)]
        try:
            b=Probe('COM5');b.set_continuous(False);b.set_wake(True)
            b.daily=DailyRoutines(Path(tmp)/'daily.json',clock[0]);b.daily.save()
            original=b.service_daily;b.service_daily=lambda now=None:original(clock[0])
            thread=threading.Thread(target=b.run,daemon=True);thread.start()
            b.wait_line('CHAT_READY.*pc_scene=singer1');wait(b,lambda:b.wake_session is not None,'initial KWS')
            wait(b,lambda:not b.control_active and not b.control_requests,'controls handshake')
            index=[0]
            def render(outdata,frames,times,status):
                n=np.arange(index[0],index[0]+frames);index[0]+=frames
                outdata[:]=(.04*np.sin(2*np.pi*440*n/48000))[:,None]
            audio=sd.OutputStream(samplerate=48000,channels=2,blocksize=960,dtype='float32',callback=render)
            audio.start()
            session=begin(b)
            time.sleep(.6)
            for hour in (21,22):
                start=time.monotonic();clock[0]=at(hour)
                stopped,_=b.wait_line('STREAM_DONE.*terminal=1',start)
                speaking,_=b.wait_line('SPEAKING',start)
                played,_=b.wait_line('PLAY_DONE cancelled=0',start)
                wait(b,lambda:b.pc_session is not None and b.pc_session is not session and
                     b.pc_session.metrics['sent']>=20,'audio resumes after hourly')
                resumed=b.pc_session
                stream_ready,_=b.wait_line('STREAM_READY',played)
                assert stopped<speaking<played<stream_ready
                assert session.done.is_set() and not session.error,session.error
                assert session.metrics['device']['bad']==0 and session.metrics['device']['terminal']==1
                assert session.metrics['device']['peak']>100,'Non-silent Windows render did not reach MCU'
                assert b.reminder_token is None and not b.pc_hourly_resume
                assert b.wake_session is None
                assert DailyRoutines(b.daily.path,clock[0]).hourly_due(clock[0]) is None
                time.sleep(1.2)
                assert len([l for t,l in b.lines if t>=start and 'SPEAKING' in l])==1
                report['cycles'].append(dict(hour=hour,scheduled=True,
                    yield_to_speaking_ms=round((speaking-start)*1000),
                    play_done_to_stream_ready_ms=round((stream_ready-played)*1000),
                    stopped_stream=deepcopy(session.metrics),resumed_frames=resumed.metrics['sent'],
                    persistent_dedup=True))
                session=resumed
            # The UI test button uses the identical preempt/resume path even if schedule is off.
            b.daily.hourly_enabled=False;start=time.monotonic();b.request_hourly()
            b.wait_line('SPEAKING',start);b.wait_line('PLAY_DONE cancelled=0',start)
            assert session.metrics['device']['peak']>100,'Second resumed stream is silent'
            report['both_scheduled_resumes_carry_non_silent_windows_audio']=True
            wait(b,lambda:b.pc_session is not None and b.pc_session is not session and
                 b.pc_session.metrics['sent']>=20,'manual report resumes stream')
            session=b.pc_session;report['manual_preview_resumes_stream']=True
            # Explicit audio OFF during speech is sticky; no delayed auto-restart.
            start=time.monotonic();b.request_hourly();b.wait_line('SPEAKING',start)
            b.request_pc_audio(False);b.wait_line('PLAY_DONE cancelled=0',start)
            wait(b,lambda:b.wake_session is not None,'user OFF returns KWS')
            time.sleep(1.5);assert b.pc_session is None and not b.pc_requested
            report['user_off_during_report_prevents_resume']=True
            # The native short-click callback cancels speech and drops resume intent.
            session=begin(b);start=time.monotonic();b.request_hourly();b.wait_line('SPEAKING',start)
            b.send(b'n');b.wait_line('PLAY_DONE cancelled=1',start)
            wait(b,lambda:b.wake_session is not None,'touch cancels hourly and returns KWS')
            time.sleep(1.5);assert b.pc_session is None and not b.pc_hourly_resume
            report['native_short_click_cancels_report_without_resume']=True
            # Original playback -> recording -> cancel -> KWS still works.
            session=begin(b);start=time.monotonic();b.listen();b.wait_line('LISTENING',start)
            b.cancel();b.wait_line('CANCELLED',start)
            wait(b,lambda:b.wake_session is not None,'record cancel returns KWS')
            assert not b.pc_requested and b.cloud.calls==0
            report['original_playback_to_listen_cancel_kws']=True
            report['status']='PASS_DEVICE_HOURLY_PREEMPT_RESUME_AWAITING_HUMAN_HEARING'
        except Exception as exc:
            report['error']=str(exc);raise
        finally:
            if audio:audio.stop();audio.close()
            if b:b.close()
            if thread:thread.join(7)
            report['device_events']=[l for _,l in b.lines] if b else []
            (ROOT/'speaker-hourly-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
