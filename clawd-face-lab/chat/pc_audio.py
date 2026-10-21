"""Explicit, local-only WASAPI loopback -> bounded USB PCM stream.

No microphone, audio files, cloud calls, default-device or volume changes.
SoundCard's installed WASAPI backend resamples to 24k; capture both channels
before downmixing (its Windows mono-capture path has a known limitation).
"""
import binascii
from collections import deque
import queue
import struct
import threading
import time
import warnings


RATE=24000
SAMPLES=480
FRAME_BYTES=SAMPLES*2
WINDOW=4


def packet(sequence, payload=b'', flags=0):
    if not ((flags==0 and len(payload)==FRAME_BYTES) or
            (flags==1 and not payload) or (flags==2 and len(payload)==2)):
        raise ValueError('Invalid PC audio frame')
    if flags==2 and struct.unpack('<H',payload)[0]>100:raise ValueError('Invalid volume')
    head=struct.pack('<4sIHH',b'KPCM',sequence,len(payload),flags)
    return head+struct.pack('<I',binascii.crc32(head+payload))+payload


def mono_pcm(data):
    import numpy as np
    data=np.asarray(data)
    if data.ndim!=2 or len(data)!=SAMPLES or data.shape[1]<2:
        raise ValueError('Expected a stereo 20ms loopback block')
    mono=np.nan_to_num(data.mean(axis=1),nan=0.,posinf=1.,neginf=-1.)
    return (np.clip(mono,-1.,32767/32768)*32768).astype('<i2').tobytes()


class LoopbackSource:
    def __enter__(self):
        import soundcard as sc
        import ctypes
        # SoundCard initializes COM only on its import thread. Each capture
        # restart runs on a fresh thread and must initialize its own apartment.
        self.ole=ctypes.windll.ole32
        result=self.ole.CoInitializeEx(None,0)
        self.com_owned=result in (0,1)
        if not self.com_owned and result!=-2147417850:
            raise RuntimeError('Windows声音接口初始化失败。')
        self.sc=sc
        try:
            if not sc.all_speakers():
                raise RuntimeError('Windows没有可用的播放设备。请打开显示器或连接音箱/耳机，再开启电脑音箱。')
            speaker=sc.default_speaker()
            if speaker is None:raise RuntimeError('Windows没有可用的播放设备。')
            self.device_id=speaker.id;self.name=speaker.name
            mic=sc.get_microphone(speaker.id,include_loopback=True)
            if not mic.isloopback:raise RuntimeError('未找到电脑声音回环通道；未启用麦克风。')
            self.context=mic.recorder(samplerate=RATE,channels=max(2,speaker.channels),blocksize=960)
            self.recorder=self.context.__enter__()
            return self
        except Exception:
            if self.com_owned:self.ole.CoUninitialize()
            raise

    def read(self):return mono_pcm(self.recorder.record(SAMPLES))

    def check_device(self):
        if self.sc.default_speaker().id!=self.device_id:
            raise RuntimeError('电脑播放设备已改变；请重新开启电脑音箱。')

    def __exit__(self,*args):
        try:return self.context.__exit__(*args)
        finally:
            self.recorder=None;self.context=None
            if self.com_owned:self.ole.CoUninitialize()


