"""Observe existing automatic speaker with a normal Windows WAV playback.

No serial connection, microphone, driver/settings changes, saved audio, or cloud.
The normal parent panel must already be connected with automatic audio enabled.
Only numeric loopback statistics and selected panel events are saved.
"""
import argparse
import json
from pathlib import Path
import time
import wave
import winsound

from pc_audio import AutoAudioMonitor


ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--intermittent', action='store_true', help='400ms tone, 300ms silence, repeated for 6s')
    args = parser.parse_args()
    log = ROOT / 'connection.jsonl'
    offset = log.stat().st_size if log.exists() else 0
    monitor = AutoAudioMonitor()
    report = {'cloud_calls': 0, 'audio_saved': False, 'system_settings_changed': False}
    monitor.start()
    try:
        deadline = time.monotonic() + 8
        while not monitor.ready and time.monotonic() < deadline:
            time.sleep(.02)
        if not monitor.ready:
            raise RuntimeError(monitor.error or 'Loopback unavailable')
        report['source'] = monitor.name
        fixture = ROOT / 'assets' / 'workday-alarm.wav'
        with wave.open(str(fixture), 'rb') as wav:
            duration = wav.getnframes() / wav.getframerate()
        output = None
        if args.intermittent:
            import numpy as np
            import sounddevice as sd
            duration = 6
            t = np.arange(48000 * duration) / 48000
            signal = (.12 * np.sin(2 * np.pi * 440 * t) * ((t % .7) < .4)).astype(np.float32)
            output = sd
            report['fixture'] = '400ms tone / 300ms silence at 48kHz, Windows default output'
        else:
            report['fixture'] = fixture.name
        report['duration_s'] = duration
        samples = []
        start = time.monotonic()
        if output:
            output.play(signal[:, None].repeat(2, axis=1), 48000)
        else:
            winsound.PlaySound(str(fixture), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        while time.monotonic() - start < duration + 13:
            samples.append({'ms': round((time.monotonic() - start) * 1000),
                            'rms': monitor.rms, 'frames': monitor.frames,
                            'active': monitor.active(), 'ready': monitor.ready})
            time.sleep(.05)
        report['peak_rms'] = max(s['rms'] for s in samples)
        hits = [s['ms'] for s in samples if s['active']]
        report['first_monitor_hit_ms'] = hits[0] if hits else None
        report['samples'] = samples
        with log.open('rb') as stream:
            stream.seek(offset)
            events = [json.loads(line) for line in stream if line.strip()]
        report['panel_events'] = [e for e in events if e.get('event', '').startswith(('pc_', 'wake_standby', 'error'))]
        done = [e for e in report['panel_events'] if e['event'] == 'pc_audio_done']
        report['automatic_audio_verified'] = bool(
            any(e['event'] == 'pc_auto_sound_start' for e in report['panel_events'])
            and done and done[-1].get('error') is None
            and done[-1].get('device', {}).get('peak', 0) > 80
            and done[-1].get('device', {}).get('bad', 1) == 0)
        report['status'] = 'OBSERVED'
    except Exception as exc:
        report.update(status='FAILED_CHECK', error=str(exc))
    finally:
        winsound.PlaySound(None, 0)
        if args.intermittent:
            import sounddevice as sd
            sd.stop()
        monitor.stop()
        monitor.thread.join(3)
        report['capture_closed'] = not monitor.thread.is_alive()
        destination = ROOT / ('auto-audio-reassessment-' + time.strftime('%Y%m%d-%H%M%S') + '.json')
        destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({k: v for k, v in report.items() if k != 'samples'}, ensure_ascii=False), flush=True)
        print(str(destination), flush=True)


if __name__ == '__main__':
    main()
