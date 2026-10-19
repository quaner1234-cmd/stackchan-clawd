"""Bounded real-device regression: speech detection and automatic next turn.

Uses an existing adult synthetic WAV, never cloud calls or saved room audio.
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
    bridge=Probe('COM5')
    bridge.set_continuous(False)
    thread=threading.Thread(target=bridge.run,daemon=True)
    thread.start()
    report={'cloud_calls':0,'audio_source':'existing adult synthetic WAV',
            'room_audio_saved':False,'status':'FAIL'}
    try:
        _,ready=bridge.wait_line('CHAT_READY')
        assert any('voice_profile='+profile in ready for profile in ('vad2','family1','family2','family3'))
        started=time.monotonic()
        bridge.listen()
        bridge.wait_line('LISTENING',started)
        bridge.wait_line('NO_SPEECH',started,timeout=9)
        report['no_speech_without_cloud']=True
        report['quiet_listen_seconds']=round(time.monotonic()-started,2)
        bridge.set_continuous(True)
        bridge.continuous_active=True
        bridge.pending_history=(bridge.epoch,'Synthetic adult check','Synthetic adult answer')
        with wave.open(str(ROOT/'adult-cloud-test.wav')) as wav:
            pcm=wav.readframes(wav.getnframes())
        started=time.monotonic()
        bridge.play(pcm,bridge.epoch)
        done,_=bridge.wait_line('PLAY_DONE cancelled=0',started)
        listened,_=bridge.wait_line('LISTENING',done,timeout=3)
        report['playback_completed']=True
        report['auto_listen_after_playback_ms']=round((listened-done)*1000)
        quiet,_=bridge.wait_line('NO_SPEECH',listened,timeout=9)
        report['speaker_tail_did_not_trigger_speech']=True
        bridge.wait_line('LISTENING',quiet,timeout=3)
        stopped=time.monotonic()
        bridge.cancel()
        bridge.wait_line('CANCELLED',stopped)
        time.sleep(1.5)
        assert not bridge.continuous_active and bridge.next_listen_at is None
        assert not any(t>stopped and 'LISTENING' in line for t,line in bridge.lines)
        report['stop_prevents_auto_listen']=True
        report['status']='PASS_DEVICE_VAD_AND_CONTINUOUS'
    finally:
        bridge.close()
        thread.join(timeout=3)
        report['device_events']=[line for _,line in bridge.lines]
        (ROOT/'continuous-hardware-check.json').write_text(
            json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},ensure_ascii=False))


if __name__=='__main__':main()
