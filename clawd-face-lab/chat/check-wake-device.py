"""Real USB continuous wake/state regression; no cloud, no room PCM/photos saved."""
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
import runpy
import threading
import time
import wave
import camera

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def memory_mb():
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in (
            'PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage',
            'QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage')]
    counters=Counters();counters.cb=ctypes.sizeof(counters)
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
    info=ctypes.WinDLL('psapi').GetProcessMemoryInfo
    info.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    info.restype=wintypes.BOOL
    if not info(kernel.GetCurrentProcess(),ctypes.byref(counters),counters.cb):
        return None
    return round(counters.WorkingSetSize/1024**2,1)


def main():
    b=Probe('COM5');b.set_wake(True);b.set_continuous(False)
    b.camera=camera.Camera(keep_photos=False)
    worker=threading.Thread(target=b.run,daemon=True);worker.start()
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,test_photos_saved=False,
                human_wake_acceptance=False,physical_touch_acceptance=False)
    def wait(predicate,label,timeout=12):
        deadline=time.monotonic()+timeout
        while not predicate() and time.monotonic()<deadline and not b.closed.is_set():time.sleep(.02)
        assert predicate(),label
    def standby():wait(lambda:b.wake_session is not None,'Wake standby',15)
    try:
        b.wait_line('CHAT_READY');standby()
        t=time.monotonic();cpu=time.process_time();initial=b.wake_engine.frames;initial_hits=b.wake_engine.hits
        while time.monotonic()-t<30 and not b.closed.is_set():time.sleep(.1)
        elapsed=time.monotonic()-t
        report['standby']=dict(wall_s=round(elapsed,2),frames=b.wake_engine.frames-initial,
            unlabelled_detections=b.wake_engine.hits-initial_hits,
            process_cpu_percent_one_core=round((time.process_time()-cpu)/elapsed*100,2),
            working_set_mb=memory_mb(),metrics=b.wake_engine.metrics())
        # Off must stop microphone and frames, without taking away touch recording.
        t=time.monotonic();b.set_wake(False);b.wait_line('WAKE_STOPPED',t)
        count=b.wake_engine.frames;time.sleep(1)
        assert b.wake_engine.frames==count
        report['disable_stops_stream']=True
        t=time.monotonic();b.listen();b.wait_line('LISTENING',t)
        time.sleep(.4);b.cancel();b.wait_line('CANCELLED',t)
        wait(lambda:b.phase=='paused','Cancel acknowledgement')
        report['original_record_cancel_when_disabled']=True
        b.set_wake(True);standby()
        t=time.monotonic();b.listen();b.wait_line('LISTENING',t)
        stop=b.wait_line('WAKE_STOPPED',t)[0];listen=b.wait_line('LISTENING',t)[0]
        report['stream_stop_to_record_ms']=round((listen-stop)*1000)
        assert stop<=listen
        b.cancel();b.wait_line('CANCELLED',t);standby()
        report['record_cancel_returns_wake']=True
        with wave.open(str(ROOT/'adult-cloud-test.wav')) as wav:pcm=wav.readframes(wav.getnframes())
        t=time.monotonic();b.play(pcm,b.epoch);b.wait_line('SPEAKING',t)
        assert b.wake_session is None
        b.wait_line('PLAY_DONE cancelled=0',t);standby()
        report['playback_suspends_and_resumes']=True
        result=[];t=time.monotonic()
        photo_thread=threading.Thread(target=lambda:result.append(b.request_photo()),daemon=True)
        photo_thread.start();b.wait_line('PHOTO_PREVIEW shown',t)
        assert b.wake_session is None
        b.wait_line('PHOTO_PREVIEW closed',t);photo_thread.join(3)
        assert result and result[0] and result[0][:2]==b'\xff\xd8'
        result.clear();standby()
        report['photo_preview_suspends_and_resumes']=True
        t=time.monotonic();b.stop_wake();b.dance_token=b.epoch;b.dance_deadline=t+12;b.phase='dancing';b.send(b'B')
        b.wait_line('DANCE_START',t);assert b.wake_session is None
        b.wait_line('DANCE_DONE cancelled=0',t);standby()
        report['dance_suspends_and_resumes']=True
        # A new USB host closes the prior stream on h; explicit q closes it too.
        t=time.monotonic();b.send(b'q');b.wait_line('WAKE_STOPPED',t)
        report['host_quit_stops_wake']=True
        report['status']='PASS_DEVICE_PROTOCOL_AWAITING_HUMAN'
    finally:
        b.close();worker.join(4)
        report['device_events']=[line for _,line in b.lines]
        (ROOT.parent/'wake-word/production-device-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='device_events'},indent=2),flush=True)


if __name__=='__main__':main()
