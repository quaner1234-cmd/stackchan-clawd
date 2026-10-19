"""Real host-loss and mid-frame-cancel checks. Synthetic PC speaker fixture only.

No cloud requests and no persisted mic audio. PC speakers may be muted; that part
then remains unverified rather than being labelled PASS.
"""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
import time
import winsound

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def ready(b):
    worker=threading.Thread(target=b.run,daemon=True);worker.start()
    b.wait_line('CHAT_READY')
    deadline=time.monotonic()+15
    while b.wake_session is None and time.monotonic()<deadline:time.sleep(.02)
    assert b.wake_session is not None
    return worker


class CancelProbe(Probe):
    def __init__(self):
        super().__init__('COM5');self.cancel_transfer=False;self.discarded=False

    def exact(self,length,timeout=20):
        if self.cancel_transfer and length>4800:
            self.cancel_transfer=False
            first=super().exact(64,timeout)
            self.send(b'x')
            time.sleep(.2)
            return first+super().exact(length-64,timeout)
        return super().exact(length,timeout)

    def connection_event(self,event,**metadata):
        if event=='record_transfer_discarded':self.discarded=True
        super().connection_event(event,**metadata)


def main():
    if '--child' in sys.argv:
        b=Probe('COM5');b.set_wake(True);ready(b)
        time.sleep(1)
        # Deliberately emulate a process crash: no finally, q or U command.
        os._exit(0)
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,physical_usb_unplug_tested=False)
    with (ROOT.parent/'wake-word/host-loss-child.log').open('w',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,__file__,'--child'],stdout=log,stderr=subprocess.STDOUT)
        assert child.wait(timeout=35)==0
    time.sleep(13)
    b=CancelProbe();b.set_wake(True);b.set_continuous(False);worker=ready(b)
    try:
        report['unclean_host_exit_reconnect']=True
        report['device_host_timeout_seen']=any('HOST_TIMEOUT' in line for _,line in b.lines)
        b.set_wake(False);time.sleep(.5)
        t=time.monotonic();b.cancel_transfer=True;b.listen();b.wait_line('LISTENING',t)
        winsound.PlaySound(str(ROOT/'adult-cloud-test.wav'),winsound.SND_FILENAME|winsound.SND_ASYNC)
        deadline=time.monotonic()+17
        while time.monotonic()<deadline and not b.closed.is_set():
            if b.discarded or any('NO_SPEECH' in line for stamp,line in b.lines if stamp>=t):break
            time.sleep(.05)
        winsound.PlaySound(None,0)
        report['synthetic_pc_speaker_detected']=any('RECORD_DONE' in line for stamp,line in b.lines if stamp>=t)
        report['mid_frame_cancel_discarded']=b.discarded
        report['bridge_still_ready']=b.state=='ready' and not b.closed.is_set()
        assert report['bridge_still_ready']
        b.cancel();b.set_wake(True)
        deadline=time.monotonic()+8
        while b.wake_session is None and time.monotonic()<deadline:time.sleep(.02)
        assert b.wake_session is not None
        report['standby_recovers']=True
        report['status']='PASS' if b.discarded else 'PARTIAL_PC_SPEAKER_NOT_HEARD'
    finally:
        b.close();worker.join(4)
        report['device_events']=[line for _,line in b.lines]
        (ROOT.parent/'wake-word/production-interrupt-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},indent=2))


if __name__=='__main__':main()
