"""Local-only KWS. No microphone, network, disk audio or child transcripts here.

USB audio has exactly one reader (Bridge.run). Decoding also runs on that reader;
only model loading runs on a background thread. No unbounded audio queue exists.
"""
from pathlib import Path
import queue
import threading
import time

ROOT = Path(__file__).resolve().parent.parent / 'wake-word'
MODEL = ROOT / 'sherpa-onnx-kws-zipformer-zh-en-3M-2025-12-20'
CHINESE_MODEL = ROOT / 'sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01'


class LocalWake:
    def __init__(self, model='chinese'):
        import numpy as np
        import sherpa_onnx
        self.np = np
        self.model=model
        directory=CHINESE_MODEL if model=='chinese' else MODEL
        stem = ('epoch-12' if model=='chinese' else 'epoch-13')+'-avg-2-chunk-16-left-64.onnx'
        self.spotter = sherpa_onnx.KeywordSpotter(
            tokens=str(directory / 'tokens.txt'), encoder=str(directory / ('encoder-'+stem)),
            decoder=str(directory / ('decoder-'+stem)), joiner=str(directory / ('joiner-'+stem)),
            keywords_file=str(ROOT / 'keywords-production.txt'),
            # Keep the global default aligned with the per-keyword #0.4 value.
            keywords_threshold=0.4,
            keywords_score=2.0, num_trailing_blanks=1,
            max_active_paths=4, num_threads=1, provider='cpu')
        self.stream = None
        self.lock = threading.RLock()
        self.session = None
        self.sequence = None
        self.frames = self.hits = self.gaps = 0
        self.audio_s = self.decode_s = self.max_decode_ms = 0.0

    def reset(self):
        with self.lock:
            self.stream = None
            self.session = self.sequence = None

    def feed(self, session, sequence, pcm, rate=24000):
        with self.lock:return self._feed(session,sequence,pcm,rate)

    def _feed(self, session, sequence, pcm, rate):
        if self.session != session or self.sequence is None or sequence != self.sequence+1:
            if self.session == session and self.sequence is not None:
                self.gaps += 1
            self.stream = self.spotter.create_stream()
            self.session = session
        self.sequence = sequence
        started = time.perf_counter()
        samples = self.np.frombuffer(pcm, dtype='<i2').astype(self.np.float32)/32768.0
        # sherpa resamples the actual input rate; never relabel 24 kHz as 16 kHz.
        self.stream.accept_waveform(rate, samples)
        hit = False
        while self.spotter.is_ready(self.stream):
            self.spotter.decode_stream(self.stream)
            if self.spotter.get_result(self.stream):
                hit = True
                self.spotter.reset_stream(self.stream)
        elapsed = time.perf_counter()-started
        self.frames += 1
        self.audio_s += len(pcm)/(rate*2)
        self.decode_s += elapsed
        self.max_decode_ms = max(self.max_decode_ms, elapsed*1000)
        if hit:self.hits += 1
        return hit

    def metrics(self):
        return dict(model=self.model,frames=self.frames, hits=self.hits, frame_gaps=self.gaps,
                    audio_s=round(self.audio_s,2), decode_s=round(self.decode_s,3),
                    max_decode_ms=round(self.max_decode_ms,2),
                    realtime_factor=round(self.decode_s/max(self.audio_s,.001),4))


def load_async():
    result = queue.Queue(maxsize=1)
    def load():
        try:result.put(LocalWake())
        except Exception as exc:result.put(exc)
    threading.Thread(target=load, daemon=True, name='clawd-kws-load').start()
    return result
