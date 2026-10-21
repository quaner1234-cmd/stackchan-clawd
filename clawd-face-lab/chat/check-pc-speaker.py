"""Real Windows render -> WASAPI loopback -> USB -> MCU output verification.

Frozen gates: >=95% of 50 frames/s, no CRC failure, <=1 output underrun/min,
<=1% host drops, bounded last-window ACK p95 <250ms, successful stop/recovery.
ACK timing is NOT an acoustic or lip-sync measurement. Synthetic stereo tones
exercise ordinary Windows output; system audio and room PCM are never saved.
"""
import argparse
from contextlib import nullcontext
from copy import deepcopy
import json
from pathlib import Path
import runpy
import threading
import time
from unittest.mock import patch

import numpy as np
import sounddevice as sd
import camera
from pc_audio import SpeakerSession

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']
memory_mb=runpy.run_path(str(ROOT/'check-wake-device.py'))['memory_mb']


class SpeakerProbe(Probe):
    def device_event(self,line):
        if 'STREAM_ACK' not in line:super().device_event(line)


def wait(b,predicate,label,timeout=15):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        if b.closed.is_set():raise RuntimeError('USB closed: '+label)
        time.sleep(.02)
    raise RuntimeError('Timed out: '+label)


def connect():
    b=SpeakerProbe('COM5');b.set_continuous(False);b.set_wake(True)
    b.camera=camera.Camera(keep_photos=False)
    thread=threading.Thread(target=b.run,daemon=True);thread.start()
    b.wait_line('CHAT_READY');wait(b,lambda:b.wake_session is not None,'initial KWS')
    wait(b,lambda:not b.control_active and not b.control_requests,'control handshake')
    return b,thread


def begin(b):
    assert b.request_pc_audio()
    wait(b,lambda:b.pc_session is not None,'stream request')
    session=b.pc_session
    wait(b,lambda:session.done.is_set() or session.metrics['sent']>=15,'sound reaches MCU')
    if session.done.is_set():raise RuntimeError(session.error or 'stream stopped')
    assert b.wake_session is None
    return session


