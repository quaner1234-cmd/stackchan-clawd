"""Production detector regression with existing synthetic fixtures; zero cloud."""
import json
import argparse
from pathlib import Path
import time
import wave
from wake import LocalWake, ROOT


def run():
    parser=argparse.ArgumentParser();parser.add_argument('--model',default='bilingual',choices=['bilingual','chinese'])
    args=parser.parse_args()
    started=time.perf_counter();engine=LocalWake(args.model);loaded=time.perf_counter()-started
    results=[]
    for path in sorted((ROOT/'clips').glob('*.wav')):
        engine.reset()
        with wave.open(str(path)) as wav:
            rate=wav.getframerate()
            assert wav.getnchannels()==1 and wav.getsampwidth()==2
            pcm=wav.readframes(wav.getnframes())
        # True audio sample rate, 100ms live framing, .8s tail (file fixtures only).
        n=rate//10*2;data=pcm+b'\0'*(int(rate*.8)*2);hits=[]
        for seq,offset in enumerate(range(0,len(data),n)):
            if engine.feed(1,seq,data[offset:offset+n],rate):hits.append(round(offset/(rate*2),2))
        results.append(dict(file=path.name,synthetic=True,duration_s=round(len(pcm)/(rate*2),2),hits_s=hits))
    report=dict(model=args.model,
                keywords=['你好小克'],load_s=round(loaded,3),metrics=engine.metrics(),
                cloud_calls=0,human_acceptance=False,fixtures=results)
    name='production-offline-check.json' if args.model=='bilingual' else 'chinese-offline-check.json'
    (ROOT/name).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':run()
