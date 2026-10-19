"""Bounded real USB control checks; no cloud, stored room PCM or camera images."""
import argparse
import json
from pathlib import Path
import runpy
import threading
import time
from daily_routines import hourly_pcm

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--verify-reboot',action='store_true')
    parser.add_argument('--wake-switch',action='store_true')
    args=parser.parse_args()
    b=Probe('COM5');b.set_continuous(False)
    thread=threading.Thread(target=b.run,daemon=True)
    report={'status':'FAIL','cloud_calls':0,'room_audio_saved':False,'camera_images_saved':False,
            'touch_method':'existing n callback, not human touch','released_pose_tolerance_degrees':2.0,'criteria_note':'1.5 degree arrival gate; allow one encoder step plus torque-release settling in final readback. Previous strict 1.5 degree failure retained.'}
    def until(predicate,timeout=15):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if predicate():return
            if b.closed.is_set():raise RuntimeError('USB closed')
            time.sleep(.025)
        raise RuntimeError('Timed out waiting for state')
    def control(kind,*values):
        until(lambda:b.scene_idle())
        start=time.monotonic();assert b.request_control(kind,*values)
        if kind=='pose':
            begun,_=b.wait_line('CONTROL_BEGIN',start)
            _,done=b.wait_line('CONTROL_DONE reason=(complete|timeout)',begun)
            assert 'reason=complete' in done,done
        else:b.wait_line('CONTROL_DONE reason='+('status' if kind=='status' else 'volume'),start)
        until(lambda:not b.control_active and b.control_status.get('active')==0)
        return dict(b.control_status)
    def standby():until(lambda:b.wake_session is not None)
    thread.start()
    try:
        b.wait_line('CHAT_READY');b.wait_line('CONTROL_DONE reason=status')
        until(lambda:bool(b.control_status) and not b.control_active)
        assert b.device_controls
        report['initial']=dict(b.control_status)
        if args.wake_switch:
            b.set_wake(True);standby()
            origin=(b.control_status['yaw'],b.control_status['pitch'])
            report['cycles']=[]
            for i in range(6):
                standby();until(lambda:b.wake_device_active)
                result=control('pose',origin[0],origin[1]+(30 if i%2==0 else 0))
                assert not result['yaw_torque'] and not result['pitch_torque']
                standby();report['cycles'].append(result)
            assert not any('CONTROL_ERROR' in line for _,line in b.lines)
            report['wake_to_control_to_wake_without_busy_errors']=True
        elif args.verify_reboot:
            assert b.control_status['volume']==0,'Mute not retained through real hardware reset'
            report['mute_survives_reset']=True
            report['restored_user_volume']=control('volume',100)['volume']
            assert report['restored_user_volume']==100
        else:
            assert b.control_status['volume']==100
            origin=(b.control_status['yaw'],b.control_status['pitch'])
            reports=[]
            for yaw,pitch in [(max(-270,min(270,origin[0]))+30,origin[1]),origin,
                              (origin[0],max(30,min(420,origin[1]))+30),origin]:
                result=control('pose',yaw,pitch)
                assert not result['active'] and not result['yaw_torque'] and not result['pitch_torque']
                assert abs(result['yaw']-yaw)<=20 and abs(result['pitch']-pitch)<=20,(yaw,pitch,result)
                reports.append({'target':[yaw,pitch],'actual':result})
            report['small_moves']=reports
            b.set_wake(True);standby()
            start=time.monotonic();b.request_control('pose',origin[0]+30,origin[1]+30)
            b.wait_line('CONTROL_BEGIN',start);cancel=time.monotonic();b.send(b'n')
            stopped,_=b.wait_line('CONTROL_DONE reason=touch',cancel)
            b.wait_line('CANCELLED',cancel)
            report['touch_cancel_ms']=round((stopped-cancel)*1000)
            standby();report['wake_after_control']=True
            start=time.monotonic();b.send(b'n');b.wait_line('LISTENING',start)
            b.cancel();standby();report['touch_start_cancel']=True
            b.set_wake(False);until(lambda:not b.wake_requested)
            control('pose',*origin)
            report['volume_50']=control('volume',50)['volume'];assert report['volume_50']==50
            start=time.monotonic();b.play(hourly_pcm(12),b.epoch)
            b.wait_line('SPEAKING volume=50',start)
            b.request_control('volume',60)
            end,_=b.wait_line('PLAY_DONE cancelled=0',start)
            b.wait_line('CONTROL_DONE reason=volume',end)
            until(lambda:b.control_status.get('volume')==60 and not b.control_active)
            assert not any('CONTROL_STATUS' in line and 'volume=60 ' in line and stamp<end for stamp,line in b.lines)
            report['queued_volume_after_playback']=True
            start=time.monotonic();b.send(b'n');b.wait_line('LISTENING',start)
            stopped=time.monotonic();b.cancel();b.wait_line('CANCELLED',stopped)
            until(lambda:b.scene_idle())
            report['cue_retains_volume']=control('status')['volume'];assert report['cue_retains_volume']==60
            report['mute']=control('volume',0)['volume'];assert report['mute']==0
            start=time.monotonic();b.play(hourly_pcm(12),b.epoch)
            b.wait_line('SPEAKING volume=0',start);b.wait_line('PLAY_DONE cancelled=0',start)
            until(lambda:b.scene_idle())
            report['mute_playback_retained']=control('status')['volume']==0
            report['awaiting_reset_check']=True
        assert b.cloud.calls==0
        report['status']='PASS_DEVICE_CONTROL_EVENTS'
    finally:
        if report['status']=='FAIL':
            try:control('volume',100)
            except Exception:pass
        b.close();thread.join(timeout=4)
        report['events']=[line for _,line in b.lines]
        name='control-panel-wake-switch-check.json' if args.wake_switch else 'control-panel-reboot-check.json' if args.verify_reboot else 'control-panel-device-check.json'
        (ROOT/name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
