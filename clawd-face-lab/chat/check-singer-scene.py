"""Real LCD snapshots of the stream scene's 600ms visual tail after STOP.

No new USB diagnostics inside PCM, no camera capture or cloud. Synthetic tones
drive the real audio amplitude/mouth path. These are actual LVGL snapshots, not
the geometry sketch. Physical touch and subjective listening remain separate.
"""
import json
from pathlib import Path
import re
import runpy
import threading
import time
from unittest.mock import patch
import wave

import numpy as np
from pc_audio import SpeakerSession

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


class SingerProbe(Probe):
    def device_event(self,line):
        if 'STREAM_ACK' not in line:super().device_event(line)


def main():
    b=SingerProbe('COM5');b.set_continuous(False);b.set_wake(True)
    report={'status':'FAIL','cloud_calls':0,'camera_photos_saved':False,'room_pcm_saved':False,
        'snapshot_timing':'actual stream scene retained 600ms after framed STOP; no LCD command inside PCM',
        'physical_touch_tested':False,'human_visual_acceptance':False,'captures':[]}
    amplitude=[0]
    class Source:
        name='local synthetic tone for mouth geometry'
        def __enter__(self):self.next=time.monotonic();self.n=0;return self
        def __exit__(self,*_):pass
        def check_device(self):pass
        def read(self):
            self.next+=.02;time.sleep(max(0,self.next-time.monotonic()))
            n=np.arange(self.n,self.n+480);self.n+=480
            return (amplitude[0]*np.sin(2*np.pi*440*n/24000)).astype('<i2').tobytes()
    worker=threading.Thread(target=b.run,daemon=True)
    def wait(predicate,label,timeout=15):
        end=time.monotonic()+timeout
        while time.monotonic()<end and not b.closed.is_set():
            if predicate():return
            time.sleep(.01)
        raise RuntimeError(label)
    worker.start()
    try:
        ready=b.wait_line('CHAT_READY')[1];assert 'pc_scene=singer1' in ready
        wait(lambda:b.wake_session is not None,'initial KWS')
        wait(lambda:not b.control_active and not b.control_requests,'hardware status')
        with patch('bridge.SpeakerSession',lambda bridge:SpeakerSession(bridge,Source)):
            for name,volume in [('singer-quiet-native',0),('singer-singing-native',4500)]:
                amplitude[0]=volume;start=time.monotonic();assert b.request_pc_audio()
                wait(lambda:b.pc_session is not None,'start stream');session=b.pc_session
                b.wait_line('SINGER visible=1',start)
                wait(lambda:session.metrics['sent']>=30,'audio amplitude settled')
                b.request_pc_audio(False)
                wait(lambda:b.pc_session is None,'STOP releases native RX')
                capture_start=time.monotonic();b.capture(name)
                _,line=b.wait_line('SINGER_FRAME visible=1',capture_start)
                level=int(re.search(r'level=(\d+)',line)[1])
                assert (level==0 if volume==0 else level>=40),line
                report['captures'].append({'name':name,'level':level,'stream':session.metrics})
                assert session.metrics['device']['bad']==0
                assert session.metrics['device']['underruns']==0
                b.wait_line('SINGER visible=0',capture_start)
                wait(lambda:b.wake_session is not None,'restore KWS')
        # Ordinary chat, microphone and TTS do not borrow the singer costume.
        start=time.monotonic();b.send(b'n');b.wait_line('LISTENING',start)
        b.capture('singer-back-to-listening-native')
        _,line=b.wait_line('SINGER_FRAME visible=0',start);assert 'visible=0' in line
        b.send(b'n');b.wait_line('CANCELLED',start)
        wait(lambda:b.wake_session is not None,'touch cancel restores wake')
        with wave.open(str(ROOT/'adult-cloud-test.wav')) as wav:pcm=wav.readframes(wav.getnframes())
        start=time.monotonic();b.play(pcm,b.epoch);b.wait_line('SPEAKING',start)
        b.capture('singer-normal-chat-native')
        b.wait_line('SINGER_FRAME visible=0',start)
        b.wait_line('PLAY_DONE cancelled=0',start)
        wait(lambda:b.wake_session is not None,'TTS restores wake')
        report['ordinary_chat_face_preserved']=True
        report['stream_returns_to_wake']=True
        report['status']='PASS_DEVICE_LCD_AWAITING_HUMAN_VISUAL_REVIEW'
    finally:
        b.close();worker.join(5)
        report['device_events']=[line for _,line in b.lines]
        (ROOT/'singer-scene-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
