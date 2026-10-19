"""Two local device photos: shutter, same-frame preview, 5s timeout and cancel.

No cloud calls; keeps the first requested photo in the normal PC photo folder.
Close the chat GUI first so COM5 has one owner.
"""
import binascii
import json
from pathlib import Path
import re
import runpy
import threading
import time

from camera import Camera

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']


def main():
    bridge=Probe('COM5');bridge.camera=Camera();bridge.set_continuous(False)
    thread=threading.Thread(target=bridge.run,daemon=True);thread.start()
    report={'status':'FAIL','cloud_calls':0,'physical_shutter_sound_human_confirmed':False}
    try:
        _,ready=bridge.wait_line('CHAT_READY')
        assert 'photo_preview_ms=5000' in ready,ready
        started=time.monotonic();jpeg=bridge.request_photo()
        assert jpeg and jpeg.startswith(b'\xff\xd8')
        bridge.wait_line('PHOTO_DONE',started)
        _,line=bridge.wait_line('PHOTO_PREVIEW shown',started)
        rgb_crc=int(re.search(r'rgb_crc=(\d+)',line)[1])
        assert int(re.search(r'jpeg_crc=(\d+)',line)[1])==binascii.crc32(jpeg)
        saved=bridge.camera.save(jpeg)
        assert saved and saved.read_bytes()==jpeg
        report['saved_photo']=str(saved)
        report['photo_bytes']=len(jpeg)
        bridge.wait_line('SHUTTER played=1',started)
        for name in ('photo-preview-early','photo-preview-late'):
            if name.endswith('late'):time.sleep(.6)
            bridge.capture(name)
            raw=(ROOT.parent/f'device/captures/{name}.rgb565').read_bytes()
            assert binascii.crc32(raw)==rgb_crc,'LCD differs from the captured photo: '+name
        report['same_photo_on_lcd_and_pc']=True
        _,closed=bridge.wait_line('PHOTO_PREVIEW closed reason=timeout',started,timeout=9)
        shown_ms=int(re.search(r'shown_ms=(\d+)',closed)[1])
        assert 4990<=shown_ms<=6000,closed
        report['preview_shown_ms']=shown_ms
        bridge.capture('photo-preview-restored')
        restored=(ROOT.parent/'device/captures/photo-preview-restored.rgb565').read_bytes()
        assert binascii.crc32(restored)!=rgb_crc
        report['restored_after_5_seconds']=True
        started=time.monotonic();assert bridge.request_photo()
        bridge.wait_line('PHOTO_DONE',started)
        bridge.cancel()
        _,closed=bridge.wait_line('PHOTO_PREVIEW closed reason=cancelled',started)
        assert int(re.search(r'shown_ms=(\d+)',closed)[1])<5000
        bridge.capture('photo-preview-cancelled')
        report['cancel_closes_preview']=True
        report['captures']=bridge.captured
        report['status']='PASS_DEVICE_PHOTO_PREVIEW_5_SECONDS'
    finally:
        bridge.close();thread.join(3)
        report['events']=[line for _,line in bridge.lines]
        (ROOT/'photo-preview-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='events'},ensure_ascii=False))


if __name__=='__main__':main()