class SpeakerSession:
    def __init__(self, bridge, source_factory=LoopbackSource):
        self.bridge=bridge;self.source_factory=source_factory
        self.stop_event=threading.Event();self.done=threading.Event()
        self.ready=False;self.device_done=False;self.device_error=None
        self.condition=threading.Condition();self.audio=queue.Queue(maxsize=3)
        self.thread=None;self.capture_thread=None
        self.volume=None;self.sequence=0;self.pending={}
        self.error=None;self.source='';self.metrics={'captured':0,'sent':0,'dropped':0,'warnings':0,'warning_types':{}}
        self.ack_ms=deque(maxlen=500);self.started=time.monotonic()

    def start(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='clawd-pc-audio');self.thread.start()

    def stop(self):
        self.stop_event.set()
        with self.condition:self.condition.notify_all()

    def event(self,line):
        import re
        fields={k.decode():int(v) for k,v in re.findall(rb'(\w+)=(\d+)',line)}
        with self.condition:
            if line.startswith(b'CLAWD STREAM_READY'):self.ready=True
            elif line.startswith(b'CLAWD STREAM_ACK'):
                seq=fields['seq'];now=time.monotonic()
                for key in list(self.pending):
                    if key<=seq:
                        stamp=self.pending.pop(key);self.ack_ms.append((now-stamp)*1000)
                self.metrics['device_age_us']=fields.get('age_us',0)
            elif line.startswith(b'CLAWD STREAM_DONE'):
                self.device_done=True;self.metrics['device']=fields;self.stop_event.set()
                if fields.get('terminal',1)!=1 or fields.get('bad',0):
                    self.device_error='音箱数据中断，需要重新连接。'
                    self.error=self.device_error
            elif line.startswith(b'CLAWD STREAM_ERROR'):
                self.device_error='机器人无法开启电脑音箱。';self.stop_event.set()
            elif line.startswith(b'CLAWD STREAM_VOLUME'):
                self.bridge.control_status['volume']=fields['volume']
            self.condition.notify_all()

    def capture(self):
        try:
            with self.source_factory() as source, warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter('always')
                # SoundCard 0.4.6 sets ndarray.shape; NumPy 2.5 deprecates that
                # API. Fidelity diagnostic confirms both rates reproduce 440Hz.
                # Keep actual WASAPI discontinuity warnings visible in metrics.
                warnings.filterwarnings('ignore',message='Setting the shape on a NumPy array has been deprecated.*',
                    category=DeprecationWarning)
                self.source=source.name;last_check=time.monotonic()
                while not self.stop_event.is_set() and not self.bridge.closed.is_set():
                    pcm=source.read()
                    self.metrics['warnings']+=len(caught)
                    for warning in caught:
                        key=(type(warning.message).__name__+': '+str(warning.message))[:180]
                        counts=self.metrics['warning_types']
                        if key in counts or len(counts)<8:counts[key]=counts.get(key,0)+1
                    caught.clear()
                    self.metrics['captured']+=1
                    if time.monotonic()-last_check>2:
                        source.check_device();last_check=time.monotonic()
                    item=(time.monotonic(),pcm)
                    try:self.audio.put_nowait(item)
                    except queue.Full:
                        try:self.audio.get_nowait();self.metrics['dropped']+=1
                        except queue.Empty:pass
                        self.audio.put_nowait(item)
        except Exception as exc:
            self.error=str(exc) if isinstance(exc,RuntimeError) else '无法捕获电脑声音，请检查Windows播放设备。'
            self.stop()

    def write(self,payload=b'',flags=0):
        data=packet(self.sequence,payload,flags)
        with self.bridge.write_lock:
            if self.bridge.closed.is_set():raise RuntimeError('USB已断开。')
            written=self.bridge.serial.write(data)
            if written!=len(data):raise RuntimeError('USB音箱帧未完整发送。')
        self.sequence+=1

    def run(self):
        try:
            with self.condition:
                if not self.condition.wait_for(lambda:self.ready or self.device_error or self.stop_event.is_set(),5):
                    raise RuntimeError('机器人音箱握手超时。')
            if self.device_error:raise RuntimeError(self.device_error)
            if not self.ready:return
            self.capture_thread=threading.Thread(target=self.capture,daemon=True,name='clawd-loopback')
            self.capture_thread.start()
            while not self.stop_event.is_set() and not self.bridge.closed.is_set():
                with self.condition:
                    if len(self.pending)>=WINDOW:
                        if not self.condition.wait_for(lambda:len(self.pending)<WINDOW or self.stop_event.is_set(),1):
                            raise RuntimeError('USB音箱播放没有及时确认。')
                if self.stop_event.is_set():break
                if self.volume is not None:
                    value=self.volume;self.volume=None;self.write(struct.pack('<H',value),2)
                try:stamp,pcm=self.audio.get(timeout=.1)
                except queue.Empty:continue
                # Avoid replaying stale sound after a paused/saturated USB path.
                if time.monotonic()-stamp>.15:self.metrics['dropped']+=1;continue
                with self.condition:
                    self.pending[self.sequence]=stamp
                    self.write(pcm);self.metrics['sent']+=1
        except Exception as exc:
            self.error=str(exc) if isinstance(exc,RuntimeError) else '电脑音箱连接异常，已停止。'
        finally:
            self.stop_event.set()
            if self.ready and not self.device_done and not self.bridge.closed.is_set():
                try:
                    self.write(flags=1)
                    with self.condition:self.condition.wait_for(lambda:self.device_done,3)
                    if not self.device_done:self.error=self.error or '机器人未确认停止，请重新连接。'
                except Exception:self.error=self.error or 'USB断开，电脑音箱已关闭。'
            if self.capture_thread:self.capture_thread.join(timeout=2)
            if self.capture_thread and self.capture_thread.is_alive():
                self.error='Windows声音捕获未退出，请关闭窗口后重试。'
            self.metrics['seconds']=round(time.monotonic()-self.started,3)
            if self.ack_ms:
                values=sorted(self.ack_ms)
                self.metrics['capture_return_to_play_ack_ms']={'median':round(values[len(values)//2],1),
                    'p95':round(values[int((len(values)-1)*.95)],1),'max':round(values[-1],1)}
            self.audio=queue.Queue(maxsize=3)  # Release ephemeral system-audio frames.
            self.done.set()
