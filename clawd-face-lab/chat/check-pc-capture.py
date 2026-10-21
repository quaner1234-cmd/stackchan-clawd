"""Short WASAPI fidelity diagnostic; persist numeric results, never system audio."""
from collections import Counter
import json
from pathlib import Path
import warnings
import numpy as np
import soundcard as sc
import sounddevice as sd


def main():
    speaker=sc.default_speaker();report={'device':speaker.name,'audio_saved':False,'rates':{}}
    n=[0]
    def render(out,frames,*_):
        index=np.arange(n[0],n[0]+frames);n[0]+=frames
        out[:]=(.05*np.sin(2*np.pi*440*index/48000))[:,None]
    with sd.OutputStream(samplerate=48000,channels=2,blocksize=960,dtype='float32',callback=render):
        for rate in (24000,48000):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                with sc.get_microphone(speaker.id,include_loopback=True).recorder(
                        samplerate=rate,channels=2,blocksize=int(rate*.04)) as mic:
                    chunks=[mic.record(int(rate*.02)).mean(axis=1) for _ in range(100)]
                audio=np.concatenate(chunks)[rate//2:]
                spectrum=np.abs(np.fft.rfft(audio*np.hanning(len(audio))))
                peak_bin=int(np.argmax(spectrum));peak_hz=peak_bin*rate/len(audio)
                report['rates'][rate]={'peak_hz':round(peak_hz,2),'rms':float(np.sqrt(np.mean(audio**2))),
                    'peak':float(np.max(np.abs(audio))),
                    'warnings':dict(Counter(type(w.message).__name__+': '+str(w.message) for w in caught))}
    (Path(__file__).parent/'pc-speaker-capture-diagnostic.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
