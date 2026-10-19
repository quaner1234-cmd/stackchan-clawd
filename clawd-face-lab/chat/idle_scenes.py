"""Parent-approved half-hour reminders. Only clock/settings; no cloud or audio capture."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import wave

ROOT=Path(__file__).resolve().parent
PHRASE='小克也喝水啦，你也喝一小口吧。'
BEIJING=timezone(timedelta(hours=8))


class DrinkReminder:
    def __init__(self, path=None):
        self.path=Path(path or ROOT/'drink-settings.json')
        try:settings=json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError,ValueError):settings={}
        if not isinstance(settings,dict):settings={}
        self.enabled=settings.get('enabled',True) is True
        self.last=settings.get('last_slot','')
        if not isinstance(self.last,str):self.last=''
        self.pending=None

    def save(self):
        self.path.write_text(json.dumps({'enabled':self.enabled,'last_slot':self.last})+'\n',encoding='utf-8')

    def set_enabled(self, enabled):
        self.enabled=bool(enabled)
        if not self.enabled:self.pending=None
        self.save()

    def due(self, now=None):
        now=(now or datetime.now(BEIJING)).astimezone(BEIJING)
        if not self.enabled:
            self.pending=None;return None
        # A busy slot can wait up to ten minutes, never accumulate a backlog.
        if self.pending and not (0 <= (now-self.pending).total_seconds() < 600):self.pending=None
        if now.minute in (0,30) and 9*60 <= now.hour*60+now.minute <= 20*60:
            slot=now.replace(second=0,microsecond=0)
            if slot.isoformat()>self.last:self.pending=slot
        return self.pending.isoformat() if self.pending else None

    def claim(self, slot):
        if slot is not None:
            self.last=slot;self.pending=None
            # Persist before playing: reopening the GUI will not repeat this slot.
            self.save()


def drink_pcm():
    with wave.open(str(ROOT/'assets/drink-reminder.wav'),'rb') as wav:
        if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,24000):
            raise ValueError('喝水提醒语音格式错误。')
        pcm=wav.readframes(24000*10+1)
        if not pcm or len(pcm)>24000*10*2:raise ValueError('喝水提醒语音时长错误。')
        return pcm
