"""Real device dance with fake recognition/TTS; no cloud or room-audio storage.

The existing adult synthetic WAV stands in for the spoken introduction. Exercise
the real Bridge reply/completion path, then the MCU's tap and disconnect paths.
Close the chat GUI before running this script.
"""
import json
from pathlib import Path
import re
import runpy
import threading
import time
import wave

from bridge import Bridge

ROOT = Path(__file__).resolve().parent
Probe = runpy.run_path(str(ROOT / 'check-device.py'))['Probe']


def fields(line):
    return dict(re.findall(r'(\w+)=([^\s]+)', line))


def main():
    with wave.open(str(ROOT / 'adult-cloud-test.wav')) as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 24000)
        pcm = wav.readframes(wav.getnframes())
    bridge = Probe('COM5')
    thread = threading.Thread(target=bridge.run, daemon=True)
    thread.start()
    report = {'status': 'FAIL', 'cloud_calls': 0, 'room_audio_saved': False,
              'recognition_and_tts_are_fakes': True, 'human_dance_confirmed': False}

    def released():
        started = time.monotonic()
        bridge.send(b'j')
        value = fields(bridge.wait_line('DANCE_STATUS', started)[1])
        assert all(value[k] == '0' for k in ('active', 'yaw_torque', 'pitch_torque')), value
        return value

    try:
        ready = bridge.wait_line('CHAT_READY')[1]
        assert 'dance=crab1 dance_ms=8000' in ready
        deadline = time.monotonic() + 2
        while not bridge.device_dance and time.monotonic() < deadline:
            time.sleep(.02)
        assert bridge.device_dance
        fake_calls = []

        class LocalCloud:
            def recognize(self, recording):
                fake_calls.append('fake_recognize')
                return '小克，跳个舞吧'

            def synthesize(self, text):
                assert '小螃蟹' in text
                fake_calls.append('fake_synthesize')
                return pcm

            def reply(self, *args, **kwargs):
                raise AssertionError('Dance must not call the chat model')

        bridge.cloud = LocalCloud()
        bridge.continuous_active = True
        started = time.monotonic()
        Bridge.respond(bridge, b'\0\0', bridge.epoch)
        bridge.wait_line('PLAY_DONE cancelled=0', started)
        dance_started = bridge.wait_line('DANCE_START', started)[0]
        assert not any(t < dance_started and t >= started and 'CLAWD LISTENING' in line
                       for t, line in bridge.lines)
        bridge.capture('dance-face')
        done_at, done_line = bridge.wait_line('DANCE_DONE cancelled=0', dance_started, timeout=12)
        elapsed = int(fields(done_line)['elapsed_ms'])
        assert 7990 <= elapsed <= 9000, done_line
        stages = [fields(line) for t, line in bridge.lines
                  if t >= dance_started and 'DANCE_STAGE' in line]
        assert {int(value['stage']) for value in stages} == set(range(10)), stages
        for value in stages:
            assert abs(int(value['target_yaw']) - int(value['base_yaw'])) <= 60
            assert abs(int(value['target_pitch']) - int(value['base_pitch'])) <= 40
        yaws = [int(v['yaw']) for v in stages]
        pitches = [int(v['pitch']) for v in stages]
        assert max(yaws) - min(yaws) >= 20, yaws
        assert max(pitches) - min(pitches) >= 10, pitches
        assert not any(dance_started <= t < done_at and 'CLAWD LISTENING' in line
                       for t, line in bridge.lines)
        listened_at = bridge.wait_line('LISTENING', done_at, timeout=4)[0]
        assert listened_at > done_at
        final_pose=released()
        assert abs(int(final_pose['yaw'])-int(stages[0]['base_yaw']))<=15, final_pose
        assert abs(int(final_pose['pitch'])-int(stages[0]['base_pitch']))<=15, final_pose
        report.update(duration_ms=elapsed, stages=stages, fake_calls=fake_calls,
                      voice_reply_then_dance_then_listen=True, quiet_motors_on_resume=final_pose,
                      final_pose_within_1_5_degrees=True)
        # A new dance command while the mic is open is refused by the MCU.
        blocked = time.monotonic()
        bridge.send(b'B')
        bridge.wait_line('DANCE_ERROR busy', blocked)
        assert not any(t >= blocked and 'DANCE_START' in line for t, line in bridge.lines)
        report['dance_refused_while_listening'] = True
        bridge.cancel()
        bridge.wait_line(r'CANCELLED peak_rms=\d+', blocked)

        started = time.monotonic()
        bridge.send(b'B')
        bridge.wait_line('DANCE_START', started)
        time.sleep(.4)
        tap = time.monotonic()
        bridge.send(b'n')  # Same next_emotion event consumed by the physical tap.
        _, stopped = bridge.wait_line('DANCE_DONE cancelled=1', tap)
        assert 'reason=touch' in stopped
        assert int(fields(stopped)['elapsed_ms']) < 3000
        report['tap_cancels'] = stopped
        for _ in range(4):
            report['tap_motors_released'] = released()
            time.sleep(.12)

        started = time.monotonic()
        bridge.send(b'B')
        bridge.wait_line('DANCE_START', started)
        bridge.send(b'q')
        bridge.wait_line('DANCE_DONE cancelled=1', started)
        report['disconnect_motors_released'] = released()
        report['captures'] = bridge.captured
        report['status'] = 'PASS_DEVICE_DANCE_AWAITING_HUMAN_FEEDBACK'
    finally:
        bridge.close()
        thread.join(3)
        report['events'] = [line for _, line in bridge.lines]
        (ROOT / 'dance-device-check.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('events', 'stages')}, ensure_ascii=False))


if __name__ == '__main__':
    main()
