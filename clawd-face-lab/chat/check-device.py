"""Real USB hardware checks using an adult synthetic WAV; never calls cloud APIs."""
import argparse
import json
from pathlib import Path
import re
import threading
import time
import wave

from bridge import Bridge

ROOT=Path(__file__).resolve().parent
CAPTURES=ROOT.parent/'device'/'captures'


class Probe(Bridge):
    def __init__(self,port):
        super().__init__(port,self.notice)
        self.agent_display.enabled=False  # Independent hardware checks must be deterministic.
        self.cloud.max_calls=0
        self.drink_reminder.enabled=False
        self.workday_alarm.enabled=False
        self.daily.hourly_enabled=False;self.daily.sleep_enabled=False
        self.lines=[]
        self.capture_name=None
        self.captured=[]

    def notice(self,message,metadata):
        print(message,flush=True)

    def device_event(self,line):
        self.lines.append((time.monotonic(),line))
        if 'UPLOAD_ACK' not in line:print(line,flush=True)

    def screen_frame(self,metadata,data):
        if self.capture_name:
            CAPTURES.mkdir(exist_ok=True)
            name=self.capture_name
            (CAPTURES/f'{name}.rgb565').write_bytes(data)
            (CAPTURES/f'{name}.json').write_text(json.dumps(metadata),encoding='utf-8')
            self.captured.append(name)
            self.capture_name=None

    def respond(self,pcm,token):
        # Deliberately no ASR, chat or TTS here. Do not persist room audio.
        print('HARDWARE_MIC_PCM',json.dumps({'bytes':len(pcm),'seconds':len(pcm)/48000}),flush=True)
        self.send(b'i')

    def wait_line(self,pattern,since=0,timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            match=next(((t,line) for t,line in self.lines if t>=since and re.search(pattern,line)),None)
            if match:return match
            if self.closed.is_set():raise RuntimeError('USB bridge closed during hardware check')
            time.sleep(.02)
        raise RuntimeError('No device event: '+pattern)

    def capture(self,name):
        self.capture_name=name;self.send(b'c')
        deadline=time.monotonic()+6
        while name not in self.captured and time.monotonic()<deadline:time.sleep(.02)
        if name not in self.captured:raise RuntimeError('No rendered display frame: '+name)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',default='COM5')
    args=parser.parse_args()
    with wave.open(str(ROOT/'adult-cloud-test.wav')) as wav:
        assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())==(1,2,24000)
        pcm=wav.readframes(wav.getnframes())
    bridge=Probe(args.port)
    thread=threading.Thread(target=bridge.run,daemon=True);thread.start()
    report={'scope':'USB device playback/cancellation/microphone/display, no cloud calls',
            'audio_source':'adult-cloud-test.wav (synthetic adult test text)'}
    try:
        bridge.wait_line('CHAT_READY')
        started=time.monotonic();bridge.play(pcm,bridge.epoch)
        bridge.wait_line('SPEAKING',started)
        time.sleep(.3);bridge.capture('voice-speaking')
        bridge.wait_line('PLAY_DONE cancelled=0',started)
        report['playback_complete']=True
        started=time.monotonic();bridge.play(pcm,bridge.epoch)
        bridge.wait_line('SPEAKING',started)
        time.sleep(.25);cancelled=time.monotonic();bridge.cancel()
        stopped,_=bridge.wait_line('PLAY_DONE cancelled=1',cancelled)
        report['cancel_to_stop_ms']=round((stopped-cancelled)*1000)
        bridge.capture('voice-paused')
        started=time.monotonic();bridge.listen();bridge.wait_line('LISTENING',started)
        bridge.capture('voice-listening')
        time.sleep(.6);cancelled=time.monotonic();bridge.cancel()
        _,line=bridge.wait_line(r'CANCELLED peak_rms=\d+',cancelled)
        report['microphone_peak_rms']=int(line.rsplit('=',1)[-1])
        report['microphone_cancelled_without_cloud']=True
        report['captures']=bridge.captured
        report['status']='PASS_DEVICE_EVENTS_AWAITING_HUMAN_AUDIO_CONFIRMATION'
    finally:
        bridge.close();thread.join(timeout=3)
        report['device_events']=[line for _,line in bridge.lines]
        (ROOT/'hardware-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({key:value for key,value in report.items() if key!='device_events'},ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__':main()
