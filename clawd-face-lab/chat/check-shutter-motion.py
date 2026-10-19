"""Local device acceptance: original shutter, relative gestures and quiet states.

Uses the existing adult synthetic WAV. Zero cloud calls, no room audio saved,
and the temporary camera JPEG stays in memory. Close the chat GUI first.
"""
import argparse
import json
from pathlib import Path
import re
import runpy
import threading
import time
import wave

from camera import Camera

ROOT = Path(__file__).resolve().parent
Probe = runpy.run_path(str(ROOT / 'check-device.py'))['Probe']


def fields(line):
    return dict(re.findall(r'(\w+)=([^\s]+)', line))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', default='COM5')
    args = parser.parse_args()
    with wave.open(str(ROOT / 'adult-cloud-test.wav')) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        pcm = wav.readframes(wav.getnframes())
    bridge = Probe(args.port)
    bridge.camera = Camera(keep_photos=False)
    bridge.set_continuous(False)
    thread = threading.Thread(target=bridge.run, daemon=True)
    thread.start()
    report = {'status': 'FAIL', 'cloud_calls': 0, 'test_photos_saved': False,
              'human_shutter_and_motion_confirmed': False}

    def status():
        started = time.monotonic()
        bridge.send(b'j')
        return fields(bridge.wait_line('MOTION_STATUS', started)[1])

    def released():
        value = status()
        assert all(value[key] == '0' for key in ('active', 'yaw_torque', 'pitch_torque')), value
        return value

    try:
        ready = bridge.wait_line('CHAT_READY')[1]
        assert 'shutter=original_max chat_motion=gentle1' in ready, ready
        started = time.monotonic()
        jpeg = bridge.request_photo()
        assert jpeg and jpeg.startswith(b'\xff\xd8')
        report['photo_bytes'] = len(jpeg)
        del jpeg
        sound = fields(bridge.wait_line('SHUTTER played=1', started)[1])
        assert sound['asset'] == 'camera_shutter' and sound['volume'] == '100', sound
        assert sound['restored'] == '1' and sound['peak'] == '28000', sound
        assert 300 <= int(sound['duration_ms']) <= 1500, sound
        report['shutter'] = sound
        report['photo_motors_released'] = released()
        closed = bridge.wait_line('PHOTO_PREVIEW closed reason=timeout', started, timeout=9)[1]
        assert 4990 <= int(fields(closed)['shown_ms']) <= 6000, closed
        report['preview_ms'] = int(fields(closed)['shown_ms'])

        report['gestures'] = []
        for kind, axis, limit in (('nod', 'pitch', 30), ('sway', 'yaw', 45)):
            started = time.monotonic()
            bridge.play(pcm, bridge.epoch)
            bridge.wait_line('SPEAKING', started)
            bridge.wait_line(f'MOTION kind={kind} stage=0', started)
            readings = []
            while not any(t >= started and 'PLAY_DONE' in line for t, line in bridge.lines):
                readings.append(status())
                if time.monotonic() - started > 12:
                    raise RuntimeError('Playback did not finish')
                time.sleep(.18)
            events = [fields(line) for t, line in bridge.lines
                      if t >= started and f'MOTION kind={kind}' in line]
            base = int(events[0]['base'])
            assert all(abs(int(event['target']) - base) <= limit for event in events), events
            actual = [int(value[axis]) for value in readings]
            assert max(actual) - min(actual) >= 6, (kind, readings)
            assert all(abs(value - base) <= limit + 12 for value in actual), (kind, readings)
            report['gestures'].append({'kind': kind, 'base': base, 'targets': events,
                                       'physical_readings': readings, 'after': released()})

        started = time.monotonic()
        bridge.play(pcm[:24000], bridge.epoch)  # Half-second reply must stay still.
        bridge.wait_line('PLAY_DONE cancelled=0', started)
        assert not any(t >= started and 'MOTION kind=' in line for t, line in bridge.lines)
        report['short_reply_skips_motion'] = released()

        # Interrupt a gesture while it is active, then exercise the real mic path.
        started = time.monotonic()
        bridge.play(pcm, bridge.epoch)
        bridge.wait_line('MOTION kind=nod stage=0', started)
        time.sleep(.2)
        cancelled = time.monotonic()
        bridge.cancel()
        stopped = bridge.wait_line('MOTION stopped', cancelled)[0]
        bridge.wait_line('PLAY_DONE cancelled=1', cancelled)
        report['cancel_to_motion_stop_ms'] = round((stopped - cancelled) * 1000)
        assert report['cancel_to_motion_stop_ms'] < 700
        report['cancel_motors_released'] = []
        for _ in range(4):
            report['cancel_motors_released'].append(released())
            time.sleep(.15)
        started = time.monotonic()
        bridge.listen()
        bridge.wait_line('LISTENING', started)
        for _ in range(3):
            report['listening_motors_released'] = released()
            time.sleep(.2)
        bridge.cancel()
        bridge.wait_line(r'CANCELLED peak_rms=\d+', started)
        bridge.send(b'q')
        report['disconnected_motors_released'] = released()
        report['status'] = 'PASS_DEVICE_SHUTTER_AND_GESTURES_AWAITING_HUMAN_FEEDBACK'
    finally:
        bridge.close()
        thread.join(3)
        report['events'] = [line for _, line in bridge.lines]
        (ROOT / 'shutter-motion-check.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'events'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