def end(b,session,listen=False):
    started=time.monotonic()
    if listen:b.listen()
    else:b.request_pc_audio(False)
    wait(b,lambda:b.pc_session is None,'terminal handshake')
    assert session.done.is_set() and not session.error,session.error
    assert not session.capture_thread.is_alive()
    result=deepcopy(session.metrics);result['stop_ms']=round((time.monotonic()-started)*1000)
    assert result['device']['terminal']==1 and result['device']['bad']==0
    assert result['device']['peak']>100,'Only silence reached playback'
    if listen:
        wait(b,lambda:b.phase=='listening','listen after stream')
        b.cancel();wait(b,lambda:b.phase=='paused','record cancel')
    wait(b,lambda:b.wake_session is not None,'KWS restored')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--seconds',type=int,default=180)
    parser.add_argument('--disconnect',action='store_true');parser.add_argument('--regression',action='store_true')
    parser.add_argument('--transport-only',action='store_true',help='Synthetic PCM direct to USB; NOT a system-loopback acceptance')
    parser.add_argument('--report',help='Report filename inside chat/; preserves earlier acceptance reports')
    args=parser.parse_args();b=None;thread=None
    if args.report and (Path(args.report).name!=args.report or not args.report.endswith('.json')):
        parser.error('--report must be a JSON filename inside chat/')
    report={'status':'FAIL','seconds_requested':args.seconds,'cloud_calls':0,'system_audio_saved':False,
        'room_pcm_saved':False,'photo_saved':False,'physical_unplug_tested':False,
        'physical_touch_tested':False,'acoustic_latency_measured':False,'sessions':[]}
    report['windows_loopback_tested']=not args.transport_only
    index=[0]
    def render(outdata,frames,times,status):
        n=np.arange(index[0],index[0]+frames);index[0]+=frames
        notes=np.array([262,330,392,523,440,392,330,294])
        freq=notes[(n//24000)%len(notes)]
        wave=.05*np.sin(2*np.pi*freq*n/48000)+.012*np.sin(4*np.pi*freq*n/48000)
        outdata[:]=wave[:,None]
    class SyntheticSource:
        name='local generated PCM (NOT Windows loopback)'
        def __enter__(self):self.n=0;self.next=time.monotonic();return self
        def __exit__(self,*_):pass
        def check_device(self):pass
        def read(self):
            self.next+=.02;time.sleep(max(0,self.next-time.monotonic()))
            n=np.arange(self.n,self.n+480);self.n+=480
            return (np.sin(2*np.pi*440*n/24000)*1600).astype('<i2').tobytes()
    factory=patch('bridge.SpeakerSession',lambda bridge:SpeakerSession(bridge,SyntheticSource)) if args.transport_only else nullcontext()
    factory.__enter__()
    try:
        b,thread=connect();session=begin(b)
        cpu=time.process_time();start=time.monotonic();working_set=memory_mb();samples=[]
        with (nullcontext() if args.transport_only else sd.OutputStream(samplerate=48000,channels=2,blocksize=960,dtype='float32',callback=render)):
            while time.monotonic()-start<args.seconds:
                if session.done.is_set():raise RuntimeError(session.error or 'Stream ended unexpectedly')
                if b.closed.is_set():raise RuntimeError(b.last_error)
                if int(time.monotonic()-start)%15==0:
                    samples.append({'seconds':round(time.monotonic()-start,1),'sent':session.metrics['sent'],
                        'dropped':session.metrics['dropped'],'working_set_mb':memory_mb(),
                        'last_ack_ms':round(session.ack_ms[-1],1) if session.ack_ms else None})
                time.sleep(.5)
            b.request_control('volume',60);b.wait_line('STREAM_VOLUME volume=60',start)
            b.request_control('volume',100);b.wait_line('STREAM_VOLUME volume=100',start)
            elapsed=time.monotonic()-start
            report['resources']={'process_cpu_percent_one_core':round((time.process_time()-cpu)/elapsed*100,2),
                'working_set_initial_mb':working_set,'working_set_final_mb':memory_mb(),'samples':samples}
            report['sessions'].append(end(b,session))
            result=report['sessions'][-1]
            assert result['sent']>=args.seconds*50*.95,result
            assert result['dropped']<=result['captured']*.01,result
            assert result['device']['underruns']<=max(1,args.seconds//60),result
            assert result['capture_return_to_play_ack_ms']['p95']<250,result
            session=begin(b);time.sleep(2);report['sessions'].append(end(b,session,listen=True))
            report['playback_to_record_to_wake']=True
            if args.disconnect:
                session=begin(b);time.sleep(1)
                b.serial.close();thread.join(6)
                assert b.closed.is_set()
                if session.thread:session.thread.join(5)
                assert session.done.is_set() and not session.capture_thread.is_alive()
                report['host_disconnect_releases_capture']=True
                report['disconnect_metrics']=session.metrics
                time.sleep(3)
                b,thread=connect();report['host_disconnect_reconnect_kws']=True
        if args.regression:
            started=time.monotonic();b.send(b'n');b.wait_line('LISTENING',started)
            b.send(b'n');b.wait_line('CANCELLED',started)
            wait(b,lambda:b.wake_session is not None,'original touch path')
            started=time.monotonic();b.send(b's');b.wait_line('CHAT_RESET',started)
            wait(b,lambda:b.wake_session is not None,'long press path')
            result=[];started=time.monotonic()
            photo=threading.Thread(target=lambda:result.append(b.request_photo()),daemon=True);photo.start()
            b.wait_line('PHOTO_PREVIEW shown',started);b.cancel();photo.join(6);result.clear()
            wait(b,lambda:b.wake_session is not None,'photo cancellation')
            started=time.monotonic();b.stop_wake();b.phase='dancing';b.dance_token=b.epoch;b.dance_deadline=started+12;b.send(b'B')
            b.wait_line('DANCE_START',started);b.cancel();b.wait_line('DANCE_DONE cancelled=1',started)
            wait(b,lambda:b.wake_session is not None,'dance cancellation')
            assert b.cloud.calls==0
            report['original_touch_reset_photo_dance_wake_paths']=True
        report['status']=('PASS_DEVICE_TRANSPORT_ONLY_SYSTEM_LOOPBACK_UNVERIFIED' if args.transport_only else
            'PASS_DEVICE_DIGITAL_STREAM_AWAITING_HUMAN_LISTENING')
    except Exception as exc:
        report['error']=str(exc)
        if b and b.pc_session:report['failed_metrics']=deepcopy(b.pc_session.metrics)
        raise
    finally:
        if b:b.close()
        if thread:thread.join(5)
        factory.__exit__(None,None,None)
        report['device_events']=[line for _,line in b.lines] if b else []
        filename=args.report or ('pc-speaker-transport-check.json' if args.transport_only else 'pc-speaker-device-check.json')
        (ROOT/filename).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
