"""Recover actual USB connection after an induced reader stall, then play alarm."""
import json
from pathlib import Path
import runpy
import threading
import time
import argparse
import re

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def start():
    bridge=Probe('COM5');bridge.set_wake(True);bridge.set_continuous(False)
    bridge.workday_alarm.enabled=True
    worker=threading.Thread(target=bridge.run,daemon=True);worker.start()
    bridge.wait_line('CHAT_READY',timeout=30)
    deadline=time.monotonic()+15
    while bridge.wake_session is None and time.monotonic()<deadline:time.sleep(.03)
    assert bridge.wake_session is not None
    return bridge,worker


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--expect-stall-recovery',action='store_true');args=parser.parse_args()
    report=dict(status='FAIL',cloud_calls=0,room_audio_saved=False,physical_cable_unplug_tested=False,
                gui_auto_reconnect_callback_test='test_alarm_reconnect.py',reader_stall_s=1.0)
    old=None;new=None
    try:
        old,worker=start();original=old.wake_engine.feed;stalled=False
        def delayed(*args):
            nonlocal stalled
            if not stalled:stalled=True;time.sleep(1)
            return original(*args)
        old.wake_engine.feed=delayed
        deadline=time.monotonic()+8
        while not old.closed.is_set() and time.monotonic()<deadline:time.sleep(.04)
        report['stall_triggered_crc_failure']=bool(old.closed.is_set() and '完整性' in (old.last_error or ''))
        if args.expect_stall_recovery:
            assert not old.closed.is_set(),'A one-second reader stall still breaks the connection'
            report['one_second_stall_recovered_without_reconnect']=True
        if not old.closed.is_set():
            report['host_serial_close_used']=True
            old.serial.close()
        worker.join(5);assert not worker.is_alive()
        report['first_connection_error']=old.last_error
        time.sleep(2)
        new,new_worker=start()
        report['actual_serial_reconnection_ready']=True
        if args.expect_stall_recovery:
            heaps=[]
            for i in range(4):
                started=time.monotonic();new.set_wake(False)
                line=new.wait_line('WAKE_IDLE heap_internal=',started)[1]
                heaps.append(int(re.search(r'heap_internal=(\d+)',line).group(1)))
                new.set_wake(True)
                new.wait_line('WAKE_STARTED',time.monotonic())
            report['wake_stop_internal_heap_bytes']=heaps
            report['four_wake_cycles_no_monotonic_buffer_leak']=min(heaps[1:])>=heaps[0]-4096
            assert report['four_wake_cycles_no_monotonic_buffer_leak'],heaps
        started=time.monotonic();new.request_alarm()
        new.wait_line('SPEAKING volume=100 pcm_gain_percent=100',started)
        new.cancel();new.wait_line('PLAY_DONE cancelled=1',started)
        report.update(local_alarm_after_reconnection=True,status='PASS_USB_RECONNECT_AND_ALARM')
    finally:
        if old and not old.closed.is_set():old.close()
        if new:new.close();new_worker.join(4)
        (ROOT/'workday-alarm-reconnect-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
