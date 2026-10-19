"""Verify a corrupt PCM frame is rejected and USB can recover; no cloud calls."""
import binascii
import json
from pathlib import Path
import runpy
import struct
import threading
import time

ROOT=Path(__file__).resolve().parent
Probe=runpy.run_path(str(ROOT/'check-device.py'))['Probe']
bridge=Probe('COM5')
thread=threading.Thread(target=bridge.run,daemon=True);thread.start()
try:
    bridge.wait_line('CHAT_READY')
    bridge.uploading=True  # Keep heartbeat bytes out of the deliberately framed payload.
    bridge.send(b'p');bridge.wait_line('UPLOAD_READY')
    pcm=bytes(2048)
    bridge.send(struct.pack('<II',len(pcm),binascii.crc32(pcm)^1))
    bridge.send(pcm)
    bridge.wait_line('AUDIO_ERROR crc_or_timeout')
    assert not any('SPEAKING' in line or 'LISTENING' in line for _,line in bridge.lines)
finally:
    bridge.uploading=False
    bridge.close();thread.join(timeout=3)
time.sleep(3)
recovered=Probe('COM5')
thread=threading.Thread(target=recovered.run,daemon=True);thread.start()
try:
    recovered.wait_line('CHAT_READY')
    assert recovered.cloud.calls==0
    report={'status':'PASS','corrupt_frame_rejected':True,'no_playback_or_recording':True,
            'reconnect_after_quiet_interval':True,'cloud_calls':0,
            'device_events':[line for _,line in bridge.lines]}
    (ROOT/'framing-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
finally:
    recovered.close();thread.join(timeout=3)
