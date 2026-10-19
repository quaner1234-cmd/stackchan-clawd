"""Verify real codec volume and wake recovery with an existing synthetic WAV.

No cloud calls, no new recording files, no camera capture.
"""
import json
from pathlib import Path
import runpy
import threading
import time
import wave

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    bridge=Probe('COM5');bridge.set_wake(True);bridge.set_continuous(False)
    worker=threading.Thread(target=bridge.run,daemon=True);worker.start()
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,
        fixture='adult-cloud-test.wav (existing synthetic adult voice)',
        human_loudness_confirmed=False)
    def standby():
        deadline=time.monotonic()+15
        while bridge.wake_session is None and time.monotonic()<deadline and not bridge.closed.is_set():time.sleep(.02)
        assert bridge.wake_session is not None
    try:
        bridge.wait_line('CHAT_READY');standby()
        with wave.open(str(ROOT/'adult-cloud-test.wav')) as wav:
            assert (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())==(1,2,24000)
            pcm=wav.readframes(24000*4)
        t=time.monotonic();bridge.play(pcm,bridge.epoch)
        speaking=bridge.wait_line('SPEAKING',t)[1]
        assert 'volume=100 pcm_gain_percent=100' in speaking,speaking
        assert bridge.wake_session is None
        bridge.wait_line('PLAY_DONE cancelled=0',t)
        standby()
        report.update(codec_volume=100,pcm_gain_percent=100,playback_complete=True,wake_recovered_after_playback=True)
        t=time.monotonic();bridge.listen();bridge.wait_line('LISTENING',t)
        time.sleep(.3);bridge.cancel();bridge.wait_line('CANCELLED',t);standby()
        report.update(record_cancel_recovered=True,status='PASS_DEVICE_VOLUME')
    finally:
        bridge.close();worker.join(4)
        report['device_events']=[line for _,line in bridge.lines]
        (ROOT/'max-volume-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},indent=2))


if __name__=='__main__':main()
